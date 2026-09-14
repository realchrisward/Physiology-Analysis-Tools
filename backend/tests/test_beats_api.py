import pytest
from fastapi.testclient import TestClient

from backend.app import create_app


def test_real_beats_detected(real_beats_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [real_beats_txt_file]})

    response = client.post(
        "/beats/detect",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["count"] == 15
    assert len(result["beats"]) == 15
    assert result["mean_hr"] == pytest.approx(650.96, abs=0.01)
    assert result["duration"] == pytest.approx(1.435, abs=0.001)
    assert result["elapsed_seconds"] > 0
    assert result["file_size_bytes"] == 45877

    first_beat = result["beats"][0]
    assert first_beat["ts"] == pytest.approx(0.0855, abs=1e-4)
    assert first_beat["rr"] == pytest.approx(0.069, abs=1e-3)
    assert first_beat["r_amplitude"] == pytest.approx(0.508387, abs=1e-5)
    assert first_beat["hr"] == pytest.approx(869.565217, abs=1e-4)


def test_successful_run_zero_beats_is_not_an_error(example_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [example_txt_file]})

    response = client.post(
        "/beats/detect",
        json={"path": example_txt_file, "channel": "channel 1"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["count"] == 0
    assert result["beats"] == []
    assert result["mean_hr"] is None
    assert result["duration"] is None


def test_file_never_imported_reports_error_not_crash():
    client = TestClient(create_app())

    response = client.post(
        "/beats/detect",
        json={"path": "/nonexistent/path/never_imported.txt", "channel": "channel 1"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]


def test_unknown_channel_reports_error_not_crash(real_beats_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [real_beats_txt_file]})

    response = client.post(
        "/beats/detect",
        json={"path": real_beats_txt_file, "channel": "not_a_real_channel"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]
