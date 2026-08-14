"""R0.1 — v0.3 design memo §C のレンダラ改修。

`voice_r0.py`（v0、凍結・無改変）から VoiceGenome dataclass と応答関数
（register_mix / breathiness / harmonic_tilt_db_per_oct / formant_shift /
formant_filter_gain / voice_a / voice_b）をそのまま再利用する。差替えるのは
`render_note` のみ:

§C-1 修正: 息ノイズ（breathiness 成分）に、倍音源と同じ spectral tilt +
高次減衰フィルタを（フォルマント包絡フィルタの *前に*）適用する。v0 では
息ノイズがフォルマント整形のみを受け、倍音源より平坦なスペクトルを持って
いたため、breathiness ノブを動かすと spectral_centroid が大きく動く実
entanglement を生んでいた（VT-3 v1 実測: C3 で centroid +65%）。

§C-2: breathiness の 0.95 クランプは v0 の `voice_r0.breathiness()` に
既に実装済み（VT-1 v1 で発見した周期性消滅縮退への対策として先行導入して
いた）。本メモはこれを正式仕様に格上げするのみで、コード変更は不要
（`voice_r0.breathiness` をそのまま再利用する）。

§C-3: register 境界・フォルマント基準値等、上記以外のパラメータは v0 のまま
凍結する（voice_a() / voice_b() の Genome 定義をそのまま再利用）。
"""
from __future__ import annotations

import numpy as np

from voice_r0 import (  # noqa: F401  (voice_a/voice_b/dataclasses は再エクスポートし呼び出し側の互換性を保つ)
    NOTE_DUR_SEC,
    SR,
    MicroprosodyParams,
    NoiseParams,
    RangeParams,
    RegisterParams,
    ResonanceParams,
    SourceParams,
    VoiceGenome,
    breathiness,
    formant_filter_gain,
    formant_shift,
    harmonic_tilt_db_per_oct,
    register_mix,
    voice_a,
    voice_b,
)


def _noise_tilt_envelope_gain(
    freq_hz: np.ndarray,
    f0_hz: float,
    tilt_db_per_oct: float,
    extra_decay_start_harmonic: float,
    extra_decay_db_per_harmonic: float,
) -> np.ndarray:
    """倍音源の spectral tilt + 高次減衰モデルを連続スペクトルへ拡張する（§C-1）。

    離散調波モデルでは harmonic number k=1,2,3... に対して
        db(k) = tilt*log2(k) - extra_decay*max(0, k-start)
    と定義されていた（voice_r0.py 内 render_note）。ノイズは離散倍音を
    持たないため、k を連続変数 f/f0 として拡張する。

    [UNDERSPEC-v2] design memo は連続拡張の式を与えていない。k = f/f0 を
    そのまま使うと f<f0（基音未満の帯域）で log2(k)<0 となり、tilt が負
    （通常ケース）のとき利得が f→0 で発散してしまう。離散モデルはそもそも
    k>=1 でしか評価されないため、この拡張と矛盾しない範囲で k を 1.0 に
    下側クランプする（基音未満の帯域は「ロールオフなし＝平坦」として扱う）。
    """
    k = np.maximum(freq_hz / max(f0_hz, 1e-6), 1.0)
    db = tilt_db_per_oct * np.log2(k) - extra_decay_db_per_harmonic * np.maximum(
        0.0, k - extra_decay_start_harmonic
    )
    return 10.0 ** (db / 20.0)


def render_note(
    genome: VoiceGenome,
    midi: float,
    intensity: float = 0.7,
    duration_sec: float = NOTE_DUR_SEC,
    sr: int = SR,
) -> np.ndarray:
    """R0.1: v0 の render_note と同一構造だが、息ノイズの整形順序のみ修正する。"""
    n = int(round(duration_sec * sr))
    t = np.arange(n) / sr

    f0_base = 440.0 * 2.0 ** ((midi - 69.0) / 12.0)

    mp = genome.microprosody
    vibrato_cents = mp.vibrato_depth_cents * np.sin(2 * np.pi * mp.vibrato_rate_hz * t)

    rng = np.random.default_rng(mp.jitter_seed + int(round(midi * 100)))
    period_sec = 1.0 / max(f0_base, 1e-6)
    n_periods_est = max(int(duration_sec / period_sec), 8)
    jitter_walk_coarse = np.cumsum(rng.normal(0.0, 1.0, size=n_periods_est))
    jitter_walk_coarse = jitter_walk_coarse - np.linspace(0, jitter_walk_coarse[-1], n_periods_est)
    coarse_t = np.linspace(0, duration_sec, n_periods_est)
    jitter_cents = np.interp(t, coarse_t, jitter_walk_coarse) * mp.jitter_pct_of_period * 100.0 / 50.0

    f0_t = f0_base * (2.0 ** ((vibrato_cents + jitter_cents) / 1200.0))

    weights = register_mix(midi, genome.register)
    breath = breathiness(midi, intensity, weights, genome.noise)
    tilt_db_per_oct = harmonic_tilt_db_per_oct(midi, intensity, genome.source)
    formants = formant_shift(f0_base, genome.resonance)

    phase = 2 * np.pi * np.cumsum(f0_t) / sr

    harmonic_signal = np.zeros(n, dtype=np.float64)
    nyq = sr / 2.0
    src = genome.source
    for k in range(1, src.n_harmonics + 1):
        f_k = k * f0_base
        if f_k >= nyq * 0.95:
            break
        db = tilt_db_per_oct * np.log2(k) - src.extra_decay_db_per_harmonic * max(
            0, k - src.extra_decay_start_harmonic
        )
        amp_k = 10.0 ** (db / 20.0)
        filt_gain = formant_filter_gain(np.array([f_k]), formants, genome.resonance)[0]
        harmonic_signal += amp_k * filt_gain * np.sin(k * phase)

    # --- §C-1: 息ノイズに tilt+高次減衰フィルタをフォルマント包絡の前に適用 ---
    freqs_full = np.fft.rfftfreq(n, d=1.0 / sr)
    noise_white = rng.normal(0.0, 1.0, size=n)
    noise_spec = np.fft.rfft(noise_white)

    tilt_gain = _noise_tilt_envelope_gain(
        freqs_full,
        f0_base,
        tilt_db_per_oct,
        src.extra_decay_start_harmonic,
        src.extra_decay_db_per_harmonic,
    )
    formant_gain = formant_filter_gain(freqs_full, formants, genome.resonance)
    noise_shaped = np.fft.irfft(noise_spec * tilt_gain * formant_gain, n=n)

    harm_rms = float(np.sqrt(np.mean(harmonic_signal**2)) + 1e-12)
    noise_rms = float(np.sqrt(np.mean(noise_shaped**2)) + 1e-12)
    noise_shaped = noise_shaped * (harm_rms * breath / noise_rms)

    signal = harmonic_signal + noise_shaped

    fade_n = int(0.03 * sr)
    fade_n = min(fade_n, n // 4) if n >= 4 else 0
    if fade_n > 0:
        env = np.ones(n)
        env[:fade_n] = np.linspace(0, 1, fade_n)
        env[-fade_n:] = np.linspace(1, 0, fade_n)
        signal = signal * env

    target_rms = 0.1
    cur_rms = float(np.sqrt(np.mean(signal**2)) + 1e-12)
    if cur_rms > 1e-9:
        signal = signal * (target_rms / cur_rms)

    return signal.astype(np.float64)
