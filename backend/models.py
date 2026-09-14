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
