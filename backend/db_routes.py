import json
import os

import pandas as pd
from fastapi import APIRouter, Request
from physiology_analysis_tools.modules import (
    arrhythmia_detection,
    heartbeat_detection,
    ml_tools,
)

from backend import beat_editing, categories, db
from backend.locks import path_lock
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
    CategoryUpdateRequest,
    CategoryUpdateResult,
    ChannelPersistRequest,
    ChannelPersistResult,
    DiscardStateRequest,
    DiscardStateResult,
    FileStateResult,
    PersistBeatsRequest,
    PersistBeatsResult,
    PersistedBeat,
    ReportRequest,
    ReportResult,
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
            return FileStateResult(status="ok", found=False)

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
                        cache_df = pd.DataFrame(
                            {
                                "ts": [beat_row["ts"] for beat_row in beat_rows],
                                "RR": [beat_row["rr"] for beat_row in beat_rows],
                                "R_amplitude": [
                                    beat_row["r_amplitude"] for beat_row in beat_rows
                                ],
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
                        cache_df["review_state"] = [
                            beat_row["review_state"] for beat_row in beat_rows
                        ]
                        cache_df["reassigned_category"] = [
                            beat_row["reassigned_category"] for beat_row in beat_rows
                        ]
                        request.app.state.beat_cache[path] = cache_df
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

        return FileStateResult(
            status="ok",
            found=True,
            channel=row["channel"],
            beats=beats,
            bad_data_marks=bad_data_marks,
            beat_settings=beat_settings,
            arrhythmia_settings=arrhythmia_settings,
        )
    except Exception as e:
        return FileStateResult(status="error", error=str(e))
    finally:
        conn.close()


def _to_bool(value: int | None) -> bool | None:
    return None if value is None else bool(value)


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

    conn = db.connect(request.app.state.db_path)
    try:
        file_id = db.upsert_file(
            conn,
            payload.path,
            imported.size,
            imported.modified_time,
            channel=payload.channel,
            beat_settings_json=json.dumps(request.app.state.beat_settings.__dict__),
            arrhythmia_settings_json=json.dumps(
                request.app.state.arrhythmia_settings.__dict__
            ),
            commit=False,
        )
        preserved_state = db.replace_beats(conn, file_id, beat_df, commit=False)
        conn.commit()

        # Sync review_state/reassigned_category onto the beat_cache[path]
        # DataFrame `/beats/window` reads from — replace_beats above only
        # preserved them in SQLite; without this, a beat reviewed before this
        # redetect+persist would show as "unreviewed" on the graph until
        # individually touched again. Every row gets a value (defaulting to
        # unreviewed/None), matched by `ts`. This builds a NEW frame and swaps
        # it in rather than adding the columns to the cached one: window
        # requests iterate that frame while this runs, and columns appearing
        # under them mid-iteration made them fail.
        request.app.state.beat_cache[payload.path] = db.with_review_state(
            beat_df, preserved_state
        )

        return PersistBeatsResult(status="ok", count=len(beat_df))
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
    if payload.action not in {"confirm", "reject", "reassign", "remove_flag"}:
        return CategoryUpdateResult(
            status="error",
            error=f"Unknown action: {payload.action!r}",
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

        updated = db.update_beat_category(
            conn, file_row["id"], payload.ts, payload.action, payload.category
        )
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
                request.app.state.beat_cache[payload.path] = new_df

        return CategoryUpdateResult(
            status="ok",
            ts=updated["ts"],
            review_state=updated["review_state"],
            reassigned_category=updated["reassigned_category"],
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
        if file_row is not None:
            db.delete_file_state(conn, file_row["id"])

        request.app.state.beat_cache.pop(payload.path, None)

        # Nothing persisted in the first place is a successful no-op, not an
        # error: the caller's intent ("leave me with a clean slate") holds.
        return DiscardStateResult(status="ok")
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
                "heartbeat_version": heartbeat_detection.__version__,
                "arrhythmia_version": arrhythmia_detection.__version__,
                "ml_version": ml_tools.__version__,
            },
            index=[0],
        )

        # The technician's chosen filename wins as-is; only defensively
        # append .xlsx if they (or a non-Electron caller) omitted it — the
        # Save As dialog's own file-type filter normally prevents this.
        output_path = payload.output_path
        if not output_path.lower().endswith(".xlsx"):
            output_path += ".xlsx"

        try:
            writer = pd.ExcelWriter(output_path, engine="xlsxwriter")
            beats_df.to_excel(writer, sheet_name="beats", index=False)
            bad_data_df.to_excel(writer, sheet_name="bad_data_marks", index=False)
            settings_df.to_excel(writer, sheet_name="settings", index=False)
            writer.close()
        except Exception as e:
            return ReportResult(status="error", error=str(e))

        return ReportResult(status="ok", output_path=output_path)
    except Exception as e:
        return ReportResult(status="error", error=str(e))
    finally:
        conn.close()
