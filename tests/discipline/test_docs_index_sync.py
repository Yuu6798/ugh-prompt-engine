"""docs/*.md が CLAUDE.md / docs/README.md の索引から漏れていないことを検証する。

CLAUDE.md「設計ドキュメント索引」節とドキュメント管理ポリシー節は、新規
`docs/<topic>.md` を作成したら両方の索引に 1 行追加することを規約として
定める。本テストはその規約を機械的に enforce する:

1. `docs/*.md`（`docs/README.md` 自身を除く）は全件 CLAUDE.md の索引表
   （`| [\\`docs/x.md\\`](docs/x.md) | ... |` 形式の行）に掲載されていること
2. 同様に全件 `docs/README.md` の索引（`(x.md)` / `(./x.md)` 形式のリンク）
   に掲載されていること
3. どちらの索引にも実在しない docs ファイルへのリンクがないこと
"""
from __future__ import annotations

import re

import pytest

from ._helpers import FIXTURES, REPO_ROOT

CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
DOCS_README = REPO_ROOT / "docs" / "README.md"
DOCS_DIR = REPO_ROOT / "docs"

# CLAUDE.md 索引表の行: `| [`docs/x.md`](docs/x.md) | 内容 |`
_CLAUDE_MD_ROW_RE = re.compile(r"\[`docs/([A-Za-z0-9_.]+\.md)`\]\(docs/[A-Za-z0-9_.]+\.md\)")

# docs/README.md のリンク: `(x.md)` または `(./x.md)`。`../` や `sub/x.md` は
# docs/ 直下ではない参照（AGENTS.md や voice_genesis/ 配下など）なので、
# スラッシュを含まないファイル名のみを対象にする。
_DOCS_README_LINK_RE = re.compile(r"\]\((?:\./)?([A-Za-z0-9_.]+\.md)\)")


def _claude_md_index_docs(text: str) -> set[str]:
    return set(_CLAUDE_MD_ROW_RE.findall(text))


def _docs_readme_index_docs(text: str) -> set[str]:
    return set(_DOCS_README_LINK_RE.findall(text))


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
    existing = _real_docs_files() | {"README.md"}
    _assert_no_broken_links(
        CLAUDE_MD.read_text(encoding="utf-8"),
        existing,
        source="CLAUDE.md の設計ドキュメント索引表",
        extractor=_claude_md_index_docs,
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
    text = (FIXTURES / "docs_index_broken_link.md").read_text(encoding="utf-8")
    with pytest.raises(AssertionError, match="実在しない docs ファイルへのリンク"):
        _assert_no_broken_links(
            text,
            {"example_a.md"},
            source="fixture",
            extractor=_claude_md_index_docs,
        )
