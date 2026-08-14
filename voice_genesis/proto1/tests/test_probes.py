"""test_probes.py — P2 (VG-002) の受け入れテスト: fixture 定義・manifest・hash 決定論性。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import genome as g
import probes


@pytest.fixture(scope="module")
def sample_genome():
    return g.build_genome("probe-test")


def test_probe_definitions_match_memo_spec():
    assert set(probes.PROBE_NAMES) == {"sustain", "register_sweep", "vibrato", "phrase", "cross_range"}

    sustain = probes.PROBE_DEFINITIONS["sustain"]
    assert sustain.notes_midi == (48.0, 69.0, 84.0)  # C3, A4, C6
    assert sustain.durations_sec == (1.5, 1.5, 1.5)

    sweep = probes.PROBE_DEFINITIONS["register_sweep"]
    assert sweep.notes_midi[0] == 45.0
    assert sweep.notes_midi[-1] == 90.0
    assert len(sweep.notes_midi) == 46
    assert all(d == 0.25 for d in sweep.durations_sec)

    vibrato = probes.PROBE_DEFINITIONS["vibrato"]
    assert vibrato.notes_midi == (69.0,)
    assert vibrato.durations_sec == (3.0,)

    phrase = probes.PROBE_DEFINITIONS["phrase"]
    assert phrase.notes_midi == (60.0, 62.0, 64.0, 67.0, 64.0, 62.0, 60.0, 60.0)
    assert all(d == 0.5 for d in phrase.durations_sec)

    cross = probes.PROBE_DEFINITIONS["cross_range"]
    assert cross.notes_midi == (48.0, 84.0)


def test_render_probe_returns_one_waveform_per_note(sample_genome):
    result = probes.render_probe(sample_genome, "phrase")
    assert len(result.waveforms) == len(result.notes_midi) == 8
    for wave, dur in zip(result.waveforms, result.durations_sec):
        expected_n = int(round(dur * probes.SR))
        assert len(wave) == expected_n


def test_render_probe_unknown_name_raises(sample_genome):
    with pytest.raises(KeyError):
        probes.render_probe(sample_genome, "no_such_probe")


def test_manifest_is_deterministic_for_same_genome(sample_genome):
    m1 = probes.full_manifest(sample_genome)
    m2 = probes.full_manifest(sample_genome)
    assert m1 == m2
    for probe_name in probes.PROBE_NAMES:
        assert m1["probes"][probe_name]["combined_waveform_sha256"] == m2["probes"][probe_name]["combined_waveform_sha256"]


def test_manifest_differs_for_different_genomes():
    g1 = g.build_genome("hash-a", source=g.SourceSection(tilt=-9.0))
    g2 = g.build_genome("hash-b", source=g.SourceSection(tilt=-11.0))
    m1 = probes.probe_manifest(g1, "sustain")
    m2 = probes.probe_manifest(g2, "sustain")
    assert m1["combined_waveform_sha256"] != m2["combined_waveform_sha256"]


def test_note_hashes_match_recomputed_waveform_hash(sample_genome):
    from hashing import sha256_of_waveform

    result = probes.render_probe(sample_genome, "sustain")
    manifest = probes.probe_manifest(sample_genome, "sustain")
    for wave, expected_hash in zip(result.waveforms, manifest["note_waveform_sha256"]):
        assert sha256_of_waveform(wave) == expected_hash


def test_full_manifest_covers_all_probes(sample_genome):
    manifest = probes.full_manifest(sample_genome)
    assert set(manifest["probes"].keys()) == set(probes.PROBE_NAMES)
