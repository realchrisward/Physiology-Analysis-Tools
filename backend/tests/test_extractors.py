import pytest

from backend.extractors import (
    EXTRACTOR_SPECS,
    NoExtractorSucceededError,
    extract_dataframe,
)


def test_extract_dataframe_loads_labchart_text_file(example_txt_file):
    df = extract_dataframe(example_txt_file)

    assert "channel 1" in df.columns
    assert "ts" in df.columns
    assert len(df) > 0


def test_extract_dataframe_raises_for_unrecognized_extension():
    with pytest.raises(NoExtractorSucceededError):
        extract_dataframe("/some/path/file.unknownext")


def test_extract_dataframe_raises_for_missing_file(example_txt_file):
    missing = example_txt_file.replace("10.txt", "does_not_exist.txt")

    with pytest.raises(NoExtractorSucceededError) as exc_info:
        extract_dataframe(missing)

    assert exc_info.value.errors  # at least one candidate extractor's error is recorded


def test_txt_candidates_try_labchart_text_before_pcc():
    txt_candidate_names = [spec.name for spec in EXTRACTOR_SPECS if spec.ext == ".txt"]

    assert txt_candidate_names == ["labchart_text", "pcc"]
