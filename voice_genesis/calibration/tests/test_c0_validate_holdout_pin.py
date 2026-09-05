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

from voice_genesis.calibration import c0_validate, vocab
from voice_genesis.calibration.c0_freeze import STRATUM_FACTOR_NAMES, _row_inputs_for_split
from voice_genesis.calibration.fixtures.matrix import (
    build_matrix,
    claim_relevant_fields_by_family,
    declared_sweeps_by_family,
    invariance_axes_by_family,
    pin_holdout_sweeps_by_family,
)
from voice_genesis.calibration.splitter import pin_and_realize_holdout
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
    `u_num_bound`（と両 formula）を丸ごと消すと、v1.0 legacy 経路のように
    黙って `continue` せず、4 件 (bound x2 + formula x2) の violation が
    出て fail-closed になる。"""
    manifest = _v1_1_manifest_with_fixture_spec({"TILT_GT": {}})
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    sweep_ids = {v.sweep_id for v in violations}
    assert sweep_ids == {"u_gt_bound", "u_gt_bound_formula", "u_num_bound", "u_num_bound_formula"}
    assert all(v.family == "TILT_GT" for v in violations)
    assert all(v.violation == "u_bound_missing_or_invalid" for v in violations)

    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert result.u_gt_u_num_bound_violations == violations


def test_u_gt_u_num_bounds_v1_1_manifest_missing_one_bound_blocks() -> None:
    """v1.1 manifest から `u_num_bound`（+ その formula）だけを消しても検出
    される（`u_gt_bound` 側は無傷）。"""
    manifest = _v1_1_manifest_with_fixture_spec(
        {
            "TILT_GT": {
                "u_gt_bound": 0.0,
                "u_gt_bound_formula": "U_GT = 0 (analytic)",
            }
        }
    )
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    sweep_ids = {v.sweep_id for v in violations}
    assert sweep_ids == {"u_num_bound", "u_num_bound_formula"}
    assert all(v.family == "TILT_GT" for v in violations)


def test_u_gt_u_num_bounds_legacy_manifest_missing_bounds_still_not_blocked() -> None:
    """marker が無い legacy (v1.0) manifest は、両フィールド欠落でも従来
    どおり violation を出さない（後方互換の維持を明示的に固定する）。"""
    manifest = _manifest_with_fixture_spec({"TILT_GT": {}})
    assert not c0_validate._is_v1_1_manifest(manifest)
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert violations == ()


def test_u_gt_u_num_bounds_v1_1_manifest_complete_passes() -> None:
    """完全な v1.1 manifest（全 family、両 bound + formula 具備）は
    violation を出さない。"""
    fixture_spec: dict[str, object] = {}
    for family_id in ("F0_CONTROL", "FORMANT_GT", "TILT_GT", "APERIODICITY_GT", "TRANSITION_GT"):
        fixture_spec[family_id] = {
            "u_gt_bound": 0.0,
            "u_gt_bound_formula": "U_GT = 0 (analytic)",
            "u_num_bound": 0.024,
            "u_num_bound_formula": "U_num = derive_floor(...)",
        }
    for family_id in ("RESONANCE_GT", "IDENTITY_CAUSAL_SWEEP"):
        fixture_spec[family_id] = {
            "u_gt_bound": "ABSENT:diagnostic_only",
            "u_gt_bound_formula": "no gate input",
            "u_num_bound": "ABSENT:diagnostic_only",
            "u_num_bound_formula": "no gate input",
        }
    manifest = _v1_1_manifest_with_fixture_spec(fixture_spec)
    assert c0_validate._is_v1_1_manifest(manifest)
    violations = c0_validate._check_u_gt_u_num_bounds(manifest)
    assert violations == ()


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
