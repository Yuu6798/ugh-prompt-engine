"""Audit current genre rules against labeled calibration samples."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from svp_rpe.calibration.analyze import (
    ExcludedSample,
    _measurement_proxy,
    _repo_root,
    _sample_measured,
)
from svp_rpe.calibration.manifest import GenreCorpusManifest
from svp_rpe.rpe.semantic_rules import (
    _backfill_genre_sections,
    _infer_cultural_context,
    _infer_instrumentation,
    load_config,
)

# Descriptive reference labels for mismatch markers only. This is not a verdict
# table, and labels absent from this map are never marked mismatched.
GENRE_CONTEXT_EXPECTATIONS: dict[str, set[str]] = {
    "orchestral": {"cinematic/orchestral"},
    "electronic": {"bass-music", "electronic/dance", "high-energy-electronic"},
    "electronic-dance": {"bass-music", "electronic/dance", "high-energy-electronic"},
}


class SamplePrediction(BaseModel):
    """One sample's current-rule genre prediction."""

    model_config = ConfigDict(extra="forbid")

    id: str
    genre_label: str
    predicted_cultural_context: list[str] = Field(default_factory=list)
    predicted_instrumentation: str
    mismatch: bool = False


class MisfireAuditReport(BaseModel):
    """Descriptive audit of current genre-rule outputs."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    confusion: dict[str, dict[str, int]] = Field(default_factory=dict)
    predictions: list[SamplePrediction] = Field(default_factory=list)
    excluded_samples: list[ExcludedSample] = Field(default_factory=list)


def run_genre_misfire_audit(
    manifest: GenreCorpusManifest,
    *,
    repo_root: str | Path | None = None,
) -> MisfireAuditReport:
    """Apply production genre rules to a labeled manifest without scoring it."""

    config = _genre_rule_config()
    root = Path(repo_root) if repo_root is not None else _repo_root()
    confusion: dict[str, defaultdict[str, int]] = {}
    predictions: list[SamplePrediction] = []
    excluded: list[ExcludedSample] = []

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

        proxy = _measurement_proxy(measured)
        contexts = _infer_cultural_context(proxy, config)
        instrumentation = _infer_instrumentation(proxy, config)
        predictions.append(
            SamplePrediction(
                id=sample.id,
                genre_label=sample.genre_label,
                predicted_cultural_context=contexts,
                predicted_instrumentation=instrumentation,
                mismatch=_mismatch(sample.genre_label, contexts),
            )
        )

        genre_bucket = confusion.setdefault(sample.genre_label, defaultdict(int))
        for context in contexts:
            genre_bucket[context] += 1

    return MisfireAuditReport(
        confusion={
            genre: dict(sorted(contexts.items()))
            for genre, contexts in sorted(confusion.items())
        },
        predictions=predictions,
        excluded_samples=excluded,
    )


def _genre_rule_config() -> dict[str, Any]:
    try:
        config = load_config("semantic_rules")
    except FileNotFoundError:
        config = {}
    return _backfill_genre_sections(config)


def _mismatch(genre_label: str, contexts: list[str]) -> bool:
    expected = GENRE_CONTEXT_EXPECTATIONS.get(genre_label)
    if expected is None:
        return False
    return not bool(set(contexts) & expected)
