import os
import sqlite3
from datetime import datetime, timezone

import pandas as pd

_UNSET = object()

OPTIONAL_BEAT_COLUMNS = [
    "bradycardia_absolute",
    "tachycardia_absolute",
    "skipped_beat",
    "prem_beat",
    "abn_cluster",
    "any_arrhythmia",
    "other_arrhythmia",
]

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
) -> int:
    """Insert or update the `files` row for (path, size, mtime).

    Only columns explicitly passed a value (not the `_UNSET` sentinel) are
    overwritten on conflict, so e.g. persisting a channel choice doesn't
    blow away beat/arrhythmia settings JSON a prior persist already saved,
    and vice versa.
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
    conn.commit()

    # sqlite's last_insert_rowid() isn't reliably updated when an upsert
    # takes the ON CONFLICT DO UPDATE branch, so look the row back up by its
    # natural key rather than trusting cursor.lastrowid.
    row = get_file_row(conn, path, size, mtime)
    return row["id"]


def replace_beats(conn: sqlite3.Connection, file_id: int, beat_df) -> None:
    """Delete-then-reinsert every beat for `file_id` from `beat_df`.

    Wholesale-replacing (rather than merging) keeps repeated persists
    idempotent: calling this twice for the same detection result leaves
    exactly one row per beat, not duplicates.
    """
    conn.execute("DELETE FROM beats WHERE file_id=?", (file_id,))

    for row in beat_df.itertuples():
        optional_values = []
        for col in OPTIONAL_BEAT_COLUMNS:
            if col in beat_df.columns:
                value = getattr(row, col)
                optional_values.append(None if pd.isna(value) else bool(value))
            else:
                optional_values.append(None)

        conn.execute(
            "INSERT INTO beats ("
            "file_id, ts, rr, r_amplitude, hr, "
            "bradycardia_absolute, tachycardia_absolute, skipped_beat, "
            "prem_beat, abn_cluster, any_arrhythmia, other_arrhythmia, "
            "review_state, reassigned_category"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unreviewed', NULL)",
            (file_id, row.ts, row.RR, row.R_amplitude, row.HR, *optional_values),
        )

    conn.commit()
