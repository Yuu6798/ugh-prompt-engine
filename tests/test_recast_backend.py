"""`recast/backend.py` + `recast/backends/*` テスト（PR3）。"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from svp_rpe.recast import RecastError, load_recast_project
from svp_rpe.recast.backend import (
    RecastBackendUnavailable,
    build_recast_run_context,
    resolve_invoker,
    run_context_from_plan_artifacts,
)
from svp_rpe.recast.backends.deterministic import DeterministicInvoker
from svp_rpe.recast.backends.manual import ManualInvoker
from svp_rpe.recast.backends.musicgen import MusicgenInvoker
from svp_rpe.recast.plan import build_recast_plan_artifacts

DEMO_PROJECT = Path("examples/recast/demo_project")
EXPECTED_ORDERS = DEMO_PROJECT / "expected" / "orders" / "edm@suno"


def _copy_demo_project(tmp_path: Path) -> Path:
    """`tests/test_recast_plan.py` と同じ working-copy 構築（`expected/` は除外）。"""
    dest = tmp_path / "demo_project"
    dest.mkdir()
    shutil.copy(DEMO_PROJECT / "project.yaml", dest / "project.yaml")
    shutil.copy(DEMO_PROJECT / "composition_score.yaml", dest / "composition_score.yaml")
    shutil.copy(DEMO_PROJECT / "identity.yaml", dest / "identity.yaml")
    shutil.copytree(DEMO_PROJECT / "identity", dest / "identity")
    shutil.copytree(DEMO_PROJECT / "arrangements", dest / "arrangements")
    return dest / "project.yaml"


def _add_target_backend_variant(project_path: Path, *, variant_name: str, target_backend: str) -> None:
    """既存 demo project の working copy に、`rendering.target_backend` を
    `target_backend` へ override する variant を追加する（`deterministic`/
    `musicgen` local backend の検証に共通で使う）。

    実装ノート（PR3 逸脱事項・詳細は project.yaml のコメント参照）: demo の
    composition_score.yaml は `rendering.target_backend: "external"` を宣言して
    おり、`package.py` の generator 一致検査（`profile.generator ==
    resolve_backend_descriptor(target_backend).profile_key`、strict 無関係に
    無条件）により、既存 "edm" variant に対しては capability_profile 側 generator
    が "suno" 以外の backend（deterministic/musicgen）は常に blocked_capability
    に到達する（target_backend が score-level の静的宣言であり backend 選択と
    独立、という既存アーキテクチャの制約）。local backend を実際に
    compiled/verified まで機能させて検証するため、この専用 variant だけ
    target_backend を上書きする。
    """
    project_dir = project_path.parent
    arrangements_dir = project_dir / "arrangements"
    edm_arrangement = (arrangements_dir / "edm.yaml").read_text(encoding="utf-8")
    overridden = edm_arrangement.replace(
        "target:\n  semantic:",
        f'target:\n  rendering:\n    target_backend: "{target_backend}"\n  semantic:',
        1,
    )
    assert overridden != edm_arrangement  # sanity: replacement matched
    overridden = overridden.replace(
        "preservation:\n  score_fields:\n",
        "preservation:\n  score_fields:\n    rendering.target_backend: free\n",
        1,
    )
    (arrangements_dir / f"{variant_name}.yaml").write_text(overridden, encoding="utf-8")

    project_text = project_path.read_text(encoding="utf-8")
    updated = project_text.replace(
        "variants:\n  edm:\n    arrangement: arrangements/edm.yaml\n",
        "variants:\n  edm:\n    arrangement: arrangements/edm.yaml\n"
        f"  {variant_name}:\n    arrangement: arrangements/{variant_name}.yaml\n",
        1,
    )
    assert f"{variant_name}:" in updated  # sanity: replacement matched
    project_path.write_text(updated, encoding="utf-8")


def _prepare_manual(project_path: Path):
    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm", backend="suno")
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    assert isinstance(invoker, ManualInvoker)
    prepared = invoker.prepare(ctx)
    return loaded, invoker, prepared


# --- manual: order file content -----------------------------------------------


def test_manual_order_files_match_committed_snapshot(tmp_path: Path) -> None:
    """全 6 ファイルを byte-pin する — `prompt.json`/`expected_artifacts.json` の
    `content_digest`/`package_sha256` も含む。以前はこれらを正規化して除外していた
    （`plan.py` が `tempfile.TemporaryDirectory()` を package の一時置き場に使い、
    `artifact_base.locator` が呼び出し環境のテンポラリパス深さに依存し
    package_sha256/content_digest が非決定だったため）が、Codex P2 review
    （PR3 #208 指摘 3）で `plan.py` が `<builds_root>/packages/<variant>@<backend>/`
    へ package/report を永続公開するよう修正され、`artifact_base.locator` は
    project_dir 配下の固定深さのみに依存する checkout-stable な値になった —
    正規化 workaround は不要になったため撤去した。"""
    project_path = _copy_demo_project(tmp_path)
    _loaded, _invoker, prepared = _prepare_manual(project_path)

    for name in (
        "prompt.json",
        "lyrics.txt",
        "section_tags.txt",
        "order_sheet.md",
        "expected_artifacts.json",
        "next_command.txt",
    ):
        actual = (prepared.order_dir / name).read_text(encoding="utf-8")
        expected = (EXPECTED_ORDERS / name).read_text(encoding="utf-8")
        assert actual == expected, f"order file drift: {name}"


_TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def test_manual_order_files_have_no_timestamps_or_absolute_paths(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _loaded, _invoker, prepared = _prepare_manual(project_path)

    for name in (
        "prompt.json",
        "lyrics.txt",
        "section_tags.txt",
        "order_sheet.md",
        "expected_artifacts.json",
        "next_command.txt",
    ):
        content = (prepared.order_dir / name).read_text(encoding="utf-8")
        assert not _TIMESTAMP_PATTERN.search(content), f"{name} leaks a timestamp"
        assert str(tmp_path) not in content, f"{name} leaks an absolute path"
        assert str(prepared.order_dir) not in content, f"{name} leaks an absolute path"


def test_manual_order_files_are_byte_identical_across_reruns(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _loaded, _invoker, prepared_first = _prepare_manual(project_path)
    first = {
        name: (prepared_first.order_dir / name).read_bytes()
        for name in ("prompt.json", "order_sheet.md", "next_command.txt")
    }

    _loaded2, _invoker2, prepared_second = _prepare_manual(project_path)
    for name, expected_bytes in first.items():
        assert (prepared_second.order_dir / name).read_bytes() == expected_bytes


def test_next_command_txt_advertises_a_real_working_ingest_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """機械 assert: `next_command.txt`/`order_sheet.md` が案内するコマンドが
    実在し、実際に成功することを検証する（Codex P2 review round 2 指摘 5:
    従来は `svprpe recast ingest` が未実装のまま案内されていた）。

    `next_command.txt` を一切解釈・書き換えせず、そのままシェルへ渡せる文字列
    として `shlex.split` するだけで CLI 起動引数を得て実行する — 文面が実
    コマンドと僅かでもずれていれば exit_code != 0 で検出できる。`svprpe recast
    run`（CLI）で publish させる — `ingest` は `recast_state.json` の
    `awaiting_generation` + `recast_plan.json` の存在を前提とするため、API 直呼び
    の `_prepare_manual` ではその前提が揃わない。
    """
    import shlex

    from typer.testing import CliRunner

    from svp_rpe.cli import app

    project_path = _copy_demo_project(tmp_path)
    runner = CliRunner()
    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    order_dir = project_path.parent / "builds" / "orders" / "edm@suno"
    next_command = (order_dir / "next_command.txt").read_text(encoding="utf-8").strip()
    order_sheet = (order_dir / "order_sheet.md").read_text(encoding="utf-8")
    assert next_command in order_sheet  # order_sheet.md のコードブロックと一致

    tokens = shlex.split(next_command)
    assert tokens[0] == "svprpe"
    argv = tokens[1:]

    audio_relative = argv[argv.index("--audio") + 1]
    audio_path = project_path.parent / audio_relative
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    # next_command.txt のパス（project 引数・--audio）はいずれも project_dir
    # 相対 — 実運用同様、project_dir を cwd にしてから実行する。
    monkeypatch.chdir(project_path.parent)
    result = runner.invoke(app, argv)

    assert result.exit_code == 0, result.output
    from svp_rpe.recast.state import load_recast_state

    state_file = load_recast_state(project_path.parent)
    assert state_file.runs["edm@suno"].state == "generated"


def test_next_command_txt_is_shell_quoted_for_project_filename_with_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 review round 5（PR3 #208 指摘 11）: `next_command.txt` の
    `project_relative`（`ctx.loaded.path.name` — CLI にユーザーが渡した
    project YAML のファイル名そのまま）が空白等を含む場合、未クオートだと
    copy-paste 実行時にシェルがトークンを分割してしまい実行不能になる。
    project YAML を `my project.yaml`（空白入りファイル名）として配置し、
    `shlex.split(next_command)` が単一トークンへ正しく分割され、実際に
    `svprpe recast ingest` として成功することを検証する（`tmp_path` 直下に
    空白入りディレクトリを作る代わりに、より直接的な発生源であるファイル名
    自体に空白を持たせる — 空白はディレクトリ名でもファイル名でも同じ
    トークン分割問題を起こすため等価な回帰形態）。"""
    import shlex

    from typer.testing import CliRunner

    from svp_rpe.cli import app

    dest = tmp_path / "demo_project"
    dest.mkdir()
    shutil.copy(DEMO_PROJECT / "project.yaml", dest / "my project.yaml")
    shutil.copy(DEMO_PROJECT / "composition_score.yaml", dest / "composition_score.yaml")
    shutil.copy(DEMO_PROJECT / "identity.yaml", dest / "identity.yaml")
    shutil.copytree(DEMO_PROJECT / "identity", dest / "identity")
    shutil.copytree(DEMO_PROJECT / "arrangements", dest / "arrangements")
    project_path = dest / "my project.yaml"

    runner = CliRunner()
    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    order_dir = dest / "builds" / "orders" / "edm@suno"
    next_command = (order_dir / "next_command.txt").read_text(encoding="utf-8").strip()
    order_sheet = (order_dir / "order_sheet.md").read_text(encoding="utf-8")
    assert next_command in order_sheet

    # 未クオートのままなら "my" と "project.yaml" の 2 トークンに割れてしまう —
    # shlex.split で 1 トークンに正しく戻ることを機械的に確認する。
    tokens = shlex.split(next_command)
    assert tokens[:3] == ["svprpe", "recast", "ingest"]
    project_token = tokens[3]
    assert project_token == "my project.yaml"

    argv = tokens[1:]
    audio_relative = argv[argv.index("--audio") + 1]
    audio_path = dest / audio_relative
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    monkeypatch.chdir(dest)
    result = runner.invoke(app, argv)

    assert result.exit_code == 0, result.output
    from svp_rpe.recast.state import load_recast_state

    state_file = load_recast_state(dest)
    assert state_file.runs["edm@suno"].state == "generated"


def test_order_sheet_cd_line_is_shell_quoted_for_project_filename_with_space(
    tmp_path: Path,
) -> None:
    """Codex P2 review round 14（PR3 #208 — #210 側にも同内容の指摘）:
    `order_sheet.md` のコマンドブロック先頭 `cd <cwd_from_order_dir>` が
    未クオートだった — `_next_command_text`（指摘11 で対応済み）の
    `--audio`/project 引数と同じコマンドブロック内にある隣接行なのに、
    そちらだけ `shlex.join` によるクオートが漏れていた。`shlex.join(["cd",
    cwd_from_order_dir])` でクオートし、空白入り project ファイル名の
    project で `order_sheet.md` の `cd` 行が `shlex.split` で厳密に 2
    トークン（`["cd", <単一パス>]`）に戻ることを検証する。"""
    import shlex

    dest = tmp_path / "demo_project"
    dest.mkdir()
    shutil.copy(DEMO_PROJECT / "project.yaml", dest / "my project.yaml")
    shutil.copy(DEMO_PROJECT / "composition_score.yaml", dest / "composition_score.yaml")
    shutil.copy(DEMO_PROJECT / "identity.yaml", dest / "identity.yaml")
    shutil.copytree(DEMO_PROJECT / "identity", dest / "identity")
    shutil.copytree(DEMO_PROJECT / "arrangements", dest / "arrangements")
    project_path = dest / "my project.yaml"

    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm", backend="suno")
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    prepared = invoker.prepare(ctx)

    order_sheet = (prepared.order_dir / "order_sheet.md").read_text(encoding="utf-8")
    cd_lines = [line for line in order_sheet.splitlines() if line.startswith("cd ")]
    assert len(cd_lines) == 1
    # 末尾のシェルコメント（`  # ...`）はコマンド本体ではないため
    # shlex.split の対象から外す。
    command_part = cd_lines[0].split("  #", 1)[0]
    tokens = shlex.split(command_part)
    assert tokens[0] == "cd"
    assert len(tokens) == 2  # ["cd", <単一パス>] — 空白でトークン分割されない


# --- manual: multi-anchor channel artifact rendering ----------------------------


def _add_second_lyrics_anchor(project_path: Path, *, content: bytes) -> None:
    """demo project の identity manifest に 2 件目の lyrics anchor
    （id=`lyrics_alt`）を追加する（Codex P2 ninth round #207 指摘18 の
    回帰テスト用フィクスチャ構築）。`identity/lyrics_alt.txt` を書き、
    identity.yaml の anchors・arrangements/edm.yaml の
    preservation.identity_anchors 両方に登録する（`build_preservation_
    contract` は `required: true` な anchor 全てに policy を要求する既存
    契約 — demo project の他 3 anchor と同じ規約）。"""
    import hashlib

    project_dir = project_path.parent
    artifact_path = project_dir / "identity" / "lyrics_alt.txt"
    artifact_path.write_bytes(content)
    sha256 = hashlib.sha256(content).hexdigest()

    identity_path = project_dir / "identity.yaml"
    identity_text = identity_path.read_text(encoding="utf-8")
    anchor_block = (
        "  - id: lyrics_alt\n"
        "    domain: lyrics\n"
        "    artifact: identity/lyrics_alt.txt\n"
        "    artifact_type: lyrics_text\n"
        "    media_type: text/plain\n"
        f'    sha256: "{sha256}"\n'
        "    required: true\n"
    )
    # 既存の "lyrics" anchor 直後（"melody" anchor の手前）へ挿入する —
    # 宣言順 = lyrics → lyrics_alt → melody → harmony となり、レンダリング順が
    # 宣言順を保存しているかどうかを意味のある形で検証できる。
    updated_identity = identity_text.replace(
        "    required: true\n  - id: melody\n",
        "    required: true\n" + anchor_block + "  - id: melody\n",
        1,
    )
    assert updated_identity != identity_text  # sanity
    identity_path.write_text(updated_identity, encoding="utf-8")

    arrangement_path = project_dir / "arrangements" / "edm.yaml"
    arrangement_text = arrangement_path.read_text(encoding="utf-8")
    updated_arrangement = arrangement_text.replace(
        "  identity_anchors:\n",
        "  identity_anchors:\n    lyrics_alt:\n      mode: hard\n      allow: []\n",
        1,
    )
    assert updated_arrangement != arrangement_text  # sanity
    arrangement_path.write_text(updated_arrangement, encoding="utf-8")


def test_lyrics_text_renders_all_delivered_lyrics_anchors(tmp_path: Path) -> None:
    """Codex P2 review round 9（PR3 #208 指摘18）: `build_performance_package`
    は preserve される全 lyrics anchor を `channel_artifacts['lyrics_text']`
    へ記録するが、`_lyrics_text` は従来 `refs[0]` のみを読んでおり、2 件目
    以降の lyrics anchor（plan/package は delivered と報告している）を
    黙って `lyrics.txt` から落としていた。2 件の lyrics anchor を preserve
    する project で `lyrics.txt` に両方の内容が現れる（package 内の順を
    保存し、複数件なので anchor_id 見出し付きで連結される）ことを検証する。"""
    project_path = _copy_demo_project(tmp_path)
    _add_second_lyrics_anchor(
        project_path, content=b"Second lyric anchor content.\nLine two.\n"
    )

    _loaded, _invoker, prepared = _prepare_manual(project_path)

    lyrics_text = (prepared.order_dir / "lyrics.txt").read_text(encoding="utf-8")
    assert "# lyrics\n" in lyrics_text
    assert "# lyrics_alt\n" in lyrics_text
    assert "Second lyric anchor content." in lyrics_text
    # 元の lyrics anchor の本文も引き続き含まれる。
    original_lyrics = (project_path.parent / "identity" / "lyrics.txt").read_text(
        encoding="utf-8"
    )
    assert original_lyrics.splitlines()[0] in lyrics_text
    # package 内の順（identity.yaml の宣言順 = lyrics → lyrics_alt）を保存する。
    assert lyrics_text.index("# lyrics\n") < lyrics_text.index("# lyrics_alt\n")


def _add_section_map_v2_anchor(project_path: Path, *, sections: list[tuple[str, str]]) -> None:
    """demo project の identity manifest へ `section-map/0.2` structure anchor
    （`domain="structure"`, `artifact_type="section_map"`）を追加する（Codex P2
    ninth round #207 指摘20 の回帰テスト用フィクスチャ構築 — `_add_second_
    lyrics_anchor` と同型の手順）。"""
    import hashlib
    import json

    project_dir = project_path.parent
    payload = {
        "schema_version": "section-map/0.2",
        "sections": [{"id": entry_id, "label": label} for entry_id, label in sections],
    }
    content = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    artifact_path = project_dir / "identity" / "section_tags.json"
    artifact_path.write_bytes(content)
    sha256 = hashlib.sha256(content).hexdigest()

    identity_path = project_dir / "identity.yaml"
    identity_text = identity_path.read_text(encoding="utf-8")
    anchor_block = (
        "  - id: structure_tags\n"
        "    domain: structure\n"
        "    artifact: identity/section_tags.json\n"
        "    artifact_type: section_map\n"
        "    media_type: application/json\n"
        '    format_version: "section-map/0.2"\n'
        f'    sha256: "{sha256}"\n'
        "    required: true\n"
    )
    updated_identity = identity_text.replace(
        "    required: true\n  - id: melody\n",
        "    required: true\n" + anchor_block + "  - id: melody\n",
        1,
    )
    assert updated_identity != identity_text  # sanity
    identity_path.write_text(updated_identity, encoding="utf-8")

    arrangement_path = project_dir / "arrangements" / "edm.yaml"
    arrangement_text = arrangement_path.read_text(encoding="utf-8")
    updated_arrangement = arrangement_text.replace(
        "  identity_anchors:\n",
        "  identity_anchors:\n    structure_tags:\n      mode: hard\n      allow: []\n",
        1,
    )
    assert updated_arrangement != arrangement_text  # sanity
    arrangement_path.write_text(updated_arrangement, encoding="utf-8")


def test_section_tags_text_prefers_delivered_channel_artifact_over_prompt(
    tmp_path: Path,
) -> None:
    """Codex P2 review round 9（PR3 #208 指摘20）: `_section_tags_text` は従来
    `prompt.section_tags`（derived score 由来の advisory 値）を無条件に先に
    採用しており、`channel_artifacts['section_tags']` に実際に delivered な
    section_map（hard-preservation の実体）があってもそちらを一切読まなかった
    — plan は delivered と報告するのに manual generator へ渡る
    `section_tags.txt` には反映されない、指摘18 と同型の欠落だった。
    delivered な section-map/0.2 anchor を preserve する project で
    `section_tags.txt` がその artifact 由来の内容になり、
    `prompt.section_tags`（demo デフォルトの `[Intro: ...]` 形式）ではない
    ことを検証する。"""
    project_path = _copy_demo_project(tmp_path)
    _add_section_map_v2_anchor(
        project_path, sections=[("intro", "Intro"), ("drop", "Drop")]
    )

    _loaded, _invoker, prepared = _prepare_manual(project_path)
    assert "section_tags" in prepared.package.channel_artifacts  # delivered 前提の sanity

    section_tags_text = (prepared.order_dir / "section_tags.txt").read_text(
        encoding="utf-8"
    )
    assert section_tags_text == "intro:Intro, drop:Drop\n"
    # 従来の prompt.section_tags 由来のプレースホルダ形式ではないことを確認。
    assert "[Intro:" not in section_tags_text


def test_lyrics_text_uses_plan_pinned_bytes_not_disk_reread_at_render_time(
    tmp_path: Path,
) -> None:
    """Codex P2 review round 11（PR3 #208 指摘22）: `_lyrics_text` は従来
    `resolve_confined(...).read_text()` で注文書描画のたびに artifact を
    disk から再 read しており、plan 段（`build_recast_plan_artifacts`）の
    hash 検証と実際に描画される中身が乖離し得る TOCTOU があった。plan
    構築後・`prepare()` 呼び出し前に artifact ファイルの中身を書き換えても、
    `lyrics.txt` には plan 段が pin した（書き換え前の）bytes がそのまま
    反映されることを検証する。"""
    project_path = _copy_demo_project(tmp_path)
    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm", backend="suno")

    lyrics_path = project_path.parent / "identity" / "lyrics.txt"
    original_text = lyrics_path.read_text(encoding="utf-8")
    lyrics_path.write_bytes(b"MUTATED AFTER PLAN BUILD, BEFORE RENDER\n")

    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    prepared = invoker.prepare(ctx)

    lyrics_text = (prepared.order_dir / "lyrics.txt").read_text(encoding="utf-8")
    assert "MUTATED AFTER PLAN BUILD" not in lyrics_text
    assert lyrics_text == (
        original_text if original_text.endswith("\n") else original_text + "\n"
    )


def _add_malformed_section_map_anchor(project_path: Path, *, content: bytes) -> None:
    """指摘23 回帰テスト用フィクスチャ: sha256 は content と一致する（plan 段の
    hash 検証は通る）が、section-map v0.1/v0.2 いずれの schema にも parse
    できない bytes を持つ section_tags anchor を追加する。`format_version` は
    宣言しない — 宣言すると（`section-map/0.2` の場合）plan.py 側の
    `_is_section_map_v2_structure_anchor`（AR2-3 の `section_ref` merge
    resolution）がこの anchor を先に parse し、plan 段で blocked_verification
    として弾かれてしまい `ManualInvoker.prepare()` まで到達しない
    （= 本テストが検証したい manual.py 側の fail-closed 経路に届かない）。"""
    import hashlib

    project_dir = project_path.parent
    artifact_path = project_dir / "identity" / "section_tags_broken.json"
    artifact_path.write_bytes(content)
    sha256 = hashlib.sha256(content).hexdigest()

    identity_path = project_dir / "identity.yaml"
    identity_text = identity_path.read_text(encoding="utf-8")
    anchor_block = (
        "  - id: structure_tags\n"
        "    domain: structure\n"
        "    artifact: identity/section_tags_broken.json\n"
        "    artifact_type: section_map\n"
        "    media_type: application/json\n"
        f'    sha256: "{sha256}"\n'
        "    required: true\n"
    )
    updated_identity = identity_text.replace(
        "    required: true\n  - id: melody\n",
        "    required: true\n" + anchor_block + "  - id: melody\n",
        1,
    )
    assert updated_identity != identity_text  # sanity
    identity_path.write_text(updated_identity, encoding="utf-8")

    arrangement_path = project_dir / "arrangements" / "edm.yaml"
    arrangement_text = arrangement_path.read_text(encoding="utf-8")
    updated_arrangement = arrangement_text.replace(
        "  identity_anchors:\n",
        "  identity_anchors:\n    structure_tags:\n      mode: hard\n      allow: []\n",
        1,
    )
    assert updated_arrangement != arrangement_text  # sanity
    arrangement_path.write_text(updated_arrangement, encoding="utf-8")


def test_section_tags_text_fails_closed_when_delivered_section_map_is_unparseable(
    tmp_path: Path,
) -> None:
    """Codex P2 review round 11（PR3 #208 指摘23）: 配送済み
    `channel_artifacts['section_tags']` ref が section-map v0.1/v0.2 いずれの
    schema にも parse できない場合、従来は `_channel_artifact_refs_section_
    tags_text` が黙って `None` を返し、呼び出し側が `prompt.section_tags`/
    placeholder へ silent fallback していた — plan/package は delivered と
    報告している hard anchor の実体が、気づかれないまま注文書から欠落する
    事故を招く。1 件も解釈できない場合は `RecastError` で fail-closed し、
    注文書（6 ファイルいずれも）を一切公開しないことを検証する。"""
    project_path = _copy_demo_project(tmp_path)
    _add_malformed_section_map_anchor(project_path, content=b"not valid json at all")

    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm", backend="suno")
    assert "section_tags" in ctx.compiled.package.channel_artifacts  # delivered 前提の sanity
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)

    with pytest.raises(RecastError, match="structure_tags"):
        invoker.prepare(ctx)

    order_dir = loaded.builds_root / "orders" / "edm@suno"
    assert not order_dir.exists()


def _add_one_valid_and_one_broken_section_map_anchor(project_path: Path) -> None:
    """指摘25 回帰テスト用フィクスチャ: 2 件の delivered section_tags anchor を
    追加する — 1 件は正常な `section-map/0.2`（id=`structure_tags_ok`）、もう
    1 件は sha256 は正しいが schema として parse 不能（id=`structure_tags_
    broken`、`_add_malformed_section_map_anchor` と同じ理由で `format_version`
    は宣言しない）。"""
    import hashlib
    import json

    project_dir = project_path.parent

    ok_payload = {
        "schema_version": "section-map/0.2",
        "sections": [{"id": "intro", "label": "Intro"}, {"id": "drop", "label": "Drop"}],
    }
    ok_content = (json.dumps(ok_payload, ensure_ascii=False) + "\n").encode("utf-8")
    ok_artifact_path = project_dir / "identity" / "section_tags_ok.json"
    ok_artifact_path.write_bytes(ok_content)
    ok_sha256 = hashlib.sha256(ok_content).hexdigest()

    broken_content = b"not valid json at all"
    broken_artifact_path = project_dir / "identity" / "section_tags_broken.json"
    broken_artifact_path.write_bytes(broken_content)
    broken_sha256 = hashlib.sha256(broken_content).hexdigest()

    identity_path = project_dir / "identity.yaml"
    identity_text = identity_path.read_text(encoding="utf-8")
    anchor_block = (
        "  - id: structure_tags_ok\n"
        "    domain: structure\n"
        "    artifact: identity/section_tags_ok.json\n"
        "    artifact_type: section_map\n"
        "    media_type: application/json\n"
        '    format_version: "section-map/0.2"\n'
        f'    sha256: "{ok_sha256}"\n'
        "    required: true\n"
        "  - id: structure_tags_broken\n"
        "    domain: structure\n"
        "    artifact: identity/section_tags_broken.json\n"
        "    artifact_type: section_map\n"
        "    media_type: application/json\n"
        f'    sha256: "{broken_sha256}"\n'
        "    required: true\n"
    )
    updated_identity = identity_text.replace(
        "    required: true\n  - id: melody\n",
        "    required: true\n" + anchor_block + "  - id: melody\n",
        1,
    )
    assert updated_identity != identity_text  # sanity
    identity_path.write_text(updated_identity, encoding="utf-8")

    arrangement_path = project_dir / "arrangements" / "edm.yaml"
    arrangement_text = arrangement_path.read_text(encoding="utf-8")
    updated_arrangement = arrangement_text.replace(
        "  identity_anchors:\n",
        "  identity_anchors:\n"
        "    structure_tags_ok:\n      mode: hard\n      allow: []\n"
        "    structure_tags_broken:\n      mode: hard\n      allow: []\n",
        1,
    )
    assert updated_arrangement != arrangement_text  # sanity
    arrangement_path.write_text(updated_arrangement, encoding="utf-8")


def test_section_tags_text_fails_closed_when_one_of_several_delivered_refs_is_unparseable(
    tmp_path: Path,
) -> None:
    """Codex P2 review round 12（PR3 #208 指摘25）: round 11 の指摘23 修正は
    「delivered な section_tags ref が**全滅**したときだけ」fail-closed して
    おり、複数 delivered ref のうち一部だけが parse 不能な場合はその ref
    だけを黙って読み飛ばし、残りの ref だけで「不完全だが動く」注文書を
    出していた。2 件の delivered section_tags anchor（1 件は正常な
    section-map/0.2、もう 1 件は schema として parse 不能）を preserve する
    project で、`prepare()` が `RecastError` を送出し（正常な 1 件があっても
    公開を拒否し）、注文書ディレクトリが一切作られないことを検証する。"""
    project_path = _copy_demo_project(tmp_path)
    _add_one_valid_and_one_broken_section_map_anchor(project_path)

    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm", backend="suno")
    section_tags_refs = ctx.compiled.package.channel_artifacts.get("section_tags", [])
    assert {ref.anchor_id for ref in section_tags_refs} == {
        "structure_tags_ok",
        "structure_tags_broken",
    }  # 2 件とも delivered 前提の sanity
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)

    with pytest.raises(RecastError, match="structure_tags_broken"):
        invoker.prepare(ctx)

    order_dir = loaded.builds_root / "orders" / "edm@suno"
    assert not order_dir.exists()


# --- manual: cover mode branch --------------------------------------------------


def test_manual_cover_mode_order_sheet_references_identity_source(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    arrangement_path = project_path.parent / "arrangements" / "edm.yaml"
    original = arrangement_path.read_text(encoding="utf-8")
    mutated = original.replace(
        "  physical:\n    bpm: 132\n    brightness: \"bright\"\n",
        "  physical:\n    bpm: 132\n    brightness: \"bright\"\n"
        '    time_signature: "3/4"\n',
        1,
    )
    assert mutated != original  # sanity
    mutated = mutated.replace(
        "preservation:\n  score_fields:\n",
        "preservation:\n  score_fields:\n    physical.time_signature: free\n",
        1,
    )
    arrangement_path.write_text(mutated, encoding="utf-8")

    project_text = project_path.read_text(encoding="utf-8")
    project_text = project_text.replace("invocation_mode: prompt_only", "invocation_mode: cover")
    project_path.write_text(project_text, encoding="utf-8")

    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm", backend="suno")
    assert ctx.backend_ref.invocation_mode == "cover"
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    prepared = invoker.prepare(ctx)

    order_sheet = (prepared.order_dir / "order_sheet.md").read_text(encoding="utf-8")
    assert "cover" in order_sheet
    assert prepared.identity_source_locator in order_sheet
    assert prepared.identity_source_sha256 in order_sheet
    assert "physical.time_signature" in order_sheet
    assert "unsupported" in order_sheet  # cover の time_signature override は届かない実測


def test_manual_cover_mode_order_sheet_uses_plan_pinned_identity_source_not_disk_reread(
    tmp_path: Path,
) -> None:
    """Codex P2 review round 12（PR3 #208 指摘26）: `base_prepared_invocation`
    は従来 `read_identity_source(ctx.loaded)` で注文書描画のたびに
    `identity.yaml` を再 read/再 parse しており、plan 段（`build_recast_
    plan_artifacts`）の hash 検証・診断が前提とした source メタデータと
    実際に注文書へ書かれる中身が乖離し得る TOCTOU があった。plan 構築後・
    `prepare()` 呼び出し前に `identity.yaml` の `source.locator`/`source.
    sha256` を書き換えても、`order_sheet.md` には plan 段が pin した
    （書き換え前の）値がそのまま反映されることを検証する。"""
    project_path = _copy_demo_project(tmp_path)
    project_text = project_path.read_text(encoding="utf-8")
    project_text = project_text.replace("invocation_mode: prompt_only", "invocation_mode: cover")
    project_path.write_text(project_text, encoding="utf-8")

    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm", backend="suno")
    assert ctx.backend_ref.invocation_mode == "cover"
    original_locator = ctx.identity_source_locator
    original_sha256 = ctx.identity_source_sha256

    identity_path = project_path.parent / "identity.yaml"
    identity_text = identity_path.read_text(encoding="utf-8")
    mutated = identity_text.replace(
        f'locator: {original_locator}\n  sha256: "{original_sha256}"',
        'locator: mutated_after_plan_build.yaml\n  sha256: "' + ("f" * 64) + '"',
        1,
    )
    assert mutated != identity_text  # sanity: replacement matched
    identity_path.write_text(mutated, encoding="utf-8")

    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    prepared = invoker.prepare(ctx)

    order_sheet = (prepared.order_dir / "order_sheet.md").read_text(encoding="utf-8")
    assert original_locator in order_sheet
    assert original_sha256 in order_sheet
    assert "mutated_after_plan_build.yaml" not in order_sheet
    assert "f" * 64 not in order_sheet


# --- manual: invoke/collect contract --------------------------------------------


def test_manual_invoke_raises_recast_error(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _loaded, invoker, prepared = _prepare_manual(project_path)

    with pytest.raises(RecastError):
        invoker.invoke(prepared)


def test_manual_collect_rejects_missing_file(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _loaded, invoker, prepared = _prepare_manual(project_path)

    with pytest.raises(RecastError):
        invoker.collect(prepared, tmp_path / "does-not-exist.wav")


def test_manual_collect_rejects_unaccepted_extension(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _loaded, invoker, prepared = _prepare_manual(project_path)
    bogus = tmp_path / "take.flac"
    bogus.write_bytes(b"not really audio")

    with pytest.raises(RecastError):
        invoker.collect(prepared, bogus)


def test_manual_collect_writes_take_and_take_json(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _loaded, invoker, prepared = _prepare_manual(project_path)
    supplied = tmp_path / "external_take.wav"
    supplied.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    take = invoker.collect(prepared, supplied)

    assert take.source == "manual"
    assert take.audio_path == prepared.takes_dir / "take-01.wav"
    assert take.audio_path.read_bytes() == supplied.read_bytes()

    import hashlib

    assert take.sha256 == hashlib.sha256(supplied.read_bytes()).hexdigest()

    take_json = json.loads((prepared.takes_dir / "take.json").read_text(encoding="utf-8"))
    assert take_json["sha256"] == take.sha256
    assert take_json["source"] == "manual"
    assert take_json["backend_name"] == "suno"


def test_manual_collect_take_json_failure_leaves_no_orphaned_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 review（PR3 #208 指摘 1）: `take-01.wav` を先に最終名へ publish
    してから `take.json` を書くと、後者が失敗したときに provenance の無い音声
    だけが `takes_dir` に残ってしまう。`atomic_publish_bytes_bundle` は両方を
    staging してから publish するため、`take.json` 側の最終 rename を失敗
    注入しても `take-01.wav` が残らないことを検証する。"""
    import os

    project_path = _copy_demo_project(tmp_path)
    _loaded, invoker, prepared = _prepare_manual(project_path)
    supplied = tmp_path / "external_take.wav"
    supplied.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    real_replace = os.replace

    def flaky_replace(src: object, dst: object) -> None:
        if str(dst).endswith("take.json"):
            raise OSError("simulated disk full while publishing take.json")
        real_replace(src, dst)

    monkeypatch.setattr("os.replace", flaky_replace)

    with pytest.raises(OSError):
        invoker.collect(prepared, supplied)

    assert not (prepared.takes_dir / "take-01.wav").exists()
    assert not (prepared.takes_dir / "take.json").exists()


class _InjectedBaseException(BaseException):
    """`Exception` を継承しない注入用テスト例外（`KeyboardInterrupt`/
    `SystemExit` と同じ `BaseException` 直下の系統を模す — PR3 #208 指摘 14）。"""


def test_manual_collect_take_json_base_exception_during_rename_still_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 review round 7（PR3 #208 指摘 14）: `atomic_publish_bytes_bundle`
    の rename フェーズが従来 `except OSError` のみだったため、rename 中に
    `KeyboardInterrupt`/`SystemExit` 系（`Exception` を継承しない
    `BaseException`）が飛ぶと rollback を素通りし、snapshot ごと失われた
    「半端に publish 済み」の状態が残り得た。`os.replace` の `take.json` 側
    最終 rename に `Exception` を継承しない例外を注入し、それでも
    `take-01.wav`/`take.json` が残らない（rollback が効く）ことを検証する。"""
    import os

    project_path = _copy_demo_project(tmp_path)
    _loaded, invoker, prepared = _prepare_manual(project_path)
    supplied = tmp_path / "external_take.wav"
    supplied.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    real_replace = os.replace

    def flaky_replace(src: object, dst: object) -> None:
        if str(dst).endswith("take.json"):
            raise _InjectedBaseException("simulated KeyboardInterrupt-like interruption")
        real_replace(src, dst)

    monkeypatch.setattr("os.replace", flaky_replace)

    with pytest.raises(_InjectedBaseException):
        invoker.collect(prepared, supplied)

    assert not (prepared.takes_dir / "take-01.wav").exists()
    assert not (prepared.takes_dir / "take.json").exists()


def test_manual_orders_publish_base_exception_during_rename_still_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 review round 9（PR3 #208 指摘17）: 注文書 6 ファイルの公開が
    `cli/builds_root.py:_publish_artifacts_atomically`（rename フェーズの
    rollback が `except OSError` のみ）に依存していたため、rename 中の
    `KeyboardInterrupt`/`SystemExit` 系で 6 ファイルの一部だけが更新された
    状態が残り得た。`prepare()` を先に 1 回成功させて 6 ファイルの baseline を
    作り、2 回目の `prepare()` で最後（辞書順で最後 = `order_sheet.md`）の
    最終 rename にだけ `Exception` を継承しない例外を注入する — snapshot
    フェーズは既存の 6 ファイル全てを退避済みのため、rollback が
    `except BaseException` で正しく機能していれば 6 ファイル全てが baseline
    と byte-identical に復元される（rollback が旧 `except OSError` のままなら
    一部ファイルが欠落する）。"""
    import os

    project_path = _copy_demo_project(tmp_path)
    loaded, invoker, prepared_first = _prepare_manual(project_path)

    order_filenames = (
        "prompt.json",
        "lyrics.txt",
        "section_tags.txt",
        "expected_artifacts.json",
        "next_command.txt",
        "order_sheet.md",
    )
    baseline = {
        name: (prepared_first.order_dir / name).read_bytes() for name in order_filenames
    }

    ctx = build_recast_run_context(loaded, variant="edm", backend="suno")
    real_replace = os.replace
    calls = {"failed_once": False}

    def flaky_replace(src: object, dst: object) -> None:
        if (
            str(dst).endswith("order_sheet.md")
            and not str(dst).endswith(".prev")
            and not calls["failed_once"]
        ):
            calls["failed_once"] = True
            raise _InjectedBaseException("simulated KeyboardInterrupt-like interruption")
        real_replace(src, dst)

    monkeypatch.setattr("os.replace", flaky_replace)

    with pytest.raises(_InjectedBaseException):
        invoker.prepare(ctx)

    for name, expected_bytes in baseline.items():
        actual_path = prepared_first.order_dir / name
        assert actual_path.is_file(), f"{name} missing after rollback"
        assert actual_path.read_bytes() == expected_bytes, f"{name} not restored to baseline"


# --- manual: orders publish collision guard --------------------------------------


def test_manual_orders_publish_rejects_mode_overrides_colliding_with_output(
    tmp_path: Path,
) -> None:
    """Codex P2 review（PR3 #208 指摘 2）: 従来の衝突ガードは
    project/score/identity_manifest/arrangement のみが対象で、project ローカルの
    capability_profile / mode_overrides は対象外だった。ここでは
    `backends.suno.mode_overrides` を orders 出力パス（`prompt.json`）の位置に
    直接置き、`prepare()` が publish 前に fail-closed で拒否する（かつ既存内容を
    上書きしない）ことを検証する。"""
    project_path = _copy_demo_project(tmp_path)
    project_dir = project_path.parent

    colliding_path = project_dir / "builds" / "orders" / "edm@suno" / "prompt.json"
    colliding_path.parent.mkdir(parents=True, exist_ok=True)
    original_mode_overrides = (
        Path("config/mode_overrides/suno.yaml").read_bytes()
    )
    colliding_path.write_bytes(original_mode_overrides)

    project_text = project_path.read_text(encoding="utf-8")
    updated = project_text.replace(
        'mode_overrides: "suno"',
        'mode_overrides: "builds/orders/edm@suno/prompt.json"',
        1,
    )
    assert updated != project_text  # sanity
    project_path.write_text(updated, encoding="utf-8")

    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm", backend="suno")
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)

    with pytest.raises(ValueError):
        invoker.prepare(ctx)

    # fail-closed: 衝突先の既存内容（mode_overrides YAML）が上書きされていない。
    assert colliding_path.read_bytes() == original_mode_overrides


# --- deterministic: takes 公開の衝突ガード ----------------------------------------


@pytest.mark.slow
def test_deterministic_invoke_rejects_capability_profile_colliding_with_takes_output(
    tmp_path: Path,
) -> None:
    """Codex P2 review round 3（PR3 #208 指摘 6）: `DeterministicInvoker.invoke()`
    の takes 公開が `protected_inputs` を一切渡しておらず、identity source /
    anchor artifact 等の入力が takes 出力パスにあると無警告で上書きし得た。
    ここでは `backends.deterministic.capability_profile` を takes 出力パス
    （`take-01.wav`）へ直接向け、`invoke()` が publish 前に fail-closed で拒否し
    （かつ既存内容を上書きしない）ことを検証する。"""
    project_path = _copy_demo_project(tmp_path)
    _add_target_backend_variant(
        project_path, variant_name="edm_deterministic", target_backend="deterministic"
    )
    project_dir = project_path.parent

    colliding_path = project_dir / "builds" / "takes" / "edm_deterministic@deterministic" / "take-01.wav"
    colliding_path.parent.mkdir(parents=True, exist_ok=True)
    original_capability_profile = Path("config/capability_profiles/deterministic.yaml").read_bytes()
    colliding_path.write_bytes(original_capability_profile)

    project_text = project_path.read_text(encoding="utf-8")
    updated = project_text.replace(
        'capability_profile: "deterministic"',
        'capability_profile: "builds/takes/edm_deterministic@deterministic/take-01.wav"',
        1,
    )
    assert updated != project_text  # sanity
    project_path.write_text(updated, encoding="utf-8")

    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm_deterministic", backend="deterministic")
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    assert isinstance(invoker, DeterministicInvoker)
    prepared = invoker.prepare(ctx)

    with pytest.raises(ValueError):
        invoker.invoke(prepared)

    # fail-closed: 衝突先の既存内容（capability_profile YAML）が上書きされていない。
    assert colliding_path.read_bytes() == original_capability_profile
    assert not (colliding_path.parent / "take.json").exists()


# --- plan: packages 公開の衝突ガード ----------------------------------------------


def test_build_recast_plan_rejects_capability_profile_colliding_with_packages_output(
    tmp_path: Path,
) -> None:
    """Codex P2 review round 3（PR3 #208 指摘 7）: `build_recast_plan_artifacts`
    の package/report 永続公開（`packages/<variant>@<backend>/`）が衝突ガードを
    一切持たず、`capability_profile`/`mode_overrides`/manifest anchor artifact
    等の入力がその出力パスにあると無警告で上書きし得た。ここでは
    `backends.suno.capability_profile` を packages 出力パス
    （`performance_package.json`）へ直接向け、`build_recast_plan_artifacts` が
    publish 前に fail-closed で `blocked_capability` へ倒す（かつ既存内容を
    上書きしない）ことを検証する。"""
    project_path = _copy_demo_project(tmp_path)
    project_dir = project_path.parent

    colliding_path = (
        project_dir / "builds" / "packages" / "edm@suno" / "performance_package.json"
    )
    colliding_path.parent.mkdir(parents=True, exist_ok=True)
    original_capability_profile = Path("config/capability_profiles/suno.yaml").read_bytes()
    colliding_path.write_bytes(original_capability_profile)

    project_text = project_path.read_text(encoding="utf-8")
    updated = project_text.replace(
        'capability_profile: "suno"',
        'capability_profile: "builds/packages/edm@suno/performance_package.json"',
        1,
    )
    assert updated != project_text  # sanity
    project_path.write_text(updated, encoding="utf-8")

    loaded = load_recast_project(project_path)
    artifacts = build_recast_plan_artifacts(loaded, variant="edm", backend="suno", publish=True)

    assert artifacts.result.plan.state_reached == "blocked_capability"
    assert artifacts.result.plan.blocked is not None
    assert any(
        "collides with a protected input path" in reason
        for reason in artifacts.result.plan.blocked.reasons
    )

    # fail-closed: 衝突先の既存内容（capability_profile YAML）が上書きされていない。
    assert colliding_path.read_bytes() == original_capability_profile
    assert not (colliding_path.parent / "compilation_report.json").exists()


def test_atomic_publish_text_bundle_partial_failure_restores_old_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 review round 4（PR3 #208 指摘 8）: `performance_package.json` /
    `compilation_report.json` は従来 2 回の独立 `_atomic_write_text` で公開して
    いたため、package.json の rename が成功した直後に report.json の rename
    が失敗すると「新 package + 古い report」という half-updated な packages
    ディレクトリが残り得た。`_atomic_publish_text_bundle`（`build_recast_plan_
    artifacts` が package/report を公開する際に使う）で report.json 側の最終
    rename だけを失敗注入し、package.json 側も含めて呼び出し前の内容（＝
    旧 package/旧 report）へロールバックされる（新 package だけが現れる／
    古い report だけが残るという half-updated 状態にならない）ことを検証する。"""
    import os

    from svp_rpe.recast.plan import _atomic_publish_text_bundle

    output_dir = tmp_path / "packages" / "edm@suno"
    output_dir.mkdir(parents=True)
    (output_dir / "performance_package.json").write_text("old-package", encoding="utf-8")
    (output_dir / "compilation_report.json").write_text("old-report", encoding="utf-8")

    real_replace = os.replace
    calls = {"failed_once": False}

    def flaky_replace(src: object, dst: object) -> None:
        if str(dst) == str(output_dir / "compilation_report.json") and not calls["failed_once"]:
            calls["failed_once"] = True
            raise OSError("simulated disk full while publishing compilation_report.json")
        real_replace(src, dst)

    monkeypatch.setattr("os.replace", flaky_replace)

    with pytest.raises(OSError):
        _atomic_publish_text_bundle(
            output_dir,
            {
                "performance_package.json": b"new-package",
                "compilation_report.json": b"new-report",
            },
            protected_inputs=[],
        )

    # package.json 側の rename は成功していたが、report.json 側の失敗を受けて
    # 両方とも旧内容へロールバックされている（新 package だけが生き残らない）。
    assert (output_dir / "performance_package.json").read_text(encoding="utf-8") == "old-package"
    assert (output_dir / "compilation_report.json").read_text(encoding="utf-8") == "old-report"


def test_atomic_publish_text_bundle_base_exception_during_rename_still_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 review round 7（PR3 #208 指摘 14）: `_atomic_publish_text_bundle`
    の rename フェーズが従来 `except OSError` のみだったため、rename 中に
    `Exception` を継承しない `BaseException`（`KeyboardInterrupt`/`SystemExit`
    系）が飛ぶと rollback を素通りしていた。`compilation_report.json` 側の
    最終 rename に `Exception` を継承しない例外を注入しても、両ファイルとも
    旧内容へロールバックされる（rollback が確実に効く）ことを検証する。"""
    import os

    from svp_rpe.recast.plan import _atomic_publish_text_bundle

    output_dir = tmp_path / "packages" / "edm@suno"
    output_dir.mkdir(parents=True)
    (output_dir / "performance_package.json").write_text("old-package", encoding="utf-8")
    (output_dir / "compilation_report.json").write_text("old-report", encoding="utf-8")

    real_replace = os.replace
    calls = {"failed_once": False}

    def flaky_replace(src: object, dst: object) -> None:
        if str(dst) == str(output_dir / "compilation_report.json") and not calls["failed_once"]:
            calls["failed_once"] = True
            raise _InjectedBaseException("simulated KeyboardInterrupt-like interruption")
        real_replace(src, dst)

    monkeypatch.setattr("os.replace", flaky_replace)

    with pytest.raises(_InjectedBaseException):
        _atomic_publish_text_bundle(
            output_dir,
            {
                "performance_package.json": b"new-package",
                "compilation_report.json": b"new-report",
            },
            protected_inputs=[],
        )

    assert (output_dir / "performance_package.json").read_text(encoding="utf-8") == "old-package"
    assert (output_dir / "compilation_report.json").read_text(encoding="utf-8") == "old-report"


def test_published_package_json_bytes_sha256_matches_recorded_package_sha256(
    tmp_path: Path,
) -> None:
    """Codex P2 review round 6（PR3 #208 指摘 12）: `_atomic_publish_text_bundle`
    が従来 `Path.write_text(..., encoding="utf-8")` で `performance_package.json`
    を publish していたため、Windows の text-mode 既定 newline 変換
    （`"\n"` → `"\r\n"`）でディスク上の実 bytes が `package_sha256`（
    `compiled.package_json.encode("utf-8")` から計算済みの pin — `arrange/
    package.py:build_performance_package`）と乖離し得た。実際に公開された
    `performance_package.json` の実 bytes の sha256 が、同じく公開された
    `compilation_report.json` に記録された `package_sha256` と一致することを
    直接検証する（このテストは POSIX 環境では常に pass するが、`write_bytes`
    経路への置換自体は Windows 上の newline 変換を構造的に排除する）。"""
    project_path = _copy_demo_project(tmp_path)
    loaded = load_recast_project(project_path)
    artifacts = build_recast_plan_artifacts(loaded, variant="edm", backend="suno", publish=True)

    assert artifacts.result.plan.state_reached == "verified"
    package_dir = project_path.parent / "builds" / "packages" / "edm@suno"
    published_package_bytes = (package_dir / "performance_package.json").read_bytes()
    published_report = json.loads(
        (package_dir / "compilation_report.json").read_text(encoding="utf-8")
    )

    import hashlib

    assert hashlib.sha256(published_package_bytes).hexdigest() == published_report["package_sha256"]


# --- deterministic: invoke + determinism ----------------------------------------


@pytest.mark.slow
def test_deterministic_invoke_writes_take(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _add_target_backend_variant(project_path, variant_name="edm_deterministic", target_backend="deterministic")
    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm_deterministic", backend="deterministic")
    assert ctx.plan_result.plan.state_reached in ("compiled", "verified")

    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    assert isinstance(invoker, DeterministicInvoker)
    prepared = invoker.prepare(ctx)

    take = invoker.invoke(prepared)

    assert take.source == "local"
    assert take.audio_path == prepared.takes_dir / "take-01.wav"
    assert take.audio_path.is_file()
    take_json = json.loads((prepared.takes_dir / "take.json").read_text(encoding="utf-8"))
    assert take_json["sha256"] == take.sha256
    assert take_json["source"] == "local"


@pytest.mark.slow
def test_deterministic_invoke_take_json_failure_leaves_no_orphaned_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 review（PR3 #208 指摘 1）: `DeterministicInvoker.invoke` も
    manual collect と同じ atomic bundle publish を使う — `take.json` の最終
    rename を失敗注入しても `take-01.wav` が残らないことを検証する。"""
    import os

    project_path = _copy_demo_project(tmp_path)
    _add_target_backend_variant(
        project_path, variant_name="edm_deterministic", target_backend="deterministic"
    )
    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm_deterministic", backend="deterministic")
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    prepared = invoker.prepare(ctx)

    real_replace = os.replace

    def flaky_replace(src: object, dst: object) -> None:
        if str(dst).endswith("take.json"):
            raise OSError("simulated disk full while publishing take.json")
        real_replace(src, dst)

    monkeypatch.setattr("os.replace", flaky_replace)

    with pytest.raises(OSError):
        invoker.invoke(prepared)

    assert not (prepared.takes_dir / "take-01.wav").exists()
    assert not (prepared.takes_dir / "take.json").exists()


@pytest.mark.slow
def test_deterministic_invoke_is_byte_identical_across_runs(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _add_target_backend_variant(project_path, variant_name="edm_deterministic", target_backend="deterministic")
    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm_deterministic", backend="deterministic")
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    prepared = invoker.prepare(ctx)

    take_first = invoker.invoke(prepared)
    first_bytes = take_first.audio_path.read_bytes()
    take_second = invoker.invoke(prepared)
    second_bytes = take_second.audio_path.read_bytes()

    assert take_first.sha256 == take_second.sha256
    assert first_bytes == second_bytes


def test_deterministic_collect_raises_recast_error(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _add_target_backend_variant(project_path, variant_name="edm_deterministic", target_backend="deterministic")
    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm_deterministic", backend="deterministic")
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    prepared = invoker.prepare(ctx)

    with pytest.raises(RecastError):
        invoker.collect(prepared, tmp_path / "whatever.wav")


# --- musicgen: stub -------------------------------------------------------------


def test_musicgen_invoke_raises_unavailable(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    _add_target_backend_variant(project_path, variant_name="edm_musicgen", target_backend="musicgen")
    project_text = project_path.read_text(encoding="utf-8")
    project_text = project_text.replace(
        "  deterministic:\n    capability_profile: \"deterministic\"\n"
        "    invocation: local\n    invocation_mode: prompt_only\n",
        "  deterministic:\n    capability_profile: \"deterministic\"\n"
        "    invocation: local\n    invocation_mode: prompt_only\n"
        "  musicgen:\n    capability_profile: \"musicgen\"\n"
        "    invocation: local\n    invocation_mode: prompt_only\n",
        1,
    )
    assert "musicgen:" in project_text  # sanity
    project_path.write_text(project_text, encoding="utf-8")

    loaded = load_recast_project(project_path)
    ctx = build_recast_run_context(loaded, variant="edm_musicgen", backend="musicgen")
    invoker = resolve_invoker(ctx.backend_ref, ctx.profile)
    assert isinstance(invoker, MusicgenInvoker)
    prepared = invoker.prepare(ctx)

    with pytest.raises(RecastBackendUnavailable):
        invoker.invoke(prepared)

    with pytest.raises(RecastError):
        invoker.collect(prepared, tmp_path / "whatever.wav")


@pytest.mark.skip(reason="musicgen backend is stubbed in PR3; no real inference wiring yet")
def test_musicgen_real_inference_not_yet_wired() -> None:
    """PR3 時点では `musicgen` backend に実推論の配線がないためスキップする
    プレースホルダ（`docs/musicgen_backend.md` の runbook 完了後の将来 PR 用）。"""


# --- resolve_invoker / run_context_from_plan_artifacts errors -------------------


def test_resolve_invoker_unknown_local_generator_raises(tmp_path: Path) -> None:
    from svp_rpe.recast.models import BackendRef

    backend_ref = BackendRef(
        capability_profile="suno", invocation="local", invocation_mode="prompt_only"
    )
    from svp_rpe.arrange.capabilities import InputCapabilityProfile

    profile = InputCapabilityProfile.model_validate(
        {
            "schema_version": "input-capability/0.2",
            "generator": "unknown-generator",
            "generator_variant": "standard",
            "profile_version": "2026-07",
            "input_channels": {},
        }
    )
    with pytest.raises(RecastError):
        resolve_invoker(backend_ref, profile)


def test_run_context_from_plan_artifacts_raises_when_not_compiled(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    score_path = project_path.parent / "composition_score.yaml"
    score_path.write_text(
        score_path.read_text(encoding="utf-8").replace(
            'core: "introspective night drive"',
            'core: "TODO(transcribe): author input required"',
        ),
        encoding="utf-8",
    )
    loaded = load_recast_project(project_path)
    artifacts = build_recast_plan_artifacts(loaded, variant="edm", backend="suno", publish=True)
    assert artifacts.result.plan.state_reached == "blocked_authoring"

    with pytest.raises(RecastError):
        run_context_from_plan_artifacts(
            loaded, variant="edm", backend="suno", artifacts=artifacts
        )


def test_run_context_from_plan_artifacts_reuses_plan_profile_without_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 review round 12（PR3 #208 指摘24）: `run_context_from_plan_
    artifacts` はもはや `load_backend_capability_profile` を呼ばない —
    plan 段（`build_recast_plan_artifacts`）が single-read 束で既に
    parse・validate 済みの `artifacts.profile` をそのまま `RecastRunContext.
    profile` へ複製する（従来は独立に capability_profile YAML を再 read/
    再 parse しており、plan の診断が前提とした profile と実行時に invoker
    解決・注文書メタデータへ使われる profile が乖離し得た）。
    `load_backend_capability_profile` を呼んだら即座に失敗するようパッチ
    しても `run_context_from_plan_artifacts` が成功し、`ctx.profile` が
    `artifacts.profile` とオブジェクト同一であることを検証する。"""
    import svp_rpe.recast.backend as backend_module

    def _fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "load_backend_capability_profile must not be called by "
            "run_context_from_plan_artifacts (Codex P2 review round 12, 指摘24)"
        )

    monkeypatch.setattr(backend_module, "load_backend_capability_profile", _fail_if_called)

    project_path = _copy_demo_project(tmp_path)
    loaded = load_recast_project(project_path)
    artifacts = build_recast_plan_artifacts(loaded, variant="edm", backend="suno", publish=True)
    assert artifacts.profile is not None  # sanity: plan 段の single-read 束で parse 済み

    ctx = run_context_from_plan_artifacts(
        loaded, variant="edm", backend="suno", artifacts=artifacts
    )
    assert ctx.profile is artifacts.profile
