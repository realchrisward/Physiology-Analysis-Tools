# Dev / Testing Setup

Branch: `web-ui-redesign`.

**Prerequisites** (install these first, on any machine):

| | Version | Windows check | macOS / Linux check |
|---|---|---|---|
| Python | 3.12+ | `py --version` | `python3 --version` |
| Node.js | 20+ (tested on 22) | `node --version` | `node --version` |
| Git | any | `git --version` — only needed for the clone route | `git --version` — only needed for the clone route |

Always use the project's own `.venv` — never the bare system `python3`. The
package is also published to PyPI, so a system interpreter may silently
resolve to a stale, separately-installed copy of the analysis library
instead of this working tree.

---

## Setup A — from a fresh `git clone`

Same commands on every OS:

```bash
git clone https://github.com/realchrisward/Physiology-Analysis-Tools.git
cd Physiology-Analysis-Tools
git checkout web-ui-redesign
```

Then run **Common setup** below.

## Setup B — from a zip of an existing working copy

Unzip it, `cd` into the folder, then **delete the two directories that do
not survive the trip** before running Common setup:

Windows (PowerShell):

```powershell
Remove-Item -Recurse -Force .venv, node_modules, frontend\node_modules, desktop\node_modules
```

macOS / Linux:

```bash
rm -rf .venv node_modules frontend/node_modules desktop/node_modules
```

This matters even when moving between two machines of the same OS:

- **`.venv` is not portable.** It hardcodes absolute paths to the machine
  that created it, so it breaks as soon as the folder moves.
- **`node_modules` is not portable either.** Electron ships a
  platform-specific native binary, so a macOS copy will not run on Windows
  (and vice versa).

Everything else in the zip is fine to keep. Note there is no git history in
this route, so you cannot pull updates or see what changed — prefer the
clone route unless the machine has no network access.

---

## Common setup

Run from the repo root. Takes a few minutes, mostly downloading.

### 1. Python backend

Windows (PowerShell / cmd):

```powershell
py -m venv .venv
.venv\Scripts\activate

pip install -e .                            # the analysis library (pyproject.toml)
pip install -r backend/requirements.txt     # FastAPI backend + test deps
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -e .
pip install -r backend/requirements.txt
```

`pip install -e .` pulls in PySide6 and pyqtgraph. Those belong to the old
desktop app and the new one does not use them, but they are still declared
in `pyproject.toml`, so expect a large download.

### 2. Node (frontend + Electron)

There are no npm workspaces yet, so install in all three places (same
commands on every OS):

```bash
npm install                     # repo root (concurrently, wait-on)
npm install --prefix frontend
npm install --prefix desktop
```

### 3. Verify the setup

All four should pass before you start changing anything.

Windows:

```powershell
.venv\Scripts\activate
pytest backend/ -q                     # 110 passed, 1 skipped
npm --prefix frontend run test         # 204 passed
npm --prefix frontend run check        # 0 errors (2 known warnings)
npm --prefix desktop run test          # 5 passed
```

macOS / Linux:

```bash
source .venv/bin/activate
pytest backend/ -q
npm --prefix frontend run test
npm --prefix frontend run check
npm --prefix desktop run test
```

---

## Running the app

Same command on every OS, from the repo root:

```bash
npm run dev
```

Starts Vite and Electron together. Electron spawns the Python backend
itself, on an automatically chosen free port — no separate `uvicorn` needed.
It runs `.venv`'s interpreter directly, so the venv does **not** need to be
activated in the shell you launch from, but it does need to exist at
`<repo root>/.venv`.

To poke at the API on its own:

Windows:

```powershell
.venv\Scripts\activate
uvicorn backend.app:app --reload        # http://127.0.0.1:8000/docs
```

macOS / Linux:

```bash
source .venv/bin/activate
uvicorn backend.app:app --reload
```

---

## Where state lives

Review state (channel choice, beats, confirm/reject decisions, bad-data
marks) is in SQLite at `~/.physiology_analysis_tools/state.db` — **outside**
the repo, so it does not come along in a zip or a clone. A new machine
starts with an empty review history; the app just re-detects on first open.

Deleting that file resets every file's saved review. Per-file, the in-app
**Start fresh** button does the same thing for one recording.

Sample recordings for manual testing are in
`src/physiology_analysis_tools/examples/` (`57 long.txt` and `9 long.txt`
are the most useful — real traces with a decent number of beats).

---

## Known environment gaps

- **`.adicht` support is unverified here.** `adi-reader` has a native
  extension that does not build on macOS, so that import path needs a real
  Windows machine to test. The backend test for it self-skips when the
  package is unavailable.
- **Nothing is packaged yet.** There is no installer or bundled build —
  `npm run dev` from a checkout is the only way to run the app. Bundling the
  Python backend (PyInstaller/Nuitka) has not been started.
- **`backend/requirements.txt` mixes runtime and test dependencies**
  (`pytest`, `openpyxl` are test-only). Worth splitting before anyone builds
  a distributable.

---

## Further reading

- `BACKEND_OVERVIEW.md` — API reference, SQLite schema, design decisions.
- `FRONTEND_OVERVIEW.md` — component structure and frontend conventions.
- `FEATURE_PARITY.md` — what the new app does vs. the original PySide6 one.
- `algo.md` — the beat-of-interest detection algorithm.
