"""Deterministic synth primitives shared by demos and tests."""
from __future__ import annotations

import hashlib
import io

import numpy as np
from scipy.io import wavfile

SAMPLE_RATE = 44100


def _adsr_envelope(length: int, attack_sec: float, release_sec: float) -> np.ndarray:
    envelope = np.ones(length, dtype=np.float64)
    attack = min(length, int(round(attack_sec * SAMPLE_RATE)))
    release = min(length, int(round(release_sec * SAMPLE_RATE)))
    if attack > 0:
        envelope[:attack] *= np.linspace(0.0, 1.0, attack, endpoint=False)
    if release > 0:
        envelope[-release:] *= np.linspace(1.0, 0.0, release, endpoint=False)
    return envelope


def wav_bytes(samples: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    wavfile.write(buffer, SAMPLE_RATE, samples)
    return buffer.getvalue()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
