import pandas as pd
from fastapi import APIRouter, Request

from backend import categories
from backend.models import BeatsOfInterestResult, BeatWindowResult, WindowBeat

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
        if "manual_added_at" in df.columns:
            value = row.manual_added_at
            kwargs["manual_added_at"] = None if pd.isna(value) else str(value)
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


@router.get("/of-interest", response_model=BeatsOfInterestResult)
def get_beats_of_interest(path: str, request: Request) -> BeatsOfInterestResult:
    """Every beat currently flagged as an arrhythmia, by timestamp.

    A beat the technician rejected has `any_arrhythmia` cleared by the review
    action itself, so it drops out of this list automatically — reviewing a
    beat away genuinely removes it from the navigation.
    """
    df = request.app.state.beat_cache.get(path)
    if df is None:
        return BeatsOfInterestResult(
            status="error", error="Beat detection has not been run for this file yet"
        )

    if "any_arrhythmia" not in df.columns:
        # Beat detection has run but arrhythmia detection hasn't — no flags
        # exist yet, which is an empty result, not an error.
        return BeatsOfInterestResult(status="ok", ts=[], count=0)

    flagged = df[df["any_arrhythmia"].fillna(False).astype(bool)]
    timestamps = sorted(float(ts) for ts in flagged["ts"])

    return BeatsOfInterestResult(
        status="ok", ts=timestamps, count=len(timestamps)
    )
