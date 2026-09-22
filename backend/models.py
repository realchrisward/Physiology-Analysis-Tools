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
    bradycardia_absolute: bool | None = None
    tachycardia_absolute: bool | None = None
    skipped_beat: bool | None = None
    prem_beat: bool | None = None
    abn_cluster: bool | None = None
    any_arrhythmia: bool | None = None
    other_arrhythmia: bool | None = None
    review_state: str = "unreviewed"
    reassigned_category: str | None = None


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
    bradycardia_absolute: bool | None = None
    tachycardia_absolute: bool | None = None
    skipped_beat: bool | None = None
    prem_beat: bool | None = None
    abn_cluster: bool | None = None
    any_arrhythmia: bool | None = None
    other_arrhythmia: bool | None = None
    review_state: str = "unreviewed"
    reassigned_category: str | None = None


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


class BadDataDeleteRequest(BaseModel):
    path: str
    id: int


class BadDataDeleteResult(BaseModel):
    status: str
    error: str | None = None


class CategoryUpdateRequest(BaseModel):
    path: str
    ts: float
    action: str
    category: str | None = None


class CategoryUpdateResult(BaseModel):
    status: str
    ts: float | None = None
    review_state: str | None = None
    reassigned_category: str | None = None
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
