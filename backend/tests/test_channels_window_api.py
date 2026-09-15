import pytest
from fastapi.testclient import TestClient

from backend.app import create_app


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
