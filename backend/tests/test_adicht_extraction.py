import os

import pytest

pytest.importorskip(
    "adi", reason="adi-reader requires a native extension not available on this machine"
)

from backend.channel_selection import select_default_channel
from backend.extractors import extract_dataframe


def test_extract_dataframe_loads_numeric_filename_adicht_file(adicht_examples_dir):
    path = os.path.join(adicht_examples_dir, "556420.adicht")

    df = extract_dataframe(path)

    assert "channel 1" in df.columns
    selection = select_default_channel(os.path.basename(path), list(df.columns))
    assert selection.channel == "channel 1"
    assert selection.matched_rule is True
