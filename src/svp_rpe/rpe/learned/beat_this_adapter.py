"""rpe/learned/beat_this_adapter.py — beat_this learned beat/downbeat adapter.

Optional via the `beat` extra. Output is isolated in
`LearnedAudioAnnotations.time_events`; never written into
`PhysicalRPE.downbeat_times`. `dbn=False` is hard-fixed so this adapter
does NOT chain a madmom DBN post-processor (which would re-introduce the
madmom dependency the policy doc rejects).

See docs/learned_models_policy.md for the full policy.
"""
from __future__ import annotations

import importlib
from typing import Any, Iterable

import numpy as np

from svp_rpe.rpe.models import (
    LearnedAudioAnnotations,
    LearnedModelInfo,
    LearnedTimeEvent,
)

__all__ = [
    "LearnedModelUnavailable",
    "extract_beat_this_annotations",
]


class LearnedModelUnavailable(RuntimeError):
    """Raised when a learned-model optional dependency is not installed.

    Callers can catch this to fall back to the deterministic backend or
    surface a structured "extra not installed" error to the user.
    """


_BEAT_THIS_MODULE = "beat_this.inference"
_MODEL_NAME = "beat_this"
_MODEL_TASK = "beat_downbeat"
_MODEL_LICENSE = "MIT"

# DBN post-processing is hard-fixed off. Enabling it would re-introduce a
# madmom transitive dependency, which the policy rejects (Python 3.11+
# incompatibility, NC-licensed weights). This is not a tunable knob.
_HARD_DBN: bool = False

_INSTALL_HINT = (
    "beat_this is not installed. Install it via the optional `beat` extra:\n"
    '    pip install -e ".[beat]"'
)


def _load_beat_this_inference() -> Any:
    try:
        return importlib.import_module(_BEAT_THIS_MODULE)
    except ImportError as exc:
        raise LearnedModelUnavailable(_INSTALL_HINT) from exc


def extract_beat_this_annotations(
    audio: np.ndarray,
    sample_rate: int,
    *,
    checkpoint: str = "final0",
) -> LearnedAudioAnnotations:
    """Run beat_this on `audio` and return a learned annotations payload.

    The result is intended to be attached to `RPEBundle.learned_annotations`
    via `svp_rpe.rpe.learned.attach_learned_annotations`. It MUST NOT be
    merged into `PhysicalRPE` or `SemanticRPE`.

    `dbn=False` is hard-fixed; callers cannot override it.

    Raises
    ------
    LearnedModelUnavailable
        If `beat_this` is not installed.
    """
    module = _load_beat_this_inference()
    file2beats = module.File2Beats(checkpoint_path=checkpoint, dbn=_HARD_DBN)
    beats, downbeats = file2beats(audio, sample_rate)
    return _build_annotations(beats, downbeats, checkpoint=checkpoint)


def _build_annotations(
    beats: Iterable[float],
    downbeats: Iterable[float],
    *,
    checkpoint: str,
) -> LearnedAudioAnnotations:
    time_events: list[LearnedTimeEvent] = []
    for t in beats:
        time_events.append(
            LearnedTimeEvent(
                time_sec=float(t),
                event_type="beat",
                source_model=_MODEL_NAME,
            )
        )
    for t in downbeats:
        time_events.append(
            LearnedTimeEvent(
                time_sec=float(t),
                event_type="downbeat",
                source_model=_MODEL_NAME,
            )
        )
    return LearnedAudioAnnotations(
        enabled_models=[
            LearnedModelInfo(
                name=_MODEL_NAME,
                task=_MODEL_TASK,
                license=_MODEL_LICENSE,
            )
        ],
        time_events=time_events,
        inference_config={
            "dbn": _HARD_DBN,
            "source": _MODEL_NAME,
            "checkpoint": checkpoint,
        },
        license_metadata={
            _MODEL_NAME: _MODEL_LICENSE,
        },
    )
