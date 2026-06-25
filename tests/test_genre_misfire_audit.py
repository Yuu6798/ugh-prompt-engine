from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from typer.testing import CliRunner

import svp_rpe.calibration.audit as audit_module
from svp_rpe.calibration import (
    GenreCorpusManifest,
    GenreSample,
    run_genre_misfire_audit,
)
from svp_rpe.cli import app

ROOT = Path(__file__).resolve().parents[1]


def _sample(sample_id: str, genre: str, measured: dict[str, Any]) -> GenreSample:
    return GenreSample(
        id=sample_id,
        genre_label=genre,
        generator="fixture",
        prompt=f"{genre} fixture",
        measured=measured,
    )


def _manifest(samples: list[GenreSample]) -> GenreCorpusManifest:
    return GenreCorpusManifest(samples=samples)


def _assert_no_outcome_keys(value: Any) -> None:
    blocked = {"verdict", "pass", "fail", "passed", "failed", "ok"}
    if isinstance(value, dict):
        assert blocked.isdisjoint(value)
        for child in value.values():
            _assert_no_outcome_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_outcome_keys(child)


def test_orchestral_seed_misfires_as_bass_music() -> None:
    report = run_genre_misfire_audit(
        _manifest(
            [
                _sample(
                    "orch-low-heavy",
                    "orchestral",
                    {
                        "low_ratio": 0.45,
                        "mid_ratio": 0.30,
                        "high_ratio": 0.25,
                        "valley_depth": 0.10,
                    },
                )
            ]
        ),
        repo_root=ROOT,
    )

    prediction = report.predictions[0]
    assert "bass-music" in prediction.predicted_cultural_context
    assert "cinematic/orchestral" not in prediction.predicted_cultural_context
    assert report.confusion["orchestral"]["bass-music"] >= 1
    assert prediction.mismatch is True


def test_audit_uses_backfilled_production_genre_sections(monkeypatch) -> None:
    monkeypatch.setattr(audit_module, "load_config", lambda name: {})

    report = audit_module.run_genre_misfire_audit(
        _manifest(
            [
                _sample(
                    "orch-low-heavy",
                    "orchestral",
                    {"low_ratio": 0.45, "valley_depth": 0.10},
                )
            ]
        ),
        repo_root=ROOT,
    )

    assert report.confusion["orchestral"]["bass-music"] == 1


def test_electronic_seed_uses_current_dance_rule_without_mismatch() -> None:
    report = run_genre_misfire_audit(
        _manifest(
            [
                _sample(
                    "edm-active",
                    "electronic",
                    {
                        "bpm": 152.0,
                        "active_rate": 0.85,
                        "low_ratio": 0.20,
                        "mid_ratio": 0.20,
                        "high_ratio": 0.60,
                    },
                )
            ]
        ),
        repo_root=ROOT,
    )

    prediction = report.predictions[0]
    assert prediction.predicted_cultural_context == ["electronic/dance"]
    assert report.confusion["electronic"]["electronic/dance"] == 1
    assert prediction.mismatch is False


def test_missing_bpm_and_active_rate_do_not_fire_tempo_rule() -> None:
    report = run_genre_misfire_audit(
        _manifest(
            [
                _sample(
                    "partial",
                    "electronic",
                    {
                        "low_ratio": 0.10,
                        "mid_ratio": 0.20,
                        "high_ratio": 0.70,
                    },
                )
            ]
        ),
        repo_root=ROOT,
    )

    contexts = report.predictions[0].predicted_cultural_context
    assert "electronic/dance" not in contexts
    assert contexts == ["general"]


def test_excluded_samples_are_reported_not_predicted() -> None:
    report = run_genre_misfire_audit(
        _manifest(
            [
                _sample("kept", "electronic", {"bpm": 150, "active_rate": 0.9}),
                GenreSample(
                    id="drop",
                    genre_label="orchestral",
                    generator="fixture",
                    prompt="fixture",
                    measured={"low_ratio": 0.5},
                    excluded=True,
                ),
            ]
        ),
        repo_root=ROOT,
    )

    assert [item.id for item in report.predictions] == ["kept"]
    assert [item.id for item in report.excluded_samples] == ["drop"]
    assert report.excluded_samples[0].reason == "excluded"


def test_audit_report_has_no_outcome_keys() -> None:
    report = run_genre_misfire_audit(
        _manifest(
            [
                _sample(
                    "orch-low-heavy",
                    "orchestral",
                    {"low_ratio": 0.45, "valley_depth": 0.1},
                )
            ]
        ),
        repo_root=ROOT,
    )

    _assert_no_outcome_keys(report.model_dump(mode="json"))


def test_cli_text_and_json_smoke(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "samples": [
                    {
                        "id": "orch-low-heavy",
                        "genre_label": "orchestral",
                        "generator": "fixture",
                        "prompt": "fixture",
                        "measured": {"low_ratio": 0.45, "valley_depth": 0.1},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    text_result = runner.invoke(app, ["genre-audit", str(manifest_path)])
    assert text_result.exit_code == 0
    assert "Genre Misfire Audit" in text_result.output
    assert "bass-music" in text_result.output

    out = tmp_path / "audit.json"
    json_result = runner.invoke(
        app,
        ["genre-audit", str(manifest_path), "--format", "json", "-o", str(out)],
    )
    assert json_result.exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["confusion"]["orchestral"]["bass-music"] == 1
    _assert_no_outcome_keys(payload)
