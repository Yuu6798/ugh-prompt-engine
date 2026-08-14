"""probes.py — P2 (VG-002): Probe Score Suite。

`proto1_design_memo.md` §P2 の 5 probe を凍結表として定義し、Genome を
レンダリングして manifest（MIDI 列・長さ・波形 sha256）を作る決定論 fixture
生成器。

probe 定義（凍結。値を変更する場合は本ファイルではなく新しい probe 名を
追加すること — 既存 probe の改変は過去の manifest hash と非互換になる）:
  - sustain: {C3, A4, C6} × 1.5s
  - register_sweep: MIDI 45→90 半音階、各 0.25s（声区遷移連続性の検査対象）
  - vibrato: A4 × 3s
  - phrase: C4 D4 E4 G4 E4 D4 C4 C4（各 0.5s）
  - cross_range: C3 と C6 の同一母音ペア、各 1.5s
    （sustain と同じ音長を採用。underspec_log_p1.md [UNDERSPEC-P1-6]）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np

import bridge
from genome import VoiceGenome
from hashing import sha256_of_waveform

SR = bridge.SR


def _chromatic(lo_midi: int, hi_midi: int) -> Tuple[int, ...]:
    return tuple(range(lo_midi, hi_midi + 1))


# MIDI 記号: C3=48, A4=69, C6=84, C4=60, D4=62, E4=64, G4=67
@dataclass(frozen=True)
class ProbeSpec:
    name: str
    notes_midi: Tuple[float, ...]
    durations_sec: Tuple[float, ...]  # notes_midi と同じ長さ
    intensity: float = 0.7


def _uniform(notes: Tuple[float, ...], duration: float) -> Tuple[float, ...]:
    return tuple(duration for _ in notes)


PROBE_DEFINITIONS: Dict[str, ProbeSpec] = {
    "sustain": ProbeSpec(
        name="sustain",
        notes_midi=(48.0, 69.0, 84.0),  # C3, A4, C6
        durations_sec=_uniform((48.0, 69.0, 84.0), 1.5),
    ),
    "register_sweep": ProbeSpec(
        name="register_sweep",
        notes_midi=tuple(float(m) for m in _chromatic(45, 90)),
        durations_sec=_uniform(tuple(_chromatic(45, 90)), 0.25),
    ),
    "vibrato": ProbeSpec(
        name="vibrato",
        notes_midi=(69.0,),  # A4
        durations_sec=(3.0,),
    ),
    "phrase": ProbeSpec(
        name="phrase",
        notes_midi=(60.0, 62.0, 64.0, 67.0, 64.0, 62.0, 60.0, 60.0),  # C4 D4 E4 G4 E4 D4 C4 C4
        durations_sec=_uniform((60.0, 62.0, 64.0, 67.0, 64.0, 62.0, 60.0, 60.0), 0.5),
    ),
    "cross_range": ProbeSpec(
        name="cross_range",
        notes_midi=(48.0, 84.0),  # C3, C6
        durations_sec=(1.5, 1.5),
    ),
}

PROBE_NAMES: Tuple[str, ...] = tuple(PROBE_DEFINITIONS.keys())


@dataclass
class ProbeRenderResult:
    probe_name: str
    notes_midi: Tuple[float, ...]
    durations_sec: Tuple[float, ...]
    waveforms: List[np.ndarray]  # notes_midi と同じ順序・同じ長さ


def render_probe(genome: VoiceGenome, probe_name: str) -> ProbeRenderResult:
    if probe_name not in PROBE_DEFINITIONS:
        raise KeyError(f"未知の probe: {probe_name!r}（既知: {PROBE_NAMES}）")
    spec = PROBE_DEFINITIONS[probe_name]
    waveforms = [
        bridge.render_note(genome, midi, intensity=spec.intensity, duration_sec=dur)
        for midi, dur in zip(spec.notes_midi, spec.durations_sec)
    ]
    return ProbeRenderResult(
        probe_name=probe_name,
        notes_midi=spec.notes_midi,
        durations_sec=spec.durations_sec,
        waveforms=waveforms,
    )


def probe_manifest(genome: VoiceGenome, probe_name: str) -> Dict:
    """probe ごとの MIDI 列・長さ・sha256（波形 hash）を記録した manifest dict。

    決定論性: 同一 Genome + probe → 同一 manifest（hash 含む）になることを
    `tests/test_probes.py` が検証する。
    """
    result = render_probe(genome, probe_name)
    note_hashes = [sha256_of_waveform(wave) for wave in result.waveforms]
    combined = sha256_of_waveform(np.concatenate(result.waveforms))
    return {
        "probe_name": probe_name,
        "genome_name": genome.name,
        "sr": SR,
        "notes_midi": list(result.notes_midi),
        "durations_sec": list(result.durations_sec),
        "note_waveform_sha256": note_hashes,
        "combined_waveform_sha256": combined,
    }


def full_manifest(genome: VoiceGenome, probe_names: Tuple[str, ...] = PROBE_NAMES) -> Dict:
    return {
        "genome_name": genome.name,
        "probes": {name: probe_manifest(genome, name) for name in probe_names},
    }
