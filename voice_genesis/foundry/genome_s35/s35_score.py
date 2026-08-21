"""genome_s35/s35_score.py — Phase D–E（設計書 v1.0 §21）。

**回答凍結後だけ実行する。** 採点前に正解を表示しない。

責務: answers SHA 固定 / key commitment 検証 / trial 採点 / gene 集約 /
S4_READY 判定 / key_reveal 生成。
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
ANSWERS = RESULTS / "answers.json"


def load_answers(path: Optional[Path] = None) -> Tuple[Dict[str, Any], str]:
    """§12 回答ファイルを読み、**そのままの bytes** の SHA を凍結する。"""
    path = ANSWERS if path is None else path
    if not path.exists():
        raise prep.S35Stop(reason=f"回答ファイルが無い（{path}）",
                           required_action="聴取後の answers.json を配置する")
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise prep.S35Stop(reason=f"回答ファイルが JSON として読めない: {exc}",
                           required_action="answers.json を確認する") from exc
    if data.get("schema") != sp.ANSWERS_SCHEMA:
        raise prep.S35Stop(reason=f"回答 schema が不正: {data.get('schema')!r}",
                           required_action=f"schema を {sp.ANSWERS_SCHEMA} にする")
    return data, prep.sha256_bytes(raw)


def load_private_key(path: Optional[Path] = None) -> Tuple[Dict[str, Any], str]:
    # 既定値を def 時に束縛しない（テスト・別 session で差し替えられるように）
    path = prep.PRIVATE_KEY if path is None else path
    if not path.exists():
        raise prep.S35Stop(reason=f"private key が無い（{path}）",
                           required_action="Phase C の成果物を復元する。再生成は blind の破壊")
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), prep.sha256_bytes(raw)


def verify_commitment(manifest: Dict[str, Any], key_sha: str) -> bool:
    """§9 commitment 一致検証。不一致は INVALID（回答後の正解変更を禁止する装置）。"""
    return manifest.get("key_commitment") == key_sha


def check_answers_complete(answers: Dict[str, str], trial_ids: List[str]) -> None:
    """§25「回答ファイルが部分的」は即停止。空文字も未回答として扱う。"""
    missing = [t for t in trial_ids if not answers.get(t)]
    extra = [t for t in answers if t not in trial_ids]
    if missing:
        raise prep.S35Stop(reason=f"未回答の trial がある: {len(missing)} 件 "
                                  f"(例 {missing[:3]})",
                           required_action="全 trial に A / B / UNSURE を入れる")
    if extra:
        raise prep.S35Stop(reason=f"manifest に無い trial の回答がある: {extra[:3]}",
                           required_action="回答ファイルを blind manifest に合わせる")
    bad = {t: v for t, v in answers.items() if v not in {a.value for a in sp.Answer}}
    if bad:
        raise prep.S35Stop(reason=f"A / B / UNSURE 以外の回答がある: {list(bad.items())[:3]}",
                           required_action="回答語彙を A / B / UNSURE に限定する")


def score(answers: Dict[str, str], key: Dict[str, Any], *,
          commitment_verified: bool, audio_verified: bool) -> Dict[str, Any]:
    """§13 trial 採点 → gene 集約。`UNSURE` は正答に数えない。"""
    per_gene: Dict[str, Dict[str, Any]] = {}
    for tid, spec in key["trials"].items():
        g = per_gene.setdefault(spec["gene"], {
            "correct": 0, "total": 0, "unsure": 0,
            "pairs": set(), "contexts": set(), "trials": {}})
        given = answers.get(tid, "")
        ok = given == spec["correct"]          # UNSURE は一致しない = incorrect
        g["total"] += 1
        g["correct"] += int(ok)
        g["unsure"] += int(given == sp.Answer.UNSURE.value)
        g["pairs"].add(spec["pair_key"])
        g["contexts"].add(spec["probe_kind"])
        g["trials"][tid] = {"answer": given, "correct": ok}

    out: Dict[str, Any] = {}
    for gene, g in per_gene.items():
        verdict = sp.gene_verdict(
            g["correct"], g["total"], len(g["pairs"]), len(g["contexts"]),
            commitment_verified=commitment_verified, audio_verified=audio_verified)
        out[gene] = {
            "verdict": verdict.value,
            "correct": g["correct"], "total": g["total"], "unsure": g["unsure"],
            "distinct_pairs": len(g["pairs"]), "contexts": len(g["contexts"]),
            "context_ids": sorted(g["contexts"]),
            "pair_keys": sorted(g["pairs"]),
            "trials": g["trials"],
        }
    return out


def write_key_reveal(key: Dict[str, Any], key_sha: str, commitment: str,
                     answers_sha: str, path: Optional[Path] = None) -> str:
    """§9 回答凍結**後**にのみ生成する。"""
    path = KEY_REVEAL if path is None else path
    reveal = {
        "protocol_version": key.get("protocol_version"),
        "revealed_after_answers_sha256": answers_sha,
        "key_commitment": commitment,
        "key_sha256": key_sha,
        "commitment_verified": commitment == key_sha,
        "trials": key["trials"],
    }
    path.write_text(prep._dumps(reveal), encoding="utf-8")
    return prep.sha256_file(path)


def run(answers_path: Optional[Path] = None) -> Dict[str, Any]:
    answers_path = ANSWERS if answers_path is None else answers_path
    if not prep.BLIND_MANIFEST.exists():
        raise prep.S35Stop(reason=f"blind manifest が無い（{prep.BLIND_MANIFEST}）",
                           required_action="Phase C を先に実行する")
    manifest = json.loads(prep.BLIND_MANIFEST.read_text(encoding="utf-8"))
    manifest_sha = prep.sha256_file(prep.BLIND_MANIFEST)

    answers_doc, answers_sha = load_answers(answers_path)     # §12 回答凍結
    key, key_sha = load_private_key()
    commitment_ok = verify_commitment(manifest, key_sha)

    check_answers_complete(answers_doc.get("answers") or {}, manifest["trial_ids"])

    # §3/§13 audio SHA verified — pack の実体を manifest と突き合わせる
    audio_ok, audio_detail = verify_pack_audio(manifest)

    genes = score(answers_doc.get("answers") or {}, key,
                  commitment_verified=commitment_ok, audio_verified=audio_ok)
    reveal_sha = write_key_reveal(key, key_sha, manifest.get("key_commitment", ""),
                                  answers_sha)

    perceptible = sorted(g for g, v in genes.items()
                         if v["verdict"] == sp.GeneVerdict.PERCEPTIBLE.value)
    return {
        "s3_results_sha256": manifest.get("s3_results_sha256"),
        "protocol_version": manifest.get("protocol_version"),
        "blind_manifest_sha256": manifest_sha,
        "answers_sha256": answers_sha,
        "key_commitment": manifest.get("key_commitment"),
        "key_sha256": key_sha,
        "commitment_verified": commitment_ok,
        "audio_verified": audio_ok,
        "audio_detail": audio_detail,
        "key_reveal_sha256": reveal_sha,
        "listener_id": answers_doc.get("listener_id"),
        "session_id": answers_doc.get("session_id"),
        "genes": genes,
        "overall": {"perceptible_gene_count": len(perceptible),
                    "perceptible_genes": perceptible,
                    "verdict": sp.s4_gate(len(perceptible))},
    }


def verify_pack_audio(manifest: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    """配布した pack の WAV が manifest の SHA と一致することを確認する。"""
    bad: List[str] = []
    checked = 0
    for tid, per in (manifest.get("audio_sha256") or {}).items():
        for slot, want in per.items():
            p = prep.PACK_AUDIO / f"{tid}_{slot}.wav"
            if not p.exists():
                bad.append(f"{p.name} 欠落")
                continue
            checked += 1
            if prep.sha256_file(p) != want:
                bad.append(f"{p.name} SHA 不一致")
    return (not bad), {"checked": checked, "problems": bad[:5],
                       "problem_count": len(bad)}


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ANSWERS
    try:
        result = run(path)
    except prep.S35Stop as stop:
        print(prep._dumps(stop.as_dict()))
        raise SystemExit(3) from None
    print(prep._dumps(result["overall"]))


def scored_or_blocked(answers_path: Optional[Path] = None,
                      ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """report 側から使うラッパ。BLOCKED を例外でなく値で返す。"""
    try:
        return run(answers_path), None
    except prep.S35Stop as stop:
        return None, stop.as_dict()
