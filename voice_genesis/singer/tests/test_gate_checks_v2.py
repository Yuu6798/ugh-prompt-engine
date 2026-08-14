"""test_gate_checks_v2.py — PR#261 レビュー R23: gate6-v2 の grip 判定に
sweep×probe グリッドの全セル有限性を PASS の前提条件として追加したことの検証。

`_grip_axis_v2()` は曲全体の音声合成を要するため重いが、`render_sustained_vowel`
と `_quick_features_scored` を monkeypatch して軽量に検証する（合成音声なし）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import gate_checks_v2 as gc2
import render_song as rs


def _patch_lightweight(monkeypatch, n_probe, nan_at):
    """render_sustained_vowel/_quick_features_scored をダミー化する。

    intended feature("periodicity")は si に比例した値を全 probe 共通で返し、
    side features は 0 固定（=grip 判定が閾値を満たすよう仕込む）。
    `nan_at`（(sweep_index, probe_index) タプル、または None）に該当する
    セルだけ全特徴を NaN にする。
    """
    monkeypatch.setattr(gc2.rs, "render_sustained_vowel", lambda g, midi, vowel="a", duration_sec=1.2: np.zeros(4))

    counter = {"n": 0}

    def fake_quick_features_scored(genome_base, midi, wav, sr):
        idx = counter["n"]
        counter["n"] += 1
        si, pi = divmod(idx, n_probe)
        if nan_at is not None and (si, pi) == nan_at:
            feats = {"mean_f0": float("nan"), "periodicity": float("nan"), "rms": float("nan"), "vibrato_depth": float("nan")}
        else:
            feats = {"mean_f0": 0.0, "periodicity": 10.0 * si, "rms": 0.0, "vibrato_depth": 0.0}
        return feats, 0.0

    monkeypatch.setattr(gc2, "_quick_features_scored", fake_quick_features_scored)


def test_grip_axis_v2_fails_when_a_probe_cell_is_non_finite(monkeypatch):
    """`gate_checks.py::_grip_axis` と同型の穴が gate6-v2 側にも存在した
    (PR#261 R23)。sweep×probe グリッドの 1 セルが非有限でも、他 probe が
    同一値であれば `np.nanmedian` は中央値を変えず旧実装は検出できなかった。
    """
    probe_names = list(gc2.gc._PROBE_MIDIS.keys())
    n_probe = len(probe_names)
    sweep_values = [0.0, 0.1, 0.2, 0.35, 0.5]
    n_sweep = len(sweep_values)
    nan_at = (n_sweep - 1, 2)

    _patch_lightweight(monkeypatch, n_probe, nan_at)

    genome_base = rs.voice_a()
    result = gc2._grip_axis_v2(genome_base, "breathiness", sweep_values, "periodicity")

    assert result["non_finite_cells"], "注入した非有限セルが検出されていない"
    assert any(c["sweep_index"] == nan_at[0] and c["probe"] == probe_names[nan_at[1]] for c in result["non_finite_cells"])
    assert result["pass"] is False


def test_grip_axis_v2_passes_for_all_finite_grid_non_regression(monkeypatch):
    """非退行確認: 同じ合成データで NaN を注入しなければ従来どおり pass する。"""
    probe_names = list(gc2.gc._PROBE_MIDIS.keys())
    n_probe = len(probe_names)
    sweep_values = [0.0, 0.1, 0.2, 0.35, 0.5]

    _patch_lightweight(monkeypatch, n_probe, None)

    genome_base = rs.voice_a()
    result = gc2._grip_axis_v2(genome_base, "breathiness", sweep_values, "periodicity")

    assert result["non_finite_cells"] == []
    assert result["pass"] is True
