"""Generate deterministic synthetic sine-wave sample inputs for Q0-1."""
from __future__ import annotations

import argparse
import hashlib
import io
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import yaml
from scipy.io import wavfile

SAMPLE_RATE = 44100
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "examples" / "sample_input"

NOTE_FREQUENCIES = {
    "C3": 130.8128,
    "D3": 146.8324,
    "E3": 164.8138,
    "F#3": 184.9972,
    "G3": 195.9977,
    "A3": 220.0000,
    "B3": 246.9417,
    "C4": 261.6256,
    "D4": 293.6648,
    "E4": 329.6276,
    "F#4": 369.9944,
    "G4": 391.9954,
    "A4": 440.0000,
    "B4": 493.8833,
    "C5": 523.2511,
    "D5": 587.3295,
    "E5": 659.2551,
    "F#5": 739.9888,
    "G5": 783.9909,
    "A5": 880.0000,
    "B5": 987.7666,
}


@dataclass(frozen=True)
class SampleSpec:
    id: str
    descriptor: str
    bpm: float
    key: str
    mode: str
    time_signature: str
    duration_sec: float
    expected_brightness_band: str
    seed: int
    chords: tuple[tuple[str, ...], ...]
    harmonic_weights: tuple[float, ...]
    body_gain: float

    @property
    def filename(self) -> str:
        return f"{self.id}.wav"

    @property
    def sections(self) -> tuple[dict[str, float | str], ...]:
        intro_end = 8.0
        outro_start = self.duration_sec - 6.0
        return (
            {"label": "intro", "start_sec": 0.0, "end_sec": intro_end},
            {"label": "body", "start_sec": intro_end, "end_sec": outro_start},
            {"label": "outro", "start_sec": outro_start, "end_sec": self.duration_sec},
        )


SAMPLES = (
    SampleSpec(
        id="synth_01_slow_pad_c_major",
        descriptor="slow_pad_c_major",
        bpm=60.0,
        key="C",
        mode="major",
        time_signature="4/4",
        duration_sec=40.0,
        expected_brightness_band="low",
        seed=101,
        chords=(
            ("C3", "E3", "G3"),
            ("F#3", "A3", "C4"),
            ("G3", "B3", "D4"),
            ("C3", "E3", "G3"),
        ),
        harmonic_weights=(1.0, 0.18),
        body_gain=0.42,
    ),
    SampleSpec(
        id="synth_02_minor_pulse_a_minor",
        descriptor="minor_pulse_a_minor",
        bpm=90.0,
        key="A",
        mode="minor",
        time_signature="4/4",
        duration_sec=36.0,
        expected_brightness_band="mid",
        seed=202,
        chords=(
            ("A3", "C4", "E4"),
            ("F#3", "A3", "C4"),
            ("G3", "B3", "D4"),
            ("A3", "C4", "E4"),
        ),
        harmonic_weights=(1.0, 0.30, 0.12),
        body_gain=0.45,
    ),
    SampleSpec(
        id="synth_03_mid_groove_g_major",
        descriptor="mid_groove_g_major",
        bpm=120.0,
        key="G",
        mode="major",
        time_signature="4/4",
        duration_sec=44.0,
        expected_brightness_band="mid",
        seed=303,
        chords=(
            ("G3", "B3", "D4"),
            ("C4", "E4", "G4"),
            ("D4", "F#4", "A4"),
            ("G3", "B3", "D4"),
        ),
        harmonic_weights=(1.0, 0.36, 0.18),
        body_gain=0.48,
    ),
    SampleSpec(
        id="synth_04_waltz_fsharp_minor",
        descriptor="waltz_fsharp_minor",
        bpm=140.0,
        key="F#",
        mode="minor",
        time_signature="3/4",
        duration_sec=45.0,
        expected_brightness_band="high",
        seed=404,
        chords=(
            ("F#3", "A3", "C4"),
            ("D4", "F#4", "A4"),
            ("E4", "G4", "B4"),
            ("F#3", "A3", "C4"),
        ),
        harmonic_weights=(1.0, 0.45, 0.25, 0.10),
        body_gain=0.46,
    ),
    SampleSpec(
        id="synth_05_fast_bright_d_major",
        descriptor="fast_bright_d_major",
        bpm=170.0,
        key="D",
        mode="major",
        time_signature="4/4",
        duration_sec=42.0,
        expected_brightness_band="high",
        seed=505,
        chords=(
            ("D4", "F#4", "A4"),
            ("G4", "B4", "D5"),
            ("A4", "C5", "E5"),
            ("D4", "F#4", "A4"),
        ),
        harmonic_weights=(1.0, 0.55, 0.32, 0.16),
        body_gain=0.44,
    ),
)


def _time_axis(duration_sec: float) -> np.ndarray:
    return np.arange(int(round(duration_sec * SAMPLE_RATE)), dtype=np.float64) / SAMPLE_RATE


def _adsr_envelope(length: int, attack_sec: float, release_sec: float) -> np.ndarray:
    envelope = np.ones(length, dtype=np.float64)
    attack = min(length, int(round(attack_sec * SAMPLE_RATE)))
    release = min(length, int(round(release_sec * SAMPLE_RATE)))
    if attack > 0:
        envelope[:attack] *= np.linspace(0.0, 1.0, attack, endpoint=False)
    if release > 0:
        envelope[-release:] *= np.linspace(1.0, 0.0, release, endpoint=False)
    return envelope


def _chord_signal(
    t: np.ndarray,
    chord: Iterable[str],
    harmonic_weights: tuple[float, ...],
) -> np.ndarray:
    signal = np.zeros_like(t)
    for note in chord:
        base = NOTE_FREQUENCIES[note]
        for harmonic_index, weight in enumerate(harmonic_weights, start=1):
            signal += weight * np.sin(2.0 * np.pi * base * harmonic_index * t)
    peak = np.max(np.abs(signal))
    return signal / peak if peak else signal


def _pulse_train(t: np.ndarray, bpm: float, time_signature: str) -> np.ndarray:
    beat_period = 60.0 / bpm
    beats_per_bar = int(time_signature.split("/", 1)[0])
    pulse = np.zeros_like(t)
    for beat_index, beat_time in enumerate(np.arange(0.0, t[-1] + beat_period, beat_period)):
        accent = 1.0 if beat_index % beats_per_bar == 0 else 0.45
        width = 0.018 if accent == 1.0 else 0.012
        pulse += accent * np.exp(-0.5 * ((t - beat_time) / width) ** 2)
    return pulse


def render_sample(spec: SampleSpec) -> np.ndarray:
    rng = np.random.default_rng(seed=spec.seed)
    t = _time_axis(spec.duration_sec)
    signal = np.zeros_like(t)

    intro_end = int(round(8.0 * SAMPLE_RATE))
    outro_start = int(round((spec.duration_sec - 6.0) * SAMPLE_RATE))
    body_len = max(1, outro_start - intro_end)
    chord_len = max(1, body_len // len(spec.chords))

    intro_t = t[:intro_end]
    intro_freq = NOTE_FREQUENCIES[f"{spec.key}3"] if f"{spec.key}3" in NOTE_FREQUENCIES else 130.8128
    signal[:intro_end] = 0.22 * np.sin(2.0 * np.pi * intro_freq * intro_t)
    signal[:intro_end] *= _adsr_envelope(len(intro_t), 2.0, 1.0)

    for chord_index, chord in enumerate(spec.chords):
        start = intro_end + chord_index * chord_len
        end = outro_start if chord_index == len(spec.chords) - 1 else min(outro_start, start + chord_len)
        local_t = t[start:end] - t[start]
        chord_wave = _chord_signal(local_t, chord, spec.harmonic_weights)
        pulse = _pulse_train(local_t, spec.bpm, spec.time_signature)
        pulse = pulse / max(float(np.max(pulse)), 1.0)
        tremolo = 0.82 + 0.18 * pulse
        signal[start:end] = spec.body_gain * chord_wave * tremolo

    outro_t = t[outro_start:] - t[outro_start]
    outro_chord = _chord_signal(outro_t, spec.chords[-1], spec.harmonic_weights)
    signal[outro_start:] = 0.32 * outro_chord
    signal[outro_start:] *= _adsr_envelope(len(outro_t), 0.2, 5.5)

    noise = rng.normal(0.0, 0.0015, size=signal.shape)
    signal = signal + noise
    signal *= _adsr_envelope(len(signal), 0.02, 0.02)
    peak = np.max(np.abs(signal))
    if peak:
        signal = signal / peak * 0.82
    return np.round(signal * 32767.0).astype(np.int16)


def wav_bytes(samples: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    wavfile.write(buffer, SAMPLE_RATE, samples)
    return buffer.getvalue()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ground_truth_rows() -> list[dict]:
    rows: list[dict] = []
    for spec in SAMPLES:
        data = wav_bytes(render_sample(spec))
        rows.append(
            {
                "id": spec.id,
                "filename": spec.filename,
                "bpm": spec.bpm,
                "key": spec.key,
                "mode": spec.mode,
                "time_signature": spec.time_signature,
                "sample_rate": SAMPLE_RATE,
                "bit_depth": 16,
                "channels": 1,
                "duration_sec": spec.duration_sec,
                "expected_sections": ["intro", "body", "outro"],
                "sections": list(spec.sections),
                "section_boundaries_sec": [
                    section["start_sec"]
                    for section in spec.sections
                ]
                + [spec.duration_sec],
                "expected_brightness_band": spec.expected_brightness_band,
                "sha256": sha256_bytes(data),
            }
        )
    return rows


def write_samples(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for spec in SAMPLES:
        path = output_dir / spec.filename
        path.write_bytes(wav_bytes(render_sample(spec)))

    truth_path = output_dir / "ground_truth.yaml"
    truth_path.write_text(
        yaml.safe_dump(
            ground_truth_rows(),
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def verify_samples(output_dir: Path) -> int:
    truth_path = output_dir / "ground_truth.yaml"
    if not truth_path.is_file():
        print(f"Missing ground truth: {truth_path}", file=sys.stderr)
        return 1
    expected_rows = yaml.safe_load(truth_path.read_text(encoding="utf-8"))
    expected_by_id = {row["id"]: row for row in expected_rows}

    ok = True
    for spec in SAMPLES:
        row = expected_by_id.get(spec.id)
        if row is None:
            print(f"Missing ground-truth row for {spec.id}", file=sys.stderr)
            ok = False
            continue
        expected_hash = sha256_bytes(wav_bytes(render_sample(spec)))
        if row.get("sha256") != expected_hash:
            print(
                f"Ground truth hash mismatch for {spec.filename}: "
                f"expected {row.get('sha256')}, regenerated {expected_hash}",
                file=sys.stderr,
            )
            ok = False
        path = output_dir / spec.filename
        if not path.is_file():
            print(f"Missing WAV: {path}", file=sys.stderr)
            ok = False
            continue
        actual_hash = sha256_bytes(path.read_bytes())
        if actual_hash != expected_hash:
            print(
                f"Hash mismatch for {spec.filename}: expected {expected_hash}, got {actual_hash}",
                file=sys.stderr,
            )
            ok = False
    if ok:
        print(f"Verified {len(SAMPLES)} synth samples in {output_dir}")
        return 0
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--verify", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.verify:
        return verify_samples(args.output_dir)
    write_samples(args.output_dir)
    print(f"Wrote {len(SAMPLES)} synth samples to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
