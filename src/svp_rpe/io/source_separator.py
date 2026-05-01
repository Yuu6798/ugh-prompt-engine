"""Optional Demucs source separation adapter.

This module intentionally keeps Demucs behind an optional import so the normal
package install and CI path do not require torch/Demucs.
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, field_validator

try:
    from demucs.api import Separator as _DemucsAPI  # pragma: no cover - optional dependency
except ImportError:
    _DemucsAPI = None

_HAS_DEMUCS = _DemucsAPI is not None

DEFAULT_MODEL = "htdemucs_ft"
DEFAULT_SAMPLE_RATE = 44100
STEM_NAMES = ("vocals", "drums", "bass", "other")
REQUIRED_STEMS = frozenset(STEM_NAMES)


class SeparatorNotAvailableError(RuntimeError):
    """Raised when source separation is requested without Demucs installed."""


class StemBundle(BaseModel):
    """Separated mono stems returned by the source-separation adapter."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_path: str
    model_name: str
    sample_rate: int
    duration_sec: float
    stems: dict[str, np.ndarray]

    @field_validator("sample_rate")
    @classmethod
    def sample_rate_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("sample_rate must be positive")
        return value

    @field_validator("duration_sec")
    @classmethod
    def duration_non_negative(cls, value: float) -> float:
        if value < 0.0:
            raise ValueError("duration_sec must be non-negative")
        return value

    @field_validator("stems")
    @classmethod
    def stems_are_complete_mono_float32(
        cls,
        value: dict[str, np.ndarray],
    ) -> dict[str, np.ndarray]:
        keys = frozenset(value)
        if not REQUIRED_STEMS.issubset(keys):
            missing = sorted(REQUIRED_STEMS - keys)
            raise ValueError(f"stems must contain at least {sorted(REQUIRED_STEMS)}; "
                             f"missing={missing}")

        for name, stem in value.items():
            if not isinstance(stem, np.ndarray):
                raise ValueError(f"stem {name!r} must be a numpy array")
            if stem.ndim != 1:
                raise ValueError(f"stem {name!r} must be mono 1D")
            if stem.dtype != np.float32:
                raise ValueError(f"stem {name!r} must have dtype float32")
        return value


def _as_numpy(value: Any) -> np.ndarray:
    """Convert a Demucs tensor-like value to a numpy array."""
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        return value.numpy()
    return np.asarray(value)


def _to_mono_float32(value: Any) -> np.ndarray:
    arr = _as_numpy(value)
    while arr.ndim > 2 and arr.shape[0] == 1:
        arr = arr[0]
    if arr.ndim == 2:
        arr = np.mean(arr, axis=0)
    elif arr.ndim != 1:
        raise ValueError(f"expected stem tensor with 1D or 2D shape, got {arr.shape}")
    return np.asarray(arr, dtype=np.float32)


def _get_demucs_separator_class() -> type[Any]:
    if not _HAS_DEMUCS or _DemucsAPI is None:
        raise SeparatorNotAvailableError(
            "demucs is not installed. Install the optional extra with: "
            "pip install 'svp-rpe[separate]'"
        )
    return _DemucsAPI


def _separator_sample_rate(separator: Any) -> int:
    sample_rate = getattr(separator, "samplerate", None)
    if sample_rate is None:
        warnings.warn(
            "Demucs separator did not expose samplerate; using 44100 Hz.",
            RuntimeWarning,
            stacklevel=2,
        )
        return DEFAULT_SAMPLE_RATE
    sample_rate = int(sample_rate)
    if sample_rate <= 0:
        raise ValueError("Demucs samplerate must be positive")
    return sample_rate


def separate_stems(
    path: str | Path,
    *,
    model: str = DEFAULT_MODEL,
    device: str = "cpu",
) -> StemBundle:
    """Separate an audio file into vocals, drums, bass, and other stems."""
    source_path = Path(path)
    separator_cls = _get_demucs_separator_class()
    separator = separator_cls(model=model, device=device)
    _, separated = separator.separate_audio_file(source_path)

    if not isinstance(separated, dict):
        raise ValueError("Demucs separator returned an invalid stem mapping")

    stems = {name: _to_mono_float32(stem) for name, stem in separated.items()}
    sample_rate = _separator_sample_rate(separator)
    n_samples = max((len(stem) for stem in stems.values()), default=0)

    return StemBundle(
        source_path=str(source_path),
        model_name=model,
        sample_rate=sample_rate,
        duration_sec=round(n_samples / sample_rate, 4),
        stems=stems,
    )
