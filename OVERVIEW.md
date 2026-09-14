# Physiology Analysis Tools — Codebase Overview

This document explains what the tool does, how it's built, and how the pieces
fit together — written for a software engineer with **no medical/physiology
background**. Where a domain term shows up, it's defined inline.

It complements two other docs already in this repo:
- `changes_made.md` — a changelog of recent fixes/features (the "what changed").
- `issues_fixed.md` — cross-reference of that work against the project's
  GitHub issue tracker (the "why", tied to upstream bug reports).

This document instead answers "what *is* this program and how does it work",
independent of any particular round of changes.

---

## 1. What the tool does (domain primer)

This is a **desktop GUI application** for analyzing **ECG (electrocardiogram)
recordings** — the electrical signal traced by a heartbeat, the same kind of
squiggly-line trace you'd see on a hospital heart monitor. The recordings this
tool processes come from lab equipment monitoring **live animal research
subjects** (the project originates from the International Mouse Phenotyping
Consortium), not humans — but the signal processing math is the same either
way. Think of it as: **load a long time-series voltage recording → find each
heartbeat in it → flag heartbeats that look abnormal → let a human confirm
which flags are real → export a spreadsheet report.**

Concretely, a user:
1. **Imports** one or more raw recording files (several incompatible vendor
   file formats are supported — see §4).
2. Picks which **channel** (data column) in the file is the ECG signal, and
   runs **beat detection** — an algorithm that scans the voltage trace and
   marks the timestamp of each heartbeat (technically, each "R-peak" — the
   sharp upward spike in one repeating unit of the ECG waveform called the
   "QRS complex"; you don't need to know the rest of the letters, R is just
   "the big spike that's easy to detect algorithmically").
3. Runs **arrhythmia detection** — flags heartbeats that look abnormal
   (irregular timing, wrong shape) using two different strategies (rule-based
   thresholds, and unsupervised machine-learning clustering — see §6).
4. **Reviews** the flagged results visually on a scrolling graph, and can
   confirm/reject each flag, or manually mark stretches of the recording as
   "bad data" (noise, disconnection, etc. — to be excluded from analysis).
5. **Exports** everything to an Excel report for downstream use.

There is no server, no database, no network component. It's a single-process
desktop app that reads local files and writes local Excel files.

---

## 2. High-level architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         main.py  (GUI layer)                        │
│  MainWindow — loads ecg_analysis_tool.ui, wires up every button,    │
│  owns the pyqtgraph plot, drives the review workflow, writes the    │
│  .xlsx report.                                                      │
└───────────────┬───────────────────────────────────┬─────────────────┘
                 │                                   │
                 ▼                                   ▼
┌────────────────────────────────┐   ┌───────────────────────────────────┐
│   modules/signal_converters/    │   │        modules/ (analysis)        │
│  one module per file format,    │   │  heartbeat_detection.py           │
│  each exposing the same         │   │    → find R-peaks (beats)         │
│  SASSI_extract(filepath)        │   │  arrhythmia_detection.py          │
│  function → returns a pandas    │   │    → flag abnormal beats          │
│  DataFrame in a common shape    │   │      (heuristic rules)            │
│  (a "ts" time column + one      │   │  ml_tools.py                      │
│  column per signal channel)     │   │    → flag abnormal beats          │
└────────────────────────────────┘   │      (unsupervised clustering,    │
                                      │       called BY arrhythmia_       │
                                      │       detection.py)               │
                                      └───────────────────────────────────┘
```

The dependency direction is: `main.py` → `arrhythmia_detection.py` →
`ml_tools.py`, and `main.py` → `heartbeat_detection.py`, and `main.py` →
each `signal_converters/*.py` module independently. The signal converters
don't depend on the analysis modules or on `main.py` at all — they're pure
"file in, DataFrame out" adapters and could be reused standalone (e.g. in a
script, with no GUI).

**Everything is in-memory pandas DataFrames.** There's no database — a
loaded file becomes a DataFrame held in `MainWindow.data`; beat-detection
results become a second DataFrame `MainWindow.beat_df`; that DataFrame keeps
growing extra columns as arrhythmia detection and manual review add more
information to it, and it's finally dumped straight to Excel sheets. This
"one row per detected beat, with more and more analysis columns bolted on"
DataFrame is the central data structure of the whole application.

---

## 3. The GUI framework and how the UI is built

- **PySide6** (the official Qt-for-Python binding) is the GUI toolkit.
- **`ecg_analysis_tool.ui`** is a Qt Designer XML file — this defines *what
  widgets exist* (buttons, list boxes, splitters, spin boxes) and their
  layout, but contains **zero behavior**. It's loaded at runtime via
  `QUiLoader` (`main.py`'s `main()` function), *not* compiled to Python code
  ahead of time. This means: to change what's on screen, you edit the `.ui`
  XML (by hand, or by opening it in Qt Designer); to change what happens
  when something is clicked, you edit `main.py`.
- **`MainWindow.__init__`** takes the loaded `ui` object and does something
  worth calling out: it copies every child widget attribute from `ui` onto
  `self` (`for att, val in ui.__dict__.items(): setattr(self, att, val)`).
  That's why the rest of the code can write `self.pushButton_Add_Files`
  directly instead of `self.ui.pushButton_Add_Files` — it's a flattening
  trick, not two separate widget trees.
- **`attach_buttons()`** is the single place every button/menu-item/spinbox
  gets its Qt signal connected to a handler method. If you're trying to find
  "what runs when the user clicks X", start here — it's a flat list of
  `self.someWidget.clicked.connect(self.action_something)` lines.
- **Naming convention**: handler methods are prefixed `action_*`
  (`action_BeatDetection`, `action_Add_Files`, etc.) — grepping for
  `action_` is the fastest way to find "the code behind a UI feature".
- **Styling**: `APP_STYLESHEET` (a big Qt CSS-like string near the bottom of
  `main.py`) applies a flat, modern light theme app-wide. The plot itself is
  *not* styled through this — pyqtgraph draws its own background/graphics.
- **Layout**: the central widget uses a `QSplitter` (`splitter_main`) with
  three panes — left sidebar (file list, signal list, output settings),
  center (the graph + time-navigation controls), right panel (arrhythmia
  review controls) — so the window is resizable, unlike an earlier version
  that used fixed pixel coordinates (see `changes_made.md`).

---

## 4. Loading data: the signal-converter plugin system

Different lab equipment vendors export recordings in incompatible formats.
`modules/signal_converters/` has one module per format:

| Module | File extension | Format | Notes |
|---|---|---|---|
| `labchart_text_extract.py` | `.txt` | ADInstruments LabChart, text export | Parses a text header + tab-separated voltage columns |
| `adi_extract.py` | `.adicht` | ADInstruments LabChart, native binary | Needs the `adi-reader` package, which only works on Windows |
| `edf_extract.py` | `.edf` | European Data Format (a standard medical signal format) | Needs the optional `pyedflib` package; import-guarded so the app still starts if it's missing (see §7) |
| `dsi_fp_matlab_extract.py` | `.mat` | DSI telemetry system, exported to MATLAB `.mat` | Uses `scipy.io` |
| `pklgzip_extract.py` | `.gzip` | A gzip-compressed pandas pickle | The tool's own "already processed" intermediate format |
| `pcc_extract.py` | `.txt` | PCC data acquisition system | Also `.txt`, but a different layout than LabChart's — the app tries each matching extractor in turn until one succeeds |

**The shared contract**: every module exposes a function
`SASSI_extract(filepath) -> pandas.DataFrame`. ("SASSI" is a name carried
over from a sibling/predecessor project, "Breathing Analysis Selection and
Segmentation" — several of these converter modules were adapted from that
codebase, hence the GPLv3 attribution headers crediting the Ray Lab.) The
returned DataFrame always has:
- a `"ts"` column — timestamps, in seconds, as floats
- one column per recorded signal channel (voltage readings), with the
  channel's original name

Because every converter returns the *same shape* of DataFrame, everything
downstream (`main.py`'s plotting, `heartbeat_detection.py`,
`arrhythmia_detection.py`) works identically regardless of which file format
was actually loaded. This is the key abstraction that keeps the analysis
code decoupled from file-format details.

**How the app picks a converter** (`main.py`,
`action_update_selected_file`): it filters `extractors` (a dict defined near
the top of `main.py`) by matching the file's extension, then tries each
matching extractor's `SASSI_extract()` in turn, catching exceptions, until
one succeeds. Two formats share the `.txt` extension (LabChart text and PCC),
which is why "try each candidate, catch failures" is necessary rather than a
simple extension→module lookup.

`edf_extract` is special-cased: it depends on the third-party `pyedflib`
package, which isn't guaranteed to install successfully on every platform.
It's imported in its own `try/except` block, and if that import fails,
`edf_extract` is set to `None` and simply omitted from the `extractors` dict
— EDF support silently disables itself rather than crashing the whole app on
startup.

---

## 5. Beat detection (`modules/heartbeat_detection.py`)

**Goal**: given a raw voltage trace over time, output one row per detected
heartbeat, with its timestamp and instantaneous heart rate.

The core function is `beatcaller(df, voltage_column, time_column, **settings)`.
Algorithm, step by step:

1. **(Optional) Invert the signal.** Some recordings have the ECG trace
   flipped upside-down (the "R-peak" points down instead of up) due to how
   the electrodes were physically attached. Either the user manually flags
   this (`ecg_invert`), or the tool can guess automatically
   (`auto_detect_invert`, off by default): it finds local peaks in both
   directions, compares the 97th-percentile amplitude of positive vs.
   negative peaks (using a high percentile rather than a mean, so it isn't
   thrown off by ordinary background noise peaks), and flips polarity if the
   negative side is clearly dominant. It only trusts this comparison when
   there are at least 30 peaks on each side (`_MIN_PEAKS_FOR_INVERT_DETECTION`)
   — too few peaks makes the percentile comparison unreliable, so short
   recordings are left alone rather than risking a wrong guess.
2. **High-pass filter** (`ecg_filter`, on by default): removes slow drift in
   the baseline voltage (e.g. from breathing motion) using a Butterworth
   filter (`scipy.signal.butter`), so the peak-detection threshold in the
   next step isn't thrown off by that drift.
3. **Set a detection threshold.** Either an absolute voltage
   (`abs_thresh`) or — more commonly — a percentile of the whole signal's
   amplitude distribution (`perc_thresh`, default 97th percentile: "the
   threshold is set so that only the top 3% most extreme voltage values in
   the recording count as a potential beat").
4. **Find peaks** above that threshold using `scipy.signal.find_peaks`, with
   a minimum spacing between accepted peaks (`min_RR`, in milliseconds) —
   this prevents one wide, noisy beat spike from being counted as two beats.
5. **Filter out likely-noise peaks** ("breath filter"): compares each
   candidate peak's height to the average height of its neighboring peaks;
   a peak that's unusually small relative to its neighbors
   (`min_relative_amplitude`, default 0.6× the local average) gets dropped.
   This is aimed at spurious small peaks — e.g. noise correlated with
   breathing — that clear the global threshold but are clearly not real
   heartbeats compared to the beats around them.
6. **Compute intervals and rate.** For each consecutive pair of accepted
   beats: the time between them (**RR interval**, named for the R-peak — a
   standard term in this field, not related to variables named `RR`
   elsewhere in general programming), and heart rate in beats-per-minute
   (`60 / RR`).

Output: a DataFrame with one row per beat — columns `ts` (timestamp),
`RR` (interval to previous beat), `R_amplitude` (peak height),
`HR` (heart rate), `beats` (always 1, used as a plot-marker helper column).
This `beat_df` is the DataFrame that everything in §6 and the report export
builds on top of.

`Settings` is a plain class holding all the tunable parameters above with
their defaults — an instance of it is what the Settings dialog in the GUI
edits, and its `__dict__` is passed straight into `beatcaller(**settings)`
via keyword-argument unpacking.

---

## 6. Arrhythmia detection — flagging abnormal beats

"Arrhythmia" here just means "a heartbeat, or the interval around it, that
doesn't look normal" — could be too slow, too fast, mistimed, or oddly
*shaped*. The tool offers two independent detection strategies that the user
picks between (or runs both) via a dropdown ("Heuristics" / "Unsupervised" /
"Both"):

### 6a. Heuristic (rule-based) — `arrhythmia_detection.py`

Simple threshold rules applied directly to the beat table:

- `bradycardia_absolute` — heart rate below a fixed threshold (too slow;
  "brady-" = slow)
- `tachycardia_absolute` — heart rate above a fixed threshold (too fast;
  "tachy-" = fast)
- `skipped_beat` — this beat's RR interval is unusually long compared to a
  rolling local average of surrounding RR intervals (i.e. a beat that seems
  to be "missing" — the gap where it should have been is much bigger than
  normal)
- `prem_beat` — the opposite: an interval unusually *short* compared to the
  local average (a beat arriving early / "premature")

Each rule is a simple vectorized pandas/numpy comparison — no ML involved.
Results become boolean columns on `beat_df` (e.g. `bradycardia_absolute`),
and `any_arrhythmia` is `True` if *any* enabled category fired for that beat.

### 6b. Unsupervised (shape-based clustering) — `ml_tools.py`

This catches abnormalities the simple rate-based rules can't: a beat that
occurs at a *normal* time but has an unusual *shape* (waveform morphology) —
e.g. a different kind of arrhythmia, or a noise artifact that still crossed
the beat-detection threshold.

Pipeline (`call_arrhythmias_PCA`):
1. **`beatepocher`** — for every detected beat, slice out a short window of
   raw voltage samples centered on that beat (an "epoch") — literally "what
   does the waveform look like right around this beat".
2. **`detrend_normalise`** — remove any linear trend and rescale each epoch
   to unit length, so epochs are compared on *shape* alone, not on absolute
   voltage or slow drift.
3. **`beat_clusterer`** — reduce each epoch (a vector of many voltage
   samples) down to 2 dimensions with **PCA** (Principal Component
   Analysis — a standard technique for compressing high-dimensional data
   down to its main axes of variation, here so that similarly-shaped beats
   land near each other in 2D), then group nearby points with **DBSCAN**
   (a density-based clustering algorithm — points in dense neighborhoods
   form a cluster; anything too sparse/isolated is left unclustered, i.e.
   labeled as an "outlier" rather than forced into some cluster).
4. Cluster `0` (DBSCAN's largest/first-found dense cluster — the common beat
   shape) is treated as "normal"; every other cluster label (including
   DBSCAN's own "noise/outlier" label) is flagged `abn_cluster` = abnormal.

This is unsupervised in the ML sense — there's no pre-labeled training set of
"normal" vs. "abnormal" beat shapes; it just clusters *this recording's own*
beats and calls the odd-ones-out abnormal. That also means it needs enough
beats to form a meaningful "normal" cluster, and it's the operation flagged
in `changes_made.md` as slow enough (10+ seconds) to need a background
thread so it doesn't freeze the UI (see §8).

Both strategies' outputs land as columns on the same `beat_df`, and
`arrhythmia_detection.call_arrhythmias()` is the single entry point `main.py`
calls, which dispatches to one or both strategies based on the user's
dropdown selection and merges the results (plus building `annot_*` integer
columns used for the manual-review workflow described next).

### 6c. Human review layer

Detection alone isn't the end of the pipeline — every flagged beat becomes a
DataFrame integer code (not just true/false) so a human reviewer's decision
can be recorded distinctly from the algorithm's original flag:
- `0` = not flagged
- `1` (or truthy from the raw detector) = algorithm-flagged, not yet reviewed
- `2` = human-confirmed as a real arrhythmia
- `-1` = human-rejected (algorithm was wrong)

`main.py` drives this: `action_next_arrhythmia` / `action_prev_arrhythmia`
step through flagged beats on the graph one at a time,
`action_confirm_arrhythmia` / `action_reject_arrhythmia` set the code, and
`action_clear_rejected_arrhythmias` hides (but doesn't delete) rejected
markers from the plot so the review pass stays uncluttered while the
rejection is still preserved for the exported report.

---

## 7. Optional dependencies and defensive imports

Two dependencies aren't guaranteed to work on every machine, and the code
explicitly plans around that instead of letting an ImportError kill the app:

- **`adi-reader`** (for `.adicht` files) — Windows-only in practice. Imported
  in a bare `try/except` inside `adi_extract.py` itself, setting a module
  `__working__` flag.
- **`pyedflib`** (for `.edf` files) — imported in `main.py` inside its own
  isolated `try/except`; on failure, `edf_extract` is set to `None` and
  omitted from the `extractors` dict entirely (§4). The rest of the app
  starts normally either way.

Also worth knowing: **`main.py` imports `modules.*` inside a `try/except
ImportError`**, first trying an absolute import (`modules.heartbeat_detection`)
and falling back to a package-relative import
(`physiology_analysis_tools.modules.heartbeat_detection`). The comment in
the code calls this a "temporary solution... needed for pip distribution" —
it exists because the app can be run two different ways (as a script
directly inside the `physiology_analysis_tools/` folder, vs. as an installed
package via `python -m physiology_analysis_tools.main`), and each needs a
different import style to find the same modules.

---

## 8. Concurrency: keeping the UI responsive

Qt GUI apps have one rule that matters a lot here: **all UI updates must
happen on the main/UI thread**, and any slow work done on that thread freezes
the window (no redraws, no clicking) until it finishes. The unsupervised
clustering step (§6b) can take 10+ seconds on a large recording, so it's
moved off the main thread:

- **`ArrhythmiaAnalysisWorker`** (`QObject` subclass) wraps the call to
  `arrhythmia_detection.call_arrhythmias()` and emits a Qt `Signal` when done
  (`finished`) or if it throws (`error`).
- **`action_Arrhythmia_Analysis`** creates a `QThread`, moves the worker onto
  it (`moveToThread`), and connects signals so that when the thread finishes
  the result flows back to `_on_arrhythmia_analysis_finished` — which runs
  back on the main thread automatically, because Qt's signal/slot connections
  cross threads safely by default (this is *the* standard safe pattern for
  background work in Qt — never touch widgets directly from inside the worker
  thread).
- Meanwhile, an indeterminate `QProgressDialog` is shown so the user knows
  something is happening, and the two buttons that could re-trigger analysis
  are disabled until it's done.

Everything else in the app (file loading, beat detection, graph redraws) runs
synchronously on the main thread — those are fast enough not to need this
treatment.

---

## 9. Plotting and the "min-max downsampling" trick

The graph is a `pyqtgraph.PlotWidget` (`self.graph`), chosen over
matplotlib for its much better interactive-pan/zoom performance with large
time-series. Key mechanics:

- **`gather_data()`** (`main.py`) is called every time the visible plot needs
  new data: it slices the DataFrame to just the currently-visible time
  window (`x_min` to `x_min + x_window`), and — if there are more raw samples
  in that window than can usefully be drawn — downsamples before handing
  points to pyqtgraph.
- **Why downsample at all**: a recording can have millions of raw samples;
  handing all of them to the plot widget every time the user drags a slider
  would be far too slow, and far more detail than a screen can render anyway.
- **`min_max_downsample()`** — the downsampling method actually used. It
  splits the visible window into `n_bins` equal chunks (bin count based on
  the plot widget's pixel width) and, from each chunk, keeps **both** the
  minimum and maximum y-value (in original time order). This is a deliberate
  choice over simpler "keep every Nth sample" (stride/decimation)
  downsampling: stride sampling can silently step *over* a brief spike (like
  an arrhythmic beat) that falls between the samples it happens to keep,
  making it invisible on the zoomed-out graph. Keeping the min and max per
  bin guarantees any spike within a bin still shows up, at the cost of
  potentially ~2x more rendered points than a stride approach for the same
  bin count.

---

## 10. Settings

Two lightweight `Settings` classes (`heartbeat_detection.Settings`,
`arrhythmia_detection.Settings`) hold every tunable parameter as plain
instance attributes with defaults — no config file, no persistence between
runs (defaults reset each launch). `MainWindow` owns one instance of each.
`SettingsWindow` (a `QDialog` in `main.py`) auto-generates one form row per
attribute in each settings object via `FlexibleEntryWidget` — a small helper
that picks a checkbox for booleans and a text field for everything else,
converting text back to the right type (`float`/`int`/`str`/`None`) when
read back. This means adding a new setting to either `Settings` class is
enough to have it automatically show up in the Settings dialog — no manual
UI wiring needed for new parameters, only (optionally) an entry in
`SETTINGS_TOOLTIPS` for the hover-help text.

---

## 11. Report export

`action_generate_report()` writes one `.xlsx` file per source recording
(named after the input file), via `pandas.ExcelWriter` with the `xlsxwriter`
engine, with three sheets:
- **`beats`** — the full `beat_df` (every detected beat, every detection
  column, every annotation column, every arrhythmia flag)
- **`bad_data_marks`** — the list of manually-marked bad-data time ranges
- **`settings`** — every beat-detection and arrhythmia-detection setting used
  for this run, plus the version numbers of `main.py`,
  `heartbeat_detection.py`, `arrhythmia_detection.py`, and `ml_tools.py` at
  the time of export — so a report can always be traced back to exactly
  which algorithm versions and parameters produced it.

---

## 12. Where to look for what (quick index)

| I want to understand... | Look at |
|---|---|
| What happens when a button is clicked | `main.py`, method named `action_<ButtonName>` |
| How a specific file format is parsed | `modules/signal_converters/<format>_extract.py`, function `SASSI_extract` |
| The heartbeat-finding algorithm | `modules/heartbeat_detection.py`, `beatcaller()` |
| Rule-based arrhythmia flags | `modules/arrhythmia_detection.py`, `call_*` functions |
| Shape-based (ML) arrhythmia flags | `modules/ml_tools.py`, `call_arrhythmias_PCA()` |
| Layout of the window / what widgets exist | `ecg_analysis_tool.ui` (open in Qt Designer, or read the XML) |
| Default parameter values | `Settings` classes in `heartbeat_detection.py` / `arrhythmia_detection.py` |
| App-wide visual theme | `main.py`, `APP_STYLESHEET` string |
| Recent bug fixes and why | `changes_made.md` |
| Which GitHub issues map to what work, and what's still open | `issues_fixed.md` |

---

## 13. Known limitations (as of this writing)

Carried forward from `changes_made.md` / `issues_fixed.md`, since they shape
how you should read the current code:

- No automated test suite exists anywhere in the repo — all verification so
  far has been manual/exploratory.
- Beat detection still struggles on recordings with large baseline drift
  from movement (a bigger fix — a local/rolling detection threshold instead
  of one global percentile threshold — has been identified but not
  attempted).
- `.adicht` (native ADInstruments) support can't be tested on macOS (the
  `adi-reader` dependency is Windows-only), so changes touching that path
  carry more risk until verified on Windows.
- "Quality Scoring" is a stub menu item — currently does nothing.
- No session persistence: closing the app discards all in-progress
  review/settings state; there's no way to reload a previous session or a
  previously exported report.
