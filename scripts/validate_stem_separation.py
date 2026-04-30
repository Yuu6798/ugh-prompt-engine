"""Validate Q3 stem separation residual and per-stem BPM alignment.

This script is a manual sign-off tool for real audio. It intentionally keeps
Demucs optional: CI uses synthetic stem tests, while this script runs only when
the local environment has `svp-rpe[separate]` installed.

Usage:
    python scripts/validate_stem_separation.py track.wav
    python scripts/validate_stem_separation.py track.wav --check
    python scripts/validate_stem_separation.py track.wav --json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from svp_rpe.eval.stem_validation import (  # noqa: E402
    DEFAULT_STEM_BPM_TOLERANCE,
    DEFAULT_STEM_RESIDUAL_THRESHOLD,
    validate_stem_bpm_alignment,
    validate_stem_reconstruction,
)
from svp_rpe.io.audio_loader import load_audio  # noqa: E402
from svp_rpe.io.source_separator import (  # noqa: E402
    DEFAULT_MODEL,
    SeparatorNotAvailableError,
    separate_stems,
)
from svp_rpe.rpe.extractor import extract_physical  # noqa: E402


def validate_file(
    audio_path: Path,
    *,
    model: str,
    device: str,
    residual_threshold: float,
    bpm_tolerance: float,
) -> dict[str, Any]:
    # Keep the full mix at native sample rate so summed-stem residual compares
    # waveforms on the same sample grid as Demucs output.
    audio = load_audio(str(audio_path), target_sr=None)
    stem_bundle = separate_stems(audio_path, model=model, device=device)
    physical, _, _ = extract_physical(audio, stem_bundle=stem_bundle)

    residual = validate_stem_reconstruction(
        audio,
        stem_bundle,
        threshold=residual_threshold,
    )
    bpm_alignment = validate_stem_bpm_alignment(
        physical,
        tolerance=bpm_tolerance,
    )

    return {
        "audio": str(audio_path),
        "model": model,
        "device": device,
        "residual": asdict(residual),
        "bpm_alignment": asdict(bpm_alignment),
        "passed": residual.passed and bpm_alignment.passed,
    }


def _print_markdown(result: dict[str, Any]) -> None:
    residual = result["residual"]
    bpm = result["bpm_alignment"]
    print("# Q3 Stem Separation Validation")
    print()
    print(f"- audio: `{result['audio']}`")
    print(f"- model: `{result['model']}`")
    print(f"- device: `{result['device']}`")
    print(f"- overall: {'PASS' if result['passed'] else 'FAIL'}")
    print()
    print("| Check | Value | Threshold | Result |")
    print("|---|---:|---:|---|")
    print(
        f"| summed-stem residual ratio | {residual['residual_ratio']:.6f} | "
        f"{residual['threshold']:.6f} | {'PASS' if residual['passed'] else 'FAIL'} |"
    )
    print()
    print(f"Full-mix BPM: `{bpm['full_bpm']}`")
    print()
    print("| Stem | BPM | Diff | Tolerance | Result |")
    print("|---|---:|---:|---:|---|")
    for stem_name, stem_bpm in bpm["stem_bpms"].items():
        diff = bpm["bpm_diffs"][stem_name]
        bpm_text = "None" if stem_bpm is None else f"{stem_bpm:.2f}"
        diff_text = "None" if diff is None else f"{diff:.4f}"
        passed = diff is not None and diff <= bpm["tolerance"]
        result_text = "PASS" if passed else "FAIL"
        print(
            f"| {stem_name} | {bpm_text} | {diff_text} | "
            f"{bpm['tolerance']:.2f} | {result_text} |"
        )
    if bpm["missing_stems"]:
        print()
        print(f"Missing stems: {', '.join(bpm['missing_stems'])}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Q3 summed-stem residual and per-stem BPM alignment.",
    )
    parser.add_argument("audio", type=Path, help="Audio file to separate and validate")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Demucs model name")
    parser.add_argument("--device", default="cpu", help="Demucs device, e.g. cpu or cuda")
    parser.add_argument(
        "--residual-threshold",
        type=float,
        default=DEFAULT_STEM_RESIDUAL_THRESHOLD,
        help="Maximum rms(source - sum(stems)) / rms(source)",
    )
    parser.add_argument(
        "--bpm-tolerance",
        type=float,
        default=DEFAULT_STEM_BPM_TOLERANCE,
        help="Maximum BPM difference between each stem and full mix",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--check", action="store_true", help="Exit 1 when validation fails")
    args = parser.parse_args()

    try:
        result = validate_file(
            args.audio,
            model=args.model,
            device=args.device,
            residual_threshold=args.residual_threshold,
            bpm_tolerance=args.bpm_tolerance,
        )
    except SeparatorNotAvailableError as exc:
        print(f"Demucs is not available: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_markdown(result)

    if args.check and not result["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
