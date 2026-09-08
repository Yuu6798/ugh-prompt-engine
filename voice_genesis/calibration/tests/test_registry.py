"""candidates/registry.py の 99 候補宣言の検証（設計正本 §8, memo §2.6）。"""

from __future__ import annotations

from collections import Counter

import pytest

from voice_genesis.calibration import vocab
from voice_genesis.calibration.candidates import registry as reg

# tier が許す claim ceiling の「上限」の強さの全順序（registry.py docstring の
# 「tier→ceiling consistency」定義: ceiling は tier の上限を超えない）。
_CEILING_RANK = {
    vocab.ClaimCeiling.ABSOLUTE: 3,
    vocab.ClaimCeiling.DIRECTIONAL: 2,
    vocab.ClaimCeiling.DIAGNOSTIC_ONLY: 1,
    vocab.ClaimCeiling.NONE: 0,
}


def test_total_count_is_99() -> None:
    assert len(reg.ALL_CANDIDATES) == 99


@pytest.mark.parametrize(
    "meter,expected",
    [
        (vocab.MeterId.F0_CONTROL, 5),
        (vocab.MeterId.M3_FORMANTS, 43),
        (vocab.MeterId.M2_SPECTRAL_TILT, 13),
        (vocab.MeterId.M2_APERIODICITY, 24),
        (vocab.MeterId.M4_RESONANCE, 5),
        (vocab.MeterId.M5_TRANSITION, 7),
        (vocab.MeterId.M6_IDENTITY, 2),
    ],
)
def test_per_meter_counts(meter: vocab.MeterId, expected: int) -> None:
    assert len(reg.candidates_for_meter(meter)) == expected


def test_meter_counts_cover_all_candidates_exactly() -> None:
    counts = Counter(c.meter for c in reg.ALL_CANDIDATES)
    assert sum(counts.values()) == 99
    assert set(counts.keys()) == set(vocab.MeterId)


def test_candidate_id_uniqueness() -> None:
    ids = [c.candidate_id for c in reg.ALL_CANDIDATES]
    assert len(ids) == len(set(ids))


def test_b0_candidates_present_for_every_meter() -> None:
    b0_ids = {
        "F0-B0-CURRENT",
        "M3-B0-CURRENT-CENTROID",
        "M2T-B0-CURRENT-HYBRID",
        "M2A-B0-AUTOCORR-PERIODICITY",
        "M4-B0-CURRENT-CENTROID",
    }
    present = {c.candidate_id for c in reg.ALL_CANDIDATES}
    assert b0_ids.issubset(present)


def test_tier_ceiling_consistency() -> None:
    """registry の claim_ceiling は tier が許す最大 ceiling を超えない。

    INVALID_CIRCULAR tier は必ず ceiling=NONE と等価（vocab の写像がそれ
    しか許さないため、下限=上限で自動的に等号になる）。
    """
    for c in reg.ALL_CANDIDATES:
        tier_max = vocab.INDEPENDENCE_TIER_CLAIM_CEILING[c.independence_tier]
        assert _CEILING_RANK[c.claim_ceiling] <= _CEILING_RANK[tier_max], c.candidate_id
        if c.independence_tier is vocab.IndependenceTier.INVALID_CIRCULAR:
            assert c.claim_ceiling is vocab.ClaimCeiling.NONE, c.candidate_id


def test_complexity_rank_total_ordered_within_meter_family() -> None:
    for meter in vocab.MeterId:
        ranks = sorted(c.complexity_rank for c in reg.candidates_for_meter(meter))
        assert ranks == list(range(len(ranks))), meter


def test_candidate_by_id_roundtrip() -> None:
    for c in reg.ALL_CANDIDATES:
        assert reg.candidate_by_id(c.candidate_id) is c
    with pytest.raises(KeyError):
        reg.candidate_by_id("does-not-exist")


def test_implementation_ref_has_module_colon_function_shape() -> None:
    for c in reg.ALL_CANDIDATES:
        assert ":" in c.implementation_ref, c.candidate_id
        module_part, _, func_part = c.implementation_ref.partition(":")
        assert module_part and func_part, c.candidate_id


# ---------------------------------------------------------------------------
# RUN10-CAL-v1.2 WP1 (3) / v1.3 §X1: `detection_predicate` — optional; declared
# by exactly the 12 TILT harmonic candidates as of v1.3 (preregistration).
# ---------------------------------------------------------------------------

_V1_3_DECLARED_PREDICATE_IDS = frozenset(
    f"M2T-HARMONIC-{estimator}-K{k}-WIN{window}"
    for estimator in ("OLS", "THEILSEN")
    for k in (4, 6, 8)
    for window in ("HANN", "BLACKMAN_HARRIS")
)


def test_detection_predicate_declared_exactly_by_the_tilt_harmonic_twelve() -> None:
    """v1.3 §X1 preregistration: TILT harmonic 12 候補（OLS 6 + THEILSEN 6）
    だけが `hnr_acf_db >= -5.0` を宣言し、他の 87 候補（`M2T-B0-CURRENT-HYBRID`
    = ceiling NONE を含む）は未宣言のまま。"""
    declared = {c.candidate_id for c in reg.ALL_CANDIDATES if c.detection_predicate is not None}
    assert declared == set(_V1_3_DECLARED_PREDICATE_IDS)
    assert len(declared) == 12
    assert "M2T-B0-CURRENT-HYBRID" not in declared


def test_declared_detection_predicate_field_and_threshold() -> None:
    """閾値と field は v1.3 §X1.2 が凍結した値（WP-A 実測: 正例
    [-1.915, +0.747] dB / NOISE_ONLY [-9.878, -9.758] dB の中間）。"""
    for candidate_id in sorted(_V1_3_DECLARED_PREDICATE_IDS):
        predicate = reg.candidate_by_id(candidate_id).detection_predicate
        assert predicate is not None, candidate_id
        assert predicate.field == "hnr_acf_db", candidate_id
        assert predicate.min_value == -5.0, candidate_id


def test_declared_predicate_field_is_produced_by_the_wired_implementation() -> None:
    """宣言した `field` は当該候補の `measure()` が実際に `values` へ出す
    キーであること（宣言と実装の乖離＝恒久非発火を防ぐ）。"""
    import numpy as np

    from voice_genesis.calibration.candidates.impl import tilt_harmonic

    sr = 48000
    t_axis = np.arange(int(0.5 * sr)) / sr
    signal = sum(np.sin(2 * np.pi * 130.813 * h * t_axis) / h for h in range(1, 9))
    for candidate_id in sorted(_V1_3_DECLARED_PREDICATE_IDS):
        candidate = reg.candidate_by_id(candidate_id)
        params = dict(candidate.params_dict())
        params["f0_hz"] = 130.813
        measure = (
            tilt_harmonic.measure_ols
            if candidate.algorithm_family == "HARMONIC_OLS"
            else tilt_harmonic.measure_theilsen
        )
        output = measure(signal, sr, params)
        assert output.missing_reason is None, candidate_id
        assert candidate.detection_predicate is not None
        assert candidate.detection_predicate.field in output.values, candidate_id


def test_detection_predicate_accepts_a_declared_value() -> None:
    import dataclasses

    from voice_genesis.calibration.fixtures.controls import DetectionPredicate

    base = reg.candidate_by_id("F0-B0-CURRENT")
    declared = dataclasses.replace(
        base, detection_predicate=DetectionPredicate(field="f0_hz", min_value=1.0)
    )
    assert declared.detection_predicate == DetectionPredicate(field="f0_hz", min_value=1.0)
    # the base registry entry itself is untouched (dataclasses.replace copies).
    assert base.detection_predicate is None


# ---------------------------------------------------------------------------
# §2.6 パラメタグリッドの literal 一致（memo §2.6 が凍結する値そのもの）
# ---------------------------------------------------------------------------


def _param_sets(candidate_ids: list[str]) -> list[frozenset[tuple[str, object]]]:
    return [frozenset(reg.candidate_by_id(cid).parameters) for cid in candidate_ids]


def test_f0_pyin_grid_matches_frozen_spec() -> None:
    ids = [c.candidate_id for c in reg.ALL_CANDIDATES if c.algorithm_family == "PYIN"]
    assert len(ids) == 4
    expected = {
        frozenset({("frame_length", f), ("hop_length", h), ("fmin", 80.0), ("fmax", 600.0)})
        for f in (2048, 4096)
        for h in (256, 512)
    }
    assert set(_param_sets(ids)) == expected


def test_m3_cepstral_grid_matches_frozen_spec() -> None:
    ids = [c.candidate_id for c in reg.ALL_CANDIDATES if c.algorithm_family == "CEPSTRAL_POLES"]
    assert len(ids) == 18
    expected = {
        frozenset(
            {
                ("lifter_ratio", lr),
                ("min_lifter_samples", ml),
                ("band_hi", bh),
                ("band_lo", 300.0),
            }
        )
        for lr in (0.5, 0.7, 0.9)
        for ml in (4, 8)
        for bh in (3500, 4000, 4500)
    }
    assert set(_param_sets(ids)) == expected


def test_m3_burg_grid_matches_frozen_spec() -> None:
    ids = [c.candidate_id for c in reg.ALL_CANDIDATES if c.algorithm_family == "BURG_LPC"]
    assert len(ids) == 24
    expected = {
        frozenset(
            {
                ("order", order),
                ("window_ms", wm),
                ("preemph_hz", pe),
                ("max_formant_hz", mf),
            }
        )
        for order in (12, 16, 20)
        for wm in (25, 40)
        for pe in (0, 50)
        for mf in (4000, 5000)
    }
    assert set(_param_sets(ids)) == expected


def test_m2t_harmonic_grids_match_frozen_spec() -> None:
    ols_ids = [c.candidate_id for c in reg.ALL_CANDIDATES if c.algorithm_family == "HARMONIC_OLS"]
    ts_ids = [
        c.candidate_id for c in reg.ALL_CANDIDATES if c.algorithm_family == "HARMONIC_THEILSEN"
    ]
    assert len(ols_ids) == 6
    assert len(ts_ids) == 6
    expected = {
        frozenset({("k", k), ("window", w)})
        for k in (4, 6, 8)
        for w in ("hann", "blackman_harris")
    }
    assert set(_param_sets(ols_ids)) == expected
    assert set(_param_sets(ts_ids)) == expected


def test_m2a_hnr_acf_grid_matches_frozen_spec() -> None:
    ids = [c.candidate_id for c in reg.ALL_CANDIDATES if c.algorithm_family == "HNR_ACF"]
    assert len(ids) == 8
    expected = {
        frozenset({("frame_ms", fr), ("hop_ms", hp), ("window", w)})
        for fr in (25, 40)
        for hp in (10, 20)
        for w in ("hann", "blackman_harris")
    }
    assert set(_param_sets(ids)) == expected


def test_m2a_harmonic_residual_grid_matches_frozen_spec() -> None:
    ids = [c.candidate_id for c in reg.ALL_CANDIDATES if c.algorithm_family == "HARMONIC_RESIDUAL"]
    assert len(ids) == 12
    expected = {
        frozenset({("k", k), ("window", w), ("residual_band", b)})
        for k in (8, 10, 12)
        for w in ("hann", "blackman_harris")
        for b in ("broadband", "0-6khz")
    }
    assert set(_param_sets(ids)) == expected


def test_m2a_d4c_grid_matches_frozen_spec() -> None:
    ids = [c.candidate_id for c in reg.ALL_CANDIDATES if c.algorithm_family == "D4C_WORLD"]
    assert len(ids) == 3
    expected = {frozenset({("band", b)}) for b in ("broadband", "0-3khz", "3-6khz")}
    assert set(_param_sets(ids)) == expected


def test_m4_local_prominence_grid_matches_frozen_spec() -> None:
    ids = [c.candidate_id for c in reg.ALL_CANDIDATES if c.algorithm_family == "LOCAL_PROMINENCE"]
    assert len(ids) == 4
    expected = {
        frozenset({("prominence_db", p), ("smoothing_bandwidth_hz", s)})
        for p in (6, 12)
        for s in (150, 300)
    }
    assert set(_param_sets(ids)) == expected


def test_m5_wave_discontinuity_grid_matches_frozen_spec() -> None:
    ids = [c.candidate_id for c in reg.ALL_CANDIDATES if c.algorithm_family == "WAVE_DISCONTINUITY"]
    assert len(ids) == 3
    expected = {frozenset({("window_ms", w)}) for w in (2, 5, 10)}
    assert set(_param_sets(ids)) == expected


def test_m5_spectral_flux_grid_matches_frozen_spec() -> None:
    ids = [c.candidate_id for c in reg.ALL_CANDIDATES if c.algorithm_family == "SPECTRAL_FLUX"]
    assert len(ids) == 4
    expected = {
        frozenset({("frame_len", fl), ("norm", n)}) for fl in (512, 1024) for n in ("L1", "L2")
    }
    assert set(_param_sets(ids)) == expected


def test_m4_all_candidates_diagnostic_only() -> None:
    """設計正本 §16: RUN10 では全 M4 候補を DIAGNOSTIC_ONLY 上限で閉じる。"""
    for c in reg.candidates_for_meter(vocab.MeterId.M4_RESONANCE):
        assert c.claim_ceiling is vocab.ClaimCeiling.DIAGNOSTIC_ONLY, c.candidate_id


def test_m2t_b0_hybrid_is_invalid() -> None:
    c = reg.candidate_by_id("M2T-B0-CURRENT-HYBRID")
    assert c.claim_ceiling is vocab.ClaimCeiling.NONE


def test_m6_ceiling_is_directional() -> None:
    """設計正本 §12: M6 ceiling = CALIBRATED_DIRECTIONAL。"""
    for c in reg.candidates_for_meter(vocab.MeterId.M6_IDENTITY):
        assert c.claim_ceiling is vocab.ClaimCeiling.DIRECTIONAL
