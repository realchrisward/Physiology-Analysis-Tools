import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.test_beats_category_api import _setup_persisted_beats


def _beats(client, path):
    state = client.get("/files/state", params={"path": path}).json()
    return sorted(state["beats"], key=lambda beat: beat["ts"])


def _delete(client, path, ts):
    response = client.request("DELETE", "/files/beats/one", json={"path": path, "ts": ts})
    assert response.json()["status"] == "ok"


def _window_beats(client, path):
    result = client.get(
        "/beats/window", params={"path": path, "start": -1e9, "end": 1e9}
    ).json()
    return sorted(result["beats"], key=lambda beat: beat["ts"])


def _client_with_beats(tmp_path, path):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    _setup_persisted_beats(client, path)
    return client


def test_snap_finds_the_peak_a_rough_click_was_aiming_at(tmp_path, real_beats_txt_file):
    path = real_beats_txt_file
    client = _client_with_beats(tmp_path, path)
    beats = _beats(client, path)
    target = beats[6]["ts"]
    _delete(client, path, target)

    snapped = client.post(
        "/files/beats/snap",
        json={"path": path, "channel": "channel 1", "ts": target + 0.02},
    ).json()

    assert snapped["status"] == "ok"
    assert snapped["ts"] == pytest.approx(target, abs=0.005)


def test_add_restores_a_deleted_beat_and_fixes_neighbour_rr(tmp_path, real_beats_txt_file):
    path = real_beats_txt_file
    client = _client_with_beats(tmp_path, path)
    before = _beats(client, path)
    target = before[6]

    _delete(client, path, target["ts"])
    after_delete = _beats(client, path)
    assert len(after_delete) == len(before) - 1
    # The beat after the gap now measures RR from the beat before it.
    successor = after_delete[6]
    assert successor["rr"] == pytest.approx(successor["ts"] - after_delete[5]["ts"])
    assert successor["hr"] == pytest.approx(60 / successor["rr"])

    added = client.post(
        "/files/beats/one",
        json={"path": path, "channel": "channel 1", "ts": target["ts"] + 0.0004},
    ).json()

    assert added["status"] == "ok"
    assert added["beat"]["ts"] == pytest.approx(target["ts"], abs=0.002)
    restored = _beats(client, path)
    assert len(restored) == len(before)
    new_index = next(i for i, b in enumerate(restored) if b["ts"] == added["beat"]["ts"])
    new, following = restored[new_index], restored[new_index + 1]
    assert new["rr"] == pytest.approx(new["ts"] - restored[new_index - 1]["ts"])
    assert following["rr"] == pytest.approx(following["ts"] - new["ts"])
    assert new["review_state"] == "unreviewed"
    assert new["any_arrhythmia"] is None

    # The in-memory cache the graph reads from agrees with SQLite.
    cached = _window_beats(client, path)
    assert [b["ts"] for b in cached] == [b["ts"] for b in restored]
    assert cached[new_index]["rr"] == pytest.approx(new["rr"])


def test_add_refuses_a_position_that_already_has_a_beat(tmp_path, real_beats_txt_file):
    path = real_beats_txt_file
    client = _client_with_beats(tmp_path, path)
    existing = _beats(client, path)[4]["ts"]

    added = client.post(
        "/files/beats/one",
        json={"path": path, "channel": "channel 1", "ts": existing + 0.001},
    ).json()

    assert added["status"] == "error"
    assert "already a beat" in added["error"]


def test_add_outside_the_recording_is_an_error(tmp_path, real_beats_txt_file):
    path = real_beats_txt_file
    client = _client_with_beats(tmp_path, path)

    added = client.post(
        "/files/beats/one",
        json={"path": path, "channel": "channel 1", "ts": 1e6},
    ).json()

    assert added["status"] == "error"
    assert "outside the recording" in added["error"]


def test_add_before_beats_are_saved_is_an_error(tmp_path, real_beats_txt_file):
    path = real_beats_txt_file
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [path]})
    client.post("/beats/detect", json={"path": path, "channel": "channel 1"})
    ts = client.get(
        "/beats/window", params={"path": path, "start": 0, "end": 100}
    ).json()["beats"][3]["ts"]

    added = client.post(
        "/files/beats/one",
        json={"path": path, "channel": "channel 1", "ts": ts + 0.05},
    ).json()

    assert added["status"] == "error"


def test_snap_and_add_need_beat_detection_first(tmp_path, real_beats_txt_file):
    path = real_beats_txt_file
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [path]})

    snapped = client.post(
        "/files/beats/snap", json={"path": path, "channel": "channel 1", "ts": 0.5}
    ).json()

    assert snapped["status"] == "error"
    assert "beat detection" in snapped["error"]


def _redetect(client, path):
    """Re-run the whole pipeline the way the app does after a settings change."""
    assert client.post(
        "/beats/detect", json={"path": path, "channel": "channel 1"}
    ).json()["status"] == "ok"
    client.post(
        "/arrhythmia/detect",
        json={"path": path, "channel": "channel 1", "method": "heuristic"},
    )
    assert client.post(
        "/files/beats", json={"path": path, "channel": "channel 1"}
    ).json()["status"] == "ok"


def _add_at(client, path, ts):
    added = client.post(
        "/files/beats/one", json={"path": path, "channel": "channel 1", "ts": ts}
    ).json()
    assert added["status"] == "ok"
    return added["beat"]


def test_added_beat_is_flagged_manual_with_a_timestamp(tmp_path, real_beats_txt_file):
    path = real_beats_txt_file
    client = _client_with_beats(tmp_path, path)
    target = _beats(client, path)[6]["ts"]
    _delete(client, path, target)

    added = _add_at(client, path, target)

    assert added["manual_added_at"]
    stored = next(b for b in _beats(client, path) if b["ts"] == added["ts"])
    assert stored["manual_added_at"] == added["manual_added_at"]
    cached = next(b for b in _window_beats(client, path) if b["ts"] == added["ts"])
    assert cached["manual_added_at"] == added["manual_added_at"]
    assert all(b["manual_added_at"] is None for b in _beats(client, path) if b["ts"] != added["ts"])


def test_redetection_does_not_duplicate_a_manual_add_it_now_finds(tmp_path, real_beats_txt_file):
    path = real_beats_txt_file
    client = _client_with_beats(tmp_path, path)
    target = _beats(client, path)[6]["ts"]
    _delete(client, path, target)
    added = _add_at(client, path, target)
    # The peak is a real one, so re-detection finds it by itself; restoring
    # the manual add on top must not create a second beat.
    _redetect(client, path)

    beats = _beats(client, path)
    near = [b for b in beats if abs(b["ts"] - added["ts"]) < 0.01]
    assert len(near) == 1


def test_manual_add_of_a_missed_beat_is_restored_after_redetection(tmp_path, real_beats_txt_file):
    path = real_beats_txt_file
    client = _client_with_beats(tmp_path, path)
    beats = _beats(client, path)
    # Midway between two beats: detection would never find a peak here.
    midpoint = (beats[6]["ts"] + beats[7]["ts"]) / 2
    added = _add_at(client, path, midpoint)
    count_with_add = len(_beats(client, path))

    _redetect(client, path)

    after = _beats(client, path)
    assert len(after) == count_with_add
    restored = next(b for b in after if b["ts"] == pytest.approx(added["ts"]))
    assert restored["manual_added_at"] == added["manual_added_at"]
    index = after.index(restored)
    assert restored["rr"] == pytest.approx(restored["ts"] - after[index - 1]["ts"])
    assert after[index + 1]["rr"] == pytest.approx(after[index + 1]["ts"] - restored["ts"])
    assert restored["review_state"] == "unreviewed"


def test_manual_delete_survives_redetection(tmp_path, real_beats_txt_file):
    path = real_beats_txt_file
    client = _client_with_beats(tmp_path, path)
    before = _beats(client, path)
    removed = before[6]["ts"]
    _delete(client, path, removed)

    _redetect(client, path)

    after = _beats(client, path)
    assert len(after) == len(before) - 1
    assert all(abs(b["ts"] - removed) > 0.01 for b in after)
    successor = next(b for b in after if b["ts"] > removed)
    predecessor = max((b for b in after if b["ts"] < removed), key=lambda b: b["ts"])
    assert successor["rr"] == pytest.approx(successor["ts"] - predecessor["ts"])


def test_deleting_a_manual_beat_forgets_it_for_good(tmp_path, real_beats_txt_file):
    path = real_beats_txt_file
    client = _client_with_beats(tmp_path, path)
    beats = _beats(client, path)
    added = _add_at(client, path, (beats[6]["ts"] + beats[7]["ts"]) / 2)
    _delete(client, path, added["ts"])

    _redetect(client, path)

    assert len(_beats(client, path)) == len(beats)


def test_old_databases_gain_the_manual_column(tmp_path):
    import sqlite3

    from backend import db

    db_path = str(tmp_path / "old.db")
    old = sqlite3.connect(db_path)
    old.execute(
        "CREATE TABLE beats (id INTEGER PRIMARY KEY, file_id INTEGER, ts REAL, "
        "rr REAL, r_amplitude REAL, hr REAL)"
    )
    old.commit()
    old.close()

    conn = db.connect(db_path)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(beats)")}
    conn.close()

    assert "manual_added_at" in columns


def test_discarding_saved_state_forgets_manual_edits(tmp_path, real_beats_txt_file):
    path = real_beats_txt_file
    client = _client_with_beats(tmp_path, path)
    before = _beats(client, path)
    _delete(client, path, before[6]["ts"])
    client.request("DELETE", "/files/state", json={"path": path})

    client.post("/beats/detect", json={"path": path, "channel": "channel 1"})
    client.post("/files/beats", json={"path": path, "channel": "channel 1"})

    assert len(_beats(client, path)) == len(before)
