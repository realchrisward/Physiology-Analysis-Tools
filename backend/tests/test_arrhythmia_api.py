import pytest
from fastapi.testclient import TestClient

from backend.app import create_app


def test_heuristic_method_real_per_beat_results(real_beats_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [real_beats_txt_file]})
    client.post(
        "/beats/detect",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )

    response = client.post(
        "/arrhythmia/detect",
        json={
            "path": real_beats_txt_file,
            "channel": "channel 1",
            "method": "heuristic",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["count"] == 15

    first_beat = result["beats"][0]
    assert first_beat["ts"] == pytest.approx(0.0855, abs=1e-4)
    assert first_beat["tachycardia_absolute"] is True
    assert first_beat["any_arrhythmia"] is True
    assert first_beat["abn_cluster"] is None

    second_beat = result["beats"][1]
    assert second_beat["ts"] == pytest.approx(0.21, abs=1e-2)
    assert second_beat["bradycardia_absolute"] is False
    assert second_beat["tachycardia_absolute"] is False
    assert second_beat["skipped_beat"] is False
    assert second_beat["prem_beat"] is False
    assert second_beat["any_arrhythmia"] is False
    assert second_beat["other_arrhythmia"] is False
    assert second_beat["abn_cluster"] is None

    for beat in result["beats"]:
        assert beat["abn_cluster"] is None

    assert result["any_arrhythmia_count"] == 5


def test_both_methods_on_larger_real_file(long_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [long_txt_file]})
    client.post(
        "/beats/detect",
        json={"path": long_txt_file, "channel": "channel 1"},
    )

    response = client.post(
        "/arrhythmia/detect",
        json={"path": long_txt_file, "channel": "channel 1", "method": "both"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["count"] == 163

    assert any(beat["abn_cluster"] is not None for beat in result["beats"])

    assert result["any_arrhythmia_count"] == 163


def test_beats_not_detected_yet_reports_error(real_beats_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [real_beats_txt_file]})

    response = client.post(
        "/arrhythmia/detect",
        json={
            "path": real_beats_txt_file,
            "channel": "channel 1",
            "method": "heuristic",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]
    assert "beat detection" in result["error"].lower()


def test_unrecognized_method_reports_error(real_beats_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [real_beats_txt_file]})
    client.post(
        "/beats/detect",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )

    response = client.post(
        "/arrhythmia/detect",
        json={
            "path": real_beats_txt_file,
            "channel": "channel 1",
            "method": "not_a_real_method",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]
