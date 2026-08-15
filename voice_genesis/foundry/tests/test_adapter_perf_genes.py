"""test_adapter_perf_genes.py — perf_genes の位相リセットと seed 再現の検証。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapter"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "singer"))

import numpy as np
import pytest

import performance as perf
import phoneme_jp as pj
import score as sc

import perf_genes as pg

SR = 24000


def _make_two_note_segments(dur1_sec: float = 0.8, dur2_sec: float = 0.8):
    mora = pj.Mora(kana="ら", onset="r", vowel="a")
    note1 = sc.ScoreNote(midi=69.0, duration_beats=1.0, mora=mora, phrase_index=0, is_phrase_final=False)
    note2 = sc.ScoreNote(midi=69.0, duration_beats=1.0, mora=mora, phrase_index=0, is_phrase_final=True)
    n1 = int(round(dur1_sec * SR))
    n2 = int(round(dur2_sec * SR))
    seg1 = perf.TimelineSegment(note=note1, start_sample=0, end_sample=n1, is_phrase_first=True, is_phrase_last=False)
    seg2 = perf.TimelineSegment(note=note2, start_sample=n1, end_sample=n1 + n2, is_phrase_first=False, is_phrase_last=True)
    return [seg1, seg2], n1 + n2


_DEFAULT_PERF_SPEC = dict(
    onset_glide=dict(depth_cents=-80.0, time_ms=60.0),
    vibrato=dict(rate_hz=5.0, depth_cents=25.0, onset_ms=150.0, phase_reset_per_note=True),
    drift=dict(depth_cents=10.0, rate_hz=0.4),
    jitter_cents=3.0,
    portamento_ms=55.0,
)


def test_build_perf_f0_deterministic_same_seed() -> None:
    segments, total_samples = _make_two_note_segments()
    f0_a = pg.build_perf_f0(segments, total_samples, SR, _DEFAULT_PERF_SPEC, seed=11)
    f0_b = pg.build_perf_f0(segments, total_samples, SR, _DEFAULT_PERF_SPEC, seed=11)
    assert np.array_equal(f0_a, f0_b)


def test_build_perf_f0_differs_with_different_seed() -> None:
    segments, total_samples = _make_two_note_segments()
    f0_a = pg.build_perf_f0(segments, total_samples, SR, _DEFAULT_PERF_SPEC, seed=11)
    f0_b = pg.build_perf_f0(segments, total_samples, SR, _DEFAULT_PERF_SPEC, seed=97)
    assert not np.array_equal(f0_a, f0_b)


def test_build_perf_f0_unvoiced_stays_zero() -> None:
    # ブレスを模擬: セグメント間に無音ギャップを残す (total_samples > 末尾セグメント終端)
    segments, total_samples = _make_two_note_segments()
    total_samples_with_gap = total_samples + int(0.25 * SR)
    f0 = pg.build_perf_f0(segments, total_samples_with_gap, SR, _DEFAULT_PERF_SPEC, seed=11)
    assert np.all(f0[total_samples:] == 0.0)


def test_vibrato_phase_resets_per_note() -> None:
    """2 ノートが同一 pitch/duration なら、ノート先頭からの位相は各ノートで
    リセットされるため、vibrato onset 後の cents 変調パターンは 2 ノートで
    同一になるはず（連続位相なら 1 ノート目終端の位相を引き継ぎズレるため
    異なるパターンになる）。"""
    segments, total_samples = _make_two_note_segments(dur1_sec=1.0, dur2_sec=1.0)
    spec = dict(_DEFAULT_PERF_SPEC)
    spec["onset_glide"] = dict(depth_cents=0.0, time_ms=1.0)  # glide を無効化して vibrato のみ観測
    spec["drift"] = dict(depth_cents=0.0, rate_hz=0.4)
    spec["jitter_cents"] = 0.0

    cents1 = pg._vibrato_cents_phase_reset(
        segments, total_samples, SR, rate_hz=5.0, depth_cents=25.0, onset_ms=150.0
    )
    seg1_cents = cents1[segments[0].start_sample:segments[0].end_sample]
    seg2_cents = cents1[segments[1].start_sample:segments[1].end_sample]
    assert np.allclose(seg1_cents, seg2_cents)


def test_onset_glide_shape() -> None:
    segments, total_samples = _make_two_note_segments(dur1_sec=1.0, dur2_sec=1.0)
    cents = pg._onset_glide_cents(segments, total_samples, SR, depth_cents=-80.0, time_ms=60.0)
    seg = segments[0]
    assert cents[seg.start_sample] == pytest.approx(-80.0)
    ramp_len = int(round(SR * 60.0 / 1000.0))
    assert cents[seg.start_sample + ramp_len - 1] == pytest.approx(0.0, abs=2.0)
    assert cents[seg.start_sample + ramp_len + 100] == 0.0


def test_iid_block_walk_cents_seed_reproducible_and_bounded() -> None:
    n = SR * 2
    w1 = pg._iid_block_walk_cents(n, SR, block_sec=1.25, depth_cents=10.0, seed=5)
    w2 = pg._iid_block_walk_cents(n, SR, block_sec=1.25, depth_cents=10.0, seed=5)
    assert np.array_equal(w1, w2)
    assert np.max(np.abs(w1)) <= 10.0 + 1e-9
    w3 = pg._iid_block_walk_cents(n, SR, block_sec=1.25, depth_cents=10.0, seed=6)
    assert not np.array_equal(w1, w3)


def test_iid_block_walk_cents_zero_depth_is_zero() -> None:
    n = 1000
    w = pg._iid_block_walk_cents(n, SR, block_sec=0.01, depth_cents=0.0, seed=1)
    assert np.all(w == 0.0)
