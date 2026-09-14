# Backend API Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a local FastAPI backend that wraps the existing `physiology_analysis_tools` analysis library, with a working file-import endpoint that loads a recording and picks its default signal channel using the confirmed filename→channel-name rules.

**Architecture:** New `backend/` package at the repo root (separate from the pip-installable `src/physiology_analysis_tools`), importing the existing signal-converter modules directly and exposing them over a small FastAPI app. No frontend, no Electron, no persistence yet — this plan produces a backend you can run and hit with `curl`/pytest on its own, which every later milestone (data windowing, beat detection, the Svelte UI) builds on.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, pytest, httpx (for `TestClient`).

**Spec:**
- `docs/superpowers/specs/2026-09-07-web-ui-redesign-design.md` (system architecture)
- `docs/superpowers/specs/2026-09-14-ui-workflow-features-design.md` (channel-selection rules, §1 "Default channel selection")

## Roadmap (not in this plan — future plan docs, one milestone at a time)

This is Milestone 1 of the rewrite. Deliberately scoped to just file import +
channel selection so it's independently testable. Once it's built and
verified, later milestones get their own plan doc each: multi-resolution
data windowing/LOD for the graph, beat detection endpoint (with the
auto-run tickbox/ETA/stop behavior), arrhythmia analysis endpoint,
annotation persistence (SQLite), the Svelte frontend + uPlot graph,
Electron shell + packaging, and finally old-UI (`main.py`/PySide6) removal.

## Global Constraints

- Python 3.12+ required (repo's `pyproject.toml` sets `requires-python = ">= 3.12"`); use the pyenv 3.12 interpreter at `/Users/mrduck/.pyenv/shims/python3.12` to create the venv — the system default `python3` resolves to a Python 3.9 conda env and will not work.
- The backend only ever binds to `127.0.0.1` — fully offline, no network exposure (enforced when uvicorn is wired up in a later milestone; keep in mind when adding any server config).
- The core analysis library (`physiology_analysis_tools`, under `src/`) stays GUI-free and pip-installable — the backend imports it as a normal dependency and never modifies its PySide6-adjacent code.
- Channel matching for default-selection is **exact channel name, case-insensitive** — not a substring guess (confirmed against real sample files). `ts`/`time` (whichever the file has) and `comment` are always excluded from the *selectable* channel list; `comment` is hidden everywhere, but the detected time column is still surfaced separately in the API response as informational metadata (`time_column`), since extractors disagree on its name — `labchart_text_extract` returns `ts`, `adi_extract` returns `time`.
- Work happens on the `web-ui-redesign` branch. Do not merge to `main`. Commit at the end of every task below with a 1-2 sentence commit message (explicit project convention for this work).

---

### Task 1: Backend project scaffold + health endpoint

**Files:**
- Create: `backend/__init__.py`
- Create: `backend/app.py`
- Create: `backend/requirements.txt`
- Create: `backend/tests/__init__.py`
- Test: `backend/tests/test_app.py`

**Interfaces:**
- Produces: `backend.app.create_app() -> fastapi.FastAPI` — a factory function (not a module-level singleton used directly in tests) so each test gets a fresh app instance. The returned app exposes `GET /health` returning `{"status": "ok"}`. Also exposes a module-level `app = create_app()` for uvicorn to import in a later milestone.

- [ ] **Step 1: Create the virtual environment and install the core package**

Run from the repo root:
```bash
cd /Users/mrduck/CodeZone/Physiology-Analysis-Tools
/Users/mrduck/.pyenv/shims/python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```
Expected: no errors; `python -c "import physiology_analysis_tools"` succeeds afterward.

- [ ] **Step 2: Write `backend/requirements.txt` and install it**

```
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
httpx>=0.27.0
pytest>=8.3.0
```

```bash
pip install -r backend/requirements.txt
```

- [ ] **Step 3: Create empty package files**

`backend/__init__.py` — empty file.
`backend/tests/__init__.py` — empty file.

- [ ] **Step 4: Write the failing test**

`backend/tests/test_app.py`:
```python
from fastapi.testclient import TestClient

from backend.app import create_app


def test_health_returns_ok():
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 5: Run the test to verify it fails**

Run: `cd /Users/mrduck/CodeZone/Physiology-Analysis-Tools && source .venv/bin/activate && pytest backend/tests/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.app'` (the file doesn't exist yet).

- [ ] **Step 6: Implement `backend/app.py`**

```python
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Physiology Analysis Tools Backend")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `pytest backend/tests/test_app.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/__init__.py backend/app.py backend/requirements.txt backend/tests/__init__.py backend/tests/test_app.py
git commit -m "Scaffold FastAPI backend with a health check endpoint

Establishes the backend/ package and dev environment (Python 3.12
venv, FastAPI/uvicorn/pytest deps) that later API work builds on."
```

---

### Task 2: Channel-selection pure function

**Files:**
- Create: `backend/channel_selection.py`
- Test: `backend/tests/test_channel_selection.py`

**Interfaces:**
- Produces:
  - `backend.channel_selection.ChannelSelection` — frozen dataclass with fields `channel: str | None` and `matched_rule: bool`.
  - `backend.channel_selection.select_default_channel(filename: str, available_channels: list[str]) -> ChannelSelection`.
  - `backend.channel_selection.CHANNEL_RULES: list[ChannelRule]` — the editable filename-pattern → target-channel-name table.
  - `backend.channel_selection.NON_SIGNAL_CHANNELS: set[str]` — `{"ts", "time", "comment"}`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_channel_selection.py`:
```python
from backend.channel_selection import select_default_channel


def test_numeric_filename_defaults_to_channel_1():
    result = select_default_channel(
        "556420.adicht", ["channel 1", "time", "comment"]
    )

    assert result.channel == "channel 1"
    assert result.matched_rule is True


def test_m_prefixed_filename_defaults_to_channel_2():
    result = select_default_channel(
        "M00561499.adicht",
        ["channel 1", "channel 2", "channel 3", "time", "comment"],
    )

    assert result.channel == "channel 2"
    assert result.matched_rule is True


def test_d_prefixed_filename_defaults_to_ekg():
    result = select_default_channel(
        "D48136.adicht", ["ekg", "hr", "rr", "time", "comment"]
    )

    assert result.channel == "ekg"
    assert result.matched_rule is True


def test_channel_matching_is_case_insensitive():
    result = select_default_channel(
        "d48136.adicht", ["EKG", "HR", "RR", "TIME", "COMMENT"]
    )

    assert result.channel == "EKG"
    assert result.matched_rule is True


def test_unrecognized_filename_falls_back_to_first_signal_channel():
    result = select_default_channel(
        "weird_name.txt", ["comment", "ts", "raw_signal"]
    )

    assert result.channel == "raw_signal"
    assert result.matched_rule is False


def test_no_signal_channels_returns_none():
    result = select_default_channel("123.adicht", ["time", "comment"])

    assert result.channel is None
    assert result.matched_rule is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest backend/tests/test_channel_selection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.channel_selection'`

- [ ] **Step 3: Implement `backend/channel_selection.py`**

```python
import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelRule:
    pattern: re.Pattern
    target_channel: str


# Confirmed against real sample files in
# src/physiology_analysis_tools/examples/ECG_traces. Edit this table if a
# new filename convention is confirmed later - nothing else needs to change.
CHANNEL_RULES: list[ChannelRule] = [
    ChannelRule(re.compile(r"^\d+$"), "channel 1"),
    ChannelRule(re.compile(r"^M\d+$", re.IGNORECASE), "channel 2"),
    ChannelRule(re.compile(r"^D\d+$", re.IGNORECASE), "ekg"),
]

# Different extractors name the time column differently (labchart_text_extract
# uses "ts", adi_extract uses "time") - exclude both, plus "comment".
NON_SIGNAL_CHANNELS: set[str] = {"ts", "time", "comment"}


@dataclass(frozen=True)
class ChannelSelection:
    channel: str | None
    matched_rule: bool


def select_default_channel(
    filename: str, available_channels: list[str]
) -> ChannelSelection:
    stem = os.path.splitext(os.path.basename(filename))[0]
    by_lower = {c.lower(): c for c in available_channels}

    for rule in CHANNEL_RULES:
        if rule.pattern.match(stem):
            matched = by_lower.get(rule.target_channel.lower())
            if matched is not None:
                return ChannelSelection(channel=matched, matched_rule=True)

    for c in available_channels:
        if c.lower() not in NON_SIGNAL_CHANNELS:
            return ChannelSelection(channel=c, matched_rule=False)

    return ChannelSelection(channel=None, matched_rule=False)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest backend/tests/test_channel_selection.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/channel_selection.py backend/tests/test_channel_selection.py
git commit -m "Add default-channel-selection logic using confirmed exact channel names

Numeric filenames -> channel 1, M-prefixed -> channel 2, D-prefixed
-> ekg, verified against the real sample files; falls back to the
first non-time/comment channel when no filename rule matches."
```

---

### Task 3: Extractor wrapper around the existing signal converters

**Files:**
- Create: `backend/extractors.py`
- Create: `backend/tests/conftest.py`
- Test: `backend/tests/test_extractors.py`

**Interfaces:**
- Consumes: `physiology_analysis_tools.modules.signal_converters.<module>.SASSI_extract(filepath: str) -> pandas.DataFrame` (existing, unchanged).
- Produces:
  - `backend.extractors.extract_dataframe(filepath: str) -> pandas.DataFrame`
  - `backend.extractors.NoExtractorSucceededError(filepath: str, errors: dict[str, str])` — exception, raised when no extractor for the file's extension succeeds (including when the extension is unrecognized, in which case `errors` is empty).
  - `backend.extractors.EXTRACTOR_SPECS: list[ExtractorSpec]`
- Also adds a shared pytest fixture: `example_txt_file` (in `backend/tests/conftest.py`), reused by this task's tests and Task 4's.

- [ ] **Step 1: Write the shared test fixture**

`backend/tests/conftest.py`:
```python
import os

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
def example_txt_file() -> str:
    """A real LabChart-text-exported ECG file, safe to parse on any OS."""
    return os.path.join(
        REPO_ROOT, "src", "physiology_analysis_tools", "examples", "10.txt"
    )
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_extractors.py`:
```python
import pytest

from backend.extractors import NoExtractorSucceededError, extract_dataframe


def test_extract_dataframe_loads_labchart_text_file(example_txt_file):
    df = extract_dataframe(example_txt_file)

    assert "channel 1" in df.columns
    assert "ts" in df.columns
    assert len(df) > 0


def test_extract_dataframe_raises_for_unrecognized_extension():
    with pytest.raises(NoExtractorSucceededError):
        extract_dataframe("/some/path/file.unknownext")


def test_extract_dataframe_raises_for_missing_file(example_txt_file):
    missing = example_txt_file.replace("10.txt", "does_not_exist.txt")

    with pytest.raises(NoExtractorSucceededError) as exc_info:
        extract_dataframe(missing)

    assert exc_info.value.errors  # at least one candidate extractor's error is recorded
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest backend/tests/test_extractors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.extractors'`

- [ ] **Step 4: Implement `backend/extractors.py`**

```python
import importlib
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractorSpec:
    name: str
    module_path: str
    ext: str


# Order matters: for extensions shared by two extractors (.txt is used by
# both labchart_text and pcc), candidates are tried in this order and the
# first one whose SASSI_extract() succeeds wins - mirrors the old
# main.py's extractor dict ordering.
EXTRACTOR_SPECS: list[ExtractorSpec] = [
    ExtractorSpec(
        "adi", "physiology_analysis_tools.modules.signal_converters.adi_extract", ".adicht"
    ),
    ExtractorSpec(
        "labchart_text",
        "physiology_analysis_tools.modules.signal_converters.labchart_text_extract",
        ".txt",
    ),
    ExtractorSpec(
        "dsi_fp_matlab",
        "physiology_analysis_tools.modules.signal_converters.dsi_fp_matlab_extract",
        ".mat",
    ),
    ExtractorSpec(
        "pklgzip",
        "physiology_analysis_tools.modules.signal_converters.pklgzip_extract",
        ".gzip",
    ),
    ExtractorSpec(
        "pcc", "physiology_analysis_tools.modules.signal_converters.pcc_extract", ".txt"
    ),
]

try:
    importlib.import_module(
        "physiology_analysis_tools.modules.signal_converters.edf_extract"
    )
    EXTRACTOR_SPECS.append(
        ExtractorSpec(
            "edf",
            "physiology_analysis_tools.modules.signal_converters.edf_extract",
            ".edf",
        )
    )
except ImportError:
    pass  # pyedflib not installed - EDF support stays disabled, same as main.py


class NoExtractorSucceededError(Exception):
    def __init__(self, filepath: str, errors: dict[str, str]):
        self.filepath = filepath
        self.errors = errors
        super().__init__(f"No extractor could load {filepath}: {errors}")


def extract_dataframe(filepath: str):
    ext = os.path.splitext(filepath)[1].lower()
    candidates = [spec for spec in EXTRACTOR_SPECS if spec.ext == ext]

    errors: dict[str, str] = {}
    for spec in candidates:
        module = importlib.import_module(spec.module_path)
        try:
            return module.SASSI_extract(filepath)
        except Exception as e:  # deliberately broad - mirrors main.py's per-extractor fallback
            errors[spec.name] = str(e)

    raise NoExtractorSucceededError(filepath, errors)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest backend/tests/test_extractors.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/extractors.py backend/tests/conftest.py backend/tests/test_extractors.py
git commit -m "Add extractor wrapper reusing the existing signal_converters modules

Tries each file-extension-matching extractor in order until one
succeeds, mirroring the old app's fallback behavior, decoupled from
any Qt/GUI code."
```

---

### Task 4: Files API — import and list endpoints

**Files:**
- Create: `backend/models.py`
- Create: `backend/files.py`
- Modify: `backend/app.py` (register file-registry state and the files router)
- Test: `backend/tests/test_files_api.py`

**Interfaces:**
- Consumes: `select_default_channel` (Task 2), `extract_dataframe` / `NoExtractorSucceededError` (Task 3), `create_app` (Task 1), `example_txt_file` fixture (Task 3).
- Produces:
  - `backend.models.FileImportResult` (Pydantic model): `path: str`, `filename: str`, `status: str` (`"ok"` or `"error"`), `channels: list[str]` (selectable signal channels — never includes `ts`/`time`/`comment`), `time_column: str | None` (the detected time column's actual name, `"ts"` or `"time"`, shown as informational metadata — not selectable), `default_channel: str | None`, `default_channel_matched_rule: bool`, `error: str | None`.
  - `backend.models.ImportRequest`: `paths: list[str]`.
  - `backend.models.ImportResponse`: `results: list[FileImportResult]`.
  - `POST /files/import` — body `{"paths": [...]}`, returns `ImportResponse`.
  - `GET /files` — returns `list[FileImportResult]` of everything imported so far this session (in-memory; replaced by the SQLite-backed store in the annotation-persistence milestone).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_files_api.py`:
```python
from fastapi.testclient import TestClient

from backend.app import create_app


def test_import_labchart_text_file_selects_channel_1(example_txt_file):
    client = TestClient(create_app())

    response = client.post("/files/import", json={"paths": [example_txt_file]})

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "ok"
    assert result["channels"] == ["channel 1"]
    assert result["time_column"] == "ts"
    assert result["default_channel"] == "channel 1"
    assert result["default_channel_matched_rule"] is True


def test_import_then_list_returns_the_file(example_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [example_txt_file]})

    response = client.get("/files")

    assert response.status_code == 200
    paths = [f["path"] for f in response.json()]
    assert example_txt_file in paths


def test_import_missing_file_reports_error_not_crash():
    client = TestClient(create_app())

    response = client.post(
        "/files/import", json={"paths": ["/nonexistent/path/should_fail.txt"]}
    )

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "error"
    assert result["error"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest backend/tests/test_files_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.models'` (or a 404 on `/files/import` once models exist but the route doesn't)

- [ ] **Step 3: Implement `backend/models.py`**

```python
from pydantic import BaseModel


class ImportRequest(BaseModel):
    paths: list[str]


class FileImportResult(BaseModel):
    path: str
    filename: str
    status: str
    channels: list[str] = []
    time_column: str | None = None
    default_channel: str | None = None
    default_channel_matched_rule: bool = False
    error: str | None = None


class ImportResponse(BaseModel):
    results: list[FileImportResult]
```

- [ ] **Step 4: Implement `backend/files.py`**

```python
import os

from fastapi import APIRouter, Request

from backend.channel_selection import select_default_channel
from backend.extractors import extract_dataframe
from backend.models import FileImportResult, ImportRequest, ImportResponse

router = APIRouter(prefix="/files", tags=["files"])

TIME_COLUMN_NAMES = {"ts", "time"}
NEVER_A_CHANNEL = TIME_COLUMN_NAMES | {"comment"}


@router.post("/import", response_model=ImportResponse)
def import_files(payload: ImportRequest, request: Request) -> ImportResponse:
    registry: dict[str, FileImportResult] = request.app.state.imported_files
    results: list[FileImportResult] = []

    for path in payload.paths:
        filename = os.path.basename(path)
        try:
            df = extract_dataframe(path)
            channel_names = list(df.columns)
            time_column = next(
                (c for c in channel_names if c.lower() in TIME_COLUMN_NAMES), None
            )
            selection = select_default_channel(filename, channel_names)
            result = FileImportResult(
                path=path,
                filename=filename,
                status="ok",
                channels=[
                    c for c in channel_names if c.lower() not in NEVER_A_CHANNEL
                ],
                time_column=time_column,
                default_channel=selection.channel,
                default_channel_matched_rule=selection.matched_rule,
            )
        except Exception as e:
            result = FileImportResult(
                path=path, filename=filename, status="error", error=str(e)
            )

        registry[path] = result
        results.append(result)

    return ImportResponse(results=results)


@router.get("", response_model=list[FileImportResult])
def list_files(request: Request) -> list[FileImportResult]:
    return list(request.app.state.imported_files.values())
```

- [ ] **Step 5: Modify `backend/app.py` to wire in file-registry state and the router**

Replace the whole file with:
```python
from fastapi import FastAPI

from backend.files import router as files_router
from backend.models import FileImportResult


def create_app() -> FastAPI:
    app = FastAPI(title="Physiology Analysis Tools Backend")
    app.state.imported_files: dict[str, FileImportResult] = {}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(files_router)

    return app


app = create_app()
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest backend/ -v`
Expected: PASS (all tests across all four tasks, 13 passed)

- [ ] **Step 7: Commit**

```bash
git add backend/models.py backend/files.py backend/app.py backend/tests/test_files_api.py
git commit -m "Add POST /files/import and GET /files endpoints

Wires the extractor wrapper and channel-selection logic into a real
API: importing a file returns its channels and confirmed default
channel; imported files are listed for the session."
```
