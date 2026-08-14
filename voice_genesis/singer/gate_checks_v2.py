"""singer/gate_checks_v2.py — S4 W1: gate6 の score-informed QC 再設計。

`gate_checks.py`（凍結・無改変。gate1-5 とその閾値定数を import 流用）は
一切変更しない。本モジュールは gate6 のみを **score-informed 計測**
（memo 原理: gate6 は「レンダラが Genome の命令に従っているか」の製造検査
であり、ブラインド分析ではない。検査者は命令値=楽譜 F0・commanded vibrato
を知ってよい——ブラインド推定の性能保証は別トラック(VT-2)の責務）に
差し替えた `gate6_grip_quick_check_v2` と、gate1-5(無改変) + gate6-v2 を
束ねた `full_gates_v2` を提供する。

score-informed 化の内容（memo W1）:
  1. periodicity: フレーム毎ブラインド F0 推定ではなく、**commanded F0
     （ビブラート込みの命令 F0 軌跡。render_sustained_vowel と同一の生成式
     をこのファイル内で複製——render_song.py 無改変を保つため）由来のラグ
     で正規化自己相関を評価する。ブラインド F0 誤りが periodicity 計測へ
     混入する経路を断つ
  2. vibrato_depth: ブラインドフレーム F0（`measure_v3.raw_frame_f0_track`、
     ノート単位オクターブ補正は使わない=完全に score-informed な棄却に
     委ねる）を commanded F0 の ±200 cent 窓で棄却してから、採用フレームの
     median 中心 MAD で深度を推定する（旧: 自己参照 ±600cent 棄却 →
     新: 既知の命令値を参照する棄却。棄却率は必ず記録する）

**provenance**: 本モジュールが計算する periodicity / vibrato_depth の値は
必ず `PROVENANCE = "measured (score-informed QC)"` を伴って報告する。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import numpy as np

_HERE = Path(__file__).resolve().parent
_VT_HARNESS_DIR = _HERE.parent / "harness"
if str(_VT_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_VT_HARNESS_DIR))

import measure as m  # noqa: E402  (harness、無改変・import 流用のみ)
import measure_v2 as m2  # noqa: E402  (harness、無改変・import 流用のみ)
import measure_v3 as m3  # noqa: E402  (harness、無改変・import 流用のみ)

import render_song as rs  # noqa: E402
import gate_checks as gc  # noqa: E402  (無改変。gate1-5・閾値定数を import 流用)

PROVENANCE = "measured (score-informed QC)"
VIBRATO_WINDOW_CENTS = 200.0

FMIN, FMAX = gc.FMIN, gc.FMAX

# ---------------------------------------------------------------------------
# commanded F0 軌跡（render_sustained_vowel の生成式を複製。render_song.py
# は無改変で保つ——jitter は sub-cent オーダーで ±200cent窓に対し無視できる
# ため式には含めない）
# ---------------------------------------------------------------------------


def commanded_f0_track(midi: float, genome, n_samples: int, sr: int) -> np.ndarray:
    f0_hz = 440.0 * 2.0 ** ((midi - 69.0) / 12.0)
    t = np.arange(n_samples) / sr
    vibrato_cents = genome.microprosody.vibrato_depth_cents * np.sin(2 * np.pi * genome.microprosody.vibrato_rate_hz * t)
    return f0_hz * (2.0 ** (vibrato_cents / 1200.0))


def _frame_centers(sig: np.ndarray, sr: int) -> List[int]:
    """`measure._analysis_window`（start_frac=0.3）+ `measure_v3._iter_frames`
    と同一のフレーム切り出し規約で、各フレーム中心の元 sig 上インデックスを返す。
    """
    n_total = len(sig)
    offset = int(n_total * 0.3)
    analysis = m._analysis_window(sig, sr)
    frame_len = int(m3._FRAME_LEN_SEC * sr)
    hop = max(int(m3._HOP_SEC * sr), 1)
    n = len(analysis)
    if n < frame_len:
        frame_len = n
    centers = []
    for start in range(0, max(n - frame_len, 1), hop):
        if start + frame_len > n:
            break
        centers.append(offset + start + frame_len // 2)
    return centers


# ---------------------------------------------------------------------------
# periodicity（score-informed）
# ---------------------------------------------------------------------------


def periodicity_track_scored(sig: np.ndarray, sr: int, commanded_f0: np.ndarray) -> m3.PeriodicityTrackV3:
    n_total = len(sig)
    offset = int(n_total * 0.3)
    analysis = m._analysis_window(sig, sr)
    frame_len = int(m3._FRAME_LEN_SEC * sr)
    hop = max(int(m3._HOP_SEC * sr), 1)
    n = len(analysis)
    if n < frame_len:
        frame_len = n

    r_vals, db_vals = [], []
    n_voiced = 0
    for start in range(0, max(n - frame_len, 1), hop):
        frame = analysis[start:start + frame_len]
        if len(frame) < frame_len:
            break
        center = offset + start + frame_len // 2
        f0_cmd = commanded_f0[center] if 0 <= center < len(commanded_f0) else float("nan")
        if np.isfinite(f0_cmd) and f0_cmd > 0:
            acf = m2._acf_full(frame)
            r = float(np.clip(m2._r_at_lag(acf, sr / f0_cmd), 0.0, 0.999))
            n_voiced += 1
        else:
            r = 0.0
        r_vals.append(r)
        r_c = float(np.clip(r, 0.01, 0.99))
        db_vals.append(10.0 * np.log10(r_c / (1.0 - r_c)))

    r_arr = np.array(r_vals, dtype=float)
    db_arr = np.array(db_vals, dtype=float)
    return m3.PeriodicityTrackV3(
        r_median=float(np.median(r_arr)) if len(r_arr) else 0.0,
        periodicity_db_median=float(np.median(db_arr)) if len(db_arr) else float("nan"),
        n_frames=len(r_vals), n_voiced_frames=n_voiced,
    )


# ---------------------------------------------------------------------------
# vibrato_depth（score-informed: commanded F0 ±200cent 窓で棄却）
# ---------------------------------------------------------------------------


@dataclass
class VibratoScoredV2:
    depth_cents: float
    reject_rate: float
    n_frames: int
    n_accepted: int


def vibrato_depth_scored(sig: np.ndarray, sr: int, commanded_f0: np.ndarray, window_cents: float = VIBRATO_WINDOW_CENTS) -> VibratoScoredV2:
    blind_track = m3.raw_frame_f0_track(sig, sr, FMIN, FMAX)  # ノート単位オクターブ補正なし = 完全に score-informed な棄却に委ねる
    centers = _frame_centers(sig, sr)
    n = min(len(blind_track), len(centers))
    blind_track = blind_track[:n]
    cmd_at_frames = np.array([
        commanded_f0[c] if 0 <= c < len(commanded_f0) else float("nan") for c in centers[:n]
    ])

    valid = np.isfinite(blind_track) & (blind_track > 0) & np.isfinite(cmd_at_frames) & (cmd_at_frames > 0)
    cents_vs_cmd = np.full(n, np.nan)
    cents_vs_cmd[valid] = 1200.0 * np.log2(blind_track[valid] / cmd_at_frames[valid])
    accept = valid & (np.abs(np.nan_to_num(cents_vs_cmd, nan=1e9)) <= window_cents)

    n_frames = n
    reject_rate = 1.0 - (float(accept.sum()) / n_frames if n_frames else 0.0)
    if accept.sum() < 4:
        return VibratoScoredV2(float("nan"), reject_rate, n_frames, int(accept.sum()))

    accepted_hz = blind_track[accept]
    med = float(np.median(accepted_hz))
    cents = 1200.0 * np.log2(accepted_hz / med)
    mad = float(np.median(np.abs(cents - np.median(cents))))
    return VibratoScoredV2(mad * 1.4826, reject_rate, n_frames, int(accept.sum()))


# ---------------------------------------------------------------------------
# gate6-v2: grip 非退行クイックチェック（score-informed periodicity/vibrato）
# ---------------------------------------------------------------------------

_QUICK_FEATURES = list(gc._QUICK_FEATURES)
_QUICK_REF_SCALE = dict(gc._QUICK_REF_SCALE)


def _quick_features_scored(genome_base, midi: float, wav: np.ndarray, sr: int) -> Dict[str, float]:
    f0 = m3.estimate_f0_v3(wav, sr=sr, fmin=FMIN, fmax=FMAX)  # mean_f0 は W1 対象外（memo 範囲外）、無改変で流用
    cmd = commanded_f0_track(midi, genome_base, len(wav), sr)
    ptrack = periodicity_track_scored(wav, sr, cmd)
    rms = float(np.sqrt(np.mean(wav ** 2)))
    vib = vibrato_depth_scored(wav, sr, cmd)
    return {
        "mean_f0": 1200.0 * np.log2(f0) if np.isfinite(f0) and f0 > 0 else float("nan"),
        "periodicity": ptrack.periodicity_db_median,
        "rms": 20.0 * np.log10(max(rms, 1e-12)),
        "vibrato_depth": vib.depth_cents,
    }, vib.reject_rate


def _grip_axis_v2(genome_base, axis: str, sweep_values: List[float], intended: str) -> Dict:
    from dataclasses import replace as _replace
    n_sweep, n_probe = len(sweep_values), len(gc._PROBE_MIDIS)
    matrices = {f: np.full((n_sweep, n_probe), np.nan) for f in _QUICK_FEATURES}
    reject_rates = np.full((n_sweep, n_probe), np.nan)

    for si, v in enumerate(sweep_values):
        if axis == "breathiness":
            g = _replace(genome_base, noise=_replace(genome_base.noise, breathiness_base=v))
        elif axis == "vibrato_depth":
            g = _replace(genome_base, microprosody=_replace(genome_base.microprosody, vibrato_depth_cents=v))
        else:
            raise ValueError(axis)
        for pi, (probe_name, midi) in enumerate(gc._PROBE_MIDIS.items()):
            wav = rs.render_sustained_vowel(g, midi, vowel="a", duration_sec=1.2)
            feats, reject_rate = _quick_features_scored(g, midi, wav, rs.SR_OUT)
            for f in _QUICK_FEATURES:
                matrices[f][si, pi] = feats[f]
            reject_rates[si, pi] = reject_rate

    side_features = [f for f in _QUICK_FEATURES if f != intended]
    E = {}
    for f in _QUICK_FEATURES:
        raw_deltas = matrices[f][n_sweep - 1, :] - matrices[f][0, :]
        E_note = np.abs(raw_deltas) / _QUICK_REF_SCALE[f]
        E[f] = float(np.nanmedian(E_note))

    E_intended = E[intended]
    max_side = max(E[f] for f in side_features) if side_features else 0.0
    grip = E_intended / max(max_side, 1.0)

    mat = matrices[intended]
    matches, total = 0, 0
    for pi in range(n_probe):
        col = mat[:, pi]
        if not np.all(np.isfinite(col)):
            continue
        overall = np.sign(col[-1] - col[0])
        if overall == 0:
            continue
        for d in np.diff(col):
            total += 1
            if np.sign(d) == overall or d == 0:
                matches += 1
    direction = matches / total if total > 0 else float("nan")

    gate_pass = bool(grip >= gc.GRIP_GATE_MIN and np.isfinite(direction) and direction >= gc.GRIP_DIRECTION_MIN and E_intended >= gc.GRIP_EFFECT_MIN)
    return {
        "axis": axis, "intended_feature": intended, "grip_ratio": round(grip, 4),
        "direction_consistency": round(direction, 4) if np.isfinite(direction) else None,
        "E_intended": round(E_intended, 4), "E_side": {f: round(E[f], 4) for f in side_features},
        "vibrato_reject_rate_mean": float(np.nanmean(reject_rates)),
        "vibrato_reject_rate_max": float(np.nanmax(reject_rates)),
        "provenance": PROVENANCE,
        "pass": gate_pass,
    }


def gate6_grip_quick_check_v2(genome_base) -> Dict:
    breathiness = _grip_axis_v2(genome_base, "breathiness", [0.0, 0.1, 0.2, 0.35, 0.5], "periodicity")
    vibrato = _grip_axis_v2(genome_base, "vibrato_depth", [0.0, 25.0, 50.0, 75.0, 100.0], "vibrato_depth")
    return {"breathiness": breathiness, "vibrato_depth": vibrato, "provenance": PROVENANCE, "pass": bool(breathiness["pass"] and vibrato["pass"])}


# ---------------------------------------------------------------------------
# full_gates_v2: gate1-5(無改変 import) + gate6-v2
# ---------------------------------------------------------------------------


def full_gates_v2(genome) -> Dict:
    result = rs.render_sakura(genome)
    rows = gc.measure_notes(result)
    g1 = gc.gate1_f0_tracking(rows)
    g2 = gc.gate2_plausibility(rows)
    g3 = gc.gate3_consonant_existence(result)
    g4 = gc.gate4_determinism(genome)
    g5 = gc.gate5_aliasing(result.wav, result.sr)
    g6 = gate6_grip_quick_check_v2(genome)
    all_pass = bool(g1["pass"] and g2["pass"] and g3["pass"] and g4["pass"] and g5["pass"] and g6["pass"])
    return {"gate1": g1, "gate2": g2, "gate3": g3, "gate4": g4, "gate5": g5, "gate6": g6, "pass": all_pass, "provenance_gate6": PROVENANCE}
