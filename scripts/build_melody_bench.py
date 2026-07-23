"""build_melody_bench.py — CI 安全な合成 melody_bench fixture の決定論ビルダー。

`tests/fixtures/melody_bench/synthesis_specs.yaml` の仕様から波形を決定論的に
合成する。乱数を用いないため、仕様の pin = 波形の pin（バイナリ WAV を committed
しない Phase 0 スパイクと同じ provenance 方針）。

ライブラリとしても使える（`build_signal` / `load_specs` / `build_all` を
`tests/test_melody_observability.py` と `scripts/run_melody_observability.py` が
import する）。CLI として実行すると WAV + sha256 manifest を書き出す。

使い方::

    python scripts/build_melody_bench.py --out-dir /tmp/melody_bench
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import soundfile as sf
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPECS_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "synthesis_specs.yaml"


def midi_to_hz(midi: float) -> float:
    """MIDI ノート番号 → Hz（69 = A4 = 440Hz）。"""
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def _tone(hz: float, dur_sec: float, sample_rate: int, amplitude: float) -> np.ndarray:
    n = int(round(sample_rate * dur_sec))
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    t = np.linspace(0.0, dur_sec, n, endpoint=False)
    return (amplitude * np.sin(2.0 * np.pi * hz * t)).astype(np.float32)


def _rest(dur_sec: float, sample_rate: int) -> np.ndarray:
    return np.zeros(max(0, int(round(sample_rate * dur_sec))), dtype=np.float32)


def _build_monophonic(spec: Dict[str, Any], sample_rate: int, amplitude: float) -> np.ndarray:
    note_dur = float(spec["note_dur_sec"])
    note_gap = float(spec.get("note_gap_sec", 0.0))
    phrase_gap = float(spec.get("phrase_gap_sec", 0.0))
    segments: List[np.ndarray] = []
    phrases: List[List[float]] = spec["phrases"]
    for phrase in phrases:
        for midi in phrase:
            segments.append(_tone(midi_to_hz(float(midi)), note_dur, sample_rate, amplitude))
            if note_gap > 0.0:
                segments.append(_rest(note_gap, sample_rate))
        if phrase_gap > 0.0:
            segments.append(_rest(phrase_gap, sample_rate))
    return np.concatenate(segments) if segments else np.zeros(0, dtype=np.float32)


def _build_chord_pad(spec: Dict[str, Any], sample_rate: int, amplitude: float) -> np.ndarray:
    duration = float(spec["duration_sec"])
    chords: List[List[float]] = spec["chords"]
    # 単一（先頭）コードを duration 持続で重ねる（旋律の無い持続パッド）。
    chord = chords[0]
    per_voice = amplitude / max(1, len(chord))
    mixed = np.zeros(int(round(sample_rate * duration)), dtype=np.float32)
    for midi in chord:
        voice = _tone(midi_to_hz(float(midi)), duration, sample_rate, per_voice)
        mixed[: len(voice)] += voice[: len(mixed)]
    return mixed


def build_signal(fixture_id: str, specs: Dict[str, Any]) -> Tuple[np.ndarray, int]:
    """`fixture_id` の合成波形 (y, sample_rate) を決定論的に返す。"""
    sample_rate = int(specs["sample_rate"])
    amplitude = float(specs["amplitude"])
    fixtures = specs["fixtures"]
    if fixture_id not in fixtures:
        raise KeyError(f"unknown fixture id: {fixture_id!r}")
    spec = fixtures[fixture_id]
    kind = spec["kind"]
    if kind == "monophonic":
        return _build_monophonic(spec, sample_rate, amplitude), sample_rate
    if kind == "chord_pad":
        return _build_chord_pad(spec, sample_rate, amplitude), sample_rate
    raise ValueError(f"unknown fixture kind: {kind!r}")


def load_specs(path: Path = SPECS_PATH) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def build_all(specs: Dict[str, Any]) -> Dict[str, Tuple[np.ndarray, int]]:
    return {fid: build_signal(fid, specs) for fid in specs["fixtures"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--specs", type=Path, default=SPECS_PATH)
    args = parser.parse_args()

    specs = load_specs(args.specs)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    # 成果物一式（全 WAV + manifest）を staging に完成させ、out_dir へ os.replace で
    # 公開する。既存 out_dir へ直接書くと、途中で中断された場合に新 WAV と旧
    # manifest.json（や WAV の部分集合）が混在した半端な成果物を下流の手動確認が
    # 消費しうる（Codex 指摘・AGENTS §8）。manifest を最後に move するため、
    # manifest が存在する時点では必ず全 WAV が配置済みである。
    manifest: Dict[str, Dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="melody-bench-stage-") as tmp:
        staging = Path(tmp)
        staged: List[Tuple[Path, Path]] = []
        for fid, (y, sr) in build_all(specs).items():
            staged_wav = staging / f"{fid}.wav"
            sf.write(staged_wav, y, sr, subtype="FLOAT")
            digest = hashlib.sha256(staged_wav.read_bytes()).hexdigest()
            final_wav = args.out_dir / f"{fid}.wav"
            staged.append((staged_wav, final_wav))
            manifest[fid] = {"path": str(final_wav), "sample_rate": sr, "sha256": digest}
        staged_manifest = staging / "manifest.json"
        staged_manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        for staged_wav, final_wav in staged:
            os.replace(staged_wav, final_wav)
        os.replace(staged_manifest, args.out_dir / "manifest.json")
    print(f"wrote {len(manifest)} fixtures + manifest to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
