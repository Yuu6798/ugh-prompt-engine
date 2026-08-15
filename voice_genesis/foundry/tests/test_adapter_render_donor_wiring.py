"""test_adapter_render_donor_wiring.py — 追補 F1.2-D 配線の単体検証。
録音済み子音前置の決定論・母音分布ヘルパーを合成 fixture で検証する
（実 render パイプライン全体（WORLD 合成込み）は非依存・軽量）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapter"))

import numpy as np

import donor_bank_utau as dbu
import render as rd
import units as un


def _resolved(n_frames: int, n_bins: int = 8, fill: float = 1.0) -> un.ResolvedSegment:
    sp = np.full((n_frames, n_bins), fill, dtype=np.float64)
    ap = np.full((n_frames, n_bins), 0.1, dtype=np.float64)
    return un.ResolvedSegment(
        note_index=0, sp=sp, ap=ap, n_frames=n_frames, true_ratio=1.0, applied_ratio=1.0,
        cap_mode="none", n_loop_cycles=0,
    )


def _clip(n_frames: int, n_bins: int = 8, fill: float = 9.0) -> dbu.ConsonantClip:
    sp = np.full((n_frames, n_bins), fill, dtype=np.float64)
    ap = np.full((n_frames, n_bins), 0.9, dtype=np.float64)
    return dbu.ConsonantClip(
        onset="k", sp=sp, ap=ap, n_frames=n_frames, source_wav="w.wav", source_alias="- かA3",
        is_phrase_initial=True,
    )


def test_prepend_recorded_consonant_concatenates_in_order() -> None:
    resolved = _resolved(n_frames=10, fill=1.0)
    clip = _clip(n_frames=4, fill=9.0)
    out, event = rd.prepend_recorded_consonant(resolved, clip, note_index=3)

    assert out.n_frames == 14
    assert out.sp.shape == (14, 8)
    # クリップが先頭、母音核が後ろ（時系列順）。
    assert np.all(out.sp[:4] == 9.0)
    assert np.all(out.sp[4:] == 1.0)
    assert np.all(out.ap[:4] == 0.9)
    assert np.all(out.ap[4:] == 0.1)

    assert event.note_index == 3
    assert event.onset == "k"
    assert event.consonant_class == "recorded"
    assert event.n_frames_processed == 4


def test_prepend_recorded_consonant_is_deterministic() -> None:
    resolved = _resolved(n_frames=6)
    clip = _clip(n_frames=3)
    out1, ev1 = rd.prepend_recorded_consonant(resolved, clip, note_index=0)
    out2, ev2 = rd.prepend_recorded_consonant(resolved, clip, note_index=0)
    assert np.array_equal(out1.sp, out2.sp)
    assert np.array_equal(out1.ap, out2.ap)
    assert ev1 == ev2


def test_prepend_recorded_consonant_does_not_mutate_inputs() -> None:
    resolved = _resolved(n_frames=5, fill=2.0)
    clip = _clip(n_frames=2, fill=7.0)
    sp_before = resolved.sp.copy()
    rd.prepend_recorded_consonant(resolved, clip, note_index=0)
    assert np.array_equal(resolved.sp, sp_before)


def test_vowel_distribution_from_labels() -> None:
    labels = {0: "a", 1: "a", 2: "i", 3: "N"}
    dist = rd._vowel_distribution_from_labels(labels)
    assert dist == {"a": 2, "i": 1, "N": 1}


def test_vowel_distribution_from_labels_empty() -> None:
    assert rd._vowel_distribution_from_labels({}) == {}
