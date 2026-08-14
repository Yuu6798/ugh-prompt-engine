"""test_render_health.py — P6 の受け入れテスト: aliasing / register transition / formant sweep。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import genome as g
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


def test_register_transition_continuity_across_sampled_genomes():
    failures = []
    for seed in range(10):
        gen = sampler.sample(seed)
        report = rh.register_transition_report(gen)
        if not report.passed:
            failures.append((seed, report.max_db_jump))
    assert not failures, f"register transition 不連続: {failures}"


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
