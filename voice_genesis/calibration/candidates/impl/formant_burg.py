"""M3-BURG-LPC algorithm family（設計正本 §8: 「唯一の独立 family」）。

Burg 法による LPC 係数推定 → 極（root）を安定領域から抽出 → 角周波数を
formant 周波数・帯域幅へ変換する。解析前に `fs' = 2*max_formant_hz` への
決定的リサンプルを必須とする（設計正本 §8 の明記事項）。

[UNDERSPEC-CAL-C03] 設計正本はグリッド（order/window/preemph/max_formant）
を凍結するのみで、以下の実装詳細までは規定しない。最も単純で決定論的な
選択を採り、ここに記録する:

- **リサンプラ**: `scipy.signal.resample_poly`（多相フィルタ、有理比
  `up/down` を `Fraction` で正確に求めて使用。FFT ベースの `resample` より
  端点アーチファクトが小さく決定論的）。
- **前処理（preemph）**: `preemph_hz` を 1 次ハイパスの時定数として解釈し、
  `alpha = exp(-2*pi*preemph_hz/fs')`、`y[n] = x[n] - alpha*x[n-1]` を適用
  する。`preemph_hz=0` は `alpha=1`（完全な差分器 = 最大限の高域強調）、
  `preemph_hz` が大きいほど `alpha` は 1 未満に小さくなり強調が弱まる
  （設計正本はこの 2 値グリッドの物理的意味を規定していないため、
  「周波数が大きいほど補正が弱い」という単調な対応のみを保証する）。
- **窓関数**: フレーム抽出後に Hamming 窓を適用する（LPC 分析の標準的
  選択）。`window` パラメタは窓関数名ではなく分析フレーム長 (ms) を指す
  （グリッド定義 `window {25,40}ms` の文言に従う）。
- **極選択**: 単位円内 (`|root|<1`) かつ虚部が正の根のみを候補とし、
  周波数昇順に並べて F1, F2, F3 ... として報告する（DC 近傍・
  Nyquist 近傍の縮退根は周波数 > 50Hz かつ帯域幅 < fs'/2 の物理的制約で除外）。
"""

from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction

import numpy as np
from scipy.signal import resample_poly
from scipy.signal.windows import hamming

from ... import vocab
from ..adapter import MeterOutput


def _deterministic_resample(signal: np.ndarray, sr: int, target_sr: int) -> np.ndarray:
    """有理比 `up/down` を Fraction で正確に求めて `resample_poly` を適用する。"""
    frac = Fraction(target_sr, sr).limit_denominator(1000)
    return resample_poly(signal, frac.numerator, frac.denominator)


def _preemphasis(signal: np.ndarray, sr: int, preemph_hz: float) -> np.ndarray:
    alpha = float(np.exp(-2.0 * np.pi * preemph_hz / sr))
    out = np.empty_like(signal)
    out[0] = signal[0]
    out[1:] = signal[1:] - alpha * signal[:-1]
    return out


def _burg_lpc(frame: np.ndarray, order: int) -> np.ndarray:
    """Burg 法による LPC 係数 (a[1..order], `a[0]=1` を含む長さ order+1 の配列)。

    標準的な Burg 再帰（前方・後方予測誤差を同時最小化する反射係数の逐次推定）。
    """
    x = np.asarray(frame, dtype=float)
    n = len(x)
    order = min(order, n - 1)
    if order < 1:
        return np.array([1.0])

    f = x.copy()
    b = x.copy()
    a = np.zeros(order + 1)
    a[0] = 1.0
    dk = float(np.sum(f[1:] ** 2) + np.sum(b[:-1] ** 2))

    for k in range(order):
        f_k = f[k + 1 :]
        b_k = b[k:-1]
        if dk <= 1e-20 or len(f_k) == 0:
            break
        num = 2.0 * float(np.dot(f_k, b_k))
        reflection = num / dk
        reflection = float(np.clip(reflection, -0.999999, 0.999999))

        a_prev = a.copy()
        for i in range(1, k + 2):
            a[i] = a_prev[i] - reflection * a_prev[k + 1 - i]

        f_new = f_k - reflection * b_k
        b_new = b_k - reflection * f_k
        f[k + 1 :] = f_new
        b[k:-1] = b_new

        dk = (1.0 - reflection**2) * dk - f[k + 1] ** 2 - b[-1] ** 2
        dk = max(dk, 1e-20)

    return a[: order + 1]


def formant_poles_burg(
    signal: np.ndarray,
    sr: int,
    *,
    order: int,
    window_ms: float,
    preemph_hz: float,
    max_formant_hz: float,
) -> list[tuple[float, float]]:
    """Burg LPC の安定極から `(frequency_hz, bandwidth_hz)` のリスト（周波数昇順）を返す。"""
    target_sr = int(round(2.0 * max_formant_hz))
    resampled = _deterministic_resample(np.asarray(signal, dtype=float), sr, target_sr)
    preemph = _preemphasis(resampled, target_sr, preemph_hz)

    frame_len = int(window_ms / 1000.0 * target_sr)
    frame_len = min(frame_len, len(preemph))
    if frame_len < order + 2:
        return []
    start = max((len(preemph) - frame_len) // 2, 0)
    frame = preemph[start : start + frame_len]
    frame = frame * hamming(len(frame))

    a = _burg_lpc(frame, order)
    if len(a) < 2:
        return []
    roots = np.roots(a)
    formants: list[tuple[float, float]] = []
    for root in roots:
        if root.imag <= 0 or abs(root) >= 1.0 or abs(root) <= 1e-9:
            continue
        angle = float(np.angle(root))
        freq = angle * target_sr / (2.0 * np.pi)
        bandwidth = -target_sr / np.pi * float(np.log(abs(root)))
        if freq <= 50.0 or freq >= target_sr / 2.0:
            continue
        if bandwidth <= 0.0 or bandwidth >= target_sr / 2.0:
            continue
        formants.append((freq, bandwidth))
    formants.sort(key=lambda p: p[0])
    return formants


def measure(signal: np.ndarray, sr: int, params: Mapping[str, object]) -> MeterOutput:
    order = int(params["order"])
    window_ms = float(params["window_ms"])
    preemph_hz = float(params["preemph_hz"])
    max_formant_hz = float(params["max_formant_hz"])

    formants = formant_poles_burg(
        signal,
        sr,
        order=order,
        window_ms=window_ms,
        preemph_hz=preemph_hz,
        max_formant_hz=max_formant_hz,
    )
    if not formants:
        return MeterOutput(missing_reason=vocab.MissingReason.OUTPUT_MISSING)
    values: dict[str, float] = {}
    for i, (freq, bw) in enumerate(formants[:3]):
        values[f"f{i + 1}_hz"] = float(freq)
        values[f"f{i + 1}_bandwidth_hz"] = float(bw)
    return MeterOutput(values=values, diagnostics={"n_poles_found": len(formants)})
