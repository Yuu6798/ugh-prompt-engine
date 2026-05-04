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
    include_stems: bool = False,
    separation_model: str = "htdemucs_ft",
    separation_device: str = "cpu",
) -> dict:
    """Run batch processing on a directory of audio files.

    Returns a summary dict with rankings and per-file results.
    """
    def discover_pairs() -> tuple[list[Path], list[tuple[Path, list[Path]]]]:
        audio_files = discover_audio_files(audio_dir)
        if not audio_files:
            return [], []

        svp_files = discover_svp_files(svp_dir) if svp_dir else []
        pairs = match_audio_to_svp(audio_files, svp_files) if svp_files else [
            (audio_path, []) for audio_path in audio_files
        ]
        return audio_files, pairs

    def compare_against_svp_candidates(rpe_bundle, svp_paths: list[Path]) -> list[dict]:
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
        return comparisons

    def process_audio_entry(audio_path: Path, svp_paths: list[Path]) -> dict:
        rpe_bundle = extract_rpe_from_file(
            str(audio_path),
            include_stems=include_stems,
            separation_model=separation_model,
            separation_device=separation_device,
        )
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

        if mode == "compare" and svp_paths:
            entry["comparisons"] = compare_against_svp_candidates(rpe_bundle, svp_paths)

        return entry

    def rank_successful_results(results: list[dict]) -> list[dict]:
        return sorted(
            [result for result in results if "integrated_score" in result],
            key=lambda result: result["integrated_score"],
            reverse=True,
        )

    def build_summary(audio_files: list[Path], results: list[dict], ranked: list[dict]) -> dict:
        return {
            "total_files": len(audio_files),
            "successful": len([result for result in results if "error" not in result]),
            "failed": len([result for result in results if "error" in result]),
            "baseline_profile": baseline,
            "ranking": [
                {"rank": index + 1, "audio": result["audio"], "score": result["integrated_score"]}
                for index, result in enumerate(ranked)
            ],
            "results": results,
        }

    def render_summary_csv(ranked: list[dict]) -> str:
        csv_lines = ["rank,audio,integrated_score,ugher_score,rpe_score,baseline_profile"]
        for index, result in enumerate(ranked):
            ugher = result.get("ugher_score")
            rpe_score_value = result.get("rpe_score")
            ugher_str = f"{ugher:.4f}" if isinstance(ugher, float) else "N/A"
            rpe_str = f"{rpe_score_value:.4f}" if isinstance(rpe_score_value, float) else "N/A"
            csv_lines.append(
                f"{index + 1},{result['audio']},{result['integrated_score']:.4f},"
                f"{ugher_str},{rpe_str},{result.get('baseline_profile', baseline)}"
            )
        return "\n".join(csv_lines)

    def render_next_actions(ranked: list[dict]) -> str:
        md_lines = ["# Next Actions\n"]
        for result in ranked[:5]:
            md_lines.append(f"## {result['audio']} (score: {result['integrated_score']:.4f})\n")
            if "comparisons" in result:
                for comparison in result["comparisons"]:
                    if "action_hints" in comparison:
                        for hint in comparison["action_hints"]:
                            md_lines.append(f"- {hint}")
                md_lines.append("")
        return "\n".join(md_lines)

    def write_batch_outputs(output_dir: str, summary: dict, ranked: list[dict]) -> None:
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
        (out / "summary.csv").write_text(
            render_summary_csv(ranked),
            encoding="utf-8",
        )
        (out / "next_action.md").write_text(
            render_next_actions(ranked),
            encoding="utf-8",
        )

    audio_files, pairs = discover_pairs()
    if not audio_files:
        return {"error": "no audio files found", "results": []}

    results = []

    for audio_path, svp_paths in pairs:
        try:
            entry = process_audio_entry(audio_path, svp_paths)
            results.append(entry)
        except Exception as e:
            results.append({
                "audio": str(audio_path.name),
                "error": str(e),
            })

    ranked = rank_successful_results(results)
    summary = build_summary(audio_files, results, ranked)

    if output_dir:
        write_batch_outputs(output_dir, summary, ranked)

    return summary
