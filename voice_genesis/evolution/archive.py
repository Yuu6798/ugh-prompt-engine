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

「Archive は台帳掲載済みの個体のみを受理する」構造不変量（PR #267 Codex
R14 指摘, 2026-08-18 採用）: `Archive.__init__()` は `ledger.Ledger` を
必須引数として受け取る。`submit()` は自己完結の round-trip 検証（従来の
R9 実装）だけでは「宣言された operator が実際に親からその座標を生めるか」
を見ない — build_genome() で直接「親と同一座標の novelty_jump 子」
（L1=0 は `models.NOVELTY_JUMP_MIN_L1`=0.4 の凍結最小を満たさない）を
構築すれば、座標・lineage・operator 整合の検証だけを通る自己完結ペイロード
としては正当な genome になり、NOVELTY elite として受理されてしまう
（`Ledger.write()` の R7 再導出検証はこの子を拒否するにもかかわらず）。
本 fix は Archive 自身が親解決・operator 再導出を再実装するのではなく、
`self.ledger.read(genome.genome_id)` の成功 + 戻り値と提出 genome の
シリアライズ後バイト完全一致を要求することで、`Ledger.write()` の
publish 時再導出検証（親解決・operator 再実行・provenance 伝播、R7）を
Archive 受理の前提として自動的に効かせる（二重実装を避ける — 設計判断）。
この照合が R9 の自己完結 round-trip 検証を包含するため（台帳へ publish
済みの genome は publish 時点で必ず `genome_from_dict()` の完全検証を
通過済み — ledger.py 参照)、R9 の独立した round-trip 呼び出しは重複となり
削除した。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
import models  # noqa: E402
import simplex  # noqa: E402
from ledger import Ledger, LedgerError  # noqa: E402

CellId = Tuple[int, int, int]


class ArchiveAdmissionError(ValueError):
    """`Archive.submit()` が「台帳掲載済みの個体のみを受理する」構造不変量
    を強制する際の拒否理由（PR #267 Codex R14 指摘, 2026-08-18 採用）:
    提出された genome_id が台帳に存在しない、または台帳エントリの読込
    自体が失敗する（破損・symlink 逸脱・genome_id 不一致など）、あるいは
    台帳エントリと提出 genome がシリアライズ後バイト不一致（publish 後の
    フィールド差し替え）のいずれか。"""


class Archive:
    """MAP-Elites 型 Archive。`n` 分割の三角格子（既定 N=5、25セル）。

    `ledger` は必須引数（PR #267 Codex R14 指摘, 2026-08-18 採用）:
    `submit()` は「台帳掲載済みの個体のみを受理する」構造不変量を
    `self.ledger.read()` 照合で強制するため、その参照先を Archive の
    構築時点で要求する（`submit()` 呼び出し毎の任意引数ではなく
    constructor 必須引数を選んだ理由: Archive は1つの台帳に対して1つの
    MAP-Elites 探索セッションを表す長寿命オブジェクトであり、`submit()`
    毎に異なる台帳を渡す設計上の必要性がない — `Ledger(directory)` が
    同型で directory を constructor 必須引数に取る家風にも揃う）。
    """

    def __init__(self, ledger: Ledger, n: int = simplex.GRID_N):
        self.ledger = ledger
        self.n = n
        self._cell_ids: FrozenSet[CellId] = simplex.all_cell_ids(n)
        self._elite: Dict[CellId, models.ArchiveEntry] = {}
        self._protected: Dict[CellId, models.ArchiveEntry] = {}
        self.eviction_log: List[models.EvictionEvent] = []

    def _cell_for(self, genome: models.VoiceGenome) -> CellId:
        return simplex.cell_id(genome.coords, self.n)

    def _lineage_has_elite(self, lineage: str) -> bool:
        return any(entry.lineage == lineage for entry in self._elite.values())

    def _evict_protected_for_lineage(self, lineage: str, incoming: models.ArchiveEntry) -> None:
        """`lineage` が elite として（どのセルであれ）受理されたことで、同一
        lineage が占有する保護スロットはもはや「系統的に唯一」ではなくなる
        （PR #267 Codex R5 指摘2, 2026-08-17 採用）。該当する保護スロット
        （複数セルにまたがりうる）を全て無効化し、追い出しイベントとして
        記録する。従来はこの evict が無かったため、L-R の保護個体が elite
        受理後もスロットを占有し続け、後から来る真に未代表の系統の個体を
        品質比較で不当に弾いていた。
        """
        stale_cells = [c for c, entry in self._protected.items() if entry.lineage == lineage]
        for c in stale_cells:
            evicted = self._protected.pop(c)
            self.eviction_log.append(models.EvictionEvent(
                cell=c, slot="protected",
                evicted_genome_id=evicted.genome_id, evicted_quality=evicted.quality,
                incoming_genome_id=incoming.genome_id, incoming_quality=incoming.quality,
                reason="lineage_no_longer_unique",
            ))

    def _route_evicted_elite_to_protection(self, evicted: models.ArchiveEntry, cell: CellId) -> None:
        """elite スロットから追い出された個体を、保護スロット資格判定（R6の
        above-floor 敗退フォールスルーと同じ経路）へルーティングする
        （PR #267 Codex R7 指摘2, 2026-08-17 採用）。elite が別系統の高品質
        個体に置換されたとき、追い出された個体の lineage が Archive 全体の
        どの elite にも代表されなくなった場合（＝この個体こそが「系統的に
        唯一」になった場合）に限り、追い出し先の同一セルの保護スロットへ
        quality 比較で採否判定する。従来はこの経路が無く、単に破棄される
        だけだったため、2個体を提出する順序（先に elite→後で高品質個体に
        置換 / 先に高品質個体が elite→後で低品質個体が above-floor 敗退で
        保護スロットへ）によって最終状態が異なっていた。

        本メソッドが呼ばれる時点で `self._elite[cell]` は新 elite（=呼び出し元
        の `incoming`）へ既に更新済みであることを前提とする（`_lineage_has_elite`
        が置換後の状態を正しく反映するため）。`incoming` と `evicted` が同一
        lineage の場合（通常の「同系統内でのより高品質な elite への置換」）は
        `_lineage_has_elite(evicted.lineage)` が真のまま（incoming 自身がその
        lineage を代表するため）となり、本メソッドは何もしない — 同系統内の
        置換は保護スロットの対象にならない。
        """
        if self._lineage_has_elite(evicted.lineage):
            return
        existing_protected = self._protected.get(cell)
        if existing_protected is None or evicted.quality > existing_protected.quality:
            if existing_protected is not None:
                self.eviction_log.append(models.EvictionEvent(
                    cell=cell, slot="protected",
                    evicted_genome_id=existing_protected.genome_id, evicted_quality=existing_protected.quality,
                    incoming_genome_id=evicted.genome_id, incoming_quality=evicted.quality,
                    reason="lineage_unique_replacement",
                ))
            self._protected[cell] = evicted

    def submit(self, genome: models.VoiceGenome, quality: float, *, quality_floor: float) -> str:
        """genome をその座標が属するセルへ提出する。戻り値は
        `"elite"` / `"protected"` / `"rejected"` のいずれか。

        冒頭で `self.ledger.read(genome.genome_id)` が成功し、その戻り値が
        提出された `genome` とシリアライズ後バイト完全一致することを要求
        する（PR #267 Codex R14 指摘, 2026-08-18 採用: 「Archive は台帳掲載
        済みの個体のみを受理する」構造不変量）。未 publish・破損・
        symlink 逸脱・genome_id 不一致のいずれも `ArchiveAdmissionError`
        で fail-closed 拒否する。台帳への publish（`Ledger.write()`）は
        親解決 + operator 再導出検証（R7）を経ているため、この照合を通った
        genome は自動的にその検証も経たことになる（Archive 側で親解決・
        再導出を再実装しない）。台帳エントリは publish 時点で
        `genome_from_dict()` の完全検証（型・数値範囲・genome_id 再計算
        一致・座標由来 lineage 整合等 — 旧 R9 の自己完結 round-trip 検証と
        同じ検証）を通過済みのため、この照合は R9 の検証を包含する（重複を
        避けるため R9 の独立 round-trip 呼び出しは削除した）。

        判定順序:
        0. `self.ledger.read(genome.genome_id)` の成功 + バイト完全一致
           （上記）。不成立なら以降の判定に進まず `ArchiveAdmissionError`。
        1. `quality >= quality_floor` かつ（セルに elite が無い、または
           `quality` が既存 elite の quality を上回る）→ elite として採用
           （既存 elite があれば追い出し記録を append）。elite 採用時は
           同一 lineage が占有する保護スロット（どのセルであれ）を無効化
           する（追い出し記録付き — PR #267 Codex R5 指摘2: elite に到達した
           lineage はもはや「唯一」ではないため）。さらに、追い出された旧
           elite の lineage が Archive 全体のどの elite にも一切代表され
           なくなった場合は、その旧 elite を保護スロット資格判定へルーティング
           する（PR #267 Codex R6 指摘2の above-floor 敗退フォールスルーと
           同じ経路 — PR #267 Codex R7 指摘2, 2026-08-17 採用: 従来はこの
           ルーティングが無く、elite 置換で追い出された個体は保護スロット
           選定を経由せず即破棄されていたため、同じ2個体でも投入順序に
           よって最終状態が変わっていた）。
        2. 1 に該当しない場合（floor 未満、または floor 以上だが既存 elite
           に劣る — いずれの場合も elite 競争に敗れたとして 2 へフォール
           スルーする。PR #267 Codex R6 指摘2, 2026-08-17 採用: 従来は
           floor 以上で elite に劣った場合に即 `"rejected"` を return して
           おり、保護スロット判定へフォールスルーしなかった。above-floor
           だが既存 elite に劣る候補でも、その lineage が Archive 全体の
           どの elite にも一切代表されていなければ「系統的に唯一」であり
           保護スロットの対象になるべき — 実例: 別セルの L-R elite
           (q=0.9) に対し、未代表の L-C 候補 (q=0.8, floor=0.5) が
           above-floor であるという理由だけで保護スロットを素通りし
           拒否されていた）: この genome の lineage が Archive 全体の
           どの elite にも一致しない（＝この lineage が elite として一切
           生き残っていない）場合に限り、保護スロットで採否判定する
           （既存の保護スロット占有者より quality が高ければ採用・追い出し
           記録を append）。
        3. いずれにも該当しなければ `"rejected"`。
        """
        # PR #267 Codex R10 指摘B（P2）, 2026-08-17 採用: bool は int のサブ
        # クラスのため `math.isfinite(True)` は無検証で通り、`quality=True`
        # が `quality=1.0` として選抜を汚染していた。有限性チェックより前に
        # isinstance(x, bool) で fail-closed 拒否する。
        if isinstance(quality, bool):
            raise ValueError(f"bool quality rejected: {quality!r}")
        if isinstance(quality_floor, bool):
            raise ValueError(f"bool quality_floor rejected: {quality_floor!r}")
        if not math.isfinite(quality):
            raise ValueError(f"non-finite quality rejected: {quality}")
        if not math.isfinite(quality_floor):
            raise ValueError(f"non-finite quality_floor rejected: {quality_floor}")

        # PR #267 Codex R14 指摘（P1）, 2026-08-18 採用: 「Archive は台帳掲載
        # 済みの個体のみを受理する」構造不変量。旧 R9 の自己完結 round-trip
        # 検証（`genome_from_dict(genome_to_dict(genome))`）は genome 単体の
        # schema/型/座標・lineage 整合しか見ないため、「宣言された operator
        # が実際に親からその座標を生めるか」（= `Ledger.write()` の R7 再導出
        # 検証が守る不変量）を素通ししていた。実例: `models.build_genome()`
        # で直接「親と同一座標の novelty_jump 子」（L1=0 <
        # `models.NOVELTY_JUMP_MIN_L1`=0.4 の凍結最小）を構築すると、座標
        # 自体は正規形・lineage=NOVELTY は operator=novelty_jump と整合する
        # ため R9 の round-trip 検証は通過し、NOVELTY elite として受理され
        # 得た — にもかかわらず、同じ子を `Ledger.write()` しようとすると
        # R7 の再導出検証（宣言 operator_params から実際に novelty_jump()
        # を再実行し、得られる genome_id が一致するか）が確実に拒否する
        # （novelty_jump() は棄却サンプリングで L1>=0.4 を保証するため、
        # 親と同一座標には原理的に再導出され得ない）。
        #
        # Archive 側で親解決・operator 再導出を再実装するのではなく、台帳
        # に既に実装済みのこの検証を「publish 済みかどうか」の照合を通じて
        # 前提として利用する（二重実装を避ける — 設計判断）。
        try:
            ledger_genome = self.ledger.read(genome.genome_id)
        except FileNotFoundError:
            raise ArchiveAdmissionError(
                f"refusing to submit genome_id {genome.genome_id!r}: not found in the ledger — Archive "
                "only accepts genomes that are already published to the ledger (parent resolution + "
                "operator re-derivation is enforced at Ledger.write() time — PR #267 Codex R14; Archive "
                "does not re-implement it)"
            ) from None
        except (json.JSONDecodeError, models.GenomeValidationError, LedgerError) as exc:
            raise ArchiveAdmissionError(
                f"refusing to submit genome_id {genome.genome_id!r}: ledger lookup failed full "
                f"validation on read ({exc})"
            ) from exc

        submitted_bytes = models.genome_to_json(genome).encode("utf-8")
        ledger_bytes = models.genome_to_json(ledger_genome).encode("utf-8")
        if submitted_bytes != ledger_bytes:
            raise ArchiveAdmissionError(
                f"refusing to submit genome_id {genome.genome_id!r}: submitted genome does not "
                "byte-for-byte match the published ledger entry (fields tampered after publish — "
                "the ledger copy is the single source of truth for admission)"
            )

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
                self._evict_protected_for_lineage(incoming.lineage, incoming)
                if existing is not None:
                    self._route_evicted_elite_to_protection(existing, cell)
                return "elite"
            # PR #267 Codex R6 指摘2（P2）: above-floor だが既存 elite に劣る
            # 候補は、即 "rejected" にせず保護スロット判定（2.）へフォール
            # スルーする — この genome の lineage が elite として一切
            # 生き残っていなければ、below-floor の候補と同様に保護スロットの
            # 対象になるべきため（early return しない = 意図的な fall-through）。

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
