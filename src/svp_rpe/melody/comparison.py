"""melody/comparison.py — M3c: 軸別類似 + MelodyComparisonReport。

`representation.py`（M3a）の正規化系列と `alignment.py`（M3b）のギャップ許容整列の
上で、旋律の軸別類似（contour / interval / rhythm）を算出し、根拠つきの
`MelodyComparisonReport` を返す。総合同一性スコア・軸間重み付き平均は一切作らない
（`docs/DESIGN_M3_melody_comparator.md` §8）。

比較の入口では常に両観測へ M1 観測ゲート（`observability.assess_observability`）を
課す。insufficient な観測同士・被覆不足の対は「測れない」を正直に `not_comparable`
として返し、類似値を出さない。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from svp_rpe.melody.alignment import align_melodies
from svp_rpe.melody.observability import (
    MelodyNote,
    MelodyObservation,
    ObservabilityThresholds,
    assess_observability,
    notes_from_frames,
)
from svp_rpe.melody.representation import (
    EvidenceThresholdsConfig,
    M3ComparisonConfig,
    build_sequences,
    sequence_sha256,
    split_phrases,
)

__all__ = ["MelodyComparisonReport", "compare_melodies"]

# 軸名は固定 3 種（設計 §5）。総合スコアではなく、常にこの 3 軸で報告する。
_AXES: Tuple[str, ...] = ("contour", "interval", "rhythm")
_VERDICT_RANK: Dict[str, int] = {"none": 0, "weak": 1, "strong": 2}


@dataclass(frozen=True)
class MelodyComparisonReport:
    """旋律比較 1 件の結果（軸別・総合スコアなし）。

    ``evidence`` / ``axis_evidence`` は「同一旋律の証拠」であって
    ``preserved`` 判定ではない（設計 §5）。
    """

    axes: Dict[str, Optional[float]]
    coverage: Dict[str, float]
    octave_artifact_suspected: bool
    evidence: str  # "strong" | "weak" | "none" | "not_comparable"
    axis_evidence: Dict[str, str]  # 軸別 "strong"|"weak"|"none"|"uncalibrated"
    reasons: List[str] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "axes": dict(self.axes),
            "coverage": dict(self.coverage),
            "octave_artifact_suspected": self.octave_artifact_suspected,
            "evidence": self.evidence,
            "axis_evidence": dict(self.axis_evidence),
            "reasons": list(self.reasons),
            "provenance": dict(self.provenance),
        }


def _derive_notes(
    observation: MelodyObservation, thresholds: ObservabilityThresholds
) -> List[MelodyNote]:
    """観測からノート列を導く（ノート系抽出器はそのまま、フレーム系は導出）。

    `assess_observability` 内部のノート導出と同一の分岐（`extractors._observation_notes`
    と同型）を用いる——比較が見るノート列はゲート判定が見たノート列と一致しなければ
    ならない。
    """
    if observation.notes:
        return list(observation.notes)
    return notes_from_frames(
        observation.frame_times,
        observation.frame_hz,
        observation.frame_confidence,
        thresholds,
    )


def _not_comparable(
    *,
    route_a: str,
    route_b: str,
    reasons: List[str],
    coverage: Dict[str, float],
    provenance_extra: Optional[Dict[str, Any]],
) -> MelodyComparisonReport:
    provenance: Dict[str, Any] = {"route_a": route_a, "route_b": route_b}
    if provenance_extra:
        provenance.update(provenance_extra)
    return MelodyComparisonReport(
        axes={axis: None for axis in _AXES},
        coverage=coverage,
        octave_artifact_suspected=False,
        evidence="not_comparable",
        axis_evidence={},
        reasons=reasons,
        provenance=provenance,
    )


def _derive_evidence(
    axes: Dict[str, Optional[float]],
    evidence_thresholds: EvidenceThresholdsConfig,
) -> Tuple[Dict[str, str], str, List[str]]:
    """軸別 sim 値 + 校正済み（または未校正）閾値 → (axis_evidence, evidence, reasons)。

    総合スコアではなく、軸別 verdict の**最強値**（strong > weak > none）を
    `evidence` として報告する。軸が割れたらそのまま `axis_evidence` に残し、
    `axes_disagree(...)` を reasons に積む（隠さない）。
    """
    reasons: List[str] = []
    if evidence_thresholds.status == "uncalibrated":
        # 未校正の計器は証拠を出せない（軸の生値は axes に残る）。
        axis_evidence = {axis: "uncalibrated" for axis in _AXES}
        reasons.append("evidence_thresholds_uncalibrated")
        return axis_evidence, "none", reasons

    axis_bounds = evidence_thresholds.axes or {}
    computed: Dict[str, str] = {}
    for axis in _AXES:
        sim = axes.get(axis)
        if sim is None:
            reasons.append(f"axis_not_computable:{axis}")
            continue
        bounds = axis_bounds.get(axis)
        if bounds is None:
            reasons.append(f"axis_thresholds_missing:{axis}")
            continue
        strong_min = bounds["strong_min"]
        none_max = bounds["none_max"]
        if sim >= strong_min:
            verdict = "strong"
        elif sim <= none_max:
            verdict = "none"
        else:
            verdict = "weak"
        computed[axis] = verdict

    if not computed:
        reasons.append("no_axes_evidence_computable")
        return computed, "none", reasons

    distinct = set(computed.values())
    evidence = max(distinct, key=lambda v: _VERDICT_RANK[v])
    if len(distinct) > 1:
        disagreement = ", ".join(f"{axis}={verdict}" for axis, verdict in computed.items())
        reasons.append(f"axes_disagree({disagreement})")
    return computed, evidence, reasons


def compare_melodies(
    observation_a: MelodyObservation,
    observation_b: MelodyObservation,
    *,
    observability_thresholds: ObservabilityThresholds,
    config: M3ComparisonConfig,
    provenance_extra: Optional[Dict[str, Any]] = None,
) -> MelodyComparisonReport:
    """2 つの旋律観測を比較し、軸別類似 + 根拠つきの `MelodyComparisonReport` を返す。

    処理順（`docs/DESIGN_M3_melody_comparator.md` §5 / M3 実装 memo）:
    1. M1 観測ゲート（insufficient なら `not_comparable`）
    2. ノート導出 3. 表現(M3a) 4. 整列(M3b) 5. 被覆下限ゲート
    6. 軸類似 7. オクターブ折返しガード 8. evidence 導出 9. provenance
    """
    # 1) M1 観測ゲート。
    report_a = assess_observability(observation_a, observability_thresholds)
    report_b = assess_observability(observation_b, observability_thresholds)
    if report_a.status == "insufficient" or report_b.status == "insufficient":
        reasons: List[str] = []
        if report_a.status == "insufficient":
            reasons.append(
                f"observation_gate_insufficient_a: {'; '.join(report_a.reasons)}"
            )
        if report_b.status == "insufficient":
            reasons.append(
                f"observation_gate_insufficient_b: {'; '.join(report_b.reasons)}"
            )
        return _not_comparable(
            route_a=observation_a.route,
            route_b=observation_b.route,
            reasons=reasons,
            coverage={},
            provenance_extra=provenance_extra,
        )

    # 2) ノート導出（ゲート判定が見たノート列と同一の分岐）。
    notes_a = _derive_notes(observation_a, observability_thresholds)
    notes_b = _derive_notes(observation_b, observability_thresholds)

    # 3) 表現（M3a）。
    seqs_a = build_sequences(notes_a, config)
    seqs_b = build_sequences(notes_b, config)
    phrase_gap_sec = config.alignment.phrase_gap_sec
    phrases_a = split_phrases(notes_a, phrase_gap_sec)
    phrases_b = split_phrases(notes_b, phrase_gap_sec)

    # 4) 整列（M3b）+ 被覆信号。
    alignment_result = align_melodies(seqs_a, seqs_b, phrases_a, phrases_b, config)
    coverage = {
        "aligned_note_fraction_a": alignment_result.coverage.aligned_note_fraction_a,
        "aligned_note_fraction_b": alignment_result.coverage.aligned_note_fraction_b,
        "phrase_coverage_a": alignment_result.coverage.phrase_coverage_a,
        "phrase_coverage_b": alignment_result.coverage.phrase_coverage_b,
    }

    # 5) 被覆下限ゲート。
    min_fraction = min(
        alignment_result.coverage.aligned_note_fraction_a,
        alignment_result.coverage.aligned_note_fraction_b,
    )
    floor = config.coverage.floor
    if min_fraction < floor:
        return _not_comparable(
            route_a=observation_a.route,
            route_b=observation_b.route,
            reasons=[
                f"insufficient_overlap(min_fraction={min_fraction:.4f}, floor={floor:.4f})"
            ],
            coverage=coverage,
            provenance_extra=provenance_extra,
        )

    # 6) 軸類似（整列カラム上のみ）。フレーズ対応ごとにフレーズ内表現を再構築し、
    # `NoteAlignment.pairs`（フレーズ内 interval index）で値を引く。
    reasons = []
    interval_total = 0
    interval_matched_folded = 0
    interval_matched_raw = 0
    contour_total = 0
    contour_matched = 0
    dur_total = 0
    dur_matched = 0
    ioi_total = 0
    ioi_matched = 0

    for corr in alignment_result.phrase_correspondences:
        pairs = corr.note_alignment.pairs
        if not pairs:
            continue
        phrase_a_notes = phrases_a[corr.phrase_index_a]
        phrase_b_notes = phrases_b[corr.phrase_index_b]
        phrase_seqs_a = build_sequences(phrase_a_notes, config)
        phrase_seqs_b = build_sequences(phrase_b_notes, config)
        pair_set = set(pairs)
        for ia, ib in pairs:
            interval_total += 1
            folded_a = phrase_seqs_a.intervals_folded[ia]
            folded_b = phrase_seqs_b.intervals_folded[ib]
            if folded_a == folded_b:
                interval_matched_folded += 1
            raw_a = phrase_seqs_a.intervals_raw[ia]
            raw_b = phrase_seqs_b.intervals_raw[ib]
            if raw_a == raw_b:
                interval_matched_raw += 1

            contour_total += 1
            if phrase_seqs_a.contour[ia] == phrase_seqs_b.contour[ib]:
                contour_matched += 1

            dur_a = phrase_seqs_a.duration_log2_ratios[ia]
            dur_b = phrase_seqs_b.duration_log2_ratios[ib]
            if dur_a is not None and dur_b is not None:
                dur_total += 1
                if dur_a == dur_b:
                    dur_matched += 1

            # ioi 比は「直前カラムも連続整列」のカラムのみ比較する（両側とも
            # index が 1 前のペアが整列済みであること）。
            if (ia - 1, ib - 1) in pair_set:
                ioi_a = phrase_seqs_a.ioi_log2_ratios[ia]
                ioi_b = phrase_seqs_b.ioi_log2_ratios[ib]
                if ioi_a is not None and ioi_b is not None:
                    ioi_total += 1
                    if ioi_a == ioi_b:
                        ioi_matched += 1

    axes: Dict[str, Optional[float]] = {}
    if interval_total > 0:
        axes["interval"] = interval_matched_folded / interval_total
    else:
        axes["interval"] = None
        reasons.append("interval_not_computable")

    if contour_total > 0:
        axes["contour"] = contour_matched / contour_total
    else:
        axes["contour"] = None
        reasons.append("contour_not_computable")

    rhythm_denominator = dur_total + ioi_total
    if rhythm_denominator > 0:
        axes["rhythm"] = (dur_matched + ioi_matched) / rhythm_denominator
    else:
        axes["rhythm"] = None
        reasons.append("rhythm_not_computable")

    # 7) オクターブ折返しガード: 折返し類似と生類似の乖離を隠さず記録する
    # （判定自体は折返し側=interval 軸を用いる）。
    octave_artifact_suspected = False
    if interval_total > 0:
        sim_folded = interval_matched_folded / interval_total
        sim_raw = interval_matched_raw / interval_total
        if sim_folded - sim_raw > config.representation.octave_artifact_divergence:
            octave_artifact_suspected = True
            reasons.append(
                f"octave_artifact_suspected(folded={sim_folded:.4f}, raw={sim_raw:.4f})"
            )

    # 8) evidence 導出（機械導出のみ・総合スコア禁止）。
    axis_evidence, evidence, evidence_reasons = _derive_evidence(
        axes, config.evidence_thresholds
    )
    reasons.extend(evidence_reasons)

    # 9) provenance。
    provenance: Dict[str, Any] = {
        "route_a": observation_a.route,
        "route_b": observation_b.route,
        "sequence_sha256_a": sequence_sha256(seqs_a),
        "sequence_sha256_b": sequence_sha256(seqs_b),
        "aligned_pair_count": interval_total,
        "phrase_correspondence_count": len(alignment_result.phrase_correspondences),
        "unmatched_phrase_count_a": len(alignment_result.unmatched_phrase_indices_a),
        "unmatched_phrase_count_b": len(alignment_result.unmatched_phrase_indices_b),
    }
    if provenance_extra:
        provenance.update(provenance_extra)

    return MelodyComparisonReport(
        axes=axes,
        coverage=coverage,
        octave_artifact_suspected=octave_artifact_suspected,
        evidence=evidence,
        axis_evidence=axis_evidence,
        reasons=reasons,
        provenance=provenance,
    )
