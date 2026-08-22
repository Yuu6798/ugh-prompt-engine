"""run8/s7_b1_v11.py — B-1 の 1.1 系列（periodicity + energy evidence）。

User 裁定 2026-08-21（Option C）: 1.0 は差し替えず、**新系列**として
`TRF measurement spec / 1.1-rc1` を作る。目的は「F0 が存在するか」ではなく
**意味のある終端有声 vs SP 上に残る低エネルギー NSF excitation** を弁別できる
voiced detector を選ぶこと。

1.0 から**変えないもの**（本モジュールは 1.0 の実装をそのまま呼ぶ）:

- 4 軸の計算式（`s7_b1_calibration.measure_voicing_axes` / `measure_mel_axis`）
- 順位付けキーと丸め（`s7_b1_calibration.rank_key`）
- ε の導出（`max(numerical_floor, reproducibility_bound)`）
- 校正音源（1.0 で事前登録・生成済みの real-render セットを sha pin で継承）

1.1 で**変えるもの**:

- voicing 候補が periodicity 証拠と energy 証拠の**両方**を要求する。energy gate は
  「frame RMS / **同一 render の** voiced-core RMS」という同一レンダ内比なので
  線形 gain に不変（旧候補 B の固定 absolute RMS threshold は復活させない）
- hard requirement に `production_range_resolution` を新設（§事前登録参照）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s7_b1_calibration as b1  # noqa: E402
import s7_io  # noqa: E402
import s7_spec as sp  # noqa: E402

RESULTS_DIR = _HERE.parent / "results_s7"
CANDIDATE_SPACE_PATH = RESULTS_DIR / "s7_b1_candidate_space_1_1.json"
CALIBRATION_SET_PATH = RESULTS_DIR / "s7_b1_calibration_set_1_1.json"
SELECTION_RULE_PATH = RESULTS_DIR / "s7_b1_selection_rule_1_1.json"
SPEC_1_0_PATH = RESULTS_DIR / "trf_measurement_spec.json"

SPEC_VERSION = "1.1-rc1"
SPEC_FREEZE_STATUS = "candidate"
SERIES = "TRF measurement spec / 1.1"
_MS = 1000.0

PRODUCTION_RANGE_REQ = "production_range_resolution"


@dataclass(frozen=True)
class Prereg11:
    candidate_space: Dict[str, Any]
    calibration_set: Dict[str, Any]
    selection_rule: Dict[str, Any]
    pins: Dict[str, str]


def load_prereg_11() -> Prereg11:
    cs, cs_sha, _ = s7_io.read_json_with_pin(CANDIDATE_SPACE_PATH)
    cal, cal_sha, _ = s7_io.read_json_with_pin(CALIBRATION_SET_PATH)
    rule, rule_sha, _ = s7_io.read_json_with_pin(SELECTION_RULE_PATH)
    for doc, path in ((cs, CANDIDATE_SPACE_PATH), (cal, CALIBRATION_SET_PATH), (rule, SELECTION_RULE_PATH)):
        if doc.get("series") != SERIES:
            raise ValueError(f"{path.name}: series {doc.get('series')!r} != {SERIES!r}")
    # 1.0 の校正音源を sha pin で継承していることを確認する（新規生成しない）
    inherited = cal["inherits"]["calibration_set_1_0"]["sha256"]
    got = hashlib.sha256((RESULTS_DIR / "s7_b1_calibration_set.json").read_bytes()).hexdigest()
    if inherited != got:
        raise ValueError(f"継承元の校正セット sha が違う: {got} != {inherited}")
    return Prereg11(cs, cal, rule, {
        CANDIDATE_SPACE_PATH.name: cs_sha,
        CALIBRATION_SET_PATH.name: cal_sha,
        SELECTION_RULE_PATH.name: rule_sha,
    })


# --- 候補 -------------------------------------------------------------------


@dataclass(frozen=True)
class Cand11:
    candidate_id: str
    kind: str                      # "voicing" | "mel"
    family: Optional[str] = None   # A_pyin_prob_relenergy 等
    periodicity_thr: Optional[float] = None
    tau: Optional[float] = None
    window_ms: Optional[float] = None
    hop_ms: Optional[float] = None
    mel: Optional[Tuple[int, int, int]] = None


_FAMILY_THR_KEY = {
    "A_pyin_prob_relenergy": "p_thr",
    "B_autocorr_relenergy": "r_thr",
    "C_harmonicity_relenergy": "h_thr_db",
}


def enumerate_candidates_11(prereg: Prereg11) -> List[Cand11]:
    cs = prereg.candidate_space
    out: List[Cand11] = []
    for spec in cs["voicing"]:
        fam = str(spec["id"])
        thrs = [float(t) for t in spec[_FAMILY_THR_KEY[fam]]]
        for thr in thrs:
            for tau in [float(t) for t in cs["energy_gate_tau"]]:
                for win in [float(w) for w in cs["window_ms"]]:
                    for hop in [float(h) for h in cs["hop_ms"]]:
                        out.append(
                            Cand11(
                                candidate_id=f"{fam}|thr{thr:g}|tau{tau:g}|win{win:g}|hop{hop:g}",
                                kind="voicing", family=fam, periodicity_thr=thr,
                                tau=tau, window_ms=win, hop_ms=hop,
                            )
                        )
    for m in cs["mel"]:
        out.append(
            Cand11(
                candidate_id=str(m["id"]), kind="mel",
                mel=(int(m["n_fft"]), int(m["hop_length"]), int(m["n_mels"])),
            )
        )
    expected = int(cs["n_voicing_candidates"]) + int(cs["n_mel_candidates"])
    if len(out) != expected:
        raise ValueError(f"候補数 {len(out)} != 事前登録 {expected}")
    return out


# --- フレーム解析（候補間で共有・閾値だけ後から当てる） ----------------------


_FRAME_CACHE: Dict[Tuple[str, int, float, float], Dict[str, np.ndarray]] = {}


def _samples_key(y: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(y, dtype=np.float64).tobytes()).hexdigest()


def analyse_frames(y: np.ndarray, sr: int, window_ms: float, hop_ms: float) -> Dict[str, np.ndarray]:
    """1 レンダ × 1 (窓, hop) のフレーム量をまとめて出す（閾値非依存）。

    返す量: `times` / `rms` / `pyin_prob` / `pyin_f0` / `ac_peak` / `hnr_db` / `ac_f0`。
    候補は periodicity 閾値と energy 比 τ を**この結果に当てるだけ**なので、
    96 候補でも重い解析は (族に依らず) (窓, hop) の数しか走らない。
    """
    key = (_samples_key(y), int(sr), float(window_ms), float(hop_ms))
    if key in _FRAME_CACHE:
        return _FRAME_CACHE[key]

    import librosa

    frame_length = int(round(window_ms / _MS * sr))
    hop_length = max(1, int(round(hop_ms / _MS * sr)))
    y64 = np.asarray(y, dtype=np.float64)

    f0, voiced_flag, voiced_prob = librosa.pyin(
        y64, sr=sr, fmin=60.0, fmax=800.0,
        frame_length=frame_length, hop_length=hop_length,
    )
    n = len(f0)
    times = librosa.frames_to_time(np.arange(n), sr=sr, hop_length=hop_length)

    pad = frame_length // 2
    ypad = np.pad(y64, (pad, pad), mode="constant")
    rms = np.zeros(n, dtype=np.float64)
    ac_peak = np.zeros(n, dtype=np.float64)
    hnr_db = np.full(n, -np.inf, dtype=np.float64)
    ac_f0 = np.full(n, np.nan, dtype=np.float64)
    nfft = int(2 ** math.ceil(math.log2(2 * frame_length)))
    lag_min, lag_max = max(1, int(sr / 800.0)), min(frame_length - 1, int(sr / 60.0))
    for i in range(n):
        seg = ypad[i * hop_length: i * hop_length + frame_length]
        if seg.size < frame_length:
            break
        rms[i] = float(np.sqrt(np.mean(seg**2)))
        x = seg - seg.mean()
        spec = np.fft.rfft(x, nfft)
        ac = np.fft.irfft(spec * np.conjugate(spec), nfft)[:frame_length]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        if lag_max <= lag_min:
            continue
        k = int(np.argmax(ac[lag_min:lag_max])) + lag_min
        r = float(ac[k])
        ac_peak[i] = r
        rr = min(max(r, 1e-6), 1 - 1e-6)
        hnr_db[i] = float(10.0 * math.log10(rr / (1.0 - rr)))
        ac_f0[i] = sr / k
    out = {
        "times": times, "rms": rms,
        "pyin_prob": np.nan_to_num(np.asarray(voiced_prob, dtype=np.float64)),
        "pyin_f0": np.asarray(f0, dtype=np.float64),
        "pyin_voiced": np.asarray(voiced_flag, dtype=bool),
        "ac_peak": ac_peak, "hnr_db": hnr_db, "ac_f0": ac_f0,
    }
    _FRAME_CACHE[key] = out
    return out


def voiced_core_rms(frames: Dict[str, np.ndarray], stim: b1.Stimulus) -> float:
    """energy gate の分母。**同一レンダ**のノート核 RMS（1.0 の mel 核窓と同一）。"""
    t = frames["times"]
    lo, hi = stim.note_onset_s + 0.10, stim.commanded_note_end_s - 0.05
    mask = (t >= lo) & (t <= hi)
    if not np.any(mask):
        return 0.0
    return float(np.sqrt(np.mean(frames["rms"][mask] ** 2)))


def voiced_mask_and_f0(
    cand: Cand11, stim: b1.Stimulus
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    frames = analyse_frames(stim.samples, stim.sr, float(cand.window_ms), float(cand.hop_ms))
    core = voiced_core_rms(frames, stim)
    if core <= 0.0:
        # fail-closed: 核が取れないレンダでは energy gate は不成立
        rel = np.zeros_like(frames["rms"])
    else:
        rel = frames["rms"] / core
    energy_ok = rel >= float(cand.tau)
    fam = str(cand.family)
    if fam == "A_pyin_prob_relenergy":
        per_ok = frames["pyin_prob"] >= float(cand.periodicity_thr)
        f0 = frames["pyin_f0"]
    elif fam == "B_autocorr_relenergy":
        per_ok = frames["ac_peak"] >= float(cand.periodicity_thr)
        f0 = frames["ac_f0"]
    elif fam == "C_harmonicity_relenergy":
        per_ok = frames["hnr_db"] >= float(cand.periodicity_thr)
        f0 = frames["ac_f0"]
    else:
        raise ValueError(f"unknown family: {fam!r}")
    voiced = per_ok & energy_ok
    f0_out = np.where(voiced, f0, np.nan)
    return voiced, f0_out, frames["times"]


def measure_candidate_11(cand: Cand11, stim: b1.Stimulus) -> Dict[str, float]:
    """1 候補 × 1 刺激。**軸の計算式は 1.0 の実装をそのまま呼ぶ**。"""
    if cand.kind == "voicing":
        voiced, f0, times = voiced_mask_and_f0(cand, stim)
        return b1.measure_voicing_axes(voiced, f0, times, stim, float(cand.hop_ms))
    n_fft, hop_length, n_mels = cand.mel  # type: ignore[misc]
    return {
        "terminal_mel_persistence": b1.measure_mel_axis(
            stim, {"n_fft": n_fft, "hop_length": hop_length, "n_mels": n_mels}
        )
    }


def _axes_of(cand: Cand11) -> Tuple[str, ...]:
    return tuple(a for a in sp.PRIMARY_AXES if sp.AXIS_CANDIDATE_KIND[a] == cand.kind)


# --- 測定の実行（同一プロセス反復 + 独立プロセス反復） ----------------------


def run_measurements_11(
    prereg: Prereg11,
    stimuli: Dict[str, b1.Stimulus],
    candidates: Sequence[Cand11],
    roles: Dict[str, Any],
    cross_process: bool = True,
    real_render_manifest: Optional[str] = None,
) -> Dict[str, Any]:
    repeat_ids = list(roles["reproducibility"])
    cross_ids = list(roles["cross_process_reproducibility"])
    table: Dict[str, Any] = {}
    for cand in candidates:
        first = {sid: measure_candidate_11(cand, stim) for sid, stim in stimuli.items()}
        repeat = {sid: measure_candidate_11(cand, stimuli[sid]) for sid in repeat_ids}
        cross: Dict[str, Dict[str, float]] = {}
        if cross_process:
            cross = _measure_in_subprocess_11(cand.candidate_id, cross_ids, real_render_manifest)
        table[cand.candidate_id] = {
            "candidate": cand, "first": first, "repeat": repeat, "cross": cross,
        }
    return table


def _measure_in_subprocess_11(
    candidate_id: str, stim_ids: Sequence[str], real_render_manifest: Optional[str]
) -> Dict[str, Dict[str, float]]:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    argv = [sys.executable, str(Path(__file__).resolve()), "--probe", candidate_id, ",".join(stim_ids)]
    if real_render_manifest:
        argv += ["--real-render", str(real_render_manifest)]
    proc = subprocess.run(argv, capture_output=True, text=True, env=env, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"cross-process probe failed ({candidate_id} / {list(stim_ids)}): {proc.stderr[-2000:]}"
        )
    return json.loads(proc.stdout.strip().splitlines()[-1])


# --- 要件評価（従来 6 + production_range_resolution） ------------------------


def _tolerances_11(rule: Dict[str, Any], axis: str, cand: Cand11) -> Dict[str, Any]:
    kind = sp.AXIS_KIND[axis]
    reqs = {r["id"]: r for r in rule["hard_requirements"]}
    pr = reqs[PRODUCTION_RANGE_REQ]["parts"]
    if kind == "ms":
        gain_tol = float(cand.hop_ms) if cand.hop_ms is not None else 0.0
        zero_tol = float(reqs[sp.ZERO_INPUT_REQUIREMENT]["tolerance"]["ms_axes"])
        min_span = float(reqs["monotone_response"]["tolerance"]["ms_axes_min_span"])
        floor_tol = float(pr["floor_non_saturation"]["tolerance"]["ms_axes"])
        min_sep = float(pr["separation_from_floor"]["min_separation"]["ms_axes"])
    else:
        gain_tol = float(reqs["gain_invariance"]["tolerance"]["ratio_axes"])
        zero_tol = float(reqs[sp.ZERO_INPUT_REQUIREMENT]["tolerance"]["ratio_axes"])
        min_span = float(reqs["monotone_response"]["tolerance"]["ratio_axes_min_span"])
        floor_tol = float(pr["floor_non_saturation"]["tolerance"]["ratio_axes"])
        min_sep = float(pr["separation_from_floor"]["min_separation"]["ratio_axes"])
    return {
        "gain_tol": gain_tol, "silence_tol": zero_tol, "min_span": min_span,
        "floor_tol": floor_tol, "min_separation": min_sep,
        "floor_exempt": axis in pr["floor_non_saturation"]["exempt"],
    }


def evaluate_axis_candidate_11(
    axis: str, entry: Dict[str, Any], prereg: Prereg11, roles: Dict[str, Any]
) -> Dict[str, Any]:
    cand: Cand11 = entry["candidate"]
    tol = _tolerances_11(prereg.selection_rule, axis, cand)
    first = {k: v[axis] for k, v in entry["first"].items()}
    repeat = {k: v[axis] for k, v in entry["repeat"].items()}
    cross = {k: v[axis] for k, v in entry["cross"].items()}

    finite = all(math.isfinite(v) for v in first.values())
    repro_err = max((abs(first[k] - repeat[k]) for k in repeat), default=0.0)
    cross_err = max((abs(first[k] - cross[k]) for k in cross), default=0.0)
    repro_bound = max(repro_err, cross_err)

    gain_err = max(abs(first[a] - first[b]) for a, b in roles["gain_invariance"])
    silence_res = abs(first[str(roles[sp.ZERO_INPUT_REQUIREMENT][0])])

    ladder = list(roles["monotone_ladder"]["order"])
    values = [first[s] for s in ladder]
    steps = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    span = values[-1] - values[0]
    monotone_ok = all(s >= -repro_bound for s in steps) and span >= tol["min_span"]

    pr_roles = roles[PRODUCTION_RANGE_REQ]
    floor_v = float(first[str(pr_roles["production_floor"])])
    tail_v = float(first[str(pr_roles["terminal_tail_response"])])
    separation = tail_v - floor_v
    floor_ok = True if tol["floor_exempt"] else abs(floor_v) <= tol["floor_tol"]
    production_ok = floor_ok and separation >= tol["min_separation"]

    checks = {
        "reproducibility": repro_err == 0.0,
        "gain_invariance": gain_err <= tol["gain_tol"],
        sp.ZERO_INPUT_REQUIREMENT: silence_res <= tol["silence_tol"],
        "monotone_response": monotone_ok,
        "cross_process_reproducibility": bool(cross) and cross_err == 0.0,
        "numerical_stability": finite,
        PRODUCTION_RANGE_REQ: production_ok,
    }
    return {
        "candidate_id": cand.candidate_id,
        "checks": checks,
        "survives": all(checks.values()),
        "metrics": {
            "reproducibility_error": repro_err,
            "cross_process_error": cross_err,
            "reproducibility_bound": repro_bound,
            "gain_invariance_error": gain_err,
            "silence_residual": silence_res,
            "monotone_values": values,
            "monotone_min_step": min(steps) if steps else 0.0,
            "monotone_span": span,
            "production_floor_value": floor_v,
            "terminal_tail_value": tail_v,
            "separation_from_floor": separation,
            "floor_non_saturation_ok": floor_ok,
        },
        "tolerances": tol,
        "hop_ms": cand.hop_ms,
        "window_ms": cand.window_ms,
    }


def select_for_axis_11(
    axis: str, table: Dict[str, Any], prereg: Prereg11, roles: Dict[str, Any]
) -> Dict[str, Any]:
    kind = sp.AXIS_CANDIDATE_KIND[axis]
    records = [
        evaluate_axis_candidate_11(axis, e, prereg, roles)
        for e in table.values() if e["candidate"].kind == kind
    ]
    survivors = [r for r in records if r["survives"]]
    if not survivors:
        failing = sorted({c for r in records for c, ok in r["checks"].items() if not ok})
        return {
            "axis": axis, "status": sp.AxisStatus.UNAVAILABLE.value, "selected": None,
            "failing_requirements": failing,
            "candidates": sorted(records, key=lambda r: r["candidate_id"]),
        }
    # 順位付けキーは 1.0 と同一（rank_key をそのまま使う = 変更していない証拠）
    winner = sorted(survivors, key=b1.rank_key)[0]
    return {
        "axis": axis, "status": sp.AxisStatus.FROZEN.value, "selected": winner["candidate_id"],
        "selected_record": winner,
        "candidates": sorted(records, key=lambda r: r["candidate_id"]),
    }


def epsilon_for_axis_11(
    axis: str, selection: Dict[str, Any], prereg: Prereg11, source
) -> Dict[str, Any]:
    """ε = max(numerical_floor, reproducibility_bound)。1.0 と同一の導出。"""
    if selection["status"] != sp.AxisStatus.FROZEN.value:
        return {"axis": axis, "epsilon": None, "reason": "axis_unavailable"}
    rec = selection["selected_record"]
    tail_window_ms = float(source.boundaries["tail_window_ms"])
    sr = int(source.sample_rate_hz)
    if sp.AXIS_KIND[axis] == "ms":
        floor = float(rec["hop_ms"])
        note = "選定候補の hop 長（フレーム格子の量子化幅）"
    elif sp.AXIS_CANDIDATE_KIND[axis] == "voicing":
        floor = 1.0 / (tail_window_ms / float(rec["hop_ms"]))
        note = "終端窓に入るフレーム数の逆数（比の量子化幅）"
    else:
        mel_cfg = next(
            m for m in prereg.candidate_space["mel"] if m["id"] == selection["selected"]
        )
        floor = 1.0 / ((tail_window_ms / _MS) * sr / float(mel_cfg["hop_length"]))
        note = "終端窓に入る mel フレーム数の逆数（比の量子化幅）"
    bound = float(rec["metrics"]["reproducibility_bound"])
    return {
        "axis": axis, "epsilon": max(floor, bound), "numerical_floor": floor,
        "numerical_floor_note": note, "reproducibility_bound": bound,
        "derived_from": "measurement-side only (calibration repeats + frame quantisation)",
    }


# --- spec の組み立てと CLI --------------------------------------------------


def build_spec_11(
    prereg: Prereg11,
    selections: Dict[str, Dict[str, Any]],
    epsilons: Dict[str, Dict[str, Any]],
    table: Dict[str, Any],
    source,
    analysis_stack_observed: Dict[str, str],
) -> Dict[str, Any]:
    all_frozen = all(s["status"] == sp.AxisStatus.FROZEN.value for s in selections.values())
    spec_1_0_sha = hashlib.sha256(SPEC_1_0_PATH.read_bytes()).hexdigest()
    worked, reference = {}, {}
    for axis, sel in selections.items():
        if sel["status"] != sp.AxisStatus.FROZEN.value:
            continue
        cid = sel["selected"]
        first = table[cid]["first"]
        worked[axis] = {
            "candidate_id": cid, "stimulus": source.worked_example_stim,
            "value": first[source.worked_example_stim][axis],
            "why": "終端窓に 80 ms の有声継続を注入した刺激。手計算で追える単調ラダーの中点。",
        }
        reference[axis] = {sid: round(float(v[axis]), 9) for sid, v in sorted(first.items())}
    return {
        "schema": sp.TRF_SPEC_SCHEMA,
        "series": SERIES,
        "spec_version": SPEC_VERSION if not all_frozen else "1.1",
        "freeze_status": SPEC_FREEZE_STATUS if not all_frozen else "frozen",
        "freeze_status_note": (
            "4 軸とも 7 要件（従来 6 + production_range_resolution）を通過した。"
            if all_frozen else
            "7 要件を通過しない軸がある。1.1 は凍結しない（fail-closed。要件は緩めない）。"
        ),
        "authority": "User 裁定 2026-08-21（Option C）/ DESIGN_S7_run8.md 12-0-B",
        "generated_by": "voice_genesis/foundry/run8/s7_b1_v11.py",
        "supersedes_nothing": (
            "1.0 は frozen のまま保存する。1.1 が freeze されるまで唯一の凍結仕様は 1.0 である"
        ),
        "spec_1_0": {"path": str(SPEC_1_0_PATH.name), "sha256": spec_1_0_sha,
                     "production_applicability": "NOT_ESTABLISHED / DEGENERATE"},
        "calibration_source": {
            "name": source.name, "manifest": source.manifest_path,
            "roles": prereg.calibration_set["roles"],
            "stimulus_ids": sorted(source.stimuli),
            "inherited_from_1_0": True,
        },
        "prereg_pins": dict(prereg.pins) | dict(source.extra_pins),
        "analysis_stack": {
            "declared": {k: v for k, v in prereg.candidate_space["analysis_stack_pin"].items() if k != "note"},
            "observed": analysis_stack_observed, "verified": True,
        },
        "sample_rate_hz": int(source.sample_rate_hz),
        "boundaries": source.boundaries,
        "measured_on": "raw pre-normalisation waveform (DESIGN_S7_run8.md 5-2)",
        "axes": {
            axis: {
                "status": sel["status"],
                "selected_candidate": sel["selected"],
                "unit": b1.AXIS_FORMULAS[axis]["unit"],
                "formula": b1.AXIS_FORMULAS[axis]["formula"],
                "note": b1.AXIS_FORMULAS[axis]["note"],
                "voicing_rule": (
                    "periodicity evidence AND energy evidence（energy = frame RMS / "
                    "同一 render の voiced-core RMS）"
                ) if sp.AXIS_CANDIDATE_KIND[axis] == "voicing" else None,
                "epsilon": epsilons[axis]["epsilon"],
                "epsilon_derivation": {k: v for k, v in epsilons[axis].items() if k not in ("axis", "epsilon")},
                "hard_requirement_checks": (
                    sel["selected_record"]["checks"] if sel["status"] == sp.AxisStatus.FROZEN.value else None
                ),
                "selection_metrics": (
                    sel["selected_record"]["metrics"] if sel["status"] == sp.AxisStatus.FROZEN.value else None
                ),
                "failing_requirements": sel.get("failing_requirements"),
                "candidate_survival": {r["candidate_id"]: r["survives"] for r in sel["candidates"]},
                "candidate_records": {
                    r["candidate_id"]: {"checks": r["checks"], "metrics": r["metrics"]}
                    for r in sel["candidates"]
                },
                "rank_order": [
                    r["candidate_id"]
                    for r in sorted([x for x in sel["candidates"] if x["survives"]], key=b1.rank_key)
                ],
            }
            for axis, sel in selections.items()
        },
        "worked_example": worked,
        "reference_output": reference,
        "prohibitions": {
            "no_label_input": "B-1 は聴取ラベル・本番 360 セルへの入力口を持たない。",
            "no_auc": "順位付けキーに分離性能は 1 つも入っていない（rank_key は 1.0 と同一実装）。",
            "no_threshold_fitting": "現 360 セルに合わせた閾値の後付けをしていない。",
            "no_epsilon_shrink": "ε の導出は 1.0 と同一式で、縮めていない。",
        },
        "auxiliary_axes_not_in_gate": list(sp.AUXILIARY_AXES_NOT_IN_GATE),
    }


def run_b1_11(cross_process: bool = True, real_render_manifest: Optional[Path] = None) -> Dict[str, Any]:
    prereg11 = load_prereg_11()
    prereg10 = b1.load_prereg()
    observed = b1.verify_analysis_stack(prereg10)
    if real_render_manifest is None:
        raise ValueError("1.1 は実レンダ校正でのみ回す（--real-render が要る）")
    source = b1.real_render_source(prereg10, Path(real_render_manifest))
    roles = prereg11.calibration_set["roles"]
    candidates = enumerate_candidates_11(prereg11)
    table = run_measurements_11(
        prereg11, source.stimuli, candidates, roles,
        cross_process=cross_process, real_render_manifest=str(real_render_manifest),
    )
    selections = {a: select_for_axis_11(a, table, prereg11, roles) for a in sp.PRIMARY_AXES}
    epsilons = {a: epsilon_for_axis_11(a, selections[a], prereg11, source) for a in sp.PRIMARY_AXES}
    return build_spec_11(prereg11, selections, epsilons, table, source, observed)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="B-1 1.1 calibration harness (User 裁定 2026-08-21 Option C)")
    ap.add_argument("--out", type=Path, default=RESULTS_DIR / "trf_measurement_spec_1_1.json")
    ap.add_argument("--real-render", type=Path, required=False)
    ap.add_argument("--no-cross-process", action="store_true")
    ap.add_argument("--probe", nargs=2, metavar=("CANDIDATE_ID", "STIMULUS_IDS"))
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.probe:
        prereg11 = load_prereg_11()
        prereg10 = b1.load_prereg()
        b1.verify_analysis_stack(prereg10)
        source = b1.real_render_source(prereg10, Path(args.real_render))
        cand = next(c for c in enumerate_candidates_11(prereg11) if c.candidate_id == args.probe[0])
        payload = {
            sid: measure_candidate_11(cand, source.stimuli[sid]) for sid in args.probe[1].split(",")
        }
        print(json.dumps(payload, sort_keys=True))
        return 0

    s7_io.reject_output_collision(
        [args.out],
        [CANDIDATE_SPACE_PATH, CALIBRATION_SET_PATH, SELECTION_RULE_PATH, SPEC_1_0_PATH,
         *( [Path(args.real_render)] if args.real_render else [] )],
    )
    spec = run_b1_11(cross_process=not args.no_cross_process, real_render_manifest=args.real_render)
    s7_io.assert_json_finite(spec)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    frozen = [a for a, v in spec["axes"].items() if v["status"] == sp.AxisStatus.FROZEN.value]
    print(f"wrote {args.out} ({len(frozen)}/{len(sp.PRIMARY_AXES)} axes frozen) "
          f"version={spec['spec_version']} status={spec['freeze_status']}")
    for axis, v in spec["axes"].items():
        print(f"  {axis}: {v['status']} {v['selected_candidate']} eps={v['epsilon']}")
        if v["failing_requirements"]:
            print(f"      failing: {v['failing_requirements']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
