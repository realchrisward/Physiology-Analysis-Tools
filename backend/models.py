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
    window_size: int
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


class BeatWindowResult(BaseModel):
    status: str
    beats: list[WindowBeat] = []
    count: int = 0
    error: str | None = None


class ChannelWindowResult(BaseModel):
    status: str
    x: list[float] = []
    y: list[float] = []
    point_count: int = 0
    downsampled: bool = False
    error: str | None = None
