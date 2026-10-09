import threading

import numpy
import pandas
from fastapi import APIRouter, Request
from physiology_analysis_tools.modules import heartbeat_detection

from backend.models import ChannelWindowResult

router = APIRouter(prefix="/channels", tags=["channels"])

# Every distinct zoom/pan is a new window_cache key (~450 KB of result), so an
# uncapped cache grows for as long as the app is open. Oldest-inserted entries
# go first; a re-request just recomputes.
WINDOW_CACHE_MAX_ENTRIES = 256
_window_cache_write_lock = threading.Lock()


def min_max_downsample(x_val, y_val, n_bins):
    """
    Downsample (x_val, y_val) to roughly 2 * n_bins points by splitting the
    series into n_bins equal-sized bins and keeping both the min and max
    y-sample (in original chronological order) from each bin.

    Unlike stride/decimation downsampling (which just keeps every Nth
    sample), this preserves transient spikes - e.g. arrhythmic beats - that
    would otherwise be skipped over entirely when zoomed out.

    Ported from src/physiology_analysis_tools/main.py's min_max_downsample()
    - same algorithm, same bin-edge math, decoupled from pyqtgraph.
    """
    x_arr = numpy.asarray(x_val)
    y_arr = numpy.asarray(y_val)
    n = len(x_arr)

    if n_bins <= 0:
        return [], []

    bin_edges = numpy.linspace(0, n, n_bins + 1).astype(int)

    x_out = []
    y_out = []
    for i in range(n_bins):
        start, stop = bin_edges[i], bin_edges[i + 1]
        if start >= stop:
            continue
        bin_x = x_arr[start:stop]
        bin_y = y_arr[start:stop]
        min_idx = int(numpy.argmin(bin_y))
        max_idx = int(numpy.argmax(bin_y))
        # preserve chronological order of the min/max pair within the bin
        if min_idx > max_idx:
            min_idx, max_idx = max_idx, min_idx
        x_out.append(bin_x[min_idx])
        y_out.append(bin_y[min_idx])
        if max_idx != min_idx:
            x_out.append(bin_x[max_idx])
            y_out.append(bin_y[max_idx])

    return x_out, y_out


def window_channel_data(df, time_column, signal_column, start, end, resolution):
    """
    Filter df to [start, end] (inclusive) on time_column, then downsample
    the signal_column via min_max_downsample() if the filtered range is
    large relative to resolution.

    Ported from src/physiology_analysis_tools/main.py's gather_data() -
    same filtering and downsampling decision. The old app's filt_column
    display toggle is served by get_filtered_channel() below instead of by
    a second column on the same frame.
    """
    data_filter = (df[time_column] >= start) & (df[time_column] <= end)
    x_val = df[time_column][data_filter]
    y_val = df[signal_column][data_filter]

    if resolution > 0 and len(x_val) > resolution * 4:
        x_val, y_val = min_max_downsample(x_val, y_val, n_bins=resolution * 2)
        downsampled = True
    else:
        x_val = list(x_val)
        y_val = list(y_val)
        downsampled = False

    return x_val, y_val, downsampled


def get_filtered_channel(request, path, channel, cache_entry):
    """The channel with the same highpass filter beat detection applies,
    memoized per (path, channel, order, cutoff).

    Filtering the WHOLE channel (rather than each requested window) is what
    makes the displayed trace match the signal beat detection actually ran
    against, and avoids filter edge artefacts appearing at the boundaries of
    whatever range the user happens to have panned to.
    """
    settings = request.app.state.beat_settings
    order = settings.ecg_filt_order
    cutoff = settings.ecg_filt_cutoff

    cache_key = (path, channel, order, cutoff)
    cache = request.app.state.filtered_signal_cache
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    df = cache_entry["df"]
    time_values = df[cache_entry["time_column"]]
    sample_rate = 1 / (time_values.iloc[1] - time_values.iloc[0])

    filtered = heartbeat_detection.basic_filter(
        order, df[channel], fs=sample_rate, cutoff=cutoff, use_pandas=False
    )
    # basic_filter returns a bare array; re-attach the frame's own index so
    # the windowing filter below aligns row-for-row with the time column.
    series = pandas.Series(filtered, index=df.index)
    # Don't resurrect an entry for a file that was evicted or re-imported
    # while this filter ran; the next request recomputes against the live data.
    if request.app.state.signal_cache.get(path) is cache_entry:
        # Changing the filter settings leaves this channel's copies for the
        # old order/cutoff unreachable (each is a whole-channel array), so
        # drop them rather than let them pile up.
        for key in list(cache):
            if key[0] == path and key[1] == channel and key != cache_key:
                cache.pop(key, None)
        cache[cache_key] = series
    return series


def remember_window(window_cache, cache_key, result) -> None:
    """Memoize a window result, keeping the cache under its size cap.

    Lookups stay lock-free (`.get`); only inserts and the trim are serialized
    so two inserting threads don't both trim the same overshoot.
    """
    with _window_cache_write_lock:
        window_cache[cache_key] = result
        while len(window_cache) > WINDOW_CACHE_MAX_ENTRIES:
            try:
                oldest = next(iter(window_cache))
            except (StopIteration, RuntimeError):
                # Emptied, or a concurrent eviction resized the dict under the
                # iterator: the next insert trims again.
                break
            window_cache.pop(oldest, None)


@router.get("/window", response_model=ChannelWindowResult)
def get_channel_window(
    path: str,
    channel: str,
    start: float,
    end: float,
    resolution: int,
    request: Request,
    filtered: bool = False,
) -> ChannelWindowResult:
    # The filter parameters are part of the key, not just the `filtered`
    # flag: editing the beat-detection filter settings must not keep serving
    # a window that was filtered with the previous cutoff.
    beat_settings = request.app.state.beat_settings
    filter_params = (
        (beat_settings.ecg_filt_order, beat_settings.ecg_filt_cutoff)
        if filtered
        else None
    )
    cache_key = (path, channel, start, end, resolution, filter_params)
    window_cache = request.app.state.window_cache

    # Checked before the memo so a path that was evicted never serves a window
    # that an in-flight request managed to re-insert after the eviction.
    cache_entry = request.app.state.signal_cache.get(path)
    if cache_entry is None:
        return ChannelWindowResult(status="error", error=f"File not imported: {path}")

    cached = window_cache.get(cache_key)
    if cached is not None:
        return cached

    if channel not in cache_entry["df"].columns:
        return ChannelWindowResult(status="error", error=f"Unknown channel: {channel}")

    source_df = cache_entry["df"]
    signal_column = channel
    if filtered:
        try:
            source_df = source_df.assign(
                **{
                    "__filtered__": get_filtered_channel(
                        request, path, channel, cache_entry
                    )
                }
            )
            signal_column = "__filtered__"
        except Exception as e:
            return ChannelWindowResult(
                status="error", error=f"Could not filter channel: {e}"
            )

    x, y, downsampled = window_channel_data(
        source_df,
        cache_entry["time_column"],
        signal_column,
        start,
        end,
        resolution,
    )

    result = ChannelWindowResult(
        status="ok",
        x=[float(v) for v in x],
        y=[float(v) for v in y],
        point_count=len(x),
        downsampled=downsampled,
    )
    # Only memoize while this is still the file's live data: a re-import or
    # eviction that landed while the window was computing must not be undone
    # by a stale result (the invalidation already ran).
    if request.app.state.signal_cache.get(path) is cache_entry:
        remember_window(window_cache, cache_key, result)
    return result
