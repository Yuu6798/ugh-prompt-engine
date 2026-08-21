"""genome_s3/s3_report.py — 機械可読 / 人間可読の記録を生成する（設計書 §15–§17）。

出力:

- `results/s3_results.json`   … §16 のスキーマ
- `results/S3_RECORD.md`      … 人間可読の要約

**判定はここで行わない。** `s3_gates` の返り値をそのまま並べるだけ。
WAV 本体は repository に commit しない（`results/.gitignore` で除外）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

_HERE = Path(__file__).resolve().parent
_FOUNDRY = _HERE.parent
for _p in (_HERE, _FOUNDRY / "planb", _FOUNDRY / "planb_real"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import s3_gates as sg  # noqa: E402
import s3_runner as sr  # noqa: E402
import s3_spec as sp  # noqa: E402

RESULTS = _HERE / "results"
JSON_PATH = RESULTS / "s3_results.json"
RECORD_PATH = RESULTS / "S3_RECORD.md"


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False, sort_keys=False)


def publish_bundle(pairs: Tuple[Tuple[Path, str], ...]) -> None:
    """JSON と Markdown を **1 つの束**として差し替える。

    この 2 つは同じ実験結果の 2 つの見え方なので、片方だけ新しい状態
    （途中で落ちた・ディスクが埋まった）を露出させると会計が壊れる。
    全部を temp へ書き切ってから rename する。書き込み中に落ちた場合、
    既存の束はそのまま残る。
    """
    staged: List[Tuple[Path, Path]] = []
    try:
        for path, text in pairs:
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            staged.append((tmp, path))
    except BaseException:
        for tmp, _dest in staged:
            tmp.unlink(missing_ok=True)
        raise
    for tmp, dest in staged:
        os.replace(tmp, dest)


def reproducibility_records(runs: List[sr.PairRun]) -> List[Dict[str, Any]]:
    """§17 の再現性記録。1 出力 = 1 行。"""
    rows: List[Dict[str, Any]] = []
    commit = sr.source_commit()
    for run in runs:
        for cond, co in run.conditions.items():
            rows.append({
                "pair_key": run.pair_key,
                "context_id": run.context_id,
                "gene": next((g.value for g, c in sp.GENE_CONDITION.items() if c == cond), None),
                "condition": cond,
                "toggle_state": co.toggles,
                "identity_sha256": run.identity_sha256,
                "performance_sha256": run.performance_sha256,
                "sample_sha256": co.sample_sha256,
                "wav_sha256": co.wav_sha256,
                "code_commit": commit,
            })
    return rows


def build_results(runs: List[sr.PairRun], meta: Dict[str, Any],
                  cross: Dict[str, Dict[str, str]],
                  criteria: sp.Criteria = sp.Criteria()) -> Dict[str, Any]:
    genes: Dict[str, Dict[str, Any]] = {}
    for gene in sp.Gene:
        per_pair = [sg.pair_verdict(gene, run, cross) for run in runs]
        genes[gene.value] = sg.gene_verdict(per_pair, criteria)
    return {
        "schema": sp.SCHEMA,
        "source_commit": meta.get("source_commit", "unknown"),
        "input_manifest_sha256": meta.get("input_manifest_sha256", ""),
        "code_state": meta.get("code_state", sr.code_state()),
        "context_phones": meta.get("context_phones"),
        "identity_ap_scale": meta.get("identity_ap_scale"),
        "pair_count": len(runs),
        "conditions": sorted(sp.CONDITIONS),
        "criteria": criteria.as_dict(),
        "genes": genes,
        "overall": sg.overall_verdict(genes, criteria),
        "reproducibility": reproducibility_records(runs),
        "out_of_scope_observations": [],
    }


def _fmt(v: Any) -> str:
    return "—" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))


def render_record(res: Dict[str, Any]) -> str:
    ov = res["overall"]
    lines: List[str] = []
    lines.append("# S3 RECORD — Performance Gene Isolation & Independent Transplant")
    lines.append("")
    lines.append(f"- schema: `{res['schema']}`")
    cs = res.get("code_state") or {}
    dirty = cs.get("dirty")
    if dirty is True:
        code_note = f" **(worktree dirty — 差分ダイジェスト `{cs.get('dirty_digest', '')[:16]}`)**"
    elif dirty is None:
        code_note = " *(git 状態を確認できず、clean かどうか不明)*"
    else:
        code_note = " (clean worktree)"
    lines.append(f"- source_commit: `{res['source_commit']}`{code_note}")
    lines.append(f"- input_manifest_sha256: `{res['input_manifest_sha256']}`")
    lines.append(f"- pairs: {res['pair_count']} / conditions: {', '.join(res['conditions'])}")
    lines.append(f"- context_phones: {res.get('context_phones')} / "
                 f"identity_ap_scale: {res.get('identity_ap_scale')}")
    lines.append("")
    lines.append("## Overall")
    lines.append("")
    lines.append(f"**{ov['verdict']}** — supported_gene_count = {ov['supported_gene_count']} "
                 f"(必要 {res['criteria']['min_supported_genes']}): "
                 f"{', '.join(ov['supported_genes']) or 'なし'}")
    lines.append("")
    if ov["verdict"] == "PASS":
        lines.append("> **Genome Architecture S3 PASS — Performance の複数形質が、"
                     "複数 pair・複数文脈の実コーパス上で、独立操作可能かつ再現可能な "
                     "gene として部分分解できた。**")
    else:
        lines.append("> **Genome Architecture S3 NOT ESTABLISHED — S2 の Identity × "
                     "Performance 部分分離は維持するが、Performance 内部の複数 gene "
                     "分解は未成立。**")
    lines.append("")
    lines.append("どちらの場合も S2 の成功判定は変更しない。")
    lines.append("")
    lines.append("## Gene-Level")
    lines.append("")
    lines.append("| gene | verdict | evaluable | supported | ratio | eval ctx | sup ctx "
                 "| struct fail | det fail |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for g, v in res["genes"].items():
        lines.append(
            f"| {g} | {v['verdict']} | {v['evaluable_pairs']} | {v['supported_pairs']} "
            f"| {v['support_ratio']:.3f} | {v['distinct_evaluable_context_count']} "
            f"| {v['distinct_supported_context_count']} | {v['structural_failures']} "
            f"| {v['determinism_failures']} |")
    lines.append("")
    lines.append(f"閾値（事前登録・変更禁止）: {res['criteria']}")
    lines.append("")
    lines.append("## Pair-Level")
    lines.append("")
    for g, v in res["genes"].items():
        metric, direction = sp.GENE_METRIC[sp.Gene(g)]
        lines.append(f"### {g} — metric `{metric}` ({direction} is better)")
        lines.append("")
        lines.append("| pair | context | verdict | P1 | P2 amount | P3 B0 → gene | P4 |")
        lines.append("|---|---|---|---|---|---|---|")
        for pk, r in v["pairs"].items():
            d = r["detail"]
            amt = d["P2"].get("amount")
            unit = d["P2"].get("unit") or ""
            # 構造的に適用先が無い pair（note span 1 unit / terminal unit 無し）は
            # 移植量が非ゼロでも「適用できない」ので、量だけ見て誤読しないよう明示する。
            noop = " *(構造的 no-op)*" if d["P2"].get("structural_noop") else ""
            p3 = d["P3"]
            lines.append(
                f"| `{pk}` | {r['context_id']} | {r['verdict']} "
                f"| {'pass' if r['P1_structural_isolation'] else 'FAIL'} "
                f"| {_fmt(amt)} {unit}{noop} "
                f"| {_fmt(p3.get('b0'))} → {_fmt(p3.get('gene_condition'))} "
                f"| {'pass' if r['P4_determinism'] else 'FAIL'} |")
        lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- WAV 本体は commit しない（`results/.gitignore`）。SHA のみ記録する。")
    lines.append("- 本記録は S3 の契約（設計書 v1.1）だけを対象とし、"
                 "範囲外の品質問題は修正も測定もしていない。")
    lines.append("")
    return "\n".join(lines)


def render_only() -> int:
    """既存の `s3_results.json` から `S3_RECORD.md` だけを作り直す。

    判定値は一切再計算しない（読み方の表示を直すためだけの経路）。
    """
    res = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    if res.get("status") == "BLOCKED":
        print("BLOCKED な results からは記録を再描画しない")
        return 3
    publish_bundle(((RECORD_PATH, render_record(res)),))
    print(f"re-rendered {RECORD_PATH}")
    return 0 if res["overall"]["verdict"] == "PASS" else 1


def main(*, write_wav: bool = True) -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    try:
        runs, meta = sr.run_all(write_wav=write_wav)
        cross = sr.cross_process_shas()
    except sr.S3Stop as stop:
        publish_bundle((
            (JSON_PATH, _dumps({"schema": sp.SCHEMA, **stop.as_dict()})),
            (RECORD_PATH,
             "# S3 RECORD — BLOCKED\n\n"
             f"- 原因: {stop.cause}\n- 影響: {stop.impact}\n"
             f"- 最小修正案: {stop.minimal_fix}\n\n"
             "修正実装は行わない（設計書 §20）。\n"),
        ))
        print("BLOCKED:", stop.cause)
        return 3
    res = build_results(runs, meta, cross)
    publish_bundle(((JSON_PATH, _dumps(res)), (RECORD_PATH, render_record(res))))
    ov = res["overall"]
    print(f"S3 {ov['verdict']}: supported_gene_count={ov['supported_gene_count']} "
          f"({', '.join(ov['supported_genes']) or 'なし'})")
    for g, v in res["genes"].items():
        print(f"  {g:9s} {v['verdict']:14s} "
              f"evaluable={v['evaluable_pairs']} supported={v['supported_pairs']} "
              f"ratio={v['support_ratio']:.3f} "
              f"ctx={v['distinct_evaluable_context_count']}/"
              f"{v['distinct_supported_context_count']} "
              f"struct_fail={v['structural_failures']} det_fail={v['determinism_failures']}")
    return 0 if ov["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(render_only() if "--render-only" in sys.argv else main())
