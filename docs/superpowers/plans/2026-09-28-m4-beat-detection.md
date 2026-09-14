# M4: Beat Detection Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `POST /beats/detect` endpoint that runs real beat detection against an imported file's cached signal data and returns the results — the first of four backend-completion milestones.

**Architecture:** Stop discarding the DataFrame that `/files/import` already extracts — cache it in `app.state.signal_cache`, keyed by path. A new `backend/beats.py` router reads from that cache, calls the existing unmodified `heartbeat_detection.beatcaller()`, and caches its own result in `app.state.beat_cache` for later milestones (arrhythmia detection, windowing) to build on.

**Tech Stack:** Python 3.12, FastAPI, pandas — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-28-backend-completion-design.md` (§M4)

## Global Constraints

- Every failure path (file not in cache, unknown channel, any `beatcaller()` exception) returns a clean per-call error, never a 500 — matches the established pattern from `/files/import`.
- No WebSocket/background-task infrastructure — beat detection is a fast, synchronous, atomic call; that's a deliberate scope decision from the spec, not an oversight.
- Tests run against real example files in `src/physiology_analysis_tools/examples/`, calling the real unmodified `heartbeat_detection.beatcaller()` — never mocked.
- Use `.venv`'s Python for every command in this plan (`source .venv/bin/activate` first) — the bare `python3` on this machine resolves to a stale, differently-shaped PyPI install and must never be used for anything in this repo.

---

### Task 1: Cache the signal DataFrame on import

**Files:**
- Modify: `backend/app.py`
- Modify: `backend/files.py`
- Test: `backend/tests/test_files_api.py`

**Interfaces:**
- Produces: `app.state.signal_cache: dict[str, dict]`, entries shaped `{"df": pandas.DataFrame, "time_column": str}`, populated for every successfully-imported file. Also produces `app.state.beat_settings: heartbeat_detection.Settings` (a plain instance, defaults untouched — no endpoint reads or writes it yet; that's Task 2 and M5's job respectively, but initializing it here means neither needs to touch `create_app()` again).

- [ ] **Step 1: Write the failing test**

In `backend/tests/test_files_api.py`, add a test that imports `example_txt_file` (the existing fixture, `10.txt`) through a `TestClient` built from an explicit `app = create_app()` (not the inline `TestClient(create_app())` pattern the existing tests use — this test needs to inspect `app.state` afterward, so it needs to keep a reference to `app`). After the import call succeeds, assert `example_txt_file in app.state.signal_cache`, that the cached entry's `"time_column"` equals `"ts"` (the known real time column for this file, established in Task 3 of the original files-API plan), and that the cached `"df"`'s `.shape[0]` is `2280` (the file's known real row count) and `"channel 1"` is among its columns.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest backend/tests/test_files_api.py -v -k signal_cache`
Expected: FAIL — `AttributeError` or `KeyError`, since `app.state.signal_cache` doesn't exist yet.

- [ ] **Step 3: Implement**

In `backend/app.py`'s `create_app()`, alongside the existing `app.state.imported_files: dict[str, FileImportResult] = {}` line, add:
```python
app.state.signal_cache: dict[str, dict] = {}
app.state.beat_settings = heartbeat_detection.Settings()
```
Import `heartbeat_detection` at the top of the file the same way `backend/beats.py` will in Task 2 (see that task's import line — use the identical import path so both files resolve the same module).

In `backend/files.py`'s `import_files()`, immediately after the existing successful `extract_dataframe(path)` call (inside the same `try` block, before building `FileImportResult`), add the file's DataFrame and its resolved `time_column` into `request.app.state.signal_cache[path]` as `{"df": df, "time_column": time_column}`. `time_column` is already computed a few lines later in the current code (`next((c for c in channel_names if c.lower() in TIME_COLUMN_NAMES), None)`) — reorder so that computation happens before this caching line rather than duplicating it.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest backend/tests/test_files_api.py -v`
Expected: all existing tests still pass (the caching addition doesn't change any response shape), plus the new test passes.

- [ ] **Step 5: Commit**

```bash
git add backend/app.py backend/files.py backend/tests/test_files_api.py
git commit -m "Cache imported signal DataFrames instead of discarding them"
```

---

### Task 2: `POST /beats/detect`

**Files:**
- Create: `backend/beats.py`
- Modify: `backend/models.py`
- Modify: `backend/app.py`
- Create: `backend/tests/test_beats_api.py`
- Modify: `backend/tests/conftest.py`

**Interfaces:**
- Consumes: `app.state.signal_cache[path]` and `app.state.beat_settings` (Task 1), `app.state.imported_files[path].size` (existing, from the files milestone).
- Produces: `POST /beats/detect` — request body `{path: str, channel: str}`; response `{status: "ok"|"error", beats: list[{ts, rr, r_amplitude, hr}], count: int, mean_hr: float | null, duration: float | null, elapsed_seconds: float, file_size_bytes: int | null, error: str | null}`. Also populates `app.state.beat_cache: dict[str, pandas.DataFrame]` (raw `beatcaller()` output, indexed by path) — the next two milestones (arrhythmia detection, windowing) read from this.

- [ ] **Step 1: Add a second real-data fixture to `conftest.py`**

Add a `real_beats_txt_file` fixture alongside the existing `example_txt_file` one, pointing at `src/physiology_analysis_tools/examples/57.txt` (same `REPO_ROOT`-relative pattern already used for `example_txt_file`). This file reliably produces real beats with default settings, unlike `10.txt` (which is real but too short to produce any — useful for a different test below).

- [ ] **Step 2: Write the failing tests**

In `backend/tests/test_beats_api.py`:

1. **Real beats detected.** Import `real_beats_txt_file` (57.txt), then `POST /beats/detect` with `{"path": real_beats_txt_file, "channel": "channel 1"}`. Assert `status == "ok"`, `count == 15`, `len(beats) == 15`, `mean_hr` is approximately `650.96` (tolerance ±0.01), `duration` is approximately `1.435` (tolerance ±0.001), `elapsed_seconds > 0`, `file_size_bytes == 45877` (the file's real, known byte size). Assert the first beat in the response matches the real known values: `ts ≈ 0.0855`, `rr ≈ 0.069`, `r_amplitude ≈ 0.508387`, `hr ≈ 869.565217` (small float tolerances throughout).

2. **Successful run, zero beats (not an error).** Import `example_txt_file` (10.txt, already known from Task 1 to produce zero beats with default settings), `POST /beats/detect` with channel `"channel 1"`. Assert `status == "ok"` (this is not a failure — the detector ran correctly and found nothing), `count == 0`, `beats == []`, `mean_hr is None`, `duration is None`.

3. **File never imported.** `POST /beats/detect` with a path that was never passed to `/files/import`. Assert `status == "error"`, response HTTP status is `200` (matches the project's established per-call-error convention, not an HTTP error code), `error` is a non-empty string.

4. **Unknown channel.** Import `real_beats_txt_file`, then `POST /beats/detect` with a channel name that isn't one of the file's real columns (e.g. `"not_a_real_channel"`). Assert `status == "error"` and a non-empty `error` message (this will come from `beatcaller()` raising a `KeyError`-style exception on the unknown column — let that exception's message flow through as the error string, don't swallow or replace it).

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest backend/tests/test_beats_api.py -v`
Expected: FAIL — `404` (no such route) or import error, since `backend/beats.py` doesn't exist yet.

- [ ] **Step 4: Implement the models**

In `backend/models.py`, add:
- `BeatDetectRequest(BaseModel)`: `path: str`, `channel: str`.
- `Beat(BaseModel)`: `ts: float`, `rr: float`, `r_amplitude: float`, `hr: float`.
- `BeatDetectionResult(BaseModel)`: `status: str`, `beats: list[Beat] = []`, `count: int = 0`, `mean_hr: float | None = None`, `duration: float | None = None`, `elapsed_seconds: float = 0.0`, `file_size_bytes: int | None = None`, `error: str | None = None`.

- [ ] **Step 5: Implement `backend/beats.py`**

An `APIRouter(prefix="/beats", tags=["beats"])` with one route, `POST /detect`, taking `BeatDetectRequest` and the FastAPI `Request` (to reach `request.app.state`, matching the pattern already used in `backend/files.py`). Logic:
1. Look up `request.app.state.signal_cache.get(payload.path)` — if missing, return a `BeatDetectionResult(status="error", error=f"File not imported: {payload.path}")` immediately (no exception raised, no 500).
2. Otherwise, `import time; start = time.monotonic()`, call `heartbeat_detection.beatcaller(cache_entry["df"], time_column=cache_entry["time_column"], voltage_column=payload.channel, **request.app.state.beat_settings.__dict__)` inside a `try`. On any exception, return `BeatDetectionResult(status="error", error=str(e), elapsed_seconds=time.monotonic() - start)`.
3. On success: `elapsed_seconds = time.monotonic() - start`, store the raw returned DataFrame into `request.app.state.beat_cache[payload.path]`, build the `beats` list (one `Beat` per row: `ts`, `rr` from the DataFrame's `RR` column, `r_amplitude` from `R_amplitude`, `hr` from `HR`), `count = len(beats)`, `mean_hr = float(df["HR"].mean())` if `count > 0` else `None`, `duration = float(df["ts"].iloc[-1] - df["ts"].iloc[0])` if `count > 0` else `None`, `file_size_bytes = request.app.state.imported_files.get(payload.path)` then `.size` if that entry exists else `None`. Return `BeatDetectionResult(status="ok", beats=beats, count=count, mean_hr=mean_hr, duration=duration, elapsed_seconds=elapsed_seconds, file_size_bytes=file_size_bytes)`.

Import with `from physiology_analysis_tools.modules import heartbeat_detection` — a plain top-level import, verified working directly against this file in this session. No dual absolute/relative import handling is needed here (that historical pattern in the old `main.py` existed only because that file could be run either as a standalone script or as part of the installed package; `backend/` code always runs as part of the installed package, so a plain import is correct and sufficient).

- [ ] **Step 6: Wire it into `backend/app.py`**

Add `app.state.beat_cache: dict[str, "pandas.DataFrame"] = {}` in `create_app()` (a plain `{}` is fine at runtime — the type-comment style already used for `imported_files`/`signal_cache` is just for readability, not enforcement). Import the new router (`from backend.beats import router as beats_router`) and `app.include_router(beats_router)`, matching how `files_router` is already included.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest backend/ -v`
Expected: all prior tests still pass unchanged, plus the 4 new tests in `test_beats_api.py` pass. Total count grows from 17 passed/1 skipped to 21 passed/1 skipped.

- [ ] **Step 8: Commit**

```bash
git add backend/beats.py backend/models.py backend/app.py backend/tests/test_beats_api.py backend/tests/conftest.py
git commit -m "Add POST /beats/detect endpoint"
```
