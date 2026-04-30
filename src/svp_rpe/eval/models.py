"""eval/models.py — Evaluation score models (Pydantic)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_serializer


class RPEScore(BaseModel):
    """RPE physical quality score against Pro baseline."""

    schema_version: str = "1.2"
    baseline_profile: str = "pro"
    rms_score: float
    active_rate_score: float
    crest_factor_score: float
    valley_score: float
    thickness_score: float
    overall: float
    stem_scores: dict[str, "RPEScore"] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def omit_empty_stem_scores(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if not self.stem_scores:
            data.pop("stem_scores", None)
        return data


class UGHerScore(BaseModel):
    """UGHer semantic consistency score."""

    schema_version: str = "1.0"
    por_similarity: float
    grv_consistency: float
    delta_e_assessment: float
    physical_accuracy: float
    overall: float


class IntegratedScore(BaseModel):
    """Weighted integrated score combining UGHer and RPE."""

    schema_version: str = "1.0"
    ugher_score: float
    rpe_score: float
    integrated_score: float
    ugher_weight: float = 0.5
    rpe_weight: float = 0.5
