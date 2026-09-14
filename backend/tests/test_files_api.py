from fastapi.testclient import TestClient

from backend.app import create_app


def test_import_labchart_text_file_selects_channel_1(example_txt_file):
    client = TestClient(create_app())

    response = client.post("/files/import", json={"paths": [example_txt_file]})

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "ok"
    assert result["channels"] == ["channel 1"]
    assert result["time_column"] == "ts"
    assert result["default_channel"] == "channel 1"
    assert result["default_channel_matched_rule"] is True


def test_import_then_list_returns_the_file(example_txt_file):
    client = TestClient(create_app())
    client.post("/files/import", json={"paths": [example_txt_file]})

    response = client.get("/files")

    assert response.status_code == 200
    paths = [f["path"] for f in response.json()]
    assert example_txt_file in paths


def test_import_missing_file_reports_error_not_crash():
    client = TestClient(create_app())

    response = client.post(
        "/files/import", json={"paths": ["/nonexistent/path/should_fail.txt"]}
    )

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "error"
    assert result["error"]
