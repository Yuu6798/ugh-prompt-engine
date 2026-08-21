"""genome_s3/s3_gates.py — 設計書 §8–§11 をそのまま実装する。

**判定ロジックを増やさない。** 4 状態以外を作らない。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

_HERE = Path(__file__).resolve().parent
_FOUNDRY = _HERE.parent
for _p in (_HERE, _FOUNDRY / "planb", _FOUNDRY / "planb_real"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pb_compose as pc  # noqa: E402
import pb_gates as pbg  # noqa: E402

import pr_performance as prp  # noqa: E402
import s3_spec as sp  # noqa: E402
from s3_spec import Gene, GeneVerdict, PairVerdict  # noqa: E402


@dataclass
class PairGeneResult:
    pair_key: str
    context_id: str
    gene: str
    verdict: str
    p1_structural_isolation: bool
    p2_intervention_nonzero: bool
    p3_realized: Optional[bool]
    p4_determinism: bool
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"pair_key": self.pair_key, "context_id": self.context_id,
                "gene": self.gene, "verdict": self.verdict,
                "P1_structural_isolation": self.p1_structural_isolation,
                "P2_intervention_nonzero": self.p2_intervention_nonzero,
                "P3_realized_in_output": self.p3_realized,
                "P4_determinism": self.p4_determinism,
                "detail": self.detail}


# ---------------------------------------------------------------------------
# §8 P1 — STRUCTURAL_ISOLATION
# ---------------------------------------------------------------------------
def p1_structural_isolation(gene: Gene, run, bank=None, track=None) -> Dict[str, Any]:
    """対象 gene のトグルだけが true で、Performance path に 2-D が無いこと。

    証拠は `run` に記録済みのもの（条件ごとの proxy tripwire / 1-D payload 検査）を
    読むだけ。`bank`/`track` を渡した場合はその場でも tripwire を張り直す
    （単体テスト用の経路であって、判定基準は変えない）。
    """
    tg = sp.toggles_for(gene)
    cond = sp.GENE_CONDITION[gene]
    detail: Dict[str, Any] = {
        "only_target_toggle_on": sp.only_one_toggle_on(tg, gene),
        "toggles": tg.label,
        "performance_payload_1d": bool(getattr(run, "performance_payload_1d", True)),
        "identity_sha256": run.identity_sha256[:16],
        "performance_sha256": run.performance_sha256[:16],
    }
    co = run.conditions.get(cond)
    b0 = run.conditions.get("B0")
    recorded = [c for c in (co, b0) if c is not None]
    detail["tripwire_status"] = [c.tripwire_status for c in recorded]
    detail["tripwire_accessed"] = sorted({a for c in recorded
                                          for a in getattr(c, "tripwire_accessed", [])})
    detail["no_two_dimensional_payload"] = all(c.tripwire_status == "pass" for c in recorded) \
        if recorded else False
    ok = (detail["only_target_toggle_on"] and detail["performance_payload_1d"]
          and detail["no_two_dimensional_payload"])
    if bank is not None and track is not None:
        tw = pbg.gate_tripwire(pc.compose, bank, track, tg)
        detail["live_tripwire_status"] = tw.status
        ok = ok and tw.status == "pass"
    return {"pass": bool(ok), **detail}


def assert_performance_payload_1d(perf: prp.RealPerformanceTrack) -> bool:
    try:
        prp.assert_no_spectral_payload(perf)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# §8 P2 — INTERVENTION_NONZERO
# ---------------------------------------------------------------------------
def p2_intervention_nonzero(gene: Gene, intervention: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    info = intervention.get(gene.value, {})
    return {"pass": bool(info.get("nonzero", False)),
            "amount": info.get("amount"), "unit": info.get("unit"),
            "structural_noop": bool(info.get("structural_noop", False))}


# ---------------------------------------------------------------------------
# §8 P3 — REALIZED_IN_OUTPUT
# ---------------------------------------------------------------------------
def p3_realized_in_output(gene: Gene, run) -> Dict[str, Any]:
    metric, direction = sp.GENE_METRIC[gene]
    cond = sp.GENE_CONDITION[gene]
    base = run.conditions["B0"].metrics.get(metric)
    cur = run.conditions[cond].metrics.get(metric)
    if base is None or cur is None:
        return {"pass": None, "metric": metric, "direction": direction,
                "b0": base, "gene_condition": cur, "reason": "metric unavailable"}
    ok = cur < base if direction == "lower" else cur > base
    return {"pass": bool(ok), "metric": metric, "direction": direction,
            "b0": base, "gene_condition": cur, "delta": round(cur - base, 6)}


# ---------------------------------------------------------------------------
# §8 P4 — DETERMINISM
# ---------------------------------------------------------------------------
def _condition_determinism(run, cond: str,
                           cross_process: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    recorded = run.conditions[cond].sample_sha256
    same_proc = run.repeat_sample_sha256.get(cond)
    cross = cross_process.get(run.pair_key, {}).get(cond)
    same_ok = recorded == same_proc
    cross_ok = cross is not None and recorded == cross
    return {"pass": bool(same_ok and cross_ok),
            "same_process": bool(same_ok), "cross_process": bool(cross_ok),
            "recorded": recorded[:16], "same_process_sha": (same_proc or "")[:16],
            "cross_process_sha": (cross or "")[:16]}


def p4_determinism(gene: Gene, run, cross_process: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """gene 条件 **と B0** の両方が再現すること。

    P3 は「B0 との比較」なので、B0 が非決定論なら比較そのものが再現しない。
    gene 条件だけを見ていると、揺れている baseline に対して SUPPORTED が立ち、
    偽の S3 PASS まで通ってしまう。両方を見る。
    """
    cond = sp.GENE_CONDITION[gene]
    d_gene = _condition_determinism(run, cond, cross_process)
    d_base = _condition_determinism(run, "B0", cross_process)
    return {"pass": bool(d_gene["pass"] and d_base["pass"]),
            "gene_condition": d_gene, "baseline_B0": d_base,
            # 後方互換の要約（記録の読み手が 1 行で見るため）
            "same_process": bool(d_gene["same_process"] and d_base["same_process"]),
            "cross_process": bool(d_gene["cross_process"] and d_base["cross_process"])}


# ---------------------------------------------------------------------------
# §9 Pair-Level Verdict
# ---------------------------------------------------------------------------
def pair_verdict(gene: Gene, run, cross_process: Dict[str, Dict[str, str]],
                 bank=None, track=None) -> PairGeneResult:
    d1 = p1_structural_isolation(gene, run, bank, track)
    d2 = p2_intervention_nonzero(gene, run.intervention)
    d3 = p3_realized_in_output(gene, run)
    d4 = p4_determinism(gene, run, cross_process)
    if not d1["pass"]:
        v = PairVerdict.FAILED
    elif not d2["pass"]:
        v = PairVerdict.NOT_EVALUABLE
    elif d3["pass"] is True and d4["pass"]:
        v = PairVerdict.SUPPORTED
    else:
        v = PairVerdict.UNSUPPORTED
    return PairGeneResult(
        pair_key=run.pair_key, context_id=run.context_id, gene=gene.value,
        verdict=v.value, p1_structural_isolation=d1["pass"],
        p2_intervention_nonzero=d2["pass"], p3_realized=d3["pass"],
        p4_determinism=d4["pass"],
        detail={"P1": d1, "P2": d2, "P3": d3, "P4": d4})


# ---------------------------------------------------------------------------
# §10 Gene-Level Verdict
# ---------------------------------------------------------------------------
def gene_verdict(results: Sequence[PairGeneResult],
                 criteria: sp.Criteria = sp.Criteria()) -> Dict[str, Any]:
    evaluable = [r for r in results
                 if r.verdict in (PairVerdict.SUPPORTED.value, PairVerdict.UNSUPPORTED.value)]
    supported = [r for r in evaluable if r.verdict == PairVerdict.SUPPORTED.value]
    structural_failures = sum(1 for r in results if not r.p1_structural_isolation)
    # §10「構造分離と決定論は平均化せず、1 件でも失敗したら gene 全体を FAILED」。
    # evaluable かどうかで絞らない（設計書に絞り込みの記載が無く、fail-closed 側を取る）。
    determinism_failures = sum(1 for r in results if not r.p4_determinism)
    ev_ctx = sorted({r.context_id for r in evaluable})
    sup_ctx = sorted({r.context_id for r in supported})
    ratio = (len(supported) / len(evaluable)) if evaluable else 0.0

    agg = {
        "evaluable_pairs": len(evaluable),
        "supported_pairs": len(supported),
        "support_ratio": round(ratio, 6),
        "evaluable_contexts": ev_ctx,
        "supported_contexts": sup_ctx,
        "distinct_evaluable_context_count": len(ev_ctx),
        "distinct_supported_context_count": len(sup_ctx),
        "structural_failures": structural_failures,
        "determinism_failures": determinism_failures,
        "pairs": {r.pair_key: r.as_dict() for r in results},
    }
    # §10 判定優先順位: FAILED -> NOT_EVALUABLE -> SUPPORTED -> UNSUPPORTED
    if structural_failures > 0 or determinism_failures > 0:
        agg["verdict"] = GeneVerdict.FAILED.value
    elif (len(evaluable) < criteria.min_evaluable_pairs
          or len(ev_ctx) < criteria.min_evaluable_contexts):
        agg["verdict"] = GeneVerdict.NOT_EVALUABLE.value
    elif (ratio >= criteria.min_support_ratio
          and len(sup_ctx) >= criteria.min_supported_contexts):
        agg["verdict"] = GeneVerdict.SUPPORTED.value
    else:
        agg["verdict"] = GeneVerdict.UNSUPPORTED.value
    return agg


# ---------------------------------------------------------------------------
# §11 S3 Overall Gate
# ---------------------------------------------------------------------------
def overall_verdict(genes: Dict[str, Dict[str, Any]],
                    criteria: sp.Criteria = sp.Criteria()) -> Dict[str, Any]:
    supported = sorted(g for g, v in genes.items()
                       if v.get("verdict") == GeneVerdict.SUPPORTED.value)
    return {"supported_gene_count": len(supported),
            "supported_genes": supported,
            "verdict": "PASS" if len(supported) >= criteria.min_supported_genes else "FAIL"}
