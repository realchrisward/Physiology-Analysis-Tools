import pytest
from fastapi.testclient import TestClient

from backend.app import create_app


def _setup_persisted_beats(client, path):
    client.post("/files/import", json={"paths": [path]})
    client.post("/beats/detect", json={"path": path, "channel": "channel 1"})
    client.post(
        "/arrhythmia/detect",
        json={"path": path, "channel": "channel 1", "method": "heuristic"},
    )
    response = client.post(
        "/files/beats", json={"path": path, "channel": "channel 1"}
    )
    assert response.json()["status"] == "ok"


def _get_beat(client, path, ts):
    state = client.get("/files/state", params={"path": path}).json()
    for beat in state["beats"]:
        if beat["ts"] == pytest.approx(ts, abs=1e-4):
            return beat
    return None


def _exact_ts(client, path, approx_ts):
    """The real, exact ts that round-tripped through detection + persist for
    the beat near `approx_ts` — PATCH lookups use float equality, so tests
    must send this rather than a hand-typed approximate value."""
    beat = _get_beat(client, path, approx_ts)
    assert beat is not None
    return beat["ts"]


def test_confirm_does_not_touch_category_flags(tmp_path, real_beats_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    _setup_persisted_beats(client, real_beats_txt_file)
    ts = _exact_ts(client, real_beats_txt_file, 0.0855)

    response = client.patch(
        "/files/beats/category",
        json={"path": real_beats_txt_file, "ts": ts, "action": "confirm"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["review_state"] == "confirmed"

    beat = _get_beat(client, real_beats_txt_file, 0.0855)
    assert beat is not None
    assert beat["tachycardia_absolute"] is True
    assert beat["review_state"] == "confirmed"


def test_reject_cascades_and_clears_all_category_flags(tmp_path, real_beats_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    _setup_persisted_beats(client, real_beats_txt_file)
    ts = _exact_ts(client, real_beats_txt_file, 0.0855)

    response = client.patch(
        "/files/beats/category",
        json={"path": real_beats_txt_file, "ts": ts, "action": "reject"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["review_state"] == "rejected"

    beat = _get_beat(client, real_beats_txt_file, 0.0855)
    assert beat is not None
    assert beat["tachycardia_absolute"] is False
    assert beat["any_arrhythmia"] is False
    assert beat["bradycardia_absolute"] is False
    assert beat["skipped_beat"] is False
    assert beat["prem_beat"] is False
    assert beat["abn_cluster"] is False
    assert beat["other_arrhythmia"] is False


def test_reassign_sets_category_and_clears_others(tmp_path, real_beats_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    _setup_persisted_beats(client, real_beats_txt_file)
    ts = _exact_ts(client, real_beats_txt_file, 0.21)

    response = client.patch(
        "/files/beats/category",
        json={
            "path": real_beats_txt_file,
            "ts": ts,
            "action": "reassign",
            "category": "prem_beat",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["review_state"] == "confirmed"
    assert result["reassigned_category"] == "prem_beat"

    beat = _get_beat(client, real_beats_txt_file, 0.21)
    assert beat is not None
    assert beat["prem_beat"] is True
    assert beat["any_arrhythmia"] is True
    assert beat["bradycardia_absolute"] is False
    assert beat["tachycardia_absolute"] is False
    assert beat["skipped_beat"] is False
    assert beat["abn_cluster"] is False
    assert beat["other_arrhythmia"] is False


def test_reassign_with_invalid_category_leaves_row_unchanged(
    tmp_path, real_beats_txt_file
):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    _setup_persisted_beats(client, real_beats_txt_file)
    before = _get_beat(client, real_beats_txt_file, 0.0855)

    response = client.patch(
        "/files/beats/category",
        json={
            "path": real_beats_txt_file,
            "ts": 0.0855,
            "action": "reassign",
            "category": "not_a_real_category",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]

    after = _get_beat(client, real_beats_txt_file, 0.0855)
    assert after == before


def test_reassign_without_category_is_an_error(tmp_path, real_beats_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    _setup_persisted_beats(client, real_beats_txt_file)

    response = client.patch(
        "/files/beats/category",
        json={"path": real_beats_txt_file, "ts": 0.0855, "action": "reassign"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]


def test_unknown_ts_is_an_error(tmp_path, real_beats_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    _setup_persisted_beats(client, real_beats_txt_file)

    response = client.patch(
        "/files/beats/category",
        json={"path": real_beats_txt_file, "ts": 999.0, "action": "confirm"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]


def test_reject_is_immediately_visible_via_beats_window_same_instance(
    tmp_path, real_beats_txt_file
):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    _setup_persisted_beats(client, real_beats_txt_file)
    ts = _exact_ts(client, real_beats_txt_file, 0.0855)

    response = client.patch(
        "/files/beats/category",
        json={"path": real_beats_txt_file, "ts": ts, "action": "reject"},
    )
    assert response.json()["status"] == "ok"

    window_response = client.get(
        "/beats/window",
        params={"path": real_beats_txt_file, "start": ts - 0.01, "end": ts + 0.01},
    )
    result = window_response.json()
    assert result["status"] == "ok"
    beat = result["beats"][0]
    assert beat["ts"] == pytest.approx(ts, abs=1e-6)
    assert beat["tachycardia_absolute"] is False
    assert beat["any_arrhythmia"] is False
    assert beat["bradycardia_absolute"] is False
    assert beat["skipped_beat"] is False
    assert beat["prem_beat"] is False
    assert beat["abn_cluster"] is False
    assert beat["other_arrhythmia"] is False


def test_reassign_is_immediately_visible_via_beats_window_same_instance(
    tmp_path, real_beats_txt_file
):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    _setup_persisted_beats(client, real_beats_txt_file)
    ts = _exact_ts(client, real_beats_txt_file, 0.21)

    response = client.patch(
        "/files/beats/category",
        json={
            "path": real_beats_txt_file,
            "ts": ts,
            "action": "reassign",
            "category": "prem_beat",
        },
    )
    assert response.json()["status"] == "ok"

    window_response = client.get(
        "/beats/window",
        params={"path": real_beats_txt_file, "start": ts - 0.01, "end": ts + 0.01},
    )
    result = window_response.json()
    assert result["status"] == "ok"
    beat = result["beats"][0]
    assert beat["ts"] == pytest.approx(ts, abs=1e-6)
    assert beat["prem_beat"] is True
    assert beat["any_arrhythmia"] is True
    assert beat["bradycardia_absolute"] is False
    assert beat["tachycardia_absolute"] is False
    assert beat["skipped_beat"] is False
    assert beat["abn_cluster"] is False
    assert beat["other_arrhythmia"] is False


def test_remove_flag_clears_one_category_and_leaves_others_and_review_state_alone(
    tmp_path, real_beats_txt_file
):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    _setup_persisted_beats(client, real_beats_txt_file)
    ts = _exact_ts(client, real_beats_txt_file, 0.0855)
    before = _get_beat(client, real_beats_txt_file, 0.0855)
    assert before["tachycardia_absolute"] is True
    assert before["review_state"] == "unreviewed"

    response = client.patch(
        "/files/beats/category",
        json={
            "path": real_beats_txt_file,
            "ts": ts,
            "action": "remove_flag",
            "category": "tachycardia_absolute",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    # remove_flag is a correction to the raw detection, not a review
    # decision — review_state/reassigned_category are untouched.
    assert result["review_state"] == "unreviewed"
    assert result["reassigned_category"] is None

    beat = _get_beat(client, real_beats_txt_file, 0.0855)
    assert beat["tachycardia_absolute"] is False
    assert beat["review_state"] == "unreviewed"


def test_remove_flag_clears_any_arrhythmia_once_no_flags_remain(
    tmp_path, real_beats_txt_file
):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    _setup_persisted_beats(client, real_beats_txt_file)
    ts = _exact_ts(client, real_beats_txt_file, 0.0855)
    before = _get_beat(client, real_beats_txt_file, 0.0855)
    flagged = [
        cat
        for cat in [
            "bradycardia_absolute",
            "tachycardia_absolute",
            "skipped_beat",
            "prem_beat",
            "abn_cluster",
            "other_arrhythmia",
        ]
        if before[cat]
    ]
    assert flagged  # sanity: this beat has at least one real flag to remove

    for category in flagged:
        response = client.patch(
            "/files/beats/category",
            json={
                "path": real_beats_txt_file,
                "ts": ts,
                "action": "remove_flag",
                "category": category,
            },
        )
        assert response.json()["status"] == "ok"

    beat = _get_beat(client, real_beats_txt_file, 0.0855)
    assert beat["any_arrhythmia"] is False
    for category in flagged:
        assert beat[category] is False

    # Immediately visible via /beats/window too (same in-memory beat_cache
    # sync path the other three actions already rely on).
    window_response = client.get(
        "/beats/window",
        params={"path": real_beats_txt_file, "start": ts - 0.01, "end": ts + 0.01},
    )
    window_beat = window_response.json()["beats"][0]
    assert window_beat["any_arrhythmia"] is False


def test_remove_flag_with_invalid_category_is_an_error(tmp_path, real_beats_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    _setup_persisted_beats(client, real_beats_txt_file)

    response = client.patch(
        "/files/beats/category",
        json={
            "path": real_beats_txt_file,
            "ts": 0.0855,
            "action": "remove_flag",
            "category": "not_a_real_category",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]


def test_remove_flag_without_category_is_an_error(tmp_path, real_beats_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    _setup_persisted_beats(client, real_beats_txt_file)

    response = client.patch(
        "/files/beats/category",
        json={"path": real_beats_txt_file, "ts": 0.0855, "action": "remove_flag"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]


def test_no_persisted_record_for_file_is_an_error(tmp_path, example_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [example_txt_file]})

    response = client.patch(
        "/files/beats/category",
        json={"path": example_txt_file, "ts": 0.0855, "action": "confirm"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]
