"""Report labels: an .xlsx report (typically exported by the original PySide
app, possibly edited by hand) whose per-beat labels are shown over the open
recording, matched to our own detected beats. Read-only: our saved review is
never touched.

Labels: 2 = confirmed arrhythmia, 1 = detected but not confirmed/rejected,
0 = not detected (normal), -1 = rejected (normal).

Every endpoint reports problems in-band (HTTP 200 with status="error"), the
same way backend/files.py and backend/flag_lists.py do, so the renderer never
has to special-case a thrown request.

State: `app.state.report_cache[recording_path]` holds one `_Entry` (the parsed
report as numpy arrays) per recording. An entry is immutable after it is built,
except for `_Entry.memo`, which caches the result of matching it against the
recording's beat frame (see `_matching`).
"""

import math
import os
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd
from fastapi import APIRouter, Request

from backend import categories
from backend.models import (
    ReportBadDataRange,
    ReportBeat,
    ReportBeatResult,
    ReportCandidatesRequest,
    ReportCandidatesResult,
    ReportLoadRequest,
    ReportLoadResult,
    ReportNavigationResult,
    ReportSettingDifference,
    ReportSummary,
    ReportUnloadRequest,
    ReportUnloadResult,
    ReportWindowResult,
)

router = APIRouter(prefix="/reports", tags=["reports"])

# A report beat and one of ours are "the same beat" when their timestamps are
# this close (seconds). The PySide app and this one detect on the same signal
# with the same algorithm, so in practice most match exactly; the tolerance
# absorbs a hand-edited ts or a slightly different filter.
MATCH_TOLERANCE_S = 0.010
# Float slack: a ts that is "exactly" on the tolerance edge, or "exactly" equal,
# differs from it by rounding noise, not by a real amount.
_EPS = 1e-9

# Reports are a few hundred KB; the file picker also offers "All files", so a
# wrong pick (a multi-GB export) must not be read into memory.
MAX_REPORT_BYTES = 50 * 1024 * 1024
REPORT_EXTENSIONS = (".xlsx", ".xlsm")

# Column-name candidates in priority order. Names are compared after
# lower-casing and dropping whitespace / underscores / hyphens (see _compact).
TS_COLUMNS = ("ts", "time", "timestamp", "t")
PRIMARY_LABEL_COLUMN = "annot_any_arrhythmia"
LABEL_COLUMNS = (PRIMARY_LABEL_COLUMN, "label", "labels", "annotation", "annot", "class")
VALID_LABELS = (2, 1, 0, -1)

# Match of a report beat against our beats (codes index MATCH_NAMES).
EXACT, NEAR, NONE, UNKNOWN = 0, 1, 2, 3
MATCH_NAMES = ("exact", "near", "none", "unknown")

# Agreement between the report's decision and ours (codes index AGREEMENT_NAMES).
AGREE, REPORT_ONLY, OURS_ONLY, PENDING, UNMATCHED, AGREEMENT_UNKNOWN = range(6)
AGREEMENT_NAMES = (
    "agree",
    "report_only",
    "ours_only",
    "pending",
    "unmatched",
    "unknown",
)

# A beat's decision, on either side.
NORMAL, FLAGGED, ARRHYTHMIA = 0, 1, 2

_MAX_COLUMNS_IN_ERROR = 20
_OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class _ReportError(Exception):
    """A problem with the report file, worded for the technician."""


def _compact(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", name.strip().lower())


# ---------------------------------------------------------------------------
# Parsed report + matching result
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class _Matching:
    """The report matched against one particular beat frame (arrays are
    parallel to the report's, sorted by ts). Immutable once built."""

    match: np.ndarray  # int8 codes into MATCH_NAMES
    our_ts: np.ndarray  # float64, NaN where there is no matched beat of ours
    agreement: np.ndarray  # int8 codes into AGREEMENT_NAMES
    matched: int
    extra_ours: int
    has_our_beats: bool
    targets: dict[str, list[float]]


@dataclass(eq=False)
class _Entry:
    # Everything but `memo` is set when the report is loaded and never changes.
    summary: ReportSummary  # settings_differences/warnings are filled per request
    ts: np.ndarray  # float64, strictly ascending
    labels: np.ndarray  # int8, one of 2/1/0/-1
    decisions: np.ndarray  # int8, the report's decision per beat (NORMAL/...)
    category_names: list[str]
    category_flags: np.ndarray  # bool, shape (n, len(category_names))
    settings_raw: dict[str, object]
    # Static warnings; the settings warning depends on the live settings, so it
    # is built per request (see _summary_for), between these two groups.
    warnings_before_settings: list[str]
    warnings_after_settings: list[str]
    # The matching for "our beats don't exist yet" (set right after building).
    unknown: _Matching | None = None
    # (the beat frame it was computed for, the matching). Replaced as a single
    # attribute store, so a reader sees either the old pair or the new one.
    memo: tuple | None = None

    def categories_for(self, idx: np.ndarray) -> list[list[str]]:
        if not self.category_names:
            return [[] for _ in range(len(idx))]
        flags = self.category_flags[idx]
        any_flag = flags.any(axis=1)
        names = self.category_names
        return [
            [names[j] for j in np.flatnonzero(row)] if has else []
            for row, has in zip(flags, any_flag)
        ]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _is_blank(value) -> bool:
    if value is None or value is pd.NA or value is pd.NaT:
        return True
    if isinstance(value, (float, np.floating)):
        return math.isnan(value)
    return isinstance(value, str) and not value.strip()


def _stringify(value) -> str:
    """A settings value as the text shown to the technician; blank/NaN -> ''."""
    if _is_blank(value):
        return ""
    if isinstance(value, (bool, np.bool_)):
        return "True" if value else "False"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if math.isfinite(number) and number == int(number) and abs(number) < 1e15:
            return str(int(number))
        return str(number)
    return str(value).strip()


def _plain(value):
    """A numpy scalar as the equivalent Python one."""
    return value.item() if isinstance(value, np.generic) else value


def _as_floats(series: pd.Series) -> np.ndarray:
    """The column as float64, NaN wherever a cell isn't a number (numeric
    strings such as "2" or "0.5" count as numbers)."""
    try:
        if series.dtype == bool:
            return series.to_numpy(dtype=float)
        if pd.api.types.is_datetime64_any_dtype(
            series.dtype
        ) or pd.api.types.is_timedelta64_dtype(series.dtype):
            return np.full(len(series), np.nan)
        return pd.to_numeric(series, errors="coerce").to_numpy(
            dtype=float, na_value=np.nan
        )
    except Exception:
        return np.full(len(series), np.nan)


def _as_bool(value) -> bool | None:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, float, np.number)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        word = value.strip().lower()
        if word in ("true", "yes", "1", "1.0"):
            return True
        if word in ("false", "no", "0", "0.0"):
            return False
    return None


def _as_float(value) -> float | None:
    if isinstance(value, (bool, np.bool_)):
        return float(value)
    if isinstance(value, (int, float, np.number)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _settings_equal(report_value, our_value) -> bool:
    """Whether a report setting equals ours: blanks (NaN/None/'') equal each
    other, numbers compare numerically (1 == 1.0), bools accept True/'True'/1."""
    report_blank, our_blank = _is_blank(report_value), _is_blank(our_value)
    if report_blank or our_blank:
        return report_blank and our_blank
    if isinstance(our_value, (bool, np.bool_)):
        as_bool = _as_bool(report_value)
        return as_bool is not None and as_bool == bool(our_value)
    if isinstance(our_value, (int, float, np.number)):
        as_float = _as_float(report_value)
        return as_float is not None and math.isclose(
            as_float, float(our_value), rel_tol=1e-9, abs_tol=1e-12
        )
    return str(report_value).strip() == str(our_value).strip()


def _current_settings(state) -> dict[str, object]:
    """The live beat + arrhythmia settings as one flat dict (the settings are
    plain objects whose attributes are the values; see backend/settings.py)."""
    current: dict[str, object] = {}
    for settings in (
        getattr(state, "beat_settings", None),
        getattr(state, "arrhythmia_settings", None),
    ):
        current.update(dict(getattr(settings, "__dict__", {})))
    return current


def _settings_differences(
    settings_raw: dict[str, object], state
) -> list[ReportSettingDifference]:
    current = _current_settings(state)
    differences = []
    for name, report_value in settings_raw.items():
        if name not in current:
            continue
        our_value = current[name]
        if not _settings_equal(report_value, our_value):
            differences.append(
                ReportSettingDifference(
                    name=name,
                    report_value=_stringify(report_value),
                    our_value=_stringify(our_value),
                )
            )
    return differences


def name_matches_recording(report_path: str, recording_path: str) -> bool:
    """The report's file stem equals the recording's (case-insensitively), or
    starts with it followed by a non-alphanumeric character - this app's own
    export is `<stem>-report.xlsx`."""
    report_stem = os.path.splitext(os.path.basename(report_path))[0].lower()
    recording_stem = os.path.splitext(os.path.basename(recording_path))[0].lower()
    if not recording_stem:
        return False
    if report_stem == recording_stem:
        return True
    return report_stem.startswith(recording_stem) and not report_stem[
        len(recording_stem)
    ].isalnum()


@dataclass
class _Columns:
    ts: object
    label: object
    # No label column: labels are derived from our own export's review_state
    # (+ any_arrhythmia) columns instead.
    derived: bool
    by_name: dict[str, object]  # compacted header -> first column with it


def _find_columns(df: pd.DataFrame) -> _Columns | None:
    by_name: dict[str, object] = {}
    for column in df.columns:
        by_name.setdefault(_compact(str(column)), column)
    ts_col = next(
        (by_name[_compact(c)] for c in TS_COLUMNS if _compact(c) in by_name), None
    )
    if ts_col is None:
        return None
    for candidate in LABEL_COLUMNS:
        if _compact(candidate) in by_name:
            return _Columns(ts_col, by_name[_compact(candidate)], False, by_name)
    # This app's own export has no annotation column, only the review outcome.
    if "reviewstate" in by_name:
        return _Columns(ts_col, by_name["reviewstate"], True, by_name)
    return None


def _pick_sheet(sheets: dict[str, pd.DataFrame]) -> tuple[str, pd.DataFrame, _Columns]:
    """The `beats` sheet if it has the columns, else the first sheet that does."""
    ordered = sorted(sheets, key=lambda name: _compact(str(name)) != "beats")
    for name in ordered:
        columns = _find_columns(sheets[name])
        if columns is not None:
            return str(name), sheets[name], columns

    shown_sheet = next(
        (n for n in sheets if _compact(str(n)) == "beats"), next(iter(sheets), None)
    )
    found = [str(c) for c in sheets[shown_sheet].columns] if shown_sheet is not None else []
    shown = ", ".join(found[:_MAX_COLUMNS_IN_ERROR])
    if len(found) > _MAX_COLUMNS_IN_ERROR:
        shown += f", ... ({len(found)} columns)"
    raise _ReportError(
        "No sheet has both a timestamp column (ts, time, timestamp or t) and a "
        f"label column ({PRIMARY_LABEL_COLUMN}, label, annotation, ...). "
        f"Columns found{f' in sheet {shown_sheet!r}' if shown_sheet else ''}: "
        f"{shown or '(none)'}"
    )


def _category_columns(df: pd.DataFrame, columns: _Columns) -> list[tuple[object, str]]:
    """(column, category name) for every per-category flag column: each
    annot_<x> other than the label column (-> <x>), except the any_arrhythmia
    roll-up. For this app's own export, its category flag columns."""
    found: list[tuple[object, str]] = []
    seen: set[str] = set()
    for column in df.columns:
        if column == columns.label:
            continue
        if columns.derived:
            name = str(column).strip()
            if name not in categories.REASSIGNABLE_CATEGORIES:
                continue
        else:
            match = re.fullmatch(r"annot[\s_\-]+(.+)", str(column).strip(), re.I)
            if not match:
                continue
            name = re.sub(r"[\s\-]+", "_", match.group(1).strip().lower())
        if name == "any_arrhythmia" or name in seen:
            continue
        seen.add(name)
        found.append((column, name))
    return found


def _label_values(df: pd.DataFrame, columns: _Columns) -> np.ndarray:
    if not columns.derived:
        return _as_floats(df[columns.label])
    # review_state: confirmed -> 2, rejected -> -1, otherwise 1 if the detector
    # flagged the beat (any_arrhythmia) and 0 if not.
    state = df[columns.label].map(
        lambda v: "" if _is_blank(v) else str(v).strip().lower()
    )
    any_col = columns.by_name.get("anyarrhythmia")
    flagged = (
        np.nan_to_num(_as_floats(df[any_col])) != 0
        if any_col is not None
        else np.zeros(len(df), dtype=bool)
    )
    state_values = state.to_numpy(dtype=object)
    return np.select(
        [state_values == "confirmed", state_values == "rejected", flagged],
        [2.0, -1.0, 1.0],
        default=0.0,
    )


def _read_settings(sheets: dict[str, pd.DataFrame]) -> dict[str, object]:
    """First row of the `settings` sheet, by column."""
    for name, df in sheets.items():
        if _compact(str(name)) == "settings" and len(df) > 0:
            return {
                str(column).strip(): _plain(df[column].iloc[0]) for column in df.columns
            }
    return {}


def _read_bad_data_marks(sheets: dict[str, pd.DataFrame]) -> list[ReportBadDataRange]:
    for name, df in sheets.items():
        if _compact(str(name)) != "baddatamarks":
            continue
        by_name = {_compact(str(c)): c for c in reversed(list(df.columns))}
        if "start" not in by_name or "stop" not in by_name:
            return []
        start = _as_floats(df[by_name["start"]])
        stop = _as_floats(df[by_name["stop"]])
        marks = []
        for a, b in zip(start.tolist(), stop.tolist()):
            if math.isfinite(a) and math.isfinite(b):
                marks.append(ReportBadDataRange(start=min(a, b), stop=max(a, b)))
        return marks
    return []


def _recording_span(cache_entry) -> tuple[float, float] | None:
    """(first, last) time of the recording's signal, or None if unknown."""
    try:
        time_column = cache_entry["time_column"]
        if time_column is None:
            return None
        times = cache_entry["df"][time_column]
        lo, hi = float(times.min()), float(times.max())
    except Exception:
        return None
    return (lo, hi) if math.isfinite(lo) and math.isfinite(hi) else None


def _read_workbook(report_path: str) -> dict[str, pd.DataFrame]:
    if not os.path.exists(report_path):
        raise _ReportError(f"File not found: {report_path}")
    if not os.path.isfile(report_path):
        raise _ReportError(f"Not a file: {report_path}")
    if os.path.splitext(report_path)[1].lower() == ".xls":
        raise _ReportError(
            "Old .xls spreadsheets are not supported. Open it in Excel and Save "
            "it as .xlsx."
        )

    size = os.path.getsize(report_path)
    if size > MAX_REPORT_BYTES:
        raise _ReportError(
            f"The file is {size / (1024 * 1024):,.1f} MB, far larger than a report "
            f"(limit {MAX_REPORT_BYTES // (1024 * 1024)} MB). Did you pick the "
            "wrong file?"
        )

    try:
        with open(report_path, "rb") as f:
            head = f.read(len(_OLE2_SIGNATURE))
        if head == _OLE2_SIGNATURE:
            raise _ReportError(
                "This is an old .xls spreadsheet or a password-protected workbook. "
                "Save it as an unprotected .xlsx."
            )
        sheets = pd.read_excel(report_path, sheet_name=None, engine="openpyxl")
    except _ReportError:
        raise
    except Exception as e:
        raise _ReportError(
            f"Could not read {os.path.basename(report_path)!r} as an Excel workbook "
            f"({type(e).__name__}: {e}). It may be corrupt, password-protected or "
            "not a spreadsheet."
        ) from None
    if not sheets:
        raise _ReportError("The workbook has no sheets.")
    return sheets


def _build_entry(report_path: str, recording_path: str, state) -> _Entry:
    sheets = _read_workbook(report_path)
    sheet_name, df, columns = _pick_sheet(sheets)

    ts = _as_floats(df[columns.ts])
    label = _label_values(df, columns)
    # Wholly empty rows (spreadsheet padding) are not data rows.
    nonblank = ~df.isna().all(axis=1).to_numpy()
    usable = np.isfinite(ts) & np.isin(label, VALID_LABELS)
    skipped_rows = int(np.count_nonzero(nonblank & ~usable))

    usable_rows = np.flatnonzero(usable)
    if usable_rows.size == 0:
        raise _ReportError(
            f"No usable beats in sheet {sheet_name!r}: every row lacks a valid "
            "timestamp or a label of 2, 1, 0 or -1."
            if skipped_rows
            else f"No usable beats in sheet {sheet_name!r}: it has no data rows."
        )
    # Sorted by ts; for a duplicated ts the first row in the sheet wins.
    sorted_ts, first = np.unique(ts[usable_rows], return_index=True)
    skipped_rows += int(usable_rows.size - sorted_ts.size)
    rows = usable_rows[first]

    labels = label[rows].astype(np.int8)
    decisions = np.select(
        [labels == 2, labels == 1], [ARRHYTHMIA, FLAGGED], default=NORMAL
    ).astype(np.int8)

    category_cols = _category_columns(df, columns)
    if category_cols:
        category_flags = np.column_stack(
            [np.nan_to_num(_as_floats(df[c]))[rows] != 0 for c, _ in category_cols]
        )
    else:
        category_flags = np.zeros((rows.size, 0), dtype=bool)

    settings_raw = _read_settings(sheets)
    filename = os.path.basename(report_path)
    name_ok = name_matches_recording(report_path, recording_path)
    counts = {str(v): int(np.count_nonzero(labels == v)) for v in VALID_LABELS}

    warnings_before: list[str] = []
    span = _recording_span(state.signal_cache.get(recording_path) or {})
    if span is not None:
        outside = int(
            np.count_nonzero(
                (sorted_ts < span[0] - MATCH_TOLERANCE_S)
                | (sorted_ts > span[1] + MATCH_TOLERANCE_S)
            )
        )
        if outside:
            warnings_before.append(
                f"{outside:,} of the report's {sorted_ts.size:,} beats lie outside "
                f"the recording ({span[0]:g} to {span[1]:g} s) - this report may "
                "belong to a different recording."
            )
    if not name_ok:
        warnings_before.append(
            f"The report's file name ({filename!r}) does not match the recording "
            f"({os.path.basename(recording_path)!r}). Make sure it belongs to this "
            "recording."
        )
    warnings_after: list[str] = []
    if skipped_rows:
        warnings_after.append(
            f"{skipped_rows:,} row(s) skipped: no valid timestamp or label, or a "
            "duplicate timestamp."
        )
    if columns.derived:
        warnings_after.append(
            "The report has no annotation column; labels were derived from its "
            "review_state and any_arrhythmia columns (an export from this app)."
        )
    elif _compact(str(columns.label)) != _compact(PRIMARY_LABEL_COLUMN):
        warnings_after.append(
            f"No {PRIMARY_LABEL_COLUMN} column; using {str(columns.label)!r} for the "
            "labels."
        )

    summary = ReportSummary(
        report_path=report_path,
        filename=filename,
        sheet=sheet_name,
        label_column=str(columns.label),
        category_columns=[str(c) for c, _ in category_cols],
        total_beats=int(sorted_ts.size),
        counts=counts,
        ts_start=float(sorted_ts[0]),
        ts_end=float(sorted_ts[-1]),
        name_matches_recording=name_ok,
        settings={k: _stringify(v) for k, v in settings_raw.items()},
        bad_data_marks=_read_bad_data_marks(sheets),
        skipped_rows=skipped_rows,
    )
    entry = _Entry(
        summary=summary,
        ts=sorted_ts,
        labels=labels,
        decisions=decisions,
        category_names=[name for _, name in category_cols],
        category_flags=category_flags,
        settings_raw=settings_raw,
        warnings_before_settings=warnings_before,
        warnings_after_settings=warnings_after,
    )
    entry.unknown = _unknown_matching(entry)
    return entry


def _summary_for(entry: _Entry, state) -> ReportSummary:
    """The summary with the settings comparison against the CURRENT settings
    (they can change after the report was loaded)."""
    differences = _settings_differences(entry.settings_raw, state)
    warnings = list(entry.warnings_before_settings)
    if differences:
        names = ", ".join(d.name for d in differences)
        warnings.append(
            f"{len(differences)} detection setting(s) differ from the current "
            f"settings: {names}."
        )
    warnings.extend(entry.warnings_after_settings)
    return entry.summary.model_copy(
        update={"settings_differences": differences, "warnings": warnings}
    )


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _nearest(sorted_values: np.ndarray, queries: np.ndarray):
    """For each query, the index into `sorted_values` (non-empty, ascending) of
    its nearest value and the distance to it."""
    insertion = np.searchsorted(sorted_values, queries)
    right = np.minimum(insertion, sorted_values.size - 1)
    left = np.maximum(insertion - 1, 0)
    d_right = np.abs(sorted_values[right] - queries)
    d_left = np.abs(queries - sorted_values[left])
    use_right = d_right < d_left
    return np.where(use_right, right, left), np.where(use_right, d_right, d_left)


def _targets(
    entry: _Entry, match: np.ndarray, agreement: np.ndarray
) -> dict[str, list[float]]:
    disagree = (agreement == REPORT_ONLY) | (agreement == OURS_ONLY)
    return {
        "arrhythmia": entry.ts[entry.labels == 2].tolist(),
        "unreviewed": entry.ts[entry.labels == 1].tolist(),
        "rejected": entry.ts[entry.labels == -1].tolist(),
        "disagreements": entry.ts[disagree].tolist(),
        "not_detected": entry.ts[match == NONE].tolist(),
    }


def _unknown_matching(entry: _Entry) -> _Matching:
    n = entry.ts.size
    match = np.full(n, UNKNOWN, dtype=np.int8)
    agreement = np.full(n, AGREEMENT_UNKNOWN, dtype=np.int8)
    return _Matching(
        match=match,
        our_ts=np.full(n, np.nan),
        agreement=agreement,
        matched=0,
        extra_ours=0,
        has_our_beats=False,
        targets=_targets(entry, match, agreement),
    )


def _truthy(column: pd.Series) -> np.ndarray:
    # A category column can hold NaN next to real booleans (a beat too close to
    # the signal's edge to evaluate): not evaluated means not flagged.
    return column.fillna(False).astype(bool).to_numpy()


def _our_decisions(frame: pd.DataFrame) -> np.ndarray:
    """Our decision per row of the beat frame: a confirmed beat is an
    arrhythmia, a rejected one normal; otherwise a beat the detectors flagged
    is 'flagged' (detected, not reviewed) and any other normal. `review_state`
    is absent until a review action / restore adds it, and NaN where a sync had
    no value: both mean unreviewed."""
    flagged = np.zeros(len(frame), dtype=bool)
    for column in categories.ALL_OPTIONAL_COLUMNS:
        if column in frame.columns:
            flagged |= _truthy(frame[column])
    decisions = np.where(flagged, FLAGGED, NORMAL).astype(np.int8)
    if "review_state" in frame.columns:
        state = frame["review_state"]
        decisions[state.eq("confirmed").to_numpy()] = ARRHYTHMIA
        decisions[state.eq("rejected").to_numpy()] = NORMAL
    return decisions


def _compute_matching(entry: _Entry, frame: pd.DataFrame) -> _Matching:
    all_ts = frame["ts"].to_numpy(dtype=float)
    positions = np.flatnonzero(np.isfinite(all_ts))
    our_ts_sorted = all_ts[positions]
    if our_ts_sorted.size > 1 and np.any(our_ts_sorted[1:] < our_ts_sorted[:-1]):
        order = np.argsort(our_ts_sorted, kind="stable")
        positions, our_ts_sorted = positions[order], our_ts_sorted[order]

    n = entry.ts.size
    if our_ts_sorted.size == 0:
        match = np.full(n, NONE, dtype=np.int8)
        agreement = np.full(n, UNMATCHED, dtype=np.int8)
        return _Matching(
            match=match,
            our_ts=np.full(n, np.nan),
            agreement=agreement,
            matched=0,
            extra_ours=0,
            has_our_beats=True,
            targets=_targets(entry, match, agreement),
        )

    idx, dist = _nearest(our_ts_sorted, entry.ts)
    match = np.where(
        dist < _EPS, EXACT, np.where(dist <= MATCH_TOLERANCE_S + _EPS, NEAR, NONE)
    ).astype(np.int8)
    matched_mask = match != NONE
    our_ts = np.where(matched_mask, our_ts_sorted[idx], np.nan)

    ours = _our_decisions(frame)[positions[idx]]
    theirs = entry.decisions
    agreement = np.select(
        [
            ~matched_mask,
            (theirs == FLAGGED) | (ours == FLAGGED),
            theirs == ours,
            (theirs == ARRHYTHMIA) & (ours == NORMAL),
            (theirs == NORMAL) & (ours == ARRHYTHMIA),
        ],
        [UNMATCHED, PENDING, AGREE, REPORT_ONLY, OURS_ONLY],
        default=UNMATCHED,
    ).astype(np.int8)

    _, back_dist = _nearest(entry.ts, our_ts_sorted)
    extra_ours = int(np.count_nonzero(back_dist > MATCH_TOLERANCE_S + _EPS))
    return _Matching(
        match=match,
        our_ts=our_ts,
        agreement=agreement,
        matched=int(np.count_nonzero(matched_mask)),
        extra_ours=extra_ours,
        has_our_beats=True,
        targets=_targets(entry, match, agreement),
    )


def _matching(entry: _Entry, frame: pd.DataFrame | None) -> _Matching:
    """The report matched against `frame` (the recording's beat_cache entry,
    read once by the caller).

    Matching 70k beats costs tens of milliseconds and the graph asks on every
    pan/zoom, so the result is memoised against the IDENTITY of the frame it
    was computed for. Frames are replaced, never mutated in place, so `is` is
    exactly "nothing changed". Threads may race to compute it twice (harmless);
    the memo is one attribute assigned in one store, so it can never be seen
    half-updated, and a reader holding a different frame just recomputes.
    """
    if frame is None or "ts" not in frame.columns:
        entry.memo = None  # don't keep a replaced/dropped frame alive
        return entry.unknown
    memo = entry.memo
    if memo is not None and memo[0] is frame:
        return memo[1]
    result = _compute_matching(entry, frame)
    entry.memo = (frame, result)
    return result


def _match_error(error: Exception) -> str:
    return f"Could not match the report against the detected beats: {error}"


def _beats_at(entry: _Entry, matching: _Matching, idx: np.ndarray) -> list[ReportBeat]:
    ts = entry.ts[idx].tolist()
    labels = entry.labels[idx].tolist()
    match = matching.match[idx].tolist()
    our_ts = matching.our_ts[idx].tolist()
    agreement = matching.agreement[idx].tolist()
    cats = entry.categories_for(idx)
    # model_construct skips per-beat validation (every value is already a plain
    # Python float/int/str/list built above): "all beats incl. normal" over a
    # long recording is tens of thousands of these. The route's response_model
    # still validates the finished response.
    return [
        ReportBeat.model_construct(
            ts=ts[i],
            label=labels[i],
            categories=cats[i],
            match=MATCH_NAMES[match[i]],
            our_ts=None if math.isnan(our_ts[i]) else our_ts[i],
            agreement=AGREEMENT_NAMES[agreement[i]],
        )
        for i in range(len(ts))
    ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/load", response_model=ReportLoadResult)
def load_report(payload: ReportLoadRequest, request: Request) -> ReportLoadResult:
    state = request.app.state
    path = payload.recording_path
    imported = state.imported_files.get(path)
    if imported is None or imported.status != "ok":
        return ReportLoadResult(status="error", error=f"File not imported: {path}")

    try:
        entry = _build_entry(payload.report_path, path, state)
        summary = _summary_for(entry, state)
    except _ReportError as e:
        return ReportLoadResult(status="error", error=str(e))
    except Exception as e:
        return ReportLoadResult(status="error", error=f"Could not read the report: {e}")

    # A previous report is replaced only by a successfully parsed one. Parsing
    # runs without any lock, so the recording may have been evicted meanwhile:
    # store first, then re-check, and take the entry back out if so. (Evict
    # pops imported_files BEFORE report_cache, so either this check sees the
    # eviction, or evict's own pop removes what was just stored.)
    state.report_cache[path] = entry
    if path not in state.imported_files:
        if state.report_cache.get(path) is entry:
            state.report_cache.pop(path, None)
        return ReportLoadResult(status="error", error=f"File not imported: {path}")
    return ReportLoadResult(status="ok", summary=summary)


@router.post("/unload", response_model=ReportUnloadResult)
def unload_report(
    payload: ReportUnloadRequest, request: Request
) -> ReportUnloadResult:
    request.app.state.report_cache.pop(payload.recording_path, None)
    return ReportUnloadResult(status="ok")


@router.get("/state", response_model=ReportLoadResult)
def get_report_state(path: str, request: Request) -> ReportLoadResult:
    entry = request.app.state.report_cache.get(path)
    if entry is None:
        return ReportLoadResult(status="ok", summary=None)
    return ReportLoadResult(status="ok", summary=_summary_for(entry, request.app.state))


@router.get("/window", response_model=ReportWindowResult)
def get_report_window(
    path: str,
    start: float,
    end: float,
    request: Request,
    include_normal: bool = False,
) -> ReportWindowResult:
    entry = request.app.state.report_cache.get(path)
    if entry is None or math.isnan(start) or math.isnan(end) or start > end:
        return ReportWindowResult(status="ok", beats=[])
    frame = request.app.state.beat_cache.get(path)

    lo = int(np.searchsorted(entry.ts, start, side="left"))
    hi = int(np.searchsorted(entry.ts, end, side="right"))
    if hi <= lo:
        return ReportWindowResult(status="ok", beats=[])
    try:
        matching = _matching(entry, frame)
    except Exception as e:
        return ReportWindowResult(status="error", error=_match_error(e))
    idx = np.arange(lo, hi)
    if not include_normal:
        # Normal (label 0) beats are left out to keep wide windows light —
        # except ones this review confirmed as arrhythmia, the disagreement
        # the technician most needs to see. There are few of those.
        keep = (entry.labels[lo:hi] != 0) | (matching.agreement[lo:hi] == OURS_ONLY)
        idx = idx[keep]
        if idx.size == 0:
            return ReportWindowResult(status="ok", beats=[])
    return ReportWindowResult(status="ok", beats=_beats_at(entry, matching, idx))


@router.get("/navigation", response_model=ReportNavigationResult)
def get_report_navigation(path: str, request: Request) -> ReportNavigationResult:
    entry = request.app.state.report_cache.get(path)
    frame = request.app.state.beat_cache.get(path)
    if entry is None:
        return ReportNavigationResult(
            status="ok",
            targets={
                name: []
                for name in (
                    "arrhythmia",
                    "unreviewed",
                    "rejected",
                    "disagreements",
                    "not_detected",
                )
            },
            has_our_beats=frame is not None and "ts" in frame.columns,
        )
    try:
        matching = _matching(entry, frame)
    except Exception as e:
        return ReportNavigationResult(status="error", error=_match_error(e))
    return ReportNavigationResult(
        status="ok",
        targets=matching.targets,
        matched=matching.matched,
        total=int(entry.ts.size),
        extra_ours=matching.extra_ours,
        has_our_beats=matching.has_our_beats,
    )


@router.get("/beat", response_model=ReportBeatResult)
def get_report_beat(path: str, ts: float, request: Request) -> ReportBeatResult:
    entry = request.app.state.report_cache.get(path)
    if entry is None or not math.isfinite(ts):
        return ReportBeatResult(status="ok", beat=None)
    frame = request.app.state.beat_cache.get(path)

    nearest, distance = _nearest(entry.ts, np.array([ts]))
    if distance[0] > MATCH_TOLERANCE_S + _EPS:
        return ReportBeatResult(status="ok", beat=None)
    try:
        matching = _matching(entry, frame)
    except Exception as e:
        return ReportBeatResult(status="error", error=_match_error(e))
    return ReportBeatResult(
        status="ok", beat=_beats_at(entry, matching, nearest)[0]
    )


def _find_candidates(recording_path: str, folders: list[str]) -> list[str]:
    ordered: list[str] = []
    seen_folders: set[str] = set()
    for folder in [os.path.dirname(recording_path) or ".", *folders]:
        if not folder or not folder.strip():
            continue
        key = os.path.normcase(os.path.abspath(folder))
        if key not in seen_folders:
            seen_folders.add(key)
            ordered.append(folder)

    paths: list[str] = []
    seen_files: set[str] = set()
    for folder in ordered:
        try:
            with os.scandir(folder) as entries:
                names = sorted(
                    (e for e in entries if _is_candidate(e, recording_path)),
                    key=lambda e: e.name.lower(),
                )
        except OSError:
            continue  # missing or unreadable folder: skipped silently
        for e in names:
            try:
                key = os.path.normcase(os.path.realpath(e.path))
            except OSError:
                key = os.path.normcase(os.path.abspath(e.path))
            if key not in seen_files:
                seen_files.add(key)
                paths.append(e.path)
    return paths


def _is_candidate(entry: os.DirEntry, recording_path: str) -> bool:
    if os.path.splitext(entry.name)[1].lower() not in REPORT_EXTENSIONS:
        return False
    try:
        if not entry.is_file():
            return False
    except OSError:
        return False
    return name_matches_recording(entry.name, recording_path)


@router.post("/candidates", response_model=ReportCandidatesResult)
def find_report_candidates(payload: ReportCandidatesRequest) -> ReportCandidatesResult:
    if not payload.recording_path.strip():
        return ReportCandidatesResult(status="error", error="No recording was given.")
    try:
        paths = _find_candidates(payload.recording_path, payload.folders)
    except Exception as e:
        return ReportCandidatesResult(
            status="error", error=f"Could not search for reports: {e}"
        )
    return ReportCandidatesResult(status="ok", paths=paths)
