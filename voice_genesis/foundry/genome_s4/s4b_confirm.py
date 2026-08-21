"""genome_s4/s4b_confirm.py — S4b Perceptual Coexpression Confirmation（User 指示 2026-08-21）。

目的は 1 つだけ。

> 複合発現時に F0 / Duration の差が **別 pair でも**耳へ残るかを確認する。

**S4 本体の結果は変更しない。** 出力は `results/s4b/` に隔離し、
`s4_results.json` / `S4_RECORD.md` / freeze 判定には一切触れない。

制約:

- 使用音源は **S4 canonical WAV のみ**。再生成・補正・normalize は行わない
  （コピー前後で `s4_results.json` が記録した `wav_sha256` と突き合わせる）
- 対象 context は `terminal_i` / `terminal_ri`
- pair は S4 耳判定で**使わなかった** SUPPORTED(=COMBINABLE) pair を各 context から
  決定論的に 1 件
- 4 問（context 2 × gene 2）。blind 規律は S4 §14 と同じ commitment 方式
"""
from __future__ import annotations

import hashlib
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
ANSWERS_SCHEMA = "voicegenesis-s4b-answers/1.0"

#: pair 選択ハッシュの領域分離プレフィクス。S4 本体（`voicegenesis-s4-ear-v1`）と
#: 分けることで、同じ正本でも S4b は別の順序で選ぶ（同じ pair を引き当てない）。
SELECTION_DOMAIN = "voicegenesis-s4b-ear-v1"

S4_RESULTS = _HERE / "results" / "s4_results.json"
S4_KEY_REVEAL = _HERE / "results" / "key_reveal.json"
S4_WAV_DIR = _HERE / "results" / "wav"

RESULTS = _HERE / "results" / "s4b"
AUDIO_DIR = RESULTS / "audio"
BLIND_MANIFEST = RESULTS / "blind_manifest.json"
PRIVATE_KEY = RESULTS / "answer_key.private.json"
KEY_REVEAL = RESULTS / "key_reveal.json"
ANSWERS = RESULTS / "answers.json"
JSON_PATH = RESULTS / "s4b_results.json"
RECORD_PATH = RESULTS / "S4B_RECORD.md"

#: User 指示の 4 問。(gene, 背景条件, 複合条件)。差は対象 gene の追加だけ。
QUESTIONS: Tuple[Tuple[str, str, str], ...] = (
    ("f0", "D", "FD"),          # D vs FD -> F0 追加を識別できるか
    ("duration", "F", "FD"),    # F vs FD -> Duration 追加を識別できるか
)
CONTEXTS: Tuple[str, ...] = ("terminal_i", "terminal_ri")
TOTAL = len(CONTEXTS) * len(QUESTIONS)


def selection_hash(s3_sha: str, s35_sha: str, context_id: str, pair_key: str) -> str:
    h = hashlib.sha256()
    h.update((SELECTION_DOMAIN + s3_sha + s35_sha + context_id + pair_key)
             .encode("utf-8"))
    return h.hexdigest()


def _load(path: Path, what: str) -> Dict[str, Any]:
    if not path.exists():
        raise S4Stop(cause=f"{what} が無い（{path}）",
                     impact="S4b の入力を確定できない",
                     minimal_fix="先に S4 の Phase A / Phase C を完了させる")
    return json.loads(path.read_bytes().decode("utf-8"))


def load_inputs() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """S4 正本と S4 の key_reveal（= 耳判定で使った pair）を読む。"""
    s4 = _load(S4_RESULTS, "S4 正本 s4_results.json")
    if (s4.get("mechanistic") or {}).get("verdict") != "PASS":
        raise S4Stop(
            cause=f"S4 の機械 Gate が {(s4.get('mechanistic') or {}).get('verdict')!r}",
            impact="canonical WAV が機械 Gate を通っていない条件で耳判定を足すことになる",
            minimal_fix="S4 の Phase A を完了させる")
    reveal = _load(S4_KEY_REVEAL, "S4 key_reveal.json")
    return s4, reveal


def select_pairs(s4: Dict[str, Any], reveal: Dict[str, Any]) -> Dict[str, str]:
    """各 context から **S4 で使わなかった** COMBINABLE pair を決定論的に 1 件選ぶ。"""
    used = set((reveal.get("selected_pairs") or {}).values())
    s3_sha = str(s4["s3_results_sha256"])
    s35_sha = str(s4["s35_results_sha256"])
    out: Dict[str, str] = {}
    for ctx in CONTEXTS:
        cands = [pk for pk, r in (s4["mechanistic"]["pairs"]).items()
                 if r.get("context_id") == ctx
                 and r.get("verdict") == sp.PairVerdict.COMBINABLE.value
                 and pk not in used]
        if not cands:
            raise S4Stop(
                cause=f"{ctx}: S4 耳判定で未使用の COMBINABLE pair が無い",
                impact="別 pair での確認という S4b の目的が成立しない",
                minimal_fix="対象 context を勝手に差し替えず User 裁定へ戻す")
        out[ctx] = min(cands, key=lambda pk: selection_hash(s3_sha, s35_sha, ctx, pk))
    return out


def build_trials(selected: Dict[str, str], salt: bytes) -> List[Dict[str, Any]]:
    trials: List[Dict[str, Any]] = []
    for ctx in CONTEXTS:
        pair_key = selected[ctx]
        for gene, background, combined in QUESTIONS:
            uid = f"s4b|{gene}|{ctx}|{pair_key}"
            a_is_bg = sb._bit(salt, "ab|" + uid) == 0
            x_is_bg = sb._bit(salt, "x|" + uid) == 0
            a_cond = background if a_is_bg else combined
            b_cond = combined if a_is_bg else background
            x_cond = background if x_is_bg else combined
            trials.append({"kind": "ABX", "gene": gene, "context_id": ctx,
                           "pair_key": pair_key, "a_condition": a_cond,
                           "b_condition": b_cond, "x_condition": x_cond,
                           "correct": "A" if a_cond == x_cond else "B", "_uid": uid})
    trials.sort(key=lambda t: sb._order_key(salt, t["_uid"]))
    for i, t in enumerate(trials, start=1):
        t["trial_id"] = f"Q{i:03d}"
    if len(trials) != TOTAL:
        raise S4Stop(cause=f"S4b の問数が {len(trials)}（要求 {TOTAL}）",
                     impact="事前に決めた問数と違う pack は確認に使えない",
                     minimal_fix="問数を変えない。原因を User 裁定へ戻す")
    return trials


#: **境界宣言**: `s4_results.json` は per-file の `wav_sha256` を記録していない
#: （`ConditionOutput.wav_sha256` は runner の中間表現に留まり、§21 の出力
#: スキーマへ載らない）。したがって S4b は「S4 が pin した digest との突き合わせ」
#: を行えない。ここで採るのは **S4b が実際に読んだ bytes の self-pin** であり、
#: S4 側の pin との cross-check ではない。偽の検証を作らずに範囲を明示する。
#: S4 の記録を後付けで書き換えるのは「S4 本体の結果は変更しない」に反するため
#: 行わない（次回 S4 走行で `wav_sha256` を §21 へ載せるのが本来の是正）。
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


def prepare() -> int:
    s4, reveal = load_inputs()
    selected = select_pairs(s4, reveal)
    salt = os.urandom(32)
    trials = build_trials(selected, salt)
    resolved = canonical_sources(s4, trials)
    source_pins = {f"{pk}|{cond}": sha for (pk, cond), (_p, sha) in sorted(resolved.items())}

    staging = AUDIO_DIR.with_name(AUDIO_DIR.name + ".staging")
    audio_sha: Dict[str, Dict[str, str]] = _materialize(trials, resolved, staging)
    key = {"schema": SCHEMA, "s3_results_sha256": s4["s3_results_sha256"],
           "s35_results_sha256": s4["s35_results_sha256"],
           "source_wav_sha256": source_pins, "source_wav_pin_note": WAV_PIN_NOTE,
           "salt_hex": salt.hex(), "selected_pairs": dict(sorted(selected.items())),
           "trials": {t["trial_id"]: {k: t[k] for k in sorted(t) if k != "_uid"}
                      for t in trials}}
    key_raw = sb.canonical_bytes(key)
    commitment = sb.sha256_bytes(key_raw)
    manifest = {"schema": SCHEMA, "s4_results_sha256": sb.sha256_file(S4_RESULTS),
                "trial_ids": [t["trial_id"] for t in trials],
                "audio_sha256": audio_sha, "key_commitment": commitment,
                "instructions": {"ABX": "A と B を聴き、X が A と B のどちらと同じかを"
                                        "答える（A / B / UNSURE）。"}}
    template = {"schema": ANSWERS_SCHEMA, "key_commitment": commitment,
                "answers": {t["trial_id"]: "A|B|UNSURE" for t in trials}}
    RESULTS.mkdir(parents=True, exist_ok=True)
    srep.publish(
        files=[(PRIVATE_KEY, key_raw),
               (BLIND_MANIFEST, srep._dumps(manifest).encode("utf-8")),
               (ANSWERS.with_name("answers.template.json"),
                srep._dumps(template).encode("utf-8"))],
        dir_swaps=[(staging, AUDIO_DIR)], secret=[PRIVATE_KEY])
    print(f"S4b pack ready: {TOTAL} 問 / {AUDIO_DIR}")
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
    manifest = _load(BLIND_MANIFEST, "S4b blind_manifest.json")
    key_raw = PRIVATE_KEY.read_bytes() if PRIVATE_KEY.exists() else b""
    if not key_raw or not sb.verify_commitment(key_raw, manifest):
        raise S4Stop(cause="S4b の key commitment が検証できない",
                     impact="回答後に正解が差し替えられていないことを保証できない",
                     minimal_fix="pack を作り直して回答をやり直す")
    sb.verify_pack_audio(manifest, AUDIO_DIR)
    key = json.loads(key_raw.decode("utf-8"))
    trials = [dict(v, trial_id=k) for k, v in key["trials"].items()]
    answers_doc, answers, answers_sha = sb.load_answers(ANSWERS)
    sb.verify_answer_binding(answers_doc, manifest)
    scored = sb.score(trials, answers)

    per_cell = [{"trial_id": r["trial_id"], "gene": r["gene"],
                 "context_id": r["context_id"], "answer": r["answer"],
                 "correct": r["correct"]} for r in scored["abx"]]
    correct = scored["abx_correct"]
    res = {
        "schema": SCHEMA,
        "purpose": "複合発現時に F0 / Duration の差が別 pair でも耳へ残るかの確認。"
                   "S4 本体の結果は変更しない。",
        "s4_results_sha256": manifest["s4_results_sha256"],
        "selected_pairs": key["selected_pairs"],
        "audio_source": "S4 canonical WAV の byte copy（再生成・補正・normalize なし）",
        "source_wav_sha256": key.get("source_wav_sha256", {}),
        "source_wav_pin_note": WAV_PIN_NOTE,
        "abx_correct": correct, "abx_total": len(per_cell),
        "cells": per_cell,
        "by_gene": {g: sum(1 for r in per_cell if r["gene"] == g and r["correct"])
                    for g in ("f0", "duration")},
        "by_context": {c: sum(1 for r in per_cell if r["context_id"] == c
                              and r["correct"]) for c in CONTEXTS},
        "answers_sha256": answers_sha,
        "key_commitment": manifest["key_commitment"],
        "commitment_verified": True, "pack_audio_verified": True,
        "answer_binding_verified": True,
        # 閾値は S4 §15.1 の写し。**新しい裁定ではない**（S4 の verdict は変えない）。
        "verdict": "CONFIRMED" if correct == len(per_cell) else "NOT_CONFIRMED",
        "s4_overall_unchanged": True,
    }
    reveal = {"schema": SCHEMA, "answers_sha256": answers_sha,
              "salt_hex": key["salt_hex"], "selected_pairs": key["selected_pairs"],
              "trials": key["trials"], "scored": scored}
    srep.publish(files=[(JSON_PATH, srep._dumps(res).encode("utf-8")),
                        (RECORD_PATH, render(res, key).encode("utf-8")),
                        (KEY_REVEAL, srep._dumps(reveal).encode("utf-8"))])
    print(f"S4b {res['verdict']}: ABX {correct}/{len(per_cell)}")
    for r in per_cell:
        print(f"  {r['trial_id']} {r['gene']:8s} {r['context_id']:12s} "
              f"{'OK' if r['correct'] else 'MISS'}")
    return 0 if res["verdict"] == "CONFIRMED" else 1


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
              f"- gene 別正解: f0 {res['by_gene']['f0']}/2 / "
              f"duration {res['by_gene']['duration']}/2",
              "- context 別正解: "
              + " / ".join(f"{c} {res['by_context'][c]}/2" for c in CONTEXTS), "",
              "## 対象 pair（S4 耳判定で未使用）", ""]
    for ctx, pk in sorted(res["selected_pairs"].items()):
        lines.append(f"- `{ctx}`: `{pk.replace('|', chr(92) + '|')}`")
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
