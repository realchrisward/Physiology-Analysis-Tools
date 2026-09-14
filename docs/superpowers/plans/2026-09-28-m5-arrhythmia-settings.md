# M5: Arrhythmia Detection + Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `GET`/`PUT /settings` for beat + arrhythmia detection parameters, and `POST /arrhythmia/detect` running heuristic and/or unsupervised arrhythmia detection against a file's cached beats.

**Architecture:** Settings live as mutable objects in `app.state` (already partially true — `beat_settings` was initialized in M4; this milestone adds `arrhythmia_settings` alongside it and the endpoints to read/write both). The arrhythmia endpoint reads `beat_cache[path]` (from M4) and `signal_cache[path]` (from M4), calls the existing unmodified `arrhythmia_detection.call_arrhythmias()`, merges the result back into `beat_cache[path]`.

**Tech Stack:** Python 3.12, FastAPI — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-28-backend-completion-design.md` (§M5)

## Global Constraints

- Sync `def` endpoints (not `async def`) for the arrhythmia route — matches the established pattern (`BACKEND_OVERVIEW.md`'s documented reason: FastAPI threadpools sync handlers automatically, avoiding the old app's QThread-freeze problem). No WebSocket/background-task infrastructure.
- Every failure path returns a clean per-call error (HTTP 200, `status: "error"`), never a 500.
- `PUT /settings` rejects (doesn't apply) if `bradycardia_absolute_hr >= tachycardia_absolute_hr` — the workflow spec's mutual-exclusivity requirement.
- Tests run against real example files, real calls into the unmodified `arrhythmia_detection`/`heartbeat_detection` modules — never mocked.
- Use `.venv`'s Python for every command — never the bare `python3` on this machine (stale PyPI install).

---

### Task 1: `GET`/`PUT /settings`

**Files:**
- Create: `backend/settings.py`
- Modify: `backend/models.py`
- Modify: `backend/app.py`
- Create: `backend/tests/test_settings_api.py`

**Interfaces:**
- Produces: `app.state.arrhythmia_settings: arrhythmia_detection.Settings` (new — `app.state.beat_settings` already exists from M4). `GET /settings` returns both as one JSON object; `PUT /settings` takes the same shape and either replaces both in `app.state` and returns the new values, or returns a clean validation error without applying anything.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_settings_api.py`:
1. **GET returns real current defaults.** With a fresh app, `GET /settings` should return `beat.min_RR == 60`, `beat.perc_thresh == 97`, `arrhythmia.bradycardia_absolute_hr == 300`, `arrhythmia.tachycardia_absolute_hr == 850`, `arrhythmia.window_size == 100` — the real default values from `heartbeat_detection.Settings()` and `arrhythmia_detection.Settings()` (verified this session: beat settings are `{min_RR: 60, ecg_invert: False, auto_detect_invert: False, ecg_filter: True, ecg_filt_order: 2, ecg_filt_cutoff: 5, abs_thresh: None, perc_thresh: 97}`; arrhythmia settings are `{bradycardia_absolute_hr: 300, tachycardia_absolute_hr: 850, skipped_beat_multiple_rr: 1.5, premature_beat_multiple_rr: 0.7, window_size: 100, eps: 0.03, min_samples: 30}`).
2. **PUT applies a valid change.** `PUT /settings` with the current values except `arrhythmia.bradycardia_absolute_hr` changed to `250`. Assert the response reflects `250`, then `GET /settings` again and assert it now also returns `250` (proves the change actually persisted in `app.state`, not just echoed back).
3. **PUT rejects an invalid brady/tachy pair.** `PUT /settings` with `bradycardia_absolute_hr = 900` and `tachycardia_absolute_hr = 850` (brady >= tachy). Assert the response is `status: "error"` with a non-empty error message (HTTP 200, not a validation-error 422 — this project's established per-call-error convention), and that a subsequent `GET /settings` shows the *original* unmodified values — the rejected PUT must not have partially applied.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest backend/tests/test_settings_api.py -v`
Expected: FAIL — route doesn't exist yet (404s / import errors).

- [ ] **Step 3: Implement the models**

In `backend/models.py`, add:
- `BeatSettingsModel(BaseModel)`: one field per `heartbeat_detection.Settings()` attribute, exact same names and types (`min_RR: int`, `ecg_invert: bool`, `auto_detect_invert: bool`, `ecg_filter: bool`, `ecg_filt_order: int`, `ecg_filt_cutoff: int`, `abs_thresh: float | None`, `perc_thresh: int | None`).
- `ArrhythmiaSettingsModel(BaseModel)`: one field per `arrhythmia_detection.Settings()` attribute (`bradycardia_absolute_hr: int`, `tachycardia_absolute_hr: int`, `skipped_beat_multiple_rr: float`, `premature_beat_multiple_rr: float`, `window_size: int`, `eps: float`, `min_samples: int`).
- `SettingsPayload(BaseModel)`: `beat: BeatSettingsModel`, `arrhythmia: ArrhythmiaSettingsModel` — this is both the `GET` response shape and the `PUT` request/response shape.
- `SettingsResult(BaseModel)`: `status: str`, `settings: SettingsPayload | None = None`, `error: str | None = None` — wraps `SettingsPayload` for `PUT`'s response so a validation failure has somewhere to put the error message; `GET` can return a bare `SettingsPayload` directly since it can't fail.

- [ ] **Step 4: Implement `backend/settings.py`**

An `APIRouter()` (no prefix — these are top-level `/settings` routes) with:
- `GET /settings` — build a `SettingsPayload` directly from `request.app.state.beat_settings.__dict__` and `request.app.state.arrhythmia_settings.__dict__` (each dict unpacks directly into the corresponding Pydantic model's constructor).
- `PUT /settings` — takes a `SettingsPayload` body. Validate `payload.arrhythmia.bradycardia_absolute_hr < payload.arrhythmia.tachycardia_absolute_hr`; if not, return `SettingsResult(status="error", error="bradycardia_absolute_hr must be less than tachycardia_absolute_hr")` without touching `app.state`. Otherwise, replace `request.app.state.beat_settings` and `request.app.state.arrhythmia_settings` with fresh `heartbeat_detection.Settings()`/`arrhythmia_detection.Settings()` instances whose `__dict__` is updated from the payload (or construct new instances and set each attribute — either way, end state is that `app.state`'s two settings objects reflect exactly the validated payload), and return `SettingsResult(status="ok", settings=payload)`.

Import both modules with plain top-level imports: `from physiology_analysis_tools.modules import heartbeat_detection, arrhythmia_detection` (matches the proven-working pattern from M4 — no dual-import handling needed anywhere in `backend/`).

- [ ] **Step 5: Wire into `backend/app.py`**

Add `app.state.arrhythmia_settings = arrhythmia_detection.Settings()` alongside the existing `app.state.beat_settings` line (both now initialized together — add the `arrhythmia_detection` import to the existing `heartbeat_detection` import line). Import and include the new `settings` router the same way `files_router`/`beats_router` are included.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest backend/ -v`
Expected: total grows from 22 passed/1 skipped to 25 passed/1 skipped.

- [ ] **Step 7: Commit**

```bash
git add backend/settings.py backend/models.py backend/app.py backend/tests/test_settings_api.py
git commit -m "Add GET/PUT /settings with bradycardia/tachycardia validation"
```

---

### Task 2: `POST /arrhythmia/detect`

**Files:**
- Create: `backend/arrhythmia.py`
- Modify: `backend/models.py`
- Modify: `backend/app.py`
- Create: `backend/tests/test_arrhythmia_api.py`
- Modify: `backend/tests/conftest.py`

**Interfaces:**
- Consumes: `app.state.beat_cache[path]` and `app.state.signal_cache[path]` (M4), `app.state.arrhythmia_settings` (Task 1).
- Produces: `POST /arrhythmia/detect` — request `{path: str, channel: str, method: "heuristic" | "unsupervised" | "both"}`; response includes per-beat category flags and updates `app.state.beat_cache[path]` in place with the merged result (so a later call — e.g. re-running with a different method — sees the previous run's categories still present unless overwritten by the new call's own categories).

- [ ] **Step 1: Add a fixture for a beat-detection-and-arrhythmia-ready real file**

In `backend/tests/conftest.py`, add a `long_txt_file` fixture pointing at `src/physiology_analysis_tools/examples/9 long.txt` (same `REPO_ROOT`-relative pattern as the other fixtures). This file real-detects 163 beats and is large enough for the unsupervised method's clustering to run meaningfully (its `min_samples=30` default needs a real population of beats — `57.txt`'s 15 beats are too few and would degrade to all-noise clustering, not representative of real behavior).

- [ ] **Step 2: Write the failing tests**

In `backend/tests/test_arrhythmia_api.py`:

1. **Heuristic method, real per-beat results.** Import `real_beats_txt_file` (`57.txt`, from M4's conftest fixture), `POST /beats/detect` with channel `"channel 1"`, then `POST /arrhythmia/detect` with `{"path": ..., "channel": "channel 1", "method": "heuristic"}`. Assert `status == "ok"`, `count == 15`. Assert the first beat in the response (`ts ≈ 0.0855`) has `tachycardia_absolute == True` and `any_arrhythmia == True`; the second beat (`ts ≈ 0.21`) has every category flag `False`; `abn_cluster` is `None` for every beat (not evaluated — heuristic-only was requested). Assert `any_arrhythmia_count == 5` (real, verified count).

2. **Both methods on a larger real file.** Import and beat-detect `long_txt_file` (`9 long.txt`, channel `"channel 1"`), then `POST /arrhythmia/detect` with `method: "both"`. Assert `status == "ok"`, `count == 163`, and that `abn_cluster` is not `None` for any beat (it was evaluated this time) — don't assert its exact per-beat values beyond that, since DBSCAN clustering is sensitive to floating-point/library-version drift in a way the heuristic categories aren't; do assert `any_arrhythmia_count == 157` (the real, verified count for this file+method+default-settings combination).

3. **Beats not detected yet.** `POST /arrhythmia/detect` for a path that was imported but never had `/beats/detect` called. Assert `status == "error"`, HTTP 200, non-empty `error` message (something like "beat detection has not been run for this file" — the message should clearly point at the missing prerequisite, not just be a generic exception string).

4. **Unrecognized method string.** `POST /arrhythmia/detect` with `method: "not_a_real_method"` on an already-beat-detected file. Assert `status == "error"`, non-empty `error`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest backend/tests/test_arrhythmia_api.py -v`
Expected: FAIL — route doesn't exist yet.

- [ ] **Step 4: Implement the models**

In `backend/models.py`, add:
- `ArrhythmiaDetectRequest(BaseModel)`: `path: str`, `channel: str`, `method: str`.
- `ArrhythmiaBeat(BaseModel)`: `ts: float`, `bradycardia_absolute: bool | None = None`, `tachycardia_absolute: bool | None = None`, `skipped_beat: bool | None = None`, `prem_beat: bool | None = None`, `abn_cluster: bool | None = None`, `any_arrhythmia: bool`, `other_arrhythmia: bool`.
- `ArrhythmiaDetectionResult(BaseModel)`: `status: str`, `beats: list[ArrhythmiaBeat] = []`, `count: int = 0`, `any_arrhythmia_count: int = 0`, `elapsed_seconds: float = 0.0`, `error: str | None = None`.

- [ ] **Step 5: Implement `backend/arrhythmia.py`**

An `APIRouter(prefix="/arrhythmia", tags=["arrhythmia"])` with `POST /detect`, taking `ArrhythmiaDetectRequest` and `Request`. Logic:
1. Map `payload.method` (case-sensitive, one of `"heuristic"`, `"unsupervised"`, `"both"`) to the library's exact expected strings: `"heuristic" -> "Heuristic"`, `"unsupervised" -> "Unsupervised"`, `"both" -> "Both"`. Any other value returns `ArrhythmiaDetectionResult(status="error", error=f"Unrecognized method: {payload.method}")` immediately.
2. Look up `beat_df = request.app.state.beat_cache.get(payload.path)` — if `None`, return `ArrhythmiaDetectionResult(status="error", error="Beat detection has not been run for this file yet")`.
3. Look up `cache_entry = request.app.state.signal_cache.get(payload.path)` — if `None` (shouldn't normally happen since `beat_cache` can't be populated without `signal_cache` existing first, but check anyway for a clean error rather than a `KeyError`), return a clean error the same way.
4. `import time; start = time.monotonic()`; call `arrhythmia_detection.call_arrhythmias(beat_df.copy(), request.app.state.arrhythmia_settings, signals=cache_entry["df"], selected_signal=payload.channel, selected_time=cache_entry["time_column"], arr_methods=<mapped method string>)` inside a `try`. On any exception, return `ArrhythmiaDetectionResult(status="error", error=str(e), elapsed_seconds=time.monotonic() - start)`.

   Note: this passes the *raw* cached signal DataFrame as `signals`, not a separately-filtered version — the old app fed a UI-configurable filtered signal into this same call, but that filter was a display-toggle feature that doesn't exist backend-side yet. This is a deliberate, documented simplification for this milestone, not an oversight — revisit if/when a "filtered signal" concept is added for the graph.

5. On success: `elapsed_seconds = time.monotonic() - start`. Store the returned (category-column-merged) DataFrame back into `request.app.state.beat_cache[payload.path]`, replacing the old entry — this is how later calls (e.g. re-running with a different method) see the accumulated state. Build the `beats` list: one `ArrhythmiaBeat` per row, reading `any_arrhythmia` and `other_arrhythmia` directly (always present), and each of `bradycardia_absolute`, `tachycardia_absolute`, `skipped_beat`, `prem_beat`, `abn_cluster` only if that column exists in the result DataFrame (`col in df.columns`) — else leave it `None` on the model (its default). `count = len(beats)`. `any_arrhythmia_count = int(df["any_arrhythmia"].sum())`. Return `ArrhythmiaDetectionResult(status="ok", beats=beats, count=count, any_arrhythmia_count=any_arrhythmia_count, elapsed_seconds=elapsed_seconds)`.

- [ ] **Step 6: Wire into `backend/app.py`**

Import and include the new `arrhythmia` router, matching the existing pattern.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest backend/ -v`
Expected: total grows from 25 passed/1 skipped to 29 passed/1 skipped. The "both methods" test will take a few real seconds (DBSCAN on a real ~163-beat file) — that's expected, not a hang.

- [ ] **Step 8: Commit**

```bash
git add backend/arrhythmia.py backend/models.py backend/app.py backend/tests/test_arrhythmia_api.py backend/tests/conftest.py
git commit -m "Add POST /arrhythmia/detect endpoint"
```
