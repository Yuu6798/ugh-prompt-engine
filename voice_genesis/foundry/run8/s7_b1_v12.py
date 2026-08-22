"""run8/s7_b1_v12.py — B-1 の 1.2 系列（スペクトル形状での分離 + 本番振幅帯の校正）。

User 裁定 2026-08-22:「次系列は (A)+(B) 併用。分離軸を相対エネルギー 1 次元から変える。
同時に本番 p05–p95 を張る calibration set に作り直す。**新軸は事前に固定してから測定する。
結果を見て軸を追加しない**」「停止条件は 1.2 で打ち切る」。

1.0 / 1.1 から**変えないもの**（実装をそのまま呼ぶ）:
軸の計算式 / 順位付けキー `rank_key` / ε の導出 / mel 候補 3 種。

1.2 で**変えるもの**: voicing 候補の分離軸（形状距離・帯域尖り）と、
`production_range_resolution` の参照刺激（`rr_amp_p50` = 本番中央値相当）。

事前登録の解釈について 1 点（記録に残す）: 候補空間は
「選定された mel 候補と同じ n_fft / hop_length / n_mels を使う（新しい格子を導入しない）」
と書いているが、voicing 候補は自身の窓/hop を持つため、**候補自身の格子**
（`n_fft = 窓長`, `hop_length = hop`, `n_mels = 80`）で形状を計算する。
`n_mels = 80` は pin 済み mel 候補 3 種のうち 2 種が使い、1.0 / 1.1 の勝者 M2 が使う値。
この読み方だけが pin 済みの `n_voicing_candidates = 32` と両立する（別解釈だと 96 になる）。
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
import s7_b1_v11 as v11  # noqa: E402
import s7_io  # noqa: E402
import s7_spec as sp  # noqa: E402

RESULTS_DIR = _HERE.parent / "results_s7"
CANDIDATE_SPACE_PATH = RESULTS_DIR / "s7_b1_candidate_space_1_2.json"
CALIBRATION_SET_PATH = RESULTS_DIR / "s7_b1_calibration_set_1_2.json"
SELECTION_RULE_PATH = RESULTS_DIR / "s7_b1_selection_rule_1_2.json"

SERIES = "TRF measurement spec / 1.2"
SPEC_VERSION_RC = "1.2-rc1"
PYIN_PROB_GATE = 0.5
SHAPE_N_MELS = 80
_MS = 1000.0


@dataclass(frozen=True)
class Cand12:
    candidate_id: str
    kind: str                       # "voicing" | "mel"
    family: Optional[str] = None
    threshold: Optional[float] = None
    window_ms: Optional[float] = None
    hop_ms: Optional[float] = None
    mel: Optional[Tuple[int, int, int]] = None


def load_prereg_12():
    cs, cs_sha, _ = s7_io.read_json_with_pin(CANDIDATE_SPACE_PATH)
    cal, cal_sha, _ = s7_io.read_json_with_pin(CALIBRATION_SET_PATH)
    rule, rule_sha, _ = s7_io.read_json_with_pin(SELECTION_RULE_PATH)
    for doc, path in ((cs, CANDIDATE_SPACE_PATH), (cal, CALIBRATION_SET_PATH), (rule, SELECTION_RULE_PATH)):
        if doc.get("series") != SERIES:
            raise ValueError(f"{path.name}: series {doc.get('series')!r} != {SERIES!r}")
    pins = {CANDIDATE_SPACE_PATH.name: cs_sha, CALIBRATION_SET_PATH.name: cal_sha,
            SELECTION_RULE_PATH.name: rule_sha}
    return cs, cal, rule, pins


_THR_KEY = {"S_melshape_core_distance": "d_thr", "P_mel_peakiness": "p_thr"}


def enumerate_candidates_12(cs: Dict[str, Any]) -> List[Cand12]:
    out: List[Cand12] = []
    for spec in cs["voicing"]:
        fam = str(spec["id"])
        for thr in [float(t) for t in spec[_THR_KEY[fam]]]:
            for win in [float(w) for w in cs["window_ms"]]:
                for hop in [float(h) for h in cs["hop_ms"]]:
                    out.append(Cand12(
                        candidate_id=f"{fam}|thr{thr:g}|win{win:g}|hop{hop:g}",
                        kind="voicing", family=fam, threshold=thr, window_ms=win, hop_ms=hop))
    for m in cs["mel"]:
        out.append(Cand12(candidate_id=str(m["id"]), kind="mel",
                          mel=(int(m["n_fft"]), int(m["hop_length"]), int(m["n_mels"]))))
    expected = int(cs["n_voicing_candidates"]) + int(cs["n_mel_candidates"])
    if len(out) != expected:
        raise ValueError(f"候補数 {len(out)} != 事前登録 {expected}")
    return out


# --- 形状フレームの解析（閾値非依存・(窓, hop) ごとにキャッシュ） --------------

_SHAPE_CACHE: Dict[Tuple[str, int, float, float], Dict[str, np.ndarray]] = {}


def analyse_shape(y: np.ndarray, sr: int, window_ms: float, hop_ms: float) -> Dict[str, np.ndarray]:
    key = (hashlib.sha256(np.ascontiguousarray(y, dtype=np.float64).tobytes()).hexdigest(),
           int(sr), float(window_ms), float(hop_ms))
    if key in _SHAPE_CACHE:
        return _SHAPE_CACHE[key]
    import librosa

    frames = v11.analyse_frames(y, sr, window_ms, hop_ms)   # pyin_prob / times / rms を共用
    n_fft = int(round(window_ms / _MS * sr))
    hop_length = max(1, int(round(hop_ms / _MS * sr)))
    S = librosa.feature.melspectrogram(
        y=np.asarray(y, dtype=np.float64), sr=sr, n_fft=n_fft, hop_length=hop_length,
        n_mels=SHAPE_N_MELS, power=2.0, htk=False, fmin=0.0, fmax=sr / 2.0,
    )
    V = np.asarray(S, dtype=np.float64).T                     # [T, n_mels]
    n = min(V.shape[0], len(frames["times"]))
    V = V[:n]
    energy = np.linalg.norm(V, axis=1)
    shape = np.zeros_like(V)
    nz = energy > 0
    shape[nz] = V[nz] / energy[nz, None]                      # L2 正規化 = gain 不変
    med = np.median(V, axis=1)
    peak = np.max(V, axis=1)
    peakiness = np.where(med > 0, peak / np.maximum(med, 1e-300), 0.0)
    out = {
        "times": frames["times"][:n], "pyin_prob": frames["pyin_prob"][:n],
        "pyin_f0": frames["pyin_f0"][:n], "shape": shape, "energy": energy,
        "peakiness": peakiness,
    }
    _SHAPE_CACHE[key] = out
    return out


def core_shape(fr: Dict[str, np.ndarray], stim: b1.Stimulus) -> Optional[np.ndarray]:
    t = fr["times"]
    m = (t >= stim.note_onset_s + 0.10) & (t <= stim.commanded_note_end_s - 0.05) & (fr["energy"] > 0)
    if not np.any(m):
        return None
    v = fr["shape"][m].mean(axis=0)
    nrm = float(np.linalg.norm(v))
    return None if nrm <= 0 else v / nrm


def voiced_mask_12(cand: Cand12, stim: b1.Stimulus) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    fr = analyse_shape(stim.samples, stim.sr, float(cand.window_ms), float(cand.hop_ms))
    per_ok = fr["pyin_prob"] >= PYIN_PROB_GATE
    defined = fr["energy"] > 0                                # 形状未定義 -> fail-closed
    if cand.family == "S_melshape_core_distance":
        ref = core_shape(fr, stim)
        if ref is None:
            crit = np.zeros_like(per_ok)
        else:
            cos = fr["shape"] @ ref
            crit = (1.0 - cos) <= float(cand.threshold)
    elif cand.family == "P_mel_peakiness":
        crit = fr["peakiness"] >= float(cand.threshold)
    else:
        raise ValueError(f"unknown family {cand.family!r}")
    voiced = per_ok & defined & crit
    f0 = np.where(voiced, fr["pyin_f0"], np.nan)
    return voiced, f0, fr["times"]


def measure_candidate_12(cand: Cand12, stim: b1.Stimulus) -> Dict[str, float]:
    if cand.kind == "voicing":
        voiced, f0, times = voiced_mask_12(cand, stim)
        return b1.measure_voicing_axes(voiced, f0, times, stim, float(cand.hop_ms))
    n_fft, hop_length, n_mels = cand.mel  # type: ignore[misc]
    return {"terminal_mel_persistence": b1.measure_mel_axis(
        stim, {"n_fft": n_fft, "hop_length": hop_length, "n_mels": n_mels})}


# --- 校正音源（1.2 manifest。振幅 rung を含む） ------------------------------


class Manifest12Error(ValueError):
    """1.2 manifest が事前登録と結びついていない（fail-closed）。"""


def _expected_condition_ids_12(cal: Dict[str, Any], cal_1_0: Dict[str, Any]) -> List[str]:
    """1.2 で測るべき条件 ID の**厳密集合**を事前登録から組み立てる。

    = 1.0 `real_render_set` の条件 + 派生 + zero buffer + 1.2 の振幅 rung。
    """
    rrs = cal_1_0["real_render_set"]
    ids = [str(c["id"]) for c in rrs["conditions"]]
    ids += [str(d["id"]) for d in rrs.get("derived", [])]
    ids += [str(z["id"]) for z in rrs.get("zero_buffers", [])]
    ids += [str(r["id"]) for r in cal["amplitude_ladder"]["rungs"]]
    return ids


def source_12(manifest_path: Path, cal: Dict[str, Any]) -> Any:
    """1.2 manifest を読み、刺激辞書と境界を作る（1.0 の照合規律を踏襲）。

    照合は 1.0 `real_render_source` と**同じ 4 段**にする（PR #303 レビュー指摘:
    ここだけ「rung ID が含まれていれば良い」という部分集合検査で、schema・
    事前登録 sha・条件集合の厳密一致・重複 ID を見ていなかった。手編集や
    古い manifest でも通ってしまい、**別の条件で測った値に現行の事前登録 pin を
    貼った成果物**が出る）。"""
    man, man_sha, _ = s7_io.read_json_with_pin(manifest_path)

    # (1) schema
    if str(man.get("schema")) != sp.REAL_RENDER_MANIFEST_SCHEMA_1_2:
        raise Manifest12Error(f"schema {man.get('schema')!r} != {sp.REAL_RENDER_MANIFEST_SCHEMA_1_2!r}")

    # (2) この manifest が生成時に見た事前登録 == いま読んでいる事前登録
    cal_1_0, cal_1_0_sha, _ = s7_io.read_json_with_pin(b1.CALIBRATION_SET_PATH)
    _, cal_1_2_sha, _ = s7_io.read_json_with_pin(CALIBRATION_SET_PATH)
    if str(man.get("calibration_set_1_2", {}).get("sha256")) != cal_1_2_sha:
        raise Manifest12Error(
            f"manifest がレンダ時に見た 1.2 事前登録 "
            f"{man.get('calibration_set_1_2', {}).get('sha256')} と現在の {cal_1_2_sha} が違う"
        )
    inherit = cal["inherits"]
    if cal_1_0_sha != str(inherit["calibration_set_1_0"]["sha256"]):
        raise Manifest12Error(
            f"1.0 事前登録 {cal_1_0_sha} が 1.2 の inherits pin と違う"
        )
    if str(man.get("extends", {}).get("sha256")) != str(inherit["real_render_manifest"]["sha256"]):
        raise Manifest12Error(
            f"manifest の親 {man.get('extends', {}).get('sha256')} が "
            f"1.2 の inherits pin {inherit['real_render_manifest']['sha256']} と違う"
        )

    # (3) 条件集合の**厳密一致**（部分集合ではない）+ 重複 ID の拒否
    got_ids = [str(c["condition_id"]) for c in man["conditions"]]
    if len(set(got_ids)) != len(got_ids):
        dupes = sorted({i for i in got_ids if got_ids.count(i) > 1})
        raise Manifest12Error(f"manifest に重複した condition_id がある: {dupes}")
    expected_ids = _expected_condition_ids_12(cal, cal_1_0)
    if sorted(got_ids) != sorted(expected_ids):
        raise Manifest12Error(
            f"条件集合が事前登録と違う: {sorted(got_ids)} != {sorted(expected_ids)}"
        )

    # (4) 各 WAV の pin（容器 sha + 標本 sha）は s7_io で照合する
    out_dir = Path(str(man["out_dir"]))
    zero_ids = {str(z["id"]) for z in cal_1_0["real_render_set"].get("zero_buffers", [])}
    stimuli: Dict[str, b1.Stimulus] = {}
    for e in man["conditions"]:
        cid = str(e["condition_id"])
        try:
            y, sr = s7_io.read_wav_with_pins(
                s7_io.child_path(out_dir, e["wav"]),
                e.get("wav_sha256"), e.get("samples_sha256"),
            )
        except s7_io.WavPinMismatch as exc:
            raise Manifest12Error(f"{cid}: {exc}") from exc
        if cid in zero_ids and np.any(np.asarray(y) != 0.0):
            raise Manifest12Error(f"{cid}: zero buffer に非ゼロ標本がある")
        stimuli[cid] = b1.Stimulus(
            stim_id=cid, family=str(e.get("maps_to", "")), samples=np.asarray(y, dtype=np.float64),
            sr=int(sr), note_onset_s=float(e["note_onset_s"]),
            commanded_note_end_s=float(e["commanded_note_end_s"]),
            score_boundary_s=float(e["score_boundary_s"]),
            tail_window_ms=float(e["tail_window_ms"]))
    ref = man["conditions"][0]
    return {
        "stimuli": stimuli, "manifest_sha256": man_sha, "manifest_path": str(manifest_path),
        "sample_rate_hz": int(next(iter(stimuli.values())).sr),
        "boundaries": {k: float(ref[k]) for k in
                       ("note_onset_s", "commanded_note_end_s", "score_boundary_s", "tail_window_ms")},
        "model_sha256": man["render_path"]["model_sha256"],
    }


# --- 要件評価（7 件。1.1 と同じ形で参照刺激だけが違う） ----------------------


def _tol_12(rule: Dict[str, Any], axis: str, cand: Cand12) -> Dict[str, Any]:
    kind = sp.AXIS_KIND[axis]
    reqs = {r["id"]: r for r in rule["hard_requirements"]}
    pr = reqs[v11.PRODUCTION_RANGE_REQ]["parts"]
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
    return {"gain_tol": gain_tol, "silence_tol": zero_tol, "min_span": min_span,
            "floor_tol": floor_tol, "min_separation": min_sep,
            "floor_exempt": axis in pr["floor_non_saturation"]["exempt"]}


def evaluate_12(axis: str, entry: Dict[str, Any], rule: Dict[str, Any],
                roles: Dict[str, Any]) -> Dict[str, Any]:
    cand: Cand12 = entry["candidate"]
    tol = _tol_12(rule, axis, cand)
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
    pr_roles = roles[v11.PRODUCTION_RANGE_REQ]
    floor_v = float(first[str(pr_roles["production_floor"])])
    tail_key = str(pr_roles["terminal_tail_response"]).split("（")[0].strip()
    tail_v = float(first[tail_key])
    separation = tail_v - floor_v
    floor_ok = True if tol["floor_exempt"] else abs(floor_v) <= tol["floor_tol"]
    checks = {
        "reproducibility": repro_err == 0.0,
        "gain_invariance": gain_err <= tol["gain_tol"],
        sp.ZERO_INPUT_REQUIREMENT: silence_res <= tol["silence_tol"],
        "monotone_response": monotone_ok,
        "cross_process_reproducibility": bool(cross) and cross_err == 0.0,
        "numerical_stability": finite,
        v11.PRODUCTION_RANGE_REQ: floor_ok and separation >= tol["min_separation"],
    }
    return {
        "candidate_id": cand.candidate_id, "checks": checks, "survives": all(checks.values()),
        "metrics": {
            "reproducibility_error": repro_err, "cross_process_error": cross_err,
            "reproducibility_bound": repro_bound, "gain_invariance_error": gain_err,
            "silence_residual": silence_res, "monotone_values": values,
            "monotone_min_step": min(steps) if steps else 0.0, "monotone_span": span,
            "production_floor_value": floor_v, "terminal_tail_value": tail_v,
            "separation_from_floor": separation, "floor_non_saturation_ok": floor_ok,
            "amplitude_ladder_values": {
                r: float(first[r]) for r in sorted(first) if r.startswith("rr_amp_")},
        },
        "tolerances": tol, "hop_ms": cand.hop_ms, "window_ms": cand.window_ms,
    }


def _probe_subprocess(candidate_id: str, stim_ids: Sequence[str], manifest: str) -> Dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "0"
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--probe", candidate_id,
         ",".join(stim_ids), "--manifest", manifest],
        capture_output=True, text=True, env=env, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"cross-process probe failed ({candidate_id}): {proc.stderr[-1500:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def run_b1_12(manifest: Path, cross_process: bool = True) -> Dict[str, Any]:
    cs, cal, rule, pins = load_prereg_12()
    b1.verify_analysis_stack(b1.load_prereg())
    src = source_12(manifest, cal)
    roles = cal["roles"]
    cands = enumerate_candidates_12(cs)
    table: Dict[str, Any] = {}
    for c in cands:
        first = {sid: measure_candidate_12(c, st) for sid, st in src["stimuli"].items()}
        repeat = {sid: measure_candidate_12(c, src["stimuli"][sid]) for sid in roles["reproducibility"]}
        cross = _probe_subprocess(c.candidate_id, roles["cross_process_reproducibility"],
                                  str(manifest)) if cross_process else {}
        table[c.candidate_id] = {"candidate": c, "first": first, "repeat": repeat, "cross": cross}

    selections, epsilons = {}, {}
    for axis in sp.PRIMARY_AXES:
        kind = sp.AXIS_CANDIDATE_KIND[axis]
        recs = [evaluate_12(axis, e, rule, roles) for e in table.values() if e["candidate"].kind == kind]
        surv = [r for r in recs if r["survives"]]
        if surv:
            w = sorted(surv, key=b1.rank_key)[0]
            sel = {"axis": axis, "status": sp.AxisStatus.FROZEN.value, "selected": w["candidate_id"],
                   "selected_record": w, "candidates": sorted(recs, key=lambda r: r["candidate_id"])}
            rec = w
            if sp.AXIS_KIND[axis] == "ms":
                floor = float(rec["hop_ms"])
                note = "選定候補の hop 長"
            elif kind == "voicing":
                floor = 1.0 / (float(src["boundaries"]["tail_window_ms"]) / float(rec["hop_ms"]))
                note = "終端窓に入るフレーム数の逆数"
            else:
                mc = next(m for m in cs["mel"] if m["id"] == w["candidate_id"])
                floor = 1.0 / ((float(src["boundaries"]["tail_window_ms"]) / _MS)
                               * src["sample_rate_hz"] / float(mc["hop_length"]))
                note = "終端窓に入る mel フレーム数の逆数"
            bound = float(rec["metrics"]["reproducibility_bound"])
            epsilons[axis] = {"axis": axis, "epsilon": max(floor, bound), "numerical_floor": floor,
                              "numerical_floor_note": note, "reproducibility_bound": bound,
                              "derived_from": "measurement-side only"}
        else:
            failing = sorted({c for r in recs for c, ok in r["checks"].items() if not ok})
            sel = {"axis": axis, "status": sp.AxisStatus.UNAVAILABLE.value, "selected": None,
                   "failing_requirements": failing,
                   "candidates": sorted(recs, key=lambda r: r["candidate_id"])}
            epsilons[axis] = {"axis": axis, "epsilon": None, "reason": "axis_unavailable"}
        selections[axis] = sel

    all_frozen = all(s["status"] == sp.AxisStatus.FROZEN.value for s in selections.values())
    return {
        "schema": sp.TRF_SPEC_SCHEMA, "series": SERIES,
        "spec_version": "1.2" if all_frozen else SPEC_VERSION_RC,
        "freeze_status": "frozen" if all_frozen else "blocked",
        "freeze_status_note": (
            "4 軸とも 7 要件を通過した（参照刺激は本番中央値相当 rr_amp_p50）。"
            if all_frozen else
            "7 要件を通過しない軸がある。**停止条件により 1.3 は作らない**。"
            "Gate 1 = UNDETERMINED で Run 8 を閉じ、別の観測設計として再事前登録する"),
        "authority": "User 裁定 2026-08-22（(A)+(B) 併用 / 1.2 で打ち切り）",
        "generated_by": "voice_genesis/foundry/run8/s7_b1_v12.py",
        "stop_condition": rule["stop_condition"],
        "prereg_pins": dict(pins) | {"s7_b1_real_render_manifest_1_2.json": src["manifest_sha256"]},
        "calibration_source": {"name": "real_render_v1_2", "manifest": src["manifest_path"],
                               "roles": roles, "stimulus_ids": sorted(src["stimuli"])},
        "sample_rate_hz": src["sample_rate_hz"], "boundaries": src["boundaries"],
        "measured_on": "raw pre-normalisation waveform (DESIGN_S7_run8.md 5-2)",
        "axes": {
            axis: {
                "status": sel["status"], "selected_candidate": sel["selected"],
                "unit": b1.AXIS_FORMULAS[axis]["unit"], "formula": b1.AXIS_FORMULAS[axis]["formula"],
                "epsilon": epsilons[axis]["epsilon"],
                "epsilon_derivation": {k: v for k, v in epsilons[axis].items() if k not in ("axis", "epsilon")},
                "hard_requirement_checks": (sel["selected_record"]["checks"]
                                            if sel["status"] == sp.AxisStatus.FROZEN.value else None),
                "selection_metrics": (sel["selected_record"]["metrics"]
                                      if sel["status"] == sp.AxisStatus.FROZEN.value else None),
                "failing_requirements": sel.get("failing_requirements"),
                "candidate_survival": {r["candidate_id"]: r["survives"] for r in sel["candidates"]},
                "candidate_records": {r["candidate_id"]: {"checks": r["checks"], "metrics": r["metrics"]}
                                      for r in sel["candidates"]},
                "rank_order": [r["candidate_id"] for r in
                               sorted([x for x in sel["candidates"] if x["survives"]], key=b1.rank_key)],
            } for axis, sel in selections.items()},
        "reference_output": {
            axis: {sid: round(float(v[axis]), 9)
                   for sid, v in sorted(table[selections[axis]["selected"]]["first"].items())}
            for axis in sp.PRIMARY_AXES if selections[axis]["status"] == sp.AxisStatus.FROZEN.value},
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="B-1 1.2 (User 裁定 2026-08-22)")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", type=Path, default=RESULTS_DIR / "trf_measurement_spec_1_2.json")
    ap.add_argument("--no-cross-process", action="store_true")
    ap.add_argument("--probe", nargs=2, metavar=("CANDIDATE_ID", "STIMULUS_IDS"))
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.probe:
        cs, cal, _rule, _pins = load_prereg_12()
        b1.verify_analysis_stack(b1.load_prereg())
        src = source_12(Path(args.manifest), cal)
        cand = next(c for c in enumerate_candidates_12(cs) if c.candidate_id == args.probe[0])
        print(json.dumps({sid: measure_candidate_12(cand, src["stimuli"][sid])
                          for sid in args.probe[1].split(",")}, sort_keys=True))
        return 0

    s7_io.reject_output_collision([args.out], [CANDIDATE_SPACE_PATH, CALIBRATION_SET_PATH,
                                               SELECTION_RULE_PATH, Path(args.manifest)])
    spec = run_b1_12(Path(args.manifest), cross_process=not args.no_cross_process)
    s7_io.assert_json_finite(spec)
    args.out.write_text(json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    frozen = [a for a, v in spec["axes"].items() if v["status"] == sp.AxisStatus.FROZEN.value]
    print(f"wrote {args.out} ({len(frozen)}/4 axes frozen) version={spec['spec_version']} "
          f"status={spec['freeze_status']}")
    for axis, v in spec["axes"].items():
        print(f"  {axis}: {v['status']} {v['selected_candidate']} eps={v['epsilon']}")
        if v["failing_requirements"]:
            print(f"      failing: {v['failing_requirements']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
