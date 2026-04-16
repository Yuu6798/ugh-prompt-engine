"""cli.py — typer CLI for svp-rpe.

Commands:
  svprpe extract <audio>        → RPE JSON
  svprpe generate <rpe>         → SVP YAML/TXT
  svprpe evaluate --audio <wav> → Evaluation JSON (self or with --svp)
  svprpe compare ...            → Reference vs candidate comparison
  svprpe run <audio>            → Full pipeline
  svprpe batch <dir>            → Batch processing
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

app = typer.Typer(
    name="svprpe",
    help="SVP-RPE: Audio analysis → RPE extraction → SVP generation → Evaluation",
)
console = Console()


@app.command()
def extract(
    audio: str = typer.Argument(..., help="Path to WAV/MP3 file"),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output JSON path"),
    valley_method: str = typer.Option("hybrid", "--valley-method",
                                       help="Valley method: rms_percentile/section_ar/hybrid"),
) -> None:
    """Extract RPE from audio file."""
    from svp_rpe.rpe.extractor import extract_rpe_from_file

    console.print(f"[bold]Extracting RPE from {audio}...[/bold]")
    bundle = extract_rpe_from_file(audio, valley_method=valley_method)
    result = bundle.model_dump()
    result_json = json.dumps(result, ensure_ascii=False, indent=2)

    if output:
        Path(output).write_text(result_json, encoding="utf-8")
        console.print(f"[green]RPE saved to {output}[/green]")
    else:
        console.print(result_json)


@app.command()
def generate(
    rpe_json: str = typer.Argument(..., help="Path to RPE JSON"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", help="Output directory"),
    fmt: str = typer.Option("yaml", "--format", help="Output format: yaml | text"),
) -> None:
    """Generate SVP from RPE JSON."""
    from svp_rpe.rpe.models import RPEBundle
    from svp_rpe.svp.generator import generate_svp
    from svp_rpe.svp.render_text import render_text
    from svp_rpe.svp.render_yaml import render_yaml

    console.print(f"[bold]Generating SVP from {rpe_json}...[/bold]")
    rpe_data = json.loads(Path(rpe_json).read_text(encoding="utf-8"))
    bundle = RPEBundle(**rpe_data)
    svp = generate_svp(bundle)

    if fmt == "yaml":
        content = render_yaml(svp)
        ext = "yaml"
    else:
        content = render_text(svp)
        ext = "md"

    if output_dir:
        out_path = Path(output_dir) / f"svp.{ext}"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        console.print(f"[green]SVP saved to {out_path}[/green]")
    else:
        console.print(content)


@app.command()
def evaluate(
    audio: str = typer.Option(..., "--audio", help="Path to audio file"),
    svp: Optional[str] = typer.Option(None, "--svp", help="Path to external SVP file"),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output JSON path"),
    valley_method: str = typer.Option("hybrid", "--valley-method",
                                       help="Valley method: rms_percentile/section_ar/hybrid"),
) -> None:
    """Evaluate audio. With --svp: compare against external SVP. Without: self-evaluate."""
    from svp_rpe.eval.scorer_integrated import score_integrated
    from svp_rpe.eval.scorer_rpe import score_rpe
    from svp_rpe.eval.scorer_ugher import score_ugher
    from svp_rpe.rpe.extractor import extract_rpe_from_file
    from svp_rpe.svp.generator import generate_svp

    console.print(f"[bold]Evaluating {audio}...[/bold]")
    rpe_bundle = extract_rpe_from_file(audio, valley_method=valley_method)
    svp_bundle = generate_svp(rpe_bundle)

    rpe_score = score_rpe(rpe_bundle.physical)
    ugher_score = score_ugher(rpe_bundle, svp_bundle)
    integrated = score_integrated(ugher_score, rpe_score)

    result: dict = {
        "mode": "self",
        "rpe_score": rpe_score.model_dump(),
        "ugher_score": ugher_score.model_dump(),
        "integrated_score": integrated.model_dump(),
    }

    # If external SVP provided, run comparison
    if svp:
        from svp_rpe.eval.comparison import compare_rpe_vs_svp
        from svp_rpe.svp.parser import load_svp

        console.print(f"[bold]Comparing against external SVP: {svp}[/bold]")
        parsed_svp = load_svp(svp)
        comp = compare_rpe_vs_svp(rpe_bundle, parsed_svp)
        result["mode"] = "compare"
        result["comparison"] = comp.model_dump()
        result["action_hints"] = comp.action_hints

    result_json = json.dumps(result, ensure_ascii=False, indent=2)

    if output:
        Path(output).write_text(result_json, encoding="utf-8")
        console.print(f"[green]Evaluation saved to {output}[/green]")
    else:
        console.print(result_json)


@app.command()
def compare(
    reference_audio: str = typer.Option(..., "--reference-audio", help="Reference audio file"),
    candidate_audio: Optional[str] = typer.Option(None, "--candidate-audio",
                                                    help="Candidate audio file"),
    reference_svp: Optional[str] = typer.Option(None, "--reference-svp",
                                                  help="Reference SVP file"),
    candidate_svp: Optional[str] = typer.Option(None, "--candidate-svp",
                                                  help="Candidate SVP file"),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output JSON path"),
    valley_method: str = typer.Option("hybrid", "--valley-method",
                                       help="Valley method: rms_percentile/section_ar/hybrid"),
) -> None:
    """Compare reference audio against candidate audio/SVP."""
    from svp_rpe.eval.comparison import compare_rpe_vs_svp
    from svp_rpe.rpe.extractor import extract_rpe_from_file
    from svp_rpe.svp.parser import load_svp

    console.print(f"[bold]Extracting RPE from reference: {reference_audio}...[/bold]")
    ref_rpe = extract_rpe_from_file(reference_audio, valley_method=valley_method)

    # Determine comparison target
    candidate_phys = None
    if candidate_audio:
        console.print(f"[bold]Extracting RPE from candidate: {candidate_audio}...[/bold]")
        cand_rpe = extract_rpe_from_file(candidate_audio, valley_method=valley_method)
        candidate_phys = cand_rpe.physical

    # Determine SVP to compare against
    if candidate_svp:
        parsed_svp = load_svp(candidate_svp)
    elif reference_svp:
        parsed_svp = load_svp(reference_svp)
    else:
        # Auto-generate SVP from reference
        from svp_rpe.svp.generator import generate_svp

        svp_bundle = generate_svp(ref_rpe)
        from svp_rpe.eval.diff_models import ParsedSVP
        parsed_svp = ParsedSVP(
            por_core=svp_bundle.analysis_rpe.por_core,
            por_surface=svp_bundle.analysis_rpe.por_surface,
            grv_primary=svp_bundle.analysis_rpe.grv_primary,
            bpm=svp_bundle.analysis_rpe.bpm,
            key=svp_bundle.analysis_rpe.key,
            mode=svp_bundle.analysis_rpe.mode,
            duration_sec=svp_bundle.analysis_rpe.duration_sec,
            constraints=svp_bundle.svp_for_generation.constraints,
            style_tags=svp_bundle.svp_for_generation.style_tags,
            delta_e_profile=svp_bundle.minimal_svp.de,
        )

    comp = compare_rpe_vs_svp(ref_rpe, parsed_svp, candidate_phys=candidate_phys)

    result = comp.model_dump()
    result["reference_source"] = reference_audio
    result["candidate_source"] = candidate_audio or candidate_svp or "auto-generated"

    result_json = json.dumps(result, ensure_ascii=False, indent=2)

    if output:
        Path(output).write_text(result_json, encoding="utf-8")
        console.print(f"[green]Comparison saved to {output}[/green]")
    else:
        console.print(result_json)


@app.command()
def run(
    audio: str = typer.Argument(..., help="Path to WAV/MP3 file"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", help="Output directory"),
    no_save: bool = typer.Option(False, "--no-save", help="Print to stdout only"),
    valley_method: str = typer.Option("hybrid", "--valley-method",
                                       help="Valley method: rms_percentile/section_ar/hybrid"),
) -> None:
    """Run full pipeline: extract → generate → evaluate."""
    from svp_rpe.eval.scorer_integrated import score_integrated
    from svp_rpe.eval.scorer_rpe import score_rpe
    from svp_rpe.eval.scorer_ugher import score_ugher
    from svp_rpe.rpe.extractor import extract_rpe_from_file
    from svp_rpe.svp.generator import generate_svp
    from svp_rpe.svp.render_yaml import render_yaml

    console.print(f"[bold]Running full pipeline on {audio}...[/bold]")

    rpe_bundle = extract_rpe_from_file(audio, valley_method=valley_method)
    console.print("[green]✓[/green] RPE extraction complete")

    svp_bundle = generate_svp(rpe_bundle)
    console.print("[green]✓[/green] SVP generation complete")

    rpe_score = score_rpe(rpe_bundle.physical)
    ugher_score = score_ugher(rpe_bundle, svp_bundle)
    integrated = score_integrated(ugher_score, rpe_score)
    console.print("[green]✓[/green] Evaluation complete")

    console.print(f"\n[bold]Integrated Score: {integrated.integrated_score:.4f}[/bold]")
    console.print(f"  UGHer: {ugher_score.overall:.4f}  |  RPE: {rpe_score.overall:.4f}")

    if no_save:
        console.print("\n--- RPE ---")
        console.print(json.dumps(rpe_bundle.model_dump(), ensure_ascii=False, indent=2))
        console.print("\n--- SVP ---")
        console.print(render_yaml(svp_bundle))
        return

    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        (out / "rpe.json").write_text(
            json.dumps(rpe_bundle.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out / "svp.yaml").write_text(render_yaml(svp_bundle), encoding="utf-8")
        (out / "evaluation.json").write_text(
            json.dumps({
                "rpe_score": rpe_score.model_dump(),
                "ugher_score": ugher_score.model_dump(),
                "integrated_score": integrated.model_dump(),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        console.print(f"\n[green]All outputs saved to {out}/[/green]")
    else:
        console.print("\n[dim]Use --output-dir to save files, or --no-save to print.[/dim]")


@app.command()
def batch(
    audio_dir: str = typer.Argument(..., help="Directory containing audio files"),
    svp_dir: Optional[str] = typer.Option(None, "--svp-dir", help="Directory with SVP candidates"),
    mode: str = typer.Option("evaluate", "--mode", help="Mode: evaluate | compare"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", help="Output directory"),
) -> None:
    """Batch process multiple audio files."""
    from svp_rpe.batch.runner import run_batch

    console.print(f"[bold]Batch processing {audio_dir}...[/bold]")
    summary = run_batch(
        audio_dir,
        svp_dir=svp_dir,
        mode=mode,
        output_dir=output_dir,
    )

    console.print(f"\n[bold]Results: {summary['successful']}/{summary['total_files']} successful[/bold]")

    if summary.get("ranking"):
        console.print("\n[bold]Ranking:[/bold]")
        for entry in summary["ranking"][:10]:
            console.print(f"  {entry['rank']}. {entry['audio']} — {entry['score']:.4f}")

    if output_dir:
        console.print(f"\n[green]Reports saved to {output_dir}/[/green]")


if __name__ == "__main__":
    app()
