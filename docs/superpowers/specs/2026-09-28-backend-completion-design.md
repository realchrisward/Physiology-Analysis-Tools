# Backend Completion — Beat Detection, Arrhythmia, Windowing, Persistence

Builds on `2026-09-07-web-ui-redesign-design.md` (architecture) and
`2026-09-14-ui-workflow-features-design.md` (workflow). Covers the four
remaining backend milestones needed before any frontend screen can be built
against real data instead of mocks. No frontend work happens until all four
land — see `BACKEND_OVERVIEW.md` for what already exists (file import,
channel selection).

## Cross-milestone state

Extends the existing `app.state` pattern (not module globals — matches
`BACKEND_OVERVIEW.md`'s own stated reason: isolated state per test/app
instance):
- `signal_cache[path] -> {df, time_column}` — populated by `/files/import`
  (currently extracts the DataFrame just to build response metadata and
  discards it; this milestone set keeps it). No eviction in this pass —
  full DataFrames stay resident for the app's lifetime; the windowing
  milestone below will likely change this strategy later, not now.
- `beat_cache[path] -> beat_df` — populated by beat detection, updated
  in-place by arrhythmia detection (merges its category columns in).
  Ephemeral — cleared/overwritten on re-detection (e.g. channel change).
- `beat_settings` / `arrhythmia_settings` in `app.state`, global (matches
  the old app: one settings instance shared across whatever file is being
  worked on, not per-file — "settings used" gets snapshotted per-file only
  when persisted in M7).

## M4 — Beat detection

`POST /beats/detect` — body `{path, channel}`. Looks up `signal_cache[path]`
(clean error if missing — file was never imported). Calls
`heartbeat_detection.beatcaller(df, time_column=<from cache>,
voltage_column=channel, **beat_settings.__dict__)`, unmodified. Catches any
exception from `beatcaller()` as a clean per-call error, never a 500.
Caches the result into `beat_cache[path]`. One file per call — the frontend
owns any batching/stop behavior, nothing server-side to manage.

Response: the beat rows (`ts`, `RR`, `R_amplitude`, `HR`) plus a summary
(count, mean HR, duration — same figures the old app's status log showed),
plus `elapsed_seconds` and `file_size_bytes` for the frontend to build its
own running throughput estimate from (see below — the backend never
fabricates an ETA number, it only ever reports what actually happened).

**ETA note** (frontend concern, recorded here so it's not lost before that
work starts): show "Calculating..." until at least one real
`elapsed_seconds`/`file_size_bytes` pair exists for this session, then
compute from real measurements — never seed with a hardcoded guess
presented as a real number.

## M5 — Arrhythmia detection + settings

`POST /arrhythmia/detect` — body `{path, method: "heuristic"|"unsupervised"|"both"}`.
Requires `beat_cache[path]` to exist (clean error: "run beat detection
first"); unsupervised/both also need `signal_cache[path]` (the raw signal,
for `ml_tools.beatepocher`). Calls the existing
`arrhythmia_detection.call_arrhythmias()` unmodified, merges the result
back into `beat_cache[path]`, returns the per-beat category columns
(`arrhythmia_detection.arrhythmia_categories`: bradycardia_absolute,
tachycardia_absolute, skipped_beat, prem_beat, abn_cluster, any_arrhythmia,
other_arrhythmia).

Written as a **sync `def` endpoint** (not `async def`) — FastAPI
auto-threadpools it, the same pattern `BACKEND_OVERVIEW.md` already
documents as this codebase's deliberate choice for slow-but-atomic work.
No WebSocket/background-task infrastructure: PCA/DBSCAN has no real
mid-computation progress signal, the same reasoning that already ruled out
a progress stream for beat detection.

`GET /settings` / `PUT /settings` — beat + arrhythmia settings, held in
`app.state`. `PUT` rejects (clean error, not a 500) if
`bradycardia_absolute_hr >= tachycardia_absolute_hr` — the mutual-exclusivity
requirement from the workflow spec, enforced here since this is the only
place these settings are ever written.

## M6 — Data windowing (the graph)

`GET /channels/{path}/window?channel=&start=&end=&resolution=` — slices
`signal_cache[path]`'s DataFrame to `[start, end]`, downsamples with the
existing min-max-per-bin algorithm (ported from `main.py`'s
`min_max_downsample`/`gather_data`, decoupled from pyqtgraph — the
algorithm is kept as-is, it already correctly preserves spikes; the bug
being fixed is the *recomputation*, not the math). Memoized by
`(path, channel, start, end, resolution)` — that memoization is the
determinism fix: the same query always returns the identical cached
result, unlike the old bug of recomputing bins against a live-changing
pixel width. No pre-built resolution pyramid on import — compute-and-cache
on first request matches the workflow spec's own stated fallback and is
simpler; revisit only if a real file proves this too slow.

`GET /beats/{path}/window?start=&end=` — same range-filtering for beat and
arrhythmia markers (from `beat_cache[path]`), so the frontend never holds
or filters a full multi-day beat list client-side.

## M7 — Persistence + review mutations + report export

SQLite (stdlib `sqlite3`, no new dependency), one file in the app's own
data directory (not next to raw recordings — matches the workflow spec's
persistence decision). Keyed by `(path, size, mtime)`.

Schema: `files` (channel, settings-used snapshot as JSON, timestamps),
`beats` (one row per beat: ts/RR/amplitude/HR, one column per arrhythmia
category, plus a review-state column per beat — confirmed/rejected/
unreviewed, and which category was assigned if reassigned), `bad_data_marks`
(file reference, start, stop).

Endpoints:
- `GET /files/{path}/state` — prior record if one exists (matched by
  path+size+mtime), else a clean "no prior record" response. This is what
  lets the frontend decide "resume" vs. "start fresh."
- `PUT /files/{path}/channel` — persist the technician's channel choice.
- `POST /files/{path}/beats` — persist a fresh detect(+arrhythmia) result
  wholesale (called after M4/M5's endpoints return, once the technician's
  work should be saved).
- `PATCH /files/{path}/beats/{ts}/category` — confirm, reject, or reassign
  a single beat's classification. Reject cascades: clears every other
  category flag on that beat too, not just the top-level one — the gap the
  workflow spec explicitly calls out in the old app. This is also where
  **Confirm Arrhythmia gets fixed** — a fresh, tested endpoint in the new
  architecture, not a patch to the old broken Qt handler.
- `POST /files/{path}/bad-data` / `DELETE /files/{path}/bad-data/{id}` —
  add/remove a bad-data mark; start/stop auto-sorted server-side regardless
  of the order the frontend sends them in.
- `POST /files/{path}/report` — generates the Excel report from persisted
  state (beats sheet, bad-data-marks sheet, settings sheet), same shape as
  the old app's export.

"Autosave" is these mutation endpoints being fast and called on every
technician action (every confirm/reject click, every bad-data drag) — no
separate autosave timer or background job.

## Testing

Same standard as M1-M3: pytest against real example files already in the
repo, real calls into the unmodified analysis functions (never mocked),
error-path tests for every "clean error, not a 500" case named above.

## Build order

M4 → M5 → M6 → M7 (each depends on the previous existing; M7 last since it
needs the final shape of everything it persists). Frontend work starts
only once all four are done and reviewed.
