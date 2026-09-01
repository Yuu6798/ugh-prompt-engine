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


def test_selection_missing_required_field_raises() -> None:
    incomplete = CandidateCriteria(candidate_id="x")
    with pytest.raises(ValueError):
        select([incomplete], SelectionFamily.ABSOLUTE)


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
