"""candidates/registry.py の候補宣言の検証（設計正本 §8, memo §2.6）。

凍結空間は 99 候補（設計正本 §8）。RUN10-CAL リセット設計 v0 §2 が TILT の
ピーク探索版 12 候補を **追加のみ** で足したため現在の総数は 111 で、
凍結 99 の宣言（id・parameters・implementation_ref・tier・ceiling）は不変。
"""

from __future__ import annotations

import hashlib
import json
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


FROZEN_SPACE_COUNT = 99
"""設計正本 §8 が凍結した候補数。以後の追加はここへ加算して数える。"""

RESET_V0_PEAK_IDS = frozenset(
    f"M2T-HARMONIC-{estimator}-PEAK-K{k}-WIN{window}"
    for estimator in ("OLS", "THEILSEN")
    for k in (4, 6, 8)
    for window in ("HANN", "BLACKMAN_HARRIS")
)
"""リセット設計 v0 §2 の追加分（12 件）。"""

TOTAL_COUNT = FROZEN_SPACE_COUNT + len(RESET_V0_PEAK_IDS)

FROZEN_SPACE_ID_SHA256 = "ebcd0cc3c67886f9232af1d75fecb34c1422e1f7ab655e0a69f069b9ae7dd34a"
"""凍結 99 candidate_id を昇順 JSON 配列（区切り最小）にした sha256。追加分を
除いた集合がこの pin と一致することで、追加が既存 id を書き換えていない
（rename / 削除がない）ことを機械的に固定する。"""

_FROZEN_99_IDS = {c.candidate_id for c in reg.ALL_CANDIDATES} - RESET_V0_PEAK_IDS


def test_frozen_99_ids_match_the_committed_pin() -> None:
    payload = json.dumps(sorted(_FROZEN_99_IDS), separators=(",", ":")).encode("utf-8")
    assert len(_FROZEN_99_IDS) == FROZEN_SPACE_COUNT
    assert hashlib.sha256(payload).hexdigest() == FROZEN_SPACE_ID_SHA256


def test_total_count_is_the_frozen_space_plus_declared_additions() -> None:
    assert len(reg.ALL_CANDIDATES) == TOTAL_COUNT
    added = {c.candidate_id for c in reg.ALL_CANDIDATES} - _FROZEN_99_IDS
    assert added == set(RESET_V0_PEAK_IDS)


def test_frozen_99_candidates_are_unchanged_by_additions() -> None:
    """追加は既存候補の宣言を一切動かさない（歴史 campaign が pin している）。"""
    frozen = {c.candidate_id: c for c in reg.ALL_CANDIDATES if c.candidate_id in _FROZEN_99_IDS}
    assert len(frozen) == FROZEN_SPACE_COUNT
    for candidate_id, candidate in frozen.items():
        assert "-PEAK-" not in candidate_id, candidate_id
        assert not candidate.implementation_ref.endswith("_peak"), candidate_id
    peak = [c for c in reg.ALL_CANDIDATES if c.candidate_id in RESET_V0_PEAK_IDS]
    assert all(
        c.implementation_ref.endswith(("measure_ols_peak", "measure_theilsen_peak")) for c in peak
    )
    assert all(c.complexity_rank >= 13 for c in peak), "追加分は既存 rank の後ろへ連番で並ぶ"


def test_peak_variants_reuse_the_sibling_family_and_domain() -> None:
    """リセット設計 v0 §3 Tier F: 新しい `algorithm_family` / `domain` を作ると
    campaign 基盤側（primary output 表 / F0 依存集合 / E_use 表）が黙って
    fail-open するため、ピーク探索版は兄弟候補の宣言を逐語で共有する。"""
    from voice_genesis.calibration.campaign import measure_stage

    peak = [c for c in reg.ALL_CANDIDATES if c.candidate_id in RESET_V0_PEAK_IDS]
    fixed_bin = {
        c.algorithm_family: c
        for c in reg.candidates_for_meter(vocab.MeterId.M2_SPECTRAL_TILT)
        if c.candidate_id in _FROZEN_99_IDS and c.algorithm_family.startswith("HARMONIC_")
    }
    assert set(fixed_bin) == {"HARMONIC_OLS", "HARMONIC_THEILSEN"}
    for candidate in peak:
        sibling = fixed_bin[candidate.algorithm_family]
        assert (candidate.construct, candidate.unit, candidate.domain) == (
            sibling.construct,
            sibling.unit,
            sibling.domain,
        ), candidate.candidate_id
        assert (
            measure_stage.PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY[candidate.algorithm_family]
            == "tilt_db_per_oct"
        )
        assert candidate.algorithm_family in measure_stage.F0_DEPENDENT_ALGORITHM_FAMILIES


def test_e_use_table_needs_no_new_row_for_the_additions() -> None:
    """`(construct, unit, domain)` の一意タプル集合が追加前後で変わらない
    （Gate 1 承認済み `config/e_use_table_v1.json` へ行を足さない）。"""
    tuples = {(c.construct, c.unit, c.domain) for c in reg.ALL_CANDIDATES}
    frozen_tuples = {
        (c.construct, c.unit, c.domain)
        for c in reg.ALL_CANDIDATES
        if c.candidate_id in _FROZEN_99_IDS
    }
    assert tuples == frozen_tuples


@pytest.mark.parametrize(
    "meter,expected",
    [
        (vocab.MeterId.F0_CONTROL, 5),
        (vocab.MeterId.M3_FORMANTS, 43),
        (vocab.MeterId.M2_SPECTRAL_TILT, 13 + len(RESET_V0_PEAK_IDS)),
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
    assert sum(counts.values()) == TOTAL_COUNT
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
    """v1.3 §X1 preregistration: 凍結 99 候補の中では TILT harmonic 12 候補
    （OLS 6 + THEILSEN 6）だけが `hnr_acf_db >= -5.0` を宣言し、他の 87 候補
    （`M2T-B0-CURRENT-HYBRID` = ceiling NONE を含む）は未宣言のまま——この
    preregistration は追加後も凍結空間について逐語で成り立つ。

    リセット設計 v0 §2 のピーク探索 12 候補も同じ predicate を引き継ぐ
    （負例で非発火する条件は倍音振幅の取得方式に依存しないため）。
    """
    declared = {c.candidate_id for c in reg.ALL_CANDIDATES if c.detection_predicate is not None}
    assert declared & _FROZEN_99_IDS == set(_V1_3_DECLARED_PREDICATE_IDS)
    assert len(declared & _FROZEN_99_IDS) == 12
    assert "M2T-B0-CURRENT-HYBRID" not in declared
    assert declared == set(_V1_3_DECLARED_PREDICATE_IDS) | set(RESET_V0_PEAK_IDS)


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
# RUN10-CAL-v1.4 §前提 2 経路 (B)/§前提 4: `Candidate.abstention_reasons`
# — optional; declared by `M2A-B0-AUTOCORR-PERIODICITY` (P2 census PASS —
# `scratchpad/v14/p23/p23_report.txt` §5.2) and, as of the PR #354 round 3
# 追補 (2026-09-09), by all 5 F0_CONTROL candidates (F0 census
# `scratchpad/v14/p2f0/p2f0_report.txt`: positives 12/12 measured&detected,
# 0 `OUTPUT_MISSING`; SILENCE 3/3 `OUTPUT_MISSING`) — 6 candidates total.
# ---------------------------------------------------------------------------

#: v1.4 preregistration の宣言集合（pinned value）。F0_CONTROL 5 件は round 3
#: 追補で追加（第 1 稿の census が TILT_GT/APERIODICITY_GT のみで F0 を
#: 欠いていたため未宣言のままだった——`DESIGN_VG_METER_CAL_DEBT_v1.4.md` §Y4）。
_V1_4_DECLARED_ABSTENTION_IDS = {
    "M2A-B0-AUTOCORR-PERIODICITY",
    "F0-B0-CURRENT",
    "F0-PYIN-FRAME2048-HOP256",
    "F0-PYIN-FRAME2048-HOP512",
    "F0-PYIN-FRAME4096-HOP256",
    "F0-PYIN-FRAME4096-HOP512",
}


def test_abstention_reasons_declared_exactly_by_v1_4_preregistration() -> None:
    declared = {
        c.candidate_id for c in reg.ALL_CANDIDATES if c.abstention_reasons
    }
    assert declared == _V1_4_DECLARED_ABSTENTION_IDS


def test_declared_abstention_reasons_value() -> None:
    for candidate_id in sorted(_V1_4_DECLARED_ABSTENTION_IDS):
        candidate = reg.candidate_by_id(candidate_id)
        assert candidate.abstention_reasons == frozenset(
            {vocab.MissingReason.OUTPUT_MISSING}
        ), candidate_id


def test_every_f0_control_candidate_declares_output_missing_abstention() -> None:
    """PR #354 round 3 追補: F0 census が全 5 候補について「正例で
    `OUTPUT_MISSING` 0 件」を示したため、F0_CONTROL family は全件宣言する
    （family 単位で漏れが出れば production C3a がその候補だけ偽失敗する）。"""
    f0_candidates = reg.candidates_for_meter(vocab.MeterId.F0_CONTROL)
    assert len(f0_candidates) == 5
    for candidate in f0_candidates:
        assert candidate.abstention_reasons == frozenset(
            {vocab.MissingReason.OUTPUT_MISSING}
        ), candidate.candidate_id


def test_abstention_reasons_accepts_a_declared_value() -> None:
    """AGENTS.md §7 item 11: 新しい導出軸は None/空でない fixture テストと
    セットで導入する（`dataclasses.replace` 経由の round-trip 確認）。
    base は宣言していない候補（round 3 追補で F0-B0-CURRENT が宣言側へ
    移ったため M3-B0-CURRENT-CENTROID へ差し替え）。"""
    import dataclasses

    base = reg.candidate_by_id("M3-B0-CURRENT-CENTROID")
    assert base.abstention_reasons == frozenset()
    declared = dataclasses.replace(
        base, abstention_reasons=frozenset({vocab.MissingReason.OUTPUT_MISSING})
    )
    assert declared.abstention_reasons == frozenset({vocab.MissingReason.OUTPUT_MISSING})
    assert base.abstention_reasons == frozenset()


# ---------------------------------------------------------------------------
# RUN10-CAL-v1.4 §前提 5: `Candidate.truth_polarity` — declared for
# APERIODICITY_GT's 3 DIRECTIONAL algorithm families only (harmonic_to_
# noise_ratio: -1, injected_noise_fraction: +1, world_d4c_aperiodicity: +1).
# No other family declares a polarity in v1.4.
# ---------------------------------------------------------------------------

_V1_4_HNR_TO_NOISE_RATIO_IDS = frozenset(
    {"M2A-B0-AUTOCORR-PERIODICITY"}
    | {c.candidate_id for c in reg.ALL_CANDIDATES if c.algorithm_family == "HNR_ACF"}
)
_V1_4_INJECTED_NOISE_FRACTION_IDS = frozenset(
    c.candidate_id for c in reg.ALL_CANDIDATES if c.algorithm_family == "HARMONIC_RESIDUAL"
)
_V1_4_WORLD_D4C_IDS = frozenset(
    c.candidate_id for c in reg.ALL_CANDIDATES if c.algorithm_family == "D4C_WORLD"
)


def test_truth_polarity_declared_exactly_by_aperiodicity_directional_families() -> None:
    declared = {c.candidate_id for c in reg.ALL_CANDIDATES if c.truth_polarity is not None}
    expected = (
        _V1_4_HNR_TO_NOISE_RATIO_IDS
        | _V1_4_INJECTED_NOISE_FRACTION_IDS
        | _V1_4_WORLD_D4C_IDS
    )
    assert declared == expected
    assert len(declared) == 9 + 12 + 3


def test_truth_polarity_values_match_construct_physics() -> None:
    for candidate_id in sorted(_V1_4_HNR_TO_NOISE_RATIO_IDS):
        assert reg.candidate_by_id(candidate_id).truth_polarity == -1, candidate_id
    for candidate_id in sorted(_V1_4_INJECTED_NOISE_FRACTION_IDS):
        assert reg.candidate_by_id(candidate_id).truth_polarity == 1, candidate_id
    for candidate_id in sorted(_V1_4_WORLD_D4C_IDS):
        assert reg.candidate_by_id(candidate_id).truth_polarity == 1, candidate_id


def test_truth_polarity_not_declared_outside_aperiodicity_gt() -> None:
    declared_ids = {c.candidate_id for c in reg.ALL_CANDIDATES if c.truth_polarity is not None}
    for candidate_id in declared_ids:
        assert reg.candidate_by_id(candidate_id).meter == vocab.MeterId.M2_APERIODICITY


def test_truth_polarity_rejects_values_outside_plus_minus_one_or_none() -> None:
    import dataclasses

    base = reg.candidate_by_id("F0-B0-CURRENT")
    for bad_value in (0, 2, -2, 0.5):
        with pytest.raises(ValueError):
            dataclasses.replace(base, truth_polarity=bad_value)


def test_truth_polarity_accepts_a_declared_value() -> None:
    import dataclasses

    base = reg.candidate_by_id("F0-B0-CURRENT")
    assert base.truth_polarity is None
    declared_pos = dataclasses.replace(base, truth_polarity=1)
    declared_neg = dataclasses.replace(base, truth_polarity=-1)
    assert declared_pos.truth_polarity == 1
    assert declared_neg.truth_polarity == -1
    assert base.truth_polarity is None


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
    """凍結グリッドの検査。リセット設計 v0 §2 のピーク探索版は同じ
    `algorithm_family` を共有する（Tier F の primary output 表 / F0 依存集合を
    そのまま使うため）ので、凍結 99 側だけを取り出して数える。"""
    ols_ids = [
        c.candidate_id
        for c in reg.ALL_CANDIDATES
        if c.algorithm_family == "HARMONIC_OLS" and c.candidate_id in _FROZEN_99_IDS
    ]
    ts_ids = [
        c.candidate_id
        for c in reg.ALL_CANDIDATES
        if c.algorithm_family == "HARMONIC_THEILSEN" and c.candidate_id in _FROZEN_99_IDS
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
    # 追加分も同じグリッドを張る（選抜の裁量を残さない）
    peak_ids = [c.candidate_id for c in reg.ALL_CANDIDATES if c.candidate_id in RESET_V0_PEAK_IDS]
    assert set(_param_sets(peak_ids)) == expected


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
