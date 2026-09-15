# Backend Overview

Living doc for the new FastAPI backend (`backend/`) built as Milestone 1 of
the UI redesign (`web-ui-redesign` branch). Update this as later milestones
land. Full context: `docs/superpowers/specs/2026-09-07-web-ui-redesign-design.md`
(architecture) and `docs/superpowers/specs/2026-09-14-ui-workflow-features-design.md`
(workflow/features), `docs/superpowers/plans/2026-09-14-backend-api-foundation.md`
(this milestone's plan).

## Files

| File | Purpose |
|---|---|
| `backend/app.py` | `create_app()` factory — builds the FastAPI app, owns `app.state.imported_files` (in-memory file registry), registers routers. `GET /health`. |
| `backend/channel_selection.py` | Pure function `select_default_channel(filename, available_channels)`. Filename-pattern → exact channel-name rules (`CHANNEL_RULES`), confirmed against real sample files. `NON_SIGNAL_CHANNELS` = `{ts, time, comment}` — the single source of truth for what's never a signal channel. |
| `backend/extractors.py` | `extract_dataframe(filepath)` — tries each extension-matching extractor from the existing `physiology_analysis_tools.modules.signal_converters.*` in order (`EXTRACTOR_SPECS`), returns the first success. `NoExtractorSucceededError` on total failure. `EXTRACTOR_LOAD_ERRORS` records why an extractor (e.g. EDF) failed to even load. |
| `backend/models.py` | Pydantic request/response models: `ImportRequest`, `FileImportResult` (path, filename, status, channels, time_column, size, modified_time, default_channel, error), `ImportResponse`. |
| `backend/files.py` | `POST /files/import`, `GET /files`. Wires `extractors.py` + `channel_selection.py` together; per-file error isolation (one bad file doesn't fail the batch). |
| `backend/beats.py` | `POST /beats/detect` — wraps the existing `heartbeat_detection.beatcaller()`. Reads the parsed DataFrame from `app.state.signal_cache` (set by `/files/import`), runs beat detection with `app.state.beat_settings`, caches the resulting beats DataFrame into `app.state.beat_cache` with `.reset_index(drop=True)` applied (required for M5's arrhythmia join to align correctly). |
| `backend/settings.py` | `GET /settings`, `PUT /settings` — reads/writes `app.state.beat_settings` and `app.state.arrhythmia_settings`. `PUT` validates `bradycardia_absolute_hr < tachycardia_absolute_hr` before any mutation (reject whole request, no partial-apply, on failure); response reflects the actually-constructed `Settings()` instances, not the raw input. |
| `backend/arrhythmia.py` | `POST /arrhythmia/detect` — wraps the existing `arrhythmia_detection.call_arrhythmias()`. Reads `beat_cache[path]` + `signal_cache[path]`, runs detection with `app.state.arrhythmia_settings` and the requested method (`heuristic`/`unsupervised`/`both`, mapped to the library's exact capitalized strings), defensively recomputes `any_arrhythmia` (see Design decisions) before caching the merged result back into `beat_cache`. |
| `backend/requirements.txt` | fastapi, uvicorn, httpx, pytest — not yet split into runtime vs. dev deps (noted below). |
| `backend/tests/` | `conftest.py` (shared fixtures: `example_txt_file`, `real_beats_txt_file`, `long_txt_file`, `adicht_examples_dir`), one test file per module above, plus `test_adicht_extraction.py` (self-skips unless `adi-reader` is functional). |

## Design decisions

- **Sync `def` endpoints, not `async def`** — FastAPI runs sync handlers in a threadpool, so a slow pandas parse doesn't block the event loop. Deliberate choice to avoid the old app's QThread-freeze problem.
- **`create_app()` factory + `app.state`**, not a module singleton — isolated state per test/instance; the in-memory `imported_files` dict is a placeholder for the SQLite-backed store a future milestone adds (spec §1).
- **Channel/time-column split**: `channels` (selectable) never includes `ts`/`time`/`comment`; `time_column` surfaces whichever of `ts`/`time` the file actually has, separately, as metadata. One shared constant (`channel_selection.NON_SIGNAL_CHANNELS`) drives both — do not reintroduce a second copy of this set (a final-review finding caught exactly that drift once already).
- **Default-channel matching is exact name, case-insensitive** — not substring. Falls back to the first non-excluded channel, `matched_rule=False`, if no filename rule fires.
- **Per-file error isolation**: `POST /files/import` never fails the whole request for one bad path; each result is `status: "ok"|"error"` independently.
- **`/files/import` now caches the parsed DataFrame** instead of discarding it after channel detection: `app.state.signal_cache[path] -> {df, time_column}`, so `/beats/detect` (and future analysis endpoints) can reuse it without re-parsing. `app.state.beat_settings`/`app.state.arrhythmia_settings` hold the current settings, mutable via `GET`/`PUT /settings`.
- **`beatcaller()`'s output must have its index reset before caching** (`backend/beats.py`) — `beatcaller()` preserves the original sample-position index (e.g. 2883, 4430, ...) rather than a fresh 0-based range. `ml_tools.beatepocher()` (used by the unsupervised arrhythmia method) keys its per-beat clustering output by positional `enumerate()` index, and the downstream `.join()` aligns by index *label* — without the reset, every row goes unmatched and `abn_cluster` comes back all-NaN. Any future code that re-caches or rebuilds `beat_cache` entries must preserve this reset.
- **`any_arrhythmia` is defensively recomputed in `backend/arrhythmia.py`**, not trusted as returned by `call_arrhythmias()`. The library computes it internally as `df[categories].any(axis=1, bool_only=True)`, which silently drops an entire category column (not just its NaN rows) once that column has mixed True/False/NaN values — reachable whenever `ml_tools.beatepocher()` boundary-skips a beat too close to the signal's start/end for its epoch window. The recompute ORs the library's value with a NaN-safe `.any()` over the present optional columns, so it can only become more true, never mask a fully-populated category. Covered by `test_any_arrhythmia_survives_mixed_nan_abn_cluster_column`.

## Known cross-effects / risks

- **Two library files were modified outside `backend/`**: `src/physiology_analysis_tools/modules/signal_converters/{labchart_text_extract,pcc_extract,adi_extract,edf_extract,dsi_fp_matlab_extract}.py` — all five had a module-level `tkinter` import (used only by unused legacy GUI-picker functions) that crashes on any Python build without Tcl/Tk. All five now import tkinter lazily inside those functions only. `SASSI_extract()` behavior is unchanged. This affects the *pip-installed library too*, not just the backend — worth knowing if anything else imports these modules.
- **`.adicht` extraction is untested in this environment** — `adi-reader`'s native extension doesn't build on macOS; `test_adicht_extraction.py` self-skips here and needs a real run on Windows to confirm the channel rules against real `.adicht` files.
- **No auth/network hardening yet** — `/files/import` accepts and echoes arbitrary local file paths. Fine today (no port is bound), but must be addressed before the Electron/uvicorn milestone binds one (parked finding, not forgotten).
- **Error responses are a free-form string** (`error: str | None`), not the structured `{code, message}` the architecture spec describes — parked until more endpoints exist to design the full error taxonomy at once instead of one-off.
- **`backend/requirements.txt` mixes runtime and dev/test deps** — needs splitting before the PyInstaller/Nuitka sidecar build reads it for packaging.
- **Quitting Electron during backend startup (the ~1-8s health-check window) can orphan the spawned backend process** — `before-quit` only calls `event.preventDefault()` once `stopBackend` is assigned, so a quit requested before `startBackend()` resolves isn't guaranteed to be delayed long enough for cleanup to run. Narrow window, self-limiting (the orphan doesn't block the next launch, which spawns its own backend on a new port), not fixed after a deep review + one fix round — parked rather than risking a 3rd round of changes to this file. `desktop/main.js`'s `before-quit`/`whenReady` handlers.
- **Test coverage gap**: `desktop/backend-process.test.js`'s "fails fast when already exited" test exercises `waitForHealth()` directly with a hand-built state object, not `startBackend()`'s actual spawn→exit-listener wiring — a regression in that wiring specifically wouldn't be caught by the current suite.
- **Unsupervised arrhythmia detection's `abn_cluster` result quality is unvalidated**: on the one real multi-beat file tested (`9 long.txt`), DBSCAN cluster label 0 comes back `True` (flagged abnormal) for all 163 beats — `ml_tools.py`'s clustering doesn't guarantee label 0 means "normal," so this may be a labeling-convention mismatch rather than a genuine finding of universal abnormality. Outside `backend/`'s files (lives in the unmodified library); needs real-world validation with a lab technician before the unsupervised method is trusted, not a code-level fix. Parked, M5 final review.

## Progress

- **Done**: Milestone 1 — backend scaffold, channel-selection logic, extractor wrapper, file import/list API. Milestone 4 — beat-detection endpoint (`POST /beats/detect`, wraps `heartbeat_detection.beatcaller()`, backed by `signal_cache`/`beat_cache`/`beat_settings`). Milestone 5 — `GET`/`PUT /settings`, arrhythmia-detection endpoint (`POST /arrhythmia/detect`, wraps `arrhythmia_detection.call_arrhythmias()`, backed by `arrhythmia_settings`). 30 tests passing, 1 correctly skipped (up from 22/1). Reviewed (per-task + whole-branch), fix waves applied and re-reviewed clean.
- **Not started**: data windowing/LOD for the graph (M6), SQLite persistence + review mutations + report export (M7), auto-run/ETA/stop for beat detection, Svelte frontend, Electron packaging, old-UI (`main.py`/PySide6) removal — see `docs/superpowers/specs/2026-09-28-backend-completion-design.md` for M6/M7 scope.
- **Branch**: `web-ui-redesign`, not merged to `main`.

## Frontend/Electron dev setup

Not npm workspaces yet (planned follow-up), so install separately in three
places: repo root, `frontend/`, and `desktop/`. Then run `npm run dev` from
repo root to launch Vite + Electron together. Electron spawns the backend
automatically on a dynamically-chosen free port and passes it to the renderer
over IPC — no manual `uvicorn` start needed.
