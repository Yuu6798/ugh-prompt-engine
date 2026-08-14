"""test_sampler.py — P3 (VG-009) の受け入れテスト: 決定論性、境界フラグ、再現性。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import genome as g
import sampler


def test_sample_is_deterministic():
    a = sampler.sample(123)
    b = sampler.sample(123)
    assert a == b


def test_sample_different_seeds_differ():
    a = sampler.sample(1)
    b = sampler.sample(2)
    assert a != b


def test_sample_within_physio_range_by_construction():
    for seed in range(20):
        gen = sampler.sample(seed)
        assert gen.physio_range.out_of_physio_range is False, gen.physio_range.violated_bounds


def test_sample_register_boundaries_ascending():
    for seed in range(20):
        gen = sampler.sample(seed)
        b = gen.register.boundaries_midi
        assert all(b[i] < b[i + 1] for i in range(3))


def test_mutate_is_deterministic():
    base = sampler.sample(1)
    a = sampler.mutate(base, seed=42, scale=0.1)
    b = sampler.mutate(base, seed=42, scale=0.1)
    assert a == b


def test_mutate_different_seed_differs():
    base = sampler.sample(1)
    a = sampler.mutate(base, seed=1, scale=0.1)
    b = sampler.mutate(base, seed=2, scale=0.1)
    assert a != b


def test_mutate_small_scale_usually_stays_in_range():
    base = sampler.sample(1)
    out_of_range_count = 0
    for seed in range(30):
        mutated = sampler.mutate(base, seed=seed, scale=0.02)
        if mutated.physio_range.out_of_physio_range:
            out_of_range_count += 1
    # 小さい scale ならほぼ全て範囲内に収まるはず（縮退防止の緩いスモークチェック）。
    assert out_of_range_count <= 5


def test_mutate_large_scale_can_flag_out_of_range():
    base = sampler.sample(1)
    flagged = False
    for seed in range(10):
        mutated = sampler.mutate(base, seed=seed, scale=3.0)
        if mutated.physio_range.out_of_physio_range:
            flagged = True
            break
    assert flagged, "大きな scale での mutate は少なくとも 1 回は out_of_physio_range を立てるはず"


def test_mutate_does_not_reject_out_of_range_result():
    base = sampler.sample(1)
    mutated = sampler.mutate(base, seed=999, scale=10.0)
    assert isinstance(mutated, g.VoiceGenome)  # 例外を投げず構築できる


def test_mutate_preserves_boundary_ascending_order():
    base = sampler.sample(1)
    for seed in range(15):
        mutated = sampler.mutate(base, seed=seed, scale=0.5)
        b = mutated.register.boundaries_midi
        assert all(b[i] <= b[i + 1] for i in range(3))


def test_crossover_is_deterministic():
    a = sampler.sample(1)
    b = sampler.sample(2)
    c1 = sampler.crossover(a, b, seed=7)
    c2 = sampler.crossover(a, b, seed=7)
    assert c1 == c2


def test_crossover_fields_come_from_either_parent():
    a = sampler.sample(1)
    b = sampler.sample(2)
    child = sampler.crossover(a, b, seed=7)
    assert child.source.tilt in (a.source.tilt, b.source.tilt)
    assert child.source.source_mode in (a.source.source_mode, b.source.source_mode)
    assert child.resonance.formant_scale in (a.resonance.formant_scale, b.resonance.formant_scale)
    assert child.register.boundaries_midi in (a.register.boundaries_midi, b.register.boundaries_midi)


def test_crossover_different_seed_can_differ():
    a = sampler.sample(1)
    b = sampler.sample(2)
    children = {sampler.crossover(a, b, seed=s) for s in range(20)}
    assert len(children) > 1
