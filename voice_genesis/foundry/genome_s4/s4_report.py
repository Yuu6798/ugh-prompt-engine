"""genome_s4/s4_report.py — 記録の生成と**原子的公開**（設計書 §17 / §20〜§22）。

出力:

- `results/s4_results.json`  … §21 のスキーマ
- `results/S4_RECORD.md`     … 人間可読の要約
- `results/ear_pack/`, `results/blind_manifest.json`, `results/answer_key.private.json`
  … 機械 Overall PASS のときだけ（§13）
- `results/key_reveal.json`  … 回答凍結後だけ（§14）
- `results/GENOME_ARCHITECTURE_V0_1_FREEZE.{json,md}` … S4 PASS のときだけ（§17）

**判定はここで行わない。** `s4_gates` の返り値を並べるだけ。
WAV / private key / answers は git 管理外（`results/.gitignore`）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_HERE = Path(__file__).resolve().parent
_FOUNDRY = _HERE.parent
for _p in (_HERE, _FOUNDRY / "genome_s3", _FOUNDRY / "planb", _FOUNDRY / "planb_real"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import s4_blind as sb  # noqa: E402
import s4_gates as sg  # noqa: E402
import s4_runner as sr  # noqa: E402
import s4_spec as sp  # noqa: E402
from s4_runner import S4Stop  # noqa: E402

RESULTS = _HERE / "results"
JSON_PATH = RESULTS / "s4_results.json"
RECORD_PATH = RESULTS / "S4_RECORD.md"
FREEZE_JSON = RESULTS / "GENOME_ARCHITECTURE_V0_1_FREEZE.json"
FREEZE_MD = RESULTS / "GENOME_ARCHITECTURE_V0_1_FREEZE.md"

#: §2「異常を見つけた場合は observed_but_out_of_scope として記録だけ行う」。
OUT_OF_SCOPE_OBSERVATIONS: Tuple[str, ...] = (
    "ABX の X は A か B と byte-identical なので、聴取者が 3 ファイルを sha256 で"
    "突き合わせれば聴かずに正答できる。commitment 方式が守るのは「実験者が回答後に"
    "正解を変えないこと」であって聴取者の自己申告ではない（S3.5 と同じ既知の性質）。"
    "プロトコル変更は §24 で禁じられているため実装では手を付けず、記録にのみ残す。",
    "ABX 4 問の偶然一致確率は 1/16。本 Gate は統計的有意差ではなく"
    "工学的進行 Gate である（設計書 §15.1 が明記）。",
)


def _cell(value: Any) -> str:
    """Markdown 表のセル。pair_key は `|` を含むので列区切りと衝突させない。"""
    return str(value).replace("|", "\\|")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False, sort_keys=False)


# ---------------------------------------------------------------------------
# §22 原子的公開
# ---------------------------------------------------------------------------
def publish(files: Sequence[Tuple[Path, bytes]] = (),
            dir_swaps: Sequence[Tuple[Path, Path]] = (),
            removals: Sequence[Path] = (),
            secret: Sequence[Path] = ()) -> None:
    """ファイル群とディレクトリ群を **1 つの最終 transaction** で公開する。

    JSON だけ新しく WAV が古い（またはその逆）を許さない（§22）。

    1. 全ファイルを temp へ書き切る。書き込み中に落ちたら temp を捨てて既存を残す
    2. ディレクトリを swap し、旧版を `.prev` に退避する
    3. ファイルを rename する
    4. どこかで落ちたら **既に置き換えた分をすべて巻き戻す**
    """
    secret_set = {Path(p) for p in secret}
    staged: List[Tuple[Path, Path]] = []
    try:
        for path, data in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            if path in secret_set:
                os.chmod(tmp, 0o600)      # 公開先へ出る前に権限を落とす
            staged.append((tmp, path))
    except BaseException:
        for tmp, _dest in staged:
            tmp.unlink(missing_ok=True)
        raise

    previous: Dict[Path, Optional[bytes]] = {
        dest: (dest.read_bytes() if dest.exists() else None) for _tmp, dest in staged}
    for path in removals:
        previous.setdefault(path, path.read_bytes() if path.exists() else None)
    swapped: List[Tuple[Path, Optional[Path]]] = []
    done: List[Path] = []
    removed: List[Path] = []
    try:
        for staging, dest in dir_swaps:
            backup = sr.publish_wav(staging, dest)
            swapped.append((dest, backup))
        for tmp, dest in staged:
            os.replace(tmp, dest)
            done.append(dest)
        for path in removals:
            if path.exists():
                path.unlink()
                removed.append(path)
    except BaseException:
        for path in removed:
            old = previous.get(path)
            if old is not None:
                path.write_bytes(old)
        for dest in done:
            old = previous[dest]
            if old is None:
                dest.unlink(missing_ok=True)
            else:
                dest.write_bytes(old)
        for dest, backup in swapped:
            sr.rollback_wav(dest, backup)
        for tmp, _dest in staged:
            tmp.unlink(missing_ok=True)
        raise
    for _dest, backup in swapped:
        sr.drop_backup(backup)


def blocked_bundle(stop: S4Stop) -> Tuple[Tuple[Path, bytes], ...]:
    pre = sr.preflight_record()
    body = {"schema": sp.SCHEMA, **stop.as_dict(), "preflight": pre}
    lines = ["# S4 RECORD — " + stop.status, "",
             f"- 原因: {stop.cause}", f"- 影響: {stop.impact}",
             f"- 最小修正案: {stop.minimal_fix}", "",
             "S4 の結果は出さない。修正実装は行わない（設計書 §24）。", "",
             "S2 PASS / S3 PASS / S3.5 の結果は変更しない（設計書 §16）。", ""]
    if pre:
        lines += ["## 停止までに通過した Gate", "",
                  "判定には使わない。**どこまで進んで何で止まったか**を記録だけで"
                  "追えるようにするための証拠。", "",
                  "| 項目 | 値 |", "|---|---|"]
        for k, v in pre.items():
            if isinstance(v, list):
                v = ", ".join(f"`{_cell(x)}`" for x in v) or "—"
            else:
                v = _cell(v)
            lines.append(f"| {k} | {v} |")
        lines.append("")
    md = "\n".join(lines)
    return ((JSON_PATH, _dumps(body).encode("utf-8")),
            (RECORD_PATH, md.encode("utf-8")))


# ---------------------------------------------------------------------------
# §21 出力 schema
# ---------------------------------------------------------------------------
def build_results(meta: Dict[str, Any], mech: Dict[str, Any],
                  perceptual: Optional[Dict[str, Any]] = None,
                  overall_verdict: Optional[str] = None) -> Dict[str, Any]:
    per = perceptual or {"abx_correct": 0, "abx_total": sp.ABX_TOTAL,
                         "identity_yes": 0, "identity_total": sp.IDENTITY_TOTAL,
                         "verdict": "BLOCKED"}
    verdict = overall_verdict or sg.s4_overall(
        mech["verdict"], None, None, hard_failure=mech.get("hard_failure", False))
    return {
        "schema": sp.SCHEMA,
        "s3_results_sha256": meta["s3_results_sha256"],
        "s35_results_sha256": meta["s35_results_sha256"],
        "input_manifest_sha256": meta["input_manifest_sha256"],
        "code_state": meta["code_state"],
        "context_phones": meta.get("context_phones"),
        "identity_ap_scale": meta.get("identity_ap_scale"),
        "candidate_pairs": mech["candidate_pairs"],
        "candidate_pair_keys": [c["pair_key"] for c in meta.get("candidate_pairs", [])],
        "conditions": list(sp.CONDITIONS),
        "mechanistic": mech,
        "perceptual": per,
        "overall": {"verdict": verdict},
        "out_of_scope_observations": list(OUT_OF_SCOPE_OBSERVATIONS),
    }


def _fmt(v: Any) -> str:
    return "—" if v is None else (f"{v:+.3f}" if isinstance(v, float) else str(v))


def render_record(res: Dict[str, Any]) -> str:
    mech = res["mechanistic"]
    per = res["perceptual"]
    ov = res["overall"]["verdict"]
    cs = res.get("code_state") or {}
    lines: List[str] = []
    lines.append("# S4 RECORD — Multi-Gene Co-expression & Retention PoC")
    lines.append("")
    lines.append(f"- schema: `{res['schema']}`")
    lines.append(f"- s3_results_sha256: `{res['s3_results_sha256']}`")
    lines.append(f"- s35_results_sha256: `{res['s35_results_sha256']}`")
    lines.append(f"- input_manifest_sha256: `{res['input_manifest_sha256']}`")
    lines.append(f"- commit: `{cs.get('commit', 'unknown')}` "
                 f"(clean worktree: {(cs.get('worktree') or {}).get('clean')})")
    closure = cs.get("closure") or {}
    lines.append(f"- closure digest: `{closure.get('digest', '')}` "
                 f"({closure.get('file_count', 0)} files)")
    lines.append(f"- conditions: {', '.join(res['conditions'])} "
                 f"/ candidate pairs: {res['candidate_pairs']}")
    lines.append("")
    lines.append("## Overall")
    lines.append("")
    lines.append(f"**{ov}**")
    lines.append("")
    if ov == "PASS":
        lines.append("> **Genome Architecture S4 PASS — Ritsu Identity 上で、F0 gene と "
                     "Duration gene を同時に発現させても、両 gene の増分効果・帰属・"
                     "知覚・再現性が保持された。**")
    elif ov == "NOT_ESTABLISHED":
        lines.append("> **S4 NOT ESTABLISHED — gene 単独成立は維持されるが、複合発現時の"
                     "知覚または Identity 保持を本条件では確認できなかった。**")
    elif ov == "FAILED":
        lines.append("> **S4 FAILED — 構造分離・決定論・入力 pin・S3 再現のいずれかに"
                     "違反した。**")
    else:
        lines.append("> **S4 BLOCKED — 入力・素材・正本が不足し、判定を実行できない。**")
    lines.append("")
    lines.append("どの結果でも S2 PASS / S3 PASS / S3.5 の結果は変更しない（§16）。")
    lines.append("")
    lines.append("## Mechanistic Gate（§11）")
    lines.append("")
    lines.append(f"**{mech['verdict']}** — candidate {mech['candidate_pairs']} / "
                 f"evaluable {mech['evaluable_pairs']} / combinable "
                 f"{mech['combinable_pairs']} / ratio {mech['support_ratio']:.3f}")
    lines.append("")
    lines.append("| check | 結果 |")
    lines.append("|---|---|")
    for k, v in mech["checks"].items():
        lines.append(f"| {k} | {'pass' if v else 'FAIL'} |")
    lines.append("")
    lines.append(f"閾値（事前登録・変更禁止 §24）: {mech['criteria']}")
    lines.append("")
    lines.append(f"contexts: {', '.join(mech['contexts']) or 'なし'} / "
                 f"supported contexts: {', '.join(mech['supported_contexts']) or 'なし'}")
    lines.append("")
    lines.append("## Pair-Level（§9 / §10）")
    lines.append("")
    lines.append("| pair | context | verdict | F0 alone | F0 with D | F0 retention "
                 "| Dur alone | Dur with F | Dur retention | FD distinct | id margin |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for pk, r in mech["pairs"].items():
        c = r["combination"]
        f0, du = c["genes"]["f0"], c["genes"]["duration"]
        lines.append(
            f"| `{pk}` | {r['context_id']} | {r['verdict']} "
            f"| {_fmt(f0['alone']['effect'])} | {_fmt(f0['with_background']['effect'])} "
            f"| {_fmt(f0['retention'])} "
            f"| {_fmt(du['alone']['effect'])} | {_fmt(du['with_background']['effect'])} "
            f"| {_fmt(du['retention'])} "
            f"| {'yes' if c['distinctness']['pass'] else 'NO'} "
            f"| {_fmt(c['identity']['fd_value'])} |")
    lines.append("")
    lines.append("- 効果量は全て **lower-is-better metric の差**（正 = 改善）。"
                 "F0 = `f0_dev_rmse_cents`、Duration = `note_split_mae_ms`。")
    lines.append("- retention は **診断値**であり Gate に使わない（§9.3）。"
                 "`>1` 相乗 / `0〜1` 弱まるが残る / `<=0` 消失または逆転。")
    lines.append("")
    lines.append("## Perceptual Gate（§13〜§15）")
    lines.append("")
    lines.append(f"**{per['verdict']}** — ABX {per['abx_correct']}/{per['abx_total']} 正解 "
                 f"/ Identity {per['identity_yes']}/{per['identity_total']} YES")
    if per.get("abx_verdict"):
        lines.append("")
        lines.append(f"- gene retention: {per['abx_verdict']}")
        lines.append(f"- identity: {per['identity_verdict']}")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- WAV / private key / answers は commit しない（`results/.gitignore`）。")
    lines.append("- 本記録は S4 の契約（設計書 v1.0）だけを対象とし、"
                 "範囲外の品質問題は修正も測定もしていない（§2）。")
    lines.append("")
    lines.append("### observed_but_out_of_scope")
    lines.append("")
    for obs in res.get("out_of_scope_observations", []):
        lines.append(f"- {obs}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# §17 凍結成果物（S4 PASS のときだけ）
# ---------------------------------------------------------------------------
def freeze_payload() -> Dict[str, Any]:
    return {
        "schema": sp.FREEZE_SCHEMA,
        "identity": {"source": "ritsu", "preserved_in_s2": True, "preserved_in_s4": True},
        "performance_genes": {
            "f0": {"mechanistic": True, "perceptual": True, "combinable": True},
            "duration": {"mechanistic": True, "perceptual": True, "combinable": True},
            "energy": {"mechanistic": True, "perceptual": "unestablished",
                       "combinable": "untested"},
            "release": {"mechanistic": True, "perceptual": "unestablished",
                        "combinable": "untested", "scope": "partial_terminal_taper"},
        },
        "scope": {
            "identity_donors_tested": ["ritsu"],
            "performance_donors_tested": ["pjs"],
            "cross_donor_crossover_tested": False,
            "learning_tested": False,
            "education_tested": False,
            "quality_improvement_tested": False,
        },
    }


def render_freeze(payload: Dict[str, Any], res: Dict[str, Any]) -> str:
    lines = ["# Genome Architecture v0.1 — FREEZE", "",
             "> **VoiceGenesis Genome Architecture PoC v0.1 COMPLETE**", "",
             "確認済み範囲:", "",
             "```text",
             "S1: 実コーパスで動く",
             "S2: Identity + Performance 部分分解",
             "S3: Performance gene 部分分解",
             "S3.5: F0 / Duration 差が人間にも届く",
             "S4: F0 + Duration の複合発現でも両 gene と Identity を保持",
             "```", "",
             "この時点で S 系列を凍結する。次の作業（VoiceGenesis 本体への "
             "Genome Architecture v0.1 統合）は別計画であり、S4 実装者は着手しない"
             "（設計書 §26）。", "",
             f"- s4_results.json sha 連結: s3=`{res['s3_results_sha256'][:16]}…` "
             f"/ s35=`{res['s35_results_sha256'][:16]}…`", "",
             "## payload", "", "```json", _dumps(payload), "```", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# §20 Phase A — Mechanistic
# ---------------------------------------------------------------------------
def phase_a(*, write_wav: bool = True, require_clean: bool = True) -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    backup: Optional[Path] = None
    try:
        runs, meta, backup = sr.run_all(write_wav=write_wav, require_clean=require_clean)
        cross = sr.cross_process_shas()
        results = [sg.pair_verdict(r, cross) for r in runs]
        mech = sg.overall_gate(results)
        res = build_results(meta, mech)
        files = [(JSON_PATH, _dumps(res).encode("utf-8")),
                 (RECORD_PATH, render_record(res).encode("utf-8"))]
        dir_swaps: List[Tuple[Path, Path]] = []
        if mech["verdict"] == "PASS":
            files += _stage_ear_pack(meta, runs, dir_swaps)
        publish(files=files, dir_swaps=dir_swaps, secret=[sb.PRIVATE_KEY])
    except S4Stop as stop:
        sr.rollback_wav(sr.WAV_DIR, backup)
        publish(files=blocked_bundle(stop))
        print(f"{stop.status}: {stop.cause}")
        return 3
    except BaseException:
        sr.rollback_wav(sr.WAV_DIR, backup)
        raise
    sr.drop_backup(backup)
    print(f"S4 mechanistic {mech['verdict']}: "
          f"combinable={mech['combinable_pairs']}/{mech['evaluable_pairs']} "
          f"ratio={mech['support_ratio']:.3f} contexts={mech['supported_context_count']}")
    for pk, r in mech["pairs"].items():
        print(f"  {r['verdict']:14s} {pk}")
    if mech["verdict"] == "PASS":
        print("READY_FOR_LISTENING — results/ear_pack/ の 6 問に回答して "
              "results/answers.json を置き、`python s4_report.py --phase-c` を実行する")
        return 0
    print("機械 Gate 不通過のため耳 pack は作らない（§13）")
    return 1


def _stage_ear_pack(meta: Dict[str, Any], runs: Sequence[sr.PairRun],
                    dir_swaps: List[Tuple[Path, Path]]) -> List[Tuple[Path, bytes]]:
    """機械 PASS 後の耳 pack を staging に作り、最終 transaction へ載せる（§13 / §22）。"""
    cands = [(c["pair_key"], c["context_id"]) for c in meta["candidate_pairs"]]
    selected = sb.select_pairs(cands, meta["s3_results_sha256"],
                               meta["s35_results_sha256"])
    salt = os.urandom(32)
    trials = sb.build_trials(selected, salt)
    # `run_all` が staging を既に公開済みなので、参照先は公開後の WAV_DIR。
    resolved: Dict[Tuple[str, str], Tuple[Path, str]] = {}
    for run in runs:
        for cond, co in run.conditions.items():
            resolved[(run.pair_key, cond)] = (Path(co.wav_path), co.wav_sha256)
    staging = sb.EAR_AUDIO.with_name(sb.EAR_AUDIO.name + ".staging")
    audio_sha = sb.materialize(trials, resolved, staging)
    dir_swaps.append((staging, sb.EAR_AUDIO))
    _key, key_raw = sb.build_private_key(trials, salt, meta["s3_results_sha256"],
                                         meta["s35_results_sha256"], selected)
    # private key も **同じ transaction** で置く。先に書くと、後段が失敗したとき
    # 「新しい key + 古い manifest」が残り commitment 検証が通らなくなる。
    commitment = sb.sha256_bytes(key_raw)
    manifest = sb.build_blind_manifest(trials, audio_sha, meta["s3_results_sha256"],
                                       meta["s35_results_sha256"], commitment)
    return [(sb.PRIVATE_KEY, key_raw),
            (sb.BLIND_MANIFEST, _dumps(manifest).encode("utf-8")),
            (sb.ANSWERS.with_name("answers.template.json"),
             _dumps(sb.answers_template(trials)).encode("utf-8"))]


# ---------------------------------------------------------------------------
# §20 Phase C — Final
# ---------------------------------------------------------------------------
def phase_c() -> int:
    try:
        res_raw = JSON_PATH.read_bytes() if JSON_PATH.exists() else b""
        res = json.loads(res_raw.decode("utf-8")) if res_raw else {}
        if not isinstance(res, dict) or res.get("status") == "BLOCKED":
            raise S4Stop(cause="Phase A の機械 PASS 記録が無い",
                         impact="人間 Gate を採点する前提が無い",
                         minimal_fix="先に Phase A を実行して機械 PASS を得る")
        mech = res.get("mechanistic") or {}
        if mech.get("verdict") != "PASS":
            raise S4Stop(
                cause=f"機械 Overall が {mech.get('verdict')!r} なので耳判定へ進めない",
                impact="機械 FAIL / BLOCKED では耳 pack を作らない（§13）",
                minimal_fix="機械 Gate の不通過理由を User 裁定へ戻す")
        if not sb.PRIVATE_KEY.exists() or not sb.BLIND_MANIFEST.exists():
            raise S4Stop(cause="blind manifest / private key が無い",
                         impact="commitment を検証できず、採点結果を信用できない",
                         minimal_fix="Phase A を実行して耳 pack を作る")
        key_raw = sb.PRIVATE_KEY.read_bytes()
        manifest = json.loads(sb.BLIND_MANIFEST.read_bytes().decode("utf-8"))
        if not sb.verify_commitment(key_raw, manifest):
            raise S4Stop(
                cause="answer_key の SHA が blind_manifest の key_commitment と一致しない",
                impact="回答後に正解が差し替えられていないことを保証できない（blind 破壊）",
                minimal_fix="耳 pack を作り直し、回答をやり直す")
        key = json.loads(key_raw.decode("utf-8"))
        trials = [dict(v, trial_id=k) for k, v in key["trials"].items()]
        answers, answers_sha = sb.load_answers()
        scored = sb.score(trials, answers)
        abx_v = sg.abx_verdict(scored["abx_correct"], scored["abx_total"])
        id_v = sg.identity_verdict(scored["identity_yes"], scored["identity_total"])
        per = {"abx_correct": scored["abx_correct"], "abx_total": scored["abx_total"],
               "identity_yes": scored["identity_yes"],
               "identity_total": scored["identity_total"],
               "abx_verdict": abx_v, "identity_verdict": id_v,
               "answers_sha256": answers_sha,
               "key_commitment": manifest["key_commitment"],
               "commitment_verified": True,
               "verdict": sg.perceptual_verdict(abx_v, id_v),
               "trials": scored["abx"] + scored["identity"]}
        overall = sg.s4_overall(mech["verdict"], abx_v, id_v,
                                hard_failure=mech.get("hard_failure", False))
        res["perceptual"] = per
        res["overall"] = {"verdict": overall}
        reveal = sb.build_key_reveal(key, answers_sha, scored)
        files = [(JSON_PATH, _dumps(res).encode("utf-8")),
                 (RECORD_PATH, render_record(res).encode("utf-8")),
                 (sb.KEY_REVEAL, _dumps(reveal).encode("utf-8"))]
        removals: List[Path] = []
        if overall == "PASS":
            payload = freeze_payload()
            files += [(FREEZE_JSON, _dumps(payload).encode("utf-8")),
                      (FREEZE_MD, render_freeze(payload, res).encode("utf-8"))]
        else:
            # PASS 以外で古い freeze を残すと「凍結済み」を偽って主張する。
            removals += [FREEZE_JSON, FREEZE_MD]
        publish(files=files, removals=removals)
    except S4Stop as stop:
        print(f"{stop.status}: {stop.cause}")
        return 3
    print(f"S4 {overall}: ABX {scored['abx_correct']}/{scored['abx_total']} "
          f"({abx_v}) / Identity {scored['identity_yes']}/{scored['identity_total']} ({id_v})")
    return 0 if overall == "PASS" else 1


def main(argv: Sequence[str]) -> int:
    if "--phase-c" in argv:
        return phase_c()
    return phase_a(write_wav="--no-wav" not in argv,
                   require_clean="--allow-dirty" not in argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
