# -*- coding: utf-8 -*-
"""
Base classes and shared helpers for pluggable signal filters and beat callers.

Design notes
------------
* Filtering and beat detection are SEPARATE steps. The existing
  ``heartbeat_detection.beatcaller`` filters internally; the wrappers in
  ``modules.beatcallers`` switch that off and consume the pre-filtered signal
  supplied by the Pipeline. This is what makes an M x N (filter x caller) grid
  meaningful.
* Settings objects are plain attribute-holding classes (same convention as
  ``heartbeat_detection.Settings``) so that main.FlexibleEntryWidget and the
  comparison window's settings form can build a form by introspecting
  ``__dict__``.

written for Physiology Analysis Tools (C) 2024
"""

__version__ = "0.0.1"

import copy

import numpy
import pandas

# canonical columns every beat caller must produce
BEAT_COLUMNS = ["ts", "RR", "R_amplitude", "HR", "beats"]


class Settings:
    """Base for algorithm settings. Subclasses just set attributes in __init__."""

    def as_dict(self):
        return dict(self.__dict__)

    def update(self, **kwargs):
        for k, v in kwargs.items():
            if k in self.__dict__:
                setattr(self, k, v)
        return self

    def clone(self):
        return copy.deepcopy(self)

    def signature(self):
        """Stable string used for caching / uid generation."""
        return ",".join(f"{k}={v!r}" for k, v in sorted(self.__dict__.items()))

    def __repr__(self):
        return f"{type(self).__name__}({self.signature()})"


class SignalFilter:
    """
    Interface for a digital filter.

    Subclasses set ``name`` (stable id, used in reports and saved configs) and
    ``label`` (shown in the UI), point ``Settings`` at a Settings subclass, and
    implement ``apply``.
    """

    name = None
    label = None
    description = ""
    Settings = Settings

    @staticmethod
    def apply(voltage, fs, settings):
        """
        Parameters
        ----------
        voltage : 1d numpy array of float
        fs : float
            sampling frequency (Hz)
        settings : instance of cls.Settings

        Returns
        -------
        1d numpy array of float, same length as ``voltage``
        """
        raise NotImplementedError


class SignalNormalizer:
    """
    Interface for an amplitude normalisation step, applied AFTER filtering and
    BEFORE beat detection.

    This stage exists because detectors carrying an absolute threshold (e.g.
    ``abs_thresh``, or basicRR's ``absthresh`` floor) are not comparable across
    filters that change the signal's scale: swap the filter and you have
    silently changed the detector too. Normalising puts every filter's output on
    a common amplitude scale, so a grid compares detection strategies rather
    than gain.
    """

    name = None
    label = None
    description = ""
    Settings = Settings

    @staticmethod
    def apply(voltage, fs, settings):
        raise NotImplementedError


class BeatCaller:
    """
    Interface for a beat detection algorithm.

    ``call`` receives the already filtered and normalised voltage. It must
    return a DataFrame with the columns in ``BEAT_COLUMNS``.
    """

    name = None
    label = None
    description = ""
    Settings = Settings

    @staticmethod
    def call(voltage, time, fs, settings):
        raise NotImplementedError


# %% helpers


def sampling_frequency(time):
    """Robust fs estimate (median sample interval - tolerates a stitched block)."""
    time = numpy.asarray(time, dtype=float)
    if time.size < 2:
        raise ValueError("cannot determine sampling frequency from < 2 samples")
    dt = numpy.median(numpy.diff(time))
    if dt <= 0:
        raise ValueError(f"non-monotonic time column (median dt = {dt})")
    return 1.0 / dt


def empty_beat_df():
    return pandas.DataFrame({c: [] for c in BEAT_COLUMNS})


def beats_from_indices(time, voltage, indices):
    """
    Build the canonical beat DataFrame from sample indices of detected R peaks.

    The first detected peak is dropped because it has no preceding RR interval
    (same convention as the existing heartbeat_detection.beatcaller, which also
    drops the last beat; comparison metrics tolerate the one-beat edge
    difference because scoring is done inside a matching tolerance window).
    """
    idx = numpy.unique(numpy.asarray(list(indices), dtype=int))
    if idx.size < 2:
        return empty_beat_df()

    time = numpy.asarray(time, dtype=float)
    voltage = numpy.asarray(voltage, dtype=float)

    ts = time[idx]
    amp = voltage[idx]
    rr = numpy.diff(ts)

    keep = rr > 0
    return pandas.DataFrame(
        {
            "ts": ts[1:][keep],
            "RR": rr[keep],
            "R_amplitude": amp[1:][keep],
            "HR": 60.0 / rr[keep],
            "beats": 1,
        }
    ).reset_index(drop=True)


def rolling_threshold(x, fs, window_s=2.0, fraction=0.35, quantile=0.99):
    """
    Adaptive threshold that tracks amplitude drift (electrode contact,
    anaesthetic depth, motion): a rolling floor (median) plus a fraction of the
    distance to a rolling ceiling (high quantile).

    Returns an array the same length as ``x``, suitable as ``height=`` for
    scipy.signal.find_peaks.
    """
    x = pandas.Series(numpy.asarray(x, dtype=float))
    window = max(3, int(round(window_s * fs)))
    floor = x.rolling(window, center=True, min_periods=1).median()
    ceiling = x.rolling(window, center=True, min_periods=1).quantile(quantile)
    return (floor + fraction * (ceiling - floor)).to_numpy()


def refine_to_peak(voltage, indices, fs, window_ms=15.0):
    """Snap each candidate index onto the local maximum of ``voltage``."""
    voltage = numpy.asarray(voltage, dtype=float)
    half = max(1, int(round(window_ms / 1000.0 * fs)))
    refined = []
    for i in numpy.asarray(indices, dtype=int):
        start = max(0, i - half)
        stop = min(voltage.size, i + half + 1)
        if stop > start:
            refined.append(start + int(numpy.argmax(voltage[start:stop])))
    return numpy.unique(refined)


def enforce_refractory(voltage, indices, fs, min_RR_ms):
    """
    Drop detections closer than ``min_RR_ms`` to an accepted one, keeping
    whichever of the pair has the larger amplitude.
    """
    voltage = numpy.asarray(voltage, dtype=float)
    idx = numpy.unique(numpy.asarray(indices, dtype=int))
    if idx.size == 0:
        return idx
    min_samples = max(1, int(round(min_RR_ms / 1000.0 * fs)))

    kept = [idx[0]]
    for i in idx[1:]:
        if i - kept[-1] < min_samples:
            if voltage[i] > voltage[kept[-1]]:
                kept[-1] = i
        else:
            kept.append(i)
    return numpy.array(kept, dtype=int)


def normalize_beat_df(df):
    """Ensure a beat DataFrame from a legacy function has the canonical columns."""
    df = df.copy().reset_index(drop=True)
    if "ts" not in df.columns:
        raise ValueError("beat DataFrame is missing a 'ts' column")
    if "RR" not in df.columns:
        df["RR"] = df["ts"].diff()
    if "HR" not in df.columns:
        df["HR"] = 60.0 / df["RR"]
    if "R_amplitude" not in df.columns:
        df["R_amplitude"] = numpy.nan
    if "beats" not in df.columns:
        df["beats"] = 1
    other = [c for c in df.columns if c not in BEAT_COLUMNS]
    return df[BEAT_COLUMNS + other]
