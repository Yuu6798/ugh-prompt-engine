"""eval/scorer_rpe.py — RPE physical quality scoring against Pro baseline.

Each physical metric is scored [0,1] by proximity to Pro reference values.
"""
from __future__ import annotations

from svp_rpe.eval.models import RPEScore
from svp_rpe.rpe.models import PhysicalRPE
from svp_rpe.utils.config_loader import load_config


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _proximity_score(actual: float, ideal: float, tolerance: float) -> float:
    """Score based on proximity to ideal. 1.0 = exact match, 0.0 = far away."""
    if tolerance == 0:
        return 1.0 if actual == ideal else 0.0
    distance = abs(actual - ideal) / tolerance
    return _clamp(1.0 - distance)


def score_rpe(phys: PhysicalRPE) -> RPEScore:
    """Score PhysicalRPE against Pro baseline values."""
    try:
        cfg = load_config("pro_baseline")
    except FileNotFoundError:
        # Fallback defaults
        cfg = {
            "rms_mean_pro": 0.298,
            "active_rate_ideal": 0.915,
            "crest_factor_ideal": 5.0,
            "valley_depth_pro": 0.2165,
            "thickness_pro": 2.105,
        }

    rms_score = _proximity_score(phys.rms_mean, cfg["rms_mean_pro"], 0.3)
    active_rate_score = _proximity_score(phys.active_rate, cfg["active_rate_ideal"], 0.5)
    crest_factor_score = _proximity_score(phys.crest_factor, cfg["crest_factor_ideal"], 5.0)
    valley_score = _proximity_score(phys.valley_depth, cfg["valley_depth_pro"], 0.3)
    thickness_score = _proximity_score(phys.thickness, cfg["thickness_pro"], 2.0)

    overall = round(
        (rms_score + active_rate_score + crest_factor_score + valley_score + thickness_score) / 5.0,
        4,
    )

    return RPEScore(
        rms_score=round(rms_score, 4),
        active_rate_score=round(active_rate_score, 4),
        crest_factor_score=round(crest_factor_score, 4),
        valley_score=round(valley_score, 4),
        thickness_score=round(thickness_score, 4),
        overall=overall,
    )
