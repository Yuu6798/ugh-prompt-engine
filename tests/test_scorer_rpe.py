"""tests/test_scorer_rpe.py - RPE baseline profile scoring tests."""
from __future__ import annotations

import pytest

from svp_rpe.eval.scorer_rpe import BASELINE_CONFIGS, score_rpe
from svp_rpe.rpe.models import PhysicalRPE, SectionMarker, SpectralProfile
from svp_rpe.utils.config_loader import load_config


def _spectral_profile() -> SpectralProfile:
    return SpectralProfile(
        centroid=2000.0,
        low_ratio=0.3,
        mid_ratio=0.5,
        high_ratio=0.2,
        brightness=0.2,
    )


def _physical_at_baseline(profile: str) -> PhysicalRPE:
    cfg = load_config(BASELINE_CONFIGS[profile])
    return PhysicalRPE(
        duration_sec=60.0,
        sample_rate=44100,
        structure=[SectionMarker(label="Full", start_sec=0.0, end_sec=60.0)],
        rms_mean=cfg["rms_mean_pro"],
        peak_amplitude=0.9,
        crest_factor=cfg["crest_factor_ideal"],
        active_rate=cfg["active_rate_ideal"],
        valley_depth=cfg["valley_depth_pro"],
        thickness=cfg["thickness_pro"],
        spectral_centroid=2000.0,
        spectral_profile=_spectral_profile(),
        onset_density=2.0,
    )


@pytest.mark.parametrize("profile", ["pro", "loud_pop", "acoustic", "edm"])
def test_baseline_profile_exact_match_scores_one(profile: str) -> None:
    phys = _physical_at_baseline(profile)

    score = score_rpe(phys, baseline=profile)

    assert score.schema_version == "1.1"
    assert score.baseline_profile == profile
    assert score.overall == 1.0
    assert score.rms_score == 1.0
    assert score.active_rate_score == 1.0
    assert score.crest_factor_score == 1.0
    assert score.valley_score == 1.0
    assert score.thickness_score == 1.0


def test_default_baseline_is_pro() -> None:
    phys = _physical_at_baseline("pro")

    assert score_rpe(phys).model_dump() == score_rpe(phys, baseline="pro").model_dump()


def test_invalid_baseline_is_rejected() -> None:
    phys = _physical_at_baseline("pro")

    with pytest.raises(ValueError, match="unknown baseline profile"):
        score_rpe(phys, baseline="not_a_profile")
