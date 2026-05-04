"""eval/diff_models.py — Data models for comparison and diagnostics."""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


def _is_numeric_metric_value(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _derive_metric_diff(actual: Any, target: Any) -> Optional[float]:
    if _is_numeric_metric_value(actual) and _is_numeric_metric_value(target):
        return abs(float(actual) - float(target))
    return None


def _derive_metric_passed(
    actual: Any,
    target: Any,
    *,
    diff: Optional[float],
    tolerance: Optional[float],
) -> Optional[bool]:
    if tolerance is not None and diff is not None:
        return diff <= tolerance
    if target is not None and not _both_numeric_metric_values(actual, target):
        return actual == target
    return None


def _both_numeric_metric_values(actual: Any, target: Any) -> bool:
    return _is_numeric_metric_value(actual) and _is_numeric_metric_value(target)


class SemanticDiff(BaseModel):
    """Semantic layer difference between reference and candidate."""

    schema_version: str = "1.0"
    por_lexical_similarity: float
    grv_anchor_match: float
    delta_e_profile_alignment: float
    instrumentation_context_alignment: float
    overall: float
    details: Dict[str, str] = Field(default_factory=dict)


class MetricDiff(BaseModel):
    """Generic metric comparison item."""

    name: str
    actual: Any = None
    target: Any = None
    diff: Optional[float] = None
    unit: Optional[str] = None
    tolerance: Optional[float] = None
    passed: Optional[bool] = None

    @model_validator(mode="after")
    def derive_diff_and_passed(self) -> "MetricDiff":
        if self.diff is None:
            self.diff = _derive_metric_diff(self.actual, self.target)
        if self.passed is None:
            self.passed = _derive_metric_passed(
                self.actual,
                self.target,
                diff=self.diff,
                tolerance=self.tolerance,
            )
        return self


class PhysicalDiff(BaseModel):
    """Physical layer difference between reference and candidate."""

    schema_version: str = "1.0"
    domain: str = "music"
    bpm_diff: Optional[float] = None
    key_match: bool = False
    rms_diff: float = 0.0
    valley_diff: float = 0.0
    active_rate_diff: float = 0.0
    thickness_diff: float = 0.0
    spectral_centroid_diff: float = 0.0
    metrics: Dict[str, MetricDiff] = Field(default_factory=dict)
    overall: float = 0.0
    details: Dict[str, str] = Field(default_factory=dict)

    LEGACY_METRIC_FIELDS: ClassVar[tuple[str, ...]] = (
        "bpm_diff",
        "key_match",
        "rms_diff",
        "valley_diff",
        "active_rate_diff",
        "thickness_diff",
        "spectral_centroid_diff",
    )

    @model_validator(mode="after")
    def populate_generic_metrics(self) -> "PhysicalDiff":
        if self.metrics:
            return self
        legacy_fields_set = set(self.LEGACY_METRIC_FIELDS) & self.model_fields_set
        if not legacy_fields_set:
            return self
        for field_name in self.LEGACY_METRIC_FIELDS:
            if field_name not in legacy_fields_set:
                continue
            value = getattr(self, field_name)
            if field_name == "bpm_diff" and value is None:
                continue
            if field_name.endswith("_match"):
                self.metrics[field_name] = MetricDiff(
                    name=field_name,
                    actual=value,
                    target=True,
                    passed=bool(value),
                )
            else:
                signed = float(value)
                self.metrics[field_name] = MetricDiff(
                    name=field_name,
                    actual=signed,
                    target=0.0,
                    diff=abs(signed),
                )
        return self

    def metric(self, name: str) -> Optional[MetricDiff]:
        return self.metrics.get(name)


class ValleyDiagnostics(BaseModel):
    """Diagnostic data for valley_depth computation."""

    schema_version: str = "1.0"
    method: str
    rms_p90: float = 0.0
    rms_p10: float = 0.0
    ar_main: float = 0.0
    ar_min: float = 0.0
    chorus_sections: List[str] = Field(default_factory=list)
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
    domain: str = "music"
    source_artifact: Optional[Dict[str, Any]] = None
    por_core: str = ""
    por_surface: List[str] = Field(default_factory=list)
    grv_primary: str = ""
    grv_anchors: List[str] = Field(default_factory=list)
    delta_e_profile: str = ""
    bpm: Optional[float] = None
    key: Optional[str] = None
    mode: Optional[str] = None
    duration_sec: Optional[float] = None
    constraints: List[str] = Field(default_factory=list)
    style_tags: List[str] = Field(default_factory=list)
    instrumentation_notes: List[str] = Field(default_factory=list)
    raw_text: str = ""


class ComparisonResult(BaseModel):
    """Full comparison output between reference and candidate."""

    schema_version: str = "1.0"
    semantic_diff: SemanticDiff
    physical_diff: PhysicalDiff
    action_hints: List[str]
    overall_score: float
    mode: str = "compare"
    reference_source: str = ""
    candidate_source: str = ""
