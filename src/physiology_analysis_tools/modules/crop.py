# -*- coding: utf-8 -*-
"""
Crop large recordings into small ones for algorithm development.

The comparison window can restrict itself to an analysis segment, which solves
the interactive case. This module solves the other one: producing small files
on disk so that opening them in the GUI, or sweeping a grid over a folder with
``batch.py``, does not mean loading three hours of signal every time.

    # 60 s starting at 12:00 into the recording
    python -m physiology_analysis_tools.modules.crop \\
        --input  /data/mouse_A.pkl.gzip \\
        --output /data/dev \\
        --start  720 --duration 60

    # same 60 s window out of every recording in a folder
    python -m physiology_analysis_tools.modules.crop \\
        --input /data/cohort_A --output /data/dev --start 720 --duration 60

    # three 30 s samples spread evenly through each file, written separately
    python -m physiology_analysis_tools.modules.crop \\
        --input /data/cohort_A --output /data/dev --duration 30 --samples 3

Timestamps are preserved as they appear in the source file, so a beat called at
t=735.2 s in a crop is the same beat at t=735.2 s in the original. Pass
``--rezero`` if you would rather each crop start at t=0.

written for Physiology Analysis Tools (C) 2024
"""

__version__ = "0.0.1"

import argparse
import os
import sys

import numpy
import pandas

try:
    from .signal_converters import pklgzip_extract
except ImportError:  # direct script execution
    from physiology_analysis_tools.modules.signal_converters import pklgzip_extract

KNOWN_TIME_COLUMNS = ["ts", "time"]
SUFFIX = ".pkl.gzip"


def pick_time_column(df, requested=None):
    lookup = {str(c).strip().lower(): c for c in df.columns}
    candidates = [requested] if requested else KNOWN_TIME_COLUMNS
    for name in candidates:
        if name and str(name).strip().lower() in lookup:
            return lookup[str(name).strip().lower()]
    raise ValueError(
        f"no time column found (looked for {candidates}); "
        f"columns present: {list(df.columns)}"
    )


def crop_dataframe(df, time_column, start, duration, rezero=False):
    """
    Return the rows of ``df`` whose timestamp falls in [start, start+duration].

    Slicing on timestamps rather than row counts keeps this correct for files
    whose sampling rate is not exactly what you assumed, and for the
    multi-block files that adi/labchart extractors concatenate.
    """
    time = numpy.asarray(df[time_column], dtype=float)
    stop = start + duration

    i0 = int(numpy.searchsorted(time, start, side="left"))
    i1 = int(numpy.searchsorted(time, stop, side="right"))
    if i1 - i0 < 2:
        raise ValueError(
            f"window {start}-{stop} s contains {i1 - i0} sample(s); "
            f"the file spans {time[0]:.1f}-{time[-1]:.1f} s"
        )

    cropped = df.iloc[i0:i1].reset_index(drop=True)
    if rezero:
        cropped.loc[:, time_column] = (
            cropped[time_column] - cropped[time_column].iloc[0]
        )
    return cropped


def sample_starts(time, duration, samples):
    """
    Evenly spaced start times for ``samples`` non-overlapping crops.

    One crop of the first 60 s tells you how a pipeline does on the first 60 s.
    Recording quality drifts (electrode contact, anaesthetic depth, movement),
    so a detector tuned on a single clean window will flatter itself. Spreading
    the crops is the cheap defence.
    """
    first, last = float(time[0]), float(time[-1])
    span = last - first
    if samples < 1:
        raise ValueError("samples must be >= 1")
    if duration * samples > span:
        raise ValueError(
            f"{samples} x {duration} s does not fit in a {span:.1f} s recording"
        )
    if samples == 1:
        return [first]
    stride = (span - duration) / (samples - 1)
    return [first + i * stride for i in range(samples)]


def input_files(path):
    if os.path.isfile(path):
        return [path]
    files = sorted(
        os.path.join(path, f)
        for f in os.listdir(path)
        if f.endswith(SUFFIX)
    )
    if not files:
        raise ValueError(f"no {SUFFIX} files found in {path}")
    return files


def crop_file(
    path,
    output_dir,
    duration,
    start=None,
    samples=1,
    time_column=None,
    rezero=False,
    verbose=True,
):
    df = pklgzip_extract.SASSI_extract(path)
    column = pick_time_column(df, time_column)
    time = numpy.asarray(df[column], dtype=float)

    if start is not None:
        starts = [start]
    else:
        starts = sample_starts(time, duration, samples)

    stem = os.path.basename(path)
    for ext in (SUFFIX, ".gzip", ".pkl"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break

    written = []
    for i, begin in enumerate(starts):
        cropped = crop_dataframe(df, column, begin, duration, rezero=rezero)
        tag = f"_{int(round(begin))}s_{int(round(duration))}s"
        out_path = os.path.join(output_dir, f"{stem}{tag}{SUFFIX}")
        cropped.to_pickle(
            out_path,
            compression={"method": "gzip", "compresslevel": 1, "mtime": 1},
        )
        written.append(out_path)
        if verbose:
            size_mb = os.path.getsize(out_path) / 1e6
            print(
                f"  {os.path.basename(out_path)}  "
                f"{len(cropped):,} rows  {size_mb:.1f} MB"
            )
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Crop pkl.gzip recordings into short test files."
    )
    parser.add_argument(
        "--input", required=True, help="a .pkl.gzip file, or a folder of them"
    )
    parser.add_argument("--output", required=True, help="output directory")
    parser.add_argument(
        "--duration", type=float, default=60.0, help="crop length in seconds"
    )
    parser.add_argument(
        "--start",
        type=float,
        default=None,
        help="start time in seconds; omit to spread --samples crops evenly",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=1,
        help="number of evenly spaced crops per file (ignored with --start)",
    )
    parser.add_argument(
        "--time-column", default=None, help="override the time column name"
    )
    parser.add_argument(
        "--rezero",
        action="store_true",
        help="shift each crop so it starts at t=0 (default keeps original times)",
    )
    args = parser.parse_args(argv)

    os.makedirs(args.output, exist_ok=True)

    failures = []
    for path in input_files(args.input):
        print(os.path.basename(path))
        try:
            crop_file(
                path,
                args.output,
                duration=args.duration,
                start=args.start,
                samples=args.samples,
                time_column=args.time_column,
                rezero=args.rezero,
            )
        except Exception as e:  # keep going: one bad file should not stop a batch
            print(f"  FAILED: {e}")
            failures.append(path)

    if failures:
        print(f"\n{len(failures)} file(s) failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
