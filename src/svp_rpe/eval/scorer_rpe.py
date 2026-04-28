"""eval/scorer_rpe.py — RPE physical quality scoring against per-genre baselines.

Each physical metric is scored [0,1] by proximity to the selected baseline's
reference values. Q1-4 introduced multiple baselines (Pro / Loud Pop /
Acoustic / EDM); the caller picks one via the `baseline` parameter, default
`"pro"`. Adding a new baseline only requires dropping
`config/<name>_baseline.yaml` (and the packaged twin under
`src/svp_rpe/config/`).
"""
from __future__ import annotations

from svp_rpe.eval.models import RPEScore
from svp_rpe.rpe.models import PhysicalRPE
from svp_rpe.utils.config_loader import load_config

# Built-in baselines packaged with svp-rpe. Custom baselines can still be
# added by dropping a `<name>_baseline.yaml` into the config directory; this
# tuple just documents the canonical Q1-4 set and supports validation.
KNOWN_BASELINES: tuple[str, ...] = ("pro", "loud_pop", "acoustic", "edm")
DEFAULT_BASELINE = "pro"

# Fallback only kept for the original "pro" baseline so test environments
# without packaged config still produce a sensible default. Other baselines
# require the YAML to exist; otherwise FileNotFoundError propagates.
_PRO_FALLBACK_DEFAULTS = {
    "rms_mean_pro": 0.298,
    "active_rate_ideal": 0.915,
    "crest_factor_ideal": 5.0,
    "valley_depth_pro": 0.2165,
    "thickness_pro": 2.105,
}


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _proximity_score(actual: float, ideal: float, tolerance: float) -> float:
    """Score based on proximity to ideal. 1.0 = exact match, 0.0 = far away."""
    if tolerance == 0:
        return 1.0 if actual == ideal else 0.0
    distance = abs(actual - ideal) / tolerance
    return _clamp(1.0 - distance)


def _load_baseline(baseline: str) -> dict:
    config_name = f"{baseline}_baseline"
    try:
        return load_config(config_name)
    except FileNotFoundError:
        if baseline == DEFAULT_BASELINE:
            return _PRO_FALLBACK_DEFAULTS
        raise


def score_rpe(phys: PhysicalRPE, *, baseline: str = DEFAULT_BASELINE) -> RPEScore:
    """Score PhysicalRPE against the named baseline (default `"pro"`)."""
    cfg = _load_baseline(baseline)

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
