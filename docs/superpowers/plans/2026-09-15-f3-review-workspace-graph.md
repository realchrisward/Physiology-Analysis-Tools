# F3: Review Workspace + Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The app shell (Import ↔ Review view switch), a Review workspace with channel selection, and the ECG graph — real pan/zoom against `GET /channels/window`, beat/arrhythmia markers from `GET /beats/window`, and bad-data click-drag marking wired to `POST`/`DELETE /files/bad-data`. This is the app's most technically involved frontend milestone: the graph is the first real npm runtime dependency (`uplot`) and the first component with live user-driven interaction against a windowed backend endpoint.

**Architecture:** `App.svelte` gains `view: 'import'|'review'` and `selectedFile` state; `ImportScreen` gets an `onReview(row)` callback prop, called from a new per-row "Review" action (only enabled once a row has a `defaultChannel`). `ReviewWorkspace.svelte` owns the current file's channel selection and hosts `EcgGraph.svelte` (a thin uPlot wrapper: owns the plot instance, the visible x-range, and re-fetches windowed data on every range change) plus a `BadDataControls` toggle. No persistence integration in this milestone — `GET /files/state` (loading prior saved state on reopen) is explicitly F5's job; F3 works entirely off live in-memory detection results.

**Tech Stack:** Svelte 5, `uplot` (new dependency — canvas-based time-series charting, chosen in F1's design spec for its tiny footprint and pan/zoom fit), the F1 API client (`windowing.ts`, `persistence.ts` for `addBadData`/`deleteBadData` only — not `getFileState`).

**Spec:** `docs/superpowers/specs/2026-09-15-frontend-implementation-design.md` (F3 scope, charting rationale), `docs/superpowers/specs/2026-09-14-ui-workflow-features-design.md` §1 ("Free-form graph pan/zoom... single reset view button", "Bad data marking").

## Global Constraints

- Add `uplot` to `frontend/package.json` dependencies (not devDependencies — it ships in the built app), pinned to `1.6.31` (a real, stable published version). Import its required CSS (`uplot/dist/uPlot.min.css`) once, in `EcgGraph.svelte` or `app.css` — check which the implementer finds cleaner, document the choice.
- **No persistence in this milestone.** Bad-data marks added via `POST /files/bad-data` are also kept in local `EcgGraph`/`ReviewWorkspace` state for immediate rendering this session; there is no `GET /files/state` call to hydrate marks from a prior session — that hydration is explicitly F5's job. Don't build a stub for it.
- **Resolution** passed to `GET /channels/window` is the plot container's real rendered pixel width (`bind:clientWidth` on the container element), not a hardcoded number — this is what makes the backend's downsampling produce a graph-appropriate point density.
- Debounce re-fetching windowed data on pan/zoom (150ms after the last scale change) — a raw drag/wheel event fires many times per second; fetching on every one would flood the backend and isn't what "the same query always returns the identical cached result" (the backend's own memoization) is optimized for.
- "Reset view" restores the x-scale to the full data extent computed on initial load (the first `GET /channels/window` call's returned `x` array's min/max) — not a hardcoded guess.
- Every new component gets `data-testid` attributes on anything a test queries. Real component tests via `@testing-library/svelte`, mocking `fetch`/`window.api` only — for `EcgGraph` specifically, since `uplot` does real canvas rendering that jsdom (the test environment) doesn't support, tests verify the component's *data-fetching and state* behavior (what it calls, with what params, in response to what interaction) rather than pixel-level canvas output; check `frontend/vite.config.ts`/`frontend/src/test-setup.ts` for the current jsdom setup before assuming what's available.

---

### Task 1: App shell + Review workspace + channel selection

**Files:**
- Create: `frontend/src/components/review/ReviewWorkspace.svelte`
- Create: `frontend/src/components/review/ReviewWorkspace.test.ts`
- Modify: `frontend/src/App.svelte`
- Modify: `frontend/src/App.test.ts` (create if it doesn't exist yet — check first)
- Modify: `frontend/src/components/import/ImportScreen.svelte`
- Modify: `frontend/src/components/import/ImportScreen.test.ts`

**Interfaces:**
- Produces: `ImportScreen` gains an `onReview: (row: FileRow) => void` prop (optional — only called when the user clicks a "Review" action on a row that has a non-null `defaultChannel`; read the actual current `FileRow` shape from `ImportScreen.svelte` first, it already has `channels`/`defaultChannel` fields this task needs). `App.svelte` passes a handler that sets `view = 'review'` and `selectedFile = {path, channels, defaultChannel}`.

- [ ] **Step 1: Write the failing tests**

  1. **`ImportScreen`: Review action appears only for ready/detected rows with a channel, and fires the callback.** Render with a mocked `onReview` prop; import a file (mock response with a real `default_channel`); click the new `data-testid="review-button"` on that row; assert `onReview` was called with the row's `path`/`channels`/`defaultChannel`. Assert a row still in `'error'` status has no Review button at all.
  2. **`App.svelte`: switching to Review renders `ReviewWorkspace` with the selected file, and a "Back to Import" control returns to `ImportScreen`.** Render `App`; simulate the `ImportScreen`→`onReview` callback firing (either by finding a real path through the rendered tree with a mocked import, or by testing `App`'s view-state logic directly if that's cleaner given how `ImportScreen` is wired in — implementer's judgment, document which). Assert `ReviewWorkspace`'s content becomes visible (e.g. its `data-testid="review-workspace"` root) and `ImportScreen`'s content (e.g. `data-testid="import-files-button"`) is no longer rendered. Click `data-testid="back-to-import-button"`; assert the reverse.
  3. **`ReviewWorkspace`: channel dropdown defaults to the file's default channel, re-runs detection on change.** Render `ReviewWorkspace` with `path`, `channels: ['channel 1', 'channel 2']`, `defaultChannel: 'channel 1'`. Assert the `data-testid="channel-select"` dropdown's value is `'channel 1'`. Change it to `'channel 2'`; mock `fetch` for `POST /beats/detect`; assert `detectBeats` was called with `(path, 'channel 2')`, and assert a detection-summary area (`data-testid="detection-summary"`) updates from the mocked response's `count`/`mean_hr`/`duration` once it resolves.

- [ ] **Step 2: Run the tests to verify they fail**

  Run: `npm --prefix frontend run test -- App ReviewWorkspace ImportScreen`
  Expected: FAIL — new behavior/component doesn't exist yet.

- [ ] **Step 3: Implement**

  `ImportScreen.svelte`: add an `onReview` prop; render a "Review" button per row (`data-testid="review-button"`, disabled/absent when `defaultChannel` is `null` or `status === 'error'`) that calls `onReview({path: row.path, channels: row.channels, defaultChannel: row.defaultChannel})`.

  `ReviewWorkspace.svelte`: props `path: string`, `channels: string[]`, `defaultChannel: string`. `$state` for `selectedChannel` (init to `defaultChannel`) and a detection-summary object (`count`/`meanHr`/`duration`/`status`, `null` until a detection resolves). A `<select data-testid="channel-select">` bound to `selectedChannel`; on change, call `detectBeats(path, selectedChannel)` and update the summary from the result (narrow `status !== 'ok'` the same way `ImportScreen` already does for its own detection calls — read that code for the established pattern). This component is also where Task 2/3 mount `EcgGraph` — leave a clear spot for it (e.g. a `<div class="graph-area">` below the channel selector), but do not build a placeholder graph component here; Task 2 adds the real one in the same file.

  `App.svelte`: `view: 'import'|'review'` (`$state`, init `'import'`), `selectedFile: {path, channels, defaultChannel} | null` (`$state`). Render `ImportScreen` with `onReview={(row) => { selectedFile = row; view = 'review' }}` when `view === 'import'`; render `ReviewWorkspace` with a "Back to Import" button (`data-testid="back-to-import-button"`, sets `view = 'import'`) when `view === 'review'`.

- [ ] **Step 4: Run the tests to verify they pass**

  Run: `npm --prefix frontend run test`
  Expected: all pass, no regressions.

- [ ] **Step 5: Commit**

  ```bash
  git add frontend/src/App.svelte frontend/src/App.test.ts frontend/src/components/review/ frontend/src/components/import/
  git commit -m "Add app shell and Review workspace with channel selection"
  ```

---

### Task 2: `EcgGraph` — windowed data, real pan/zoom, reset view

**Files:**
- Create: `frontend/src/components/graph/EcgGraph.svelte`
- Create: `frontend/src/components/graph/EcgGraph.test.ts`
- Modify: `frontend/src/components/review/ReviewWorkspace.svelte`
- Modify: `frontend/package.json` (add `uplot` dependency)

**Interfaces:**
- Consumes: `getChannelWindow(path, channel, start, end, resolution)` (F1, `frontend/src/lib/api/windowing.ts`).
- Produces: `EcgGraph` props `path: string`, `channel: string`. Mounted inside `ReviewWorkspace`'s graph area, re-mounted (or its props updated) whenever `selectedChannel` changes — either approach is fine, document which.

- [ ] **Step 1: Write the failing tests**

  In `EcgGraph.test.ts` (mocking `fetch` for `/channels/window`, not mocking `uplot` itself unless jsdom's lack of canvas support forces it — check `frontend/src/test-setup.ts` first and document whatever approach is needed):

  1. **Initial load fetches the full range and renders without error.** Mount with `path`/`channel`. Assert `fetch` was called for `GET /channels/window` with `path`/`channel` query params and `start=0` (or however "full range" is expressed — document the exact initial query), and that the component doesn't throw/crash once the mocked response resolves.
  2. **Resolution matches the real container width.** Mock the container's `clientWidth` (jsdom supports this via a direct property set, or via `Object.defineProperty` — implementer's judgment) to a known value, e.g. `800`; assert the `resolution` query param sent to `/channels/window` matches it (or a documented, deterministic function of it).
  3. **Pan/zoom triggers a debounced re-fetch with the new range.** Simulate the interaction that changes the visible x-range (however Task 2 actually wires pan/zoom — read the real component once implemented rather than guessing the exact DOM event here; this test is written after Step 3's implementation, following TDD's spirit if the exact interaction mechanics only become concrete during implementation — acceptable deviation from strict step-ordering for this one sub-case, since uPlot's real interaction API needs to exist before it can be simulated). Advance fake timers past 150ms; assert a second `/channels/window` call fired with different `start`/`end` than the first.
  4. **Reset view restores the initial full extent.** After a pan/zoom-triggered re-fetch, click `data-testid="reset-view-button"`; assert the x-scale (whatever observable proxy for "current visible range" the implementation exposes — e.g. a data attribute, or the next `/channels/window` call's params) returns to the initial load's range.

- [ ] **Step 2-4: TDD cycle**

  Run tests (fail), implement, run tests (pass) — same convention as every earlier task.

  Implementation notes (behavioral contract, not literal code — uPlot's real API is well-documented and stable; the implementer should consult it directly rather than have code transcribed here):
  - On mount: fetch `getChannelWindow(path, channel, 0, Number.MAX_SAFE_INTEGER, containerWidth)` — passing an effectively-unbounded `end` returns the file's whole real range (the backend filters `time_column` against `[start, end]`, so an oversized `end` just means "everything," no new backend behavior needed). Store the response's `x[0]`/`x[last]` as the "full extent" for Reset View. Initialize the uPlot instance with this data and this scale range.
  - Wheel = zoom (in/out, centered on cursor position); left-drag (no modifier) = pan. Both mutate the uPlot x-scale directly (`u.setScale`), then schedule (debounced 150ms) a re-fetch of `getChannelWindow` for the new visible `[min, max]` at the current `containerWidth`, replacing the plot's data with the fresh response on resolution.
  - "Reset view" button resets the x-scale to the stored full extent and re-fetches at that range.
  - Container width is tracked via `bind:clientWidth` on the plot's wrapping element; if it changes (window resize), treat it the same as a pan/zoom (debounced re-fetch at the new resolution).

- [ ] **Step 5: Wire into `ReviewWorkspace.svelte`**

  Mount `<EcgGraph path={path} channel={selectedChannel} />` in the graph area reserved by Task 1.

- [ ] **Step 6: Run the full suite, commit**

  ```bash
  git add frontend/package.json frontend/package-lock.json frontend/src/components/graph/ frontend/src/components/review/ReviewWorkspace.svelte
  git commit -m "Add EcgGraph with real windowed pan/zoom and reset view"
  ```

---

### Task 3: Beat/arrhythmia markers + bad-data marking

**Files:**
- Modify: `frontend/src/components/graph/EcgGraph.svelte`
- Modify: `frontend/src/components/graph/EcgGraph.test.ts`
- Create: `frontend/src/components/graph/BadDataControls.svelte` (or fold into `EcgGraph` if the implementer judges that cleaner given how tightly bad-data marking is coupled to the plot's drag interaction — document the choice)

**Interfaces:**
- Consumes: `getBeatsWindow(path, start, end)`, `addBadData(path, start, stop)`, `deleteBadData(path, id)` (F1, `frontend/src/lib/api/windowing.ts`/`persistence.ts`).
- Produces: bad-data marks tracked in local component state this session (no persistence-loading in F3 — see Global Constraints).

- [ ] **Step 1: Write the failing tests**

  1. **Beat markers fetched alongside channel data.** On the same range-change (initial load and every pan/zoom re-fetch) that triggers `getChannelWindow`, assert `getBeatsWindow(path, start, end)` is also called with the matching range.
  2. **Beats render as a distinct marker series, arrhythmia-flagged beats visually distinct.** Mock a `getBeatsWindow` response with 2 beats, one `any_arrhythmia: true`, one `any_arrhythmia: false` (and one more with `any_arrhythmia: null`, the "not yet arrhythmia-detected" case) — assert the plot receives 3 distinguishable marker points (however "distinguishable" is observable in a test without real canvas rendering — e.g. the series/point data passed to uPlot, not pixel output; document the exact assertion).
  3. **"Mark Bad Data" mode toggles drag behavior and adds a mark.** Click `data-testid="bad-data-mode-button"` to enter bad-data mode. Simulate a drag on the plot (start/end x positions, in either order — the backend already auto-sorts, so the test doesn't need to assert client-side sorting, just that both values are sent). Assert `addBadData(path, start, stop)` was called, and that the returned mark (with its real `id`) is now rendered/tracked locally (assert on whatever local state or DOM the implementation exposes for "current bad-data marks this session").
  4. **Clicking an existing bad-data mark removes it.** With one mark already present (from the prior test's flow, or seeded directly into component state), click it (`data-testid="bad-data-mark"` or similar); assert `deleteBadData(path, mark.id)` was called and the mark is removed from local state/rendering.
  5. **Bad-data mode is off by default** — dragging without first entering the mode pans the graph (Task 2's behavior), not add a mark; assert `addBadData` is NOT called on a plain drag.

- [ ] **Step 2-4: TDD cycle**

  Same convention. Implementation notes:
  - `getBeatsWindow`'s response `beats` array maps onto a second uPlot series (e.g. plotted at each beat's `ts`/`r_amplitude`, `paths: null` with a `points`-only renderer) — color/style driven by `any_arrhythmia` (`true`→flagged color from `tokens.css`, `false`→neutral, `null`→a third "not evaluated" treatment, e.g. dimmed).
  - Bad-data mode is a boolean toggle; while active, the drag handler calls `addBadData` on release instead of Task 2's pan logic (the two behaviors are mutually exclusive per drag, gated by the mode flag).
  - Existing marks render as shaded x-axis bands (uPlot supports this via a plugin/hook drawing rects between two x values) or an equivalent visual treatment — implementer's judgment on exact rendering approach, document it; the important contract is that a technician can see which ranges are marked and remove one.

- [ ] **Step 5: Run the full suite, commit**

  ```bash
  git add frontend/src/components/graph/
  git commit -m "Add beat/arrhythmia markers and bad-data click-drag marking"
  ```

---

## Self-Review

- **Spec coverage:** app shell (deferred here from F1/F2 per their own scope rulings), channel select/switch with re-run-on-change, graph with real pan/zoom + reset view, beat/arrhythmia markers, bad-data click-drag marking — all covered.
- **Deliberately out of scope, and why:** persistence/reopen (`GET /files/state`) is F5's; per-beat confirm/reject/reassign controls and arrhythmia re-run controls (heuristic/unsupervised/both) are F4's — this milestone only renders markers, it doesn't yet let a technician act on them.
- **No placeholders:** `ReviewWorkspace`'s graph area is filled by Task 2's real `EcgGraph` in the same milestone, not a stub carried into a later one.
