# -*- coding: utf-8 -*-
"""
Starter set of digital filters.

Each class is registered under a stable ``name``; that name is what gets written
into reports and saved pipeline configs, so do not rename one casually.
"""

__version__ = "0.0.1"

import numpy
import scipy.signal

from ..registries import FILTERS
from ..algorithms import Settings, SignalFilter


# %% no filter (control condition - always worth including in a comparison)


class PassthroughSettings(Settings):
    def __init__(self):
        pass


@FILTERS.register
class Passthrough(SignalFilter):
    name = "passthrough"
    label = "None (raw signal)"
    description = "No filtering. Control condition for the comparison grid."
    Settings = PassthroughSettings

    @staticmethod
    def apply(voltage, fs, settings):
        return numpy.asarray(voltage, dtype=float)


# %% butterworth high pass (equivalent to heartbeat_detection.basic_filter)


class ButterHighpassSettings(Settings):
    def __init__(self):
        self.order = 2
        self.cutoff = 5.0


@FILTERS.register
class ButterHighpass(SignalFilter):
    name = "butter_highpass"
    label = "Butterworth high-pass"
    description = (
        "Zero-phase Butterworth high-pass (sosfiltfilt). Removes baseline "
        "wander / respiratory drift. Matches heartbeat_detection.basic_filter."
    )
    Settings = ButterHighpassSettings

    @staticmethod
    def apply(voltage, fs, settings):
        sos = scipy.signal.butter(
            settings.order, settings.cutoff, fs=fs, btype="highpass", output="sos"
        )
        return scipy.signal.sosfiltfilt(sos, numpy.asarray(voltage, dtype=float))


# %% butterworth band pass


class ButterBandpassSettings(Settings):
    def __init__(self):
        self.order = 2
        self.low_cutoff = 5.0
        self.high_cutoff = 100.0


@FILTERS.register
class ButterBandpass(SignalFilter):
    name = "butter_bandpass"
    label = "Butterworth band-pass"
    description = (
        "Zero-phase Butterworth band-pass. The classic QRS-enhancing band; for "
        "mouse ECG the R wave sits considerably higher than in human ECG, so "
        "the upper cutoff usually needs to be well above the 15-40 Hz used "
        "clinically."
    )
    Settings = ButterBandpassSettings

    @staticmethod
    def apply(voltage, fs, settings):
        nyquist = fs / 2.0
        high = min(settings.high_cutoff, nyquist * 0.99)
        sos = scipy.signal.butter(
            settings.order,
            [settings.low_cutoff, high],
            fs=fs,
            btype="bandpass",
            output="sos",
        )
        return scipy.signal.sosfiltfilt(sos, numpy.asarray(voltage, dtype=float))


# %% notch + butterworth (equivalent to signal_filters_and_analyzers.basicFilt)


class NotchButterSettings(Settings):
    def __init__(self):
        self.f0 = 60.0  # mains frequency (50 outside north america)
        self.Q = 30.0
        self.highpass_cutoff = 1.0
        self.highpass_order = 1


@FILTERS.register
class NotchButter(SignalFilter):
    name = "notch_butter"
    label = "Mains notch + high-pass"
    description = (
        "IIR notch at the mains frequency followed by a high-pass. Equivalent "
        "to signal_filters_and_analyzers.basicFilt."
    )
    Settings = NotchButterSettings

    @staticmethod
    def apply(voltage, fs, settings):
        voltage = numpy.asarray(voltage, dtype=float)
        b, a = scipy.signal.iirnotch(settings.f0 / (fs / 2.0), settings.Q)
        notched = scipy.signal.filtfilt(b, a, voltage)
        b, a = scipy.signal.butter(
            settings.highpass_order,
            settings.highpass_cutoff / (fs / 2.0),
            btype="highpass",
        )
        return scipy.signal.filtfilt(b, a, notched)


# %% rolling median baseline removal


class MedianBaselineSettings(Settings):
    def __init__(self):
        self.window_ms = 200.0


@FILTERS.register
class MedianBaseline(SignalFilter):
    name = "median_baseline"
    label = "Median baseline subtraction"
    description = (
        "Subtracts a rolling median baseline. Non-linear, so it removes drift "
        "without the phase/overshoot artifacts a high-order IIR can introduce "
        "around the QRS."
    )
    Settings = MedianBaselineSettings

    @staticmethod
    def apply(voltage, fs, settings):
        voltage = numpy.asarray(voltage, dtype=float)
        window = int(round(settings.window_ms / 1000.0 * fs))
        if window % 2 == 0:
            window += 1
        window = max(window, 3)
        baseline = scipy.signal.medfilt(voltage, kernel_size=window)
        return voltage - baseline
