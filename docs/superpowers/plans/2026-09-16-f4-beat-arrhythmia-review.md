# F4: Beat/Arrhythmia Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Click a beat marker on the graph to select it, see only the categories that actually fired for it, confirm/reject/reassign its classification, and re-run arrhythmia detection (heuristic/unsupervised/both) with progress feedback. This is the milestone that fixes the old desktop app's "Confirm Arrhythmia" workflow in the new UI, on top of the backend fix M7 already shipped.

**Architecture:** Extends `EcgGraph.svelte` (click-vs-drag disambiguation, hit-testing against rendered beat markers) and `ReviewWorkspace.svelte` (owns the selected beat, hosts a new `BeatCategoryPanel.svelte`, hosts new arrhythmia re-run controls). No new top-level screen.

**Tech Stack:** Svelte 5, the F1 API client (`persistence.ts`'s `updateBeatCategory`/`persistBeats`, `arrhythmia.ts`'s `detectArrhythmias`), the F3 `EcgGraph`/`ReviewWorkspace` components.

**Spec:** `docs/superpowers/specs/2026-09-15-frontend-implementation-design.md` (F4 scope), `docs/superpowers/specs/2026-09-14-ui-workflow-features-design.md` §1 ("Per-beat category list shows only categories that fired", "Reassign or remove a beat's category classification", "Rejecting an arrhythmia clears all its sub-category flags" — already true server-side per `BACKEND_OVERVIEW.md`'s M7 record, this milestone is the UI for it — "Fix Confirm Arrhythmia").

## Global Constraints — including one cross-milestone correction

- **Corrected scope from the original F1 design spec**: `PATCH /files/beats/category` requires a `files`+`beats` row to already exist in the backend's SQLite store — it was designed (M7) assuming `POST /files/beats` (persist a fresh detection result) has already been called for this file. The original milestone breakdown put ALL persistence in F5, which would make F4's confirm/reject/reassign UI non-functional (every action would hit the backend's clean "no persisted data for this file" error) until F5 ships. **Correction, ruled here rather than discovered mid-implementation**: F4 itself calls `persistBeats(path, channel)` (`POST /files/beats`) once, automatically, the first time a technician attempts any category action on a file this session (confirm/reject/reassign, or entering into "review mode" — implementer's choice on the exact trigger point, documented). This is the ONE persistence call F4 needs pulled forward; F5 still owns everything else (`GET /files/state` reload-on-reopen, the settings dialog, report export) — F4 does not build any of those.
- Beat selection state (which beat is currently selected, if any) lives in `ReviewWorkspace`, not `EcgGraph` — the category panel is a sibling of the graph, not nested inside it, so the selection needs to be visible to both.
- Category-action responses (`CategoryUpdateResult`: `status, ts, review_state, reassigned_category, error`) are applied directly to local state — no `GET /files/state` re-fetch needed after a mutation.
- Reassignable categories are exactly `bradycardia_absolute, tachycardia_absolute, skipped_beat, prem_beat, abn_cluster, other_arrhythmia` (the backend's `REASSIGNABLE_CATEGORIES` — `any_arrhythmia` is a derived summary flag, never a reassignment target; see `BACKEND_OVERVIEW.md`).
- After a successful arrhythmia re-run, the graph's beat markers must refresh (re-fetch `getBeatsWindow` for the current visible range) so newly-populated/changed category flags show up without a manual pan/zoom.
- Real component tests via `@testing-library/svelte`, mocking `fetch` only, matching every prior milestone's conventions. `data-testid` on anything tested.

---

### Task 1: Beat selection — click-vs-drag disambiguation, hit-testing, retained beat data

**Files:**
- Modify: `frontend/src/components/graph/EcgGraph.svelte`
- Modify: `frontend/src/components/graph/EcgGraph.test.ts`
- Modify: `frontend/src/components/review/ReviewWorkspace.svelte`
- Modify: `frontend/src/components/review/ReviewWorkspace.test.ts`

**Interfaces:**
- Produces: `EcgGraph` gains an `onBeatSelect: (beat: WindowBeat) => void` prop, called when a click (not a drag) lands within a small hit-test tolerance of a rendered beat marker. `EcgGraph` retains the raw `WindowBeat[]` from its last `getBeatsWindow` response (not just the `buildBeatAlignedData`-merged series) so a hit-test click can look up the full beat record, not just `ts`/`r_amplitude`.

- [ ] **Step 1: Write the failing tests**

  In `EcgGraph.test.ts`:
  1. **A plain click (no movement) on a beat marker's position fires `onBeatSelect` with that beat's full record.** Mount with a mocked `getBeatsWindow` response containing one beat at a known `ts`. Dispatch `mousedown` then `mouseup` at the same coordinates (no `mousemove` in between, or movement below the click-vs-drag threshold) at the pixel position corresponding to that beat's `ts`/`r_amplitude` on the plot. Assert `onBeatSelect` was called once with a `WindowBeat` matching every field of the mocked beat (not just `ts`).
  2. **A drag (movement beyond the threshold) does NOT fire `onBeatSelect`**, and still performs the existing pan behavior (or bad-data-mark behavior, if that mode is active) exactly as before this task. Assert `onBeatSelect` was never called, and (reusing the existing pan test's assertion shape) that the debounced re-fetch with a shifted range still occurs.
  3. **A click far from any beat marker does not fire `onBeatSelect`.** Same click gesture, but at a position with no nearby beat — assert no call.
  4. **Existing bad-data-mode drag test still passes unchanged** — clicking-to-select must not interfere with bad-data mode's own drag-to-mark behavior (run the existing suite, no new test needed here beyond confirming no regression).

- [ ] **Step 2: Run the tests to verify they fail**

  Run: `npm --prefix frontend run test -- EcgGraph`
  Expected: FAIL — `onBeatSelect` doesn't exist yet.

- [ ] **Step 3: Implement**

  In `EcgGraph.svelte`: retain the raw `WindowBeat[]` from the last successful `getBeatsWindow` call in component state (alongside whatever `buildBeatAlignedData` already produces for rendering — don't replace one with the other, keep both). Extend the existing `mousedown`/`mousemove`/`mouseup` handling (shared by pan and bad-data-mark) with click-vs-drag disambiguation: track the total pixel movement between `mousedown` and `mouseup`; if it stays under a small threshold (e.g. 5px — pick a reasonable constant, document it) and neither pan nor bad-data-marking's own drag logic actually moved the scale/started a mark, treat it as a click. On a click, convert the click's pixel position to data-space `(x, y)` via uPlot's own coordinate APIs (the same ones already used elsewhere in this file for cursor-centered zoom), then find the nearest beat in the retained `WindowBeat[]` array within a small tolerance (in pixels, converted back from data-space, or a simpler "nearest beat by x-distance, within N pixels" — document the exact approach), and call `onBeatSelect(beat)` if one is found.

- [ ] **Step 4: Wire into `ReviewWorkspace.svelte`**

  Add `selectedBeat: WindowBeat | null` (`$state`, init `null`). Pass `onBeatSelect={(beat) => selectedBeat = beat}` to `EcgGraph`. Reserve a slot below/beside the graph for Task 2's `BeatCategoryPanel` (same "leave a real slot, don't build a placeholder" discipline as F3's Task 1) — but since Task 2 lands in the same milestone, a minimal real rendering of `selectedBeat` (e.g. its `ts`/`hr` as plain text) is acceptable here as a stepping stone Task 2 replaces, since it's genuinely functional (proves the wiring works) rather than an inert stub. Document whichever choice is made.

- [ ] **Step 5: Run the tests to verify they pass, commit**

  Run: `npm --prefix frontend run test`
  Expected: all pass, no regressions.

  ```bash
  git add frontend/src/components/graph/ frontend/src/components/review/
  git commit -m "Add beat click-to-select with click-vs-drag disambiguation"
  ```

---

### Task 2: `BeatCategoryPanel` — confirm/reject/reassign, with the pulled-forward persist prerequisite

**Files:**
- Create: `frontend/src/components/review/BeatCategoryPanel.svelte`
- Create: `frontend/src/components/review/BeatCategoryPanel.test.ts`
- Modify: `frontend/src/components/review/ReviewWorkspace.svelte`
- Modify: `frontend/src/components/review/ReviewWorkspace.test.ts`

**Interfaces:**
- Consumes: `updateBeatCategory(path, ts, action, category?)`, `persistBeats(path, channel)` (F1, `frontend/src/lib/api/persistence.ts`).
- Produces: `BeatCategoryPanel` props `path: string`, `beat: WindowBeat`, `onUpdated: (result: CategoryUpdateResult) => void`.

- [ ] **Step 1: Write the failing tests**

  In `BeatCategoryPanel.test.ts`:
  1. **Only categories that fired render.** Render with a `beat` where `tachycardia_absolute: true`, `bradycardia_absolute: false`, `skipped_beat: null`, others `false`/`null`. Assert only "tachycardia_absolute" (or its human-readable label — implementer's choice, document it) appears in the rendered category list; `bradycardia_absolute`/`skipped_beat` do not.
  2. **No categories fired.** All category fields `false`/`null`. Assert a "no arrhythmia categories" (or equivalent) message renders instead of an empty list.
  3. **Confirm calls the API with the right params and reports the result.** Click `data-testid="confirm-button"`; assert `updateBeatCategory(path, beat.ts, 'confirm')` was called; assert `onUpdated` fires with the mocked response.
  4. **Reject calls the API correctly.** Same shape, `action: 'reject'`.
  5. **Reassign shows only the 6 reassignable categories as options** (not `any_arrhythmia`), and calls the API with the chosen one. Click `data-testid="reassign-button"` (or select from a dropdown — implementer's choice on exact UI, document it), pick e.g. `'prem_beat'`; assert `updateBeatCategory(path, beat.ts, 'reassign', 'prem_beat')`.
  6. **A "no persisted data for this file" error from the API surfaces a clear retry path**, not a silent failure — mock `updateBeatCategory` returning `{status:'error', error:'No persisted data for this file — run POST /files/beats first'}` (the real backend message per `BACKEND_OVERVIEW.md`); assert the panel shows a clean error state, distinct from a validation error (e.g. an invalid category).

- [ ] **Step 2: Run the tests to verify they fail**

  Run: `npm --prefix frontend run test -- BeatCategoryPanel`
  Expected: FAIL — component doesn't exist yet.

- [ ] **Step 3: Implement `BeatCategoryPanel.svelte`**

  Renders `beat.ts`/`beat.hr`/`beat.rr` for context. Category list: filter `bradycardia_absolute, tachycardia_absolute, skipped_beat, prem_beat, abn_cluster, other_arrhythmia` to those `=== true` on `beat` (matching the workflow spec's "only categories that fired" requirement exactly — `null`/`false` never shown as an assignable/visible category, only ever as "not flagged"). Confirm/Reject buttons call `updateBeatCategory(path, beat.ts, action)`. Reassign UI (dropdown or button group) offers exactly the 6 reassignable category names, calling `updateBeatCategory(path, beat.ts, 'reassign', chosen)`. On any result, call `onUpdated(result)`; on `status:'error'`, show the error message inline rather than throwing/crashing.

- [ ] **Step 4: Wire the persist-prerequisite into `ReviewWorkspace.svelte`**

  Before the FIRST category action a technician takes in a given review session for a file (implementer's choice of exact trigger: e.g. lazily on first `BeatCategoryPanel` action-button click, or eagerly once beats are first successfully detected/selected — document which and why), call `persistBeats(path, activeChannel)` if it hasn't already been called this session for the current `(path, activeChannel)` pair. Track this with a simple boolean/set in component state — no need for anything more elaborate. If `persistBeats` itself fails, show that error and do NOT attempt the category action that triggered it.

  Mount `<BeatCategoryPanel path={path} beat={selectedBeat} onUpdated={...} />` when `selectedBeat` is non-null, replacing Task 1's stepping-stone plain-text rendering. On `onUpdated`, update local state so the panel reflects the new `review_state`/`reassigned_category` immediately (no need to re-fetch anything).

- [ ] **Step 5: Run the tests to verify they pass, commit**

  ```bash
  git add frontend/src/components/review/
  git commit -m "Add BeatCategoryPanel with confirm/reject/reassign and persist-on-first-action"
  ```

---

### Task 3: Arrhythmia re-run controls (heuristic/unsupervised/both) with progress feedback

**Files:**
- Create: `frontend/src/components/review/ArrhythmiaControls.svelte`
- Create: `frontend/src/components/review/ArrhythmiaControls.test.ts`
- Modify: `frontend/src/components/review/ReviewWorkspace.svelte`
- Modify: `frontend/src/components/review/ReviewWorkspace.test.ts`

**Interfaces:**
- Consumes: `detectArrhythmias(path, channel, method)` (F1, `frontend/src/lib/api/arrhythmia.ts`).
- Produces: `ArrhythmiaControls` props `path: string`, `channel: string`, `onComplete: () => void` (called after a successful run, so `ReviewWorkspace` can trigger the graph's beat-marker refresh).

- [ ] **Step 1: Write the failing tests**

  In `ArrhythmiaControls.test.ts`:
  1. **Three method buttons, each calls the API with the right method string.** Click `data-testid="run-heuristic-button"`; assert `detectArrhythmias(path, channel, 'heuristic')`. Same for `'unsupervised'`/`'both'` (`run-unsupervised-button`/`run-both-button`).
  2. **Progress feedback while running.** Before the mocked call resolves, assert a running/pending indicator is visible (`data-testid="arrhythmia-running"`) and the three buttons are disabled (reuse the same "one state value, set synchronously before the first await" discipline established in `ImportScreen`'s `queueState` — read that pattern and apply it here, don't reinvent a weaker version).
  3. **On success, `onComplete` fires and the running state clears.**
  4. **On failure, the running state clears, an error shows, and `onComplete` does NOT fire** (the graph shouldn't refresh against a failed/unchanged result).

- [ ] **Step 2: Run the tests to verify they fail**

  Run: `npm --prefix frontend run test -- ArrhythmiaControls`
  Expected: FAIL.

- [ ] **Step 3: Implement `ArrhythmiaControls.svelte`**

  A single `running: boolean` state (or a small `'idle'|'running'` union if that reads more cleanly given `ReviewWorkspace`'s own existing status-union conventions — match house style), set `true` synchronously in each button's click handler before `await detectArrhythmias(...)`, reset in a `finally`. All three buttons `disabled={running}`. On success (`status === 'ok'`), call `onComplete()`. On failure, show the error, don't call `onComplete`.

- [ ] **Step 4: Wire into `ReviewWorkspace.svelte`**

  Mount `<ArrhythmiaControls path={path} channel={activeChannel} onComplete={refreshGraphBeats} />`. `refreshGraphBeats` needs to trigger `EcgGraph`'s beat re-fetch for its current visible range without disturbing the current pan/zoom position — check `EcgGraph`'s current props/API from Task 1's work and add whatever minimal hook is needed (e.g. a prop that increments on each call, which `EcgGraph` watches via an `$effect` to trigger a beats-only re-fetch at its current range — don't have it refetch/reset the channel data or the viewport, only the beats). Document the exact mechanism chosen.

- [ ] **Step 5: Run the full suite, commit**

  ```bash
  git add frontend/src/components/review/ frontend/src/components/graph/
  git commit -m "Add arrhythmia re-run controls with progress feedback"
  ```

---

## Self-Review

- **Spec coverage:** per-beat category list (only fired categories), confirm/reject/reassign, arrhythmia re-run (all 3 methods) with progress feedback — all covered. "Fix Confirm Arrhythmia" is satisfied by construction: this UI calls the M7-fixed `PATCH /files/beats/category` endpoint, which already correctly cascades a reject and never suffered the old app's unreliability.
- **Cross-milestone correction documented, not silently absorbed**: the `persistBeats`-before-first-category-action requirement is called out explicitly in Global Constraints as a deliberate scope pull-forward from F5, with the reasoning stated, mirroring how F1/F2/F3 handled similar scope-boundary discoveries (documented rulings, not silent narrowing).
- **No placeholders**: Task 1's stepping-stone plain-text beat display is explicitly replaced by Task 2's real panel in the same milestone, not carried forward.
- **Type/interface consistency**: `WindowBeat` (F3), `CategoryUpdateResult`/`ArrhythmiaDetectionResult` (F1) field names used exactly as already defined — no renamed fields.
