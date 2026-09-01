from __future__ import annotations

import random
from dataclasses import replace

import pytest

from voice_genesis.calibration.fixtures import axes, controls
from voice_genesis.calibration.fixtures.axes import FixtureFamily
from voice_genesis.calibration.fixtures.matrix import (
    FixtureRow,
    build_matrix,
    compute_domain,
    f0_band_ok,
    validate_matrix,
)
from voice_genesis.calibration.splitter import RowInput, realize_split
from voice_genesis.calibration.vocab import Domain, Split

SPLIT_SECRET = b"\x09" * 32

#: Codex レビュー 2026-09-01 P1 finding #3 の実測回帰 secret: 旧 coverage
#: 制約（truth-core 行の split 当たり被覆下限=1）の下では F0_CONTROL の
#: HOLDOUT 側 truth-core 行が 1 件しか配置されず、
#: `controls.positive_detection_instances()` が `N_pos=5` (< 10) に留まった。
FINDING3_SECRET = bytes.fromhex(
    "a52252084218c91a0f70951ea70100f8e054e48fb898a7aa6d4220b5a4d85236"
)


def _to_row_inputs(rows):
    return [
        RowInput(
            row_id=mr.row_id,
            family=mr.row.family,
            stratum={"family": mr.row.family},
            truth_level=mr.row.block,
            generator_impl=mr.row.generator_impl,
            boundary_class=mr.domain.value,
        )
        for mr in rows
    ]


# ---------------------------------------------------------------------------
# 総数 / per-family 内訳 (§5.2)
# ---------------------------------------------------------------------------


def test_total_is_456() -> None:
    rows = build_matrix()
    assert len(rows) == 456


def test_per_family_totals_and_block_breakdown_match_section_5_2() -> None:
    rows = build_matrix()
    report = validate_matrix(rows)
    assert report.ok is True
    assert report.total == 456
    for family, (truth_n, confound_n, boundary_n, total_n) in axes.FAMILY_COUNTS.items():
        fam_key = family.value
        assert report.per_family_total[fam_key] == total_n
        blocks = report.per_family_block_counts[fam_key]
        assert blocks.get("TRUTH_CORE", 0) == truth_n
        assert blocks.get("CONFOUND", 0) == confound_n
        assert blocks.get("BOUNDARY", 0) + blocks.get("NEGATIVE_CONTROL", 0) == boundary_n


def test_sum_of_family_totals_is_456() -> None:
    total = sum(t for (_, _, _, t) in axes.FAMILY_COUNTS.values())
    assert total == 456


# ---------------------------------------------------------------------------
# row_id 一意性
# ---------------------------------------------------------------------------


def test_all_row_ids_unique() -> None:
    rows = build_matrix()
    ids = [mr.row_id for mr in rows]
    assert len(ids) == len(set(ids))


def test_duplicate_row_id_is_rejected() -> None:
    from voice_genesis.calibration.fixtures.matrix import assert_no_duplicate_row_ids
    from voice_genesis.calibration.fixtures.matrix import _finalize

    rows = build_matrix()
    dup = _finalize(rows[0].row)  # 同一内容 -> 同一 row_id
    with pytest.raises(ValueError, match="duplicate row_id"):
        assert_no_duplicate_row_ids(rows + [dup])


def test_validate_matrix_flags_duplicate_via_ok_false() -> None:
    from voice_genesis.calibration.fixtures.matrix import _finalize

    rows = build_matrix()
    dup = _finalize(rows[0].row)
    report = validate_matrix(rows + [dup])
    assert report.ok is False
    assert len(report.duplicate_row_ids) == 1


# ---------------------------------------------------------------------------
# domain タグ導出 (D2 + F0 帯域整合検査)
# ---------------------------------------------------------------------------


def test_domain_tag_present_and_split_between_primary_and_boundary() -> None:
    rows = build_matrix()
    domains = {mr.domain for mr in rows}
    assert domains == {Domain.PRIMARY, Domain.BOUNDARY}
    primary_n = sum(1 for mr in rows if mr.domain == Domain.PRIMARY)
    boundary_n = sum(1 for mr in rows if mr.domain == Domain.BOUNDARY)
    assert primary_n + boundary_n == 456
    assert boundary_n > 0


def test_d2_any_boundary_axis_retags_boundary() -> None:
    truth_row = FixtureRow(
        family="F0_CONTROL",
        block="TRUTH_CORE",
        f0_hz=261.626,
        sr_hz=48000,
        gain_dbfs=-12.0,
        duration_s=1.0,
        noise_clean=True,
        noise_snr_db=None,
        context="steady-isolated",
    )
    assert compute_domain(truth_row) == Domain.PRIMARY

    boundary_sr_row = replace(truth_row, sr_hz=16000)
    assert compute_domain(boundary_sr_row) == Domain.BOUNDARY

    boundary_gain_row = replace(truth_row, gain_dbfs=-36.0)
    assert compute_domain(boundary_gain_row) == Domain.BOUNDARY


def test_f0_band_check_retags_boundary_without_touching_other_boundary_axes() -> None:
    """§3.3: `fmin <= 0.8*min(PRIMARY F0)` かつ `fmax >= 1.25*max(PRIMARY F0)`。
    行自身の f0_hz がこの帯域外なら（他の軸が全て primary level でも）BOUNDARY
    へ再タグする。"""
    fmin = 0.8 * min(axes.PRIMARY_F0_HZ)
    fmax = 1.25 * max(axes.PRIMARY_F0_HZ)
    assert f0_band_ok(fmin) is True
    assert f0_band_ok(fmin - 0.01) is False
    assert f0_band_ok(fmax) is True
    assert f0_band_ok(fmax + 0.01) is False

    # 構成したケース: primary 軸のみを使うが f0_hz を帯域のすぐ外側に置く
    # (D2 の boundary axis セットには入らない値: PRIMARY_F0_HZ にも
    # BOUNDARY_F0_HZ にも一致しない任意点)。
    out_of_band_f0 = fmin - 5.0
    row = FixtureRow(
        family="F0_CONTROL",
        block="TRUTH_CORE",
        f0_hz=out_of_band_f0,
        sr_hz=48000,
        gain_dbfs=-12.0,
        duration_s=1.0,
        noise_clean=True,
        noise_snr_db=None,
        context="steady-isolated",
    )
    assert compute_domain(row) == Domain.BOUNDARY

    in_band_row = replace(row, f0_hz=(fmin + fmax) / 2.0)
    assert compute_domain(in_band_row) == Domain.PRIMARY


def test_negative_control_rows_are_always_boundary() -> None:
    rows = build_matrix()
    for mr in rows:
        if mr.row.control_class is not None:
            assert mr.domain == Domain.BOUNDARY


# ---------------------------------------------------------------------------
# §2.7 targeted interaction 列挙が verbatim であること
# ---------------------------------------------------------------------------


def test_transition_has_exactly_the_4_enumerated_interactions() -> None:
    rows = build_matrix()
    tagged = [
        mr.row.interaction_tag
        for mr in rows
        if mr.row.family == FixtureFamily.TRANSITION_GT.value and mr.row.interaction_tag
    ]
    assert tagged == [
        "high-F0×low-SR",
        "high-F0×short-duration",
        "high-F0×low-SNR",
        "low-F0×high-SR",
    ]


def test_full_six_interactions_present_for_f0_formant_identity() -> None:
    expected = [
        "high-F0×low-SR",
        "high-F0×short-duration",
        "high-F0×low-SNR",
        "low-F0×high-SR",
        "low-gain×noise",
        "transition×short-duration",
    ]
    rows = build_matrix()
    for family in (
        FixtureFamily.F0_CONTROL,
        FixtureFamily.FORMANT_GT,
        FixtureFamily.IDENTITY_CAUSAL_SWEEP,
    ):
        tagged = [
            mr.row.interaction_tag
            for mr in rows
            if mr.row.family == family.value and mr.row.interaction_tag
        ]
        assert tagged == expected, family


def test_tilt_and_resonance_have_exactly_one_interaction() -> None:
    rows = build_matrix()
    for family in (FixtureFamily.TILT_GT, FixtureFamily.RESONANCE_GT):
        tagged = [
            mr.row.interaction_tag
            for mr in rows
            if mr.row.family == family.value and mr.row.interaction_tag
        ]
        assert tagged == ["high-F0×low-SR"], family


def test_aperiodicity_has_zero_interactions() -> None:
    rows = build_matrix()
    tagged = [
        mr.row.interaction_tag
        for mr in rows
        if mr.row.family == FixtureFamily.APERIODICITY_GT.value and mr.row.interaction_tag
    ]
    assert tagged == []


# ---------------------------------------------------------------------------
# positive control 2 件/family
# ---------------------------------------------------------------------------


def test_exactly_two_positive_control_rows_per_family() -> None:
    rows = build_matrix()
    from collections import Counter

    counts = Counter(mr.row.family for mr in rows if mr.row.positive_control)
    assert set(counts.keys()) == {f.value for f in axes.FAMILY_ORDER}
    assert all(n == 2 for n in counts.values())
    for mr in rows:
        if mr.row.positive_control:
            assert mr.row.block == "TRUTH_CORE"


# ---------------------------------------------------------------------------
# 列挙の決定性
# ---------------------------------------------------------------------------


def test_enumeration_is_deterministic_across_calls() -> None:
    a = build_matrix()
    b = build_matrix()
    assert [mr.row_id for mr in a] == [mr.row_id for mr in b]
    assert [mr.row.family for mr in a] == [mr.row.family for mr in b]
    assert [mr.domain for mr in a] == [mr.domain for mr in b]


# ---------------------------------------------------------------------------
# Phase A splitter との結合: 228/114/114、split 制約充足
# ---------------------------------------------------------------------------


def test_splitter_over_full_matrix_yields_228_114_114() -> None:
    rows = build_matrix()
    row_inputs = _to_row_inputs(rows)
    realized = realize_split(row_inputs, SPLIT_SECRET, ["family"])
    counts = {Split.CALIBRATION: 0, Split.SELECTION: 0, Split.HOLDOUT: 0}
    for s in realized.assignment.values():
        counts[s] += 1
    assert counts[Split.CALIBRATION] == 228
    assert counts[Split.SELECTION] == 114
    assert counts[Split.HOLDOUT] == 114
    assert sum(counts.values()) == 456
    assert set(realized.assignment.keys()) == {mr.row_id for mr in rows}


def test_splitter_over_full_matrix_verifies() -> None:
    from voice_genesis.calibration.splitter import verify_split

    rows = build_matrix()
    row_inputs = _to_row_inputs(rows)
    realized = realize_split(row_inputs, SPLIT_SECRET, ["family"])
    assert verify_split(row_inputs, SPLIT_SECRET, realized) is True


def test_splitter_per_family_totals_match_family_target_50_25_25() -> None:
    rows = build_matrix()
    row_inputs = _to_row_inputs(rows)
    realized = realize_split(row_inputs, SPLIT_SECRET, ["family"])
    by_family_split: dict[tuple[str, Split], int] = {}
    row_by_id = {r.row_id: r for r in row_inputs}
    for rid, split in realized.assignment.items():
        fam = row_by_id[rid].family
        by_family_split[(fam, split)] = by_family_split.get((fam, split), 0) + 1
    for family, (_t, _c, _b, total_n) in axes.FAMILY_COUNTS.items():
        fam_key = family.value
        fam_total = sum(
            n for (f, _s), n in by_family_split.items() if f == fam_key
        )
        assert fam_total == total_n


# ---------------------------------------------------------------------------
# truth-core coverage 下限 2/family/split (Codex レビュー 2026-09-01 P1
# finding #3): N_pos>=10 を SELECTION/HOLDOUT 双方で安定して満たすための
# splitter.py 側 coverage 制約強化の実データ検証。
# ---------------------------------------------------------------------------


def _truth_core_count(rows, assignment, family: str, split: Split) -> int:
    return sum(
        1
        for mr in rows
        if mr.row.family == family
        and mr.row.block == "TRUTH_CORE"
        and assignment[mr.row_id] == split
    )


def test_truth_core_coverage_min_2_per_family_split_with_finding3_secret() -> None:
    """finding #3 の実測 secret を使った実 matrix split で、全 family ×
    {SELECTION, HOLDOUT} が truth-core 行 2 件以上・`N_pos>=10` を満たす
    （修正前は F0_CONTROL/HOLDOUT が truth-core 行 1 件・N_pos=5 で違反した）。
    """
    rows = build_matrix()
    row_inputs = _to_row_inputs(rows)
    realized = realize_split(row_inputs, FINDING3_SECRET, ["family"])

    for family in {mr.row.family for mr in rows}:
        for split in (Split.SELECTION, Split.HOLDOUT):
            truth_core_count = _truth_core_count(rows, realized.assignment, family, split)
            assert truth_core_count >= 2, (family, split, truth_core_count)

            instances = controls.positive_detection_instances(
                rows, realized.assignment, split, family=family
            )
            assert len(instances) >= 10, (family, split, len(instances))


def test_truth_core_coverage_min_2_property_over_random_secrets() -> None:
    """5 件のランダム secret でも同様に成立する（単一 secret での偶然の成功と
    区別する。設計上の要求はどの secret でも壊れないこと）。実 matrix は全
    family が truth-core 行 >=12 件を持つため `CoverageRepairInfeasible` は
    発生しない設計判断（finding #3 の DESIGN RULING）。
    """
    rows = build_matrix()
    row_inputs = _to_row_inputs(rows)
    rng = random.Random(2026_09_01)

    for _ in range(5):
        secret = rng.randbytes(32)
        realized = realize_split(row_inputs, secret, ["family"])
        for family in {mr.row.family for mr in rows}:
            for split in (Split.SELECTION, Split.HOLDOUT):
                truth_core_count = _truth_core_count(rows, realized.assignment, family, split)
                assert truth_core_count >= 2, (family, split, truth_core_count, secret.hex())
