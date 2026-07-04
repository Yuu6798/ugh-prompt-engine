"""rpe/learned/semantic_axes.py — CLAP extraction-stage semantic-axis sensor.

This is the extraction-stage counterpart to the post-hoc A/B comparator in
`clap_adapter.py` / `similarity.py`. Those modules only ever ran CLAP
against *generated outputs* via fixture scripts (`scripts/collect_clap_fixture.py`);
this module reads the SOURCE audio itself at `svprpe extract` time, against a
fixed battery of named semantic axes (`config/semantic_probe_axes.yaml`),
producing per-axis A/B `contrast_fit` readings.

Isolation policy (see `docs/learned_models_policy.md` Section 2): output is
confined to `LearnedAudioAnnotations.semantic_axes` and MUST NOT be folded
into `SemanticRPE.por_surface`, `PhysicalRPE.*`, or
`SVPForGeneration.style_tags`. `contrast_fit` is read as a learned grip — a
signed A/B contrast, never a [0, 1] confidence or a verdict.

Gated behind the `semantic-embed` extra (torch + laion-clap), exactly like
`clap_adapter.py`. `LearnedModelUnavailable` / `LearnedModelIncompatible`
propagate unchanged from the underlying adapter calls; both are re-exported
here for caller convenience.

Determinism: `embed_audio_file` / `embed_texts` are already deterministic
by construction (see `clap_adapter.py`'s module docstring). Everything in
this module downstream of those calls (`contrast_fit`, axis-battery
iteration) is pure numpy arithmetic over already-computed vectors — no RNG
is introduced here. `contrast_fit` values are rounded to 6 digits, matching
`scripts/collect_clap_fixture.py`'s convention.
"""
from __future__ import annotations

from typing import Any, Optional

from svp_rpe.rpe.learned import LearnedModelIncompatible, LearnedModelUnavailable
from svp_rpe.rpe.learned.clap_adapter import embed_audio_file, embed_texts, load_clap_model
from svp_rpe.rpe.learned.similarity import contrast_fit
from svp_rpe.rpe.models import LearnedAudioAnnotations, LearnedSemanticAxis
from svp_rpe.utils.config_loader import load_config

__all__ = [
    "LearnedModelUnavailable",
    "LearnedModelIncompatible",
    "load_semantic_axes",
    "extract_clap_semantic_axes",
]

_SOURCE_MODEL = "laion_clap:CLAP_Module"


def _validated_axes(axes: list[dict]) -> list[dict]:
    for axis in axes:
        name = axis.get("name")
        if not name:
            raise ValueError(f"semantic_probe_axes entry missing non-empty 'name': {axis!r}")
        for side in ("positive", "negative"):
            probes = axis.get(side)
            # A bare string is truthy but `list("a bright song")` char-splits it
            # into meaningless single-character CLAP prompts — require an
            # explicit non-empty list/tuple of non-empty strings so a malformed
            # battery fails loudly instead of silently corrupting readings.
            if isinstance(probes, str) or not isinstance(probes, (list, tuple)):
                raise ValueError(
                    f"semantic_probe_axes entry {name!r}: {side!r} must be a list of "
                    f"strings, got {type(probes).__name__}"
                )
            if not probes:
                raise ValueError(
                    f"semantic_probe_axes entry {name!r} missing non-empty {side!r}"
                )
            if not all(isinstance(probe, str) and probe for probe in probes):
                raise ValueError(
                    f"semantic_probe_axes entry {name!r}: {side!r} must contain only "
                    "non-empty strings"
                )
    return axes


def load_semantic_axes() -> list[dict]:
    """Load the CLAP semantic-axis battery from `config/semantic_probe_axes.yaml`.

    Raises
    ------
    ValueError
        If any axis entry is missing a non-empty `name`, `positive`, or
        `negative`.
    """
    config = load_config("semantic_probe_axes")
    return _validated_axes(config.get("axes", []))


def extract_clap_semantic_axes(
    audio_path: str,
    *,
    axes: Optional[list[dict]] = None,
    model: Optional[Any] = None,
    checkpoint: Optional[str] = None,
    amodel: Optional[str] = None,
) -> LearnedAudioAnnotations:
    """Read `audio_path`'s semantic content with CLAP against an axis battery.

    Loads the CLAP model once (reusing `model` if supplied), embeds the
    audio via `clap_adapter.embed_audio_file`, then for each axis in
    `axes` (default: `load_semantic_axes()`) embeds the axis's positive
    and negative probes and computes `contrast_fit` against the audio
    embedding.

    Returns a copy of the `LearnedAudioAnnotations` produced by
    `embed_audio_file` with `semantic_axes` populated and
    `inference_config` augmented with `semantic_axes_config_version` and
    `n_semantic_axes`.

    Raises
    ------
    LearnedModelUnavailable
        If `laion_clap` is not installed.
    LearnedModelIncompatible
        If `laion_clap` is installed but its API does not match the
        expected contract.
    """
    clap_model = model if model is not None else load_clap_model(checkpoint, amodel)
    annotations = embed_audio_file(
        audio_path, model=clap_model, checkpoint=checkpoint, amodel=amodel
    )
    audio_vec = annotations.embedding.vector

    axes_config = load_config("semantic_probe_axes")
    # Validate both the default battery AND caller-supplied custom axes, so the
    # char-split footgun (scalar probe strings) can't reach embedding either way.
    axes = _validated_axes(axes if axes is not None else axes_config.get("axes", []))

    semantic_axes: list[LearnedSemanticAxis] = []
    for axis in axes:
        positive_probes = list(axis["positive"])
        negative_probes = list(axis["negative"])
        pos_emb = embed_texts(positive_probes, model=clap_model)
        neg_emb = embed_texts(negative_probes, model=clap_model)
        fit = contrast_fit(audio_vec, pos_emb, neg_emb)
        semantic_axes.append(
            LearnedSemanticAxis(
                axis=axis["name"],
                contrast_fit=round(fit, 6),
                positive_probes=positive_probes,
                negative_probes=negative_probes,
                source_model=_SOURCE_MODEL,
            )
        )

    return annotations.model_copy(
        update={
            "semantic_axes": semantic_axes,
            "inference_config": {
                **annotations.inference_config,
                "semantic_axes_config_version": axes_config.get("schema_version"),
                "n_semantic_axes": len(axes),
            },
        }
    )
