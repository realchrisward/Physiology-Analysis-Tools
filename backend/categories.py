"""Shared category-column constants.

These lists name the per-beat arrhythmia category columns used across
detection, windowing, persistence, and the review-category API. They used to
be independently defined (and drift-prone) in four different files; this
module is the single source of truth.
"""

BASE_CATEGORIES = [
    "bradycardia_absolute",
    "tachycardia_absolute",
    "skipped_beat",
    "prem_beat",
    "abn_cluster",
]

REASSIGNABLE_CATEGORIES = BASE_CATEGORIES + ["other_arrhythmia"]

ALL_OPTIONAL_COLUMNS = REASSIGNABLE_CATEGORIES + ["any_arrhythmia"]
