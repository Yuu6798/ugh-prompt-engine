"""genome_s3/s3_spec.py — S3 の凍結定義（設計書 v1.1 §15）。

**音響処理・I/O・動的閾値をここに置かない。** 定義だけ。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, Tuple

_PLANB = Path(__file__).resolve().parent.parent / "planb"
if str(_PLANB) not in sys.path:
    sys.path.insert(0, str(_PLANB))

from pb_tracks import PerformanceToggles  # noqa: E402

SCHEMA = "voicegenesis-genome-s3/1.1"


class Gene(str, Enum):
    F0 = "f0"
    DURATION = "duration"
    ENERGY = "energy"
    RELEASE = "release"


class PairVerdict(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    NOT_EVALUABLE = "NOT_EVALUABLE"
    FAILED = "FAILED"


class GeneVerdict(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    NOT_EVALUABLE = "NOT_EVALUABLE"
    FAILED = "FAILED"


#: 条件名 -> トグル。B0 は全 OFF、各 gene は 1 つだけ ON（設計書 §6）。
#: 組合せは作らない（F+D 等は S4 以降）。
CONDITIONS: Dict[str, PerformanceToggles] = {
    "B0": PerformanceToggles(),
    "F": PerformanceToggles(f0=True),
    "D": PerformanceToggles(duration=True),
    "E": PerformanceToggles(energy=True),
    "R": PerformanceToggles(release=True),
}

#: gene -> 条件名
GENE_CONDITION: Dict[Gene, str] = {
    Gene.F0: "F", Gene.DURATION: "D", Gene.ENERGY: "E", Gene.RELEASE: "R",
}

#: gene -> (P3 で使う既存 metric 名, 期待する向き)。**新しい metric を足さない**（§8）。
GENE_METRIC: Dict[Gene, Tuple[str, str]] = {
    Gene.F0: ("f0_dev_rmse_cents", "lower"),
    Gene.DURATION: ("note_split_mae_ms", "lower"),
    Gene.ENERGY: ("energy_corr", "higher"),
    Gene.RELEASE: ("taper_rmse_db", "lower"),
}

# --- 事前登録した工学的受入基準（§10・§19 により変更禁止）---
MIN_SUPPORT_RATIO = 0.75
MIN_EVALUABLE_PAIRS = 3
MIN_EVALUABLE_CONTEXTS = 2
MIN_SUPPORTED_CONTEXTS = 2
MIN_SUPPORTED_GENES = 2


@dataclass(frozen=True)
class Criteria:
    min_evaluable_pairs: int = MIN_EVALUABLE_PAIRS
    min_evaluable_contexts: int = MIN_EVALUABLE_CONTEXTS
    min_supported_contexts: int = MIN_SUPPORTED_CONTEXTS
    min_support_ratio: float = MIN_SUPPORT_RATIO
    min_supported_genes: int = MIN_SUPPORTED_GENES

    def as_dict(self) -> Dict[str, object]:
        return {"min_evaluable_pairs": self.min_evaluable_pairs,
                "min_evaluable_contexts": self.min_evaluable_contexts,
                "min_supported_contexts": self.min_supported_contexts,
                "min_support_ratio": self.min_support_ratio,
                "min_supported_genes": self.min_supported_genes}


def context_id(pair: Dict[str, object]) -> str:
    """`context_id` = frozen manifest の `probe_kind` を**そのまま**使う（§5）。

    再分類・統合・分割・改名は禁止。欠落は呼び出し側が BLOCKED にする。
    """
    kind = pair.get("probe_kind")
    if not isinstance(kind, str) or not kind:
        raise KeyError("probe_kind が無い pair は使用できない（設計書 §5）")
    return kind


def toggles_for(gene: Gene) -> PerformanceToggles:
    return CONDITIONS[GENE_CONDITION[gene]]


def only_one_toggle_on(tg: PerformanceToggles, gene: Gene) -> bool:
    """対象 gene のトグルだけが True であること（§8 P1）。"""
    state = {Gene.F0: tg.f0, Gene.DURATION: tg.duration,
             Gene.ENERGY: tg.energy, Gene.RELEASE: tg.release}
    return state[gene] and not any(v for g, v in state.items() if g is not gene)
