"""eval/diff_models.py — Data models for comparison and diagnostics."""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel


class SemanticDiff(BaseModel):
    """Semantic layer difference between reference and candidate."""

    schema_version: str = "1.0"
    por_lexical_similarity: float    # [0,1] token/synonym overlap of por_core
    grv_anchor_match: float          # [0,1] anchor alignment
    delta_e_profile_alignment: float  # [0,1] energy profile match
    instrumentation_context_alignment: float  # [0,1]
    overall: float
    details: Dict[str, str] = {}


class PhysicalDiff(BaseModel):
    """Physical layer difference between reference and candidate."""

    schema_version: str = "1.0"
    bpm_diff: Optional[float] = None
    key_match: bool = False
    rms_diff: float = 0.0
    valley_diff: float = 0.0
    active_rate_diff: float = 0.0
    thickness_diff: float = 0.0
    spectral_centroid_diff: float = 0.0
    overall: float = 0.0
    details: Dict[str, str] = {}


class ValleyDiagnostics(BaseModel):
    """Diagnostic data for valley_depth computation."""

    schema_version: str = "1.0"
    method: str                    # "rms_percentile" | "section_ar" | "hybrid"
    rms_p90: float = 0.0
    rms_p10: float = 0.0
    ar_main: float = 0.0
    ar_min: float = 0.0
    chorus_sections: List[str] = []
    lowest_section: str = ""
    confidence: float = 0.5
    rms_percentile_value: float = 0.0
    section_ar_value: float = 0.0
    hybrid_value: float = 0.0


class SectionFeature(BaseModel):
    """Per-section feature vector."""

    label: str
    start_sec: float
    end_sec: float
    rms_mean: float = 0.0
    active_rate: float = 0.0
    spectral_centroid: float = 0.0
    onset_density: float = 0.0
    spectral_flux_mean: float = 0.0
    chroma_change: float = 0.0


class ParsedSVP(BaseModel):
    """Parsed external SVP file."""

    schema_version: str = "1.0"
    por_core: str = ""
    por_surface: List[str] = []
    grv_primary: str = ""
    grv_anchors: List[str] = []
    delta_e_profile: str = ""
    bpm: Optional[float] = None
    key: Optional[str] = None
    mode: Optional[str] = None
    duration_sec: Optional[float] = None
    constraints: List[str] = []
    style_tags: List[str] = []
    instrumentation_notes: List[str] = []
    raw_text: str = ""


class ComparisonResult(BaseModel):
    """Full comparison output between reference and candidate."""

    schema_version: str = "1.0"
    semantic_diff: SemanticDiff
    physical_diff: PhysicalDiff
    action_hints: List[str]
    overall_score: float
    mode: str = "compare"  # "self" | "compare"
    reference_source: str = ""
    candidate_source: str = ""
