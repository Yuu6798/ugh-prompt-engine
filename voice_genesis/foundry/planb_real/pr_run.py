"""planb_real/pr_run.py — 実行順オーケストレータ（instruction §17）。

    1. Material Acquisition -> 2. License Gate -> 3. Corpus Census
    -> 4. Ritsu/PJS 共通表現 -> 5. R0–R4 + P0 -> 6. machine gates
    -> 7. human listening -> 8. adjudication

各段は fail-closed。上流が BLOCKED なら下流は SKIPPED として記録し、
**推測で先へ進まない**（§16）。

CLI:
    python pr_run.py acquire --ritsu-archive ... --ritsu-extracted ... --ritsu-license ...
                             --pjs-archive ... --pjs-extracted ... --pjs-license ...
    python pr_run.py census
    python pr_run.py ladder [--max-pairs 4]
    python pr_run.py gates
    python pr_run.py all
    python pr_run.py status
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

_HERE = Path(__file__).resolve().parent
_PLANB = _HERE.parent / "planb"
for _p in (_HERE, _PLANB):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pr_census  # noqa: E402
import pr_gates  # noqa: E402
import pr_identity  # noqa: E402
import pr_lab  # noqa: E402
import pr_ladder  # noqa: E402
import pr_manifest as prm  # noqa: E402
import pr_performance as prp  # noqa: E402
from pr_status import (  # noqa: E402
    BLOCKED, PASS, SKIPPED, GateResult, RunLedger, StopRule, summarize,
)

RESULTS = _HERE / "results"
MANIFEST = RESULTS / "source_manifest.json"
LICENSE = RESULTS / "LICENSE_LEDGER.json"
INV_RITSU = RESULTS / "corpus_inventory_ritsu.json"
INV_PJS = RESULTS / "corpus_inventory_pjs.json"
COVERAGE = RESULTS / "coverage_comparison.json"
LADDER_MANIFEST = RESULTS / "ladder_manifest.json"
TRF_RESULTS = RESULTS / "trf_results.json"
DETERMINISM = RESULTS / "determinism.json"
GATE_RESULTS = RESULTS / "gate_results.json"
EAR_ANSWERS = RESULTS / "ear_answers.json"
CENSUS_GATE = RESULTS / "census_gate.json"
PERF_DIR = RESULTS / "performance_tracks"
ID_DIR = RESULTS / "identity_tracks"
WAV_DIR = RESULTS / "wav"
WAV_STAGING = RESULTS / "wav.staging"

PROBE_PRIORITY = ("terminal_ri", "terminal_su", "terminal_i", "terminal_N", "medial_ri")


def _dumps(obj: Any) -> str:
    """NaN を出さない JSON 直列化（bare NaN は JSON 仕様外 = r3825494572）。"""
    def _clean(o):
        if isinstance(o, float):
            return None if not np.isfinite(o) else o
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_clean(v) for v in o]
        return o
    return json.dumps(_clean(obj), ensure_ascii=False, indent=2, allow_nan=False)


def environment() -> Dict[str, Any]:
    import pyworld
    return {"python": platform.python_version(), "numpy": np.__version__,
            "pyworld": getattr(pyworld, "__version__", "unknown"),
            "platform": platform.platform()}


# ---------------------------------------------------------------------------
# 1. Material Acquisition
# ---------------------------------------------------------------------------
def cmd_acquire(args) -> int:
    manifest = prm.SourceManifest()
    specs = (
        ("ritsu_singing_db", args.ritsu_version, args.ritsu_origin,
         args.ritsu_archive, args.ritsu_license, args.ritsu_extracted),
        ("pjs_corpus", args.pjs_version, args.pjs_origin,
         args.pjs_archive, args.pjs_license, args.pjs_extracted),
    )
    for source, version, origin, archive, lic, extracted in specs:
        if not archive:
            print(f"[skip] {source}: --{source.split('_')[0]}-archive 未指定")
            continue
        manifest.add(prm.build_source_entry(
            source=source, version=version, download_origin=origin,
            archive_path=Path(archive), license_document_path=Path(lic),
            extracted_path=Path(extracted) if extracted else None))
    manifest.write(MANIFEST)
    if not LICENSE.exists():
        prm.blank_ledger().write(LICENSE)
        print(f"[created] {LICENSE} — 規約の確認項目は人が記入すること（自動では埋めない）")
    print(f"[written] {MANIFEST}")
    ledger = RunLedger()
    ledger.add(prm.gate_material(manifest, prm.repo_root_of(_HERE)))
    print(summarize(ledger))
    return ledger.exit_code()


# ---------------------------------------------------------------------------
# 3. Corpus Census
# ---------------------------------------------------------------------------
def _extracted_paths() -> Tuple[Path, Path]:
    manifest = prm.SourceManifest.read(MANIFEST)
    out = []
    for src in prm.REQUIRED_SOURCES:
        entry = manifest.entries.get(src)
        if entry is None or not entry.extracted_path:
            raise StopRule("G-CENSUS", f"{src}: 展開先が manifest に無い",
                           "pr_run.py acquire を --*-extracted 付きで実行すること")
        out.append(Path(entry.extracted_path))
    return out[0], out[1]


def cmd_census(args) -> int:
    ledger = RunLedger()
    try:
        ritsu_root, pjs_root = _extracted_paths()
        inv_r = pr_census.census(ritsu_root, "ritsu_singing_db")
        inv_p = pr_census.census(pjs_root, "pjs_corpus")
        inv_r.write(INV_RITSU)
        inv_p.write(INV_PJS)
        cov = pr_census.coverage_comparison(inv_r, inv_p)
        COVERAGE.parent.mkdir(parents=True, exist_ok=True)
        COVERAGE.write_text(json.dumps(cov, ensure_ascii=False, indent=2), encoding="utf-8")
        gc = pr_census.gate_census(inv_r, inv_p, cov)
        CENSUS_GATE.write_text(json.dumps(gc.as_dict(), ensure_ascii=False, indent=2),
                               encoding="utf-8")
        ledger.add(gc)
    except StopRule as exc:
        ledger.add(exc.as_result())
    print(summarize(ledger))
    return ledger.exit_code()


# ---------------------------------------------------------------------------
# 4–5. 共通表現 + ラダー
# ---------------------------------------------------------------------------
def _load_probe_events(inventory_path: Path, kind: str) -> List[Dict[str, Any]]:
    data = json.loads(inventory_path.read_text(encoding="utf-8"))
    return data.get("probes", {}).get(kind, [])


def _wav_for_lab(lab_path: Path) -> Optional[Path]:
    """命名規則は census と同一の 1 箇所（pr_census.wav_for_lab）に集約する。"""
    return pr_census.wav_for_lab(lab_path)


def _pairs_for_kind(kind: str, max_pairs: int) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    rit = sorted(_load_probe_events(INV_RITSU, kind), key=lambda e: (e["file"], e["phone_index"]))
    pjs = sorted(_load_probe_events(INV_PJS, kind), key=lambda e: (e["file"], e["phone_index"]))
    n = min(len(rit), len(pjs), max_pairs)
    return [(rit[i], pjs[i]) for i in range(n)]


def cmd_ladder(args) -> int:
    ledger = RunLedger()
    pairs_out: List[Any] = []
    frozen: Dict[str, Dict[str, Any]] = {}
    try:
        # 上流（資材・許諾・census 判定）を必ず再検査してから素材を消費する。
        # 以前は census 出力ファイルの存在だけを見ており、G-MATERIAL / G-LICENSE /
        # G-CENSUS が BLOCKED でもラダーが走った（レビュー指摘 r3825325976 /
        # r3825561974）。
        _require_upstream(ledger)
        if not INV_RITSU.exists() or not INV_PJS.exists():
            raise StopRule("G-LADDER", "census 出力が無い", "pr_run.py census を先に実行すること")
        if not CENSUS_GATE.exists():
            raise StopRule("G-CENSUS", "census の判定が記録されていない",
                           "pr_run.py census を実行し直すこと")
        cg = _gate_from_file(CENSUS_GATE, "G-CENSUS")
        if cg.status != PASS:
            raise StopRule("G-CENSUS", f"census が {cg.status}: {cg.detail}", cg.next_action)
        # probe 種を横断して拾う（§8 は primary 1 種 + control 4 種を求めている）。
        # 1 種で max_pairs を食い潰すと control が 1 件も走らないため per-kind で配る。
        # probe 種を **round-robin** で配る。以前は種ごとに per_kind 件を積んでから
        # 先頭を切り落としており、`--max-pairs 4` だと terminal_N / medial_ri の
        # 対照が 1 件も入らなかった（レビュー指摘 r3825626124）。
        per_kind = max(1, int(getattr(args, "per_kind", 2)))
        buckets = {k: _pairs_for_kind(k, per_kind) for k in PROBE_PRIORITY}
        selected: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
        for slot in range(per_kind):
            for kind in PROBE_PRIORITY:
                if slot < len(buckets[kind]) and len(selected) < args.max_pairs:
                    r_ev, p_ev = buckets[kind][slot]
                    selected.append((kind, r_ev, p_ev))
        dropped = {k: max(0, len(v) - sum(1 for x in selected if x[0] == k))
                   for k, v in buckets.items()}
        if any(dropped.values()):
            print(f"[note] 上限により見送った probe: "
                  f"{ {k: v for k, v in dropped.items() if v} }")
        if not selected:
            raise StopRule("G-LADDER", "両側に共通する probe が 1 件も無い",
                           "coverage_comparison.json の usable_for_ladder を確認すること")

        for kind, r_ev, p_ev in selected:
            r_lab_path, p_lab_path = Path(r_ev["file"]), Path(p_ev["file"])
            r_wav, p_wav = _wav_for_lab(r_lab_path), _wav_for_lab(p_lab_path)
            if r_wav is None or p_wav is None:
                raise StopRule("G-LADDER", f"{kind}: lab に対応する wav が見つからない",
                               "corpus の配置（wav と lab の stem 一致）を確認すること")
            r_lab, p_lab = pr_lab.read_lab(r_lab_path), pr_lab.read_lab(p_lab_path)
            bank, probe_pos = pr_identity.build_identity_for_probe(
                r_wav, r_lab, r_ev["phone_index"], source_id="ritsu_singing_db",
                context=int(getattr(args, "context", pr_identity.DEFAULT_CONTEXT_PHONES)))
            # PJS 側も probe 周辺だけを解析する（全長解析は不要かつ遅い）
            p_idx = p_ev["phone_index"]
            p_lo = p_lab.phones[max(0, p_idx - 2)].start_s
            p_hi = p_lab.phones[min(len(p_lab.phones) - 1, p_idx + 1)].end_s
            p_analysis, p_power_db, p_off = pr_identity.analyze_for_performance(
                p_wav, start_s=p_lo - pr_identity.SLICE_PAD_S,
                end_s=p_hi + pr_identity.SLICE_PAD_S)
            p_phones = pr_identity.shift_phones(p_lab.phones, p_off)
            perf = prp.extract_performance(
                f0=p_analysis.f0, power_db=p_power_db,
                frame_period_ms=p_analysis.frame_period_ms, phones=p_phones,
                vowel_index=p_idx, source_id="pjs_corpus",
                source_file=str(p_lab_path), probe_kind=kind)
            donor_core = pr_ladder.donor_core_envelope(
                p_analysis, p_phones, p_idx,
                target_sr=bank.sr, target_bins=bank.sp[probe_pos].shape[1])
            key = f"{kind}|{r_lab_path.stem}#{r_ev['phone_index']}|{p_lab_path.stem}#{p_idx}"
            out_dir = WAV_STAGING / key.replace("|", "__").replace("#", "-")
            result = pr_ladder.run_probe_pair(
                pair_key=key, probe_kind=kind, bank=bank, probe_pos=probe_pos, perf=perf,
                donor_core_sp=donor_core, ritsu_file=str(r_lab_path),
                pjs_file=str(p_lab_path), out_dir=out_dir,
                p0_reference={"pjs_wav": str(p_wav), "pjs_probe_index": p_idx,
                              "pjs_slice_offset_s": round(p_off, 4)})
            # 事前登録: R0 だけを見て閾値を凍結する（R1–R4 は既に計測済みだが参照しない）
            frozen[key] = pr_gates.freeze_trf_gate(result)
            PERF_DIR.mkdir(parents=True, exist_ok=True)
            (PERF_DIR / f"{out_dir.name}.json").write_text(
                json.dumps(perf.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            ID_DIR.mkdir(parents=True, exist_ok=True)
            (ID_DIR / f"{out_dir.name}.json").write_text(json.dumps({
                "source_id": bank.source_id, "identity_sha256": bank.content_sha256(),
                "units": [{"label": u.label, "vowel": u.vowel, "start_s": round(u.start_s, 4),
                           "end_s": round(u.end_s, 4), "is_terminal": u.is_terminal}
                          for u in bank.units],
                "probe_unit_index": probe_pos,
                "nasal_envelope_source": bank.nasal_envelope_source,
                "ap_scale": pr_identity.AP_SCALE,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            pairs_out.append(result)

        # 全ペアが成功してから初めて公開する。途中で落ちた場合、旧 wav と
        # 新 manifest が混在した「一貫して見える」成果物を残さない
        # （レビュー指摘 r3825927055）。
        if WAV_DIR.exists():
            shutil.rmtree(WAV_DIR)
        if WAV_STAGING.exists():
            WAV_STAGING.rename(WAV_DIR)
        for r in pairs_out:
            for name, m in r.rungs.items():
                m.wav_path = str(WAV_DIR / Path(m.wav_path).parent.name / f"{name}.wav")
        LADDER_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        LADDER_MANIFEST.write_text(_dumps({
            "environment": environment(),
            "context_phones": int(getattr(args, "context", pr_identity.DEFAULT_CONTEXT_PHONES)),
            "identity_ap_scale": pr_identity.AP_SCALE,
            "ladder": [n for n, _ in pr_ladder.LADDER],
            "pairs": [p.as_dict() for p in pairs_out],
            "trf_gate_frozen": frozen,
        }), encoding="utf-8")
        TRF_RESULTS.write_text(_dumps({
            "registered_primary_axis": pr_gates.REGISTERED_PRIMARY_AXIS,
            "exploratory_axis": pr_gates.EXPLORATORY_AXIS,
            "exploratory_status": "CANDIDATE_SECONDARY_AXIS",
            "frozen": frozen,
            "per_pair": {p.pair_key: {n: m.trf for n, m in p.rungs.items()}
                         for p in pairs_out},
        }), encoding="utf-8")
        ledger.add(GateResult("G-LADDER", PASS,
                              f"{len(pairs_out)} probe ペアで R0–R4 を生成", "",
                              {"pairs": [p.pair_key for p in pairs_out]}))
    except StopRule as exc:
        ledger.add(exc.as_result())
    print(summarize(ledger))
    return ledger.exit_code()


# ---------------------------------------------------------------------------
# 6–8. gates
# ---------------------------------------------------------------------------
def _reload_pairs():
    data = json.loads(LADDER_MANIFEST.read_text(encoding="utf-8"))
    pairs = []
    for p in data["pairs"]:
        obj = pr_ladder.ProbePairResult(
            pair_key=p["pair_key"], probe_kind=p["probe_kind"], ritsu_file=p["ritsu_file"],
            pjs_file=p["pjs_file"], identity_sha256=p["identity_sha256"],
            performance_sha256=p["performance_sha256"], matching=p["matching"],
            p0_reference=p.get("P0_reference", {}))
        for name, m in p["rungs"].items():
            obj.rungs[name] = pr_ladder.RungMeasurement(**{
                k: v for k, v in m.items() if k != "identity_margin_db"})
        pairs.append(obj)
    return pairs, data.get("trf_gate_frozen", {})


def _recompute_shas_subprocess() -> Dict[str, List[str]]:
    """別プロセスで pin 済み入力から R0–R4 を作り直し、サンプル列 sha256 を得る。"""
    proc = subprocess.run([sys.executable, str(_HERE / "pr_run.py"), "recompute-shas"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return {}
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return {}


def cmd_recompute_shas(args) -> int:
    """pin 済み入力からラダーを作り直し、pair_key -> [sha256...] を JSON で出す。"""
    import pb_compose as pc
    import pr_attribution as pa
    data = json.loads(LADDER_MANIFEST.read_text(encoding="utf-8"))
    ctx = int(data.get("context_phones", pr_identity.DEFAULT_CONTEXT_PHONES))
    out: Dict[str, List[str]] = {}
    for pair in data["pairs"]:
        bank, pos, track = pa._rebuild(pair, ctx)
        out[pair["pair_key"]] = [pc.compose(bank, track, tg).sha256()
                                 for _n, tg in pr_ladder.LADDER]
    print(json.dumps(out))
    return 0


def _run_structural_gate(pairs) -> GateResult:
    """G2 を実際に走らせる（消費した bank / performance / track を作り直す）。"""
    import pr_attribution as pa
    data = json.loads(LADDER_MANIFEST.read_text(encoding="utf-8"))
    ctx = int(data.get("context_phones", pr_identity.DEFAULT_CONTEXT_PHONES))
    # tripwire は release 経路も踏ませたいので、**終端 probe を持つペア**を選ぶ
    # （語中 probe だと `unit.is_terminal` が False で release 分岐に入らず、
    #  どのフィールドが読まれるかが並び順に依存してしまう）
    perfs, chosen = [], None
    for pair in data["pairs"]:
        bank, pos, track = pa._rebuild(pair, ctx)
        perfs.append(pa._rebuild_performance(pair, ctx))
        if chosen is None and pair["probe_kind"].startswith("terminal"):
            chosen = (bank, track)
    last = chosen
    if last is None:
        return GateResult("G2-structural", BLOCKED, "probe ペアが無く G2 を実行できない",
                          "pr_run.py ladder を先に実行すること")
    if not perfs:
        # RealPerformanceTrack を個別に作り直せない場合でも、合成器の
        # アクセス集合（tripwire）だけは必ず実測する
        return pr_gates.gate_structural_separation([], last[0], last[1])
    return pr_gates.gate_structural_separation(perfs, last[0], last[1])


def cmd_gates(args) -> int:
    ledger = RunLedger()
    if not LADDER_MANIFEST.exists():
        ledger.add(GateResult("G-LADDER", BLOCKED, "ladder_manifest.json が無い",
                              "pr_run.py ladder を先に実行すること"))
        print(summarize(ledger))
        return ledger.exit_code()
    pairs, frozen = _reload_pairs()
    # success_level が §12 の「資材・許諾に到達しているか」を見るため、
    # 上流ゲートの現在値をこの ledger にも載せる（別々に走らせても評価が壊れない）。
    if MANIFEST.exists():
        ledger.add(prm.gate_material(prm.SourceManifest.read(MANIFEST),
                                     prm.repo_root_of(_HERE)))
    if LICENSE.exists():
        ledger.add(prm.gate_license(prm.LicenseLedger.read(LICENSE),
                                    prm.SourceManifest.read(MANIFEST) if MANIFEST.exists() else None))

    # G1 は **pin 済み入力から作り直した値**と比較しなければ何も検証しない。
    # 以前は記録済みハッシュをそのまま返しており、値を自分自身と比べていた
    # （レビュー指摘 r3825325974）。別プロセスで再合成して突き合わせる。
    recomputed = _recompute_shas_subprocess()

    def _recompute(pair):
        return recomputed.get(pair.pair_key, ["<not-recomputed>"])

    ledger.add(pr_gates.gate_determinism(pairs, _recompute))
    # G2（構造分離）は cmd_gates から呼ばれていなかった（同 r3825463544）。
    # Performance の 1 次元性と合成器のアクセス集合を実測する唯一のゲートなので、
    # 消費した bank / performance / track を作り直して必ず走らせる。
    ledger.add(_run_structural_gate(pairs))
    ledger.add(pr_gates.gate_intervention_tripwire(pairs))
    ledger.add(pr_gates.gate_donor_texture_isolation(pairs))
    ledger.add(pr_gates.gate_ladder_attribution(pairs))
    ledger.add(pr_gates.gate_identity(pairs))
    ledger.add(pr_gates.gate_trf(pairs, frozen))
    answers = json.loads(EAR_ANSWERS.read_text(encoding="utf-8")) if EAR_ANSWERS.exists() else None
    ledger.add(pr_gates.gate_ear(answers))
    level = pr_gates.success_level(ledger.gates, pairs, answers)
    GATE_RESULTS.parent.mkdir(parents=True, exist_ok=True)
    GATE_RESULTS.write_text(_dumps({**ledger.as_dict(), "success_level": level}),
                            encoding="utf-8")
    DETERMINISM.write_text(json.dumps(
        {"gate": "G1-determinism",
         "evidence": next((g.evidence for g in ledger.gates if g.gate == "G1-determinism"), {})},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(summarize(ledger))
    print(f"success_level = {level}")
    return ledger.exit_code()


def _gate_from_file(path: Path, gate: str) -> GateResult:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return GateResult(d.get("gate", gate), d.get("status", BLOCKED),
                          d.get("detail", ""), d.get("next_action", ""))
    except Exception as exc:  # noqa: BLE001
        return GateResult(gate, BLOCKED, f"{path.name} を読めない ({type(exc).__name__})")


def _require_upstream(ledger: RunLedger) -> None:
    """上流ゲート（資材・許諾・census）が PASS でなければ StopRule。"""
    if not MANIFEST.exists():
        raise StopRule("G-MATERIAL", "source_manifest.json が無い", "pr_run.py acquire")
    r = prm.gate_material(prm.SourceManifest.read(MANIFEST), prm.repo_root_of(_HERE))
    ledger.add(r)
    if r.status != PASS:
        raise StopRule(r.gate, r.detail, r.next_action)
    if not LICENSE.exists():
        raise StopRule("G-LICENSE", "LICENSE_LEDGER.json が無い", "pr_run.py acquire")
    r = prm.gate_license(prm.LicenseLedger.read(LICENSE), prm.SourceManifest.read(MANIFEST))
    ledger.add(r)
    if r.status != PASS:
        raise StopRule(r.gate, r.detail, r.next_action)


def cmd_status(args) -> int:
    ledger = RunLedger()
    if MANIFEST.exists():
        ledger.add(prm.gate_material(prm.SourceManifest.read(MANIFEST),
                                     prm.repo_root_of(_HERE)))
    else:
        ledger.add(GateResult("G-MATERIAL", BLOCKED, "source_manifest.json が無い（資材未取得）",
                              "配布元から Ritsu 歌声 DB と PJS を取得し pr_run.py acquire"))
    if LICENSE.exists():
        ledger.add(prm.gate_license(prm.LicenseLedger.read(LICENSE),
                                    prm.SourceManifest.read(MANIFEST) if MANIFEST.exists() else None))
    else:
        ledger.add(GateResult("G-LICENSE", BLOCKED, "LICENSE_LEDGER.json が無い",
                              "pr_run.py acquire が雛形を作る。確認項目は人が記入する"))
    for name, path in (("G-CENSUS", COVERAGE), ("G-LADDER", LADDER_MANIFEST),
                       ("G-GATES", GATE_RESULTS)):
        if not path.exists():
            ledger.add(GateResult(name, SKIPPED, f"{path.name} 未生成（上流が未完了）"))
    print(summarize(ledger))
    return ledger.exit_code()


def cmd_all(args) -> int:
    for fn in (cmd_census, cmd_ladder, cmd_gates):
        code = fn(args)
        if code != 0:
            return code
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Real-Corpus PoC orchestrator")
    sub = ap.add_subparsers(dest="cmd", required=False)

    a = sub.add_parser("acquire", help="§1 資材の来歴固定")
    for side in ("ritsu", "pjs"):
        a.add_argument(f"--{side}-archive")
        a.add_argument(f"--{side}-extracted")
        a.add_argument(f"--{side}-license")
        a.add_argument(f"--{side}-version", default="")
        a.add_argument(f"--{side}-origin", default="")
    a.set_defaults(func=cmd_acquire)

    sub.add_parser("recompute-shas",
                   help="pin 済み入力からラダーを作り直し sha256 を出す（G1 用）"
                   ).set_defaults(func=cmd_recompute_shas)

    for name, fn, helptext in (("census", cmd_census, "§3 コーパス census"),
                               ("gates", cmd_gates, "§10 機械ゲート"),
                               ("status", cmd_status, "現在の到達点"),
                               ("all", cmd_all, "§17 の順に通す")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--max-pairs", type=int, default=10)
        p.add_argument("--per-kind", type=int, default=2)
        p.add_argument("--context", type=int, default=pr_identity.DEFAULT_CONTEXT_PHONES)
        p.set_defaults(func=fn)

    p = sub.add_parser("ladder", help="§7 R0–R4 + P0")
    p.add_argument("--max-pairs", type=int, default=10)
    p.add_argument("--per-kind", type=int, default=2)
    p.add_argument("--context", type=int, default=pr_identity.DEFAULT_CONTEXT_PHONES,
                   help="probe の前に含める音素数（耳判定が成立する長さが要る）")
    p.set_defaults(func=cmd_ladder)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        args = ap.parse_args(["status"])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
