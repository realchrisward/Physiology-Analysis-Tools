import os

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
def example_txt_file() -> str:
    """A real LabChart-text-exported ECG file, safe to parse on any OS."""
    return os.path.join(
        REPO_ROOT, "src", "physiology_analysis_tools", "examples", "10.txt"
    )


@pytest.fixture
def real_beats_txt_file() -> str:
    """A real LabChart-text-exported ECG file that reliably produces real
    beats with default settings (unlike 10.txt, which is real but too short
    to produce any)."""
    return os.path.join(
        REPO_ROOT, "src", "physiology_analysis_tools", "examples", "57.txt"
    )


@pytest.fixture
def adicht_examples_dir() -> str:
    return os.path.join(
        REPO_ROOT, "src", "physiology_analysis_tools", "examples", "ECG_traces"
    )
