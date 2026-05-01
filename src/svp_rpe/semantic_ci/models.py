"""Pydantic models for the semantic CI Phase 1 core."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def _normalize_signal(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def normalize_signals(values: List[str]) -> List[str]:
    normalized = []
    for value in values:
        signal = _normalize_signal(value)
        if signal:
            normalized.append(signal)
    return sorted(dict.fromkeys(normalized))


class TargetSVP(BaseModel):
    """Human-authored target specification for generation and post-checks."""

    schema_version: str = "1.0"
    id: str
    domain: str = "generic"
    core: str
    surface: List[str] = Field(default_factory=list)
    grv: List[str] = Field(default_factory=list)
    delta_e_profile: str = ""
    preserve: List[str] = Field(default_factory=list)
    avoid: List[str] = Field(default_factory=list)
    lock: List[str] = Field(default_factory=list)
    metric_targets: Dict[str, Any] = Field(default_factory=dict)
    tolerances: Dict[str, float] = Field(default_factory=dict)
    change_budget: int = Field(default=3, ge=0)
    notes: List[str] = Field(default_factory=list)

    @field_validator("domain", "core", "delta_e_profile")
    @classmethod
    def normalize_scalar(cls, value: str) -> str:
        return _normalize_signal(value)

    @field_validator("surface", "grv", "preserve", "avoid", "lock", "notes")
    @classmethod
    def normalize_list(cls, values: List[str]) -> List[str]:
        return normalize_signals(values)

    @model_validator(mode="after")
    def include_core_in_preserve(self) -> "TargetSVP":
        if self.core:
            self.preserve = normalize_signals([*self.preserve, self.core])
        return self


class ExpectedRPE(BaseModel):
    """Deterministic expectation generated from a TargetSVP."""

    schema_version: str = "1.0"
    source_svp_id: str
    domain: str = "generic"
    required_signals: List[str] = Field(default_factory=list)
    allowed_signals: List[str] = Field(default_factory=list)
    prohibited_signals: List[str] = Field(default_factory=list)
    locked_signals: List[str] = Field(default_factory=list)
    metric_targets: Dict[str, Any] = Field(default_factory=dict)
    tolerances: Dict[str, float] = Field(default_factory=dict)
    change_budget: int = 3
    source_hash: str = ""

    @field_validator(
        "required_signals",
        "allowed_signals",
        "prohibited_signals",
        "locked_signals",
    )
    @classmethod
    def normalize_list(cls, values: List[str]) -> List[str]:
        return normalize_signals(values)


class ObservedRPE(BaseModel):
    """Fixture or measured RPE used as the observed artifact state."""

    schema_version: str = "1.0"
    id: str
    domain: str = "generic"
    signals: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    source: str = "fixture"

    @field_validator("domain", "source")
    @classmethod
    def normalize_scalar(cls, value: str) -> str:
        return _normalize_signal(value)

    @field_validator("signals")
    @classmethod
    def normalize_list(cls, values: List[str]) -> List[str]:
        return normalize_signals(values)


class MetricDiff(BaseModel):
    """Single expected-vs-observed metric comparison."""

    name: str
    expected: Any = None
    observed: Any = None
    tolerance: Optional[float] = None
    diff: Optional[float] = None
    passed: bool = False


class SemanticDiff(BaseModel):
    """Diagnostic diff between ExpectedRPE and ObservedRPE."""

    schema_version: str = "1.0"
    missing: List[str] = Field(default_factory=list)
    preserved: List[str] = Field(default_factory=list)
    over_changed: List[str] = Field(default_factory=list)
    metric_diffs: List[MetricDiff] = Field(default_factory=list)
    loss: float = 0.0
    threshold: float = 0.0
    verdict: Literal["pass", "repair"] = "pass"


class RepairAction(BaseModel):
    """Single deterministic repair operation."""

    op: Literal["preserve", "restore", "reduce", "lock"]
    signal: str
    applied: bool = True

    @field_validator("signal")
    @classmethod
    def normalize_signal(cls, value: str) -> str:
        return _normalize_signal(value)


class RepairSVP(BaseModel):
    """Structured SVP repair plan generated from a SemanticDiff."""

    schema_version: str = "1.0"
    source_svp_id: str
    change_budget: int = Field(default=3, ge=0)
    preserve: List[str] = Field(default_factory=list)
    restore: List[str] = Field(default_factory=list)
    reduce: List[str] = Field(default_factory=list)
    lock: List[str] = Field(default_factory=list)
    deferred_restore: List[str] = Field(default_factory=list)
    deferred_reduce: List[str] = Field(default_factory=list)
    repair_order: List[RepairAction] = Field(default_factory=list)


class RoundTripStep(BaseModel):
    """Stable state transition record for one semantic CI phase."""

    name: str
    input_hash: str = ""
    output_hash: str


class RoundTripLog(BaseModel):
    """Deterministic trace of a semantic CI round trip."""

    schema_version: str = "1.0"
    target_svp_hash: str
    expected_rpe_hash: str
    observed_rpe_hash: str
    semantic_diff_hash: str
    repair_svp_hash: str
    transitions: List[RoundTripStep]
    final_hash: str


class SemanticCIRun(BaseModel):
    """Full semantic CI output bundle."""

    schema_version: str = "1.0"
    target_svp: TargetSVP
    expected_rpe: ExpectedRPE
    observed_rpe: ObservedRPE
    semantic_diff: SemanticDiff
    repair_svp: RepairSVP
    repaired_svp: TargetSVP
    roundtrip_log: RoundTripLog
