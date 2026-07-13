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
    MATCH_TIGHT_MIN,
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
    # summary_gated: categorical 経路のヌルゲート（`gated_classification`）適用後の
    # 集計。continuous 結果には `gated_classification` が存在しないため生の
    # `classification`（変更なし）にフォールバックする — additive な併記であり、
    # 既存の `summary` はここでは一切変更しない。
    summary_gated = Counter(
        result.get("gated_classification", result["classification"]) for result in results
    )
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
        "summary_gated": {
            "tight": int(summary_gated["tight"]),
            "loose": int(summary_gated["loose"]),
            "dead": int(summary_gated["dead"]),
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

    score_fn = _key_match_score if sensor == "key" else _exact_match_score
    low_scores = [score_fn(low_level, observed) for observed in low_observed]
    high_scores = [score_fn(high_level, observed) for observed in high_observed]
    combined_rate = match_rate(low_scores + high_scores)
    low_mean = _round_float(match_rate(low_scores))
    high_mean = _round_float(match_rate(high_scores))
    classification = classify_match_grip(combined_rate)

    # 事前登録ヌルゲート（m2_plan.yaml §3、K3-1b 由来の known-dead ヌル天井と同型）:
    # high セルの一致率が low セルを下回るなら、combined match_rate の機械分類
    # （loose に見えうる）によらず dead へ格下げる。既存の `classification` は
    # 計器の生出力として温存し、本フィールドは additive に併記する
    # （categorical 経路のみ・continuous の effect_size 経路には適用しない）。
    #
    # 等号規則の原理（#174 Codex P2 第 2 ラウンド採用・第 5 ラウンドで 1+s へ
    # 一般化）: 静的（処方非依存）出力の 1 観測は両レベル合計で最大 1 + s
    # （s = レベル間相互スコア = 当該 knob の match 計算に実際に使うスコア関数で
    # low_level と high_level を相互採点した値。非対称スコアラーに備え max を
    # 採る）しか取れない。排他的完全一致センサーは s=0 で従来の和 <= 1 に帰着
    # （0.5/0.5 = 静的コインで説明可能につき発火、1.0/1.0 や 0.9/0.9 の和 > 1 は
    # 静的出力で説明不能＝応答性の実証につき非発火）。fuzzy スコアラー
    # （key の五度部分点等）では境界が 1+s に上がる — 五度部分点は方向付き
    # （C 参照×G 観測 0.5 / 逆方向 0）のため文字どおりの C/G 静的 50/50 混合は
    # 0.75/0.5 で strict 分岐が発火し、等号境界を突く 0.75/0.75（和 1.5 = 1+0.5）
    # も等号分岐で発火。1.0/1.0（和 2.0 > 1.5）は完全一致必須につき静的で説明
    # 不能＝免除。将来の fuzzy スコアラーにも自動で一般化される。
    # structure 計器の 0.667/0.667 dead 前例は非排他マッチの別計器であり
    # 本規則の対象外。
    #
    # high セル tight 免除（#174 Codex P2 第 3 ラウンド採用）: ゲートの目的は
    # low/デフォルトセルが combined を押し上げる誤読の防止である。非改善
    # （high < low）でも high セル単独が tight 水準（>= MATCH_TIGHT_MIN）に達して
    # いれば high 処方は実現されており誤読は発生しない＝免除する
    # （例 low 1.0 / high 0.875、combined 0.9375 tight を温存。第 2 ラウンドまでの
    # 「厳密不等号は常に発火」を精密化した合成規則）。M2 実データ high 0.125 は
    # tight 水準未達につき引き続き発火し裁定不変。
    cross_score = max(score_fn(low_level, high_level), score_fn(high_level, low_level))
    null_gate_fired = (
        (high_mean < low_mean and high_mean < MATCH_TIGHT_MIN)
        or (high_mean == low_mean and (low_mean + high_mean) <= 1.0 + cross_score)
    )
    gated_classification = "dead" if null_gate_fired else classification

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
        "low_mean": low_mean,
        "high_mean": high_mean,
        "grip": _round_float(combined_rate),
        "classification": classification,
        "null_gate_fired": null_gate_fired,
        "gated_classification": gated_classification,
    }


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"


def render_markdown(report: dict[str, Any]) -> str:
    # gate / gated class 列（#174 Codex P2 第 4 ラウンド採用）: gated 値が JSON
    # 出力のみだと既定 CLI（markdown）は stock classification を表示し続け、
    # ヌルゲートの dead 格下げが隠れる。生値（class / summary）と裁定
    # （gated class / summary_gated）の並記は本 PR の一貫方針。continuous 行は
    # ゲート対象外のため "—" を表示する。
    lines: list[str] = []
    lines.append("# K0 grip measurement\n")
    lines.append(f"- fixture: `{report['fixture_id']}`")
    lines.append(f"- repetitions: {report['repetitions']}")
    lines.append("")
    lines.append(
        "| knob | sensor | low | high | mean low | mean high | grip | class "
        "| gate | gated class |"
    )
    lines.append("|---|---|---|---|---:|---:|---:|---|---|---|")
    for result in report["results"]:
        if "null_gate_fired" in result:
            gate_cell = "fired" if result["null_gate_fired"] else "not fired"
            gated_class_cell = str(result["gated_classification"])
        else:
            gate_cell = "—"
            gated_class_cell = "—"
        lines.append(
            f"| {result['knob']} "
            f"| {result['sensor']} "
            f"| {result['low_level']} "
            f"| {result['high_level']} "
            f"| {result['low_mean']:.6g} "
            f"| {result['high_mean']:.6g} "
            f"| {result['grip']:.6g} "
            f"| {result['classification']} "
            f"| {gate_cell} "
            f"| {gated_class_cell} |"
        )
    summary = report["summary"]
    summary_gated = report["summary_gated"]
    lines.append("")
    lines.append(
        f"- summary (stock): tight {summary['tight']} / loose {summary['loose']} "
        f"/ dead {summary['dead']}"
    )
    lines.append(
        f"- summary_gated: tight {summary_gated['tight']} / loose {summary_gated['loose']} "
        f"/ dead {summary_gated['dead']}"
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


def _exact_match_score(target: str, observed: str) -> float:
    """key 以外の categorical センサー向けの汎用一致スコア（casefold + 空白正規化の完全一致）。

    `_key_match_score`（mir_eval 経由の音楽 key 専用ファジーマッチ）は
    "4/4"/"3/4" のような非 key 文字列に対しては意味を持たない
    （最良でも exact-match フォールバックに落ちるだけで、近縁調のような
    段階採点は原理的に成立しない）。sensor が "key" でない categorical ノブは
    この関数で採点する — mir_eval には一切ルーティングしない。
    """
    target_norm = " ".join(str(target).casefold().split())
    observed_norm = " ".join(str(observed).casefold().split())
    return 1.0 if target_norm == observed_norm else 0.0


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
