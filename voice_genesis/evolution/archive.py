"""archive.py — VG-E0: MAP-Elites 型 Archive（DESIGN_VG_E0.md §3.2）。

セル毎に elite 1 個体（品質スコア最大）+ 保護スロット 1（品質床未満だが
系統的に唯一な個体の保留 — 進化論文書 §7.3）。追い出しは記録付き
（append-only の `eviction_log`。「絶滅も研究資産」）。

`quality` / `quality_floor` はいずれも `submit()` の呼び出し側が明示的に
渡す（DESIGN_VG_E0.md のスコープ境界表「Hard Quality Gate の閾値...
フィールド定義のみ（値は凍結しない）」— run4 実測後に校正されるべき値を
Archive 側で勝手に定数化しない）。この `quality` は EvaluationRecord.axes
（総合1点スコアを恒久禁止する公開 schema、§5）とは別物であり、Archive
内部の elite 順位付けにのみ使う非公開の順序キーである（VG-E1 で
axes からどう導出するかを設計する）。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
import models  # noqa: E402
import simplex  # noqa: E402

CellId = Tuple[int, int, int]


class Archive:
    """MAP-Elites 型 Archive。`n` 分割の三角格子（既定 N=5、25セル）。"""

    def __init__(self, n: int = simplex.GRID_N):
        self.n = n
        self._cell_ids: FrozenSet[CellId] = simplex.all_cell_ids(n)
        self._elite: Dict[CellId, models.ArchiveEntry] = {}
        self._protected: Dict[CellId, models.ArchiveEntry] = {}
        self.eviction_log: List[models.EvictionEvent] = []

    def _cell_for(self, genome: models.VoiceGenome) -> CellId:
        return simplex.cell_id(genome.coords, self.n)

    def _lineage_has_elite(self, lineage: str) -> bool:
        return any(entry.lineage == lineage for entry in self._elite.values())

    def submit(self, genome: models.VoiceGenome, quality: float, *, quality_floor: float) -> str:
        """genome をその座標が属するセルへ提出する。戻り値は
        `"elite"` / `"protected"` / `"rejected"` のいずれか。

        判定順序:
        1. `quality >= quality_floor` かつ（セルに elite が無い、または
           `quality` が既存 elite の quality を上回る）→ elite として採用
           （既存 elite があれば追い出し記録を append）。
        2. 1 に該当しない場合（floor 未満、または floor 以上だが既存 elite
           に劣る）: この genome の lineage が Archive 全体のどの elite にも
           一致しない（＝この lineage が elite として一切生き残っていない）
           場合に限り、保護スロットで採否判定する（既存の保護スロット
           占有者より quality が高ければ採用・追い出し記録を append）。
        3. いずれにも該当しなければ `"rejected"`。
        """
        if not math.isfinite(quality):
            raise ValueError(f"non-finite quality rejected: {quality}")
        if not math.isfinite(quality_floor):
            raise ValueError(f"non-finite quality_floor rejected: {quality_floor}")

        cell = self._cell_for(genome)
        incoming = models.ArchiveEntry(genome_id=genome.genome_id, lineage=genome.lineage, quality=quality)

        if quality >= quality_floor:
            existing = self._elite.get(cell)
            if existing is None or quality > existing.quality:
                if existing is not None:
                    self.eviction_log.append(models.EvictionEvent(
                        cell=cell, slot="elite",
                        evicted_genome_id=existing.genome_id, evicted_quality=existing.quality,
                        incoming_genome_id=incoming.genome_id, incoming_quality=incoming.quality,
                        reason="higher_quality_elite",
                    ))
                self._elite[cell] = incoming
                return "elite"
            return "rejected"

        if not self._lineage_has_elite(genome.lineage):
            existing = self._protected.get(cell)
            if existing is None or quality > existing.quality:
                if existing is not None:
                    self.eviction_log.append(models.EvictionEvent(
                        cell=cell, slot="protected",
                        evicted_genome_id=existing.genome_id, evicted_quality=existing.quality,
                        incoming_genome_id=incoming.genome_id, incoming_quality=incoming.quality,
                        reason="lineage_unique_replacement",
                    ))
                self._protected[cell] = incoming
                return "protected"
            return "rejected"

        return "rejected"

    def elite_at(self, cell: CellId) -> Optional[models.ArchiveEntry]:
        return self._elite.get(cell)

    def protected_at(self, cell: CellId) -> Optional[models.ArchiveEntry]:
        return self._protected.get(cell)

    def cells(self) -> FrozenSet[CellId]:
        return self._cell_ids

    def occupancy(self) -> Dict[str, object]:
        """Archive occupancy 集計（進化論文書 §7.4「MAP-Elites の Archive
        occupancy」の最小実装）。"""
        n_cells = len(self._cell_ids)
        n_elite = len(self._elite)
        n_protected = len(self._protected)
        return {
            "n_cells": n_cells,
            "elite_occupied": n_elite,
            "protected_occupied": n_protected,
            "elite_coverage": n_elite / n_cells if n_cells else 0.0,
            "lineages_with_elite": sorted({e.lineage for e in self._elite.values()}),
            "lineages_with_protected": sorted({e.lineage for e in self._protected.values()}),
        }
