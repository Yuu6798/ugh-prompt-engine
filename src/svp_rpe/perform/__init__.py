"""Deterministic performer package for CompositionScore harnesses."""
from __future__ import annotations

from .performer import (
    FAITHFUL_TAKE,
    FIRST_TAKE,
    STYLES,
    PerformanceStyle,
    parse_key,
    perform,
    scaled_score,
)
from .synth import SAMPLE_RATE, _adsr_envelope, sha256_bytes, wav_bytes

__all__ = [
    "FAITHFUL_TAKE",
    "FIRST_TAKE",
    "SAMPLE_RATE",
    "STYLES",
    "PerformanceStyle",
    "_adsr_envelope",
    "parse_key",
    "perform",
    "scaled_score",
    "sha256_bytes",
    "wav_bytes",
]
