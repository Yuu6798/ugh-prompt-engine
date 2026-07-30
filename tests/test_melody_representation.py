"""tests/test_melody_representation.py — melody/representation.py（M3a）のテスト。

CI 安全（重依存なし・pytest -m "not slow" に含む）: レジストリロード・正規化系列の
手計算一致・移調/変速不変性・折返し境界・hash 決定論・フレーズ分割の
`observability._phrase_count` との同期を検証する。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from svp_rpe.melody.observability import MelodyNote
from svp_rpe.melody.observability import _phrase_count as _obs_phrase_count
from svp_rpe.melody.representation import (
    M3ComparisonConfig,
    build_sequences,
    load_m3_registry,
    sequence_sha256,
    split_phrases,
)

ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = ROOT / "tests" / "fixtures" / "melody_bench"
M1_REGISTRY_PATH = BENCH_DIR / "registry.yaml"
M3_REGISTRY_PATH = BENCH_DIR / "m3_comparison_registry.yaml"


def _load_m3_mapping() -> dict:
    return yaml.safe_load(M3_REGISTRY_PATH.read_text(encoding="utf-8"))


def _default_config() -> M3ComparisonConfig:
    config, _ = load_m3_registry(M3_REGISTRY_PATH)
    return config


def _note(pitch_midi: float, start_sec: float, end_sec: float, confidence: float = 0.9) -> MelodyNote:
    return MelodyNote(
        start_sec=start_sec, end_sec=end_sec, pitch_midi=pitch_midi, confidence=confidence
    )


# --------------------------------------------------------------------------- #
# レジストリロード
# --------------------------------------------------------------------------- #
def test_load_m3_registry_roundtrip():
    config, digest = load_m3_registry(M3_REGISTRY_PATH)
    assert config.schema == "m3-comparison/0.1"
    assert config.representation.pitch_quantization_semitones == 1
    assert config.representation.contour_small_max_semitones == 2
    assert config.alignment.phrase_gap_sec == pytest.approx(0.6)
    assert config.alignment.traceback_preference == ("diag", "up", "left")
    assert config.coverage.floor == pytest.approx(0.5)
    assert config.evidence_thresholds.status == "uncalibrated"
    assert config.separation_margin.min_same_minus_cross_margin == pytest.approx(0.15)
    assert len(digest) == 64


def test_from_registry_rejects_unknown_top_level_key():
    mapping = _load_m3_mapping()
    mapping["bogus_top_key"] = 1
    with pytest.raises(ValueError, match="unknown m3_comparison_registry top-level keys"):
        M3ComparisonConfig.from_registry(mapping)


def test_from_registry_rejects_unknown_nested_key():
    mapping = _load_m3_mapping()
    mapping["representation"] = dict(mapping["representation"], bogus_nested_key=1)
    with pytest.raises(ValueError, match="unknown m3_comparison_registry.representation keys"):
        M3ComparisonConfig.from_registry(mapping)


def test_from_registry_rejects_missing_top_level_key():
    mapping = _load_m3_mapping()
    del mapping["coverage"]
    with pytest.raises(ValueError, match="missing required top-level keys"):
        M3ComparisonConfig.from_registry(mapping)


def test_from_registry_rejects_missing_nested_key():
    mapping = _load_m3_mapping()
    alignment = dict(mapping["alignment"])
    del alignment["phrase_gap_sec"]
    mapping["alignment"] = alignment
    with pytest.raises(ValueError, match="m3_comparison_registry.alignment missing required keys"):
        M3ComparisonConfig.from_registry(mapping)


def test_from_registry_rejects_wrong_schema():
    mapping = _load_m3_mapping()
    mapping["schema"] = "m3-comparison/9.9"
    with pytest.raises(ValueError, match="unsupported m3-comparison registry schema"):
        M3ComparisonConfig.from_registry(mapping)


def test_phrase_gap_sec_synced_with_m1_registry():
    """M3 alignment.phrase_gap_sec は M1 registry.yaml の observation_gate.phrase_gap_sec と同値。"""
    m1 = yaml.safe_load(M1_REGISTRY_PATH.read_text(encoding="utf-8"))
    m3 = _load_m3_mapping()
    assert m3["alignment"]["phrase_gap_sec"] == m1["observation_gate"]["phrase_gap_sec"]


def test_separation_margin_synced_with_m0_registry():
    """M3 separation_margin は M0 registry.yaml の separation_gate.min_same_minus_cross_margin を継承。"""
    m1 = yaml.safe_load(M1_REGISTRY_PATH.read_text(encoding="utf-8"))
    m3 = _load_m3_mapping()
    assert (
        m3["separation_margin"]["min_same_minus_cross_margin"]
        == m1["separation_gate"]["min_same_minus_cross_margin"]
    )


# --------------------------------------------------------------------------- #
# 手計算一致
# --------------------------------------------------------------------------- #
def _hand_calc_notes() -> list:
    return [
        _note(60, 0.0, 0.4),
        _note(64, 0.5, 0.9),
        _note(67, 1.0, 1.3),
        _note(65, 1.6, 2.0),
    ]


def test_build_sequences_hand_calc():
    config = _default_config()
    seqs = build_sequences(_hand_calc_notes(), config)

    assert seqs.pitch_semitones == (60, 64, 67, 65)
    assert seqs.intervals_raw == (4, 3, -2)
    assert seqs.intervals_folded == (4, 3, -2)
    assert seqs.contour == ("up_large", "up_large", "down_small")
    assert seqs.ioi_log2_ratios == (None, 0.0, 0.25)
    assert seqs.duration_log2_ratios == (0.0, -0.5, 0.5)


def test_build_sequences_empty_and_single_note_degenerate():
    config = _default_config()
    empty = build_sequences([], config)
    assert empty.pitch_semitones == ()
    assert empty.intervals_raw == ()
    assert empty.ioi_log2_ratios == ()
    assert empty.duration_log2_ratios == ()

    single = build_sequences([_note(60, 0.0, 0.4)], config)
    assert single.pitch_semitones == (60,)
    assert single.intervals_raw == ()
    assert single.contour == ()
    assert single.ioi_log2_ratios == ()
    assert single.duration_log2_ratios == ()


# --------------------------------------------------------------------------- #
# 移調 / 変速不変性
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("shift", [-5, -2, 2, 5])
def test_transposition_invariance(shift: int):
    config = _default_config()
    base = _hand_calc_notes()
    shifted = [_note(n.pitch_midi + shift, n.start_sec, n.end_sec) for n in base]

    seqs_base = build_sequences(base, config)
    seqs_shifted = build_sequences(shifted, config)

    assert seqs_shifted.intervals_raw == seqs_base.intervals_raw
    assert seqs_shifted.intervals_folded == seqs_base.intervals_folded
    assert seqs_shifted.contour == seqs_base.contour
    # 音高そのものは移調で変わる（不変であってはならない）。
    assert seqs_shifted.pitch_semitones != seqs_base.pitch_semitones


@pytest.mark.parametrize("rate", [0.5, 0.85, 1.15, 2.0])
def test_tempo_invariance(rate: float):
    config = _default_config()
    base = _hand_calc_notes()
    rated = [_note(n.pitch_midi, n.start_sec * rate, n.end_sec * rate) for n in base]

    seqs_base = build_sequences(base, config)
    seqs_rated = build_sequences(rated, config)

    assert seqs_rated.ioi_log2_ratios == seqs_base.ioi_log2_ratios
    assert seqs_rated.duration_log2_ratios == seqs_base.duration_log2_ratios
    # 音程列はテンポと無関係にそもそも不変。
    assert seqs_rated.intervals_raw == seqs_base.intervals_raw


# --------------------------------------------------------------------------- #
# オクターブ折返し境界
# --------------------------------------------------------------------------- #
def test_octave_fold_boundaries():
    config = _default_config()
    notes = [_note(60, 0.0, 0.3), _note(67, 0.5, 0.8), _note(79, 1.0, 1.3), _note(66, 1.6, 1.9)]
    seqs = build_sequences(notes, config)
    assert seqs.intervals_raw == (7, 12, -13)
    assert seqs.intervals_folded == (-5, 0, -1)


# --------------------------------------------------------------------------- #
# hash 決定論
# --------------------------------------------------------------------------- #
def test_sequence_sha256_deterministic():
    config = _default_config()
    seqs_a = build_sequences(_hand_calc_notes(), config)
    seqs_b = build_sequences(_hand_calc_notes(), config)
    assert sequence_sha256(seqs_a) == sequence_sha256(seqs_b)


def test_sequence_sha256_changes_with_content():
    config = _default_config()
    base = build_sequences(_hand_calc_notes(), config)
    altered_notes = _hand_calc_notes()
    altered_notes[-1] = _note(72, 1.6, 2.0)  # 末尾ノートの音高を変える
    altered = build_sequences(altered_notes, config)
    assert sequence_sha256(base) != sequence_sha256(altered)


# --------------------------------------------------------------------------- #
# フレーズ分割 = observability._phrase_count
# --------------------------------------------------------------------------- #
def test_split_phrases_matches_observability_phrase_count():
    phrase_gap_sec = 0.6
    notes = [
        _note(60, 0.0, 0.3),
        _note(62, 0.4, 0.7),
        _note(64, 0.8, 1.1),
        # ギャップ > 0.6 秒 → 新フレーズ
        _note(67, 2.0, 2.3),
        _note(65, 2.4, 2.7),
    ]
    phrases = split_phrases(notes, phrase_gap_sec)
    assert len(phrases) == _obs_phrase_count(notes, phrase_gap_sec)
    assert len(phrases) == 2
    assert len(phrases[0]) == 3
    assert len(phrases[1]) == 2


def test_split_phrases_empty_and_single_note():
    assert split_phrases([], 0.6) == []
    single = [_note(60, 0.0, 0.3)]
    phrases = split_phrases(single, 0.6)
    assert len(phrases) == 1 == _obs_phrase_count(single, 0.6)
