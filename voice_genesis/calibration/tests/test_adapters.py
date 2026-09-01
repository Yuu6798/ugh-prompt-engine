"""candidates/impl/* + adapter.py の単純合成信号によるユニットテスト。

fixtures/ パッケージ（並行実装中）には依存しない。全信号はここで numpy
により生成する（設計正本の fixture 校正はキャンペーン後段の別フェーズで
別途行う。ここでの目的は「実装が壊れていないこと」の素朴な検証）。
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import lfilter

from voice_genesis.calibration import vocab
from voice_genesis.calibration.candidates import adapter
from voice_genesis.calibration.candidates.impl import (
    aperiodicity,
    b0_wrappers,
    f0_pyin,
    formant_burg,
    formant_cepstral,
    resonance_prominence,
    tilt_harmonic,
    transition,
)

SR = 22050


def _harmonic_complex(f0: float, sr: int, dur: float, n_harmonics: int = 8) -> np.ndarray:
    t = np.arange(int(dur * sr)) / sr
    sig = sum(np.sin(2 * np.pi * f0 * k * t) * (0.75 ** (k - 1)) for k in range(1, n_harmonics + 1))
    return sig / np.max(np.abs(sig)) * 0.5


def _single_resonance(center_hz: float, bandwidth_hz: float, sr: int, dur: float, seed: int = 0) -> np.ndarray:
    """白色雑音励振の 2 極共振フィルタ（単一 formant/resonance を持つ合成信号）。"""
    rng = np.random.default_rng(seed)
    noise = rng.normal(size=int(dur * sr))
    r = np.exp(-np.pi * bandwidth_hz / sr)
    theta = 2 * np.pi * center_hz / sr
    a1 = -2.0 * r * np.cos(theta)
    a2 = r * r
    filtered = lfilter([1.0], [1.0, a1, a2], noise)
    return filtered / np.max(np.abs(filtered)) * 0.5


def _harmonics_with_tilt(f0: float, sr: int, dur: float, slope_db_per_oct: float, k_max: int) -> np.ndarray:
    t = np.arange(int(dur * sr)) / sr
    sig = np.zeros_like(t)
    for k in range(1, k_max + 1):
        amp = 10 ** (slope_db_per_oct * np.log2(k) / 20.0)
        sig += amp * np.sin(2 * np.pi * k * f0 * t)
    return sig / np.max(np.abs(sig)) * 0.5


# ---------------------------------------------------------------------------
# F0-PYIN
# ---------------------------------------------------------------------------


def test_f0_pyin_recovers_440hz() -> None:
    sig = _harmonic_complex(440.0, SR, 0.4, n_harmonics=1)
    out = f0_pyin.measure(sig, SR, {"frame_length": 2048, "hop_length": 256})
    assert out.missing_reason is None
    assert out.values["f0_hz"] == pytest.approx(440.0, abs=5.0)


# ---------------------------------------------------------------------------
# M3-CEPSTRAL-POLES / M3-BURG-LPC: 単一共振ピーク回帰（粗い許容差）
# ---------------------------------------------------------------------------


def test_cepstral_formant_recovers_single_resonance_peak() -> None:
    sig = _single_resonance(1200.0, 100.0, SR, 0.5)
    out = formant_cepstral.measure(
        sig,
        SR,
        {"f0_hz": float("nan"), "lifter_ratio": 0.7, "min_lifter_samples": 4, "band_hi": 4000.0},
    )
    assert out.missing_reason is None
    nearest = min(out.values.values(), key=lambda f: abs(f - 1200.0))
    assert nearest == pytest.approx(1200.0, abs=250.0)


def test_burg_formant_recovers_single_resonance_peak() -> None:
    sr = 16000
    sig = _single_resonance(1200.0, 100.0, sr, 0.5)
    out = formant_burg.measure(
        sig,
        sr,
        {"order": 16, "window_ms": 25.0, "preemph_hz": 0.0, "max_formant_hz": 4000.0},
    )
    assert out.missing_reason is None
    freqs = [v for k, v in out.values.items() if k.endswith("_hz") and not k.endswith("bandwidth_hz")]
    nearest = min(freqs, key=lambda f: abs(f - 1200.0))
    assert nearest == pytest.approx(1200.0, abs=250.0)


# ---------------------------------------------------------------------------
# M2T-HARMONIC-OLS / THEILSEN: hand-computed -12 dB/oct oracle
# ---------------------------------------------------------------------------


def test_tilt_ols_recovers_constructed_slope() -> None:
    f0 = 180.0
    sig = _harmonics_with_tilt(f0, SR, 0.5, slope_db_per_oct=-12.0, k_max=8)
    out = tilt_harmonic.measure_ols(sig, SR, {"f0_hz": f0, "k": 8, "window": "hann"})
    assert out.missing_reason is None
    assert out.values["tilt_db_per_oct"] == pytest.approx(-12.0, abs=0.5)


def test_tilt_theilsen_recovers_constructed_slope() -> None:
    f0 = 180.0
    sig = _harmonics_with_tilt(f0, SR, 0.5, slope_db_per_oct=-12.0, k_max=8)
    out = tilt_harmonic.measure_theilsen(sig, SR, {"f0_hz": f0, "k": 8, "window": "blackman_harris"})
    assert out.missing_reason is None
    assert out.values["tilt_db_per_oct"] == pytest.approx(-12.0, abs=0.5)


def test_tilt_missing_when_fewer_than_k_harmonics_available() -> None:
    """K 本未満の倍音しか取れない（Nyquist を超える）場合は縮退せず missing。"""
    f0 = 9000.0  # k=3 で Nyquist(11025Hz) 超過 → K=8 は満たせない
    sig = _harmonics_with_tilt(f0, SR, 0.3, slope_db_per_oct=-6.0, k_max=2)
    out = tilt_harmonic.measure_ols(sig, SR, {"f0_hz": f0, "k": 8, "window": "hann"})
    assert out.missing_reason is vocab.MissingReason.OUTPUT_MISSING
    assert out.values == {}


# ---------------------------------------------------------------------------
# M2A-HNR-ACF: directional oracle（clean > noisy）
# ---------------------------------------------------------------------------


def test_hnr_acf_orders_clean_above_noisy() -> None:
    f0 = 180.0
    tone = _harmonic_complex(f0, SR, 0.5, n_harmonics=5)
    rng = np.random.default_rng(1)
    noise = rng.normal(size=tone.shape)
    noise = noise / np.max(np.abs(noise))

    clean = tone
    noisy = 0.5 * tone + 0.5 * noise
    noisy = noisy / np.max(np.abs(noisy)) * 0.5

    hnr_clean = aperiodicity.hnr_acf_db(clean, SR, frame_ms=25.0, hop_ms=10.0, window="hann")
    hnr_noisy = aperiodicity.hnr_acf_db(noisy, SR, frame_ms=25.0, hop_ms=10.0, window="hann")
    assert np.isfinite(hnr_clean) and np.isfinite(hnr_noisy)
    assert hnr_clean > hnr_noisy


def test_hnr_acf_adapter_finite_output() -> None:
    tone = _harmonic_complex(180.0, SR, 0.4, n_harmonics=5)
    out = aperiodicity.measure_hnr_acf(
        tone, SR, {"frame_ms": 25.0, "hop_ms": 10.0, "window": "hann"}
    )
    assert out.missing_reason is None
    assert np.isfinite(out.values["hnr_db"])


# ---------------------------------------------------------------------------
# M2A-HARMONIC-RESIDUAL: fraction は注入雑音量とともに増加する
# ---------------------------------------------------------------------------


def test_harmonic_residual_fraction_increases_with_injected_noise() -> None:
    f0 = 180.0
    tone = _harmonic_complex(f0, SR, 0.5, n_harmonics=5)
    rng = np.random.default_rng(2)
    noise = rng.normal(size=tone.shape)
    noise = noise / np.max(np.abs(noise))

    fractions = []
    for frac in (0.0, 0.1, 0.3, 0.6):
        mixed = (1 - frac) * tone + frac * noise
        mixed = mixed / np.max(np.abs(mixed)) * 0.5
        r = aperiodicity.harmonic_residual_fraction(
            mixed, SR, f0, k=8, window="hann", residual_band="broadband"
        )
        assert np.isfinite(r)
        fractions.append(r)
    assert fractions == sorted(fractions)
    assert fractions[0] < fractions[-1]


# ---------------------------------------------------------------------------
# M2A-D4C: pyworld 不在時は typed ineligible result（例外を投げない）
# ---------------------------------------------------------------------------


def test_d4c_reports_ineligible_when_pyworld_absent() -> None:
    if aperiodicity.d4c_available():
        pytest.skip("pyworld is installed in this environment; ineligible-path not exercised")
    out = aperiodicity.measure_d4c(
        np.zeros(2000), SR, {"f0_hz": 200.0, "band": "broadband"}
    )
    assert out.ineligible is True
    assert out.ineligible_reason == adapter.INELIGIBLE_DEPENDENCY_ABSENT
    assert out.missing_reason is None


def _reload_aperiodicity_under_patched_import(monkeypatch, broken_import) -> None:  # noqa: ANN001
    import builtins
    import importlib

    real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", broken_import(real_import))
    try:
        importlib.reload(aperiodicity)
    finally:
        monkeypatch.undo()
        importlib.reload(aperiodicity)  # 後始末: 実 import 状態へ復元（他テストへの汚染防止）


def test_pyworld_broken_shared_library_importerror_propagates(monkeypatch) -> None:  # noqa: ANN001
    """`pyworld` はインストール済みだが共有ライブラリ破損等で `ImportError`
    （`ModuleNotFoundError` ではない）を投げる場合、「不在」として握りつぶさず
    re-raise する（Codex レビュー 2026-09-01 P2）。
    """

    def broken_import(real_import):
        def _fn(name, *args, **kwargs):
            if name == "pyworld":
                raise ImportError("libworld.so: cannot open shared object file")
            return real_import(name, *args, **kwargs)

        return _fn

    with pytest.raises(ImportError):
        _reload_aperiodicity_under_patched_import(monkeypatch, broken_import)
    assert aperiodicity.d4c_available() is False  # 後始末後は元の（不在）状態


def test_pyworld_modulenotfound_with_other_name_propagates(monkeypatch) -> None:  # noqa: ANN001
    """`pyworld` 自体ではなく、その依存先が `ModuleNotFoundError` を出す場合
    （`e.name != "pyworld"`）も「不在」扱いにせず re-raise する。
    """

    def broken_import(real_import):
        def _fn(name, *args, **kwargs):
            if name == "pyworld":
                raise ModuleNotFoundError("No module named 'pyworld._internal_dep'", name="pyworld._internal_dep")
            return real_import(name, *args, **kwargs)

        return _fn

    with pytest.raises(ModuleNotFoundError):
        _reload_aperiodicity_under_patched_import(monkeypatch, broken_import)
    assert aperiodicity.d4c_available() is False


# ---------------------------------------------------------------------------
# M4-LOCAL-PROMINENCE
# ---------------------------------------------------------------------------


def test_resonance_prominence_recovers_single_peak() -> None:
    sig = _single_resonance(1200.0, 100.0, SR, 0.5)
    out = resonance_prominence.measure(
        sig, SR, {"prominence_db": 6.0, "smoothing_bandwidth_hz": 150.0}
    )
    assert out.missing_reason is None
    assert out.values["center_hz"] == pytest.approx(1200.0, abs=100.0)


# ---------------------------------------------------------------------------
# M5-WAVE-DISCONTINUITY / M5-SPECTRAL-FLUX: 両側検定（step で発火・定常で沈黙）
# ---------------------------------------------------------------------------


def _steady_and_step_signals() -> tuple[np.ndarray, np.ndarray]:
    dur = 0.3
    t = np.arange(int(dur * SR)) / SR
    steady = 0.3 * np.sin(2 * np.pi * 220 * t)
    step = steady.copy()
    mid = len(step) // 2
    step[mid:] *= 3.0
    return steady, step


def test_wave_discontinuity_fires_on_step_and_silent_on_steady() -> None:
    steady, step = _steady_and_step_signals()
    for window_ms in (2.0, 5.0, 10.0):
        _, mag_steady = transition.wave_discontinuity(steady, SR, window_ms=window_ms)
        _, mag_step = transition.wave_discontinuity(step, SR, window_ms=window_ms)
        assert mag_step > mag_steady * 5.0


def test_spectral_flux_fires_on_step_and_silent_on_steady() -> None:
    steady, step = _steady_and_step_signals()
    for frame_len in (512, 1024):
        for norm in ("l1", "l2"):
            _, mag_steady = transition.spectral_flux(steady, SR, frame_len=frame_len, norm=norm)
            _, mag_step = transition.spectral_flux(step, SR, frame_len=frame_len, norm=norm)
            assert mag_step > mag_steady * 5.0


# ---------------------------------------------------------------------------
# b0_wrappers: 有限値を返すこと
# ---------------------------------------------------------------------------


def test_b0_wrappers_return_finite_values_on_synthetic_signal() -> None:
    sig = _harmonic_complex(220.0, SR, 0.5, n_harmonics=5)

    f0_out = b0_wrappers.measure_f0_b0(sig, SR, {})
    assert f0_out.missing_reason is None
    assert np.isfinite(f0_out.values["f0_hz"])

    m3_out = b0_wrappers.measure_m3_b0_centroid(sig, SR, {})
    assert m3_out.missing_reason is None
    assert all(np.isfinite(v) for v in m3_out.values.values())

    m4_out = b0_wrappers.measure_m4_b0_centroid(sig, SR, {})
    assert m4_out.missing_reason is None
    assert all(np.isfinite(v) for v in m4_out.values.values())

    m2t_out = b0_wrappers.measure_m2t_b0_hybrid(sig, SR, {})
    assert m2t_out.missing_reason is None
    assert np.isfinite(m2t_out.values["value"])

    m2a_out = b0_wrappers.measure_m2a_b0_periodicity(sig, SR, {})
    assert m2a_out.missing_reason is None
    assert np.isfinite(m2a_out.values["hnr_db"])


# ---------------------------------------------------------------------------
# adapter.py: 共通 fail filter
# ---------------------------------------------------------------------------


def test_schema_violation_detects_missing_required_field() -> None:
    ok = adapter.MeterOutput(values={"f0_hz": 440.0})
    assert adapter.schema_violation(ok, {"f0_hz"}) is False
    bad = adapter.MeterOutput(values={"other_field": 1.0})
    assert adapter.schema_violation(bad, {"f0_hz"}) is True
    explained = adapter.MeterOutput(missing_reason=vocab.MissingReason.OUTPUT_MISSING)
    assert adapter.schema_violation(explained, {"f0_hz"}) is False


def test_unexplained_nonfinite_detects_nan_without_reason() -> None:
    bad = adapter.MeterOutput(values={"f0_hz": float("nan")})
    assert adapter.unexplained_nonfinite(bad) is True
    explained = adapter.MeterOutput(
        values={}, missing_reason=vocab.MissingReason.OUTPUT_MISSING
    )
    assert adapter.unexplained_nonfinite(explained) is False
    ok = adapter.MeterOutput(values={"f0_hz": 220.0})
    assert adapter.unexplained_nonfinite(ok) is False


def test_within_fresh_process_mismatch() -> None:
    within = [{"f0_hz": 220.0}, {"f0_hz": 220.1}, {"f0_hz": 219.9}]
    fresh_match = [{"f0_hz": 220.05}]
    fresh_mismatch = [{"f0_hz": 500.0}]
    assert (
        adapter.within_fresh_process_mismatch(
            within, fresh_match, field_name="f0_hz", tol=1.0
        )
        is False
    )
    assert (
        adapter.within_fresh_process_mismatch(
            within, fresh_mismatch, field_name="f0_hz", tol=1.0
        )
        is True
    )
    assert adapter.within_fresh_process_mismatch([], fresh_match, field_name="f0_hz") is True


def test_negative_positive_control_filters() -> None:
    assert adapter.negative_control_false_fire([False, False, False]) is False
    assert adapter.negative_control_false_fire([False, True, False]) is True
    assert adapter.positive_control_non_fire([True, True, True]) is False
    assert adapter.positive_control_non_fire([True, False, True]) is True
