"""Intent Graph v0 のテスト（`svp_rpe.intent`）。

全合成・高速（音声処理・実抽出ゼロ）。`slow` マーカーは不要。

テスト観点（Design Memo「Intent Graph v0」より）:
1. seed graph が clean にロードできる（実パス evidence の実在含む）
2. 合成の不正グラフが各々 ValueError: 循環 / 未知 depends_on / dead で reentry
   欠落 / 実在しない evidence パス / 重複 id
3. frontier 導出の単体（小合成グラフで frontier/blocked/pending の 3 分類を検証）
4. seed 回帰ピン: `intent.ai_score` が frontier に入る /
   `score.melody_axis` と `authoring.melody_axis` が blocked（m3d closeout と整合）
"""
from __future__ import annotations

from pathlib import Path

import pytest

from svp_rpe.intent.frontier import derive_frontier
from svp_rpe.intent.loader import load_intent_graph
from svp_rpe.intent.models import IntentGraph, IntentNode

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_GRAPH_PATH = REPO_ROOT / "docs" / "intent" / "graph.yaml"

_GRAPH_HEADER = 'schema_version: "intent-graph/0.1"\nnodes:\n'


def _write_graph(tmp_path: Path, body: str) -> Path:
    """`<tmp_path>/docs/intent/graph.yaml` に書き出す。

    repo root 解決（`load_intent_graph` は graph.yaml の位置から 2 階層上を
    repo root とみなす）を実グラフと同じ深さで再現するため、
    `docs/intent/graph.yaml` という相対配置をそのまま踏襲する。
    """
    graph_dir = tmp_path / "docs" / "intent"
    graph_dir.mkdir(parents=True)
    graph_path = graph_dir / "graph.yaml"
    graph_path.write_text(_GRAPH_HEADER + body, encoding="utf-8")
    return graph_path


# ---------------------------------------------------------------------------
# 観点 1: seed graph が clean にロードできる
# ---------------------------------------------------------------------------


def test_seed_graph_loads_cleanly():
    graph = load_intent_graph(SEED_GRAPH_PATH)
    assert graph.schema_version == "intent-graph/0.1"
    assert len(graph.nodes) == 31
    ids = [node.id for node in graph.nodes]
    assert len(ids) == len(set(ids))  # 一意性は loader が保証済みだが、念のため二重確認


# ---------------------------------------------------------------------------
# 観点 2: 合成の不正グラフが各々 ValueError
# ---------------------------------------------------------------------------


def test_cyclic_depends_on_raises_value_error(tmp_path: Path):
    body = """\
  - id: cycle.a
    claim: "a"
    status: verified
    evidence: ["PR #1"]
    depends_on: ["cycle.b"]
  - id: cycle.b
    claim: "b"
    status: verified
    evidence: ["PR #2"]
    depends_on: ["cycle.a"]
"""
    graph_path = _write_graph(tmp_path, body)
    with pytest.raises(ValueError, match="circular depends_on"):
        load_intent_graph(graph_path)


def test_unknown_depends_on_raises_value_error(tmp_path: Path):
    body = """\
  - id: node.a
    claim: "a"
    status: verified
    evidence: ["PR #1"]
    depends_on: ["node.missing"]
"""
    graph_path = _write_graph(tmp_path, body)
    with pytest.raises(ValueError, match="unknown id 'node.missing'"):
        load_intent_graph(graph_path)


def test_dead_without_reentry_raises_value_error(tmp_path: Path):
    body = """\
  - id: node.a
    claim: "a"
    status: dead
    evidence: ["PR #1"]
"""
    graph_path = _write_graph(tmp_path, body)
    with pytest.raises(ValueError, match="reentry"):
        load_intent_graph(graph_path)


def test_missing_evidence_path_raises_value_error(tmp_path: Path):
    body = """\
  - id: node.a
    claim: "a"
    status: verified
    evidence: ["docs/does_not_exist.md"]
"""
    graph_path = _write_graph(tmp_path, body)
    with pytest.raises(ValueError, match="evidence path .* does not exist"):
        load_intent_graph(graph_path)


def test_duplicate_id_raises_value_error(tmp_path: Path):
    body = """\
  - id: node.a
    claim: "a"
    status: verified
    evidence: ["PR #1"]
  - id: node.a
    claim: "a duplicated"
    status: verified
    evidence: ["PR #2"]
"""
    graph_path = _write_graph(tmp_path, body)
    with pytest.raises(ValueError, match="duplicate node id"):
        load_intent_graph(graph_path)


# ---------------------------------------------------------------------------
# 観点 3: frontier 導出の単体
# ---------------------------------------------------------------------------


def _node(**kwargs) -> IntentNode:
    return IntentNode(**kwargs)


def test_derive_frontier_classifies_small_synthetic_graph():
    nodes = [
        _node(id="root.verified", claim="verified root", status="verified", evidence=["PR #1"]),
        _node(
            id="root.dead",
            claim="dead root",
            status="dead",
            evidence=["PR #2"],
            reentry="never",
        ),
        _node(
            id="a.frontier",
            claim="ready to verify",
            status="untested",
            depends_on=["root.verified"],
        ),
        _node(
            id="b.blocked",
            claim="blocked by dead ancestor",
            status="untested",
            depends_on=["root.dead"],
        ),
        _node(
            id="c.pending",
            claim="waiting on an untested dependency",
            status="untested",
            depends_on=["a.frontier"],
        ),
        _node(
            id="d.machine",
            claim="needs real hardware/material",
            status="machine_dependent",
            evidence=["PR #3"],
        ),
    ]
    graph = IntentGraph(schema_version="intent-graph/0.1", nodes=nodes)
    report = derive_frontier(graph)

    assert [entry.id for entry in report.frontier] == ["a.frontier"]
    assert [entry.id for entry in report.pending] == ["c.pending"]
    assert [entry.id for entry in report.machine_dependent] == ["d.machine"]

    blocked_ids = {entry.id: entry.blocking_dead_ancestors for entry in report.blocked}
    assert blocked_ids == {"b.blocked": ("root.dead",)}

    assert report.status_counts == {
        "dead": 1,
        "machine_dependent": 1,
        "untested": 3,
        "verified": 1,
    }


# ---------------------------------------------------------------------------
# 観点 4: seed 回帰ピン
# ---------------------------------------------------------------------------


def test_seed_graph_frontier_pin():
    graph = load_intent_graph(SEED_GRAPH_PATH)
    report = derive_frontier(graph)

    frontier_ids = {entry.id for entry in report.frontier}
    assert "intent.ai_score" in frontier_ids

    blocked_ids = {entry.id for entry in report.blocked}
    assert "score.melody_axis" in blocked_ids
    assert "authoring.melody_axis" in blocked_ids
