"""Build the K3 orthogonality fixture by measuring the C4 deterministic synth performer.

K3 extends the K1/K2 diagonal grip harness (docs/controllability_poc.md §5) to an
N×N knob-vs-sensor interference matrix. Each of the 5 K1 knobs is rendered with
its own dedicated 2-level x R-repetition sweep (no sample reuse across knobs,
unlike K1's brightness_band_ratio row), and every sample is measured against all
core + extended sensors so that scripts/measure_orthogonality.py can compute
off-diagonal interference and DCI-style disentanglement/completeness.

DD-A（controllability_poc.md §4）どおり、音源はコミットせず数値 fixture のみを
コミットする。fixture → 行列計算は scripts/measure_orthogonality.py が担う。

Usage:
    python scripts/build_k3_fixture.py             # fixture を再生成
    python scripts/build_k3_fixture.py --verify    # コミット済み fixture と一致検証
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
from scripts.measure_grip import _key_match_score  # noqa: E402
from svp_rpe.compose import load_composition_score  # noqa: E402
from svp_rpe.compose.models import CompositionScore  # noqa: E402

FIXTURE_SCHEMA_VERSION = "1.0"
FIXTURE_ID = "k3_synth_performer_matrix_rpe_features"
GENERATOR = "synth_performer_c4"
REPETITIONS = 5
BARS_SCALE = 0.25  # Midnight Signal を 12 小節に縮めて抽出を軽くする
BPM_JITTER_SD = 0.01  # 生成器の確率性の模擬: 演奏テンポに ±1% 程度の seed 駆動ゆらぎ
DEFAULT_FIXTURE = ROOT / "examples" / "control" / "k3" / "synth_performer_matrix_fixture.json"


@dataclass(frozen=True)
class KnobSpec:
    """K3 で振るツマミの定義。K1 の 5 ツマミと 1:1 対応する対角センサーを持つ。"""

    name: str
    field: str  # PhysicalLayer のフィールド名
    low: Any
    high: Any
    diagonal_sensor: str
    expected_sign: int
    known_dead: bool = False


KNOBS = (
    KnobSpec(name="bpm", field="bpm", low=90, high=140, diagonal_sensor="bpm", expected_sign=1),
    # ベースライン一致スコアは key を離すと下がる（expected_sign=-1）。
    # low = ベースラインそのもの（C minor）なので low 群は「無編集」を測る。
    KnobSpec(
        name="key",
        field="key",
        low="C minor",
        high="F# minor",
        diagonal_sensor="key_match_baseline",
        expected_sign=-1,
    ),
    KnobSpec(
        name="brightness",
        field="brightness",
        low="dark",
        high="bright",
        diagonal_sensor="spectral_centroid",
        expected_sign=1,
    ),
    # 決定論 performer はこの 2 フィールドを読まない（コード検査で既知、
    # controllability_poc.md §5.1 K1「ツマミ死」）＝この行の全セルは純粋な
    # seed ノイズ＝経験的ヌル分布の供給源（K3-1b, §5.3 発見2）。
    KnobSpec(
        name="active_rate_target",
        field="active_rate_target",
        low="0.80-0.85",
        high="0.95-0.98",
        diagonal_sensor="active_rate",
        expected_sign=1,
        known_dead=True,
    ),
    KnobSpec(
        name="valley_depth_target",
        field="valley_depth_target",
        low="0.05-0.10",
        high="0.30-0.40",
        diagonal_sensor="valley_depth",
        expected_sign=1,
        known_dead=True,
    ),
)

SENSORS_CORE = (
    {"name": "bpm", "path": "bpm", "kind": "core"},
    {"name": "key_match_baseline", "path": "key_match_baseline", "kind": "core"},
    {"name": "spectral_centroid", "path": "spectral_centroid", "kind": "core"},
    {"name": "active_rate", "path": "active_rate", "kind": "core"},
    {"name": "valley_depth", "path": "valley_depth", "kind": "core"},
)
SENSORS_EXTENDED = (
    {"name": "onset_density", "path": "onset_density", "kind": "extended"},
)


def base_score() -> CompositionScore:
    return scaled_score(load_composition_score(str(SCORE_PATH)), bars_scale=BARS_SCALE)


def baseline_key(score: CompositionScore) -> str:
    """base score が宣言する物理層の key（"{tonic} {mode}" 形式）を読む。"""
    return str(score.physical.key)


def score_with(score: CompositionScore, field: str, value: Any) -> CompositionScore:
    data = score.model_dump()
    data["physical"][field] = value
    return CompositionScore.model_validate(data)


def sample_seed(knob_index: int, level_index: int, repeat: int) -> int:
    """(ツマミ, 水準, 反復) ごとに安定な seed を割り当てる。

    K1 は base 1000 を使ったため、K3 は base 3000 として 2 つの fixture の
    seed 空間が重ならないようにする。
    """
    return 3000 + knob_index * 100 + level_index * 50 + repeat


def jittered_style(name: str, score_bpm: float, seed: int) -> PerformanceStyle:
    """seed 駆動の bpm ジッターで「同一指示・別演奏」を作る。"""
    rng = np.random.default_rng(seed)
    factor = 1.0 + float(np.clip(rng.normal(0.0, BPM_JITTER_SD), -0.025, 0.025))
    return PerformanceStyle(name=name, bpm_bias=score_bpm * (factor - 1.0), seed=seed)


def extract_features(
    samples: np.ndarray,
    tmp_dir: Path,
    sample_id: str,
    baseline: str,
) -> dict[str, Any]:
    from svp_rpe.rpe.extractor import extract_physical_from_file

    wav_path = tmp_dir / f"{sample_id}.wav"
    wav_path.write_bytes(wav_bytes(samples))
    physical = extract_physical_from_file(str(wav_path))
    observed_key = (
        f"{physical.key} {physical.mode}" if physical.key and physical.mode else "unknown"
    )

    features = {
        "bpm": physical.bpm,
        "spectral_centroid": physical.spectral_centroid,
        "active_rate": physical.active_rate,
        "valley_depth": physical.valley_depth,
        "onset_density": physical.onset_density,
        "observed_key": observed_key,
        "key_match_baseline": _key_match_score(baseline, observed_key),
    }
    for key, value in features.items():
        if key == "observed_key":
            continue
        if value is None:
            raise ValueError(f"sample {sample_id!r} produced None for sensor {key!r}")
    return features


def build_fixture(repetitions: int = REPETITIONS) -> dict[str, Any]:
    score = base_score()
    baseline = baseline_key(score)
    samples: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for knob_index, knob in enumerate(KNOBS):
            for level_index, level in enumerate((knob.low, knob.high)):
                for repeat in range(1, repetitions + 1):
                    seed = sample_seed(knob_index, level_index, repeat)
                    variant = score_with(score, knob.field, level)
                    style = jittered_style(
                        name=f"{knob.name}_{level_index}_{repeat}",
                        score_bpm=float(variant.physical.bpm),
                        seed=seed,
                    )
                    sample_id = (
                        f"{knob.name}_{'low' if level_index == 0 else 'high'}_r{repeat:02d}"
                    )
                    features = extract_features(
                        perform(variant, style), tmp_dir, sample_id, baseline
                    )
                    samples.append(
                        {
                            "sample_id": sample_id,
                            "knob": knob.name,
                            "level": str(level),
                            "repeat": repeat,
                            "features": features,
                        }
                    )

    knob_specs = [
        {
            "name": knob.name,
            "field": knob.field,
            "low_level": str(knob.low),
            "high_level": str(knob.high),
            "diagonal_sensor": knob.diagonal_sensor,
            "expected_sign": knob.expected_sign,
            **({"known_dead": True} if knob.known_dead else {}),
        }
        for knob in KNOBS
    ]

    return {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "fixture_id": FIXTURE_ID,
        "generator": GENERATOR,
        "base_score": "examples/composition/midnight_signal/composition_score.yaml",
        "bars_scale": BARS_SCALE,
        "bpm_jitter_sd": BPM_JITTER_SD,
        "baseline_key": baseline,
        "repetitions": repetitions,
        "knobs": knob_specs,
        "sensors": [dict(s) for s in SENSORS_CORE] + [dict(s) for s in SENSORS_EXTENDED],
        "samples": samples,
    }


def render_fixture(fixture: dict[str, Any]) -> str:
    return json.dumps(fixture, ensure_ascii=False, indent=2) + "\n"


def verify(fixture_path: Path) -> int:
    if not fixture_path.is_file():
        print(f"Missing fixture: {fixture_path}", file=sys.stderr)
        return 1
    committed = fixture_path.read_text(encoding="utf-8")
    raw = json.loads(committed)
    repetitions = int(raw.get("repetitions", REPETITIONS))
    regenerated = render_fixture(build_fixture(repetitions=repetitions))
    if committed != regenerated:
        print(f"Fixture drift: {fixture_path}", file=sys.stderr)
        return 1
    print(f"Verified K3 fixture {fixture_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--repetitions",
        type=int,
        default=REPETITIONS,
        help=f"repetitions per level, per knob (default: {REPETITIONS}; use a small value "
        "for smoke runs)",
    )
    args = parser.parse_args(argv)

    if args.verify:
        return verify(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        render_fixture(build_fixture(repetitions=args.repetitions)), encoding="utf-8"
    )
    print(f"Wrote K3 fixture to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
