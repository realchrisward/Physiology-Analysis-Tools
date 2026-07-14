# -*- coding: utf-8 -*-
"""
Beat callers imported from sibling projects.

  absolute_threshold  - SLusk's find_peaks caller: a fixed voltage threshold and
                        a refractory period expressed in SECONDS.
  isoline_offset      - the "median + 0.15 V" caller: threshold set from the
                        isoelectric line of the recording plus a fixed offset.

Both originals filtered internally (a hard-wired 60 Hz "kick60" notch in the
isoline caller). As with every other caller in this package, that is disabled
here: the Pipeline's filter stage is the only filter applied, which is what
makes an M x N x K grid mean anything.

Deviations from the originals are collected in the class docstrings rather than
silently applied. Read them before you trust a comparison.
"""

__version__ = "0.0.1"

import numpy
import scipy.signal

from ..registries import BEATCALLERS
from ..algorithms import BeatCaller, Settings, beats_from_indices, empty_beat_df


# %% SLusk: absolute threshold + find_peaks


class AbsoluteThresholdSettings(Settings):
    def __init__(self):
        self.abs_thresh = 0.14  # volts (or normalised units - see note)
        self.min_RR_ms = 100.0  # refractory period, MILLISECONDS
        self.ecg_invert = False


@BEATCALLERS.register
class AbsoluteThreshold(BeatCaller):
    """
    Fixed-threshold reference detector (from SLusk's ``beat_caller``).

    Threshold policy: STATIONARY / SIGNAL-INDEPENDENT. The threshold is a
    constant the user supplies; nothing about the recording is measured. That
    makes it the cleanest control condition in the grid - it is the only
    detector whose behaviour is completely determined by the filter and
    normaliser upstream of it - and also the one most exposed to recording gain.
    Pair it with ``percentile_scale`` (R peak ~ 1.0) or ``rolling_robust`` and
    the 0.14 default becomes meaningful across recordings; pair it with ``none``
    and you are testing the gain of your amplifier.

    Deviations from the original
    ----------------------------
    * The original references an undefined name ``absthresh_ecg`` inside
      ``find_peaks`` while accepting ``absthresh`` as its argument - it raises
      ``NameError`` unless a module-level ``absthresh_ecg`` happens to exist.
      Fixed here to use the settings value.
    * ``minRR`` is expressed as ``min_RR_ms`` (default 100 ms, the intended
      value) and converted to samples with ``fs``, rather than being derived
      from ``TS[1] - TS[0]``. The pipeline's ``fs`` is a median-of-diffs
      estimate and survives stitched multi-block recordings.
    * ``ts``/``RR`` convention: the original labels each beat with the timestamp
      of the peak that OPENS the interval (``ts = peaks[:-1]``, RR = the
      following interval). Every caller in this package uses the repository
      convention instead (``ts`` = the peak, RR = the interval that PRECEDES
      it), so that RR, HR and R_amplitude on a row all describe the same beat.
      Without this the grid would be comparing detectors against ground truth
      with a one-beat offset baked into half of them.
    """

    name = "absolute_threshold"
    label = "Absolute threshold (find_peaks, SLusk)"
    description = (
        "Fixed voltage threshold plus a refractory period, via "
        "scipy.signal.find_peaks. No signal statistic is used to set the "
        "threshold, so this detector is entirely at the mercy of the "
        "normalisation stage - which is exactly what makes it a useful control."
    )
    Settings = AbsoluteThresholdSettings

    @staticmethod
    def call(voltage, time, fs, settings):
        v = numpy.asarray(voltage, dtype=float)
        t = numpy.asarray(time, dtype=float)

        if settings.ecg_invert:
            v = -v

        distance = max(1, int(round(settings.min_RR_ms / 1000.0 * fs)))

        peaks, _ = scipy.signal.find_peaks(
            v, height=settings.abs_thresh, distance=distance
        )

        if peaks.size < 2:
            return empty_beat_df()

        return beats_from_indices(t, v, peaks)


# %% isoelectric line + fixed offset


class IsolineOffsetSettings(Settings):
    def __init__(self):
        self.offset = 0.15  # added to the isoelectric line
        self.min_RR_ms = 100.0  # refractory period, MILLISECONDS
        self.ecg_invert = False


@BEATCALLERS.register
class IsolineOffset(BeatCaller):
    """
    Isoelectric line plus a fixed offset (from the ``beatcaller`` that computed
    ``absthresh = median(ecg) + 0.15``).

    Threshold policy: GLOBAL, SIGNAL-DERIVED, but only in its baseline. The
    median locates the isoelectric line over the whole analysis segment; the
    distance from that line to the detection threshold is a constant. So the
    detector is immune to DC offset and baseline wander (to the extent the
    median tracks them) but not to gain: halve the R amplitude and 0.15 V is
    suddenly above every beat. This is the intermediate case between
    ``absolute_threshold`` (nothing measured) and ``percentile_threshold``
    (both baseline and scale measured).

    Deviations from the original
    ----------------------------
    * The internal ``basicFilt_alt`` / kick60 filter is removed. The pipeline
      filter stage owns filtering.
    * The original takes the median from the RAW ``signal_data["ecg"]`` but
      searches for peaks in the FILTERED ``CT``. After any high-pass filter the
      filtered signal is ~zero-centred, so the raw DC offset gets added to a
      signal that no longer carries it - the threshold is off by the raw
      baseline. Here the median is taken from the same array the peaks are found
      in, which is what the code was evidently trying to do.
    * ``distance=minRR`` in the original is passed to ``find_peaks`` as a SAMPLE
      COUNT, so the intended 100 ms refractory period only holds at fs = 1 kHz;
      at any other sampling rate it silently becomes something else. The
      intended behaviour is preserved here: ``min_RR_ms`` defaults to 100 ms and
      is converted to samples with ``fs``, so it is 100 ms at every rate.
    * ``ts``/``RR`` convention: as for ``absolute_threshold`` above.
    """

    name = "isoline_offset"
    label = "Isoelectric line + offset"
    description = (
        "Threshold = median of the analysis segment (the isoelectric line) plus "
        "a fixed offset. Robust to baseline offset, sensitive to gain."
    )
    Settings = IsolineOffsetSettings

    @staticmethod
    def call(voltage, time, fs, settings):
        v = numpy.asarray(voltage, dtype=float)
        t = numpy.asarray(time, dtype=float)

        if settings.ecg_invert:
            v = -v

        isoline = float(numpy.median(v))
        threshold = isoline + settings.offset

        distance = max(1, int(round(settings.min_RR_ms / 1000.0 * fs)))

        peaks, _ = scipy.signal.find_peaks(v, height=threshold, distance=distance)

        if peaks.size < 2:
            return empty_beat_df()

        return beats_from_indices(t, v, peaks)
