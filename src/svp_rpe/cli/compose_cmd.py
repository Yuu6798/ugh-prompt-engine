"""svprpe compose."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click
import typer

from svp_rpe.cli._app import app, console


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
