"""svprpe roundtrip-corpus / roundtrip-rep / genre-calibrate / genre-audit."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click
import typer

from svp_rpe.cli._app import app


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
