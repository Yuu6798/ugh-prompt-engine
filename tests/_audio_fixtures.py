"""tests/_audio_fixtures.py — 複数テストファイルで重複していた WAV fixture
書き出しヘルパーの集約先（機械的リファクタ、挙動は不変）。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf


def write_wav(path: Path, *, seconds: float = 0.05, sample_rate: int = 48000) -> None:
    """Write a tiny real mono WAV file that `librosa.load` can decode.

    A short sine burst rather than silence, so accidental all-zero
    handling elsewhere can't mask a bug.
    """
    n_samples = max(1, int(round(seconds * sample_rate)))
    t = np.linspace(0, seconds, n_samples, endpoint=False)
    y = (0.2 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    sf.write(str(path), y, sample_rate)
