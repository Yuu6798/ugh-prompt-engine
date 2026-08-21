"""genome_s35/s35_prepare.py — Phase A–C（設計書 v1.0 §20）。

責務は 10 個だけ。研究判断は行わない。

1. S3 正本読み込み        6. WAV SHA 検証
2. S3 PASS 確認           7. private blind key 生成
3. S3 結果 SHA 固定       8. ABX pack 生成
4. candidate gene 抽出    9. blind manifest 生成
5. pair 決定論選択       10. key commitment 保存

**禁止**: rerender / normalize / trim / fade / gain / resample / gene metric 再計算。
A/B/X は元 WAV の **byte copy**。

`genome_s3` / `planb_real` / `planb` は read-only（§18）。
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
PACK_DIR = RESULTS / "abx"
PACK_AUDIO = PACK_DIR / "audio"
BLIND_MANIFEST = RESULTS / "blind_manifest.json"
PRIVATE_KEY = RESULTS / "answer_key.private.json"
ANSWER_SHEET = PACK_DIR / "answer_sheet.template.json"
LISTENING_UI = PACK_DIR / "index.html"


class S35Stop(Exception):
    """§25 Stop Rules。出力は status / reason / affected_gene / required_action のみ。"""

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
    """commitment を取る対象の正規形（§9）。file の bytes と一致させる。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False, sort_keys=False)


def pair_leaf(pair_key: str) -> str:
    """S3 が WAV を置いたディレクトリ名（S3 側の規約をそのまま使う）。"""
    return pair_key.replace("|", "__").replace("#", "-")


# ---------------------------------------------------------------------------
# Phase A — S3 正本の検証（§3, §4, §25）
# ---------------------------------------------------------------------------
def load_s3_results() -> Tuple[Dict[str, Any], str]:
    """S3 正本を読み、**parse したのと同じ bytes** の digest を返す。"""
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
    """§4 S3 正本の事前 Gate。**S3.5 側で S3 の問題を修正しない。**"""
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


def verify_audio(res: Dict[str, Any],
                 needed: Optional[Sequence[Tuple[str, str]]] = None) -> Dict[Tuple[str, str], Path]:
    """§3 各 WAV の `actual_sha256 == 記録 SHA` を確認する。

    `needed` = `(pair_key, condition)` の列。None なら記録全件。
    不一致・欠落は BLOCKED（S3.5 側で再生成しない）。
    """
    rows = {(r["pair_key"], r["condition"]): r for r in res["reproducibility"]}
    targets = list(rows) if needed is None else list(needed)
    resolved: Dict[Tuple[str, str], Path] = {}
    missing: List[str] = []
    mismatched: List[str] = []
    for key in targets:
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
        resolved[key] = path
    if missing:
        raise S35Stop(reason=f"canonical WAV が欠落: {len(missing)} 件 (例 {missing[:2]})",
                      required_action="S3 の走行成果物を復元する。S3.5 で再合成しない")
    if mismatched:
        raise S35Stop(reason=f"WAV SHA が S3 正本と不一致: {len(mismatched)} 件 "
                             f"(例 {mismatched[:2]})",
                      required_action="S3 側へ戻す。S3.5 で再生成・補正しない")
    return resolved


# ---------------------------------------------------------------------------
# Phase B — candidate 抽出と pair 決定論選択（§5, §6）
# ---------------------------------------------------------------------------
def candidate_genes(res: Dict[str, Any]) -> List[str]:
    """§5 S3 で `SUPPORTED` の gene だけ。順序は S3 の記載順を保つ。"""
    return [g for g, v in res["genes"].items() if v.get("verdict") == sp.S3_SUPPORTED]


def candidate_pairs(res: Dict[str, Any], gene: str) -> List[Tuple[str, str]]:
    """§5/§6 S3 で `SUPPORTED` の pair のみ。`UNSUPPORTED`/`NOT_EVALUABLE` は昇格しない。"""
    pairs = res["genes"][gene]["pairs"]
    return [(p["pair_key"], p["context_id"]) for p in pairs.values()
            if p.get("verdict") == sp.S3_SUPPORTED]


def selection_for_gene(res: Dict[str, Any], s3_sha: str, gene: str) -> Dict[str, Any]:
    """1 gene の pair 選択。緩めない（満たせなければ `NOT_EVALUABLE_S35`）。"""
    cands = candidate_pairs(res, gene)
    picked = sp.select_pairs(cands, s3_sha, gene)
    if not picked:
        return {"gene": gene, "verdict": sp.GeneVerdict.NOT_EVALUABLE_S35.value,
                "candidate_pairs": len(cands),
                "candidate_contexts": sorted({k for _, k in cands}),
                "selected": []}
    return {
        "gene": gene,
        "candidate_pairs": len(cands),
        "candidate_contexts": sorted({k for _, k in cands}),
        "selected": [{"pair_key": pk, "probe_kind": kind,
                      "selection_hash": sp.selection_hash(s3_sha, gene, pk)}
                     for pk, kind in picked],
        "distinct_pairs": len({pk for pk, _ in picked}),
        "contexts": sorted({k for _, k in picked}),
    }


# ---------------------------------------------------------------------------
# Phase C — blind key と ABX pack（§7–§10）
# ---------------------------------------------------------------------------
def _bit(salt: bytes, label: str) -> int:
    return hashlib.sha256(salt + label.encode("utf-8")).digest()[0] & 1


def _order_key(salt: bytes, label: str) -> str:
    return hashlib.sha256(salt + b"order" + label.encode("utf-8")).hexdigest()


def build_trials(selections: Sequence[Dict[str, Any]], salt: bytes) -> List[Dict[str, Any]]:
    """§7/§8 各 pair 2 trial。X は B0 と gene を 1 回ずつ、A/B 配置は反転。

    どちらを先に出すか・どちらの極性で始めるかは **blind key で決める**。
    """
    trials: List[Dict[str, Any]] = []
    for sel in selections:
        if not sel.get("selected"):
            continue
        gene = sel["gene"]
        for entry in sel["selected"]:
            pk = entry["pair_key"]
            uid = f"{gene}|{pk}"
            swap = _bit(salt, "ab|" + uid)          # rep1 の A/B 極性
            for rep, x_side in ((1, sp.SIDE_B0), (2, sp.SIDE_GENE)):
                # rep1: (B0, gene) か (gene, B0) / rep2 はその反転
                first_is_b0 = (swap == 0) if rep == 1 else (swap == 1)
                a_side = sp.SIDE_B0 if first_is_b0 else sp.SIDE_GENE
                b_side = sp.SIDE_GENE if first_is_b0 else sp.SIDE_B0
                trials.append({
                    "gene": gene, "pair_key": pk, "probe_kind": entry["probe_kind"],
                    "repeat": rep, "a_side": a_side, "b_side": b_side, "x_side": x_side,
                    "correct": "A" if a_side == x_side else "B",
                    "_uid": f"{uid}|{rep}",
                })
    # §10 gene を跨いで blind salt でシャッフルする（gene ごとに固めない）
    trials.sort(key=lambda t: _order_key(salt, t["_uid"]))
    for i, t in enumerate(trials, start=1):
        t["trial_id"] = f"T{i:03d}"
    return trials


def _condition_for(gene: str, side: str) -> str:
    return "B0" if side == sp.SIDE_B0 else sp.GENE_CONDITION[gene]


def materialize_pack(trials: Sequence[Dict[str, Any]],
                     resolved: Dict[Tuple[str, str], Path]) -> Dict[str, Dict[str, str]]:
    """§6/§20 元 WAV の **byte copy**。コピー前後の SHA 一致を確認する。"""
    if PACK_AUDIO.exists():
        shutil.rmtree(PACK_AUDIO)
    PACK_AUDIO.mkdir(parents=True, exist_ok=True)
    audio_sha: Dict[str, Dict[str, str]] = {}
    for t in trials:
        per: Dict[str, str] = {}
        for slot in ("A", "B", "X"):
            side = t[f"{slot.lower()}_side"] if slot != "X" else t["x_side"]
            src = resolved[(t["pair_key"], _condition_for(t["gene"], side))]
            before = sha256_file(src)
            dst = PACK_AUDIO / f"{t['trial_id']}_{slot}.wav"
            shutil.copyfile(src, dst)
            after = sha256_file(dst)
            if before != after:
                raise S35Stop(
                    reason=f"{dst.name}: byte copy 前後で SHA が変わった "
                           f"({before[:16]}… -> {after[:16]}…)",
                    required_action="コピー経路を確認する。正規化・変換を挟まない",
                    affected_gene=t["gene"])
            per[slot] = after
        audio_sha[t["trial_id"]] = per
    return audio_sha


def write_private_key(trials: Sequence[Dict[str, Any]], salt: bytes,
                      s3_sha: str) -> Tuple[Dict[str, Any], str]:
    """§9 local-only の正解表を書き、その **file bytes** の SHA を commitment にする。"""
    key = {
        "protocol_version": sp.PROTOCOL_VERSION,
        "s3_results_sha256": s3_sha,
        "salt_hex": salt.hex(),
        "trials": {t["trial_id"]: {"gene": t["gene"], "pair_key": t["pair_key"],
                                   "probe_kind": t["probe_kind"], "repeat": t["repeat"],
                                   "a_side": t["a_side"], "b_side": t["b_side"],
                                   "x_side": t["x_side"], "correct": t["correct"]}
                   for t in trials},
    }
    raw = canonical_bytes(key)
    RESULTS.mkdir(parents=True, exist_ok=True)
    PRIVATE_KEY.write_bytes(raw)
    os.chmod(PRIVATE_KEY, 0o600)
    return key, sha256_bytes(raw)


def write_blind_manifest(trials: Sequence[Dict[str, Any]],
                         audio_sha: Dict[str, Dict[str, str]],
                         s3_sha: str, commitment: str) -> str:
    """§9 公開側。gene 名・pair_key・A/B の正体・X 正解は入れない。"""
    manifest = {
        "protocol_version": sp.PROTOCOL_VERSION,
        "s3_results_sha256": s3_sha,
        "trial_ids": [t["trial_id"] for t in trials],
        "audio_sha256": {tid: audio_sha[tid] for tid in (t["trial_id"] for t in trials)},
        "key_commitment": commitment,
    }
    BLIND_MANIFEST.write_text(_dumps(manifest), encoding="utf-8")
    return sha256_file(BLIND_MANIFEST)


def write_answer_sheet(trials: Sequence[Dict[str, Any]]) -> None:
    """§12 の回答ファイル雛形。回答は A / B / UNSURE のみ。"""
    ANSWER_SHEET.write_text(_dumps({
        "schema": sp.ANSWERS_SCHEMA,
        "listener_id": "listener-01",
        "session_id": "REPLACE_ME",
        "answers": {t["trial_id"]: "" for t in trials},
    }), encoding="utf-8")


_UI_TEMPLATE = """<meta charset="utf-8"><title>S3.5 ABX</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:44rem;margin:2rem auto;padding:0 1rem}
 .t{border:1px solid #8884;border-radius:.5rem;padding:1rem;margin:.75rem 0}
 button{font-size:1rem;padding:.4rem .9rem;margin-right:.4rem}
 button[disabled]{opacity:.4}
 label{margin-right:1rem}
 #out{width:100%;height:12rem;font-family:ui-monospace,monospace}
 .n{color:#888;font-size:.85rem}
</style>
<h1>S3.5 ABX</h1>
<p class="n">X は A と B のどちらと同じかだけを答えます。良し悪し・自然さは判定しません。
各クリップの再生は最大 __MAXREPLAY__ 回。正誤は最後まで表示されません。
自動音量正規化・EQ・空間オーディオはオフにしてください。</p>
<div id="trials"></div>
<h2>回答</h2>
<p class="n">下の JSON を保存して渡してください（session_id は任意の文字列）。</p>
<textarea id="out" readonly></textarea>
<script>
const IDS = __IDS__, MAX = __MAXREPLAY__, ans = {}, plays = {};
const root = document.getElementById('trials'), out = document.getElementById('out');
function render(){
  out.value = JSON.stringify({schema:"__ANSWERSCHEMA__",listener_id:"listener-01",
    session_id:"REPLACE_ME",answers:Object.fromEntries(IDS.map(i=>[i,ans[i]||""]))},null,2);
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


def write_listening_ui(trials: Sequence[Dict[str, Any]]) -> None:
    """§9 最小 UI。**answer key を JS へ埋め込まない**（trial_id だけ）。"""
    html = (_UI_TEMPLATE
            .replace("__IDS__", json.dumps([t["trial_id"] for t in trials]))
            .replace("__MAXREPLAY__", str(sp.MAX_REPLAYS_PER_CLIP))
            .replace("__ANSWERSCHEMA__", sp.ANSWERS_SCHEMA))
    LISTENING_UI.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase A–C ドライバ
# ---------------------------------------------------------------------------
def prepare(salt: Optional[bytes] = None) -> Dict[str, Any]:
    res, s3_sha = load_s3_results()
    gate_s3(res)

    selections = [selection_for_gene(res, s3_sha, g) for g in candidate_genes(res)]
    usable = [s for s in selections if s.get("selected")]
    if not usable:
        raise S35Stop(reason="どの gene でも 4 pair / 2 context を満たせない",
                      required_action="S3 の SUPPORTED pair を増やす。閾値は緩めない")

    needed: List[Tuple[str, str]] = []
    for sel in usable:
        for e in sel["selected"]:
            needed.append((e["pair_key"], "B0"))
            needed.append((e["pair_key"], sp.GENE_CONDITION[sel["gene"]]))
    resolved = verify_audio(res, needed)

    if PRIVATE_KEY.exists():
        raise S35Stop(reason=f"private key が既に存在する（{PRIVATE_KEY.name}）",
                      required_action="既存 session を閉じるか結果を退避してから作り直す。"
                                      "上書きは blind の破壊にあたる")
    salt = salt if salt is not None else secrets.token_bytes(32)   # §9 256-bit CSPRNG
    trials = build_trials(usable, salt)

    PACK_DIR.mkdir(parents=True, exist_ok=True)
    audio_sha = materialize_pack(trials, resolved)
    _key, commitment = write_private_key(trials, salt, s3_sha)
    manifest_sha = write_blind_manifest(trials, audio_sha, s3_sha, commitment)
    write_answer_sheet(trials)
    write_listening_ui(trials)

    return {
        "s3_results_sha256": s3_sha,
        "trial_count": len(trials),
        "genes": [s["gene"] for s in usable],
        "not_evaluable": [s["gene"] for s in selections if not s.get("selected")],
        "blind_manifest_sha256": manifest_sha,
        "key_commitment": commitment,
        "audio_sha_verified": True,
        "selections": selections,
    }


if __name__ == "__main__":
    try:
        info = prepare()
    except S35Stop as stop:
        print(_dumps(stop.as_dict()))
        raise SystemExit(3) from None
    print("READY_FOR_LISTENING")
    print()
    print(f"trial_count: {info['trial_count']}")
    print(f"genes: {', '.join(info['genes'])}")
    print(f"s3_results_sha256: {info['s3_results_sha256']}")
    print(f"blind_manifest_sha256: {info['blind_manifest_sha256']}")
    print(f"key_commitment: {info['key_commitment']}")
    print("audio_sha_verified: true")
