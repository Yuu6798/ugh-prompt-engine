"""Deterministic genre calibration analysis."""
from __future__ import annotations

import math
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from svp_rpe.calibration.manifest import GenreCorpusManifest, GenreSample
from svp_rpe.perform import sha256_bytes
from svp_rpe.rpe.semantic_rules import _feature_value

MIN_SAMPLES_PER_GENRE = 3

SPECTRAL_BAND_FEATURES: tuple[str, ...] = (
    "spectral_bands.sub_bass",
    "spectral_bands.bass",
    "spectral_bands.low_mid",
    "spectral_bands.mid",
    "spectral_bands.high_mid",
    "spectral_bands.presence",
    "spectral_bands.brilliance",
)

CALIB_FEATURES: tuple[str, ...] = (
    "harmonic_ratio",
    "percussive_ratio",
    "spectral_centroid",
    "dynamic_range_db",
    "valley_depth",
    "low_ratio",
    "mid_ratio",
    "high_ratio",
    *SPECTRAL_BAND_FEATURES,
)

GenreStatus = Literal["sufficient", "insufficient"]
PairStatus = Literal["candidate", "overlap", "insufficient", "zero_variance"]


class GenreFeatureStats(BaseModel):
    """Summary stats for one feature in one genre."""

    model_config = ConfigDict(extra="forbid")

    count: int
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    std: float | None = None


class GenreStats(BaseModel):
    """Per-genre sample count and feature statistics."""

    model_config = ConfigDict(extra="forbid")

    genre: str
    sample_count: int
    status: GenreStatus
    features: dict[str, GenreFeatureStats] = Field(default_factory=dict)


class ThresholdCandidate(BaseModel):
    """One non-overlapping one-dimensional threshold candidate."""

    model_config = ConfigDict(extra="forbid")

    genre_a: str
    genre_b: str
    feature: str
    threshold: float
    lower_genre: str
    higher_genre: str
    direction: str
    d: float | None = None


class PairSeparability(BaseModel):
    """One feature's separability for one genre pair."""

    model_config = ConfigDict(extra="forbid")

    genre_a: str
    genre_b: str
    feature: str
    count_a: int
    count_b: int
    d: float | None = None
    overlap: bool | None = None
    status: PairStatus
    threshold_candidate: ThresholdCandidate | None = None


class ExcludedSample(BaseModel):
    """A manifest sample excluded from analysis."""

    model_config = ConfigDict(extra="forbid")

    id: str
    genre_label: str
    reason: str


class GenreCalibrationReport(BaseModel):
    """Genre calibration report with descriptive stats and candidate cuts."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    min_samples_per_genre: int = MIN_SAMPLES_PER_GENRE
    genres: dict[str, GenreStats] = Field(default_factory=dict)
    pair_separability: list[PairSeparability] = Field(default_factory=list)
    threshold_candidates: list[ThresholdCandidate] = Field(default_factory=list)
    excluded_samples: list[ExcludedSample] = Field(default_factory=list)


def run_genre_calibration(
    manifest: GenreCorpusManifest,
    *,
    repo_root: str | Path | None = None,
) -> GenreCalibrationReport:
    """Analyze a genre-labeled manifest without producing any verdict."""

    samples_by_genre: dict[str, list[dict[str, float | None]]] = {
        genre: [] for genre in sorted({sample.genre_label for sample in manifest.samples})
    }
    excluded: list[ExcludedSample] = []
    root = Path(repo_root) if repo_root is not None else _repo_root()

    for sample in sorted(manifest.samples, key=lambda item: item.id):
        if sample.excluded:
            excluded.append(
                ExcludedSample(
                    id=sample.id,
                    genre_label=sample.genre_label,
                    reason="excluded",
                )
            )
            continue
        measured = _sample_measured(sample, repo_root=root)
        if measured is None:
            excluded.append(
                ExcludedSample(
                    id=sample.id,
                    genre_label=sample.genre_label,
                    reason="no_measured_or_audio",
                )
            )
            continue
        samples_by_genre.setdefault(sample.genre_label, []).append(
            _feature_vector_from_measured(measured)
        )

    genres = {
        genre: _genre_stats(genre, vectors)
        for genre, vectors in sorted(samples_by_genre.items())
    }
    pair_separability, threshold_candidates = _pair_separability(genres)
    return GenreCalibrationReport(
        genres=genres,
        pair_separability=pair_separability,
        threshold_candidates=threshold_candidates,
        excluded_samples=excluded,
    )


def _sample_measured(
    sample: GenreSample,
    *,
    repo_root: Path,
) -> dict[str, Any] | None:
    audio_path = _resolve_audio_path(sample, repo_root=repo_root)
    if audio_path is not None:
        from svp_rpe.rpe.extractor import extract_rpe_from_file

        bundle = extract_rpe_from_file(str(audio_path))
        return bundle.physical.model_dump(mode="json")
    if sample.measured is not None:
        return dict(sample.measured)
    return None


def _resolve_audio_path(sample: GenreSample, *, repo_root: Path) -> Path | None:
    if not sample.audio_locator or not sample.audio_hash:
        return None
    locator = Path(sample.audio_locator)
    candidate = locator if locator.is_absolute() else (repo_root / locator).resolve()
    try:
        candidate.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    if sha256_bytes(candidate.read_bytes()) != sample.audio_hash:
        return None
    return candidate


def _feature_vector_from_measured(measured: dict[str, Any]) -> dict[str, float | None]:
    proxy = _measurement_proxy(measured)
    vector: dict[str, float | None] = {}
    for feature in CALIB_FEATURES:
        value = _feature_value(feature, proxy)
        vector[feature] = float(value) if isinstance(value, int | float) else None
    return vector


def _measurement_proxy(measured: dict[str, Any]) -> Any:
    data = dict(measured)
    profile_data = measured.get("spectral_profile")
    profile = dict(profile_data) if isinstance(profile_data, dict) else {}
    for key in ("low_ratio", "mid_ratio", "high_ratio", "brightness", "centroid"):
        profile.setdefault(key, measured.get(key))
    profile.setdefault("centroid", measured.get("spectral_centroid"))
    data["spectral_profile"] = SimpleNamespace(**profile)

    band_data = measured.get("spectral_bands")
    bands = dict(band_data) if isinstance(band_data, dict) else {}
    for feature in SPECTRAL_BAND_FEATURES:
        _, _, band = feature.partition(".")
        if band in measured:
            bands.setdefault(band, measured.get(band))
    data["spectral_bands"] = SimpleNamespace(**bands) if bands else None
    return SimpleNamespace(**data)


def _genre_stats(genre: str, vectors: list[dict[str, float | None]]) -> GenreStats:
    features = {
        feature: _feature_stats([vector.get(feature) for vector in vectors])
        for feature in CALIB_FEATURES
    }
    sample_count = len(vectors)
    return GenreStats(
        genre=genre,
        sample_count=sample_count,
        status="sufficient" if sample_count >= MIN_SAMPLES_PER_GENRE else "insufficient",
        features=features,
    )


def _feature_stats(values: list[float | None]) -> GenreFeatureStats:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return GenreFeatureStats(count=0)
    arr = np.array(clean, dtype=float)
    return GenreFeatureStats(
        count=len(clean),
        min=_round_float(np.min(arr)),
        max=_round_float(np.max(arr)),
        mean=_round_float(np.mean(arr)),
        std=_round_float(np.std(arr)),
    )


def _pair_separability(
    genres: dict[str, GenreStats],
) -> tuple[list[PairSeparability], list[ThresholdCandidate]]:
    rows: list[PairSeparability] = []
    candidates: list[ThresholdCandidate] = []
    for genre_a, genre_b in combinations(sorted(genres), 2):
        stats_a = genres[genre_a]
        stats_b = genres[genre_b]
        pair_rows: list[PairSeparability] = []
        for feature in CALIB_FEATURES:
            row = _compare_feature(stats_a, stats_b, feature)
            pair_rows.append(row)
            if row.threshold_candidate is not None:
                candidates.append(row.threshold_candidate)
        rows.extend(_rank_pair_rows(pair_rows))
    candidates.sort(key=lambda item: (_candidate_sort_key(item), item.feature, item.genre_a, item.genre_b))
    return rows, candidates


def _compare_feature(
    stats_a: GenreStats,
    stats_b: GenreStats,
    feature: str,
) -> PairSeparability:
    feature_a = stats_a.features[feature]
    feature_b = stats_b.features[feature]
    if (
        stats_a.status == "insufficient"
        or stats_b.status == "insufficient"
        or feature_a.count < MIN_SAMPLES_PER_GENRE
        or feature_b.count < MIN_SAMPLES_PER_GENRE
    ):
        return PairSeparability(
            genre_a=stats_a.genre,
            genre_b=stats_b.genre,
            feature=feature,
            count_a=feature_a.count,
            count_b=feature_b.count,
            status="insufficient",
        )

    assert feature_a.mean is not None
    assert feature_b.mean is not None
    assert feature_a.std is not None
    assert feature_b.std is not None
    assert feature_a.min is not None
    assert feature_a.max is not None
    assert feature_b.min is not None
    assert feature_b.max is not None

    pooled_std = math.sqrt((feature_a.std**2 + feature_b.std**2) / 2.0)
    d = None if pooled_std == 0.0 else _round_float(abs(feature_a.mean - feature_b.mean) / pooled_std)
    overlap = max(feature_a.min, feature_b.min) <= min(feature_a.max, feature_b.max)
    candidate = None
    status: PairStatus
    if overlap:
        status = "overlap"
    else:
        status = "candidate"
        candidate = _threshold_candidate(stats_a, stats_b, feature, d)
    if d is None and candidate is None:
        status = "zero_variance"
    return PairSeparability(
        genre_a=stats_a.genre,
        genre_b=stats_b.genre,
        feature=feature,
        count_a=feature_a.count,
        count_b=feature_b.count,
        d=d,
        overlap=overlap,
        status=status,
        threshold_candidate=candidate,
    )


def _threshold_candidate(
    stats_a: GenreStats,
    stats_b: GenreStats,
    feature: str,
    d: float | None,
) -> ThresholdCandidate:
    feature_a = stats_a.features[feature]
    feature_b = stats_b.features[feature]
    assert feature_a.mean is not None
    assert feature_a.min is not None
    assert feature_a.max is not None
    assert feature_b.mean is not None
    assert feature_b.min is not None
    assert feature_b.max is not None

    if feature_a.mean < feature_b.mean:
        lower_genre = stats_a.genre
        higher_genre = stats_b.genre
        lower_max = feature_a.max
        higher_min = feature_b.min
    else:
        lower_genre = stats_b.genre
        higher_genre = stats_a.genre
        lower_max = feature_b.max
        higher_min = feature_a.min
    threshold = _round_float((lower_max + higher_min) / 2.0)
    return ThresholdCandidate(
        genre_a=stats_a.genre,
        genre_b=stats_b.genre,
        feature=feature,
        threshold=threshold,
        lower_genre=lower_genre,
        higher_genre=higher_genre,
        direction=f"{higher_genre} > {lower_genre}",
        d=d,
    )


def _rank_pair_rows(rows: list[PairSeparability]) -> list[PairSeparability]:
    return sorted(rows, key=lambda item: (_row_sort_key(item), item.feature))


def _row_sort_key(row: PairSeparability) -> tuple[int, float]:
    if row.d is None:
        return (1, 0.0)
    return (0, -row.d)


def _candidate_sort_key(candidate: ThresholdCandidate) -> tuple[int, float]:
    if candidate.d is None:
        return (1, 0.0)
    return (0, -candidate.d)


def _round_float(value: Any) -> float:
    return round(float(value), 6)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]
