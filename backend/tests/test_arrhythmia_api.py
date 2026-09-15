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

    optional_columns = [
        "bradycardia_absolute",
        "tachycardia_absolute",
        "skipped_beat",
        "prem_beat",
        "abn_cluster",
    ]
    for beat in result["beats"]:
        if any(beat.get(col) for col in optional_columns):
            assert beat["any_arrhythmia"] is True


def test_any_arrhythmia_survives_mixed_nan_abn_cluster_column(long_txt_file):
    # A window_size large enough that the unsupervised method's epoch window
    # (window_size / 2 samples on each side of the beat) extends past the
    # start of the signal for the very first beat, but not past the end for
    # the last beat: ml_tools.beatepocher() skips that one boundary beat,
    # ml_tools.call_arrhythmias_PCA() left-joins its cluster labels back onto
    # beat_df, and the skipped beat's abn_cluster comes back as a genuine NaN
    # mixed in with real booleans for the other beats - the exact scenario
    # that gives the column dtype=object and (pre-fix) got it silently
    # dropped from any_arrhythmia's aggregation for every beat, not just the
    # NaN one.
    client = TestClient(create_app())

    settings = client.get("/settings").json()
    settings["arrhythmia"]["window_size"] = 6000
    put_response = client.put("/settings", json=settings)
    assert put_response.json()["status"] == "ok"

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

    # Confirm this genuinely exercised a mixed-NaN abn_cluster column (not
    # just the invariant in isolation): at least one beat is NaN (None) and
    # at least one other beat is really True.
    assert any(beat["abn_cluster"] is None for beat in result["beats"])
    assert any(beat["abn_cluster"] is True for beat in result["beats"])

    for beat in result["beats"]:
        if beat["abn_cluster"] is True:
            assert beat["any_arrhythmia"] is True


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
