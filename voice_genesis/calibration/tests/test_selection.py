from __future__ import annotations

import pytest

from voice_genesis.calibration.selection import (
    CandidateCriteria,
    SelectionFamily,
    round_error,
    round_rate,
    select,
    select_across_ceilings,
)
from voice_genesis.calibration.vocab import ClaimCeiling


def test_round_error_3_sig_figs_hand_computed() -> None:
    # 有効数字3桁: 123456 -> 123000, 0.0012345 -> 0.00123, 9.8765 -> 9.88
    assert round_error(123456.0) == pytest.approx(123000.0)
    assert round_error(0.0012345) == pytest.approx(0.00123)
    assert round_error(9.8765) == pytest.approx(9.88)


def test_round_error_zero() -> None:
    assert round_error(0.0) == 0.0


def test_round_error_negative() -> None:
    assert round_error(-9.8765) == pytest.approx(-9.88)


def test_round_rate_0_001_steps_hand_computed() -> None:
    # 0.0014 -> 0.001 (最近接 0.001 刻み), 0.0016 -> 0.002, 0.1234 -> 0.123
    assert round_rate(0.0014) == pytest.approx(0.001)
    assert round_rate(0.0016) == pytest.approx(0.002)
    assert round_rate(0.1234) == pytest.approx(0.123)


def test_selection_rounding_creates_tie_broken_by_next_criterion() -> None:
    # candidate A と B は primary_normalized_mae が丸め後に同値になるよう
    # わずかに異なる生値を持つ (有効数字3桁: 0.123456 と 0.123449 は
    # どちらも round_error で 0.123 になる)。次の基準 |signed_bias| で
    # A(0.01) < B(0.02) のため A が勝つ。
    a = CandidateCriteria(
        candidate_id="cand-A",
        primary_normalized_mae=0.123456,
        signed_bias=0.01,
        primary_q95_ae=0.5,
    )
    b = CandidateCriteria(
        candidate_id="cand-B",
        primary_normalized_mae=0.123449,
        signed_bias=0.02,
        primary_q95_ae=0.5,
    )
    assert round_error(a.primary_normalized_mae) == round_error(b.primary_normalized_mae)
    outcome = select([a, b], SelectionFamily.ABSOLUTE)
    assert outcome.selected_candidate_id == "cand-A"
    assert outcome.ranked_candidate_ids == ("cand-A", "cand-B")


def test_selection_candidate_id_lexical_tiebreak_last_resort() -> None:
    # 全ての criterion が完全一致する 2 候補は candidate_id の字句順で決まる。
    common = dict(
        primary_normalized_mae=0.1, signed_bias=0.0, primary_q95_ae=0.2,
        nuisance_sensitivity_max=0.0, missing_failure_rate=0.0, complexity_rank=1,
    )
    z = CandidateCriteria(candidate_id="zzz", **common)
    a = CandidateCriteria(candidate_id="aaa", **common)
    outcome = select([z, a], SelectionFamily.ABSOLUTE)
    assert outcome.selected_candidate_id == "aaa"
    assert outcome.ranked_candidate_ids == ("aaa", "zzz")


def test_selection_empty_eligible_set_is_selection_failed_closed() -> None:
    ineligible = CandidateCriteria(
        candidate_id="cand-1",
        eligible=False,
        primary_normalized_mae=0.1,
        signed_bias=0.0,
        primary_q95_ae=0.1,
    )
    outcome = select([ineligible], SelectionFamily.ABSOLUTE)
    assert outcome.outcome == "SELECTION_FAILED_CLOSED"
    assert outcome.selected_candidate_id is None
    assert outcome.ranked_candidate_ids == ()


def test_selection_empty_candidates_is_selection_failed_closed() -> None:
    outcome = select([], SelectionFamily.DIRECTIONAL)
    assert outcome.outcome == "SELECTION_FAILED_CLOSED"


def test_selection_records_raw_and_rounded_vectors_for_every_candidate() -> None:
    a = CandidateCriteria(
        candidate_id="cand-A", primary_normalized_mae=0.123456, signed_bias=0.01,
        primary_q95_ae=0.5,
    )
    b = CandidateCriteria(
        candidate_id="cand-B", eligible=False, primary_normalized_mae=0.9,
        signed_bias=0.9, primary_q95_ae=0.9,
    )
    outcome = select([a, b], SelectionFamily.ABSOLUTE)
    # ineligible な候補も raw/rounded vector には記録される (SELECTION_FROZEN
    # event で全候補ベクトルを記録する要件)。
    assert "cand-A" in outcome.raw_vectors and "cand-A" in outcome.rounded_vectors
    assert "cand-B" in outcome.raw_vectors and "cand-B" in outcome.rounded_vectors
    assert outcome.raw_vectors["cand-A"][0] == pytest.approx(0.123456)
    assert outcome.rounded_vectors["cand-A"][0] == pytest.approx(0.123)


def test_selection_directional_family_uses_kendall_tau_and_reversal_rate() -> None:
    good = CandidateCriteria(
        candidate_id="good", kendall_tau=0.95, adjacent_reversal_rate=0.0,
    )
    bad = CandidateCriteria(
        candidate_id="bad", kendall_tau=0.5, adjacent_reversal_rate=0.1,
    )
    outcome = select([good, bad], SelectionFamily.DIRECTIONAL)
    assert outcome.selected_candidate_id == "good"


def test_selection_missing_required_field_is_ineligible_not_a_raise() -> None:
    """[Codex レビュー 2026-09-01 P1] regression: `eligible=True`（既定値）
    でも criteria payload そのものが欠けている候補は、旧実装では
    `_vector_for()` が無条件に呼ばれ `ValueError` を送出していた。修正後は
    `criteria_payload_absent` として ineligible 扱いになり、
    fail-closed（`SELECTION_FAILED_CLOSED`）へ正常に到達する。"""
    incomplete = CandidateCriteria(candidate_id="x")
    outcome = select([incomplete], SelectionFamily.ABSOLUTE)
    assert outcome.outcome == "SELECTION_FAILED_CLOSED"
    assert outcome.selected_candidate_id is None
    assert outcome.ineligible_candidates == (("x", "criteria_payload_absent"),)
    # criteria が欠けているため vector は構築されない。
    assert "x" not in outcome.raw_vectors
    assert "x" not in outcome.rounded_vectors


def test_selection_mix_of_eligible_and_criteria_absent_ineligible_succeeds() -> None:
    """[Codex レビュー 2026-09-01 P1] finding #2 regression: eligible な候補
    2 件 + criteria payload が丸ごと欠けた ineligible 候補 1 件（D4C without
    pyworld のような「測定基準そのものが無い」ケースを模す）を混在させても、
    `_vector_for()` が ineligible 候補で ValueError を送出することなく
    eligible な 2 件のみで選抜が成立し、ineligible 候補は理由付きで
    `SelectionOutcome.ineligible_candidates` に記録されること。"""
    a = CandidateCriteria(
        candidate_id="cand-A", primary_normalized_mae=0.1, signed_bias=0.0,
        primary_q95_ae=0.1,
    )
    b = CandidateCriteria(
        candidate_id="cand-B", primary_normalized_mae=0.5, signed_bias=0.0,
        primary_q95_ae=0.5,
    )
    no_criteria = CandidateCriteria(candidate_id="d4c-no-pyworld")
    outcome = select([a, b, no_criteria], SelectionFamily.ABSOLUTE)
    assert outcome.outcome == "SELECTED"
    assert outcome.selected_candidate_id == "cand-A"
    assert set(outcome.ranked_candidate_ids) == {"cand-A", "cand-B"}
    assert outcome.ineligible_candidates == (("d4c-no-pyworld", "criteria_payload_absent"),)
    assert "d4c-no-pyworld" not in outcome.raw_vectors


def test_selection_all_ineligible_without_criteria_is_selection_failed_closed() -> None:
    """全候補が criteria payload absent の ineligible → `SELECTION_FAILED_CLOSED`
    （raise ではなく fail-closed 経路に正常到達すること）。"""
    a = CandidateCriteria(candidate_id="no-criteria-a")
    b = CandidateCriteria(candidate_id="no-criteria-b")
    outcome = select([a, b], SelectionFamily.ABSOLUTE)
    assert outcome.outcome == "SELECTION_FAILED_CLOSED"
    assert outcome.selected_candidate_id is None
    assert set(outcome.ineligible_candidates) == {
        ("no-criteria-a", "criteria_payload_absent"),
        ("no-criteria-b", "criteria_payload_absent"),
    }


def test_selection_flagged_ineligible_with_criteria_still_builds_vector() -> None:
    """`eligible=False` だが criteria 自体は揃っている候補は、引き続き
    raw/rounded vector に記録される（SELECTION_FROZEN 全候補監査要件は
    保たれる）一方、ineligible として reason `"flagged_ineligible"` で
    記録される。"""
    a = CandidateCriteria(
        candidate_id="cand-A", primary_normalized_mae=0.1, signed_bias=0.0,
        primary_q95_ae=0.1,
    )
    flagged = CandidateCriteria(
        candidate_id="cand-B", eligible=False, primary_normalized_mae=0.9,
        signed_bias=0.9, primary_q95_ae=0.9,
    )
    outcome = select([a, flagged], SelectionFamily.ABSOLUTE)
    assert outcome.outcome == "SELECTED"
    assert outcome.ineligible_candidates == (("cand-B", "flagged_ineligible"),)
    assert "cand-B" in outcome.raw_vectors and "cand-B" in outcome.rounded_vectors


# ---------------------------------------------------------------------------
# ceiling 階級間裁定 (select_across_ceilings) — §2.6 / Codex レビュー第 4 巡
# ---------------------------------------------------------------------------


def test_select_across_ceilings_absolute_pool_wins_even_if_directional_looks_better() -> None:
    # DIRECTIONAL 候補 2 件は kendall_tau=0.999 (ほぼ完璧) と非常に良い数値を
    # 持つが、ceiling が ABSOLUTE の候補が 1 件でも非空プールとして存在すれば
    # criteria の数値に関わらず ABSOLUTE pool のみで選抜される。
    absolute_cand = CandidateCriteria(
        candidate_id="abs-1",
        ceiling=ClaimCeiling.ABSOLUTE,
        primary_normalized_mae=0.5,  # 数値上は平凡
        signed_bias=0.5,
        primary_q95_ae=0.5,
    )
    directional_1 = CandidateCriteria(
        candidate_id="dir-1",
        ceiling=ClaimCeiling.DIRECTIONAL,
        kendall_tau=0.999,
        adjacent_reversal_rate=0.0,
    )
    directional_2 = CandidateCriteria(
        candidate_id="dir-2",
        ceiling=ClaimCeiling.DIRECTIONAL,
        kendall_tau=0.998,
        adjacent_reversal_rate=0.0,
    )
    outcome = select_across_ceilings([absolute_cand, directional_1, directional_2])
    assert outcome.family == SelectionFamily.ABSOLUTE
    assert outcome.selected_candidate_id == "abs-1"
    assert outcome.outcome == "SELECTED"


def test_select_across_ceilings_falls_back_to_directional_when_absolute_empty() -> None:
    directional_1 = CandidateCriteria(
        candidate_id="dir-1",
        ceiling=ClaimCeiling.DIRECTIONAL,
        kendall_tau=0.5,
        adjacent_reversal_rate=0.1,
    )
    directional_2 = CandidateCriteria(
        candidate_id="dir-2",
        ceiling=ClaimCeiling.DIRECTIONAL,
        kendall_tau=0.9,
        adjacent_reversal_rate=0.0,
    )
    diagnostic_only = CandidateCriteria(
        candidate_id="diag-1", ceiling=ClaimCeiling.DIAGNOSTIC_ONLY
    )
    outcome = select_across_ceilings([directional_1, directional_2, diagnostic_only])
    assert outcome.family == SelectionFamily.DIRECTIONAL
    assert outcome.selected_candidate_id == "dir-2"  # 高い kendall_tau が優先
    assert outcome.outcome == "SELECTED"


def test_select_across_ceilings_diagnostic_only_never_selected() -> None:
    diag_1 = CandidateCriteria(candidate_id="diag-1", ceiling=ClaimCeiling.DIAGNOSTIC_ONLY)
    diag_2 = CandidateCriteria(candidate_id="diag-2", ceiling=ClaimCeiling.DIAGNOSTIC_ONLY)
    outcome = select_across_ceilings([diag_1, diag_2])
    assert outcome.outcome == "SELECTION_FAILED_CLOSED"
    assert outcome.selected_candidate_id is None


def test_select_duplicate_candidate_id_raises() -> None:
    """[Codex レビュー 2026-09-01] regression: dict comprehension で
    candidate_id をキーにすると重複時に一方が黙って上書きされる。`select()`
    はこれを検出して `ValueError` を送出しなければならない。"""
    a = CandidateCriteria(
        candidate_id="dup", primary_normalized_mae=0.1, signed_bias=0.0, primary_q95_ae=0.1,
    )
    b = CandidateCriteria(
        candidate_id="dup", primary_normalized_mae=0.9, signed_bias=0.9, primary_q95_ae=0.9,
    )
    with pytest.raises(ValueError, match="dup"):
        select([a, b], SelectionFamily.ABSOLUTE)


def test_select_across_ceilings_duplicate_candidate_id_raises() -> None:
    """[Codex レビュー 2026-09-01] regression: `select_across_ceilings` も
    pool 分割前に candidate_id の一意性を検証する。"""
    a = CandidateCriteria(
        candidate_id="dup", ceiling=ClaimCeiling.ABSOLUTE,
        primary_normalized_mae=0.1, signed_bias=0.0, primary_q95_ae=0.1,
    )
    b = CandidateCriteria(
        candidate_id="dup", ceiling=ClaimCeiling.DIRECTIONAL,
        kendall_tau=0.5, adjacent_reversal_rate=0.0,
    )
    with pytest.raises(ValueError, match="dup"):
        select_across_ceilings([a, b])


def test_select_across_ceilings_ineligible_absolute_does_not_block_directional_fallback() -> None:
    ineligible_absolute = CandidateCriteria(
        candidate_id="abs-ineligible",
        ceiling=ClaimCeiling.ABSOLUTE,
        eligible=False,
        primary_normalized_mae=0.1,
        signed_bias=0.0,
        primary_q95_ae=0.1,
    )
    directional = CandidateCriteria(
        candidate_id="dir-1",
        ceiling=ClaimCeiling.DIRECTIONAL,
        kendall_tau=0.7,
        adjacent_reversal_rate=0.0,
    )
    outcome = select_across_ceilings([ineligible_absolute, directional])
    assert outcome.family == SelectionFamily.DIRECTIONAL
    assert outcome.selected_candidate_id == "dir-1"


def test_select_across_ceilings_absolute_without_criteria_falls_back_to_directional() -> None:
    """[P1] regression: ceiling プールの非空判定が `eligible` フラグのみに
    基づいていると、`ceiling=ABSOLUTE` かつ `eligible=True`（既定値）だが
    criteria payload そのものが欠けた候補（pyworld 未導入時の D4C 系候補等）
    が ABSOLUTE pool を「非空」に見せかけ、`select()` 内部で
    `criteria_payload_absent` として ineligible 判定された結果、eligible
    候補 0 件で `SELECTION_FAILED_CLOSED` が DIRECTIONAL へフォールバック
    せずに直接返っていた。修正後は `_has_required_criteria()` も非空判定に
    使うため、真に選抜可能な候補が無い ABSOLUTE pool は非空とみなされず
    DIRECTIONAL へ正しくフォールバックする。"""
    absolute_no_criteria = CandidateCriteria(
        candidate_id="abs-no-criteria",
        ceiling=ClaimCeiling.ABSOLUTE,
        # primary_normalized_mae / signed_bias / primary_q95_ae は未設定
        # (criteria payload absent).
    )
    directional = CandidateCriteria(
        candidate_id="dir-1",
        ceiling=ClaimCeiling.DIRECTIONAL,
        kendall_tau=0.7,
        adjacent_reversal_rate=0.0,
    )
    outcome = select_across_ceilings([absolute_no_criteria, directional])
    assert outcome.family == SelectionFamily.DIRECTIONAL
    assert outcome.outcome == "SELECTED"
    assert outcome.selected_candidate_id == "dir-1"
    # ABSOLUTE 側の criteria-absent 候補は ineligible として理由付きで記録
    # される（監査要件: なぜ選抜対象外だったかを追跡できる）。
    assert ("abs-no-criteria", "criteria_payload_absent") in outcome.ineligible_candidates
