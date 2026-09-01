"""RESONANCE_GT generator（設計正本 §4.2）: 宣言 pole/Q resonator を broadband
励起（white noise）に適用し、broadband floor に対する declared prominence
（dB）を実現する。truth = center/bandwidth/prominence。M3（formant）とは
異なる出力 construct を宣言（§4.2）— broadband 励起上の単一ピークであり、
harmonic pulse train 励起の pole/zero 構造（FORMANT_GT）とは生成経路が異なる。

prominence 実現則（Codex レビュー 2026-09-01 P2 改訂）: truth は
「LOCAL SPECTRAL PEAK PROMINENCE（宣言 center における spectral floor から
の dB）」であり、`peak_rms / floor_rms`（全帯域 RMS 比）ではない。同じ RMS
比でも `resonance_bandwidth_hz` が異なれば、その RMS が center 近傍にどれだけ
集中するかが変わるため、旧実装（RMS 比のみでスケール）は帯域幅が異なる行で
同じ declared prominence を宣言していても実現値が食い違っていた。

本実装は以下の決定論的な **one-shot 補正パス**でこれを是正する（反復探索
ではない）:

1. 従来どおり `peak_rms / floor_rms = 10**(prominence_db/20)` を満たす
   unit-pass の gain で peak 成分をスケールする。
2. `scipy.signal.welch`（固定 `nperseg`。データに依存しないため決定論を
   壊さない）で PSD を測り、(a) 宣言 center 近傍（`center_hz` ±
   `resonance_bandwidth_hz`/2）の peak レベルを **混合信号**上で、
   (b) spectral floor レベルを **floor 成分単独**上で測定する。

   floor を「混合信号上で center 近傍を除外した帯域」から測ろうとする素朴な
   実装は、このモジュールが使う 2-pole `iirpeak` resonator の skirt
   （roll-off）が Q（`= center_hz / resonance_bandwidth_hz`。本 registry の
   グリッドでは最大 70 に達する高 Q）に対して緩やかなため機能しない
   — 実測では center から ±32*bandwidth 離れた窓でも、真の noise floor より
   なお 5–20dB 高い（`resonance_bandwidth_hz` が小さいほど悪化する。実測は
   本ファイルのレビュー記録を参照）。floor 成分は加算前の broadband white
   noise そのもの（設計上スペクトル的にフラットで center に依存しない）
   であるため、その PSD を直接測ることが「skirt に汚染されない spectral
   floor」の最も直接的かつ頑健な参照になる。
3. 実現された prominence（`peak_db - floor_db`）と宣言値との不足分
   （shortfall, dB）を計算し、`10**(shortfall/20)` を peak 成分へ追加で
   一括乗算する（測定 → 補正の 1 パスのみ。確率的な繰り返し探索なし）。

**近似誤差と U_GT への寄与**: この 1-パス補正は「peak 窓内の PSD レベルは
floor 成分の寄与を無視できるほど peak 成分が支配的である」という線形近似に
基づく（peak 成分をスケール `c` 倍すると、peak 窓内のパワーはおおむね `c**2`
倍になる、という前提。floor 成分自身が peak 窓内にも常に存在するため、この
前提は宣言 prominence が小さい＝floor 寄与が相対的に無視できないほど、また
`welch` の周波数分解能が粗い（narrow bandwidth を数ビンでしか解像できない）
ほど劣化する）。この残差は `RESONANCE_GT` truth の `U_GT`（generator truth
の保守上限）へ計上すべき成分である（`tests/test_generators.py` の帯域端
(50Hz/300Hz, declared 12dB) 検証で実測される許容誤差 ±1.5dB が、この近似
誤差の実測上限の目安）。
"""

from __future__ import annotations

import numpy as np
from scipy.signal import iirpeak, lfilter, welch

from voice_genesis.calibration.fixtures.generators import common

_WELCH_NPERSEG_CAP = 2048
_PSD_FLOOR_EPS = 1e-20


def _welch_psd_db(x: np.ndarray, sr_hz: int) -> tuple[np.ndarray, np.ndarray]:
    """`(freqs, psd_db)` を固定 `nperseg`（データ内容に依存しない、`len(x)` と
    `_WELCH_NPERSEG_CAP` のみで決まる）で返す。決定論契約: 同一 `row`+seed
    → 同一 `len(x)` → 同一 `nperseg` → 同一 PSD grid。"""
    nperseg = int(min(len(x), _WELCH_NPERSEG_CAP))
    freqs, psd = welch(x, fs=sr_hz, nperseg=nperseg, window="hann", detrend=False)
    return freqs, 10.0 * np.log10(psd + _PSD_FLOOR_EPS)


def _measure_peak_db(mixed: np.ndarray, sr_hz: int, center_hz: float, bandwidth_hz: float) -> float:
    """`center_hz` ± `bandwidth_hz/2` 内の PSD 最大値（混合信号上で測る、
    実際に rendered される局所ピークレベル）。"""
    freqs, psd_db = _welch_psd_db(mixed, sr_hz)
    half_bw = bandwidth_hz / 2.0
    peak_mask = np.abs(freqs - center_hz) <= half_bw
    if not peak_mask.any():
        peak_mask = np.zeros_like(freqs, dtype=bool)
        peak_mask[int(np.argmin(np.abs(freqs - center_hz)))] = True
    return float(psd_db[peak_mask].max())


def _measure_floor_db(floor: np.ndarray, sr_hz: int) -> float:
    """floor 成分（broadband white noise, peak 混合前）単独の PSD 中央値。
    設計上スペクトル的にフラットなので、center 近傍に限定する必要がない
    （= 全帯域の中央値がそのまま local floor の頑健な推定になる。モジュール
    docstring 参照: 混合信号上の近傍窓から測ろうとすると resonator skirt に
    汚染される）。"""
    nyquist = sr_hz / 2.0
    freqs, psd_db = _welch_psd_db(floor, sr_hz)
    valid = (freqs >= 50.0) & (freqs <= nyquist * 0.95)
    return float(np.median(psd_db[valid]))


def _core(row: object, rng: np.random.Generator) -> np.ndarray:
    sr_hz = row.sr_hz
    n = common.n_samples(row.duration_s, sr_hz)
    floor = rng.standard_normal(n)

    center = row.center_hz
    bandwidth = row.resonance_bandwidth_hz
    nyquist = sr_hz / 2.0
    w0 = float(np.clip(center / nyquist, 1e-4, 0.999))
    q = max(center / bandwidth, 0.5)
    b, a = iirpeak(w0, q)
    peak_component = lfilter(b, a, rng.standard_normal(n))

    floor_rms = common.rms(floor) or 1e-9
    peak_rms = common.rms(peak_component) or 1e-9
    target_ratio = 10.0 ** (row.prominence_db / 20.0)
    scaled_peak = peak_component * (target_ratio * floor_rms / peak_rms)

    # one-shot 補正パス（測定 → 不足分算出 → 1 回だけ追加乗算。反復なし）。
    floor_db = _measure_floor_db(floor, sr_hz)
    unit_pass_peak_db = _measure_peak_db(floor + scaled_peak, sr_hz, center, bandwidth)
    shortfall_db = float(row.prominence_db) - (unit_pass_peak_db - floor_db)
    correction = 10.0 ** (shortfall_db / 20.0)

    mixed = floor + scaled_peak * correction
    return common.peak_normalize(mixed)


def render(row: object, rng: np.random.Generator) -> np.ndarray:
    n = common.n_samples(row.duration_s, row.sr_hz)
    core = common.negative_control_core(row, rng, n, row.f0_hz)
    if core is None:
        core = _core(row, rng)
    return common.finalize(core, row, rng)
