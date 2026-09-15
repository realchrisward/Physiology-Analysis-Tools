import json

from fastapi import APIRouter, Request

from backend import db
from backend.models import (
    ArrhythmiaSettingsModel,
    BadDataMark,
    BeatSettingsModel,
    ChannelPersistRequest,
    ChannelPersistResult,
    FileStateResult,
    PersistBeatsRequest,
    PersistBeatsResult,
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


@router.put("/channel", response_model=ChannelPersistResult)
def put_channel(
    payload: ChannelPersistRequest, request: Request
) -> ChannelPersistResult:
    imported = request.app.state.imported_files.get(payload.path)
    if imported is None or imported.status != "ok":
        return ChannelPersistResult(
            status="error", error=f"File not imported: {payload.path}"
        )

    conn = db.connect(request.app.state.db_path)
    try:
        db.upsert_file(
            conn,
            payload.path,
            imported.size,
            imported.modified_time,
            channel=payload.channel,
        )
        return ChannelPersistResult(status="ok", channel=payload.channel)
    except Exception as e:
        return ChannelPersistResult(status="error", error=str(e))
    finally:
        conn.close()


@router.post("/beats", response_model=PersistBeatsResult)
def persist_beats(
    payload: PersistBeatsRequest, request: Request
) -> PersistBeatsResult:
    imported = request.app.state.imported_files.get(payload.path)
    if imported is None or imported.status != "ok":
        return PersistBeatsResult(
            status="error", error=f"File not imported: {payload.path}"
        )

    beat_df = request.app.state.beat_cache.get(payload.path)
    if beat_df is None:
        return PersistBeatsResult(
            status="error", error="Beat detection has not been run for this file yet"
        )

    conn = db.connect(request.app.state.db_path)
    try:
        file_id = db.upsert_file(
            conn,
            payload.path,
            imported.size,
            imported.modified_time,
            channel=payload.channel,
            beat_settings_json=json.dumps(request.app.state.beat_settings.__dict__),
            arrhythmia_settings_json=json.dumps(
                request.app.state.arrhythmia_settings.__dict__
            ),
        )
        db.replace_beats(conn, file_id, beat_df)
        return PersistBeatsResult(status="ok", count=len(beat_df))
    except Exception as e:
        return PersistBeatsResult(status="error", error=str(e))
    finally:
        conn.close()
