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

_ARTIFACTS = Path(__file__).resolve().parent.parent / "results_s7"

VALID = {v.value for v in sp.Verdict}
EPS = b2.Epsilon.uniform(0.3, reason="テストは 1 世代・1 群だけの合成セルを使う")


def _ladder(speaker: str, values, generation: str = "run7", pitch_bin: str = "mid"):
    return [
        b2.Cell(f"{speaker}_l{lvl}_{pitch_bin}", speaker, generation, "P-RI-FINAL", z,
                ladder_level=lvl, pitch_bin=pitch_bin)
        for lvl, z in values
    ]


def test_worked_example_is_reproducible():
    doc = b2._worked_example()
    assert doc["H0"]["verdict"] == sp.Verdict.SUPPORTED.value
    assert doc["H0"]["detail"]["ritsu/run7"]["delta_z"] == pytest.approx(1.20, abs=1e-9)
    assert doc["H1"]["series"]["user/run7/mid"] == sp.Verdict.SUPPORTED.value
    assert doc["H1"]["series"]["pjs/run7/mid"] == sp.Verdict.REFUTED.value
    # supported は「refuted が 0」を要求するので overall は undetermined
    assert doc["H1"]["verdict"] == sp.Verdict.UNDETERMINED.value
    assert doc["H4"]["verdict"] == sp.Verdict.SUPPORTED.value
    assert doc["H4"]["boundaries"]["user"] == 2.0
    # 「一度も越えない」は Infinity ではなく null + status 語彙で表す
    assert doc["H4"]["boundaries"]["pjs"] is None
    assert doc["H4"]["boundary_status"]["pjs"] == b2.BOUNDARY_NEVER_CROSSED
    assert doc["H4"]["boundary_status"]["user"] == b2.BOUNDARY_CROSSED


def test_h0_is_undetermined_without_the_prereg_matched_condition():
    """§4-2 の対（P-ANCHOR かぎり 対 P-RI-FINAL 4 拍・低）が無ければ裁定しない。"""
    cells = [
        b2.Cell("P-ANCHOR/kagiri", "ritsu", "run7", "P-ANCHOR", 1.6),
        # 4 拍・低ではない（2 拍・中）ので対にならない
        b2.Cell("P-RI-FINAL/x", "ritsu", "run7", "P-RI-FINAL", 0.4, ladder_level=2, pitch_bin="mid"),
    ]
    out = b2.evaluate_h0(cells, eps_z=EPS)
    assert out["verdict"] == sp.Verdict.UNDETERMINED.value


def test_all_hypotheses_return_three_valued_verdicts_on_empty_input():
    out = b2.evaluate_all([], eps_z=EPS, theta=0.5)
    assert set(out) == set(sp.B2_HYPOTHESES)
    assert all(v["verdict"] in VALID for v in out.values())
    assert all(v["verdict"] == sp.Verdict.UNDETERMINED.value for v in out.values())


def test_excluded_cells_are_counted_not_silently_dropped():
    cells = _ladder("user", [(1, 0.1), (2, 0.7), (4, 1.4)])
    cells.append(
        b2.Cell("u_bad", "user", "run7", "P-RI-FINAL", 9.9, ladder_level=8, pitch_bin="mid",
                status=sp.STATUS_RINGING_UNCORRECTED_GROUP)
    )
    out = b2.evaluate_h1(cells, eps_z=EPS)
    assert out["excluded"] == {sp.STATUS_RINGING_UNCORRECTED_GROUP: 1}
    assert out["series"]["user/run7/mid"] == sp.Verdict.SUPPORTED.value


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
    out = b2.evaluate_h3(cells, eps_z=EPS)
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
    out = b2.evaluate_h5(uniform, eps_z=EPS)
    assert out["detail"]["user"]["spread_z"] == pytest.approx(0.0)
    assert out["series"]["user"] == sp.Verdict.REFUTED.value

    moved = speaker_cells("run5", 0.0)
    moved += [b2.Cell(f"t7{i}", "user", "run7", "P-RI-FINAL", 2.0) for i in range(3)]
    moved += [b2.Cell(f"c7{i}", "user", "run7", "P-N-FINAL", 0.0) for i in range(3)]
    out2 = b2.evaluate_h5(moved, eps_z=EPS)
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


def test_scalar_epsilon_is_rejected():
    """スカラー 1 個を全群へ当てる誤用を型で塞ぐ（PR #300 Codex P1）。"""
    with pytest.raises(TypeError):
        b2.evaluate_h1(_ladder("user", [(1, 0.1), (2, 0.7), (4, 1.4)]), eps_z=0.3)
    with pytest.raises(ValueError):
        b2.Epsilon.uniform(0.3, reason="")


def test_epsilon_is_looked_up_per_speaker_generation_group():
    eps = b2.Epsilon.per_group({("user", "run7"): 0.30, ("user", "run5"): 5.00})
    cells = _ladder("user", [(1, 0.1), (2, 0.7), (4, 1.4)], generation="run7")
    cells += _ladder("user", [(1, 0.1), (2, 0.7), (4, 1.4)], generation="run5")
    out = b2.evaluate_h1(cells, eps_z=eps)
    # 同じ数値の系列でも、群ごとの ε が違えば裁定が変わる
    assert out["series"]["user/run7/mid"] == sp.Verdict.SUPPORTED.value
    assert out["series"]["user/run5/mid"] == sp.Verdict.REFUTED.value
    assert out["eps_z_by_series"]["user/run5/mid"] == 5.0


def test_missing_group_epsilon_is_undetermined_not_a_default():
    eps = b2.Epsilon.per_group({("pjs", "run7"): 0.30})
    out = b2.evaluate_h1(_ladder("user", [(1, 0.1), (2, 0.7), (4, 1.4)]), eps_z=eps)
    assert out["series"]["user/run7/mid"] == sp.Verdict.UNDETERMINED.value


def test_h1_series_do_not_merge_generations():
    """run5 と run7 が 1 本のラダーに混ざらない（PR #300 Codex P1）。"""
    cells = _ladder("user", [(1, 0.1), (2, 0.7), (4, 1.4)], generation="run5")
    cells += _ladder("user", [(1, 0.2), (2, 0.8), (4, 1.5)], generation="run7")
    out = b2.evaluate_h1(cells, eps_z=EPS)
    assert set(out["series"]) == {"user/run5/mid", "user/run7/mid"}


def test_h2_supports_only_when_low_pitch_is_the_worse_side():
    """high の方が悪いケースを supported にしない（PR #300 Codex P2）。"""

    def pair(low_z: float, high_z: float):
        return [
            b2.Cell("lo", "user", "run7", "P-RI-FINAL", low_z, ladder_level=2, pitch_bin="low"),
            b2.Cell("hi", "user", "run7", "P-RI-FINAL", high_z, ladder_level=2, pitch_bin="high"),
        ]

    low_worse = b2.evaluate_h2(pair(2.0, 0.0), eps_z=EPS)
    assert low_worse["series"]["user/run7/L2"] == sp.Verdict.SUPPORTED.value

    high_worse = b2.evaluate_h2(pair(0.0, 2.0), eps_z=EPS)
    assert high_worse["series"]["user/run7/L2"] == sp.Verdict.REFUTED.value
    assert high_worse["detail"]["user/run7/L2"]["reason"] == "opposite_direction"

    flat = b2.evaluate_h2(pair(0.1, 0.0), eps_z=EPS)
    assert flat["detail"]["user/run7/L2"]["reason"] == "no_difference"


def test_h0_is_adjudicated_per_group_not_pooled():
    """対称な 2 群がプールで打ち消し合う偽 refuted を塞ぐ（PR #300 Codex 第 2 巡 P1）。"""
    cells = [
        b2.Cell("P-ANCHOR/kagiri/a", "spk_a", "run7", "P-ANCHOR", +2.0),
        b2.Cell("P-RI-FINAL/a", "spk_a", "run7", "P-RI-FINAL", +1.0, ladder_level=4, pitch_bin="low"),
        b2.Cell("P-ANCHOR/kagiri/b", "spk_b", "run7", "P-ANCHOR", -2.0),
        b2.Cell("P-RI-FINAL/b", "spk_b", "run7", "P-RI-FINAL", -1.0, ladder_level=4, pitch_bin="low"),
    ]
    out = b2.evaluate_h0(cells, eps_z=b2.Epsilon.uniform(0.5, reason="単一世代の合成例"))
    assert out["series"] == {
        "spk_a/run7": sp.Verdict.SUPPORTED.value,
        "spk_b/run7": sp.Verdict.SUPPORTED.value,
    }
    assert out["verdict"] == sp.Verdict.SUPPORTED.value


def test_h4_is_adjudicated_per_generation_in_evaluate_all():
    """世代を束ねて渡しても H4 が構造的 undetermined にならない（PR #300 Codex 第 2 巡 P1）。"""
    cells = []
    for gen in ("run5", "run6", "run7"):
        cells += _ladder("user", [(1, 0.1), (2, 0.7), (4, 1.4)], generation=gen)
        cells += _ladder("pjs", [(1, 0.0), (2, 0.1), (4, 0.2)], generation=gen)
    out = b2.evaluate_all(cells, eps_z=EPS, theta=0.5)["H4"]
    assert set(out["per_generation"]) == {"run5", "run6", "run7"}
    assert out["verdict"] == sp.Verdict.SUPPORTED.value


def test_artifacts_never_contain_infinity_or_nan():
    """canonical JSON へ非有限値を書かない（PR #300 最終巡 未適用指摘 1）。"""
    import json

    import s7_io

    doc = b2.build_algebra_doc(
        json.loads((_ARTIFACTS / "trf_measurement_spec.json").read_text(encoding="utf-8")),
        _ARTIFACTS / "trf_measurement_spec.json",
        "0" * 64,
        123,
    )
    s7_io.assert_json_finite(doc)                     # 走査して例外が出ないこと
    assert "Infinity" not in json.dumps(doc)
    assert "NaN" not in json.dumps(doc)

    with pytest.raises(s7_io.NonFiniteArtifactError):
        s7_io.assert_json_finite({"a": [1.0, float("inf")]})


def test_b2_artifact_requires_the_consumed_spec_sha():
    """ε の出どころ（B-1 spec のバイト列）を pin しないと凍結しない（未適用指摘 2）。"""
    import json

    spec = json.loads((_ARTIFACTS / "trf_measurement_spec.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="sha256"):
        b2.build_algebra_doc(spec, _ARTIFACTS / "trf_measurement_spec.json", "", 0)

    doc = b2.build_algebra_doc(spec, _ARTIFACTS / "trf_measurement_spec.json", "a" * 64, 42)
    assert doc["consumed_b1_spec"]["sha256"] == "a" * 64
    assert doc["consumed_b1_spec"]["bytes"] == 42


def test_frozen_b2_artifact_pins_the_spec_on_disk():
    """コミット済みの B-2 成果物が、実在する spec のバイト列を指していること。"""
    import json

    import s7_io

    doc = json.loads((_ARTIFACTS / "s7_b2_verdict_algebra.json").read_text(encoding="utf-8"))
    _spec, sha, n = s7_io.read_json_with_pin(_ARTIFACTS / "trf_measurement_spec.json")
    assert doc["consumed_b1_spec"]["sha256"] == sha
    assert doc["consumed_b1_spec"]["bytes"] == n
