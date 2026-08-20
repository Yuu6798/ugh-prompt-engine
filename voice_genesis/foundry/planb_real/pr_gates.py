"""planb_real/pr_gates.py — 実コーパス版ゲート（instruction §10・§11）。

surrogate PoC のゲートを流用せず、実コーパス用に別建てで持つ（§9: 2 つの
結果を別 ledger に保つ）。事前登録 primary axis は **結果を見てから変えない**
（§0-8）。`nasal_gain_shape_db` は exploratory のまま保持し、ゲートに使わない
（§11）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

_PLANB = Path(__file__).resolve().parent.parent / "planb"
if str(_PLANB) not in sys.path:
    sys.path.insert(0, str(_PLANB))

import pb_compose as pc  # noqa: E402
import pb_gates as pbg  # noqa: E402
from pb_tracks import PerformanceToggles  # noqa: E402

from pr_performance import RealPerformanceTrack, assert_no_spectral_payload  # noqa: E402
from pr_status import BLOCKED, FAIL, NOT_EVALUABLE, PASS, GateResult  # noqa: E402

#: 事前登録 primary axis（surrogate PoC で凍結済み。結果を見て変えない = §0-8）
REGISTERED_PRIMARY_AXIS = "nasal_gain_db"
#: 昇格候補。**ゲートには使わない**（§11 CANDIDATE_SECONDARY_AXIS）
EXPLORATORY_AXIS = "nasal_gain_shape_db"

FAILURE_PRESENCE_DB = 1.0
MIN_ABSOLUTE_IMPROVEMENT_DB = 1.0
RELATIVE_IMPROVEMENT = 0.25
REGRESSION_TOL_DB = 1.0

#: 介入 tripwire の「動いた」判定しきい値
AXIS_MOVE_TOL = {
    "f0_dev_rmse_cents": 2.0,
    "note_split_mae_ms": 5.0,
    "energy_corr": 0.02,
    "taper_rmse_db": 0.5,
}
RUNG_EXPECTED_AXES = {
    "R1": {"f0_dev_rmse_cents"},
    "R2": {"note_split_mae_ms"},
    "R3": {"f0_dev_rmse_cents", "note_split_mae_ms"},
    "R4": {"f0_dev_rmse_cents", "note_split_mae_ms", "energy_corr", "taper_rmse_db"},
}


def _trf_of(measurement, axis: str) -> float:
    (only,) = list(measurement.trf.values())
    return float(only[axis])


# ---------------------------------------------------------------------------
def gate_determinism(pairs: Sequence[Any], recompute) -> GateResult:
    """G1（§10）。同一 source SHA / config / control で feature・WAV hash が再現する。"""
    ev: Dict[str, Any] = {}
    bad: List[str] = []
    for pair in pairs:
        recorded = [pair.rungs[n].samples_sha256 for n, _ in _LADDER_NAMES(pair)]
        again = recompute(pair)
        ok = recorded == again
        ev[pair.pair_key] = {"identical": ok,
                             "recorded": [s[:16] for s in recorded],
                             "recomputed": [s[:16] for s in again]}
        if not ok:
            bad.append(pair.pair_key)
    if bad:
        return GateResult("G1-determinism", FAIL,
                          f"再計算で sha256 が一致しない probe ペア: {bad}",
                          "乱数・時刻・並列順のいずれかが混入していないか compose 経路を洗うこと",
                          ev)
    return GateResult("G1-determinism", PASS,
                      f"{len(pairs)} probe ペアで R0–R4 のサンプル列 sha256 が再現", "", ev)


def _LADDER_NAMES(pair):
    return [(k, None) for k in pair.rungs]


def gate_structural_separation(perfs: Sequence[RealPerformanceTrack],
                               bank, track) -> GateResult:
    """G2（§10）。Performance 経路に PJS spectral identity が乗っていない。"""
    for p in perfs:
        try:
            assert_no_spectral_payload(p)
        except ValueError as exc:
            return GateResult("G2-structural", FAIL, str(exc),
                              "Performance dataclass から 2 次元配列を除くこと", {})
    tw = pbg.gate_tripwire(pc.compose, bank, track,
                           PerformanceToggles(True, True, True, True))
    if tw.status != "pass":
        return GateResult("G2-structural", FAIL, tw.detail,
                          "合成器が Performance の宣言外フィールドへ触れている", tw.evidence)
    return GateResult("G2-structural", PASS,
                      "Performance は 1 次元制御のみ、合成器のアクセスも宣言内",
                      "", {"accessed": tw.evidence.get("accessed", [])})


def gate_intervention_tripwire(pairs: Sequence[Any]) -> GateResult:
    """G3（§10）。各段で**意図した軸だけ**が動いたかを実測する。"""
    ev: Dict[str, Any] = {}
    problems: List[str] = []
    for pair in pairs:
        base = pair.rungs.get("R0")
        if base is None:
            continue
        per_pair: Dict[str, Any] = {}
        for rung, expected in RUNG_EXPECTED_AXES.items():
            cur = pair.rungs.get(rung)
            if cur is None:
                continue
            moved = set()
            deltas = {}
            for axis, tol in AXIS_MOVE_TOL.items():
                b, c = getattr(base, axis), getattr(cur, axis)
                if not (np.isfinite(b) and np.isfinite(c)):
                    deltas[axis] = None
                    continue
                d = float(c - b)
                deltas[axis] = round(d, 4)
                if abs(d) > tol:
                    moved.add(axis)
            per_pair[rung] = {"deltas": deltas, "moved": sorted(moved),
                              "expected": sorted(expected)}
            unexpected = moved - expected
            missing = expected - moved
            if unexpected:
                problems.append(f"{pair.pair_key} {rung}: 想定外の軸が動いた {sorted(unexpected)}")
            if missing:
                problems.append(f"{pair.pair_key} {rung}: 動くべき軸が動かない {sorted(missing)}")
        ev[pair.pair_key] = per_pair
    if problems:
        return GateResult("G3-intervention", FAIL, "; ".join(problems[:6]),
                          "成分トグルの実装か、軸の定義（交絡）を見直すこと", ev)
    return GateResult("G3-intervention", PASS, "R1/R2/R3/R4 で意図した軸のみが動いた", "", ev)


def gate_donor_texture_isolation(pairs: Sequence[Any], *,
                                 tol_db: float = 0.5) -> GateResult:
    """G4（§10）。R0→R4 が PJS テクスチャへ単調接近するだけなら FAIL。"""
    ev: Dict[str, Any] = {}
    problems: List[str] = []
    for pair in pairs:
        seq = [pair.rungs[n].donor_lsd_db for n in ("R0", "R1", "R2", "R3", "R4")
               if n in pair.rungs]
        margins = [pair.rungs[n].identity_margin_db for n in pair.rungs]
        if not seq or not all(np.isfinite(seq)):
            ev[pair.pair_key] = "donor 距離を測れない（PJS 側コア包絡が無い）"
            problems.append(f"{pair.pair_key}: donor 距離が未計測")
            continue
        diffs = np.diff(seq)
        monotone_approach = bool(np.all(diffs <= 1e-9)) and (seq[0] - seq[-1]) > tol_db
        ev[pair.pair_key] = {"donor_lsd_db": [round(float(x), 4) for x in seq],
                             "total_delta_db": round(float(seq[-1] - seq[0]), 4),
                             "monotone_approach": monotone_approach,
                             "min_identity_margin_db": round(float(min(margins)), 4)}
        if monotone_approach:
            problems.append(f"{pair.pair_key}: PJS テクスチャへ単調接近している（leakage 疑い）")
        if min(margins) <= 0.0:
            problems.append(f"{pair.pair_key}: 出力が identity より donor に近い段がある")
    if problems:
        return GateResult("G4-donor-isolation", FAIL, "; ".join(problems[:6]),
                          "Performance 経路のどこでテクスチャが漏れているかを特定すること", ev)
    return GateResult("G4-donor-isolation", PASS,
                      "全 probe ペアで PJS テクスチャへの単調接近なし・identity 寄りを維持",
                      "", ev)


def freeze_trf_gate(pair, axis: str = REGISTERED_PRIMARY_AXIS) -> Dict[str, Any]:
    """R0 **のみ**から閾値を確定する（R1–R4 を見る前に呼ぶ）。"""
    base = _trf_of(pair.rungs["R0"], axis)
    required = max(MIN_ABSOLUTE_IMPROVEMENT_DB, RELATIVE_IMPROVEMENT * abs(base))
    return {"pair_key": pair.pair_key, "primary_axis": axis,
            "r0_value_db": round(base, 4),
            "failure_present": bool(base >= FAILURE_PRESENCE_DB),
            "required_improvement_db": round(required, 4),
            "target_max_db": round(base - required, 4),
            "regression_tol_db": REGRESSION_TOL_DB,
            "rule": ("R4 の primary axis <= target_max_db。failure_present=False なら "
                     "NOT_EVALUABLE（改善を主張しない）。drift_db / energy_tail_db が "
                     "R0 比 +regression_tol_db を超えないこと")}


def gate_trf(pairs: Sequence[Any], frozen: Dict[str, Dict[str, Any]]) -> GateResult:
    """G7（§10・§11）。primary は登録軸のみ。exploratory は併記して判定に使わない。"""
    ev: Dict[str, Any] = {}
    verdicts: List[str] = []
    for pair in pairs:
        fz = frozen.get(pair.pair_key)
        if fz is None:
            continue
        r0, r4 = pair.rungs["R0"], pair.rungs["R4"]
        v0 = _trf_of(r0, REGISTERED_PRIMARY_AXIS)
        v4 = _trf_of(r4, REGISTERED_PRIMARY_AXIS)
        item: Dict[str, Any] = {
            "primary_axis": REGISTERED_PRIMARY_AXIS,
            "r0_db": round(v0, 4), "r4_db": round(v4, 4),
            "target_max_db": fz["target_max_db"],
            "exploratory": {EXPLORATORY_AXIS: {
                "r0_db": round(_trf_of(r0, EXPLORATORY_AXIS), 4),
                "r4_db": round(_trf_of(r4, EXPLORATORY_AXIS), 4),
                "status": "CANDIDATE_SECONDARY_AXIS (判定に使わない)"}},
        }
        if not fz["failure_present"]:
            item["verdict"] = NOT_EVALUABLE
            verdicts.append(NOT_EVALUABLE)
        else:
            reg_ok = ((_trf_of(r4, "drift_db") - _trf_of(r0, "drift_db")) <= REGRESSION_TOL_DB
                      and (_trf_of(r4, "energy_tail_db")
                           - _trf_of(r0, "energy_tail_db")) <= REGRESSION_TOL_DB)
            ok = v4 <= fz["target_max_db"] and reg_ok
            item["secondary_regression_ok"] = reg_ok
            item["verdict"] = PASS if ok else FAIL
            verdicts.append(item["verdict"])
        ev[pair.pair_key] = item
    if not verdicts:
        return GateResult("G7-TRF", BLOCKED, "判定できる probe ペアが無い",
                          "census で primary probe を確保すること", ev)
    if FAIL in verdicts:
        return GateResult("G7-TRF", FAIL,
                          f"{verdicts.count(FAIL)}/{len(verdicts)} ペアが事前登録ゲート不通過",
                          "§14 の失敗解釈表に従って原因を分類すること", ev)
    if PASS not in verdicts:
        return GateResult("G7-TRF", NOT_EVALUABLE,
                          "全ペアで baseline に破綻が無く、改善を主張できない", "", ev)
    return GateResult("G7-TRF", PASS,
                      f"{verdicts.count(PASS)}/{len(verdicts)} ペアが事前登録ゲート通過", "", ev)


def gate_ladder_attribution(pairs: Sequence[Any]) -> GateResult:
    """G5（§10）。どの段で TRF が動いたかを追跡可能な形で残す。"""
    ev: Dict[str, Any] = {}
    for pair in pairs:
        base = _trf_of(pair.rungs["R0"], REGISTERED_PRIMARY_AXIS)
        ev[pair.pair_key] = {
            n: {"primary_delta_db": round(_trf_of(pair.rungs[n], REGISTERED_PRIMARY_AXIS)
                                          - base, 4),
                "exploratory_delta_db": round(
                    _trf_of(pair.rungs[n], EXPLORATORY_AXIS)
                    - _trf_of(pair.rungs["R0"], EXPLORATORY_AXIS), 4)}
            for n in pair.rungs if n != "R0"}
    return GateResult("G5-attribution", PASS, "各段の TRF 変化量を probe ペア別に記録", "", ev)


def gate_identity(pairs: Sequence[Any], *, min_margin_db: float = 0.0) -> GateResult:
    """G6（§10）。最初の Real PoC では重くしない = 「identity 寄り」を要求する。"""
    ev: Dict[str, Any] = {}
    problems: List[str] = []
    for pair in pairs:
        margins = {n: round(m.identity_margin_db, 4) for n, m in pair.rungs.items()}
        ev[pair.pair_key] = margins
        worst = min(margins.values())
        if worst <= min_margin_db:
            problems.append(f"{pair.pair_key}: identity 寄り margin が {worst:.3f}dB")
    if problems:
        return GateResult("G6-identity", FAIL, "; ".join(problems[:6]),
                          "§14「Identity 崩壊」= identity representation を再設計する", ev)
    return GateResult("G6-identity", PASS,
                      "全 probe ペア・全段で出力は PJS より Ritsu に近い", "", ev)


EAR_QUESTIONS = (
    ("Q1", "R0–R4 は同一 Identity 系統（Ritsu 系）に聞こえるか？"),
    ("Q2", "Performance の違いは聞き分けられるか？"),
    ("Q3", "terminal /ri/ failure は改善したか？"),
    ("Q4", "PJS 本人の声へ近づいただけではないか？"),
)


def gate_ear(answers: Optional[Dict[str, Any]]) -> GateResult:
    """G-ear（§10）。人の耳の答えが無い限り PASS にしない（機械で上書きしない = §0-10）。"""
    if not answers:
        return GateResult("G-ear", BLOCKED,
                          "聴感判定が未実施（機械評価では代替できない）",
                          "results/<run>/ の R0–R4 wav を試聴し、ear_answers.json へ "
                          + " / ".join(f"{k}: {q}" for k, q in EAR_QUESTIONS),
                          {"questions": {k: q for k, q in EAR_QUESTIONS}})
    missing = [k for k, _q in EAR_QUESTIONS if k not in answers]
    if missing:
        return GateResult("G-ear", BLOCKED, f"未回答の設問 {missing}",
                          "ear_answers.json の残り設問へ回答すること", {"answers": answers})
    return GateResult("G-ear", PASS, "聴感 4 設問に回答あり（内容の解釈は record に記載）",
                      "", {"answers": answers})


def success_level(ledger_gates: Sequence[GateResult], pairs: Sequence[Any]) -> str:
    """instruction §12 の到達レベルを機械的に判定する。"""
    st = {g.gate: g.status for g in ledger_gates}
    if st.get("G-MATERIAL") != PASS or st.get("G-LICENSE") != PASS:
        return "S0_NOT_REACHED"
    if not pairs:
        return "S0_ACQUISITION"
    if st.get("G3-intervention") != PASS or st.get("G4-donor-isolation") != PASS:
        return "S1_REAL_RENDER"
    moved_axes = set()
    for pair in pairs:
        base = pair.rungs.get("R0")
        for rung in ("R1", "R2", "R3", "R4"):
            cur = pair.rungs.get(rung)
            if cur is None or base is None:
                continue
            for axis, tol in AXIS_MOVE_TOL.items():
                b, c = getattr(base, axis), getattr(cur, axis)
                if np.isfinite(b) and np.isfinite(c) and abs(c - b) > tol:
                    moved_axes.add(axis)
    if st.get("G6-identity") != PASS or not moved_axes:
        return "S1_REAL_RENDER"
    if len(moved_axes) >= 2:
        return "S3_SKILL_ATTRIBUTION"
    return "S2_PARTIAL_SEPARATION"
