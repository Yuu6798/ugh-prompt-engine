"""svprpe validate — L0a 記号検証ゲート CLI (D-L0a-2)。音声処理ゼロ。"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

import click
import typer
import yaml
from pydantic import ValidationError
from rich.table import Table

from svp_rpe.cli._app import app, console

if TYPE_CHECKING:
    from svp_rpe.authoring.validate import SymbolicValidationResult

FORMAT_CHOICE = click.Choice(["text", "json"])


class ProtectedPathError(RuntimeError):
    """`-o` が入力の `score.yaml` パスそのものと衝突する（書き込み前に拒否）。

    `examples/l0s_spike/scripts/validate_score.py` の出力衝突ガード規則 (c)
    （入力パスとの衝突拒否）のみを踏襲する——同スクリプトの規則 (a)/(b)
    （保護済みスパイク木への衝突）はスパイク成果物専用の懸念で、汎用 CLI で
    ある本コマンドの対象外。
    """


def _reject_output_collision(output_path: Path, *, score_path: Path) -> None:
    resolved_output = output_path.resolve()
    resolved_score = score_path.resolve()
    if resolved_output == resolved_score:
        raise ProtectedPathError(
            f"-o/--output must not resolve to the score.yaml input path itself "
            f"(got {resolved_output}) — writing the result there would clobber "
            "the input this run reads."
        )


def _render_text(result: "SymbolicValidationResult", *, score_path: Path) -> None:
    color = "green" if result.status == "pass" else "red"
    console.print(f"[bold]Validate: {score_path}[/bold]")
    console.print(f"status=[{color}]{result.status}[/{color}]")
    if not result.errors:
        return
    table = Table()
    table.add_column("where")
    table.add_column("kind")
    table.add_column("message")
    for error in result.errors:
        table.add_row(error.where, error.kind, error.message)
    console.print(table)


@app.command("validate")
def validate_cmd(
    score: str = typer.Argument(..., help="Path to score.yaml"),
    contract: Optional[str] = typer.Option(
        None,
        "--contract",
        help=(
            "Path to an authoring contract spec YAML (public-scope check). "
            "Omit for canonical-only validation (defaults to no public-scope check)."
        ),
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        click_type=FORMAT_CHOICE,
        help="Console output format: text (default) or json.",
    ),
    output: Optional[str] = typer.Option(
        None, "-o", "--output", help="Write the result as byte-deterministic JSON to this path."
    ),
) -> None:
    """Audio-free symbolic validation gate for a `score.yaml` (L0a, D-L0a-2).

    Runs two checks, always both (a public-scope failure never short-circuits
    canonical validation — same design as
    `examples/l0s_spike/scripts/validate_score.py`, this command's spike-era
    predecessor, which stays unmodified as a frozen historical artifact):

    1. **Public-scope check** — only when `--contract` is given: every key at
       every depth must be published by the contract spec's schema tree, and
       every published field's type/enum/literal/format must match. Omitting
       `--contract` skips this check entirely (canonical-only mode).
    2. **Canonical validation** — the score must validate against
       `CompositionScore` (`svp_rpe.compose.models`).

    Errors are a deterministically sorted list of `{where, message, kind}`
    (`kind` in `public_scope`/`type`/`enum`/`literal`/`format`/`canonical`).

    Output JSON (`{"status": "pass"|"fail", "errors": [...]}` when `-o` is
    given, or when `--format json`) is byte-deterministic: the JSON bytes are
    built once and shared between the hash-relevant content and the write —
    `write_bytes`, not `write_text`, to avoid platform-dependent newline
    translation (same convention `validate_score.py`/`measure_round.py` use).

    Exit codes: `0` = pass, `1` = fail (including a `score.yaml` that fails to
    parse as YAML — recorded as a `canonical`-kind error at `<file>`, not an
    operational error, matching `validate_score.py`'s precedent), `2` =
    operational error (missing `score.yaml`, an unreadable/invalid
    `--contract` spec, or an `-o` path colliding with the `score.yaml` input).
    """
    from svp_rpe.authoring.contract import load_authoring_contract
    from svp_rpe.authoring.report import dump_json_bytes
    from svp_rpe.authoring.validate import AuthoringErrorItem, SymbolicValidationResult, validate_score

    if output_format not in ("text", "json"):
        raise typer.BadParameter("--format must be 'text' or 'json'", param_hint="--format")

    score_path = Path(score)
    try:
        score_text = score_path.read_text(encoding="utf-8")
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    spec = None
    if contract is not None:
        try:
            spec = load_authoring_contract(Path(contract))
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            typer.echo(f"Error: failed to load authoring contract spec: {exc}", err=True)
            raise typer.Exit(code=2) from exc

    output_path: Optional[Path] = None
    if output is not None:
        output_path = Path(output)
        try:
            _reject_output_collision(output_path, score_path=score_path)
        except ProtectedPathError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=2) from exc

    try:
        raw = yaml.safe_load(score_text)
    except yaml.YAMLError as exc:
        result = SymbolicValidationResult(
            status="fail",
            errors=[AuthoringErrorItem(where="<file>", message=str(exc), kind="canonical")],
        )
    else:
        result = validate_score(raw, spec)

    content_bytes = dump_json_bytes(result)
    if output_path is not None:
        output_path.write_bytes(content_bytes)

    if output_format == "json":
        typer.echo(content_bytes.decode("utf-8"), nl=False)
    else:
        _render_text(result, score_path=score_path)
        if output_path is not None:
            console.print(f"[green]status={result.status} -> {output_path}[/green]")

    raise typer.Exit(code=0 if result.status == "pass" else 1)
