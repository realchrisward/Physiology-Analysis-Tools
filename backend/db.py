import itertools
import os
import sqlite3
import threading
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
    manual_added_at TEXT,
    UNIQUE(file_id, ts)
);

-- Beats the technician added or removed by hand. Kept apart from `beats`
-- (which a re-detect rewrites wholesale) so they can be re-applied to the
-- fresh detection result instead of being lost.
CREATE TABLE IF NOT EXISTS manual_beat_edits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id),
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    r_amplitude REAL,
    created_at TEXT NOT NULL
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


# Stored in the database file itself (PRAGMA user_version), so a file that is
# deleted and recreated (Settings > reset database) is correctly seen as
# un-initialised again, which a per-path in-process flag would not notice.
SCHEMA_VERSION = 1
_schema_lock = threading.Lock()

# How long a connection waits on another writer before "database is locked".
# The default (5 s) is shorter than a large persist or a burst of them.
BUSY_TIMEOUT_SECONDS = 30


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_SECONDS)
    try:
        conn.row_factory = sqlite3.Row
        # WAL lets readers and the one writer run side by side instead of
        # every write locking everyone out. It is stored in the database file,
        # so this is a no-op after the first connection to a file.
        journal_mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(journal_mode).lower() == "wal":
            # Safe (no corruption) in WAL mode; only the last commits can be
            # lost on power failure. Not worth the fsync per commit here.
            conn.execute("PRAGMA synchronous=NORMAL")
        if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
            _init_schema(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create/upgrade the tables. Runs once per database file, not per request."""
    with _schema_lock:
        # Another thread may have finished while this one waited for the lock.
        if conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
            return
        conn.executescript(SCHEMA_SQL)
        # Databases created before manual beat editing lack this column;
        # CREATE TABLE IF NOT EXISTS above will not add it to an existing table.
        beat_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(beats)")
        }
        if "manual_added_at" not in beat_columns:
            try:
                conn.execute("ALTER TABLE beats ADD COLUMN manual_added_at TEXT")
            except sqlite3.OperationalError as e:
                # Another process upgraded the file between the check and here.
                if "duplicate column" not in str(e).lower():
                    raise
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()


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


def update_bad_data_mark(
    conn: sqlite3.Connection, file_id: int, mark_id: int, start: float, stop: float
) -> sqlite3.Row | None:
    """Move an existing mark to a new range (auto-sorted like an add),
    scoped to `file_id`. Returns the updated row, or None if no such mark."""
    lo, hi = min(start, stop), max(start, stop)
    cursor = conn.execute(
        "UPDATE bad_data_marks SET start=?, stop=? WHERE id=? AND file_id=?",
        (lo, hi, mark_id, file_id),
    )
    conn.commit()
    if cursor.rowcount == 0:
        return None
    return conn.execute(
        "SELECT * FROM bad_data_marks WHERE id=?", (mark_id,)
    ).fetchone()


def insert_beat(
    conn: sqlite3.Connection,
    file_id: int,
    ts: float,
    rr: float,
    r_amplitude: float,
    hr: float,
    manual_added_at: str | None = None,
) -> None:
    """Insert one manually added beat: unreviewed, with no arrhythmia flags
    (arrhythmia detection has not seen it). Raises sqlite3.IntegrityError if
    a beat already exists at exactly this `ts`."""
    conn.execute(
        "INSERT INTO beats (file_id, ts, rr, r_amplitude, hr, review_state, "
        "manual_added_at) VALUES (?, ?, ?, ?, ?, 'unreviewed', ?)",
        (file_id, ts, rr, r_amplitude, hr, manual_added_at),
    )
    conn.commit()


def record_manual_add(
    conn: sqlite3.Connection, file_id: int, ts: float, r_amplitude: float, tolerance: float
) -> str:
    """Remember a hand-added beat so a re-detect can restore it. Returns the
    ISO (UTC) time it was recorded. Adding a beat where one had been manually
    removed cancels that removal."""
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "DELETE FROM manual_beat_edits WHERE file_id=? AND kind='delete' "
        "AND ABS(ts - ?) < ?",
        (file_id, ts, tolerance),
    )
    conn.execute(
        "INSERT INTO manual_beat_edits (file_id, ts, kind, r_amplitude, created_at) "
        "VALUES (?, ?, 'add', ?, ?)",
        (file_id, ts, r_amplitude, created_at),
    )
    conn.commit()
    return created_at


def record_manual_delete(conn: sqlite3.Connection, file_id: int, ts: float) -> None:
    """Remember a hand-removed beat so a re-detect does not bring it back.
    Removing a beat that was itself hand-added just forgets that addition."""
    cursor = conn.execute(
        "DELETE FROM manual_beat_edits WHERE file_id=? AND kind='add' AND ts=?",
        (file_id, ts),
    )
    if cursor.rowcount == 0:
        conn.execute(
            "INSERT INTO manual_beat_edits (file_id, ts, kind, created_at) "
            "VALUES (?, ?, 'delete', ?)",
            (file_id, ts, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
    conn.commit()


def list_manual_edits(conn: sqlite3.Connection, file_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM manual_beat_edits WHERE file_id=? ORDER BY id", (file_id,)
    ).fetchall()


def update_beat_rr_hr(
    conn: sqlite3.Connection, file_id: int, ts: float, rr: float, hr: float
) -> None:
    """Rewrite one beat's RR/HR after a neighbour was added or removed."""
    conn.execute(
        "UPDATE beats SET rr=?, hr=? WHERE file_id=? AND ts=?", (rr, hr, file_id, ts)
    )
    conn.commit()


def delete_beat(conn: sqlite3.Connection, file_id: int, ts: float) -> bool:
    """Delete one beat outright — for a detection false positive (a noise
    spike picked up as a beat).

    Distinct from rejecting a beat: "reject" in `update_beat_category` means
    "this beat is real, but it is not an arrhythmia" and keeps the row,
    whereas this means "this is not a beat at all", so it stops contributing
    to beat counts, rate statistics, and the exported report.
    """
    cursor = conn.execute("DELETE FROM beats WHERE file_id=? AND ts=?", (file_id, ts))
    conn.commit()
    return cursor.rowcount > 0


def delete_file_state(conn: sqlite3.Connection, file_id: int) -> None:
    """Discard everything persisted for a file — every beat, every bad-data
    mark, and the file row itself (channel choice and settings snapshots).

    Backs the "start fresh" action: detection then starts from a clean slate
    rather than inheriting review decisions the technician has explicitly
    chosen to abandon.
    """
    conn.execute("DELETE FROM beats WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM manual_beat_edits WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM bad_data_marks WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM files WHERE id=?", (file_id,))
    conn.commit()


def delete_bad_data_mark(conn: sqlite3.Connection, file_id: int, mark_id: int) -> bool:
    """Delete the bad-data mark with `mark_id`, scoped to `file_id` so one
    file's DELETE can never remove another file's mark."""
    cursor = conn.execute(
        "DELETE FROM bad_data_marks WHERE id=? AND file_id=?", (mark_id, file_id)
    )
    conn.commit()
    return cursor.rowcount > 0


def load_review_state(
    conn: sqlite3.Connection, file_id: int
) -> dict[float, tuple[str, str | None]]:
    """`ts -> (review_state, reassigned_category)` for every beat of the file
    that has been confirmed/rejected/reassigned. Unreviewed beats are absent."""
    return {
        row["ts"]: (row["review_state"], row["reassigned_category"])
        for row in conn.execute(
            "SELECT ts, review_state, reassigned_category FROM beats "
            "WHERE file_id=? AND review_state != 'unreviewed'",
            (file_id,),
        ).fetchall()
    }


def with_review_state(
    beat_df: pd.DataFrame, review_state: dict[float, tuple[str, str | None]]
) -> pd.DataFrame:
    """A NEW frame with `review_state`/`reassigned_category` filled in from
    `review_state` by exact `ts` (default unreviewed/None), same matching rule
    as `replace_beats`. Never touches `beat_df`: it may be the frame other
    requests are reading from `beat_cache` right now.
    """
    default = ("unreviewed", None)
    return beat_df.assign(
        review_state=beat_df["ts"].map(lambda ts: review_state.get(ts, default)[0]),
        reassigned_category=beat_df["ts"].map(
            lambda ts: review_state.get(ts, default)[1]
        ),
    )


def _nullable_bools(beat_df: pd.DataFrame, column: str) -> list:
    """`column` as a list of bool/None (NaN -> None), or all None when the
    frame has no such column."""
    if column not in beat_df.columns:
        return [None] * len(beat_df)
    series = beat_df[column]
    missing = series.isna().to_numpy()
    return [None if m else bool(v) for v, m in zip(series.tolist(), missing)]


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
    preserved_state = load_review_state(conn, file_id)

    conn.execute("DELETE FROM beats WHERE file_id=?", (file_id,))

    # One executemany over precomputed column lists, not an execute per beat:
    # at ~150k beats the per-row version held the write lock for many seconds,
    # which is what made every concurrent request time out.
    ts_values = beat_df["ts"].tolist()
    optional_columns = [_nullable_bools(beat_df, col) for col in OPTIONAL_BEAT_COLUMNS]
    default_review = ("unreviewed", None)
    review_values = [preserved_state.get(ts, default_review) for ts in ts_values]
    if "manual_added_at" in beat_df.columns:
        added = beat_df["manual_added_at"]
        added_missing = added.isna().to_numpy()
        manual_added_at = [
            None if m else str(v) for v, m in zip(added.tolist(), added_missing)
        ]
    else:
        manual_added_at = [None] * len(ts_values)

    conn.executemany(
        "INSERT INTO beats ("
        "file_id, ts, rr, r_amplitude, hr, "
        "bradycardia_absolute, tachycardia_absolute, skipped_beat, "
        "prem_beat, abn_cluster, other_arrhythmia, any_arrhythmia, "
        "review_state, reassigned_category, manual_added_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        zip(
            itertools.repeat(file_id),
            ts_values,
            beat_df["RR"].tolist(),
            beat_df["R_amplitude"].tolist(),
            beat_df["HR"].tolist(),
            *optional_columns,
            (state for state, _ in review_values),
            (category for _, category in review_values),
            manual_added_at,
        ),
    )

    if commit:
        conn.commit()

    return preserved_state
