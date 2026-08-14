"""plausibility.py — F1 (final_assembly_memo.md): naturalness/plausibility ゲート。

設計書 §7.4 の plausibility（人間的発声テクスチャの保持。生物学的実現可能性
ではない）を、vt_harness VT-1 v3 が採用した床
（`results_v3/vt1_plausibility_v3.json`, `r_threshold=0.35`、周期性
`periodicity_track_v3.r_median` によるゲート）と同一の閾値・同一指標で
測定する。VT-1 は 122 ノード個々（集約値ではない）に閾値を課しており、本
実装もノート単位判定で揃えた（比較可能性の維持。詳細は
`underspec_log_final.md`）。

対象 probe: sustain / phrase / cross_range（F1 memo の記述どおり）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import bridge
import probes
from genome import VoiceGenome

import measure_v3 as mv3

R_MEDIAN_THRESHOLD = 0.35  # vt_harness VT-1 v3 と同一床
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


def plausibility_report(
    genome: VoiceGenome,
    probe_names: Tuple[str, ...] = PLAUSIBILITY_PROBES,
    threshold: float = R_MEDIAN_THRESHOLD,
) -> PlausibilityReport:
    notes: List[NoteReport] = []
    for probe_name in probe_names:
        result = probes.render_probe(genome, probe_name)
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
        passed=bool(n_violations == 0),
    )
