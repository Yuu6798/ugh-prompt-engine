"""planb/pb_gates.py — プラン §8 受け入れ条件の機械判定。

プランの 6 条件のうち、**耳と実資産を要しないもの**を機械ゲート化する。
耳律速（条件 1 の聴感・条件 2 の実 TRF 改善）は本モジュールでは
`blocked` を返し、`pb_ladder` が record にそのまま載せる（成功と数えない）。

| 条件 | 内容 | ゲート |
|---|---|---|
| 1 | R0/R4 で identity が保たれる | G6（機械代理: コア包絡 LSD）+ 耳は blocked |
| 2 | R4 が R0 より TRF 改善 | G7（事前登録ルール。実資産では実 TRF、代替では合成故障） |
| 3 | held-out で重大回帰なし | G7-holdout |
| 4 | どこで改善が生じたか再現可能 | G5（ラダー帰属の記録） |
| 5 | 同一入力 -> 再現性 | G1（同一プロセス + 別プロセスの sha256 一致） |
| 6 | PJS テクスチャ非使用 | G2（構造）+ G3（tripwire）+ G4（実測 LSD 非接近） |
"""
from __future__ import annotations

import json
from dataclasses import dataclass, fields
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from pb_tracks import PerformanceTrack, ReleaseSpec, assert_no_spectral_payload


PRIMARY_AXIS = "nasal_gain_db"
FAILURE_PRESENCE_DB = 1.0
MIN_ABSOLUTE_IMPROVEMENT_DB = 1.0
RELATIVE_IMPROVEMENT = 0.25
REGRESSION_TOL_DB = 1.0


@dataclass(frozen=True)
class GateResult:
    gate: str
    criterion: str
    status: str          # "pass" / "fail" / "blocked" / "not_evaluable"
    detail: str
    evidence: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {"gate": self.gate, "criterion": self.criterion, "status": self.status,
                "detail": self.detail, "evidence": self.evidence}


# ---------------------------------------------------------------------------
# G2: 構造ゲート（Performance に 2 次元配列が無い）
# ---------------------------------------------------------------------------
def gate_structural(track: PerformanceTrack) -> GateResult:
    try:
        assert_no_spectral_payload(track)
    except ValueError as exc:
        return GateResult("G2", "criterion-6", "fail", str(exc), {})
    dims = {}
    for f in fields(track):
        v = getattr(track, f.name)
        if isinstance(v, np.ndarray):
            dims[f.name] = int(v.ndim)
        elif isinstance(v, ReleaseSpec):
            dims[f"{f.name}.taper_db"] = int(np.asarray(v.taper_db).ndim)
    return GateResult(
        "G2", "criterion-6", "pass",
        "PerformanceTrack の全 ndarray フィールドが 1 次元（spectral payload 不在）",
        {"ndim_by_field": dims},
    )


# ---------------------------------------------------------------------------
# G3: tripwire（合成器が Performance の宣言フィールド以外へ触れない）
# ---------------------------------------------------------------------------
class _AccessRecorder:
    """属性アクセスを記録し、2 次元配列の返却を例外にするプロキシ。"""

    def __init__(self, target: Any, log: List[str], prefix: str = "") -> None:
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_log", log)
        object.__setattr__(self, "_prefix", prefix)

    def __getattr__(self, name: str) -> Any:
        target = object.__getattribute__(self, "_target")
        log = object.__getattribute__(self, "_log")
        prefix = object.__getattribute__(self, "_prefix")
        value = getattr(target, name)
        log.append(prefix + name)
        if isinstance(value, np.ndarray) and value.ndim > 1:
            raise AssertionError(
                f"tripwire: Performance 経由で {prefix + name} が {value.ndim} 次元を返した"
            )
        if isinstance(value, ReleaseSpec):
            return _AccessRecorder(value, log, prefix=prefix + name + ".")
        return value

    def __setattr__(self, name: str, value: Any) -> None:  # pragma: no cover - 不変契約
        raise AttributeError("PerformanceTrack proxy は不変")


def gate_tripwire(compose_fn, identity, track: PerformanceTrack, toggles) -> GateResult:
    """全トグル ON で合成し、Performance 側へのアクセス集合を検査する。"""
    log: List[str] = []
    proxy = _AccessRecorder(track, log)
    try:
        compose_fn(identity, proxy, toggles)
    except AssertionError as exc:
        return GateResult("G3", "criterion-6", "fail", str(exc), {"accessed": sorted(set(log))})
    declared = {f.name for f in fields(PerformanceTrack)} | {"n_units"}
    declared |= {f"release.{n}" for n in ("window_frac", "taper_db", "hold_core")}
    accessed = sorted(set(log))
    unknown = [a for a in accessed if a not in declared]
    if unknown:
        return GateResult("G3", "criterion-6", "fail",
                          f"宣言外フィールドへのアクセス: {unknown}", {"accessed": accessed})
    return GateResult(
        "G3", "criterion-6", "pass",
        "合成器は Performance の宣言済み 1 次元フィールドのみを読んだ",
        {"accessed": accessed},
    )


# ---------------------------------------------------------------------------
# G1: 決定論
# ---------------------------------------------------------------------------
def gate_determinism(sha_a: Sequence[str], sha_b: Sequence[str],
                     subprocess_sha: Optional[Sequence[str]] = None) -> GateResult:
    same = list(sha_a) == list(sha_b)
    ev: Dict[str, Any] = {"in_process": {"first": list(sha_a), "second": list(sha_b),
                                         "identical": same}}
    status = "pass" if same else "fail"
    if subprocess_sha is not None:
        sub_same = list(sha_a) == list(subprocess_sha)
        ev["cross_process"] = {"sha": list(subprocess_sha), "identical": sub_same}
        if not sub_same:
            status = "fail"
    detail = "同一入力 + 同一トグルで wav の sha256 が一致" if status == "pass" \
        else "sha256 が一致しない（決定論違反）"
    return GateResult("G1", "criterion-5", status, detail, ev)


# ---------------------------------------------------------------------------
# G4: 実測テクスチャ非接近（criterion 6 の測定側）
# ---------------------------------------------------------------------------
def gate_donor_texture(metrics_by_rung: Dict[str, Any], *, tol_db: float = 0.5) -> GateResult:
    r0 = metrics_by_rung["R0"].donor_texture_lsd_db
    r4 = metrics_by_rung["R4"].donor_texture_lsd_db
    delta = r4 - r0
    status = "pass" if delta >= -tol_db else "fail"
    return GateResult(
        "G4", "criterion-6", status,
        f"donor テクスチャ距離 R0={r0:.3f}dB -> R4={r4:.3f}dB (Δ={delta:+.3f}dB, tol={tol_db})",
        {"r0_db": round(r0, 4), "r4_db": round(r4, 4), "delta_db": round(delta, 4),
         "tol_db": tol_db},
    )


# ---------------------------------------------------------------------------
# G6: identity 保存（機械代理）
# ---------------------------------------------------------------------------
def gate_identity_preservation(metrics_by_rung: Dict[str, Any], *, min_margin_db: float = 1.0) -> GateResult:
    """出力が donor より identity に近いこと（margin > 0）を全段で要求する。

    絶対 LSD には合成 -> 再解析の往復床（本ハーネスで ~3dB）が乗るため、
    絶対値の閾値ではなく **identity 距離と donor 距離の差**で判定する。
    差が正 = 出力のテクスチャは identity 側に属する。
    """
    ev, worst = {}, None
    for k, m in metrics_by_rung.items():
        margin = m.donor_texture_lsd_db - m.identity_core_lsd_db
        ev[k] = {"identity_lsd_db": round(m.identity_core_lsd_db, 4),
                 "donor_lsd_db": round(m.donor_texture_lsd_db, 4),
                 "margin_db": round(margin, 4)}
        worst = margin if worst is None else min(worst, margin)
    status = "pass" if (worst is not None and worst >= min_margin_db) else "fail"
    return GateResult(
        "G6", "criterion-1(machine-proxy)", status,
        f"identity 寄り margin の最小値 {worst:.3f}dB (要求 >= {min_margin_db}dB)。"
        "聴感判定そのものは耳律速のため別建て",
        {"by_rung": ev, "min_margin_db": min_margin_db},
    )


def gate_medial_regression(metrics_by_rung: Dict[str, Any], medial_keys: Sequence[str],
                           *, tol_db: float = REGRESSION_TOL_DB) -> GateResult:
    """フレーズ中間 unit が release skill で荒らされていないこと（criterion-3 の一部）。"""
    if not medial_keys:
        return GateResult("G8", "criterion-3", "not_evaluable",
                          "フレーズ中間 probe が素材に無い", {})
    t0 = {t.label + f"@{t.unit_index}": t for t in metrics_by_rung["R0"].trf}
    t4 = {t.label + f"@{t.unit_index}": t for t in metrics_by_rung["R4"].trf}
    ev, ok = {}, True
    for k in medial_keys:
        if k not in t0 or k not in t4:
            ev[k] = "absent"
            continue
        d = t4[k].drift_db - t0[k].drift_db
        ev[k] = {"drift_r0_db": round(t0[k].drift_db, 4), "drift_r4_db": round(t4[k].drift_db, 4),
                 "delta_db": round(d, 4)}
        if d > tol_db:
            ok = False
    return GateResult("G8", "criterion-3", "pass" if ok else "fail",
                      f"フレーズ中間 probe の drift 増分が {tol_db}dB 以内", ev)


# ---------------------------------------------------------------------------
# G7: TRF 事前登録ゲート
# ---------------------------------------------------------------------------
def freeze_trf_gate(r0_metrics, primary_probe: str, holdout_probes: Sequence[str]) -> Dict[str, Any]:
    """**R0 のみ**から閾値を確定して凍結する（R1–R4 を見る前に呼ぶこと）。

    事前登録の実務: 「改善の向き」と「決定ルール」は設計書で先に固定し、
    数値だけを R0 baseline から導く。R1–R4 の実測は閾値決定に一切関与しない。
    """
    trf = {t.label + f"@{t.unit_index}": t for t in r0_metrics.trf}
    prim = trf.get(primary_probe)
    if prim is None:
        raise KeyError(f"primary probe {primary_probe} が R0 の計測に無い")
    base = float(getattr(prim, PRIMARY_AXIS))
    required = max(MIN_ABSOLUTE_IMPROVEMENT_DB, RELATIVE_IMPROVEMENT * abs(base))
    return {
        "primary_probe": primary_probe,
        "primary_axis": PRIMARY_AXIS,
        "r0_value_db": round(base, 4),
        "failure_present": bool(base >= FAILURE_PRESENCE_DB),
        "failure_presence_threshold_db": FAILURE_PRESENCE_DB,
        "required_improvement_db": round(required, 4),
        "target_max_db": round(base - required, 4),
        "regression_tol_db": REGRESSION_TOL_DB,
        "holdout_probes": list(holdout_probes),
        "holdout_r0_db": {p: round(float(getattr(trf[p], PRIMARY_AXIS)), 4)
                          for p in holdout_probes if p in trf},
        "rule": (
            "primary: R4 の nasal_gain_db <= target_max_db。"
            "ただし failure_present=False（R0 < 1.0dB）のときは not_evaluable とし成功に数えない。"
            "regression: drift_db / energy_tail_db が R0 比 +regression_tol_db を超えないこと。"
            "holdout: 各 holdout probe の nasal_gain_db が R0 比 +regression_tol_db を超えないこと。"
        ),
    }


def gate_trf(frozen: Dict[str, Any], r0_metrics, r4_metrics) -> Tuple[GateResult, GateResult]:
    """凍結ゲートに対する R4 の判定（primary, holdout）。"""
    t0 = {t.label + f"@{t.unit_index}": t for t in r0_metrics.trf}
    t4 = {t.label + f"@{t.unit_index}": t for t in r4_metrics.trf}
    p = frozen["primary_probe"]
    v0, v4 = float(getattr(t0[p], PRIMARY_AXIS)), float(getattr(t4[p], PRIMARY_AXIS))
    ev = {"probe": p, "axis": PRIMARY_AXIS, "r0_db": round(v0, 4), "r4_db": round(v4, 4),
          "delta_db": round(v4 - v0, 4), "target_max_db": frozen["target_max_db"],
          "drift_db": {"r0": round(t0[p].drift_db, 4), "r4": round(t4[p].drift_db, 4)},
          "energy_tail_db": {"r0": round(t0[p].energy_tail_db, 4),
                             "r4": round(t4[p].energy_tail_db, 4)}}
    if not frozen["failure_present"]:
        primary = GateResult(
            "G7", "criterion-2", "not_evaluable",
            f"R0 の {PRIMARY_AXIS}={v0:.3f}dB < {FAILURE_PRESENCE_DB}dB。"
            "改善すべき破綻が baseline に存在しないため、この軸は評価不能（成功に数えない）",
            ev)
    else:
        reg_ok = ((t4[p].drift_db - t0[p].drift_db) <= frozen["regression_tol_db"]
                  and (t4[p].energy_tail_db - t0[p].energy_tail_db) <= frozen["regression_tol_db"])
        ok = v4 <= frozen["target_max_db"] and reg_ok
        primary = GateResult(
            "G7", "criterion-2", "pass" if ok else "fail",
            f"{p} の {PRIMARY_AXIS}: R0={v0:.3f} -> R4={v4:.3f} dB "
            f"(必要 <= {frozen['target_max_db']:.3f}, 副軸回帰 {'ok' if reg_ok else 'ng'})",
            ev)

    hold_ev, hold_ok = {}, True
    for hp in frozen["holdout_probes"]:
        if hp not in t0 or hp not in t4:
            hold_ev[hp] = "absent"
            continue
        d = float(getattr(t4[hp], PRIMARY_AXIS)) - float(getattr(t0[hp], PRIMARY_AXIS))
        hold_ev[hp] = {"r0_db": round(float(getattr(t0[hp], PRIMARY_AXIS)), 4),
                       "r4_db": round(float(getattr(t4[hp], PRIMARY_AXIS)), 4),
                       "delta_db": round(d, 4)}
        if d > frozen["regression_tol_db"]:
            hold_ok = False
    if not frozen["holdout_probes"]:
        holdout = GateResult("G7-holdout", "criterion-3", "not_evaluable",
                             "held-out probe が代替素材に存在しない（被覆申告を参照）", hold_ev)
    else:
        holdout = GateResult("G7-holdout", "criterion-3", "pass" if hold_ok else "fail",
                             f"held-out probe の回帰許容 {frozen['regression_tol_db']}dB",
                             hold_ev)
    return primary, holdout


# ---------------------------------------------------------------------------
# G5: ラダー帰属（criterion 4）
# ---------------------------------------------------------------------------
def gate_attribution(metrics_by_rung: Dict[str, Any]) -> GateResult:
    """各段が「自分の軸」を動かしたかを記録し、帰属の再現可能性を示す。"""
    axes = {
        "R1": ("f0_dev_rmse_cents", "lower"),
        "R2": ("dur_mae_ms", "lower"),
        "R3": ("f0_dev_rmse_cents", "lower"),
        "R4": ("energy_corr", "higher"),
    }
    ev, ok = {}, True
    for rung, (axis, direction) in axes.items():
        base = getattr(metrics_by_rung["R0"], axis)
        cur = getattr(metrics_by_rung[rung], axis)
        moved = (cur < base) if direction == "lower" else (cur > base)
        ev[rung] = {"axis": axis, "r0": round(float(base), 4), "value": round(float(cur), 4),
                    "expected": direction, "moved": bool(moved)}
        if not moved:
            ok = False
    return GateResult("G5", "criterion-4", "pass" if ok else "fail",
                      "各段が対応する performance 軸を期待方向へ動かしたか", ev)


def gate_ear_blocked(what: str) -> GateResult:
    return GateResult("G-ear", "criterion-1/2(perceptual)", "blocked", what, {})


def dumps(results: Sequence[GateResult]) -> str:
    return json.dumps([r.as_dict() for r in results], ensure_ascii=False, indent=2)
