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
def long_txt_file() -> str:
    """A real LabChart-text-exported ECG file with enough beats (163) for the
    unsupervised clustering method's default min_samples=30 to run
    meaningfully, unlike 57.txt's 15 beats which would degrade to all-noise
    clustering."""
    return os.path.join(
        REPO_ROOT, "src", "physiology_analysis_tools", "examples", "9 long.txt"
    )


@pytest.fixture
def adicht_examples_dir() -> str:
    return os.path.join(
        REPO_ROOT, "src", "physiology_analysis_tools", "examples", "ECG_traces"
    )
