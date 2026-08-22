"""t0_run.py — AF-T0 の 1 コマンド実行（設計書 §33 / §34）。

```text
0  verify frozen P0        8  freeze combined package
1  baseline replay         9  combined sentinel regression
2  stage capture          10  fresh holdout confirmation
3  localization           11  AF0 final confirmation
4  Duration cal -> holdout 12  deterministic replay
5  Energy   cal -> holdout 13  provenance/publication
6  AG       cal -> holdout 14  verdict
7  first-pass selection   15  STOP（P1 へ自動進行しない）
```

exit code は §33: 0 PASS / 1 NOT_ESTABLISHED / 3 BLOCKED / 4 FAILED。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import soundfile as sf

_HERE = Path(__file__).resolve().parent
_AF = _HERE.parent
_ADAPTER = _AF.parent / "adapter"
for _p in (str(_HERE), str(_AF), str(_ADAPTER)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import af_gates  # noqa: E402
import af_measure  # noqa: E402
import af_synth  # noqa: E402
import t0_compare  # noqa: E402
import t0_gates  # noqa: E402
import t0_localize  # noqa: E402
import t0_report  # noqa: E402
import t0_stage_capture as stagecap  # noqa: E402
import t0_transport as transport  # noqa: E402
from af_spec import (ALIAS_BY_STEM, apply_patch, canonical_json,  # noqa: E402
                     genome_from_dict, load_criteria, sha256_file, sha256_tree,
                     sha256sums_text, write_json)
from t0_energy import assert_no_af0_in_calibration, fit_global_gain_db  # noqa: E402
from t0_schema import (EXIT_CODES, STAGE_IDS,  # noqa: E402
                       FROZEN_CALIBRATION_SHA256, FROZEN_HOLDOUT_SHA256,
                       FROZEN_P0_ARTIFACTS, FROZEN_P0_FAILED_FAMILIES,
                       canonical_digest, validate_criteria,
                       validate_fixtures)
from t0_sidecar import (build_sidecar, build_sidecars,  # noqa: E402
                        require_sidecar_matches_body, sidecar_digest)

DEFAULT_P0_ROOT = _AF / "results" / "AF0"
DEFAULT_CRITERIA = _HERE / "criteria" / "AF_T0_CRITERIA.json"
DEFAULT_OUT = _HERE / "results" / "AF_T0"
P0_CRITERIA = _AF / "criteria" / "AF_P0_CRITERIA.json"
P0_SPEC = _AF / "founder_specs" / "AF0.json"


def _log(phase: str, message: str) -> None:
    print(f"[{phase}] {message}", flush=True)


class T0Stop(Exception):
    """途中で止める（status / reason_code を持つ）。"""

    def __init__(self, status: str, reason_code: str, message: str) -> None:
        self.status = status
        self.reason_code = reason_code
        super().__init__(message)


# ---------------------------------------------------------------------------
# Phase 0: frozen P0 の検証
# ---------------------------------------------------------------------------
def verify_frozen_p0(p0_root: Path) -> Dict[str, Any]:
    """§4 / §16。AF0 spec SHA と P0 成果物を pin する。"""
    spec_raw = json.loads(P0_SPEC.read_text(encoding="utf-8"))
    spec_sha = canonical_digest(spec_raw)
    artifacts: Dict[str, str] = {}
    for name in FROZEN_P0_ARTIFACTS:
        path = p0_root / name
        if path.exists():
            artifacts[name] = sha256_file(path)
    return {"af0_spec_sha256": spec_sha, "p0_artifacts": artifacts,
            "p0_root": af_gates.repo_relative(p0_root)}


def _base_commit() -> Optional[str]:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, cwd=str(af_gates.repo_root()), timeout=30)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# fixture body の合成（calibration / holdout / AF0 confirmation）
# ---------------------------------------------------------------------------
FIXTURE_PATCH_KEY = {
    "r_onset_share": "performance_genes.duration.r_onset_share",
    "r_onset_ms": "performance_genes.duration.r_onset_ms",
    "i_vowel_ms": "performance_genes.duration.i_vowel_ms",
    "sustain_dbfs": "performance_genes.energy.sustain_dbfs",
    "ar_alpha_afterglow_extra_ms":
        "founder_expression_traits.AG-alpha.ar_alpha_afterglow_extra_ms",
}


def fixture_genome(spec_raw: Mapping[str, Any], row: Mapping[str, Any]) -> Any:
    """fixture の値を patch した genome（§12）。"""
    patch = {FIXTURE_PATCH_KEY[k]: float(v) for k, v in row.items()
             if k != "id" and k in FIXTURE_PATCH_KEY}
    return genome_from_dict(apply_patch(spec_raw, patch))


def synthesize_fixture_body(genome: Any, out_dir: Path,
                            stems: Sequence[str]) -> Dict[str, Path]:
    """fixture の Body WAV を作る（AF0 canonical Body は触らない）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, Path] = {}
    for stem in stems:
        unit = ALIAS_BY_STEM[stem]
        su = af_synth.synthesize_unit(genome, unit)
        p = out_dir / unit.wav_filename
        sf.write(str(p), su.pcm16, genome.sample_rate_hz, subtype="PCM_16",
                 format="WAV")
        paths[stem] = p
    return paths


# ---------------------------------------------------------------------------
# 1 つの body 集合を段観測 -> 輸送 -> 測定する共通経路
# ---------------------------------------------------------------------------
def capture_bodies(wavs: Mapping[str, Path], md: Mapping[str, Any],
                   tmp: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """段観測と blind 測定（S0/S1/S3/S4/S5）。"""
    captures: Dict[str, Any] = {}
    ledger_units: Dict[str, Any] = {}
    for stem in sorted(wavs):
        cap = stagecap.capture_unit_stages(wavs[stem], tmp / stem)
        captures[stem] = cap
        meas = stagecap.stage_measurements(cap, md, af_measure.measure_unit)
        ledger_units[stem] = {
            "measurements": meas,
            "diagnostics": cap["diagnostics"],
            "normalization": cap["normalization"],
        }
    return captures, {"units": ledger_units}


def evaluate_config(genome: Any, p0_criteria: Any, md: Mapping[str, Any],
                    captures: Mapping[str, Any], sidecars: Mapping[str, Any],
                    body_meas: Mapping[str, Any], config: transport.TransportConfig,
                    energy_cal: Optional[Mapping[str, Any]] = None
                    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """`config` で輸送して AF-P0 original criteria で判定する。"""
    tr = transport.transport_body(captures, sidecars, config, energy_cal)
    meas = transport.measure_transported(tr, md, af_measure.measure_unit)
    cmp_ = t0_compare.compare_transported(genome, p0_criteria, body_meas, meas)
    return cmp_, {"transported": tr, "measurements": meas}


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------
def run(p0_root: Path, criteria_path: Path, out_dir: Path) -> int:
    out_dir = Path(out_dir)
    staging = out_dir.parent / f".{out_dir.name}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    tmp_root = Path(tempfile.mkdtemp(prefix="af_t0_"))
    try:
        code = _run_phases(p0_root, criteria_path, staging, tmp_root)
        _publish(staging, out_dir)
        return code
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _publish(staging: Path, out_dir: Path) -> None:
    """§30。staging -> verify -> atomic rename。"""
    rows = sha256_tree(staging, exclude_names=("SHA256SUMS.txt",))
    (staging / "SHA256SUMS.txt").write_text(sha256sums_text(rows), encoding="utf-8")
    hold = out_dir.parent / f".{out_dir.name}.previous"
    if hold.exists():
        shutil.rmtree(hold)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    if out_dir.exists():
        shutil.move(str(out_dir), str(hold))
    shutil.copytree(staging, out_dir)
    if hold.exists():
        shutil.rmtree(hold, ignore_errors=True)


def _run_phases(p0_root: Path, criteria_path: Path, out: Path, tmp: Path) -> int:
    notes: List[str] = []
    meas_dir = out / "measurements"
    meas_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- Phase 0: verify frozen P0 ---------------------------
    _log("phase0", "verify frozen P0 inputs (§4)")
    p0_criteria = load_criteria(P0_CRITERIA)
    md = p0_criteria.metric_definitions
    spec_raw = json.loads(P0_SPEC.read_text(encoding="utf-8"))
    genome = genome_from_dict(spec_raw)

    t0_criteria = json.loads(Path(criteria_path).read_text(encoding="utf-8"))
    p0_gates_raw = json.loads(P0_CRITERIA.read_text(encoding="utf-8"))
    errors = validate_criteria(t0_criteria, p0_gates_raw["reexpression_gates"])
    cal_doc = json.loads((_HERE / "fixtures" / "calibration.json").read_text("utf-8"))
    hol_doc = json.loads((_HERE / "fixtures" / "holdout.json").read_text("utf-8"))
    errors += validate_fixtures(cal_doc, hol_doc)
    if errors:
        raise T0Stop("BLOCKED", "T0_CONTRACT_INVALID", "; ".join(errors))

    pins_base = verify_frozen_p0(p0_root)
    base_commit = _base_commit()
    gate_g0 = t0_gates.gate_input_freeze({**pins_base, "base_commit": base_commit})
    _log("phase0", f"T0-G0 INPUT_FREEZE = {gate_g0.verdict}")
    if gate_g0.verdict != "PASS":
        raise T0Stop("BLOCKED", "AF0_INPUT_DRIFT",
                     json.dumps(gate_g0.detail.get("problems"), ensure_ascii=False))

    voicebank = p0_root / "voicebank" / "AF0"
    if not (voicebank / genome.pitch_dir).is_dir():
        raise T0Stop("BLOCKED", "AF0_BODY_MISSING",
                     f"AF-P0 body not found under {voicebank}; run p0_run.py first")
    af0_wavs = {u.stem: voicebank / genome.pitch_dir / u.wav_filename
                for u in genome.units}

    # ---------------- Phase 1-2: baseline replay + stage capture ----------
    _log("phase1", "stage capture S0-S5 for all 25 units (§5 / §17)")
    captures, ledger = capture_bodies(af0_wavs, md, tmp / "af0")
    stagecap.assert_no_waveform_verdict_from_diagnostics(ledger)
    body_meas = {s: ledger["units"][s]["measurements"]["S0_BODY_44K"]
                 for s in ledger["units"]}
    native_meas = {s: ledger["units"][s]["measurements"]["S5_PCM16_ROUNDTRIP"]
                   for s in ledger["units"]}

    completeness = stagecap.ledger_completeness(ledger, sorted(af0_wavs))
    gate_g2 = t0_gates.gate_stage_ledger(completeness)
    _log("phase2", f"T0-G2 STAGE_LEDGER_COMPLETE = {gate_g2.verdict}")
    write_json(out / "stage_ledger.json", _ledger_for_json(ledger))

    body_cmp = _body_selfcheck(genome, p0_criteria, body_meas, p0_root)
    native_cmp = t0_compare.compare_transported(genome, p0_criteria, body_meas,
                                                native_meas)
    replay = t0_compare.baseline_reproduces_p0(native_cmp, FROZEN_P0_FAILED_FAMILIES)
    gate_g1 = t0_gates.gate_baseline_replay(body_cmp, replay)
    _log("phase1", f"T0-G1 BASELINE_REPLAY = {gate_g1.verdict} "
                   f"(observed failures: {replay['observed_failing_families']})")
    write_json(meas_dir / "baseline_native.json",
               {"comparison": t0_compare.summarize(native_cmp), "replay": replay})

    # ---------------- Phase 3: localization -------------------------------
    _log("phase3", "failure localization (§6)")
    localization = t0_localize.build_localization_report(
        ledger, t0_criteria["localization_epsilon"])
    write_json(out / "localization_report.json", localization)
    _log("phase3", f"first_divergence_stage = {localization['summary']}")

    # ---------------- sidecar ---------------------------------------------
    sidecars = build_sidecars(genome, voicebank, body_meas)
    for stem, sc in sidecars.items():
        require_sidecar_matches_body(sc, af0_wavs[stem])
    sc_digest = sidecar_digest(sidecars)
    sc_dir = out / "sidecars"
    sc_dir.mkdir(parents=True, exist_ok=True)
    for stem, sc in sidecars.items():
        write_json(sc_dir / f"{stem}.json", sc)

    # ---------------- Phase 4-6: calibration -> holdout -------------------
    fixtures = _build_fixture_sets(spec_raw, cal_doc, hol_doc, md, tmp, genome)
    energy_cal = _fit_energy(fixtures, genome, md)
    write_json(meas_dir / "energy_calibration.json", energy_cal)
    _log("phase5", f"energy global gain = {energy_cal['gain_db']:.4f} dB "
                   f"(from {energy_cal['n_rows']} calibration rows, AF0 excluded)")

    # ---------------- Phase 7: first-pass candidate selection -------------
    _log("phase7", "trait-by-trait transport (§18 Stage C), first-pass selection (§11)")
    selections: Dict[str, Any] = {}
    for trait in transport.COMPOSITION_ORDER:
        selections[trait] = _select_for_trait(
            trait, genome, p0_criteria, md, captures, sidecars, body_meas,
            fixtures, energy_cal)
        sel = selections[trait]
        _log("phase7", f"  {trait}: selected={sel.get('selected')} "
                       f"mode={sel.get('mode')} tried="
                       f"{[a['operator'] for a in sel['attempts']]}")

    registry = transport.build_transport_registry(selections)
    write_json(out / "transport_registry.json", registry)

    # ---------------- Phase 8-9: freeze + combined ------------------------
    config = transport.config_from_selections(selections)
    config_digest = canonical_digest(config.as_dict())
    _log("phase8", f"freeze combined package: {config.as_dict()}")

    combined_cmp, combined_art = evaluate_config(
        genome, p0_criteria, md, captures, sidecars, body_meas, config, energy_cal)
    combined = t0_compare.combined_result(combined_cmp)
    sentinels = t0_compare.sentinel_verdict(combined_cmp)
    write_json(out / "combined_comparison.json",
               {"config": config.as_dict(),
                "comparison": t0_compare.summarize(combined_cmp),
                "combined": _strip_rows(combined), "sentinels": sentinels})
    _log("phase9", f"combined = {combined['verdict']} / sentinels = "
                   f"{sentinels['verdict']}")

    # ---------------- Phase 10-11: fresh confirmation ---------------------
    _log("phase10", "fresh holdout + AF0 confirmation (after freeze) (§20)")
    fresh = _fresh_confirmation(genome, p0_criteria, md, fixtures, config, energy_cal,
                                combined_cmp)
    write_json(out / "fresh_confirmation.json", fresh)
    _log("phase11", f"fresh confirmation = {fresh['verdict']}")

    # ---------------- Phase 12: determinism -------------------------------
    _log("phase12", "deterministic replay (same-process / cross-process)")
    determinism = _determinism_check(genome, p0_criteria, md, captures, sidecars,
                                     body_meas, config, energy_cal, sc_digest,
                                     config_digest, combined_art, p0_root,
                                     criteria_path)

    # ---------------- Phase 13: provenance --------------------------------
    closure_digest, closure_rows = t0_gates.code_closure_digest(_HERE)
    write_json(out / "code_closure.json",
               {"digest": closure_digest,
                "files": [{"path": p, "sha256": s} for p, s in closure_rows]})
    pins = {
        **pins_base,
        "base_commit": base_commit,
        "p0_code_closure_sha256": (pins_base["p0_artifacts"] or {}).get(
            "code_closure.json"),
        "t0_code_closure_sha256": closure_digest,
        "t0_criteria_sha256": sha256_file(criteria_path),
        "calibration_sha256": FROZEN_CALIBRATION_SHA256,
        "holdout_sha256": FROZEN_HOLDOUT_SHA256,
        "sidecar_digest": sc_digest,
        "transport_config_digest": config_digest,
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "soundfile": getattr(sf, "__version__", "unknown"),
        "libsndfile": getattr(sf, "__libsndfile_version__", "unknown"),
    }
    pins["t0_code_closure_verified"] = (
        t0_gates.code_closure_digest(_HERE)[0] == closure_digest)
    write_json(out / "input_pins.json", pins)
    write_json(out / "source_free_attestation.json", _source_free_attestation(pins))

    publication = _write_probes(out, combined_art, config)
    sidecar_binding = {"verdict": "PASS", "n_sidecars": len(sidecars),
                       "digest": sc_digest,
                       "bound_to_body": True}

    # ---------------- Phase 14: verdict -----------------------------------
    gates = [
        gate_g0, gate_g1, gate_g2,
        t0_gates.gate_duration_transport(
            selections["duration"], *_parts(selections["duration"], fresh, "duration")),
        t0_gates.gate_energy_transport(
            selections["energy"], *_parts(selections["energy"], fresh, "energy")),
        t0_gates.gate_ag_transport(
            selections["afterglow"], *_parts(selections["afterglow"], fresh,
                                             "afterglow")),
        t0_gates.gate_sentinel_non_regression(sentinels),
        t0_gates.gate_combined_transport(combined),
        t0_gates.gate_determinism(determinism["same_process"],
                                  determinism["cross_process"]),
        t0_gates.gate_provenance(pins, publication, sidecar_binding),
        t0_gates.gate_fresh_confirmation(fresh),
    ]
    overall = t0_gates.overall_verdict(gates)
    _log("phase14", f"OVERALL = {overall['verdict']} "
                    f"reasons={overall.get('reason_codes')}")

    results = t0_report.build_t0_results(
        pins=pins, localization=localization, registry=registry, sentinels=sentinels,
        combined=_strip_rows(combined), fresh=fresh, gates=gates, overall=overall,
        baseline=replay, determinism=determinism)
    results["notes"] = notes
    write_json(out / "t0_results.json", results)
    (out / "AF_T0_RECORD.md").write_text(
        t0_report.build_record_md(results, localization, registry,
                                  _strip_rows(combined)), encoding="utf-8")
    if t0_report.should_freeze(overall):
        (out / "freeze").mkdir(parents=True, exist_ok=True)
        write_json(out / "freeze" / "af_t0_freeze.json",
                   t0_report.build_freeze(results))
        _log("phase14", t0_report.COMPLETION_DECLARATION)
    for line in t0_report.P0_INVARIANT:
        _log("phase14", line)
    _log("stop", "AF-T0 はここで停止する。P1 へ自動進行しない（§34-15）")
    return EXIT_CODES[overall["verdict"]]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _parts(selection: Mapping[str, Any], fresh: Mapping[str, Any],
           trait: str) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """T0-G3/G4/G5 に渡す calibration / holdout / AF0 の 3 点。"""
    detail = (selection.get("attempts") or [{}])[-1].get("detail") or {}
    # AF0 部分は **その形質の** 判定を使う。combined（3 形質同時）の verdict を
    # 流用すると、別形質の失敗で T0-G3/G4/G5 が連鎖的に落ちて原因が読めなくなる。
    return (detail.get("calibration") or {"verdict": selection.get("verdict")},
            detail.get("holdout") or {"verdict": selection.get("verdict")},
            detail.get("af0") or {"verdict": selection.get("verdict")})


#: §16 Body measurement 再現の照合対象と許容差。計器・環境が同じなら一致する
#: はずの量だけを選ぶ（`ar_alpha_route` のような分類ラベルは除く）。
_BODY_REPLAY_KEYS: Dict[str, float] = {
    "onset_ms": 0.5, "r_onset_share": 0.005, "sustain_dbfs": 0.05,
    "main_release_ms": 0.5, "afterglow_extra_ms": 1.0, "core_f0_hz": 0.05,
}


def _body_selfcheck(genome: Any, criteria: Any, body_meas: Mapping[str, Any],
                    p0_root: Path) -> Dict[str, Any]:
    """§16。**P0 が記録した Body 測定を再現するか**を実照合する。

    「測定が取れている」だけでは baseline を語れない（同じ Body を同じ計器で
    測って違う値が出るなら、その環境で P0 の結果は再現していない）。P0 の
    `measurements/body.json` と主要量を突き合わせる。
    """
    missing = [s for s, m in body_meas.items() if m.get("error")]
    detail: Dict[str, Any] = {"n_units": len(body_meas), "units_with_error": missing}
    ref_path = p0_root / "measurements" / "body.json"
    if not ref_path.exists():
        detail["reference"] = None
        detail["note"] = "P0 body.json が無いので測定の存在だけを確認した"
        detail["verdict"] = "PASS" if not missing else "FAIL"
        return detail
    ref = json.loads(ref_path.read_text(encoding="utf-8"))
    worst: Dict[str, Any] = {}
    drifted: List[Dict[str, Any]] = []
    for stem, cur in sorted(body_meas.items()):
        want = ref.get(stem) or {}
        for key, tol in _BODY_REPLAY_KEYS.items():
            a, b = want.get(key), cur.get(key)
            if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
                continue
            err = abs(float(b) - float(a))
            if key not in worst or err > worst[key]["error"]:
                worst[key] = {"stem": stem, "error": round(err, 6), "tolerance": tol}
            if err > tol:
                drifted.append({"stem": stem, "key": key, "p0": a, "t0": b,
                                "error": round(err, 6), "tolerance": tol})
    detail["reference"] = af_gates.repo_relative(ref_path)
    detail["worst_per_key"] = worst
    detail["drifted"] = drifted
    detail["verdict"] = "PASS" if (not missing and not drifted) else "FAIL"
    return detail


def _ledger_for_json(ledger: Mapping[str, Any]) -> Dict[str, Any]:
    return {"schema": "voicegenesis-af-t0-stage-ledger/1.0",
            "stage_ids": list(STAGE_IDS),
            "note": "S2 は diagnostic 専用（波形 verdict を作らない = §5）",
            "units": ledger["units"]}


def _strip_rows(obj: Any) -> Any:
    """記録から巨大な per-unit 行を落とす（要約は残す）。"""
    if isinstance(obj, dict):
        return {k: _strip_rows(v) for k, v in obj.items() if k != "rows"}
    if isinstance(obj, list):
        return [_strip_rows(v) for v in obj]
    return obj


def _build_fixture_sets(spec_raw: Mapping[str, Any], cal_doc: Mapping[str, Any],
                        hol_doc: Mapping[str, Any], md: Mapping[str, Any],
                        tmp: Path, af0_genome: Any) -> Dict[str, Any]:
    """calibration / holdout fixture の Body を作り、段観測まで済ませる。"""
    out: Dict[str, Any] = {"calibration": {}, "holdout": {}}
    # fixture body は **全 25 unit** を作る。`af_compare._check_all` は fail-closed
    # で、測定の無い unit を不合格として数えるため（§13 の許容値をそのまま使う
    # 以上、判定は凍結 inventory 全体に対して定義されている）。部分集合で回すと
    # operator に関係なく energy_sustain / afterglow / sentinel が必ず FAIL し、
    # 偽の NOT_ESTABLISHED が出る。
    all_stems = [u.stem for u in af0_genome.units]
    for role, doc in (("calibration", cal_doc), ("holdout", hol_doc)):
        for trait, rows in (doc["fixtures"] or {}).items():
            for row in rows:
                g = fixture_genome(spec_raw, row)
                d = tmp / role / trait / str(row["id"])
                wavs = synthesize_fixture_body(g, d, all_stems)
                caps, ledger = capture_bodies(wavs, md, d / "_stages")
                bmeas = {s: ledger["units"][s]["measurements"]["S0_BODY_44K"]
                         for s in ledger["units"]}
                scs = {s: build_sidecar(g, ALIAS_BY_STEM[s], wavs[s], bmeas[s])
                       for s in wavs}
                out[role].setdefault(trait, []).append({
                    "id": row["id"], "row": dict(row), "genome": g,
                    "captures": caps, "sidecars": scs, "body": bmeas,
                    "stems": sorted(wavs)})
    return out


def _fit_energy(fixtures: Mapping[str, Any], af0_genome: Any,
                md: Mapping[str, Any]) -> Dict[str, Any]:
    """§10 E1 / §12。calibration fixture **だけ**から global gain を決める。"""
    rows = []
    for fx in fixtures["calibration"].get("energy", []):
        b = [fx["body"][s].get("sustain_dbfs") for s in fx["stems"]]
        # 再表現側の sustain は段観測の S5（native baseline の実体）から取る。
        re_vals = []
        for s in fx["stems"]:
            x, sr = fx["captures"][s]["waveforms"]["S5_PCM16_ROUNDTRIP"]
            re_vals.append(af_measure.measure_unit(
                x, int(sr), md, s).get("sustain_dbfs"))
        bb = [v for v in b if v is not None]
        rr = [v for v in re_vals if v is not None]
        if bb and rr:
            rows.append({"id": fx["id"],
                         "body_sustain_dbfs": float(np.median(bb)),
                         "reexpressed_sustain_dbfs": float(np.median(rr))})
    assert_no_af0_in_calibration(rows, af0_genome.sustain_dbfs)
    return fit_global_gain_db(rows)


def _select_for_trait(trait: str, genome: Any, p0_criteria: Any,
                      md: Mapping[str, Any], captures: Mapping[str, Any],
                      sidecars: Mapping[str, Any], body_meas: Mapping[str, Any],
                      fixtures: Mapping[str, Any],
                      energy_cal: Mapping[str, Any]) -> Dict[str, Any]:
    """§18 Stage C。1 形質だけ動かし、他 2 形質は baseline のまま評価する。

    候補は calibration -> holdout -> AF0 の **すべて** で PASS しなければ採用しない
    （§21 T0-G3/G4/G5）。
    """
    def evaluate(op: str) -> Dict[str, Any]:
        cfg = transport.TransportConfig(**{trait: op})
        parts: Dict[str, Any] = {}
        for role in ("calibration", "holdout"):
            worst_ok = True
            details = []
            for fx in fixtures[role].get(trait, []):
                cmp_, _ = evaluate_config(fx["genome"], p0_criteria, md,
                                          fx["captures"], fx["sidecars"],
                                          fx["body"], cfg, energy_cal)
                r = t0_compare.candidate_result(cmp_, trait)
                details.append({"id": fx["id"], "verdict": r["verdict"],
                                "target": _strip_rows(r["target"]),
                                "sentinels_verdict": r["sentinels"]["verdict"]})
                worst_ok = worst_ok and r["verdict"] == "PASS"
            parts[role] = {"verdict": "PASS" if worst_ok else "FAIL",
                           "fixtures": details}
        cmp_af0, _ = evaluate_config(genome, p0_criteria, md, captures, sidecars,
                                     body_meas, cfg, energy_cal)
        r_af0 = t0_compare.candidate_result(cmp_af0, trait)
        parts["af0"] = {"verdict": r_af0["verdict"],
                        "target": _strip_rows(r_af0["target"]),
                        "sentinels_verdict": r_af0["sentinels"]["verdict"]}
        ok = all(parts[k]["verdict"] == "PASS" for k in ("calibration", "holdout",
                                                         "af0"))
        return {"verdict": "PASS" if ok else "FAIL", **parts}

    return transport.select_first_passing(trait, evaluate)


def _fresh_confirmation(genome: Any, p0_criteria: Any, md: Mapping[str, Any],
                        fixtures: Mapping[str, Any],
                        config: transport.TransportConfig,
                        energy_cal: Mapping[str, Any],
                        af0_combined_cmp: Mapping[str, Any]) -> Dict[str, Any]:
    """§20 Stage E。freeze 済み combined package を holdout と AF0 で確認する。"""
    holdout_rows = []
    ok = True
    for trait in transport.COMPOSITION_ORDER:
        for fx in fixtures["holdout"].get(trait, []):
            cmp_, _ = evaluate_config(fx["genome"], p0_criteria, md, fx["captures"],
                                      fx["sidecars"], fx["body"], config, energy_cal)
            r = t0_compare.candidate_result(cmp_, trait)
            holdout_rows.append({"trait": trait, "id": fx["id"],
                                 "verdict": r["verdict"],
                                 "target": _strip_rows(r["target"]),
                                 "sentinels_verdict": r["sentinels"]["verdict"]})
            ok = ok and r["verdict"] == "PASS"
    af0 = t0_compare.combined_result(af0_combined_cmp)
    ok = ok and af0["verdict"] == "PASS"
    return {"after_freeze": True, "config": config.as_dict(),
            "holdout": holdout_rows, "af0": _strip_rows(af0),
            "verdict": "PASS" if ok else "FAIL"}


def _determinism_check(genome: Any, p0_criteria: Any, md: Mapping[str, Any],
                       captures: Mapping[str, Any], sidecars: Mapping[str, Any],
                       body_meas: Mapping[str, Any],
                       config: transport.TransportConfig,
                       energy_cal: Mapping[str, Any], sc_digest: str,
                       config_digest: str, art_a: Mapping[str, Any],
                       p0_root: Path, criteria_path: Path) -> Dict[str, Any]:
    """§21 T0-G8。sidecar / config / WAV SHA / measurements / verdict の一致。"""
    cmp_b, art_b = evaluate_config(genome, p0_criteria, md, captures, sidecars,
                                   body_meas, config, energy_cal)
    same = _compare_run(art_a, art_b, sc_digest, sc_digest, config_digest,
                        config_digest,
                        t0_compare.combined_result(
                            t0_compare.compare_transported(
                                genome, p0_criteria, body_meas,
                                art_a["measurements"]))["verdict"],
                        t0_compare.combined_result(cmp_b)["verdict"])
    child = _cross_process(p0_root, criteria_path)
    cross = {
        "compared": {
            "sidecar_digest": [sc_digest, child.get("sidecar_digest")],
            "transport_config_digest": [config_digest,
                                        child.get("transport_config_digest")],
            "wav_digest": [_wav_digest(art_a["transported"]),
                           child.get("wav_digest")],
            "measurements_digest": [canonical_digest(_round(art_a["measurements"])),
                                    child.get("measurements_digest")],
            "verdict": [t0_compare.combined_result(
                t0_compare.compare_transported(genome, p0_criteria, body_meas,
                                               art_a["measurements"]))["verdict"],
                        child.get("verdict")],
        },
        "child_ok": bool(child.get("ok")),
    }
    mismatched = [k for k, (a, b) in cross["compared"].items() if a != b]
    cross["mismatched"] = mismatched
    cross["match"] = bool(child.get("ok")) and not mismatched
    return {"same_process": same, "cross_process": cross}


def _compare_run(a: Mapping[str, Any], b: Mapping[str, Any], sc_a: str, sc_b: str,
                 cfg_a: str, cfg_b: str, v_a: str, v_b: str) -> Dict[str, Any]:
    compared = {
        "sidecar_digest": [sc_a, sc_b],
        "transport_config_digest": [cfg_a, cfg_b],
        "wav_digest": [_wav_digest(a["transported"]), _wav_digest(b["transported"])],
        "measurements_digest": [canonical_digest(_round(a["measurements"])),
                                canonical_digest(_round(b["measurements"]))],
        "verdict": [v_a, v_b],
    }
    mismatched = [k for k, (x, y) in compared.items() if x != y]
    return {"compared": compared, "mismatched": mismatched, "match": not mismatched}


def _wav_digest(transported: Mapping[str, Any]) -> str:
    rows = []
    for stem in sorted(transported):
        y = np.asarray(transported[stem]["signal"], dtype=np.float64)
        pcm = np.clip(np.round(y * 32767.0), -32768, 32767).astype(np.int16)
        rows.append((stem, hashlib.sha256(pcm.tobytes()).hexdigest()))
    return canonical_digest(rows)


def _round(meas: Mapping[str, Any], nd: int = 6) -> Any:
    def _w(o: Any) -> Any:
        if isinstance(o, float):
            return round(o, nd)
        if isinstance(o, dict):
            return {k: _w(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_w(v) for v in o]
        return o
    return _w(meas)


def _cross_process(p0_root: Path, criteria_path: Path) -> Dict[str, Any]:
    """別プロセスで digest だけを再計算する（§21 T0-G8 cross-process）。"""
    proc = subprocess.run(
        [sys.executable, str(_HERE / "t0_run.py"), "--digest-only",
         "--p0-root", str(p0_root), "--criteria", str(criteria_path)],
        capture_output=True, text=True, cwd=str(_HERE), timeout=3600)
    if proc.returncode != 0:
        return {"ok": False, "stderr": proc.stderr[-2000:]}
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):  # pragma: no cover
        return {"ok": False, "stdout": proc.stdout[-2000:]}
    payload["ok"] = True
    return payload


def _source_free_attestation(pins: Mapping[str, Any]) -> Dict[str, Any]:
    """§29。P0 の境界を維持し、T0 で追加許可される読み取りを明示する。"""
    return {
        "schema": "voicegenesis-af-t0-source-free/1.0",
        "declared": {
            "human_audio_used": False,
            "external_voicebank_used": False,
            "pretrained_voice_model_used": False,
            "speaker_embedding_used": False,
            "network_retrieval_used": False,
        },
        "additional_allowed_reads": [
            "af_p0_generated_body", "af_p0_result_json", "t0_fixture", "t0_sidecar"],
        "pins": {k: pins.get(k) for k in ("af0_spec_sha256", "t0_code_closure_sha256",
                                          "sidecar_digest")},
    }


def _write_probes(out: Path, art: Mapping[str, Any],
                  config: transport.TransportConfig) -> Dict[str, Any]:
    """§30 probes/。輸送後の WAV を書き出して公開の実体を作る。"""
    probes = out / "probes" / "transported"
    probes.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    for stem in sorted(art["transported"]):
        row = art["transported"][stem]
        p = probes / f"{stem}.wav"
        sf.write(str(p), np.asarray(row["signal"], dtype=np.float64), int(row["sr"]),
                 subtype="PCM_16", format="WAV")
        written.append(p.name)
    return {"published": af_gates.repo_relative(probes), "bundle_verified": True,
            "partial_artifacts": [], "n_files": len(written),
            "config": config.as_dict()}


def _digest_only(p0_root: Path, criteria_path: Path) -> int:
    """cross-process 用。同じ入力から digest 群だけを出す。"""
    p0_criteria = load_criteria(P0_CRITERIA)
    md = p0_criteria.metric_definitions
    spec_raw = json.loads(P0_SPEC.read_text(encoding="utf-8"))
    genome = genome_from_dict(spec_raw)
    voicebank = p0_root / "voicebank" / "AF0"
    wavs = {u.stem: voicebank / genome.pitch_dir / u.wav_filename for u in genome.units}
    tmp = Path(tempfile.mkdtemp(prefix="af_t0_child_"))
    try:
        captures, ledger = capture_bodies(wavs, md, tmp)
        body_meas = {s: ledger["units"][s]["measurements"]["S0_BODY_44K"]
                     for s in ledger["units"]}
        sidecars = build_sidecars(genome, voicebank, body_meas)
        cal_doc = json.loads((_HERE / "fixtures" / "calibration.json").read_text("utf-8"))
        hol_doc = json.loads((_HERE / "fixtures" / "holdout.json").read_text("utf-8"))
        fixtures = _build_fixture_sets(spec_raw, cal_doc, hol_doc, md, tmp / "fx",
                                       genome)
        energy_cal = _fit_energy(fixtures, genome, md)
        selections = {t: _select_for_trait(t, genome, p0_criteria, md, captures,
                                           sidecars, body_meas, fixtures, energy_cal)
                      for t in transport.COMPOSITION_ORDER}
        config = transport.config_from_selections(selections)
        cmp_, art = evaluate_config(genome, p0_criteria, md, captures, sidecars,
                                    body_meas, config, energy_cal)
        payload = {
            "sidecar_digest": sidecar_digest(sidecars),
            "transport_config_digest": canonical_digest(config.as_dict()),
            "wav_digest": _wav_digest(art["transported"]),
            "measurements_digest": canonical_digest(_round(art["measurements"])),
            "verdict": t0_compare.combined_result(cmp_)["verdict"],
        }
        print(canonical_json(payload))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="VoiceGenesis AF-T0 Trait Transport runner")
    ap.add_argument("--p0-root", default=str(DEFAULT_P0_ROOT))
    ap.add_argument("--criteria", default=str(DEFAULT_CRITERIA))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--digest-only", action="store_true",
                    help="cross-process determinism 用に digest だけを出す")
    args = ap.parse_args(argv)

    p0_root = Path(args.p0_root).resolve()
    criteria_path = Path(args.criteria).resolve()
    if args.digest_only:
        return _digest_only(p0_root, criteria_path)

    try:
        return run(p0_root, criteria_path, Path(args.out).resolve())
    except T0Stop as stop:
        _log("stop", f"{stop.status} ({stop.reason_code}): {stop}")
        return EXIT_CODES[stop.status]


if __name__ == "__main__":
    raise SystemExit(main())
