"""singer/gate_checks.py — S5 機械前提ゲート 6 条件の実装（凍結ゲート）。

`measure_v3.py`（harness、強化 F0 推定器・periodicity・vibrato_depth）を
無改変で import・再利用する。本ファイルはゲート判定ロジックのみを持ち、
`run_machine_gates.py`（レポート生成）と `tests/test_machine_gates.py`
（pytest 自動テスト）の両方から共有される。
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, List

import numpy as np

_HERE = Path(__file__).resolve().parent
_VT_HARNESS_DIR = _HERE.parent / "harness"
if str(_VT_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_VT_HARNESS_DIR))

import measure_v3 as m3  # noqa: E402  (harness、変更禁止・import 流用のみ)

import render_song as rs  # noqa: E402

FMIN, FMAX = 55.0, 2200.0

# --- gate 閾値（凍結、r09_design_memo.md §S5） ---
F0_MEDIAN_CENTS_MAX = 50.0
F0_ALL_NOTES_CENTS_MAX = 100.0
PERIODICITY_R_MIN = 0.35
ALIASING_DB_MAX = -40.0
ALIASING_FREQ_FRACTION = 0.45
GRIP_GATE_MIN = 3.0
GRIP_DIRECTION_MIN = 0.90
GRIP_EFFECT_MIN = 2.0
# gate 3（子音の実在）の閾値: 子音区間の高域エネルギー比が隣接母音区間の
# 何倍以上あれば「鳴っている」とみなすか。[UNDERSPEC-S5-1] memo は
# 「閾値は実装時に設定し記録」と明記しており、ここで確定する。
CONSONANT_ENERGY_RATIO_MIN = 1.3


def midi_to_hz(m: float) -> float:
    return 440.0 * 2.0 ** ((m - 69.0) / 12.0)


def cents_diff(a_hz: float, b_hz: float) -> float:
    if a_hz <= 0 or b_hz <= 0 or not np.isfinite(a_hz) or not np.isfinite(b_hz):
        return float("nan")
    return 1200.0 * float(np.log2(a_hz / b_hz))


# ---------------------------------------------------------------------------
# gate 1 + 2: F0 追従 / plausibility
# ---------------------------------------------------------------------------


@dataclass
class NoteMeasurement:
    note_index: int
    kana: str
    true_midi: float
    true_hz: float
    est_hz: float
    cents_err: float
    r_median: float


def measure_notes(result: rs.RenderResult) -> List[NoteMeasurement]:
    rows = []
    for i, seg in enumerate(result.segments):
        n = seg.end_sample - seg.start_sample
        core_lo = seg.start_sample + int(n * 0.25)
        core_hi = seg.start_sample + int(n * 0.75)
        core = result.wav[core_lo:core_hi]
        if len(core) < 256:
            core = result.wav[seg.start_sample:seg.end_sample]
        est_hz = m3.estimate_f0_v3(core, sr=result.sr, fmin=FMIN, fmax=FMAX)
        true_hz = midi_to_hz(seg.note.midi)
        err = cents_diff(est_hz, true_hz)
        ptrack = m3.periodicity_track_v3(core, sr=result.sr, fmin=FMIN, fmax=FMAX)
        rows.append(
            NoteMeasurement(
                note_index=i, kana=seg.note.mora.kana, true_midi=seg.note.midi, true_hz=true_hz,
                est_hz=est_hz, cents_err=err, r_median=ptrack.r_median,
            )
        )
    return rows


def gate1_f0_tracking(rows: List[NoteMeasurement]) -> Dict:
    errs = [abs(r.cents_err) for r in rows if np.isfinite(r.cents_err)]
    median_err = float(np.median(errs)) if errs else float("nan")
    max_err = float(np.max(errs)) if errs else float("nan")
    all_ok = bool(errs) and all(e <= F0_ALL_NOTES_CENTS_MAX for e in errs)
    median_ok = np.isfinite(median_err) and median_err <= F0_MEDIAN_CENTS_MAX
    n_finite = len(errs)
    return {
        "n_notes": len(rows), "n_finite": n_finite,
        "median_abs_cents_err": median_err, "max_abs_cents_err": max_err,
        "gate_median_le_50c": bool(median_ok), "gate_all_le_100c": bool(all_ok),
        "pass": bool(median_ok and all_ok and n_finite == len(rows)),
    }


def gate2_plausibility(rows: List[NoteMeasurement]) -> Dict:
    # 非有限（NaN/inf）の r_median は「未測定」であり「measured 良好」ではない。
    # `r_median < PERIODICITY_R_MIN` だけで判定すると NaN は比較が常に False に
    # なるため violation に数えられず、測定不能ノートが黙って合格側へ漏れる
    # （PR#261 レビュー R7）。有限性チェックを閾値比較より先に行い、非有限は
    # 無条件で violation 側へ倒す（fail-closed）。
    violations = [r for r in rows if not np.isfinite(r.r_median) or r.r_median < PERIODICITY_R_MIN]
    return {
        "n_notes": len(rows), "n_violations": len(violations),
        "r_threshold": PERIODICITY_R_MIN,
        "violating_notes": [(v.note_index, v.kana, round(v.r_median, 4)) for v in violations],
        "pass": bool(len(violations) == 0),
    }


# ---------------------------------------------------------------------------
# gate 3: 子音の実在
# ---------------------------------------------------------------------------


def _high_energy_ratio(sig: np.ndarray, sr: int, cutoff_hz: float = 3000.0) -> float:
    if len(sig) < 32:
        return float("nan")
    spec = np.abs(np.fft.rfft(sig)) ** 2
    freqs = np.fft.rfftfreq(len(sig), d=1.0 / sr)
    total = spec.sum()
    if total <= 0:
        return float("nan")
    high = spec[freqs >= cutoff_hz].sum()
    return float(high / total)


def gate3_consonant_existence(result: rs.RenderResult) -> Dict:
    """gate 3: 子音の実在。

    PR#261 レビュー R20: 旧実装は `result.subsegments_out`（レンダラが実際に
    生成したサブセグメント列）だけを走査し、期待される子音インスタンスの
    集合と一切照合していなかった。レンダラ側の不具合等で期待される子音
    サブセグメントが 1 つも出力されなかった場合、走査対象自体が最初から
    空になるため検出しようがなく、そのノートは黙って評価対象から外れ、
    他のノートが全て pass すれば gate3 全体が pass してしまう検出力の
    欠陥があった（「レンダラが出したものだけを見る」検証は「レンダラが
    何かを出し忘れた」ケースを原理的に検出できない）。

    本実装は楽譜側（`result.segments[i].note.mora.onset`。S4 score.py の
    `build_sakura_score()` が phoneme_jp.py の `kana_to_morae()` で決めた
    値であり、レンダラの実出力とは独立した「楽譜が何を要求しているか」の
    情報源）から「期待子音インスタンス集合」
    `{(note_index, onset) | onset in target_onsets}` を先に確定し、実際の
    `subsegments_out` に対応するインスタンスが 1 つでも欠落していれば
    無条件で FAIL とする。
    """
    target_onsets = {"s": "fricative_high", "k": "burst_k", "t": "burst_t"}

    expected_keys = {
        (i, seg.note.mora.onset)
        for i, seg in enumerate(result.segments)
        if seg.note.mora.onset in target_onsets
    }

    actual_by_key: Dict[tuple, object] = {}
    for sub in result.subsegments_out:
        if sub.onset not in target_onsets or sub.noise_type != target_onsets[sub.onset]:
            continue
        # 同一 (note_index, onset) への複数一致は現行レンダラの実装上
        # 起こらないが、あれば先勝ちで代表させる。
        actual_by_key.setdefault((sub.note_index, sub.onset), sub)

    rows = []
    missing = []
    for note_index, onset in sorted(expected_keys):
        sub = actual_by_key.get((note_index, onset))
        if sub is None:
            missing.append({"note_index": note_index, "onset": onset, "reason": "missing_consonant_subsegment"})
            continue
        cons_wav = result.wav[sub.start:sub.end]
        # 直後の母音区間（同ノートの残り、または次ノート開始まで）と比較
        note_end = result.segments[sub.note_index].end_sample
        vowel_wav = result.wav[sub.end:min(note_end, sub.end + (sub.end - sub.start))]
        cons_ratio = _high_energy_ratio(cons_wav, result.sr)
        vowel_ratio = _high_energy_ratio(vowel_wav, result.sr)
        ok = bool(
            np.isfinite(cons_ratio) and np.isfinite(vowel_ratio) and vowel_ratio > 0
            and cons_ratio >= CONSONANT_ENERGY_RATIO_MIN * vowel_ratio
        )
        rows.append(
            {
                "note_index": sub.note_index, "onset": sub.onset,
                "consonant_high_energy_ratio": cons_ratio, "vowel_high_energy_ratio": vowel_ratio,
                "ratio_of_ratios": (cons_ratio / vowel_ratio) if (np.isfinite(cons_ratio) and vowel_ratio) else None,
                "pass": ok,
            }
        )
    return {
        "threshold_ratio_of_ratios_min": CONSONANT_ENERGY_RATIO_MIN,
        "n_expected": len(expected_keys),
        "n_checked": len(rows), "n_pass": sum(1 for r in rows if r["pass"]),
        "n_missing": len(missing), "missing": missing,
        "details": rows,
        "pass": bool(expected_keys) and not missing and all(r["pass"] for r in rows),
    }


# ---------------------------------------------------------------------------
# gate 4: 決定論
# ---------------------------------------------------------------------------


def gate4_determinism(genome) -> Dict:
    r1 = rs.render_sakura(genome)
    r2 = rs.render_sakura(genome)
    h1 = hashlib.sha256(r1.wav.tobytes()).hexdigest()
    h2 = hashlib.sha256(r2.wav.tobytes()).hexdigest()
    return {"hash_1": h1, "hash_2": h2, "identical_arrays": bool(np.array_equal(r1.wav, r2.wav)), "pass": bool(h1 == h2)}


# ---------------------------------------------------------------------------
# gate 5: aliasing
# ---------------------------------------------------------------------------


def gate5_aliasing(wav: np.ndarray, sr: int) -> Dict:
    spec = np.abs(np.fft.rfft(wav)) ** 2
    freqs = np.fft.rfftfreq(len(wav), d=1.0 / sr)
    total = spec.sum() + 1e-30
    cutoff = ALIASING_FREQ_FRACTION * sr
    high = spec[freqs >= cutoff].sum()
    ratio = high / total
    db = 10.0 * np.log10(max(ratio, 1e-30))
    return {"cutoff_hz": cutoff, "energy_ratio": float(ratio), "energy_ratio_db": float(db), "pass": bool(db < ALIASING_DB_MAX)}


# ---------------------------------------------------------------------------
# gate 6: grip 非退行クイックチェック（breathiness / vibrato_depth のみ）
# ---------------------------------------------------------------------------

_PROBE_MIDIS = {"C3": 48, "E4": 64, "A4": 69, "C5": 72, "C6": 84}
_QUICK_FEATURES = ["mean_f0", "periodicity", "rms", "vibrato_depth"]
_QUICK_REF_SCALE = {"mean_f0": 25.0, "periodicity": 3.0, "rms": 1.0, "vibrato_depth": 10.0}


def _quick_features(wav: np.ndarray, sr: int) -> Dict[str, float]:
    f0 = m3.estimate_f0_v3(wav, sr=sr, fmin=FMIN, fmax=FMAX)
    ptrack = m3.periodicity_track_v3(wav, sr=sr, fmin=FMIN, fmax=FMAX)
    rms = float(np.sqrt(np.mean(wav**2)))
    vib = m3.vibrato_depth_robust_v3(wav, sr=sr, fmin=FMIN, fmax=FMAX)
    return {
        "mean_f0": 1200.0 * np.log2(f0) if np.isfinite(f0) and f0 > 0 else float("nan"),
        "periodicity": ptrack.periodicity_db_median,
        "rms": 20.0 * np.log10(max(rms, 1e-12)),
        "vibrato_depth": vib.depth_cents,
    }


def _grip_axis(genome_base, axis: str, sweep_values: List[float], intended: str) -> Dict:
    n_sweep, n_probe = len(sweep_values), len(_PROBE_MIDIS)
    matrices = {f: np.full((n_sweep, n_probe), np.nan) for f in _QUICK_FEATURES}

    for si, v in enumerate(sweep_values):
        if axis == "breathiness":
            g = replace(genome_base, noise=replace(genome_base.noise, breathiness_base=v))
        elif axis == "vibrato_depth":
            g = replace(genome_base, microprosody=replace(genome_base.microprosody, vibrato_depth_cents=v))
        else:
            raise ValueError(axis)
        for pi, (probe_name, midi) in enumerate(_PROBE_MIDIS.items()):
            wav = rs.render_sustained_vowel(g, midi, vowel="a", duration_sec=1.2)
            feats = _quick_features(wav, rs.SR_OUT)
            for f in _QUICK_FEATURES:
                matrices[f][si, pi] = feats[f]

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

    gate_pass = bool(grip >= GRIP_GATE_MIN and np.isfinite(direction) and direction >= GRIP_DIRECTION_MIN and E_intended >= GRIP_EFFECT_MIN)
    return {
        "axis": axis, "intended_feature": intended, "grip_ratio": round(grip, 4),
        "direction_consistency": round(direction, 4) if np.isfinite(direction) else None,
        "E_intended": round(E_intended, 4), "E_side": {f: round(E[f], 4) for f in side_features},
        "pass": gate_pass,
    }


def gate6_grip_quick_check(genome_base) -> Dict:
    breathiness = _grip_axis(genome_base, "breathiness", [0.0, 0.1, 0.2, 0.35, 0.5], "periodicity")
    vibrato = _grip_axis(genome_base, "vibrato_depth", [0.0, 25.0, 50.0, 75.0, 100.0], "vibrato_depth")
    return {"breathiness": breathiness, "vibrato_depth": vibrato, "pass": bool(breathiness["pass"] and vibrato["pass"])}
