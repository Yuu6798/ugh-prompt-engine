"""tests/test_collect_ar4_observation.py — scripts/collect_ar4_observation.py smoke tests.

`collect_ar4_observation.py` performs real MusicGen inference in its `generate`
subcommand (torch required, non-deterministic model load), so this test suite
stays confined to the pure-logic manifest-building helpers — the same split
`test_musicgen_runbook.py` uses for `collect_musicgen_takes.py`. In particular
it locks in the `performance_package` provenance shape (Codex P2 review #191,
discussion_r3610116228): the generator must never serialize a machine-specific
absolute path, and its default output for the AR4 midnight_signal fixture must
match the committed `ar4_takes_manifest.json` byte-for-byte in structure.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.collect_ar4_observation import (
    DEFAULT_ARRANGEMENT,
    DEFAULT_CAPABILITY_PROFILE,
    DEFAULT_IDENTITY_MANIFEST,
    DEFAULT_SCORE,
    ROOT,
    build_package_provenance,
    build_takes_manifest,
)

FIXTURE_MANIFEST_PATH = Path(
    "examples/arrangement/midnight_signal/observed/musicgen/ar4_takes_manifest.json"
)


def _load_fixture_manifest() -> dict[str, Any]:
    return json.loads(FIXTURE_MANIFEST_PATH.read_text(encoding="utf-8"))


def test_build_package_provenance_never_records_absolute_scratch_path() -> None:
    """scratch ビルドディレクトリ（リポジトリ外）の package path は絶対パスとして
    記録されない — `str(package_path)` の直接埋め込みが禁じ手（Codex P2 #191）。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    provenance = build_package_provenance(
        scratch_package,
        "a" * 64,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    serialized = json.dumps(provenance)
    assert str(scratch_package) not in serialized
    assert "/tmp/" not in serialized


def test_build_package_provenance_scratch_build_records_structured_recipe() -> None:
    """4 入力が全て repo 相対に解決できれば、入力パス + コンパイルコマンドの
    構造化レシピ (`build_recipe`) を記録する。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    package_sha256 = "b" * 64
    provenance = build_package_provenance(
        scratch_package,
        package_sha256,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert provenance["sha256"] == package_sha256
    assert "path" not in provenance  # 旧フィールド名は残らない
    recipe = provenance["build_recipe"]
    assert recipe["inputs"] == {
        "score": "examples/arrangement/midnight_signal/composition_score.yaml",
        "identity_manifest": "examples/arrangement/midnight_signal/identity_manifest.yaml",
        "arrangement": "examples/arrangement/midnight_signal/edm.identity.musicgen.arrangement.yaml",
        "capability_profile": "config/capability_profiles/musicgen.yaml",
    }
    assert recipe["compile_command"] == (
        "svprpe package examples/arrangement/midnight_signal/composition_score.yaml "
        "examples/arrangement/midnight_signal/identity_manifest.yaml "
        "examples/arrangement/midnight_signal/edm.identity.musicgen.arrangement.yaml "
        "--capability-profile config/capability_profiles/musicgen.yaml "
        "--output-dir <output-dir>"
    )
    # レシピの全ての入力パスが実在する repo 相対パスであること（架空パスの捏造禁止）
    for relative_path in recipe["inputs"].values():
        assert (ROOT / relative_path).is_file()


def test_build_package_provenance_repo_relative_package_path() -> None:
    """package_path 自体がリポジトリ内ファイルなら repo 相対パスをそのまま記録する
    （scratch レシピより優先）。"""
    in_repo_package = ROOT / "examples/arrangement/midnight_signal/composition_score.yaml"
    provenance = build_package_provenance(in_repo_package, "c" * 64)
    assert provenance == {
        "sha256": "c" * 64,
        "repo_relative_path": "examples/arrangement/midnight_signal/composition_score.yaml",
    }


def test_build_package_provenance_falls_back_to_note_without_recipe_inputs() -> None:
    """scratch build かつレシピ入力が一切与えられない場合は sha256 + note のみ
    （部分的な入力から不完全なレシピを捏造しない）。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    provenance = build_package_provenance(scratch_package, "d" * 64)
    assert provenance["sha256"] == "d" * 64
    assert "build_recipe" not in provenance
    assert "note" in provenance
    assert str(scratch_package) not in provenance["note"]


def test_build_package_provenance_falls_back_to_note_with_partial_recipe_inputs() -> None:
    """レシピ入力が一部のみ与えられた場合も不完全なレシピを組み立てず note へ
    フォールバックする。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    provenance = build_package_provenance(
        scratch_package,
        "e" * 64,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        # arrangement / capability_profile は未指定
    )
    assert "build_recipe" not in provenance
    assert "note" in provenance


def test_build_takes_manifest_matches_committed_fixture_with_default_recipe_inputs() -> None:
    """修正後の生成器（既定のレシピ入力）が、committed
    `ar4_takes_manifest.json` と構造的に一致する manifest を組み立てることを
    確認する — 完了条件: 生成器の出力形式と fixture の直接一致。"""
    fixture = _load_fixture_manifest()
    result = {
        "prompt": fixture["prompt"],
        "model_id": fixture["model_id"],
        "model_revision": fixture["model_revision"],
        "duration_seconds": fixture["duration_seconds"],
        "guidance_scale": fixture["guidance_scale"],
        "samples": fixture["samples"],
    }
    scratch_package = Path("/tmp/ar4_takes/performance_package.json")
    manifest = build_takes_manifest(
        result,
        fixture_id=fixture["fixture_id"],
        package_path=scratch_package,
        package_sha256=fixture["performance_package"]["sha256"],
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert manifest == fixture
