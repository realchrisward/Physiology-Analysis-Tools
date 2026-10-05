import os

from fastapi import APIRouter, Request
from physiology_analysis_tools.modules import arrhythmia_detection, heartbeat_detection

from backend import db
from backend.models import (
    ArrhythmiaSettingsModel,
    BeatSettingsModel,
    DatabaseInfoResult,
    DatabaseResetResult,
    SettingsPayload,
    SettingsResult,
)

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=SettingsPayload)
def get_settings(request: Request) -> SettingsPayload:
    return SettingsPayload(
        beat=BeatSettingsModel(**request.app.state.beat_settings.__dict__),
        arrhythmia=ArrhythmiaSettingsModel(
            **request.app.state.arrhythmia_settings.__dict__
        ),
    )


@router.get("/settings/defaults", response_model=SettingsPayload)
def get_default_settings() -> SettingsPayload:
    """Factory defaults, without touching the live settings — the Settings
    dialog's "Restore Defaults" fills its form from this and only applies
    them if the technician then saves."""
    return SettingsPayload(
        beat=BeatSettingsModel(**heartbeat_detection.Settings().__dict__),
        arrhythmia=ArrhythmiaSettingsModel(**arrhythmia_detection.Settings().__dict__),
    )


@router.put("/settings", response_model=SettingsResult)
def put_settings(payload: SettingsPayload, request: Request) -> SettingsResult:
    if (
        payload.arrhythmia.bradycardia_absolute_hr
        >= payload.arrhythmia.tachycardia_absolute_hr
    ):
        return SettingsResult(
            status="error",
            error="bradycardia_absolute_hr must be less than tachycardia_absolute_hr",
        )

    beat_settings = heartbeat_detection.Settings()
    beat_settings.__dict__.update(payload.beat.model_dump())
    arrhythmia_settings = arrhythmia_detection.Settings()
    arrhythmia_settings.__dict__.update(payload.arrhythmia.model_dump())

    request.app.state.beat_settings = beat_settings
    request.app.state.arrhythmia_settings = arrhythmia_settings

    return SettingsResult(
        status="ok",
        settings=SettingsPayload(
            beat=BeatSettingsModel(**beat_settings.__dict__),
            arrhythmia=ArrhythmiaSettingsModel(**arrhythmia_settings.__dict__),
        ),
    )


@router.get("/settings/database", response_model=DatabaseInfoResult)
def get_database_info(request: Request) -> DatabaseInfoResult:
    """Where the review database lives and roughly what's in it, for the
    Settings dialog's Database tab - the folder is shown rather than just
    the bare file so a technician can go look (or back it up) directly."""
    db_path = request.app.state.db_path
    try:
        size_bytes = os.path.getsize(db_path) if os.path.exists(db_path) else 0
        conn = db.connect(db_path)
        try:
            file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            beat_count = conn.execute("SELECT COUNT(*) FROM beats").fetchone()[0]
        finally:
            conn.close()
        return DatabaseInfoResult(
            status="ok",
            path=db_path,
            directory=os.path.dirname(db_path),
            size_bytes=size_bytes,
            file_count=file_count,
            beat_count=beat_count,
        )
    except OSError as e:
        return DatabaseInfoResult(status="error", error=str(e))


@router.delete("/settings/database", response_model=DatabaseResetResult)
def reset_database(request: Request) -> DatabaseResetResult:
    """Deletes the on-disk SQLite file outright - every file's beats, review
    decisions, manual edits, and bad-data marks - then immediately recreates
    an empty one with the current schema, so the app is never left pointing
    at a missing file.

    Also drops the in-memory beat/typical-RR caches: `beat_cache` holds each
    file's review state synced down from the row this just deleted (see
    `db.replace_beats`'s own docstring), so leaving it in place would keep
    serving now-discarded review decisions until the process restarts.
    """
    db_path = request.app.state.db_path
    try:
        # SQLite's WAL mode can leave -wal/-shm sidecar files alongside the
        # main one; removing only the main file would leave stale pages in
        # those behind for a future connection to (confusingly) replay.
        for candidate in (db_path, f"{db_path}-wal", f"{db_path}-shm"):
            if os.path.exists(candidate):
                os.remove(candidate)
        db.connect(db_path).close()
        request.app.state.beat_cache.clear()
        request.app.state.typical_rr_cache.clear()
        return DatabaseResetResult(status="ok")
    except OSError as e:
        return DatabaseResetResult(status="error", error=str(e))
