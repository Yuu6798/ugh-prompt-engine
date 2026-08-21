"""run8/s7_spec.py — run 8（S7）の凍結定数と enum。

**凍結定数・enum・純粋な語彙だけを置く。** 研究判断・I/O・音響処理は禁止
（`genome_s3/s3_spec.py` / `genome_s35/s35_spec.py` と同じ流儀）。

正本 = [`DESIGN_S7_run8.md`](../DESIGN_S7_run8.md)。本モジュールが持つのは
その設計書が**すでに事前登録した数値**の写しであり、ここで新しい閾値を
作らない（乖離した場合は設計書が勝つ）。
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, Final, Tuple

# --- schema 名 -------------------------------------------------------------
LEDGER_SCHEMA: Final = "target_exposure_ledger/0.1"
TRF_SCHEMA: Final = "terminal_release_failure/0.1"
TRF_SPEC_SCHEMA: Final = "s7-trf-measurement-spec/1.0"
B2_SCHEMA: Final = "s7-b2-verdict-algebra/0.1"
CANDIDATE_SPACE_SCHEMA: Final = "s7-b1-candidate-space/0.1"
CALIBRATION_SET_SCHEMA: Final = "s7-b1-calibration-set/0.1"
SELECTION_RULE_SCHEMA: Final = "s7-b1-selection-rule/0.1"


class Verdict(str, Enum):
    """三値裁定（§3 / §12-0-C）。`undetermined` を既定に置く。"""

    SUPPORTED = "supported"
    REFUTED = "refuted"
    UNDETERMINED = "undetermined"


#: H-TTD の**閉じ方**（User 裁定 2026-08-21）。`undetermined`（判定に至らなかった）と
#: 区別して、「現在の corpus には裁定できるだけの標的イベントが存在しなかった」ことを
#: 名前で残す。**仮説が反証されたという意味ではない**。
#: 救済（閾値を下げる / d3+d4 を合算する / pitch 層を潰す）は禁止。
NOT_EVALUABLE_INSUFFICIENT_SUPPORT: Final = "NOT_EVALUABLE_INSUFFICIENT_SUPPORT"


# --- §3 台帳 ---------------------------------------------------------------
#: 先行尺 bin は**秒**で切る（beats を使わない・§3 の 2026-08-20 訂正）。
DURATION_BINS: Final[Tuple[Tuple[str, float, float], ...]] = (
    ("d0", 0.0, 0.5),
    ("d1", 0.5, 1.0),
    ("d2", 1.0, 1.5),
    ("d3", 1.5, 2.5),
    ("d4", 2.5, float("inf")),
)

#: 主層（primary stratum）の先行尺 bin = `>= 1.5 s`。
PRIMARY_DURATION_BINS: Final[Tuple[str, ...]] = ("d3", "d4")
PRIMARY_POSITION: Final = "utterance_final"
PRIMARY_TRANSITION: Final = "ri_to_SP"

PITCH_BINS: Final[Tuple[str, ...]] = ("low", "mid", "high")
#: 話者内三分位のカット点（§3）。
PITCH_QUANTILES: Final[Tuple[float, float]] = (33.3, 66.7)
PITCH_BIN_UNKNOWN: Final = "unknown"

MODALITIES: Final[Tuple[str, ...]] = ("real_song", "synthetic_song", "VCV", "speech")

#: 層別判定のゼロ分岐（§3「判定順」）。
MIN_ELIGIBLE_TERMINAL_COUNT: Final = 20
DENSITY_RATIO_THRESHOLD: Final = 2.0
RATIO_SEPARATED_BY_ZERO: Final = "separated_by_zero"

#: overall H-TTD の多数決（§3）。
MIN_SCORED_STRATA: Final = 2
MAJORITY_FRACTION: Final = 2.0 / 3.0

#: `events` 内訳を必須とする層（§3・2026-08-21 追加分）。
EVENT_DETAIL_REQUIRED_TRANSITIONS: Final[Tuple[str, ...]] = ("ri_to_SP",)
EVENT_DETAIL_REQUIRED_POSITION: Final = "utterance_final"

# --- §5 TRF 主観測 4 値 ----------------------------------------------------
#: B-1 の既定スコープ（§7-0-(2b)）。Gate の primary 候補になれるのはこの 4 軸のみ。
PRIMARY_AXES: Final[Tuple[str, ...]] = (
    "excess_tail_voiced_ms",
    "release_after_score_boundary_ms",
    "tail_f0_persistence",
    "terminal_mel_persistence",
)

#: 軸の単位系。ms 軸と ratio 軸で許容差の様式が違う（selection rule / ε）。
AXIS_KIND: Final[Dict[str, str]] = {
    "excess_tail_voiced_ms": "ms",
    "release_after_score_boundary_ms": "ms",
    "tail_f0_persistence": "ratio",
    "terminal_mel_persistence": "ratio",
}

#: 各軸が依存する候補次元（`s7_b1_candidate_space.json` の axis_dimension_map と一致）。
AXIS_CANDIDATE_KIND: Final[Dict[str, str]] = {
    "excess_tail_voiced_ms": "voicing",
    "release_after_score_boundary_ms": "voicing",
    "tail_f0_persistence": "voicing",
    "terminal_mel_persistence": "mel",
}

#: 未凍結の補助軸（診断レポートには残すが Gate には入れない・§7-0-(2b)）。
AUXILIARY_AXES_NOT_IN_GATE: Final[Tuple[str, ...]] = (
    "N_similarity_delta",
    "i_reference_distance",
    "hnr_median_db_p1",
    "hnr_median_db_p2",
    "hnr_delta_db",
    "vowel_drift_l1",
    "energy_decay_slope",
)


class AxisStatus(str, Enum):
    FROZEN = "frozen"
    UNAVAILABLE = "unavailable"


# --- §2-3 仮説 -------------------------------------------------------------
#: B-2 が三値へ機械変換する対象（H0–H5）。H-TTD は §3 の台帳側、
#: H-dur / H-f0 / H-SP / H-rescue は §7G-5 側で裁定される（B-2 の対象外）。
B2_HYPOTHESES: Final[Tuple[str, ...]] = ("H0", "H1", "H2", "H3", "H4", "H5")

#: §5-1 の話者内 3 対照。
CONTRAST_PROBES: Final[Dict[str, str]] = {
    "P-RI-FINAL": "target",
    "P-N-FINAL": "control",
    "P-RI-MEDIAL": "control",
    "P-I-FINAL": "control",
}

#: §7-0-(7) の contrast 計算に使うセル分類。
TARGET_CELL_KIND: Final = "target"
CONTROL_CELL_KIND: Final = "control"

#: 校正・Gate 計算から除外される status（§5-0）。
STATUS_RINGING_UNCORRECTED: Final = "ringing_uncorrected"
STATUS_RINGING_UNCORRECTED_GROUP: Final = "ringing_uncorrected_group"
STATUS_DEGENERATE_AXIS: Final = "degenerate_axis"
STATUS_ORIENTATION_CONFLICT: Final = "orientation_conflict"
STATUS_INSUFFICIENT_CLASS_SUPPORT: Final = "insufficient_class_support"
