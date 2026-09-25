"""Shared `--clap-checkpoint` / `--clap-amodel` typer.Option definitions.

`cli/extract_cmd.py` (`extract`) and `cli/transcribe_cmd.py` (`transcribe`) each
independently declared these two options with identical help text. Centralized
here following the `SeparateOption`/`SeparationModelOption`/`SeparationDeviceOption`
pattern in `cli/_app.py`.
"""
from __future__ import annotations

from typing import Annotated, Optional

import typer

CLAP_CHECKPOINT_HELP = (
    "Local path to a CLAP checkpoint to pin (e.g. the "
    "fixture-provenance music_audioset_epoch_15_esc_90.14.pt). "
    "Default None keeps upstream's default checkpoint download."
)
CLAP_AMODEL_HELP = (
    "Audio-tower architecture matching the checkpoint family (the "
    "music_* checkpoints require HTSAT-base). Default None = "
    "upstream default HTSAT-tiny family."
)

ClapCheckpointOption = Annotated[
    Optional[str], typer.Option("--clap-checkpoint", help=CLAP_CHECKPOINT_HELP)
]
ClapAmodelOption = Annotated[
    Optional[str], typer.Option("--clap-amodel", help=CLAP_AMODEL_HELP)
]
