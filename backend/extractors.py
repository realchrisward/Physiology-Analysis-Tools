import importlib
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractorSpec:
    name: str
    module_path: str
    ext: str


# Order matters: for extensions shared by two extractors (.txt is used by
# both labchart_text and pcc), candidates are tried in this order and the
# first one whose SASSI_extract() succeeds wins - mirrors the old
# main.py's extractor dict ordering.
EXTRACTOR_SPECS: list[ExtractorSpec] = [
    ExtractorSpec(
        "adi", "physiology_analysis_tools.modules.signal_converters.adi_extract", ".adicht"
    ),
    ExtractorSpec(
        "labchart_text",
        "physiology_analysis_tools.modules.signal_converters.labchart_text_extract",
        ".txt",
    ),
    ExtractorSpec(
        "dsi_fp_matlab",
        "physiology_analysis_tools.modules.signal_converters.dsi_fp_matlab_extract",
        ".mat",
    ),
    ExtractorSpec(
        "pklgzip",
        "physiology_analysis_tools.modules.signal_converters.pklgzip_extract",
        ".gzip",
    ),
    ExtractorSpec(
        "pcc", "physiology_analysis_tools.modules.signal_converters.pcc_extract", ".txt"
    ),
]

EXTRACTOR_LOAD_ERRORS: dict[str, str] = {}

try:
    importlib.import_module(
        "physiology_analysis_tools.modules.signal_converters.edf_extract"
    )
    EXTRACTOR_SPECS.append(
        ExtractorSpec(
            "edf",
            "physiology_analysis_tools.modules.signal_converters.edf_extract",
            ".edf",
        )
    )
except ImportError as e:
    # Record the real cause instead of assuming a specific one (e.g. missing
    # pyedflib) - any ImportError while importing edf_extract lands here,
    # including one raised from deep inside its own import chain.
    EXTRACTOR_LOAD_ERRORS["edf"] = str(e)


class NoExtractorSucceededError(Exception):
    def __init__(self, filepath: str, errors: dict[str, str]):
        self.filepath = filepath
        self.errors = errors
        super().__init__(f"No extractor could load {filepath}: {errors}")


def extract_dataframe(filepath: str):
    ext = os.path.splitext(filepath)[1].lower()
    candidates = [spec for spec in EXTRACTOR_SPECS if spec.ext == ext]

    errors: dict[str, str] = {}
    for spec in candidates:
        try:
            module = importlib.import_module(spec.module_path)
            return module.SASSI_extract(filepath)
        except Exception as e:  # deliberately broad - mirrors main.py's per-extractor fallback
            errors[spec.name] = str(e)

    raise NoExtractorSucceededError(filepath, errors)
