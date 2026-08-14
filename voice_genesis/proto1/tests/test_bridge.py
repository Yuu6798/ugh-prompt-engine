"""test_bridge.py — genome.py ↔ harness Genome の橋渡し関数の健全性。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import bridge
import genome as g


def test_to_render_genome_maps_scalar_fields():
    gen = g.build_genome(
        "bridge-test",
        source=g.SourceSection(tilt=-12.5, source_mode="breathy"),
        noise=g.NoiseSection(breathiness_base=0.2),
        register=g.RegisterSection(boundaries_midi=(50.0, 60.0, 72.0, 85.0), transition_width=2.5),
        microprosody=g.MicroprosodySection(vibrato_rate_hz=6.0, vibrato_depth_cents=60.0, jitter_amount=0.01, jitter_seed=3),
        range_=g.RangeSection(lowest_midi=40.0, highest_midi=90.0),
    )
    r0 = bridge.to_render_genome(gen)

    assert r0.source.base_tilt_db_per_oct == -12.5
    assert r0.source.source_mode == "breathy"
    assert r0.noise.base_breathiness == 0.2
    assert r0.register.chest_to_mix_midi == 50.0
    assert r0.register.mix_to_head_midi == 60.0
    assert r0.register.head_to_falsetto_midi == 72.0
    assert r0.register.falsetto_to_whistle_midi == 85.0
    assert r0.register.transition_width == 2.5
    assert r0.microprosody.vibrato_rate_hz == 6.0
    assert r0.microprosody.vibrato_depth_cents == 60.0
    assert r0.microprosody.jitter_seed == 3
    assert r0.range.midi_min == 40.0
    assert r0.range.midi_max == 90.0


def test_jitter_amount_scales_to_jitter_pct_of_period():
    gen = g.build_genome("jitter-test", microprosody=g.MicroprosodySection(jitter_amount=0.01))
    r0 = bridge.to_render_genome(gen)
    assert r0.microprosody.jitter_pct_of_period == 0.01 * bridge.JITTER_BRIDGE_SCALE


def test_formant_offsets_apply_multiplicatively():
    defaults = bridge._vr0.ResonanceParams().base_formants_hz
    gen = g.build_genome(
        "formant-test",
        resonance=g.ResonanceSection(formant_scale=1.0, formant_offsets=(0.1, 0.0, -0.1, 0.0), bandwidth_scale=1.0),
    )
    r0 = bridge.to_render_genome(gen)
    assert r0.resonance.formant_scale == 1.0  # スケールは base_formants_hz に織り込み済み
    assert r0.resonance.base_formants_hz[0] == defaults[0] * 1.1
    assert r0.resonance.base_formants_hz[2] == defaults[2] * 0.9
    assert r0.resonance.base_formants_hz[1] == defaults[1]
    assert r0.resonance.base_formants_hz[3] == defaults[3]


def test_bandwidth_scale_applies_uniformly():
    defaults = bridge._vr0.ResonanceParams().bandwidths_hz
    gen = g.build_genome("bw-test", resonance=g.ResonanceSection(bandwidth_scale=1.5))
    r0 = bridge.to_render_genome(gen)
    for got, base in zip(r0.resonance.bandwidths_hz, defaults):
        assert got == base * 1.5


def test_render_note_is_deterministic():
    gen = g.build_genome("render-det")
    a = bridge.render_note(gen, 60, duration_sec=0.3)
    b = bridge.render_note(gen, 60, duration_sec=0.3)
    assert np.array_equal(a, b)


def test_render_note_returns_expected_length():
    gen = g.build_genome("render-len")
    sig = bridge.render_note(gen, 60, duration_sec=0.5)
    assert len(sig) == int(round(0.5 * bridge.SR))


def test_default_new_schema_genome_matches_voice_a_scalar_fields():
    """既定値の VoiceGenome v0.2 は voice_r0.voice_a() と実質等価な音を出すはず。"""
    gen = g.build_genome("equiv-voice-a")
    r0 = bridge.to_render_genome(gen)
    voice_a = bridge._vr0.voice_a()
    assert r0.source.base_tilt_db_per_oct == voice_a.source.base_tilt_db_per_oct
    assert r0.noise.base_breathiness == voice_a.noise.base_breathiness
    assert r0.register.chest_to_mix_midi == voice_a.register.chest_to_mix_midi
