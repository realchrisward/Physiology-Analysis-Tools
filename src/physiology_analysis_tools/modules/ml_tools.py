# -*- coding: utf-8 -*-

"""
unsupervised clustering based arrhythmia_detection for ECG Analysis Tool
written by Ewan Selkirk, 2024

"""

import numpy
import scipy
import scipy.signal
import scipy.stats
import pandas as pd
import sklearn.decomposition
import sklearn.cluster
import sklearn.neighbors

__version__ = "0.0.2"

# Beat epochs are keyed by timestamp rather than by positional index (see
# beatepocher_kde_clipped_rr_smooth), and floats coming back out of a
# resample/round-trip can differ in their last bits from the values in
# beat_df. Rounding both sides to this many decimals before matching keeps
# the join stable — at 1e-6 s (1 microsecond) it is far finer than any
# realistic sampling interval, so it can't collapse two genuine beats.
TIMESTAMP_DECIMALS = 6


def basic_filter(order, signal, fs=1000, cutoff=5, output="sos"):
    """
    Copy of heartbeat_detection.basic_filter, where inverted beats are detected automatically.
    These inverted beats will be flipped to allow for comparison across datasets.
    """
    sos = scipy.signal.butter(order, cutoff, fs=fs, btype="highpass", output="sos")
    filtered_data = scipy.signal.sosfiltfilt(sos, signal)

    peaks, _ = scipy.signal.find_peaks(filtered_data, distance=100)
    neg_peaks, _ = scipy.signal.find_peaks(-filtered_data, distance=100)

    ecg_invert = abs(filtered_data[peaks].mean()) < abs(filtered_data[neg_peaks].mean())

    if ecg_invert:
        return filtered_data * -1

    return filtered_data


def beatepocher(
    filtered_data_frame, beat_df, voltage_column="ecg", time_column = "time", window=250, **kwargs
):
    """
    Create a list numpy arrays, of the voltage over heartbeats detected in ECG, detrended and normalised. Takes ECG signal and the timestamps of the heartbeats as input.

    Parameters:
        df - dataframe - Dataframe of filtered data (Usually filtered using `basic_filter` function)
        beat_index - list of ints - List of the indices where the beat was detected. e.g. Index of the dataframe returned by heartbeat_detection.beat_caller
        voltage_column - str - Column name for the voltages in `df`
        window - int - Number of datapoints to include in the window
        **kwargs -
            pre - int - shares of window before R peak
            post - int - shares of window after R peak
            Pre and post arguments allow you to skew the window to either before (pre) or after (post) the R peak/detected beat.
            e.g To skew the window to 2/3rds before the R peak, submit pre = 2, post =1

    Returns:
    - Dictionary of Numpy arrays, containing voltages over course of each window around a heartbeat. Labelled with index where beat was detected
    """
    pre_window = window / 2
    post_window = window / 2

    if "pre" in kwargs:
        if "post" in kwargs:
            pre = kwargs.get("pre")
            post = kwargs.get("post")

            pre_window = pre / (pre + post) * window
            post_window = post / (pre + post) * window

    epochs_dict = {}

    for idx, ts in enumerate(beat_df.ts):

        index = list(filtered_data_frame[time_column]).index(ts)

        start = index - round(pre_window, 0)
        end = index + round(post_window, 0)

        if (start < min(filtered_data_frame.index)) or (
            end > max(filtered_data_frame.index)
        ):
            continue

        data_epoch = filtered_data_frame.loc[start:end]

        beat_epoch = data_epoch[voltage_column].to_numpy()

        epochs_dict[idx] = detrend_normalise(beat_epoch)

    return epochs_dict


def extract_first_modality_kde(rr_data, bandwidth=0.05):
    """
    Return only the RR intervals belonging to the first (dominant) mode of
    the RR distribution.

    An ECG recording's RR intervals are usually multi-modal: one dense mode
    at the animal's normal beat-to-beat interval, plus longer intervals
    wherever a beat was missed, skipped, or the trace was noisy. Taking a
    plain median over all of them drags the "typical" RR upwards. Estimating
    the distribution with a Gaussian KDE and cutting at the first local
    minimum to the right of its tallest peak isolates that dominant mode.

    The tallest peak is used as the anchor (not the leftmost one) because
    spurious short RR intervals from double-detected beats put real peaks to
    the left of the true normal-beat mode.
    """
    rr_data = numpy.asarray(rr_data)
    if len(rr_data) < 2:
        return rr_data

    try:
        kde = scipy.stats.gaussian_kde(rr_data, bw_method=bandwidth)
    except numpy.linalg.LinAlgError:
        # A perfectly regular recording (every RR interval identical) has a
        # singular covariance, which gaussian_kde cannot fit. There is no
        # second mode to separate out in that case, so every interval
        # already belongs to the first one.
        return rr_data

    rr_grid = numpy.linspace(numpy.min(rr_data), numpy.max(rr_data), 1000)
    kde_vals = kde(rr_grid)

    peaks, _ = scipy.signal.find_peaks(kde_vals)
    if len(peaks) == 0:
        return rr_data
    main_peak = peaks[numpy.argmax(kde_vals[peaks])]

    # Local minima of the KDE curve: where its first difference changes from
    # falling to rising.
    minima = (numpy.diff(numpy.sign(numpy.diff(kde_vals))) > 0).nonzero()[0] + 1
    right_min = minima[minima > main_peak]
    if len(right_min) == 0:
        return rr_data

    cutoff_val = rr_grid[right_min[0]]
    return rr_data[rr_data < cutoff_val]


# RR intervals longer than this many times the median are gaps (a bad-data
# stretch, a pause, joined recordings), not beats, and are left out before
# looking for the dominant mode.
_TYPICAL_RR_MAX_MEDIAN_MULTIPLE = 5
# Above this many intervals the KDE is fitted to an evenly-strided subset:
# the distribution's shape is the same, and the cost stays bounded.
_TYPICAL_RR_MAX_SAMPLES = 20000


def estimate_typical_rr(ts, bandwidth=0.05):
    """
    The recording's typical beat-to-beat interval in seconds: the median of
    the dominant RR mode (see extract_first_modality_kde), or None when there
    are too few beats to say.

    Unlike the epoch window in beatepocher_kde_clipped_rr_smooth this is NOT
    clipped to the mouse heart-rate range, so it is right for any species; it
    is used to size the review graph's view around a beat.

    Gaps and non-positive intervals (duplicate timestamps) are dropped first.
    One very long gap otherwise stretches the KDE grid so far that the real
    mode lands on its edge, where no peak is found and the mode filter does
    nothing.
    """
    ts = numpy.sort(numpy.asarray(ts, dtype=float))
    if len(ts) < 2:
        return None
    rr = numpy.diff(ts)
    rr = rr[rr > 0]
    if len(rr) == 0:
        return None

    rr = rr[rr <= _TYPICAL_RR_MAX_MEDIAN_MULTIPLE * numpy.median(rr)]
    if len(rr) > _TYPICAL_RR_MAX_SAMPLES:
        rr = rr[:: len(rr) // _TYPICAL_RR_MAX_SAMPLES]

    first_mode = extract_first_modality_kde(rr, bandwidth=bandwidth)
    chosen = first_mode if len(first_mode) > 0 else rr
    return float(numpy.median(chosen))


def beatepocher_kde_clipped_rr_smooth(
    filtered_data_frame,
    beat_df,
    voltage_column="ecg",
    time_column="time",
    beat_window=1,
    bias=0,
    beat_length=128,
    kde_bandwidth=0.05,
    min_rr=0.1,
    max_rr=0.1667,
):
    """
    Slice one fixed-length, shape-normalised epoch out of the signal around
    every detected beat, for shape-based clustering (see beat_clusterer).

    Improves on `beatepocher` in three ways:
      - the epoch window is sized from the recording's OWN dominant RR
        interval (via extract_first_modality_kde), not a fixed sample count,
        so one window setting works across recordings at different heart
        rates and sample rates;
      - that RR estimate is clipped to [min_rr, max_rr] so a recording with
        too few or mostly-artefactual beats can't produce an absurd window;
      - every epoch is resampled to exactly `beat_length` samples, so epochs
        stay directly comparable even when the window size differs.

    `bias` shifts the window around the R peak: 0 centres it, +1 puts it
    entirely after the peak, -1 entirely before.

    Returns a dict of {rounded timestamp: normalised epoch}. Keying by
    timestamp (rather than `beatepocher`'s positional index) is what lets
    call_arrhythmias_PCA join results back onto the beat table by `ts`,
    which stays correct no matter how beat_df is indexed.
    """
    ts_values = beat_df["ts"].values
    rr_all = numpy.diff(ts_values)

    if len(rr_all) == 0:
        rr_window = 0.0
    else:
        rr_first_modality = extract_first_modality_kde(rr_all, bandwidth=kde_bandwidth)
        median_rr = (
            numpy.median(rr_first_modality)
            if len(rr_first_modality) > 0
            else numpy.median(rr_all)
        )
        rr_window = float(numpy.clip(median_rr, min_rr, max_rr))

    sample_interval = (
        filtered_data_frame[time_column].iloc[1]
        - filtered_data_frame[time_column].iloc[0]
    )
    sample_rate = 1 / sample_interval

    right_share = (1 + bias) / 2
    left_share = 1 - right_share
    left_window = left_share * beat_window * rr_window * sample_rate
    right_window = right_share * beat_window * rr_window * sample_rate

    epochs_dict = {}
    time_positions = list(filtered_data_frame[time_column])
    index_min = min(filtered_data_frame.index)
    index_max = max(filtered_data_frame.index)

    for ts_val in beat_df.ts:
        index = time_positions.index(ts_val)
        start = int(index - round(left_window))
        end = int(index + round(right_window))
        if (start < index_min) or (end > index_max):
            continue

        beat_epoch = filtered_data_frame.loc[start:end][voltage_column].to_numpy()
        beat_epoch = scipy.signal.resample(beat_epoch, beat_length)
        key = round(float(ts_val), TIMESTAMP_DECIMALS)
        epochs_dict[key] = detrend_normalise(beat_epoch)

    return epochs_dict


def detrend_normalise(signal):
    """
    Detrend and normalise ECG signal for a single Epoch.

    Parameters:
        signal - array_like - voltages of ECG signal (for a single epoch)

    Returns :
    - Array: Array of detrended and normalised signal

    """
    signal = scipy.signal.detrend(signal)
    dn_signal = signal / numpy.linalg.norm(signal, ord=2)
    return dn_signal


def auto_eps(points, min_samples, percentile=90):
    """
    Pick a DBSCAN `eps` from the data itself, via the standard k-distance
    heuristic: take each point's distance to its `min_samples`-th nearest
    neighbour, and use a high percentile of those distances.

    A fixed `eps` cannot work here. It is an absolute distance in a PCA space
    whose scale is set by each recording's own epoch amplitudes and shape
    variance, so the same value that isolates a handful of odd beats in one
    recording labels either none or all of them in the next. Deriving it from
    the k-distance distribution makes the threshold mean the same thing
    ("further from its neighbours than most beats are") in every recording.
    """
    points = numpy.asarray(points)
    k = max(2, min(min_samples, len(points) - 1))
    neighbours = sklearn.neighbors.NearestNeighbors(n_neighbors=k).fit(points)
    distances, _ = neighbours.kneighbors(points)
    return float(numpy.percentile(distances[:, -1], percentile))


def beat_clusterer(
    epochs_dict,
    eps=0.5,
    min_samples=20,
    eps_auto=False,
    eps_percentile=90,
):
    """
    Cluster the beats based on shape in PCA space using DBSCAN (density based clustering)

    Parameters:
        epochs_dict - Dictionary of Numpy arrays, with kets as index where heartbeat is detected and values being numpy arr of voltages. Output of beatepocher
        eps_auto - derive `eps` from the data via auto_eps() instead of using the fixed `eps` value
    """
    if len(epochs_dict) < 2:
        raise ValueError(
            f"Unsupervised (PCA/clustering) arrhythmia detection needs at "
            f"least 2 usable beat epochs, got {len(epochs_dict)}. Beats too "
            f"close to the start/end of the recording are skipped, so this "
            f"usually means too few beats were detected overall — try the "
            f"Heuristic method instead, or check beat detection settings."
        )

    df = pd.DataFrame.from_dict(epochs_dict, orient="index")

    # A flat/zero-variance epoch can make detrend_normalise divide by a
    # near-zero norm, injecting inf/NaN — drop those rows rather than
    # letting them crash PCA with an opaque sklearn error.
    valid_mask = numpy.isfinite(df.to_numpy()).all(axis=1)
    df = df.loc[valid_mask]

    if len(df) < 2:
        raise ValueError(
            f"Unsupervised (PCA/clustering) arrhythmia detection needs at "
            f"least 2 valid beat epochs, but only {len(df)} remained after "
            f"discarding beats with invalid (non-finite) signal data — try "
            f"the Heuristic method instead, or check beat detection settings."
        )

    PCAobj = sklearn.decomposition.PCA(n_components=2)
    fit = PCAobj.fit_transform(df)
    fitDF = pd.DataFrame(data=fit, columns=["PC1", "PC2"])

    # DBSCAN can only form a cluster of at least min_samples points, so a
    # min_samples larger than the recording's beat count would label every
    # beat an outlier no matter how tightly grouped they are.
    effective_min_samples = max(2, min(min_samples, len(df) // 2))

    effective_eps = (
        auto_eps(fit, effective_min_samples, percentile=eps_percentile)
        if eps_auto
        else eps
    )

    cluster = sklearn.cluster.DBSCAN(
        eps=effective_eps, min_samples=effective_min_samples
    ).fit(fitDF)
    cluster_dict = dict(zip(df.index, cluster.labels_))
    return cluster_dict


def call_arrhythmias_PCA(filtered_data_df, beats_df, voltage_column_name, time_column_name, settings):

    beat_epochs_dict = beatepocher_kde_clipped_rr_smooth(
        filtered_data_df,
        beats_df,
        voltage_column=voltage_column_name,
        time_column=time_column_name,
        beat_window=settings.beat_window,
        bias=settings.beat_window_bias,
        beat_length=settings.beat_length,
        kde_bandwidth=settings.kde_bandwidth,
        min_rr=settings.min_rr,
        max_rr=settings.max_rr,
    )

    cluster_dict = beat_clusterer(
        beat_epochs_dict,
        eps=settings.eps,
        min_samples=settings.min_samples,
        eps_auto=settings.eps_auto,
        eps_percentile=settings.eps_percentile,
    )

    # DBSCAN labels the largest dense group of same-shaped beats 0; every
    # other label (including -1, its own "outlier" label) is a beat whose
    # shape doesn't match the recording's own normal beat.
    abn_by_ts = {ts: label != 0 for ts, label in cluster_dict.items()}

    # Clear previously assigned abn_clusters. i.e if rerunning PCA analysis
    if "abn_cluster" in beats_df.columns:
        beats_df = beats_df.drop(columns="abn_cluster")

    # Matched on `ts`, not on row position: epochs are keyed by timestamp and
    # boundary-skipped beats are simply absent from the dict, so a beat with
    # no epoch correctly lands as NaN ("not evaluated") rather than silently
    # taking its neighbour's label.
    beats_df["abn_cluster"] = (
        beats_df["ts"].round(TIMESTAMP_DECIMALS).map(abn_by_ts)
    )

    return beats_df


