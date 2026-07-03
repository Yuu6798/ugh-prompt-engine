"""K3 直交性行列ハーネスのテスト。"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from scripts.measure_orthogonality import analyze_orthogonality, main, render_markdown
from svp_rpe.control import (
    classify_interference,
    completeness_scores,
    disentanglement_scores,
    effect_size_gap,
    importance_matrix,
    mass_weighted_overall,
    noise_ceiling,
    noise_margin,
    normalized_entropy,
)

K3_FIXTURE_PATH = Path("examples/control/k3/synth_performer_matrix_fixture.json")
K3_EXPECTED_PATH = Path("examples/control/k3/expected_orthogonality.json")

K3_SUNO_MINI_FIXTURE_PATH = Path("examples/control/k3/suno_mini_matrix_fixture.json")
K3_SUNO_MINI_EXPECTED_PATH = Path("examples/control/k3/expected_orthogonality_suno_mini.json")


# --------------------------------------------------------------------------
# classify_interference
# --------------------------------------------------------------------------


def test_classify_interference_boundaries() -> None:
    assert classify_interference(0.19) == "clean"
    assert classify_interference(0.2) == "weak"
    assert classify_interference(-0.5) == "weak"
    assert classify_interference(0.79) == "weak"
    assert classify_interference(0.8) == "strong"
    assert classify_interference(-3.0) == "strong"


# --------------------------------------------------------------------------
# normalized_entropy
# --------------------------------------------------------------------------


def test_normalized_entropy_empty_or_zero_is_none() -> None:
    assert normalized_entropy([]) is None
    assert normalized_entropy([0.0, 0.0, 0.0]) is None


def test_normalized_entropy_single_element_is_zero() -> None:
    assert normalized_entropy([5.0]) == 0.0


def test_normalized_entropy_uniform_is_one() -> None:
    assert normalized_entropy([1.0, 1.0, 1.0, 1.0]) == pytest.approx(1.0)


def test_normalized_entropy_one_hot_is_zero() -> None:
    assert normalized_entropy([7.0, 0.0, 0.0]) == pytest.approx(0.0)


def test_normalized_entropy_clamped_to_unit_interval() -> None:
    """一様分布の H/ln(n) が浮動小数点誤差で 1 を超えない（1-H の -0.0 回帰ガード）。"""
    for n in range(2, 12):
        value = normalized_entropy([1.0] * n)
        assert value is not None
        assert 0.0 <= value <= 1.0


# --------------------------------------------------------------------------
# importance_matrix
# --------------------------------------------------------------------------


def test_importance_matrix_abs_floor_and_cap() -> None:
    matrix = importance_matrix([[0.19, 0.2, -999.0], [999.0, -0.5, 0.0]])
    assert matrix[0, 0] == 0.0
    assert matrix[0, 1] == pytest.approx(0.2)
    assert matrix[0, 2] == pytest.approx(10.0)
    assert matrix[1, 0] == pytest.approx(10.0)
    assert matrix[1, 1] == pytest.approx(0.5)
    assert matrix[1, 2] == 0.0


def test_importance_matrix_rejects_ragged_or_empty() -> None:
    with pytest.raises(ValueError):
        importance_matrix([])
    with pytest.raises(ValueError):
        importance_matrix([[1.0, 2.0], [1.0]])
    with pytest.raises(ValueError):
        importance_matrix([[]])


# --------------------------------------------------------------------------
# disentanglement_scores / completeness_scores
# --------------------------------------------------------------------------


def test_disentanglement_and_completeness_identity_matrix() -> None:
    importance = np.array([[5.0, 0.0], [0.0, 5.0]])
    assert disentanglement_scores(importance) == pytest.approx([1.0, 1.0])
    assert completeness_scores(importance) == pytest.approx([1.0, 1.0])


def test_disentanglement_and_completeness_uniform_matrix_is_zero() -> None:
    importance = np.array([[1.0, 1.0], [1.0, 1.0]])
    assert disentanglement_scores(importance) == pytest.approx([0.0, 0.0])
    assert completeness_scores(importance) == pytest.approx([0.0, 0.0])


def test_disentanglement_zero_row_is_none_and_excluded_from_overall() -> None:
    importance = np.array([[0.0, 0.0], [3.0, 3.0]])
    scores = disentanglement_scores(importance)
    assert scores[0] is None
    assert scores[1] == pytest.approx(0.0)

    masses = importance.sum(axis=1).tolist()
    assert mass_weighted_overall(scores, masses) == pytest.approx(0.0)


def test_disentanglement_and_completeness_asymmetric_closed_form() -> None:
    """[[4,1],[0,2]] を手計算のクローズドフォームエントロピーと突き合わせる。"""
    importance = np.array([[4.0, 1.0], [0.0, 2.0]])

    # row0 = [4,1] -> p=[0.8,0.2]
    h_row0 = -(0.8 * math.log(0.8) + 0.2 * math.log(0.2)) / math.log(2)
    # row1 = [0,2] -> p=[0,1]; 0*ln0 := 0
    h_row1 = -(1.0 * math.log(1.0)) / math.log(2)
    expected_disentanglement = [1.0 - h_row0, 1.0 - h_row1]
    assert disentanglement_scores(importance) == pytest.approx(expected_disentanglement)

    # col0 = [4,0] -> p=[1,0]
    h_col0 = -(1.0 * math.log(1.0)) / math.log(2)
    # col1 = [1,2] -> p=[1/3, 2/3]
    h_col1 = -((1 / 3) * math.log(1 / 3) + (2 / 3) * math.log(2 / 3)) / math.log(2)
    expected_completeness = [1.0 - h_col0, 1.0 - h_col1]
    assert completeness_scores(importance) == pytest.approx(expected_completeness)


# --------------------------------------------------------------------------
# mass_weighted_overall
# --------------------------------------------------------------------------


def test_mass_weighted_overall_all_none_is_none() -> None:
    assert mass_weighted_overall([None, None], [1.0, 2.0]) is None


def test_mass_weighted_overall_weighting() -> None:
    assert mass_weighted_overall([1.0, 0.0], [3.0, 1.0]) == pytest.approx(0.75)


def test_mass_weighted_overall_zero_total_mass_is_none() -> None:
    assert mass_weighted_overall([1.0, 0.0], [0.0, 0.0]) is None


# --------------------------------------------------------------------------
# effect_size_gap
# --------------------------------------------------------------------------


def test_effect_size_gap_dominant_column_is_high() -> None:
    importance = np.array([[10.0], [1.0], [0.0]])
    assert effect_size_gap(importance) == pytest.approx([0.9])


def test_effect_size_gap_tie_column_is_zero() -> None:
    importance = np.array([[2.0], [2.0]])
    assert effect_size_gap(importance) == pytest.approx([0.0])


def test_effect_size_gap_zero_column_is_none() -> None:
    importance = np.array([[0.0], [0.0]])
    assert effect_size_gap(importance) == [None]


def test_effect_size_gap_single_row_is_one() -> None:
    importance = np.array([[5.0, 3.0]])
    assert effect_size_gap(importance) == pytest.approx([1.0, 1.0])


# --------------------------------------------------------------------------
# noise_ceiling / noise_margin
# --------------------------------------------------------------------------


def test_noise_ceiling_empty_is_none() -> None:
    assert noise_ceiling([]) is None


def test_noise_ceiling_mixed_signs_is_max_abs() -> None:
    assert noise_ceiling([-2.53, 0.0, 0.4, -0.1]) == pytest.approx(2.53)


def test_noise_ceiling_sentinel_dominates() -> None:
    """dead 行にセンチネル ±999 が紛れると天井が 999 に張り付く（fail-safe）。"""
    assert noise_ceiling([0.5, -999.0, 0.2]) == pytest.approx(999.0)


def test_noise_margin_none_ceiling_is_none() -> None:
    assert noise_margin(5.0, None) is None


def test_noise_margin_zero_ceiling_is_none() -> None:
    assert noise_margin(5.0, 0.0) is None


def test_noise_margin_ratio() -> None:
    assert noise_margin(-5.06, 2.53) == pytest.approx(2.0)
    assert noise_margin(1.265, 2.53) == pytest.approx(0.5)


# --------------------------------------------------------------------------
# analyze_orthogonality on a small synthetic in-memory fixture
# --------------------------------------------------------------------------


def _synthetic_raw() -> dict:
    knobs = [
        {"name": "k1", "low_level": "lo", "high_level": "hi", "diagonal_sensor": "s1",
         "expected_sign": 1},
        {"name": "k2", "low_level": "lo", "high_level": "hi", "diagonal_sensor": "s2",
         "expected_sign": 1},
    ]
    sensors = [
        {"name": "s1", "path": "s1", "kind": "core"},
        {"name": "s2", "path": "s2", "kind": "core"},
        {"name": "e1", "path": "e1", "kind": "extended"},
    ]
    samples = [
        # k1: strong effect on its own diagonal sensor s1, no effect on s2,
        # weak effect on the extended sensor e1.
        {"sample_id": "k1_lo_r01", "knob": "k1", "level": "lo", "repeat": 1,
         "features": {"s1": 0.0, "s2": 1.0, "e1": 0.0}},
        {"sample_id": "k1_lo_r02", "knob": "k1", "level": "lo", "repeat": 2,
         "features": {"s1": 0.2, "s2": 1.0, "e1": 1.0}},
        {"sample_id": "k1_hi_r01", "knob": "k1", "level": "hi", "repeat": 1,
         "features": {"s1": 2.0, "s2": 1.0, "e1": 0.3}},
        {"sample_id": "k1_hi_r02", "knob": "k1", "level": "hi", "repeat": 2,
         "features": {"s1": 2.2, "s2": 1.0, "e1": 1.3}},
        # k2: strong effect on its own diagonal sensor s2, no effect on s1,
        # strong (saturated) effect on the extended sensor e1.
        {"sample_id": "k2_lo_r01", "knob": "k2", "level": "lo", "repeat": 1,
         "features": {"s1": 5.0, "s2": 0.0, "e1": 0.0}},
        {"sample_id": "k2_lo_r02", "knob": "k2", "level": "lo", "repeat": 2,
         "features": {"s1": 5.0, "s2": 0.2, "e1": 0.0}},
        {"sample_id": "k2_hi_r01", "knob": "k2", "level": "hi", "repeat": 1,
         "features": {"s1": 5.0, "s2": 2.0, "e1": 5.0}},
        {"sample_id": "k2_hi_r02", "knob": "k2", "level": "hi", "repeat": 2,
         "features": {"s1": 5.0, "s2": 2.2, "e1": 5.0}},
    ]
    return {
        "fixture_id": "synthetic_test_fixture",
        "generator": "synthetic",
        "repetitions": 2,
        "knobs": knobs,
        "sensors": sensors,
        "samples": samples,
    }


def test_analyze_orthogonality_matrix_shape_and_diagonal() -> None:
    report = analyze_orthogonality(_synthetic_raw())

    assert report["knobs"] == ["k1", "k2"]
    assert report["matrix"]["sensors_core"] == ["s1", "s2"]
    assert report["matrix"]["sensors_extended"] == ["e1"]
    assert len(report["matrix"]["effects_core"]) == 2
    assert all(len(row) == 2 for row in report["matrix"]["effects_core"])
    assert len(report["matrix"]["effects_extended"]) == 2
    assert all(len(row) == 1 for row in report["matrix"]["effects_extended"])

    # 6 knob x sensor cells over the core+extended sensor set = 2 knobs * 3 sensors
    assert len(report["cells"]) == 6
    diagonal_cells = [c for c in report["cells"] if c["is_diagonal"]]
    off_diagonal_cells = [c for c in report["cells"] if not c["is_diagonal"]]
    assert len(diagonal_cells) == 2
    assert len(off_diagonal_cells) == 4
    assert {c["knob"] for c in diagonal_cells} == {"k1", "k2"}
    for cell in diagonal_cells:
        assert "diagonal_classification" in cell
        assert "expected_sign" in cell
    for cell in off_diagonal_cells:
        assert "diagonal_classification" not in cell
        assert "expected_sign" not in cell

    assert report["diagonal_summary"] == {"tight": 2, "loose": 0, "dead": 0}


def test_analyze_orthogonality_interference_summary_counts() -> None:
    report = analyze_orthogonality(_synthetic_raw())

    # off-diagonal cells: k1->s2 clean, k1->e1 weak, k2->s1 clean, k2->e1 strong
    assert report["interference_summary"] == {"clean": 2, "weak": 1, "strong": 1}


def test_analyze_orthogonality_diagonal_sensor_mismatch_raises() -> None:
    raw = _synthetic_raw()
    raw["knobs"][0]["diagonal_sensor"] = "s2"  # should be s1
    with pytest.raises(ValueError):
        analyze_orthogonality(raw)


def test_analyze_orthogonality_short_repetitions_raises() -> None:
    raw = _synthetic_raw()
    raw["repetitions"] = 3  # only 2 samples exist per (knob, level)
    with pytest.raises(ValueError):
        analyze_orthogonality(raw)


# --------------------------------------------------------------------------
# K3-1b: noise-ceiling instrumentation on analyze_orthogonality
# --------------------------------------------------------------------------


def test_analyze_orthogonality_no_dead_rows_has_no_ceiling() -> None:
    """既知 dead 行が無い fixture では有意性を主張できない（計器の自己申告）。"""
    report = analyze_orthogonality(_synthetic_raw())

    assert report["noise"] == {
        "known_dead_knobs": [],
        "null_cell_count": 0,
        "ceiling": None,
        "null_values": [],
    }
    non_null_cells = [c for c in report["cells"] if not c["is_null_source"]]
    assert len(non_null_cells) == len(report["cells"])
    for cell in non_null_cells:
        assert cell["noise_margin"] is None
        assert cell["exceeds_noise_ceiling"] is None
    assert report["resolution_summary"] == {
        "resolved": 0,
        "unresolved": 0,
        "no_ceiling": len(report["cells"]),
    }


def _synthetic_raw_with_known_dead() -> dict:
    """k1/k2 は診断センサーだけを動かす clean な設計、k3 は既知 dead 行（ノイズ源）。"""
    knobs = [
        {"name": "k1", "low_level": "lo", "high_level": "hi", "diagonal_sensor": "s1",
         "expected_sign": 1},
        {"name": "k2", "low_level": "lo", "high_level": "hi", "diagonal_sensor": "s2",
         "expected_sign": 1},
        {"name": "k3", "low_level": "lo", "high_level": "hi", "diagonal_sensor": "s3",
         "expected_sign": 1, "known_dead": True},
    ]
    sensors = [
        {"name": "s1", "path": "s1", "kind": "core"},
        {"name": "s2", "path": "s2", "kind": "core"},
        {"name": "s3", "path": "s3", "kind": "core"},
    ]
    samples = [
        # k1: large, clean effect on its own diagonal s1; s2/s3 untouched.
        {"sample_id": "k1_lo_r01", "knob": "k1", "level": "lo", "repeat": 1,
         "features": {"s1": 0.0, "s2": 1.0, "s3": 0.0}},
        {"sample_id": "k1_lo_r02", "knob": "k1", "level": "lo", "repeat": 2,
         "features": {"s1": 0.1, "s2": 1.0, "s3": 0.0}},
        {"sample_id": "k1_hi_r01", "knob": "k1", "level": "hi", "repeat": 1,
         "features": {"s1": 3.0, "s2": 1.0, "s3": 0.0}},
        {"sample_id": "k1_hi_r02", "knob": "k1", "level": "hi", "repeat": 2,
         "features": {"s1": 3.1, "s2": 1.0, "s3": 0.0}},
        # k2: large, clean effect on its own diagonal s2; s1/s3 untouched.
        {"sample_id": "k2_lo_r01", "knob": "k2", "level": "lo", "repeat": 1,
         "features": {"s1": 1.0, "s2": 0.0, "s3": 0.0}},
        {"sample_id": "k2_lo_r02", "knob": "k2", "level": "lo", "repeat": 2,
         "features": {"s1": 1.0, "s2": 0.1, "s3": 0.0}},
        {"sample_id": "k2_hi_r01", "knob": "k2", "level": "hi", "repeat": 1,
         "features": {"s1": 1.0, "s2": 3.0, "s3": 0.0}},
        {"sample_id": "k2_hi_r02", "knob": "k2", "level": "hi", "repeat": 2,
         "features": {"s1": 1.0, "s2": 3.1, "s3": 0.0}},
        # k3 (known_dead): seed-noise-only wobble on every sensor, including its
        # own "diagonal" s3 -- nothing here is a real signal.
        {"sample_id": "k3_lo_r01", "knob": "k3", "level": "lo", "repeat": 1,
         "features": {"s1": 0.0, "s2": 0.0, "s3": 0.0}},
        {"sample_id": "k3_lo_r02", "knob": "k3", "level": "lo", "repeat": 2,
         "features": {"s1": 0.05, "s2": 0.05, "s3": 0.05}},
        {"sample_id": "k3_hi_r01", "knob": "k3", "level": "hi", "repeat": 1,
         "features": {"s1": 0.1, "s2": 0.1, "s3": 0.2}},
        {"sample_id": "k3_hi_r02", "knob": "k3", "level": "hi", "repeat": 2,
         "features": {"s1": 0.15, "s2": 0.15, "s3": 0.25}},
    ]
    return {
        "fixture_id": "synthetic_test_fixture_known_dead",
        "generator": "synthetic",
        "repetitions": 2,
        "knobs": knobs,
        "sensors": sensors,
        "samples": samples,
    }


def test_analyze_orthogonality_known_dead_row_supplies_null_pool() -> None:
    report = analyze_orthogonality(_synthetic_raw_with_known_dead())

    null_source_cells = [c for c in report["cells"] if c["is_null_source"]]
    assert {c["knob"] for c in null_source_cells} == {"k3"}
    # all 3 sensors of the known_dead row, including its own diagonal.
    assert len(null_source_cells) == 3
    for cell in null_source_cells:
        assert cell["noise_margin"] is None
        assert cell["exceeds_noise_ceiling"] is None

    assert report["noise"]["known_dead_knobs"] == ["k3"]
    assert report["noise"]["null_cell_count"] == 3
    assert report["noise"]["ceiling"] == pytest.approx(5.656854)
    assert report["noise"]["null_values"] == [
        pytest.approx(2.828427),
        pytest.approx(2.828427),
        pytest.approx(5.656854),
    ]


def test_analyze_orthogonality_known_dead_row_classifies_live_cells() -> None:
    report = analyze_orthogonality(_synthetic_raw_with_known_dead())

    by_knob_sensor = {(c["knob"], c["sensor"]): c for c in report["cells"]}

    resolved = by_knob_sensor[("k1", "s1")]
    assert resolved["exceeds_noise_ceiling"] is True
    assert resolved["noise_margin"] == pytest.approx(7.5)

    resolved2 = by_knob_sensor[("k2", "s2")]
    assert resolved2["exceeds_noise_ceiling"] is True

    unresolved = by_knob_sensor[("k1", "s2")]
    assert unresolved["effect"] == pytest.approx(0.0)
    assert unresolved["exceeds_noise_ceiling"] is False
    assert unresolved["noise_margin"] == pytest.approx(0.0)

    assert report["resolution_summary"] == {"resolved": 2, "unresolved": 4, "no_ceiling": 0}


# --------------------------------------------------------------------------
# Snapshot test (fixture generated by scripts/build_k3_fixture.py)
# --------------------------------------------------------------------------


def test_k3_fixture_snapshot() -> None:
    from scripts.measure_grip import load_fixture

    report = analyze_orthogonality(load_fixture(K3_FIXTURE_PATH))
    expected = json.loads(K3_EXPECTED_PATH.read_text(encoding="utf-8"))

    assert report == expected


# --------------------------------------------------------------------------
# K3-2a: K2 Suno fixture -> K3 orthogonality fixture converter (pure JSON reshape)
# --------------------------------------------------------------------------


def test_k3_suno_mini_converter_determinism() -> None:
    from scripts.build_k3_suno_mini_fixture import convert, load_source, render_fixture

    regenerated = render_fixture(convert(load_source()))
    committed = K3_SUNO_MINI_FIXTURE_PATH.read_text(encoding="utf-8")

    assert regenerated == committed


def test_k3_suno_mini_fixture_snapshot() -> None:
    from scripts.measure_grip import load_fixture

    report = analyze_orthogonality(load_fixture(K3_SUNO_MINI_FIXTURE_PATH))
    expected = json.loads(K3_SUNO_MINI_EXPECTED_PATH.read_text(encoding="utf-8"))

    assert report == expected


def test_k3_suno_mini_converter_rejects_unexpected_source() -> None:
    from scripts.build_k3_suno_mini_fixture import convert, load_source

    raw = load_source()
    raw["knobs"][0]["name"] = "tempo"  # should be "bpm"

    with pytest.raises(ValueError):
        convert(raw)


# --------------------------------------------------------------------------
# K3-2b: K2-schema MusicGen extract fixture -> K3 orthogonality fixture
# converter (5-knob full matrix, synthetic in-memory source only -- the real
# committed extract fixture does not exist yet, so no snapshot test here).
# --------------------------------------------------------------------------


def _musicgen_extract_synthetic_raw() -> dict:
    def feat(
        bpm: float,
        key: str,
        centroid: float,
        brightness: float,
        active: float,
        valley: float,
    ) -> dict:
        return {
            "bpm": bpm,
            "key": key,
            "spectral_centroid": centroid,
            "spectral_profile": {"brightness": brightness},
            "active_rate": active,
            "valley_depth": valley,
        }

    knobs = [
        {"name": "bpm", "sensor": "bpm", "low_level": "90", "high_level": "170",
         "expected_sign": 1},
        {"name": "key", "sensor": "key", "low_level": "C major",
         "high_level": "F sharp major", "expected_sign": -1},
        {"name": "brightness", "sensor": "spectral_centroid", "low_level": "dark",
         "high_level": "bright", "expected_sign": 1},
        {"name": "active_rate_target", "sensor": "active_rate", "low_level": "0.80-0.85",
         "high_level": "0.90-0.95", "expected_sign": 1},
        {"name": "valley_depth_target", "sensor": "valley_depth", "low_level": "0.10-0.20",
         "high_level": "0.30-0.40", "expected_sign": 1},
    ]
    samples = [
        {"sample_id": "bpm_low_00", "knob": "bpm", "level": "90",
         "features": feat(90.0, "C major", 2000.0, 0.1, 0.9, 0.15)},
        {"sample_id": "bpm_low_01", "knob": "bpm", "level": "90",
         "features": feat(91.0, "C major", 2000.0, 0.1, 0.9, 0.15)},
        {"sample_id": "bpm_high_00", "knob": "bpm", "level": "170",
         "features": feat(168.0, "C major", 2000.0, 0.1, 0.9, 0.15)},
        {"sample_id": "bpm_high_01", "knob": "bpm", "level": "170",
         "features": feat(169.0, "C major", 2000.0, 0.1, 0.9, 0.15)},
        {"sample_id": "key_low_00", "knob": "key", "level": "C major",
         "features": feat(120.0, "C major", 2000.0, 0.1, 0.9, 0.15)},
        {"sample_id": "key_low_01", "knob": "key", "level": "C major",
         "features": feat(120.0, "C major", 2000.0, 0.1, 0.9, 0.15)},
        {"sample_id": "key_high_00", "knob": "key", "level": "F sharp major",
         "features": feat(120.0, "F# major", 2000.0, 0.1, 0.9, 0.15)},
        {"sample_id": "key_high_01", "knob": "key", "level": "F sharp major",
         "features": feat(120.0, "F# major", 2000.0, 0.1, 0.9, 0.15)},
        {"sample_id": "brightness_low_00", "knob": "brightness", "level": "dark",
         "features": feat(120.0, "C major", 500.0, 0.01, 0.9, 0.15)},
        {"sample_id": "brightness_low_01", "knob": "brightness", "level": "dark",
         "features": feat(120.0, "C major", 520.0, 0.01, 0.9, 0.15)},
        {"sample_id": "brightness_high_00", "knob": "brightness", "level": "bright",
         "features": feat(120.0, "C major", 5000.0, 0.5, 0.9, 0.15)},
        {"sample_id": "brightness_high_01", "knob": "brightness", "level": "bright",
         "features": feat(120.0, "C major", 5200.0, 0.5, 0.9, 0.15)},
        {"sample_id": "active_rate_target_low_00", "knob": "active_rate_target",
         "level": "0.80-0.85", "features": feat(120.0, "C major", 2000.0, 0.1, 0.82, 0.15)},
        {"sample_id": "active_rate_target_low_01", "knob": "active_rate_target",
         "level": "0.80-0.85", "features": feat(120.0, "C major", 2000.0, 0.1, 0.84, 0.15)},
        {"sample_id": "active_rate_target_high_00", "knob": "active_rate_target",
         "level": "0.90-0.95", "features": feat(120.0, "C major", 2000.0, 0.1, 0.83, 0.15)},
        {"sample_id": "active_rate_target_high_01", "knob": "active_rate_target",
         "level": "0.90-0.95", "features": feat(120.0, "C major", 2000.0, 0.1, 0.85, 0.15)},
        {"sample_id": "valley_depth_target_low_00", "knob": "valley_depth_target",
         "level": "0.10-0.20", "features": feat(120.0, "C major", 2000.0, 0.1, 0.9, 0.15)},
        {"sample_id": "valley_depth_target_low_01", "knob": "valley_depth_target",
         "level": "0.10-0.20", "features": feat(120.0, "C major", 2000.0, 0.1, 0.9, 0.16)},
        {"sample_id": "valley_depth_target_high_00", "knob": "valley_depth_target",
         "level": "0.30-0.40", "features": feat(120.0, "C major", 2000.0, 0.1, 0.9, 0.17)},
        {"sample_id": "valley_depth_target_high_01", "knob": "valley_depth_target",
         "level": "0.30-0.40", "features": feat(120.0, "C major", 2000.0, 0.1, 0.9, 0.18)},
    ]
    return {
        "schema_version": "1.0",
        "fixture_id": "musicgen_matrix_synthetic_test",
        "generator": "musicgen-small-test",
        "repetitions": 2,
        "knobs": knobs,
        "samples": samples,
    }


def test_k3_musicgen_converter_is_deterministic() -> None:
    from scripts.build_k3_musicgen_fixture import convert

    raw = _musicgen_extract_synthetic_raw()
    assert convert(raw) == convert(raw)


def test_k3_musicgen_converter_computes_key_match_baseline() -> None:
    from scripts.build_k3_musicgen_fixture import BASELINE_KEY, convert
    from scripts.measure_grip import _key_match_score

    fixture = convert(_musicgen_extract_synthetic_raw())

    assert fixture["baseline_key"] == BASELINE_KEY
    for sample in fixture["samples"]:
        observed_key = sample["features"]["key"]
        expected = _key_match_score(BASELINE_KEY, observed_key)
        assert sample["features"]["key_match_baseline"] == pytest.approx(expected)
    # same-as-baseline samples must score a perfect match regardless of whether
    # mir_eval is installed (mir_eval path and the normalized-equality fallback
    # both return 1.0 for an exact match, per _key_match_score's docstring).
    key_low_sample = next(s for s in fixture["samples"] if s["sample_id"] == "key_low_00")
    assert key_low_sample["features"]["key_match_baseline"] == pytest.approx(1.0)


def test_k3_musicgen_converter_known_dead_only_on_target_knobs() -> None:
    from scripts.build_k3_musicgen_fixture import convert

    fixture = convert(_musicgen_extract_synthetic_raw())
    specs_by_name = {spec["name"]: spec for spec in fixture["knobs"]}

    assert specs_by_name["active_rate_target"].get("known_dead") is True
    assert specs_by_name["valley_depth_target"].get("known_dead") is True
    for name in ("bpm", "key", "brightness"):
        assert "known_dead" not in specs_by_name[name]


def test_k3_musicgen_converter_output_feeds_analyze_orthogonality() -> None:
    from scripts.build_k3_musicgen_fixture import convert

    fixture = convert(_musicgen_extract_synthetic_raw())
    report = analyze_orthogonality(fixture)

    assert report["knobs"] == [
        "bpm", "key", "brightness", "active_rate_target", "valley_depth_target",
    ]
    assert report["matrix"]["sensors_core"] == [
        "bpm", "key_match_baseline", "spectral_centroid", "active_rate", "valley_depth",
    ]
    assert report["matrix"]["sensors_extended"] == ["brightness_band_ratio"]
    assert set(report["noise"]["known_dead_knobs"]) == {
        "active_rate_target", "valley_depth_target",
    }


def test_k3_musicgen_converter_rejects_unexpected_source_knob_name() -> None:
    from scripts.build_k3_musicgen_fixture import convert

    raw = _musicgen_extract_synthetic_raw()
    raw["knobs"][0]["name"] = "tempo"  # should be "bpm"

    with pytest.raises(ValueError):
        convert(raw)


def test_k3_musicgen_converter_rejects_unexpected_source_sensor() -> None:
    from scripts.build_k3_musicgen_fixture import convert

    raw = _musicgen_extract_synthetic_raw()
    raw["knobs"][1]["sensor"] = "observed_key"  # should be "key"

    with pytest.raises(ValueError):
        convert(raw)


def test_k3_musicgen_converter_rejects_unexpected_expected_sign() -> None:
    from scripts.build_k3_musicgen_fixture import convert

    raw = _musicgen_extract_synthetic_raw()
    raw["knobs"][1]["expected_sign"] = 1  # key knob should declare -1

    with pytest.raises(ValueError):
        convert(raw)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_measure_orthogonality_cli_json_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(_synthetic_raw()), encoding="utf-8")

    exit_code = main(["--fixture", str(fixture_path), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == analyze_orthogonality(_synthetic_raw())


def test_measure_orthogonality_cli_markdown_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(_synthetic_raw()), encoding="utf-8")

    exit_code = main(["--fixture", str(fixture_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "# K3 orthogonality matrix" in out


def test_render_markdown_contains_matrix_header() -> None:
    report = analyze_orthogonality(_synthetic_raw())
    markdown = render_markdown(report)
    assert "# K3 orthogonality matrix" in markdown
    assert "## Effect-size matrix" in markdown


def test_render_markdown_no_ceiling_shows_none_phrasing() -> None:
    report = analyze_orthogonality(_synthetic_raw())
    markdown = render_markdown(report)
    assert "## Noise ceiling" in markdown
    assert "none — 既知 dead 行なし＝全セル unresolved（計器は有意性を主張できない）" in markdown


def test_render_markdown_with_ceiling_shows_legend_and_resolved_table() -> None:
    report = analyze_orthogonality(_synthetic_raw_with_known_dead())
    markdown = render_markdown(report)
    assert "## Noise ceiling" in markdown
    assert "* = ノイズ天井超え（既知 dead 行の経験的ヌル分布 max |d| を上回る）" in markdown
    assert "### Resolved cells" in markdown
    assert "| k1 | s1 |" in markdown
