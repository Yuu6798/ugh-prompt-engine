"""Convert the K2-schema MusicGen extract fixture into the K3 orthogonality fixture format.

K3-2b — `scripts/collect_musicgen_takes.py extract` が書き出す K2 スキーマの
実測 fixture（既定パス `examples/control/k3/musicgen_matrix_extract.json`。生成
バッチ完了後に配置される — 本スクリプト作成時点ではまだ存在しない）を、5 ツマミ
フル行列の K3 直交性スキーマへ決定論変換する。`docs/controllability_poc.md`
§5.4「K3-2b への設計指示」の 3 条件（(a) known-dead ノブ行の同梱、(b) R>=8、
(c) ベースライン key の宣言）は `examples/control/k3/musicgen_matrix_plan.yaml`
側（生成計画）で既に満たされている。本スクリプトはその実測結果を計器
（`scripts/measure_orthogonality.py`）が読める形へ純粋に reshape するのみで、
新規の数値計算は key センサー化（`key_match_baseline`）以外に一切行わない
（`scripts/build_k3_suno_mini_fixture.py` と同じ Pure JSON reshaping 原則）。

key センサー化: `scripts/build_k3_fixture.py` の `key_match_baseline`
（= `scripts/measure_grip.py` の `_key_match_score(baseline, observed)`）と同じ
規約を踏襲する。ソースの生 `key` 観測値と `BASELINE_KEY`（"C major" —
`musicgen_matrix_plan.yaml` の全プロンプトが共有するベースライン）から
per-sample に計算し、`features.key_match_baseline` として追記する。fixture
トップレベルにも `baseline_key` を記録する（`build_k3_fixture.py` の規約）。

known_dead 宣言: `active_rate_target` / `valley_depth_target` の 2 行は
known_dead=true を付与する。根拠は K3-1b 型の宣言（散文判断を計器に入力する）
——「active rate target 0.90-0.95」のような数値レンジ文字列は MIR 内部指標の
ジャーゴンであり、MusicGen のようなテキスト条件付け生成器の可読語彙の外にある
（自然言語が持つ「密度」「静寂」等の記述表現とは別物で、生成器がプロンプト中の
このトークン列を意味ある制御信号として解釈するとは期待できない）。この 2 行の
全セル（対角含む）は seed のみに駆動される経験的ヌル分布として
`scripts/measure_orthogonality.py` のノイズ天井計算に供給される。

Usage:
    python scripts/build_k3_musicgen_fixture.py             # fixture を再生成
    python scripts/build_k3_musicgen_fixture.py --verify    # コミット済み fixture と一致検証
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.measure_grip import _key_match_score  # noqa: E402

SOURCE_FIXTURE = ROOT / "examples" / "control" / "k3" / "musicgen_matrix_extract.json"
SOURCE_FIXTURE_REL = "examples/control/k3/musicgen_matrix_extract.json"
DEFAULT_FIXTURE = ROOT / "examples" / "control" / "k3" / "musicgen_matrix_fixture.json"

FIXTURE_SCHEMA_VERSION = "1.0"
FIXTURE_ID = "k3_2b_musicgen_matrix_rpe_features"

# ベースライン key。`musicgen_matrix_plan.yaml` の全プロンプトが共有する
# "in the key of C major" と一致させる（key 行のみ high 水準で差し替える）。
BASELINE_KEY = "C major"

# ソース fixture（K2 スキーマ、`collect_musicgen_takes.py extract` 出力）が
# 宣言する 5 ツマミの想定形。ソース側の knob 名 / raw sensor / expected_sign が
# これと食い違えば fail fast する。`source_sensor` はソースの生 `features` 内の
# パス（key 行のみ変換前の `key`）、`diagonal_sensor` は本変換後の K3 fixture が
# 対角に据えるセンサー名（key 行のみ `key_match_baseline` へ変換される）。
EXPECTED_KNOBS: tuple[dict[str, Any], ...] = (
    {
        "name": "bpm",
        "field": "bpm",
        "source_sensor": "bpm",
        "diagonal_sensor": "bpm",
        "expected_sign": 1,
    },
    {
        "name": "key",
        "field": "key",
        "source_sensor": "key",
        "diagonal_sensor": "key_match_baseline",
        "expected_sign": -1,
    },
    {
        "name": "brightness",
        "field": "brightness",
        "source_sensor": "spectral_centroid",
        "diagonal_sensor": "spectral_centroid",
        "expected_sign": 1,
    },
    {
        "name": "active_rate_target",
        "field": "active_rate_target",
        "source_sensor": "active_rate",
        "diagonal_sensor": "active_rate",
        "expected_sign": 1,
        "known_dead": True,
    },
    {
        "name": "valley_depth_target",
        "field": "valley_depth_target",
        "source_sensor": "valley_depth",
        "diagonal_sensor": "valley_depth",
        "expected_sign": 1,
        "known_dead": True,
    },
)

# core = 5 knobs 分の対角センサー（knob 順と一致させる必要がある —
# scripts/measure_orthogonality.py の analyze_orthogonality が zip(knobs, core)
# で対角一致を検証する）。
SENSORS_CORE: tuple[dict[str, str], ...] = (
    {"name": "bpm", "path": "bpm", "kind": "core"},
    {"name": "key_match_baseline", "path": "key_match_baseline", "kind": "core"},
    {"name": "spectral_centroid", "path": "spectral_centroid", "kind": "core"},
    {"name": "active_rate", "path": "active_rate", "kind": "core"},
    {"name": "valley_depth", "path": "valley_depth", "kind": "core"},
)
# brightness_band_ratio = K1/suno_mini 由来の legacy 帯域比センサーを、MusicGen
# の干渉列として観測し直す（suno_mini 変換器の同名センサーの扱いを踏襲）。
SENSORS_EXTENDED: tuple[dict[str, str], ...] = (
    {"name": "brightness_band_ratio", "path": "spectral_profile.brightness", "kind": "extended"},
)


def load_source(path: Path = SOURCE_FIXTURE) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"source fixture must be a JSON object: {path}")
    return raw


def _with_key_match_baseline(features: dict[str, Any]) -> dict[str, Any]:
    """features に `key_match_baseline` を追記した新しい dict を返す。"""
    observed_key = str(features["key"])
    updated = dict(features)
    updated["key_match_baseline"] = _key_match_score(BASELINE_KEY, observed_key)
    return updated


def convert(raw: dict[str, Any]) -> dict[str, Any]:
    """K2 スキーマの MusicGen extract fixture dict を K3 直交性 fixture dict へ決定論変換する。"""
    source_knobs = list(raw["knobs"])
    if len(source_knobs) != len(EXPECTED_KNOBS):
        raise ValueError(
            f"source fixture must declare {len(EXPECTED_KNOBS)} knobs, "
            f"got {len(source_knobs)}"
        )

    knob_specs: list[dict[str, Any]] = []
    for index, (expected, source_knob) in enumerate(zip(EXPECTED_KNOBS, source_knobs)):
        name = str(source_knob["name"])
        source_sensor = str(source_knob["sensor"])
        expected_sign = int(source_knob["expected_sign"])
        if (
            name != expected["name"]
            or source_sensor != expected["source_sensor"]
            or expected_sign != expected["expected_sign"]
        ):
            raise ValueError(
                f"unexpected source knob at index {index}: "
                f"got name={name!r} sensor={source_sensor!r} expected_sign={expected_sign!r}, "
                f"expected name={expected['name']!r} sensor={expected['source_sensor']!r} "
                f"expected_sign={expected['expected_sign']!r}"
            )
        spec: dict[str, Any] = {
            "name": expected["name"],
            "field": expected["field"],
            "low_level": str(source_knob["low_level"]),
            "high_level": str(source_knob["high_level"]),
            "diagonal_sensor": expected["diagonal_sensor"],
            "expected_sign": expected_sign,
        }
        if expected.get("known_dead"):
            spec["known_dead"] = True
        knob_specs.append(spec)

    samples = [
        {**sample, "features": _with_key_match_baseline(dict(sample["features"]))}
        for sample in raw["samples"]
    ]

    return {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "fixture_id": FIXTURE_ID,
        "generator": str(raw["generator"]),
        "source_fixture": SOURCE_FIXTURE_REL,
        "baseline_key": BASELINE_KEY,
        "repetitions": int(raw["repetitions"]),
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
    regenerated = render_fixture(convert(load_source()))
    if committed != regenerated:
        print(f"Fixture drift: {fixture_path}", file=sys.stderr)
        return 1
    print(f"Verified K3 musicgen matrix fixture {fixture_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)

    if args.verify:
        return verify(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_fixture(convert(load_source())), encoding="utf-8")
    print(f"Wrote K3 musicgen matrix fixture to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
