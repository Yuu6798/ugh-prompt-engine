"""Composition E2E demo (C4): Score → prompt → 演奏 → audit のフルループ。

外部生成器（Suno/Udio）への手動依頼ステップを「決定論的シンセ演奏者」で代替し、
ループ全体をローカル完結・再現可能にする。同一 Score を 2 つの演奏スタイル
（first_take: Score から逸脱 / faithful_take: Score 忠実）で演奏し、
`svprpe audit` 制御盤の針が target へ近づくことを観測する。

audit 哲学を維持する: 出力は針位置の比較であり、verdict / pass-fail は出さない。

Usage:
    python scripts/compose_e2e_demo.py            # 成果物を examples/.../e2e に生成
    python scripts/compose_e2e_demo.py --verify   # コミット済み成果物との一致を検証
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.generate_synth_samples import (  # noqa: E402
    SAMPLE_RATE,
    _adsr_envelope,
    sha256_bytes,
    wav_bytes,
)
from svp_rpe.compose import ExternalPromptAdapter, load_composition_score  # noqa: E402
from svp_rpe.compose.models import CompositionScore, StructureSection  # noqa: E402

SCORE_PATH = ROOT / "examples" / "composition" / "midnight_signal" / "composition_score.yaml"
DEFAULT_OUTPUT_DIR = ROOT / "examples" / "composition" / "midnight_signal" / "e2e"

KEY_PATTERN = re.compile(r"^\s*([A-Ga-g])\s*([#b]?)\s*(major|minor)\s*$", re.IGNORECASE)
PITCH_CLASSES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
MINOR_TRIAD = (0, 3, 7)
MAJOR_TRIAD = (0, 4, 7)
# 自然短調 i–VI–VII–i / 長調 I–IV–V–I。(度数オフセット, 三和音種別)
MINOR_PROGRESSION = ((0, MINOR_TRIAD), (8, MAJOR_TRIAD), (10, MAJOR_TRIAD), (0, MINOR_TRIAD))
MAJOR_PROGRESSION = ((0, MAJOR_TRIAD), (5, MAJOR_TRIAD), (7, MAJOR_TRIAD), (0, MAJOR_TRIAD))


@dataclass(frozen=True)
class PerformanceStyle:
    """演奏者の癖。Score は不変のまま、演奏だけが変わる。"""

    name: str
    bpm_bias: float = 0.0          # 演奏テンポ = score.bpm + bias
    transpose: int = 0             # 半音単位の転調（キー無視の度合い）
    bright_voicing: bool = False   # 高次倍音 + 1 オクターブ上げ（brightness 逸脱）
    flatten_dynamics: bool = False  # 構造ロールを無視して一定エネルギーで弾く
    seed: int = 0


FIRST_TAKE = PerformanceStyle(
    name="first_take",
    bpm_bias=-28.0,
    transpose=4,
    bright_voicing=True,
    flatten_dynamics=True,
    seed=11,
)
FAITHFUL_TAKE = PerformanceStyle(name="faithful_take", seed=12)
STYLES = (FIRST_TAKE, FAITHFUL_TAKE)


@dataclass(frozen=True)
class _SectionProfile:
    level: float
    pulse: bool
    melody: bool
    drone_only: bool
    rest_gate: bool = False  # 各小節の最終拍を無音にする（"clear rests" の解釈）


def parse_key(text: str) -> tuple[int, str]:
    """"C minor" 形式のキー表記を (pitch class, mode) に分解する。"""
    match = KEY_PATTERN.match(text.replace("♯", "#").replace("♭", "b"))
    if match is None:
        raise ValueError(f"unsupported key spec: {text!r}")
    tonic = PITCH_CLASSES[match.group(1).upper()]
    accidental = match.group(2)
    if accidental == "#":
        tonic = (tonic + 1) % 12
    elif accidental == "b":
        tonic = (tonic - 1) % 12
    return tonic, match.group(3).lower()


def midi_to_freq(midi_note: float) -> float:
    return 440.0 * 2.0 ** ((midi_note - 69.0) / 12.0)


def _section_profile(section: StructureSection, *, flatten: bool) -> _SectionProfile:
    """structure 層の physical/role ヒントを演奏エネルギーへ読み替える。

    キーワードヒューリスティックは PoC 範囲の簡易解釈
    （docs/composition_poc_report.md 参照）。
    """
    if flatten:
        return _SectionProfile(level=0.70, pulse=True, melody=True, drone_only=False)
    hint = f"{section.physical} {section.role}".lower()
    rest_gate = "rest" in hint
    if "silence" in hint or "no kick" in hint:
        return _SectionProfile(level=0.10, pulse=False, melody=False, drone_only=True)
    if "low density" in hint or "sub bass" in hint:
        return _SectionProfile(level=0.25, pulse=False, melody=False, drone_only=True)
    if "sparse" in hint:
        return _SectionProfile(
            level=0.45, pulse=True, melody=False, drone_only=False, rest_gate=rest_gate
        )
    if "full energy" in hint or "release" in hint:
        return _SectionProfile(level=0.85, pulse=True, melody=True, drone_only=False)
    return _SectionProfile(
        level=0.55, pulse=True, melody=True, drone_only=False, rest_gate=rest_gate
    )


def _triad_frequencies(
    tonic: int,
    degree_offset: int,
    triad: tuple[int, ...],
    *,
    base_octave_midi: int,
) -> list[float]:
    root = base_octave_midi + (tonic + degree_offset) % 12
    return [midi_to_freq(root + interval) for interval in triad]


def _chord_wave(
    t: np.ndarray,
    frequencies: list[float],
    harmonic_weights: tuple[float, ...],
) -> np.ndarray:
    signal = np.zeros_like(t)
    for freq in frequencies:
        for harmonic_index, weight in enumerate(harmonic_weights, start=1):
            signal += weight * np.sin(2.0 * np.pi * freq * harmonic_index * t)
    peak = float(np.max(np.abs(signal)))
    return signal / peak if peak else signal


def _pulse_train(t: np.ndarray, bpm: float, beats_per_bar: int) -> np.ndarray:
    beat_period = 60.0 / bpm
    pulse = np.zeros_like(t)
    if len(t) == 0:
        return pulse
    for beat_index, beat_time in enumerate(np.arange(0.0, t[-1] + beat_period, beat_period)):
        accent = 1.0 if beat_index % beats_per_bar == 0 else 0.45
        width = 0.018 if accent == 1.0 else 0.012
        pulse += accent * np.exp(-0.5 * ((t - beat_time) / width) ** 2)
    peak = float(np.max(pulse))
    return pulse / peak if peak else pulse


def _rest_gate_mask(length: int, bar_sec: float, beats_per_bar: int) -> np.ndarray:
    """小節ごとに最終拍を無音化するマスク（短いフェードでクリックを防ぐ）。"""
    mask = np.ones(length, dtype=np.float64)
    bar_samples = int(round(bar_sec * SAMPLE_RATE))
    if bar_samples <= 0:
        return mask
    beat_samples = max(1, bar_samples // beats_per_bar)
    fade = min(beat_samples // 8, int(0.01 * SAMPLE_RATE))
    start = bar_samples - beat_samples
    while start < length:
        end = min(start + beat_samples, length)
        mask[start:end] = 0.0
        if fade > 0 and start - fade >= 0:
            mask[start - fade:start] = np.linspace(1.0, 0.0, fade, endpoint=False)
        if fade > 0 and end + fade <= length:
            mask[end:end + fade] = np.linspace(0.0, 1.0, fade, endpoint=False)
        start += bar_samples
    return mask


def perform(score: CompositionScore, style: PerformanceStyle) -> np.ndarray:
    """Score を style の癖で「演奏」した int16 mono 波形を返す（決定論的）。"""
    if not score.structure:
        raise ValueError("perform() requires at least one structure section")
    tonic, mode = parse_key(score.physical.key)
    tonic = (tonic + style.transpose) % 12
    progression = MINOR_PROGRESSION if mode == "minor" else MAJOR_PROGRESSION
    bpm = float(score.physical.bpm) + style.bpm_bias
    beats_per_bar = int(score.physical.time_signature.split("/", 1)[0])
    bar_sec = beats_per_bar * 60.0 / bpm

    dark_target = score.physical.brightness.strip().lower() == "dark"
    if style.bright_voicing:
        harmonic_weights: tuple[float, ...] = (1.0, 0.55, 0.35, 0.20)
        base_octave_midi = 60  # C4 周辺
    elif dark_target:
        harmonic_weights = (1.0, 0.18)
        base_octave_midi = 48  # C3 周辺
    else:
        harmonic_weights = (1.0, 0.35, 0.15)
        base_octave_midi = 48

    rng = np.random.default_rng(seed=style.seed)
    chunks: list[np.ndarray] = []
    for section in score.structure:
        profile = _section_profile(section, flatten=style.flatten_dynamics)
        section_len = int(round(section.bars * bar_sec * SAMPLE_RATE))
        t = np.arange(section_len, dtype=np.float64) / SAMPLE_RATE
        if profile.drone_only:
            root_freq = midi_to_freq(base_octave_midi - 12 + tonic)
            wave = _chord_wave(t, [root_freq], harmonic_weights)
        else:
            block = max(1, section_len // len(progression))
            wave = np.zeros_like(t)
            for chord_index, (degree, triad) in enumerate(progression):
                start = chord_index * block
                end = section_len if chord_index == len(progression) - 1 else start + block
                local_t = t[start:end] - t[start]
                frequencies = _triad_frequencies(
                    tonic, degree, triad, base_octave_midi=base_octave_midi
                )
                wave[start:end] = _chord_wave(local_t, frequencies, harmonic_weights)
        if profile.pulse:
            pulse = _pulse_train(t, bpm, beats_per_bar)
            wave *= 0.55 + 0.45 * pulse
        if profile.melody:
            melody_degrees = (progression[0], progression[1], progression[2], progression[0])
            block = max(1, section_len // len(melody_degrees))
            for note_index, (degree, triad) in enumerate(melody_degrees):
                start = note_index * block
                end = section_len if note_index == len(melody_degrees) - 1 else start + block
                local_t = t[start:end] - t[start]
                freq = _triad_frequencies(
                    tonic, degree, triad, base_octave_midi=base_octave_midi + 12
                )[0]
                note = np.sin(2.0 * np.pi * freq * local_t)
                note *= _adsr_envelope(len(note), 0.03, 0.05)
                wave[start:end] += 0.5 * note
        if profile.rest_gate:
            wave *= _rest_gate_mask(section_len, bar_sec, beats_per_bar)
        wave *= profile.level
        wave *= _adsr_envelope(section_len, 0.05, 0.08)
        chunks.append(wave)

    signal = np.concatenate(chunks)
    signal = signal + rng.normal(0.0, 0.0015, size=signal.shape)
    signal *= _adsr_envelope(len(signal), 0.02, 0.02)
    peak = float(np.max(np.abs(signal)))
    if peak:
        signal = signal / peak * 0.82
    return np.round(signal * 32767.0).astype(np.int16)


def scaled_score(score: CompositionScore, *, bars_scale: float) -> CompositionScore:
    """テスト高速化用に structure の bars を縮める（最低 1 小節は維持）。

    model_copy(update=) は再バリデーションしないため、model_validate を通して
    extra="forbid" と型の保証を維持する。
    """
    data = score.model_dump()
    for section in data["structure"]:
        section["bars"] = max(1, int(round(section["bars"] * bars_scale)))
    return CompositionScore.model_validate(data)


def run_take(
    score: CompositionScore,
    style: PerformanceStyle,
    output_dir: Path,
) -> dict[str, Any]:
    """1 テイク分の perform → extract → audit を実行し、成果物を書き出す。"""
    from svp_rpe.rpe.extractor import extract_rpe_from_file
    from svp_rpe.semantic_ci.audit import build_audit_report, render_audit_text

    samples = perform(score, style)
    wav_path = output_dir / f"{style.name}.wav"
    wav_data = wav_bytes(samples)
    wav_path.write_bytes(wav_data)

    bundle = extract_rpe_from_file(str(wav_path))
    rpe_payload = bundle.model_dump(mode="json")
    (output_dir / f"{style.name}_rpe.json").write_text(
        json.dumps(rpe_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    report = build_audit_report(score, bundle, observed_id=style.name)
    report_payload = report.model_dump(mode="json")
    report_md = render_audit_text(report)
    (output_dir / f"{style.name}_audit.json").write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / f"{style.name}_audit.md").write_text(report_md, encoding="utf-8")
    return {
        "style": style.name,
        "wav_sha256": sha256_bytes(wav_data),
        "report": report_payload,
        "report_md": report_md,
    }


def _needle_index(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    needles: dict[str, dict[str, Any]] = {}
    for layer in ("physical", "semantic"):
        for needle in report[layer]:
            needles[needle["name"]] = needle
    return needles


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item)
    return str(value)


def render_summary(takes: list[dict[str, Any]]) -> str:
    """2 テイクの針位置を並べた比較表を Markdown で返す。

    「改善判定」はせず、deviation の絶対値が縮んだ針に → 印を付けるだけに留める
    （audit 哲学: 針の移動の観測であって verdict ではない）。
    """
    first, faithful = takes[0], takes[1]
    first_needles = _needle_index(first["report"])
    faithful_needles = _needle_index(faithful["report"])
    lines = [
        "# Composition E2E Needle Comparison — Midnight Signal",
        "",
        "同一 Score を 2 つの演奏スタイルで演奏し、audit 制御盤の針位置を比較する。",
        "",
        f"- score_id: {first['report']['score_id']}",
        f"- take 1: `{first['style']}` (wav sha256 `{first['wav_sha256'][:12]}…`)",
        f"- take 2: `{faithful['style']}` (wav sha256 `{faithful['wav_sha256'][:12]}…`)",
        "",
        "| knob | layer | target | first_take | faithful_take | dev (first) | dev (faithful) | 針の移動 |",
        "|---|---|---|---|---|---:|---:|---|",
    ]
    for name, first_needle in first_needles.items():
        faithful_needle = faithful_needles.get(name)
        if faithful_needle is None:
            continue
        dev_first = first_needle.get("deviation")
        dev_faithful = faithful_needle.get("deviation")
        movement = ""
        if dev_first is not None and dev_faithful is not None:
            if abs(dev_faithful) < abs(dev_first):
                movement = "→ target"
            elif abs(dev_faithful) > abs(dev_first):
                movement = "← away"
            else:
                movement = "= flat"
        lines.append(
            "| "
            f"{name} | "
            f"{first_needle['layer']} | "
            f"{_format_value(first_needle.get('target'))} | "
            f"{_format_value(first_needle.get('observed'))} | "
            f"{_format_value(faithful_needle.get('observed'))} | "
            f"{_format_value(dev_first)} | "
            f"{_format_value(dev_faithful)} | "
            f"{movement} |"
        )
    return "\n".join(lines) + "\n"


def run_demo(
    score_path: Path = SCORE_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    """フルループを実行し、成果物一式を output_dir に書き出す。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    # WAV / RPE / prompt は決定論的に再生成できる中間生成物なのでコミットしない。
    # コミット対象は audit レポートと針比較表のみ。
    (output_dir / ".gitignore").write_text(
        "*.wav\n*_rpe.json\ngenerated_prompt.txt\n", encoding="utf-8"
    )

    score = load_composition_score(str(score_path))
    prompt = ExternalPromptAdapter().render(score)
    (output_dir / "generated_prompt.txt").write_text(prompt.text + "\n", encoding="utf-8")

    takes = [run_take(score, style, output_dir) for style in STYLES]
    summary = render_summary(takes)
    (output_dir / "needle_comparison.md").write_text(summary, encoding="utf-8")
    return {"prompt": prompt.model_dump(mode="json"), "takes": takes, "summary": summary}


def verify(
    score_path: Path = SCORE_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> int:
    """成果物が現行コードからの再生成と一致するか検証する（WAV は再演奏して比較）。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        regenerated = run_demo(score_path=score_path, output_dir=Path(tmp))
    ok = True
    for take in regenerated["takes"]:
        name = take["style"]
        committed_path = output_dir / f"{name}_audit.json"
        if not committed_path.is_file():
            print(f"Missing artifact: {committed_path}", file=sys.stderr)
            ok = False
            continue
        committed_audit = json.loads(committed_path.read_text(encoding="utf-8"))
        if committed_audit != take["report"]:
            print(f"Audit report drift for {name}", file=sys.stderr)
            ok = False
        md_path = output_dir / f"{name}_audit.md"
        if not md_path.is_file():
            print(f"Missing artifact: {md_path}", file=sys.stderr)
            ok = False
        elif md_path.read_text(encoding="utf-8") != take["report_md"]:
            print(f"Audit markdown drift for {name}", file=sys.stderr)
            ok = False
    summary_path = output_dir / "needle_comparison.md"
    if not summary_path.is_file():
        print(f"Missing artifact: {summary_path}", file=sys.stderr)
        ok = False
    elif summary_path.read_text(encoding="utf-8") != regenerated["summary"]:
        print("needle_comparison.md drift", file=sys.stderr)
        ok = False
    if ok:
        print(f"Verified composition E2E artifacts in {output_dir}")
        return 0
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", type=Path, default=SCORE_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--verify", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.verify:
        return verify(score_path=args.score, output_dir=args.output_dir)
    result = run_demo(score_path=args.score, output_dir=args.output_dir)
    print(result["summary"])
    print(f"Artifacts written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
