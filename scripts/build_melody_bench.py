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
from typing import Any, Dict, List, Optional, Tuple

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
    """monophonic fixture を合成する。

    レビュー対応 第 9 ラウンド（K2）: 任意フィールド `note_durs_sec`（`phrases`
    と同じ入れ子構造——フレーズごとのノート数分の秒数リスト）を追加した。
    指定時はフレーズ内でノートごとに `note_dur_sec` を上書きできる——従来の
    スカラー `note_dur_sec`（全音符一様）だけでは「同音程・別リズム」の
    負例が作れない: M3（`svp_rpe.melody.representation.build_sequences`）の
    IOI/duration 表現はテンポ不変（連続比の log2 のみを見る）ため、フレーズ
    内が一様なら一様スケールのテンポ差（従来の negative_rhythm b 側の設計）は
    両側とも全ゼロの log 比列に潰れ、rhythm 軸が構造的に区別不能になる
    （`docs/measurements/m3d_2026-08/preregistration.md`「実測前の事前登録
    修正」参照）。`note_durs_sec` はフレーズ内で非一様なタイミングパターン
    （例: 長短交互）を作るための唯一の手段——`phrase_gap_sec`（フレーズ間の
    無音）は比較器がフレーズ分割後に切り捨てるため変えても不可視。

    未指定時は従来どおりスカラー `note_dur_sec` を全音符へ一様適用する
    （完全後方互換——`synthesis_specs.yaml`（M0）や他の m3d_synth_specs.yaml
    fixture は本フィールドを使わないため無影響）。`note_dur_sec` はスカラー
    経路でのみ必須（`note_durs_sec` 指定時は省略可）。
    """
    phrases: List[List[float]] = spec["phrases"]
    note_gap = float(spec.get("note_gap_sec", 0.0))
    phrase_gap = float(spec.get("phrase_gap_sec", 0.0))

    note_durs_override: Optional[List[List[float]]] = spec.get("note_durs_sec")
    uniform_dur: Optional[float] = None
    if note_durs_override is None:
        if "note_dur_sec" not in spec:
            raise ValueError(
                "monophonic fixture には 'note_dur_sec'（スカラー）か "
                "'note_durs_sec'（フレーズ内非一様）のいずれかが必要"
            )
        uniform_dur = float(spec["note_dur_sec"])
    else:
        if len(note_durs_override) != len(phrases):
            raise ValueError(
                "note_durs_sec のフレーズ数が phrases と不一致 "
                f"({len(note_durs_override)} != {len(phrases)})"
            )
        for phrase, durs in zip(phrases, note_durs_override):
            if len(durs) != len(phrase):
                raise ValueError(
                    "note_durs_sec の音符数が対応する phrase と不一致 "
                    f"(phrase={phrase!r} durs={durs!r})"
                )

    segments: List[np.ndarray] = []
    for phrase_idx, phrase in enumerate(phrases):
        durs_for_phrase = (
            note_durs_override[phrase_idx] if note_durs_override is not None else None
        )
        for note_idx, midi in enumerate(phrase):
            dur = float(durs_for_phrase[note_idx]) if durs_for_phrase is not None else uniform_dur
            segments.append(_tone(midi_to_hz(float(midi)), dur, sample_rate, amplitude))
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
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # 成果物一式（全 WAV + manifest）を staging に完成させ、out_dir へ os.replace で
    # 公開する。既存 out_dir へ直接書くと、途中で中断された場合に新 WAV と旧
    # manifest.json（や WAV の部分集合）が混在した半端な成果物を下流の手動確認が
    # 消費しうる（Codex 指摘・AGENTS §8）。manifest を最後に move するため、
    # manifest が存在する時点では必ず全 WAV が配置済みである。
    #
    # staging は **out_dir の中**（隠し temp サブディレクトリ）に作る。既定 temp（/tmp）
    # に置くと out_dir が別マウントのとき os.replace が EXDEV で失敗する。out_dir.parent
    # 直下でも不十分で、out_dir が**別 fs を指す symlink**のとき os.replace の宛先は
    # symlink を辿って target fs 上に解決される一方、staging は symlink の字面上の親 fs に
    # 載るため EXDEV が残る（Codex 指摘）。staging を out_dir 内に置けば、staging も宛先も
    # 同じ out_dir（symlink 先の実ディレクトリ）を親に持つため、rename は symlink/マウント
    # 構成によらず必ず同一 fs で成立する。隠し prefix + manifest 駆動消費なので途中成果物は
    # 下流から不可視。
    manifest: Dict[str, Dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(
        prefix=f".{out_dir.name}.stage-", dir=out_dir
    ) as tmp:
        staging = Path(tmp)
        staged: List[Tuple[Path, Path]] = []
        for fid, (y, sr) in build_all(specs).items():
            staged_wav = staging / f"{fid}.wav"
            sf.write(staged_wav, y, sr, subtype="FLOAT")
            digest = hashlib.sha256(staged_wav.read_bytes()).hexdigest()
            final_wav = out_dir / f"{fid}.wav"
            staged.append((staged_wav, final_wav))
            manifest[fid] = {"path": str(final_wav), "sample_rate": sr, "sha256": digest}
        staged_manifest = staging / "manifest.json"
        staged_manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        for staged_wav, final_wav in staged:
            os.replace(staged_wav, final_wav)
        os.replace(staged_manifest, out_dir / "manifest.json")
    print(f"wrote {len(manifest)} fixtures + manifest to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
