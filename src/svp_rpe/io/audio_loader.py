"""io/audio_loader.py — WAV/MP3 loading + AudioMetadata.

Loads audio files via librosa/soundfile, returns normalized waveform data
and structured metadata. Mono/stereo both supported.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import librosa
import numpy as np
import soundfile as sf
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class UnsupportedFormatError(ValueError):
    """Audio format is not supported (only WAV/MP3)."""


class DecodeError(RuntimeError):
    """Audio file could not be decoded."""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

SUPPORTED_FORMATS = frozenset({"wav", "mp3", "flac"})


class AudioMetadata(BaseModel):
    """Metadata extracted from an audio file."""

    schema_version: str = "1.0"
    file_path: str
    duration_sec: float
    sample_rate: int
    channels: int
    format: str  # "wav" | "mp3" | "flac"


class AudioData(BaseModel):
    """Loaded audio waveform + metadata.

    model_config allows numpy arrays via arbitrary_types_allowed.
    """

    model_config = {"arbitrary_types_allowed": True}

    metadata: AudioMetadata
    y_mono: object       # np.ndarray (float32, mono)
    y_stereo: object     # np.ndarray or None (float32, stereo channels)
    sr: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_audio(
    path: str | Path,
    *,
    target_sr: Optional[int] = 22050,
) -> AudioData:
    """Load an audio file and return AudioData.

    Args:
        path: path to WAV/MP3/FLAC file
        target_sr: target sample rate for resampling (None = native)

    Returns:
        AudioData with mono waveform, optional stereo, and metadata.

    Raises:
        FileNotFoundError: file does not exist
        UnsupportedFormatError: format not in SUPPORTED_FORMATS
        DecodeError: librosa/soundfile failed to decode
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"audio file not found: {p}")

    suffix = p.suffix.lower().lstrip(".")
    if suffix not in SUPPORTED_FORMATS:
        raise UnsupportedFormatError(
            f"unsupported format '{suffix}'. supported: {sorted(SUPPORTED_FORMATS)}"
        )

    try:
        # Get channel info from soundfile
        info = sf.info(str(p))
        channels = info.channels
        native_sr = info.samplerate
        duration_sec = info.duration

        # Load once with mono=False, derive y_mono from y_stereo to avoid
        # decoding + resampling the same file twice (the duplicate load was
        # the dominant memory cost: ~170 MB/30 s for stereo input).
        y_raw, sr = librosa.load(str(p), sr=target_sr, mono=False)
        y_raw = y_raw.astype(np.float32, copy=False)

        if y_raw.ndim == 2 and y_raw.shape[0] >= 2:
            y_stereo = y_raw
            y_mono = np.mean(y_raw, axis=0, dtype=np.float32)
        else:
            y_stereo = None
            y_mono = y_raw if y_raw.ndim == 1 else y_raw[0]

    except Exception as e:
        if isinstance(e, (FileNotFoundError, UnsupportedFormatError)):
            raise
        raise DecodeError(f"failed to decode {p}: {e}") from e

    metadata = AudioMetadata(
        file_path=str(p),
        duration_sec=round(duration_sec, 4),
        sample_rate=sr if target_sr is not None else native_sr,
        channels=channels,
        format=suffix,
    )

    return AudioData(
        metadata=metadata,
        y_mono=y_mono,
        y_stereo=y_stereo,
        sr=sr,
    )


def to_mono(y: np.ndarray) -> np.ndarray:
    """Convert multichannel audio to mono by averaging channels."""
    if y.ndim == 1:
        return y
    return np.mean(y, axis=0).astype(np.float32)


def normalize_audio(y: np.ndarray) -> np.ndarray:
    """Peak-normalize audio to [-1, 1]."""
    peak = np.max(np.abs(y))
    if peak == 0:
        return y
    return (y / peak).astype(np.float32)
