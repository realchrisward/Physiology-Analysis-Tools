"""Manual beat editing: adding a beat the detector missed.

Adding is a two-step flow so the technician can check the result before it
is saved: `/files/beats/snap` suggests the peak nearest a rough click (no
side effects), and `/files/beats/one` (POST) inserts a beat at an exact
time. Deleting lives in db_routes.py; the RR/HR fix-up both need is here.
"""

import numpy as np
import pandas as pd
import scipy.signal
from fastapi import APIRouter, Request

from backend import db
from backend.models import (
    BeatAddRequest,
    BeatAddResult,
    BeatSnapRequest,
    BeatSnapResult,
    WindowBeat,
)
from backend.windowing import get_filtered_channel

router = APIRouter(prefix="/files", tags=["beat-editing"])

# How far either side of a rough click the snap looks for a peak: the
# detector's minimum beat spacing, and never less than 50 ms. Kept tight so a
# fast heart rate (beats ~130 ms apart) cannot pull the snap onto the wrong
# beat; the technician can zoom in and place the beat exactly if it misses.
_MIN_HALF_WINDOW_S = 0.05


def apply_manual_edits(df: pd.DataFrame, edits, min_gap: float) -> pd.DataFrame:
    """Re-apply a file's hand-made beat edits to a fresh detection result.

    Removals drop any detected beat within `min_gap` seconds of the removed
    one (detection can land a sample or two off if settings changed).
    Additions are put back with their original timestamp and time of entry,
    unless detection now found a beat there itself. RR/HR are then redone
    for every beat but the first, whose predecessor is not in the table.
    """
    if not edits:
        return df
    df = df.copy()
    ts_values = df["ts"].to_numpy(dtype=float)

    keep = np.ones(len(df), dtype=bool)
    for edit in edits:
        if edit["kind"] == "delete":
            keep &= np.abs(ts_values - edit["ts"]) >= min_gap
    df = df[keep].reset_index(drop=True)
    ts_values = df["ts"].to_numpy(dtype=float)

    if "manual_added_at" not in df.columns:
        df["manual_added_at"] = None
    new_rows = []
    for edit in edits:
        if edit["kind"] != "add":
            continue
        if len(ts_values) and np.any(np.abs(ts_values - edit["ts"]) < min_gap):
            continue
        row = {column: None for column in df.columns}
        row.update(
            ts=edit["ts"],
            R_amplitude=edit["r_amplitude"],
            manual_added_at=edit["created_at"],
        )
        if "beats" in row:
            row["beats"] = 1
        if "review_state" in row:
            row["review_state"] = "unreviewed"
        new_rows.append(row)
    if new_rows:
        df = (
            pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
            .sort_values("ts")
            .reset_index(drop=True)
        )

    ts_values = df["ts"].to_numpy(dtype=float)
    if len(ts_values) >= 2:
        rr = df["RR"].to_numpy(dtype=float).copy()
        rr[1:] = np.diff(ts_values)
        if np.isnan(rr[0]):
            rr[0] = ts_values[1] - ts_values[0]
        df["RR"] = rr
        df["HR"] = 60.0 / rr
    return df


def rr_hr_for_successor(
    df: pd.DataFrame, removed_ts: float
) -> tuple[float, float, float] | None:
    """After a beat at `removed_ts` was dropped from `df`, the beat that now
    follows the gap needs its RR (interval from the previous beat) redone.

    Returns (ts, rr, hr) for that beat, or None when there is nothing to fix
    (the removed beat was first or last: the new first beat keeps the RR it
    was detected with, since the beat before it is not in the table).
    """
    ts_values = df["ts"].to_numpy(dtype=float)
    k = int(np.searchsorted(ts_values, removed_ts))
    if k == 0 or k >= len(ts_values):
        return None
    rr = float(ts_values[k] - ts_values[k - 1])
    return float(ts_values[k]), rr, 60.0 / rr


def _detection_signal(request: Request, path: str, channel: str, cache_entry: dict):
    """(time, values) of the trace beat detection looked at: highpass
    filtered when the beat settings filter, raw otherwise."""
    settings = request.app.state.beat_settings
    df = cache_entry["df"]
    time = df[cache_entry["time_column"]].to_numpy(dtype=float)
    if settings.ecg_filter:
        values = get_filtered_channel(request, path, channel, cache_entry)
    else:
        values = df[channel]
    return time, values.to_numpy(dtype=float)


def _polarity(time, values, beat_ts, settings) -> float:
    """+1 when R peaks point up in `values`, -1 when they point down.

    Read from where the existing beats sit, which is right whatever mix of
    manual/auto invert produced them; the setting is only a fallback when
    there are too few beats to tell.
    """
    if len(beat_ts) >= 3:
        idx = np.clip(np.searchsorted(time, beat_ts), 0, len(time) - 1)
        return -1.0 if np.median(values[idx]) < np.median(values) else 1.0
    return -1.0 if settings.ecg_invert else 1.0


def _snap_index(
    time, values, sign: float, ts: float, half_window: float, beat_ts, min_gap: float
) -> int | None:
    """Index of the peak nearest `ts`, ignoring samples that sit on an
    existing beat (snapping onto a beat that is already there is never the
    intent). None if the window holds no candidate."""
    lo = int(np.searchsorted(time, ts - half_window, side="left"))
    hi = int(np.searchsorted(time, ts + half_window, side="right"))
    if hi - lo < 1:
        return None
    segment = values[lo:hi] * sign
    taken = np.zeros(len(segment), dtype=bool)
    for existing in beat_ts[np.abs(beat_ts - ts) < half_window + min_gap]:
        taken |= np.abs(time[lo:hi] - existing) < min_gap
    if taken.all():
        return None
    segment = np.where(taken, segment[~taken].min() - 1.0, segment)
    peaks, _ = scipy.signal.find_peaks(segment)
    if len(peaks) > 0:
        return lo + int(peaks[np.argmax(segment[peaks])])
    # No interior peak (the window is all slope): the highest sample is the
    # best available answer.
    return lo + int(np.argmax(segment))


def _prepare(request: Request, payload):
    """Shared validation for snap and add. Returns (context, error)."""
    imported = request.app.state.imported_files.get(payload.path)
    if imported is None or imported.status != "ok":
        return None, f"File not imported: {payload.path}"
    cache_entry = request.app.state.signal_cache.get(payload.path)
    if cache_entry is None or payload.channel not in cache_entry["df"].columns:
        return None, f"Channel not available: {payload.channel}"
    beat_df = request.app.state.beat_cache.get(payload.path)
    if beat_df is None or len(beat_df) < 2:
        return None, "Run beat detection on this file first"

    settings = request.app.state.beat_settings
    time, values = _detection_signal(request, payload.path, payload.channel, cache_entry)
    beat_ts = beat_df["ts"].to_numpy(dtype=float)
    sign = _polarity(time, values, beat_ts, settings)
    min_gap = settings.min_RR / 1000.0
    return {
        "imported": imported,
        "beat_df": beat_df,
        "settings": settings,
        "time": time,
        "values": values,
        "beat_ts": beat_ts,
        "sign": sign,
        "min_gap": min_gap,
    }, None


def _existing_beat_near(ctx, ts: float) -> float | None:
    gaps = np.abs(ctx["beat_ts"] - ts)
    nearest = int(np.argmin(gaps))
    if gaps[nearest] < ctx["min_gap"]:
        return float(ctx["beat_ts"][nearest])
    return None


@router.post("/beats/snap", response_model=BeatSnapResult)
def snap_beat(payload: BeatSnapRequest, request: Request) -> BeatSnapResult:
    """Suggest where a beat added near `ts` belongs: the peak nearest a rough
    click. Read-only."""
    ctx, error = _prepare(request, payload)
    if error:
        return BeatSnapResult(status="error", error=error)

    half_window = max(ctx["min_gap"], _MIN_HALF_WINDOW_S)
    idx = _snap_index(
        ctx["time"], ctx["values"], ctx["sign"], payload.ts, half_window,
        ctx["beat_ts"], ctx["min_gap"],
    )
    if idx is None:
        existing = _existing_beat_near(ctx, payload.ts)
        if existing is not None:
            return BeatSnapResult(
                status="error", error=f"There is already a beat here (t={existing:.3f}s)"
            )
        return BeatSnapResult(status="error", error="No signal near this position")

    return BeatSnapResult(status="ok", ts=float(ctx["time"][idx]))


@router.post("/beats/one", response_model=BeatAddResult)
def add_beat(payload: BeatAddRequest, request: Request) -> BeatAddResult:
    """Insert a beat at the sample nearest `ts`. Saved to SQLite and to the
    in-memory beat cache; the neighbouring beat's RR/HR are recomputed."""
    ctx, error = _prepare(request, payload)
    if error:
        return BeatAddResult(status="error", error=error)

    time, values = ctx["time"], ctx["values"]
    if payload.ts < time[0] or payload.ts > time[-1]:
        return BeatAddResult(status="error", error="Position is outside the recording")
    idx = int(np.clip(np.searchsorted(time, payload.ts), 1, len(time) - 1))
    if abs(time[idx - 1] - payload.ts) <= abs(time[idx] - payload.ts):
        idx -= 1
    new_ts = float(time[idx])

    existing = _existing_beat_near(ctx, new_ts)
    if existing is not None:
        return BeatAddResult(
            status="error", error=f"There is already a beat here (t={existing:.3f}s)"
        )

    imported = ctx["imported"]
    conn = db.connect(request.app.state.db_path)
    try:
        file_row = db.get_file_row(
            conn, payload.path, imported.size, imported.modified_time
        )
        if file_row is None:
            return BeatAddResult(
                status="error",
                error="No persisted data for this file - run POST /files/beats first",
            )

        beat_df: pd.DataFrame = ctx["beat_df"]
        ts_after = np.sort(np.append(ctx["beat_ts"], new_ts))
        k = int(np.searchsorted(ts_after, new_ts))
        # The first beat has no predecessor in the table, so its RR is the
        # gap to the beat after it; every other beat's RR is the gap back.
        rr = float(ts_after[1] - ts_after[0]) if k == 0 else float(ts_after[k] - ts_after[k - 1])
        hr = 60.0 / rr
        r_amplitude = float(values[idx] * ctx["sign"])

        added_at = db.record_manual_add(
            conn, file_row["id"], new_ts, r_amplitude, ctx["min_gap"]
        )
        db.insert_beat(conn, file_row["id"], new_ts, rr, r_amplitude, hr, added_at)

        # The beat after the new one now measures its RR from it.
        updates = {new_ts: (rr, hr)}
        if k + 1 < len(ts_after):
            successor_ts = float(ts_after[k + 1])
            successor_rr = successor_ts - new_ts
            updates[successor_ts] = (successor_rr, 60.0 / successor_rr)
            db.update_beat_rr_hr(
                conn, file_row["id"], successor_ts, successor_rr, 60.0 / successor_rr
            )

        if "manual_added_at" not in beat_df.columns:
            beat_df = beat_df.assign(manual_added_at=None)
        row = {column: None for column in beat_df.columns}
        row.update(
            ts=new_ts, RR=rr, R_amplitude=r_amplitude, HR=hr, manual_added_at=added_at
        )
        if "beats" in row:
            row["beats"] = 1
        if "review_state" in row:
            row["review_state"] = "unreviewed"
        new_df = (
            pd.concat([beat_df, pd.DataFrame([row])], ignore_index=True)
            .sort_values("ts")
            .reset_index(drop=True)
        )
        for ts_key, (row_rr, row_hr) in updates.items():
            mask = new_df["ts"] == ts_key
            new_df.loc[mask, "RR"] = row_rr
            new_df.loc[mask, "HR"] = row_hr
        request.app.state.beat_cache[payload.path] = new_df

        return BeatAddResult(
            status="ok",
            beat=WindowBeat(
                ts=new_ts,
                rr=rr,
                r_amplitude=r_amplitude,
                hr=hr,
                manual_added_at=added_at,
            ),
        )
    except Exception as e:
        return BeatAddResult(status="error", error=str(e))
    finally:
        conn.close()
