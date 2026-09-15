# Frontend Implementation Design

Builds on `2026-09-07-web-ui-redesign-design.md` (architecture) and
`2026-09-14-ui-workflow-features-design.md` (workflow/UX — the authority for
app flow and feature behavior; this doc only covers frontend structure/tech,
not UX decisions already made there). Backend is complete: `BACKEND_OVERVIEW.md`
has the full API reference (15 endpoints across file import, beat/arrhythmia
detection, settings, windowing, persistence, review mutations, report export).

## Scope

Build the real Svelte frontend against the real backend — no mocks, no
design-canvas mockups (per explicit earlier user instruction). Existing
scaffold (`frontend/`): plain Vite + Svelte 5 + TypeScript + Vitest, a
`getBackendUrl()` helper reading the port from Electron IPC, one demo
component (`HealthStatus.svelte`) to be replaced.

## Tech choices

- **No UI framework/component library** — small, focused set of custom
  components matching a clean scientific-tool aesthetic (per the original
  brainstorm: modern, sleek, professional). Plain CSS with a small design-
  tokens file (`src/lib/tokens.css`: color, spacing, type scale) rather than
  Tailwind/a framework — keeps the bundle small and the packaged app simple.
- **Charting: `uplot`** (tiny, canvas-based, built specifically for large
  time-series with pan/zoom) — the ECG graph must render a live-updating
  window from `GET /channels/window` as the technician pans/zooms, overlay
  beat/arrhythmia markers from `GET /beats/window`, and support click-drag
  range selection for bad-data marking. uPlot's small footprint and
  cursor/selection plugin API fit this directly; heavier libraries
  (Chart.js, ECharts) aren't built for this data shape or interaction
  pattern and add real weight to a packaged desktop app.
- **No router library** — the app has one shell with a small number of
  named views (Import, Review) driven by simple Svelte state, not URL-based
  navigation (an offline desktop tool has no shareable-URL requirement).
- **No global state management library** — Svelte 5 runes (`$state`,
  `$derived`) are sufficient for this app's scope; a small number of shared
  stores (current file, current file's persisted/live state) live in
  `src/lib/stores/`.
- **API client**: one typed wrapper module per backend router
  (`src/lib/api/files.ts`, `beats.ts`, `arrhythmia.ts`, `settings.ts`,
  `windowing.ts`, `persistence.ts`), each a thin `fetch()` wrapper returning
  the exact response shapes from `backend/models.py` (mirrored as TS
  interfaces in `src/lib/api/types.ts`). No codegen — the API surface is
  small (15 endpoints) and stable; hand-written types stay closer to the
  actual Pydantic models and are easier to keep in sync by inspection.

## Component structure

```
src/
  lib/
    api/           - typed fetch wrappers + shared response types
    stores/        - shared reactive state (current file, file registry)
    tokens.css     - design tokens
  components/
    import/        - ImportScreen, FileList, FileRow, ETABadge
    review/        - ReviewWorkspace, ChannelSelect, SettingsPanel
    graph/          - EcgGraph (uPlot wrapper), BadDataOverlay
    beats/          - BeatList, BeatCategoryControls
  App.svelte        - top-level shell: Import view <-> Review view
```

## Milestone breakdown

Mirrors the backend's shape: each milestone is a working, testable slice.

- **F1 — Foundation.** API client (all 15 endpoints, typed), design tokens,
  Electron IPC addition for a native "choose output directory" dialog
  (needed by F5's report export). The app shell (Import/Review view switch)
  is deferred to F3, which needs a shell to render its screen into anyway.
- **F2 — Import screen.** Multi-select + folder import, per-file status,
  "Auto-run beat detection" tickbox (checked by default), ETA display
  (starts "Calculating...", then a real running-average estimate — per the
  session's earlier ETA UX note, never a fabricated hardcoded number
  presented as real).
- **F3 — Review workspace shell + graph.** App shell (Import/Review view
  switch) plus channel select/switch (re-runs detection on change),
  `EcgGraph` wired to `GET /channels/window` with real pan/zoom,
  beat/arrhythmia markers from `GET /beats/window`, bad-data click-drag
  marking wired to `POST`/`DELETE /files/bad-data`.
- **F4 — Beat/arrhythmia review.** Click-to-select a beat on the graph,
  per-beat category list (only categories that actually fired),
  confirm/reject/reassign controls wired to `PATCH /files/beats/category`,
  arrhythmia re-run controls (heuristic/unsupervised/both) with progress
  feedback. **Also calls `POST /files/beats` once, automatically, before a
  file's first category action this session** — `PATCH .../category`
  requires a persisted row to exist, so this one persistence call is
  pulled forward from F5 (a scope correction made while planning F4, not
  part of the original milestone breakdown above).
- **F5 — Persistence + settings + export.** Load prior state on reopen via
  `GET /files/state`; autosave (call the M7 mutation endpoints on every
  action, no separate save button/timer — F4's own pulled-forward
  `POST /files/beats` call is the one exception already covered);
  settings dialog for `GET`/`PUT /settings`; "Generate Report" button
  (native output-dir picker + `POST /files/report`).

Each milestone gets its own implementation plan (`docs/superpowers/plans/`)
and runs through subagent-driven-development the same way M4-M7 did: Sonnet
implementer + Sonnet per-task review + Sonnet final whole-milestone review,
continuous execution without stopping between milestones.

## Testing

Component/unit tests via Vitest + `@testing-library/svelte` (already in the
scaffold). No real backend calls in tests — the API client module is the
mocking boundary (mock `fetch`, not individual component internals),
consistent with "test real behavior" applied to a frontend: real component
rendering/interaction, mocked network layer (there's no safe way to run the
real FastAPI backend + real `.venv` inside a Vitest run, and this project's
own backend tests already cover the real analysis pipeline end-to-end).
Real device/file testing (Windows, real `.adicht` files) explicitly
deferred by the user to a later phase — not part of this build's
verification loop.

## Out of scope for this build

Electron packaging (PyInstaller/Nuitka sidecar bundling), old-UI
(`main.py`/PySide6) removal — both already tracked as not-started in
`BACKEND_OVERVIEW.md` and not part of "frontend with backend integration."
