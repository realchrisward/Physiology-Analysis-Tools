import pytest
from fastapi.testclient import TestClient

from backend.app import create_app


def test_put_channel_persists_and_state_reflects_it(tmp_path, real_beats_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [real_beats_txt_file]})

    response = client.put(
        "/files/channel",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["channel"] == "channel 1"

    state_response = client.get(
        "/files/state", params={"path": real_beats_txt_file}
    )
    state = state_response.json()
    assert state["found"] is True
    assert state["channel"] == "channel 1"
    assert state["beats"] == []


def test_put_channel_on_never_imported_file(tmp_path, real_beats_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))

    response = client.put(
        "/files/channel",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]


def test_post_beats_persists_beat_detection_only_result(tmp_path, real_beats_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [real_beats_txt_file]})
    client.post(
        "/beats/detect",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )

    response = client.post(
        "/files/beats",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["count"] == 15

    state_response = client.get(
        "/files/state", params={"path": real_beats_txt_file}
    )
    state = state_response.json()
    assert state["found"] is True
    assert len(state["beats"]) == 15

    optional_columns = [
        "any_arrhythmia",
        "other_arrhythmia",
        "bradycardia_absolute",
        "tachycardia_absolute",
        "skipped_beat",
        "prem_beat",
        "abn_cluster",
    ]
    for beat in state["beats"]:
        assert beat["review_state"] == "unreviewed"
        assert beat["reassigned_category"] is None
        for col in optional_columns:
            assert beat[col] is None

    first_beat = state["beats"][0]
    assert first_beat["ts"] == pytest.approx(0.0855, abs=1e-4)


def test_post_beats_without_beat_detection_reports_error(
    tmp_path, real_beats_txt_file
):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [real_beats_txt_file]})

    response = client.post(
        "/files/beats",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"] == "Beat detection has not been run for this file yet"


def test_post_beats_is_idempotent_wholesale_replace(tmp_path, real_beats_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [real_beats_txt_file]})
    client.post(
        "/beats/detect",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )

    client.post(
        "/files/beats",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )
    response = client.post(
        "/files/beats",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["count"] == 15

    state_response = client.get(
        "/files/state", params={"path": real_beats_txt_file}
    )
    state = state_response.json()
    assert len(state["beats"]) == 15


def test_post_beats_after_arrhythmia_detection_round_trips_categories(
    tmp_path, real_beats_txt_file
):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [real_beats_txt_file]})
    client.post(
        "/beats/detect",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )
    client.post(
        "/arrhythmia/detect",
        json={
            "path": real_beats_txt_file,
            "channel": "channel 1",
            "method": "heuristic",
        },
    )

    response = client.post(
        "/files/beats",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["count"] == 15

    state_response = client.get(
        "/files/state", params={"path": real_beats_txt_file}
    )
    state = state_response.json()
    first_beat = state["beats"][0]
    assert first_beat["ts"] == pytest.approx(0.0855, abs=1e-4)
    assert first_beat["tachycardia_absolute"] is True
    assert first_beat["any_arrhythmia"] is True
