"""tests/test_metrics_v2.py — Metrics v2 (level-invariant) acceptance tests.

Synthetic-signal acceptance tests for docs/metrics.md "Metrics v2" /
the upstream Metrics_v2_spec.md. Pure numpy + librosa, no audio decode, so
this stays fast enough for the default (non-`slow`) loop. sr=44100, float32,
seeds fixed for determinism throughout.

T5 (skipped, per Design Memo decision): "confirm no percentile-threshold
active-rate implementation exists" is a source-inspection assertion, not a
signal-based test. It is already covered structurally by T3/INV below — a
percentile-of-full-signal-threshold implementation could not pass an
unconditional level-invariance check by construction, so a regression there
would already fail T3/INV.
"""
from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from svp_rpe.rpe.physical_features import (
    compute_active_rate,
    compute_active_rate_v2,
    compute_crest_factor_robust,
    compute_fullness,
    compute_silence_rate,
    compute_valley_db,
)
from svp_rpe.rpe.valley import valley_rms_percentile

SR = 44100


def _tone(duration_sec: float, amp: float, freq: float = 440.0, sr: int = SR) -> np.ndarray:
    t = np.arange(int(duration_sec * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silence(duration_sec: float, sr: int = SR) -> np.ndarray:
    return np.zeros(int(duration_sec * sr), dtype=np.float32)


@pytest.fixture(scope="module")
def t1_signal() -> np.ndarray:
    """30s 440Hz tone (amp 0.2) + 30s digital silence."""
    return np.concatenate([_tone(30.0, 0.2), _silence(30.0)])


@pytest.fixture(scope="module")
def t2_signal() -> np.ndarray:
    """30s -16dBFS tone + 30s -6dBFS tone, back to back (no silence)."""
    return np.concatenate(
        [_tone(30.0, 10 ** (-16 / 20)), _tone(30.0, 10 ** (-6 / 20))]
    )


@pytest.fixture(scope="module")
def t3_signal(t2_signal: np.ndarray) -> np.ndarray:
    """T2 attenuated -12 dB overall — the level-invariance stress case."""
    return (t2_signal * 10 ** (-12 / 20)).astype(np.float32)


class TestT1SilenceHalf:
    """T1: half tone, half silence → active_rate_v2 ≈ 0.50."""

    def test_active_rate_v2(self, t1_signal: np.ndarray) -> None:
        assert compute_active_rate_v2(t1_signal, SR) == pytest.approx(0.50, abs=0.03)

    def test_valley_db_small_for_stationary_tone(self, t1_signal: np.ndarray) -> None:
        # T1b: the sounding half is a stationary sine — no real loud/quiet
        # contrast among its own non-silent frames, so dB dynamic range
        # should stay small (not a strict 0 — frame-to-frame RMS still
        # wobbles slightly at window edges).
        assert compute_valley_db(t1_signal, SR) < 1.0


class TestT2LoudQuietContrast:
    """T2: two levels 10 dB apart, always sounding → active=1.0, DR≈10dB."""

    def test_active_rate_v2(self, t2_signal: np.ndarray) -> None:
        assert compute_active_rate_v2(t2_signal, SR) == pytest.approx(1.0, abs=0.02)

    def test_valley_db(self, t2_signal: np.ndarray) -> None:
        assert compute_valley_db(t2_signal, SR) == pytest.approx(10.0, abs=0.6)

    def test_fullness(self, t2_signal: np.ndarray) -> None:
        assert compute_fullness(t2_signal, SR) == pytest.approx(1.0, abs=0.02)


class TestT3LevelInvariance:
    """T3 = T2 attenuated -12 dB: v2 metrics must not move.

    This is the direct regression test for the legacy defect: the old
    `valley_rms_percentile` (linear P90-P10 of RMS) shrinks by the same
    -12 dB factor (~0.251x) instead of staying constant — see
    TestLegacyRegressionProtection.test_valley_rms_percentile_scales_with_level.
    """

    def test_active_rate_v2_unchanged(
        self, t2_signal: np.ndarray, t3_signal: np.ndarray
    ) -> None:
        assert compute_active_rate_v2(t3_signal, SR) == pytest.approx(
            compute_active_rate_v2(t2_signal, SR), abs=0.02
        )

    def test_fullness_unchanged(
        self, t2_signal: np.ndarray, t3_signal: np.ndarray
    ) -> None:
        assert compute_fullness(t3_signal, SR) == pytest.approx(
            compute_fullness(t2_signal, SR), abs=0.02
        )

    def test_valley_db_unchanged(
        self, t2_signal: np.ndarray, t3_signal: np.ndarray
    ) -> None:
        assert compute_valley_db(t3_signal, SR) == pytest.approx(
            compute_valley_db(t2_signal, SR), abs=0.3
        )


class TestT4TimbreInvariance:
    """T4: same-RMS 50Hz tone vs seeded white noise → same active_rate_v2.

    active_rate_v2 is an RMS-gated silence detector, not a spectral one, so
    it must not distinguish a pure low tone from broadband noise at matched
    RMS.
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def matched_rms_pair() -> tuple[np.ndarray, np.ndarray]:
        duration = 10.0
        target_rms = 0.1

        tone = _tone(duration, 1.0, freq=50.0)
        tone = (tone / np.sqrt(np.mean(tone ** 2)) * target_rms).astype(np.float32)

        rng = np.random.default_rng(1234)
        noise = rng.normal(0.0, 1.0, int(duration * SR)).astype(np.float64)
        noise = (noise / np.sqrt(np.mean(noise ** 2)) * target_rms).astype(np.float32)

        return tone, noise

    def test_active_rate_v2_matches(
        self, matched_rms_pair: tuple[np.ndarray, np.ndarray]
    ) -> None:
        tone, noise = matched_rms_pair
        diff = abs(compute_active_rate_v2(tone, SR) - compute_active_rate_v2(noise, SR))
        assert diff < 0.02


@pytest.mark.parametrize("scale_db", [0.0, -12.0, -24.0])
def test_inv_level_invariance_on_synthetic_mix(scale_db: float) -> None:
    """INV: a fixed deterministic synth (seeded noise + tone + slow swell,
    standing in for "any real song y") must yield unchanged valley_db /
    active_rate_v2 / fullness under arbitrary attenuation.

    This is the headline Metrics v2 guarantee (alongside T3) that the legacy
    valley_depth formula fails.
    """
    rng = np.random.default_rng(2026)
    duration = 20.0
    t = np.arange(int(duration * SR)) / SR
    tone = 0.3 * np.sin(2 * np.pi * 220.0 * t)
    noise = 0.05 * rng.normal(0.0, 1.0, t.size)
    envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 0.1 * t)  # slow swell, never silent
    y = ((tone + noise) * envelope).astype(np.float32)

    y_scaled = (y * 10 ** (scale_db / 20.0)).astype(np.float32)

    assert compute_active_rate_v2(y_scaled, SR) == pytest.approx(
        compute_active_rate_v2(y, SR), abs=0.02
    )
    assert compute_fullness(y_scaled, SR) == pytest.approx(
        compute_fullness(y, SR), abs=0.02
    )
    assert compute_valley_db(y_scaled, SR) == pytest.approx(
        compute_valley_db(y, SR), abs=0.3
    )


# ---------------------------------------------------------------------------
# T-INV property sweep — generalizes test_inv_level_invariance_on_synthetic_mix
# (single fixed signal x 3 gains) into a Hypothesis sweep over the signal
# family + an arbitrary gain. Pure numpy/librosa, no audio decode, so this
# stays in the fast (non-`slow`) loop — see CLAUDE.md Testing 節.
# ---------------------------------------------------------------------------


def _sweep_signal(
    *,
    freq: float,
    envelope_depth: float,
    noise_amp: float,
    seed: int,
    duration_sec: float = 8.0,
    sr: int = SR,
) -> np.ndarray:
    """Same recipe as test_inv_level_invariance_on_synthetic_mix (tone + seeded
    noise + slow swell), parameterized for the property sweep. `duration_sec`
    is shortened from the fixed test's 20s to keep the sweep's total runtime
    small. The envelope is `0.5 + envelope_depth * sin(...)`; with
    `envelope_depth` capped at 0.4 the minimum level is 0.5 - 0.4 = 0.1, so the
    signal never dips into true digital silence (matching the Design Memo's
    "min level does not fall below 0.1" constraint)."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(duration_sec * sr)) / sr
    tone = 0.3 * np.sin(2 * np.pi * freq * t)
    noise = noise_amp * rng.normal(0.0, 1.0, t.size)
    envelope = 0.5 + envelope_depth * np.sin(2 * np.pi * 0.1 * t)
    return ((tone + noise) * envelope).astype(np.float32)


@settings(max_examples=15, deadline=None, derandomize=True)
@given(
    gain_db=st.floats(min_value=-60.0, max_value=12.0),
    freq=st.floats(min_value=80.0, max_value=2000.0),
    envelope_depth=st.floats(min_value=0.0, max_value=0.4),
    noise_amp=st.floats(min_value=0.0, max_value=0.1),
    seed=st.sampled_from([1, 2, 3, 4, 5]),
)
def test_inv_level_invariance_property_sweep(
    gain_db: float,
    freq: float,
    envelope_depth: float,
    noise_amp: float,
    seed: int,
) -> None:
    """T-INV, generalized: for an arbitrary signal from the tone+noise+swell
    family and an arbitrary gain shift, the v2 metrics must not move (and
    silence_rate/active_rate_v2 must always sum to 1)."""
    y = _sweep_signal(freq=freq, envelope_depth=envelope_depth, noise_amp=noise_amp, seed=seed)
    y_scaled = (y * 10 ** (gain_db / 20.0)).astype(np.float32)

    silence, silence_scaled = compute_silence_rate(y, SR), compute_silence_rate(y_scaled, SR)
    active, active_scaled = compute_active_rate_v2(y, SR), compute_active_rate_v2(y_scaled, SR)
    fullness, fullness_scaled = compute_fullness(y, SR), compute_fullness(y_scaled, SR)
    valley, valley_scaled = compute_valley_db(y, SR), compute_valley_db(y_scaled, SR)
    crest, crest_scaled = compute_crest_factor_robust(y), compute_crest_factor_robust(y_scaled)

    # Identity: silence_rate + active_rate_v2 == 1, at any gain.
    assert silence + active == pytest.approx(1.0, abs=1e-6)
    assert silence_scaled + active_scaled == pytest.approx(1.0, abs=1e-6)

    # Level invariance, tolerances inherited from the fixed T3/INV tests above.
    assert active_scaled == pytest.approx(active, abs=0.02)
    assert fullness_scaled == pytest.approx(fullness, abs=0.02)
    assert valley_scaled == pytest.approx(valley, abs=0.3)
    # crest_factor_robust is a ratio (peak/RMS) so it is level-invariant by
    # construction; 1% relative tolerance absorbs float32 rounding only.
    assert crest_scaled == pytest.approx(crest, rel=0.01)


class TestLegacyRegressionProtection:
    """LEGACY: pin the pre-v2 behavior of compute_active_rate and
    valley_rms_percentile so adding v2 cannot silently change them (v2 is
    additive; the legacy functions must stay byte-for-byte the same)."""

    def test_active_rate_on_t1(self, t1_signal: np.ndarray) -> None:
        # Fixed absolute 0.01 RMS threshold: T1's sounding-half frame RMS
        # (~0.2/sqrt(2) ≈ 0.141) clears it easily, so legacy also reads
        # ≈0.5 here — via a different (level-dependent) mechanism than
        # active_rate_v2's own-peak-relative gate.
        assert compute_active_rate(t1_signal, SR) == pytest.approx(0.50, abs=0.03)

    def test_active_rate_saturates_on_loud_signal(self) -> None:
        # The level-dependence defect (Metrics_v2_spec.md §1): a
        # produced-loud tone saturates legacy active_rate to 1.0 regardless
        # of its actual relative dynamics.
        loud = _tone(5.0, 0.9)
        assert compute_active_rate(loud, SR) == pytest.approx(1.0, abs=0.001)

    def test_valley_rms_percentile_scales_with_level(
        self, t2_signal: np.ndarray, t3_signal: np.ndarray
    ) -> None:
        # The core legacy defect this task fixes: valley (linear P90-P10 of
        # RMS) shrinks under attenuation by the same linear factor, instead
        # of staying constant like the v2 dB metric does (see T3 above).
        val_t2, _ = valley_rms_percentile(t2_signal, SR)
        val_t3, _ = valley_rms_percentile(t3_signal, SR)
        assert val_t2 > 0.0
        # -12 dB → linear factor 10**(-12/20) ≈ 0.2512
        assert val_t3 == pytest.approx(val_t2 * 10 ** (-12 / 20), rel=0.05)
