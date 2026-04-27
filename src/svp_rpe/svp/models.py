"""svp/models.py - SVP data models (Pydantic)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


class SourceArtifact(BaseModel):
    """Domain-neutral source artifact metadata."""

    schema_version: str = "1.0"
    type: str = "artifact"
    path: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DataLineage(BaseModel):
    """Provenance tracking for SVP generation."""

    schema_version: str = "1.0"
    source_artifact: Optional[SourceArtifact] = None
    source_audio: Optional[str] = Field(
        default=None,
        exclude=True,
        description="Legacy audio-only alias. YAML output uses source_artifact.",
    )
    rpe_version: str = "1.0"
    svp_version: str = "1.0"
    generation_method: str = "deterministic_rule_based"

    @model_validator(mode="after")
    def ensure_source_artifact(self) -> "DataLineage":
        if self.source_artifact is None and self.source_audio:
            self.source_artifact = SourceArtifact(
                type="audio",
                path=self.source_audio,
                metadata={"legacy_field": "source_audio"},
            )
        if self.source_artifact is None:
            raise ValueError("data_lineage requires source_artifact or legacy source_audio")
        if self.source_audio is None:
            self.source_audio = self.source_artifact.path
        return self


class AnalysisRPE(BaseModel):
    """RPE summary embedded in SVP for reference."""

    schema_version: str = "1.0"
    por_core: str
    por_surface: List[str] = Field(default_factory=list)
    grv_primary: str
    bpm: Optional[float] = None
    key: Optional[str] = None
    mode: Optional[str] = None
    duration_sec: float
    structure_summary: str
    domain_features: Dict[str, Any] = Field(default_factory=dict)


class SVPForGeneration(BaseModel):
    """Generation instructions derived from RPE."""

    schema_version: str = "1.0"
    prompt_text: str
    constraints: List[str] = Field(default_factory=list)
    style_tags: List[str] = Field(default_factory=list)
    tempo_range: Optional[str] = None
    key_suggestion: Optional[str] = None
    generation_hints: Dict[str, Any] = Field(default_factory=dict)


class EvaluationCriteria(BaseModel):
    """Criteria for evaluating generated output against RPE."""

    schema_version: str = "1.0"
    por_check: str
    grv_check: str
    delta_e_check: str
    physical_checks: List[str] = Field(default_factory=list)
    metric_checks: Dict[str, Any] = Field(default_factory=dict)


class MinimalSVP(BaseModel):
    """Compact SVP for quick reference."""

    schema_version: str = "1.0"
    c: str
    g: List[str] = Field(default_factory=list)
    de: str


class SVPBundle(BaseModel):
    """Complete SVP output."""

    schema_version: str = "1.0"
    domain: str = "music"
    data_lineage: DataLineage
    analysis_rpe: AnalysisRPE
    svp_for_generation: SVPForGeneration
    evaluation_criteria: EvaluationCriteria
    minimal_svp: MinimalSVP
