# -*- coding: utf-8 -*-
"""
Scoring and agreement metrics for comparing beat callers.

Two detectors never agree to the sample, so everything here is built on a
tolerance-window matcher: a test beat matches a reference beat if it is the
nearest unclaimed reference beat within +/- ``tolerance`` seconds.

A "reference" can be:
  * another pipeline (pick your current production combo),
  * manual annotations made in the comparison window (the eventual gold
    standard), or
  * an n-of-m consensus across the enabled pipelines (useful before any manual
    annotation exists).

written for Physiology Analysis Tools (C) 2024
"""

__version__ = "0.0.1"

import numpy
import pandas

DEFAULT_TOLERANCE = 0.025  # seconds; +/- 25 ms


def _as_ts(obj):
    """Accept a beat DataFrame, a Series, or a bare array of timestamps."""
    if isinstance(obj, pandas.DataFrame):
        if obj.empty or "ts" not in obj.columns:
            return numpy.array([], dtype=float)
        values = obj["ts"].to_numpy(dtype=float)
    else:
        values = numpy.asarray(obj, dtype=float)
    return numpy.sort(values[~numpy.isnan(values)])


def match_beats(reference, test, tolerance=DEFAULT_TOLERANCE):
    """
    One-to-one nearest matching inside a tolerance window.

    Returns
    -------
    matches : list of (ref_index, test_index)
    unmatched_ref : numpy array of reference indices (false negatives)
    unmatched_test : numpy array of test indices (false positives)
    """
    ref = _as_ts(reference)
    tst = _as_ts(test)

    if ref.size == 0 or tst.size == 0:
        return [], numpy.arange(ref.size), numpy.arange(tst.size)

    # candidate pairs: for each test beat, the nearest reference beat
    insert = numpy.searchsorted(ref, tst)
    candidates = []
    for t_i, pos in enumerate(insert):
        for r_i in (pos - 1, pos):
            if 0 <= r_i < ref.size:
                delta = abs(tst[t_i] - ref[r_i])
                if delta <= tolerance:
                    candidates.append((delta, t_i, r_i))

    # greedy: closest pairs claim each other first
    candidates.sort()
    ref_taken = set()
    test_taken = set()
    matches = []
    for _, t_i, r_i in candidates:
        if t_i in test_taken or r_i in ref_taken:
            continue
        test_taken.add(t_i)
        ref_taken.add(r_i)
        matches.append((r_i, t_i))

    matches.sort()
    unmatched_ref = numpy.array(
        [i for i in range(ref.size) if i not in ref_taken], dtype=int
    )
    unmatched_test = numpy.array(
        [i for i in range(tst.size) if i not in test_taken], dtype=int
    )
    return matches, unmatched_ref, unmatched_test


def score(reference, test, tolerance=DEFAULT_TOLERANCE):
    """Detection performance of ``test`` against ``reference``."""
    ref = _as_ts(reference)
    tst = _as_ts(test)
    matches, missed, extra = match_beats(ref, tst, tolerance)

    tp = len(matches)
    fn = len(missed)
    fp = len(extra)

    if tp:
        errors = numpy.array([tst[t] - ref[r] for r, t in matches])
    else:
        errors = numpy.array([])

    sensitivity = tp / (tp + fn) if (tp + fn) else numpy.nan
    ppv = tp / (tp + fp) if (tp + fp) else numpy.nan
    f1 = (
        2 * sensitivity * ppv / (sensitivity + ppv)
        if sensitivity and ppv and not numpy.isnan(sensitivity + ppv)
        else numpy.nan
    )

    return {
        "n_reference": ref.size,
        "n_test": tst.size,
        "TP": tp,
        "FP": fp,
        "FN": fn,
        "sensitivity": sensitivity,
        "PPV": ppv,
        "F1": f1,
        "mean_error_ms": float(numpy.mean(errors) * 1000) if errors.size else numpy.nan,
        "sd_error_ms": float(numpy.std(errors) * 1000) if errors.size else numpy.nan,
        "abs_error_ms": (
            float(numpy.mean(numpy.abs(errors)) * 1000) if errors.size else numpy.nan
        ),
        "mean_HR": float(60.0 / numpy.mean(numpy.diff(tst))) if tst.size > 1 else numpy.nan,
    }


def score_table(results, reference_ts, tolerance=DEFAULT_TOLERANCE, labels=None):
    """
    Score every pipeline result against one reference.

    ``results`` : {uid: PipelineResult}
    ``labels``  : optional {uid: label}
    """
    rows = []
    for uid, result in results.items():
        row = {"uid": uid, "pipeline": (labels or {}).get(uid, uid)}
        if not result.ok:
            row["error"] = result.error
        row.update(score(reference_ts, result.beats, tolerance))
        row["runtime_s"] = round(result.runtime_s, 3)
        rows.append(row)
    return pandas.DataFrame(rows)


def pairwise_f1(results, tolerance=DEFAULT_TOLERANCE, labels=None):
    """Symmetric-ish F1 matrix - how much does each pipeline agree with each other?"""
    uids = list(results)
    names = [(labels or {}).get(u, u) for u in uids]
    matrix = pandas.DataFrame(index=names, columns=names, dtype=float)
    for i, a in enumerate(uids):
        for j, b in enumerate(uids):
            if i == j:
                matrix.iloc[i, j] = 1.0
            else:
                matrix.iloc[i, j] = score(
                    results[a].beats, results[b].beats, tolerance
                )["F1"]
    return matrix


def consensus_beats(beat_sets, tolerance=DEFAULT_TOLERANCE, min_votes=None):
    """
    Cluster beats across detectors and keep clusters supported by at least
    ``min_votes`` detectors (default: a simple majority). Returns the cluster
    mean timestamps - a serviceable stand-in reference until manual annotation
    exists.
    """
    sets = [_as_ts(b) for b in beat_sets if _as_ts(b).size]
    if not sets:
        return numpy.array([], dtype=float)
    if min_votes is None:
        min_votes = len(sets) // 2 + 1

    stamped = numpy.concatenate(
        [numpy.column_stack([s, numpy.full(s.size, i)]) for i, s in enumerate(sets)]
    )
    stamped = stamped[numpy.argsort(stamped[:, 0])]

    consensus = []
    cluster_times = [stamped[0, 0]]
    cluster_voters = {int(stamped[0, 1])}

    for ts, source in stamped[1:]:
        if ts - cluster_times[0] <= 2 * tolerance:
            cluster_times.append(ts)
            cluster_voters.add(int(source))
        else:
            if len(cluster_voters) >= min_votes:
                consensus.append(float(numpy.mean(cluster_times)))
            cluster_times = [ts]
            cluster_voters = {int(source)}

    if len(cluster_voters) >= min_votes:
        consensus.append(float(numpy.mean(cluster_times)))

    return numpy.array(consensus, dtype=float)


def disagreement_times(results, reference_ts, tolerance=DEFAULT_TOLERANCE):
    """
    Timestamps where at least one pipeline disagrees with the reference -
    a false positive (extra beat) or a false negative (missed beat).

    Returns a DataFrame (ts, kind, pipeline_uid) sorted by time. This is the
    review queue: it is where your eyes are actually worth spending.
    """
    ref = _as_ts(reference_ts)
    rows = []
    for uid, result in results.items():
        tst = _as_ts(result.beats)
        _, missed, extra = match_beats(ref, tst, tolerance)
        rows += [{"ts": ref[i], "kind": "missed", "pipeline_uid": uid} for i in missed]
        rows += [{"ts": tst[i], "kind": "extra", "pipeline_uid": uid} for i in extra]

    if not rows:
        return pandas.DataFrame(columns=["ts", "kind", "pipeline_uid"])
    return pandas.DataFrame(rows).sort_values("ts").reset_index(drop=True)


def cluster_disagreements(disagreements, gap=0.5):
    """Collapse the disagreement list into review windows (unique times to visit)."""
    if disagreements.empty:
        return numpy.array([], dtype=float)
    times = disagreements["ts"].to_numpy(dtype=float)
    windows = [times[0]]
    for t in times[1:]:
        if t - windows[-1] > gap:
            windows.append(t)
    return numpy.array(windows)
