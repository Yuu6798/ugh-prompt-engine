"""melody/alignment.py — M3b: ギャップ許容 NW 整列（フレーズ層 + ノート層）と被覆信号。

`representation.py`（M3a）が作る正規化系列を、アフィンギャップ Needleman–Wunsch
（Gotoh 3 行列）でノート層整列し、`phrase_gap_sec` によるフレーズ分割の上でフレーズ層
NW（線形ギャップ）を重ねて対応フレーズを決める。装飾音の挿入・音の削除はギャップとして
吸収し、どこを整列できなかったかを `AlignmentCoverage` として明示する
（`docs/DESIGN_M3_melody_comparator.md` §4）。

決定論: 乱数を用いず、スコア同点時は `traceback_preference`（既定
``["diag", "up", "left"]``）の順で一意に解決する。総合類似度・軸間重み付き平均は
一切算出しない（§8 禁止事項）——それは `comparison.py`（M3c）の、しかも軸別にとどめる
仕事である。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from svp_rpe.melody.observability import MelodyNote
from svp_rpe.melody.representation import M3ComparisonConfig, MelodySequences, build_sequences

__all__ = [
    "NoteAlignment",
    "AlignmentCoverage",
    "PhraseCorrespondence",
    "MelodyAlignmentResult",
    "align_intervals",
    "align_melodies",
]

_NEG_INF = float("-inf")


# --------------------------------------------------------------------------- #
# ノート層: アフィンギャップ NW（Gotoh 3 行列）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class NoteAlignment:
    """整数音程列（``intervals_folded``）上のギャップ許容大域整列 1 件。"""

    pairs: Tuple[Tuple[int, int], ...]  # (A 側 interval index, B 側 interval index) の非ギャップ整列列
    gaps_a: Tuple[int, ...]             # A 側でギャップに落ちた interval index
    gaps_b: Tuple[int, ...]             # B 側でギャップに落ちた interval index
    score: float


def _approx_eq(a: float, b: float) -> bool:
    if a == b:
        return True
    return abs(a - b) < 1e-9


def _select(preference: Sequence[str], candidates: Dict[str, float]) -> str:
    """`candidates` のうち最大値を持つキーを、同点なら `preference` の順で選ぶ。

    NW/Gotoh の traceback における唯一の決定論源。`candidates` は非空である前提。
    """
    best = max(candidates.values())
    for name in preference:
        if name in candidates and _approx_eq(candidates[name], best):
            return name
    raise AssertionError("no candidate matched the maximum score (unreachable)")


def align_intervals(
    seq_a: Sequence[int],
    seq_b: Sequence[int],
    config: M3ComparisonConfig,
) -> NoteAlignment:
    """整数音程列（通常は ``intervals_folded``）のアフィンギャップ大域整列（Gotoh）。

    forward pass で M/Ix/Iy の 3 行列と同時に traceback pointer を記録するため、
    backtrack は再計算なしにポインタを辿るだけで済み、浮動小数点の同点判定を
    forward pass の 1 箇所（`_select`）へ集約できる（決定論の単純さ）。
    """
    match_score = config.alignment.match_score
    mismatch_score = config.alignment.mismatch_score
    gap_open = config.alignment.gap_open
    gap_extend = config.alignment.gap_extend
    preference = config.alignment.traceback_preference

    n, m = len(seq_a), len(seq_b)
    M = [[_NEG_INF] * (m + 1) for _ in range(n + 1)]
    Ix = [[_NEG_INF] * (m + 1) for _ in range(n + 1)]  # "up"（A 側消費・B にギャップ）
    Iy = [[_NEG_INF] * (m + 1) for _ in range(n + 1)]  # "left"（B 側消費・A にギャップ）
    M_ptr: List[List[Optional[str]]] = [[None] * (m + 1) for _ in range(n + 1)]
    Ix_ptr: List[List[Optional[str]]] = [[None] * (m + 1) for _ in range(n + 1)]
    Iy_ptr: List[List[Optional[str]]] = [[None] * (m + 1) for _ in range(n + 1)]

    M[0][0] = 0.0
    for i in range(1, n + 1):
        Ix[i][0] = gap_open + (i - 1) * gap_extend
        Ix_ptr[i][0] = "up" if i > 1 else None
    for j in range(1, m + 1):
        Iy[0][j] = gap_open + (j - 1) * gap_extend
        Iy_ptr[0][j] = "left" if j > 1 else None

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            sub = match_score if seq_a[i - 1] == seq_b[j - 1] else mismatch_score

            diag_candidates = {
                "diag": M[i - 1][j - 1],
                "up": Ix[i - 1][j - 1],
                "left": Iy[i - 1][j - 1],
            }
            diag_state = _select(preference, diag_candidates)
            M[i][j] = diag_candidates[diag_state] + sub
            M_ptr[i][j] = diag_state

            up_candidates = {"diag": M[i - 1][j] + gap_open, "up": Ix[i - 1][j] + gap_extend}
            up_state = _select(preference, up_candidates)
            Ix[i][j] = up_candidates[up_state]
            Ix_ptr[i][j] = up_state

            left_candidates = {"diag": M[i][j - 1] + gap_open, "left": Iy[i][j - 1] + gap_extend}
            left_state = _select(preference, left_candidates)
            Iy[i][j] = left_candidates[left_state]
            Iy_ptr[i][j] = left_state

    final_candidates = {"diag": M[n][m], "up": Ix[n][m], "left": Iy[n][m]}
    state = _select(preference, final_candidates)
    score = final_candidates[state]

    pairs: List[Tuple[int, int]] = []
    gaps_a: List[int] = []
    gaps_b: List[int] = []
    i, j = n, m
    while i > 0 or j > 0:
        if state == "diag":
            pairs.append((i - 1, j - 1))
            state = M_ptr[i][j]
            i, j = i - 1, j - 1
        elif state == "up":
            gaps_a.append(i - 1)
            state = Ix_ptr[i][j]
            i -= 1
        else:
            gaps_b.append(j - 1)
            state = Iy_ptr[i][j]
            j -= 1
    pairs.reverse()
    gaps_a.reverse()
    gaps_b.reverse()
    return NoteAlignment(pairs=tuple(pairs), gaps_a=tuple(gaps_a), gaps_b=tuple(gaps_b), score=score)


# --------------------------------------------------------------------------- #
# フレーズ層: 線形ギャップ NW（substitution = ノート層一致率）
# --------------------------------------------------------------------------- #
def _phrase_similarity_and_alignment(
    phrase_a: Tuple[MelodyNote, ...],
    phrase_b: Tuple[MelodyNote, ...],
    config: M3ComparisonConfig,
) -> Tuple[float, NoteAlignment]:
    """フレーズ対 1 組のノート層整列と類似度 ``matched_columns / max(len_a, len_b)``。

    空 vs 空（単ノートフレーズ同士）は 1.0、空 vs 非空は 0.0（`intervals_folded` は
    単ノートフレーズで空タプルになるため、通常の NW 一致率計算が定義できない縮退
    ケースをここで明示的に処理する）。
    """
    intervals_a = build_sequences(phrase_a, config).intervals_folded
    intervals_b = build_sequences(phrase_b, config).intervals_folded
    len_a, len_b = len(intervals_a), len(intervals_b)
    alignment = align_intervals(intervals_a, intervals_b, config)
    if len_a == 0 and len_b == 0:
        return 1.0, alignment
    if len_a == 0 or len_b == 0:
        return 0.0, alignment
    matched = sum(1 for ia, ib in alignment.pairs if intervals_a[ia] == intervals_b[ib])
    return matched / max(len_a, len_b), alignment


def _phrase_layer_nw(
    phrases_a: Sequence[Tuple[MelodyNote, ...]],
    phrases_b: Sequence[Tuple[MelodyNote, ...]],
    config: M3ComparisonConfig,
) -> Tuple[
    List[Tuple[int, int]],
    List[int],
    List[int],
    Dict[Tuple[int, int], Tuple[float, NoteAlignment]],
]:
    """フレーズ列同士の線形ギャップ NW。substitution はノート層一致率、gap は加点。

    `phrase_gap_score` は事前登録どおり**加点**（ペナルティでなく被覆欠損の記録用）
    として扱う（`docs/DESIGN_M3_melody_comparator.md` §4「未対応フレーズはペナルティ
    でなく被覆の欠損として記録」）。
    """
    na, nb = len(phrases_a), len(phrases_b)
    cache: Dict[Tuple[int, int], Tuple[float, NoteAlignment]] = {}
    for i in range(na):
        for j in range(nb):
            cache[(i, j)] = _phrase_similarity_and_alignment(phrases_a[i], phrases_b[j], config)

    gap_score = config.alignment.phrase_gap_score
    preference = config.alignment.traceback_preference

    S = [[0.0] * (nb + 1) for _ in range(na + 1)]
    ptr: List[List[Optional[str]]] = [[None] * (nb + 1) for _ in range(na + 1)]
    for i in range(1, na + 1):
        S[i][0] = S[i - 1][0] + gap_score
        ptr[i][0] = "up"
    for j in range(1, nb + 1):
        S[0][j] = S[0][j - 1] + gap_score
        ptr[0][j] = "left"

    for i in range(1, na + 1):
        for j in range(1, nb + 1):
            sim, _ = cache[(i - 1, j - 1)]
            candidates = {
                "diag": S[i - 1][j - 1] + sim,
                "up": S[i - 1][j] + gap_score,
                "left": S[i][j - 1] + gap_score,
            }
            state = _select(preference, candidates)
            S[i][j] = candidates[state]
            ptr[i][j] = state

    pairs: List[Tuple[int, int]] = []
    gaps_a: List[int] = []
    gaps_b: List[int] = []
    i, j = na, nb
    while i > 0 or j > 0:
        state = ptr[i][j]
        if state == "diag":
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif state == "up":
            gaps_a.append(i - 1)
            i -= 1
        else:
            gaps_b.append(j - 1)
            j -= 1
    pairs.reverse()
    gaps_a.reverse()
    gaps_b.reverse()
    return pairs, gaps_a, gaps_b, cache


# --------------------------------------------------------------------------- #
# 被覆信号 + 統合エントリ
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PhraseCorrespondence:
    """フレーズ層 NW で対応が成立した（diag）フレーズ対 1 件。"""

    phrase_index_a: int
    phrase_index_b: int
    similarity: float
    note_alignment: NoteAlignment


@dataclass(frozen=True)
class AlignmentCoverage:
    """整列の被覆信号（設計 §4.2）。総合スコアではない——観測事実の記録。"""

    aligned_note_fraction_a: float
    aligned_note_fraction_b: float
    phrase_coverage_a: float
    phrase_coverage_b: float


@dataclass(frozen=True)
class MelodyAlignmentResult:
    """`align_melodies` の戻り値: 対応フレーズ対ごとのノート層整列 + 被覆信号。"""

    phrase_correspondences: Tuple[PhraseCorrespondence, ...]
    unmatched_phrase_indices_a: Tuple[int, ...]
    unmatched_phrase_indices_b: Tuple[int, ...]
    coverage: AlignmentCoverage


def align_melodies(
    seqs_a: MelodySequences,
    seqs_b: MelodySequences,
    phrases_a: Sequence[Tuple[MelodyNote, ...]],
    phrases_b: Sequence[Tuple[MelodyNote, ...]],
    config: M3ComparisonConfig,
) -> MelodyAlignmentResult:
    """フレーズ層対応付け + 対応フレーズ内ノート層整列 + 被覆信号を一括して返す。

    `seqs_a` / `seqs_b` は全体のノート数（被覆分母）を取るためだけに用いる。
    `phrases_a` / `phrases_b` は `representation.split_phrases` の出力（各要素が
    1 フレーズのノート列）で、両者は同じノート列から分割されたものでなければ
    ならない（呼び出し側の責務）。
    """
    pairs, gaps_a_idx, gaps_b_idx, cache = _phrase_layer_nw(phrases_a, phrases_b, config)

    correspondences = tuple(
        PhraseCorrespondence(
            phrase_index_a=pi,
            phrase_index_b=pj,
            similarity=cache[(pi, pj)][0],
            note_alignment=cache[(pi, pj)][1],
        )
        for pi, pj in pairs
    )

    aligned_notes_a: set = set()
    aligned_notes_b: set = set()
    for corr in correspondences:
        phrase_a = phrases_a[corr.phrase_index_a]
        phrase_b = phrases_b[corr.phrase_index_b]
        if len(phrase_a) == 1 and len(phrase_b) == 1:
            # 単ノートフレーズ同士: intervals が空でノート層整列は自明に空になる
            # ため、フレーズ対応成立自体をそのノートの整列根拠として扱う。
            aligned_notes_a.add((corr.phrase_index_a, 0))
            aligned_notes_b.add((corr.phrase_index_b, 0))
            continue
        for ia, ib in corr.note_alignment.pairs:
            # 非ギャップ整列 interval 列 k は、ノート k と k+1 の両方に関与する。
            aligned_notes_a.add((corr.phrase_index_a, ia))
            aligned_notes_a.add((corr.phrase_index_a, ia + 1))
            aligned_notes_b.add((corr.phrase_index_b, ib))
            aligned_notes_b.add((corr.phrase_index_b, ib + 1))

    total_notes_a = len(seqs_a.pitch_semitones)
    total_notes_b = len(seqs_b.pitch_semitones)
    total_phrases_a = len(phrases_a)
    total_phrases_b = len(phrases_b)

    coverage = AlignmentCoverage(
        aligned_note_fraction_a=(
            len(aligned_notes_a) / total_notes_a if total_notes_a > 0 else 0.0
        ),
        aligned_note_fraction_b=(
            len(aligned_notes_b) / total_notes_b if total_notes_b > 0 else 0.0
        ),
        phrase_coverage_a=(
            len(correspondences) / total_phrases_a if total_phrases_a > 0 else 0.0
        ),
        phrase_coverage_b=(
            len(correspondences) / total_phrases_b if total_phrases_b > 0 else 0.0
        ),
    )

    return MelodyAlignmentResult(
        phrase_correspondences=correspondences,
        unmatched_phrase_indices_a=tuple(gaps_a_idx),
        unmatched_phrase_indices_b=tuple(gaps_b_idx),
        coverage=coverage,
    )
