"""genome_s35/s35_prepare.py — S3 正本の検証と 2 段階 ABX pack の生成。

責務:

1. S3 正本読み込み・PASS 確認・結果 SHA 固定
2. candidate gene 抽出（S3 `SUPPORTED` のみ）
3. Stage 1 / Stage 2 の pair を**まとめて**決定論選択して commit する
   （Stage 1 の結果を見てから Stage 2 の pair を選べないようにするため）
4. WAV SHA 検証
5. private blind key 生成 + key commitment
6. Stage 1 pack 生成（Stage 2 は通過 gene が確定してから materialize）

**禁止**: rerender / normalize / trim / fade / gain / resample / denoise /
gene metric 再計算。A/B/X は元 WAV の **byte copy**。

`genome_s3` / `planb_real` / `planb` は read-only。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_HERE = Path(__file__).resolve().parent
_FOUNDRY = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s35_spec as sp  # noqa: E402

S3_RESULTS = _FOUNDRY / "genome_s3" / "results" / "s3_results.json"
S3_WAV_DIR = _FOUNDRY / "genome_s3" / "results" / "wav"

RESULTS = _HERE / "results"
BLIND_MANIFEST = RESULTS / "blind_manifest.json"
PRIVATE_KEY = RESULTS / "answer_key.private.json"


class S35Stop(Exception):
    """停止規則。出力は status / reason / affected_gene / required_action のみ。"""

    def __init__(self, reason: str, required_action: str,
                 affected_gene: Optional[str] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.required_action = required_action
        self.affected_gene = affected_gene

    def as_dict(self) -> Dict[str, Optional[str]]:
        return {"status": "BLOCKED", "reason": self.reason,
                "affected_gene": self.affected_gene,
                "required_action": self.required_action}


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_bytes(obj: Any) -> bytes:
    """commitment を取る対象の正規形。file の bytes と一致させる。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False, sort_keys=False)


def pair_leaf(pair_key: str) -> str:
    return pair_key.replace("|", "__").replace("#", "-")


def stage_dir(stage: int) -> Path:
    return RESULTS / f"stage{stage}"


def stage_audio_dir(stage: int) -> Path:
    return stage_dir(stage) / "audio"


# ---------------------------------------------------------------------------
# S3 正本の検証
# ---------------------------------------------------------------------------
def load_s3_results() -> Tuple[Dict[str, Any], str]:
    if not S3_RESULTS.exists():
        raise S35Stop(reason=f"S3 正本が無い（{S3_RESULTS}）",
                      required_action="genome_s3 の s3_report.py を実行して正本を用意する")
    raw = S3_RESULTS.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise S35Stop(reason=f"S3 正本が JSON として読めない: {exc}",
                      required_action="s3_results.json の破損を確認する") from exc
    return data, sha256_bytes(raw)


def gate_s3(res: Dict[str, Any]) -> None:
    overall = res.get("overall") or {}
    if overall.get("verdict") != "PASS":
        raise S35Stop(reason=f"S3 overall が PASS でない: {overall.get('verdict')!r}",
                      required_action="S3 側へ戻す。S3.5 は S3 を再裁定しない")
    count = overall.get("supported_gene_count")
    if not isinstance(count, int) or count < 2:
        raise S35Stop(reason=f"S3 supported_gene_count が 2 未満: {count!r}",
                      required_action="S3 側へ戻す")
    if not res.get("reproducibility"):
        raise S35Stop(reason="S3 正本に reproducibility 記録が無い",
                      required_action="S3 正本を最終コードで再生成する")


def verify_audio(res: Dict[str, Any], needed: Sequence[Tuple[str, str]],
                 ) -> Dict[Tuple[str, str], Tuple[Path, str]]:
    """各 WAV の `actual_sha256 == 記録 SHA` を確認する。S3.5 側で再生成しない。

    **検証済みの digest も返す。** copy 時に読み直すと、その間に S3 が再生成
    されていた場合に「差し替わった bytes 同士が一致する」だけになり、
    非正本の音声が verified として公開されてしまう（S3.5 の成果物を一切
    触らなくても、S3 の並行再生成だけで踏める）。
    """
    rows = {(r["pair_key"], r["condition"]): r for r in res["reproducibility"]}
    resolved: Dict[Tuple[str, str], Tuple[Path, str]] = {}
    missing: List[str] = []
    mismatched: List[str] = []
    for key in needed:
        row = rows.get(key)
        if row is None:
            missing.append(f"{key[0]}/{key[1]} (記録なし)")
            continue
        path = S3_WAV_DIR / pair_leaf(key[0]) / f"{key[1]}.wav"
        if not path.exists():
            missing.append(str(path))
            continue
        got = sha256_file(path)
        if got != row["wav_sha256"]:
            mismatched.append(f"{key[0]}/{key[1]}: {got[:16]}… != {row['wav_sha256'][:16]}…")
            continue
        resolved[key] = (path, got)      # 記録と一致した digest を持ち回る
    if missing:
        raise S35Stop(reason=f"canonical WAV が欠落: {len(missing)} 件 (例 {missing[:2]})",
                      required_action="S3 の走行成果物を復元する。S3.5 で再合成しない")
    if mismatched:
        raise S35Stop(reason=f"WAV SHA が S3 正本と不一致: {len(mismatched)} 件 "
                             f"(例 {mismatched[:2]})",
                      required_action="S3 側へ戻す。S3.5 で再生成・補正しない")
    return resolved


# ---------------------------------------------------------------------------
# candidate 抽出と 2 段階 pair 選択
# ---------------------------------------------------------------------------
def candidate_genes(res: Dict[str, Any]) -> List[str]:
    return [g for g, v in res["genes"].items() if v.get("verdict") == sp.S3_SUPPORTED]


def candidate_pairs(res: Dict[str, Any], gene: str) -> List[Tuple[str, str]]:
    pairs = res["genes"][gene]["pairs"]
    return [(p["pair_key"], p["context_id"]) for p in pairs.values()
            if p.get("verdict") == sp.S3_SUPPORTED]


def plan_gene(res: Dict[str, Any], s3_sha: str, gene: str) -> Dict[str, Any]:
    """1 gene の Stage 1 / Stage 2 pair を決める。人間も効果量も関与しない。"""
    cands = candidate_pairs(res, gene)
    first, second = sp.select_two_stage_pairs(cands, s3_sha, gene)
    plan: Dict[str, Any] = {
        "gene": gene,
        "candidate_pairs": len(cands),
        "candidate_contexts": sorted({k for _, k in cands}),
        "stage1": None, "stage2": None,
    }
    if first is None:
        plan["verdict"] = sp.GeneVerdict.NOT_EVALUABLE_S35.value
        plan["not_evaluable_reason"] = "S3 SUPPORTED の pair が無い"
        return plan
    plan["stage1"] = {"pair_key": first[0], "probe_kind": first[1],
                      "selection_hash": sp.selection_hash(s3_sha, gene, first[0])}
    if second is None:
        plan["verdict"] = sp.GeneVerdict.NOT_EVALUABLE_S35.value
        plan["not_evaluable_reason"] = (
            f"Stage 2 用の別 context が無い（Stage 1 = {first[1]} / "
            f"候補 context = {sorted({k for _, k in cands})}）")
        return plan
    plan["stage2"] = {"pair_key": second[0], "probe_kind": second[1],
                      "selection_hash": sp.selection_hash(s3_sha, gene, second[0])}
    return plan


# ---------------------------------------------------------------------------
# trial 組み立てと pack
# ---------------------------------------------------------------------------
def _bit(salt: bytes, label: str) -> int:
    return hashlib.sha256(salt + label.encode("utf-8")).digest()[0] & 1


def _order_key(salt: bytes, label: str) -> str:
    return hashlib.sha256(salt + b"order" + label.encode("utf-8")).hexdigest()


def build_stage_trials(plans: Sequence[Dict[str, Any]], stage: int,
                       salt: bytes, genes: Optional[Sequence[str]] = None,
                       ) -> List[Dict[str, Any]]:
    """1 stage 分の trial を作る。1 gene = 1 問。gene を跨いでシャッフルする。"""
    wanted = set(genes) if genes is not None else None
    trials: List[Dict[str, Any]] = []
    for plan in plans:
        entry = plan.get(f"stage{stage}")
        if entry is None:
            continue
        if wanted is not None and plan["gene"] not in wanted:
            continue
        gene = plan["gene"]
        uid = f"{gene}|{entry['pair_key']}|s{stage}"
        a_is_b0 = _bit(salt, "ab|" + uid) == 0
        x_is_b0 = _bit(salt, "x|" + uid) == 0
        a_side = sp.SIDE_B0 if a_is_b0 else sp.SIDE_GENE
        b_side = sp.SIDE_GENE if a_is_b0 else sp.SIDE_B0
        x_side = sp.SIDE_B0 if x_is_b0 else sp.SIDE_GENE
        trials.append({
            "gene": gene, "stage": stage, "pair_key": entry["pair_key"],
            "probe_kind": entry["probe_kind"],
            "a_side": a_side, "b_side": b_side, "x_side": x_side,
            "correct": "A" if a_side == x_side else "B",
            "_uid": uid,
        })
    trials.sort(key=lambda t: _order_key(salt, t["_uid"]))
    for i, t in enumerate(trials, start=1):
        t["trial_id"] = f"S{stage}T{i:02d}"
    return trials


def _condition_for(gene: str, side: str) -> str:
    return "B0" if side == sp.SIDE_B0 else sp.GENE_CONDITION[gene]


def needed_wavs(trials: Sequence[Dict[str, Any]]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for t in trials:
        out.append((t["pair_key"], "B0"))
        out.append((t["pair_key"], sp.GENE_CONDITION[t["gene"]]))
    return out


def materialize_stage(trials: Sequence[Dict[str, Any]], stage: int,
                      resolved: Dict[Tuple[str, str], Tuple[Path, str]],
                      audio_dir: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    """元 WAV の byte copy。コピー前後の SHA 一致を確認する。

    **公開先を先に消さない。** staging へ全部作り切ってから入れ替える。
    直接書くと、途中で落ちた瞬間に前回の音声が失われ、既存 manifest が
    部分的な置き換えを指したまま復旧経路が無くなる（`genome_s3` の
    WAV_STAGING と同じ扱い）。
    """
    staging = audio_dir if audio_dir is not None else stage_audio_dir(stage)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    audio_sha: Dict[str, Dict[str, str]] = {}
    try:
        for t in trials:
            per: Dict[str, str] = {}
            for slot in ("A", "B", "X"):
                side = t["x_side"] if slot == "X" else t[f"{slot.lower()}_side"]
                src, want = resolved[(t["pair_key"], _condition_for(t["gene"], side))]
                dst = staging / f"{t['trial_id']}_{slot}.wav"
                shutil.copyfile(src, dst)
                after = sha256_file(dst)
                # 比較対象は **S3 正本記録と照合済みの digest**。ここで src を
                # 読み直すと、S3 が並行再生成された場合に差し替わった bytes 同士で
                # 一致してしまう。
                if after != want:
                    raise S35Stop(
                        reason=f"{dst.name}: copy 後の SHA が S3 正本の検証済み "
                               f"digest と一致しない ({after[:16]}… != {want[:16]}…)",
                        required_action="S3 正本が走行中に変わっていないか確認する。"
                                        "S3.5 側で再生成・補正しない",
                        affected_gene=t["gene"])
                per[slot] = after
            audio_sha[t["trial_id"]] = per
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise                       # 公開先は触っていないので前回の pack が残る
    return audio_sha


def _swap_dir(staging: Path, dest: Path) -> Optional[Path]:
    """出来上がった staging を公開先へ入れ替え、旧版の退避先を返す。

    返した `.prev` は、後続（manifest 書き込み等）が失敗したときに
    `_rollback_dir()` で戻すために呼び出し側が保持する。
    """
    backup = dest.with_name(dest.name + ".prev")
    if backup.exists():
        shutil.rmtree(backup)
    had_dest = dest.exists()
    try:
        if had_dest:
            os.replace(dest, backup)
        os.replace(staging, dest)
    except BaseException:
        if had_dest and backup.exists() and not dest.exists():
            os.replace(backup, dest)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return backup if had_dest else None


def _rollback_dir(dest: Path, backup: Optional[Path]) -> None:
    """`_swap_dir` を巻き戻す（後続が失敗したとき用）。"""
    shutil.rmtree(dest, ignore_errors=True)
    if backup is not None and backup.exists():
        os.replace(backup, dest)


def _drop_backup(backup: Optional[Path]) -> None:
    if backup is not None and backup.exists():
        shutil.rmtree(backup)


def publish_stage(stage: int, trials: Sequence[Dict[str, Any]],
                  resolved: Dict[Tuple[str, str], Tuple[Path, str]],
                  write_manifest) -> Dict[str, Dict[str, str]]:
    """**stage 一式を 1 トランザクションで公開する。**

    音声・UI・回答用紙を staging の stage ディレクトリへ作り切り、丸ごと入れ替え、
    最後に manifest を書く。manifest 書き込みが失敗したら stage ディレクトリごと
    巻き戻す。音声だけを守っても、その後の UI / manifest 書き込みで落ちると
    「旧 manifest が別の pack を指したまま、前回の音声は消えている」状態になる。
    """
    dest = stage_dir(stage)
    staging = dest.with_name(dest.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        audio_sha = materialize_stage(trials, stage, resolved, staging / "audio")
        write_stage_ui(trials, stage, staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise                        # 公開先に触れていない
    backup = _swap_dir(staging, dest)
    try:
        write_manifest(audio_sha)
    except BaseException:
        _rollback_dir(dest, backup)
        raise
    _drop_backup(backup)
    return audio_sha


_UI_TEMPLATE = """<meta charset="utf-8"><title>S3.5 ABX — Stage __STAGE__</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:44rem;margin:2rem auto;padding:0 1rem}
 .t{border:1px solid #8884;border-radius:.5rem;padding:1rem;margin:.75rem 0}
 button{font-size:1rem;padding:.4rem .9rem;margin-right:.4rem}
 button[disabled]{opacity:.4}
 label{margin-right:1rem}
 #out{width:100%;height:10rem;font-family:ui-monospace,monospace}
 .n{color:#888;font-size:.85rem}
</style>
<h1>S3.5 ABX — Stage __STAGE__（__COUNT__ 問）</h1>
<p class="n">質問はこれだけです — <b>X は A と B のどちらと同じですか？</b><br>
自然さ・好み・品質・語尾破綻・改善度は聞きません。分からなければ UNSURE。<br>
各クリップの再生は最大 __MAXREPLAY__ 回。正誤は最後まで表示されません。<br>
自動音量正規化・EQ・空間オーディオはオフにしてください（音量も触らないこと）。</p>
<div id="trials"></div>
<h2>回答</h2>
<p class="n">下の JSON を保存して渡してください。</p>
<textarea id="out" readonly></textarea>
<script>
const IDS = __IDS__, MAX = __MAXREPLAY__, STAGE = __STAGE__, ans = {}, plays = {};
const root = document.getElementById('trials'), out = document.getElementById('out');
function render(){
  out.value = JSON.stringify({schema:"__ANSWERSCHEMA__",stage:STAGE,
    listener_id:"listener-01",session_id:"REPLACE_ME",
    answers:Object.fromEntries(IDS.map(i=>[i,ans[i]||""]))},null,2);
}
IDS.forEach(id=>{
  const d=document.createElement('div'); d.className='t';
  d.innerHTML=`<b>${id}</b><br>`;
  ['A','B','X'].forEach(s=>{
    const k=id+s; plays[k]=0;
    const b=document.createElement('button'); b.textContent=s+' ▶ ('+MAX+')';
    const au=new Audio('audio/'+id+'_'+s+'.wav');
    b.onclick=()=>{ if(plays[k]>=MAX) return; plays[k]++;
      b.textContent=s+' ▶ ('+(MAX-plays[k])+')'; if(plays[k]>=MAX) b.disabled=true;
      au.currentTime=0; au.play(); };
    d.appendChild(b);
  });
  const p=document.createElement('div'); p.style.marginTop='.6rem';
  ['A','B','UNSURE'].forEach(v=>{
    const l=document.createElement('label'); const r=document.createElement('input');
    r.type='radio'; r.name=id; r.value=v;
    r.onchange=()=>{ ans[id]=v; render(); };
    l.appendChild(r); l.appendChild(document.createTextNode(' '+v)); p.appendChild(l);
  });
  d.appendChild(p); root.appendChild(d);
});
render();
</script>
"""


def write_stage_ui(trials: Sequence[Dict[str, Any]], stage: int,
                   out_dir: Optional[Path] = None) -> None:
    """最小 UI。**answer key を JS へ埋め込まない**（trial_id だけ）。"""
    out_dir = stage_dir(stage) if out_dir is None else out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    html = (_UI_TEMPLATE
            .replace("__IDS__", json.dumps([t["trial_id"] for t in trials]))
            .replace("__MAXREPLAY__", str(sp.MAX_REPLAYS_PER_CLIP))
            .replace("__STAGE__", str(stage))
            .replace("__COUNT__", str(len(trials)))
            .replace("__ANSWERSCHEMA__", sp.ANSWERS_SCHEMA))
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    (out_dir / "answer_sheet.template.json").write_text(_dumps({
        "schema": sp.ANSWERS_SCHEMA, "stage": stage,
        "listener_id": "listener-01", "session_id": "REPLACE_ME",
        "answers": {t["trial_id"]: "" for t in trials},
    }), encoding="utf-8")


# ---------------------------------------------------------------------------
# blind key / manifest
# ---------------------------------------------------------------------------
def write_private_key(plans: Sequence[Dict[str, Any]], all_trials: Dict[int, List[Dict[str, Any]]],
                      salt: bytes, s3_sha: str) -> Tuple[Dict[str, Any], str]:
    """**両 stage の正解を最初に確定して commit する。**

    Stage 1 の結果を見てから Stage 2 の pair を選べないようにするため、
    Stage 2 の trial も先に決めて key に含める（配布は後）。
    """
    key = {
        "protocol_version": sp.PROTOCOL_VERSION,
        "s3_results_sha256": s3_sha,
        "salt_hex": salt.hex(),
        "plans": {p["gene"]: {"stage1": p["stage1"], "stage2": p["stage2"],
                              "verdict": p.get("verdict"),
                              "not_evaluable_reason": p.get("not_evaluable_reason")}
                  for p in plans},
        "trials": {t["trial_id"]: {k: t[k] for k in
                                   ("gene", "stage", "pair_key", "probe_kind",
                                    "a_side", "b_side", "x_side", "correct")}
                   for stage_trials in all_trials.values() for t in stage_trials},
    }
    raw = canonical_bytes(key)
    RESULTS.mkdir(parents=True, exist_ok=True)
    PRIVATE_KEY.write_bytes(raw)
    os.chmod(PRIVATE_KEY, 0o600)
    return key, sha256_bytes(raw)


def write_blind_manifest(stage_trials: Dict[int, List[Dict[str, Any]]],
                         audio_sha: Dict[int, Dict[str, Dict[str, str]]],
                         s3_sha: str, commitment: str) -> str:
    """公開側。gene 名・pair_key・A/B の正体・X 正解は入れない。

    Stage 2 の trial_id は**まだ配布していないなら載せない**（未配布の問題数を
    先に見せない）。載せるのは materialize 済みの stage だけ。
    """
    manifest = {
        "protocol_version": sp.PROTOCOL_VERSION,
        "s3_results_sha256": s3_sha,
        "stages": {str(st): {"trial_ids": [t["trial_id"] for t in trials],
                             "audio_sha256": audio_sha[st]}
                   for st, trials in sorted(stage_trials.items())},
        "key_commitment": commitment,
    }
    BLIND_MANIFEST.write_text(_dumps(manifest), encoding="utf-8")
    return sha256_file(BLIND_MANIFEST)


def load_manifest() -> Dict[str, Any]:
    if not BLIND_MANIFEST.exists():
        raise S35Stop(reason=f"blind manifest が無い（{BLIND_MANIFEST}）",
                      required_action="Stage 1 の準備を先に実行する")
    return json.loads(BLIND_MANIFEST.read_text(encoding="utf-8"))


def load_private_key() -> Tuple[Dict[str, Any], str]:
    if not PRIVATE_KEY.exists():
        raise S35Stop(reason=f"private key が無い（{PRIVATE_KEY}）",
                      required_action="Stage 1 の成果物を復元する。再生成は blind の破壊")
    raw = PRIVATE_KEY.read_bytes()
    return json.loads(raw.decode("utf-8")), sha256_bytes(raw)


# ---------------------------------------------------------------------------
# Stage 1 / Stage 2 の準備
# ---------------------------------------------------------------------------
def prepare_stage1(salt: Optional[bytes] = None) -> Dict[str, Any]:
    res, s3_sha = load_s3_results()
    gate_s3(res)

    plans = [plan_gene(res, s3_sha, g) for g in candidate_genes(res)]
    usable = [p for p in plans if p["stage1"] is not None]
    if not usable:
        raise S35Stop(reason="Stage 1 を作れる gene が 1 つも無い",
                      required_action="S3 の SUPPORTED pair を確認する。閾値は緩めない")

    if PRIVATE_KEY.exists():
        raise S35Stop(reason=f"private key が既に存在する（{PRIVATE_KEY.name}）",
                      required_action="既存 session を閉じるか結果を退避してから作り直す。"
                                      "上書きは blind の破壊にあたる")
    salt = salt if salt is not None else secrets.token_bytes(32)

    t1 = build_stage_trials(usable, sp.STAGE1, salt)
    t2 = build_stage_trials(usable, sp.STAGE2, salt)      # 先に確定（配布はしない）
    if len(t1) > sp.MAX_TOTAL_TRIALS or len(t1) + len(t2) > sp.MAX_TOTAL_TRIALS:
        raise S35Stop(reason=f"trial 総数が上限 {sp.MAX_TOTAL_TRIALS} を超える "
                             f"(stage1={len(t1)} / stage2={len(t2)})",
                      required_action="候補 gene 数を確認する。上限は緩めない")

    resolved = verify_audio(res, needed_wavs(t1))
    _key, commitment = write_private_key(usable, {sp.STAGE1: t1, sp.STAGE2: t2},
                                         salt, s3_sha)
    holder: Dict[str, str] = {}

    def _write_manifest(sha1: Dict[str, Dict[str, str]]) -> None:
        holder["sha"] = write_blind_manifest({sp.STAGE1: t1}, {sp.STAGE1: sha1},
                                             s3_sha, commitment)

    publish_stage(sp.STAGE1, t1, resolved, _write_manifest)
    manifest_sha = holder["sha"]
    return {
        "stage": sp.STAGE1,
        "s3_results_sha256": s3_sha,
        "trial_count": len(t1),
        "genes": [p["gene"] for p in usable],
        "not_evaluable": [p["gene"] for p in plans
                          if p.get("verdict") == sp.GeneVerdict.NOT_EVALUABLE_S35.value],
        "blind_manifest_sha256": manifest_sha,
        "key_commitment": commitment,
        "audio_sha_verified": True,
        "plans": plans,
    }


def prepare_stage2(advancing: Sequence[str]) -> Dict[str, Any]:
    """Stage 1 に正解した gene だけ Stage 2 を配布する。pair は既に commit 済み。"""
    res, s3_sha = load_s3_results()
    key, commitment = load_private_key()
    if key.get("s3_results_sha256") != s3_sha:
        raise S35Stop(reason="S3 正本 SHA が Stage 1 時点から変わっている",
                      required_action="S3 正本を戻すか、session 全体を作り直す")

    t2 = [dict(spec, trial_id=tid) for tid, spec in key["trials"].items()
          if spec["stage"] == sp.STAGE2 and spec["gene"] in set(advancing)]
    t2.sort(key=lambda t: t["trial_id"])
    if not t2:
        return {"stage": sp.STAGE2, "trial_count": 0, "genes": [],
                "note": "Stage 2 へ進む gene が無い"}

    resolved = verify_audio(res, needed_wavs(t2))

    def _write_manifest(sha2: Dict[str, Dict[str, str]]) -> None:
        manifest = load_manifest()
        manifest["stages"][str(sp.STAGE2)] = {
            "trial_ids": [t["trial_id"] for t in t2], "audio_sha256": sha2}
        tmp = BLIND_MANIFEST.with_suffix(BLIND_MANIFEST.suffix + ".tmp")
        tmp.write_text(_dumps(manifest), encoding="utf-8")
        os.replace(tmp, BLIND_MANIFEST)

    publish_stage(sp.STAGE2, t2, resolved, _write_manifest)
    return {
        "stage": sp.STAGE2,
        "s3_results_sha256": s3_sha,
        "trial_count": len(t2),
        "genes": sorted({t["gene"] for t in t2}),
        "blind_manifest_sha256": sha256_file(BLIND_MANIFEST),
        "key_commitment": commitment,
        "audio_sha_verified": True,
    }


if __name__ == "__main__":
    try:
        info = prepare_stage1()
    except S35Stop as stop:
        print(_dumps(stop.as_dict()))
        raise SystemExit(3) from None
    print("READY_FOR_LISTENING (Stage 1)")
    print()
    print(f"trial_count: {info['trial_count']}")
    print(f"genes: {', '.join(info['genes'])}")
    print(f"s3_results_sha256: {info['s3_results_sha256']}")
    print(f"blind_manifest_sha256: {info['blind_manifest_sha256']}")
    print(f"key_commitment: {info['key_commitment']}")
    print("audio_sha_verified: true")
