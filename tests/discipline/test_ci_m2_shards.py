"""CI の M2 shard 定義が全 shard を fail-closed で起動することを固定する。"""

from __future__ import annotations

import re

import pytest
import yaml

from ._helpers import REPO_ROOT


CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _assert_m2_shard_contract(workflow: dict) -> None:
    job = workflow["jobs"]["test-m2-harness"]

    shard_indices = [int(value) for value in job["strategy"]["matrix"]["shard-index"]]
    test_steps = [step for step in job["steps"] if "shard_total=" in step.get("run", "")]
    assert len(test_steps) == 1, "M2 shard_total assignment must exist in exactly one step"

    assignments = re.findall(
        r"^\s*shard_total=(\d+)\s*$",
        test_steps[0]["run"],
        flags=re.MULTILINE,
    )
    assert len(assignments) == 1, "M2 shard_total must be one literal integer assignment"

    shard_total = int(assignments[0])
    assert shard_indices == list(range(shard_total)), (
        "M2 shard-index matrix must cover every index in range(shard_total); "
        f"indices={shard_indices}, shard_total={shard_total}"
    )


def test_m2_shard_matrix_matches_shell_shard_total() -> None:
    """matrix と shell の shard 数がずれてテスト末尾を黙って落とさない。"""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    _assert_m2_shard_contract(workflow)


def test_guard_rejects_missing_final_shard() -> None:
    """matrix だけを縮めた場合に、末尾 shard の未実行を必ず検出する。"""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    workflow["jobs"]["test-m2-harness"]["strategy"]["matrix"]["shard-index"] = [0, 1]

    with pytest.raises(AssertionError, match="must cover every index"):
        _assert_m2_shard_contract(workflow)
