# GitHub Issues — Status Cross-Reference

Source: `gh issue list --repo realchrisward/Physiology-Analysis-Tools --state all` (32 issues, #1–#47, fetched live from the upstream repo this project was cloned from).

This cross-references the upstream issue tracker against (a) what the upstream maintainer already fixed before we cloned, and (b) what we fixed in this session (see `changes_made.md` for full technical detail on our changes).

---

## 1. Fixed by us this session

| Issue | Title | Status on GitHub | What we did |
|---|---|---|---|
| [#1](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/1) | tool for loading EDF files | OPEN | Registered the existing (but dormant) `edf_extract.py` in `main.py`'s extractor list, added the missing `SASSI_extract()` wrapper it needed, and added `pyedflib` as a declared dependency. **Partially resolves this issue**: the existing extractor already handled the two sub-asks called out in the issue body — channel-name collisions ("multiple channels in an EDF can have similar names") via `f"{i} - {label}"` indexed naming, and differing per-channel sample frequencies via a common resampled timeline. What's still missing (not done by us): a "scan input file to define signal contents" preview step before full load. |
| [#37](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/37) | add status messages to status window | OPEN | Directly implemented: added `MainWindow.log_status()` and wired it into file loading, beat detection, arrhythmia analysis, report generation, and error paths, all writing to `textBrowser_Status`. |
| [#18](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/18) | next milestone checklist | OPEN (partially checked) | Resolves the remaining unchecked box **"either populate text window or remove it"** — we populated it (see #37 above). The other unchecked box, "permit loading of report", is still not implemented. |
| [#44](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/44) | User Feedback summary - spin off issues as needed | OPEN | Two of the eight bullet points addressed: **"I want confirmation that the report was generated... make the ui more responsive"** — report generation now logs "Report saved: \<path\>" via status messages, and arrhythmia analysis (the slowest operation, previously freezing the UI for 10+ seconds) now runs on a background `QThread` with a progress dialog instead of blocking the UI thread. The other six bullet points (beat detection quality on low-amplitude R waves, manual beat addition tool, bad-data-marking refinement, wider viewing window, post-breath false positives, steady-vs-transient bradycardia category, performance with many loaded files) are **not** addressed — see section 3. |

## 2. Already fixed upstream (before we cloned / started this session — not our work)

Confirmed via `git log` on the local clone; listed here so it's clear these are pre-existing, not something we need to re-do:

| Issue | Title | Notes |
|---|---|---|
| [#41](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/41) (closed) | scroll bar in 0.0.17 is not full width | Matches local commits `bdb4597` "update to scroll bar width" / `c80aaf6` "tweak to ui width". |
| [#23](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/23) (closed) | beat detection and arrhythmia algorithm improvements | Matches local commit `b8028a9` "update beat detection with relative peak amplitude" — this is the breathing-artifact relative-amplitude filter documented in `interview_prep.md`. |
| [#36](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/36) (closed) | add tab to output indicating settings used for beat/arrhyth marks | Matches local commit `4a51b89` "added settings tab to output". |
| [#24](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/24) (closed) | FEATURE REQUEST - settings menu | Settings dialog already existed; we improved it further this session (tooltips, grouping, Restore Defaults, bugfix — see `changes_made.md` item 7), but the base feature predates us. |
| [#33](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/33) (closed) | transition from pyqt6 to pyside6 | Already done — codebase is PySide6-only. |
| [#20](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/20), [#12](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/12), [#11](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/11), [#8](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/8), [#6](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/6), [#14](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/14), [#38](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/38) (all closed) | various early bugs/setup | Windows install instructions, file-list tracking bug, GUI control wiring, t=0 plotting bug, trace viewer, example files folder, scrollbar snapping — all closed upstream, no local action needed. |

## 3. Identified, real, and still open — recommended for future work

Cross-referencing our own bug review (`interview_prep.md`) against the live issue tracker turned up matching **open** upstream issues for problems we found but deliberately left unfixed this session (out of the explicit scope given):

| Priority | Issue | Title | Why it matters / what we found |
|---|---|---|---|
| High | [#40](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/40) / [#13](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/13) | ECG beat detection performs poorly in some files / algorithm needs improvement | Confirmed contributing cause in code: `gather_data()`'s stride-based downsampling (`x_val[::downsample_factor]`) can skip transient spikes/arrhythmic beats entirely when zoomed out (our "Bug 4" from `interview_prep.md`). Fix: min-max downsampling (Plan Item 7, not implemented this session). Also flagged upstream: poor performance on low-amplitude R waves and inverted/shifted-baseline signals (e.g. ecgenie data) — would need slope-based or absolute-value peak detection, not just percentile threshold. |
| High | [#44](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/44) (remaining bullets) | User Feedback summary | Still open: manual tool to add unmarked beats, refinement of bad-data marking, wider default viewing window, post-breath-artifact false positives, a "steady slow beat vs. acute transient slowing" category, and performance degradation with many loaded files. |
| Medium | [#18](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/18) (remaining box) | next milestone checklist | "permit loading of report" — no way to reload a previously saved/exported session; ties to the "Session Persistence" longer-term roadmap item we noted in `changes_made.md`. |
| Medium | [#43](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/43) | add more info to settings tab of output | Excel report's `settings` sheet doesn't currently record which signal channel or which arrhythmia method (Heuristic/Unsupervised/Both) was used for the run — easy addition to `action_generate_report()`. |
| Medium | [#42](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/42) | option to remove files from worklist as report is generated | Would help the "performance drops with many files" complaint in #44 by shrinking the in-memory worklist as files are completed. |
| Medium | [#32](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/32) | unsupervised arrhythmia detection - ignore clusters based on library of examples | Real usability gap: breathing/EMG artifacts reliably form their own DBSCAN cluster and get flagged as arrhythmia with no way to mark that cluster shape as "known artifact, ignore" for future runs. |
| Low | [#45](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/45) | transition from pandas to polars | Performance/architecture proposal, no functional bug. |
| Low | [#25](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/25) | add vendor compatibility (EMKA) | Blocked on maintainer obtaining sample files / format spec from EMKA. |
| Roadmap | [#47](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/47), [#46](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/46), [#4](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/4), [#3](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/3), [#5](https://github.com/realchrisward/Physiology-Analysis-Tools/issues/5) | ML model roadmap, human-vs-tool performance review, blood pressure tool, EEG tool, breathing detection tool | Large net-new features/modalities, not bugs — consistent with the "planned but not implemented" features `interview_prep.md` already notes (EEG/EMG/blood pressure). |

## 4. Bugs we found in `interview_prep.md` with no corresponding GitHub issue

For completeness — these don't have an open upstream issue tracking them, so they'd need a new issue filed if not fixed:

- **Bug 9 / Plan Item 12** — Quality Scoring menu item is a stub (`action_Quality_Scoring` does nothing). No open GitHub issue references this by name.
- **Bug 10 / Plan Item 16** — No automated tests anywhere in the repo. No open GitHub issue.
- **Bug 12 / Plan Item 11** — Absolute-pixel UI layout (53 `geometry` properties vs. 1 layout manager) — window can't be resized. Not filed upstream, though #41 (scrollbar width, closed) was a symptom of the same underlying layout style.
- **Bug 8** (`FlexibleEntryWidget.getValues()` crash on string settings) — fixed by us this session; no matching GitHub issue existed (latent bug, no user had hit it yet since no setting is currently `str`-typed).
