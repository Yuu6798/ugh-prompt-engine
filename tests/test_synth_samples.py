"""Regression tests for deterministic Q0-1 synthetic sample audio."""
from __future__ import annotations

import subprocess
import sys
import shutil
from pathlib import Path

import soundfile as sf
import yaml

from svp_rpe.io.audio_loader import load_audio

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "examples" / "sample_input"
GENERATOR = ROOT / "scripts" / "generate_synth_samples.py"


def _ground_truth_rows() -> list[dict]:
    return yaml.safe_load((SAMPLE_DIR / "ground_truth.yaml").read_text(encoding="utf-8"))


def test_synth_samples_match_ground_truth_audio_metadata() -> None:
    rows = _ground_truth_rows()

    assert len(rows) == 5
    assert sorted(row["bpm"] for row in rows) == [60.0, 90.0, 120.0, 140.0, 170.0]
    assert {(row["key"], row["mode"]) for row in rows} == {
        ("C", "major"),
        ("A", "minor"),
        ("G", "major"),
        ("F#", "minor"),
        ("D", "major"),
    }
    assert [row["time_signature"] for row in rows].count("4/4") == 4
    assert [row["time_signature"] for row in rows].count("3/4") == 1

    for row in rows:
        path = SAMPLE_DIR / row["filename"]
        assert path.is_file()
        assert path.name == f"{row['id']}.wav"
        assert row["expected_sections"] == ["intro", "body", "outro"]
        assert 30.0 <= row["duration_sec"] <= 60.0

        info = sf.info(str(path))
        assert info.samplerate == 44100
        assert info.channels == 1
        assert info.subtype == "PCM_16"
        assert abs(info.duration - row["duration_sec"]) < 0.001

        audio = load_audio(path, target_sr=None)
        assert audio.metadata.sample_rate == 44100
        assert audio.metadata.channels == 1
        assert abs(audio.metadata.duration_sec - row["duration_sec"]) < 0.001


def test_synth_sample_verify_command_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--verify"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Verified 5 synth samples" in result.stdout


def test_synth_sample_verify_rejects_metadata_mismatch(tmp_path: Path) -> None:
    for wav_path in SAMPLE_DIR.glob("synth_*.wav"):
        shutil.copy2(wav_path, tmp_path / wav_path.name)

    rows = _ground_truth_rows()
    rows[0]["bpm"] = 61.0
    (tmp_path / "ground_truth.yaml").write_text(
        yaml.safe_dump(rows, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--output-dir", str(tmp_path), "--verify"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Ground truth metadata mismatch" in result.stderr
