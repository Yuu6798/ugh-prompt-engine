"""VT-2 / VT-3 で使う自前計測器群。

C2-C7 (65-2093 Hz) を単一アルゴリズムでカバーする F0 推定器（HPS）、および
grip 行列で使う特徴抽出器（spectral centroid / tilt / HNR 近似 / RMS /
vibrato depth）を実装する。

[UNDERSPEC] 各特徴の定義選択は underspec_log.md にまとめて記録する
（ここではコード上の実装詳細のみコメントする）。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SR_DEFAULT = 22050


def _analysis_window(sig: np.ndarray, sr: int, start_frac: float = 0.3, end_frac: float = 0.85) -> np.ndarray:
    """アタック/リリースのフェード区間を避けた定常区間を切り出す。"""
    n = len(sig)
    a = int(n * start_frac)
    b = int(n * end_frac)
    return sig[a:b]


# ---------------------------------------------------------------------------
# 自前 F0 推定器: 正規化自己相関 (NACF), C2-C7 対応
#
# [UNDERSPEC-13] 当初 HPS (Harmonic Product Spectrum) で実装したが、本 R0
# 合成器はフォルマントフィルタで基音 (k=1) を強く減衰させるため
# （フォルマント周波数から遠い低音域の基音ほど強く減衰 = missing
# fundamental 現象）、周波数領域で基音付近のみを見る素朴な HPS
# (n_harmonics=5 分程度) は低音域で顕著なオクターブ誤り（3 倍音への
# 誤検出等）を起こした。時間領域の自己相関はどの倍音にエネルギーが
# あっても真の周期で強め合うため、フォルマント整形に対して頑健。
# 設計書はアルゴリズムを指定していない（HPS or 自己相関の例示のみ）ため、
# 実測で頑健性の高い自己相関を採用する。この判断も underspec_log に記録。
# ---------------------------------------------------------------------------


def _nacf_frame(frame: np.ndarray, sr: int, fmin: float, fmax: float, yin_threshold: float = 0.15) -> float:
    """1 フレームの YIN 式差分関数から F0 を推定する（放物線補間つき）。

    [UNDERSPEC-13] 単純な正規化自己相関（グローバル最大ピーク選択）は、
    本合成器のフォルマント整形で上位倍音が支配的になる音（特に高音域）で
    オクターブ誤り（真の周期の 2 倍・半分を誤選択）を頻発させた。
    YIN (de Cheveigne & Kawahara 2002) の累積平均正規化差分関数は
    「閾値を最初に下回る最小ラグ」を選ぶことでオクターブ誤りに頑健であり、
    実測で本合成器に対して大幅に改善したため採用する。
    """
    frame = frame - frame.mean()
    n = len(frame)
    window = np.hanning(n)
    framed = frame * window

    # [UNDERSPEC-13 追記] 探索範囲の境界ちょうどに真の周期が来るケース（C7 =
    # 2093Hz は sr=22050 で周期 10.5 サンプルしかなく fmax 直近）で、境界点が
    # 「極小」判定から漏れて選ばれない実測不具合があったため、fmin/fmax の
    # 外側に 1 サンプル分のマージンを取って探索する。
    lag_min = max(int(sr / fmax) - 1, 1)
    lag_max = min(int(sr / fmin) + 1, n - 2)
    if lag_max <= lag_min + 2:
        return float("nan")

    # d(tau) = sum_j x[j]^2 (0..n-tau-1) + sum_j x[j+tau]^2 (tau..n-1) - 2*ACF(tau)
    n_fft = int(2 ** np.ceil(np.log2(2 * n)))
    spec = np.fft.rfft(framed, n=n_fft)
    acf_full = np.fft.irfft(spec * np.conj(spec), n=n_fft)[:n]

    sq = framed**2
    cumsum = np.concatenate([[0.0], np.cumsum(sq)])
    total = cumsum[-1]

    # sum_j x[j]^2 for j in [0, n-tau) == cumsum[n-tau]
    # sum_j x[j+tau]^2 for j in [0, n-tau) == total - cumsum[tau]
    d = np.full(lag_max + 1, np.nan)
    for tau in range(1, lag_max + 1):
        e1 = cumsum[n - tau]
        e2 = total - cumsum[tau]
        d[tau] = e1 + e2 - 2 * acf_full[tau]

    # 累積平均正規化差分関数
    d_prime = np.ones(lag_max + 1)
    running_sum = 0.0
    for tau in range(1, lag_max + 1):
        running_sum += d[tau]
        mean_d = running_sum / tau
        d_prime[tau] = d[tau] / mean_d if mean_d > 1e-12 else 1.0

    search = d_prime[lag_min : lag_max + 1]
    if len(search) < 3:
        return float("nan")

    def _is_local_min(i: int) -> bool:
        # 境界点も片側近傍のみで判定する（[UNDERSPEC-13 追記]: 真の周期が
        # 探索範囲の端に来るケースで境界点が除外され続けた不具合の修正）。
        left_ok = search[i] <= search[i - 1] if i > 0 else True
        right_ok = search[i] <= search[i + 1] if i < len(search) - 1 else True
        return left_ok and right_ok

    # 閾値を最初に下回る局所最小を探す。
    chosen_rel = None
    for i in range(len(search)):
        if search[i] < yin_threshold and _is_local_min(i):
            chosen_rel = i
            break

    if chosen_rel is None:
        # [UNDERSPEC-13 追記] 絶対閾値を一度も下回らない場合（短周期・高倍音
        # 支配の高音域で周期性強度が弱い）、素朴なグローバル argmin は無関係な
        # 遠方ラグ（ノイズ由来の疑似周期）を誤選択しやすいことを実測で確認した。
        # 代わりに「グローバル最小値の近傍（相対許容 20%）に入る局所最小のうち
        # 最小ラグのもの」を選ぶ（真の基本周期が最小ラグ側にあるという事前知識
        # を使う、YIN 実装で一般的なオクターブ誤り対策）。
        local_minima = [i for i in range(len(search)) if _is_local_min(i)]
        if not local_minima:
            local_minima = [int(np.argmin(search))]
        global_min_val = min(search[i] for i in local_minima)
        if global_min_val > 0.9:  # 周期性が弱すぎる（無音・無声成分）
            return float("nan")
        rel_tol = 1.5
        candidates = [i for i in local_minima if search[i] <= global_min_val * rel_tol]
        chosen_rel = min(candidates)

    peak_lag = lag_min + chosen_rel

    # 放物線補間（差分関数は極小なので符号に注意）
    if lag_min < peak_lag < lag_max:
        y0, y1, y2 = d_prime[peak_lag - 1], d_prime[peak_lag], d_prime[peak_lag + 1]
        denom = y0 - 2 * y1 + y2
        delta = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-12 else 0.0
        delta = float(np.clip(delta, -1.0, 1.0))
    else:
        delta = 0.0
    refined_lag = peak_lag + delta
    if refined_lag <= 0:
        return float("nan")
    return float(sr / refined_lag)


def estimate_f0_hps(
    sig: np.ndarray,
    sr: int = SR_DEFAULT,
    fmin: float = 55.0,
    fmax: float = 2200.0,
    frame_len_sec: float = 0.093,
    hop_sec: float = 0.023,
) -> float:
    """自己相関ベースのフレーム毎 F0 推定 → 定常区間の中央値を返す（"mean F0"）。

    関数名は HPS 由来だが実装は NACF（[UNDERSPEC-13] 参照）。呼び出し側
    (VT-2/VT-3) との互換性のため名称は維持する。
    """
    analysis = _analysis_window(sig, sr)
    frame_len = int(frame_len_sec * sr)
    hop = max(int(hop_sec * sr), 1)
    if len(analysis) < frame_len:
        frame_len = len(analysis)

    f0_estimates = []
    for start in range(0, max(len(analysis) - frame_len, 1), hop):
        frame = analysis[start : start + frame_len]
        if len(frame) < frame_len:
            break
        f0 = _nacf_frame(frame, sr, fmin, fmax)
        if np.isfinite(f0):
            f0_estimates.append(f0)

    if not f0_estimates:
        return float("nan")
    return float(np.median(f0_estimates))


def estimate_f0_track_hps(
    sig: np.ndarray,
    sr: int = SR_DEFAULT,
    fmin: float = 55.0,
    fmax: float = 2200.0,
    frame_len_sec: float = 0.093,
    hop_sec: float = 0.023,
) -> np.ndarray:
    """estimate_f0_hps と同一アルゴリズムでフレーム毎 F0 系列を返す（vibrato depth 計測用）。"""
    analysis = _analysis_window(sig, sr)
    frame_len = int(frame_len_sec * sr)
    hop = max(int(hop_sec * sr), 1)
    if len(analysis) < frame_len:
        frame_len = len(analysis)

    track = []
    for start in range(0, max(len(analysis) - frame_len, 1), hop):
        frame = analysis[start : start + frame_len]
        if len(frame) < frame_len:
            break
        track.append(_nacf_frame(frame, sr, fmin, fmax))
    return np.array(track, dtype=float)


def cents_error(est_hz: float, true_hz: float) -> float:
    if not np.isfinite(est_hz) or est_hz <= 0 or true_hz <= 0:
        return float("nan")
    return float(1200.0 * np.log2(est_hz / true_hz))


# ---------------------------------------------------------------------------
# grip matrix 用特徴抽出器
# ---------------------------------------------------------------------------


@dataclass
class Features:
    mean_f0: float
    spectral_centroid: float
    spectral_tilt_db_per_oct: float
    hnr_db: float
    rms: float
    vibrato_depth_cents: float


def spectral_centroid_hz(sig: np.ndarray, sr: int = SR_DEFAULT, band_hz: float = 8000.0) -> float:
    analysis = _analysis_window(sig, sr)
    spec = np.abs(np.fft.rfft(analysis))
    freqs = np.fft.rfftfreq(len(analysis), d=1.0 / sr)
    band = freqs <= band_hz
    freqs = freqs[band]
    spec = spec[band]
    total = spec.sum()
    if total <= 0:
        return float("nan")
    return float((freqs * spec).sum() / total)


def spectral_tilt_db_per_oct(sig: np.ndarray, sr: int, f0_hz: float, n_harmonics: int = 12) -> float:
    """ハーモニック振幅の回帰勾配（dB vs log2(harmonic index)）。

    自前 F0 推定値を基準にビン中心を探索し、各倍音のピーク振幅を測って
    log2(k) に対する線形回帰の傾きを spectral tilt (dB/oct) として返す。
    """
    if not np.isfinite(f0_hz) or f0_hz <= 0:
        return float("nan")
    analysis = _analysis_window(sig, sr)
    window = np.hanning(len(analysis))
    spec = np.abs(np.fft.rfft(analysis * window))
    freqs = np.fft.rfftfreq(len(analysis), d=1.0 / sr)
    bin_hz = freqs[1] - freqs[0] if len(freqs) > 1 else 1.0

    xs, ys = [], []
    for k in range(1, n_harmonics + 1):
        target = k * f0_hz
        if target >= sr / 2 * 0.95:
            break
        search_half_width = max(2 * bin_hz, target * 0.05)
        lo = np.searchsorted(freqs, target - search_half_width)
        hi = np.searchsorted(freqs, target + search_half_width)
        if hi <= lo:
            continue
        peak_amp = spec[lo:hi].max()
        if peak_amp <= 1e-9:
            continue
        xs.append(np.log2(k))
        ys.append(20.0 * np.log10(peak_amp))
    if len(xs) < 3:
        return float("nan")
    xs_arr = np.array(xs)
    ys_arr = np.array(ys)
    slope, _intercept = np.polyfit(xs_arr, ys_arr, 1)
    return float(slope)


def hnr_db_approx(sig: np.ndarray, sr: int, f0_hz: float, n_harmonics: int = 10, rel_bw_frac: float = 0.015) -> float:
    """harmonic band エネルギー vs noise band エネルギーの近似 HNR。

    各倍音周辺 ±rel_bw_frac*freq の帯域エネルギーを harmonic energy として
    合算し、解析帯域 (0-8000Hz) の残りを noise energy とする。
    """
    if not np.isfinite(f0_hz) or f0_hz <= 0:
        return float("nan")
    analysis = _analysis_window(sig, sr)
    window = np.hanning(len(analysis))
    spec = np.abs(np.fft.rfft(analysis * window)) ** 2
    freqs = np.fft.rfftfreq(len(analysis), d=1.0 / sr)

    band_hz = 8000.0
    band_mask = freqs <= band_hz
    total_energy = spec[band_mask].sum()

    harmonic_mask = np.zeros_like(spec, dtype=bool)
    for k in range(1, n_harmonics + 1):
        target = k * f0_hz
        if target >= band_hz:
            break
        half_bw = max(target * rel_bw_frac, freqs[1] - freqs[0])
        lo = np.searchsorted(freqs, target - half_bw)
        hi = np.searchsorted(freqs, target + half_bw)
        harmonic_mask[lo:hi] = True

    harmonic_energy = spec[harmonic_mask & band_mask].sum()
    noise_energy = spec[(~harmonic_mask) & band_mask].sum()
    if noise_energy <= 1e-12:
        noise_energy = 1e-12
    if harmonic_energy <= 1e-12:
        harmonic_energy = 1e-12
    return float(10.0 * np.log10(harmonic_energy / noise_energy))


def rms_loudness(sig: np.ndarray) -> float:
    analysis = _analysis_window(sig, 1)  # sr unused in slicing fractions
    return float(np.sqrt(np.mean(analysis**2)))


def vibrato_depth_cents(sig: np.ndarray, sr: int = SR_DEFAULT, fmin: float = 55.0, fmax: float = 2200.0) -> float:
    """定常区間の F0 軌跡の std（cents）を vibrato/F0 変調深度として返す。"""
    track = estimate_f0_track_hps(sig, sr=sr, fmin=fmin, fmax=fmax)
    valid = track[np.isfinite(track) & (track > 0)]
    if len(valid) < 4:
        return float("nan")
    med = np.median(valid)
    cents = 1200.0 * np.log2(valid / med)
    return float(np.std(cents))


def extract_all_features(sig: np.ndarray, sr: int = SR_DEFAULT) -> Features:
    f0 = estimate_f0_hps(sig, sr=sr)
    centroid = spectral_centroid_hz(sig, sr=sr)
    tilt = spectral_tilt_db_per_oct(sig, sr=sr, f0_hz=f0)
    hnr = hnr_db_approx(sig, sr=sr, f0_hz=f0)
    rms = rms_loudness(sig)
    vib = vibrato_depth_cents(sig, sr=sr)
    return Features(
        mean_f0=f0,
        spectral_centroid=centroid,
        spectral_tilt_db_per_oct=tilt,
        hnr_db=hnr,
        rms=rms,
        vibrato_depth_cents=vib,
    )
