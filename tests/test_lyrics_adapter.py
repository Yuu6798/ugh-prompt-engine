"""tests/test_lyrics_adapter.py — faster-whisper lyrics adapter tests.

The real `faster_whisper` package is NOT required to run these tests. We
monkeypatch `sys.modules["faster_whisper"]` with a fake module mirroring
the real wheel's surface area:

- `faster_whisper.WhisperModel(model_size, device=..., compute_type=...)`
- `.transcribe(audio, **kwargs)` -> `(segments_iterable, info)`, where
  `info` carries `.language` / `.language_probability` and each segment
  carries `.start` / `.end` / `.text` / `.avg_logprob` / `.no_speech_prob`
  (the faster-whisper `Segment`/`TranscriptionInfo` surface).

Reuses `_write_wav` from `tests.test_clap_adapter` (real tiny WAV fixtures
librosa can decode) rather than duplicating it.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest
from typer.testing import CliRunner

import svp_rpe.rpe.extractor as extractor
from svp_rpe.cli import app
from svp_rpe.io.source_separator import SeparatorNotAvailableError, StemBundle
from svp_rpe.rpe.learned import LearnedModelIncompatible, LearnedModelUnavailable
from svp_rpe.rpe.models import LearnedLyricsTranscription
from tests.test_clap_adapter import _install_fake_clap, _make_bundle, _write_wav

runner = CliRunner()


class _FakeSegment:
    def __init__(self, start, end, text, avg_logprob=-0.1, no_speech_prob=0.01):
        self.start = start
        self.end = end
        self.text = text
        self.avg_logprob = avg_logprob
        self.no_speech_prob = no_speech_prob


class _FakeInfo:
    def __init__(self, language="en", language_probability=0.98):
        self.language = language
        self.language_probability = language_probability


_DEFAULT_SEGMENTS = [
    _FakeSegment(0.0, 1.5, " hello world ", -0.1, 0.01),
    _FakeSegment(1.5, 3.0, "second line", -0.2, 0.02),
]


def _install_fake_faster_whisper(
    monkeypatch: pytest.MonkeyPatch,
    *,
    segments=None,
    language="en",
    language_probability=0.98,
    version=None,
    has_whisper_model=True,
    has_transcribe=True,
) -> dict:
    captured: dict = {}
    resolved_segments = segments if segments is not None else _DEFAULT_SEGMENTS

    class FakeWhisperModel:
        def __init__(self, model_size, device="cpu", compute_type="int8"):
            captured["model_size"] = model_size
            captured["device"] = device
            captured["compute_type"] = compute_type

        def transcribe(self, audio, **kwargs):
            captured["audio"] = audio
            captured["transcribe_kwargs"] = kwargs
            return iter(resolved_segments), _FakeInfo(language, language_probability)

    if not has_transcribe:
        del FakeWhisperModel.transcribe

    fake_root = types.ModuleType("faster_whisper")
    if has_whisper_model:
        fake_root.WhisperModel = FakeWhisperModel
    if version is not None:
        fake_root.__version__ = version

    monkeypatch.setitem(sys.modules, "faster_whisper", fake_root)

    # Keep the fake hermetic even if faster-whisper is actually pip-installed
    # in this environment (same rationale as _install_fake_clap).
    from svp_rpe.rpe.learned import lyrics_adapter

    def _package_not_found(name: str) -> str:
        raise lyrics_adapter._pkg_metadata.PackageNotFoundError(name)

    monkeypatch.setattr(lyrics_adapter._pkg_metadata, "version", _package_not_found)
    return captured


def _force_lyrics_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", None)


def _make_stem_bundle(*, source_path: str, sample_rate: int = 16000, seconds: float = 0.05):
    n_samples = max(1, int(round(seconds * sample_rate)))
    t = np.linspace(0, seconds, n_samples, endpoint=False)
    vocals = (0.3 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    other_stem = np.zeros(n_samples, dtype=np.float32)
    return StemBundle(
        source_path=source_path,
        model_name="htdemucs_ft",
        sample_rate=sample_rate,
        duration_sec=round(n_samples / sample_rate, 4),
        stems={
            "vocals": vocals,
            "drums": other_stem,
            "bass": other_stem,
            "other": other_stem,
        },
    )


# ---------------------------------------------------------------------------
# Availability / compatibility probes
# ---------------------------------------------------------------------------


class TestAdapterUnavailable:
    def test_ensure_lyrics_available_raises_with_install_hint(self, monkeypatch):
        _force_lyrics_unavailable(monkeypatch)

        from svp_rpe.rpe.learned.lyrics_adapter import ensure_lyrics_available

        with pytest.raises(LearnedModelUnavailable, match="lyrics"):
            ensure_lyrics_available()

    def test_load_lyrics_model_raises_with_install_hint(self, monkeypatch):
        _force_lyrics_unavailable(monkeypatch)

        from svp_rpe.rpe.learned.lyrics_adapter import load_lyrics_model

        with pytest.raises(LearnedModelUnavailable, match="faster_whisper"):
            load_lyrics_model()

    def test_transitive_import_error_is_not_swallowed(self, monkeypatch):
        # A transitive dependency failure (module present, but importing it
        # raises something other than ImportError-on-faster_whisper-itself)
        # must fail loudly rather than being mistaken for "not installed".
        def _raise_runtime_error(name, *args, **kwargs):
            if name == "faster_whisper":
                raise RuntimeError("boom: transitive ctranslate2 failure")
            raise ImportError(name)

        import importlib

        monkeypatch.setitem(sys.modules, "faster_whisper", None)
        monkeypatch.setattr(importlib, "import_module", _raise_runtime_error)

        from svp_rpe.rpe.learned.lyrics_adapter import ensure_lyrics_available

        with pytest.raises(RuntimeError, match="transitive ctranslate2 failure"):
            ensure_lyrics_available()

    def test_transcribe_lyrics_raises_with_install_hint(self, monkeypatch, tmp_path):
        _force_lyrics_unavailable(monkeypatch)

        from svp_rpe.rpe.learned.lyrics_adapter import transcribe_lyrics

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        with pytest.raises(LearnedModelUnavailable, match="lyrics"):
            transcribe_lyrics(str(audio), separate_vocals=False)


class TestAdapterIncompatible:
    def test_missing_whisper_model_class(self, monkeypatch):
        _install_fake_faster_whisper(monkeypatch, has_whisper_model=False)

        from svp_rpe.rpe.learned.lyrics_adapter import load_lyrics_model

        with pytest.raises(LearnedModelIncompatible):
            load_lyrics_model()
        assert issubclass(LearnedModelIncompatible, LearnedModelUnavailable)

    def test_missing_transcribe_method(self, monkeypatch, tmp_path):
        _install_fake_faster_whisper(monkeypatch, has_transcribe=False)

        from svp_rpe.rpe.learned.lyrics_adapter import transcribe_lyrics

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        with pytest.raises(LearnedModelIncompatible):
            transcribe_lyrics(str(audio), separate_vocals=False)


# ---------------------------------------------------------------------------
# transcribe_lyrics — full mix path (no separation)
# ---------------------------------------------------------------------------


class TestTranscribeLyricsFullMix:
    def test_populates_transcription_fields(self, monkeypatch, tmp_path):
        captured = _install_fake_faster_whisper(monkeypatch)

        from svp_rpe.rpe.learned.lyrics_adapter import transcribe_lyrics

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)  # already 16kHz -> no resample
        result = transcribe_lyrics(str(audio), separate_vocals=False)

        assert isinstance(result, LearnedLyricsTranscription)
        assert result.language == "en"
        assert result.language_probability == pytest.approx(0.98)
        assert result.text == "hello world\nsecond line"
        assert len(result.segments) == 2
        assert result.segments[0].start_sec == pytest.approx(0.0)
        assert result.segments[0].end_sec == pytest.approx(1.5)
        assert result.segments[0].text == "hello world"
        assert result.segments[0].avg_logprob == pytest.approx(-0.1)
        assert result.segments[0].no_speech_prob == pytest.approx(0.01)
        assert result.source_model == "faster_whisper:WhisperModel"

        assert result.inference_config["model_size"] == "small"
        assert result.inference_config["device"] == "cpu"
        assert result.inference_config["compute_type"] == "int8"
        assert result.inference_config["beam_size"] == 5
        assert result.inference_config["temperature"] == 0.0
        assert result.inference_config["condition_on_previous_text"] is False
        assert result.inference_config["language"] is None
        assert result.inference_config["vocal_separation"] is False
        assert result.inference_config["sample_rate"] == 16000
        assert "separation_model" not in result.inference_config

        assert captured["transcribe_kwargs"] == {
            "language": None,
            "beam_size": 5,
            "temperature": 0.0,
            "condition_on_previous_text": False,
        }
        assert captured["model_size"] == "small"

    def test_forwards_language_and_beam_size(self, monkeypatch, tmp_path):
        captured = _install_fake_faster_whisper(monkeypatch)

        from svp_rpe.rpe.learned.lyrics_adapter import transcribe_lyrics

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        result = transcribe_lyrics(
            str(audio), separate_vocals=False, language="ja", beam_size=3
        )

        assert captured["transcribe_kwargs"]["language"] == "ja"
        assert captured["transcribe_kwargs"]["beam_size"] == 3
        assert result.inference_config["language"] == "ja"

    def test_reuses_provided_model_without_reloading(self, monkeypatch, tmp_path):
        captured = _install_fake_faster_whisper(monkeypatch)

        from svp_rpe.rpe.learned.lyrics_adapter import load_lyrics_model, transcribe_lyrics

        model = load_lyrics_model()
        captured.pop("model_size", None)

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        transcribe_lyrics(str(audio), separate_vocals=False, model=model)
        # No fresh WhisperModel(...) construction should have happened.
        assert "model_size" not in captured

    def test_empty_segments_yield_empty_text(self, monkeypatch, tmp_path):
        _install_fake_faster_whisper(monkeypatch, segments=[])

        from svp_rpe.rpe.learned.lyrics_adapter import transcribe_lyrics

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        result = transcribe_lyrics(str(audio), separate_vocals=False)
        assert result.text == ""
        assert result.segments == []


# ---------------------------------------------------------------------------
# transcribe_lyrics — vocal separation path
# ---------------------------------------------------------------------------


class TestTranscribeLyricsSeparation:
    def test_uses_vocals_stem_and_records_separation_model(self, monkeypatch, tmp_path):
        captured = _install_fake_faster_whisper(monkeypatch)

        import svp_rpe.rpe.learned.lyrics_adapter as lyrics_adapter

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        stem_bundle = _make_stem_bundle(source_path=str(audio), sample_rate=16000)

        recorded_calls: list[dict] = []

        def _fake_separate_stems(path, *, model, device):
            recorded_calls.append({"path": path, "model": model, "device": device})
            return stem_bundle

        monkeypatch.setattr(lyrics_adapter, "separate_stems", _fake_separate_stems)

        result = lyrics_adapter.transcribe_lyrics(
            str(audio),
            separate_vocals=True,
            separation_model="htdemucs_ft",
            separation_device="cpu",
        )

        assert len(recorded_calls) == 1
        assert recorded_calls[0]["model"] == "htdemucs_ft"
        assert recorded_calls[0]["device"] == "cpu"
        # Sample rate already matches the target -> no resample -> the exact
        # vocals stem array is what reached WhisperModel.transcribe.
        assert np.array_equal(captured["audio"], stem_bundle.stems["vocals"])
        assert result.inference_config["vocal_separation"] is True
        assert result.inference_config["separation_model"] == "htdemucs_ft"

    def test_resamples_when_stem_sample_rate_differs(self, monkeypatch, tmp_path):
        captured = _install_fake_faster_whisper(monkeypatch)

        import svp_rpe.rpe.learned.lyrics_adapter as lyrics_adapter

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=44100)
        stem_bundle = _make_stem_bundle(source_path=str(audio), sample_rate=44100)
        monkeypatch.setattr(
            lyrics_adapter, "separate_stems", lambda path, **kwargs: stem_bundle
        )

        lyrics_adapter.transcribe_lyrics(str(audio), separate_vocals=True)

        # Resampled to 16kHz -> array length differs from the original 44.1kHz stem.
        assert captured["audio"].shape[0] != stem_bundle.stems["vocals"].shape[0]
        assert captured["audio"].dtype == np.float32

    def test_separator_not_available_propagates(self, monkeypatch, tmp_path):
        _install_fake_faster_whisper(monkeypatch)

        import svp_rpe.rpe.learned.lyrics_adapter as lyrics_adapter

        def _raise_not_available(path, **kwargs):
            raise SeparatorNotAvailableError("demucs is not installed")

        monkeypatch.setattr(lyrics_adapter, "separate_stems", _raise_not_available)

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        with pytest.raises(SeparatorNotAvailableError):
            lyrics_adapter.transcribe_lyrics(str(audio), separate_vocals=True)


# ---------------------------------------------------------------------------
# CLI: svprpe extract --lyrics
# ---------------------------------------------------------------------------


class TestCliExtractLyrics:
    def test_lyrics_flag_attaches_transcription(self, monkeypatch, tmp_path):
        _install_fake_faster_whisper(monkeypatch)
        monkeypatch.setattr(
            extractor, "extract_rpe_from_file", lambda path, **kwargs: _make_bundle()
        )

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        output = tmp_path / "out.json"

        result = runner.invoke(
            app,
            ["extract", str(audio), "--lyrics", "--lyrics-no-separate", "-o", str(output)],
        )

        assert result.exit_code == 0, result.stdout
        import json

        dumped = json.loads(output.read_text(encoding="utf-8"))
        assert "learned_annotations" in dumped
        assert dumped["learned_annotations"]["lyrics_transcription"]["text"] == (
            "hello world\nsecond line"
        )
        assert dumped["learned_annotations"]["enabled_models"][0]["name"] == "faster_whisper"

    def test_without_lyrics_flag_omits_transcription(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            extractor, "extract_rpe_from_file", lambda path, **kwargs: _make_bundle()
        )

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        output = tmp_path / "out.json"

        result = runner.invoke(app, ["extract", str(audio), "-o", str(output)])

        assert result.exit_code == 0, result.stdout
        import json

        dumped = json.loads(output.read_text(encoding="utf-8"))
        assert "learned_annotations" not in dumped

    def test_lyrics_unavailable_fails_fast_before_extraction(self, monkeypatch, tmp_path):
        _force_lyrics_unavailable(monkeypatch)
        calls: list[str] = []

        def _recording_extract(path, **kwargs):
            calls.append(path)
            return _make_bundle()

        monkeypatch.setattr(extractor, "extract_rpe_from_file", _recording_extract)

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)

        result = runner.invoke(app, ["extract", str(audio), "--lyrics"])

        assert result.exit_code == 1
        assert calls == []
        assert '.[lyrics]' in result.stdout

    def test_lyrics_flags_propagate_to_transcribe_lyrics(self, monkeypatch, tmp_path):
        captured = _install_fake_faster_whisper(monkeypatch)
        monkeypatch.setattr(
            extractor, "extract_rpe_from_file", lambda path, **kwargs: _make_bundle()
        )

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)

        result = runner.invoke(
            app,
            [
                "extract",
                str(audio),
                "--lyrics",
                "--lyrics-no-separate",
                "--lyrics-model",
                "medium",
            ],
        )

        assert result.exit_code == 0, result.stdout
        assert captured["model_size"] == "medium"

    def test_combined_clap_semantic_and_lyrics_both_populated(self, monkeypatch, tmp_path):
        from tests.test_semantic_axes import _VOCAL_TEXT_VECTORS

        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0], text_vectors=_VOCAL_TEXT_VECTORS)
        _install_fake_faster_whisper(monkeypatch)
        monkeypatch.setattr(
            extractor, "extract_rpe_from_file", lambda path, **kwargs: _make_bundle()
        )

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        output = tmp_path / "out.json"

        result = runner.invoke(
            app,
            [
                "extract",
                str(audio),
                "--clap-semantic",
                "--lyrics",
                "--lyrics-no-separate",
                "-o",
                str(output),
            ],
        )

        assert result.exit_code == 0, result.stdout
        import json

        dumped = json.loads(output.read_text(encoding="utf-8"))
        annotations = dumped["learned_annotations"]
        assert len(annotations["semantic_axes"]) == 5
        assert annotations["lyrics_transcription"]["text"] == "hello world\nsecond line"
        model_names = {m["name"] for m in annotations["enabled_models"]}
        assert model_names == {"laion_clap", "faster_whisper"}

    def test_isolation_svp_and_semantic_unchanged_with_lyrics(self, monkeypatch, tmp_path):
        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.lyrics_adapter import lyrics_model_info, transcribe_lyrics
        from svp_rpe.rpe.models import LearnedAudioAnnotations
        from svp_rpe.svp.generator import generate_svp

        sentinel = "__LYRICS_ISOLATION_SENTINEL__"
        _install_fake_faster_whisper(
            monkeypatch, segments=[_FakeSegment(0.0, 1.0, sentinel)]
        )

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)

        bundle = _make_bundle()
        transcription = transcribe_lyrics(str(audio), separate_vocals=False)
        assert sentinel in transcription.text

        annotations = LearnedAudioAnnotations(
            enabled_models=[lyrics_model_info()], lyrics_transcription=transcription
        )
        enriched = attach_learned_annotations(bundle, annotations)

        assert enriched.semantic.model_dump() == bundle.semantic.model_dump()
        assert enriched.physical.model_dump() == bundle.physical.model_dump()
        assert generate_svp(bundle).model_dump() == generate_svp(enriched).model_dump()

        svp_json = generate_svp(enriched).model_dump_json()
        assert sentinel not in svp_json

        enriched_json = enriched.model_dump_json()
        assert sentinel in enriched_json
        # The sentinel must appear ONLY inside learned_annotations — remove
        # that sub-tree and confirm the sentinel disappears entirely.
        dumped = enriched.model_dump(mode="json")
        dumped.pop("learned_annotations", None)
        import json

        assert sentinel not in json.dumps(dumped, ensure_ascii=False)
