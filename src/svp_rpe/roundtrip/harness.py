"""Deterministic score -> audio -> draft score roundtrip harness."""
from __future__ import annotations

import tempfile
from pathlib import Path

from svp_rpe.compose.models import CompositionScore
from svp_rpe.perform import FAITHFUL_TAKE, perform, wav_bytes
from svp_rpe.roundtrip.diagnose import diagnose_roundtrip
from svp_rpe.roundtrip.models import RoundtripReport
from svp_rpe.rpe.extractor import extract_rpe_from_file
from svp_rpe.transcribe import draft_score


def run_roundtrip(score: CompositionScore) -> RoundtripReport:
    """Run the deterministic R0 roundtrip for a CompositionScore."""

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = Path(tmp) / "roundtrip.wav"
        wav_path.write_bytes(wav_bytes(perform(score, FAITHFUL_TAKE)))
        bundle = extract_rpe_from_file(str(wav_path))
        transcribed = draft_score(bundle)
    return diagnose_roundtrip(score, transcribed)
