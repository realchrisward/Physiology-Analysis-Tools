import time

from fastapi import APIRouter, Request
from physiology_analysis_tools.modules import heartbeat_detection

from backend.models import Beat, BeatDetectionResult, BeatDetectRequest

router = APIRouter(prefix="/beats", tags=["beats"])


@router.post("/detect", response_model=BeatDetectionResult)
def detect_beats(payload: BeatDetectRequest, request: Request) -> BeatDetectionResult:
    cache_entry = request.app.state.signal_cache.get(payload.path)
    if cache_entry is None:
        return BeatDetectionResult(
            status="error", error=f"File not imported: {payload.path}"
        )

    start = time.monotonic()
    try:
        df = heartbeat_detection.beatcaller(
            cache_entry["df"],
            time_column=cache_entry["time_column"],
            voltage_column=payload.channel,
            **request.app.state.beat_settings.__dict__,
        )
    except Exception as e:
        return BeatDetectionResult(
            status="error", error=str(e), elapsed_seconds=time.monotonic() - start
        )

    elapsed_seconds = time.monotonic() - start
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
