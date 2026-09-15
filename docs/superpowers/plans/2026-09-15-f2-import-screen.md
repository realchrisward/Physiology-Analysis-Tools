# F2: Import Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The app's first real screen — multi-file/folder import via native Electron dialogs, per-file status, and an "Auto-run beat detection" batch with a learn-as-you-go ETA and a Stop control. Replaces the `HealthStatus` scaffold demo as `App.svelte`'s content.

**Architecture:** `ImportScreen.svelte` owns the file list as local component state (an array of per-file row objects — no global store needed yet, since nothing outside this screen reads it until F3 needs a "selected file to review," which is F3's own concern to wire). A small `src/lib/stores/eta.ts` module holds the session's running average (bytes/sec) as a plain exported object with functions, not a Svelte store proper — nothing needs it to be reactive outside this screen's own polling of it after each detection completes.

**Tech Stack:** Svelte 5, the F1 API client (`importFiles`, `detectBeats`), the F1 Electron IPC pickers (`pickFiles`, `pickFolder`), F1's design tokens.

**Spec:** `docs/superpowers/specs/2026-09-15-frontend-implementation-design.md` (F2 scope), `docs/superpowers/specs/2026-09-14-ui-workflow-features-design.md` §1 ("Auto beat detection on load", "Beat detection progress & cancel", "Bulk upload").

## Global Constraints

- **ETA display**: show "Calculating…" until at least one real `elapsed_seconds`/`file_size_bytes` pair exists this session; only then compute an estimate from the real running average — never seed with a hardcoded guess presented as real (this exact rule was set earlier in this project, before the backend even existed, specifically for this UI).
- **Stop semantics**: cancels the *batch* — the file currently detecting is allowed to finish (fast, in-memory, seconds even for large files), every file still queued behind it is dropped (left at whatever status it had — "ready," not auto-run). No partial/half-computed detection result is ever kept or shown.
- **Auto-run is sequential, not parallel** — one file detects at a time, both because the Stop semantics require a clean "current vs. queued" boundary and because the backend's `beat_cache` is keyed per-path with no inherent concurrency guarantee across simultaneous requests for different paths that hasn't been tested.
- **Scope note**: this milestone does not build a working "switch to Review" transition — the Review workspace doesn't exist as real UI until F3, so there is nothing to switch to yet. `App.svelte` renders `ImportScreen` directly; F3 adds the view-switching state once Review has real content to switch into. This mirrors F1's own final-review ruling to defer scope until the destination exists rather than build a placeholder screen.
- Every new component gets `data-testid` attributes on anything a test queries, matching the existing `HealthStatus.svelte`/`.test.ts` convention.
- Real (not over-mocked) component tests via `@testing-library/svelte`; mock only `fetch` and `window.api`, per F1's established testing philosophy.

---

### Task 1: File picking + import wiring

**Files:**
- Create: `frontend/src/components/import/ImportScreen.svelte`
- Create: `frontend/src/components/import/ImportScreen.test.ts`
- Modify: `frontend/src/App.svelte`

**Interfaces:**
- Consumes: `pickFiles()`, `pickFolder()` (F1, `window.api`), `importFiles(paths)` (F1, `frontend/src/lib/api/files.ts`).
- Produces: an internal `FileRow` shape (not exported — Task 2 extends this same component, not a separate module) covering at minimum `{path, filename, status: 'ready'|'error', channels, defaultChannel, size, error}` — Task 2's brief will read this component's actual field names directly rather than have them pre-specified here, since both tasks land in the same file/session.

- [ ] **Step 1: Write the failing tests**

  In `ImportScreen.test.ts`:
  1. **Import via file picker.** Mock `window.api.pickFiles` to resolve `['/data/57.txt']`. Mock `fetch` so `POST /files/import` resolves `{results: [{path:'/data/57.txt', filename:'57.txt', status:'ok', channels:['channel 1'], time_column:'ts', size:12345, modified_time:1.0, default_channel:'channel 1', default_channel_matched_rule:true}]}`. Render `ImportScreen`, click the "Import Files" button (`data-testid="import-files-button"`). Assert a file row appears (`data-testid="file-row"`) showing the filename and a "ready" status, and that `fetch` was called with `POST /files/import` and body `{paths:['/data/57.txt']}`.
  2. **Import via folder picker.** Same shape, but `window.api.pickFolder` resolves `['/data/a.txt', '/data/b.txt']`, clicking "Import Folder" (`data-testid="import-folder-button"`). Assert both rows appear.
  3. **Per-file error isolation.** `importFiles` response has two results: one `status:'ok'`, one `status:'error', error:'No extractor succeeded'`. Assert the first row shows "ready" and the second shows the error message, distinctly (not both erroring, not the error swallowed).
  4. **Empty picker result.** `pickFiles` resolves `[]` (user canceled the dialog). Assert no `fetch` call happens and no rows are added — canceling is a no-op, not an error state.
  5. **Network-level import failure.** `fetch` rejects (backend unreachable). Since `importFiles` returns `ImportResponse | ApiError` (F1), assert the screen shows a clean error state (e.g. a `data-testid="import-error"` banner with the message) rather than crashing — this is exactly the failure mode F1's final-review fix wave made type-safe; prove the UI actually handles the `ApiError` branch.

- [ ] **Step 2: Run the tests to verify they fail**

  Run: `npm --prefix frontend run test -- ImportScreen`
  Expected: FAIL — component doesn't exist yet.

- [ ] **Step 3: Implement `ImportScreen.svelte`**

  Svelte 5 runes (`$state` for the file-row array). Two buttons ("Import Files" → `pickFiles()`, "Import Folder" → `pickFolder()`), both funneling into one `importPaths(paths: string[])` function: no-op on empty array; otherwise call `importFiles(paths)`, narrow the `ImportResponse | ApiError` result (`'error' in result` → show the import-error banner and stop; else iterate `result.results`, appending one row per entry — `status:'ok'` rows get `status:'ready'`, `status:'error'` rows keep their backend error message). Render the row list with filename + status/error, each row `data-testid="file-row"`.

- [ ] **Step 4: Wire into `App.svelte`**

  Replace `<HealthStatus />` with `<ImportScreen />` (remove the now-unused `HealthStatus` import — leave `HealthStatus.svelte`/`.test.ts` themselves in place, don't delete working scaffold files as part of this task; a later cleanup task can remove them once nothing references the pattern).

- [ ] **Step 5: Run the tests to verify they pass**

  Run: `npm --prefix frontend run test`
  Expected: all pass, including the 5 new tests; no regressions.

- [ ] **Step 6: Commit**

  ```bash
  git add frontend/src/components/import/ frontend/src/App.svelte
  git commit -m "Add Import screen with file/folder picking"
  ```

---

### Task 2: Auto-run beat detection + ETA + Stop

**Files:**
- Create: `frontend/src/lib/stores/eta.ts`
- Create: `frontend/src/lib/stores/eta.test.ts`
- Modify: `frontend/src/components/import/ImportScreen.svelte`
- Modify: `frontend/src/components/import/ImportScreen.test.ts`

**Interfaces:**
- Consumes: `detectBeats(path, channel)` (F1, `frontend/src/lib/api/beats.ts`), Task 1's `FileRow` shape (read the actual field names from the committed Task 1 code, not from this plan).
- Produces: `eta.ts` exports `recordSample(bytes: number, seconds: number): void` (accumulates into the session running total) and `estimate(bytes: number): number | null` (returns `null` if no sample has been recorded yet — the "Calculating…" case — else `bytes / (totalBytes / totalSeconds)`).

- [ ] **Step 1: Write `eta.ts`'s failing tests**

  In `eta.test.ts` (reset module state between tests — either re-import fresh each test via `vi.resetModules()`, or design `eta.ts` to export a `resetForTesting()` helper; either is fine, document whichever is chosen):
  1. **No samples yet.** `estimate(1000)` returns `null`.
  2. **One sample.** `recordSample(1000, 2)` (1000 bytes took 2s → 500 bytes/sec). `estimate(2000)` returns `4` (2000/500).
  3. **Running average across multiple samples.** `recordSample(1000, 2)` then `recordSample(3000, 3)` — total 4000 bytes / 5s = 800 bytes/sec. `estimate(1600)` returns `2`.

- [ ] **Step 2: Run, confirm fail, implement `eta.ts`, confirm pass**

  Run: `npm --prefix frontend run test -- eta.test.ts` (fail, then pass after implementing).

- [ ] **Step 3: Write the failing tests for auto-run + ETA + Stop in `ImportScreen.test.ts`**

  1. **Auto-run tickbox defaults checked.** Render the screen; assert `data-testid="auto-run-checkbox"` is checked by default.
  2. **Auto-run triggers detection sequentially.** Import 2 files (both `status:'ok'`, real `default_channel`s). Mock `detectBeats` (via the `fetch` mock for `POST /beats/detect`) to resolve `{status:'ok', count:15, mean_hr:650, elapsed_seconds:0.5, file_size_bytes:45877, beats:[...]}` for the first call and a second, different result for the second. Assert `fetch` was called for `/beats/detect` twice, in order, with each file's own `path`/`default_channel`, and assert both rows update to show a "beats detected" status with the real count (not the picker/import status anymore).
  3. **ETA shown as "Calculating…" for the first file, then a real number for the second.** Before the first file's detection call resolves, assert the visible ETA text is "Calculating…" (`data-testid="eta-badge"`). After it resolves (real `elapsed_seconds`/`file_size_bytes` now recorded), assert the second file's ETA badge shows a real number-derived string, not "Calculating…" again.
  4. **Unchecking auto-run skips detection entirely.** Uncheck the checkbox before importing; import a file; assert no `/beats/detect` call happens and the row stays at "ready."
  5. **Stop cancels the queue, not the in-flight file.** Import 3 files with auto-run on. Before the first `detectBeats` call's mocked promise resolves, click Stop (`data-testid="stop-button"`). Let the first call's promise resolve. Assert: the first file's row shows its real completed result (the in-flight call was allowed to finish); the second and third files' rows remain at "ready," never having triggered `/beats/detect` (only 1 total call to that endpoint, not 3).

- [ ] **Step 4: Run the tests to verify they fail**

  Run: `npm --prefix frontend run test -- ImportScreen`
  Expected: FAIL — new behavior doesn't exist yet.

- [ ] **Step 5: Implement**

  Add to `ImportScreen.svelte`: an `autoRun` boolean state (default `true`) bound to the checkbox; a `stopped` boolean flag; after a successful import batch, if `autoRun` is true, sequentially `await` `detectBeats(row.path, row.defaultChannel)` for each `status:'ready'` row *in order*, checking the `stopped` flag before starting each new file's call (not mid-call — matches the "let the in-flight file finish" requirement, since there's no cancellable in-flight primitive to interrupt a `fetch` mid-request here and the workflow spec doesn't ask for one). Before each call, render the ETA via `estimate(row.size)` (formatted, e.g. `~${seconds}s` or "Calculating…" if `null`). After each call resolves (success or the `ApiError`/backend-`status:'error'` case), call `recordSample(row.size, result.elapsed_seconds)` only on real success (never record a sample from a failed/errored call), and update that row's status/fields from the result. Stop button sets `stopped = true`.

- [ ] **Step 6: Run the tests to verify they pass**

  Run: `npm --prefix frontend run test`
  Expected: all pass.

- [ ] **Step 7: Commit**

  ```bash
  git add frontend/src/lib/stores/eta.ts frontend/src/lib/stores/eta.test.ts frontend/src/components/import/
  git commit -m "Add auto-run beat detection with learned ETA and batch Stop"
  ```

---

## Self-Review

- **Spec coverage:** the workflow spec's "Auto beat detection on load" (tickbox, default channel, re-run on channel change — channel re-selection itself is a Review-workspace concern, F3, not Import), "Beat detection progress & cancel" (ETA + Stop, exact semantics), and "Bulk upload" (multi-select + folder, per-file status) are all covered by Task 1/2.
- **Deliberately out of scope, and why**: per-file channel override (a dropdown to pick a non-default channel before/instead of auto-run) — the workflow spec frames channel selection as something the technician does in the Review workspace, and re-running detection on a channel change is explicitly an F3 concern once that workspace exists. Import screen only ever auto-runs against each file's *default* channel.
- **No placeholders**: no stub "Review" view is built — `App.svelte` renders `ImportScreen` directly until F3 gives it a real destination to switch to (see Global Constraints).
