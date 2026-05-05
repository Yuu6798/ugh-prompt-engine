"""eval/comparison.py — Comparison engine for reference vs candidate evaluation.

Supports self-evaluation and cross-comparison modes.
Generates action hints from detected diffs.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Optional

from svp_rpe.eval.anchor_matcher import grv_anchor_match
from svp_rpe.eval.delta_e_alignment import delta_e_profile_alignment
from svp_rpe.eval.diff_models import (
    ComparisonResult,
    MetricDiff,
    ParsedSVP,
    PhysicalDiff,
    SemanticDiff,
)
from svp_rpe.eval.semantic_similarity import por_lexical_similarity
from svp_rpe.rpe.models import PhysicalRPE, RPEBundle


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _is_numeric_metric_value(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _instrumentation_alignment(notes_a: List[str], notes_b: List[str]) -> float:
    """Token overlap of instrumentation notes."""
    if not notes_a and not notes_b:
        return 1.0
    if not notes_a or not notes_b:
        return 0.0
    set_a = set(" ".join(notes_a).lower().split())
    set_b = set(" ".join(notes_b).lower().split())
    if not set_a or not set_b:
        return 0.0
    return _clamp(len(set_a & set_b) / max(len(set_a), len(set_b)))


def compute_semantic_diff(
    rpe: RPEBundle,
    svp: ParsedSVP,
) -> SemanticDiff:
    """Compute semantic diff between RPE extraction and parsed SVP."""
    sem = rpe.semantic

    por_sim = por_lexical_similarity(sem.por_core, svp.por_core)

    grv_match = grv_anchor_match(
        primary_a=sem.grv_anchor.primary,
        primary_b=svp.grv_primary,
        bpm_a=rpe.physical.bpm,
        bpm_b=svp.bpm,
        key_a=rpe.physical.key,
        key_b=svp.key,
        duration_a=rpe.physical.duration_sec,
        duration_b=svp.duration_sec,
    )

    delta_e_align = delta_e_profile_alignment(
        sem.delta_e_profile.transition_type,
        svp.delta_e_profile if svp.delta_e_profile else "unknown",
        sem.delta_e_profile.intensity,
    )

    instr_align = _instrumentation_alignment(
        [sem.instrumentation_summary] + sem.production_notes,
        svp.instrumentation_notes + svp.style_tags,
    )

    overall = round(
        por_sim * 0.3 + grv_match * 0.3
        + delta_e_align * 0.2 + instr_align * 0.2,
        4,
    )

    return SemanticDiff(
        por_lexical_similarity=round(por_sim, 4),
        grv_anchor_match=round(grv_match, 4),
        delta_e_profile_alignment=round(delta_e_align, 4),
        instrumentation_context_alignment=round(instr_align, 4),
        overall=overall,
    )


def compute_physical_diff(
    phys_ref: PhysicalRPE,
    phys_cand: PhysicalRPE,
) -> PhysicalDiff:
    """Compute physical diff between two PhysicalRPE extractions."""
    bpm_diff = None
    if phys_ref.bpm is not None and phys_cand.bpm is not None:
        bpm_diff = round(phys_cand.bpm - phys_ref.bpm, 2)

    key_match = (
        phys_ref.key is not None
        and phys_cand.key is not None
        and phys_ref.key.lower() == phys_cand.key.lower()
    )

    rms_diff = round(phys_cand.rms_mean - phys_ref.rms_mean, 4)
    valley_diff = round(phys_cand.valley_depth - phys_ref.valley_depth, 4)
    ar_diff = round(phys_cand.active_rate - phys_ref.active_rate, 4)
    thick_diff = round(phys_cand.thickness - phys_ref.thickness, 4)
    sc_diff = round(phys_cand.spectral_centroid - phys_ref.spectral_centroid, 2)

    # Overall: proximity score (closer = better)
    scores = []
    if bpm_diff is not None:
        scores.append(_clamp(1.0 - abs(bpm_diff) / 20.0))
    scores.append(1.0 if key_match else 0.0)
    scores.append(_clamp(1.0 - abs(rms_diff) / 0.3))
    scores.append(_clamp(1.0 - abs(valley_diff) / 0.3))
    scores.append(_clamp(1.0 - abs(ar_diff) / 0.3))

    overall = round(sum(scores) / max(len(scores), 1), 4)

    return PhysicalDiff(
        bpm_diff=bpm_diff,
        key_match=key_match,
        rms_diff=rms_diff,
        valley_diff=valley_diff,
        active_rate_diff=ar_diff,
        thickness_diff=thick_diff,
        spectral_centroid_diff=sc_diff,
        overall=overall,
    )


def compare_metric_values(
    reference: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    metric_names: Optional[Iterable[str]] = None,
    tolerances: Optional[Mapping[str, float]] = None,
    domain: str = "generic",
) -> PhysicalDiff:
    """Compare arbitrary domain metrics into generic MetricDiff entries."""
    tolerance_map = dict(tolerances or {})
    metrics: dict[str, MetricDiff] = {}
    scores: list[float] = []

    def selected_names() -> list[str]:
        if metric_names is not None:
            return list(metric_names)
        return sorted(set(reference.keys()) & set(candidate.keys()))

    def build_metric(name: str) -> MetricDiff | None:
        if name not in reference or name not in candidate:
            return None
        ref_value = reference[name]
        cand_value = candidate[name]
        if ref_value is None or cand_value is None:
            return None
        tolerance = tolerance_map.get(name)
        if _is_numeric_metric_value(ref_value) and _is_numeric_metric_value(cand_value):
            diff = abs(float(cand_value) - float(ref_value))
            passed = diff <= tolerance if tolerance is not None else None
            return MetricDiff(
                name=name,
                actual=cand_value,
                target=ref_value,
                diff=diff,
                tolerance=tolerance,
                passed=passed,
            )

        return MetricDiff(
            name=name,
            actual=cand_value,
            target=ref_value,
            passed=cand_value == ref_value,
        )

    def metric_score(metric: MetricDiff) -> float | None:
        if metric.passed is not None:
            return 1.0 if metric.passed else 0.0
        if metric.diff is not None:
            return 1.0 / (1.0 + metric.diff)
        return None

    for name in selected_names():
        metric = build_metric(name)
        if metric is None:
            continue
        metrics[name] = metric

        score = metric_score(metric)
        if score is not None:
            scores.append(score)

    overall = round(sum(scores) / max(len(scores), 1), 4)
    return PhysicalDiff(domain=domain, metrics=metrics, overall=overall)


# ---------------------------------------------------------------------------
# Action hints generation
# ---------------------------------------------------------------------------


def _physical_action_hints(physical_diff: PhysicalDiff) -> List[str]:
    hints: List[str] = []

    if physical_diff.valley_diff < -0.05:
        hints.append("Bridge/Verse の低密度設計を強化 (valley_depth が低い)")

    if physical_diff.active_rate_diff > 0.05 and physical_diff.valley_diff < 0:
        hints.append("breakdown / silence bar を挿入 (AR高+valley不足)")

    if physical_diff.bpm_diff is not None and abs(physical_diff.bpm_diff) > 10:
        hints.append(f"grv anchor に bpm を明示 (差分: {physical_diff.bpm_diff:+.0f})")
    if not physical_diff.key_match:
        hints.append("grv anchor に key を明示 (key 不一致)")

    return hints


def _semantic_action_hints(semantic_diff: SemanticDiff) -> List[str]:
    hints: List[str] = []

    if semantic_diff.delta_e_profile_alignment < 0.5:
        hints.append("section role と ΔE profile を再記述 (alignment 低)")

    if semantic_diff.instrumentation_context_alignment < 0.3:
        hints.append("SVP の bass-heavy / wide / dense 記述を実測に合わせて調整")

    if semantic_diff.por_lexical_similarity < 0.3:
        hints.append("por_core の意味核を SVP に明示 (lexical similarity 低)")

    if semantic_diff.grv_anchor_match < 0.5:
        hints.append("grv anchor (bpm/key/length/theme) を SVP に反映")

    return hints


def generate_action_hints(
    semantic_diff: SemanticDiff,
    physical_diff: PhysicalDiff,
) -> List[str]:
    """Generate actionable hints from comparison diffs."""
    hints = _physical_action_hints(physical_diff) + _semantic_action_hints(semantic_diff)
    return hints or ["大きな差分なし — 現状維持または微調整"]


# ---------------------------------------------------------------------------
# High-level comparison API
# ---------------------------------------------------------------------------


def compare_rpe_vs_svp(
    rpe: RPEBundle,
    svp: ParsedSVP,
    *,
    candidate_phys: Optional[PhysicalRPE] = None,
) -> ComparisonResult:
    """Compare RPE extraction against a parsed SVP.

    If candidate_phys is provided, physical diff is computed between
    the reference RPE and the candidate's physical features.
    """
    semantic_diff = compute_semantic_diff(rpe, svp)

    if candidate_phys:
        physical_diff = compute_physical_diff(rpe.physical, candidate_phys)
    else:
        # Self-evaluation: no physical diff
        physical_diff = PhysicalDiff(overall=1.0)

    action_hints = generate_action_hints(semantic_diff, physical_diff)

    overall = round(semantic_diff.overall * 0.6 + physical_diff.overall * 0.4, 4)

    return ComparisonResult(
        semantic_diff=semantic_diff,
        physical_diff=physical_diff,
        action_hints=action_hints,
        overall_score=overall,
        mode="compare" if candidate_phys else "self",
    )
