from __future__ import annotations

import numpy as np
import pytest

from voice_genesis.calibration.fixtures.generators import (
    aperiodicity,
    f0_control,
    formant,
    identity_sweep,
    render_row,
    resonance,
    tilt,
    transition,
)
from voice_genesis.calibration.fixtures.matrix import FixtureRow
from voice_genesis.calibration.streams import derive_generator

SECRET = b"\x42" * 32
SR = 48000


def _rng(row_id: str = "row-1", purpose: str = "generator") -> np.random.Generator:
    return derive_generator(
        SECRET,
        campaign_id="RUN10-CAL",
        family="TEST",
        split="CALIBRATION",
        row_id=row_id,
        probe_index=0,
        purpose=purpose,
    )


def _f0_row(**overrides: object) -> FixtureRow:
    base = dict(
        family="F0_CONTROL",
        block="TRUTH_CORE",
        f0_hz=261.626,
        sr_hz=SR,
        gain_dbfs=-12.0,
        duration_s=0.3,
        noise_clean=True,
        noise_snr_db=None,
        context="steady-isolated",
    )
    base.update(overrides)
    return FixtureRow(**base)


def _tilt_row(**overrides: object) -> FixtureRow:
    base = dict(
        family="TILT_GT",
        block="TRUTH_CORE",
        f0_hz=130.813,
        sr_hz=SR,
        gain_dbfs=-12.0,
        duration_s=0.3,
        noise_clean=True,
        noise_snr_db=None,
        context="steady-isolated",
        slope_db_per_oct=-12.0,
    )
    base.update(overrides)
    return FixtureRow(**base)


def _aperiodicity_row(**overrides: object) -> FixtureRow:
    base = dict(
        family="APERIODICITY_GT",
        block="TRUTH_CORE",
        f0_hz=130.813,
        sr_hz=SR,
        gain_dbfs=-12.0,
        duration_s=0.3,
        noise_clean=True,
        noise_snr_db=None,
        context="steady-isolated",
        injected_noise_fraction=0.30,
        bandwise_band=None,
    )
    base.update(overrides)
    return FixtureRow(**base)


def _formant_row(impl: str, **overrides: object) -> FixtureRow:
    base = dict(
        family="FORMANT_GT",
        block="TRUTH_CORE",
        f0_hz=130.813,
        sr_hz=SR,
        gain_dbfs=-12.0,
        duration_s=0.3,
        noise_clean=True,
        noise_snr_db=None,
        context="steady-isolated",
        pole_freqs_hz=(500.0, 1900.0, 2600.0),
        bandwidth_hz=100.0,
        generator_impl=impl,
    )
    base.update(overrides)
    return FixtureRow(**base)


def _resonance_row(**overrides: object) -> FixtureRow:
    base = dict(
        family="RESONANCE_GT",
        block="TRUTH_CORE",
        f0_hz=261.626,
        sr_hz=SR,
        gain_dbfs=-12.0,
        duration_s=0.3,
        noise_clean=True,
        noise_snr_db=None,
        context="steady-isolated",
        center_hz=2000.0,
        resonance_bandwidth_hz=150.0,
        prominence_db=12.0,
    )
    base.update(overrides)
    return FixtureRow(**base)


def _transition_row(**overrides: object) -> FixtureRow:
    base = dict(
        family="TRANSITION_GT",
        block="TRUTH_CORE",
        f0_hz=261.626,
        sr_hz=SR,
        gain_dbfs=-12.0,
        duration_s=0.4,
        noise_clean=True,
        noise_snr_db=None,
        context="steady-isolated",
        join_type="amplitude-step",
        severity="high",
        duration_class="long",
        join_time_s=0.2,
        discontinuity_magnitude=0.65,
    )
    base.update(overrides)
    return FixtureRow(**base)


def _identity_row(**overrides: object) -> FixtureRow:
    base = dict(
        family="IDENTITY_CAUSAL_SWEEP",
        block="TRUTH_CORE",
        f0_hz=130.813,
        sr_hz=SR,
        gain_dbfs=-12.0,
        duration_s=0.3,
        noise_clean=True,
        noise_snr_db=None,
        context="steady-isolated",
        founder_id="F1",
        trait="F0",
        delta=0,
    )
    base.update(overrides)
    return FixtureRow(**base)


ALL_SAMPLE_ROWS = [
    _f0_row(),
    _tilt_row(),
    _aperiodicity_row(),
    _formant_row("cascade"),
    _formant_row("additive"),
    _resonance_row(),
    _transition_row(),
    _identity_row(),
]


# ---------------------------------------------------------------------------
# 決定性: 同 seed -> byte-identical PCM (in-process 2 回)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("row", ALL_SAMPLE_ROWS, ids=lambda r: f"{r.family}:{r.generator_impl}")
def test_same_seed_gives_byte_identical_pcm(row: FixtureRow) -> None:
    pcm_a = render_row(row, _rng())
    pcm_b = render_row(row, _rng())
    assert np.array_equal(pcm_a, pcm_b)
    assert pcm_a.dtype == np.int16


@pytest.mark.parametrize("row", ALL_SAMPLE_ROWS, ids=lambda r: f"{r.family}:{r.generator_impl}")
def test_different_seed_gives_different_pcm_for_noise_bearing_rows(row: FixtureRow) -> None:
    pcm_a = render_row(row, _rng("row-1"))
    pcm_b = render_row(row, _rng("row-2"))
    # F0_CONTROL/TILT/FORMANT の truth core は乱数を消費しない
    # (harmonic complex は解析的、noise_clean=True) ため row_id を変えても
    # 同一 PCM になりうる。乱数を消費する family (APERIODICITY/RESONANCE) のみ
    # 差異を要求する。
    if row.family in ("APERIODICITY_GT", "RESONANCE_GT"):
        assert not np.array_equal(pcm_a, pcm_b)


# ---------------------------------------------------------------------------
# 独立オラクル: F0_CONTROL -> FFT peak
# ---------------------------------------------------------------------------


def test_f0_control_fft_peak_near_declared_f0() -> None:
    row = _f0_row(f0_hz=261.626, duration_s=0.5)
    pcm = f0_control.render(row, _rng())
    x = pcm.astype(np.float64) / 32767.0
    freqs = np.fft.rfftfreq(len(x), 1.0 / row.sr_hz)
    mag = np.abs(np.fft.rfft(x))
    peak_freq = freqs[np.argmax(mag)]
    bin_width = row.sr_hz / len(x)
    assert abs(peak_freq - row.f0_hz) <= 2 * bin_width


# ---------------------------------------------------------------------------
# 独立オラクル: TILT_GT -> fitted log-magnitude slope
# ---------------------------------------------------------------------------


def test_tilt_fitted_slope_matches_declared_within_tolerance() -> None:
    row = _tilt_row(slope_db_per_oct=-18.0, duration_s=0.5)
    pcm = tilt.render(row, _rng())
    x = pcm.astype(np.float64) / 32767.0
    n = len(x)
    freqs = np.fft.rfftfreq(n, 1.0 / row.sr_hz)
    spectrum = np.abs(np.fft.rfft(x))

    ks = np.arange(1, 8)
    harm_freqs = ks * row.f0_hz
    mags = np.array([spectrum[np.argmin(np.abs(freqs - hf))] for hf in harm_freqs])
    mag_db = 20.0 * np.log10(mags / mags[0])
    fitted_slope, _intercept = np.polyfit(np.log2(ks), mag_db, 1)

    assert abs(fitted_slope - row.slope_db_per_oct) < 2.0  # dB/oct


# ---------------------------------------------------------------------------
# 独立オラクル: APERIODICITY_GT -> noise power fraction
# ---------------------------------------------------------------------------


def test_aperiodicity_noise_power_fraction_within_tolerance() -> None:
    row = _aperiodicity_row(injected_noise_fraction=0.30, duration_s=0.5)
    pcm = aperiodicity.render(row, _rng())
    x = pcm.astype(np.float64) / 32767.0
    n = len(x)
    spectrum = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, 1.0 / row.sr_hz)

    total_power = float(np.sum(np.abs(spectrum) ** 2))
    harmonic_power = 0.0
    k = 1
    while k * row.f0_hz < 0.45 * row.sr_hz:
        idx = int(np.argmin(np.abs(freqs - k * row.f0_hz)))
        lo, hi = max(0, idx - 2), idx + 3
        harmonic_power += float(np.sum(np.abs(spectrum[lo:hi]) ** 2))
        k += 1
    estimated_fraction = (total_power - harmonic_power) / total_power

    assert abs(estimated_fraction - row.injected_noise_fraction) < 0.15


def test_aperiodicity_fraction_zero_is_much_less_noisy_than_fraction_high() -> None:
    def _estimate_fraction(fraction: float) -> float:
        row = _aperiodicity_row(injected_noise_fraction=fraction, duration_s=0.5)
        pcm = aperiodicity.render(row, _rng())
        x = pcm.astype(np.float64) / 32767.0
        n = len(x)
        spectrum = np.fft.rfft(x)
        freqs = np.fft.rfftfreq(n, 1.0 / row.sr_hz)
        total_power = float(np.sum(np.abs(spectrum) ** 2))
        harmonic_power = 0.0
        k = 1
        while k * row.f0_hz < 0.45 * row.sr_hz:
            idx = int(np.argmin(np.abs(freqs - k * row.f0_hz)))
            lo, hi = max(0, idx - 2), idx + 3
            harmonic_power += float(np.sum(np.abs(spectrum[lo:hi]) ** 2))
            k += 1
        return (total_power - harmonic_power) / total_power

    low = _estimate_fraction(0.0)
    high = _estimate_fraction(0.60)
    assert low < high


# ---------------------------------------------------------------------------
# 独立オラクル: FORMANT_GT -> 2 実装の |H| peak が declared pole 近傍に一致
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("impl", ["cascade", "additive"])
def test_formant_spectral_peaks_near_declared_poles(impl: str) -> None:
    poles = (500.0, 1900.0, 2600.0)
    row = _formant_row(impl, pole_freqs_hz=poles, f0_hz=130.813, duration_s=0.5)
    pcm = formant.render(row, _rng())
    x = pcm.astype(np.float64) / 32767.0
    n = len(x)
    freqs = np.fft.rfftfreq(n, 1.0 / row.sr_hz)
    mag = np.abs(np.fft.rfft(x * np.hanning(n)))

    for pole_hz in poles:
        window = (freqs > pole_hz - 200) & (freqs < pole_hz + 200)
        assert window.any()
        peak_freq = freqs[window][np.argmax(mag[window])]
        assert abs(peak_freq - pole_hz) <= 200.0


def test_formant_cascade_and_additive_agree_on_peak_locations() -> None:
    poles = (500.0, 1900.0, 2600.0)
    row_cascade = _formant_row("cascade", pole_freqs_hz=poles, f0_hz=130.813, duration_s=0.5)
    row_additive = _formant_row("additive", pole_freqs_hz=poles, f0_hz=130.813, duration_s=0.5)

    def _peaks(row: FixtureRow) -> list[float]:
        pcm = formant.render(row, _rng())
        x = pcm.astype(np.float64) / 32767.0
        n = len(x)
        freqs = np.fft.rfftfreq(n, 1.0 / row.sr_hz)
        mag = np.abs(np.fft.rfft(x * np.hanning(n)))
        out = []
        for pole_hz in poles:
            window = (freqs > pole_hz - 200) & (freqs < pole_hz + 200)
            out.append(float(freqs[window][np.argmax(mag[window])]))
        return out

    peaks_cascade = _peaks(row_cascade)
    peaks_additive = _peaks(row_additive)
    for pc, pa in zip(peaks_cascade, peaks_additive):
        assert abs(pc - pa) < 20.0


# ---------------------------------------------------------------------------
# RESONANCE_GT: 宣言 center 近傍にスペクトルピークが存在
# ---------------------------------------------------------------------------


def test_resonance_spectral_peak_near_declared_center() -> None:
    row = _resonance_row(center_hz=2000.0, resonance_bandwidth_hz=150.0, duration_s=0.5)
    pcm = resonance.render(row, _rng())
    x = pcm.astype(np.float64) / 32767.0
    n = len(x)
    freqs = np.fft.rfftfreq(n, 1.0 / row.sr_hz)
    mag_db = 20.0 * np.log10(np.abs(np.fft.rfft(x * np.hanning(n))) + 1e-9)

    near = (freqs > row.center_hz - 100) & (freqs < row.center_hz + 100)
    far = (freqs > 200) & (freqs < row.center_hz - 500)
    assert near.any() and far.any()
    assert mag_db[near].max() > np.median(mag_db[far]) + 3.0


# ---------------------------------------------------------------------------
# TRANSITION_GT: join 前後で振幅の不連続が検出できる
# ---------------------------------------------------------------------------


def test_transition_amplitude_step_is_detectable_at_join_time() -> None:
    row = _transition_row(
        join_type="amplitude-step", severity="high", duration_s=0.4, join_time_s=0.2,
        discontinuity_magnitude=0.65,
    )
    pcm = transition.render(row, _rng())
    x = pcm.astype(np.float64) / 32767.0
    join_sample = int(round(row.join_time_s * row.sr_hz))
    window = int(0.02 * row.sr_hz)
    pre_rms = float(np.sqrt(np.mean(x[join_sample - window : join_sample] ** 2)))
    post_rms = float(np.sqrt(np.mean(x[join_sample : join_sample + window] ** 2)))
    assert post_rms > pre_rms * 1.1


# ---------------------------------------------------------------------------
# IDENTITY_CAUSAL_SWEEP: delta=0 は founder baseline と一致 (trait 無関係)
# ---------------------------------------------------------------------------


def test_identity_delta_zero_is_independent_of_trait_choice() -> None:
    row_f0 = _identity_row(founder_id="F1", trait="F0", delta=0, duration_s=0.3)
    row_tilt = _identity_row(founder_id="F1", trait="TILT_SLOPE", delta=0, duration_s=0.3)
    pcm_f0 = identity_sweep.render(row_f0, _rng())
    pcm_tilt = identity_sweep.render(row_tilt, _rng())
    assert np.array_equal(pcm_f0, pcm_tilt)


def test_identity_f0_delta_shifts_fundamental() -> None:
    row_base = _identity_row(founder_id="F1", trait="F0", delta=0, duration_s=0.5)
    row_shifted = _identity_row(founder_id="F1", trait="F0", delta=2, duration_s=0.5)
    pcm_base = identity_sweep.render(row_base, _rng())
    pcm_shifted = identity_sweep.render(row_shifted, _rng())
    assert not np.array_equal(pcm_base, pcm_shifted)


# ---------------------------------------------------------------------------
# negative control: silence is silent / noise-only has no harmonic comb
# ---------------------------------------------------------------------------


def test_silence_negative_control_is_silent() -> None:
    row = _f0_row(block="NEGATIVE_CONTROL", control_class="SILENCE", duration_s=0.2)
    pcm = f0_control.render(row, _rng())
    assert np.max(np.abs(pcm)) == 0


def test_noise_only_negative_control_has_no_harmonic_comb() -> None:
    row = _f0_row(block="NEGATIVE_CONTROL", control_class="NOISE_ONLY", duration_s=0.5)
    pcm = f0_control.render(row, _rng())
    x = pcm.astype(np.float64)
    mag = np.abs(np.fft.rfft(x))
    # harmonic comb (band-limited pulse train) は少数ビンに極端に集中する。
    # white noise はビン間で比較的平坦: peak/median 比が低い。
    assert (mag.max() / np.median(mag)) < 15.0


def test_pure_sine_negative_control_renders_single_tone_for_formant() -> None:
    row = _formant_row(
        "cascade",
        block="NEGATIVE_CONTROL",
        control_class="PURE_SINE",
        f0_hz=261.626,
        duration_s=0.5,
    )
    pcm = formant.render(row, _rng())
    x = pcm.astype(np.float64) / 32767.0
    n = len(x)
    freqs = np.fft.rfftfreq(n, 1.0 / row.sr_hz)
    mag = np.abs(np.fft.rfft(x))
    # 単一正弦なので基本波近傍にほぼ全パワーが集中する。
    fundamental_idx = int(np.argmin(np.abs(freqs - row.f0_hz)))
    total_power = float(np.sum(mag**2))
    fundamental_power = float(np.sum(mag[max(0, fundamental_idx - 2) : fundamental_idx + 3] ** 2))
    assert fundamental_power / total_power > 0.9
