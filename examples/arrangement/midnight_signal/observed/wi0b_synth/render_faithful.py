"""Render the faithful (transpose=0) take of a CompositionScore to a wav file.

Verbatim invocation recorded in wi0b_run/commands.md. Not a CLI subcommand —
there is no `svprpe perform` command; this calls the same
svp_rpe.perform.FAITHFUL_TAKE / perform() / wav_bytes() API that
svp_rpe.roundtrip.harness.run_roundtrip uses internally (R0 harness).
"""
from __future__ import annotations

import sys
from pathlib import Path

from svp_rpe.compose.loader import load_composition_score
from svp_rpe.perform import FAITHFUL_TAKE, perform, wav_bytes


def main() -> None:
    score_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    score = load_composition_score(score_path)
    samples = perform(score, FAITHFUL_TAKE)
    out_path.write_bytes(wav_bytes(samples))
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
