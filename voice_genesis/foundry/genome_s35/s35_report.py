"""genome_s35/s35_report.py — 最終記録。

生成:

- `results/s35_results.json`
- `results/S3_5_RECORD.md`

raw WAV は commit しない。private key は commit しない。
**判定はここで行わない**（`s35_score.finalize` の返り値を並べるだけ）。
"""
from __future__ import annotations

import json
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
#: 確定済み記録を潰さずに停止理由を残す先。
LAST_ERROR = RESULTS / "last_error.json"

#: 別の問題を見つけても修正しない — 記録だけ残す。
OUT_OF_SCOPE_OBSERVATIONS: Tuple[str, ...] = (
    "X は A か B と byte-identical なので、聴取者が 3 ファイルを sha256 で"
    "突き合わせれば聴かずに正答できる。commitment 方式が守るのは"
    "「実験者が回答後に正解を変えないこと」であって聴取者の自己申告ではない。"
    "プロトコル変更は範囲外のため実装では手を付けず、記録にのみ残す。",
    "1 gene あたり 2 問なので、偶然の一致でも 1/4 で PERCEPTIBLE_CANDIDATE に"
    "到達する。本 Gate は統計的証明ではなく、S4 へ進むための最小確認である。",
)


def publish_bundle(pairs: Tuple[Tuple[Path, str], ...]) -> None:
    """JSON と Markdown を 1 つの束として差し替える。"""
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
    """記録本体を組む。`_` 始まりの内部フィールドは出力へ含めない。"""
    genes = {}
    for g, v in scored["genes"].items():
        s1, s2 = v["stage1"], v["stage2"]
        genes[g] = {
            "verdict": v["verdict"],
            "stage1_correct": None if s1 is None else s1["correct"],
            "stage2_correct": None if s2 is None else s2["correct"],
            "stage1_answer": None if s1 is None else s1["answer"],
            "stage2_answer": None if s2 is None else s2["answer"],
            "stage1_pair": None if s1 is None else s1["pair_key"],
            # 出題した pair と、事前 commit 済みだが出題しなかった pair を区別する
            "stage2_presented_pair": None if s2 is None else s2["pair_key"],
            "stage2_committed_pair": (v["stage2_committed"] or {}).get("pair_key"),
            "stage2_presented": v["stage2_presented"],
            "contexts": v["contexts"],
            "has_stage2_pair": v["has_stage2_pair"],
            "not_evaluable_reason": v["not_evaluable_reason"],
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
        "advancing_genes": scored["advancing_genes"],
        "genes": genes,
        "overall": scored["overall"],
        "out_of_scope_observations": list(OUT_OF_SCOPE_OBSERVATIONS),
    }


def _mark(v: Optional[bool]) -> str:
    return "—" if v is None else ("正解" if v else "不正解")


def render_record(res: Dict[str, Any]) -> str:
    ov = res["overall"]
    L: List[str] = []
    L.append("# S3.5 RECORD — Perceptual Gene Gate（人間負担軽量版）")
    L.append("")
    L.append(f"- schema: `{res['schema']}` / protocol: `{res['protocol_version']}`")
    L.append(f"- s3_results_sha256: `{res['s3_results_sha256']}`")
    L.append(f"- blind_manifest_sha256: `{res['blind_manifest_sha256']}`")
    for st, sha in res["answers_sha256"].items():
        L.append(f"- answers_sha256 (stage {st}): `{sha}`")
    L.append(f"- key_commitment: `{res['key_commitment']}`")
    L.append(f"- key_reveal_sha256: `{res['key_reveal_sha256']}`")
    L.append(f"- commitment_verified: **{res['commitment_verified']}** / "
             f"audio_verified: **{res['audio_verified']}**")
    L.append(f"- listener: `{res['listener_id']}` / session: `{res['session_id']}`")
    L.append("")
    L.append("## Overall")
    L.append("")
    L.append(f"**{ov['verdict']}** — perceptible_candidate_count = "
             f"{ov['perceptible_candidate_count']}"
             f"（必要 {sp.MIN_PERCEPTIBLE_GENES_FOR_S4}）: "
             f"{', '.join(ov['perceptible_candidates']) or 'なし'}")
    L.append("")
    if ov["verdict"] == "S4_READY":
        L.append("> **S4_READY — 少なくとも 2 つの Performance gene について、"
                 "異なる 2 文脈で単独介入差を人間が識別できた。**")
    else:
        L.append("> **S4_NOT_READY — S3 の機械的 gene 分離は維持されるが、"
                 "S4 へ進むための知覚成立 gene 数が不足した。**")
    L.append("")
    L.append("`S4_NOT_READY` は S3 FAIL を意味しない。S3.5 は S3 を覆さない。")
    L.append("")
    L.append("**統計的有意差・知覚閾値・完全独立は主張しない。**")
    L.append("")
    L.append("## Gene-Level")
    L.append("")
    L.append("| gene | Stage 1 | Stage 2 | contexts | verdict |")
    L.append("|---|---|---|---|---|")
    for g, v in res["genes"].items():
        L.append(f"| {g} | {_mark(v['stage1_correct'])} | {_mark(v['stage2_correct'])} "
                 f"| {', '.join(v['contexts']) or '—'} | {v['verdict']} |")
    L.append("")
    L.append("判定規則: Stage 1 正解 **かつ** Stage 2 正解 → `PERCEPTIBLE_CANDIDATE` / "
             "どちらか不正解または `UNSURE` → `NOT_ESTABLISHED` / "
             "Stage 2 用の別 context が無い → `NOT_EVALUABLE_S35`。")
    L.append("")
    L.append("## Selected pairs（決定論選択・人間は選んでいない）")
    L.append("")
    for g, v in res["genes"].items():
        L.append(f"### {g}")
        L.append("")
        L.append(f"- Stage 1: `{v['stage1_pair'] or '—'}`")
        committed = v["stage2_committed_pair"]
        if v["stage2_presented"]:
            L.append(f"- Stage 2（出題）: `{v['stage2_presented_pair']}`")
        elif committed:
            L.append(f"- Stage 2（事前 commit 済み・**出題せず**）: `{committed}`")
        else:
            L.append("- Stage 2: — （別 context が無く選べなかった）")
        if v["not_evaluable_reason"]:
            L.append(f"- 備考: {v['not_evaluable_reason']}")
        L.append("")
    L.append("## S4 へ渡すもの")
    L.append("")
    L.append("`S3 SUPPORTED` かつ `S3.5 PERCEPTIBLE_CANDIDATE` の gene。"
             "`NOT_ESTABLISHED` の gene は削除せず保持する。")
    L.append("")
    for g, v in res["genes"].items():
        est = v["verdict"] == sp.GeneVerdict.PERCEPTIBLE_CANDIDATE.value
        L.append(f"- `{g}`: mechanistically_supported = true / "
                 f"perceptually_established = {str(est).lower()}")
    L.append("")
    L.append("## 主張禁止")
    L.append("")
    L.append("S4_READY でも次は言わない: gene を人間が意味分類できる / gene が知覚上"
             "完全独立 / 自然 / 高品質 / 改善 / 歌唱技能を獲得 / "
             "Genome Architecture 完成 / 統計的有意 / 知覚閾値を測った。")
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
    L.append("- `answer_key.private.json` は commit しない。")
    L.append("- 音源は S3 canonical WAV の byte copy のみ。再生成・normalize・gain・"
             "trim・fade・denoise・resample はしていない（SHA 一致を検査済み）。")
    L.append("- 結果を見たあとの pair 入れ替え・閾値緩和は禁止。必要なら新規事前登録の"
             "別実験として行い、本結果は残す。")
    L.append("")
    return "\n".join(L)


def load_finalized() -> Optional[Dict[str, Any]]:
    """既に確定済みの記録（BLOCKED でない s35_results.json）を返す。無ければ None。"""
    if not JSON_PATH.exists():
        return None
    try:
        doc = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if doc.get("status") == "BLOCKED" or "overall" not in doc:
        return None
    return doc


def existing_finalized() -> bool:
    return load_finalized() is not None


#: 確定記録の同一性を決めるフィールド。**判定だけでなく来歴も含める。**
#: 判定が同じでも来歴（どの S3 正本・どのプロトコル・どの manifest で出たか）が
#: 違えば、それは別の実験の結果であって同じ記録ではない。
_FINGERPRINT_FIELDS = (
    "overall", "answers_sha256", "key_commitment", "key_reveal_sha256",
    "s3_results_sha256", "protocol_version", "blind_manifest_sha256",
    "commitment_verified", "audio_verified", "listener_id", "session_id",
)


def _verdict_fingerprint(doc: Dict[str, Any]) -> Dict[str, Any]:
    """記録の**判定 + 来歴**。フィールドが増えただけでは変わらない部分を取る。"""
    fp: Dict[str, Any] = {k: doc.get(k) for k in _FINGERPRINT_FIELDS}
    fp["genes"] = {g: v.get("verdict") for g, v in (doc.get("genes") or {}).items()}
    return fp


def conflicting_with_finalized(res: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """確定済み記録と**判定が食い違う**なら、その差分を返す。

    音声の欠落・改変は `verify_stage_audio` が例外ではなく `False` を返すので、
    `finalize()` は「全 gene INVALID」で**成功**してしまう。そのまま publish すると
    確定済みの S4_READY が S4_NOT_READY で上書きされる（前巡のガードは BLOCKED
    経路しか塞いでいなかった）。フィールドが増えただけの再描画は通す。
    """
    prev = load_finalized()
    if prev is None:
        return None
    was, now = _verdict_fingerprint(prev), _verdict_fingerprint(res)
    if was == now:
        return None
    return {"was": was, "now": now}


def main(stage1_path: Optional[Path] = None, stage2_path: Optional[Path] = None) -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    scored, blocked = scoring.finalize_or_blocked(stage1_path, stage2_path)
    if blocked is not None:
        # **確定済みの記録を BLOCKED 文書で潰さない。** 開示後の再採点ガードが
        # 正しく発火しても、ここで上書きすると S4_READY の正本が消えてしまう
        # （ガードが守ったはずのものを、ガードの副作用で失う）。
        if existing_finalized():
            LAST_ERROR.write_text(prep._dumps({"schema": sp.SCHEMA, **blocked}),
                                  encoding="utf-8")
            print("BLOCKED:", blocked["reason"])
            print(f"確定済みの記録は保持した（詳細は {LAST_ERROR.name}）")
            return 3
        publish_bundle((
            (JSON_PATH, prep._dumps({"schema": sp.SCHEMA, **blocked})),
            (RECORD_PATH,
             "# S3.5 RECORD — BLOCKED\n\n"
             f"- reason: {blocked['reason']}\n"
             f"- affected_gene: {blocked['affected_gene']}\n"
             f"- required_action: {blocked['required_action']}\n\n"
             "合理的推測で突破しない。\n"),
        ))
        print("BLOCKED:", blocked["reason"])
        return 3
    assert scored is not None
    res = build_results(scored)
    conflict = conflicting_with_finalized(res)
    if conflict is not None:
        # 確定済み session の判定は不変。作り直すなら新規 session として実施する。
        LAST_ERROR.write_text(prep._dumps({
            "schema": sp.SCHEMA, "status": "BLOCKED",
            "reason": "確定済み記録と判定が食い違う再実行を拒否した",
            "affected_gene": None,
            "required_action": "確定済み session の結果はそのまま残す。"
                               "やり直すなら新規に事前登録した別 session として実施する",
            "conflict": conflict}), encoding="utf-8")
        print("BLOCKED: 確定済み記録と判定が食い違う再実行を拒否した")
        print(f"確定済みの記録は保持した（詳細は {LAST_ERROR.name}）")
        return 3
    # 開示文書も同じ束で publish する（ガードを通ってから初めてディスクへ出る）
    publish_bundle(((JSON_PATH, prep._dumps(res)),
                    (RECORD_PATH, render_record(res)),
                    (scoring.KEY_REVEAL, scored["_key_reveal_text"])))
    LAST_ERROR.unlink(missing_ok=True)      # 成功したら古いエラーを残さない

    ov = res["overall"]
    print(f"{'gene':11} {'stage1':9} {'stage2':9} verdict")
    for g, v in res["genes"].items():
        print(f"{g:11} {_mark(v['stage1_correct']):9} {_mark(v['stage2_correct']):9} "
              f"{v['verdict']}")
    print()
    print(f"perceptible_candidate_count = {ov['perceptible_candidate_count']}")
    print(f"S4 gate = {ov['verdict']}")
    return 0 if ov["verdict"] == "S4_READY" else 1


if __name__ == "__main__":
    a1 = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    a2 = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    raise SystemExit(main(a1, a2))
