"""fixture generator 共通処理（IMPLEMENTATION_MAP §2 module table）:
PCM 16-bit 量子化（最終 byte-determinism 境界）・dBFS gain 適用・20ms cosine
ramp・100ms voiced prefix/suffix context 組み立て・transition-adjacent
context・declared SNR での noise mixing（streams 由来の `np.random.Generator`
を使用）。

全関数は `(row, rng)` を除き副作用を持たない純粋関数。float64 内部演算 → 本
モジュールで最終的に PCM int16 へ量子化する（設計正本 §6: 「float 中間から PCM
量子化する場合は最終 PCM で一致」の一致対象を本モジュールの出力とする）。
"""

from __future__ import annotations

import numpy as np


def n_samples(duration_s: float, sr_hz: int) -> int:
    return max(1, int(round(duration_s * sr_hz)))


def rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x))))


def peak_normalize(x: np.ndarray) -> np.ndarray:
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak <= 0.0:
        return x
    return x / peak


def harmonic_pulse_train(
    f0_hz: float, sr_hz: int, n: int, *, cutoff_ratio: float = 0.45
) -> np.ndarray:
    """band-limited harmonic pulse train: harmonics up to `cutoff_ratio*sr_hz`,
    amplitude `1/k`（f0_control / aperiodicity 生成器の共通励起）。
    """
    t = np.arange(n, dtype=np.float64) / sr_hz
    x = np.zeros(n, dtype=np.float64)
    k = 1
    cutoff = cutoff_ratio * sr_hz
    while k * f0_hz < cutoff:
        x += (1.0 / k) * np.sin(2.0 * np.pi * k * f0_hz * t)
        k += 1
    return peak_normalize(x)


def apply_gain_dbfs(x: np.ndarray, gain_dbfs: float) -> np.ndarray:
    """peak を 1.0 に正規化してから `gain_dbfs` の peak 振幅へスケールする。"""
    normalized = peak_normalize(x)
    target_amp = 10.0 ** (gain_dbfs / 20.0)
    return normalized * target_amp


def cosine_ramp_envelope(n: int, ramp_samples: int) -> np.ndarray:
    ramp_samples = max(0, min(ramp_samples, n // 2))
    env = np.ones(n, dtype=np.float64)
    if ramp_samples == 0:
        return env
    ramp_in = 0.5 * (1.0 - np.cos(np.pi * np.arange(ramp_samples) / ramp_samples))
    env[:ramp_samples] = ramp_in
    env[n - ramp_samples :] = ramp_in[::-1]
    return env


def apply_cosine_ramp(x: np.ndarray, sr_hz: int, ramp_s: float) -> np.ndarray:
    ramp_samples = int(round(ramp_s * sr_hz))
    return x * cosine_ramp_envelope(x.size, ramp_samples)


def make_voiced_tone(f0_hz: float, sr_hz: int, duration_s: float) -> np.ndarray:
    """context 組み立て用の一定音高トーン（真値の対象ではないため単純な
    band-limited pulse train を再利用する）。"""
    n = n_samples(duration_s, sr_hz)
    return harmonic_pulse_train(f0_hz, sr_hz, n) * 0.5


def assemble_context(core: np.ndarray, *, sr_hz: int, context: str, f0_hz: float) -> np.ndarray:
    """§5.1 の 4 context 水準（steady-isolated / 20ms-cosine-ramp /
    100ms-voiced-prefix-suffix / transition-adjacent）を適用する。"""
    if context == "steady-isolated":
        return core
    if context == "20ms-cosine-ramp":
        return apply_cosine_ramp(core, sr_hz, 0.020)
    if context == "100ms-voiced-prefix/suffix":
        prefix = make_voiced_tone(f0_hz, sr_hz, 0.100)
        suffix = make_voiced_tone(f0_hz, sr_hz, 0.100)
        return np.concatenate([prefix, core, suffix])
    if context == "transition-adjacent":
        # 隣接する別トーンをクロスフェードなしで直接連結し、"境界の近傍"という
        # 文脈ナイサンスを表現する（TRANSITION_GT 自身の truth-bearing join とは
        # 別物: あくまで外的な framing nuisance）。
        adjacent = make_voiced_tone(f0_hz * 1.5, sr_hz, 0.050)
        return np.concatenate([core, adjacent])
    raise ValueError(f"unknown context level: {context!r}")


def add_noise_at_snr(
    core: np.ndarray,
    rng: np.random.Generator,
    *,
    noise_clean: bool,
    noise_snr_db: float | None,
) -> np.ndarray:
    if noise_clean or noise_snr_db is None:
        return core
    signal_power = float(np.mean(np.square(core))) if core.size else 0.0
    if signal_power <= 0.0:
        return core
    noise = rng.standard_normal(core.size)
    noise_power = float(np.mean(np.square(noise)))
    target_noise_power = signal_power / (10.0 ** (noise_snr_db / 10.0))
    scale = np.sqrt(target_noise_power / noise_power) if noise_power > 0 else 0.0
    return core + noise * scale


def quantize_pcm16(x: np.ndarray) -> np.ndarray:
    """PCM 16-bit 量子化（最終 byte-determinism 境界）。[-1, 1] へクリップして
    丸める。"""
    clipped = np.clip(x, -1.0, 1.0)
    return np.round(clipped * 32767.0).astype(np.int16)


def pcm16_bytes(x_int16: np.ndarray) -> bytes:
    return np.ascontiguousarray(x_int16, dtype=np.int16).tobytes()


def negative_control_core(
    row: "object", rng: np.random.Generator, n: int, f0_hz: float
) -> np.ndarray | None:
    """SILENCE / NOISE_ONLY / PURE_SINE の共通 negative control 波形。
    該当しない control_class（None・OUT_OF_BAND_POLE・TOO_SHORT・INVALID_SR）
    には `None` を返し、呼び出し側が family 固有の通常経路へフォールバックする
    （TOO_SHORT/INVALID_SR は duration_s/sr_hz が既に row 上で書き換わっている
    だけなので通常経路のままでよい）。
    """
    control_class = getattr(row, "control_class", None)
    if control_class == "SILENCE":
        return np.zeros(n, dtype=np.float64)
    if control_class == "NOISE_ONLY":
        return peak_normalize(rng.standard_normal(n))
    if control_class == "PURE_SINE":
        t = np.arange(n, dtype=np.float64) / row.sr_hz
        return np.sin(2.0 * np.pi * f0_hz * t)
    return None


def finalize(core: np.ndarray, row: "object", rng: np.random.Generator) -> np.ndarray:
    """gain -> context -> noise -> PCM16 の共通仕上げパイプライン。"""
    core = apply_gain_dbfs(core, row.gain_dbfs)
    core = assemble_context(core, sr_hz=row.sr_hz, context=row.context, f0_hz=row.f0_hz)
    core = add_noise_at_snr(core, rng, noise_clean=row.noise_clean, noise_snr_db=row.noise_snr_db)
    return quantize_pcm16(core)
