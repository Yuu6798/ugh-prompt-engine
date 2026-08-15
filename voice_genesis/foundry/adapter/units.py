"""adapter/units.py — VG-F1: 単位選択（target cost + concatenation cost・貪欲・決定論）
+ 尺合わせ（伸縮キャップ + 往復ループ）。

設計書 §2 units.py に対応する。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from donor_bank import DonorBank, DonorUnit  # noqa: F401  (adapter sibling import)
import vowel_class as vc  # noqa: F401  (adapter sibling import・追補 F1.1-A)

DEFAULT_W_P = 1.0
DEFAULT_W_D = 0.3
DEFAULT_W_C = 1.0
# 追補 F1.1-A item3: 母音不一致ペナルティ。設計書の指定値は「既定 10.0・
# 強制力のある大きさ」。
#
# [実装決定・要 record] 実ドナー（vocadito clip 2, A1 notes, 74 units）で
# 実測すると、既存の接合コスト cost_concat（24 log 帯域 L2 距離、w_c=1.0）は
# 異なる母音の unit 間で ~15-22 の値を取る（母音間で sp 包絡が大きく異なる
# ため、母音の異同と強く相関する構造的な性質）。w_v=10.0（既定値）のままだと
# この concat cost 差に負けて、母音制約が「音域内に候補が実在するのに」
# 無視される事例が sakura/umi 実測で複数発生した（w_v=10.0: 母音一致率
# 16/32、真に候補が存在しない 1 件を除いても 3 件が回避可能な不一致）。
# w_v を 18.0 まで引き上げると実測データで不一致 0 件（32/32 一致）に達した
# ため、安全域込みで 25.0 を採用する（"強制力のある大きさ" という設計意図を
# 実際の concat cost スケールで担保する較正。帯域指標での最適化ではなく、
# 母音制約という新しい選択軸を機能させるための重み較正）。
DEFAULT_W_V = 25.0

# 追補 F1.1-A item3: 近縁母音への半ペナルティフォールバック表（a↔o, i↔e, u↔o）。
# "↔" は双方向を意味する（例: a の近縁は o、かつ o の近縁は a と u の両方）。
NEAR_VOWEL_FALLBACK: Dict[str, Tuple[str, ...]] = {
    "a": ("o",),
    "o": ("a", "u"),
    "i": ("e",),
    "e": ("i",),
    "u": ("o",),
}


def mora_to_vowel_target(mora) -> Optional[str]:
    """score mora -> 選択コストの母音制約ターゲット。

    設計書追補 F1.1-A item4「目標母音時系列は score の mora から導出
    （F1a glue の母音解決を共通化）」に対応する。F1a glue（`results_f1a/glue_control.py`）
    は `seg.note.mora.vowel` をそのまま母音区間表に使っており、本関数もそれを
    踏襲する。

    [実装決定・record 記録] 撥音「ん」（`mora.vowel == "N"`）は鼻音として
    子音層（consonants.py）に処理を委譲し、母音選択には制約を課さない
    （None を返す = 制約なし）。sakura/umi の歌詞には「ん」は出現しないため
    現時点では未発動の分岐だが、将来の歌詞拡張に備えて実装しておく。
    """
    if mora.vowel in vc.VOWELS_5:
        return mora.vowel
    return None

INITIAL_SEMITONE_RANGE = 3.0
SEMITONE_EXPANSION_STEP = 3.0
# 段階拡張の安全弁（3 オクターブ分。実装決定・record 記録）。ここまで空なら
# 全 unit を候補にフォールバックする。
MAX_SEMITONE_RANGE = 36.0

STRETCH_RATIO_MIN = 0.5
STRETCH_RATIO_MAX = 2.0
# 往復ループに使う unit 中央区間の割合（中央 50%）。
LOOP_CENTER_FRACTION = 0.5


def _semitone_dist(f_a: float, f_b: float) -> float:
    f_a = max(f_a, 1e-6)
    f_b = max(f_b, 1e-6)
    return float(abs(12.0 * np.log2(f_a / f_b)))


@dataclass(frozen=True)
class TargetNote:
    pitch_hz: float
    duration_sec: float
    label: str = ""
    # 追補 F1.1-A: 母音制約ターゲット（a/i/u/e/o のいずれか、None=制約なし）。
    # `mora_to_vowel_target()` で score の mora から導出する。
    vowel_target: Optional[str] = None


@dataclass(frozen=True)
class UnitSelection:
    note_index: int
    unit: DonorUnit
    cost_pitch: float
    cost_duration: float
    cost_concat: float
    cost_vowel: float
    cost_total: float
    n_candidates: int
    semitone_range_used: float
    expanded: bool
    # 追補 F1.1-A: 母音制約の結果記録（vowel_target=None なら常に unconstrained）。
    vowel_target: Optional[str] = None
    vowel_selected: Optional[str] = None
    vowel_fallback: bool = False


def select_units(
    targets: Sequence[TargetNote],
    units: Sequence[DonorUnit],
    w_p: float = DEFAULT_W_P,
    w_d: float = DEFAULT_W_D,
    w_c: float = DEFAULT_W_C,
    unit_vowels: Optional[Dict[int, str]] = None,
    w_v: float = DEFAULT_W_V,
) -> Tuple[List[UnitSelection], dict]:
    """target ノート列に対し、貪欲逐次 argmin（決定論）で donor unit を選ぶ。

    候補 = |Δpitch(semitone)| <= 現在の半音レンジの unit。空なら段階拡張し
    （拡張履歴は expanded フラグ + semitone_range_used で結果に残す）、
    それでも空なら全 unit へフォールバックする。tie は unit.index 昇順
    （argmin を index 昇順に走査し「厳密に小さい場合のみ更新」することで
    決定論を保証する）。

    追補 F1.1-A item3: `unit_vowels`（unit.index -> ラベル、`vowel_class.classify_donor_units`
    の戻り値）と `note.vowel_target` が両方指定されている場合、候補コストに
    母音不一致ペナルティ `w_v` を加算する（一致=0 / 近縁母音=半ペナルティ
    （フォールバック発動として記録）/ それ以外（"x" 含む）=w_v 満額）。
    `unit_vowels=None` または `vowel_target=None` の note は無制約（既定挙動と
    完全後方互換）。
    """
    if not units:
        raise ValueError("units is empty: donor bank に単位が 1 つもない")

    sorted_units = sorted(units, key=lambda u: u.index)

    selections: List[UnitSelection] = []
    last_unit: Optional[DonorUnit] = None
    n_expansions = 0
    n_vowel_unconstrained = 0
    n_vowel_exact = 0
    n_vowel_near_fallback = 0
    n_vowel_mismatch = 0

    for i, note in enumerate(targets):
        semitone_range = INITIAL_SEMITONE_RANGE
        expanded = False
        candidates = [u for u in sorted_units if _semitone_dist(u.median_f0, note.pitch_hz) <= semitone_range]
        while not candidates and semitone_range < MAX_SEMITONE_RANGE:
            semitone_range += SEMITONE_EXPANSION_STEP
            expanded = True
            candidates = [u for u in sorted_units if _semitone_dist(u.median_f0, note.pitch_hz) <= semitone_range]
        if not candidates:
            candidates = list(sorted_units)
            expanded = True

        target_vowel = note.vowel_target if unit_vowels is not None else None

        # 追補 F1.1-A item3「目標母音の unit が音域内に無い場合は近縁母音へ
        # フォールバック」に対応: 現在の候補内に一致/近縁ラベルの unit が
        # 1 つも無い場合、既存の段階拡張と同じ機構（同じ step / 安全弁）で
        # 探索範囲を広げる（[実装決定・record 記録] ピッチ空集合時の拡張を
        # 母音空集合時にも適用する自然な拡張。既存の候補生成そのものは
        # unit_vowels=None の呼び出しで完全後方互換）。
        if target_vowel is not None:

            def _has_vowel_candidate(cands: List[DonorUnit]) -> bool:
                for u in cands:
                    lbl = unit_vowels.get(u.index, vc.LOW_CONFIDENCE_LABEL)  # type: ignore[union-attr]
                    if lbl == target_vowel or lbl in NEAR_VOWEL_FALLBACK.get(target_vowel, ()):
                        return True
                return False

            while not _has_vowel_candidate(candidates) and semitone_range < MAX_SEMITONE_RANGE:
                semitone_range += SEMITONE_EXPANSION_STEP
                expanded = True
                candidates = [
                    u for u in sorted_units if _semitone_dist(u.median_f0, note.pitch_hz) <= semitone_range
                ]
            if not _has_vowel_candidate(candidates):
                candidates = list(sorted_units)
                expanded = True

        if expanded:
            n_expansions += 1

        best: Optional[Tuple[float, DonorUnit, float, float, float, float, bool]] = None
        for u in candidates:  # sorted_units 由来なので index 昇順を保つ
            cp = _semitone_dist(u.median_f0, note.pitch_hz)
            cd = abs(float(np.log(max(u.duration_s, 1e-6) / max(note.duration_sec, 1e-6))))
            if last_unit is None:
                cc = 0.0
            else:
                cc = float(np.linalg.norm(last_unit.tail_log_bands - u.head_log_bands))
            if target_vowel is None:
                cv, fb = 0.0, False
            else:
                u_label = unit_vowels.get(u.index, vc.LOW_CONFIDENCE_LABEL)  # type: ignore[union-attr]
                if u_label == target_vowel:
                    cv, fb = 0.0, False
                elif u_label in NEAR_VOWEL_FALLBACK.get(target_vowel, ()):
                    cv, fb = 0.5 * w_v, True
                else:
                    cv, fb = w_v, False
            total = w_p * cp + w_d * cd + w_c * cc + cv
            if best is None or total < best[0]:
                best = (total, u, cp, cd, cc, cv, fb)

        assert best is not None
        total, u, cp, cd, cc, cv, fb = best

        vowel_selected: Optional[str] = None
        if target_vowel is not None:
            vowel_selected = unit_vowels.get(u.index, vc.LOW_CONFIDENCE_LABEL)  # type: ignore[union-attr]
            if vowel_selected == target_vowel:
                n_vowel_exact += 1
            elif fb:
                n_vowel_near_fallback += 1
            else:
                n_vowel_mismatch += 1
        else:
            n_vowel_unconstrained += 1

        selections.append(
            UnitSelection(
                note_index=i, unit=u, cost_pitch=cp, cost_duration=cd, cost_concat=cc, cost_vowel=cv,
                cost_total=total, n_candidates=len(candidates), semitone_range_used=semitone_range,
                expanded=expanded, vowel_target=target_vowel, vowel_selected=vowel_selected, vowel_fallback=fb,
            )
        )
        last_unit = u

    stats = dict(
        n_notes=len(targets), n_expansions=n_expansions,
        n_vowel_unconstrained=n_vowel_unconstrained, n_vowel_exact=n_vowel_exact,
        n_vowel_near_fallback=n_vowel_near_fallback, n_vowel_mismatch=n_vowel_mismatch,
    )
    return selections, stats


def _linear_resample_frames(x: np.ndarray, out_len: int) -> np.ndarray:
    """frame 軸の線形リサンプル（各周波数ビンごとに独立に線形補間）。"""
    n = x.shape[0]
    if out_len <= 0:
        out_len = 1
    if n == 1:
        return np.repeat(x, out_len, axis=0)
    src_pos = np.linspace(0.0, n - 1, out_len)
    idx0 = np.floor(src_pos).astype(np.int64)
    idx1 = np.minimum(idx0 + 1, n - 1)
    w = (src_pos - idx0)[:, None]
    return x[idx0] * (1.0 - w) + x[idx1] * w


def _ping_pong_pad(seg: np.ndarray, deficit: int) -> Tuple[np.ndarray, int]:
    """seg（中央 50% 区間）を往復（forward/backward 交互）で並べ deficit フレーム分作る。"""
    seg_len = seg.shape[0]
    if seg_len == 0 or deficit <= 0:
        return seg[:0], 0
    pieces = []
    total = 0
    forward = True
    n_cycles = 0
    while total < deficit:
        piece = seg if forward else seg[::-1]
        take = min(seg_len, deficit - total)
        pieces.append(piece[:take])
        total += take
        forward = not forward
        n_cycles += 1
    return np.concatenate(pieces, axis=0), n_cycles


def _force_length(x: np.ndarray, target_len: int) -> np.ndarray:
    n = x.shape[0]
    if n == target_len:
        return x
    if n > target_len:
        return x[:target_len]
    pad_n = target_len - n
    if n == 0:
        return np.zeros((target_len,) + x.shape[1:], dtype=x.dtype)
    pad = np.repeat(x[-1:], pad_n, axis=0)
    return np.concatenate([x, pad], axis=0)


@dataclass(frozen=True)
class ResolvedSegment:
    note_index: int
    sp: np.ndarray  # (n_frames, n_bins)
    ap: np.ndarray  # (n_frames, n_bins)
    n_frames: int
    true_ratio: float
    applied_ratio: float
    cap_mode: str  # "none" | "extended_looped" | "compressed_truncated"
    n_loop_cycles: int


def resolve_unit_to_note(
    bank: DonorBank, unit: DonorUnit, target_n_frames: int, note_index: int = -1
) -> ResolvedSegment:
    """donor unit を note の目標フレーム長へ尺合わせする。

    比率 target/unit_len を [0.5, 2.0] にキャップして線形リサンプルする。
    2.0 を超える長音は、キャップ後の unit 中央 50% 区間を往復ループして
    不足分を延伸する。0.5 未満（過圧縮）はキャップ後の結果を先頭から
    target 長へ切り詰める（最終フレームで不足すれば末尾フレームを保持して
    埋める。設計書に明記のない edge case のため実装決定・record 記録）。
    """
    unit_len = unit.end_frame - unit.start_frame
    if unit_len <= 0:
        raise ValueError(f"degenerate unit (empty frame range): {unit}")
    target_n_frames = max(target_n_frames, 1)

    sp_src = bank.sp[unit.start_frame:unit.end_frame]
    ap_src = bank.ap[unit.start_frame:unit.end_frame]

    true_ratio = target_n_frames / unit_len
    applied_ratio = min(max(true_ratio, STRETCH_RATIO_MIN), STRETCH_RATIO_MAX)
    base_len = max(1, int(round(unit_len * applied_ratio)))
    sp_base = _linear_resample_frames(sp_src, base_len)
    ap_base = _linear_resample_frames(ap_src, base_len)

    n_loop_cycles = 0
    if true_ratio > STRETCH_RATIO_MAX:
        cap_mode = "extended_looped"
        deficit = target_n_frames - base_len
        center_lo = base_len // 4
        center_hi = base_len - base_len // 4
        if center_hi <= center_lo:
            center_hi = min(base_len, center_lo + 1)
        sp_pad, n_loop_cycles = _ping_pong_pad(sp_base[center_lo:center_hi], deficit)
        ap_pad, _ = _ping_pong_pad(ap_base[center_lo:center_hi], deficit)
        sp_out = np.concatenate([sp_base, sp_pad], axis=0)
        ap_out = np.concatenate([ap_base, ap_pad], axis=0)
    elif true_ratio < STRETCH_RATIO_MIN:
        cap_mode = "compressed_truncated"
        sp_out = sp_base[:target_n_frames]
        ap_out = ap_base[:target_n_frames]
    else:
        cap_mode = "none"
        sp_out, ap_out = sp_base, ap_base

    sp_out = _force_length(sp_out, target_n_frames)
    ap_out = _force_length(ap_out, target_n_frames)

    return ResolvedSegment(
        note_index=note_index, sp=sp_out, ap=ap_out, n_frames=target_n_frames,
        true_ratio=true_ratio, applied_ratio=applied_ratio, cap_mode=cap_mode,
        n_loop_cycles=n_loop_cycles,
    )
