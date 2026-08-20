"""planb_real/pr_match.py — Ritsu/PJS の突合と正規化転写（instruction §6）。

PJS と Ritsu に同じ歌詞・旋律を要求しない。転写するのは**比率と形**であって
絶対値ではない。

    duration : PJS の consonant_share / vowel_share を Ritsu のノート総尺へ配分
               （総尺は Ritsu のまま = 「歌」を差し替えるのではなく「配分」を移す）
    pitch    : note-relative cents（絶対 Hz を写さない）+ phase-relative な写像
    dynamics : unit 内平均 0 の正規化 dB
    release  : 正規化した終端カーブ

音素対応は phoneme-aligned:  子音→子音 / 母音→母音 の位置で写す。
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np

from pb_tracks import IdentityBank, PerformanceTrack, ReleaseSpec  # noqa: E402
from pr_performance import RealPerformanceTrack

_EPS = 1e-12


def _is_consonant_unit(bank: IdentityBank, i: int) -> bool:
    return bank.units[i].vowel not in ("a", "i", "u", "e", "o", "N", "sil")


def note_unit_span(bank: IdentityBank, probe_pos: int) -> Tuple[int, int]:
    """probe ノートを構成する unit 範囲 [lo, hi]（子音があれば含める）。"""
    lo = probe_pos
    if probe_pos > 0 and _is_consonant_unit(bank, probe_pos - 1):
        lo = probe_pos - 1
    return lo, probe_pos


def transplanted_durations(bank: IdentityBank, probe_pos: int,
                           perf: RealPerformanceTrack) -> np.ndarray:
    """PJS の share を Ritsu のノート総尺へ適用した unit 尺列を返す。"""
    dur = np.array([u.duration_s for u in bank.units], dtype=np.float64)
    lo, hi = note_unit_span(bank, probe_pos)
    total = float(np.sum(dur[lo:hi + 1]))
    if lo == hi:
        return dur
    c_share = float(np.clip(perf.timing.consonant_share, 0.02, 0.6))
    dur[lo] = total * c_share
    dur[hi] = total * (1.0 - c_share)
    return dur


#: 転写後の配分が元と同じとみなす閾値（share 差）
NOOP_SHARE_TOL = 1e-3


def timing_transplant_is_noop(bank: IdentityBank, probe_pos: int,
                              perf: RealPerformanceTrack) -> bool:
    """PJS と Ritsu の子音/母音比が同じなら duration 転写は何も動かさない。

    §6 の正規化は「比率」を移すので、両者が同じ比率（例: 一様なテンポ差だけ）
    のとき R2 は R0 と同一になる。これは実装の不具合ではなく**素材の性質**で
    あり、黙って通すと「timing skill を交換した」と誤読されるため明示的に
    検出して record へ載せる。
    """
    lo, hi = note_unit_span(bank, probe_pos)
    if lo == hi:
        return True
    dur = [u.duration_s for u in bank.units]
    total = float(dur[lo] + dur[hi])
    native_share = float(dur[lo] / total) if total > 0 else 0.0
    return abs(native_share - float(perf.timing.consonant_share)) < NOOP_SHARE_TOL


def _sample(series: np.ndarray, src_pos: np.ndarray, targets: np.ndarray) -> np.ndarray:
    order = np.argsort(np.asarray(src_pos, dtype=np.float64), kind="stable")
    return np.interp(targets, np.asarray(src_pos, dtype=np.float64)[order],
                     np.asarray(series, dtype=np.float64)[order])


def build_compose_track(
    bank: IdentityBank, probe_pos: int, perf: RealPerformanceTrack, *,
    frames_per_unit: int = 64,
) -> PerformanceTrack:
    """RealPerformanceTrack -> 合成器が食える PerformanceTrack（1 次元のみ）。

    probe ノート以外の unit には Performance を載せない（逸脱 0 / 利得 0）。
    介入を probe ノートに限定することで、G3 intervention tripwire の
    「どの軸が動いたか」を曖昧にしない。
    """
    n_units = bank.n_units
    lo, hi = note_unit_span(bank, probe_pos)
    c_share_src = float(np.clip(perf.timing.consonant_share, 0.0, 1.0))

    dev_c: List[np.ndarray] = []
    en_db: List[np.ndarray] = []
    idx_all: List[np.ndarray] = []
    pos_all: List[np.ndarray] = []

    for i in range(n_units):
        pos = (np.arange(frames_per_unit, dtype=np.float64) + 0.5) / float(frames_per_unit)
        idx_all.append(np.full(frames_per_unit, i, dtype=np.float64))
        pos_all.append(pos)
        if i < lo or i > hi:
            dev_c.append(np.zeros(frames_per_unit))
            en_db.append(np.zeros(frames_per_unit))
            continue
        if lo != hi and i == lo:            # 子音 unit -> PJS 子音区間へ写す
            g = pos * c_share_src
        elif lo != hi and i == hi:          # 母音 unit -> PJS 母音区間へ写す
            g = c_share_src + pos * (1.0 - c_share_src)
        else:                                # 子音なしノート
            g = pos
        dev_c.append(_sample(perf.pitch.f0_dev_cents, perf.pitch.f0_dev_pos, g))
        en_db.append(_sample(perf.dynamics.energy_db, perf.dynamics.energy_pos, g))

    return PerformanceTrack(
        source_id=f"{perf.source_id}:{perf.probe_kind}",
        f0_dev_cents=np.concatenate(dev_c),
        f0_dev_unit_index=np.concatenate(idx_all),
        f0_dev_unit_pos=np.concatenate(pos_all),
        unit_durations_s=transplanted_durations(bank, probe_pos, perf),
        energy_db=np.concatenate(en_db),
        energy_unit_index=np.concatenate(idx_all),
        energy_unit_pos=np.concatenate(pos_all),
        release=ReleaseSpec(window_frac=float(perf.release.window_frac),
                            taper_db=np.asarray(perf.release.terminal_decay_shape_db,
                                                dtype=np.float64),
                            hold_core=bool(perf.release.hold_core)),
    )


def matching_report(bank: IdentityBank, probe_pos: int,
                    perf: RealPerformanceTrack) -> dict:
    """転写の内訳（§6 の例と同じ読み方ができる表）。"""
    lo, hi = note_unit_span(bank, probe_pos)
    src_dur = [float(u.duration_s) for u in bank.units]
    tgt = transplanted_durations(bank, probe_pos, perf)
    return {
        "pjs_note_duration_s": round(perf.timing.note_duration_s, 4),
        "pjs_consonant_duration_s": round(perf.timing.consonant_duration_s, 4),
        "pjs_vowel_duration_s": round(perf.timing.vowel_duration_s, 4),
        "pjs_consonant_share": round(perf.timing.consonant_share, 4),
        "pjs_vowel_share": round(perf.timing.vowel_share, 4),
        "ritsu_note_total_s": round(float(sum(src_dur[lo:hi + 1])), 4),
        "ritsu_native_split_s": [round(x, 4) for x in src_dur[lo:hi + 1]],
        "transplanted_split_s": [round(float(x), 4) for x in tgt[lo:hi + 1]],
        "note_unit_span": [lo, hi],
        "timing_transplant_is_noop": timing_transplant_is_noop(bank, probe_pos, perf),
        "absolute_hz_copied": False,
        "absolute_seconds_copied": False,
    }


def probe_pair_key(ritsu_file: str, pjs_file: str, kind: str) -> str:
    return f"{kind}|{ritsu_file}|{pjs_file}"


def stratify(events: Sequence[dict]) -> dict:
    """§8 の層別（短/中/長）。probe 母音尺の 3 分位で分ける。"""
    if not events:
        return {"short": [], "medium": [], "long": []}
    durs = np.array([e["vowel_duration_s"] for e in events], dtype=np.float64)
    q1, q2 = np.quantile(durs, [1 / 3, 2 / 3])
    out = {"short": [], "medium": [], "long": []}
    for e, d in zip(events, durs):
        out["short" if d <= q1 else ("medium" if d <= q2 else "long")].append(e)
    return out
