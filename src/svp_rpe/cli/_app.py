"""Shared Typer app, console, and CLI-wide option definitions for svp_rpe.cli."""
from __future__ import annotations

import sys
from collections.abc import Iterable
from typing import Annotated

import click
import typer
from rich.console import Console

from svp_rpe.eval.scorer_rpe import BASELINE_CONFIGS


def _configure_windows_utf8_output(platform: str, streams: Iterable[object]) -> None:
    """Keep Rich/Typer help renderable on Windows legacy code-page streams."""
    if platform != "win32":
        return
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            # Embedded hosts and closed streams may reject reconfiguration.
            continue


_configure_windows_utf8_output(sys.platform, (sys.stdout, sys.stderr))

app = typer.Typer(
    name="svprpe",
    help="SVP-RPE: Audio analysis → RPE extraction → SVP generation → Evaluation",
)
console = Console()
BASELINE_PROFILE_CHOICE = click.Choice(sorted(BASELINE_CONFIGS))
BASELINE_PROFILE_HELP = "RPE baseline profile used as scoring reference."
DEFAULT_SEPARATION_MODEL = "htdemucs_ft"
DEFAULT_SEPARATION_DEVICE = "cpu"
SEPARATE_HELP = "Enable Demucs stem separation (opt-in). Requires demucs installed."
SEPARATION_MODEL_HELP = "Demucs model name used when --separate is set."
SEPARATION_DEVICE_HELP = "Demucs inference device used when --separate is set."

SeparateOption = Annotated[bool, typer.Option("--separate", help=SEPARATE_HELP)]
SeparationModelOption = Annotated[
    str, typer.Option("--separation-model", help=SEPARATION_MODEL_HELP)
]
SeparationDeviceOption = Annotated[
    str, typer.Option("--separation-device", help=SEPARATION_DEVICE_HELP)
]
