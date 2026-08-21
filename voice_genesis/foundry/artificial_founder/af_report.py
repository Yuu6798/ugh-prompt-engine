"""af_report.py — `p0_results.json` / `AF_P0_RECORD.md` / freeze（設計書 §25 / §26 / §33）。

**主張の上限を守る**（§4）。PASS 時でも「人間のように自然」「人工生命が成立」
「世代遺伝が成立」等は書かない。文言は §4 / §20 / §33 の定型を使い、ここで
自由作文しない。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from af_spec import (EXPERIMENT_ID, FREEZE_SCHEMA, P0_SCHEMA, FounderGenome, canonical_json,
                     round_floats, write_json)

#: §4 PASS 時に言ってよいこと（短縮宣言）。
PASS_DECLARATION: Sequence[str] = (
    "Artificial Founder AF0 ESTABLISHED",
    "Founder Trait Set AF0/0.1 PRESERVED",
)

#: §4 PASS でも言ってはいけないこと。record に必ず併記して主張の上限を固定する。
FORBIDDEN_CLAIMS: Sequence[str] = (
    "人間のように自然", "完成した日本語歌手", "人工生命が成立", "gene が教育可能",
    "世代遺伝が成立", "crossover 成立", "Founder Trait が知覚的に顕著",
    "HL-α / AR-α / AG-α が進化的に有利", "AF0 に人格や意識がある",
)

#: §33「未確認」。PASS でもここは空欄のまま。
UNVERIFIED: Sequence[str] = (
    "Naturalness", "Perceptual uniqueness", "Learning", "Education", "Mutation",
    "Crossover", "Inheritance", "Selection", "Multi-generation evolution",
    "Human x Synthetic",
)


def _verdict_of(gates: Sequence[Any], gate_id: str) -> str:
    for g in gates:
        if g.gate_id == gate_id:
            return g.verdict
    return "SKIPPED"


def build_p0_results(genome: FounderGenome, gates: Sequence[Any], overall: Mapping[str, Any],
                     pins: Mapping[str, Any], body_cmp: Mapping[str, Any],
                     reexp_cmp: Optional[Mapping[str, Any]],
                     extra: Mapping[str, Any]) -> Dict[str, Any]:
    """§26 の `p0_results.json`（スキーマ 1.1）を組む。"""

    def _sub(section: Mapping[str, Any], key: str) -> str:
        return section.get(key, {}).get("verdict", "SKIPPED") if section else "SKIPPED"

    results: Dict[str, Any] = {
        "schema": P0_SCHEMA,
        "experiment_id": EXPERIMENT_ID,
        "founder_id": genome.founder_id,
        "phenotype": genome.phenotype_codename,
        "source_free": {"verdict": _verdict_of(gates, "G0")},
        "determinism": {
            "same_process": extra.get("determinism", {}).get("same_process", "SKIPPED"),
            "cross_process": extra.get("determinism", {}).get("cross_process", "SKIPPED"),
        },
        "body": {
            "utau": _verdict_of(gates, "G3"),
            "voicegenesis_ingestion": _verdict_of(gates, "G4"),
        },
        "meter_control": {"verdict": _verdict_of(gates, "G5")},
        "standard_traits": {
            "identity": {"verdict": _verdict_of(gates, "G6")},
            "f0": {"verdict": _verdict_of(gates, "G9")},
            "duration": {"verdict": _verdict_of(gates, "G10")},
            "energy": {"verdict": _verdict_of(gates, "G11")},
            "release": {"verdict": _verdict_of(gates, "G12")},
        },
        "founder_traits": {
            "HL-alpha": {"verdict": _verdict_of(gates, "G7")},
            "AR-alpha": {"verdict": _verdict_of(gates, "G8")},
            "AR-beta": {"verdict": _verdict_of(gates, "G8")},
            "AG-alpha": {"verdict": _verdict_of(gates, "G13")},
        },
        "gates": [g.as_dict() for g in gates],
        "pins": dict(pins),
        "body_comparison": {k: _sub(body_cmp, k) for k in sorted(body_cmp)},
        "reexpression_comparison": ({k: _sub(reexp_cmp, k) for k in sorted(reexp_cmp)}
                                    if reexp_cmp else "SKIPPED"),
        "overall": dict(overall),
        "claim_ceiling": {"allowed": list(PASS_DECLARATION), "forbidden": list(FORBIDDEN_CLAIMS),
                          "unverified": list(UNVERIFIED)},
    }
    return round_floats(results, 6)


def _table(rows: Sequence[Sequence[str]], header: Sequence[str]) -> str:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def build_record_md(genome: FounderGenome, results: Mapping[str, Any],
                    body_cmp: Mapping[str, Any], reexp_cmp: Optional[Mapping[str, Any]],
                    extra: Mapping[str, Any]) -> str:
    """`AF_P0_RECORD.md`。数値は結果 JSON から引くだけで、ここで再計算しない。"""
    overall = results["overall"]
    lines: List[str] = [
        f"# {EXPERIMENT_ID} RECORD — Artificial Founder {genome.founder_id}",
        "",
        f"- **Overall verdict**: `{overall['verdict']}`",
        f"- **Reason codes**: {overall['reason_codes'] or 'none'}",
        f"- **Phenotype**: {genome.phenotype_codename}",
        f"- **Spec sha256**: `{genome.sha256}`",
        "",
        "本記録は事前登録設計 `DESIGN_AF_P0.md` に対する実行結果である。",
        "許容値・候補・Gate は結果を見て変更していない。",
        "",
        "## Gates",
        "",
        _table([[g["gate"], g["name"], g["verdict"],
                 g["detail"].get("reason_code", "")] for g in results["gates"]],
               ["Gate", "Name", "Verdict", "Reason"]),
        "",
        "## Body traits (§18.1)",
        "",
        _table([[k, body_cmp[k].get("verdict", "?"),
                 _fmt_worst(body_cmp[k])] for k in sorted(body_cmp)],
               ["Trait", "Verdict", "Worst error / detail"]),
        "",
    ]
    if reexp_cmp:
        lines += [
            "## VoiceGenesis re-expression traits (§18.2)",
            "",
            _table([[k, reexp_cmp[k].get("verdict", "?"), _fmt_worst(reexp_cmp[k])]
                    for k in sorted(reexp_cmp)],
                   ["Trait", "Verdict", "Worst error / detail"]),
            "",
        ]
    else:
        lines += ["## VoiceGenesis re-expression traits (§18.2)", "",
                  "SKIPPED（G4 が成立していないため測定していない）。", ""]

    split = stage_split(results["gates"])
    if split:
        lines += [
            "## 失敗の切り分け（§16 二段測定）",
            "",
            "Body（44.1 kHz voicebank）と VoiceGenesis re-expression（WORLD 24 kHz）の",
            "どちらで形質が落ちたかを分ける。compiler 失敗 / body 表現失敗 / WORLD 分析・",
            "再合成での形質喪失を混同しないための §16 の目的そのもの。",
            "",
            _table([[row["gate"], row["name"], row["failed_stage"]] for row in split],
                   ["Gate", "Name", "落ちた段"]),
            "",
        ]

    lines += [
        "## Claim ceiling (§4)",
        "",
        "PASS 時に言ってよいこと:",
        "",
    ]
    lines += [f"- {c}" for c in PASS_DECLARATION]
    lines += ["", "PASS でも言ってはいけないこと:", ""]
    lines += [f"- {c}" for c in FORBIDDEN_CLAIMS]
    lines += ["", "## 未確認 (§33)", ""]
    lines += [f"- {c}" for c in UNVERIFIED]
    lines += ["", "## Environment / pins", "",
              "```json", canonical_json(results["pins"]), "```", ""]
    notes = extra.get("notes") or []
    if notes:
        lines += ["## Notes", ""] + [f"- {n}" for n in notes] + [""]
    return "\n".join(lines)


def stage_split(gates: Sequence[Mapping[str, Any]]) -> List[Dict[str, str]]:
    """不成立の trait Gate が Body / re-expression のどちらで落ちたかを分類する（§16）。"""
    rows: List[Dict[str, str]] = []
    for g in gates:
        if g["verdict"] == "PASS" or "reason_code" not in g.get("detail", {}):
            continue
        detail = g["detail"]
        body = detail.get("body")
        reexp = detail.get("reexpression")
        if not isinstance(body, dict):
            continue
        body_failed = any(v != "PASS" for v in body.values())
        if reexp == "SKIPPED":
            stage = "body" if body_failed else "re-expression (SKIPPED)"
        elif isinstance(reexp, dict):
            reexp_failed = any(v != "PASS" for v in reexp.values())
            if body_failed and reexp_failed:
                stage = "both"
            elif body_failed:
                stage = "body"
            elif reexp_failed:
                stage = "re-expression"
            else:
                continue
        else:
            stage = "unknown"
        rows.append({"gate": g["gate"], "name": g["name"], "failed_stage": stage})
    return rows


def _fmt_worst(section: Mapping[str, Any]) -> str:
    if "worst" in section and section["worst"] is not None:
        base = f"worst={section['worst']:.4g} / tol={section.get('tolerance')}"
        if section.get("failed_units"):
            base += f" / failed={section['failed_units']}"
        return base
    if "n_pass" in section:
        return f"{section['n_pass']}/{section['n_peaks']} peaks (need {section['required_pass']})"
    if "median" in section and section["median"] is not None:
        return f"median={section['median']:.4g} / each>={section.get('each_min')}"
    if section.get("failed_units"):
        return f"failed={section['failed_units']}"
    return ""


def write_freeze(out_dir: str | Path, genome: FounderGenome,
                 results: Mapping[str, Any]) -> Dict[str, Any]:
    """§25: freeze は **PASS 時のみ**。"""
    if results["overall"]["verdict"] != "PASS":
        raise ValueError("freeze artifacts may only be written on an overall PASS verdict (§25)")
    freeze_dir = Path(out_dir) / "freeze"
    freeze_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": FREEZE_SCHEMA,
        "experiment_id": EXPERIMENT_ID,
        "founder_id": genome.founder_id,
        "phenotype_codename": genome.phenotype_codename,
        "declaration": list(PASS_DECLARATION),
        "founder_trait_set": {
            "HL-alpha": {"odd_multiplier": genome.hl_odd_multiplier,
                         "even_multiplier": genome.hl_even_multiplier},
            "AR-alpha": {"center_hz": genome.ar_alpha_center_hz,
                         "bandwidth_hz": genome.ar_alpha_bandwidth_hz,
                         "gain_db": genome.ar_alpha_gain_db},
            "AR-beta": {"center_hz": genome.ar_beta_center_hz,
                        "bandwidth_hz": genome.ar_beta_bandwidth_hz,
                        "beta_alpha_energy_ratio": genome.beta_alpha_energy_ratio},
            "AG-alpha": {"main_release_ms": genome.main_taper_ms,
                         "terminal_f0_delta_cents": genome.terminal_f0_delta_cents,
                         "ar_alpha_afterglow_extra_ms": genome.ar_alpha_afterglow_extra_ms},
        },
        "pins": results["pins"],
        "unverified": list(UNVERIFIED),
    }
    write_json(freeze_dir / "ARTIFICIAL_FOUNDER_AF0_FREEZE.json", payload)
    md = [
        f"# ARTIFICIAL FOUNDER {genome.founder_id} — FREEZE",
        "",
        f"> **VoiceGenesis Artificial Founder Series {EXPERIMENT_ID} COMPLETE — "
        f"Artificial Founder {genome.founder_id} ESTABLISHED.**",
        "",
        "> **AF0 Founder Trait Set 0.1 PRESERVED — Harmonic Lattice HL-α、Dual Ancestral "
        "Resonance AR-α/β、Afterglow Expression AG-α は、人工始祖の身体生成と "
        "VoiceGenesis 再表現を通じて事前登録範囲で回収された。**",
        "",
        "P0 はここで凍結する。P1 mutation へ自動進行しない（§23）。",
        "",
        "## 未確認",
        "",
    ]
    md += [f"- {c}" for c in UNVERIFIED]
    md += ["", "## Pins", "", "```json", canonical_json(results["pins"]), "```", ""]
    (freeze_dir / "ARTIFICIAL_FOUNDER_AF0_FREEZE.md").write_text("\n".join(md),
                                                                 encoding="utf-8")
    return {"freeze_dir": str(freeze_dir)}
