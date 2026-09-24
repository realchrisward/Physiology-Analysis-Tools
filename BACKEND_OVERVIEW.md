# Backend Overview

The complete FastAPI backend (`backend/`) for the Physiology Analysis Tools UI
redesign (`web-ui-redesign` branch) — a full rewrite of the old PySide6 desktop
app's analysis logic behind a local HTTP API, consumed by an Electron+Svelte
frontend. All four backend-completion milestones (M4-M7), plus the original
three foundation milestones (M1-M3), are complete: **77 tests passing, 1
correctly skipped**. Two small, justified additions landed during the frontend
build's F5 milestone (a real reopen-resume gap and a review-state-preservation
fix, both found by exercising the whole app together — see Design decisions)
— the backend itself has otherwise been unchanged since M7. This doc
consolidates everything from the individual milestone plans/specs into one
reference — those documents (listed at the bottom) remain as historical/audit
record but this file is the fast path to understanding current backend state.

## Architecture

- **Electron main process** spawns a Python/`uvicorn` subprocess on a
  dynamically-chosen free port, health-polled, torn down on quit.
- **FastAPI backend**, `create_app()` factory + `app.state` (not module
  globals) — isolated state per test/instance.
- **Sync `def` endpoints, not `async def`** everywhere — FastAPI runs sync
  handlers in a threadpool automatically, so a slow pandas/numpy call never
  blocks the event loop. Deliberate choice to avoid the old app's
  QThread-freeze problem, and avoids needing WebSocket/background-task
  infrastructure for anything in this backend.
- **Every failure path returns HTTP 200 with `status: "error"`**, never a
  500 (the one exception: genuine Pydantic type-validation failures on a
  request body itself, e.g. sending a string where a float is expected).
- **The existing analysis library (`physiology_analysis_tools.modules.*`) is
  called unmodified** everywhere — `heartbeat_detection.beatcaller()`,
  `arrhythmia_detection.call_arrhythmias()` — the backend wraps it, never
  forks its logic. Two library files needed a lazy-import fix (tkinter
  crashes without Tcl/Tk); see Known risks.

## Features (by milestone)

- **M1 — Foundation.** File import (multi-path batch, per-file error
  isolation), channel detection/selection via a filename-pattern config,
  file listing.
- **M4 — Beat detection.** Run `heartbeat_detection.beatcaller()` against an
  imported file's chosen channel; returns per-beat rows plus a summary
  (count, mean HR, duration).
- **M5 — Arrhythmia detection + settings.** Run heuristic and/or
  unsupervised (PCA/DBSCAN) arrhythmia detection against detected beats;
  mutable beat/arrhythmia detection settings with cross-field validation.
- **M6 — Data windowing (the graph).** Downsampled, deterministic signal
  data for any time range/resolution — fixes the old app's "graph reshapes
  on every zoom" bug via memoization. Range-filtered beat/arrhythmia
  markers, so the frontend never holds/filters a full beat list.
- **M7 — Persistence, review, export.** SQLite-backed save/reload of a
  file's channel choice, beat/arrhythmia results, and bad-data marks.
  Confirm/reject/reassign a beat's classification (fixes the old app's
  unreliable "Confirm Arrhythmia" button and a real reject-cascade bug).
  Bad-data range marking. Excel report export matching the old app's shape.
- **M8 — Manual beat editing.** Add a beat the detector missed (with a
  snap-to-nearest-peak suggestion), delete a false one, and edit or delete
  bad-data ranges. Hand edits are remembered per file and re-applied when
  beats are re-detected, and added beats carry the time they were added.

## Files

| File | Purpose |
|---|---|
| `backend/app.py` | `create_app()` factory — builds the app, owns all `app.state` (`imported_files`, `signal_cache`, `beat_cache`, `beat_settings`, `arrhythmia_settings`, `window_cache`, `db_path`), registers every router. `GET /health`. CORS for the Electron dev/packaged origins. |
| `backend/channel_selection.py` | `select_default_channel(filename, available_channels)`. Filename-pattern → exact channel-name rules, confirmed against real sample files. `NON_SIGNAL_CHANNELS = {ts, time, comment}` — single source of truth for what's never a signal channel. |
| `backend/extractors.py` | `extract_dataframe(filepath)` — tries each extension-matching extractor from `physiology_analysis_tools.modules.signal_converters.*` in order, returns the first success. |
| `backend/files.py` | `POST /files/import`, `GET /files`. Populates `signal_cache[path]`; invalidates any stale `window_cache` entries for a re-imported path. |
| `backend/beats.py` | `POST /beats/detect` — wraps `heartbeat_detection.beatcaller()`. Caches into `beat_cache[path]` with `.reset_index(drop=True)` (required for M5's join to align). |
| `backend/settings.py` | `GET`/`PUT /settings` — reads/writes `beat_settings`/`arrhythmia_settings`. `PUT` validates before mutating (reject whole request on failure, no partial-apply). |
| `backend/arrhythmia.py` | `POST /arrhythmia/detect` — wraps `arrhythmia_detection.call_arrhythmias()`. Defensively recomputes `any_arrhythmia` (see Design decisions) before caching the merged result back into `beat_cache`. |
| `backend/windowing.py` | `GET /channels/window` — downsampled signal data. Unchanged port of the old app's min-max-per-bin algorithm. Memoized by `(path, channel, start, end, resolution)` in `window_cache`. |
| `backend/beat_editing.py` | `POST /files/beats/snap` (read-only: the signal peak nearest a rough click) and `POST /files/beats/one` (insert a beat at the nearest sample, recompute the neighbour's RR/HR, record it as a manual edit). Also `apply_manual_edits()` (re-applies a file's hand edits to a fresh detection result) and `rr_hr_for_successor()` (RR/HR fix-up after a delete). |
| `backend/beats_window.py` | `GET /beats/window` — range-filters `beat_cache[path]` by `ts`. Arrhythmia category fields all optional (a file may only have had beat detection run). |
| `backend/categories.py` | Single source of truth for arrhythmia category column names: `BASE_CATEGORIES` (5), `REASSIGNABLE_CATEGORIES` (+`other_arrhythmia`, 6), `ALL_OPTIONAL_COLUMNS` (+`any_arrhythmia`, 7). Consumed by `arrhythmia.py`, `beats_window.py`, `db.py`, `db_routes.py`. |
| `backend/db.py` | SQLite persistence layer. `connect()`, `default_db_path()`, `get_file_row`, `upsert_file` (partial-update via an `_UNSET` sentinel, optional `commit=False` for transactions), `replace_beats`, `update_beat_category` (confirm/reject/reassign), `add_bad_data_mark`/`delete_bad_data_mark`. Schema below. |
| `backend/db_routes.py` | `APIRouter(prefix="/files")`, 7 persistence endpoints — see API Reference. Every endpoint wraps its DB body in `try/except Exception`. `GET /files/state` restores `app.state.beat_cache[path]` from persisted rows when found (frontend-driven addition, see Design decisions); `PATCH /files/beats/category` keeps that same cache entry in sync with every mutation. |
| `backend/models.py` | Every Pydantic request/response model — see API Reference for exact shapes. |
| `backend/requirements.txt` | fastapi, uvicorn, httpx, pytest, openpyxl (test-only, see Known risks) — not yet split into runtime vs. dev deps. |
| `backend/tests/` | `conftest.py` (shared fixtures: `example_txt_file`=10.txt, `real_beats_txt_file`=57.txt, `long_txt_file`="9 long.txt", `adicht_examples_dir`), one test file per module above, plus `test_adicht_extraction.py` (self-skips unless `adi-reader` is functional). |

## API Reference

`path` is always a query parameter on `GET` requests and a JSON body field
on `PUT`/`POST`/`PATCH`/`DELETE` — real file paths (Windows drive letters,
spaces, colons) don't round-trip safely as a raw URL segment.

| Method & Route | Request | Response | Notes |
|---|---|---|---|
| `GET /health` | — | `{"status": "ok"}` | |
| `POST /files/import` | `{paths: [str]}` | `{results: [FileImportResult]}` | Per-file error isolation. |
| `GET /files` | — | `[FileImportResult]` | |
| `POST /beats/detect` | `{path, channel}` | `BeatDetectionResult` | `beats`, `count`, `mean_hr`, `duration`, `elapsed_seconds`, `file_size_bytes`. |
| `GET /settings` | — | `SettingsPayload` (`{beat, arrhythmia}`) | |
| `PUT /settings` | `SettingsPayload` | `SettingsResult` | Rejects if `bradycardia_absolute_hr >= tachycardia_absolute_hr`. |
| `POST /arrhythmia/detect` | `{path, channel, method}` (`method`: `"heuristic"\|"unsupervised"\|"both"`) | `ArrhythmiaDetectionResult` | `beats` (each with the 5 optional category flags + `any_arrhythmia`/`other_arrhythmia`), `any_arrhythmia_count`. |
| `GET /channels/window` | query: `path, channel, start, end, resolution` | `ChannelWindowResult` | `x`, `y`, `point_count`, `downsampled`. Memoized. |
| `GET /beats/window` | query: `path, start, end` | `BeatWindowResult` | `beats: [WindowBeat]` (all category fields optional). |
| `GET /beats/of-interest` | query: `path` | `BeatsOfInterestResult` | `ts` of every flagged beat, plus `typical_rr` (seconds; dominant RR mode, unclipped, `null` under 2 beats) which the graph uses to size its focus view. Memoized in `app.state.typical_rr_cache`, keyed on the beat timestamps and KDE bandwidth, so review actions do not recompute it. |
| `GET /files/state` | query: `path` | `FileStateResult` | `found`, `channel`, `beats: [PersistedBeat]`, `bad_data_marks`, `beat_settings`, `arrhythmia_settings`. **Side effect**: when found, also restores `app.state.beat_cache[path]` from the persisted rows (best-effort, own exception guard — never breaks the response contract). |
| `PUT /files/channel` | `{path, channel}` | `ChannelPersistResult` | |
| `POST /files/beats` | `{path, channel}` | `PersistBeatsResult` | Wholesale delete-then-reinsert from `beat_cache[path]`, atomic with the channel/settings-snapshot update. **Safe to call repeatedly on the same channel** — `replace_beats()` preserves any beat's existing non-`"unreviewed"` `review_state`/`reassigned_category` across the reinsert, by exact `ts` match (see Design decisions). |
| `PATCH /files/beats/category` | `{path, ts, action, category?}` (`action`: `"confirm"\|"reject"\|"reassign"`) | `CategoryUpdateResult` | `reject` clears all 6 category columns + `any_arrhythmia`. `reassign` needs `category` in `REASSIGNABLE_CATEGORIES`. Also syncs `app.state.beat_cache[path]` in place if present. |
| `POST /files/bad-data` | `{path, start, stop}` | `BadDataAddResult` | Always auto-sorted (`min`/`max`), regardless of input order. |
| `PATCH /files/bad-data` | `{path, id, start, stop}` | `BadDataUpdateResult` | Moves an existing mark; auto-sorted like an add. Error if the id does not exist for this file. |
| `POST /files/beats/snap` | `{path, channel, ts}` | `BeatSnapResult` | Read-only. Searches about ±60 ms (the larger of `min_RR` and 50 ms) around `ts` on the signal detection used, ignoring samples on existing beats, and returns the highest peak as `ts`. Error if a beat is already there. |
| `POST /files/beats/one` | `{path, channel, ts}` | `BeatAddResult` | Adds a beat at the sample nearest `ts` to SQLite and `beat_cache`. Needs beats already saved for the file (`POST /files/beats`). Refuses a spot within `min_RR` of an existing beat or outside the recording. Returns the new `WindowBeat` including `manual_added_at`. Arrhythmia flags are left empty until arrhythmia detection is re-run. |
| `DELETE /files/beats/one` | `{path, ts}` | `BeatDeleteResult` | Removes a beat from SQLite and `beat_cache`, recomputes the following beat's RR/HR, and remembers the removal. |
| `DELETE /files/state` | `{path}` | `DiscardStateResult` | "Start fresh": drops everything saved for the file, including manual edits. |
| `DELETE /files/bad-data` | `{path, id}` | `BadDataDeleteResult` | Scoped to `(id, file_id)`. |
| `POST /files/report` | `{path, output_dir}` | `ReportResult` | Writes `<output_dir>/<basename>.xlsx`, 3 sheets (`beats`, `bad_data_marks`, `settings`), via `xlsxwriter`. |

**SQLite schema** (`backend/db.py`, one file at `default_db_path()` —
`~/.physiology_analysis_tools/state.db` — or a test-supplied path via
`create_app(db_path=...)`):

```sql
files (id, path, size, mtime, channel, beat_settings_json,
       arrhythmia_settings_json, updated_at, UNIQUE(path, size, mtime))
beats (id, file_id, ts, rr, r_amplitude, hr, <7 category columns>,
       review_state DEFAULT 'unreviewed', reassigned_category,
       UNIQUE(file_id, ts))
bad_data_marks (id, file_id, start, stop)
manual_beat_edits (id, file_id, ts, kind 'add'|'delete', r_amplitude,
                   created_at)   -- ISO UTC time of the edit
```

`beats` also has `manual_added_at` (NULL for detected beats). Databases from
before this column existed are upgraded by `connect()` with an `ALTER TABLE`.

## Design decisions

- **Manual beat edits outlive re-detection.** `beats` is rewritten wholesale
  on every persist, so hand edits live in their own `manual_beat_edits`
  table. `POST /beats/detect` re-applies them to the fresh result *before*
  arrhythmia detection runs, so the new beats are analysed and saved like
  any other: removals drop any detected beat within `min_RR` of the removed
  one, additions are restored at their original time with their original
  `manual_added_at`, and RR/HR are recomputed. Deleting a hand-added beat
  forgets the addition instead of recording a removal; adding a beat where
  one was removed cancels the removal. If detection now finds an added beat
  itself, the detected one is kept (no duplicate) and loses its manual flag.
- **`create_app()` factory + `app.state`**, not a module singleton —
  isolated state per test/instance. `create_app(db_path: str | None = None)`:
  tests always pass an isolated `tmp_path`-backed DB, never the real
  per-user default.
- **Channel/time-column split**: `channels` never includes `ts`/`time`/
  `comment`; `time_column` surfaces separately as metadata. One shared
  constant (`channel_selection.NON_SIGNAL_CHANNELS`) drives both.
- **Default-channel matching is exact name, case-insensitive** — not
  substring. Falls back to the first non-excluded channel if no rule fires.
- **`beatcaller()`'s output must have its index reset before caching**
  (`backend/beats.py`) — it preserves the original sample-position index;
  `ml_tools.beatepocher()` (unsupervised method) keys by positional index,
  and the downstream join aligns by label — without the reset, every row
  goes unmatched and `abn_cluster` comes back all-NaN.
- **`any_arrhythmia` is defensively recomputed in `backend/arrhythmia.py`**,
  not trusted as returned by `call_arrhythmias()`. The library's own
  `df[categories].any(axis=1, bool_only=True)` silently drops an entire
  category column (not just its NaN rows) once that column has mixed
  True/False/NaN values — reachable whenever `beatepocher()` boundary-skips
  a beat. The recompute ORs in the present optional columns, NaN-safe.
- **`window_cache` entries are invalidated by path on re-import**
  (`backend/files.py`) — without this, a repeated `/channels/window` query
  after a re-import would silently serve stale downsampled data.
- **`path` is always a query parameter, never a URL path segment** —
  across every endpoint. Windows drive letters/spaces/colons don't
  round-trip safely as a raw URL segment; this is a deliberate deviation
  from the M6/M7 specs' literal `{path}`-in-URL route shapes.
- **`(size, mtime)` for every M7 persistence operation comes from
  `app.state.imported_files[path]`** (already stat'd at import), never a
  fresh `os.stat()`.
- **`POST /files/beats` is one atomic transaction** — `upsert_file`/
  `replace_beats` both take an optional `commit=False`; the route commits
  once after both succeed, rolling back on either's failure. Every OTHER
  caller of these two functions still gets immediate-commit behavior.
- **Reject cascades in `PATCH /files/beats/category`**: rejecting a beat
  clears every category column plus `any_arrhythmia`, not just a top-level
  flag — the fix for the old app's `action_reject_arrhythmia` bug (it only
  ever touched `annot_any_arrhythmia`). `reassign` sets exactly one target
  category and clears the rest; `confirm` touches no category column.
- **Every `db_routes.py` endpoint wraps its DB body in `try/except
  Exception`** — including `GET /files/state`, closed in M7's final-review
  fix wave after being deliberately left open through Tasks 1-4.
- **`GET /files/state` restores `beat_cache[path]` on reopen; `PATCH
  /files/beats/category` keeps it synced on every mutation.** Found while
  building the frontend's F5 milestone: M7 shipped `GET /files/state` as a
  read-only status report, but every OTHER beat-reading endpoint
  (`GET /beats/window`, `POST /arrhythmia/detect`) reads the in-memory
  `beat_cache`, not SQLite — so on a genuine process restart, a technician's
  persisted review history existed in SQLite but was invisible to the live
  graph and blocked re-running arrhythmia detection. Neither gap was new
  scope; both complete M7's own "resume" intent for the specific case of a
  real restart, which M7's own tests (all same-process) never exercised.
- **`replace_beats()` preserves review state across repeated persists.**
  The original M7 design was correct for a channel's FIRST persist
  (`review_state` defaults to `"unreviewed"` for every row) but wholesale
  delete-and-reinsert on every LATER persist too — so re-persisting a
  channel (e.g. after re-running arrhythmia detection) would silently
  reset every beat's confirm/reject/reassign history back to
  `"unreviewed"`. Found by exercising the full frontend flow end-to-end
  (review some beats → rerun arrhythmia → the graph's own re-fetch logic
  needs a fresh persist to keep the export in sync). Fix: snapshot every
  beat's non-`"unreviewed"` `(review_state, reassigned_category)` by exact
  `ts` before the delete, reapply it on matching `ts` during the reinsert —
  a beat with no prior review, or a `ts` that didn't survive re-detection,
  still correctly defaults to `"unreviewed"`/`NULL`. Matching by exact
  `ts` mirrors `update_beat_category`'s existing precedent; a coincidental
  `ts` collision across a genuine re-detection could in principle inherit
  stale state (theoretical, not observed — see Known risks).

## Known cross-effects / risks

- **Two library files were modified outside `backend/`**:
  `src/physiology_analysis_tools/modules/signal_converters/*.py` — a
  module-level `tkinter` import (unused GUI-picker functions) crashed on
  any Python build without Tcl/Tk. Now lazy-imported inside those functions
  only. Affects the pip-installed library too, not just the backend.
- **`.adicht` extraction is untested in this environment** —
  `adi-reader`'s native extension doesn't build on macOS; needs a real run
  on Windows against real `.adicht` files (explicitly deferred by the user
  to that later testing phase).
- **No auth/network hardening** — endpoints accept/echo arbitrary local
  file paths and write reports to arbitrary local directories. Acceptable
  for a trusted, offline, single-user desktop app with no bound external
  port; revisit only if that threat model ever changes.
- **Error responses are a free-form string** (`error: str | None`), not a
  structured `{code, message}` — parked, no frontend need identified yet.
- **`backend/requirements.txt` mixes runtime and dev/test deps** (now
  including `openpyxl`, added purely for `pandas.read_excel` test
  verification — production code only ever writes via `xlsxwriter`) —
  needs splitting before a PyInstaller/Nuitka sidecar build reads it.
- **Quitting Electron during backend startup can orphan the spawned
  process** — narrow window, self-limiting, parked (`desktop/main.js`).
- **Test coverage gap**: `desktop/backend-process.test.js`'s "fails fast"
  test doesn't exercise `startBackend()`'s actual spawn→exit wiring.
- **Unsupervised arrhythmia detection's `abn_cluster` quality is
  unvalidated** — on the one real file tested, DBSCAN label 0 comes back
  `True` for all 163 beats; may be a labeling-convention mismatch in the
  unmodified library, not a genuine universal finding. Needs real-world
  validation with a lab technician.
- **`window_cache` has no eviction policy** — cardinality is bounded by
  distinct query tuples, not distinct files, so continuous frontend
  pan/zoom could grow it unboundedly within one session. Fast-follow once
  real usage is observable (e.g. a simple LRU cap).
- **`beat_cache`/persistence trust the caller's `channel` string** rather
  than verifying it against what the cached `beat_df` was actually
  detected against — `beat_cache` is keyed by `path` alone, not
  `(path, channel)`.
- **The Excel report represents category flags as `0`/`1`**, not
  `TRUE`/`FALSE` — cosmetic difference from the old app's real-bool
  columns. Worth a product check with a lab technician.
- **`PersistedBeat` (M7) and `WindowBeat` (M6) are near-duplicate models**
  — a shared base model would remove the duplication.
- **`replace_beats()`'s review-state preservation matches beats by exact
  `ts` alone**, same precedent as `update_beat_category`. A coincidental
  `ts` collision between two genuinely different beats across a real
  re-detection would inherit the wrong (stale) review state onto the new
  beat. Theoretical, not observed against any real file this session —
  flagged explicitly during F5's final fix-wave rather than silently
  assumed safe.

## Progress

- **Done**: all backend work, plus 2 small F5-frontend-driven additions.
  M1 (scaffold, channel selection, extractor wrapper, import/list). M4
  (beat detection). M5 (settings, arrhythmia detection). M6 (data
  windowing/LOD, deterministic downsampling). M7 (SQLite persistence,
  review mutations, Excel export). F5 additions: `GET /files/state`
  restores `beat_cache` on reopen; `PATCH /files/beats/category` keeps it
  synced; `replace_beats()` preserves review state across repeated
  persists. Manual beat editing (M8) followed. **126 tests passing, 1
  correctly skipped** as of M8. Every milestone
  and fix wave reviewed (Sonnet, per-task + whole-branch), every finding
  fix-waved and re-reviewed clean — several with real fail-before/
  pass-after verification against the pre-fix commit.
- **Skipped by explicit user instruction**: the one consolidated
  cross-milestone Opus review (hit a session rate limit mid-run; user
  chose to proceed to frontend work rather than retry it).
- **The frontend (F1-F5) is also now complete** — see
  `FRONTEND_OVERVIEW.md`. The two backend additions above were both
  found by exercising the full app end-to-end during F5, not backend
  work resuming independently.
- **Not started**: Electron packaging, old-UI (`main.py`/PySide6) removal,
  a visual/layout styling pass on the frontend — all explicitly out of
  scope for the M1-M7/F1-F5 build, not newly discovered gaps. Real
  device/file testing (Windows, real `.adicht` files) remains explicitly
  deferred per direct user instruction.
- **Branch**: `web-ui-redesign`, not merged to `main`.

## Frontend/Electron dev setup

Not npm workspaces yet (planned follow-up), so install separately in three
places: repo root, `frontend/`, and `desktop/`. Then run `npm run dev` from
repo root to launch Vite + Electron together. Electron spawns the backend
automatically on a dynamically-chosen free port and passes it to the renderer
over IPC — no manual `uvicorn` start needed.

## Historical documents (superseded by this file as the primary reference)

- `docs/superpowers/specs/2026-09-07-web-ui-redesign-design.md` — original architecture (Electron+FastAPI+Svelte)
- `docs/superpowers/specs/2026-09-14-ui-workflow-features-design.md` — workflow/feature decisions (still the authority for frontend UX behavior)
- `docs/superpowers/specs/2026-09-28-backend-completion-design.md` — M4-M7 design
- `docs/superpowers/plans/2026-09-14-backend-api-foundation.md` — M1 plan
- `docs/superpowers/plans/2026-09-28-m4-beat-detection.md`, `m5-arrhythmia-settings.md`, `m6-data-windowing.md`, `m7-persistence-review-export.md` — M4-M7 plans, each with full TDD steps and exact grounded test values
