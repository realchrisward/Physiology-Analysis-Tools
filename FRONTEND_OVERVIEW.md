# Frontend Overview

Living doc for the Svelte frontend (`frontend/`) + Electron shell (`desktop/`)
on the `web-ui-redesign` branch. Update this as later milestones land, the
same way `BACKEND_OVERVIEW.md` tracks the (now complete) backend. Full
context: `docs/superpowers/specs/2026-09-15-frontend-implementation-design.md`
(tech choices, component structure, F1-F5 milestone breakdown) and
`docs/superpowers/specs/2026-09-14-ui-workflow-features-design.md` (UX/
workflow authority).

## Files

| File | Purpose |
|---|---|
| `frontend/src/lib/api.ts` | `getBackendUrl()` — resolves the real backend port via Electron IPC (`window.api.getBackendPort`), falls back to `http://127.0.0.1:8000` outside Electron. |
| `frontend/src/lib/api/http.ts` | `apiGet`/`apiPost` — shared fetch wrapper. Never throws: network failures and non-2xx responses are caught and returned as `{status:'error', error}`. |
| `frontend/src/lib/api/types.ts` | Hand-written TS interfaces mirroring every `backend/models.py` class the client uses, plus `ApiError` (the explicit failure-shape union member for the 3 endpoints whose success shape doesn't carry `status`/`error`). |
| `frontend/src/lib/api/{files,beats,arrhythmia,settings,windowing,persistence}.ts` | One thin wrapper file per backend router, all 15 endpoints. |
| `frontend/src/lib/tokens.css` | Design tokens: color (neutral scale + accent + semantic success/warning/danger, WCAG AA, light+dark), spacing, type (incl. a mono scale for numeric readouts), radii, shadow. |
| `desktop/main.js` | Electron main process. Spawns/tears down the backend subprocess; `ipcMain.handle`s: `get-backend-port`, `pick-files`, `pick-folder`, `pick-output-directory` (all dialog-based, filtered to `SUPPORTED_EXTENSIONS` — `adicht`, `txt`, `mat`, `gzip`, `edf`). |
| `desktop/preload.js` | `contextBridge.exposeInMainWorld('api', {...})` — the only surface the renderer can call into Electron/Node through. |

## Design decisions

- **No UI framework, no router, no state-management library** — plain CSS + design tokens, Svelte 5 runes for state, a small number of named views driven by simple component state (an offline desktop tool has no shareable-URL/deep-linking need).
- **Charting: `uplot`** (arrives in F3) — chosen for large time-series + pan/zoom + tiny footprint, matching the ECG graph's real requirements (see the frontend design spec for the full rationale).
- **API client never throws** — every function resolves to a value; callers check `.status`/narrow via `'error' in result`, mirroring the backend's own "never surprise the caller" convention. `SUPPORTED_EXTENSIONS` (`desktop/main.js`) is the single source of truth for supported file types on the Electron side — kept in sync with `backend/extractors.py`'s `EXTRACTOR_SPECS` by inspection (no shared source between the two processes; a real, if narrow, drift risk — noted below).
- **3 of the 15 API client functions have a `T | ApiError` return type**, not a bare `T` — `importFiles` (`ImportResponse`), `listFiles` (`FileImportResult[]`), `getSettings` (`SettingsPayload`) — because their success shapes don't naturally carry `status`/`error`, unlike the other 12. Any future endpoint wrapper whose success type also lacks those fields needs the same treatment; check before assuming the bare cast is safe.

## Known cross-effects / risks

- **`SUPPORTED_EXTENSIONS` (Electron) and `EXTRACTOR_SPECS` (backend Python) are two independent lists with the same intent** — nothing enforces they stay in sync. Already caught one real drift during F1 (`.edf` was missing from the Electron list despite being a live, `pyedflib`-backed extractor). Worth a shared source of truth (e.g. the backend exposing its supported extensions via an endpoint) if this drifts again.
- **Real device/file testing (Windows, real `.adicht` files) is explicitly deferred** — per direct user instruction, not part of this build's verification loop. Everything is verified via Vitest + mocked `fetch` + `svelte-check`, not a real backend or real hardware.

## Progress

- **Done**: F1 — API client (all 15 endpoints, typed, network-failure-safe), design tokens, Electron file/folder/output-directory pickers. 18 frontend tests passing (up from 5 at scaffold start), 5 desktop tests passing. Reviewed (per-task + whole-milestone), fix wave applied and re-reviewed clean.
- **Not started**: F2 (Import screen + app shell), F3 (review workspace + graph), F4 (beat/arrhythmia review), F5 (persistence + settings + report export). Electron packaging, old-UI removal — see `BACKEND_OVERVIEW.md`.
- **Branch**: `web-ui-redesign`, not merged to `main`.
