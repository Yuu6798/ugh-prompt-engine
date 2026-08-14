"""plausibility.py — F1 (final_assembly_memo.md): naturalness/plausibility ゲート。

設計書 §7.4 の plausibility（人間的発声テクスチャの保持。生物学的実現可能性
ではない）を、harness VT-1 v3 が採用した床
（`results_v3/vt1_plausibility_v3.json`, `r_threshold=0.35`、周期性
`periodicity_track_v3.r_median` によるゲート）と同一の閾値・同一指標で
測定する。VT-1 は 122 ノード個々（集約値ではない）に閾値を課しており、本
実装もノート単位判定で揃えた（比較可能性の維持。詳細は
`underspec_log_final.md`）。

対象 probe: sustain / phrase / cross_range（F1 memo の記述どおり）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

import bridge
import probes
from genome import VoiceGenome

import measure_v3 as mv3

R_MEDIAN_THRESHOLD = 0.35  # harness VT-1 v3 と同一床
PLAUSIBILITY_PROBES: Tuple[str, ...] = ("sustain", "phrase", "cross_range")


@dataclass
class NoteReport:
    probe_name: str
    midi: float
    r_median: float
    passed: bool


@dataclass
class PlausibilityReport:
    threshold: float
    probe_names: Tuple[str, ...]
    notes: List[NoteReport]
    n_notes: int
    n_violations: int
    min_r_median: float
    passed: bool
    # PR#261 レビュー R38: probe ごとの実測 waveform 数が ProbeSpec 由来の
    # notes_midi 数と食い違った probe 名のリスト。1 件でも非空なら
    # `passed=False`（下記 docstring 参照）。
    probe_count_mismatches: List[str] = field(default_factory=list)


def plausibility_report(
    genome: VoiceGenome,
    probe_names: Tuple[str, ...] = PLAUSIBILITY_PROBES,
    threshold: float = R_MEDIAN_THRESHOLD,
) -> PlausibilityReport:
    notes: List[NoteReport] = []
    # PR#261 レビュー R38: `zip(result.notes_midi, result.waveforms)` は
    # 両者の長さが食い違っても例外を出さず、短い方に合わせて黙って
    # 切り詰める。`probes.render_probe()` の現行実装は常に
    # `len(waveforms) == len(notes_midi)` を満たすが、その保証は呼び出し元
    # からは検証されていなかった。waveform 数が notes_midi 数（= ProbeSpec
    # が定める期待数）と一致しない場合、レンダラが一部の音を欠落させて
    # 返したことを意味し、そのまま zip すると欠落分の評価が黙ってスキップ
    # され「一部の音だけ測って良好だった」ことを「全音を測って良好
    # だった」と取り違える不完全証拠 PASS になり得る
    # （gate_checks.py::_grip_axis の R23・genesis_v0.py::quick_s5() の
    # R36 と同型）。この probe は評価前に測定不能として記録し、ノートを
    # 一切追加しない（どのノートがどの波形に対応するか信頼できないため
    # 部分的な評価すら行わない）。
    probe_count_mismatches: List[str] = []
    for probe_name in probe_names:
        result = probes.render_probe(genome, probe_name)
        if len(result.waveforms) != len(result.notes_midi):
            probe_count_mismatches.append(probe_name)
            continue
        for midi, wave in zip(result.notes_midi, result.waveforms):
            track = mv3.periodicity_track_v3(wave, sr=bridge.SR)
            r_median = float(track.r_median)
            notes.append(
                NoteReport(probe_name=probe_name, midi=midi, r_median=r_median, passed=bool(r_median >= threshold))
            )
    n_violations = sum(1 for n in notes if not n.passed)
    min_r = min((n.r_median for n in notes), default=float("nan"))
    return PlausibilityReport(
        threshold=threshold,
        probe_names=probe_names,
        notes=notes,
        n_notes=len(notes),
        n_violations=n_violations,
        min_r_median=min_r,
        passed=bool(n_violations == 0 and not probe_count_mismatches),
        probe_count_mismatches=probe_count_mismatches,
    )
