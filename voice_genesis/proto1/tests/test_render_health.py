"""test_render_health.py — P6 の受け入れテスト: aliasing / register transition / formant sweep。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataclasses import replace

import numpy as np

import genome as g
import probes
import render_health as rh
import sampler


def test_aliasing_below_threshold_for_default_genome():
    gen = g.build_genome("aliasing-default")
    report = rh.check_aliasing(gen)
    assert report.passed is True
    assert report.high_band_energy_ratio_db < rh.ALIASING_THRESHOLD_DB


def test_aliasing_holds_across_several_sampled_genomes():
    failures = []
    for seed in range(10):
        gen = sampler.sample(seed)
        report = rh.check_aliasing(gen)
        if not report.passed:
            failures.append((seed, report.high_band_energy_ratio_db))
    assert not failures, f"aliasing 超過: {failures}"


def test_aliasing_at_high_pitch_c6():
    gen = g.build_genome("aliasing-c6")
    report = rh.check_aliasing(gen, midi=84.0)  # C6: 高音域ほど倍音が Nyquist に近づく
    assert report.passed is True


def test_register_transition_continuity_for_default_genome():
    gen = g.build_genome("register-default")
    report = rh.register_transition_report(gen)
    assert len(report.note_rms_db) == 46
    assert len(report.adjacent_db_jumps) == 45
    assert report.passed is True
    assert report.max_db_jump <= rh.REGISTER_TRANSITION_MAX_DB
    # PR#261 レビュー R38: 非退行確認。実測数が期待数（46）と一致すること。
    assert report.expected_note_count == 46
    assert report.actual_note_count == 46


def test_register_transition_continuity_across_sampled_genomes():
    failures = []
    for seed in range(10):
        gen = sampler.sample(seed)
        report = rh.register_transition_report(gen)
        if not report.passed:
            failures.append((seed, report.max_db_jump))
    assert not failures, f"register transition 不連続: {failures}"


# --- PR#261 レビュー R38: register_transition_report() の期待数照合 --------


def test_register_transition_report_fails_closed_when_zero_waveforms_returned(monkeypatch):
    """probes.render_probe() が waveforms=[] を返す退行を注入する。旧実装は
    `jumps=[]` → `max_jump=0.0`（`<=` 閾値内）で無条件 PASS してしまっていたが、
    R38 により実測数(0) != 期待数(46) で測定不能 FAIL になることを確認する。
    """
    gen = g.build_genome("register-zero")
    real_render_probe = probes.render_probe

    def _fake_render_probe(genome, probe_name):
        result = real_render_probe(genome, probe_name)
        if probe_name == "register_sweep":
            return replace(result, waveforms=[])
        return result

    monkeypatch.setattr(rh.probes, "render_probe", _fake_render_probe)

    report = rh.register_transition_report(gen)
    assert report.passed is False
    assert report.expected_note_count == 46
    assert report.actual_note_count == 0
    assert report.note_rms_db == []
    assert report.adjacent_db_jumps == []


def test_register_transition_report_fails_closed_when_one_waveform_returned(monkeypatch):
    """1 本のみ返る退行でも同様に測定不能 FAIL になることを確認する（1 本しか
    無ければ `adjacent_db_jumps` が空になり `max_jump=0.0` の偽 PASS が起き得た）。
    """
    gen = g.build_genome("register-one")
    real_render_probe = probes.render_probe

    def _fake_render_probe(genome, probe_name):
        result = real_render_probe(genome, probe_name)
        if probe_name == "register_sweep":
            return replace(result, waveforms=result.waveforms[:1])
        return result

    monkeypatch.setattr(rh.probes, "render_probe", _fake_render_probe)

    report = rh.register_transition_report(gen)
    assert report.passed is False
    assert report.expected_note_count == 46
    assert report.actual_note_count == 1


def test_formant_sweep_direction_consistency_for_default_genome():
    gen = g.build_genome("formant-default")
    report = rh.formant_sweep_report(gen)
    assert len(report.formant_scales) == len(rh.FORMANT_SWEEP_SCALES)
    assert report.direction_consistency >= rh.DIRECTION_CONSISTENCY_THRESHOLD
    assert report.passed is True


def test_formant_sweep_report_is_deterministic():
    gen = g.build_genome("formant-det")
    a = rh.formant_sweep_report(gen)
    b = rh.formant_sweep_report(gen)
    assert a.formant_centroid_log2hz == b.formant_centroid_log2hz


def test_formant_sweep_report_non_finite_scales_empty_on_default_genome_non_regression():
    """非退行確認: 既存正本と同様のデフォルト genome では非有限セントロイドは
    発生せず、`non_finite_scales` は空のまま従来どおり pass する（PR#261 R31）。
    """
    gen = g.build_genome("formant-non-regression")
    report = rh.formant_sweep_report(gen)
    assert report.non_finite_scales == []
    assert report.passed is True


# --- PR#261 レビュー R31: 非有限セントロイドの fail-closed 化 ---------------


def test_formant_sweep_report_fails_closed_on_non_finite_centroid(monkeypatch):
    """中央 1 点（7 点中の 4 番目、scale=1.0）の centroid 測定が NaN に
    なった場合、旧実装は隣接差を `1.0 if d >= 0.0 else 0.0` で二値化して
    いたため「方向が逆だった 1 ステップ」として黙って 0 加算し、残り 6 点が
    健全であれば direction_consistency が閾値を上回って測定不能なまま
    PASS し得た（gate_checks.py の R23 と同型の穴）。非有限を検出力に
    組み込んだことで無条件 FAIL することを確認する。
    """
    gen = g.build_genome("formant-nan-injection")
    scales = rh.FORMANT_SWEEP_SCALES
    nan_index = len(scales) // 2  # 中央 1 点

    monkeypatch.setattr(rh.bridge, "render_note", lambda genome, midi, duration_sec=1.0: np.zeros(4))
    monkeypatch.setattr(rh.mv3, "estimate_f0_v3", lambda sig, sr: 220.0)

    call_counter = {"n": 0}

    def fake_formant_centroid_and_f1(sig, sr, f0):
        idx = call_counter["n"]
        call_counter["n"] += 1
        if idx == nan_index:
            return {"formant_centroid_log2hz": float("nan")}
        return {"formant_centroid_log2hz": 10.0 + 0.1 * idx}  # 単調増加のダミー値（健全な点）

    monkeypatch.setattr(rh.mv3, "formant_centroid_and_f1", fake_formant_centroid_and_f1)

    report = rh.formant_sweep_report(gen)

    assert report.passed is False
    assert report.non_finite_scales == [scales[nan_index]]
