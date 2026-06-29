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
    load_genre_manifest,
    run_genre_misfire_audit,
)
from svp_rpe.cli import app

ROOT = Path(__file__).resolve().parents[1]
SEED_MANIFEST = ROOT / "examples" / "calibration" / "genre" / "manifest.yaml"


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


def test_bright_low_heavy_orchestral_label_is_reported_as_mismatch() -> None:
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


def test_seed_manifest_uses_current_brightness_split() -> None:
    report = run_genre_misfire_audit(load_genre_manifest(SEED_MANIFEST), repo_root=ROOT)

    portals = next(item for item in report.predictions if item.id == "portals")
    assert portals.predicted_cultural_context == ["cinematic/orchestral"]
    # 2026-06-25 実 Suno seed（orchestral 5 本 + portals）は全て B-2 brightness split で
    # cinematic/orchestral に着地し bass-music へ誤判定しない（旧 low_ratio>0.4 の誤診を是正）。
    assert report.confusion["orchestral"]["cinematic/orchestral"] == 6
    assert report.confusion["orchestral"].get("bass-music", 0) == 0
    # electronic-dance seed 5 本は bass-music に着地。
    assert report.confusion["electronic-dance"]["bass-music"] == 5
    # Phase B-3-rock: rock seed 5 本は brilliance 中域バンドで rock に着地し、
    # bass-music / cinematic へ裂けない（旧単一閾値 0.1537 の誤分割を是正）。
    assert report.confusion["rock"]["rock"] == 5
    assert report.confusion["rock"].get("bass-music", 0) == 0
    assert report.confusion["rock"].get("cinematic/orchestral", 0) == 0
    rock_preds = [item for item in report.predictions if item.genre_label == "rock"]
    assert all(item.mismatch is False for item in rock_preds)


def test_real_anchors_are_visible_in_audit() -> None:
    """Phase E ルール追加後の本物アンカー（`*-real`）の audit 状態を pin する。

    Phase C で `GENRE_CONTEXT_EXPECTATIONS` に `*-real` を配線し死角を解消（#111 Codex P2）、
    Phase D で rock/edm を本物対応へ再設計した結果:
    - rock-real 3/3 → match（brilliance 下限 0.117→0.105 で grunge HSB 0.108 を捕捉）。
    - edm-real 3/3 → match（sub_bass で rock と分離。Strobe/Levels が brilliance 帯重なりでも bass-music）。

    Phase E で orchestral の中域主役域を onset_density 第二軸で捕捉:
    - 本物管弦は low_ratio<0.4 の mid-dominant でスペクトル(low/mid/harmonic/centroid)は
      thin dark synth と分離不能（Phase A 罠）だが、onset_density が分離する
      （repo synth 0.133–0.167 vs 管弦 star wars 3.87 / holst 3.93 / ano 2.34）。gate>=1.0 を追加。
    - 3 本とも実測 onset_density を manifest に保全したので **orchestral-real 3/3 → match**
      （star wars は sha256 一致の FLAC 再添付で n=3 化が完了。Phase D の既知限界を解消）。
      synth 側は `test_thin_dark_synth_material_does_not_fire_genre_split` が pin している。
    """
    report = run_genre_misfire_audit(load_genre_manifest(SEED_MANIFEST), repo_root=ROOT)
    by_id = {item.id: item for item in report.predictions}

    # rock / edm / orchestral 本物は全て match（Phase D で rock/edm、Phase E で orchestral を解消）
    for label in ("rock-real", "edm-real", "orchestral-real"):
        preds = [item for item in report.predictions if item.genre_label == label]
        assert len(preds) == 3, (label, len(preds))
        assert all(item.mismatch is False for item in preds), [
            (p.id, p.predicted_cultural_context, p.mismatch) for p in preds
        ]
    assert by_id["rock_real_01_heartshapedbox"].predicted_cultural_context == ["rock"]
    assert by_id["edm_real_02_strobe"].predicted_cultural_context == ["bass-music"]
    for orch_id in (
        "orchestral_real_01_starwars",
        "orchestral_real_02_holst_mars",
        "orchestral_real_03_ano_natsu_e",
    ):
        assert "cinematic/orchestral" in by_id[orch_id].predicted_cultural_context, (
            orch_id,
            by_id[orch_id].predicted_cultural_context,
        )


def test_audit_uses_backfilled_production_genre_sections(monkeypatch) -> None:
    monkeypatch.setattr(audit_module, "load_config", lambda name: {})

    report = audit_module.run_genre_misfire_audit(
        _manifest(
            [
                _sample(
                    "orch-low-heavy",
                    "orchestral",
                    {"low_ratio": 0.45, "high_ratio": 0.005, "valley_depth": 0.10},
                )
            ]
        ),
        repo_root=ROOT,
    )

    assert report.confusion["orchestral"]["cinematic/orchestral"] == 1


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
                        "measured": {
                            "low_ratio": 0.45,
                            "high_ratio": 0.005,
                            "valley_depth": 0.1,
                        },
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
    assert "cinematic/orchestral" in text_result.output

    out = tmp_path / "audit.json"
    json_result = runner.invoke(
        app,
        ["genre-audit", str(manifest_path), "--format", "json", "-o", str(out)],
    )
    assert json_result.exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["confusion"]["orchestral"]["cinematic/orchestral"] == 1
    _assert_no_outcome_keys(payload)
