"""planb/pb_extract.py — (wav, sr, Unit 列) から Identity / Performance を抽出する。

**資産に依存しない**のが要点。substitute（手続き合成）でも実資産
（リツ voicebank レンダ / PJS 実歌唱 + lab）でも、`Unit` 列さえ用意できれば
同じ関数で両 Track を作れる。したがって実資産経路で追加が要るのは
「wav と音素境界の供給」だけであり、分離機構そのものはここで完結する。

Identity 側は sp/ap をユニット単位に切り出すだけ。Performance 側は
f0/duration/energy/release を **1 次元系列**へ落とす。
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

import numpy as np

import pb_world as w
from pb_tracks import IdentityBank, PerformanceTrack, ReleaseSpec, Unit

_EPS = 1e-12
F0_DEV_CLAMP_CENTS = 700.0


def _frame_range(unit: Unit, frame_period_ms: float, n_frames: int) -> Tuple[int, int]:
    fp = frame_period_ms / 1000.0
    lo = int(np.floor(unit.start_s / fp))
    hi = int(np.ceil(unit.end_s / fp))
    lo = max(0, min(lo, n_frames - 1))
    hi = max(lo + 1, min(hi, n_frames))
    return lo, hi


def _core_range(unit: Unit, lo: int, frame_period_ms: float, n_unit: int) -> Tuple[int, int]:
    fp = frame_period_ms / 1000.0
    c0 = int(np.floor(unit.core_start_s / fp)) - lo
    c1 = int(np.ceil(unit.core_end_s / fp)) - lo
    c0 = max(0, min(c0, n_unit - 1))
    c1 = max(c0 + 1, min(c1, n_unit))
    return c0, c1


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
def build_identity_bank(
    analysis: w.WorldAnalysis, units: Sequence[Unit], *, source_id: str,
) -> IdentityBank:
    """WORLD 解析 + Unit 列 -> IdentityBank（sp/ap をユニット単位に切り出す）。"""
    sps, aps, f0s, cores = [], [], [], []
    for u in units:
        lo, hi = _frame_range(u, analysis.frame_period_ms, analysis.n_frames)
        sp = np.array(analysis.sp[lo:hi], dtype=np.float64)
        ap = np.array(analysis.ap[lo:hi], dtype=np.float64)
        f0 = np.array(analysis.f0[lo:hi], dtype=np.float64)
        c0, c1 = _core_range(u, lo, analysis.frame_period_ms, sp.shape[0])
        sps.append(sp)
        aps.append(ap)
        f0s.append(f0)
        cores.append((c0, c1))

    nasal_env, nasal_src = _measure_nasal_envelope(units, sps, cores)
    return IdentityBank(
        source_id=source_id, sr=analysis.sr, frame_period_ms=analysis.frame_period_ms,
        units=tuple(units), sp=tuple(sps), ap=tuple(aps), f0=tuple(f0s),
        core_slices=tuple(cores), nasal_envelope=nasal_env, nasal_envelope_source=nasal_src,
    )


def _measure_nasal_envelope(
    units: Sequence[Unit], sps: Sequence[np.ndarray], cores: Sequence[Tuple[int, int]],
) -> Tuple[Optional[np.ndarray], str]:
    """identity 自身の鼻音包絡を実測する（撥音 /N/ のコア、または /m/ /n/ の頭部）。

    り→ん破綻の「ん」側テクスチャは **identity から取る**（Performance ドナーの
    テクスチャは一切使わない）。実測源が無ければ None を返し、呼び出し側は
    その旨を record に明記する（合成代替で埋めない = 捏造しない）。
    """
    for i, u in enumerate(units):
        if u.vowel == "N":
            c0, c1 = cores[i]
            return np.mean(sps[i][c0:c1], axis=0), f"measured:{u.label}(moraic_nasal_core)"
    for i, u in enumerate(units):
        c0, _c1 = cores[i]
        if u.onset in ("m", "n") and c0 >= 2:
            return np.mean(sps[i][:c0], axis=0), f"measured:{u.label}(onset_{u.onset}_head)"
    return None, "absent"


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------
def build_performance_track(
    analysis: w.WorldAnalysis, units: Sequence[Unit], *, source_id: str,
    energy_smooth_frames: int = 9, release_window_frac: float = 0.35,
) -> PerformanceTrack:
    """WORLD 解析 + Unit 列 -> PerformanceTrack（1 次元制御系列のみ）。

    - `f0_dev_cents`: 各 unit のコア中央値からの逸脱（cent）= vibrato /
      portamento / 立ち上がりの形。**絶対音高は捨てる**ので歌手の音域
      （identity 的性質）は移らない。
    - `unit_durations_s`: 音素・ノート尺。
    - `energy_db`: WORLD sp のフレーム総パワー（dB）を unit 内平均 0 へ正規化した
      **スカラ**列（低レート平滑済み）。スペクトル形状は含まない。
    - `release`: 終端 unit の末尾 `release_window_frac` 区間の正規化利得カーブ。
    """
    fp = analysis.frame_period_ms / 1000.0
    dev_c, dev_i, dev_p = [], [], []
    en_db, en_i, en_p = [], [], []
    durations = []

    frame_power_db = 10.0 * np.log10(np.maximum(np.sum(analysis.sp, axis=1), _EPS))
    frame_power_db = _smooth(frame_power_db, energy_smooth_frames)

    for idx, u in enumerate(units):
        durations.append(u.duration_s)
        lo, hi = _frame_range(u, analysis.frame_period_ms, analysis.n_frames)
        n = hi - lo
        c0, c1 = _core_range(u, lo, analysis.frame_period_ms, n)
        f0 = analysis.f0[lo:hi]
        voiced_core = f0[c0:c1][f0[c0:c1] > 0.0]
        ref = float(np.median(voiced_core)) if voiced_core.size else 0.0
        pos = (np.arange(n, dtype=np.float64) + 0.5) / float(n)
        if ref > 0.0:
            cents = np.where(f0 > 0.0, 1200.0 * np.log2(np.maximum(f0, _EPS) / ref), 0.0)
            # unit 内の逸脱（vibrato / portamento / 立ち上がり）に上限を置く。
            # これを超える値は harvest の倍/半周期誤検出であって歌い方ではない。
            cents = np.clip(cents, -F0_DEV_CLAMP_CENTS, F0_DEV_CLAMP_CENTS)
        else:
            cents = np.zeros(n, dtype=np.float64)
        dev_c.append(cents)
        dev_i.append(np.full(n, idx, dtype=np.float64))
        dev_p.append(pos)

        e = frame_power_db[lo:hi]
        e = e - float(np.mean(e))
        en_db.append(e)
        en_i.append(np.full(n, idx, dtype=np.float64))
        en_p.append(pos)

    release = _extract_release(
        analysis, units, frame_power_db, release_window_frac, fp,
    )
    return PerformanceTrack(
        source_id=source_id,
        f0_dev_cents=np.concatenate(dev_c), f0_dev_unit_index=np.concatenate(dev_i),
        f0_dev_unit_pos=np.concatenate(dev_p),
        unit_durations_s=np.asarray(durations, dtype=np.float64),
        energy_db=np.concatenate(en_db), energy_unit_index=np.concatenate(en_i),
        energy_unit_pos=np.concatenate(en_p),
        release=release,
    )


def _extract_release(
    analysis: w.WorldAnalysis, units: Sequence[Unit], frame_power_db: np.ndarray,
    window_frac: float, fp: float,
) -> ReleaseSpec:
    """終端 unit 群の末尾利得カーブを平均して 1 本の taper を作る。"""
    curves = []
    n_ref = 24
    for u in units:
        if not u.is_terminal:
            continue
        lo, hi = _frame_range(u, analysis.frame_period_ms, analysis.n_frames)
        n = hi - lo
        r0 = hi - max(2, int(round(n * window_frac)))
        seg = frame_power_db[r0:hi]
        if seg.size < 2:
            continue
        seg = seg - float(seg[0])
        curves.append(np.interp(np.linspace(0.0, 1.0, n_ref),
                                np.linspace(0.0, 1.0, seg.size), seg))
    if not curves:
        taper = np.zeros(n_ref, dtype=np.float64)
    else:
        taper = np.mean(np.stack(curves, axis=0), axis=0)
    return ReleaseSpec(window_frac=float(window_frac), taper_db=taper, hold_core=True)


def _smooth(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1:
        return np.asarray(x, dtype=np.float64)
    k = np.ones(int(n), dtype=np.float64) / float(n)
    pad = int(n) // 2
    xp = np.pad(np.asarray(x, dtype=np.float64), (pad, pad), mode="edge")
    return np.convolve(xp, k, mode="valid")[: x.shape[0]]
