"""Render both WI3 decisive-synth styles (faithful_take, first_take) of the
midnight_signal CompositionScore to wav files, from a checkout-stable
relative path.

Relationship to the wi0b_synth committed recipe: this is the same recipe as
`examples/arrangement/midnight_signal/observed/wi0b_synth/render_faithful.py`
(same Python API — no CLI subcommand exists for this; both scripts call
`svp_rpe.perform.{FAITHFUL_TAKE,perform,wav_bytes}`, the API
`svp_rpe.roundtrip.harness.run_roundtrip` uses internally for R0), extended
to also render the FIRST_TAKE style in the same invocation. Where
`render_faithful.py` writes a single wav to an explicit output path,
this script writes both styles into an output *directory*, matching the
provenance recipe recorded in `wi3_rank_synth_provenance.yaml` (the canonical
relative output location is
`examples/arrangement/midnight_signal/observed/wi3_human_calibration/synth/`,
non-committed — sha256-pinned in that provenance file instead).

Usage (from the repository root, per the provenance recipe):

    python examples/arrangement/midnight_signal/observed/wi3_human_calibration/render_synth_takes.py \\
      examples/arrangement/midnight_signal/composition_score.yaml \\
      examples/arrangement/midnight_signal/observed/wi3_human_calibration/synth
"""
from __future__ import annotations

import sys
from pathlib import Path

from svp_rpe.compose.loader import load_composition_score
from svp_rpe.perform import FAITHFUL_TAKE, FIRST_TAKE, perform, wav_bytes


def main() -> None:
    score_path = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    # `synth/` is intentionally non-committed (git does not track empty
    # directories), so a fresh checkout has no such directory yet -- create it
    # here rather than requiring the caller to `mkdir` first (that omission
    # was a real FileNotFoundError on a fresh checkout, caught by PR #202
    # Codex P2).
    out_dir.mkdir(parents=True, exist_ok=True)
    score = load_composition_score(score_path)

    for style, fname in ((FAITHFUL_TAKE, "faithful_take.wav"), (FIRST_TAKE, "first_take.wav")):
        samples = perform(score, style)
        out_path = out_dir / fname
        out_path.write_bytes(wav_bytes(samples))
        print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
