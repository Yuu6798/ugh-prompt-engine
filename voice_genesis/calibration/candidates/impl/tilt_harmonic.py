"""M2T-HARMONIC-OLS / M2T-HARMONIC-THEILSEN algorithm families（設計正本 §8）。

倍音振幅取得方式を単一の方法に凍結する（§8: 「harmonic amplitude 取得方式は
1案に凍結」）: rFFT ビンのうち `k*f0` に最も近いビンを中心とした 3 点
（直前・直後ビンを含む）の対数振幅（dB）へ放物線補間を適用し、`k*f0` 位置
での振幅（dB）を推定する。

`K` 本未満の倍音しか取得できない場合は **縮退せず missing** とする
（§8 の明記事項。H1-H2 へのフォールバックは行わない = 「H1-H2 は別
construct として selection 競争から除外」）。

回帰は OLS（最小二乗、`np.polyfit`）と Theil-Sen（`scipy.stats.theilslopes`、
外れ値に頑健な中央値ベース勾配）の 2 系列を提供する。
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
from scipy.signal.windows import blackmanharris, hann
from scipy.stats import theilslopes

from ... import vocab
from ..adapter import MeterOutput

_WINDOWS = {"hann": hann, "blackman_harris": blackmanharris}


def _analysis_window(signal: np.ndarray, start_frac: float = 0.15, end_frac: float = 0.9) -> np.ndarray:
    n = len(signal)
    a = int(n * start_frac)
    b = int(n * end_frac)
    return signal[a:b] if b > a else signal


def _parabolic_interp_db(log_mag_db: np.ndarray, target_bin: float) -> float | None:
    """`target_bin`（非整数可）近傍 3 点の放物線補間により dB 値を推定する。"""
    idx = int(round(target_bin))
    if idx <= 0 or idx >= len(log_mag_db) - 1:
        return None
    y0, y1, y2 = log_mag_db[idx - 1], log_mag_db[idx], log_mag_db[idx + 1]
    delta = target_bin - idx  # in [-0.5, 0.5]
    # 2 次多項式 y = a*x^2 + b*x + c を (-1,y0),(0,y1),(1,y2) で決定し x=delta で評価
    a = 0.5 * (y0 + y2) - y1
    b = 0.5 * (y2 - y0)
    c = y1
    return float(a * delta**2 + b * delta + c)


def harmonic_amplitudes_db(
    signal: np.ndarray, sr: int, f0_hz: float, k_max: int, window_name: str
) -> list[float | None]:
    """k=1..k_max の各倍音について `20*log10(A_k)`（推定振幅、dB）を返す。

    取得できない倍音（対象周波数が Nyquist 超過 or 境界近傍）は None。
    """
    if not np.isfinite(f0_hz) or f0_hz <= 0:
        return [None] * k_max
    analysis = _analysis_window(np.asarray(signal, dtype=float))
    window_fn = _WINDOWS[window_name]
    window = window_fn(len(analysis))
    spec = np.abs(np.fft.rfft(analysis * window))
    eps = 1e-12
    log_mag_db = 20.0 * np.log10(spec + eps)
    freqs = np.fft.rfftfreq(len(analysis), d=1.0 / sr)
    bin_hz = freqs[1] - freqs[0] if len(freqs) > 1 else 1.0

    out: list[float | None] = []
    for k in range(1, k_max + 1):
        target_hz = k * f0_hz
        if target_hz >= sr / 2.0 * 0.98:
            out.append(None)
            continue
        target_bin = target_hz / bin_hz
        out.append(_parabolic_interp_db(log_mag_db, target_bin))
    return out


def _regression_inputs(amplitudes_db: list[float | None]) -> tuple[np.ndarray, np.ndarray] | None:
    xs, ys = [], []
    for k, amp_db in enumerate(amplitudes_db, start=1):
        if amp_db is None:
            return None  # 縮退せず missing（K 本すべて揃わなければ全体を missing とする）
        xs.append(np.log2(k))
        ys.append(amp_db)
    return np.array(xs), np.array(ys)


def tilt_ols_db_per_oct(signal: np.ndarray, sr: int, f0_hz: float, *, k: int, window: str) -> float | None:
    amps = harmonic_amplitudes_db(signal, sr, f0_hz, k, window)
    inputs = _regression_inputs(amps)
    if inputs is None:
        return None
    xs, ys = inputs
    slope, _intercept = np.polyfit(xs, ys, 1)
    return float(slope)


def tilt_theilsen_db_per_oct(signal: np.ndarray, sr: int, f0_hz: float, *, k: int, window: str) -> float | None:
    amps = harmonic_amplitudes_db(signal, sr, f0_hz, k, window)
    inputs = _regression_inputs(amps)
    if inputs is None:
        return None
    xs, ys = inputs
    slope, _intercept, _lo, _hi = theilslopes(ys, xs)
    return float(slope)


def _measure(
    signal: np.ndarray, sr: int, params: Mapping[str, object], estimator
) -> MeterOutput:
    f0_hz = float(params.get("f0_hz", float("nan")))
    k = int(params["k"])
    window = str(params["window"])
    slope = estimator(signal, sr, f0_hz, k=k, window=window)
    if slope is None:
        return MeterOutput(missing_reason=vocab.MissingReason.OUTPUT_MISSING)
    return MeterOutput(values={"tilt_db_per_oct": slope})


def measure_ols(signal: np.ndarray, sr: int, params: Mapping[str, object]) -> MeterOutput:
    return _measure(signal, sr, params, tilt_ols_db_per_oct)


def measure_theilsen(signal: np.ndarray, sr: int, params: Mapping[str, object]) -> MeterOutput:
    return _measure(signal, sr, params, tilt_theilsen_db_per_oct)
