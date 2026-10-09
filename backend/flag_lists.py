"""FP/FN review lists: parse a classifier's flagged-beat CSV and locate the
recordings it refers to.

Both endpoints report problems in-band (HTTP 200 with status="error"), the
same way backend/files.py reports per-file import failures, so the renderer
never has to special-case a thrown request.
"""

import codecs
import csv
import itertools
import math
import os
import re
import time

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

# The picker also offers "All files", so a wrong pick (a multi-GB export, a
# recording) must not be read into memory. A real list is a few hundred KB.
MAX_LIST_BYTES = 50 * 1024 * 1024
# Each parsed row costs ~1 KB of Python objects (plus its JSON copy), so a
# 50 MB file of short rows could still exhaust memory. A list for human review
# is hundreds of rows; this is a ceiling, not a target.
MAX_LIST_ROWS = 250_000
# The header line must fit in this many characters to be sniffed.
_MAX_HEADER_CHARS = 256 * 1024

# Match-scan limits: stop (and say so) rather than hang on a huge or hostile tree.
MAX_SCAN_ENTRIES = 2_000_000
MAX_SCAN_SECONDS = 120.0

_TRUE_WORDS = {"true", "yes"}
_FALSE_WORDS = {"false", "no"}
_FLOAT_ID = re.compile(r"(\d+)\.0+")


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


def _sniff_delimiter(header: str) -> str:
    """Pick comma / tab / semicolon by whichever splits the header line into the
    most fields. The data rows here are full of empty cells, which makes
    csv.Sniffer unreliable, so we only look at the header."""
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
    # float() accepts digit-group underscores ("1_0" -> 10.0); a list never
    # means that, so treat it as unreadable.
    if not text or "_" in text:
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


def _parse_ground_truth(text: str, decimal_comma: bool) -> int | None:
    word = text.lower()
    if word in _TRUE_WORDS:
        return 1
    if word in _FALSE_WORDS:
        return 0
    return _to_binary_int(_to_float(text, decimal_comma))


def _normalise_id(raw: str) -> str:
    animal_id = raw.strip()
    # A file/filename column may hold a path: keep just the file name.
    if "/" in animal_id or "\\" in animal_id:
        animal_id = re.split(r"[\\/]", animal_id)[-1].strip()
    lowered = animal_id.lower()
    for ext in RECORDING_EXTENSIONS:
        if lowered.endswith(ext):
            animal_id = animal_id[: -len(ext)].strip()
            break
    # Spreadsheet exports turn numeric IDs into floats ("682251.0").
    float_id = _FLOAT_ID.fullmatch(animal_id)
    if float_id:
        animal_id = float_id.group(1)
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


class _MalformedList(Exception):
    def __init__(self, line: int, reason: str):
        super().__init__(reason)
        self.line = line
        self.reason = reason


def _checked_rows(reader, line_offset: int):
    """Yield the reader's rows, turning a csv.Error into _MalformedList carrying
    the line the bad record *started* on (an unclosed quote only errors at the
    end of the file, far from where it opened)."""
    while True:
        start = reader.line_num + line_offset + 1
        try:
            row = next(reader)
        except StopIteration:
            return
        except csv.Error as e:
            reason = (
                "an unclosed quote?"
                if "unexpected end of data" in str(e)
                else str(e)
            )
            raise _MalformedList(start, reason) from None
        yield row


def _has_utf16_bom(path: str) -> bool:
    with open(path, "rb") as f:
        return f.read(2) in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)


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

    size = os.path.getsize(path)
    if size > MAX_LIST_BYTES:
        return FlagListParseResult(
            status="error",
            filename=filename,
            error=(
                f"The file is {size / (1024 * 1024):,.1f} MB, far larger than an FP/FN "
                f"list (limit {MAX_LIST_BYTES // (1024 * 1024)} MB). Did you pick the "
                "wrong file?"
            ),
        )

    # latin-1 maps every byte, so the second attempt can't fail; good enough
    # for the ASCII IDs and numbers we read. Streamed, so the file is never
    # held in memory as bytes + text + a copy.
    encodings = ("utf-16",) if _has_utf16_bom(path) else ("utf-8-sig", "latin-1")
    for i, encoding in enumerate(encodings):
        try:
            with open(path, "r", encoding=encoding, newline="") as f:
                return _parse_stream(f, filename)
        except UnicodeDecodeError:
            if i == len(encodings) - 1:
                raise
    raise AssertionError("unreachable")


def _parse_stream(f, filename: str) -> FlagListParseResult:
    # Sniff the delimiter from the first non-blank line only; the lines read
    # here are handed back to the csv reader ahead of the rest of the stream.
    header_line = ""
    blank_lines = 0
    while True:
        line = f.readline(_MAX_HEADER_CHARS)
        if not line:
            break
        if len(line) >= _MAX_HEADER_CHARS and not line.endswith(("\n", "\r")):
            return FlagListParseResult(
                status="error",
                filename=filename,
                error="The first line of the file is far too long to be a list header.",
            )
        if line.strip():
            header_line = line
            break
        blank_lines += 1

    delimiter = _sniff_delimiter(header_line)
    reader = csv.reader(
        itertools.chain([header_line] if header_line else [], f),
        delimiter=delimiter,
        strict=True,
    )

    try:
        return _parse_rows(_checked_rows(reader, blank_lines), delimiter, filename)
    except _MalformedList as e:
        return FlagListParseResult(
            status="error",
            filename=filename,
            error=f"Line {e.line} of the list is malformed ({e.reason})",
        )


def _parse_rows(rows_in, delimiter: str, filename: str) -> FlagListParseResult:
    header: list[str] | None = None
    for row in rows_in:
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
    for row in rows_in:
        if not any(cell.strip() for cell in row):
            continue
        total_rows += 1
        if total_rows > MAX_LIST_ROWS:
            return FlagListParseResult(
                status="error",
                filename=filename,
                error=(
                    f"The list has more than {MAX_LIST_ROWS:,} rows, far more than an "
                    "FP/FN list. Did you pick the wrong file?"
                ),
            )

        animal_id = _normalise_id(_cell(row, id_idx))
        ts = _to_float(_cell(row, ts_idx), decimal_comma)
        if not animal_id or ts is None:
            skipped_rows += 1
            continue

        ground_truth = (
            _parse_ground_truth(_cell(row, gt_idx), decimal_comma)
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


def _real_path_key(path: str) -> str:
    """Like _path_key, but resolves links so a recording reachable through a
    symlink/junction is recognised as the same file."""
    try:
        return os.path.normcase(os.path.realpath(path))
    except OSError:
        return _path_key(path)


_WIN_LONG_PREFIX = "\\\\?\\"


def _walkable(folder: str) -> str:
    """The path to hand to os.walk. On Windows the absolute folder gets the
    extended-length prefix so trees deeper than MAX_PATH (260) are readable;
    elsewhere it is the folder unchanged. Callers strip the prefix again with
    `root[len(walkable):]`, so returned paths never carry it."""
    if os.name != "nt":
        return folder
    absolute = os.path.abspath(folder)
    if absolute.startswith(_WIN_LONG_PREFIX):
        return absolute
    if absolute.startswith("\\\\"):  # UNC: \\server\share -> \\?\UNC\server\share
        return _WIN_LONG_PREFIX + "UNC\\" + absolute[2:]
    return _WIN_LONG_PREFIX + absolute


def _dir_identity(path: str) -> tuple:
    """A key that is equal for two paths naming the same directory (a symlink or
    junction and its target), used to avoid walking a directory twice and to
    break link loops. (st_dev, st_ino) where the filesystem provides them,
    else the resolved path."""
    try:
        st = os.stat(path)
        if st.st_ino:
            return ("id", st.st_dev, st.st_ino)
    except OSError:
        pass
    return ("path", _real_path_key(path))


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
    to_walk: list[str] = []
    walked: set[str] = set()
    for folder in folders:
        if not os.path.isdir(folder):
            if folder not in missing_folders:
                missing_folders.append(folder)
            continue
        folder_key = _path_key(folder)
        if folder_key not in walked:
            walked.add(folder_key)
            to_walk.append(folder)

    # lower-cased stem -> {resolved path key -> path}; keyed so a recording
    # reachable through overlapping folders or links is listed once.
    index: dict[str, dict[str, str]] = {}
    scanned_files = 0
    skipped_dirs = 0
    truncated = False
    entries_seen = 0
    # Directories already walked (across all folders): a revisit is either an
    # overlapping folder or a link loop, and its files are already indexed.
    visited_dirs: set[tuple] = set()
    started = time.monotonic()

    def on_walk_error(_error: OSError) -> None:
        nonlocal skipped_dirs
        skipped_dirs += 1

    for folder in to_walk:
        walk_top = _walkable(folder)
        for root, dirs, names in os.walk(walk_top, onerror=on_walk_error):
            if entries_seen >= MAX_SCAN_ENTRIES or (
                time.monotonic() - started > MAX_SCAN_SECONDS
            ):
                truncated = True
                break

            identity = _dir_identity(root)
            if identity in visited_dirs:
                dirs[:] = []
                continue
            visited_dirs.add(identity)

            entries_seen += len(names) + 1
            relative = root[len(walk_top):].lstrip("\\/")
            for name in names:
                stem, ext = os.path.splitext(name)
                if ext.lower() not in RECORDING_EXTENSIONS:
                    continue
                scanned_files += 1
                stem_key = stem.lower()
                if stem_key in wanted:
                    full_path = os.path.normpath(os.path.join(folder, relative, name))
                    index.setdefault(stem_key, {}).setdefault(
                        _real_path_key(full_path), full_path
                    )
        if truncated:
            break

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
        scanned_files=scanned_files,
        skipped_dirs=skipped_dirs,
        truncated=truncated,
    )


@router.post("/match", response_model=FlagListMatchResult)
def match_flag_list_files(payload: FlagListMatchRequest) -> FlagListMatchResult:
    try:
        return _match_flag_list_files(payload.animal_ids, payload.folders)
    except Exception as e:
        return FlagListMatchResult(
            status="error", error=f"Could not search the folders: {e}"
        )
