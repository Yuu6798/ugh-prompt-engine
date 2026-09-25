from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SINGER_DIR = Path(__file__).resolve().parents[1]
if str(_SINGER_DIR) not in sys.path:
    sys.path.insert(0, str(_SINGER_DIR))

import performance as perf  # noqa: E402
import phoneme_jp as pj  # noqa: E402
import score as sc  # noqa: E402


def test_phrase_final_release_closes_envelope_without_fading_legato_boundary() -> None:
    morae = pj.kana_to_morae("さく")
    notes = [
        sc.ScoreNote(60.0, 1.0, morae[0], phrase_index=0, is_phrase_final=False),
        sc.ScoreNote(62.0, 1.0, morae[1], phrase_index=0, is_phrase_final=True),
    ]
    segments, total_samples = perf.build_timeline(notes, sr=1000, tempo_bpm=60.0)

    envelope = perf.build_amplitude_envelope(segments, total_samples, sr=1000)

    assert envelope[segments[0].end_sample - 1] > 0.5
    assert envelope[-1] == pytest.approx(0.0)
    assert envelope[-16] > 0.0
