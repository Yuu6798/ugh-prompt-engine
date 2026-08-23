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
REAL_RENDER_HISTORICAL_COMMIT = "8a14ca97eda1a6bf96f956a8173f512f0cdb50ae"
REAL_RENDER_ACOUSTIC_SHA256 = "f0e71f06b16e448622f3e0d9b977a26fbaa306bb608a08ed26efeb871332a7d1"

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


def verify_runner_pins() -> None:
    """再生成が起動する実装を、D6 pin inventory の値へ照合する。"""
    pins = json.loads(FIXED_PROBE_PINS.read_text(encoding="utf-8"))
    common = pins["common_fixed"]
    refs = [
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


def _checkpoint_dir(root: Path, generation: str) -> Path:
    if generation in {"run5", "run6"}:
        return root / "materials" / "ckpts" / f"{generation}_bundle"
    return root / "materials" / "run7_ckpt"


def _render_python(root: Path) -> str:
    return str(root / "venv_render" / "bin" / "python")


def build_provision_command(root: Path) -> list[str]:
    return ["bash", str(PROVISION), "--root", str(root)]


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


def build_measure_command(root: Path, *, python_executable: str = sys.executable) -> list[str]:
    command = [python_executable, str(D4_RUNNER), "measure"]
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
    foundry = source / "voice_genesis" / "foundry"
    for path, expected, label in (
        (foundry / "s1_gate" / "gate_synth.py", aux["gate_synth_py"], "gate_synth_py"),
        (
            foundry / "run8" / "s7_calib_score.py",
            aux["s7_calib_score_py"],
            "s7_calib_score_py",
        ),
        (
            foundry / "run8" / "s7_calib_render.py",
            aux["s7_calib_render_py"],
            "s7_calib_render_py",
        ),
        (
            foundry / "results_s7" / "s7_b1_calibration_set.json",
            baseline["prereg"]["sha256"],
            "real-render prereg",
        ),
    ):
        _verify_file_pin(path, expected, label=f"historical source {label}")


def materialize_historical_real_render_source(root: Path) -> Path:
    """Git objectからreal-render実行時のsource treeを展開してpin照合する。"""
    target = _real_render_source_root(root)
    if target.is_dir():
        _verify_historical_source(target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".real-render-source-", dir=target.parent))
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
                tar.extract(member, staging)
        _verify_historical_source(staging)
        staging.rename(target)
    finally:
        archive.unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging)
    return target


def reconcile_real_render_manifest(observed_path: Path, report_path: Path) -> dict[str, Any]:
    """履歴real-render全14条件の標本pinを基準manifestへ突き合わせる。"""
    baseline = json.loads(REAL_RENDER_BASELINE.read_text(encoding="utf-8"))
    observed = json.loads(Path(observed_path).read_text(encoding="utf-8"))
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
        "value": {
            "n_rendered": int(observed["n_rendered"]),
            "n_compared": len(expected_conditions),
            "samples_matches": len(expected_conditions),
            "samples_mismatches": [],
            "wav_container_matches": wav_matches,
            "wav_container_mismatches": len(expected_conditions) - wav_matches,
        },
        "baseline_manifest_sha256": sha256_file(REAL_RENDER_BASELINE),
        "regenerated_manifest_sha256": sha256_file(Path(observed_path)),
        "recovered_acoustic_sha256": REAL_RENDER_ACOUSTIC_SHA256,
        "historical_source_commit": REAL_RENDER_HISTORICAL_COMMIT,
        "execution_commit": commit,
    }
    Path(report_path).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _float32_wav_bytes(samples: Any, sample_rate: int) -> tuple[bytes, bytes, bytes]:
    """IEEE float32 mono WAV を PEAK/時刻 chunk なしで決定論的に作る。

    戻り値は ``(wav, pcm_f32le, analysis_f64le)``。最後の値は B-1 harness が
    実際に測る float64 標本列なので、WAV 容器だけでなく測定入力も束縛できる。
    """
    import numpy as np

    analysis = np.ascontiguousarray(samples, dtype="<f8").tobytes()
    pcm = np.ascontiguousarray(samples, dtype="<f4").tobytes()
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
        "n_conditions": len(entries),
        "stimuli": entries,
    }


def verify_calibration_outputs(out_dir: Path) -> dict[str, Any]:
    expected = json.loads(CALIBRATION_PINS.read_text(encoding="utf-8"))
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
            "sha256": sha256_file(CALIBRATION_PINS),
        },
        "runner": {
            "path": str(Path(__file__).resolve().relative_to(REPO_ROOT)),
            "sha256": sha256_file(Path(__file__).resolve()),
        },
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
    args = parser.parse_args(argv)
    root: Path = args.root
    real_render_acoustic = args.real_render_acoustic_onnx.expanduser().resolve()
    verify_runner_pins()
    provision, exports, renders = static_plan(root)
    if args.plan:
        if not args.skip_provision:
            _print_commands([provision])
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
    verify_calibration_outputs(root / "calibration_synthetic")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
