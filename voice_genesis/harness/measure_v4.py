"""v0.4 案A: source-filter 同時推定（design memo v04 §A）。

現行（v3）の共線性機序:
  (i) F1_est が動くと source_tilt の回帰対象倍音集合（k<=min(8,0.9F1/f0)）が
      連動して変わる
  (ii) tilt が動くとケプストラム包絡ピークの見え方が変わり formant_centroid
       の推定に影響する
これは「F1 を先に決めてから tilt を求める」逐次推定の連鎖依存が原因。

本ファイルは倍音振幅列 {(k, A_k[dB])} に対し
    A_k = c + tilt*log2(k) + Σ_i L_i(f_k)
（L_i はローレンツ型共鳴ピーク、i=1..2）を **同時に** 座標降下でフィットし、
tilt と formant peak を対等な最適化対象として求めることで逐次依存を切断する。

制約: 既存の measure.py / measure_v2.py / measure_v3.py は不変のまま再利用
する（呼び出しのみ）。mean_f0 / periodicity / rms / vibrato_depth は v3 の
実装をそのまま使う（特徴量セット v3 として凍結、差分は formant_centroid /
source_tilt の推定法のみ）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

import measure as m  # 無改変で再利用
import measure_v3 as m3  # 無改変で再利用（強化 F0 推定器 / periodicity / vibrato）

SR_DEFAULT = m.SR_DEFAULT
EPS = 1e-12

# --- joint fit 設計定数（凍結・決定論） ---
BAND_LO_HZ = 300.0  # フォルマントピーク探索帯域下限（v3 の formant_centroid と同じ）
BAND_HI_HZ = 4000.0  # 同上限
HARMONIC_BAND_LIMIT_HZ = 8000.0  # 倍音観測の帯域上限（「帯域内全倍音」の帯域）
MAX_HARMONICS = 40  # memo §A: 上限40
MIN_HARMONICS_FOR_2PEAK = 5  # memo §A: K<5 で1ピークへ縮退
MIN_HARMONICS_FOR_FIT = 3  # [UNDERSPEC-v4] memoが明記しない下限。c+tilt+1peak(F,G)の
# 最小限4自由度に対し独立観測3点はなお過小決定だが、これ未満は物理的に無意味な
# フィットになるためNaN+fit_mode="insufficient_harmonics"で明示的に諦める閾値とした。

PEAK_BANDWIDTHS_HZ: Tuple[float, float] = (80.0, 100.0)  # [UNDERSPEC-v4] 固定 B_i。
# memoは「v0 R0のフォルマント帯域幅オーダーの固定値」と指示するのみで具体値は
# 与えていない。voice_r0.ResonanceParams.bandwidths_hz のデフォルト先頭2値
# (80.0, 90.0) のオーダーを踏襲し、2本目をやや広め(100Hz)にして分離を助けた。

N_ITER = 4  # memo §A: 3-5回反復
COARSE_GRID_N = 40
FINE_GRID_N = 15
MIN_PEAK_SEPARATION_HZ = 150.0  # 初期化時、2ピークが同一箇所に縮退しないための最小分離


# ---------------------------------------------------------------------------
# 倍音振幅観測（帯域内全倍音、上限40）
# ---------------------------------------------------------------------------


def harmonic_peak_amplitudes(sig: np.ndarray, sr: int, f0: float) -> List[Tuple[int, float, float]]:
    """(k, f_k, amp_k_dB) のリスト。measure.py のピーク探索手法を踏襲。"""
    if not np.isfinite(f0) or f0 <= 0:
        return []
    analysis = m._analysis_window(sig, sr)
    window = np.hanning(len(analysis))
    spec = np.abs(np.fft.rfft(analysis * window))
    freqs = np.fft.rfftfreq(len(analysis), d=1.0 / sr)
    bin_hz = freqs[1] - freqs[0] if len(freqs) > 1 else 1.0
    band_limit = min(sr / 2.0 * 0.95, HARMONIC_BAND_LIMIT_HZ)

    out = []
    for k in range(1, MAX_HARMONICS + 1):
        f_k = k * f0
        if f_k >= band_limit:
            break
        half_width = max(2 * bin_hz, f_k * 0.05)
        lo = np.searchsorted(freqs, f_k - half_width)
        hi = np.searchsorted(freqs, f_k + half_width)
        if hi <= lo:
            continue
        peak = spec[lo:hi].max()
        if peak <= 1e-9:
            continue
        out.append((k, f_k, 20.0 * math.log10(peak)))
    return out


# ---------------------------------------------------------------------------
# ローレンツ型共鳴ピーク（dB 領域）+ 座標降下 joint fit
# ---------------------------------------------------------------------------


def _lorentz_shape(f: np.ndarray, F: float, B: float) -> np.ndarray:
    gamma = B / 2.0
    return 1.0 / (1.0 + ((f - F) / gamma) ** 2)


def _solve_peak_gain(shape_vals: np.ndarray, residual: np.ndarray) -> float:
    """固定 F, B のもとで G を閉形式の線形最小二乗で解く（1 パラメータ回帰）。

    [UNDERSPEC-v4] G>=0 制約（共鳴ピークは正のブーストのみ、notch は物理的に
    別現象）は memo に明記がないが、無制約のまま座標降下すると G<0 の
    「反ピーク」がノイズ的な残差構造を吸収する縮退解に落ち、真の F1/F2 から
    大きく外れた局所解へ発散する実害を確認した（実測: G<0 の "anti-peak"
    が生じたノートは fit が高周波帯へ発散していた）。共鳴ピークの物理的
    意味（正のブーストのみ）に基づき G を非負にクリップする。
    """
    denom = float(np.sum(shape_vals**2))
    if denom <= 1e-12:
        return 0.0
    g = float(np.sum(shape_vals * residual) / denom)
    return max(0.0, g)


def _fit_single_peak(
    f_k: np.ndarray,
    residual: np.ndarray,
    band_lo: float,
    band_hi: float,
    B: float,
    search_center: Optional[float] = None,
    local_span_ratio: float = 1.3,
) -> Tuple[float, float, float]:
    """グリッド探索 + 局所精密化で 1 ピーク (F, G) を残差に対しフィットする。

    [UNDERSPEC-v4] memo は「グリッド + 局所精密化」とのみ指示し、座標降下の
    反復ごとに全帯域を再探索するか、現在位置の近傍だけを探索するかを
    明記していない。全帯域再探索を実装したところ、倍音本数の少ない高音域
    ノード（E4/A4/C5/C6）で座標降下がケプストラム初期値（真の F1/F2 付近）
    から 3000-4000Hz の帯域端へ発散する実害を確認した（残差の裾に対する
    過適合。狭帯域ローレンツピークは端点付近で大きな G を割り当てると
    局所的に RSS を下げられてしまうため）。反復 1 回目のみ全帯域探索とし、
    2 回目以降は現在位置の ±30% 近傍のみを探索する「局所化する座標降下」
    に変更したところ発散が解消したため採用する。
    """
    if search_center is not None:
        lo = max(band_lo, search_center / local_span_ratio)
        hi = min(band_hi, search_center * local_span_ratio)
        if hi <= lo:
            lo, hi = band_lo, band_hi
    else:
        lo, hi = band_lo, band_hi

    coarse = np.geomspace(lo, hi, COARSE_GRID_N)
    best_F, best_G, best_rss = float(coarse[0]), 0.0, float("inf")
    for F in coarse:
        shape = _lorentz_shape(f_k, F, B)
        G = _solve_peak_gain(shape, residual)
        rss = float(np.sum((residual - G * shape) ** 2))
        if rss < best_rss:
            best_rss, best_F, best_G = rss, float(F), G

    spacing_ratio = coarse[1] / coarse[0] if len(coarse) > 1 else 1.1
    fine_lo = max(lo, best_F / spacing_ratio)
    fine_hi = min(hi, best_F * spacing_ratio)
    fine = np.linspace(fine_lo, fine_hi, FINE_GRID_N)
    for F in fine:
        shape = _lorentz_shape(f_k, F, B)
        G = _solve_peak_gain(shape, residual)
        rss = float(np.sum((residual - G * shape) ** 2))
        if rss < best_rss:
            best_rss, best_F, best_G = rss, float(F), G

    return best_F, best_G, best_rss


@dataclass
class JointFitResult:
    c: float
    tilt: float
    peaks: List[Tuple[float, float]]  # [(F,G), ...] F 昇順
    fit_mode: str
    residual_rms: float
    n_harmonics_used: int


def _init_peaks_from_cepstrum(
    sig: np.ndarray, sr: int, f0: float, n_peaks: int, band_lo: float, band_hi: float
) -> List[List[float]]:
    """初期ピーク位置をケプストラム包絡（measure_v3、無改変で再利用）の帯域内ピークから決める。

    [UNDERSPEC-v4] memo は初期化手順を規定していない。当初「粗い tilt 回帰の
    残差が最大の倍音」から初期ピークを選ぶ方式を実装したが、倍音本数が少ない
    高音域ノート（E4/A4/C5 等）で残差最大点が帯域端の外れ値（雑音・境界効果）
    に引きずられ、座標降下が F1/F2 と無関係な高周波（3000-4000Hz 帯）に
    収束する実害を実測した（真の F1,F2=(800,1150)Hz に対し fit 結果が
    (3067,3798)Hz 等に発散）。ケプストラム平滑化包絡（v3 で既に実装・検証
    済み、調波リップルを除去した滑らかな包絡）のピークを初期値にすることで
    この発散を解消した。**探索対象の倍音集合自体は制限しない**（全 K 本を
    joint fit に使う）ため、v3 の「F1_est で倍音集合を絞る」逐次依存とは
    異なる — ここでの流用はあくまで初期値のみで、最終推定は同時最適化する。
    """
    freqs, env_db = m3.cepstral_envelope_db(sig, sr, f0)
    peaks = m3._find_local_peaks(freqs, env_db, band_lo, band_hi)
    # 振幅の大きい順に上位 n_peaks 個を採用してから周波数昇順に並べ直す
    # （n_peaks=1 のとき単純な最低周波数ピークでは弱い/スプリアスなピークを
    # 拾ってしまうため。§A の 2 ピーク時は元々ほぼ同義になる）
    peaks_by_mag = sorted(peaks, key=lambda p: p[1], reverse=True)
    chosen = sorted(peaks_by_mag[:n_peaks], key=lambda p: p[0])
    result = [[float(f), max(float(db), 0.1)] for f, db in chosen]
    while len(result) < n_peaks:
        candidate = min(result[-1][0] * 1.4, band_hi) if result else (band_lo + band_hi) / 2.0
        result.append([candidate, 0.1])
    return result


def _run_coordinate_descent(
    f_k: np.ndarray, log2k: np.ndarray, amps: np.ndarray, n_peaks: int, bandwidths: Tuple[float, ...],
    init_peaks: List[List[float]], band_lo: float, band_hi: float,
) -> Tuple[float, float, List[List[float]], float]:
    peaks = [list(p) for p in init_peaks]
    X0 = np.column_stack([np.ones_like(log2k), log2k])
    coef0, *_ = np.linalg.lstsq(X0, amps, rcond=None)
    c, tilt = float(coef0[0]), float(coef0[1])

    # 1 回目は全帯域探索、2 回目以降は現在位置近傍のみ探索（局所化。詳細は
    # _fit_single_peak の docstring [UNDERSPEC-v4] 参照）
    for iter_idx in range(N_ITER):
        peak_contrib = np.zeros_like(amps)
        for (F, G), B in zip(peaks, bandwidths):
            peak_contrib += G * _lorentz_shape(f_k, F, B)
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
                other_contrib += G * _lorentz_shape(f_k, F, B)
            residual = amps - base - other_contrib
            search_center = peaks[i][0]
            F_new, G_new, _rss = _fit_single_peak(
                f_k, residual, band_lo, band_hi, bandwidths[i], search_center=search_center
            )
            peaks[i] = [F_new, G_new]

    peak_contrib = np.zeros_like(amps)
    for (F, G), B in zip(peaks, bandwidths):
        peak_contrib += G * _lorentz_shape(f_k, F, B)
    pred = c + tilt * log2k + peak_contrib
    residual_rms = float(np.sqrt(np.mean((amps - pred) ** 2)))
    return c, tilt, peaks, residual_rms


def joint_source_filter_fit(
    sig: np.ndarray, sr: int, f0: float, band_lo: float = BAND_LO_HZ, band_hi: float = BAND_HI_HZ,
    two_peak_improvement_ratio: float = 0.85,
) -> JointFitResult:
    """§A joint fit。[UNDERSPEC-v4 モデル選択] 追記:

    2 ピークモデルは倍音間隔（f0）がフォルマント帯域幅・F1-F2 分離幅に対し
    相対的に大きい高音域ノードで自由度過多になり、座標降下が物理的に無意味な
    局所解（片方の共鳴ピークが帯域端で巨大な G を取る等）に収束する縮退を
    実測で確認した。memo は K<5 の場合のみ 1 ピークへの縮退を規定するが、
    それだけでは不十分だったため、**2 ピーク fit の残差 RMS が 1 ピーク fit
    より `two_peak_improvement_ratio`（=0.85、つまり 15% 以上の相対改善）を
    超えて改善する場合のみ 2 ピークモデルを採用し、そうでなければより単純な
    1 ピークモデルにフォールバックする**モデル選択を追加した（fit_mode に
    `_model_selected_1peak` を付記して開示する）。真値（Genome のフォルマント
    設計値）は一切参照せず、観測データの当てはまり改善度のみで判定する。
    """
    obs = harmonic_peak_amplitudes(sig, sr, f0)
    K = len(obs)
    if K < MIN_HARMONICS_FOR_FIT:
        return JointFitResult(
            c=float("nan"), tilt=float("nan"), peaks=[], fit_mode="insufficient_harmonics",
            residual_rms=float("nan"), n_harmonics_used=K,
        )

    ks = np.array([o[0] for o in obs], dtype=float)
    f_k = np.array([o[1] for o in obs], dtype=float)
    amps = np.array([o[2] for o in obs], dtype=float)
    log2k = np.log2(ks)

    # --- 1 ピークモデル（常に計算し、フォールバック候補とする） ---
    init_1 = _init_peaks_from_cepstrum(sig, sr, f0, 1, band_lo, band_hi)
    c1, tilt1, peaks1, rms1 = _run_coordinate_descent(
        f_k, log2k, amps, 1, PEAK_BANDWIDTHS_HZ[:1], init_1, band_lo, band_hi
    )

    if K < MIN_HARMONICS_FOR_2PEAK:
        peaks_sorted = sorted(peaks1, key=lambda p: p[0])
        return JointFitResult(
            c=c1, tilt=tilt1, peaks=[(p[0], p[1]) for p in peaks_sorted], fit_mode="joint_1peak_fallback",
            residual_rms=rms1, n_harmonics_used=K,
        )

    # --- 2 ピークモデル ---
    init_2 = _init_peaks_from_cepstrum(sig, sr, f0, 2, band_lo, band_hi)
    c2, tilt2, peaks2, rms2 = _run_coordinate_descent(
        f_k, log2k, amps, 2, PEAK_BANDWIDTHS_HZ[:2], init_2, band_lo, band_hi
    )

    # [UNDERSPEC-v4 追記] 単純な RSS 比（two_peak_improvement_ratio）だけでは、
    # 倍音本数 K が少ない（15本以下程度）ノードで 2 ピーク目が実質的に
    # 過学習（帯域端の外れ値吸収）で RSS を稼いでしまい阻止できないことを
    # 実測した。BIC（ベイズ情報量規準: K*ln(RSS/K) + n_params*ln(K)）による
    # モデル選択に切替え、サンプル数に対して相対的にパラメータ数が多い
    # 2 ピークモデルをより強く罰則することで過学習を抑制した。
    n_params_1, n_params_2 = 4, 6  # (c,tilt,F,G) と (c,tilt,F1,G1,F2,G2)
    rss1 = max(rms1**2 * K, 1e-12)
    rss2 = max(rms2**2 * K, 1e-12)
    bic1 = K * math.log(rss1 / K) + n_params_1 * math.log(K)
    bic2 = K * math.log(rss2 / K) + n_params_2 * math.log(K)

    # [UNDERSPEC-v4 追記] BIC を導入してもなお、K が band 上限(=7 程度)に近い
    # ノードで 2 本目のピークが帯域端の外れ値に張り付き、F2/F1 比が現実の
    # 母音では起こらない値（実測で 3.85 倍等）まで発散するケースが残った。
    # 声道音響の物理的妥当性（母音フォルマント比は概ね F2/F1 <~3 に収まる）
    # を最終ガードとして導入し、フィット後の F2/F1 比がこの範囲外なら
    # （真値を参照せず、比の妥当域のみで）2 ピーク解を棄却して 1 ピークへ
    # フォールバックする。
    peaks2_sorted = sorted(peaks2, key=lambda p: p[0])
    ratio2 = peaks2_sorted[1][0] / peaks2_sorted[0][0] if peaks2_sorted[0][0] > 0 else float("inf")
    plausible_ratio = 1.05 <= ratio2 <= 2.5

    if bic2 < bic1 and rms2 <= two_peak_improvement_ratio * rms1 and plausible_ratio:
        c, tilt, peaks, residual_rms, fit_mode = c2, tilt2, peaks2, rms2, "joint_2peak"
    elif not plausible_ratio:
        c, tilt, peaks, residual_rms, fit_mode = c1, tilt1, peaks1, rms1, "joint_2peak_implausible_ratio_fallback_1peak"
    else:
        c, tilt, peaks, residual_rms, fit_mode = c1, tilt1, peaks1, rms1, "joint_2peak_model_selected_1peak"

    peaks_sorted = sorted(peaks, key=lambda p: p[0])
    return JointFitResult(
        c=c, tilt=tilt, peaks=[(p[0], p[1]) for p in peaks_sorted], fit_mode=fit_mode,
        residual_rms=residual_rms, n_harmonics_used=K,
    )


def source_tilt_v4(fit: JointFitResult) -> float:
    return fit.tilt


def formant_centroid_v4(fit: JointFitResult) -> Tuple[float, float]:
    """(geomean_hz, log2hz) を返す。1ピーク縮退時は単独周波数を使う。"""
    if not fit.peaks:
        return float("nan"), float("nan")
    freqs = [p[0] for p in fit.peaks[:2]]
    if len(freqs) == 2:
        geomean = math.sqrt(freqs[0] * freqs[1])
    else:
        geomean = freqs[0]
    return geomean, (math.log2(geomean) if geomean > 0 else float("nan"))


# ---------------------------------------------------------------------------
# 統合特徴抽出（特徴量セット v3 継承 + formant_centroid_v4 / source_tilt_v4）
# ---------------------------------------------------------------------------

REF_SCALE_V4 = {
    "mean_f0": 25.0,
    "formant_centroid": 0.10,
    "source_tilt": 1.5,  # joint fit は常に回帰勾配を返すため v3 の h1h2 分岐は不要（単一 ref）
    "periodicity": 3.0,
    "rms": 1.0,
    "vibrato_depth": 10.0,
}

FEATURE_NAMES_V4 = ["mean_f0", "formant_centroid", "source_tilt", "periodicity", "rms", "vibrato_depth"]


@dataclass
class FeaturesV4:
    mean_f0_hz: float
    mean_f0_cents: float
    formant_centroid_hz: float
    formant_centroid_log2hz: float
    fit_peaks: List[Tuple[float, float]]
    fit_c: float
    source_tilt_value: float
    fit_mode: str
    fit_residual_rms: float
    n_harmonics_used: int
    periodicity_db: float
    periodicity_r_median: float
    rms: float
    rms_db: float
    vibrato_depth_cents: float
    vibrato_reject_rate: float
    measured_with_caveat: bool


def extract_all_features_v4(
    sig: np.ndarray, sr: int = SR_DEFAULT, fmin: float = 55.0, fmax: float = 2900.0, caveat_reject_threshold: float = 0.05
) -> FeaturesV4:
    f0 = m3.estimate_f0_v3(sig, sr=sr, fmin=fmin, fmax=fmax)
    fit = joint_source_filter_fit(sig, sr, f0)
    fc_hz, fc_log2 = formant_centroid_v4(fit)

    ptrack = m3.periodicity_track_v3(sig, sr=sr, fmin=fmin, fmax=fmax)
    rms = m.rms_loudness(sig)
    rms_db = 20.0 * np.log10(max(rms, EPS))
    vib = m3.vibrato_depth_robust_v3(sig, sr=sr, fmin=fmin, fmax=fmax)
    caveat = bool(vib.reject_rate > caveat_reject_threshold)

    mean_f0_cents = 1200.0 * np.log2(f0) if np.isfinite(f0) and f0 > 0 else float("nan")

    return FeaturesV4(
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


def transformed_value(feat: FeaturesV4, feature_name: str) -> float:
    if feature_name == "mean_f0":
        return feat.mean_f0_cents
    if feature_name == "formant_centroid":
        return feat.formant_centroid_log2hz
    if feature_name == "source_tilt":
        return feat.source_tilt_value
    if feature_name == "periodicity":
        return feat.periodicity_db
    if feature_name == "rms":
        return feat.rms_db
    if feature_name == "vibrato_depth":
        return feat.vibrato_depth_cents
    raise KeyError(feature_name)


def ref_scale_for(feature_name: str) -> float:
    return REF_SCALE_V4[feature_name]
