"""Direct tests for the beat-shape analysis in
physiology_analysis_tools.modules.ml_tools - the KDE-clipped epocher and the
adaptive DBSCAN eps that the unsupervised ("abnormal cluster") arrhythmia
method is built on.

These sit alongside the end-to-end coverage in test_arrhythmia_api.py: that
proves the pipeline works through the API, these pin the algorithm's own
behaviour where an end-to-end assertion couldn't show why it broke.
"""

import numpy
import pandas as pd
import pytest

from physiology_analysis_tools.modules import ml_tools


def _synthetic_recording(rr_intervals, sample_rate=1000, beat_amplitude=1.0):
    """A square-ish ECG-like trace with one spike per beat, plus the beat
    table that beat detection would have produced for it."""
    timestamps = numpy.cumsum([1.0] + list(rr_intervals))
    duration = timestamps[-1] + 1.0
    n = int(duration * sample_rate)
    time = numpy.arange(n) / sample_rate
    voltage = numpy.random.default_rng(0).normal(0, 0.01, n)
    for ts in timestamps:
        idx = int(round(ts * sample_rate))
        voltage[idx] += beat_amplitude
        voltage[idx - 1] += beat_amplitude / 2
        voltage[idx + 1] += beat_amplitude / 2
    signal_df = pd.DataFrame({"ts": time, "ecg": voltage})
    beat_df = pd.DataFrame({"ts": [time[int(round(t * sample_rate))] for t in timestamps]})
    return signal_df, beat_df


class TestExtractFirstModalityKde:
    def test_keeps_only_the_dominant_rr_mode(self):
        # 100 normal beats around 0.1s, plus a scattered tail of long pauses
        # around 0.4s - the tail is exactly what drags a plain median upward.
        rng = numpy.random.default_rng(0)
        normal = rng.normal(0.10, 0.004, 100)
        pauses = rng.normal(0.40, 0.010, 25)
        rr = numpy.concatenate([normal, pauses])

        kept = ml_tools.extract_first_modality_kde(rr)

        assert len(kept) < len(rr)
        assert kept.max() < 0.2
        # The whole point: the median of the first mode reflects the normal
        # beat, while the median over everything is pulled off it.
        assert numpy.median(kept) == pytest.approx(0.10, abs=0.01)
        assert numpy.median(rr) > numpy.median(kept)

    def test_unimodal_data_keeps_the_mode_intact(self):
        rr = numpy.random.default_rng(1).normal(0.15, 0.005, 80)

        kept = ml_tools.extract_first_modality_kde(rr)

        # With a single mode there is no second population to cut away, so
        # the retained values must still describe the same distribution. (The
        # narrow KDE bandwidth means the cut can still trim the mode's upper
        # tail, so this asserts the estimate is preserved, not the count.)
        assert len(kept) > len(rr) / 2
        assert numpy.median(kept) == pytest.approx(numpy.median(rr), abs=0.005)

    def test_perfectly_regular_rr_intervals_do_not_raise(self):
        # Identical intervals give a singular covariance, which gaussian_kde
        # cannot fit - every interval is already the one and only mode.
        rr = numpy.full(20, 0.5)

        kept = ml_tools.extract_first_modality_kde(rr)

        assert len(kept) == 20

    def test_too_few_values_returned_as_is(self):
        rr = numpy.array([0.12])
        assert list(ml_tools.extract_first_modality_kde(rr)) == [0.12]


class TestBeatepocherKdeClippedRrSmooth:
    def test_epochs_are_fixed_length_and_keyed_by_timestamp(self):
        signal_df, beat_df = _synthetic_recording([0.12] * 20)

        epochs = ml_tools.beatepocher_kde_clipped_rr_smooth(
            signal_df, beat_df, voltage_column="ecg", time_column="ts", beat_length=64
        )

        assert len(epochs) == len(beat_df)
        assert all(len(epoch) == 64 for epoch in epochs.values())
        # Keyed by rounded timestamp, which is what lets call_arrhythmias_PCA
        # map labels back onto the beat table by ts rather than row position.
        expected_keys = {
            round(float(ts), ml_tools.TIMESTAMP_DECIMALS) for ts in beat_df["ts"]
        }
        assert set(epochs.keys()) == expected_keys

    def test_beat_length_is_independent_of_the_window_actually_sliced(self):
        signal_df, beat_df = _synthetic_recording([0.12] * 20)

        narrow = ml_tools.beatepocher_kde_clipped_rr_smooth(
            signal_df, beat_df, voltage_column="ecg", time_column="ts",
            beat_window=1, beat_length=128,
        )
        wide = ml_tools.beatepocher_kde_clipped_rr_smooth(
            signal_df, beat_df, voltage_column="ecg", time_column="ts",
            beat_window=2, beat_length=128,
        )

        # Different amounts of signal, resampled to the same length so the
        # epochs stay directly comparable in the clustering step.
        assert all(len(e) == 128 for e in narrow.values())
        assert all(len(e) == 128 for e in wide.values())
        first_key = min(narrow)
        assert not numpy.allclose(narrow[first_key], wide[first_key])

    def test_rr_window_is_clipped_so_a_slow_recording_cannot_explode_the_window(self):
        # Beats 0.5s apart, but max_rr caps the window at 0.1667s worth.
        signal_df, beat_df = _synthetic_recording([0.5] * 12)

        clipped = ml_tools.beatepocher_kde_clipped_rr_smooth(
            signal_df, beat_df, voltage_column="ecg", time_column="ts",
            max_rr=0.1667,
        )
        unclipped = ml_tools.beatepocher_kde_clipped_rr_smooth(
            signal_df, beat_df, voltage_column="ecg", time_column="ts",
            max_rr=5.0,
        )

        # Both epoch every beat here, but the clipped one sliced a much
        # narrower window - visible as a different normalised shape.
        assert len(clipped) == len(unclipped) == len(beat_df)
        key = min(clipped)
        assert not numpy.allclose(clipped[key], unclipped[key])

    def test_beats_too_close_to_the_signal_edges_are_skipped(self):
        signal_df, beat_df = _synthetic_recording([0.12] * 10)

        epochs = ml_tools.beatepocher_kde_clipped_rr_smooth(
            signal_df, beat_df, voltage_column="ecg", time_column="ts",
            beat_window=30,
        )

        # A window this wide runs off the start/end of the recording for the
        # outermost beats; they're dropped rather than zero-padded.
        assert len(epochs) < len(beat_df)


class TestAutoEps:
    def test_scales_with_the_spread_of_the_data(self):
        rng = numpy.random.default_rng(2)
        tight = rng.normal(0, 0.1, (200, 2))
        loose = tight * 10

        eps_tight = ml_tools.auto_eps(tight, min_samples=10)
        eps_loose = ml_tools.auto_eps(loose, min_samples=10)

        # The same relative structure at 10x the scale must give ~10x the eps
        # - that scale-following is exactly why a fixed eps cannot work
        # across recordings.
        assert eps_loose == pytest.approx(eps_tight * 10, rel=0.01)

    def test_higher_percentile_gives_a_larger_radius(self):
        points = numpy.random.default_rng(3).normal(0, 1, (200, 2))

        assert ml_tools.auto_eps(points, 10, percentile=50) < ml_tools.auto_eps(
            points, 10, percentile=95
        )


class TestBeatClusterer:
    def _epochs_with_outliers(self):
        """40 near-identical beats plus 3 clearly differently-shaped ones."""
        rng = numpy.random.default_rng(4)
        base = numpy.sin(numpy.linspace(0, numpy.pi, 64))
        epochs = {}
        for i in range(40):
            epochs[float(i)] = ml_tools.detrend_normalise(
                base + rng.normal(0, 0.01, 64)
            )
        odd = numpy.sin(numpy.linspace(0, 4 * numpy.pi, 64))
        for i in range(40, 43):
            epochs[float(i)] = ml_tools.detrend_normalise(
                odd + rng.normal(0, 0.01, 64)
            )
        return epochs

    def test_auto_eps_separates_odd_shaped_beats_from_the_normal_group(self):
        epochs = self._epochs_with_outliers()

        labels = ml_tools.beat_clusterer(epochs, eps_auto=True, min_samples=10)

        normal = [labels[float(i)] for i in range(40)]
        odd = [labels[float(i)] for i in range(40, 43)]
        # The same-shaped beats form the main cluster (label 0) and the 3 odd
        # ones are kept out of it - which is what abn_cluster reports as
        # abnormal. A few normal beats on the edge of the group may also fall
        # out, so this asserts the clear majority, not every single one.
        assert sum(1 for label in normal if label == 0) > 30
        assert all(label != 0 for label in odd)

    def test_min_samples_is_capped_so_a_short_recording_still_clusters(self):
        # min_samples far larger than the number of beats would otherwise
        # make DBSCAN label every single beat an outlier.
        epochs = {k: v for k, v in list(self._epochs_with_outliers().items())[:12]}

        labels = ml_tools.beat_clusterer(epochs, eps_auto=True, min_samples=500)

        assert any(label == 0 for label in labels.values())

    def test_fewer_than_two_epochs_raises_a_clear_error(self):
        with pytest.raises(ValueError, match="at least 2 usable beat epochs"):
            ml_tools.beat_clusterer({1.0: numpy.zeros(64)}, eps_auto=True)
