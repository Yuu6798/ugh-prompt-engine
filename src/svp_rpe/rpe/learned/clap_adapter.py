"""rpe/learned/clap_adapter.py — laion-clap (CLAP) embedding adapter.

Optional via the `semantic-embed` extra. Output is isolated in
`LearnedAudioAnnotations.embedding` (NEVER `PhysicalRPE`, `SemanticRPE`,
`SemanticRPE.por_surface`, or `SVPForGeneration.style_tags`). See
`docs/learned_models_policy.md` for the full policy.

This is the first adapter in `rpe/learned/` to populate
`LearnedAudioAnnotations.embedding` — `panns_adapter.py` discards the
embedding it receives (see its docstring). The CLAP audio embedding here
is the numeric substrate for the similarity instrument in
`similarity.py`: prompt<->audio / score<->audio cosine "fit" is a
learned-model-derived signal, cross-checked against — never substituted
for — the rule-based `SemanticRPE` layer. It has no write-through path
into evidence-bearing fields, and this module does not create one.

Upstream API note (laion_clap >= 1.1):
    `laion_clap.CLAP_Module(enable_fusion=False)` constructs the model;
    `.load_ckpt(checkpoint)` loads pretrained weights (downloads the
    upstream default checkpoint when `checkpoint` is `None`). Embeddings
    come from `.get_audio_embedding_from_filelist(x_list, use_tensor=False)`
    (audio) and `.get_text_embedding(x_list, use_tensor=False)` (text),
    both returning a numpy array shaped `(len(x_list), embedding_dim)`.

License note (see docs/learned_models_policy.md Section 3.1 for the
verbatim verification with URLs):
    Code: the PyPI `laion-clap` distribution's own metadata is internally
    inconsistent — `https://pypi.org/pypi/laion-clap/json` `info.license`
    contains the full CC0 1.0 Universal legal text (matching the GitHub
    `LICENSE` file at `https://raw.githubusercontent.com/LAION-AI/CLAP/
    main/LICENSE`), while the package's own trove classifier declares
    `License :: OSI Approved :: Apache Software License`. Both are
    permissive; the discrepancy itself is recorded here rather than
    resolved.
    Weights: unstated. The README's "Pretrained Models" section
    (`https://github.com/LAION-AI/CLAP#pretrained-models`) links
    checkpoints such as `music_audioset_epoch_15_esc_90.14.pt` to
    `https://huggingface.co/lukewys/laion_clap/tree/main` without a
    license statement; the Hugging Face model card was not reachable
    from this environment to cross-check. Re-verify at PR2b-2 when real
    weights are fetched (gate G4, docs/learned_models_policy.md Section 7).
"""
from __future__ import annotations

import importlib
import importlib.metadata as _pkg_metadata
import sys
from typing import Any, Optional

import numpy as np

from svp_rpe.rpe.learned import LearnedModelIncompatible, LearnedModelUnavailable
from svp_rpe.rpe.models import LearnedAudioAnnotations, LearnedEmbedding, LearnedModelInfo

__all__ = [
    "LearnedModelUnavailable",
    "LearnedModelIncompatible",
    "load_clap_model",
    "embed_audio_file",
    "embed_texts",
]

_MODULE_NAME = "laion_clap"
_MODEL_TASK = "embedding"
_MODEL_PROVIDER = "LAION-AI/CLAP"
_SOURCE_MODEL = "laion_clap:CLAP_Module"

# See the module docstring's License note for the verbatim verification.
_CODE_LICENSE = (
    "CC0-1.0 (upstream LICENSE); PyPI trove classifier says Apache-2.0 "
    "(inconsistent upstream metadata, see docs/learned_models_policy.md 3.1)"
)
_WEIGHTS_LICENSE = (
    "要確認: no explicit checkpoint license found in README or linked "
    "Hugging Face repo (unreachable for cross-check); re-verify at PR2b-2"
)
_LICENSE_NOTE = (
    "laion-clap code license metadata is internally inconsistent upstream "
    "(CC0-1.0 LICENSE file vs Apache Software License PyPI classifier); "
    "checkpoint weights license is unstated. See "
    "docs/learned_models_policy.md 3.1 for the verbatim findings."
)

_INSTALL_HINT = (
    "laion_clap is not installed. Install it via the optional "
    "`semantic-embed` extra:\n"
    '    pip install -e ".[semantic-embed]"'
)


def _load_clap_module() -> Any:
    try:
        return importlib.import_module(_MODULE_NAME)
    except ImportError as exc:
        raise LearnedModelUnavailable(_INSTALL_HINT) from exc


def _detect_clap_version() -> Optional[str]:
    """Best-effort detect installed laion_clap version.

    Same fallback chain as the other `rpe/learned` adapters: imported
    package `__version__` first, then `importlib.metadata`, then `None`.
    """
    root = sys.modules.get(_MODULE_NAME)
    if root is not None:
        candidate = getattr(root, "__version__", None)
        if isinstance(candidate, str) and candidate:
            return candidate
    try:
        return _pkg_metadata.version(_MODULE_NAME)
    except _pkg_metadata.PackageNotFoundError:
        return None


def load_clap_model(checkpoint: Optional[str] = None) -> Any:
    """Instantiate a `laion_clap.CLAP_Module` and load its checkpoint.

    `enable_fusion=False` is hard-coded — the fusion variant is a separate
    checkpoint family this adapter does not target. `checkpoint=None`
    defers to upstream's default checkpoint download behavior.

    Raises
    ------
    LearnedModelUnavailable
        If `laion_clap` is not installed.
    LearnedModelIncompatible
        If `laion_clap` is installed but `CLAP_Module` or `load_ckpt` is
        missing from its public surface (upstream API shift).
    """
    clap_root = _load_clap_module()
    if not hasattr(clap_root, "CLAP_Module"):
        raise LearnedModelIncompatible(
            "laion_clap.CLAP_Module not found; incompatible upstream version"
        )
    model = clap_root.CLAP_Module(enable_fusion=False)
    if not hasattr(model, "load_ckpt"):
        raise LearnedModelIncompatible(
            "laion_clap.CLAP_Module has no load_ckpt method; incompatible upstream version"
        )
    model.load_ckpt(checkpoint)
    return model


def _l2_normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm < 1e-12:
        return vector
    return vector / norm


def embed_audio_file(
    path: str,
    model: Optional[Any] = None,
    checkpoint: Optional[str] = None,
) -> LearnedAudioAnnotations:
    """Embed a single audio file with CLAP, isolated in `LearnedAudioAnnotations`.

    Output is intended to be attached via
    `svp_rpe.rpe.learned.attach_learned_annotations`. It MUST NOT be
    merged into `PhysicalRPE`, `SemanticRPE.por_surface`,
    `SVPForGeneration.style_tags`, or scoring — see
    `docs/learned_models_policy.md` Section 2.

    Parameters
    ----------
    path
        Path to a local audio file, forwarded verbatim to
        `get_audio_embedding_from_filelist`.
    model
        A model already returned by `load_clap_model`. If omitted, a
        fresh model is loaded via `load_clap_model(checkpoint)` — callers
        processing many files should load once and pass `model` to avoid
        reloading the checkpoint per call.
    checkpoint
        Forwarded to `load_clap_model` when `model` is not supplied.
        Recorded in `inference_config.checkpoint` for provenance
        regardless of whether it triggered a fresh load.

    Raises
    ------
    LearnedModelUnavailable
        If `laion_clap` is not installed.
    LearnedModelIncompatible
        If `laion_clap` is installed but its audio-embedding method is
        missing or returns an unexpected shape (expected: `(1, dim)`).
    """
    clap_module = model if model is not None else load_clap_model(checkpoint)
    if not hasattr(clap_module, "get_audio_embedding_from_filelist"):
        raise LearnedModelIncompatible(
            "laion_clap model has no get_audio_embedding_from_filelist method; "
            "incompatible upstream version"
        )
    raw = clap_module.get_audio_embedding_from_filelist([path], use_tensor=False)
    array = np.asarray(raw, dtype=np.float64)
    if array.ndim != 2 or array.shape[0] != 1:
        raise LearnedModelIncompatible(
            f"expected a (1, dim) audio embedding, got shape {array.shape}"
        )
    vector = _l2_normalize(array[0])

    return LearnedAudioAnnotations(
        enabled_models=[
            LearnedModelInfo(
                name=_MODULE_NAME,
                version=_detect_clap_version(),
                provider=_MODEL_PROVIDER,
                task=_MODEL_TASK,
                license=_CODE_LICENSE,
                weights_license=_WEIGHTS_LICENSE,
            )
        ],
        embedding=LearnedEmbedding(
            source_model=_SOURCE_MODEL,
            vector=vector.tolist(),
            dimensions=vector.shape[0],
        ),
        inference_config={
            "checkpoint": checkpoint,
            "enable_fusion": False,
            "source": _MODULE_NAME,
            "audio_path": path,
        },
        license_metadata={_MODULE_NAME: _LICENSE_NOTE},
    )


def embed_texts(texts: list[str], model: Optional[Any] = None) -> list[list[float]]:
    """Embed `texts` with CLAP's text tower; L2-normalized.

    NOTE: unlike `embed_audio_file`, these vectors are transient
    computation inputs for the similarity instrument in `similarity.py`
    (`cosine_similarity` / `prompt_audio_fit` / `contrast_fit`), NOT
    annotations. They are never attached to `LearnedAudioAnnotations` or
    any other bundle field — callers compute them ad hoc for cosine
    comparisons and discard them.

    Parameters
    ----------
    texts
        Prompt strings to embed.
    model
        A model already returned by `load_clap_model`. If omitted, a
        fresh model is loaded via `load_clap_model()` with the default
        checkpoint.

    Raises
    ------
    LearnedModelUnavailable
        If `laion_clap` is not installed.
    LearnedModelIncompatible
        If `laion_clap` is installed but its text-embedding method is
        missing or returns an unexpected shape (expected: `(len(texts), dim)`).
    """
    clap_module = model if model is not None else load_clap_model()
    if not hasattr(clap_module, "get_text_embedding"):
        raise LearnedModelIncompatible(
            "laion_clap model has no get_text_embedding method; incompatible upstream version"
        )
    raw = clap_module.get_text_embedding(list(texts), use_tensor=False)
    array = np.asarray(raw, dtype=np.float64)
    if array.ndim != 2 or array.shape[0] != len(texts):
        raise LearnedModelIncompatible(
            f"expected a ({len(texts)}, dim) text embedding, got shape {array.shape}"
        )
    return [_l2_normalize(array[index]).tolist() for index in range(array.shape[0])]
