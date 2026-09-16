# Dev / Testing Setup

Branch: `web-ui-redesign`. Requires Python 3.12+ and Node 20+ (tested on
Node 22). Always use the project's own `.venv` — never the bare system
`python3` (it may resolve to a stale, separately-published version of this
package).

## 1. Backend (Python)

```bash
# from repo root
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e .                   # the analysis library (pyproject.toml)
pip install -r backend/requirements.txt   # FastAPI backend + test deps
```

Run the backend standalone (for manual API testing):
```bash
source .venv/bin/activate
uvicorn backend.app:app --reload
```

Run backend tests:
```bash
source .venv/bin/activate
pytest backend/ -v
```

## 2. Frontend + Electron (Node)

No npm workspaces yet — install in all three places:
```bash
npm install                # repo root (concurrently, wait-on)
npm install --prefix frontend
npm install --prefix desktop
```

Run the full app (Vite + Electron together, auto-spawns the Python backend):
```bash
npm run dev                # from repo root
```

Run frontend tests / type-check:
```bash
npm --prefix frontend run test
npm --prefix frontend run check
```

Run desktop (Electron main-process) tests:
```bash
npm --prefix desktop run test
```

## Notes

- Electron picks a free port for the backend automatically — no manual
  `uvicorn` start needed when using `npm run dev`.
- `.adicht` file support and Windows-specific testing are not yet verified
  in this dev environment — deferred to real Windows testing.
- The UI has a full visual redesign (collapsible sidebar, Welcome screen
  with recent files, a real Settings overlay, a two-column Review
  workspace) — verified via `npm --prefix frontend run test`/`check` and a
  production build; a manual `npm run dev` visual pass on Windows is still
  recommended before considering it final.
- Full architecture/API reference: `BACKEND_OVERVIEW.md`, `FRONTEND_OVERVIEW.md`.
