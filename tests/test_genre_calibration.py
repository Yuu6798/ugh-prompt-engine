from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

import svp_rpe.rpe.extractor as extractor
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
    ids = [sample.id for sample in manifest.samples]
    # 本物アンカー stub（先頭）+ 2026-06-25 実 Suno 実測 seed（orchestral/EDM 各 5 本）。
    assert ids[:2] == ["portals", "uza"]
    assert manifest.samples[0].genre_label == "orchestral"
    by_label: dict[str, int] = {}
    for sample in manifest.samples:
        by_label[sample.genre_label] = by_label.get(sample.genre_label, 0) + 1
    assert by_label["orchestral"] == 6  # portals + orchestral_01..05
    assert by_label["electronic-dance"] == 5

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

    # orchestral seed 6 本全てに harmonic_ratio があり count=6。
    assert report.genres["orchestral"].features["harmonic_ratio"].count == 6
    # spectral_bands 未計測の portals stub は presence の count から除外される（5 本のみ）。
    assert report.genres["orchestral"].features["spectral_bands.presence"].count == 5
    # uza stub は bands 未計測なので electronic の brilliance は None のまま。
    assert report.genres["electronic"].features["spectral_bands.brilliance"].mean is None


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


def test_verified_audio_preferred_over_cached_measured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio = tmp_path / "take.wav"
    audio.write_bytes(b"verified")
    from svp_rpe.perform import sha256_bytes

    calls: list[str] = []

    def fake_extract(path: str):
        calls.append(path)
        return SimpleNamespace(
            physical=SimpleNamespace(
                model_dump=lambda mode="json": {"spectral_centroid": 4321.0}
            )
        )

    monkeypatch.setattr(extractor, "extract_rpe_from_file", fake_extract)

    report = run_genre_calibration(
        _manifest(
            [
                GenreSample(
                    id="verified",
                    genre_label="electronic",
                    generator="fixture",
                    prompt="fixture",
                    measured={"spectral_centroid": 1000.0},
                    audio_locator="take.wav",
                    audio_hash=sha256_bytes(b"verified"),
                )
            ]
        ),
        repo_root=tmp_path,
    )

    assert calls == [str(audio)]
    assert report.genres["electronic"].features["spectral_centroid"].mean == 4321.0


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
    # 実 Suno seed で orchestral/electronic-dance は sufficient、uza stub の
    # electronic は n=1 で insufficient。
    assert payload["genres"]["orchestral"]["status"] == "sufficient"
    assert payload["genres"]["electronic"]["status"] == "insufficient"
    assert payload["threshold_candidates"] != []


def _pair_row(report, feature: str, genre_a: str, genre_b: str):
    for row in report.pair_separability:
        if row.feature == feature and {row.genre_a, row.genre_b} == {genre_a, genre_b}:
            return row
    raise AssertionError(f"no pair row for {feature} {genre_a}/{genre_b}")


def test_low_mid_power_bands_stay_power_q1_5_ph2() -> None:
    """Q1-5 Ph2: low/mid_ratio の power→magnitude 移行は seed 実測で「不要かつ不可」と判明。

    高域 brightness（power high_ratio）は #91 で power が defective と分かり B-3 で
    magnitude `brilliance` へ移行した。残る low/mid_ratio についても seed corpus
    （orchestral/rock/electronic-dance）で測ると:

    1. power `low_ratio` は 3 低域厚ジャンルを全て admit（min>0.4）＝**判別器でなくゲート**。
       ジャンル間は全ペア overlap で discriminate しない。`mid_ratio` も同様に overlap。
    2. magnitude 低域 `spectral_bands.bass` も全ペア overlap＝magnitude にしてもゲートとして
       power より優れない（高域 brilliance のような分離は低域には無い）。
    3. 一方 `spectral_bands.brilliance` は全ペア candidate（分離可、d>3）＝**判別器は既に
       B-3 で magnitude 化済み**。

    結論: low/mid の power 帯は健全なゲートで、移行の是正動機が無い。ゲート境界
    （低域厚 vs 非低域厚 general）の magnitude 再導出には general アンカーが要るが seed に
    不在のため Phase C 待ち。本テストはこの「low/mid は power 据え置き」決定を回帰固定する。
    """
    report = run_genre_calibration(load_genre_manifest(SEED_MANIFEST), repo_root=ROOT)
    low_heavy = ("orchestral", "rock", "electronic-dance")
    pairs = (
        ("orchestral", "electronic-dance"),
        ("rock", "electronic-dance"),
        ("orchestral", "rock"),
    )

    # (1) power low_ratio ゲートは 3 低域厚ジャンルを全て admit（>0.4）
    for genre in low_heavy:
        low_stats = report.genres[genre].features["low_ratio"]
        assert low_stats.min is not None and low_stats.min > 0.4, genre

    # (1)(2) power low/mid_ratio と magnitude 低域 bass は判別器にならない（全ペア overlap）
    for feature in ("low_ratio", "mid_ratio", "spectral_bands.bass"):
        for genre_a, genre_b in pairs:
            assert _pair_row(report, feature, genre_a, genre_b).status == "overlap", (
                feature,
                genre_a,
                genre_b,
            )

    # (3) 判別器は magnitude brilliance（全ペア分離可）＝高域のみ magnitude 化が必要だった
    for genre_a, genre_b in pairs:
        row = _pair_row(report, "spectral_bands.brilliance", genre_a, genre_b)
        assert row.status == "candidate", (genre_a, genre_b)
        assert row.d is not None and row.d > 3.0, (genre_a, genre_b)


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
