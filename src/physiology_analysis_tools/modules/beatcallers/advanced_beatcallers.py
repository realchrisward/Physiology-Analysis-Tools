# -*- coding: utf-8 -*-
"""
Second wave of beat callers.

  snr_threshold_v2  - basicRR2: the legacy SNR detector, with the systematic
                      timing bias removed and the refractory-period bug fixed
  template_match    - normalised cross-correlation against a learned QRS template
  hilbert_envelope  - analytic envelope of the derivative
  two_average       - Elgendi's two moving averages

All of them receive the ALREADY FILTERED voltage from the Pipeline.
"""

__version__ = "0.0.1"

import numpy
import pandas
import scipy.signal

from ..registries import BEATCALLERS
from ..algorithms import (
    BeatCaller,
    Settings,
    beats_from_indices,
    empty_beat_df,
    enforce_refractory,
    refine_to_peak,
    rolling_threshold,
)


# %% basicRR2 -----------------------------------------------------------------


def basicRR2(
    voltage,
    time,
    fs=None,
    noisecutoff=75.0,
    threshfactor=2.0,
    absthresh=0.2,
    min_RR_ms=60.0,
    noise_on_abs=True,
    adaptive=False,
    adaptive_window_s=5.0,
    max_search_ms=40.0,
):
    """
    Revised version of signal_filters_and_analyzers.basicRR.

    Differences from basicRR (all of them deliberate):

    1. TIMING. basicRR reports the timestamp of the *threshold crossing*
       (``TS_R = TS[i]`` at the rising edge), which sits ~2-4 ms before the R
       peak and drifts with signal amplitude - a systematic bias that survives
       into RR, HRV and any beat-shape epoching. basicRR2 uses the crossing
       only to flag a *candidate*, then reports the time of the maximum of the
       filtered signal within that suprathreshold excursion.
    2. REFRACTORY PERIOD. basicRR compares ``j - prevJ`` (a difference in
       SAMPLE INDICES) against ``minRR`` (a value in SECONDS), so with default
       arguments the refractory check is effectively "50 samples", not 50 ms.
       basicRR2 takes ``min_RR_ms`` and converts it to samples using fs, and
       resolves collisions by keeping the larger R wave rather than the first.
    3. RR CONVENTION. basicRR measures RR between suprathreshold *exit* points;
       basicRR2 measures peak-to-peak, which is what RR is supposed to mean.
    4. It does not filter. The pipeline owns filtering.
    5. Optionally the threshold can adapt over time instead of being a single
       global value (``adaptive=True``) - useful for long awake recordings
       where R amplitude wanders.

    Returns the canonical beat DataFrame (ts, RR, R_amplitude, HR, beats) plus
    the irregularity scores basicRR provided (IS_RR, IS_HR).
    """
    voltage = numpy.asarray(voltage, dtype=float)
    time = numpy.asarray(time, dtype=float)
    if fs is None:
        fs = 1.0 / numpy.median(numpy.diff(time))

    reference = numpy.abs(voltage) if noise_on_abs else voltage
    noise_level = numpy.percentile(reference, noisecutoff)

    if adaptive:
        threshold = numpy.maximum(
            rolling_threshold(
                voltage, fs, window_s=adaptive_window_s, fraction=0.5
            ),
            absthresh,
        )
    else:
        threshold = numpy.full(
            voltage.shape, max(noise_level * threshfactor, absthresh)
        )

    above = voltage >= threshold
    if not above.any():
        return empty_beat_df()

    # rising edges = candidate beats
    crossings = numpy.flatnonzero(above[1:] & ~above[:-1]) + 1
    if crossings.size == 0:
        return empty_beat_df()

    max_search = max(2, int(round(max_search_ms / 1000.0 * fs)))
    candidates = []
    for start in crossings:
        # walk forward to the end of the suprathreshold excursion (capped, so a
        # long artifact plateau cannot swallow several beats)
        stop = start
        limit = min(start + max_search, voltage.size)
        while stop < limit and above[stop]:
            stop += 1
        candidates.append(start + int(numpy.argmax(voltage[start:max(stop, start + 1)])))

    peaks = enforce_refractory(voltage, candidates, fs, min_RR_ms)

    beats = beats_from_indices(time, voltage, peaks)
    if beats.empty:
        return beats

    # legacy irregularity scores, preserved for continuity with basicRR
    beats["IS_RR"] = beats["RR"].diff().abs() / beats["RR"].shift(1) * 100
    beats["IS_HR"] = beats["HR"].diff().abs() / beats["HR"].shift(1) * 100
    return beats


class SNRThresholdV2Settings(Settings):
    def __init__(self):
        self.noisecutoff = 75.0
        self.threshfactor = 2.0
        self.absthresh = 0.2
        self.min_RR_ms = 60.0
        self.noise_on_abs = True
        self.adaptive = False
        self.adaptive_window_s = 5.0
        self.max_search_ms = 40.0


@BEATCALLERS.register
class SNRThresholdV2(BeatCaller):
    name = "snr_threshold_v2"
    label = "Relative SNR threshold v2 (basicRR2)"
    description = (
        "basicRR with the threshold-crossing timing bias removed (crossings "
        "flag candidates; the R peak within the excursion gives the time), the "
        "refractory-period units fixed (ms, not samples), and peak-to-peak RR."
    )
    Settings = SNRThresholdV2Settings

    @staticmethod
    def call(voltage, time, fs, settings):
        return basicRR2(
            voltage,
            time,
            fs=fs,
            noisecutoff=settings.noisecutoff,
            threshfactor=settings.threshfactor,
            absthresh=settings.absthresh,
            min_RR_ms=settings.min_RR_ms,
            noise_on_abs=settings.noise_on_abs,
            adaptive=settings.adaptive,
            adaptive_window_s=settings.adaptive_window_s,
            max_search_ms=settings.max_search_ms,
        )


# %% template matching --------------------------------------------------------


class TemplateMatchSettings(Settings):
    def __init__(self):
        self.invert = False
        self.seed_perc_thresh = 99.0  # threshold for the seed detections
        self.template_window_ms = 40.0  # width of the QRS template
        self.correlation_thresh = 0.6  # normalised cross-correlation cutoff
        self.min_RR_ms = 60.0
        self.refine_window_ms = 10.0


@BEATCALLERS.register
class TemplateMatch(BeatCaller):
    name = "template_match"
    label = "Template match (matched filter)"
    description = (
        "Builds a QRS template from the median of high-confidence seed beats, "
        "then detects by normalised cross-correlation. Shape-sensitive: it "
        "rejects motion/EMG spikes that a pure amplitude threshold accepts, but "
        "for the same reason it can under-call ectopic beats whose morphology "
        "differs from the template - worth knowing before you use it upstream "
        "of arrhythmia detection."
    )
    Settings = TemplateMatchSettings

    @staticmethod
    def call(voltage, time, fs, settings):
        v = numpy.asarray(voltage, dtype=float)
        if settings.invert:
            v = -v

        min_distance = max(1, int(settings.min_RR_ms / 1000.0 * fs))

        # 1. seed detections
        seeds, _ = scipy.signal.find_peaks(
            v,
            height=numpy.percentile(v, settings.seed_perc_thresh),
            distance=min_distance,
        )
        half = max(2, int(round(settings.template_window_ms / 1000.0 * fs / 2)))
        epochs = [
            v[p - half : p + half]
            for p in seeds
            if p - half >= 0 and p + half <= v.size
        ]
        if len(epochs) < 5:
            return empty_beat_df()

        # 2. template = median beat, mean-removed and unit-normalised
        template = numpy.median(numpy.vstack(epochs), axis=0)
        template = template - template.mean()
        norm = numpy.linalg.norm(template)
        if norm == 0:
            return empty_beat_df()
        template = template / norm

        # 3. normalised cross-correlation
        correlation = scipy.signal.correlate(v, template, mode="same")
        energy = numpy.sqrt(
            numpy.convolve(v ** 2, numpy.ones(template.size), mode="same")
        )
        ncc = correlation / (energy + 1e-12)

        peaks, _ = scipy.signal.find_peaks(
            ncc, height=settings.correlation_thresh, distance=min_distance
        )
        peaks = refine_to_peak(v, peaks, fs, settings.refine_window_ms)
        peaks = enforce_refractory(v, peaks, fs, settings.min_RR_ms)
        return beats_from_indices(time, v, peaks)


# %% hilbert envelope ---------------------------------------------------------


class HilbertEnvelopeSettings(Settings):
    def __init__(self):
        self.invert = False
        self.smooth_ms = 15.0
        self.adaptive_window_s = 2.0
        self.threshold_fraction = 0.3
        self.min_RR_ms = 60.0
        self.refine_window_ms = 15.0


@BEATCALLERS.register
class HilbertEnvelope(BeatCaller):
    name = "hilbert_envelope"
    label = "Hilbert envelope"
    description = (
        "Analytic (Hilbert) envelope of the signal derivative, smoothed and cut "
        "with an adaptive threshold. Phase-insensitive, so it is largely "
        "indifferent to whether the R wave is positive or negative - handy when "
        "lead polarity is inconsistent across a cohort."
    )
    Settings = HilbertEnvelopeSettings

    @staticmethod
    def call(voltage, time, fs, settings):
        v = numpy.asarray(voltage, dtype=float)
        if settings.invert:
            v = -v

        derivative = numpy.gradient(v) * fs
        envelope = numpy.abs(scipy.signal.hilbert(derivative))

        window = max(1, int(round(settings.smooth_ms / 1000.0 * fs)))
        envelope = numpy.convolve(
            envelope, numpy.ones(window) / window, mode="same"
        )

        threshold = rolling_threshold(
            envelope,
            fs,
            window_s=settings.adaptive_window_s,
            fraction=settings.threshold_fraction,
        )
        peaks, _ = scipy.signal.find_peaks(
            envelope,
            height=threshold,
            distance=max(1, int(settings.min_RR_ms / 1000.0 * fs)),
        )
        peaks = refine_to_peak(v, peaks, fs, settings.refine_window_ms)
        peaks = enforce_refractory(v, peaks, fs, settings.min_RR_ms)
        return beats_from_indices(time, v, peaks)


# %% two moving averages (Elgendi) --------------------------------------------


class TwoAverageSettings(Settings):
    def __init__(self):
        self.invert = False
        self.qrs_window_ms = 15.0  # W1 - roughly one QRS width (mouse)
        self.beat_window_ms = 90.0  # W2 - roughly one beat
        self.beta = 0.08  # offset as a fraction of mean energy
        self.min_block_ms = 4.0
        self.min_RR_ms = 60.0


@BEATCALLERS.register
class TwoAverage(BeatCaller):
    name = "two_average"
    label = "Two moving averages (Elgendi)"
    description = (
        "Elgendi's event-related moving averages: a short (QRS-width) average of "
        "the squared signal is compared against a long (beat-width) average plus "
        "an offset; contiguous blocks above it are QRS candidates. Cheap, no "
        "percentile assumptions, and it copes well with varying beat amplitude. "
        "Window widths here are scaled for rodent heart rates."
    )
    Settings = TwoAverageSettings

    @staticmethod
    def call(voltage, time, fs, settings):
        v = numpy.asarray(voltage, dtype=float)
        if settings.invert:
            v = -v

        squared = numpy.clip(v, 0, None) ** 2

        w1 = max(1, int(round(settings.qrs_window_ms / 1000.0 * fs)))
        w2 = max(w1 + 1, int(round(settings.beat_window_ms / 1000.0 * fs)))
        ma_peak = numpy.convolve(squared, numpy.ones(w1) / w1, mode="same")
        ma_beat = numpy.convolve(squared, numpy.ones(w2) / w2, mode="same")

        threshold = ma_beat + settings.beta * squared.mean()
        blocks = ma_peak > threshold

        min_block = max(1, int(round(settings.min_block_ms / 1000.0 * fs)))

        # contiguous runs of True
        edges = numpy.diff(blocks.astype(int))
        starts = numpy.flatnonzero(edges == 1) + 1
        stops = numpy.flatnonzero(edges == -1) + 1
        if blocks[0]:
            starts = numpy.insert(starts, 0, 0)
        if blocks[-1]:
            stops = numpy.append(stops, blocks.size)

        peaks = [
            start + int(numpy.argmax(v[start:stop]))
            for start, stop in zip(starts, stops)
            if stop - start >= min_block
        ]
        peaks = enforce_refractory(v, peaks, fs, settings.min_RR_ms)
        return beats_from_indices(time, v, peaks)
