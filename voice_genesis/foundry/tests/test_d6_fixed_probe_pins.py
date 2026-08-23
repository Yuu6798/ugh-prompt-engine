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

from voice_genesis.foundry.debt.d6 import d6_regenerate

_FOUNDRY = Path(__file__).resolve().parent.parent
_REPO_ROOT = _FOUNDRY.parent.parent
PINS_PATH = _FOUNDRY / "debt" / "d6" / "s7_fixed_probe_pins.json"
VG_DET0_DESIGN_PATH = _FOUNDRY / "DESIGN_VG_DET0_run7_replication.md"

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


D4_RESULTS_SHA256 = _sha256_file("voice_genesis/foundry/debt/d4/d4_results_2026-08-22.json")
SYNTHETIC_PINS_SHA256 = _sha256_file(
    "voice_genesis/foundry/debt/d6/s7_synthetic_calibration_output_pins.json"
)
REAL_RENDER_MANIFEST_SHA256 = _sha256_file(
    "voice_genesis/foundry/results_s7/s7_b1_real_render_manifest.json"
)


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


def test_vg_det0_uses_successful_run7_manifest_commit_as_baseline() -> None:
    text = VG_DET0_DESIGN_PATH.read_text(encoding="utf-8")
    successful = "7df3a5fe5e34129218d5f3f0cc33ce332eebfff3"
    assert text.count(successful) >= 3
    assert "8ef874b" not in text


REQUIRED_TOP_LEVEL_KEYS = {
    "schema",
    "debt_ref",
    "phase",
    "authority",
    "purpose",
    "wav_not_committed_policy",
    "production_cells",
    "calibration_set",
    "common_fixed",
    "reproducibility_reconciliation",
    "honest_accounting",
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
        ("run5", "ritsu"),
        ("run5", "pjs"),
        ("run5", "user"),
        ("run6", "ritsu"),
        ("run6", "pjs"),
        ("run6", "user"),
        ("run7", "ritsu"),
        ("run7", "pjs"),
        ("run7", "user"),
        ("run7", "amitaro"),
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
    assert real["n_total_conditions"] == 14


def _assert_real_render_recovery(recovery: Dict[str, Any]) -> None:
    assert recovery["required_asset"]["sha256"] == (d6_regenerate.REAL_RENDER_ACOUSTIC_SHA256)
    assert recovery["historical_source"]["git_commit"] == (
        d6_regenerate.REAL_RENDER_HISTORICAL_COMMIT
    )
    assert "再export" in recovery["required_asset"]["substitution_policy"]
    schema = recovery["recovered_value_schema"]
    assert set(schema["required_keys"]) == {
        "execution_commit",
        "recovered_asset_sha256",
        "baseline_manifest_sha256",
        "regenerated_manifest_sha256",
        "n_compared",
        "n_matches",
        "n_mismatches",
    }
    assert schema["fixed_values"] == {
        "recovered_asset_sha256": d6_regenerate.REAL_RENDER_ACOUSTIC_SHA256,
        "baseline_manifest_sha256": REAL_RENDER_MANIFEST_SHA256,
        "n_compared": 14,
        "n_matches": 14,
        "n_mismatches": 0,
    }
    status = recovery["status"]
    if status == "BLOCKED_MISSING_PINNED_ACOUSTIC":
        assert recovery["required_asset"]["source"] == "UNRECORDED"
        assert recovery.get("value") is None
        return
    assert status == "RECOVERED_AND_RECONCILED"
    assert recovery["required_asset"]["source"] != "UNRECORDED"
    value = recovery["value"]
    assert set(value) == {
        "execution_commit",
        "recovered_asset_sha256",
        "baseline_manifest_sha256",
        "regenerated_manifest_sha256",
        "n_compared",
        "n_matches",
        "n_mismatches",
    }
    assert re.fullmatch(r"[0-9a-f]{40}", value["execution_commit"])
    assert value["recovered_asset_sha256"] == d6_regenerate.REAL_RENDER_ACOUSTIC_SHA256
    assert value["baseline_manifest_sha256"] == REAL_RENDER_MANIFEST_SHA256
    assert SHA256_RE.fullmatch(value["regenerated_manifest_sha256"])
    _assert_count_reconciliation(value, expected=14, matches_key="n_matches")


def test_real_render_recovery_accepts_blocked_and_complete_recovered_states(
    pins: Dict[str, Any],
) -> None:
    recovery = pins["calibration_set"]["real_render_recovery"]
    _assert_real_render_recovery(recovery)

    recovered = json.loads(json.dumps(recovery))
    recovered["status"] = "RECOVERED_AND_RECONCILED"
    recovered["required_asset"]["source"] = "operator-recovered archival copy"
    recovered["value"] = {
        "execution_commit": "a" * 40,
        "recovered_asset_sha256": d6_regenerate.REAL_RENDER_ACOUSTIC_SHA256,
        "baseline_manifest_sha256": REAL_RENDER_MANIFEST_SHA256,
        "regenerated_manifest_sha256": "b" * 64,
        "n_compared": 14,
        "n_matches": 14,
        "n_mismatches": 0,
    }
    _assert_real_render_recovery(recovered)

    recovered["value"]["recovered_asset_sha256"] = "0" * 64
    with pytest.raises(AssertionError):
        _assert_real_render_recovery(recovered)


def test_synthetic_calibration_output_pins_cover_and_reproduce_all_13(
    pins: Dict[str, Any], tmp_path: Path
) -> None:
    ref = pins["calibration_set"]["refs"]["synthetic_output_pins"]
    expected = json.loads((_REPO_ROOT / ref["path"]).read_text(encoding="utf-8"))
    observed = d6_regenerate.generate_calibration_outputs(tmp_path / "calibration")
    assert expected == observed
    assert expected["n_conditions"] == 13
    assert len(expected["stimuli"]) == 13
    for stim_id, stimulus in expected["stimuli"].items():
        for field in (
            "wav_sha256",
            "pcm_f32le_sha256",
            "analysis_samples_f64le_sha256",
        ):
            assert SHA256_RE.fullmatch(stimulus[field]), field
        import numpy as np
        import soundfile as sf

        decoded, sample_rate = sf.read(tmp_path / "calibration" / f"{stim_id}.wav", dtype="float32")
        assert sample_rate == stimulus["sample_rate_hz"]
        assert (
            hashlib.sha256(np.ascontiguousarray(decoded, dtype="<f4").tobytes()).hexdigest()
            == (stimulus["pcm_f32le_sha256"])
        )
    report = d6_regenerate.verify_calibration_outputs(tmp_path / "verified_calibration")
    assert report["verdict"] == "PASS"
    assert report["value"] == {"matched_conditions": 13, "mismatches": []}
    assert re.fullmatch(r"[0-9a-f]{40}", report["execution_commit"])
    assert report["runner"]["sha256"] == _sha256_file(report["runner"]["path"])


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


def test_material_acquisition_command_reaches_pinned_provision_sh(pins: Dict[str, Any]) -> None:
    cmd = pins["common_fixed"]["material_acquisition_command"]["command"]
    assert "--root" in cmd
    assert "--real-render-acoustic-onnx" in cmd
    provision = d6_regenerate.build_provision_command(Path("/tmp/d6-provision-check"))
    assert provision[0] == "bash"
    assert provision[1].endswith("voice_genesis/foundry/run8/provision.sh")


def test_provision_builds_and_checks_complete_pinned_render_runtime() -> None:
    text = d6_regenerate.PROVISION.read_text(encoding="utf-8")
    assert "venv_render" in text
    for pin in (
        "3.11.15",
        "numpy==2.4.6",
        "onnxruntime==1.29.0",
        "soundfile==0.14.0",
        "PyYAML==6.0.1",
    ):
        assert pin in text


def test_render_runtime_verification_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = json.loads(d6_regenerate.REAL_RENDER_BASELINE.read_text(encoding="utf-8"))

    def completed(observed: Dict[str, str]) -> Any:
        return d6_regenerate.subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(observed), stderr=""
        )

    monkeypatch.setattr(
        d6_regenerate.subprocess,
        "run",
        lambda *_args, **_kwargs: completed(baseline["render_stack"]),
    )
    d6_regenerate.verify_real_render_stack("/pinned/render/python")

    wrong = dict(baseline["render_stack"])
    wrong["onnxruntime"] = "0.0.0"
    monkeypatch.setattr(
        d6_regenerate.subprocess,
        "run",
        lambda *_args, **_kwargs: completed(wrong),
    )
    with pytest.raises(d6_regenerate.RegenerationError, match="execution profile mismatch"):
        d6_regenerate.verify_real_render_stack("/pinned/render/python")


def test_regeneration_runner_covers_10_exports_and_render_groups(
    pins: Dict[str, Any], tmp_path: Path
) -> None:
    regen = pins["common_fixed"]["regeneration_commands"]
    root = (tmp_path / "d6work").resolve()
    provision, exports, renders = d6_regenerate.static_plan(
        root, python_executable="/pinned/analysis/python"
    )
    assert len(exports) == regen["coverage"]["witnessed_exports"] == 10
    assert len(renders) == regen["coverage"]["render_groups"] == 10
    assert provision[-2:] == ["--root", str(root)]
    assert {
        (cmd[cmd.index("--generation") + 1], cmd[cmd.index("--artifact") + 1]) for cmd in exports
    } == {
        (generation, f"acoustic_onnx=s6_{generation}_acoustic.onnx")
        for generation, _speaker in d6_regenerate.GROUPS
    }
    pairs = {
        (cmd[cmd.index("--generation") + 1], cmd[cmd.index("--speaker") + 1]) for cmd in renders
    }
    assert pairs == set(d6_regenerate.GROUPS)
    for command in (*exports, *renders):
        assert all("<" not in arg and ">" not in arg for arg in command)

    _provision, _exports, default_renders = d6_regenerate.static_plan(root)
    assert all(
        command[0] == str(root / "venv_render" / "bin" / "python") for command in default_renders
    )

    recovered = tmp_path / "recovered" / "s6_run7_acoustic.onnx"
    real_command = d6_regenerate.build_real_render_command(
        root, recovered, python_executable="/pinned/analysis/python"
    )
    assert str(recovered) in real_command
    assert str(d6_regenerate.REAL_RENDER_HISTORICAL_COMMIT) in real_command[1]
    assert real_command[real_command.index("--manifest-out") + 1] == str(
        root / "calibration_real_render_manifest.json"
    )
    assert d6_regenerate.build_real_render_command(root, recovered)[0] == str(
        root / "venv_render" / "bin" / "python"
    )


def test_real_render_reconciliation_compares_all_14_sample_pins(tmp_path: Path) -> None:
    baseline = json.loads(d6_regenerate.REAL_RENDER_BASELINE.read_text(encoding="utf-8"))
    observed = json.loads(json.dumps(baseline))
    observed["out_dir"] = str(tmp_path / "regenerated")
    for condition in observed["conditions"]:
        condition["wav_sha256"] = "0" * 64
    observed_path = tmp_path / "observed.json"
    observed_path.write_text(json.dumps(observed), encoding="utf-8")
    report = d6_regenerate.reconcile_real_render_manifest(
        observed_path, tmp_path / "reconciliation.json"
    )
    assert report["verdict"] == "PASS"
    assert report["value"]["n_rendered"] == 11
    assert report["value"]["n_compared"] == 14
    assert report["value"]["samples_matches"] == 14
    assert report["value"]["wav_container_mismatches"] == 14

    observed["conditions"][0]["samples_sha256"] = "f" * 64
    observed_path.write_text(json.dumps(observed), encoding="utf-8")
    with pytest.raises(d6_regenerate.RegenerationError, match="samples"):
        d6_regenerate.reconcile_real_render_manifest(observed_path, tmp_path / "must-not-pass.json")


def test_phase_b_composer_emits_the_exact_validator_shape_and_fails_on_axis_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    regenerated_path = tmp_path / "d6_regenerated_results.json"
    baseline = json.loads(d6_regenerate.D4_BASELINE.read_text(encoding="utf-8"))
    regenerated_path.write_text(json.dumps(baseline), encoding="utf-8")
    synthetic_path = tmp_path / "calibration_synthetic_reconciliation.json"
    synthetic_path.write_text(
        json.dumps(
            {
                "schema": "vg-d6-synthetic-calibration-reconciliation/0.1",
                "verdict": "PASS",
                "value": {"matched_conditions": 13, "mismatches": []},
                "execution_commit": "a" * 40,
                "output_pins": {
                    "sha256": d6_regenerate.sha256_file(d6_regenerate.CALIBRATION_PINS)
                },
                "runner": {"sha256": d6_regenerate.sha256_file(Path(d6_regenerate.__file__))},
            }
        ),
        encoding="utf-8",
    )
    real_path = tmp_path / "calibration_real_render_reconciliation.json"
    real_path.write_text(
        json.dumps(
            {
                "schema": "vg-d6-real-render-calibration-reconciliation/0.1",
                "verdict": "PASS",
                "value": {
                    "n_rendered": 11,
                    "n_compared": 14,
                    "samples_matches": 14,
                    "samples_mismatches": [],
                    "wav_container_matches": 0,
                    "wav_container_mismatches": 14,
                },
                "baseline_manifest_sha256": REAL_RENDER_MANIFEST_SHA256,
                "regenerated_manifest_sha256": "b" * 64,
                "recovered_acoustic_sha256": d6_regenerate.REAL_RENDER_ACOUSTIC_SHA256,
                "historical_source_commit": d6_regenerate.REAL_RENDER_HISTORICAL_COMMIT,
                "execution_commit": "a" * 40,
            }
        ),
        encoding="utf-8",
    )
    recovered_acoustic = tmp_path / "operator-recovered.onnx"
    monkeypatch.setattr(d6_regenerate, "_verify_file_pin", lambda *_args, **_kwargs: None)
    report = d6_regenerate.compose_phase_b_reconciliation(
        regenerated_path,
        synthetic_path,
        real_path,
        recovered_acoustic,
        tmp_path / "phase_b.json",
    )
    _assert_real_render_recovery(report["real_render_recovery"])
    _assert_pending_or_resolved(
        report["reproducibility_reconciliation"],
        context="reproducibility_reconciliation",
    )

    first_group = next(iter(baseline["groups"].values()))
    first_cell = next(iter(first_group["cells"].values()))
    first_cell["axes"]["excess_tail_voiced_ms"] += 100.0
    regenerated_path.write_text(json.dumps(baseline), encoding="utf-8")
    with pytest.raises(d6_regenerate.RegenerationError, match="epsilon外"):
        d6_regenerate.compose_phase_b_reconciliation(
            regenerated_path,
            synthetic_path,
            real_path,
            recovered_acoustic,
            tmp_path / "failed_phase_b.json",
        )


def test_missing_or_reexported_real_render_acoustic_is_rejected(tmp_path: Path) -> None:
    missing = tmp_path / "missing.onnx"
    with pytest.raises(d6_regenerate.RegenerationError, match="必須の固定資産が無い"):
        d6_regenerate._verify_file_pin(
            missing,
            d6_regenerate.REAL_RENDER_ACOUSTIC_SHA256,
            label="historical acoustic ONNX",
        )
    reexport = tmp_path / "reexport.onnx"
    reexport.write_bytes(b"not the historical ONNX")
    with pytest.raises(d6_regenerate.RegenerationError, match="sha256"):
        d6_regenerate._verify_file_pin(
            reexport,
            d6_regenerate.REAL_RENDER_ACOUSTIC_SHA256,
            label="historical acoustic ONNX",
        )


def test_regeneration_runner_verifies_its_own_pinned_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d6_regenerate.verify_runner_pins()
    monkeypatch.setattr(d6_regenerate, "sha256_file", lambda _path: "0" * 64)
    with pytest.raises(d6_regenerate.RegenerationError, match="runner pin 不一致"):
        d6_regenerate.verify_runner_pins()


def test_regeneration_runner_rejects_tampered_calibration_output_pins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = d6_regenerate.sha256_file

    def tampered_calibration_only(path: Path) -> str:
        if path.resolve() == d6_regenerate.CALIBRATION_PINS.resolve():
            return "0" * 64
        return original(path)

    monkeypatch.setattr(d6_regenerate, "sha256_file", tampered_calibration_only)
    with pytest.raises(d6_regenerate.RegenerationError, match="output_pins"):
        d6_regenerate.verify_runner_pins()


def test_measure_command_hashes_all_generated_manifests(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "d6work").resolve()
    groups = d6_regenerate.group_paths(root)
    for index, group in enumerate(groups):
        group.render_manifest.parent.mkdir(parents=True, exist_ok=True)
        group.render_manifest.write_bytes(f"manifest-{index}".encode())
    command = d6_regenerate.build_measure_command(root, python_executable="/pinned/analysis/python")
    assert command.count("--render-doc") == 10
    assert command.count("--render-manifest") == 10
    assert command.count("--render-manifest-sha256") == 10
    digests = [
        command[index + 1] for index, arg in enumerate(command) if arg == "--render-manifest-sha256"
    ]
    assert digests == [d6_regenerate.sha256_file(group.render_manifest) for group in groups]


def test_measure_command_fails_closed_when_a_manifest_is_missing(tmp_path: Path) -> None:
    root = (tmp_path / "d6work").resolve()
    with pytest.raises(d6_regenerate.RegenerationError, match="未生成"):
        d6_regenerate.build_measure_command(root)


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
    root = Path("/tmp/d6-spec-check")
    _provision, _exports, renders = d6_regenerate.static_plan(root)
    for command in renders:
        index = command.index("--spec-sha256")
        assert command[index + 1] == current


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


def _assert_count_reconciliation(node: Dict[str, Any], *, expected: int, matches_key: str) -> None:
    assert node["n_compared"] == expected
    assert isinstance(node[matches_key], int) and node[matches_key] >= 0
    assert isinstance(node["n_mismatches"], int) and node["n_mismatches"] >= 0
    assert node[matches_key] + node["n_mismatches"] == expected


def _assert_complete_reconciliation_value(value: Any) -> None:
    assert isinstance(value, dict)
    assert set(value) == {
        "reference_output_remeasurement",
        "samples_sha256",
        "wav_sha256",
        "calibration",
    }
    reference = value["reference_output_remeasurement"]
    assert set(reference) == {
        "n_compared",
        "n_within_epsilon",
        "n_mismatches",
        "baseline_results_sha256",
        "regenerated_results_sha256",
        "max_abs_delta_by_axis",
    }
    _assert_count_reconciliation(reference, expected=360, matches_key="n_within_epsilon")
    assert reference["n_within_epsilon"] == 360
    assert reference["n_mismatches"] == 0
    assert reference["baseline_results_sha256"] == D4_RESULTS_SHA256
    assert SHA256_RE.fullmatch(reference["regenerated_results_sha256"])
    assert set(reference["max_abs_delta_by_axis"]) == {
        "excess_tail_voiced_ms",
        "release_after_score_boundary_ms",
        "tail_f0_persistence",
    }
    assert all(
        isinstance(axis_value, (int, float)) and axis_value >= 0
        for axis_value in reference["max_abs_delta_by_axis"].values()
    )
    for name in ("samples_sha256", "wav_sha256"):
        section = value[name]
        assert set(section) == {
            "n_compared",
            "n_matches",
            "n_mismatches",
            "baseline_inventory_sha256",
            "regenerated_inventory_sha256",
        }
        _assert_count_reconciliation(section, expected=360, matches_key="n_matches")
        assert section["baseline_inventory_sha256"] == D4_RESULTS_SHA256
        assert SHA256_RE.fullmatch(section["regenerated_inventory_sha256"])

    calibration = value["calibration"]
    assert set(calibration) == {"synthetic", "real_render"}
    synthetic = calibration["synthetic"]
    assert set(synthetic) == {
        "n_compared",
        "n_matches",
        "n_mismatches",
        "baseline_pins_sha256",
        "reconciliation_sha256",
    }
    _assert_count_reconciliation(synthetic, expected=13, matches_key="n_matches")
    assert synthetic["n_matches"] == 13 and synthetic["n_mismatches"] == 0
    assert synthetic["baseline_pins_sha256"] == SYNTHETIC_PINS_SHA256
    assert SHA256_RE.fullmatch(synthetic["reconciliation_sha256"])

    real_render = calibration["real_render"]
    assert set(real_render) == {
        "n_compared",
        "n_matches",
        "n_mismatches",
        "baseline_manifest_sha256",
        "regenerated_manifest_sha256",
        "recovery_acoustic_sha256",
    }
    _assert_count_reconciliation(real_render, expected=14, matches_key="n_matches")
    assert real_render["n_matches"] == 14 and real_render["n_mismatches"] == 0
    assert real_render["baseline_manifest_sha256"] == REAL_RENDER_MANIFEST_SHA256
    assert SHA256_RE.fullmatch(real_render["regenerated_manifest_sha256"])
    assert real_render["recovery_acoustic_sha256"] == (d6_regenerate.REAL_RENDER_ACOUSTIC_SHA256)


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
        return
    assert status == "RESOLVED", f"{context}: 未知の status {status!r}"
    value = node.get("value")
    if context == "reproducibility_reconciliation":
        _assert_complete_reconciliation_value(value)
        assert re.fullmatch(r"[0-9a-f]{40}", str(node.get("execution_commit", ""))), (
            f"{context}: RESOLVED の execution_commit が完全な git SHA でない"
        )
    else:
        assert re.fullmatch(r"[0-9a-f]{40}", str(value or "")), (
            f"{context}: RESOLVED の value が完全な git SHA でない"
        )


@pytest.mark.parametrize(
    ("context", "node"),
    [
        (
            "reproducibility_reconciliation",
            {
                "status": "RESOLVED",
                "execution_commit": "a" * 40,
                "value": {
                    "reference_output_remeasurement": {
                        "n_compared": 360,
                        "n_within_epsilon": 360,
                        "n_mismatches": 0,
                        "baseline_results_sha256": D4_RESULTS_SHA256,
                        "regenerated_results_sha256": "2" * 64,
                        "max_abs_delta_by_axis": {
                            "excess_tail_voiced_ms": 0.0,
                            "release_after_score_boundary_ms": 0.0,
                            "tail_f0_persistence": 0.0,
                        },
                    },
                    "samples_sha256": {
                        "n_compared": 360,
                        "n_matches": 360,
                        "n_mismatches": 0,
                        "baseline_inventory_sha256": D4_RESULTS_SHA256,
                        "regenerated_inventory_sha256": "4" * 64,
                    },
                    "wav_sha256": {
                        "n_compared": 360,
                        "n_matches": 0,
                        "n_mismatches": 360,
                        "baseline_inventory_sha256": D4_RESULTS_SHA256,
                        "regenerated_inventory_sha256": "6" * 64,
                    },
                    "calibration": {
                        "synthetic": {
                            "n_compared": 13,
                            "n_matches": 13,
                            "n_mismatches": 0,
                            "baseline_pins_sha256": SYNTHETIC_PINS_SHA256,
                            "reconciliation_sha256": "8" * 64,
                        },
                        "real_render": {
                            "n_compared": 14,
                            "n_matches": 14,
                            "n_mismatches": 0,
                            "baseline_manifest_sha256": REAL_RENDER_MANIFEST_SHA256,
                            "regenerated_manifest_sha256": "a" * 64,
                            "recovery_acoustic_sha256": (d6_regenerate.REAL_RENDER_ACOUSTIC_SHA256),
                        },
                    },
                },
            },
        ),
        ("honest_accounting.commit", {"status": "RESOLVED", "value": "b" * 40}),
    ],
)
def test_resolved_phase_b_shapes_are_accepted(context: str, node: Dict[str, Any]) -> None:
    _assert_pending_or_resolved(node, context=context)


@pytest.mark.parametrize(
    ("context", "node"),
    [
        ("reproducibility_reconciliation", {"status": "RESOLVED", "value": None}),
        (
            "reproducibility_reconciliation",
            {"status": "RESOLVED", "value": {"verdict": "PASS"}, "execution_commit": "a" * 40},
        ),
        ("reproducibility_reconciliation", {"status": "DONE", "value": {"x": 1}}),
        ("honest_accounting.commit", {"status": "RESOLVED", "value": None}),
        ("honest_accounting.commit", {"status": "resolved", "value": "a" * 40}),
    ],
)
def test_false_resolved_phase_b_shapes_are_rejected(context: str, node: Dict[str, Any]) -> None:
    with pytest.raises(AssertionError):
        _assert_pending_or_resolved(node, context=context)


def test_reproducibility_reconciliation_is_pending_phase_b_or_resolved(
    pins: Dict[str, Any],
) -> None:
    node = pins["reproducibility_reconciliation"]
    _assert_pending_or_resolved(node, context="reproducibility_reconciliation")
    assert isinstance(node.get("levels"), list) and len(node["levels"]) == 3
    level_names = {level["name"] for level in node["levels"]}
    assert level_names == {
        "reference_output_remeasurement",
        "samples_sha256",
        "wav_sha256",
    }
    # 強さの順（s7_reproducibility_finding.md §4 が正本）を level 番号が
    # reference_output > samples_sha256 > wav_sha256 の順に反映していること。
    by_name = {level["name"]: level["level"] for level in node["levels"]}
    assert (
        by_name["reference_output_remeasurement"]
        < by_name["samples_sha256"]
        < by_name["wav_sha256"]
    )
    schema = node["resolved_value_schema"]
    assert set(schema) == {
        "reference_output_remeasurement",
        "samples_sha256",
        "wav_sha256",
        "calibration",
        "execution_commit",
    }
    assert schema["reference_output_remeasurement"]["baseline_results_sha256"] == (
        D4_RESULTS_SHA256
    )
    assert schema["samples_sha256"]["baseline_inventory_sha256"] == D4_RESULTS_SHA256
    assert schema["wav_sha256"]["baseline_inventory_sha256"] == D4_RESULTS_SHA256
    assert schema["calibration"]["synthetic"]["baseline_pins_sha256"] == (SYNTHETIC_PINS_SHA256)
    assert schema["calibration"]["real_render"]["baseline_manifest_sha256"] == (
        REAL_RENDER_MANIFEST_SHA256
    )
    assert schema["calibration"]["real_render"]["recovery_acoustic_sha256"] == (
        d6_regenerate.REAL_RENDER_ACOUSTIC_SHA256
    )
    recovery = pins["calibration_set"]["real_render_recovery"]
    _assert_real_render_recovery(recovery)
    if recovery["status"] == "RECOVERED_AND_RECONCILED":
        assert node["status"] == "RESOLVED"
    else:
        assert node["status"] == "PENDING_PHASE_B"
        assert node["value"] is None


def test_honest_accounting_commit_is_pending_phase_b_or_resolved(pins: Dict[str, Any]) -> None:
    node = pins["honest_accounting"]["commit"]
    _assert_pending_or_resolved(node, context="honest_accounting.commit")


def test_honest_accounting_documents_unrecorded_execution_commits(pins: Dict[str, Any]) -> None:
    section = pins["honest_accounting"]["unrecorded_execution_commits"]
    assert isinstance(section.get("statement"), str) and section["statement"].strip()
    for date in ("2026-08-21", "2026-08-22"):
        assert date in section["statement"], (
            f"{date} が unrecorded_execution_commits.statement に無い"
        )
    assert (
        isinstance(section.get("why_unrecoverable"), str) and section["why_unrecoverable"].strip()
    )


# --- CI 収集ガード ----------------------------------------------------------


def test_pyproject_lists_this_test_module() -> None:
    """収集しないと『緑なのに何も検査していない』状態になる
    （test_run4_provenance_closure.py と同じ教訓）。"""
    pyproject = _REPO_ROOT / "pyproject.toml"
    assert pyproject.exists()
    text = pyproject.read_text(encoding="utf-8")
    assert "voice_genesis/foundry/tests/test_d6_fixed_probe_pins.py" in text
