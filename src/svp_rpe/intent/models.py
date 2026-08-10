"""Intent Graph v0 — pydantic v2 frozen schema（`docs/intent/graph.yaml`、`intent-graph/0.1`）。

充足/不足のポインタ台帳 1 ノードの構造を定義する。値のみを保持する不変モデルで、
1 ノード単体で閉じた制約のみをここで検証する: `id` パターン・`status` 語彙・
`untested` 以外は `evidence` 1 件以上必須・`dead` は `reentry` 必須。

ノード間・ファイルシステムに跨る検証（一意 id・`depends_on` 実在・非循環・
`evidence` のリポジトリ相対パス実在）は本モジュールの責務外で `intent/loader.py`
が担う。frontier/blocked/pending の導出は `intent/frontier.py`。

PR #256 再レビュー対応（2026-08-10 第 3R）:
- `_id_matches_pattern` を `.match()` から `.fullmatch()` へ変更（`re.match` は
  文字列全体ではなく先頭一致のみを見るため、末尾に余分な文字（例改行
  `"node.a\n"`）が付いた id を素通ししてしまう穴を塞ぐ）
- `IntentNode.evidence` / `IntentNode.depends_on` / `IntentGraph.nodes` を
  `List` から `Tuple[..., ...]`（default_factory=tuple）へ変更し、frozen モデル
  の不変性をコレクションのミュータビリティにまで及ぼす（frozen dataclass /
  pydantic model は値オブジェクトを不変で定義する、が本リポジトリの規約）
"""
from __future__ import annotations

import re
from typing import Literal, Optional, Self, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "intent-graph/0.1"

IntentStatus = Literal["verified", "partial", "dead", "untested", "machine_dependent"]

# ドット区切りの小文字スラグ列（`physical.roundtrip_determinism` 等）。
# 全体で 1 意（一意性はグラフ全体を見る必要があるため loader.py の責務）。
_NODE_ID_PATTERN = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")


class IntentModel(BaseModel):
    """intent グラフ側スキーマの共通基底。未知 key を fail-closed で拒否する。"""

    model_config = ConfigDict(frozen=True, extra="forbid")


class IntentNode(IntentModel):
    """Intent「AI の楽譜を作る」に対する充足/不足の 1 主張 + 証拠ポインタ 1 件。"""

    id: str
    claim: str = Field(min_length=1)
    status: IntentStatus
    evidence: Tuple[str, ...] = Field(default_factory=tuple)
    depends_on: Tuple[str, ...] = Field(default_factory=tuple)
    reentry: Optional[str] = None
    note: Optional[str] = None

    @model_validator(mode="after")
    def _id_matches_pattern(self) -> Self:
        if not _NODE_ID_PATTERN.fullmatch(self.id):
            raise ValueError(
                f"node id {self.id!r} must match {_NODE_ID_PATTERN.pattern!r} "
                "(lowercase dotted segments, e.g. 'physical.roundtrip_determinism')"
            )
        return self

    @model_validator(mode="after")
    def _evidence_required_unless_untested(self) -> Self:
        if self.status != "untested" and not self.evidence:
            raise ValueError(
                f"node {self.id!r}: evidence is required (>= 1 entry) for "
                f"status={self.status!r} (only status='untested' may omit evidence)"
            )
        return self

    @model_validator(mode="after")
    def _dead_requires_reentry(self) -> Self:
        if self.status == "dead" and not self.reentry:
            raise ValueError(
                f"node {self.id!r}: status='dead' requires a non-empty 'reentry' field"
            )
        return self

    @model_validator(mode="after")
    def _partial_requires_note(self) -> Self:
        if self.status == "partial" and not self.note:
            raise ValueError(
                f"node {self.id!r}: status='partial' requires a non-empty 'note' field"
            )
        return self


class IntentGraph(IntentModel):
    """`docs/intent/graph.yaml` 全体（トップレベルスキーマ）。"""

    schema_version: Literal[SCHEMA_VERSION]
    nodes: Tuple[IntentNode, ...] = Field(default_factory=tuple)
