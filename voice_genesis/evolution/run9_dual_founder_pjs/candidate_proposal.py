"""candidate_proposal.py — RUN9 HARNESS-3c: `candidate_generation_spec_v1.json`
`proposal` 節（candidate_ordering / exploratory_candidate_rule /
neighborhood_candidate_rule）の byte レベル参照実装。

設計根拠: PR #331 Codex bot レビュー第2巡指摘1（P1「Specify the complete
digest-to-candidate mapping」、採用）。`inputs/candidate_generation_spec_v1.json`
`proposal` 節が凍結する、digest のどのバイトが軸・note/phrase・delta/offset
値を選ぶか、および「近傍」の全順序を、実装が選べる余地なく機械的に再現する。

**スコープ境界**: 本モジュールは spec の「候補列 L の構築」「digest→候補の
写像」「近傍列挙」という *決定論的で generator 非依存な部分* のみを実装
する。実際の探索ループ（PRACTICE actor 内での候補適用・render・loss
評価・trial を跨いだ best 更新・trace 保存）は本モジュールの対象外であり
別途配線する（レビュー指摘原文が述べる「次の PR で generator を実装する」
の対象）。本モジュールはその generator が満たすべき写像を曖昧さなく検査
可能にするための正本実装として提供する。

**catalog 消費**: offset/delta の量子化刻み・range・min-duration は
`score_axis_catalog_v1.json` の値をハードコードした定数として持つ
（AX-P1: range=[-2.0,2.0]・step=0.5、AX-D1: step=0.25・min_duration=0.25 —
`inputs/candidate_generation_spec_v1.json` `proposal.candidate_ordering` が
これらの値を逐語で凍結しており、catalog 側の値と一致することは
`run9_schema.py` の cross-check が別途保証する）。
"""
from __future__ import annotations

import hashlib
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

# AX-P1: 0 除外の8グリッド値（score_axis_catalog_v1.json axes.AX-P1
# range_semitones=[-2.0,2.0] / quantization_step_semitones=0.5 から、0
# を除いた値。0 は単一軸候補としては非変換であり L に含めない）。
AX_P1_OFFSET_DOMAIN: Tuple[float, ...] = (-2.0, -1.5, -1.0, -0.5, 0.5, 1.0, 1.5, 2.0)

AX_P1_QUANTIZATION_STEP = 0.5
AX_D1_QUANTIZATION_STEP = 0.25
AX_D1_MIN_DURATION_BEATS = 0.25

# L の要素型: AX-P1 = ("AX-P1", note_index, offset)
#             AX-D1 = ("AX-D1", phrase_index, (i, j), delta)
Candidate = Tuple[Union[str, int, float, Tuple[int, int]], ...]


def _round_grid(value: float, *, ndigits: int = 10) -> float:
    """浮動小数の累積誤差を格子値の比較に影響させないための丸め
    （量子化格子上の値であることは呼び出し側が担保する——本関数は表示・
    比較用の丸めのみ行う）。"""
    return round(value, ndigits)


def build_ax_p1_ordering(note_count: int) -> List[Candidate]:
    """AX-P1 の単一軸候補を `(axis_id, note_index, offset)` の辞書順
    （note_index 昇順 → offset 昇順）で列挙する。"""
    if note_count < 0:
        raise ValueError(f"note_count must be >= 0, got {note_count}")
    return [
        ("AX-P1", note_index, offset)
        for note_index in range(note_count)
        for offset in AX_P1_OFFSET_DOMAIN
    ]


def build_ax_d1_ordering(
    phrase_of_note: Sequence[int],
    original_duration_beats: Sequence[float],
    *,
    quantization_step_beats: float = AX_D1_QUANTIZATION_STEP,
    min_duration_beats: float = AX_D1_MIN_DURATION_BEATS,
) -> List[Candidate]:
    """AX-D1 の単一軸候補を `(axis_id, phrase_index, (i, j), delta)` の
    辞書順（phrase_index 昇順 → i 昇順 → j 昇順 → delta 昇順）で列挙する。

    delta は note i（失う側）から note j（得る側）への移動量。delta の
    上限は「note i の変換後 duration が min_duration_beats 以上」を満たす
    最大の量子化格子値（0.25 刻み）。この上限を満たす delta が存在しない
    （original_duration_beats[i] < 2 * min_duration_beats の場合など）
    (i, j) ペアは候補を生まない。
    """
    if len(phrase_of_note) != len(original_duration_beats):
        raise ValueError(
            "phrase_of_note と original_duration_beats の長さが一致しない "
            f"({len(phrase_of_note)} != {len(original_duration_beats)})"
        )
    phrases: Dict[int, List[int]] = {}
    for note_index, phrase_index in enumerate(phrase_of_note):
        phrases.setdefault(phrase_index, []).append(note_index)

    ordering: List[Candidate] = []
    for phrase_index in sorted(phrases):
        note_indices = sorted(phrases[phrase_index])
        for i in note_indices:
            for j in note_indices:
                if i == j:
                    continue
                max_steps = int(
                    (original_duration_beats[i] - min_duration_beats + 1e-9)
                    // quantization_step_beats
                )
                for step in range(1, max_steps + 1):
                    delta = _round_grid(step * quantization_step_beats)
                    ordering.append(("AX-D1", phrase_index, (i, j), delta))
    return ordering


def build_candidate_ordering(
    *,
    note_count: int,
    phrase_of_note: Sequence[int],
    original_duration_beats: Sequence[float],
    quantization_step_beats: float = AX_D1_QUANTIZATION_STEP,
    min_duration_beats: float = AX_D1_MIN_DURATION_BEATS,
) -> List[Candidate]:
    """正準候補列 L（`candidate_generation_spec_v1.json`
    `proposal.candidate_ordering` の凍結定義）を、total_order（タプル
    比較の辞書順。axis_id 文字列比較で "AX-D1" < "AX-P1" のため AX-D1 群が
    先に並ぶ）で構築する。"""
    ax_d1 = build_ax_d1_ordering(
        phrase_of_note,
        original_duration_beats,
        quantization_step_beats=quantization_step_beats,
        min_duration_beats=min_duration_beats,
    )
    ax_p1 = build_ax_p1_ordering(note_count)
    ordering = ax_d1 + ax_p1
    ordering.sort()
    return ordering


# ---------------------------------------------------------------------------
# digest -> index 写像 + 探査候補の線形プロービング
# ---------------------------------------------------------------------------


def digest_bytes(seed: int, arm: str, founder_id: str, trial: int, candidate: int) -> bytes:
    """`digest_formula`（`digest = sha256(UTF-8(f"{seed}:{arm}:{founder_id}:
    {trial}:{candidate}"))`）の逐語実装。"""
    template = f"{seed}:{arm}:{founder_id}:{trial}:{candidate}"
    return hashlib.sha256(template.encode("utf-8")).digest()


def digest_to_index(digest: bytes, *, list_length: int) -> int:
    """digest の先頭8バイトを big-endian uint64 として解釈し、
    `idx = u mod len(L)` を返す。"""
    if list_length <= 0:
        raise ValueError(f"list_length must be positive, got {list_length}")
    u = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return u % list_length


def select_exploratory_candidate(
    candidates: Sequence[Candidate],
    *,
    seed: int,
    arm: str,
    founder_id: str,
    trial: int,
    candidate_index: int,
    is_acceptable: Callable[[Candidate], bool],
) -> Optional[Candidate]:
    """`exploratory_candidate_rule` の逐語実装: digest 由来の初期 index から
    決定論線形プロービングで最初の未評価かつ有効な候補を返す。全滅時は
    None（NOT_PROPOSABLE を表す）を返す——架空の候補を発明しない。

    `is_acceptable(candidate)` は「未評価かつ catalog 制約を満たす」ことを
    呼び出し側が判定する述語（本モジュールは探索状態を保持しない）。
    """
    n = len(candidates)
    if n == 0:
        return None
    start = digest_to_index(
        digest_bytes(seed, arm, founder_id, trial, candidate_index), list_length=n
    )
    for step in range(n):
        idx = (start + step) % n
        cand = candidates[idx]
        if is_acceptable(cand):
            return cand
    return None


# ---------------------------------------------------------------------------
# 近傍候補列挙
# ---------------------------------------------------------------------------

# 恒等候補（全軸 0）を表すセンチネル。L の要素ではない。
IDENTITY: None = None


def neighbors_of(current_best: Optional[Candidate], all_candidates: Sequence[Candidate]) -> List[Candidate]:
    """`neighborhood_candidate_rule` の `neighbor_value_perturbation` /
    `identity_neighbor_rule` の逐語実装: 現 best からちょうど1量子化ステップ
    だけ値キーが異なる L の要素を、L と同じ辞書順で返す（存在しない値は
    単に含めない）。"""
    candidate_set = set(all_candidates)
    neighbors: List[Candidate] = []

    if current_best is None:
        # identity_neighbor_rule: 恒等からちょうど1量子化ステップの候補。
        for cand in all_candidates:
            if cand[0] == "AX-P1":
                _, _note_index, offset = cand
                if abs(abs(offset) - AX_P1_QUANTIZATION_STEP) < 1e-9:
                    neighbors.append(cand)
            elif cand[0] == "AX-D1":
                _, _phrase_index, _pair, delta = cand
                if abs(delta - AX_D1_QUANTIZATION_STEP) < 1e-9:
                    neighbors.append(cand)
        neighbors.sort()
        return neighbors

    axis_id = current_best[0]
    if axis_id == "AX-P1":
        _, note_index, offset = current_best
        for step in (-AX_P1_QUANTIZATION_STEP, AX_P1_QUANTIZATION_STEP):
            cand = ("AX-P1", note_index, _round_grid(offset + step))
            if cand in candidate_set:
                neighbors.append(cand)
    elif axis_id == "AX-D1":
        _, phrase_index, pair, delta = current_best
        for step in (-AX_D1_QUANTIZATION_STEP, AX_D1_QUANTIZATION_STEP):
            cand = ("AX-D1", phrase_index, pair, _round_grid(delta + step))
            if cand in candidate_set:
                neighbors.append(cand)
    else:
        raise ValueError(f"unknown axis_id in current_best: {axis_id!r}")

    neighbors.sort()
    return neighbors


def select_neighborhood_candidates(
    current_best: Optional[Candidate],
    all_candidates: Sequence[Candidate],
    *,
    is_evaluated: Callable[[Candidate], bool],
    limit: int = 3,
) -> List[Candidate]:
    """`enumeration_order` の逐語実装: 近傍候補集合を L の辞書順で列挙し、
    未評価のものを先頭から `limit` 件返す（3件未満の不足分は
    `shortfall_handling` により呼び出し側が `select_exploratory_candidate()`
    で補充する——本関数は補充を行わない）。"""
    neighbors = neighbors_of(current_best, all_candidates)
    selected = [cand for cand in neighbors if not is_evaluated(cand)]
    return selected[:limit]
