import pytest
from fastapi.testclient import TestClient

from backend.app import create_app


def test_filter_after_beat_detection_only(real_beats_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [real_beats_txt_file]})
    client.post(
        "/beats/detect",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )

    response = client.get(
        "/beats/window",
        params={"path": real_beats_txt_file, "start": 0, "end": 0.25},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["count"] == 2

    optional_columns = [
        "bradycardia_absolute",
        "tachycardia_absolute",
        "skipped_beat",
        "prem_beat",
        "abn_cluster",
        "any_arrhythmia",
        "other_arrhythmia",
    ]

    first_beat = result["beats"][0]
    assert first_beat["ts"] == pytest.approx(0.0855, abs=1e-4)
    assert first_beat["rr"] is not None
    assert first_beat["r_amplitude"] is not None
    assert first_beat["hr"] is not None
    for col in optional_columns:
        assert first_beat[col] is None

    second_beat = result["beats"][1]
    assert second_beat["ts"] == pytest.approx(0.21, abs=1e-2)
    for col in optional_columns:
        assert second_beat[col] is None


def test_filter_after_arrhythmia_detection(real_beats_txt_file):
    client = TestClient(create_app())
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

    response = client.get(
        "/beats/window",
        params={"path": real_beats_txt_file, "start": 0, "end": 0.25},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["count"] == 2

    first_beat = result["beats"][0]
    assert first_beat["ts"] == pytest.approx(0.0855, abs=1e-4)
    assert first_beat["tachycardia_absolute"] is True
    assert first_beat["any_arrhythmia"] is True

    second_beat = result["beats"][1]
    assert second_beat["ts"] == pytest.approx(0.21, abs=1e-2)
    assert second_beat["bradycardia_absolute"] is False
    assert second_beat["tachycardia_absolute"] is False
    assert second_beat["skipped_beat"] is False
    assert second_beat["prem_beat"] is False
    assert second_beat["any_arrhythmia"] is False
    assert second_beat["other_arrhythmia"] is False


def test_full_range_query_returns_every_beat(real_beats_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [real_beats_txt_file]})
    client.post(
        "/beats/detect",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )

    response = client.get(
        "/beats/window",
        params={"path": real_beats_txt_file, "start": 0, "end": 999},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["count"] == 15


def test_narrow_range_on_larger_file_after_both_methods(long_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [long_txt_file]})
    client.post(
        "/beats/detect",
        json={"path": long_txt_file, "channel": "channel 1"},
    )
    client.post(
        "/arrhythmia/detect",
        json={"path": long_txt_file, "channel": "channel 1", "method": "both"},
    )

    response = client.get(
        "/beats/window",
        params={"path": long_txt_file, "start": 0, "end": 3},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["count"] >= 5

    for beat in result["beats"]:
        assert 0 <= beat["ts"] <= 3
        assert beat["abn_cluster"] is not None


def test_empty_range_is_not_an_error(long_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [long_txt_file]})
    client.post(
        "/beats/detect",
        json={"path": long_txt_file, "channel": "channel 1"},
    )
    client.post(
        "/arrhythmia/detect",
        json={"path": long_txt_file, "channel": "channel 1", "method": "both"},
    )

    response = client.get(
        "/beats/window",
        params={"path": long_txt_file, "start": 200, "end": 300},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["count"] == 0
    assert result["beats"] == []


def test_beat_detection_never_run_reports_error(real_beats_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [real_beats_txt_file]})

    response = client.get(
        "/beats/window",
        params={"path": real_beats_txt_file, "start": 0, "end": 999},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"] == "Beat detection has not been run for this file yet"


def test_beats_of_interest_lists_flagged_beats_in_time_order(real_beats_txt_file):
    client = TestClient(create_app())
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

    response = client.get(
        "/beats/of-interest", params={"path": real_beats_txt_file}
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    # 57.txt flags 5 of its 15 beats under the default heuristic thresholds.
    assert result["count"] == 5
    assert len(result["ts"]) == 5
    assert result["ts"] == sorted(result["ts"])

    window = client.get(
        "/beats/window",
        params={"path": real_beats_txt_file, "start": 0, "end": 999},
    ).json()
    flagged_in_window = [b["ts"] for b in window["beats"] if b["any_arrhythmia"]]
    assert result["ts"] == pytest.approx(flagged_in_window)


def test_beats_of_interest_is_empty_before_arrhythmia_detection(real_beats_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [real_beats_txt_file]})
    client.post(
        "/beats/detect",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )

    response = client.get(
        "/beats/of-interest", params={"path": real_beats_txt_file}
    )

    # Beats exist but nothing has been flagged yet - an empty list, not an
    # error: the graph just has no beats of interest to navigate.
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["count"] == 0
    assert result["ts"] == []


def test_rejecting_a_beat_drops_it_from_beats_of_interest(
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
    client.post("/files/beats", json={"path": real_beats_txt_file, "channel": "channel 1"})

    before = client.get(
        "/beats/of-interest", params={"path": real_beats_txt_file}
    ).json()
    rejected_ts = before["ts"][0]

    client.patch(
        "/files/beats/category",
        json={"path": real_beats_txt_file, "ts": rejected_ts, "action": "reject"},
    )

    after = client.get(
        "/beats/of-interest", params={"path": real_beats_txt_file}
    ).json()

    assert after["count"] == before["count"] - 1
    assert rejected_ts not in after["ts"]


def test_beats_of_interest_before_beat_detection_reports_error(real_beats_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [real_beats_txt_file]})

    response = client.get(
        "/beats/of-interest", params={"path": real_beats_txt_file}
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]
