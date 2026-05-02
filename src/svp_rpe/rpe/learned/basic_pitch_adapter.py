"""rpe/learned/basic_pitch_adapter.py — basic-pitch learned note adapter.

Optional via the `pitch` extra. Output is isolated in
`LearnedAudioAnnotations.note_events`; never written into
`PhysicalRPE.melody_contour` or `PhysicalRPE.chord_events`. See
`docs/learned_models_policy.md` for the full policy.

Upstream API note (basic-pitch >= 0.2):
    `basic_pitch.inference.predict(audio_path, ...)` returns a
    `(model_output, midi_data, note_events)` triple. `note_events` is a
    list of tuples shaped roughly like
    `(start_sec, end_sec, pitch_midi, amplitude, pitch_bends)` — only the
    first four fields are consumed here. `amplitude` is upstream's
    [0, 1] note-confidence proxy; we surface it as
    `LearnedNoteEvent.confidence` rather than inventing a unified
    semantic across pitch backends.

    `predict` accepts a path. Real basic-pitch internally loads + resamples
    audio; we therefore take a path argument rather than an in-memory
    buffer. An in-memory variant can be added later if a use case appears.

License note:
    basic-pitch code is Apache-2.0. The model artifact is downloaded lazily
    by upstream on first use and has its own provenance — we do NOT assert
    a license for the artifact. `license_metadata` reflects this asymmetry
    rather than over-claiming.

Performance note:
    `predict()` instantiates the TF / CoreML model on every call. This is
    fine for a spike but will dominate latency on real workloads; a
    per-process cache or an explicit `model` parameter belongs to the
    pipeline-wiring PR.
"""
from __future__ import annotations

import importlib
import importlib.metadata as _pkg_metadata
import sys
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from svp_rpe.rpe.learned import LearnedModelIncompatible, LearnedModelUnavailable
from svp_rpe.rpe.models import (
    LearnedAudioAnnotations,
    LearnedModelInfo,
    LearnedNoteEvent,
)

__all__ = [
    "LearnedModelUnavailable",
    "LearnedModelIncompatible",
    "extract_basic_pitch_annotations",
]


_BASIC_PITCH_PACKAGE = "basic_pitch"
_BASIC_PITCH_INFERENCE_MODULE = "basic_pitch.inference"
_MODEL_NAME = "basic_pitch"
_MODEL_TASK = "pitch"
_MODEL_PROVIDER = "spotify/basic-pitch"
_CODE_LICENSE = "Apache-2.0"
_LICENSE_NOTE = (
    "Apache-2.0 code; model artifact provenance requires upstream verification"
)

_INSTALL_HINT = (
    "basic_pitch is not installed. Install it via the optional `pitch` extra:\n"
    '    pip install -e ".[pitch]"'
)


def _load_basic_pitch_inference() -> Any:
    try:
        return importlib.import_module(_BASIC_PITCH_INFERENCE_MODULE)
    except ImportError as exc:
        raise LearnedModelUnavailable(_INSTALL_HINT) from exc


def _detect_basic_pitch_version() -> Optional[str]:
    """Best-effort detect installed basic-pitch version.

    Same fallback chain as the other adapters: imported package
    `__version__` first, then importlib.metadata, then None.
    """
    root = sys.modules.get(_BASIC_PITCH_PACKAGE)
    if root is not None:
        candidate = getattr(root, "__version__", None)
        if isinstance(candidate, str) and candidate:
            return candidate
    try:
        # PyPI distribution name is "basic-pitch" (hyphen). importlib.metadata
        # accepts either form — it normalises hyphens / underscores per PEP 503.
        return _pkg_metadata.version("basic-pitch")
    except _pkg_metadata.PackageNotFoundError:
        return None


def extract_basic_pitch_annotations(
    audio_path: Union[str, Path],
    *,
    model_name: str = "basic_pitch",
) -> LearnedAudioAnnotations:
    """Run basic-pitch on `audio_path` and return note-event annotations.

    Output is isolated in `LearnedAudioAnnotations.note_events` and intended
    to be attached via `svp_rpe.rpe.learned.attach_learned_annotations`. It
    MUST NOT be merged into `PhysicalRPE.melody_contour`,
    `PhysicalRPE.chord_events`, `SemanticRPE.por_surface`,
    `SVPForGeneration.style_tags`, or scoring.

    Parameters
    ----------
    audio_path
        Path to the audio file. basic-pitch handles its own loading and
        resampling internally — this adapter does not pre-process.
    model_name
        Identifier recorded in `source_model` and `inference_config` for
        provenance. Does not switch between checkpoints in this PR.

    Raises
    ------
    LearnedModelUnavailable
        If `basic_pitch` is not installed.
    LearnedModelIncompatible
        If the upstream `predict` call returns a shape we cannot map to
        `LearnedNoteEvent`.
    """
    inference_module = _load_basic_pitch_inference()
    predict_fn = getattr(inference_module, "predict", None)
    if predict_fn is None:
        raise LearnedModelIncompatible(
            "basic_pitch.inference.predict not found; "
            "incompatible basic_pitch version"
        )

    result = predict_fn(str(audio_path))
    note_events_raw = _extract_note_events_field(result)
    note_events = _build_note_events(note_events_raw, source_model=_MODEL_NAME)

    return _build_annotations(
        note_events,
        model_name=model_name,
        version=_detect_basic_pitch_version(),
    )


def _extract_note_events_field(result: Any) -> Iterable[Any]:
    """Pull the note-events list out of the `predict` return value.

    basic-pitch >= 0.2 returns `(model_output, midi_data, note_events)`. We
    accept the third element of any tuple/list-shaped result. A more exotic
    return shape (dict, custom object) is treated as incompatible rather
    than guessed at.
    """
    if isinstance(result, tuple) or isinstance(result, list):
        if len(result) < 3:
            raise LearnedModelIncompatible(
                f"basic_pitch.predict returned a {len(result)}-tuple; "
                "expected (model_output, midi_data, note_events)"
            )
        return result[2]
    raise LearnedModelIncompatible(
        f"basic_pitch.predict returned unexpected type: {type(result).__name__}"
    )


def _build_note_events(
    note_events_raw: Iterable[Any],
    *,
    source_model: str,
) -> list[LearnedNoteEvent]:
    """Map basic-pitch note tuples to LearnedNoteEvent.

    basic-pitch tuples are shaped `(start, end, midi, amplitude, ...)`. We
    consume the first four positional fields. Out-of-range values
    (negative time, midi outside [0, 127], confidence outside [0, 1],
    end < start) surface as Pydantic ValidationError — never silently
    clamped.
    """
    events: list[LearnedNoteEvent] = []
    for raw in note_events_raw:
        if not isinstance(raw, (tuple, list)) or len(raw) < 4:
            raise LearnedModelIncompatible(
                f"basic_pitch note event has unexpected shape: {raw!r}"
            )
        start_sec, end_sec, pitch_midi, amplitude = raw[0], raw[1], raw[2], raw[3]
        events.append(
            LearnedNoteEvent(
                start_sec=float(start_sec),
                end_sec=float(end_sec),
                pitch_midi=int(pitch_midi),
                confidence=float(amplitude),
                source_model=source_model,
            )
        )
    return events


def _build_annotations(
    note_events: list[LearnedNoteEvent],
    *,
    model_name: str,
    version: Optional[str],
) -> LearnedAudioAnnotations:
    return LearnedAudioAnnotations(
        enabled_models=[
            LearnedModelInfo(
                name=_MODEL_NAME,
                version=version,
                provider=_MODEL_PROVIDER,
                task=_MODEL_TASK,
                license=_CODE_LICENSE,
                weights_license=None,  # See license_metadata note instead.
            )
        ],
        note_events=note_events,
        inference_config={
            "model_name": model_name,
            "source": _MODEL_NAME,
            "entry_point": "predict",
        },
        license_metadata={
            _MODEL_NAME: _LICENSE_NOTE,
        },
    )
