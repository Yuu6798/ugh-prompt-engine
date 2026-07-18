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

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Callable, Optional

import click
import typer
from rich.console import Console
from rich.table import Table

from svp_rpe.eval.scorer_rpe import BASELINE_CONFIGS

if TYPE_CHECKING:
    from svp_rpe.arrange.identity import IdentityManifest
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
    from svp_rpe.compose import (
        ExternalPromptAdapter,
        load_composition_score,
        resolve_backend_descriptor,
    )

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

    # #163 Codex P2: omit_body_negative backend（suno / musicgen）では本文に "Avoid:" が
    # 出ないため、text 出力だけを見るユーザーには除外要求が silent に失われて見える。
    # advisory と同じ経路（stdout / -o ファイルは不変・stderr のみ）で negative_tags を
    # 1 行可視化する。JSON 出力は model_dump に negative_tags が乗るため追加処理不要。
    if output_format == "text" and prompt.negative_tags:
        descriptor = resolve_backend_descriptor(score.rendering.target_backend)
        typer.echo(
            f"Negative tags (paste into the generator's {descriptor.negative_channel} field): "
            + "; ".join(prompt.negative_tags),
            err=True,
        )

    # structure チャネル再配線（#169 follow-up）: suno backend では structure 散文の
    # 代わりに section_tags（Lyrics 欄向けセクション・メタタグ台本）が生成される。
    # 本文（Style 欄）とは別チャネルの成果物のため、negative_tags と同じ経路
    # （stdout / -o ファイルは不変・stderr のみ）で可視化する。JSON 出力は
    # model_dump に section_tags が自然に乗るため追加処理不要。
    if output_format == "text" and prompt.section_tags:
        typer.echo(
            "Section tags (paste into the generator's Lyrics field):\n" + prompt.section_tags,
            err=True,
        )


def _publish_artifacts_atomically(
    contents: dict[str, str],
    output_dir: str | Path,
    input_paths: list[str | Path],
) -> Path:
    """Publish a complete artifact set with rollback on an interrupted rename."""
    import os
    import tempfile

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved_inputs = {Path(path).resolve() for path in input_paths}
    for filename in contents:
        target = out_dir / filename
        if target.resolve() in resolved_inputs:
            raise ValueError(f"input path collides with output artifact path: {target}")
        if target.is_dir():
            raise ValueError(f"output path is an existing directory: {target}")

    with tempfile.TemporaryDirectory(dir=out_dir) as staging:
        staging_dir = Path(staging)
        for filename, content in contents.items():
            (staging_dir / filename).write_bytes(content.encode("utf-8"))

        snapshots: dict[str, Path] = {}
        published: list[str] = []
        try:
            for filename in contents:
                target = out_dir / filename
                if target.exists():
                    previous = staging_dir / f"{filename}.prev"
                    os.replace(target, previous)
                    snapshots[filename] = previous
            for filename in contents:
                os.replace(staging_dir / filename, out_dir / filename)
                published.append(filename)
        except OSError:
            for filename in published:
                try:
                    os.unlink(out_dir / filename)
                except OSError:
                    pass
            for filename, previous in snapshots.items():
                try:
                    os.replace(previous, out_dir / filename)
                except OSError:
                    pass
            raise
    return out_dir


BUILDS_LATEST_SCHEMA_VERSION = "builds-latest/0.1"
# `os.path.relpath` depends only on path *depth*, never on the literal name
# of the final component, so any 64-hex-char stand-in produces the exact
# same relative locator as the real `content_digest` would (every real
# digest directory sits at the same depth under `<builds_root>/builds/`).
# This lets `package` compute `artifact_base.locator` — itself part of the
# package content that determines `content_digest` — *before* the digest is
# known, without the two ever needing to agree on a real value.
_BUILDS_LOCATOR_PLACEHOLDER_DIGEST = "0" * 64


def _builds_placeholder_package_dir(builds_root: str | Path) -> Path:
    """Same-depth stand-in for `<builds_root>/builds/<content_digest>/`."""
    return Path(builds_root) / "builds" / _BUILDS_LOCATOR_PLACEHOLDER_DIGEST


def _update_builds_latest_pointer(latest_path: Path, content_digest: str, *, root: Path) -> None:
    """Atomically (over)write `<root>/latest.json` to point at `content_digest`.

    `latest.json` is the one file this scheme ever overwrites — everything
    under `<root>/builds/<digest>/` is immutable once published (Design
    Memo §4).
    """
    import os
    import tempfile

    payload = json.dumps(
        {"schema_version": BUILDS_LATEST_SCHEMA_VERSION, "content_digest": content_digest},
        ensure_ascii=False,
        indent=2,
    )
    root.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=root, prefix="latest.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, latest_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _reject_builds_root_input_collision(
    input_paths: list[str | Path],
    builds_root: str | Path,
    *,
    reject_placeholder_subtree: bool = False,
) -> None:
    """Guard against an input path aliasing `<builds_root>/latest.json`, or
    (package only) sitting inside the reserved locator-placeholder subtree.

    `latest.json` is the **only** file the builds-root scheme ever
    overwrites; if an input (score / spec / manifest / capability profile /
    etc.) resolves to that same path, publishing would silently clobber the
    input on the `latest.json` update — the same shape of hole
    `_publish_artifacts_atomically` already guards against for `--output-dir`
    (#176 P2 R3). No equivalent check is needed against a real
    `<builds_root>/builds/<content_digest>/` directory: unpublished, it
    doesn't exist yet; once published, it is never written to again — so it
    is structurally safe either way.

    `reject_placeholder_subtree=True` (package only; PR #184 review round 5,
    Thread G) additionally rejects any input that resolves inside
    `<builds_root>/builds/<64 zeros>/` — the reserved, same-depth stand-in
    `_builds_placeholder_package_dir` uses to compute `artifact_base.locator`
    *before* the real `content_digest` is known (see that function's
    docstring). If an input — most concretely, an identity manifest anchor
    artifact — actually lives inside that reserved subtree, the locator
    computed against the placeholder does not describe where the artifact
    ends up relative to the real digest directory once published, silently
    breaking `artifact_base`. A *real* digest directory (any other 64-hex
    name) is not reserved and is not rejected: an input placed there resolves
    correctly regardless, because `os.path.relpath` only depends on depth and
    every digest directory sits at the same depth under `builds/` (the same
    property that makes the placeholder trick valid in the first place).
    """
    latest_path = Path(builds_root) / "latest.json"
    resolved_latest = latest_path.resolve()
    placeholder_dir = (
        _builds_placeholder_package_dir(builds_root).resolve()
        if reject_placeholder_subtree
        else None
    )
    for input_path in input_paths:
        resolved_input = Path(input_path).resolve()
        if resolved_input == resolved_latest:
            raise ValueError(
                f"input path collides with output artifact path: {latest_path}"
            )
        if placeholder_dir is not None and resolved_input.is_relative_to(placeholder_dir):
            raise ValueError(
                f"input path collides with output artifact path: {resolved_input} "
                f"lies inside the builds-root locator-placeholder subtree "
                f"{placeholder_dir} reserved for artifact_base.locator computation"
            )


def _reject_if_not_regular_file(path: Path, *, label: str) -> None:
    """Reject a symlink, or any existing non-regular-file entity, before it
    is read (PR #184 review round 7, Thread I).

    A blessed, immutable publication must not depend on a mutable entity
    outside `builds_root` — the same principle as the round 3/6 digest- and
    `builds/`-directory symlink rejections, applied one level down to every
    file `_check_existing_builds_root_publication` reads (the descriptor
    itself and each declared content artifact). A symlink is rejected
    regardless of what it resolves to (even a symlink to an otherwise
    byte-identical file) — resolving it at all would mean trusting whoever
    controls that link.
    """
    if path.is_symlink():
        raise ValueError(f"builds-root {label} is a symlink: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"builds-root {label} is not a regular file: {path}")


def _check_existing_builds_root_publication(
    target_dir: Path,
    descriptive_filename: str,
    pending_content: str,
    *,
    content_digest: str,
    expected_schema_version: str,
    extract_digest_inputs: Callable[[dict[str, Any]], dict[str, str]],
    allowed_provenance_fields: frozenset[str],
    validate_descriptor: Callable[[dict[str, Any]], None],
) -> bool:
    """Read-and-verify check for the immutable no-op publish path, run before
    `latest.json` is ever moved onto an existing digest directory.

    Reads the existing digest directory's one *descriptive* file
    (`arrangement_bundle.json` for `arrange`, `compilation_report.json` for
    `package` — the file that describes the whole publication, as opposed to
    plain content outputs) exactly once — after `_reject_if_not_regular_file`
    confirms it is neither a symlink nor some other non-regular entity. If
    its bytes match what this invocation would have published for that same
    filename, no parsing, no digest recomputation, no schema check is needed
    for the *descriptor itself* — an identical-bytes descriptor was
    necessarily produced by *this* schema version by construction. (PR #184
    review round 5, Thread F: that fast path alone is not enough to bless the
    directory — see below.)

    If the descriptor's bytes differ, this is *not* immediately treated as
    mere provenance/metadata drift (round 2, Thread B) — a directory merely
    occupying this digest path (corrupted, hand-edited, or from an unrelated
    build) must not be blessed by a `latest.json` update just because a byte
    difference was, in isolation, plausible-looking provenance drift. Before
    accepting it as provenance drift, several things are verified, each
    raising `ValueError` (and leaving `latest.json` untouched) on failure:

    1. the descriptive file parses as JSON;
    2. its `schema_version` field equals `expected_schema_version` (AGENTS.md
       §8 Persistent Artifact Safety Gate: unknown `schema_version` values
       are rejected, not read as if they were the current schema — a
       descriptor written by an older/newer schema must not be silently
       treated as an ordinary provenance difference in the *current* one).
       This explicit check runs *before* step 3 so an unknown-schema
       descriptor gets this specific, targeted error rather than whatever
       incidental validation error the current schema's model happens to
       raise against it;
    3. (round 9) the whole descriptor validates against `validate_descriptor`
       — `ArrangementBundleDescriptor.model_validate` for `arrange`,
       `CompilationReport.model_validate` for `package` (both translate
       `pydantic.ValidationError` to `ValueError` internally). This is a full
       schema validation, not just the top-level fields step 6 below
       compares: every nested shape (e.g. `outputs`' `{path, sha256}` entries,
       `changes`' leaf-change records) is checked, `extra="forbid"` rejects
       unknown keys, and it subsumes step 2's schema_version check in the
       sense that an unexpected schema_version would fail model validation
       too — step 2 is kept anyway for the more specific error message;
    4. its own `content_digest` field equals the `content_digest` this
       invocation computed (the digest is the directory's name, so this
       catches a descriptor that doesn't even claim to describe *this*
       digest);
    5. recomputing `content_digest` from the descriptor's own declared
       output hashes (via `extract_digest_inputs`, which knows the
       arrange-vs-package shape of "artifact_name -> sha256") reproduces the
       same digest — this catches a descriptor whose declared hashes were
       tampered with (or corrupted) to match step 4's digest field without
       actually being self-consistent;
    6. (round 7, Thread J, checked after every content artifact below is
       also confirmed present and hash-matching) every top-level field of
       the existing descriptor that is *not* in `allowed_provenance_fields`
       is byte-for-byte equal (as parsed JSON) to the same field in the
       pending descriptor — e.g. for `arrange`'s bundle, only
       `source_score`/`arrangement_spec` may differ; `arrangement_id` and
       `changes` are *not* part of `content_digest`, so without this check a
       tampered `changes` list could otherwise be waved through as "mere
       provenance drift" merely because `content_digest` still matched.

    Either way — byte-identical fast path or a byte-difference that survives
    checks 1-5 — one more thing is verified **before** `latest.json` may be
    moved onto this directory: every content artifact the descriptor
    declares (`extract_digest_inputs`' `{artifact_name: sha256}`, e.g.
    `derived_score.yaml` + `arrangement_diff.json` for `arrange`) is neither
    a symlink nor a non-regular file (`_reject_if_not_regular_file` again),
    actually exists in `target_dir`, and hashes to its declared value. A
    byte-identical (or otherwise self-consistent) *descriptor* copied into a
    directory that is missing, has tampered, or has symlinked-out content
    files would otherwise slip through undetected — the descriptor alone
    doesn't prove the directory it sits in is complete. On the fast path,
    the declared output map is read from `pending_content` (already known to
    be byte-identical to the on-disk descriptor, so parsing either is
    equivalent) rather than re-reading the descriptor from disk a second
    time — and, being byte-identical to a descriptor this invocation itself
    just built via its own model, it needs no separate schema validation.

    Blessing a digest directory with a `latest.json` update therefore always
    means: descriptor self-consistency (schema + digest + provenance-only
    diff, checks 1-6), every declared output artifact present with a
    matching hash, and nothing read along the way was a symlink. This is
    still not a full audit — it never rehashes *undeclared* files or
    recurses into anything beyond what the descriptor itself lists, and
    nothing in the directory is ever rewritten or repaired; an independent,
    fully recursive audit is left to a future `verify`-style command.
    """
    descriptive_path = target_dir / descriptive_filename
    _reject_if_not_regular_file(
        descriptive_path,
        label=f"digest directory {target_dir} descriptive file {descriptive_filename!r}",
    )
    try:
        existing_bytes = descriptive_path.read_bytes()
    except OSError as exc:
        raise ValueError(
            f"builds-root digest directory {target_dir} is missing or unreadable "
            f"descriptive file {descriptive_filename!r}: {exc}"
        ) from exc

    pending_bytes = pending_content.encode("utf-8")
    provenance_differs = existing_bytes != pending_bytes

    if not provenance_differs:
        # Fast path: the descriptor on disk is byte-identical to the one
        # this invocation would publish, so parsing the pending copy (which
        # this process already holds in memory) is equivalent to re-reading
        # and re-parsing the one on disk.
        digest_inputs = extract_digest_inputs(json.loads(pending_bytes))
    else:
        try:
            existing_descriptor = json.loads(existing_bytes)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"builds-root digest directory {target_dir} has an unparsable "
                f"descriptive file {descriptive_filename!r}: {exc}"
            ) from exc
        if not isinstance(existing_descriptor, dict):
            raise ValueError(
                f"builds-root digest directory {target_dir} descriptive file "
                f"{descriptive_filename!r} must be a JSON object"
            )

        recorded_schema_version = existing_descriptor.get("schema_version")
        if recorded_schema_version != expected_schema_version:
            raise ValueError(
                f"builds-root digest directory {target_dir} descriptive file "
                f"{descriptive_filename!r} declares schema_version "
                f"{recorded_schema_version!r}, expected {expected_schema_version!r}"
            )

        # `validate_descriptor` (arrange: `ArrangementBundleDescriptor.model_validate`,
        # package: `CompilationReport.model_validate`) is expected to translate
        # `pydantic.ValidationError` into `ValueError` itself — see
        # `_arrange_bundle_validate_descriptor` / `_package_report_validate_descriptor`
        # — so it can simply be called here and let `ValueError` propagate like
        # every other check in this function.
        validate_descriptor(existing_descriptor)

        recorded_digest = existing_descriptor.get("content_digest")
        if recorded_digest != content_digest:
            raise ValueError(
                f"builds-root digest directory {target_dir} descriptive file "
                f"{descriptive_filename!r} declares content_digest "
                f"{recorded_digest!r}, expected {content_digest!r}"
            )

        try:
            digest_inputs = extract_digest_inputs(existing_descriptor)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(
                f"builds-root digest directory {target_dir} descriptive file "
                f"{descriptive_filename!r} is missing field(s) needed to recompute "
                f"content_digest: {exc}"
            ) from exc

        from svp_rpe.arrange.bundle import compute_content_digest

        recomputed_digest = compute_content_digest(digest_inputs)
        if recomputed_digest != content_digest:
            raise ValueError(
                f"builds-root digest directory {target_dir} descriptive file "
                f"{descriptive_filename!r} recomputes to content_digest "
                f"{recomputed_digest!r} from its own declared output hashes, "
                f"expected {content_digest!r} — its declared hashes are not "
                "self-consistent"
            )

    for artifact_name, expected_sha256 in digest_inputs.items():
        artifact_path = target_dir / artifact_name
        _reject_if_not_regular_file(
            artifact_path,
            label=(
                f"digest directory {target_dir} content artifact {artifact_name!r} "
                f"(declared by descriptive file {descriptive_filename!r})"
            ),
        )
        try:
            artifact_bytes = artifact_path.read_bytes()
        except OSError as exc:
            raise ValueError(
                f"builds-root digest directory {target_dir} is missing or "
                f"unreadable content artifact {artifact_name!r} declared by "
                f"descriptive file {descriptive_filename!r}: {exc}"
            ) from exc
        actual_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError(
                f"builds-root digest directory {target_dir} content artifact "
                f"{artifact_name!r} sha256 mismatch: expected {expected_sha256!r}, "
                f"got {actual_sha256!r} (declared by descriptive file "
                f"{descriptive_filename!r})"
            )

    if provenance_differs:
        pending_descriptor = json.loads(pending_bytes)
        for key in sorted(set(existing_descriptor) | set(pending_descriptor)):
            if key in allowed_provenance_fields:
                continue
            if existing_descriptor.get(key) != pending_descriptor.get(key):
                raise ValueError(
                    f"builds-root digest directory {target_dir} descriptive file "
                    f"{descriptive_filename!r} differs from this invocation in "
                    f"non-provenance field {key!r} (existing="
                    f"{existing_descriptor.get(key)!r}, pending="
                    f"{pending_descriptor.get(key)!r}); only "
                    f"{sorted(allowed_provenance_fields)!r} may differ between "
                    "publications sharing a content_digest"
                )

    return provenance_differs


def _reject_invalid_latest_pointer_target(latest_path: Path) -> None:
    """Preflight: `<root>/latest.json` must be either absent or a plain,
    non-symlink file — checked *before* anything under `builds/` is touched.

    `latest.json` is the one path this scheme ever writes to via
    `os.replace`; a directory, a symlink (dangling or not), or any other
    non-regular entity sitting at that path must not be silently accepted
    (`os.replace` onto a directory raises anyway, but only *after* a fresh
    digest directory may already have been published — this preflight fails
    first, before `builds/` is created or written to at all, so a bad
    `latest.json` never leaves a stray digest directory behind it).
    """
    if latest_path.is_symlink() or (latest_path.exists() and not latest_path.is_file()):
        raise ValueError(
            f"builds-root latest pointer target is not a plain file: {latest_path}"
        )


def _publish_artifacts_to_builds_root(
    contents: dict[str, str],
    builds_root: str | Path,
    content_digest: str,
    *,
    descriptive_filename: str,
    expected_schema_version: str,
    extract_digest_inputs: Callable[[dict[str, Any]], dict[str, str]],
    allowed_provenance_fields: frozenset[str],
    validate_descriptor: Callable[[dict[str, Any]], None],
) -> tuple[Path, bool, bool]:
    """Publish a complete artifact set under `<builds_root>/builds/<content_digest>/`.

    Immutability contract (Design Memo §4, revised by PR #184 review): if the
    digest directory already exists, its artifacts are never rewritten,
    appended to, or fully re-verified — but a single read of its one
    *descriptive* file (`descriptive_filename`) is compared against what this
    invocation would have published, so a directory that merely happens to
    exist at this digest path isn't blindly trusted (see
    `_check_existing_builds_root_publication`, which also recomputes
    `content_digest` from the descriptor's own declared hashes via
    `extract_digest_inputs` before accepting a byte difference as mere
    provenance drift). `latest.json` is updated only when that check passes.
    Returns `(published_dir, already_existed, provenance_differs)`.

    Before anything is written, `_reject_invalid_latest_pointer_target`
    rejects an invalid `latest.json` target (see that function) so a
    misplaced `latest.json` can never leave a stray digest directory behind.

    `<builds_root>/builds` itself is rejected the same way if it is a
    symlink (any target, including a real directory): every digest directory
    this scheme publishes lives under `builds/`, so a symlinked `builds/`
    would place publications at a location outside `builds_root` that could
    be repointed out from under the immutability contract by whoever
    controls the symlink — the same hole a symlinked digest target (below)
    would open one level down. `builds_root` itself (`root`) is deliberately
    *not* checked this way: it is a user-supplied path the caller fully
    controls (like `--output-dir`), not part of the scheme's own reserved
    layout, so a symlinked `builds_root` is accepted.

    An existing digest path that is not a real, non-symlink directory (a
    regular file, a dangling symlink, *or a symlink to a directory*) is
    likewise rejected outright rather than silently treated as "already
    published": a symlink digest target — even one pointing at a real
    directory — could be repointed out from under the immutability contract
    by whoever controls the symlink, and (like the non-directory cases)
    still moving `latest.json` onto it would advertise a target this scheme
    doesn't actually control as valid.

    Self-healing on a `latest.json` update failure: if writing the artifact
    set itself succeeds but the trailing `_update_builds_latest_pointer` call
    fails (e.g. a permission or disk error on the rename), the freshly
    published digest directory is **not** rolled back or deleted — it is a
    complete, valid publication, and removing it would violate the
    immutability contract for no benefit (the failure is in the mutable
    `latest.json` pointer, not the immutable content). The run still fails
    (the caller sees the wrapped error), but a subsequent identical
    invocation takes the already-published no-op path and retries the
    `latest.json` update on its own — no separate repair step exists or is
    needed.

    First publish: the full artifact set is written into a
    `tempfile.mkdtemp` staging directory under `<builds_root>/builds/`, then
    the whole staging directory is moved onto the target with a single
    `os.rename` (an atomic directory-level publish, unlike the
    snapshot+per-file `os.replace` dance `_publish_artifacts_atomically`
    needs for the mutable `--output-dir` case — a fresh digest directory has
    no previous content to roll back to). A `rename` failure that turns out
    to be a concurrent publish (the target now exists *as a real directory*)
    is treated as the immutable no-op case; any other failure propagates
    after best-effort staging cleanup.
    """
    import os
    import shutil
    import tempfile

    root = Path(builds_root)
    builds_dir = root / "builds"
    target_dir = builds_dir / content_digest
    latest_path = root / "latest.json"

    if builds_dir.is_symlink():
        raise ValueError(f"builds-root builds directory is a symlink: {builds_dir}")

    _reject_invalid_latest_pointer_target(latest_path)

    def _reject_if_not_directory() -> None:
        if target_dir.is_symlink() or (target_dir.exists() and not target_dir.is_dir()):
            raise ValueError(
                f"builds-root digest target is not a directory: {target_dir}"
            )

    _reject_if_not_directory()
    already_existed = target_dir.is_dir()
    if not already_existed:
        builds_dir.mkdir(parents=True, exist_ok=True)
        staging = tempfile.mkdtemp(dir=builds_dir)
        try:
            staging_path = Path(staging)
            for filename, content in contents.items():
                (staging_path / filename).write_bytes(content.encode("utf-8"))
            try:
                os.rename(staging, target_dir)
            except OSError:
                _reject_if_not_directory()
                if target_dir.is_dir():
                    # Lost a concurrent-publish race: the directory some other
                    # process just published wins (immutability contract).
                    already_existed = True
                else:
                    raise
        finally:
            if Path(staging).exists():
                shutil.rmtree(staging, ignore_errors=True)

    provenance_differs = False
    if already_existed:
        provenance_differs = _check_existing_builds_root_publication(
            target_dir,
            descriptive_filename,
            contents[descriptive_filename],
            content_digest=content_digest,
            expected_schema_version=expected_schema_version,
            extract_digest_inputs=extract_digest_inputs,
            allowed_provenance_fields=allowed_provenance_fields,
            validate_descriptor=validate_descriptor,
        )

    try:
        _update_builds_latest_pointer(latest_path, content_digest, root=root)
    except Exception as exc:
        raise ValueError(
            f"failed to update {latest_path} to point at content_digest "
            f"{content_digest!r}: {exc}. The digest directory {target_dir} was "
            "already published successfully and remains valid (not rolled back); "
            "re-running this command with the same inputs will retry the "
            "latest.json update via the already-published no-op path."
        ) from exc
    return target_dir, already_existed, provenance_differs


def _print_builds_root_publish_note(
    content_digest: str, *, already_existed: bool, provenance_differs: bool
) -> None:
    if not already_existed:
        return
    if provenance_differs:
        typer.echo(
            f"note: builds/{content_digest} already published; existing publication "
            "retained; its provenance differs from this invocation (latest.json updated)",
            err=True,
        )
    else:
        typer.echo(
            f"note: builds/{content_digest} already published; left untouched "
            "(latest.json updated)",
            err=True,
        )


def _arrange_bundle_digest_inputs(descriptor: dict[str, Any]) -> dict[str, str]:
    """`{artifact_name: sha256}` from a parsed `arrangement_bundle.json`.

    Mirrors `compile_arrangement`'s own `compute_content_digest` call: the
    digest is computed over `outputs`' `{path, sha256}` entries (keyed by
    filename), never over `source_score`/`arrangement_spec` (D-1: bundle
    provenance is descriptive, not digest-bearing).
    """
    outputs = descriptor["outputs"]
    return {entry["path"]: entry["sha256"] for entry in outputs.values()}


def _package_report_digest_inputs(descriptor: dict[str, Any]) -> dict[str, str]:
    """`{artifact_name: sha256}` from a parsed `compilation_report.json`.

    Mirrors `build_performance_package`'s own `compute_content_digest` call:
    the digest is computed over `{performance_package.json: package_sha256}`
    only, never over `invocation_provenance` (D-1).
    """
    from svp_rpe.arrange.package import PERFORMANCE_PACKAGE_FILENAME

    return {PERFORMANCE_PACKAGE_FILENAME: descriptor["package_sha256"]}


def _arrange_bundle_validate_descriptor(descriptor: dict[str, Any]) -> None:
    """Full schema validation of a parsed `arrangement_bundle.json` (round 9).

    `ArrangementBundleDescriptor` (`arrange/bundle.py`) is a **reader-only**
    model — `compile_arrangement` never uses it and keeps building the bundle
    as a plain dict, so publish-side bytes are unaffected. It exists purely
    so this blessing-path validation can check the whole descriptor's shape
    (nested `{path, sha256}` entries, `changes` records, unknown top-level
    keys via `extra="forbid"`) instead of just the individual fields
    `_check_existing_builds_root_publication` otherwise inspects one at a
    time.
    """
    from pydantic import ValidationError

    from svp_rpe.arrange.bundle import ArrangementBundleDescriptor

    try:
        ArrangementBundleDescriptor.model_validate(descriptor)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def _package_report_validate_descriptor(descriptor: dict[str, Any]) -> None:
    """Full schema validation of a parsed `compilation_report.json` (round 9).

    Reuses `CompilationReport` itself (`arrange/package.py`) — the same model
    `build_performance_package` constructs on the publish side — so there is
    no separate reader-only model to keep in sync for `package`.
    """
    from pydantic import ValidationError

    from svp_rpe.arrange.package import CompilationReport

    try:
        CompilationReport.model_validate(descriptor)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


# PR #184 review round 7, Thread J: top-level descriptor fields allowed to
# differ between two publications that share a `content_digest` without
# that being treated as anything worse than "provenance drift". Every other
# top-level field — `schema_version`/`content_digest`/`content_digest_basis`
# are already pinned exactly by earlier checks; `arrangement_id` / `changes`
# (arrange) and `work_id` / `generator` / `generator_variant` / `package_sha256`
# (package) are not part of `content_digest` at all, so without this whitelist a tampered
# `changes` list (for example) could otherwise be waved through as mere
# provenance. Paired with `_arrange_bundle_digest_inputs` /
# `_package_report_digest_inputs` the same way those pair with
# `descriptive_filename` — one arrange-shaped constant, one package-shaped
# constant.
#
# Round 8 partial adoption: `warnings` was removed from the package
# whitelist (kept in round 7). Every warning `build_performance_package`
# ever emits is a pure function of facts already baked into
# `performance_package.json` bytes — channel support / anchor delivery
# status — so an identical `package_sha256` (already pinned by
# `content_digest`, since content_digest = sha256(package_sha256) for
# package) implies identical warnings; device-profile advisories are a
# separate stderr-only channel that never lands in `warnings` (#128), so
# they can't explain a difference here either. A `warnings` difference at
# matching `package_sha256` therefore isn't legitimate invocation drift —
# it's a tamper signal, so it stays outside the whitelist.
#
# `inputs` is deliberately *kept* in the whitelist (the other half of the
# same round-8 review comment was rejected): `inputs` records input-file
# byte hashes, which is invocation provenance in the same sense
# `invocation_provenance.compiler.git_commit` already is — first-publish-wins
# already accepts that the *first* successful invocation's environment is
# what gets recorded permanently. Requiring `inputs` to match exactly would
# reject legitimate re-runs where a parse-equivalent but differently
# formatted input (whitespace, key order) produces the same package but a
# different input-file hash — a false positive, not a caught tamper.
_ARRANGE_BUNDLE_ALLOWED_PROVENANCE_FIELDS = frozenset({"source_score", "arrangement_spec"})
_PACKAGE_REPORT_ALLOWED_PROVENANCE_FIELDS = frozenset({"inputs", "invocation_provenance", "mode"})


@app.command()
def arrange(
    score_yaml: str = typer.Argument(..., help="Path to base Composition Score YAML"),
    arrangement_yaml: str = typer.Argument(..., help="Path to ArrangementSpec YAML"),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", help="Output directory (mutually exclusive with --builds-root)"
    ),
    builds_root: Optional[str] = typer.Option(
        None,
        "--builds-root",
        help=(
            "Publish under <root>/builds/<content_digest>/ (immutable once "
            "published; <root>/latest.json points at the most recent digest). "
            "Mutually exclusive with --output-dir."
        ),
    ),
) -> None:
    """Resolve an ArrangementSpec against a base Composition Score into provenance artifacts."""
    import yaml
    from pydantic import ValidationError

    from svp_rpe.arrange import (
        ArrangementError,
        BUNDLE_FILENAME,
        DERIVED_SCORE_FILENAME,
        DIFF_FILENAME,
        compile_arrangement,
    )
    from svp_rpe.arrange.bundle import BUNDLE_SCHEMA_VERSION

    if (output_dir is None) == (builds_root is None):
        raise typer.BadParameter(
            "exactly one of --output-dir or --builds-root is required",
            param_hint="--output-dir / --builds-root",
        )

    try:
        compiled = compile_arrangement(score_yaml, arrangement_yaml)
    except (
        # OSError は FileNotFoundError に加え IsADirectoryError / PermissionError 等の
        # 入力読み取り失敗を包含する（P2 第 4 ラウンド: 生 traceback でなく exit 1）。
        OSError,
        yaml.YAMLError,
        ValueError,
        ValidationError,
        ArrangementError,
    ) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    contents = {
        DERIVED_SCORE_FILENAME: compiled.derived_score_yaml,
        BUNDLE_FILENAME: json.dumps(compiled.bundle, ensure_ascii=False, indent=2),
        DIFF_FILENAME: json.dumps(compiled.diff, ensure_ascii=False, indent=2),
    }

    if builds_root is not None:
        content_digest = compiled.bundle["content_digest"]
        try:
            _reject_builds_root_input_collision([score_yaml, arrangement_yaml], builds_root)
            published_dir, already_existed, provenance_differs = _publish_artifacts_to_builds_root(
                contents,
                builds_root,
                content_digest,
                descriptive_filename=BUNDLE_FILENAME,
                expected_schema_version=BUNDLE_SCHEMA_VERSION,
                extract_digest_inputs=_arrange_bundle_digest_inputs,
                allowed_provenance_fields=_ARRANGE_BUNDLE_ALLOWED_PROVENANCE_FIELDS,
                validate_descriptor=_arrange_bundle_validate_descriptor,
            )
        except (OSError, ValueError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        _print_builds_root_publish_note(
            content_digest, already_existed=already_existed, provenance_differs=provenance_differs
        )
        console.print(
            f"[green]Derived score saved to {published_dir / DERIVED_SCORE_FILENAME}[/green]"
        )
        console.print(
            f"[green]Arrangement bundle saved to {published_dir / BUNDLE_FILENAME}[/green]"
        )
        console.print(
            f"[green]Arrangement diff saved to {published_dir / DIFF_FILENAME}[/green]"
        )
        return

    assert output_dir is not None  # mutual-exclusion check above guarantees this
    try:
        out_dir = _publish_artifacts_atomically(
            contents, output_dir, [score_yaml, arrangement_yaml]
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Derived score saved to {out_dir / DERIVED_SCORE_FILENAME}[/green]")
    console.print(f"[green]Arrangement bundle saved to {out_dir / BUNDLE_FILENAME}[/green]")
    console.print(f"[green]Arrangement diff saved to {out_dir / DIFF_FILENAME}[/green]")


@app.command("package")
def package_command(
    score_yaml: str = typer.Argument(..., help="Path to base Composition Score YAML"),
    identity_yaml: str = typer.Argument(..., help="Path to IdentityManifest YAML"),
    arrangement_yaml: str = typer.Argument(..., help="Path to ArrangementSpec YAML"),
    capability_profile: str = typer.Option(
        ...,
        "--capability-profile",
        help="Path to InputCapabilityProfile YAML",
    ),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", help="Output directory (mutually exclusive with --builds-root)"
    ),
    builds_root: Optional[str] = typer.Option(
        None,
        "--builds-root",
        help=(
            "Publish under <root>/builds/<content_digest>/ (immutable once "
            "published; <root>/latest.json points at the most recent digest). "
            "Mutually exclusive with --output-dir."
        ),
    ),
    strict_capabilities: bool = typer.Option(
        False,
        "--strict-capabilities",
        help="Fail when a hard anchor is unsupported or unknown",
    ),
) -> None:
    """Compile a generator handoff package and capability report."""
    import yaml
    from pydantic import ValidationError

    from svp_rpe.arrange import (
        ArrangementError,
        COMPILATION_REPORT_FILENAME,
        PERFORMANCE_PACKAGE_FILENAME,
        compile_performance_package,
    )
    from svp_rpe.arrange.package import COMPILATION_REPORT_SCHEMA_VERSION

    if (output_dir is None) == (builds_root is None):
        raise typer.BadParameter(
            "exactly one of --output-dir or --builds-root is required",
            param_hint="--output-dir / --builds-root",
        )

    # `artifact_base.locator` (embedded in the package, part of what
    # determines content_digest) is relative to the eventual package
    # directory. In builds mode that directory's name is the content_digest
    # itself, which is not known yet — a same-depth placeholder yields an
    # identical relative path (see `_builds_placeholder_package_dir`).
    if builds_root is not None:
        placeholder_dir = _builds_placeholder_package_dir(builds_root)
        # PR #184 review round 7, Thread K: a *symlinked* placeholder would
        # make `package_dir.resolve()` follow the symlink to wherever it
        # actually points, so the locator gets computed relative to that
        # (arbitrary, mutable) target instead of the reserved same-depth
        # placeholder path — the locator would then be wrong once the real
        # digest directory is published. A real directory sitting at the
        # placeholder path is fine (same depth, so the locator is still
        # correct); only a symlink is rejected here.
        if placeholder_dir.is_symlink():
            typer.echo(
                f"Error: builds-root locator placeholder is a symlink: {placeholder_dir}",
                err=True,
            )
            raise typer.Exit(code=1)
        locator_dir = str(placeholder_dir)
    else:
        locator_dir = output_dir

    try:
        compiled = compile_performance_package(
            score_yaml,
            identity_yaml,
            arrangement_yaml,
            capability_profile,
            locator_dir,
            strict=strict_capabilities,
        )
    except (
        OSError,
        yaml.YAMLError,
        ValueError,
        ValidationError,
        ArrangementError,
    ) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    contents = {
        PERFORMANCE_PACKAGE_FILENAME: compiled.package_json,
        COMPILATION_REPORT_FILENAME: compiled.report_json,
    }

    if builds_root is not None:
        content_digest = compiled.report.content_digest
        try:
            _reject_builds_root_input_collision(
                list(compiled.protected_input_paths),
                builds_root,
                reject_placeholder_subtree=True,
            )
            published_dir, already_existed, provenance_differs = _publish_artifacts_to_builds_root(
                contents,
                builds_root,
                content_digest,
                descriptive_filename=COMPILATION_REPORT_FILENAME,
                expected_schema_version=COMPILATION_REPORT_SCHEMA_VERSION,
                extract_digest_inputs=_package_report_digest_inputs,
                allowed_provenance_fields=_PACKAGE_REPORT_ALLOWED_PROVENANCE_FIELDS,
                validate_descriptor=_package_report_validate_descriptor,
            )
        except (OSError, ValueError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        _print_builds_root_publish_note(
            content_digest, already_existed=already_existed, provenance_differs=provenance_differs
        )
        console.print(
            "[green]Performance package saved to "
            f"{published_dir / PERFORMANCE_PACKAGE_FILENAME}[/green]"
        )
        console.print(
            "[green]Compilation report saved to "
            f"{published_dir / COMPILATION_REPORT_FILENAME}[/green]"
        )
        return

    assert output_dir is not None  # mutual-exclusion check above guarantees this
    try:
        out_dir = _publish_artifacts_atomically(
            contents,
            output_dir,
            list(compiled.protected_input_paths),
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    console.print(
        f"[green]Performance package saved to "
        f"{out_dir / PERFORMANCE_PACKAGE_FILENAME}[/green]"
    )
    console.print(
        f"[green]Compilation report saved to "
        f"{out_dir / COMPILATION_REPORT_FILENAME}[/green]"
    )


def _observe_protected_input_paths(
    package_path: Path,
    manifest_path: Path,
    audio_path: Path,
    manifest: "IdentityManifest",
) -> list[Path]:
    """Every path `observe` reads as an input — package / manifest / audio /
    manifest source locator / every anchor artifact — resolved so `-o` can be
    checked against all of them before anything is measured or written
    (PR #187 review round 1: the same collision shape
    `_publish_artifacts_atomically` / `_reject_builds_root_input_collision`
    already guard against for `arrange` / `package`, applied here to
    `observe`'s single `-o` output file)."""
    from svp_rpe.arrange.identity import _resolve_confined

    base_dir = manifest_path.resolve().parent
    work_id = manifest.meta.work_id
    paths = [package_path.resolve(), manifest_path.resolve(), audio_path.resolve()]
    paths.append(
        _resolve_confined(manifest.source.locator, base_dir, work_id=work_id, target="source")
    )
    for anchor in manifest.anchors:
        paths.append(
            _resolve_confined(
                anchor.artifact, base_dir, work_id=work_id, target=f"anchor '{anchor.id}'"
            )
        )
    return paths


def _reject_observe_output_collision(output_path: str | Path, input_paths: list[Path]) -> None:
    """Reject `-o` if it aliases any input path — by resolved-path equality,
    and (PR #187 review round 3) by same-file identity (`os.path.samefile`,
    i.e. same `st_dev`/`st_ino`) when the output path already exists on disk.
    The resolved-path check alone misses a hard link: two distinct paths that
    are actually the same inode, neither of which is a symlink the other
    resolves through, so `Path.resolve()` leaves them unequal even though
    writing through one clobbers the other. `samefile` requires both paths to
    exist; if it can't be determined (e.g. an input vanished between
    resolution and this check, or some other OSError), that pair is skipped
    and only the plain path-equality result stands — a determination failure
    here must never crash the CLI, just fall back to the weaker check.
    """
    import os

    resolved_output = Path(output_path).resolve()
    output_exists = resolved_output.exists()
    for input_path in input_paths:
        if input_path == resolved_output:
            raise ValueError(f"input path collides with output artifact path: {resolved_output}")
        if output_exists:
            try:
                same_file = os.path.samefile(resolved_output, input_path)
            except OSError:
                continue
            if same_file:
                raise ValueError(
                    f"input path collides with output artifact path: {resolved_output} "
                    f"(same file as {input_path})"
                )


def _write_observation_report_atomically(output_path: Path, content: str) -> None:
    """Publish `output_path` via staging file + `os.replace` (PR #187 review
    round 6), the same minimal pattern `_update_builds_latest_pointer` uses
    for `latest.json`: write to a tempfile in the same directory, then
    atomically rename it onto the target. A partially-written report must
    never be observable as a complete one, whether the process is
    interrupted mid-write or an existing report is being overwritten (D-1's
    own instrument still needs its own output to be trustworthy). Any
    failure cleans up the staging file on a best-effort basis (its own
    unlink failure is swallowed) before re-raising.
    """
    import os
    import tempfile

    output_dir = output_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=output_dir, prefix=f"{output_path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_name, output_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


@app.command("observe")
def observe_cmd(
    package_json: str = typer.Argument(..., help="Path to performance_package.json"),
    audio: str = typer.Argument(..., help="Path to the generated audio (WAV/MP3)"),
    manifest: str = typer.Option(
        ..., "--manifest", help="Path to the IdentityManifest YAML used to compile the package"
    ),
    output: str = typer.Option(
        ..., "-o", "--output", help="Output observation report JSON path"
    ),
) -> None:
    """Record post-generation anchor observations against a generated artifact (AR4).

    Instrument, not verdict (Design Memo D-1): `adherence_status` is only ever
    set when it can be decided without a threshold — `not_observed` when no
    sensor is wired for an anchor's domain, `preserved` on an exact identity
    match, and `not_observed` (with `determination: "deferred"`) plus the raw
    measurements when the sensor ran but did not match exactly. Threshold-based
    `changed_within_policy` / `changed_outside_policy` classification is out of
    scope until a future Design Memo fixes it.

    Before measuring, verifies the provenance chain (D-3): the `--manifest`
    file's sha256 must equal `package.inputs.identity_manifest.sha256`, and
    every manifest anchor's artifact hash must match the file on disk (via the
    same loader `package` uses). A broken chain or malformed package exits 1
    without measuring anything — this instrument refuses to run against
    inputs it cannot trust. `-o` is also rejected (exit 1, nothing written) if
    it resolves to any input path (package / manifest / audio / manifest
    source locator / any anchor artifact) — the same input/output collision
    guard `arrange` / `package` apply to their own outputs.

    Scope boundary (PR #187 review round 16, P2 rejected by design): the
    cross-object checks here stop at *identifying* the observation target —
    manifest sha256, `work_id`, and the `anchor_statuses` id set, i.e. "does
    this package describe the same work and anchor set this manifest does".
    Internal consistency of `package`'s own fields (e.g. `channel_artifacts`
    entries' `artifact` / `artifact_sha256` matching the manifest anchors they
    reference) is `package`'s own schema-validation responsibility, not
    `observe`'s — a future `verify` command is the right home for exhaustive
    package-internal consistency checking, so that job doesn't creep into this
    instrument's provenance-identification role.
    """
    import hashlib
    import os
    import tempfile

    import yaml
    from pydantic import ValidationError

    from svp_rpe.arrange.identity import (
        IdentityManifestError,
        parse_identity_manifest_with_artifacts,
    )
    from svp_rpe.arrange.observe import build_observation_report, is_harmony_sensor_anchor
    from svp_rpe.arrange.package import PerformancePackage

    package_path = Path(package_json)
    manifest_path = Path(manifest)
    audio_path = Path(audio)

    try:
        package_bytes = package_path.read_bytes()
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    try:
        package = PerformancePackage.model_validate_json(package_bytes)
    except ValidationError as exc:
        typer.echo(f"Error: performance package failed schema validation: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if manifest_sha256 != package.inputs.identity_manifest.sha256:
        typer.echo(
            "Error: identity manifest sha256 does not match "
            f"package.inputs.identity_manifest.sha256: {manifest_sha256} != "
            f"{package.inputs.identity_manifest.sha256}",
            err=True,
        )
        raise typer.Exit(code=1)

    # Parse from the bytes already read and hashed above — `manifest_path` is
    # only used to resolve anchor/source locators and for error messages, the
    # manifest file itself is never read a second time (PR #187 review round 1).
    # `artifact_bytes_by_id` carries each anchor artifact's already-verified
    # bytes (same read the hash check performed) so the harmony sensor doesn't
    # reopen the file either (PR #187 review round 2). The exception set below
    # matches `package`'s own manifest-parsing catch (PR #187 review round 6):
    # a manifest can hash-match the package's recorded sha256 and still be
    # malformed YAML, non-mapping, or schema-invalid content.
    try:
        # Collect only the anchors the harmony sensor actually reads content
        # for (PR #187 review round 11) — every anchor's bytes are still read
        # once and hash-verified regardless, but bytes for anchors outside
        # this predicate (e.g. a large `audio_excerpt` reference) are
        # discarded immediately rather than held in `artifact_bytes_by_id`
        # for the whole observe run. Widen `is_harmony_sensor_anchor` (in
        # observe.py, shared with `_observe_anchor`'s routing) if a future
        # sensor needs another anchor's bytes.
        loaded_manifest, artifact_bytes_by_id = parse_identity_manifest_with_artifacts(
            manifest_bytes, manifest_path, collect=is_harmony_sensor_anchor
        )
    except (IdentityManifestError, ValueError, ValidationError, yaml.YAMLError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # PR #187 review round 10: the sha256 chain check above only proves the
    # manifest *bytes* the package recorded and the `--manifest` file agree —
    # it says nothing about whether they describe the *same work*. Two
    # different manifests could (accidentally or not) hash-match a stale
    # `package.inputs.identity_manifest.sha256` from an unrelated build only
    # if the bytes are literally identical, in which case work_id already
    # matches too — but defense in depth belongs at the provenance-chain
    # layer, not assumed from the hash check alone.
    if package.work_id != loaded_manifest.meta.work_id:
        typer.echo(
            "Error: package.work_id does not match manifest.meta.work_id: "
            f"{package.work_id!r} != {loaded_manifest.meta.work_id!r}",
            err=True,
        )
        raise typer.Exit(code=1)

    # PR #187 review round 15: sha256 and work_id matching still don't rule
    # out a package whose `anchor_statuses` set of anchor ids was tampered to
    # no longer match `loaded_manifest.anchors` (missing an anchor the
    # manifest declares, or carrying an extra one it doesn't) — a downstream
    # anchor_id join between this observation report and the package would
    # silently describe a different anchor set than the package's own
    # provenance fields claim. Order-independent set comparison; either
    # direction of mismatch is reported.
    package_anchor_ids = {status.anchor_id for status in package.anchor_statuses}
    manifest_anchor_ids = {anchor.id for anchor in loaded_manifest.anchors}
    if package_anchor_ids != manifest_anchor_ids:
        missing_from_package = sorted(manifest_anchor_ids - package_anchor_ids)
        extra_in_package = sorted(package_anchor_ids - manifest_anchor_ids)
        details = []
        if missing_from_package:
            details.append(f"missing from package.anchor_statuses: {missing_from_package}")
        if extra_in_package:
            details.append(f"extra in package.anchor_statuses: {extra_in_package}")
        typer.echo(
            "Error: package.anchor_statuses anchor_id set does not match "
            "manifest.anchors id set (" + "; ".join(details) + ")",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        protected_paths = _observe_protected_input_paths(
            package_path, manifest_path, audio_path, loaded_manifest
        )
        _reject_observe_output_collision(output, protected_paths)
    except (IdentityManifestError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        audio_bytes = audio_path.read_bytes()
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    audio_sha256 = hashlib.sha256(audio_bytes).hexdigest()
    package_sha256 = hashlib.sha256(package_bytes).hexdigest()

    # PR #187 review round 13: only build the snapshot tempfile when
    # extraction will actually happen — the same predicate
    # `build_observation_report` gates its own `extract_rpe_from_file` call
    # on (round 12), so routing / extraction-gate / snapshot-gate all agree
    # on a single source of truth (no anchor matching `is_harmony_sensor_anchor`
    # means nothing ever reads audio content, so the TOCTOU concern the
    # snapshot exists for (round 1) doesn't arise — there's no "bytes actually
    # measured" to keep in sync with the hashed bytes).
    needs_extraction = any(
        is_harmony_sensor_anchor(anchor) for anchor in loaded_manifest.anchors
    )
    snapshot_path: Optional[Path] = None
    try:
        if needs_extraction:
            # Extract from a snapshot of exactly the bytes just hashed, not
            # from `audio_path` directly — this makes "the sha256 recorded in
            # the report" and "the bytes actually measured" the same bytes by
            # construction, not merely by the assumption that nothing touches
            # `audio_path` in between (PR #187 review round 1).
            # `generated_artifact.path` in the report still reports the
            # user-supplied `audio` string, not this temp path.
            #
            # PR #187 review round 17: `mkstemp` / the snapshot write can
            # raise `OSError` (temp disk full, unwritable, etc.) — wrapped in
            # the same "Error: ..." + exit 1 handling every other failure
            # path in this command uses, instead of escaping as a raw
            # traceback. `snapshot_path` is set immediately after `mkstemp`
            # succeeds (before the write), so the outer `finally` still
            # cleans up a partially-written temp file on a write failure.
            try:
                snapshot_fd, snapshot_name = tempfile.mkstemp(
                    suffix=audio_path.suffix or ".wav"
                )
                snapshot_path = Path(snapshot_name)
                with os.fdopen(snapshot_fd, "wb") as snapshot_file:
                    snapshot_file.write(audio_bytes)
            except OSError as exc:
                typer.echo(f"Error: {exc}", err=True)
                raise typer.Exit(code=1) from exc
            measurement_audio_path = snapshot_path
        else:
            measurement_audio_path = audio_path

        try:
            report = build_observation_report(
                package=package,
                manifest=loaded_manifest,
                manifest_path=manifest_path,
                artifact_bytes=artifact_bytes_by_id,
                audio_path=measurement_audio_path,
                package_sha256=package_sha256,
                audio_sha256=audio_sha256,
                generated_artifact_path=audio,
            )
        except (OSError, ValueError, ValidationError, IdentityManifestError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    finally:
        if snapshot_path is not None:
            snapshot_path.unlink(missing_ok=True)

    content = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
    try:
        _write_observation_report_atomically(Path(output), content)
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    table = Table(title=f"Observation: {package.work_id}")
    table.add_column("anchor_id")
    table.add_column("domain")
    table.add_column("sensor")
    table.add_column("available")
    table.add_column("adherence_status")
    table.add_column("determination")
    table.add_column("measurements")
    for anchor_observation in report.anchors:
        measurements_summary = ", ".join(
            f"{key}={value}" for key, value in anchor_observation.measurements.items()
        )
        table.add_row(
            anchor_observation.anchor_id,
            anchor_observation.domain,
            anchor_observation.sensor.name,
            "yes" if anchor_observation.sensor.available else "no",
            anchor_observation.adherence_status,
            anchor_observation.determination,
            measurements_summary,
        )
    console.print(table)
    console.print(f"[green]Observation report saved to {output}[/green]")


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


@app.command()
def evaluate(
    audio: str = typer.Option(..., "--audio", help="Path to audio file"),
    svp: Optional[str] = typer.Option(None, "--svp", help="Path to external SVP file"),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output JSON path"),
    valley_method: str = typer.Option("v2", "--valley-method",
                                       help="Valley method: v2/legacy_hybrid/rms_percentile/section_ar/hybrid"),
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
    valley_method: str = typer.Option("v2", "--valley-method",
                                       help="Valley method: v2/legacy_hybrid/rms_percentile/section_ar/hybrid"),
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
    valley_method: str = typer.Option("v2", "--valley-method",
                                       help="Valley method: v2/legacy_hybrid/rms_percentile/section_ar/hybrid"),
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
        "v2",
        "--valley-method",
        help="Valley method for audio input: v2/legacy_hybrid/rms_percentile/section_ar/hybrid",
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
