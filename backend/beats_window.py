import pandas as pd
from fastapi import APIRouter, Request

from backend import categories
from backend.models import BeatWindowResult, WindowBeat

router = APIRouter(prefix="/beats", tags=["beats"])

OPTIONAL_COLUMNS = categories.ALL_OPTIONAL_COLUMNS


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
        # `review_state`/`reassigned_category` are only ever added to
        # beat_cache[path] by a review action or a reopen/re-persist sync
        # (see backend/db_routes.py) — absent (a never-reviewed file this
        # session) or NaN (a row that sync didn't have a value for) both
        # mean the beat itself is genuinely unreviewed.
        if "review_state" in df.columns:
            value = row.review_state
            kwargs["review_state"] = "unreviewed" if pd.isna(value) else value
        if "reassigned_category" in df.columns:
            value = row.reassigned_category
            kwargs["reassigned_category"] = None if pd.isna(value) else value
        beats.append(WindowBeat(**kwargs))

    return BeatWindowResult(status="ok", beats=beats, count=len(beats))
