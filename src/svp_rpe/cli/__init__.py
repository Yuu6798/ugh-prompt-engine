"""cli — typer CLI package for svp-rpe.

Commands:
  svprpe extract <audio>        → RPE JSON
  svprpe generate <rpe>         → SVP YAML/TXT
  svprpe compose <score>        → Composition Score prompt
  svprpe evaluate --audio <wav> → Evaluation JSON (self or with --svp)
  svprpe compare ...            → Reference vs candidate comparison
  svprpe ci-check ...           → Deterministic semantic CI fixture check
  svprpe run <audio>            → Full pipeline
  svprpe batch <dir>            → Batch processing
"""
from __future__ import annotations

from svp_rpe.cli._app import app, console

# Command modules are imported for their registration side effect
# (`@app.command()` on import) in the same order they were originally
# defined in cli.py, so `svprpe --help` lists subcommands identically.
from svp_rpe.cli import extract_cmd as extract_cmd
from svp_rpe.cli import compose_cmd as compose_cmd
from svp_rpe.cli import arrange_cmd as arrange_cmd
from svp_rpe.cli import package_cmd as package_cmd
from svp_rpe.cli import observe_cmd as observe_cmd
from svp_rpe.cli import verify_cmd as verify_cmd
from svp_rpe.cli import transcribe_cmd as transcribe_cmd
from svp_rpe.cli import eval_cmd as eval_cmd
from svp_rpe.cli import roundtrip_cmd as roundtrip_cmd
from svp_rpe.cli import corpus_cmd as corpus_cmd
from svp_rpe.cli import recast_cmd as recast_cmd
from svp_rpe.cli import validate_cmd as validate_cmd

__all__ = ["app", "console"]
