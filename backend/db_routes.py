import json

from fastapi import APIRouter, Request

from backend import db
from backend.models import (
    ArrhythmiaSettingsModel,
    BadDataMark,
    BeatSettingsModel,
    FileStateResult,
    PersistedBeat,
)

router = APIRouter(prefix="/files", tags=["persistence"])


@router.get("/state", response_model=FileStateResult)
def get_file_state(path: str, request: Request) -> FileStateResult:
    imported = request.app.state.imported_files.get(path)
    if imported is None or imported.status != "ok":
        return FileStateResult(status="error", error=f"File not imported: {path}")

    conn = db.connect(request.app.state.db_path)
    try:
        row = db.get_file_row(conn, path, imported.size, imported.modified_time)
        if row is None:
            return FileStateResult(status="ok", found=False)

        beat_rows = conn.execute(
            "SELECT * FROM beats WHERE file_id=? ORDER BY ts",
            (row["id"],),
        ).fetchall()
        beats = [
            PersistedBeat(
                ts=beat_row["ts"],
                rr=beat_row["rr"],
                r_amplitude=beat_row["r_amplitude"],
                hr=beat_row["hr"],
                bradycardia_absolute=_to_bool(beat_row["bradycardia_absolute"]),
                tachycardia_absolute=_to_bool(beat_row["tachycardia_absolute"]),
                skipped_beat=_to_bool(beat_row["skipped_beat"]),
                prem_beat=_to_bool(beat_row["prem_beat"]),
                abn_cluster=_to_bool(beat_row["abn_cluster"]),
                any_arrhythmia=_to_bool(beat_row["any_arrhythmia"]),
                other_arrhythmia=_to_bool(beat_row["other_arrhythmia"]),
                review_state=beat_row["review_state"],
                reassigned_category=beat_row["reassigned_category"],
            )
            for beat_row in beat_rows
        ]

        mark_rows = conn.execute(
            "SELECT * FROM bad_data_marks WHERE file_id=? ORDER BY id",
            (row["id"],),
        ).fetchall()
        bad_data_marks = [
            BadDataMark(
                id=mark_row["id"], start=mark_row["start"], stop=mark_row["stop"]
            )
            for mark_row in mark_rows
        ]

        beat_settings = (
            BeatSettingsModel(**json.loads(row["beat_settings_json"]))
            if row["beat_settings_json"] is not None
            else None
        )
        arrhythmia_settings = (
            ArrhythmiaSettingsModel(**json.loads(row["arrhythmia_settings_json"]))
            if row["arrhythmia_settings_json"] is not None
            else None
        )

        return FileStateResult(
            status="ok",
            found=True,
            channel=row["channel"],
            beats=beats,
            bad_data_marks=bad_data_marks,
            beat_settings=beat_settings,
            arrhythmia_settings=arrhythmia_settings,
        )
    finally:
        conn.close()


def _to_bool(value: int | None) -> bool | None:
    return None if value is None else bool(value)
