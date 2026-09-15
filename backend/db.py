import os
import sqlite3

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
