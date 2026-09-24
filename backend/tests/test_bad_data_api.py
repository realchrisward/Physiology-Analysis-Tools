from fastapi.testclient import TestClient

from backend.app import create_app


def test_add_mark_auto_sorts_regardless_of_drag_direction(tmp_path, example_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [example_txt_file]})

    response = client.post(
        "/files/bad-data",
        json={"path": example_txt_file, "start": 0.8, "stop": 0.3},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["mark"]["start"] == 0.3
    assert result["mark"]["stop"] == 0.8

    state = client.get("/files/state", params={"path": example_txt_file}).json()
    assert len(state["bad_data_marks"]) == 1
    mark = state["bad_data_marks"][0]
    assert mark["start"] == 0.3
    assert mark["stop"] == 0.8
    assert mark["id"] == result["mark"]["id"]


def test_add_mark_on_never_imported_file_is_an_error(tmp_path, example_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))

    response = client.post(
        "/files/bad-data",
        json={"path": example_txt_file, "start": 0.3, "stop": 0.8},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"


def test_add_second_mark_then_delete_first(tmp_path, example_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [example_txt_file]})

    first = client.post(
        "/files/bad-data",
        json={"path": example_txt_file, "start": 0.8, "stop": 0.3},
    ).json()
    assert first["status"] == "ok"

    second = client.post(
        "/files/bad-data",
        json={"path": example_txt_file, "start": 0.9, "stop": 1.0},
    ).json()
    assert second["status"] == "ok"

    response = client.request(
        "DELETE",
        "/files/bad-data",
        json={"path": example_txt_file, "id": first["mark"]["id"]},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"

    state = client.get("/files/state", params={"path": example_txt_file}).json()
    assert len(state["bad_data_marks"]) == 1
    assert state["bad_data_marks"][0]["id"] == second["mark"]["id"]
    assert state["bad_data_marks"][0]["start"] == 0.9
    assert state["bad_data_marks"][0]["stop"] == 1.0


def test_delete_nonexistent_id_is_an_error(tmp_path, example_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [example_txt_file]})
    client.post(
        "/files/bad-data",
        json={"path": example_txt_file, "start": 0.3, "stop": 0.8},
    )

    response = client.request(
        "DELETE",
        "/files/bad-data",
        json={"path": example_txt_file, "id": 999999},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]


def test_update_mark_moves_range_and_auto_sorts(tmp_path, example_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [example_txt_file]})
    mark = client.post(
        "/files/bad-data",
        json={"path": example_txt_file, "start": 0.3, "stop": 0.8},
    ).json()["mark"]

    response = client.patch(
        "/files/bad-data",
        json={"path": example_txt_file, "id": mark["id"], "start": 1.2, "stop": 0.9},
    )

    result = response.json()
    assert result["status"] == "ok"
    assert result["mark"] == {"id": mark["id"], "start": 0.9, "stop": 1.2}
    state = client.get("/files/state", params={"path": example_txt_file}).json()
    assert state["bad_data_marks"] == [{"id": mark["id"], "start": 0.9, "stop": 1.2}]


def test_update_unknown_mark_is_an_error(tmp_path, example_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [example_txt_file]})
    client.post(
        "/files/bad-data",
        json={"path": example_txt_file, "start": 0.3, "stop": 0.8},
    )

    result = client.patch(
        "/files/bad-data",
        json={"path": example_txt_file, "id": 9999, "start": 0.1, "stop": 0.2},
    ).json()

    assert result["status"] == "error"
    assert "No bad-data mark" in result["error"]


def test_update_mark_on_never_imported_file_is_an_error(tmp_path, example_txt_file):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))

    result = client.patch(
        "/files/bad-data",
        json={"path": example_txt_file, "id": 1, "start": 0.1, "stop": 0.2},
    ).json()

    assert result["status"] == "error"
