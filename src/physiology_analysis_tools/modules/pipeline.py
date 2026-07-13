# -*- coding: utf-8 -*-
"""
Pipeline = one (filter, normaliser, beat caller) combination with its settings.

A Pipeline is the unit of comparison. Running a grid of pipelines over a signal
gives one PipelineResult each, all of which can then be scored against one
another (or against manual annotations) by modules.comparison.

Stages
------
    raw voltage -> FILTER -> NORMALISE -> BEAT CALLER -> beat DataFrame

The normalise stage is what makes detectors carrying absolute thresholds
comparable across filters; see modules.normalizers.

written for Physiology Analysis Tools (C) 2024
"""

__version__ = "0.0.2"

import hashlib
import time as _time
from dataclasses import dataclass

import numpy
import pandas

from .algorithms import sampling_frequency
from .beatcallers import BEATCALLERS
from .filters import FILTERS
from .normalizers import NORMALIZERS

# distinct plot colours, cycled for new pipelines
PIPELINE_COLORS = [
    (0, 114, 189),
    (217, 83, 25),
    (119, 172, 48),
    (126, 47, 142),
    (237, 177, 32),
    (77, 190, 238),
    (162, 20, 47),
    (0, 0, 0),
]

DEFAULT_NORMALIZER = "percentile_scale"


@dataclass
class PipelineResult:
    pipeline: "Pipeline"
    filtered: numpy.ndarray  # after the filter stage
    signal: numpy.ndarray  # after the normalise stage - what the detector saw
    beats: pandas.DataFrame
    runtime_s: float = 0.0
    error: str = None

    @property
    def ok(self):
        return self.error is None


@dataclass
class Pipeline:
    filter_name: str
    caller_name: str
    norm_name: str = DEFAULT_NORMALIZER
    filter_settings: object = None
    norm_settings: object = None
    caller_settings: object = None
    label: str = None
    color: tuple = (0, 114, 189)
    enabled: bool = True  # include in the next run
    visible: bool = True  # draw in the plots (independent of enabled)

    def __post_init__(self):
        if self.filter_settings is None:
            self.filter_settings = FILTERS[self.filter_name].Settings()
        if self.norm_settings is None:
            self.norm_settings = NORMALIZERS[self.norm_name].Settings()
        if self.caller_settings is None:
            self.caller_settings = BEATCALLERS[self.caller_name].Settings()
        if not self.label:
            self.label = self.default_label()

    # -- identity ----------------------------------------------------------
    def default_label(self):
        parts = [FILTERS[self.filter_name].label]
        if self.norm_name != "none":
            parts.append(NORMALIZERS[self.norm_name].label)
        parts.append(BEATCALLERS[self.caller_name].label)
        return " + ".join(parts)

    @property
    def filter_key(self):
        """Cache key for the filtered signal (shared across normalisers)."""
        return f"{self.filter_name}|{self.filter_settings.signature()}"

    @property
    def signal_key(self):
        """Cache key for the normalised signal (shared across beat callers)."""
        return f"{self.filter_key}||{self.norm_name}|{self.norm_settings.signature()}"

    @property
    def uid(self):
        raw = (
            f"{self.signal_key}||"
            f"{self.caller_name}|{self.caller_settings.signature()}"
        )
        return hashlib.sha1(raw.encode()).hexdigest()[:10]

    def as_record(self):
        """Flat dict for the report 'pipelines' sheet."""
        record = {
            "uid": self.uid,
            "label": self.label,
            "filter": self.filter_name,
            "normalizer": self.norm_name,
            "beatcaller": self.caller_name,
        }
        record.update(
            {f"filt.{k}": v for k, v in self.filter_settings.as_dict().items()}
        )
        record.update(
            {f"norm.{k}": v for k, v in self.norm_settings.as_dict().items()}
        )
        record.update(
            {f"beat.{k}": v for k, v in self.caller_settings.as_dict().items()}
        )
        return record

    def clone(self):
        return Pipeline(
            filter_name=self.filter_name,
            caller_name=self.caller_name,
            norm_name=self.norm_name,
            filter_settings=self.filter_settings.clone(),
            norm_settings=self.norm_settings.clone(),
            caller_settings=self.caller_settings.clone(),
            label=self.label,
            color=self.color,
            enabled=self.enabled,
            visible=self.visible,
        )

    # -- execution ---------------------------------------------------------
    def run(self, df, time_column, voltage_column, cache=None):
        """
        ``cache`` : optional dict shared across a grid run. Filtered and
        normalised signals are memoised in it, so an M x N x K grid performs
        only M filter passes and M x N normalisation passes.
        """
        started = _time.perf_counter()
        cache = {} if cache is None else cache

        time = numpy.asarray(df[time_column], dtype=float)
        voltage = numpy.asarray(df[voltage_column], dtype=float)
        fs = sampling_frequency(time)

        try:
            if self.filter_key in cache:
                filtered = cache[self.filter_key]
            else:
                filtered = FILTERS[self.filter_name].apply(
                    voltage, fs, self.filter_settings
                )
                cache[self.filter_key] = filtered

            if self.signal_key in cache:
                signal = cache[self.signal_key]
            else:
                signal = NORMALIZERS[self.norm_name].apply(
                    filtered, fs, self.norm_settings
                )
                cache[self.signal_key] = signal

            beats = BEATCALLERS[self.caller_name].call(
                signal, time, fs, self.caller_settings
            )
            return PipelineResult(
                pipeline=self,
                filtered=filtered,
                signal=signal,
                beats=beats.reset_index(drop=True),
                runtime_s=_time.perf_counter() - started,
            )
        except Exception as e:  # noqa: BLE001 - a bad combo must not kill the grid
            blank = numpy.full(voltage.shape, numpy.nan)
            return PipelineResult(
                pipeline=self,
                filtered=blank,
                signal=blank,
                beats=pandas.DataFrame(),
                runtime_s=_time.perf_counter() - started,
                error=f"{type(e).__name__}: {e}",
            )


def next_color(index):
    return PIPELINE_COLORS[index % len(PIPELINE_COLORS)]


def build_grid(filter_names, caller_names, norm_names=None):
    """Every combination of the supplied stages, at default settings."""
    norm_names = norm_names or [DEFAULT_NORMALIZER]
    pipelines = []
    i = 0
    for f in filter_names:
        for n in norm_names:
            for c in caller_names:
                pipelines.append(Pipeline(f, c, norm_name=n, color=next_color(i)))
                i += 1
    return pipelines


def run_pipelines(pipelines, df, time_column, voltage_column, progress=None):
    """
    Run every enabled pipeline, sharing one signal cache.

    ``progress`` : optional callable(done:int, total:int, label:str)
    """
    cache = {}
    results = {}
    active = [p for p in pipelines if p.enabled]
    for i, p in enumerate(active):
        if progress:
            progress(i, len(active), p.label)
        results[p.uid] = p.run(df, time_column, voltage_column, cache=cache)
    if progress:
        progress(len(active), len(active), "done")
    return results
