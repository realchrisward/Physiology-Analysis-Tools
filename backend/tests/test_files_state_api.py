import pytest
from fastapi.testclient import TestClient

from backend import db
from backend.app import create_app


def _detect_and_persist(client, path, *, with_arrhythmia=True):
    client.post("/files/import", json={"paths": [path]})
    client.post("/beats/detect", json={"path": path, "channel": "channel 1"})
    if with_arrhythmia:
        client.post(
            "/arrhythmia/detect",
            json={"path": path, "channel": "channel 1", "method": "heuristic"},
        )
    response = client.post("/files/beats", json={"path": path, "channel": "channel 1"})
    assert response.json()["status"] == "ok"


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

    # No prior record means nothing to restore — beat_cache must be left
    # untouched, not populated with some empty-but-"ok" state.
    window_response = client.get(
        "/beats/window", params={"path": example_txt_file, "start": 0, "end": 999}
    )
    window_result = window_response.json()
    assert window_result["status"] == "error"
    assert "has not been run" in window_result["error"]


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


def test_restore_populates_beat_cache_on_fresh_instance(tmp_path, real_beats_txt_file):
    """Beats restored into beat_cache are visible via GET /beats/window on a
    fresh app instance — simulating a reopen after restart."""
    db_path = str(tmp_path / "test.db")

    client1 = TestClient(create_app(db_path=db_path))
    _detect_and_persist(client1, real_beats_txt_file)

    # Brand new app instance, same db_path, fresh (empty) beat_cache.
    client2 = TestClient(create_app(db_path=db_path))
    client2.post("/files/import", json={"paths": [real_beats_txt_file]})

    state_response = client2.get(
        "/files/state", params={"path": real_beats_txt_file}
    )
    assert state_response.json()["found"] is True

    window_response = client2.get(
        "/beats/window",
        params={"path": real_beats_txt_file, "start": 0, "end": 999},
    )
    result = window_response.json()
    assert result["status"] == "ok"
    assert result["count"] == 15

    first_beat = result["beats"][0]
    assert first_beat["ts"] == pytest.approx(0.0855, abs=1e-3)
    assert first_beat["tachycardia_absolute"] is True
    assert first_beat["any_arrhythmia"] is True


def test_restore_reflects_reject_made_in_first_session(tmp_path, real_beats_txt_file):
    """A reject made in the first session (persisted straight to SQLite by
    PATCH /files/beats/category) is reflected in the restored beat_cache on
    reopen — not just a naive re-run of detection."""
    db_path = str(tmp_path / "test.db")

    client1 = TestClient(create_app(db_path=db_path))
    _detect_and_persist(client1, real_beats_txt_file)

    persisted = client1.get(
        "/files/state", params={"path": real_beats_txt_file}
    ).json()
    first_ts = persisted["beats"][0]["ts"]
    assert first_ts == pytest.approx(0.0855, abs=1e-3)

    reject_response = client1.patch(
        "/files/beats/category",
        json={"path": real_beats_txt_file, "ts": first_ts, "action": "reject"},
    )
    assert reject_response.json()["status"] == "ok"

    client2 = TestClient(create_app(db_path=db_path))
    client2.post("/files/import", json={"paths": [real_beats_txt_file]})
    client2.get("/files/state", params={"path": real_beats_txt_file})

    window_response = client2.get(
        "/beats/window",
        params={"path": real_beats_txt_file, "start": 0, "end": 999},
    )
    result = window_response.json()
    first_beat = result["beats"][0]
    assert first_beat["ts"] == pytest.approx(first_ts, abs=1e-6)
    assert first_beat["tachycardia_absolute"] is False
    assert first_beat["any_arrhythmia"] is False


def test_restore_no_prior_record_leaves_beat_cache_untouched(
    tmp_path, example_txt_file
):
    db_path = str(tmp_path / "test.db")
    client = TestClient(create_app(db_path=db_path))
    client.post("/files/import", json={"paths": [example_txt_file]})

    state_response = client.get(
        "/files/state", params={"path": example_txt_file}
    )
    assert state_response.json()["found"] is False

    window_response = client.get(
        "/beats/window", params={"path": example_txt_file, "start": 0, "end": 999}
    )
    window_result = window_response.json()
    assert window_result["status"] == "error"
    assert "has not been run" in window_result["error"]


def test_restore_omits_category_columns_when_arrhythmia_never_run(
    tmp_path, real_beats_txt_file
):
    """Only category columns with real data are restored — a file persisted
    via POST /files/beats alone (arrhythmia detection never run) restores
    beats with every category column absent (None), not present-but-False."""
    db_path = str(tmp_path / "test.db")

    client1 = TestClient(create_app(db_path=db_path))
    _detect_and_persist(client1, real_beats_txt_file, with_arrhythmia=False)

    client2 = TestClient(create_app(db_path=db_path))
    client2.post("/files/import", json={"paths": [real_beats_txt_file]})
    client2.get("/files/state", params={"path": real_beats_txt_file})

    window_response = client2.get(
        "/beats/window",
        params={"path": real_beats_txt_file, "start": 0, "end": 999},
    )
    result = window_response.json()
    assert result["status"] == "ok"
    assert result["count"] == 15
    for beat in result["beats"]:
        assert beat["any_arrhythmia"] is None
        assert beat["tachycardia_absolute"] is None
        assert beat["bradycardia_absolute"] is None
        assert beat["skipped_beat"] is None
        assert beat["prem_beat"] is None
        assert beat["abn_cluster"] is None
        assert beat["other_arrhythmia"] is None
