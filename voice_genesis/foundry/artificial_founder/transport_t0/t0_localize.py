"""t0_localize.py — Failure Localization（設計書 §6 / §17）。

各失敗形質へ `first_divergence_stage` を付ける。判定は **S0/S1/S3/S4/S5 の
blind meter 値だけ**から行い、S2（WORLD analysis）の診断は使わない（§5 末尾）。

localization epsilon（§6）は **final PASS 閾値ではない**。「どの段で最初に
形質が動いたか」を言うためだけの分解能であり、`t0_compare` の合否には
一切使わない。この分離は型で表す: 本モジュールは criteria の
`final_tolerances` を受け取らない。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from t0_schema import (DIVERGENCE_VOCABULARY, FROZEN_FOCUS_UNITS,
                       WAVEFORM_STAGE_IDS)

#: 形質 -> blind meter の metric キー。afterglow は「Body との差」で見るので、
#: 他 2 つ（真値との差）とは扱いが違う（§9）。
TRAIT_METRIC: Dict[str, str] = {
    "duration": "onset_ms",
    "energy": "sustain_dbfs",
    "afterglow": "afterglow_extra_ms",
}

#: 段の**遷移**が起きた場所 -> §6 の語彙。S0 は起点なので遷移を持たない。
STAGE_TRANSITION: List[Tuple[str, str, str]] = [
    ("S0_BODY_44K", "S1_RESAMPLED_24K", "RESAMPLING"),
    ("S1_RESAMPLED_24K", "S3_WORLD_SYNTH_RAW", "WORLD_SYNTHESIS"),
    ("S3_WORLD_SYNTH_RAW", "S4_POST_NORM_FLOAT", "POST_SYNTH_NORMALIZATION"),
    ("S4_POST_NORM_FLOAT", "S5_PCM16_ROUNDTRIP", "PCM_PUBLICATION"),
]


def _value(measurements: Mapping[str, Any], stage: str, key: str) -> Optional[float]:
    v = (measurements.get(stage) or {}).get(key)
    if v is None or not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    return float(v) if math.isfinite(float(v)) else None


def localize_unit(measurements: Mapping[str, Any], trait: str,
                  epsilon: float) -> Dict[str, Any]:
    """1 unit・1 形質の `first_divergence_stage` を決める。

    S0 を基準に、段の遷移ごとの差分を見る。**epsilon を初めて超えた遷移**が
    first divergence。超える遷移が複数あれば `MULTI_STAGE`、どこも超えないのに
    S0→S5 の総差が epsilon を超えるなら `METER_ONLY`（各段の差は小さいのに
    積み上がっている = 計器分解能の側の問題）、値が取れなければ `UNRESOLVED`。
    """
    key = TRAIT_METRIC[trait]
    series = {s: _value(measurements, s, key) for s in WAVEFORM_STAGE_IDS}
    steps: List[Dict[str, Any]] = []
    crossed: List[str] = []
    for src, dst, label in STAGE_TRANSITION:
        a, b = series.get(src), series.get(dst)
        delta = None if (a is None or b is None) else abs(b - a)
        step = {"from": src, "to": dst, "label": label,
                "from_value": a, "to_value": b, "delta": delta,
                "exceeds_epsilon": bool(delta is not None and delta > epsilon)}
        steps.append(step)
        if step["exceeds_epsilon"]:
            crossed.append(label)

    s0, s5 = series.get("S0_BODY_44K"), series.get("S5_PCM16_ROUNDTRIP")
    total = None if (s0 is None or s5 is None) else abs(s5 - s0)
    if any(v is None for v in series.values()):
        stage = "UNRESOLVED"
    elif len(crossed) == 1:
        stage = crossed[0]
    elif len(crossed) > 1:
        stage = "MULTI_STAGE"
    elif total is not None and total > epsilon:
        stage = "METER_ONLY"
    else:
        stage = "UNRESOLVED" if total is None else "METER_ONLY"
        if total is not None and total <= epsilon:
            # どの段でも動いていない = この unit では形質は失われていない。
            stage = "UNRESOLVED"
    assert stage in DIVERGENCE_VOCABULARY
    return {"trait": trait, "metric": key, "epsilon": epsilon,
            "series": series, "steps": steps, "total_delta": total,
            "crossed_transitions": crossed, "first_divergence_stage": stage}


def _dominant(stages: Sequence[str]) -> str:
    """unit ごとの結論から形質全体の結論を決める（多数決 + 決定論的タイブレーク）。

    `UNRESOLVED`（動いていない unit）は投票から除く。全 unit が動いていない
    場合だけ `UNRESOLVED` を返す。同数の場合は §6 の語彙順で決める
    （実行ごとに揺れないようにするため）。
    """
    votes = [s for s in stages if s != "UNRESOLVED"]
    if not votes:
        return "UNRESOLVED"
    counts: Dict[str, int] = {}
    for s in votes:
        counts[s] = counts.get(s, 0) + 1
    best = max(counts.values())
    tied = [s for s in DIVERGENCE_VOCABULARY if counts.get(s, 0) == best]
    return tied[0]


def localize_trait(ledger: Mapping[str, Any], trait: str,
                   epsilon: float) -> Dict[str, Any]:
    """1 形質の localization。重点 failure unit（§17）を別掲する。"""
    units = ledger.get("units") or {}
    focus = FROZEN_FOCUS_UNITS[trait]
    per_unit: Dict[str, Any] = {}
    for stem in sorted(units):
        meas = (units[stem] or {}).get("measurements") or {}
        per_unit[stem] = localize_unit(meas, trait, epsilon)

    focus_stages = [per_unit[s]["first_divergence_stage"] for s in focus
                    if s in per_unit]
    all_stages = [row["first_divergence_stage"] for row in per_unit.values()]
    worst = max((row["total_delta"] for row in per_unit.values()
                 if row["total_delta"] is not None), default=None)
    return {
        "trait": trait,
        "metric": TRAIT_METRIC[trait],
        "epsilon": epsilon,
        "focus_units": list(focus),
        "first_divergence_stage": _dominant(focus_stages or all_stages),
        "first_divergence_stage_all_units": _dominant(all_stages),
        "worst_total_delta": worst,
        "per_unit": per_unit,
    }


def build_localization_report(ledger: Mapping[str, Any],
                              epsilon: Mapping[str, float]) -> Dict[str, Any]:
    """§17 の `localization_report.json` 本体。"""
    traits = {}
    for trait in ("duration", "energy", "afterglow"):
        traits[trait] = localize_trait(ledger, trait, float(epsilon[trait]))
    return {
        "schema": "voicegenesis-af-t0-localization/1.0",
        "note": ("localization epsilon は final PASS 閾値ではない（§6）。"
                 "S2 WORLD_ANALYSIS の診断は判定に使っていない（§5）。"),
        "epsilon": dict(epsilon),
        "traits": traits,
        "summary": {t: traits[t]["first_divergence_stage"] for t in traits},
    }


__all__ = ["TRAIT_METRIC", "STAGE_TRANSITION", "localize_unit", "localize_trait",
           "build_localization_report"]
