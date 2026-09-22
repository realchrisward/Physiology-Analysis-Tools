import pytest
from fastapi.testclient import TestClient

from backend.app import create_app


def test_reimport_invalidates_stale_window_cache_entries(example_txt_file):
    app = create_app()
    client = TestClient(app)
    client.post("/files/import", json={"paths": [example_txt_file]})

    params = {
        "path": example_txt_file,
        "channel": "channel 1",
        "start": 0,
        "end": 1.1394999999999982,
        "resolution": 10000,
    }
    cache_key = (
        params["path"],
        params["channel"],
        params["start"],
        params["end"],
        params["resolution"],
        # No filter parameters, since this is an unfiltered (raw) request —
        # see get_channel_window's own cache-key construction.
        None,
    )

    response = client.get("/channels/window", params=params)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    # The query above must have been memoized under this exact key.
    assert cache_key in app.state.window_cache

    # Re-importing the same path (e.g. the technician re-imported after the
    # file changed on disk) must drop the memoized entry rather than let a
    # later identical query silently return the old, now-possibly-stale
    # result.
    client.post("/files/import", json={"paths": [example_txt_file]})
    assert cache_key not in app.state.window_cache


def test_small_file_full_range_high_resolution_is_passthrough(example_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [example_txt_file]})

    response = client.get(
        "/channels/window",
        params={
            "path": example_txt_file,
            "channel": "channel 1",
            "start": 0,
            "end": 1.1394999999999982,
            "resolution": 10000,
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["downsampled"] is False
    assert result["point_count"] == 2280
    assert len(result["x"]) == 2280
    assert len(result["y"]) == 2280


def test_large_file_full_range_low_resolution_downsamples(long_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [long_txt_file]})

    response = client.get(
        "/channels/window",
        params={
            "path": long_txt_file,
            "channel": "channel 1",
            "start": 0,
            "end": 111.7995,
            "resolution": 100,
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["downsampled"] is True
    assert result["point_count"] == 400
    assert len(result["x"]) == 400
    assert len(result["y"]) == 400

    assert result["x"][0] == pytest.approx(0.2565, abs=1e-4)
    assert result["y"][0] == pytest.approx(-10.24, abs=1e-2)
    assert result["x"][-1] == pytest.approx(111.786, abs=1e-3)
    assert result["y"][-1] == pytest.approx(-6.848, abs=1e-3)


def test_narrower_window_mid_file(long_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [long_txt_file]})

    response = client.get(
        "/channels/window",
        params={
            "path": long_txt_file,
            "channel": "channel 1",
            "start": 10,
            "end": 20,
            "resolution": 50,
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["point_count"] == 200

    for x in result["x"]:
        assert 10 <= x <= 20


def test_determinism_repeat_query_returns_identical_result(long_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [long_txt_file]})

    params = {
        "path": long_txt_file,
        "channel": "channel 1",
        "start": 0,
        "end": 111.7995,
        "resolution": 100,
    }

    first = client.get("/channels/window", params=params).json()
    second = client.get("/channels/window", params=params).json()

    assert first["x"] == second["x"]
    assert first["y"] == second["y"]


def test_unknown_channel_reports_error(example_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [example_txt_file]})

    response = client.get(
        "/channels/window",
        params={
            "path": example_txt_file,
            "channel": "not_a_real_channel",
            "start": 0,
            "end": 1.1394999999999982,
            "resolution": 100,
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]


def test_file_never_imported_reports_error_not_crash():
    client = TestClient(create_app())

    response = client.get(
        "/channels/window",
        params={
            "path": "/nonexistent/path/never_imported.txt",
            "channel": "channel 1",
            "start": 0,
            "end": 10,
            "resolution": 100,
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]


def test_empty_range_outside_data_is_ok_with_zero_points(example_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [example_txt_file]})

    response = client.get(
        "/channels/window",
        params={
            "path": example_txt_file,
            "channel": "channel 1",
            "start": 5,
            "end": 6,
            "resolution": 100,
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["point_count"] == 0
    assert result["x"] == []
    assert result["y"] == []


def test_filtered_view_removes_baseline_offset_raw_view_keeps(real_beats_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [real_beats_txt_file]})

    params = {
        "path": real_beats_txt_file,
        "channel": "channel 1",
        "start": 0,
        "end": 999,
        "resolution": 100000,
    }
    raw = client.get("/channels/window", params=params).json()
    filtered = client.get(
        "/channels/window", params={**params, "filtered": "true"}
    ).json()

    assert raw["status"] == "ok"
    assert filtered["status"] == "ok"
    # Same time base, same number of points - only the voltages differ.
    assert filtered["x"] == raw["x"]
    assert filtered["y"] != raw["y"]

    # The highpass filter's whole purpose is removing slow baseline drift, so
    # the filtered trace must sit much closer to zero than the raw one, which
    # carries this recording's real DC offset (measured: raw -0.0574,
    # filtered -0.00057, a ~100x reduction).
    raw_mean = sum(raw["y"]) / len(raw["y"])
    filtered_mean = sum(filtered["y"]) / len(filtered["y"])
    assert abs(raw_mean) > 0.01
    assert abs(filtered_mean) < abs(raw_mean) / 10


def test_filtered_and_raw_windows_are_cached_separately(real_beats_txt_file):
    app = create_app()
    client = TestClient(app)
    client.post("/files/import", json={"paths": [real_beats_txt_file]})

    params = {
        "path": real_beats_txt_file,
        "channel": "channel 1",
        "start": 0,
        "end": 1,
        "resolution": 500,
    }
    raw = client.get("/channels/window", params=params).json()
    filtered = client.get(
        "/channels/window", params={**params, "filtered": "true"}
    ).json()

    assert len(app.state.window_cache) == 2
    # A repeat of each must be served from cache, not recomputed differently.
    assert client.get("/channels/window", params=params).json() == raw
    assert (
        client.get("/channels/window", params={**params, "filtered": "true"}).json()
        == filtered
    )


def test_changing_filter_settings_does_not_serve_a_stale_filtered_window(
    real_beats_txt_file,
):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [real_beats_txt_file]})

    params = {
        "path": real_beats_txt_file,
        "channel": "channel 1",
        "start": 0,
        "end": 1,
        "resolution": 500,
        "filtered": "true",
    }
    before = client.get("/channels/window", params=params).json()

    settings = client.get("/settings").json()
    settings["beat"]["ecg_filt_cutoff"] = 40
    assert client.put("/settings", json=settings).json()["status"] == "ok"

    after = client.get("/channels/window", params=params).json()

    assert after["status"] == "ok"
    assert after["y"] != before["y"]
