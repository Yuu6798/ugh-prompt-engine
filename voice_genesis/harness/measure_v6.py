"""v0.6 sweep 内計器凍結（design memo v06 §A, §B）。

制約: `measure_v4.py` は無改変のまま import して再利用する。本ファイルは
「系列中央 sweep 点で 1 度だけモデル構造（次数・アンカーピーク位置）を
v4 の既存規則（BIC + F2/F1 妥当性ゲート）で決定し、系列内の全 sweep 点
（および σ_meas 反復）をその凍結構造で再フィットする」ための拡張のみを
追加する。

凍結フィットの局所窓は **フレーム毎に移動する現在位置ではなく、常に固定
アンカーそのものを中心に ±30%** とする（`measure_v4._run_coordinate_descent`
の `search_center=peaks[i][0]`（毎回のイテレーションで現在位置を中心に
再探索）をそのまま流用すると、複数反復にわたって局所窓が複利的に
アンカーから離れうるため、本ファイルでは `measure_v4._fit_single_peak`
を直接呼び出し `search_center=<凍結アンカー>` を毎回固定して渡す独自の
座標降下ループを実装する）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

import measure as m  # 無改変で再利用
import measure_v3 as m3  # 無改変で再利用
import measure_v4 as m4  # 無改変で再利用（変更禁止）

SR_DEFAULT = m.SR_DEFAULT
EPS = 1e-12


# ---------------------------------------------------------------------------
# 凍結構造の決定（系列中央 sweep 点で 1 回だけ、v4 の既存規則をそのまま使用）
# ---------------------------------------------------------------------------


@dataclass
class FrozenStructure:
    n_peaks: int
    anchor_peaks: List[float]  # F のみ（G は凍結フィットのたびに再解く）
    bandwidths: Tuple[float, ...]
    source_fit_mode: str  # 中央点で v4 が選んだ fit_mode（開示用）
    source_residual_rms: float
    source_n_harmonics_used: int


def determine_frozen_structure(sig_mid: np.ndarray, sr: int, f0_mid: float) -> FrozenStructure:
    """系列中央 sweep 点で v4 の joint_source_filter_fit（無改変）を 1 回実行し、
    そのモデル次数・アンカーピーク位置を凍結構造として抽出する。
    """
    fit = m4.joint_source_filter_fit(sig_mid, sr, f0_mid)
    n_peaks = len(fit.peaks)
    anchors = [float(F) for F, _G in fit.peaks]
    bandwidths = m4.PEAK_BANDWIDTHS_HZ[:n_peaks] if n_peaks > 0 else tuple()
    return FrozenStructure(
        n_peaks=n_peaks,
        anchor_peaks=anchors,
        bandwidths=bandwidths,
        source_fit_mode=fit.fit_mode,
        source_residual_rms=fit.residual_rms,
        source_n_harmonics_used=fit.n_harmonics_used,
    )


# ---------------------------------------------------------------------------
# 凍結構造での再フィット（次数固定・アンカー ±30% 局所窓固定・モデル選択なし）
# ---------------------------------------------------------------------------


def _run_frozen_coordinate_descent(
    f_k: np.ndarray,
    log2k: np.ndarray,
    amps: np.ndarray,
    n_peaks: int,
    bandwidths: Tuple[float, ...],
    anchors: List[float],
    band_lo: float,
    band_hi: float,
    n_iter: int = m4.N_ITER,
) -> Tuple[float, float, List[List[float]], float]:
    peaks: List[List[float]] = [[float(F), 0.1] for F in anchors]

    for _ in range(n_iter):
        peak_contrib = np.zeros_like(amps)
        for (F, G), B in zip(peaks, bandwidths):
            peak_contrib += G * m4._lorentz_shape(f_k, F, B)
        y = amps - peak_contrib
        X = np.column_stack([np.ones_like(log2k), log2k])
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        c, tilt = float(coef[0]), float(coef[1])

        base = c + tilt * log2k
        for i in range(n_peaks):
            other_contrib = np.zeros_like(amps)
            for j, ((F, G), B) in enumerate(zip(peaks, bandwidths)):
                if j == i:
                    continue
                other_contrib += G * m4._lorentz_shape(f_k, F, B)
            residual = amps - base - other_contrib
            # 局所窓は「凍結アンカーそのもの」を中心に固定（peaks[i][0] の
            # 現在値ではない）: 反復をまたいだ複利的なドリフトを防ぐ。
            F_new, G_new, _rss = m4._fit_single_peak(
                f_k, residual, band_lo, band_hi, bandwidths[i], search_center=anchors[i]
            )
            peaks[i] = [F_new, G_new]

    peak_contrib = np.zeros_like(amps)
    for (F, G), B in zip(peaks, bandwidths):
        peak_contrib += G * m4._lorentz_shape(f_k, F, B)
    pred = c + tilt * log2k + peak_contrib
    residual_rms = float(np.sqrt(np.mean((amps - pred) ** 2)))
    return c, tilt, peaks, residual_rms


def fit_with_frozen_structure(
    sig: np.ndarray, sr: int, f0: float, frozen: FrozenStructure,
    band_lo: float = m4.BAND_LO_HZ, band_hi: float = m4.BAND_HI_HZ,
) -> m4.JointFitResult:
    """凍結構造（次数・アンカー）で、モデル選択・全帯域探索を一切行わずに再フィットする。"""
    obs = m4.harmonic_peak_amplitudes(sig, sr, f0)
    K = len(obs)

    if frozen.n_peaks == 0 or K < m4.MIN_HARMONICS_FOR_FIT:
        return m4.JointFitResult(
            c=float("nan"), tilt=float("nan"), peaks=[], fit_mode="frozen_insufficient_harmonics",
            residual_rms=float("nan"), n_harmonics_used=K,
        )

    ks = np.array([o[0] for o in obs], dtype=float)
    f_k = np.array([o[1] for o in obs], dtype=float)
    amps = np.array([o[2] for o in obs], dtype=float)
    log2k = np.log2(ks)

    c, tilt, peaks, residual_rms = _run_frozen_coordinate_descent(
        f_k, log2k, amps, frozen.n_peaks, frozen.bandwidths, frozen.anchor_peaks, band_lo, band_hi
    )
    peaks_sorted = sorted(peaks, key=lambda p: p[0])
    fit_mode = f"frozen_{frozen.n_peaks}peak"
    return m4.JointFitResult(
        c=c, tilt=tilt, peaks=[(p[0], p[1]) for p in peaks_sorted], fit_mode=fit_mode,
        residual_rms=residual_rms, n_harmonics_used=K,
    )


# ---------------------------------------------------------------------------
# 統合特徴抽出（凍結構造を外部から与える版。他 4 特徴は v3/v4 と同一定義）
# ---------------------------------------------------------------------------


def extract_features_frozen(
    sig: np.ndarray, sr: int, frozen: FrozenStructure, fmin: float = 55.0, fmax: float = 2900.0,
    caveat_reject_threshold: float = 0.05,
) -> m4.FeaturesV4:
    f0 = m3.estimate_f0_v3(sig, sr=sr, fmin=fmin, fmax=fmax)
    fit = fit_with_frozen_structure(sig, sr, f0, frozen)
    fc_hz, fc_log2 = m4.formant_centroid_v4(fit)

    ptrack = m3.periodicity_track_v3(sig, sr=sr, fmin=fmin, fmax=fmax)
    rms = m.rms_loudness(sig)
    rms_db = 20.0 * np.log10(max(rms, EPS))
    vib = m3.vibrato_depth_robust_v3(sig, sr=sr, fmin=fmin, fmax=fmax)
    caveat = bool(vib.reject_rate > caveat_reject_threshold)

    mean_f0_cents = 1200.0 * np.log2(f0) if np.isfinite(f0) and f0 > 0 else float("nan")

    return m4.FeaturesV4(
        mean_f0_hz=f0,
        mean_f0_cents=float(mean_f0_cents),
        formant_centroid_hz=fc_hz,
        formant_centroid_log2hz=fc_log2,
        fit_peaks=fit.peaks,
        fit_c=fit.c,
        source_tilt_value=fit.tilt,
        fit_mode=fit.fit_mode,
        fit_residual_rms=fit.residual_rms,
        n_harmonics_used=fit.n_harmonics_used,
        periodicity_db=ptrack.periodicity_db_median,
        periodicity_r_median=ptrack.r_median,
        rms=rms,
        rms_db=float(rms_db),
        vibrato_depth_cents=vib.depth_cents,
        vibrato_reject_rate=vib.reject_rate,
        measured_with_caveat=caveat,
    )


# 特徴名・ref_scale は v4 のものをそのまま再エクスポート（凍結の有無で変わらない）
FEATURE_NAMES_V6 = m4.FEATURE_NAMES_V4
REF_SCALE_V6 = m4.REF_SCALE_V4
transformed_value = m4.transformed_value
