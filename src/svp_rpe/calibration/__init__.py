"""Genre calibration corpus analysis."""
from __future__ import annotations

from .analyze import (
    CALIB_FEATURES,
    MIN_SAMPLES_PER_GENRE,
    ExcludedSample,
    GenreCalibrationReport,
    GenreFeatureStats,
    GenreStats,
    PairSeparability,
    ThresholdCandidate,
    run_genre_calibration,
)
from .audit import (
    GENRE_CONTEXT_EXPECTATIONS,
    MisfireAuditReport,
    SamplePrediction,
    run_genre_misfire_audit,
)
from .manifest import GenreCorpusManifest, GenreSample, load_genre_manifest
from .render import render_genre_report_text, render_misfire_audit_text

__all__ = [
    "CALIB_FEATURES",
    "GENRE_CONTEXT_EXPECTATIONS",
    "MIN_SAMPLES_PER_GENRE",
    "ExcludedSample",
    "GenreCalibrationReport",
    "GenreCorpusManifest",
    "GenreFeatureStats",
    "GenreSample",
    "GenreStats",
    "MisfireAuditReport",
    "PairSeparability",
    "SamplePrediction",
    "ThresholdCandidate",
    "load_genre_manifest",
    "render_genre_report_text",
    "render_misfire_audit_text",
    "run_genre_calibration",
    "run_genre_misfire_audit",
]
