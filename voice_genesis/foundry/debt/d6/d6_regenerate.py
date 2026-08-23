"""VG-DEBT-006 の固定 probe と合成校正出力を再生成する実行体。

例（取得から 360 セル再測定まで）::

    python3 voice_genesis/foundry/debt/d6/d6_regenerate.py \
        --root /home/user/d6work

``--plan`` は副作用なしで、同じ絶対パスを使う全コマンドを表示する。通常実行は
pin 照合つき provision、合成校正13条件の再生成照合、10群の witnessed export、
10群 render、生成された render manifest の実 sha256 を束縛した measure の順に
fail-closed で進む。動的 digest を人手の placeholder に戻さないため、measure
コマンドは render 完了後に本実行体が組み立てる。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import struct
import subprocess
import sys
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
    root: Path, group: GroupPaths, *, python_executable: str = sys.executable
) -> list[str]:
    stem = f"s6_{group.generation}_acoustic"
    canon = root / "materials" / "extracted" / "ds" / "NamineRitsu_DiffSinger"
    return [
        python_executable,
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
    root: Path, *, python_executable: str = sys.executable
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
    parser.add_argument("--plan", action="store_true", help="副作用なしで静的コマンドを表示")
    parser.add_argument(
        "--skip-provision",
        action="store_true",
        help="既に同じ provision.sh で照合済みの root に限り取得段を省略",
    )
    args = parser.parse_args(argv)
    root: Path = args.root
    verify_runner_pins()
    provision, exports, renders = static_plan(root)
    if args.plan:
        if not args.skip_provision:
            _print_commands([provision])
        _print_commands(exports)
        _print_commands(renders)
        print(
            "# measure は render 後、上記10 manifestの実 sha256を計算して"
            " --render-manifest-sha256 へ渡す"
        )
        return 0

    if not args.skip_provision:
        _run(provision)
    verify_calibration_outputs(root / "calibration_synthetic")
    for export in exports:
        _run(export)
    for render in renders:
        _run(render)
    _run(build_measure_command(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
