"""genome_s4/s4_blind.py — 耳 pack の作成・blind 規律・採点（設計書 §13〜§15）。

**機械 Overall PASS の後だけ**呼ばれる。機械 FAIL / BLOCKED では pack を作らない（§13）。

- pair 選択は `s4_spec.ear_selection_hash` の昇順先頭（§13.1）。effect size・音質・
  聞きやすさは入力に含めない
- 問数は ABX 4 + Identity 2 の 6 問固定（§13.2 / §13.3）
- 正解は回答前に非公開。`answer_key.private.json` を commit し、回答凍結後だけ
  `key_reveal.json` を出す（§14）
- filename に意味情報を入れない（§14）
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s4_spec as sp  # noqa: E402
from s4_spec import S4Stop  # noqa: E402

RESULTS = _HERE / "results"
EAR_DIR = RESULTS / "ear_pack"
EAR_AUDIO = EAR_DIR / "audio"
BLIND_MANIFEST = RESULTS / "blind_manifest.json"
PRIVATE_KEY = RESULTS / "answer_key.private.json"
KEY_REVEAL = RESULTS / "key_reveal.json"
ANSWERS = RESULTS / "answers.json"

#: ABX の対象 gene -> (提示する 2 条件)。差は対象 gene の**追加だけ**（§13.2）。
ABX_PLAN: Tuple[Tuple[str, str, str], ...] = (
    # (問いの対象 gene, 背景条件, 複合条件)
    (sp.Gene.F0.value, "D", "FD"),          # F0 が Duration 背景で残るか
    (sp.Gene.DURATION.value, "F", "FD"),    # Duration が F0 背景で残るか
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def canonical_bytes(obj: Any) -> bytes:
    """commitment 対象の正規形。file の bytes と一致させる。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)


def _bit(salt: bytes, label: str) -> int:
    return hashlib.sha256(salt + label.encode("utf-8")).digest()[0] & 1


def _order_key(salt: bytes, label: str) -> str:
    return hashlib.sha256(salt + b"order" + label.encode("utf-8")).hexdigest()


def filename_is_blind(name: str) -> bool:
    """§14: filename に意味の分かる語を入れない。"""
    stem = Path(name).stem
    return not any(tok in stem for tok in sp.FORBIDDEN_FILENAME_TOKENS)


# ---------------------------------------------------------------------------
# §13.1 pair 選択
# ---------------------------------------------------------------------------
def select_pairs(candidates: Sequence[Tuple[str, str]], s3_sha: str, s35_sha: str,
                 ) -> Dict[str, str]:
    """`EAR_CONTEXTS` の各 context について pair を 1 件だけ決定論選択する。

    `candidates` = `(pair_key, context_id)`（S3 で F0 / Duration とも SUPPORTED）。
    """
    out: Dict[str, str] = {}
    for ctx in sp.EAR_CONTEXTS:
        in_ctx = [pk for pk, c in candidates if c == ctx]
        chosen = sp.select_ear_pair(in_ctx, s3_sha, s35_sha, ctx)
        if chosen is None:
            raise S4Stop(
                cause=f"耳 pack 対象 context {ctx!r} に candidate pair が無い",
                impact="§13.1 が要求する 2 context の耳判定を構成できない",
                minimal_fix="対象 context を勝手に差し替えず User 裁定へ戻す（§24）")
        out[ctx] = chosen
    return out


# ---------------------------------------------------------------------------
# §13.2 / §13.3 trial 構成
# ---------------------------------------------------------------------------
def build_trials(selected: Dict[str, str], salt: bytes) -> List[Dict[str, Any]]:
    """ABX 4 問 + Identity 2 問を作る。context を跨いで決定論シャッフルする。"""
    abx: List[Dict[str, Any]] = []
    for ctx in sp.EAR_CONTEXTS:
        pair_key = selected[ctx]
        for gene, background, combined in ABX_PLAN:
            uid = f"abx|{gene}|{ctx}|{pair_key}"
            a_is_bg = _bit(salt, "ab|" + uid) == 0
            x_is_bg = _bit(salt, "x|" + uid) == 0
            a_cond = background if a_is_bg else combined
            b_cond = combined if a_is_bg else background
            x_cond = background if x_is_bg else combined
            abx.append({"kind": "ABX", "gene": gene, "context_id": ctx,
                        "pair_key": pair_key, "a_condition": a_cond,
                        "b_condition": b_cond, "x_condition": x_cond,
                        "correct": "A" if a_cond == x_cond else "B", "_uid": uid})
    abx.sort(key=lambda t: _order_key(salt, t["_uid"]))
    for i, t in enumerate(abx, start=1):
        t["trial_id"] = f"T{i:03d}"

    ident: List[Dict[str, Any]] = []
    for ctx in sp.EAR_CONTEXTS:
        pair_key = selected[ctx]
        uid = f"identity|{ctx}|{pair_key}"
        one_is_b0 = _bit(salt, "id|" + uid) == 0
        ident.append({"kind": "IDENTITY", "gene": None, "context_id": ctx,
                      "pair_key": pair_key,
                      "slot1_condition": "B0" if one_is_b0 else "FD",
                      "slot2_condition": "FD" if one_is_b0 else "B0",
                      "_uid": uid})
    ident.sort(key=lambda t: _order_key(salt, t["_uid"]))
    for i, t in enumerate(ident, start=1):
        t["trial_id"] = f"I{i:03d}"

    trials = abx + ident
    if len(abx) != sp.ABX_TOTAL or len(ident) != sp.IDENTITY_TOTAL:
        raise S4Stop(
            cause=f"耳 pack の問数が {len(abx)} + {len(ident)} 問"
                  f"（要求 {sp.ABX_TOTAL} + {sp.IDENTITY_TOTAL}）",
            impact="事前登録した問数と違う pack は §13 の判定に使えない",
            minimal_fix="問数を勝手に変えない（§24）。原因を User 裁定へ戻す")
    return trials


def needed_clips(trials: Sequence[Dict[str, Any]]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for t in trials:
        if t["kind"] == "ABX":
            for slot in ("a_condition", "b_condition", "x_condition"):
                out.append((t["pair_key"], t[slot]))
        else:
            for slot in ("slot1_condition", "slot2_condition"):
                out.append((t["pair_key"], t[slot]))
    return sorted(set(out))


def clip_names(trial: Dict[str, Any]) -> List[Tuple[str, str]]:
    """`(filename, condition)`。§14 の命名規則（T001_A / I001_1）。"""
    tid = trial["trial_id"]
    if trial["kind"] == "ABX":
        return [(f"{tid}_A.wav", trial["a_condition"]),
                (f"{tid}_B.wav", trial["b_condition"]),
                (f"{tid}_X.wav", trial["x_condition"])]
    return [(f"{tid}_1.wav", trial["slot1_condition"]),
            (f"{tid}_2.wav", trial["slot2_condition"])]


# ---------------------------------------------------------------------------
# pack の生成
# ---------------------------------------------------------------------------
def materialize(trials: Sequence[Dict[str, Any]],
                resolved: Dict[Tuple[str, str], Tuple[Path, str]],
                staging: Path) -> Dict[str, Dict[str, str]]:
    """元 WAV の byte copy。コピー後の SHA が検証済み digest と一致すること。

    **公開先を先に消さない。** staging へ作り切ってから呼び出し側が入れ替える。
    """
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    audio_sha: Dict[str, Dict[str, str]] = {}
    for t in trials:
        per: Dict[str, str] = {}
        for name, cond in clip_names(t):
            if not filename_is_blind(name):
                raise S4Stop(
                    cause=f"耳 pack の filename に意味情報が含まれる: {name}",
                    impact="blind が破れ、ABX の回答が音以外の情報で決まりうる",
                    minimal_fix="命名規則（§14）を確認する")
            key = (t["pair_key"], cond)
            if key not in resolved:
                raise S4Stop(
                    cause=f"耳 pack に必要な WAV が無い: {t['pair_key']} / {cond}",
                    impact="pack を構成できず、人間 Gate を実行できない",
                    minimal_fix="Phase A の WAV 出力が残っているか確認する")
            src, want = resolved[key]
            dst = staging / name
            shutil.copyfile(src, dst)
            after = sha256_file(dst)
            if after != want:
                raise S4Stop(
                    cause=f"{name}: copy 後の SHA が検証済み digest と一致しない "
                          f"({after[:16]}… != {want[:16]}…)",
                    impact="pack の音が Phase A の記録と別物になり、判定の対象が食い違う",
                    minimal_fix="Phase A の WAV が走行中に変わっていないか確認する")
            per[name] = after
        audio_sha[t["trial_id"]] = per
    return audio_sha


def build_private_key(trials: Sequence[Dict[str, Any]], salt: bytes,
                      s3_sha: str, s35_sha: str, selected: Dict[str, str],
                      *, audio_sha256: Optional[Dict[str, Dict[str, str]]] = None,
                      mechanistic_digest: Optional[str] = None,
                      ) -> Tuple[Dict[str, Any], bytes]:
    """**6 問すべての正解を最初に確定して commit する**（§14）。

    正解に加えて、**聴かせるクリップの digest** と **その pack が対応する機械結果の
    digest** も封じる。どちらも可変ファイル（`blind_manifest.json` /
    `s4_results.json`）側にしか無いと、回答収集後に差し替えて全検査を通せる。
    """
    key = {
        "schema": sp.SCHEMA,
        "s3_results_sha256": s3_sha,
        "s35_results_sha256": s35_sha,
        "mechanistic_digest": mechanistic_digest,
        "audio_sha256": {k: dict(sorted(v.items()))
                         for k, v in sorted((audio_sha256 or {}).items())},
        "salt_hex": salt.hex(),
        "selected_pairs": dict(sorted(selected.items())),
        "trials": {t["trial_id"]: {k: t[k] for k in sorted(t) if k != "_uid"}
                   for t in trials},
    }
    return key, canonical_bytes(key)


def build_blind_manifest(trials: Sequence[Dict[str, Any]],
                         audio_sha: Dict[str, Dict[str, str]],
                         s3_sha: str, s35_sha: str, commitment: str) -> Dict[str, Any]:
    """公開側。gene 名・pair_key・context・A/B の正体・X の正解は入れない（§14）。"""
    return {
        "schema": sp.SCHEMA,
        "s3_results_sha256": s3_sha,
        "s35_results_sha256": s35_sha,
        "abx_trial_ids": [t["trial_id"] for t in trials if t["kind"] == "ABX"],
        "identity_trial_ids": [t["trial_id"] for t in trials if t["kind"] == "IDENTITY"],
        "audio_sha256": audio_sha,
        "key_commitment": commitment,
        "instructions": {
            "ABX": "A と B を聴き、X が A と B のどちらと同じかを答える（A / B / UNSURE）。",
            "IDENTITY": "2 つが同じ Ritsu 系の歌い手に聞こえるかを答える"
                        "（YES / NO / UNSURE）。自然さ・品質・好みは問わない。",
        },
    }


def answers_template(trials: Sequence[Dict[str, Any]], commitment: str) -> Dict[str, Any]:
    """回答用紙。**どの pack を聴いた回答か**を回答側の成果物にも刻む。

    これが無いと、Phase A を回し直して salt / 正解対応が変わった後の pack に対して
    古い回答をそのまま採点でき、偽の不成立（と確率次第で偽の PASS）を作れる。
    """
    return {"schema": sp.ANSWERS_SCHEMA,
            "key_commitment": commitment,
            "answers": {t["trial_id"]: ("A|B|UNSURE" if t["kind"] == "ABX"
                                        else "YES|NO|UNSURE")
                        for t in sorted(trials, key=lambda x: x["trial_id"])}}


# ---------------------------------------------------------------------------
# §14 回答凍結 / key reveal
# ---------------------------------------------------------------------------
def verify_commitment(key_raw: bytes, manifest: Dict[str, Any]) -> bool:
    return sha256_bytes(key_raw) == manifest.get("key_commitment")


def expected_audio(key: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """**commitment 済みの key** から、期待するクリップ digest 表を取り出す。

    `blind_manifest.json` は commitment に覆われていない可変ファイルなので、
    そこに載っている digest を信用してはならない（WAV を差し替えて manifest の
    digest も書き換えれば、全ての検査が通ってしまう）。期待値は key 側に置き、
    key は commitment で固定する。
    """
    listed = key.get("audio_sha256")
    if not isinstance(listed, dict) or not listed:
        raise S4Stop(
            cause="commitment 済み key に audio_sha256 が無い（または空）",
            impact="聴いた音が Phase A で pin した音と同じかを確認できない",
            minimal_fix="Phase A を実行して耳 pack を作り直す")
    trials = key.get("trials")
    if not isinstance(trials, dict) or set(listed) != set(trials):
        raise S4Stop(
            cause=f"key の audio_sha256 が全 trial を覆っていない "
                  f"(audio {sorted(listed)} / trials {sorted(trials or {})})",
            impact="欠けた問の音を検査しないまま採点し、差し替え・欠落を見逃す",
            minimal_fix="Phase A を実行して耳 pack を作り直す")
    return {str(k): {str(n): str(v) for n, v in per.items()}
            for k, per in listed.items()}


def verify_pack_audio(key: Dict[str, Any], audio_dir: Path = EAR_AUDIO) -> None:
    """採点の前に **pack の音そのもの**を再ハッシュして pin と突き合わせる。

    commitment が守るのは「実験者が回答後に正解を変えないこと」だけで、Phase A の
    後に WAV が差し替わった・壊れた・消えた場合は素通りする。それを許すと、
    pin された音とは別の音についての回答で PASS を凍結できてしまう。

    期待値は **key（commitment 済み）**から取る。可変な manifest は信用しない。
    """
    for trial_id, per in sorted(expected_audio(key).items()):
        if not per:
            raise S4Stop(
                cause=f"key の audio_sha256[{trial_id}] が空",
                impact="当該問の音を pin と突き合わせられない",
                minimal_fix="Phase A を実行して耳 pack を作り直す")
        for name, want in sorted(per.items()):
            path = audio_dir / name
            if not path.is_file():
                raise S4Stop(
                    cause=f"耳 pack の音が無い: {path}",
                    impact="pin された音とは別の（あるいは欠けた）pack についての"
                           "回答を採点することになる",
                    minimal_fix="Phase A を実行して耳 pack を作り直し、聴き直す")
            got = sha256_file(path)
            if got != want:
                raise S4Stop(
                    cause=f"耳 pack の音が pin と一致しない: {name} "
                          f"({got[:16]}… != {str(want)[:16]}…)",
                    impact="Phase A で pin した音とは別の音についての回答で"
                           "S4 の判定を凍結することになる",
                    minimal_fix="Phase A を実行して耳 pack を作り直し、聴き直す")


def verify_answer_binding(answers_doc: Dict[str, Any], manifest: Dict[str, Any]) -> None:
    """回答が **その pack** に対して集められたことを確認する。"""
    want = manifest.get("key_commitment")
    got = answers_doc.get("key_commitment")
    if not got:
        raise S4Stop(
            cause="回答ファイルに key_commitment が無い",
            impact="どの耳 pack に対する回答かを確認できず、"
                   "作り直した pack へ古い回答を当てて採点できてしまう",
            minimal_fix="results/answers.template.json を写して回答し直す")
    if got != want:
        raise S4Stop(
            cause=f"回答ファイルの key_commitment が blind manifest と一致しない "
                  f"({str(got)[:16]}… != {str(want)[:16]}…)",
            impact="別の耳 pack（別の salt・別の正解対応）で集めた回答を"
                   "現在の pack の正解で採点することになる",
            minimal_fix="現在の pack を聴いて回答し直す。古い回答を流用しない")


def load_answers(path: Path = ANSWERS) -> Tuple[Dict[str, Any], Dict[str, str], str]:
    """回答を 1 回だけ bytes で読み、doc / 回答 / bytes の SHA を返す。"""
    if not path.exists():
        raise S4Stop(
            cause=f"回答ファイルが無い（{path}）",
            impact="人間 Gate を採点できない",
            minimal_fix="ear_pack の 6 問に回答して results/answers.json を置く")
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise S4Stop(cause=f"回答ファイルが JSON として読めない: {exc}",
                     impact="人間 Gate を採点できない",
                     minimal_fix="answers.json の形式を確認する") from exc
    if not isinstance(data, dict):
        raise S4Stop(cause=f"回答ファイルの根が object でない: {type(data).__name__}",
                     impact="人間 Gate を採点できない",
                     minimal_fix="answers.json の形式を確認する")
    ans = data.get("answers")
    if not isinstance(ans, dict):
        raise S4Stop(cause="回答ファイルに answers object が無い",
                     impact="人間 Gate を採点できない",
                     minimal_fix="answers.json の形式を確認する")
    return data, {str(k): str(v) for k, v in ans.items()}, sha256_bytes(raw)


def assert_answers_complete(trials: Sequence[Dict[str, Any]],
                            answers: Dict[str, str]) -> None:
    """採点の**前**に、回答が committed trial 集合と正確に一致することを要求する。

    欠落・テンプレート値・語彙外の値を「不正解」として数えると、**未完成の回答用紙が
    偽の実験失敗**に化ける。それは NOT_ESTABLISHED ではなく BLOCKED である。
    """
    want = {t["trial_id"] for t in trials}
    got = set(answers)
    if want != got:
        raise S4Stop(
            cause=f"回答が committed trial 集合と一致しない "
                  f"(欠落 {sorted(want - got)} / 余分 {sorted(got - want)})",
            impact="未回答を不正解として数えると、未完成の回答用紙が偽の実験失敗になる",
            minimal_fix="results/answers.template.json を写して全問に回答する")
    allowed = {"ABX": {a.value for a in sp.Answer},
               "IDENTITY": {a.value for a in sp.IdentityAnswer}}
    for t in sorted(trials, key=lambda x: x["trial_id"]):
        value = answers[t["trial_id"]]
        ok = allowed[t["kind"] if t["kind"] in allowed else "ABX"]
        if value not in ok:
            raise S4Stop(
                cause=f"{t['trial_id']}: 回答 {value!r} が語彙外（許容 {sorted(ok)}）",
                impact="テンプレート値や誤入力を不正解として数えると偽の実験失敗になる",
                minimal_fix="当該問に有効な値で回答し直す")


def score(trials: Sequence[Dict[str, Any]], answers: Dict[str, str]) -> Dict[str, Any]:
    """ABX / Identity を採点する。**UNSURE は正答・YES に数えない**（§13.2 / §15）。"""
    abx_rows: List[Dict[str, Any]] = []
    id_rows: List[Dict[str, Any]] = []
    for t in sorted(trials, key=lambda x: x["trial_id"]):
        tid = t["trial_id"]
        given = answers.get(tid)
        if t["kind"] == "ABX":
            valid = given in (sp.Answer.A.value, sp.Answer.B.value,
                              sp.Answer.UNSURE.value)
            abx_rows.append({"trial_id": tid, "gene": t["gene"],
                             "context_id": t["context_id"], "answer": given,
                             "valid": bool(valid),
                             "correct": bool(valid and given == t["correct"])})
        else:
            valid = given in (sp.IdentityAnswer.YES.value, sp.IdentityAnswer.NO.value,
                              sp.IdentityAnswer.UNSURE.value)
            id_rows.append({"trial_id": tid, "context_id": t["context_id"],
                            "answer": given, "valid": bool(valid),
                            "yes": bool(valid and given == sp.IdentityAnswer.YES.value)})
    return {
        "abx": abx_rows, "identity": id_rows,
        "abx_correct": sum(1 for r in abx_rows if r["correct"]),
        "abx_total": len(abx_rows),
        "identity_yes": sum(1 for r in id_rows if r["yes"]),
        "identity_total": len(id_rows),
        "unanswered": sorted(t["trial_id"] for t in trials
                             if not answers.get(t["trial_id"])),
    }


def build_key_reveal(key: Dict[str, Any], answers_sha: str,
                     scored: Dict[str, Any]) -> Dict[str, Any]:
    """回答凍結**後**にだけ作る（§14）。"""
    return {"schema": sp.SCHEMA, "answers_sha256": answers_sha,
            "salt_hex": key["salt_hex"], "selected_pairs": key["selected_pairs"],
            "trials": key["trials"], "scored": scored}
