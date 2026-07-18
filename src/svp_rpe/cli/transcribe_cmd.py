"""svprpe measure / transcribe."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

import typer
from rich.table import Table

from svp_rpe.cli._app import app, console

if TYPE_CHECKING:
    from svp_rpe.transcribe import MeasurementReport


@app.command()
def measure(
    audio: str = typer.Argument(..., help="Path to WAV/MP3 file"),
    fields: Optional[str] = typer.Option(
        None,
        "--fields",
        help="Comma-separated CompositionScore physical fields; default: all",
    ),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output JSON path"),
) -> None:
    """Measure CompositionScore physical fields from one audio file."""
    from svp_rpe.rpe.extractor import extract_rpe_from_file
    from svp_rpe.transcribe import measure_fields, parse_field_filter, render_measurement_json

    try:
        requested_fields = parse_field_filter(fields)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    console.print(f"[bold]Measuring score fields from {audio}...[/bold]")
    bundle = extract_rpe_from_file(audio)
    report = measure_fields(bundle, requested_fields)
    content = render_measurement_json(report)

    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(content, encoding="utf-8")
        console.print(f"[green]Measurement report saved to {output}[/green]")
    else:
        console.print(_measurement_table(report))


def _measurement_table(report: MeasurementReport) -> Table:
    table = Table(title=f"Measurement: {report.sample_id}")
    table.add_column("score_field")
    table.add_column("sensor")
    table.add_column("raw_value")
    table.add_column("unit")
    table.add_column("score_value")
    for item in report.measurements:
        table.add_row(
            item.score_field,
            item.sensor,
            "" if item.raw_value is None else str(item.raw_value),
            "" if item.unit is None else item.unit,
            "" if item.score_value is None else str(item.score_value),
        )
    return table


@app.command()
def transcribe(
    audio: str = typer.Argument(..., help="Path to WAV/MP3 file"),
    output: Optional[str] = typer.Option(
        None,
        "-o",
        "--output",
        help="Output CompositionScore YAML path",
    ),
    clap_semantic: bool = typer.Option(
        False,
        "--clap-semantic",
        help=(
            "Prepend advisory CLAP semantic-axis readings of the source audio "
            "as a YAML comment block (requires the semantic-embed extra). "
            "Advisory only — does not fill the authored semantic.* fields (DD-D)."
        ),
    ),
    clap_checkpoint: Optional[str] = typer.Option(
        None,
        "--clap-checkpoint",
        help=(
            "Local path to a CLAP checkpoint to pin (e.g. the "
            "fixture-provenance music_audioset_epoch_15_esc_90.14.pt). "
            "Default None keeps upstream's default checkpoint download."
        ),
    ),
    clap_amodel: Optional[str] = typer.Option(
        None,
        "--clap-amodel",
        help=(
            "Audio-tower architecture matching the checkpoint family (the "
            "music_* checkpoints require HTSAT-base). Default None = "
            "upstream default HTSAT-tiny family."
        ),
    ),
) -> None:
    """Transcribe one audio file into a loader-valid draft CompositionScore YAML."""
    from svp_rpe.rpe.extractor import extract_rpe_from_file
    from svp_rpe.transcribe import draft_score, render_draft_score_yaml

    if clap_semantic:
        # Fail fast on the missing optional dependency before the base
        # extraction (probe imports the module; no weight download).
        from svp_rpe.rpe.learned import LearnedModelUnavailable
        from svp_rpe.rpe.learned.clap_adapter import ensure_clap_available

        try:
            ensure_clap_available()
        except LearnedModelUnavailable as exc:
            console.print(str(exc), style="yellow", markup=False)
            raise typer.Exit(code=1) from exc

    bundle = extract_rpe_from_file(audio)
    score = draft_score(bundle)

    advisory = ""
    if clap_semantic:
        from svp_rpe.rpe.learned import LearnedModelUnavailable
        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_axes
        from svp_rpe.transcribe import render_semantic_axes_advisory

        try:
            annotations = extract_clap_semantic_axes(
                audio, checkpoint=clap_checkpoint, amodel=clap_amodel
            )
        except LearnedModelUnavailable as exc:
            console.print(str(exc), style="yellow", markup=False)
            raise typer.Exit(code=1) from exc
        advisory = render_semantic_axes_advisory(annotations)

    content = advisory + render_draft_score_yaml(score)

    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(content, encoding="utf-8")
        console.print(f"[green]Draft CompositionScore saved to {output}[/green]")
    else:
        typer.echo(content, nl=False)
