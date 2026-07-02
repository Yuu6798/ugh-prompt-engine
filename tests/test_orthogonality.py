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
    normalized_entropy,
)

K3_FIXTURE_PATH = Path("examples/control/k3/synth_performer_matrix_fixture.json")
K3_EXPECTED_PATH = Path("examples/control/k3/expected_orthogonality.json")


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
# Snapshot test (fixture generated by scripts/build_k3_fixture.py)
# --------------------------------------------------------------------------


def test_k3_fixture_snapshot() -> None:
    from scripts.measure_grip import load_fixture

    report = analyze_orthogonality(load_fixture(K3_FIXTURE_PATH))
    expected = json.loads(K3_EXPECTED_PATH.read_text(encoding="utf-8"))

    assert report == expected


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
