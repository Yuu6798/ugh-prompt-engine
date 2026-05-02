"""rpe/learned/beat_this_adapter.py — beat_this learned beat/downbeat adapter.

Optional via the `beat` extra. Output is isolated in
`LearnedAudioAnnotations.time_events`; never written into
`PhysicalRPE.downbeat_times`. `dbn=False` is hard-fixed so this adapter
does NOT chain a madmom DBN post-processor (which would re-introduce the
madmom dependency the policy doc rejects).

Upstream API note (beat_this >= 1.1):
    `beat_this.inference` exposes two callables. `File2Beats` takes a
    file path; `Audio2Beats` takes an in-memory signal + sample rate.
    This adapter receives a `np.ndarray`, so it uses `Audio2Beats`.

See docs/learned_models_policy.md for the full policy.
"""
from __future__ import annotations

import importlib
import importlib.metadata as _pkg_metadata
import sys
from typing import Any, Iterable, Optional

import numpy as np

from svp_rpe.rpe.learned import LearnedModelUnavailable
from svp_rpe.rpe.models import (
    LearnedAudioAnnotations,
    LearnedModelInfo,
    LearnedTimeEvent,
)

__all__ = [
    "LearnedModelUnavailable",
    "extract_beat_this_annotations",
]


_BEAT_THIS_PACKAGE = "beat_this"
_BEAT_THIS_MODULE = "beat_this.inference"
_MODEL_NAME = "beat_this"
_MODEL_TASK = "beat_downbeat"
_MODEL_LICENSE = "MIT"
_MODEL_PROVIDER = "CPJKU/beat_this"

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


def _detect_beat_this_version() -> Optional[str]:
    """Best-effort detect the installed beat_this version.

    Checks the imported package's `__version__` attribute first (cheap and
    matches whatever code is actually running), then falls back to
    `importlib.metadata` which reads dist-info from the install. Returns
    None when neither path resolves — provenance is still emitted, just
    without a version string.
    """
    root = sys.modules.get(_BEAT_THIS_PACKAGE)
    if root is not None:
        candidate = getattr(root, "__version__", None)
        if isinstance(candidate, str) and candidate:
            return candidate
    try:
        return _pkg_metadata.version(_BEAT_THIS_PACKAGE)
    except _pkg_metadata.PackageNotFoundError:
        return None


def extract_beat_this_annotations(
    audio: np.ndarray,
    sample_rate: int,
    *,
    checkpoint: str = "final0",
) -> LearnedAudioAnnotations:
    """Run beat_this on an in-memory `audio` signal and return annotations.

    Uses `beat_this.inference.Audio2Beats(checkpoint_path=..., dbn=False)`,
    which is the in-memory entry point in beat_this >= 1.1. The file-path
    counterpart `File2Beats` is intentionally NOT used here — we already
    have the decoded signal, and `File2Beats` would force a re-decode
    detour through disk.

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
    audio2beats = module.Audio2Beats(checkpoint_path=checkpoint, dbn=_HARD_DBN)
    beats, downbeats = audio2beats(audio, sample_rate)
    return _build_annotations(
        beats,
        downbeats,
        checkpoint=checkpoint,
        version=_detect_beat_this_version(),
    )


def _build_annotations(
    beats: Iterable[float],
    downbeats: Iterable[float],
    *,
    checkpoint: str,
    version: Optional[str],
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
                version=version,
                provider=_MODEL_PROVIDER,
                task=_MODEL_TASK,
                license=_MODEL_LICENSE,
            )
        ],
        time_events=time_events,
        inference_config={
            "dbn": _HARD_DBN,
            "source": _MODEL_NAME,
            "checkpoint": checkpoint,
            "entry_point": "Audio2Beats",
        },
        license_metadata={
            _MODEL_NAME: _MODEL_LICENSE,
        },
    )
