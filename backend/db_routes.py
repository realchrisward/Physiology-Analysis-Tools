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
                        cache_df["uncertain"] = [
                            bool(beat_row["uncertain"]) for beat_row in beat_rows
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
    if payload.action not in {
        "confirm",
        "reject",
        "reassign",
        "remove_flag",
        "set_uncertain",
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
            )
        except db.UncertainNeedsDecisionError as e:
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


def _review_columns_together(beats_df: pd.DataFrame) -> pd.DataFrame:
    """Move `uncertain` and `annot_any_arrhythmia` to sit right after
    `reassigned_category`, so the review outcome reads as one block (the table
    itself keeps `manual_added_at` and `uncertain` at its end)."""
    moved = ["uncertain", "annot_any_arrhythmia"]
    others = [col for col in beats_df.columns if col not in moved]
    at = others.index("reassigned_category") + 1
    return beats_df[others[:at] + moved + others[at:]]


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
        ("channel", file_row["channel"]),
        ("channels_available", ", ".join(imported.channels)),
        ("time_column", imported.time_column),
        ("sample_rate_hz", _or_blank(sample_rate_hz)),
        (
            "recording_duration_s",
            _or_blank(lambda: _finite(time_values[-1] - time_values[0], 3)),
        ),
        ("beats_total", len(beats_df)),
        ("beats_confirmed", int((state == "confirmed").sum())),
        ("beats_rejected", int((state == "rejected").sum())),
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
        beats_df = _review_columns_together(beats_df)

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
            beats_df.to_excel(writer, sheet_name="beats", index=False)
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
