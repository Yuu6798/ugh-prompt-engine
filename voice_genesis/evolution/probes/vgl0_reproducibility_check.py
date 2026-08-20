"""VG-L0: Render Reproducibility を **独立プロセス間**で検証する。

レビュー指摘（PR #289 2 巡目）: 同一 Python プロセス内で条件を順に生成した
場合、monkeypatch 状態 / module global / セッションオブジェクト / mutable
cache / 乱数状態が共有される。**同一プロセス内の反復一致は independent
replay の証明にならない。**

本スクリプトは `vgl0_control_axis_probe.py` を **1 条件 = 1 サブプロセス**で
起動し、次の 2 つを検査する:

1. **fresh-process 再現性**: 同じ条件を、まっさらな import から始まる
   別プロセスで 2 回生成し、WAV sha256 の一致を確認する
2. **順序非依存性**: 全条件を forward 順で通したプロセスと reverse 順で
   通したプロセスを走らせ、**同じ条件の WAV sha256 が実行順に依存しない**
   ことを確認する（条件順序汚染の検査）

いずれかが不一致なら **fail-closed**（終了コード 1）。「一致した」だけでなく
「何と何を比べて一致したか」を結果 JSON に残す。

正直会計: 本検査が固定するのは *ExecutionProfile を固定した上での* 再現性
であって、環境非依存の決定論ではない。実行環境（python / onnxruntime /
numpy / platform）は probe 側が `execution_profile` として結果へ記録し、
本スクリプトはそれらが全プロセスで一致していることも確認する。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence


def sha256_path(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


PROBE = Path(__file__).resolve().parent / "vgl0_control_axis_probe.py"

# 期待条件集合は probe 本体の定義を単一ソースとして読む。両実行が「同じ条件を
# 揃って落とした」場合、突き合わせだけでは検出できない（レビュー指摘）ので、
# **期待集合そのもの**と照合する。
sys.path.insert(0, str(PROBE.parent))
from vgl0_control_axis_probe import CONDITIONS as _PROBE_CONDITIONS  # noqa: E402

EXPECTED_LABELS = {label for label, _ in _PROBE_CONDITIONS}

# fresh-process 反復の対象。**制御軸を有効にした条件を必ず含める**
# （baseline だけでは patch 経路を一度も通らない — 1 巡目の指摘）。
REPLAY_LABELS = [
    "baseline",
    "cdur_x2.0",
    "phrase_breath_20f",
    "phrase_final_x1.5",
]


def run_probe(
    py: str, model_args: Sequence[str], out_dir: Path, result_json: Path,
    *, only: Optional[str] = None, reverse: bool = False,
) -> dict:
    cmd = [py, str(PROBE), *model_args,
           "--out-dir", str(out_dir), "--result-json", str(result_json)]
    if only:
        cmd += ["--only", only]
    if reverse:
        cmd += ["--reverse"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # probe は条件が失敗すると rc=1 を返すが、失敗も含めて結果 JSON へ
    # 記録している。ここで即 SystemExit すると「何が失敗したか」が
    # 結果に残らないので、**JSON が読めるならそれを返し、判定は
    # 呼び出し側の errored_labels に委ねる**。JSON 自体が無い/壊れている
    # ときだけ、記録しようがないので落とす。
    if not result_json.exists():
        raise SystemExit(
            f"probe failed (rc={proc.returncode}) for only={only} reverse={reverse} "
            f"— 結果 JSON が書かれていない\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    try:
        return json.loads(result_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"probe の結果 JSON が壊れている ({result_json}): {exc}\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}") from exc


def sha_map(payload: dict) -> Dict[str, str]:
    return {c["label"]: c["wav_sha256"] for c in payload["conditions"]
            if "wav_sha256" in c}


def errored_labels(payload: dict) -> List[str]:
    """probe は条件が失敗しても記録して続行するため、終了コードだけでは
    取りこぼす。**失敗した条件を落としたまま PASS を出さない**ように明示的に拾う
    （レビュー指摘: 失敗条件が集合演算で黙って消え、fail-closed のはずの
    スクリプトが PASS を返しうる）。"""
    return [c.get("label", "<unknown>") for c in payload["conditions"] if "error" in c]


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--canon-dir", required=True)
    ap.add_argument("--vocoder-dir", required=True)
    ap.add_argument("--acoustic-dir", required=True)
    ap.add_argument("--acoustic-onnx", required=True)
    ap.add_argument("--speaker", default="ritsu")
    ap.add_argument("--song", default="sakura")
    ap.add_argument("--notes-limit", type=int, default=8)
    ap.add_argument("--work-dir", required=True,
                    help="各サブプロセスの出力を分けて置く作業ディレクトリ")
    ap.add_argument("--result-json", required=True)
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args(argv)

    model_args = [
        "--canon-dir", a.canon_dir, "--vocoder-dir", a.vocoder_dir,
        "--acoustic-dir", a.acoustic_dir, "--acoustic-onnx", a.acoustic_onnx,
        "--speaker", a.speaker, "--song", a.song,
        "--notes-limit", str(a.notes_limit),
    ]
    work = Path(a.work_dir)
    work.mkdir(parents=True, exist_ok=True)

    unknown = sorted(set(REPLAY_LABELS) - EXPECTED_LABELS)
    if unknown:
        raise SystemExit(f"REPLAY_LABELS に probe が持たない条件がある: {unknown}")

    findings: List[dict] = []
    exec_profiles: List[dict] = []
    failures: List[str] = []

    # --- 検査 1: fresh-process 反復 -----------------------------------------
    for label in REPLAY_LABELS:
        shas: List[Optional[str]] = []
        for rep in ("procA", "procB"):
            tag = f"replay_{label}_{rep}"
            payload = run_probe(
                a.python, model_args, work / tag, work / f"{tag}.json", only=label)
            exec_profiles.append(payload["execution_profile"])
            bad = errored_labels(payload)
            if bad:
                failures.append(f"replay {label} ({rep}) の条件が失敗: {bad}")
            # 失敗時は None を入れる。ここで KeyError を投げると結果 JSON が
            # 1 行も書かれずに落ち、何が起きたかが残らない。
            shas.append(sha_map(payload).get(label))
        ok = shas[0] is not None and shas[0] == shas[1]
        findings.append({
            "check": "fresh_process_replay", "label": label,
            "sha_proc_a": shas[0], "sha_proc_b": shas[1], "match": ok,
        })
        if not ok:
            failures.append(f"fresh_process_replay mismatch: {label}")
        shown = shas[0][:12] if shas[0] else "<no sha>"
        print(f"[replay] {label:24s} {'MATCH' if ok else 'MISMATCH'} {shown}")

    # --- 検査 2: 実行順の非依存性 -------------------------------------------
    fwd = run_probe(a.python, model_args, work / "order_forward",
                    work / "order_forward.json")
    rev = run_probe(a.python, model_args, work / "order_reverse",
                    work / "order_reverse.json", reverse=True)
    exec_profiles += [fwd["execution_profile"], rev["execution_profile"]]
    for name, payload in (("forward", fwd), ("reverse", rev)):
        bad = errored_labels(payload)
        if bad:
            failures.append(f"order_independence の {name} 実行で条件が失敗: {bad}")
    fwd_shas, rev_shas = sha_map(fwd), sha_map(rev)
    # 積集合だけを回すと、片方で失敗した条件が黙って検査対象から消える。
    # 和集合で回し、欠けている側は不一致として扱う。
    only_fwd = sorted(set(fwd_shas) - set(rev_shas))
    only_rev = sorted(set(rev_shas) - set(fwd_shas))
    if only_fwd or only_rev:
        failures.append(
            f"順序間で条件集合が一致しない (forward のみ={only_fwd} / reverse のみ={only_rev})")
    # 両方が同じ条件を落としていると上の差分は空になる。期待集合と照合して
    # 「揃って欠けた」ケースも fail にする。
    for name, shas in (("forward", fwd_shas), ("reverse", rev_shas)):
        missing = sorted(EXPECTED_LABELS - set(shas))
        if missing:
            failures.append(f"{name} 実行に期待条件が欠けている: {missing}")
    for label in sorted(EXPECTED_LABELS | set(fwd_shas) | set(rev_shas)):
        f_sha, r_sha = fwd_shas.get(label), rev_shas.get(label)
        ok = f_sha is not None and f_sha == r_sha
        findings.append({
            "check": "order_independence", "label": label,
            "sha_forward": f_sha, "sha_reverse": r_sha, "match": ok,
        })
        if not ok:
            failures.append(f"order_independence mismatch: {label}")
        print(f"[order]  {label:24s} {'MATCH' if ok else 'MISMATCH'}")

    # --- 検査 3: 全プロセスの ExecutionProfile 一致 --------------------------
    distinct = {json.dumps(p, sort_keys=True) for p in exec_profiles}
    exec_ok = len(distinct) == 1
    findings.append({"check": "execution_profile_identical",
                     "n_distinct": len(distinct), "match": exec_ok})
    if not exec_ok:
        failures.append("execution_profile differs between processes")

    # 同一プロセス内の反復（forward 実行の *_repeat 条件）は**別種の証拠**
    # として区別して残す — これは independent replay ではない。
    in_process = [
        {"check": "in_process_repeat", "label": lbl,
         "sha": fwd_shas.get(lbl), "sha_base": fwd_shas.get(lbl.replace("_repeat", "")),
         "match": fwd_shas.get(lbl) == fwd_shas.get(lbl.replace("_repeat", "")),
         "note": "同一プロセス内の反復。independent replay の証拠にはならない"}
        for lbl in fwd_shas if lbl.endswith("_repeat")
    ]

    payload = {
        # 結果を生んだコード自身の sha も束縛する（probe だけ pin して検査
        # スクリプトを pin しないと、PASS の出どころが辿れない）
        "checker_script": {"path": str(Path(__file__).resolve()),
                           "sha256": sha256_path(Path(__file__).resolve())},
        # verdict を入力そのものへ束縛する。probe/checker の sha だけでは
        # 「どのモデル・楽譜に対する PASS か」が結果から辿れない（レビュー指摘）。
        "pins": fwd.get("pins"),
        "probe_script": (fwd.get("pins") or {}).get("probe_script"),
        "consumed_model_bytes_check": fwd.get("consumed_model_bytes_check"),
        "n_processes": len(exec_profiles),
        "replay_labels": REPLAY_LABELS,
        "execution_profile": exec_profiles[0] if exec_profiles else None,
        "findings": findings,
        "in_process_repeat": in_process,
        "verdict": "PASS" if not failures else "FAIL",
        "failures": failures,
    }
    Path(a.result_json).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\n=== Render Reproducibility: {payload['verdict']} "
          f"({len(exec_profiles)} processes) ===")
    for f in failures:
        print("  FAIL:", f)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
