import pandas as pd
from fastapi import APIRouter, Request

from backend.models import BeatWindowResult, WindowBeat

router = APIRouter(prefix="/beats", tags=["beats"])

OPTIONAL_COLUMNS = [
    "bradycardia_absolute",
    "tachycardia_absolute",
    "skipped_beat",
    "prem_beat",
    "abn_cluster",
    "any_arrhythmia",
    "other_arrhythmia",
]


@router.get("/window", response_model=BeatWindowResult)
def get_beat_window(
    path: str, start: float, end: float, request: Request
) -> BeatWindowResult:
    df = request.app.state.beat_cache.get(path)
    if df is None:
        return BeatWindowResult(
            status="error", error="Beat detection has not been run for this file yet"
        )

    filtered = df[(df["ts"] >= start) & (df["ts"] <= end)]

    beats = []
    for row in filtered.itertuples():
        kwargs = {
            "ts": row.ts,
            "rr": row.RR,
            "r_amplitude": row.R_amplitude,
            "hr": row.HR,
        }
        for col in OPTIONAL_COLUMNS:
            if col in df.columns:
                value = getattr(row, col)
                kwargs[col] = None if pd.isna(value) else bool(value)
        beats.append(WindowBeat(**kwargs))

    return BeatWindowResult(status="ok", beats=beats, count=len(beats))
