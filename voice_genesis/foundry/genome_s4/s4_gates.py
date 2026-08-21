"""genome_s4/s4_gates.py — 設計書 §7〜§16 をそのまま実装する。

**判定を増やさない。** pair verdict は §10 の 4 状態以外を作らない。
retention（§9.3）は診断値であり、閾値には使わない。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_HERE = Path(__file__).resolve().parent
_FOUNDRY = _HERE.parent
for _p in (_HERE, _FOUNDRY / "planb"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import s4_spec as sp  # noqa: E402
from s4_spec import Gene, PairVerdict  # noqa: E402


# ---------------------------------------------------------------------------
# §7 構造 Gate
# ---------------------------------------------------------------------------
def structure_gate(run) -> Dict[str, Any]:
    """条件トグルの厳密一致 / 1-D payload / tripwire / Energy・Release 非参照。

    構造違反は **pair 単位ではなく S4 全体を FAILED** にする（§7）。ここでは
    pair ごとの証拠を返し、集計側（`overall_gate`）が全体判定へ持ち上げる。
    """
    detail: Dict[str, Any] = {}
    toggles_ok = True
    for cond in sp.CONDITIONS:
        co = run.conditions.get(cond)
        if co is None:
            toggles_ok = False
            detail[f"{cond}_missing"] = True
            continue
        want = sp.CONDITIONS[cond].label
        ok = co.toggles == want
        toggles_ok = toggles_ok and ok
        detail[f"{cond}_toggles"] = co.toggles
    fd = run.conditions.get("FD")
    detail["fd_is_exact_f0_duration"] = bool(
        fd is not None and fd.toggles == sp.CONDITIONS["FD"].label)

    accessed: Dict[str, List[str]] = {
        c: sorted(getattr(co, "tripwire_accessed", []) or [])
        for c, co in run.conditions.items()}
    detail["tripwire_accessed"] = accessed
    detail["tripwire_status"] = {c: co.tripwire_status for c, co in run.conditions.items()}
    forbidden = {c: [a for a in acc if a in sp.ENERGY_RELEASE_FIELDS]
                 for c, acc in accessed.items()}
    detail["energy_release_accessed"] = {c: v for c, v in forbidden.items() if v}
    no_er = not any(forbidden.values())
    tw_ok = all(co.tripwire_status == "pass" for co in run.conditions.values()) \
        if run.conditions else False

    # Identity / Performance source は 1 pair につき 1 つで、4 条件が同じ bank と
    # track を共有する。記録に載せて「同一である」ことを読み手が確認できるようにする。
    detail["identity_sha256"] = run.identity_sha256[:16]
    detail["performance_sha256"] = run.performance_sha256[:16]
    detail["identity_shared_across_conditions"] = True
    detail["performance_shared_across_conditions"] = True
    detail["performance_payload_1d"] = bool(getattr(run, "performance_payload_1d", False))

    ok = bool(toggles_ok and detail["fd_is_exact_f0_duration"] and no_er and tw_ok
              and detail["performance_payload_1d"])
    return {"pass": ok, "no_energy_release_access": no_er,
            "all_tripwires_pass": tw_ok, "toggles_exact": toggles_ok, **detail}


# ---------------------------------------------------------------------------
# §12 決定論 Gate
# ---------------------------------------------------------------------------
def determinism_gate(run, cross_process: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """4 条件すべてで same-process / cross-process の sample_sha256 が一致すること。"""
    per: Dict[str, Dict[str, Any]] = {}
    ok = True
    cross = cross_process.get(run.pair_key, {})
    for cond in sp.CONDITIONS:
        co = run.conditions.get(cond)
        if co is None:
            per[cond] = {"pass": False, "reason": "condition missing"}
            ok = False
            continue
        recorded = co.sample_sha256
        same = run.repeat_sample_sha256.get(cond)
        xp = cross.get(cond)
        same_ok = recorded == same
        cross_ok = xp is not None and recorded == xp
        per[cond] = {"pass": bool(same_ok and cross_ok),
                     "same_process": bool(same_ok), "cross_process": bool(cross_ok),
                     "recorded": recorded[:16], "same_process_sha": (same or "")[:16],
                     "cross_process_sha": (xp or "")[:16]}
        ok = ok and per[cond]["pass"]
    return {"pass": bool(ok), "conditions": per}


# ---------------------------------------------------------------------------
# §6 S3 replay Gate
# ---------------------------------------------------------------------------
def replay_gate(run) -> Dict[str, Any]:
    """B0 / F / D が S3 canonical と sample_sha256 で一致すること。"""
    rep = run.s3_replay or {}
    mismatches = [c for c in sp.REPLAY_CONDITIONS
                  if not (rep.get(c) or {}).get("match", False)]
    return {"pass": not mismatches, "mismatched_conditions": mismatches,
            "detail": rep}


# ---------------------------------------------------------------------------
# §9 Pair-Level Combination Gate
# ---------------------------------------------------------------------------
def _metric(run, cond: str, name: str) -> Optional[float]:
    co = run.conditions.get(cond)
    if co is None:
        return None
    v = co.metrics.get(name)
    return None if v is None else float(v)


def _effect(run, from_cond: str, to_cond: str, metric: str) -> Optional[float]:
    """`metric` は全て lower-is-better。効果量 = from - to（正 = 改善）。"""
    a, b = _metric(run, from_cond, metric), _metric(run, to_cond, metric)
    if a is None or b is None:
        return None
    return round(a - b, 6)


def _ratio(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or den is None or den == 0.0:
        return None
    return round(num / den, 6)


def combination_gate(run) -> Dict[str, Any]:
    """§9.1〜§9.5 を計算する。**閾値の追加はしない。**"""
    out: Dict[str, Any] = {"genes": {}, "distinctness": {}, "identity": {}}
    for gene, (alone_cond, bg_cond, metric, direction) in sp.GENE_PLAN.items():
        assert direction == "lower"      # §8 の metric は全て lower is better
        alone = _effect(run, "B0", alone_cond, metric)          # §9.1
        with_bg = _effect(run, bg_cond, "FD", metric)           # §9.2
        out["genes"][gene.value] = {
            "metric": metric,
            "alone": {"from": "B0", "to": alone_cond, "effect": alone,
                      "positive": bool(alone is not None and alone > 0.0)},
            "with_background": {"from": bg_cond, "to": "FD", "effect": with_bg,
                                "positive": bool(with_bg is not None and with_bg > 0.0)},
            # §9.3 retention は **診断値**。Gate には使わない。
            "retention": _ratio(with_bg, alone),
            "values": {c: _metric(run, c, metric) for c in sp.CONDITIONS},
        }
    fd = run.conditions.get("FD")
    fd_sha = fd.sample_sha256 if fd is not None else None
    for cond in ("B0", "F", "D"):
        co = run.conditions.get(cond)
        out["distinctness"][cond] = bool(
            fd_sha is not None and co is not None and fd_sha != co.sample_sha256)
    out["distinctness"]["pass"] = all(out["distinctness"][c] for c in ("B0", "F", "D"))
    margin = _metric(run, "FD", sp.IDENTITY_METRIC)
    out["identity"] = {"metric": sp.IDENTITY_METRIC, "fd_value": margin,
                       "positive": bool(margin is not None and margin > 0.0)}
    return out


def intervention_nonzero(run) -> Dict[str, Any]:
    """§10: F0 または Duration の**介入量**が 0 なら NOT_EVALUABLE。"""
    per: Dict[str, Any] = {}
    ok = True
    for gene in sp.Gene:
        info = (run.intervention or {}).get(gene.value, {})
        nonzero = bool(info.get("nonzero", False))
        per[gene.value] = {"nonzero": nonzero, "amount": info.get("amount"),
                           "unit": info.get("unit"),
                           "structural_noop": bool(info.get("structural_noop", False))}
        ok = ok and nonzero
    return {"pass": bool(ok), "genes": per}


# ---------------------------------------------------------------------------
# §10 Pair verdict
# ---------------------------------------------------------------------------
@dataclass
class PairResult:
    pair_key: str
    context_id: str
    verdict: str
    structure: Dict[str, Any] = field(default_factory=dict)
    determinism: Dict[str, Any] = field(default_factory=dict)
    replay: Dict[str, Any] = field(default_factory=dict)
    intervention: Dict[str, Any] = field(default_factory=dict)
    combination: Dict[str, Any] = field(default_factory=dict)
    s3_consistency: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"pair_key": self.pair_key, "context_id": self.context_id,
                "verdict": self.verdict,
                "structure": self.structure, "determinism": self.determinism,
                "s3_replay": self.replay, "intervention": self.intervention,
                "combination": self.combination,
                "s3_consistency": self.s3_consistency}


def pair_verdict(run, cross_process: Dict[str, Dict[str, str]]) -> PairResult:
    """§10 の判定木をそのまま実装する。**4 状態以外を作らない。**"""
    st = structure_gate(run)
    det = determinism_gate(run, cross_process)
    rep = replay_gate(run)
    iv = intervention_nonzero(run)
    comb = combination_gate(run)

    # §9.1: S3 正本が SUPPORT していた単独効果と**逆**なら FAILED。
    alone_ok = all(comb["genes"][g.value]["alone"]["positive"] for g in sp.Gene)
    s3_consistency = {
        "pass": bool(alone_ok),
        "genes": {g.value: comb["genes"][g.value]["alone"] for g in sp.Gene}}

    if not st["pass"] or not det["pass"] or not rep["pass"] or not alone_ok:
        v = PairVerdict.FAILED
    elif not iv["pass"]:
        v = PairVerdict.NOT_EVALUABLE
    elif (comb["genes"][Gene.F0.value]["with_background"]["positive"]
          and comb["genes"][Gene.DURATION.value]["with_background"]["positive"]
          and comb["distinctness"]["pass"]
          and comb["identity"]["positive"]):
        v = PairVerdict.COMBINABLE
    else:
        v = PairVerdict.UNSUPPORTED
    return PairResult(pair_key=run.pair_key, context_id=run.context_id,
                      verdict=v.value, structure=st, determinism=det, replay=rep,
                      intervention=iv, combination=comb, s3_consistency=s3_consistency)


# ---------------------------------------------------------------------------
# §11 機械 Overall Gate
# ---------------------------------------------------------------------------
def overall_gate(results: Sequence[PairResult],
                 criteria: sp.Criteria = sp.Criteria()) -> Dict[str, Any]:
    """事前登録値だけで判定する。走行中に閾値を足さない。"""
    evaluable = [r for r in results
                 if r.verdict in (PairVerdict.COMBINABLE.value,
                                  PairVerdict.UNSUPPORTED.value)]
    combinable = [r for r in evaluable if r.verdict == PairVerdict.COMBINABLE.value]
    # FAILED は evaluable の分母から外れるので、これを数えないと
    # 「5 COMBINABLE + 1 FAILED」で ratio 5/5 = 1.0 が立ち、S3 と矛盾する pair を
    # 抱えたまま PASS が出る。pair verdict そのものを hard failure として数える。
    failed_pairs = [r for r in results if r.verdict == PairVerdict.FAILED.value]
    structural_failures = sum(1 for r in results if not r.structure.get("pass"))
    determinism_failures = sum(1 for r in results if not r.determinism.get("pass"))
    replay_mismatches = sum(1 for r in results if not r.replay.get("pass"))
    s3_contradictions = sum(1 for r in results if not r.s3_consistency.get("pass", True))
    contexts = sorted({r.context_id for r in results})
    sup_ctx = sorted({r.context_id for r in combinable})
    ratio = (len(combinable) / len(evaluable)) if evaluable else 0.0
    checks = {
        "candidate_pairs": len(results) >= criteria.min_candidate_pairs,
        "distinct_contexts": len(contexts) >= criteria.min_contexts,
        "support_ratio": ratio >= criteria.min_support_ratio,
        "supported_contexts": len(sup_ctx) >= criteria.min_supported_contexts,
        "no_structural_failures": structural_failures == 0,
        "no_determinism_failures": determinism_failures == 0,
        "no_s3_replay_mismatches": replay_mismatches == 0,
        "no_failed_pairs": not failed_pairs,
    }
    # 構造 / 決定論 / replay の違反は「不足」ではなく **違反** なので FAIL と
    # 区別できるように理由を残す（§7 は S4 全体を FAILED にする）。
    hard_fail = not (checks["no_structural_failures"]
                     and checks["no_determinism_failures"]
                     and checks["no_s3_replay_mismatches"]
                     and checks["no_failed_pairs"])
    verdict = "PASS" if all(checks.values()) else "FAIL"
    return {
        "candidate_pairs": len(results),
        "evaluable_pairs": len(evaluable),
        "combinable_pairs": len(combinable),
        "support_ratio": round(ratio, 6),
        "contexts": contexts,
        "supported_contexts": sup_ctx,
        "distinct_context_count": len(contexts),
        "supported_context_count": len(sup_ctx),
        "failed_pairs": len(failed_pairs),
        "failed_pair_keys": [r.pair_key for r in failed_pairs],
        "structural_failures": structural_failures,
        "determinism_failures": determinism_failures,
        "s3_replay_mismatches": replay_mismatches,
        "s3_contradictions": s3_contradictions,
        "checks": checks,
        "hard_failure": hard_fail,
        "criteria": criteria.as_dict(),
        "verdict": verdict,
        "pairs": {r.pair_key: r.as_dict() for r in results},
    }


# ---------------------------------------------------------------------------
# §15 人間 Gate 判定
# ---------------------------------------------------------------------------
def abx_verdict(correct: int, total: int = sp.ABX_TOTAL) -> str:
    """4 問すべて正解のときだけ PASS（§15.1）。UNSURE は正答に数えない。"""
    return ("PERCEPTUAL_COEXPRESSION_PASS" if total == sp.ABX_TOTAL and correct == total
            else "PERCEPTUAL_COEXPRESSION_NOT_ESTABLISHED")


def identity_verdict(yes: int, total: int = sp.IDENTITY_TOTAL) -> str:
    """2 問とも YES のときだけ PRESERVED（§15.2）。"""
    return ("IDENTITY_PRESERVED" if total == sp.IDENTITY_TOTAL and yes == total
            else "IDENTITY_NOT_ESTABLISHED")


def perceptual_verdict(abx: str, identity: str) -> str:
    return ("PASS" if abx == "PERCEPTUAL_COEXPRESSION_PASS"
            and identity == "IDENTITY_PRESERVED" else "NOT_ESTABLISHED")


# ---------------------------------------------------------------------------
# §16 S4 Overall
# ---------------------------------------------------------------------------
def s4_overall(mechanistic_verdict: str, abx: Optional[str], identity: Optional[str],
               *, hard_failure: bool = False) -> str:
    """PASS / NOT_ESTABLISHED / FAILED / BLOCKED の 4 状態だけを返す。"""
    if hard_failure:
        return "FAILED"
    if mechanistic_verdict == "BLOCKED":
        return "BLOCKED"
    if mechanistic_verdict != "PASS":
        # 違反ではない機械 FAIL（support_ratio 不足など）は「複合発現だけが
        # 未成立」= NOT_ESTABLISHED。S2 / S3 / S3.5 の判定は変えない（§16）。
        return "NOT_ESTABLISHED"
    if abx is None or identity is None:
        return "BLOCKED"                 # 機械 PASS 済みだが耳判定が未了
    return perceptual_verdict(abx, identity)
