"""tests/test_collect_ar4_observation.py — scripts/collect_ar4_observation.py smoke tests.

`collect_ar4_observation.py` performs real MusicGen inference in its `generate`
subcommand (torch required, non-deterministic model load), so this test suite
stays confined to the pure-logic manifest-building helpers — the same split
`test_musicgen_runbook.py` uses for `collect_musicgen_takes.py`. In particular
it locks in the `performance_package` provenance shape (Codex P2 review #191,
discussion_r3610116228): the generator must never serialize a machine-specific
absolute path, and its default output for the AR4 midnight_signal fixture must
match the committed `ar4_takes_manifest.json` byte-for-byte in structure. It
also locks in the geometry-independent composite-pin verification for
``derived_score`` / ``preservation_contract`` added in Codex 4R P2 review #191
(discussion_r3610170990): a `--score` or `--arrangement` mistyped to another
*existing* repo YAML must be caught by pin mismatch, not waved through by an
existence-only check.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from scripts.collect_ar4_observation import (
    DEFAULT_ARRANGEMENT,
    DEFAULT_CAPABILITY_PROFILE,
    DEFAULT_IDENTITY_MANIFEST,
    DEFAULT_SCORE,
    ROOT,
    build_package_provenance,
    build_takes_manifest,
)

SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from svp_rpe.arrange.contract import build_preservation_contract  # noqa: E402
from svp_rpe.arrange.identity import IdentityManifest  # noqa: E402
from svp_rpe.arrange.models import ArrangementSpec  # noqa: E402
from svp_rpe.arrange.package import (  # noqa: E402
    compute_derived_score_sha256,
    compute_preservation_contract_sha256,
)
from svp_rpe.arrange.resolver import resolve_arrangement  # noqa: E402
from svp_rpe.compose.models import CompositionScore  # noqa: E402

FIXTURE_MANIFEST_PATH = Path(
    "examples/arrangement/midnight_signal/observed/musicgen/ar4_takes_manifest.json"
)

# 誤指定シナリオ用: リポジトリに実在するが DEFAULT_SCORE / DEFAULT_ARRANGEMENT とは
# 別内容の YAML（存在はするが誤った入力を模す）。
WRONG_SCORE = ROOT / "examples/control/k2_suno_segments/structure4_score_high.yaml"
WRONG_ARRANGEMENT = ROOT / "examples/arrangement/midnight_signal/edm.identity.arrangement.yaml"


def _load_fixture_manifest() -> dict[str, Any]:
    return json.loads(FIXTURE_MANIFEST_PATH.read_text(encoding="utf-8"))


def _real_recipe_package_data() -> dict[str, Any]:
    """DEFAULT_SCORE/DEFAULT_IDENTITY_MANIFEST/DEFAULT_ARRANGEMENT/
    DEFAULT_CAPABILITY_PROFILE から実際に compile した場合に
    `performance_package.json` が持つはずの 4 pin を、production の pin 計算
    経路（`resolve_arrangement` / `compute_derived_score_sha256` /
    `build_preservation_contract` / `compute_preservation_contract_sha256`）を
    直接呼んで独立に再現する。`build_package_provenance` の内部 recompute
    ヘルパーとは別経路で期待値を作ることで、テストが検証対象と同じ計算を
    無条件に信頼してしまう（tautology）のを避ける。
    """
    score_bytes = DEFAULT_SCORE.read_bytes()
    manifest_bytes = DEFAULT_IDENTITY_MANIFEST.read_bytes()
    arrangement_bytes = DEFAULT_ARRANGEMENT.read_bytes()
    capability_bytes = DEFAULT_CAPABILITY_PROFILE.read_bytes()

    source = CompositionScore.model_validate(yaml.safe_load(score_bytes))
    manifest = IdentityManifest.model_validate(yaml.safe_load(manifest_bytes))
    spec = ArrangementSpec.model_validate(yaml.safe_load(arrangement_bytes))

    resolution = resolve_arrangement(source, spec)
    derived_score_sha256 = compute_derived_score_sha256(resolution.derived_score)

    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    spec_sha256 = hashlib.sha256(arrangement_bytes).hexdigest()
    contract = build_preservation_contract(
        manifest, spec, manifest_sha256=manifest_sha256, spec_sha256=spec_sha256
    )
    contract_sha256 = compute_preservation_contract_sha256(contract)

    return {
        "inputs": {
            "identity_manifest": {"sha256": manifest_sha256},
            "capability_profile": {
                "sha256": hashlib.sha256(capability_bytes).hexdigest()
            },
            "derived_score": {"sha256": derived_score_sha256},
            "preservation_contract": {"sha256": contract_sha256},
        }
    }


_DEFAULT_RECIPE_PACKAGE_DATA = _real_recipe_package_data()


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
    """4 入力が全て repo 相対に解決でき、かつ package の pin と幾何非依存の
    合成 pin（derived_score / preservation_contract）が一致すれば、入力パス +
    コンパイルコマンドの構造化レシピ (`build_recipe`) を記録する。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    package_sha256 = "b" * 64
    provenance = build_package_provenance(
        scratch_package,
        package_sha256,
        _DEFAULT_RECIPE_PACKAGE_DATA,
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


def test_build_package_provenance_rejects_nonexistent_repo_relative_recipe_input() -> None:
    """レシピ入力の1つが repo 相対に解決できても実在しない場合（誤指定パス）は
    偽の `build_recipe` を出さず sha256-only フォールバックする（Codex 3R P2
    review #191, discussion_r3610153978）。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    bogus_score = ROOT / "examples/arrangement/midnight_signal/does_not_exist.yaml"
    assert not bogus_score.is_file()  # 前提: このパスは本当に存在しない
    provenance = build_package_provenance(
        scratch_package,
        "f" * 64,
        score=bogus_score,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert "build_recipe" not in provenance
    assert "note" in provenance
    assert "does not exist" in provenance["note"]
    assert "score:" in provenance["note"]


def test_build_package_provenance_rejects_pin_mismatch() -> None:
    """レシピ入力が実在しても、package の pin と bytes が食い違う場合（改ざん/
    差し替え）は build_recipe を出さず sha256-only フォールバックする。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    real_capability_sha256 = hashlib.sha256(
        DEFAULT_CAPABILITY_PROFILE.read_bytes()
    ).hexdigest()
    package_data = {
        "inputs": {
            # identity_manifest の pin をわざと不一致にする（tampered manifest 相当）
            "identity_manifest": {"sha256": "0" * 64},
            "capability_profile": {"sha256": real_capability_sha256},
        }
    }
    provenance = build_package_provenance(
        scratch_package,
        "1" + "a" * 63,
        package_data,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert "build_recipe" not in provenance
    assert "note" in provenance
    assert "identity_manifest:" in provenance["note"]
    assert "does not match package-pinned sha256" in provenance["note"]


def test_build_package_provenance_emits_recipe_when_pins_match() -> None:
    """package の pin（identity_manifest / capability_profile の raw bytes pin
    と、derived_score / preservation_contract の幾何非依存合成 pin）が実ファイル
    /実計算と一致する場合（正しい既定入力）は、従来どおり build_recipe を
    記録する — pin 突合の追加が正当な入力を巻き添えにしないことの回帰確認。"""
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    provenance = build_package_provenance(
        scratch_package,
        "2" + "b" * 63,
        _DEFAULT_RECIPE_PACKAGE_DATA,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert "note" not in provenance
    recipe = provenance["build_recipe"]
    assert recipe["inputs"] == {
        "score": "examples/arrangement/midnight_signal/composition_score.yaml",
        "identity_manifest": "examples/arrangement/midnight_signal/identity_manifest.yaml",
        "arrangement": "examples/arrangement/midnight_signal/edm.identity.musicgen.arrangement.yaml",
        "capability_profile": "config/capability_profiles/musicgen.yaml",
    }


def test_build_package_provenance_rejects_mistyped_score_with_existing_yaml() -> None:
    """`--score` が実在するが別内容の YAML（別 work の score）に誤指定された
    場合、existence-only では検出できないが derived_score の合成 pin 不一致で
    検出され、build_recipe を出さず sha256-only フォールバックする（Codex 4R P2
    review #191, discussion_r3610170990）。"""
    assert WRONG_SCORE.is_file()  # 前提: 実在する別 YAML であること
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    provenance = build_package_provenance(
        scratch_package,
        "3" + "c" * 63,
        _DEFAULT_RECIPE_PACKAGE_DATA,
        score=WRONG_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert "build_recipe" not in provenance
    assert "note" in provenance
    assert "derived_score:" in provenance["note"]
    assert "does not match package-pinned inputs.derived_score.sha256" in provenance["note"]


def test_build_package_provenance_rejects_mistyped_arrangement_with_existing_yaml() -> None:
    """`--arrangement` が実在するが別 variant の arrangement spec に誤指定された
    場合、existence-only では検出できないが preservation_contract の合成 pin
    不一致で検出され、build_recipe を出さず sha256-only フォールバックする
    （Codex 4R P2 review #191, discussion_r3610170990）。"""
    assert WRONG_ARRANGEMENT.is_file()  # 前提: 実在する別 YAML であること
    assert WRONG_ARRANGEMENT != DEFAULT_ARRANGEMENT
    scratch_package = Path("/tmp/ar4_scratch_build/performance_package.json")
    provenance = build_package_provenance(
        scratch_package,
        "4" + "d" * 63,
        _DEFAULT_RECIPE_PACKAGE_DATA,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=WRONG_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert "build_recipe" not in provenance
    assert "note" in provenance
    assert "preservation_contract:" in provenance["note"]
    assert (
        "does not match package-pinned inputs.preservation_contract.sha256"
        in provenance["note"]
    )


def test_build_takes_manifest_matches_committed_fixture_with_default_recipe_inputs() -> None:
    """修正後の生成器（既定のレシピ入力 + 実 package pin）が、committed
    `ar4_takes_manifest.json` と構造的に一致する manifest を組み立てることを
    確認する — 完了条件: 生成器の出力形式と fixture の直接一致。

    `package_data` には `_DEFAULT_RECIPE_PACKAGE_DATA`（DEFAULT_* 入力から
    独立に再計算した実 pin）を渡す — Codex 4R P2 review #191 の修正で
    `package_data` なしでは幾何非依存 pin を突合できず build_recipe を
    emit しなくなったため、committed fixture の `build_recipe` を再現するには
    実 pin が必要（design memo point 4）。
    """
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
        package_data=_DEFAULT_RECIPE_PACKAGE_DATA,
        score=DEFAULT_SCORE,
        identity_manifest=DEFAULT_IDENTITY_MANIFEST,
        arrangement=DEFAULT_ARRANGEMENT,
        capability_profile=DEFAULT_CAPABILITY_PROFILE,
    )
    assert manifest == fixture
