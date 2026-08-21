"""genome_s35/s35_spec.py — S3.5 の凍結定義（設計書 v1.0 §19）。

**凍結定数と verdict enum だけを置く。** 研究判断・I/O・音響処理は禁止。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Sequence, Tuple

SCHEMA = "voicegenesis-genome-s35/1.0"
ANSWERS_SCHEMA = "voicegenesis-s35-answers/1.0"
PROTOCOL_VERSION = "voicegenesis-s35-v1"

#: §6.1 selection_hash の領域分離プレフィクス。**変更禁止**。
SELECTION_DOMAIN = "voicegenesis-s35-v1"

# --- §19 凍結定数（変更禁止）---------------------------------------------
PAIRS_PER_GENE = 4
REPEATS_PER_PAIR = 2
TRIALS_PER_GENE = 8
MIN_CONTEXTS = 2
PERCEPTIBLE_CORRECT = 7
MIN_PERCEPTIBLE_GENES_FOR_S4 = 2
MAX_REPLAYS_PER_CLIP = 3


class GeneVerdict(str, Enum):
    PERCEPTIBLE = "PERCEPTIBLE"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    NOT_EVALUABLE_S35 = "NOT_EVALUABLE_S35"
    INVALID = "INVALID"


class Answer(str, Enum):
    A = "A"
    B = "B"
    UNSURE = "UNSURE"


#: S3 側で `SUPPORTED` の gene だけが候補（§5）。S3 の判定語彙は再定義しない。
S3_SUPPORTED = "SUPPORTED"

#: A/B に置く 2 音の正体。`gene` = gene-only 条件の出力。
SIDE_B0 = "B0"
SIDE_GENE = "gene"

#: S3 の条件名。gene 名 -> gene-only 条件（S3 の `GENE_CONDITION` と同値）。
#: S3 側を import せずに済ませるための**写し**で、テストで S3 と一致を検査する。
GENE_CONDITION: Dict[str, str] = {
    "f0": "F", "duration": "D", "energy": "E", "release": "R",
}


@dataclass(frozen=True)
class Protocol:
    type: str = "ABX"
    pairs_per_gene: int = PAIRS_PER_GENE
    repeats_per_pair: int = REPEATS_PER_PAIR
    trials_per_gene: int = TRIALS_PER_GENE
    threshold_correct: int = PERCEPTIBLE_CORRECT

    def as_dict(self) -> Dict[str, object]:
        return {"type": self.type, "pairs_per_gene": self.pairs_per_gene,
                "repeats_per_pair": self.repeats_per_pair,
                "trials_per_gene": self.trials_per_gene,
                "threshold_correct": self.threshold_correct}


def selection_hash(s3_results_sha256: str, gene: str, pair_key: str) -> str:
    """§6.1 の決定論的 pair 選択ハッシュ。

        SHA256("voicegenesis-s35-v1" + s3_results_sha256 + gene + pair_key)

    効果量・metric 値・聞きやすさは**入力に含めない**。同じ S3 正本なら
    同じ選択になり、結果を見てからの入れ替えができない。
    """
    h = hashlib.sha256()
    h.update((SELECTION_DOMAIN + s3_results_sha256 + gene + pair_key).encode("utf-8"))
    return h.hexdigest()


def select_pairs(candidates: Sequence[Tuple[str, str]], s3_results_sha256: str,
                 gene: str) -> Tuple[Tuple[str, str], ...]:
    """§6.1 の選択手順をそのまま実装する。

    `candidates` = `(pair_key, probe_kind)` の列（S3 で `SUPPORTED` の pair のみ）。

    1. `selection_hash` 昇順に並べる
    2. まず異なる `probe_kind` から 1 件ずつ、**最低 2 context** を確保
    3. 残りを全候補の hash 順で埋める
    4. 合計 4 distinct pair

    条件を満たせない場合は空を返す（呼び出し側が `NOT_EVALUABLE_S35`）。
    """
    ordered = sorted(candidates, key=lambda c: selection_hash(s3_results_sha256, gene, c[0]))
    if len({p for p, _ in ordered}) < PAIRS_PER_GENE:
        return ()
    if len({k for _, k in ordered}) < MIN_CONTEXTS:
        return ()

    picked: list = []
    seen_pairs: set = set()
    seen_kinds: set = set()
    # 手順 2: hash 順に、まだ出ていない probe_kind から 1 件ずつ。
    # **最低 2 context** を確保した時点で打ち切る（MIN_CONTEXTS は凍結定数）。
    for pair_key, kind in ordered:
        if len(seen_kinds) >= MIN_CONTEXTS:
            break
        if kind in seen_kinds:
            continue
        picked.append((pair_key, kind))
        seen_pairs.add(pair_key)
        seen_kinds.add(kind)
    # 手順 3: 残りを全候補の hash 順で埋める
    for pair_key, kind in ordered:
        if len(picked) >= PAIRS_PER_GENE:
            break
        if pair_key in seen_pairs:
            continue
        picked.append((pair_key, kind))
        seen_pairs.add(pair_key)
    if len(picked) != PAIRS_PER_GENE:
        return ()
    return tuple(picked)


def gene_verdict(correct: int, total: int, distinct_pairs: int, contexts: int,
                 *, commitment_verified: bool, audio_verified: bool) -> GeneVerdict:
    """§13 Gene-Level Gate。**4 状態以外を作らない。**"""
    if not commitment_verified or not audio_verified:
        return GeneVerdict.INVALID
    if total != TRIALS_PER_GENE:
        return GeneVerdict.INVALID
    if distinct_pairs != PAIRS_PER_GENE or contexts < MIN_CONTEXTS:
        return GeneVerdict.NOT_EVALUABLE_S35
    if correct >= PERCEPTIBLE_CORRECT:
        return GeneVerdict.PERCEPTIBLE
    return GeneVerdict.NOT_ESTABLISHED


def s4_gate(perceptible_gene_count: int) -> str:
    """§14 S4 進行 Gate。`S4_NOT_READY` は S3 FAIL を意味しない。"""
    return ("S4_READY" if perceptible_gene_count >= MIN_PERCEPTIBLE_GENES_FOR_S4
            else "S4_NOT_READY")
