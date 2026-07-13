# Pipeline comparison tools — install & usage

Adds a **filter → normalise → detect** pipeline framework, a GUI comparison
window, and a headless batch mode. Everything is **additive**: the existing
single-pipeline flow (`beat_settings`, `beat_df`, arrhythmia annotation,
report) is untouched, and no existing file is overwritten.

---

## 1. Install

### 1.1 Copy the new files

All paths relative to `src/physiology_analysis_tools/`:

```
modules/registry.py                        registry + auto-discovery
modules/algorithms.py                      base classes + shared helpers
modules/pipeline.py                        Pipeline, PipelineResult, grid runner
modules/comparison.py                      beat matching, metrics, consensus
modules/comparison_window.py               the GUI comparison window
modules/batch.py                           headless batch mode + CLI

modules/filters/__init__.py                FILTERS registry
modules/filters/basic_filters.py           passthrough, butter HP/BP, notch, median
modules/filters/advanced_filters.py        bessel, auto-invert, savgol, wavelet

modules/normalizers/__init__.py            NORMALIZERS registry
modules/normalizers/basic_normalizers.py   none, percentile, zscore, robust, ...

modules/beatcallers/__init__.py            BEATCALLERS registry
modules/beatcallers/basic_beatcallers.py   legacy wrappers, find_peaks, pan-tompkins
modules/beatcallers/advanced_beatcallers.py  basicRR2, template, hilbert, two-average
```

Nothing else in `modules/` changes.

### 1.2 Patch `main.py` (four small edits)

**a. import** — near the other module imports:

```python
try:
    from modules import comparison_window
except ImportError:
    from .modules import comparison_window
```

**b. menu entry** — at the end of `MainWindow.attach_buttons()`. No `.ui` edit
is needed; the action is created in code:

```python
        self.actionCompare_Pipelines = self.menuRun.addAction(
            "Filter / Beat-caller Comparison..."
        )
        self.actionCompare_Pipelines.triggered.connect(
            self.action_compare_pipelines
        )
```

**c. new method** on `MainWindow`:

```python
    def action_compare_pipelines(self):
        if self.data is None:
            QMessageBox.warning(None, "Comparison", "Open a signal file first.")
            return
        if self.listWidget_Signals.currentItem() is None:
            QMessageBox.warning(
                None, "Comparison", "Select a signal channel first."
            )
            return

        if self.DEVMODE:
            importlib.reload(comparison_window)

        window = comparison_window.ComparisonWindow(
            data=self.data,
            time_column=self.comboBox_time_column.currentText(),
            voltage_column=self.listWidget_Signals.currentItem().text(),
            parent=self,
        )
        window.exec()
```

**d. version info** (optional) — in `main()`:

```python
    from .modules import pipeline, comparison
    window.version_info = {
        ...,
        "pipeline": pipeline.__version__,
        "comparison": comparison.__version__,
    }
```

### 1.3 Dependencies

No new **required** dependencies. `wavelet_denoise` needs `PyWavelets`; without
it that one filter simply does not register and everything else works. To
enable it, in `pyproject.toml`:

```toml
[project.optional-dependencies]
wavelet = ["PyWavelets>=1.6"]
```

### 1.4 Verify

```bash
python -m physiology_analysis_tools.modules.batch --list
```

Should print 9 filters, 6 normalisers, 8 beat callers. If a converter is
missing an optional dependency it prints a note and carries on — that is
expected, not a failure.

---

## 2. GUI usage

1. Open a file and **select a signal channel** in the main window (the
   comparison window inherits the channel and time column from it).
2. **Run → Filter / Beat-caller Comparison…**
3. Build the grid:
   - **Add** creates one pipeline; set its Filter / Normalise / Beat caller
     from the three combos; **Settings** opens the parameter form for each
     stage (built by introspection, so a new algorithm's settings show up with
     no UI work).
   - **Full grid** multi-selects filters × normalisers × callers at defaults.
   - **Duplicate** is for A/B-ing one parameter of an otherwise identical combo.
4. **Run all enabled pipelines.** Filtered and normalised signals are cached
   and shared, so an M×N×K grid does only M filter passes.
5. Read the output. **Every pipeline whose `show` box is ticked is drawn at the
   same time, on both plots** — the two checkbox columns are independent:
   - `run` = include in the next run. `show` = draw in the plots. Run a 60-cell
     grid once, then `Show none` and tick the three you are actually comparing.
   - **Top plot**: raw trace in grey; the *detector input* (filtered AND
     normalised) of the highlighted row, or of every shown pipeline if
     **overlay all shown detector inputs** is ticked; and above the trace, one
     **marker lane per shown pipeline**, plus a lane for the reference.
   - **Bottom raster**: the same lanes, zoomed out, for scanning the recording.
   - **Marker coding** (both plots), against the current reference:
     | symbol | meaning |
     |---|---|
     | filled circle, pipeline colour | hit — matched a reference beat |
     | red **x** | false positive — a beat the reference does not have |
     | hollow red circle | false negative — a reference beat this pipeline missed |
     An `x` and a hollow circle in the same column tell you *which* pipeline is
     wrong at a glance, without reading the metrics table.
   - **Metrics table**: scored against the **Reference** combo (manual
     annotations / consensus / any pipeline) within the **Tolerance**.
6. **Annotate beats** (toggle) → click the plot to add a beat (snapped to the
   local maximum); ctrl-click to remove one. Then set Reference = *Manual
   annotations*. This is the only way to get true sensitivity/PPV; everything
   else measures agreement, not correctness.
7. **Disagreements |< <<< >>> >|** walks the review queue — the timestamps
   where at least one pipeline disagrees with the reference. Annotating only
   these is a far better use of your time than annotating from the start.
8. **Export comparison report** → xlsx (`pipelines`, `scores`, `pairwise_F1`,
   `disagreements`, `manual_annotations`, per-pipeline beats).
9. **Save pipeline config** → json for batch mode.
   **Send selected beats to main window** → loads the highlighted pipeline's
   beats into `beat_df`, so the existing arrhythmia workflow runs on them.

---

## 3. Batch usage

```bash
# what's registered?
python -m physiology_analysis_tools.modules.batch --list

# template config
python -m physiology_analysis_tools.modules.batch --write-config pipelines.json

# run a folder (consensus reference)
python -m physiology_analysis_tools.modules.batch \
    --input  /data/cohort_A \
    --output /results/cohort_A \
    --config pipelines.json

# run against manual annotations (one <recording>.xlsx or .csv per file)
python -m physiology_analysis_tools.modules.batch \
    -i /data/cohort_A -o /results/cohort_A -c pipelines.json \
    -a /annotations/cohort_A

# also dump every pipeline's beats
... --export-beats
```

### Config

Either an explicit `pipelines` list (what the GUI writes) or a `grid`, or both:

```json
{
  "signal_column": "ecg",
  "signal_column_aliases": ["ecg", "1 - ecg", "ekg"],
  "time_column": null,
  "tolerance_ms": 25.0,
  "reference": "consensus",
  "consensus_min_votes": null,
  "grid": {
    "filters": ["butter_highpass", "butter_bandpass"],
    "normalizers": ["percentile_scale"],
    "beatcallers": ["percentile_threshold", "snr_threshold_v2", "two_average"]
  }
}
```

`time_column: null` auto-detects (`ts`, then `time`). `signal_column_aliases`
covers channel-name drift across a cohort (`ecg` vs `1 - ECG`).

### Output: `batch_comparison.xlsx`

| sheet | contents |
|---|---|
| `summary` | pipelines ranked across all files |
| `per_file` | every pipeline × every file (usually the more informative sheet) |
| `pipelines` | full config of each pipeline, for reproducibility |
| `files` | time/signal column used, duration, reference, per-file failures |
| `failures` | files that could not be processed, with the error |

**Reading `summary`:**

- `pooled_F1` — from summed TP/FP/FN, so long recordings weigh more.
- `mean_F1` — unweighted; every animal counts once.
- `sd_F1` / `worst_F1` — the stability columns. **A pipeline with a high mean
  and a bad worst-case fails silently on some animals**, which is usually worse
  for a phenotyping pipeline than one that is uniformly mediocre. Rank on
  `worst_F1` at least as often as on the mean.

---

## 4. Adding an algorithm

Drop a file in the relevant package; the registry auto-discovers it and the GUI
combos and settings forms populate themselves. No other file changes.

```python
from . import FILTERS                       # or NORMALIZERS / BEATCALLERS
from ..algorithms import Settings, SignalFilter

class MyFilterSettings(Settings):
    def __init__(self):
        self.cutoff = 10.0                  # attribute type drives the widget

@FILTERS.register
class MyFilter(SignalFilter):
    name = "my_filter"                      # stable id — appears in reports
    label = "My filter"                     # shown in the UI
    description = "..."                     # shown above the settings form
    Settings = MyFilterSettings

    @staticmethod
    def apply(voltage, fs, settings):       # numpy in, numpy out, same length
        ...
```

A `BeatCaller` implements `call(voltage, time, fs, settings)` and returns a
DataFrame with `ts, RR, R_amplitude, HR, beats` — use the helpers in
`algorithms.py` (`beats_from_indices`, `refine_to_peak`, `enforce_refractory`,
`rolling_threshold`) rather than re-deriving them. **The voltage it receives is
already filtered and normalised** — do not filter inside a detector, or the grid
stops measuring what it claims to.

---

## 5. Caveats worth carrying around

- **Consensus is not truth.** A consensus of detectors that share a blind spot
  is blind in the same place. Use it to find *candidates* and to locate
  disagreements; use manual annotation to decide who is right.
- **`bessel_smoothing` at its default 50 Hz low-pass destroys the mouse R
  wave** (it is a flow-signal default). Any SNR-relative detector then computes
  a threshold *above* the R peak and finds zero beats. Raise the cutoff to
  200–300 Hz before using it on ECG.
- **`rolling_robust` erases genuine R-amplitude changes.** It is excellent for
  detection on drifting recordings and unusable if `R_amplitude` is an outcome.
- **The legacy detectors have known defects.** `basicRR` reports the
  threshold-crossing time (≈ −3.5 ms bias) and compares a sample index against
  a value in seconds for its refractory check, which massively over-calls on
  noisy data; `snr_threshold_v2` (`basicRR2`) fixes both. The legacy behaviour
  is preserved unchanged under `snr_threshold` so old results stay reproducible.
- **Uniform sampling is assumed.** `fs` comes from the median sample interval;
  a stitched multi-block recording with a changed rate will not be handled.
