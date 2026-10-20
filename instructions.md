# Setup and running

The app has two halves that live in this one repo:

- a **Python backend** (`backend/` + the analysis library in `src/`), run from
  a virtual environment in `.venv`;
- a **desktop app** (Svelte frontend in `frontend/`, Electron shell in
  `desktop/`), installed with one `npm install` at the repo root (npm
  workspaces).

`npm run dev` starts everything; Electron launches the Python backend itself.

---

## What you need

| Tool | Version | Check with |
|---|---|---|
| Git | any recent | `git --version` |
| Python | **3.12** (tested; newer versions are untested) | Windows: `py -3.12 --version` · macOS: `python3.12 --version` |
| Node.js | **22.12 or newer** (22 or 24 LTS recommended) | `node --version` |

**Windows:** install [Python 3.12](https://www.python.org/downloads/) (keep
"py launcher" ticked), the [Node.js LTS](https://nodejs.org/) installer and
[Git for Windows](https://git-scm.com/download/win).

**macOS:** with [Homebrew](https://brew.sh/):

```bash
brew install python@3.12 node git
```

---

## First-time setup — Windows

Run these in PowerShell (or the VS Code terminal). The installs take a few
minutes, mostly downloading.

```powershell
# 1. Get the code
git clone https://github.com/realchrisward/Physiology-Analysis-Tools.git
cd Physiology-Analysis-Tools
git checkout web-ui-redesign

# 2. Python environment (no need to "activate" it)
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -e . -r backend/requirements.txt

# 3. Node packages: one install covers the frontend and Electron
npm install

# 4. Run
npm run dev
```

---

## First-time setup — macOS

```bash
# 1. Get the code
git clone https://github.com/realchrisward/Physiology-Analysis-Tools.git
cd Physiology-Analysis-Tools
git checkout web-ui-redesign

# 2. Python environment (no need to "activate" it)
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e . -r backend/requirements.txt

# 3. Node packages: one install covers the frontend and Electron
npm install

# 4. Run
npm run dev
```

**macOS limitation:** `.adicht` (LabChart) recordings can only be opened on
Windows — the reader library ships Windows-only binaries. Everything else
works; export recordings as `.txt`, `.edf` or `.mat` to use them on a Mac.

---

## Running it again (every time after setup)

From the repo folder:

```bash
npm run dev
```

That's all — the virtual environment does not need activating; Electron finds
`.venv` by itself.

### Getting the latest code

```bash
git pull
npm install
```

then reinstall the Python packages in case they changed:

- Windows: `.venv\Scripts\python -m pip install -e . -r backend/requirements.txt`
- macOS: `.venv/bin/python -m pip install -e . -r backend/requirements.txt`

and `npm run dev` as usual. Both installs are quick when nothing changed.

---

## Checking the install (optional)

| | Windows | macOS |
|---|---|---|
| Frontend + Electron tests | `npm test` | `npm test` |
| Type check | `npm run check` | `npm run check` |
| Backend tests | `.venv\Scripts\python -m pytest backend/tests -q` | `.venv/bin/python -m pytest backend/tests -q` |

The backend's `.adicht` test skips itself on macOS.

To run the backend API on its own (interactive docs at
http://127.0.0.1:8000/docs):

- Windows: `.venv\Scripts\python -m uvicorn backend.app:app --reload`
- macOS: `.venv/bin/python -m uvicorn backend.app:app --reload`

---

## Troubleshooting

- **"Python virtual environment not found"** when the app starts — `.venv`
  is missing or was created somewhere else. It must be in the repo root:
  re-run step 2 from inside the `Physiology-Analysis-Tools` folder.
- **`py` is not recognised (Windows)** — reinstall Python from python.org with
  the py launcher ticked, then open a new terminal.
- **`npm install` complains about the Node version** — Electron needs Node
  22.12+. Install the current LTS and open a new terminal.
- **First start is slow** — the first `npm run dev` downloads Electron
  (~100 MB) and the backend's first launch can take a while (antivirus scans
  the new packages). Later starts are fast.
- **Port 5173 already in use** — another dev server is running; close it.
- **The Python install is big** — `pip install -e .` also pulls in PySide6 and
  pyqtgraph for the original desktop app; the new app doesn't use them.

---

## Where state lives

- **Review work** (channel choice, beats, confirm/reject decisions, bad-data
  marks) is in SQLite at `~/.physiology_analysis_tools/state.db`, outside the
  repo. Deleting it resets every file's saved review; **Start fresh** in the
  app does the same for one recording. It runs in WAL mode — close the app
  before copying or deleting it.
- **Recent files, FP/FN review projects and report links** (which report goes
  with which recording) are kept in the app's own profile storage. FP/FN lists
  and reports are read when loaded, but the recordings must stay where they
  were found (**Find recordings…** on Home searches again).
- **Sample recordings** for trying the app are in
  `src/physiology_analysis_tools/examples/`.

---

## Known limitations

- `.adicht` files open on Windows only (see above).
- There is no installer yet — the app runs from a checkout with
  `npm run dev`.
