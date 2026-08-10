"""Intent Graph の frontier/blocked/pending 導出（純関数、状態を書き換えない）。

`docs/intent/graph.yaml` に格納された `status` は本モジュールでは一切変更しない
— ここで返す `blocked`/`frontier`/`pending`/`machine_dependent` は表示上の導出
分類にすぎない。導出意味論（Design Memo「Intent Graph v0」より）:

- **blocked**: 推移的依存（`depends_on` を辿った先）のどこかに `status="dead"`
  のノードがある（自身の `status` は問わない — `status="dead"` のノードが
  さらに `dead` な祖先へ依存していれば、それ自身も blocked になりうる）
- **frontier**: stored `status="untested"` かつ blocked でなく、直接
  `depends_on` の全ノードの `status` が `"verified"` または `"partial"`
- **pending**: stored `status="untested"` で frontier でも blocked でもない
- **machine_dependent**: `status="machine_dependent"` のノード（frontier には
  一切入れず、別枠で列挙する）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Tuple

from svp_rpe.intent.models import IntentGraph, IntentNode

_TIGHT_ENOUGH_FOR_FRONTIER = frozenset({"verified", "partial"})


@dataclass(frozen=True)
class FrontierEntry:
    """frontier / pending 1 件（id + claim）。"""

    id: str
    claim: str


@dataclass(frozen=True)
class BlockedEntry:
    """blocked 1 件（id + claim + それを阻害している dead 祖先の id 一覧）。"""

    id: str
    claim: str
    blocking_dead_ancestors: Tuple[str, ...]


@dataclass(frozen=True)
class FrontierReport:
    """`derive_frontier` の戻り値。全リストは id 昇順でソート済み。"""

    status_counts: Dict[str, int]
    frontier: Tuple[FrontierEntry, ...] = field(default_factory=tuple)
    blocked: Tuple[BlockedEntry, ...] = field(default_factory=tuple)
    pending: Tuple[FrontierEntry, ...] = field(default_factory=tuple)
    machine_dependent: Tuple[FrontierEntry, ...] = field(default_factory=tuple)


def derive_frontier(graph: IntentGraph) -> FrontierReport:
    """グラフを走査し frontier/blocked/pending/machine_dependent を導出する。

    入力グラフは DAG（`depends_on` が非循環）であることを前提とする —
    循環・未知 `depends_on` の検証は `intent/loader.py` の責務であり、本関数は
    再検証しない（純関数として、既に整合済みの `IntentGraph` を受け取る）。
    """
    by_id: Dict[str, IntentNode] = {node.id: node for node in graph.nodes}
    dead_ancestors_cache: Dict[str, FrozenSet[str]] = {}

    def dead_ancestors(node_id: str) -> FrozenSet[str]:
        if node_id in dead_ancestors_cache:
            return dead_ancestors_cache[node_id]
        node = by_id.get(node_id)
        result: set[str] = set()
        if node is not None:
            for dep_id in node.depends_on:
                dep = by_id.get(dep_id)
                if dep is None:
                    continue
                if dep.status == "dead":
                    result.add(dep_id)
                result |= dead_ancestors(dep_id)
        frozen = frozenset(result)
        dead_ancestors_cache[node_id] = frozen
        return frozen

    status_counts: Dict[str, int] = {}
    frontier: List[FrontierEntry] = []
    blocked: List[BlockedEntry] = []
    pending: List[FrontierEntry] = []
    machine_dependent: List[FrontierEntry] = []

    for node in graph.nodes:
        status_counts[node.status] = status_counts.get(node.status, 0) + 1

        ancestors = dead_ancestors(node.id)
        is_blocked = bool(ancestors)
        if is_blocked:
            blocked.append(
                BlockedEntry(
                    id=node.id,
                    claim=node.claim,
                    blocking_dead_ancestors=tuple(sorted(ancestors)),
                )
            )

        if node.status == "machine_dependent":
            machine_dependent.append(FrontierEntry(id=node.id, claim=node.claim))
            continue

        if node.status != "untested":
            continue

        if is_blocked:
            # blocked かつ untested は blocked 一覧にのみ現れる（pending からは
            # 除外— Design Memo: "stored untested で frontier でも blocked でも
            # ない" が pending の定義）。
            continue

        direct_deps_ready = all(
            by_id[dep_id].status in _TIGHT_ENOUGH_FOR_FRONTIER
            for dep_id in node.depends_on
            if dep_id in by_id
        )
        if direct_deps_ready:
            frontier.append(FrontierEntry(id=node.id, claim=node.claim))
        else:
            pending.append(FrontierEntry(id=node.id, claim=node.claim))

    return FrontierReport(
        status_counts=dict(sorted(status_counts.items())),
        frontier=tuple(sorted(frontier, key=lambda entry: entry.id)),
        blocked=tuple(sorted(blocked, key=lambda entry: entry.id)),
        pending=tuple(sorted(pending, key=lambda entry: entry.id)),
        machine_dependent=tuple(sorted(machine_dependent, key=lambda entry: entry.id)),
    )
