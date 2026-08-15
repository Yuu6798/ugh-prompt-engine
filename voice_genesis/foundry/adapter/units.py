"""adapter/units.py — VG-F1: 単位選択（target cost + concatenation cost・貪欲・決定論）
+ 尺合わせ（伸縮キャップ + 往復ループ）。

設計書 §2 units.py に対応する。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

from donor_bank import DonorBank, DonorUnit  # noqa: F401  (adapter sibling import)

DEFAULT_W_P = 1.0
DEFAULT_W_D = 0.3
DEFAULT_W_C = 1.0

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


@dataclass(frozen=True)
class UnitSelection:
    note_index: int
    unit: DonorUnit
    cost_pitch: float
    cost_duration: float
    cost_concat: float
    cost_total: float
    n_candidates: int
    semitone_range_used: float
    expanded: bool


def select_units(
    targets: Sequence[TargetNote],
    units: Sequence[DonorUnit],
    w_p: float = DEFAULT_W_P,
    w_d: float = DEFAULT_W_D,
    w_c: float = DEFAULT_W_C,
) -> Tuple[List[UnitSelection], dict]:
    """target ノート列に対し、貪欲逐次 argmin（決定論）で donor unit を選ぶ。

    候補 = |Δpitch(semitone)| <= 現在の半音レンジの unit。空なら段階拡張し
    （拡張履歴は expanded フラグ + semitone_range_used で結果に残す）、
    それでも空なら全 unit へフォールバックする。tie は unit.index 昇順
    （argmin を index 昇順に走査し「厳密に小さい場合のみ更新」することで
    決定論を保証する）。
    """
    if not units:
        raise ValueError("units is empty: donor bank に単位が 1 つもない")

    sorted_units = sorted(units, key=lambda u: u.index)

    selections: List[UnitSelection] = []
    last_unit: Optional[DonorUnit] = None
    n_expansions = 0

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

        if expanded:
            n_expansions += 1

        best: Optional[Tuple[float, DonorUnit, float, float, float]] = None
        for u in candidates:  # sorted_units 由来なので index 昇順を保つ
            cp = _semitone_dist(u.median_f0, note.pitch_hz)
            cd = abs(float(np.log(max(u.duration_s, 1e-6) / max(note.duration_sec, 1e-6))))
            if last_unit is None:
                cc = 0.0
            else:
                cc = float(np.linalg.norm(last_unit.tail_log_bands - u.head_log_bands))
            total = w_p * cp + w_d * cd + w_c * cc
            if best is None or total < best[0]:
                best = (total, u, cp, cd, cc)

        assert best is not None
        total, u, cp, cd, cc = best
        selections.append(
            UnitSelection(
                note_index=i, unit=u, cost_pitch=cp, cost_duration=cd, cost_concat=cc,
                cost_total=total, n_candidates=len(candidates), semitone_range_used=semitone_range,
                expanded=expanded,
            )
        )
        last_unit = u

    stats = dict(n_notes=len(targets), n_expansions=n_expansions)
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
