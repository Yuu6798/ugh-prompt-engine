"""planb/pb_metrics.py — Plan B の計測軸（TRF / identity / performance）。

**総合スコアは作らない**（svp-rpe 側 M3 の規約と同じ方針: 軸別 evidence のみ）。
プランの受け入れ条件は軸ごとに独立した判定として `pb_gates` が扱う。

計測は合成結果の内部配列ではなく、**合成した wav を WORLD で再解析**して行う。
意図（compose が置いた sp）ではなく結果（実際に鳴っている音）を測るためで、
ボコーダ段で潰れた変化は改善として数えない。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

import pb_world as w
from pb_compose import ComposeResult
from pb_tracks import IdentityBank, PerformanceTrack, Unit

_EPS = 1e-12


@dataclass(frozen=True)
class TrfAxes:
    """terminal release failure の軸別 evidence（1 probe unit ぶん）。"""

    unit_index: int
    label: str
    nasal_gain_db: float      # 主軸: +で「コアより鼻音へ寄った」= り→ん signature
    drift_db: float           # 副軸: コアからの離れ具合（方向を問わない）
    f0_sag_cents: float       # 終端の音高のずり下がり（負で下降）
    energy_tail_db: float     # 終端 20% の平均 - release 窓頭。0 近傍/正 = 連続発声
    # --- レベル正規化版（各フレームを総パワー 1 へ正規化してから平均）---
    # S4 対照（taper のみ / コア保持なし）で「減衰させるだけで生の nasal_gain が
    # 下がる」交絡を実測したため追加した診断軸。**事前登録の主軸は生の
    # nasal_gain_db のまま**で、こちらは次走行への登録候補として併記する。
    nasal_gain_shape_db: float
    drift_shape_db: float

    def as_dict(self) -> Dict[str, object]:
        return {
            "unit_index": self.unit_index, "label": self.label,
            "nasal_gain_db": round(self.nasal_gain_db, 4),
            "drift_db": round(self.drift_db, 4),
            "f0_sag_cents": round(self.f0_sag_cents, 3),
            "energy_tail_db": round(self.energy_tail_db, 4),
            "nasal_gain_shape_db": round(self.nasal_gain_shape_db, 4),
            "drift_shape_db": round(self.drift_shape_db, 4),
        }


@dataclass(frozen=True)
class RungMetrics:
    rung: str
    toggles: str
    wav_sha256: str
    duration_s: float
    trf: Tuple[TrfAxes, ...]
    identity_core_lsd_db: float
    donor_texture_lsd_db: float
    f0_dev_rmse_cents: float
    dur_mae_ms: float
    energy_corr: float
    release_taper_rmse_db: float

    def as_dict(self) -> Dict[str, object]:
        return {
            "rung": self.rung, "toggles": self.toggles, "wav_sha256": self.wav_sha256,
            "duration_s": round(self.duration_s, 4),
            "trf": {t.label + f"@{t.unit_index}": t.as_dict() for t in self.trf},
            "identity_core_lsd_db": round(self.identity_core_lsd_db, 4),
            "donor_texture_lsd_db": round(self.donor_texture_lsd_db, 4),
            "f0_dev_rmse_cents": round(self.f0_dev_rmse_cents, 3),
            "dur_mae_ms": round(self.dur_mae_ms, 3),
            "energy_corr": round(self.energy_corr, 4),
            "release_taper_rmse_db": round(self.release_taper_rmse_db, 4),
        }


# ---------------------------------------------------------------------------
def _window(fr: Tuple[int, int], frac_lo: float, frac_hi: float) -> Tuple[int, int]:
    lo, hi = fr
    n = hi - lo
    a = lo + int(np.floor(n * frac_lo))
    b = lo + int(np.ceil(n * frac_hi))
    return max(lo, min(a, hi - 1)), max(a + 1, min(b, hi))


def _mean_sp(sp: np.ndarray, rng: Tuple[int, int]) -> np.ndarray:
    a, b = rng
    a = max(0, min(a, sp.shape[0] - 1))
    b = max(a + 1, min(b, sp.shape[0]))
    return np.mean(sp[a:b], axis=0)


def _mean_sp_shape(sp: np.ndarray, rng: Tuple[int, int]) -> np.ndarray:
    """各フレームを総パワー 1 へ正規化してから平均する（形状のみ・レベル非依存）。"""
    a, b = rng
    a = max(0, min(a, sp.shape[0] - 1))
    b = max(a + 1, min(b, sp.shape[0]))
    seg = sp[a:b]
    return np.mean(seg / np.maximum(np.sum(seg, axis=1, keepdims=True), _EPS), axis=0)


def _median_f0(f0: np.ndarray, rng: Tuple[int, int]) -> float:
    a, b = rng
    seg = f0[max(0, a):max(a + 1, b)]
    v = seg[seg > 0.0]
    return float(np.median(v)) if v.size else 0.0


def _power_db(sp: np.ndarray, rng: Tuple[int, int]) -> float:
    return float(10.0 * np.log10(np.maximum(np.mean(np.sum(_slice(sp, rng), axis=1)), _EPS)))


def _slice(arr: np.ndarray, rng: Tuple[int, int]) -> np.ndarray:
    a, b = rng
    a = max(0, min(a, arr.shape[0] - 1))
    b = max(a + 1, min(b, arr.shape[0]))
    return arr[a:b]


def measure_trf(
    analysis: w.WorldAnalysis,
    unit_frames: Sequence[Tuple[int, int]],
    probe_indices: Sequence[int],
    labels: Sequence[str],
    nasal_envelope: Optional[np.ndarray],
    *,
    release_frac: float = 0.35,
    core_ref_frac: Tuple[float, float] = (0.35, 0.60),
) -> Tuple[TrfAxes, ...]:
    """probe unit ごとに TRF 4 軸を測る。

    `core_ref_frac` = 「立ち上がり直後の母音の姿」= 終端側の変質を含まない参照。
    """
    out: List[TrfAxes] = []
    for idx in probe_indices:
        fr = unit_frames[idx]
        core = _window(fr, *core_ref_frac)
        rel = _window(fr, 1.0 - release_frac, 1.0)
        tail = _window(fr, 0.8, 1.0)
        core_sp = _mean_sp(analysis.sp, core)
        rel_sp = _mean_sp(analysis.sp, rel)
        drift = w.log_spectral_distance_db(rel_sp, core_sp)
        core_shape = _mean_sp_shape(analysis.sp, core)
        rel_shape = _mean_sp_shape(analysis.sp, rel)
        drift_shape = w.log_spectral_distance_db(rel_shape, core_shape)
        if nasal_envelope is None:
            nasal_gain = float("nan")
            nasal_gain_shape = float("nan")
        else:
            nasal_gain = (w.log_spectral_distance_db(core_sp, nasal_envelope)
                          - w.log_spectral_distance_db(rel_sp, nasal_envelope))
            nasal_shape = np.asarray(nasal_envelope, dtype=np.float64)
            nasal_shape = nasal_shape / max(float(np.sum(nasal_shape)), _EPS)
            nasal_gain_shape = (w.log_spectral_distance_db(core_shape, nasal_shape)
                                - w.log_spectral_distance_db(rel_shape, nasal_shape))
        f0_core = _median_f0(analysis.f0, core)
        f0_rel = _median_f0(analysis.f0, rel)
        sag = (1200.0 * np.log2(max(f0_rel, _EPS) / max(f0_core, _EPS))
               if f0_core > 0.0 and f0_rel > 0.0 else float("nan"))
        rel_onset = _window(fr, 1.0 - release_frac, 1.0 - release_frac + 0.05)
        energy_tail = _power_db(analysis.sp, tail) - _power_db(analysis.sp, rel_onset)
        out.append(TrfAxes(
            unit_index=int(idx), label=str(labels[idx]),
            nasal_gain_db=float(nasal_gain), drift_db=float(drift),
            f0_sag_cents=float(sag), energy_tail_db=float(energy_tail),
            nasal_gain_shape_db=float(nasal_gain_shape), drift_shape_db=float(drift_shape),
        ))
    return tuple(out)


# ---------------------------------------------------------------------------
def measure_identity_distance(
    analysis: w.WorldAnalysis, unit_core_frames: Sequence[Tuple[int, int]],
    reference_cores: Sequence[np.ndarray], skip: Sequence[bool],
) -> float:
    """出力コア包絡 vs 参照コア包絡の平均 LSD [dB]。"""
    vals = []
    for fr, ref, sk in zip(unit_core_frames, reference_cores, skip):
        if sk:
            continue
        vals.append(w.log_spectral_distance_db(_mean_sp(analysis.sp, fr), ref))
    return float(np.mean(vals)) if vals else float("nan")


def donor_core_envelopes(
    donor_analysis: w.WorldAnalysis, donor_units: Sequence[Unit],
) -> List[np.ndarray]:
    fp = donor_analysis.frame_period_ms / 1000.0
    out = []
    for u in donor_units:
        a = int(u.core_start_s / fp)
        b = int(u.core_end_s / fp)
        out.append(_mean_sp(donor_analysis.sp, (a, b)))
    return out


# ---------------------------------------------------------------------------
def measure_performance_axes(
    analysis: w.WorldAnalysis, result: ComposeResult, performance: PerformanceTrack,
    units: Sequence[Unit], *, release_frac: float = 0.35,
) -> Tuple[float, float, float, float]:
    """(f0_dev_rmse_cents, dur_mae_ms, energy_corr, release_taper_rmse_db)。"""
    fp = result.frame_period_ms / 1000.0
    dev_err, own_e, ref_e = [], [], []
    dur_err = []
    for i, (fr, u) in enumerate(zip(result.unit_frames, units)):
        n = fr[1] - fr[0]
        dur_err.append(abs(n * fp - float(performance.unit_durations_s[i])) * 1000.0)
        f0 = _slice(analysis.f0, fr)
        voiced = f0 > 0.0
        if voiced.sum() < 4:
            continue
        core = _window(fr, 0.35, 0.85)
        ref_hz = _median_f0(analysis.f0, core)
        if ref_hz <= 0.0:
            continue
        own_cents = np.where(voiced, 1200.0 * np.log2(np.maximum(f0, _EPS) / ref_hz), 0.0)
        tgt = _interp_unit(performance.f0_dev_cents, performance.f0_dev_unit_index,
                           performance.f0_dev_unit_pos, i, n)
        dev_err.append(float(np.sqrt(np.mean((own_cents[voiced] - tgt[voiced]) ** 2))))

        e = 10.0 * np.log10(np.maximum(np.sum(_slice(analysis.sp, fr), axis=1), _EPS))
        e = e - float(np.mean(e))
        own_e.append(e)
        ref_e.append(_interp_unit(performance.energy_db, performance.energy_unit_index,
                                  performance.energy_unit_pos, i, n))
    f0_rmse = float(np.mean(dev_err)) if dev_err else float("nan")
    dur_mae = float(np.mean(dur_err)) if dur_err else float("nan")
    if own_e:
        a = np.concatenate(own_e)
        b = np.concatenate(ref_e)
        corr = float(np.corrcoef(a, b)[0, 1]) if a.size > 2 else float("nan")
    else:
        corr = float("nan")

    taper_err = []
    ref_taper = np.asarray(performance.release.taper_db, dtype=np.float64)
    for i, u in enumerate(units):
        if not u.is_terminal:
            continue
        fr = result.unit_frames[i]
        rel = _window(fr, 1.0 - release_frac, 1.0)
        e = 10.0 * np.log10(np.maximum(np.sum(_slice(analysis.sp, rel), axis=1), _EPS))
        e = e - float(e[0])
        e_r = np.interp(np.linspace(0.0, 1.0, ref_taper.shape[0]),
                        np.linspace(0.0, 1.0, e.shape[0]), e)
        taper_err.append(float(np.sqrt(np.mean((e_r - ref_taper) ** 2))))
    taper_rmse = float(np.mean(taper_err)) if taper_err else float("nan")
    return f0_rmse, dur_mae, corr, taper_rmse


def _interp_unit(values, unit_index, unit_pos, i: int, n: int) -> np.ndarray:
    mask = unit_index == i
    if not np.any(mask):
        return np.zeros(n, dtype=np.float64)
    p = np.asarray(unit_pos[mask], dtype=np.float64)
    v = np.asarray(values[mask], dtype=np.float64)
    o = np.argsort(p, kind="stable")
    dst = (np.arange(n, dtype=np.float64) + 0.5) / float(n)
    return np.interp(dst, p[o], v[o])


def measure_rung(
    rung: str, result: ComposeResult, identity: IdentityBank,
    performance: PerformanceTrack, probe_indices: Sequence[int],
    donor_cores: Sequence[np.ndarray],
) -> RungMetrics:
    analysis = w.analyze(result.wav, result.sr, frame_period_ms=result.frame_period_ms)
    labels = [u.label for u in identity.units]
    skip = [u.vowel == "sil" for u in identity.units]
    identity_cores = [identity.core_mean_sp(i) for i in range(identity.n_units)]
    trf = measure_trf(analysis, result.unit_frames, probe_indices, labels,
                      identity.nasal_envelope)
    id_lsd = measure_identity_distance(analysis, result.unit_core_frames, identity_cores, skip)
    donor_lsd = measure_identity_distance(analysis, result.unit_core_frames, donor_cores, skip)
    f0_rmse, dur_mae, corr, taper_rmse = measure_performance_axes(
        analysis, result, performance, identity.units,
    )
    return RungMetrics(
        rung=rung, toggles=result.toggles_label, wav_sha256=result.sha256(),
        duration_s=result.wav.shape[0] / result.sr, trf=trf,
        identity_core_lsd_db=id_lsd, donor_texture_lsd_db=donor_lsd,
        f0_dev_rmse_cents=f0_rmse, dur_mae_ms=dur_mae, energy_corr=corr,
        release_taper_rmse_db=taper_rmse,
    )
