# -*- coding: utf-8 -*-
"""
Batch mode: run a grid of pipelines across a folder of recordings and aggregate.

Typical use
-----------
1. Build and tune a set of pipelines in the GUI comparison window, then press
   "Save pipeline config" to write a JSON config.
2. Run the whole folder headlessly:

       python -m physiology_analysis_tools.modules.batch \\
           --input  /data/cohort_A \\
           --output /results/cohort_A \\
           --config pipelines.json

3. Read `batch_comparison.xlsx`: `summary` ranks the pipelines pooled across
   every file; `per_file` shows where the ranking is unstable (which is usually
   the more informative sheet).

Ground truth
------------
Without annotations, pipelines are scored against an n-of-m CONSENSUS of the
enabled pipelines. That answers "which pipeline is the odd one out", NOT "which
pipeline is right" - a consensus of detectors that share a blind spot is blind
in the same place. Score against manual annotations (--annotations) as soon as
you have any; the GUI's annotation lane exports exactly the format expected.

written for Physiology Analysis Tools (C) 2024
"""

__version__ = "0.0.1"

import argparse
import json
import os
import sys
import traceback

import numpy
import pandas

from . import comparison
from .beatcallers import BEATCALLERS
from .filters import FILTERS
from .normalizers import NORMALIZERS
from .pipeline import DEFAULT_NORMALIZER, Pipeline, next_color, run_pipelines

KNOWN_TIME_COLUMNS = ["ts", "time"]


# %% extractors (imported individually - some have optional dependencies)


def available_extractors():
    """{extension: module} for every signal converter that imports cleanly."""
    extractors = {}
    candidates = [
        ("adi_extract", ".adicht"),
        ("labchart_text_extract", ".txt"),
        ("dsi_fp_matlab_extract", ".mat"),
        ("pklgzip_extract", ".gzip"),
        ("pcc_extract", ".txt"),
        ("edf_extract", ".edf"),
    ]
    for module_name, extension in candidates:
        try:
            module = __import__(
                f"{__package__}.signal_converters.{module_name}",
                fromlist=[module_name],
            )
        except Exception as e:  # noqa: BLE001 - optional dependencies
            print(f"  (converter {module_name} unavailable: {e})")
            continue
        extractors.setdefault(extension, []).append(module)
    return extractors


def extract(filepath, extractors=None):
    """Open a signal file with the first converter that succeeds."""
    extractors = extractors or available_extractors()
    extension = os.path.splitext(filepath)[1].lower()
    errors = []
    for module in extractors.get(extension, []):
        for entry_point in ("SASSI_extract", "basspro_extract"):
            reader = getattr(module, entry_point, None)
            if reader is None:
                continue
            try:
                return reader(filepath)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{module.__name__}: {e}")
    raise IOError(f"no converter could open {filepath} ({'; '.join(errors) or 'none tried'})")


def pick_column(df, requested, fallbacks=()):
    """Case-insensitive column lookup, tolerant of channel-name drift."""
    lookup = {str(c).strip().lower(): c for c in df.columns}
    for candidate in [requested, *fallbacks]:
        if candidate and str(candidate).strip().lower() in lookup:
            return lookup[str(candidate).strip().lower()]
    return None


# %% config


def pipelines_to_config(pipelines):
    return [
        {
            "label": p.label,
            "filter": p.filter_name,
            "filter_settings": p.filter_settings.as_dict(),
            "normalizer": p.norm_name,
            "normalizer_settings": p.norm_settings.as_dict(),
            "beatcaller": p.caller_name,
            "beatcaller_settings": p.caller_settings.as_dict(),
            "enabled": p.enabled,
        }
        for p in pipelines
    ]


def pipelines_from_config(config):
    """Accepts either an explicit 'pipelines' list or a 'grid' specification."""
    pipelines = []

    for i, entry in enumerate(config.get("pipelines", [])):
        pipeline = Pipeline(
            filter_name=entry["filter"],
            caller_name=entry["beatcaller"],
            norm_name=entry.get("normalizer", DEFAULT_NORMALIZER),
            label=entry.get("label"),
            color=next_color(i),
            enabled=entry.get("enabled", True),
        )
        pipeline.filter_settings.update(**entry.get("filter_settings", {}))
        pipeline.norm_settings.update(**entry.get("normalizer_settings", {}))
        pipeline.caller_settings.update(**entry.get("beatcaller_settings", {}))
        pipelines.append(pipeline)

    grid = config.get("grid")
    if grid:
        i = len(pipelines)
        for f in grid.get("filters", FILTERS.names()):
            for n in grid.get("normalizers", [DEFAULT_NORMALIZER]):
                for c in grid.get("beatcallers", BEATCALLERS.names()):
                    pipelines.append(
                        Pipeline(f, c, norm_name=n, color=next_color(i))
                    )
                    i += 1

    if not pipelines:
        raise ValueError("config contains no pipelines and no grid")
    return pipelines


def default_config():
    return {
        "signal_column": "ecg",
        "signal_column_aliases": ["ecg", "1 - ecg", "ekg"],
        "time_column": None,  # None -> auto-detect (ts, then time)
        "tolerance_ms": 25.0,
        "reference": "consensus",  # consensus | manual | a pipeline label
        "consensus_min_votes": None,  # None -> simple majority
        "grid": {
            "filters": ["butter_highpass", "butter_bandpass"],
            "normalizers": ["percentile_scale"],
            "beatcallers": ["percentile_threshold", "snr_threshold_v2", "two_average"],
        },
    }


# %% annotations


def load_annotations(path):
    """
    Manual beat annotations for one recording.

    Accepts the workbook the GUI writes (a 'manual_annotations' sheet with a
    'ts' column) or a plain csv/xlsx with a 'ts' column.
    """
    if path.lower().endswith((".xlsx", ".xlsm")):
        try:
            frame = pandas.read_excel(path, sheet_name="manual_annotations")
        except (ValueError, KeyError):
            frame = pandas.read_excel(path)
    else:
        frame = pandas.read_csv(path)

    column = pick_column(frame, "ts", ["time", "timestamp"])
    if column is None:
        raise ValueError(f"{path} has no ts/time column")
    return numpy.sort(frame[column].dropna().to_numpy(dtype=float))


def recording_stem(filepath):
    """
    Base name without extension, tolerant of the compound extensions used here
    ('mouse_1.pkl.gzip' -> 'mouse_1', not 'mouse_1.pkl').
    """
    compound = (".gzip", ".gz", ".bz2", ".xz", ".zip", ".pkl")
    stem = os.path.basename(filepath)
    while True:
        base, extension = os.path.splitext(stem)
        if not extension:
            return stem
        stem = base
        if extension.lower() not in compound:
            return stem


def find_annotation_file(annotation_dir, filepath):
    if not annotation_dir:
        return None
    stem = recording_stem(filepath)
    for extension in (".xlsx", ".csv"):
        candidate = os.path.join(annotation_dir, stem + extension)
        if os.path.exists(candidate):
            return candidate
    return None


# %% per-file run


def run_file(filepath, pipelines, config, extractors=None, annotation_dir=None):
    """
    Returns (scores DataFrame, info dict). Raises only on unrecoverable errors;
    a pipeline that fails is reported in its row rather than aborting the file.
    """
    df = extract(filepath, extractors)
    df.columns = [str(c).strip().lower() for c in df.columns]

    time_column = pick_column(df, config.get("time_column"), KNOWN_TIME_COLUMNS)
    if time_column is None:
        raise ValueError(
            f"no time column (looked for {config.get('time_column')}, "
            f"{KNOWN_TIME_COLUMNS}); columns present: {list(df.columns)}"
        )

    signal_column = pick_column(
        df,
        config.get("signal_column"),
        config.get("signal_column_aliases", []),
    )
    if signal_column is None:
        raise ValueError(
            f"no signal column matching {config.get('signal_column')!r} or its "
            f"aliases; columns present: {list(df.columns)}"
        )

    results = run_pipelines(pipelines, df, time_column, signal_column)
    labels = {r.pipeline.uid: r.pipeline.label for r in results.values()}
    tolerance = config.get("tolerance_ms", 25.0) / 1000.0

    # -- reference
    reference_kind = config.get("reference", "consensus")
    annotation_path = find_annotation_file(annotation_dir, filepath)

    if reference_kind == "manual" or annotation_path:
        if not annotation_path:
            raise ValueError(
                f"reference is 'manual' but no annotation file was found for "
                f"{os.path.basename(filepath)} in {annotation_dir}"
            )
        reference = load_annotations(annotation_path)
        reference_used = f"manual ({os.path.basename(annotation_path)})"
    elif reference_kind == "consensus":
        reference = comparison.consensus_beats(
            [r.beats for r in results.values() if r.ok],
            tolerance=tolerance,
            min_votes=config.get("consensus_min_votes"),
        )
        reference_used = "consensus"
    else:
        match = [u for u, label in labels.items() if label == reference_kind]
        if not match:
            raise ValueError(f"reference pipeline {reference_kind!r} is not in the grid")
        reference = results[match[0]].beats
        reference_used = f"pipeline: {reference_kind}"

    scores = comparison.score_table(results, reference, tolerance, labels)
    scores.insert(0, "file", os.path.basename(filepath))
    scores["filter"] = [results[u].pipeline.filter_name for u in scores["uid"]]
    scores["normalizer"] = [results[u].pipeline.norm_name for u in scores["uid"]]
    scores["beatcaller"] = [results[u].pipeline.caller_name for u in scores["uid"]]
    scores["reference"] = reference_used

    info = {
        "file": os.path.basename(filepath),
        "time_column": time_column,
        "signal_column": signal_column,
        "samples": len(df),
        "duration_s": float(df[time_column].max() - df[time_column].min()),
        "reference": reference_used,
        "n_reference_beats": len(comparison._as_ts(reference)),
        "failed_pipelines": "; ".join(
            f"{r.pipeline.label}: {r.error}" for r in results.values() if not r.ok
        ),
    }
    return scores, info, results


# %% folder run


def run_folder(
    input_dir,
    output_dir,
    config,
    annotation_dir=None,
    extensions=None,
    export_beats=False,
    verbose=True,
):
    os.makedirs(output_dir, exist_ok=True)
    extractors = available_extractors()
    extensions = extensions or list(extractors)

    files = sorted(
        os.path.join(input_dir, f)
        for f in os.listdir(input_dir)
        if os.path.splitext(f)[1].lower() in extensions
    )
    if not files:
        raise IOError(f"no signal files with extensions {extensions} in {input_dir}")

    pipelines = pipelines_from_config(config)
    if verbose:
        print(f"{len(files)} file(s) x {len(pipelines)} pipeline(s)")

    all_scores = []
    infos = []
    failures = []

    for i, filepath in enumerate(files, 1):
        name = os.path.basename(filepath)
        if verbose:
            print(f"[{i}/{len(files)}] {name}")
        try:
            scores, info, results = run_file(
                filepath, pipelines, config, extractors, annotation_dir
            )
            all_scores.append(scores)
            infos.append(info)

            if export_beats:
                beats_dir = os.path.join(output_dir, "beats")
                os.makedirs(beats_dir, exist_ok=True)
                for uid, result in results.items():
                    if result.ok and len(result.beats):
                        result.beats.to_csv(
                            os.path.join(
                                beats_dir,
                                f"{recording_stem(name)}__{uid}.csv",
                            ),
                            index=False,
                        )
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the batch
            if verbose:
                print(f"    FAILED: {e}")
            failures.append({"file": name, "error": str(e),
                             "traceback": traceback.format_exc(limit=3)})

    if not all_scores:
        raise RuntimeError("every file failed - see the printed errors above")

    per_file = pandas.concat(all_scores, ignore_index=True)
    summary = summarise(per_file)

    output_path = os.path.join(output_dir, "batch_comparison.xlsx")
    writer = pandas.ExcelWriter(output_path, engine="xlsxwriter")
    summary.to_excel(writer, sheet_name="summary", index=False)
    per_file.to_excel(writer, sheet_name="per_file", index=False)
    pandas.DataFrame(pipelines_to_config(pipelines)).to_excel(
        writer, sheet_name="pipelines", index=False
    )
    pandas.DataFrame(infos).to_excel(writer, sheet_name="files", index=False)
    if failures:
        pandas.DataFrame(failures).to_excel(
            writer, sheet_name="failures", index=False
        )
    writer.close()

    if verbose:
        print(f"\nwrote {output_path}")
        print(f"{len(failures)} file(s) failed" if failures else "no file failures")
        print("\ntop pipelines (pooled):")
        print(
            summary.head(5)[
                ["pipeline", "pooled_F1", "mean_F1", "sd_F1", "worst_F1", "n_files"]
            ].to_string(index=False)
        )

    return summary, per_file, failures


def summarise(per_file):
    """
    Rank pipelines across files.

    ``pooled_*``  : computed from summed TP/FP/FN, so long recordings weigh more.
    ``mean_F1``   : unweighted mean across files - every animal counts once.
    ``sd_F1`` / ``worst_F1`` : the stability columns. A pipeline with a high mean
                    and a bad worst-case is one that fails silently on some
                    animals, which is usually worse than one that is uniformly
                    mediocre.
    """
    grouped = per_file.groupby(["pipeline", "filter", "normalizer", "beatcaller"])

    summary = grouped.agg(
        n_files=("file", "nunique"),
        TP=("TP", "sum"),
        FP=("FP", "sum"),
        FN=("FN", "sum"),
        mean_F1=("F1", "mean"),
        sd_F1=("F1", "std"),
        worst_F1=("F1", "min"),
        mean_sensitivity=("sensitivity", "mean"),
        mean_PPV=("PPV", "mean"),
        mean_abs_error_ms=("abs_error_ms", "mean"),
        mean_HR=("mean_HR", "mean"),
        total_runtime_s=("runtime_s", "sum"),
    ).reset_index()

    pooled_sensitivity = summary.TP / (summary.TP + summary.FN).replace(0, numpy.nan)
    pooled_ppv = summary.TP / (summary.TP + summary.FP).replace(0, numpy.nan)
    summary["pooled_sensitivity"] = pooled_sensitivity
    summary["pooled_PPV"] = pooled_ppv
    summary["pooled_F1"] = (
        2 * pooled_sensitivity * pooled_ppv / (pooled_sensitivity + pooled_ppv)
    )

    columns = [
        "pipeline", "filter", "normalizer", "beatcaller", "n_files",
        "pooled_F1", "mean_F1", "sd_F1", "worst_F1",
        "pooled_sensitivity", "pooled_PPV", "mean_abs_error_ms", "mean_HR",
        "TP", "FP", "FN", "total_runtime_s",
    ]
    return summary[columns].sort_values("pooled_F1", ascending=False).reset_index(
        drop=True
    )


# %% cli


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare filter / normaliser / beat-caller pipelines across "
                    "a folder of recordings."
    )
    parser.add_argument("--input", "-i", help="folder of signal files")
    parser.add_argument("--output", "-o", help="folder for the report")
    parser.add_argument("--config", "-c", help="pipeline config json")
    parser.add_argument(
        "--annotations", "-a",
        help="folder of manual annotation files (<recording>.xlsx or .csv)",
    )
    parser.add_argument("--signal", "-s", help="signal column (overrides config)")
    parser.add_argument("--tolerance-ms", type=float, help="matching tolerance")
    parser.add_argument(
        "--export-beats", action="store_true",
        help="also write a csv of detected beats per file per pipeline",
    )
    parser.add_argument(
        "--write-config", metavar="PATH",
        help="write a template config json and exit",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="list every registered filter / normaliser / beat caller and exit",
    )
    args = parser.parse_args(argv)

    if args.list:
        for title, registry in [
            ("filters", FILTERS),
            ("normalizers", NORMALIZERS),
            ("beatcallers", BEATCALLERS),
        ]:
            print(f"\n{title}:")
            for name, label in registry.labels().items():
                print(f"  {name:22s} {label}")
        return 0

    if args.write_config:
        with open(args.write_config, "w") as handle:
            json.dump(default_config(), handle, indent=2)
        print(f"wrote template config to {args.write_config}")
        return 0

    if not (args.input and args.output):
        parser.error("--input and --output are required (or use --write-config/--list)")

    config = default_config()
    if args.config:
        with open(args.config) as handle:
            config.update(json.load(handle))
    if args.signal:
        config["signal_column"] = args.signal
    if args.tolerance_ms:
        config["tolerance_ms"] = args.tolerance_ms
    if args.annotations:
        config["reference"] = "manual"

    run_folder(
        args.input,
        args.output,
        config,
        annotation_dir=args.annotations,
        export_beats=args.export_beats,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
