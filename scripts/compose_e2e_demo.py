"""Composition E2E demo (C4): score -> prompt -> deterministic performance -> audit.

The deterministic performer now lives in :mod:`svp_rpe.perform` so that CLI and
wheel users can import it. This script remains a backwards-compatible demo and
re-export surface for existing tests and utility scripts.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from svp_rpe.compose import ExternalPromptAdapter, load_composition_score  # noqa: E402
from svp_rpe.compose.models import CompositionScore  # noqa: E402
from svp_rpe.perform import (  # noqa: E402
    FAITHFUL_TAKE,
    FIRST_TAKE,
    STYLES,
    PerformanceStyle,
    parse_key,
    perform,
    scaled_score,
    sha256_bytes,
    wav_bytes,
)

SCORE_PATH = ROOT / "examples" / "composition" / "midnight_signal" / "composition_score.yaml"
DEFAULT_OUTPUT_DIR = ROOT / "examples" / "composition" / "midnight_signal" / "e2e"

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "FAITHFUL_TAKE",
    "FIRST_TAKE",
    "SCORE_PATH",
    "STYLES",
    "PerformanceStyle",
    "parse_key",
    "perform",
    "scaled_score",
]


def run_take(
    score: CompositionScore,
    style: PerformanceStyle,
    output_dir: Path,
) -> dict[str, Any]:
    """Run one deterministic take through perform, extract, and audit."""

    from svp_rpe.rpe.extractor import extract_rpe_from_file
    from svp_rpe.semantic_ci.audit import build_audit_report, render_audit_text

    samples = perform(score, style)
    wav_path = output_dir / f"{style.name}.wav"
    wav_data = wav_bytes(samples)
    wav_path.write_bytes(wav_data)

    bundle = extract_rpe_from_file(str(wav_path))
    rpe_payload = bundle.model_dump(mode="json")
    (output_dir / f"{style.name}_rpe.json").write_text(
        json.dumps(rpe_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    report = build_audit_report(score, bundle, observed_id=style.name)
    report_payload = report.model_dump(mode="json")
    report_md = render_audit_text(report)
    (output_dir / f"{style.name}_audit.json").write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / f"{style.name}_audit.md").write_text(report_md, encoding="utf-8")
    return {
        "style": style.name,
        "wav_sha256": sha256_bytes(wav_data),
        "report": report_payload,
        "report_md": report_md,
    }


def _needle_index(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    needles: dict[str, dict[str, Any]] = {}
    for layer in ("physical", "semantic"):
        for needle in report[layer]:
            needles[needle["name"]] = needle
    return needles


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item)
    return str(value)


def render_summary(takes: list[dict[str, Any]]) -> str:
    """Render a markdown comparison of the two deterministic takes."""

    first, faithful = takes[0], takes[1]
    first_needles = _needle_index(first["report"])
    faithful_needles = _needle_index(faithful["report"])
    lines = [
        "# Composition E2E Needle Comparison - Midnight Signal",
        "",
        "Same score, two deterministic performance styles, compared as audit needles.",
        "",
        f"- score_id: {first['report']['score_id']}",
        f"- take 1: `{first['style']}` (wav sha256 `{first['wav_sha256'][:12]}...`)",
        f"- take 2: `{faithful['style']}` (wav sha256 `{faithful['wav_sha256'][:12]}...`)",
        "",
        "| knob | layer | target | first_take | faithful_take | dev (first) | dev (faithful) | movement |",
        "|---|---|---|---|---|---:|---:|---|",
    ]
    for name, first_needle in first_needles.items():
        faithful_needle = faithful_needles.get(name)
        if faithful_needle is None:
            continue
        dev_first = first_needle.get("deviation")
        dev_faithful = faithful_needle.get("deviation")
        movement = ""
        if dev_first is not None and dev_faithful is not None:
            if abs(dev_faithful) < abs(dev_first):
                movement = "toward target"
            elif abs(dev_faithful) > abs(dev_first):
                movement = "away"
            else:
                movement = "flat"
        lines.append(
            "| "
            f"{name} | "
            f"{first_needle['layer']} | "
            f"{_format_value(first_needle.get('target'))} | "
            f"{_format_value(first_needle.get('observed'))} | "
            f"{_format_value(faithful_needle.get('observed'))} | "
            f"{_format_value(dev_first)} | "
            f"{_format_value(dev_faithful)} | "
            f"{movement} |"
        )
    return "\n".join(lines) + "\n"


def run_demo(
    score_path: Path = SCORE_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    """Run the full deterministic composition demo and write artifacts."""

    output_dir.mkdir(parents=True, exist_ok=True)
    gitignore_path = output_dir / ".gitignore"
    required_patterns = ("*.wav", "*_rpe.json", "generated_prompt.txt")
    existing_lines = (
        gitignore_path.read_text(encoding="utf-8").splitlines()
        if gitignore_path.is_file()
        else []
    )
    missing_patterns = [item for item in required_patterns if item not in existing_lines]
    if missing_patterns:
        gitignore_path.write_text(
            "\n".join([*existing_lines, *missing_patterns]) + "\n", encoding="utf-8"
        )

    score = load_composition_score(str(score_path))
    prompt = ExternalPromptAdapter().render(score)
    (output_dir / "generated_prompt.txt").write_text(prompt.text + "\n", encoding="utf-8")

    takes = [run_take(score, style, output_dir) for style in STYLES]
    summary = render_summary(takes)
    (output_dir / "needle_comparison.md").write_text(summary, encoding="utf-8")
    return {"prompt": prompt.model_dump(mode="json"), "takes": takes, "summary": summary}


def verify(
    score_path: Path = SCORE_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> int:
    """Verify committed deterministic demo artifacts against regenerated output."""

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        regenerated = run_demo(score_path=score_path, output_dir=Path(tmp))
    ok = True
    for take in regenerated["takes"]:
        name = take["style"]
        committed_path = output_dir / f"{name}_audit.json"
        if not committed_path.is_file():
            print(f"Missing artifact: {committed_path}", file=sys.stderr)
            ok = False
            continue
        committed_audit = json.loads(committed_path.read_text(encoding="utf-8"))
        if committed_audit != take["report"]:
            print(f"Audit report drift for {name}", file=sys.stderr)
            ok = False
        md_path = output_dir / f"{name}_audit.md"
        if not md_path.is_file():
            print(f"Missing artifact: {md_path}", file=sys.stderr)
            ok = False
        elif md_path.read_text(encoding="utf-8") != take["report_md"]:
            print(f"Audit markdown drift for {name}", file=sys.stderr)
            ok = False
    summary_path = output_dir / "needle_comparison.md"
    if not summary_path.is_file():
        print(f"Missing artifact: {summary_path}", file=sys.stderr)
        ok = False
    elif summary_path.read_text(encoding="utf-8") != regenerated["summary"]:
        print("needle_comparison.md drift", file=sys.stderr)
        ok = False
    if ok:
        print(f"Verified composition E2E artifacts in {output_dir}")
        return 0
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", type=Path, default=SCORE_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--verify", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.verify:
        return verify(score_path=args.score, output_dir=args.output_dir)
    result = run_demo(score_path=args.score, output_dir=args.output_dir)
    print(result["summary"])
    print(f"Artifacts written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
