import importlib.metadata
import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from fastapi import APIRouter, Request
from physiology_analysis_tools.modules import (
    arrhythmia_detection,
    heartbeat_detection,
    ml_tools,
)

from backend import beat_editing, categories, db
from backend.locks import bump_beat_epoch, path_lock
from backend.models import (
    ArrhythmiaSettingsModel,
    BadDataAddRequest,
    BadDataAddResult,
    BadDataDeleteRequest,
    BadDataDeleteResult,
    BadDataMark,
    BadDataUpdateRequest,
    BadDataUpdateResult,
    BeatDeleteRequest,
    BeatDeleteResult,
    BeatSettingsModel,
    CHANNEL_LABEL_MAX_LENGTH,
    CategoryUpdateRequest,
    CategoryUpdateResult,
    ChannelLabelRequest,
    ChannelLabelResult,
    ChannelPersistRequest,
    ChannelPersistResult,
    AdoptPreviousRequest,
    AdoptPreviousResult,
    DiscardStateRequest,
    DiscardStateResult,
    PreviousVersionInfo,
    FileStateResult,
    FileStateSummaryResult,
    FileSummariesRequest,
    FileSummariesResult,
    FileSummary,
    PersistBeatsRequest,
    PersistBeatsResult,
    PersistedBeat,
    ReportRequest,
    ReportResult,
    RestoreBackupRequest,
    RestoreBackupResult,
    ReviewBackupInfo,
)

router = APIRouter(prefix="/files", tags=["persistence"])

REASSIGNABLE_CATEGORIES = set(categories.REASSIGNABLE_CATEGORIES)


@router.get("/state", response_model=FileStateResult)
def get_file_state(path: str, request: Request) -> FileStateResult:
    imported = request.app.state.imported_files.get(path)
    if imported is None or imported.status != "ok":
        return FileStateResult(status="error", error=f"File not imported: {path}")

    conn = db.connect(request.app.state.db_path)
    try:
        row = db.get_file_row(conn, path, imported.size, imported.modified_time)
        if row is None:
            # Nothing saved for the file as it is now. Say so if there is a
            # review of an earlier version of it (the file was saved again on
            # disk), and a backup (Start fresh keeps one).
            backups = _backups(conn, path, imported)
            return FileStateResult(
                status="ok",
                found=False,
                backup=backups[0] if backups else None,
                backups=backups,
                previous_version=_previous_version(
                    conn, db.previous_version_row(conn, path, imported.size, imported.modified_time)
                ),
            )

        # The cache restore below replaces beat_cache[path] with what SQLite
        # held when it was read, so the read and the swap must be one step
        # under the path's lock (an edit landing in between would be undone).
        # It never WAITS for it though: a lock that is busy means an edit or
        # arrhythmia run is mid-flight on this file, whose frame is newer than
        # this snapshot, so skipping the restore is the right answer and a
        # request that only wants the saved state is not held up behind it.
        lock = path_lock(path)
        holding_lock = lock.acquire(blocking=False)
        try:
            beat_rows = conn.execute(
                "SELECT * FROM beats WHERE file_id=? ORDER BY ts",
                (row["id"],),
            ).fetchall()

            # Best-effort restore of the in-memory beat_cache entry every other
            # beat-reading endpoint relies on. This is a side effect on top of
            # this endpoint's normal response contract, so a malformed restore
            # must never turn an otherwise-successful GET into an error.
            # A path evicted since the check above is not restored: nothing
            # would ever free it.
            if holding_lock and request.app.state.imported_files.get(path) is imported:
                try:
                    if beat_rows:
                        request.app.state.beat_cache[path] = _cache_frame(beat_rows)
                        # The frame is the saved one again, not a detection.
                        getattr(request.app.state, "fresh_beats", set()).discard(path)
                        # The saved review is what the technician continues: a
                        # detection still running from the import must not
                        # replace it in memory when it lands (see
                        # locks.beat_epoch). That held for a review taken from
                        # a report, and holds as much for any other: one made
                        # with other detection settings would otherwise be
                        # swapped for new beats behind the technician's back.
                        bump_beat_epoch(request.app.state, path)
                except Exception:
                    pass
        finally:
            if holding_lock:
                lock.release()

        beats = [
            PersistedBeat(
                ts=beat_row["ts"],
                rr=beat_row["rr"],
                r_amplitude=beat_row["r_amplitude"],
                hr=beat_row["hr"],
                manual_added_at=beat_row["manual_added_at"],
                bradycardia_absolute=_to_bool(beat_row["bradycardia_absolute"]),
                tachycardia_absolute=_to_bool(beat_row["tachycardia_absolute"]),
                skipped_beat=_to_bool(beat_row["skipped_beat"]),
                prem_beat=_to_bool(beat_row["prem_beat"]),
                abn_cluster=_to_bool(beat_row["abn_cluster"]),
                any_arrhythmia=_to_bool(beat_row["any_arrhythmia"]),
                other_arrhythmia=_to_bool(beat_row["other_arrhythmia"]),
                review_state=beat_row["review_state"],
                reassigned_category=beat_row["reassigned_category"],
                uncertain=bool(beat_row["uncertain"]),
            )
            for beat_row in beat_rows
        ]

        mark_rows = conn.execute(
            "SELECT * FROM bad_data_marks WHERE file_id=? ORDER BY id",
            (row["id"],),
        ).fetchall()
        bad_data_marks = [
            BadDataMark(
                id=mark_row["id"], start=mark_row["start"], stop=mark_row["stop"]
            )
            for mark_row in mark_rows
        ]

        beat_settings = (
            BeatSettingsModel(**json.loads(row["beat_settings_json"]))
            if row["beat_settings_json"] is not None
            else None
        )
        arrhythmia_settings = (
            ArrhythmiaSettingsModel(**json.loads(row["arrhythmia_settings_json"]))
            if row["arrhythmia_settings_json"] is not None
            else None
        )

        backups = _backups(conn, path, imported)
        return FileStateResult(
            status="ok",
            found=True,
            channel=row["channel"],
            beats=beats,
            bad_data_marks=bad_data_marks,
            beat_settings=beat_settings,
            arrhythmia_settings=arrhythmia_settings,
            source_report=row["source_report"],
            source_report_at=row["source_report_at"],
            backup=backups[0] if backups else None,
            backups=backups,
        )
    except Exception as e:
        return FileStateResult(status="error", error=str(e))
    finally:
        conn.close()


def _cache_frame(beat_rows) -> pd.DataFrame:
    """Saved `beats` rows (ordered by ts) as the in-memory beat frame every
    beat-reading endpoint works from."""
    cache_df = pd.DataFrame(
        {
            "ts": [beat_row["ts"] for beat_row in beat_rows],
            "RR": [beat_row["rr"] for beat_row in beat_rows],
            "R_amplitude": [beat_row["r_amplitude"] for beat_row in beat_rows],
            "HR": [beat_row["hr"] for beat_row in beat_rows],
        }
    )
    for col in categories.ALL_OPTIONAL_COLUMNS:
        col_values = [beat_row[col] for beat_row in beat_rows]
        if any(value is not None for value in col_values):
            cache_df[col] = [_to_bool(value) for value in col_values]
    if any(beat_row["manual_added_at"] for beat_row in beat_rows):
        cache_df["manual_added_at"] = [
            beat_row["manual_added_at"] for beat_row in beat_rows
        ]
    cache_df["review_state"] = [beat_row["review_state"] for beat_row in beat_rows]
    cache_df["reassigned_category"] = [
        beat_row["reassigned_category"] for beat_row in beat_rows
    ]
    cache_df["uncertain"] = [bool(beat_row["uncertain"]) for beat_row in beat_rows]
    return cache_df


def _backup_info(conn, row) -> ReviewBackupInfo | None:
    details = db.backup_details(conn, row)
    return ReviewBackupInfo(**details) if details else None


def _backups(conn, path, imported) -> list[ReviewBackupInfo]:
    return [
        _backup_info(conn, row)
        for row in db.list_backups(conn, path, imported.size, imported.modified_time)
    ]


def _previous_version(conn, row) -> PreviousVersionInfo | None:
    if row is None:
        return None
    counts = db.review_counts(conn, row["id"])
    decided = conn.execute(
        "SELECT COUNT(*) FROM beats WHERE file_id=? AND review_state IN ('confirmed', 'rejected')",
        (row["id"],),
    ).fetchone()[0]
    reviewed = conn.execute(
        "SELECT COUNT(*) FROM beats WHERE file_id=? AND review_state != 'unreviewed'",
        (row["id"],),
    ).fetchone()[0]
    return PreviousVersionInfo(
        saved_at=row["updated_at"],
        beats=counts["beats"],
        reviewed=reviewed,
        decided=decided,
        bad_data_marks=counts["bad_data_marks"],
        channel=row["channel"],
    )


@router.post("/state/adopt-previous", response_model=AdoptPreviousResult)
def adopt_previous_version(payload: AdoptPreviousRequest, request: Request) -> AdoptPreviousResult:
    """Continue the review saved for an earlier version of the file (it was
    saved again on disk since): it becomes the review of the file as it is
    now, with its backups. The screen then reloads it like any saved review."""
    with path_lock(payload.path):
        imported = request.app.state.imported_files.get(payload.path)
        if imported is None or imported.status != "ok":
            return AdoptPreviousResult(status="error", error=f"File not imported: {payload.path}")
        conn = db.connect(request.app.state.db_path)
        try:
            row = db.previous_version_row(
                conn, payload.path, imported.size, imported.modified_time
            )
            if row is None:
                return AdoptPreviousResult(
                    status="error", error="There is no earlier review of this recording."
                )
            db.adopt_previous_version(conn, row, imported.size, imported.modified_time)
            return AdoptPreviousResult(status="ok")
        except Exception as e:
            return AdoptPreviousResult(status="error", error=str(e))
        finally:
            conn.close()


@router.get("/state/summary", response_model=FileStateSummaryResult)
def get_file_state_summary(path: str, request: Request) -> FileStateSummaryResult:
    """How much review work is saved for a file (counts only) and whether a
    backup of an earlier review exists: what replacing it would discard."""
    imported = request.app.state.imported_files.get(path)
    if imported is None or imported.status != "ok":
        return FileStateSummaryResult(status="error", error=f"File not imported: {path}")
    conn = db.connect(request.app.state.db_path)
    try:
        backup = _backup_info(
            conn, db.latest_backup(conn, path, imported.size, imported.modified_time)
        )
        row = db.get_file_row(conn, path, imported.size, imported.modified_time)
        if row is None:
            return FileStateSummaryResult(status="ok", found=False, backup=backup)
        counts = conn.execute(
            "SELECT COUNT(*) AS beats, "
            "COALESCE(SUM(review_state != 'unreviewed'), 0) AS reviewed, "
            "COALESCE(SUM(review_state IN ('confirmed', 'rejected')), 0) AS decided "
            "FROM beats WHERE file_id=?",
            (row["id"],),
        ).fetchone()
        marks = conn.execute(
            "SELECT COUNT(*) FROM bad_data_marks WHERE file_id=?", (row["id"],)
        ).fetchone()[0]
        return FileStateSummaryResult(
            status="ok",
            found=True,
            channel=row["channel"],
            beats=counts["beats"],
            reviewed=counts["reviewed"],
            decided=counts["decided"],
            bad_data_marks=marks,
            source_report=row["source_report"],
            source_report_at=row["source_report_at"],
            backup=backup,
        )
    except Exception as e:
        return FileStateSummaryResult(status="error", error=str(e))
    finally:
        conn.close()


# More would only be a slow request: the lists hold at most a few hundred.
MAX_SUMMARY_PATHS = 2000


def _file_summary(conn, path: str, labels: dict[str, str]) -> FileSummary:
    summary = FileSummary(path=path, channel_labels=labels)
    try:
        stat = os.stat(path)
    except OSError:
        stat = None
    if stat is not None:
        summary.exists = True
        summary.size = stat.st_size
        summary.modified_at = stat.st_mtime
        # A file changed on disk since its review was saved opens without it.
        row = db.get_file_row(conn, path, stat.st_size, stat.st_mtime)
    else:
        row = db.latest_file_row(conn, path)
    if row is None:
        return summary
    counts = db.review_counts(conn, row["id"])
    summary.found = True
    summary.channel = row["channel"]
    summary.beats = counts["beats"]
    mean_hr = counts["mean_hr"]
    summary.mean_hr = float(mean_hr) if mean_hr is not None and np.isfinite(mean_hr) else None
    summary.arrhythmia_beats = counts["arrhythmia_beats"]
    summary.to_review = counts["to_review"]
    summary.reviewed = counts["reviewed"]
    summary.uncertain = counts["uncertain"]
    summary.bad_data_marks = counts["bad_data_marks"]
    summary.last_saved_at = row["updated_at"]
    summary.source_report = row["source_report"]
    return summary


@router.post("/summaries", response_model=FileSummariesResult)
def get_file_summaries(
    payload: FileSummariesRequest, request: Request
) -> FileSummariesResult:
    """What the file lists (Home's recordings, the sidebar) sort and filter by,
    for many files at once: whether each is on disk, its saved review's counts
    and its renamed channels. Read-only; the files need not be imported. A
    file that is gone shows its latest saved review."""
    paths = list(dict.fromkeys(payload.paths))[:MAX_SUMMARY_PATHS]
    conn = db.connect(request.app.state.db_path)
    try:
        labels = db.get_channel_labels(conn, paths)
        return FileSummariesResult(
            status="ok",
            summaries=[_file_summary(conn, path, labels.get(path, {})) for path in paths],
        )
    except Exception as e:
        return FileSummariesResult(status="error", error=str(e))
    finally:
        conn.close()


@router.post("/state/restore-backup", response_model=RestoreBackupResult)
def restore_backup(payload: RestoreBackupRequest, request: Request) -> RestoreBackupResult:
    """Put back the newest unexpired backup (the review a report replaced).
    What is saved now is backed up first, so this is a swap, not a loss
    (`kept`). The in-memory beats become the restored ones."""
    with path_lock(payload.path):
        state = request.app.state
        imported = state.imported_files.get(payload.path)
        if imported is None or imported.status != "ok":
            return RestoreBackupResult(
                status="error", error=f"File not imported: {payload.path}"
            )
        conn = db.connect(state.db_path)
        try:
            backup = db.latest_backup(
                conn, payload.path, imported.size, imported.modified_time
            )
            if payload.backup_id is not None:
                backup = next(
                    (
                        row
                        for row in db.list_backups(
                            conn, payload.path, imported.size, imported.modified_time
                        )
                        if row["id"] == payload.backup_id
                    ),
                    None,
                )
            if backup is None:
                return RestoreBackupResult(
                    status="error",
                    error="There is no backup to restore (it may have expired).",
                )
            file_id, kept = db.restore_backup(conn, backup, db.backup_days(payload.days))
            kept_info = _backup_info(conn, kept)
            beat_rows = conn.execute(
                "SELECT * FROM beats WHERE file_id=? ORDER BY ts", (file_id,)
            ).fetchall()
        except Exception as e:
            return RestoreBackupResult(status="error", error=str(e))
        finally:
            conn.close()
        # The restored review is the recording's beats now (none: nothing
        # detected yet, as when the backup was taken).
        if beat_rows:
            state.beat_cache[payload.path] = _cache_frame(beat_rows)
        else:
            state.beat_cache.pop(payload.path, None)
        state.typical_rr_cache.pop(payload.path, None)
        getattr(state, "fresh_beats", set()).discard(payload.path)
        bump_beat_epoch(state, payload.path)
        return RestoreBackupResult(status="ok", kept=kept_info)


def _to_bool(value: int | None) -> bool | None:
    return None if value is None else bool(value)


def _channel_display(imported, channel: str | None) -> str | None:
    """A channel as the report shows it: "custom name (original name)" when
    the technician renamed it, else just its own name."""
    label = imported.channel_labels.get(channel) if channel else None
    return f"{label} ({channel})" if label else channel


@router.put("/channel-label", response_model=ChannelLabelResult)
def put_channel_label(payload: ChannelLabelRequest, request: Request) -> ChannelLabelResult:
    """Give `channel` a display name in each of `paths` (one file or several),
    or reset it. Only the name shown changes: the channel keeps its own name
    as its key for detection, saved beats and every other route."""
    label = " ".join((payload.label or "").split())
    if len(label) > CHANNEL_LABEL_MAX_LENGTH:
        return ChannelLabelResult(
            status="error",
            error=f"Keep the name to {CHANNEL_LABEL_MAX_LENGTH} characters or fewer",
        )
    # A blank name, or the channel's own name, means "no custom name".
    stored = label if label and label != payload.channel else None

    registry = request.app.state.imported_files
    targets, skipped = [], []
    for path in dict.fromkeys(payload.paths):
        imported = registry.get(path)
        if imported is not None and imported.status == "ok" and payload.channel in imported.channels:
            targets.append(path)
        else:
            skipped.append(path)
    if not targets:
        return ChannelLabelResult(
            status="error",
            skipped=skipped,
            error=f"None of these files has a channel named {payload.channel}",
        )
    # Two channels shown under one name would make the channel list ambiguous.
    if stored is not None:
        for path in targets:
            imported = registry[path]
            taken = {
                imported.channel_labels.get(other, other).casefold()
                for other in imported.channels
                if other != payload.channel
            } | {other.casefold() for other in imported.channels if other != payload.channel}
            if stored.casefold() in taken:
                return ChannelLabelResult(
                    status="error",
                    error=f"{imported.filename} already has a channel called {stored}",
                )

    conn = db.connect(request.app.state.db_path)
    try:
        db.set_channel_label(conn, targets, payload.channel, stored)
    except Exception as e:
        return ChannelLabelResult(status="error", error=str(e))
    finally:
        conn.close()

    # Keep the in-memory import results in step, so GET /files and the report
    # see the new name without a re-import.
    labels = {}
    for path in targets:
        imported = registry[path]
        names = {k: v for k, v in imported.channel_labels.items() if k != payload.channel}
        if stored is not None:
            names[payload.channel] = stored
        imported.channel_labels = names
        labels[path] = names
    return ChannelLabelResult(status="ok", labels=labels, skipped=skipped)


@router.put("/channel", response_model=ChannelPersistResult)
def put_channel(
    payload: ChannelPersistRequest, request: Request
) -> ChannelPersistResult:
    imported = request.app.state.imported_files.get(payload.path)
    if imported is None or imported.status != "ok":
        return ChannelPersistResult(
            status="error", error=f"File not imported: {payload.path}"
        )

    conn = db.connect(request.app.state.db_path)
    try:
        db.upsert_file(
            conn,
            payload.path,
            imported.size,
            imported.modified_time,
            channel=payload.channel,
        )
        return ChannelPersistResult(status="ok", channel=payload.channel)
    except Exception as e:
        return ChannelPersistResult(status="error", error=str(e))
    finally:
        conn.close()


@router.post("/beats", response_model=PersistBeatsResult)
def persist_beats(
    payload: PersistBeatsRequest, request: Request
) -> PersistBeatsResult:
    # Snapshot, SQLite write and cache swap are one step under the path's lock,
    # so an edit made during the write cannot be lost from the cache.
    with path_lock(payload.path):
        return _persist_beats_locked(payload, request)


def _persist_beats_locked(
    payload: PersistBeatsRequest, request: Request
) -> PersistBeatsResult:
    imported = request.app.state.imported_files.get(payload.path)
    if imported is None or imported.status != "ok":
        return PersistBeatsResult(
            status="error", error=f"File not imported: {payload.path}"
        )

    beat_df = request.app.state.beat_cache.get(payload.path)
    if beat_df is None:
        return PersistBeatsResult(
            status="error", error="Beat detection has not been run for this file yet"
        )

    # Beats freshly detected (not yet saved) replace a review taken from a
    # report, so the file stops saying it came from one. Otherwise the saved
    # beats are still the report's, and so are the beat settings saved with
    # them (an arrhythmia run only changes the flags).
    fresh_beats = getattr(request.app.state, "fresh_beats", set())
    fresh = payload.path in fresh_beats

    conn = db.connect(request.app.state.db_path)
    try:
        existing = db.get_file_row(conn, payload.path, imported.size, imported.modified_time)
        from_report = existing is not None and bool(existing["source_report"])
        # The saved review belongs to the beats of the channel it was made on:
        # another channel's beats start a review of their own.
        switching = (
            existing is not None
            and existing["channel"] is not None
            and existing["channel"] != payload.channel
        )
        # Anything of the saved review these beats can't carry (a reviewed beat
        # no longer found, or a whole other channel's review) is backed up
        # first and reported, never dropped silently.
        backup = None
        if existing is not None:
            saved_review = db.load_review_state(conn, existing["id"])
            lost = (
                len(saved_review)
                if switching
                else len(db.carry_review(beat_df, saved_review).dropped)
            )
            if lost > 0:
                backup = db.create_backup(
                    conn,
                    existing,
                    db.BACKUP_REASON_CHANNEL if switching else db.BACKUP_REASON_REDETECT,
                )
        provenance = {}
        if not from_report or fresh:
            # The beats are this channel's detection: it and the settings that
            # made it are what the review is now. (Beats still the report's
            # stay on the channel they were saved against: a save racing the
            # replace must not move them.)
            provenance["channel"] = payload.channel
            provenance["beat_settings_json"] = json.dumps(
                request.app.state.beat_settings.__dict__
            )
        if from_report and fresh:
            provenance.update(source_report=None, source_report_at=None)
        file_id = db.upsert_file(
            conn,
            payload.path,
            imported.size,
            imported.modified_time,
            arrhythmia_settings_json=json.dumps(
                request.app.state.arrhythmia_settings.__dict__
            ),
            commit=False,
            **provenance,
        )
        carried = db.replace_beats(
            conn, file_id, beat_df, commit=False, carry=not switching
        )
        conn.commit()
        fresh_beats.discard(payload.path)
        backup_info = _backup_info(conn, backup)

        # Sync review_state/reassigned_category/uncertain onto the
        # beat_cache[path] DataFrame `/beats/window` reads from —
        # replace_beats above only preserved them in SQLite; without this, a
        # beat reviewed before this redetect+persist would show as
        # "unreviewed" on the graph until individually touched again. Every
        # row gets a value (defaulting to unreviewed/None/not uncertain),
        # matched by `ts`. This builds a NEW frame and swaps it in rather than
        # adding the columns to the cached one: window requests iterate that
        # frame while this runs, and columns appearing under them
        # mid-iteration made them fail.
        request.app.state.beat_cache[payload.path] = db.with_review_state(
            beat_df, carried.state, carried.confirmed_categories
        )

        return PersistBeatsResult(
            status="ok",
            count=len(beat_df),
            review_carried=len(carried.state),
            review_dropped=len(carried.dropped),
            previous_channel=existing["channel"] if switching else None,
            backup=backup_info,
        )
    except Exception as e:
        conn.rollback()
        return PersistBeatsResult(status="error", error=str(e))
    finally:
        conn.close()


@router.patch("/beats/category", response_model=CategoryUpdateResult)
def update_beat_category(
    payload: CategoryUpdateRequest, request: Request
) -> CategoryUpdateResult:
    # SQLite write + cache update as one step per path (see backend/locks.py):
    # a review made while an arrhythmia run is computing waits for it and then
    # applies to the new frame, instead of being overwritten by it.
    with path_lock(payload.path):
        return _update_beat_category_locked(payload, request)


def _update_beat_category_locked(
    payload: CategoryUpdateRequest, request: Request
) -> CategoryUpdateResult:
    if payload.action not in {
        "confirm",
        "reject",
        "reassign",
        "remove_flag",
        "set_uncertain",
        "seen",
        "clear_decision",
        "unreview",
        "set_categories",
    }:
        return CategoryUpdateResult(
            status="error",
            error=f"Unknown action: {payload.action!r}",
        )

    if payload.action == "set_uncertain" and payload.uncertain is None:
        return CategoryUpdateResult(
            status="error",
            error="uncertain is required for a set_uncertain action",
        )

    if payload.action == "set_categories" and payload.categories is None:
        return CategoryUpdateResult(
            status="error", error="categories is required for a set_categories action"
        )
    if payload.categories is not None:
        unknown = [c for c in payload.categories if c not in REASSIGNABLE_CATEGORIES]
        if unknown:
            return CategoryUpdateResult(
                status="error", error=f"Invalid category: {unknown[0]!r}"
            )

    if payload.action in {"reassign", "remove_flag"}:
        if payload.category is None:
            return CategoryUpdateResult(
                status="error",
                error=f"category is required for a {payload.action} action",
            )
        if payload.category not in REASSIGNABLE_CATEGORIES:
            return CategoryUpdateResult(
                status="error",
                error=f"Invalid category: {payload.category!r}",
            )

    imported = request.app.state.imported_files.get(payload.path)
    if imported is None or imported.status != "ok":
        return CategoryUpdateResult(
            status="error", error=f"File not imported: {payload.path}"
        )

    conn = db.connect(request.app.state.db_path)
    try:
        file_row = db.get_file_row(
            conn, payload.path, imported.size, imported.modified_time
        )
        if file_row is None:
            return CategoryUpdateResult(
                status="error",
                error="No persisted data for this file — run POST /files/beats first",
            )

        try:
            updated = db.update_beat_category(
                conn,
                file_row["id"],
                payload.ts,
                payload.action,
                payload.category,
                payload.uncertain,
                payload.categories,
            )
        except db.ReviewRuleError as e:
            return CategoryUpdateResult(status="error", error=str(e))
        if updated is None:
            return CategoryUpdateResult(
                status="error", error="No beat found at this timestamp"
            )

        # Keep the in-memory beat_cache entry (what every beat-reading
        # endpoint actually reads from) in sync with this SQLite write, so a
        # confirm/reject/reassign is visible immediately without a restart.
        # No cache entry yet is a no-op, not an error — the DB write above
        # already succeeded regardless.
        # The edit goes on a copy that then replaces the cache entry: window
        # requests may be iterating the cached frame right now.
        cached_df = request.app.state.beat_cache.get(payload.path)
        if cached_df is not None:
            mask = cached_df["ts"] == updated["ts"]
            if mask.any():
                new_df = cached_df.copy()
                for col in categories.ALL_OPTIONAL_COLUMNS:
                    value = _to_bool(updated[col])
                    # A flag SQLite has as NULL (arrhythmia ran after the last
                    # persist) can't be written into a plain bool column; make
                    # that column nullable first instead of failing the review.
                    if (
                        value is None
                        and col in new_df.columns
                        and new_df[col].dtype == bool
                    ):
                        new_df[col] = new_df[col].astype(object)
                    new_df.loc[mask, col] = value
                new_df.loc[mask, "review_state"] = updated["review_state"]
                new_df.loc[mask, "reassigned_category"] = updated["reassigned_category"]
                # A frame that has never been reviewed has no such column yet;
                # start it all-False so it stays a plain bool column.
                if "uncertain" not in new_df.columns:
                    new_df["uncertain"] = False
                new_df.loc[mask, "uncertain"] = bool(updated["uncertain"])
                request.app.state.beat_cache[payload.path] = new_df

        return CategoryUpdateResult(
            status="ok",
            ts=updated["ts"],
            review_state=updated["review_state"],
            reassigned_category=updated["reassigned_category"],
            uncertain=bool(updated["uncertain"]),
            categories=[c for c in categories.REASSIGNABLE_CATEGORIES if updated[c]],
        )
    except Exception as e:
        return CategoryUpdateResult(status="error", error=str(e))
    finally:
        conn.close()


@router.delete("/beats/one", response_model=BeatDeleteResult)
def delete_beat(payload: BeatDeleteRequest, request: Request) -> BeatDeleteResult:
    """Remove a beat that isn't really a beat (a detection false positive).

    Unlike rejecting a beat - which keeps the row and just says it isn't an
    arrhythmia - this drops it from SQLite AND from the in-memory beat_cache
    every beat-reading endpoint serves from, so it disappears from the
    graph, the counts, and the exported report.
    """
    # SQLite delete + cache rebuild as one step per path (see backend/locks.py),
    # so a concurrent arrhythmia run cannot put the beat back afterwards.
    with path_lock(payload.path):
        return _delete_beat_locked(payload, request)


def _delete_beat_locked(payload: BeatDeleteRequest, request: Request) -> BeatDeleteResult:
    imported = request.app.state.imported_files.get(payload.path)
    if imported is None or imported.status != "ok":
        return BeatDeleteResult(
            status="error", error=f"File not imported: {payload.path}"
        )

    conn = db.connect(request.app.state.db_path)
    try:
        file_row = db.get_file_row(
            conn, payload.path, imported.size, imported.modified_time
        )
        if file_row is None:
            return BeatDeleteResult(
                status="error",
                error="No persisted data for this file - run POST /files/beats first",
            )

        if not db.delete_beat(conn, file_row["id"], payload.ts):
            return BeatDeleteResult(
                status="error", error="No beat found at this timestamp"
            )
        # Remember the removal so re-running beat detection does not bring
        # the beat back.
        db.record_manual_delete(conn, file_row["id"], payload.ts)

        cached_df = request.app.state.beat_cache.get(payload.path)
        if cached_df is not None:
            remaining = cached_df[cached_df["ts"] != payload.ts].reset_index(drop=True)
            # The beat after the gap measures its RR from the one before it
            # now, so its RR/HR change too.
            fix = beat_editing.rr_hr_for_successor(remaining, payload.ts)
            if fix is not None:
                fix_ts, fix_rr, fix_hr = fix
                remaining.loc[remaining["ts"] == fix_ts, "RR"] = fix_rr
                remaining.loc[remaining["ts"] == fix_ts, "HR"] = fix_hr
                db.update_beat_rr_hr(conn, file_row["id"], fix_ts, fix_rr, fix_hr)
            request.app.state.beat_cache[payload.path] = remaining

        return BeatDeleteResult(status="ok")
    except Exception as e:
        return BeatDeleteResult(status="error", error=str(e))
    finally:
        conn.close()


@router.delete("/state", response_model=DiscardStateResult)
def discard_file_state(
    payload: DiscardStateRequest, request: Request
) -> DiscardStateResult:
    """Throw away everything saved for this file so the next detection run
    starts clean - the "start fresh" escape hatch from a restored session.

    Also drops the in-memory beat cache, so nothing survives in one place
    after being discarded from the other.
    """
    with path_lock(payload.path):
        return _discard_file_state_locked(payload, request)


def _discard_file_state_locked(
    payload: DiscardStateRequest, request: Request
) -> DiscardStateResult:
    imported = request.app.state.imported_files.get(payload.path)
    if imported is None or imported.status != "ok":
        return DiscardStateResult(
            status="error", error=f"File not imported: {payload.path}"
        )

    conn = db.connect(request.app.state.db_path)
    try:
        file_row = db.get_file_row(
            conn, payload.path, imported.size, imported.modified_time
        )
        backup = None
        if file_row is not None:
            # Starting fresh is undoable: the discarded review is backed up
            # (Restore backup brings it back) when it had anything in it.
            if db.has_saved_review(conn, file_row["id"]):
                backup = db.create_backup(conn, file_row, db.BACKUP_REASON_FRESH)
            db.delete_file_state(conn, file_row["id"])
        backup_info = _backup_info(conn, backup)

        request.app.state.beat_cache.pop(payload.path, None)

        # Nothing persisted in the first place is a successful no-op, not an
        # error: the caller's intent ("leave me with a clean slate") holds.
        return DiscardStateResult(status="ok", backup=backup_info)
    except Exception as e:
        return DiscardStateResult(status="error", error=str(e))
    finally:
        conn.close()


@router.post("/bad-data", response_model=BadDataAddResult)
def add_bad_data(payload: BadDataAddRequest, request: Request) -> BadDataAddResult:
    imported = request.app.state.imported_files.get(payload.path)
    if imported is None or imported.status != "ok":
        return BadDataAddResult(
            status="error", error=f"File not imported: {payload.path}"
        )

    conn = db.connect(request.app.state.db_path)
    try:
        file_id = db.upsert_file(
            conn, payload.path, imported.size, imported.modified_time
        )
        row = db.add_bad_data_mark(conn, file_id, payload.start, payload.stop)
        return BadDataAddResult(
            status="ok",
            mark=BadDataMark(id=row["id"], start=row["start"], stop=row["stop"]),
        )
    except Exception as e:
        return BadDataAddResult(status="error", error=str(e))
    finally:
        conn.close()


@router.patch("/bad-data", response_model=BadDataUpdateResult)
def update_bad_data(
    payload: BadDataUpdateRequest, request: Request
) -> BadDataUpdateResult:
    imported = request.app.state.imported_files.get(payload.path)
    if imported is None or imported.status != "ok":
        return BadDataUpdateResult(
            status="error", error=f"File not imported: {payload.path}"
        )

    conn = db.connect(request.app.state.db_path)
    try:
        file_row = db.get_file_row(
            conn, payload.path, imported.size, imported.modified_time
        )
        if file_row is None:
            return BadDataUpdateResult(
                status="error", error="No persisted data for this file"
            )

        row = db.update_bad_data_mark(
            conn, file_row["id"], payload.id, payload.start, payload.stop
        )
        if row is None:
            return BadDataUpdateResult(
                status="error", error="No bad-data mark with this id"
            )

        return BadDataUpdateResult(
            status="ok",
            mark=BadDataMark(id=row["id"], start=row["start"], stop=row["stop"]),
        )
    except Exception as e:
        return BadDataUpdateResult(status="error", error=str(e))
    finally:
        conn.close()


@router.delete("/bad-data", response_model=BadDataDeleteResult)
def delete_bad_data(
    payload: BadDataDeleteRequest, request: Request
) -> BadDataDeleteResult:
    imported = request.app.state.imported_files.get(payload.path)
    if imported is None or imported.status != "ok":
        return BadDataDeleteResult(
            status="error", error=f"File not imported: {payload.path}"
        )

    conn = db.connect(request.app.state.db_path)
    try:
        file_row = db.get_file_row(
            conn, payload.path, imported.size, imported.modified_time
        )
        if file_row is None:
            return BadDataDeleteResult(
                status="error", error="No persisted data for this file"
            )

        deleted = db.delete_bad_data_mark(conn, file_row["id"], payload.id)
        if not deleted:
            return BadDataDeleteResult(
                status="error", error="No bad-data mark with this id"
            )

        return BadDataDeleteResult(status="ok")
    except Exception as e:
        return BadDataDeleteResult(status="error", error=str(e))
    finally:
        conn.close()


def _annot_labels(beats_df: pd.DataFrame) -> np.ndarray:
    """The per-beat training label: 2 confirmed arrhythmia, -1 rejected, 1
    detected (any_arrhythmia) but not reviewed, 0 not detected and not
    reviewed. This is the scheme the report loader (backend/reports.py) reads
    back from an `annot_any_arrhythmia` column."""
    state = beats_df["review_state"]
    # NULL (arrhythmia detection never ran) reads as not detected.
    detected = pd.to_numeric(beats_df["any_arrhythmia"], errors="coerce").fillna(0) != 0
    return np.select(
        [state == "confirmed", state == "rejected", detected], [2, -1, 1], default=0
    )


# The detector flag columns in the order the original desktop app's report had
# them (arrhythmia_detection.call_arrhythmias: the heuristic categories, then
# the unsupervised one).
_ORIGINAL_CATEGORY_ORDER = [
    "bradycardia_absolute",
    "tachycardia_absolute",
    "skipped_beat",
    "prem_beat",
    "abn_cluster",
]
# This app's review columns, which the original report did not have: after
# the original ones.
_REVIEW_COLUMNS = ["review_state", "reassigned_category", "uncertain", "manual_added_at"]


def _detector_filled(beats_df: pd.DataFrame, column: str) -> bool:
    """Whether a detection method filled `column`. The original report only
    had a method's columns when that method had run; here a method that never
    ran leaves its column NULL. Reassigning a beat writes every category
    column (and so did rejecting one, in reviews saved by earlier versions),
    so beats the review left alone decide (all of them, if the review touched
    every beat)."""
    untouched = (beats_df["review_state"] != "rejected") & beats_df["reassigned_category"].isna()
    rows = beats_df.loc[untouched, column] if untouched.any() else beats_df[column]
    return bool(rows.notna().any())


def _flags(series: pd.Series) -> list:
    """A 0/1/NULL flag column as the True/False the original report wrote
    (blank where the beat was not evaluated)."""
    missing = series.isna().to_numpy()
    return [None if m else bool(v) for v, m in zip(series.tolist(), missing)]


def _original_beats_layout(beats_df: pd.DataFrame) -> pd.DataFrame:
    """The `beats` sheet with the original desktop app's columns, names, order
    and value types (its report was its whole beat table: beatcaller's ts / RR
    / R_amplitude / HR / beats, then call_arrhythmias' flags and their integer
    `annot_` copies), followed by this app's own review columns.

    `annot_any_arrhythmia` is the review label, as in the original: 2 confirmed
    arrhythmia, -1 rejected, 1 detected but not reviewed, 0 not detected. The
    other `annot_` columns are the flags as 0/1, as the original wrote them.
    `beats_df` is the saved table with `annot_any_arrhythmia` already added.
    """
    out = pd.DataFrame(
        {
            "ts": beats_df["ts"],
            "RR": beats_df["rr"],
            "R_amplitude": beats_df["r_amplitude"],
            "HR": beats_df["hr"],
            "beats": 1,
        }
    )
    flag_columns = [
        column for column in _ORIGINAL_CATEGORY_ORDER if _detector_filled(beats_df, column)
    ] + ["any_arrhythmia", "other_arrhythmia"]
    for column in flag_columns:
        out[column] = _flags(beats_df[column])
    for column in flag_columns:
        if column == "any_arrhythmia":
            out["annot_any_arrhythmia"] = beats_df["annot_any_arrhythmia"]
        else:
            out[f"annot_{column}"] = (
                pd.to_numeric(beats_df[column], errors="coerce").fillna(0).astype(int)
            )
    for column in _REVIEW_COLUMNS:
        out[column] = beats_df[column]
    return out


# The sample rate is estimated from this many samples at the start of the
# recording: a median needs no more, and diffing a multi-hour recording's whole
# time column would copy hundreds of MB for the same answer.
_RATE_SAMPLES = 1_000_000


def _or_blank(compute):
    """`compute()`, or None (an empty cell) when it fails: a metadata value
    that cannot be worked out must never fail the report."""
    try:
        return compute()
    except Exception:
        return None


def _finite(value, digits: int):
    """`value` rounded to `digits`, or None when it is not a finite number."""
    value = float(value)
    return round(value, digits) if np.isfinite(value) else None


def _local_iso(moment: datetime) -> str:
    """ISO 8601 in the machine's local time, with its UTC offset."""
    return moment.astimezone().isoformat(timespec="seconds")


def _marked_seconds(bad_data_df: pd.DataFrame) -> float:
    """Total time covered by the bad-data marks; overlapping marks count once."""
    total, covered_to = 0.0, -np.inf
    for start, stop in sorted(zip(bad_data_df["start"], bad_data_df["stop"])):
        total += max(0.0, stop - max(start, covered_to))
        covered_to = max(covered_to, stop)
    return total


def _analysis_library_version() -> str:
    """The physiology_analysis_tools version (what main.py declares). Reading
    it from the installed package's metadata avoids importing main.py, which
    pulls in the whole Qt UI; with no metadata (a frozen build) fall back to
    the module versions the settings sheet already records."""
    try:
        return importlib.metadata.version("physiology_analysis_tools")
    except importlib.metadata.PackageNotFoundError:
        return (
            f"heartbeat_detection {heartbeat_detection.__version__}, "
            f"arrhythmia_detection {arrhythmia_detection.__version__}, "
            f"ml_tools {ml_tools.__version__}"
        )


def _report_metadata(
    request: Request,
    path: str,
    imported,
    file_row,
    beats_df: pd.DataFrame,
    bad_data_df: pd.DataFrame,
) -> pd.DataFrame:
    """The report's `metadata` sheet: one row per fact about the recording and
    the review, as `field` / `value`. A value that cannot be worked out is
    left blank rather than failing the export."""
    cache_entry = request.app.state.signal_cache.get(path)
    time_values = _or_blank(
        lambda: cache_entry["df"][cache_entry["time_column"]].to_numpy(dtype=float)
    )

    def sample_rate_hz():
        steps = np.diff(time_values[:_RATE_SAMPLES])
        steps = steps[np.isfinite(steps) & (steps > 0)]
        return _finite(1.0 / np.median(steps), 3)

    state = beats_df["review_state"]
    labels = beats_df["annot_any_arrhythmia"]
    rows = [
        ("report_generated_at", _local_iso(datetime.now())),
        ("recording_file", imported.filename),
        ("recording_path", path),
        ("file_size_bytes", imported.size),
        (
            "file_modified_at",
            _or_blank(
                lambda: _local_iso(
                    datetime.fromtimestamp(imported.modified_time, tz=timezone.utc)
                )
            ),
        ),
        ("channel", _channel_display(imported, file_row["channel"])),
        (
            "channels_available",
            ", ".join(_channel_display(imported, c) for c in imported.channels),
        ),
        ("time_column", imported.time_column),
        ("sample_rate_hz", _or_blank(sample_rate_hz)),
        (
            "recording_duration_s",
            _or_blank(lambda: _finite(time_values[-1] - time_values[0], 3)),
        ),
        ("beats_total", len(beats_df)),
        ("beats_confirmed", int((state == "confirmed").sum())),
        ("beats_rejected", int((state == "rejected").sum())),
        ("beats_reviewed_no_decision", int((state == "reviewed").sum())),
        ("beats_unreviewed", int((state == "unreviewed").sum())),
        ("beats_detected_unreviewed", int((labels == 1).sum())),
        ("beats_uncertain", int(beats_df["uncertain"].sum())),
        ("beats_added_manually", int(beats_df["manual_added_at"].notna().sum())),
        ("mean_hr_bpm", _or_blank(lambda: _finite(beats_df["hr"].mean(), 2))),
        ("bad_data_marks", len(bad_data_df)),
        (
            "bad_data_seconds",
            _or_blank(lambda: _finite(_marked_seconds(bad_data_df), 3)),
        ),
        (
            "review_last_saved_at",
            _or_blank(
                lambda: _local_iso(datetime.fromisoformat(file_row["updated_at"]))
            ),
        ),
        ("analysis_library_version", _or_blank(_analysis_library_version)),
        (
            "annot_any_arrhythmia_legend",
            "2 confirmed arrhythmia, 1 detected not reviewed, 0 not detected, "
            "-1 rejected",
        ),
        (
            "review_state_legend",
            "unreviewed = not looked at, reviewed = looked at without a decision, "
            "confirmed = arrhythmia, rejected = not arrhythmia",
        ),
        (
            "reassigned_category_legend",
            "the categories the reviewer chose for the beat, comma-separated "
            "(blank: as detected); the category flag columns match them",
        ),
        (
            "uncertain_legend",
            "1 = reviewer marked unsure; the beat is still confirmed or rejected",
        ),
    ]
    return pd.DataFrame(rows, columns=["field", "value"])


@router.post("/report", response_model=ReportResult)
def generate_report(payload: ReportRequest, request: Request) -> ReportResult:
    imported = request.app.state.imported_files.get(payload.path)
    if imported is None or imported.status != "ok":
        return ReportResult(status="error", error=f"File not imported: {payload.path}")

    conn = db.connect(request.app.state.db_path)
    try:
        file_row = db.get_file_row(
            conn, payload.path, imported.size, imported.modified_time
        )
        if file_row is None or file_row["beat_settings_json"] is None:
            return ReportResult(
                status="error",
                error="No persisted data for this file — run POST /files/beats first",
            )

        output_dir = os.path.dirname(payload.output_path) or "."
        if not os.path.isdir(output_dir):
            return ReportResult(
                status="error",
                error=f"Output directory does not exist: {output_dir}",
            )

        beats_df = pd.read_sql_query(
            "SELECT * FROM beats WHERE file_id=? ORDER BY ts",
            conn,
            params=(file_row["id"],),
        ).drop(columns=["id", "file_id"])
        beats_df["annot_any_arrhythmia"] = _annot_labels(beats_df)

        mark_rows = conn.execute(
            "SELECT start, stop FROM bad_data_marks WHERE file_id=? ORDER BY id",
            (file_row["id"],),
        ).fetchall()
        bad_data_df = pd.DataFrame(
            [(row["start"], row["stop"]) for row in mark_rows],
            columns=["start", "stop"],
        )

        settings_df = pd.DataFrame(
            {
                **json.loads(file_row["beat_settings_json"]),
                **json.loads(file_row["arrhythmia_settings_json"]),
                # The original report's version of the app itself.
                "main_version": _or_blank(_analysis_library_version),
                "heartbeat_version": heartbeat_detection.__version__,
                "arrhythmia_version": arrhythmia_detection.__version__,
                "ml_version": ml_tools.__version__,
            },
            index=[0],
        )

        metadata_df = _report_metadata(
            request, payload.path, imported, file_row, beats_df, bad_data_df
        )

        # The technician's chosen filename wins as-is; only defensively
        # append .xlsx if they (or a non-Electron caller) omitted it — the
        # Save As dialog's own file-type filter normally prevents this.
        output_path = payload.output_path
        if not output_path.lower().endswith(".xlsx"):
            output_path += ".xlsx"

        try:
            writer = pd.ExcelWriter(output_path, engine="xlsxwriter")
            _original_beats_layout(beats_df).to_excel(
                writer, sheet_name="beats", index=False
            )
            bad_data_df.to_excel(writer, sheet_name="bad_data_marks", index=False)
            settings_df.to_excel(writer, sheet_name="settings", index=False)
            metadata_df.to_excel(writer, sheet_name="metadata", index=False)
            writer.close()
        except Exception as e:
            return ReportResult(status="error", error=str(e))

        return ReportResult(status="ok", output_path=output_path)
    except Exception as e:
        return ReportResult(status="error", error=str(e))
    finally:
        conn.close()
