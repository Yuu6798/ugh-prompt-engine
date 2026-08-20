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
from typing import Dict, List, Optional, Sequence, Tuple


def sha256_path(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


PROBE = Path(__file__).resolve().parent / "vgl0_control_axis_probe.py"
SELF = Path(__file__).resolve()

# **ロード時に自分自身の sha を固定する**。実行後にディスクから読むと、10 本の
# サブプロセスが走っている数分の間にこのファイルが編集された場合、
# 「判定を出したのは旧コード / 記録される hash は新ファイル」という乖離ができ、
# fixture テストが別実装を検証してしまう（レビュー指摘 P2 /
# AGENTS.md「hash した bytes と実行された bytes の間に cache の窓が無いか」）。
# 実行後に再 hash して一致も検証する。
_SELF_SHA_AT_LOAD = hashlib.sha256(SELF.read_bytes()).hexdigest()

# 期待条件集合は probe 本体の定義を単一ソースとして読む。両実行が「同じ条件を
# 揃って落とした」場合、突き合わせだけでは検出できない（レビュー指摘）ので、
# **期待集合そのもの**と照合する。
sys.path.insert(0, str(PROBE.parent))
import vgl0_control_axis_probe as probe_mod  # noqa: E402

EXPECTED_LABELS = {label for label, _ in probe_mod.CONDITIONS}

# fresh-process 反復の対象。**制御軸を有効にした条件を必ず含める**
# （baseline だけでは patch 経路を一度も通らない — 1 巡目の指摘）。
REPLAY_LABELS = [
    "baseline",
    "cdur_x2.0",
    "phrase_breath_20f",
    "phrase_final_x1.5",
]


WORK_OWNER_MARKER = ".vgl0_checker_workdir"


def claim_work_dir(work: Path) -> Path:
    """作業ディレクトリを **checker の所有物として確保する**。

    checker は `order_forward.json` / `replay_baseline_procA.json` のような
    **固定名**のファイルを work ディレクトリ直下へ書き、起動ごとに消す。
    既存の無関係なディレクトリを `--work-dir` に指定すると、同名ファイルが
    所有検査なしに消える（レビュー指摘 P2・7 巡目 / AGENTS.md「per-run パス」）。

    そこで probe の条件ディレクトリと同じ様式で所有マーカーを置き、
    **マーカーが無く空でもないディレクトリは fail-closed** にする。
    """
    if work.exists():
        if not work.is_dir():
            raise SystemExit(f"--work-dir {work} はディレクトリではない")
        # マーカーは **symlink でない通常ファイル**であること。`exists()` は
        # リンクを追うので、保護対象入力への symlink を置かれると「所有して
        # いる」と誤判定し、下の書き込みがリンク先を切り詰める（レビュー指摘 P2）。
        if not probe_mod.is_own_marker(work / WORK_OWNER_MARKER) and any(work.iterdir()):
            raise SystemExit(
                f"--work-dir {work} は checker が作ったディレクトリではない"
                f"（所有マーカー {WORK_OWNER_MARKER} が通常ファイルとして無く、"
                f"中身がある）。無関係なファイルを消さないため中断する。"
                f"空の場所を指すこと")
    work.mkdir(parents=True, exist_ok=True)
    probe_mod.write_own_marker(
        work / WORK_OWNER_MARKER,
        "vgl0_reproducibility_check が所有する作業ディレクトリ。"
        "このファイルがあるディレクトリの中だけ checker は削除・再作成する。\n")
    return work


def run_probe(
    py: str, model_args: Sequence[str], out_dir: Path, result_json: Path,
    *, owned_work_dir: Path, only: Optional[str] = None, reverse: bool = False,
) -> Tuple[dict, int]:
    """probe を 1 プロセス起動し、(結果 payload, 終了コード) を返す。

    **起動前に結果ファイルを消す**: `--work-dir` を使い回した状態で新しい
    プロセスが JSON を書く前に落ちる（import エラー・モデル欠落・強制終了）と、
    前回成功時のファイルが残ったままになる。それを読むと「完走しなかった
    probe」に対して PASS を出しうる（レビュー指摘 P1）。

    **終了コードは呼び出し側へ返す**: probe は消費バイト/pin 不一致のとき
    条件レベルの `error` を付けずに rc=1 を返す。rc を捨てると
    `errored_labels` では拾えず、WAV hash が一致しているだけで PASS に
    なりうる（レビュー指摘 P1）。
    """
    # 消してよいのは **checker が所有する work ディレクトリの中**だけ
    # （レビュー指摘 P2・7 巡目）。所有マーカーは `claim_work_dir` が置く。
    if result_json.resolve().parent != owned_work_dir.resolve():
        raise SystemExit(
            f"結果ファイル {result_json} が所有 work ディレクトリ "
            f"{owned_work_dir} の直下にない — 消してよいか判定できないため中断する")
    if result_json.exists():
        result_json.unlink()
    cmd = [py, str(PROBE), *model_args,
           "--out-dir", str(out_dir), "--result-json", str(result_json)]
    if only:
        cmd += ["--only", only]
    if reverse:
        cmd += ["--reverse"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # JSON が書かれていれば失敗も記録されているので読む（判定は呼び出し側）。
    # 書かれていない/壊れているときは記録しようがないので落とす。
    if not result_json.exists():
        raise SystemExit(
            f"probe failed (rc={proc.returncode}) for only={only} reverse={reverse} "
            f"— 結果 JSON が書かれていない\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    try:
        return json.loads(result_json.read_text(encoding="utf-8")), proc.returncode
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"probe の結果 JSON が壊れている ({result_json}): {exc}\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}") from exc


def probe_run_failures(payload: dict, rc: int, tag: str) -> List[str]:
    """1 回の probe 起動について、PASS を妨げる事象を全部拾う。

    条件レベルの error だけを見ると、**probe の provenance ゲート
    （消費バイトと pin の一致）が落ちた場合を見逃す** — そちらは rc でしか
    表に出ない。rc・条件 error・消費バイト検査の 3 つを揃って検査する。
    """
    out: List[str] = []
    if rc != 0:
        out.append(f"{tag}: probe が非ゼロ終了 (rc={rc})")
    bad = errored_labels(payload)
    if bad:
        out.append(f"{tag}: 条件が失敗 {bad}")
    for key, label in (("consumed_model_bytes_check", "消費モデルバイト"),
                       ("consumed_score_bytes_check", "実行された楽譜バイト")):
        check = payload.get(key)
        if check is None:
            # 欠落は fail（probe は常に両方を出す）。「検査が無い」を
            # 「検査に通った」と読み替えないための fail-closed。
            out.append(f"{tag}: {key} が結果に無い")
        elif not check.get("ok"):
            out.append(f"{tag}: {label}が pin と不一致 {check.get('mismatches')}")
    return out


def pin_map(payload: dict) -> Dict[str, str]:
    """payload の pin を {key: sha256} へ畳む（パスは環境依存なので除く）。"""
    return {k: v.get("sha256") for k, v in (payload.get("pins") or {}).items()}


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
    # probe と同じ衝突ガードを checker 側の出力にも通す（--work-dir /
    # --result-json が入力を指していたら書く前に止める）。
    #
    # **保護対象は probe の `ProbeConfig.protected_inputs()` をそのまま流用する**
    # （レビュー指摘 P2・7 巡目）。checker 側で列挙を書き写すと、`--acoustic-onnx`
    # が `--acoustic-dir` の外にある場合の派生ファイル
    # （`*.phonemes.json` / `*.<spk>.emb`）のように、probe 側だけが知っている
    # 入力が抜ける。単一ソース化しておけば probe に入力が増えたとき自動で追随する。
    probe_cfg = probe_mod.ProbeConfig(argparse.Namespace(
        canon_dir=a.canon_dir, vocoder_dir=a.vocoder_dir,
        acoustic_dir=a.acoustic_dir, acoustic_onnx=a.acoustic_onnx,
        speaker=a.speaker, song=a.song, notes_limit=a.notes_limit,
        # checker は --singer-dir を probe へ渡さないので、各サブプロセスは
        # probe の既定値を使う。保護対象もその既定値で解決する。
        singer_dir=str(probe_mod.DEFAULT_SINGER_DIR),
        out_dir=str(work),
    ))
    probe_mod.assert_writes_do_not_touch_inputs(
        write_paths=[work, Path(a.result_json)],
        protected_inputs=[SELF, *probe_cfg.protected_inputs()],
    )
    claim_work_dir(work)

    unknown = sorted(set(REPLAY_LABELS) - EXPECTED_LABELS)
    if unknown:
        raise SystemExit(f"REPLAY_LABELS に probe が持たない条件がある: {unknown}")

    findings: List[dict] = []
    exec_profiles: List[dict] = []
    failures: List[str] = []
    # 「何プロセスを、どのゲートで検査したか」を結果に残す（PASS の根拠を
    # 後から数えられるようにする）
    probe_runs: List[dict] = []
    # 全サブプロセスの pin を集めて突き合わせる。forward の pin だけ保持して
    # 比較しないと、**出力に影響しないバイト変更**（score.py の整形など）が
    # 途中で入っても WAV は一致し PASS が出てしまう（レビュー指摘 P2）。
    pin_maps: List[Tuple[str, Dict[str, str]]] = []

    # --- 検査 1: fresh-process 反復 -----------------------------------------
    for label in REPLAY_LABELS:
        shas: List[Optional[str]] = []
        for rep in ("procA", "procB"):
            tag = f"replay_{label}_{rep}"
            payload, rc = run_probe(
                a.python, model_args, work / tag, work / f"{tag}.json",
                owned_work_dir=work, only=label)
            exec_profiles.append(payload["execution_profile"])
            tag_name = f"replay {label} ({rep})"
            run_failures = probe_run_failures(payload, rc, tag_name)
            failures.extend(run_failures)
            probe_runs.append({
                "tag": tag_name, "returncode": rc,
                "consumed_ok": (payload.get("consumed_model_bytes_check") or {}).get("ok"),
                "consumed_score_ok": (
                    payload.get("consumed_score_bytes_check") or {}).get("ok"),
                "failures": run_failures,
            })
            pin_maps.append((tag_name, pin_map(payload)))
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
    fwd, fwd_rc = run_probe(a.python, model_args, work / "order_forward",
                            work / "order_forward.json", owned_work_dir=work)
    rev, rev_rc = run_probe(a.python, model_args, work / "order_reverse",
                            work / "order_reverse.json", owned_work_dir=work,
                            reverse=True)
    exec_profiles += [fwd["execution_profile"], rev["execution_profile"]]
    for name, payload, rc in (("forward", fwd, fwd_rc), ("reverse", rev, rev_rc)):
        tag_name = f"order_independence {name}"
        run_failures = probe_run_failures(payload, rc, tag_name)
        failures.extend(run_failures)
        probe_runs.append({
            "tag": tag_name, "returncode": rc,
            "consumed_ok": (payload.get("consumed_model_bytes_check") or {}).get("ok"),
            "consumed_score_ok": (
                payload.get("consumed_score_bytes_check") or {}).get("ok"),
            "failures": run_failures,
        })
        pin_maps.append((tag_name, pin_map(payload)))
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

    # --- 検査 4: 全サブプロセスの pin 一致 ----------------------------------
    ref_tag, ref_pins = pin_maps[0]
    pin_diffs = []
    for tag_name, pins in pin_maps[1:]:
        differing = sorted(k for k in set(ref_pins) | set(pins)
                           if ref_pins.get(k) != pins.get(k))
        if differing:
            pin_diffs.append({"tag": tag_name, "vs": ref_tag, "keys": differing})
            failures.append(f"{tag_name}: pin が {ref_tag} と異なる {differing}")
    findings.append({"check": "pins_identical_across_processes",
                     "n_compared": len(pin_maps), "diffs": pin_diffs,
                     "match": not pin_diffs})

    # --- 検査 5: 検査スクリプト自身が実行中に書き換わっていないか -------------
    self_sha_after = hashlib.sha256(SELF.read_bytes()).hexdigest()
    self_stable = self_sha_after == _SELF_SHA_AT_LOAD
    findings.append({"check": "checker_unchanged_during_run",
                     "sha_at_load": _SELF_SHA_AT_LOAD,
                     "sha_after_run": self_sha_after, "match": self_stable})
    if not self_stable:
        failures.append(
            "検査スクリプトが実行中に書き換わった — 判定を出したコードと "
            "記録される hash が食い違うため verdict を信頼できない")

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
        "checker_script": {"path": str(SELF), "sha256": _SELF_SHA_AT_LOAD,
                           "sha256_after_run": self_sha_after,
                           "note": "sha256 は **ロード時**に固定した値。実行後の "
                                   "再 hash と一致することを findings で検証する"},
        # verdict を入力そのものへ束縛する。probe/checker の sha だけでは
        # 「どのモデル・楽譜に対する PASS か」が結果から辿れない（レビュー指摘）。
        "pins": fwd.get("pins"),
        "probe_script": (fwd.get("pins") or {}).get("probe_script"),
        "consumed_model_bytes_check": fwd.get("consumed_model_bytes_check"),
        "n_processes": len(exec_profiles),
        "probe_runs": probe_runs,
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
