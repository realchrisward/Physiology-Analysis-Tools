from fastapi import APIRouter, Request
from physiology_analysis_tools.modules import arrhythmia_detection, heartbeat_detection

from backend.models import (
    ArrhythmiaSettingsModel,
    BeatSettingsModel,
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

    return SettingsResult(status="ok", settings=payload)
