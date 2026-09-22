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
    assert result["arrhythmia"]["min_samples"] == 30
    assert result["arrhythmia"]["eps_auto"] is True
    assert result["arrhythmia"]["beat_length"] == 128
    assert result["arrhythmia"]["max_rr"] == 0.1667


def test_defaults_endpoint_returns_factory_values_without_changing_live_settings():
    client = TestClient(create_app())

    current = client.get("/settings").json()
    current["arrhythmia"]["bradycardia_absolute_hr"] = 250
    assert client.put("/settings", json=current).json()["status"] == "ok"

    defaults = client.get("/settings/defaults").json()

    assert defaults["arrhythmia"]["bradycardia_absolute_hr"] == 300
    # Asking for the defaults must not itself revert anything.
    assert client.get("/settings").json()["arrhythmia"]["bradycardia_absolute_hr"] == 250


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
