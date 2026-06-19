"""Deterministic CompositionScore performer used by C4/R0 harnesses."""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from svp_rpe.compose.models import CompositionScore, StructureSection
from svp_rpe.perform.synth import SAMPLE_RATE, _adsr_envelope

KEY_PATTERN = re.compile(r"^\s*([A-Ga-g])\s*([#b]?)\s*(major|minor)\s*$", re.IGNORECASE)
PITCH_CLASSES = {
    "C": 0,
    "C#": 1,
    "D": 2,
    "D#": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "G": 7,
    "G#": 8,
    "A": 9,
    "A#": 10,
    "B": 11,
}
MINOR_TRIAD = (0, 3, 7)
MAJOR_TRIAD = (0, 4, 7)
Progression = tuple[tuple[int, tuple[int, ...]], ...]
MINOR_PROGRESSION: Progression = (
    (0, MINOR_TRIAD),
    (8, MAJOR_TRIAD),
    (10, MAJOR_TRIAD),
    (0, MINOR_TRIAD),
)
MAJOR_PROGRESSION: Progression = (
    (0, MAJOR_TRIAD),
    (5, MAJOR_TRIAD),
    (7, MAJOR_TRIAD),
    (0, MAJOR_TRIAD),
)


@dataclass(frozen=True)
class PerformanceStyle:
    """Deterministic performer style; the score stays fixed, only the take changes."""

    name: str
    bpm_bias: float = 0.0
    transpose: int = 0
    bright_voicing: bool = False
    flatten_dynamics: bool = False
    seed: int = 0


FIRST_TAKE = PerformanceStyle(
    name="first_take",
    bpm_bias=-28.0,
    transpose=4,
    bright_voicing=True,
    flatten_dynamics=True,
    seed=11,
)
FAITHFUL_TAKE = PerformanceStyle(name="faithful_take", seed=12)
STYLES = (FIRST_TAKE, FAITHFUL_TAKE)


@dataclass(frozen=True)
class _SectionProfile:
    level: float
    pulse: bool
    melody: bool
    drone_only: bool
    rest_gate: bool = False


def parse_key(text: str) -> tuple[int, str]:
    """Parse a key string such as ``C minor`` into pitch class and mode."""

    match = KEY_PATTERN.match(text.replace("♯", "#").replace("♭", "b"))
    if match is None:
        raise ValueError(f"unsupported key spec: {text!r}")
    tonic = PITCH_CLASSES[match.group(1).upper()]
    accidental = match.group(2)
    if accidental == "#":
        tonic = (tonic + 1) % 12
    elif accidental == "b":
        tonic = (tonic - 1) % 12
    return tonic, match.group(3).lower()


def midi_to_freq(midi_note: float) -> float:
    return 440.0 * 2.0 ** ((midi_note - 69.0) / 12.0)


def _section_profile(section: StructureSection, *, flatten: bool) -> _SectionProfile:
    if flatten:
        return _SectionProfile(level=0.70, pulse=True, melody=True, drone_only=False)
    hint = f"{section.physical} {section.role}".lower()
    rest_gate = "rest" in hint
    if "silence" in hint or "no kick" in hint:
        return _SectionProfile(level=0.10, pulse=False, melody=False, drone_only=True)
    if "low density" in hint or "sub bass" in hint:
        return _SectionProfile(level=0.25, pulse=False, melody=False, drone_only=True)
    if "sparse" in hint:
        return _SectionProfile(
            level=0.45, pulse=True, melody=False, drone_only=False, rest_gate=rest_gate
        )
    if "full energy" in hint or "release" in hint:
        return _SectionProfile(level=0.85, pulse=True, melody=True, drone_only=False)
    return _SectionProfile(
        level=0.55, pulse=True, melody=True, drone_only=False, rest_gate=rest_gate
    )


def _triad_frequencies(
    tonic: int,
    degree_offset: int,
    triad: tuple[int, ...],
    *,
    base_octave_midi: int,
) -> list[float]:
    root = base_octave_midi + (tonic + degree_offset) % 12
    return [midi_to_freq(root + interval) for interval in triad]


def _resolve_progression(score: CompositionScore, tonic_key: int, mode: str) -> Progression:
    if score.events is not None and score.events.chord_progression:
        return tuple(
            (
                (PITCH_CLASSES[chord.root] - tonic_key) % 12,
                MAJOR_TRIAD if chord.quality == "major" else MINOR_TRIAD,
            )
            for chord in score.events.chord_progression
        )
    return MINOR_PROGRESSION if mode == "minor" else MAJOR_PROGRESSION


def _chord_wave(
    t: np.ndarray,
    frequencies: list[float],
    harmonic_weights: tuple[float, ...],
) -> np.ndarray:
    signal = np.zeros_like(t)
    for freq in frequencies:
        for harmonic_index, weight in enumerate(harmonic_weights, start=1):
            signal += weight * np.sin(2.0 * np.pi * freq * harmonic_index * t)
    peak = float(np.max(np.abs(signal)))
    return signal / peak if peak else signal


def _pulse_train(t: np.ndarray, bpm: float, beats_per_bar: int) -> np.ndarray:
    beat_period = 60.0 / bpm
    pulse = np.zeros_like(t)
    if len(t) == 0:
        return pulse
    for beat_index, beat_time in enumerate(np.arange(0.0, t[-1] + beat_period, beat_period)):
        accent = 1.0 if beat_index % beats_per_bar == 0 else 0.45
        width = 0.018 if accent == 1.0 else 0.012
        pulse += accent * np.exp(-0.5 * ((t - beat_time) / width) ** 2)
    peak = float(np.max(pulse))
    return pulse / peak if peak else pulse


def _rest_gate_mask(length: int, bar_sec: float, beats_per_bar: int) -> np.ndarray:
    mask = np.ones(length, dtype=np.float64)
    bar_samples = int(round(bar_sec * SAMPLE_RATE))
    if bar_samples <= 0:
        return mask
    beat_samples = max(1, bar_samples // beats_per_bar)
    fade = min(beat_samples // 8, int(0.01 * SAMPLE_RATE))
    start = bar_samples - beat_samples
    while start < length:
        end = min(start + beat_samples, length)
        mask[start:end] = 0.0
        if fade > 0 and start - fade >= 0:
            mask[start - fade:start] = np.linspace(1.0, 0.0, fade, endpoint=False)
        if fade > 0 and end + fade <= length:
            mask[end:end + fade] = np.linspace(0.0, 1.0, fade, endpoint=False)
        start += bar_samples
    return mask


def perform(score: CompositionScore, style: PerformanceStyle) -> np.ndarray:
    """Perform a CompositionScore into deterministic int16 mono samples."""

    if not score.structure:
        raise ValueError("perform() requires at least one structure section")
    tonic, mode = parse_key(score.physical.key)
    progression = _resolve_progression(score, tonic, mode)
    tonic = (tonic + style.transpose) % 12
    bpm = float(score.physical.bpm) + style.bpm_bias
    beats_per_bar = int(score.physical.time_signature.split("/", 1)[0])
    bar_sec = beats_per_bar * 60.0 / bpm

    brightness_target = score.physical.brightness.strip().lower()
    dark_target = brightness_target == "dark"
    if style.bright_voicing or brightness_target == "bright":
        harmonic_weights: tuple[float, ...] = (1.0, 0.55, 0.35, 0.20)
        base_octave_midi = 60
    elif dark_target:
        harmonic_weights = (1.0, 0.18)
        base_octave_midi = 48
    else:
        harmonic_weights = (1.0, 0.35, 0.15)
        base_octave_midi = 48

    rng = np.random.default_rng(seed=style.seed)
    chunks: list[np.ndarray] = []
    for section in score.structure:
        profile = _section_profile(section, flatten=style.flatten_dynamics)
        section_len = int(round(section.bars * bar_sec * SAMPLE_RATE))
        t = np.arange(section_len, dtype=np.float64) / SAMPLE_RATE
        if profile.drone_only:
            root_freq = midi_to_freq(base_octave_midi - 12 + tonic)
            wave = _chord_wave(t, [root_freq], harmonic_weights)
        else:
            block = max(1, section_len // len(progression))
            wave = np.zeros_like(t)
            for chord_index, (degree, triad) in enumerate(progression):
                start = chord_index * block
                end = section_len if chord_index == len(progression) - 1 else start + block
                local_t = t[start:end] - t[start]
                frequencies = _triad_frequencies(
                    tonic, degree, triad, base_octave_midi=base_octave_midi
                )
                wave[start:end] = _chord_wave(local_t, frequencies, harmonic_weights)
        if profile.pulse:
            pulse = _pulse_train(t, bpm, beats_per_bar)
            wave *= 0.55 + 0.45 * pulse
        if profile.melody:
            melody_degrees = tuple(progression[index % len(progression)] for index in range(4))
            block = max(1, section_len // len(melody_degrees))
            for note_index, (degree, triad) in enumerate(melody_degrees):
                start = note_index * block
                end = section_len if note_index == len(melody_degrees) - 1 else start + block
                local_t = t[start:end] - t[start]
                freq = _triad_frequencies(
                    tonic, degree, triad, base_octave_midi=base_octave_midi + 12
                )[0]
                note = np.sin(2.0 * np.pi * freq * local_t)
                note *= _adsr_envelope(len(note), 0.03, 0.05)
                wave[start:end] += 0.5 * note
        if profile.rest_gate:
            wave *= _rest_gate_mask(section_len, bar_sec, beats_per_bar)
        wave *= profile.level
        wave *= _adsr_envelope(section_len, 0.05, 0.08)
        chunks.append(wave)

    signal = np.concatenate(chunks)
    signal = signal + rng.normal(0.0, 0.0015, size=signal.shape)
    signal *= _adsr_envelope(len(signal), 0.02, 0.02)
    peak = float(np.max(np.abs(signal)))
    if peak:
        signal = signal / peak * 0.82
    return np.round(signal * 32767.0).astype(np.int16)


def scaled_score(score: CompositionScore, *, bars_scale: float) -> CompositionScore:
    """Return a score with scaled structure bars while preserving schema validation."""

    data = score.model_dump()
    for section in data["structure"]:
        section["bars"] = max(1, int(round(section["bars"] * bars_scale)))
    return CompositionScore.model_validate(data)
