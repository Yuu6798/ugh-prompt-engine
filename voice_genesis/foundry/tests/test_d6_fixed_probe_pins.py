"""test_d6_fixed_probe_pins.py — `debt/d6/s7_fixed_probe_pins.json` の形状テスト
（VG-DEBT-006 Phase A: pinned_regenerable 凍結の machine-independent 部分）。

`debt/DEBT_ADJUDICATION_v1.1.md` §2 裁定2 が定める固定対象一式（WAV sha256 /
PCM sha256 / checkpoint sha256 / ONNX・config・input の sha256 / commit / seed /
execution profile / 再生成コマンド / 再生成後の hash 一致）を、run8-0 の校正
セット・本番セルの双方で記帳した pin ファイルであり、`test_d5_claim_strength.py`
/ `test_run4_provenance_closure.py` の流儀（人手編集の JSON/YAML ファイルは
**構造のみ**を機械強制し、値の内容そのものの妥当性は判読しない）を踏襲する。

検証する不変条件:

- (a) schema / debt_ref / トップレベル必須節の充足
- (b) `{path, sha256}` 参照が実ファイルと一致する（実測 sha256 を再計算して照合。
  repo 内ファイルのみが対象 — Phase B が実走する外部実体には触れない）
- (c) `PENDING_PHASE_B` プレースホルダの構造妥当性（Phase B 完了後に確定形
  （実 sha256 値）へ差し替わっても壊れないよう、両方の形を許容する）
- pyproject.toml の testpaths に本モジュールが登録されている
  （`test_run4_provenance_closure.py` の教訓: 収集しないと『緑なのに何も
  検査していない』状態になる）

Phase A の時点では実測値に依存するフィールド（`reproducibility_reconciliation`
の実測値・`honest_accounting.commit`）は未確定のプレースホルダであることを
確認するに留め、Phase B 完了後の値そのもの（判読・裁定）は検証しない。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List

import pytest

_FOUNDRY = Path(__file__).resolve().parent.parent
_REPO_ROOT = _FOUNDRY.parent.parent
PINS_PATH = _FOUNDRY / "debt" / "d6" / "s7_fixed_probe_pins.json"

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@pytest.fixture(scope="module")
def pins() -> Dict[str, Any]:
    assert PINS_PATH.exists(), f"not found: {PINS_PATH}"
    with PINS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _sha256_file(rel_path: str) -> str:
    path = _REPO_ROOT / rel_path
    assert path.is_file(), f"referenced file does not exist: {rel_path}"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iter_path_sha_refs(node: Any) -> List[Dict[str, Any]]:
    """`node` 以下を再帰的に走査し、`{"path": ..., "sha256": ...}` 形の
    dict をすべて収集する（構造上どこに現れても網羅的に拾う。手で列挙すると
    追加した参照が検査から漏れる事故を防ぐ）。"""
    found: List[Dict[str, Any]] = []
    if isinstance(node, dict):
        if (
            "path" in node
            and "sha256" in node
            and isinstance(node.get("path"), str)
            and isinstance(node.get("sha256"), str)
        ):
            found.append(node)
        for value in node.values():
            found.extend(_iter_path_sha_refs(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_iter_path_sha_refs(item))
    return found


# --- トップレベル形状 ------------------------------------------------------


def test_pins_is_valid_mapping(pins: Dict[str, Any]) -> None:
    assert isinstance(pins, dict)


def test_schema_is_pinned_version(pins: Dict[str, Any]) -> None:
    assert pins["schema"] == "vg-d6-fixed-probe-pins/0.1"


def test_debt_ref_is_vg_debt_006(pins: Dict[str, Any]) -> None:
    assert pins["debt_ref"] == "VG-DEBT-006"


def test_phase_is_a(pins: Dict[str, Any]) -> None:
    assert pins["phase"] == "A"


REQUIRED_TOP_LEVEL_KEYS = {
    "schema", "debt_ref", "phase", "authority", "purpose",
    "wav_not_committed_policy", "production_cells", "calibration_set",
    "common_fixed", "reproducibility_reconciliation", "honest_accounting",
}


def test_top_level_required_keys_present(pins: Dict[str, Any]) -> None:
    missing = REQUIRED_TOP_LEVEL_KEYS - pins.keys()
    assert not missing, f"missing top-level keys: {sorted(missing)}"


# --- 本番セル節 -------------------------------------------------------------


def test_production_cells_covers_360_cells_in_10_groups(pins: Dict[str, Any]) -> None:
    prod = pins["production_cells"]
    assert prod["n_groups"] == 10
    assert prod["n_cells_per_group"] == 36
    assert prod["n_total_cells"] == 360


def test_production_cells_has_probe_0b_groups_for_all_10_groups(pins: Dict[str, Any]) -> None:
    refs = pins["production_cells"]["refs"]["probe_0b_groups_1_0"]
    assert isinstance(refs, list)
    assert len(refs) == 10
    pairs = {(r["generation"], r["speaker"]) for r in refs}
    expected = {
        ("run5", "ritsu"), ("run5", "pjs"), ("run5", "user"),
        ("run6", "ritsu"), ("run6", "pjs"), ("run6", "user"),
        ("run7", "ritsu"), ("run7", "pjs"), ("run7", "user"), ("run7", "amitaro"),
    }
    assert pairs == expected, f"group coverage mismatch: {pairs.symmetric_difference(expected)}"


def test_production_cells_has_cell_definition_and_d4_remeasurement_refs(
    pins: Dict[str, Any],
) -> None:
    refs = pins["production_cells"]["refs"]
    assert "cell_definition_and_checkpoints" in refs
    assert "d4_1_2_remeasurement" in refs
    assert refs["cell_definition_and_checkpoints"]["path"] == (
        "voice_genesis/foundry/results_s7/s7_0b_probe_spec.json"
    )
    assert refs["d4_1_2_remeasurement"]["path"] == (
        "voice_genesis/foundry/debt/d4/d4_results_2026-08-22.json"
    )


def test_pin_field_map_documents_wav_pcm_checkpoint_onnx(pins: Dict[str, Any]) -> None:
    field_map = pins["production_cells"]["pin_field_map"]
    for key in ("wav_sha256", "pcm_sha256", "checkpoint_sha256", "onnx_config_input_sha256"):
        assert isinstance(field_map.get(key), str) and field_map[key].strip(), (
            f"pin_field_map.{key} is missing or empty"
        )


# --- 校正セット節 -----------------------------------------------------------


def test_calibration_set_has_synthetic_and_real_render_refs(pins: Dict[str, Any]) -> None:
    refs = pins["calibration_set"]["refs"]
    synth = refs["synthetic_stimuli"]
    real = refs["real_render_manifest"]
    assert synth["path"] == "voice_genesis/foundry/results_s7/s7_b1_calibration_set.json"
    assert synth["n_synthetic_conditions"] == 13
    assert synth["rng"]["seed"] == 20260821
    assert synth["rng"]["bit_generator"] == "PCG64"
    assert real["path"] == "voice_genesis/foundry/results_s7/s7_b1_real_render_manifest.json"
    assert real["n_real_render_conditions"] == 11


# --- 共通固定節 -------------------------------------------------------------


def test_seed_is_42_with_line_referenced_source(pins: Dict[str, Any]) -> None:
    seed = pins["common_fixed"]["seed"]
    assert seed["value"] == 42
    assert "gate_synth.py:149" in seed["source"]
    assert "SEED = 42" in seed["source"]


def test_onnxruntime_session_settings_are_pinned(pins: Dict[str, Any]) -> None:
    settings = pins["common_fixed"]["onnxruntime_session_settings"]
    assert settings["intra_op_num_threads"] == 1
    assert settings["inter_op_num_threads"] == 1
    assert settings["providers"] == ["CPUExecutionProvider"]


def test_execution_profile_matches_real_render_manifest_render_stack(
    pins: Dict[str, Any],
) -> None:
    profile = pins["common_fixed"]["execution_profile"]["value"]
    manifest_path = _REPO_ROOT / "voice_genesis/foundry/results_s7/s7_b1_real_render_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert profile == manifest["render_stack"], (
        "common_fixed.execution_profile.value が s7_b1_real_render_manifest.json "
        "の render_stack 節と一致しない（転記ミスまたは正本側の更新未反映）"
    )


def test_material_acquisition_command_references_provision_sh(pins: Dict[str, Any]) -> None:
    cmd = pins["common_fixed"]["material_acquisition_command"]["command"]
    assert "voice_genesis/foundry/run8/provision.sh" in cmd
    assert "--root" in cmd


def test_regeneration_commands_cover_10_render_groups(pins: Dict[str, Any]) -> None:
    regen = pins["common_fixed"]["regeneration_commands"]
    commands = regen["render_commands_10_groups"]
    assert isinstance(commands, list)
    assert len(commands) == 10
    for cmd in commands:
        assert "d4_runner.py render" in cmd
        assert "--generation" in cmd
        assert "--speaker" in cmd
        assert "--spec-sha256" in cmd
    measure_cmd = regen["measure_command"]
    assert "d4_runner.py measure" in measure_cmd
    assert measure_cmd.count("--render-doc") == 10
    assert measure_cmd.count("--render-manifest ") == 10 or measure_cmd.count(
        "--render-manifest-sha256"
    ) == 10


def test_regeneration_command_spec_sha256_matches_current_committed_spec(
    pins: Dict[str, Any],
) -> None:
    """再生成コマンドの --spec-sha256 は『現行コミット済み spec』の実 sha256 を
    使う設計（2026-08-22 実測が束縛した過去の v0.1 sha ではない）。値が実ファイル
    と一致していないと、テンプレート通りに実行しても d4_runner.py が起動時
    fail-closed で abort する壊れたコマンドになる。"""
    regen = pins["common_fixed"]["regeneration_commands"]
    current = regen["spec_sha256_current_v0_4"]
    actual = _sha256_file("voice_genesis/foundry/debt/d4/d4_remeasure_spec.json")
    assert current == actual, (
        f"regeneration_commands.spec_sha256_current_v0_4 {current} が "
        f"d4_remeasure_spec.json の実 sha256 {actual} と一致しない"
    )
    for cmd in regen["render_commands_10_groups"]:
        assert current in cmd, "render コマンドの --spec-sha256 が現行 sha256 と食い違う"
    assert current in regen["measure_command"]


def test_regeneration_command_frozen_v0_1_sha_matches_immutable_d4_results(
    pins: Dict[str, Any],
) -> None:
    """derivation 節に記載する『2026-08-22実測が束縛した v0.1 sha』は
    test_committed_artifacts_immutable.py が保護する d4_results_2026-08-22.json
    の d4_remeasure_spec_sha256 と一致していなければならない（凍結物の記帳と
    矛盾した値を pin ファイル側で主張しない）。"""
    regen = pins["common_fixed"]["regeneration_commands"]
    frozen_v0_1 = regen["spec_sha256_execution_time_v0_1_frozen"]
    d4_results_path = _REPO_ROOT / "voice_genesis/foundry/debt/d4/d4_results_2026-08-22.json"
    d4_results = json.loads(d4_results_path.read_text(encoding="utf-8"))
    assert frozen_v0_1 == d4_results["d4_remeasure_spec_sha256"]


# --- 参照 {path, sha256} の実ファイル照合 -----------------------------------


def test_all_path_sha256_refs_are_nonempty(pins: Dict[str, Any]) -> None:
    refs = _iter_path_sha_refs(pins)
    assert len(refs) > 0, "no {path, sha256} refs found — inventory走査が壊れている"
    for r in refs:
        assert r["path"].strip(), r
        assert SHA256_RE.match(r["sha256"]), f"not a 64-hex sha256: {r['sha256']!r} ({r['path']})"


def test_all_path_sha256_refs_resolve_to_repo_files(pins: Dict[str, Any]) -> None:
    for r in _iter_path_sha_refs(pins):
        path = _REPO_ROOT / r["path"]
        assert path.is_file(), f"referenced file does not exist: {r['path']}"


def test_all_path_sha256_refs_match_actual_file_bytes(pins: Dict[str, Any]) -> None:
    """記帳した sha256 が実ファイルの実測値と一致する（転記ミス・正本側の
    事後更新の両方を検出する）。"""
    mismatches = []
    for r in _iter_path_sha_refs(pins):
        actual = _sha256_file(r["path"])
        if actual != r["sha256"]:
            mismatches.append((r["path"], r["sha256"], actual))
    assert not mismatches, (
        "sha256 mismatch(es) between pin file and actual repo file:\n"
        + "\n".join(f"  {p}: pinned={pinned} actual={actual}" for p, pinned, actual in mismatches)
    )


def test_at_least_14_distinct_refs_are_pinned(pins: Dict[str, Any]) -> None:
    """10 probe_0b_groups + cell_definition + d4_results + calibration 2件で
    最低 14 件の {path, sha256} 参照が要る —
    参照が『束縛しているつもりで実は空』という事故（PR #306 系のレビューで
    繰り返し指摘された取りこぼしパターン）を粗く検出する下限値。共通固定節の
    `sha256_of_*` フィールドは単独 sha であって {path, sha256} 型の参照では
    ないため、この下限には数えない。"""
    refs = _iter_path_sha_refs(pins)
    distinct_paths = {r["path"] for r in refs}
    assert len(distinct_paths) >= 14, sorted(distinct_paths)


# --- PENDING_PHASE_B プレースホルダの構造妥当性 ------------------------------


def _assert_pending_or_resolved(node: Dict[str, Any], *, context: str) -> None:
    """`status` キーを持つノードは `PENDING_PHASE_B`（未確定）か、確定後の
    実測値（`status` キーが無い、または他の確定語彙）のどちらかでなければ
    ならない。`PENDING_PHASE_B` のときは `reason` が必須・`value` は null。"""
    status = node.get("status")
    if status == "PENDING_PHASE_B":
        assert node.get("value") is None, f"{context}: PENDING_PHASE_B なのに value が非null"
        assert isinstance(node.get("reason"), str) and node["reason"].strip(), (
            f"{context}: PENDING_PHASE_B なのに reason が空"
        )
    elif status is not None:
        # Phase B 完了後に確定語彙（例: "RESOLVED"）へ差し替わるケースを許容する。
        assert isinstance(status, str) and status.strip()


def test_reproducibility_reconciliation_is_pending_phase_b_or_resolved(
    pins: Dict[str, Any],
) -> None:
    node = pins["reproducibility_reconciliation"]
    _assert_pending_or_resolved(node, context="reproducibility_reconciliation")
    assert isinstance(node.get("levels"), list) and len(node["levels"]) == 3
    level_names = {level["name"] for level in node["levels"]}
    assert level_names == {
        "reference_output_remeasurement", "samples_sha256", "wav_sha256",
    }
    # 強さの順（s7_reproducibility_finding.md §4 が正本）を level 番号が
    # reference_output > samples_sha256 > wav_sha256 の順に反映していること。
    by_name = {level["name"]: level["level"] for level in node["levels"]}
    assert (
        by_name["reference_output_remeasurement"]
        < by_name["samples_sha256"]
        < by_name["wav_sha256"]
    )


def test_honest_accounting_commit_is_pending_phase_b_or_resolved(pins: Dict[str, Any]) -> None:
    node = pins["honest_accounting"]["commit"]
    _assert_pending_or_resolved(node, context="honest_accounting.commit")


def test_honest_accounting_documents_unrecorded_execution_commits(pins: Dict[str, Any]) -> None:
    section = pins["honest_accounting"]["unrecorded_execution_commits"]
    assert isinstance(section.get("statement"), str) and section["statement"].strip()
    for date in ("2026-08-21", "2026-08-22"):
        assert date in section["statement"], f"{date} が unrecorded_execution_commits.statement に無い"
    assert isinstance(section.get("why_unrecoverable"), str) and section["why_unrecoverable"].strip()


# --- CI 収集ガード ----------------------------------------------------------


def test_pyproject_lists_this_test_module() -> None:
    """収集しないと『緑なのに何も検査していない』状態になる
    （test_run4_provenance_closure.py と同じ教訓）。"""
    pyproject = _REPO_ROOT / "pyproject.toml"
    assert pyproject.exists()
    text = pyproject.read_text(encoding="utf-8")
    assert "voice_genesis/foundry/tests/test_d6_fixed_probe_pins.py" in text
