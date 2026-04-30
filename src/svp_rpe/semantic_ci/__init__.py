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
from svp_rpe.semantic_ci.report import render_markdown

__all__ = [
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
    "compare_expected_observed",
    "generate_expected_rpe",
    "generate_repair_svp",
    "render_markdown",
    "run_semantic_ci",
    "stable_hash",
]
