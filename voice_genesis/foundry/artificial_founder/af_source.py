"""af_source.py — AF0 の有声源・HL-α・決定論 PRNG・F0 軌道（設計書 §9.1 / §9.2 / §22）。

**このモジュールは「源」だけを持つ。** 母音共鳴（`af_filter`）・包絡と残光
（`af_expression`）を混ぜない。将来 P1 で source だけを変異させられるようにする
ためで、設計書 §22 が明示的に要求する分離である。

OS entropy は一切使わない。`numpy.random` も使わない（実装・版差でビット列が
変わりうる）。PRNG は xorshift64* を自前実装し、シードは
`af_spec.unit_seed`（SHA256 由来）から取る。
"""
from __future__ import annotations

from typing import Tuple

import numpy as np

_MASK64 = (1 << 64) - 1
_XORSHIFT_MULT = 0x2545F4914F6CDD1D


def xorshift64star(seed: int, n: int) -> np.ndarray:
    """xorshift64* で `n` 個の一様乱数 `[-1, 1)` を返す（§9.2 推奨 PRNG）。

    状態遷移・出力語長・[-1,1) への写像を固定しているので、Python / numpy の
    版に依らずビット同一の系列を返す（float64 への変換のみ IEEE754 に依存する
    が、これは §27 で固定する前提と同じ）。
    """
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    state = int(seed) & _MASK64
    if state == 0:
        raise ValueError("xorshift64* seed must be non-zero")
    out = np.empty(n, dtype=np.float64)
    scale = 1.0 / float(1 << 63)
    for i in range(n):
        state ^= (state >> 12) & _MASK64
        state = (state ^ ((state << 25) & _MASK64)) & _MASK64
        state ^= (state >> 27) & _MASK64
        value = (state * _XORSHIFT_MULT) & _MASK64
        # 上位 64bit のうち符号付き 64bit として解釈し [-1, 1) へ線形写像する。
        signed = value - (1 << 64) if value >= (1 << 63) else value
        out[i] = signed * scale
    return out


def deterministic_noise(founder_id: str, alias: str, component: str,
                        n: int, global_seed: int = 0) -> np.ndarray:
    """unit / 成分ごとに領域分離した決定論ノイズ（§9.2）。"""
    from af_spec import unit_seed  # 局所 import: af_spec -> af_source の循環を避ける

    return xorshift64star(unit_seed(founder_id, alias, component, global_seed), n)


# ---------------------------------------------------------------------------
# §9.1 HL-α — Harmonic Lattice
# ---------------------------------------------------------------------------
def lattice_multipliers(harmonic_count: int, odd_multiplier: float,
                        even_multiplier: float) -> np.ndarray:
    """HL-α の倍音別係数（h=1..H。奇数=odd_multiplier / 偶数=even_multiplier）。"""
    h = np.arange(1, harmonic_count + 1, dtype=np.float64)
    return np.where(h % 2 == 1, odd_multiplier, even_multiplier)


def harmonic_amplitudes(harmonic_count: int, rolloff_power: float,
                        odd_multiplier: float, even_multiplier: float) -> np.ndarray:
    """`A(h) = h^-p × lattice_multiplier(h)`（§9.1）。"""
    h = np.arange(1, harmonic_count + 1, dtype=np.float64)
    base = h ** (-float(rolloff_power))
    return base * lattice_multipliers(harmonic_count, odd_multiplier, even_multiplier)


def schroeder_phases(amplitudes: np.ndarray) -> np.ndarray:
    """Schroeder (1970) の低クレスト位相列。

    位相方式を固定するのは**クリップ回避のため**であり形質ではない。振幅比 /
    スペクトル包絡には影響しないので HL-α・formant・AR の測定値は不変。
    """
    power = amplitudes ** 2
    total = float(power.sum())
    if total <= 0.0:
        return np.zeros_like(amplitudes)
    p = power / total
    phases = np.empty_like(amplitudes)
    phases[0] = 0.0
    acc = 0.0
    for i in range(1, amplitudes.size):
        acc += p[i - 1]
        phases[i] = phases[i - 1] - 2.0 * np.pi * acc
    return phases


def harmonic_phases(scheme: str, amplitudes: np.ndarray) -> np.ndarray:
    if scheme == "schroeder":
        return schroeder_phases(amplitudes)
    if scheme == "zero":
        return np.zeros_like(amplitudes)
    raise ValueError(f"unknown harmonic_phase_scheme {scheme!r}")


# ---------------------------------------------------------------------------
# §9.1 F0 軌道（jitter=0 / vibrato=0 / terminal fall のみ）
# ---------------------------------------------------------------------------
def f0_trajectory(n: int, sr: int, core_f0_hz: float, terminal_start: int,
                  terminal_end: int, delta_cents: float) -> np.ndarray:
    """core F0 を保ち、terminal window でのみ `delta_cents` へ半コサインで下降する。

    設計書 §10 の並び（terminal F0 fall → main amplitude taper）に従い、**F0 の
    下降は articulation の末尾で完了し、release taper 中は下降後の値を保持する**。
    そのため terminal delta は「振幅がまだ十分ある領域」で測定でき、taper 末端の
    無信号区間で F0 を推定する必要がない。
    """
    f0 = np.full(n, float(core_f0_hz), dtype=np.float64)
    if delta_cents == 0.0 or terminal_end <= terminal_start:
        return f0
    lo = max(0, terminal_start)
    hi = min(n, terminal_end)
    span = terminal_end - terminal_start
    final_ratio = 2.0 ** (float(delta_cents) / 1200.0)
    if hi > lo:
        u = (np.arange(lo, hi, dtype=np.float64) - terminal_start) / float(span)
        shape = 0.5 * (1.0 - np.cos(np.pi * u))
        f0[lo:hi] = core_f0_hz * (2.0 ** (float(delta_cents) * shape / 1200.0))
    if hi < n:
        f0[hi:] = core_f0_hz * final_ratio
    return f0


def integrate_phase(f0: np.ndarray, sr: int) -> np.ndarray:
    """瞬時 F0 から基本位相 `Φ[n] = 2π/sr · Σ f0` を作る（§9.1「時間積分した位相」）。"""
    return (2.0 * np.pi / float(sr)) * np.cumsum(f0)


def harmonic_source(f0: np.ndarray, sr: int, amplitudes: np.ndarray,
                    phases: np.ndarray) -> np.ndarray:
    """`Σ_h A(h)·sin(h·Φ(t) + φ_h)`（§9.1）。Nyquist 以上の倍音は生成しない。"""
    phi = integrate_phase(f0, sr)
    out = np.zeros(f0.size, dtype=np.float64)
    nyquist = 0.5 * sr
    f0_max = float(np.max(f0)) if f0.size else 0.0
    for i, amp in enumerate(amplitudes):
        h = i + 1
        if amp == 0.0 or h * f0_max >= nyquist:
            continue
        out += amp * np.sin(h * phi + phases[i])
    return out


def voiced_source(founder_id: str, alias: str, n: int, sr: int, core_f0_hz: float,
                  terminal_start: int, terminal_end: int, delta_cents: float,
                  harmonic_count: int, rolloff_power: float, odd_multiplier: float,
                  even_multiplier: float, phase_scheme: str,
                  breath_db_rel: float, global_seed: int = 0) -> Tuple[np.ndarray, np.ndarray]:
    """AF0 の有声源 1 本を作り `(source, f0_trajectory)` を返す。

    breath noise（§2「minimal and deterministic」）は**声道フィルタの手前**で
    混ぜる。これは (a) 実際の呼気雑音と同じ経路であること、(b) 倍音間の連続
    スペクトルが声道 + AR の応答をそのまま写すので、F0 = 261.6 Hz の粗い倍音
    格子だけでは分離できない F1（例 /i/ の 280 Hz）を測定器が盲目のまま回収
    できること、の 2 点による（`DESIGN_AF_P0.md` §A-1）。
    """
    amps = harmonic_amplitudes(harmonic_count, rolloff_power, odd_multiplier, even_multiplier)
    phases = harmonic_phases(phase_scheme, amps)
    f0 = f0_trajectory(n, sr, core_f0_hz, terminal_start, terminal_end, delta_cents)
    tone = harmonic_source(f0, sr, amps, phases)
    tone_rms = float(np.sqrt(np.mean(tone ** 2))) if tone.size else 0.0
    if breath_db_rel is not None and tone_rms > 0.0:
        noise = deterministic_noise(founder_id, alias, "breath", n, global_seed)
        noise_rms = float(np.sqrt(np.mean(noise ** 2)))
        if noise_rms > 0.0:
            noise *= (tone_rms * (10.0 ** (float(breath_db_rel) / 20.0))) / noise_rms
        tone = tone + noise
    return tone, f0
