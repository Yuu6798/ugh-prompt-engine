"""v1.1 §V2.2/§V2.3 — holdout sweep pinning（2 段割当）の単体・property テスト。

Design Memo AC6/AC7（`DESIGN_VG_METER_CAL_DEBT_v1.1.md` §V2 正本）:

- AC6: ダミー secret >=12 種での 456 セル realized split property テスト。
  各 family の pin sweep 数が k_hold 期待値で member 全行 HOLDOUT / pin 外
  truth-core 行の holdout 割当 0 / family 合計 50/25/25 厳密一致 / 既存
  coverage 制約充足 / verify_split 照合成立。
- AC7 は `tests/test_campaign_cli.py::
  test_c4_directional_holdout_sweeps_manifest_narrows_expected_sweep_ids` で
  別途固定する。

本ファイルは matrix 側 (`fixtures.matrix`) の pin アルゴリズム単体と、
splitter との統合 (§V2.2 段 1+段 2) を検証する。production の row→RowInput
変換・stratum 化因子は `c0_freeze._row_inputs_for_split()` /
`c0_freeze.STRATUM_FACTOR_NAMES` をそのまま使う（`c0_freeze.armed_freeze()`
が実際に呼ぶのと同一の入口 — テスト用の簡略変換を独自に持たない）。
"""

from __future__ import annotations

import dataclasses
import hashlib

import pytest

from voice_genesis.calibration.c0_freeze import (
    STRATUM_FACTOR_NAMES,
    _realized_split_to_dict,
    _row_inputs_for_split,
)
from voice_genesis.calibration.campaign.state import _realized_split_from_manifest
from voice_genesis.calibration.fixtures import axes
from voice_genesis.calibration.fixtures.matrix import (
    HoldoutPinInfeasible,
    build_matrix,
    claim_relevant_fields_by_family,
    declared_sweeps_by_family,
    holdout_pin_params_by_family,
    pin_holdout_sweeps_by_family,
)
from voice_genesis.calibration.splitter import _required_pairs, _axis_value, realize_split, verify_split
from voice_genesis.calibration.vocab import Split

#: 決定論なダミー secret >=12 種（AC6 要件）。sha256 で導出し、値そのものは
#: 意味を持たない（本モジュール内の一貫した再現性のためだけの定数）。
DUMMY_SECRETS: tuple[bytes, ...] = tuple(
    hashlib.sha256(f"vg-cal-v1.1-holdout-pin-dummy-secret-{i}".encode()).digest()
    for i in range(14)
)

#: §V2.2 の k_hold 実測値表（本 Design Memo R2 節・設計文書 §V2.2 末尾の
#: 数値注記と同一。456 セル canonical matrix でのみ成立する凍結値）。
EXPECTED_K_HOLD: dict[str, int] = {
    "F0_CONTROL": 1,
    "FORMANT_GT": 3,
    "TILT_GT": 2,
    "APERIODICITY_GT": 2,
    "RESONANCE_GT": 2,
    "TRANSITION_GT": 2,
    "IDENTITY_CAUSAL_SWEEP": 4,
}

#: §V2.2 5th bullet の claim-relevant field 機械導出の凍結帰結。
EXPECTED_CLAIM_RELEVANT_FIELDS: dict[str, tuple[str, ...]] = {
    "F0_CONTROL": (),
    "FORMANT_GT": (),
    "TILT_GT": (),
    "APERIODICITY_GT": ("bandwise_band",),
    "RESONANCE_GT": (),
    "TRANSITION_GT": ("duration_class", "join_type"),
    "IDENTITY_CAUSAL_SWEEP": ("founder_id", "trait"),
}


# ---------------------------------------------------------------------------
# k_hold / claim-relevant field 実測値表（secret 非依存）
# ---------------------------------------------------------------------------


def test_k_hold_matches_v2_2_frozen_table() -> None:
    rows = build_matrix()
    params = holdout_pin_params_by_family(rows)
    assert {fam: p.k_hold for fam, p in params.items()} == EXPECTED_K_HOLD
    assert all(p.feasible for p in params.values())


def test_holdout_pin_params_structural_values() -> None:
    """S（宣言 sweep 数）/ r（sweep 当たり member 行数）/ N_hold（family
    holdout 目標行数）の実測値を §V2.2 の記述と突き合わせる。"""
    rows = build_matrix()
    params = holdout_pin_params_by_family(rows)
    expected = {
        # family: (S, r, N_hold, max_field_cardinality)
        "F0_CONTROL": (3, 4, 12, 1),
        "FORMANT_GT": (12, 5, 24, 2),
        "TILT_GT": (6, 5, 12, 1),
        "APERIODICITY_GT": (10, 6, 18, 1),
        "RESONANCE_GT": (6, 4, 12, 1),
        "TRANSITION_GT": (8, 3, 12, 1),
        "IDENTITY_CAUSAL_SWEEP": (12, 5, 24, 4),
    }
    for fam, (s, r, n_hold, max_card) in expected.items():
        p = params[fam]
        assert (p.sweep_count, p.member_rows_per_sweep, p.n_hold, p.max_field_cardinality) == (
            s,
            r,
            n_hold,
            max_card,
        ), fam


def test_claim_relevant_fields_match_v2_2_frozen_table() -> None:
    rows = build_matrix()
    derived = claim_relevant_fields_by_family(rows)
    assert derived == EXPECTED_CLAIM_RELEVANT_FIELDS


# ---------------------------------------------------------------------------
# AC6: pin 選抜の被覆保証（sweep stratum key の全値被覆）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("secret", DUMMY_SECRETS)
def test_pin_selection_covers_formant_generator_impl_both_values(secret: bytes) -> None:
    rows = build_matrix()
    row_by_id = {mr.row_id: mr.row for mr in rows}
    pinned = pin_holdout_sweeps_by_family(rows, secret)["FORMANT_GT"]
    assert len(pinned) == EXPECTED_K_HOLD["FORMANT_GT"]
    impls = {row_by_id[rid].generator_impl for members in pinned.values() for rid in members}
    assert impls == {"cascade", "additive"}


@pytest.mark.parametrize("secret", DUMMY_SECRETS)
def test_pin_selection_covers_identity_all_founders_and_traits(secret: bytes) -> None:
    rows = build_matrix()
    row_by_id = {mr.row_id: mr.row for mr in rows}
    pinned = pin_holdout_sweeps_by_family(rows, secret)["IDENTITY_CAUSAL_SWEEP"]
    assert len(pinned) == EXPECTED_K_HOLD["IDENTITY_CAUSAL_SWEEP"]
    founders = {row_by_id[rid].founder_id for members in pinned.values() for rid in members}
    traits = {row_by_id[rid].trait for members in pinned.values() for rid in members}
    assert founders == set(axes.IDENTITY_FOUNDER_IDS)
    assert traits == set(axes.IDENTITY_TRAITS)


@pytest.mark.parametrize("secret", DUMMY_SECRETS)
def test_pin_selection_is_deterministic_and_subset_of_declared(secret: bytes) -> None:
    rows = build_matrix()
    declared = declared_sweeps_by_family(rows)
    pinned_a = pin_holdout_sweeps_by_family(rows, secret)
    pinned_b = pin_holdout_sweeps_by_family(rows, secret)
    assert pinned_a == pinned_b
    for family, sweeps in pinned_a.items():
        assert len(sweeps) == EXPECTED_K_HOLD[family]
        for sweep_id, member_row_ids in sweeps.items():
            assert sweep_id in declared[family]
            assert member_row_ids == declared[family][sweep_id]


def test_pin_selection_differs_across_secrets() -> None:
    rows = build_matrix()
    results = {pin_holdout_sweeps_by_family(rows, s)["FORMANT_GT"].keys().__str__() for s in DUMMY_SECRETS[:6]}
    # 全 secret で同一選抜になるほど偶然が重なるとは考えにくい（決定論だが
    # secret 依存であることの緩い実測確認）。
    assert len(results) > 1


# ---------------------------------------------------------------------------
# AC6: splitter 統合（2 段割当）— 456 セル canonical matrix、>=12 secret
# ---------------------------------------------------------------------------


def _pinned_row_ids(holdout_sweeps: dict[str, dict[str, tuple[str, ...]]]) -> frozenset[str]:
    return frozenset(
        rid
        for family_sweeps in holdout_sweeps.values()
        for member_row_ids in family_sweeps.values()
        for rid in member_row_ids
    )


@pytest.mark.parametrize("secret", DUMMY_SECRETS)
def test_realize_split_pin_integration_full_matrix(secret: bytes) -> None:
    """AC6 (a)-(e) を単一 secret について検証する（parametrize で >=12 回
    実行）。"""
    rows = build_matrix()
    row_inputs = _row_inputs_for_split(rows, STRATUM_FACTOR_NAMES)
    rows_by_id = {r.row_id: r for r in row_inputs}
    holdout_sweeps = pin_holdout_sweeps_by_family(rows, secret)
    pinned_ids = _pinned_row_ids(holdout_sweeps)

    realized = realize_split(
        row_inputs, secret, STRATUM_FACTOR_NAMES, pinned_holdout_row_ids=pinned_ids
    )

    # (a) 各 family の pin sweep 数が k_hold 期待値で member 全行 HOLDOUT。
    for family, sweeps in holdout_sweeps.items():
        assert len(sweeps) == EXPECTED_K_HOLD[family]
        for member_row_ids in sweeps.values():
            for rid in member_row_ids:
                assert realized.assignment[rid] == Split.HOLDOUT

    # (b) pin 外の truth-core 行の holdout 割当は 0。
    truth_core_ids = {rid for rid, r in rows_by_id.items() if r.truth_level == "TRUTH_CORE"}
    non_pinned_truth_core = truth_core_ids - pinned_ids
    assert all(realized.assignment[rid] != Split.HOLDOUT for rid in non_pinned_truth_core)

    # (c) family 合計 50/25/25 の厳密一致。
    for family, (_t, _c, _b, total_n) in axes.FAMILY_COUNTS.items():
        fam_key = family.value
        fam_rows = [r for r in row_inputs if r.family == fam_key]
        counts = {Split.CALIBRATION: 0, Split.SELECTION: 0, Split.HOLDOUT: 0}
        for r in fam_rows:
            counts[realized.assignment[r.row_id]] += 1
        assert sum(counts.values()) == total_n == len(fam_rows)
        assert counts[Split.CALIBRATION] == total_n // 2
        assert counts[Split.SELECTION] == total_n // 4
        assert counts[Split.HOLDOUT] == total_n // 4

    # (d) 既存 coverage 制約充足: 各 split に >=1 BOUNDARY 行、FORMANT 両
    # generator_impl、IDENTITY 全 founder・全 trait（`_required_pairs`/
    # `_axis_value` を直接使い、splitter が実際に検査する制約と同じ規約で
    # 判定する）。
    for family in axes.FAMILY_ORDER:
        fam_key = family.value
        fam_rows = [r for r in row_inputs if r.family == fam_key]
        required = _required_pairs(fam_rows)
        for (axis, value), min_count in required.items():
            for split in (Split.CALIBRATION, Split.SELECTION, Split.HOLDOUT):
                cnt = sum(
                    1
                    for r in fam_rows
                    if realized.assignment[r.row_id] == split and _axis_value(r, axis) == value
                )
                assert cnt >= min_count, (fam_key, axis, value, split, cnt)
        # explicit BOUNDARY >=1 per split (subsumed by _required_pairs above
        # when count>=3, but assert directly too for the AC's own wording).
        boundary_rows = [r for r in fam_rows if r.boundary_class == "BOUNDARY"]
        if len(boundary_rows) >= 3:
            for split in (Split.CALIBRATION, Split.SELECTION, Split.HOLDOUT):
                assert any(realized.assignment[r.row_id] == split for r in boundary_rows)

    formant_rows = [r for r in row_inputs if r.family == "FORMANT_GT"]
    for split in (Split.CALIBRATION, Split.SELECTION, Split.HOLDOUT):
        impls = {
            r.generator_impl
            for r in formant_rows
            if realized.assignment[r.row_id] == split and r.generator_impl is not None
        }
        assert {"cascade", "additive"} <= impls

    # (e) verify_split 照合成立。
    assert verify_split(row_inputs, secret, realized) is True


def test_no_pin_path_is_unaffected_by_pinning_machinery() -> None:
    """`pinned_holdout_row_ids` を渡さない（既定 = 空 frozenset）呼び出しは
    v1.0 以来の挙動と完全に同一のまま——回帰確認。"""
    rows = build_matrix()
    row_inputs = _row_inputs_for_split(rows, STRATUM_FACTOR_NAMES)
    realized = realize_split(row_inputs, DUMMY_SECRETS[0], STRATUM_FACTOR_NAMES)
    assert len(realized.assignment) == axes.TOTAL_LOGICAL_CELLS
    assert realized.pinned_holdout_row_ids == frozenset()
    assert verify_split(row_inputs, DUMMY_SECRETS[0], realized) is True


# ---------------------------------------------------------------------------
# HoldoutPinInfeasible（合成の infeasible シナリオ）
# ---------------------------------------------------------------------------


def test_holdout_pin_infeasible_when_cap_below_required_cardinality() -> None:
    """`IDENTITY_CAUSAL_SWEEP` 型（founder x trait 2-field stratum key）を、
    N_hold を極端に小さくした合成 matrix で再現し、cap
    `floor((N_hold-1)/r)` が `max_field_cardinality`（founder 4 値）を
    下回る場合に `HoldoutPinInfeasible` が fail-closed で送出されることを
    確認する（456 セルでは発生しない経路の直接検証）。"""
    import voice_genesis.calibration.fixtures.matrix as matrix_mod

    rows = [
        mr
        for mr in build_matrix()
        if mr.row.family == "IDENTITY_CAUSAL_SWEEP" and mr.row.block == "TRUTH_CORE"
    ]
    assert rows, "expected IDENTITY_CAUSAL_SWEEP TRUTH_CORE rows"

    # N_hold は `axes.FAMILY_COUNTS[family][3] // 4` から導出されるため、
    # family total を細工した `FixtureFamily`-keyed dict を monkeypatch する
    # のではなく、`holdout_pin_params_by_family`/`pin_holdout_sweeps_by_family`
    # が読む `axes.FAMILY_COUNTS` を直接一時的に差し替える。
    from voice_genesis.calibration.fixtures.axes import FixtureFamily

    original_counts = matrix_mod.axes.FAMILY_COUNTS
    tiny_counts = dict(original_counts)
    # r=5 (IDENTITY member_rows_per_sweep) のとき N_hold=4 なら
    # cap=floor((4-1)/5)=0 < max_field_cardinality(4) -> infeasible。
    tiny_counts[FixtureFamily.IDENTITY_CAUSAL_SWEEP] = (60, 24, 12, 16)
    matrix_mod.axes.FAMILY_COUNTS = tiny_counts
    try:
        params = holdout_pin_params_by_family(rows)
        assert params["IDENTITY_CAUSAL_SWEEP"].feasible is False
        with pytest.raises(HoldoutPinInfeasible) as excinfo:
            pin_holdout_sweeps_by_family(rows, DUMMY_SECRETS[0])
        assert excinfo.value.family == "IDENTITY_CAUSAL_SWEEP"
    finally:
        matrix_mod.axes.FAMILY_COUNTS = original_counts


# ---------------------------------------------------------------------------
# 逸脱発見の回帰固定: `campaign.state._realized_split_from_manifest()`
# （`c0_freeze.py` とは独立実装、`campaign/state.py` docstring 参照）も
# `pinned_holdout_row_ids` を読み戻さなければ、v1.1 pin を使った実
# campaign の manifest を読み込んだ `RealizedSplitMap` で `verify_split()`
# が常に偽の tamper 検出を返す（`provenance.Ledger.check_leakage` が
# campaign 実行時にこの経路を使う——BLOCKED_LEAKAGE の偽陽性）。
# ---------------------------------------------------------------------------


def test_realized_split_manifest_round_trip_preserves_pin_for_verify_split() -> None:
    rows = build_matrix()
    row_inputs = _row_inputs_for_split(rows, STRATUM_FACTOR_NAMES)
    secret = DUMMY_SECRETS[0]
    holdout_sweeps = pin_holdout_sweeps_by_family(rows, secret)
    pinned_ids = _pinned_row_ids(holdout_sweeps)
    realized = realize_split(
        row_inputs, secret, STRATUM_FACTOR_NAMES, pinned_holdout_row_ids=pinned_ids
    )

    # manifest への書出し（`c0_freeze._attach_freeze_extras()` が実際に行う
    # のと同一の shape）+ `campaign.state` の独立読み戻し。
    manifest_dict = _realized_split_to_dict(realized)
    roundtripped = _realized_split_from_manifest(manifest_dict)

    assert roundtripped.pinned_holdout_row_ids == frozenset(pinned_ids)
    assert verify_split(row_inputs, secret, roundtripped) is True

    # 回帰再現: `pinned_holdout_row_ids` を読み戻さなかった場合（修正前の
    # 挙動）は、pin を使った実 assignment を「改竄」として誤検出する。
    without_pin_field = dataclasses.replace(roundtripped, pinned_holdout_row_ids=frozenset())
    assert verify_split(row_inputs, secret, without_pin_field) is False
