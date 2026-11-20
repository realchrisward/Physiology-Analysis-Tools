import itertools
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

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
    -- The report this file's review was taken from ("Replace my review with
    -- this report"), and when. Cleared when beats are detected afresh.
    source_report TEXT,
    source_report_at TEXT,
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
    -- The reviewer was unsure. Only ever 1 on a confirmed/rejected beat: the
    -- mark is saved with a decision, it does not replace one.
    uncertain INTEGER NOT NULL DEFAULT 0,
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

-- Display names the technician gave a file's channels. Keyed by path, not by
-- the files row (path, size, mtime), so a name survives the recording being
-- re-saved; `channel` is the extractor's own name, which stays the key
-- everywhere else.
CREATE TABLE IF NOT EXISTS channel_labels (
    path TEXT NOT NULL,
    channel TEXT NOT NULL,
    label TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (path, channel)
);

-- A file's whole saved review, copied aside before a report replaced it, so
-- the replacement can be undone. Keyed like `files` (path, size, mtime), not
-- by files.id: restoring re-creates that row. Each backup expires.
CREATE TABLE IF NOT EXISTS review_backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    replaced_by TEXT,
    channel TEXT,
    beat_settings_json TEXT,
    arrhythmia_settings_json TEXT,
    source_report TEXT,
    source_report_at TEXT,
    file_updated_at TEXT
);

CREATE TABLE IF NOT EXISTS backup_beats (
    backup_id INTEGER NOT NULL REFERENCES review_backups(id),
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
    review_state TEXT NOT NULL,
    reassigned_category TEXT,
    manual_added_at TEXT,
    uncertain INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS backup_bad_data_marks (
    backup_id INTEGER NOT NULL REFERENCES review_backups(id),
    start REAL NOT NULL,
    stop REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS backup_manual_beat_edits (
    backup_id INTEGER NOT NULL REFERENCES review_backups(id),
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    r_amplitude REAL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_backups_path ON review_backups(path);
CREATE INDEX IF NOT EXISTS idx_backup_beats_backup ON backup_beats(backup_id);
"""


def default_db_path() -> str:
    """The app's own per-user data folder (not next to raw recordings)."""
    directory = os.path.join(os.path.expanduser("~"), ".physiology_analysis_tools")
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "state.db")


# Stored in the database file itself (PRAGMA user_version), so a file that is
# deleted and recreated (Settings > reset database) is correctly seen as
# un-initialised again, which a per-path in-process flag would not notice.
SCHEMA_VERSION = 4
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
        # Databases created before manual beat editing / the uncertain mark /
        # replacing a review with a report lack these columns; CREATE TABLE IF
        # NOT EXISTS above will not add them to an existing table.
        for table, column, definition in (
            ("beats", "manual_added_at", "TEXT"),
            ("beats", "uncertain", "INTEGER NOT NULL DEFAULT 0"),
            ("files", "source_report", "TEXT"),
            ("files", "source_report_at", "TEXT"),
        ):
            existing = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            if column in existing:
                continue
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError as e:
                # Another process upgraded the file between the check and here.
                if "duplicate column" not in str(e).lower():
                    raise
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()


def get_channel_labels(
    conn: sqlite3.Connection, paths: list[str]
) -> dict[str, dict[str, str]]:
    """{path: {channel: label}} for the given paths (paths with none are left out)."""
    labels: dict[str, dict[str, str]] = {}
    # Chunked: SQLite caps the number of bound parameters per statement.
    unique = list(dict.fromkeys(paths))
    for start in range(0, len(unique), 500):
        chunk = unique[start : start + 500]
        rows = conn.execute(
            "SELECT path, channel, label FROM channel_labels "
            f"WHERE path IN ({', '.join('?' for _ in chunk)})",
            chunk,
        )
        for row in rows:
            labels.setdefault(row["path"], {})[row["channel"]] = row["label"]
    return labels


def set_channel_label(
    conn: sqlite3.Connection, paths: list[str], channel: str, label: str | None
) -> None:
    """Name `channel` `label` in every one of `paths`, in one transaction;
    `None` goes back to the original name."""
    with conn:
        if label is None:
            conn.executemany(
                "DELETE FROM channel_labels WHERE path=? AND channel=?",
                [(path, channel) for path in paths],
            )
        else:
            updated_at = datetime.now(timezone.utc).isoformat()
            conn.executemany(
                "INSERT INTO channel_labels (path, channel, label, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(path, channel) DO UPDATE SET "
                "label=excluded.label, updated_at=excluded.updated_at",
                [(path, channel, label, updated_at) for path in paths],
            )


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
    source_report=_UNSET,
    source_report_at=_UNSET,
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
        "source_report": source_report,
        "source_report_at": source_report_at,
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

UNCERTAIN_NEEDS_DECISION = (
    "Confirm or reject this beat first — the uncertain mark is saved with a decision."
)


class UncertainNeedsDecisionError(Exception):
    """`set_uncertain` on a beat that is not confirmed or rejected."""


def update_beat_category(
    conn: sqlite3.Connection,
    file_id: int,
    ts: float,
    action: str,
    category: str | None = None,
    uncertain: bool | None = None,
) -> sqlite3.Row | None:
    """Apply a review `action` to the beat at (file_id, ts) and return the
    updated row, or None if no beat exists at that timestamp.

    `uncertain` is the reviewer's "not sure" mark. `"confirm"`, `"reject"` and
    `"reassign"` store it in the same statement as the decision (None keeps
    the beat's current value); `"set_uncertain"` changes only the mark, and is
    refused (UncertainNeedsDecisionError) unless the beat is already confirmed
    or rejected, so the mark never exists on a beat without a decision.

    `"reject"` clears every category column (not just the top-level
    `any_arrhythmia` flag) so a rejected beat's per-category data doesn't
    stay stuck showing as if it were still flagged.

    `"remove_flag"` is narrower than all three: it clears exactly one
    auto-detected category column (`category`, already validated by the
    caller against `REASSIGNABLE_CATEGORIES` before it reaches here — never
    interpolate an unvalidated value into the column-name SQL below) and
    recomputes `any_arrhythmia` from whatever flags remain, WITHOUT
    touching `review_state`/`reassigned_category`/`uncertain` — this corrects the
    detector's raw output, it isn't a confirm/reject/reassign review
    decision the way the other three actions are.
    """
    row = conn.execute(
        "SELECT id FROM beats WHERE file_id=? AND ts=?", (file_id, ts)
    ).fetchone()
    if row is None:
        return None
    beat_id = row["id"]

    # Appended to the three decision updates below so the mark and the decision
    # land in one statement; left out entirely when the caller sent no value,
    # which keeps whatever the beat already has.
    uncertain_set, uncertain_args = (
        ("", ()) if uncertain is None else (", uncertain=?", (int(uncertain),))
    )

    if action == "confirm":
        conn.execute(
            "UPDATE beats SET review_state='confirmed'" + uncertain_set + " WHERE id=?",
            (*uncertain_args, beat_id),
        )
    elif action == "reject":
        conn.execute(
            "UPDATE beats SET "
            + ", ".join(f"{col}=0" for col in CATEGORY_COLUMNS)
            + ", any_arrhythmia=0, review_state='rejected', "
            "reassigned_category=NULL" + uncertain_set + " WHERE id=?",
            (*uncertain_args, beat_id),
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
            "reassigned_category=?" + uncertain_set + " WHERE id=?",
            (category, *uncertain_args, beat_id),
        )
    elif action == "set_uncertain":
        # The state check is part of the UPDATE, not a separate read, so the
        # mark can never land on a beat that has no decision.
        cursor = conn.execute(
            "UPDATE beats SET uncertain=? WHERE id=? "
            "AND review_state IN ('confirmed', 'rejected')",
            (int(bool(uncertain)), beat_id),
        )
        if cursor.rowcount == 0:
            raise UncertainNeedsDecisionError(UNCERTAIN_NEEDS_DECISION)
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

    # Every review action counts as the file's latest saved work (the report's
    # `review_last_saved_at` reads this), not only persists and channel saves.
    conn.execute(
        "UPDATE files SET updated_at=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), file_id),
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
) -> dict[float, tuple[str, str | None, bool]]:
    """`ts -> (review_state, reassigned_category, uncertain)` for every beat of
    the file that has been confirmed/rejected/reassigned. Unreviewed beats are
    absent, so they fall back to the unreviewed default with `uncertain`
    cleared: the mark only means something next to a decision."""
    return {
        row["ts"]: (
            row["review_state"],
            row["reassigned_category"],
            bool(row["uncertain"]),
        )
        for row in conn.execute(
            "SELECT ts, review_state, reassigned_category, uncertain FROM beats "
            "WHERE file_id=? AND review_state != 'unreviewed'",
            (file_id,),
        ).fetchall()
    }


def with_review_state(
    beat_df: pd.DataFrame, review_state: dict[float, tuple[str, str | None, bool]]
) -> pd.DataFrame:
    """A NEW frame with `review_state`/`reassigned_category`/`uncertain` filled
    in from `review_state` by exact `ts` (default unreviewed/None/False), same
    matching rule as `replace_beats`. Never touches `beat_df`: it may be the
    frame other requests are reading from `beat_cache` right now.
    """
    default = ("unreviewed", None, False)
    return beat_df.assign(
        review_state=beat_df["ts"].map(lambda ts: review_state.get(ts, default)[0]),
        reassigned_category=beat_df["ts"].map(
            lambda ts: review_state.get(ts, default)[1]
        ),
        uncertain=beat_df["ts"].map(
            lambda ts: review_state.get(ts, default)[2]
        ).astype(bool),
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
) -> dict[float, tuple[str, str | None, bool]]:
    """Delete-then-reinsert every beat for `file_id` from `beat_df`.

    Wholesale-replacing (rather than merging) keeps repeated persists
    idempotent: calling this twice for the same detection result leaves
    exactly one row per beat, not duplicates.

    Prior review state is preserved across a re-persist: any beat that had
    already been confirmed/rejected/reassigned (i.e. `review_state !=
    'unreviewed'`) keeps its `review_state`/`reassigned_category`/`uncertain`
    if a beat with the same `ts` is still present in `beat_df`. This makes it
    safe to call this again after new detection output arrives (e.g. an
    arrhythmia re-run) without silently discarding a technician's prior review
    work.
    Matching is by exact float equality on `ts`, mirroring the precedent
    already set by `update_beat_category`'s `WHERE ts=?` lookup — `ts`
    always round-trips through the same detection pipeline rather than
    being user-typed, so exact equality is safe here. A beat with no prior
    review, or whose `ts` no longer appears in `beat_df`, still gets the
    default `'unreviewed'`/`NULL`/not uncertain.

    `commit` defaults to True (standalone-call behavior). Pass
    `commit=False` when this call is one half of a larger transaction the
    caller will commit (or roll back) itself.

    Returns the SAME `preserved_state` mapping (`ts -> (review_state,
    reassigned_category, uncertain)`) this function computed to do its own
    preserving — the caller (`persist_beats` in db_routes.py) uses it to sync
    those three columns back onto `beat_cache[path]` (the live, in-memory
    `beat_df` this function was handed), which never gets that write
    otherwise: only `update_beat_category` (a single-beat action) syncs the
    cache today, so without this, a beat reviewed before a redetect+re-persist
    would keep showing as "unreviewed" in the graph even though SQLite
    correctly preserved it.
    """
    preserved_state = load_review_state(conn, file_id)

    conn.execute("DELETE FROM beats WHERE file_id=?", (file_id,))

    default_review = ("unreviewed", None, False)
    review_values = [
        preserved_state.get(ts, default_review) for ts in beat_df["ts"].tolist()
    ]
    write_beats(conn, file_id, beat_df, review_values)

    if commit:
        conn.commit()

    return preserved_state


def write_beats(
    conn: sqlite3.Connection,
    file_id: int,
    beat_df,
    review_values: list[tuple[str, str | None, bool]],
) -> None:
    """Insert every row of `beat_df` as a beat of `file_id`, with
    `review_values[i]` = (review_state, reassigned_category, uncertain) for
    row i. The caller deletes what was there before and commits."""
    # One executemany over precomputed column lists, not an execute per beat:
    # at ~150k beats the per-row version held the write lock for many seconds,
    # which is what made every concurrent request time out.
    ts_values = beat_df["ts"].tolist()
    optional_columns = [_nullable_bools(beat_df, col) for col in OPTIONAL_BEAT_COLUMNS]
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
        "review_state, reassigned_category, manual_added_at, uncertain"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        zip(
            itertools.repeat(file_id),
            ts_values,
            beat_df["RR"].tolist(),
            beat_df["R_amplitude"].tolist(),
            beat_df["HR"].tolist(),
            *optional_columns,
            (state for state, _, _ in review_values),
            (category for _, category, _ in review_values),
            manual_added_at,
            (int(uncertain) for _, _, uncertain in review_values),
        ),
    )


# ---------------------------------------------------------------------------
# Review backups (taken before a report replaces a file's review)
# ---------------------------------------------------------------------------

BACKUP_RETENTION_DAYS = 7
# Backups kept per recording; replacing more often than this drops the oldest.
MAX_BACKUPS_PER_FILE = 5

_BEAT_COPY_COLUMNS = (
    "ts, rr, r_amplitude, hr, bradycardia_absolute, tachycardia_absolute, "
    "skipped_beat, prem_beat, abn_cluster, any_arrhythmia, other_arrhythmia, "
    "review_state, reassigned_category, manual_added_at, uncertain"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _delete_backups(conn: sqlite3.Connection, ids: list[int]) -> None:
    for backup_id in ids:
        for table in (
            "backup_beats",
            "backup_bad_data_marks",
            "backup_manual_beat_edits",
        ):
            conn.execute(f"DELETE FROM {table} WHERE backup_id=?", (backup_id,))
        conn.execute("DELETE FROM review_backups WHERE id=?", (backup_id,))


def purge_expired_backups(conn: sqlite3.Connection) -> int:
    """Drop (and commit dropping) every backup past its expiry. Returns how
    many went. Must not be called inside another transaction."""
    ids = [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM review_backups WHERE expires_at <= ?", (_now_iso(),)
        ).fetchall()
    ]
    if ids:
        _delete_backups(conn, ids)
        conn.commit()
    return len(ids)


def create_backup(
    conn: sqlite3.Connection,
    file_row: sqlite3.Row,
    replaced_by: str | None,
    days: int = BACKUP_RETENTION_DAYS,
) -> sqlite3.Row:
    """Copy the file's whole saved review (its `files` row, beats, bad-data
    marks and manual edits) aside, to expire after `days`, and drop the
    oldest backups beyond MAX_BACKUPS_PER_FILE. Part of the caller's
    transaction: nothing is committed here."""
    created = datetime.now(timezone.utc)
    cursor = conn.execute(
        "INSERT INTO review_backups (path, size, mtime, created_at, expires_at, "
        "replaced_by, channel, beat_settings_json, arrhythmia_settings_json, "
        "source_report, source_report_at, file_updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            file_row["path"],
            file_row["size"],
            file_row["mtime"],
            created.isoformat(),
            (created + timedelta(days=days)).isoformat(),
            replaced_by,
            file_row["channel"],
            file_row["beat_settings_json"],
            file_row["arrhythmia_settings_json"],
            file_row["source_report"],
            file_row["source_report_at"],
            file_row["updated_at"],
        ),
    )
    backup_id = cursor.lastrowid
    file_id = file_row["id"]
    conn.execute(
        f"INSERT INTO backup_beats (backup_id, {_BEAT_COPY_COLUMNS}) "
        f"SELECT ?, {_BEAT_COPY_COLUMNS} FROM beats WHERE file_id=? ORDER BY ts",
        (backup_id, file_id),
    )
    conn.execute(
        "INSERT INTO backup_bad_data_marks (backup_id, start, stop) "
        "SELECT ?, start, stop FROM bad_data_marks WHERE file_id=? ORDER BY id",
        (backup_id, file_id),
    )
    conn.execute(
        "INSERT INTO backup_manual_beat_edits (backup_id, ts, kind, r_amplitude, "
        "created_at) SELECT ?, ts, kind, r_amplitude, created_at "
        "FROM manual_beat_edits WHERE file_id=? ORDER BY id",
        (backup_id, file_id),
    )

    older = [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM review_backups WHERE path=? AND size=? AND mtime=? "
            "ORDER BY id DESC",
            (file_row["path"], file_row["size"], file_row["mtime"]),
        ).fetchall()
    ][MAX_BACKUPS_PER_FILE:]
    _delete_backups(conn, older)
    return conn.execute(
        "SELECT * FROM review_backups WHERE id=?", (backup_id,)
    ).fetchone()


def latest_backup(
    conn: sqlite3.Connection, path: str, size: int, mtime: float
) -> sqlite3.Row | None:
    """The newest unexpired backup of this file's review, if any."""
    return conn.execute(
        "SELECT * FROM review_backups WHERE path=? AND size=? AND mtime=? "
        "AND expires_at > ? ORDER BY id DESC LIMIT 1",
        (path, size, mtime, _now_iso()),
    ).fetchone()


def has_saved_review(conn: sqlite3.Connection, file_id: int) -> bool:
    """Whether the file has any beats or bad-data marks saved."""
    return any(
        conn.execute(f"SELECT 1 FROM {table} WHERE file_id=? LIMIT 1", (file_id,)).fetchone()
        for table in ("beats", "bad_data_marks")
    )


def clear_file_rows(conn: sqlite3.Connection, file_id: int) -> None:
    """Delete the file's beats, manual edits and bad-data marks, keeping its
    `files` row. Part of the caller's transaction."""
    conn.execute("DELETE FROM beats WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM manual_beat_edits WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM bad_data_marks WHERE file_id=?", (file_id,))


RESTORED_FROM_BACKUP = "(restored an earlier review)"


def restore_backup(
    conn: sqlite3.Connection, backup: sqlite3.Row
) -> tuple[int, sqlite3.Row | None]:
    """Put a backup back as the file's saved review and delete that backup,
    in one transaction. The review saved now is not thrown away: it is backed
    up first (when there is any), so restoring is a swap that can itself be
    undone. Returns (the file's id, the backup of the review it replaced).
    Raises LookupError if the backup was deleted (expired and purged) after
    it was read."""
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Read again inside the write transaction: a purge of expired backups
        # committed since `backup` was read would otherwise leave this copying
        # nothing back over the review it is about to clear.
        if (
            conn.execute(
                "SELECT 1 FROM review_backups WHERE id=?", (backup["id"],)
            ).fetchone()
            is None
        ):
            raise LookupError("The backup expired and was removed.")
        current = get_file_row(conn, backup["path"], backup["size"], backup["mtime"])
        kept = None
        if current is not None:
            if has_saved_review(conn, current["id"]):
                kept = create_backup(conn, current, RESTORED_FROM_BACKUP)
            clear_file_rows(conn, current["id"])
        file_id = upsert_file(
            conn,
            backup["path"],
            backup["size"],
            backup["mtime"],
            channel=backup["channel"],
            beat_settings_json=backup["beat_settings_json"],
            arrhythmia_settings_json=backup["arrhythmia_settings_json"],
            source_report=backup["source_report"],
            source_report_at=backup["source_report_at"],
            commit=False,
        )
        conn.execute(
            f"INSERT INTO beats (file_id, {_BEAT_COPY_COLUMNS}) "
            f"SELECT ?, {_BEAT_COPY_COLUMNS} FROM backup_beats WHERE backup_id=? "
            "ORDER BY ts",
            (file_id, backup["id"]),
        )
        conn.execute(
            "INSERT INTO bad_data_marks (file_id, start, stop) "
            "SELECT ?, start, stop FROM backup_bad_data_marks WHERE backup_id=? "
            "ORDER BY rowid",
            (file_id, backup["id"]),
        )
        conn.execute(
            "INSERT INTO manual_beat_edits (file_id, ts, kind, r_amplitude, "
            "created_at) SELECT ?, ts, kind, r_amplitude, created_at "
            "FROM backup_manual_beat_edits WHERE backup_id=? ORDER BY rowid",
            (file_id, backup["id"]),
        )
        if backup["file_updated_at"]:
            conn.execute(
                "UPDATE files SET updated_at=? WHERE id=?",
                (backup["file_updated_at"], file_id),
            )
        _delete_backups(conn, [backup["id"]])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return file_id, kept
