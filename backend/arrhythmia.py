import time

import pandas as pd
from fastapi import APIRouter, Request
from physiology_analysis_tools.modules import arrhythmia_detection

from backend.models import (
    ArrhythmiaBeat,
    ArrhythmiaDetectionResult,
    ArrhythmiaDetectRequest,
)

router = APIRouter(prefix="/arrhythmia", tags=["arrhythmia"])

METHOD_MAP = {
    "heuristic": "Heuristic",
    "unsupervised": "Unsupervised",
    "both": "Both",
}


@router.post("/detect", response_model=ArrhythmiaDetectionResult)
def detect_arrhythmias(
    payload: ArrhythmiaDetectRequest, request: Request
) -> ArrhythmiaDetectionResult:
    mapped_method = METHOD_MAP.get(payload.method)
    if mapped_method is None:
        return ArrhythmiaDetectionResult(
            status="error", error=f"Unrecognized method: {payload.method}"
        )

    beat_df = request.app.state.beat_cache.get(payload.path)
    if beat_df is None:
        return ArrhythmiaDetectionResult(
            status="error", error="Beat detection has not been run for this file yet"
        )

    cache_entry = request.app.state.signal_cache.get(payload.path)
    if cache_entry is None:
        return ArrhythmiaDetectionResult(
            status="error", error="Beat detection has not been run for this file yet"
        )

    start = time.monotonic()
    try:
        df = arrhythmia_detection.call_arrhythmias(
            beat_df.copy(),
            request.app.state.arrhythmia_settings,
            signals=cache_entry["df"],
            selected_signal=payload.channel,
            selected_time=cache_entry["time_column"],
            arr_methods=mapped_method,
        )
    except Exception as e:
        return ArrhythmiaDetectionResult(
            status="error", error=str(e), elapsed_seconds=time.monotonic() - start
        )

    elapsed_seconds = time.monotonic() - start
    request.app.state.beat_cache[payload.path] = df

    optional_columns = [
        "bradycardia_absolute",
        "tachycardia_absolute",
        "skipped_beat",
        "prem_beat",
        "abn_cluster",
    ]
    beats = []
    for row in df.itertuples():
        kwargs = {
            "ts": row.ts,
            "any_arrhythmia": row.any_arrhythmia,
            "other_arrhythmia": row.other_arrhythmia,
        }
        for col in optional_columns:
            if col in df.columns:
                value = getattr(row, col)
                # abn_cluster in particular can come back NaN: the
                # unsupervised method's clustering result is joined onto
                # beat_df by a positional 0..N-1 index that doesn't line up
                # with beat_cache's DataFrame index (which keeps beatcaller's
                # original sample-position index), so the join leaves every
                # row unmatched. Treat NaN as "not evaluated" (None) rather
                # than erroring on a non-boolean value.
                kwargs[col] = None if pd.isna(value) else bool(value)
        beats.append(ArrhythmiaBeat(**kwargs))

    count = len(beats)
    any_arrhythmia_count = int(df["any_arrhythmia"].sum())

    return ArrhythmiaDetectionResult(
        status="ok",
        beats=beats,
        count=count,
        any_arrhythmia_count=any_arrhythmia_count,
        elapsed_seconds=elapsed_seconds,
    )
