from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from svp_rpe.calibration import (
    MIN_SAMPLES_PER_GENRE,
    GenreCorpusManifest,
    GenreSample,
    load_genre_manifest,
    run_genre_calibration,
)
from svp_rpe.cli import app

ROOT = Path(__file__).resolve().parents[1]
SEED_MANIFEST = ROOT / "examples" / "calibration" / "genre" / "manifest.yaml"


def _sample(sample_id: str, genre: str, measured: dict[str, float]) -> GenreSample:
    return GenreSample(
        id=sample_id,
        genre_label=genre,
        generator="fixture",
        prompt=f"{genre} fixture",
        measured=measured,
    )


def _manifest(samples: list[GenreSample]) -> GenreCorpusManifest:
    return GenreCorpusManifest(samples=samples)


def _candidate_by_feature(report, feature: str):
    return next(item for item in report.threshold_candidates if item.feature == feature)


def test_manifest_loads_seed_and_forbids_unknown_fields() -> None:
    manifest = load_genre_manifest(SEED_MANIFEST)

    assert manifest.schema_version == "1.0"
    assert [sample.id for sample in manifest.samples] == ["portals", "uza"]
    assert manifest.samples[0].genre_label == "orchestral"

    with pytest.raises(ValidationError):
        GenreSample.model_validate(
            {
                "id": "bad",
                "genre_label": "electronic",
                "generator": "fixture",
                "prompt": "fixture",
                "unexpected": True,
            }
        )


def test_feature_stats_and_threshold_candidates_for_separable_fixture() -> None:
    manifest = _manifest(
        [
            _sample(
                "orch-1",
                "orchestral",
                {"spectral_centroid": 1000, "dynamic_range_db": 18, "harmonic_ratio": 0.80},
            ),
            _sample(
                "orch-2",
                "orchestral",
                {"spectral_centroid": 1100, "dynamic_range_db": 19, "harmonic_ratio": 0.82},
            ),
            _sample(
                "orch-3",
                "orchestral",
                {"spectral_centroid": 1200, "dynamic_range_db": 20, "harmonic_ratio": 0.84},
            ),
            _sample(
                "elec-1",
                "electronic",
                {"spectral_centroid": 3000, "dynamic_range_db": 4, "harmonic_ratio": 0.60},
            ),
            _sample(
                "elec-2",
                "electronic",
                {"spectral_centroid": 3100, "dynamic_range_db": 4.5, "harmonic_ratio": 0.62},
            ),
            _sample(
                "elec-3",
                "electronic",
                {"spectral_centroid": 3200, "dynamic_range_db": 5, "harmonic_ratio": 0.64},
            ),
        ]
    )

    report = run_genre_calibration(manifest, repo_root=ROOT)

    electronic_centroid = report.genres["electronic"].features["spectral_centroid"]
    assert report.genres["electronic"].status == "sufficient"
    assert report.genres["orchestral"].sample_count == MIN_SAMPLES_PER_GENRE
    assert electronic_centroid.count == 3
    assert electronic_centroid.min == 3000
    assert electronic_centroid.max == 3200
    assert electronic_centroid.mean == pytest.approx(3100)
    assert electronic_centroid.std == pytest.approx(81.649658)

    candidate = _candidate_by_feature(report, "spectral_centroid")
    assert candidate.threshold == pytest.approx(2100)
    assert candidate.lower_genre == "orchestral"
    assert candidate.higher_genre == "electronic"
    assert candidate.direction == "electronic > orchestral"
    assert candidate.d is not None


def test_overlapping_feature_does_not_emit_threshold_candidate() -> None:
    manifest = _manifest(
        [
            _sample(f"a-{idx}", "a", {"harmonic_ratio": value})
            for idx, value in enumerate([0.1, 0.2, 0.3], start=1)
        ]
        + [
            _sample(f"b-{idx}", "b", {"harmonic_ratio": value})
            for idx, value in enumerate([0.2, 0.3, 0.4], start=1)
        ]
    )

    report = run_genre_calibration(manifest, repo_root=ROOT)

    harmonic_rows = [
        row for row in report.pair_separability if row.feature == "harmonic_ratio"
    ]
    assert harmonic_rows[0].status == "overlap"
    assert harmonic_rows[0].threshold_candidate is None
    assert report.threshold_candidates == []


def test_insufficient_genres_do_not_emit_threshold_candidates() -> None:
    report = run_genre_calibration(
        _manifest(
            [
                _sample("orch", "orchestral", {"spectral_centroid": 1000}),
                _sample("elec", "electronic", {"spectral_centroid": 3000}),
            ]
        ),
        repo_root=ROOT,
    )

    assert report.genres["orchestral"].status == "insufficient"
    assert report.genres["electronic"].status == "insufficient"
    assert report.threshold_candidates == []
    assert {row.status for row in report.pair_separability} == {"insufficient"}


def test_missing_measured_features_are_excluded_from_that_feature_stats() -> None:
    report = run_genre_calibration(load_genre_manifest(SEED_MANIFEST), repo_root=ROOT)

    assert report.genres["orchestral"].features["harmonic_ratio"].count == 1
    assert report.genres["orchestral"].features["spectral_bands.presence"].count == 0
    assert report.genres["electronic"].features["spectral_bands.brilliance"].mean is None
    assert report.threshold_candidates == []


def test_excluded_samples_are_reported_not_analyzed() -> None:
    report = run_genre_calibration(
        _manifest(
            [
                _sample("kept", "electronic", {"spectral_centroid": 3000}),
                GenreSample(
                    id="drop",
                    genre_label="electronic",
                    generator="fixture",
                    prompt="fixture",
                    measured={"spectral_centroid": 1000},
                    excluded=True,
                ),
            ]
        ),
        repo_root=ROOT,
    )

    assert report.genres["electronic"].sample_count == 1
    assert [item.id for item in report.excluded_samples] == ["drop"]


def test_unresolved_genre_labels_remain_visible_as_insufficient() -> None:
    report = run_genre_calibration(
        _manifest(
            [
                GenreSample(
                    id="missing-rock",
                    genre_label="rock",
                    generator="fixture",
                    prompt="fixture",
                    audio_locator="missing.wav",
                    audio_hash="0" * 64,
                ),
                GenreSample(
                    id="excluded-rock",
                    genre_label="rock",
                    generator="fixture",
                    prompt="fixture",
                    measured={"spectral_centroid": 2500},
                    excluded=True,
                ),
            ]
        ),
        repo_root=ROOT,
    )

    assert report.genres["rock"].sample_count == 0
    assert report.genres["rock"].status == "insufficient"
    assert {item.id for item in report.excluded_samples} == {
        "missing-rock",
        "excluded-rock",
    }


def test_locator_backed_audio_requires_hash_and_checkout_root(tmp_path: Path) -> None:
    root_file = tmp_path / "inside.wav"
    root_file.write_bytes(b"fixture")
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "outside.wav"
    outside_file.write_bytes(b"fixture")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    report = run_genre_calibration(
        _manifest(
            [
                GenreSample(
                    id="no-hash",
                    genre_label="electronic",
                    generator="fixture",
                    prompt="fixture",
                    audio_locator=str(root_file),
                ),
                GenreSample(
                    id="outside-root",
                    genre_label="orchestral",
                    generator="fixture",
                    prompt="fixture",
                    audio_locator=str(outside_file),
                    audio_hash="0" * 64,
                ),
            ]
        ),
        repo_root=repo_root,
    )

    assert report.genres["electronic"].sample_count == 0
    assert report.genres["orchestral"].sample_count == 0
    assert {item.reason for item in report.excluded_samples} == {"no_measured_or_audio"}


def test_cli_text_and_json_smoke_on_seed_manifest(tmp_path: Path) -> None:
    runner = CliRunner()
    text_result = runner.invoke(app, ["genre-calibrate", str(SEED_MANIFEST)])
    assert text_result.exit_code == 0
    assert "Genre Calibration" in text_result.output
    assert "insufficient" in text_result.output

    out = tmp_path / "genre_report.json"
    json_result = runner.invoke(
        app,
        ["genre-calibrate", str(SEED_MANIFEST), "--format", "json", "-o", str(out)],
    )
    assert json_result.exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["genres"]["orchestral"]["status"] == "insufficient"
    assert payload["threshold_candidates"] == []


def test_manifest_yaml_round_trip_shape(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "samples": [
                    {
                        "id": "fixture",
                        "genre_label": "electronic",
                        "generator": "fixture",
                        "prompt": "prompt",
                        "measured": {"spectral_bands": {"presence": 0.2}},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    manifest = load_genre_manifest(path)
    report = run_genre_calibration(manifest, repo_root=ROOT)

    assert report.genres["electronic"].features["spectral_bands.presence"].mean == 0.2
