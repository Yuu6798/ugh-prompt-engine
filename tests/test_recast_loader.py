"""`load_recast_project` の参照解決テスト（PR1）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from svp_rpe.recast import RecastReferenceError, load_recast_project

FIXTURE_PATH = Path("examples/recast/demo_project/project.yaml")


PROJECT_YAML_TEMPLATE = """\
schema_version: "recast-project/0.1"

project:
  id: "demo-project"
  builds_root: '{builds_root}'

work:
  score: '{score}'
  identity_manifest: '{identity_manifest}'

variants:
  edm:
    arrangement: '{arrangement}'

backends:
  suno:
    capability_profile: '{capability_profile}'
    invocation: manual
    invocation_mode: prompt_only

policy:
  capability_mode: advisory

observation:
  enabled: false
  anchors: []
"""


def _write_project(
    tmp_path: Path,
    *,
    score: str = "composition_score.yaml",
    identity_manifest: str = "identity.yaml",
    arrangement: str = "arrangements/edm.yaml",
    capability_profile: str = "suno",
    builds_root: str = "builds",
) -> Path:
    project_path = tmp_path / "project.yaml"
    project_path.write_text(
        PROJECT_YAML_TEMPLATE.format(
            score=score,
            identity_manifest=identity_manifest,
            arrangement=arrangement,
            capability_profile=capability_profile,
            builds_root=builds_root,
        ),
        encoding="utf-8",
    )
    return project_path


def _write_minimal_referents(tmp_path: Path) -> None:
    (tmp_path / "composition_score.yaml").write_text("score: {}\n", encoding="utf-8")
    (tmp_path / "identity.yaml").write_text("identity: {}\n", encoding="utf-8")
    (tmp_path / "arrangements").mkdir()
    (tmp_path / "arrangements" / "edm.yaml").write_text("arrangement: {}\n", encoding="utf-8")


# --- happy path: committed fixture ----------------------------------------------


def test_committed_fixture_loads_and_resolves_paths() -> None:
    loaded = load_recast_project(FIXTURE_PATH)

    project_dir = FIXTURE_PATH.resolve().parent
    assert loaded.project_dir == project_dir
    assert loaded.score_path == project_dir / "composition_score.yaml"
    assert loaded.identity_manifest_path == project_dir / "identity.yaml"
    assert loaded.arrangement_paths == {"edm": project_dir / "arrangements" / "edm.yaml"}
    assert loaded.mode_override_paths == {}
    assert loaded.builds_root == project_dir / "builds"
    assert loaded.sha256  # non-empty hex digest
    assert len(loaded.sha256) == 64

    # capability_profile "suno" は名前参照として config/capability_profiles/suno.yaml
    # (repo checkout) へ解決される。
    suno_path = loaded.capability_profile_paths["suno"]
    assert suno_path.is_file()
    assert suno_path.parts[-3:] == ("config", "capability_profiles", "suno.yaml")

    for path in (loaded.score_path, loaded.identity_manifest_path):
        assert path.is_file()
    for path in loaded.arrangement_paths.values():
        assert path.is_file()


# --- reference not found --------------------------------------------------------


def test_missing_score_reference_raises(tmp_path: Path) -> None:
    project_path = _write_project(tmp_path)
    # composition_score.yaml を作らない: identity/arrangement のみ用意
    (tmp_path / "identity.yaml").write_text("identity: {}\n", encoding="utf-8")
    (tmp_path / "arrangements").mkdir()
    (tmp_path / "arrangements" / "edm.yaml").write_text("arrangement: {}\n", encoding="utf-8")

    with pytest.raises(RecastReferenceError):
        load_recast_project(project_path)


def test_project_yaml_alone_in_tmp_path_raises(tmp_path: Path) -> None:
    project_path = _write_project(tmp_path)

    with pytest.raises(RecastReferenceError):
        load_recast_project(project_path)


# --- absolute path / traversal rejection ----------------------------------------


def test_absolute_path_reference_raises(tmp_path: Path) -> None:
    _write_minimal_referents(tmp_path)
    project_path = _write_project(tmp_path, score="/etc/passwd")

    with pytest.raises(RecastReferenceError):
        load_recast_project(project_path)


def test_posix_traversal_reference_raises(tmp_path: Path) -> None:
    _write_minimal_referents(tmp_path)
    project_path = _write_project(tmp_path, score="../composition_score.yaml")

    with pytest.raises(RecastReferenceError):
        load_recast_project(project_path)


def test_windows_style_traversal_reference_raises(tmp_path: Path) -> None:
    _write_minimal_referents(tmp_path)
    project_path = _write_project(tmp_path, score="..\\composition_score.yaml")

    with pytest.raises(RecastReferenceError):
        load_recast_project(project_path)


# --- builds_root existing-file rejection ----------------------------------------


def test_builds_root_pointing_at_existing_file_raises(tmp_path: Path) -> None:
    _write_minimal_referents(tmp_path)
    (tmp_path / "builds").write_text("not a directory\n", encoding="utf-8")
    project_path = _write_project(tmp_path)

    with pytest.raises(RecastReferenceError):
        load_recast_project(project_path)


def test_builds_root_nonexistent_directory_is_allowed(tmp_path: Path) -> None:
    _write_minimal_referents(tmp_path)
    project_path = _write_project(tmp_path, builds_root="builds/does/not/exist/yet")

    loaded = load_recast_project(project_path)

    assert loaded.builds_root == tmp_path / "builds" / "does" / "not" / "exist" / "yet"
    assert not loaded.builds_root.exists()


# --- capability_profile name resolution -----------------------------------------


def test_capability_profile_name_reference_resolves(tmp_path: Path) -> None:
    _write_minimal_referents(tmp_path)
    project_path = _write_project(tmp_path, capability_profile="suno")

    loaded = load_recast_project(project_path)

    assert loaded.capability_profile_paths["suno"].is_file()


def test_capability_profile_unknown_name_raises(tmp_path: Path) -> None:
    _write_minimal_referents(tmp_path)
    project_path = _write_project(tmp_path, capability_profile="not-a-real-generator")

    with pytest.raises(RecastReferenceError):
        load_recast_project(project_path)
