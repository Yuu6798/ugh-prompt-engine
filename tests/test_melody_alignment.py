"""tests/test_melody_alignment.py — melody/alignment.py（M3b）のテスト。

CI 安全（重依存なし・pytest -m "not slow" に含む）: ノート層アフィンギャップ NW
（Gotoh）・フレーズ層 NW・被覆信号の会計整合・決定論的 tie-break・縮退ケースを
検証する。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from svp_rpe.melody.alignment import align_intervals, align_melodies
from svp_rpe.melody.observability import MelodyNote
from svp_rpe.melody.representation import (
    AlignmentConfig,
    M3ComparisonConfig,
    build_sequences,
    load_m3_registry,
    split_phrases,
)

ROOT = Path(__file__).resolve().parents[1]
M3_REGISTRY_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m3_comparison_registry.yaml"


def _default_config() -> M3ComparisonConfig:
    config, _ = load_m3_registry(M3_REGISTRY_PATH)
    return config


def _with_alignment(config: M3ComparisonConfig, **overrides) -> M3ComparisonConfig:
    """`config.alignment` の一部フィールドだけ差し替えた新しい config を返す(テスト専用)。"""
    fields = {
        "match_score": config.alignment.match_score,
        "mismatch_score": config.alignment.mismatch_score,
        "gap_open": config.alignment.gap_open,
        "gap_extend": config.alignment.gap_extend,
        "traceback_preference": config.alignment.traceback_preference,
        "phrase_gap_sec": config.alignment.phrase_gap_sec,
        "phrase_gap_score": config.alignment.phrase_gap_score,
    }
    fields.update(overrides)
    return M3ComparisonConfig(
        schema=config.schema,
        registered_utc=config.registered_utc,
        representation=config.representation,
        alignment=AlignmentConfig(**fields),
        coverage=config.coverage,
        evidence_thresholds=config.evidence_thresholds,
        separation_margin=config.separation_margin,
    )


def _note(pitch_midi: float, start_sec: float, end_sec: float) -> MelodyNote:
    return MelodyNote(start_sec=start_sec, end_sec=end_sec, pitch_midi=pitch_midi, confidence=0.9)


# --------------------------------------------------------------------------- #
# ノート層 NW: 装飾音挿入によるギャップ吸収
# --------------------------------------------------------------------------- #
def test_align_intervals_absorbs_grace_note_insertion():
    config = _default_config()
    seq_a = (4, 3, -2, 5)
    seq_b = (4, 3, 7, -2, 5)  # index2 に装飾音由来の音程 7 を挿入
    alignment = align_intervals(seq_a, seq_b, config)

    assert alignment.gaps_b == (2,)
    assert alignment.gaps_a == ()
    # 挿入以外の全ノートが対応する。
    assert alignment.pairs == ((0, 0), (1, 1), (2, 3), (3, 4))
    assert alignment.score == pytest.approx(3.0)


def test_align_intervals_mismatch_costs_more_than_gap_when_isolated():
    """1 箇所だけ値が違う音程列は、ギャップで吸収する方がミスマッチより有利になりうる。"""
    config = _default_config()
    seq_a = (4, 3, -2)
    seq_b = (4, 3, -2, 9)  # 末尾に大きく異なる音程が 1 個追加
    alignment = align_intervals(seq_a, seq_b, config)
    assert alignment.pairs == ((0, 0), (1, 1), (2, 2))
    assert alignment.gaps_b == (3,)
    assert alignment.gaps_a == ()


# --------------------------------------------------------------------------- #
# 決定論 + tie-break
# --------------------------------------------------------------------------- #
def test_align_intervals_deterministic_bit_identical():
    config = _default_config()
    seq_a = (4, 3, -2, 5)
    seq_b = (4, 3, 7, -2, 5)
    first = align_intervals(seq_a, seq_b, config)
    second = align_intervals(seq_a, seq_b, config)
    assert first == second


def test_align_intervals_tie_break_respects_preference_order():
    """全コストが同値のとき、tie-break は traceback_preference の順で一意に決まる。"""
    base = _default_config()
    zero_cost = _with_alignment(
        base,
        match_score=0.0,
        mismatch_score=0.0,
        gap_open=0.0,
        gap_extend=0.0,
        traceback_preference=("diag", "up", "left"),
    )
    diag_first = align_intervals((1, 2), (3, 4), zero_cost)
    assert diag_first.pairs == ((0, 0), (1, 1))
    assert diag_first.gaps_a == ()
    assert diag_first.gaps_b == ()

    up_first = _with_alignment(zero_cost, traceback_preference=("up", "diag", "left"))
    up_result = align_intervals((1, 2), (3, 4), up_first)
    # preference を変えると同点の解決先が変わる（tie-break が実際に参照されている証拠）。
    assert up_result.pairs != diag_first.pairs
    assert len(up_result.pairs) + len(up_result.gaps_a) == 2
    assert len(up_result.pairs) + len(up_result.gaps_b) == 2


# --------------------------------------------------------------------------- #
# フレーズ増減
# --------------------------------------------------------------------------- #
def _phrase_fixture_notes():
    phrase0 = [_note(60, 0.0, 0.3), _note(64, 0.4, 0.7), _note(67, 0.8, 1.1), _note(65, 1.2, 1.5)]
    phrase1 = [_note(50, 2.2, 2.5), _note(55, 2.6, 2.9), _note(50, 3.0, 3.3)]
    return phrase0, phrase1


def test_align_melodies_records_extra_phrase_as_unmatched_without_disturbing_others():
    config = _default_config()
    phrase0, phrase1 = _phrase_fixture_notes()
    notes_a = phrase0 + phrase1

    extra_phrase = [_note(40, 4.0, 4.3), _note(41, 4.4, 4.7), _note(40, 4.8, 5.1), _note(41, 5.2, 5.5)]
    notes_b = phrase0 + phrase1 + extra_phrase

    seqs_a = build_sequences(notes_a, config)
    seqs_b = build_sequences(notes_b, config)
    phrases_a = split_phrases(notes_a, config.alignment.phrase_gap_sec)
    phrases_b = split_phrases(notes_b, config.alignment.phrase_gap_sec)
    assert len(phrases_a) == 2
    assert len(phrases_b) == 3

    result = align_melodies(seqs_a, seqs_b, phrases_a, phrases_b, config)

    assert [(c.phrase_index_a, c.phrase_index_b) for c in result.phrase_correspondences] == [
        (0, 0),
        (1, 1),
    ]
    assert result.unmatched_phrase_indices_a == ()
    assert result.unmatched_phrase_indices_b == (2,)
    for corr in result.phrase_correspondences:
        assert corr.similarity == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# 被覆会計の合計整合
# --------------------------------------------------------------------------- #
def test_alignment_coverage_accounting_totals():
    config = _default_config()
    phrase0, phrase1 = _phrase_fixture_notes()
    notes_a = phrase0 + phrase1
    extra_phrase = [_note(40, 4.0, 4.3), _note(41, 4.4, 4.7), _note(40, 4.8, 5.1), _note(41, 5.2, 5.5)]
    notes_b = phrase0 + phrase1 + extra_phrase

    seqs_a = build_sequences(notes_a, config)
    seqs_b = build_sequences(notes_b, config)
    phrases_a = split_phrases(notes_a, config.alignment.phrase_gap_sec)
    phrases_b = split_phrases(notes_b, config.alignment.phrase_gap_sec)
    result = align_melodies(seqs_a, seqs_b, phrases_a, phrases_b, config)

    # フレーズ: 対応 + 未対応 = 全フレーズ数（両側とも）。
    assert len(result.phrase_correspondences) + len(result.unmatched_phrase_indices_a) == len(
        phrases_a
    )
    assert len(result.phrase_correspondences) + len(result.unmatched_phrase_indices_b) == len(
        phrases_b
    )

    # ノート: 整列ノート集合を明示的に再構築し、fraction と突き合わせる。
    aligned_a: set = set()
    aligned_b: set = set()
    for corr in result.phrase_correspondences:
        pa = phrases_a[corr.phrase_index_a]
        pb = phrases_b[corr.phrase_index_b]
        if len(pa) == 1 and len(pb) == 1:
            aligned_a.add((corr.phrase_index_a, 0))
            aligned_b.add((corr.phrase_index_b, 0))
            continue
        for ia, ib in corr.note_alignment.pairs:
            aligned_a.add((corr.phrase_index_a, ia))
            aligned_a.add((corr.phrase_index_a, ia + 1))
            aligned_b.add((corr.phrase_index_b, ib))
            aligned_b.add((corr.phrase_index_b, ib + 1))

    total_notes_a = len(seqs_a.pitch_semitones)
    total_notes_b = len(seqs_b.pitch_semitones)
    assert len(aligned_a) / total_notes_a == pytest.approx(result.coverage.aligned_note_fraction_a)
    assert len(aligned_b) / total_notes_b == pytest.approx(result.coverage.aligned_note_fraction_b)
    # aligned + unaligned = 全ノート数（両側）。
    assert len(aligned_a) + (total_notes_a - len(aligned_a)) == total_notes_a
    assert len(aligned_b) + (total_notes_b - len(aligned_b)) == total_notes_b
    # 未対応フレーズ（extra_phrase）のノートは分母に入るが整列済みにはならない。
    assert len(aligned_b) < total_notes_b


# --------------------------------------------------------------------------- #
# 空 / 単ノート系列の縮退
# --------------------------------------------------------------------------- #
def test_align_melodies_empty_sequences_degenerate():
    config = _default_config()
    empty_seqs = build_sequences([], config)
    empty_phrases = split_phrases([], config.alignment.phrase_gap_sec)

    result = align_melodies(empty_seqs, empty_seqs, empty_phrases, empty_phrases, config)

    assert result.phrase_correspondences == ()
    assert result.unmatched_phrase_indices_a == ()
    assert result.unmatched_phrase_indices_b == ()
    assert result.coverage.aligned_note_fraction_a == 0.0
    assert result.coverage.aligned_note_fraction_b == 0.0
    assert result.coverage.phrase_coverage_a == 0.0
    assert result.coverage.phrase_coverage_b == 0.0


def test_align_melodies_single_note_self_comparison_is_fully_covered():
    config = _default_config()
    notes = [_note(60, 0.0, 0.3)]
    seqs = build_sequences(notes, config)
    phrases = split_phrases(notes, config.alignment.phrase_gap_sec)

    result = align_melodies(seqs, seqs, phrases, phrases, config)

    assert len(result.phrase_correspondences) == 1
    corr = result.phrase_correspondences[0]
    assert corr.similarity == pytest.approx(1.0)
    assert corr.note_alignment.pairs == ()
    assert result.coverage.aligned_note_fraction_a == pytest.approx(1.0)
    assert result.coverage.aligned_note_fraction_b == pytest.approx(1.0)
    assert result.coverage.phrase_coverage_a == pytest.approx(1.0)
    assert result.coverage.phrase_coverage_b == pytest.approx(1.0)


def test_align_intervals_both_empty_is_trivial():
    config = _default_config()
    alignment = align_intervals((), (), config)
    assert alignment.pairs == ()
    assert alignment.gaps_a == ()
    assert alignment.gaps_b == ()
    assert alignment.score == pytest.approx(0.0)
