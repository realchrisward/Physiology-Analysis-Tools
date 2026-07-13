# -*- coding: utf-8 -*-
"""
Second wave of filters.

  bessel_smoothing    - wraps the existing apply_smoothing_filter (HP + Bessel LP)
  autoinvert_highpass - wraps the existing ml_tools.basic_filter (auto polarity)
  savgol_detrend      - Savitzky-Golay baseline subtraction
  wavelet_denoise     - stationary wavelet shrinkage (needs PyWavelets)
"""

__version__ = "0.0.1"

import numpy
import pandas
import scipy.signal

from . import FILTERS
from ..algorithms import Settings, SignalFilter
from .. import ml_tools
from ..signal_converters import signal_filters_and_analyzers


# %% bessel smoothing (existing apply_smoothing_filter)


class BesselSmoothingSettings(Settings):
    def __init__(self):
        self.high_pass = 0.1
        self.high_pass_order = 2
        self.low_pass = 50.0
        self.low_pass_order = 10


@FILTERS.register
class BesselSmoothing(SignalFilter):
    name = "bessel_smoothing"
    label = "High-pass + Bessel low-pass"
    description = (
        "signal_filters_and_analyzers.apply_smoothing_filter. The Bessel low-pass "
        "has a near-linear phase response, so it attenuates noise without "
        "smearing the QRS in time the way a steep Butterworth low-pass does. "
        "NOTE the default 50 Hz low-pass is a flow-signal setting - for mouse ECG "
        "it will blunt the R wave badly; raise it (200-300 Hz) before trusting it."
    )
    Settings = BesselSmoothingSettings

    @staticmethod
    def apply(voltage, fs, settings):
        voltage = numpy.asarray(voltage, dtype=float)
        frame = pandas.DataFrame(
            {"ts": numpy.arange(voltage.size) / fs, "signal": voltage}
        )
        return numpy.asarray(
            signal_filters_and_analyzers.apply_smoothing_filter(
                frame,
                "signal",
                high_pass=settings.high_pass,
                high_pass_order=settings.high_pass_order,
                low_pass=min(settings.low_pass, fs / 2 * 0.99),
                low_pass_order=settings.low_pass_order,
            ),
            dtype=float,
        )


# %% auto-inverting high pass (existing ml_tools.basic_filter)


class AutoInvertHighpassSettings(Settings):
    def __init__(self):
        self.order = 2
        self.cutoff = 5.0


@FILTERS.register
class AutoInvertHighpass(SignalFilter):
    name = "autoinvert_highpass"
    label = "Butterworth high-pass + auto-invert"
    description = (
        "ml_tools.basic_filter: a Butterworth high-pass that additionally decides "
        "the polarity of the trace from the data (comparing mean positive vs mean "
        "negative peak height) and flips it if the R wave points down. The only "
        "place in the codebase where inversion is inferred rather than configured; "
        "pair it with a detector whose own 'invert' setting is left False."
    )
    Settings = AutoInvertHighpassSettings

    @staticmethod
    def apply(voltage, fs, settings):
        return numpy.asarray(
            ml_tools.basic_filter(
                settings.order,
                numpy.asarray(voltage, dtype=float),
                fs=fs,
                cutoff=settings.cutoff,
                output="sos",
            ),
            dtype=float,
        )


# %% savitzky-golay detrend


class SavgolDetrendSettings(Settings):
    def __init__(self):
        self.window_ms = 150.0
        self.polyorder = 3


@FILTERS.register
class SavgolDetrend(SignalFilter):
    name = "savgol_detrend"
    label = "Savitzky-Golay detrend"
    description = (
        "Fits a rolling low-order polynomial baseline and subtracts it. Follows "
        "curved baseline wander more closely than a linear-phase high-pass while "
        "leaving the QRS amplitude essentially intact (the QRS is too brief for "
        "the polynomial to track)."
    )
    Settings = SavgolDetrendSettings

    @staticmethod
    def apply(voltage, fs, settings):
        voltage = numpy.asarray(voltage, dtype=float)
        window = int(round(settings.window_ms / 1000.0 * fs))
        if window % 2 == 0:
            window += 1
        window = max(window, int(settings.polyorder) + 2)
        if window % 2 == 0:
            window += 1
        baseline = scipy.signal.savgol_filter(
            voltage, window, int(settings.polyorder)
        )
        return voltage - baseline


# %% wavelet denoising (optional dependency: PyWavelets)


try:
    import pywt

    class WaveletDenoiseSettings(Settings):
        def __init__(self):
            self.wavelet = "db4"
            self.level = 5
            self.drop_approximation = True  # removes baseline wander
            self.threshold_scale = 1.0  # universal threshold multiplier

    @FILTERS.register
    class WaveletDenoise(SignalFilter):
        name = "wavelet_denoise"
        label = "Wavelet denoise (SWT)"
        description = (
            "Stationary wavelet decomposition with soft universal (VisuShrink) "
            "thresholding; optionally discards the coarsest approximation to "
            "remove baseline wander. Unlike an IIR filter it is shift-invariant "
            "and does not ring around the QRS, at the cost of a wavelet choice "
            "that is itself a tunable."
        )
        Settings = WaveletDenoiseSettings

        @staticmethod
        def apply(voltage, fs, settings):
            voltage = numpy.asarray(voltage, dtype=float)

            # SWT requires a length divisible by 2**level
            level = int(settings.level)
            block = 2 ** level
            pad = (-voltage.size) % block
            padded = numpy.pad(voltage, (0, pad), mode="edge")

            coefficients = pywt.swt(
                padded, settings.wavelet, level=level, trim_approx=True, norm=True
            )
            approximation, details = coefficients[0], list(coefficients[1:])

            sigma = numpy.median(numpy.abs(details[-1])) / 0.6745
            threshold = (
                settings.threshold_scale
                * sigma
                * numpy.sqrt(2 * numpy.log(max(padded.size, 2)))
            )
            details = [
                pywt.threshold(d, threshold, mode="soft") for d in details
            ]

            if settings.drop_approximation:
                approximation = numpy.zeros_like(approximation)

            reconstructed = pywt.iswt(
                [approximation] + details,
                settings.wavelet,
                norm=True,
            )
            return numpy.asarray(reconstructed[: voltage.size], dtype=float)

except ImportError:  # pragma: no cover - optional dependency
    print(
        "wavelet_denoise filter unavailable - install PyWavelets to enable it "
        "(pip install PyWavelets)"
    )
