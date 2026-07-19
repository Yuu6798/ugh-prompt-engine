"""scripts/collect_ar4_observation.py — AR4 実観測バッチ orchestration (MusicGen).

**手動 runbook。CI では絶対に実行しない**（`scripts/collect_musicgen_takes.py` と同じ
DD-A 規律: torch 経由の非決定論的推論を行うため、`pip install -e ".[musicgen]"` が
必要）。目的は AR4（生成後観測）の observation report を初めて実生成物で埋めること
（Design Memo:
`design_memo_ar4_musicgen.md`、`docs/arrangement_identity_planning.md` §5）。

新規ロジックは「package が compile した prompt テキストを読む -> n take 生成 ->
sha256/タイムスタンプを記録する」というオーケストレーションのみに限定する。
モデルロード/生成そのものは `scripts/collect_musicgen_takes.py` の
`_import_musicgen_stack`（torch/transformers の遅延 import ガード）と
`_sha256_file` を再利用し、生成ループの形（`processor(...)` -> `model.generate(...)`
-> `wavfile.write`）も同スクリプトの `perform_takes` と同型に揃える —
違いは prompt の出所のみ（`perform_takes` はスコアから
`ExternalPromptAdapter` で再導出するが、本スクリプトは既にコンパイル済みの
`performance_package.json` の `prompt.text` を検証済みチェーンの終端としてそのまま
読む。chain の正直さ: 楽譜 -> package -> 生成器）。

Usage:
    # 1) 生成（手動・torch 必須。canonical n=2 バッチ）
    python scripts/collect_ar4_observation.py generate \\
        --package /path/to/performance_package.json \\
        --output-dir /tmp/ar4_takes \\
        --manifest-out /tmp/ar4_takes/ar4_takes_manifest.json \\
        --model-revision 4c8334b02c6ec4e8664a91979669a501ec497792

    # 2) Phase 3 決定論スポット検証: 上と同一 --package/--model-revision で
    #    --take-index を 1 本ずつ指定し、別プロセスで再生成する
    python scripts/collect_ar4_observation.py generate \\
        --package /path/to/performance_package.json \\
        --output-dir /tmp/ar4_spotcheck \\
        --manifest-out /tmp/ar4_spotcheck/take0_manifest.json \\
        --take-index 0 --model-revision 4c8334b02c6ec4e8664a91979669a501ec497792
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.collect_musicgen_takes import _import_musicgen_stack, _sha256_file  # noqa: E402

SCHEMA_VERSION = "1.0"
GENERATOR = "collect_ar4_observation.py"
DEFAULT_FIXTURE_ID = "ar4_musicgen_midnight_signal_edm"
DEFAULT_MODEL_ID = "facebook/musicgen-small"
DEFAULT_SEED_BASE = 8000  # AR4 専用レンジ（sample_seed の 1000+ / perform_takes の
# 既定 seed_base=5000 と衝突しない。ar4_plan.yaml の seeds.formula と同一定義）


def _utc_now_iso() -> str:
    """実測 UTC タイムスタンプ（秒精度、`date -u` 相当）。推定値は使わない。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_package_prompt(package_path: Path) -> tuple[str, str]:
    """コンパイル済み `performance_package.json` の `prompt.text` を検証済みの
    終端としてそのまま読む（chain の正直さ: 再導出しない）。

    `prompt` が `None`、または `text` が空/空白のみの場合は ``ValueError`` を送出
    する — 呼び出し側はここで停止して報告し、プロンプトを発明してはならない
    （Design Memo Phase 0 #3 / 呼び出し元指示）。
    """
    package_bytes = package_path.read_bytes()
    package_sha256 = hashlib.sha256(package_bytes).hexdigest()
    data = json.loads(package_bytes)
    prompt = data.get("prompt")
    if prompt is None:
        raise ValueError(
            f"performance_package.json at {package_path} has prompt=None — the "
            "style_prompt channel was omitted at compile time (see 'warnings' in "
            "the sibling compilation_report.json). Refusing to invent a prompt; "
            "stop here and report."
        )
    text = prompt.get("text")
    if text is None or not str(text).strip():
        raise ValueError(
            f"performance_package.json at {package_path} has an empty prompt.text. "
            "Refusing to invent a prompt; stop here and report."
        )
    return str(text), package_sha256


def generate_ar4_takes(
    prompt_text: str,
    *,
    output_dir: Path,
    take_indices: list[int],
    seed_base: int = DEFAULT_SEED_BASE,
    model_id: str = DEFAULT_MODEL_ID,
    model_revision: Optional[str] = None,
    duration_seconds: float = 12.0,
    guidance_scale: float = 3.0,
) -> dict[str, Any]:
    """`take_indices` 各要素について MusicGen 推論を 1 回ずつ行う（seed = seed_base +
    take_index）。モデルロード/生成そのものは
    `scripts/collect_musicgen_takes.py` の `_import_musicgen_stack` を再利用し、
    生成ループの形も同スクリプトの `perform_takes` と同型に揃える。

    音声は ``output_dir`` に WAV として書き出すのみでコミット対象にはしない
    （DD-A）。各生成コールの実測 UTC 開始/終了時刻（秒精度・推定禁止）を
    ``timestamps`` として返す — 呼び出し側が
    ``ar4_generation_timestamps.yaml`` を書き出すのに使う。
    """
    torch, AutoProcessor, MusicgenForConditionalGeneration = _import_musicgen_stack()
    from scipy.io import wavfile

    processor = AutoProcessor.from_pretrained(model_id, revision=model_revision)
    model = MusicgenForConditionalGeneration.from_pretrained(model_id, revision=model_revision)
    model.eval()
    sampling_rate = int(model.config.audio_encoder.sampling_rate)
    resolved_revision = getattr(model.config, "_commit_hash", None) or model_revision

    output_dir.mkdir(parents=True, exist_ok=True)
    max_new_tokens = int(duration_seconds * 50)

    samples: list[dict[str, Any]] = []
    timestamps: list[dict[str, Any]] = []
    for take_index in take_indices:
        seed = seed_base + take_index
        sample_id = f"take{take_index}"
        started_at = _utc_now_iso()
        torch.manual_seed(seed)
        inputs = processor(text=[prompt_text], padding=True, return_tensors="pt")
        audio_values = model.generate(
            **inputs,
            do_sample=True,
            guidance_scale=guidance_scale,
            max_new_tokens=max_new_tokens,
        )
        waveform = audio_values[0, 0].detach().cpu().numpy()
        ended_at = _utc_now_iso()

        audio_path = output_dir / f"{sample_id}.wav"
        wavfile.write(str(audio_path), sampling_rate, waveform)
        audio_sha256 = _sha256_file(audio_path)

        samples.append(
            {
                "sample_id": sample_id,
                "take_index": take_index,
                "seed": seed,
                "audio_path": audio_path.name,
                "audio_sha256": audio_sha256,
                "duration_seconds": duration_seconds,
                "guidance_scale": guidance_scale,
                "model_id": model_id,
                "model_revision": resolved_revision,
            }
        )
        timestamps.append(
            {
                "sample_id": sample_id,
                "take_index": take_index,
                "seed": seed,
                "started_at_utc": started_at,
                "ended_at_utc": ended_at,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "prompt": prompt_text,
        "model_id": model_id,
        "model_revision": resolved_revision,
        "duration_seconds": duration_seconds,
        "guidance_scale": guidance_scale,
        "samples": samples,
        "timestamps": timestamps,
    }


def build_takes_manifest(
    result: dict[str, Any],
    *,
    fixture_id: str,
    package_path: Path,
    package_sha256: str,
) -> dict[str, Any]:
    """`generate_ar4_takes` の戻り値から、コミット対象の
    ``ar4_takes_manifest.json`` 用ペイロード（timestamps を除く）を組み立てる。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "fixture_id": fixture_id,
        "performance_package": {
            "path": str(package_path),
            "sha256": package_sha256,
        },
        "prompt": result["prompt"],
        "model_id": result["model_id"],
        "model_revision": result["model_revision"],
        "duration_seconds": result["duration_seconds"],
        "guidance_scale": result["guidance_scale"],
        "samples": result["samples"],
    }


def build_generation_timestamps(result: dict[str, Any]) -> dict[str, Any]:
    """`generate_ar4_takes` の戻り値から、コミット対象の
    ``ar4_generation_timestamps.yaml`` 用ペイロードを組み立てる。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "generation_calls": result["timestamps"],
    }


def _cmd_generate(args: argparse.Namespace) -> int:
    try:
        prompt_text, package_sha256 = load_package_prompt(args.package)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    take_indices = args.take_index if args.take_index else [0, 1]

    try:
        result = generate_ar4_takes(
            prompt_text,
            output_dir=args.output_dir,
            take_indices=take_indices,
            seed_base=args.seed_base,
            model_id=args.model_id,
            model_revision=args.model_revision,
            duration_seconds=args.duration_seconds,
            guidance_scale=args.guidance_scale,
        )
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    manifest = build_takes_manifest(
        result,
        fixture_id=args.fixture_id,
        package_path=args.package,
        package_sha256=package_sha256,
    )
    args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_out.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(manifest['samples'])} take(s) to {args.output_dir}")
    print(f"wrote manifest to {args.manifest_out}")

    if args.timestamps_out is not None:
        timestamps = build_generation_timestamps(result)
        args.timestamps_out.parent.mkdir(parents=True, exist_ok=True)
        args.timestamps_out.write_text(
            json.dumps(timestamps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote generation timestamps to {args.timestamps_out}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "AR4 実観測バッチ orchestration (MusicGen). CI では実行しない — "
            "package が compile した prompt を読み、n take を生成し、"
            "sha256/タイムスタンプを記録する。"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser(
        "generate",
        help="performance_package.json の prompt から MusicGen で take を生成する（torch 必須）",
    )
    generate_parser.add_argument(
        "--package", type=Path, required=True, help="performance_package.json path"
    )
    generate_parser.add_argument(
        "--output-dir", type=Path, required=True, help="生成 WAV の書き出し先"
    )
    generate_parser.add_argument(
        "--manifest-out", type=Path, required=True, help="takes manifest JSON の出力先"
    )
    generate_parser.add_argument(
        "--timestamps-out",
        type=Path,
        default=None,
        help="生成タイムスタンプ JSON の出力先（省略時は書き出さない）",
    )
    generate_parser.add_argument(
        "--take-index",
        type=int,
        action="append",
        default=None,
        metavar="N",
        help="生成する take index（複数指定可・0-indexed）。省略時は [0, 1]（AR4 canonical n=2）",
    )
    generate_parser.add_argument(
        "--seed-base", type=int, default=DEFAULT_SEED_BASE, help="seed = seed_base + take_index（既定 8000）"
    )
    generate_parser.add_argument(
        "--model-id", type=str, default=DEFAULT_MODEL_ID, help="HuggingFace model id"
    )
    generate_parser.add_argument(
        "--model-revision", type=str, default=None, help="pin する revision（推奨: 40 桁 commit hash）"
    )
    generate_parser.add_argument(
        "--duration-seconds", type=float, default=12.0, help="生成長 [秒]（既定 12.0）"
    )
    generate_parser.add_argument(
        "--guidance-scale", type=float, default=3.0, help="classifier-free guidance scale（既定 3.0）"
    )
    generate_parser.add_argument(
        "--fixture-id", type=str, default=DEFAULT_FIXTURE_ID, help="manifest の fixture_id"
    )
    generate_parser.set_defaults(func=_cmd_generate)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
