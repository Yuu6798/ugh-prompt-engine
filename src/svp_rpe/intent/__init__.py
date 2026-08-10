"""intent — Intent Graph v0: 充足/不足のポインタ台帳 + frontier 導出。

`docs/intent/graph.yaml`（schema `intent-graph/0.1`）を正本として、Intent
「AI の楽譜を作る」に対する充足/不足を機械可読に管理する。status の
自動更新・自動推論はしない（更新は PR レビュー経由の手動編集のみ）。
重み付け・スコアリング・可視化（描画）もしない — ポインタと導出のみ。

- `models.py` — 1 ノード単体で閉じた pydantic frozen スキーマ
- `loader.py` — YAML ロード + グラフ全体の整合検証（fail-fast）
- `frontier.py` — frontier/blocked/pending の導出（純関数）

詳細仕様は `docs/intent_graph.md`。CLI は `svprpe intent-status`
（`svp_rpe.cli.intent`）。
"""
from __future__ import annotations

from svp_rpe.intent.frontier import BlockedEntry, FrontierEntry, FrontierReport, derive_frontier
from svp_rpe.intent.loader import load_intent_graph
from svp_rpe.intent.models import SCHEMA_VERSION, IntentGraph, IntentNode, IntentStatus

__all__ = [
    "SCHEMA_VERSION",
    "BlockedEntry",
    "FrontierEntry",
    "FrontierReport",
    "IntentGraph",
    "IntentNode",
    "IntentStatus",
    "derive_frontier",
    "load_intent_graph",
]
