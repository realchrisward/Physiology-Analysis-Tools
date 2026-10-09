"""FP/FN review lists: parse a classifier's flagged-beat CSV and locate the
recordings it refers to.

Both endpoints report problems in-band (HTTP 200 with status="error"), the
same way backend/files.py reports per-file import failures, so the renderer
never has to special-case a thrown request.
"""

import csv
import io
import math
import os
import re

from fastapi import APIRouter

from backend.models import (
    FlagListMatch,
    FlagListMatchRequest,
    FlagListMatchResult,
    FlagListParseRequest,
    FlagListParseResult,
    FlagListRow,
)

router = APIRouter(prefix="/flag-lists", tags=["flag-lists"])

# Same set as SUPPORTED_EXTENSIONS in desktop/main.js.
RECORDING_EXTENSIONS = (".adicht", ".txt", ".mat", ".gzip", ".edf")

# Column-name candidates, in priority order. Names are compared after
# lower-casing and dropping whitespace / underscores / hyphens (see
# _compact), so "Animal ID", "animal_id" and "AnimalID" are all the same.
ID_COLUMNS = ("animal_id", "animalid", "animal", "id", "file", "filename")
TS_COLUMNS = ("ts", "timestamp", "time", "time_s", "t")
GROUND_TRUTH_COLUMNS = ("ground_truth", "groundtruth")
PREDICTION_COLUMNS = ("prediction",)
KIND_COLUMNS = ("kind", "type", "error_type", "label")

# The model's decision threshold: prediction >= 0.5 means "arrhythmia".
PREDICTION_THRESHOLD = 0.5

_KIND_VALUES = {
    "fp": "fp",
    "falsepositive": "fp",
    "fn": "fn",
    "falsenegative": "fn",
}

_MAX_COLUMNS_IN_ERROR = 20


def _compact(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", name.strip().lower())


def _find_column(columns: dict[str, int], candidates: tuple[str, ...]) -> int | None:
    """Index of the first candidate (in priority order) present in `columns`,
    which maps compacted header -> first column index with that header."""
    for candidate in candidates:
        idx = columns.get(_compact(candidate))
        if idx is not None:
            return idx
    return None


def _decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # latin-1 maps every byte, so this can't fail; good enough for the
        # ASCII IDs and numbers we read.
        return raw.decode("latin-1")


def _sniff_delimiter(text: str) -> str:
    """Pick comma / tab / semicolon by whichever splits the header line into the
    most fields. The data rows here are full of empty cells, which makes
    csv.Sniffer unreliable, so we only look at the header."""
    header = next((ln for ln in text.splitlines() if ln.strip()), "")
    best, best_count = ",", 0
    for delimiter in (",", "\t", ";"):
        fields = next(csv.reader([header], delimiter=delimiter), [])
        if len(fields) > best_count:
            best, best_count = delimiter, len(fields)
    return best


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx].strip()


def _to_float(text: str, decimal_comma: bool) -> float | None:
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        if decimal_comma and "," in text and "." not in text:
            try:
                value = float(text.replace(",", "."))
            except ValueError:
                return None
        else:
            return None
    return value if math.isfinite(value) else None


def _to_binary_int(value: float | None) -> int | None:
    if value is None or value != int(value):
        return None
    return int(value)


def _normalise_id(raw: str) -> str:
    animal_id = raw.strip()
    lowered = animal_id.lower()
    for ext in RECORDING_EXTENSIONS:
        if lowered.endswith(ext):
            return animal_id[: -len(ext)].strip()
    return animal_id


def _kind_from_label(text: str) -> str:
    return _KIND_VALUES.get(_compact(text), "flag")


def _kind_from_scores(ground_truth: int | None, prediction: float | None) -> str:
    if ground_truth == 0 and prediction is not None and prediction >= PREDICTION_THRESHOLD:
        return "fp"
    if ground_truth == 1 and prediction is not None and prediction < PREDICTION_THRESHOLD:
        return "fn"
    return "flag"


def _kind_from_ground_truth(ground_truth: int | None) -> str:
    if ground_truth == 0:
        return "fp"
    if ground_truth == 1:
        return "fn"
    return "flag"


def _parse_flag_list(path: str) -> FlagListParseResult:
    filename = os.path.basename(path)
    if not os.path.exists(path):
        return FlagListParseResult(
            status="error", filename=filename, error=f"File not found: {path}"
        )
    if not os.path.isfile(path):
        return FlagListParseResult(
            status="error", filename=filename, error=f"Not a file: {path}"
        )

    with open(path, "rb") as f:
        text = _decode(f.read())

    delimiter = _sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)

    header: list[str] | None = None
    for row in reader:
        if any(cell.strip() for cell in row):
            header = row
            break
    if header is None:
        return FlagListParseResult(
            status="error", filename=filename, error="The file is empty."
        )

    columns: dict[str, int] = {}
    for i, name in enumerate(header):
        columns.setdefault(_compact(name), i)

    id_idx = _find_column(columns, ID_COLUMNS)
    ts_idx = _find_column(columns, TS_COLUMNS)
    if id_idx is None or ts_idx is None:
        found = [h.strip() for h in header if h.strip()]
        shown = ", ".join(found[:_MAX_COLUMNS_IN_ERROR])
        if len(found) > _MAX_COLUMNS_IN_ERROR:
            shown += f", ... ({len(found)} columns)"
        return FlagListParseResult(
            status="error",
            filename=filename,
            error=(
                "The list needs an animal ID column and a timestamp column. "
                f"Found columns: {shown or '(none)'}"
            ),
        )

    gt_idx = _find_column(columns, GROUND_TRUTH_COLUMNS)
    pred_idx = _find_column(columns, PREDICTION_COLUMNS)
    kind_idx = _find_column(columns, KIND_COLUMNS)
    # A semicolon-delimited file may well use decimal commas ("12,5").
    decimal_comma = delimiter == ";"

    rows: list[FlagListRow] = []
    total_rows = 0
    skipped_rows = 0
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        total_rows += 1

        animal_id = _normalise_id(_cell(row, id_idx))
        ts = _to_float(_cell(row, ts_idx), decimal_comma)
        if not animal_id or ts is None:
            skipped_rows += 1
            continue

        ground_truth = (
            _to_binary_int(_to_float(_cell(row, gt_idx), decimal_comma))
            if gt_idx is not None
            else None
        )
        prediction = (
            _to_float(_cell(row, pred_idx), decimal_comma)
            if pred_idx is not None
            else None
        )

        if gt_idx is not None and pred_idx is not None:
            kind = _kind_from_scores(ground_truth, prediction)
        elif gt_idx is not None:
            kind = _kind_from_ground_truth(ground_truth)
        elif kind_idx is not None:
            kind = _kind_from_label(_cell(row, kind_idx))
        else:
            kind = "flag"

        rows.append(
            FlagListRow(
                animal_id=animal_id,
                ts=ts,
                kind=kind,
                prediction=prediction,
                ground_truth=ground_truth,
            )
        )

    return FlagListParseResult(
        status="ok",
        filename=filename,
        rows=rows,
        total_rows=total_rows,
        skipped_rows=skipped_rows,
    )


@router.post("/parse", response_model=FlagListParseResult)
def parse_flag_list(payload: FlagListParseRequest) -> FlagListParseResult:
    try:
        return _parse_flag_list(payload.path)
    except Exception as e:
        return FlagListParseResult(
            status="error",
            filename=os.path.basename(payload.path),
            error=f"Could not read the list: {e}",
        )


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _match_flag_list_files(
    animal_ids: list[str], folders: list[str]
) -> FlagListMatchResult:
    # Distinct requested IDs in request order, echoed exactly as sent. Blank
    # IDs can't match anything and are ignored.
    requested: list[str] = []
    seen_requested: set[str] = set()
    for animal_id in animal_ids:
        if animal_id.strip() and animal_id not in seen_requested:
            seen_requested.add(animal_id)
            requested.append(animal_id)
    wanted = {animal_id.strip().lower() for animal_id in requested}

    missing_folders: list[str] = []
    walked: set[str] = set()
    # lower-cased stem -> {path key -> path}; keyed so a recording reachable
    # through overlapping folders is listed once.
    index: dict[str, dict[str, str]] = {}
    all_files: set[str] = set()

    for folder in folders:
        if not os.path.isdir(folder):
            if folder not in missing_folders:
                missing_folders.append(folder)
            continue
        folder_key = _path_key(folder)
        if folder_key in walked:
            continue
        walked.add(folder_key)

        for root, _dirs, names in os.walk(folder):
            for name in names:
                stem, ext = os.path.splitext(name)
                if ext.lower() not in RECORDING_EXTENSIONS:
                    continue
                full_path = os.path.normpath(os.path.join(root, name))
                key = _path_key(full_path)
                all_files.add(key)
                stem_key = stem.lower()
                if stem_key in wanted:
                    index.setdefault(stem_key, {}).setdefault(key, full_path)

    def sort_key(path: str) -> tuple[int, str]:
        is_adicht = os.path.splitext(path)[1].lower() == ".adicht"
        return (0 if is_adicht else 1, path)

    matches: list[FlagListMatch] = []
    missing: list[str] = []
    for animal_id in requested:
        found = index.get(animal_id.strip().lower())
        if found:
            matches.append(
                FlagListMatch(
                    animal_id=animal_id, paths=sorted(found.values(), key=sort_key)
                )
            )
        else:
            missing.append(animal_id)

    return FlagListMatchResult(
        status="ok",
        matches=matches,
        missing=missing,
        missing_folders=missing_folders,
        scanned_files=len(all_files),
    )


@router.post("/match", response_model=FlagListMatchResult)
def match_flag_list_files(payload: FlagListMatchRequest) -> FlagListMatchResult:
    try:
        return _match_flag_list_files(payload.animal_ids, payload.folders)
    except Exception as e:
        return FlagListMatchResult(
            status="error", error=f"Could not search the folders: {e}"
        )
