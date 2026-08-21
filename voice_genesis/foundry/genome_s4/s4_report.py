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
import shutil
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
    "第 1 走行の実測: `note_split_mae_ms` は F0 トグルに対して**完全に不感**で、"
    "全 6 pair で metric(B0) == metric(F) かつ metric(D) == metric(FD) が"
    "厳密に成立した。したがって §9.2 の Duration 側増分 "
    "(metric(F) - metric(FD)) は §9.1 の単独増分 (metric(B0) - metric(D)) と"
    "数値的に同一で、duration_retention は構造上つねに 1.000 になる。"
    "つまり **Duration 軸の機械 Gate は「F0 背景でも残るか」を実質的に検定して"
    "いない**（合成経路上、F0 トグルは note の尺に触れないため）。"
    "F0 軸は同一でない（例: 75.400 -> 74.240, retention 0.985）ので、機械側で"
    "共発現を実測しているのは F0 軸のみ。Duration 軸の共発現は §13.2 の ABX が"
    "担う。metric の変更は §24 で禁止されているため実装では手を付けず記録に残す。",
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


#: S4b は S4 の結果に従属する（`s4_overall_unchanged` と S4 digest を pin する）。
#: Phase A が s4_results.json を差し替えたら、**どの verdict であっても**
#: S4b の判定成果物は古い S4 を指したままになるので同じ transaction で落とす。
S4B_DEPENDENT_ARTIFACTS: Tuple[Path, ...] = (
    RESULTS / "s4b" / "s4b_results.json",
    RESULTS / "s4b" / "S4B_RECORD.md",
    RESULTS / "s4b" / "key_reveal.json",
    RESULTS / "s4b" / "blind_manifest.json",
    RESULTS / "s4b" / "answer_key.private.json",
    RESULTS / "s4b" / "answers.template.json",
)

#: 走行結果に紐づく成果物。**Phase A が PASS 以外で終わったら残してはならない。**
#: 残すと「NOT_ESTABLISHED / BLOCKED の正本」と「S4 完了を主張する freeze・鍵・
#: pack」が同居し、記録が自分自身と矛盾する。
OUTCOME_ARTIFACTS: Tuple[Path, ...] = (
    FREEZE_JSON, FREEZE_MD, sb.KEY_REVEAL, sb.BLIND_MANIFEST, sb.PRIVATE_KEY,
    sb.ANSWERS.with_name("answers.template.json"))


def _rollback_wav_if_published(published: bool, backup: Optional[Path]) -> None:
    """**公開に到達した場合だけ**巻き戻す。

    `run_all` が publish_wav の前に止まったとき（素材欠落・pin 不一致・replay
    不一致など）は `backup` が None のままで、無条件に巻き戻すと
    `rmtree(WAV_DIR)` だけが走って**前回の正当な成果物を復元不能に破壊する**。
    """
    if published:
        sr.rollback_wav(sr.WAV_DIR, backup)


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
    # 機械 PASS 済みで耳判定が未了の BLOCKED は、§16 の「入力・素材・正本が不足」
    # とは別物。verdict 語彙は 4 状態のまま、待ち先だけを記録に足す。
    awaiting = ("perceptual_gate"
                if verdict == "BLOCKED" and mech.get("verdict") == "PASS" else None)
    return {
        "schema": sp.SCHEMA,
        "s3_results_sha256": meta["s3_results_sha256"],
        "s35_results_sha256": meta["s35_results_sha256"],
        "input_manifest_sha256": meta["input_manifest_sha256"],
        "code_state": meta["code_state"],
        "material_provenance": meta.get("material_provenance", {"relocated": False}),
        "context_phones": meta.get("context_phones"),
        "identity_ap_scale": meta.get("identity_ap_scale"),
        "candidate_pairs": mech["candidate_pairs"],
        "candidate_pair_keys": [c["pair_key"] for c in meta.get("candidate_pairs", [])],
        "conditions": list(sp.CONDITIONS),
        "mechanistic": mech,
        "perceptual": per,
        "overall": ({"verdict": verdict, "awaiting": awaiting} if awaiting
                    else {"verdict": verdict}),
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
    mp = res.get("material_provenance") or {}
    if mp.get("relocated"):
        lines.append("- 素材: **relocatable rematerialization**（凍結 manifest の"
                     "絶対パスは変更せず、走行時メモリ上でのみ新 root へ写像）")
        for row in mp.get("sources", []):
            lines.append(
                f"  - `{row['source']}`: archive `{str(row.get('archive_sha256'))[:16]}…`"
                f"（検証 {'済' if row.get('archive_verified') else '未'}） / "
                f"展開物集約 `{str(row.get('extracted_sha256'))[:16]}…` "
                f"({row.get('extracted_file_count')} files) / "
                f"new_root `{_cell(row['new_root'])}`")
    else:
        lines.append("- 素材: 凍結 manifest の絶対パスをそのまま使用")
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
    elif res["overall"].get("awaiting") == "perceptual_gate":
        lines.append("> **S4 READY_FOR_LISTENING — 機械 Gate は通過した。"
                     "人間 Gate（§13 の 6 問）が未了のため S4 の verdict はまだ出せない。**")
        lines.append("")
        lines.append("verdict 語彙は §16 の 4 状態しか無いので `BLOCKED` と記録するが、"
                     "これは「入力・素材・正本が不足」の BLOCKED ではない。")
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
    published = False        # publish_wav まで到達したか（未到達なら消すものは無い）
    try:
        runs, meta, backup = sr.run_all(write_wav=write_wav, require_clean=require_clean)
        published = write_wav
        cross = sr.cross_process_shas()
        # `run_all()` の検査は cross-process 再計算の**前**に終わっている。
        # 別プロセスが走っている間に正本が差し替わると、記録が「もう存在しない
        # bytes の digest」を来歴として主張したまま公開されうる。公開直前にもう一度見る。
        sr.assert_canonical_unchanged()
        results = [sg.pair_verdict(r, cross) for r in runs]
        mech = sg.overall_gate(results)
        res = build_results(meta, mech)
        files = [(JSON_PATH, _dumps(res).encode("utf-8")),
                 (RECORD_PATH, render_record(res).encode("utf-8"))]
        dir_swaps: List[Tuple[Path, Path]] = []
        removals: List[Path] = []
        if mech["verdict"] == "PASS":
            files += _stage_ear_pack(meta, runs, res, dir_swaps)
            # 新しい pack を出すので、前回の reveal と freeze は同じ transaction で消す
            removals += [FREEZE_JSON, FREEZE_MD, sb.KEY_REVEAL]
            removals += list(S4B_DEPENDENT_ARTIFACTS)
        else:
            # 機械 FAIL / BLOCKED では pack を作らない（§13）。前回走行の
            # freeze・鍵・pack が残ると、正本と矛盾する成果物が同居する。
            removals += list(OUTCOME_ARTIFACTS) + list(S4B_DEPENDENT_ARTIFACTS)
            dir_swaps.append((_empty_dir(), sb.EAR_AUDIO))
        publish(files=files, dir_swaps=dir_swaps, removals=removals,
                secret=[sb.PRIVATE_KEY])
    except S4Stop as stop:
        _rollback_wav_if_published(published, backup)
        publish(files=blocked_bundle(stop),
                removals=list(OUTCOME_ARTIFACTS) + list(S4B_DEPENDENT_ARTIFACTS))
        print(f"{stop.status}: {stop.cause}")
        return 3
    except BaseException:
        _rollback_wav_if_published(published, backup)
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


def _empty_dir() -> Path:
    """`dir_swaps` で公開先を空へ置き換えるための staging。"""
    staging = sb.EAR_AUDIO.with_name(sb.EAR_AUDIO.name + ".staging")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    return staging


def _stage_ear_pack(meta: Dict[str, Any], runs: Sequence[sr.PairRun],
                    res: Dict[str, Any],
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
    # **期待クリップ digest と機械結果の digest を key へ封じる。**
    # manifest は commitment に覆われない可変ファイルなので、そこに載せた digest を
    # Phase C が信用すると「WAV を差し替えて manifest も書き換える」で全検査が通る。
    # 同様に、Phase C が s4_results.json の verdict だけを見ると、別走行の
    # 機械 PASS と別 pack の回答を組み合わせて公開できる。
    _key, key_raw = sb.build_private_key(
        trials, salt, meta["s3_results_sha256"], meta["s35_results_sha256"], selected,
        audio_sha256=audio_sha, mechanistic_digest=sb.sha256_bytes(_dumps(res).encode("utf-8")))
    # private key も **同じ transaction** で置く。先に書くと、後段が失敗したとき
    # 「新しい key + 古い manifest」が残り commitment 検証が通らなくなる。
    commitment = sb.sha256_bytes(key_raw)
    manifest = sb.build_blind_manifest(trials, audio_sha, meta["s3_results_sha256"],
                                       meta["s35_results_sha256"], commitment)
    return [(sb.PRIVATE_KEY, key_raw),
            (sb.BLIND_MANIFEST, _dumps(manifest).encode("utf-8")),
            (sb.ANSWERS.with_name("answers.template.json"),
             _dumps(sb.answers_template(trials, commitment)).encode("utf-8"))]


# ---------------------------------------------------------------------------
# §20 Phase C — Final
# ---------------------------------------------------------------------------
#: Phase C が書き換えるキー。結合対象から外さないと、**同じ回答での冪等な再実行**が
#: `_assert_mechanistic_binding` で落ちる（初回 Phase C が結果を書き換えるため）。
_PHASE_C_KEYS = ("perceptual", "overall")


def mechanistic_digest(res: Dict[str, Any]) -> str:
    """Phase A が確定させる部分（= Phase C が触らない部分）の digest。

    ファイル全体を覆うと、初回 Phase C が `perceptual` / `overall` を書き足した
    時点で digest が動き、`_assert_reveal_idempotent` が許すはずの
    「同じ回答での再実行」が手前で落ちる。
    """
    stable = {k: v for k, v in res.items() if k not in _PHASE_C_KEYS}
    return sb.sha256_bytes(sb.canonical_bytes(stable))


def _assert_mechanistic_binding(key: Dict[str, Any], res_raw: bytes) -> None:
    """pack が **この** 機械結果に対して作られたことを確認する。

    verdict の文字列だけを見ると、別走行の機械 PASS と別 pack の回答を組み合わせて
    公開できる。Phase A は結果 bytes の digest を key へ封じてあるので突き合わせる。
    """
    want = key.get("mechanistic_digest")
    if not want:
        raise S4Stop(
            cause="commitment 済み key に mechanistic_digest が無い",
            impact="この耳 pack がどの機械結果に対応するかを確認できない",
            minimal_fix="Phase A を実行して耳 pack を作り直す")
    try:
        parsed = json.loads(res_raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise S4Stop(cause=f"s4_results.json が JSON として読めない: {exc}",
                     impact="pack と機械結果の対応を確認できない",
                     minimal_fix="Phase A からやり直す") from exc
    got = mechanistic_digest(parsed)
    if got != want:
        raise S4Stop(
            cause=f"s4_results.json の digest が key の pin と一致しない "
                  f"({got[:16]}… != {str(want)[:16]}…)",
            impact="別走行の機械結果と、別 pack で集めた回答を組み合わせて"
                   "S4 の判定を公開することになる",
            minimal_fix="Phase A からやり直す。機械結果を手で差し替えない")


def _assert_reveal_idempotent(answers_sha: str, manifest: Dict[str, Any]) -> None:
    """既に key を開封済みなら、**同じ回答での再実行しか許さない**。

    `key_reveal.json` は全問の正解を露出する。開封後に `answers.json` を書き換えて
    再採点できると、正当な不成立を PASS へ反転できてしまう。
    """
    if not sb.KEY_REVEAL.exists():
        # **境界宣言**: reveal が無い状態を「未開封」と読む。開封後に消して
        # 回答を書き換える経路は塞げていない。`results/` は clean worktree 判定の
        # 対象外で、reveal も再生成される（Phase A の PASS 経路が前回分を削除する）
        # ため、「HEAD に在るか」を開封の証拠にはできない — それをやると**正規の
        # 初回 Phase C が必ず BLOCKED になる**（実測で確認、PR #299 第 10 巡）。
        # commitment が守るのは「実験者が回答後に正解を差し替えないこと」であって、
        # results/ へ書ける主体からの防御ではない（S3.5 の既知の性質と同じ範囲）。
        return
    try:
        reveal = json.loads(sb.KEY_REVEAL.read_bytes().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise S4Stop(cause=f"既存の key_reveal.json が読めない: {exc}",
                     impact="開封後の回答差し替えを検出できない",
                     minimal_fix="key_reveal.json の破損を確認する") from exc
    prev = reveal.get("answers_sha256")
    if prev and prev != answers_sha:
        raise S4Stop(
            cause=f"key 開封後に回答が変わっている "
                  f"({str(prev)[:16]}… -> {answers_sha[:16]}…)",
            impact="正解を見たあとで回答を書き換え、不成立を PASS へ反転できてしまう",
            minimal_fix="この pack の判定は確定済み。やり直すなら Phase A から"
                        "新しい pack を作る")
    if reveal.get("key_commitment") and \
            reveal["key_commitment"] != manifest.get("key_commitment"):
        raise S4Stop(
            cause="既存 key_reveal が別の pack のものである",
            impact="開封済み pack と現在の pack が食い違ったまま再採点される",
            minimal_fix="Phase A からやり直す")


def _phase_c_closure() -> Dict[str, Any]:
    """採点に使う実装を Phase C 時点で pin する。

    Phase A の closure だけを記録すると、pack 生成後に採点コード（閾値・採点関数）
    を書き換えて、別のコードで出した判定を Phase A の来歴で公開できる。
    """
    wt = sr.worktree_state()
    if wt["clean"] is not True:
        reason = ("git 状態を確認できない" if wt["clean"] is None
                  else f"未コミットの変更がある: {wt['entries'][:5]}")
        raise S4Stop(
            cause=f"Phase C も clean worktree を要求する（{reason}）",
            impact="採点した実装と記録する closure が対応せず、判定が再現不能になる",
            minimal_fix="変更を commit してから Phase C を実行する")
    return {"worktree": wt, "closure": sr.closure_digest()}


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
        # commitment が守るのは「回答後に正解が差し替わらないこと」だけ。
        # 聴いた音・回答の帰属・機械結果の帰属・採点コードは別途 pin する。
        _assert_mechanistic_binding(key, res_raw)
        sb.verify_pack_audio(key)                 # 期待値は key 側（manifest は可変）
        trials = [dict(v, trial_id=k) for k, v in key["trials"].items()]
        answers_doc, answers, answers_sha = sb.load_answers()
        sb.verify_answer_binding(answers_doc, manifest)
        _assert_reveal_idempotent(answers_sha, manifest)
        sb.assert_answers_complete(trials, answers)
        phase_c_closure = _phase_c_closure()
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
               "pack_audio_verified": True,
               "answer_binding_verified": True,
               "mechanistic_binding_verified": True,
               "phase_c_closure": phase_c_closure,
               "verdict": sg.perceptual_verdict(abx_v, id_v),
               "trials": scored["abx"] + scored["identity"]}
        overall = sg.s4_overall(mech["verdict"], abx_v, id_v,
                                hard_failure=mech.get("hard_failure", False))
        res["perceptual"] = per
        res["overall"] = {"verdict": overall}
        reveal = sb.build_key_reveal(key, answers_sha, scored)
        reveal["key_commitment"] = manifest["key_commitment"]
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
