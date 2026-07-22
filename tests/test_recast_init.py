"""`svprpe recast init` のテスト（PR5）。

実音源からの抽出（`extract_rpe_from_file`）を伴うテストは各 `@pytest.mark.slow`
（`examples/sample_input/*.wav` 1 本あたり実測 ~15-20 秒）。TOCTOU 排除・
staging atomic 化の機械 assert（Codex P2, #210 / round 2）は
`extract_rpe_from_file` を monkeypatch で差し替えるため非-slow。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

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


def _leftover_staging_dirs(parent: Path, dest_name: str) -> list[Path]:
    if not parent.exists():
        return []
    return [p for p in parent.iterdir() if p.name.startswith(f".{dest_name}.")]


def test_recast_init_extracts_from_staging_snapshot_not_original_audio_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2（#210 AGENTS §8-A 項目1 / round 2 指摘3）: `extract_rpe_from_file`
    へ渡す path は `audio_path`（ユーザー指定の元ファイル）ではなく、
    staging ディレクトリ（`--project-dir` の親配下 = 同一ファイルシステムの
    sibling、`os.replace` の atomic rename 前提）内へ 1 回だけ read_bytes
    した bytes をそのまま書き出したスナップショットでなければならない
    （実行中に `audio_path` が差し替わっても抽出/コピー/identity manifest の
    source pin が同一 bytes を消費し続ける — TOCTOU 排除）。抽出時点では
    `--project-dir` 自体はまだ存在しない（staging 構築中のみで、最終検証後の
    atomic rename で初めて現れる — round 2 指摘3の atomic 化）。"""
    project_dir = tmp_path / "proj"
    captured: list[dict[str, Any]] = []

    def _stub_extract(path: str) -> RPEBundle:
        resolved = Path(path).resolve()
        captured.append({"path": resolved, "project_dir_existed": project_dir.exists()})
        assert resolved.name == SAMPLE_AUDIO.name
        assert resolved.parent.name == "source"
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
    assert len(captured) == 1  # 抽出は 1 回だけ呼ばれる
    # 抽出時点では project-dir 自体はまだ存在しない（staging はその外側 —
    # round 2 指摘3の atomic 化そのものの機械 assert）。
    assert captured[0]["project_dir_existed"] is False
    extraction_snapshot_path: Path = captured[0]["path"]
    # staging ディレクトリは project_dir の親配下の sibling であり、
    # project_dir 自身の内側ではない。
    assert extraction_snapshot_path.parent.parent.parent == project_dir.parent.resolve()
    assert not str(extraction_snapshot_path).startswith(f"{project_dir.resolve()}/")

    # atomic rename 後: 最終的な --project-dir 配下に同じ音声がコピーされて
    # いる（staging から中身ごと移動済み）。
    final_source = project_dir / "source" / SAMPLE_AUDIO.name
    assert final_source.is_file()
    assert final_source.read_bytes() == SAMPLE_AUDIO.read_bytes()
    # staging 自体は残らない（rename 済み）。
    assert _leftover_staging_dirs(tmp_path, project_dir.name) == []


def test_recast_init_extraction_failure_leaves_no_partial_project_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2（#210 round 2 指摘3）: 抽出失敗（staging 構築の途中失敗）は
    staging を後始末するだけで済み、`--project-dir` には何も残さない（旧
    実装の「非空のため再実行不能な部分初期化」を構造的に排除したことの
    機械 assert）。"""
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
    assert _leftover_staging_dirs(tmp_path, project_dir_fail.name) == []

    # 失敗直後に同じ --project-dir で再実行できる（部分初期化が残っていない
    # ことの実運用相当の証明）。
    monkeypatch.setattr("svp_rpe.rpe.extractor.extract_rpe_from_file", _stub_bundle)
    retry_result = runner.invoke(
        app,
        [
            "recast", "init", str(SAMPLE_AUDIO),
            "--project-dir", str(project_dir_fail),
            "--no-interactive",
        ],
    )
    assert retry_result.exit_code == 0, retry_result.output
    assert (project_dir_fail / "project.yaml").is_file()


def test_recast_init_interactive_abort_leaves_no_partial_project_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2（#210 round 2 指摘3）: 対話式プロンプトの中断（EOF /
    `click.exceptions.Abort`）も staging の `finally` が一律に後始末する —
    抽出失敗と同じ安全性を持つことを検証する（「対話式の入力途中中断も
    同様に安全なこと」）。"""
    project_dir = tmp_path / "proj_abort"
    monkeypatch.setattr("svp_rpe.rpe.extractor.extract_rpe_from_file", _stub_bundle)

    result = runner.invoke(
        app,
        [
            "recast", "init", str(SAMPLE_AUDIO),
            "--project-dir", str(project_dir),
            "--interactive",
        ],
        input="",  # 即 EOF -> click.exceptions.Abort
    )
    assert result.exit_code != 0

    assert not project_dir.exists()
    assert _leftover_staging_dirs(tmp_path, project_dir.name) == []


def test_recast_init_preexisting_empty_project_dir_survives_failed_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """既存の空 `--project-dir`（init 前の許容ケース）は、staging 構築中の
    失敗があっても消えたり書き換わったりしない — atomic rename の直前まで
    `--project-dir` 自体には一切触れない設計であることの機械 assert。"""
    project_dir = tmp_path / "proj_preexisting"
    project_dir.mkdir()

    def _stub_extract_fails(path: str) -> RPEBundle:
        raise ValueError("synthetic extraction failure (test double)")

    monkeypatch.setattr("svp_rpe.rpe.extractor.extract_rpe_from_file", _stub_extract_fails)
    fail_result = runner.invoke(
        app,
        [
            "recast", "init", str(SAMPLE_AUDIO),
            "--project-dir", str(project_dir),
            "--no-interactive",
        ],
    )
    assert fail_result.exit_code == 1
    assert project_dir.is_dir()
    assert list(project_dir.iterdir()) == []
    assert _leftover_staging_dirs(tmp_path, project_dir.name) == []


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

    # Codex P2（#210 round 4 指摘6）: identity.yaml の各 pin（source + 全
    # anchor）が disk 上の実 bytes の sha256 と一致することを明示的にループで
    # 検証する（`load_identity_manifest` 自体が同じ照合で fail-closed する
    # ため上の呼び出しが既に間接的に保証しているが、pin と実 bytes が
    # 同一の bytes 経路（`write_bytes` 1 回 encode）から生まれていることを
    # 直接 assert する — text-mode 改行変換のような環境依存の乖離を pin の
    # 数値そのもので検出する回帰テスト）。
    assert (
        hashlib.sha256((project_dir / manifest.source.locator).read_bytes()).hexdigest()
        == manifest.source.sha256
    )
    for anchor in manifest.anchors:
        artifact_bytes = (project_dir / anchor.artifact).read_bytes()
        assert hashlib.sha256(artifact_bytes).hexdigest() == anchor.sha256, anchor.id

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
