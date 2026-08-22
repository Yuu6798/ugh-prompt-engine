"""genome_s4/s4_spec.py — S4 の凍結定義（設計書 v1.0 §19）。

**凍結定義だけを置く。** I/O・音響処理・動的閾値は禁止。

S4 が扱う gene は `f0` と `duration` の 2 つだけ（§0.2）。Energy / Release は
S3 / S3.5 の記録上は保持されるが、S4 の入力・Gate・出力条件には入れない。
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

_HERE = Path(__file__).resolve().parent
_FOUNDRY = _HERE.parent
for _p in (_HERE, _FOUNDRY / "planb"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from pb_tracks import PerformanceToggles  # noqa: E402

SCHEMA = "voicegenesis-genome-s4/1.0"
ANSWERS_SCHEMA = "voicegenesis-s4-answers/1.0"
FREEZE_SCHEMA = "voicegenesis-genome-architecture/0.1"

#: 耳 pack の pair 選択ハッシュの領域分離プレフィクス（§13.1）。変更禁止。
EAR_SELECTION_DOMAIN = "voicegenesis-s4-ear-v1"


class S4Stop(Exception):
    """§16 BLOCKED / FAILED の停止条件。原因・影響・最小修正案だけを持つ。

    **`s4_spec` に置く。** `s4_runner` に置くと、判定ロジックだけを検査したい側が
    WORLD（`pyworld`）を含む import 閉包を引き込むことになり、その依存が無い環境
    （CI）で不変条件テストが丸ごと skip される。
    """

    def __init__(self, cause: str, impact: str, minimal_fix: str,
                 status: str = "BLOCKED") -> None:
        super().__init__(cause)
        self.cause, self.impact, self.minimal_fix = cause, impact, minimal_fix
        self.status = status

    def as_dict(self) -> Dict[str, str]:
        return {"status": self.status, "cause": self.cause,
                "impact": self.impact, "minimal_fix": self.minimal_fix}


class Gene(str, Enum):
    F0 = "f0"
    DURATION = "duration"


class PairVerdict(str, Enum):
    """§10。**4 状態以外を作らない。**"""

    COMBINABLE = "COMBINABLE"
    UNSUPPORTED = "UNSUPPORTED"
    NOT_EVALUABLE = "NOT_EVALUABLE"
    FAILED = "FAILED"


class Answer(str, Enum):
    A = "A"
    B = "B"
    UNSURE = "UNSURE"


class IdentityAnswer(str, Enum):
    YES = "YES"
    NO = "NO"
    UNSURE = "UNSURE"


#: §3 の 2×2 factorial。**この 4 条件以外を作らない**（E / R / FE / DR / FDE / FDER は禁止）。
CONDITIONS: Dict[str, PerformanceToggles] = {
    "B0": PerformanceToggles(),
    "F": PerformanceToggles(f0=True),
    "D": PerformanceToggles(duration=True),
    "FD": PerformanceToggles(f0=True, duration=True),
}

#: S4 が S3 から再現する 3 条件（§6）。FD は S3 に存在しないので replay 対象外。
REPLAY_CONDITIONS: Tuple[str, ...] = ("B0", "F", "D")

#: 条件名 -> S3 の同名条件（S3 `CONDITIONS` の写し。テストで一致検査する）。
S3_CONDITION_ALIAS: Dict[str, str] = {"B0": "B0", "F": "F", "D": "D"}

#: gene -> (単独条件, 背景条件, metric 名, 良い向き)。**新しい metric を足さない**（§8）。
#:
#: 背景条件 = 相手 gene だけが ON の条件。増分効果は `metric(背景) - metric(FD)`。
GENE_PLAN: Dict[Gene, Tuple[str, str, str, str]] = {
    Gene.F0: ("F", "D", "f0_dev_rmse_cents", "lower"),
    Gene.DURATION: ("D", "F", "note_split_mae_ms", "lower"),
}

#: Identity 補助指標（§8）。donor_lsd_db - identity_lsd_db。正 = Ritsu 側。
IDENTITY_METRIC = "identity_margin_db"

#: §8 が使ってよい metric 名の全集合。ここに無い名前は記録に載せない。
ALLOWED_METRICS: Tuple[str, ...] = (
    "f0_dev_rmse_cents", "note_split_mae_ms", IDENTITY_METRIC)

# --- §11 事前登録値（§24 により Claude は変更してはならない）-----------------
MIN_CANDIDATE_PAIRS = 4
MIN_CONTEXTS = 2
MIN_SUPPORT_RATIO = 0.75
MIN_SUPPORTED_CONTEXTS = 2

# --- §13 人間 Gate の問数（変更禁止）----------------------------------------
#: 耳判定の対象 context（§13.1）。
EAR_CONTEXTS: Tuple[str, ...] = ("terminal_i", "terminal_ri")
#: gene retention ABX = context 2 × gene 2 = 4 問（§13.2）。
ABX_TOTAL = 4
#: Identity = context 2 × 1 = 2 問（§13.3）。
IDENTITY_TOTAL = 2
#: 耳 pack の総問数。
EAR_TOTAL = ABX_TOTAL + IDENTITY_TOTAL

#: §14 filename に現れてはならない語（意味の分かる名前の禁止）。
FORBIDDEN_FILENAME_TOKENS: Tuple[str, ...] = (
    "F0", "f0", "Duration", "duration", "B0", "FD", "terminal_i", "terminal_ri",
    "medial_ri", "terminal_N", "gene", "pjs", "ritsu")

#: S3 側の pair verdict 語彙のうち、候補導出に使う値（§4）。
S3_SUPPORTED = "SUPPORTED"
#: S3 / S3.5 正本に要求する overall（§5 G0-1 / G0-2）。
REQUIRED_S3_OVERALL = "PASS"
REQUIRED_S35_OVERALL = "S4_READY"
REQUIRED_S35_GENE_VERDICT = "PERCEPTIBLE_CANDIDATE"

#: §5 G0-5 の最低被覆。実行に関与する module closure はここから**広げる**方向にのみ動く。
MIN_CLOSURE_FILES: Tuple[str, ...] = (
    "genome_s4/s4_spec.py",
    "genome_s4/s4_runner.py",
    "genome_s4/s4_gates.py",
    "genome_s4/s4_blind.py",
    "genome_s4/s4_report.py",
    "genome_s3/s3_spec.py",
    "genome_s3/s3_runner.py",
    "genome_s3/s3_gates.py",
    "planb/pb_compose.py",
    "planb/pb_tracks.py",
    "planb/pb_world.py",
    "planb_real/pr_identity.py",
    "planb_real/pr_performance.py",
    "planb_real/pr_match.py",
    "planb_real/pr_ladder.py",
    # 再配置素材の同一性（展開物の集約 SHA）を決める実装。pin から漏らさない。
    "planb_real/pr_manifest.py",
)


@dataclass(frozen=True)
class Criteria:
    """§11 の機械 Overall Gate。事前登録値であり走行中に変えない。"""

    min_candidate_pairs: int = MIN_CANDIDATE_PAIRS
    min_contexts: int = MIN_CONTEXTS
    min_support_ratio: float = MIN_SUPPORT_RATIO
    min_supported_contexts: int = MIN_SUPPORTED_CONTEXTS

    def as_dict(self) -> Dict[str, object]:
        return {"min_candidate_pairs": self.min_candidate_pairs,
                "min_contexts": self.min_contexts,
                "min_support_ratio": self.min_support_ratio,
                "min_supported_contexts": self.min_supported_contexts}


def toggles_for(condition: str) -> PerformanceToggles:
    return CONDITIONS[condition]


def is_exact_fd(tg: PerformanceToggles) -> bool:
    """§7 構造 Gate: FD は f0/duration だけが True であること。"""
    return bool(tg.f0 and tg.duration and not tg.energy and not tg.release)


def is_exact_condition(condition: str, tg: PerformanceToggles) -> bool:
    """条件名が要求するトグル状態と厳密に一致するか（§7 / テスト 10–14）。"""
    want = CONDITIONS[condition]
    return (tg.f0, tg.duration, tg.energy, tg.release) == \
           (want.f0, want.duration, want.energy, want.release)


#: `pb_tracks.PerformanceTrack` の Energy / Release 系フィールド名（§7 tripwire 用）。
ENERGY_RELEASE_FIELDS: Tuple[str, ...] = (
    "energy_db", "energy_unit_index", "energy_unit_pos",
    "release", "release.window_frac", "release.taper_db", "release.hold_core")


def ear_selection_hash(s3_sha: str, s35_sha: str, context_id: str, pair_key: str) -> str:
    """§13.1 の決定論 pair 選択ハッシュ。

        SHA256("voicegenesis-s4-ear-v1" + s3_sha + s35_sha + context_id + pair_key)

    effect size・音質・聞きやすさは**入力に含めない**。人間も選ばない。
    """
    h = hashlib.sha256()
    h.update((EAR_SELECTION_DOMAIN + s3_sha + s35_sha + context_id + pair_key)
             .encode("utf-8"))
    return h.hexdigest()


def select_ear_pair(candidates: Sequence[str], s3_sha: str, s35_sha: str,
                    context_id: str) -> Optional[str]:
    """`candidates`（同一 context の pair_key）から hash 昇順先頭を採る（§13.1）。"""
    if not candidates:
        return None
    return min(candidates, key=lambda pk: ear_selection_hash(s3_sha, s35_sha,
                                                             context_id, pk))
