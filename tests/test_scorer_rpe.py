"""tests/test_scorer_rpe.py - RPE baseline profile scoring tests."""
from __future__ import annotations

import pytest

from svp_rpe.eval.scorer_rpe import BASELINE_CONFIGS, STEM_BASELINE_PROFILES, score_rpe
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

    assert score.schema_version == "1.2"
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
    assert "stem_scores" not in score_rpe(phys).model_dump()


def test_invalid_baseline_is_rejected() -> None:
    phys = _physical_at_baseline("pro")

    with pytest.raises(ValueError, match="unknown baseline profile"):
        score_rpe(phys, baseline="not_a_profile")


def test_stem_rpe_scores_use_stem_baseline_profiles() -> None:
    stem_rpe = {
        stem_name: _physical_at_baseline(stem_baseline)
        for stem_name, stem_baseline in STEM_BASELINE_PROFILES.items()
    }
    phys = _physical_at_baseline("pro").model_copy(update={"stem_rpe": stem_rpe})

    score = score_rpe(phys, baseline="pro")

    assert set(score.stem_scores) == set(STEM_BASELINE_PROFILES)
    assert score.overall == 1.0
    for stem_name, stem_score in score.stem_scores.items():
        assert stem_score.baseline_profile == STEM_BASELINE_PROFILES[stem_name]
        assert stem_score.overall == 1.0
        assert stem_score.stem_scores == {}


def test_stem_score_keeps_partial_distance_when_not_at_stem_baseline() -> None:
    phys = _physical_at_baseline("pro").model_copy(
        update={"stem_rpe": {"vocals": _physical_at_baseline("edm")}},
    )

    score = score_rpe(phys, baseline="pro")

    vocal_score = score.stem_scores["vocals"]
    assert vocal_score.baseline_profile == "acoustic"
    assert 0.0 < vocal_score.overall < 1.0
    assert vocal_score.rms_score < 1.0


def test_stem_scores_are_serialized_only_when_present() -> None:
    phys = _physical_at_baseline("pro")
    no_stems = score_rpe(phys).model_dump()
    assert "stem_scores" not in no_stems

    phys = phys.model_copy(update={"stem_rpe": {"vocals": _physical_at_baseline("acoustic")}})
    with_stems = score_rpe(phys).model_dump()

    assert set(with_stems["stem_scores"]) == {"vocals"}
    assert with_stems["stem_scores"]["vocals"]["baseline_profile"] == "acoustic"
    assert "stem_scores" not in with_stems["stem_scores"]["vocals"]


def test_unknown_stem_name_warns_and_uses_parent_baseline() -> None:
    phys = _physical_at_baseline("pro").model_copy(
        update={"stem_rpe": {"synth": _physical_at_baseline("pro")}},
    )

    with pytest.warns(RuntimeWarning, match="Unknown stem 'synth'"):
        score = score_rpe(phys, baseline="pro")

    assert score.stem_scores["synth"].baseline_profile == "pro"


def test_nested_stem_rpe_warns_and_is_not_scored_recursively() -> None:
    nested = _physical_at_baseline("pro").model_copy(
        update={"stem_rpe": {"bass": _physical_at_baseline("edm")}},
    )
    phys = _physical_at_baseline("pro").model_copy(update={"stem_rpe": {"vocals": nested}})

    with pytest.warns(RuntimeWarning, match="Nested stem_rpe"):
        score = score_rpe(phys, baseline="pro")

    assert set(score.stem_scores) == {"vocals"}
    assert score.stem_scores["vocals"].stem_scores == {}


def test_rpe_score_rejects_nested_stem_scores() -> None:
    """RPEScore validator must reject manually-constructed nested stem_scores."""
    from svp_rpe.eval.models import RPEScore

    base_kwargs = dict(
        rms_score=1.0,
        active_rate_score=1.0,
        crest_factor_score=1.0,
        valley_score=1.0,
        thickness_score=1.0,
        overall=1.0,
    )
    nested_inner = RPEScore(
        baseline_profile="acoustic",
        stem_scores={"bass": RPEScore(baseline_profile="edm", **base_kwargs)},
        **base_kwargs,
    )

    with pytest.raises(ValueError, match="must not contain nested stem_scores"):
        RPEScore(
            baseline_profile="pro",
            stem_scores={"vocals": nested_inner},
            **base_kwargs,
        )
