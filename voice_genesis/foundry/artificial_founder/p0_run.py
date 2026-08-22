"""p0_run.py — AF-P0 の 1 コマンド実行（設計書 §23 / §24）。

```text
Phase 0 Preregister + pin      Phase 5 Body measurement
Phase 1 Meter control          Phase 6 VoiceGenesis ingestion
Phase 2 Compile AF0            Phase 7 Re-expression measurement
Phase 3 Independent recompute  Phase 8 Ground Truth comparison
Phase 4 UTAU structural valid. Phase 9 Publish verdict
                                       STOP
```

**P1 へ自動進行しない**（§23）。exit code は §24 のとおり
`0 PASS / 1 NOT_ESTABLISHED / 3 BLOCKED / 4 FAILED`。

Phase 1（計器の校正）は Phase 5 以降（AF0 の形質測定）より**前**に置く。
計器が未校正のまま AF0 の値を見てから peak finder を触る余地を、実行順で
塞ぐためである（§15.3 / §17）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import af_compare  # noqa: E402
import af_controls  # noqa: E402
import af_gates  # noqa: E402
import af_measure  # noqa: E402
import af_report  # noqa: E402
import af_utau  # noqa: E402
import convert_founder  # noqa: E402
from af_schema import validate_founder_spec  # noqa: E402
from af_spec import SpecError  # noqa: E402
from af_spec import EXIT_CODES, AFStop, FounderGenome  # noqa: E402
from af_spec import aggregate_digest, canonical_json, genome_from_dict  # noqa: E402
from af_spec import load_controls, load_criteria, load_probes  # noqa: E402
from af_spec import round_floats, sha256_tree, write_json  # noqa: E402

DEFAULT_SPEC = HERE / "founder_specs" / "AF0.json"
DEFAULT_CRITERIA = HERE / "criteria" / "AF_P0_CRITERIA.json"
DEFAULT_CONTROLS = HERE / "controls" / "AF_P0_CONTROLS.json"
DEFAULT_PROBES = HERE / "probes" / "AF_P0_PROBES.json"
DEFAULT_OUT = HERE / "results" / "AF0"


def _log(phase: str, message: str) -> None:
    print(f"[{phase}] {message}", flush=True)


#: 公開（旧 valid bundle の差し替え）を許す前提 Gate。§28「失敗時は旧 valid
#: bundle を保持」を満たすため、Source-Free / spec / 決定論 / Body 構造の
#: いずれかが落ちた候補で canonical 公開場所を上書きしない。
PUBLICATION_PREREQUISITES: Tuple[str, ...] = ("G0", "G1", "G2", "G3")


def output_snapshot_path(out_dir: Path) -> Path:
    """`prepare_output_tree` が直前ツリーを退避する先。**唯一の定義**。

    削除ガード（`reject_output_collision`）と実装が別々にこの名前を綴ると、
    片方だけ変えた瞬間に保護が外れる。両者はこの関数を通す。
    """
    out = Path(out_dir)
    return out.parent / f".{out.name}.previous"


class OutputCollisionError(ValueError):
    """`--out` が保護対象（入力・パッケージ・リポジトリ）と重なっている。"""


def reject_output_collision(out_dir: Path, protected: Sequence[Path]) -> None:
    """`prepare_output_tree` が保護対象を消す構成を **削除前に** 拒否する。

    `prepare_output_tree` はツリーを `rmtree` するので、`--out` に
    `founder_specs/` やパッケージ/リポジトリのルートを渡すと、spec 本体や
    リポジトリ内容を消してから失敗する。出力ディレクトリが保護対象と同一、
    あるいは保護対象を **内包** する場合は着手前に止める（既定の
    `results/AF0` はパッケージ配下だが何も内包しないので通る）。

    `prepare_output_tree` が実際に触る派生パス（スナップショット）も同じ規則で
    検査する。派生パス名は `output_snapshot_path` **1 箇所** から取る。以前は
    ガード側が旧名 `.voicebank-hold` を、実装側が `.previous` を使っており、
    `--out /tmp/AF0` と `--spec /tmp/.AF0.previous` の組み合わせで preflight を
    素通りして spec を消していた（名前がずれた瞬間に穴が開く形だった）。
    """
    out = Path(out_dir).resolve()
    candidates = [out, output_snapshot_path(out)]
    for prot in protected:
        try:
            target = Path(prot).resolve()
        except OSError:  # pragma: no cover
            continue
        for cand in candidates:
            if cand == target or cand in target.parents:
                raise OutputCollisionError(
                    f"--out {out} would delete a protected path ({target}); "
                    "choose an output directory that neither is nor contains an input, "
                    "the package directory, or the repository root")


def prepare_output_tree(out_dir: Path) -> Dict[str, Any]:
    """成果物ツリーを **run ごとに丸ごと作り直す**（§28 partial artifacts 禁止）。

    以前は到達したファイルだけを上書きしていたため、(a) PASS 実行が残した
    `freeze/` が後続の NOT_ESTABLISHED 実行後も canonical 位置に生き残り、
    (b) spec 不正の早期 return が `p0_results.json` だけを書き換えて古い record /
    manifest / measurements / SHA256SUMS を並べたまま残す、という矛盾した
    canonical 成果物ができた。

    ただし **消してから作る** だけだと、spec 不正・依存欠落・中断で run が
    途中で落ちたときに、空または partial なツリーだけが残り、直前の record /
    measurements / freeze / checksums が復元不能に失われる。そこで旧ツリー全体を
    スナップショットへ退避し、`restore_output_tree` で戻せるようにする
    （呼び出し側が `BaseException` で必ず戻す）。

    既発行の voicebank（旧 valid bundle）は §28 に従って新ツリーへ即復元する。
    今回の run が公開まで到達しなければ、旧 bundle がそのまま残る。
    """
    out_dir = Path(out_dir)
    detail: Dict[str, Any] = {"cleared": False, "preserved_voicebank": False,
                              "snapshot": None, "first_run": False}
    if not out_dir.exists():
        # 初回 run。戻す先が無いので、落ちたときは **partial なツリーを消す**
        # （`restore_or_clear_output_tree`）。staging へ作って最後に rename する
        # 案は採らない: provenance へ書くパス（`repo_relative`）が canonical 位置
        # を指さなくなり、第 2/3 巡で入れた checkout 非依存の記録が壊れるため。
        out_dir.mkdir(parents=True, exist_ok=True)
        detail["first_run"] = True
        return detail
    snapshot = output_snapshot_path(out_dir)
    if snapshot.exists():
        shutil.rmtree(snapshot)
    shutil.move(str(out_dir), str(snapshot))
    out_dir.mkdir(parents=True, exist_ok=True)
    detail["cleared"] = True
    detail["snapshot"] = str(snapshot)
    previous_bank = snapshot / "voicebank"
    if previous_bank.is_dir():
        shutil.copytree(previous_bank, out_dir / "voicebank")
        detail["preserved_voicebank"] = True
    return detail


def restore_output_tree(out_dir: Path, snapshot: Optional[str | Path]) -> bool:
    """run が落ちたとき、退避しておいた直前の成果物ツリーを丸ごと戻す。"""
    if not snapshot:
        return False
    snap, out_dir = Path(snapshot), Path(out_dir)
    if not snap.exists():
        return False
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.move(str(snap), str(out_dir))
    return True


def restore_or_clear_output_tree(out_dir: Path,
                                 snapshot: Optional[str | Path]) -> Dict[str, Any]:
    """落ちた run の後始末。**canonical 位置を常に既知の状態にする**。

    退避があれば戻す。初回 run のように戻す先が無い場合は、途中まで書かれた
    ツリーを削除する（§28「partial generation を canonical 成果物として残さない」）。
    `withdraw_publication` と同じ「restore-or-remove」の形。
    """
    if restore_output_tree(out_dir, snapshot):
        return {"restored": True, "removed_partial": False,
                "disposition": "previous result tree restored"}
    out_dir = Path(out_dir)
    removed = False
    if out_dir.exists():
        shutil.rmtree(out_dir)
        removed = True
    return {"restored": False, "removed_partial": removed,
            "disposition": ("partial result tree removed (no previous generation)"
                            if removed else "nothing to clean up")}


def discard_output_snapshot(snapshot: Optional[str | Path]) -> bool:
    """run が最後まで到達したのでスナップショットを破棄する。"""
    if not snapshot:
        return False
    snap = Path(snapshot)
    if not snap.exists():
        return False
    shutil.rmtree(snap)
    return True


def _fill_skipped(evaluated: Sequence[af_gates.GateResult]) -> List[af_gates.GateResult]:
    """未評価の Gate を `SKIPPED` で埋め、常に G0–G14 の全集合を返す。

    `af_gates.overall_verdict` は集合の過不足を判定不能として弾くので、停止した
    run でも「どこまで評価したか」を欠落ではなく SKIPPED で明示する。
    """
    names = {"G0": "SOURCE_FREE", "G1": "SPEC_VALID", "G2": "DETERMINISTIC_COMPILATION",
             "G3": "UTAU_BODY", "G4": "VOICEGENESIS_INGESTION", "G5": "METER_CONTROL",
             "G6": "STANDARD_IDENTITY", "G7": "FOUNDER_SOURCE_HL",
             "G8": "FOUNDER_IDENTITY_AR", "G9": "F0", "G10": "DURATION", "G11": "ENERGY",
             "G12": "RELEASE", "G13": "FOUNDER_EXPRESSION_AG",
             "G14": "PROVENANCE_AND_PUBLICATION"}
    have = {g.gate_id for g in evaluated}
    out = list(evaluated)
    for gid in af_gates.ALL_GATE_IDS:
        if gid not in have:
            out.append(af_gates.GateResult(gid, names[gid], "SKIPPED",
                                           {"reason_code": "NOT_EVALUATED"}))
    return sorted(out, key=lambda g: int(g.gate_id[1:]))


def _finalize(out_dir: Path, genome: Optional[FounderGenome],
              gates: Sequence[af_gates.GateResult], pins: Mapping[str, Any],
              body_cmp: Mapping[str, Any], reexp_cmp: Optional[Mapping[str, Any]],
              extra: Mapping[str, Any]) -> int:
    """結果 JSON / record / freeze / SHA256SUMS を書き、exit code を返す。"""
    gates = _fill_skipped(gates)
    overall = af_gates.overall_verdict(gates)
    if genome is None:
        write_json(out_dir / "p0_results.json", {
            "schema": "voicegenesis-artificial-founder-p0/1.1",
            "gates": [g.as_dict() for g in gates], "pins": dict(pins),
            "overall": overall})
    else:
        results = af_report.build_p0_results(genome, gates, overall, pins, body_cmp,
                                             reexp_cmp, extra)
        write_json(out_dir / "p0_results.json", results)
        (out_dir / "AF_P0_RECORD.md").write_text(
            af_report.build_record_md(genome, results, body_cmp, reexp_cmp, extra),
            encoding="utf-8")
        if overall["verdict"] == "PASS":
            af_report.write_freeze(out_dir, genome, results)
            _log("phase9", "freeze written (PASS only)")
    rows = sha256_tree(out_dir, exclude_names=("SHA256SUMS.txt",))
    (out_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{sha}  {rel}\n" for rel, sha in rows), encoding="utf-8")
    _log("phase9", f"OVERALL = {overall['verdict']} reasons={overall['reason_codes']}")
    _log("stop", "P0 はここで停止する。P1 mutation へ自動進行しない（§23）。")
    return EXIT_CODES[overall["verdict"]]


# ---------------------------------------------------------------------------
# Phase 2 / 3: compile
# ---------------------------------------------------------------------------
def compile_artifacts(genome: FounderGenome, staging: Path) -> Dict[str, Any]:
    """G2 が対象とする生成物一式（Body + oto + truth + manifest + dataset）を作る。

    §19 G2 は `WAV / oto.ini / truth / manifest / dataset` の SHA 一致を要求する。
    Body だけを再計算していたときは、dataset 生成がプロセス間で食い違っても
    G2 が PASS を返し、その生成物が「再現可能」として扱われていた。
    """
    build = af_utau.write_body(genome, staging)
    dataset_dir = staging.parent / "dataset"
    convert_founder.convert_from_genome(genome, staging, dataset_dir)
    dataset_rows = sha256_tree(dataset_dir)
    return {"digest": build.identity_digest, "rows": build.sha_rows,
            "n_files": len(build.sha_rows), "units": build.units,
            "dataset_digest": aggregate_digest(dataset_rows),
            "dataset_n_files": len(dataset_rows), "dataset_dir": dataset_dir}


#: 後方互換の別名（テスト・外部からの参照用）。
compile_body = compile_artifacts


def cross_process_digest(spec_path: Path, staging: Path) -> Dict[str, Any]:
    """別プロセスで **G2 対象の生成物一式** を作り直し、ダイジェストを返す（§19 G2）。"""
    cmd = [sys.executable, str(HERE / "p0_run.py"), "--compile-only",
           "--spec", str(Path(spec_path).resolve()), "--out", str(Path(staging).resolve())]
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(HERE))
    if proc.returncode != 0:
        return {"match": False, "error": proc.stderr.strip()[-2000:], "digest": None}
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"match": False, "error": f"unparsable child output: {proc.stdout[-500:]}",
                "digest": None}
    if not isinstance(payload, dict):
        return {"match": False, "error": f"unexpected child payload: {payload!r}",
                "digest": None}
    # 子プロセスの payload をそのまま返す（キーを列挙して詰め替えると、G2 の
    # 対象を増やしたときに新しいダイジェストが黙って落ちる）。
    return dict(payload)


# ---------------------------------------------------------------------------
# Phase 5 / 7: measurement + probes
# ---------------------------------------------------------------------------
def measure_dir(wav_dir: Path, stems: Sequence[str],
                metric_definitions: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for stem in stems:
        path = wav_dir / f"{stem}.wav"
        out[stem] = af_measure.measure_wav_file(path, metric_definitions, stem)
    return out


def dump_probes(wav_dir: Path, out_dir: Path, probes: Mapping[str, Any],
                metric_definitions: Mapping[str, Any],
                measurements: Mapping[str, Dict[str, Any]]) -> Dict[str, Any]:
    """§25 probes/。**診断専用**（判定には使わない）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    sp = probes["spectrum_probes"]
    ep = probes["envelope_probes"]
    ap = probes["afterglow_probes"]
    written: List[str] = []

    def _contained(path: Path, root: Path) -> Path:
        """probe の alias 由来パスが root の外へ出ていないことを確かめる。

        alias は `load_probes` が凍結 unit 名へ制限しているが、読み書きの境界でも
        独立に確認する（config 由来の名前がパスになる箇所は二重に閉じる）。
        """
        resolved, base = path.resolve(), root.resolve()
        if base not in resolved.parents and resolved != base:
            raise AFStop(cause=f"probe path {path} escapes {root}",
                         impact="probes would read or write outside the result tree",
                         minimal_fix="use frozen AF-P0 body unit names as probe aliases",
                         status="FAILED", reason_code="PATH_ESCAPE")
        return path

    for stem in sp["aliases"]:
        m = measurements.get(stem)
        wav = _contained(wav_dir / f"{stem}.wav", wav_dir)
        if m is None or not wav.exists() or m.get("core_probe_ms") is None:
            continue
        x, sr = af_measure.read_wav_mono(wav)
        lo, hi = m["core_probe_ms"]
        seg = x[int(lo * sr / 1000.0):int(hi * sr / 1000.0)]
        freqs, mag = af_measure.magnitude_spectrum_db(seg, sr, int(sp["nfft"]),
                                                      str(metric_definitions["analysis"]
                                                          ["fft_window"]))
        b_lo, b_hi = sp["band_hz"]
        mask = (freqs >= b_lo) & (freqs <= b_hi)
        idx = np.linspace(0, int(mask.sum()) - 1, int(sp["n_points"])).astype(int)
        payload = {
            "stem": stem, "sr": sr, "region": sp["region"],
            "freq_hz": [round(float(v), sp["round_decimals"]) for v in freqs[mask][idx]],
            "mag_db": [round(float(v), sp["round_decimals"]) for v in mag[mask][idx]],
        }
        write_json(_contained(out_dir / f"spectrum_{stem}.json", out_dir), payload)
        written.append(f"spectrum_{stem}.json")

    for stem in ep["aliases"]:
        wav = _contained(wav_dir / f"{stem}.wav", wav_dir)
        if not wav.exists():
            continue
        x, sr = af_measure.read_wav_mono(wav)
        t, e = af_measure.rms_envelope(x, sr, float(ep["hop_ms"]), 4.0)
        write_json(_contained(out_dir / f"envelope_{stem}.json", out_dir), {
            "stem": stem, "sr": sr, "hop_ms": ep["hop_ms"],
            "t_ms": [round(float(v), 3) for v in t],
            "rms": [round(float(v), ep["round_decimals"]) for v in e]})
        written.append(f"envelope_{stem}.json")

    for stem in ap["aliases"]:
        m = measurements.get(stem)
        wav = _contained(wav_dir / f"{stem}.wav", wav_dir)
        if m is None or not wav.exists() or not m.get("ar_alpha_center_hz"):
            continue
        x, sr = af_measure.read_wav_mono(wav)
        mag = metric_definitions["afterglow"]
        half = float(mag["alpha_band_half_width_hz"])
        center = float(m["ar_alpha_center_hz"])
        t_m, e_m = af_measure.band_envelope(x, sr, mag["main_band_hz"], float(ap["hop_ms"]),
                                            float(mag["envelope_window_ms"]), order=8)
        t_a, e_a = af_measure.band_envelope(x, sr, (center - half, center + half),
                                            float(ap["hop_ms"]),
                                            float(mag["envelope_window_ms"]), order=6)
        write_json(_contained(out_dir / f"afterglow_{stem}.json", out_dir), {
            "stem": stem, "sr": sr, "ar_alpha_center_hz": center,
            "t_ms": [round(float(v), 3) for v in t_m],
            "main_rms": [round(float(v), ap["round_decimals"]) for v in e_m],
            "ar_alpha_rms": [round(float(v), ap["round_decimals"]) for v in e_a]})
        written.append(f"afterglow_{stem}.json")
    return {"files": written}


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------
def run(spec_path: Path, criteria_path: Path, controls_path: Path, probes_path: Path,
        out_dir: Path) -> int:
    """§23 の Phase 0-9 を実行する。

    途中で落ちた場合は **直前の成果物ツリーを丸ごと戻してから** 送出する
    （空/partial なツリーだけを canonical 位置に残さない = §28）。
    """
    out_dir = Path(out_dir)
    reject_output_collision(out_dir, [Path(spec_path), Path(criteria_path),
                                      Path(controls_path), Path(probes_path),
                                      HERE, HERE.parents[2]])
    tree = prepare_output_tree(out_dir)
    try:
        code = _run_phases(spec_path, criteria_path, controls_path, probes_path, out_dir,
                           tree)
    except BaseException:
        outcome = restore_or_clear_output_tree(out_dir, tree.get("snapshot"))
        _log("abort", f"run aborted; {outcome['disposition']}")
        raise
    discard_output_snapshot(tree.get("snapshot"))
    return code


def _run_phases(spec_path: Path, criteria_path: Path, controls_path: Path,
                probes_path: Path, out_dir: Path, tree: Mapping[str, Any]) -> int:
    notes: List[str] = []
    meas_dir = out_dir / "measurements"
    meas_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- Phase 0: Preregister + pin --------------------------
    _log("phase0", f"preregister + pin (result tree rebuilt: {tree})")
    spec_raw = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    spec_errors = list(validate_founder_spec(spec_raw))
    closure_digest, closure_rows = af_gates.code_closure_digest(HERE)

    # pinned input（criteria / controls / probes）の契約違反も G1 で止める。
    # ハッシュを残すだけでは「正しい文書か」を保証できないため、identity と
    # 凍結値の不一致は spec 不正と同じ扱いにする（判定不能 = BLOCKED）。
    criteria = None
    controls: Dict[str, Any] = {}
    controls_sha = probes_sha = ""
    probes: Dict[str, Any] = {}
    try:
        criteria = load_criteria(criteria_path)
        controls, controls_sha = load_controls(controls_path)
        probes, probes_sha = load_probes(probes_path)
    except SpecError as exc:
        spec_errors += [f"pinned input rejected: {e}" for e in exc.errors]
    gate_g1 = af_gates.gate_spec_valid(spec_errors)

    if spec_errors:
        _log("phase0", f"G1 SPEC_VALID = FAIL ({len(spec_errors)} errors) — 以降を実行しない")
        return _finalize(out_dir, None, [gate_g1],
                         {"criteria_sha256": criteria.sha256 if criteria else None,
                          "controls_sha256": controls_sha,
                          "probes_sha256": probes_sha, "code_closure_sha256": closure_digest},
                         {}, None,
                         {"notes": ["inputs invalid; no compilation attempted"]})

    genome = genome_from_dict(spec_raw)
    pins = {
        "spec_sha256": genome.sha256,
        "criteria_sha256": criteria.sha256,
        "controls_sha256": controls_sha,
        "probes_sha256": probes_sha,
        "code_closure_sha256": closure_digest,
        "python": sys.version.split()[0],
        "numpy": np.__version__,
    }
    try:
        import scipy
        pins["scipy"] = scipy.__version__
    except ModuleNotFoundError:  # pragma: no cover
        pins["scipy"] = None
    try:
        import pyworld
        pins["pyworld"] = getattr(pyworld, "__version__", "unknown")
    except ModuleNotFoundError:
        pins["pyworld"] = None
    write_json(out_dir / "input_pins.json", {**pins, "code_closure_files": len(closure_rows)})
    write_json(out_dir / "code_closure.json",
               {"digest": closure_digest,
                "files": [{"path": p, "sha256": s} for p, s in closure_rows]})

    # ---------------- Phase 1: Meter control ------------------------------
    _log("phase1", "meter control (§17) — AF0 の形質を見る前に計器を検証する")
    control_results = af_controls.run_controls(spec_raw, controls, criteria.metric_definitions)
    write_json(meas_dir / "controls.json", round_floats(control_results, 6))
    gate_g5 = af_gates.gate_meter_control(control_results)
    _log("phase1", f"G5 METER_CONTROL = {gate_g5.verdict} "
                   f"(failed: {gate_g5.detail.get('failed_families')})")
    if gate_g5.verdict != "PASS":
        # §17: 計器が未校正なら **AF0 を評価せず BLOCKED**。ここで止めないと、
        # 未校正の計器が出した AF0 の測定値を先に見てしまい、事前登録した順序
        # （§23 Phase 1 -> Phase 5）が意味を失う。
        notes.append("METER_NOT_CALIBRATED: AF0 was not compiled or measured (§17)")
        _log("phase1", "METER_NOT_CALIBRATED — Phase 2 以降を実行しない（§17）")
        return _finalize(out_dir, genome, [gate_g1, gate_g5], pins, {}, None,
                         {"determinism": {"same_process": "SKIPPED",
                                          "cross_process": "SKIPPED"},
                          "notes": notes})

    # ---------------- Phase 2: Compile AF0 --------------------------------
    _log("phase2", "compile AF0 (source-free tripwire 有効)")
    staging = out_dir / "staging" / "AF0"
    second_dir = out_dir / "staging_repeat" / "AF0"
    # 生成 staging 配下の read は §27 の禁止 read に当たらない（自分が今作った
    # WAV のハッシュを取り直すため）。同一プロセス決定論の比較用ディレクトリも
    # 生成物なので同じ扱いにする。
    audit = af_gates.SourceFreeAudit(
        allowed_roots=[HERE, HERE.parent / "adapter", HERE.parents[1] / "singer"],
        staging_roots=[staging.parent, second_dir.parent],
        pinned_inputs=[Path(spec_path), Path(criteria_path), Path(controls_path),
                       Path(probes_path)])
    with af_gates.source_free_tripwire(audit):
        first = compile_artifacts(genome, staging)
        second = compile_artifacts(genome, second_dir)
    same_process = {
        "match": (first["digest"] == second["digest"]
                  and first["dataset_digest"] == second["dataset_digest"]),
        "digest_a": first["digest"], "digest_b": second["digest"],
        "dataset_digest_a": first["dataset_digest"],
        "dataset_digest_b": second["dataset_digest"],
    }
    shutil.rmtree(second_dir.parent, ignore_errors=True)
    gate_g0 = af_gates.gate_source_free(spec_raw["origin"], audit.as_dict())
    write_json(out_dir / "source_free_attestation.json",
               {"declared": spec_raw["origin"], "audit": audit.as_dict(),
                "verdict": gate_g0.verdict})
    _log("phase2", f"G0 SOURCE_FREE = {gate_g0.verdict}; same-process determinism = "
                   f"{same_process['match']}")

    # ---------------- Phase 3: Independent recompute ----------------------
    _log("phase3", "independent-process recompute")
    with tempfile.TemporaryDirectory() as tmp:
        child = cross_process_digest(Path(spec_path), Path(tmp) / "AF0")
    cross_process = {
        "match": (child.get("digest") == first["digest"]
                  and child.get("dataset_digest") == first["dataset_digest"]),
        "digest_child": child.get("digest"), "digest_parent": first["digest"],
        "dataset_digest_child": child.get("dataset_digest"),
        "dataset_digest_parent": first["dataset_digest"],
    }
    if child.get("error"):
        cross_process["error"] = child["error"]
    gate_g2 = af_gates.gate_determinism(same_process, cross_process)
    _log("phase3", f"G2 DETERMINISTIC_COMPILATION = {gate_g2.verdict}")

    # ---------------- Phase 4: UTAU structural validation -----------------
    _log("phase4", "UTAU structural validation")
    structure = af_utau.validate_body(genome, staging)
    sums = af_utau.check_sha256sums(staging)
    gate_g3 = af_gates.gate_utau_body(structure, sums)
    _log("phase4", f"G3 UTAU_BODY = {gate_g3.verdict}")

    # ---------------- Phase 5: Body measurement ---------------------------
    _log("phase5", "body measurement (44.1 kHz)")
    stems = [u.stem for u in genome.units]
    body_meas = measure_dir(staging / genome.pitch_dir, stems, criteria.metric_definitions)
    write_json(meas_dir / "body.json", round_floats(body_meas, 6))
    dump_probes(staging / genome.pitch_dir, out_dir / "probes" / "body", probes,
                criteria.metric_definitions, body_meas)

    # ---------------- Phase 6: VoiceGenesis ingestion ---------------------
    _log("phase6", "VoiceGenesis ingestion (DonorBank + WORLD + join smoke + dataset)")
    reexp_dir = out_dir / "reexpressed"
    dataset_dir = out_dir / "dataset"
    ingestion_block: Optional[Dict[str, str]] = None
    health = {"verdict": "SKIPPED"}
    join = {"verdict": "SKIPPED"}
    reexpression = {"all_finite": False}
    dataset = {"verdict": "SKIPPED"}
    try:
        import af_ingest
        bank, unit_vowels, clips, bank_stats = af_ingest.build_bank(genome, staging,
                                                                    criteria.ingestion)
        health = af_ingest.bank_health(bank, unit_vowels, clips, bank_stats, criteria.ingestion)
        join = af_ingest.join_smoke(genome, bank, unit_vowels,
                                    criteria.ingestion["join_smoke_sequence"],
                                    out_dir / "join_smoke.wav")
        reexpression = af_ingest.reexpress_body(genome, staging, reexp_dir)
        dataset = convert_founder.convert_from_genome(genome, staging, dataset_dir)
    except AFStop as stop:
        ingestion_block = stop.as_dict()
        notes.append(f"ingestion BLOCKED: {stop.cause}")
        _log("phase6", f"BLOCKED: {stop.cause}")
    # 正規化した記録を **Gate へも** 渡す（Gate detail は p0_results.json へ
    # そのまま載るので、ここで絶対パスを残すと provenance が checkout 依存になる）。
    dataset_record = dict(dataset)
    if dataset_record.get("out_dir"):
        dataset_record["out_dir"] = af_gates.repo_relative(dataset_record["out_dir"])
    gate_g4 = af_gates.gate_ingestion(health, join, reexpression, dataset_record)
    write_json(meas_dir / "ingestion.json",
               round_floats({"donor_bank": health, "join_smoke": join,
                             "reexpression": reexpression, "dataset": dataset_record,
                             "blocked": ingestion_block}, 6))
    _log("phase6", f"G4 VOICEGENESIS_INGESTION = {gate_g4.verdict}")

    # ---------------- Phase 7: Re-expression measurement ------------------
    reexp_meas: Optional[Dict[str, Dict[str, Any]]] = None
    if gate_g4.verdict == "PASS":
        _log("phase7", "re-expression measurement (24 kHz)")
        reexp_meas = measure_dir(reexp_dir, stems, criteria.metric_definitions)
        write_json(meas_dir / "reexpressed.json", round_floats(reexp_meas, 6))
        dump_probes(reexp_dir, out_dir / "probes" / "reexpressed", probes,
                    criteria.metric_definitions, reexp_meas)
    else:
        _log("phase7", "SKIPPED (G4 not PASS)")

    # ---------------- Phase 8: Ground Truth comparison --------------------
    _log("phase8", "ground truth comparison (§18)")
    body_cmp = af_compare.compare_body(genome, criteria, body_meas)
    reexp_cmp = (af_compare.compare_reexpression(genome, criteria, body_meas, reexp_meas)
                 if reexp_meas else None)
    write_json(out_dir / "comparison.json",
               round_floats({"body": body_cmp, "reexpression": reexp_cmp}, 6))
    write_json(meas_dir / "founder_traits.json", round_floats({
        "body": {k: body_cmp[k] for k in ("hl_alpha", "ar_alpha", "ar_beta", "afterglow")},
        "reexpression": ({k: reexp_cmp[k] for k in ("hl_alpha", "ar_alpha", "ar_beta",
                                                    "afterglow")} if reexp_cmp else None),
    }, 6))

    # ---------------- Phase 9: Publish verdict ----------------------------
    _log("phase9", "publish + verdict")
    # 閉包 pin は Phase 0 で **import 後** に取っている。長寿命プロセスで run 中に
    # AF モジュールが書き換わると、実行されたコードと記録された閉包がずれたまま
    # G2 / G14 が PASS しうる。公開の直前に取り直して一致を要求し、run の全区間に
    # わたって「記録された閉包 = 実際に走ったコード」を成立させる。
    closure_recheck, _ = af_gates.code_closure_digest(HERE)
    pins["code_closure_verified"] = closure_recheck == closure_digest
    if not pins["code_closure_verified"]:
        pins["code_closure_sha256_at_publish"] = closure_recheck
        notes.append("code closure changed during the run: "
                     f"{closure_digest} -> {closure_recheck}")
        _log("phase9", "code closure changed mid-run; publication will be withheld")
    pins["body_identity_digest"] = first["digest"]
    published_root = out_dir / "voicebank" / "AF0"
    publication: Dict[str, Any] = {"published": None, "bundle_verified": False,
                                   "partial_artifacts": []}
    prereq = {g.gate_id: g.verdict for g in (gate_g0, gate_g1, gate_g2, gate_g3)}
    blocking = [gid for gid in PUBLICATION_PREREQUISITES if prereq.get(gid) != "PASS"]
    if not pins["code_closure_verified"]:
        blocking = blocking + ["CODE_CLOSURE"]
    if blocking:
        # §28「失敗時は旧 valid bundle を保持」。Source-Free / 決定論 / Body 構造の
        # いずれかが落ちた候補で canonical 公開場所を上書きしない。
        publication["withheld_reason"] = f"publication prerequisites not met: {blocking}"
        notes.append(publication["withheld_reason"])
        _log("phase9", f"publication withheld ({blocking}); previous bundle kept intact")
        gate_g14 = af_gates.GateResult(
            "G14", "PROVENANCE_AND_PUBLICATION", "SKIPPED",
            {"reason_code": "PUBLICATION_WITHHELD", "publication": publication,
             "prerequisites": prereq})
        shutil.rmtree(out_dir / "staging", ignore_errors=True)
        gates = [gate_g0, gate_g1, gate_g2, gate_g3, gate_g4, gate_g5]
        gates += af_gates.trait_gates(body_cmp, reexp_cmp)
        gates.append(gate_g14)
        return _finalize(out_dir, genome, gates, pins, body_cmp, reexp_cmp,
                         {"determinism": {
                             "same_process": "PASS" if same_process["match"] else "FAIL",
                             "cross_process": "PASS" if cross_process["match"] else "FAIL"},
                          "notes": notes})
    pub: Dict[str, Any] = {}
    try:
        # 旧世代は **公開後の検証が通るまで** 残す（`keep_rollback=True`）。
        pub = af_utau.publish_atomically(staging, published_root, keep_rollback=True)
        shutil.rmtree(out_dir / "staging", ignore_errors=True)
        verify = af_utau.check_sha256sums(published_root)
        rows = sha256_tree(published_root, exclude_names=("SHA256SUMS.txt",))
        digest = aggregate_digest(rows)
        publication = {
            "published": af_gates.repo_relative(pub["published"]),
            "rolled_over_previous": pub["rolled_over_previous"],
            "bundle_verified": verify["verdict"] == "PASS" and digest == first["digest"],
            "published_identity_digest": digest,
            "matches_compiled_digest": digest == first["digest"],
            "sha256sums_verdict": verify["verdict"],
            "partial_artifacts": sorted(p.name for p in (out_dir / "staging").glob("*"))
            if (out_dir / "staging").exists() else [],
        }
        if publication["bundle_verified"]:
            af_utau.commit_publication(pub.get("rollback_path"))
        else:
            outcome = af_utau.withdraw_publication(published_root,
                                                   pub.get("rollback_path"))
            publication.update(outcome)
            publication["published"] = None
            notes.append(f"post-publish verification failed; {outcome['disposition']}")
    except BaseException as exc:  # noqa: BLE001 - 公開失敗は provenance 違反として記録する
        outcome = af_utau.withdraw_publication(published_root, pub.get("rollback_path"))
        publication.update(outcome)
        publication["error"] = f"{type(exc).__name__}: {exc}"
        publication["published"] = None
        notes.append(f"publication failed: {publication['error']}; "
                     f"{outcome['disposition']}")
        if not isinstance(exc, Exception):
            raise

    gate_g14 = af_gates.gate_provenance(pins, publication)
    if (published_root / "founder_manifest.json").exists():
        write_json(out_dir / "founder_manifest.json",
                   json.loads((published_root / "founder_manifest.json")
                              .read_text(encoding="utf-8")))
    gates = [gate_g0, gate_g1, gate_g2, gate_g3, gate_g4, gate_g5]
    gates += af_gates.trait_gates(body_cmp, reexp_cmp)
    gates.append(gate_g14)
    return _finalize(out_dir, genome, gates, pins, body_cmp, reexp_cmp,
                     {"determinism": {
                         "same_process": "PASS" if same_process["match"] else "FAIL",
                         "cross_process": "PASS" if cross_process["match"] else "FAIL"},
                      "notes": notes})


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="VoiceGenesis Artificial Founder AF-P0 runner")
    ap.add_argument("--spec", default=str(DEFAULT_SPEC))
    ap.add_argument("--criteria", default=str(DEFAULT_CRITERIA))
    ap.add_argument("--controls", default=str(DEFAULT_CONTROLS))
    ap.add_argument("--probes", default=str(DEFAULT_PROBES))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--compile-only", action="store_true",
                    help="Body だけを生成してダイジェストを JSON 1 行で出す（§19 G2 の "
                         "independent-process recompute 用）")
    args = ap.parse_args(argv)

    if args.compile_only:
        spec_raw = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        genome = genome_from_dict(spec_raw)
        build = compile_artifacts(genome, Path(args.out))
        print(canonical_json({"digest": build["digest"], "n_files": build["n_files"],
                              "dataset_digest": build["dataset_digest"],
                              "dataset_n_files": build["dataset_n_files"]}))
        return 0

    return run(Path(args.spec).resolve(), Path(args.criteria).resolve(),
               Path(args.controls).resolve(), Path(args.probes).resolve(),
               Path(args.out).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
