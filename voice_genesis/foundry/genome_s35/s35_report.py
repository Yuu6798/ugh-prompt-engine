"""genome_s35/s35_report.py — Phase F（設計書 v1.0 §22, §23）。

生成:

- `results/s35_results.json`  … §23 の最小 schema
- `results/S3_5_RECORD.md`    … 人間可読

raw WAV は commit しない。private key は回答前に commit しない。
**判定はここで行わない**（`s35_score` の返り値を並べるだけ）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s35_prepare as prep  # noqa: E402
import s35_score as scoring  # noqa: E402
import s35_spec as sp  # noqa: E402

RESULTS = prep.RESULTS
JSON_PATH = RESULTS / "s35_results.json"
RECORD_PATH = RESULTS / "S3_5_RECORD.md"

#: §16「別の問題を見つけても記録だけにする」— 修正はしない。
OUT_OF_SCOPE_OBSERVATIONS: Tuple[str, ...] = (
    "X は A か B と byte-identical（§7）なので、聴取者が 3 ファイルを sha256 で"
    "突き合わせれば聴かずに正答できる。commitment 方式が守るのは"
    "「実験者が回答後に正解を変えないこと」であって聴取者の自己申告ではない。"
    "プロトコル変更は §12/§16 で禁止のため実装では手を付けず、記録にのみ残す。",
)


def publish_bundle(pairs: Tuple[Tuple[Path, str], ...]) -> None:
    """JSON と Markdown を 1 つの束として差し替える（S3 と同じ扱い）。"""
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
    previous: Dict[Path, Optional[bytes]] = {
        dest: (dest.read_bytes() if dest.exists() else None) for _tmp, dest in staged}
    done: List[Path] = []
    try:
        for tmp, dest in staged:
            os.replace(tmp, dest)
            done.append(dest)
    except BaseException:
        for dest in done:
            old = previous[dest]
            if old is None:
                dest.unlink(missing_ok=True)
            else:
                dest.write_bytes(old)
        for tmp, _dest in staged:
            tmp.unlink(missing_ok=True)
        raise
    for tmp, _dest in staged:
        tmp.unlink(missing_ok=True)


def build_results(scored: Dict[str, Any]) -> Dict[str, Any]:
    genes = {
        g: {"verdict": v["verdict"], "correct": v["correct"], "total": v["total"],
            "unsure": v["unsure"], "distinct_pairs": v["distinct_pairs"],
            "contexts": v["contexts"], "context_ids": v["context_ids"],
            "pair_keys": v["pair_keys"]}
        for g, v in scored["genes"].items()
    }
    return {
        "schema": sp.SCHEMA,
        "protocol_version": scored["protocol_version"],
        "s3_results_sha256": scored["s3_results_sha256"],
        "blind_manifest_sha256": scored["blind_manifest_sha256"],
        "answers_sha256": scored["answers_sha256"],
        "key_commitment": scored["key_commitment"],
        "key_reveal_sha256": scored["key_reveal_sha256"],
        "commitment_verified": scored["commitment_verified"],
        "audio_verified": scored["audio_verified"],
        "listener_count": 1,
        "listener_id": scored["listener_id"],
        "session_id": scored["session_id"],
        "protocol": sp.Protocol().as_dict(),
        "genes": genes,
        "overall": scored["overall"],
        "out_of_scope_observations": list(OUT_OF_SCOPE_OBSERVATIONS),
    }


def render_record(res: Dict[str, Any]) -> str:
    ov = res["overall"]
    L: List[str] = []
    L.append("# S3.5 RECORD — Perceptual Gene Gate")
    L.append("")
    L.append(f"- schema: `{res['schema']}` / protocol: `{res['protocol_version']}`")
    L.append(f"- s3_results_sha256: `{res['s3_results_sha256']}`")
    L.append(f"- blind_manifest_sha256: `{res['blind_manifest_sha256']}`")
    L.append(f"- answers_sha256: `{res['answers_sha256']}`")
    L.append(f"- key_commitment: `{res['key_commitment']}`")
    L.append(f"- key_reveal_sha256: `{res['key_reveal_sha256']}`")
    L.append(f"- commitment_verified: **{res['commitment_verified']}** / "
             f"audio_verified: **{res['audio_verified']}**")
    L.append(f"- listener: `{res['listener_id']}` / session: `{res['session_id']}`")
    L.append("")
    L.append("## Overall")
    L.append("")
    L.append(f"**{ov['verdict']}** — perceptible_gene_count = "
             f"{ov['perceptible_gene_count']}（必要 {sp.MIN_PERCEPTIBLE_GENES_FOR_S4}）: "
             f"{', '.join(ov['perceptible_genes']) or 'なし'}")
    L.append("")
    if ov["verdict"] == "S4_READY":
        L.append("> **S3.5 PASS / S4_READY — S3 で機械的に成立した Performance gene の"
                 "うち少なくとも 2 つについて、単独介入差を人間が事前登録 ABX 条件で"
                 "識別できた。**")
    else:
        L.append("> **S3.5 S4_NOT_READY — S3 の機械的 gene 分離は維持されるが、S4 へ"
                 "進むための知覚成立 gene 数が不足した。**")
    L.append("")
    L.append("`S4_NOT_READY` は S3 FAIL を意味しない（§14）。S3.5 は S3 を覆さない。")
    L.append("")
    L.append("## Gene-Level")
    L.append("")
    L.append("| gene | correct | verdict | distinct pairs | contexts | UNSURE |")
    L.append("|---|---|---|---|---|---|")
    for g, v in res["genes"].items():
        L.append(f"| {g} | {v['correct']}/{v['total']} | {v['verdict']} "
                 f"| {v['distinct_pairs']} | {v['contexts']} | {v['unsure']} |")
    L.append("")
    L.append(f"事前登録 Gate: `correct >= {sp.PERCEPTIBLE_CORRECT} / "
             f"{sp.TRIALS_PER_GENE}` で PERCEPTIBLE。`UNSURE` は正答に数えない。"
             f"ランダム回答での到達確率は 9/256 ≈ 3.52%（§13/§15）。")
    L.append("")
    L.append("## Selected pairs")
    L.append("")
    for g, v in res["genes"].items():
        L.append(f"### {g} — contexts: {', '.join(v['context_ids'])}")
        L.append("")
        for pk in v["pair_keys"]:
            L.append(f"- `{pk}`")
        L.append("")
    L.append("## §17 S4 へ渡すもの")
    L.append("")
    L.append("`S3 SUPPORTED` かつ `S3.5 PERCEPTIBLE` の gene。"
             "`NOT_ESTABLISHED` の gene は削除せず、"
             "`mechanistically_supported = true` / `perceptually_established = false` "
             "として保持する。")
    L.append("")
    for g, v in res["genes"].items():
        est = v["verdict"] == sp.GeneVerdict.PERCEPTIBLE.value
        L.append(f"- `{g}`: mechanistically_supported = true / "
                 f"perceptually_established = {str(est).lower()}")
    L.append("")
    L.append("## §27 主張禁止")
    L.append("")
    L.append("S3.5 PASS でも次は言わない: gene を人間が意味分類できる / 4 gene が知覚上"
             "完全独立 / 自然 / 高品質 / 改善 / 歌唱技能を獲得 / Genome Architecture 完成。")
    L.append("")
    L.append("## Out-of-scope observations（記録のみ・修正しない）")
    L.append("")
    for o in res["out_of_scope_observations"]:
        L.append(f"- {o}")
    L.append("")
    L.append("## Notes")
    L.append("")
    L.append("- raw WAV は commit しない（`results/.gitignore`）。"
             "波音リツ 歌声データベース利用規約 第3条1（転載禁止）にも該当。")
    L.append("- `answer_key.private.json` は回答前に commit しない（§22）。")
    L.append("- 再試験禁止（§16）: 結果を見たあとの pair 入れ替え・dose 変更・"
             "音量補正・再生回数増・6/8 への緩和は禁止。必要なら `S3.5-v2` を"
             "新規事前登録して別実験として行い、v1 結果は残す。")
    L.append("")
    return "\n".join(L)


def main(answers_path: Optional[Path] = None) -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    scored, blocked = scoring.scored_or_blocked(answers_path)
    if blocked is not None:
        publish_bundle((
            (JSON_PATH, prep._dumps({"schema": sp.SCHEMA, **blocked})),
            (RECORD_PATH,
             "# S3.5 RECORD — BLOCKED\n\n"
             f"- reason: {blocked['reason']}\n"
             f"- affected_gene: {blocked['affected_gene']}\n"
             f"- required_action: {blocked['required_action']}\n\n"
             "合理的推測で突破しない（§25）。\n"),
        ))
        print("BLOCKED:", blocked["reason"])
        return 3
    assert scored is not None
    res = build_results(scored)
    publish_bundle(((JSON_PATH, prep._dumps(res)), (RECORD_PATH, render_record(res))))

    ov = res["overall"]
    print(f"{'gene':11} {'correct':9} verdict")
    for g, v in res["genes"].items():
        print(f"{g:11} {v['correct']}/{v['total']:<7} {v['verdict']}")
    print()
    print(f"perceptible_gene_count = {ov['perceptible_gene_count']}")
    print(f"S4 gate = {ov['verdict']}")
    return 0 if ov["verdict"] == "S4_READY" else 1


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else scoring.ANSWERS
    raise SystemExit(main(p))
