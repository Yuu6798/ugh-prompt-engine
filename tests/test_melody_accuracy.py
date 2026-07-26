"""tests/test_melody_accuracy.py — M2a `svp_rpe.melody.accuracy` の単体テスト。

対象: `docs/DESIGN_M2_extraction_accuracy.md`（M2a 行）。

1. `evaluate_melody_accuracy` が既知の小さな f0 系列に対して手計算値（RPA/RCA/
   VR/VFA）と一致するか（mir_eval 自体は再実装しない——ここでは mir_eval の
   出力を直接検証し、本モジュールのラッパがそれをそのまま転記しているかを
   確認する）。
2. mir_eval 外で追加算出する「有声かつ chroma 一致フレームの絶対 cent 誤差の
   中央値」の手計算一致。
3. spec → 10ms hop f0 系列の決定論導出（同一 spec → bit 一致）。
4. 正解の区間境界が `scripts/build_melody_bench.py` が実際にレンダする**整数標本
   境界**と一致すること（秒の累積和では量子化端数がドリフトし、境界フレームの
   有声/無声・音高ラベルが実波形とずれる）。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_melody_bench as builder  # noqa: E402

from svp_rpe.melody.accuracy import (  # noqa: E402
    DEFAULT_HOP_SEC,
    evaluate_melody_accuracy,
    midi_to_hz,
    monophonic_note_intervals,
    monophonic_note_sample_intervals,
    monophonic_total_duration_sec,
    monophonic_total_samples,
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
_TEST_SR = 22050

# builder の `_tone` / `_rest` と同一の式（`int(round(sample_rate * dur_sec))`）。
# 期待値を秒（0.3 / 0.05 / 0.6）から手計算で書き下すと、まさに本テストが守りたい
# 量子化端数を落としてしまうため、標本数はここでも同じ式で導出する。
_NOTE_N = int(round(_TEST_SR * 0.3))
_GAP_N = int(round(_TEST_SR * 0.05))
_PHRASE_N = int(round(_TEST_SR * 0.6))


def test_monophonic_note_sample_intervals_matches_builder_segment_order() -> None:
    intervals = monophonic_note_sample_intervals(_TEST_SPEC, sample_rate=_TEST_SR)
    # phrase 1: note60 [0,N), gap; note62 [N+G, 2N+G), gap; phrase_gap
    # phrase 2: note64 [2(N+G)+P, 2(N+G)+P+N), gap; phrase_gap
    assert len(intervals) == 3
    assert intervals[0] == (0, _NOTE_N, midi_to_hz(60))
    assert intervals[1] == (_NOTE_N + _GAP_N, 2 * _NOTE_N + _GAP_N, midi_to_hz(62))
    third_start = 2 * (_NOTE_N + _GAP_N) + _PHRASE_N
    assert intervals[2] == (third_start, third_start + _NOTE_N, midi_to_hz(64))


def test_monophonic_note_intervals_are_sample_boundaries_in_seconds() -> None:
    intervals = monophonic_note_intervals(_TEST_SPEC, sample_rate=_TEST_SR)
    expected = [
        (start / _TEST_SR, end / _TEST_SR, hz)
        for start, end, hz in monophonic_note_sample_intervals(_TEST_SPEC, sample_rate=_TEST_SR)
    ]
    assert intervals == expected


def test_monophonic_total_samples_matches_rendered_waveform_length() -> None:
    specs = {"sample_rate": _TEST_SR, "amplitude": 0.3, "fixtures": {"t": _TEST_SPEC}}
    y, sr = builder.build_signal("t", specs)
    assert sr == _TEST_SR
    assert len(y) == monophonic_total_samples(_TEST_SPEC, sample_rate=_TEST_SR)
    assert monophonic_total_duration_sec(_TEST_SPEC, sample_rate=_TEST_SR) == pytest.approx(
        len(y) / _TEST_SR
    )


def test_monophonic_note_sample_intervals_locate_rendered_tone_and_silence() -> None:
    """区間内は鳴っており、区間直後の note_gap 標本は厳密な無音である。"""
    specs = {"sample_rate": _TEST_SR, "amplitude": 0.3, "fixtures": {"t": _TEST_SPEC}}
    y, _sr = builder.build_signal("t", specs)
    for start, end, _hz in monophonic_note_sample_intervals(_TEST_SPEC, sample_rate=_TEST_SR):
        assert np.any(np.asarray(y[start:end]) != 0.0)
        assert np.all(np.asarray(y[end : end + _GAP_N]) == 0.0)


def test_reference_f0_from_monophonic_spec_is_deterministic_bit_exact() -> None:
    first_times, first_freqs = reference_f0_from_monophonic_spec(
        _TEST_SPEC, sample_rate=_TEST_SR
    )
    second_times, second_freqs = reference_f0_from_monophonic_spec(
        _TEST_SPEC, sample_rate=_TEST_SR
    )
    assert first_times == second_times
    assert first_freqs == second_freqs


def test_reference_f0_from_monophonic_spec_frame_grid() -> None:
    times, freqs = reference_f0_from_monophonic_spec(
        _TEST_SPEC, sample_rate=_TEST_SR, hop_sec=DEFAULT_HOP_SEC
    )
    # フレーム数 = 標本位置が総標本数の内側にある時刻の個数（ceiling。round は
    # 端数下半分の尾を落とす）。hop は 10 進表現（1/100 秒）で厳密に数える。
    from fractions import Fraction

    total = monophonic_total_samples(_TEST_SPEC, sample_rate=_TEST_SR)
    spf = Fraction(str(DEFAULT_HOP_SEC)) * _TEST_SR
    expected_n_frames = math.ceil(Fraction(total) / spf)
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
        reference_f0_from_monophonic_spec(
            {"kind": "chord_pad", "duration_sec": 1.0, "chords": [[60]]},
            sample_rate=_TEST_SR,
        )


def test_reference_f0_from_monophonic_spec_rejects_nonpositive_sample_rate() -> None:
    with pytest.raises(ValueError, match="sample_rate must be positive"):
        reference_f0_from_monophonic_spec(_TEST_SPEC, sample_rate=0)


# ---------------------------------------------------------------------------
# 量子化ドリフト回帰（Codex 指摘）: セグメント長が整数標本にならない spec では、
# 秒を累積する実装の区間境界が実波形の標本境界とずれ、境界フレームのラベルが
# 実際にレンダされた音と食い違う。
# ---------------------------------------------------------------------------

# 1000Hz で note_dur 0.0105s = 10.5 標本 → builder は int(round(...)) = 10 標本を
# レンダする（= 0.010s）。秒累積の実装では 1 番目のノートが [0, 0.0105) まで伸び、
# t=0.01 のフレームを 60 番と誤ラベルするが、実波形ではその標本は既に 2 番目の
# ノート（62 番）である。
_QUANTIZATION_SPEC = {
    "kind": "monophonic",
    "note_dur_sec": 0.0105,
    "phrases": [[60, 62]],
}
_QUANTIZATION_SR = 1000


def test_reference_labels_follow_rendered_sample_boundaries_not_accumulated_seconds() -> None:
    specs = {
        "sample_rate": _QUANTIZATION_SR,
        "amplitude": 0.3,
        "fixtures": {"q": _QUANTIZATION_SPEC},
    }
    y, sr = builder.build_signal("q", specs)
    # builder は各ノートを 10 標本（0.0105s 相当の 10.5 標本ではない）でレンダする。
    assert (sr, len(y)) == (_QUANTIZATION_SR, 20)

    intervals = monophonic_note_sample_intervals(
        _QUANTIZATION_SPEC, sample_rate=_QUANTIZATION_SR
    )
    assert [(start, end) for start, end, _hz in intervals] == [(0, 10), (10, 20)]

    _times, freqs = reference_f0_from_monophonic_spec(
        _QUANTIZATION_SPEC, sample_rate=_QUANTIZATION_SR, hop_sec=0.01
    )
    assert len(freqs) == 2
    assert freqs[0] == pytest.approx(midi_to_hz(60))
    # 秒累積の実装ならここが midi 60 になる（実波形は既に 62 番）。
    assert freqs[1] == pytest.approx(midi_to_hz(62))


def test_midi_to_hz_a4_is_440() -> None:
    assert midi_to_hz(69.0) == pytest.approx(440.0)
    assert midi_to_hz(81.0) == pytest.approx(880.0)  # +1 octave
    assert not math.isnan(midi_to_hz(60.0))


# ---------------------------------------------------------------------------
# 第 19 巡: フレームグリッドの ceiling / hop の 10 進厳密表現（Codex P2×2）
# ---------------------------------------------------------------------------


def test_reference_grid_covers_the_waveform_tail_with_ceiling() -> None:
    """14ms fixture × 10ms hop → 2 フレーム（round だと t=0.01 の尾が落ちる）。"""
    spec = {"kind": "monophonic", "note_dur_sec": 0.014, "phrases": [[60]]}
    times, freqs = reference_f0_from_monophonic_spec(spec, sample_rate=1000, hop_sec=0.01)
    assert len(times) == 2  # round(1.4)=1 だった
    assert times == (0.0, 0.01)
    assert freqs[0] == pytest.approx(midi_to_hz(60))
    assert freqs[1] == pytest.approx(midi_to_hz(60))  # 標本 10 < 14 なので有声


def test_reference_grid_excludes_position_exactly_at_duration() -> None:
    """総標本数ちょうどの位置のフレームは含めない（position < total の半開区間）。"""
    spec = {"kind": "monophonic", "note_dur_sec": 0.02, "phrases": [[60]]}
    times, _freqs = reference_f0_from_monophonic_spec(spec, sample_rate=1000, hop_sec=0.01)
    # 20 標本、hop=10 標本 → 位置 0, 10 のみ（位置 20 は範囲外）。
    assert times == (0.0, 0.01)


def test_hop_uses_decimal_representation_not_binary_float() -> None:
    """hop 0.03 × 100Hz: フレーム 1 は厳密に標本 3 = 次ノートの先頭（Codex 指摘の例）。

    `Fraction(0.03)` は binary float の近似（2.9999...）を厳密化してしまい、
    フレーム 1 が直前ノート（標本 [0,3)）へ誤帰属していた。
    """
    spec = {"kind": "monophonic", "note_dur_sec": 0.03, "phrases": [[60, 62]]}
    times, freqs = reference_f0_from_monophonic_spec(spec, sample_rate=100, hop_sec=0.03)
    assert len(freqs) == 2
    assert freqs[0] == pytest.approx(midi_to_hz(60))
    assert freqs[1] == pytest.approx(midi_to_hz(62))  # 標本 3 = 2 番目のノートの先頭
