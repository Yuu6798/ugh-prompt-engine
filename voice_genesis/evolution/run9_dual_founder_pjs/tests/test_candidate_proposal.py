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
#
# PR #331 Codex bot レビュー第3巡指摘1（P1「Make the scheduled neighborhood
# ratio achievable」、採用）: `neighbors_of()` を値キー ±1/±2 量子化ステップ
# + 隣接 index キー（L 順で1つ前/後）の優先順位リストへ拡張した。以下は
# その決定論性・内部領域での3件達成可能性・端点（range端+index端）での
# shortfall の直接テスト。
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


def test_neighbors_of_deterministic_across_two_calls() -> None:
    ordering = _build_l()
    current_best = ("AX-P1", 1, 1.0)
    n1 = cp.neighbors_of(current_best, ordering)
    n2 = cp.neighbors_of(current_best, ordering)
    assert n1 == n2


def test_neighbors_of_ax_p1_interior_yields_priority_ordered_value_and_index_neighbors() -> None:
    # note_index=1（3-note fixture の中央、index 端ではない）・v=1.0
    # （domain 内部、range 端ではない）: 値キー±1/±2ステップの3件
    # （1.5/0.5/2.0。-1.0 側は 0.0 で domain 除外）+ 隣接 note_index 0/2 の
    # 同値候補2件が優先順位どおりに列挙される。
    ordering = _build_l()
    current_best = ("AX-P1", 1, 1.0)
    neighbors = cp.neighbors_of(current_best, ordering)
    assert neighbors == [
        ("AX-P1", 1, 1.5),
        ("AX-P1", 1, 0.5),
        ("AX-P1", 1, 2.0),
        ("AX-P1", 0, 1.0),
        ("AX-P1", 2, 1.0),
    ]


def test_neighbors_of_ax_p1_range_boundary_alone_still_achieves_three() -> None:
    # v=2.0（range 上端）だが note_index=1 は index 端ではないため、値キー
    # 側の不足（+2.5/+3.0 は range 外）を隣接 index キーの同値候補が補い、
    # 内部領域では3:1 が構造的に達成可能という本改訂の要点を示す。
    ordering = _build_l()
    current_best = ("AX-P1", 1, 2.0)
    neighbors = cp.neighbors_of(current_best, ordering)
    assert neighbors == [
        ("AX-P1", 1, 1.5),
        ("AX-P1", 1, 1.0),
        ("AX-P1", 0, 2.0),
        ("AX-P1", 2, 2.0),
    ]
    selected = cp.select_neighborhood_candidates(
        current_best, ordering, is_evaluated=lambda c: False, limit=3
    )
    assert len(selected) == 3


def test_neighbors_of_ax_p1_index_boundary_alone_still_achieves_three() -> None:
    # note_index=0（index 端）だが v=1.0 は range 端ではないため、値キー側
    # の±1/±2ステップだけで3件が確保できる（隣接 index キー側の不足は
    # index=-1 が存在しないため item5 のみ欠けるが item6 が補う）。
    ordering = _build_l()
    current_best = ("AX-P1", 0, 1.0)
    neighbors = cp.neighbors_of(current_best, ordering)
    assert neighbors == [
        ("AX-P1", 0, 1.5),
        ("AX-P1", 0, 0.5),
        ("AX-P1", 0, 2.0),
        ("AX-P1", 1, 1.0),
    ]
    selected = cp.select_neighborhood_candidates(
        current_best, ordering, is_evaluated=lambda c: False, limit=3
    )
    assert len(selected) == 3


def test_neighbors_of_ax_p1_double_endpoint_shortfall() -> None:
    # 端点の例外: note_count=1（index 端）かつ v=2.0（range 端）が同時に
    # 揃う場合のみ、優先順位リスト6項目を尽くしても3件に満たない
    # （item5/6 は index±1 が存在せず不適用、item1/3 は range 外）。
    single_note_ordering = cp.build_candidate_ordering(
        note_count=1, phrase_of_note=[0], original_duration_beats=[1.0]
    )
    current_best = ("AX-P1", 0, 2.0)
    neighbors = cp.neighbors_of(current_best, single_note_ordering)
    assert neighbors == [("AX-P1", 0, 1.5), ("AX-P1", 0, 1.0)]
    assert len(neighbors) < 3
    selected = cp.select_neighborhood_candidates(
        current_best, single_note_ordering, is_evaluated=lambda c: False, limit=3
    )
    assert len(selected) == 2  # shortfall: 呼び出し側が exploratory 規則で1件補充する


def test_neighbors_of_ax_d1_candidate_are_adjacent_grid_values() -> None:
    # 隣接 index キー（(0,(0,2))）の同値候補が優先順位リスト項目6として
    # 値キー±1ステップの2件を補い、3件ちょうどに達する。
    ordering = _build_l()
    current_best = ("AX-D1", 0, (0, 1), 0.5)
    neighbors = cp.neighbors_of(current_best, ordering)
    assert neighbors == [
        ("AX-D1", 0, (0, 1), 0.75),
        ("AX-D1", 0, (0, 1), 0.25),
        ("AX-D1", 0, (0, 2), 0.5),
    ]


def test_neighbors_of_ax_d1_boundary_at_minimum_delta() -> None:
    # delta=0.25（domain 最小、-0.25 側は 0 で L に存在しない）でも、隣接
    # index キー (0,(0,2)) の同値候補（item6）が値キー+1ステップ（item1）
    # と合わせて3件ちょうどを構成する。
    ordering = _build_l()
    current_best = ("AX-D1", 0, (0, 1), 0.25)
    neighbors = cp.neighbors_of(current_best, ordering)
    assert neighbors == [
        ("AX-D1", 0, (0, 1), 0.5),
        ("AX-D1", 0, (0, 1), 0.75),
        ("AX-D1", 0, (0, 2), 0.25),
    ]


def test_neighbors_of_ax_d1_sole_pair_double_endpoint_shortfall() -> None:
    # 端点の例外（AX-D1 版）: フレーズが2note のみで L 内 AX-D1 候補が
    # 唯一の (donor, receiver) ペア1件しか存在しない場合、値キー側
    # （±1/±2ステップとも donor の min-duration 上限を超え無効）・
    # index キー側（隣接 index キーが存在しない）のいずれも候補を持たず、
    # 近傍0件のまま全滅する（探査規則が3件全てを補充する）。
    sole_pair_ordering = cp.build_candidate_ordering(
        note_count=2, phrase_of_note=[0, 0], original_duration_beats=[0.5, 0.25]
    )
    ax_d1_only = [c for c in sole_pair_ordering if c[0] == "AX-D1"]
    assert ax_d1_only == [("AX-D1", 0, (0, 1), 0.25)]
    current_best = ("AX-D1", 0, (0, 1), 0.25)
    neighbors = cp.neighbors_of(current_best, sole_pair_ordering)
    assert neighbors == []
    selected = cp.select_neighborhood_candidates(
        current_best, sole_pair_ordering, is_evaluated=lambda c: False, limit=3
    )
    assert selected == []


def test_select_neighborhood_candidates_limits_to_three_and_skips_evaluated() -> None:
    ordering = _build_l()
    evaluated = {("AX-P1", 0, 0.5)}
    selected = cp.select_neighborhood_candidates(
        None, ordering, is_evaluated=lambda c: c in evaluated, limit=3
    )
    assert len(selected) <= 3
    assert all(c not in evaluated for c in selected)
    assert selected == sorted(selected)


def test_select_neighborhood_candidates_non_identity_best_achieves_three_neighbors() -> None:
    # PR #331 第3巡指摘1の直接テスト: 内部領域の非恒等 best は近傍3枠を
    # 構造的に満たす（旧実装は最大2件までしか満たせなかった）。
    ordering = _build_l()
    current_best = ("AX-P1", 1, 1.0)
    selected = cp.select_neighborhood_candidates(
        current_best, ordering, is_evaluated=lambda c: False, limit=3
    )
    assert len(selected) == 3
    assert selected == [
        ("AX-P1", 1, 1.5),
        ("AX-P1", 1, 0.5),
        ("AX-P1", 1, 2.0),
    ]


def test_select_neighborhood_candidates_shortfall_when_all_evaluated() -> None:
    ordering = _build_l()
    current_best = ("AX-P1", 1, 1.0)
    selected_none_evaluated = cp.select_neighborhood_candidates(
        current_best, ordering, is_evaluated=lambda c: False, limit=3
    )
    assert len(selected_none_evaluated) == 3
    selected_all_evaluated = cp.select_neighborhood_candidates(
        current_best, ordering, is_evaluated=lambda c: True, limit=3
    )
    assert selected_all_evaluated == []
