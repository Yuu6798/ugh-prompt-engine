"""Build the K1 grip fixture by measuring the C4 deterministic synth performer.

MusicGen はリポジトリ外・重依存のため、K1 初版の参照生成器として C4 の決定論的
シンセ演奏者（scripts/compose_e2e_demo.perform）を用いる。Composition Score の
物理ツマミを 2 水準に振り、seed 駆動の bpm ジッターで生成器の確率性を模擬しながら
R 反復で演奏 → RPE 抽出し、per-sample 数値特徴を fixture JSON に書き出す。

DD-A（controllability_poc.md §4）どおり、音源はコミットせず数値 fixture のみを
コミットする。grip 計算（fixture → 効果量）は scripts/measure_grip.py が担う。

Usage:
    python scripts/build_k1_fixture.py             # fixture を再生成
    python scripts/build_k1_fixture.py --verify    # コミット済み fixture と一致検証
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.compose_e2e_demo import (  # noqa: E402
    SCORE_PATH,
    PerformanceStyle,
    perform,
    scaled_score,
    wav_bytes,
)
from svp_rpe.compose import load_composition_score  # noqa: E402
from svp_rpe.compose.models import CompositionScore  # noqa: E402

FIXTURE_SCHEMA_VERSION = "1.1"
FIXTURE_ID = "k1_synth_performer_rpe_features"
GENERATOR = "synth_performer_c4"
REPETITIONS = 5
BARS_SCALE = 0.25  # Midnight Signal を 12 小節に縮めて抽出を軽くする
BPM_JITTER_SD = 0.01  # 生成器の確率性の模擬: 演奏テンポに ±1% 程度の seed 駆動ゆらぎ
DEFAULT_FIXTURE = ROOT / "examples" / "control" / "k1" / "synth_performer_rpe_fixture.json"


@dataclass(frozen=True)
class KnobSpec:
    """K1 で振るツマミの定義（controllability_poc.md §6 の対応表に準拠）。"""

    name: str
    field: str               # PhysicalLayer のフィールド名
    low: Any
    high: Any
    sensor: str
    kind: str = "continuous"
    expected_sign: int = 1
    samples_from: str = ""   # 他ツマミの演奏を再利用する場合の参照元（補助センサー用）


KNOBS = (
    KnobSpec(name="bpm", field="bpm", low=90, high=140, sensor="bpm"),
    KnobSpec(
        name="key",
        field="key",
        low="C major",
        high="F# minor",
        sensor="key",
        kind="categorical",
    ),
    KnobSpec(
        name="brightness",
        field="brightness",
        low="dark",
        high="bright",
        sensor="spectral_profile.brightness",
    ),
    # 補助センサー行: brightness と同一の演奏サンプルを spectral_centroid でも観測し、
    # 「ツマミが死んでいる」のか「センサーが盲目」なのかを切り分ける（C4 の発見の追試）
    KnobSpec(
        name="brightness_centroid",
        field="brightness",
        low="dark",
        high="bright",
        sensor="spectral_centroid",
        samples_from="brightness",
    ),
    KnobSpec(
        name="active_rate_target",
        field="active_rate_target",
        low="0.80-0.85",
        high="0.95-0.98",
        sensor="active_rate",
    ),
    KnobSpec(
        name="valley_depth_target",
        field="valley_depth_target",
        low="0.05-0.10",
        high="0.30-0.40",
        sensor="valley_depth",
    ),
)


def base_score() -> CompositionScore:
    return scaled_score(load_composition_score(str(SCORE_PATH)), bars_scale=BARS_SCALE)


def score_with(score: CompositionScore, field: str, value: Any) -> CompositionScore:
    data = score.model_dump()
    data["physical"][field] = value
    return CompositionScore.model_validate(data)


def sample_seed(knob_index: int, level_index: int, repeat: int) -> int:
    """(ツマミ, 水準, 反復) ごとに安定な seed を割り当てる。"""
    return 1000 + knob_index * 100 + level_index * 50 + repeat


def jittered_style(name: str, score_bpm: float, seed: int) -> PerformanceStyle:
    """seed 駆動の bpm ジッターで「同一指示・別演奏」を作る。"""
    rng = np.random.default_rng(seed)
    factor = 1.0 + float(np.clip(rng.normal(0.0, BPM_JITTER_SD), -0.025, 0.025))
    return PerformanceStyle(name=name, bpm_bias=score_bpm * (factor - 1.0), seed=seed)


def extract_features(samples: np.ndarray, tmp_dir: Path, sample_id: str) -> dict[str, Any]:
    from svp_rpe.rpe.extractor import extract_rpe_from_file

    wav_path = tmp_dir / f"{sample_id}.wav"
    wav_path.write_bytes(wav_bytes(samples))
    bundle = extract_rpe_from_file(str(wav_path))
    physical = bundle.physical
    observed_key = (
        f"{physical.key} {physical.mode}"
        if physical.key and physical.mode
        else "unknown"
    )
    return {
        "bpm": physical.bpm,
        "key": observed_key,
        "spectral_profile": {"brightness": physical.spectral_profile.brightness},
        "spectral_centroid": physical.spectral_centroid,
        "active_rate": physical.active_rate,
        "valley_depth": physical.valley_depth,
    }


def build_fixture() -> dict[str, Any]:
    score = base_score()
    samples: list[dict[str, Any]] = []
    rendered: dict[tuple[str, str, int], dict[str, Any]] = {}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for knob_index, knob in enumerate(KNOBS):
            for level_index, level in enumerate((knob.low, knob.high)):
                for repeat in range(1, REPETITIONS + 1):
                    if knob.samples_from:
                        # 補助センサー行: 参照元ツマミの演奏済み特徴を再利用する
                        source = rendered[(knob.samples_from, str(level), repeat)]
                        features = source["features"]
                    else:
                        seed = sample_seed(knob_index, level_index, repeat)
                        variant = score_with(score, knob.field, level)
                        style = jittered_style(
                            name=f"{knob.name}_{level_index}_{repeat}",
                            score_bpm=float(variant.physical.bpm),
                            seed=seed,
                        )
                        sample_id = f"{knob.name}_{'low' if level_index == 0 else 'high'}_r{repeat:02d}"
                        features = extract_features(
                            perform(variant, style), tmp_dir, sample_id
                        )
                    record = {
                        "sample_id": (
                            f"{knob.name}_{'low' if level_index == 0 else 'high'}_r{repeat:02d}"
                        ),
                        "knob": knob.name,
                        "level": str(level),
                        "repeat": repeat,
                        "features": features,
                    }
                    samples.append(record)
                    rendered[(knob.name, str(level), repeat)] = record

    knob_specs = []
    for knob in KNOBS:
        spec: dict[str, Any] = {
            "name": knob.name,
            "sensor": knob.sensor,
            "low_level": str(knob.low),
            "high_level": str(knob.high),
        }
        if knob.kind == "categorical":
            spec["kind"] = "categorical"
        else:
            spec["expected_sign"] = knob.expected_sign
        knob_specs.append(spec)

    return {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "fixture_id": FIXTURE_ID,
        "generator": GENERATOR,
        "base_score": "examples/composition/midnight_signal/composition_score.yaml",
        "bars_scale": BARS_SCALE,
        "bpm_jitter_sd": BPM_JITTER_SD,
        "repetitions": REPETITIONS,
        "knobs": knob_specs,
        "samples": samples,
    }


def render_fixture(fixture: dict[str, Any]) -> str:
    return json.dumps(fixture, ensure_ascii=False, indent=2) + "\n"


def verify(fixture_path: Path) -> int:
    if not fixture_path.is_file():
        print(f"Missing fixture: {fixture_path}", file=sys.stderr)
        return 1
    committed = fixture_path.read_text(encoding="utf-8")
    regenerated = render_fixture(build_fixture())
    if committed != regenerated:
        print(f"Fixture drift: {fixture_path}", file=sys.stderr)
        return 1
    print(f"Verified K1 fixture {fixture_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)

    if args.verify:
        return verify(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_fixture(build_fixture()), encoding="utf-8")
    print(f"Wrote K1 fixture to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
