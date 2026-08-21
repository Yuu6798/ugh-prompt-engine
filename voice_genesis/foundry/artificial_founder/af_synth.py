"""af_synth.py — AF0 Founder Body の 1 unit を合成する（設計書 §9 / §10 / §22）。

`af_source`（源 + HL-α）、`af_filter`（母音 + AR）、`af_expression`（包絡 + 残光）を
束ねるだけで、**新しい形質をここで足さない**。子音（§9.4）は phonetic usability
のための最小実装であり、native-likeness を目的にしない。

出力は 44.1 kHz PCM16 の 1 unit と、その `UnitTruth`（設計値のタイムライン）。
`UnitTruth` は `af_compare` 側の照合に使う。**`af_measure` へは渡さない。**
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np

import af_filter as filt
import af_source as src
from af_expression import (build_envelopes, half_cosine_rise, normalization_gain,
                           regularity_gain)
from af_spec import AliasUnit, FounderGenome, UnitTimeline, timeline_for

#: 子音 onset 区間の振幅上限（sustain=1.0 に対する比）。
#: 測定器は「sustain の 50% を最後に上向き通過した点」を onset 境界とするので、
#: onset 区間は 0.5 を超えてはならない（§15.5 の ±2 ms 要求）。
ONSET_PEAK = 0.30

#: 立ち上がり / 立ち下がりのクリック抑制用フェード（ms）。
_FADE_MS = 0.5


@dataclass(frozen=True)
class UnitTruth:
    """1 unit の設計値（Ground Truth, §6）。measure 側からは決して読まれない。"""

    alias: str
    stem: str
    onset: str
    vowel: str
    sr: int
    n_samples: int
    lead_ms: float
    onset_ms: float
    vowel_ms: float
    articulation_ms: float
    main_release_ms: float
    afterglow_extra_ms: float
    tail_zero_ms: float
    core_f0_hz: float
    terminal_f0_delta_cents: float
    attack_ms: float
    sustain_dbfs: float
    formants_hz: Tuple[float, float, float]
    ar_alpha_center_hz: float
    ar_beta_center_hz: float
    beta_alpha_energy_ratio: float
    hl_even_odd_ratio: float
    peak_abs: float
    r_onset_share: float

    def as_dict(self) -> Dict[str, object]:
        return {
            "alias": self.alias, "stem": self.stem, "onset": self.onset, "vowel": self.vowel,
            "sr": self.sr, "n_samples": self.n_samples,
            "lead_ms": self.lead_ms, "onset_ms": self.onset_ms, "vowel_ms": self.vowel_ms,
            "articulation_ms": self.articulation_ms, "main_release_ms": self.main_release_ms,
            "afterglow_extra_ms": self.afterglow_extra_ms, "tail_zero_ms": self.tail_zero_ms,
            "core_f0_hz": self.core_f0_hz,
            "terminal_f0_delta_cents": self.terminal_f0_delta_cents,
            "attack_ms": self.attack_ms, "sustain_dbfs": self.sustain_dbfs,
            "formants_hz": list(self.formants_hz),
            "ar_alpha_center_hz": self.ar_alpha_center_hz,
            "ar_beta_center_hz": self.ar_beta_center_hz,
            "beta_alpha_energy_ratio": self.beta_alpha_energy_ratio,
            "hl_even_odd_ratio": self.hl_even_odd_ratio,
            "peak_abs": self.peak_abs, "r_onset_share": self.r_onset_share,
        }


@dataclass(frozen=True)
class SynthesizedUnit:
    unit: AliasUnit
    timeline: UnitTimeline
    samples_f64: np.ndarray
    pcm16: np.ndarray
    truth: UnitTruth


def _fade(n_total: int, fade: int, rising: bool) -> np.ndarray:
    w = np.ones(n_total, dtype=np.float64)
    f = min(fade, n_total)
    if f <= 0:
        return w
    ramp = half_cosine_rise(f)
    if rising:
        w[:f] = ramp
    else:
        w[n_total - f:] = ramp[::-1]
    return w


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x ** 2))) if x.size else 0.0


def _scale_to_rms(x: np.ndarray, target_rms: float) -> np.ndarray:
    r = _rms(x)
    return x * (target_rms / r) if r > 0.0 else x


def _onset_signal(genome: FounderGenome, unit: AliasUnit, tl: UnitTimeline,
                  source: np.ndarray, vowel_body: np.ndarray, rms_ref: float) -> np.ndarray:
    """§9.4 の最小子音実装。`source` / `vowel_body` は lead 起点の配列。

    `source` = 有声源（母音共鳴の手前）、`vowel_body` = 母音共鳴通過後、
    `rms_ref` = sustain 区間の `vowel_body` RMS。

    **全 onset の振幅は `rms_ref` 相対で与える。** 測定器は「sustain 包絡の 50%
    を最後に上向き通過した点」を onset 境界とするので、onset 区間の RMS が
    sustain の 0.5 を超えると境界が onset 内部へずれる（§15.5 の ±2 ms 要求）。
    絶対振幅で書くと母音ごとの共鳴利得差でこの不変条件が壊れる。
    """
    sr = tl.sr
    n = tl.onset_len
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    fade = max(1, int(round(_FADE_MS * sr / 1000.0)))
    out = np.zeros(n, dtype=np.float64)
    noise = src.deterministic_noise(genome.founder_id, unit.alias, f"onset:{unit.onset}", n,
                                    genome.global_seed)

    if unit.onset == "k":
        # closure（digital silence）+ deterministic burst。
        burst_start = int(round(0.65 * n))
        m = n - burst_start
        if m > 0:
            b = filt.bandpass(filt.one_pole_highpass(noise[burst_start:], 1200.0, sr),
                              2000.0, 1400.0, sr)
            decay = np.exp(-np.arange(m, dtype=np.float64) / max(1.0, 0.22 * m))
            out[burst_start:] = _scale_to_rms(b * decay, ONSET_PEAK * rms_ref)
    elif unit.onset in ("s", "sh"):
        center, bw = (6000.0, 3000.0) if unit.onset == "s" else (2800.0, 1600.0)
        f = filt.bandpass(filt.one_pole_highpass(noise, 2500.0 if unit.onset == "s" else 1500.0,
                                                 sr), center, bw, sr)
        f = _scale_to_rms(f, ONSET_PEAK * rms_ref)
        out[:] = f * _fade(n, fade, True) * _fade(n, fade, False)
    elif unit.onset == "n":
        # voiced source + nasal shaping（低域極 + 高域減衰）。
        nasal = filt.one_pole_lowpass(filt.apply_resonator(source[:n], 270.0, 120.0, sr),
                                      900.0, sr)
        nasal = _scale_to_rms(nasal, 0.28 * rms_ref)
        out[:] = nasal * _fade(n, fade, True)
    elif unit.onset == "r":
        # short tap/interruption + voiced transition（§9.4）。
        prof = np.empty(n, dtype=np.float64)
        a = int(round(0.30 * n))
        b = int(round(0.45 * n))
        prof[:a] = 0.22
        prof[a:b] = 0.01
        prof[b:] = 0.01 + (ONSET_PEAK - 0.01 - 0.02) * half_cosine_rise(max(1, n - b))
        out[:] = vowel_body[:n] * prof * _fade(n, fade, True)
    else:  # pragma: no cover - スキーマ側で弾かれる
        raise ValueError(f"unknown onset class {unit.onset!r}")
    return out


def synthesize_unit(genome: FounderGenome, unit: AliasUnit) -> SynthesizedUnit:
    """1 alias 分の Founder Body を決定論的に合成する。"""
    sr = genome.sample_rate_hz
    tl = timeline_for(genome, unit)
    n_voiced = tl.main_end - tl.lead

    # --- §9.1 有声源 + HL-α + F0 軌道 --------------------------------------
    source, _f0 = src.voiced_source(
        founder_id=genome.founder_id, alias=unit.alias, n=n_voiced, sr=sr,
        core_f0_hz=genome.core_f0_hz,
        terminal_start=tl.terminal_start - tl.lead, terminal_end=tl.art_end - tl.lead,
        delta_cents=genome.terminal_f0_delta_cents,
        harmonic_count=genome.harmonic_count, rolloff_power=genome.harmonic_rolloff_power,
        odd_multiplier=genome.hl_odd_multiplier, even_multiplier=genome.hl_even_multiplier,
        phase_scheme=genome.harmonic_phase_scheme,
        breath_db_rel=genome.breath_noise_db_rel_sustain, global_seed=genome.global_seed)

    # --- §9.3 母音共鳴 -> Founder Identity（別 stage）----------------------
    formants = genome.vowels[unit.vowel]
    vowel_body = filt.apply_formant_parallel(source, formants,
                                             genome.vowel_formant_bandwidths_hz, sr)
    g_alpha = filt.solve_branch_gain_for_peak_db(genome.ar_alpha_center_hz,
                                                 genome.ar_alpha_bandwidth_hz, sr,
                                                 genome.ar_alpha_gain_db)
    g_beta, _ = filt.calibrate_ar_beta_gain(
        g_alpha, genome.ar_alpha_center_hz, genome.ar_alpha_bandwidth_hz,
        genome.ar_beta_center_hz, genome.ar_beta_bandwidth_hz, sr,
        genome.beta_alpha_energy_ratio)
    br_alpha = filt.modal_branch(vowel_body, genome.ar_alpha_center_hz,
                                 genome.ar_alpha_bandwidth_hz, sr, g_alpha)
    br_beta = filt.modal_branch(vowel_body, genome.ar_beta_center_hz,
                                genome.ar_beta_bandwidth_hz, sr, g_beta)

    main_dry = vowel_body + g_beta * br_beta.signal
    ar_dry = g_alpha * br_alpha.signal

    # --- §10 包絡・残光 -----------------------------------------------------
    env = build_envelopes(genome, unit, tl)
    # Energy の平坦化ゲイン（§2 "Energy: highly regular"）。main と AR で同一の
    # 曲線を使うので AR/main のスペクトル比は保たれる。
    reg = regularity_gain(main_dry, sr, env.sustain_start - tl.lead, tl.art_end - tl.lead,
                          1.0 / genome.core_f0_hz)
    reg_full = np.empty(tl.total, dtype=np.float64)
    reg_full[tl.lead:tl.main_end] = reg
    reg_full[:tl.lead] = reg[0]
    reg_full[tl.main_end:] = reg[-1]

    y = np.zeros(tl.total, dtype=np.float64)
    y[tl.lead:tl.main_end] += main_dry * reg * env.main[tl.lead:tl.main_end]

    ar_full = np.zeros(tl.total, dtype=np.float64)
    ar_full[tl.lead:tl.main_end] = ar_dry
    n_glow = tl.ag_end - tl.main_end
    if n_glow > 0:
        ar_full[tl.main_end:tl.ag_end] = g_alpha * br_alpha.afterglow(n_voiced - 1, n_glow)
    y += ar_full * reg_full * env.ar_alpha

    # --- §9.4 子音 onset ----------------------------------------------------
    if unit.has_onset:
        rms_ref = _rms((vowel_body * reg)[env.sustain_start - tl.lead:env.sustain_end - tl.lead])
        y[tl.lead:tl.onset_end] += _onset_signal(genome, unit, tl, source, vowel_body, rms_ref)

    # --- Energy 正規化（sustain RMS = sustain_dbfs）------------------------
    y *= normalization_gain(y, env.sustain_start, env.sustain_end, genome.sustain_dbfs)
    y[tl.ag_end:] = 0.0
    y[:tl.lead] = 0.0

    peak = float(np.max(np.abs(y))) if y.size else 0.0
    pcm16 = np.round(np.clip(y, -1.0, 1.0) * 32767.0).astype(np.int16)

    truth = UnitTruth(
        alias=unit.alias, stem=unit.stem, onset=unit.onset, vowel=unit.vowel, sr=sr,
        n_samples=int(tl.total),
        lead_ms=tl.ms(tl.lead), onset_ms=tl.ms(tl.onset_len), vowel_ms=tl.ms(tl.vowel_len),
        articulation_ms=tl.ms(tl.art_end - tl.lead),
        main_release_ms=tl.ms(tl.main_end - tl.art_end),
        afterglow_extra_ms=tl.ms(tl.ag_end - tl.main_end),
        tail_zero_ms=tl.ms(tl.total - tl.ag_end),
        core_f0_hz=genome.core_f0_hz,
        terminal_f0_delta_cents=genome.terminal_f0_delta_cents,
        attack_ms=genome.attack_ms, sustain_dbfs=genome.sustain_dbfs,
        formants_hz=formants, ar_alpha_center_hz=genome.ar_alpha_center_hz,
        ar_beta_center_hz=genome.ar_beta_center_hz,
        beta_alpha_energy_ratio=genome.beta_alpha_energy_ratio,
        hl_even_odd_ratio=genome.hl_even_multiplier / genome.hl_odd_multiplier,
        peak_abs=peak,
        r_onset_share=(tl.onset_len / max(1, tl.art_end - tl.lead)) if unit.has_onset else 0.0,
    )
    return SynthesizedUnit(unit=unit, timeline=tl, samples_f64=y, pcm16=pcm16, truth=truth)


def synthesize_body(genome: FounderGenome) -> List[SynthesizedUnit]:
    """inventory 全 alias を合成する（順序は `inventory.aliases` のまま）。"""
    return [synthesize_unit(genome, u) for u in genome.units]
