"""`docs/intent/graph.yaml` のロード + グラフ全体の整合検証（fail-fast）。

`intent/models.py` は 1 ノード単体で閉じた制約のみを検証する。本モジュールは
ノード間・ファイルシステムに跨る検証を担う: 一意 id・`depends_on` 実在・
非循環（DAG）・`evidence` のリポジトリ相対パス実在。見つかった違反は集約し、
1 回の `ValueError` にまとめて報告する（最初の 1 件で止めない）。

`evidence` の「リポジトリ相対パス」判定は `/` を含むかどうかで行う（`/` を
含まない項目、例 `"PR #171"` は参照として素通しし実在検証しない）。repo root
は `path`（既定 `docs/intent/graph.yaml`）の位置から 2 階層上として解決する
（`docs/intent/graph.yaml` → `docs/intent` → `docs` → repo root）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import yaml

from svp_rpe.intent.models import IntentGraph, IntentNode

# graph.yaml の既定配置からの repo root への相対階層数
# (docs/intent/graph.yaml -> docs/intent -> docs -> repo root)。
_REPO_ROOT_PARENT_DEPTH = 2


def load_intent_graph(path: Path | str) -> IntentGraph:
    """intent graph YAML を読み込み、スキーマ検証 + グラフ全体の整合検証を行う。"""
    graph_path = Path(path)
    try:
        raw_bytes = graph_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"intent graph unreadable at {graph_path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw_bytes)
    except yaml.YAMLError as exc:
        raise ValueError(f"intent graph {graph_path} is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"intent graph {graph_path} must be a mapping")

    # pydantic.ValidationError は ValueError のサブクラスなので、id パターン・
    # status 語彙・evidence/reentry 必須制約（models.py 側）はここで自然に
    # fail-fast する。
    graph = IntentGraph.model_validate(data)

    repo_root = graph_path.resolve().parents[_REPO_ROOT_PARENT_DEPTH]
    errors = _collect_graph_errors(graph, graph_path=graph_path, repo_root=repo_root)
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


def _find_cycles(nodes: List[IntentNode], by_id: Dict[str, IntentNode]) -> List[str]:
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
    nodes: List[IntentNode], *, graph_path: Path, repo_root: Path
) -> List[str]:
    errors: List[str] = []
    for node in nodes:
        for entry in node.evidence:
            if "/" not in entry:
                # `/` を含まない項目（例 "PR #171"）は参照として素通しする。
                continue
            candidate = repo_root / entry
            if not candidate.is_file():
                errors.append(
                    f"node {node.id!r}: evidence path {entry!r} does not exist "
                    f"at {candidate} (resolved from {graph_path})"
                )
    return errors
