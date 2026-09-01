"""M2A aperiodicity algorithm families（設計正本 §8）: HNR-ACF /
harmonic-residual / D4C（pyworld guarded）。

いずれも `f0_hz` を `params` から受け取る（F0 選択が下流候補実行前に完了
しているという設計正本 §8 の一方向依存を、呼び出し側が `params` に
`f0_hz` を注入することで表現する。本モジュールは F0 を自前推定しない）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
from scipy.signal.windows import blackmanharris, hann

from ... import vocab
from ..adapter import INELIGIBLE_DEPENDENCY_ABSENT, MeterOutput

try:
    import pyworld  # type: ignore[import-untyped]

    _PYWORLD_AVAILABLE = True
except ImportError:  # pragma: no cover - 本環境では未インストール（設計正本 §3.3 pyworld 特則）
    pyworld = None  # type: ignore[assignment]
    _PYWORLD_AVAILABLE = False

_WINDOWS = {"hann": hann, "blackman_harris": blackmanharris}

FMIN_HZ = 80.0
FMAX_HZ = 600.0


# ---------------------------------------------------------------------------
# M2A-HNR-ACF
# ---------------------------------------------------------------------------


def _frame_hnr_db(frame: np.ndarray, sr: int, window_name: str) -> float:
    windowed = frame * _WINDOWS[window_name](len(frame))
    windowed = windowed - windowed.mean()
    n = len(windowed)
    if n < 4 or np.allclose(windowed, 0.0):
        return float("nan")
    n_fft = int(2 ** np.ceil(np.log2(2 * n)))
    spec = np.fft.rfft(windowed, n=n_fft)
    acf = np.fft.irfft(spec * np.conj(spec), n=n_fft)[:n]
    if acf[0] <= 1e-20:
        return float("nan")
    acf_norm = acf / acf[0]

    lag_min = max(int(sr / FMAX_HZ), 1)
    lag_max = min(int(sr / FMIN_HZ), n - 1)
    if lag_max <= lag_min:
        return float("nan")
    r_max = float(np.max(acf_norm[lag_min : lag_max + 1]))
    r_max = min(max(r_max, 0.0), 0.999999)
    if r_max <= 0.0:
        return float("-inf")
    return float(10.0 * math.log10(r_max / (1.0 - r_max)))


def hnr_acf_db(
    signal: np.ndarray, sr: int, *, frame_ms: float, hop_ms: float, window: str
) -> float:
    """正規化自己相関ピークから求めたフレーム毎 HNR (dB) の中央値。"""
    sig = np.asarray(signal, dtype=float)
    frame_len = max(int(frame_ms / 1000.0 * sr), 8)
    hop = max(int(hop_ms / 1000.0 * sr), 1)
    if len(sig) < frame_len:
        return float("nan")

    values = []
    for start in range(0, len(sig) - frame_len + 1, hop):
        frame = sig[start : start + frame_len]
        v = _frame_hnr_db(frame, sr, window)
        if np.isfinite(v):
            values.append(v)
    if not values:
        return float("nan")
    return float(np.median(values))


def measure_hnr_acf(signal: np.ndarray, sr: int, params: Mapping[str, object]) -> MeterOutput:
    frame_ms = float(params["frame_ms"])
    hop_ms = float(params["hop_ms"])
    window = str(params["window"])
    value = hnr_acf_db(signal, sr, frame_ms=frame_ms, hop_ms=hop_ms, window=window)
    if not np.isfinite(value):
        return MeterOutput(missing_reason=vocab.MissingReason.OUTPUT_MISSING)
    return MeterOutput(values={"hnr_db": value})


# ---------------------------------------------------------------------------
# M2A-HARMONIC-RESIDUAL
# ---------------------------------------------------------------------------


def _analysis_window(signal: np.ndarray, start_frac: float = 0.15, end_frac: float = 0.9) -> np.ndarray:
    n = len(signal)
    a = int(n * start_frac)
    b = int(n * end_frac)
    return signal[a:b] if b > a else signal


def harmonic_residual_fraction(
    signal: np.ndarray,
    sr: int,
    f0_hz: float,
    *,
    k: int,
    window: str,
    residual_band: str,
) -> float:
    """comb-remove（各倍音近傍を除去）した残差パワー / 対象帯域全パワー。"""
    if not np.isfinite(f0_hz) or f0_hz <= 0:
        return float("nan")
    analysis = _analysis_window(np.asarray(signal, dtype=float))
    windowed = analysis * _WINDOWS[window](len(analysis))
    spec_power = np.abs(np.fft.rfft(windowed)) ** 2
    freqs = np.fft.rfftfreq(len(analysis), d=1.0 / sr)
    bin_hz = freqs[1] - freqs[0] if len(freqs) > 1 else 1.0

    if residual_band == "0-6khz":
        band_mask = freqs <= 6000.0
    elif residual_band == "broadband":
        band_mask = np.ones_like(freqs, dtype=bool)
    else:
        raise ValueError(f"unknown residual_band: {residual_band!r}")

    harmonic_mask = np.zeros_like(spec_power, dtype=bool)
    half_bw = max(2 * bin_hz, 0.02 * f0_hz)
    for h in range(1, k + 1):
        target = h * f0_hz
        if target >= sr / 2.0:
            break
        lo = np.searchsorted(freqs, target - half_bw)
        hi = np.searchsorted(freqs, target + half_bw)
        harmonic_mask[lo:hi] = True

    total_power = float(spec_power[band_mask].sum())
    if total_power <= 1e-20:
        return float("nan")
    residual_power = float(spec_power[band_mask & (~harmonic_mask)].sum())
    return residual_power / total_power


def measure_harmonic_residual(
    signal: np.ndarray, sr: int, params: Mapping[str, object]
) -> MeterOutput:
    f0_hz = float(params.get("f0_hz", float("nan")))
    k = int(params["k"])
    window = str(params["window"])
    residual_band = str(params["residual_band"])
    fraction = harmonic_residual_fraction(
        signal, sr, f0_hz, k=k, window=window, residual_band=residual_band
    )
    if not np.isfinite(fraction):
        return MeterOutput(missing_reason=vocab.MissingReason.OUTPUT_MISSING)
    return MeterOutput(values={"residual_fraction": fraction})


# ---------------------------------------------------------------------------
# M2A-D4C（pyworld guarded。設計正本 §3.3 pyworld 特則: 不在は当該候補のみ
# ineligible とし campaign 全体は BLOCK しない）
# ---------------------------------------------------------------------------


def d4c_available() -> bool:
    return _PYWORLD_AVAILABLE


def _band_slice(freq_axis: np.ndarray, band: str) -> np.ndarray:
    if band == "broadband":
        return np.ones_like(freq_axis, dtype=bool)
    if band == "0-3khz":
        return freq_axis <= 3000.0
    if band == "3-6khz":
        return (freq_axis > 3000.0) & (freq_axis <= 6000.0)
    raise ValueError(f"unknown band: {band!r}")


def measure_d4c(signal: np.ndarray, sr: int, params: Mapping[str, object]) -> MeterOutput:
    if not _PYWORLD_AVAILABLE:
        return MeterOutput(ineligible=True, ineligible_reason=INELIGIBLE_DEPENDENCY_ABSENT)

    f0_hz = float(params.get("f0_hz", float("nan")))
    band = str(params["band"])
    if not np.isfinite(f0_hz) or f0_hz <= 0:
        return MeterOutput(missing_reason=vocab.MissingReason.INPUT_MISSING)

    x = np.ascontiguousarray(np.asarray(signal, dtype=np.float64))
    frame_period = 5.0  # ms
    n_frames = max(int(len(x) / sr / (frame_period / 1000.0)), 1)
    temporal_positions = np.arange(n_frames) * (frame_period / 1000.0)
    f0_contour = np.full(n_frames, f0_hz, dtype=np.float64)

    ap = pyworld.d4c(x, f0_contour, temporal_positions, sr)  # (n_frames, n_fft//2+1)
    n_fft = 2 * (ap.shape[1] - 1)
    freq_axis = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    band_mask = _band_slice(freq_axis, band)
    if not band_mask.any():
        return MeterOutput(missing_reason=vocab.MissingReason.OUTPUT_MISSING)
    value = float(np.median(ap[:, band_mask]))
    if not np.isfinite(value):
        return MeterOutput(missing_reason=vocab.MissingReason.OUTPUT_MISSING)
    return MeterOutput(values={"aperiodicity": value})
