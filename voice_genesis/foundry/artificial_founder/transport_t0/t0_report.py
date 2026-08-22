"""t0_report.py — 記録の生成（設計書 §2 / §22 / §23 / §30 / §31 / §40）。

主張の上限（§2 Claim Ceiling）を **定型で** 記録に埋め込む。特に:

* sidecar を使ったら「WORLD native preservation ではない」と必ず併記する（§7）
* AF-T0 が PASS しても **AF-P0 = NOT_ESTABLISHED は変わらない**（§23 / §40）

この 2 つは書き忘れると主張が過大になるので、文面を定数に持ち、
`build_t0_results` / `build_record_md` の両方から必ず出力する。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from t0_schema import FROZEN_P0_VERDICT, T0_SCHEMA

#: §22 PASS 時の宣言。
PASS_DECLARATION = (
    "AF-T0 PASS — Trait Transport Fidelity established for Duration, Energy, "
    "and AG-α under the preregistered AF0 and fixture conditions.")

#: §40 Completion Declaration。
COMPLETION_DECLARATION = (
    "VoiceGenesis AF-T0 COMPLETE — Trait Transport Fidelity established for "
    "Duration, Energy, and AG-α under the preregistered AF0 and fixture conditions.")

#: §23 / §40。PASS でも必ず併記する。
P0_INVARIANT = [
    "AF-P0 historical verdict remains NOT_ESTABLISHED.",
    "AF-T0 does not retroactively alter AF-P0.",
]

#: §7。sidecar を使った場合の必須注記。
SIDECAR_CAVEAT = (
    "WORLD 単独保存ではなく、明示 Trait Sidecar を併用する transport "
    "architecture で保存された。")

#: §2 禁止主張。
FORBIDDEN_CLAIMS = [
    "AF-P0 は実は PASS だった",
    "WORLD は全形質を完全保存する",
    "任意の人間音声で成立",
    "inheritance 成立",
    "mutation 成立",
    "crossover 成立",
]


def build_t0_results(*, pins: Mapping[str, Any], localization: Mapping[str, Any],
                     registry: Mapping[str, Any], sentinels: Mapping[str, Any],
                     combined: Mapping[str, Any], fresh: Mapping[str, Any],
                     gates: Sequence[Any], overall: Mapping[str, Any],
                     baseline: Mapping[str, Any],
                     determinism: Mapping[str, Any]) -> Dict[str, Any]:
    """§31 `t0_results.json`。"""
    traits = registry.get("traits") or {}
    out: Dict[str, Any] = {
        "schema": T0_SCHEMA,
        "experiment_id": "AF-T0",
        "founder_id": "AF0",
        "p0_reference": {
            "verdict": FROZEN_P0_VERDICT,
            "note": " ".join(P0_INVARIANT),
        },
        "baseline_replay": dict(baseline),
        "localization": {
            trait: {"first_divergence_stage":
                    (localization.get("traits") or {}).get(trait, {})
                    .get("first_divergence_stage")}
            for trait in ("duration", "energy", "afterglow")
        },
        "transport": {
            trait: {"mode": traits.get(trait, {}).get("mode"),
                    "operator": traits.get(trait, {}).get("operator")}
            for trait in ("duration", "energy", "afterglow")
        },
        "sentinels": {f: v.get("verdict")
                      for f, v in (sentinels.get("per_family") or {}).items()},
        "combined": {"verdict": combined.get("verdict"),
                     "failed_traits": combined.get("failed_traits") or []},
        "fresh_confirmation": {"verdict": fresh.get("verdict"),
                               "after_freeze": bool(fresh.get("after_freeze"))},
        "determinism": dict(determinism),
        "gates": [g.as_dict() if hasattr(g, "as_dict") else dict(g) for g in gates],
        "overall": dict(overall),
        "pins": dict(pins),
        "claim_ceiling": _claim_ceiling(registry, overall),
    }
    # §22。AG-alpha は設計の表記に合わせて別名でも引けるようにする。
    out["transport"]["AG-alpha"] = out["transport"]["afterglow"]
    out["localization"]["AG-alpha"] = out["localization"]["afterglow"]
    return out


def _claim_ceiling(registry: Mapping[str, Any],
                   overall: Mapping[str, Any]) -> Dict[str, Any]:
    """§2 / §7。言ってよいことと、必ず併記することを記録に固定する。"""
    allowed: List[str] = []
    if overall.get("verdict") == "PASS":
        allowed.append(PASS_DECLARATION)
        if registry.get("uses_sidecar"):
            allowed.append(SIDECAR_CAVEAT)
    return {
        "allowed": allowed,
        "always": list(P0_INVARIANT),
        "forbidden": list(FORBIDDEN_CLAIMS),
        "uses_sidecar": bool(registry.get("uses_sidecar")),
        "all_native": bool(registry.get("all_native")),
    }


def _fmt(v: Any, nd: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def build_record_md(results: Mapping[str, Any], localization: Mapping[str, Any],
                    registry: Mapping[str, Any],
                    combined: Mapping[str, Any]) -> str:
    """§30 `AF_T0_RECORD.md`。"""
    overall = results.get("overall") or {}
    lines: List[str] = [
        "# AF-T0 Trait Transport Fidelity — 実行記録",
        "",
        "- experiment_id: `AF-T0` / founder_id: `AF0`",
        f"- overall verdict: **{overall.get('verdict')}**",
    ]
    if overall.get("reason_codes"):
        lines.append(f"- reason codes: {overall['reason_codes']}")
    lines += [
        "",
        "## AF-P0 との関係（§23）",
        "",
    ] + [f"- {s}" for s in P0_INVARIANT] + [
        "",
        "## Localization（§6）",
        "",
        "| trait | first_divergence_stage | worst total delta |",
        "|---|---|---|",
    ]
    for trait in ("duration", "energy", "afterglow"):
        row = (localization.get("traits") or {}).get(trait, {})
        lines.append(f"| {trait} | `{row.get('first_divergence_stage')}` | "
                     f"{_fmt(row.get('worst_total_delta'))} |")

    lines += [
        "",
        "## Transport（§7 / §22）",
        "",
        "| trait | operator | mode | candidates tried |",
        "|---|---|---|---|",
    ]
    for trait in ("duration", "energy", "afterglow"):
        row = (registry.get("traits") or {}).get(trait, {})
        lines.append(f"| {trait} | `{row.get('operator')}` | `{row.get('mode')}` | "
                     f"{row.get('candidates_tried')} |")
    if registry.get("uses_sidecar"):
        lines += ["", f"> {SIDECAR_CAVEAT}"]

    lines += ["", "## Combined package（§19）", ""]
    for trait, row in (combined.get("traits") or {}).items():
        lines.append(f"- **{trait}**: {row.get('verdict')} "
                     f"(worst={row.get('worst')})")
    sent = combined.get("sentinels") or {}
    lines.append(f"- **sentinels**: {sent.get('verdict')} "
                 f"(failed={sent.get('failed_families')})")

    lines += ["", "## Gates（§21）", "", "| gate | name | verdict |", "|---|---|---|"]
    for g in results.get("gates") or []:
        lines.append(f"| `{g.get('gate')}` | {g.get('name')} | {g.get('verdict')} |")

    lines += ["", "## 主張上限（§2）", "", "**言ってよい:**", ""]
    allowed = (results.get("claim_ceiling") or {}).get("allowed") or []
    lines += [f"- {s}" for s in allowed] if allowed else ["- （PASS 時のみ）"]
    lines += ["", "**禁止:**", ""] + [f"- {s}" for s in FORBIDDEN_CLAIMS]
    lines += ["", "## 次段（§24 / §38）", "",
              "- AF-T0 PASS して初めて AF-P1 Controlled Mutation へ進む。",
              "- P0 と同じく、本 run は P1 へ自動進行しない（§34-15 STOP）。", ""]
    return "\n".join(lines)


def should_freeze(overall: Mapping[str, Any]) -> bool:
    """§30。freeze は PASS 時のみ。"""
    return overall.get("verdict") == "PASS"


def build_freeze(results: Mapping[str, Any]) -> Dict[str, Any]:
    """§20 / §30。凍結する combined package の内容。"""
    return {
        "schema": "voicegenesis-af-t0-freeze/1.0",
        "experiment_id": "AF-T0",
        "founder_id": "AF0",
        "declaration": COMPLETION_DECLARATION,
        "p0_invariant": list(P0_INVARIANT),
        "transport": results.get("transport"),
        "pins": results.get("pins"),
        "claim_ceiling": results.get("claim_ceiling"),
    }


__all__ = ["PASS_DECLARATION", "COMPLETION_DECLARATION", "P0_INVARIANT",
           "SIDECAR_CAVEAT", "FORBIDDEN_CLAIMS", "build_t0_results",
           "build_record_md", "should_freeze", "build_freeze"]
