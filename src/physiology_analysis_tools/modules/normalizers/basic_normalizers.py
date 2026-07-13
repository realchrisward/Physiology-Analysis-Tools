# -*- coding: utf-8 -*-
"""
Starter set of amplitude normalisers.

Choosing one is a real decision, not a formality:

* ``passthrough`` leaves the signal in its recorded units. Absolute thresholds
  then mean volts - fine if you never change the filter, misleading in a grid.
* ``percentile_scale`` is the recommended default. It puts the R peak at
  roughly 1.0 regardless of filter or recording gain, which makes an
  ``abs_thresh`` of, say, 0.5 mean "half of a typical R wave" in every pipeline.
* ``rolling_robust`` additionally follows slow changes in R amplitude
  (electrode contact, anaesthetic depth, awake behaviour). It effectively hands
  a fixed-threshold detector an adaptive threshold - but it also flattens
  genuine amplitude changes, so do NOT use it if R-wave amplitude is one of
  your outcome measures.
"""

__version__ = "0.0.1"

import numpy
import pandas

from . import NORMALIZERS
from ..algorithms import Settings, SignalNormalizer

EPSILON = 1e-12


# %% none


class PassthroughNormSettings(Settings):
    def __init__(self):
        pass


@NORMALIZERS.register
class PassthroughNorm(SignalNormalizer):
    name = "none"
    label = "None (recorded units)"
    description = "No normalisation. Absolute thresholds are in recorded units."
    Settings = PassthroughNormSettings

    @staticmethod
    def apply(voltage, fs, settings):
        return numpy.asarray(voltage, dtype=float)


# %% percentile scaling (recommended default)


class PercentileScaleSettings(Settings):
    def __init__(self):
        self.percentile = 99.0  # ~ the height of a typical R wave
        self.center = "median"  # median | mean | none


@NORMALIZERS.register
class PercentileScale(SignalNormalizer):
    name = "percentile_scale"
    label = "Percentile scale (R peak ~ 1.0)"
    description = (
        "Centres the signal and divides by the distance from the centre to a "
        "high percentile, so a typical R wave lands near 1.0 in every pipeline. "
        "Makes absolute thresholds portable across filters and across animals."
    )
    Settings = PercentileScaleSettings

    @staticmethod
    def apply(voltage, fs, settings):
        v = numpy.asarray(voltage, dtype=float)
        if settings.center == "mean":
            centre = v.mean()
        elif settings.center == "none":
            centre = 0.0
        else:
            centre = numpy.median(v)
        scale = numpy.percentile(v, settings.percentile) - centre
        return (v - centre) / (abs(scale) + EPSILON)


# %% z-score


class ZScoreSettings(Settings):
    def __init__(self):
        pass


@NORMALIZERS.register
class ZScore(SignalNormalizer):
    name = "zscore"
    label = "Z-score (mean / SD)"
    description = (
        "Classic standardisation. The SD is dominated by whatever is most "
        "energetic in the trace, so on a noisy recording the R wave can end up "
        "at a very different z than on a clean one - robust scaling is usually "
        "the better choice for ECG."
    )
    Settings = ZScoreSettings

    @staticmethod
    def apply(voltage, fs, settings):
        v = numpy.asarray(voltage, dtype=float)
        return (v - v.mean()) / (v.std() + EPSILON)


# %% robust (median / MAD)


class RobustSettings(Settings):
    def __init__(self):
        pass


@NORMALIZERS.register
class RobustScale(SignalNormalizer):
    name = "robust"
    label = "Robust (median / MAD)"
    description = (
        "Median-centred, scaled by the median absolute deviation (x1.4826, so "
        "it matches SD for gaussian noise). The scale is set by the baseline "
        "rather than by the R waves, so R peaks land at a large multiple of 1 - "
        "a good scale for noise-referenced thresholds."
    )
    Settings = RobustSettings

    @staticmethod
    def apply(voltage, fs, settings):
        v = numpy.asarray(voltage, dtype=float)
        centre = numpy.median(v)
        mad = numpy.median(numpy.abs(v - centre)) * 1.4826
        return (v - centre) / (mad + EPSILON)


# %% unit max


class UnitMaxSettings(Settings):
    def __init__(self):
        pass


@NORMALIZERS.register
class UnitMax(SignalNormalizer):
    name = "unit_max"
    label = "Unit max (|v| <= 1)"
    description = (
        "Divides by the largest absolute value. Simple, but a single motion "
        "artifact sets the scale for the whole recording - included mainly as a "
        "cautionary control."
    )
    Settings = UnitMaxSettings

    @staticmethod
    def apply(voltage, fs, settings):
        v = numpy.asarray(voltage, dtype=float)
        return v / (numpy.abs(v).max() + EPSILON)


# %% rolling robust (tracks amplitude drift)


class RollingRobustSettings(Settings):
    def __init__(self):
        self.window_s = 10.0
        self.percentile = 99.0
        self.min_scale = 1e-6


@NORMALIZERS.register
class RollingRobust(SignalNormalizer):
    name = "rolling_robust"
    label = "Rolling robust scale (drift tracking)"
    description = (
        "Percentile scaling recomputed in a rolling window, so R waves stay near "
        "1.0 even as their amplitude drifts. This hands a fixed-threshold "
        "detector an adaptive threshold for free. WARNING: it also erases genuine "
        "changes in R amplitude - unusable if R_amplitude is an outcome measure, "
        "and it will rescale the neighbourhood of a large artifact."
    )
    Settings = RollingRobustSettings

    @staticmethod
    def apply(voltage, fs, settings):
        v = pandas.Series(numpy.asarray(voltage, dtype=float))
        window = max(3, int(round(settings.window_s * fs)))
        centre = v.rolling(window, center=True, min_periods=1).median()
        ceiling = v.rolling(window, center=True, min_periods=1).quantile(
            settings.percentile / 100.0
        )
        scale = (ceiling - centre).abs().clip(lower=settings.min_scale)
        return ((v - centre) / scale).to_numpy()
