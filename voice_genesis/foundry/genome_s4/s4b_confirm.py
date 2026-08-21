"""genome_s4/s4b_confirm.py — S4b Salience-Controlled Coexpression Gate。

（User 指示 2026-08-21 / 設計者と相談のうえ改訂）

目的は 1 つだけ。

> F0 + Duration 複合発現時にも、**各 gene が十分強く発現した条件なら**
> 人間に知覚可能かを確認する。

**S4 本体の結果（NOT_ESTABLISHED）は変更しない。** 出力は `results/s4b/` に隔離し、
`s4_results.json` / `S4_RECORD.md` / `key_reveal.json` / freeze 判定には触れない。

pair 選択は **耳では選ばない**。S3 / S4 の既存機械値だけを使って事前選択する。

    F0 / Duration 共通:
      1. S3 で当該 gene が SUPPORTED
      2. S4 で COMBINABLE
      3. identity_margin_db(FD) > 0
      4. 当該 gene の intervention amount が最大

    可能なら **別 context** でもう 1 pair（= gene あたり最大 2 pair）。

聴取:

    D vs FD -> F0 追加を識別できるか
    F vs FD -> Duration 追加を識別できるか

最大 4 問。

制約:

- 使用音源は **S4 canonical WAV のみ**。再生成・補正・normalize は行わない
- blind 規律は S4 §14 と同じ commitment 方式
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s4_blind as sb  # noqa: E402
import s4_report as srep  # noqa: E402
import s4_spec as sp  # noqa: E402
from s4_runner import S4Stop  # noqa: E402

SCHEMA = "voicegenesis-genome-s4b/1.0"
#: S4b は S4 の判定が確定した後にしか走らせない（記録が S4 の結果を断言するため）。
REQUIRED_S4_OVERALL = "NOT_ESTABLISHED"
ANSWERS_SCHEMA = "voicegenesis-s4b-answers/1.0"

#: 選択規則の識別子。S4b は hash ではなく **機械値（intervention amount）順**で
#: 選ぶ。耳・音質・聞きやすさ・S4 での使用有無は入力に含めない。
SELECTION_RULE = "s4b-salience-v1: S3 SUPPORTED & S4 COMBINABLE & identity_margin>0 " \
                 "のうち intervention amount 最大（同値は pair_key 昇順）"

S4_RESULTS = _HERE / "results" / "s4_results.json"
S3_RESULTS = _HERE.parent / "genome_s3" / "results" / "s3_results.json"
S4_WAV_DIR = _HERE / "results" / "wav"

RESULTS = _HERE / "results" / "s4b"
AUDIO_DIR = RESULTS / "audio"
BLIND_MANIFEST = RESULTS / "blind_manifest.json"
PRIVATE_KEY = RESULTS / "answer_key.private.json"
KEY_REVEAL = RESULTS / "key_reveal.json"
ANSWERS = RESULTS / "answers.json"
JSON_PATH = RESULTS / "s4b_results.json"
RECORD_PATH = RESULTS / "S4B_RECORD.md"

#: gene -> (背景条件, 複合条件)。差は対象 gene の追加だけ。
GENE_CONTRAST: Dict[str, Tuple[str, str]] = {
    "f0": ("D", "FD"),          # D vs FD -> F0 追加を識別できるか
    "duration": ("F", "FD"),    # F vs FD -> Duration 追加を識別できるか
}
#: gene あたり最大 2 pair（最大 salience + 別 context の最大 salience）。
MAX_PAIRS_PER_GENE = 2
#: 上限問数。
MAX_TRIALS = len(GENE_CONTRAST) * MAX_PAIRS_PER_GENE


def _load(path: Path, what: str) -> Tuple[Dict[str, Any], str]:
    """**1 回だけ** bytes を読み、その同じ bytes から parse と digest を作る。

    読み直すと、走行中に差し替わった場合に「parse したのと違う bytes の digest」を
    来歴として記録してしまう。
    """
    if not path.exists():
        raise S4Stop(cause=f"{what} が無い（{path}）",
                     impact="S4b の入力を確定できない",
                     minimal_fix="先に S4 の Phase A / Phase C を完了させる")
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise S4Stop(cause=f"{what} が JSON として読めない: {exc}",
                     impact="S4b の入力を確定できない",
                     minimal_fix=f"{path} を確認する") from exc
    if not isinstance(data, dict):
        raise S4Stop(cause=f"{what} の根が object でない",
                     impact="S4b の入力を確定できない",
                     minimal_fix=f"{path} を確認する")
    return data, sb.sha256_bytes(raw)


def load_inputs() -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """S4 正本と S3 正本を読む。**耳判定の結果（key_reveal）は選択に使わない。**

    S4 の overall が確定していること（NOT_ESTABLISHED）を要求する。機械 PASS だけを
    見ると、Phase C 未了（overall = BLOCKED）や S4 PASS の状態でも S4b を走らせられ、
    「S4 は NOT_ESTABLISHED」と断言する記録が実際の S4 結果と矛盾する。
    """
    s4, s4_sha = _load(S4_RESULTS, "S4 正本 s4_results.json")
    mech = (s4.get("mechanistic") or {}).get("verdict")
    if mech != "PASS":
        raise S4Stop(
            cause=f"S4 の機械 Gate が {mech!r}",
            impact="canonical WAV が機械 Gate を通っていない条件で耳判定を足すことになる",
            minimal_fix="S4 の Phase A を完了させる")
    overall = (s4.get("overall") or {}).get("verdict")
    if overall != REQUIRED_S4_OVERALL:
        raise S4Stop(
            cause=f"S4 の overall が {overall!r}（要求 {REQUIRED_S4_OVERALL!r}）",
            impact="S4b の記録は「S4 = NOT_ESTABLISHED」を前提に書かれるため、"
                   "確定前・別結果の S4 と組み合わせると記録が矛盾する",
            minimal_fix="S4 の Phase C を完了させてから S4b を実行する")
    s3, s3_sha = _load(S3_RESULTS, "S3 正本 s3_results.json")
    # S4 が pin した S3 と同じ bytes であること。別の S3 で母集団を選びながら
    # S4 の digest を来歴として書くと、確認の provenance が偽になる。
    want = s4.get("s3_results_sha256")
    if s3_sha != want:
        raise S4Stop(
            cause=f"S3 正本の digest が S4 の pin と一致しない "
                  f"({s3_sha[:16]}… != {str(want)[:16]}…)",
            impact="S4 と別の S3 で candidate 母集団を選びながら、S4 の digest を"
                   "来歴として記録することになる",
            minimal_fix="S4 走行時の s3_results.json を復元する")
    return s4, s4_sha, s3


def qualify(s4: Dict[str, Any], s3: Dict[str, Any], gene: str) -> List[Dict[str, Any]]:
    """基準 1〜3 を満たす pair を集め、基準 4 用の機械値を添える。

    1. S3 で当該 gene が SUPPORTED
    2. S4 で COMBINABLE
    3. identity_margin_db(FD) > 0

    **耳・音質・聞きやすさ・S4 での使用有無は入力に含めない。**
    """
    s3_pairs = ((s3.get("genes") or {}).get(gene) or {}).get("pairs") or {}
    if not s3_pairs:
        raise S4Stop(cause=f"S3 正本に {gene} の pairs が無い",
                     impact="基準 1 を検査できず、事前選択が成立しない",
                     minimal_fix="s3_results.json を確認する")
    out: List[Dict[str, Any]] = []
    for pk, r in (s4["mechanistic"]["pairs"]).items():
        if r.get("verdict") != sp.PairVerdict.COMBINABLE.value:
            continue                                     # 基準 2
        if (s3_pairs.get(pk) or {}).get("verdict") != sp.S3_SUPPORTED:
            continue                                     # 基準 1
        margin = ((r.get("combination") or {}).get("identity") or {}).get("fd_value")
        if margin is None or float(margin) <= 0.0:
            continue                                     # 基準 3
        info = ((r.get("intervention") or {}).get("genes") or {}).get(gene) or {}
        amount = info.get("amount")
        if amount is None or not info.get("nonzero", False):
            continue                       # 介入量ゼロは salience を語れない
        out.append({"pair_key": pk, "context_id": r["context_id"],
                    "identity_margin_db": float(margin),
                    "intervention_amount": float(amount),
                    "intervention_unit": info.get("unit")})
    return out


def select_by_salience(qualified: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """基準 4: intervention amount 最大。可能なら **別 context** でもう 1 pair。

    同値は `pair_key` 昇順で割る（走行ごとに揺れない）。
    """
    if not qualified:
        return []
    ordered = sorted(qualified, key=lambda c: (-c["intervention_amount"], c["pair_key"]))
    picked = [dict(ordered[0], rank="max_salience")]
    other = next((c for c in ordered[1:]
                  if c["context_id"] != picked[0]["context_id"]), None)
    if other is not None:
        picked.append(dict(other, rank="max_salience_other_context"))
    return picked[:MAX_PAIRS_PER_GENE]


def plan(s4: Dict[str, Any], s3: Dict[str, Any]) -> List[Dict[str, Any]]:
    """gene ごとに事前選択し、聴取セル（gene × pair）を組む。"""
    cells: List[Dict[str, Any]] = []
    for gene in GENE_CONTRAST:
        chosen = select_by_salience(qualify(s4, s3, gene))
        if not chosen:
            raise S4Stop(
                cause=f"{gene}: 基準 1〜3 を満たす pair が無い",
                impact="salience 制御下の確認という S4b の目的が成立しない",
                minimal_fix="基準を緩めず User 裁定へ戻す")
        for c in chosen:
            cells.append(dict(c, gene=gene))
    if len(cells) > MAX_TRIALS:
        raise S4Stop(cause=f"聴取セルが {len(cells)}（上限 {MAX_TRIALS}）",
                     impact="事前に決めた上限問数を超える",
                     minimal_fix="上限を変えない。原因を User 裁定へ戻す")
    return cells


def build_trials(cells: Sequence[Dict[str, Any]], salt: bytes) -> List[Dict[str, Any]]:
    """セルから ABX trial を作る。gene / context を跨いで決定論シャッフルする。"""
    trials: List[Dict[str, Any]] = []
    for c in cells:
        gene = c["gene"]
        background, combined = GENE_CONTRAST[gene]
        uid = f"s4b|{gene}|{c['context_id']}|{c['pair_key']}"
        a_is_bg = sb._bit(salt, "ab|" + uid) == 0
        x_is_bg = sb._bit(salt, "x|" + uid) == 0
        a_cond = background if a_is_bg else combined
        b_cond = combined if a_is_bg else background
        x_cond = background if x_is_bg else combined
        trials.append({"kind": "ABX", "gene": gene, "context_id": c["context_id"],
                       "pair_key": c["pair_key"], "rank": c["rank"],
                       "intervention_amount": c["intervention_amount"],
                       "intervention_unit": c["intervention_unit"],
                       "identity_margin_db": c["identity_margin_db"],
                       "a_condition": a_cond, "b_condition": b_cond,
                       "x_condition": x_cond,
                       "correct": "A" if a_cond == x_cond else "B", "_uid": uid})
    trials.sort(key=lambda t: sb._order_key(salt, t["_uid"]))
    for i, t in enumerate(trials, start=1):
        t["trial_id"] = f"Q{i:03d}"
    return trials


#: **境界宣言**: `s4_results.json` は per-file の `wav_sha256` を記録していない
#: （`ConditionOutput.wav_sha256` は runner の中間表現に留まり、§21 の出力
#: スキーマへ載らない）。したがって S4b は「S4 が pin した digest との突き合わせ」
#: を行えない。ここで採るのは **S4b が実際に読んだ bytes の self-pin** であり、
#: S4 側 pin との cross-check ではない。偽の検証を作らずに範囲を明示する。
WAV_PIN_NOTE = ("s4_results.json は per-file の wav_sha256 を記録していないため、"
                "S4b が記録するのは実際に読んだ bytes の self-pin であり、"
                "S4 側 pin との cross-check ではない")


def canonical_sources(s4: Dict[str, Any], trials: Sequence[Dict[str, Any]],
                      ) -> Dict[Tuple[str, str], Tuple[Path, str]]:
    """S4 canonical WAV の実体を引き、読んだ bytes の digest を返す。

    **再生成・補正・normalize はしない。** 参照するのは S4 Phase A が公開した
    `results/wav/` の実体だけ。
    """
    out: Dict[Tuple[str, str], Tuple[Path, str]] = {}
    known = set(s4["mechanistic"]["pairs"])
    for t in trials:
        pk = t["pair_key"]
        if pk not in known:
            raise S4Stop(
                cause=f"{pk} が S4 正本の pair に無い",
                impact="S4 が判定していない pair の音で耳判定を行うことになる",
                minimal_fix="対象 pair の導出を確認する")
        leaf = pk.replace("|", "__").replace("#", "-")
        for cond in (t["a_condition"], t["b_condition"], t["x_condition"]):
            if (pk, cond) in out:
                continue
            path = S4_WAV_DIR / leaf / f"{cond}.wav"
            if not path.is_file():
                raise S4Stop(
                    cause=f"S4 canonical WAV が無い: {path}",
                    impact="再生成が禁止されているため、この条件の音を用意できない",
                    minimal_fix="S4 Phase A の WAV 出力を復元する（再生成・補正はしない）")
            out[(pk, cond)] = (path, sb.sha256_file(path))
    return out


def clip_names(trial: Dict[str, Any]) -> List[Tuple[str, str]]:
    tid = trial["trial_id"]
    return [(f"{tid}_A.wav", trial["a_condition"]),
            (f"{tid}_B.wav", trial["b_condition"]),
            (f"{tid}_X.wav", trial["x_condition"])]


def _closure() -> Dict[str, Any]:
    """S4b も実行コードを pin する（S4 Phase A/C と同じ規律）。

    これが無いと、pack 生成と採点の間に `plan()` / `sb.score()` / verdict 判定を
    書き換えても、記録には入力と音の hash しか残らず「どのコードが走ったか」を
    示せない。
    """
    import s4_runner as _sr  # noqa: PLC0415
    wt = _sr.worktree_state()
    if wt["clean"] is not True:
        reason = ("git 状態を確認できない" if wt["clean"] is None
                  else f"未コミットの変更がある: {wt['entries'][:5]}")
        raise S4Stop(
            cause=f"S4b も clean worktree を要求する（{reason}）",
            impact="選択・採点した実装と記録が対応せず、確認結果が再現不能になる",
            minimal_fix="変更を commit してから実行する")
    return {"worktree": wt, "closure": _sr.closure_digest()}


def prepare() -> int:
    prepare_closure = _closure()
    s4, s4_sha, s3 = load_inputs()
    cells = plan(s4, s3)
    salt = os.urandom(32)
    trials = build_trials(cells, salt)
    resolved = canonical_sources(s4, trials)
    source_pins = {f"{pk}|{cond}": sha for (pk, cond), (_p, sha) in sorted(resolved.items())}

    staging = AUDIO_DIR.with_name(AUDIO_DIR.name + ".staging")
    audio_sha: Dict[str, Dict[str, str]] = _materialize(trials, resolved, staging)
    key = {"schema": SCHEMA, "s3_results_sha256": s4["s3_results_sha256"],
           "s35_results_sha256": s4["s35_results_sha256"],
           "s4_results_sha256": s4_sha,
           "source_wav_sha256": source_pins, "source_wav_pin_note": WAV_PIN_NOTE,
           # 期待クリップ digest は **key 側**に置く。manifest は commitment に
           # 覆われない可変ファイルなので、そこの digest を採点が信用すると
           # 「WAV 差し替え + manifest 書き換え」で全検査が通る。
           "audio_sha256": {k: dict(sorted(v.items()))
                            for k, v in sorted(audio_sha.items())},
           "selection_rule": SELECTION_RULE, "salt_hex": salt.hex(),
           "prepare_closure": prepare_closure, "cells": cells,
           "trials": {t["trial_id"]: {k: t[k] for k in sorted(t) if k != "_uid"}
                      for t in trials}}
    key_raw = sb.canonical_bytes(key)
    commitment = sb.sha256_bytes(key_raw)
    manifest = {"schema": SCHEMA, "s4_results_sha256": s4_sha,
                "trial_ids": [t["trial_id"] for t in trials],
                "audio_sha256": audio_sha, "key_commitment": commitment,
                "instructions": {"ABX": "A と B を聴き、X が A と B のどちらと同じかを"
                                        "答える（A / B / UNSURE）。"}}
    template = {"schema": ANSWERS_SCHEMA, "key_commitment": commitment,
                "answers": {t["trial_id"]: "A|B|UNSURE" for t in trials}}
    RESULTS.mkdir(parents=True, exist_ok=True)
    # 新しい pack を出すので、前回走行の判定成果物は同じ transaction で消す。
    # 残すと「別 commitment の CONFIRMED/NOT_CONFIRMED」が未回答の pack の隣に
    # 並んだままになる。
    srep.publish(
        files=[(PRIVATE_KEY, key_raw),
               (BLIND_MANIFEST, srep._dumps(manifest).encode("utf-8")),
               (ANSWERS.with_name("answers.template.json"),
                srep._dumps(template).encode("utf-8"))],
        dir_swaps=[(staging, AUDIO_DIR)],
        removals=[JSON_PATH, RECORD_PATH, KEY_REVEAL],
        secret=[PRIVATE_KEY])
    print(f"S4b pack ready: {len(trials)} 問 / {AUDIO_DIR}")
    for t in trials:
        print(f"  {t['trial_id']}  (blind)")
    return 0


def _materialize(trials, resolved, staging) -> Dict[str, Dict[str, str]]:
    import shutil  # noqa: PLC0415
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    audio_sha: Dict[str, Dict[str, str]] = {}
    for t in trials:
        per: Dict[str, str] = {}
        for name, cond in clip_names(t):
            if not sb.filename_is_blind(name):
                raise S4Stop(cause=f"filename に意味情報が含まれる: {name}",
                             impact="blind が破れる",
                             minimal_fix="命名規則を確認する")
            src, want = resolved[(t["pair_key"], cond)]
            dst = staging / name
            shutil.copyfile(src, dst)          # byte copy。normalize も変換もしない
            after = sb.sha256_file(dst)
            if after != want:
                raise S4Stop(cause=f"{name}: copy 後の sha が一致しない",
                             impact="S4 canonical と別の音を提示することになる",
                             minimal_fix="コピー元を確認する")
            per[name] = after
        audio_sha[t["trial_id"]] = per
    return audio_sha


def score() -> int:
    score_closure = _closure()
    manifest, _mf_sha = _load(BLIND_MANIFEST, "S4b blind_manifest.json")
    key_raw = PRIVATE_KEY.read_bytes() if PRIVATE_KEY.exists() else b""
    if not key_raw or not sb.verify_commitment(key_raw, manifest):
        raise S4Stop(cause="S4b の key commitment が検証できない",
                     impact="回答後に正解が差し替えられていないことを保証できない",
                     minimal_fix="pack を作り直して回答をやり直す")
    key = json.loads(key_raw.decode("utf-8"))
    # prepare() と --score の間に S4 が回し直されている可能性がある。採点時点の
    # S4 正本を読み直し、pack が作られたときの digest と outcome の両方を確認する。
    # これをしないと「S4 の NOT_ESTABLISHED は不変」と記録しながら、現在の S4 が
    # PASS / BLOCKED / 別走行という状態を作れる。
    _assert_s4_unchanged_at_score_time(key)
    sb.verify_pack_audio(key, AUDIO_DIR)      # 期待値は key 側（manifest は可変）
    trials = [dict(v, trial_id=k) for k, v in key["trials"].items()]
    answers_doc, answers, answers_sha = sb.load_answers(ANSWERS)
    sb.verify_answer_binding(answers_doc, manifest)
    _assert_reveal_idempotent(answers_sha, manifest)
    sb.assert_answers_complete(trials, answers)
    scored = sb.score(trials, answers)

    by_id = {t["trial_id"]: t for t in trials}
    per_cell = [{"trial_id": r["trial_id"], "gene": r["gene"],
                 "context_id": r["context_id"], "answer": r["answer"],
                 "correct": r["correct"],
                 "pair_key": by_id[r["trial_id"]]["pair_key"],
                 "rank": by_id[r["trial_id"]]["rank"],
                 "intervention_amount": by_id[r["trial_id"]]["intervention_amount"],
                 "intervention_unit": by_id[r["trial_id"]]["intervention_unit"]}
                for r in scored["abx"]]
    correct = scored["abx_correct"]
    res = {
        "schema": SCHEMA,
        "purpose": "複合発現時に F0 / Duration の差が別 pair でも耳へ残るかの確認。"
                   "S4 本体の結果は変更しない。",
        "s4_results_sha256": manifest["s4_results_sha256"],
        "selection_rule": key["selection_rule"],
        "selection": key["cells"],
        "audio_source": "S4 canonical WAV の byte copy（再生成・補正・normalize なし）",
        "source_wav_sha256": key.get("source_wav_sha256", {}),
        "source_wav_pin_note": WAV_PIN_NOTE,
        "abx_correct": correct, "abx_total": len(per_cell),
        "cells": per_cell,
        "by_gene": {g: sum(1 for r in per_cell if r["gene"] == g and r["correct"])
                    for g in ("f0", "duration")},
        "by_context": {c: sum(1 for r in per_cell if r["context_id"] == c
                              and r["correct"])
                       for c in sorted({r["context_id"] for r in per_cell})},
        "answers_sha256": answers_sha,
        "key_commitment": manifest["key_commitment"],
        "commitment_verified": True, "pack_audio_verified": True,
        "answer_binding_verified": True,
        "prepare_closure": key.get("prepare_closure"),
        "score_closure": score_closure,
        # 閾値は S4 §15.1 の写し。**新しい裁定ではない**（S4 の verdict は変えない）。
        "verdict": "CONFIRMED" if correct == len(per_cell) else "NOT_CONFIRMED",
        "s4_overall_unchanged": True,
    }
    reveal = {"schema": SCHEMA, "answers_sha256": answers_sha,
              "key_commitment": manifest["key_commitment"],
              "salt_hex": key["salt_hex"], "selection_rule": key["selection_rule"],
              "cells": key["cells"], "trials": key["trials"], "scored": scored}
    srep.publish(files=[(JSON_PATH, srep._dumps(res).encode("utf-8")),
                        (RECORD_PATH, render(res, key).encode("utf-8")),
                        (KEY_REVEAL, srep._dumps(reveal).encode("utf-8"))])
    print(f"S4b {res['verdict']}: ABX {correct}/{len(per_cell)}")
    for r in per_cell:
        print(f"  {r['trial_id']} {r['gene']:8s} {r['context_id']:12s} "
              f"{'OK' if r['correct'] else 'MISS'}")
    return 0 if res["verdict"] == "CONFIRMED" else 1


def _assert_s4_unchanged_at_score_time(key: Dict[str, Any]) -> None:
    """採点時点の S4 正本が、pack を作ったときと同一であること。

    `s4b_results.json` は `s4_overall_unchanged: true` を主張し、`render()` は
    「S4 本体の結果（NOT_ESTABLISHED）」と書く。その主張は**採点時点でも**成り立って
    いなければならない。
    """
    s4, s4_sha = _load(S4_RESULTS, "S4 正本 s4_results.json")
    want = key.get("s4_results_sha256")
    if not want:
        raise S4Stop(
            cause="commitment 済み key に s4_results_sha256 が無い",
            impact="この pack がどの S4 走行に対応するかを採点時点で確認できない",
            minimal_fix="pack を作り直す")
    if s4_sha != want:
        raise S4Stop(
            cause=f"採点時点の S4 正本が pack 作成時と違う "
                  f"({s4_sha[:16]}… != {str(want)[:16]}…)",
            impact="別走行の S4 に対して「NOT_ESTABLISHED は不変」と記録することになる",
            minimal_fix="pack 作成時の s4_results.json を復元するか、pack を作り直す")
    overall = (s4.get("overall") or {}).get("verdict")
    if overall != REQUIRED_S4_OVERALL:
        raise S4Stop(
            cause=f"採点時点の S4 overall が {overall!r}（要求 {REQUIRED_S4_OVERALL!r}）",
            impact="S4b の記録が S4 の実際の結果と矛盾する",
            minimal_fix="S4 の状態を確認して User 裁定へ戻す")


def _assert_reveal_idempotent(answers_sha: str, manifest: Dict[str, Any]) -> None:
    """既に key を開封済みなら、**同じ回答での再実行しか許さない**。

    開封後に `answers.json` を書き換えて再採点できると、正当な NOT_CONFIRMED を
    CONFIRMED へ反転できてしまう。
    """
    if not KEY_REVEAL.exists():
        import s4_runner as _sr  # noqa: PLC0415 — git 参照だけに使う
        tracked = _sr.tracked_in_head(KEY_REVEAL)
        if tracked is True:
            raise S4Stop(
                cause=f"開封済みの {KEY_REVEAL.name} が worktree から消えている",
                impact="正解を見たあとで reveal を消し、回答を書き換えて"
                       "「初回採点」として通す経路になる",
                minimal_fix="git から reveal を復元する。やり直すなら pack を作り直す")
        if tracked is None:
            raise S4Stop(
                cause="git が使えず、reveal が未生成なのか消されたのか判別できない",
                impact="開封後の回答差し替えを検出できないまま採点することになる",
                minimal_fix="git が使える環境で実行する")
        return
    try:
        reveal = json.loads(KEY_REVEAL.read_bytes().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise S4Stop(cause=f"既存の S4b key_reveal.json が読めない: {exc}",
                     impact="開封後の回答差し替えを検出できない",
                     minimal_fix="key_reveal.json の破損を確認する") from exc
    prev = reveal.get("answers_sha256")
    if prev and prev != answers_sha:
        raise S4Stop(
            cause=f"key 開封後に回答が変わっている "
                  f"({str(prev)[:16]}… -> {answers_sha[:16]}…)",
            impact="正解を見たあとで回答を書き換え、NOT_CONFIRMED を CONFIRMED へ"
                   "反転できてしまう",
            minimal_fix="この pack の判定は確定済み。やり直すなら pack を作り直す")
    if reveal.get("key_commitment") and \
            reveal["key_commitment"] != manifest.get("key_commitment"):
        raise S4Stop(cause="既存 key_reveal が別の pack のものである",
                     impact="開封済み pack と現在の pack が食い違ったまま再採点される",
                     minimal_fix="pack を作り直す")


def render(res: Dict[str, Any], key: Dict[str, Any]) -> str:
    lines = ["# S4B RECORD — Perceptual Coexpression Confirmation", "",
             f"- schema: `{res['schema']}`",
             f"- s4_results_sha256: `{res['s4_results_sha256']}`",
             f"- 音源: {res['audio_source']}", "",
             "## 位置づけ", "",
             "複合発現時に F0 / Duration の差が **別 pair でも**耳へ残るかの確認。",
             "**S4 本体の結果（NOT_ESTABLISHED）は変更しない。**"
             " 判定閾値は S4 §15.1 の写しであり、新しい裁定ではない。", "",
             "## 結果", "",
             f"**{res['verdict']}** — ABX {res['abx_correct']}/{res['abx_total']} 正解",
             "",
             "| 問 | gene | context | 提示 | 正解 | 回答 | |",
             "|---|---|---|---|---|---|---|"]
    for r in res["cells"]:
        t = key["trials"][r["trial_id"]]
        lines.append(
            f"| {r['trial_id']} | {r['gene']} | {r['context_id']} "
            f"| {t['a_condition']} vs {t['b_condition']} | {t['correct']} "
            f"| {r['answer']} | {'OK' if r['correct'] else 'MISS'} |")
    lines += ["",
              "- gene 別正解: "
              + " / ".join(f"{g} {res['by_gene'][g]}" for g in sorted(res["by_gene"])),
              "- context 別正解: "
              + " / ".join(f"{c} {res['by_context'][c]}"
                           for c in sorted(res["by_context"])), "",
              "## 事前選択（耳では選ばない）", "",
              f"選択規則: `{res['selection_rule']}`", "",
              "| gene | rank | context | pair | intervention | identity margin |",
              "|---|---|---|---|---|---|"]
    for c in res["selection"]:
        lines.append(
            f"| {c['gene']} | {c['rank']} | {c['context_id']} "
            f"| `{c['pair_key'].replace('|', chr(92) + '|')}` "
            f"| {c['intervention_amount']:.3f} {c['intervention_unit']} "
            f"| {c['identity_margin_db']:+.3f} dB |")
    lines += ["", "## Notes", "",
              "- 各セル 1 問・偶然一致 1/2。統計的検定ではなく確認である。",
              "- WAV / private key / answers は commit しない。",
              f"- **境界宣言**: {res['source_wav_pin_note']}。"
              "S4 の記録を後付けで書き換えるのは「S4 本体の結果は変更しない」に"
              "反するため行わない（次回 S4 走行で wav_sha256 を §21 出力へ載せるのが"
              "本来の是正）。", ""]
    return "\n".join(lines)


def main(argv: Sequence[str]) -> int:
    try:
        return score() if "--score" in argv else prepare()
    except S4Stop as stop:
        print(f"{stop.status}: {stop.cause}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
