# F5: Persistence, Settings, Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reopening a previously-reviewed file actually resumes (not just reports) prior work; the technician's channel choice survives across sessions; settings are editable from the UI; a report can be exported. This is the LAST frontend milestone — once it closes, the entire `web-ui-redesign` branch is functionally complete end-to-end.

**Architecture:** Three frontend tasks plus one small, justified backend addition discovered while planning this milestone (see Task 1's rationale below — this is not new backend scope, it's completing M7's own "resume" intent, which M7's original design didn't fully close).

**Tech Stack:** Python/FastAPI (Task 1 only), Svelte 5, the F1 API client's three still-unused functions (`getFileState`, `putChannel`, `generateReport` — grepped this session, confirmed unused anywhere in `frontend/src/components/` until now).

**Spec:** `docs/superpowers/specs/2026-09-15-frontend-implementation-design.md` (F5 scope), `docs/superpowers/specs/2026-09-14-ui-workflow-features-design.md` §1 ("Annotation persistence", app flow step 3 "Skip straight to review — auto-run is skipped for this file, it already has results", step 5 "Autosave", step 6 "Export"), `BACKEND_OVERVIEW.md` (M7 API reference).

## Global Constraints — including two gaps found during/after planning

- **Found while planning, not previously caught**: `GET /files/state` (backend, M7) reads persisted beats/marks from SQLite and reports them, but never restores `app.state.beat_cache[path]` — the in-memory dict every other beat-reading endpoint (`GET /beats/window`, `POST /arrhythmia/detect`) actually reads from. `PATCH /files/beats/category` itself is fine on the SQLite side (it operates purely against SQLite, verified this session by reading `backend/db.py`'s `update_beat_category` directly). But on a genuine reopen (a fresh backend process, `beat_cache` empty), the graph's markers (`GET /beats/window`) would come back empty/erroring even though the technician's prior confirm/reject/reassign work is safely in SQLite, and re-running arrhythmia detection would fail with "beat detection has not been run for this file yet" despite it clearly having been. This directly blocks the workflow spec's own explicit "skip straight to review, it already has results" requirement — it's not new scope, it's finishing what M7 set out to do. Task 1 closes this gap with a small, backend-only addition.
- **Found by F4's final review, folded in here rather than reopening F4's scope**: `PATCH /files/beats/category` writes only to SQLite, never to `app.state.beat_cache[path]` — so a rejected/reassigned beat's marker on the live graph stays showing its PRE-review flagged state indefinitely (the graph reads `beat_cache` via `GET /beats/window`, which never sees the mutation), and `BeatCategoryPanel`'s own local `reviewState` resets on every `{#key selectedBeat.ts}` remount, so reselecting a previously-reviewed beat shows no trace of the action having happened, even though it's safely recorded. Task 1 (which already touches `beat_cache`-reconstruction logic in `backend/db_routes.py`) also fixes the PATCH handler to keep `beat_cache[path]` in sync with every mutation. Task 2 (which already touches `ReviewWorkspace.svelte`'s completion-handling for other async operations) wires the graph to actually refresh after a successful category action, reusing the existing `beatsRefreshToken` mechanism from F4 rather than inventing a new refresh path.
- Every new UI surface follows this project's established conventions: `data-testid` on anything tested, `T | ApiError`-aware narrowing where the API client requires it, real component tests mocking only `fetch`/`window.api`.
- No new npm dependencies.

---

### Task 1 (backend): `GET /files/state` restores `beat_cache` as a side effect

**Files:**
- Modify: `backend/db_routes.py`
- Modify: `backend/tests/test_files_state_api.py`

**Interfaces:**
- Produces: `GET /files/state` now has a side effect (in addition to its existing response) — when a persisted record is found, `request.app.state.beat_cache[path]` is set to a DataFrame reconstructed from the persisted `beats` rows, in the same shape `beat_cache` entries already have everywhere else in the backend (`ts, RR, R_amplitude, HR` + whichever of the 7 optional category columns — `bradycardia_absolute, tachycardia_absolute, skipped_beat, prem_beat, abn_cluster, other_arrhythmia, any_arrhythmia` — have at least one non-`NULL` value across the fetched rows, matching the existing "a column either fully exists or is entirely absent" convention `beats_window.py`/`arrhythmia.py` already rely on via `col in df.columns` checks). `review_state`/`reassigned_category` are NOT included in the restored DataFrame — those are SQLite-only concepts `beat_cache` has never carried; the reconstructed category COLUMN VALUES already correctly reflect any reject-cascade or reassignment (since `PATCH /files/beats/category` mutates the real category columns directly).

- [ ] **Step 1: Write the failing tests**

  In `backend/tests/test_files_state_api.py`, using `real_beats_txt_file` (`57.txt`) and TWO separate `create_app(db_path=...)` instances sharing the SAME `db_path` (a `tmp_path`-backed file) to genuinely simulate a reopen-after-restart (a fresh `app.state.beat_cache` that never saw the original detection call):

  1. **Beats restored into `beat_cache` are visible via `GET /beats/window` on a fresh app instance.** On app instance 1: import, `POST /beats/detect` (channel `"channel 1"`), `POST /arrhythmia/detect` (`method: "heuristic"`), `POST /files/beats` to persist. On a BRAND NEW app instance 2 (same `db_path`, fresh `beat_cache = {}`): import the same file again (repopulates `imported_files`/`signal_cache`, required for the "file not imported" guard — matches how every other persistence endpoint already requires this), then `GET /files/state?path=...` — assert `found == True`. Then, on the SAME instance 2, `GET /beats/window?path=...&start=0&end=999` — assert `status == "ok"`, `count == 15` (real, verified value for `57.txt`), and the first beat (`ts ≈ 0.0855`) has `tachycardia_absolute == True`/`any_arrhythmia == True` (the real, already-verified values for this file+method) — proving the restored `beat_cache` entry has the real category data, not just a bare beat list.
  2. **A reject/reassign made in the first session is reflected in the restored `beat_cache`.** Same two-instance setup, but on instance 1, before persisting, `PATCH /files/beats/category` to reject the first beat (clears its category columns per the existing cascade). Persist. On instance 2, after `GET /files/state`, `GET /beats/window` for that beat's range — assert `tachycardia_absolute == False`/`any_arrhythmia == False` for that beat (not `True` as it would be from a naive fresh re-detection) — this is the test that actually proves the restore reflects review history, not just raw detection.
  3. **`GET /files/state` with no prior record does NOT touch `beat_cache`** (no side effect when there's nothing to restore) — reuse the existing "no prior record" test, add an assertion that a subsequent `GET /beats/window` still correctly reports "beat detection has not been run for this file yet" rather than some empty-but-"ok" restored state.
  4. **Only category columns with real data are restored, matching the "column presence" convention.** A file persisted via `POST /files/beats` alone (never had `POST /arrhythmia/detect` run) — on the second instance, after `GET /files/state`, `GET /beats/window` for that file returns beats with `any_arrhythmia`/etc. as `None` for every beat (column absent from the restored DataFrame, not present-but-`False`) — matching exactly how a freshly-detected-but-not-arrhythmia-analyzed file already behaves elsewhere in this backend.

- [ ] **Step 2: Run the tests to verify they fail**

  Run: `pytest backend/tests/test_files_state_api.py -v`
  Expected: FAIL — the restore side effect doesn't exist yet (beats-window calls on the fresh instance report "not run yet").

- [ ] **Step 3: Implement**

  In `backend/db_routes.py`'s `get_file_state` handler, after building the `beats`/`bad_data_marks` response lists (the existing code already fetches `beat_rows` via SQL) and before returning: if `beat_rows` is non-empty, build a `pandas.DataFrame` with one row per `beat_row` — columns `ts`, `RR` (from `beat_row["rr"]`), `R_amplitude` (from `beat_row["r_amplitude"]`), `HR` (from `beat_row["hr"]`), plus each of `categories.ALL_OPTIONAL_COLUMNS` included as a column only if at least one row has a non-`None` value for it (reading directly from `beat_row[col]`, already `0`/`1`/`None` from SQLite — convert `0`/`1` to real Python `bool`, keep `None` as `NaN`/`None` in the DataFrame, matching every other `_to_bool`-style conversion already in this file). Assign this DataFrame to `request.app.state.beat_cache[path]`. Wrap this reconstruction in the SAME `try/except Exception` the handler already has (never let a malformed restore crash the endpoint — on any exception here, log/ignore and still return the normal `FileStateResult`, since the restore is a best-effort side effect, not the endpoint's primary contract).

- [ ] **Step 4: Also keep `beat_cache` in sync with every `PATCH /files/beats/category` mutation**

  In `backend/tests/test_beats_category_api.py`, add: **A confirm/reject/reassign is immediately visible via `GET /beats/window` on the SAME app instance** (no restart needed for this one — this is the live-sync case, distinct from the reopen-restore case above). Import, detect, persist, `PATCH ... action="reject"` on the first beat, then `GET /beats/window` for that beat's range on the SAME app instance — assert the category columns are `False`/`any_arrhythmia: False` in the windowed response, not still showing the pre-reject flagged state. Repeat for `reassign` (assert the newly-assigned category is `True`, others `False`).

  Run these new tests, confirm they fail, then implement: in `backend/db_routes.py`'s `update_beat_category` route handler, after `db.update_beat_category(...)` returns the updated row successfully, if `request.app.state.beat_cache.get(path)` is not `None`, locate the row with matching `ts` in that cached DataFrame and update its category columns (`bradycardia_absolute` through `other_arrhythmia`, plus `any_arrhythmia`) to match the fresh DB row's values — same column-name mapping as the restore logic above. If no `beat_cache[path]` entry exists yet, this is a no-op, not an error — the SQLite write already succeeded regardless.

- [ ] **Step 5: Run the tests to verify they pass**

  Run: `pytest backend/ -v`
  Expected: total grows from 69 passed/1 skipped to 75 passed/1 skipped (2 more than the restore tests alone).

- [ ] **Step 6: Commit**

  ```bash
  git add backend/db_routes.py backend/tests/test_files_state_api.py backend/tests/test_beats_category_api.py
  git commit -m "Restore beat_cache on reopen; keep it synced with every category mutation"
  ```

---

### Task 2 (frontend): Reopen hydration + persisted channel choice

**Files:**
- Modify: `frontend/src/components/review/ReviewWorkspace.svelte`
- Modify: `frontend/src/components/review/ReviewWorkspace.test.ts`
- Modify: `frontend/src/components/graph/EcgGraph.svelte`
- Modify: `frontend/src/components/graph/EcgGraph.test.ts`

**Interfaces:**
- Consumes: `getFileState(path)`, `putChannel(path, channel)` (F1, `frontend/src/lib/api/persistence.ts`, both currently unused anywhere in the app).
- Produces: `EcgGraph` gains an `initialBadDataMarks?: BadDataMark[]` prop, seeding its local `badDataMarks` state instead of always starting empty. `ReviewWorkspace`'s existing `handleCategoryUpdated` (currently a documented no-op from F4) becomes real: on a successful confirm/reject/reassign, it calls the existing `refreshGraphBeats()` (F4's `beatsRefreshToken` mechanism) so the graph's marker for that beat updates immediately instead of staying stuck at its pre-review state — this closes Important finding #2 from F4's final review (backend now keeps `beat_cache` synced on every category mutation per Task 1 above; this step is what actually makes the graph pick that up).

- [ ] **Step 1: Write the failing tests**

  In `ReviewWorkspace.test.ts`:
  1. **On mount, checks for prior state; if found, skips detection and uses the persisted channel.** Mock `getFileState` returning `{status:'ok', found:true, channel:'channel 2', beats:[...], bad_data_marks:[{id:1,start:2,stop:5}], beat_settings:null, arrhythmia_settings:null}`. Render `ReviewWorkspace` with `defaultChannel: 'channel 1'` (deliberately different, to prove the persisted choice wins). Assert: `getFileState` was called with `path`; the channel `<select>`'s value is `'channel 2'`, not the prop's `defaultChannel`; `detectBeats` was NEVER called (no re-detection needed); `EcgGraph` mounts with `channel: 'channel 2'`.
  2. **Bad-data marks from the prior state are passed through to `EcgGraph`.** Same mock — assert `EcgGraph` receives `initialBadDataMarks` matching the mocked `bad_data_marks` list (check via a `data-testid`/prop-visible marker, matching however `EcgGraph.test.ts`'s own new test in this task asserts initial marks render).
  3. **No prior record — falls back to today's existing behavior exactly.** Mock `getFileState` returning `{status:'ok', found:false}`. Assert the channel `<select>` uses `defaultChannel` as before, `EcgGraph` gets no `initialBadDataMarks` (or an empty array), and existing behavior (no `detectBeats` call needed at mount, matching current behavior since Import's auto-run already handled the default channel) is unchanged.
  4. **`getFileState` network failure doesn't block the workspace from rendering.** Mock a rejected/`ApiError` response — assert the workspace still renders using `defaultChannel`, exactly as the "no prior record" case (a state-check failure should never be worse than "start fresh").
  5. **Selecting a new channel persists the choice.** Change the channel `<select>` to a new value; assert `putChannel(path, newChannel)` is called (alongside the existing `detectBeats` call this already triggers) — this closes the "remember last-used channel per file" requirement end-to-end. If `putChannel` fails, it should not block the existing `detectBeats`/`activeChannel` flow (log/ignore — channel-choice persistence is a nice-to-have next to detection actually working).

  In `EcgGraph.test.ts`:
  6. **`initialBadDataMarks` prop seeds the local marks state and renders them immediately**, without needing a drag interaction first — mount with `initialBadDataMarks: [{id:1, start:2, stop:5}]`, assert a `data-testid="bad-data-mark"` is present without any drag having happened.

  Also in `ReviewWorkspace.test.ts` (closing F4's Important finding #2):
  7. **A successful confirm/reject/reassign refreshes the graph's beat markers.** Select a beat, trigger a category action via `BeatCategoryPanel` (mock `updateBeatCategory` resolving `status:'ok'`), assert an additional `/beats/window` fetch fires afterward (the same observable signal Task 3-of-F4's arrhythmia-refresh test already uses for "the graph refreshed").

- [ ] **Step 2: Run the tests to verify they fail**

  Run: `npm --prefix frontend run test -- ReviewWorkspace EcgGraph`
  Expected: FAIL.

- [ ] **Step 3: Implement**

  `EcgGraph.svelte`: add `initialBadDataMarks?: BadDataMark[]` to props; initialize `badDataMarks = $state(initialBadDataMarks ?? [])` instead of always `[]`.

  `ReviewWorkspace.svelte`: on mount (an `$effect` or top-level async IIFE — match the file's existing idiom for one-time-on-mount async work), call `getFileState(path)`. Narrow `FileStateResult`'s `status`/`found`: if `found === true`, set `selectedChannel`/`activeChannel` directly to the persisted `channel` (skip the `detectBeats`-then-advance dance Task-1-era code uses for a fresh default — the persisted channel is already known-good per Task 1's backend restore), pass its `bad_data_marks` through to `EcgGraph` as `initialBadDataMarks`, and also seed `lastPersistedChannel = channel` (the value the existing `persistRequestId`-guarded `ensureChannelPersisted` checks against) so selecting a beat on the just-reloaded channel doesn't fire a redundant, wasted `POST /files/beats` for data that's already known to be current. If `found === false` or the call fails/errors, proceed exactly as today (no behavior change, `lastPersistedChannel` stays `null` as it already does). In `handleChannelChange` (the existing channel-`<select>` handler), after a successful `detectBeats`+`activeChannel` advance, also call `putChannel(path, channel)` (fire-and-forget is acceptable — don't let its failure roll back the already-successful channel switch; log the error if you want, don't surface it as blocking).

  Also implement `handleCategoryUpdated` for real (currently a documented no-op): on `result.status === 'ok'`, call the existing `refreshGraphBeats()` (the same function `ArrhythmiaControls`'s `onComplete` already calls — reuse it exactly, don't build a second beats-refresh path).

- [ ] **Step 4: Run the tests to verify they pass, commit**

  ```bash
  git add frontend/src/components/review/ frontend/src/components/graph/
  git commit -m "Load prior review state on reopen; persist channel choice"
  ```

---

### Task 3 (frontend): Settings dialog

**Files:**
- Create: `frontend/src/components/settings/SettingsDialog.svelte`
- Create: `frontend/src/components/settings/SettingsDialog.test.ts`
- Modify: `frontend/src/App.svelte`
- Modify: `frontend/src/App.test.ts`

**Interfaces:**
- Consumes: `getSettings()`, `putSettings(payload)` (F1, `frontend/src/lib/api/settings.ts`).
- Produces: a modal/panel toggled from a new `data-testid="settings-button"` always visible in `App.svelte`'s shell (both Import and Review views).

- [ ] **Step 1: Write the failing tests**

  In `SettingsDialog.test.ts`:
  1. **Loads and displays current settings on open.** Mock `getSettings()` returning real default values (`beat.min_RR: 60`, `arrhythmia.bradycardia_absolute_hr: 300`, etc. — the real, already-verified defaults from `BACKEND_OVERVIEW.md`). Assert the rendered form fields show these values.
  2. **Save calls `putSettings` with the edited payload.** Change one field (e.g. `arrhythmia.bradycardia_absolute_hr` to `250`); click `data-testid="save-settings-button"`; assert `putSettings` was called with a payload matching the full current form state (all fields, not just the changed one — `PUT /settings` replaces the whole payload).
  3. **A validation error from the backend surfaces inline, doesn't close the dialog.** Mock `putSettings` returning `{status:'error', error:'bradycardia_absolute_hr must be less than tachycardia_absolute_hr'}` — assert the error message renders and the dialog stays open (the technician can fix the value and retry).
  4. **On successful save, the dialog closes (or shows a success state — implementer's choice, document it).**

  In `App.test.ts`:
  5. **The settings button is visible and opens the dialog regardless of current view** (Import or Review) — a quick smoke test in each view.

- [ ] **Step 2: Run the tests to verify they fail**

  Run: `npm --prefix frontend run test -- SettingsDialog App`
  Expected: FAIL.

- [ ] **Step 3: Implement**

  `SettingsDialog.svelte`: on open, `getSettings()`, populate local form state (`beat.*`/`arrhythmia.*` fields — a plain form, one input per field, matching `BeatSettingsModel`/`ArrhythmiaSettingsModel`'s exact field names/types from `frontend/src/lib/api/types.ts`). Save button calls `putSettings({beat: {...}, arrhythmia: {...}})` with the full current form state; on `status:'error'`, show the message inline; on success, close (or show success, per your documented choice).

  `App.svelte`: add a settings button + conditionally-rendered `<SettingsDialog>`, visible in both views (place it in the shared `<main>` markup, not inside the `{#if view === ...}` branches).

- [ ] **Step 4: Run the tests to verify they pass, commit**

  ```bash
  git add frontend/src/components/settings/ frontend/src/App.svelte frontend/src/App.test.ts
  git commit -m "Add settings dialog for beat/arrhythmia detection parameters"
  ```

---

### Task 4 (frontend): Report export

**Files:**
- Modify: `frontend/src/components/review/ReviewWorkspace.svelte`
- Modify: `frontend/src/components/review/ReviewWorkspace.test.ts`

**Interfaces:**
- Consumes: `window.api.pickOutputDirectory(): Promise<string | null>` (F1, Electron IPC), `generateReport(path, outputDir)` (F1, `frontend/src/lib/api/persistence.ts`).

- [ ] **Step 1: Write the failing tests**

  In `ReviewWorkspace.test.ts`:
  1. **Clicking "Generate Report" opens the native directory picker, then calls the API with the chosen path.** Mock `window.api.pickOutputDirectory` resolving `/Users/tech/reports`. Click `data-testid="generate-report-button"`. Assert `generateReport(path, '/Users/tech/reports')` was called.
  2. **Canceling the directory picker is a no-op** (`pickOutputDirectory` resolves `null`) — assert `generateReport` is NEVER called.
  3. **Success shows the real output path.** Mock `generateReport` resolving `{status:'ok', output_path:'/Users/tech/reports/57.xlsx'}` — assert that real path (not a fabricated/guessed one) renders in a success message (`data-testid="report-success"`).
  4. **Failure (e.g. no persisted data for this file yet) shows a clean error**, matching the real backend message pattern — mock `{status:'error', error:'No persisted data for this file — run POST /files/beats first'}`, assert it renders inline, no crash.

- [ ] **Step 2: Run the tests to verify they fail**

  Run: `npm --prefix frontend run test -- ReviewWorkspace`
  Expected: FAIL.

- [ ] **Step 3: Implement**

  A "Generate Report" button in `ReviewWorkspace.svelte`. On click: `const dir = await window.api?.pickOutputDirectory()`; if `dir` is `null`/`undefined`, do nothing (canceled); else `await generateReport(path, dir)`, show the real `output_path` on success or the error message on failure — reuse whatever single-state-value discipline (`'idle'|'running'|...'`) this file already applies elsewhere for consistency, don't add a fourth ad-hoc boolean pairing.

- [ ] **Step 4: Run the full suite, commit**

  ```bash
  git add frontend/src/components/review/
  git commit -m "Add report export with native output-directory picker"
  ```

---

## Self-Review

- **Spec coverage:** reopen loads prior state (§1 "Annotation persistence", app-flow step 3), autosave is now genuinely complete (channel choice + every mutation already wired by F3/F4 + this milestone's `putChannel` addition), settings dialog, report export — all covered.
- **Backend addition justified, not scope creep**: Task 1 completes M7's own stated intent (a technician can resume work) which M7's original design left incomplete for the specific case of a real process restart — discovered and grounded via direct code reading this session (`update_beat_category` doesn't touch `beat_cache`; `get_file_state` never restores it), not a hypothetical.
- **No placeholders**: every task ships real, working, tested functionality — this is also the LAST frontend milestone, so nothing here defers further work to a milestone that doesn't exist.
- **Type/interface consistency**: `putChannel`/`getFileState`/`generateReport` used with the exact signatures already defined in F1's `persistence.ts` — no new API client changes needed.
