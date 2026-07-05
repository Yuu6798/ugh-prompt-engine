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

# faster-whisper's documented plain model-size convention (the
# `WhisperModel(model_size_or_path)` shorthand set). A `model_size` in this
# tuple resolves to the upstream-converted `Systran/faster-whisper-{size}`
# Hugging Face repo; anything else (an HF repo id containing "/", or a
# local path) is recorded VERBATIM — never fabricate a repo name we did
# not resolve.
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


def _resolve_weights_identifier(model_size: str) -> str:
    """Resolve `model_size` to the weights identifier recorded in provenance.

    A plain size from `_KNOWN_MODEL_SIZES` derives the upstream-converted
    `Systran/faster-whisper-{size}` Hugging Face repo. Anything else (an HF
    repo id containing "/", or a local path) is returned verbatim — this
    function never fabricates a repo name it did not resolve.
    """
    if model_size in _KNOWN_MODEL_SIZES:
        return f"Systran/faster-whisper-{model_size}"
    return model_size


def _weights_license_note(model_size: str) -> str:
    """Honest per-identifier weights-license text for `LearnedModelInfo`.

    Only `Systran/faster-whisper-small`'s model card was actually checked
    (2026-07-05); other plain sizes share the Systran family badge but are
    recorded as unverified family members, and verbatim identifiers (custom
    HF repos / local paths) carry no license claim at all — mirroring how
    docs/learned_models_policy.md scopes the verification.
    """
    identifier = _resolve_weights_identifier(model_size)
    if model_size in _KNOWN_MODEL_SIZES:
        return (
            f"{identifier} (Hugging Face): Systran faster-whisper family "
            "license badge MIT; Systran/faster-whisper-small verified "
            "2026-07-05, other sizes recorded as family members without "
            "per-repo verification"
        )
    return (
        f"{identifier} (recorded verbatim — custom repo id or local path; "
        "weights license not verified, Systran family badge does not apply)"
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
    actually ran (a plain size resolves to its `Systran/faster-whisper-*`
    repo; custom repo ids / local paths are recorded verbatim — see
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
    return module.WhisperModel(model_size, device=device, compute_type=compute_type)


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
    model_size: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
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
        fresh model is loaded via `load_lyrics_model(model_size, device,
        compute_type)` — callers processing many files should load once
        and pass `model` to avoid reloading per call.
    model_size, device, compute_type
        Forwarded to `load_lyrics_model` when `model` is not supplied.
        Recorded in `inference_config` for provenance regardless of
        whether they triggered a fresh load.
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
    waveform, source_sample_rate = _load_waveform(
        audio_path,
        separate_vocals=separate_vocals,
        separation_model=separation_model,
        separation_device=separation_device,
    )
    waveform = _resample_to_target(waveform, source_sample_rate)

    whisper_model = model if model is not None else load_lyrics_model(
        model_size, device, compute_type
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
        "model_size": model_size,
        "device": device,
        "compute_type": compute_type,
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
