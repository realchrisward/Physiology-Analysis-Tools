import os

import pandas as pd
from fastapi.testclient import TestClient

from backend.app import create_app


def test_generate_report_writes_xlsx_with_three_sheets(tmp_path, real_beats_txt_file):
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
    client.post(
        "/files/beats",
        json={"path": real_beats_txt_file, "channel": "channel 1"},
    )
    client.post(
        "/files/bad-data",
        json={"path": real_beats_txt_file, "start": 0.3, "stop": 0.8},
    )

    output_dir = tmp_path / "reports"
    os.makedirs(output_dir)
    output_path = output_dir / "57-report.xlsx"

    response = client.post(
        "/files/report",
        json={"path": real_beats_txt_file, "output_path": str(output_path)},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok"
    assert result["output_path"].endswith("57-report.xlsx")
    assert os.path.exists(result["output_path"])

    sheets = pd.read_excel(result["output_path"], sheet_name=None)

    beats_df = sheets["beats"]
    assert len(beats_df) == 15
    assert "review_state" in beats_df.columns
    assert (beats_df["review_state"] == "unreviewed").all()

    bad_data_df = sheets["bad_data_marks"]
    assert len(bad_data_df) == 1
    assert bad_data_df.iloc[0]["start"] == 0.3
    assert bad_data_df.iloc[0]["stop"] == 0.8

    settings_df = sheets["settings"]
    assert len(settings_df) == 1
    assert settings_df.iloc[0]["heartbeat_version"] == "0.0.4"
    assert settings_df.iloc[0]["arrhythmia_version"] == "0.0.9"
    assert settings_df.iloc[0]["ml_version"] == "0.0.1"


def test_generate_report_appends_xlsx_extension_if_missing(
    tmp_path, real_beats_txt_file
):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [real_beats_txt_file]})
    client.post(
        "/beats/detect", json={"path": real_beats_txt_file, "channel": "channel 1"}
    )
    client.post(
        "/files/beats", json={"path": real_beats_txt_file, "channel": "channel 1"}
    )

    output_dir = tmp_path / "reports"
    os.makedirs(output_dir)

    response = client.post(
        "/files/report",
        json={
            "path": real_beats_txt_file,
            "output_path": str(output_dir / "no-extension"),
        },
    )

    result = response.json()
    assert result["status"] == "ok"
    assert result["output_path"].endswith("no-extension.xlsx")
    assert os.path.exists(result["output_path"])


def test_generate_report_without_persisted_data_is_an_error(
    tmp_path, real_beats_txt_file
):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [real_beats_txt_file]})

    output_dir = tmp_path / "reports"
    os.makedirs(output_dir)

    response = client.post(
        "/files/report",
        json={"path": real_beats_txt_file, "output_path": str(output_dir / "57.xlsx")},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]


def test_generate_report_with_only_bad_data_persisted_is_clean_domain_error(
    tmp_path, real_beats_txt_file
):
    client = TestClient(create_app(db_path=str(tmp_path / "test.db")))
    client.post("/files/import", json={"paths": [real_beats_txt_file]})
    # A `files` row now exists (created by bad-data alone), but
    # beat_settings_json/arrhythmia_settings_json are still NULL because
    # POST /files/beats was never called.
    client.post(
        "/files/bad-data",
        json={"path": real_beats_txt_file, "start": 0.3, "stop": 0.8},
    )

    output_dir = tmp_path / "reports"
    os.makedirs(output_dir)

    response = client.post(
        "/files/report",
        json={"path": real_beats_txt_file, "output_path": str(output_dir / "57.xlsx")},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert (
        result["error"]
        == "No persisted data for this file — run POST /files/beats first"
    )
    assert "NoneType" not in result["error"]
    assert "TypeError" not in result["error"]


def test_generate_report_with_nonexistent_output_dir_is_an_error(
    tmp_path, real_beats_txt_file
):
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
        "/files/report",
        json={
            "path": real_beats_txt_file,
            "output_path": str(tmp_path / "does_not_exist" / "57.xlsx"),
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "error"
    assert result["error"]
