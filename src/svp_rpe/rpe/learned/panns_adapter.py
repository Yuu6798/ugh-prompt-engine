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
    shape `(batch, 527)` (AudioSet posterior probabilities).

    The 527-class label list lives in `panns_inference.config.labels` and
    is re-exported on the package root as `panns_inference.labels` — note
    that this is an *attribute* (a list), NOT a submodule. We read the
    root attribute first and fall back to `panns_inference.config.labels`
    if the re-export ever moves.

    The Cnn14 backbone is hard-coded to expect 32 kHz mono input
    (sample_rate=32000 is baked into the model construction; there is
    no resampling inside `inference()`). This adapter resamples to
    32 kHz before calling `inference` and records the target rate in
    `inference_config.target_sample_rate` for provenance.

License note:
    panns_inference code is MIT. Pretrained checkpoint files have their own
    provenance and are downloaded lazily by upstream on first use; we do NOT
    assert a license for the weights here. `license_metadata` reflects this
    asymmetry rather than over-claiming.

Performance note:
    `AudioTagging(...)` loads ~80MB of weights on construction. This adapter
    builds a fresh instance per call, which is fine for the spike but will
    dominate latency on real workloads. A per-process cache or an explicit
    `audio_tagging` parameter can be added later when this is wired into a
    pipeline; the current shape is intentionally simple.
"""
from __future__ import annotations

import importlib
import importlib.metadata as _pkg_metadata
import sys
from typing import Any, Optional

import numpy as np

from svp_rpe.rpe.learned import LearnedModelIncompatible, LearnedModelUnavailable
from svp_rpe.rpe.models import (
    LearnedAudioAnnotations,
    LearnedAudioLabel,
    LearnedModelInfo,
)

__all__ = [
    "LearnedModelUnavailable",
    "LearnedModelIncompatible",
    "extract_panns_annotations",
]


_PANNS_PACKAGE = "panns_inference"
_PANNS_CONFIG_MODULE = "panns_inference.config"
_MODEL_TASK = "tagging"
_MODEL_PROVIDER = "qiuqiangkong/panns_inference"
_CODE_LICENSE = "MIT"
_LICENSE_NOTE = (
    "MIT code; pretrained weights require upstream verification"
)
_LABEL_CATEGORY = "audioset"

# Cnn14 (the default panns_inference checkpoint) is constructed with
# sample_rate=32000 baked in and does NOT resample internally. This is
# the rate we feed `AudioTagging.inference` regardless of the caller's
# input rate; provenance is recorded so callers can audit the resample.
_TARGET_SAMPLE_RATE: int = 32000

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


def _load_panns_labels(panns_root: Any) -> list[str]:
    """Load the AudioSet label list from the panns_inference root or config.

    panns_inference >= 0.1 re-exports `labels` (a list, not a submodule)
    on the package root via `from .config import labels`. We read the root
    attribute first to match the documented public surface, then fall back
    to importing `panns_inference.config` directly so a future re-export
    change doesn't immediately break the adapter.
    """
    labels = getattr(panns_root, "labels", None)
    if labels is None:
        try:
            config = importlib.import_module(_PANNS_CONFIG_MODULE)
        except ImportError as exc:
            raise LearnedModelIncompatible(
                "panns_inference.labels not at package root and "
                "panns_inference.config not importable; "
                "incompatible upstream version"
            ) from exc
        labels = getattr(config, "labels", None)
    if labels is None:
        raise LearnedModelIncompatible(
            "panns_inference labels not found at root or in config; "
            "incompatible upstream version"
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


def _validate_top_k(top_k: object) -> None:
    """Reject non-int / bool / non-positive `top_k` before loading the model.

    `bool` is a subclass of `int` in Python, so a naive `isinstance(top_k, int)`
    check accepts `top_k=True` as 1. We reject it explicitly because passing
    a bool is almost certainly a caller bug, not a deliberate request for
    top-1.
    """
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ValueError(f"top_k must be a positive integer, got {top_k!r}")


def extract_panns_annotations(
    audio: np.ndarray,
    sample_rate: int,
    *,
    top_k: int = 10,
    model_name: str = "Cnn14",
    device: str = "cpu",
) -> LearnedAudioAnnotations:
    """Run panns_inference AudioTagging on `audio`, return top-k tags.

    Audio is downmixed to mono and resampled to 32 kHz (the rate the Cnn14
    backbone was trained on) before reaching `AudioTagging.inference`. The
    original `sample_rate` is recorded in `inference_config.sample_rate`
    and the rate actually fed to the model is recorded in
    `inference_config.target_sample_rate`.

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
        1D mono or 2D stereo array. The 2D heuristic assumes
        `samples >> channels` to pick the channel axis; this is true for
        every realistic audio buffer (`channels` is 1 or 2, `samples` is
        thousands). For pathological cases pass mono yourself.
    sample_rate
        Sampling rate of `audio`. Resampled to 32 kHz before inference;
        recorded in `inference_config.sample_rate` for provenance.
    top_k
        Number of highest-confidence labels to return. Must be a positive
        non-bool int.
    model_name
        Identifier recorded in `source_model` and `inference_config` for
        provenance. Does not switch between checkpoints in this PR.
    device
        Forwarded to `AudioTagging(device=...)`. Defaults to "cpu" so CI
        and laptops work out-of-the-box; pass "cuda" for GPU.

    Raises
    ------
    LearnedModelUnavailable
        If `panns_inference` is not installed.
    LearnedModelIncompatible
        If `panns_inference` is installed but its labels list / output
        shape does not match the contract this adapter targets.
    ValueError
        If `top_k` is not a positive integer (bool is rejected too).
    """
    _validate_top_k(top_k)

    panns_root = _load_panns_root()
    labels_list = _load_panns_labels(panns_root)

    mono = _to_mono_1d(audio)
    if sample_rate != _TARGET_SAMPLE_RATE:
        # Lazy import to keep the no-op (sample_rate == 32000) path fast.
        # librosa is already a hard runtime dep, so this never fails on
        # default installs.
        import librosa

        mono = librosa.resample(
            mono, orig_sr=sample_rate, target_sr=_TARGET_SAMPLE_RATE
        )
    batch = np.asarray(mono, dtype=np.float32).reshape(1, -1)

    audio_tagging = panns_root.AudioTagging(checkpoint_path=None, device=device)
    clipwise_output, _embedding = audio_tagging.inference(batch)

    posterior = np.asarray(clipwise_output, dtype=np.float64).reshape(-1)
    if posterior.shape[0] != len(labels_list):
        # Adapter targets the 527-label AudioSet contract. A mismatch is an
        # API shift, not a missing install.
        raise LearnedModelIncompatible(
            "panns_inference label count mismatch: "
            f"clipwise={posterior.shape[0]}, labels={len(labels_list)}"
        )

    selected = _select_top_k(posterior, labels_list, top_k=top_k)
    return _build_annotations(
        selected,
        top_k=top_k,
        model_name=model_name,
        sample_rate=sample_rate,
        target_sample_rate=_TARGET_SAMPLE_RATE,
        device=device,
        version=_detect_panns_version(),
    )


def _to_mono_1d(audio: np.ndarray) -> np.ndarray:
    """Coerce audio to a 1D float32 mono signal.

    Heuristic: the channel axis is the smaller one. This works as long as
    `samples >> channels`, which holds for every realistic audio buffer
    (channels is 1 or 2, samples is in the thousands at minimum). It is
    deliberately ambiguous on tiny shapes like (2, 2) or (2, 1) — those
    are not legitimate audio inputs.
    """
    array = np.asarray(audio, dtype=np.float32)
    if array.ndim == 1:
        return array
    if array.ndim == 2:
        if array.shape[0] <= array.shape[1]:
            # (channels, samples) with channels <= samples — typical librosa.
            return array.mean(axis=0)
        # (samples, channels) — typical soundfile.
        return array.mean(axis=1)
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
    target_sample_rate: int,
    device: str,
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
            "target_sample_rate": target_sample_rate,
            "device": device,
            "source": _PANNS_PACKAGE,
        },
        license_metadata={
            _PANNS_PACKAGE: _LICENSE_NOTE,
        },
    )
