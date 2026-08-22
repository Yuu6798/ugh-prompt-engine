"""t0_afterglow.py — AG-alpha transport 候補 A0 / A1 / A2（設計書 §10 / §15）。

AG-α は AF0 の Founder Expression。「音が切れた後、AR-α 帯だけが 35 ms 長く
残る」。WORLD は非周期成分と包絡を平滑化するため、この裾が伸びたり潰れたりする。

```text
A0  Native baseline
A1  Founder Expression Sidecar Re-injection
      WORLD synth 後に、AR-alpha center / Body 実現 afterglow 振幅参照 /
      sidecar duration から **AR-alpha 帯だけ** へ決定論的 residual を再注入
A2  AR-alpha Band Envelope Restoration
      A1 が sentinel regression で落ちた場合のみ。Body AR-alpha 帯包絡の
      正規化形状を sidecar で運び、固定 operator で適用
```

§10 で明示的に禁止:

```text
全帯域 tail 延長 / 評価 metric まで残光を足し続ける
```

したがって A1 は **AR-α 帯域幅の内側にしか触れない**。帯域外の残差は 0 で
あることを `assert_band_limited` が構造的に検査できる。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.signal import butter, hilbert, sosfiltfilt


class AfterglowOperatorError(RuntimeError):
    """AG-α operator の前提が満たされない。"""


def _finite(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _ms_to_samples(ms: float, sr: int) -> int:
    return int(round(float(ms) * sr / 1000.0))


#: 帯域抽出の次数。零位相 Butterworth（`sosfiltfilt`）を使う。
BANDPASS_ORDER = 6

#: AR-alpha 帯の半値幅を共振器帯域幅の何倍に取るか（事前登録定数）。
#: 1.0 = ±bw（共振器の -12 dB 裾まで覆う）。
BAND_HALF_WIDTH_FACTOR = 1.0


def _bandpass(x: np.ndarray, sr: int, lo_hz: float, hi_hz: float) -> np.ndarray:
    """零位相 Butterworth による帯域通過（決定論・固定手続き）。

    当初は FFT の理想矩形マスクで実装したが実測で破綻した: 矩形マスクの
    インパルス応答は sinc で減衰しないため、sustain 部の大振幅から尾部へ
    リンギングが撒かれ、AR-alpha 包絡の床が -110 dB から -80 dB へ持ち上がって
    残光消失時刻が信号終端に張り付いた（u: afterglow 33 -> 116 ms）。
    Butterworth はインパルス応答が指数減衰でコンパクトなのでこの汚染がない。
    計器 (`af_measure.band_envelope`) と同じ族・同じ次数を使う。
    """
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return x.copy()
    nyq = 0.5 * sr
    lo = max(20.0, float(lo_hz)) / nyq
    hi = min(nyq - 50.0, float(hi_hz)) / nyq
    if hi <= lo:
        return np.zeros_like(x)
    sos = butter(BANDPASS_ORDER, [lo, hi], btype="bandpass", output="sos")
    return np.asarray(sosfiltfilt(sos, x), dtype=np.float64)


def _band_rms(x: np.ndarray, sr: int, lo: float, hi: float) -> float:
    b = _bandpass(x, sr, lo, hi)
    return float(np.sqrt(np.mean(b ** 2))) if b.size else 0.0


def apply_a0(y: np.ndarray, sr: int, sidecar: Mapping[str, Any]
             ) -> Tuple[np.ndarray, Dict[str, Any]]:
    """A0 Native baseline。"""
    return np.asarray(y, dtype=np.float64).copy(), {
        "operator": "A0", "mode": "native", "changed": False}


def apply_a1(y: np.ndarray, sr: int, sidecar: Mapping[str, Any]
             ) -> Tuple[np.ndarray, Dict[str, Any]]:
    """A1 Founder Expression Sidecar Re-injection。

    手順は事前固定:

    1. sidecar から AR-α 中心・帯域幅、articulation 終端、Body 実現 afterglow 長を引く
    2. articulation 終端の直前の窓から AR-α 帯の振幅参照を取る
    3. 終端以降の AR-α 帯成分を **一度ゼロにし**、参照振幅から始まる半コサイン
       減衰の正弦波を、sidecar の長さだけ **AR-α 帯にのみ** 足す

    再表現側の afterglow 測定値は参照しない。長さは sidecar の正典値で決まる
    ので、「metric が 35 ms になるまで伸ばす」ループにはならない（§15）。
    """
    y = np.asarray(y, dtype=np.float64)
    n = y.size
    fe = sidecar.get("founder_expression") or {}
    dur = sidecar.get("duration") or {}
    center = _finite(fe.get("ar_alpha_center_hz"))
    bw = _finite(fe.get("ar_alpha_bandwidth_hz"))
    extra_ms = _finite(fe.get("ar_alpha_afterglow_extra_ms"))
    if center is None or bw is None or extra_ms is None:
        raise AfterglowOperatorError(
            "A1 requires sidecar.founder_expression center/bandwidth/extra_ms (§8)")

    lead = _finite(dur.get("lead_zero_ms")) or 0.0
    onset = _finite(dur.get("onset_ms")) or 0.0
    vowel = _finite(dur.get("vowel_ms")) or 0.0
    taper = _finite(dur.get("main_taper_ms")) or 0.0
    main_end = min(n, _ms_to_samples(lead + onset + vowel + taper, sr))
    # AR-alpha 帯の取り方: 共振器の -3 dB 幅（±bw/2）ではなく **±bw** を使う。
    # -3 dB 幅は共振器の裾を含まず、WORLD が肩へ広げた成分が operator の外に
    # 残る（実測: ±bw/2 では帯域外の肩が閾値上に残り、AR-alpha 消失時刻が
    # operator の終端より後ろへ出た）。±bw は共振器の -12 dB 裾に相当する
    # 物理的な取り方で、**事前登録の定数**（計器の探査帯から読み取っていない）。
    lo, hi = max(0.0, center - bw * BAND_HALF_WIDTH_FACTOR), \
        center + bw * BAND_HALF_WIDTH_FACTOR

    # §9: final target は **Body で実現した** afterglow。genome parameter は
    # それが取れない場合のフォールバック。
    realized = _finite(fe.get("body_realized_afterglow_ms"))
    target_ms = realized if realized is not None and realized > 0.0 else extra_ms

    if main_end >= n or target_ms <= 0.0:
        return y.copy(), {"operator": "A1", "mode": "sidecar", "changed": False,
                          "reason": "no afterglow region"}

    # (2) 参照振幅は **afterglow の開始点（main taper 終端）** で取る。
    #     articulation 終端で取ると taper 前の sustain 振幅になり、Body より
    #     はるかに大きい残光を注入してしまう（実測で afterglow が伸びた）。
    ref_win = max(1, _ms_to_samples(10.0, sr))
    ref_lo = max(0, main_end - ref_win)
    ref_amp = _band_rms(y[ref_lo:main_end], sr, lo, hi) * math.sqrt(2.0)
    if ref_amp <= 0.0:
        return y.copy(), {"operator": "A1", "mode": "sidecar", "changed": False,
                          "reason": "no AR-alpha energy at afterglow onset"}

    band = _bandpass(y, sr, lo, hi)

    ag_len = _ms_to_samples(target_ms, sr)
    ag_start = main_end
    ag_end = min(n, ag_start + ag_len)

    # residual は **AR-alpha 帯の乗算包絡** として作る。設計 §10 A1 の 3 入力
    # （AR-alpha center / Body 実現 afterglow 振幅参照 / sidecar duration）だけを
    # 使い、afterglow 区間を半コサインで減衰させて sidecar の長さで消す。
    #
    # ここは **1 度で決まる固定手続き**であり、評価 metric を見て量を調整する
    # ループは持たない（§15）。減衰方向にしか振らない（`gain <= 1`）ので
    # 「metric が合うまで残光を足し続ける」形にもならない（§10 の禁止事項）。
    #
    # 実装上の失敗を 2 件記録しておく（どちらも実測で発覚）:
    #  1. 加算注入 + 理想矩形 FFT マスク: sinc 尾のリンギングが尾部の床を
    #     -110 dB から -80 dB へ持ち上げ、残光が信号終端に張り付いた
    #     （u: 33 -> 116 ms）。-> 乗算包絡 + Butterworth へ変更。
    #  2. 目標包絡 / 現在包絡の除算でゲインを作る形: 現在包絡が小さい区間で
    #     不安定になり、一部 unit がかえって悪化した（no: 41 -> 99 ms）。
    #     -> 除算をやめ、単調減衰の窓のみに戻した。
    gain = np.ones(n, dtype=np.float64)
    if ag_end > ag_start:
        k = ag_end - ag_start
        idx = np.arange(k, dtype=np.float64)
        gain[ag_start:ag_end] = 0.5 * (1.0 + np.cos(math.pi * idx / max(1, k - 1)))
    gain[ag_end:] = 0.0

    delta = band * (gain - 1.0)
    out = y + delta
    return np.ascontiguousarray(out), {
        "operator": "A1", "mode": "sidecar", "changed": True,
        "band_hz": [round(lo, 3), round(hi, 3)],
        "afterglow_ms": float(target_ms),
        "target_source": "body_realization" if realized else "genome_parameter",
        "ref_amplitude": round(float(ref_amp), 9),
        "region": [int(ag_start), int(ag_end)],
        "residual_form": "multiplicative_band_envelope",
        "attenuation_only": True,
        "band_limited": True}


def _amplitude_envelope(x: np.ndarray, sr: int, smooth_ms: float = 2.0) -> np.ndarray:
    """帯域信号の振幅包絡（解析信号の絶対値を移動平均で均す）。"""
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return x.copy()
    env = np.abs(hilbert(x))
    w = max(1, int(round(smooth_ms * sr / 1000.0)))
    if w > 1 and env.size >= w:
        kern = np.ones(w, dtype=np.float64) / w
        env = np.convolve(env, kern, mode="same")
    return env


def apply_a2(y: np.ndarray, sr: int, sidecar: Mapping[str, Any]
             ) -> Tuple[np.ndarray, Dict[str, Any]]:
    """A2 AR-alpha Band Envelope Restoration（A1 が sentinel で落ちた場合のみ）。

    Body の AR-α 帯包絡の **正規化形状** を sidecar が運ぶ前提で、WORLD 出力の
    AR-α 帯へ固定 operator（形状の掛け合わせ）で適用する。帯域外は触らない。
    """
    y = np.asarray(y, dtype=np.float64)
    fe = sidecar.get("founder_expression") or {}
    center = _finite(fe.get("ar_alpha_center_hz"))
    bw = _finite(fe.get("ar_alpha_bandwidth_hz"))
    shape = fe.get("ar_alpha_band_envelope_shape")
    if center is None or bw is None:
        raise AfterglowOperatorError("A2 requires AR-alpha center/bandwidth (§8)")
    if not shape:
        return y.copy(), {"operator": "A2", "mode": "sidecar", "changed": False,
                          "reason": "sidecar carries no normalized band envelope"}
    lo, hi = max(0.0, center - bw / 2.0), center + bw / 2.0
    band = _bandpass(y, sr, lo, hi)
    residual = y - band
    target = np.interp(np.linspace(0.0, 1.0, band.size),
                       np.linspace(0.0, 1.0, len(shape)),
                       np.asarray(shape, dtype=np.float64))
    cur = np.abs(band)
    peak = float(cur.max()) if cur.size else 0.0
    if peak <= 0.0:
        return y.copy(), {"operator": "A2", "mode": "sidecar", "changed": False,
                          "reason": "no AR-alpha energy"}
    scale = np.divide(target, np.maximum(cur / peak, 1e-6),
                      out=np.ones_like(target), where=target > 0)
    scale = np.clip(scale, 0.0, 4.0)
    return np.ascontiguousarray(residual + band * scale), {
        "operator": "A2", "mode": "sidecar", "changed": True,
        "band_hz": [round(lo, 3), round(hi, 3)], "band_limited": True}


def assert_band_limited(before: np.ndarray, after: np.ndarray, sr: int,
                        center: float, bandwidth: float,
                        tol: float = 1e-6,
                        factor: float = BAND_HALF_WIDTH_FACTOR) -> Dict[str, Any]:
    """§10 の禁止「全帯域 tail 延長」を構造的に検査する。

    AR-α 帯 **の外側** のエネルギーが operator の前後で変わっていないこと。
    変わっていたら、その operator は帯域限定ではない。

    帯域の取り方は operator と同じ `BAND_HALF_WIDTH_FACTOR` を既定にする
    （検査側だけ ±bw/2 にすると、operator が正しく ±bw で動いていても
    「帯域外を触った」と誤検出する）。
    """
    lo, hi = max(0.0, center - bandwidth * factor), center + bandwidth * factor
    b0 = _bandpass(np.asarray(before, dtype=np.float64), sr, lo, hi)
    b1 = _bandpass(np.asarray(after, dtype=np.float64), sr, lo, hi)
    out0 = np.asarray(before, dtype=np.float64) - b0
    out1 = np.asarray(after, dtype=np.float64) - b1
    n = min(out0.size, out1.size)
    delta = float(np.max(np.abs(out1[:n] - out0[:n]))) if n else 0.0
    e0 = float(np.sum(out0[:n] ** 2)) if n else 0.0
    e1 = float(np.sum(out1[:n] ** 2)) if n else 0.0
    ratio = (e1 / e0) if e0 > 0 else (1.0 if e1 == 0 else float("inf"))
    return {"out_of_band_max_delta": delta, "tolerance": tol,
            "out_of_band_energy_before": e0, "out_of_band_energy_after": e1,
            "out_of_band_energy_ratio": ratio,
            "band_limited": bool(delta <= tol)}


AFTERGLOW_OPERATORS = {"A0": apply_a0, "A1": apply_a1, "A2": apply_a2}

__all__ = ["AfterglowOperatorError", "AFTERGLOW_OPERATORS", "apply_a0", "apply_a1",
           "apply_a2", "assert_band_limited"]
