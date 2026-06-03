"""Deterministic semantic CI core for SVP/RPE round trips."""
from __future__ import annotations

from svp_rpe.semantic_ci.core import (
    apply_repair_svp,
    compare_expected_observed,
    generate_expected_rpe,
    generate_repair_svp,
    run_semantic_ci,
    stable_hash,
)
from svp_rpe.semantic_ci.models import (
    ExpectedRPE,
    MetricDiff,
    ObservedRPE,
    RepairAction,
    RepairSVP,
    RoundTripLog,
    RoundTripStep,
    SemanticCIRun,
    SemanticDiff,
    TargetSVP,
)
from svp_rpe.semantic_ci.audit import (
    AuditNeedle,
    AuditReport,
    build_audit_report,
    render_audit_text,
)
from svp_rpe.semantic_ci.observed_adapter import rpe_bundle_to_observed
from svp_rpe.semantic_ci.report import render_markdown

__all__ = [
    "AuditNeedle",
    "AuditReport",
    "ExpectedRPE",
    "MetricDiff",
    "ObservedRPE",
    "RepairAction",
    "RepairSVP",
    "RoundTripLog",
    "RoundTripStep",
    "SemanticCIRun",
    "SemanticDiff",
    "TargetSVP",
    "apply_repair_svp",
    "build_audit_report",
    "compare_expected_observed",
    "generate_expected_rpe",
    "generate_repair_svp",
    "render_audit_text",
    "render_markdown",
    "rpe_bundle_to_observed",
    "run_semantic_ci",
    "stable_hash",
]
