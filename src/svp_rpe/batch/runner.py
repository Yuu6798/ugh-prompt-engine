"""batch/runner.py — Batch processing engine."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from svp_rpe.batch.discovery import discover_audio_files, discover_svp_files, match_audio_to_svp
from svp_rpe.eval.comparison import compare_rpe_vs_svp
from svp_rpe.eval.scorer_integrated import score_integrated
from svp_rpe.eval.scorer_rpe import score_rpe
from svp_rpe.eval.scorer_ugher import score_ugher
from svp_rpe.rpe.extractor import extract_rpe_from_file
from svp_rpe.svp.generator import generate_svp
from svp_rpe.svp.parser import load_svp


def run_batch(
    audio_dir: str,
    *,
    svp_dir: Optional[str] = None,
    mode: str = "evaluate",  # "evaluate" | "compare"
    output_dir: Optional[str] = None,
    baseline: str = "pro",
) -> dict:
    """Run batch processing on a directory of audio files.

    Returns a summary dict with rankings and per-file results.
    """
    audio_files = discover_audio_files(audio_dir)
    if not audio_files:
        return {"error": "no audio files found", "results": []}

    svp_files = discover_svp_files(svp_dir) if svp_dir else []
    pairs = match_audio_to_svp(audio_files, svp_files) if svp_files else [
        (a, []) for a in audio_files
    ]

    results = []

    for audio_path, svp_paths in pairs:
        try:
            rpe_bundle = extract_rpe_from_file(str(audio_path))
            svp_bundle = generate_svp(rpe_bundle)
            rpe_score = score_rpe(rpe_bundle.physical, baseline=baseline)
            ugher_score = score_ugher(rpe_bundle, svp_bundle)
            integrated = score_integrated(ugher_score, rpe_score)

            entry = {
                "audio": str(audio_path.name),
                "integrated_score": integrated.integrated_score,
                "ugher_score": ugher_score.overall,
                "rpe_score": rpe_score.overall,
                "baseline_profile": rpe_score.baseline_profile,
            }

            # Compare mode: evaluate against each SVP candidate
            if mode == "compare" and svp_paths:
                comparisons = []
                for svp_path in svp_paths:
                    try:
                        parsed_svp = load_svp(str(svp_path))
                        comp = compare_rpe_vs_svp(rpe_bundle, parsed_svp)
                        comparisons.append({
                            "svp_file": str(svp_path.name),
                            "comparison_score": comp.overall_score,
                            "action_hints": comp.action_hints,
                            "semantic_diff": comp.semantic_diff.model_dump(),
                            "physical_diff": comp.physical_diff.model_dump(),
                        })
                    except Exception as e:
                        comparisons.append({
                            "svp_file": str(svp_path.name),
                            "error": str(e),
                        })
                entry["comparisons"] = comparisons

            results.append(entry)

        except Exception as e:
            results.append({
                "audio": str(audio_path.name),
                "error": str(e),
            })

    # Sort by integrated score (descending)
    ranked = sorted(
        [r for r in results if "integrated_score" in r],
        key=lambda x: x["integrated_score"],
        reverse=True,
    )

    summary = {
        "total_files": len(audio_files),
        "successful": len([r for r in results if "error" not in r]),
        "failed": len([r for r in results if "error" in r]),
        "baseline_profile": baseline,
        "ranking": [
            {"rank": i + 1, "audio": r["audio"], "score": r["integrated_score"]}
            for i, r in enumerate(ranked)
        ],
        "results": results,
    }

    # Save if output_dir specified
    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        (out / "ranking.json").write_text(
            json.dumps(summary["ranking"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Generate summary.csv
        csv_lines = ["rank,audio,integrated_score,ugher_score,rpe_score,baseline_profile"]
        for i, r in enumerate(ranked):
            ugher = r.get("ugher_score")
            rpe_s = r.get("rpe_score")
            ugher_str = f"{ugher:.4f}" if isinstance(ugher, float) else "N/A"
            rpe_str = f"{rpe_s:.4f}" if isinstance(rpe_s, float) else "N/A"
            csv_lines.append(
                f"{i+1},{r['audio']},{r['integrated_score']:.4f},"
                f"{ugher_str},{rpe_str},{r.get('baseline_profile', baseline)}"
            )
        (out / "summary.csv").write_text("\n".join(csv_lines), encoding="utf-8")

        # Generate next_action.md
        md_lines = ["# Next Actions\n"]
        for r in ranked[:5]:
            md_lines.append(f"## {r['audio']} (score: {r['integrated_score']:.4f})\n")
            if "comparisons" in r:
                for comp in r["comparisons"]:
                    if "action_hints" in comp:
                        for hint in comp["action_hints"]:
                            md_lines.append(f"- {hint}")
                md_lines.append("")
        (out / "next_action.md").write_text("\n".join(md_lines), encoding="utf-8")

    return summary
