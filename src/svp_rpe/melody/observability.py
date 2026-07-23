"""melody/observability.py — 観測ゲート指標の算出（M1 本体）。

**比較（類似度）を計算する前に**、各 (素材 × 抽出器 × 前処理) について旋律データが
十分かを判定して構造体で返す。基準未達なら `status="insufficient"` を理由つきで
返し、後段（比較）を呼ばない。これは `docs/melody_observability.md`（M1）が定める
「比較の前に観測を問う」規律の実装である。

抽出器非依存の設計:
    pyin / CREPE / Melodia / basic-pitch はいずれもフレーム単位の F0（+ voicing/
    confidence）またはノート系列を返す。本モジュールは共通中間表現
    `MelodyObservation`（フレーム F0 + optional ノート）へ正規化し、
    `assess_observability` は抽出器の実装詳細を知らずにゲート指標を算出する。
    ノート系列が与えられなければ（pyin / CREPE / Melodia のようなフレーム系
    抽出器）、`notes_from_frames` がフレームからノートを導出する。

隔離方針:
    ここで返す `MelodyObservabilityReport` は観測レポートであって、
    `PhysicalRPE.melody_contour` 等の決定論 RPE フィールドへは一切書き戻さない
    （`docs/learned_models_policy.md` Section 2）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "MelodyNote",
    "MelodyObservation",
    "ObservabilityThresholds",
    "MelodyObservabilityReport",
    "notes_from_frames",
    "cross_extractor_agreement",
    "assess_observability",
]

# A4 = 440 Hz = MIDI 69。Hz→MIDI 変換の基準。
_A4_HZ = 440.0
_A4_MIDI = 69.0


def _hz_to_midi(hz: float) -> float:
    """Hz を（連続値の）MIDI ノート番号へ変換する。hz<=0 は NaN 相当扱い。"""
    if hz <= 0.0 or not math.isfinite(hz):
        return math.nan
    return _A4_MIDI + 12.0 * math.log2(hz / _A4_HZ)


@dataclass(frozen=True)
class MelodyNote:
    """フレーム系列から導出（またはノート系抽出器が直接供給）した 1 ノート。

    `pitch_midi` は連続値（フレーム中央値）を許容する（量子化はゲート判定側の
    `octave_jump` 計算内で半音丸めして行う）。
    """

    start_sec: float
    end_sec: float
    pitch_midi: float
    confidence: float


@dataclass(frozen=True)
class MelodyObservation:
    """抽出器出力を正規化した観測ゲートの共通入力。

    すべての抽出器（pyin / CREPE / Melodia / basic-pitch）はこの共通中間表現へ
    落とし込まれ、`assess_observability` が抽出器非依存にゲート指標を算出する。

    Attributes
    ----------
    route
        観測を生んだ経路ラベル（例 ``demucs_vocals_then_crepe``）。レポートへ
        そのまま転記される。
    source_model
        抽出器の provenance 識別子（例 ``crepe:predict`` / ``librosa:pyin``）。
    frame_times, frame_hz, frame_confidence
        フレーム単位の F0 トラック。``frame_hz`` は無声/無ピッチフレームで 0.0。
        ``frame_confidence`` は [0, 1]。3 配列は同じ長さでなければならない。
    notes
        ノート系抽出器（basic-pitch）が供給するノート系列。空なら
        `notes_from_frames` がフレームから導出する。
    total_duration_sec
        対象区間の総尺（秒）。``None`` ならフレーム時刻の最大値で近似する。
        voiced_coverage の分母に用いる。
    """

    route: str
    source_model: str
    frame_times: Tuple[float, ...] = ()
    frame_hz: Tuple[float, ...] = ()
    frame_confidence: Tuple[float, ...] = ()
    notes: Tuple[MelodyNote, ...] = ()
    total_duration_sec: Optional[float] = None

    def __post_init__(self) -> None:
        lengths = {len(self.frame_times), len(self.frame_hz), len(self.frame_confidence)}
        if len(lengths) != 1:
            raise ValueError(
                "frame_times, frame_hz, frame_confidence must have the same length"
            )


@dataclass(frozen=True)
class ObservabilityThresholds:
    """M0 で事前登録する観測ゲート閾値（M1 で後から緩めない）。

    `docs/melody_observability.md` の事前登録節と
    `tests/fixtures/melody_bench/registry.yaml` が single source of truth。
    """

    min_voiced_coverage: float
    min_note_count: int
    min_phrase_count: int
    min_confidence_mean: float
    max_low_confidence_rate: float
    max_octave_jump_rate: float
    voiced_confidence_floor: float
    low_confidence_floor: float
    min_cross_extractor_agreement: Optional[float] = None
    # ノート導出パラメータ（フレーム → ノート）。
    note_min_run_frames: int = 3
    median_filter_frames: int = 5
    phrase_gap_sec: float = 0.6
    octave_jump_semitones: float = 11.0

    @classmethod
    def from_registry(cls, mapping: Dict[str, Any]) -> "ObservabilityThresholds":
        """`registry.yaml` の ``observation_gate`` マッピングから構築する。

        未知キーは無視せず fail-fast（fixity と同じく事前登録の逸脱を検出する）
        したいので、既知フィールドのみを受け取り、余剰キーがあれば例外を送出する。
        """
        known = {
            "min_voiced_coverage",
            "min_note_count",
            "min_phrase_count",
            "min_confidence_mean",
            "max_low_confidence_rate",
            "max_octave_jump_rate",
            "voiced_confidence_floor",
            "low_confidence_floor",
            "min_cross_extractor_agreement",
            "note_min_run_frames",
            "median_filter_frames",
            "phrase_gap_sec",
            "octave_jump_semitones",
        }
        unknown = set(mapping) - known
        if unknown:
            raise ValueError(
                f"unknown observation_gate keys (pre-registration violation): "
                f"{sorted(unknown)}"
            )
        return cls(**{key: mapping[key] for key in mapping})


@dataclass(frozen=True)
class MelodyObservabilityReport:
    """観測ゲートの判定結果（設計 §4.1 の構造体）。"""

    voiced_coverage: float
    note_count: int
    phrase_count: int
    confidence_mean: float
    low_confidence_rate: float
    octave_jump_rate: float
    cross_extractor_agreement: Optional[float]
    status: str  # "sufficient" | "insufficient"
    route: str
    reasons: List[str] = field(default_factory=list)

    @property
    def sufficient(self) -> bool:
        return self.status == "sufficient"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "voiced_coverage": round(self.voiced_coverage, 4),
            "note_count": self.note_count,
            "phrase_count": self.phrase_count,
            "confidence_mean": round(self.confidence_mean, 4),
            "low_confidence_rate": round(self.low_confidence_rate, 4),
            "octave_jump_rate": round(self.octave_jump_rate, 4),
            "cross_extractor_agreement": (
                None
                if self.cross_extractor_agreement is None
                else round(self.cross_extractor_agreement, 4)
            ),
            "status": self.status,
            "route": self.route,
            "reasons": list(self.reasons),
        }


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        return math.nan
    mid = count // 2
    if count % 2 == 1:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def notes_from_frames(
    frame_times: Sequence[float],
    frame_hz: Sequence[float],
    frame_confidence: Sequence[float],
    thresholds: ObservabilityThresholds,
) -> List[MelodyNote]:
    """フレーム F0 トラックからノート系列を導出する（Phase 0 スパイクと同手法）。

    手順:
    1. voiced フレーム（``confidence >= voiced_confidence_floor`` かつ ``hz > 0``）
       のみ採用し、Hz→MIDI 変換。
    2. 半音丸めした MIDI 値に対し中央値フィルタ（``median_filter_frames``）で
       短いスパイクを均す。
    3. 同一（丸め）半音が ``note_min_run_frames`` フレーム以上連続したランを
       1 ノートにまとめ、``pitch_midi`` はランの連続 MIDI 中央値、
       ``confidence`` はランの平均 confidence とする。

    決定論: 乱数を用いない。同一フレーム入力 → 同一ノート系列。
    """
    voiced_idx = [
        i
        for i, (hz, conf) in enumerate(zip(frame_hz, frame_confidence))
        if hz > 0.0 and conf >= thresholds.voiced_confidence_floor
    ]
    if not voiced_idx:
        return []

    # 半音丸めした MIDI（None = 無声）を全フレーム分並べる。
    rounded: List[Optional[int]] = [None] * len(frame_hz)
    continuous: List[float] = [math.nan] * len(frame_hz)
    for i in voiced_idx:
        midi = _hz_to_midi(float(frame_hz[i]))
        if math.isnan(midi):
            continue
        rounded[i] = int(round(midi))
        continuous[i] = midi

    rounded = _median_filter_semitones(rounded, thresholds.median_filter_frames)

    notes: List[MelodyNote] = []
    run_start: Optional[int] = None
    run_value: Optional[int] = None
    for i in range(len(rounded) + 1):
        current = rounded[i] if i < len(rounded) else None
        if run_value is not None and current == run_value:
            continue
        # ラン終了
        if run_value is not None and run_start is not None:
            run_len = i - run_start
            if run_len >= thresholds.note_min_run_frames:
                notes.append(
                    _note_from_run(
                        run_start, i, frame_times, continuous, frame_confidence
                    )
                )
        if current is None:
            run_start, run_value = None, None
        else:
            run_start, run_value = i, current
    return notes


def _median_filter_semitones(
    rounded: List[Optional[int]], kernel: int
) -> List[Optional[int]]:
    """None を跨がない半音列の中央値フィルタ（kernel は奇数へ丸める）。"""
    if kernel <= 1:
        return list(rounded)
    if kernel % 2 == 0:
        kernel += 1
    half = kernel // 2
    out: List[Optional[int]] = list(rounded)
    for i, value in enumerate(rounded):
        if value is None:
            continue
        window = [
            rounded[j]
            for j in range(max(0, i - half), min(len(rounded), i + half + 1))
            if rounded[j] is not None
        ]
        if window:
            out[i] = int(round(_median([float(v) for v in window])))
    return out


def _note_from_run(
    start_idx: int,
    end_idx: int,
    frame_times: Sequence[float],
    continuous: Sequence[float],
    frame_confidence: Sequence[float],
) -> MelodyNote:
    pitches = [continuous[j] for j in range(start_idx, end_idx) if not math.isnan(continuous[j])]
    confs = [float(frame_confidence[j]) for j in range(start_idx, end_idx)]
    start_sec = float(frame_times[start_idx])
    # end は次フレーム開始（無ければ最終フレーム時刻）で近似。
    end_sec = float(frame_times[end_idx]) if end_idx < len(frame_times) else float(frame_times[-1])
    return MelodyNote(
        start_sec=round(start_sec, 4),
        end_sec=round(max(end_sec, start_sec), 4),
        pitch_midi=round(_median(pitches), 4) if pitches else math.nan,
        confidence=round(sum(confs) / len(confs), 4) if confs else 0.0,
    )


def _phrase_count(notes: Sequence[MelodyNote], phrase_gap_sec: float) -> int:
    """ノート間ギャップが ``phrase_gap_sec`` を超えたら新フレーズとみなす。"""
    if not notes:
        return 0
    ordered = sorted(notes, key=lambda n: n.start_sec)
    phrases = 1
    for prev, current in zip(ordered, ordered[1:]):
        if current.start_sec - prev.end_sec > phrase_gap_sec:
            phrases += 1
    return phrases


def _octave_jump_rate(notes: Sequence[MelodyNote], octave_jump_semitones: float) -> float:
    """連続ノート遷移のうちオクターブ級ジャンプ（|Δ| >= 閾値半音）の割合。

    ノートが 2 未満なら遷移が無いので 0.0（note/phrase count 側の理由で拾われる）。
    """
    valid = [n for n in notes if not math.isnan(n.pitch_midi)]
    if len(valid) < 2:
        return 0.0
    jumps = 0
    transitions = 0
    for prev, current in zip(valid, valid[1:]):
        transitions += 1
        if abs(current.pitch_midi - prev.pitch_midi) >= octave_jump_semitones:
            jumps += 1
    return jumps / transitions if transitions else 0.0


def cross_extractor_agreement(
    notes_a: Sequence[MelodyNote],
    notes_b: Sequence[MelodyNote],
) -> Optional[float]:
    """2 抽出器のノート系列の一致度を [0, 1] で返す（音程列 LCS 比）。

    ピッチクラス（半音丸め）系列の最長共通部分列長を、短い方の系列長で正規化する。
    どちらかが空なら ``None``（一致度を定義できない）。移調不変性は M2 の課題なので、
    ここでは素の半音列で比較する（M1 は「観測できたか」の弁別材料に留める）。
    """
    seq_a = [int(round(n.pitch_midi)) for n in notes_a if not math.isnan(n.pitch_midi)]
    seq_b = [int(round(n.pitch_midi)) for n in notes_b if not math.isnan(n.pitch_midi)]
    if not seq_a or not seq_b:
        return None
    lcs = _lcs_length(seq_a, seq_b)
    return lcs / min(len(seq_a), len(seq_b))


def _lcs_length(a: Sequence[int], b: Sequence[int]) -> int:
    prev = [0] * (len(b) + 1)
    for value_a in a:
        current = [0] * (len(b) + 1)
        for j, value_b in enumerate(b, start=1):
            if value_a == value_b:
                current[j] = prev[j - 1] + 1
            else:
                current[j] = max(prev[j], current[j - 1])
        prev = current
    return prev[-1]


def assess_observability(
    observation: MelodyObservation,
    thresholds: ObservabilityThresholds,
    *,
    reference_notes: Optional[Sequence[MelodyNote]] = None,
) -> MelodyObservabilityReport:
    """観測 1 件をゲート判定し `MelodyObservabilityReport` を返す。

    ノートが与えられなければフレームから導出する。基準未達フィールドは
    ``reasons`` に理由文字列を積み、いずれか 1 つでも未達なら
    ``status="insufficient"``。**この関数は比較（後段）を呼ばない** —
    観測が十分かどうかを判定するだけである。

    Parameters
    ----------
    reference_notes
        指定時は cross-extractor 一致度を算出する第 2 抽出器のノート系列
        （M1 の任意計測。``min_cross_extractor_agreement`` が None なら
        一致度は算出するがゲート条件には用いない）。
    """
    # ノート導出（ノート系抽出器は供給済み、フレーム系は導出）。
    if observation.notes:
        notes: List[MelodyNote] = list(observation.notes)
    else:
        notes = notes_from_frames(
            observation.frame_times,
            observation.frame_hz,
            observation.frame_confidence,
            thresholds,
        )

    voiced_coverage = _voiced_coverage(observation, thresholds)
    confidence_mean, low_confidence_rate = _confidence_stats(observation, notes, thresholds)
    note_count = len(notes)
    phrase_count = _phrase_count(notes, thresholds.phrase_gap_sec)
    octave_jump_rate = _octave_jump_rate(notes, thresholds.octave_jump_semitones)

    agreement: Optional[float] = None
    if reference_notes is not None:
        agreement = cross_extractor_agreement(notes, reference_notes)

    reasons: List[str] = []
    if voiced_coverage < thresholds.min_voiced_coverage:
        reasons.append(
            f"voiced_coverage {voiced_coverage:.3f} < min {thresholds.min_voiced_coverage:.3f}"
        )
    if note_count < thresholds.min_note_count:
        reasons.append(f"note_count {note_count} < min {thresholds.min_note_count}")
    if phrase_count < thresholds.min_phrase_count:
        reasons.append(f"phrase_count {phrase_count} < min {thresholds.min_phrase_count}")
    if confidence_mean < thresholds.min_confidence_mean:
        reasons.append(
            f"confidence_mean {confidence_mean:.3f} < min {thresholds.min_confidence_mean:.3f}"
        )
    if low_confidence_rate > thresholds.max_low_confidence_rate:
        reasons.append(
            f"low_confidence_rate {low_confidence_rate:.3f} > max "
            f"{thresholds.max_low_confidence_rate:.3f}"
        )
    if octave_jump_rate > thresholds.max_octave_jump_rate:
        reasons.append(
            f"octave_jump_rate {octave_jump_rate:.3f} > max "
            f"{thresholds.max_octave_jump_rate:.3f}"
        )
    if (
        thresholds.min_cross_extractor_agreement is not None
        and agreement is not None
        and agreement < thresholds.min_cross_extractor_agreement
    ):
        reasons.append(
            f"cross_extractor_agreement {agreement:.3f} < min "
            f"{thresholds.min_cross_extractor_agreement:.3f}"
        )

    status = "sufficient" if not reasons else "insufficient"
    return MelodyObservabilityReport(
        voiced_coverage=voiced_coverage,
        note_count=note_count,
        phrase_count=phrase_count,
        confidence_mean=confidence_mean,
        low_confidence_rate=low_confidence_rate,
        octave_jump_rate=octave_jump_rate,
        cross_extractor_agreement=agreement,
        status=status,
        route=observation.route,
        reasons=reasons,
    )


def _voiced_coverage(
    observation: MelodyObservation, thresholds: ObservabilityThresholds
) -> float:
    """有声フレーム（hz>0 かつ confidence>=floor）が全フレームに占める割合。"""
    total = len(observation.frame_hz)
    if total == 0:
        return 0.0
    voiced = sum(
        1
        for hz, conf in zip(observation.frame_hz, observation.frame_confidence)
        if hz > 0.0 and conf >= thresholds.voiced_confidence_floor
    )
    return voiced / total


def _confidence_stats(
    observation: MelodyObservation,
    notes: Sequence[MelodyNote],
    thresholds: ObservabilityThresholds,
) -> Tuple[float, float]:
    """(confidence_mean, low_confidence_rate) を返す。

    フレーム系抽出器では有声フレームの confidence を、ノート系抽出器
    （フレーム無し）ではノート confidence を母集団に用いる。低信頼率は
    ``low_confidence_floor`` 未満の割合。
    """
    if observation.frame_hz:
        confs = [
            float(conf)
            for hz, conf in zip(observation.frame_hz, observation.frame_confidence)
            if hz > 0.0 and conf >= thresholds.voiced_confidence_floor
        ]
    else:
        confs = [n.confidence for n in notes]
    if not confs:
        return 0.0, 1.0
    mean = sum(confs) / len(confs)
    low = sum(1 for c in confs if c < thresholds.low_confidence_floor) / len(confs)
    return mean, low
