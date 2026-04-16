"""eval/scorer_integrated.py — Weighted integrated scoring."""
from __future__ import annotations

from svp_rpe.eval.models import IntegratedScore, RPEScore, UGHerScore


def score_integrated(
    ugher: UGHerScore,
    rpe: RPEScore,
    *,
    ugher_weight: float = 0.5,
    rpe_weight: float = 0.5,
) -> IntegratedScore:
    """Compute weighted integrated score.

    Deterministic: same inputs → same output.
    """
    total_weight = ugher_weight + rpe_weight
    if total_weight == 0:
        total_weight = 1.0

    integrated = (ugher.overall * ugher_weight + rpe.overall * rpe_weight) / total_weight

    return IntegratedScore(
        ugher_score=ugher.overall,
        rpe_score=rpe.overall,
        integrated_score=round(integrated, 4),
        ugher_weight=ugher_weight,
        rpe_weight=rpe_weight,
    )
