"""Genre calibration corpus analysis."""
from __future__ import annotations

from .analyze import (
    CALIB_FEATURES,
    MIN_SAMPLES_PER_GENRE,
    GenreCalibrationReport,
    GenreFeatureStats,
    GenreStats,
    PairSeparability,
    ThresholdCandidate,
    run_genre_calibration,
)
from .manifest import GenreCorpusManifest, GenreSample, load_genre_manifest
from .render import render_genre_report_text

__all__ = [
    "CALIB_FEATURES",
    "MIN_SAMPLES_PER_GENRE",
    "GenreCalibrationReport",
    "GenreCorpusManifest",
    "GenreFeatureStats",
    "GenreSample",
    "GenreStats",
    "PairSeparability",
    "ThresholdCandidate",
    "load_genre_manifest",
    "render_genre_report_text",
    "run_genre_calibration",
]
