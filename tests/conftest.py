"""conftest.py — shared test fixtures."""
from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture
def sine_wave_mono(tmp_path):
    """Generate a mono sine wave WAV file for testing."""
    import soundfile as sf

    sr = 22050
    duration = 3.0
    freq = 440.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    y = 0.5 * np.sin(2 * np.pi * freq * t).astype(np.float32)

    path = tmp_path / "test_mono.wav"
    sf.write(str(path), y, sr)
    return str(path)


@pytest.fixture
def sine_wave_stereo(tmp_path):
    """Generate a stereo sine wave WAV file for testing."""
    import soundfile as sf

    sr = 22050
    duration = 3.0
    freq_l, freq_r = 440.0, 550.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    left = 0.5 * np.sin(2 * np.pi * freq_l * t).astype(np.float32)
    right = 0.3 * np.sin(2 * np.pi * freq_r * t).astype(np.float32)
    stereo = np.column_stack([left, right])

    path = tmp_path / "test_stereo.wav"
    sf.write(str(path), stereo, sr)
    return str(path)


#: 各テストファイルの private コピー（roundtrip_preservation / roundtrip_repetition /
#: roundtrip_corpus / identity_rank / genre_misfire_audit）で禁止されていたキーの和集合。
DEFAULT_FORBIDDEN_OUTCOME_KEYS: frozenset[str] = frozenset(
    {"verdict", "passed", "pass", "failed", "fail", "ok", "loss", "threshold"}
)


def assert_no_outcome_keys(value, forbidden: frozenset[str] = DEFAULT_FORBIDDEN_OUTCOME_KEYS):
    """audit 哲学の横断不変条件: verdict / passed / loss 等の outcome キーが存在しないこと。"""
    if isinstance(value, dict):
        for key in forbidden:
            assert key not in value
        for item in value.values():
            assert_no_outcome_keys(item, forbidden)
    elif isinstance(value, list):
        for item in value:
            assert_no_outcome_keys(item, forbidden)
