"""run8/s7_trf.py — 観測ベクトル `terminal_release_failure/0.1` の実装（§5）。

**単一スコアに潰さない。** 主観測 4 値は**凍結済み** `trf_measurement_spec.json`
（1.0 / frozen）の選定候補と窓・hop でのみ測る（新しい metric を作らない）。
補助軸は §5 の一覧に対応する機械量を持ち、機械量を持たない軸
（`intelligibility_loss`）は**持たないと宣言する**（§5-0a）。

境界は**命令から**取る（波形から推定しない）。測定は**正規化前の生波形**で行う
（§5-2。`synth_song` のピーク正規化を通さない）。

無声対照との差分（§5-0）は群単位の後処理なので `ringing.py` 相当の関数を本
モジュール末尾に置く（`apply_ringing_correction`）。`/su/ だから無声` という
前提は使わず、**終端窓の voiced_frames == 0 を機械検査で満たしたレンダだけ**を
基準母集団に入れ、評価対象セル自身は必ず除外する（leave-one-out）。
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import s7_b1_calibration as b1  # noqa: E402
import s7_io  # noqa: E402
import s7_spec as sp  # noqa: E402

RESULTS_DIR = _HERE.parent / "results_s7"
TRF_SPEC_PATH = RESULTS_DIR / "trf_measurement_spec.json"
TRF_SCHEMA = sp.TRF_SCHEMA

_MS = 1000.0
#: 有声判定の絶対 RMS 閾値（§5-2 が `hnr_median_db` について明示している値）。
RMS_FLOOR = 1e-3


class MeasurementSpecNotFrozen(RuntimeError):
    """凍結されていない測定仕様で本番セルを測ろうとした（fail-closed）。"""


@dataclass(frozen=True)
class FrozenMeasurement:
    """凍結 spec から作った「測り方」。候補・窓・hop・ε をここでしか持たない。"""

    spec_sha256: str
    spec_version: str
    tail_window_ms: float
    candidates: Dict[str, Any]          # axis -> b1.Candidate
    epsilons: Dict[str, float]

    def axis_candidate_id(self, axis: str) -> str:
        return str(self.candidates[axis].candidate_id)


def load_frozen_measurement(path: Path = TRF_SPEC_PATH) -> FrozenMeasurement:
    spec, sha, _ = s7_io.read_json_with_pin(path)
    if spec.get("freeze_status") != "frozen":
        raise MeasurementSpecNotFrozen(
            f"{path.name}: freeze_status={spec.get('freeze_status')!r}。"
            "凍結前の仕様で本番セルを測らない（§12-0-D）"
        )
    prereg = b1.load_prereg()
    by_id = {c.candidate_id: c for c in b1.enumerate_candidates(prereg)}
    candidates, epsilons = {}, {}
    for axis in sp.PRIMARY_AXES:
        entry = spec["axes"][axis]
        if entry["status"] != sp.AxisStatus.FROZEN.value:
            raise MeasurementSpecNotFrozen(f"axis {axis} is {entry['status']}")
        cid = str(entry["selected_candidate"])
        if cid not in by_id:
            raise MeasurementSpecNotFrozen(f"axis {axis}: 候補 {cid} が候補空間に無い")
        candidates[axis] = by_id[cid]
        epsilons[axis] = float(entry["epsilon"])
    return FrozenMeasurement(
        spec_sha256=sha,
        spec_version=str(spec["spec_version"]),
        tail_window_ms=float(spec["boundaries"]["tail_window_ms"]),
        candidates=candidates,
        epsilons=epsilons,
    )


@dataclass(frozen=True)
class CellWindows:
    """1 セルの測定窓（すべて**命令から**算出する）。"""

    sample_rate: int
    note_onset_s: float
    commanded_note_end_s: float
    score_boundary_s: float
    tail_window_ms: float
    target_phone_frames: Tuple[int, ...]
    frame_ms: float

    def as_stimulus(self, samples: np.ndarray, stim_id: str, family: str):
        return b1.Stimulus(
            stim_id=stim_id,
            family=family,
            samples=np.asarray(samples, dtype=np.float64),
            sr=int(self.sample_rate),
            note_onset_s=self.note_onset_s,
            commanded_note_end_s=self.commanded_note_end_s,
            score_boundary_s=self.score_boundary_s,
            tail_window_ms=self.tail_window_ms,
        )


def windows_from_command(
    measurement: Dict[str, Any],
    target_note_index: int,
    target_beats: float,
    tempo_bpm: float,
    tail_window_ms: float,
) -> CellWindows:
    """`run_pipeline(measurement_out=...)` の命令区間から窓を作る。"""
    counts = list(measurement["note_phone_counts"])
    dur = list(measurement["final_phone_dur"])
    frame_ms = float(measurement["frame_ms"])
    head = int(measurement["head_frames"])
    start_phone = int(sum(counts[:target_note_index]))
    n_phones = int(counts[target_note_index])
    frames_before = int(sum(dur[:start_phone]))
    target_frames = tuple(int(x) for x in dur[start_phone: start_phone + n_phones])
    onset_s = (head + frames_before) * frame_ms / _MS
    commanded_end_s = onset_s + sum(target_frames) * frame_ms / _MS
    boundary_s = onset_s + float(target_beats) * 60.0 / float(tempo_bpm)
    return CellWindows(
        sample_rate=int(measurement["sample_rate"]),
        note_onset_s=onset_s,
        commanded_note_end_s=commanded_end_s,
        score_boundary_s=boundary_s,
        tail_window_ms=float(tail_window_ms),
        target_phone_frames=target_frames,
        frame_ms=frame_ms,
    )


# --- 主観測 4 値（凍結候補でのみ測る） --------------------------------------


def measure_primary(
    frozen: FrozenMeasurement, samples: np.ndarray, win: CellWindows, cell_id: str
) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for axis in sp.PRIMARY_AXES:
        cand = frozen.candidates[axis]
        stim = win.as_stimulus(samples, cell_id, "production_cell")
        out[axis] = float(b1.measure_candidate(cand, stim)[axis])
    return out


def tail_voiced_frames(
    frozen: FrozenMeasurement, samples: np.ndarray, win: CellWindows, cell_id: str
) -> int:
    """終端窓の有声フレーム数（§5-0-1 の無声検査。ms 軸の凍結候補で数える）。"""
    axis = "excess_tail_voiced_ms"
    cand = frozen.candidates[axis]
    impl = b1.VOICING_IMPLS[str(cand.voicing_id)]
    voiced, _f0, times = impl(
        np.asarray(samples, dtype=np.float64), win.sample_rate,
        float(cand.window_ms), float(cand.hop_ms),
    )
    w = win.tail_window_ms / _MS
    mask = (times > win.score_boundary_s) & (times <= win.score_boundary_s + w) & voiced
    return int(np.count_nonzero(mask))


# --- 補助軸（§5。機械量を持つものだけ） -------------------------------------


def _frame_rms(y: np.ndarray, sr: int, win_ms: float, hop_ms: float) -> Tuple[np.ndarray, np.ndarray]:
    n_win = max(1, int(round(win_ms / _MS * sr)))
    n_hop = max(1, int(round(hop_ms / _MS * sr)))
    n = 1 + max(0, (len(y) - n_win) // n_hop)
    times = np.arange(n, dtype=np.float64) * n_hop / sr
    rms = np.empty(n, dtype=np.float64)
    for i in range(n):
        seg = y[i * n_hop: i * n_hop + n_win]
        rms[i] = float(np.sqrt(np.mean(seg**2))) if seg.size else 0.0
    return rms, times


def _hnr_db(seg: np.ndarray, sr: int) -> Optional[float]:
    """自己相関ピークからの HNR（dB）。無声・低エネルギーは None。"""
    if seg.size < 64 or float(np.sqrt(np.mean(seg**2))) < RMS_FLOOR:
        return None
    x = seg - seg.mean()
    n = int(2 ** math.ceil(math.log2(2 * len(x))))
    spec = np.fft.rfft(x, n)
    ac = np.fft.irfft(spec * np.conjugate(spec), n)[: len(x)]
    if ac[0] <= 0:
        return None
    ac = ac / ac[0]
    lag_min = max(1, int(sr / 800.0))
    lag_max = min(len(ac) - 1, int(sr / 60.0))
    if lag_max <= lag_min:
        return None
    r = float(np.max(ac[lag_min:lag_max]))
    r = min(max(r, 1e-6), 1 - 1e-6)
    return float(10.0 * math.log10(r / (1.0 - r)))


def measure_hnr(samples: np.ndarray, win: CellWindows) -> Dict[str, Optional[float]]:
    """終端母音区間を前後 2 窓に割り、各窓の HNR 中央値と差を返す（§5)。"""
    sr = win.sample_rate
    v_start = win.note_onset_s + sum(win.target_phone_frames[:-1]) * win.frame_ms / _MS
    v_end = win.commanded_note_end_s
    i0, i1 = int(round(v_start * sr)), int(round(v_end * sr))
    if i1 - i0 < sr // 20:
        return {"hnr_median_db_p1": None, "hnr_median_db_p2": None, "hnr_delta_db": None}
    mid = (i0 + i1) // 2
    out: Dict[str, Optional[float]] = {}
    for key, (a, b) in (("hnr_median_db_p1", (i0, mid)), ("hnr_median_db_p2", (mid, i1))):
        frame = max(1, int(0.030 * sr))
        hop = max(1, int(0.010 * sr))
        vals = [
            v
            for start in range(a, max(a, b - frame) + 1, hop)
            if (v := _hnr_db(samples[start: start + frame], sr)) is not None
        ]
        out[key] = float(np.median(vals)) if vals else None
    p1, p2 = out["hnr_median_db_p1"], out["hnr_median_db_p2"]
    out["hnr_delta_db"] = None if (p1 is None or p2 is None) else float(p2 - p1)
    return out


def _mel_frames(mel: np.ndarray) -> np.ndarray:
    """acoustic の mel を [T, n_mels] へそろえる。"""
    arr = np.asarray(mel, dtype=np.float64)
    if arr.ndim == 3:
        arr = arr[0]
    if arr.shape[0] < arr.shape[1] and arr.shape[0] in (80, 128):
        arr = arr.T
    return arr


def terminal_mel_vector(mel: np.ndarray, win: CellWindows, measurement: Dict[str, Any]) -> np.ndarray:
    """終端母音の後半 1/3 の平均 mel（対照間距離の共通表現）。"""
    frames = _mel_frames(mel)
    head = int(measurement["head_frames"])
    start = head + int(round((win.note_onset_s * _MS) / win.frame_ms)) - head
    n_target = int(sum(win.target_phone_frames))
    v_start = head + int(round((win.note_onset_s * _MS) / win.frame_ms)) - head
    del start, v_start
    onset_frame = int(round(win.note_onset_s * _MS / win.frame_ms))
    vowel_start = onset_frame + int(sum(win.target_phone_frames[:-1]))
    vowel_end = onset_frame + n_target
    lo = max(0, vowel_start + (2 * (vowel_end - vowel_start)) // 3)
    hi = max(lo + 1, min(frames.shape[0], vowel_end))
    return frames[lo:hi].mean(axis=0)


def measure_vowel_drift(mel: np.ndarray, win: CellWindows) -> Optional[float]:
    """終端母音区間の前 1/3 と後 1/3 の平均 mel の L1（§5）。"""
    frames = _mel_frames(mel)
    onset_frame = int(round(win.note_onset_s * _MS / win.frame_ms))
    vowel_start = onset_frame + int(sum(win.target_phone_frames[:-1]))
    vowel_end = onset_frame + int(sum(win.target_phone_frames))
    span = vowel_end - vowel_start
    if span < 3 or vowel_end > frames.shape[0]:
        return None
    third = max(1, span // 3)
    a = frames[vowel_start: vowel_start + third].mean(axis=0)
    b = frames[vowel_end - third: vowel_end].mean(axis=0)
    return float(np.abs(b - a).sum())


def measure_energy_decay_slope(samples: np.ndarray, win: CellWindows) -> Optional[float]:
    """終端窓の log エネルギーの傾き（dB/s）。窓に信号が無ければ None。"""
    sr = win.sample_rate
    a = int(round(win.score_boundary_s * sr))
    b = int(round((win.score_boundary_s + win.tail_window_ms / _MS) * sr))
    seg = np.asarray(samples[a:b], dtype=np.float64)
    if seg.size < sr // 50:
        return None
    rms, times = _frame_rms(seg, sr, 20.0, 10.0)
    ok = rms > 0
    if int(np.count_nonzero(ok)) < 3:
        return None
    db = 20.0 * np.log10(rms[ok] + 1e-12)
    slope = np.polyfit(times[ok], db, 1)[0]
    return float(slope)


def measure_duration_axes(win: CellWindows) -> Dict[str, Any]:
    """命令側の尺（§5 duration）。すべて命令から算出する。"""
    frames = list(win.target_phone_frames)
    return {
        "r_frames": int(frames[0]) if len(frames) > 1 else 0,
        "i_frames": int(frames[-1]),
        "target_note_frames": int(sum(frames)),
        "duration_overshoot_ms": float(
            (win.commanded_note_end_s - win.score_boundary_s) * _MS
        ),
    }


#: 機械量を持たない TRF 下位軸（§5-0a。**持たない軸を持つと書かない**）。
AXES_WITHOUT_MACHINE_MEASURE = ("intelligibility_loss",)


# --- 無声対照との差分（§5-0） -----------------------------------------------


def apply_ringing_correction(
    cells: Sequence[Dict[str, Any]], axes: Sequence[str] = sp.PRIMARY_AXES
) -> Dict[str, Any]:
    """群（同一話者 × 同一世代）内で ringing 基準を作り、主観測値を補正する。

    規則（§5-0）:
      1. 終端窓の `tail_voiced_frames == 0` を**機械検査**で満たしたセルだけが基準母集団
      2. leave-one-out（評価対象セル自身は必ず除外）
      3. 基準が空なら差し引かず `status = ringing_uncorrected`
      4. 群に 1 つでも補正不能があれば群ごと Gate から外し
         `status = ringing_uncorrected_group`
    """
    pool = [c for c in cells if c.get("tail_voiced_frames") == 0 and c.get("primary")]
    group_status = "ok"
    for cell in cells:
        if not cell.get("primary"):
            continue
        refs = [c for c in pool if c["cell_id"] != cell["cell_id"]]
        if not refs:
            cell["status"] = "ringing_uncorrected"
            cell["primary_corrected"] = None
            group_status = "ringing_uncorrected_group"
            continue
        corrected = {}
        for axis in axes:
            base = float(np.median([float(r["primary"][axis]) for r in refs]))
            corrected[axis] = float(cell["primary"][axis]) - base
        cell["primary_corrected"] = corrected
        cell["ringing_reference_n"] = len(refs)
        if cell.get("status") in (None, "ok"):
            cell["status"] = "ok"
    return {
        "group_status": group_status,
        "unvoiced_reference_pool": [c["cell_id"] for c in pool],
        "in_gate": group_status == "ok",
    }


def add_contrast_axes(cells: Sequence[Dict[str, Any]]) -> None:
    """話者内 3 対照差分（§5-1）。同一 (音高 × 尺) の /N/ と /i/ を基準にする。"""
    by_key: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for c in cells:
        probe = c.get("probe")
        if probe in ("P-N-FINAL", "P-I-FINAL") and c.get("terminal_mel") is not None:
            by_key[(probe, str(c.get("pitch_key")), str(c.get("duration_key")))] = c
    for c in cells:
        vec = c.get("terminal_mel")
        if vec is None:
            continue
        key_n = ("P-N-FINAL", str(c.get("pitch_key")), str(c.get("duration_key")))
        key_i = ("P-I-FINAL", str(c.get("pitch_key")), str(c.get("duration_key")))
        ref_n, ref_i = by_key.get(key_n), by_key.get(key_i)
        d_n = (
            float(np.abs(np.asarray(vec) - np.asarray(ref_n["terminal_mel"])).sum())
            if ref_n is not None
            else None
        )
        d_i = (
            float(np.abs(np.asarray(vec) - np.asarray(ref_i["terminal_mel"])).sum())
            if ref_i is not None
            else None
        )
        c.setdefault("acoustic", {})
        c["acoustic"]["i_reference_distance"] = d_i
        c["acoustic"]["N_reference_distance"] = d_n
        c["acoustic"]["N_similarity_delta"] = (
            None if (d_n is None or d_i is None) else float(d_i - d_n)
        )
