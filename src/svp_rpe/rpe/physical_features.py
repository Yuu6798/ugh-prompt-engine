"""rpe/physical_features.py — librosa-based physical feature computation.

All functions are deterministic: same waveform → same output.
Values are clamped to sensible ranges where applicable.
"""
from __future__ import annotations

from typing import Optional

import librosa
import numpy as np
from scipy import signal as scipy_signal

from svp_rpe.rpe.models import SpectralProfile, StereoProfile

try:
    import pyloudnorm as pyln
    _HAS_PYLOUDNORM = True
except ModuleNotFoundError:  # pragma: no cover - optional at runtime
    _HAS_PYLOUDNORM = False


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Core physical features
# ---------------------------------------------------------------------------


def compute_rms_mean(y: np.ndarray, sr: int) -> float:
    """Frame-level RMS mean."""
    rms = librosa.feature.rms(y=y)[0]
    return float(np.mean(rms))


def compute_active_rate(y: np.ndarray, sr: int, threshold: float = 0.01) -> float:
    """Fraction of frames where RMS exceeds threshold."""
    rms = librosa.feature.rms(y=y)[0]
    if len(rms) == 0:
        return 0.0
    return _clamp(float(np.sum(rms > threshold) / len(rms)))


def compute_crest_factor(y: np.ndarray) -> float:
    """Peak-to-RMS ratio."""
    rms = float(np.sqrt(np.mean(y ** 2)))
    if rms == 0:
        return 0.0
    peak = float(np.max(np.abs(y)))
    return round(peak / rms, 4)


def _valley_depth_simple(y: np.ndarray, sr: int) -> float:
    """Simple dynamic range: P90 - P10 of frame RMS. Used internally by thickness."""
    rms = librosa.feature.rms(y=y)[0]
    if len(rms) < 2:
        return 0.0
    p90 = float(np.percentile(rms, 90))
    p10 = float(np.percentile(rms, 10))
    return round(max(0.0, p90 - p10), 4)


def compute_thickness(y: np.ndarray, sr: int) -> float:
    """Sonic density composite: spectral richness + RMS + valley inverse.

    Returns a value roughly in [0, 3+] range (not clamped to [0,1]).
    """
    # Spectral richness: bandwidth normalized
    bw = librosa.feature.spectral_bandwidth(y=y, sr=sr)[0]
    spectral_richness = _clamp(float(np.mean(bw)) / 5000.0)

    # RMS normalized to ~[0,1] (0.5 is typical for loud audio)
    rms = float(np.sqrt(np.mean(y ** 2)))
    rms_norm = _clamp(rms / 0.5)

    # Valley inverse: less valley = more continuous = thicker
    valley = _valley_depth_simple(y, sr)
    valley_norm = _clamp(valley / 0.5)

    w1, w2, w3 = 1.0, 1.0, 1.0
    return round(w1 * spectral_richness + w2 * rms_norm + w3 * (1.0 - valley_norm), 4)


def compute_spectral_profile(y: np.ndarray, sr: int) -> SpectralProfile:
    """Spectral frequency distribution analysis."""
    S = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)

    # Energy per band
    total_energy = float(np.sum(S ** 2))
    if total_energy == 0:
        return SpectralProfile(
            centroid=0.0, low_ratio=0.0, mid_ratio=0.0,
            high_ratio=0.0, brightness=0.0,
        )

    low_mask = freqs < 300
    mid_mask = (freqs >= 300) & (freqs < 4000)
    high_mask = freqs >= 4000

    low_energy = float(np.sum(S[low_mask] ** 2))
    mid_energy = float(np.sum(S[mid_mask] ** 2))
    high_energy = float(np.sum(S[high_mask] ** 2))

    low_ratio = round(low_energy / total_energy, 4)
    mid_ratio = round(mid_energy / total_energy, 4)
    high_ratio = round(high_energy / total_energy, 4)

    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    brightness = round(high_ratio / max(low_ratio + mid_ratio + high_ratio, 1e-8), 4)

    return SpectralProfile(
        centroid=round(float(np.mean(centroid)), 2),
        low_ratio=low_ratio,
        mid_ratio=mid_ratio,
        high_ratio=high_ratio,
        brightness=brightness,
    )


def compute_stereo_profile(y_stereo: np.ndarray, sr: int) -> Optional[StereoProfile]:
    """Stereo field analysis. Returns None for mono input."""
    if y_stereo is None or y_stereo.ndim != 2 or y_stereo.shape[0] < 2:
        return None

    left = y_stereo[0]
    right = y_stereo[1]

    # Width: RMS of difference / RMS of sum
    diff = left - right
    summ = left + right
    rms_diff = float(np.sqrt(np.mean(diff ** 2)))
    rms_sum = float(np.sqrt(np.mean(summ ** 2)))
    width = _clamp(rms_diff / max(rms_sum, 1e-8))

    # Correlation
    if len(left) > 0:
        corr = float(np.corrcoef(left, right)[0, 1])
        if np.isnan(corr):
            corr = 0.0
    else:
        corr = 0.0

    return StereoProfile(
        width=round(width, 4),
        correlation=round(_clamp(corr, -1.0, 1.0), 4),
    )


def compute_onset_density(y: np.ndarray, sr: int) -> float:
    """Onsets per second."""
    onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time")
    duration = len(y) / sr
    if duration == 0:
        return 0.0
    return round(len(onsets) / duration, 4)


def compute_loudness(
    y: np.ndarray, sr: int
) -> tuple[Optional[float], Optional[float]]:
    """Compute ITU-R BS.1770 integrated loudness (LUFS) and true peak (dBFS).

    Accepts mono `(samples,)` and channel-first stereo `(channels, samples)`
    (the layout produced by `audio_loader.load_audio`). Internally transposes
    stereo to samples-first for pyloudnorm and aligns the resampling axis
    accordingly.

    Returns (None, None) when pyloudnorm is unavailable, the audio is shorter
    than the minimum gating block length, or the signal is digital silence.
    """
    if not _HAS_PYLOUDNORM or y.size == 0:
        return (None, None)

    if y.ndim == 1:
        signal = y
        n_samples = signal.shape[0]
    elif y.ndim == 2:
        # Codebase uses channel-first (channels, samples); pyloudnorm wants
        # samples-first (samples, channels).
        signal = y.T
        n_samples = signal.shape[0]
    else:
        return (None, None)

    # pyloudnorm requires at least 0.4 s of audio for the gating block size.
    if n_samples / sr < 0.4:
        return (None, None)

    try:
        meter = pyln.Meter(sr)
        lufs = float(meter.integrated_loudness(signal))
    except (ValueError, ZeroDivisionError):
        lufs = None
    else:
        # Digital silence yields -inf; drop to None to avoid serialising inf.
        if not np.isfinite(lufs):
            lufs = None
        else:
            lufs = round(lufs, 2)

    # True peak via 4x oversampling per ITU-R BS.1770-4 simplified path.
    peak_abs = float(np.max(np.abs(signal)))
    if peak_abs <= 0.0:
        return (lufs, None)
    try:
        upsampled = scipy_signal.resample_poly(signal, 4, 1, axis=0)
        true_peak_lin = float(np.max(np.abs(upsampled)))
    except ValueError:
        true_peak_lin = peak_abs
    if true_peak_lin <= 0.0:
        return (lufs, None)
    true_peak_dbfs = round(20.0 * float(np.log10(true_peak_lin)), 2)
    return (lufs, true_peak_dbfs)


# BPM confidence calibration (Q1-3).
# Formula: confidence = clamp(1.0 - BPM_CONFIDENCE_CV_SCALE * CV, 0.0, 1.0)
# The 5.0 scale is empirically tuned: synth samples within ±5 BPM of truth
# observe CV ∈ [0.024, 0.035], yielding confidence ∈ [0.83, 0.88], comfortably
# above the Q1-3 acceptance threshold (>0.7). Adjust if the extractor changes.
BPM_CONFIDENCE_CV_SCALE = 5.0
BPM_CONFIDENCE_AC_THRESHOLD = 0.7


def compute_bpm(y: np.ndarray, sr: int) -> tuple[Optional[float], Optional[float]]:
    """Estimate BPM via librosa.beat.beat_track. Returns (bpm, confidence).

    `confidence` reflects the **regularity of detected beats** rather than
    the static distance from a "typical" 120 BPM. Regular beats → low CV →
    high confidence. See BPM_CONFIDENCE_CV_SCALE for calibration notes; the
    Q1-3 acceptance criterion is confidence > BPM_CONFIDENCE_AC_THRESHOLD
    (0.7) when the estimate is within ±5 BPM of truth.
    """
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(np.atleast_1d(tempo)[0])
    if bpm <= 0:
        return None, 0.0

    beat_times = librosa.frames_to_time(np.asarray(beats), sr=sr)
    intervals = np.diff(beat_times)
    # Need ≥2 intervals (≥3 beats) for std to carry information. With a
    # single interval, std is mathematically 0 → CV 0 → confidence 1.0,
    # which is a false certainty when there is not enough evidence to
    # measure regularity.
    if intervals.size < 2:
        return round(bpm, 2), 0.0

    mean_interval = float(np.mean(intervals))
    if mean_interval <= 0.0:
        return round(bpm, 2), 0.0

    cv = float(np.std(intervals) / mean_interval)
    confidence = _clamp(1.0 - BPM_CONFIDENCE_CV_SCALE * cv, 0.0, 1.0)
    return round(bpm, 2), round(confidence, 4)


def compute_key(y: np.ndarray, sr: int) -> tuple[Optional[str], Optional[str], Optional[float]]:
    """Estimate key via chroma → Krumhansl-Kessler template matching.

    Returns (key_name, mode, confidence).
    """
    # Krumhansl-Kessler key profiles
    major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    key_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    if chroma.size == 0:
        return None, None, None

    chroma_mean = np.mean(chroma, axis=1)

    best_corr = -2.0
    best_key = 0
    best_mode = "major"

    for shift in range(12):
        shifted = np.roll(chroma_mean, -shift)
        corr_major = float(np.corrcoef(shifted, major_profile)[0, 1])
        corr_minor = float(np.corrcoef(shifted, minor_profile)[0, 1])
        if corr_major > best_corr:
            best_corr = corr_major
            best_key = shift
            best_mode = "major"
        if corr_minor > best_corr:
            best_corr = corr_minor
            best_key = shift
            best_mode = "minor"

    confidence = _clamp((best_corr + 1.0) / 2.0)  # normalize [-1,1] → [0,1]
    return key_names[best_key], best_mode, round(confidence, 4)
