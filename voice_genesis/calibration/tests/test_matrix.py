from __future__ import annotations

import ast
import random
from dataclasses import replace
from pathlib import Path

import pytest

from voice_genesis.calibration.candidates import registry as candidate_registry
from voice_genesis.calibration.fixtures import axes, controls
from voice_genesis.calibration.fixtures.axes import FixtureFamily
from voice_genesis.calibration.fixtures.matrix import (
    FixtureRow,
    active_matrix,
    build_matrix,
    build_rehearsal_matrix,
    compute_domain,
    declared_sweeps_by_family,
    f0_band_ok,
    rehearsal_mode,
    set_rehearsal_mode,
    single_axis_nuisance_tag_axis,
    truth_identity_for_row,
    validate_matrix,
)
from voice_genesis.calibration.gates import MIN_RESOLVABLE_PAIRS_PER_SWEEP
from voice_genesis.calibration.splitter import RowInput, realize_split
from voice_genesis.calibration.vocab import ClaimCeiling, Domain, Split

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
# 宣言済み sweep（UNDERSPEC-CAL-D76 def A。supersedes D75's nuisance-axis
# definition, tested above through round 5 — see `sweep_truth_investigation.md`
# for the full derivation and the four candidate definitions it rules out）
# ---------------------------------------------------------------------------

#: per-family (n_sweeps, rows_per_sweep) expected under def A on the
#: canonical 456-cell matrix (`sweep_truth_investigation.md` §Q3 table,
#: reproduced/verified here). FORMANT_GT's 5 distinct truth levels use the
#: *full* `pole_freqs_hz` tuple as truth identity (not the F1-only scalar
#: `campaign.selection_stage.truth_value_for_row()` uses for ranking) — this
#: sidesteps the known F1-collision ambiguity (`[UNDERSPEC-CAL-D13]`: two
#: pole sets share F1=500Hz) that would otherwise reduce one sweep's count
#: to 4 distinct levels / 9 resolvable pairs instead of 5 levels / 10 pairs.
_EXPECTED_DEF_A_SHAPE: dict[str, tuple[int, int]] = {
    "F0_CONTROL": (3, 4),
    "FORMANT_GT": (12, 5),
    "TILT_GT": (6, 5),
    "APERIODICITY_GT": (10, 6),
    "RESONANCE_GT": (6, 4),
    "TRANSITION_GT": (8, 3),
    "IDENTITY_CAUSAL_SWEEP": (12, 5),
}


def test_declared_sweeps_by_family_matches_investigation_table() -> None:
    """def A（sweep = nuisance/covariate 設定を固定し truth 水準だけを動かす
    truth-core block の行集合）の per-family (sweep 数, sweep あたり行数) が
    `sweep_truth_investigation.md` の調査結果と一致する。"""
    rows = build_matrix()
    row_by_id = {mr.row_id: mr.row for mr in rows}
    declared = declared_sweeps_by_family(rows)
    for family, (expected_n_sweeps, expected_rows_per_sweep) in _EXPECTED_DEF_A_SHAPE.items():
        sweeps = declared[family]
        assert len(sweeps) == expected_n_sweeps, (family, sorted(sweeps))
        for sweep_id, member_row_ids in sweeps.items():
            assert len(member_row_ids) == expected_rows_per_sweep, (family, sweep_id, member_row_ids)
            # every member row must actually belong to this family's TRUTH_CORE/PRIMARY block.
            for row_id in member_row_ids:
                row = row_by_id[row_id]
                assert row.family == family
                assert row.block == "TRUTH_CORE"
    # f0_hz/sr_hz are truth-core grid axes, not sweep-defining fields, for
    # every family except F0_CONTROL itself (whose truth field *is* f0_hz).
    assert set(declared) == set(_EXPECTED_DEF_A_SHAPE)


def test_declared_sweeps_never_split_by_positive_control_or_block() -> None:
    """UNDERSPEC-CAL-D76: sweep key は `positive_control`/`block` を含んでは
    ならない——含めると同じ nuisance 条件の行が anchor/non-anchor で別 sweep
    に誤って分断される（family anchor 行にのみ `positive_control=True` が
    立つため）。"""
    rows = build_matrix()
    row_by_id = {mr.row_id: mr.row for mr in rows}
    declared = declared_sweeps_by_family(rows)
    saw_mixed_positive_control_sweep = False
    for family, sweeps in declared.items():
        for member_row_ids in sweeps.values():
            flags = {row_by_id[rid].positive_control for rid in member_row_ids}
            if len(flags) > 1:
                saw_mixed_positive_control_sweep = True
    assert saw_mixed_positive_control_sweep, (
        "expected at least one declared sweep whose members mix "
        "positive_control True/False (anchor rows are TRUTH_CORE members "
        "too) — otherwise this test cannot distinguish the correct key from "
        "one that wrongly includes positive_control"
    )


def test_every_declared_sweep_has_at_least_3_distinct_truth_levels() -> None:
    """§10.4「resolvable pair は各 sweep で >= 3」の構造的前提: def A の下
    では sweep 内の PRIMARY 行数そのものではなく、相異なる truth level 数
    （`truth_identity_for_row()`）が `C(levels, 2) >= 3` を満たす必要が
    ある。"""
    rows = build_matrix()
    row_by_id = {mr.row_id: mr.row for mr in rows}
    declared = declared_sweeps_by_family(rows)
    for family, sweeps in declared.items():
        assert sweeps, f"{family} declares no sweeps at all"
        for sweep_id, member_row_ids in sweeps.items():
            n_levels = len({truth_identity_for_row(row_by_id[rid]) for rid in member_row_ids})
            assert n_levels >= MIN_RESOLVABLE_PAIRS_PER_SWEEP, (family, sweep_id, n_levels)


def test_starved_matrix_drops_a_sweep_below_the_minimum_truth_levels() -> None:
    """`declared_sweeps_by_family` は与えられた `matrix_rows` から素直に
    再計算するだけなので、matrix を人工的に飢餓状態へ書き換えれば違反を
    検出できる（`c0_validate` 側の fail-closed 検査が使うのと同じ入力形）。
    """
    rows = build_matrix()
    tilt_sweeps = declared_sweeps_by_family(rows)["TILT_GT"]
    sweep_id, member_row_ids = sorted(tilt_sweeps.items())[0]
    assert len(member_row_ids) == 5
    to_drop = set(member_row_ids[2:])  # keep 2 of 5 -> 2 distinct truth levels
    starved = [mr for mr in rows if mr.row_id not in to_drop]
    row_by_id = {mr.row_id: mr.row for mr in starved}
    starved_sweeps = declared_sweeps_by_family(starved)["TILT_GT"]
    n_levels = len({truth_identity_for_row(row_by_id[rid]) for rid in starved_sweeps[sweep_id]})
    assert n_levels == MIN_RESOLVABLE_PAIRS_PER_SWEEP - 1


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


# ---------------------------------------------------------------------------
# v1.2 WP2 — rehearsal 行列（縮小行列と 1 箇所切替）
# ---------------------------------------------------------------------------

#: `build_rehearsal_matrix()` の実測行数（決定論的部分集合なので固定値で
#: 縛る——canonical matrix の構成が変われば必ずここが落ち、rehearsal 経路の
#: 被覆を無言で失わないための番人）。
REHEARSAL_MATRIX_ROW_COUNT = 58


def test_rehearsal_matrix_is_deterministic_subset() -> None:
    """§A-3: 部分集合であること・`build_matrix()` の列挙順を保存すること・
    全 family / 全 control_class / 単一軸 nuisance 行 >= 1 / anchor >= 1 を
    満たすこと・呼び出しの間で決定論的であること。"""
    full = build_matrix()
    rehearsal = build_rehearsal_matrix()

    full_ids = [mr.row_id for mr in full]
    rehearsal_ids = [mr.row_id for mr in rehearsal]

    # 部分集合 + 重複なし。
    assert len(set(rehearsal_ids)) == len(rehearsal_ids)
    assert set(rehearsal_ids) <= set(full_ids)
    # 列挙順の保存（full の順で filter したものと完全一致）。
    kept = set(rehearsal_ids)
    assert rehearsal_ids == [rid for rid in full_ids if rid in kept]
    # 決定論（同一入力 -> 同一出力）。
    assert [mr.row_id for mr in build_rehearsal_matrix()] == rehearsal_ids
    # 行数は実測値で固定する。
    assert len(rehearsal) == REHEARSAL_MATRIX_ROW_COUNT

    families = {mr.row.family for mr in rehearsal}
    assert families == {family.value for family in axes.FAMILY_ORDER}

    # (c) 全 control_class が残る。
    assert {mr.row.control_class for mr in rehearsal if mr.row.control_class is not None} == {
        mr.row.control_class for mr in full if mr.row.control_class is not None
    }

    for family in axes.FAMILY_ORDER:
        fam_rows = [mr for mr in rehearsal if mr.row.family == family.value]
        # (a) truth core の declared sweep が §10.4 の下限を満たす。
        assert len([mr for mr in fam_rows if mr.row.block == "TRUTH_CORE"]) >= (
            MIN_RESOLVABLE_PAIRS_PER_SWEEP
        ), family
        # (b) 単一軸 nuisance 主効果の CONFOUND 行 >= 1。
        assert [
            mr
            for mr in fam_rows
            if mr.row.block == "CONFOUND" and single_axis_nuisance_tag_axis(mr.row) is not None
        ], family
        # (d) anchor（positive control）>= 1。
        assert [mr for mr in fam_rows if mr.row.positive_control], family


def test_rehearsal_matrix_declared_sweeps_meet_truth_level_floor() -> None:
    """縮小行列が生む declared sweep はすべて §10.4 の truth level 下限
    （`MIN_RESOLVABLE_PAIRS_PER_SWEEP`）を満たす——満たさないと
    `c0_validate._check_declared_sweep_truth_levels()` が rehearsal freeze を
    構造的に必ず BLOCK する（この不変が本 WP の C0 疎通の前提）。"""
    rehearsal = build_rehearsal_matrix()
    row_by_id = {mr.row_id: mr.row for mr in rehearsal}
    declared = declared_sweeps_by_family(rehearsal)
    for family, sweeps in declared.items():
        assert sweeps, family
        for sweep_id, member_ids in sweeps.items():
            levels = {truth_identity_for_row(row_by_id[rid]) for rid in member_ids}
            assert len(levels) >= MIN_RESOLVABLE_PAIRS_PER_SWEEP, (family, sweep_id, levels)


def test_active_matrix_switches_only_under_rehearsal_mode() -> None:
    """`active_matrix()` は既定で本番 456 セル、`set_rehearsal_mode(True)` の
    下でのみ縮小行列を返す（プロセス大域状態なので必ず戻す）。"""
    assert not rehearsal_mode()
    assert len(active_matrix()) == 456
    try:
        set_rehearsal_mode(True)
        assert rehearsal_mode()
        assert [mr.row_id for mr in active_matrix()] == [
            mr.row_id for mr in build_rehearsal_matrix()
        ]
    finally:
        set_rehearsal_mode(False)
    assert not rehearsal_mode()
    assert len(active_matrix()) == 456


#: `build_matrix()` を直接呼んでよい production モジュール。
#:
#: - `fixtures/matrix.py`: 定義元（`active_matrix()`/`build_rehearsal_matrix()`
#:   の実装そのものが呼ぶ）。
#: - `campaign/diagnose.py`: C-1 探索ステージ（RUN10-CAL-v1.2 WP4）。freeze も
#:   封印も ledger も持たない **armed campaign ではない** cheap gate であり、
#:   `--rehearsal` の対象外（rehearsal は「campaign 経路の疎通試験」であって、
#:   その前段の診断まで縮小する意味は無い）。
_BUILD_MATRIX_CALL_SITE_ALLOWLIST = frozenset(
    {
        "voice_genesis/calibration/fixtures/matrix.py",
        "voice_genesis/calibration/campaign/diagnose.py",
    }
)


def _direct_build_matrix_calls(source: str) -> list[int]:
    """`source` 中で `build_matrix(...)` を **実際に呼んでいる** 行番号
    （docstring/コメント中の言及は AST に現れないので自動的に除外される）。
    `x.build_matrix()` のような属性呼び出しも対象にする。"""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name == "build_matrix":
            lines.append(node.lineno)
    return lines


def test_no_direct_build_matrix_call_sites_outside_matrix_module() -> None:
    """§A-2: `build_matrix()` の直接呼び出しが allowlist 以外の production
    モジュールに無いことを全数で固定する（テストは対象外——テストは意図的に
    「常に本番 456 セル」を読む）。新しい call site が `active_matrix()` を
    経由し忘れると rehearsal 切替が片肺になるため、この全数検査を凍結する。"""
    calibration_root = Path(__file__).resolve().parents[1]
    repo_root = calibration_root.parents[1]
    offenders: list[str] = []
    for path in sorted(calibration_root.rglob("*.py")):
        rel = path.relative_to(repo_root).as_posix()
        if "/tests/" in f"/{rel}" or rel in _BUILD_MATRIX_CALL_SITE_ALLOWLIST:
            continue
        for lineno in _direct_build_matrix_calls(path.read_text(encoding="utf-8")):
            offenders.append(f"{rel}:{lineno}")
    assert not offenders, (
        "direct build_matrix() call site(s) outside the allowlist — route them "
        "through fixtures.matrix.active_matrix() so --rehearsal switches every "
        f"consumer at once: {offenders}"
    )


# ---------------------------------------------------------------------------
# v1.2 WP2b — rehearsal 候補プール（`candidates.registry.active_candidates()`）
# ---------------------------------------------------------------------------


def test_rehearsal_candidate_pool_is_deterministic_subset_in_registry_order() -> None:
    """縮小プールは `ALL_CANDIDATES` の部分集合であり、registry の宣言順を
    保存し、呼ぶたびに同一（手選び・辞書順依存が入っていない）。"""
    pool = candidate_registry.rehearsal_candidate_pool()
    assert pool == candidate_registry.rehearsal_candidate_pool()
    ids = [c.candidate_id for c in pool]
    all_ids = [c.candidate_id for c in candidate_registry.ALL_CANDIDATES]
    assert set(ids) <= set(all_ids)
    assert ids == [cid for cid in all_ids if cid in set(ids)]
    assert len(ids) == len(set(ids))


def test_rehearsal_candidate_pool_takes_b0_plus_one_usable_per_family() -> None:
    """規則の全数固定: family ごとに (i) B0 先頭 1 件 + (ii) `claim_ceiling !=
    NONE` の先頭 1 件（B0 を持たない family は (ii) の 1 件のみ）。"""
    pool = candidate_registry.rehearsal_candidate_pool()
    by_meter: dict[object, list[str]] = {}
    for c in pool:
        by_meter.setdefault(c.meter, []).append(c.candidate_id)

    all_meters = {c.meter for c in candidate_registry.ALL_CANDIDATES}
    assert set(by_meter) == all_meters, "縮小プールは全 meter family を残す"

    for meter in all_meters:
        family_all = [c for c in candidate_registry.ALL_CANDIDATES if c.meter == meter]
        expected: list[str] = []
        b0 = next((c for c in family_all if "-B0-" in c.candidate_id), None)
        if b0 is not None:
            expected.append(b0.candidate_id)
        usable = next(
            (
                c
                for c in family_all
                if c.candidate_id not in expected
                and c.claim_ceiling is not ClaimCeiling.NONE
            ),
            None,
        )
        if usable is not None:
            expected.append(usable.candidate_id)
        assert sorted(by_meter[meter]) == sorted(expected), meter
        assert len(by_meter[meter]) <= 2, meter


def test_active_candidates_switches_only_under_rehearsal_mode() -> None:
    """`active_candidates()` は行列と **同じ 1 つのフラグ**で切り替わる
    （行列だけ縮小して候補は全件、のような未定義の組を作らせない）。"""
    assert not rehearsal_mode()
    assert candidate_registry.active_candidates() == candidate_registry.ALL_CANDIDATES
    try:
        set_rehearsal_mode(True)
        assert (
            candidate_registry.active_candidates()
            == candidate_registry.rehearsal_candidate_pool()
        )
        for meter in {c.meter for c in candidate_registry.ALL_CANDIDATES}:
            assert candidate_registry.active_candidates_for_meter(meter) == tuple(
                c for c in candidate_registry.rehearsal_candidate_pool() if c.meter == meter
            )
    finally:
        set_rehearsal_mode(False)
    assert candidate_registry.active_candidates() == candidate_registry.ALL_CANDIDATES


#: `ALL_CANDIDATES` / `candidates_for_meter()` を **直接**読んでよい production
#: モジュールと、そこでの参照回数（AST 上の Name/Attribute 出現数）。
#:
#: - `candidates/registry.py`: 定義元（`active_candidates()` の実装が読む）。
#: - `c0_validate.py`: 凍結 manifest を **registry 全体**と突合する検証器。
#:   `independence_ledger` の全件一致（99 候補の tier 宣言）と、v1.2 WP2b の
#:   候補空間 x rehearsal フラグ検査（本番は全件・rehearsal は縮小プール）。
#: - `e_use_table.py`: E_use evidence 表の key 集合は registry 全体に対する
#:   静的 config の被覆要求であり、campaign の候補空間とは別物。
#: - `c0_freeze.py`: `independence_ledger`（registry 全体の tier 宣言）と
#:   `max_claim_scope` の construct 語彙検査。どちらも候補空間ではない。
#: - `campaign/diagnose.py`: C-1 探索ステージ（WP4）。freeze も封印も ledger も
#:   持たない armed campaign ではない cheap gate であり `--rehearsal` の対象外
#:   （`_BUILD_MATRIX_CALL_SITE_ALLOWLIST` と同じ判定）。
_CANDIDATE_ENUMERATION_ALLOWLIST: dict[str, dict[str, int]] = {
    # `candidates_for_meter` の定義そのもの（`ast.FunctionDef`）は Name/
    # Attribute ではないのでここには数えられない。
    "voice_genesis/calibration/candidates/registry.py": {"ALL_CANDIDATES": 6},
    "voice_genesis/calibration/c0_validate.py": {"ALL_CANDIDATES": 4},
    "voice_genesis/calibration/e_use_table.py": {"ALL_CANDIDATES": 1},
    "voice_genesis/calibration/c0_freeze.py": {"ALL_CANDIDATES": 2},
    "voice_genesis/calibration/campaign/diagnose.py": {"candidates_for_meter": 2},
}


def _candidate_enumeration_references(source: str) -> dict[str, int]:
    """`source` 中の `ALL_CANDIDATES` / `candidates_for_meter` 参照回数
    （Name / Attribute の双方。docstring/コメントは AST に現れない）。"""
    counts: dict[str, int] = {}
    for node in ast.walk(ast.parse(source)):
        name = (
            node.id
            if isinstance(node, ast.Name)
            else node.attr
            if isinstance(node, ast.Attribute)
            else None
        )
        if name in {"ALL_CANDIDATES", "candidates_for_meter"}:
            counts[name] = counts.get(name, 0) + 1
    return counts


def test_candidate_enumeration_call_sites_are_frozen() -> None:
    """v1.2 WP2b: 候補列挙の call site を全数で固定する。

    `--rehearsal` の縮小プールは `active_candidates()` の 1 箇所でしか
    切り替わらないため、新しい call site が `ALL_CANDIDATES` /
    `candidates_for_meter()` を直接読むと rehearsal が片肺になる（行列だけ
    縮小され候補は 99 のまま = 実測 3 時間の元凶）。allowlist に載る参照は
    「registry 全体を意図的に見る検証器/静的 config」に限る。"""
    calibration_root = Path(__file__).resolve().parents[1]
    repo_root = calibration_root.parents[1]
    actual: dict[str, dict[str, int]] = {}
    for path in sorted(calibration_root.rglob("*.py")):
        rel = path.relative_to(repo_root).as_posix()
        if "/tests/" in f"/{rel}":
            continue
        counts = _candidate_enumeration_references(path.read_text(encoding="utf-8"))
        if counts:
            actual[rel] = counts
    assert actual == _CANDIDATE_ENUMERATION_ALLOWLIST, (
        "candidate enumeration call sites changed — route production consumers "
        "through candidates.registry.active_candidates()/active_candidates_for_meter() "
        "so --rehearsal switches every consumer at once, then update this frozen "
        f"allowlist with the reason: {actual}"
    )
