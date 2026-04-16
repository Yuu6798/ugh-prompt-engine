"""cli.py — typer CLI for svp-rpe."""
from __future__ import annotations

import typer

app = typer.Typer(
    name="svprpe",
    help="SVP-RPE: Audio analysis → RPE extraction → SVP generation → Evaluation",
)


@app.command()
def extract(
    audio: str = typer.Argument(..., help="Path to WAV/MP3 file"),
    output: str = typer.Option(None, "-o", "--output", help="Output JSON path"),
) -> None:
    """Extract RPE from audio file."""
    typer.echo(f"[stub] extract: {audio}")


@app.command()
def generate(
    rpe_json: str = typer.Argument(..., help="Path to RPE JSON"),
    output_dir: str = typer.Option(None, "--output-dir", help="Output directory"),
    fmt: str = typer.Option("yaml", "--format", help="Output format: yaml | text"),
) -> None:
    """Generate SVP from RPE JSON."""
    typer.echo(f"[stub] generate: {rpe_json}")


@app.command()
def evaluate(
    audio: str = typer.Option(..., "--audio", help="Path to audio file"),
    svp: str = typer.Option(..., "--svp", help="Path to SVP YAML"),
    output: str = typer.Option(None, "-o", "--output", help="Output JSON path"),
) -> None:
    """Evaluate SVP against audio."""
    typer.echo(f"[stub] evaluate: audio={audio}, svp={svp}")


@app.command()
def run(
    audio: str = typer.Argument(..., help="Path to WAV/MP3 file"),
    output_dir: str = typer.Option(None, "--output-dir", help="Output directory"),
    no_save: bool = typer.Option(False, "--no-save", help="Print to stdout only"),
) -> None:
    """Run full pipeline: extract → generate → evaluate."""
    typer.echo(f"[stub] run: {audio}")


if __name__ == "__main__":
    app()
