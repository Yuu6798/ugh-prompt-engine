"""Reshape the K2(#117) Suno grip fixture into the K3 orthogonality fixture format.

K3-2a — K2 の本物 Suno fixture（examples/control/k2/suno_rpe_fixture.json）を
K3 直交性スキーマへ決定論変換する。新規生成ゼロで実生成器（Suno）の 2x2 コア
（bpm x spectral_centroid）+ extended 干渉列（active_rate / valley_depth /
brightness_band_ratio）を scripts/measure_orthogonality.py で測れるようにする。
音源は repo 外のまま（audio_sha256 provenance は K2 由来のまま保全し、K3 側で
新規に生成・追記しない）。

Pure JSON reshaping only — 音声処理・数値計算は一切行わない。samples の
features はソースの値をそのまま透過コピーする。

Usage:
    python scripts/build_k3_suno_mini_fixture.py             # fixture を再生成
    python scripts/build_k3_suno_mini_fixture.py --verify    # コミット済み fixture と一致検証
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

SOURCE_FIXTURE = ROOT / "examples" / "control" / "k2" / "suno_rpe_fixture.json"
SOURCE_FIXTURE_REL = "examples/control/k2/suno_rpe_fixture.json"
DEFAULT_FIXTURE = ROOT / "examples" / "control" / "k3" / "suno_mini_matrix_fixture.json"

FIXTURE_SCHEMA_VERSION = "1.0"
FIXTURE_ID = "k3_2a_suno_mini_matrix_rpe_features"

# K2 fixture が宣言する 2 ツマミの想定形。field 名はソース fixture に無いため
# ここで固定する（K1/K3 の PhysicalLayer フィールド命名に合わせる）。ソース側の
# knob 名 / 対角センサー名がこれと食い違えば fail fast する。
EXPECTED_KNOBS: tuple[dict[str, str], ...] = (
    {"name": "bpm", "field": "bpm", "diagonal_sensor": "bpm"},
    {"name": "brightness", "field": "brightness", "diagonal_sensor": "spectral_centroid"},
)

SENSORS_CORE: tuple[dict[str, str], ...] = (
    {"name": "bpm", "path": "bpm", "kind": "core"},
    {"name": "spectral_centroid", "path": "spectral_centroid", "kind": "core"},
)
# brightness_band_ratio = K1 で dead だった legacy 帯域比センサーを、実音源
# （Suno）の干渉列として観測し直す。
SENSORS_EXTENDED: tuple[dict[str, str], ...] = (
    {"name": "active_rate", "path": "active_rate", "kind": "extended"},
    {"name": "valley_depth", "path": "valley_depth", "kind": "extended"},
    {"name": "brightness_band_ratio", "path": "spectral_profile.brightness", "kind": "extended"},
)


def load_source(path: Path = SOURCE_FIXTURE) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"source fixture must be a JSON object: {path}")
    return raw


def convert(raw: dict[str, Any]) -> dict[str, Any]:
    """K2 の生 fixture dict を K3 直交性 fixture dict へ決定論変換する。"""
    source_knobs = list(raw["knobs"])
    if len(source_knobs) != len(EXPECTED_KNOBS):
        raise ValueError(
            f"source fixture must declare {len(EXPECTED_KNOBS)} knobs, "
            f"got {len(source_knobs)}"
        )

    knob_specs: list[dict[str, Any]] = []
    for index, (expected, source_knob) in enumerate(zip(EXPECTED_KNOBS, source_knobs)):
        name = str(source_knob["name"])
        diagonal_sensor = str(source_knob["sensor"])
        if name != expected["name"] or diagonal_sensor != expected["diagonal_sensor"]:
            raise ValueError(
                f"unexpected source knob at index {index}: "
                f"got name={name!r} sensor={diagonal_sensor!r}, "
                f"expected name={expected['name']!r} sensor={expected['diagonal_sensor']!r}"
            )
        knob_specs.append(
            {
                "name": expected["name"],
                "field": expected["field"],
                "low_level": str(source_knob["low_level"]),
                "high_level": str(source_knob["high_level"]),
                "diagonal_sensor": expected["diagonal_sensor"],
                "expected_sign": int(source_knob["expected_sign"]),
            }
        )

    return {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "fixture_id": FIXTURE_ID,
        "generator": str(raw["generator"]),
        "source_fixture": SOURCE_FIXTURE_REL,
        "repetitions": int(raw["repetitions"]),
        "knobs": knob_specs,
        "sensors": [dict(s) for s in SENSORS_CORE] + [dict(s) for s in SENSORS_EXTENDED],
        "samples": list(raw["samples"]),
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
    print(f"Verified K3 suno mini fixture {fixture_path}")
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
    print(f"Wrote K3 suno mini fixture to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
