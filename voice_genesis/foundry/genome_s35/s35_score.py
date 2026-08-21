"""genome_s35/s35_score.py — Stage 1 / Stage 2 の採点。

**回答凍結後だけ実行する。** 採点前に正解を表示しない。
各問終了時に正誤を出さない（stage 単位でまとめて採点する）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s35_prepare as prep  # noqa: E402
import s35_spec as sp  # noqa: E402

RESULTS = prep.RESULTS
KEY_REVEAL = RESULTS / "key_reveal.json"


def answers_path(stage: int) -> Path:
    return RESULTS / f"answers_stage{stage}.json"


def load_answers(stage: int, path: Optional[Path] = None) -> Tuple[Dict[str, Any], str]:
    """回答ファイルを読み、**そのままの bytes** の SHA を凍結する。"""
    path = answers_path(stage) if path is None else path
    if not path.exists():
        raise prep.S35Stop(reason=f"Stage {stage} の回答ファイルが無い（{path}）",
                           required_action=f"聴取後の answers_stage{stage}.json を配置する")
    raw = path.read_bytes()
    data = prep.json_object(raw, f"Stage {stage} の回答ファイル", "回答 JSON を確認する")
    if data.get("schema") != sp.ANSWERS_SCHEMA:
        raise prep.S35Stop(reason=f"回答 schema が不正: {data.get('schema')!r}",
                           required_action=f"schema を {sp.ANSWERS_SCHEMA} にする")
    # `stage` は**実際の整数**を要求する。欠落を要求 stage で補うと壊れた来歴を
    # 黙って受け入れ、null / dict / 非数値文字列だと `int()` が S35Stop 以外の
    # 例外を投げて BLOCKED 記録が出せなくなる。
    got_stage = data.get("stage")
    if not isinstance(got_stage, int) or isinstance(got_stage, bool) or got_stage != stage:
        raise prep.S35Stop(
            reason=f"回答ファイルの stage が不正: {got_stage!r} (期待 {stage} / int)",
            required_action="回答ファイルに stage を整数で書く")
    # 誰が聴いたか記録に無い session を canonical にしない。`None == None` を
    # 素通りさせると、匿名の回答が `listener_count = 1` を名乗れてしまう。
    for field in ("listener_id", "session_id"):
        v = data.get(field)
        if not isinstance(v, str) or not v.strip():
            raise prep.S35Stop(
                reason=f"Stage {stage} の {field} が空または欠落: {v!r}",
                required_action="回答ファイルに listener_id / session_id を書く")
    # `answers` は**必ず dict**。真値の非 dict（`"A"` や非空 list）は
    # `check_complete()` の `.get()` / `.items()` で `AttributeError` を投げ、
    # `finalize_or_blocked()` が拾えず BLOCKED 記録を出せなくなる。
    answers = data.get("answers")
    if not isinstance(answers, dict):
        raise prep.S35Stop(
            reason=f"Stage {stage} の answers が dict でない: {type(answers).__name__}",
            required_action="answers を {trial_id: A/B/UNSURE} の object にする")
    # **各値の型まで見る。** 器の形だけ見て中身を見ないと、`["A"]` や `{"answer":"A"}`
    # が `check_complete()` の集合判定に届き、unhashable で `TypeError` になる
    # （`S35Stop` でないので BLOCKED 記録が出せない）。器と中身は同じ穴の
    # 別の深さであって、別のファミリーではない。
    bad_types = sorted(t for t, v in answers.items() if not isinstance(v, str))
    if bad_types:
        raise prep.S35Stop(
            reason=f"Stage {stage} の回答値が文字列でない: "
                   f"{[(t, type(answers[t]).__name__) for t in bad_types[:3]]}",
            required_action="各回答を A / B / UNSURE の文字列にする")
    return data, prep.sha256_bytes(raw)


def check_complete(answers: Dict[str, str], trial_ids: List[str], stage: int) -> None:
    missing = [t for t in trial_ids if not answers.get(t)]
    extra = [t for t in answers if t not in trial_ids]
    if missing:
        raise prep.S35Stop(reason=f"Stage {stage} に未回答の trial がある: "
                                  f"{len(missing)} 件 (例 {missing[:3]})",
                           required_action="全 trial に A / B / UNSURE を入れる")
    if extra:
        raise prep.S35Stop(reason=f"manifest に無い trial の回答がある: {extra[:3]}",
                           required_action="回答ファイルを blind manifest に合わせる")
    bad = {t: v for t, v in answers.items() if v not in {a.value for a in sp.Answer}}
    if bad:
        raise prep.S35Stop(reason=f"A / B / UNSURE 以外の回答がある: {list(bad.items())[:3]}",
                           required_action="回答語彙を A / B / UNSURE に限定する")


def verify_stage_audio(manifest: Dict[str, Any], stage: int) -> Tuple[bool, Dict[str, Any]]:
    """配布した pack の WAV が manifest の SHA と一致することを確認する。

    **被覆そのものを先に検査する。** `audio_sha256` が空だったり trial/slot を
    取りこぼしていると、ループが 0 回または部分的に回っただけで "verified" に
    なってしまう（WAV を全部消しても true が出る）。
    """
    block = prep.manifest_stage_block(manifest, stage)
    trial_ids = list(block.get("trial_ids") or [])
    table = block.get("audio_sha256") or {}
    bad: List[str] = []

    if not trial_ids:
        return False, {"checked": 0, "problems": [f"stage {stage} に trial_ids が無い"],
                       "problem_count": 1}
    if set(table) != set(trial_ids):
        missing = sorted(set(trial_ids) - set(table))
        extra = sorted(set(table) - set(trial_ids))
        return False, {"checked": 0,
                       "problems": [f"audio_sha256 の被覆が trial_ids と一致しない "
                                    f"(欠落 {missing[:3]} / 余分 {extra[:3]})"],
                       "problem_count": 1}
    for tid in trial_ids:
        per = table.get(tid) or {}
        if set(per) != {"A", "B", "X"}:
            bad.append(f"{tid}: slot が A/B/X で揃っていない ({sorted(per)})")
            continue
        for slot, want in per.items():
            if not isinstance(want, str) or len(want) != 64:
                bad.append(f"{tid}_{slot}: SHA の形式が不正")
                continue
            p = prep.stage_audio_dir(stage) / f"{tid}_{slot}.wav"
            if not p.exists():
                bad.append(f"{p.name} 欠落")
                continue
            if prep.sha256_file(p) != want:
                bad.append(f"{p.name} SHA 不一致")
    expected = len(trial_ids) * 3
    checked = expected - len(bad)
    return (not bad), {"checked": checked, "expected": expected,
                       "problems": bad[:5], "problem_count": len(bad)}


def assert_stage_block_matches_key(manifest: Dict[str, Any], key: Dict[str, Any],
                                   stage: int) -> None:
    """manifest の stage block が、**commit 済み key のその stage の trial** だけを
    指していることを検査する。

    `trial_ids` を引いて `key["trials"][tid]` を見るだけだと、stage の帰属を
    確かめていないので、Stage 2 の枠に Stage 1 の trial を並べて音声をコピーすれば
    「1 文脈しか無いのに 2 文脈で識別した」という記録が作れてしまう。
    stage 帰属・gene の一意性・pair/probe_kind の一致まで commit 済み plan と
    突き合わせる。
    """
    block = prep.manifest_stage_block(manifest, stage)
    ids = list(block.get("trial_ids") or [])
    trials = key.get("trials") or {}
    plans = key.get("plans") or {}

    committed = {tid for tid, spec in trials.items() if spec.get("stage") == stage}
    unknown = [t for t in ids if t not in trials]
    if unknown:
        raise prep.S35Stop(
            reason=f"Stage {stage} の manifest に key へ無い trial がある: {unknown[:3]}",
            required_action="manifest を commit 済みの key に合わせる")
    wrong_stage = [t for t in ids if trials[t].get("stage") != stage]
    if wrong_stage:
        raise prep.S35Stop(
            reason=f"Stage {stage} の manifest に別 stage の trial が混ざっている: "
                   f"{wrong_stage[:3]}",
            required_action="manifest の stage block を commit 済みの stage 帰属へ戻す")
    extra = [t for t in ids if t not in committed]
    if extra:
        raise prep.S35Stop(
            reason=f"Stage {stage} の manifest に commit 外の trial がある: {extra[:3]}",
            required_action="manifest を commit 済みの key に合わせる")
    if len(set(ids)) != len(ids):
        raise prep.S35Stop(reason=f"Stage {stage} の trial_ids に重複がある",
                           required_action="manifest の trial_ids を一意にする")
    genes = [trials[t]["gene"] for t in ids]
    if len(set(genes)) != len(genes):
        raise prep.S35Stop(
            reason=f"Stage {stage} で同じ gene が複数回出題されている: {sorted(genes)}",
            required_action="1 stage 1 gene 1 問に戻す")
    if stage == sp.STAGE1 and set(ids) != committed:
        raise prep.S35Stop(
            reason=f"Stage 1 の trial_ids が commit 済み集合と一致しない "
                   f"(欠落 {sorted(committed - set(ids))[:3]})",
            required_action="Stage 1 は全 gene 分を出題する")
    # 各 trial が commit 済み plan の pair / probe_kind と一致すること
    for tid in ids:
        spec = trials[tid]
        want = (plans.get(spec["gene"]) or {}).get(f"stage{stage}") or {}
        for field in ("pair_key", "probe_kind"):
            if want.get(field) != spec.get(field):
                raise prep.S35Stop(
                    reason=f"{tid}: {field} が commit 済み plan と違う "
                           f"({spec.get(field)!r} != {want.get(field)!r})",
                    required_action="commit 済みの plan へ戻す。差し替えは blind の破壊",
                    affected_gene=spec.get("gene"))


def score_stage(stage: int, path: Optional[Path] = None) -> Dict[str, Any]:
    """1 stage を採点する。`UNSURE` は正答に数えない。"""
    manifest = prep.load_manifest()
    key, key_sha = prep.load_private_key()
    commitment_ok = manifest.get("key_commitment") == key_sha

    block = prep.manifest_stage_block(manifest, stage) or None
    if not block:
        raise prep.S35Stop(reason=f"Stage {stage} が manifest に無い",
                           required_action=f"Stage {stage} の pack を先に生成する")
    assert_stage_block_matches_key(manifest, key, stage)
    doc, ans_sha = load_answers(stage, path)
    answers = doc["answers"]          # `load_answers` が dict を保証済み
    check_complete(answers, block["trial_ids"], stage)
    audio_ok, audio_detail = verify_stage_audio(manifest, stage)

    per_gene: Dict[str, Dict[str, Any]] = {}
    for tid in block["trial_ids"]:
        spec = key["trials"][tid]
        given = answers.get(tid, "")
        per_gene[spec["gene"]] = {
            "trial_id": tid, "answer": given,
            "correct": given == spec["correct"],      # UNSURE は一致しない
            "pair_key": spec["pair_key"], "probe_kind": spec["probe_kind"],
        }
    return {"stage": stage, "answers_sha256": ans_sha,
            "listener_id": doc.get("listener_id"), "session_id": doc.get("session_id"),
            "commitment_verified": commitment_ok,
            "audio_verified": audio_ok, "audio_detail": audio_detail,
            "by_gene": per_gene}


def advancing_from_stage1(stage1: Dict[str, Any]) -> List[str]:
    return sp.advancing_genes({g: v["correct"] for g, v in stage1["by_gene"].items()})


def build_key_reveal(key: Dict[str, Any], key_sha: str, commitment: str,
                     answers_sha: Dict[int, str]) -> Tuple[Dict[str, Any], str, str]:
    """開示文書を**作るだけ**（書かない）。`(doc, text, sha256)` を返す。

    書き込みは `s35_report.main()` が確定記録ガードを通した**後**に、
    JSON / Markdown と同じ束で行う。公開の可否を決めるガードより前に
    成果物を書くと、拒否された再実行でも開示が差し替わってしまう。
    """
    reveal = {
        "protocol_version": key.get("protocol_version"),
        "revealed_after_answers_sha256": {str(k): v for k, v in sorted(answers_sha.items())},
        "key_commitment": commitment,
        "key_sha256": key_sha,
        "commitment_verified": commitment == key_sha,
        # **commitment の原像そのもの。** これが無いと第三者は
        # `sha256(canonical_bytes(key)) == key_commitment` を再計算できず、
        # `commitment_verified: true` が検証不能な自己申告のまま下流へ流れる
        # （commitment 方式の目的は第三者検証なので、それでは意味を成さない）。
        # `salt_hex` / `s3_results_sha256` は開示後の秘匿価値がゼロ —
        # 全正解は下の `trials` で既に公開されている。
        "key_preimage": {k: key[k] for k in sorted(key)},
        "key_preimage_note": ("sha256(canonical_bytes(key_preimage)) == key_commitment "
                              "を再計算して検証できる"),
        "plans": key.get("plans"),
        "trials": key["trials"],
    }
    text = prep._dumps(reveal)
    return reveal, text, prep.sha256_bytes(text.encode("utf-8"))


def _assert_same_listener(s1: Dict[str, Any], s2: Optional[Dict[str, Any]]) -> None:
    """Stage 間で聴取者が入れ替わっていないこと。

    別人が 1 文脈ずつ正解して `PERCEPTIBLE_CANDIDATE` が立つと、記録は
    `listener_count = 1` のまま「1 人が 2 文脈で識別した」と偽ることになる。
    各 stage 単体の来歴（空・欠落）は `load_answers` が先に弾く。
    """
    if s2 is None:
        return
    for field in ("listener_id", "session_id"):
        if s1.get(field) != s2.get(field):
            raise prep.S35Stop(
                reason=f"Stage 間で {field} が違う: "
                       f"stage1={s1.get(field)!r} / stage2={s2.get(field)!r}",
                required_action="同一聴取者・同一 session の回答に揃える。"
                                "別人が答えたなら session 全体を INVALID として"
                                "新規 session でやり直す")


def _assert_not_rescoring_after_reveal(answers_sha: Dict[int, str], key_sha: str) -> None:
    """**正解が開示された後の採点し直しを拒否する。**

    `key_reveal.json` は全問の正解を含む。これが既に存在する状態で回答を
    書き換えて再採点できると、開示済みの session から `S4_NOT_READY` を
    `S4_READY` へ作り替えられてしまう（commitment も audio も true のまま）。
    同一の回答・同一の鍵での再実行だけは冪等な再描画として許す。
    """
    if not KEY_REVEAL.exists():
        return
    prev = prep.json_object(
        KEY_REVEAL.read_bytes(), "既存の key_reveal.json",
        "開示済み成果物を復元するか、session 全体をやり直す")
    now = {str(k): v for k, v in sorted(answers_sha.items())}
    was = prep.require_mapping(
        prev.get("revealed_after_answers_sha256"),
        "既存 key_reveal.json の revealed_after_answers_sha256",
        "開示済み成果物を復元するか、session 全体をやり直す")
    # 手元にある stage 分だけで先に比較できる（Stage 2 を採点する前に、
    # Stage 1 の書き換えを検出したい）。両方揃っていれば全体比較になる。
    was_subset = {k: v for k, v in was.items() if k in now}
    if was_subset != now or (len(now) == len(was) and was != now):
        raise prep.S35Stop(
            reason="正解開示後に回答が変わっている（再採点は禁止）: "
                   f"開示時 {was} -> 現在 {now}",
            required_action="開示済み session の結果はそのまま残す。やり直すなら"
                            "新規に事前登録した別 session として実施する")
    if prev.get("key_sha256") not in (None, key_sha):
        raise prep.S35Stop(
            reason="正解開示後に private key が変わっている",
            required_action="開示時の鍵を復元する。鍵の差し替えは blind の破壊")


def finalize(stage1_path: Optional[Path] = None,
             stage2_path: Optional[Path] = None) -> Dict[str, Any]:
    """Stage 1 + Stage 2 を採点して gene verdict と S4 gate を出す。"""
    manifest = prep.load_manifest()
    key, key_sha = prep.load_private_key()
    commitment = manifest.get("key_commitment", "")

    s1 = score_stage(sp.STAGE1, stage1_path)
    # 開示後に Stage 1 の回答が書き換わっていれば、Stage 2 へ進む前に止める
    # （後段の被覆検査より先に、根本原因を報告するため）。
    _assert_not_rescoring_after_reveal({sp.STAGE1: s1["answers_sha256"]}, key_sha)
    advancing = advancing_from_stage1(s1)

    s2: Optional[Dict[str, Any]] = None
    has_stage2_block = bool(prep.manifest_stage_block(manifest, sp.STAGE2))
    plans_early = key.get("plans") or {}
    # **別 context が無い gene は Stage 2 を配布できない**（配布しないのが正しい）。
    # これを「Stage 2 待ち」に数えると、正常な走行が false BLOCKED で終わる。
    owed = [g for g in advancing if (plans_early.get(g) or {}).get("stage2")]
    if owed and has_stage2_block:
        # **部分的な Stage 2 pack を受け付けない。** 一部の gene を欠いたまま
        # 採点すると、欠けた gene が INVALID になる一方で残りが S4_READY を作り、
        # プロトコル的に不完全な走行が「成功」で終わる。
        block2 = prep.manifest_stage_block(manifest, sp.STAGE2)
        covered = {(key["trials"].get(t) or {}).get("gene")
                   for t in (block2.get("trial_ids") or [])}
        # 検査するのは**欠落**（owed ⊆ covered）。過剰被覆は無害で、
        # Stage 1 を落ちた gene に Stage 2 結果が付いても verdict は
        # NOT_ESTABLISHED のまま変わらない（鍵の改竄は commitment 検査が拾う）。
        missing = sorted(set(owed) - covered)
        if missing:
            raise prep.S35Stop(
                reason=f"Stage 2 pack が owed gene を覆っていない（欠落 {missing}）: "
                       f"owed={sorted(owed)} / covered={sorted(c for c in covered if c)}",
                required_action="prepare_stage2() へ advancing gene を全て渡して"
                                "配布し直す。部分 pack での採点はしない")
        s2 = score_stage(sp.STAGE2, stage2_path)
    elif owed and not has_stage2_block:
        raise prep.S35Stop(
            reason=f"Stage 1 を通過し Stage 2 pair も確定している gene があるのに "
                   f"Stage 2 pack が無い: {owed}",
            required_action="prepare_stage2() で Stage 2 を配布し、回答を得る")

    answers_sha = {sp.STAGE1: s1["answers_sha256"]}
    if s2:
        answers_sha[sp.STAGE2] = s2["answers_sha256"]
    _assert_same_listener(s1, s2)
    _assert_not_rescoring_after_reveal(answers_sha, key_sha)

    plans = key.get("plans") or {}
    genes: Dict[str, Any] = {}
    for gene, g1 in s1["by_gene"].items():
        plan = plans.get(gene) or {}
        has_s2_pair = plan.get("stage2") is not None
        g2 = (s2 or {}).get("by_gene", {}).get(gene)
        verdict = sp.gene_verdict(
            g1["correct"], (g2 or {}).get("correct"),
            has_stage2_pair=has_s2_pair,
            commitment_verified=s1["commitment_verified"] and (
                s2["commitment_verified"] if s2 else True),
            audio_verified=s1["audio_verified"] and (
                s2["audio_verified"] if s2 else True))
        genes[gene] = {
            "verdict": verdict.value,
            "stage1": g1,
            "stage2": g2,
            "has_stage2_pair": has_s2_pair,
            # 事前 commit 済みの Stage 2 pair。Stage 1 で落ちて**出題しなかった**
            # gene でも key には入っているので、記録側でも残す
            # （`has_stage2_pair: true` なのに pair が空だと記録が自己矛盾する）。
            "stage2_committed": plan.get("stage2"),
            "stage2_presented": g2 is not None,
            "not_evaluable_reason": plan.get("not_evaluable_reason"),
            "contexts": sorted({c for c in (g1["probe_kind"],
                                            (g2 or {}).get("probe_kind")) if c}),
        }
    # Stage 1 すら作れなかった gene（plan に stage1 が無い）も記録に残す
    for gene, plan in plans.items():
        if gene in genes or plan.get("stage1") is not None:
            continue
        genes[gene] = {"verdict": sp.GeneVerdict.NOT_EVALUABLE_S35.value,
                       "stage1": None, "stage2": None, "has_stage2_pair": False,
                       "stage2_committed": plan.get("stage2"),
                       "stage2_presented": False,
                       "not_evaluable_reason": plan.get("not_evaluable_reason"),
                       "contexts": []}

    _reveal, reveal_text, reveal_sha = build_key_reveal(
        key, key_sha, commitment, answers_sha)

    candidates = sorted(g for g, v in genes.items()
                        if v["verdict"] == sp.GeneVerdict.PERCEPTIBLE_CANDIDATE.value)
    return {
        "s3_results_sha256": manifest.get("s3_results_sha256"),
        "protocol_version": manifest.get("protocol_version"),
        "blind_manifest_sha256": prep.sha256_file(prep.BLIND_MANIFEST),
        "answers_sha256": {str(k): v for k, v in sorted(answers_sha.items())},
        "key_commitment": commitment,
        "key_sha256": key_sha,
        "commitment_verified": s1["commitment_verified"] and (
            s2["commitment_verified"] if s2 else True),
        "audio_verified": s1["audio_verified"] and (s2["audio_verified"] if s2 else True),
        "key_reveal_sha256": reveal_sha,
        # 書き込みは report 側の確定記録ガードを通ってから（束で公開する）
        "_key_reveal_text": reveal_text,
        "listener_id": s1["listener_id"], "session_id": s1["session_id"],
        "advancing_genes": advancing,
        "genes": genes,
        "overall": {"perceptible_candidate_count": len(candidates),
                    "perceptible_candidates": candidates,
                    "verdict": sp.s4_gate(len(candidates))},
    }


def finalize_or_blocked(stage1_path: Optional[Path] = None,
                        stage2_path: Optional[Path] = None,
                        ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    try:
        return finalize(stage1_path, stage2_path), None
    except prep.S35Stop as stop:
        return None, stop.as_dict()


if __name__ == "__main__":
    # Stage 1 だけを採点して、Stage 2 へ進む gene を出す（正誤は gene 単位）。
    try:
        res = score_stage(sp.STAGE1)
    except prep.S35Stop as stop:
        print(prep._dumps(stop.as_dict()))
        raise SystemExit(3) from None
    print(prep._dumps({"stage": 1, "advancing": advancing_from_stage1(res),
                       "commitment_verified": res["commitment_verified"],
                       "audio_verified": res["audio_verified"]}))
