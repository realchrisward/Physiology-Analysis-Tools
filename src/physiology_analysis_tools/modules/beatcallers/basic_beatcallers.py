# -*- coding: utf-8 -*-
"""
Starter set of beat callers.

IMPORTANT: every caller here receives the ALREADY FILTERED voltage from the
Pipeline. The two legacy wrappers therefore disable the internal filtering of
the functions they wrap, so that the filter under test is the only filter
applied.
"""

__version__ = "0.0.1"

import numpy
import pandas
import scipy.signal

from . import BEATCALLERS
from ..algorithms import (
    BeatCaller,
    Settings,
    beats_from_indices,
    empty_beat_df,
    normalize_beat_df,
)
from .. import heartbeat_detection
from ..signal_converters import signal_filters_and_analyzers


# %% legacy: heartbeat_detection.beatcaller


class PercentileThresholdSettings(Settings):
    def __init__(self):
        self.min_RR = 60  # ms
        self.ecg_invert = False
        self.ecg_abs_value = False
        self.abs_thresh = None
        self.perc_thresh = 97
        self.breath_filter = True
        self.breath_filter_cutoff = None


@BEATCALLERS.register
class PercentileThreshold(BeatCaller):
    name = "percentile_threshold"
    label = "Percentile threshold (legacy)"
    description = (
        "The current production detector (heartbeat_detection.beatcaller), with "
        "its internal filtering disabled so the pipeline filter is the only one "
        "applied. Threshold = a percentile of the filtered voltage, peaks found "
        "with a minimum RR refractory period."
    )
    Settings = PercentileThresholdSettings

    @staticmethod
    def call(voltage, time, fs, settings):
        df = pandas.DataFrame({"time": numpy.asarray(time, dtype=float),
                               "voltage": numpy.asarray(voltage, dtype=float)})
        beat_df = heartbeat_detection.beatcaller(
            df,
            voltage_column="voltage",
            time_column="time",
            min_RR=settings.min_RR,
            ecg_invert=settings.ecg_invert,
            ecg_abs_value=settings.ecg_abs_value,
            ecg_filter=False,  # <- pipeline owns filtering
            abs_thresh=settings.abs_thresh,
            perc_thresh=settings.perc_thresh,
            breath_filter=settings.breath_filter,
            breath_filter_cutoff=settings.breath_filter_cutoff,
        )
        return normalize_beat_df(beat_df)


# %% legacy: signal_filters_and_analyzers.basicRR


class SNRThresholdSettings(Settings):
    def __init__(self):
        self.noisecutoff = 75.0
        self.threshfactor = 2.0
        self.absthresh = 0.2
        self.minRR = 0.05  # seconds


@BEATCALLERS.register
class SNRThreshold(BeatCaller):
    name = "snr_threshold"
    label = "Relative SNR threshold (legacy)"
    description = (
        "signal_filters_and_analyzers.basicRR: threshold set as a multiple of a "
        "percentile 'noise level', floored by an absolute threshold. Internal "
        "filtering disabled."
    )
    Settings = SNRThresholdSettings

    @staticmethod
    def call(voltage, time, fs, settings):
        beat_df = signal_filters_and_analyzers.basicRR(
            pandas.Series(numpy.asarray(voltage, dtype=float)).reset_index(drop=True),
            pandas.Series(numpy.asarray(time, dtype=float)).reset_index(drop=True),
            noisecutoff=settings.noisecutoff,
            threshfactor=settings.threshfactor,
            absthresh=settings.absthresh,
            minRR=settings.minRR,
            ecg_filter="0",  # <- pipeline owns filtering
            ecg_invert="0",
        )
        if not isinstance(beat_df, pandas.DataFrame) or beat_df.empty:
            return empty_beat_df()
        return normalize_beat_df(beat_df)


# %% plain scipy.find_peaks


class FindPeaksSettings(Settings):
    def __init__(self):
        self.invert = False
        self.perc_thresh = 97.0
        self.abs_thresh = None
        self.min_RR_ms = 60.0
        self.prominence = None


@BEATCALLERS.register
class FindPeaks(BeatCaller):
    name = "find_peaks"
    label = "scipy find_peaks"
    description = (
        "Minimal reference detector: threshold + refractory period, optionally "
        "with a prominence requirement. Useful as a floor to beat."
    )
    Settings = FindPeaksSettings

    @staticmethod
    def call(voltage, time, fs, settings):
        v = numpy.asarray(voltage, dtype=float)
        if settings.invert:
            v = -v

        if settings.abs_thresh is not None:
            height = settings.abs_thresh
        else:
            height = numpy.percentile(v, settings.perc_thresh)

        peaks, _ = scipy.signal.find_peaks(
            v,
            height=height,
            distance=max(1, int(settings.min_RR_ms / 1000.0 * fs)),
            prominence=settings.prominence,
        )
        return beats_from_indices(time, v, peaks)


# %% pan-tompkins style


class PanTompkinsSettings(Settings):
    def __init__(self):
        self.invert = False
        self.integration_window_ms = 30.0  # scaled down from the human 150 ms
        self.adaptive_window_s = 2.0  # 0 -> use a single global threshold
        self.threshold_fraction = 0.35  # between the rolling floor and ceiling
        self.perc_thresh = 85.0  # used only when adaptive_window_s == 0
        self.min_RR_ms = 60.0
        self.refine_window_ms = 15.0


@BEATCALLERS.register
class PanTompkins(BeatCaller):
    name = "pan_tompkins"
    label = "Pan-Tompkins (energy)"
    description = (
        "Derivative -> square -> moving-window integration, then threshold on "
        "the energy envelope; peaks are refined back onto the local maximum of "
        "the filtered signal. Window widths are scaled for rodent heart rates."
    )
    Settings = PanTompkinsSettings

    @staticmethod
    def call(voltage, time, fs, settings):
        v = numpy.asarray(voltage, dtype=float)
        if settings.invert:
            v = -v

        derivative = numpy.gradient(v) * fs
        squared = derivative ** 2

        window = max(1, int(round(settings.integration_window_ms / 1000.0 * fs)))
        integrated = numpy.convolve(
            squared, numpy.ones(window) / window, mode="same"
        )

        if settings.adaptive_window_s:
            # running floor / ceiling of the energy envelope: tracks amplitude
            # drift (electrode contact, anaesthetic depth) that a single global
            # percentile cannot follow
            window = max(3, int(round(settings.adaptive_window_s * fs)))
            envelope = pandas.Series(integrated)
            floor = envelope.rolling(window, center=True, min_periods=1).median()
            ceiling = envelope.rolling(window, center=True, min_periods=1).quantile(0.99)
            threshold = (
                floor + settings.threshold_fraction * (ceiling - floor)
            ).to_numpy()
        else:
            threshold = numpy.percentile(integrated, settings.perc_thresh)

        peaks, _ = scipy.signal.find_peaks(
            integrated,
            height=threshold,
            distance=max(1, int(settings.min_RR_ms / 1000.0 * fs)),
        )

        # refine each detection onto the local maximum of the filtered signal
        refine = max(1, int(round(settings.refine_window_ms / 1000.0 * fs)))
        refined = []
        for p in peaks:
            start = max(0, p - refine)
            stop = min(v.size, p + refine + 1)
            refined.append(start + int(numpy.argmax(v[start:stop])))

        return beats_from_indices(time, v, refined)
