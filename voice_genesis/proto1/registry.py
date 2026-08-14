"""registry.py — P4 (VG-010): Genome registry + lineage。

JSONL append-only ストア + sidecar 様式 `genome-registry/0.1`。1 行 = 1 エントリ:

  genome_id（内容 sha256 の先頭 12 桁）/ version / created_at(UTC ISO8601) /
  parents: [] / op(sample|mutate|crossover) / seed / renderer_version("R0.1") /
  feature_set_version / eval: {plausibility, grip_ref, novelty} /
  audit（reference_set_hash, linkability_report_id, residual_gate_passed） /
  genome 本体。

フィールド意味論の補充判断は underspec_log_p1.md [UNDERSPEC-P1-7]・
[UNDERSPEC-P1-8] を参照。

`genome_id` の content hash は genome の正規形 JSON（`genome.to_dict`）を
`hashing.sha256_of_canonical_json` した先頭 12 桁。physio_range / audit を
含む全フィールドが対象（=同一パラメータでも audit 情報が異なれば別 ID になる。
これは意図的: 監査結果が紐付いた genome の同一性は audit を含めて識別すべき
という設計判断）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from genome import VoiceGenome, from_dict, to_dict
from hashing import sha256_of_canonical_json

REGISTRY_SCHEMA = "genome-registry/0.1"
RENDERER_VERSION = "R0.1"
FEATURE_SET_VERSION = "measure_v3"

VALID_OPS = ("sample", "mutate", "crossover")


class RegistryError(ValueError):
    pass


def genome_content_hash(genome: VoiceGenome) -> str:
    """内容 sha256 の先頭 12 桁（genome_id）。"""
    return sha256_of_canonical_json(to_dict(genome))[:12]


@dataclass
class RegistryEntry:
    genome_id: str
    version: str
    created_at: str
    parents: List[str]
    op: str
    seed: Optional[int]
    renderer_version: str
    feature_set_version: str
    # P1 では {plausibility, grip_ref, novelty} は None プレースホルダのみ
    # だったが（[UNDERSPEC-P1-8]）、F1 (final_assembly_memo) の E2E デモは
    # 実測の構造化オブジェクト（例: grip_ref = {"ref": "...", "gate_semantics_version": "..."})
    # を渡すため Any に緩めてある。float 単体を渡す旧来の使い方も引き続き有効。
    eval: Dict[str, Any]
    audit: Dict[str, Any]
    genome: Dict[str, Any]
    registry_schema: str = REGISTRY_SCHEMA

    def to_json_line(self) -> str:
        return json.dumps(_entry_to_dict(self), sort_keys=True, ensure_ascii=True)


def _entry_to_dict(entry: RegistryEntry) -> Dict[str, Any]:
    return {
        "registry_schema": entry.registry_schema,
        "genome_id": entry.genome_id,
        "version": entry.version,
        "created_at": entry.created_at,
        "parents": entry.parents,
        "op": entry.op,
        "seed": entry.seed,
        "renderer_version": entry.renderer_version,
        "feature_set_version": entry.feature_set_version,
        "eval": entry.eval,
        "audit": entry.audit,
        "genome": entry.genome,
    }


def entry_to_dict(entry: RegistryEntry) -> Dict[str, Any]:
    """`_entry_to_dict` の公開エイリアス（proto1_demo.py 等、外部モジュールから
    レジストリエントリを JSON 化するための公開 API）。"""
    return _entry_to_dict(entry)


def _entry_from_dict(data: Dict[str, Any]) -> RegistryEntry:
    return RegistryEntry(
        genome_id=data["genome_id"],
        version=data["version"],
        created_at=data["created_at"],
        parents=list(data.get("parents", [])),
        op=data["op"],
        seed=data.get("seed"),
        renderer_version=data.get("renderer_version", RENDERER_VERSION),
        feature_set_version=data.get("feature_set_version", FEATURE_SET_VERSION),
        eval=dict(data.get("eval", {})),
        audit=dict(data.get("audit", {})),
        genome=data["genome"],
        registry_schema=data.get("registry_schema", REGISTRY_SCHEMA),
    )


class GenomeRegistry:
    """JSONL append-only ストアのラッパ。"""

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)

    def append(
        self,
        genome: VoiceGenome,
        op: str,
        seed: Optional[int] = None,
        parents: Optional[List[str]] = None,
        eval_scores: Optional[Dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> RegistryEntry:
        """genome を 1 行追記する。genome_id はここで content hash から計算する。

        `now` はテスト用の注入ポイント（省略時は wall-clock。§CLAUDE.md の
        「wall-clock 依存は created_at 記録のみ許可」の唯一の使用箇所）。
        """
        if op not in VALID_OPS:
            raise RegistryError(f"op は {VALID_OPS} のいずれかでなければならない（実際: {op!r}）")

        genome_id = genome_content_hash(genome)
        created_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()

        eval_scores = eval_scores or {"plausibility": None, "grip_ref": None, "novelty": None}
        audit = {
            "reference_set_hash": genome.audit.reference_set_hash,
            "linkability_report_id": genome.audit.linkability_report_id,
            "residual_gate_passed": genome.audit.residual_gate_passed,
        }

        entry = RegistryEntry(
            genome_id=genome_id,
            version=genome.schema_version,
            created_at=created_at,
            parents=list(parents or []),
            op=op,
            seed=seed,
            renderer_version=RENDERER_VERSION,
            feature_set_version=FEATURE_SET_VERSION,
            eval=eval_scores,
            audit=audit,
            genome=to_dict(genome),
        )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(entry.to_json_line())
            fh.write("\n")
        return entry

    def load_all(self) -> List[RegistryEntry]:
        if not self.path.exists():
            return []
        entries = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                entries.append(_entry_from_dict(json.loads(line)))
        return entries

    def get(self, genome_id: str) -> Optional[RegistryEntry]:
        """genome_id に一致する最新（末尾）のエントリを返す（無ければ None）。"""
        match = None
        for entry in self.load_all():
            if entry.genome_id == genome_id:
                match = entry
        return match

    def get_genome(self, genome_id: str) -> Optional[VoiceGenome]:
        entry = self.get(genome_id)
        return from_dict(entry.genome) if entry is not None else None

    def lineage(self, genome_id: str) -> List[RegistryEntry]:
        """genome_id から親鎖を遡上し、[最古の祖先, ..., genome_id 自身] の順で返す。

        循環参照防止のため訪問済み genome_id を記録する（append-only ストアに
        循環は起こらないはずだが、防御的に対応する）。
        """
        by_id: Dict[str, RegistryEntry] = {}
        for entry in self.load_all():
            by_id[entry.genome_id] = entry  # 同一 id が複数あれば最後の登録を採用

        if genome_id not in by_id:
            raise RegistryError(f"genome_id が見つからない: {genome_id!r}")

        chain: List[RegistryEntry] = []
        visited = set()
        cursor: Optional[str] = genome_id
        while cursor is not None:
            if cursor in visited:
                raise RegistryError(f"lineage に循環参照を検出: {cursor!r}")
            visited.add(cursor)
            entry = by_id.get(cursor)
            if entry is None:
                break
            chain.append(entry)
            parents = entry.parents
            cursor = parents[0] if parents else None  # sample/mutate は親 1 個。crossover は先頭を主系列とする

        chain.reverse()
        return chain
