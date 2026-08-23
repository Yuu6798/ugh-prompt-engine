"""VG-DEBT-006 の固定 probe と合成校正出力を再生成する実行体。

例（取得から 360 セル再測定まで）::

    python3 voice_genesis/foundry/debt/d6/d6_regenerate.py \
        --root /home/user/d6work \
        --real-render-acoustic-onnx \
        /home/user/d6-real-render-input/s6_run7_acoustic.onnx

``--plan`` は副作用なしで、同じ絶対パスを使う全コマンドを表示する。通常実行は
pin 照合つき provision、合成校正13条件の再生成照合、10群の witnessed export、
履歴実装と回収済み acoustic ONNX によるreal-render校正14条件の再生成照合、
10群 render、生成された render manifest の実 sha256 を束縛した measure の順に
fail-closed で進む。real-render acoustic ONNX は再exportで代替できないため、未回収
なら開始前に停止する。動的 digest は本実行体が実ファイルから計算する。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shlex
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

HERE = Path(__file__).resolve().parent
FOUNDRY = HERE.parent.parent
REPO_ROOT = FOUNDRY.parent.parent
PROVISION = FOUNDRY / "run8" / "provision.sh"
EXPORTER = FOUNDRY / "run8" / "s7_export_manifest.py"
D4_RUNNER = FOUNDRY / "debt" / "d4" / "d4_runner.py"
D4_SPEC = FOUNDRY / "debt" / "d4" / "d4_remeasure_spec.json"
CALIBRATION_PINS = HERE / "s7_synthetic_calibration_output_pins.json"
FIXED_PROBE_PINS = HERE / "s7_fixed_probe_pins.json"
REAL_RENDER_BASELINE = FOUNDRY / "results_s7" / "s7_b1_real_render_manifest.json"
REAL_RENDER_BASELINE_SHA256 = (
    "bde66f3f8599ea48d6e8ff8fdc63a362c6aed5f846655189bf3b52c5628d3343"
)
REAL_RENDER_HISTORICAL_COMMIT = "8a14ca97eda1a6bf96f956a8173f512f0cdb50ae"
_HISTORICAL_REAL_RENDER_IMPORT_CLOSURE = (
    ("gate_synth_sha256", Path("voice_genesis/foundry/s1_gate/gate_synth.py")),
    (
        "s7_calib_render_sha256",
        Path("voice_genesis/foundry/run8/s7_calib_render.py"),
    ),
    (
        "s7_calib_score_sha256",
        Path("voice_genesis/foundry/run8/s7_calib_score.py"),
    ),
    ("s7_io_sha256", Path("voice_genesis/foundry/run8/s7_io.py")),
    ("s7_spec_sha256", Path("voice_genesis/foundry/run8/s7_spec.py")),
)
_HISTORICAL_REAL_RENDER_PREREG = Path(
    "voice_genesis/foundry/results_s7/s7_b1_calibration_set.json"
)
REAL_RENDER_ACOUSTIC_SHA256 = "f0e71f06b16e448622f3e0d9b977a26fbaa306bb608a08ed26efeb871332a7d1"
D4_BASELINE = FOUNDRY / "debt" / "d4" / "d4_results_2026-08-22.json"
D4_BASELINE_SHA256 = "6b820a2a27744b9ed4f6e873231aa103b57dd622f993982a112063e5b4bacfa7"
TRF_SPEC = FOUNDRY / "results_s7" / "trf_measurement_spec_1_2.json"
RECONCILIATION_AXES = (
    "excess_tail_voiced_ms",
    "release_after_score_boundary_ms",
    "tail_f0_persistence",
)
PHASE_B_REPORT_PATH = Path(
    "voice_genesis/foundry/debt/d6/d6_phase_b_reconciliation.json"
)
PHASE_B_EVIDENCE_PATH = PHASE_B_REPORT_PATH.parent / "d6_phase_b_evidence"
PHASE_B_RESULTS_PATH = PHASE_B_EVIDENCE_PATH / "d6_regenerated_results.json"
PHASE_B_SYNTHETIC_PATH = (
    PHASE_B_EVIDENCE_PATH / "calibration_synthetic_reconciliation.json"
)
PHASE_B_REAL_RENDER_MANIFEST_PATH = (
    PHASE_B_EVIDENCE_PATH / "calibration_real_render_manifest.json"
)
_D4_RUNTIME_STACK_KEYS = {"python", "numpy", "onnxruntime", "soundfile", "PyYAML"}
_D4_MEASUREMENT_DEPENDENCY_KEYS = {"pyloudnorm", "scipy"}
_INTERNAL_SYNTHETIC_CALIBRATION_FLAG = "--internal-synthetic-calibration-out"

GROUPS = (
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
)


class RegenerationError(RuntimeError):
    """固定契約を満たさないため再生成を続行できない。"""


@dataclass(frozen=True)
class GroupPaths:
    generation: str
    speaker: str
    export_dir: Path
    export_manifest: Path
    render_dir: Path
    render_doc: Path
    render_manifest: Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_lower_hex(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(char in "0123456789abcdef" for char in value)
    )


def verify_runner_pins() -> None:
    """再生成が起動する実装を、D6 pin inventory の値へ照合する。"""
    pins = json.loads(FIXED_PROBE_PINS.read_text(encoding="utf-8"))
    common = pins["common_fixed"]
    refs = [
        pins["production_cells"]["refs"]["d4_1_2_remeasurement"],
        pins["calibration_set"]["refs"]["synthetic_stimuli"],
        pins["calibration_set"]["refs"]["synthetic_output_pins"],
        pins["calibration_set"]["refs"]["real_render_manifest"],
        common["material_acquisition_command"]["provisioner"],
        common["regeneration_commands"]["orchestrator"],
        common["regeneration_commands"]["d4_runner"],
        common["regeneration_commands"]["export_manifest_runner"],
        common["regeneration_commands"]["synthetic_calibration_generator"],
        common["regeneration_commands"]["d4_spec"],
    ]
    mismatches = []
    for ref in refs:
        path = REPO_ROOT / ref["path"]
        observed = sha256_file(path) if path.is_file() else "MISSING"
        if observed != ref["sha256"]:
            mismatches.append(f"{ref['path']}: {observed} != {ref['sha256']}")
    if mismatches:
        raise RegenerationError("再生成 runner pin 不一致: " + "; ".join(mismatches))


def _group_paths(root: Path, generation: str, speaker: str) -> GroupPaths:
    group = f"{generation}_{speaker}"
    render_doc = root / "render" / f"{group}.json"
    return GroupPaths(
        generation=generation,
        speaker=speaker,
        export_dir=root / "out" / f"export_{group}",
        export_manifest=root / "out" / f"export_{group}" / "export_manifest.json",
        render_dir=root / "render" / group,
        render_doc=render_doc,
        render_manifest=render_doc.with_name(f"{render_doc.stem}_render_manifest.json"),
    )


def group_paths(root: Path) -> tuple[GroupPaths, ...]:
    return tuple(_group_paths(root, generation, speaker) for generation, speaker in GROUPS)


def _phase_b_composer_inputs(
    regenerated_results_path: Path,
    synthetic_report_path: Path,
    real_render_report_path: Path,
    real_render_manifest_path: Path,
    recovered_acoustic_path: Path,
    rendered_groups: Sequence[GroupPaths],
) -> tuple[Path, ...]:
    """Phase B composerが読む全ファイルをpublication保護集合へ閉じる。"""
    inputs = {
        Path(regenerated_results_path).resolve(),
        Path(synthetic_report_path).resolve(),
        Path(real_render_report_path).resolve(),
        Path(real_render_manifest_path).resolve(),
        Path(recovered_acoustic_path).resolve(),
        D4_BASELINE.resolve(),
        FIXED_PROBE_PINS.resolve(),
        D4_SPEC.resolve(),
        D4_RUNNER.resolve(),
        TRF_SPEC.resolve(),
        CALIBRATION_PINS.resolve(),
        REAL_RENDER_BASELINE.resolve(),
        Path(__file__).resolve(),
    }
    for group in rendered_groups:
        inputs.add(group.render_doc.resolve())
        inputs.add(group.render_manifest.resolve())
    return tuple(sorted(inputs, key=str))


def _checkpoint_dir(root: Path, generation: str) -> Path:
    if generation in {"run5", "run6"}:
        return root / "materials" / "ckpts" / f"{generation}_bundle"
    return root / "materials" / "run7_ckpt"


def _render_python(root: Path) -> str:
    return str(root / "venv_render" / "bin" / "python")


def _phase_b_report_path(root: Path) -> Path:
    """作業 root 内に checkout と同じ repository-relative 配置を返す。"""
    return Path(root).resolve() / PHASE_B_REPORT_PATH


def _paths_overlap(left: Path, right: Path) -> bool:
    left = Path(left).resolve()
    right = Path(right).resolve()
    return left == right or left in right.parents or right in left.parents


def _preflight_protected_inputs(root: Path, protected_inputs: Iterable[Path]) -> None:
    """生成 root と回収資産が重なる実行を、最初の書込みより前に拒否する。"""
    resolved_root = Path(root).resolve()
    for protected in protected_inputs:
        resolved = Path(protected).resolve()
        if _paths_overlap(resolved_root, resolved):
            raise RegenerationError(
                "生成 root が保護入力と重なる: "
                f"root={resolved_root}, protected={resolved}"
            )


def build_provision_command(root: Path) -> list[str]:
    return ["bash", str(PROVISION), "--root", str(root)]


def build_synthetic_calibration_command(
    root: Path, *, python_executable: str | None = None
) -> list[str]:
    """合成校正を、provision 後に照合する絶対パスの interpreter で実行する。"""
    return [
        python_executable or _render_python(root),
        str(Path(__file__).resolve()),
        _INTERNAL_SYNTHETIC_CALIBRATION_FLAG,
        str(Path(root).resolve() / "calibration_synthetic"),
    ]


def build_export_command(root: Path, group: GroupPaths) -> list[str]:
    stem = f"s6_{group.generation}_acoustic"
    export_python = root / "venv_export" / "bin" / "python"
    return [
        str(export_python),
        str(EXPORTER),
        "--generation",
        group.generation,
        "--exporter-root",
        str(root / "materials" / "DiffSinger"),
        "--exp",
        f"d6_{group.generation}_{group.speaker}",
        "--ckpt-steps",
        "40000",
        "--ckpt-dir",
        str(_checkpoint_dir(root, group.generation)),
        "--out-dir",
        str(group.export_dir),
        "--artifact",
        f"acoustic_onnx={stem}.onnx",
        "--artifact",
        "acoustic_dsconfig=dsconfig.yaml",
        "--artifact",
        f"acoustic_phonemes_json={stem}.phonemes.json",
        "--artifact",
        f"speaker_embed={stem}.{group.speaker}.emb",
        "--out",
        str(group.export_manifest),
    ]


def build_render_command(
    root: Path, group: GroupPaths, *, python_executable: str | None = None
) -> list[str]:
    stem = f"s6_{group.generation}_acoustic"
    canon = root / "materials" / "extracted" / "ds" / "NamineRitsu_DiffSinger"
    return [
        python_executable or _render_python(root),
        str(D4_RUNNER),
        "render",
        "--generation",
        group.generation,
        "--speaker",
        group.speaker,
        "--acoustic-dir",
        str(group.export_dir),
        "--acoustic-stem",
        stem,
        "--export-manifest",
        str(group.export_manifest),
        "--canon-model-dir",
        str(canon),
        "--vocoder-dir",
        str(root / "materials" / "vocoder_onnx"),
        "--canon-phonemes-txt",
        str(canon / "phonemes.txt"),
        "--out-dir",
        str(group.render_dir),
        "--result-out",
        str(group.render_doc),
        "--spec-sha256",
        sha256_file(D4_SPEC),
    ]


def _real_render_source_root(root: Path) -> Path:
    return root / "historical_source" / REAL_RENDER_HISTORICAL_COMMIT


def build_real_render_command(
    root: Path,
    acoustic_onnx: Path,
    *,
    python_executable: str | None = None,
) -> list[str]:
    """成功したB-1 real-renderを生んだ履歴実装で14条件を再生成する。"""
    source = _real_render_source_root(root)
    foundry = source / "voice_genesis" / "foundry"
    export = _group_paths(root, "run7", "ritsu").export_dir
    stem = "s6_run7_acoustic"
    canon = root / "materials" / "extracted" / "ds" / "NamineRitsu_DiffSinger"
    return [
        python_executable or _render_python(root),
        str(foundry / "run8" / "s7_calib_render.py"),
        "--canon-model-dir",
        str(canon),
        "--vocoder-dir",
        str(root / "materials" / "vocoder_onnx"),
        "--acoustic-onnx",
        str(acoustic_onnx),
        "--acoustic-dsconfig",
        str(export / "dsconfig.yaml"),
        "--acoustic-phonemes-json",
        str(export / f"{stem}.phonemes.json"),
        "--canon-phonemes-txt",
        str(canon / "phonemes.txt"),
        "--speaker",
        "ritsu",
        "--speaker-emb",
        str(export / f"{stem}.ritsu.emb"),
        "--ckpt",
        str(root / "materials" / "run7_ckpt" / "model_ckpt_steps_40000.ckpt"),
        "--canon-zip",
        str(root / "materials" / "NamineRitsu_DiffSinger.zip"),
        "--vocoder-container",
        str(root / "materials" / "nsf_hifigan.oudep"),
        "--out-dir",
        str(root / "calibration_real_render"),
        "--manifest-out",
        str(root / "calibration_real_render_manifest.json"),
    ]


def build_measure_command(
    root: Path, *, python_executable: str | None = None
) -> list[str]:
    command = [python_executable or _render_python(root), str(D4_RUNNER), "measure"]
    groups = group_paths(root)
    missing = [
        str(group.render_manifest) for group in groups if not group.render_manifest.is_file()
    ]
    if missing:
        raise RegenerationError(
            "measure の信頼根となる render manifest が未生成: " + ", ".join(missing)
        )
    for group in groups:
        command.extend(["--render-doc", str(group.render_doc)])
    for group in groups:
        command.extend(["--render-manifest", str(group.render_manifest)])
    for group in groups:
        command.extend(["--render-manifest-sha256", sha256_file(group.render_manifest)])
    command.extend(
        [
            "--out",
            str(root / "d6_regenerated_results.json"),
            "--spec-sha256",
            sha256_file(D4_SPEC),
        ]
    )
    return command


def static_plan(
    root: Path, *, python_executable: str | None = None
) -> tuple[list[str], tuple[list[str], ...], tuple[list[str], ...]]:
    groups = group_paths(root)
    return (
        build_provision_command(root),
        tuple(build_export_command(root, group) for group in groups),
        tuple(
            build_render_command(root, group, python_executable=python_executable)
            for group in groups
        ),
    )


def _verify_file_pin(path: Path, expected: str, *, label: str) -> None:
    if not path.is_file():
        raise RegenerationError(f"{label}: 必須の固定資産が無い: {path}")
    observed = sha256_file(path)
    if observed != expected:
        raise RegenerationError(f"{label}: sha256 {observed} != pin {expected}")


def _historical_git_object_sha256(relative_path: Path) -> str:
    """固定commitが持つ履歴source bytesのsha256をGit objectから得る。"""
    observed = subprocess.run(
        [
            "git",
            "show",
            f"{REAL_RENDER_HISTORICAL_COMMIT}:{relative_path.as_posix()}",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
    )
    if observed.returncode != 0:
        raise RegenerationError(
            "historical source Git objectを読めない: "
            f"{relative_path}: {observed.stderr.decode(errors='replace').strip()}"
        )
    return hashlib.sha256(observed.stdout).hexdigest()


def verify_real_render_inputs(root: Path, acoustic_onnx: Path) -> None:
    """履歴real-renderが消費する再配布外の4資産をレンダ前に照合する。"""
    baseline = json.loads(REAL_RENDER_BASELINE.read_text(encoding="utf-8"))
    model = baseline["render_path"]["model_sha256"]
    aux = baseline["render_path"]["aux_sha256"]
    export = _group_paths(root, "run7", "ritsu").export_dir
    stem = "s6_run7_acoustic"
    for path, expected, label in (
        (Path(acoustic_onnx), REAL_RENDER_ACOUSTIC_SHA256, "historical acoustic ONNX"),
        (export / "dsconfig.yaml", model["acoustic_dsconfig_yaml"], "acoustic dsconfig"),
        (
            export / f"{stem}.phonemes.json",
            aux["acoustic_phonemes_json"],
            "acoustic phonemes",
        ),
        (export / f"{stem}.ritsu.emb", aux["speaker_embed"], "Ritsu embedding"),
    ):
        _verify_file_pin(path, expected, label=label)


def verify_real_render_stack(python_executable: str | Path) -> None:
    """B-1 manifest が束縛した数値実行環境以外で履歴renderを走らせない。"""
    baseline = json.loads(REAL_RENDER_BASELINE.read_text(encoding="utf-8"))
    expected = baseline["render_stack"]
    check = subprocess.run(
        [
            str(python_executable),
            "-c",
            (
                "import importlib.metadata as m,json,platform;"
                "print(json.dumps({"
                "'numpy':m.version('numpy'),"
                "'onnxruntime':m.version('onnxruntime'),"
                "'soundfile':m.version('soundfile'),"
                "'PyYAML':m.version('PyYAML'),"
                "'python':platform.python_version()}))"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        raise RegenerationError(
            f"render runtime を検査できない: {python_executable}: {check.stderr.strip()}"
        )
    observed = json.loads(check.stdout)
    if observed != expected:
        raise RegenerationError(
            f"real-render execution profile mismatch: {observed} != pin {expected}"
        )


def _verify_historical_source(source: Path) -> None:
    baseline = json.loads(REAL_RENDER_BASELINE.read_text(encoding="utf-8"))
    aux = baseline["render_path"]["aux_sha256"]
    baseline_keys = {
        "gate_synth_sha256": "gate_synth_py",
        "s7_calib_render_sha256": "s7_calib_render_py",
        "s7_calib_score_sha256": "s7_calib_score_py",
    }
    for label, relative_path in _HISTORICAL_REAL_RENDER_IMPORT_CLOSURE:
        expected = _historical_git_object_sha256(relative_path)
        baseline_key = baseline_keys.get(label)
        if baseline_key is not None and aux.get(baseline_key) != expected:
            raise RegenerationError(
                f"historical source {label}: baseline digest が固定commitと不一致"
            )
        _verify_file_pin(
            source / relative_path,
            expected,
            label=f"historical source {label}",
        )
    prereg_expected = _historical_git_object_sha256(_HISTORICAL_REAL_RENDER_PREREG)
    if baseline["prereg"].get("sha256") != prereg_expected:
        raise RegenerationError(
            "historical source real-render prereg: baseline digest が固定commitと不一致"
        )
    _verify_file_pin(
        source / _HISTORICAL_REAL_RENDER_PREREG,
        prereg_expected,
        label="historical source real-render prereg",
    )


def materialize_historical_real_render_source(root: Path) -> Path:
    """Git objectからreal-render実行時のsource treeを毎回fresh展開する。"""
    target = _real_render_source_root(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".real-render-source-", dir=target.parent))
    backup_root: Path | None = None
    archive_fd, archive_name = tempfile.mkstemp(prefix=".real-render-", suffix=".tar", dir=root)
    os.close(archive_fd)
    archive = Path(archive_name)
    try:
        subprocess.run(
            [
                "git",
                "archive",
                "--format=tar",
                f"--output={archive}",
                REAL_RENDER_HISTORICAL_COMMIT,
                "voice_genesis",
                "pyproject.toml",
            ],
            cwd=REPO_ROOT,
            check=True,
        )
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                destination = (staging / member.name).resolve()
                if (
                    staging.resolve() not in destination.parents
                    and destination != staging.resolve()
                ):
                    raise RegenerationError(f"historical archive path escape: {member.name}")
                if not (member.isfile() or member.isdir()):
                    raise RegenerationError(
                        f"historical archive に通常ファイル以外が含まれる: {member.name}"
                    )
                tar.extract(member, staging, filter="fully_trusted")
        _verify_historical_source(staging)
        if target.exists():
            backup_root = Path(
                tempfile.mkdtemp(prefix=".real-render-source-backup-", dir=target.parent)
            )
            target.rename(backup_root / "previous")
        try:
            staging.rename(target)
        except BaseException:
            if backup_root is not None and not target.exists():
                (backup_root / "previous").rename(target)
            raise
        if backup_root is not None:
            shutil.rmtree(backup_root)
            backup_root = None
    finally:
        archive.unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging)
        if backup_root is not None and backup_root.exists():
            if not target.exists() and (backup_root / "previous").exists():
                (backup_root / "previous").rename(target)
            shutil.rmtree(backup_root)
    return target


def _reconcile_real_render_data(
    baseline: dict[str, Any], observed: dict[str, Any]
) -> dict[str, Any]:
    """real-render manifest 14条件を比較し、再構成可能な裁定値を返す。"""
    if observed.get("schema") != baseline.get("schema"):
        raise RegenerationError("real-render manifest schema mismatch")
    expected_conditions = {entry["condition_id"]: entry for entry in baseline["conditions"]}
    observed_conditions = {entry["condition_id"]: entry for entry in observed["conditions"]}
    if (
        len(expected_conditions) != len(baseline["conditions"])
        or len(observed_conditions) != len(observed["conditions"])
        or expected_conditions.keys() != observed_conditions.keys()
    ):
        raise RegenerationError("real-render condition集合が基準14条件と一致しない")
    for counter in ("n_rendered", "n_derived", "n_zero_buffers"):
        if observed.get(counter) != baseline.get(counter):
            raise RegenerationError(
                f"real-render {counter} mismatch: {observed.get(counter)} != {baseline.get(counter)}"
            )
    sample_mismatches = []
    wav_matches = 0
    for condition_id, expected in expected_conditions.items():
        actual = observed_conditions[condition_id]
        if actual.get("samples_sha256") != expected.get("samples_sha256"):
            sample_mismatches.append(condition_id)
        if actual.get("wav_sha256") == expected.get("wav_sha256"):
            wav_matches += 1
    path_mismatches = {}
    for section in ("model_sha256", "aux_sha256"):
        expected_pins = baseline["render_path"][section]
        actual_pins = observed["render_path"].get(section, {})
        for key, expected in expected_pins.items():
            if actual_pins.get(key) != expected:
                path_mismatches[f"{section}.{key}"] = (expected, actual_pins.get(key))
    if sample_mismatches or path_mismatches:
        raise RegenerationError(
            "real-render reconciliation mismatch: "
            f"samples={sample_mismatches}, render_path={path_mismatches}"
        )
    return {
        "n_rendered": int(observed["n_rendered"]),
        "n_compared": len(expected_conditions),
        "samples_matches": len(expected_conditions),
        "samples_mismatches": [],
        "wav_container_matches": wav_matches,
        "wav_container_mismatches": len(expected_conditions) - wav_matches,
    }


def reconcile_real_render_manifest(observed_path: Path, report_path: Path) -> dict[str, Any]:
    """履歴real-render全14条件の標本pinを基準manifestへ突き合わせる。"""
    baseline_bytes = REAL_RENDER_BASELINE.read_bytes()
    observed_bytes = Path(observed_path).read_bytes()
    value = _reconcile_real_render_data(
        json.loads(baseline_bytes), json.loads(observed_bytes)
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    report = {
        "schema": "vg-d6-real-render-calibration-reconciliation/0.1",
        "verdict": "PASS",
        "value": value,
        "baseline_manifest_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
        "regenerated_manifest_sha256": hashlib.sha256(observed_bytes).hexdigest(),
        "recovered_acoustic_sha256": REAL_RENDER_ACOUSTIC_SHA256,
        "historical_source_commit": REAL_RENDER_HISTORICAL_COMMIT,
        "execution_commit": commit,
    }
    Path(report_path).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _measured_cells(
    results: dict[str, Any], *, label: str
) -> dict[tuple[str, str], dict[str, Any]]:
    expected_groups = {f"{generation}_{speaker}" for generation, speaker in GROUPS}
    groups = results.get("groups")
    if not isinstance(groups, dict) or set(groups) != expected_groups:
        raise RegenerationError(f"{label}: D4 group集合が固定10群と一致しない")
    cells: dict[tuple[str, str], dict[str, Any]] = {}
    for group_id, group in groups.items():
        group_cells = group.get("cells")
        if not isinstance(group_cells, dict) or len(group_cells) != 36:
            raise RegenerationError(f"{label}: {group_id} が固定36セルでない")
        for cell_id, cell in group_cells.items():
            if cell.get("outcome") != "measured":
                raise RegenerationError(f"{label}: {group_id}/{cell_id} がmeasuredでない")
            if set(cell.get("axes", {})) != set(RECONCILIATION_AXES):
                raise RegenerationError(f"{label}: {group_id}/{cell_id} の3軸が不完全")
            cells[(group_id, cell_id)] = cell
    if len(cells) != 360:
        raise RegenerationError(f"{label}: measured cell数 {len(cells)} != 360")
    return cells


def _reconcile_production_measurements(
    baseline: dict[str, Any],
    regenerated: dict[str, Any],
    trf_spec: dict[str, Any],
    *,
    baseline_sha: str,
    regenerated_sha: str,
) -> dict[str, Any]:
    """360セルの実測からproduction裁定値を決定論的に再構成する。"""
    expected_cells = _measured_cells(baseline, label="baseline")
    actual_cells = _measured_cells(regenerated, label="regenerated")
    if expected_cells.keys() != actual_cells.keys():
        raise RegenerationError("Phase B: baseline/regenerated cell集合が一致しない")
    try:
        epsilons = {
            axis: float(trf_spec["axes"][axis]["epsilon"])
            for axis in RECONCILIATION_AXES
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise RegenerationError("Phase B: TRF epsilon pin が不完全") from exc
    if any(not math.isfinite(value) or value < 0 for value in epsilons.values()):
        raise RegenerationError("Phase B: TRF epsilon pin が有限の非負値でない")

    max_deltas = {axis: 0.0 for axis in RECONCILIATION_AXES}
    n_within_epsilon = samples_matches = wav_matches = 0
    for key, expected in expected_cells.items():
        actual = actual_cells[key]
        within = True
        for axis in RECONCILIATION_AXES:
            expected_value = float(expected["axes"][axis])
            actual_value = float(actual["axes"][axis])
            if not (math.isfinite(expected_value) and math.isfinite(actual_value)):
                raise RegenerationError(f"Phase B: {key} {axis} が有限値でない")
            delta = abs(actual_value - expected_value)
            max_deltas[axis] = max(max_deltas[axis], delta)
            within = within and delta <= epsilons[axis]
        n_within_epsilon += int(within)
        samples_matches += int(
            actual.get("samples_sha256") == expected.get("samples_sha256")
        )
        wav_matches += int(actual.get("wav_sha256") == expected.get("wav_sha256"))

    return {
        "reference_output_remeasurement": {
            "n_compared": 360,
            "n_within_epsilon": n_within_epsilon,
            "n_mismatches": 360 - n_within_epsilon,
            "baseline_results_sha256": baseline_sha,
            "regenerated_results_sha256": regenerated_sha,
            "max_abs_delta_by_axis": max_deltas,
        },
        "samples_sha256": {
            "n_compared": 360,
            "n_matches": samples_matches,
            "n_mismatches": 360 - samples_matches,
            "baseline_inventory_sha256": baseline_sha,
            "regenerated_inventory_sha256": regenerated_sha,
        },
        "wav_sha256": {
            "n_compared": 360,
            "n_matches": wav_matches,
            "n_mismatches": 360 - wav_matches,
            "baseline_inventory_sha256": baseline_sha,
            "regenerated_inventory_sha256": regenerated_sha,
        },
    }


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _phase_b_bundle_path(bundle_id: str, path: Path) -> Path:
    if not _is_lower_hex(bundle_id, 64):
        raise RegenerationError("Phase B evidence bundle id が不正")
    try:
        suffix = path.relative_to(PHASE_B_EVIDENCE_PATH)
    except ValueError as exc:
        raise RegenerationError(f"Phase B evidence path が正本配下でない: {path}") from exc
    return PHASE_B_EVIDENCE_PATH / bundle_id / suffix


def _phase_b_group_refs(
    group_id: str, *, bundle_id: str | None = None
) -> tuple[Path, Path]:
    refs = (
        PHASE_B_EVIDENCE_PATH / f"{group_id}.json",
        PHASE_B_EVIDENCE_PATH / f"{group_id}_render_manifest.json",
    )
    if bundle_id is None:
        return refs
    return tuple(_phase_b_bundle_path(bundle_id, path) for path in refs)


def _bound_ref(path: Path, digest: str) -> dict[str, str]:
    return {"path": str(path), "sha256": digest}


def _phase_b_evidence_bundle_id(artifacts: dict[Path, bytes]) -> str:
    entries = []
    for path, payload in artifacts.items():
        try:
            suffix = path.relative_to(PHASE_B_EVIDENCE_PATH)
        except ValueError as exc:
            raise RegenerationError(
                f"Phase B evidence path が正本配下でない: {path}"
            ) from exc
        entries.append(
            {"path": str(suffix), "sha256": hashlib.sha256(payload).hexdigest()}
        )
    return _canonical_json_sha256(sorted(entries, key=lambda entry: entry["path"]))


def _execution_profile_from_pins(fixed_probe_pins: dict[str, Any]) -> dict[str, str]:
    """D6が許すrender runtimeを固定pinから厳密に取り出す。"""
    profile = (
        fixed_probe_pins.get("common_fixed", {})
        .get("execution_profile", {})
        .get("value")
    )
    if (
        not isinstance(profile, dict)
        or set(profile) != _D4_RUNTIME_STACK_KEYS
        or any(not isinstance(value, str) or not value for value in profile.values())
    ):
        raise RegenerationError("Phase B: execution_profile pin が固定runtime形状でない")
    return dict(profile)


def _measurement_dependency_profile_from_pins(
    fixed_probe_pins: dict[str, Any],
) -> dict[str, str]:
    """D4測定の数値経路へ入る追加依存を固定pinから取り出す。"""
    profile = (
        fixed_probe_pins.get("common_fixed", {})
        .get("execution_profile", {})
        .get("measurement_dependencies", {})
        .get("value")
    )
    if (
        not isinstance(profile, dict)
        or set(profile) != _D4_MEASUREMENT_DEPENDENCY_KEYS
        or any(not isinstance(value, str) or not value for value in profile.values())
    ):
        raise RegenerationError("Phase B: measurement dependency pin が固定形状でない")
    return dict(profile)


def _validate_regenerated_provenance(
    regenerated: dict[str, Any],
    *,
    baseline: dict[str, Any],
    d4_spec: dict[str, Any],
    d4_spec_sha: str,
    rendered_groups: Sequence[GroupPaths],
    expected_render_runtime: dict[str, str],
    expected_measurement_dependencies: dict[str, str],
    require_result_path_match: bool = True,
    bundle_id: str | None = None,
) -> tuple[dict[str, Any], dict[Path, bytes]]:
    """再測定結果を、この Phase B 実行が直前に作った10群へ結合する。

    D4 measure 自身も manifest を検証するが、最終合成器は従来その結合を
    再確認せず、過去結果 JSON のコピーでも360セルさえ整えば受理していた。
    ここでは manifest と render doc を各1回だけ読み、同じ bytes の digest と
    parse 結果から、群結果・材料 pin・runtime provenance を再結合する。
    """
    expected_top = {
        "schema": "vg-d4-remeasure-results/0.1",
        "debt_ref": "VG-DEBT-004",
        "generated_by": "voice_genesis/foundry/debt/d4/d4_runner.py",
        "d4_remeasure_spec_sha256": d4_spec_sha,
        "d4_remeasure_spec_path": "voice_genesis/foundry/debt/d4/d4_remeasure_spec.json",
        "trf_measurement_spec_1_2_sha256": d4_spec["pins"][
            "trf_measurement_spec_1_2_sha256"
        ],
        "instrument_sha256": d4_spec["pins"]["instrument_sha256"],
        "candidate_ids": baseline["candidate_ids"],
        "analysis_stack": baseline["analysis_stack"],
        "n_groups": 10,
        "n_total_cells": 360,
        "n_total_measured": 360,
        "n_total_error": 0,
        "complete": True,
    }
    mismatches = {
        key: {"expected": expected, "actual": regenerated.get(key)}
        for key, expected in expected_top.items()
        if regenerated.get(key) != expected
    }
    if mismatches:
        raise RegenerationError(f"Phase B: regenerated D4 provenance mismatch: {mismatches}")
    runtime_stack = regenerated.get("runtime_stack")
    if runtime_stack != expected_render_runtime:
        raise RegenerationError(
            "Phase B: regenerated D4 measurement runtime_stack が"
            "common_fixed.execution_profile.value と不一致"
        )
    if regenerated.get("measurement_dependency_stack") != expected_measurement_dependencies:
        raise RegenerationError(
            "Phase B: regenerated D4 measurement dependency stack が固定pinと不一致"
        )

    expected_group_ids = {f"{generation}_{speaker}" for generation, speaker in GROUPS}
    supplied = {
        f"{group.generation}_{group.speaker}": group for group in rendered_groups
    }
    if set(supplied) != expected_group_ids or len(rendered_groups) != len(expected_group_ids):
        raise RegenerationError("Phase B: current render evidence が固定10群と一致しない")

    evidence: dict[str, Any] = {}
    artifact_bytes: dict[Path, bytes] = {}
    for group_id in sorted(expected_group_ids):
        paths = supplied[group_id]
        manifest_bytes = paths.render_manifest.read_bytes()
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        try:
            manifest = json.loads(manifest_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RegenerationError(
                f"Phase B: {group_id} render manifest がJSONでない"
            ) from exc
        doc_bytes = paths.render_doc.read_bytes()
        doc_sha = hashlib.sha256(doc_bytes).hexdigest()
        try:
            doc = json.loads(doc_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RegenerationError(f"Phase B: {group_id} render doc がJSONでない") from exc
        manifest_groups = manifest.get("groups")
        manifest_entry = (
            manifest_groups.get(group_id) if isinstance(manifest_groups, dict) else None
        )
        if (
            manifest.get("schema") != "vg-d4-render-manifest/0.1"
            or not isinstance(manifest_groups, dict)
            or set(manifest_groups) != {group_id}
            or not isinstance(manifest_entry, dict)
            or manifest_entry.get("render_doc_sha256") != doc_sha
            or manifest_entry.get("path") != paths.render_doc.name
        ):
            raise RegenerationError(
                f"Phase B: {group_id} manifest が current render doc を束縛しない"
            )
        render_runtime = doc.get("runtime_stack")
        if (
            doc.get("generation") != paths.generation
            or doc.get("speaker") != paths.speaker
            or doc.get("d4_schema") != "vg-d4-render-group-result/0.1"
            or doc.get("d4_remeasure_spec_sha256") != d4_spec_sha
            or doc.get("d4_remeasure_spec_path")
            != "voice_genesis/foundry/debt/d4/d4_remeasure_spec.json"
            or render_runtime != expected_render_runtime
        ):
            raise RegenerationError(
                f"Phase B: {group_id} render runtime がexecution_profile pinと不一致"
            )

        materials = {
            "model_sha256": doc.get("model_sha256"),
            "aux_sha256": doc.get("aux_sha256"),
            "export_binding": doc.get("export_binding"),
        }
        model_pins = materials["model_sha256"]
        aux_pins = materials["aux_sha256"]
        export_binding = materials["export_binding"]
        if (
            not isinstance(model_pins, dict)
            or not model_pins
            or any(not _is_lower_hex(value, 64) for value in model_pins.values())
            or not isinstance(aux_pins, dict)
            or not aux_pins
            or any(not _is_lower_hex(value, 64) for value in aux_pins.values())
            or not isinstance(export_binding, dict)
            or export_binding.get("binding_evidence") != "witnessed_export"
            or export_binding.get("generation") != paths.generation
            or any(
                not _is_lower_hex(export_binding.get(key), 64)
                for key in (
                    "manifest_sha256",
                    "source_checkpoint_sha256",
                    "source_config_sha256",
                )
            )
        ):
            raise RegenerationError(f"Phase B: {group_id} material pins が不完全")
        result_group = regenerated["groups"][group_id]
        result_path = Path(str(result_group.get("render_doc_path", ""))).resolve()
        if (
            result_group.get("generation") != paths.generation
            or result_group.get("speaker") != paths.speaker
            or (require_result_path_match and result_path != paths.render_doc.resolve())
            or result_group.get("render_doc_sha256") != doc_sha
            or result_group.get("materials_sha256") != materials
            or result_group.get("render_runtime_stack") != render_runtime
            or result_group.get("render_runtime_stack_note") is not None
        ):
            raise RegenerationError(
                f"Phase B: {group_id} regenerated result が current render/materials と不一致"
            )
        doc_ref, manifest_ref = _phase_b_group_refs(group_id, bundle_id=bundle_id)
        evidence[group_id] = {
            "render_manifest": _bound_ref(manifest_ref, manifest_sha),
            "render_doc": _bound_ref(doc_ref, doc_sha),
            "materials_sha256_digest": _canonical_json_sha256(materials),
            "render_runtime_stack": render_runtime,
        }
        artifact_bytes[manifest_ref] = manifest_bytes
        artifact_bytes[doc_ref] = doc_bytes
    return evidence, artifact_bytes


def _atomic_write_verified(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
        if path.read_bytes() != payload:
            raise RegenerationError(f"Phase B evidence readback mismatch: {path}")
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish_phase_b_bundle(
    out_path: Path,
    artifacts: dict[Path, bytes],
    report_payload: bytes,
    *,
    bundle_id: str,
    protected_inputs: Iterable[Path],
) -> None:
    """完全なcontent-addressed bundleを先に公開し、reportを最後に切り替える。"""
    output_parent = Path(out_path).resolve().parent
    bundle_path = PHASE_B_EVIDENCE_PATH / bundle_id
    unversioned_artifacts: dict[Path, bytes] = {}
    for path, payload in artifacts.items():
        try:
            suffix = path.relative_to(bundle_path)
        except ValueError as exc:
            raise RegenerationError(f"Phase B artifact がbundle配下でない: {path}") from exc
        unversioned_artifacts[PHASE_B_EVIDENCE_PATH / suffix] = payload
    if (
        len(unversioned_artifacts) != len(artifacts)
        or _phase_b_evidence_bundle_id(unversioned_artifacts) != bundle_id
    ):
        raise RegenerationError("Phase B content-addressed bundle id が成果物と不一致")
    try:
        bundle_suffix = bundle_path.relative_to(PHASE_B_REPORT_PATH.parent)
    except ValueError as exc:
        raise RegenerationError("Phase B bundle path がreport正本配下でない") from exc
    target = output_parent / bundle_suffix
    report_path = Path(out_path).resolve()
    for protected in protected_inputs:
        resolved = Path(protected).resolve()
        if _paths_overlap(report_path, resolved) or _paths_overlap(target, resolved):
            raise RegenerationError(
                "Phase B 出力が保護入力と重なる: "
                f"report={report_path}, bundle={target}, protected={resolved}"
            )
    target.parent.mkdir(parents=True, exist_ok=True)
    previous_report = report_path.read_bytes() if report_path.is_file() else None
    staging = Path(
        tempfile.mkdtemp(prefix=f".{bundle_id}.staging-", dir=target.parent)
    )
    expected: dict[Path, bytes] = {}
    try:
        for canonical_path, payload in artifacts.items():
            try:
                suffix = canonical_path.relative_to(bundle_path)
            except ValueError as exc:
                raise RegenerationError(
                    f"Phase B artifact がbundle配下でない: {canonical_path}"
                ) from exc
            expected[suffix] = payload
            _atomic_write_verified(staging / suffix, payload)
        _fsync_directory(staging)

        if target.exists():
            actual_files = {
                path.relative_to(target)
                for path in target.rglob("*")
                if path.is_file()
            }
            if actual_files != set(expected) or any(
                (target / suffix).read_bytes() != payload
                for suffix, payload in expected.items()
            ):
                raise RegenerationError(
                    f"Phase B content-addressed bundle が既存bytesと不一致: {bundle_id}"
                )
        else:
            os.replace(staging, target)
            _fsync_directory(target.parent)
        try:
            _atomic_write_verified(report_path, report_payload)
        except BaseException:
            if previous_report is None:
                report_path.unlink(missing_ok=True)
            else:
                _atomic_write_verified(report_path, previous_report)
            raise
        _fsync_directory(output_parent)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _read_bound_bytes(
    report_root: Path, ref: Any, *, expected_path: Path, label: str
) -> bytes:
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
        raise RegenerationError(f"{label}: bound ref shape が不正")
    if ref.get("path") != str(expected_path) or not _is_lower_hex(ref.get("sha256"), 64):
        raise RegenerationError(f"{label}: canonical path/sha256 が不正")
    payload = (Path(report_root).resolve() / expected_path).read_bytes()
    if hashlib.sha256(payload).hexdigest() != ref["sha256"]:
        raise RegenerationError(f"{label}: 実bytes sha256 がreportと不一致")
    return payload


def _read_bound_json(
    report_root: Path, ref: Any, *, expected_path: Path, label: str
) -> tuple[dict[str, Any], bytes]:
    payload = _read_bound_bytes(
        report_root, ref, expected_path=expected_path, label=label
    )
    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegenerationError(f"{label}: JSONでない") from exc
    if not isinstance(parsed, dict):
        raise RegenerationError(f"{label}: JSON objectでない")
    return parsed, payload


def _committed_group_paths(
    report_root: Path, groups: dict[str, Any], *, bundle_id: str
) -> tuple[GroupPaths, ...]:
    expected_ids = {f"{generation}_{speaker}" for generation, speaker in GROUPS}
    if set(groups) != expected_ids:
        raise RegenerationError("Phase B report: provenance groups が固定10群でない")
    paths: list[GroupPaths] = []
    for generation, speaker in GROUPS:
        group_id = f"{generation}_{speaker}"
        node = groups[group_id]
        if not isinstance(node, dict) or set(node) != {
            "render_manifest",
            "render_doc",
            "materials_sha256_digest",
            "render_runtime_stack",
        }:
            raise RegenerationError(f"Phase B report: {group_id} evidence shape が不正")
        doc_ref, manifest_ref = _phase_b_group_refs(group_id, bundle_id=bundle_id)
        doc_binding = node["render_doc"]
        manifest_binding = node["render_manifest"]
        if (
            not isinstance(doc_binding, dict)
            or set(doc_binding) != {"path", "sha256"}
            or doc_binding.get("path") != str(doc_ref)
            or not _is_lower_hex(doc_binding.get("sha256"), 64)
        ):
            raise RegenerationError(f"Phase B report: {group_id} render doc path が不正")
        if (
            not isinstance(manifest_binding, dict)
            or set(manifest_binding) != {"path", "sha256"}
            or manifest_binding.get("path") != str(manifest_ref)
            or not _is_lower_hex(manifest_binding.get("sha256"), 64)
        ):
            raise RegenerationError(f"Phase B report: {group_id} manifest path が不正")
        paths.append(
            GroupPaths(
                generation=generation,
                speaker=speaker,
                export_dir=Path(),
                export_manifest=Path(),
                render_dir=Path(),
                render_doc=Path(report_root).resolve() / doc_ref,
                render_manifest=Path(report_root).resolve() / manifest_ref,
            )
        )
    return tuple(paths)


def _validate_synthetic_reconciliation(
    synthetic: Any,
    *,
    calibration_pins_bytes: bytes,
    runner_sha: str,
    execution_commit: str | None = None,
) -> dict[str, int]:
    """13条件のobserved pinを正本と再比較し、summaryの手書き改変を拒否する。"""
    expected = json.loads(calibration_pins_bytes)
    expected_keys = {
        "schema",
        "verdict",
        "value",
        "execution_commit",
        "output_pins",
        "runner",
        "observed",
    }
    if not isinstance(synthetic, dict) or set(synthetic) != expected_keys:
        raise RegenerationError("Phase B: synthetic reconciliation shape が不正")
    observed = synthetic.get("observed")
    actual_commit = synthetic.get("execution_commit")
    if (
        synthetic.get("schema") != "vg-d6-synthetic-calibration-reconciliation/0.1"
        or synthetic.get("verdict") != "PASS"
        or observed != expected
        or expected.get("n_conditions") != 13
        or not isinstance(expected.get("stimuli"), dict)
        or len(expected["stimuli"]) != 13
        or synthetic.get("value") != {"matched_conditions": 13, "mismatches": []}
        or synthetic.get("output_pins")
        != {
            "path": str(CALIBRATION_PINS.relative_to(REPO_ROOT)),
            "sha256": hashlib.sha256(calibration_pins_bytes).hexdigest(),
        }
        or synthetic.get("runner")
        != {
            "path": str(Path(__file__).resolve().relative_to(REPO_ROOT)),
            "sha256": runner_sha,
        }
        or not _is_lower_hex(actual_commit, 40)
        or (execution_commit is not None and actual_commit != execution_commit)
    ):
        raise RegenerationError("Phase B: synthetic calibration 13条件の再構成が不一致")
    return {"n_compared": 13, "n_matches": 13, "n_mismatches": 0}


def _historical_source_from_real_render_baseline(baseline: Any) -> dict[str, str]:
    """real-render正本が実際に消費した履歴実装digestを取り出す。"""
    render_path = baseline.get("render_path") if isinstance(baseline, dict) else None
    aux = render_path.get("aux_sha256") if isinstance(render_path, dict) else None
    if not isinstance(aux, dict):
        raise RegenerationError("Phase B report: real-render baseline aux_sha256 が不正")
    expected = {
        "git_commit": REAL_RENDER_HISTORICAL_COMMIT,
        "gate_synth_sha256": aux.get("gate_synth_py"),
        "s7_calib_render_sha256": aux.get("s7_calib_render_py"),
        "s7_calib_score_sha256": aux.get("s7_calib_score_py"),
        "s7_io_sha256": _historical_git_object_sha256(
            Path("voice_genesis/foundry/run8/s7_io.py")
        ),
        "s7_spec_sha256": _historical_git_object_sha256(
            Path("voice_genesis/foundry/run8/s7_spec.py")
        ),
    }
    if any(
        not _is_lower_hex(expected[key], 64)
        for key in (
            "gate_synth_sha256",
            "s7_calib_render_sha256",
            "s7_calib_score_sha256",
            "s7_io_sha256",
            "s7_spec_sha256",
        )
    ):
        raise RegenerationError("Phase B report: real-render baseline historical digest が不正")
    return expected


def _read_canonical_real_render_baseline(
    report_root: Path, fixed_probe_pins: Any
) -> tuple[dict[str, Any], bytes]:
    """real-render正本を単一readし、固定pin照合後にだけparseする。"""
    try:
        ref = fixed_probe_pins["calibration_set"]["refs"]["real_render_manifest"]
    except (KeyError, TypeError) as exc:
        raise RegenerationError(
            "Phase B: canonical real-render baseline ref が不正"
        ) from exc
    expected_path = Path(
        "voice_genesis/foundry/results_s7/s7_b1_real_render_manifest.json"
    )
    if (
        not isinstance(ref, dict)
        or ref.get("path") != str(expected_path)
        or ref.get("sha256") != REAL_RENDER_BASELINE_SHA256
    ):
        raise RegenerationError(
            "Phase B: canonical real-render baseline ref が固定pinと不一致"
        )
    baseline_bytes = (Path(report_root).resolve() / expected_path).read_bytes()
    observed_sha = hashlib.sha256(baseline_bytes).hexdigest()
    if observed_sha != REAL_RENDER_BASELINE_SHA256:
        raise RegenerationError(
            "Phase B: canonical real-render baseline sha256 "
            f"{observed_sha} != {REAL_RENDER_BASELINE_SHA256}"
        )
    try:
        baseline = json.loads(baseline_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegenerationError(
            "Phase B: canonical real-render baseline がJSONでない"
        ) from exc
    if not isinstance(baseline, dict):
        raise RegenerationError(
            "Phase B: canonical real-render baseline shape が不正"
        )
    return baseline, baseline_bytes


def _validate_committed_real_render_recovery(
    recovery: Any,
    *,
    baseline: dict[str, Any],
    baseline_sha: str,
    execution_commit: Any,
    reconciliation_value: Any,
) -> None:
    expected_keys = {
        "status",
        "required_asset",
        "historical_source",
        "recovery_condition",
        "closure_guard",
        "recovered_value_schema",
        "value",
    }
    if not isinstance(recovery, dict) or set(recovery) != expected_keys:
        raise RegenerationError("Phase B report: real-render recovery shape が不正")
    required_asset = recovery.get("required_asset")
    if (
        recovery.get("status") != "RECOVERED_AND_RECONCILED"
        or not isinstance(required_asset, dict)
        or required_asset.get("sha256") != REAL_RENDER_ACOUSTIC_SHA256
        or required_asset.get("source")
        != "external operator-supplied artifact; path intentionally not persisted"
    ):
        raise RegenerationError("Phase B report: real-render recovered asset が不正")
    expected_historical = _historical_source_from_real_render_baseline(baseline)
    if recovery.get("historical_source") != expected_historical:
        raise RegenerationError(
            "Phase B report: real-render historical source がbaseline aux_sha256と不一致"
        )
    value = recovery.get("value")
    calibration = (reconciliation_value or {}).get("calibration", {}).get(
        "real_render", {}
    )
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "execution_commit",
            "recovered_asset_sha256",
            "baseline_manifest_sha256",
            "regenerated_manifest_sha256",
            "n_compared",
            "n_matches",
            "n_mismatches",
        }
        or value.get("execution_commit") != execution_commit
        or value.get("recovered_asset_sha256") != REAL_RENDER_ACOUSTIC_SHA256
        or value.get("baseline_manifest_sha256") != baseline_sha
        or value.get("regenerated_manifest_sha256")
        != calibration.get("regenerated_manifest_sha256")
        or (value.get("n_compared"), value.get("n_matches"), value.get("n_mismatches"))
        != (14, 14, 0)
    ):
        raise RegenerationError("Phase B report: real-render recovery value が裁定値と不一致")


def validate_resolved_reconciliation(
    node: dict[str, Any], *, report_root: Path = REPO_ROOT
) -> dict[str, Any]:
    """RESOLVED pin を、commit 済み Phase B report の実 bytes へ結合する。"""
    if node.get("status") != "RESOLVED":
        raise RegenerationError("reproducibility reconciliation は RESOLVED でない")
    binding = node.get("report_binding")
    if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
        raise RegenerationError("RESOLVED reconciliation に report_binding が無い")
    if binding.get("path") != str(PHASE_B_REPORT_PATH):
        raise RegenerationError("RESOLVED reconciliation の report path が正本でない")
    if not _is_lower_hex(binding.get("sha256"), 64):
        raise RegenerationError("RESOLVED reconciliation の report sha256 が不正")
    report_path = Path(report_root).resolve() / PHASE_B_REPORT_PATH
    report_bytes = report_path.read_bytes()
    report_sha = hashlib.sha256(report_bytes).hexdigest()
    if report_sha != binding["sha256"]:
        raise RegenerationError("RESOLVED reconciliation の report sha256 が実体と不一致")
    try:
        report = json.loads(report_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegenerationError("RESOLVED reconciliation report がJSONでない") from exc
    if set(report) != {
        "schema",
        "regenerated_provenance",
        "real_render_recovery",
        "reproducibility_reconciliation",
    }:
        raise RegenerationError("RESOLVED reconciliation report のtop-level shapeが不正")
    report_node = report.get("reproducibility_reconciliation")
    committed_state = {
        "status": node.get("status"),
        "execution_commit": node.get("execution_commit"),
        "value": node.get("value"),
    }
    if (
        report.get("schema") != "vg-d6-phase-b-reconciliation/0.1"
        or report_node != committed_state
    ):
        raise RegenerationError("RESOLVED reconciliation が report の裁定値と不一致")
    provenance = report.get("regenerated_provenance")
    if not isinstance(provenance, dict) or set(provenance) != {
        "bundle_id",
        "results",
        "d4_runner",
        "d4_remeasure_spec",
        "synthetic_reconciliation",
        "real_render_manifest",
        "groups",
    }:
        raise RegenerationError("RESOLVED reconciliation report のprovenance shapeが不正")
    bundle_id = provenance["bundle_id"]
    if not _is_lower_hex(bundle_id, 64):
        raise RegenerationError("RESOLVED reconciliation report のbundle idが不正")
    regenerated, regenerated_bytes = _read_bound_json(
        report_root,
        provenance["results"],
        expected_path=_phase_b_bundle_path(bundle_id, PHASE_B_RESULTS_PATH),
        label="Phase B packaged results",
    )
    d4_runner_path = Path("voice_genesis/foundry/debt/d4/d4_runner.py")
    _read_bound_bytes(
        report_root,
        provenance["d4_runner"],
        expected_path=d4_runner_path,
        label="Phase B D4 runner",
    )
    d4_spec_path = Path("voice_genesis/foundry/debt/d4/d4_remeasure_spec.json")
    d4_spec, d4_spec_bytes = _read_bound_json(
        report_root,
        provenance["d4_remeasure_spec"],
        expected_path=d4_spec_path,
        label="Phase B D4 spec",
    )
    baseline_path = Path(
        "voice_genesis/foundry/debt/d4/d4_results_2026-08-22.json"
    )
    baseline_bytes = (Path(report_root).resolve() / baseline_path).read_bytes()
    if hashlib.sha256(baseline_bytes).hexdigest() != D4_BASELINE_SHA256:
        raise RegenerationError("Phase B canonical baseline がpinと不一致")
    baseline = json.loads(baseline_bytes)
    fixed_probe_path = Path("voice_genesis/foundry/debt/d6/s7_fixed_probe_pins.json")
    fixed_probe_pins = json.loads(
        (Path(report_root).resolve() / fixed_probe_path).read_bytes()
    )
    real_baseline, real_baseline_bytes = _read_canonical_real_render_baseline(
        report_root, fixed_probe_pins
    )
    real_baseline_sha = hashlib.sha256(real_baseline_bytes).hexdigest()
    _validate_committed_real_render_recovery(
        report["real_render_recovery"],
        baseline=real_baseline,
        baseline_sha=real_baseline_sha,
        execution_commit=committed_state["execution_commit"],
        reconciliation_value=committed_state["value"],
    )
    expected_render_runtime = _execution_profile_from_pins(fixed_probe_pins)
    expected_measurement_dependencies = _measurement_dependency_profile_from_pins(
        fixed_probe_pins
    )
    committed_groups = _committed_group_paths(
        report_root, provenance["groups"], bundle_id=bundle_id
    )
    observed_groups, observed_artifacts = _validate_regenerated_provenance(
        regenerated,
        baseline=baseline,
        d4_spec=d4_spec,
        d4_spec_sha=hashlib.sha256(d4_spec_bytes).hexdigest(),
        rendered_groups=committed_groups,
        expected_render_runtime=expected_render_runtime,
        expected_measurement_dependencies=expected_measurement_dependencies,
        require_result_path_match=False,
        bundle_id=bundle_id,
    )
    if observed_groups != provenance["groups"]:
        raise RegenerationError("Phase B report provenance がcommitted evidenceと不一致")
    regenerated_sha = provenance["results"]["sha256"]
    value = committed_state.get("value") or {}
    trf_spec_path = Path("voice_genesis/foundry/results_s7/trf_measurement_spec_1_2.json")
    trf_spec_bytes = (Path(report_root).resolve() / trf_spec_path).read_bytes()
    if hashlib.sha256(trf_spec_bytes).hexdigest() != d4_spec["pins"].get(
        "trf_measurement_spec_1_2_sha256"
    ):
        raise RegenerationError("Phase B TRF spec がD4 pinと不一致")
    recomputed_production = _reconcile_production_measurements(
        baseline,
        regenerated,
        json.loads(trf_spec_bytes),
        baseline_sha=D4_BASELINE_SHA256,
        regenerated_sha=regenerated_sha,
    )
    committed_production = {
        key: value.get(key)
        for key in (
            "reference_output_remeasurement",
            "samples_sha256",
            "wav_sha256",
        )
    }
    if committed_production != recomputed_production:
        raise RegenerationError("Phase B production裁定値が360測定値の再計算と不一致")

    synthetic, synthetic_bytes = _read_bound_json(
        report_root,
        provenance["synthetic_reconciliation"],
        expected_path=_phase_b_bundle_path(bundle_id, PHASE_B_SYNTHETIC_PATH),
        label="Phase B synthetic reconciliation",
    )
    calibration_pins_bytes = (
        Path(report_root).resolve()
        / Path("voice_genesis/foundry/debt/d6/s7_synthetic_calibration_output_pins.json")
    ).read_bytes()
    synthetic_summary = _validate_synthetic_reconciliation(
        synthetic,
        calibration_pins_bytes=calibration_pins_bytes,
        runner_sha=hashlib.sha256(
            (
                Path(report_root).resolve()
                / Path("voice_genesis/foundry/debt/d6/d6_regenerate.py")
            ).read_bytes()
        ).hexdigest(),
        execution_commit=committed_state["execution_commit"],
    )
    real_manifest, real_manifest_bytes = _read_bound_json(
        report_root,
        provenance["real_render_manifest"],
        expected_path=_phase_b_bundle_path(
            bundle_id, PHASE_B_REAL_RENDER_MANIFEST_PATH
        ),
        label="Phase B real-render manifest",
    )
    real_summary = _reconcile_real_render_data(
        real_baseline, real_manifest
    )
    expected_calibration = {
        "synthetic": {
            **synthetic_summary,
            "baseline_pins_sha256": hashlib.sha256(calibration_pins_bytes).hexdigest(),
            "reconciliation_sha256": provenance["synthetic_reconciliation"]["sha256"],
        },
        "real_render": {
            "n_compared": real_summary["n_compared"],
            "n_matches": real_summary["samples_matches"],
            "n_mismatches": len(real_summary["samples_mismatches"]),
            "baseline_manifest_sha256": real_baseline_sha,
            "regenerated_manifest_sha256": hashlib.sha256(
                real_manifest_bytes
            ).hexdigest(),
            "recovery_acoustic_sha256": REAL_RENDER_ACOUSTIC_SHA256,
        },
    }
    if value.get("calibration") != expected_calibration:
        raise RegenerationError("Phase B calibration裁定値がpackaged evidenceの再計算と不一致")
    bundle_prefix = PHASE_B_EVIDENCE_PATH / bundle_id
    normalized_artifacts = {
        PHASE_B_EVIDENCE_PATH / path.relative_to(bundle_prefix): payload
        for path, payload in observed_artifacts.items()
    }
    normalized_artifacts.update(
        {
            PHASE_B_RESULTS_PATH: regenerated_bytes,
            PHASE_B_SYNTHETIC_PATH: synthetic_bytes,
            PHASE_B_REAL_RENDER_MANIFEST_PATH: real_manifest_bytes,
        }
    )
    if len(normalized_artifacts) != 23 or _phase_b_evidence_bundle_id(
        normalized_artifacts
    ) != bundle_id:
        raise RegenerationError("Phase B evidence bundle id が23成果物と不一致")
    return report


def compose_phase_b_reconciliation(
    regenerated_results_path: Path,
    synthetic_report_path: Path,
    real_render_report_path: Path,
    real_render_manifest_path: Path,
    recovered_acoustic_path: Path,
    out_path: Path,
    rendered_groups: Sequence[GroupPaths],
) -> dict[str, Any]:
    """各段の実測をD6の唯一のPhase B正規形へ合成し、false closureを拒否する。"""
    _verify_file_pin(
        recovered_acoustic_path,
        REAL_RENDER_ACOUSTIC_SHA256,
        label="historical acoustic ONNX at reconciliation",
    )
    baseline_bytes = D4_BASELINE.read_bytes()
    baseline_sha = hashlib.sha256(baseline_bytes).hexdigest()
    fixed_probe_pins = json.loads(FIXED_PROBE_PINS.read_bytes())
    expected_render_runtime = _execution_profile_from_pins(fixed_probe_pins)
    expected_measurement_dependencies = _measurement_dependency_profile_from_pins(
        fixed_probe_pins
    )
    baseline_ref = fixed_probe_pins["production_cells"]["refs"]["d4_1_2_remeasurement"]
    if (
        baseline_ref.get("path") != "voice_genesis/foundry/debt/d4/d4_results_2026-08-22.json"
        or baseline_ref.get("sha256") != D4_BASELINE_SHA256
        or baseline_sha != D4_BASELINE_SHA256
    ):
        raise RegenerationError(
            f"Phase B: canonical D4 baseline sha256 {baseline_sha} != {D4_BASELINE_SHA256}"
        )
    runner_ref = fixed_probe_pins["common_fixed"]["regeneration_commands"]["d4_runner"]
    d4_runner_sha = sha256_file(D4_RUNNER)
    if (
        runner_ref.get("path") != "voice_genesis/foundry/debt/d4/d4_runner.py"
        or runner_ref.get("sha256") != d4_runner_sha
    ):
        raise RegenerationError("Phase B: regenerated producer d4_runner.py がpinと不一致")
    baseline = json.loads(baseline_bytes)
    regenerated_bytes = Path(regenerated_results_path).read_bytes()
    regenerated_sha = hashlib.sha256(regenerated_bytes).hexdigest()
    regenerated = json.loads(regenerated_bytes)

    d4_spec_bytes = D4_SPEC.read_bytes()
    d4_spec_sha = hashlib.sha256(d4_spec_bytes).hexdigest()
    d4_spec = json.loads(d4_spec_bytes)
    regenerated_provenance, evidence_artifacts = _validate_regenerated_provenance(
        regenerated,
        baseline=baseline,
        d4_spec=d4_spec,
        d4_spec_sha=d4_spec_sha,
        rendered_groups=rendered_groups,
        expected_render_runtime=expected_render_runtime,
        expected_measurement_dependencies=expected_measurement_dependencies,
    )
    evidence_artifacts[PHASE_B_RESULTS_PATH] = regenerated_bytes
    expected_trf_sha = d4_spec["pins"]["trf_measurement_spec_1_2_sha256"]
    trf_spec_bytes = TRF_SPEC.read_bytes()
    if hashlib.sha256(trf_spec_bytes).hexdigest() != expected_trf_sha:
        raise RegenerationError("Phase B: TRF measurement spec 1.2 がD4 pinと一致しない")
    trf_spec = json.loads(trf_spec_bytes)
    production_value = _reconcile_production_measurements(
        baseline,
        regenerated,
        trf_spec,
        baseline_sha=baseline_sha,
        regenerated_sha=regenerated_sha,
    )

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    calibration_pins_bytes = CALIBRATION_PINS.read_bytes()
    calibration_pins_sha = hashlib.sha256(calibration_pins_bytes).hexdigest()
    real_render_baseline, real_render_baseline_bytes = (
        _read_canonical_real_render_baseline(REPO_ROOT, fixed_probe_pins)
    )
    real_render_baseline_sha = hashlib.sha256(real_render_baseline_bytes).hexdigest()
    synthetic_bytes = Path(synthetic_report_path).read_bytes()
    synthetic_sha = hashlib.sha256(synthetic_bytes).hexdigest()
    synthetic = json.loads(synthetic_bytes)
    synthetic_value = _validate_synthetic_reconciliation(
        synthetic,
        calibration_pins_bytes=calibration_pins_bytes,
        runner_sha=sha256_file(Path(__file__).resolve()),
        execution_commit=commit,
    )
    real_render_bytes = Path(real_render_report_path).read_bytes()
    real_render = json.loads(real_render_bytes)
    real_render_manifest_bytes = Path(real_render_manifest_path).read_bytes()
    real_render_manifest_sha = hashlib.sha256(real_render_manifest_bytes).hexdigest()
    real_value = _reconcile_real_render_data(
        real_render_baseline, json.loads(real_render_manifest_bytes)
    )
    if (
        real_render.get("schema") != "vg-d6-real-render-calibration-reconciliation/0.1"
        or real_render.get("verdict") != "PASS"
        or real_render.get("value") != real_value
        or real_render.get("recovered_acoustic_sha256") != REAL_RENDER_ACOUSTIC_SHA256
        or real_render.get("baseline_manifest_sha256") != real_render_baseline_sha
        or real_render.get("regenerated_manifest_sha256") != real_render_manifest_sha
        or real_render.get("historical_source_commit") != REAL_RENDER_HISTORICAL_COMMIT
        or real_render.get("execution_commit") != commit
    ):
        raise RegenerationError("Phase B: real-render calibration 14条件の照合がPASSでない")
    evidence_artifacts[PHASE_B_SYNTHETIC_PATH] = synthetic_bytes
    evidence_artifacts[PHASE_B_REAL_RENDER_MANIFEST_PATH] = real_render_manifest_bytes
    if len(evidence_artifacts) != 23:
        raise RegenerationError("Phase B evidence bundle が固定23成果物でない")
    bundle_id = _phase_b_evidence_bundle_id(evidence_artifacts)
    for group in regenerated_provenance.values():
        for ref_name in ("render_doc", "render_manifest"):
            group[ref_name]["path"] = str(
                _phase_b_bundle_path(bundle_id, Path(group[ref_name]["path"]))
            )
    versioned_artifacts = {
        _phase_b_bundle_path(bundle_id, path): payload
        for path, payload in evidence_artifacts.items()
    }
    reference_mismatches = production_value["reference_output_remeasurement"][
        "n_mismatches"
    ]
    value = {
        **production_value,
        "calibration": {
            "synthetic": {
                **synthetic_value,
                "baseline_pins_sha256": calibration_pins_sha,
                "reconciliation_sha256": synthetic_sha,
            },
            "real_render": {
                "n_compared": real_value["n_compared"],
                "n_matches": real_value["samples_matches"],
                "n_mismatches": len(real_value["samples_mismatches"]),
                "baseline_manifest_sha256": real_render_baseline_sha,
                "regenerated_manifest_sha256": real_render_manifest_sha,
                "recovery_acoustic_sha256": REAL_RENDER_ACOUSTIC_SHA256,
            },
        },
    }
    recovery = fixed_probe_pins["calibration_set"]["real_render_recovery"]
    expected_historical = _historical_source_from_real_render_baseline(
        real_render_baseline
    )
    if recovery.get("historical_source") != expected_historical:
        raise RegenerationError(
            "Phase B: fixed probe historical source がreal-render baselineと不一致"
        )
    recovery["status"] = "RECOVERED_AND_RECONCILED"
    recovery["required_asset"]["source"] = (
        "external operator-supplied artifact; path intentionally not persisted"
    )
    recovery["value"] = {
        "execution_commit": commit,
        "recovered_asset_sha256": REAL_RENDER_ACOUSTIC_SHA256,
        "baseline_manifest_sha256": real_render_baseline_sha,
        "regenerated_manifest_sha256": real_render_manifest_sha,
        "n_compared": 14,
        "n_matches": 14,
        "n_mismatches": 0,
    }
    report = {
        "schema": "vg-d6-phase-b-reconciliation/0.1",
        "regenerated_provenance": {
            "bundle_id": bundle_id,
            "results": _bound_ref(
                _phase_b_bundle_path(bundle_id, PHASE_B_RESULTS_PATH),
                regenerated_sha,
            ),
            "d4_runner": _bound_ref(
                Path("voice_genesis/foundry/debt/d4/d4_runner.py"), d4_runner_sha
            ),
            "d4_remeasure_spec": _bound_ref(
                Path("voice_genesis/foundry/debt/d4/d4_remeasure_spec.json"),
                d4_spec_sha,
            ),
            "synthetic_reconciliation": _bound_ref(
                _phase_b_bundle_path(bundle_id, PHASE_B_SYNTHETIC_PATH),
                synthetic_sha,
            ),
            "real_render_manifest": _bound_ref(
                _phase_b_bundle_path(bundle_id, PHASE_B_REAL_RENDER_MANIFEST_PATH),
                real_render_manifest_sha,
            ),
            "groups": regenerated_provenance,
        },
        "real_render_recovery": recovery,
        "reproducibility_reconciliation": {
            "status": "RESOLVED" if reference_mismatches == 0 else "FAILED_RECONCILIATION",
            "execution_commit": commit,
            "value": value,
        },
    }
    report_payload = (
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    if reference_mismatches:
        raise RegenerationError(
            "Phase B: reference_output がepsilon外 "
            f"{reference_mismatches}/360。既存の正典report/bundleは変更しない"
        )
    _publish_phase_b_bundle(
        Path(out_path),
        versioned_artifacts,
        report_payload,
        bundle_id=bundle_id,
        protected_inputs=_phase_b_composer_inputs(
            regenerated_results_path,
            synthetic_report_path,
            real_render_report_path,
            real_render_manifest_path,
            recovered_acoustic_path,
            rendered_groups,
        ),
    )
    return report


def _float32_wav_bytes(samples: Any, sample_rate: int) -> tuple[bytes, bytes, bytes]:
    """IEEE float32 mono WAV を PEAK/時刻 chunk なしで決定論的に作る。

    戻り値は ``(wav, pcm_f32le, decoded_f64le)``。最後の値は float32 PCMを
    float64へ厳密拡大した列で、WAVをB-1の実レンダー経路と同じ方式で復号した
    測定入力を表す。合成直後のfloat64列はlibm/CPU差を含み得るためpinしない。
    """
    import numpy as np

    pcm_samples = np.ascontiguousarray(samples, dtype="<f4")
    pcm = pcm_samples.tobytes()
    analysis = np.ascontiguousarray(pcm_samples, dtype="<f8").tobytes()
    byte_rate = int(sample_rate) * 4
    fmt = struct.pack("<HHIIHH", 3, 1, int(sample_rate), byte_rate, 4, 32)
    riff_size = 4 + (8 + len(fmt)) + (8 + len(pcm))
    wav = (
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVEfmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )
    return wav, pcm, analysis


def generate_calibration_outputs(out_dir: Path) -> dict[str, Any]:
    run8 = FOUNDRY / "run8"
    if str(run8) not in sys.path:
        sys.path.insert(0, str(run8))
    import s7_b1_calibration as calibration

    prereg = calibration.load_prereg()
    stimuli = calibration.build_calibration_set(prereg)
    out_dir.mkdir(parents=True, exist_ok=True)
    entries: dict[str, Any] = {}
    for stim_id, stimulus in sorted(stimuli.items()):
        wav, pcm, analysis = _float32_wav_bytes(stimulus.samples, stimulus.sr)
        wav_path = out_dir / f"{stim_id}.wav"
        wav_path.write_bytes(wav)
        entries[stim_id] = {
            "sample_rate_hz": stimulus.sr,
            "n_samples": len(stimulus.samples),
            "wav_sha256": hashlib.sha256(wav).hexdigest(),
            "pcm_f32le_sha256": hashlib.sha256(pcm).hexdigest(),
            "analysis_samples_f64le_sha256": hashlib.sha256(analysis).hexdigest(),
        }
    return {
        "schema": "vg-d6-synthetic-calibration-output-pins/0.1",
        "source_prereg": {
            "path": "voice_genesis/foundry/results_s7/s7_b1_calibration_set.json",
            "sha256": prereg.pins["s7_b1_calibration_set.json"],
        },
        "format": "mono IEEE-float32 little-endian WAV; deterministic RIFF without PEAK chunk",
        "analysis_format": "float32 PCM widened exactly to little-endian float64",
        "n_conditions": len(entries),
        "stimuli": entries,
    }


def verify_calibration_outputs(out_dir: Path) -> dict[str, Any]:
    expected_bytes = CALIBRATION_PINS.read_bytes()
    expected = json.loads(expected_bytes)
    observed = generate_calibration_outputs(out_dir)
    comparable = {key: expected[key] for key in observed}
    if observed != comparable:
        raise RegenerationError(
            "合成校正13条件の WAV/PCM pin が不一致。入力・実装・numeric stack を確認する"
        )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    report = {
        "schema": "vg-d6-synthetic-calibration-reconciliation/0.1",
        "verdict": "PASS",
        "value": {"matched_conditions": len(observed["stimuli"]), "mismatches": []},
        "execution_commit": commit,
        "output_pins": {
            "path": str(CALIBRATION_PINS.relative_to(REPO_ROOT)),
            "sha256": hashlib.sha256(expected_bytes).hexdigest(),
        },
        "runner": {
            "path": str(Path(__file__).resolve().relative_to(REPO_ROOT)),
            "sha256": sha256_file(Path(__file__).resolve()),
        },
        "observed": observed,
    }
    report_path = out_dir.parent / "calibration_synthetic_reconciliation.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _run(command: Sequence[str]) -> None:
    print("+", shlex.join(command), flush=True)
    subprocess.run(list(command), cwd=REPO_ROOT, check=True)


def _print_commands(commands: Iterable[Sequence[str]]) -> None:
    for command in commands:
        print(shlex.join(command))


def _validated_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_absolute():
        raise argparse.ArgumentTypeError("--root は絶対パスで指定する")
    if root == REPO_ROOT or REPO_ROOT in root.parents or root in REPO_ROOT.parents:
        raise argparse.ArgumentTypeError("--root は repository の内外関係にない作業先を指定する")
    return root


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv[:1] == [_INTERNAL_SYNTHETIC_CALIBRATION_FLAG]:
        if len(raw_argv) != 2:
            raise RegenerationError(
                f"{_INTERNAL_SYNTHETIC_CALIBRATION_FLAG} は出力先1個だけを取る"
            )
        verify_runner_pins()
        verify_calibration_outputs(Path(raw_argv[1]).expanduser().resolve())
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=_validated_root)
    parser.add_argument(
        "--real-render-acoustic-onnx",
        required=True,
        type=Path,
        help=(
            "2026-08-21 B-1 real-renderで実際に使った acoustic ONNX。"
            f"sha256 {REAL_RENDER_ACOUSTIC_SHA256} 以外は再exportを含め拒否する"
        ),
    )
    parser.add_argument("--plan", action="store_true", help="副作用なしで静的コマンドを表示")
    parser.add_argument(
        "--skip-provision",
        action="store_true",
        help="既に同じ provision.sh で照合済みの root に限り取得段を省略",
    )
    args = parser.parse_args(raw_argv)
    root: Path = args.root
    real_render_acoustic = args.real_render_acoustic_onnx.expanduser().resolve()
    _preflight_protected_inputs(root, (real_render_acoustic,))
    verify_runner_pins()
    provision, exports, renders = static_plan(root)
    if args.plan:
        if not args.skip_provision:
            _print_commands([provision])
        _print_commands([build_synthetic_calibration_command(root)])
        _print_commands(exports)
        _print_commands([build_real_render_command(root, real_render_acoustic)])
        _print_commands(renders)
        print(
            "# measure は render 後、上記10 manifestの実 sha256を計算して"
            " --render-manifest-sha256 へ渡す"
        )
        return 0

    _verify_file_pin(
        real_render_acoustic,
        REAL_RENDER_ACOUSTIC_SHA256,
        label="historical acoustic ONNX",
    )
    if not args.skip_provision:
        _run(provision)
    verify_real_render_stack(_render_python(root))
    _run(build_synthetic_calibration_command(root))
    for export in exports:
        _run(export)
    verify_real_render_inputs(root, real_render_acoustic)
    materialize_historical_real_render_source(root)
    _run(build_real_render_command(root, real_render_acoustic))
    reconcile_real_render_manifest(
        root / "calibration_real_render_manifest.json",
        root / "calibration_real_render_reconciliation.json",
    )
    for render in renders:
        _run(render)
    _run(build_measure_command(root))
    compose_phase_b_reconciliation(
        root / "d6_regenerated_results.json",
        root / "calibration_synthetic_reconciliation.json",
        root / "calibration_real_render_reconciliation.json",
        root / "calibration_real_render_manifest.json",
        real_render_acoustic,
        _phase_b_report_path(root),
        group_paths(root),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
