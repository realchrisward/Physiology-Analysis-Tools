# Physiology Analysis Tools — Web UI Redesign

## 1. Summary

Replace the PySide6/Qt desktop UI with a local web UI: a Svelte frontend
running inside an Electron shell, talking to the existing Python analysis
code via a local-only backend API. Ships as one offline desktop installer
per OS (Windows `.exe`, macOS `.dmg`). The Python analysis library keeps
shipping to PyPI separately, unchanged in spirit, for scripting/headless use.

**Chosen approach**: Electron + Python sidecar + Svelte (Option A from
brainstorming). Rejected: QML modernization (same ecosystem limits we're
trying to escape) and pywebview (smaller bundle, but depends on the OS
webview being present — ruled out per offline/zero-dependency requirement).

## 2. Goals

- Fix the graph: accurate, deterministic rendering that doesn't shift shape
  on zoom/pan; free-form navigation (drag/scroll/pinch), not manual
  y-value-entry buttons.
- Modern, discoverable UI: proper file picker/drag-drop, visible
  loading/processing states, cleaner layout — built by people who are
  actually fluent in a modern frontend stack.
- Fully offline. No installed component may be assumed present on the lab
  PC (no reliance on system WebView2/WKWebView, no CDN calls at runtime).
- Keep the core analysis logic (signal converters, beat detection,
  arrhythmia detection) as the source of truth, reusable outside the GUI.

## 3. Stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | Svelte (Vite), TypeScript | Team's strongest skillset; small runtime; fast dev loop |
| Charting | uPlot | Canvas-based, built for large real-time timeseries, tiny footprint |
| Desktop shell | Electron | Bundles Chromium+Node itself — the only realistic way to get zero dependency on the OS's webview |
| Backend | Python, FastAPI + uvicorn | Reuses existing pandas/scipy/sklearn analysis code almost as-is; async endpoints replace the current QThread workaround |
| Backend↔Frontend transport | HTTP (REST) + WebSocket | REST for requests (load file, get data window, run analysis); WebSocket for progress/status streaming |
| Packaging (desktop app) | electron-builder + PyInstaller/Nuitka (backend frozen to one executable, bundled as an Electron resource) | Produces a single offline installer per OS |
| Packaging (library) | Existing hatchling/PyPI setup, mostly unchanged | Keeps `pip install physiology_analysis_tools` working for headless/scripted use |

## 4. Repository shape

The core analysis package stays pip-installable and GUI-free — this is the
main structural change from today:

```
physiology_analysis_tools/          # existing pip package, GUI code removed
├── modules/                        # unchanged: heartbeat_detection, arrhythmia_detection,
│                                    # ml_tools, signal_converters/*
backend/                            # new: FastAPI app, imports physiology_analysis_tools
├── api/                            # routes: files, channels, data windows, analysis, reports
├── worker/                         # background analysis tasks (replaces QThread pattern)
frontend/                           # new: Svelte + Vite app
├── src/
desktop/                            # new: Electron main process, packaging config
├── main.js                         # spawns backend subprocess, opens window, manages lifecycle
```

`main.py`, `ecg_analysis_tool.ui`, and all PySide6/pyqtgraph code are
removed once the new UI reaches parity — they are not kept in parallel
long-term.

## 5. System architecture

```
┌─────────────────────────────── Electron app (one process tree) ───────────────────────────────┐
│                                                                                                  │
│  ┌───────────────────────────┐        localhost:PORT         ┌─────────────────────────────┐   │
│  │  Renderer process          │  <───── HTTP + WebSocket ────>│  Python backend (subprocess)│   │
│  │  Svelte UI + uPlot graph   │        127.0.0.1 only          │  FastAPI + physiology_       │   │
│  └───────────────────────────┘                                 analysis_tools library         │   │
│              ▲                                                └─────────────────────────────┘   │
│              │ spawns, health-checks, tears down                       ▲                        │
│  ┌───────────────────────────┐                                          │                        │
│  │  Electron main process     │──────────────────────────────────────────┘                        │
│  │  (Node) — app lifecycle,   │                                                                    │
│  │  window mgmt, native menus,│                                                                    │
│  │  native file dialogs        │                                                                    │
│  └───────────────────────────┘                                                                    │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

On launch: Electron main process starts the frozen Python backend
executable on a random free port, polls a `/health` endpoint until ready
(with a loading screen shown meanwhile), then loads the Svelte app pointed
at that port. On quit, the main process terminates the Python subprocess.
The API binds to `127.0.0.1` only — never reachable from the network.

## 6. Data flow (the graph, specifically)

This is where the current app has its worst bug and biggest UX gap, so it
gets a dedicated design:

- On file load, the backend builds a **multi-resolution index** per signal
  channel: several pre-downsampled tiers (like map zoom levels), computed
  once and cached, instead of recomputing bins live from the plot widget's
  pixel width on every redraw (today's approach, and the likely cause of
  the "shape changes on zoom" bug — same time range, slightly different
  live-computed bins each redraw).
- The frontend requests `GET /channels/{id}/window?start=&end=&resolution=`;
  the backend picks the matching pre-computed tier (or computes and caches
  it on first request at a new resolution) and returns points via the
  min-max-per-bin method already used today (kept — it correctly preserves
  spikes; the bug is the *recomputation*, not the *algorithm*).
- Same range + resolution always returns the same cached result →
  deterministic shape regardless of how the user got there (dragging,
  scrolling, typing a range).
- Beat/arrhythmia markers ride along the same windowed-request pattern so
  they stay in sync with whatever range is currently rendered.

## 7. Long-running analysis & progress states

Arrhythmia analysis (PCA/DBSCAN) is the slow step today and currently
freezes the UI without a QThread workaround. In the new architecture:
`POST /analysis/arrhythmia` kicks off a background task in the Python
process; progress updates stream back over the WebSocket
(`queued → running (x%) → done/error`); the frontend renders this as an
actual progress UI instead of an indeterminate spinner. Same pattern
covers file loading and report generation.

## 8. Error handling

- Backend: structured JSON errors (`{code, message}`) on every endpoint;
  exceptions in analysis code are caught at the API boundary and reported,
  not left to crash the process.
- Frontend: toasts for recoverable errors (bad file, failed extractor);
  a full-screen fatal state if the backend subprocess dies or never
  becomes healthy (with a "view logs" affordance, since technicians can't
  be expected to open a terminal).
- Electron main process: detects backend subprocess exit and either
  restarts it once or surfaces the fatal state above — the app is useless
  without it, so this can't fail silently.

## 9. Testing

There is no automated test suite today. This is a natural point to start
one, kept minimal:
- Backend: pytest + FastAPI `TestClient` for the API layer; existing
  analysis functions can be tested directly (they're already pure
  DataFrame-in/DataFrame-out).
- Frontend: component tests for the graph windowing logic (deterministic
  by design, so easy to assert on) and Playwright for the golden path
  (load file → detect beats → run arrhythmia analysis → export report).

## 10. Benefits vs. costs

**Benefits**
- Fixes the zoom/shape bug structurally, not with a patch.
- Free-form graph navigation, modern file selection, real loading states —
  all comparatively cheap in a web stack, all currently painful in Qt widgets.
- Analysis code decouples from the GUI entirely — testable in isolation,
  and still usable headless via the existing pip package.
- Team builds in the stack they're actually fluent in.

**Costs**
- Bundle size ~200–300MB (accepted as fine for a desktop install).
- New failure surface: a process boundary between Electron and Python that
  doesn't exist today (subprocess startup/health/teardown, port handling).
- Two runtimes/toolchains to build and ship (Node + Python), vs. one today.
- Full rewrite of the GUI layer — no incremental migration path; old and
  new UI won't run side by side.
- Packaging pipeline changes completely (electron-builder replacing the
  current Nuitka-exe flow for the *app*; Nuitka/PyInstaller still used, but
  now just to freeze the backend as a sidecar).

## 11. Open questions

- Should the FastAPI backend also be published as an installable
  library/CLI (for headless batch analysis), or kept purely as the
  desktop app's internal sidecar?
- Auto-update strategy for the desktop app (electron-builder supports it,
  but lab PCs being offline may make this moot — manual reinstall may be
  simpler and more predictable for a regulated/lab environment).
- Migration of existing `.pkl.gzip` "already processed" files and any
  in-flight user data/settings from the old app — is there anything to
  carry forward, or is this a clean cutover?
