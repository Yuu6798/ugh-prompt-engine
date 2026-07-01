"""Measure the K3 knob-vs-sensor interference matrix from a fixture.

K3 extends the K0-K2 diagonal grip harness to an N×N matrix: for every
(knob, sensor) pair we measure the A/B effect size the knob's low/high
contrast leaves on that sensor, not only on its own diagonal sensor. The
diagonal cells reuse the existing grip classification (tight/loose/dead);
off-diagonal cells are classified as interference (clean/weak/strong). DCI
(disentanglement / completeness) and an effect-size-gap ("MIG-like") summary
are computed on the square core-sensor block.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.measure_grip import (  # noqa: E402
    _feature_value,
    _round_float,
    _round_list,
    load_fixture,
)
from svp_rpe.control import (  # noqa: E402
    IMPORTANCE_CAP,
    IMPORTANCE_FLOOR,
    classify_grip,
    classify_interference,
    completeness_scores,
    disentanglement_scores,
    effect_size_gap,
    grip_effect_size,
    importance_matrix,
    mass_weighted_overall,
)

SCHEMA_VERSION = "1.0"
DEFAULT_FIXTURE = ROOT / "examples" / "control" / "k3" / "synth_performer_matrix_fixture.json"


def _values_for(
    samples: list[dict[str, Any]],
    knob_name: str,
    level: str,
    sensor_path: str,
) -> list[float]:
    values: list[float] = []
    for sample in samples:
        if str(sample["knob"]) != knob_name or str(sample["level"]) != level:
            continue
        values.append(_feature_value(sample["features"], sensor_path))
    return values


def analyze_orthogonality(raw: dict[str, Any]) -> dict[str, Any]:
    fixture_id = str(raw.get("fixture_id", "unnamed_fixture"))
    generator = str(raw.get("generator", "unknown"))
    repetitions = int(raw["repetitions"])
    knobs = list(raw["knobs"])
    sensors = list(raw["sensors"])
    samples = list(raw["samples"])

    core_sensors = [s for s in sensors if str(s.get("kind")) == "core"]
    extended_sensors = [s for s in sensors if str(s.get("kind")) == "extended"]

    if len(core_sensors) != len(knobs):
        raise ValueError(
            f"core sensor count ({len(core_sensors)}) must match knob count ({len(knobs)})"
        )
    for i, (knob, sensor) in enumerate(zip(knobs, core_sensors)):
        diagonal_sensor = str(knob["diagonal_sensor"])
        sensor_name = str(sensor["name"])
        if diagonal_sensor != sensor_name:
            raise ValueError(
                f"diagonal_sensor mismatch at index {i}: "
                f"knob {knob['name']!r} expects {diagonal_sensor!r}, "
                f"core sensor is {sensor_name!r}"
            )

    ordered_sensors = core_sensors + extended_sensors
    core_names = [str(s["name"]) for s in core_sensors]
    extended_names = [str(s["name"]) for s in extended_sensors]

    cells: list[dict[str, Any]] = []
    effects_core_raw: list[list[float]] = []
    effects_extended_raw: list[list[float]] = []

    for knob in knobs:
        knob_name = str(knob["name"])
        low_level = str(knob["low_level"])
        high_level = str(knob["high_level"])
        expected_sign = int(knob["expected_sign"])

        core_row: list[float] = []
        extended_row: list[float] = []
        for sensor in ordered_sensors:
            sensor_name = str(sensor["name"])
            sensor_kind = str(sensor["kind"])
            sensor_path = str(sensor["path"])

            low_values = _values_for(samples, knob_name, low_level, sensor_path)
            high_values = _values_for(samples, knob_name, high_level, sensor_path)
            if len(low_values) != repetitions or len(high_values) != repetitions:
                raise ValueError(
                    f"{knob_name}/{sensor_name} expects {repetitions} repetitions per level, "
                    f"got low={len(low_values)} high={len(high_values)}"
                )

            effect = grip_effect_size(low_values, high_values)
            is_diagonal = sensor_kind == "core" and sensor_name == str(knob["diagonal_sensor"])

            cell: dict[str, Any] = {
                "knob": knob_name,
                "sensor": sensor_name,
                "sensor_kind": sensor_kind,
                "is_diagonal": is_diagonal,
                "low_mean": _round_float(float(np.mean(low_values))),
                "high_mean": _round_float(float(np.mean(high_values))),
                "effect": _round_float(effect),
                "interference_class": classify_interference(effect),
            }
            if is_diagonal:
                cell["expected_sign"] = expected_sign
                cell["diagonal_classification"] = classify_grip(effect, expected_sign)
            cells.append(cell)

            if sensor_kind == "core":
                core_row.append(effect)
            else:
                extended_row.append(effect)

        effects_core_raw.append(core_row)
        effects_extended_raw.append(extended_row)

    importance = importance_matrix(effects_core_raw)
    row_masses = importance.sum(axis=1).tolist()
    col_masses = importance.sum(axis=0).tolist()

    disentanglement = disentanglement_scores(importance)
    completeness = completeness_scores(importance)
    gaps = effect_size_gap(importance)

    knob_names = [str(k["name"]) for k in knobs]

    overall_disentanglement = mass_weighted_overall(disentanglement, row_masses)
    overall_completeness = mass_weighted_overall(completeness, col_masses)
    non_none_gaps = [g for g in gaps if g is not None]
    mean_gap = float(np.mean(non_none_gaps)) if non_none_gaps else None

    dci = {
        "importance_floor": _round_float(IMPORTANCE_FLOOR),
        "importance_cap": _round_float(IMPORTANCE_CAP),
        "disentanglement": {
            "per_knob": {
                name: (_round_float(score) if score is not None else None)
                for name, score in zip(knob_names, disentanglement)
            },
            "overall": _round_float(overall_disentanglement)
            if overall_disentanglement is not None
            else None,
        },
        "completeness": {
            "per_sensor": {
                name: (_round_float(score) if score is not None else None)
                for name, score in zip(core_names, completeness)
            },
            "overall": _round_float(overall_completeness)
            if overall_completeness is not None
            else None,
        },
        "effect_size_gap": {
            "per_sensor": {
                name: (_round_float(gap) if gap is not None else None)
                for name, gap in zip(core_names, gaps)
            },
            "mean": _round_float(mean_gap) if mean_gap is not None else None,
        },
    }

    diagonal_summary: Counter[str] = Counter()
    interference_summary: Counter[str] = Counter()
    for cell in cells:
        if cell["is_diagonal"]:
            diagonal_summary[cell["diagonal_classification"]] += 1
        else:
            interference_summary[cell["interference_class"]] += 1

    dead_knobs = [name for name, score in zip(knob_names, disentanglement) if score is None]
    untouched_sensors = [
        name for name, score in zip(core_names, completeness) if score is None
    ]

    matrix = {
        "sensors_core": core_names,
        "sensors_extended": extended_names,
        "effects_core": [_round_list(row) for row in effects_core_raw],
        "effects_extended": [_round_list(row) for row in effects_extended_raw],
        "importance_core": [_round_list(row.tolist()) for row in importance],
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_id": fixture_id,
        "generator": generator,
        "repetitions": repetitions,
        "knobs": knob_names,
        "cells": cells,
        "matrix": matrix,
        "dci": dci,
        "diagonal_summary": {
            "tight": int(diagonal_summary["tight"]),
            "loose": int(diagonal_summary["loose"]),
            "dead": int(diagonal_summary["dead"]),
        },
        "interference_summary": {
            "clean": int(interference_summary["clean"]),
            "weak": int(interference_summary["weak"]),
            "strong": int(interference_summary["strong"]),
        },
        "dead_knobs": dead_knobs,
        "untouched_sensors": untouched_sensors,
    }


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"


def _fmt(value: float | None, spec: str = ".3g") -> str:
    if value is None:
        return "—"
    return format(value, spec)


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# K3 orthogonality matrix\n")
    lines.append(f"- fixture: `{report['fixture_id']}`")
    lines.append(f"- generator: `{report['generator']}`")
    lines.append(f"- repetitions: {report['repetitions']}")
    lines.append("")

    matrix = report["matrix"]
    sensor_names = matrix["sensors_core"] + matrix["sensors_extended"]
    header = ["knob \\ sensor"] + [
        f"{name}†" if name in matrix["sensors_extended"] else name for name in sensor_names
    ]
    lines.append("## Effect-size matrix\n")
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))

    cells_by_knob: dict[str, dict[str, dict[str, Any]]] = {}
    for cell in report["cells"]:
        cells_by_knob.setdefault(cell["knob"], {})[cell["sensor"]] = cell

    for knob in report["knobs"]:
        row_cells = cells_by_knob[knob]
        row = [knob]
        for sensor in sensor_names:
            cell = row_cells[sensor]
            text = _fmt(cell["effect"])
            if cell["is_diagonal"]:
                text = f"**{text}**"
            elif cell["interference_class"] == "strong":
                text = f"{text} ⚠"
            row.append(text)
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    dci = report["dci"]
    lines.append("## DCI\n")
    lines.append("### Disentanglement (per knob)\n")
    lines.append("| knob | disentanglement |")
    lines.append("|---|---:|")
    for knob in report["knobs"]:
        lines.append(f"| {knob} | {_fmt(dci['disentanglement']['per_knob'][knob], '.4g')} |")
    lines.append(f"\n- overall disentanglement: {_fmt(dci['disentanglement']['overall'], '.4g')}")
    lines.append("")

    lines.append("### Completeness / effect_size_gap (per sensor)\n")
    lines.append("| sensor | completeness | effect_size_gap |")
    lines.append("|---|---:|---:|")
    for sensor in matrix["sensors_core"]:
        lines.append(
            f"| {sensor} "
            f"| {_fmt(dci['completeness']['per_sensor'][sensor], '.4g')} "
            f"| {_fmt(dci['effect_size_gap']['per_sensor'][sensor], '.4g')} |"
        )
    lines.append(f"\n- overall completeness: {_fmt(dci['completeness']['overall'], '.4g')}")
    lines.append(f"- mean effect_size_gap: {_fmt(dci['effect_size_gap']['mean'], '.4g')}")
    lines.append("")

    lines.append("## Summary\n")
    ds = report["diagonal_summary"]
    is_ = report["interference_summary"]
    lines.append(
        f"- diagonal: tight={ds['tight']} loose={ds['loose']} dead={ds['dead']}"
    )
    lines.append(
        f"- interference (off-diagonal): clean={is_['clean']} weak={is_['weak']} "
        f"strong={is_['strong']}"
    )
    dead_knobs = report["dead_knobs"]
    lines.append(f"- dead knobs: {', '.join(dead_knobs) if dead_knobs else 'none'}")
    untouched = report["untouched_sensors"]
    lines.append(f"- untouched sensors: {', '.join(untouched) if untouched else 'none'}")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure the K3 knob-vs-sensor interference matrix from a fixture.",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE,
        help=f"fixture JSON path (default: {DEFAULT_FIXTURE.relative_to(ROOT)})",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = parser.parse_args(argv)

    report = analyze_orthogonality(load_fixture(args.fixture))
    sys.stdout.write(render_json(report) if args.json else render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
