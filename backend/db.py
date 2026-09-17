import os
import sqlite3
from datetime import datetime, timezone

import pandas as pd

from backend import categories

_UNSET = object()

OPTIONAL_BEAT_COLUMNS = categories.ALL_OPTIONAL_COLUMNS

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    channel TEXT,
    beat_settings_json TEXT,
    arrhythmia_settings_json TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(path, size, mtime)
);

CREATE TABLE IF NOT EXISTS beats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id),
    ts REAL NOT NULL,
    rr REAL NOT NULL,
    r_amplitude REAL NOT NULL,
    hr REAL NOT NULL,
    bradycardia_absolute INTEGER,
    tachycardia_absolute INTEGER,
    skipped_beat INTEGER,
    prem_beat INTEGER,
    abn_cluster INTEGER,
    any_arrhythmia INTEGER,
    other_arrhythmia INTEGER,
    review_state TEXT NOT NULL DEFAULT 'unreviewed',
    reassigned_category TEXT,
    UNIQUE(file_id, ts)
);

CREATE TABLE IF NOT EXISTS bad_data_marks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id),
    start REAL NOT NULL,
    stop REAL NOT NULL
);
"""


def default_db_path() -> str:
    """The app's own per-user data folder (not next to raw recordings)."""
    directory = os.path.join(os.path.expanduser("~"), ".physiology_analysis_tools")
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "state.db")


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def get_file_row(
    conn: sqlite3.Connection, path: str, size: int, mtime: float
) -> sqlite3.Row | None:
    cursor = conn.execute(
        "SELECT * FROM files WHERE path=? AND size=? AND mtime=?",
        (path, size, mtime),
    )
    return cursor.fetchone()


def upsert_file(
    conn: sqlite3.Connection,
    path: str,
    size: int,
    mtime: float,
    *,
    channel=_UNSET,
    beat_settings_json=_UNSET,
    arrhythmia_settings_json=_UNSET,
    commit: bool = True,
) -> int:
    """Insert or update the `files` row for (path, size, mtime).

    Only columns explicitly passed a value (not the `_UNSET` sentinel) are
    overwritten on conflict, so e.g. persisting a channel choice doesn't
    blow away beat/arrhythmia settings JSON a prior persist already saved,
    and vice versa.

    `commit` defaults to True (standalone-call behavior). Pass
    `commit=False` when this call is one half of a larger transaction the
    caller will commit (or roll back) itself.
    """
    updated_at = datetime.now(timezone.utc).isoformat()

    columns = ["path", "size", "mtime", "updated_at"]
    values = [path, size, mtime, updated_at]
    update_clauses = ["updated_at=excluded.updated_at"]

    optional = {
        "channel": channel,
        "beat_settings_json": beat_settings_json,
        "arrhythmia_settings_json": arrhythmia_settings_json,
    }
    for column, value in optional.items():
        if value is not _UNSET:
            columns.append(column)
            values.append(value)
            update_clauses.append(f"{column}=excluded.{column}")

    conn.execute(
        f"INSERT INTO files ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)}) "
        f"ON CONFLICT(path, size, mtime) DO UPDATE SET {', '.join(update_clauses)}",
        values,
    )
    if commit:
        conn.commit()

    # sqlite's last_insert_rowid() isn't reliably updated when an upsert
    # takes the ON CONFLICT DO UPDATE branch, so look the row back up by its
    # natural key rather than trusting cursor.lastrowid.
    row = get_file_row(conn, path, size, mtime)
    return row["id"]


CATEGORY_COLUMNS = categories.REASSIGNABLE_CATEGORIES


def update_beat_category(
    conn: sqlite3.Connection,
    file_id: int,
    ts: float,
    action: str,
    category: str | None = None,
) -> sqlite3.Row | None:
    """Apply a review `action` to the beat at (file_id, ts) and return the
    updated row, or None if no beat exists at that timestamp.

    `"reject"` clears every category column (not just the top-level
    `any_arrhythmia` flag) so a rejected beat's per-category data doesn't
    stay stuck showing as if it were still flagged.

    `"remove_flag"` is narrower than all three: it clears exactly one
    auto-detected category column (`category`, already validated by the
    caller against `REASSIGNABLE_CATEGORIES` before it reaches here — never
    interpolate an unvalidated value into the column-name SQL below) and
    recomputes `any_arrhythmia` from whatever flags remain, WITHOUT
    touching `review_state`/`reassigned_category` — this corrects the
    detector's raw output, it isn't a confirm/reject/reassign review
    decision the way the other three actions are.
    """
    row = conn.execute(
        "SELECT id FROM beats WHERE file_id=? AND ts=?", (file_id, ts)
    ).fetchone()
    if row is None:
        return None
    beat_id = row["id"]

    if action == "confirm":
        conn.execute(
            "UPDATE beats SET review_state='confirmed' WHERE id=?", (beat_id,)
        )
    elif action == "reject":
        conn.execute(
            "UPDATE beats SET "
            + ", ".join(f"{col}=0" for col in CATEGORY_COLUMNS)
            + ", any_arrhythmia=0, review_state='rejected', "
            "reassigned_category=NULL WHERE id=?",
            (beat_id,),
        )
    elif action == "reassign":
        set_clauses = [
            f"{col}=1" if col == category else f"{col}=0"
            for col in CATEGORY_COLUMNS
        ]
        conn.execute(
            "UPDATE beats SET "
            + ", ".join(set_clauses)
            + ", any_arrhythmia=1, review_state='confirmed', "
            "reassigned_category=? WHERE id=?",
            (category, beat_id),
        )
    elif action == "remove_flag":
        conn.execute(f"UPDATE beats SET {category}=0 WHERE id=?", (beat_id,))
        remaining = conn.execute(
            "SELECT " + ", ".join(CATEGORY_COLUMNS) + " FROM beats WHERE id=?",
            (beat_id,),
        ).fetchone()
        any_flagged = any(remaining[col] for col in CATEGORY_COLUMNS)
        conn.execute(
            "UPDATE beats SET any_arrhythmia=? WHERE id=?",
            (1 if any_flagged else 0, beat_id),
        )

    conn.commit()
    return conn.execute("SELECT * FROM beats WHERE id=?", (beat_id,)).fetchone()


def add_bad_data_mark(
    conn: sqlite3.Connection, file_id: int, start: float, stop: float
) -> sqlite3.Row:
    """Insert a bad-data mark, auto-sorting so `start <= stop` regardless of
    which direction the technician drag-selected."""
    lo, hi = min(start, stop), max(start, stop)
    cursor = conn.execute(
        "INSERT INTO bad_data_marks (file_id, start, stop) VALUES (?, ?, ?)",
        (file_id, lo, hi),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM bad_data_marks WHERE id=?", (cursor.lastrowid,)
    ).fetchone()


def delete_bad_data_mark(conn: sqlite3.Connection, file_id: int, mark_id: int) -> bool:
    """Delete the bad-data mark with `mark_id`, scoped to `file_id` so one
    file's DELETE can never remove another file's mark."""
    cursor = conn.execute(
        "DELETE FROM bad_data_marks WHERE id=? AND file_id=?", (mark_id, file_id)
    )
    conn.commit()
    return cursor.rowcount > 0


def replace_beats(
    conn: sqlite3.Connection, file_id: int, beat_df, *, commit: bool = True
) -> dict[float, tuple[str, str | None]]:
    """Delete-then-reinsert every beat for `file_id` from `beat_df`.

    Wholesale-replacing (rather than merging) keeps repeated persists
    idempotent: calling this twice for the same detection result leaves
    exactly one row per beat, not duplicates.

    Prior review state is preserved across a re-persist: any beat that had
    already been confirmed/rejected/reassigned (i.e. `review_state !=
    'unreviewed'`) keeps its `review_state`/`reassigned_category` if a beat
    with the same `ts` is still present in `beat_df`. This makes it safe to
    call this again after new detection output arrives (e.g. an arrhythmia
    re-run) without silently discarding a technician's prior review work.
    Matching is by exact float equality on `ts`, mirroring the precedent
    already set by `update_beat_category`'s `WHERE ts=?` lookup — `ts`
    always round-trips through the same detection pipeline rather than
    being user-typed, so exact equality is safe here. A beat with no prior
    review, or whose `ts` no longer appears in `beat_df`, still gets the
    default `'unreviewed'`/`NULL`.

    `commit` defaults to True (standalone-call behavior). Pass
    `commit=False` when this call is one half of a larger transaction the
    caller will commit (or roll back) itself.

    Returns the SAME `preserved_state` mapping (`ts -> (review_state,
    reassigned_category)`) this function computed to do its own preserving
    — the caller (`persist_beats` in db_routes.py) uses it to sync
    `review_state`/`reassigned_category` back onto `beat_cache[path]` (the
    live, in-memory `beat_df` this function was handed), which never gets
    that write otherwise: only `update_beat_category` (a single-beat
    action) syncs the cache today, so without this, a beat reviewed before
    a redetect+re-persist would keep showing as "unreviewed" in the graph
    even though SQLite correctly preserved it.
    """
    preserved_state: dict[float, tuple[str, str | None]] = {
        row["ts"]: (row["review_state"], row["reassigned_category"])
        for row in conn.execute(
            "SELECT ts, review_state, reassigned_category FROM beats "
            "WHERE file_id=? AND review_state != 'unreviewed'",
            (file_id,),
        ).fetchall()
    }

    conn.execute("DELETE FROM beats WHERE file_id=?", (file_id,))

    for row in beat_df.itertuples():
        optional_values = []
        for col in OPTIONAL_BEAT_COLUMNS:
            if col in beat_df.columns:
                value = getattr(row, col)
                optional_values.append(None if pd.isna(value) else bool(value))
            else:
                optional_values.append(None)

        review_state, reassigned_category = preserved_state.get(
            row.ts, ("unreviewed", None)
        )

        conn.execute(
            "INSERT INTO beats ("
            "file_id, ts, rr, r_amplitude, hr, "
            "bradycardia_absolute, tachycardia_absolute, skipped_beat, "
            "prem_beat, abn_cluster, other_arrhythmia, any_arrhythmia, "
            "review_state, reassigned_category"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                file_id,
                row.ts,
                row.RR,
                row.R_amplitude,
                row.HR,
                *optional_values,
                review_state,
                reassigned_category,
            ),
        )

    if commit:
        conn.commit()

    return preserved_state
