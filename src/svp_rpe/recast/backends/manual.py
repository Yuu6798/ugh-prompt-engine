"""ManualInvoker: generator 非依存の汎用「注文書」ビルダー。

Suno のような手動生成器固有の分岐は一切ここに持ち込まない — invocation_mode
（cover / prompt_only）による分岐のみで、generator 名では分岐しない
（PR3 指示書「Suno 例外分岐は manual backend に集約（散在なし）」）。

`prepare()` は注文書 6 ファイル（`prompt.json` / `lyrics.txt` /
`section_tags.txt` / `order_sheet.md` / `expected_artifacts.json` /
`next_command.txt`）を全て決定論的に構築し（タイムスタンプなし・
checkout-stable 相対パスのみ）、`<builds_root>/orders/<variant>@<backend>/`
へ atomic 公開する（`cli/builds_root.py` の `_publish_artifacts_atomically`）。

`invoke()` は常に `RecastError`（manual backend はローカル生成できない）。
`collect()` は外部生成された音声を受領し、`<builds_root>/takes/<variant>@<backend>/`
へ収蔵する — こちらは manual invocation の実受領経路。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

from svp_rpe.arrange.pathsafe import resolve_confined
from svp_rpe.arrange.section_map import (
    parse_section_map_artifact_0_1,
    parse_section_map_artifact_0_2,
)
from svp_rpe.cli.builds_root import _publish_artifacts_atomically
from svp_rpe.recast.backend import (
    GeneratedTake,
    PreparedInvocation,
    RecastRunContext,
    atomic_publish_bytes_bundle,
    base_prepared_invocation,
)
from svp_rpe.recast.loader import LoadedRecastProject
from svp_rpe.recast.models import RecastError

_ACCEPTED_AUDIO_EXTENSIONS = (".wav", ".mp3")
_LYRICS_ANCHOR_PLACEHOLDER = "(歌詞アンカーなし)\n"
_SECTION_TAGS_PLACEHOLDER = "(section tags なし)\n"


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _prompt_json(prepared: PreparedInvocation) -> str:
    prompt = prepared.package.prompt
    payload = {
        "generator": prepared.generator,
        "invocation_mode": prepared.invocation_mode,
        "package_sha256": prepared.package_sha256,
        "content_digest": prepared.content_digest,
        "prompt": None
        if prompt is None
        else {
            "text": prompt.text,
            "tags": list(prompt.tags),
            "negative_tags": list(prompt.negative_tags),
            "section_tags": prompt.section_tags,
        },
    }
    return _canonical_json(payload)


def _identity_manifest_dir(loaded: LoadedRecastProject) -> Path:
    return loaded.identity_manifest_path.resolve().parent


def _lyrics_text(prepared: PreparedInvocation, loaded: LoadedRecastProject) -> str:
    refs = prepared.package.channel_artifacts.get("lyrics_text")
    if not refs:
        return _LYRICS_ANCHOR_PLACEHOLDER
    artifact_path = resolve_confined(refs[0].artifact, _identity_manifest_dir(loaded))
    text = artifact_path.read_text(encoding="utf-8")
    return text if text.endswith("\n") else text + "\n"


def _section_tags_text(prepared: PreparedInvocation, loaded: LoadedRecastProject) -> str:
    prompt = prepared.package.prompt
    if prompt is not None and prompt.section_tags:
        return prompt.section_tags if prompt.section_tags.endswith("\n") else prompt.section_tags + "\n"

    refs = prepared.package.channel_artifacts.get("section_tags")
    if refs:
        artifact_path = resolve_confined(refs[0].artifact, _identity_manifest_dir(loaded))
        raw = artifact_path.read_bytes()
        try:
            v2 = parse_section_map_artifact_0_2(raw, artifact_path=artifact_path)
            return ", ".join(f"{entry.id}:{entry.label}" for entry in v2.sections) + "\n"
        except ValueError:
            pass
        try:
            v1 = parse_section_map_artifact_0_1(raw, artifact_path=artifact_path)
            return ", ".join(v1.sections) + "\n"
        except ValueError:
            pass
    return _SECTION_TAGS_PLACEHOLDER


def _expected_artifacts_json(prepared: PreparedInvocation) -> str:
    payload = {
        "accepted_formats": ["wav", "mp3"],
        "content_digest": prepared.content_digest,
        "filename_pattern": "take-01.{wav,mp3}",
        "package_sha256": prepared.package_sha256,
    }
    return _canonical_json(payload)


def _next_command_text(ctx: RecastRunContext, prepared: PreparedInvocation) -> str:
    project_relative = ctx.loaded.path.name
    takes_relative = os.path.relpath(prepared.takes_dir, ctx.loaded.project_dir)
    audio_relative = f"{takes_relative}/take-01.wav"
    return (
        f"svprpe recast ingest {project_relative} --variant {prepared.variant} "
        f"--backend {prepared.backend_name} --audio {audio_relative}\n"
    )


def _order_sheet_md(ctx: RecastRunContext, prepared: PreparedInvocation, next_command: str) -> str:
    hard_anchors = [
        anchor for anchor in ctx.plan_result.plan.anchors if anchor.policy_mode == "hard"
    ]
    mode_notes = [
        f"{change.path} ({change.mode_support}): {change.note}"
        for change in ctx.plan_result.plan.changed_fields
        if change.note is not None
    ]
    takes_relative = os.path.relpath(prepared.takes_dir, ctx.loaded.project_dir)

    lines: list[str] = [
        f"# Recast order sheet: {prepared.variant}@{prepared.backend_name}",
        "",
        f"- generator: {prepared.generator}",
        f"- invocation_mode: {prepared.invocation_mode}",
        "",
    ]
    if prepared.invocation_mode == "cover":
        lines += [
            "## 手順（cover: 参照音声からのカバー生成）",
            "",
            "次の identity source を参照音声として添付し、そこからカバー生成してください:",
            f"- locator: {prepared.identity_source_locator}",
            f"- sha256: {prepared.identity_source_sha256}",
        ]
    else:
        lines += [
            "## 手順（prompt_only: テキストのみで生成）",
            "",
            "参照音声は使わず、`prompt.json` のテキスト + `lyrics.txt` の歌詞 + "
            "`section_tags.txt` のタグのみで生成してください。",
        ]

    lines += ["", "## 保持すべき hard anchor", ""]
    if hard_anchors:
        for anchor in hard_anchors:
            lines.append(f"- {anchor.anchor_id} ({anchor.domain})")
    else:
        lines.append("- (hard anchor なし)")

    lines += ["", f"## mode_overrides 由来の注意（invocation_mode={prepared.invocation_mode}）", ""]
    if mode_notes:
        for note in mode_notes:
            lines.append(f"- {note}")
    else:
        lines.append("- (実測記録なし)")

    lines += [
        "",
        "## 出力音源の保存",
        "",
        f"生成した音源を `{takes_relative}/take-01.wav`（または `.mp3`）として保存し、"
        "以下のコマンドで取り込んでください:",
        "",
        "```",
        next_command.rstrip("\n"),
        "```",
        "",
    ]
    return "\n".join(lines) + "\n"


class ManualInvoker:
    """`invocation == "manual"` の全 backend で共有する generator 非依存 invoker。"""

    def prepare(self, ctx: RecastRunContext) -> PreparedInvocation:
        prepared = base_prepared_invocation(ctx)
        next_command = _next_command_text(ctx, prepared)
        contents = {
            "prompt.json": _prompt_json(prepared),
            "lyrics.txt": _lyrics_text(prepared, ctx.loaded),
            "section_tags.txt": _section_tags_text(prepared, ctx.loaded),
            "expected_artifacts.json": _expected_artifacts_json(prepared),
            "next_command.txt": next_command,
            "order_sheet.md": _order_sheet_md(ctx, prepared, next_command),
        }
        # 出力パスが project.yaml/score/identity manifest/arrangement/
        # capability_profile/mode_overrides/manifest 側 anchor artifact・source
        # のいずれとも衝突しないことを保証する（Codex P2 review, PR3 #208
        # 指摘 2 — 従来は project/score/manifest/arrangement の 4 者のみが
        # 対象だった）。`prepared.protected_input_paths` は
        # `base_prepared_invocation` が計算済みの同じ値 — ここで再計算しない
        # single source（指摘 6/7 対応で全公開サイト共通化）。
        _publish_artifacts_atomically(
            contents, prepared.order_dir, prepared.protected_input_paths
        )
        return prepared

    def invoke(self, prepared: PreparedInvocation) -> GeneratedTake:
        raise RecastError(
            "manual backend は invoke 不可。注文書に従い外部生成後 ingest してください "
            f"(注文書: {prepared.order_dir})"
        )

    def collect(self, prepared: PreparedInvocation, supplied_audio: Path) -> GeneratedTake:
        if not supplied_audio.is_file():
            raise RecastError(f"supplied audio does not exist: {supplied_audio}")
        extension = supplied_audio.suffix.lower()
        if extension not in _ACCEPTED_AUDIO_EXTENSIONS:
            raise RecastError(
                "supplied audio must have one of "
                f"{_ACCEPTED_AUDIO_EXTENSIONS} extensions, got {supplied_audio.suffix!r}: "
                f"{supplied_audio}"
            )
        data = supplied_audio.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        take_filename = f"take-01{extension}"
        target = prepared.takes_dir / take_filename

        take_record = _canonical_json(
            {
                "sha256": sha256,
                "source": "manual",
                "backend_name": prepared.backend_name,
                "original_filename": supplied_audio.name,
            }
        )
        # 音声 + take.json を 1 組として atomic publish する（Codex P2 review,
        # PR3 #208 指摘 1: 個別 publish だと take.json 書き込み失敗時に
        # provenance の無い音声だけが takes_dir に残り得る）。`protected_inputs`
        # には `supplied_audio` 自体を含めない — `data` は `supplied_audio` から
        # 既に読み終えているため、`supplied_audio` が最終公開先そのもの
        # （`svprpe recast ingest` の next_command.txt が案内する
        # `<takes_dir>/take-01.wav` に生成音声を直接置いてから ingest する自然な
        # 運用）であっても安全な自己上書き。`prepared.protected_input_paths`
        # （project/score/manifest/arrangement/capability_profile/
        # mode_overrides/anchor artifact・source）とは衝突しないことを保証する
        # （Codex P2 review, PR3 #208 指摘 6: 従来 collect() は一切渡していなかった）。
        atomic_publish_bytes_bundle(
            prepared.takes_dir,
            {take_filename: data, "take.json": take_record.encode("utf-8")},
            protected_inputs=prepared.protected_input_paths,
        )

        note: Optional[str] = f"collected from {supplied_audio.name}"
        return GeneratedTake(
            audio_path=target,
            sha256=sha256,
            source="manual",
            backend_name=prepared.backend_name,
            note=note,
        )
