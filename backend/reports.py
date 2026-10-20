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

The workbook is read with openpyxl's read-only mode, never pandas: pandas pads
every row to the sheet's widest row, so one stray cell far to the right (or a
bogus `<dimension>`) in an otherwise tiny sheet costs minutes and gigabytes.
Here only the header's width is read, and only the rows of the sheets needed.
"""

import datetime
import math
import os
import re
import threading
from dataclasses import dataclass

import numpy as np
import openpyxl
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
# A worksheet has at most 1,048,576 rows, and a report a few hundred thousand
# at the very most; a sheet reaching this many rows is not a report. The cap
# also bounds the work a hostile file (rows numbered in the billions) can cost.
MAX_REPORT_ROWS = 2_000_000
# Columns read per sheet: the header's width, never more than this.
MAX_REPORT_COLUMNS = 200
# The header row is searched for within this many rows from the top (title
# rows above it are tolerated).
HEADER_SCAN_ROWS = 10

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
_LOCKED_MESSAGE = (
    "The report is open in another program that blocks reading — close it "
    "there and try again."
)
# Text cells spreadsheet readers (pandas, which this module replaced) take for
# an empty cell, plus Excel's error values. A row of nothing else is padding.
_NA_STRINGS = frozenset(
    {
        "",
        "#N/A",
        "#N/A N/A",
        "#NA",
        "-1.#IND",
        "-1.#QNAN",
        "-NaN",
        "-nan",
        "1.#IND",
        "1.#QNAN",
        "<NA>",
        "N/A",
        "NA",
        "NULL",
        "NaN",
        "None",
        "n/a",
        "nan",
        "null",
        "#DIV/0!",
        "#NAME?",
        "#NULL!",
        "#NUM!",
        "#REF!",
        "#VALUE!",
    }
)


class _ReportError(Exception):
    """A problem with the report file, worded for the technician."""


def _compact(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", name.strip().lower())


def _io_path(path: str) -> str:
    """`path` in a form the file APIs accept. Windows refuses paths longer than
    259 characters unless they carry the `\\\\?\\` prefix (a deep folder tree
    on a network drive gets there); other systems and short paths are left as
    given. Only for opening: messages and the summary keep the user's path."""
    if os.name != "nt" or not path or path.startswith("\\\\?\\"):
        return path
    try:
        absolute = os.path.abspath(path)
    except (OSError, ValueError):
        return path
    if len(absolute) < 260:
        return path
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


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
    # Whether our beats carry any arrhythmia flags yet. Without them (detection
    # not run, or a restored recording with empty flags) an unreviewed beat of
    # ours has no decision, so its agreement with the report is 'unknown'.
    has_our_flags: bool = False


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


def _blank_cell(value) -> bool:
    """Whether a cell counts as empty: no value, NaN, or text such as "" / "NA"
    that a spreadsheet reader takes for a missing value."""
    if value is None:
        return True
    if isinstance(value, str):
        return value in _NA_STRINGS
    return isinstance(value, float) and value != value


# Cell values that are not plain numbers or text. openpyxl returns these for
# cells with a date/time number format and for TRUE/FALSE cells.
_SPECIAL_CELLS = (bool, np.bool_, datetime.date, datetime.time, datetime.timedelta)


def _special_number(value, *, bools: bool, times: bool) -> float:
    if isinstance(value, (bool, np.bool_)):
        return float(value) if bools else math.nan
    if times:
        # A time of day or a duration (a ts column formatted h:mm:ss) is a
        # number of seconds. A full date-time is not a recording time.
        if isinstance(value, datetime.timedelta):
            return value.total_seconds()
        if isinstance(value, datetime.time):
            return (
                value.hour * 3600
                + value.minute * 60
                + value.second
                + value.microsecond / 1e6
            )
    return math.nan


def _as_floats(
    series: pd.Series, *, bools: bool = False, times: bool = False
) -> np.ndarray:
    """The column as float64, NaN wherever a cell isn't a number (numeric
    strings such as "2" or "0.5" count as numbers). TRUE/FALSE count as 1/0
    only with `bools` (flag columns; a label of True is not a label), and
    time-formatted cells as seconds only with `times` (the ts column); dates
    are never numbers."""
    n = len(series)
    values = series.to_numpy(dtype=object)
    special = None
    if any(issubclass(kind, _SPECIAL_CELLS) for kind in set(map(type, values))):
        special = np.fromiter(
            (isinstance(v, _SPECIAL_CELLS) for v in values), dtype=bool, count=n
        )
        values = values.copy()
        values[special] = None
    try:
        numbers = pd.to_numeric(pd.Series(values, dtype=object), errors="coerce")
        out = numbers.to_numpy(dtype=float, na_value=np.nan)
    except Exception:
        out = np.full(n, np.nan)
    if special is not None:
        out = np.array(out, dtype=float)  # writable whatever pandas handed back
        originals = series.to_numpy(dtype=object)
        for i in np.flatnonzero(special):
            out[i] = _special_number(originals[i], bools=bools, times=times)
    return out


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
    # Column positions (0-based) in the sheet's header.
    ts: int
    label: int
    # No label column: labels are derived from our own export's review_state
    # (+ any_arrhythmia) columns instead.
    derived: bool
    by_name: dict[str, int]  # compacted header -> first column with it


def _header_text(value) -> str:
    """A header cell as text ("" when empty). Strings are kept as written."""
    if _blank_cell(value):
        return ""
    return value if isinstance(value, str) else _stringify(value)


def _header_texts(row: tuple) -> list[str]:
    """The header's cells up to the last non-empty one."""
    texts = [_header_text(value) for value in row]
    while texts and not texts[-1].strip():
        texts.pop()
    return texts


def _find_columns(headers: list[str]) -> _Columns | None:
    by_name: dict[str, int] = {}
    for position, text in enumerate(headers):
        key = _compact(text)
        if key:
            by_name.setdefault(key, position)
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


def _head_rows(ws, count: int = HEADER_SCAN_ROWS) -> list[tuple]:
    """The first `count` rows of a sheet (fewer if it is shorter), at most
    MAX_REPORT_COLUMNS wide. The stored dimension is ignored: generators of
    odd files get it wrong, and a wrong one must not decide what is read."""
    ws.reset_dimensions()
    return list(
        ws.iter_rows(
            min_row=1, max_row=count, max_col=MAX_REPORT_COLUMNS, values_only=True
        )
    )


@dataclass
class _Table:
    """The data rows of one sheet below its header, as read."""

    headers: list[str]  # one per column read (up to the last non-empty header)
    rows: list[tuple]  # each exactly len(headers) wide, none wholly empty

    def column(self, position: int) -> pd.Series:
        return pd.Series([row[position] for row in self.rows], dtype=object)


def _read_table(ws, header_row: int, headers: list[str]) -> _Table:
    """The rows below `header_row` (1-based), `len(headers)` columns wide. Wholly
    empty rows (padding) are dropped; a sheet running past MAX_REPORT_ROWS is
    refused rather than read."""
    width = len(headers)
    last_row = header_row + MAX_REPORT_ROWS
    ws.reset_dimensions()
    rows: list[tuple] = []
    for number, row in enumerate(
        ws.iter_rows(
            min_row=header_row + 1,
            max_row=last_row + 1,
            max_col=width,
            values_only=True,
        ),
        start=header_row + 1,
    ):
        if number > last_row:
            # The reader only gets this far when the sheet has rows past the cap.
            raise _ReportError(
                f"Sheet {ws.title!r} has more than {MAX_REPORT_ROWS:,} rows - far "
                "more than a report. Did you pick the wrong file?"
            )
        if row.count(None) == width:
            continue  # padding: the reader fills the rows the file doesn't have
        for value in row:
            if not _blank_cell(value):
                rows.append(row)
                break
    return _Table(headers, rows)


@dataclass
class _Located:
    sheet: str
    header_row: int
    headers: list[str]
    columns: _Columns


def _locate_beats(wb) -> _Located:
    """The `beats` sheet if it has the columns, else the first sheet that does.
    Only each sheet's top rows are read, to find its header row."""
    worksheets = list(wb.worksheets)
    if not worksheets:
        raise _ReportError("The workbook has no sheets.")
    ordered = sorted(worksheets, key=lambda ws: _compact(ws.title) != "beats")
    heads: dict[str, list[tuple]] = {}
    for ws in ordered:
        head = heads[ws.title] = _head_rows(ws)
        for number, row in enumerate(head, start=1):
            headers = _header_texts(row)
            columns = _find_columns(headers)
            if columns is not None:
                return _Located(ws.title, number, headers, columns)

    shown_sheet = next(
        (ws for ws in worksheets if _compact(ws.title) == "beats"), worksheets[0]
    )
    first_header = next(
        (h for h in map(_header_texts, heads[shown_sheet.title]) if h), []
    )
    found = [h for h in first_header if h.strip()]
    shown = ", ".join(found[:_MAX_COLUMNS_IN_ERROR])
    if len(found) > _MAX_COLUMNS_IN_ERROR:
        shown += f", ... ({len(found)} columns)"
    raise _ReportError(
        "No sheet has both a timestamp column (ts, time, timestamp or t) and a "
        f"label column ({PRIMARY_LABEL_COLUMN}, label, annotation, ...). "
        f"Columns found in sheet {shown_sheet.title!r}: {shown or '(none)'}"
    )


def _category_columns(
    headers: list[str], columns: _Columns
) -> list[tuple[int, str]]:
    """(column position, category name) for every per-category flag column: each
    annot_<x> other than the label column (-> <x>), except the any_arrhythmia
    roll-up (also when the header is repeated). For this app's own export, its
    category flag columns."""
    found: list[tuple[int, str]] = []
    seen: set[str] = set()
    for position, header in enumerate(headers):
        if position == columns.label:
            continue
        if columns.derived:
            name = header.strip()
            if name not in categories.REASSIGNABLE_CATEGORIES:
                continue
        else:
            match = re.fullmatch(r"annot[\s_\-]+(.+)", header.strip(), re.I)
            if not match:
                continue
            name = re.sub(r"[\s\-]+", "_", match.group(1).strip().lower())
        if name == "any_arrhythmia" or name in seen:
            continue
        seen.add(name)
        found.append((position, name))
    return found


def _label_values(table: _Table, columns: _Columns) -> np.ndarray:
    if not columns.derived:
        return _as_floats(table.column(columns.label))
    # review_state: confirmed -> 2, rejected -> -1, otherwise 1 if the detector
    # flagged the beat (any_arrhythmia) and 0 if not.
    state = table.column(columns.label).map(
        lambda v: "" if _blank_cell(v) else str(v).strip().lower()
    )
    any_col = columns.by_name.get("anyarrhythmia")
    flagged = (
        np.nan_to_num(_as_floats(table.column(any_col), bools=True)) != 0
        if any_col is not None
        else np.zeros(len(table.rows), dtype=bool)
    )
    state_values = state.to_numpy(dtype=object)
    return np.select(
        [state_values == "confirmed", state_values == "rejected", flagged],
        [2.0, -1.0, 1.0],
        default=0.0,
    )


def _read_settings(wb) -> dict[str, object]:
    """First data row of the `settings` sheet, by column (an empty cell is
    None; a repeated header keeps its first column)."""
    for ws in wb.worksheets:
        if _compact(ws.title) != "settings":
            continue
        head = _head_rows(ws, HEADER_SCAN_ROWS + 1)
        for number, row in enumerate(head):
            headers = _header_texts(row)
            if not headers:
                continue
            values = head[number + 1] if number + 1 < len(head) else ()
            if all(_blank_cell(v) for v in values):
                break  # no data row
            settings: dict[str, object] = {}
            for position, header in enumerate(headers):
                name = header.strip()
                if name and name not in settings:
                    value = values[position]
                    settings[name] = None if _blank_cell(value) else _plain(value)
            return settings
    return {}


def _read_bad_data_marks(wb) -> list[ReportBadDataRange]:
    for ws in wb.worksheets:
        if _compact(ws.title) != "baddatamarks":
            continue
        for number, row in enumerate(_head_rows(ws), start=1):
            headers = _header_texts(row)
            by_name: dict[str, int] = {}
            for position, header in enumerate(headers):
                by_name.setdefault(_compact(header), position)
            if "start" not in by_name or "stop" not in by_name:
                continue
            table = _read_table(ws, number, headers)
            start = _as_floats(table.column(by_name["start"]))
            stop = _as_floats(table.column(by_name["stop"]))
            marks = []
            for a, b in zip(start.tolist(), stop.tolist()):
                if math.isfinite(a) and math.isfinite(b):
                    marks.append(ReportBadDataRange(start=min(a, b), stop=max(a, b)))
            return marks
        return []
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


@dataclass
class _Workbook:
    """What a report file yields: its beats sheet and the two small ones."""

    sheet: str
    columns: _Columns
    table: _Table
    settings_raw: dict[str, object]
    bad_data_marks: list[ReportBadDataRange]


def _read_workbook(report_path: str) -> _Workbook:
    io_path = _io_path(report_path)
    if not os.path.exists(io_path):
        raise _ReportError(f"File not found: {report_path}")
    if not os.path.isfile(io_path):
        raise _ReportError(f"Not a file: {report_path}")
    if os.path.splitext(report_path)[1].lower() == ".xls":
        raise _ReportError(
            "Old .xls spreadsheets are not supported. Open it in Excel and Save "
            "it as .xlsx."
        )

    try:
        size = os.path.getsize(io_path)
        if size > MAX_REPORT_BYTES:
            raise _ReportError(
                f"The file is {size / (1024 * 1024):,.1f} MB, far larger than a "
                f"report (limit {MAX_REPORT_BYTES // (1024 * 1024)} MB). Did you "
                "pick the wrong file?"
            )
        # Opened here and handed to openpyxl as a stream: it then doesn't judge
        # the file by its extension (the old reader never did), and the handle
        # is closed below however reading ends.
        with open(io_path, "rb") as f:
            if f.read(len(_OLE2_SIGNATURE)) == _OLE2_SIGNATURE:
                raise _ReportError(
                    "This is an old .xls spreadsheet or a password-protected "
                    "workbook. Save it as an unprotected .xlsx."
                )
            f.seek(0)
            workbook = openpyxl.load_workbook(f, read_only=True, data_only=True)
            try:
                return _read_sheets(workbook)
            finally:
                workbook.close()
    except _ReportError:
        raise
    except PermissionError:
        raise _ReportError(_LOCKED_MESSAGE) from None
    except Exception as e:
        raise _ReportError(
            f"Could not read {os.path.basename(report_path)!r} as an Excel workbook "
            f"({type(e).__name__}: {e}). It may be corrupt, password-protected or "
            "not a spreadsheet."
        ) from None


def _read_sheets(wb) -> _Workbook:
    located = _locate_beats(wb)
    table = _read_table(wb[located.sheet], located.header_row, located.headers)
    return _Workbook(
        sheet=located.sheet,
        columns=located.columns,
        table=table,
        settings_raw=_read_settings(wb),
        bad_data_marks=_read_bad_data_marks(wb),
    )


def _build_entry(report_path: str, recording_path: str, state) -> _Entry:
    parsed = _read_workbook(report_path)
    sheet_name, columns, table = parsed.sheet, parsed.columns, parsed.table

    ts = _as_floats(table.column(columns.ts), times=True)
    label = _label_values(table, columns)
    # Every row of the table has something in it (padding rows are dropped when
    # reading), so a row without a usable ts and label is a skipped one.
    usable = np.isfinite(ts) & np.isin(label, VALID_LABELS)
    skipped_rows = int(np.count_nonzero(~usable))

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

    category_cols = _category_columns(table.headers, columns)
    if category_cols:
        category_flags = np.column_stack(
            [
                np.nan_to_num(_as_floats(table.column(c), bools=True))[rows] != 0
                for c, _ in category_cols
            ]
        )
    else:
        category_flags = np.zeros((rows.size, 0), dtype=bool)

    settings_raw = parsed.settings_raw
    label_header = table.headers[columns.label]
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
    elif _compact(label_header) != _compact(PRIMARY_LABEL_COLUMN):
        warnings_after.append(
            f"No {PRIMARY_LABEL_COLUMN} column; using {label_header!r} for the "
            "labels."
        )

    summary = ReportSummary(
        report_path=report_path,
        filename=filename,
        sheet=sheet_name,
        label_column=label_header,
        category_columns=[table.headers[c] for c, _ in category_cols],
        total_beats=int(sorted_ts.size),
        counts=counts,
        ts_start=float(sorted_ts[0]),
        ts_end=float(sorted_ts[-1]),
        name_matches_recording=name_ok,
        settings={k: _stringify(v) for k, v in settings_raw.items()},
        bad_data_marks=parsed.bad_data_marks,
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


def _our_decisions(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, bool]:
    """(decision per row of the beat frame, whether that decision is known,
    whether the frame has arrhythmia flags at all).

    A confirmed beat is an arrhythmia, a rejected one normal; otherwise a beat
    the detectors flagged is 'flagged' (detected, not reviewed) and any other
    normal. `review_state` is absent until a review action / restore adds it,
    and NaN where a sync had no value: both mean unreviewed.

    The flag columns are absent right after beat detection (arrhythmia
    detection adds them) and all NULL in a frame restored from the database.
    Then "not flagged" means "not evaluated", so an unreviewed beat's decision
    is unknown; only a review (confirm/reject) is a decision without flags."""
    flagged = np.zeros(len(frame), dtype=bool)
    has_flags = False
    for column in categories.ALL_OPTIONAL_COLUMNS:
        if column in frame.columns:
            series = frame[column]
            has_flags = has_flags or bool(series.notna().any())
            flagged |= _truthy(series)
    decisions = np.where(flagged, FLAGGED, NORMAL).astype(np.int8)
    reviewed = np.zeros(len(frame), dtype=bool)
    if "review_state" in frame.columns:
        state = frame["review_state"]
        confirmed = state.eq("confirmed").to_numpy()
        rejected = state.eq("rejected").to_numpy()
        decisions[confirmed] = ARRHYTHMIA
        decisions[rejected] = NORMAL
        reviewed = confirmed | rejected
    return decisions, reviewed | has_flags, has_flags


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

    our_decisions, our_known, has_our_flags = _our_decisions(frame)
    ours = our_decisions[positions[idx]]
    known = our_known[positions[idx]]
    theirs = entry.decisions
    agreement = np.select(
        [
            ~matched_mask,
            ~known,
            (theirs == FLAGGED) | (ours == FLAGGED),
            theirs == ours,
            (theirs == ARRHYTHMIA) & (ours == NORMAL),
            (theirs == NORMAL) & (ours == ARRHYTHMIA),
        ],
        [UNMATCHED, AGREEMENT_UNKNOWN, PENDING, AGREE, REPORT_ONLY, OURS_ONLY],
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
        has_our_flags=has_our_flags,
    )


def _matching(
    entry: _Entry, frame: pd.DataFrame | None, state=None, path: str | None = None
) -> _Matching:
    """The report matched against `frame` (the recording's beat_cache entry,
    read once by the caller).

    Matching 70k beats costs tens of milliseconds and the graph asks on every
    pan/zoom, so the result is memoised against the IDENTITY of the frame it
    was computed for. Frames are replaced, never mutated in place, so `is` is
    exactly "nothing changed". Threads may race to compute it twice (harmless);
    the memo is one attribute assigned in one store, so it can never be seen
    half-updated, and a reader holding a different frame just recomputes.

    A reader that was handed a frame and then lost the race to a re-detection
    would overwrite the newer frame's memo with its own, so a result is stored
    only while `frame` is still the recording's current one (given `state` and
    `path`); otherwise it is just returned.
    """
    if frame is None or "ts" not in frame.columns:
        entry.memo = None  # don't keep a replaced/dropped frame alive
        return entry.unknown
    memo = entry.memo
    if memo is not None and memo[0] is frame:
        return memo[1]
    result = _compute_matching(entry, frame)
    if state is None or state.beat_cache.get(path) is frame:
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


class _Generations:
    """Which load/unload request for a recording is the latest.

    Parsing runs without any lock and takes as long as the file is big, so
    requests for one recording can finish out of order: a slow load requested
    first must not overwrite a faster one requested later, and a report must
    not reappear because an unload came in while it was still being read.
    Every load and every unload takes the recording's next number when it
    starts; a load may publish its report only while its number is still the
    latest, and an unload publishes "nothing" the same way. The check and the
    store happen under one lock, so the two cannot interleave."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, int] = {}

    def start(self, path: str) -> int:
        with self._lock:
            number = self._latest.get(path, 0) + 1
            self._latest[path] = number
            return number

    def publish(self, cache: dict, path: str, number: int, entry: _Entry) -> bool:
        """Store `entry` for `path` if `number` is still the latest request."""
        with self._lock:
            if self._latest.get(path, 0) != number:
                return False
            cache[path] = entry
            return True

    def remove(self, cache: dict, path: str) -> None:
        """Drop the recording's report and supersede any load in flight."""
        with self._lock:
            self._latest[path] = self._latest.get(path, 0) + 1
            cache.pop(path, None)


_generations_init = threading.Lock()


def _generations(state) -> _Generations:
    """The app's _Generations (made on first use: app.state is built before
    this module is, and requests run on a thread pool)."""
    generations = getattr(state, "report_generations", None)
    if generations is None:
        with _generations_init:
            generations = getattr(state, "report_generations", None)
            if generations is None:
                generations = state.report_generations = _Generations()
    return generations


@router.post("/load", response_model=ReportLoadResult)
def load_report(payload: ReportLoadRequest, request: Request) -> ReportLoadResult:
    state = request.app.state
    path = payload.recording_path
    imported = state.imported_files.get(path)
    if imported is None or imported.status != "ok":
        return ReportLoadResult(status="error", error=f"File not imported: {path}")

    generations = _generations(state)
    number = generations.start(path)
    try:
        entry = _build_entry(payload.report_path, path, state)
        summary = _summary_for(entry, state)
    except _ReportError as e:
        return ReportLoadResult(status="error", error=str(e))
    except Exception as e:
        return ReportLoadResult(status="error", error=f"Could not read the report: {e}")

    # A previous report is replaced only by a successfully parsed one, and only
    # if no later load or unload for this recording has started meanwhile.
    # Reported as an error, so a client that was replacing a report re-reads
    # the state (GET /reports/state) and keeps what is really loaded.
    if not generations.publish(state.report_cache, path, number, entry):
        return ReportLoadResult(
            status="error",
            error=(
                "This report was not loaded: a newer load or unload for this "
                "recording was requested while it was being read."
            ),
        )
    # The recording may have been evicted while the report was parsed: stored
    # first, then re-checked, and taken back out if so. (Evict pops
    # imported_files BEFORE report_cache, so either this check sees the
    # eviction, or evict's own pop removes what was just stored.)
    if path not in state.imported_files:
        if state.report_cache.get(path) is entry:
            state.report_cache.pop(path, None)
        return ReportLoadResult(status="error", error=f"File not imported: {path}")
    return ReportLoadResult(status="ok", summary=summary)


@router.post("/unload", response_model=ReportUnloadResult)
def unload_report(
    payload: ReportUnloadRequest, request: Request
) -> ReportUnloadResult:
    state = request.app.state
    _generations(state).remove(state.report_cache, payload.recording_path)
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
        matching = _matching(entry, frame, request.app.state, path)
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
        matching = _matching(entry, frame, request.app.state, path)
    except Exception as e:
        return ReportNavigationResult(status="error", error=_match_error(e))
    return ReportNavigationResult(
        status="ok",
        targets=matching.targets,
        matched=matching.matched,
        total=int(entry.ts.size),
        extra_ours=matching.extra_ours,
        has_our_beats=matching.has_our_beats,
        has_our_flags=matching.has_our_flags,
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
        matching = _matching(entry, frame, request.app.state, path)
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

    recording_stem = os.path.splitext(os.path.basename(recording_path))[0].lower()

    def order(e: os.DirEntry):
        # The report named exactly like the recording first (.xlsx before
        # .xlsm), then the rest ("<stem> (1)", "<stem>-report", ...) by name.
        stem, extension = os.path.splitext(e.name.lower())
        return (
            stem != recording_stem,
            stem,
            REPORT_EXTENSIONS.index(extension),
            e.name,
        )

    paths: list[str] = []
    seen_files: set[str] = set()
    for folder in ordered:
        try:
            with os.scandir(folder) as entries:
                names = sorted(
                    (e for e in entries if _is_candidate(e, recording_path)),
                    key=order,
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
