"""rpe/learned/lyrics_adapter.py — faster-whisper symbolic lyrics-content sensor.

Optional via the `lyrics` extra. Output is isolated in
`LearnedAudioAnnotations.lyrics_transcription` (NEVER `PhysicalRPE`,
`SemanticRPE`, `SemanticRPE.por_surface`, or `SVPForGeneration.style_tags`).
See `docs/learned_models_policy.md` Section 2 and
`docs/lyrics_transcription_sensor.md` for the full policy and rationale.

Motivation (docs/lyrics_semantic_anchor.md): lyrics are the semantic-layer
anchor where, until now, "the ear is the only sensor". The CLAP semantic
sensor (`rpe/learned/semantic_axes.py`) reads continuous grips (e.g.
`vocal_presence`) but cannot read lyric CONTENT — an ordered symbol
sequence. This module is the complementary symbolic sensor, used on BOTH
sides of the pipeline:

- **Input side** (`svprpe extract --lyrics`): read the SOURCE audio's
  lyrics at extraction time, isolated as an annotation alongside the
  rule-based RPE layers.
- **Output side** (`svprpe lyrics-adherence`, `eval/lyrics_match.py`):
  check whether GENERATED audio sings the ordered lyrics a
  `CompositionScore` (or any reference text) specifies — a check proven
  manually in the 2026-07-03 demo session, now institutionalized as a
  repeatable instrument.

Upstream API note (faster-whisper >= 1.0):
    `faster_whisper.WhisperModel(model_size, device=..., compute_type=...)`
    constructs the model (CTranslate2-backed; downloads/caches weights
    from Hugging Face lazily on first construction). `.transcribe(audio,
    **kwargs)` returns `(segments, info)`: `segments` is a *generator* of
    segment objects (`.start` / `.end` / `.text` / `.avg_logprob` /
    `.no_speech_prob`), and `info` carries `.language` /
    `.language_probability`. `transcribe_lyrics` iterates `segments` fully
    before returning, so the generator is fully consumed inside this
    module — callers never see a partially-drained generator.

Determinism: `temperature=0.0` and `condition_on_previous_text=False` are
hard-coded in the `.transcribe(...)` call so beam search is greedy and
each segment's decoding does not depend on previously decoded text — the
same two knobs CLAP's adapter uses conceptually (no RNG in the deterministic
path). This is a same-machine determinism contract, not a cross-machine
one: CTranslate2 kernel selection can vary the exact floating-point path
across hardware/build, so re-running on a different machine may not
byte-for-byte reproduce timings/logprobs even though the same greedy
decoding target is being sought — the same caveat CLAP's fixture-driven
determinism contract documents for its own backend (see `clap_adapter.py`
and `docs/learned_models_policy.md`).

Isolation policy (docs/learned_models_policy.md Section 2): the output of
`transcribe_lyrics` (a `LearnedLyricsTranscription`) is confined to
`LearnedAudioAnnotations.lyrics_transcription`. It MUST NOT flow into
`SemanticRPE`, `PhysicalRPE`, or `SVPForGeneration.style_tags` — this
module does not create a write-through path into those fields, and no
caller should either.
"""
from __future__ import annotations

import importlib
import importlib.metadata as _pkg_metadata
import sys
from typing import Any, Optional

import librosa
import numpy as np

from svp_rpe.io.source_separator import separate_stems
from svp_rpe.rpe.learned import LearnedModelIncompatible, LearnedModelUnavailable
from svp_rpe.rpe.models import (
    LearnedLyricsSegment,
    LearnedLyricsTranscription,
    LearnedModelInfo,
)

__all__ = [
    "LearnedModelUnavailable",
    "LearnedModelIncompatible",
    "ensure_lyrics_available",
    "load_lyrics_model",
    "transcribe_lyrics",
    "lyrics_model_info",
]

_MODULE_NAME = "faster_whisper"
_MODEL_TASK = "other"
_MODEL_PROVIDER = "SYSTRAN/faster-whisper"
_SOURCE_MODEL = "faster_whisper:WhisperModel"

# faster-whisper expects 16 kHz mono float32 arrays regardless of the
# source's native sample rate (44.1 kHz mix, or whatever Demucs/soundfile
# hands back for the vocals stem). Read as a module global at call time
# (not bound as a default parameter value) so tests can monkeypatch it.
_TARGET_SAMPLE_RATE = 16000

# License note: package-level verification only (see
# docs/learned_models_policy.md's adopt entry for the verbatim record).
_CODE_LICENSE = (
    "MIT (faster-whisper + ctranslate2, pip show verified 2026-07-05)"
)

# faster-whisper's classic plain model-size convention: the shorthand
# subset that maps 1:1 to the upstream-converted
# `Systran/faster-whisper-{size}` Hugging Face repos. This is the STATIC
# FALLBACK tier of `_resolve_weights_identifier` — the installed
# upstream mapping (`faster_whisper.utils._MODELS`), when readable, is
# consulted first because newer shorthands (`turbo`,
# `large-v3-turbo`, `distil-large-v3`, ...) resolve to repos OUTSIDE the
# Systran org (e.g. mobiuslabsgmbh/faster-whisper-large-v3-turbo) that a
# static table here would misrecord.
_KNOWN_MODEL_SIZES = (
    "tiny",
    "tiny.en",
    "base",
    "base.en",
    "small",
    "small.en",
    "medium",
    "medium.en",
    "large-v1",
    "large-v2",
    "large-v3",
    "large",
)

# Resolution-source markers recorded inside the weights-license note so an
# audit can tell HOW the repo name was obtained (see
# `_resolve_weights_identifier`).
_RES_VERBATIM = "recorded verbatim"
_RES_UPSTREAM = "resolved via installed faster_whisper.utils._MODELS"
_RES_STATIC = "static family convention"

# Documented load-parameter defaults, applied ONLY when this module itself
# loads the model. A caller-supplied preloaded model resolves its
# provenance from the `load_lyrics_model` stamp (or explicit kwargs)
# instead — the defaults are NEVER silently recorded for a model this
# call did not load (see `transcribe_lyrics`).
_DEFAULT_MODEL_SIZE = "small"
_DEFAULT_DEVICE = "cpu"
_DEFAULT_COMPUTE_TYPE = "int8"

# Attribute stamped by `load_lyrics_model` onto the returned model so a
# reused model carries the ground truth of what was actually loaded.
_LOAD_PARAMS_ATTR = "_svp_rpe_load_params"

# Honest provenance marker for a preloaded model whose load params cannot
# be determined (no stamp, no explicit kwargs).
_UNKNOWN_PROVENANCE = "unknown (preloaded model without provenance)"

_INSTALL_HINT = (
    "faster_whisper is not installed. Install it via the optional "
    "`lyrics` extra:\n"
    '    pip install -e ".[lyrics]"'
)


def _load_lyrics_module() -> Any:
    try:
        return importlib.import_module(_MODULE_NAME)
    except ImportError as exc:
        raise LearnedModelUnavailable(_INSTALL_HINT) from exc


def ensure_lyrics_available() -> None:
    """Probe that `faster_whisper` is importable, raising `LearnedModelUnavailable`
    (with the `lyrics` install hint) if it is not.

    This only attempts the module import — it does NOT construct a
    `WhisperModel` or load/download weights, so it triggers no model
    download. Callers use it to fail fast on the missing optional
    dependency BEFORE spending time on unrelated work (e.g. base RPE
    extraction / Demucs separation), mirroring
    `clap_adapter.ensure_clap_available`.
    """
    _load_lyrics_module()


def _detect_lyrics_version() -> Optional[str]:
    """Best-effort detect installed faster_whisper version.

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


def _upstream_model_mapping() -> Optional[dict[str, str]]:
    """Best-effort read of the installed `faster_whisper.utils._MODELS` table.

    `_MODELS` is a PRIVATE upstream API mapping documented shorthands
    (`"turbo"`, `"distil-large-v3"`, ...) to the HF repos actually
    downloaded by `WhisperModel(...)` — the ground truth for provenance on
    the installed version. Because it is private it is consulted
    OPPORTUNISTICALLY, never trusted blindly: the import is guarded, the
    shape is validated (a dict of str -> str), and ANY surprise (missing
    module, missing attribute, changed shape) returns `None` so
    `_resolve_weights_identifier` falls through to the static
    `_KNOWN_MODEL_SIZES` convention instead of raising.
    """
    try:
        root = importlib.import_module(_MODULE_NAME)
    except Exception:
        return None
    utils_mod = getattr(root, "utils", None)
    if utils_mod is None:
        try:
            utils_mod = importlib.import_module(f"{_MODULE_NAME}.utils")
        except Exception:
            return None
    mapping = getattr(utils_mod, "_MODELS", None)
    if not isinstance(mapping, dict):
        return None
    if not all(
        isinstance(key, str) and isinstance(value, str) for key, value in mapping.items()
    ):
        return None
    return mapping


def _resolve_weights_identifier(model_size: str) -> tuple[str, str]:
    """Resolve `model_size` to `(weights identifier, resolution source)`.

    Three tiers, in order:

    1. **Verbatim** — the value contains a path separator or looks like a
       filesystem path (explicit HF repo id or local checkpoint dir): the
       caller named the exact weights, so it is recorded as-is and the
       upstream mapping is never consulted.
    2. **Installed upstream mapping** — `faster_whisper.utils._MODELS`
       (see `_upstream_model_mapping`): a documented shorthand resolves to
       the repo the installed version actually downloads, including
       non-Systran repos (`turbo` -> mobiuslabsgmbh/..., `distil-*` ->
       distil-whisper/...).
    3. **Static fallback** — `_KNOWN_MODEL_SIZES` ->
       `Systran/faster-whisper-{size}` (covers fake-backend tests and any
       upstream shape change that makes tier 2 unreadable).

    Anything that matches no tier is recorded verbatim — this function
    never fabricates a repo name it did not resolve.
    """
    if "/" in model_size or "\\" in model_size or model_size.startswith("."):
        return model_size, _RES_VERBATIM
    mapping = _upstream_model_mapping()
    if mapping is not None and model_size in mapping:
        return mapping[model_size], _RES_UPSTREAM
    if model_size in _KNOWN_MODEL_SIZES:
        return f"Systran/faster-whisper-{model_size}", _RES_STATIC
    return model_size, _RES_VERBATIM


def _weights_license_note(model_size: str) -> str:
    """Honest weights-license text for `LearnedModelInfo`, from the RESOLVED repo.

    The license claim derives from the RESOLVED identifier, not the input
    shorthand: only `Systran/faster-whisper-small`'s model card was
    actually checked (2026-07-05); other `Systran/faster-whisper-*` repos
    share the family badge but are recorded as unverified members; any
    other resolved identifier (mobiuslabsgmbh / distil-whisper / verbatim
    custom repo / local path) carries NO license claim at all — mirroring
    how docs/learned_models_policy.md scopes the verification. The
    resolution source (`recorded verbatim` / upstream mapping / static
    convention) is embedded so audits can tell how the repo name was
    obtained.
    """
    identifier, resolution = _resolve_weights_identifier(model_size)
    if identifier == "Systran/faster-whisper-small":
        return (
            f"{identifier} (Hugging Face): license badge MIT, model card "
            f"verified 2026-07-05 ({resolution})"
        )
    if identifier.startswith("Systran/faster-whisper-"):
        return (
            f"{identifier} (Hugging Face): Systran faster-whisper family "
            "license badge MIT; Systran/faster-whisper-small verified "
            "2026-07-05, this repo recorded as a family member without "
            f"per-repo verification ({resolution})"
        )
    return (
        f"{identifier}: ライセンス未確認・採取時に実確認 — no license claim "
        f"({resolution})"
    )


def lyrics_model_info(model_size: str = "small") -> LearnedModelInfo:
    """Provenance entry for `LearnedAudioAnnotations.enabled_models`.

    `transcribe_lyrics` returns the narrower `LearnedLyricsTranscription`
    payload (not a full `LearnedAudioAnnotations`), so callers that attach
    it (the `extract --lyrics` / `lyrics-adherence` CLI paths) compose this
    provenance record alongside it — the same role `clap_adapter`'s
    internal `_model_info()` plays when it builds `embed_audio_file`'s
    `LearnedAudioAnnotations` directly.

    `model_size` MUST be the same value passed to `transcribe_lyrics` /
    `load_lyrics_model` so the audited weights identifier matches what
    actually ran (3-tier resolution: explicit repo id / path verbatim ->
    installed `faster_whisper.utils._MODELS` -> static
    `Systran/faster-whisper-{size}` convention — see
    `_resolve_weights_identifier`).
    """
    return LearnedModelInfo(
        name=_MODULE_NAME,
        version=_detect_lyrics_version(),
        provider=_MODEL_PROVIDER,
        task=_MODEL_TASK,
        license=_CODE_LICENSE,
        weights_license=_weights_license_note(model_size),
    )


def load_lyrics_model(
    model_size: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
) -> Any:
    """Instantiate a `faster_whisper.WhisperModel`.

    The actual load parameters are stamped onto the returned object
    (`_svp_rpe_load_params`) so `transcribe_lyrics(model=...)` can record
    the ground truth of what was loaded instead of guessing — see its
    docstring's provenance-resolution note.

    Raises
    ------
    LearnedModelUnavailable
        If `faster_whisper` is not installed.
    LearnedModelIncompatible
        If `faster_whisper` is installed but `WhisperModel` is missing
        from its public surface (upstream API shift).
    """
    module = _load_lyrics_module()
    if not hasattr(module, "WhisperModel"):
        raise LearnedModelIncompatible(
            "faster_whisper.WhisperModel not found; incompatible upstream version"
        )
    model = module.WhisperModel(model_size, device=device, compute_type=compute_type)
    try:
        setattr(
            model,
            _LOAD_PARAMS_ATTR,
            {"model_size": model_size, "device": device, "compute_type": compute_type},
        )
    except Exception:
        # Stamping is provenance sugar, not a load requirement — attribute
        # assignment can fail on exotic objects (__slots__, frozen
        # wrappers); the model itself is still perfectly usable and
        # `transcribe_lyrics` falls back to explicit kwargs / the honest
        # unknown marker.
        pass
    return model


def _load_waveform(
    audio_path: str,
    *,
    separate_vocals: bool,
    separation_model: str,
    separation_device: str,
) -> tuple[np.ndarray, int]:
    """Return (mono float32 waveform, sample_rate) for `audio_path`.

    When `separate_vocals` is set, delegates to
    `separate_stems` and takes the isolated `"vocals"` stem — a
    `SeparatorNotAvailableError` (missing Demucs) propagates unchanged;
    this is an explicit opt-in path, so it fails fast rather than
    silently falling back to the full mix. Otherwise decodes the full mix
    directly via `librosa.load` (mono, native sample rate).
    """
    if separate_vocals:
        stem_bundle = separate_stems(
            audio_path, model=separation_model, device=separation_device
        )
        return stem_bundle.stems["vocals"], stem_bundle.sample_rate
    waveform, sample_rate = librosa.load(audio_path, sr=None, mono=True)
    return np.asarray(waveform, dtype=np.float32), int(sample_rate)


def _resample_to_target(waveform: np.ndarray, source_sample_rate: int) -> np.ndarray:
    """Resample `waveform` to `_TARGET_SAMPLE_RATE` (16 kHz), deterministically.

    A no-op (aside from a dtype-safe copy) when already at the target
    rate. `librosa.resample` introduces no RNG.
    """
    waveform = np.asarray(waveform, dtype=np.float32)
    if source_sample_rate == _TARGET_SAMPLE_RATE:
        return waveform
    resampled = librosa.resample(
        waveform, orig_sr=source_sample_rate, target_sr=_TARGET_SAMPLE_RATE
    )
    return np.asarray(resampled, dtype=np.float32)


def transcribe_lyrics(
    audio_path: str,
    *,
    separate_vocals: bool = True,
    separation_model: str = "htdemucs_ft",
    separation_device: str = "cpu",
    model: Optional[Any] = None,
    model_size: Optional[str] = None,
    device: Optional[str] = None,
    compute_type: Optional[str] = None,
    language: Optional[str] = None,
    beam_size: int = 5,
) -> LearnedLyricsTranscription:
    """Transcribe `audio_path`'s lyrics with faster-whisper.

    By default isolates the vocals stem via Demucs (`separate_stems`)
    before transcribing — lyrics recognition on a full instrumental mix is
    materially worse, mirroring the rationale for `--separate` elsewhere
    in this repo. Pass `separate_vocals=False` to transcribe the full mix
    directly (no Demucs dependency needed on that path).

    The decoded waveform (vocals stem or full mix) is resampled to 16 kHz
    mono float32 (`_resample_to_target`) before being handed to
    `WhisperModel.transcribe`, which is called with `temperature=0.0` and
    `condition_on_previous_text=False` for deterministic greedy decoding
    (see the module docstring's determinism note — a same-machine
    contract, not a cross-machine one).

    Parameters
    ----------
    audio_path
        Path to a local audio file.
    separate_vocals
        When True (default), isolate vocals via `separate_stems` first.
        `SeparatorNotAvailableError` propagates unchanged if Demucs is not
        installed — this is an explicit opt-in, so it fails fast rather
        than silently transcribing the full mix instead.
    separation_model, separation_device
        Forwarded to `separate_stems` when `separate_vocals` is True.
    model
        A model already returned by `load_lyrics_model`. If omitted, a
        fresh model is loaded via `load_lyrics_model(...)` — callers
        processing many files should load once and pass `model` to avoid
        reloading per call. Models from `load_lyrics_model` carry their
        load parameters automatically (the `_svp_rpe_load_params` stamp);
        a FOREIGN preloaded model should pass `model_size` / `device` /
        `compute_type` explicitly, otherwise those provenance fields are
        recorded as `"unknown (preloaded model without provenance)"`.
    model_size, device, compute_type
        `None` sentinels; provenance resolution per field:

        - `model` is None (this call loads): the explicit value, or its
          documented default (`"small"` / `"cpu"` / `"int8"`), is used for
          the load AND recorded in `inference_config`.
        - `model` supplied: the `load_lyrics_model` stamp — the ground
          truth of what was actually loaded — wins over kwargs; else an
          explicit non-None kwarg is recorded; else the honest unknown
          marker. The defaults are NEVER silently recorded for a model
          this call did not load.
    language
        BCP-47-ish language hint forwarded to `.transcribe` (`None` lets
        faster-whisper auto-detect). Recorded verbatim in
        `inference_config.language`.
    beam_size
        Forwarded to `.transcribe`.

    Raises
    ------
    LearnedModelUnavailable
        If `faster_whisper` is not installed.
    LearnedModelIncompatible
        If `faster_whisper` is installed but its `WhisperModel` /
        `.transcribe` surface is missing (upstream API shift).
    SeparatorNotAvailableError
        If `separate_vocals` is True and Demucs is not installed
        (propagated unchanged from `separate_stems`).
    """
    # Provenance resolution (see the parameter docs above): when this call
    # loads the model, explicit values / documented defaults are both used
    # and recorded; when the model is preloaded, the load stamp wins, then
    # explicit kwargs, then the honest unknown marker — the defaults are
    # never silently recorded for a model this call did not load.
    if model is None:
        recorded_model_size = model_size if model_size is not None else _DEFAULT_MODEL_SIZE
        recorded_device = device if device is not None else _DEFAULT_DEVICE
        recorded_compute_type = (
            compute_type if compute_type is not None else _DEFAULT_COMPUTE_TYPE
        )
    else:
        stamp = getattr(model, _LOAD_PARAMS_ATTR, None)
        stamp = stamp if isinstance(stamp, dict) else {}

        def _provenance(field: str, explicit: Optional[str]) -> str:
            stamped = stamp.get(field)
            if isinstance(stamped, str):
                return stamped
            if explicit is not None:
                return explicit
            return _UNKNOWN_PROVENANCE

        recorded_model_size = _provenance("model_size", model_size)
        recorded_device = _provenance("device", device)
        recorded_compute_type = _provenance("compute_type", compute_type)

    waveform, source_sample_rate = _load_waveform(
        audio_path,
        separate_vocals=separate_vocals,
        separation_model=separation_model,
        separation_device=separation_device,
    )
    waveform = _resample_to_target(waveform, source_sample_rate)

    whisper_model = model if model is not None else load_lyrics_model(
        recorded_model_size, recorded_device, recorded_compute_type
    )
    if not hasattr(whisper_model, "transcribe"):
        raise LearnedModelIncompatible(
            "faster_whisper WhisperModel has no transcribe method; "
            "incompatible upstream version"
        )

    segments_iter, info = whisper_model.transcribe(
        waveform,
        language=language,
        beam_size=beam_size,
        temperature=0.0,
        condition_on_previous_text=False,
    )

    segments: list[LearnedLyricsSegment] = []
    for raw_segment in segments_iter:
        avg_logprob = getattr(raw_segment, "avg_logprob", None)
        no_speech_prob = getattr(raw_segment, "no_speech_prob", None)
        segments.append(
            LearnedLyricsSegment(
                start_sec=round(float(raw_segment.start), 3),
                end_sec=round(float(raw_segment.end), 3),
                text=str(raw_segment.text).strip(),
                avg_logprob=None if avg_logprob is None else round(float(avg_logprob), 4),
                no_speech_prob=(
                    None if no_speech_prob is None else round(float(no_speech_prob), 4)
                ),
            )
        )

    text = "\n".join(segment.text for segment in segments)

    language_probability = getattr(info, "language_probability", None)
    inference_config: dict[str, Any] = {
        "model_size": recorded_model_size,
        "device": recorded_device,
        "compute_type": recorded_compute_type,
        "beam_size": beam_size,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "language": language,
        "vocal_separation": separate_vocals,
        "sample_rate": _TARGET_SAMPLE_RATE,
    }
    if separate_vocals:
        inference_config["separation_model"] = separation_model

    return LearnedLyricsTranscription(
        language=getattr(info, "language", None),
        language_probability=(
            None if language_probability is None else round(float(language_probability), 4)
        ),
        text=text,
        segments=segments,
        source_model=_SOURCE_MODEL,
        inference_config=inference_config,
    )
