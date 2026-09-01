"""M5 transition/join algorithm families（設計正本 §8）: wave-discontinuity /
spectral-flux。いずれも「join time 推定値 + magnitude（検出強度）」を返す
（§8: 「3軸を単一 TotalScore に合成しない」。本 module は単一検出器 1 候補
= 1 magnitude のみを返し、TotalScore は作らない）。
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from ... import vocab
from ..adapter import MeterOutput

# ---------------------------------------------------------------------------
# M5-WAVE-DISCONTINUITY
# ---------------------------------------------------------------------------


def wave_discontinuity(
    signal: np.ndarray, sr: int, *, window_ms: float
) -> tuple[float, float]:
    """短窓 RMS の frame-to-frame 差分（jump）が最大となる時刻と magnitude。

    window_ms 幅の非重複フレームごとに RMS を計算し、隣接フレーム RMS の
    絶対差分の最大値を magnitude、その位置（境界時刻）を join_time とする。
    """
    sig = np.asarray(signal, dtype=float)
    win = max(int(window_ms / 1000.0 * sr), 4)
    n_frames = len(sig) // win
    if n_frames < 2:
        return float("nan"), float("nan")
    rms = np.array(
        [
            float(np.sqrt(np.mean(sig[i * win : (i + 1) * win] ** 2)))
            for i in range(n_frames)
        ]
    )
    diffs = np.abs(np.diff(rms))
    peak_idx = int(np.argmax(diffs))
    magnitude = float(diffs[peak_idx])
    join_time_s = float((peak_idx + 1) * win / sr)
    return join_time_s, magnitude


def measure_wave_discontinuity(
    signal: np.ndarray, sr: int, params: Mapping[str, object]
) -> MeterOutput:
    window_ms = float(params["window_ms"])
    join_time_s, magnitude = wave_discontinuity(signal, sr, window_ms=window_ms)
    if not np.isfinite(magnitude):
        return MeterOutput(missing_reason=vocab.MissingReason.OUTPUT_MISSING)
    return MeterOutput(values={"join_time_s": join_time_s, "magnitude": magnitude})


# ---------------------------------------------------------------------------
# M5-SPECTRAL-FLUX
# ---------------------------------------------------------------------------


def spectral_flux(
    signal: np.ndarray, sr: int, *, frame_len: int, norm: str
) -> tuple[float, float]:
    """frame-to-frame 振幅スペクトル差分の L1/L2 ノルム（flux）が最大となる時刻と magnitude。"""
    sig = np.asarray(signal, dtype=float)
    hop = frame_len // 2
    n_frames = (len(sig) - frame_len) // hop + 1 if len(sig) >= frame_len else 0
    if n_frames < 2:
        return float("nan"), float("nan")

    window = np.hanning(frame_len)
    prev_mag = None
    flux_values = []
    for i in range(n_frames):
        start = i * hop
        frame = sig[start : start + frame_len] * window
        mag = np.abs(np.fft.rfft(frame))
        if prev_mag is not None:
            diff = mag - prev_mag
            if norm == "l1":
                flux = float(np.sum(np.abs(diff)))
            elif norm == "l2":
                flux = float(np.sqrt(np.sum(diff**2)))
            else:
                raise ValueError(f"unknown norm: {norm!r}")
            flux_values.append(flux)
        prev_mag = mag

    if not flux_values:
        return float("nan"), float("nan")
    flux_arr = np.array(flux_values)
    peak_idx = int(np.argmax(flux_arr))
    magnitude = float(flux_arr[peak_idx])
    join_time_s = float((peak_idx + 1) * hop / sr)
    return join_time_s, magnitude


def measure_spectral_flux(
    signal: np.ndarray, sr: int, params: Mapping[str, object]
) -> MeterOutput:
    frame_len = int(params["frame_len"])
    norm = str(params["norm"]).lower()
    join_time_s, magnitude = spectral_flux(signal, sr, frame_len=frame_len, norm=norm)
    if not np.isfinite(magnitude):
        return MeterOutput(missing_reason=vocab.MissingReason.OUTPUT_MISSING)
    return MeterOutput(values={"join_time_s": join_time_s, "magnitude": magnitude})
