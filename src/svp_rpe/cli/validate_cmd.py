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
    """`-o` が入力パス（`score.yaml` または `--contract` spec）そのものと
    衝突する（書き込み前に拒否）。

    `examples/l0s_spike/scripts/validate_score.py` の出力衝突ガード規則 (c)
    （入力パスとの衝突拒否）を踏襲し、この CLI が受け取る**全入力パス**
    （`score.yaml` に加え、与えられていれば `--contract`）へ拡張する
    （PR #245/#246 の衝突ファミリー掃討と同じ規則: 保護すべき入力が増えたら
    保護集合も機械的に追随させる）——同スクリプトの規則 (a)/(b)（保護済み
    スパイク木への衝突）はスパイク成果物専用の懸念で、汎用 CLI である本
    コマンドの対象外のまま。
    """


def _reject_output_collision(
    output_path: Path, *, score_path: Path, contract_path: Optional[Path] = None
) -> None:
    resolved_output = output_path.resolve()
    protected: list[tuple[str, Path]] = [("score.yaml", score_path.resolve())]
    if contract_path is not None:
        protected.append(("--contract", contract_path.resolve()))

    for label, resolved_protected in protected:
        if resolved_output == resolved_protected:
            raise ProtectedPathError(
                f"-o/--output must not resolve to the {label} input path itself "
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
    built once and shared between the hash-relevant content and the write.
    `-o` writes via `svp_rpe.utils.atomic_io.atomic_write_bytes` (tempfile +
    `os.replace`, PR #246 Codex P2 review 8 巡目 C) rather than a direct
    `write_bytes` — a partial write must never be observable as a complete
    report — while still avoiding `write_text`'s platform-dependent newline
    translation (same byte-determinism convention `validate_score.py`/
    `measure_round.py` use).

    Exit codes: `0` = pass, `1` = fail (including a `score.yaml` that fails to
    parse as YAML, or one that parses but has a duplicate mapping key at any
    depth (PR #246 Codex P2 review 18 巡目 B — a plain YAML loader silently
    keeps the last value, masking the earlier one) — both are recorded as a
    `canonical`-kind error at `<file>`, not an operational error, matching
    `validate_score.py`'s precedent), `2` = operational error (missing
    `score.yaml`; an unreadable, malformed, non-mapping-YAML, or
    duplicate-key `--contract` spec — a YAML parse failure (`YAMLError`), a
    duplicate mapping key or a document that parses but isn't a mapping
    (`ValueError` from `contract.py`'s loader), or one that fails the spec's
    own schema (`ValidationError`) are all classified alike; or an `-o` path
    colliding with the `score.yaml` or `--contract` input). This is a
    deliberate asymmetry with `score.yaml`'s own YAML-parse-failure handling
    above: an invalid *score* is the artifact under test failing (`fail`,
    exit `1`), while an invalid *contract* is the measuring instrument itself
    being broken (an operational error, exit `2`) — the contract is the
    validator's own configuration, not the thing being validated (Codex P2
    review, PR #246).
    """
    from svp_rpe.authoring.contract import load_authoring_contract
    from svp_rpe.authoring.report import dump_json_bytes
    from svp_rpe.authoring.validate import AuthoringErrorItem, SymbolicValidationResult, validate_score
    from svp_rpe.melody.representation import _NoDupSafeLoader

    if output_format not in ("text", "json"):
        raise typer.BadParameter("--format must be 'text' or 'json'", param_hint="--format")

    score_path = Path(score)
    try:
        score_text = score_path.read_text(encoding="utf-8")
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    spec = None
    contract_path: Optional[Path] = None
    if contract is not None:
        contract_path = Path(contract)
        try:
            spec = load_authoring_contract(contract_path)
        except (OSError, yaml.YAMLError, ValueError, ValidationError) as exc:
            typer.echo(f"Error: failed to load authoring contract spec: {exc}", err=True)
            raise typer.Exit(code=2) from exc

    output_path: Optional[Path] = None
    if output is not None:
        output_path = Path(output)
        try:
            _reject_output_collision(
                output_path, score_path=score_path, contract_path=contract_path
            )
        except ProtectedPathError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=2) from exc

    try:
        raw = yaml.load(score_text, Loader=_NoDupSafeLoader)  # noqa: S506 (dup-key 拒否付き SafeLoader)
    except (yaml.YAMLError, ValueError) as exc:
        # PR #246 Codex P2 review 18 巡目 B: 素の `yaml.safe_load` は
        # `score.yaml` の重複 mapping キーを「最後の値が勝つ」形で黙って
        # 受理していた（例: `physical: {bpm: 0}` の直後に `physical: {bpm:
        # 120}` を書くと後者だけが読まれる）——被検証物である score 自体の
        # 不正なので、YAML parse 失敗と同じ `fail`/exit 1 経路（`canonical`
        # kind、`where="<file>"`）で報告する。`melody.representation.
        # _NoDupSafeLoader`（import のみ・重複実装禁止——`recast/
        # experimental.py:_load_m1_registry` と同じ再利用パターン）は
        # 重複キー検出時に `ValueError` を送出するため、既存の
        # `yaml.YAMLError` 捕捉に `ValueError` を追加するだけで両方が
        # 同じ fail 経路に乗る。
        result = SymbolicValidationResult(
            status="fail",
            errors=[AuthoringErrorItem(where="<file>", message=str(exc), kind="canonical")],
        )
    else:
        result = validate_score(raw, spec)

    content_bytes = dump_json_bytes(result)
    if output_path is not None:
        from svp_rpe.utils.atomic_io import atomic_write_bytes

        # PR #246 Codex P2 review 8 巡目 C: `-o` を直接 `write_bytes` して
        # いた（部分書き込みが観測されうる非原子な書き出し）のを、
        # `arrange`/`recast` 等が使う共通の atomic writer へ揃えた
        # （tempfile へ書いて `os.replace` — 同ディレクトリ内 rename は
        # POSIX で atomic）。`content_bytes` は既に一度だけ構築済みの
        # バイト列をそのまま渡すため、バイト決定論（同一入力 2 回で
        # 出力バイト一致）は不変。衝突ガード（`_reject_output_collision`）
        # → atomic 書き込み、という順序も維持する。
        #
        # 9 巡目 C: `atomic_write_bytes` 自体が送出しうる `OSError`
        # （`-o` が既存ディレクトリを指す等）が未捕捉のまま生トレースバック
        # で漏れていたのを、他の書き込み失敗経路と同じ
        # `Error: ... / exit 2` へ統一する。
        try:
            atomic_write_bytes(output_path, content_bytes)
        except OSError as exc:
            typer.echo(f"Error: failed to write -o/--output: {exc}", err=True)
            raise typer.Exit(code=2) from exc

    if output_format == "json":
        typer.echo(content_bytes.decode("utf-8"), nl=False)
    else:
        _render_text(result, score_path=score_path)
        if output_path is not None:
            console.print(f"[green]status={result.status} -> {output_path}[/green]")

    raise typer.Exit(code=0 if result.status == "pass" else 1)
