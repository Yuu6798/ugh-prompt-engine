"""`svprpe recast init` のテスト（PR5）。

実音源からの抽出（`extract_rpe_from_file`）を伴うテストは各 `@pytest.mark.slow`
（`examples/sample_input/*.wav` 1 本あたり実測 ~15-20 秒）。TOCTOU 排除の
機械 assert（Codex P2, #210）は `extract_rpe_from_file` を monkeypatch で
差し替えるため非-slow。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from svp_rpe.arrange.identity import load_identity_manifest
from svp_rpe.recast import load_recast_project
from svp_rpe.rpe.models import PhysicalRPE, RPEBundle, SectionMarker, SpectralProfile
from svp_rpe.rpe.semantic_rules import generate_semantic
from svp_rpe.sentinels import is_todo_sentinel
from svp_rpe.cli import app

runner = CliRunner()

SAMPLE_AUDIO = Path("examples/sample_input/synth_01_slow_pad_c_major.wav")


def _stub_bundle(audio_file: str) -> RPEBundle:
    physical = PhysicalRPE(
        bpm=120.0,
        key="C",
        mode="major",
        duration_sec=4.0,
        sample_rate=44100,
        time_signature="4/4",
        time_signature_confidence=0.5,
        structure=[SectionMarker(label="full", start_sec=0.0, end_sec=4.0)],
        rms_mean=0.2,
        peak_amplitude=0.8,
        crest_factor=4.0,
        active_rate=0.7,
        valley_depth=0.2,
        thickness=1.0,
        spectral_centroid=900.0,
        spectral_profile=SpectralProfile(
            centroid=900.0, low_ratio=0.4, mid_ratio=0.5, high_ratio=0.1, brightness=0.1
        ),
        onset_density=2.0,
    )
    return RPEBundle(
        physical=physical,
        semantic=generate_semantic(physical),
        audio_file=audio_file,
        audio_duration_sec=4.0,
        audio_sample_rate=44100,
        audio_channels=1,
        audio_format="wav",
    )


def test_recast_init_extracts_from_project_dir_snapshot_not_original_audio_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2（#210, AGENTS §8-A 項目1）: `extract_rpe_from_file` へ渡す
    path は `audio_path`（ユーザー指定の元ファイル）ではなく、`--project-dir`
    内へ 1 回だけ read_bytes した bytes をそのまま書き出したスナップショット
    でなければならない（実行中に `audio_path` が差し替わっても、抽出/
    コピー/identity manifest の source pin が同一 bytes を消費し続ける —
    TOCTOU 排除）。"""
    project_dir = tmp_path / "proj"
    captured_paths: list[Path] = []

    def _stub_extract(path: str) -> RPEBundle:
        resolved = Path(path).resolve()
        captured_paths.append(resolved)
        # 渡されたのは project-dir 内のコピー先であり、ユーザー指定の元ファイル
        # そのものではない。
        assert resolved.parent == (project_dir / "source").resolve()
        assert resolved != SAMPLE_AUDIO.resolve()
        # かつそのコピーは元ファイルと bytes 一致（同一 read から書き出された
        # スナップショットである証拠）。
        assert resolved.read_bytes() == SAMPLE_AUDIO.read_bytes()
        return _stub_bundle(path)

    monkeypatch.setattr("svp_rpe.rpe.extractor.extract_rpe_from_file", _stub_extract)

    result = runner.invoke(
        app,
        [
            "recast", "init", str(SAMPLE_AUDIO),
            "--project-dir", str(project_dir),
            "--no-interactive",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(captured_paths) == 1  # 抽出は 1 回だけ呼ばれる
    assert captured_paths[0] == (project_dir / "source" / SAMPLE_AUDIO.name).resolve()

    # 抽出失敗時に project-dir を後始末することも同じ機構で検証する（コピー
    # 後に落ちても、既存ファイルの無い project-dir には何も残さない）。
    project_dir_fail = tmp_path / "proj_fail"

    def _stub_extract_fails(path: str) -> RPEBundle:
        raise ValueError("synthetic extraction failure (test double)")

    monkeypatch.setattr("svp_rpe.rpe.extractor.extract_rpe_from_file", _stub_extract_fails)
    fail_result = runner.invoke(
        app,
        [
            "recast", "init", str(SAMPLE_AUDIO),
            "--project-dir", str(project_dir_fail),
            "--no-interactive",
        ],
    )
    assert fail_result.exit_code == 1
    assert not project_dir_fail.exists()


@pytest.mark.slow
def test_recast_init_no_interactive_generates_full_project_with_todo_core(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "proj"

    result = runner.invoke(
        app,
        [
            "recast", "init", str(SAMPLE_AUDIO),
            "--project-dir", str(project_dir),
            "--no-interactive",
        ],
    )
    assert result.exit_code == 0, result.output

    # --- generated file set --------------------------------------------------
    assert (project_dir / "project.yaml").is_file()
    assert (project_dir / "composition_score.yaml").is_file()
    assert (project_dir / "identity.yaml").is_file()
    assert (project_dir / "identity" / "chord_progression.json").is_file()
    assert (project_dir / "identity" / "section_map.json").is_file()
    assert (project_dir / "arrangements" / "default.yaml").is_file()
    assert (project_dir / "source" / SAMPLE_AUDIO.name).is_file()
    assert (
        project_dir / "source" / SAMPLE_AUDIO.name
    ).read_bytes() == SAMPLE_AUDIO.read_bytes()

    # --- loadable + sha256 pins integrity ------------------------------------
    loaded = load_recast_project(project_dir / "project.yaml")
    assert set(loaded.project.variants) == {"default"}
    assert set(loaded.project.backends) == {"suno", "deterministic"}
    assert loaded.project.observation.enabled is True

    manifest = load_identity_manifest(project_dir / "identity.yaml")  # raises on hash mismatch
    anchor_ids = {a.id for a in manifest.anchors}
    assert anchor_ids == {"harmony", "structure"}
    assert manifest.source.rights_basis == "unknown"
    assert manifest.source.locator == f"source/{SAMPLE_AUDIO.name}"
    assert manifest.source.sha256 == hashlib.sha256(SAMPLE_AUDIO.read_bytes()).hexdigest()

    chord_payload = json.loads(
        (project_dir / "identity" / "chord_progression.json").read_text(encoding="utf-8")
    )
    assert chord_payload["schema"] == "chord-sequence/0.1"
    assert len(chord_payload["chords"]) > 0

    section_payload = json.loads(
        (project_dir / "identity" / "section_map.json").read_text(encoding="utf-8")
    )
    assert section_payload["schema_version"] == "section-map/0.1"
    assert len(section_payload["sections"]) > 0

    arrangement = yaml.safe_load(
        (project_dir / "arrangements" / "default.yaml").read_text(encoding="utf-8")
    )
    assert arrangement["preservation"]["identity_anchors"]["harmony"]["mode"] == "hard"
    assert arrangement["preservation"]["identity_anchors"]["structure"]["mode"] == "hard"

    # --- --no-interactive leaves semantic.core as TODO -------------------------
    score = yaml.safe_load(
        (project_dir / "composition_score.yaml").read_text(encoding="utf-8")
    )
    assert is_todo_sentinel(score["semantic"]["core"])

    # --- fail-closed acceptance test: unresolved TODO -> blocked_authoring ------
    plan_result = runner.invoke(
        app,
        [
            "recast", "plan", str(project_dir / "project.yaml"),
            "--variant", "default", "--backend", "suno",
        ],
    )
    assert plan_result.exit_code == 1
    assert "blocked_authoring" in plan_result.output


@pytest.mark.slow
def test_recast_init_interactive_fills_semantic_core_and_avoid(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"

    result = runner.invoke(
        app,
        [
            "recast", "init", str(SAMPLE_AUDIO),
            "--project-dir", str(project_dir),
            "--interactive",
        ],
        input="introspective night drive\nclutter, harsh clipping\nheadlights on a wet road\n",
    )
    assert result.exit_code == 0, result.output

    score = yaml.safe_load(
        (project_dir / "composition_score.yaml").read_text(encoding="utf-8")
    )
    assert score["semantic"]["core"] == (
        "introspective night drive; headlights on a wet road"
    )
    assert score["semantic"]["avoid"] == ["clutter", "harsh clipping"]
    assert not is_todo_sentinel(score["semantic"]["core"])


@pytest.mark.slow
def test_recast_init_refuses_nonempty_project_dir(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "existing.txt").write_text("do not touch", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "recast", "init", str(SAMPLE_AUDIO),
            "--project-dir", str(project_dir),
            "--no-interactive",
        ],
    )
    assert result.exit_code == 1
    assert not (project_dir / "project.yaml").exists()
    assert (project_dir / "existing.txt").read_text(encoding="utf-8") == "do not touch"
