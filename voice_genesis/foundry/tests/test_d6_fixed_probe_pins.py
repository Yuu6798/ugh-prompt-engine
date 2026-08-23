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
確認する。Phase B 完了後は、完全な裁定形に加えて commit 済み report の実
sha256 と report 内裁定値への結合を検証し、手書き RESOLVED を受理しない。
"""

from __future__ import annotations

import hashlib
import inspect
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


def _expected_render_runtime() -> Dict[str, str]:
    pins = json.loads(d6_regenerate.FIXED_PROBE_PINS.read_text(encoding="utf-8"))
    return dict(pins["common_fixed"]["execution_profile"]["value"])


def _expected_measurement_dependencies() -> Dict[str, str]:
    pins = json.loads(d6_regenerate.FIXED_PROBE_PINS.read_text(encoding="utf-8"))
    return dict(
        pins["common_fixed"]["execution_profile"]["measurement_dependencies"][
            "value"
        ]
    )


D4_RESULTS_SHA256 = _sha256_file("voice_genesis/foundry/debt/d4/d4_results_2026-08-22.json")
SYNTHETIC_PINS_SHA256 = _sha256_file(
    "voice_genesis/foundry/debt/d6/s7_synthetic_calibration_output_pins.json"
)
REAL_RENDER_MANIFEST_SHA256 = _sha256_file(
    "voice_genesis/foundry/results_s7/s7_b1_real_render_manifest.json"
)
TRF_EPSILONS = {
    axis: float(value["epsilon"])
    for axis, value in json.loads(d6_regenerate.TRF_SPEC.read_text(encoding="utf-8"))[
        "axes"
    ].items()
    if axis in d6_regenerate.RECONCILIATION_AXES
}


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


def test_vg_det0_preregisters_all_post_training_export_gates() -> None:
    text = VG_DET0_DESIGN_PATH.read_text(encoding="utf-8")
    for required in (
        "s7_export_manifest.py::load_input_pins()",
        "s7_exporter_input_pins.json",
        "s7_0b_probe_spec.json",
        "d4_runner.py::cmd_render()",
        "run_execution_manifest.json",
        "post-training admission commit",
        "actual checkpoint→admission→",
    ):
        assert required in text
    assert "digest の CLI 手入力・環境変数注入・run7 値の既定化は禁止" in text
    assert "3 箇所の checkpoint digest は実checkpoint bytesの観測値と同一" in text


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
    assert real["sha256"] == d6_regenerate.REAL_RENDER_BASELINE_SHA256
    assert real["n_real_render_conditions"] == 11
    assert real["n_total_conditions"] == 14


def _assert_real_render_recovery(recovery: Dict[str, Any]) -> None:
    assert recovery["required_asset"]["sha256"] == (d6_regenerate.REAL_RENDER_ACOUSTIC_SHA256)
    historical_source = recovery["historical_source"]
    assert historical_source["git_commit"] == d6_regenerate.REAL_RENDER_HISTORICAL_COMMIT
    assert set(historical_source) == {
        "git_commit",
        *(label for label, _path in d6_regenerate._HISTORICAL_REAL_RENDER_IMPORT_CLOSURE),
    }
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
    pins: Dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
        assert (
            hashlib.sha256(np.ascontiguousarray(decoded, dtype="<f8").tobytes()).hexdigest()
            == stimulus["analysis_samples_f64le_sha256"]
        )
    original_read_bytes = Path.read_bytes
    calibration_pin_reads = 0

    def counted_calibration_pin_read(path: Path) -> bytes:
        nonlocal calibration_pin_reads
        if path == d6_regenerate.CALIBRATION_PINS:
            calibration_pin_reads += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_calibration_pin_read)
    report = d6_regenerate.verify_calibration_outputs(tmp_path / "verified_calibration")
    assert calibration_pin_reads == 1
    assert report["verdict"] == "PASS"
    assert report["value"] == {"matched_conditions": 13, "mismatches": []}
    assert re.fullmatch(r"[0-9a-f]{40}", report["execution_commit"])
    assert report["output_pins"]["sha256"] == hashlib.sha256(
        original_read_bytes(d6_regenerate.CALIBRATION_PINS)
    ).hexdigest()
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
        "numba==0.66.0",
        "librosa==0.11.0",
        "pyloudnorm==0.2.0",
        "scipy==1.17.1",
    ):
        assert pin in text
    # import-only の環境検査ではなく、D4 が通常測定で呼ぶ librosa/numba 経路を
    # provision 自身が実行してから成功を宣言する。
    assert '"$RENDER_VENV/bin/python" - "$RUN8_DIR"' in text
    assert "b1.verify_analysis_stack(prereg)" in text
    assert "v12.measure_candidate_12(candidate, stimulus)" in text


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
    synthetic_command = d6_regenerate.build_synthetic_calibration_command(root)
    assert synthetic_command == [
        str(root / "venv_render" / "bin" / "python"),
        str(Path(d6_regenerate.__file__).resolve()),
        d6_regenerate._INTERNAL_SYNTHETIC_CALIBRATION_FLAG,
        str(root / "calibration_synthetic"),
    ]

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


def test_historical_source_is_fresh_and_complete_on_reused_root(tmp_path: Path) -> None:
    root = tmp_path / "d6work"
    stale = d6_regenerate._real_render_source_root(root)
    poisoned_helper = stale / "voice_genesis/foundry/run8/s7_io.py"
    poisoned_helper.parent.mkdir(parents=True, exist_ok=True)
    poisoned_helper.write_text("raise RuntimeError('poisoned')\n", encoding="utf-8")
    poisoned_cache = stale / "voice_genesis/foundry/run8/__pycache__/s7_io.pyc"
    poisoned_cache.parent.mkdir(parents=True, exist_ok=True)
    poisoned_cache.write_bytes(b"poisoned bytecode")

    source = d6_regenerate.materialize_historical_real_render_source(root)

    assert source == stale
    assert not poisoned_cache.exists()
    for _label, relative_path in d6_regenerate._HISTORICAL_REAL_RENDER_IMPORT_CLOSURE:
        expected = d6_regenerate._historical_git_object_sha256(relative_path)
        assert d6_regenerate.sha256_file(source / relative_path) == expected

    for relative_path in (
        Path("voice_genesis/foundry/run8/s7_io.py"),
        Path("voice_genesis/foundry/run8/s7_spec.py"),
    ):
        helper = source / relative_path
        original = helper.read_bytes()
        helper.write_bytes(original + b"\n# tampered\n")
        with pytest.raises(
            d6_regenerate.RegenerationError,
            match=f"historical source .*{relative_path.stem}",
        ):
            d6_regenerate._verify_historical_source(source)
        helper.write_bytes(original)

    d6_regenerate._verify_historical_source(source)


def test_real_render_reconciliation_compares_all_14_sample_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = json.loads(d6_regenerate.REAL_RENDER_BASELINE.read_text(encoding="utf-8"))
    observed = json.loads(json.dumps(baseline))
    observed["out_dir"] = str(tmp_path / "regenerated")
    for condition in observed["conditions"]:
        condition["wav_sha256"] = "0" * 64
    observed_path = tmp_path / "observed.json"
    observed_path.write_text(json.dumps(observed), encoding="utf-8")
    manifest_inputs = {d6_regenerate.REAL_RENDER_BASELINE, observed_path}
    manifest_reads = {path: 0 for path in manifest_inputs}
    original_read_bytes = Path.read_bytes

    def counted_manifest_read(path: Path) -> bytes:
        if path in manifest_reads:
            manifest_reads[path] += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_manifest_read)
    report = d6_regenerate.reconcile_real_render_manifest(
        observed_path, tmp_path / "reconciliation.json"
    )
    assert manifest_reads == {path: 1 for path in manifest_inputs}
    assert report["verdict"] == "PASS"
    assert report["value"]["n_rendered"] == 11
    assert report["value"]["n_compared"] == 14
    assert report["value"]["samples_matches"] == 14
    assert report["value"]["wav_container_mismatches"] == 14
    assert report["baseline_manifest_sha256"] == hashlib.sha256(
        original_read_bytes(d6_regenerate.REAL_RENDER_BASELINE)
    ).hexdigest()
    assert report["regenerated_manifest_sha256"] == hashlib.sha256(
        original_read_bytes(observed_path)
    ).hexdigest()

    observed["conditions"][0]["samples_sha256"] = "f" * 64
    observed_path.write_text(json.dumps(observed), encoding="utf-8")
    with pytest.raises(d6_regenerate.RegenerationError, match="samples"):
        d6_regenerate.reconcile_real_render_manifest(observed_path, tmp_path / "must-not-pass.json")


def _bind_regenerated_to_current_render_evidence(
    tmp_path: Path, regenerated: Dict[str, Any]
) -> tuple[Path, tuple[d6_regenerate.GroupPaths, ...]]:
    """current runner が出す provenance 形へ fixture を組み立てる。

    baseline JSON の単純コピーを正例にしないことが、この helper の目的。
    """
    d4_spec_bytes = d6_regenerate.D4_SPEC.read_bytes()
    d4_spec = json.loads(d4_spec_bytes)
    d4_spec_sha = hashlib.sha256(d4_spec_bytes).hexdigest()
    baseline = json.loads(d6_regenerate.D4_BASELINE.read_text(encoding="utf-8"))
    regenerated.update(
        {
            "schema": "vg-d4-remeasure-results/0.1",
            "debt_ref": "VG-DEBT-004",
            "generated_by": "voice_genesis/foundry/debt/d4/d4_runner.py",
            "d4_remeasure_spec_sha256": d4_spec_sha,
            "d4_remeasure_spec_path": (
                "voice_genesis/foundry/debt/d4/d4_remeasure_spec.json"
            ),
            "trf_measurement_spec_1_2_sha256": d4_spec["pins"][
                "trf_measurement_spec_1_2_sha256"
            ],
            "instrument_sha256": d4_spec["pins"]["instrument_sha256"],
            "candidate_ids": baseline["candidate_ids"],
            "analysis_stack": baseline["analysis_stack"],
            "runtime_stack": _expected_render_runtime(),
            "measurement_dependency_stack": _expected_measurement_dependencies(),
            "n_groups": 10,
            "n_total_cells": 360,
            "n_total_measured": 360,
            "n_total_error": 0,
            "complete": True,
        }
    )
    render_runtime = {
        "python": "3.11.15",
        "numpy": "2.4.6",
        "onnxruntime": "1.29.0",
        "soundfile": "0.14.0",
        "PyYAML": "6.0.1",
    }
    paths = d6_regenerate.group_paths(tmp_path / "current-run")
    for group in paths:
        group_id = f"{group.generation}_{group.speaker}"
        result_group = regenerated["groups"][group_id]
        materials = result_group["materials_sha256"]
        render_doc = {
            "generation": group.generation,
            "speaker": group.speaker,
            "d4_schema": "vg-d4-render-group-result/0.1",
            "d4_remeasure_spec_sha256": d4_spec_sha,
            "d4_remeasure_spec_path": (
                "voice_genesis/foundry/debt/d4/d4_remeasure_spec.json"
            ),
            "runtime_stack": render_runtime,
            "model_sha256": materials["model_sha256"],
            "aux_sha256": materials["aux_sha256"],
            "export_binding": materials["export_binding"],
        }
        group.render_doc.parent.mkdir(parents=True, exist_ok=True)
        group.render_doc.write_text(json.dumps(render_doc), encoding="utf-8")
        render_doc_sha = hashlib.sha256(group.render_doc.read_bytes()).hexdigest()
        group.render_manifest.write_text(
            json.dumps(
                {
                    "schema": "vg-d4-render-manifest/0.1",
                    "groups": {
                        group_id: {
                            "render_doc_sha256": render_doc_sha,
                            "path": group.render_doc.name,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        result_group.update(
            {
                "generation": group.generation,
                "speaker": group.speaker,
                "render_doc_path": str(group.render_doc.resolve()),
                "render_doc_sha256": render_doc_sha,
                "materials_sha256": materials,
                "render_runtime_stack": render_runtime,
                "render_runtime_stack_note": None,
            }
        )
    regenerated_path = tmp_path / "d6_regenerated_results.json"
    regenerated_path.write_text(json.dumps(regenerated), encoding="utf-8")
    return regenerated_path, paths


def test_regenerated_results_are_bound_to_current_render_manifests_and_materials(
    tmp_path: Path,
) -> None:
    copied_baseline = json.loads(d6_regenerate.D4_BASELINE.read_text(encoding="utf-8"))
    current = json.loads(json.dumps(copied_baseline))
    _current_path, rendered_groups = _bind_regenerated_to_current_render_evidence(
        tmp_path, current
    )
    d4_spec_bytes = d6_regenerate.D4_SPEC.read_bytes()
    d4_spec = json.loads(d4_spec_bytes)
    d4_spec_sha = hashlib.sha256(d4_spec_bytes).hexdigest()
    evidence, artifact_bytes = d6_regenerate._validate_regenerated_provenance(
        current,
        baseline=copied_baseline,
        d4_spec=d4_spec,
        d4_spec_sha=d4_spec_sha,
        rendered_groups=rendered_groups,
        expected_render_runtime=_expected_render_runtime(),
        expected_measurement_dependencies=_expected_measurement_dependencies(),
    )
    assert set(evidence) == {
        f"{generation}_{speaker}" for generation, speaker in d6_regenerate.GROUPS
    }
    assert set(artifact_bytes) == {
        path
        for generation, speaker in d6_regenerate.GROUPS
        for path in d6_regenerate._phase_b_group_refs(f"{generation}_{speaker}")
    }
    assert all(not Path(ref["path"]).is_absolute() for group in evidence.values() for ref in (
        group["render_doc"], group["render_manifest"]
    ))

    # 旧結果の単純コピーは、360セルが完全でも current manifest への結合が無い。
    with pytest.raises(d6_regenerate.RegenerationError, match="provenance mismatch"):
        d6_regenerate._validate_regenerated_provenance(
            copied_baseline,
            baseline=copied_baseline,
            d4_spec=d4_spec,
            d4_spec_sha=d4_spec_sha,
            rendered_groups=rendered_groups,
            expected_render_runtime=_expected_render_runtime(),
            expected_measurement_dependencies=_expected_measurement_dependencies(),
        )

    tampered = json.loads(json.dumps(current))
    tampered["groups"]["run5_ritsu"]["materials_sha256"]["model_sha256"][
        "acoustic_onnx"
    ] = "0" * 64
    with pytest.raises(d6_regenerate.RegenerationError, match="current render/materials"):
        d6_regenerate._validate_regenerated_provenance(
            tampered,
            baseline=copied_baseline,
            d4_spec=d4_spec,
            d4_spec_sha=d4_spec_sha,
            rendered_groups=rendered_groups,
            expected_render_runtime=_expected_render_runtime(),
            expected_measurement_dependencies=_expected_measurement_dependencies(),
        )


def test_render_docs_must_match_the_pinned_execution_profile(tmp_path: Path) -> None:
    baseline = json.loads(d6_regenerate.D4_BASELINE.read_text(encoding="utf-8"))
    current = json.loads(json.dumps(baseline))
    _current_path, rendered_groups = _bind_regenerated_to_current_render_evidence(
        tmp_path, current
    )
    group = rendered_groups[0]
    group_id = f"{group.generation}_{group.speaker}"
    doc = json.loads(group.render_doc.read_text(encoding="utf-8"))
    doc["runtime_stack"]["numpy"] = "9.9.9"
    group.render_doc.write_text(json.dumps(doc), encoding="utf-8")
    doc_sha = hashlib.sha256(group.render_doc.read_bytes()).hexdigest()
    manifest = json.loads(group.render_manifest.read_text(encoding="utf-8"))
    manifest["groups"][group_id]["render_doc_sha256"] = doc_sha
    group.render_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    current["groups"][group_id]["render_doc_sha256"] = doc_sha
    current["groups"][group_id]["render_runtime_stack"] = doc["runtime_stack"]
    d4_spec_bytes = d6_regenerate.D4_SPEC.read_bytes()
    with pytest.raises(d6_regenerate.RegenerationError, match="execution_profile"):
        d6_regenerate._validate_regenerated_provenance(
            current,
            baseline=baseline,
            d4_spec=json.loads(d4_spec_bytes),
            d4_spec_sha=hashlib.sha256(d4_spec_bytes).hexdigest(),
            rendered_groups=rendered_groups,
            expected_render_runtime=_expected_render_runtime(),
            expected_measurement_dependencies=_expected_measurement_dependencies(),
        )


def test_measurement_runtime_must_match_the_pinned_execution_profile(
    tmp_path: Path,
) -> None:
    baseline = json.loads(d6_regenerate.D4_BASELINE.read_text(encoding="utf-8"))
    current = json.loads(json.dumps(baseline))
    _current_path, rendered_groups = _bind_regenerated_to_current_render_evidence(
        tmp_path, current
    )
    current["runtime_stack"]["onnxruntime"] = None
    d4_spec_bytes = d6_regenerate.D4_SPEC.read_bytes()
    with pytest.raises(d6_regenerate.RegenerationError, match="measurement runtime_stack"):
        d6_regenerate._validate_regenerated_provenance(
            current,
            baseline=baseline,
            d4_spec=json.loads(d4_spec_bytes),
            d4_spec_sha=hashlib.sha256(d4_spec_bytes).hexdigest(),
            rendered_groups=rendered_groups,
            expected_render_runtime=_expected_render_runtime(),
            expected_measurement_dependencies=_expected_measurement_dependencies(),
        )


def test_measurement_dependencies_must_match_the_pinned_versions(
    tmp_path: Path,
) -> None:
    baseline = json.loads(d6_regenerate.D4_BASELINE.read_text(encoding="utf-8"))
    current = json.loads(json.dumps(baseline))
    _current_path, rendered_groups = _bind_regenerated_to_current_render_evidence(
        tmp_path, current
    )
    current["measurement_dependency_stack"]["scipy"] = "0.0.0"
    d4_spec_bytes = d6_regenerate.D4_SPEC.read_bytes()
    with pytest.raises(
        d6_regenerate.RegenerationError, match="measurement dependency stack"
    ):
        d6_regenerate._validate_regenerated_provenance(
            current,
            baseline=baseline,
            d4_spec=json.loads(d4_spec_bytes),
            d4_spec_sha=hashlib.sha256(d4_spec_bytes).hexdigest(),
            rendered_groups=rendered_groups,
            expected_render_runtime=_expected_render_runtime(),
            expected_measurement_dependencies=_expected_measurement_dependencies(),
        )


def _copy_phase_b_checkout_authorities(report_root: Path) -> None:
    for source in (
        Path(d6_regenerate.__file__).resolve(),
        d6_regenerate.D4_RUNNER,
        d6_regenerate.D4_SPEC,
        d6_regenerate.D4_BASELINE,
        d6_regenerate.FIXED_PROBE_PINS,
        d6_regenerate.TRF_SPEC,
        d6_regenerate.CALIBRATION_PINS,
        d6_regenerate.REAL_RENDER_BASELINE,
    ):
        relative = source.relative_to(d6_regenerate.REPO_ROOT)
        destination = report_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())


def test_phase_b_composer_emits_the_exact_validator_shape_and_fails_on_axis_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = json.loads(d6_regenerate.D4_BASELINE.read_text(encoding="utf-8"))
    regenerated_path, rendered_groups = _bind_regenerated_to_current_render_evidence(
        tmp_path, baseline
    )
    execution_commit = d6_regenerate.subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=d6_regenerate.REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    synthetic_observed = json.loads(
        d6_regenerate.CALIBRATION_PINS.read_text(encoding="utf-8")
    )
    synthetic_path = tmp_path / "calibration_synthetic_reconciliation.json"
    synthetic_path.write_text(
        json.dumps(
            {
                "schema": "vg-d6-synthetic-calibration-reconciliation/0.1",
                "verdict": "PASS",
                "value": {"matched_conditions": 13, "mismatches": []},
                "execution_commit": execution_commit,
                "output_pins": {
                    "path": str(
                        d6_regenerate.CALIBRATION_PINS.relative_to(
                            d6_regenerate.REPO_ROOT
                        )
                    ),
                    "sha256": d6_regenerate.sha256_file(d6_regenerate.CALIBRATION_PINS)
                },
                "runner": {
                    "path": str(
                        Path(d6_regenerate.__file__)
                        .resolve()
                        .relative_to(d6_regenerate.REPO_ROOT)
                    ),
                    "sha256": d6_regenerate.sha256_file(Path(d6_regenerate.__file__)),
                },
                "observed": synthetic_observed,
            }
        ),
        encoding="utf-8",
    )
    real_manifest_path = tmp_path / "calibration_real_render_manifest.json"
    real_manifest_path.write_bytes(d6_regenerate.REAL_RENDER_BASELINE.read_bytes())
    real_manifest_sha = hashlib.sha256(real_manifest_path.read_bytes()).hexdigest()
    real_value = d6_regenerate._reconcile_real_render_data(
        json.loads(d6_regenerate.REAL_RENDER_BASELINE.read_text(encoding="utf-8")),
        json.loads(real_manifest_path.read_text(encoding="utf-8")),
    )
    real_path = tmp_path / "calibration_real_render_reconciliation.json"
    real_path.write_text(
        json.dumps(
            {
                "schema": "vg-d6-real-render-calibration-reconciliation/0.1",
                "verdict": "PASS",
                "value": real_value,
                "baseline_manifest_sha256": REAL_RENDER_MANIFEST_SHA256,
                "regenerated_manifest_sha256": real_manifest_sha,
                "recovered_acoustic_sha256": d6_regenerate.REAL_RENDER_ACOUSTIC_SHA256,
                "historical_source_commit": d6_regenerate.REAL_RENDER_HISTORICAL_COMMIT,
                "execution_commit": execution_commit,
            }
        ),
        encoding="utf-8",
    )
    recovered_acoustic = tmp_path / "operator-recovered.onnx"
    monkeypatch.setattr(d6_regenerate, "_verify_file_pin", lambda *_args, **_kwargs: None)
    evidence_inputs = {
        regenerated_path,
        synthetic_path,
        real_path,
        real_manifest_path,
        d6_regenerate.D4_BASELINE,
        d6_regenerate.FIXED_PROBE_PINS,
        d6_regenerate.D4_SPEC,
        d6_regenerate.TRF_SPEC,
        d6_regenerate.CALIBRATION_PINS,
        d6_regenerate.REAL_RENDER_BASELINE,
        *(group.render_doc for group in rendered_groups),
        *(group.render_manifest for group in rendered_groups),
    }
    input_reads = {path: 0 for path in evidence_inputs}
    original_read_bytes = Path.read_bytes

    def counted_read_bytes(path: Path) -> bytes:
        if path in input_reads:
            input_reads[path] += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)
    report_path = tmp_path / d6_regenerate.PHASE_B_REPORT_PATH
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = d6_regenerate.compose_phase_b_reconciliation(
        regenerated_path,
        synthetic_path,
        real_path,
        real_manifest_path,
        recovered_acoustic,
        report_path,
        rendered_groups,
    )
    assert input_reads == {path: 1 for path in evidence_inputs}
    provenance = report["regenerated_provenance"]
    assert set(provenance) == {
        "bundle_id",
        "results",
        "d4_runner",
        "d4_remeasure_spec",
        "synthetic_reconciliation",
        "real_render_manifest",
        "groups",
    }
    assert SHA256_RE.fullmatch(provenance["bundle_id"])
    assert provenance["results"]["path"] == str(
        d6_regenerate._phase_b_bundle_path(
            provenance["bundle_id"], d6_regenerate.PHASE_B_RESULTS_PATH
        )
    )
    assert len(provenance["groups"]) == 10
    assert all(
        not Path(ref["path"]).is_absolute()
        for group in provenance["groups"].values()
        for ref in (group["render_doc"], group["render_manifest"])
    )
    for ref in (
        provenance["results"],
        provenance["synthetic_reconciliation"],
        provenance["real_render_manifest"],
        *(
            bound
            for group in provenance["groups"].values()
            for bound in (group["render_doc"], group["render_manifest"])
        ),
    ):
        committed = tmp_path / ref["path"]
        assert committed.is_file()
        assert hashlib.sha256(committed.read_bytes()).hexdigest() == ref["sha256"]
    assert report["reproducibility_reconciliation"]["value"]["reference_output_remeasurement"][
        "regenerated_results_sha256"
    ] == hashlib.sha256(original_read_bytes(regenerated_path)).hexdigest()
    assert report["reproducibility_reconciliation"]["value"]["calibration"]["synthetic"][
        "reconciliation_sha256"
    ] == hashlib.sha256(original_read_bytes(synthetic_path)).hexdigest()
    _assert_real_render_recovery(report["real_render_recovery"])
    resolved_node = json.loads(json.dumps(report["reproducibility_reconciliation"]))
    with pytest.raises(AssertionError):
        _assert_pending_or_resolved(
            resolved_node,
            context="reproducibility_reconciliation",
            report_root=tmp_path,
        )
    resolved_node["report_binding"] = {
        "path": str(d6_regenerate.PHASE_B_REPORT_PATH),
        "sha256": hashlib.sha256(original_read_bytes(report_path)).hexdigest(),
    }
    _copy_phase_b_checkout_authorities(tmp_path)
    _assert_pending_or_resolved(
        resolved_node,
        context="reproducibility_reconciliation",
        report_root=tmp_path,
    )
    committed_real_baseline = tmp_path / d6_regenerate.REAL_RENDER_BASELINE.relative_to(
        d6_regenerate.REPO_ROOT
    )
    validator_baseline_reads = 0

    def counted_validator_read(path: Path) -> bytes:
        nonlocal validator_baseline_reads
        if path.resolve() == committed_real_baseline.resolve():
            validator_baseline_reads += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_validator_read)
    d6_regenerate.validate_resolved_reconciliation(
        resolved_node, report_root=tmp_path
    )
    assert validator_baseline_reads == 1
    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)
    report_bytes = original_read_bytes(report_path)
    report_path.write_bytes(report_bytes + b" ")
    with pytest.raises(AssertionError):
        _assert_pending_or_resolved(
            resolved_node,
            context="reproducibility_reconciliation",
            report_root=tmp_path,
        )
    report_path.write_bytes(report_bytes)
    packaged_results_path = tmp_path / provenance["results"]["path"]
    packaged_results_bytes = packaged_results_path.read_bytes()
    packaged_results_path.write_bytes(packaged_results_bytes + b" ")
    with pytest.raises(AssertionError):
        _assert_pending_or_resolved(
            resolved_node,
            context="reproducibility_reconciliation",
            report_root=tmp_path,
        )
    packaged_results_path.write_bytes(packaged_results_bytes)

    def assert_rebound_report_rejected(tampered_report: dict[str, Any]) -> None:
        tampered_report_bytes = (json.dumps(tampered_report) + "\n").encode()
        report_path.write_bytes(tampered_report_bytes)
        tampered_node = json.loads(
            json.dumps(tampered_report["reproducibility_reconciliation"])
        )
        tampered_node["report_binding"] = {
            "path": str(d6_regenerate.PHASE_B_REPORT_PATH),
            "sha256": hashlib.sha256(tampered_report_bytes).hexdigest(),
        }
        with pytest.raises(AssertionError):
            _assert_pending_or_resolved(
                tampered_node,
                context="reproducibility_reconciliation",
                report_root=tmp_path,
            )

    # real-render正本・packaged manifest・reportを同時に再束縛しても、固定pinで拒否する。
    canonical_real_bytes = original_read_bytes(committed_real_baseline)
    replaced_real = json.loads(canonical_real_bytes)
    replaced_real["conditions"][0]["samples_sha256"] = "0" * 64
    replaced_real_bytes = (json.dumps(replaced_real) + "\n").encode()
    replaced_real_sha = hashlib.sha256(replaced_real_bytes).hexdigest()
    real_ref = provenance["real_render_manifest"]
    packaged_real_path = tmp_path / real_ref["path"]
    packaged_real_bytes = packaged_real_path.read_bytes()
    committed_real_baseline.write_bytes(replaced_real_bytes)
    packaged_real_path.write_bytes(replaced_real_bytes)
    replaced_report = json.loads(report_bytes)
    replaced_report["regenerated_provenance"]["real_render_manifest"]["sha256"] = (
        replaced_real_sha
    )
    replaced_calibration = replaced_report["reproducibility_reconciliation"]["value"][
        "calibration"
    ]["real_render"]
    replaced_calibration["baseline_manifest_sha256"] = replaced_real_sha
    replaced_calibration["regenerated_manifest_sha256"] = replaced_real_sha
    replaced_recovery = replaced_report["real_render_recovery"]["value"]
    replaced_recovery["baseline_manifest_sha256"] = replaced_real_sha
    replaced_recovery["regenerated_manifest_sha256"] = replaced_real_sha
    replaced_report_bytes = (json.dumps(replaced_report) + "\n").encode()
    report_path.write_bytes(replaced_report_bytes)
    replaced_node = json.loads(
        json.dumps(replaced_report["reproducibility_reconciliation"])
    )
    replaced_node["report_binding"] = {
        "path": str(d6_regenerate.PHASE_B_REPORT_PATH),
        "sha256": hashlib.sha256(replaced_report_bytes).hexdigest(),
    }
    validator_baseline_reads = 0
    monkeypatch.setattr(Path, "read_bytes", counted_validator_read)
    with pytest.raises(
        d6_regenerate.RegenerationError,
        match="canonical real-render baseline sha256",
    ):
        d6_regenerate.validate_resolved_reconciliation(
            replaced_node, report_root=tmp_path
        )
    assert validator_baseline_reads == 1
    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)
    committed_real_baseline.write_bytes(canonical_real_bytes)
    packaged_real_path.write_bytes(packaged_real_bytes)
    report_path.write_bytes(report_bytes)

    # report/node/digestを一緒に書き換えても、360実測からの再計算で拒否する。
    drifted_results = json.loads(packaged_results_bytes)
    drifted_group = next(iter(drifted_results["groups"].values()))
    drifted_cell = next(iter(drifted_group["cells"].values()))
    drifted_cell["axes"]["excess_tail_voiced_ms"] += 100.0
    drifted_results_bytes = (json.dumps(drifted_results) + "\n").encode()
    packaged_results_path.write_bytes(drifted_results_bytes)
    drifted_results_sha = hashlib.sha256(drifted_results_bytes).hexdigest()
    drifted_report = json.loads(report_bytes)
    drifted_report["regenerated_provenance"]["results"]["sha256"] = (
        drifted_results_sha
    )
    drifted_value = drifted_report["reproducibility_reconciliation"]["value"]
    drifted_value["reference_output_remeasurement"][
        "regenerated_results_sha256"
    ] = drifted_results_sha
    drifted_value["samples_sha256"]["regenerated_inventory_sha256"] = (
        drifted_results_sha
    )
    drifted_value["wav_sha256"]["regenerated_inventory_sha256"] = drifted_results_sha
    assert_rebound_report_rejected(drifted_report)
    packaged_results_path.write_bytes(packaged_results_bytes)
    report_path.write_bytes(report_bytes)

    # synthetic summaryだけでなく、packaged observed 13条件を正本へ再比較する。
    synthetic_ref = provenance["synthetic_reconciliation"]
    packaged_synthetic_path = tmp_path / synthetic_ref["path"]
    synthetic_bytes = packaged_synthetic_path.read_bytes()
    tampered_synthetic = json.loads(synthetic_bytes)
    tampered_synthetic["observed"]["n_conditions"] = 12
    tampered_synthetic_bytes = (json.dumps(tampered_synthetic) + "\n").encode()
    packaged_synthetic_path.write_bytes(tampered_synthetic_bytes)
    tampered_synthetic_sha = hashlib.sha256(tampered_synthetic_bytes).hexdigest()
    synthetic_report = json.loads(report_bytes)
    synthetic_report["regenerated_provenance"]["synthetic_reconciliation"][
        "sha256"
    ] = tampered_synthetic_sha
    synthetic_report["reproducibility_reconciliation"]["value"]["calibration"][
        "synthetic"
    ]["reconciliation_sha256"] = tampered_synthetic_sha
    assert_rebound_report_rejected(synthetic_report)
    packaged_synthetic_path.write_bytes(synthetic_bytes)
    report_path.write_bytes(report_bytes)

    # real-render summaryとdigestを同時改変しても、14条件manifestの再比較で拒否する。
    real_ref = provenance["real_render_manifest"]
    packaged_real_path = tmp_path / real_ref["path"]
    real_bytes = packaged_real_path.read_bytes()
    tampered_real = json.loads(real_bytes)
    tampered_real["conditions"][0]["samples_sha256"] = "0" * 64
    tampered_real_bytes = (json.dumps(tampered_real) + "\n").encode()
    packaged_real_path.write_bytes(tampered_real_bytes)
    tampered_real_sha = hashlib.sha256(tampered_real_bytes).hexdigest()
    real_report = json.loads(report_bytes)
    real_report["regenerated_provenance"]["real_render_manifest"]["sha256"] = (
        tampered_real_sha
    )
    real_report["reproducibility_reconciliation"]["value"]["calibration"][
        "real_render"
    ]["regenerated_manifest_sha256"] = tampered_real_sha
    real_report["real_render_recovery"]["value"][
        "regenerated_manifest_sha256"
    ] = tampered_real_sha
    assert_rebound_report_rejected(real_report)
    packaged_real_path.write_bytes(real_bytes)
    report_path.write_bytes(report_bytes)

    # 64桁のdigestでも、real-render正本が記帳した履歴実装と違えば拒否する。
    historical_report = json.loads(report_bytes)
    historical_report["real_render_recovery"]["historical_source"][
        "gate_synth_sha256"
    ] = "0" * 64
    assert_rebound_report_rejected(historical_report)
    report_path.write_bytes(report_bytes)

    absolute_ref_report = json.loads(report_bytes)
    first_group_id = sorted(absolute_ref_report["regenerated_provenance"]["groups"])[0]
    absolute_ref_report["regenerated_provenance"]["groups"][first_group_id]["render_doc"][
        "path"
    ] = "/operator/workspace/render.json"
    absolute_ref_bytes = (json.dumps(absolute_ref_report) + "\n").encode()
    report_path.write_bytes(absolute_ref_bytes)
    absolute_ref_node = json.loads(json.dumps(resolved_node))
    absolute_ref_node["report_binding"]["sha256"] = hashlib.sha256(
        absolute_ref_bytes
    ).hexdigest()
    with pytest.raises(AssertionError):
        _assert_pending_or_resolved(
            absolute_ref_node,
            context="reproducibility_reconciliation",
            report_root=tmp_path,
        )
    report_path.write_bytes(report_bytes)

    truncated = json.loads(report_bytes)
    truncated["regenerated_provenance"] = {}
    truncated_bytes = (json.dumps(truncated) + "\n").encode()
    report_path.write_bytes(truncated_bytes)
    truncated_node = json.loads(json.dumps(resolved_node))
    truncated_node["report_binding"]["sha256"] = hashlib.sha256(truncated_bytes).hexdigest()
    with pytest.raises(AssertionError):
        _assert_pending_or_resolved(
            truncated_node,
            context="reproducibility_reconciliation",
            report_root=tmp_path,
        )
    report_path.write_bytes(report_bytes)
    resolved_node["value"]["reference_output_remeasurement"][
        "max_abs_delta_by_axis"
    ]["excess_tail_voiced_ms"] = 100.0
    with pytest.raises(AssertionError):
        _assert_pending_or_resolved(
            resolved_node,
            context="reproducibility_reconciliation",
            report_root=tmp_path,
        )

    first_group = next(iter(baseline["groups"].values()))
    first_cell = next(iter(first_group["cells"].values()))
    first_cell["axes"]["excess_tail_voiced_ms"] += 100.0
    regenerated_path.write_text(json.dumps(baseline), encoding="utf-8")
    prior_report = report_path.read_bytes()
    prior_bundles = {
        path.relative_to(report_path.parent): path.read_bytes()
        for path in (report_path.parent / "d6_phase_b_evidence").rglob("*")
        if path.is_file()
    }
    with pytest.raises(d6_regenerate.RegenerationError, match="epsilon外"):
        d6_regenerate.compose_phase_b_reconciliation(
            regenerated_path,
            synthetic_path,
            real_path,
            real_manifest_path,
            recovered_acoustic,
            report_path,
            rendered_groups,
        )
    assert report_path.read_bytes() == prior_report
    assert {
        path.relative_to(report_path.parent): path.read_bytes()
        for path in (report_path.parent / "d6_phase_b_evidence").rglob("*")
        if path.is_file()
    } == prior_bundles

    tampered_baseline = tmp_path / "tampered_d4_baseline.json"
    tampered_baseline.write_text(json.dumps(baseline), encoding="utf-8")
    monkeypatch.setattr(d6_regenerate, "D4_BASELINE", tampered_baseline)
    with pytest.raises(d6_regenerate.RegenerationError, match="canonical D4 baseline"):
        d6_regenerate.compose_phase_b_reconciliation(
            regenerated_path,
            synthetic_path,
            real_path,
            real_manifest_path,
            recovered_acoustic,
            tmp_path / "must-not-use-tampered-baseline.json",
            rendered_groups,
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


def test_phase_b_bundle_publish_failure_preserves_previous_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_path = tmp_path / d6_regenerate.PHASE_B_REPORT_PATH
    first_leaf = {
        d6_regenerate.PHASE_B_EVIDENCE_PATH / "a.json": b"first-a",
        d6_regenerate.PHASE_B_EVIDENCE_PATH / "b.json": b"first-b",
    }
    first_id = d6_regenerate._phase_b_evidence_bundle_id(first_leaf)
    first_versioned = {
        d6_regenerate._phase_b_bundle_path(first_id, path): payload
        for path, payload in first_leaf.items()
    }
    first_report = b'{"generation":"first"}\n'
    d6_regenerate._publish_phase_b_bundle(
        report_path,
        first_versioned,
        first_report,
        bundle_id=first_id,
        protected_inputs=(),
    )
    first_dir = report_path.parent / "d6_phase_b_evidence" / first_id
    first_snapshot = {
        path.relative_to(first_dir): path.read_bytes()
        for path in first_dir.rglob("*")
        if path.is_file()
    }

    second_leaf = {
        d6_regenerate.PHASE_B_EVIDENCE_PATH / "a.json": b"second-a",
        d6_regenerate.PHASE_B_EVIDENCE_PATH / "b.json": b"second-b",
    }
    second_id = d6_regenerate._phase_b_evidence_bundle_id(second_leaf)
    second_versioned = {
        d6_regenerate._phase_b_bundle_path(second_id, path): payload
        for path, payload in second_leaf.items()
    }
    original_atomic_write = d6_regenerate._atomic_write_verified
    staging_writes = 0

    def fail_during_staging(path: Path, payload: bytes) -> None:
        nonlocal staging_writes
        if ".staging-" in str(path):
            staging_writes += 1
            if staging_writes == 2:
                raise OSError("injected evidence staging failure")
        original_atomic_write(path, payload)

    monkeypatch.setattr(d6_regenerate, "_atomic_write_verified", fail_during_staging)
    with pytest.raises(OSError, match="injected evidence staging failure"):
        d6_regenerate._publish_phase_b_bundle(
            report_path,
            second_versioned,
            b'{"generation":"second"}\n',
            bundle_id=second_id,
            protected_inputs=(),
        )

    assert report_path.read_bytes() == first_report
    assert {
        path.relative_to(first_dir): path.read_bytes()
        for path in first_dir.rglob("*")
        if path.is_file()
    } == first_snapshot
    assert not (report_path.parent / "d6_phase_b_evidence" / second_id).exists()
    assert not list((report_path.parent / "d6_phase_b_evidence").glob(".*.staging-*"))

    report_switch_attempts = 0

    def fail_first_report_switch(path: Path, payload: bytes) -> None:
        nonlocal report_switch_attempts
        if path == report_path and report_switch_attempts == 0:
            report_switch_attempts += 1
            raise OSError("injected report switch failure")
        original_atomic_write(path, payload)

    monkeypatch.setattr(
        d6_regenerate, "_atomic_write_verified", fail_first_report_switch
    )
    with pytest.raises(OSError, match="injected report switch failure"):
        d6_regenerate._publish_phase_b_bundle(
            report_path,
            second_versioned,
            b'{"generation":"second"}\n',
            bundle_id=second_id,
            protected_inputs=(),
        )
    assert report_path.read_bytes() == first_report
    assert {
        path.relative_to(first_dir): path.read_bytes()
        for path in first_dir.rglob("*")
        if path.is_file()
    } == first_snapshot
    second_dir = report_path.parent / "d6_phase_b_evidence" / second_id
    assert {
        path.relative_to(second_dir): path.read_bytes()
        for path in second_dir.rglob("*")
        if path.is_file()
    } == {
        path.relative_to(d6_regenerate.PHASE_B_EVIDENCE_PATH): payload
        for path, payload in second_leaf.items()
    }
    assert not list((report_path.parent / "d6_phase_b_evidence").glob(".*.staging-*"))


def test_phase_b_publication_requires_and_protects_every_composer_input(
    tmp_path: Path,
) -> None:
    groups = d6_regenerate.group_paths(tmp_path / "work")
    arguments = tuple(tmp_path / name for name in (
        "regenerated.json",
        "synthetic.json",
        "real-report.json",
        "real-manifest.json",
        "recovered.onnx",
    ))
    protected = set(d6_regenerate._phase_b_composer_inputs(*arguments, groups))
    expected = {
        *(path.resolve() for path in arguments),
        d6_regenerate.D4_BASELINE.resolve(),
        d6_regenerate.FIXED_PROBE_PINS.resolve(),
        d6_regenerate.D4_SPEC.resolve(),
        d6_regenerate.D4_RUNNER.resolve(),
        d6_regenerate.TRF_SPEC.resolve(),
        d6_regenerate.CALIBRATION_PINS.resolve(),
        d6_regenerate.REAL_RENDER_BASELINE.resolve(),
        Path(d6_regenerate.__file__).resolve(),
        *(group.render_doc.resolve() for group in groups),
        *(group.render_manifest.resolve() for group in groups),
    }
    assert protected == expected
    parameter = inspect.signature(
        d6_regenerate._publish_phase_b_bundle
    ).parameters["protected_inputs"]
    assert parameter.default is inspect.Parameter.empty


def test_phase_b_outputs_reject_overlap_with_recovered_input(tmp_path: Path) -> None:
    root = tmp_path / "d6work"
    recovered_inside_root = root / d6_regenerate.PHASE_B_REPORT_PATH
    with pytest.raises(d6_regenerate.RegenerationError, match="保護入力"):
        d6_regenerate._preflight_protected_inputs(root, (recovered_inside_root,))
    assert not root.exists()

    recovered = tmp_path / "operator-recovered.onnx"
    recovered.write_bytes(b"only recovered copy")
    report_path = recovered
    leaf = {d6_regenerate.PHASE_B_EVIDENCE_PATH / "a.json": b"evidence"}
    bundle_id = d6_regenerate._phase_b_evidence_bundle_id(leaf)
    versioned = {
        d6_regenerate._phase_b_bundle_path(bundle_id, path): payload
        for path, payload in leaf.items()
    }
    with pytest.raises(d6_regenerate.RegenerationError, match="保護入力"):
        d6_regenerate._publish_phase_b_bundle(
            report_path,
            versioned,
            b'{"verdict":"PASS"}\n',
            bundle_id=bundle_id,
            protected_inputs=(recovered,),
        )
    assert recovered.read_bytes() == b"only recovered copy"
    assert not (tmp_path / "d6_phase_b_evidence").exists()


def test_phase_b_cli_report_uses_repository_relative_layout(tmp_path: Path) -> None:
    root = (tmp_path / "d6work").resolve()
    assert d6_regenerate._phase_b_report_path(root) == (
        root / d6_regenerate.PHASE_B_REPORT_PATH
    )
    assert d6_regenerate._phase_b_report_path(root).relative_to(root) == (
        d6_regenerate.PHASE_B_REPORT_PATH
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
    assert d6_regenerate.build_measure_command(root)[0] == str(
        root / "venv_render" / "bin" / "python"
    )


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
    for axis, axis_value in reference["max_abs_delta_by_axis"].items():
        assert isinstance(axis_value, (int, float)) and 0 <= axis_value <= TRF_EPSILONS[axis]
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


def _assert_pending_or_resolved(
    node: Dict[str, Any], *, context: str, report_root: Path = _REPO_ROOT
) -> None:
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
        try:
            d6_regenerate.validate_resolved_reconciliation(node, report_root=report_root)
        except (d6_regenerate.RegenerationError, OSError) as exc:
            raise AssertionError(f"{context}: Phase B report binding が不正: {exc}") from exc
    else:
        assert re.fullmatch(r"[0-9a-f]{40}", str(value or "")), (
            f"{context}: RESOLVED の value が完全な git SHA でない"
        )


@pytest.mark.parametrize(
    ("context", "node"),
    [
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
        "report_binding",
    }
    assert schema["report_binding"] == {
        "path": str(d6_regenerate.PHASE_B_REPORT_PATH),
        "required_keys": ["path", "sha256"],
        "verification": (
            "report の実 bytes sha256・裁定値の逐語一致・checkout-stableな"
            "23証拠成果物からのproduction/calibration全裁定再構成"
        ),
    }
    assert schema["reference_output_remeasurement"]["baseline_results_sha256"] == (
        D4_RESULTS_SHA256
    )
    assert schema["reference_output_remeasurement"]["max_abs_delta_upper_bound_by_axis"] == (
        TRF_EPSILONS
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
