from __future__ import annotations

import pytest

from voice_genesis.calibration.fixtures.determinism import (
    check_determinism_fresh_process,
    check_determinism_in_process,
)
from voice_genesis.calibration.fixtures.matrix import FixtureRow
from voice_genesis.calibration.vocab import BlockedCode

SECRET = b"\x77" * 32


def _short_f0_row() -> FixtureRow:
    return FixtureRow(
        family="F0_CONTROL",
        block="TRUTH_CORE",
        f0_hz=261.626,
        sr_hz=48000,
        gain_dbfs=-12.0,
        duration_s=0.15,
        noise_clean=True,
        noise_snr_db=None,
        context="steady-isolated",
    )


def _short_aperiodicity_row() -> FixtureRow:
    return FixtureRow(
        family="APERIODICITY_GT",
        block="TRUTH_CORE",
        f0_hz=130.813,
        sr_hz=48000,
        gain_dbfs=-12.0,
        duration_s=0.15,
        noise_clean=True,
        noise_snr_db=None,
        context="steady-isolated",
        injected_noise_fraction=0.30,
        bandwise_band=None,
    )


def test_in_process_determinism_is_byte_identical() -> None:
    row = _short_f0_row()
    result = check_determinism_in_process(
        row,
        SECRET,
        campaign_id="RUN10-CAL",
        family="F0_CONTROL",
        split="CALIBRATION",
        row_id="row-det-1",
    )
    assert result.identical is True
    assert result.blocked_code is None
    assert result.pcm_hex_a == result.pcm_hex_b


@pytest.mark.slow
def test_fresh_process_determinism_f0_control() -> None:
    row = _short_f0_row()
    result = check_determinism_fresh_process(
        row,
        SECRET,
        campaign_id="RUN10-CAL",
        family="F0_CONTROL",
        split="CALIBRATION",
        row_id="row-det-2",
    )
    assert result.identical is True
    assert result.blocked_code is None
    assert result.pcm_hex_a == result.pcm_hex_b
    assert len(result.pcm_hex_a) > 0


@pytest.mark.slow
def test_fresh_process_determinism_aperiodicity_uses_declared_rng_stream() -> None:
    """noise を消費する family (乱数 stream 由来) でも fresh-process 間で
    byte 一致することを確認する（RNG が `row_id`/`probe_index` から
    決定論的に導出されていることの実地検証）。"""
    row = _short_aperiodicity_row()
    result = check_determinism_fresh_process(
        row,
        SECRET,
        campaign_id="RUN10-CAL",
        family="APERIODICITY_GT",
        split="CALIBRATION",
        row_id="row-det-3",
    )
    assert result.identical is True
    assert result.blocked_code is None


def test_blocked_code_value_matches_vocab() -> None:
    assert (
        BlockedCode.BLOCKED_C1_GENERATOR_NONDETERMINISTIC.value
        == "BLOCKED_C1_GENERATOR_NONDETERMINISTIC"
    )
