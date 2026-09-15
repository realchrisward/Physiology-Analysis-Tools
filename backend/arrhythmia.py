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
            status="error", error="Signal data is not available for this file"
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

    optional_columns = [
        "bradycardia_absolute",
        "tachycardia_absolute",
        "skipped_beat",
        "prem_beat",
        "abn_cluster",
    ]
    present_optional_columns = [c for c in optional_columns if c in df.columns]
    if present_optional_columns:
        # any_arrhythmia is computed internally by call_arrhythmias() as
        # df[categories].any(axis=1, bool_only=True). If any optional
        # category column ends up with even one real NaN mixed in with real
        # booleans (e.g. abn_cluster for a beat too close to the signal's
        # start/end for ml_tools.beatepocher()'s epoch window), pandas gives
        # that column dtype=object, and bool_only=True silently drops the
        # ENTIRE column from the aggregation rather than just the NaN rows.
        # Recompute defensively, OR'd with the library's own value, so this
        # can only make any_arrhythmia MORE true, never mask a category the
        # library already reported via a fully-populated column.
        df["any_arrhythmia"] = df["any_arrhythmia"] | df[
            present_optional_columns
        ].fillna(False).astype(bool).any(axis=1)

    request.app.state.beat_cache[payload.path] = df

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
                # NaN can still occur for individual boundary-skipped beats
                # even after the index-reset fix (e.g. a beat too close to
                # the signal's start/end for the unsupervised method's
                # epoch window). Treat NaN as "not evaluated" (None) rather
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
