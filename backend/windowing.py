import numpy
from fastapi import APIRouter, Request

from backend.models import ChannelWindowResult

router = APIRouter(prefix="/channels", tags=["channels"])


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
    same filtering and downsampling decision, minus the filt_column
    UI-display-toggle parameter (out of scope for this milestone).
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


@router.get("/window", response_model=ChannelWindowResult)
def get_channel_window(
    path: str,
    channel: str,
    start: float,
    end: float,
    resolution: int,
    request: Request,
) -> ChannelWindowResult:
    cache_key = (path, channel, start, end, resolution)
    window_cache = request.app.state.window_cache
    if cache_key in window_cache:
        return window_cache[cache_key]

    cache_entry = request.app.state.signal_cache.get(path)
    if cache_entry is None:
        return ChannelWindowResult(status="error", error=f"File not imported: {path}")

    if channel not in cache_entry["df"].columns:
        return ChannelWindowResult(status="error", error=f"Unknown channel: {channel}")

    x, y, downsampled = window_channel_data(
        cache_entry["df"],
        cache_entry["time_column"],
        channel,
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
    window_cache[cache_key] = result
    return result
