"""tests/_melody_helpers.py — melody (M3) 系テストで byte-identical に重複していた
private ヘルパーの集約先（機械的リファクタ、挙動は不変）。"""
from __future__ import annotations

from pathlib import Path
from typing import List

import numpy as np
import soundfile as sf
import yaml

from svp_rpe.melody.observability import MelodyNote, ObservabilityThresholds
from svp_rpe.melody.representation import M3ComparisonConfig, load_m3_registry

ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = ROOT / "tests" / "fixtures" / "melody_bench"
M1_REGISTRY_PATH = BENCH_DIR / "registry.yaml"
M3_REGISTRY_PATH = BENCH_DIR / "m3_comparison_registry.yaml"

_TONE_SAMPLE_RATE = 22050
_TONE_DURATION_SEC = 0.35  # librosa pitch_shift/time_stretch が安定して動く最小限の短さ


def default_config() -> M3ComparisonConfig:
    config, _ = load_m3_registry(M3_REGISTRY_PATH)
    return config


def default_thresholds() -> ObservabilityThresholds:
    mapping = yaml.safe_load(M1_REGISTRY_PATH.read_text(encoding="utf-8"))
    return ObservabilityThresholds.from_registry(mapping["observation_gate"])


def note(
    pitch_midi: float, start_sec: float, end_sec: float, confidence: float = 0.9
) -> MelodyNote:
    return MelodyNote(
        start_sec=start_sec, end_sec=end_sec, pitch_midi=pitch_midi, confidence=confidence
    )


def good_notes(shift: int = 0) -> List[MelodyNote]:
    """観測ゲートを通す 2 フレーズ・10 ノートの旋律。"""
    phrase1_pitches = [60, 62, 64, 65, 67]
    phrase2_pitches = [69, 67, 65, 64, 62]
    notes: List[MelodyNote] = []
    t = 0.0
    for p in phrase1_pitches:
        notes.append(note(p + shift, t, t + 0.25))
        t += 0.3
    t += 1.0  # フレーズ境界（phrase_gap_sec=0.6 を超えるギャップ）
    for p in phrase2_pitches:
        notes.append(note(p + shift, t, t + 0.25))
        t += 0.3
    return notes


def write_tone_wav(
    path: Path,
    *,
    freq: float,
    sample_rate: int = _TONE_SAMPLE_RATE,
    duration_sec: float = _TONE_DURATION_SEC,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(round(sample_rate * duration_sec))
    t = np.linspace(0.0, duration_sec, n, endpoint=False)
    y = (0.2 * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)
    sf.write(path, y, sample_rate, subtype="FLOAT")
