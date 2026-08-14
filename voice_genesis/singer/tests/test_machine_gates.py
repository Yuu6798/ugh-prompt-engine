"""S5 機械前提条件（凍結ゲート）の自動テスト。

`gate_checks.py` のロジックを `run_machine_gates.py`（レポート生成）と共有
する。重い（曲全体レンダリング・grip クイックチェック等）ため、通常の
高速テストループからは外す運用を想定するなら pytest マーカーを付与できるが、
本実装は「数分規模」の制約内に収まるため無印のまま提供する。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gate_checks as gc
import render_song as rs

VOICES = {"voice_A": rs.voice_a, "voice_B": rs.voice_b}


def test_gate1_f0_tracking_voice_a():
    result = rs.render_sakura(rs.voice_a())
    rows = gc.measure_notes(result)
    g1 = gc.gate1_f0_tracking(rows)
    assert g1["pass"], g1


def test_gate1_f0_tracking_voice_b():
    result = rs.render_sakura(rs.voice_b())
    rows = gc.measure_notes(result)
    g1 = gc.gate1_f0_tracking(rows)
    assert g1["pass"], g1


def test_gate2_plausibility_both_voices():
    for name, fn in VOICES.items():
        result = rs.render_sakura(fn())
        rows = gc.measure_notes(result)
        g2 = gc.gate2_plausibility(rows)
        assert g2["pass"], (name, g2)


def test_gate3_consonant_existence_both_voices():
    for name, fn in VOICES.items():
        result = rs.render_sakura(fn())
        g3 = gc.gate3_consonant_existence(result)
        assert g3["pass"], (name, g3)


def test_gate4_determinism_both_voices():
    for name, fn in VOICES.items():
        g4 = gc.gate4_determinism(fn())
        assert g4["pass"], (name, g4)


def test_gate5_aliasing_both_voices():
    for name, fn in VOICES.items():
        result = rs.render_sakura(fn())
        g5 = gc.gate5_aliasing(result.wav, result.sr)
        assert g5["pass"], (name, g5)


def test_gate6_grip_quick_check_voice_a():
    g6 = gc.gate6_grip_quick_check(rs.voice_a())
    assert g6["pass"], g6


# --- PR#261 レビュー R7: r_median 非有限（NaN/inf）値の fail-closed 化 --------
# 曲全体のレンダリングを伴わない軽量ユニットテスト（NoteMeasurement を直接構築）。


def _note(r_median: float, note_index: int = 0) -> gc.NoteMeasurement:
    return gc.NoteMeasurement(
        note_index=note_index, kana="あ", true_midi=60.0, true_hz=261.63,
        est_hz=261.63, cents_err=0.0, r_median=r_median,
    )


def test_gate2_plausibility_treats_nan_r_median_as_violation():
    """r_median が NaN（未測定）のノートは `< threshold` が常に False になり
    素通りしてしまう旧実装のバグ（PR#261 R7）を、有限性チェックで塞ぐ。"""
    rows = [_note(0.9, 0), _note(float("nan"), 1), _note(0.95, 2)]
    g2 = gc.gate2_plausibility(rows)
    assert g2["n_violations"] == 1
    assert g2["pass"] is False
    assert 1 in [v[0] for v in g2["violating_notes"]]


def test_gate2_plausibility_treats_inf_r_median_as_violation():
    rows = [_note(0.9, 0), _note(float("inf"), 1)]
    g2 = gc.gate2_plausibility(rows)
    assert g2["n_violations"] == 1
    assert g2["pass"] is False


def test_gate2_plausibility_passes_when_all_r_median_finite_and_above_threshold():
    """非退行確認: 全ノートが有限かつ閾値以上なら従来どおり pass する。"""
    rows = [_note(0.9, 0), _note(0.5, 1), _note(gc.PERIODICITY_R_MIN, 2)]  # 境界値も含む
    g2 = gc.gate2_plausibility(rows)
    assert g2["n_violations"] == 0
    assert g2["pass"] is True
    assert not math.isnan(g2["n_violations"])
