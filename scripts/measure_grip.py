"""Measure grip effect sizes from generated RPE feature fixtures.

K0 is fixture-driven: audio generation and RPE extraction happen before this
script. The committed fixture contains only per-sample numeric features, so the
fixture -> grip calculation is deterministic and light enough for CI.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from svp_rpe.control import (  # noqa: E402
    classify_grip,
    classify_match_grip,
    grip_effect_size,
    match_rate,
)
from svp_rpe.keys import weighted_key_score  # noqa: E402

SCHEMA_VERSION = "1.0"
DEFAULT_FIXTURE = ROOT / "examples" / "control" / "k0" / "musicgen_rpe_fixture.json"


@dataclass(frozen=True)
class GripResult:
    knob: str
    sensor: str
    expected_sign: int
    low_level: str
    high_level: str
    repetitions: int
    low_values: list[float]
    high_values: list[float]
    low_mean: float
    high_mean: float
    grip: float
    classification: Literal["tight", "loose", "dead"]


def load_fixture(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"fixture must be a JSON object: {path}")
    return raw


def analyze_fixture(raw: dict[str, Any], *, knob_filter: str | None = None) -> dict[str, Any]:
    fixture_id = str(raw.get("fixture_id", "unnamed_fixture"))
    repetitions = int(raw["repetitions"])
    knobs = list(raw["knobs"])
    samples = list(raw["samples"])

    results: list[dict[str, Any]] = []
    for spec in knobs:
        knob_name = str(spec["name"])
        if knob_filter and knob_name != knob_filter:
            continue
        kind = str(spec.get("kind", "continuous"))
        if kind == "categorical":
            results.append(_analyze_categorical_knob(spec, samples, repetitions))
            continue
        low_level = str(spec["low_level"])
        high_level = str(spec["high_level"])
        sensor = str(spec["sensor"])

        low_values = _values_for(samples, knob_name, low_level, sensor)
        high_values = _values_for(samples, knob_name, high_level, sensor)
        if len(low_values) != repetitions or len(high_values) != repetitions:
            raise ValueError(
                f"{knob_name} expects {repetitions} repetitions per level, "
                f"got low={len(low_values)} high={len(high_values)}"
            )

        grip = grip_effect_size(low_values, high_values)
        classification = classify_grip(grip, int(spec["expected_sign"]))
        results.append(
            asdict(
                GripResult(
                    knob=knob_name,
                    sensor=sensor,
                    expected_sign=int(spec["expected_sign"]),
                    low_level=low_level,
                    high_level=high_level,
                    repetitions=repetitions,
                    low_values=_round_list(low_values),
                    high_values=_round_list(high_values),
                    low_mean=_round_float(float(np.mean(low_values))),
                    high_mean=_round_float(float(np.mean(high_values))),
                    grip=_round_float(grip),
                    classification=classification,
                )
            )
        )

    summary = Counter(result["classification"] for result in results)
    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_id": fixture_id,
        "repetitions": repetitions,
        "results": results,
        "summary": {
            "tight": int(summary["tight"]),
            "loose": int(summary["loose"]),
            "dead": int(summary["dead"]),
        },
    }


def _analyze_categorical_knob(
    spec: dict[str, Any],
    samples: list[dict[str, Any]],
    repetitions: int,
) -> dict[str, Any]:
    """カテゴリツマミ: per-sample 一致スコアの平均（一致率）で grip を測る。

    効果量（pooled SD）はカテゴリ観測に乗らないため、controllability_poc.md §3 の
    規定どおり要求ターゲットへの一致率を grip 値として報告する。
    """
    knob_name = str(spec["name"])
    low_level = str(spec["low_level"])
    high_level = str(spec["high_level"])
    sensor = str(spec["sensor"])

    low_observed = _texts_for(samples, knob_name, low_level, sensor)
    high_observed = _texts_for(samples, knob_name, high_level, sensor)
    if len(low_observed) != repetitions or len(high_observed) != repetitions:
        raise ValueError(
            f"{knob_name} expects {repetitions} repetitions per level, "
            f"got low={len(low_observed)} high={len(high_observed)}"
        )

    low_scores = [_key_match_score(low_level, observed) for observed in low_observed]
    high_scores = [_key_match_score(high_level, observed) for observed in high_observed]
    combined_rate = match_rate(low_scores + high_scores)
    return {
        "knob": knob_name,
        "sensor": sensor,
        "kind": "categorical",
        "low_level": low_level,
        "high_level": high_level,
        "repetitions": repetitions,
        "low_observed": low_observed,
        "high_observed": high_observed,
        "low_values": _round_list(low_scores),
        "high_values": _round_list(high_scores),
        "low_mean": _round_float(match_rate(low_scores)),
        "high_mean": _round_float(match_rate(high_scores)),
        "grip": _round_float(combined_rate),
        "classification": classify_match_grip(combined_rate),
    }


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# K0 grip measurement\n")
    lines.append(f"- fixture: `{report['fixture_id']}`")
    lines.append(f"- repetitions: {report['repetitions']}")
    lines.append("")
    lines.append(
        "| knob | sensor | low | high | mean low | mean high | grip | class |"
    )
    lines.append("|---|---|---|---|---:|---:|---:|---|")
    for result in report["results"]:
        lines.append(
            f"| {result['knob']} "
            f"| {result['sensor']} "
            f"| {result['low_level']} "
            f"| {result['high_level']} "
            f"| {result['low_mean']:.6g} "
            f"| {result['high_mean']:.6g} "
            f"| {result['grip']:.6g} "
            f"| {result['classification']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure K0 grip effect sizes from numeric RPE feature fixtures.",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE,
        help=f"fixture JSON path (default: {DEFAULT_FIXTURE.relative_to(ROOT)})",
    )
    parser.add_argument("--knob", help="only report one knob by name")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = parser.parse_args(argv)

    report = analyze_fixture(load_fixture(args.fixture), knob_filter=args.knob)
    sys.stdout.write(render_json(report) if args.json else render_markdown(report))
    return 0


def _values_for(
    samples: list[dict[str, Any]],
    knob_name: str,
    level: str,
    sensor: str,
) -> list[float]:
    values: list[float] = []
    for sample in samples:
        if str(sample["knob"]) != knob_name or str(sample["level"]) != level:
            continue
        values.append(_feature_value(sample["features"], sensor))
    return values


def _texts_for(
    samples: list[dict[str, Any]],
    knob_name: str,
    level: str,
    sensor: str,
) -> list[str]:
    texts: list[str] = []
    for sample in samples:
        if str(sample["knob"]) != knob_name or str(sample["level"]) != level:
            continue
        texts.append(str(_sensor_node(sample["features"], sensor)))
    return texts


def _key_match_score(target: str, observed: str) -> float:
    """要求 key への per-sample 一致スコア（mir_eval 方式、∈[0,1]）。

    mir_eval が無い環境では正規化済み完全一致のフォールバック
    （semantic_ci/audit.py の key needle と同方針）。
    """
    return weighted_key_score(target, observed).score


def _sensor_node(features: dict[str, Any], sensor: str) -> Any:
    current: Any = features
    for part in sensor.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"missing sensor path {sensor!r}")
        current = current[part]
    return current


def _feature_value(features: dict[str, Any], sensor: str) -> float:
    value = float(_sensor_node(features, sensor))
    if not np.isfinite(value):
        raise ValueError(f"sensor path {sensor!r} is not finite")
    return value


def _round_float(value: float) -> float:
    return round(float(value), 6)


def _round_list(values: list[float]) -> list[float]:
    return [_round_float(value) for value in values]


if __name__ == "__main__":
    sys.exit(main())
