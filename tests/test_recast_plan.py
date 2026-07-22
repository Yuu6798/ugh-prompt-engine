"""`recast/plan.py` の build_recast_plan テスト（PR2）。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from svp_rpe.recast import RecastError, load_recast_project
from svp_rpe.recast.loader import LoadedRecastProject, load_mode_overrides
from svp_rpe.recast.models import ModeOverridesConfig
from svp_rpe.recast.plan import (
    RecastPlanResult,
    build_recast_plan,
    mode_support_for_path,
    unsupported_changed_field_reasons,
)
from svp_rpe.recast.state import load_recast_state, record_state

DEMO_PROJECT = Path("examples/recast/demo_project")
EXPECTED_PLAN = DEMO_PROJECT / "expected" / "recast_plan_edm_suno.json"


def _persist_state(
    loaded: LoadedRecastProject, variant: str, backend: str, result: RecastPlanResult
) -> None:
    """`svprpe recast plan` CLI が plan JSON publish 成功後に行う状態記録を模倣する
    （Codex P2 #207 で `build_recast_plan` からこの副作用を除去し CLI 側の責務に
    移した — このヘルパーはユニットテストから同じ手順を再現する）。"""
    if result.plan.blocked is not None:
        note = "; ".join(result.plan.blocked.reasons)
    else:
        reasons = unsupported_changed_field_reasons(
            result.plan.changed_fields, result.plan.invocation_mode
        )
        note = "; ".join(reasons) if reasons else None
    record_state(
        loaded.project_dir,
        variant,
        backend,
        result.plan.state_reached,
        note,
        inputs_digest=result.inputs_digest,
    )


def _copy_demo_project(tmp_path: Path) -> Path:
    """demo_project の入力一式（project/score/identity/arrangements）を
    tmp_path 配下へコピーする（`expected/` snapshot は意図的に除外 —
    テストが自由に破壊改変できる作業コピーに、比較専用の committed 期待値を
    混ぜない）。project.yaml への path を返す。"""
    dest = tmp_path / "demo_project"
    dest.mkdir()
    shutil.copy(DEMO_PROJECT / "project.yaml", dest / "project.yaml")
    shutil.copy(DEMO_PROJECT / "composition_score.yaml", dest / "composition_score.yaml")
    shutil.copy(DEMO_PROJECT / "identity.yaml", dest / "identity.yaml")
    shutil.copytree(DEMO_PROJECT / "identity", dest / "identity")
    shutil.copytree(DEMO_PROJECT / "arrangements", dest / "arrangements")
    return dest / "project.yaml"


# --- happy path --------------------------------------------------------------


def test_demo_project_reaches_verified(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    loaded = load_recast_project(project_path)

    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.blocked is None
    assert result.plan.state_reached == "verified"
    assert {a.anchor_id for a in result.plan.anchors} == {"lyrics", "melody", "harmony"}
    assert result.plan.recommendation == "run へ進行可。"

    _persist_state(loaded, "edm", "suno", result)
    state_file = load_recast_state(loaded.project_dir)
    assert state_file.runs["edm@suno"].state == "verified"
    assert state_file.runs["edm@suno"].inputs_digest == result.inputs_digest


def test_unknown_variant_raises_recast_error(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    loaded = load_recast_project(project_path)

    with pytest.raises(RecastError):
        build_recast_plan(loaded, variant="does-not-exist", backend="suno")


def test_unknown_backend_raises_recast_error(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    loaded = load_recast_project(project_path)

    with pytest.raises(RecastError):
        build_recast_plan(loaded, variant="edm", backend="does-not-exist")


# --- byte-pin snapshot ---------------------------------------------------------


def test_demo_project_plan_matches_committed_snapshot_byte_for_byte(tmp_path: Path) -> None:
    """`svprpe recast plan` が publish する canonical JSON（sort_keys+indent=2+
    末尾改行）は `build_recast_plan` が返す `plan` を同じ規約で直列化したものと
    等しい — CLI を介さずここで直接そのバイト列を再現し、committed
    `expected/recast_plan_edm_suno.json` と一致することを検証する。"""
    project_path = _copy_demo_project(tmp_path)
    loaded = load_recast_project(project_path)

    result = build_recast_plan(loaded, variant="edm", backend="suno")
    canonical = (
        json.dumps(
            result.plan.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )

    expected = EXPECTED_PLAN.read_text(encoding="utf-8")
    assert canonical == expected


# --- scenario (a): blocked_authoring via unresolved TODO sentinel --------------


def test_unresolved_author_field_blocks_authoring(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    score_path = project_path.parent / "composition_score.yaml"
    original = score_path.read_text(encoding="utf-8")
    mutated = original.replace(
        'core: "introspective night drive"',
        'core: "TODO(transcribe): author input required"',
    )
    assert mutated != original  # sanity: the replacement actually matched
    score_path.write_text(mutated, encoding="utf-8")

    loaded = load_recast_project(project_path)
    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.state_reached == "blocked_authoring"
    assert result.plan.blocked is not None
    assert result.plan.blocked.state == "blocked_authoring"
    assert any("semantic.core" in reason for reason in result.plan.blocked.reasons)

    _persist_state(loaded, "edm", "suno", result)
    state_file = load_recast_state(loaded.project_dir)
    assert state_file.runs["edm@suno"].state == "blocked_authoring"


def test_unresolved_structure_role_blocks_authoring(tmp_path: Path) -> None:
    """`structure[].role` は semantic 層の外にある author 欄 — 全走査ゲートが
    semantic 限定の旧実装を置換したことを検証する回帰テスト（Codex P2 #207）。"""
    project_path = _copy_demo_project(tmp_path)
    score_path = project_path.parent / "composition_score.yaml"
    original = score_path.read_text(encoding="utf-8")
    mutated = original.replace(
        'role: "establish loneliness"',
        'role: "TODO(transcribe): author input required"',
    )
    assert mutated != original  # sanity: the replacement actually matched
    score_path.write_text(mutated, encoding="utf-8")

    loaded = load_recast_project(project_path)
    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.state_reached == "blocked_authoring"
    assert result.plan.blocked is not None
    assert result.plan.blocked.state == "blocked_authoring"
    assert any("structure[0].role" in reason for reason in result.plan.blocked.reasons)

    _persist_state(loaded, "edm", "suno", result)
    state_file = load_recast_state(loaded.project_dir)
    assert state_file.runs["edm@suno"].state == "blocked_authoring"


# --- scenario (b): blocked_capability via strict capability_mode ---------------


def test_strict_capability_mode_blocks_on_hard_unsupported_anchor(tmp_path: Path) -> None:
    """demo_project の arrangement は melody / harmony を hard preservation で
    宣言している。melody の artifact_type (note_events_json) は suno の
    InputCapabilityProfile で symbolic_melody=unsupported、harmony
    (chord_sequence_json) は ARTIFACT_TYPE_CHANNEL 未対応で delivery=unknown。
    capability_mode: strict にすると `build_performance_package` は両方を
    hard anchor の strict failure として `PackageCompilationError` を送出する
    （`package.py` の `strict_failures` 収集 — `mode=="hard" and delivery_status
    in ("unsupported", "unknown")`）。"""
    project_path = _copy_demo_project(tmp_path)
    original = project_path.read_text(encoding="utf-8")
    mutated = original.replace("capability_mode: advisory", "capability_mode: strict")
    assert mutated != original
    project_path.write_text(mutated, encoding="utf-8")

    loaded = load_recast_project(project_path)
    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.state_reached == "blocked_capability"
    assert result.plan.blocked is not None
    assert result.plan.blocked.state == "blocked_capability"
    reasons_text = " ".join(result.plan.blocked.reasons)
    assert "melody" in reasons_text
    assert "strict capability check failed" in reasons_text

    _persist_state(loaded, "edm", "suno", result)
    state_file = load_recast_state(loaded.project_dir)
    assert state_file.runs["edm@suno"].state == "blocked_capability"


# --- scenario (c): blocked_verification via tampered anchor artifact -----------


def test_tampered_anchor_artifact_blocks_verification(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    lyrics_path = project_path.parent / "identity" / "lyrics.txt"
    original_bytes = lyrics_path.read_bytes()
    lyrics_path.write_bytes(original_bytes + b"X")  # 1 byte tamper, hash now stale

    loaded = load_recast_project(project_path)
    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.state_reached == "blocked_verification"
    assert result.plan.blocked is not None
    assert result.plan.blocked.state == "blocked_verification"
    assert any("sha256" in reason for reason in result.plan.blocked.reasons)

    _persist_state(loaded, "edm", "suno", result)
    state_file = load_recast_state(loaded.project_dir)
    assert state_file.runs["edm@suno"].state == "blocked_verification"


# --- unsupported changed field: strict/advisory ゲート (Codex P2 second round #207) --


def _cover_key_override_project(tmp_path: Path, *, capability_mode: str) -> Path:
    """demo project を変形する: backend の invocation_mode を cover にし、
    physical.key を override する（cover の physical.key は
    `config/mode_overrides/suno.yaml` で unsupported と実測済み）。identity_anchors
    は melody/harmony の hard 宣言に由来する既存の strict failure（本ゲートとは
    無関係な別経路 — `test_strict_capability_mode_blocks_on_hard_unsupported_anchor`
    参照）を避けるため free に緩め、changed_fields の unsupported ゲートだけを単離する。"""
    project_path = _copy_demo_project(tmp_path)

    project_text = project_path.read_text(encoding="utf-8")
    mutated_project = project_text.replace("invocation_mode: prompt_only", "invocation_mode: cover")
    assert mutated_project != project_text
    mutated_project = mutated_project.replace(
        "capability_mode: advisory", f"capability_mode: {capability_mode}"
    )
    project_path.write_text(mutated_project, encoding="utf-8")

    arrangement_path = project_path.parent / "arrangements" / "edm.yaml"
    arrangement_text = arrangement_path.read_text(encoding="utf-8")
    mutated_arrangement = arrangement_text.replace(
        '  physical:\n    bpm: 132\n    brightness: "bright"\n',
        '  physical:\n    bpm: 132\n    brightness: "bright"\n    key: "A minor"\n',
    )
    assert mutated_arrangement != arrangement_text
    mutated_arrangement = mutated_arrangement.replace(
        "    physical.key: hard", "    physical.key: free"
    )
    mutated_arrangement = mutated_arrangement.replace("mode: hard", "mode: free")
    arrangement_path.write_text(mutated_arrangement, encoding="utf-8")

    return project_path


def test_unsupported_changed_field_blocks_capability_in_strict_mode(tmp_path: Path) -> None:
    project_path = _cover_key_override_project(tmp_path, capability_mode="strict")
    loaded = load_recast_project(project_path)

    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.state_reached == "blocked_capability"
    assert result.plan.blocked is not None
    assert result.plan.blocked.state == "blocked_capability"
    reasons_text = " ".join(result.plan.blocked.reasons)
    assert "physical.key" in reasons_text
    assert "invocation_mode cover" in reasons_text
    assert "unsupported" in reasons_text

    _persist_state(loaded, "edm", "suno", result)
    state_file = load_recast_state(loaded.project_dir)
    assert state_file.runs["edm@suno"].state == "blocked_capability"


def test_unsupported_changed_field_warns_but_verifies_in_advisory_mode(tmp_path: Path) -> None:
    project_path = _cover_key_override_project(tmp_path, capability_mode="advisory")
    loaded = load_recast_project(project_path)

    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.blocked is None
    assert result.plan.state_reached == "verified"
    warnings_text = " ".join(result.plan.warnings)
    assert "physical.key" in warnings_text
    assert "invocation_mode cover" in warnings_text
    assert "unsupported" in warnings_text
    assert "届きません" in result.plan.recommendation

    _persist_state(loaded, "edm", "suno", result)
    state_file = load_recast_state(loaded.project_dir)
    assert state_file.runs["edm@suno"].state == "verified"
    assert state_file.runs["edm@suno"].note is not None
    assert "physical.key" in state_file.runs["edm@suno"].note


# --- mode_overrides: ★invocation_mode 軸 ---------------------------------------


def _suno_mode_overrides() -> ModeOverridesConfig:
    return load_mode_overrides(Path("config/mode_overrides/suno.yaml"))


def test_mode_support_differs_between_cover_and_prompt_only() -> None:
    config = _suno_mode_overrides()

    cover_support = mode_support_for_path("physical.time_signature", "cover", config)
    prompt_only_support = mode_support_for_path(
        "physical.time_signature", "prompt_only", config
    )

    assert cover_support == "unsupported"
    assert prompt_only_support == "experimental"
    assert cover_support != prompt_only_support


def test_mode_support_falls_back_to_unknown_for_undeclared_path() -> None:
    config = _suno_mode_overrides()

    assert mode_support_for_path("semantic.core", "cover", config) == "unknown"


def test_mode_support_falls_back_to_unknown_when_no_config() -> None:
    assert mode_support_for_path("physical.bpm", "cover", None) == "unknown"
