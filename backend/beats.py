import time

from fastapi import APIRouter, Request
from physiology_analysis_tools.modules import heartbeat_detection

from backend import beat_editing, db
from backend.locks import path_lock
from backend.models import Beat, BeatDetectionResult, BeatDetectRequest

router = APIRouter(prefix="/beats", tags=["beats"])


@router.post("/detect", response_model=BeatDetectionResult)
def detect_beats(payload: BeatDetectRequest, request: Request) -> BeatDetectionResult:
    cache_entry = request.app.state.signal_cache.get(payload.path)
    if cache_entry is None:
        return BeatDetectionResult(
            status="error", error=f"File not imported: {payload.path}"
        )

    # perf_counter, not monotonic: monotonic ticks at ~15 ms on Windows, so a
    # fast detection could report exactly 0.
    start = time.perf_counter()
    try:
        df = heartbeat_detection.beatcaller(
            cache_entry["df"],
            time_column=cache_entry["time_column"],
            voltage_column=payload.channel,
            **request.app.state.beat_settings.__dict__,
        ).reset_index(drop=True)
    except Exception as e:
        return BeatDetectionResult(
            status="error", error=str(e), elapsed_seconds=time.perf_counter() - start
        )

    # The detection above runs outside the path's lock (it takes seconds and
    # nothing else needs to wait for it). Everything from here to the cache
    # write is the read-modify-write: the technician's edits and reviews made
    # while it ran live in SQLite, so they are read and applied under the lock
    # and the result swapped in as one step, with no edit able to land in
    # between and be overwritten.
    with path_lock(payload.path):
        # The file may have been evicted (it left the session) while detecting;
        # storing the result would resurrect a frame nothing will ever free.
        if payload.path not in request.app.state.signal_cache:
            return BeatDetectionResult(
                status="error",
                error=f"File not imported: {payload.path}",
                elapsed_seconds=time.perf_counter() - start,
            )

        # A re-detect must not throw away what the technician did by hand: put
        # their added beats back and keep their removed beats removed, and keep
        # the review decisions (confirmed/rejected/reassigned) saved for beats
        # that are still there.
        imported = request.app.state.imported_files.get(payload.path)
        if imported is not None:
            try:
                conn = db.connect(request.app.state.db_path)
                try:
                    file_row = db.get_file_row(
                        conn, payload.path, imported.size, imported.modified_time
                    )
                    if file_row is not None:
                        df = beat_editing.apply_manual_edits(
                            df,
                            db.list_manual_edits(conn, file_row["id"]),
                            request.app.state.beat_settings.min_RR / 1000.0,
                        )
                        # Only when the saved record is for THIS channel: review
                        # decisions belong to the beats of the channel they
                        # were made on, and a different channel's beats are not
                        # the same beats even where a timestamp coincides.
                        if file_row["channel"] == payload.channel:
                            df = db.with_review_state(
                                df, db.load_review_state(conn, file_row["id"])
                            )
                finally:
                    conn.close()
            except Exception as e:
                return BeatDetectionResult(
                    status="error",
                    error=f"Could not re-apply manual beat edits: {e}",
                    elapsed_seconds=time.perf_counter() - start,
                )

        elapsed_seconds = time.perf_counter() - start
        request.app.state.beat_cache[payload.path] = df

    # Assumes beatcaller()'s output columns (ts, RR, R_amplitude, HR) — if that
    # shape ever changes, update this loop too, since it's outside the try/except above.
    beats = [
        Beat(
            ts=row.ts,
            rr=row.RR,
            r_amplitude=row.R_amplitude,
            hr=row.HR,
        )
        for row in df.itertuples()
    ]
    count = len(beats)
    mean_hr = float(df["HR"].mean()) if count > 0 else None
    duration = float(df["ts"].iloc[-1] - df["ts"].iloc[0]) if count > 0 else None

    imported = request.app.state.imported_files.get(payload.path)
    file_size_bytes = imported.size if imported is not None else None

    return BeatDetectionResult(
        status="ok",
        beats=beats,
        count=count,
        mean_hr=mean_hr,
        duration=duration,
        elapsed_seconds=elapsed_seconds,
        file_size_bytes=file_size_bytes,
    )
