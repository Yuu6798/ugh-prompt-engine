"""v1.1 §V2.2/§V2.3 — `c0_validate` の holdout sweep pinning 検証（D77 同型）。

対象: `_check_claim_relevant_fields_match()` / `_check_holdout_pin_
feasibility()` / `_check_holdout_sweeps_declaration_match()` /
`_check_holdout_sweeps_realized_membership()`（いずれも `c0_validate.py`
非公開関数だが、既存の D76/D77 系テスト — `test_c0_validate.py` の
`test_declared_sweeps_*` — と同じ規約でモジュール内部関数を直接 import して
検証する）。

新規キー（`claim_relevant_fields`/`holdout_sweeps`）はどちらも
`FIXTURE_SPEC_REQUIRED_KEYS` に加えていない（v1.1 以前の manifest fixture
との後方互換のため）ため、既存の `_complete_manifest()`（`test_c0_validate.py`）
は本テストの対象外——ここでは最小限の手組み manifest を直接使う。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_genesis.calibration import c0_validate, vocab
from voice_genesis.calibration.c0_freeze import STRATUM_FACTOR_NAMES, _row_inputs_for_split
from voice_genesis.calibration.canonical import canonical_json
from voice_genesis.calibration.canonical import manifest_sha as canonical_manifest_sha
from voice_genesis.calibration.fixtures import axes as fixture_axes
from voice_genesis.calibration.fixtures import uncertainty as fixture_uncertainty
from voice_genesis.calibration.fixtures.matrix import (
    build_matrix,
    claim_relevant_fields_by_family,
    declared_sweeps_by_family,
    invariance_axes_by_family,
    pin_holdout_sweeps_by_family,
)
from voice_genesis.calibration.provenance import Ledger
from voice_genesis.calibration.splitter import pin_and_realize_holdout
from voice_genesis.calibration.tools import archive_aborted_ledger
from voice_genesis.calibration.vocab import Split

_ROWS = build_matrix()
_ROW_INPUTS = _row_inputs_for_split(_ROWS, STRATUM_FACTOR_NAMES)
_DERIVED_CLAIM_RELEVANT = claim_relevant_fields_by_family(_ROWS)
_DERIVED_INVARIANCE_AXES = invariance_axes_by_family(_ROWS)
_DECLARED_SWEEPS = declared_sweeps_by_family(_ROWS)
_SECRET = b"\x42" * 32
#: v1.1 §V3.5 実装時発見（2026-09-05）: `_check_holdout_sweeps_declaration_
#: match()` は `splitter.pin_and_realize_holdout()`（縮退リトライ込み、
#: `c0_freeze.armed_freeze()` と同一入口）で再導出照合する——`TILT_GT` は
#: `nuisance_axis` coverage 制約により nominal k_hold=2 では coverage
#: 修復不能で全 secret 決定論的に k_hold=1 へ縮退するため、この fixture も
#: 生の `pin_holdout_sweeps_by_family()`（nominal、縮退なし）ではなく同じ
#: 縮退込みの入口で pin を計算しなければ、"正しい宣言" のはずの fixture
#: 自体が再導出照合で mismatch 扱いになる。
_PINNED, _ = pin_and_realize_holdout(_ROWS, _ROW_INPUTS, _SECRET, STRATUM_FACTOR_NAMES)


def _manifest_with_fixture_spec(fixture_spec: dict[str, object]) -> dict[str, object]:
    return {"frozen_design": {"fixture_spec": fixture_spec}}


def _v1_1_manifest_with_fixture_spec(fixture_spec: dict[str, object]) -> dict[str, object]:
    """`_manifest_with_fixture_spec()` に v1.1 version marker
    (`frozen_design.design_revision`) を付加した版（R20-3、Codex 第 20 巡
    finding (3)）。`c0_freeze._DESIGN_REVISION` の値をリテラルで直接埋め込む
    （import すると生成器側の値変更が本テストの意図を隠して素通りしてしまう
    ため、意図的に独立したリテラル文字列にする）。"""
    return {"frozen_design": {"design_revision": "1.1", "fixture_spec": fixture_spec}}


# ---------------------------------------------------------------------------
# _check_claim_relevant_fields_match
# ---------------------------------------------------------------------------


def test_claim_relevant_fields_absent_is_not_a_violation() -> None:
    """新規キーであり REQUIRED ではないため、欠落は無視する（v1.1 以前の
    manifest との後方互換）。"""
    manifest = _manifest_with_fixture_spec({"TRANSITION_GT": {}})
    violations = c0_validate._check_claim_relevant_fields_match(manifest)
    assert violations == ()


def test_claim_relevant_fields_correct_declaration_passes() -> None:
    manifest = _manifest_with_fixture_spec(
        {
            "TRANSITION_GT": {"claim_relevant_fields": list(_DERIVED_CLAIM_RELEVANT["TRANSITION_GT"])},
            "APERIODICITY_GT": {
                "claim_relevant_fields": list(_DERIVED_CLAIM_RELEVANT["APERIODICITY_GT"])
            },
            "TILT_GT": {"claim_relevant_fields": []},
        }
    )
    violations = c0_validate._check_claim_relevant_fields_match(manifest)
    assert violations == ()


def test_claim_relevant_fields_wrong_declaration_blocks() -> None:
    manifest = _manifest_with_fixture_spec(
        {"TRANSITION_GT": {"claim_relevant_fields": ["join_type"]}}  # missing duration_class
    )
    violations = c0_validate._check_claim_relevant_fields_match(manifest)
    assert len(violations) == 1
    assert violations[0].violation == "claim_relevant_field_mismatch"
    assert violations[0].family == "TRANSITION_GT"

    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert result.claim_relevant_field_violations == violations


def test_claim_relevant_fields_non_list_value_blocks() -> None:
    manifest = _manifest_with_fixture_spec({"TRANSITION_GT": {"claim_relevant_fields": "oops"}})
    violations = c0_validate._check_claim_relevant_fields_match(manifest)
    assert len(violations) == 1
    assert violations[0].family == "TRANSITION_GT"


# ---------------------------------------------------------------------------
# _check_invariance_axes_match (v1.1 §V3.5)
# ---------------------------------------------------------------------------


def test_invariance_axes_absent_is_not_a_violation() -> None:
    """`confound_axes` の非空 list 形状は `_LIST_SHAPE_FIELDS` が別途要求する
    ため、ここではキー欠落そのものは violation にしない。"""
    manifest = _manifest_with_fixture_spec({"TRANSITION_GT": {}})
    violations = c0_validate._check_invariance_axes_match(manifest)
    assert violations == ()


def test_invariance_axes_correct_declaration_passes_for_all_families() -> None:
    manifest = _manifest_with_fixture_spec(
        {
            fam: {"confound_axes": list(axes)}
            for fam, axes in _DERIVED_INVARIANCE_AXES.items()
        }
    )
    violations = c0_validate._check_invariance_axes_match(manifest)
    assert violations == ()


def test_invariance_axes_flat_six_tuple_is_rejected() -> None:
    """回帰ガード: 旧 `c0_freeze._CONFOUND_AXES` の flat 6-tuple（f0_hz/
    sr_hz を含む）は、正典 456 セル matrix のどの family の実導出値とも
    一致しないため、宣言すれば必ず mismatch として fail-closed する。"""
    stale_flat_tuple = ["f0_hz", "sr_hz", "gain_dbfs", "duration_s", "noise_snr_db", "context"]
    manifest = _manifest_with_fixture_spec(
        {"F0_CONTROL": {"confound_axes": stale_flat_tuple}}
    )
    violations = c0_validate._check_invariance_axes_match(manifest)
    assert len(violations) == 1
    assert violations[0].violation == "invariance_axis_declaration_mismatch"
    assert violations[0].family == "F0_CONTROL"

    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert result.invariance_axis_violations == violations


def test_invariance_axes_non_list_value_blocks() -> None:
    manifest = _manifest_with_fixture_spec({"TRANSITION_GT": {"confound_axes": "oops"}})
    violations = c0_validate._check_invariance_axes_match(manifest)
    assert len(violations) == 1
    assert violations[0].family == "TRANSITION_GT"


# ---------------------------------------------------------------------------
# _check_u_gt_u_num_bounds (v1.1 §V3.3 末尾)
# ---------------------------------------------------------------------------


def test_u_gt_u_num_bounds_absent_key_is_not_a_violation() -> None:
    """v1.0 形式（`u_gt_bound`/`u_num_bound` キー自体が無い legacy manifest）
    は version-aware にスキップする（後方互換）。"""
    manifest = _manifest_with_fixture_spec({"TILT_GT": {}})
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert violations == ()


def test_u_gt_u_num_bounds_valid_non_absent_family_passes() -> None:
    manifest = _manifest_with_fixture_spec(
        {
            "TILT_GT": {
                "u_gt_bound": 0.0,
                "u_gt_bound_formula": "U_GT = 0 (analytic)",
                "u_num_bound": 0.024,
                "u_num_bound_formula": "U_num = derive_floor(...)",
            }
        }
    )
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert violations == ()


def test_u_gt_u_num_bounds_valid_absent_family_passes() -> None:
    manifest = _manifest_with_fixture_spec(
        {
            "RESONANCE_GT": {
                "u_gt_bound": "ABSENT:diagnostic_only",
                "u_gt_bound_formula": "no gate input",
                "u_num_bound": "ABSENT:diagnostic_only",
                "u_num_bound_formula": "no gate input",
            }
        }
    )
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert violations == ()


def test_u_gt_u_num_bounds_negative_value_blocks() -> None:
    manifest = _manifest_with_fixture_spec(
        {
            "TILT_GT": {
                "u_gt_bound": -1.0,
                "u_gt_bound_formula": "bogus",
                "u_num_bound": 0.024,
                "u_num_bound_formula": "bogus",
            }
        }
    )
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert len(violations) == 1
    assert violations[0].violation == "u_bound_missing_or_invalid"
    assert violations[0].family == "TILT_GT"
    assert violations[0].sweep_id == "u_gt_bound"

    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert result.u_gt_u_num_bound_violations == violations


def test_u_gt_u_num_bounds_nonfinite_value_blocks() -> None:
    manifest = _manifest_with_fixture_spec(
        {
            "TILT_GT": {
                "u_gt_bound": float("nan"),
                "u_gt_bound_formula": "bogus",
                "u_num_bound": 0.024,
                "u_num_bound_formula": "bogus",
            }
        }
    )
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert len(violations) == 1
    assert violations[0].sweep_id == "u_gt_bound"


def test_u_gt_u_num_bounds_missing_formula_blocks() -> None:
    manifest = _manifest_with_fixture_spec(
        {
            "TILT_GT": {
                "u_gt_bound": 0.0,
                "u_gt_bound_formula": "",  # empty -> blocked
                "u_num_bound": 0.024,
                "u_num_bound_formula": "U_num = derive_floor(...)",
            }
        }
    )
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert len(violations) == 1
    assert violations[0].sweep_id == "u_gt_bound_formula"


def test_u_gt_u_num_bounds_absent_family_with_numeric_value_blocks() -> None:
    """ABSENT-only family（RESONANCE_GT/IDENTITY_CAUSAL_SWEEP）に数値を宣言
    したら fail-closed（`c0_freeze._U_ABSENT_REASON` の契約違反）。"""
    manifest = _manifest_with_fixture_spec(
        {
            "IDENTITY_CAUSAL_SWEEP": {
                "u_gt_bound": 0.0,
                "u_gt_bound_formula": "should have been ABSENT",
                "u_num_bound": "ABSENT:no_physical_ground_truth",
                "u_num_bound_formula": "no gate input",
            }
        }
    )
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert len(violations) == 1
    assert violations[0].family == "IDENTITY_CAUSAL_SWEEP"
    assert violations[0].sweep_id == "u_gt_bound"


def test_u_gt_u_num_bounds_real_c0_freeze_manifest_passes() -> None:
    """実 `c0_freeze.build_manifest()` の出力が本検査を通過することを固定
    する（producer/validator 間の契約回帰ガード）。R20-3 対応後は
    `build_manifest()` が常に `frozen_design.design_revision="1.1"` の
    version marker を付けるため、この manifest は
    `_check_u_gt_u_num_bounds()` の**必須化 (v1.1) 経路**を通る——本テストは
    その経路が real producer 出力に対して偽陽性にならないことも固定する。
    """
    from voice_genesis.calibration import c0_freeze

    manifest = c0_freeze.build_manifest(
        c0_freeze._REPO_ROOT, approvals={}, campaign_date_utc="2026-09-05"
    )
    assert manifest["frozen_design"]["design_revision"] == "1.1"
    assert c0_validate._is_v1_1_manifest(manifest)
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert violations == ()
    axis_violations = c0_validate._check_invariance_axes_match(manifest)
    assert axis_violations == ()


# ---------------------------------------------------------------------------
# _check_u_gt_u_num_bounds -- R20-3 (Codex 第 20 巡 finding (3)): v1.1 manifest
# は design_revision marker 経由で U_GT/U_num 境界の両フィールドを必須化する。
# ---------------------------------------------------------------------------


def test_u_gt_u_num_bounds_v1_1_manifest_missing_both_bounds_blocks() -> None:
    """finding の再現ケース: v1.1 の完全 manifest から `u_gt_bound`/
    `u_num_bound`（と両 formula・両 unit）を丸ごと消すと、v1.0 legacy 経路
    のように黙って `continue` せず、6 件 (bound x2 + formula x2 + unit x2)
    の violation が出て fail-closed になる。加えて R22-2（Codex 第 22 巡
    finding (2)）: `u_bound_inputs` も同じ manifest には無いため、canonical
    再導出チェック側の欠落 violation も 1 件追加される。"""
    manifest = _v1_1_manifest_with_fixture_spec({"TILT_GT": {}})
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    sweep_ids = {v.sweep_id for v in violations}
    assert sweep_ids == {
        "u_gt_bound",
        "u_gt_bound_formula",
        "u_gt_bound_unit",
        "u_num_bound",
        "u_num_bound_formula",
        "u_num_bound_unit",
        "u_bound_inputs",
    }
    assert all(v.family == "TILT_GT" for v in violations)
    assert all(v.violation == "u_bound_missing_or_invalid" for v in violations)

    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert result.u_gt_u_num_bound_violations == violations


def test_u_gt_u_num_bounds_v1_1_manifest_missing_one_bound_blocks() -> None:
    """v1.1 manifest から `u_num_bound`（+ その formula・unit）だけを消しても
    検出される（`u_gt_bound` 側は完備のため無傷）。この manifest には
    `u_bound_inputs` も無いため、R22-2 の canonical 再導出チェック側の欠落
    violation も 1 件追加される。"""
    manifest = _v1_1_manifest_with_fixture_spec(
        {
            "TILT_GT": {
                "u_gt_bound": 0.0,
                "u_gt_bound_formula": "U_GT = 0 (analytic)",
                "u_gt_bound_unit": "db_per_oct",
            }
        }
    )
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    sweep_ids = {v.sweep_id for v in violations}
    assert sweep_ids == {
        "u_num_bound",
        "u_num_bound_formula",
        "u_num_bound_unit",
        "u_bound_inputs",
    }
    assert all(v.family == "TILT_GT" for v in violations)


def test_u_gt_u_num_bounds_legacy_manifest_missing_bounds_still_not_blocked() -> None:
    """marker が無い legacy (v1.0) manifest は、両フィールド欠落でも従来
    どおり violation を出さない（後方互換の維持を明示的に固定する）。"""
    manifest = _manifest_with_fixture_spec({"TILT_GT": {}})
    assert not c0_validate._is_v1_1_manifest(manifest)
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert violations == ()


def _complete_v1_1_fixture_spec() -> dict[str, object]:
    """v1.1 の全 7 family に対して `u_gt_bound`/`u_num_bound` (+formula/unit/
    `u_bound_inputs`) を完備するエントリを生成する。

    R22-2 対応（Codex 第 22 巡 finding (2)、2026-09-05）: 旧実装は family
    間で使い回した固定 placeholder（`u_gt_bound=0.0`/`u_num_bound=0.024`/
    `"U_GT = 0 (analytic)"` 等）を返していたが、`_check_u_gt_u_num_bounds()`
    に canonical 再導出一致検査（`u_bound_inputs` からの再導出 == 宣言値）が
    追加されたことで、real family ごとの canonical 値でなければ通らなくなった
    （producer `c0_freeze._fixture_specs()` と同一の
    `fixtures.uncertainty.gather_u_bound_inputs()`/`derive_u_gt_bound()`/
    `derive_u_num_bound()` から実導出する）。
    """
    fixture_spec: dict[str, object] = {}
    for family in fixture_axes.FixtureFamily:
        inputs = fixture_uncertainty.gather_u_bound_inputs(family)
        gt_value, gt_formula = fixture_uncertainty.derive_u_gt_bound(family, inputs)
        num_value, num_formula = fixture_uncertainty.derive_u_num_bound(family, inputs)
        unit = (
            "n/a"
            if family.value in fixture_uncertainty.U_ABSENT_REASON_BY_FAMILY
            else fixture_axes.TRUTH_UNIT_BY_FAMILY[family.value]
        )
        fixture_spec[family.value] = {
            "u_gt_bound": gt_value,
            "u_gt_bound_formula": gt_formula,
            "u_gt_bound_unit": unit,
            "u_num_bound": num_value,
            "u_num_bound_formula": num_formula,
            "u_num_bound_unit": unit,
            "u_bound_inputs": inputs,
        }
    return fixture_spec


def test_u_gt_u_num_bounds_v1_1_manifest_complete_passes() -> None:
    """完全な v1.1 manifest（全 family、両 bound + formula + unit 具備）は
    violation を出さない。"""
    manifest = _v1_1_manifest_with_fixture_spec(_complete_v1_1_fixture_spec())
    assert c0_validate._is_v1_1_manifest(manifest)
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert violations == ()


# ---------------------------------------------------------------------------
# _check_u_gt_u_num_bounds -- R21 (Codex 第 21 巡採用): v1.1 manifest では
# u_gt_bound_unit/u_num_bound_unit も fixtures.axes.TRUTH_UNIT_BY_FAMILY と
# 照合し、欠落・改変を fail-closed にする（campaign/holdout_stage.
# units_commensurate_for_family() が §10.4 条件 (c) に直接消費するため）。
# ---------------------------------------------------------------------------


def test_u_gt_u_num_bounds_v1_1_manifest_missing_unit_blocks() -> None:
    """(e) 完全な v1.1 manifest から `u_gt_bound_unit` だけを消すと検出
    される（bound/formula 自体は無傷）。"""
    fixture_spec = _complete_v1_1_fixture_spec()
    del fixture_spec["TILT_GT"]["u_gt_bound_unit"]  # type: ignore[index]
    manifest = _v1_1_manifest_with_fixture_spec(fixture_spec)
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert len(violations) == 1
    assert violations[0].family == "TILT_GT"
    assert violations[0].sweep_id == "u_gt_bound_unit"
    assert violations[0].violation == "u_bound_missing_or_invalid"

    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert result.u_gt_u_num_bound_violations == violations


def test_u_gt_u_num_bounds_v1_1_manifest_altered_unit_blocks() -> None:
    """(f) 完全な v1.1 manifest の `u_num_bound_unit` を別 family/候補の単位
    （`hz` -> `db_per_oct`)へ改竄すると検出される（forged unit が候補宣言
    unit と正規化後に一致してしまう `units_commensurate_for_family()` の
    偽陽性経路を塞ぐ本 finding の再現）。"""
    fixture_spec = _complete_v1_1_fixture_spec()
    fixture_spec["F0_CONTROL"]["u_num_bound_unit"] = "db_per_oct"  # type: ignore[index]
    manifest = _v1_1_manifest_with_fixture_spec(fixture_spec)
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert len(violations) == 1
    assert violations[0].family == "F0_CONTROL"
    assert violations[0].sweep_id == "u_num_bound_unit"


def test_u_gt_u_num_bounds_v1_1_manifest_absent_family_wrong_unit_blocks() -> None:
    """ABSENT-only family の unit は `"n/a"` 固定を要求する——数値相当の unit
    へ改竄したら検出される。"""
    fixture_spec = _complete_v1_1_fixture_spec()
    fixture_spec["RESONANCE_GT"]["u_gt_bound_unit"] = "hz"  # type: ignore[index]
    manifest = _v1_1_manifest_with_fixture_spec(fixture_spec)
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert len(violations) == 1
    assert violations[0].family == "RESONANCE_GT"
    assert violations[0].sweep_id == "u_gt_bound_unit"


def test_u_gt_u_num_bounds_legacy_manifest_missing_unit_not_blocked() -> None:
    """legacy (v1.0, marker 無し) manifest は unit フィールドの欠落も検査
    対象外のまま（R20-3 のキー欠落と同じ後方互換規約）。"""
    manifest = _manifest_with_fixture_spec(
        {
            "TILT_GT": {
                "u_gt_bound": 0.0,
                "u_gt_bound_formula": "U_GT = 0 (analytic)",
                "u_num_bound": 0.024,
                "u_num_bound_formula": "U_num = derive_floor(...)",
            }
        }
    )
    assert not c0_validate._is_v1_1_manifest(manifest)
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert violations == ()


# ---------------------------------------------------------------------------
# _check_u_gt_u_num_bounds -- R22-2 (Codex 第 22 巡 finding (2)): v1.1 manifest
# では u_gt_bound/u_num_bound を producer と同一の canonical 関数
# (`fixtures.uncertainty`) で `u_bound_inputs` から再導出し、宣言値と厳密
# 一致することを要求する。
# ---------------------------------------------------------------------------


def test_u_gt_u_num_bounds_v1_1_manifest_zeroed_bound_with_fake_formula_blocks() -> None:
    """finding (2) の再現ケース (a): 完全な v1.1 manifest の `u_num_bound` を
    0.0 に、formula を無関係な文字列に差し替えても、`u_bound_inputs` からの
    canonical 再導出と一致しないため検出される（旧実装は形状検査のみで通して
    いた）。"""
    fixture_spec = _complete_v1_1_fixture_spec()
    fixture_spec["FORMANT_GT"]["u_num_bound"] = 0.0  # type: ignore[index]
    fixture_spec["FORMANT_GT"]["u_num_bound_formula"] = "forged: no real derivation"  # type: ignore[index]
    manifest = _v1_1_manifest_with_fixture_spec(fixture_spec)
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    sweep_ids = {v.sweep_id for v in violations}
    assert sweep_ids == {"u_num_bound", "u_num_bound_formula"}
    assert all(v.family == "FORMANT_GT" for v in violations)
    assert all(v.violation == "u_bound_missing_or_invalid" for v in violations)


def test_u_gt_u_num_bounds_v1_1_manifest_formula_only_altered_blocks() -> None:
    """finding (2) の再現ケース (b): 宣言値は canonical と一致するが formula
    文字列だけ改竄されたケースも独立に検出される（value/formula は別々に
    照合する）。"""
    fixture_spec = _complete_v1_1_fixture_spec()
    fixture_spec["TILT_GT"]["u_num_bound_formula"] = "not the real derivation"  # type: ignore[index]
    manifest = _v1_1_manifest_with_fixture_spec(fixture_spec)
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert len(violations) == 1
    assert violations[0].family == "TILT_GT"
    assert violations[0].sweep_id == "u_num_bound_formula"


def test_u_gt_u_num_bounds_v1_1_manifest_self_consistent_forged_inputs_blocks() -> None:
    """R24-1 対応（Codex 第 24 巡 P1 採用, 2026-09-05, PRRT_kwDOSD2OOM6fgdGg）:
    `u_bound_inputs.truth_scale_max`/`.float64_eps` を弱い値 (`0.0`) に
    差し替えた上で、対応する `u_num_bound`/formula もその**偽入力から
    正しく再計算**した「入力ごと自己整合な」manifest——R22-2 の再導出照合
    （入力→出力の内部整合性のみ）は素通りしてしまう偽装形——を、live
    canonical 再導出 (`fixtures.uncertainty.gather_u_bound_inputs()`) との
    不一致で検出する。R22-2 の value/formula 再導出自体は（偽入力からの
    計算として）一致するため violation を出さない——本テストは R24-1 の
    追加チェックだけが単独でこの偽装を捕捉することを確認する。"""
    fixture_spec = _complete_v1_1_fixture_spec()
    forged_inputs = dict(fixture_spec["FORMANT_GT"]["u_bound_inputs"])  # type: ignore[index]
    forged_inputs["truth_scale_max"] = 0.0
    forged_inputs["float64_eps"] = 0.0
    forged_gt_value, forged_gt_formula = fixture_uncertainty.derive_u_gt_bound(
        fixture_axes.FixtureFamily.FORMANT_GT, forged_inputs
    )
    forged_num_value, forged_num_formula = fixture_uncertainty.derive_u_num_bound(
        fixture_axes.FixtureFamily.FORMANT_GT, forged_inputs
    )
    fixture_spec["FORMANT_GT"]["u_bound_inputs"] = forged_inputs  # type: ignore[index]
    fixture_spec["FORMANT_GT"]["u_gt_bound"] = forged_gt_value  # type: ignore[index]
    fixture_spec["FORMANT_GT"]["u_gt_bound_formula"] = forged_gt_formula  # type: ignore[index]
    fixture_spec["FORMANT_GT"]["u_num_bound"] = forged_num_value  # type: ignore[index]
    fixture_spec["FORMANT_GT"]["u_num_bound_formula"] = forged_num_formula  # type: ignore[index]
    manifest = _v1_1_manifest_with_fixture_spec(fixture_spec)

    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert len(violations) == 1, violations
    assert violations[0].family == "FORMANT_GT"
    assert violations[0].sweep_id == "u_bound_inputs"
    assert violations[0].violation == "u_bound_missing_or_invalid"
    assert "does not match the canonical live re-derivation" in violations[0].detail


def test_u_gt_u_num_bounds_v1_1_manifest_input_key_deleted_blocks() -> None:
    """finding (2) の再現ケース (c): `u_bound_inputs` から再導出に必要な
    1 キーを消すと、canonical 関数呼び出しが `KeyError` を送出し、それを
    validator が fail-closed の violation に変換することを確認する
    （APERIODICITY_GT は `sr_min_hz`/`duration_min_s`/
    `aperiodicity_fraction_max` を要求する）。

    R24-1 追補（Codex 第 24 巡 P1 採用、2026-09-05）: 同じ改竄はキー集合が
    live canonical (`fixtures.uncertainty.gather_u_bound_inputs()`) とも
    食い違うため、R24-1 の live 再導出一致検査（キー集合不一致）も独立に
    fail-closed する——両方の violation が同時に出て良い（片方が拾い漏らす
    改竄をもう片方が拾う、独立した検査軸であることの確認でもある）。"""
    fixture_spec = _complete_v1_1_fixture_spec()
    del fixture_spec["APERIODICITY_GT"]["u_bound_inputs"]["sr_min_hz"]  # type: ignore[index]
    manifest = _v1_1_manifest_with_fixture_spec(fixture_spec)
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert len(violations) == 2
    assert all(v.family == "APERIODICITY_GT" for v in violations)
    assert all(v.violation == "u_bound_missing_or_invalid" for v in violations)
    assert {v.sweep_id for v in violations} == {"u_bound_inputs"}
    assert any("does not match the canonical live re-derivation" in v.detail for v in violations)
    assert any("could not be used to canonically recompute" in v.detail for v in violations)


def test_u_gt_u_num_bounds_real_c0_freeze_manifest_still_passes_after_r22_2() -> None:
    """finding (2) の再現ケース (d): `c0_freeze.build_manifest()` の実出力は
    R22-2 の canonical 再導出一致検査を通過する（producer/validator が
    同一関数・同一入力から同一結果を再現できることの回帰ガード。
    `test_u_gt_u_num_bounds_real_c0_freeze_manifest_passes` と同趣旨だが、
    R22-2 追加後であることを明示するため独立して固定する）。"""
    from voice_genesis.calibration import c0_freeze

    manifest = c0_freeze.build_manifest(
        c0_freeze._REPO_ROOT, approvals={}, campaign_date_utc="2026-09-05"
    )
    for family in fixture_axes.FixtureFamily:
        entry = manifest["frozen_design"]["fixture_spec"][family.value]
        if family.value not in fixture_uncertainty.U_ABSENT_REASON_BY_FAMILY:
            assert isinstance(entry["u_bound_inputs"], dict)
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert violations == ()


def test_u_gt_u_num_bounds_v1_1_manifest_missing_u_bound_inputs_blocks() -> None:
    """v1.1 manifest で `u_bound_inputs` キー自体が丸ごと無い場合も個別の
    violation として検出される（bound/formula/unit 自体は完備でも、R22-2 の
    canonical 再導出照合が成立しないため）。"""
    fixture_spec = _complete_v1_1_fixture_spec()
    del fixture_spec["F0_CONTROL"]["u_bound_inputs"]  # type: ignore[index]
    manifest = _v1_1_manifest_with_fixture_spec(fixture_spec)
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert len(violations) == 1
    assert violations[0].family == "F0_CONTROL"
    assert violations[0].sweep_id == "u_bound_inputs"


# ---------------------------------------------------------------------------
# _check_required_blocking / validate_c0_manifest -- R22-1 (Codex 第 22 巡
# finding (1)): frozen_design.design_revision の必須化・legacy opt-in ゲート。
# ---------------------------------------------------------------------------


def test_design_revision_missing_is_required_blocking() -> None:
    manifest = _v1_1_manifest_with_fixture_spec({})
    del manifest["frozen_design"]["design_revision"]  # type: ignore[union-attr]
    missing = c0_validate._check_required_blocking(manifest)
    assert "frozen_design.design_revision" in missing


def test_design_revision_v1_0_value_is_closed_vocabulary_violation() -> None:
    manifest = {"frozen_design": {"design_revision": "1.0"}}
    missing = c0_validate._check_required_blocking(manifest)
    assert any(
        k.startswith("frozen_design.design_revision: closed vocabulary") for k in missing
    )


def test_design_revision_legacy_opt_in_suppresses_violation() -> None:
    manifest = {"frozen_design": {}}
    missing_default = c0_validate._check_required_blocking(manifest)
    assert "frozen_design.design_revision" in missing_default
    missing_opt_in = c0_validate._check_required_blocking(
        manifest, legacy_design_revision_ok=True
    )
    assert "frozen_design.design_revision" not in missing_opt_in


def test_legacy_v1_0_opt_in_not_verified_without_manifest_path() -> None:
    """`manifest_path=None`（in-memory manifest、`c0_freeze.dry_run()`/
    `armed_freeze()` の呼び出し方）では `allow_legacy_v1_0=True` を渡しても
    opt-in が有効化されない（fail-closed）。"""
    assert c0_validate._legacy_v1_0_opt_in_verified({}, None) is False


def _write_chain_valid_ledger(ledger_path: Path, payloads: list[dict[str, object]]) -> None:
    """`payloads` を順に `provenance.Ledger.append()` で書き込み、
    seq/prev_sha/entry_sha が正しく連鎖した実物の chain-valid ledger を
    `ledger_path` に作る（R24-2 のテストが「本物の chain 検証」を要求する
    ようになったため、手書きの1行 dict では通らない——`Ledger` 自身の
    生成器を使う）。"""
    ledger = Ledger(ledger_path)
    for payload in payloads:
        ledger.append(payload)


def _write_manifest_and_freeze_identity(
    campaign_dir: Path, manifest: dict[str, object]
) -> tuple[Path, str, str]:
    """`manifest` を `c0_freeze.py` と同じ規約
    (`canonical_json(full_manifest)`) で `c0_manifest.json` に書き出し、
    その `manifest_sha`/`manifest_core_sha` を返す（R25-1 のテストが
    `_legacy_v1_0_opt_in_verified()` の (a)/(b) 束縛を満たす genesis event
    を組み立てるための共通ヘルパー）。"""
    from voice_genesis.calibration import c0_freeze

    manifest_path = campaign_dir / "c0_manifest.json"
    manifest_path.write_text(canonical_json(manifest), encoding="utf-8")
    manifest_sha = canonical_manifest_sha(manifest)
    manifest_core_sha = c0_freeze.manifest_core_sha(manifest)
    return manifest_path, manifest_sha, manifest_core_sha


def test_legacy_v1_0_opt_in_verified_for_gz_archived_campaign(tmp_path: Path) -> None:
    """R24-2 対応（Codex 第 24 巡 P2 採用, 2026-09-05）: aborted 判定は
    `archive_aborted_ledger._verify_gz_sidecar_pair()` を共有するため、
    fabricated な `ledger.jsonl.gz`（本物の gzip ですらない）ではもう
    True にならない——本物の `ensure_archived()` 出力（sidecar sha256 一致・
    実伸長・chain 検証済み）でのみ opt-in できることを確認する。

    R25-1 対応: ledger の genesis event が manifest 自身の
    manifest_sha/manifest_core_sha を記録している場合に限り True になる。"""
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    manifest = {"frozen_design": {}}
    manifest_path, manifest_sha, manifest_core_sha = _write_manifest_and_freeze_identity(
        campaign_dir, manifest
    )
    ledger_path = campaign_dir / "ledger.jsonl"
    _write_chain_valid_ledger(
        ledger_path,
        [
            {
                "kind": "c0_freeze",
                "manifest_sha": manifest_sha,
                "manifest_core_sha": manifest_core_sha,
            },
            {"kind": "holdout_executed_valid"},
        ],
    )
    result = archive_aborted_ledger.ensure_archived(campaign_dir)
    assert result.action == "archived"
    assert not ledger_path.is_file()  # ensure_archived removes the original

    assert c0_validate._legacy_v1_0_opt_in_verified(manifest, manifest_path) is True


def test_legacy_v1_0_opt_in_reads_gz_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R26 対応（Codex 第 26 巡 P2 採用, "Reuse the verified gzip snapshot for
    identity checking"）: aborted 経路は `ledger.jsonl.gz` を **1 回だけ**
    バイト列として読み、sidecar/chain 検証と freeze identity 照合の両方を
    その同一バイト列から行う——という単一読取契約を、`Path.read_bytes()` の
    呼び出し回数そのものを数えて固定する。修正前は `_verify_gz_sidecar_pair()`
    内部の読取（1 回目）と、この関数自身が freeze identity 照合のために
    行っていた読取（2 回目）の 2 回読んでおり、その間に on-disk の gz が
    差し替わると sidecar 未検証のペアで opt-in が通り得た（TOCTOU）。"""
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    manifest = {"frozen_design": {}}
    manifest_path, manifest_sha, manifest_core_sha = _write_manifest_and_freeze_identity(
        campaign_dir, manifest
    )
    ledger_path = campaign_dir / "ledger.jsonl"
    _write_chain_valid_ledger(
        ledger_path,
        [
            {
                "kind": "c0_freeze",
                "manifest_sha": manifest_sha,
                "manifest_core_sha": manifest_core_sha,
            },
            {"kind": "holdout_executed_valid"},
        ],
    )
    result = archive_aborted_ledger.ensure_archived(campaign_dir)
    assert result.action == "archived"

    gz_path = campaign_dir / archive_aborted_ledger.GZ_FILENAME
    real_read_bytes = Path.read_bytes
    gz_read_count = {"n": 0}

    def _counting_read_bytes(self: Path) -> bytes:
        if self == gz_path:
            gz_read_count["n"] += 1
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _counting_read_bytes)

    assert c0_validate._legacy_v1_0_opt_in_verified(manifest, manifest_path) is True
    assert gz_read_count["n"] == 1


def test_legacy_v1_0_opt_in_not_verified_for_fabricated_gz(tmp_path: Path) -> None:
    """R24-2 (a): 本 fix 前の穴の直接再現——`ledger.jsonl.gz` という名前の
    ただの通常ファイル（gzip ですらない）が存在するだけでは opt-in できない
    ことを固定する。"""
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    manifest: dict[str, object] = {}
    manifest_path, _, _ = _write_manifest_and_freeze_identity(campaign_dir, manifest)
    (campaign_dir / "ledger.jsonl.gz").write_bytes(b"\x1f\x8b\x00")
    assert c0_validate._legacy_v1_0_opt_in_verified(manifest, manifest_path) is False


def test_legacy_v1_0_opt_in_not_verified_for_gz_sidecar_mismatch(tmp_path: Path) -> None:
    """R24-2 (b): 本物の archive を作った後、sidecar のみを別 sha256 に
    差し替える（gz は無傷）——ペア検証が sidecar 不一致で fail するため
    opt-in できない。"""
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    manifest: dict[str, object] = {}
    manifest_path, manifest_sha, manifest_core_sha = _write_manifest_and_freeze_identity(
        campaign_dir, manifest
    )
    ledger_path = campaign_dir / "ledger.jsonl"
    _write_chain_valid_ledger(
        ledger_path,
        [{"kind": "c0_freeze", "manifest_sha": manifest_sha, "manifest_core_sha": manifest_core_sha}],
    )
    archive_aborted_ledger.ensure_archived(campaign_dir)

    sidecar_path = campaign_dir / archive_aborted_ledger.SIDECAR_FILENAME
    sidecar_path.write_text("0" * 64 + "  ledger.jsonl\n", encoding="utf-8")

    assert c0_validate._legacy_v1_0_opt_in_verified(manifest, manifest_path) is False


def test_legacy_v1_0_opt_in_verified_for_campaign_closed_ledger(tmp_path: Path) -> None:
    """R24-2 対応: closed 判定は `provenance.Ledger.load_with_verification()`
    の chain 検証 (`chain.ok`) を通り、かつ**末尾** entry が
    `campaign_closed` である本物の chain-valid ledger でのみ True になる。
    R25-1 対応: genesis event の freeze identity が manifest と一致する
    ことも要求される。"""
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    manifest = {"frozen_design": {}}
    manifest_path, manifest_sha, manifest_core_sha = _write_manifest_and_freeze_identity(
        campaign_dir, manifest
    )
    ledger_path = campaign_dir / "ledger.jsonl"
    _write_chain_valid_ledger(
        ledger_path,
        [
            {
                "kind": "c0_freeze",
                "manifest_sha": manifest_sha,
                "manifest_core_sha": manifest_core_sha,
            },
            {"kind": "campaign_closed"},
        ],
    )
    assert c0_validate._legacy_v1_0_opt_in_verified(manifest, manifest_path) is True


def test_legacy_v1_0_opt_in_not_verified_for_raw_unchained_campaign_closed_line(
    tmp_path: Path,
) -> None:
    """R24-2 (b): 本 fix 前の穴の直接再現——生の 1 行 JSON
    (`{"payload": {"kind": "campaign_closed"}}`、`seq`/`prev_sha`/
    `entry_sha` を欠く chain 未形成の行) は、旧実装（行スキャンのみ）では
    True になっていたが、chain 検証が必須になった今は False になる。"""
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    manifest: dict[str, object] = {}
    manifest_path, _, _ = _write_manifest_and_freeze_identity(campaign_dir, manifest)
    (campaign_dir / "ledger.jsonl").write_text(
        '{"payload": {"kind": "campaign_closed"}}\n', encoding="utf-8"
    )
    assert c0_validate._legacy_v1_0_opt_in_verified(manifest, manifest_path) is False


def test_legacy_v1_0_opt_in_not_verified_for_campaign_closed_not_at_tail(tmp_path: Path) -> None:
    """R24-2: chain 自体は有効でも、`campaign_closed` が**末尾ではない**
    （その後に別 event が続く）場合は opt-in できない——正規の閉鎖手順は
    `campaign_closed` を必ず最後に置く（`campaign/close_stage.py` 参照）。"""
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    manifest: dict[str, object] = {}
    manifest_path, manifest_sha, manifest_core_sha = _write_manifest_and_freeze_identity(
        campaign_dir, manifest
    )
    ledger_path = campaign_dir / "ledger.jsonl"
    _write_chain_valid_ledger(
        ledger_path,
        [
            {
                "kind": "c0_freeze",
                "manifest_sha": manifest_sha,
                "manifest_core_sha": manifest_core_sha,
            },
            {"kind": "campaign_closed"},
            {"kind": "meter_call"},
        ],
    )
    assert c0_validate._legacy_v1_0_opt_in_verified(manifest, manifest_path) is False


def test_legacy_v1_0_opt_in_not_verified_for_open_campaign(tmp_path: Path) -> None:
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    manifest: dict[str, object] = {}
    manifest_path, _, _ = _write_manifest_and_freeze_identity(campaign_dir, manifest)
    (campaign_dir / "ledger.jsonl").write_text(
        '{"payload": {"kind": "meter_call"}}\n', encoding="utf-8"
    )
    assert c0_validate._legacy_v1_0_opt_in_verified(manifest, manifest_path) is False


def test_legacy_v1_0_opt_in_not_verified_for_manifest_path_byte_mismatch(tmp_path: Path) -> None:
    """R25-1 (a): `manifest_path` の on-disk バイト列が in-memory `manifest`
    の正規化 JSON と食い違う場合は、ledger 側が chain-valid/closed であっても
    opt-in が成立しない（「in-memory manifest が path の内容と異なる」ケース
    の fail-closed）。"""
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    on_disk_manifest = {"frozen_design": {}, "campaign_meta": {"note": "on-disk"}}
    manifest_path, manifest_sha, manifest_core_sha = _write_manifest_and_freeze_identity(
        campaign_dir, on_disk_manifest
    )
    ledger_path = campaign_dir / "ledger.jsonl"
    _write_chain_valid_ledger(
        ledger_path,
        [
            {
                "kind": "c0_freeze",
                "manifest_sha": manifest_sha,
                "manifest_core_sha": manifest_core_sha,
            },
            {"kind": "campaign_closed"},
        ],
    )

    # 呼び出し元が実際に検証しようとしている in-memory manifest が、path の
    # 内容と異なる（他 campaign の manifest を誤って渡した/path が後から
    # 書き換えられた、を模す）。
    different_in_memory_manifest = {"frozen_design": {}, "campaign_meta": {"note": "in-memory"}}
    assert (
        c0_validate._legacy_v1_0_opt_in_verified(different_in_memory_manifest, manifest_path)
        is False
    )


def test_legacy_v1_0_opt_in_not_verified_when_reusing_another_campaigns_archive(
    tmp_path: Path,
) -> None:
    """R25-1 (b) の直接再現——finding が指摘した攻撃経路: 他 campaign
    (`donor`) の正規 closed archive ledger を、revision marker の無い別
    manifest (`victim`) の隣にコピーするだけでは opt-in が成立しない
    （ledger 自体は chain-valid/closed でも、genesis event の freeze
    identity が victim の manifest と一致しないため）。"""
    donor_dir = tmp_path / "donor"
    donor_dir.mkdir()
    donor_manifest = {"frozen_design": {}, "campaign_meta": {"campaign_date_utc": "2026-01-01"}}
    _, donor_sha, donor_core_sha = _write_manifest_and_freeze_identity(donor_dir, donor_manifest)
    donor_ledger_path = donor_dir / "ledger.jsonl"
    _write_chain_valid_ledger(
        donor_ledger_path,
        [
            {"kind": "c0_freeze", "manifest_sha": donor_sha, "manifest_core_sha": donor_core_sha},
            {"kind": "campaign_closed"},
        ],
    )

    victim_dir = tmp_path / "victim"
    victim_dir.mkdir()
    victim_manifest = {"frozen_design": {}, "campaign_meta": {"campaign_date_utc": "2026-02-02"}}
    victim_manifest_path, _, _ = _write_manifest_and_freeze_identity(victim_dir, victim_manifest)
    # donor の正規 closed ledger をそのまま victim の隣へ流用する。
    (victim_dir / "ledger.jsonl").write_bytes(donor_ledger_path.read_bytes())

    assert (
        c0_validate._legacy_v1_0_opt_in_verified(victim_manifest, victim_manifest_path) is False
    )


def test_validate_c0_manifest_allow_legacy_v1_0_end_to_end(tmp_path: Path) -> None:
    """`validate_c0_manifest(allow_legacy_v1_0=True, manifest_path=...)` が
    closed campaign の on-disk manifest に限って design_revision 欠落を
    suppress することを end-to-end で確認する。"""
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    manifest = {"frozen_design": {}}
    manifest_path, manifest_sha, manifest_core_sha = _write_manifest_and_freeze_identity(
        campaign_dir, manifest
    )
    ledger_path = campaign_dir / "ledger.jsonl"
    _write_chain_valid_ledger(
        ledger_path,
        [
            {
                "kind": "c0_freeze",
                "manifest_sha": manifest_sha,
                "manifest_core_sha": manifest_core_sha,
            },
            {"kind": "campaign_closed"},
        ],
    )

    without_flag = c0_validate.validate_c0_manifest(manifest)
    assert "frozen_design.design_revision" in without_flag.missing_required_keys

    with_flag = c0_validate.validate_c0_manifest(
        manifest, allow_legacy_v1_0=True, manifest_path=manifest_path
    )
    assert "frozen_design.design_revision" not in with_flag.missing_required_keys

    # manifest_path を渡さない（in-memory 新規 freeze 経路を模す）と opt-in
    # フラグを立てても効かない（fail-closed）。
    without_path = c0_validate.validate_c0_manifest(manifest, allow_legacy_v1_0=True)
    assert "frozen_design.design_revision" in without_path.missing_required_keys


# ---------------------------------------------------------------------------
# _check_holdout_pin_feasibility
# ---------------------------------------------------------------------------


def test_holdout_pin_feasibility_empty_for_canonical_matrix() -> None:
    """456 セル canonical matrix は常に feasible（§V2.2 実測値表）。"""
    violations = c0_validate._check_holdout_pin_feasibility({})
    assert violations == ()


# ---------------------------------------------------------------------------
# _check_holdout_sweeps_declaration_match
# ---------------------------------------------------------------------------


def _holdout_sweeps_manifest(pinned: dict[str, dict[str, tuple[str, ...]]]) -> dict[str, object]:
    return {
        "holdout_sweeps": {
            family: {sid: list(rids) for sid, rids in sweeps.items()}
            for family, sweeps in pinned.items()
        }
    }


def test_holdout_sweeps_absent_is_not_a_violation() -> None:
    violations = c0_validate._check_holdout_sweeps_declaration_match({}, None)
    assert violations == ()
    violations_with_secret = c0_validate._check_holdout_sweeps_declaration_match({}, _SECRET)
    assert violations_with_secret == ()


def test_holdout_sweeps_correct_pin_passes_with_and_without_secret() -> None:
    """v1.1 §V3.5 実装時発見（2026-09-05）: `_PINNED`（`splitter.
    pin_and_realize_holdout()` の実結果、`_SECRET` で決定論的に再現）は
    `TILT_GT` が `nuisance_axis` coverage 制約により nominal k_hold=2 から
    k_hold=1 へ縮退している。secret 非依存の構造検査（`_check_holdout_
    sweeps_declaration_match(manifest, None)`）はその設計上の限界どおり
    （関数 docstring 「secret 無しではその縮退が正当だったかを検査できず、
    単に len(actual) != k_hold として構造 mismatch になる」）`TILT_GT` を
    構造 mismatch として検出する——これは検証器のバグではなく、正当な
    縮退を secret 無しでは確認しようがないという既知の制約そのものである。
    secret 依存の完全再導出照合（本来の正当性確認経路）は全 family で
    一致する。"""
    manifest = _holdout_sweeps_manifest(_PINNED)
    # secret 非依存の構造検査: 縮退した TILT_GT のみ「nominal k_hold との
    # 単純比較」で構造 mismatch として検出される（設計上の既知の制約）。
    without_secret_violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, None)
    assert {v.family for v in without_secret_violations} == {"TILT_GT"}
    assert all(v.violation == "holdout_pin_declaration_mismatch" for v in without_secret_violations)
    # secret 依存の完全再導出照合（縮退の正当性まで検証できる本来の経路）
    # は全 family で一致する。
    assert c0_validate._check_holdout_sweeps_declaration_match(manifest, _SECRET) == ()

    result = c0_validate.validate_c0_manifest(manifest, split_secret=_SECRET)
    assert result.holdout_pin_declaration_violations == ()


def test_holdout_sweeps_wrong_secret_detects_mismatch() -> None:
    """宣言は secret A での正しい pin だが、実際の split_secret は別の値
    （B）——完全再導出照合が secret 依存で不一致を検出する。"""
    manifest = _holdout_sweeps_manifest(_PINNED)
    other_secret = b"\x99" * 32
    other_pinned = pin_holdout_sweeps_by_family(_ROWS, other_secret)
    assert other_pinned != _PINNED  # サニティ: 異なる secret は異なる選抜を返す

    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, other_secret)
    assert violations, "expected a mismatch when re-derived under a different split_secret"
    assert all(v.violation == "holdout_pin_declaration_mismatch" for v in violations)


def test_holdout_sweeps_wrong_member_row_ids_detected_without_secret() -> None:
    """secret を持たない構造検査でも、declared_sweeps に存在しない sweep_id
    /member 行の宣言は検出できる。"""
    tampered = {
        family: dict(sweeps) for family, sweeps in _PINNED.items()
    }
    tampered["FORMANT_GT"] = dict(tampered["FORMANT_GT"])
    bogus_sweep_id = next(iter(tampered["FORMANT_GT"]))
    tampered["FORMANT_GT"][bogus_sweep_id] = ("not-a-real-row-id",)
    manifest = _holdout_sweeps_manifest(tampered)

    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, None)
    assert violations
    assert any(v.family == "FORMANT_GT" and v.sweep_id == bogus_sweep_id for v in violations)


def test_holdout_sweeps_wrong_pin_count_detected_without_secret() -> None:
    """k_hold と異なる pin 数（宣言 sweep 数）の宣言は secret 非依存でも
    検出できる。"""
    tampered = {family: dict(sweeps) for family, sweeps in _PINNED.items()}
    # IDENTITY_CAUSAL_SWEEP の k_hold=4 のうち 1 個を落として 3 個にする。
    ident = tampered["IDENTITY_CAUSAL_SWEEP"]
    dropped_sweep_id = next(iter(ident))
    tampered["IDENTITY_CAUSAL_SWEEP"] = {
        sid: members for sid, members in ident.items() if sid != dropped_sweep_id
    }
    manifest = _holdout_sweeps_manifest(tampered)

    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, None)
    assert any(
        v.family == "IDENTITY_CAUSAL_SWEEP" and v.expected_count == 4 and v.actual_count == 3
        for v in violations
    )


# ---------------------------------------------------------------------------
# _check_holdout_sweeps_realized_membership
# ---------------------------------------------------------------------------


def test_holdout_membership_absent_realized_split_is_not_a_violation() -> None:
    manifest = _holdout_sweeps_manifest(_PINNED)
    assert c0_validate._check_holdout_sweeps_realized_membership(manifest) == ()


def test_holdout_membership_passes_when_all_pinned_rows_are_holdout() -> None:
    assignment = {}
    for family, sweeps in _PINNED.items():
        for member_row_ids in sweeps.values():
            for rid in member_row_ids:
                assignment[rid] = Split.HOLDOUT.value
    manifest = _holdout_sweeps_manifest(_PINNED)
    manifest["realized_split"] = {"assignment": assignment}

    violations = c0_validate._check_holdout_sweeps_realized_membership(manifest)
    assert violations == ()


# ---------------------------------------------------------------------------
# R10 対応（PR #346 第 10 巡 P1 採用、2026-09-05）: 縮退カウントの検証側
# 完全再導出。修正前は「宣言された pin 数が degradation_floor<=count<k_hold
# の範囲に収まっていれば、その宣言値をそのまま override として段 1 のみ
# 再導出照合する」ため、独立に構築/改竄した manifest が縮退を自称するだけで
# holdout sweep 宣言を水減らしできた。修正後は宣言値を一切入力にせず、公称
# k_hold から始まる段 1+段 2 の縮退ループ（`splitter.pin_and_realize_
# holdout()` — `armed_freeze()` と同一関数）を検証側で完全再実行する。
# ---------------------------------------------------------------------------


def test_holdout_sweeps_fabricated_degradation_within_allowed_range_is_rejected() -> None:
    """(a) 公称 k_hold で段 2 が実際には成功する（縮退不要）canonical matrix
    に対し、`degradation_floor <= 宣言数 < k_hold` の許容範囲に収まる
    「もっともらしい」水減らし宣言は fail-closed で検出される。

    FORMANT_GT は k_hold=3・degradation_floor=2（max_field_cardinality=2 >
    1 の claim 被覆 family）なので、宣言 pin 数=2 はちょうどこの範囲に
    収まる——修正前はこの一点だけで宣言値をそのまま `k_hold_overrides` に
    採用し、段 1 のみを k=2 で再導出照合していたため、宣言が「本物の 3
    sweep 選抜の先頭 2 件のみを自称した」偽物であっても一致してしまい得た。
    修正後は宣言を無視して公称 k_hold=3 から段 1+段 2 を完全再実行するため
    （canonical matrix では FORMANT_GT の段 2 は常に k=3 で成功し縮退は
    一切発生しない）、再導出結果は 3 sweep のまま——宣言の 2 sweep とは
    必ず食い違う。"""
    tampered = {family: dict(sweeps) for family, sweeps in _PINNED.items()}
    formant = tampered["FORMANT_GT"]
    assert len(formant) == 3
    dropped_sweep_id = sorted(formant)[-1]
    tampered["FORMANT_GT"] = {
        sid: members for sid, members in formant.items() if sid != dropped_sweep_id
    }
    assert len(tampered["FORMANT_GT"]) == 2  # degradation_floor(2) <= 2 < k_hold(3)

    manifest = _holdout_sweeps_manifest(tampered)
    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, _SECRET)
    assert violations, "fabricated degradation must be rejected even within the allowed range"
    assert all(v.violation == "holdout_pin_declaration_mismatch" for v in violations)
    assert any(v.family == "FORMANT_GT" for v in violations)


def test_holdout_sweeps_genuine_partial_degradation_passes_with_secret(monkeypatch) -> None:
    """(b) 公称 k_hold で段 2 が本当に `CoverageRepairInfeasible` に陥り、
    k=1 への縮退で初めて成功する合成 matrix（対照ケース）は pass する。

    TILT_GT（非 claim-coverage family、degradation_floor=0）の TRUTH_CORE
    全 30 行 + confound 1 行 + boundary 2 行だけを取り出した matrix:
    非 pin の movable 行が極端に少ないため、公称 k_hold=2（pin 行 10）は
    実際の HOLDOUT 枠を超過し修復不能——k=1（pin 行 5）まで縮退して初めて
    成功する（決定論的に 14 種のダミー secret 全てで再現済み）。宣言が
    この「本当に必要だった」縮退の帰結と厳密一致するため、公称 k_hold
    からの正規縮退ループ再実行と一致し pass する。"""
    from voice_genesis.calibration.splitter import (
        STRATUM_FACTOR_NAMES,
        pin_and_realize_holdout,
        row_inputs_for_split,
    )

    truth_core = [
        mr for mr in _ROWS if mr.row.family == "TILT_GT" and mr.row.block == "TRUTH_CORE"
    ]
    confound = [mr for mr in _ROWS if mr.row.family == "TILT_GT" and mr.row.block == "CONFOUND"]
    boundary = [mr for mr in _ROWS if mr.row.family == "TILT_GT" and mr.row.block == "BOUNDARY"]
    synth = tuple(truth_core) + tuple(confound[:1]) + tuple(boundary[:2])

    row_inputs = row_inputs_for_split(synth, STRATUM_FACTOR_NAMES)
    holdout_sweeps, _realized = pin_and_realize_holdout(
        synth, row_inputs, _SECRET, STRATUM_FACTOR_NAMES
    )
    assert len(holdout_sweeps["TILT_GT"]) == 1  # 公称 k_hold=2 は infeasible、k=1 へ縮退

    monkeypatch.setattr(c0_validate, "build_matrix", lambda: synth)
    manifest = _holdout_sweeps_manifest(holdout_sweeps)
    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, _SECRET)
    assert violations == ()


def test_holdout_sweeps_declaration_fails_closed_when_canonical_rederivation_raises(
    monkeypatch,
) -> None:
    """(c) 公称 k_hold からの正規縮退ループそのものが再実行できない
    （構造的異常・matrix 変更等）場合は、「検証不能」を「検証成功」として
    通さず、宣言のある全 family を無条件で fail-closed mismatch 扱いに
    する。"""
    from voice_genesis.calibration.fixtures.matrix import HoldoutPinDegradationExhausted

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise HoldoutPinDegradationExhausted("FORMANT_GT", floor=2, attempted_k=2)

    monkeypatch.setattr(c0_validate, "pin_and_realize_holdout", _boom)
    manifest = _holdout_sweeps_manifest(_PINNED)
    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, _SECRET)
    assert violations
    assert all(v.violation == "holdout_pin_declaration_mismatch" for v in violations)
    declared_families = {fam for fam, sweeps in _PINNED.items() if sweeps}
    assert {v.family for v in violations} == declared_families


# ---------------------------------------------------------------------------
# R11 対応（PR #346 第 11 巡採用, 2026-09-05）: `holdout_sweeps.<family>` が
# `{}`/欠落だと、修正前は段 1+段 2 の完全再導出比較に到達する前に
# `continue` して黙って skip していたため、非免除（`pin_exempt=False`、
# `k_hold>=1`）family の pin 宣言を manifest から丸ごと消す/空にするだけで
# secret 依存 C0 検証を通過できてしまう穴があった。修正後は
# `found_holdout`（v1.1+ manifest である version marker）かつ
# `split_secret is not None`（secret 依存の完全再導出経路）の場合に限り、
# 非免除 family の宣言欠落/空を fail-closed で検出し、逆に pin 免除
# family（`cap<1`）の非空宣言（免除を騙る改竄）も検出する。
# ---------------------------------------------------------------------------


def test_holdout_sweeps_missing_declaration_for_non_exempt_family_fails_closed() -> None:
    """(a) 非免除 family（FORMANT_GT, k_hold>=1）の宣言を manifest から
    丸ごと落とすと、完全再導出比較に到達する前に skip されず fail-closed
    で検出される。"""
    tampered = {fam: dict(sweeps) for fam, sweeps in _PINNED.items()}
    del tampered["FORMANT_GT"]
    manifest = _holdout_sweeps_manifest(tampered)

    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, _SECRET)
    assert violations
    assert any(
        v.family == "FORMANT_GT" and v.violation == "holdout_pin_declaration_mismatch"
        for v in violations
    )


def test_holdout_sweeps_empty_declaration_for_non_exempt_family_fails_closed() -> None:
    """(b) 非免除 family の宣言を `{}`（空 mapping）に置き換えても、missing
    と同様に fail-closed で検出される。"""
    tampered = {fam: dict(sweeps) for fam, sweeps in _PINNED.items()}
    tampered["FORMANT_GT"] = {}
    manifest = _holdout_sweeps_manifest(tampered)

    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, _SECRET)
    assert violations
    assert any(
        v.family == "FORMANT_GT" and v.violation == "holdout_pin_declaration_mismatch"
        for v in violations
    )


def _with_family_forced_exempt(monkeypatch, exempt_family: str) -> None:
    """`holdout_pin_params_by_family()` の戻り値のうち `exempt_family` だけ
    `pin_exempt=True`/`k_hold=0` に差し替えたものを `c0_validate` の名前空間
    に monkeypatch する（`full_pin` の実再計算 — `pin_and_realize_holdout()`
    — は独立に `fixtures.matrix` 側の実装を直接呼ぶため影響を受けない。
    テスト対象の新規チェックは exempt 分岐でいずれも実 pin 比較に進む前に
    `continue` するため、この不一致は問題にならない）。"""
    import dataclasses

    from voice_genesis.calibration.fixtures.matrix import (
        holdout_pin_params_by_family as real_holdout_pin_params_by_family,
    )

    def _fake(rows: object) -> dict[str, object]:
        result = dict(real_holdout_pin_params_by_family(rows))
        p = result[exempt_family]
        result[exempt_family] = dataclasses.replace(p, pin_exempt=True, k_hold=0)
        return result

    monkeypatch.setattr(c0_validate, "holdout_pin_params_by_family", _fake)


def test_holdout_sweeps_empty_declaration_for_exempt_family_passes(monkeypatch) -> None:
    """(c) pin 免除 family（`cap<1`）の空宣言 `{}` は正しい姿であり
    violation にならない。"""
    _with_family_forced_exempt(monkeypatch, "TILT_GT")
    tampered = {fam: dict(sweeps) for fam, sweeps in _PINNED.items()}
    tampered["TILT_GT"] = {}
    manifest = _holdout_sweeps_manifest(tampered)

    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, _SECRET)
    assert not any(v.family == "TILT_GT" for v in violations)


def test_holdout_sweeps_non_empty_declaration_for_exempt_family_fails_closed(monkeypatch) -> None:
    """(d) pin 免除 family が非空の pin を宣言している（免除を騙る改竄）
    場合は fail-closed で検出される。"""
    _with_family_forced_exempt(monkeypatch, "TILT_GT")
    manifest = _holdout_sweeps_manifest(_PINNED)  # TILT_GT は実際には非空宣言のまま

    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, _SECRET)
    assert any(
        v.family == "TILT_GT" and v.violation == "holdout_pin_declaration_mismatch"
        for v in violations
    )


def test_holdout_sweeps_missing_declaration_dry_run_backward_compatible() -> None:
    """(e) secret 非依存（dry-run）経路は後方互換を維持する: 非免除 family
    の宣言が欠落していても、`split_secret=None` では新規の必須化チェックは
    発動せず、従来どおり「宣言があれば照合」——欠落は無条件 skip
    （fail-closed 化は full manifest の secret 依存経路にのみ適用される）。"""
    tampered = {fam: dict(sweeps) for fam, sweeps in _PINNED.items()}
    del tampered["FORMANT_GT"]
    manifest = _holdout_sweeps_manifest(tampered)

    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, None)
    assert not any(v.family == "FORMANT_GT" for v in violations)


def test_holdout_membership_detects_pinned_row_not_in_holdout() -> None:
    """§V2.3 fail-closed: `holdout_sweeps` の member 行が 1 行でも realized
    split 上で HOLDOUT でなければ検出する。"""
    assignment = {}
    for family, sweeps in _PINNED.items():
        for member_row_ids in sweeps.values():
            for rid in member_row_ids:
                assignment[rid] = Split.HOLDOUT.value
    # 1 行だけ改竄して SELECTION にする（割当実装の欠陥を模す）。
    any_family = next(iter(_PINNED))
    any_sweep_members = next(iter(_PINNED[any_family].values()))
    offending_row_id = any_sweep_members[0]
    assignment[offending_row_id] = Split.SELECTION.value

    manifest = _holdout_sweeps_manifest(_PINNED)
    manifest["realized_split"] = {"assignment": assignment}

    violations = c0_validate._check_holdout_sweeps_realized_membership(manifest)
    assert violations
    assert all(v.violation == "holdout_pin_not_in_holdout_split" for v in violations)
    assert any(v.family == any_family for v in violations)

    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes


# ---------------------------------------------------------------------------
# R23 対応（Codex 第 23 巡 P2 採用、2026-09-05, PRRT_kwDOSD2OOM6fgdGg）:
# top-level `holdout_sweeps` キー自体の削除は、修正前は `found_holdout=False`
# へ落ちて上記 R10/R11 の全チェックを丸ごと skip させ、
# `_check_holdout_sweeps_realized_membership()` も同型に沈黙し、C4 側
# (`campaign/cli.py::_run_c4`, 別テストで検証) の `expected_sweep_ids`
# フォールバックが全宣言 sweep（HOLDOUT 非常駐 sweep を含む）を使って偽の
# `DIRECTIONAL_SWEEP_UNRESOLVABLE_ON_HOLDOUT` terminal を生み得た。
# 修正: `frozen_design.design_revision == "1.1"` かつ `realized_split` も
# 存在する full/armed-shape manifest（`realized_split`/`holdout_sweeps` は
# `c0_freeze._attach_freeze_extras()` が常に同時に付与する sibling 非-core
# キー）に限り、top-level `holdout_sweeps` キー自体の存在を必須化する。
# `realized_split` を伴わない v1.1 manifest（`c0_freeze.dry_run()` の
# core-only 形状。secret 未生成で `holdout_sweeps` 欠落が設計上正当）は
# 対象外のまま——これを見誤ると `dry_run()` 自体を壊す（本 fix 実装時に
# `_complete_manifest()`/`c0_freeze.dry_run()` 双方で確認済み）。
# ---------------------------------------------------------------------------


def _full_manifest_with_holdout_sweeps(
    pinned: dict[str, dict[str, tuple[str, ...]]], *, include_realized_split: bool = True
) -> dict[str, object]:
    manifest: dict[str, object] = {
        "frozen_design": {"design_revision": "1.1"},
        "holdout_sweeps": {
            family: {sid: list(rids) for sid, rids in sweeps.items()}
            for family, sweeps in pinned.items()
        },
    }
    if include_realized_split:
        manifest["realized_split"] = {"assignment": {}}
    return manifest


def test_holdout_sweeps_top_level_key_required_for_v1_1_full_manifest_with_secret() -> None:
    """(a) `realized_split` を伴う v1.1 full-shape manifest から top-level
    `holdout_sweeps` キー自体を削除すると、split_secret 付きでも単独の
    violation で fail-closed する（以降の per-family 照合には到達しない）。"""
    manifest = _full_manifest_with_holdout_sweeps(_PINNED)
    del manifest["holdout_sweeps"]

    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, _SECRET)
    assert len(violations) == 1
    assert violations[0].violation == "holdout_pin_declaration_mismatch"
    assert "top-level holdout_sweeps section is required" in violations[0].detail

    result = c0_validate.validate_c0_manifest(manifest, split_secret=_SECRET)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes


def test_holdout_sweeps_top_level_key_required_for_v1_1_full_manifest_without_secret() -> None:
    """(b) 同じ削除は secret 無しの構造検査でも同様に検出される——本必須化は
    `split_secret` に依存しない（削除の有無だけを見る）。"""
    manifest = _full_manifest_with_holdout_sweeps(_PINNED)
    del manifest["holdout_sweeps"]

    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, None)
    assert len(violations) == 1
    assert violations[0].violation == "holdout_pin_declaration_mismatch"
    assert "top-level holdout_sweeps section is required" in violations[0].detail


def test_holdout_sweeps_top_level_key_present_for_v1_1_full_manifest_passes() -> None:
    """(c) top-level `holdout_sweeps` キーが正しく存在する v1.1 full-shape
    manifest では、本必須化チェックはノーオペのまま既存の per-family
    再導出照合のみが働く（`_PINNED` は secret 依存の完全再導出と一致する
    正当な pin なので違反なし）。"""
    manifest = _full_manifest_with_holdout_sweeps(_PINNED)
    assert c0_validate._check_holdout_sweeps_declaration_match(manifest, _SECRET) == ()


def test_holdout_sweeps_top_level_key_not_required_without_realized_split() -> None:
    """`realized_split` を伴わない v1.1 manifest（`c0_freeze.dry_run()` の
    core-only 形状——secret 未生成で `holdout_sweeps` 欠落が正当）は本
    必須化の対象外のまま——`is_v1_1` だけで判定すると `dry_run()` 自体を
    誤ってブロックしてしまう（本 fix 実装時に発見した回帰）。"""
    manifest = _full_manifest_with_holdout_sweeps(_PINNED, include_realized_split=False)
    del manifest["holdout_sweeps"]

    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, _SECRET)
    assert violations == ()


def test_holdout_sweeps_missing_declaration_for_non_exempt_family_fails_closed_dry_run_v1_1() -> None:
    """R23 追補: `is_v1_1` の manifest なら `split_secret=None` の dry-run
    経路でも R11 の非空必須化が働く（found_holdout による skip を無効化）。
    v1.0 legacy manifest（design_revision マーカー無し、上の
    `test_holdout_sweeps_missing_declaration_dry_run_backward_compatible`）
    は引き続き対象外のまま。"""
    tampered = {fam: dict(sweeps) for fam, sweeps in _PINNED.items()}
    del tampered["FORMANT_GT"]
    manifest = _full_manifest_with_holdout_sweeps(tampered)

    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, None)
    assert any(
        v.family == "FORMANT_GT" and v.violation == "holdout_pin_declaration_mismatch"
        for v in violations
    )
