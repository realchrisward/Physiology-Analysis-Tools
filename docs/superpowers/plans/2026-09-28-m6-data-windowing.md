# M6: Data Windowing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `GET /channels/window` — downsampled, deterministic signal data for the graph, ported from the old app's min-max-per-bin algorithm. `GET /beats/window` — range-filtered beat/arrhythmia markers, so the frontend never holds or filters a full multi-day beat list client-side.

**Architecture:** Both endpoints read straight from the existing `signal_cache`/`beat_cache` (M4/M5) — no new caching of raw data. The channel-window endpoint adds one new cache, `app.state.window_cache`, keyed by the full query tuple, purely to guarantee the same query always returns the byte-identical result (the actual bug being fixed — the old app recomputed bins against a live-changing pixel width on every repaint, which is why zooming changed the graph's shape).

**Tech Stack:** Python 3.12, FastAPI, numpy (already a transitive dependency via `physiology_analysis_tools`) — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-28-backend-completion-design.md` (§M6)

## Global Constraints

- Every failure path returns a clean per-call error (HTTP 200, `status: "error"`), never a 500.
- Tests run against real example files, never mocked, following this project's established TDD/verification discipline.
- Use `.venv`'s Python for every command — never the bare `python3` on this machine (stale PyPI install).
- Both endpoints take `path` as a **query parameter**, not a URL path segment — deliberate deviation from the spec's literal `GET /channels/{path}/window` URL shape. Real file paths (Windows drive letters, spaces, colons) don't round-trip safely as a raw URL path segment, and every other endpoint in this backend (`files.py`, `beats.py`, `arrhythmia.py`) already avoids this by taking `path` as a body/query field rather than a URL segment — this plan keeps that one existing convention rather than introducing a second, inconsistent one. Final routes: `GET /channels/window?path=&channel=&start=&end=&resolution=` and `GET /beats/window?path=&start=&end=`.

---

### Task 1: `GET /channels/window` — downsampled signal data

**Files:**
- Create: `backend/windowing.py`
- Modify: `backend/models.py`
- Modify: `backend/app.py`
- Create: `backend/tests/test_channels_window_api.py`

**Interfaces:**
- Consumes: `app.state.signal_cache[path]` (M4) — `{"df": DataFrame, "time_column": str}`.
- Produces: `app.state.window_cache: dict[tuple, ChannelWindowResult]` (new). `GET /channels/window` — query params `path: str, channel: str, start: float, end: float, resolution: int`; response is the (possibly downsampled) `x`/`y` point lists for that exact range.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_channels_window_api.py`, using the existing `example_txt_file` (`10.txt`, 2280 rows, full time range `[0.0, 1.1394999999999982]`) and a new fixture for `9 long.txt` (`long_txt_file` — already exists in `conftest.py` from M5, reuse it; 223600 rows, full time range `[0.0, 111.7995]`):

1. **Small file, full range, high resolution — passthrough (no downsampling).** Import `10.txt`, then `GET /channels/window?path=<path>&channel=channel 1&start=0&end=1.1394999999999982&resolution=10000`. Assert `status == "ok"`, `downsampled == False`, `point_count == 2280` (real row count — `2280 <= 10000 * 4`, so the algorithm's passthrough branch fires, matching `main.py`'s exact `gather_data()` behavior), `len(x) == len(y) == 2280`.
2. **Large file, full range, low resolution — real downsampling.** Import `9 long.txt`, then `GET /channels/window?path=<path>&channel=channel 1&start=0&end=111.7995&resolution=100`. Assert `status == "ok"`, `downsampled == True`, `point_count == 400` (real, verified value — `223600 > 100*4`, so `min_max_downsample()` runs with `n_bins=200`, producing 2 points per bin since min/max never land on the same sample for real ECG data at this resolution). Assert the first point is `x ≈ 0.2565, y ≈ -10.24` and the last is `x ≈ 111.786, y ≈ -6.848` (real, verified values — floats, compare with `pytest.approx`).
3. **Narrower window, mid-file.** Same file, `GET /channels/window?path=<path>&channel=channel 1&start=10&end=20&resolution=50`. Assert `status == "ok"`, `point_count == 200` (real, verified — `n_bins=100`, every bin yields a distinct min/max point), and every returned `x` value falls within `[10, 20]` inclusive (verified real range: `10.034` to `19.9585`).
4. **Determinism — repeat the exact same query twice.** Re-issue the Task 1.2 query. Assert the second response's `x`/`y` lists are byte-identical (`==`) to the first — this is the actual bug fix: same query, same pixel width, same result, not a re-recomputed shape.
5. **Unknown channel.** `GET /channels/window` with `channel=not_a_real_channel` on an imported file. Assert `status == "error"`, HTTP 200, non-empty `error`.
6. **File never imported.** `GET /channels/window` with a `path` that was never imported. Assert `status == "error"`, non-empty `error`.
7. **Empty range (fully outside the file's data).** `GET /channels/window?path=<10.txt path>&channel=channel 1&start=5&end=6&resolution=100` (file's real range tops out at `1.1394999999999982`). Assert `status == "ok"` (legitimate empty result, not an error — matches this project's established convention for "ran successfully, found nothing"), `point_count == 0`, `x == [] and y == []`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest backend/tests/test_channels_window_api.py -v`
Expected: FAIL — route doesn't exist yet (404s).

- [ ] **Step 3: Implement the models**

In `backend/models.py`, add:
- `ChannelWindowResult(BaseModel)`: `status: str`, `x: list[float] = []`, `y: list[float] = []`, `point_count: int = 0`, `downsampled: bool = False`, `error: str | None = None`.

- [ ] **Step 4: Implement `backend/windowing.py`**

Two pure functions, ported from `src/physiology_analysis_tools/main.py`'s `min_max_downsample()` (lines 100-138) and `gather_data()` (lines 141-158) — same algorithm, same bin-edge math, same min/max-per-bin selection with chronological-order preservation, decoupled from pyqtgraph (no `filt_column`/filtered-signal parameter — that was a UI display-toggle feature this milestone doesn't add backend support for):

```python
def min_max_downsample(x_val, y_val, n_bins):
    # identical to main.py's version: numpy.linspace bin edges, per-bin
    # argmin/argmax with chronological-order swap, append min then max
    # (skip the duplicate if min_idx == max_idx)
    ...

def window_channel_data(df, time_column, signal_column, start, end, resolution):
    # 1. filter df to time_column in [start, end] inclusive
    # 2. if resolution > 0 and len(filtered) > resolution * 4:
    #      downsample via min_max_downsample(x, y, n_bins=resolution * 2);
    #      downsampled = True
    #    else: x, y = list(x), list(y); downsampled = False
    # 3. return x, y, downsampled
    ...
```

Reproduce the bin-edge and argmin/argmax logic exactly as verified this session (confirmed against real `9 long.txt`/`10.txt` data via a direct port of the two `main.py` functions) — do not simplify or "improve" the algorithm; the milestone's whole point is preserving the old algorithm's correctness while fixing only the memoization/determinism gap around it.

- [ ] **Step 5: Implement the endpoint in `backend/windowing.py`**

An `APIRouter(prefix="/channels", tags=["channels"])` with `GET /window`, query params `path: str, channel: str, start: float, end: float, resolution: int`:

1. Cache key: `(path, channel, start, end, resolution)`. If present in `request.app.state.window_cache`, return it directly (guarantees byte-identical repeats).
2. `cache_entry = request.app.state.signal_cache.get(path)` — if `None`, return `ChannelWindowResult(status="error", error=f"File not imported: {path}")` (uncached — errors aren't memoized).
3. If `channel not in cache_entry["df"].columns`, return `ChannelWindowResult(status="error", error=f"Unknown channel: {channel}")`.
4. Call `window_channel_data(cache_entry["df"], cache_entry["time_column"], channel, start, end, resolution)`. Build `ChannelWindowResult(status="ok", x=x, y=y, point_count=len(x), downsampled=downsampled)`, store it in `window_cache[key]`, return it.

- [ ] **Step 6: Wire into `backend/app.py`**

Add `app.state.window_cache: dict = {}` in `create_app()` alongside the existing state dicts. Import and include the new `windowing` router the same way the other routers are included.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest backend/ -v`
Expected: total grows from 30 passed/1 skipped to 37 passed/1 skipped.

- [ ] **Step 8: Commit**

```bash
git add backend/windowing.py backend/models.py backend/app.py backend/tests/test_channels_window_api.py
git commit -m "Add GET /channels/window with min-max downsampling and memoization"
```

---

### Task 2: `GET /beats/window` — range-filtered beat/arrhythmia markers

**Files:**
- Create: `backend/beats_window.py`
- Modify: `backend/models.py`
- Modify: `backend/app.py`
- Create: `backend/tests/test_beats_window_api.py`

**Interfaces:**
- Consumes: `app.state.beat_cache[path]` (M4, updated in place by M5's arrhythmia detection). No new cache — filtering a DataFrame already in memory is cheap and doesn't need memoization.
- Produces: `GET /beats/window` — query params `path: str, start: float, end: float`; response is the subset of beats (and, if arrhythmia detection has already run for this file, their category flags) whose `ts` falls in `[start, end]`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_beats_window_api.py`, using `real_beats_txt_file` (`57.txt`, M4's fixture — 15 beats, `ts` from `0.0855` to the last beat's timestamp) and `long_txt_file` (`9 long.txt`, M5's fixture — 163 beats):

1. **Filter after beat detection only (no arrhythmia run yet).** Import + `POST /beats/detect` on `57.txt`. `GET /beats/window?path=<path>&start=0&end=0.25` — this real range covers exactly the first 2 beats (`ts=0.0855`, `ts=0.21`; the 3rd beat is `ts=0.3010`, outside the range). Assert `status == "ok"`, `count == 2`, both beats have `ts`/`rr`/`r_amplitude`/`hr` populated (matching M4's `Beat` fields) and every arrhythmia category field (`any_arrhythmia`, `other_arrhythmia`, `abn_cluster`, etc.) is `None` — arrhythmia detection hasn't run, so `beat_cache[path]` has no category columns yet.
2. **Filter after arrhythmia detection has run.** Same file, additionally `POST /arrhythmia/detect` with `method: "heuristic"` before the window call. Repeat the same `start=0&end=0.25` query. Assert the same 2 beats, but now `any_arrhythmia`/`other_arrhythmia` are real booleans (not `None`) — first beat (`ts=0.0855`) has `tachycardia_absolute == True, any_arrhythmia == True`; second beat (`ts=0.21`) has every category `False` — matching M5's already-verified per-beat values for this file.
3. **Full-range query returns every beat.** `GET /beats/window?path=<path>&start=0&end=999` (well past the file's real duration) on the already-beat-detected `57.txt`. Assert `count == 15` (the full real beat count).
4. **Narrow range on the larger file, after "both" methods.** Import + beat-detect + arrhythmia-detect (`method: "both"`) `9 long.txt`. `GET /beats/window?path=<path>&start=0&end=1` — assert every returned beat's `ts` is in `[0, 1]`, and `abn_cluster` is a real bool (not `None`) for each, matching M5's established behavior for this file+method.
5. **Empty range.** Same file, `start=200&end=300` — guaranteed past every beat's `ts`, since the file's own signal data tops out at `ts=111.7995` (verified this session) and no beat can have a `ts` beyond its source signal's range. Assert `status == "ok"`, `count == 0`, `beats == []` — legitimate empty result, not an error.
6. **Beat detection never run.** `GET /beats/window` for a path that was imported but never beat-detected. Assert `status == "error"`, non-empty `error` (reuse the exact phrasing already established in `arrhythmia.py`: `"Beat detection has not been run for this file yet"` — keep error strings for the same underlying condition consistent across endpoints).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest backend/tests/test_beats_window_api.py -v`
Expected: FAIL — route doesn't exist yet.

- [ ] **Step 3: Implement the model**

In `backend/models.py`, add:
- `WindowBeat(BaseModel)`: `ts: float`, `rr: float`, `r_amplitude: float`, `hr: float`, `bradycardia_absolute: bool | None = None`, `tachycardia_absolute: bool | None = None`, `skipped_beat: bool | None = None`, `prem_beat: bool | None = None`, `abn_cluster: bool | None = None`, `any_arrhythmia: bool | None = None`, `other_arrhythmia: bool | None = None` (unlike M5's `ArrhythmiaBeat`, `any_arrhythmia`/`other_arrhythmia` are optional here too — arrhythmia detection may not have run yet for this file, so `beat_cache[path]` may only have the base beat-detection columns).
- `BeatWindowResult(BaseModel)`: `status: str`, `beats: list[WindowBeat] = []`, `count: int = 0`, `error: str | None = None`.

- [ ] **Step 4: Implement `backend/beats_window.py`**

An `APIRouter(prefix="/beats", tags=["beats"])` with `GET /window`, query params `path: str, start: float, end: float`:

1. `df = request.app.state.beat_cache.get(path)` — if `None`, return `BeatWindowResult(status="error", error="Beat detection has not been run for this file yet")`.
2. Filter: `filtered = df[(df["ts"] >= start) & (df["ts"] <= end)]`.
3. Build the `beats` list: for each row (`filtered.itertuples()`), always set `ts`, `rr=row.RR`, `r_amplitude=row.R_amplitude`, `hr=row.HR` (same mapping M4's `beats.py` already uses); for each of `bradycardia_absolute, tachycardia_absolute, skipped_beat, prem_beat, abn_cluster, any_arrhythmia, other_arrhythmia`, set it only if that column is present in `df.columns` (`col in df.columns`), reading via `getattr(row, col)` and mapping `NaN -> None` via `pd.isna()` exactly like `arrhythmia.py` already does — else leave the model default (`None`).
4. Return `BeatWindowResult(status="ok", beats=beats, count=len(beats))`.

This router uses a separate `prefix="/beats"` `APIRouter` instance from `backend/beats.py`'s — FastAPI merges routers sharing a prefix cleanly as long as their paths don't collide (`/beats/detect` vs `/beats/window` — no collision).

- [ ] **Step 5: Wire into `backend/app.py`**

Import and include the new `beats_window` router the same way the other routers are included (rename the import alias if needed to avoid colliding with the existing `beats_router` from `backend/beats.py` — e.g. `from backend.beats_window import router as beats_window_router`).

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest backend/ -v`
Expected: total grows from 37 passed/1 skipped to 43 passed/1 skipped.

- [ ] **Step 7: Commit**

```bash
git add backend/beats_window.py backend/models.py backend/app.py backend/tests/test_beats_window_api.py
git commit -m "Add GET /beats/window for range-filtered beat/arrhythmia markers"
```

---

## Self-Review

- **Spec coverage:** §M6's two endpoints (`channels window` downsampling + memoization, `beats window` range filter) are both covered by Task 1 and Task 2 respectively. The spec's own note that "the algorithm is kept as-is... the bug being fixed is the recomputation, not the math" is honored — Task 1 explicitly instructs porting `main.py`'s algorithm unchanged, not redesigning it.
- **Deviation flagged in Global Constraints**: `path` as a query param, not a URL segment — matches this backend's existing convention (every other endpoint already avoids raw file paths in URLs) and is called out explicitly rather than silently diverging from the spec's literal route shape.
- **Type/interface consistency:** `WindowBeat`'s field names (`rr`, `r_amplitude`, `hr`) match M4's `Beat` model and M5's `ArrhythmiaBeat` category field names exactly — no renamed fields between milestones.
- **No placeholders:** every test has real, grounded assertion values (verified this session against `.venv` and the real example files); the downsampling algorithm's port is fully specified by reference to exact `main.py` line numbers plus the exact behavioral contract, not left as "port the algorithm" prose alone.
