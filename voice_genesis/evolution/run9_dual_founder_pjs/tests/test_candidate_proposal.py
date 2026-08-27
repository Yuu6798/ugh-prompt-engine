"""test_candidate_proposal.py — RUN9 HARNESS-3c: `candidate_proposal.py`
（`candidate_generation_spec_v1.json` `proposal` 節の digest→候補写像・
近傍列挙の参照実装）の決定論性・全順序性・プロービング境界テスト。

PR #331 Codex bot レビュー第2巡指摘1（P1、採用）の検証: 同一入力から
同一候補列が再現すること・L が仕様どおりの辞書順であること・線形
プロービングが決定論的に境界まで動作することを確認する。

PR #331 Codex bot レビュー第5巡指摘1（P2「proposal 定数の pinned catalog
への束縛」、採用）: `candidate_proposal.py` の L 構築・近傍列挙はハード
コード定数を持たず、呼び出し側が渡す `catalog` dict から都度値を導出する
（`score_axis_transform.py` と同型の規約）。本ファイルは
`score_axis_catalog_v1.json`（本 harness の pinned catalog 実体）を直接
読んだ実データ辞書を注入して決定論性テストを回す
（`tests/test_h3c_learning_recipe_manifests.py::_manifest_data()` と同型の
パターン）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import candidate_proposal as cp  # noqa: E402

# pinned catalog 実データ（`run9_schema.SCORE_AXIS_CATALOG_PATH` と同一
# ファイルを直接読む。pin 検証・cross-check 込みのフル load は
# `run9_schema.load_pinned_score_axis_catalog_manifest()` が別途担う —
# 本ファイルは candidate_proposal.py の catalog 消費ロジックのみを高速に
# 検査するため、実バイトを直接注入する）。
CATALOG = json.loads(
    (_RUN_DIR / "inputs" / "score_axis_catalog_v1.json").read_text(encoding="utf-8")
)

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
        catalog=CATALOG,
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


# AX-P1 offset domain の期待値（`score_axis_catalog_v1.json` axes.AX-P1
# range_semitones=[-2.0,2.0]・quantization_step_semitones=0.5 から 0 を
# 除いた8値。`candidate_generation_spec_v1.json`
# `proposal.candidate_ordering.ax_p1.offset_domain` の逐語と一致する——
# catalog 実データから独立に定義し、`candidate_proposal.py` 側の導出結果
# と突き合わせる（実装と同じ計算式をテストが再実装するトートロジーを
# 避けるため、pinned catalog の現行値をここへ literal で書く）。
EXPECTED_AX_P1_OFFSET_DOMAIN = (-2.0, -1.5, -1.0, -0.5, 0.5, 1.0, 1.5, 2.0)


def test_catalog_ax_p1_matches_expected_offset_domain_fixture() -> None:
    # CATALOG（repo 収載の pinned catalog 実データ）が本ファイルの
    # EXPECTED_AX_P1_OFFSET_DOMAIN 前提と一致することを確認する
    # （catalog が repin されて range/step が変わればこのテストが検出する）。
    axis = CATALOG["axes"]["AX-P1"]
    lo, hi = axis["range_semitones"]
    step = axis["quantization_step_semitones"]
    assert (lo, hi, step) == (-2.0, 2.0, 0.5)


def test_candidate_ordering_ax_p1_excludes_zero_offset() -> None:
    ordering = _build_l()
    ax_p1_offsets = {c[2] for c in ordering if c[0] == "AX-P1"}
    assert 0.0 not in ax_p1_offsets
    assert ax_p1_offsets == set(EXPECTED_AX_P1_OFFSET_DOMAIN)


def test_candidate_ordering_ax_p1_note_index_ascending_then_offset_ascending() -> None:
    ordering = _build_l()
    ax_p1 = [c for c in ordering if c[0] == "AX-P1"]
    expected = [
        ("AX-P1", note_index, offset)
        for note_index in range(NOTE_COUNT)
        for offset in EXPECTED_AX_P1_OFFSET_DOMAIN
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
        note_count=0, phrase_of_note=[], original_duration_beats=[], catalog=CATALOG
    )
    assert ordering == []


# ---------------------------------------------------------------------------
# 1c. note_count と phrase_of_note/original_duration_beats の長さ不一致は
# fail-closed で拒否する（PR #331 Codex bot レビュー第13巡指摘1、P2、採用）。
# `build_ax_d1_ordering()` 側の phrase_of_note/original_duration_beats 相互
# 長検査だけでは、note_count が独立に取り違えられた場合を検出できない。
# ---------------------------------------------------------------------------


def test_build_candidate_ordering_rejects_note_count_larger_than_domains() -> None:
    # note_count が phrase_of_note/original_duration_beats より過大な場合、
    # 存在しない note の AX-P1 候補が L に混入し得る不整合であり拒否する。
    with pytest.raises(ValueError, match="note_count"):
        cp.build_candidate_ordering(
            note_count=NOTE_COUNT + 1,
            phrase_of_note=PHRASE_OF_NOTE,
            original_duration_beats=ORIGINAL_DURATION_BEATS,
            catalog=CATALOG,
        )


def test_build_candidate_ordering_rejects_note_count_smaller_than_domains() -> None:
    # note_count が過小な場合、有効な AX-P1 候補が無警告で欠落し digest
    # ordinal が変化し得る不整合であり拒否する。
    with pytest.raises(ValueError, match="note_count"):
        cp.build_candidate_ordering(
            note_count=NOTE_COUNT - 1,
            phrase_of_note=PHRASE_OF_NOTE,
            original_duration_beats=ORIGINAL_DURATION_BEATS,
            catalog=CATALOG,
        )


def test_build_candidate_ordering_accepts_consistent_note_count() -> None:
    # 一致するケースは引き続き pass する（回帰確認）。
    ordering = _build_l()
    assert ordering != []


# ---------------------------------------------------------------------------
# 1b. catalog 値改変が L へ反映されること（PR #331 第5巡指摘1、P2、採用の
# 直接テスト: L 構築はハードコード定数からではなく catalog 引数から都度
# 導出するため、catalog の range/step を改変すれば L の内容が追随する）。
# ---------------------------------------------------------------------------


def test_build_candidate_ordering_reflects_narrowed_ax_p1_catalog() -> None:
    narrowed = json.loads(json.dumps(CATALOG))
    narrowed["axes"]["AX-P1"]["range_semitones"] = [-1.0, 1.0]
    ordering_default = cp.build_candidate_ordering(
        note_count=NOTE_COUNT,
        phrase_of_note=PHRASE_OF_NOTE,
        original_duration_beats=ORIGINAL_DURATION_BEATS,
        catalog=CATALOG,
    )
    ordering_narrowed = cp.build_candidate_ordering(
        note_count=NOTE_COUNT,
        phrase_of_note=PHRASE_OF_NOTE,
        original_duration_beats=ORIGINAL_DURATION_BEATS,
        catalog=narrowed,
    )
    default_offsets = {c[2] for c in ordering_default if c[0] == "AX-P1"}
    narrowed_offsets = {c[2] for c in ordering_narrowed if c[0] == "AX-P1"}
    assert default_offsets == set(EXPECTED_AX_P1_OFFSET_DOMAIN)
    assert narrowed_offsets == {-1.0, -0.5, 0.5, 1.0}
    assert narrowed_offsets < default_offsets


def test_build_candidate_ordering_reflects_widened_ax_d1_min_duration_catalog() -> None:
    stricter = json.loads(json.dumps(CATALOG))
    stricter["axes"]["AX-D1"]["min_duration_beats"] = 0.75
    ordering_default = cp.build_candidate_ordering(
        note_count=NOTE_COUNT,
        phrase_of_note=PHRASE_OF_NOTE,
        original_duration_beats=ORIGINAL_DURATION_BEATS,
        catalog=CATALOG,
    )
    ordering_stricter = cp.build_candidate_ordering(
        note_count=NOTE_COUNT,
        phrase_of_note=PHRASE_OF_NOTE,
        original_duration_beats=ORIGINAL_DURATION_BEATS,
        catalog=stricter,
    )
    # デフォルト catalog（min_duration=0.25）では note 0 (duration=1.0)
    # donor の delta が最大 0.75 まで存在する。min_duration を 0.75 へ
    # 引き上げると donor 側の余地が (1.0-0.75)=0.25 分のみに縮小する。
    default_max_delta = max(
        c[3] for c in ordering_default if c[0] == "AX-D1" and c[2] == (0, 1)
    )
    stricter_deltas = [c[3] for c in ordering_stricter if c[0] == "AX-D1" and c[2] == (0, 1)]
    assert default_max_delta == 0.75
    assert stricter_deltas == [0.25]


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
    neighbors = cp.neighbors_of(None, ordering, catalog=CATALOG)
    for cand in neighbors:
        if cand[0] == "AX-P1":
            assert abs(abs(cand[2]) - 0.5) < 1e-9
        else:
            assert abs(cand[3] - 0.25) < 1e-9
    assert neighbors == sorted(neighbors)


def test_neighbors_of_deterministic_across_two_calls() -> None:
    ordering = _build_l()
    current_best = ("AX-P1", 1, 1.0)
    n1 = cp.neighbors_of(current_best, ordering, catalog=CATALOG)
    n2 = cp.neighbors_of(current_best, ordering, catalog=CATALOG)
    assert n1 == n2


def test_neighbors_of_ax_p1_interior_yields_priority_ordered_value_and_index_neighbors() -> None:
    # note_index=1（3-note fixture の中央、index 端ではない）・v=1.0
    # （domain 内部、range 端ではない）: 値キー±1/±2ステップの3件
    # （1.5/0.5/2.0。-1.0 側は 0.0 で domain 除外）+ 隣接 note_index 0/2 の
    # 同値候補2件が優先順位どおりに列挙される。
    ordering = _build_l()
    current_best = ("AX-P1", 1, 1.0)
    neighbors = cp.neighbors_of(current_best, ordering, catalog=CATALOG)
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
    neighbors = cp.neighbors_of(current_best, ordering, catalog=CATALOG)
    assert neighbors == [
        ("AX-P1", 1, 1.5),
        ("AX-P1", 1, 1.0),
        ("AX-P1", 0, 2.0),
        ("AX-P1", 2, 2.0),
    ]
    selected = cp.select_neighborhood_candidates(
        current_best, ordering, is_evaluated=lambda c: False, catalog=CATALOG, limit=3
    )
    assert len(selected) == 3


def test_neighbors_of_ax_p1_index_boundary_alone_still_achieves_three() -> None:
    # note_index=0（index 端）だが v=1.0 は range 端ではないため、値キー側
    # の±1/±2ステップだけで3件が確保できる（隣接 index キー側の不足は
    # index=-1 が存在しないため item5 のみ欠けるが item6 が補う）。
    ordering = _build_l()
    current_best = ("AX-P1", 0, 1.0)
    neighbors = cp.neighbors_of(current_best, ordering, catalog=CATALOG)
    assert neighbors == [
        ("AX-P1", 0, 1.5),
        ("AX-P1", 0, 0.5),
        ("AX-P1", 0, 2.0),
        ("AX-P1", 1, 1.0),
    ]
    selected = cp.select_neighborhood_candidates(
        current_best, ordering, is_evaluated=lambda c: False, catalog=CATALOG, limit=3
    )
    assert len(selected) == 3


def test_neighbors_of_ax_p1_double_endpoint_shortfall() -> None:
    # 端点の例外: note_count=1（index 端）かつ v=2.0（range 端）が同時に
    # 揃う場合のみ、優先順位リスト6項目を尽くしても3件に満たない
    # （item5/6 は index±1 が存在せず不適用、item1/3 は range 外）。
    single_note_ordering = cp.build_candidate_ordering(
        note_count=1, phrase_of_note=[0], original_duration_beats=[1.0], catalog=CATALOG
    )
    current_best = ("AX-P1", 0, 2.0)
    neighbors = cp.neighbors_of(current_best, single_note_ordering, catalog=CATALOG)
    assert neighbors == [("AX-P1", 0, 1.5), ("AX-P1", 0, 1.0)]
    assert len(neighbors) < 3
    selected = cp.select_neighborhood_candidates(
        current_best, single_note_ordering, is_evaluated=lambda c: False, catalog=CATALOG, limit=3
    )
    assert len(selected) == 2  # shortfall: 呼び出し側が exploratory 規則で1件補充する


def test_neighbors_of_ax_d1_candidate_are_adjacent_grid_values() -> None:
    # 隣接 index キー（(0,(0,2))）の同値候補が優先順位リスト項目6として
    # 値キー±1ステップの2件を補い、3件ちょうどに達する。
    ordering = _build_l()
    current_best = ("AX-D1", 0, (0, 1), 0.5)
    neighbors = cp.neighbors_of(current_best, ordering, catalog=CATALOG)
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
    neighbors = cp.neighbors_of(current_best, ordering, catalog=CATALOG)
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
        note_count=2, phrase_of_note=[0, 0], original_duration_beats=[0.5, 0.25], catalog=CATALOG
    )
    ax_d1_only = [c for c in sole_pair_ordering if c[0] == "AX-D1"]
    assert ax_d1_only == [("AX-D1", 0, (0, 1), 0.25)]
    current_best = ("AX-D1", 0, (0, 1), 0.25)
    neighbors = cp.neighbors_of(current_best, sole_pair_ordering, catalog=CATALOG)
    assert neighbors == []
    selected = cp.select_neighborhood_candidates(
        current_best, sole_pair_ordering, is_evaluated=lambda c: False, catalog=CATALOG, limit=3
    )
    assert selected == []


def test_select_neighborhood_candidates_limits_to_three_and_skips_evaluated() -> None:
    ordering = _build_l()
    evaluated = {("AX-P1", 0, 0.5)}
    selected = cp.select_neighborhood_candidates(
        None, ordering, is_evaluated=lambda c: c in evaluated, catalog=CATALOG, limit=3
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
        current_best, ordering, is_evaluated=lambda c: False, catalog=CATALOG, limit=3
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
        current_best, ordering, is_evaluated=lambda c: False, catalog=CATALOG, limit=3
    )
    assert len(selected_none_evaluated) == 3
    selected_all_evaluated = cp.select_neighborhood_candidates(
        current_best, ordering, is_evaluated=lambda c: True, catalog=CATALOG, limit=3
    )
    assert selected_all_evaluated == []


# ---------------------------------------------------------------------------
# 5. candidate_ordinal / tie-break の全順序
#
# PR #331 Codex bot レビュー第4巡指摘2（P1「tie-break の実行可能な全順序
# 凍結」、採用）: 旧 tie_break「(objective, 軸ベクトルの辞書順)」は座標順・
# 表現・恒等候補の位置が未定義だった。`candidate_ordinal()`（恒等=-1、
# 非恒等候補=L 内インデックス）を tie-break キーの第2要素として凍結した。
# ---------------------------------------------------------------------------


def test_candidate_ordinal_identity_is_minus_one() -> None:
    ordering = _build_l()
    assert cp.candidate_ordinal(None, ordering) == -1


def test_candidate_ordinal_matches_l_index_for_every_non_identity_candidate() -> None:
    ordering = _build_l()
    for expected_idx, cand in enumerate(ordering):
        assert cp.candidate_ordinal(cand, ordering) == expected_idx


def test_candidate_ordinal_deterministic_across_two_calls() -> None:
    ordering = _build_l()
    cand = ordering[5]
    assert cp.candidate_ordinal(cand, ordering) == cp.candidate_ordinal(cand, ordering)


def test_candidate_ordinal_raises_for_candidate_not_in_l() -> None:
    ordering = _build_l()
    bogus = ("AX-P1", 999, 0.5)
    try:
        cp.candidate_ordinal(bogus, ordering)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a candidate absent from L")


def test_tie_break_identity_beats_any_non_identity_at_equal_objective() -> None:
    # 恒等候補（ordinal=-1）は L のどの要素（ordinal>=0）よりも tie-break で
    # 常に勝つ。
    ordering = _build_l()
    non_identity = ordering[-1]
    identity_key = (0.5, cp.candidate_ordinal(None, ordering))
    non_identity_key = (0.5, cp.candidate_ordinal(non_identity, ordering))
    assert identity_key < non_identity_key


def test_tie_break_ax_d1_beats_ax_p1_at_equal_objective() -> None:
    # L の total_order で "AX-D1" < "AX-P1" のため AX-D1 群は常に AX-P1 群
    # より小さい ordinal を持ち、同 objective では AX-D1 側が tie-break で
    # 勝つ。
    ordering = _build_l()
    ax_d1_cand = next(c for c in ordering if c[0] == "AX-D1")
    ax_p1_cand = next(c for c in ordering if c[0] == "AX-P1")
    key_d1 = (1.0, cp.candidate_ordinal(ax_d1_cand, ordering))
    key_p1 = (1.0, cp.candidate_ordinal(ax_p1_cand, ordering))
    assert key_d1 < key_p1


def test_tie_break_earlier_l_position_wins_within_same_axis_at_equal_objective() -> None:
    ordering = _build_l()
    ax_p1_candidates = [c for c in ordering if c[0] == "AX-P1"]
    first, second = ax_p1_candidates[0], ax_p1_candidates[1]
    key_first = (2.0, cp.candidate_ordinal(first, ordering))
    key_second = (2.0, cp.candidate_ordinal(second, ordering))
    assert key_first < key_second


# ---------------------------------------------------------------------------
# 6. ax_p1_offset_domain_from_catalog（PR #331 第8巡指摘3、P2、採用: 公開
# ラッパー。run9_schema.load_pinned_candidate_generation_spec_manifest()
# の catalog↔spec cross-check が単一情報源として使う）。
# ---------------------------------------------------------------------------


def test_ax_p1_offset_domain_from_catalog_matches_private_helper() -> None:
    assert cp.ax_p1_offset_domain_from_catalog(CATALOG) == cp._ax_p1_offset_domain(CATALOG)  # noqa: SLF001


def test_ax_p1_offset_domain_from_catalog_matches_expected_fixture() -> None:
    assert cp.ax_p1_offset_domain_from_catalog(CATALOG) == EXPECTED_AX_P1_OFFSET_DOMAIN


def test_ax_p1_offset_domain_from_catalog_reflects_narrowed_catalog() -> None:
    narrowed = json.loads(json.dumps(CATALOG))
    narrowed["axes"]["AX-P1"]["range_semitones"] = [-1.0, 1.0]
    assert cp.ax_p1_offset_domain_from_catalog(narrowed) == (-1.0, -0.5, 0.5, 1.0)


# ---------------------------------------------------------------------------
# 7. require_sufficient_candidate_space（PR #331 第8巡指摘1、P2、採用:
# undersized L の run 前拒否ゲート）。
# ---------------------------------------------------------------------------


def test_require_sufficient_candidate_space_passes_when_l_meets_minimum() -> None:
    candidates = [("AX-P1", i, 0.5) for i in range(127)]
    cp.require_sufficient_candidate_space(candidates, render_budget=128)  # must not raise


def test_require_sufficient_candidate_space_passes_when_l_exceeds_minimum() -> None:
    ordering = _build_l()  # 32 要素 > 127 は満たさないが、render_budget を
    # 小さく設定すれば「十分」なケースとして検査できる。
    cp.require_sufficient_candidate_space(ordering, render_budget=len(ordering) + 1)


def test_require_sufficient_candidate_space_rejects_undersized_l() -> None:
    ordering = _build_l()  # 32 要素
    assert len(ordering) == 32
    with pytest.raises(ValueError, match="required minimum"):
        cp.require_sufficient_candidate_space(ordering, render_budget=128)  # 127 required, 32 < 127


def test_require_sufficient_candidate_space_boundary_exactly_at_minimum_passes() -> None:
    candidates = [("AX-P1", i, 0.5) for i in range(127)]
    cp.require_sufficient_candidate_space(candidates, render_budget=128)  # 127 == 127, must not raise


def test_require_sufficient_candidate_space_boundary_one_below_minimum_rejects() -> None:
    candidates = [("AX-P1", i, 0.5) for i in range(126)]
    with pytest.raises(ValueError):
        cp.require_sufficient_candidate_space(candidates, render_budget=128)  # 126 < 127


# ---------------------------------------------------------------------------
# 8. propose_trial_candidates（PR #331 第8巡指摘2、P1、採用: 同一 trial
# 内の予約集合の凍結、trial-level 参照実装）。
# ---------------------------------------------------------------------------


def test_propose_trial_candidates_trial1_returns_identity_plus_three_exploratory() -> None:
    ordering = _build_l()
    result = cp.propose_trial_candidates(
        trial=1,
        current_best=None,
        all_candidates=ordering,
        catalog=CATALOG,
        seed=909002,
        arm="arm-a",
        founder_id="R9F-01",
        reserved=set(),
    )
    assert len(result) == 4
    assert result[0] is None  # trial1_candidate0_rule: 恒等
    assert all(c is not None for c in result[1:])
    assert len(set(result[1:])) == 3  # 重複なし


def test_propose_trial_candidates_trial2_plus_achieves_three_neighborhood_one_exploratory() -> None:
    ordering = _build_l()
    current_best = ("AX-P1", 1, 1.0)  # 内部領域: 近傍3枠を構造的に満たす
    result = cp.propose_trial_candidates(
        trial=5,
        current_best=current_best,
        all_candidates=ordering,
        catalog=CATALOG,
        seed=909002,
        arm="arm-a",
        founder_id="R9F-01",
        reserved=set(),
    )
    assert len(result) == 4
    assert result[:3] == [
        ("AX-P1", 1, 1.5),
        ("AX-P1", 1, 0.5),
        ("AX-P1", 1, 2.0),
    ]  # neighbors_of() の優先順位どおり
    assert result[3] is not None
    assert result[3] not in result[:3]


def test_propose_trial_candidates_deterministic_across_two_calls() -> None:
    ordering = _build_l()
    current_best = ("AX-P1", 1, 1.0)
    kwargs = dict(
        trial=5, current_best=current_best, all_candidates=ordering, catalog=CATALOG,
        seed=909002, arm="arm-a", founder_id="R9F-01", reserved=set(),
    )
    r1 = cp.propose_trial_candidates(**kwargs)
    r2 = cp.propose_trial_candidates(**kwargs)
    assert r1 == r2


def test_propose_trial_candidates_respects_reserved_from_past_trials() -> None:
    ordering = _build_l()
    current_best = ("AX-P1", 1, 1.0)
    reserved = {("AX-P1", 1, 1.5)}  # 過去 trial で既に評価済みとする
    result = cp.propose_trial_candidates(
        trial=5, current_best=current_best, all_candidates=ordering, catalog=CATALOG,
        seed=909002, arm="arm-a", founder_id="R9F-01", reserved=reserved,
    )
    assert ("AX-P1", 1, 1.5) not in result
    assert len(set(c for c in result if c is not None)) == len([c for c in result if c is not None])


def test_propose_trial_candidates_shortfall_backfilled_by_exploratory() -> None:
    # note_count=1・v=2.0（値キー・index キー両方が端点）は近傍0件シナリオ
    # ではないが少数（test_neighbors_of_ax_p1_double_endpoint_shortfall と
    # 同型の fixture）: 近傍が2件しかないため candidate_index=2 は探査規則
    # で補充されるはず。
    single_note_ordering = cp.build_candidate_ordering(
        note_count=1, phrase_of_note=[0], original_duration_beats=[1.0], catalog=CATALOG
    )
    current_best = ("AX-P1", 0, 2.0)
    neighbors = cp.neighbors_of(current_best, single_note_ordering, catalog=CATALOG)
    assert len(neighbors) == 2  # shortfall: 3枠中2件のみ
    result = cp.propose_trial_candidates(
        trial=5, current_best=current_best, all_candidates=single_note_ordering, catalog=CATALOG,
        seed=909002, arm="arm-a", founder_id="R9F-01", reserved=set(),
    )
    assert len(result) == 4
    assert result[0] in neighbors
    assert result[1] in neighbors
    # candidate_index=2 は近傍が尽きたため探査規則で補充される（None も
    # あり得る = 全滅・NOT_PROPOSABLE だが、この小さな L では発生しない
    # ことをここで確認する）。
    assert result[2] is not None
    assert len(set(result)) == 4  # 重複なし（None を含む場合も等価判定される）


def test_propose_trial_candidates_no_duplicate_when_exploratory_digest_collides_with_neighbor() -> None:
    # trial=26/candidate_index=3 の探査 digest は、この 3-note fixture・
    # current_best=("AX-P1",1,1.0)・seed=909002/arm="arm-a"/
    # founder_id="R9F-01" の下で初期 index が近傍優先順位リスト先頭
    # （("AX-P1",1,1.5)）と衝突する（brute force で確認済みの固定値）。
    # 予約集合 semantics がなければこの衝突がそのまま重複選出され得る
    # ——PR #331 第8巡指摘2 が修正する具体的な不具合の直接証跡。
    ordering = _build_l()
    current_best = ("AX-P1", 1, 1.0)
    neighbors = cp.select_neighborhood_candidates(
        current_best, ordering, is_evaluated=lambda c: False, catalog=CATALOG, limit=3
    )
    collision_target = neighbors[0]
    digest = cp.digest_bytes(909002, "arm-a", "R9F-01", 26, 3)
    start = cp.digest_to_index(digest, list_length=len(ordering))
    assert ordering[start] == collision_target  # 事前計算した衝突が現行 L でも成立することを確認

    # 旧「評価済みのみ」semantics（何も評価済みでないため is_acceptable は
    # 常に True）を模すと、探査プロービングの初期 index がそのまま
    # collision_target を返す——これが「予約集合なしでは重複が起こる」
    # ことの直接証拠。
    old_semantics_pick = cp.select_exploratory_candidate(
        ordering, seed=909002, arm="arm-a", founder_id="R9F-01", trial=26, candidate_index=3,
        is_acceptable=lambda c: True,
    )
    assert old_semantics_pick == collision_target

    result = cp.propose_trial_candidates(
        trial=26, current_best=current_best, all_candidates=ordering, catalog=CATALOG,
        seed=909002, arm="arm-a", founder_id="R9F-01", reserved=set(),
    )
    assert result[:3] == neighbors
    assert result[3] != collision_target  # 予約集合が衝突を検出し次候補へプロービング
    assert len(set(result)) == 4  # 重複なし


def test_propose_trial_candidates_batch_matches_manual_sequential_simulation() -> None:
    # batch（propose_trial_candidates() を1回呼ぶ）と、呼び出し側が
    # candidate_index ごとに render/評価してから次候補を計算する逐次
    # シミュレーション（同一の予約集合更新規則を手動で適用）が常に同一の
    # 結果を生成することを確認する（PR #331 第8巡指摘2 の核心要求）。
    ordering = _build_l()
    current_best = ("AX-P1", 1, 1.0)
    seed, arm, founder_id, trial = 909002, "arm-a", "R9F-01", 26  # 衝突が起きる trial

    batch_result = cp.propose_trial_candidates(
        trial=trial, current_best=current_best, all_candidates=ordering, catalog=CATALOG,
        seed=seed, arm=arm, founder_id=founder_id, reserved=set(),
    )

    reserved: set = set()
    manual_result = []
    neighbor_queue = list(cp.neighbors_of(current_best, ordering, catalog=CATALOG))
    for candidate_index in range(4):
        if candidate_index < 3:
            while neighbor_queue and neighbor_queue[0] in reserved:
                neighbor_queue.pop(0)
            if neighbor_queue:
                cand = neighbor_queue.pop(0)
                manual_result.append(cand)
                reserved.add(cand)
                continue
        cand = cp.select_exploratory_candidate(
            ordering, seed=seed, arm=arm, founder_id=founder_id, trial=trial,
            candidate_index=candidate_index, is_acceptable=lambda c: c not in reserved,
        )
        manual_result.append(cand)
        if cand is not None:
            reserved.add(cand)

    assert manual_result == batch_result


def test_propose_trial_candidates_does_not_mutate_caller_reserved_set() -> None:
    ordering = _build_l()
    reserved = {("AX-P1", 0, 0.5)}
    reserved_copy = set(reserved)
    cp.propose_trial_candidates(
        trial=1, current_best=None, all_candidates=ordering, catalog=CATALOG,
        seed=909002, arm="arm-a", founder_id="R9F-01", reserved=reserved,
    )
    assert reserved == reserved_copy


# ---------------------------------------------------------------------------
# 9. NO_BEST sentinel（PR #331 Codex bot レビュー第11巡指摘2、P1、採用:
# best 不在と恒等候補の混同の是正）。
#
# `missing_policy` は「trial 内の全 candidate（恒等を含む）が NOT_SCORABLE
# の場合、best 更新なしで次 trial へ進む」ことを規定する。旧実装は
# `current_best: Optional[Candidate]` の `None` を恒等（identity）専用の
# 意味で使っており、「best が一度も確定していない」状態を表す別個の値が
# 存在しなかった。もし呼び出し側がこの状態を（誤って）`None` で表現すると、
# `neighbors_of()` はそれを恒等 best と取り違え、実際には一度も scorable
# と確認されていない恒等の近傍を生成してしまう——架空の best からの探索と
# いう偽成功経路。以下は、この区別が `NO_BEST` によって構造的に閉じられて
# いることを検証する（バグ再現: None のままでは identity_neighbor_rule が
# 発火してしまうこと自体は意図された IDENTITY 挙動として残るため、
# 「None を no-best の代用にしてはならない」契約を明示する形で検証する）。
# ---------------------------------------------------------------------------


def test_neighbors_of_none_still_means_identity_not_absence() -> None:
    # IDENTITY（None）の意味は本改訂で変えない: None は「恒等が正当な
    # best として確定している」ことを表す積極的な値であり、identity_
    # neighbor_rule が発火して非空の近傍集合を返す。
    ordering = _build_l()
    neighbors = cp.neighbors_of(None, ordering, catalog=CATALOG)
    assert len(neighbors) > 0


def test_neighbors_of_no_best_returns_empty_list_not_identity_neighbors() -> None:
    # バグ再現の直接証跡: current_best=NO_BEST（best 不在）は
    # current_best=None（恒等が best）と区別され、identity_neighbor_rule
    # を発火させず空リストを返す——None との取り違えがあれば本テストは
    # test_neighbors_of_none_still_means_identity_not_absence と同じ非空
    # 結果になり失敗する。
    ordering = _build_l()
    neighbors = cp.neighbors_of(cp.NO_BEST, ordering, catalog=CATALOG)
    assert neighbors == []


def test_no_best_is_not_none() -> None:
    assert cp.NO_BEST is not None
    assert cp.NO_BEST != None  # noqa: E711 - 明示的に == でも区別されることを確認する
    assert bool(cp.NO_BEST) is False


def test_select_neighborhood_candidates_no_best_returns_empty() -> None:
    ordering = _build_l()
    selected = cp.select_neighborhood_candidates(
        cp.NO_BEST, ordering, is_evaluated=lambda c: False, catalog=CATALOG, limit=3
    )
    assert selected == []


def test_propose_trial_candidates_no_best_backfills_all_four_via_exploratory() -> None:
    # NO_BEST（trial 1 の恒等候補を含む全4候補が NOT_SCORABLE だった等で
    # best が一度も確定していない状態）を trial>=2 の current_best として
    # 渡すと、近傍3スロットが構造的に全欠となり、candidate 0..3 の4枠
    # すべてが exploratory_candidate_rule（探査ストリーム）で決定論的に
    # 充当される——恒等近傍を発明しない（第11巡指摘2、P1、採用）。
    ordering = _build_l()
    trial, seed, arm, founder_id = 5, 909002, "arm-a", "R9F-01"

    result = cp.propose_trial_candidates(
        trial=trial, current_best=cp.NO_BEST, all_candidates=ordering, catalog=CATALOG,
        seed=seed, arm=arm, founder_id=founder_id, reserved=set(),
    )
    assert len(result) == 4

    # 手動シミュレーション: neighbors_of(NO_BEST) は空のため、4枠すべてが
    # select_exploratory_candidate() の逐次プロービングで決定論的に決まる
    # はず。
    reserved: set = set()
    manual_result = []
    for candidate_index in range(4):
        cand = cp.select_exploratory_candidate(
            ordering, seed=seed, arm=arm, founder_id=founder_id, trial=trial,
            candidate_index=candidate_index, is_acceptable=lambda c: c not in reserved,
        )
        manual_result.append(cand)
        if cand is not None:
            reserved.add(cand)
    assert result == manual_result
    assert len(set(result)) == 4  # 重複なし


def test_propose_trial_candidates_no_best_deterministic_across_two_calls() -> None:
    ordering = _build_l()
    kwargs = dict(
        trial=7, current_best=cp.NO_BEST, all_candidates=ordering, catalog=CATALOG,
        seed=909002, arm="arm-b", founder_id="R9F-02", reserved=set(),
    )
    r1 = cp.propose_trial_candidates(**kwargs)
    r2 = cp.propose_trial_candidates(**kwargs)
    assert r1 == r2


def test_propose_trial_candidates_no_best_never_revives_identity_across_full_run() -> None:
    # NO_SCORABLE_CANDIDATE 終端状態（candidate_generation_spec_v1.json
    # selection.no_scorable_candidate_terminal_state）のモジュールレベル
    # 直接検証: 全 trial で全 candidate が NOT_SCORABLE のまま 32 trial
    # 完走するシナリオを模擬する（current_best は trial 1 の恒等提示後、
    # 一度も scorable な candidate が確定しないため NO_BEST のまま推移する
    # ——missing_policy「trial 内の全 candidate が NOT_SCORABLE の場合、
    # best 更新なしで次 trial へ進む」を反映）。恒等（None）は
    # trial1_candidate0_rule が定める trial 1 candidate 0 の1回のみ現れ、
    # それ以降のいかなる trial でも NO_BEST から暗黙に再提案されないこと
    # を確認する（第11巡指摘2、P1、採用の核心要求: 勝者なし・暗黙の恒等
    # 採用なし）。
    # 3-note fixture（32要素）は32 trial×4 candidates=128スロットに対して
    # 枯渇（NOT_PROPOSABLE）が構造的に起こり得るため、本テストでは
    # require_sufficient_candidate_space() の必要最小値（127）を満たす
    # 十分に大きな L（各 note が単独 phrase で AX-D1 候補を生まない、
    # AX-P1 のみ 17 note × 8 offset = 136 要素）を使い、NOT_PROPOSABLE と
    # 恒等復活を混同せず「恒等は trial 1 のみ」を厳密に検証できるように
    # する。
    large_ordering = cp.build_candidate_ordering(
        note_count=17,
        phrase_of_note=list(range(17)),  # 各 note が単独 phrase = AX-D1 候補なし
        original_duration_beats=[1.0] * 17,
        catalog=CATALOG,
    )
    assert len(large_ordering) == 136
    cp.require_sufficient_candidate_space(large_ordering, render_budget=128)  # must not raise

    seed, arm, founder_id = 909002, "arm-a", "R9F-01"
    reserved: set = set()
    identity_count = 0

    for trial in range(1, 33):
        current_best = None if trial == 1 else cp.NO_BEST
        result = cp.propose_trial_candidates(
            trial=trial, current_best=current_best, all_candidates=large_ordering,
            catalog=CATALOG, seed=seed, arm=arm, founder_id=founder_id, reserved=reserved,
        )
        assert len(result) == 4
        for candidate_index, cand in enumerate(result):
            if cand is None:
                assert trial == 1 and candidate_index == 0, (
                    f"identity (None) reappeared at trial={trial} candidate_index="
                    f"{candidate_index} — NO_BEST must never implicitly revive the identity "
                    "candidate, and L is sized to avoid NOT_PROPOSABLE in this scenario"
                )
                identity_count += 1
            else:
                reserved.add(cand)

    # 恒等は trial 1 candidate 0 で必ず1回だけ現れる（trial1_candidate0_
    # rule）。それ以外の None はループ内 assert で既に構造的に排除済み。
    assert identity_count == 1
