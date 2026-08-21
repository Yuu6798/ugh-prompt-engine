"""genome_s35/s35_spec.py — S3.5 軽量版（人間負担軽量版・User 改訂 2026-08-21）の凍結定義。

**凍結定数と verdict enum だけを置く。** 研究判断・I/O・音響処理は禁止。

改訂で**削除**したもの（旧 v1 設計書 §7/§8/§13/§15）:
4 pair × 2 repeat / 8 trial per gene / 32 trial total / 7-of-8 閾値 /
binomial 確率による合格判定。統計的証明は目的にしない。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

SCHEMA = "voicegenesis-genome-s35/2.0"
ANSWERS_SCHEMA = "voicegenesis-s35-answers/2.0"
PROTOCOL_VERSION = "voicegenesis-s35-v2"

#: pair 選択ハッシュの領域分離プレフィクス。**旧版から変更しない**
#: （選択規則そのものは改訂対象外で、回答前なので差し替える理由もない）。
SELECTION_DOMAIN = "voicegenesis-s35-v1"

# --- 凍結定数 -------------------------------------------------------------
#: 1 gene あたり 1 stage で 1 問。
TRIALS_PER_GENE_PER_STAGE = 1
#: stage は 2 つ（Stage 1 → 通過 gene のみ Stage 2）。
STAGES = 2
#: 人間の回答負担の上限（4 gene × 2 stage）。
MAX_TOTAL_TRIALS = 8
#: S4 へ進むのに必要な PERCEPTIBLE_CANDIDATE 数。
MIN_PERCEPTIBLE_GENES_FOR_S4 = 2
#: 1 クリップの再生上限。
MAX_REPLAYS_PER_CLIP = 3
#: Stage 1 と Stage 2 で要求する distinct probe_kind 数。
REQUIRED_CONTEXTS = 2

STAGE1 = 1
STAGE2 = 2


class GeneVerdict(str, Enum):
    PERCEPTIBLE_CANDIDATE = "PERCEPTIBLE_CANDIDATE"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    NOT_EVALUABLE_S35 = "NOT_EVALUABLE_S35"
    INVALID = "INVALID"


class Answer(str, Enum):
    A = "A"
    B = "B"
    UNSURE = "UNSURE"


#: S3 側で `SUPPORTED` の gene / pair だけが候補。
S3_SUPPORTED = "SUPPORTED"

SIDE_B0 = "B0"
SIDE_GENE = "gene"

#: gene 名 -> S3 の gene-only 条件名（S3 `GENE_CONDITION` の写し。テストで一致検査）。
GENE_CONDITION: Dict[str, str] = {
    "f0": "F", "duration": "D", "energy": "E", "release": "R",
}


@dataclass(frozen=True)
class Protocol:
    type: str = "ABX-2stage"
    stages: int = STAGES
    trials_per_gene_per_stage: int = TRIALS_PER_GENE_PER_STAGE
    max_total_trials: int = MAX_TOTAL_TRIALS
    required_contexts: int = REQUIRED_CONTEXTS
    min_perceptible_genes_for_s4: int = MIN_PERCEPTIBLE_GENES_FOR_S4

    def as_dict(self) -> Dict[str, object]:
        return {"type": self.type, "stages": self.stages,
                "trials_per_gene_per_stage": self.trials_per_gene_per_stage,
                "max_total_trials": self.max_total_trials,
                "required_contexts": self.required_contexts,
                "min_perceptible_genes_for_s4": self.min_perceptible_genes_for_s4}


def selection_hash(s3_results_sha256: str, gene: str, pair_key: str) -> str:
    """決定論的 pair 選択ハッシュ。

        SHA256("voicegenesis-s35-v1" + s3_results_sha256 + gene + pair_key)

    効果量・metric 値・聞きやすさ・音質は**入力に含めない**。人間も選ばない。
    同じ S3 正本なら常に同じ選択になり、結果を見てからの入れ替えができない。
    """
    h = hashlib.sha256()
    h.update((SELECTION_DOMAIN + s3_results_sha256 + gene + pair_key).encode("utf-8"))
    return h.hexdigest()


def select_two_stage_pairs(candidates: Sequence[Tuple[str, str]],
                           s3_results_sha256: str, gene: str,
                           ) -> Tuple[Optional[Tuple[str, str]], Optional[Tuple[str, str]]]:
    """Stage 1 / Stage 2 の pair を決定論的に選ぶ。

    `candidates` = `(pair_key, probe_kind)`（S3 で `SUPPORTED` の pair のみ）。

    - Stage 1 = `selection_hash` 最小の pair
    - Stage 2 = **Stage 1 と別 pair かつ別 probe_kind** のうち hash 最小

    Stage 2 の probe_kind を変えるのは要件（主張上限が「異なる 2 文脈で識別できた」
    のため）。別 context が存在しない gene は呼び出し側で `NOT_EVALUABLE_S35`。
    Stage 1 すら取れない場合は `(None, None)`。
    """
    ordered = sorted(candidates, key=lambda c: selection_hash(s3_results_sha256, gene, c[0]))
    if not ordered:
        return None, None
    first = ordered[0]
    second = next((c for c in ordered[1:]
                   if c[0] != first[0] and c[1] != first[1]), None)
    return first, second


def gene_verdict(stage1_correct: Optional[bool], stage2_correct: Optional[bool],
                 *, has_stage2_pair: bool,
                 commitment_verified: bool = True,
                 audio_verified: bool = True) -> GeneVerdict:
    """gene 判定。**4 状態以外を作らない。**

    ```text
    Stage 1 正解 AND Stage 2 正解        -> PERCEPTIBLE_CANDIDATE
    どちらか不正解 / UNSURE               -> NOT_ESTABLISHED
    Stage 2 用の別 context が存在しない    -> NOT_EVALUABLE_S35
    blind 破壊 / SHA 不一致               -> INVALID
    ```

    `UNSURE` は正答に数えない（呼び出し側で `False` として渡す）。
    """
    if not commitment_verified or not audio_verified:
        return GeneVerdict.INVALID
    # **構造的な評価不能を Stage 1 の正誤より先に見る。** 別 context が無い gene は
    # 第 1 問の答えに関係なく `NOT_EVALUABLE_S35`。後ろに置くと、同じ構造的欠落が
    # 「たまたま当てたか外したか」で別の verdict に化ける。
    if not has_stage2_pair:
        return GeneVerdict.NOT_EVALUABLE_S35
    if stage1_correct is None:
        return GeneVerdict.INVALID          # Stage 1 の回答が無い
    if not stage1_correct:
        return GeneVerdict.NOT_ESTABLISHED  # Stage 2 へ進まない
    if stage2_correct is None:
        return GeneVerdict.INVALID          # 進んだのに回答が無い
    return (GeneVerdict.PERCEPTIBLE_CANDIDATE if stage2_correct
            else GeneVerdict.NOT_ESTABLISHED)


def s4_gate(perceptible_candidate_count: int) -> str:
    """S4 進行 Gate。`S4_NOT_READY` は S3 FAIL を意味しない。"""
    return ("S4_READY" if perceptible_candidate_count >= MIN_PERCEPTIBLE_GENES_FOR_S4
            else "S4_NOT_READY")


def advancing_genes(stage1_results: Dict[str, bool]) -> List[str]:
    """Stage 1 に正解した gene だけが Stage 2 へ進む。"""
    return sorted(g for g, ok in stage1_results.items() if ok)
