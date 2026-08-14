"""S5 機械前提条件（凍結ゲート）の自動テスト。

`gate_checks.py` のロジックを `run_machine_gates.py`（レポート生成）と共有
する。重い（曲全体レンダリング・grip クイックチェック等）ため、通常の
高速テストループからは外す運用を想定するなら pytest マーカーを付与できるが、
本実装は「数分規模」の制約内に収まるため無印のまま提供する。
"""
from __future__ import annotations

import math
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

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


# --- PR#261 レビュー R20: gate3 の期待子音インスタンス欠落検出 --------------


def test_gate3_consonant_existence_all_four_voices():
    """非退行確認: 既存 4 音源(voice_a〜d)は新しい期待値照合ロジックでも
    引き続き pass する。"""
    for name, fn in {
        "voice_a": rs.voice_a, "voice_b": rs.voice_b, "voice_c": rs.voice_c, "voice_d": rs.voice_d,
    }.items():
        result = rs.render_sakura(fn())
        g3 = gc.gate3_consonant_existence(result)
        assert g3["pass"], (name, g3)
        assert g3["n_missing"] == 0
        assert g3["n_expected"] > 0  # 前提確認: 楽譜側に検査対象の子音が実在する


def test_gate3_consonant_existence_detects_fully_missing_onset():
    """/k/ の子音サブセグメントが全欠落した場合を模擬し、旧実装（レンダラの
    実出力だけを走査）では検出できなかった検出力欠陥(PR#261 R20)を塞げて
    いることを確認する。全 /k/ インスタンスが subsegments_out から失われて
    いても、他ノートが全て pass すれば gate3 全体が pass してしまっていた。
    """
    result = rs.render_sakura(rs.voice_a())
    g3_before = gc.gate3_consonant_existence(result)
    assert g3_before["pass"] is True  # 前提確認: 欠落注入前は pass する
    k_expected = [n for n, o in _expected_onset_keys(result) if o == "k"]
    assert k_expected, "この楽譜には /k/ を持つノートが少なくとも 1 つ含まれるはず"

    # /k/ の burst サブセグメントだけをレンダラ出力から取り除く（他の onset は無傷）。
    filtered_subsegs = [s for s in result.subsegments_out if not (s.onset == "k" and s.noise_type == "burst_k")]
    assert len(filtered_subsegs) < len(result.subsegments_out)  # 前提確認: 実際に除去された
    tampered = replace(result, subsegments_out=filtered_subsegs)

    g3_after = gc.gate3_consonant_existence(tampered)
    assert g3_after["pass"] is False
    assert g3_after["n_missing"] >= 1
    assert all(m["onset"] == "k" for m in g3_after["missing"])


def _expected_onset_keys(result):
    target_onsets = {"s", "k", "t"}
    return [(i, seg.note.mora.onset) for i, seg in enumerate(result.segments) if seg.note.mora.onset in target_onsets]


# --- PR#261 レビュー R23: gate6 grip 判定の sweep×probe 全セル有限性チェック ---


def _patch_grip_axis_lightweight(monkeypatch, n_probe, nan_at):
    """`_grip_axis` 用: render_sustained_vowel/_quick_features をダミー化する。

    intended feature("periodicity")は si に比例した値を全 probe 共通で返し、
    side features は 0 固定（grip 判定の閾値を満たすよう仕込む）。`nan_at`
    （(sweep_index, probe_index) タプル、または None）に該当するセルだけ
    全特徴を NaN にする。
    """
    monkeypatch.setattr(gc.rs, "render_sustained_vowel", lambda g, midi, vowel="a", duration_sec=1.2: np.zeros(4))

    counter = {"n": 0}

    def fake_quick_features(wav, sr):
        idx = counter["n"]
        counter["n"] += 1
        si, pi = divmod(idx, n_probe)
        if nan_at is not None and (si, pi) == nan_at:
            return {"mean_f0": float("nan"), "periodicity": float("nan"), "rms": float("nan"), "vibrato_depth": float("nan")}
        return {"mean_f0": 0.0, "periodicity": 10.0 * si, "rms": 0.0, "vibrato_depth": 0.0}

    monkeypatch.setattr(gc, "_quick_features", fake_quick_features)


def test_grip_axis_fails_when_a_probe_cell_is_non_finite(monkeypatch):
    """`E[f] = float(np.nanmedian(E_note))` は 1 probe が完全に非有限
    （測定不能）でも他の probe が同一値なら中央値が変わらず、旧実装は
    「一部 probe でしか測定できていない不完全な証拠」を検出できなかった
    （PR#261 R23）。sweep×probe グリッドの全セル有限性を PASS の前提条件
    に追加したことで、この不完全証拠 PASS が塞がれていることを確認する。
    """
    probe_names = list(gc._PROBE_MIDIS.keys())
    n_probe = len(probe_names)
    sweep_values = [0.0, 0.1, 0.2, 0.35, 0.5]
    n_sweep = len(sweep_values)
    nan_at = (n_sweep - 1, 2)  # 最終 sweep 点 × 3 番目の probe

    _patch_grip_axis_lightweight(monkeypatch, n_probe, nan_at)

    genome_base = rs.voice_a()
    result = gc._grip_axis(genome_base, "breathiness", sweep_values, "periodicity")

    assert result["non_finite_cells"], "注入した非有限セルが検出されていない"
    assert any(c["sweep_index"] == nan_at[0] and c["probe"] == probe_names[nan_at[1]] for c in result["non_finite_cells"])
    assert result["pass"] is False


def test_grip_axis_passes_for_all_finite_grid_non_regression(monkeypatch):
    """非退行確認: 同じ合成データで NaN を注入しなければ従来どおり pass する
    （grip_ratio/direction_consistency の閾値ロジック自体は不変）。"""
    probe_names = list(gc._PROBE_MIDIS.keys())
    n_probe = len(probe_names)
    sweep_values = [0.0, 0.1, 0.2, 0.35, 0.5]

    _patch_grip_axis_lightweight(monkeypatch, n_probe, None)

    genome_base = rs.voice_a()
    result = gc._grip_axis(genome_base, "breathiness", sweep_values, "periodicity")

    assert result["non_finite_cells"] == []
    assert result["pass"] is True
