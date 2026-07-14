"""AR1-3: EDM / Jazz deterministic variants fixture の回帰テスト。

`examples/arrangement/midnight_signal/` に committed された Base Score + 2
ArrangementSpec (edm / jazz) + expected/ 成果物一式が、実 CLI (`svprpe arrange`)
の再実行と byte-identical であることを固定する。1 つの Base Score から異なる
ArrangementSpec で異なる Derived Score / field-level diff / provenance bundle を
決定論的に生成できることの実証（説明力のデモ）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from svp_rpe.cli import app
from svp_rpe.compose import load_composition_score

FIXTURE_DIR = Path("examples/arrangement/midnight_signal")
BASE_SCORE = FIXTURE_DIR / "composition_score.yaml"
SOURCE_COMPOSITION_SCORE = Path("examples/composition/midnight_signal/composition_score.yaml")

VARIANTS = ("edm", "jazz")

EXPECTED_CHANGE_PATHS = {
    "edm": {
        "semantic.grv.primary",
        "semantic.grv.secondary",
        "semantic.delta_e.overall",
        "physical.bpm",
        "physical.brightness",
    },
    "jazz": {
        "semantic.grv.primary",
        "semantic.grv.secondary",
        "semantic.delta_e.overall",
        "physical.bpm",
        "physical.valley_depth_target",
        "physical.stereo_width",
    },
}

# 安定ソート順 (resolver.py CANONICAL_PATHS 順) を pin する。
EXPECTED_CHANGE_PATH_ORDER = {
    "edm": [
        "semantic.grv.primary",
        "semantic.grv.secondary",
        "semantic.delta_e.overall",
        "physical.bpm",
        "physical.brightness",
    ],
    "jazz": [
        "semantic.grv.primary",
        "semantic.grv.secondary",
        "semantic.delta_e.overall",
        "physical.bpm",
        "physical.valley_depth_target",
        "physical.stereo_width",
    ],
}

ARTIFACT_FILENAMES = ("derived_score.yaml", "arrangement_bundle.json", "arrangement_diff.json")


def _expected_dir(variant: str) -> Path:
    return FIXTURE_DIR / "expected" / variant


def _spec_path(variant: str) -> Path:
    return FIXTURE_DIR / f"{variant}.arrangement.yaml"


def _load_bundle(variant: str) -> dict:
    return json.loads((_expected_dir(variant) / "arrangement_bundle.json").read_text(encoding="utf-8"))


# --- 1. コピー同期 pin ------------------------------------------------------------


def test_arrangement_fixture_base_score_is_byte_identical_to_composition_example() -> None:
    """examples/arrangement/.../composition_score.yaml は composition example の
    byte-identical コピーである（config 二重コピー同期テストと同型のドリフト防止）。"""
    assert BASE_SCORE.read_bytes() == SOURCE_COMPOSITION_SCORE.read_bytes()


# --- 2. 再生成 byte 一致 -----------------------------------------------------------


@pytest.mark.parametrize("variant", VARIANTS)
def test_regeneration_is_byte_identical_to_committed_expected(
    variant: str, tmp_path: Path
) -> None:
    output_dir = tmp_path / variant
    result = CliRunner().invoke(
        app,
        ["arrange", str(BASE_SCORE), str(_spec_path(variant)), "--output-dir", str(output_dir)],
    )
    assert result.exit_code == 0, result.output

    expected_dir = _expected_dir(variant)
    for filename in ARTIFACT_FILENAMES:
        regenerated = (output_dir / filename).read_bytes()
        committed = (expected_dir / filename).read_bytes()
        assert regenerated == committed, f"{variant}/{filename} drifted from committed expected"


# --- 3. Base hash 共有 -------------------------------------------------------------


def test_both_bundles_share_source_score_sha256_matching_independent_recomputation() -> None:
    independent_sha256 = hashlib.sha256(BASE_SCORE.read_bytes()).hexdigest()

    edm_bundle = _load_bundle("edm")
    jazz_bundle = _load_bundle("jazz")

    assert edm_bundle["source_score"]["sha256"] == independent_sha256
    assert jazz_bundle["source_score"]["sha256"] == independent_sha256
    assert edm_bundle["source_score"]["sha256"] == jazz_bundle["source_score"]["sha256"]
    assert edm_bundle["source_score"]["path"] == str(BASE_SCORE)
    assert jazz_bundle["source_score"]["path"] == str(BASE_SCORE)


# --- 4. field-level diff の説明力 --------------------------------------------------


@pytest.mark.parametrize("variant", VARIANTS)
def test_bundle_changes_path_set_and_order_match_variant_contract(variant: str) -> None:
    bundle = _load_bundle(variant)
    paths = [change["path"] for change in bundle["changes"]]

    assert set(paths) == EXPECTED_CHANGE_PATHS[variant]
    assert paths == EXPECTED_CHANGE_PATH_ORDER[variant]


def test_edm_and_jazz_change_path_sets_differ() -> None:
    """変更フィールド集合が EDM と Jazz で異なる（diff の説明力のデモ）。"""
    assert EXPECTED_CHANGE_PATHS["edm"] != EXPECTED_CHANGE_PATHS["jazz"]


# --- 5. derived の妥当性 -----------------------------------------------------------


@pytest.mark.parametrize("variant", VARIANTS)
def test_expected_derived_score_is_loadable_and_preserves_identity_anchor(variant: str) -> None:
    base = load_composition_score(BASE_SCORE)
    derived = load_composition_score(_expected_dir(variant) / "derived_score.yaml")

    # identity anchor (preservation-only hard): 同じ曲のまま編曲されている。
    assert derived.physical.key == base.physical.key
    assert derived.semantic.core == base.semantic.core

    # 未 override 欄はそのまま保存される。
    assert derived.meta == base.meta
    assert derived.structure == base.structure
    assert derived.control_profile == base.control_profile
    assert derived.rendering == base.rendering
