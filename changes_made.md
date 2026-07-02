# Changes Made — Physiology Analysis Tools

Summary of improvements, fixes, and dependency changes made to the codebase. See `issues_fixed.md` for how these map to the upstream GitHub issue tracker.

## New dependency

- Added **`pyedflib>=0.1.38`** to `pyproject.toml` (`dependencies`) — required to support `.edf` file loading (see below). No other `pyproject.toml` changes were made.
- Import is guarded in `main.py`: if `pyedflib` is missing or fails to install, EDF support is silently disabled and the app still starts normally — it does not crash the whole app.

## Feature additions & fixes

- **EDF file support** — the `.edf` extractor existed in the codebase but was never registered. Added the missing `SASSI_extract()` wrapper it needed and wired it into the app's extractor list.
- **Status messages in the UI** — file loading, beat detection, arrhythmia analysis, report generation, and errors now write to the on-screen status box (`textBrowser_Status`), not just the console.
- **Arrhythmia Analysis no longer freezes the UI** — the unsupervised (PCA/DBSCAN) analysis, which can take 10+ seconds, now runs on a background thread with a progress dialog instead of blocking the window.
- **"Remove Rejected Markers" button** — appears only after an arrhythmia is rejected during review; lets the reviewer clear rejected markers from the plot in one click without losing the rejection from the exported report.
- **Beat detection summary** — after running, the status box reports beat count, mean HR, and recording duration.
- **Settings dialog improvements** — parameters are now grouped into labeled sections, have hover tooltips explaining each one, and there's a "Restore Defaults" button. Also fixed a latent crash bug in the settings widget for string-typed values.
- **Resizable, restyled UI** — the window was previously a fixed-size, non-resizable absolute-pixel layout; it's now a proper resizable layout (splitter-based sidebar / graph / review panel) with a modern flat visual theme, corrected button text clipping, and a repositioned graph legend (with a note that legend entries are clickable to toggle series visibility).
- **More accurate graph rendering when zoomed out** — replaced the stride-based downsampling (which could silently skip over transient/arrhythmic spikes) with min-max downsampling, which preserves both the peak and trough of each rendered segment.
- **Auto-detection of inverted ECG signals** (opt-in setting, default off) — addresses a known cause of poor beat detection on some files (inverted R-peaks) by automatically detecting and correcting signal polarity. Off by default, so existing results are unaffected unless a user opts in. Verified against all bundled sample files (no regressions, correctly recovers beats from a synthetically-inverted signal); not yet verified against a real `.adicht` file, since that format can't be tested on macOS.
- **Fixed a blank/stuck popup** that appeared when running Arrhythmia Analysis, caused by a dialog being attached to the wrong (invisible) parent window.
- **Small fixes and cleanups**: removed a deprecated pandas API call that would break on future pandas versions, removed unreachable dead code, narrowed overly broad `except:` blocks so real errors surface instead of being silently swallowed (including in file-loading error messages), and general minor code cleanup.

## Testing approach

No automated test suite exists in this repo. Changes were verified via:
- A full end-to-end smoke test (load file → beat detection → arrhythmia analysis → reject/clear markers → generate report) run against the bundled example files.
- Manual verification scripts for specific fixes (e.g. confirming the downsampling change preserves a synthetic signal spike; confirming the auto-invert-detection setting produces identical results to before when left off).
- Visual checks of the UI at multiple window sizes.

## Known limitations / not addressed

- Beat detection quality on signals with large baseline drift (e.g. from movement) is unchanged — needs a larger algorithm change
- No automated test suite has been added.
- `.adicht` (Windows-only ADI LabChart) file handling could not be tested directly on macOS; changes affecting it should be re-verified on Windows before release.
