"""docs/*.md が CLAUDE.md / docs/README.md の索引から漏れていないことを検証する。

CLAUDE.md「設計ドキュメント索引」節とドキュメント管理ポリシー節は、新規
`docs/<topic>.md` を作成したら両方の索引に 1 行追加することを規約として
定める。本テストはその規約を機械的に enforce する:

1. `docs/*.md`（`docs/README.md` 自身を除く）は全件 CLAUDE.md の索引表
   （`| [\\`docs/x.md\\`](docs/x.md) | ... |` 形式の行）に掲載されていること
2. 同様に全件 `docs/README.md` の索引（`(x.md)` / `(./x.md)` 形式のリンク）
   に掲載されていること
3. どちらの索引にも実在しない docs ファイルへのリンクがないこと
4. CLAUDE.md の行は表示パス（バッククォート内）とリンク先（`()` 内）が
   一致していること（表示だけ正しくリンク先が別ファイルを指す drift を防ぐ。
   `docs/README.md` のリンクは表示テキストが自由文で機械的な表示/リンク先の
   対応がないため、リンク先そのものを直接検証しており本項の対象外）
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pytest

from ._helpers import FIXTURES, REPO_ROOT

CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
DOCS_README = REPO_ROOT / "docs" / "README.md"
DOCS_DIR = REPO_ROOT / "docs"

# CLAUDE.md 索引表の行: `| [`docs/x.md`](docs/y.md) | 内容 |`
# group 1 = 表示パス（バッククォート内）, group 2 = リンク先（`()` 内）。
# 両者は本来常に同一ファイルを指すべきだが、別々に捕捉することで
# 「表示は正しいがリンク先が別ファイル」という drift を検出できるようにする。
# ファイル名部分はスラッシュ・空白・括弧・バッククォートを含まなければ何でも許容する
# （ハイフンを含むファイル名も対象。`[A-Za-z0-9_.]+` だとハイフンを取りこぼす）。
_CLAUDE_MD_ROW_RE = re.compile(
    r"\[`docs/([^`()/\s]+\.md)`\]\(docs/([^`()/\s]+\.md)\)"
)

# docs/README.md の索引エントリ行: `- [Title](x.md) — description` または
# `* [Title](x.md) — description`（カテゴリ見出し `## ...` の下に並ぶ箇条書き）。
# 行頭（strip 後）が `- [` / `* [` で始まる行のみをエントリ行として扱い、
# その行内で最初に登場するリンクだけをエントリ本体とみなす。これにより
# 「見出し直下の説明文（プローズ）の中だけに登場するリンク」や「あるエントリの
# description テキスト中に埋め込まれた 2 個目以降のリンク」を索引エントリとして
# 誤カウントしない（CLAUDE.md 側で 4a20395 で直したプローズ限定リンクの穴の
# docs/README.md 版）。
# リンク先は `(x.md)` または `(./x.md)`。`../` や `sub/x.md` は docs/ 直下では
# ない参照（AGENTS.md や voice_genesis/ 配下など）なので、スラッシュを含まない
# ファイル名のみを対象にする。
# 表示テキストは `[Example A](x.md)` のように自由文のプローズであり
# `docs/x.md` 形式の表示/リンク先の対応が存在しないため、このリンクは
# リンク先 `(x.md)` そのものを直接抽出・検証している（表示側と比較する
# 余地がない = 本ファイルの display/destination mismatch チェックの対象外）。
_DOCS_README_LINK_RE = re.compile(r"^[-*]\s+\[[^\]]*\]\((?:\./)?([^`()/\s]+\.md)\)")


@dataclass(frozen=True)
class ClaudeMdRow:
    """CLAUDE.md 索引表の 1 行から抽出した表示パス / リンク先 / 元テキスト。"""

    display: str
    destination: str
    row_text: str


def _parse_claude_md_rows(text: str) -> list[ClaudeMdRow]:
    r"""索引表の行のみを対象にパースする（プローズ中のリンクは対象外）。

    `[\`docs/x.md\`](docs/x.md)` 形式のリンクは本文中の言及（例: CLAUDE.md
    冒頭の「由来 = ...」や「モジュール単位の責務詳細は ...」）にも登場しうるが、
    それらは索引表への掲載を意味しない。索引表の行（先頭が `| [` の行）のみを
    対象にすることで、プローズ中のリンクだけがあり索引表には未掲載という
    drift を正しく「未掲載」として検出できるようにする。
    """
    rows = []
    for line in text.splitlines():
        if not line.strip().startswith("| ["):
            continue
        match = _CLAUDE_MD_ROW_RE.search(line)
        if match:
            rows.append(
                ClaudeMdRow(display=match.group(1), destination=match.group(2), row_text=line.strip())
            )
    return rows


def _claude_md_index_docs(text: str) -> set[str]:
    """索引表に掲載されている docs ファイル名（表示パス基準）の集合。"""
    return {row.display for row in _parse_claude_md_rows(text)}


def _claude_md_index_targets(text: str) -> set[str]:
    """索引表の実際のリンク先（クリック時の遷移先）の集合。"""
    return {row.destination for row in _parse_claude_md_rows(text)}


def _docs_readme_index_docs(text: str) -> set[str]:
    r"""索引エントリ行のみを対象にパースする（プローズ中や description 中の
    2 個目以降のリンクは対象外）。

    `- [Title](x.md) — ...` 形式の行頭リンクのみを 1 行 1 エントリとして
    抽出する。見出し直下の説明文や、あるエントリの description テキスト中に
    別ファイルへのリンクが埋め込まれていても、それらは索引エントリとしての
    掲載を意味しないため拾わない。
    """
    docs = set()
    for line in text.splitlines():
        match = _DOCS_README_LINK_RE.match(line.strip())
        if match:
            docs.add(match.group(1))
    return docs


def _real_docs_files() -> set[str]:
    return {p.name for p in DOCS_DIR.glob("*.md") if p.name != "README.md"}


def _assert_all_docs_indexed(
    text: str, docs_files: set[str], *, source: str, extractor
) -> None:
    linked = extractor(text)
    missing = docs_files - linked
    assert not missing, (
        f"{source} に未掲載の docs/*.md がある: {sorted(missing)}\n"
        "新規 docs/<topic>.md を作成したら CLAUDE.md の設計ドキュメント索引表と "
        "docs/README.md の両方に 1 行追加すること（CLAUDE.md ドキュメント管理ポリシー節）。"
    )


def _assert_no_broken_links(
    text: str, existing_files: set[str], *, source: str, extractor
) -> None:
    linked = extractor(text)
    broken = linked - existing_files
    assert not broken, (
        f"{source} に実在しない docs ファイルへのリンクがある: {sorted(broken)}"
    )


def _assert_claude_md_targets_match_display(text: str, *, source: str) -> None:
    mismatches = [row for row in _parse_claude_md_rows(text) if row.display != row.destination]
    assert not mismatches, (
        f"{source} に表示パスとリンク先が食い違う行がある（クリック先が表示と別ファイルを指す）:\n"
        + "\n".join(f"  {row.row_text}" for row in mismatches)
    )


def test_claude_md_index_covers_all_docs():
    _assert_all_docs_indexed(
        CLAUDE_MD.read_text(encoding="utf-8"),
        _real_docs_files(),
        source="CLAUDE.md の設計ドキュメント索引表",
        extractor=_claude_md_index_docs,
    )


def test_docs_readme_index_covers_all_docs():
    _assert_all_docs_indexed(
        DOCS_README.read_text(encoding="utf-8"),
        _real_docs_files(),
        source="docs/README.md の索引",
        extractor=_docs_readme_index_docs,
    )


def test_claude_md_index_has_no_broken_links():
    # docs/README.md 自身も docs/ 直下の実在ファイルなのでリンク先として許容する。
    # ここでは表示パスではなく実際のクリック先（destination）を検証する。
    existing = _real_docs_files() | {"README.md"}
    _assert_no_broken_links(
        CLAUDE_MD.read_text(encoding="utf-8"),
        existing,
        source="CLAUDE.md の設計ドキュメント索引表",
        extractor=_claude_md_index_targets,
    )


def test_claude_md_index_targets_match_display():
    _assert_claude_md_targets_match_display(
        CLAUDE_MD.read_text(encoding="utf-8"),
        source="CLAUDE.md の設計ドキュメント索引表",
    )


def test_docs_readme_index_has_no_broken_links():
    existing = _real_docs_files() | {"README.md"}
    _assert_no_broken_links(
        DOCS_README.read_text(encoding="utf-8"),
        existing,
        source="docs/README.md の索引",
        extractor=_docs_readme_index_docs,
    )


def test_parser_detects_missing_doc_in_claude_md_fixture():
    text = (FIXTURES / "docs_index_claude_md_missing_doc.md").read_text(encoding="utf-8")
    with pytest.raises(AssertionError, match="未掲載の docs"):
        _assert_all_docs_indexed(
            text,
            {"example_a.md", "example_b.md"},
            source="fixture",
            extractor=_claude_md_index_docs,
        )


def test_parser_detects_missing_doc_in_docs_readme_fixture():
    text = (FIXTURES / "docs_readme_index_missing_doc.md").read_text(encoding="utf-8")
    with pytest.raises(AssertionError, match="未掲載の docs"):
        _assert_all_docs_indexed(
            text,
            {"example_a.md", "example_b.md"},
            source="fixture",
            extractor=_docs_readme_index_docs,
        )


def test_parser_detects_broken_link_fixture():
    # 本番の test_claude_md_index_has_no_broken_links はリンク先
    # （_claude_md_index_targets）を検証するため、self-test も同じ抽出器で
    # 揃える（表示パス側の _claude_md_index_docs では検証対象がずれる）。
    text = (FIXTURES / "docs_index_broken_link.md").read_text(encoding="utf-8")
    with pytest.raises(AssertionError, match="実在しない docs ファイルへのリンク"):
        _assert_no_broken_links(
            text,
            {"example_a.md"},
            source="fixture",
            extractor=_claude_md_index_targets,
        )


def test_parser_detects_prose_only_link_fixture():
    # プローズ中にのみ登場し索引表には掲載されていないリンクは「未掲載」と
    # 判定されるべき（索引表の行以外を拾ってしまう回帰の防止）。
    text = (FIXTURES / "docs_index_prose_only_link.md").read_text(encoding="utf-8")
    with pytest.raises(AssertionError, match="未掲載の docs"):
        _assert_all_docs_indexed(
            text,
            {"example_a.md", "example_b.md"},
            source="fixture",
            extractor=_claude_md_index_docs,
        )


def test_parser_detects_readme_prose_only_link_fixture():
    # docs/README.md 版のプローズ限定リンクの穴（CLAUDE.md 側の同型の穴は
    # 4a20395 で修正済み）。見出し直下の説明文にのみ登場するリンクは索引
    # エントリ行として拾われず、「未掲載」として検出されるべき。
    text = (FIXTURES / "docs_readme_index_prose_only_link.md").read_text(encoding="utf-8")
    with pytest.raises(AssertionError, match="未掲載の docs"):
        _assert_all_docs_indexed(
            text,
            {"example_a.md", "example_b.md"},
            source="fixture",
            extractor=_docs_readme_index_docs,
        )


def test_parser_ignores_second_link_in_readme_entry_description():
    # エントリ行の description 部分に埋め込まれた 2 個目以降のリンクは、
    # 独立した索引エントリとしてカウントしてはならない（そのエントリ行の
    # 実体は先頭リンクのみ）。
    text = (FIXTURES / "docs_readme_index_prose_only_link.md").read_text(encoding="utf-8")
    assert _docs_readme_index_docs(text) == {"example_a.md"}


def test_parser_matches_hyphenated_doc_name_fixture():
    # ファイル名にハイフンを含むケースも索引表の行として正しく捕捉できること
    # （`[A-Za-z0-9_.]+` だとハイフンを取りこぼしていた回帰の防止）。
    text = (FIXTURES / "docs_index_hyphenated_name.md").read_text(encoding="utf-8")
    assert _claude_md_index_docs(text) == {"example-hyphenated.md"}


def test_parser_detects_mismatched_target_fixture():
    text = (FIXTURES / "docs_index_mismatched_target.md").read_text(encoding="utf-8")
    with pytest.raises(AssertionError, match="表示パスとリンク先が食い違う"):
        _assert_claude_md_targets_match_display(text, source="fixture")
