from fastapi.testclient import TestClient

from backend.app import create_app


def test_get_returns_real_current_defaults():
    client = TestClient(create_app())

    response = client.get("/settings")

    assert response.status_code == 200
    result = response.json()
    assert result["beat"]["min_RR"] == 60
    assert result["beat"]["perc_thresh"] == 97
    assert result["arrhythmia"]["bradycardia_absolute_hr"] == 300
    assert result["arrhythmia"]["tachycardia_absolute_hr"] == 850
    assert result["arrhythmia"]["window_size"] == 100


def test_put_applies_a_valid_change():
    client = TestClient(create_app())

    current = client.get("/settings").json()
    current["arrhythmia"]["bradycardia_absolute_hr"] = 250

    response = client.put("/settings", json=current)

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["settings"]["arrhythmia"]["bradycardia_absolute_hr"] == 250

    followup = client.get("/settings").json()
    assert followup["arrhythmia"]["bradycardia_absolute_hr"] == 250


def test_put_rejects_invalid_brady_tachy_pair():
    client = TestClient(create_app())

    original = client.get("/settings").json()
    invalid = client.get("/settings").json()
    invalid["arrhythmia"]["bradycardia_absolute_hr"] = 900
    invalid["arrhythmia"]["tachycardia_absolute_hr"] = 850

    response = client.put("/settings", json=invalid)

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]

    followup = client.get("/settings").json()
    assert followup == original
