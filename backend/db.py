import bisect
import itertools
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

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


def previous_version_row(
    conn: sqlite3.Connection, path: str, size: int, mtime: float
) -> sqlite3.Row | None:
    """The newest saved review of `path` made while the file had another size
    or modification time (it was saved again on disk since), if there is one
    with any beats or bad-data marks."""
    for row in conn.execute(
        "SELECT * FROM files WHERE path=? AND NOT (size=? AND mtime=?) "
        "ORDER BY updated_at DESC",
        (path, size, mtime),
    ).fetchall():
        if has_saved_review(conn, row["id"]):
            return row
    return None


def adopt_previous_version(
    conn: sqlite3.Connection, row: sqlite3.Row, size: int, mtime: float
) -> sqlite3.Row | None:
    """Make the saved review in `row` (an earlier version of the file) the
    review of the file as it is now (`size`, `mtime`), with its backups.

    A review the file as it is now already has (the one started since it was
    opened) makes way: it is backed up first when there is anything in it
    (`BACKUP_REASON_EARLIER`), then removed. Returns that backup, if any."""
    try:
        conn.execute("BEGIN IMMEDIATE")
        backup = None
        current = get_file_row(conn, row["path"], size, mtime)
        if current is not None:
            # Only review work is worth a backup, not beats merely detected.
            worked = conn.execute(
                "SELECT EXISTS(SELECT 1 FROM beats WHERE file_id=:id AND review_state != 'unreviewed') "
                "OR EXISTS(SELECT 1 FROM bad_data_marks WHERE file_id=:id) "
                "OR EXISTS(SELECT 1 FROM manual_beat_edits WHERE file_id=:id)",
                {"id": current["id"]},
            ).fetchone()[0]
            if worked:
                backup = create_backup(conn, current, BACKUP_REASON_EARLIER)
            clear_file_rows(conn, current["id"])
            conn.execute("DELETE FROM files WHERE id=?", (current["id"],))
        conn.execute("UPDATE files SET size=?, mtime=? WHERE id=?", (size, mtime, row["id"]))
        conn.execute(
            "UPDATE review_backups SET size=?, mtime=? WHERE path=? AND size=? AND mtime=?",
            (size, mtime, row["path"], row["size"], row["mtime"]),
        )
        conn.commit()
        return backup
    except BaseException:
        conn.rollback()
        raise


def latest_file_row(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    """The most recently saved review of `path`, whichever version of the file
    it belongs to (for a file that is no longer on disk)."""
    return conn.execute(
        "SELECT * FROM files WHERE path=? ORDER BY updated_at DESC LIMIT 1", (path,)
    ).fetchone()


def review_counts(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row:
    """Counts over a file's saved beats: beats, mean HR, arrhythmia beats
    (flagged and not rejected, or confirmed), flagged beats nobody has looked
    at yet (to review), reviewed flagged beats (a decision, or looked at), and
    uncertain beats, plus bad-data marks."""
    return conn.execute(
        "SELECT COUNT(*) AS beats, AVG(hr) AS mean_hr, "
        "COALESCE(SUM((any_arrhythmia = 1 AND review_state != 'rejected') "
        "OR review_state = 'confirmed'), 0) AS arrhythmia_beats, "
        "COALESCE(SUM(any_arrhythmia = 1 AND review_state = 'unreviewed'), 0) AS to_review, "
        "COALESCE(SUM(review_state IN ('confirmed', 'rejected') "
        "OR (review_state = 'reviewed' AND any_arrhythmia = 1)), 0) AS reviewed, "
        "COALESCE(SUM(uncertain = 1), 0) AS uncertain, "
        "(SELECT COUNT(*) FROM bad_data_marks WHERE file_id = :id) AS bad_data_marks "
        "FROM beats WHERE file_id = :id",
        {"id": file_id},
    ).fetchone()


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
    "Choose Arrhythmia or Not arrhythmia for this beat first — the uncertain mark is saved with that decision."
)


UNCERTAIN_KEEPS_DECISION = (
    "Untick Unsure first — an unsure beat keeps its Arrhythmia or Not arrhythmia decision."
)


ARRHYTHMIA_NEEDS_CATEGORY = (
    "An arrhythmia beat needs at least one category — tick another one before removing this one."
)

NOT_ARRHYTHMIA_HAS_NO_CATEGORIES = (
    "A beat marked Not arrhythmia has no categories — mark it Arrhythmia to choose them."
)


class ReviewRuleError(Exception):
    """A review action the beat's current review does not allow (the message
    says why, for the reviewer)."""


class UncertainNeedsDecisionError(ReviewRuleError):
    """`set_uncertain` on a beat that is not confirmed or rejected, or taking
    the decision off a beat that is marked uncertain."""


def parse_categories(text) -> list[str]:
    """The categories in a `reassigned_category` value: one name, or several
    separated by commas (or semicolons), in the usual category order. Unknown
    names and blanks are left out."""
    if not isinstance(text, str) or not text.strip():
        return []
    names = {part.strip() for part in text.replace(";", ",").split(",")}
    return [name for name in CATEGORY_COLUMNS if name in names]


def join_categories(names) -> str | None:
    """The `reassigned_category` value for `names` (None for none)."""
    chosen = [name for name in CATEGORY_COLUMNS if name in set(names or [])]
    return ",".join(chosen) if chosen else None


def _set_categories_sql(chosen: list[str]) -> str:
    return ", ".join(f"{col}={1 if col in chosen else 0}" for col in CATEGORY_COLUMNS)


def update_beat_category(
    conn: sqlite3.Connection,
    file_id: int,
    ts: float,
    action: str,
    category: str | None = None,
    uncertain: bool | None = None,
    categories: list[str] | None = None,
) -> sqlite3.Row | None:
    """Apply a review `action` to the beat at (file_id, ts) and return the
    updated row, or None if no beat exists at that timestamp.

    Categories: an arrhythmia (confirmed) beat always has at least one.
    `"set_categories"` sets the beat's categories to exactly `categories`
    (validated by the caller) and records them as the reviewer's choice in
    `reassigned_category` (comma-separated), so a re-run of detection keeps
    them. It is refused (ReviewRuleError) with none on a confirmed beat, and
    on a rejected beat at all; on an unreviewed beat it also marks it
    reviewed (it was looked at). `"confirm"` may carry `categories` to set in
    the same step; without them, a beat that has no category gets
    'other_arrhythmia'. `"remove_flag"` is refused for a confirmed beat's last
    category.

    `uncertain` is the reviewer's "not sure" mark. `"confirm"`, `"reject"` and
    `"reassign"` store it in the same statement as the decision (None keeps
    the beat's current value); `"set_uncertain"` changes only the mark, and is
    refused (UncertainNeedsDecisionError) unless the beat is already confirmed
    or rejected, so the mark never exists on a beat without a decision.

    `"reject"` leaves the category flags as they are (the detector's), as the
    original desktop app did: the decision lives in `review_state`, and the
    report still shows what was detected next to label -1. Everything that
    shows or navigates flagged beats treats a rejected beat as normal.

    Review states: 'unreviewed' (not looked at), 'reviewed' (looked at, no
    decision), 'confirmed' (Arrhythmia) and 'rejected' (Not arrhythmia).
    `"seen"` marks an unreviewed beat reviewed (selecting it does this) and
    leaves any other state alone. `"clear_decision"` takes the decision off a
    confirmed/rejected beat, leaving it reviewed; `"unreview"` puts any beat
    back to unreviewed (dropping the reviewer's categories as a saved choice;
    the flags stay). Both are refused
    (UncertainNeedsDecisionError) while the beat is marked uncertain: that
    mark only exists next to a decision.

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
        chosen = parse_categories(join_categories(categories))
        if chosen:
            conn.execute(
                "UPDATE beats SET "
                + _set_categories_sql(chosen)
                + ", any_arrhythmia=1, reassigned_category=?, review_state='confirmed'"
                + uncertain_set
                + " WHERE id=?",
                (join_categories(chosen), *uncertain_args, beat_id),
            )
        else:
            current = conn.execute(
                "SELECT " + ", ".join(CATEGORY_COLUMNS) + " FROM beats WHERE id=?",
                (beat_id,),
            ).fetchone()
            # An arrhythmia always has a category: 'other' when none is known,
            # (with_reviewer_categories gives it back if a re-run drops it).
            fallback = (
                ", other_arrhythmia=1, any_arrhythmia=1"
                if not any(current[col] for col in CATEGORY_COLUMNS)
                else ""
            )
            conn.execute(
                "UPDATE beats SET review_state='confirmed'"
                + fallback
                + uncertain_set
                + " WHERE id=?",
                (*uncertain_args, beat_id),
            )
    elif action == "reject":
        # The categories (the detector's, or the reviewer's) stay with the beat.
        conn.execute(
            "UPDATE beats SET review_state='rejected'" + uncertain_set + " WHERE id=?",
            (*uncertain_args, beat_id),
        )
    elif action == "set_categories":
        current = conn.execute(
            "SELECT review_state FROM beats WHERE id=?", (beat_id,)
        ).fetchone()
        chosen = parse_categories(join_categories(categories))
        if current["review_state"] == "rejected":
            raise ReviewRuleError(NOT_ARRHYTHMIA_HAS_NO_CATEGORIES)
        if current["review_state"] == "confirmed" and not chosen:
            raise ReviewRuleError(ARRHYTHMIA_NEEDS_CATEGORY)
        conn.execute(
            "UPDATE beats SET "
            + _set_categories_sql(chosen)
            + ", any_arrhythmia=?, reassigned_category=?, review_state=CASE "
            "WHEN review_state='unreviewed' THEN 'reviewed' ELSE review_state END "
            "WHERE id=?",
            (1 if chosen else 0, join_categories(chosen), beat_id),
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
    elif action == "seen":
        conn.execute(
            "UPDATE beats SET review_state='reviewed' "
            "WHERE id=? AND review_state='unreviewed'",
            (beat_id,),
        )
    elif action in ("clear_decision", "unreview"):
        current = conn.execute(
            "SELECT review_state, uncertain FROM beats WHERE id=?", (beat_id,)
        ).fetchone()
        if current["uncertain"]:
            raise UncertainNeedsDecisionError(UNCERTAIN_KEEPS_DECISION)
        if action == "unreview":
            conn.execute(
                "UPDATE beats SET review_state='unreviewed', reassigned_category=NULL "
                "WHERE id=?",
                (beat_id,),
            )
        elif current["review_state"] in ("confirmed", "rejected"):
            # Only the decision goes: categories the reviewer chose stay.
            conn.execute(
                "UPDATE beats SET review_state='reviewed' WHERE id=?",
                (beat_id,),
            )
    elif action == "remove_flag":
        before = conn.execute(
            "SELECT review_state, " + ", ".join(CATEGORY_COLUMNS) + " FROM beats WHERE id=?",
            (beat_id,),
        ).fetchone()
        if before["review_state"] == "confirmed" and not any(
            before[col] for col in CATEGORY_COLUMNS if col != category
        ):
            raise ReviewRuleError(ARRHYTHMIA_NEEDS_CATEGORY)
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
    the file that has been looked at or decided (anything but unreviewed).
    Unreviewed beats are absent, so they fall back to the unreviewed default
    with `uncertain` cleared: the mark only means something next to a
    decision."""
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


def load_confirmed_categories(conn: sqlite3.Connection, file_id: int) -> dict[float, list[str]]:
    """`ts -> categories` for every confirmed (Arrhythmia) beat whose
    categories the reviewer did not choose by hand: the ones it was flagged
    with when it was confirmed. They are part of the decision, so a re-run of
    detection (beats or arrhythmia) keeps them instead of re-flagging the
    beat. (Reviewer-chosen categories travel in `reassigned_category`.)"""
    out: dict[float, list[str]] = {}
    for row in conn.execute(
        "SELECT ts, reassigned_category, " + ", ".join(CATEGORY_COLUMNS) + " FROM beats "
        "WHERE file_id=? AND review_state='confirmed'",
        (file_id,),
    ).fetchall():
        if parse_categories(row["reassigned_category"]):
            continue
        names = [col for col in CATEGORY_COLUMNS if row[col]]
        if names:
            out[row["ts"]] = names
    return out


# How far apart a saved beat and a newly detected one may be and still be
# the same beat, for carrying a review over (the report matching uses the
# same window). A re-detection with other settings can move a peak by a
# sample or two; a different heartbeat is ~100 ms away even in a mouse.
REVIEW_MATCH_TOLERANCE_S = 0.010


def match_beats(new_ts, old_ts, tolerance: float = REVIEW_MATCH_TOLERANCE_S) -> dict[float, float]:
    """`old ts -> new ts`: each saved beat paired with the nearest new beat
    within `tolerance`, one to one, closest pairs first (so an exact match
    always wins). Saved beats with no new beat near them are left out."""
    new = sorted(set(float(t) for t in new_ts))
    if not new:
        return {}
    pairs = []
    for t in old_ts:
        t = float(t)
        i = bisect.bisect_left(new, t)
        for j in (i - 1, i):
            if 0 <= j < len(new):
                gap = abs(new[j] - t)
                if gap <= tolerance:
                    pairs.append((gap, t, new[j]))
    pairs.sort()
    used_new: set[float] = set()
    out: dict[float, float] = {}
    for _, t, n in pairs:
        if t in out or n in used_new:
            continue
        out[t] = n
        used_new.add(n)
    return out


class CarriedReview(NamedTuple):
    """A saved review moved onto a new set of beats (see `carry_review`)."""

    # new ts -> (review_state, reassigned_category, uncertain)
    state: dict
    # new ts -> categories of a confirmed beat (see load_confirmed_categories)
    confirmed_categories: dict
    # saved beats that were looked at or decided but have no new beat
    dropped: list


def carry_review(beat_df: pd.DataFrame, review_state: dict, confirmed_categories: dict | None = None) -> CarriedReview:
    """The saved review (`review_state`, `confirmed_categories`, keyed by the
    saved beats' ts) moved onto `beat_df`'s beats: each saved beat to the
    same beat in `beat_df` (match_beats). What could not be carried is listed
    in `dropped`, so the caller can keep a backup and say so."""
    pairs = match_beats(beat_df["ts"].tolist() if len(beat_df) else [], review_state.keys())
    state = {pairs[ts]: value for ts, value in review_state.items() if ts in pairs}
    categories = {
        pairs[ts]: names for ts, names in (confirmed_categories or {}).items() if ts in pairs
    }
    dropped = [ts for ts in review_state if ts not in pairs]
    return CarriedReview(state, categories, dropped)


def with_reviewer_categories(
    beat_df: pd.DataFrame,
    review_state: dict[float, tuple[str, str | None, bool]],
    confirmed_categories: dict[float, list[str]] | None = None,
) -> pd.DataFrame:
    """`beat_df` with the categories reviewers chose (`reassigned_category`
    in `review_state`) and those confirmed beats were confirmed with
    (`confirmed_categories`), both by exact ts, written over the detector's
    flags, so a re-run of detection does not undo them. A confirmed beat
    left with no category at all gets 'other_arrhythmia': an arrhythmia
    always has one. A NEW frame when anything changes; `beat_df` itself is
    never touched."""
    chosen = {
        ts: parse_categories(category)
        for ts, (_, category, _) in review_state.items()
        if category
    }
    chosen = {ts: names for ts, names in chosen.items() if names}
    for ts, names in (confirmed_categories or {}).items():
        state = review_state.get(ts)
        if names and state is not None and state[0] == "confirmed" and ts not in chosen:
            chosen[ts] = list(names)
    confirmed = {
        ts for ts, (state, _, _) in review_state.items() if state == "confirmed" and ts not in chosen
    }
    if not chosen and not confirmed:
        return beat_df
    positions = {ts: i for i, ts in enumerate(beat_df["ts"].tolist())}
    rows = [(positions[ts], names) for ts, names in chosen.items() if ts in positions]
    present = [col for col in CATEGORY_COLUMNS if col in beat_df.columns]
    if confirmed and present:
        flagged = beat_df[present].fillna(False).astype(bool).any(axis=1).tolist()
        rows += [
            (positions[ts], ["other_arrhythmia"])
            for ts in confirmed
            if ts in positions and not flagged[positions[ts]]
        ]
    if not rows:
        return beat_df
    out = beat_df.copy()
    for column in [*CATEGORY_COLUMNS, "any_arrhythmia"]:
        values = out[column].tolist() if column in out.columns else [None] * len(out)
        for i, names in rows:
            if column == "any_arrhythmia" or column in names:
                values[i] = True
            elif not pd.isna(values[i]):
                # A category the detector never evaluated stays blank.
                values[i] = False
        out[column] = values
    return out


def with_review_state(
    beat_df: pd.DataFrame,
    review_state: dict[float, tuple[str, str | None, bool]],
    confirmed_categories: dict[float, list[str]] | None = None,
) -> pd.DataFrame:
    """A NEW frame with `review_state`/`reassigned_category`/`uncertain` filled
    in from `review_state` by exact `ts` (default unreviewed/None/False), same
    matching rule as `replace_beats`. Never touches `beat_df`: it may be the
    frame other requests are reading from `beat_cache` right now.
    """
    default = ("unreviewed", None, False)
    # Saved beats are matched to these within a few ms, not only exactly.
    carried = carry_review(beat_df, review_state, confirmed_categories)
    review_state = carried.state
    beat_df = with_reviewer_categories(beat_df, review_state, carried.confirmed_categories)
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
    conn: sqlite3.Connection,
    file_id: int,
    beat_df,
    *,
    commit: bool = True,
    carry: bool = True,
) -> CarriedReview:
    """Delete-then-reinsert every beat for `file_id` from `beat_df`.

    Wholesale-replacing (rather than merging) keeps repeated persists
    idempotent: calling this twice for the same detection result leaves
    exactly one row per beat, not duplicates.

    The saved review is carried over (`carry`): every beat that was looked at
    or decided keeps its `review_state`/`reassigned_category`/`uncertain` on
    the same beat in `beat_df` (matched within REVIEW_MATCH_TOLERANCE_S, so a
    peak a sample away after a re-detection is still the same beat), and a
    confirmed beat keeps its categories. What found no beat is returned in
    `dropped`: the caller backs it up first and tells the technician. With
    `carry=False` (the beats are another channel's) nothing is carried and
    every reviewed beat counts as dropped.

    `commit` defaults to True (standalone-call behavior). Pass
    `commit=False` when this call is one half of a larger transaction the
    caller will commit (or roll back) itself.

    The returned `state` / `confirmed_categories` are keyed by `beat_df`'s ts:
    the caller (`persist_beats` in db_routes.py) uses them to sync the review
    back onto `beat_cache[path]` (the live frame this was handed), which never
    gets that write otherwise.
    """
    saved = load_review_state(conn, file_id)
    if carry:
        carried = carry_review(beat_df, saved, load_confirmed_categories(conn, file_id))
    else:
        carried = CarriedReview({}, {}, list(saved.keys()))
    # Categories a reviewer chose, or confirmed, win over the fresh flags.
    beat_df = with_reviewer_categories(beat_df, carried.state, carried.confirmed_categories)

    conn.execute("DELETE FROM beats WHERE file_id=?", (file_id,))

    default_review = ("unreviewed", None, False)
    review_values = [
        carried.state.get(ts, default_review) for ts in beat_df["ts"].tolist()
    ]
    write_beats(conn, file_id, beat_df, review_values)

    if commit:
        conn.commit()

    return carried


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
# How long a backup can be kept for, in days (what the app offers is 7, 14 or
# 30; anything in between is accepted).
BACKUP_MIN_DAYS = 1
BACKUP_MAX_DAYS = 90


def backup_days(days) -> int:
    """`days` as a backup retention the app accepts (the default when unset)."""
    try:
        value = int(days)
    except (TypeError, ValueError):
        return BACKUP_RETENTION_DAYS
    return min(max(value, BACKUP_MIN_DAYS), BACKUP_MAX_DAYS)
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


def backup_details(conn: sqlite3.Connection, row: sqlite3.Row | None) -> dict | None:
    """What the app shows about a backup: when it was made and until when it
    is kept (and for how many days), what it holds (beats, reviewed beats,
    decisions, bad-data ranges, channel, the report its review came from),
    why it was made (`reason`: report, restore, redetect, channel, fresh or
    earlier), and how many older backups of the same recording are
    kept besides it. None for no backup."""
    if row is None:
        return None
    counts = conn.execute(
        "SELECT COUNT(*) AS beats, "
        "COALESCE(SUM(review_state != 'unreviewed'), 0) AS reviewed, "
        "COALESCE(SUM(review_state IN ('confirmed', 'rejected')), 0) AS decided "
        "FROM backup_beats WHERE backup_id=?",
        (row["id"],),
    ).fetchone()
    marks = conn.execute(
        "SELECT COUNT(*) FROM backup_bad_data_marks WHERE backup_id=?", (row["id"],)
    ).fetchone()[0]
    older = conn.execute(
        "SELECT COUNT(*) FROM review_backups WHERE path=? AND size=? AND mtime=? "
        "AND id < ? AND expires_at > ?",
        (row["path"], row["size"], row["mtime"], row["id"], _now_iso()),
    ).fetchone()[0]
    try:
        span = datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"])
        days = max(1, round(span.total_seconds() / 86400))
    except (TypeError, ValueError):
        days = BACKUP_RETENTION_DAYS
    reason = _BACKUP_REASONS.get(row["replaced_by"], "report")
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "days": days,
        "replaced_by": row["replaced_by"] if reason == "report" else None,
        "made_by_restore": reason == "restore",
        "reason": reason,
        "beats": counts["beats"],
        "reviewed": counts["reviewed"],
        "decided": counts["decided"],
        "bad_data_marks": marks,
        "channel": row["channel"],
        "source_report": row["source_report"],
        "older_backups": older,
    }


def latest_backup(
    conn: sqlite3.Connection, path: str, size: int, mtime: float
) -> sqlite3.Row | None:
    """The newest unexpired backup of this file's review, if any."""
    return conn.execute(
        "SELECT * FROM review_backups WHERE path=? AND size=? AND mtime=? "
        "AND expires_at > ? ORDER BY id DESC LIMIT 1",
        (path, size, mtime, _now_iso()),
    ).fetchone()


def list_backups(
    conn: sqlite3.Connection, path: str, size: int, mtime: float
) -> list[sqlite3.Row]:
    """Every unexpired backup of this file's review, newest first."""
    return conn.execute(
        "SELECT * FROM review_backups WHERE path=? AND size=? AND mtime=? "
        "AND expires_at > ? ORDER BY id DESC",
        (path, size, mtime, _now_iso()),
    ).fetchall()


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
# Why a backup was made when no report replaced the review (`replaced_by`).
BACKUP_REASON_REDETECT = "(beat detection was run again)"
BACKUP_REASON_CHANNEL = "(switched channel)"
BACKUP_REASON_FRESH = "(started fresh)"
BACKUP_REASON_EARLIER = "(continued the review of an earlier version)"
_BACKUP_REASONS = {
    BACKUP_REASON_EARLIER: "earlier",
    RESTORED_FROM_BACKUP: "restore",
    BACKUP_REASON_REDETECT: "redetect",
    BACKUP_REASON_CHANNEL: "channel",
    BACKUP_REASON_FRESH: "fresh",
}


def restore_backup(
    conn: sqlite3.Connection, backup: sqlite3.Row, days: int = BACKUP_RETENTION_DAYS
) -> tuple[int, sqlite3.Row | None]:
    """Put a backup back as the file's saved review and delete that backup,
    in one transaction. The review saved now is not thrown away: it is backed
    up first (when there is any) and kept for `days`, so restoring is a swap
    that can itself be undone. Returns (the file's id, the backup of the
    review it replaced).
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
                kept = create_backup(conn, current, RESTORED_FROM_BACKUP, days)
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
