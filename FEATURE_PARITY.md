# Feature Parity — Original PySide6 App vs. New Electron/FastAPI/Svelte App

Audited directly against source: `src/physiology_analysis_tools/main.py` (the
original PySide6 desktop app, 1679 lines, still present in the repo but
unused by the new app) vs. the current `backend/` (FastAPI) and `frontend/`
(Electron + Svelte 5) trees on branch `web-ui-redesign`. Supersedes the
"not started" styling note in `FRONTEND_OVERVIEW.md` — that pass has since
happened.

**Status**: 106 backend tests passing, 193 frontend tests passing. Branch
`web-ui-redesign`, not merged to `main`. Old app still present at
`src/physiology_analysis_tools/main.py` + `ecg_analysis_tool.ui`, untouched.

Every gap listed here as missing has since been closed — see "Previously
missing, now implemented" below.

---

## Fully ported

| Original feature | New app | Notes |
|---|---|---|
| Multi-file import, per-file error isolation | Sidebar + `ImportScreen`, `POST /files/import` | |
| 6 file formats (LabChart `.txt`, PCC `.txt`, `.adicht`, `.edf`, `.mat`, `.gzip`) | `backend/extractors.py` | Same converter modules, unmodified |
| Default-channel selection (filename-pattern rules) | `channel_selection.py` | |
| Beat detection (`beatcaller`, all params) | `POST /beats/detect` | |
| Heuristic arrhythmia detection (brady/tachy/skipped/premature) | `POST /arrhythmia/detect`, `method=heuristic` | |
| Unsupervised PCA/DBSCAN shape clustering | `POST /arrhythmia/detect`, `method=unsupervised\|both` | Rewritten epoching (KDE-clipped, RR-adaptive) + adaptive `eps` — see "Beat shape analysis" below |
| Confirm / Reject / Reassign a flagged beat | `BeatCategoryPanel`, `PATCH /files/beats/category` | |
| Bad-data range marking | EcgGraph "Mark Bad Data" mode | |
| Min-max downsampling for the graph | `backend/windowing.py` | Same bin-edge math as the original `min_max_downsample()` |
| Pan/zoom on the graph, reset view | `EcgGraph` (uPlot) | Plus explicit X/Y zoom in/out buttons |
| All 8 beat-detection + 7 arrhythmia-detection settings | `SettingsDialog`, `GET`/`PUT /settings` | |
| Excel report (`beats`/`bad_data_marks`/`settings` sheets) | `POST /files/report` | Now a Save-As dialog (folder + filename), was folder-only in the original |
| Background processing for the slow clustering step | *(removed, not needed)* | FastAPI's threadpool replaces the old `QThread`/`ArrhythmiaAnalysisWorker` plumbing entirely |

---

## Beat shape analysis (unsupervised detection)

The epoching step was replaced with the KDE-clipped, RR-adaptive algorithm
(`ml_tools.beatepocher_kde_clipped_rr_smooth`): the window around each beat
is sized from the recording's **own** dominant RR interval — estimated by
cutting a Gaussian KDE of the RR distribution at the first minimum right of
its tallest peak, so missed/skipped beats don't drag the estimate — clipped
to a plausible range, and every epoch resampled to a fixed length so epochs
stay comparable. Epochs are keyed by timestamp, so cluster labels map back
onto the beat table by `ts` instead of by row position.

**`eps` is now derived from the data by default.** The previous fixed
`eps=0.03` labelled *every* beat an outlier on both real test recordings
(163/163 and 15/15), which made `abn_cluster` useless. `eps` is an absolute
distance in a PCA space whose scale is set by each recording's own beat
amplitudes, so no fixed value generalises. The default now uses the standard
k-distance heuristic (a high percentile of each beat's distance to its
`min_samples`-th neighbour), which brings the long test file from 163/163
flagged down to 1/163. A fixed `eps` is still selectable in Settings.

> **Worth a look from the lab:** the heuristic thresholds have not been
> retuned. On `9 long.txt` (median rate ~202 bpm) the default 300 bpm
> bradycardia threshold flags 98 beats and the premature-beat rule 96, so
> 157/163 beats come back flagged regardless of the clustering. That is a
> threshold/recording mismatch, not a detection bug — but it does mean
> "beats of interest" will be most of the recording for files like that
> until the thresholds match the preparation.

## Improved beyond the original

- **Session persistence** — SQLite-backed; reopening a file restores channel,
  beats, review state, and bad-data marks. The original discarded everything
  on close.
- **Reject correctly clears every category flag**, not just one top-level
  flag — the original's `action_reject_arrhythmia` only ever touched
  `annot_any_arrhythmia`, leaving stale per-category data behind.
- **"Remove one flag"** — a new, narrower action lets a technician correct a
  single mis-detected category without a full reject/reassign. No equivalent
  existed in the original.
- **Per-category color + shape markers**, crossed with a reviewed/unreviewed
  visual, plus a legend that doubles as a category filter. The original drew
  every flagged beat as the same red triangle regardless of category.
- **Rejected beats get a visible strike overlay**, distinguishing "reviewed
  and dismissed" from "never flagged" (fixed this session) — the original had
  no such distinction either.
- **Recent-files list** (persisted across app restarts) and auto-open /
  auto-run-detection on import.
- **A live-status sidebar file browser**, replacing a flat file list with no
  per-file state indicator.
- **A real visual/layout design pass** — sidebar, welcome screen, dismissible
  settings overlay, styled review workspace. (Was tracked as "not started" in
  `FRONTEND_OVERVIEW.md`; that doc predates this work.)

---

## Previously missing, now implemented

- **Beat-of-interest navigation** — replaces and extends the original's
  `action_next_arrhythmia`/`action_prev_arrhythmia`/`action_first_arrhythmia`/
  `action_last_arrhythmia`. The graph now opens *focused on the first flagged
  beat* (a ~2s window) instead of the whole recording, with First/Previous/
  Next/Last controls that wrap around, a "Beat of interest N of M" counter,
  and a total count in the legend. Jumping also selects the beat, so the
  review panel acts on it. Backed by `GET /beats/of-interest`; the list
  refreshes whenever a review action changes what is flagged (rejecting a
  beat removes it from the walk).
- **Automatic detection on graph load** — arrhythmia detection now runs by
  itself when a file's graph opens and nothing is flagged yet, so beats of
  interest exist without a manual click. Tries heuristic + unsupervised, and
  falls back to heuristic-only when a recording is too short or sparse for
  the clustering to run. Skipped for a reopened file that already has
  results.
- **Jump-to-start / jump-to-end / previous-window / next-window** — discrete
  paging across the recording at the current zoom, alongside the existing
  continuous pan/zoom.
- **Raw vs. filtered trace toggle** — the original's `checkBox_plot_filtered`.
  `GET /channels/window?filtered=true` applies the same highpass filter beat
  detection uses, over the whole channel (so it matches what detection saw
  and has no per-window edge artefacts), memoized per filter setting.
- **"Hide rejected"** — declutters a review pass without deleting anything,
  alongside the existing All/Reviewed/Unreviewed filter.
- **"Restore Defaults" in Settings** — fills the form from
  `GET /settings/defaults` without applying anything until Save, matching the
  original dialog's semantics.
- **"Clear files"** — empties the session's file list from the sidebar
  (frontend-only: nothing on disk, no backend cache, and no persisted review
  work is touched).
- **About dialog** — reports the live backend connection rather than a
  hardcoded claim.

## Still not ported

- **"Quality Scoring" menu item** — not carried over, but this was already a
  non-functional stub in the original (did nothing), so nothing was lost.

---

## Explicitly out of scope / not started (not new gaps — always deferred)

- **Electron packaging** (PyInstaller/Nuitka sidecar bundling of the Python
  backend into a standalone installer) — the app only runs via `npm run dev`
  today.
- **Removal of the old PySide6 app** — `main.py` + `ecg_analysis_tool.ui`
  are still in the repo, unused but untouched.
- **Real hardware/file testing on Windows for `.adicht`** — deferred by
  explicit user instruction; `adi-reader`'s native extension doesn't build
  on macOS, so this path is unverified in this environment.
- **Auth / network hardening** — not needed; a trusted, offline, single-user
  desktop tool with no bound external port.
