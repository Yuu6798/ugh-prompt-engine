"""rpe/physical_features.py — librosa-based physical feature computation.

All functions are deterministic: same waveform → same output.
Values are clamped to sensible ranges where applicable.
"""
from __future__ import annotations

from typing import Optional

import librosa
from librosa.util.exceptions import ParameterError
import numpy as np
from scipy import signal as scipy_signal

from svp_rpe.rpe.models import ChordEvent, MelodyContour, SpectralProfile, StereoProfile

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


# RMS dynamic range floor: -60 dBFS guard against log(0) on near-silent frames.
DYNAMIC_RANGE_RMS_FLOOR = 1e-3


def compute_dynamic_range_db(y: np.ndarray, sr: int) -> Optional[float]:
    """Frame RMS P95/P10 ratio, expressed in dB.

    Lightweight descriptor of how much loudness varies across the track.
    Larger values = more dynamic; smaller = more compressed. NOT EBU R128 LRA
    (which requires K-weighted short-term loudness with gating); this is a
    cheap RMS-based proxy intended for cross-song comparison.

    Returns None for empty / degenerate input.
    """
    if y.size == 0:
        return None
    rms = librosa.feature.rms(y=y)[0]
    if rms.size < 2:
        return None
    p95 = float(np.percentile(rms, 95))
    p10 = float(np.percentile(rms, 10))
    # Floor both percentiles so silent/near-silent passages do not produce
    # absurdly large dB ratios; both clamped at the same floor preserves the
    # invariant that a flat signal yields ~0 dB.
    p95 = max(p95, DYNAMIC_RANGE_RMS_FLOOR)
    p10 = max(p10, DYNAMIC_RANGE_RMS_FLOOR)
    return round(20.0 * float(np.log10(p95 / p10)), 2)


def _prepare_loudness_signal(y: np.ndarray) -> tuple[np.ndarray, int] | None:
    if y.size == 0:
        return None
    if y.ndim == 1:
        return y, y.shape[0]
    if y.ndim == 2:
        # Codebase uses channel-first (channels, samples); pyloudnorm wants
        # samples-first (samples, channels).
        signal = y.T
        return signal, signal.shape[0]
    return None


def _has_min_loudness_duration(n_samples: int, sr: int) -> bool:
    # pyloudnorm requires at least 0.4 s of audio for the gating block size.
    return n_samples / sr >= 0.4


def _integrated_loudness_lufs(signal: np.ndarray, sr: int) -> Optional[float]:
    try:
        meter = pyln.Meter(sr)
        lufs = float(meter.integrated_loudness(signal))
    except (ValueError, ZeroDivisionError):
        return None
    # Digital silence yields -inf; drop to None to avoid serialising inf.
    if not np.isfinite(lufs):
        return None
    return round(lufs, 2)


def _true_peak_dbfs(signal: np.ndarray) -> Optional[float]:
    # True peak via 4x oversampling per ITU-R BS.1770-4 simplified path.
    peak_abs = float(np.max(np.abs(signal)))
    if peak_abs <= 0.0:
        return None
    try:
        upsampled = scipy_signal.resample_poly(signal, 4, 1, axis=0)
        true_peak_lin = float(np.max(np.abs(upsampled)))
    except ValueError:
        true_peak_lin = peak_abs
    if true_peak_lin <= 0.0:
        return None
    return round(20.0 * float(np.log10(true_peak_lin)), 2)


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
    if not _HAS_PYLOUDNORM:
        return (None, None)

    prepared = _prepare_loudness_signal(y)
    if prepared is None:
        return (None, None)

    signal, n_samples = prepared
    if not _has_min_loudness_duration(n_samples, sr):
        return (None, None)

    lufs = _integrated_loudness_lufs(signal, sr)
    true_peak = _true_peak_dbfs(signal)
    return (lufs, true_peak)


# BPM confidence calibration (Q1-3).
# Formula: confidence = clamp(1.0 - BPM_CONFIDENCE_CV_SCALE * CV, 0.0, 1.0)
# The 5.0 scale is empirically tuned: synth samples within ±5 BPM of truth
# observe CV ∈ [0.024, 0.035], yielding confidence ∈ [0.83, 0.88], comfortably
# above the Q1-3 acceptance threshold (>0.7). Adjust if the extractor changes.
BPM_CONFIDENCE_CV_SCALE = 5.0
BPM_CONFIDENCE_AC_THRESHOLD = 0.7

# Time signature calibration (Q1-2).
# The detector reads beat-level onset strength periodicity. Triple meter is
# emitted only when the 3-beat autocorrelation peak clearly beats nearby
# duple/quadruple candidates; otherwise "4/4" remains the conservative
# fallback instead of claiming weak evidence as a meter change.
TS_MIN_BEATS = 12
TS_WINSOR_PERCENTILE = 90.0
TS_TRIPLE_AC_THRESHOLD = 0.30
TS_TRIPLE_MARGIN_THRESHOLD = 0.15
TS_COMPOUND_AC_THRESHOLD = 0.45
TS_COMPOUND_MARGIN_THRESHOLD = 0.08
TS_FOUR_FOUR_BASE_CONFIDENCE = 0.55
TS_CONFIDENCE_GAIN = 1.5

# Chord extraction calibration (Q2-2).
CHORD_HOP_LENGTH = 2048
CHORD_MIN_DURATION_SEC = 0.75
CHORD_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Melody extraction calibration (Q2-3).
PYIN_FMIN_HZ = 65.4064   # C2
PYIN_FMAX_HZ = 2093.005  # C7
PYIN_HOP_LENGTH = 2048
PYIN_MIN_VOICING = 0.10
PYIN_HIGHPASS_HZ = 300.0


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


def _beat_strength_autocorrelation(
    beat_strengths: np.ndarray,
    *,
    max_lag: int = 8,
) -> dict[int, float]:
    """Autocorrelation over beat-level onset strengths for meter inference."""
    strengths = np.asarray(beat_strengths, dtype=float)
    if strengths.size <= max_lag:
        return {}

    if not np.any(np.isfinite(strengths)):
        return {}
    strengths = np.nan_to_num(strengths, nan=0.0, posinf=0.0, neginf=0.0)
    strengths = np.minimum(strengths, np.percentile(strengths, TS_WINSOR_PERCENTILE))
    centered = strengths - float(np.mean(strengths))
    denom = float(np.dot(centered, centered))
    if denom <= 0.0:
        return {}

    correlations: dict[int, float] = {}
    for lag in range(1, max_lag + 1):
        if centered.size <= lag:
            break
        left = centered[:-lag]
        right = centered[lag:]
        lag_denom = float(np.sqrt(np.dot(left, left) * np.dot(right, right)))
        if lag_denom <= 0.0:
            correlations[lag] = 0.0
        else:
            correlations[lag] = float(np.dot(left, right) / lag_denom)
    return correlations


def _classify_time_signature_from_beat_strengths(
    beat_strengths: np.ndarray,
) -> tuple[str, float]:
    """Classify meter from beat-level strength periodicity.

    Returns one of "3/4", "4/4", "6/8". The fallback is "4/4" with a low
    confidence when there is not enough beat evidence.
    """
    strengths = np.asarray(beat_strengths, dtype=float)
    if strengths.size < TS_MIN_BEATS:
        return "4/4", 0.0

    ac = _beat_strength_autocorrelation(strengths)
    if not ac:
        return "4/4", 0.0

    ac2 = ac.get(2, 0.0)
    ac3 = ac.get(3, 0.0)
    ac4 = ac.get(4, 0.0)
    ac6 = ac.get(6, 0.0)
    ac8 = ac.get(8, 0.0)

    compound_margin = ac6 - ac3
    if (
        ac6 >= TS_COMPOUND_AC_THRESHOLD
        and ac3 >= TS_TRIPLE_AC_THRESHOLD
        and compound_margin >= TS_COMPOUND_MARGIN_THRESHOLD
    ):
        confidence = _clamp(
            0.60 + TS_CONFIDENCE_GAIN * (ac6 - TS_COMPOUND_AC_THRESHOLD)
            + compound_margin,
        )
        return "6/8", round(confidence, 4)

    triple_margin = ac3 - max(ac2, ac4)
    if ac3 >= TS_TRIPLE_AC_THRESHOLD and triple_margin >= TS_TRIPLE_MARGIN_THRESHOLD:
        confidence = _clamp(
            0.60 + TS_CONFIDENCE_GAIN * (ac3 - TS_TRIPLE_AC_THRESHOLD)
            + triple_margin,
        )
        return "3/4", round(confidence, 4)

    four_four_evidence = max(ac4, ac8, 0.0) - max(ac3, 0.0)
    confidence = _clamp(TS_FOUR_FOUR_BASE_CONFIDENCE + max(0.0, four_four_evidence))
    return "4/4", round(confidence, 4)


def compute_time_signature(y: np.ndarray, sr: int) -> tuple[str, float]:
    """Estimate time signature from beat-level onset strength periodicity.

    The detector distinguishes the currently supported meters ("3/4", "4/4",
    "6/8") without learned models. When beat evidence is insufficient, it
    returns the conservative fallback ("4/4", 0.0).
    """
    if y.size == 0:
        return "4/4", 0.0

    _, beats = librosa.beat.beat_track(y=y, sr=sr)
    beat_frames = np.asarray(beats, dtype=int)
    if beat_frames.size < TS_MIN_BEATS:
        return "4/4", 0.0

    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512).astype(float)
    if onset_env.size == 0 or float(np.max(onset_env)) <= 0.0:
        return "4/4", 0.0
    onset_env = onset_env / float(np.max(onset_env))

    beat_strengths: list[float] = []
    for frame in beat_frames:
        lo = max(0, int(frame) - 1)
        hi = min(onset_env.size, int(frame) + 2)
        beat_strengths.append(float(np.max(onset_env[lo:hi])) if hi > lo else 0.0)

    return _classify_time_signature_from_beat_strengths(np.asarray(beat_strengths))


def _time_signature_numerator(time_signature: str) -> int:
    """Parse the numerator from a time-signature string."""
    try:
        numerator = int(str(time_signature).split("/", 1)[0])
    except (TypeError, ValueError):
        return 4
    if numerator <= 0:
        return 4
    return min(numerator, 16)


def _beat_level_onset_strengths(
    y: np.ndarray,
    sr: int,
    beat_frames: np.ndarray,
) -> np.ndarray:
    """Return normalized onset strength sampled around each beat frame."""
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512).astype(float)
    if onset_env.size == 0 or float(np.max(onset_env)) <= 0.0:
        return np.zeros(len(beat_frames), dtype=float)
    onset_env = onset_env / float(np.max(onset_env))

    beat_strengths: list[float] = []
    for frame in beat_frames:
        lo = max(0, int(frame) - 1)
        hi = min(onset_env.size, int(frame) + 2)
        beat_strengths.append(float(np.max(onset_env[lo:hi])) if hi > lo else 0.0)
    return np.asarray(beat_strengths, dtype=float)


def _select_downbeat_phase(beat_strengths: np.ndarray, beats_per_bar: int) -> int:
    """Choose the strongest metrical phase as the downbeat phase."""
    strengths = np.asarray(beat_strengths, dtype=float)
    if strengths.size == 0 or beats_per_bar <= 1:
        return 0

    phase_count = min(beats_per_bar, strengths.size)
    phase_means: list[float] = []
    for phase in range(phase_count):
        phase_values = strengths[phase::beats_per_bar]
        phase_means.append(float(np.mean(phase_values)) if phase_values.size else 0.0)
    return int(np.argmax(np.asarray(phase_means)))


def compute_downbeat_times(y: np.ndarray, sr: int, time_signature: str) -> list[float]:
    """Estimate downbeat times from deterministic beat tracking.

    Q2-1 deliberately keeps this lightweight and dependency-free: madmom is the
    roadmap target, but the current Python 3.11 environment cannot build it
    without extra native/Cython setup. This detector reuses librosa beats, then
    selects the strongest beat-strength phase within each bar.
    """
    if y.size == 0:
        return []

    beats_per_bar = _time_signature_numerator(time_signature)
    if beats_per_bar <= 0:
        return []

    _, beats = librosa.beat.beat_track(y=y, sr=sr)
    beat_frames = np.asarray(beats, dtype=int)
    if beat_frames.size < max(2, beats_per_bar):
        return []

    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    beat_strengths = _beat_level_onset_strengths(y, sr, beat_frames)
    phase = _select_downbeat_phase(beat_strengths, beats_per_bar)

    duration = len(y) / sr
    downbeats = beat_times[phase::beats_per_bar]
    return [
        round(float(t), 4)
        for t in downbeats
        if 0.0 <= float(t) <= duration
    ]


def _chord_templates() -> list[tuple[str, str, np.ndarray]]:
    """Return normalized major/minor triad templates."""
    templates: list[tuple[str, str, np.ndarray]] = []
    for root_index, root in enumerate(CHORD_NAMES):
        for quality, intervals in (("major", (0, 4, 7)), ("minor", (0, 3, 7))):
            vector = np.zeros(12, dtype=float)
            for interval, weight in zip(intervals, (1.0, 0.85, 0.8)):
                vector[(root_index + interval) % 12] = weight
            norm = float(np.linalg.norm(vector))
            if norm > 0.0:
                vector = vector / norm
            templates.append((root, quality, vector))
    return templates


def _classify_chroma_frame(
    chroma_frame: np.ndarray,
    templates: list[tuple[str, str, np.ndarray]],
) -> tuple[str, str, str, float]:
    """Classify one chroma frame as a major/minor triad."""
    frame = np.asarray(chroma_frame, dtype=float)
    norm = float(np.linalg.norm(frame))
    if norm <= 0.0:
        return "C major", "C", "major", 0.0
    frame = frame / norm

    scores = np.asarray([float(np.dot(frame, template)) for _, _, template in templates])
    best_index = int(np.argmax(scores))
    root, quality, _ = templates[best_index]
    confidence = _clamp(float(scores[best_index]))
    return f"{root} {quality}", root, quality, confidence


def _compute_chord_chroma(y: np.ndarray, sr: int) -> Optional[np.ndarray]:
    if y.size == 0:
        return None
    if float(np.max(np.abs(y))) <= 1e-8:
        return None
    try:
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=CHORD_HOP_LENGTH)
    except (ValueError, ParameterError):
        return None
    if chroma.size == 0 or chroma.shape[1] == 0:
        return None
    return chroma


def _classify_chroma_frames(
    chroma: np.ndarray,
    templates: list[tuple[str, str, np.ndarray]],
) -> tuple[list[str], list[str], list[str], list[float]]:
    labels: list[str] = []
    roots: list[str] = []
    qualities: list[str] = []
    confidences: list[float] = []
    for frame_index in range(chroma.shape[1]):
        chord, root, quality, confidence = _classify_chroma_frame(
            chroma[:, frame_index], templates,
        )
        labels.append(chord)
        roots.append(root)
        qualities.append(quality)
        confidences.append(confidence)
    return labels, roots, qualities, confidences


def _chord_frame_times(frame_count: int, *, duration: float, sr: int) -> np.ndarray:
    frame_times = librosa.frames_to_time(
        np.arange(frame_count + 1),
        sr=sr,
        hop_length=CHORD_HOP_LENGTH,
    )
    frame_times[-1] = min(float(frame_times[-1]), duration)
    return frame_times


def _build_chord_event(
    *,
    start_index: int,
    end_index: int,
    labels: list[str],
    roots: list[str],
    qualities: list[str],
    confidences: list[float],
    frame_times: np.ndarray,
) -> ChordEvent | None:
    start_sec = float(frame_times[start_index])
    end_sec = float(frame_times[end_index])
    if end_sec - start_sec < CHORD_MIN_DURATION_SEC:
        return None
    confidence = float(np.mean(confidences[start_index:end_index]))
    return ChordEvent(
        chord=labels[start_index],
        root=roots[start_index],
        quality=qualities[start_index],
        start_sec=round(start_sec, 4),
        end_sec=round(end_sec, 4),
        confidence=round(_clamp(confidence), 4),
    )


def _merge_chord_frames(
    *,
    labels: list[str],
    roots: list[str],
    qualities: list[str],
    confidences: list[float],
    frame_times: np.ndarray,
) -> list[ChordEvent]:
    events: list[ChordEvent] = []
    start_index = 0
    for frame_index in range(1, len(labels) + 1):
        if frame_index < len(labels) and labels[frame_index] == labels[start_index]:
            continue
        event = _build_chord_event(
            start_index=start_index,
            end_index=frame_index,
            labels=labels,
            roots=roots,
            qualities=qualities,
            confidences=confidences,
            frame_times=frame_times,
        )
        if event is not None:
            events.append(event)
        start_index = frame_index
    return events


def compute_chord_events(y: np.ndarray, sr: int) -> list[ChordEvent]:
    """Estimate time-bounded major/minor chord events from chroma templates.

    This is the lightweight Q2-2 baseline: deterministic, dependency-free, and
    deliberately limited to major/minor triads. It is intended to recover the
    main I/IV/V-style harmonic blocks in the synthetic validation set, not to
    be a production chord recognizer.
    """
    chroma = _compute_chord_chroma(y, sr)
    if chroma is None:
        return []

    labels, roots, qualities, confidences = _classify_chroma_frames(
        chroma,
        _chord_templates(),
    )
    frame_times = _chord_frame_times(chroma.shape[1], duration=len(y) / sr, sr=sr)
    return _merge_chord_frames(
        labels=labels,
        roots=roots,
        qualities=qualities,
        confidences=confidences,
        frame_times=frame_times,
    )


def _has_melody_signal(y: np.ndarray) -> bool:
    return y.size > 0 and float(np.max(np.abs(y))) > 1e-8


def _highpass_melody_signal(y: np.ndarray, sr: int) -> np.ndarray:
    if sr <= int(PYIN_HIGHPASS_HZ * 2) or y.size <= 32:
        return y
    sos = scipy_signal.butter(
        4,
        PYIN_HIGHPASS_HZ / (sr / 2.0),
        btype="highpass",
        output="sos",
    )
    try:
        return scipy_signal.sosfiltfilt(sos, y)
    except ValueError:
        return y


def _run_pyin(
    y: np.ndarray,
    sr: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    try:
        f0, voiced_flag, voiced_prob = librosa.pyin(
            y,
            sr=sr,
            fmin=PYIN_FMIN_HZ,
            fmax=PYIN_FMAX_HZ,
            hop_length=PYIN_HOP_LENGTH,
        )
    except (ValueError, ParameterError):
        return None

    if f0 is None or voiced_prob is None or len(f0) == 0:
        return None
    return f0, voiced_flag, voiced_prob


def _prepare_voicing(
    f0: np.ndarray,
    voiced_flag: np.ndarray,
    voiced_prob: np.ndarray,
) -> tuple[np.ndarray, np.ndarray] | None:
    voiced = np.asarray(voiced_flag, dtype=bool)
    voicing = np.nan_to_num(np.asarray(voiced_prob, dtype=float), nan=0.0)
    if voicing.size == 0 or float(np.max(voicing)) < PYIN_MIN_VOICING:
        return None
    frequencies = np.where(voiced & np.isfinite(f0), f0, 0.0)
    return frequencies, voicing


def _melody_contour_from_arrays(
    frequencies: np.ndarray,
    voicing: np.ndarray,
    sr: int,
) -> MelodyContour:
    times = librosa.times_like(frequencies, sr=sr, hop_length=PYIN_HOP_LENGTH)
    return MelodyContour(
        times=[round(float(t), 4) for t in times],
        frequencies_hz=[round(float(freq), 2) for freq in frequencies],
        voicing=[round(_clamp(float(prob)), 4) for prob in voicing],
    )


def compute_melody_contour(y: np.ndarray, sr: int) -> Optional[MelodyContour]:
    """Estimate a monophonic melody contour using librosa.pyin."""
    if not _has_melody_signal(y):
        return None

    pyin_result = _run_pyin(_highpass_melody_signal(y, sr), sr)
    if pyin_result is None:
        return None

    f0, voiced_flag, voiced_prob = pyin_result
    prepared = _prepare_voicing(f0, voiced_flag, voiced_prob)
    if prepared is None:
        return None

    frequencies, voicing = prepared
    return _melody_contour_from_arrays(frequencies, voicing, sr)


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
