from typing import Any

from pydantic import BaseModel


class ImportRequest(BaseModel):
    paths: list[str]


class FileImportResult(BaseModel):
    path: str
    filename: str
    status: str
    channels: list[str] = []
    time_column: str | None = None
    size: int | None = None
    modified_time: float | None = None
    default_channel: str | None = None
    default_channel_matched_rule: bool = False
    error: str | None = None


class ImportResponse(BaseModel):
    results: list[FileImportResult]


class BeatDetectRequest(BaseModel):
    path: str
    channel: str


class Beat(BaseModel):
    ts: float
    rr: float
    r_amplitude: float
    hr: float


class BeatDetectionResult(BaseModel):
    status: str
    beats: list[Beat] = []
    count: int = 0
    mean_hr: float | None = None
    duration: float | None = None
    elapsed_seconds: float = 0.0
    file_size_bytes: int | None = None
    error: str | None = None


class BeatSettingsModel(BaseModel):
    min_RR: int
    ecg_invert: bool
    auto_detect_invert: bool
    ecg_filter: bool
    ecg_filt_order: int
    ecg_filt_cutoff: int
    abs_thresh: float | None
    perc_thresh: int | None


class ArrhythmiaSettingsModel(BaseModel):
    bradycardia_absolute_hr: int
    tachycardia_absolute_hr: int
    skipped_beat_multiple_rr: float
    premature_beat_multiple_rr: float
    beat_window: float
    beat_window_bias: float
    beat_length: int
    kde_bandwidth: float
    min_rr: float
    max_rr: float
    eps_auto: bool
    eps_percentile: int
    eps: float
    min_samples: int


class ArrhythmiaDetectRequest(BaseModel):
    path: str
    channel: str
    method: str


class ArrhythmiaBeat(BaseModel):
    ts: float
    bradycardia_absolute: bool | None = None
    tachycardia_absolute: bool | None = None
    skipped_beat: bool | None = None
    prem_beat: bool | None = None
    abn_cluster: bool | None = None
    any_arrhythmia: bool
    other_arrhythmia: bool


class ArrhythmiaDetectionResult(BaseModel):
    status: str
    beats: list[ArrhythmiaBeat] = []
    count: int = 0
    any_arrhythmia_count: int = 0
    elapsed_seconds: float = 0.0
    error: str | None = None


class SettingsPayload(BaseModel):
    beat: BeatSettingsModel
    arrhythmia: ArrhythmiaSettingsModel


class SettingsResult(BaseModel):
    status: str
    settings: SettingsPayload | None = None
    error: str | None = None


class WindowBeat(BaseModel):
    ts: float
    rr: float
    r_amplitude: float
    hr: float
    manual_added_at: str | None = None
    bradycardia_absolute: bool | None = None
    tachycardia_absolute: bool | None = None
    skipped_beat: bool | None = None
    prem_beat: bool | None = None
    abn_cluster: bool | None = None
    any_arrhythmia: bool | None = None
    other_arrhythmia: bool | None = None
    review_state: str = "unreviewed"
    reassigned_category: str | None = None
    # The reviewer was unsure. Only ever True on a confirmed or rejected beat.
    uncertain: bool = False


class BeatWindowResult(BaseModel):
    status: str
    beats: list[WindowBeat] = []
    count: int = 0
    error: str | None = None


class BeatsOfInterestResult(BaseModel):
    """Just the timestamps of every currently-flagged beat, in time order —
    what the graph's beat-of-interest navigation steps through. Deliberately
    not the full beat rows: the caller needs the whole file's worth of these
    at once (unlike /beats/window, which is viewport-scoped)."""

    status: str
    ts: list[float] = []
    count: int = 0
    # The recording's typical beat-to-beat interval in seconds (dominant RR
    # mode, not clipped to any species range), so the graph can size its
    # focus view to a number of beats. None when there are too few beats.
    typical_rr: float | None = None
    error: str | None = None


class ChannelWindowResult(BaseModel):
    status: str
    x: list[float] = []
    y: list[float] = []
    point_count: int = 0
    downsampled: bool = False
    error: str | None = None


class PersistedBeat(BaseModel):
    ts: float
    rr: float
    r_amplitude: float
    hr: float
    manual_added_at: str | None = None
    bradycardia_absolute: bool | None = None
    tachycardia_absolute: bool | None = None
    skipped_beat: bool | None = None
    prem_beat: bool | None = None
    abn_cluster: bool | None = None
    any_arrhythmia: bool | None = None
    other_arrhythmia: bool | None = None
    review_state: str = "unreviewed"
    reassigned_category: str | None = None
    uncertain: bool = False


class BadDataMark(BaseModel):
    id: int
    start: float
    stop: float


class FileStateResult(BaseModel):
    status: str
    found: bool = False
    channel: str | None = None
    beats: list[PersistedBeat] = []
    bad_data_marks: list[BadDataMark] = []
    beat_settings: BeatSettingsModel | None = None
    arrhythmia_settings: ArrhythmiaSettingsModel | None = None
    error: str | None = None


class ChannelPersistRequest(BaseModel):
    path: str
    channel: str


class ChannelPersistResult(BaseModel):
    status: str
    channel: str | None = None
    error: str | None = None


class PersistBeatsRequest(BaseModel):
    path: str
    channel: str


class PersistBeatsResult(BaseModel):
    status: str
    count: int = 0
    error: str | None = None


class BadDataAddRequest(BaseModel):
    path: str
    start: float
    stop: float


class BadDataAddResult(BaseModel):
    status: str
    mark: BadDataMark | None = None
    error: str | None = None


class BadDataUpdateRequest(BaseModel):
    path: str
    id: int
    start: float
    stop: float


class BadDataUpdateResult(BaseModel):
    status: str
    mark: BadDataMark | None = None
    error: str | None = None


class BadDataDeleteRequest(BaseModel):
    path: str
    id: int


class BadDataDeleteResult(BaseModel):
    status: str
    error: str | None = None


class BeatDeleteRequest(BaseModel):
    path: str
    ts: float


class BeatDeleteResult(BaseModel):
    status: str
    error: str | None = None


class BeatSnapRequest(BaseModel):
    path: str
    channel: str
    ts: float


class BeatSnapResult(BaseModel):
    status: str
    ts: float | None = None
    error: str | None = None


class BeatAddRequest(BaseModel):
    path: str
    channel: str
    ts: float


class BeatAddResult(BaseModel):
    status: str
    beat: WindowBeat | None = None
    error: str | None = None


class DiscardStateRequest(BaseModel):
    path: str


class DiscardStateResult(BaseModel):
    status: str
    error: str | None = None


class DatabaseResetResult(BaseModel):
    status: str
    error: str | None = None


class DatabaseInfoResult(BaseModel):
    status: str
    path: str | None = None
    directory: str | None = None
    size_bytes: int | None = None
    file_count: int | None = None
    beat_count: int | None = None
    error: str | None = None


class CategoryUpdateRequest(BaseModel):
    path: str
    ts: float
    # confirm | reject | reassign | remove_flag | set_uncertain
    action: str
    category: str | None = None
    # The "not sure" mark. With confirm/reject/reassign it is saved together
    # with the decision (None keeps the beat's current value); set_uncertain
    # requires it and only works on an already confirmed/rejected beat.
    uncertain: bool | None = None


class CategoryUpdateResult(BaseModel):
    status: str
    ts: float | None = None
    review_state: str | None = None
    reassigned_category: str | None = None
    # The beat's mark after the action.
    uncertain: bool | None = None
    error: str | None = None


class ReportRequest(BaseModel):
    path: str
    # The full destination file path (directory + filename), chosen by the
    # technician via a native Save As dialog — not just a directory with a
    # server-picked filename, so they can rename the export freely.
    output_path: str


class ReportResult(BaseModel):
    status: str
    output_path: str | None = None
    error: str | None = None


class FlagListParseRequest(BaseModel):
    path: str


class FlagListRow(BaseModel):
    # A recording's file name without extension, always a string (leading
    # zeros preserved).
    animal_id: str
    ts: float
    # "fp" | "fn" | "flag" ("flag" = the list didn't say which).
    kind: str
    prediction: float | None = None
    ground_truth: int | None = None


class FlagListParseResult(BaseModel):
    status: str
    error: str | None = None
    filename: str | None = None
    rows: list[FlagListRow] = []
    total_rows: int = 0
    # Rows dropped for a blank animal ID or an unreadable timestamp.
    skipped_rows: int = 0


class FlagListMatchRequest(BaseModel):
    animal_ids: list[str]
    folders: list[str]


class FlagListMatch(BaseModel):
    animal_id: str
    paths: list[str]


class FlagListMatchResult(BaseModel):
    status: str
    error: str | None = None
    matches: list[FlagListMatch] = []
    missing: list[str] = []
    missing_folders: list[str] = []
    scanned_files: int = 0
    # Sub-folders that could not be read (permissions, broken links), so
    # recordings inside them can't have been found.
    skipped_dirs: int = 0
    # The scan stopped at its file/time limit before covering every folder.
    truncated: bool = False


class EvictRequest(BaseModel):
    paths: list[str]


class EvictResult(BaseModel):
    status: str
    evicted: int = 0
    error: str | None = None


# ---- Report labels: an xlsx report (e.g. from the original PySide app, or
# edited by hand) whose per-beat labels are shown over the open recording.
# Labels: 2 = confirmed arrhythmia, 1 = detected but not confirmed/rejected,
# 0 = not detected (normal), -1 = rejected (normal). See backend/reports.py.


class ReportLoadRequest(BaseModel):
    recording_path: str
    report_path: str


class ReportUnloadRequest(BaseModel):
    recording_path: str


class ReportSettingDifference(BaseModel):
    name: str
    report_value: str
    our_value: str


class ReportBadDataRange(BaseModel):
    start: float
    stop: float


class ReportSummary(BaseModel):
    report_path: str
    filename: str
    sheet: str
    label_column: str
    # Per-category flag columns found (e.g. annot_prem_beat), in sheet order.
    category_columns: list[str] = []
    total_beats: int = 0
    # Keyed "2", "1", "0", "-1".
    counts: dict[str, int] = {}
    ts_start: float | None = None
    ts_end: float | None = None
    # The report's file name (minus extension) equals the recording's.
    name_matches_recording: bool = True
    # The report's settings sheet (first row), values as strings.
    settings: dict[str, str] = {}
    # Detection settings where the report differs from the current ones.
    settings_differences: list[ReportSettingDifference] = []
    bad_data_marks: list[ReportBadDataRange] = []
    # Rows dropped (blank/unreadable ts or label), duplicate ts, etc.
    skipped_rows: int = 0
    warnings: list[str] = []


class ReportLoadResult(BaseModel):
    status: str
    error: str | None = None
    # None when no report is loaded for the recording (GET /reports/state).
    summary: ReportSummary | None = None


class ReportUnloadResult(BaseModel):
    status: str
    error: str | None = None


class ReportBeat(BaseModel):
    ts: float
    # 2, 1, 0 or -1.
    label: int
    # Categories that flagged this beat in the report (annot_<category> != 0,
    # excluding any_arrhythmia), e.g. ["prem_beat"].
    categories: list[str] = []
    # 'exact' | 'near' (within tolerance) | 'none' (no beat of ours there)
    # | 'unknown' (our beats aren't detected yet).
    match: str
    our_ts: float | None = None
    # 'agree' | 'report_only' (report: arrhythmia, ours: normal/rejected)
    # | 'ours_only' (ours: confirmed, report: normal/rejected)
    # | 'pending' (either side detected-but-unreviewed) | 'unmatched'
    # | 'unknown' (we have no decision yet: our beats aren't detected, or carry
    # no arrhythmia flags and this beat isn't confirmed/rejected).
    agreement: str


class ReportWindowResult(BaseModel):
    status: str
    error: str | None = None
    beats: list[ReportBeat] = []


class ReportNavigationResult(BaseModel):
    status: str
    error: str | None = None
    # Ascending ts per filter: 'arrhythmia' (label 2), 'unreviewed' (1),
    # 'rejected' (-1), 'disagreements' (report_only/ours_only), 'not_detected'
    # (match none).
    targets: dict[str, list[float]] = {}
    # Report beats matched to one of our beats (exact or near).
    matched: int = 0
    total: int = 0
    # Our beats with no report beat within tolerance.
    extra_ours: int = 0
    # False until our beats exist (detection not run yet).
    has_our_beats: bool = False
    # False while our beats carry no arrhythmia flags (arrhythmia detection
    # not run, or a restored recording whose flags are empty): a beat we
    # haven't reviewed then has no decision, so its agreement is 'unknown'.
    has_our_flags: bool = False


class ReportBeatResult(BaseModel):
    status: str
    error: str | None = None
    # The report beat nearest the given ts within tolerance, if any.
    beat: ReportBeat | None = None


class ReportCandidatesRequest(BaseModel):
    recording_path: str
    folders: list[str] = []


class ReportCandidatesResult(BaseModel):
    status: str
    error: str | None = None
    paths: list[str] = []
