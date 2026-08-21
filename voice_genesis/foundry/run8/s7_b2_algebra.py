"""run8/s7_b2_algebra.py — B-2 裁定代数（`DESIGN_S7_run8.md` §12-0-C）。

H0–H5 を **必ず三値へ機械変換できる形**で凍結する。B-1 の直後・同じ PR 内で
閉じる（§10 PR-1-4）。**本番ラベル（「ritsu は悪い」「pjs は良い」）を
許容差の決定に使わない** — ε は B-1 が測定側の性質だけから出した値である。

各仮説について次を持つ（§12-0-C の要求）:

    participating axes / direction / tolerance /
    minimum supporting cells / contradiction rule / final aggregation

**ε の単位系についての明示**: B-1 が凍結する ε は生単位（ms / 比）だが、
裁定は §7-0-(0) の `z'`（話者 × 世代内で z 化し向きを正規化した量）で行う。
よって代数側は次の**換算規則**を凍結し、数値そのものは PR-2 の校正時に
同じ規則で当てる（生の MAD は本番集団から出るので今は凍結しない）:

    eps_z(axis, group) = eps_raw(axis) / (1.4826 * MAD_group(axis))

これは z 化に使う定数と同一のものであり、**新しい自由度を導入しない**。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s7_spec as sp  # noqa: E402

RESULTS_DIR = _HERE.parent / "results_s7"
SPEC_PATH = RESULTS_DIR / "trf_measurement_spec.json"

#: Gate / 校正から除外される status（§5-0 / §7-0-(0)）。除外は必ず件数で残す。
EXCLUDED_STATUSES = frozenset(
    {
        sp.STATUS_RINGING_UNCORRECTED,
        sp.STATUS_RINGING_UNCORRECTED_GROUP,
        sp.STATUS_DEGENERATE_AXIS,
        "dropped",
    }
)

#: 系列裁定の最小要件（§3 の多数決と同型に揃える）。
MIN_SCORED_SERIES = 2
MIN_LADDER_POINTS = 3
MIN_CELLS_PER_GROUP = 3


@dataclass(frozen=True)
class Cell:
    """1 セルの観測（PR-2 の probe 結果 JSON から作る）。"""

    cell_id: str
    speaker: str
    generation: str
    probe: str                      # P-RI-FINAL / P-N-FINAL / P-RI-MEDIAL / P-I-FINAL / P-SU-FINAL / P-ANCHOR
    z_primary: Optional[float]
    ladder_level: Optional[float] = None   # H1 = 尺ラダー / H4 = 境界探索の水準
    pitch_bin: Optional[str] = None
    status: str = "rendered"

    @property
    def usable(self) -> bool:
        return self.status not in EXCLUDED_STATUSES and self.z_primary is not None


def _majority(series: Sequence[str]) -> str:
    """§3 と同一の多数決（scored >= 2 / >= 2/3 / supported は refuted 0 が条件）。"""
    scored = [v for v in series if v != sp.Verdict.UNDETERMINED.value]
    if len(scored) < MIN_SCORED_SERIES:
        return sp.Verdict.UNDETERMINED.value
    n_sup = sum(1 for v in scored if v == sp.Verdict.SUPPORTED.value)
    n_ref = sum(1 for v in scored if v == sp.Verdict.REFUTED.value)
    if n_sup >= sp.MAJORITY_FRACTION * len(scored) and n_ref == 0:
        return sp.Verdict.SUPPORTED.value
    if n_ref >= sp.MAJORITY_FRACTION * len(scored):
        return sp.Verdict.REFUTED.value
    return sp.Verdict.UNDETERMINED.value


def _median(values: Sequence[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        raise ValueError("median of empty sequence")
    mid = n // 2
    return float(s[mid]) if n % 2 else float((s[mid - 1] + s[mid]) / 2.0)


def _excluded(cells: Sequence[Cell]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for c in cells:
        if not c.usable:
            key = c.status if c.status in EXCLUDED_STATUSES else "missing_value"
            out[key] = out.get(key, 0) + 1
    return out


# --- H0: 文脈依存 ----------------------------------------------------------


#: §4-2「P-ANCHOR と同条件の P-RI-FINAL（4 拍・低）の差が H0 そのもの」。
H0_MATCHED_LADDER_LEVEL = 4.0
H0_MATCHED_PITCH_BIN = "low"
#: 陽性対照は P-ANCHOR「かぎり」（§4-2）。cell_id にこの語が入る。
H0_ANCHOR_KEY = "kagiri"


def evaluate_h0(
    cells: Sequence[Cell], eps_z: float, anchor_key: str = H0_ANCHOR_KEY
) -> Dict[str, Any]:
    """実曲アンカーと同条件 probe セルの `z'` が **一致するなら反証**（§2-3 / §4-2）。

    比較対象は §4-2 が事前登録したとおり「P-ANCHOR かぎり」対
    「同一話者・同一世代の P-RI-FINAL（4 拍・低）」で、ここで条件を選び直さない。
    """
    anchor = [c for c in cells if c.probe == "P-ANCHOR" and anchor_key in c.cell_id and c.usable]
    keys = {(c.speaker, c.generation) for c in anchor}
    matched = [
        c
        for c in cells
        if c.probe == "P-RI-FINAL"
        and c.usable
        and (c.speaker, c.generation) in keys
        and c.ladder_level == H0_MATCHED_LADDER_LEVEL
        and c.pitch_bin == H0_MATCHED_PITCH_BIN
    ]
    if not anchor or not matched:
        return {
            "verdict": sp.Verdict.UNDETERMINED.value,
            "reason": "anchor_or_matched_cell_missing",
            "excluded": _excluded(cells),
        }
    delta = abs(_median([c.z_primary for c in anchor]) - _median([c.z_primary for c in matched]))
    return {
        "verdict": (
            sp.Verdict.SUPPORTED.value if delta > eps_z else sp.Verdict.REFUTED.value
        ),
        "delta_z": delta,
        "eps_z": eps_z,
        "low_power": True,
        "low_power_note": "アンカーは 1 個しか存在しないので、この裁定は 1 対の比較に依る",
        "excluded": _excluded(cells),
    }


# --- H1 / H2: ラダー -------------------------------------------------------


def _series_ladder_verdict(series_cells: Sequence[Cell], eps_z: float) -> str:
    usable = [c for c in series_cells if c.usable and c.ladder_level is not None]
    if len({c.ladder_level for c in usable}) < MIN_LADDER_POINTS:
        return sp.Verdict.UNDETERMINED.value
    ordered = sorted(usable, key=lambda c: (c.ladder_level, c.cell_id))
    values = [c.z_primary for c in ordered]
    span = values[-1] - values[0]
    steps = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    if span > eps_z and all(s >= -eps_z for s in steps):
        return sp.Verdict.SUPPORTED.value
    if abs(span) <= eps_z:
        return sp.Verdict.REFUTED.value
    return sp.Verdict.UNDETERMINED.value


def evaluate_h1(cells: Sequence[Cell], eps_z: float) -> Dict[str, Any]:
    """尺ラダーで単調性が出なければ反証（§2-3 H1）。系列 = 話者 × pitch_bin。"""
    series: Dict[str, List[Cell]] = {}
    for c in cells:
        if c.probe != "P-RI-FINAL" or c.ladder_level is None:
            continue
        series.setdefault(f"{c.speaker}/{c.pitch_bin}", []).append(c)
    verdicts = {k: _series_ladder_verdict(v, eps_z) for k, v in sorted(series.items())}
    return {
        "verdict": _majority(list(verdicts.values())),
        "series": verdicts,
        "eps_z": eps_z,
        "excluded": _excluded(cells),
    }


def evaluate_h2(cells: Sequence[Cell], eps_z: float) -> Dict[str, Any]:
    """音高ラダーで差が出なければ反証（§2-3 H2）。系列 = 話者 × 尺ラダー水準。"""
    series: Dict[str, Dict[str, List[float]]] = {}
    for c in cells:
        if c.probe != "P-RI-FINAL" or c.pitch_bin is None or not c.usable:
            continue
        key = f"{c.speaker}/L{c.ladder_level}"
        series.setdefault(key, {}).setdefault(c.pitch_bin, []).append(c.z_primary)
    verdicts: Dict[str, str] = {}
    for key, bins in sorted(series.items()):
        if "low" not in bins or "high" not in bins:
            verdicts[key] = sp.Verdict.UNDETERMINED.value
            continue
        delta = _median(bins["high"]) - _median(bins["low"])
        verdicts[key] = (
            sp.Verdict.SUPPORTED.value if abs(delta) > eps_z else sp.Verdict.REFUTED.value
        )
    return {
        "verdict": _majority(list(verdicts.values())),
        "series": verdicts,
        "eps_z": eps_z,
        "excluded": _excluded(cells),
    }


# --- H3: 音素固有性 --------------------------------------------------------

H3_CONTROL_PROBES: Tuple[str, ...] = ("P-I-FINAL", "P-SU-FINAL", "P-N-FINAL")


def evaluate_h3(cells: Sequence[Cell], eps_z: float) -> Dict[str, Any]:
    """対照 probe で同等に崩れるなら反証（§2-3 H3）。群 = 話者 × 世代。"""
    groups: Dict[str, Dict[str, List[float]]] = {}
    for c in cells:
        if not c.usable:
            continue
        if c.probe == "P-RI-FINAL" or c.probe in H3_CONTROL_PROBES:
            groups.setdefault(f"{c.speaker}/{c.generation}", {}).setdefault(
                c.probe, []
            ).append(c.z_primary)
    verdicts: Dict[str, str] = {}
    details: Dict[str, Any] = {}
    for key, probes in sorted(groups.items()):
        controls = [v for p in H3_CONTROL_PROBES for v in probes.get(p, [])]
        target = probes.get("P-RI-FINAL", [])
        n_cells = len(controls) + len(target)
        if not target or not controls or n_cells < MIN_CELLS_PER_GROUP:
            verdicts[key] = sp.Verdict.UNDETERMINED.value
            details[key] = {"reason": "insufficient_cells", "n_cells": n_cells}
            continue
        contrast = _median(target) - _median(controls)
        verdicts[key] = (
            sp.Verdict.SUPPORTED.value if contrast > eps_z else sp.Verdict.REFUTED.value
        )
        details[key] = {"contrast_z": contrast, "n_cells": n_cells}
    return {
        "verdict": _majority(list(verdicts.values())),
        "series": verdicts,
        "detail": details,
        "eps_z": eps_z,
        "excluded": _excluded(cells),
    }


# --- H4 / H5: 境界の移動 ---------------------------------------------------


def _boundary_level(series_cells: Sequence[Cell], theta: float) -> Optional[float]:
    """`z' >= theta` を最初に満たすラダー水準（§7-0-(4) の分類規則と同じ向き）。"""
    usable = sorted(
        (c for c in series_cells if c.usable and c.ladder_level is not None),
        key=lambda c: (c.ladder_level, c.cell_id),
    )
    if len({c.ladder_level for c in usable}) < MIN_LADDER_POINTS:
        return None
    for c in usable:
        if c.z_primary >= theta:
            return float(c.ladder_level)
    return float("inf")


def evaluate_h4(cells: Sequence[Cell], theta: float, generation: Optional[str] = None) -> Dict[str, Any]:
    """4 話者で境界が同一なら反証（§2-3 H4）。**同一世代内**でのみ比較する。"""
    gens = sorted({c.generation for c in cells if c.usable})
    gen = generation or (gens[0] if len(gens) == 1 else None)
    if gen is None:
        return {
            "verdict": sp.Verdict.UNDETERMINED.value,
            "reason": "generation_not_pinned_for_speaker_comparison",
            "generations_present": gens,
        }
    by_speaker: Dict[str, List[Cell]] = {}
    for c in cells:
        if c.generation != gen or c.probe != "P-RI-FINAL":
            continue
        by_speaker.setdefault(c.speaker, []).append(c)
    boundaries = {s: _boundary_level(v, theta) for s, v in sorted(by_speaker.items())}
    defined = {s: b for s, b in boundaries.items() if b is not None}
    if len(defined) < 2:
        return {
            "verdict": sp.Verdict.UNDETERMINED.value,
            "reason": "fewer_than_two_speakers_with_defined_boundary",
            "boundaries": boundaries,
            "generation": gen,
        }
    verdict = (
        sp.Verdict.SUPPORTED.value
        if len(set(defined.values())) > 1
        else sp.Verdict.REFUTED.value
    )
    return {
        "verdict": verdict,
        "boundaries": boundaries,
        "generation": gen,
        "theta": theta,
        "reach_limit": "言えるのは『境界は話者で動く』までで、原因は識別されない（§2-3 H4）",
        "excluded": _excluded(cells),
    }


def _contrast(cells: Sequence[Cell]) -> Optional[float]:
    """§5-1 / §7-0-(7) の話者内 3 対照差分（target - control）。"""
    target = [c.z_primary for c in cells if c.usable and sp.CONTRAST_PROBES.get(c.probe) == sp.TARGET_CELL_KIND]
    control = [c.z_primary for c in cells if c.usable and sp.CONTRAST_PROBES.get(c.probe) == sp.CONTROL_CELL_KIND]
    if len(target) < MIN_CELLS_PER_GROUP or len(control) < MIN_CELLS_PER_GROUP:
        return None
    return _median(target) - _median(control)


def evaluate_h5(cells: Sequence[Cell], eps_z: float) -> Dict[str, Any]:
    """世代で境界が動くか。**差分軸のみ**で裁定する（§2-3 H5・絶対値は使わない）。"""
    by_speaker_gen: Dict[str, Dict[str, List[Cell]]] = {}
    for c in cells:
        by_speaker_gen.setdefault(c.speaker, {}).setdefault(c.generation, []).append(c)
    verdicts: Dict[str, str] = {}
    details: Dict[str, Any] = {}
    for speaker, gens in sorted(by_speaker_gen.items()):
        contrasts = {g: _contrast(v) for g, v in sorted(gens.items())}
        usable = {g: v for g, v in contrasts.items() if v is not None}
        if len(usable) < 2:
            verdicts[speaker] = sp.Verdict.UNDETERMINED.value
            details[speaker] = {"reason": "fewer_than_two_generations", "contrasts": contrasts}
            continue
        spread = max(usable.values()) - min(usable.values())
        verdicts[speaker] = (
            sp.Verdict.SUPPORTED.value if spread > eps_z else sp.Verdict.REFUTED.value
        )
        details[speaker] = {"contrasts": contrasts, "spread_z": spread}
    return {
        "verdict": _majority(list(verdicts.values())),
        "series": verdicts,
        "detail": details,
        "eps_z": eps_z,
        "measured_on": "within-speaker 3-contrast difference only (absolute levels never used)",
        "excluded": _excluded(cells),
    }


def evaluate_all(cells: Sequence[Cell], eps_z: float, theta: float) -> Dict[str, Any]:
    return {
        "H0": evaluate_h0(cells, eps_z),
        "H1": evaluate_h1(cells, eps_z),
        "H2": evaluate_h2(cells, eps_z),
        "H3": evaluate_h3(cells, eps_z),
        "H4": evaluate_h4(cells, theta),
        "H5": evaluate_h5(cells, eps_z),
    }


# --- 凍結ドキュメントの生成 ------------------------------------------------

DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "H0": {
        "claim": "破綻は文脈依存（フル譜面と短い診断セルで挙動が違う）",
        "compared_pair": (
            "P-ANCHOR「かぎり」 対 同一話者・同一世代の P-RI-FINAL（4 拍・低）"
            "（§4-2 の事前登録。ここで条件を選び直さない）"
        ),
        "participating_axes": ["primary (z')"],
        "direction": "two-sided |Δz'|",
        "tolerance": "eps_z",
        "minimum_supporting_cells": 1,
        "contradiction_rule": "|Δz'| <= eps_z を refuted とする（一致 = 文脈非依存）",
        "final_aggregation": "単一対の比較。low_power フラグを必ず立てる",
    },
    "H1": {
        "claim": "持続長が閾値を超えると解放が失敗する",
        "participating_axes": ["primary (z')"],
        "direction": "尺ラダー昇順で増加",
        "tolerance": "span > eps_z かつ 各ステップ >= -eps_z",
        "minimum_supporting_cells": MIN_LADDER_POINTS,
        "contradiction_rule": "|span| <= eps_z の系列は refuted",
        "final_aggregation": "系列（話者 × pitch_bin）ごとに三値 -> §3 と同型の多数決",
    },
    "H2": {
        "claim": "低音側で失敗する（音域被覆の欠落）",
        "participating_axes": ["primary (z')"],
        "direction": "two-sided（high と low の差）",
        "tolerance": "|Δz'| > eps_z",
        "minimum_supporting_cells": 2,
        "contradiction_rule": "|Δz'| <= eps_z の系列は refuted",
        "final_aggregation": "系列（話者 × 尺水準）ごとに三値 -> 多数決",
    },
    "H3": {
        "claim": "音素 /ri/ に固有（/i/・/su/・/N/ では起きない）",
        "participating_axes": ["primary (z')"],
        "direction": "one-sided（target - control > 0）",
        "tolerance": "contrast > eps_z",
        "minimum_supporting_cells": MIN_CELLS_PER_GROUP,
        "contradiction_rule": "contrast <= eps_z の群は refuted（対照が同等に崩れている）",
        "final_aggregation": "群（話者 × 世代）ごとに三値 -> 多数決",
    },
    "H4": {
        "claim": "話者で崩壊境界が動く",
        "participating_axes": ["primary (z')", "theta (Gate の閾値)"],
        "direction": "境界 = z' >= theta を最初に満たすラダー水準",
        "tolerance": "ラダー 1 段（水準が異なること自体）",
        "minimum_supporting_cells": MIN_LADDER_POINTS,
        "contradiction_rule": "全話者で境界が同一なら refuted",
        "final_aggregation": "**同一世代内**で比較する。世代をまたいだ比較はしない",
    },
    "H5": {
        "claim": "世代（データ構成）で境界が動く",
        "participating_axes": ["within-speaker 3-contrast difference of primary (z')"],
        "direction": "two-sided（世代間の contrast の広がり）",
        "tolerance": "spread > eps_z",
        "minimum_supporting_cells": MIN_CELLS_PER_GROUP,
        "contradiction_rule": "spread <= eps_z なら refuted",
        "final_aggregation": "話者ごとに三値 -> 多数決。**絶対値は使わない**（§2-3 H5）",
    },
}


def _worked_example() -> Dict[str, Any]:
    """実装が必ず通す独立検算例（数値は説明用の架空値）。"""
    eps_z, theta = 0.30, 0.50
    cells = [
        Cell("P-ANCHOR/kagiri/ritsu/run7", "ritsu", "run7", "P-ANCHOR", 1.60),
        Cell(
            "P-RI-FINAL/ritsu/run7/L4/low",
            "ritsu",
            "run7",
            "P-RI-FINAL",
            0.40,
            ladder_level=4,
            pitch_bin="low",
        ),
        # H1: user の尺ラダー（単調増加）と pjs の平坦
        Cell("u_l1", "user", "run7", "P-RI-FINAL", 0.10, ladder_level=1, pitch_bin="mid"),
        Cell("u_l2", "user", "run7", "P-RI-FINAL", 0.70, ladder_level=2, pitch_bin="mid"),
        Cell("u_l4", "user", "run7", "P-RI-FINAL", 1.40, ladder_level=4, pitch_bin="mid"),
        Cell("p_l1", "pjs", "run7", "P-RI-FINAL", 0.05, ladder_level=1, pitch_bin="mid"),
        Cell("p_l2", "pjs", "run7", "P-RI-FINAL", 0.10, ladder_level=2, pitch_bin="mid"),
        Cell("p_l4", "pjs", "run7", "P-RI-FINAL", 0.12, ladder_level=4, pitch_bin="mid"),
    ]
    return {
        "inputs": {"eps_z": eps_z, "theta": theta, "n_cells": len(cells)},
        "H0": evaluate_h0(cells, eps_z),
        "H1": evaluate_h1(cells, eps_z),
        "H4": evaluate_h4(cells, theta, generation="run7"),
        "note": (
            "H0: |1.60 - 0.40| = 1.20 > 0.30 -> supported（文脈で違う）。"
            "H1: user 系列 span 1.30 > 0.30 で supported / pjs 系列 span 0.07 <= 0.30 で refuted。"
            "2 系列とも scored だが refuted が 0 でないため overall = undetermined "
            "（多数決規則が『supported は refuted 0 が条件』であることの検算）。"
            "H4: user 境界 = 2（0.70 >= 0.50 の最初の水準）/ pjs 境界 = inf（一度も超えない）"
            "-> 境界が異なるので supported。"
        ),
    }


def build_algebra_doc(spec: Dict[str, Any]) -> Dict[str, Any]:
    axes = spec["axes"]
    return {
        "schema": sp.B2_SCHEMA,
        "authority": "DESIGN_S7_run8.md 12-0-C",
        "generated_by": "voice_genesis/foundry/run8/s7_b2_algebra.py",
        "frozen_after": {
            "b1_spec_version": spec["spec_version"],
            "b1_freeze_status": spec["freeze_status"],
            "note": "B-2 は B-1 の直後に閉じる（§12-0-C）。B-1 が rc の間は B-2 の ε 数値も rc に従属する。",
        },
        "epsilon_raw_from_b1": {
            axis: {
                "epsilon": v["epsilon"],
                "unit": v["unit"],
                "derivation": v["epsilon_derivation"],
            }
            for axis, v in axes.items()
        },
        "epsilon_unit_conversion": {
            "rule": "eps_z(axis, group) = eps_raw(axis) / (1.4826 * MAD_group(axis))",
            "why": (
                "裁定は §7-0-(0) の z'（話者 × 世代内で中心化・向き正規化）で行うが、"
                "B-1 の ε は生単位で凍結される。換算に使う定数は z 化に使うものと同一で、"
                "新しい自由度を持ち込まない。MAD は本番集団から出るので数値は PR-2 で当てる。"
            ),
            "degenerate": "MAD == 0 の (話者×世代) は degenerate_axis として primary 候補から外す（§7-0-(0)）",
        },
        "prohibitions": {
            "no_production_labels": "ε の決定に本番ラベルを使わない（§12-0-C）",
            "no_group_separating_epsilon": "『群を一番綺麗に分ける ε』にしない",
            "absolute_levels_for_h5": "H5 は差分軸のみ。絶対値では裁定しない（§2-3 H5）",
        },
        "excluded_statuses": sorted(EXCLUDED_STATUSES),
        "aggregation_rule": {
            "scored_minimum": MIN_SCORED_SERIES,
            "majority_fraction": sp.MAJORITY_FRACTION,
            "supported_requires_zero_refuted": True,
            "same_as": "DESIGN_S7_run8.md 3 の overall H-TTD verdict",
        },
        "hypotheses": DEFINITIONS,
        "worked_example": _worked_example(),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="B-2 verdict algebra freeze (S7 12-0-C)")
    parser.add_argument("--spec", type=Path, default=SPEC_PATH)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "s7_b2_verdict_algebra.json")
    args = parser.parse_args(list(argv) if argv is not None else None)

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if spec.get("schema") != sp.TRF_SPEC_SCHEMA:
        raise ValueError(f"{args.spec}: unexpected schema {spec.get('schema')!r}")
    doc = build_algebra_doc(spec)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.out} ({len(doc['hypotheses'])} hypotheses frozen)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
