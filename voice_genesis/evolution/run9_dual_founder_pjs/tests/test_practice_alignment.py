"""PRACTICE actor-local projection/alignment determinism and fail-closed tests."""
from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import practice_alignment as pa  # noqa: E402


MUSICXML = b"""<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list>
  <part id="P1"><measure number="1">
    <attributes><divisions>1</divisions></attributes>
    <direction><sound tempo="120"/></direction>
    <note><rest/><duration>1</duration><voice>1</voice></note>
    <note><pitch><step>A</step><octave>3</octave></pitch><duration>1</duration><voice>1</voice><lyric><text>a</text></lyric></note>
    <note><pitch><step>B</step><octave>3</octave></pitch><duration>1</duration><voice>1</voice><lyric><text>i</text></lyric></note>
    <note><rest/><duration>1</duration><voice>1</voice></note>
    <note><pitch><step>C</step><octave>4</octave></pitch><duration>2</duration><voice>1</voice><lyric><text>u</text></lyric></note>
  </measure></part>
</score-partwise>
"""


def _wav24(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0 - 1.0 / (1 << 23))
    values = np.rint(clipped * (1 << 23)).astype(np.int64)
    unsigned = np.where(values < 0, values + (1 << 24), values).astype(np.uint32)
    payload = np.empty((len(unsigned), 3), dtype=np.uint8)
    payload[:, 0] = unsigned & 0xFF
    payload[:, 1] = (unsigned >> 8) & 0xFF
    payload[:, 2] = (unsigned >> 16) & 0xFF
    data = payload.tobytes()
    fmt = struct.pack("<HHIIHH", 1, 1, pa.SAMPLE_RATE, pa.SAMPLE_RATE * 3, 3, 24)
    return (
        b"RIFF"
        + struct.pack("<I", 4 + (8 + len(fmt)) + (8 + len(data)))
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


def _synthetic_song() -> bytes:
    rate = pa.SAMPLE_RATE
    parts = [np.zeros(rate // 5, dtype=np.float64)]
    phase = 0.0
    for frequency, duration in ((220.0, 0.5), (246.9416506, 0.5), (261.6255653, 1.0)):
        count = int(rate * duration)
        t = np.arange(count, dtype=np.float64) / rate
        tone = 0.25 * np.sin(2.0 * math.pi * frequency * t + phase)
        ramp = min(rate // 100, count // 4)
        tone[:ramp] *= np.linspace(0.0, 1.0, ramp, endpoint=False)
        tone[-ramp:] *= np.linspace(1.0, 0.0, ramp, endpoint=False)
        parts.append(tone)
        phase = float((phase + 2.0 * math.pi * frequency * duration) % (2.0 * math.pi))
    parts.append(np.zeros(rate // 5, dtype=np.float64))
    return _wav24(np.concatenate(parts))


def test_projection_is_exact_six_field_and_byte_deterministic() -> None:
    first = pa.build_score_projection(MUSICXML, label="fixture.musicxml")
    second = pa.build_score_projection(MUSICXML, label="fixture.musicxml")
    assert frozenset(first) == pa.PROJECTION_KEYS
    assert pa.canonical_json_bytes(first) == pa.canonical_json_bytes(second)
    assert first["mora_order"] == [0, 1, 2]
    assert first["phrase_grouping"] == [0, 0, 1]
    assert all(row["phoneme_sequence"] == [] for row in first["lyrics_phoneme_sequence"])


def test_alignment_is_deterministic_monotonic_and_complete() -> None:
    projection = pa.build_score_projection(MUSICXML)
    wav = _synthetic_song()
    first = pa.align_wav_to_projection(wav, projection)
    second = pa.align_wav_to_projection(wav, projection)
    assert pa.canonical_json_bytes(first) == pa.canonical_json_bytes(second)
    assert first["status"] == "ALIGNED"
    assert len(first["boundaries_s"]) == projection["mora_count"] + 1
    assert all(
        first["boundaries_s"][index] < first["boundaries_s"][index + 1]
        for index in range(projection["mora_count"])
    )


def test_silence_fails_without_boundary_or_zero_fill() -> None:
    projection = pa.build_score_projection(MUSICXML)
    result = pa.align_wav_to_projection(_wav24(np.zeros(pa.SAMPLE_RATE)), projection)
    assert result["status"] == "ALIGNMENT_FAILED"
    assert result["boundaries_s"] == []
    assert result["total_cost"] is None


def test_projection_rejects_unknown_teacher_boundary_field() -> None:
    projection = pa.build_score_projection(MUSICXML)
    projection["lab_boundaries"] = [0.0]
    with pytest.raises(pa.PracticeAlignmentError, match="exactly"):
        pa.validate_score_projection(projection)


def test_direct_lab_path_is_rejected_before_any_read(tmp_path: Path) -> None:
    with pytest.raises(pa.PracticeAlignmentError, match="POST_FREEZE_AUDIT_ONLY"):
        pa.align_actor_files(tmp_path / "teacher.lab", tmp_path / "projection.json")


def test_lab_symlink_is_rejected_before_any_read(tmp_path: Path) -> None:
    lab = tmp_path / "teacher.lab"
    lab.write_text("must not be read", encoding="utf-8")
    wav_alias = tmp_path / "actor.wav"
    wav_alias.symlink_to(lab)
    with pytest.raises(pa.PracticeAlignmentError, match="POST_FREEZE_AUDIT_ONLY"):
        pa.align_actor_files(wav_alias, tmp_path / "projection.json")


def test_audit_manifest_is_rejected_before_wav_open(tmp_path: Path) -> None:
    audit = tmp_path / "practice_audit_annotation_manifest_v1.json"
    audit.write_text(json.dumps({}), encoding="utf-8")
    with pytest.raises(pa.PracticeAlignmentError, match="forbidden PRACTICE search input"):
        pa.align_actor_files(tmp_path / "does-not-exist.wav", audit)


def test_practice_module_does_not_import_education_lab_module() -> None:
    script = "\n".join(
        (
            "import json",
            "import sys",
            f"sys.path.insert(0, {str(_RUN_DIR)!r})",
            "import practice_alignment",
            "print(json.dumps('education_lesson_builder' in sys.modules))",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "false"
