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

    # The shape-based clustering must actually DISCRIMINATE between beats,
    # not label the whole recording abnormal. With a fixed eps it did exactly
    # that (163/163 abnormal, making the flag useless); the adaptive eps
    # scales the threshold to this recording's own beat-to-beat spread.
    abn_count = sum(1 for beat in result["beats"] if beat["abn_cluster"] is True)
    assert 0 <= abn_count < 163

    # Heuristic rules still flag most beats of this particular recording:
    # its median rate (~202 bpm) sits below the default 300 bpm bradycardia
    # threshold, so bradycardia/premature-beat rules fire widely. That is a
    # property of this fixture against the default thresholds, not of the
    # clustering.
    assert 0 < result["any_arrhythmia_count"] <= 163

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
    # A beat_window large enough that the unsupervised method's epoch window
    # extends past the start of the signal for the very first beat, but not
    # past the end for the last beat: the epocher skips that one boundary
    # beat, its cluster label is therefore missing when
    # ml_tools.call_arrhythmias_PCA() maps labels back onto beat_df by ts,
    # and its abn_cluster comes back as a genuine NaN mixed in with real
    # booleans for the other beats - the exact scenario that gives the column
    # dtype=object and (pre-fix) got it silently dropped from
    # any_arrhythmia's aggregation for every beat, not just the NaN one.
    #
    # eps is pinned to a fixed, deliberately tiny value here (rather than the
    # adaptive default) so that every beat that DOES get an epoch clusters as
    # an outlier - that makes the True side of the mixed column deterministic
    # rather than dependent on this recording's beat shapes.
    client = TestClient(create_app())

    settings = client.get("/settings").json()
    settings["arrhythmia"]["beat_window"] = 20
    settings["arrhythmia"]["eps_auto"] = False
    settings["arrhythmia"]["eps"] = 0.03
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


def test_too_few_beats_for_clustering_reports_clean_error(real_beats_txt_file):
    # beat_window=14 on 57.txt's 15 beats leaves exactly 1 beat epoch that
    # doesn't get boundary-skipped by the epocher (verified directly against
    # this fixture) - not enough for PCA(n_components=2), which previously
    # surfaced as a raw, unhelpful sklearn internals error ("Input X contains
    # NaN...") instead of a clean, actionable message.
    client = TestClient(create_app())

    settings = client.get("/settings").json()
    settings["arrhythmia"]["beat_window"] = 14
    put_response = client.put("/settings", json=settings)
    assert put_response.json()["status"] == "ok"

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
            "method": "unsupervised",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]
    assert "NaN" not in result["error"]
    assert "sklearn" not in result["error"]
    assert "usable beat epochs" in result["error"]


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
