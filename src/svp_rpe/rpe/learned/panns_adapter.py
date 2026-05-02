"""rpe/learned/panns_adapter.py — panns_inference learned-tag adapter.

Optional via the `learned-tags` extra. Output is isolated in
`LearnedAudioAnnotations.labels` (NEVER `SemanticRPE.por_surface`,
`SVPForGeneration.style_tags`, or scoring). See
`docs/learned_models_policy.md` for the full policy.

Upstream API note (panns_inference >= 0.1):
    `panns_inference.AudioTagging(checkpoint_path=None, device='cpu'|'cuda')`
    is the AudioSet-tagging entry point. Calling
    `at.inference(audio_batch)` with a `(batch, samples)` float32 array
    returns `(clipwise_output, embedding)` where `clipwise_output` has
    shape `(batch, 527)` (AudioSet posterior probabilities). Class names
    live in `panns_inference.labels.labels` (a list of 527 strings).

License note:
    panns_inference code is MIT. Pretrained checkpoint files have their own
    provenance and are downloaded lazily by upstream on first use; we do NOT
    assert a license for the weights here. `license_metadata` reflects this
    asymmetry rather than over-claiming.
"""
from __future__ import annotations

import importlib
import importlib.metadata as _pkg_metadata
import sys
from typing import Any, Optional

import numpy as np

from svp_rpe.rpe.learned import LearnedModelUnavailable
from svp_rpe.rpe.models import (
    LearnedAudioAnnotations,
    LearnedAudioLabel,
    LearnedModelInfo,
)

__all__ = [
    "LearnedModelUnavailable",
    "extract_panns_annotations",
]


_PANNS_PACKAGE = "panns_inference"
_PANNS_LABELS_MODULE = "panns_inference.labels"
_MODEL_TASK = "tagging"
_MODEL_PROVIDER = "qiuqiangkong/panns_inference"
_CODE_LICENSE = "MIT"
_LICENSE_NOTE = (
    "MIT code; pretrained weights require upstream verification"
)
_LABEL_CATEGORY = "audioset"

_INSTALL_HINT = (
    "panns_inference is not installed. Install it via the optional "
    "`learned-tags` extra:\n"
    '    pip install -e ".[learned-tags]"'
)


def _load_panns_root() -> Any:
    try:
        return importlib.import_module(_PANNS_PACKAGE)
    except ImportError as exc:
        raise LearnedModelUnavailable(_INSTALL_HINT) from exc


def _load_panns_labels() -> list[str]:
    try:
        labels_module = importlib.import_module(_PANNS_LABELS_MODULE)
    except ImportError as exc:
        raise LearnedModelUnavailable(_INSTALL_HINT) from exc
    labels = getattr(labels_module, "labels", None)
    if labels is None:
        raise LearnedModelUnavailable(
            "panns_inference.labels.labels not found; "
            "incompatible panns_inference version"
        )
    return list(labels)


def _detect_panns_version() -> Optional[str]:
    """Best-effort detect installed panns_inference version.

    Same fallback chain as the beat_this adapter: imported package
    `__version__` first, then importlib.metadata, then None.
    """
    root = sys.modules.get(_PANNS_PACKAGE)
    if root is not None:
        candidate = getattr(root, "__version__", None)
        if isinstance(candidate, str) and candidate:
            return candidate
    try:
        return _pkg_metadata.version(_PANNS_PACKAGE)
    except _pkg_metadata.PackageNotFoundError:
        return None


def extract_panns_annotations(
    audio: np.ndarray,
    sample_rate: int,
    *,
    top_k: int = 10,
    model_name: str = "Cnn14",
) -> LearnedAudioAnnotations:
    """Run panns_inference AudioTagging on `audio`, return top-k tags.

    Selection is deterministic: confidence descending, then label string
    ascending as the tie-break. Out-of-range confidence (NaN, < 0, > 1)
    raises a Pydantic ValidationError on the underlying
    `LearnedAudioLabel.confidence` field — we never silently clamp.

    Output is isolated in `LearnedAudioAnnotations.labels` and intended to
    be attached via `svp_rpe.rpe.learned.attach_learned_annotations`. It
    MUST NOT be merged into `SemanticRPE.por_surface`,
    `SVPForGeneration.style_tags`, or scoring.

    Parameters
    ----------
    audio
        1D mono or 2D (channels, samples) / (samples, channels) array.
        Stereo input is downmixed to mono before inference.
    sample_rate
        Sampling rate of `audio`. Recorded as inference_config metadata
        only — panns_inference handles upstream resampling internally.
    top_k
        Number of highest-confidence labels to return. Must be positive.
    model_name
        Identifier recorded in `source_model` and `inference_config` for
        provenance. Does not switch between checkpoints in this PR.

    Raises
    ------
    LearnedModelUnavailable
        If `panns_inference` is not installed or the labels module is
        missing / shaped differently from the expected upstream version.
    ValueError
        If `top_k` is not a positive integer.
    """
    if not isinstance(top_k, int) or top_k <= 0:
        raise ValueError(f"top_k must be a positive integer, got {top_k!r}")

    panns_root = _load_panns_root()
    labels_list = _load_panns_labels()

    audio_tagging = panns_root.AudioTagging(checkpoint_path=None, device="cpu")
    batch = _ensure_batch_shape(audio)
    clipwise_output, _embedding = audio_tagging.inference(batch)

    posterior = np.asarray(clipwise_output, dtype=np.float64).reshape(-1)
    if posterior.shape[0] != len(labels_list):
        raise LearnedModelUnavailable(
            "panns_inference label count mismatch: "
            f"clipwise={posterior.shape[0]}, labels={len(labels_list)}"
        )

    selected = _select_top_k(posterior, labels_list, top_k=top_k)
    return _build_annotations(
        selected,
        top_k=top_k,
        model_name=model_name,
        sample_rate=sample_rate,
        version=_detect_panns_version(),
    )


def _ensure_batch_shape(audio: np.ndarray) -> np.ndarray:
    """panns_inference expects (batch, samples). Coerce mono / stereo."""
    array = np.asarray(audio, dtype=np.float32)
    if array.ndim == 1:
        return array.reshape(1, -1)
    if array.ndim == 2:
        # Heuristic: the channel axis is the smaller one. Common shapes are
        # either (channels, samples) (e.g. librosa.load multi-channel) or
        # (samples, channels) (soundfile). Either way we reduce to mono
        # before adding the batch axis.
        if array.shape[0] <= array.shape[1]:
            mono = array.mean(axis=0)
        else:
            mono = array.mean(axis=1)
        return mono.reshape(1, -1)
    raise ValueError(f"audio must be 1D or 2D, got shape {array.shape}")


def _select_top_k(
    posterior: np.ndarray,
    labels: list[str],
    *,
    top_k: int,
) -> list[tuple[str, float]]:
    """Deterministic top-k: confidence DESC, label ASC as tie-break.

    Sorted in pure Python on a (label, confidence) list because numpy's
    argsort is not guaranteed stable across implementations and would
    require a secondary key anyway. The list is short (527 elements at
    upstream's default), so this is cheap and trivially auditable.
    """
    pairs = list(zip(labels, posterior.tolist(), strict=True))
    pairs.sort(key=lambda pair: (-float(pair[1]), pair[0]))
    return pairs[:top_k]


def _build_annotations(
    selected: list[tuple[str, float]],
    *,
    top_k: int,
    model_name: str,
    sample_rate: int,
    version: Optional[str],
) -> LearnedAudioAnnotations:
    source_model = f"{_PANNS_PACKAGE}:{model_name}"
    labels: list[LearnedAudioLabel] = [
        LearnedAudioLabel(
            label=label_text,
            category=_LABEL_CATEGORY,
            confidence=float(confidence),
            source_model=source_model,
        )
        for label_text, confidence in selected
    ]
    return LearnedAudioAnnotations(
        enabled_models=[
            LearnedModelInfo(
                name=_PANNS_PACKAGE,
                version=version,
                provider=_MODEL_PROVIDER,
                task=_MODEL_TASK,
                license=_CODE_LICENSE,
                weights_license=None,  # See license_metadata note instead.
            )
        ],
        labels=labels,
        inference_config={
            "top_k": top_k,
            "model_name": model_name,
            "sample_rate": sample_rate,
            "source": _PANNS_PACKAGE,
        },
        license_metadata={
            _PANNS_PACKAGE: _LICENSE_NOTE,
        },
    )
