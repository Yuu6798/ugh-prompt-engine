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

import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from svp_rpe.cli import app
from svp_rpe.intent.frontier import derive_frontier
from svp_rpe.intent.loader import load_intent_graph
from svp_rpe.intent.models import IntentGraph, IntentNode

runner = CliRunner()

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


def test_seed_graph_collections_are_tuples():
    """`IntentGraph.nodes` / `IntentNode.evidence` / `depends_on` は不変の tuple。"""
    graph = load_intent_graph(SEED_GRAPH_PATH)
    assert isinstance(graph.nodes, tuple)
    for node in graph.nodes:
        assert isinstance(node.evidence, tuple)
        assert isinstance(node.depends_on, tuple)


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


def test_node_id_with_trailing_newline_raises_value_error(tmp_path: Path):
    """`re.match` は末尾に余分な文字（改行）があっても素通ししてしまうため
    `re.fullmatch` へ変更した（`$` は非 MULTILINE でも末尾改行の直前にマッチ
    する仕様のため、`match()` だと "node.a\n" が id パターンを通ってしまう）。
    """
    body = """\
  - id: "node.a\\n"
    claim: "a"
    status: verified
    evidence: ["PR #1"]
"""
    graph_path = _write_graph(tmp_path, body)
    with pytest.raises(ValueError, match="must match"):
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


def test_partial_without_note_raises_value_error():
    """status='partial' で `note` が欠落 -> `models.py` の validator が拒否する。"""
    with pytest.raises(ValueError, match="status='partial' requires a non-empty 'note'"):
        IntentNode(
            id="node.a",
            claim="a",
            status="partial",
            evidence=["PR #1"],
        )


def test_partial_with_whitespace_only_note_raises_value_error():
    """note=" "（whitespace-only）は「空実質」として note 欠落と同様に拒否する。"""
    with pytest.raises(ValueError, match="status='partial' requires a non-empty 'note'"):
        IntentNode(
            id="node.a",
            claim="a",
            status="partial",
            evidence=["PR #1"],
            note=" ",
        )


def test_dead_with_whitespace_only_reentry_raises_value_error():
    """reentry=" "（whitespace-only）は reentry 欠落と同様に拒否する。"""
    with pytest.raises(ValueError, match="status='dead' requires a non-empty 'reentry'"):
        IntentNode(
            id="node.a",
            claim="a",
            status="dead",
            evidence=["PR #1"],
            reentry=" ",
        )


def test_whitespace_only_claim_raises_value_error():
    """claim=" "（whitespace-only）は `min_length=1` をすり抜けるため専用 validator で拒否する。"""
    with pytest.raises(ValueError, match="claim must not be blank"):
        IntentNode(
            id="node.a",
            claim=" ",
            status="untested",
        )


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


def test_duplicate_key_within_node_raises_value_error(tmp_path: Path):
    """ノード内で `status` が重複 -> `_NoDupSafeLoader` が拒否する。"""
    body = """\
  - id: node.a
    claim: "a"
    status: verified
    status: dead
    evidence: ["PR #1"]
"""
    graph_path = _write_graph(tmp_path, body)
    with pytest.raises(ValueError, match="duplicate"):
        load_intent_graph(graph_path)


def test_duplicate_top_level_nodes_key_raises_value_error(tmp_path: Path):
    """トップレベルで `nodes` キーが重複 -> `_NoDupSafeLoader` が拒否する。"""
    graph_dir = tmp_path / "docs" / "intent"
    graph_dir.mkdir(parents=True)
    graph_path = graph_dir / "graph.yaml"
    graph_path.write_text(
        'schema_version: "intent-graph/0.1"\n'
        "nodes:\n"
        "  - id: node.a\n"
        '    claim: "a"\n'
        "    status: verified\n"
        '    evidence: ["PR #1"]\n'
        "nodes:\n"
        "  - id: node.b\n"
        '    claim: "b"\n'
        "    status: verified\n"
        '    evidence: ["PR #2"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
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


def test_evidence_absolute_path_raises_value_error(tmp_path: Path):
    """絶対パスの evidence は `repo_root / entry` で絶対パス側が勝つため拒否する。"""
    body = """\
  - id: node.a
    claim: "a"
    status: verified
    evidence: ["/etc/passwd"]
"""
    graph_path = _write_graph(tmp_path, body)
    with pytest.raises(ValueError, match="must be repository-relative"):
        load_intent_graph(graph_path)


def test_evidence_dotdot_path_raises_value_error(tmp_path: Path):
    """`..` を含む evidence パスは repo 外への脱出とみなし拒否する。"""
    body = """\
  - id: node.a
    claim: "a"
    status: verified
    evidence: ["../outside.md"]
"""
    graph_path = _write_graph(tmp_path, body)
    with pytest.raises(ValueError, match="must not contain '..'"):
        load_intent_graph(graph_path)


def test_evidence_symlink_escape_raises_value_error(tmp_path: Path):
    """symlink 経由で repo root 外を指す evidence パスは resolve 後に拒否する。"""
    outside_dir = tmp_path.parent / f"{tmp_path.name}_outside"
    outside_dir.mkdir()
    (outside_dir / "secret.md").write_text("secret", encoding="utf-8")

    body = """\
  - id: node.a
    claim: "a"
    status: verified
    evidence: ["escape/secret.md"]
"""
    graph_path = _write_graph(tmp_path, body)
    repo_root = tmp_path
    (repo_root / "escape").symlink_to(outside_dir)

    with pytest.raises(ValueError, match="resolves outside the repository root"):
        load_intent_graph(graph_path)


def test_evidence_slashless_typo_path_raises_value_error(tmp_path: Path):
    """`/` を含まない誤記パス（例 "CLADUE.md"）も新文法ではパスとして検証される。

    旧「`/` の有無」ヒューリスティックでは参照として素通しされていた穴
    （PR #256 再レビュー指摘）。
    """
    body = """\
  - id: node.a
    claim: "a"
    status: verified
    evidence: ["CLADUE.md"]
"""
    graph_path = _write_graph(tmp_path, body)
    with pytest.raises(ValueError, match="evidence path .* does not exist"):
        load_intent_graph(graph_path)


def test_evidence_pr_reference_still_passes_through(tmp_path: Path):
    """`^PR #\\d+$` に fullmatch する項目は引き続き参照として素通しする。"""
    body = """\
  - id: node.a
    claim: "a"
    status: verified
    evidence: ["PR #123"]
"""
    graph_path = _write_graph(tmp_path, body)
    graph = load_intent_graph(graph_path)
    assert len(graph.nodes) == 1


def test_evidence_windows_absolute_path_raises_value_error(tmp_path: Path):
    """backslash を含む Windows 形式の絶対パスは POSIX の `is_absolute()` をすり抜けるため専用拒否する。"""
    body = """\
  - id: node.a
    claim: "a"
    status: verified
    evidence: ["C:\\\\temp\\\\proof.md"]
"""
    graph_path = _write_graph(tmp_path, body)
    with pytest.raises(ValueError, match=r"must not contain '\\'"):
        load_intent_graph(graph_path)


def test_evidence_windows_dotdot_path_raises_value_error(tmp_path: Path):
    """backslash 区切りの `..` トラバーサルも同じ backslash 拒否で捕捉する。"""
    body = """\
  - id: node.a
    claim: "a"
    status: verified
    evidence: ["..\\\\outside.md"]
"""
    graph_path = _write_graph(tmp_path, body)
    with pytest.raises(ValueError, match=r"must not contain '\\'"):
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


# ---------------------------------------------------------------------------
# repo_root 明示指定 + repo root 導出失敗の exit 2 契約（PR #256 レビュー対応）
# ---------------------------------------------------------------------------

_MINIMAL_VALID_BODY = _GRAPH_HEADER + (
    "  - id: node.a\n"
    '    claim: "a"\n'
    "    status: untested\n"
)


def test_repo_root_keyword_overrides_default_derivation(tmp_path: Path):
    """`repo_root` を明示すると canonical 配置でない graph.yaml でも evidence 解決できる。"""
    (tmp_path / "NOTES.md").write_text("hi", encoding="utf-8")
    graph_path = tmp_path / "graph.yaml"
    graph_path.write_text(
        _GRAPH_HEADER
        + (
            "  - id: node.a\n"
            '    claim: "a"\n'
            "    status: verified\n"
            '    evidence: ["NOTES.md"]\n'
        ),
        encoding="utf-8",
    )
    graph = load_intent_graph(graph_path, repo_root=tmp_path)
    assert len(graph.nodes) == 1


def test_repo_root_derivation_failure_raises_value_error():
    """`path` の親が 2 階層に満たない配置では `IndexError` を `ValueError` に変換する。

    実際の filesystem root 直下相当（`/tmp` 直下、ネストなし）に置くことで
    `resolve().parents[2]` が確実に `IndexError` になる配置を再現する。
    """
    graph_path = Path("/tmp") / f"intent_graph_shallow_{uuid.uuid4().hex}.yaml"
    try:
        graph_path.write_text(_MINIMAL_VALID_BODY, encoding="utf-8")
        with pytest.raises(ValueError, match="cannot derive repo root"):
            load_intent_graph(graph_path)
    finally:
        graph_path.unlink(missing_ok=True)


def test_cli_repo_root_derivation_failure_exits_2():
    """`svprpe intent-status --graph <shallow path>` は exit code 2 になる。"""
    graph_path = Path("/tmp") / f"intent_graph_shallow_cli_{uuid.uuid4().hex}.yaml"
    try:
        graph_path.write_text(_MINIMAL_VALID_BODY, encoding="utf-8")
        result = runner.invoke(app, ["intent-status", "--graph", str(graph_path)])
        assert result.exit_code == 2
    finally:
        graph_path.unlink(missing_ok=True)
