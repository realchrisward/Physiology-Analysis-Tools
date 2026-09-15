# M7: Persistence, Review Mutations, Report Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SQLite-backed persistence for a file's channel choice, beat/arrhythmia results, and bad-data marks, keyed by `(path, size, mtime)`; endpoints to load a prior record, confirm/reject/reassign a beat's arrhythmia classification (fixing the old app's unreliable "Confirm Arrhythmia" and its reject-doesn't-cascade gap), manage bad-data marks, and export the same Excel report shape the old app produced. This is the last backend milestone — no frontend work starts until this is done and reviewed.

**Architecture:** stdlib `sqlite3`, no new dependency. One DB file per app instance (`app.state.db_path`, defaulting to a real per-user location; tests pass an isolated temp path). A fresh `sqlite3.connect()` per request (SQLite handles this cheaply; avoids cross-thread connection-sharing issues since FastAPI threadpools sync handlers). Schema created idempotently (`CREATE TABLE IF NOT EXISTS`) on every connect. All new endpoints reuse this backend's established conventions: `path` as a query param on `GET`, as a JSON body field on `PUT`/`POST`/`PATCH`/`DELETE` (matches M4-M6); every failure path returns HTTP 200 with `status: "error"`, never a 500.

**Tech Stack:** Python 3.12, FastAPI, stdlib `sqlite3`, `xlsxwriter` (already a transitive dependency via `pandas.ExcelWriter` — confirmed importable in `.venv` this session, version 3.2.9 — no new entry needed in `backend/requirements.txt`).

**Spec:** `docs/superpowers/specs/2026-09-28-backend-completion-design.md` (§M7), `docs/superpowers/specs/2026-09-14-ui-workflow-features-design.md` (§1 "Annotation persistence", "Reject cascades", "Confirm Arrhythmia bug").

## Global Constraints

- Every failure path returns a clean per-call error (HTTP 200, `status: "error"`), never a 500.
- `path` is always a query parameter on `GET` requests and a JSON body field on `PUT`/`POST`/`PATCH`/`DELETE` requests — same convention as M4-M6, for the same reason (file paths don't round-trip safely as URL segments).
- A file's `(size, mtime)` for every persistence operation comes from `app.state.imported_files[path]` (already `os.stat()`'d at import time) — never a fresh `os.stat()` call. Every persistence endpoint therefore requires the file to have been imported this session first; clean error `f"File not imported: {path}"` (same string `beats.py` already uses) if not.
- **Reject cascades**: rejecting a beat clears every category flag on it (`bradycardia_absolute`, `tachycardia_absolute`, `skipped_beat`, `prem_beat`, `abn_cluster`, `other_arrhythmia`, and `any_arrhythmia`), not just a top-level annotation — this is the exact gap the workflow spec calls out in the old app (`main.py`'s `action_reject_arrhythmia` only ever sets `annot_any_arrhythmia = -1` and leaves every individual category column untouched).
- Tests run against real example files and a real (temp-file-backed) SQLite database, never mocked. Use `.venv`'s Python for every command — never the bare `python3` on this machine (stale PyPI install).

---

### Task 1: SQLite schema + `GET /files/state`

**Files:**
- Create: `backend/db.py`
- Modify: `backend/models.py`
- Modify: `backend/app.py`
- Create: `backend/tests/test_files_state_api.py`

**Interfaces:**
- Produces: `backend/db.py`'s `connect(db_path: str) -> sqlite3.Connection` (row factory set to `sqlite3.Row`, schema created idempotently), `SCHEMA_SQL` (the three `CREATE TABLE IF NOT EXISTS` statements below), and `default_db_path() -> str` (real per-user location). `app.state.db_path: str` (new). **`create_app()` gains a new optional parameter**: `create_app(db_path: str | None = None) -> FastAPI`, defaulting to `db.default_db_path()` when not passed — every test in this milestone calls `create_app(db_path=str(tmp_path / "test.db"))` (pytest's built-in `tmp_path` fixture, no custom fixture needed) so tests never touch the real per-user DB file and never share state across test runs. Every existing test file that calls `create_app()` with no arguments is unaffected (the parameter is optional and defaults to real behavior).
- Consumes (later tasks): every M7 task calls `db.connect(request.app.state.db_path)` and is responsible for closing the connection (`try/finally` or `with contextlib.closing(...)`).

- [ ] **Step 1: Implement `backend/db.py`**

Three tables, exact schema:

```sql
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    channel TEXT,
    beat_settings_json TEXT,
    arrhythmia_settings_json TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(path, size, mtime)
);

CREATE TABLE IF NOT EXISTS beats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id),
    ts REAL NOT NULL,
    rr REAL NOT NULL,
    r_amplitude REAL NOT NULL,
    hr REAL NOT NULL,
    bradycardia_absolute INTEGER,
    tachycardia_absolute INTEGER,
    skipped_beat INTEGER,
    prem_beat INTEGER,
    abn_cluster INTEGER,
    any_arrhythmia INTEGER,
    other_arrhythmia INTEGER,
    review_state TEXT NOT NULL DEFAULT 'unreviewed',
    reassigned_category TEXT,
    UNIQUE(file_id, ts)
);

CREATE TABLE IF NOT EXISTS bad_data_marks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id),
    start REAL NOT NULL,
    stop REAL NOT NULL
);
```

Nullable `INTEGER` columns on `beats` store booleans as `0`/`1`/`NULL` (SQLite has no native bool) — same optional-or-`None` semantics as M5/M6's `ArrhythmiaBeat`/`WindowBeat` models; a file that's only had beat detection (no arrhythmia detection) persists all five category columns plus `any_arrhythmia`/`other_arrhythmia` as `NULL`.

`connect(db_path: str) -> sqlite3.Connection`: `sqlite3.connect(db_path)`, set `.row_factory = sqlite3.Row`, `executescript(SCHEMA_SQL)`, `commit()`, return the connection. `default_db_path() -> str`: `os.path.join(os.path.expanduser("~"), ".physiology_analysis_tools", "state.db")`, creating the parent directory (`os.makedirs(..., exist_ok=True)`) if missing — this is the "app's own data folder (not next to raw recordings)" the spec calls for.

`get_file_row(conn, path, size, mtime) -> sqlite3.Row | None`: `SELECT * FROM files WHERE path=? AND size=? AND mtime=?`, `fetchone()`.

- [ ] **Step 2: Write the failing tests**

In `backend/tests/test_files_state_api.py`, every test constructs its own isolated app via `create_app(db_path=str(tmp_path / "test.db"))` (pytest's built-in `tmp_path` fixture — same per-test-`create_app()` pattern `test_settings_api.py` already uses, just with the new `db_path` argument) and `TestClient(app)`, using `example_txt_file` (`10.txt`):

1. **No prior record.** Import `10.txt` (never persisted). `GET /files/state?path=<path>`. Assert `status == "ok"`, `found == False`, `channel is None`, `beats == []`, `bad_data_marks == []`.
2. **File never imported.** `GET /files/state` with a `path` that was never imported this session. Assert `status == "error"`, non-empty `error`.
3. **Prior record exists, minimal (channel only, via direct DB insert).** Import `10.txt`. Insert a `files` row directly via `db.connect()` (bypassing the not-yet-built `PUT /files/channel` endpoint, since this task doesn't build it) with `channel="channel 1"`, using the real `size`/`mtime` from the import response, `beat_settings_json=None`, `arrhythmia_settings_json=None`. `GET /files/state?path=<path>`. Assert `status == "ok"`, `found == True`, `channel == "channel 1"`, `beats == []`, `bad_data_marks == []`, `beat_settings is None`, `arrhythmia_settings is None`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest backend/tests/test_files_state_api.py -v`
Expected: FAIL — route doesn't exist yet.

- [ ] **Step 4: Implement the models**

In `backend/models.py`, add:
- `PersistedBeat(BaseModel)`: `ts: float`, `rr: float`, `r_amplitude: float`, `hr: float`, `bradycardia_absolute: bool | None = None`, `tachycardia_absolute: bool | None = None`, `skipped_beat: bool | None = None`, `prem_beat: bool | None = None`, `abn_cluster: bool | None = None`, `any_arrhythmia: bool | None = None`, `other_arrhythmia: bool | None = None`, `review_state: str = "unreviewed"`, `reassigned_category: str | None = None`.
- `BadDataMark(BaseModel)`: `id: int`, `start: float`, `stop: float`.
- `FileStateResult(BaseModel)`: `status: str`, `found: bool = False`, `channel: str | None = None`, `beats: list[PersistedBeat] = []`, `bad_data_marks: list[BadDataMark] = []`, `beat_settings: BeatSettingsModel | None = None`, `arrhythmia_settings: ArrhythmiaSettingsModel | None = None`, `error: str | None = None`.

- [ ] **Step 5: Implement `GET /files/state` in `backend/db_routes.py`**

New file `backend/db_routes.py` (this and every later M7 task's endpoint live here — one router, one file, since they all share the same DB-access pattern and it keeps `backend/files.py` — the M1 import/list router — untouched). `APIRouter(prefix="/files", tags=["persistence"])`, `GET /state`:

1. `imported = request.app.state.imported_files.get(path)` — if `None` or `imported.status != "ok"`, return `FileStateResult(status="error", error=f"File not imported: {path}")`.
2. `conn = db.connect(request.app.state.db_path)`; `try`: `row = db.get_file_row(conn, path, imported.size, imported.modified_time)`. If `row is None`: return `FileStateResult(status="ok", found=False)`.
3. Else: query `beats` and `bad_data_marks` for `file_id=row["id"]`, ordered by `ts`/`id`. Build `PersistedBeat`/`BadDataMark` lists (SQLite `0`/`1`/`NULL` → Python `bool | None` via `None if v is None else bool(v)`). Parse `beat_settings_json`/`arrhythmia_settings_json` via `json.loads` into `BeatSettingsModel`/`ArrhythmiaSettingsModel` if not `None`. Return `FileStateResult(status="ok", found=True, channel=row["channel"], beats=..., bad_data_marks=..., beat_settings=..., arrhythmia_settings=...)`.
4. `finally: conn.close()`.

- [ ] **Step 6: Wire into `backend/app.py`**

Change `create_app()`'s signature to `def create_app(db_path: str | None = None) -> FastAPI`. Inside, `app.state.db_path = db_path if db_path is not None else db.default_db_path()`. Import and include the new `db_routes` router. Leave the module-level `app = create_app()` line unchanged (no `db_path` passed — uses the real default).

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest backend/ -v`
Expected: total grows from 44 passed/1 skipped to 47 passed/1 skipped.

- [ ] **Step 8: Commit**

```bash
git add backend/db.py backend/db_routes.py backend/models.py backend/app.py backend/tests/test_files_state_api.py
git commit -m "Add SQLite persistence layer and GET /files/state"
```

---

### Task 2: `PUT /files/channel` + `POST /files/beats`

**Files:**
- Modify: `backend/db.py`
- Modify: `backend/db_routes.py`
- Modify: `backend/models.py`
- Create: `backend/tests/test_files_persist_api.py`

**Interfaces:**
- Consumes: `app.state.beat_cache[path]` (M4/M5), `app.state.beat_settings`/`app.state.arrhythmia_settings` (M5).
- Produces: `db.upsert_file(conn, path, size, mtime, channel=None, beat_settings_json=None, arrhythmia_settings_json=None) -> int` (returns `file_id`; `INSERT ... ON CONFLICT(path, size, mtime) DO UPDATE SET` — only overwrites the columns explicitly passed a non-default value, so `PUT /files/channel` doesn't blow away a `beat_settings_json` a prior `POST /files/beats` already saved, and vice versa).

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_files_persist_api.py` (same per-test `create_app(db_path=str(tmp_path / "test.db"))` pattern as Task 1), using `real_beats_txt_file` (`57.txt`, 15 beats):

1. **`PUT /files/channel` persists, `GET /files/state` reflects it.** Import `57.txt`. `PUT /files/channel` with `{path, channel: "channel 1"}`. Assert `status == "ok"`, `channel == "channel 1"`. Then `GET /files/state?path=<path>`: assert `found == True`, `channel == "channel 1"`, `beats == []` (no beats persisted yet).
2. **`PUT /files/channel` on a never-imported file.** Assert `status == "error"`, non-empty `error`.
3. **`POST /files/beats` persists a full beat-detection-only result.** Import + `POST /beats/detect` (channel `"channel 1"`) on `57.txt`. `POST /files/beats` with `{path, channel: "channel 1"}`. Assert `status == "ok"`, `count == 15`. `GET /files/state?path=<path>`: assert `found == True`, `len(beats) == 15`, every beat has `review_state == "unreviewed"`, `reassigned_category is None`, and every arrhythmia category field (`any_arrhythmia`, etc.) is `None` (arrhythmia detection hasn't run) — real, verified: first beat `ts ≈ 0.0855`.
4. **`POST /files/beats` on a file with no beat-detection result yet.** Assert `status == "error"`, non-empty `error` (reuse `beats_window.py`'s exact string: `"Beat detection has not been run for this file yet"`).
5. **`POST /files/beats` is idempotent/wholesale-replacing.** Call it twice in a row for the same file+channel (no change to `beat_cache` in between). Assert the second call still reports `count == 15` and `GET /files/state` still shows exactly 15 beats (not 30) — proves the persist step deletes-then-reinserts rather than accumulating duplicates.
6. **`POST /files/beats` after arrhythmia detection.** Additionally run `POST /arrhythmia/detect` (`method: "heuristic"`) before persisting. Assert the persisted first beat (`ts ≈ 0.0855`) now has `tachycardia_absolute == True`, `any_arrhythmia == True` (real, verified values from M5) — proves the category columns round-trip through SQLite's `0`/`1`/`NULL` correctly.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest backend/tests/test_files_persist_api.py -v`
Expected: FAIL — routes don't exist yet.

- [ ] **Step 3: Implement `db.upsert_file` and `db.replace_beats` in `backend/db.py`**

`upsert_file(conn, path, size, mtime, *, channel=<UNSET sentinel>, beat_settings_json=<UNSET>, arrhythmia_settings_json=<UNSET>) -> int`: build an `INSERT INTO files (path, size, mtime, channel, beat_settings_json, arrhythmia_settings_json, updated_at) VALUES (...) ON CONFLICT(path, size, mtime) DO UPDATE SET updated_at=excluded.updated_at` plus one `SET column=excluded.column` clause per argument that was actually passed (not the sentinel) — use a plain sentinel object (`_UNSET = object()`) to distinguish "caller didn't pass this" from "caller explicitly passed `None`". `updated_at` is `datetime.now(timezone.utc).isoformat()`. Commit, return `cursor.lastrowid` if inserted, else look up and return the existing row's `id` via `get_file_row`.

`replace_beats(conn, file_id, beat_df)`: `DELETE FROM beats WHERE file_id=?`, then bulk-`INSERT` one row per `beat_df.itertuples()`, `ts=row.ts, rr=row.RR, r_amplitude=row.R_amplitude, hr=row.HR` (same mapping every earlier endpoint uses) plus each optional category column only if present in `beat_df.columns` else `NULL`, `review_state='unreviewed'`, `reassigned_category=NULL`. Commit.

- [ ] **Step 4: Implement the models**

In `backend/models.py`, add:
- `ChannelPersistRequest(BaseModel)`: `path: str`, `channel: str`.
- `ChannelPersistResult(BaseModel)`: `status: str`, `channel: str | None = None`, `error: str | None = None`.
- `PersistBeatsRequest(BaseModel)`: `path: str`, `channel: str`.
- `PersistBeatsResult(BaseModel)`: `status: str`, `count: int = 0`, `error: str | None = None`.

- [ ] **Step 5: Implement the two endpoints in `backend/db_routes.py`**

`PUT /channel`, body `ChannelPersistRequest`: look up `imported_files[path]` (clean error if missing, same string as before). `conn = db.connect(...)`; `try: db.upsert_file(conn, path, imported.size, imported.modified_time, channel=payload.channel); return ChannelPersistResult(status="ok", channel=payload.channel)`; `finally: conn.close()`.

`POST /beats`, body `PersistBeatsRequest`: look up `imported_files[path]` (clean error if missing). Look up `beat_df = request.app.state.beat_cache.get(path)` — if `None`, return `PersistBeatsResult(status="error", error="Beat detection has not been run for this file yet")`. `conn = db.connect(...)`; `try`: `file_id = db.upsert_file(conn, path, imported.size, imported.modified_time, channel=payload.channel, beat_settings_json=json.dumps(request.app.state.beat_settings.__dict__), arrhythmia_settings_json=json.dumps(request.app.state.arrhythmia_settings.__dict__))`; `db.replace_beats(conn, file_id, beat_df)`; return `PersistBeatsResult(status="ok", count=len(beat_df))`; `finally: conn.close()`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest backend/ -v`
Expected: total grows from 47 passed/1 skipped to 53 passed/1 skipped.

- [ ] **Step 7: Commit**

```bash
git add backend/db.py backend/db_routes.py backend/models.py backend/tests/test_files_persist_api.py
git commit -m "Add PUT /files/channel and POST /files/beats persistence"
```

---

### Task 3: `PATCH /files/beats/category` — confirm/reject/reassign, reject-cascade fix

**Files:**
- Modify: `backend/db.py`
- Modify: `backend/db_routes.py`
- Modify: `backend/models.py`
- Create: `backend/tests/test_beats_category_api.py`

**Interfaces:**
- Consumes: a `files` row + matching `beats` row must already exist (from Task 2's `POST /files/beats`).
- Produces: `PATCH /files/beats/category` — body `{path, ts, action: "confirm"|"reject"|"reassign", category: str | None}`.

`REASSIGNABLE_CATEGORIES = {"bradycardia_absolute", "tachycardia_absolute", "skipped_beat", "prem_beat", "abn_cluster", "other_arrhythmia"}` (the six real category columns — `any_arrhythmia` is a derived summary flag, never a reassignment target).

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_beats_category_api.py` (same per-test `create_app(db_path=str(tmp_path / "test.db"))` pattern), using `real_beats_txt_file` (`57.txt`): import, `POST /beats/detect`, `POST /arrhythmia/detect` (`method: "heuristic"`), `POST /files/beats` to persist (per Task 2) before every test below — the first beat (`ts ≈ 0.0855`) has `tachycardia_absolute=True, any_arrhythmia=True`; the second (`ts ≈ 0.21`) has every category `False`.

1. **Confirm.** `PATCH /files/beats/category` `{path, ts: 0.0855, action: "confirm"}`. Assert `status == "ok"`, `review_state == "confirmed"`. `GET /files/state`: that beat's `tachycardia_absolute` is still `True` (confirm doesn't touch category flags), `review_state == "confirmed"`.
2. **Reject cascades.** Same file, `{path, ts: 0.0855, action: "reject"}`. Assert `status == "ok"`, `review_state == "rejected"`. `GET /files/state`: that beat now has `tachycardia_absolute == False`, `any_arrhythmia == False`, and every other category field `False` (not `None` — they were real booleans before the reject, so they become real `False`, not "not evaluated") — this is the reject-cascade fix; assert this explicitly rather than just checking `any_arrhythmia`, since the old app's bug was specifically that sub-category flags survived.
3. **Reassign.** Same file, `{path, ts: 0.21, action: "reassign", category: "prem_beat"}` (a beat that started with every category `False`). Assert `status == "ok"`, `review_state == "confirmed"`, `reassigned_category == "prem_beat"`. `GET /files/state`: that beat's `prem_beat == True`, every other category `False`, `any_arrhythmia == True`.
4. **Reassign with an invalid category.** `{path, ts: 0.0855, action: "reassign", category: "not_a_real_category"}`. Assert `status == "error"`, non-empty `error`. `GET /files/state`: that beat's row is unchanged from before this call (no partial apply).
5. **Reassign without a category.** `{path, ts: 0.0855, action: "reassign"}` (omit `category`). Assert `status == "error"`, non-empty `error`.
6. **Unknown ts (no matching beat).** `{path, ts: 999.0, action: "confirm"}`. Assert `status == "error"`, non-empty `error`.
7. **No persisted record for this file at all.** A different imported-but-never-`POST /files/beats`'d file. Assert `status == "error"`, non-empty `error`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest backend/tests/test_beats_category_api.py -v`
Expected: FAIL — route doesn't exist yet.

- [ ] **Step 3: Implement `db.update_beat_category` in `backend/db.py`**

`update_beat_category(conn, file_id, ts, action, category=None) -> sqlite3.Row | None`: `SELECT id FROM beats WHERE file_id=? AND ts=?` (float `ts` equality — safe here since it's always a value that round-tripped through this exact pipeline, not user-typed). If no row, return `None`. Build the `UPDATE beats SET ...` per `action`:
- `"confirm"`: `review_state='confirmed'`.
- `"reject"`: `review_state='rejected'`, all six category columns + `any_arrhythmia` set to `0`, `reassigned_category=NULL`.
- `"reassign"`: all six category columns set to `0` except `category` set to `1`, `any_arrhythmia=1`, `review_state='confirmed'`, `reassigned_category=category`.

Commit, then `SELECT * FROM beats WHERE id=?` and return the fresh row (so the endpoint reflects what's actually in the DB, not the input).

- [ ] **Step 4: Implement the models**

In `backend/models.py`, add:
- `CategoryUpdateRequest(BaseModel)`: `path: str`, `ts: float`, `action: str`, `category: str | None = None`.
- `CategoryUpdateResult(BaseModel)`: `status: str`, `ts: float | None = None`, `review_state: str | None = None`, `reassigned_category: str | None = None`, `error: str | None = None`.

- [ ] **Step 5: Implement `PATCH /files/beats/category` in `backend/db_routes.py`**

1. Validate `action in {"confirm", "reject", "reassign"}` — else clean error.
2. If `action == "reassign"`: validate `category is not None and category in REASSIGNABLE_CATEGORIES` — else clean error, distinct messages for "missing" vs "invalid" if easy, one combined message is acceptable otherwise.
3. `imported = request.app.state.imported_files.get(payload.path)` — clean error if missing.
4. `conn = db.connect(...)`; `try`: `file_row = db.get_file_row(conn, payload.path, imported.size, imported.modified_time)` — if `None`, clean error ("No persisted data for this file — run POST /files/beats first"). `updated = db.update_beat_category(conn, file_row["id"], payload.ts, payload.action, payload.category)` — if `None`, clean error ("No beat found at this timestamp"). Return `CategoryUpdateResult(status="ok", ts=updated["ts"], review_state=updated["review_state"], reassigned_category=updated["reassigned_category"])`. `finally: conn.close()`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest backend/ -v`
Expected: total grows from 53 passed/1 skipped to 60 passed/1 skipped.

- [ ] **Step 7: Commit**

```bash
git add backend/db.py backend/db_routes.py backend/models.py backend/tests/test_beats_category_api.py
git commit -m "Add PATCH /files/beats/category with reject-cascade fix"
```

---

### Task 4: `POST`/`DELETE /files/bad-data`

**Files:**
- Modify: `backend/db.py`
- Modify: `backend/db_routes.py`
- Modify: `backend/models.py`
- Create: `backend/tests/test_bad_data_api.py`

**Interfaces:**
- Produces: `POST /files/bad-data` (add a mark, auto-sorted), `DELETE /files/bad-data` (remove one by id).

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_bad_data_api.py` (same per-test `create_app(db_path=str(tmp_path / "test.db"))` pattern), using `example_txt_file` (`10.txt`):

1. **Add a mark, auto-sorted regardless of drag direction.** Import `10.txt` (no `POST /files/beats` needed first — bad-data marking is independent of beat results). `POST /files/bad-data` `{path, start: 0.8, stop: 0.3}` (reversed — a right-to-left drag). Assert `status == "ok"`, `mark.start == 0.3`, `mark.stop == 0.8` (auto-sorted min/max, not the raw input order). `GET /files/state`: `bad_data_marks == [{id: ..., start: 0.3, stop: 0.8}]`.
2. **Add on a never-imported file.** Assert `status == "error"`.
3. **Add a second mark, delete the first.** Add a second mark `{start: 0.9, stop: 1.0}`. `DELETE /files/bad-data` `{path, id: <first mark's id>}`. Assert `status == "ok"`. `GET /files/state`: `bad_data_marks` has exactly the second mark left.
4. **Delete a nonexistent id.** `DELETE /files/bad-data` `{path, id: 999999}`. Assert `status == "error"`, non-empty `error`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest backend/tests/test_bad_data_api.py -v`
Expected: FAIL — routes don't exist yet.

- [ ] **Step 3: Implement `db.add_bad_data_mark`/`db.delete_bad_data_mark` in `backend/db.py`**

`add_bad_data_mark(conn, file_id, start, stop) -> sqlite3.Row`: sort (`lo, hi = min(start, stop), max(start, stop)`), `INSERT INTO bad_data_marks (file_id, start, stop) VALUES (?, ?, ?)`, commit, `SELECT * FROM bad_data_marks WHERE id=?` (`cursor.lastrowid`), return the row.

`delete_bad_data_mark(conn, file_id, mark_id) -> bool`: `DELETE FROM bad_data_marks WHERE id=? AND file_id=?`, commit, return `cursor.rowcount > 0` (scoped to `file_id` too, so one file's `DELETE` can never remove another file's mark even if the id happened to collide — it can't in practice since ids are global autoincrement, but the explicit scope documents the intent and costs nothing).

- [ ] **Step 4: Implement the models**

In `backend/models.py`, add:
- `BadDataAddRequest(BaseModel)`: `path: str`, `start: float`, `stop: float`.
- `BadDataAddResult(BaseModel)`: `status: str`, `mark: BadDataMark | None = None`, `error: str | None = None`.
- `BadDataDeleteRequest(BaseModel)`: `path: str`, `id: int`.
- `BadDataDeleteResult(BaseModel)`: `status: str`, `error: str | None = None`.

- [ ] **Step 5: Implement the two endpoints in `backend/db_routes.py`**

`POST /bad-data`: look up `imported_files[path]` (clean error if missing). `conn = db.connect(...)`; `try`: `file_id = db.upsert_file(conn, path, imported.size, imported.modified_time)` (no `channel`/settings args — this may be the first-ever persistence action for this file, and must not clobber anything since none of the `ON CONFLICT` columns are passed); `row = db.add_bad_data_mark(conn, file_id, payload.start, payload.stop)`; return `BadDataAddResult(status="ok", mark=BadDataMark(id=row["id"], start=row["start"], stop=row["stop"]))`; `finally: conn.close()`.

`DELETE /bad-data`: look up `imported_files[path]` (clean error if missing). `conn = db.connect(...)`; `try`: `file_row = db.get_file_row(conn, path, imported.size, imported.modified_time)` — clean error if `None` ("No persisted data for this file"). `deleted = db.delete_bad_data_mark(conn, file_row["id"], payload.id)` — clean error if `not deleted` ("No bad-data mark with this id"). Return `BadDataDeleteResult(status="ok")`. `finally: conn.close()`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest backend/ -v`
Expected: total grows from 60 passed/1 skipped to 64 passed/1 skipped.

- [ ] **Step 7: Commit**

```bash
git add backend/db.py backend/db_routes.py backend/models.py backend/tests/test_bad_data_api.py
git commit -m "Add POST/DELETE /files/bad-data with auto-sorted ranges"
```

---

### Task 5: `POST /files/report` — Excel export

**Files:**
- Modify: `backend/db_routes.py`
- Modify: `backend/models.py`
- Create: `backend/tests/test_report_api.py`

**Interfaces:**
- Consumes: a fully persisted `files` row (Task 2/3/4's data) — beats, bad-data marks, settings snapshot.
- Produces: `POST /files/report` — writes a real `.xlsx` file to disk, same three-sheet shape as the old app's `action_generate_report()` (`src/physiology_analysis_tools/main.py:1265-1299`): `beats`, `bad_data_marks`, `settings` sheets.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_report_api.py` (same per-test `create_app(db_path=str(tmp_path / "test.db"))` pattern — `tmp_path` also serves as a second, separate directory for `output_dir`, e.g. `str(tmp_path / "reports")` with `os.makedirs`), using `real_beats_txt_file` (`57.txt`):

1. **Full report, real file.** Import, `POST /beats/detect`, `POST /arrhythmia/detect` (`heuristic`), `POST /files/beats`, add one bad-data mark, then `POST /files/report` `{path, output_dir: <tmp dir>}`. Assert `status == "ok"`, `output_path` ends with `57.xlsx` and the file actually exists on disk (`os.path.exists`). Open it with `pandas.read_excel(output_path, sheet_name=None)` (reads all sheets) and assert: `"beats"` sheet has 15 rows and includes a `review_state` column (all `"unreviewed"`, nothing persisted-reviewed yet); `"bad_data_marks"` sheet has exactly 1 row with the mark's `start`/`stop`; `"settings"` sheet has exactly 1 row and includes `heartbeat_version`, `arrhythmia_version`, `ml_version` columns with real non-empty string values (verified this session: `heartbeat_detection.__version__ == "0.0.4"`, `arrhythmia_detection.__version__ == "0.0.9"`, `ml_tools.__version__ == "0.0.1"` — assert exact equality against these).
2. **No persisted data for this file.** `POST /files/report` for an imported-but-never-`POST /files/beats`'d file. Assert `status == "error"`, non-empty `error`.
3. **Nonexistent `output_dir`.** Real persisted file, `output_dir` pointing at a path that doesn't exist. Assert `status == "error"`, non-empty `error` — not a crash.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest backend/tests/test_report_api.py -v`
Expected: FAIL — route doesn't exist yet.

- [ ] **Step 3: Implement the models**

In `backend/models.py`, add:
- `ReportRequest(BaseModel)`: `path: str`, `output_dir: str`.
- `ReportResult(BaseModel)`: `status: str`, `output_path: str | None = None`, `error: str | None = None`.

- [ ] **Step 4: Implement `POST /files/report` in `backend/db_routes.py`**

1. Look up `imported_files[path]` (clean error if missing).
2. `conn = db.connect(...)`; `try`: `file_row = db.get_file_row(conn, path, imported.size, imported.modified_time)` — clean error if `None` ("No persisted data for this file — run POST /files/beats first").
3. Validate `os.path.isdir(payload.output_dir)` — clean error if not.
4. Build three `pandas.DataFrame`s from the DB: `beats_df` (all columns from the `beats` table for this `file_id`, via `pandas.read_sql_query` or a manual row-to-dict loop — either is fine; exclude the internal `id`/`file_id` columns from the exported sheet, they're storage-only); `bad_data_df` (`start`, `stop` columns only, matching the old app's exact shape — `pandas.DataFrame(marks, columns=["start", "stop"])`); `settings_df` — one row combining `json.loads(file_row["beat_settings_json"])`, `json.loads(file_row["arrhythmia_settings_json"])`, plus `heartbeat_version`, `arrhythmia_version`, `ml_version` from `heartbeat_detection.__version__`/`arrhythmia_detection.__version__`/`ml_tools.__version__` (import `ml_tools` alongside the existing `heartbeat_detection`/`arrhythmia_detection` imports — no `main_version`/app-version field, since this backend has no equivalent versioning concept to the old PySide6 app's `__version__`, a deliberate simplification from the legacy shape, not an oversight).
5. `output_path = os.path.join(payload.output_dir, os.path.splitext(os.path.basename(path))[0] + ".xlsx")`. Inside a `try`: `writer = pandas.ExcelWriter(output_path, engine="xlsxwriter"); beats_df.to_excel(writer, sheet_name="beats", index=False); bad_data_df.to_excel(writer, sheet_name="bad_data_marks", index=False); settings_df.to_excel(writer, sheet_name="settings", index=False); writer.close()` — same call shape as `main.py:1296-1299`. On any exception, clean error, not a crash.
6. Return `ReportResult(status="ok", output_path=output_path)`.
7. `finally: conn.close()`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest backend/ -v`
Expected: total grows from 64 passed/1 skipped to 67 passed/1 skipped.

- [ ] **Step 6: Commit**

```bash
git add backend/db_routes.py backend/models.py backend/tests/test_report_api.py
git commit -m "Add POST /files/report Excel export"
```

---

## Self-Review

- **Spec coverage:** §M7's schema (`files`/`beats`/`bad_data_marks`, keyed by path+size+mtime), all six named endpoints (`state`, `channel`, `beats` persist, `category` patch, bad-data add/delete, report), the reject-cascade fix, and the "Confirm Arrhythmia gets fixed" goal are each covered by a task above. "Autosave" (spec's framing: fast mutation endpoints called on every technician action, no separate timer) requires no additional backend work beyond what's already built — it's a frontend calling pattern, noted here so it isn't mistaken for a missing task.
- **Type/interface consistency:** `PersistedBeat`'s field names and optionality match `WindowBeat` (M6) and `ArrhythmiaBeat` (M5) exactly (`rr`, `r_amplitude`, `hr`, same five category names). `REASSIGNABLE_CATEGORIES` matches the five per-beat category names from M5 plus `other_arrhythmia`, excluding the derived `any_arrhythmia`.
- **No placeholders:** every test has real, grounded assertion values (module versions verified against `.venv` this session; beat counts/timestamps reuse M4/M5's already-verified `57.txt` values); the SQLite schema, upsert/replace/update SQL, and Excel-export shape are each fully specified, not left as "persist the data" prose.
- **Deviation flagged in Global Constraints**: `path` as query-param-on-GET/body-field-elsewhere, consistent with M4-M6, not the spec's literal URL-segment shape.
