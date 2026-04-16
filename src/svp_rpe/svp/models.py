"""svp/models.py — SVP data models (Pydantic)."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class DataLineage(BaseModel):
    """Provenance tracking for SVP generation."""

    schema_version: str = "1.0"
    source_audio: str
    rpe_version: str = "1.0"
    svp_version: str = "1.0"
    generation_method: str = "deterministic_rule_based"


class AnalysisRPE(BaseModel):
    """RPE summary embedded in SVP for reference."""

    schema_version: str = "1.0"
    por_core: str
    por_surface: List[str]
    grv_primary: str
    bpm: Optional[float] = None
    key: Optional[str] = None
    mode: Optional[str] = None
    duration_sec: float
    structure_summary: str


class SVPForGeneration(BaseModel):
    """Generation instructions derived from RPE."""

    schema_version: str = "1.0"
    prompt_text: str       # structured prompt for generation
    constraints: List[str]
    style_tags: List[str]
    tempo_range: Optional[str] = None
    key_suggestion: Optional[str] = None


class EvaluationCriteria(BaseModel):
    """Criteria for evaluating generated output against RPE."""

    schema_version: str = "1.0"
    por_check: str         # what to verify for PoR
    grv_check: str         # what to verify for grv
    delta_e_check: str     # what to verify for delta_e
    physical_checks: List[str]


class MinimalSVP(BaseModel):
    """Compact SVP for quick reference."""

    c: str                 # core concept
    g: List[str]           # generation constraints
    de: str                # delta_e guidance


class SVPBundle(BaseModel):
    """Complete SVP output."""

    schema_version: str = "1.0"
    data_lineage: DataLineage
    analysis_rpe: AnalysisRPE
    svp_for_generation: SVPForGeneration
    evaluation_criteria: EvaluationCriteria
    minimal_svp: MinimalSVP
