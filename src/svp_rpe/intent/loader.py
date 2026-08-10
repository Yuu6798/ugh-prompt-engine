"""`docs/intent/graph.yaml` のロード + グラフ全体の整合検証（fail-fast）。

`intent/models.py` は 1 ノード単体で閉じた制約のみを検証する。本モジュールは
ノード間・ファイルシステムに跨る検証を担う: 一意 id・`depends_on` 実在・
非循環（DAG）・`evidence` のリポジトリ相対パス実在。見つかった違反は集約し、
1 回の `ValueError` にまとめて報告する（最初の 1 件で止めない）。

`evidence` の各項目は次のいずれか一方: (1) 正規表現 `^PR #\d+$` に fullmatch
する PR 参照（例 `"PR #171"`）は参照として素通しし実在検証しない、(2) それ以外は
すべてリポジトリ相対パスとして扱い実在 + repo 内封じ込めを検証する（`/` を
含むかどうかによる緩いヒューリスティックは使わない — `"CLAUDE.md"` のような
スラッシュ無しの誤記パスも (2) 側で検証対象になる）。repo root は既定では
`path`（既定 `docs/intent/graph.yaml`）の位置から 2 階層上として解決する
（`docs/intent/graph.yaml` → `docs/intent` → `docs` → repo root）。canonical
でない配置から呼ぶ場合は `repo_root` を明示すること（`docs/intent_graph.md`
§8 参照 — 任意配置の自動解決は v0 非目標）。

PR #256 レビュー対応（2026-08-10）:
- YAML 重複キーは `yaml.safe_load` だと last-key-wins で黙って通ってしまう
  （矛盾する `status` 等が静かに片方だけ有効になる）ため拒否する。重複キー拒否
  ローダは `melody.representation._NoDupSafeLoader` を再利用する（import
  のみ・重複実装禁止 — `authoring/contract.py` / `cli/validate_cmd.py` /
  `recast/experimental.py` と同じ再利用パターン）
- `evidence` のリポジトリ相対パスは backslash・絶対パス・`..` 成分・symlink
  脱出を拒否し repo 配下に封じ込める（`repo_root / entry` は entry が絶対パス
  だと絶対パス側が勝つ pathlib の挙動があるため）
- repo root 導出が失敗する配置（`path` の親が 2 階層に満たない）では素の
  `IndexError` を漏らさず `ValueError` に変換する（CLI の exit 2 契約を保証）

PR #256 再レビュー対応（2026-08-10 第 2R）:
- `evidence` の「PR 参照 vs リポジトリ相対パス」判定を `/` の有無という緩い
  ヒューリスティックから `^PR #\d+$` fullmatch へ厳格化（`"CLAUDE.md"` のような
  スラッシュ無しの誤記パスが実在検証をすり抜けていた穴を塞ぐ）
- パス検証チェーンの先頭に backslash 拒否を追加（`\` を 1 文字でも含む項目は
  拒否 — プラットフォーム中立の方針として separator は `/` のみを認め、
  POSIX 上で `Path.is_absolute()` が素通ししてしまう Windows 形式の絶対パス/
  `..` トラバーサル、例 `"C:\\temp\\proof.md"` / `"..\\outside.md"` を捕捉する）
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from svp_rpe.intent.models import IntentGraph, IntentNode
from svp_rpe.melody.representation import _NoDupSafeLoader

# graph.yaml の既定配置からの repo root への相対階層数
# (docs/intent/graph.yaml -> docs/intent -> docs -> repo root)。
_REPO_ROOT_PARENT_DEPTH = 2

# `evidence` のうち、リポジトリ相対パスとして実在検証せず参照として素通しする
# 唯一の文法（例 "PR #171"）。これに fullmatch しない項目はすべてパスとして
# 扱われる（"CLAUDE.md" のようなスラッシュ無しの誤記パスも検証対象になる）。
_PR_REFERENCE_PATTERN = re.compile(r"^PR #\d+$")


def load_intent_graph(path: Path | str, *, repo_root: Optional[Path] = None) -> IntentGraph:
    """intent graph YAML を読み込み、スキーマ検証 + グラフ全体の整合検証を行う。

    `repo_root` を省略した場合は `path` の位置から 2 階層上を repo root と
    みなす（canonical 配置 `docs/intent/graph.yaml` 前提）。この既定導出が
    行えない配置（親ディレクトリが浅すぎる等）では `ValueError` を送出する
    ので、canonical でない配置から呼ぶ場合は `repo_root` を明示すること。
    """
    graph_path = Path(path)
    try:
        raw_bytes = graph_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"intent graph unreadable at {graph_path}: {exc}") from exc

    try:
        data = yaml.load(raw_bytes, Loader=_NoDupSafeLoader)  # noqa: S506 (dup-key 拒否付き SafeLoader)
    except (yaml.YAMLError, ValueError) as exc:
        raise ValueError(f"intent graph {graph_path} is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"intent graph {graph_path} must be a mapping")

    # pydantic.ValidationError は ValueError のサブクラスなので、id パターン・
    # status 語彙・evidence/reentry 必須制約（models.py 側）はここで自然に
    # fail-fast する。
    graph = IntentGraph.model_validate(data)

    resolved_repo_root = repo_root
    if resolved_repo_root is None:
        resolved_path = graph_path.resolve()
        try:
            resolved_repo_root = resolved_path.parents[_REPO_ROOT_PARENT_DEPTH]
        except IndexError as exc:
            raise ValueError(
                f"cannot derive repo root from {graph_path} "
                f"(resolved: {resolved_path}); pass repo_root explicitly or use "
                "the canonical docs/intent/graph.yaml layout"
            ) from exc

    errors = _collect_graph_errors(graph, graph_path=graph_path, repo_root=resolved_repo_root)
    if errors:
        joined = "\n".join(f"  - {error}" for error in errors)
        raise ValueError(f"intent graph {graph_path} failed consistency checks:\n{joined}")
    return graph


def _collect_graph_errors(graph: IntentGraph, *, graph_path: Path, repo_root: Path) -> List[str]:
    errors: List[str] = []
    by_id: Dict[str, IntentNode] = {}
    for node in graph.nodes:
        if node.id in by_id:
            errors.append(f"duplicate node id: {node.id!r}")
        else:
            by_id[node.id] = node

    for node in graph.nodes:
        for dep_id in node.depends_on:
            if dep_id not in by_id:
                errors.append(f"node {node.id!r}: depends_on references unknown id {dep_id!r}")

    errors.extend(_find_cycles(graph.nodes, by_id))
    errors.extend(_find_missing_evidence(graph.nodes, graph_path=graph_path, repo_root=repo_root))
    return errors


def _find_cycles(nodes: Tuple[IntentNode, ...], by_id: Dict[str, IntentNode]) -> List[str]:
    """`depends_on` の循環を DFS で検出する。

    未知 `depends_on`（`by_id` に存在しない参照先）は `_collect_graph_errors` が
    別途報告済みなので、ここでは辿らずに無視する。
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {node.id: WHITE for node in nodes}
    errors: List[str] = []

    def visit(node_id: str, path: List[str]) -> None:
        color[node_id] = GRAY
        path.append(node_id)
        node = by_id.get(node_id)
        if node is not None:
            for dep_id in node.depends_on:
                if dep_id not in by_id:
                    continue
                if color[dep_id] == GRAY:
                    cycle_start = path.index(dep_id)
                    cycle = [*path[cycle_start:], dep_id]
                    errors.append("circular depends_on: " + " -> ".join(cycle))
                elif color[dep_id] == WHITE:
                    visit(dep_id, path)
        path.pop()
        color[node_id] = BLACK

    for node in nodes:
        if color[node.id] == WHITE:
            visit(node.id, [])
    return errors


def _find_missing_evidence(
    nodes: Tuple[IntentNode, ...], *, graph_path: Path, repo_root: Path
) -> List[str]:
    """`evidence` の repository-relative パスを検証する（実在 + repo 内封じ込め）。

    PR #256 レビュー対応: `evidence` 項目は `^PR #\\d+$` に fullmatch する参照
    のみ素通しし、それ以外は全件パスとして扱う（`/` の有無という緩い
    ヒューリスティックは使わない）。パスは 4 段階で repo 内封じ込めを保証する:
    (1) backslash 拒否（プラットフォーム中立 — separator は `/` のみを認め、
    POSIX では通常の文字として素通ししてしまう Windows 形式の絶対パス/
    トラバーサルを捕捉する）、(2) 絶対パス拒否（`repo_root / entry` は
    `entry` が絶対パスだと `pathlib` の仕様上絶対パス側が勝ち、`repo_root` の
    外を指してしまう — `Path("/a") / "/etc/passwd"` == `Path("/etc/passwd")`）、
    (3) `..` 成分拒否、(4) `.resolve()` 後に `repo_root.resolve()` 配下に
    あることを確認（symlink がリポジトリ外を指すケースを捕捉する）。
    """
    resolved_repo_root = repo_root.resolve()
    errors: List[str] = []
    for node in nodes:
        for entry in node.evidence:
            if _PR_REFERENCE_PATTERN.fullmatch(entry):
                # "PR #171" 形式の参照は実在検証しない。
                continue
            if "\\" in entry:
                errors.append(
                    f"node {node.id!r}: evidence path {entry!r} must not contain "
                    "'\\' (only '/' is accepted as a path separator)"
                )
                continue
            entry_path = Path(entry)
            if entry_path.is_absolute():
                errors.append(
                    f"node {node.id!r}: evidence path {entry!r} must be "
                    "repository-relative (absolute paths are rejected)"
                )
                continue
            if ".." in entry_path.parts:
                errors.append(
                    f"node {node.id!r}: evidence path {entry!r} must not contain "
                    "'..' path components (repository containment)"
                )
                continue
            candidate = repo_root / entry_path
            resolved_candidate = candidate.resolve()
            if resolved_repo_root not in (resolved_candidate, *resolved_candidate.parents):
                errors.append(
                    f"node {node.id!r}: evidence path {entry!r} resolves outside "
                    f"the repository root ({resolved_candidate} is not under "
                    f"{resolved_repo_root}, possible symlink escape)"
                )
                continue
            if not candidate.is_file():
                errors.append(
                    f"node {node.id!r}: evidence path {entry!r} does not exist "
                    f"at {candidate} (resolved from {graph_path})"
                )
    return errors
