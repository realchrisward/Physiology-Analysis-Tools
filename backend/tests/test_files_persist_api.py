import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend import db
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


def test_post_beats_failure_rolls_back_channel_and_beats_atomically(
    tmp_path, real_beats_txt_file, monkeypatch
):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [real_beats_txt_file]})
    client.post(
        "/beats/detect",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )

    # First persist succeeds normally, establishing channel 1 as the
    # durable state.
    response = client.post(
        "/files/beats",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )
    assert response.json()["status"] == "ok"

    # Force replace_beats to fail on the second persist call, simulating a
    # mid-transaction failure. If the write isn't atomic, upsert_file's
    # channel="channel 2" update would be left standing even though the
    # beats table still holds the channel-1 detection result.
    from backend import db as db_module

    def failing_replace_beats(*args, **kwargs):
        raise RuntimeError("simulated replace_beats failure")

    monkeypatch.setattr(db_module, "replace_beats", failing_replace_beats)

    response = client.post(
        "/files/beats",
        json={"path": real_beats_txt_file, "channel": "channel 2"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"

    monkeypatch.undo()

    state_response = client.get(
        "/files/state", params={"path": real_beats_txt_file}
    )
    state = state_response.json()
    assert state["channel"] == "channel 1"
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


def _exact_ts(state, approx_ts):
    """The real, exact ts that round-tripped through detection + persist for
    the beat near `approx_ts` — PATCH lookups use float equality, so tests
    must send this rather than a hand-typed approximate value."""
    for beat in state["beats"]:
        if beat["ts"] == pytest.approx(approx_ts, abs=1e-4):
            return beat["ts"]
    raise AssertionError(f"no beat near ts={approx_ts}")


def test_post_beats_repersist_preserves_prior_review_state(
    tmp_path, real_beats_txt_file
):
    """Re-persisting a channel (e.g. after an arrhythmia rerun updates
    beat_cache) must not silently wipe out review work a technician already
    did via PATCH /files/beats/category — replace_beats has to carry
    forward review_state/reassigned_category for beats that still exist."""
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

    state = client.get(
        "/files/state", params={"path": real_beats_txt_file}
    ).json()
    confirm_ts = _exact_ts(state, 0.0855)
    reassign_ts = _exact_ts(state, 0.21)

    confirm_response = client.patch(
        "/files/beats/category",
        json={"path": real_beats_txt_file, "ts": confirm_ts, "action": "confirm"},
    )
    assert confirm_response.json()["status"] == "ok"

    reassign_response = client.patch(
        "/files/beats/category",
        json={
            "path": real_beats_txt_file,
            "ts": reassign_ts,
            "action": "reassign",
            "category": "prem_beat",
        },
    )
    assert reassign_response.json()["status"] == "ok"

    # Simulate a fresh detection/arrhythmia rerun re-persisting the same
    # channel (same beat_cache, since we didn't re-run detection here — the
    # point under test is that replace_beats itself is now safe to call
    # again, independent of whether the underlying beats changed).
    response = client.post(
        "/files/beats",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    state_after = client.get(
        "/files/state", params={"path": real_beats_txt_file}
    ).json()
    beats_by_ts = {beat["ts"]: beat for beat in state_after["beats"]}

    confirmed_beat = beats_by_ts[confirm_ts]
    assert confirmed_beat["review_state"] == "confirmed"
    assert confirmed_beat["reassigned_category"] is None

    reassigned_beat = beats_by_ts[reassign_ts]
    assert reassigned_beat["review_state"] == "confirmed"
    assert reassigned_beat["reassigned_category"] == "prem_beat"
    assert reassigned_beat["prem_beat"] is True

    # Everything else should remain untouched, still unreviewed.
    untouched = [
        beat
        for ts, beat in beats_by_ts.items()
        if ts not in (confirm_ts, reassign_ts)
    ]
    assert untouched
    assert all(beat["review_state"] == "unreviewed" for beat in untouched)


def test_replace_beats_still_wholesale_replaces_on_genuine_redetection(tmp_path):
    """The review-state-preservation logic must not prevent legitimate
    re-detection (e.g. a channel switch producing a different beat count)
    from correctly replacing the beat set — only beats whose `ts` survives
    into the new detection output keep their preserved review state."""
    conn = db.connect(str(tmp_path / "unit.db"))
    file_id = db.upsert_file(conn, "some/path.txt", 100, 1.0)

    first_df = pd.DataFrame(
        {
            "ts": [0.1, 0.2, 0.3],
            "RR": [0.5, 0.5, 0.5],
            "R_amplitude": [1.0, 1.0, 1.0],
            "HR": [120, 120, 120],
        }
    )
    db.replace_beats(conn, file_id, first_df)

    # Review one beat that will survive into the new detection output (0.1)
    # and one that will NOT (0.3, dropped by the new "detection" run).
    db.update_beat_category(conn, file_id, 0.1, "confirm")
    db.update_beat_category(conn, file_id, 0.3, "confirm")

    # A genuinely different detection result: different beat count (5 vs
    # 3), one surviving ts (0.1), one dropped ts (0.3), three brand-new ones.
    second_df = pd.DataFrame(
        {
            "ts": [0.1, 0.15, 0.25, 0.35, 0.45],
            "RR": [0.5] * 5,
            "R_amplitude": [1.0] * 5,
            "HR": [120] * 5,
        }
    )
    db.replace_beats(conn, file_id, second_df)

    rows = {
        row["ts"]: row
        for row in conn.execute(
            "SELECT * FROM beats WHERE file_id=? ORDER BY ts", (file_id,)
        ).fetchall()
    }

    # Wholesale replace: exactly the new beat set, nothing from the old one
    # that isn't also in the new one.
    assert set(rows.keys()) == {0.1, 0.15, 0.25, 0.35, 0.45}

    # The surviving reviewed beat kept its review state...
    assert rows[0.1]["review_state"] == "confirmed"
    # ...while every brand-new beat (including ones that happen to be new
    # additions, not just re-detected) starts unreviewed.
    for ts in (0.15, 0.25, 0.35, 0.45):
        assert rows[ts]["review_state"] == "unreviewed"
        assert rows[ts]["reassigned_category"] is None

    conn.close()
