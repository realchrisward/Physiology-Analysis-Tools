# F1: Frontend Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Typed API client covering all 15 backend endpoints, shared design tokens, and the Electron IPC surface (file/folder/output-directory pickers) that F2-F5's screens build on. No new user-visible screen in this milestone — this is infrastructure, the same way M4 was infrastructure before any UI consumed it.

**Architecture:** `frontend/src/lib/api/` — one `types.ts` (hand-written TS interfaces mirroring `backend/models.py` exactly, no codegen), one `http.ts` (shared fetch wrapper: prepends the real backend URL from `getBackendUrl()`, catches network failures and returns them in the same `{status:"error", error}` shape every backend response already uses, so callers never need try/catch), and six router-scoped wrapper files. Electron: `desktop/main.js` gains three new `ipcMain.handle` entries using the already-imported `dialog` module; `desktop/preload.js`/`frontend/src/electron.d.ts` expose them to the renderer.

**Tech Stack:** TypeScript, Vitest + `@testing-library/svelte` (already in the scaffold), Electron `dialog` (already imported in `desktop/main.js`).

**Spec:** `docs/superpowers/specs/2026-09-15-frontend-implementation-design.md`

## Global Constraints

- Every API wrapper function returns the exact response shape the backend sends (`status: "ok"|"error"` always present) — never throws for a backend-reported error; only `http.ts`'s network-failure catch synthesizes an error shape, for the case where the backend can't be reached at all.
- `path` query-string params are URL-encoded (`encodeURIComponent`) — several real file paths contain spaces (confirmed this session: `"9 long.txt"` is a real example file).
- No new runtime npm dependencies in this milestone (uPlot arrives in F3, when the graph actually needs it).
- Match the existing test conventions exactly: `vi.stubGlobal('fetch', ...)`, `@testing-library/svelte`'s `render`/`screen`/`waitFor`, `data-testid` attributes for anything a test queries.
- `desktop/main.js`'s existing IPC handlers (`get-backend-port`) are NOT unit-tested directly (Electron app-lifecycle glue isn't practical to unit test without a running Electron instance) — the new handlers follow the same convention; don't invent a new testing approach for them.

---

### Task 1: API client — types + shared fetch wrapper + all 15 endpoint functions

**Files:**
- Create: `frontend/src/lib/api/types.ts`
- Create: `frontend/src/lib/api/http.ts`
- Create: `frontend/src/lib/api/files.ts`
- Create: `frontend/src/lib/api/beats.ts`
- Create: `frontend/src/lib/api/arrhythmia.ts`
- Create: `frontend/src/lib/api/settings.ts`
- Create: `frontend/src/lib/api/windowing.ts`
- Create: `frontend/src/lib/api/persistence.ts`
- Create: `frontend/src/lib/api/http.test.ts`
- Create: `frontend/src/lib/api/files.test.ts`
- Create: `frontend/src/lib/api/persistence.test.ts`

**Interfaces:**
- Produces: every function below, used directly by F2-F5's components.

- [ ] **Step 1: `types.ts`** — one TS interface per `backend/models.py` class actually needed by the client (request bodies inlined as function params, not separate types, except where a shape is reused). Exact fields, exact optionality (`| null` for Pydantic's `| None`), matching `backend/models.py` verbatim:
  - `FileImportResult`, `ImportResponse`
  - `Beat`, `BeatDetectionResult`
  - `BeatSettingsModel`, `ArrhythmiaSettingsModel`, `SettingsPayload`, `SettingsResult`
  - `ArrhythmiaBeat`, `ArrhythmiaDetectionResult`
  - `ChannelWindowResult`
  - `WindowBeat`, `BeatWindowResult`
  - `PersistedBeat`, `BadDataMark`, `FileStateResult`
  - `ChannelPersistResult`, `PersistBeatsResult`
  - `BadDataAddResult`, `BadDataDeleteResult`
  - `CategoryUpdateResult`
  - `ReportResult`

- [ ] **Step 2: Write `http.ts`'s failing tests** (`http.test.ts`)

  1. **Success passthrough.** `apiFetch<{status:string}>('/health')` with a mocked `fetch` resolving `{ok: true, json: async () => ({status:'ok'})}`. Assert the result is `{status:'ok'}` and `fetch` was called with `` `${backendUrl}/health` `` (backend URL from the existing `getBackendUrl()` — mock `window.api.getBackendPort` the same way `HealthStatus.test.ts` already does, or rely on the default `http://127.0.0.1:8000` when `window.api` is absent).
  2. **Network failure synthesized as a clean error.** Mocked `fetch` rejecting with `new Error('network error')`. Assert the result is `{status:'error', error: 'network error'}` (or equivalent — the exact message string, not swallowed).
  3. **Non-2xx HTTP passthrough.** Mocked `fetch` resolving `{ok: false, status: 500, json: async () => ({detail: 'boom'})}` — this shouldn't normally happen (the backend never 500s per its own convention) but the client must not crash if it does; assert a clean `{status:'error', error: ...}` shape, not an unhandled exception.
  4. **Query-string building with special characters.** A GET helper (used internally by `apiFetch` or a small `buildQuery(params)` helper) given `{path: '/Users/x/9 long.txt', channel: 'channel 1'}` produces a properly `encodeURIComponent`-escaped query string — assert the exact `fetch` call URL contains `9%20long.txt` and `channel%201`, not raw spaces.
  5. **POST body handling.** A POST helper given a JSON-serializable body sets `method: 'POST'`, `headers: {'Content-Type': 'application/json'}`, and `body: JSON.stringify(...)` — assert the exact `fetch` call arguments.

- [ ] **Step 3: Run the tests to verify they fail**

  Run: `npm --prefix frontend run test -- http.test.ts`
  Expected: FAIL — module doesn't exist yet.

- [ ] **Step 4: Implement `http.ts`**

  Two exported helpers used by every router file: `apiGet<T>(path: string, query?: Record<string, string | number>): Promise<T>` and `apiPost<T>(path: string, method: 'POST'|'PUT'|'PATCH'|'DELETE', body?: unknown): Promise<T>` (a single function with a `method` param is fine too — implementer's choice, document whichever shape is chosen since F2-F5 depend on it). Both: resolve `getBackendUrl()` (import from the existing `frontend/src/lib/api.ts` — do not duplicate this logic), build the URL (query params `encodeURIComponent`-escaped), `fetch`, and on any thrown exception OR a non-`ok` HTTP response, return `{status: 'error', error: <message>} as T` rather than throwing — every backend response type already has a `status`/`error` field so this cast is safe by construction. On success, `return await response.json() as T`.

- [ ] **Step 5: Run the tests to verify they pass**

  Run: `npm --prefix frontend run test -- http.test.ts`
  Expected: PASS, all 5.

- [ ] **Step 6: Implement the six router files**, each a thin wrapper over `apiGet`/`apiPost`:

  `files.ts`:
  - `importFiles(paths: string[]): Promise<ImportResponse>` — `apiPost('/files/import', 'POST', {paths})`
  - `listFiles(): Promise<FileImportResult[]>` — `apiGet('/files')`

  `beats.ts`:
  - `detectBeats(path: string, channel: string): Promise<BeatDetectionResult>` — `apiPost('/beats/detect', 'POST', {path, channel})`

  `arrhythmia.ts`:
  - `detectArrhythmias(path: string, channel: string, method: 'heuristic'|'unsupervised'|'both'): Promise<ArrhythmiaDetectionResult>` — `apiPost('/arrhythmia/detect', 'POST', {path, channel, method})`

  `settings.ts`:
  - `getSettings(): Promise<SettingsPayload>` — `apiGet('/settings')`
  - `putSettings(payload: SettingsPayload): Promise<SettingsResult>` — `apiPost('/settings', 'PUT', payload)`

  `windowing.ts`:
  - `getChannelWindow(path: string, channel: string, start: number, end: number, resolution: number): Promise<ChannelWindowResult>` — `apiGet('/channels/window', {path, channel, start, end, resolution})`
  - `getBeatsWindow(path: string, start: number, end: number): Promise<BeatWindowResult>` — `apiGet('/beats/window', {path, start, end})`

  `persistence.ts`:
  - `getFileState(path: string): Promise<FileStateResult>` — `apiGet('/files/state', {path})`
  - `putChannel(path: string, channel: string): Promise<ChannelPersistResult>` — `apiPost('/files/channel', 'PUT', {path, channel})`
  - `persistBeats(path: string, channel: string): Promise<PersistBeatsResult>` — `apiPost('/files/beats', 'POST', {path, channel})`
  - `updateBeatCategory(path: string, ts: number, action: 'confirm'|'reject'|'reassign', category?: string): Promise<CategoryUpdateResult>` — `apiPost('/files/beats/category', 'PATCH', {path, ts, action, category})`
  - `addBadData(path: string, start: number, stop: number): Promise<BadDataAddResult>` — `apiPost('/files/bad-data', 'POST', {path, start, stop})`
  - `deleteBadData(path: string, id: number): Promise<BadDataDeleteResult>` — `apiPost('/files/bad-data', 'DELETE', {path, id})`
  - `generateReport(path: string, outputDir: string): Promise<ReportResult>` — `apiPost('/files/report', 'POST', {path, output_dir: outputDir})`

- [ ] **Step 7: Write and run representative tests for the router files** (not exhaustive per-function coverage — `http.ts` already proves the shared mechanics; these confirm each file wires the right path/method/params)

  `files.test.ts`: one test for `importFiles` (asserts POST to `/files/import` with `{paths}` body) and one for `listFiles` (asserts GET `/files`).
  `persistence.test.ts`: one test each for `getFileState` (GET `/files/state?path=...`, with a path containing a space, asserting the encoded query string), `updateBeatCategory` (PATCH with the full body including an omitted `category`), and `generateReport` (asserts the body key is `output_dir`, snake_case, not `outputDir` — this is the one place a naming mismatch with the backend would silently break).

  Run: `npm --prefix frontend run test`
  Expected: all pass, no regressions in `HealthStatus.test.ts`.

- [ ] **Step 8: Commit**

  ```bash
  git add frontend/src/lib/api/
  git commit -m "Add typed API client for all 15 backend endpoints"
  ```

---

### Task 2: Design tokens

**Files:**
- Create: `frontend/src/lib/tokens.css`
- Modify: `frontend/src/app.css` (import tokens.css)

**Interfaces:**
- Produces: CSS custom properties every later component uses — establishes the naming scheme F2-F5 build on.

- [ ] **Step 1: Define tokens** in `frontend/src/lib/tokens.css`, on `:root`:

  - Color: a calm, professional slate/blue-gray neutral scale (`--color-bg`, `--color-surface`, `--color-border`, `--color-text`, `--color-text-muted`) plus a single accent color for interactive elements (`--color-accent`, `--color-accent-hover`) and semantic status colors (`--color-danger` for rejected/error states, `--color-warning` for flagged arrhythmias, `--color-success` for confirmed states) — pick real hex values (not placeholders), aiming for strong contrast (WCAG AA) since this is a data-review tool used for extended periods.
  - Spacing scale: `--space-1` through `--space-6` (e.g. 4/8/12/16/24/32px).
  - Type scale: `--font-sans` (system UI stack), `--font-mono` (for numeric readouts — HR values, timestamps — a monospace or tabular-nums font reads more precisely for a lab technician scanning numbers), `--font-size-sm/base/lg/xl`.
  - Radii/shadows: `--radius-sm/md`, one subtle `--shadow-sm` for panels.

- [ ] **Step 2: Import into `app.css`** — add `@import './lib/tokens.css';` at the top (or equivalent — match whatever import mechanism `app.css` already uses; read the current file first).

- [ ] **Step 3: Verify the build still works**

  Run: `npm --prefix frontend run build`
  Expected: succeeds, no CSS errors.

- [ ] **Step 4: Commit**

  ```bash
  git add frontend/src/lib/tokens.css frontend/src/app.css
  git commit -m "Add design tokens"
  ```

---

### Task 3: Electron IPC — file/folder/output-directory pickers

**Files:**
- Modify: `desktop/main.js`
- Modify: `desktop/preload.js`
- Modify: `frontend/src/electron.d.ts`

**Interfaces:**
- Produces: `window.api.pickFiles(): Promise<string[]>`, `window.api.pickFolder(): Promise<string[]>` (returns every recognized file path found recursively in the chosen folder — the actual recursive walk happens in the main process, not the renderer, since only the main process has Node's `fs`), `window.api.pickOutputDirectory(): Promise<string | null>`.

- [ ] **Step 1: Implement the three `ipcMain.handle` entries in `desktop/main.js`**, alongside the existing `get-backend-port` handler (inside the same `app.whenReady().then(...)` block, so they're registered once the app is ready — matching where `get-backend-port` is already registered):

  - `'pick-files'`: `dialog.showOpenDialog({properties: ['openFile', 'multiSelections'], filters: [{name: 'Supported files', extensions: ['adicht', 'txt', 'mat', 'gzip']}]})` — the exact real extension list this backend supports (verified this session against `backend/extractors.py`'s `EXTRACTOR_SPECS`: `.adicht`, `.txt`, `.mat`, `.gzip`). Return `result.canceled ? [] : result.filePaths`.
  - `'pick-folder'`: `dialog.showOpenDialog({properties: ['openDirectory']})`. If canceled, return `[]`. Else, recursively walk the chosen directory (Node's `fs.readdirSync(..., {recursive: true})` or an explicit recursive walk — either is fine) and return every file whose extension matches the same supported-extensions list, as absolute paths.
  - `'pick-output-directory'`: `dialog.showOpenDialog({properties: ['openDirectory']})`. Return `result.canceled ? null : result.filePaths[0]`.

- [ ] **Step 2: Expose in `desktop/preload.js`**

  Add three entries to the existing `contextBridge.exposeInMainWorld('api', {...})` object: `pickFiles: () => ipcRenderer.invoke('pick-files')`, `pickFolder: () => ipcRenderer.invoke('pick-folder')`, `pickOutputDirectory: () => ipcRenderer.invoke('pick-output-directory')`.

- [ ] **Step 3: Update `frontend/src/electron.d.ts`**

  Extend the existing `Window.api` interface with the three new methods' exact signatures: `pickFiles: () => Promise<string[]>`, `pickFolder: () => Promise<string[]>`, `pickOutputDirectory: () => Promise<string | null>`.

- [ ] **Step 4: Verify**

  No dedicated test for these (per Global Constraints — matches the existing `get-backend-port` convention, which also has no direct test). Run `npm --prefix desktop run test` to confirm `backend-process.test.js` is unaffected, and `npm --prefix frontend run check` to confirm the `electron.d.ts` change type-checks cleanly against nothing yet consuming it.

- [ ] **Step 5: Commit**

  ```bash
  git add desktop/main.js desktop/preload.js frontend/src/electron.d.ts
  git commit -m "Add Electron IPC pickers for files, folders, and report output directory"
  ```

---

## Self-Review

- **Spec coverage:** the design spec's F1 scope (API client, design tokens, Electron IPC for the output-directory picker plus the file/folder pickers F2 will need) is covered by the three tasks above.
- **Type/interface consistency:** every API client function's parameter/return names match `backend/models.py` field names exactly (including the one real trap: `output_dir` snake_case in the request body vs. `outputDir` camelCase in the TS function signature — call site translates it, tested explicitly in Task 1 Step 7).
- **No placeholders:** every step produces real, working code — no `TODO`/stub screens. F1 deliberately has no new visible screen (infrastructure milestone), consistent with how M4 had no UI-visible change either.
- **App shell scope:** the design spec's original F1 bullet listed the app shell (Import/Review view switch) as in-scope, but this plan intentionally defers it to F2 instead — F2 (Import screen) needs a shell to render into anyway, so building the shell as part of F2 avoids an empty intermediate screen in F1 with no content to switch to. This is a deliberate reordering, not a silent drop; the spec's milestone breakdown has been updated to match.
