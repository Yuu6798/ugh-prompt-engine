"""tests/test_cli.py — CLI smoke tests."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

import svp_rpe.batch.runner as batch_runner
import svp_rpe.rpe.extractor as extractor
from svp_rpe.cli import app
from svp_rpe.rpe.models import (
    DeltaEProfile,
    GrvAnchor,
    PhysicalRPE,
    RPEBundle,
    SectionMarker,
    SemanticLabel,
    SemanticRPE,
    SpectralProfile,
)

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "SVP-RPE" in result.output


def test_extract_with_real_audio(sine_wave_mono):
    result = runner.invoke(app, ["extract", sine_wave_mono])
    assert result.exit_code == 0
    assert "RPE" in result.output or "schema_version" in result.output


def _fake_physical(stem_rpe: dict[str, PhysicalRPE] | None = None) -> PhysicalRPE:
    return PhysicalRPE(
        duration_sec=1.0,
        sample_rate=22050,
        structure=[SectionMarker(label="Full", start_sec=0.0, end_sec=1.0)],
        rms_mean=0.1,
        peak_amplitude=0.2,
        crest_factor=2.0,
        active_rate=0.8,
        valley_depth=0.1,
        thickness=0.2,
        spectral_centroid=1000.0,
        spectral_profile=SpectralProfile(
            centroid=1000.0,
            low_ratio=0.2,
            mid_ratio=0.6,
            high_ratio=0.2,
            brightness=0.2,
        ),
        onset_density=1.0,
        stem_rpe=stem_rpe or {},
    )


def _fake_rpe_bundle(*, include_stems: bool = False) -> RPEBundle:
    stem_rpe = {name: _fake_physical() for name in ("vocals", "drums", "bass", "other")}
    physical = _fake_physical(stem_rpe=stem_rpe if include_stems else None)
    semantic = SemanticRPE(
        por_core="A controlled test audio",
        por_surface=[
            SemanticLabel(
                label="controlled",
                layer="perceptual",
                confidence=0.9,
                evidence=["fixture=true"],
                source_rule="test.fixture",
            )
        ],
        grv_anchor=GrvAnchor(primary="controlled", secondary=[], confidence=0.9),
        delta_e_profile=DeltaEProfile(
            transition_type="flat",
            intensity=0.1,
            description="Fixture energy",
        ),
        cultural_context=[],
        instrumentation_summary="test fixture",
        production_notes=[],
        confidence_notes=[],
    )
    return RPEBundle(
        physical=physical,
        semantic=semantic,
        audio_file="fixture.wav",
        audio_duration_sec=1.0,
        audio_sample_rate=22050,
        audio_channels=1,
        audio_format="wav",
    )


def _patch_fake_extractor(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, dict]] = []

    def fake_extract(path: str, **kwargs):
        calls.append((path, kwargs))
        return _fake_rpe_bundle(include_stems=kwargs.get("include_stems", False))

    monkeypatch.setattr(extractor, "extract_rpe_from_file", fake_extract)
    return calls


def test_extract_separate_options_forward_to_extractor(monkeypatch):
    calls = _patch_fake_extractor(monkeypatch)

    result = runner.invoke(
        app,
        [
            "extract",
            "fixture.wav",
            "--separate",
            "--separation-model",
            "fake-model",
            "--separation-device",
            "cuda",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "fixture.wav",
            {
                "valley_method": "hybrid",
                "include_stems": True,
                "separation_model": "fake-model",
                "separation_device": "cuda",
            },
        )
    ]
    assert '"stem_rpe"' in result.output
    assert '"vocals"' in result.output


def test_extract_default_does_not_request_stems(monkeypatch):
    calls = _patch_fake_extractor(monkeypatch)

    result = runner.invoke(app, ["extract", "fixture.wav"])

    assert result.exit_code == 0
    assert calls[0][1]["include_stems"] is False
    assert '"stem_rpe"' not in result.output


def test_run_with_real_audio(sine_wave_mono):
    result = runner.invoke(app, ["run", sine_wave_mono, "--no-save"])
    assert result.exit_code == 0
    assert "Integrated Score" in result.output


def test_evaluate_accepts_baseline_profile(sine_wave_mono):
    result = runner.invoke(app, ["evaluate", "--audio", sine_wave_mono, "--baseline", "edm"])
    assert result.exit_code == 0
    assert '"baseline_profile": "edm"' in result.output


def test_evaluate_separate_options_forward_to_extractor(monkeypatch):
    calls = _patch_fake_extractor(monkeypatch)

    result = runner.invoke(
        app,
        ["evaluate", "--audio", "fixture.wav", "--separate", "--separation-model", "mdx_extra"],
    )

    assert result.exit_code == 0
    assert calls[0][1]["include_stems"] is True
    assert calls[0][1]["separation_model"] == "mdx_extra"
    assert calls[0][1]["separation_device"] == "cpu"


def test_run_separate_options_forward_to_extractor(monkeypatch):
    calls = _patch_fake_extractor(monkeypatch)

    result = runner.invoke(
        app,
        [
            "run",
            "fixture.wav",
            "--no-save",
            "--separate",
            "--separation-model",
            "fake-model",
            "--separation-device",
            "cuda",
        ],
    )

    assert result.exit_code == 0
    assert calls[0][1]["include_stems"] is True
    assert calls[0][1]["separation_model"] == "fake-model"
    assert calls[0][1]["separation_device"] == "cuda"


def test_compare_does_not_accept_separate_flag(monkeypatch):
    """compare's stem flag was removed because comparison engine ignores stem_rpe."""
    _patch_fake_extractor(monkeypatch)

    result = runner.invoke(
        app,
        [
            "compare",
            "--reference-audio",
            "ref.wav",
            "--candidate-audio",
            "candidate.wav",
            "--separate",
        ],
    )

    assert result.exit_code != 0
    assert "no such option" in result.output.lower() or "--separate" in result.output


def test_batch_separate_options_forward_to_runner(monkeypatch):
    calls: list[dict] = []

    def fake_run_batch(audio_dir: str, **kwargs):
        calls.append({"audio_dir": audio_dir, **kwargs})
        return {"total_files": 1, "successful": 1, "failed": 0, "ranking": []}

    monkeypatch.setattr(batch_runner, "run_batch", fake_run_batch)

    result = runner.invoke(
        app,
        [
            "batch",
            "audio_dir",
            "--separate",
            "--separation-model",
            "fake-model",
            "--separation-device",
            "cuda",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "audio_dir": "audio_dir",
            "svp_dir": None,
            "mode": "evaluate",
            "output_dir": None,
            "baseline": "pro",
            "include_stems": True,
            "separation_model": "fake-model",
            "separation_device": "cuda",
        }
    ]


@pytest.mark.parametrize(
    "args",
    [
        ["evaluate", "--audio", "nonexistent.wav", "--baseline", "jazz"],
        ["run", "nonexistent.wav", "--baseline", "jazz"],
        ["batch", "nonexistent_dir", "--baseline", "jazz"],
    ],
)
def test_baseline_option_rejects_unknown_profile(args):
    result = runner.invoke(app, args)

    # Choice validation should reject the profile before command body execution.
    assert result.exit_code != 0
    assert "Invalid value" in result.output
    assert "jazz" in result.output
    assert "acoustic" in result.output
    assert "edm" in result.output
    assert "loud_pop" in result.output
    assert "pro" in result.output


def test_extract_missing_file():
    result = runner.invoke(app, ["extract", "nonexistent.wav"])
    assert result.exit_code != 0
