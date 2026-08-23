"""Foundry テストが CI から黙って漏れないことを固定する。"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import tomllib

import pytest
import yaml

from ._helpers import REPO_ROOT


PYPROJECT = REPO_ROOT / "pyproject.toml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
FOUNDRY_ROOT = REPO_ROOT / "voice_genesis" / "foundry"
CORE_MANIFEST = REPO_ROOT / ".github" / "test-paths" / "foundry-core.txt"
OPTIONAL_MANIFEST = REPO_ROOT / ".github" / "test-paths" / "foundry-optional.txt"


def _manifest_entries(path: Path) -> list[PurePosixPath]:
    entries: list[PurePosixPath] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        entry = PurePosixPath(line)
        assert not entry.is_absolute(), f"manifest path must be relative: {line}"
        assert ".." not in entry.parts, f"manifest path must stay in the repo: {line}"
        assert entry.as_posix() == line, f"manifest path must be normalized: {line}"
        entries.append(entry)
    assert entries, f"manifest must not be empty: {path.relative_to(REPO_ROOT)}"
    assert len(entries) == len(set(entries)), f"manifest has duplicate entries: {path}"
    return entries


def _configured_foundry_tests() -> set[PurePosixPath]:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    configured = [
        REPO_ROOT / path
        for path in config["tool"]["pytest"]["ini_options"]["testpaths"]
    ]
    result: set[PurePosixPath] = set()
    for test_file in FOUNDRY_ROOT.rglob("test_*.py"):
        if any(
            test_file == path or (path.is_dir() and test_file.is_relative_to(path))
            for path in configured
        ):
            result.add(PurePosixPath(test_file.relative_to(REPO_ROOT).as_posix()))
    return result


def _all_foundry_tests() -> set[PurePosixPath]:
    return {
        PurePosixPath(path.relative_to(REPO_ROOT).as_posix())
        for path in FOUNDRY_ROOT.rglob("test_*.py")
    }


def _assert_foundry_inventory(
    all_tests: set[PurePosixPath],
    configured: set[PurePosixPath],
    optional: set[PurePosixPath],
) -> None:
    assert optional <= all_tests, f"optional ledger contains missing files: {optional - all_tests}"
    assert configured.isdisjoint(optional), (
        f"configured tests must leave the optional ledger: {configured & optional}"
    )
    assert all_tests == configured | optional, (
        f"unclassified Foundry tests: {all_tests - configured - optional}"
    )


def test_every_foundry_test_is_configured_or_explicitly_optional() -> None:
    """新規 test_*.py を追加しても、未分類のまま CI から落とさない。"""
    _assert_foundry_inventory(
        _all_foundry_tests(),
        _configured_foundry_tests(),
        set(_manifest_entries(OPTIONAL_MANIFEST)),
    )


def test_inventory_guard_rejects_an_unclassified_test() -> None:
    """実収集から漏れた新規ファイルを guard 自身が検出できる。"""
    new_test = PurePosixPath("voice_genesis/foundry/new/tests/test_new.py")
    with pytest.raises(AssertionError, match="unclassified Foundry tests"):
        _assert_foundry_inventory({new_test}, set(), set())


def test_foundry_core_manifest_is_in_pytest_testpaths() -> None:
    """Foundry 専用 job の全ファイルを通常 pytest の対象にも含める。"""
    core = set(_manifest_entries(CORE_MANIFEST))
    configured = _configured_foundry_tests()
    optional = set(_manifest_entries(OPTIONAL_MANIFEST))

    assert core <= configured, f"Foundry core tests missing from testpaths: {core - configured}"
    assert core.isdisjoint(optional), f"core and optional manifests overlap: {core & optional}"


def test_ci_runs_and_excludes_the_same_foundry_core_manifest() -> None:
    """専用 job で実行したファイルを test-rest から同一マニフェストで除外する。"""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    expected_manifest = str(CORE_MANIFEST.relative_to(REPO_ROOT))
    assert workflow["env"]["FOUNDRY_CORE_MANIFEST"] == expected_manifest

    foundry_steps = workflow["jobs"]["test-foundry-core"]["steps"]
    foundry_runs = [step["run"] for step in foundry_steps if "pytest" in step.get("run", "")]
    assert len(foundry_runs) == 1
    assert "$FOUNDRY_CORE_MANIFEST" in foundry_runs[0]
    assert '"${foundry_tests[@]}"' in foundry_runs[0]

    rest_steps = workflow["jobs"]["test-rest"]["steps"]
    rest_runs = [step["run"] for step in rest_steps if "pytest" in step.get("run", "")]
    assert len(rest_runs) == 1
    assert "$FOUNDRY_CORE_MANIFEST" in rest_runs[0]
    # Explicit pytest testpaths are not removed by --ignore.  File node-id
    # prefixes must be deselected or the dedicated 884 tests run twice.
    assert 'deselect_args+=("--deselect=$path")' in rest_runs[0]
    assert '"${deselect_args[@]}"' in rest_runs[0]
