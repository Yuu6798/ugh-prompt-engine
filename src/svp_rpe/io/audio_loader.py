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

        # Load mono via librosa (handles resampling)
        y_mono, sr = librosa.load(str(p), sr=target_sr, mono=True)
        y_mono = y_mono.astype(np.float32)

        # Load stereo if multichannel
        y_stereo = None
        if channels >= 2:
            y_raw, _ = librosa.load(str(p), sr=target_sr, mono=False)
            y_stereo = y_raw.astype(np.float32)

    except Exception as e:
        if isinstance(e, (FileNotFoundError, UnsupportedFormatError)):
            raise
        raise DecodeError(f"failed to decode {p}: {e}") from e

    metadata = AudioMetadata(
        file_path=str(p),
        duration_sec=round(duration_sec, 4),
        sample_rate=sr if target_sr else native_sr,
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
