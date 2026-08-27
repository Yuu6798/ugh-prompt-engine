"""test_candidate_proposal.py — RUN9 HARNESS-3c: `candidate_proposal.py`
（`candidate_generation_spec_v1.json` `proposal` 節の digest→候補写像・
近傍列挙の参照実装）の決定論性・全順序性・プロービング境界テスト。

PR #331 Codex bot レビュー第2巡指摘1（P1、採用）の検証: 同一入力から
同一候補列が再現すること・L が仕様どおりの辞書順であること・線形
プロービングが決定論的に境界まで動作することを確認する。
"""
from __future__ import annotations

import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import candidate_proposal as cp  # noqa: E402

# ---------------------------------------------------------------------------
# 固定 fixture: 1 phrase 3 note の小さな score（AX-D1 の (i,j) ペア列挙を
# 手計算で検証できる最小構成）。
# ---------------------------------------------------------------------------

NOTE_COUNT = 3
PHRASE_OF_NOTE = [0, 0, 0]  # 全て phrase 0
ORIGINAL_DURATION_BEATS = [1.0, 0.5, 0.25]


def _build_l():
    return cp.build_candidate_ordering(
        note_count=NOTE_COUNT,
        phrase_of_note=PHRASE_OF_NOTE,
        original_duration_beats=ORIGINAL_DURATION_BEATS,
    )


# ---------------------------------------------------------------------------
# 1. L（正準候補列）の構築・辞書順の性質
# ---------------------------------------------------------------------------


def test_candidate_ordering_deterministic_across_two_builds() -> None:
    l1 = _build_l()
    l2 = _build_l()
    assert l1 == l2


def test_candidate_ordering_is_sorted_ascending() -> None:
    ordering = _build_l()
    assert ordering == sorted(ordering)


def test_candidate_ordering_ax_d1_precedes_ax_p1() -> None:
    ordering = _build_l()
    axis_ids = [c[0] for c in ordering]
    # "AX-D1" < "AX-P1" (辞書順) のため AX-D1 群が全て先に並ぶ。
    first_p1 = axis_ids.index("AX-P1")
    assert all(a == "AX-D1" for a in axis_ids[:first_p1])
    assert all(a == "AX-P1" for a in axis_ids[first_p1:])


def test_candidate_ordering_ax_p1_excludes_zero_offset() -> None:
    ordering = _build_l()
    ax_p1_offsets = {c[2] for c in ordering if c[0] == "AX-P1"}
    assert 0.0 not in ax_p1_offsets
    assert ax_p1_offsets == set(cp.AX_P1_OFFSET_DOMAIN)


def test_candidate_ordering_ax_p1_note_index_ascending_then_offset_ascending() -> None:
    ordering = _build_l()
    ax_p1 = [c for c in ordering if c[0] == "AX-P1"]
    expected = [
        ("AX-P1", note_index, offset)
        for note_index in range(NOTE_COUNT)
        for offset in cp.AX_P1_OFFSET_DOMAIN
    ]
    assert ax_p1 == expected


def test_candidate_ordering_ax_d1_respects_min_duration() -> None:
    # note 2 の original duration = 0.25 = min_duration そのもの
    # -> どの (i,j) でも note 2 が donor(i) 側になる候補は生成されない
    # (max_steps = (0.25-0.25)//0.25 = 0)。
    ordering = _build_l()
    donors = {c[2][0] for c in ordering if c[0] == "AX-D1"}
    assert 2 not in donors


def test_candidate_ordering_ax_d1_max_delta_bounded_by_min_duration() -> None:
    # note 0 (duration=1.0) が donor の場合、最大 delta は
    # (1.0 - 0.25) // 0.25 * 0.25 = 0.75。
    ordering = _build_l()
    deltas_from_note0 = sorted(c[3] for c in ordering if c[0] == "AX-D1" and c[2][0] == 0)
    assert deltas_from_note0[-1] == 0.75
    assert deltas_from_note0[0] == 0.25


def test_candidate_ordering_ax_d1_pair_direction_distinct() -> None:
    ordering = _build_l()
    pairs = {c[2] for c in ordering if c[0] == "AX-D1"}
    assert (0, 1) in pairs
    assert (1, 0) in pairs


def test_candidate_ordering_empty_note_count_yields_no_ax_p1() -> None:
    ordering = cp.build_candidate_ordering(
        note_count=0, phrase_of_note=[], original_duration_beats=[]
    )
    assert ordering == []


# ---------------------------------------------------------------------------
# 2. digest -> index 写像の決定論性
# ---------------------------------------------------------------------------


def test_digest_bytes_deterministic() -> None:
    d1 = cp.digest_bytes(909002, "arm-a", "R9F-01", 1, 3)
    d2 = cp.digest_bytes(909002, "arm-a", "R9F-01", 1, 3)
    assert d1 == d2
    assert len(d1) == 32  # sha256 digest length


def test_digest_bytes_changes_with_any_component() -> None:
    base = cp.digest_bytes(909002, "arm-a", "R9F-01", 1, 3)
    assert cp.digest_bytes(1, "arm-a", "R9F-01", 1, 3) != base
    assert cp.digest_bytes(909002, "arm-b", "R9F-01", 1, 3) != base
    assert cp.digest_bytes(909002, "arm-a", "R9F-02", 1, 3) != base
    assert cp.digest_bytes(909002, "arm-a", "R9F-01", 2, 3) != base
    assert cp.digest_bytes(909002, "arm-a", "R9F-01", 1, 2) != base


def test_digest_to_index_matches_manual_big_endian_computation() -> None:
    digest = cp.digest_bytes(909002, "arm-a", "R9F-01", 1, 3)
    expected_u = int.from_bytes(digest[:8], byteorder="big", signed=False)
    assert cp.digest_to_index(digest, list_length=100) == expected_u % 100


def test_digest_to_index_rejects_nonpositive_list_length() -> None:
    digest = cp.digest_bytes(909002, "arm-a", "R9F-01", 1, 3)
    try:
        cp.digest_to_index(digest, list_length=0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for list_length=0")


# ---------------------------------------------------------------------------
# 3. 探査候補の決定論線形プロービング（境界）
# ---------------------------------------------------------------------------


def test_select_exploratory_candidate_deterministic_across_two_calls() -> None:
    ordering = _build_l()
    kwargs = dict(seed=909002, arm="arm-a", founder_id="R9F-01", trial=5, candidate_index=3)
    c1 = cp.select_exploratory_candidate(ordering, is_acceptable=lambda c: True, **kwargs)
    c2 = cp.select_exploratory_candidate(ordering, is_acceptable=lambda c: True, **kwargs)
    assert c1 == c2 is not None


def test_select_exploratory_candidate_picks_initial_index_when_acceptable() -> None:
    ordering = _build_l()
    digest = cp.digest_bytes(909002, "arm-a", "R9F-01", 5, 3)
    start = cp.digest_to_index(digest, list_length=len(ordering))
    picked = cp.select_exploratory_candidate(
        ordering,
        seed=909002,
        arm="arm-a",
        founder_id="R9F-01",
        trial=5,
        candidate_index=3,
        is_acceptable=lambda c: True,
    )
    assert picked == ordering[start]


def test_select_exploratory_candidate_probes_forward_when_start_rejected() -> None:
    ordering = _build_l()
    digest = cp.digest_bytes(909002, "arm-a", "R9F-01", 5, 3)
    start = cp.digest_to_index(digest, list_length=len(ordering))
    # 初期 index のみ拒否 -> 次の index (mod len(L)) が選ばれるはず。
    rejected = {ordering[start]}
    picked = cp.select_exploratory_candidate(
        ordering,
        seed=909002,
        arm="arm-a",
        founder_id="R9F-01",
        trial=5,
        candidate_index=3,
        is_acceptable=lambda c: c not in rejected,
    )
    expected = ordering[(start + 1) % len(ordering)]
    assert picked == expected


def test_select_exploratory_candidate_wraps_around_end_of_list() -> None:
    ordering = _build_l()
    digest = cp.digest_bytes(909002, "arm-a", "R9F-01", 5, 3)
    start = cp.digest_to_index(digest, list_length=len(ordering))
    # start から len(L)-1 個先まで（ラップアラウンドを含む）全て拒否し、
    # 最後の1件だけ受理 -> プロービングが mod でラップして到達することを
    # 確認する。
    only_acceptable_offset = len(ordering) - 1
    only_acceptable = ordering[(start + only_acceptable_offset) % len(ordering)]
    picked = cp.select_exploratory_candidate(
        ordering,
        seed=909002,
        arm="arm-a",
        founder_id="R9F-01",
        trial=5,
        candidate_index=3,
        is_acceptable=lambda c: c == only_acceptable,
    )
    assert picked == only_acceptable


def test_select_exploratory_candidate_exhaustion_returns_none() -> None:
    ordering = _build_l()
    picked = cp.select_exploratory_candidate(
        ordering,
        seed=909002,
        arm="arm-a",
        founder_id="R9F-01",
        trial=5,
        candidate_index=3,
        is_acceptable=lambda c: False,
    )
    assert picked is None


def test_select_exploratory_candidate_empty_list_returns_none() -> None:
    picked = cp.select_exploratory_candidate(
        [],
        seed=909002,
        arm="arm-a",
        founder_id="R9F-01",
        trial=5,
        candidate_index=3,
        is_acceptable=lambda c: True,
    )
    assert picked is None


# ---------------------------------------------------------------------------
# 4. 近傍候補列挙
# ---------------------------------------------------------------------------


def test_neighbors_of_identity_returns_one_step_candidates_only() -> None:
    ordering = _build_l()
    neighbors = cp.neighbors_of(None, ordering)
    for cand in neighbors:
        if cand[0] == "AX-P1":
            assert abs(abs(cand[2]) - 0.5) < 1e-9
        else:
            assert abs(cand[3] - 0.25) < 1e-9
    assert neighbors == sorted(neighbors)


def test_neighbors_of_ax_p1_candidate_are_adjacent_grid_values() -> None:
    ordering = _build_l()
    current_best = ("AX-P1", 0, 1.0)
    neighbors = cp.neighbors_of(current_best, ordering)
    assert set(neighbors) == {("AX-P1", 0, 0.5), ("AX-P1", 0, 1.5)}


def test_neighbors_of_ax_p1_boundary_has_single_neighbor() -> None:
    ordering = _build_l()
    current_best = ("AX-P1", 0, 2.0)  # range 上端: +2.5 は L に存在しない
    neighbors = cp.neighbors_of(current_best, ordering)
    assert neighbors == [("AX-P1", 0, 1.5)]


def test_neighbors_of_ax_d1_candidate_are_adjacent_grid_values() -> None:
    ordering = _build_l()
    current_best = ("AX-D1", 0, (0, 1), 0.5)
    neighbors = cp.neighbors_of(current_best, ordering)
    assert set(neighbors) == {("AX-D1", 0, (0, 1), 0.25), ("AX-D1", 0, (0, 1), 0.75)}


def test_neighbors_of_ax_d1_boundary_at_minimum_delta() -> None:
    ordering = _build_l()
    current_best = ("AX-D1", 0, (0, 1), 0.25)  # -0.25 側は 0 = L に存在しない
    neighbors = cp.neighbors_of(current_best, ordering)
    assert neighbors == [("AX-D1", 0, (0, 1), 0.5)]


def test_select_neighborhood_candidates_limits_to_three_and_skips_evaluated() -> None:
    ordering = _build_l()
    evaluated = {("AX-P1", 0, 0.5)}
    selected = cp.select_neighborhood_candidates(
        None, ordering, is_evaluated=lambda c: c in evaluated, limit=3
    )
    assert len(selected) <= 3
    assert all(c not in evaluated for c in selected)
    assert selected == sorted(selected)


def test_select_neighborhood_candidates_shortfall_when_all_evaluated() -> None:
    ordering = _build_l()
    current_best = ("AX-P1", 0, 2.0)  # 単一近傍のみ存在
    selected_none_evaluated = cp.select_neighborhood_candidates(
        current_best, ordering, is_evaluated=lambda c: False, limit=3
    )
    assert selected_none_evaluated == [("AX-P1", 0, 1.5)]
    selected_all_evaluated = cp.select_neighborhood_candidates(
        current_best, ordering, is_evaluated=lambda c: True, limit=3
    )
    assert selected_all_evaluated == []
