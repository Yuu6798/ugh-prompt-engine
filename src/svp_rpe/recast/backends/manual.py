"""ManualInvoker: generator 非依存の汎用「注文書」ビルダー。

Suno のような手動生成器固有の分岐は一切ここに持ち込まない — invocation_mode
（cover / prompt_only）による分岐のみで、generator 名では分岐しない
（PR3 指示書「Suno 例外分岐は manual backend に集約（散在なし）」）。

`prepare()` は注文書 6 ファイル（`prompt.json` / `lyrics.txt` /
`section_tags.txt` / `order_sheet.md` / `expected_artifacts.json` /
`next_command.txt`）を全て決定論的に構築し（タイムスタンプなし・
checkout-stable 相対パスのみ）、`<builds_root>/orders/<variant>@<backend>/`
へ atomic 公開する（`recast/backend.py` の `atomic_publish_bytes_bundle` —
Codex P2 ninth round #207 指摘17: 以前は `cli/builds_root.py:
_publish_artifacts_atomically`（rename フェーズの rollback が `except
OSError` のみ）に依存しており、rename 中の `KeyboardInterrupt`/`SystemExit`
で 6 ファイルの一部だけが更新された状態が残り得た。`atomic_publish_bytes_
bundle` は `except BaseException` で統一済み — PR3 #208 指摘 14 参照）。

`invoke()` は常に `RecastError`（manual backend はローカル生成できない）。
`collect()` は外部生成された音声を受領し、`<builds_root>/takes/<variant>@<backend>/`
へ収蔵する — こちらは manual invocation の実受領経路。
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
from pathlib import Path
from typing import List, Optional

from svp_rpe.arrange.package import ChannelArtifactReference
from svp_rpe.arrange.section_map import (
    parse_section_map_artifact_0_1,
    parse_section_map_artifact_0_2,
)
from svp_rpe.recast.backend import (
    GeneratedTake,
    PreparedInvocation,
    RecastRunContext,
    atomic_publish_bytes_bundle,
    base_prepared_invocation,
)
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


def _pinned_artifact_bytes(prepared: PreparedInvocation, ref: ChannelArtifactReference) -> bytes:
    """`ref.anchor_id` の hash 照合済み bytes を `prepared.channel_artifact_bytes`
    から取り出す（Codex P2 review round 11, PR3 #208 指摘22: plan 段
    （`build_recast_plan_artifacts`）が single-read で 1 回だけ読み hash 照合
    済みの bytes をそのまま使い、注文書描画時に artifact ファイルを disk から
    再 read しない — plan 構築から注文書公開までの間にファイルが差し替わっても
    注文書の中身は plan 段の bytes のまま揺らがない）。

    `prepared.package.channel_artifacts` に列挙される lyrics_text/section_tags
    channel の全 ref は、pin 対象を選ぶ `_is_manual_order_channel_anchor`
    （`recast/plan.py`）が同じ artifact_type 集合（`lyrics_text`/`section_map`）
    を対象にしているため必ず辞書に存在する — 見つからない場合は plan/backend
    間の契約違反であり fail-closed（`RecastError`）で止める（silent fallback
    や再 read での代替はしない）。
    """
    try:
        return prepared.channel_artifact_bytes[ref.anchor_id]
    except KeyError as exc:
        raise RecastError(
            f"recast order sheet: anchor {ref.anchor_id!r} (artifact_type="
            f"{ref.artifact_type!r}) has no pinned artifact bytes from the plan "
            "bundle — this indicates a plan/backend contract mismatch, not a "
            "recoverable I/O condition"
        ) from exc


def _lyrics_text(prepared: PreparedInvocation) -> str:
    refs = prepared.package.channel_artifacts.get("lyrics_text")
    if not refs:
        return _LYRICS_ANCHOR_PLACEHOLDER
    # 全 refs を package 内の順（`channel_artifacts` のリスト順）のまま描画する
    # （Codex P2 ninth round #207 指摘18: 従来は `refs[0]` のみを読んでおり、
    # 複数 lyrics anchor を preserve する project では 2 件目以降を黙って
    # 落としていた — plan/package は全 anchor を delivered と報告しているのに
    # manual generator へ渡る `lyrics.txt` には最初の 1 件しか現れず、
    # hard-preservation な後続 anchor が生成へ反映されない事故を招いた）。
    # 単一 ref のときは従来どおり見出しなしのプレーンテキスト（既存 snapshot
    # との互換）、複数のときのみ `anchor_id` 見出しで連結する。
    if len(refs) == 1:
        text = _pinned_artifact_bytes(prepared, refs[0]).decode("utf-8")
        return text if text.endswith("\n") else text + "\n"

    sections: list[str] = []
    for ref in refs:
        text = _pinned_artifact_bytes(prepared, ref).decode("utf-8")
        text = text if text.endswith("\n") else text + "\n"
        sections.append(f"# {ref.anchor_id}\n\n{text}")
    return "\n".join(sections)


def _channel_artifact_refs_section_tags_text(
    prepared: PreparedInvocation, refs: List[ChannelArtifactReference]
) -> Optional[str]:
    """`channel_artifacts["section_tags"]` の全 refs を package 内の順のまま
    描画する（Codex P2 ninth round #207 指摘18 と同じ全数描画の原則を
    section_tags にも適用）。単一 ref は従来どおり見出しなし、複数は
    `anchor_id` 見出しで連結する。

    plan 段の pin 済み bytes（`_pinned_artifact_bytes`、指摘22）から読む —
    disk への再 read はしない。パース不能な ref（v0.1/v0.2 いずれの section
    map schema にも該当しない）は個別には読み飛ばすが、**1 件も解釈できな
    かった場合は `None` を返さず `RecastError` で fail-closed する**（Codex
    P2 review round 11, PR3 #208 指摘23: 従来は 1 件も parse できないと
    黙って `None` を返し、呼び出し側が prompt.section_tags や placeholder
    へ silent fallback していた — refs が 1 件でも delivered と package が
    報告している以上、それが注文書へ一切反映されないまま公開されるのは
    hard anchor の欠落を隠蔽する。parse 可能な ref が 1 件でもあれば、
    それらだけを描画する既存の寛容さは維持する）。"""

    def _render_one(ref: ChannelArtifactReference) -> Optional[str]:
        raw = _pinned_artifact_bytes(prepared, ref)
        try:
            v2 = parse_section_map_artifact_0_2(raw, artifact_path=Path(ref.artifact))
            return ", ".join(f"{entry.id}:{entry.label}" for entry in v2.sections)
        except ValueError:
            pass
        try:
            v1 = parse_section_map_artifact_0_1(raw, artifact_path=Path(ref.artifact))
            return ", ".join(v1.sections)
        except ValueError:
            pass
        return None

    rendered_by_ref = [(ref, _render_one(ref)) for ref in refs]
    if all(rendered is None for _ref, rendered in rendered_by_ref):
        unparseable = ", ".join(
            f"{ref.anchor_id} (format_version={ref.format_version!r})" for ref, _rendered in rendered_by_ref
        )
        raise RecastError(
            "recast order sheet: delivered section_tags channel artifact(s) failed to "
            f"parse under both section-map/0.1 and section-map/0.2 schemas: {unparseable} "
            "— refusing to publish an order sheet that silently drops a delivered hard "
            "anchor"
        )

    if len(refs) == 1:
        rendered = rendered_by_ref[0][1]
        assert rendered is not None  # 上の all() ガードで単一件かつ None のケースは既に raise 済み
        return rendered + "\n"

    sections: list[str] = []
    for ref, rendered in rendered_by_ref:
        if rendered is None:
            continue
        sections.append(f"# {ref.anchor_id}\n\n{rendered}\n")
    return "\n".join(sections)


def _section_tags_text(prepared: PreparedInvocation) -> str:
    # 優先順（Codex P2 ninth round #207 指摘20）: 配送済み channel artifact
    # refs（hard 保存された実 section_map）→ 無ければ prompt.section_tags
    # （derived score が生成した advisory 値）→ どちらも無ければプレース
    # ホルダー。従来は prompt.section_tags を無条件に先に採用しており、
    # channel_artifacts["section_tags"] に実際に delivered な section_map
    # （hard anchor の実体）があってもそちらを一切読んでいなかった —
    # 「plan は delivered と報告するのに manual generator へ渡る
    # section_tags.txt には反映されない」という指摘18と同型の欠落だった。
    #
    # refs が 1 件でもあれば `_channel_artifact_refs_section_tags_text` が
    # 必ず文字列を返すか `RecastError` で fail-closed する（指摘23） —
    # 呼び出し側でこれ以上の None フォールバックは発生しない。
    refs = prepared.package.channel_artifacts.get("section_tags")
    if refs:
        return _channel_artifact_refs_section_tags_text(prepared, refs)

    prompt = prepared.package.prompt
    if prompt is not None and prompt.section_tags:
        return prompt.section_tags if prompt.section_tags.endswith("\n") else prompt.section_tags + "\n"

    return _SECTION_TAGS_PLACEHOLDER


def _expected_artifacts_json(prepared: PreparedInvocation) -> str:
    payload = {
        "accepted_formats": ["wav", "mp3"],
        "content_digest": prepared.content_digest,
        # `cwd`: 注文書内の相対パス（`expected_artifacts.json` 自身は使わないが
        # next_command.txt / order_sheet.md の `--audio` 等）が基準とする作業
        # ディレクトリを明示する（Codex P2 eighth round #207 指摘15: 前提 cwd が
        # どこにも明示されておらず、次善のディレクトリで実行すると相対パスが
        # 解決できなかった）。project.yaml のあるディレクトリ（"." = project_dir）
        # で固定 — checkout 間で不変なので絶対パスは焼き込まない。
        "cwd": ".",
        "filename_pattern": "take-01.{wav,mp3}",
        "package_sha256": prepared.package_sha256,
    }
    return _canonical_json(payload)


def _next_command_text(ctx: RecastRunContext, prepared: PreparedInvocation) -> str:
    project_relative = ctx.loaded.path.name
    takes_relative = os.path.relpath(prepared.takes_dir, ctx.loaded.project_dir)
    audio_relative = f"{takes_relative}/take-01.wav"
    # Codex P2 review round 5（PR3 #208 指摘 11）: 動的引数（project パス名 /
    # variant / backend / audio 相対パス）は project_dir 自体や builds_root の
    # 命名次第で空白等シェル特殊文字を含み得る。全動的引数を `shlex.join` で
    # クオートし、copy-paste でそのまま実行可能なコマンド文字列にする
    # （`tests/test_recast_backend.py:test_next_command_txt_advertises_a_real_working_ingest_command`
    # が実際に `shlex.split` → `CliRunner` で実行して検証する契約と対称）。
    command = shlex.join(
        [
            "svprpe",
            "recast",
            "ingest",
            project_relative,
            "--variant",
            prepared.variant,
            "--backend",
            prepared.backend_name,
            "--audio",
            audio_relative,
        ]
    )
    return f"{command}\n"


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
    # 注文書内の相対パス（project 引数・`--audio`）の前提 cwd を明示する
    # （Codex P2 eighth round #207 指摘15: manual.py:118 — 前提 cwd がどこにも
    # 明示されておらず、注文書だけを見た運用者が誤った場所で `next_command.txt`
    # を実行すると相対パス解決に失敗した）。order_dir から project_dir への
    # 相対パスは checkout-stable（builds_root は project_dir 配下に confine
    # 済み — loader `_resolve_builds_root`）なので絶対パスを焼き込まずに済む。
    cwd_from_order_dir = os.path.relpath(ctx.loaded.project_dir, prepared.order_dir)

    lines: list[str] = [
        f"# Recast order sheet: {prepared.variant}@{prepared.backend_name}",
        "",
        f"- generator: {prepared.generator}",
        f"- invocation_mode: {prepared.invocation_mode}",
        "",
        "## 作業ディレクトリ（cwd）",
        "",
        "このファイルおよび `next_command.txt` 内の相対パス（project 引数・"
        "`--audio`）はすべて **project.yaml のあるディレクトリ**（この注文書"
        f"（`{prepared.order_dir.name}/`）から見て `{cwd_from_order_dir}`）を"
        "基準にしている。以下のコマンドは必ずそのディレクトリで実行すること。",
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
        "project ディレクトリ（上記「作業ディレクトリ」参照）へ移動したうえで"
        "以下のコマンドで取り込んでください:",
        "",
        "```",
        f"cd {cwd_from_order_dir}  # この注文書ディレクトリから project.yaml のあるディレクトリへ",
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
            "lyrics.txt": _lyrics_text(prepared),
            "section_tags.txt": _section_tags_text(prepared),
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
        #
        # `atomic_publish_bytes_bundle`（BaseException-safe rollback、PR3
        # #208 指摘 14）で公開する — 従来の `_publish_artifacts_atomically`
        # は rename フェーズの rollback が `except OSError` のみで、
        # KeyboardInterrupt/SystemExit 系だと 6 ファイルの一部だけが更新
        # された状態が残り得た（指摘17）。同関数は bytes 契約のため、text
        # コンテンツは呼び出し側で 1 回だけ encode する（pr2 abc2350 と同じ
        # 単一 encode 原則 — hash 突合の対象ではないここでも一貫させる）。
        atomic_publish_bytes_bundle(
            prepared.order_dir,
            {name: content.encode("utf-8") for name, content in contents.items()},
            protected_inputs=prepared.protected_input_paths,
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
