# UI Redesign — Workflow & Feature Decisions

Builds on `2026-09-07-web-ui-redesign-design.md` (Electron + FastAPI +
Svelte architecture, already approved). This doc covers *what* the new UI
does and *how work flows through it* — not visual layout, which comes next
once this is validated.

## 1. Decisions

**Annotation persistence** — a central local SQLite database in the app's
own data folder (not next to raw recordings), keyed by
`(absolute file path, file size, modified-time)`. Per file it stores:
selected channel, beat detection results, arrhythmia flags + review state
(confirmed/rejected/reassigned), bad-data ranges, settings used. Reopening
a previously-analyzed file loads this state instead of starting over.
Raw lab data files are never written to. If a file is later renamed/moved,
prior work won't auto-match (accepted trade-off; a manual "relink" is a
future nice-to-have, not in scope now).

**Default channel selection** — driven by a small, editable filename-pattern
→ exact-channel-name config table. Confirmed against real sample files
(`examples/ECG_traces`):

| Filename pattern | Channels present in file | Default channel |
|---|---|---|
| all digits (e.g. `556420`) | `channel 1`, `time`, `comment` | `channel 1` |
| `M` + digits (e.g. `M00561499`) | `channel 1`, `channel 2`, `channel 3`, `time`, `comment` | `channel 2` |
| `D` + digits (e.g. `D48136`) | `ekg`, `hr`, `rr`, `time`, `comment` | `ekg` |

Matching is exact (case-insensitive) against the channel name, not a
substring guess. `time` and `comment` are never offered as a signal
channel. User can always override via a channel-select dropdown. Once a
channel is picked for a specific file, that choice is remembered (part of
the annotation-persistence record for that file) and reused next time that
exact file is opened, ahead of the filename-pattern default.

**Bradycardia/tachycardia** — validated at the settings level: reject/warn
if `bradycardia_absolute_hr >= tachycardia_absolute_hr`, which is what
currently allows their detection ranges to overlap. No additional UI-level
exclusivity logic needed — valid settings already make them mutually
exclusive by construction.

**skipped_beat / category list decluttering** — already correctly
implemented in the current app (`call_skipped_beat_multiple`'s RR-vs-local-
average threshold, and `categorize_beat_arrhythmias`'s per-beat filtering to
only categories the beat actually triggered). Carried forward as a hard
requirement: the assignable-category list for a beat only ever shows
categories that fired for *that* beat.

**Reject cascades** — rejecting a beat's arrhythmia flag must clear/hide
*all* of its sub-category annotations, not just the top-level flag (gap in
the current app: sub-category flags can survive a rejection and keep
showing as assignable).

**Bulk upload** — supports both multi-file selection and whole-folder
import (recursively picks up every recognized file type in the folder).
Per-file loading/processing status shown throughout.

**Bad data marking** — single click-and-drag directly on the graph to paint
a range, in either direction; the saved start/stop is always auto-sorted
(min/max of the drag) regardless of drag direction. Range highlights live
during the drag for feedback.

**Removed from the old UI**: manual Y-axis min/max fields, manual X-window-
width field, the `<<<`/`>>>`/first/last arrhythmia seek buttons — all
superseded by free-form graph pan/zoom (from the base architecture doc) and
a single "reset view" button.

**Auto beat detection on load** — shown at import time as a tickbox
("Auto-run beat detection"), checked by default; the technician can
uncheck it per import batch to load files without running detection
immediately. When enabled, detection runs against each file's *default*
channel (from the pattern config / remembered choice) as soon as the file
is ready — no manual button press required. If the technician then changes
the channel afterward, detection re-runs against the newly selected channel.
**Out of scope for now**: "focus on beat of interest" — explicitly deferred
until there's a clear definition for what that means.

**Beat detection progress & cancel** — since detection is one vectorized
scipy call per file (no natural midpoint to report real progress from),
progress is shown as an **approximate ETA**, estimated from file size
against a running average of prior throughput (bytes/sec) captured on this
machine — refines itself as more files are processed, starts from a
reasonable hardcoded guess before any history exists. A "Stop" control
cancels the *batch*: the file currently being processed is allowed to
finish (it's a fast in-memory computation even on large files — expected
seconds, not minutes), and every file still queued behind it is dropped.
No partial/half-processed file results are ever kept.

**Confirm Arrhythmia bug** — the old app's confirm button doesn't reliably
work; the rebuild must get this right and have a test covering it (part of
overall verification before this ships, not a separate design decision).

## 2. Feature backlog (ToDo)

- [ ] Bulk file upload: multi-select + folder import, per-file status feedback
- [ ] Auto-run beat detection tickbox at import (checked by default, togglable)
- [ ] Beat detection ETA estimate (file-size-based, learns from run history) + batch Stop control
- [ ] Channel default-selection via editable pattern config, with manual override dropdown
- [ ] Remember last-used channel per file
- [ ] Annotation/analysis persistence (local DB), auto-load on reopen
- [ ] Free-form graph pan/zoom (from base architecture doc) + "reset view" button
- [ ] Remove Y-axis min/max and X-window-width manual inputs
- [ ] Remove old seek buttons (`<<<`/`>>>`/first/last arrhythmia)
- [ ] Per-beat category list shows only categories that fired for that beat
- [ ] Reassign or remove a beat's category classification after the fact
- [ ] Rejecting an arrhythmia clears all its sub-category flags
- [ ] Validate bradycardia/tachycardia settings (reject overlapping thresholds)
- [ ] Fix Confirm Arrhythmia (and cover it with a test)
- [ ] Bad data marking via click-drag, auto-sorted range, live highlight

## 3. App flow

1. **Launch** — app starts, local annotation DB opens/initializes.
2. **Import** — technician multi-selects files or imports a folder, with
   "Auto-run beat detection" checked by default (can uncheck before
   importing); each file gets a status (queued → loading → ready/error).
3. **Per file**:
   - If this exact file (path+size+mtime) has a prior DB record → load its
     saved channel, beats, annotations, bad-data marks, settings. Skip
     straight to review (auto-run is skipped for this file — it already
     has results).
   - Else → determine default channel via the filename-pattern config
     (user can override via dropdown). If auto-run is enabled, beat
     detection kicks off immediately with an ETA shown (file-size-based
     estimate) and a batch Stop control; otherwise the file sits ready,
     waiting for a manual trigger. Re-selecting a different channel
     re-runs detection against it.
4. **Review** — technician pans/zooms the graph freely; for each flagged
   beat, sees only its relevant category options, can confirm, reject
   (cascading clear), or reassign; can mark bad-data ranges by drag-select;
   can rerun arrhythmia analysis (heuristic/unsupervised/both) with
   progress feedback.
5. **Autosave** — review state persists continuously to the local DB, not
   only at export time, so work survives an app close/reopen.
6. **Export** — generate the Excel report (beats, bad-data marks, settings)
   as today.
7. **Reopen later** — same file → step 3's "prior record found" path, no
   re-annotation from scratch.
