from fastapi.testclient import TestClient

from backend import db
from backend.app import create_app


def test_no_prior_record(tmp_path, example_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [example_txt_file]})

    response = client.get("/files/state", params={"path": example_txt_file})

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["found"] is False
    assert result["channel"] is None
    assert result["beats"] == []
    assert result["bad_data_marks"] == []


def test_file_never_imported(tmp_path, example_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))

    response = client.get("/files/state", params={"path": example_txt_file})

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]


def test_prior_record_channel_only(tmp_path, example_txt_file):
    db_path = str(tmp_path / "test.db")
    client = TestClient(create_app(db_path=db_path))
    import_response = client.post(
        "/files/import", json={"paths": [example_txt_file]}
    )
    imported = import_response.json()["results"][0]

    conn = db.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO files (path, size, mtime, channel, beat_settings_json, "
            "arrhythmia_settings_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                example_txt_file,
                imported["size"],
                imported["modified_time"],
                "channel 1",
                None,
                None,
                "2026-09-28T00:00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    response = client.get("/files/state", params={"path": example_txt_file})

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["found"] is True
    assert result["channel"] == "channel 1"
    assert result["beats"] == []
    assert result["bad_data_marks"] == []
    assert result["beat_settings"] is None
    assert result["arrhythmia_settings"] is None
