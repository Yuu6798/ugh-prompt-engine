"""af_filter.py — 母音 F1/F2/F3 と Founder Identity AR-α / AR-β（設計書 §9.3 / §22）。

**AR は F1/F2/F3 と同一フィルタへ潰さない**（§9.3）。normal vowel identity と
founder identity を測定・変異の両方で分離できるようにするため、AR は独立 stage
であり、さらに AR-α は「複素モーダル分岐」として実装する。

AR-α をモーダル分岐にしている理由は 2 つある。

1. §10 の Afterglow は「残光は共鳴器の decay として表現する」と定義されている。
   モーダル状態（複素振幅）を持っていれば、main 側が digital zero に落ちた
   あとも AR-α だけを位相連続に鳴らし続けられる。
2. 残光区間は AR-α 単独の純音になるため、測定器は F3 が近い母音（/i/: F3=3300 Hz
   に対し AR-α=3400 Hz）でも AR-α の中心を盲目のまま同定できる。F0=261.6 Hz の
   倍音格子では sustain スペクトルからこの 2 極を分離できない（`DESIGN_AF_P0.md`
   §A-2）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

import numpy as np
from scipy.signal import lfilter


# ---------------------------------------------------------------------------
# 2 極共鳴器（母音 formant）
# ---------------------------------------------------------------------------
def resonator_coeffs(center_hz: float, bandwidth_hz: float, sr: int) -> Tuple[float, float, float]:
    """共鳴周波数で振幅 1 に正規化した 2 極共鳴器 `(b0, a1, a2)` を返す。"""
    if not (0.0 < center_hz < 0.5 * sr):
        raise ValueError(f"resonator center {center_hz} Hz out of range for sr={sr}")
    if bandwidth_hz <= 0.0:
        raise ValueError(f"resonator bandwidth must be > 0, got {bandwidth_hz}")
    r = float(np.exp(-np.pi * bandwidth_hz / sr))
    theta = 2.0 * np.pi * center_hz / sr
    a1 = -2.0 * r * float(np.cos(theta))
    a2 = r * r
    z = np.exp(-1j * theta)
    denom = 1.0 + a1 * z + a2 * z * z
    b0 = 1.0 / float(np.abs(1.0 / denom))
    return b0, a1, a2


def apply_resonator(x: np.ndarray, center_hz: float, bandwidth_hz: float, sr: int) -> np.ndarray:
    b0, a1, a2 = resonator_coeffs(center_hz, bandwidth_hz, sr)
    return lfilter([b0], [1.0, a1, a2], x)


#: 並列 formant バンクの分岐極性（Holmes 型並列合成の慣行）。谷での打ち消しを
#: 避けるため交互に符号を反転する。
PARALLEL_POLARITY: Tuple[float, ...] = (1.0, -1.0, 1.0, -1.0, 1.0)


def apply_formant_parallel(x: np.ndarray, formants_hz: Sequence[float],
                           bandwidths_hz: Sequence[float], sr: int) -> np.ndarray:
    """F1/F2/F3 の**並列**バンク（§9.3 の母音 stage）。AR とは別 stage。

    縦続（cascade）にすると F3 より上が -12 dB/oct × 3 段 = -36 dB/oct で落ち、
    /u/ や /o/ では 5 kHz 帯が -115 dB（16bit 量子化床 -96 dBFS より下）へ沈む。
    AR-β（5100 Hz）は原理的に測定不能になり、§18 の `AR-β center ±90 Hz` を
    満たしようがない。並列バンクなら F3 より上の裾は 1 段分だけなので、実音声と
    同程度の高域ダイナミックレンジが残る（`DESIGN_AF_P0.md` §A-3）。
    """
    y = np.zeros_like(np.asarray(x, dtype=np.float64))
    for i, (f, bw) in enumerate(zip(formants_hz, bandwidths_hz)):
        y = y + PARALLEL_POLARITY[i % len(PARALLEL_POLARITY)] * apply_resonator(x, f, bw, sr)
    return y


def formant_parallel_response(freqs_hz: np.ndarray, formants_hz: Sequence[float],
                              bandwidths_hz: Sequence[float], sr: int) -> np.ndarray:
    """並列 formant バンクの複素周波数応答（校正・診断用。音は作らない）。"""
    w = 2.0 * np.pi * np.asarray(freqs_hz, dtype=np.float64) / sr
    z = np.exp(-1j * w)
    h = np.zeros_like(z, dtype=np.complex128)
    for i, (f, bw) in enumerate(zip(formants_hz, bandwidths_hz)):
        b0, a1, a2 = resonator_coeffs(f, bw, sr)
        h = h + PARALLEL_POLARITY[i % len(PARALLEL_POLARITY)] * (b0 / (1.0 + a1 * z + a2 * z * z))
    return h


# ---------------------------------------------------------------------------
# Founder resonance — 複素モーダル分岐
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ModalBranch:
    """AR 分岐の出力と、残光継続に必要な複素モーダル状態。"""

    signal: np.ndarray          # 2·Re(y[n])（gain 未適用）
    state: np.ndarray           # 複素モーダル振幅 y[n]
    gain: float                 # 合成時に掛ける係数
    center_hz: float
    bandwidth_hz: float
    sr: int

    def afterglow(self, from_index: int, n_samples: int) -> np.ndarray:
        """`from_index` 時点のモーダル状態から `n_samples` 分の残光を生成する。

        減衰は付けない（自然減衰は τ=1/(π·BW)≈1.4 ms なので 35 ms 残らない）。
        残光の減衰は `af_expression` の AR 専用包絡が与える = §10 の
        「AR-α だけを 35 ms 長く残す」を包絡で表現する設計。
        """
        if n_samples <= 0:
            return np.zeros(0, dtype=np.float64)
        idx = min(max(from_index, 0), self.state.size - 1)
        y0 = self.state[idx]
        omega = 2.0 * np.pi * self.center_hz / self.sr
        k = np.arange(1, n_samples + 1, dtype=np.float64)
        return 2.0 * np.real(y0 * np.exp(1j * omega * k))


def modal_branch(x: np.ndarray, center_hz: float, bandwidth_hz: float, sr: int,
                 gain: float) -> ModalBranch:
    """複素 1 極共鳴器 `y[n] = p·y[n-1] + x[n]`（p = r·e^{jω}）の分岐を作る。"""
    if not (0.0 < center_hz < 0.5 * sr):
        raise ValueError(f"AR center {center_hz} Hz out of range for sr={sr}")
    if bandwidth_hz <= 0.0:
        raise ValueError(f"AR bandwidth must be > 0, got {bandwidth_hz}")
    r = float(np.exp(-np.pi * bandwidth_hz / sr))
    omega = 2.0 * np.pi * center_hz / sr
    p = r * np.exp(1j * omega)
    y = lfilter([1.0 + 0j], [1.0 + 0j, -p], np.asarray(x, dtype=np.complex128))
    return ModalBranch(signal=2.0 * np.real(y), state=y, gain=float(gain),
                       center_hz=float(center_hz), bandwidth_hz=float(bandwidth_hz), sr=sr)


def modal_branch_response(freqs_hz: np.ndarray, center_hz: float, bandwidth_hz: float,
                          sr: int) -> np.ndarray:
    """`2·Re(y)` 分岐（gain 未適用）の複素周波数応答。"""
    r = float(np.exp(-np.pi * bandwidth_hz / sr))
    omega = 2.0 * np.pi * center_hz / sr
    p = r * np.exp(1j * omega)
    z = np.exp(-1j * 2.0 * np.pi * np.asarray(freqs_hz, dtype=np.float64) / sr)
    return 1.0 / (1.0 - p * z) + 1.0 / (1.0 - np.conj(p) * z)


def solve_branch_gain_for_peak_db(center_hz: float, bandwidth_hz: float, sr: int,
                                  gain_db: float) -> float:
    """`|1 + g·H_branch(ω_c)| = 10^(gain_db/20)` を満たす実 gain を閉形式で解く。"""
    target = 10.0 ** (float(gain_db) / 20.0)
    h = complex(modal_branch_response(np.array([center_hz]), center_hz, bandwidth_hz, sr)[0])
    a = abs(h) ** 2
    b = 2.0 * h.real
    c = 1.0 - target * target
    disc = b * b - 4.0 * a * c
    if a <= 0.0 or disc < 0.0:
        raise ValueError("no real branch gain reaches the requested AR peak gain")
    return (-b + float(np.sqrt(disc))) / (2.0 * a)


def combined_prominence_db(gain: float, center_hz: float, bandwidth_hz: float, sr: int,
                           probe_freqs_hz: np.ndarray) -> float:
    """`probe_freqs` 上での `20log10|1 + g·H_branch|` の最大値（= 設計上の卓立）。"""
    h = modal_branch_response(probe_freqs_hz, center_hz, bandwidth_hz, sr)
    mag = np.abs(1.0 + gain * h)
    return float(20.0 * np.log10(np.max(mag)))


def harmonic_grid(core_f0_hz: float, harmonic_count: int, band_hz: Tuple[float, float],
                  sr: int) -> np.ndarray:
    """帯域内の倍音周波数（測定器が実際に「見る」点）。"""
    h = np.arange(1, harmonic_count + 1, dtype=np.float64)
    f = h * float(core_f0_hz)
    lo, hi = band_hz
    m = (f >= lo) & (f <= hi) & (f < 0.5 * sr)
    return f[m]


def calibrate_ar_beta_gain(alpha_gain: float, alpha_center_hz: float, alpha_bw_hz: float,
                           beta_center_hz: float, beta_bw_hz: float, sr: int,
                           beta_alpha_energy_ratio: float) -> Tuple[float, dict]:
    """AR-β の gain を「β/α エネルギー比 = 目標値」から決定論的に逆算する。

    §8 の AR-β は **gain_db を持たず relative target しか持たない**。したがって
    gain は仕様値ではなく仕様から導出される量であり、その導出をここへ 1 箇所だけ
    置く。導出は AR フィルタ自身の応答だけを使い、母音にも測定コードにも依存しない。

    β/α エネルギー比の定義（事前登録。`criteria.metric_definitions.envelope_model`
    が当てる peaking 山の頂点と同じ量）:

    ```text
    ratio      = 10 ** ((prom_beta_db - prom_alpha_db) / 10)
    prom_X_db  = 20log10|1 + g_X · H_X(f_X)|      (各共鳴の中心での卓立)
    ```

    測定側は Lorentzian peaking 山の**頂点**を推定するので、校正側も中心での
    卓立で定義を合わせる（倍音格子上の最大値で定義すると、5100 Hz の最寄り倍音が
    129 Hz ずれている分だけ系統的に食い違う）。
    """
    prom_alpha = combined_prominence_db(alpha_gain, alpha_center_hz, alpha_bw_hz, sr,
                                        np.array([alpha_center_hz]))
    target_prom_beta = prom_alpha + 10.0 * float(np.log10(beta_alpha_energy_ratio))
    gain = solve_branch_gain_for_peak_db(beta_center_hz, beta_bw_hz, sr, target_prom_beta)
    detail = {
        "alpha_prominence_db": prom_alpha,
        "beta_prominence_db": combined_prominence_db(gain, beta_center_hz, beta_bw_hz, sr,
                                                     np.array([beta_center_hz])),
        "beta_target_prominence_db": target_prom_beta,
        "beta_gain": gain,
    }
    return gain, detail


# ---------------------------------------------------------------------------
# 子音成形用の汎用フィルタ（§9.4。native-likeness は目的ではない）
# ---------------------------------------------------------------------------
def one_pole_highpass(x: np.ndarray, cutoff_hz: float, sr: int) -> np.ndarray:
    a = float(np.exp(-2.0 * np.pi * cutoff_hz / sr))
    return lfilter([0.5 * (1.0 + a), -0.5 * (1.0 + a)], [1.0, -a], x)


def one_pole_lowpass(x: np.ndarray, cutoff_hz: float, sr: int) -> np.ndarray:
    a = float(np.exp(-2.0 * np.pi * cutoff_hz / sr))
    return lfilter([1.0 - a], [1.0, -a], x)


def bandpass(x: np.ndarray, center_hz: float, bandwidth_hz: float, sr: int) -> np.ndarray:
    """共鳴器 - 直流成分の簡易バンドパス（子音ノイズ整形用）。"""
    return apply_resonator(x, center_hz, bandwidth_hz, sr)
