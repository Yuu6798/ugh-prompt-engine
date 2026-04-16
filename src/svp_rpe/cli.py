"""cli.py — typer CLI for svp-rpe.

Commands:
  svprpe extract <audio>   → RPE JSON
  svprpe generate <rpe>    → SVP YAML/TXT
  svprpe evaluate ...      → Evaluation JSON
  svprpe run <audio>       → Full pipeline
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
) -> None:
    """Extract RPE from audio file."""
    from svp_rpe.rpe.extractor import extract_rpe_from_file

    console.print(f"[bold]Extracting RPE from {audio}...[/bold]")
    bundle = extract_rpe_from_file(audio)
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
    svp: Optional[str] = typer.Option(None, "--svp", help="Path to SVP YAML (optional)"),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output JSON path"),
) -> None:
    """Evaluate audio against RPE/SVP criteria."""
    from svp_rpe.eval.scorer_integrated import score_integrated
    from svp_rpe.eval.scorer_rpe import score_rpe
    from svp_rpe.eval.scorer_ugher import score_ugher
    from svp_rpe.rpe.extractor import extract_rpe_from_file
    from svp_rpe.svp.generator import generate_svp

    console.print(f"[bold]Evaluating {audio}...[/bold]")
    rpe_bundle = extract_rpe_from_file(audio)
    svp_bundle = generate_svp(rpe_bundle)

    rpe_score = score_rpe(rpe_bundle.physical)
    ugher_score = score_ugher(rpe_bundle, svp_bundle)
    integrated = score_integrated(ugher_score, rpe_score)

    result = {
        "rpe_score": rpe_score.model_dump(),
        "ugher_score": ugher_score.model_dump(),
        "integrated_score": integrated.model_dump(),
    }
    result_json = json.dumps(result, ensure_ascii=False, indent=2)

    if output:
        Path(output).write_text(result_json, encoding="utf-8")
        console.print(f"[green]Evaluation saved to {output}[/green]")
    else:
        console.print(result_json)


@app.command()
def run(
    audio: str = typer.Argument(..., help="Path to WAV/MP3 file"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", help="Output directory"),
    no_save: bool = typer.Option(False, "--no-save", help="Print to stdout only"),
) -> None:
    """Run full pipeline: extract → generate → evaluate."""
    from svp_rpe.eval.scorer_integrated import score_integrated
    from svp_rpe.eval.scorer_rpe import score_rpe
    from svp_rpe.eval.scorer_ugher import score_ugher
    from svp_rpe.rpe.extractor import extract_rpe_from_file
    from svp_rpe.svp.generator import generate_svp
    from svp_rpe.svp.render_yaml import render_yaml

    console.print(f"[bold]Running full pipeline on {audio}...[/bold]")

    # Extract
    rpe_bundle = extract_rpe_from_file(audio)
    console.print("[green]✓[/green] RPE extraction complete")

    # Generate SVP
    svp_bundle = generate_svp(rpe_bundle)
    console.print("[green]✓[/green] SVP generation complete")

    # Evaluate
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


if __name__ == "__main__":
    app()
