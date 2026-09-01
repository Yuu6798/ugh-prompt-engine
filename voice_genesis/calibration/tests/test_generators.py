from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import welch

from voice_genesis.calibration.fixtures.generators import (
    aperiodicity,
    common,
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
# declared gain は context 付加後の全体波形に適用される
# (Codex レビュー 2026-09-01 P1: fixtures/generators/common.py:153)
# ---------------------------------------------------------------------------


def test_context_segment_peak_matches_declared_gain_voiced_prefix() -> None:
    """-24dBFS row + `100ms-voiced-prefix/suffix` context: prefix/suffix
    segment の実現 peak が declared gain レベル（量子化許容誤差内）に一致する
    こと。修正前は `common.finalize()` が core 単体にのみ gain を適用してから
    固定振幅 (peak 0.5) の context トーンを追加していたため、-24dBFS row でも
    context 区間は常に -6dBFS 相当（`20*log10(0.5)`）に固定されていた。
    """
    row = _f0_row(f0_hz=261.626, duration_s=0.3, gain_dbfs=-24.0, context="100ms-voiced-prefix/suffix")
    pcm = f0_control.render(row, _rng())
    x = pcm.astype(np.float64) / 32767.0

    n_prefix = int(round(0.100 * row.sr_hz))
    prefix = x[:n_prefix]
    suffix = x[-n_prefix:]
    core = x[n_prefix:-n_prefix]

    target_amp = 10.0 ** (row.gain_dbfs / 20.0)
    # PCM int16 量子化ステップ (1/32767) の数ステップ分を許容誤差とする。
    quant_tol = 4.0 / 32767.0

    assert abs(float(np.max(np.abs(prefix))) - target_amp) <= quant_tol
    assert abs(float(np.max(np.abs(suffix))) - target_amp) <= quant_tol
    assert abs(float(np.max(np.abs(core))) - target_amp) <= quant_tol


def test_context_segment_peak_matches_declared_gain_transition_adjacent() -> None:
    """-24dBFS row + `transition-adjacent` context: 連結された adjacent tone
    の peak も declared gain レベルに一致すること（voiced-prefix/suffix と
    同型の回帰）。"""
    row = _f0_row(f0_hz=261.626, duration_s=0.3, gain_dbfs=-24.0, context="transition-adjacent")
    pcm = f0_control.render(row, _rng())
    x = pcm.astype(np.float64) / 32767.0

    n_core = int(round(0.3 * row.sr_hz))
    core = x[:n_core]
    adjacent = x[n_core:]

    target_amp = 10.0 ** (row.gain_dbfs / 20.0)
    quant_tol = 4.0 / 32767.0

    assert abs(float(np.max(np.abs(core))) - target_amp) <= quant_tol
    assert abs(float(np.max(np.abs(adjacent))) - target_amp) <= quant_tol


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


def _welch_local_prominence_db(
    pcm: np.ndarray, sr_hz: int, center_hz: float, bandwidth_hz: float
) -> float:
    """[Codex レビュー 2026-09-01 P2] 生成器の内部実装（`resonance._measure_*`）
    を再利用しない、独立した welch ベースの LOCAL SPECTRAL PEAK PROMINENCE
    測定。peak = `center_hz` ± `bandwidth_hz/2` 内の PSD 最大値。floor =
    `center_hz` の ±1 octave 外側（`[50Hz, center/2]` ∪ `[center*2,
    0.95*nyquist]`）の PSD 中央値 — 生成器が使う 2-pole resonator の skirt が
    近傍窓では noise floor まで十分減衰しないため、declared bandwidth の
    数倍程度の窓ではなく、center から十分離れた（1 octave）帯域を使う
    （このモジュールの他所での実測: center±32*bandwidth の窓でもなお
    真の floor より 5-20dB 高い）。
    """
    x = pcm.astype(np.float64) / 32767.0
    nperseg = min(len(x), 2048)
    freqs, psd = welch(x, fs=sr_hz, nperseg=nperseg, window="hann", detrend=False)
    psd_db = 10.0 * np.log10(psd + 1e-20)

    half_bw = bandwidth_hz / 2.0
    peak_mask = np.abs(freqs - center_hz) <= half_bw
    peak_db = float(psd_db[peak_mask].max())

    nyquist = sr_hz / 2.0
    lo_far, hi_far = center_hz / 2.0, center_hz * 2.0
    floor_mask = ((freqs >= 50.0) & (freqs <= lo_far)) | (
        (freqs >= hi_far) & (freqs <= nyquist * 0.95)
    )
    floor_db = float(np.median(psd_db[floor_mask]))
    return peak_db - floor_db


@pytest.mark.parametrize("bandwidth_hz", [50.0, 300.0])
def test_resonance_realized_prominence_matches_declared_across_bandwidth_extremes(
    bandwidth_hz: float,
) -> None:
    """[Codex レビュー 2026-09-01 P2] 実現される LOCAL SPECTRAL PEAK
    PROMINENCE は、declared `resonance_bandwidth_hz` の両極端（50Hz/300Hz）
    でも同じ declared prominence（12dB）に対して近い値へ収束すること
    （one-shot 補正パスの回帰テスト。補正前の旧実装は帯域幅ごとに実現値が
    大きくばらついていた）。
    """
    tolerance_db = 1.5  # 実測近似誤差の上限目安（モジュール docstring 参照）
    row = _resonance_row(
        center_hz=2000.0, resonance_bandwidth_hz=bandwidth_hz, prominence_db=12.0,
        duration_s=1.0,
    )
    pcm = resonance.render(row, _rng())
    realized_db = _welch_local_prominence_db(pcm, row.sr_hz, row.center_hz, bandwidth_hz)
    assert abs(realized_db - row.prominence_db) <= tolerance_db, (
        f"bandwidth={bandwidth_hz}Hz realized={realized_db:.3f}dB "
        f"declared={row.prominence_db}dB"
    )


def test_resonance_realized_prominence_matches_declared_with_snr_confound_noise() -> None:
    """[Codex レビュー 2026-09-01 P1] declared `noise_snr_db` の nuisance noise
    は `common.finalize()` が prominence 較正の**後**に加えるため、較正時に
    想定した floor と最終 PCM の floor が食い違い、confound 行（noise 軸）では
    実現 prominence が declared 値を下回っていた。20dB SNR の confound 行で、
    FINAL PCM 上の実現 prominence が declared 値の許容誤差内に収まることを
    確認する（回帰防止）。
    """
    tolerance_db = 1.5  # クリーンな場合と同じ近似誤差の上限目安を適用
    row = _resonance_row(
        center_hz=2000.0, resonance_bandwidth_hz=150.0, prominence_db=12.0,
        duration_s=1.0, noise_clean=False, noise_snr_db=20,
    )
    pcm = resonance.render(row, _rng())
    realized_db = _welch_local_prominence_db(pcm, row.sr_hz, row.center_hz, 150.0)
    assert abs(realized_db - row.prominence_db) <= tolerance_db, (
        f"snr_db=20 realized={realized_db:.3f}dB declared={row.prominence_db}dB"
    )


def test_resonance_realized_snr_and_prominence_both_hold_when_correction_nontrivial() -> None:
    """[Codex レビュー 2026-09-01 P1 finding #2] regression: nuisance noise の
    振幅は CORRECTED clean signal（`floor + scaled_peak * correction`）を基準
    に導出しなければならない。`resonance_bandwidth_hz=50` (extreme, `correction
    != 1` を誘発する) の confound 行で、FINAL PCM 上の実現 prominence と
    実現 SNR の**両方**が declared 値の許容誤差内に収まることを確認する
    （旧実装は pre-correction の `floor+scaled_peak` を基準にしており、
    `correction != 1` の行では realized SNR が declared SNR から乖離して
    いた）。

    実現 SNR は `_core` 内部の rng 消費順序（floor draw → peak raw draw →
    nuisance raw draw）を同一 seed で再現した `raw`（nuisance の未スケール
    draw）を使い、FINAL PCM 上で `raw` 方向への射影から独立に測定する
    （`_nuisance_noise_component` 自体は呼ばない）。
    """
    prominence_tolerance_db = 1.5
    snr_tolerance_db = 0.5
    row = _resonance_row(
        center_hz=2000.0, resonance_bandwidth_hz=50.0, prominence_db=12.0,
        duration_s=2.0, noise_clean=False, noise_snr_db=20.0,
    )
    n = common.n_samples(row.duration_s, row.sr_hz)

    # `_core` の rng 消費順序を同一 seed で再現し、nuisance の未スケール draw
    # (`raw`) を独立に取得する（floor draw と peak-raw draw は消費するが破棄）。
    measurement_rng = _rng()
    _floor_draw = measurement_rng.standard_normal(n)
    _peak_raw_draw = measurement_rng.standard_normal(n)
    raw = measurement_rng.standard_normal(n)

    pcm = resonance.render(row, _rng())
    x = pcm.astype(np.float64) / 32767.0

    # realized prominence（既存の独立 welch 測定を再利用）。
    realized_prominence_db = _welch_local_prominence_db(pcm, row.sr_hz, row.center_hz, 50.0)
    assert abs(realized_prominence_db - row.prominence_db) <= prominence_tolerance_db, (
        f"realized_prominence={realized_prominence_db:.3f}dB declared={row.prominence_db}dB"
    )

    # realized SNR: x = k*(floor+nuisance+final_peak) の単一線形スカラー k の
    # もとで、raw 方向への最小二乗射影から noise 成分の実現パワーを推定する
    # (floor+final_peak は raw と独立な乱数系列であるため cross term は
    # duration_s=2.0 の長さで無視できるほど小さい)。
    proj = float(np.dot(x, raw) / np.dot(raw, raw))
    noise_power = float(np.mean((proj * raw) ** 2))
    total_power = float(np.mean(x**2))
    clean_power = max(total_power - noise_power, 1e-12)
    realized_snr_db = 10.0 * np.log10(clean_power / noise_power)
    assert abs(realized_snr_db - row.noise_snr_db) <= snr_tolerance_db, (
        f"realized_snr={realized_snr_db:.3f}dB declared={row.noise_snr_db}dB"
    )


def _resonance_steady_core_pcm(
    pcm: np.ndarray, context: str, sr_hz: int, duration_s: float
) -> np.ndarray:
    """`resonance._context_core_bounds()` と独立に、テスト側でも同じ
    "steady" core 区間の切り出しを再導出する（`common.assemble_context()` の
    prefix/suffix/adjacent の組み立てレシピをそのまま反映）。連結型 context
    （voiced-prefix/suffix・transition-adjacent）の core は truth-bearing で
    ない外的 framing nuisance を含まないため、実現 prominence の測定窓は
    steady 区間に限定する（module docstring「"steady" core 区間への測定窓の
    限定」参照）。"""
    n_core = int(round(duration_s * sr_hz))
    if context == "100ms-voiced-prefix/suffix":
        n_prefix = int(round(0.100 * sr_hz))
        return pcm[n_prefix : n_prefix + n_core]
    if context == "transition-adjacent":
        return pcm[:n_core]
    return pcm  # steady-isolated / 20ms-cosine-ramp: core の長さは不変


@pytest.mark.parametrize(
    "context", ["20ms-cosine-ramp", "100ms-voiced-prefix/suffix", "transition-adjacent"]
)
def test_resonance_realized_prominence_matches_declared_with_context_confound(
    context: str,
) -> None:
    """[Codex レビュー 2026-09-01 P1] regression: context（cosine-ramp /
    voiced-prefix-suffix / transition-adjacent）は `common.finalize()` が
    prominence 較正の**後**に適用するため、較正時に測定した core と最終 PCM
    の実際の波形（context 適用後）が食い違いうる。全 3 context 水準の
    confound 行で、"steady" core 区間に限定した final PCM 上の実現 prominence
    が declared 値の許容誤差内に収まることを確認する。
    """
    tolerance_db = 1.5  # クリーンな steady-isolated と同じ許容誤差目安を適用
    duration_s = 1.0
    row = _resonance_row(
        center_hz=2000.0, resonance_bandwidth_hz=150.0, prominence_db=12.0,
        duration_s=duration_s, context=context,
    )
    pcm = resonance.render(row, _rng())
    steady_pcm = _resonance_steady_core_pcm(pcm, context, row.sr_hz, duration_s)
    realized_db = _welch_local_prominence_db(steady_pcm, row.sr_hz, row.center_hz, 150.0)
    assert abs(realized_db - row.prominence_db) <= tolerance_db, (
        f"context={context!r} realized={realized_db:.3f}dB declared={row.prominence_db}dB"
    )


def test_resonance_transition_adjacent_whole_pcm_measurement_needs_steady_restriction() -> None:
    """`transition-adjacent` の adjacent tone（`f0_hz*1.5` の高調波）は declared
    `center_hz` の peak 窓に紛れ込みうるため、最終 PCM 全体（連結された
    adjacent tone を含む）をそのまま素朴に測定すると、steady 区間に限定した
    測定より外れることを確認する（module docstring「"steady" core 区間への
    測定窓の限定」が経験的に必要であることの記録・回帰防止）。

    [Codex レビュー 2026-09-01 P1 (fixtures/generators/common.py:153)]
    以前は `common.finalize()` が declared gain を core 単体にのみ適用してから
    context（adjacent tone 含む）を固定振幅で追加していたため、adjacent tone
    が declared gain と無関係な振幅（この row では core より約 6dB 大きい）を
    持ち、素朴な全体測定の contamination が測定誤差近似 (1.5dB) の枠を明確に
    超えて悪化していた。gain を「context 付加後の完全な waveform 全体」へ
    適用するよう修正した結果、adjacent tone も declared gain レベルに揃い
    contamination は大幅に縮小したが（steady 区間限定測定との差は依然として
    正 — 周波数領域での高調波混入そのものは振幅整合とは独立に残る）、
    ゼロにはならない。よって steady 区間限定測定の方が一貫して declared 値に
    近いという方向性そのものは変わらないため、その方向性のみを検証する
    （margin を振幅バグ時代の 0.5dB から縮小）。
    """
    duration_s = 1.0
    row = _resonance_row(
        center_hz=2000.0, resonance_bandwidth_hz=150.0, prominence_db=12.0,
        duration_s=duration_s, context="transition-adjacent",
    )
    pcm = resonance.render(row, _rng())
    n_core = int(round(duration_s * row.sr_hz))
    steady_pcm = pcm[:n_core]

    whole_db = _welch_local_prominence_db(pcm, row.sr_hz, row.center_hz, 150.0)
    steady_db = _welch_local_prominence_db(steady_pcm, row.sr_hz, row.center_hz, 150.0)

    whole_error = abs(whole_db - row.prominence_db)
    steady_error = abs(steady_db - row.prominence_db)
    assert steady_error <= 1.5
    assert whole_error > steady_error + 0.02, (
        f"whole={whole_db:.3f}dB steady={steady_db:.3f}dB declared={row.prominence_db}dB "
        "(expected whole-PCM measurement to remain more contaminated than "
        "the steady-restricted measurement, even after the P1 gain fix)"
    )


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
# TRANSITION_GT: duration_class は遷移時間として物理化される
# ([UNDERSPEC-CAL-B12], Codex レビュー 2026-09-01 P1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "join_type", ["amplitude-step", "phase-jump", "spectral-envelope-switch", "crossfade"]
)
def test_transition_short_vs_long_duration_class_differ_in_bytes(join_type: str) -> None:
    """同一 severity で duration_class だけを short/long に変えると、遷移窓の
    物理的な長さ（5ms vs 50ms）が異なるため PCM が byte-identical になっては
    ならない（4 join type 全て）。"""
    row_short = _transition_row(
        join_type=join_type, severity="medium", duration_class="short",
        discontinuity_magnitude=0.35,
    )
    row_long = _transition_row(
        join_type=join_type, severity="medium", duration_class="long",
        discontinuity_magnitude=0.35,
    )
    pcm_short = transition.render(row_short, _rng())
    pcm_long = transition.render(row_long, _rng())
    assert not np.array_equal(pcm_short, pcm_long)


@pytest.mark.parametrize(
    "join_type", ["amplitude-step", "phase-jump", "spectral-envelope-switch"]
)
def test_transition_ramp_widens_with_long_duration_class(join_type: str) -> None:
    """long (50ms) は short (5ms) より遷移が widen し、join time から離れた
    位置でもなお pre/post 中間状態にある区間が長く残ること（ramp が本当に
    duration_class 由来の幅で実現されていることの直接検証。crossfade は元々
    この性質を持つため対象外）。"""
    row_short = _transition_row(
        join_type=join_type, severity="high", duration_class="short",
        discontinuity_magnitude=0.65, duration_s=0.4, join_time_s=0.2,
    )
    row_long = _transition_row(
        join_type=join_type, severity="high", duration_class="long",
        discontinuity_magnitude=0.65, duration_s=0.4, join_time_s=0.2,
    )
    pcm_short = transition.render(row_short, _rng())
    pcm_long = transition.render(row_long, _rng())

    join_sample = int(round(0.2 * SR))
    probe = int(round(0.030 * SR))  # 30ms: within the long (50ms) ramp, outside short (5ms)
    # 短い duration_class は join+30ms 時点で既に post-join 定常状態に収束
    # しているはずだが、長い duration_class はまだ遷移途中（pre/post いずれの
    # 定常値とも異なる）はず。
    steady_pre = float(np.abs(pcm_short[join_sample - probe]))
    steady_post_short = float(np.abs(pcm_short[join_sample + probe]))
    mid_long = float(np.abs(pcm_long[join_sample + probe]))
    # long の遷移途中サンプルは、short の (収束済み) post 値とは有意に異なる。
    assert abs(mid_long - steady_post_short) > 0.01 * 32767.0 or abs(
        mid_long - steady_pre
    ) > 0.01 * 32767.0


# ---------------------------------------------------------------------------
# TRANSITION_GT: crossfade is centered on declared join_time_s
# (Codex レビュー 2026-09-01 P1)
# ---------------------------------------------------------------------------


def _local_blend_fraction(
    x: np.ndarray, base: np.ndarray, alt: np.ndarray, window: int
) -> np.ndarray:
    """局所ウィンドウで `x ≈ A*base + B*alt` を最小二乗フィットし、各サンプル
    位置での energy-weighted blend fraction `B/(A+B)` を返す（`base`/`alt` は
    `transition._steady_tone` から独立に再構成した既知の厳密 reference 波形。
    `transition._blend_envelope`/`_core` は一切呼ばない独立測定）。窓内の
    局所和は boxcar 畳み込みでまとめて計算する。
    """
    kernel = np.ones(window, dtype=np.float64)

    def _local_sum(a: np.ndarray) -> np.ndarray:
        return np.convolve(a, kernel, mode="same")

    s_bb = _local_sum(base * base)
    s_aa = _local_sum(alt * alt)
    s_ba = _local_sum(base * alt)
    s_xb = _local_sum(x * base)
    s_xa = _local_sum(x * alt)

    det = s_bb * s_aa - s_ba * s_ba
    with np.errstate(invalid="ignore", divide="ignore"):
        a_coef = np.where(np.abs(det) > 1e-9, (s_xb * s_aa - s_xa * s_ba) / det, np.nan)
        b_coef = np.where(np.abs(det) > 1e-9, (s_bb * s_xa - s_ba * s_xb) / det, np.nan)
        denom = a_coef + b_coef
        frac = np.where(np.abs(denom) > 1e-9, b_coef / denom, np.nan)
    return frac


@pytest.mark.parametrize("duration_class", ["short", "long"])
def test_transition_crossfade_realized_midpoint_matches_declared_join_time(
    duration_class: str,
) -> None:
    """[Codex レビュー 2026-09-01 P1] regression: `crossfade` の実現遷移中心は
    declared `join_time_s`（= `join_sample`）と一致すること。旧実装は
    crossfade window が `join_sample` から片側にのみ伸びており（他 3 join
    type のように中心配置ではなかった）、実現遷移中心が
    `join_sample + ramp_samples/2` へずれていた。`x ≈ A*base + B*alt` の
    energy-weighted blend fraction `B/(A+B)` を独立に局所最小二乗推定し、
    それが 0.5 を跨ぐサンプル位置を実現遷移中心として測定する。
    """
    row = _transition_row(
        join_type="crossfade", duration_class=duration_class,
        discontinuity_magnitude=0.35, join_time_s=0.2, duration_s=0.4,
        f0_hz=220.0,
    )
    n = common.n_samples(row.duration_s, row.sr_hz)
    pcm = transition.render(row, _rng())
    x = pcm.astype(np.float64) / 32767.0

    base = transition._steady_tone(row.f0_hz, row.sr_hz, n)
    alt_f0 = row.f0_hz * (1.0 + row.discontinuity_magnitude)
    alt = common.peak_normalize(transition._steady_tone(alt_f0, row.sr_hz, n))

    join_sample = int(round(row.join_time_s * row.sr_hz))
    ramp_samples = transition._ramp_samples_for(row, row.sr_hz)
    old_buggy_midpoint = join_sample + ramp_samples // 2

    window = max(64, ramp_samples // 4)
    frac = _local_blend_fraction(x, base, alt, window)

    search_start = max(0, join_sample - ramp_samples * 3)
    search_end = min(len(frac), join_sample + ramp_samples * 3)
    crossing = None
    for i in range(search_start, search_end):
        if not np.isnan(frac[i]) and frac[i] >= 0.5:
            crossing = i
            break
    assert crossing is not None, "blend fraction never reaches 0.5 near join_sample"

    tolerance = max(1, ramp_samples // 2)
    assert abs(crossing - join_sample) <= tolerance, (
        f"duration_class={duration_class!r} realized_midpoint_sample={crossing} "
        f"join_sample={join_sample} ramp_samples={ramp_samples} tolerance={tolerance}"
    )
    # 旧実装の off-by-half-width シフトが解消していること: 実現中心は
    # declared join_sample の方が、旧実装が予測する位置 (join_sample +
    # ramp/2) よりも近い。
    assert abs(crossing - join_sample) < abs(crossing - old_buggy_midpoint), (
        f"duration_class={duration_class!r} realized_midpoint_sample={crossing} closer to "
        f"old buggy midpoint {old_buggy_midpoint} than declared join_sample {join_sample}"
    )


def test_transition_recorded_truth_carries_ramp_duration_and_magnitude() -> None:
    """recorded truth は severity (`discontinuity_magnitude`) と ramp duration
    の両方を担保する: `duration_class` から `[UNDERSPEC-CAL-B06]` の
    short=5ms/long=50ms 写像で ramp 秒数が一意に導ける。"""
    row = _transition_row(duration_class="short", discontinuity_magnitude=0.35)
    assert row.duration_class == "short"
    assert transition._DURATION_CLASS_S[row.duration_class] == 0.005
    assert row.discontinuity_magnitude == 0.35
    assert row.join_time_s is not None  # exact join time は変更なく記録され続ける


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
