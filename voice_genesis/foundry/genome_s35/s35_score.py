"""genome_s35/s35_score.py — Stage 1 / Stage 2 の採点。

**回答凍結後だけ実行する。** 採点前に正解を表示しない。
各問終了時に正誤を出さない（stage 単位でまとめて採点する）。
"""
from __future__ import annotations

import json
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
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise prep.S35Stop(reason=f"回答ファイルが JSON として読めない: {exc}",
                           required_action="回答 JSON を確認する") from exc
    if data.get("schema") != sp.ANSWERS_SCHEMA:
        raise prep.S35Stop(reason=f"回答 schema が不正: {data.get('schema')!r}",
                           required_action=f"schema を {sp.ANSWERS_SCHEMA} にする")
    if int(data.get("stage", stage)) != stage:
        raise prep.S35Stop(reason=f"回答ファイルの stage が違う: {data.get('stage')!r} "
                                  f"(期待 {stage})",
                           required_action="stage を合わせる")
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
    """配布した pack の WAV が manifest の SHA と一致することを確認する。"""
    bad: List[str] = []
    checked = 0
    block = (manifest.get("stages") or {}).get(str(stage)) or {}
    for tid, per in (block.get("audio_sha256") or {}).items():
        for slot, want in per.items():
            p = prep.stage_audio_dir(stage) / f"{tid}_{slot}.wav"
            if not p.exists():
                bad.append(f"{p.name} 欠落")
                continue
            checked += 1
            if prep.sha256_file(p) != want:
                bad.append(f"{p.name} SHA 不一致")
    return (not bad), {"checked": checked, "problems": bad[:5], "problem_count": len(bad)}


def score_stage(stage: int, path: Optional[Path] = None) -> Dict[str, Any]:
    """1 stage を採点する。`UNSURE` は正答に数えない。"""
    manifest = prep.load_manifest()
    key, key_sha = prep.load_private_key()
    commitment_ok = manifest.get("key_commitment") == key_sha

    block = (manifest.get("stages") or {}).get(str(stage))
    if not block:
        raise prep.S35Stop(reason=f"Stage {stage} が manifest に無い",
                           required_action=f"Stage {stage} の pack を先に生成する")
    doc, ans_sha = load_answers(stage, path)
    answers = doc.get("answers") or {}
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


def write_key_reveal(key: Dict[str, Any], key_sha: str, commitment: str,
                     answers_sha: Dict[int, str], path: Optional[Path] = None) -> str:
    """全回答が凍結された**後**にのみ生成する。"""
    path = KEY_REVEAL if path is None else path
    reveal = {
        "protocol_version": key.get("protocol_version"),
        "revealed_after_answers_sha256": {str(k): v for k, v in sorted(answers_sha.items())},
        "key_commitment": commitment,
        "key_sha256": key_sha,
        "commitment_verified": commitment == key_sha,
        "plans": key.get("plans"),
        "trials": key["trials"],
    }
    path.write_text(prep._dumps(reveal), encoding="utf-8")
    return prep.sha256_file(path)


def finalize(stage1_path: Optional[Path] = None,
             stage2_path: Optional[Path] = None) -> Dict[str, Any]:
    """Stage 1 + Stage 2 を採点して gene verdict と S4 gate を出す。"""
    manifest = prep.load_manifest()
    key, key_sha = prep.load_private_key()
    commitment = manifest.get("key_commitment", "")

    s1 = score_stage(sp.STAGE1, stage1_path)
    advancing = advancing_from_stage1(s1)

    s2: Optional[Dict[str, Any]] = None
    has_stage2_block = bool((manifest.get("stages") or {}).get(str(sp.STAGE2)))
    if advancing and has_stage2_block:
        s2 = score_stage(sp.STAGE2, stage2_path)
    elif advancing and not has_stage2_block:
        raise prep.S35Stop(
            reason=f"Stage 1 を通過した gene があるのに Stage 2 pack が無い: {advancing}",
            required_action="prepare_stage2() で Stage 2 を配布し、回答を得る")

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
                       "not_evaluable_reason": plan.get("not_evaluable_reason"),
                       "contexts": []}

    answers_sha = {sp.STAGE1: s1["answers_sha256"]}
    if s2:
        answers_sha[sp.STAGE2] = s2["answers_sha256"]
    reveal_sha = write_key_reveal(key, key_sha, commitment, answers_sha)

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
