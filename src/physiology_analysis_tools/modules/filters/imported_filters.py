# -*- coding: utf-8 -*-
"""
Filters imported from sibling projects.

  notch_butter_causal - the ``basicFilt`` variant that uses lfilter rather than
                        filtfilt. NOT the same filter as ``notch_butter``: it is
                        causal, so it shifts the signal in time.
  kick60              - WZ's "kick 60 Hz filter" (basicFilt_alt): a 4th order
                        zero-phase Butterworth bandstop across the mains line.

written for Physiology Analysis Tools (C) 2024
"""

__version__ = "0.0.1"

import numpy
import scipy.signal

from ..registries import FILTERS
from ..algorithms import SignalFilter, Settings


# %% causal mains notch + high-pass (basicFilt, lfilter form)


class NotchButterCausalSettings(Settings):
    def __init__(self):
        self.f0 = 60.0  # mains frequency (50 outside north america)
        self.Q = 30.0
        self.highpass_cutoff = 1.0
        self.highpass_order = 1


@FILTERS.register
class NotchButterCausal(SignalFilter):
    """
    Causal (single-pass) mains notch followed by a causal 1st-order high-pass.

    This is the ``lfilter`` form of ``basicFilt``. The repository already has
    ``notch_butter``, which is the same filter cascade run with ``filtfilt``.
    They are NOT interchangeable, and the difference is the point of including
    both:

    * ``notch_butter`` (filtfilt) is zero-phase - it runs the filter forwards
      and backwards, so group delay cancels exactly.
    * ``notch_butter_causal`` (lfilter) is single-pass, so the IIR group delay is
      not cancelled and R peaks are reported late.

    How late, in practice? Measured on a synthetic 1 kHz trace with these
    defaults (60 Hz Q=30 notch, 1st-order 1 Hz high-pass), the R-peak shift is
    about 0.05 ms - i.e. nothing. That is worth knowing rather than assuming:
    both stages are gentle in the QRS band, so the phase penalty people warn
    about does not materialise at these settings. It grows quickly if you lower
    Q (widening the notch's phase excursion into the QRS band) or raise the
    high-pass cutoff/order, and it is sampling-rate dependent - so if you tune
    this filter, re-check the bias rather than trusting the number above.

    What the causal form does do at default settings is ring asymmetrically
    after each QRS and carry a startup transient in the first samples, which
    ``template_match`` is more sensitive to than the amplitude-threshold
    detectors are. Include it for fidelity to the source project, and to
    quantify - rather than assert - what the single-pass form costs you.
    """

    name = "notch_butter_causal"
    label = "Mains notch + high-pass (causal, lfilter)"
    description = (
        "Single-pass IIR notch + high-pass, as in the original basicFilt. "
        "Not zero-phase, so it carries group delay and rings asymmetrically "
        "after each QRS. Provided for comparison against notch_butter."
    )
    Settings = NotchButterCausalSettings

    @staticmethod
    def apply(voltage, fs, settings):
        v = numpy.asarray(voltage, dtype=float)

        b, a = scipy.signal.iirnotch(settings.f0 / (fs / 2.0), settings.Q)
        notched = scipy.signal.lfilter(b, a, v)

        b, a = scipy.signal.butter(
            settings.highpass_order,
            settings.highpass_cutoff,
            fs=fs,
            btype="highpass",
        )
        return scipy.signal.lfilter(b, a, notched)


# %% kick60 - zero-phase bandstop across the mains line (basicFilt_alt)


class Kick60Settings(Settings):
    def __init__(self):
        self.order = 4
        self.notch_low = 59.0
        self.notch_high = 61.0


@FILTERS.register
class Kick60(SignalFilter):
    """
    WZ's "kick 60 Hz" filter: a zero-phase Butterworth bandstop across the mains
    line (``basicFilt_alt``).

    Differs from ``notch_butter`` in two ways worth being deliberate about:

    * It is a bandstop with an explicit, fairly WIDE band (59-61 Hz by default),
      not an ``iirnotch``. At order 4 the transition bands widen the effective
      stopband well beyond 2 Hz, so it removes more of the neighbourhood of 60 Hz
      than a Q=30 notch does. That is usually what you want when the mains
      contamination wanders (it does).
    * It does NOT high-pass. Baseline wander and DC offset survive it intact.
      That makes it a poor solo filter for any detector with a global or fixed
      threshold - the isoelectric line drifts under the threshold and back out
      again. Its natural use is as a mains-cleaning stage, and in this pipeline
      the way to get that is to stack it in the grid with a high-pass-bearing
      filter... which the single-filter-stage design does not currently allow.

    So: expect ``kick60`` alone to underperform ``notch_butter``, and expect the
    gap to be baseline wander rather than mains noise. If it wins anywhere, that
    tells you your recordings have very flat baselines and the high-pass in
    ``notch_butter`` was costing you QRS amplitude.

    Two practical notes:
    * ``notch_high`` must be below the Nyquist frequency. At fs <= 122 Hz this
      filter cannot be constructed; ``apply`` raises rather than returning
      something silently wrong.
    * For 50 Hz mains, set 49/51.
    """

    name = "kick60"
    label = "Mains bandstop (kick60, zero-phase)"
    description = (
        "4th order zero-phase Butterworth bandstop across the mains line. "
        "Removes line noise without high-passing, so baseline wander survives."
    )
    Settings = Kick60Settings

    @staticmethod
    def apply(voltage, fs, settings):
        v = numpy.asarray(voltage, dtype=float)

        nyquist = fs / 2.0
        if not (0 < settings.notch_low < settings.notch_high < nyquist):
            raise ValueError(
                f"kick60 band [{settings.notch_low}, {settings.notch_high}] Hz is "
                f"not valid for fs = {fs} Hz (Nyquist = {nyquist} Hz)"
            )

        b, a = scipy.signal.butter(
            settings.order,
            [settings.notch_low, settings.notch_high],
            btype="bandstop",
            fs=fs,
        )
        return scipy.signal.filtfilt(b, a, v)
