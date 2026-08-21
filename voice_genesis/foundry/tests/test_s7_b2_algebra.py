"""test_s7_b2_algebra.py — B-2 裁定代数（§12-0-C）の worked example と不変条件。

B-2 の要件は「H0–H5 が**必ず三値へ機械変換できる**」ことなので、テストは
(a) worked example の再現、(b) 三値以外を返さないこと、(c) 除外セルを黙って
落とさないこと、(d) H5 が絶対値ではなく差分軸で裁定すること、を固定する。

実行: `python -m pytest voice_genesis/foundry/tests/test_s7_b2_algebra.py -q`
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUN8 = Path(__file__).resolve().parent.parent / "run8"
if str(_RUN8) not in sys.path:
    sys.path.insert(0, str(_RUN8))

import s7_b2_algebra as b2  # noqa: E402
import s7_spec as sp  # noqa: E402

VALID = {v.value for v in sp.Verdict}


def _ladder(speaker: str, values, generation: str = "run7", pitch_bin: str = "mid"):
    return [
        b2.Cell(f"{speaker}_l{lvl}_{pitch_bin}", speaker, generation, "P-RI-FINAL", z,
                ladder_level=lvl, pitch_bin=pitch_bin)
        for lvl, z in values
    ]


def test_worked_example_is_reproducible():
    doc = b2._worked_example()
    assert doc["H0"]["verdict"] == sp.Verdict.SUPPORTED.value
    assert doc["H0"]["delta_z"] == pytest.approx(1.20, abs=1e-9)
    assert doc["H1"]["series"]["user/mid"] == sp.Verdict.SUPPORTED.value
    assert doc["H1"]["series"]["pjs/mid"] == sp.Verdict.REFUTED.value
    # supported は「refuted が 0」を要求するので overall は undetermined
    assert doc["H1"]["verdict"] == sp.Verdict.UNDETERMINED.value
    assert doc["H4"]["verdict"] == sp.Verdict.SUPPORTED.value
    assert doc["H4"]["boundaries"]["user"] == 2.0
    assert doc["H4"]["boundaries"]["pjs"] == float("inf")


def test_h0_is_undetermined_without_the_prereg_matched_condition():
    """§4-2 の対（P-ANCHOR かぎり 対 P-RI-FINAL 4 拍・低）が無ければ裁定しない。"""
    cells = [
        b2.Cell("P-ANCHOR/kagiri", "ritsu", "run7", "P-ANCHOR", 1.6),
        # 4 拍・低ではない（2 拍・中）ので対にならない
        b2.Cell("P-RI-FINAL/x", "ritsu", "run7", "P-RI-FINAL", 0.4, ladder_level=2, pitch_bin="mid"),
    ]
    out = b2.evaluate_h0(cells, eps_z=0.3)
    assert out["verdict"] == sp.Verdict.UNDETERMINED.value


def test_all_hypotheses_return_three_valued_verdicts_on_empty_input():
    out = b2.evaluate_all([], eps_z=0.3, theta=0.5)
    assert set(out) == set(sp.B2_HYPOTHESES)
    assert all(v["verdict"] in VALID for v in out.values())
    assert all(v["verdict"] == sp.Verdict.UNDETERMINED.value for v in out.values())


def test_excluded_cells_are_counted_not_silently_dropped():
    cells = _ladder("user", [(1, 0.1), (2, 0.7), (4, 1.4)])
    cells.append(
        b2.Cell("u_bad", "user", "run7", "P-RI-FINAL", 9.9, ladder_level=8, pitch_bin="mid",
                status=sp.STATUS_RINGING_UNCORRECTED_GROUP)
    )
    out = b2.evaluate_h1(cells, eps_z=0.3)
    assert out["excluded"] == {sp.STATUS_RINGING_UNCORRECTED_GROUP: 1}
    assert out["series"]["user/mid"] == sp.Verdict.SUPPORTED.value


def test_h1_refuted_when_span_within_epsilon():
    flat = _ladder("pjs", [(1, 0.05), (2, 0.10), (4, 0.12)])
    assert b2._series_ladder_verdict(flat, eps_z=0.3) == sp.Verdict.REFUTED.value


def test_h1_needs_three_ladder_points():
    two = _ladder("user", [(1, 0.1), (4, 1.4)])
    assert b2._series_ladder_verdict(two, eps_z=0.3) == sp.Verdict.UNDETERMINED.value


def test_h3_refuted_when_controls_break_equally():
    def group(speaker, ri, controls):
        cells = [b2.Cell(f"{speaker}_ri", speaker, "run7", "P-RI-FINAL", ri)]
        for probe, z in controls:
            cells.append(b2.Cell(f"{speaker}_{probe}", speaker, "run7", probe, z))
        return cells

    cells = []
    for speaker in ("user", "pjs"):
        cells += group(speaker, 1.0, [("P-I-FINAL", 1.0), ("P-N-FINAL", 0.95), ("P-SU-FINAL", 1.05)])
    out = b2.evaluate_h3(cells, eps_z=0.3)
    assert out["verdict"] == sp.Verdict.REFUTED.value


def test_h5_uses_contrast_not_absolute_level():
    """全セルが一様にシフトしても H5 は動かない（§2-3 H5 = 差分軸のみ）。"""

    def speaker_cells(gen: str, offset: float):
        out = []
        for i in range(3):
            out.append(b2.Cell(f"t{gen}{i}", "user", gen, "P-RI-FINAL", 1.0 + offset))
            out.append(b2.Cell(f"c{gen}{i}", "user", gen, "P-N-FINAL", 0.0 + offset))
        return out

    uniform = speaker_cells("run5", 0.0) + speaker_cells("run7", 5.0)
    out = b2.evaluate_h5(uniform, eps_z=0.3)
    assert out["detail"]["user"]["spread_z"] == pytest.approx(0.0)
    assert out["series"]["user"] == sp.Verdict.REFUTED.value

    moved = speaker_cells("run5", 0.0)
    moved += [b2.Cell(f"t7{i}", "user", "run7", "P-RI-FINAL", 2.0) for i in range(3)]
    moved += [b2.Cell(f"c7{i}", "user", "run7", "P-N-FINAL", 0.0) for i in range(3)]
    out2 = b2.evaluate_h5(moved, eps_z=0.3)
    assert out2["detail"]["user"]["spread_z"] == pytest.approx(1.0)
    assert out2["series"]["user"] == sp.Verdict.SUPPORTED.value


def test_h4_compares_within_one_generation_only():
    cells = _ladder("user", [(1, 0.1), (2, 0.7), (4, 1.4)], generation="run5")
    cells += _ladder("pjs", [(1, 0.0), (2, 0.1), (4, 0.2)], generation="run7")
    out = b2.evaluate_h4(cells, theta=0.5)
    assert out["verdict"] == sp.Verdict.UNDETERMINED.value
    assert out["reason"] == "generation_not_pinned_for_speaker_comparison"


def test_majority_rule_matches_section_3():
    assert b2._majority([sp.Verdict.SUPPORTED.value]) == sp.Verdict.UNDETERMINED.value
    assert (
        b2._majority([sp.Verdict.SUPPORTED.value] * 2) == sp.Verdict.SUPPORTED.value
    )
    assert (
        b2._majority([sp.Verdict.SUPPORTED.value] * 2 + [sp.Verdict.REFUTED.value])
        == sp.Verdict.UNDETERMINED.value
    )
    assert (
        b2._majority([sp.Verdict.REFUTED.value] * 2 + [sp.Verdict.SUPPORTED.value])
        == sp.Verdict.REFUTED.value
    )
