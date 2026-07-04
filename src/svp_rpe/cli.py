"""cli.py — typer CLI for svp-rpe.

Commands:
  svprpe extract <audio>        → RPE JSON
  svprpe generate <rpe>         → SVP YAML/TXT
  svprpe compose <score>        → Composition Score prompt
  svprpe evaluate --audio <wav> → Evaluation JSON (self or with --svp)
  svprpe compare ...            → Reference vs candidate comparison
  svprpe ci-check ...           → Deterministic semantic CI fixture check
  svprpe run <audio>            → Full pipeline
  svprpe batch <dir>            → Batch processing
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Optional

import click
import typer
from rich.console import Console
from rich.table import Table

from svp_rpe.eval.scorer_rpe import BASELINE_CONFIGS

if TYPE_CHECKING:
    from svp_rpe.transcribe import MeasurementReport

app = typer.Typer(
    name="svprpe",
    help="SVP-RPE: Audio analysis → RPE extraction → SVP generation → Evaluation",
)
console = Console()
BASELINE_PROFILE_CHOICE = click.Choice(sorted(BASELINE_CONFIGS))
BASELINE_PROFILE_HELP = "RPE baseline profile used as scoring reference."
DEFAULT_SEPARATION_MODEL = "htdemucs_ft"
DEFAULT_SEPARATION_DEVICE = "cpu"
SEPARATE_HELP = "Enable Demucs stem separation (opt-in). Requires demucs installed."
SEPARATION_MODEL_HELP = "Demucs model name used when --separate is set."
SEPARATION_DEVICE_HELP = "Demucs inference device used when --separate is set."
AUDIO_INPUT_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg"}

SeparateOption = Annotated[bool, typer.Option("--separate", help=SEPARATE_HELP)]
SeparationModelOption = Annotated[
    str, typer.Option("--separation-model", help=SEPARATION_MODEL_HELP)
]
SeparationDeviceOption = Annotated[
    str, typer.Option("--separation-device", help=SEPARATION_DEVICE_HELP)
]


@app.command()
def extract(
    audio: str = typer.Argument(..., help="Path to WAV/MP3 file"),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output JSON path"),
    valley_method: str = typer.Option("hybrid", "--valley-method",
                                       help="Valley method: rms_percentile/section_ar/hybrid"),
    separate: SeparateOption = False,
    separation_model: SeparationModelOption = DEFAULT_SEPARATION_MODEL,
    separation_device: SeparationDeviceOption = DEFAULT_SEPARATION_DEVICE,
    clap_semantic: bool = typer.Option(
        False,
        "--clap-semantic",
        help=(
            "Read source audio's semantic axes with CLAP at extraction "
            "(requires the semantic-embed extra); attaches isolated "
            "LearnedAudioAnnotations.semantic_axes."
        ),
    ),
) -> None:
    """Extract RPE from audio file."""
    from svp_rpe.rpe.extractor import extract_rpe_from_file

    if clap_semantic:
        # Fail fast on the missing optional dependency BEFORE the (possibly
        # slow) base extraction + Demucs separation, so `--clap-semantic`
        # without the semantic-embed extra doesn't waste that time only to
        # exit 1 with the install hint (probe imports the module; no weight
        # download).
        from svp_rpe.rpe.learned import LearnedModelUnavailable
        from svp_rpe.rpe.learned.clap_adapter import ensure_clap_available

        try:
            ensure_clap_available()
        except LearnedModelUnavailable as exc:
            # markup=False: the install hint contains `.[semantic-embed]`, which
            # Rich would otherwise parse as a markup tag and drop from the shown
            # recovery command — exactly on the missing-dependency path.
            console.print(str(exc), style="yellow", markup=False)
            raise typer.Exit(code=1) from exc

    console.print(f"[bold]Extracting RPE from {audio}...[/bold]")
    bundle = extract_rpe_from_file(
        audio,
        valley_method=valley_method,
        include_stems=separate,
        separation_model=separation_model,
        separation_device=separation_device,
    )
    if clap_semantic:
        from svp_rpe.rpe.learned import LearnedModelUnavailable, attach_learned_annotations
        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_axes

        try:
            annotations = extract_clap_semantic_axes(audio)
        except LearnedModelUnavailable as exc:
            console.print(str(exc), style="yellow", markup=False)
            raise typer.Exit(code=1) from exc
        bundle = attach_learned_annotations(bundle, annotations)
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
def compose(
    score_yaml: str = typer.Argument(..., help="Path to Composition Score YAML"),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output file path"),
    output_format: str = typer.Option(
        "text",
        "--format",
        click_type=click.Choice(["text", "json"]),
        help="Output format: text | json",
    ),
    max_chars: Optional[int] = typer.Option(
        None,
        "--max-chars",
        help="Override rendering.prompt_max_chars",
    ),
) -> None:
    """Render Composition Score into an external generator prompt."""
    from svp_rpe.compose import ExternalPromptAdapter, load_composition_score

    score = load_composition_score(score_yaml)
    prompt = ExternalPromptAdapter().render(score, max_chars=max_chars)
    content = (
        prompt.text
        if output_format == "text"
        else json.dumps(prompt.model_dump(mode="json"), ensure_ascii=False, indent=2)
    )

    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(content, encoding="utf-8")
        console.print(f"[green]Composition prompt saved to {output}[/green]")
    else:
        typer.echo(content)

    # PR3 後半: デバイスプロファイルの advisory（プロンプト本文・tags は変えない・警告のみ）。
    # text 出力は Suno などへそのまま貼り付ける成果物のため、stdout / -o ファイルへは
    # 一切混ぜず stderr にのみ出す。JSON 出力は model_dump に advisories が自然に乗るため
    # 追加処理不要。
    if output_format == "text" and prompt.advisories:
        advisories_block = "\n".join(f"- {advisory}" for advisory in prompt.advisories)
        typer.echo(f"Advisories:\n{advisories_block}", err=True)


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
            annotations = extract_clap_semantic_axes(audio)
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


@app.command()
def evaluate(
    audio: str = typer.Option(..., "--audio", help="Path to audio file"),
    svp: Optional[str] = typer.Option(None, "--svp", help="Path to external SVP file"),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output JSON path"),
    valley_method: str = typer.Option("hybrid", "--valley-method",
                                       help="Valley method: rms_percentile/section_ar/hybrid"),
    baseline: str = typer.Option(
        "pro",
        "--baseline",
        click_type=BASELINE_PROFILE_CHOICE,
        help=BASELINE_PROFILE_HELP,
    ),
    separate: SeparateOption = False,
    separation_model: SeparationModelOption = DEFAULT_SEPARATION_MODEL,
    separation_device: SeparationDeviceOption = DEFAULT_SEPARATION_DEVICE,
) -> None:
    """Evaluate audio. With --svp: compare against external SVP. Without: self-evaluate."""
    from svp_rpe.eval.scorer_integrated import score_integrated
    from svp_rpe.eval.scorer_rpe import score_rpe
    from svp_rpe.eval.scorer_ugher import score_ugher
    from svp_rpe.rpe.extractor import extract_rpe_from_file
    from svp_rpe.svp.generator import generate_svp

    console.print(f"[bold]Evaluating {audio}...[/bold]")
    rpe_bundle = extract_rpe_from_file(
        audio,
        valley_method=valley_method,
        include_stems=separate,
        separation_model=separation_model,
        separation_device=separation_device,
    )
    svp_bundle = generate_svp(rpe_bundle)

    rpe_score = score_rpe(rpe_bundle.physical, baseline=baseline)
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
    """Compare reference audio against candidate audio/SVP.

    Note: stem separation is not supported here because the comparison engine
    does not consume PhysicalRPE.stem_rpe. Use `evaluate --separate` or
    `run --separate` for per-stem analysis.
    """
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


@app.command("ci-check")
def ci_check(
    target_svp: str = typer.Argument(..., help="Path to TargetSVP JSON"),
    observed_rpe: str = typer.Argument(..., help="Path to ObservedRPE fixture JSON"),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output path"),
    output_format: str = typer.Option(
        "json",
        "--format",
        click_type=click.Choice(["json", "markdown"]),
        help="Output format: json | markdown",
    ),
    threshold: float = typer.Option(
        0.0,
        "--threshold",
        click_type=click.FloatRange(0.0, 1.0),
        help="Pass semantic CI when loss is less than or equal to this threshold.",
    ),
) -> None:
    """Run deterministic semantic CI: TargetSVP → ExpectedRPE → Diff → RepairSVP."""
    from svp_rpe.semantic_ci import ObservedRPE, TargetSVP, render_markdown, run_semantic_ci

    target_data = json.loads(Path(target_svp).read_text(encoding="utf-8"))
    observed_data = json.loads(Path(observed_rpe).read_text(encoding="utf-8"))
    result = run_semantic_ci(
        TargetSVP(**target_data),
        ObservedRPE(**observed_data),
        threshold=threshold,
    )
    content = (
        render_markdown(result)
        if output_format == "markdown"
        else json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
    )

    if output:
        Path(output).write_text(content, encoding="utf-8")
        console.print(f"[green]Semantic CI result saved to {output}[/green]")
    else:
        if output_format == "markdown":
            typer.echo(content, nl=False)
        else:
            typer.echo(content)

    if result.semantic_diff.verdict == "repair":
        raise typer.Exit(code=1)


@app.command()
def run(
    audio: str = typer.Argument(..., help="Path to WAV/MP3 file"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", help="Output directory"),
    no_save: bool = typer.Option(False, "--no-save", help="Print to stdout only"),
    valley_method: str = typer.Option("hybrid", "--valley-method",
                                       help="Valley method: rms_percentile/section_ar/hybrid"),
    baseline: str = typer.Option(
        "pro",
        "--baseline",
        click_type=BASELINE_PROFILE_CHOICE,
        help=BASELINE_PROFILE_HELP,
    ),
    separate: SeparateOption = False,
    separation_model: SeparationModelOption = DEFAULT_SEPARATION_MODEL,
    separation_device: SeparationDeviceOption = DEFAULT_SEPARATION_DEVICE,
) -> None:
    """Run full pipeline: extract → generate → evaluate."""
    from svp_rpe.eval.scorer_integrated import score_integrated
    from svp_rpe.eval.scorer_rpe import score_rpe
    from svp_rpe.eval.scorer_ugher import score_ugher
    from svp_rpe.rpe.extractor import extract_rpe_from_file
    from svp_rpe.svp.generator import generate_svp
    from svp_rpe.svp.render_yaml import render_yaml

    console.print(f"[bold]Running full pipeline on {audio}...[/bold]")

    rpe_bundle = extract_rpe_from_file(
        audio,
        valley_method=valley_method,
        include_stems=separate,
        separation_model=separation_model,
        separation_device=separation_device,
    )
    console.print("[green]✓[/green] RPE extraction complete")

    svp_bundle = generate_svp(rpe_bundle)
    console.print("[green]✓[/green] SVP generation complete")

    rpe_score = score_rpe(rpe_bundle.physical, baseline=baseline)
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
    baseline: str = typer.Option(
        "pro",
        "--baseline",
        click_type=BASELINE_PROFILE_CHOICE,
        help=BASELINE_PROFILE_HELP,
    ),
    separate: SeparateOption = False,
    separation_model: SeparationModelOption = DEFAULT_SEPARATION_MODEL,
    separation_device: SeparationDeviceOption = DEFAULT_SEPARATION_DEVICE,
) -> None:
    """Batch process multiple audio files."""
    from svp_rpe.batch.runner import run_batch

    console.print(f"[bold]Batch processing {audio_dir}...[/bold]")
    summary = run_batch(
        audio_dir,
        svp_dir=svp_dir,
        mode=mode,
        output_dir=output_dir,
        baseline=baseline,
        include_stems=separate,
        separation_model=separation_model,
        separation_device=separation_device,
    )

    console.print(f"\n[bold]Results: {summary['successful']}/{summary['total_files']} successful[/bold]")

    if summary.get("ranking"):
        console.print("\n[bold]Ranking:[/bold]")
        for entry in summary["ranking"][:10]:
            console.print(f"  {entry['rank']}. {entry['audio']} — {entry['score']:.4f}")

    if output_dir:
        console.print(f"\n[green]Reports saved to {output_dir}/[/green]")


@app.command("audit")
def audit(
    composition_score: str = typer.Argument(..., help="Path to Composition Score YAML"),
    rpe_or_audio: str = typer.Argument(
        ...,
        help="Path to extracted RPEBundle JSON or generated audio file",
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        click_type=click.Choice(["text", "json"]),
        help="Output format: text | json",
    ),
    valley_method: str = typer.Option(
        "hybrid",
        "--valley-method",
        help="Valley method for audio input: rms_percentile/section_ar/hybrid",
    ),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output file path"),
) -> None:
    """Render a composition control-panel audit from a score and RPE/audio input."""
    from svp_rpe.compose import load_composition_score
    from svp_rpe.rpe.models import RPEBundle
    from svp_rpe.semantic_ci.audit import build_audit_report, render_audit_text

    score = load_composition_score(composition_score)
    input_path = Path(rpe_or_audio)
    # JSON fixtures remain the deterministic DD-A test path. Audio inputs call
    # the existing extractor as a convenience front-end for the one-shot workflow.
    if input_path.suffix.lower() in AUDIO_INPUT_SUFFIXES:
        from svp_rpe.rpe.extractor import extract_rpe_from_file

        bundle = extract_rpe_from_file(str(input_path), valley_method=valley_method)
    else:
        data = json.loads(input_path.read_text(encoding="utf-8"))
        bundle = RPEBundle(**data)
    report = build_audit_report(score, bundle, observed_id=input_path.stem)

    if output_format == "json":
        content = json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    else:
        content = render_audit_text(report)

    if output:
        Path(output).write_text(content, encoding="utf-8")
    else:
        typer.echo(content)


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


@app.command("roundtrip-corpus")
def roundtrip_corpus(
    manifest: str = typer.Argument(..., help="Path to roundtrip corpus manifest YAML"),
    output_format: str = typer.Option(
        "text",
        "--format",
        click_type=click.Choice(["text", "json"]),
        help="Output format: text | json",
    ),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output file path"),
) -> None:
    """Run or replay a roundtrip corpus manifest."""

    from svp_rpe.roundtrip import (
        load_manifest,
        render_corpus_batch_text,
        run_corpus_batch,
    )

    manifest_path = Path(manifest)
    corpus = load_manifest(manifest_path)
    report = run_corpus_batch(corpus, repo_root=_manifest_checkout_root(manifest_path))
    if output_format == "json":
        content = json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    else:
        content = render_corpus_batch_text(report)

    if output:
        Path(output).write_text(content, encoding="utf-8")
    else:
        typer.echo(content)


@app.command("roundtrip-rep")
def roundtrip_rep(
    composition_score: str = typer.Argument(..., help="Path to Composition Score YAML"),
    takes_manifest: str = typer.Argument(..., help="Path to takes manifest JSON"),
    audio_dir: Optional[str] = typer.Option(
        None,
        "--audio-dir",
        help="Directory containing take audio (default: takes manifest's parent directory)",
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        click_type=click.Choice(["text", "json"]),
        help="Output format: text | json",
    ),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output file path"),
) -> None:
    """Run the R3 stochastic performer repetition roundtrip (R3-1/R3-2/R3-3)."""

    from svp_rpe.compose import load_composition_score
    from svp_rpe.roundtrip import (
        load_takes_for_repetition,
        render_repetition_text,
        run_repetition_batch,
    )

    manifest_path = Path(takes_manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    resolved_audio_dir = Path(audio_dir) if audio_dir else manifest_path.parent

    score = load_composition_score(composition_score)
    takes = load_takes_for_repetition(manifest, audio_dir=resolved_audio_dir)
    # score_ref は診断に実際に使った CLI のスコアで固定する。manifest 側の
    # score_path が異なる場合（マシン間移動などで stale）にそちらを転記すると、
    # 別の楽譜名義で保存率が記録され R3 実験ログを汚す。不一致は stderr へ
    # advisory として通知する（レポート本文は不変・#128 の規律）。
    manifest_score_path = manifest.get("score_path")
    if manifest_score_path is not None and str(manifest_score_path) != composition_score:
        typer.echo(
            f"note: takes manifest score_path ({manifest_score_path}) differs from the "
            f"diagnosed score ({composition_score}); score_ref records the diagnosed score",
            err=True,
        )
    report = run_repetition_batch(
        score,
        takes,
        generator=str(manifest.get("generator", "unknown")),
        score_ref=composition_score,
    )
    if output_format == "json":
        content = json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    else:
        content = render_repetition_text(report)

    if output:
        Path(output).write_text(content, encoding="utf-8")
    else:
        typer.echo(content)


@app.command("genre-calibrate")
def genre_calibrate(
    manifest: str = typer.Argument(..., help="Path to genre calibration manifest YAML"),
    output_format: str = typer.Option(
        "text",
        "--format",
        click_type=click.Choice(["text", "json"]),
        help="Output format: text | json",
    ),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output file path"),
) -> None:
    """Analyze a genre calibration corpus manifest."""

    from svp_rpe.calibration import (
        load_genre_manifest,
        render_genre_report_text,
        run_genre_calibration,
    )

    manifest_path = Path(manifest)
    corpus = load_genre_manifest(manifest_path)
    report = run_genre_calibration(corpus, repo_root=_manifest_checkout_root(manifest_path))
    if output_format == "json":
        content = json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    else:
        content = render_genre_report_text(report)

    if output:
        Path(output).write_text(content, encoding="utf-8")
    else:
        typer.echo(content)


@app.command("genre-audit")
def genre_audit(
    manifest: str = typer.Argument(..., help="Path to genre calibration manifest YAML"),
    output_format: str = typer.Option(
        "text",
        "--format",
        click_type=click.Choice(["text", "json"]),
        help="Output format: text | json",
    ),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output file path"),
) -> None:
    """Audit current genre rules against a labeled calibration manifest."""

    from svp_rpe.calibration import (
        load_genre_manifest,
        render_misfire_audit_text,
        run_genre_misfire_audit,
    )

    manifest_path = Path(manifest)
    corpus = load_genre_manifest(manifest_path)
    report = run_genre_misfire_audit(corpus, repo_root=_manifest_checkout_root(manifest_path))
    if output_format == "json":
        content = json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    else:
        content = render_misfire_audit_text(report)

    if output:
        Path(output).write_text(content, encoding="utf-8")
    else:
        typer.echo(content)


def _manifest_checkout_root(manifest_path: str | Path) -> Path:
    """Infer the checkout root for repo-relative locators in a manifest."""

    resolved = Path(manifest_path).resolve()
    start = resolved if resolved.is_dir() else resolved.parent
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd().resolve()


if __name__ == "__main__":
    app()
