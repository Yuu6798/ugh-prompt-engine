"""svprpe roundtrip / score-adherence / lyrics-adherence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click
import typer
from rich.table import Table

from svp_rpe.cli._app import (
    DEFAULT_SEPARATION_DEVICE,
    DEFAULT_SEPARATION_MODEL,
    SeparationDeviceOption,
    SeparationModelOption,
    app,
    console,
)


@app.command("roundtrip")
def roundtrip(
    composition_score: str = typer.Argument(..., help="Path to Composition Score YAML"),
    output_format: str = typer.Option(
        "text",
        "--format",
        click_type=click.Choice(["text", "json"]),
        help="Output format: text | json",
    ),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output file path"),
) -> None:
    """Run the deterministic R0 score -> audio -> draft score roundtrip."""

    from svp_rpe.compose import load_composition_score
    from svp_rpe.roundtrip import render_roundtrip_text, run_roundtrip

    score = load_composition_score(composition_score)
    report = run_roundtrip(score)
    if output_format == "json":
        content = json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    else:
        content = render_roundtrip_text(report)

    if output:
        Path(output).write_text(content, encoding="utf-8")
    else:
        typer.echo(content)


@app.command("score-adherence")
def score_adherence_cmd(
    composition_score: str = typer.Argument(..., help="Path to Composition Score YAML"),
    output_format: str = typer.Option(
        "text",
        "--format",
        click_type=click.Choice(["text", "json"]),
        help="Output format: text | json",
    ),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output file path"),
) -> None:
    """Judge whether control_profile-tight fields are kept through compile + roundtrip."""

    from svp_rpe.compose import load_composition_score
    from svp_rpe.roundtrip import render_score_adherence_text, run_score_adherence

    score = load_composition_score(composition_score)
    report = run_score_adherence(score)
    if output_format == "json":
        content = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
    else:
        content = render_score_adherence_text(report)

    if output:
        Path(output).write_text(content, encoding="utf-8")
    else:
        typer.echo(content)


@app.command("identity-rank")
def identity_rank_cmd(
    bundle: str = typer.Argument(..., help="Path to an extracted RPE bundle JSON"),
    references: str = typer.Option(
        ..., "--references", help="Path to a WI2 references YAML (ref_id / score_path / progression_path)"
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        click_type=click.Choice(["text", "json"]),
        help="Output format: text | json",
    ),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output file path"),
) -> None:
    """Rank a take's per-axis closeness to canonical/decoy/other-work references (WI2).

    Reads an already-extracted RPE bundle JSON and a WI2 references YAML
    (each entry: ``ref_id`` / ``score_path`` / optional ``progression_path``,
    resolved relative to the references YAML's own directory) and reports,
    per axis (structure / harmony / key / bpm / brightness), each
    reference's distance from the take and the resulting rank — ties broken
    by ascending ``ref_id``. Like `roundtrip` / `score-adherence` /
    `lyrics-adherence`, this is a descriptive instrument and intentionally
    does not emit a pass/fail verdict or a "closest reference" pick; it also
    reports, per axis, which references could not be observed and why, plus
    the domains (melody/lyrics/clap) this WI2 v0 instrument does not attempt
    to measure at all.
    """
    from svp_rpe.roundtrip.identity_rank import (
        identity_rank_from_paths,
        render_identity_rank_text,
    )

    report = identity_rank_from_paths(bundle, references)
    if output_format == "json":
        content = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
    else:
        content = render_identity_rank_text(report)

    if output:
        Path(output).write_text(content, encoding="utf-8")
    else:
        typer.echo(content)


@app.command("lyrics-adherence")
def lyrics_adherence_cmd(
    audio: str = typer.Argument(..., help="Path to WAV/MP3 file"),
    expected: str = typer.Option(
        ..., "--expected", help="Path to a text file of expected lyric lines (one per line)"
    ),
    lyrics_model: str = typer.Option(
        "small", "--lyrics-model", help="faster-whisper model size (e.g. small, medium)."
    ),
    lyrics_no_separate: bool = typer.Option(
        False,
        "--lyrics-no-separate",
        help="Transcribe the full mix instead of isolating vocals via Demucs first.",
    ),
    separation_model: SeparationModelOption = DEFAULT_SEPARATION_MODEL,
    separation_device: SeparationDeviceOption = DEFAULT_SEPARATION_DEVICE,
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output YAML report path"),
) -> None:
    """Check whether audio sings the ordered expected lyrics (instrument, no verdict).

    Transcribes `audio` with faster-whisper (requires the `lyrics` extra;
    isolates vocals via Demucs first by default) and reports, per expected
    line, the best char-level similarity against the transcription — see
    `eval/lyrics_match.match_lyrics`. Like `roundtrip` / `score-adherence` /
    `audit`, this is a descriptive instrument and intentionally does not
    emit a pass/fail verdict.
    """
    from svp_rpe.eval.lyrics_match import match_lyrics
    from svp_rpe.io.source_separator import SeparatorNotAvailableError
    from svp_rpe.rpe.learned import LearnedModelUnavailable
    from svp_rpe.rpe.learned.lyrics_adapter import (
        ensure_lyrics_available,
        lyrics_model_info,
        transcribe_lyrics,
    )

    try:
        ensure_lyrics_available()
    except LearnedModelUnavailable as exc:
        console.print(str(exc), style="yellow", markup=False)
        raise typer.Exit(code=1) from exc

    expected_lines = Path(expected).read_text(encoding="utf-8").splitlines()

    try:
        transcription = transcribe_lyrics(
            audio,
            separate_vocals=not lyrics_no_separate,
            separation_model=separation_model,
            separation_device=separation_device,
            model_size=lyrics_model,
        )
    except SeparatorNotAvailableError as exc:
        console.print(str(exc), style="yellow", markup=False)
        raise typer.Exit(code=1) from exc

    report = match_lyrics(expected_lines, transcription.text)

    table = Table(title=f"Lyrics adherence: {audio}")
    table.add_column("expected")
    table.add_column("best_ratio")
    table.add_column("best_match")
    table.add_column("out_of_order")
    for line in report["lines"]:
        best_match = line["best_match"]
        truncated = best_match if len(best_match) <= 60 else f"{best_match[:57]}..."
        table.add_row(
            line["expected"],
            f"{line['best_ratio']:.4f}",
            truncated,
            # Text marker, not a color-only signal; blank keeps in-order
            # rows quiet so the regressed rows stand out.
            "yes" if line["out_of_order"] else "",
        )
    console.print(table)
    console.print(f"overall_similarity: {report['overall_similarity']:.4f}")
    console.print(f"order_ratio: {report['order_ratio']:.4f}")

    if output:
        import yaml

        # `model` = the resolved weights/license provenance record
        # (lyrics_model_info resolves shorthands like `turbo` to the repo
        # actually downloaded), so the saved report is auditable on its
        # own — inference_config alone does not carry the resolved repo.
        payload = {
            **report,
            "model": lyrics_model_info(lyrics_model).model_dump(mode="json"),
            "inference_config": transcription.inference_config,
        }
        Path(output).write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        console.print(f"[green]Report saved to {output}[/green]")
