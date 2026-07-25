"""tests/test_melody_accuracy.py — M2a `svp_rpe.melody.accuracy` の単体テスト。

対象: `docs/DESIGN_M2_extraction_accuracy.md`（M2a 行）。

1. `evaluate_melody_accuracy` が既知の小さな f0 系列に対して手計算値（RPA/RCA/
   VR/VFA）と一致するか（mir_eval 自体は再実装しない——ここでは mir_eval の
   出力を直接検証し、本モジュールのラッパがそれをそのまま転記しているかを
   確認する）。
2. mir_eval 外で追加算出する「有声かつ chroma 一致フレームの絶対 cent 誤差の
   中央値」の手計算一致。
3. spec → 10ms hop f0 系列の決定論導出（同一 spec → bit 一致）。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from svp_rpe.melody.accuracy import (
    DEFAULT_HOP_SEC,
    evaluate_melody_accuracy,
    midi_to_hz,
    monophonic_note_intervals,
    monophonic_total_duration_sec,
    reference_f0_from_monophonic_spec,
)

# ---------------------------------------------------------------------------
# 手計算済みの既知フィクスチャ（本ファイルのテストと DESIGN 実装セッションの
# 事前検算で導出。5 フレーム、base_frequency=10Hz の mir_eval 既定値）:
#
#   times = [0.00, 0.01, 0.02, 0.03, 0.04]
#   ref Hz = [0(無声), 440(A4), 880(A5, +1oct), 220(A3, -1oct), 500]
#   est Hz は ref_cent に対して厳密な cent オフセットを与えて構成:
#     frame1: ref+30 cent   (ピッチ許容内の精度誤差)
#     frame2: ref-1200 cent (オクターブ誤り。chroma は一致・pitch は不一致)
#     frame3: ref+1220 cent (1oct+20cent。chroma は一致・pitch は不一致)
#     frame4: ref+800 cent  (chroma も不一致の完全ミス)
#
# 手計算:
#   ref_voicing = [0,1,1,1,1]（frame0 のみ無声。有声フレーム数 = 4）
#   RPA = 正しい pitch (|diff|<50cent) のフレーム数 / 有声フレーム数
#       = 1 (frame1 のみ) / 4 = 0.25
#   RCA = chroma 一致 (octave 補正後 |diff|<50cent) のフレーム数 / 有声フレーム数
#       = 3 (frame1,2,3) / 4 = 0.75
#   VR  = 1.0 (est も frame1-4 で有声と判定, ref 有声 4/4 一致)
#   VFA = 0.0 (ref 無声は frame0 のみ、est も frame0 で無声)
#   median cent error (有声 & chroma 一致の残差): frame1=30, frame2=0, frame3=20
#       → 中央値 = 20.0（3 件）
# ---------------------------------------------------------------------------
_TIMES = [0.00, 0.01, 0.02, 0.03, 0.04]
_REF_HZ = [0.0, 440.0, 880.0, 220.0, 500.0]
_EST_HZ = [0.0, 447.69106453411395, 440.0, 445.1125537290349, 793.7005259840998]


def test_evaluate_melody_accuracy_matches_hand_calculation() -> None:
    result = evaluate_melody_accuracy(_TIMES, _REF_HZ, _TIMES, _EST_HZ, tolerance_cents=50.0)

    assert result.raw_pitch_accuracy == pytest.approx(0.25, abs=1e-9)
    assert result.raw_chroma_accuracy == pytest.approx(0.75, abs=1e-9)
    assert result.octave_gap == pytest.approx(0.75 - 0.25, abs=1e-9)
    assert result.voicing_recall == pytest.approx(1.0, abs=1e-9)
    assert result.voicing_false_alarm == pytest.approx(0.0, abs=1e-9)
    # OA は mir_eval の合成値をそのまま転記するのみ（本テストでは参考記録として
    # mir_eval 自身の出力と一致することだけを確認し、独自の合否判定はしない）。
    import mir_eval.melody as mir_melody

    scores = mir_melody.evaluate(
        np.asarray(_TIMES), np.asarray(_REF_HZ), np.asarray(_TIMES), np.asarray(_EST_HZ),
        cent_tolerance=50.0,
    )
    assert result.overall_accuracy == pytest.approx(scores["Overall Accuracy"], abs=1e-9)


def test_median_cent_error_matches_hand_calculation() -> None:
    result = evaluate_melody_accuracy(_TIMES, _REF_HZ, _TIMES, _EST_HZ, tolerance_cents=50.0)

    assert result.voiced_chroma_correct_frame_count == 3
    assert result.median_cent_error == pytest.approx(20.0, abs=1e-6)


def test_median_cent_error_is_none_when_no_chroma_correct_frames() -> None:
    # 全フレームが chroma 不一致（500 cent オフセット）になるよう仕組む。
    times = [0.0, 0.01]
    ref_hz = [440.0, 440.0]
    est_hz_500_cent_off = 440.0 * (2.0 ** (500.0 / 1200.0))
    est_hz = [est_hz_500_cent_off, est_hz_500_cent_off]
    result = evaluate_melody_accuracy(times, ref_hz, times, est_hz, tolerance_cents=50.0)
    assert result.voiced_chroma_correct_frame_count == 0
    assert result.median_cent_error is None


def test_evaluate_melody_accuracy_rejects_length_mismatch() -> None:
    with pytest.raises(ValueError, match="same length"):
        evaluate_melody_accuracy([0.0, 0.01], [440.0], [0.0], [440.0])


def test_evaluate_melody_accuracy_rejects_empty_reference() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        evaluate_melody_accuracy([], [], [0.0], [440.0])


# ---------------------------------------------------------------------------
# spec → 10ms hop f0 系列の決定論導出
# ---------------------------------------------------------------------------

_TEST_SPEC = {
    "kind": "monophonic",
    "note_dur_sec": 0.3,
    "note_gap_sec": 0.05,
    "phrase_gap_sec": 0.6,
    "phrases": [[60, 62], [64]],
}


def test_monophonic_note_intervals_matches_builder_segment_order() -> None:
    intervals = monophonic_note_intervals(_TEST_SPEC)
    # phrase 1: note60 [0,0.3), gap→0.35; note62 [0.35,0.65), gap→0.70; phrase_gap→1.30
    # phrase 2: note64 [1.30,1.60), gap→1.65; phrase_gap→2.25
    assert len(intervals) == 3
    assert intervals[0] == pytest.approx((0.0, 0.3, midi_to_hz(60)))
    assert intervals[1] == pytest.approx((0.35, 0.65, midi_to_hz(62)))
    assert intervals[2] == pytest.approx((1.30, 1.60, midi_to_hz(64)))


def test_monophonic_total_duration_matches_note_intervals_tail() -> None:
    duration = monophonic_total_duration_sec(_TEST_SPEC)
    assert duration == pytest.approx(2.25)


def test_reference_f0_from_monophonic_spec_is_deterministic_bit_exact() -> None:
    first_times, first_freqs = reference_f0_from_monophonic_spec(_TEST_SPEC)
    second_times, second_freqs = reference_f0_from_monophonic_spec(_TEST_SPEC)
    assert first_times == second_times
    assert first_freqs == second_freqs


def test_reference_f0_from_monophonic_spec_frame_grid() -> None:
    times, freqs = reference_f0_from_monophonic_spec(_TEST_SPEC, hop_sec=DEFAULT_HOP_SEC)
    duration = monophonic_total_duration_sec(_TEST_SPEC)
    expected_n_frames = int(round(duration / DEFAULT_HOP_SEC))
    assert len(times) == expected_n_frames == len(freqs)
    assert times[0] == 0.0
    assert times[1] == pytest.approx(DEFAULT_HOP_SEC)

    # frame at t=0.10 (index 10) は note60 の区間内 [0, 0.3) → 有声・A4未満(60番)
    idx_in_note0 = int(round(0.10 / DEFAULT_HOP_SEC))
    assert freqs[idx_in_note0] == pytest.approx(midi_to_hz(60))

    # frame at t=0.32 は note_gap 区間 [0.3, 0.35) → 無声
    idx_in_gap = int(round(0.32 / DEFAULT_HOP_SEC))
    assert freqs[idx_in_gap] == 0.0

    # frame at t=1.0 は phrase_gap 区間（0.70〜1.30）内 → 無声
    idx_in_phrase_gap = int(round(1.0 / DEFAULT_HOP_SEC))
    assert freqs[idx_in_phrase_gap] == 0.0


def test_reference_f0_from_monophonic_spec_rejects_chord_pad() -> None:
    with pytest.raises(ValueError, match="monophonic"):
        reference_f0_from_monophonic_spec({"kind": "chord_pad", "duration_sec": 1.0, "chords": [[60]]})


def test_midi_to_hz_a4_is_440() -> None:
    assert midi_to_hz(69.0) == pytest.approx(440.0)
    assert midi_to_hz(81.0) == pytest.approx(880.0)  # +1 octave
    assert not math.isnan(midi_to_hz(60.0))
