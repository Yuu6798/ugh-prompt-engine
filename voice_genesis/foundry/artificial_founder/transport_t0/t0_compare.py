"""t0_compare.py — 判定（設計書 §13 / §14）。

**T0 は独自の許容値を持たない。** §13「AF-P0 original tolerance をそのまま
再利用。T0 で緩和禁止」を、`af_compare.compare_reexpression` を **そのまま
呼ぶ**ことで構造的に守る。T0 側で閾値を書き写すと、写し間違い・こっそりした
緩和の余地が生まれる。

したがって本モジュールは:

* 目標 3 形質（duration / energy / afterglow）
* sentinel 7 形質（§14）

のどちらも、同じ 1 回の `compare_reexpression` の出力から読み出すだけ。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

_AF = Path(__file__).resolve().parent.parent
_HERE = Path(__file__).resolve().parent
for _p in (str(_AF), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from af_compare import compare_reexpression  # noqa: E402

from t0_schema import FROZEN_SENTINEL_FAMILIES  # noqa: E402

#: 目標 3 形質 -> `compare_reexpression` の family キー。
#: duration は onset と share の 2 family を両方満たす必要がある（§13）。
TARGET_FAMILIES: Dict[str, tuple] = {
    "duration": ("duration_onset", "duration_share"),
    "energy": ("energy_sustain",),
    "afterglow": ("afterglow",),
}


def compare_transported(genome: Any, criteria: Any,
                        body: Mapping[str, Dict[str, Any]],
                        transported: Mapping[str, Dict[str, Any]]) -> Dict[str, Any]:
    """輸送後の測定を **AF-P0 original criteria** で判定する。

    `criteria` は AF-P0 の `Criteria`（T0 criteria ではない）。T0 criteria は
    候補順・localization epsilon・sentinel 一覧を持つだけで、**閾値は持たない**。
    """
    return compare_reexpression(genome, criteria, body, transported)


def trait_verdict(comparison: Mapping[str, Any], trait: str) -> Dict[str, Any]:
    """目標 1 形質の合否（関係する family がすべて PASS）。"""
    families = TARGET_FAMILIES[trait]
    rows = {f: (comparison.get(f) or {}) for f in families}
    failed = [f for f, r in rows.items() if r.get("verdict") != "PASS"]
    worst = {f: r.get("worst") for f, r in rows.items()}
    return {
        "trait": trait, "families": list(families),
        "per_family": {f: {"verdict": rows[f].get("verdict"),
                           "worst": rows[f].get("worst"),
                           "tolerance": rows[f].get("tolerance"),
                           "failed_units": rows[f].get("failed_units")}
                       for f in families},
        "worst": worst, "failed_families": failed,
        "verdict": "PASS" if not failed else "FAIL",
    }


def sentinel_verdict(comparison: Mapping[str, Any],
                     families: Sequence[str] = FROZEN_SENTINEL_FAMILIES
                     ) -> Dict[str, Any]:
    """§14 Sentinel Non-Regression。1 つでも FAIL なら候補は棄却される。"""
    rows = {f: (comparison.get(f) or {}) for f in families}
    failed = [f for f, r in rows.items() if r.get("verdict") != "PASS"]
    return {
        "families": list(families),
        "per_family": {f: {"verdict": rows[f].get("verdict"),
                           "worst": rows[f].get("worst"),
                           "tolerance": rows[f].get("tolerance")}
                       for f in families},
        "failed_families": failed,
        "verdict": "PASS" if not failed else "FAIL",
        "reason": None if not failed else "REJECTED_BY_REGRESSION",
    }


def candidate_result(comparison: Mapping[str, Any], trait: str) -> Dict[str, Any]:
    """1 候補の評価: **目標形質 PASS かつ sentinel 非回帰**（§14）。

    sentinel が落ちた候補は、目標形質が通っていても採用しない。
    """
    target = trait_verdict(comparison, trait)
    sentinels = sentinel_verdict(comparison)
    ok = target["verdict"] == "PASS" and sentinels["verdict"] == "PASS"
    out = {
        "trait": trait, "target": target, "sentinels": sentinels,
        "verdict": "PASS" if ok else "FAIL",
    }
    if target["verdict"] == "PASS" and sentinels["verdict"] != "PASS":
        out["reason"] = "REJECTED_BY_REGRESSION"
    elif target["verdict"] != "PASS":
        out["reason"] = f"{trait.upper()}_NOT_TRANSPORTED"
    return out


def combined_result(comparison: Mapping[str, Any]) -> Dict[str, Any]:
    """§19 Stage D。3 形質 + 全 sentinel を同時に見る。"""
    traits = {t: trait_verdict(comparison, t) for t in TARGET_FAMILIES}
    sentinels = sentinel_verdict(comparison)
    failed = [t for t, r in traits.items() if r["verdict"] != "PASS"]
    ok = not failed and sentinels["verdict"] == "PASS"
    out: Dict[str, Any] = {
        "traits": traits, "sentinels": sentinels,
        "failed_traits": failed,
        "verdict": "PASS" if ok else "FAIL",
    }
    if not ok:
        out["reason"] = "COMBINATION_NOT_ESTABLISHED"
    return out


def baseline_reproduces_p0(comparison: Mapping[str, Any],
                           expected_failures: Sequence[str]) -> Dict[str, Any]:
    """§16 Stage A。P0 の re-expression 失敗**方向**が再現しているか。

    「同じ 3 family が FAIL する」ことを求める。数値の完全一致は求めない
    （環境差で worst は動きうる）が、**どれが落ちるか**が変わったら baseline を
    語れないので BLOCKED にする。
    """
    observed = [f for f in ("duration_onset", "duration_share", "energy_sustain",
                            "afterglow")
                if (comparison.get(f) or {}).get("verdict") != "PASS"]
    want = set(expected_failures)
    got = set(observed)
    # **等号**を要求する。包含（`want <= got`）だと、P0 では通っていた
    # `duration_share` が新たに落ちても PASS になり、環境差やパス差を
    # 後段の operator が覆い隠す。「P0 と同じ 3 つが落ちる」ことが baseline
    # 再現の意味なので、増えても減っても再現していない。
    ok = want == got
    return {
        "expected_failing_families": sorted(want),
        "observed_failing_families": sorted(got),
        "missing": sorted(want - got),
        "unexpected": sorted(got - want),
        "verdict": "PASS" if ok else "FAIL",
        "reason": None if ok else "BASELINE_NOT_REPRODUCED",
    }


def worst_of(comparison: Mapping[str, Any], family: str) -> Optional[float]:
    v = (comparison.get(family) or {}).get("worst")
    return None if v is None else float(v)


def summarize(comparison: Mapping[str, Any]) -> Dict[str, Any]:
    """記録用の要約（family -> verdict / worst / tolerance）。"""
    out: Dict[str, Any] = {}
    for family, row in comparison.items():
        if not isinstance(row, dict):
            continue
        out[family] = {"verdict": row.get("verdict"), "worst": row.get("worst"),
                       "tolerance": row.get("tolerance")}
    return out


__all__ = ["TARGET_FAMILIES", "compare_transported", "trait_verdict",
           "sentinel_verdict", "candidate_result", "combined_result",
           "baseline_reproduces_p0", "worst_of", "summarize"]
