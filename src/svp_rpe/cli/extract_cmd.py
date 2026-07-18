"""svprpe extract / generate."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from svp_rpe.cli._app import (
    DEFAULT_SEPARATION_DEVICE,
    DEFAULT_SEPARATION_MODEL,
    SeparateOption,
    SeparationDeviceOption,
    SeparationModelOption,
    app,
    console,
)


@app.command()
def extract(
    audio: str = typer.Argument(..., help="Path to WAV/MP3 file"),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output JSON path"),
    valley_method: str = typer.Option("v2", "--valley-method",
                                       help="Valley method: v2/legacy_hybrid/rms_percentile/section_ar/hybrid"),
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
    clap_sections: bool = typer.Option(
        False,
        "--clap-sections",
        help=(
            "Read CLAP semantic axes per structural section (the emotional "
            "arc; superset of --clap-semantic). Requires the semantic-embed "
            "extra; attaches isolated "
            "LearnedAudioAnnotations.semantic_axis_sections."
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
    lyrics: bool = typer.Option(
        False,
        "--lyrics",
        help=(
            "Read the source audio's lyrics with faster-whisper at "
            "extraction (requires the lyrics extra); attaches isolated "
            "LearnedAudioAnnotations.lyrics_transcription. Isolates vocals "
            "via Demucs first by default — see --lyrics-no-separate."
        ),
    ),
    lyrics_model: str = typer.Option(
        "small",
        "--lyrics-model",
        help="faster-whisper model size used when --lyrics is set (e.g. small, medium).",
    ),
    lyrics_no_separate: bool = typer.Option(
        False,
        "--lyrics-no-separate",
        help=(
            "Transcribe the full mix instead of isolating vocals via Demucs "
            "first (skips the separate extra dependency on this path)."
        ),
    ),
) -> None:
    """Extract RPE from audio file."""
    from svp_rpe.rpe.extractor import extract_rpe_from_file

    if clap_semantic or clap_sections:
        # Fail fast on the missing optional dependency BEFORE the (possibly
        # slow) base extraction + Demucs separation, so `--clap-semantic` /
        # `--clap-sections` without the semantic-embed extra doesn't waste
        # that time only to exit 1 with the install hint (probe imports the
        # module; no weight download).
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

    if lyrics:
        # Same fail-fast rationale as the CLAP probe above, for the
        # `lyrics` extra (faster_whisper import only; no weight download).
        from svp_rpe.rpe.learned import LearnedModelUnavailable
        from svp_rpe.rpe.learned.lyrics_adapter import ensure_lyrics_available

        try:
            ensure_lyrics_available()
        except LearnedModelUnavailable as exc:
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
    if clap_sections:
        from svp_rpe.rpe.learned import LearnedModelUnavailable, attach_learned_annotations
        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_section_axes

        sections = [
            {"section": marker.label, "start_sec": marker.start_sec, "end_sec": marker.end_sec}
            for marker in bundle.physical.structure
        ]
        try:
            annotations = extract_clap_semantic_section_axes(
                audio, sections, checkpoint=clap_checkpoint, amodel=clap_amodel
            )
        except LearnedModelUnavailable as exc:
            console.print(str(exc), style="yellow", markup=False)
            raise typer.Exit(code=1) from exc
        bundle = attach_learned_annotations(bundle, annotations)
    elif clap_semantic:
        from svp_rpe.rpe.learned import LearnedModelUnavailable, attach_learned_annotations
        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_axes

        try:
            annotations = extract_clap_semantic_axes(
                audio, checkpoint=clap_checkpoint, amodel=clap_amodel
            )
        except LearnedModelUnavailable as exc:
            console.print(str(exc), style="yellow", markup=False)
            raise typer.Exit(code=1) from exc
        bundle = attach_learned_annotations(bundle, annotations)
    if lyrics:
        from svp_rpe.io.source_separator import SeparatorNotAvailableError
        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.lyrics_adapter import lyrics_model_info, transcribe_lyrics
        from svp_rpe.rpe.models import LearnedAudioAnnotations

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

        if bundle.learned_annotations is not None:
            # CLAP annotations were computed first (above); extend the same
            # LearnedAudioAnnotations record rather than overwriting it.
            existing = bundle.learned_annotations
            bundle = bundle.model_copy(
                update={
                    "learned_annotations": existing.model_copy(
                        update={
                            "lyrics_transcription": transcription,
                            "enabled_models": [
                                *existing.enabled_models,
                                lyrics_model_info(lyrics_model),
                            ],
                        }
                    )
                }
            )
        else:
            bundle = attach_learned_annotations(
                bundle,
                LearnedAudioAnnotations(
                    enabled_models=[lyrics_model_info(lyrics_model)],
                    lyrics_transcription=transcription,
                ),
            )
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
