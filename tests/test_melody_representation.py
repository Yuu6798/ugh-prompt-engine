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


# --------------------------------------------------------------------------- #
# 値不変条件のロード時検証（レビュー対応 2026-07-30 第 17 ラウンド）
# --------------------------------------------------------------------------- #
def test_from_registry_accepts_frozen_registry_values():
    """凍結レジストリ（`m3_comparison_registry.yaml`）の現行値は全て新検証を通る
    （回帰）。値そのものは変更しない。
    """
    config = M3ComparisonConfig.from_registry(_load_m3_mapping())
    assert config.representation.pitch_quantization_semitones == 1
    assert config.alignment.match_score > config.alignment.mismatch_score
    assert config.coverage.floor_status in {"provisional_until_m3d", "frozen"}
    assert config.evidence_thresholds.status in {"uncalibrated", "frozen"}
    assert 0.0 < config.separation_margin.min_same_minus_cross_margin <= 1.0


def test_from_registry_rejects_pitch_quantization_not_equal_one():
    """`pitch_quantization_semitones` は M2d 拘束の設計固定値=1。2 への変更は拒否。"""
    mapping = _load_m3_mapping()
    mapping["representation"] = dict(mapping["representation"], pitch_quantization_semitones=2)
    with pytest.raises(ValueError, match="pitch_quantization_semitones"):
        M3ComparisonConfig.from_registry(mapping)


def test_from_registry_rejects_octave_artifact_divergence_out_of_range():
    """`octave_artifact_divergence` は 0.0〜1.0 域外なら fail-closed。"""
    mapping = _load_m3_mapping()
    mapping["representation"] = dict(
        mapping["representation"], octave_artifact_divergence=1.5
    )
    with pytest.raises(ValueError, match="octave_artifact_divergence"):
        M3ComparisonConfig.from_registry(mapping)


def test_from_registry_rejects_match_score_not_greater_than_mismatch_score():
    """`match_score` は `mismatch_score` より大きくなければならない。"""
    mapping = _load_m3_mapping()
    mapping["alignment"] = dict(mapping["alignment"], match_score=-2.0)
    with pytest.raises(ValueError, match="match_score"):
        M3ComparisonConfig.from_registry(mapping)


def test_from_registry_rejects_traceback_preference_not_a_permutation():
    """`traceback_preference` は [diag, up, left] の順列でなければならない
    （重複・不明値・要素過不足は fail-closed）。
    """
    mapping = _load_m3_mapping()
    mapping["alignment"] = dict(mapping["alignment"], traceback_preference=["diag", "diag", "left"])
    with pytest.raises(ValueError, match="traceback_preference"):
        M3ComparisonConfig.from_registry(mapping)


def test_from_registry_rejects_coverage_floor_out_of_range():
    """`coverage.floor` は 0.0〜1.0 域外なら fail-closed。"""
    mapping = _load_m3_mapping()
    mapping["coverage"] = dict(mapping["coverage"], floor=1.5)
    with pytest.raises(ValueError, match="coverage.floor"):
        M3ComparisonConfig.from_registry(mapping)


def test_from_registry_rejects_unknown_coverage_floor_status():
    """`coverage.floor_status` は {provisional_until_m3d, frozen} のいずれかでなければ
    ならない。
    """
    mapping = _load_m3_mapping()
    mapping["coverage"] = dict(mapping["coverage"], floor_status="bogus")
    with pytest.raises(ValueError, match="floor_status"):
        M3ComparisonConfig.from_registry(mapping)


def test_from_registry_rejects_unknown_evidence_thresholds_status():
    """`evidence_thresholds.status` は {uncalibrated, frozen} のいずれかでなければ
    ならない。
    """
    mapping = _load_m3_mapping()
    mapping["evidence_thresholds"] = {"status": "bogus"}
    with pytest.raises(ValueError, match="evidence_thresholds.status"):
        M3ComparisonConfig.from_registry(mapping)


# --------------------------------------------------------------------------- #
# 凍結 axes 境界のロード時検証（レビュー対応 2026-07-30 第 18 ラウンド）
# --------------------------------------------------------------------------- #
# 凍結レジストリ（m3_comparison_registry.yaml）の現行 status は "uncalibrated"
# （axes 節を持たない）ため、以下は全て mapping を直接組み立てて
# `status: frozen` + axes 節を検証する。ハーネス側 `_validate_frozen_axes`
# （`scripts/run_melody_comparison.py`）の同種テストと対をなす——ここではロード
# 時（`M3ComparisonConfig.from_registry`）の fail-fast を検証する。
_VALID_FROZEN_AXES = {
    "contour": {"strong_min": 0.8, "none_max": 0.2},
    "interval": {"strong_min": 0.8, "none_max": 0.2},
    "rhythm": {"strong_min": 0.7, "none_max": 0.3},
}


def _frozen_mapping(axes: object) -> dict:
    mapping = _load_m3_mapping()
    mapping["evidence_thresholds"] = {"status": "frozen", "axes": axes}
    return mapping


def test_from_registry_accepts_frozen_status_with_full_axes():
    """3 軸全て整形済みの frozen registry はそのまま通る（回帰）。"""
    config = M3ComparisonConfig.from_registry(_frozen_mapping(_VALID_FROZEN_AXES))
    assert config.evidence_thresholds.status == "frozen"
    assert set(config.evidence_thresholds.axes) == {"contour", "interval", "rhythm"}


def test_from_registry_accepts_frozen_status_with_partial_axes():
    """1 軸のみ整形済みの frozen registry も通る（3 軸全て必須ではない・
    ハーネス `_validate_frozen_axes` 第 4 ラウンド緩和と同じ扱い）。
    """
    axes = {"contour": {"strong_min": 0.8, "none_max": 0.2}}
    config = M3ComparisonConfig.from_registry(_frozen_mapping(axes))
    assert set(config.evidence_thresholds.axes) == {"contour"}


def test_from_registry_rejects_frozen_status_without_axes_key():
    """`status == "frozen"` だが `axes` キー自体が無い(既定 None)場合は拒否する。"""
    mapping = _load_m3_mapping()
    mapping["evidence_thresholds"] = {"status": "frozen"}
    with pytest.raises(ValueError, match="evidence_thresholds.axes"):
        M3ComparisonConfig.from_registry(mapping)


def test_from_registry_rejects_frozen_status_with_none_axes():
    """`status == "frozen"` だが `axes: null` (明示的に None) も拒否する。"""
    with pytest.raises(ValueError, match="evidence_thresholds.axes"):
        M3ComparisonConfig.from_registry(_frozen_mapping(None))


def test_from_registry_rejects_frozen_status_with_empty_axes_mapping():
    """`status == "frozen"` だが `axes: {}` (空 mapping) も拒否する。"""
    with pytest.raises(ValueError, match="evidence_thresholds.axes"):
        M3ComparisonConfig.from_registry(_frozen_mapping({}))


def test_from_registry_rejects_frozen_axes_unknown_axis_name():
    """軸名が {contour, interval, rhythm} の部分集合でなければ拒否する。"""
    axes = dict(_VALID_FROZEN_AXES)
    axes["timbre"] = {"strong_min": 0.8, "none_max": 0.2}
    with pytest.raises(ValueError, match="未知軸名"):
        M3ComparisonConfig.from_registry(_frozen_mapping(axes))


def test_from_registry_rejects_frozen_axis_missing_key():
    """各軸は `strong_min`/`none_max` の両キーを持たなければならない(欠落は拒否)。"""
    axes = {"contour": {"strong_min": 0.8}}
    with pytest.raises(ValueError, match="evidence_thresholds.axes.contour"):
        M3ComparisonConfig.from_registry(_frozen_mapping(axes))


def test_from_registry_rejects_frozen_axis_extra_key():
    """各軸は `strong_min`/`none_max` の**完全一致**キー集合でなければならない
    (余剰キーも拒否——ハーネス `_validate_frozen_axes` は存在チェックのみで
    過不足を見ないため、ここが唯一過不足を検出する)。
    """
    axes = {"contour": {"strong_min": 0.8, "none_max": 0.2, "bogus_extra": 1.0}}
    with pytest.raises(ValueError, match="evidence_thresholds.axes.contour"):
        M3ComparisonConfig.from_registry(_frozen_mapping(axes))


@pytest.mark.parametrize(
    "bad_value",
    [1.5, -0.1],
    ids=["above_one", "below_zero"],
)
def test_from_registry_rejects_frozen_axis_value_out_of_range(bad_value: float):
    """`strong_min`/`none_max` は 0.0〜1.0 の範囲外なら拒否する。"""
    axes = {"contour": {"strong_min": bad_value, "none_max": 0.2}}
    with pytest.raises(ValueError, match="evidence_thresholds.axes.contour.strong_min"):
        M3ComparisonConfig.from_registry(_frozen_mapping(axes))


def test_from_registry_rejects_frozen_axis_non_numeric_value():
    """`strong_min`/`none_max` が非数値(bool 含む)なら拒否する。"""
    axes_bool = {"contour": {"strong_min": True, "none_max": 0.2}}
    with pytest.raises(ValueError, match="evidence_thresholds.axes.contour.strong_min"):
        M3ComparisonConfig.from_registry(_frozen_mapping(axes_bool))

    axes_str = {"contour": {"strong_min": 0.8, "none_max": "0.2"}}
    with pytest.raises(ValueError, match="evidence_thresholds.axes.contour.none_max"):
        M3ComparisonConfig.from_registry(_frozen_mapping(axes_str))


def test_from_registry_rejects_frozen_axis_strong_min_below_none_max():
    """`strong_min < none_max`(大小関係逆転)は拒否する。"""
    axes = {"contour": {"strong_min": 0.1, "none_max": 0.8}}
    with pytest.raises(ValueError, match="strong_min"):
        M3ComparisonConfig.from_registry(_frozen_mapping(axes))


def test_from_registry_rejects_uncalibrated_status_with_axes_present():
    """`status == "uncalibrated"` なのに `axes` が存在するのは不整合として拒否する
    (未校正なのに閾値がある)。
    """
    mapping = _load_m3_mapping()
    mapping["evidence_thresholds"] = {"status": "uncalibrated", "axes": _VALID_FROZEN_AXES}
    with pytest.raises(ValueError, match="uncalibrated"):
        M3ComparisonConfig.from_registry(mapping)


def test_from_registry_rejects_separation_margin_out_of_range():
    """`separation_margin.min_same_minus_cross_margin` は 0 より大きく 1.0 以下
    でなければならない。
    """
    mapping = _load_m3_mapping()
    mapping["separation_margin"] = {"min_same_minus_cross_margin": 0.0}
    with pytest.raises(ValueError, match="separation_margin"):
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
# 半音丸めの移調同変性（レビュー対応 2026-07-30 第 11 ラウンド）
# --------------------------------------------------------------------------- #
def test_pitch_quantization_translation_equivariant_at_half_semitone_boundary():
    """`round()`（偶数丸め）は `60.5→60` / `61.5→62` のように .5 境界でタイブレーク
    の向きが値の偶奇で変わるため、+1 移調しても量子化後の音程が同量シフトしない
    反例が存在した——`floor(x + 0.5)` への変更後は、半音境界に乗るピッチ列
    ``(60.5, 61.5)`` とその +1 移調 ``(61.5, 62.5)`` で `intervals_raw` が一致する
    ことを確認する（移調同変性の回帰）。
    """
    config = _default_config()
    base_notes = [_note(60.5, 0.0, 0.3), _note(61.5, 0.5, 0.8)]
    shifted_notes = [_note(61.5, 0.0, 0.3), _note(62.5, 0.5, 0.8)]

    base = build_sequences(base_notes, config)
    shifted = build_sequences(shifted_notes, config)

    assert base.pitch_semitones == (61, 62)
    assert shifted.pitch_semitones == (62, 63)
    assert base.intervals_raw == shifted.intervals_raw == (1,)


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
