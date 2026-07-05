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
    utils_models=None,
    utils_models_malformed=False,
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

    # Mirror the real wheel's `faster_whisper.utils` surface when requested
    # (its private `_MODELS` shorthand->repo table feeds the tier-2
    # provenance resolution). Always pin `sys.modules["faster_whisper.utils"]`
    # — even to None — so a REAL installed faster-whisper can never leak its
    # mapping into a fake-backend test (hermeticity, same rationale as the
    # metadata pin below).
    if utils_models is not None or utils_models_malformed:
        fake_utils = types.ModuleType("faster_whisper.utils")
        fake_utils._MODELS = (
            "not-a-dict" if utils_models_malformed else dict(utils_models)
        )
        fake_root.utils = fake_utils
        monkeypatch.setitem(sys.modules, "faster_whisper.utils", fake_utils)
    else:
        monkeypatch.setitem(sys.modules, "faster_whisper.utils", None)

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


class TestLyricsModelInfo:
    """`lyrics_model_info(model_size)` must audit the weights that actually ran.

    Codex P2 (#149): a hard-coded `Systran/faster-whisper-small` in the
    provenance record made `--lyrics-model medium` audit as if small was
    used. Plain sizes derive `Systran/faster-whisper-{size}`; anything else
    (HF repo id / local path) is recorded verbatim — never a fabricated
    repo name.
    """

    def test_default_small_resolves_systran_repo(self, monkeypatch):
        _install_fake_faster_whisper(monkeypatch)

        from svp_rpe.rpe.learned.lyrics_adapter import lyrics_model_info

        info = lyrics_model_info()
        assert "Systran/faster-whisper-small" in info.weights_license
        assert "2026-07-05" in info.weights_license

    def test_medium_resolves_systran_medium_repo(self, monkeypatch):
        _install_fake_faster_whisper(monkeypatch)

        from svp_rpe.rpe.learned.lyrics_adapter import lyrics_model_info

        info = lyrics_model_info("medium")
        assert "Systran/faster-whisper-medium" in info.weights_license
        assert "faster-whisper-small verified" in info.weights_license

    def test_custom_hf_repo_id_recorded_verbatim(self, monkeypatch):
        _install_fake_faster_whisper(monkeypatch)

        from svp_rpe.rpe.learned.lyrics_adapter import lyrics_model_info

        info = lyrics_model_info("some-org/custom-model")
        assert "some-org/custom-model" in info.weights_license
        assert "recorded verbatim" in info.weights_license
        # Never fabricate a Systran repo name from an unresolved identifier.
        assert "Systran/faster-whisper-some-org" not in info.weights_license

    def test_local_path_recorded_verbatim(self, monkeypatch):
        _install_fake_faster_whisper(monkeypatch)

        from svp_rpe.rpe.learned.lyrics_adapter import lyrics_model_info

        info = lyrics_model_info("/models/whisper-ct2")
        assert "/models/whisper-ct2" in info.weights_license
        assert "recorded verbatim" in info.weights_license

    # -- tier 2: installed upstream mapping (faster_whisper.utils._MODELS) --

    _FAKE_UPSTREAM_MODELS = {
        "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "distil-large-v3": "distil-whisper/distil-large-v3-ct2",
    }

    def test_upstream_shorthand_resolves_via_installed_mapping(self, monkeypatch):
        # `turbo` is absent from the static _KNOWN_MODEL_SIZES table but
        # present in the installed mapping — the mapped (non-Systran) repo
        # must be recorded, with no license claim, and the note must say
        # the name came from the upstream mapping. (Fake mapping values:
        # we assert the mapping is USED, not that its contents are true.)
        _install_fake_faster_whisper(monkeypatch, utils_models=self._FAKE_UPSTREAM_MODELS)

        from svp_rpe.rpe.learned.lyrics_adapter import lyrics_model_info

        info = lyrics_model_info("turbo")
        assert "mobiuslabsgmbh/faster-whisper-large-v3-turbo" in info.weights_license
        assert "ライセンス未確認" in info.weights_license
        assert "no license claim" in info.weights_license
        assert "faster_whisper.utils._MODELS" in info.weights_license

    def test_static_fallback_when_mapping_absent(self, monkeypatch):
        # Fake without a utils module (the default): tier 2 is unreadable,
        # so "medium" resolves via the static family convention and the
        # note records that source.
        _install_fake_faster_whisper(monkeypatch)

        from svp_rpe.rpe.learned.lyrics_adapter import lyrics_model_info

        info = lyrics_model_info("medium")
        assert "Systran/faster-whisper-medium" in info.weights_license
        assert "static family convention" in info.weights_license

    def test_explicit_repo_id_never_consults_mapping(self, monkeypatch):
        # A "/"-containing identifier is the caller naming exact weights —
        # verbatim even when the installed mapping happens to have the key.
        _install_fake_faster_whisper(
            monkeypatch,
            utils_models={"some-org/custom-model": "elsewhere/other-repo"},
        )

        from svp_rpe.rpe.learned.lyrics_adapter import lyrics_model_info

        info = lyrics_model_info("some-org/custom-model")
        assert "some-org/custom-model" in info.weights_license
        assert "elsewhere/other-repo" not in info.weights_license
        assert "recorded verbatim" in info.weights_license

    def test_malformed_upstream_mapping_falls_back_to_static(self, monkeypatch):
        # `_MODELS` with an unexpected shape (private API drifted) must not
        # raise — tier 2 is skipped and the static convention applies.
        _install_fake_faster_whisper(monkeypatch, utils_models_malformed=True)

        from svp_rpe.rpe.learned.lyrics_adapter import lyrics_model_info

        info = lyrics_model_info("medium")
        assert "Systran/faster-whisper-medium" in info.weights_license
        assert "static family convention" in info.weights_license


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


class TestPreloadedModelProvenance:
    """Reused-model runs must audit what was ACTUALLY loaded (Codex P2 #149).

    `load_lyrics_model` stamps its load params onto the returned model;
    `transcribe_lyrics(model=...)` records stamp > explicit kwargs >
    honest unknown marker — never the silent defaults.
    """

    _UNKNOWN = "unknown (preloaded model without provenance)"

    class _ForeignModel:
        """A preloaded model NOT from load_lyrics_model (no stamp)."""

        def transcribe(self, audio, **kwargs):
            return iter(_DEFAULT_SEGMENTS), _FakeInfo()

    def test_load_lyrics_model_stamps_load_params(self, monkeypatch):
        _install_fake_faster_whisper(monkeypatch)

        from svp_rpe.rpe.learned.lyrics_adapter import load_lyrics_model

        model = load_lyrics_model("medium", device="cuda", compute_type="float16")
        assert model._svp_rpe_load_params == {
            "model_size": "medium",
            "device": "cuda",
            "compute_type": "float16",
        }

    def test_stamped_model_records_actual_load_params(self, monkeypatch, tmp_path):
        _install_fake_faster_whisper(monkeypatch)

        from svp_rpe.rpe.learned.lyrics_adapter import load_lyrics_model, transcribe_lyrics

        model = load_lyrics_model("medium")
        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        result = transcribe_lyrics(str(audio), separate_vocals=False, model=model)

        assert result.inference_config["model_size"] == "medium"
        assert result.inference_config["device"] == "cpu"
        assert result.inference_config["compute_type"] == "int8"

    def test_unstamped_foreign_model_records_unknown_not_defaults(self, tmp_path):
        # No fake faster_whisper install needed: a supplied model skips
        # load_lyrics_model entirely.
        from svp_rpe.rpe.learned.lyrics_adapter import transcribe_lyrics

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        result = transcribe_lyrics(
            str(audio), separate_vocals=False, model=self._ForeignModel()
        )

        for field in ("model_size", "device", "compute_type"):
            assert result.inference_config[field] == self._UNKNOWN
            assert result.inference_config[field] != "small"

    def test_unstamped_model_with_explicit_kwargs_records_kwargs(self, tmp_path):
        from svp_rpe.rpe.learned.lyrics_adapter import transcribe_lyrics

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        result = transcribe_lyrics(
            str(audio),
            separate_vocals=False,
            model=self._ForeignModel(),
            model_size="large-v3",
            device="cuda",
        )

        assert result.inference_config["model_size"] == "large-v3"
        assert result.inference_config["device"] == "cuda"
        # compute_type was neither stamped nor passed — honest unknown.
        assert result.inference_config["compute_type"] == self._UNKNOWN

    def test_stamp_wins_over_contradicting_kwargs(self, monkeypatch, tmp_path):
        _install_fake_faster_whisper(monkeypatch)

        from svp_rpe.rpe.learned.lyrics_adapter import load_lyrics_model, transcribe_lyrics

        model = load_lyrics_model("medium")
        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        # The stamp is the ground truth of what was loaded; a contradicting
        # kwarg must not overwrite it.
        result = transcribe_lyrics(
            str(audio), separate_vocals=False, model=model, model_size="large-v3"
        )

        assert result.inference_config["model_size"] == "medium"


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

    def test_lyrics_model_choice_reflected_in_enabled_models_provenance(
        self, monkeypatch, tmp_path
    ):
        # Codex P2 (#149): `--lyrics-model medium` must audit as medium —
        # the enabled_models weights identifier follows the selected size.
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
                "--lyrics",
                "--lyrics-no-separate",
                "--lyrics-model",
                "medium",
                "-o",
                str(output),
            ],
        )

        assert result.exit_code == 0, result.stdout
        import json

        dumped = json.loads(output.read_text(encoding="utf-8"))
        model_info = dumped["learned_annotations"]["enabled_models"][0]
        assert model_info["name"] == "faster_whisper"
        assert "Systran/faster-whisper-medium" in model_info["weights_license"]
        assert "Systran/faster-whisper-small (Hugging Face)" not in (
            model_info["weights_license"]
        )

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
        assert len(annotations["semantic_axes"]) == 7
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


# ---------------------------------------------------------------------------
# CLI: svprpe lyrics-adherence
# ---------------------------------------------------------------------------


class TestCliLyricsAdherence:
    def _write_expected(self, tmp_path, lines):
        expected_file = tmp_path / "expected.txt"
        expected_file.write_text("\n".join(lines), encoding="utf-8")
        return expected_file

    def test_happy_path_writes_yaml_report_with_model_provenance(
        self, monkeypatch, tmp_path
    ):
        # Codex P2 (#149): the saved report must carry the resolved
        # weights/license record (`model` section), not only
        # inference_config — otherwise audits cannot tell what a shorthand
        # actually resolved to.
        captured = _install_fake_faster_whisper(monkeypatch)

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        expected_file = self._write_expected(tmp_path, ["hello world", "second line"])
        report_path = tmp_path / "report.yaml"

        result = runner.invoke(
            app,
            [
                "lyrics-adherence",
                str(audio),
                "--expected",
                str(expected_file),
                "--lyrics-no-separate",
                "--lyrics-model",
                "medium",
                "-o",
                str(report_path),
            ],
        )

        assert result.exit_code == 0, result.stdout
        assert captured["model_size"] == "medium"

        import yaml

        payload = yaml.safe_load(report_path.read_text(encoding="utf-8"))
        assert payload["overall_similarity"] == 1.0
        assert len(payload["lines"]) == 2
        # Transcription provenance...
        assert payload["inference_config"]["model_size"] == "medium"
        # ...AND the resolved weights/license record.
        assert payload["model"]["name"] == "faster_whisper"
        assert "Systran/faster-whisper-medium" in payload["model"]["weights_license"]
        # 計器: no verdict keys in the saved report.
        assert "verdict" not in payload
        assert "pass" not in payload

    def test_reversed_order_visible_in_terminal_output(self, monkeypatch, tmp_path):
        # Codex P2 (#149, round 7): without -o, the order diagnostics must
        # be visible interactively — a reversed transcript previously
        # rendered indistinguishably from a fully in-order one.
        _install_fake_faster_whisper(monkeypatch)

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        # Fake transcript is "hello world\nsecond line"; expecting the
        # lines in the OPPOSITE order regresses the offset cursor on the
        # second expected row.
        expected_file = self._write_expected(tmp_path, ["second line", "hello world"])

        result = runner.invoke(
            app,
            [
                "lyrics-adherence",
                str(audio),
                "--expected",
                str(expected_file),
                "--lyrics-no-separate",
            ],
        )

        assert result.exit_code == 0, result.stdout
        assert "order_ratio: 0.0000" in result.stdout
        # The regressed row carries the textual out_of_order marker.
        assert "yes" in result.stdout

    def test_in_order_run_prints_order_ratio_without_markers(self, monkeypatch, tmp_path):
        _install_fake_faster_whisper(monkeypatch)

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        expected_file = self._write_expected(tmp_path, ["hello world", "second line"])

        result = runner.invoke(
            app,
            [
                "lyrics-adherence",
                str(audio),
                "--expected",
                str(expected_file),
                "--lyrics-no-separate",
            ],
        )

        assert result.exit_code == 0, result.stdout
        assert "order_ratio: 1.0000" in result.stdout
        # In-order rows keep the out_of_order column quiet.
        assert "yes" not in result.stdout

    def test_probe_unavailable_fails_fast_exit_1(self, monkeypatch, tmp_path):
        _force_lyrics_unavailable(monkeypatch)

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05, sample_rate=16000)
        expected_file = self._write_expected(tmp_path, ["hello world"])

        result = runner.invoke(
            app,
            ["lyrics-adherence", str(audio), "--expected", str(expected_file)],
        )

        assert result.exit_code == 1
        assert '.[lyrics]' in result.stdout
