"""M3-CEPSTRAL-POLES algorithm family（設計正本 §8: 「baseline と同族」と明記）。

`voice_genesis/harness/measure_v3.py` の `cepstral_envelope_db` /
`formant_centroid_and_f1` と同じ理論的手法（ケプストラム low-time
liftering によるスペクトル包絡平滑化 → 包絡上の局所ピーク picking）を
踏襲するが、B0 の「無改変 import」制約とは独立に、§2.6 が凍結した
`lifter_ratio` / `min_lifter_samples` / `band_hi` グリッドをパラメタ化した
**新規実装**である（harness コードの import・変更のいずれも行わない）。

B0 が幾何平均の centroid + 最低ピークのみを返すのに対し、本 family は
ABSOLUTE 目標（F1/F2/F3 個別 Hz error）に対応するため、帯域内の局所ピーク
を周波数昇順に並べて F1/F2/F3 として報告する。

[UNDERSPEC-CAL-C02] 設計正本は「K 本未満は missing」を M2T (tilt_harmonic)
にのみ明記する。M3 formant 系にはピーク数の missing 閾値の明記がないため、
最も単純な規則として「帯域内ピークが 0 個なら OUTPUT_MISSING、1 個以上
見つかれば見つかった分だけ（F1 のみ、あるいは F1/F2 のみ等）を報告する」
を採用した（見つからなかった F2/F3 は NaN で埋めず単にキーを省略する。
`adapter.unexplained_nonfinite` と整合させるため）。
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from ... import vocab
from ..adapter import MeterOutput

def _analysis_window(signal: np.ndarray, start_frac: float = 0.15, end_frac: float = 0.9) -> np.ndarray:
    n = len(signal)
    a = int(n * start_frac)
    b = int(n * end_frac)
    return signal[a:b] if b > a else signal


def cepstral_envelope_db(
    signal: np.ndarray,
    sr: int,
    f0_hz: float,
    *,
    lifter_ratio: float,
    min_lifter_samples: int,
) -> tuple[np.ndarray, np.ndarray]:
    """log magnitude spectrum (dB) をケプストラム low-time liftering で平滑化する。

    `measure_v3.cepstral_envelope_db` と同じ liftering 方式（cutoff =
    lifter_ratio * 推定周期サンプル数、`min_lifter_samples` を下限とする）
    の独立実装。
    """
    analysis = _analysis_window(signal)
    n = len(analysis)
    window = np.hanning(n)
    framed = analysis * window

    spec = np.fft.fft(framed)
    eps = 1e-12
    log_mag_db = 20.0 * np.log10(np.abs(spec) + eps)
    cep = np.real(np.fft.ifft(log_mag_db))

    if np.isfinite(f0_hz) and f0_hz > 0:
        cutoff = int(lifter_ratio * sr / f0_hz)
    else:
        cutoff = 64
    cutoff = max(cutoff, min_lifter_samples)
    cutoff = min(cutoff, n // 2 - 1)
    cutoff = max(cutoff, 1)

    liftered = np.zeros_like(cep)
    liftered[:cutoff] = cep[:cutoff]
    if cutoff > 1:
        liftered[-(cutoff - 1) :] = cep[-(cutoff - 1) :]

    env_db_full = np.real(np.fft.fft(liftered))
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    env_db = env_db_full[: len(freqs)]
    return freqs, env_db


def _local_peaks(freqs: np.ndarray, env_db: np.ndarray, band_lo: float, band_hi: float) -> list[float]:
    lo_idx = int(np.searchsorted(freqs, band_lo))
    hi_idx = int(np.searchsorted(freqs, band_hi))
    peaks: list[tuple[float, float]] = []
    for i in range(max(lo_idx, 1), min(hi_idx, len(env_db) - 1)):
        if env_db[i] >= env_db[i - 1] and env_db[i] >= env_db[i + 1]:
            peaks.append((float(freqs[i]), float(env_db[i])))
    peaks.sort(key=lambda p: p[0])
    return [p[0] for p in peaks]


def formant_poles(
    signal: np.ndarray,
    sr: int,
    f0_hz: float,
    *,
    lifter_ratio: float,
    min_lifter_samples: int,
    band_hi: float,
    band_lo: float = 300.0,
) -> list[float]:
    """帯域内ローカルピーク周波数を昇順で返す（F1, F2, F3, ... の候補）。"""
    freqs, env_db = cepstral_envelope_db(
        signal, sr, f0_hz, lifter_ratio=lifter_ratio, min_lifter_samples=min_lifter_samples
    )
    return _local_peaks(freqs, env_db, band_lo, band_hi)


def measure(signal: np.ndarray, sr: int, params: Mapping[str, object]) -> MeterOutput:
    f0_hz = float(params.get("f0_hz", float("nan")))
    lifter_ratio = float(params["lifter_ratio"])
    min_lifter_samples = int(params["min_lifter_samples"])
    band_hi = float(params["band_hi"])

    peaks = formant_poles(
        signal,
        sr,
        f0_hz,
        lifter_ratio=lifter_ratio,
        min_lifter_samples=min_lifter_samples,
        band_hi=band_hi,
    )
    if not peaks:
        return MeterOutput(missing_reason=vocab.MissingReason.OUTPUT_MISSING)
    values = {f"f{i + 1}_hz": float(freq) for i, freq in enumerate(peaks[:3])}
    return MeterOutput(values=values, diagnostics={"n_peaks_found": len(peaks)})
