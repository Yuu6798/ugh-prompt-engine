"""`campaign.diagnose`（RUN10-CAL-v1.2 WP4 C-1 探索ステージ）のテスト。

armed campaign を一切経由しない cheap gate であることを検証する 3 本柱:

1. セル選抜（`select_diagnostic_cells`）の決定性・上限・control_class 被覆。
2. 判定（`evaluate_candidate`）の verdict 4 分岐 + sanctioned abstention 計上
   （合成 `MeterOutput` のみで検証——render/measure を一切呼ばない）。
3. `campaigns/`・`~/.vg_cal/`・ledger への書き込みゼロ（`@pytest.mark.slow`
   の 1 本だけ実 render/measure を経由する）。
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path

import pytest

from voice_genesis.calibration.campaign import diagnose
from voice_genesis.calibration.candidates import registry
from voice_genesis.calibration.candidates.adapter import MeterOutput
from voice_genesis.calibration.fixtures import matrix
from voice_genesis.calibration.fixtures.axes import FixtureFamily
from voice_genesis.calibration.vocab import ClaimCeiling, MissingReason

_TILT_FAMILY = FixtureFamily.TILT_GT.value
_F0_FAMILY = FixtureFamily.F0_CONTROL.value

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _candidate(candidate_id: str, **overrides: object) -> registry.Candidate:
    return dataclasses.replace(registry.candidate_by_id(candidate_id), **overrides)


# ---------------------------------------------------------------------------
# セル選抜: 決定性・上限・control_class 被覆
# ---------------------------------------------------------------------------


def test_select_diagnostic_cells_is_deterministic() -> None:
    first = diagnose.select_diagnostic_cells(_TILT_FAMILY, 30)
    second = diagnose.select_diagnostic_cells(_TILT_FAMILY, 30)
    assert [(mr.row_id, role) for mr, role in first] == [(mr.row_id, role) for mr, role in second]


@pytest.mark.parametrize("family", [f.value for f in FixtureFamily])
@pytest.mark.parametrize("max_cells", [6, 12, 30])
def test_select_diagnostic_cells_respects_max_cells(family: str, max_cells: int) -> None:
    cells = diagnose.select_diagnostic_cells(family, max_cells)
    assert len(cells) <= max_cells
    row_ids = [mr.row_id for mr, _ in cells]
    assert len(row_ids) == len(set(row_ids)), "no cell chosen twice"


def test_select_diagnostic_cells_positive_budget_is_half_max_cells() -> None:
    cells = diagnose.select_diagnostic_cells(_TILT_FAMILY, 30)
    positive = [mr for mr, role in cells if role == "positive"]
    assert len(positive) <= 30 // 2
    # TILT_GT の TRUTH_CORE truth level (slope_db_per_oct) は budget 未満しか
    # 無いため、この family では budget に達しない（重複排除された level 数
    # と一致すること）。
    levels = {matrix.truth_identity_for_row(mr.row) for mr in positive}
    assert len(levels) == len(positive)


def test_select_diagnostic_cells_negative_covers_applicable_control_classes() -> None:
    cells = diagnose.select_diagnostic_cells(_TILT_FAMILY, 30)
    negative_classes = {mr.row.control_class for mr, role in cells if role == "negative"}
    full = [mr for mr in matrix.build_matrix() if mr.row.family == _TILT_FAMILY]
    expected_classes = {mr.row.control_class for mr in full if mr.row.control_class is not None}
    assert negative_classes == expected_classes
    # control_class ごとに先頭 1 行のみ選ばれること。
    negative_row_ids = [mr.row_id for mr, role in cells if role == "negative"]
    assert len(negative_row_ids) == len(negative_classes)


def test_select_diagnostic_cells_confound_rows_are_single_axis_nuisance_only() -> None:
    cells = diagnose.select_diagnostic_cells(_TILT_FAMILY, 30)
    confound = [mr for mr, role in cells if role == "confound"]
    assert confound, "TILT_GT has spare budget at max_cells=30 to pick confound rows"
    for mr in confound:
        assert mr.row.block == "CONFOUND"
        assert matrix.single_axis_nuisance_tag_axis(mr.row) is not None


def test_select_diagnostic_cells_small_budget_drops_confound_first() -> None:
    # max_cells=4 (< positive 5 + negative 2) には confound 用の余りが無い。
    cells = diagnose.select_diagnostic_cells(_TILT_FAMILY, 4)
    assert len(cells) <= 4
    assert all(role != "confound" for _, role in cells)


# ---------------------------------------------------------------------------
# 判定: verdict 4 分岐 + sanctioned abstention（合成 MeterOutput のみ）
# ---------------------------------------------------------------------------


def _outcome(role: str, control_class: str | None, output: MeterOutput, reason: str | None = None):
    if reason is None and output.missing_reason is not None:
        reason = output.missing_reason.value
    return diagnose.CellOutcome(role=role, control_class=control_class, output=output, missing_reason=reason)


def test_evaluate_candidate_pass_when_positive_fires_and_negative_silent() -> None:
    candidate = _candidate("M2T-HARMONIC-OLS-K4-WINHANN", claim_ceiling=ClaimCeiling.ABSOLUTE)
    outcomes = [
        _outcome("positive", None, MeterOutput(values={"tilt_db_per_oct": -6.0})),
        _outcome("positive", None, MeterOutput(values={"tilt_db_per_oct": -8.0})),
        _outcome("negative", "SILENCE", MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING)),
        _outcome("negative", "NOISE_ONLY", MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING)),
    ]
    report = diagnose.evaluate_candidate(candidate, outcomes)
    assert report["positive_fire_rate"] == 1.0
    assert report["negative_fire_rate"] == 0.0
    assert report["verdict"] == "PASS"


def test_evaluate_candidate_fail_positive_when_a_positive_does_not_fire() -> None:
    candidate = _candidate("M2T-HARMONIC-OLS-K4-WINHANN", claim_ceiling=ClaimCeiling.ABSOLUTE)
    outcomes = [
        _outcome("positive", None, MeterOutput(values={"tilt_db_per_oct": -6.0})),
        _outcome("positive", None, MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING)),
        _outcome("negative", "SILENCE", MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING)),
    ]
    report = diagnose.evaluate_candidate(candidate, outcomes)
    assert report["positive_fire_rate"] == 0.5
    assert report["verdict"] == "FAIL_POSITIVE"


def test_evaluate_candidate_fail_negative_when_a_negative_fires() -> None:
    candidate = _candidate("M2T-HARMONIC-OLS-K4-WINHANN", claim_ceiling=ClaimCeiling.ABSOLUTE)
    outcomes = [
        _outcome("positive", None, MeterOutput(values={"tilt_db_per_oct": -6.0})),
        _outcome("negative", "SILENCE", MeterOutput(values={"tilt_db_per_oct": -1.0})),
        _outcome("negative", "NOISE_ONLY", MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING)),
    ]
    report = diagnose.evaluate_candidate(candidate, outcomes)
    assert report["positive_fire_rate"] == 1.0
    assert report["negative_fire_rate"] == 0.5
    assert report["negative_fire_by_control_class"]["SILENCE"] == 1.0
    assert report["negative_fire_by_control_class"]["NOISE_ONLY"] == 0.0
    assert report["verdict"] == "FAIL_NEGATIVE"


def test_evaluate_candidate_no_ceiling_when_registry_ceiling_is_none() -> None:
    candidate = _candidate("M2T-B0-CURRENT-HYBRID")
    assert candidate.claim_ceiling == ClaimCeiling.NONE
    outcomes = [
        _outcome("positive", None, MeterOutput(values={"value": 12.0})),
        _outcome("negative", "SILENCE", MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING)),
    ]
    report = diagnose.evaluate_candidate(candidate, outcomes)
    assert report["positive_fire_rate"] == 1.0
    assert report["negative_fire_rate"] == 0.0
    assert report["verdict"] == "NO_CEILING"


def test_evaluate_candidate_not_evaluable_with_no_positive_instances() -> None:
    candidate = _candidate("M2T-HARMONIC-OLS-K4-WINHANN", claim_ceiling=ClaimCeiling.ABSOLUTE)
    outcomes = [
        _outcome("negative", "SILENCE", MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING)),
    ]
    report = diagnose.evaluate_candidate(candidate, outcomes)
    assert report["positive_fire_rate"] is None
    assert report["verdict"] == "NOT_EVALUABLE"


def test_evaluate_candidate_not_evaluable_when_all_ineligible() -> None:
    candidate = _candidate("M2A-D4C-BAND-BROADBAND", claim_ceiling=ClaimCeiling.DIAGNOSTIC_ONLY)
    outcomes = [
        _outcome(
            "positive",
            None,
            MeterOutput(ineligible=True, ineligible_reason="INELIGIBLE_DEPENDENCY_ABSENT"),
        ),
        _outcome(
            "negative",
            "SILENCE",
            MeterOutput(ineligible=True, ineligible_reason="INELIGIBLE_DEPENDENCY_ABSENT"),
        ),
    ]
    report = diagnose.evaluate_candidate(candidate, outcomes)
    assert report["verdict"] == "NOT_EVALUABLE"


def test_evaluate_candidate_sanctioned_abstention_is_counted_separately() -> None:
    candidate = _candidate("M2T-HARMONIC-OLS-K4-WINHANN", claim_ceiling=ClaimCeiling.ABSOLUTE)
    outcomes = [
        _outcome("positive", None, MeterOutput(values={"tilt_db_per_oct": -6.0})),
        # F0 prepass unusable on SILENCE: sanctioned (SILENCE, F0_UNUSABLE).
        _outcome("negative", "SILENCE", MeterOutput(), reason=diagnose.F0_UNUSABLE_REASON),
        # same reason string on a non-sanctioned control_class must NOT count.
        _outcome("negative", "NOISE_ONLY", MeterOutput(), reason=diagnose.F0_UNUSABLE_REASON),
    ]
    report = diagnose.evaluate_candidate(candidate, outcomes)
    assert report["sanctioned_abstentions"] == 1
    assert report["missing_by_reason"] == {"F0_UNUSABLE": 2}
    # sanctioned abstention still contributes zero to the fire rate (correct
    # non-detection), so it does not itself flip the verdict.
    assert report["negative_fire_rate"] == 0.0
    assert report["verdict"] == "PASS"


def test_evaluate_candidate_confound_outcomes_excluded_from_rates() -> None:
    candidate = _candidate("M2T-HARMONIC-OLS-K4-WINHANN", claim_ceiling=ClaimCeiling.ABSOLUTE)
    outcomes = [
        _outcome("positive", None, MeterOutput(values={"tilt_db_per_oct": -6.0})),
        _outcome("negative", "SILENCE", MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING)),
        # a confound cell that would otherwise look like a "missing positive"
        # must not change positive_fire_rate.
        _outcome("confound", None, MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING)),
    ]
    report = diagnose.evaluate_candidate(candidate, outcomes)
    assert report["positive_fire_rate"] == 1.0
    assert report["missing_by_reason"] == {"OUTPUT_MISSING": 2}
    assert report["verdict"] == "PASS"


# ---------------------------------------------------------------------------
# measure_cell: F0-unusable skip 合成
# ---------------------------------------------------------------------------


def test_measure_cell_skips_call_and_synthesizes_f0_unusable(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = _candidate("M2T-HARMONIC-OLS-K4-WINHANN")
    assert diagnose.needs_f0_injection(candidate)

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("run_within_process_calls must not be called when f0 is unusable")

    monkeypatch.setattr(
        "voice_genesis.calibration.campaign.measure_stage.run_within_process_calls", _boom
    )
    import numpy as np

    outcome = diagnose.measure_cell(
        candidate, "negative", "SILENCE", np.zeros(100), 24000, None, "row-x", 0
    )
    assert outcome.missing_reason == diagnose.F0_UNUSABLE_REASON
    assert outcome.output == MeterOutput()


# ---------------------------------------------------------------------------
# ゼロ書き込み保証: campaigns/ と ~/.vg_cal/ に一切触れない
# ---------------------------------------------------------------------------


def test_run_diagnosis_writes_nothing_under_campaigns_or_vg_cal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    before = subprocess.run(
        ["git", "status", "--short", "--", "voice_genesis/calibration/campaigns"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    candidates = [registry.candidate_by_id("M2T-B0-CURRENT-HYBRID")]
    report = diagnose.run_diagnosis(_TILT_FAMILY, candidates, 12, 1)

    after = subprocess.run(
        ["git", "status", "--short", "--", "voice_genesis/calibration/campaigns"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert before == after
    assert not (fake_home / ".vg_cal").exists()
    assert report["schema"] == "diagnose/0.1"
    assert report["claimable"] is False


def test_cli_out_writes_only_the_requested_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    out_path = tmp_path / "diag.json"

    exit_code = diagnose.main(
        [
            "--family",
            _TILT_FAMILY,
            "--candidate",
            "M2T-B0-CURRENT-HYBRID",
            "--max-cells",
            "8",
            "--out",
            str(out_path),
        ]
    )
    capsys.readouterr()

    assert exit_code == 0
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["family"] == _TILT_FAMILY
    assert payload["claimable"] is False
    assert not (fake_home / ".vg_cal").exists()


def test_cli_rejects_unknown_candidate_id(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = diagnose.main(["--family", _TILT_FAMILY, "--candidate", "NOT-A-REAL-CANDIDATE"])
    out = capsys.readouterr().out
    assert exit_code == 1
    assert json.loads(out)["result"] == "ERROR"


def test_cli_rejects_candidate_from_a_different_family(capsys: pytest.CaptureFixture[str]) -> None:
    # F0-B0-CURRENT belongs to F0_CONTROL's meter, not TILT_GT's.
    exit_code = diagnose.main(["--family", _TILT_FAMILY, "--candidate", "F0-B0-CURRENT"])
    out = capsys.readouterr().out
    assert exit_code == 1
    assert json.loads(out)["result"] == "ERROR"


def test_candidates_for_family_default_matches_registry_meter() -> None:
    tilt_candidates = diagnose.candidates_for_family(_TILT_FAMILY)
    assert {c.candidate_id for c in tilt_candidates} == {
        c.candidate_id for c in registry.candidates_for_meter(diagnose.FAMILY_TO_METER[_TILT_FAMILY])
    }
    # IDENTITY_CAUSAL_SWEEP has no directly-diagnosable meter (M6 is a
    # cross-meter distance) — default candidate set is empty.
    assert diagnose.candidates_for_family(FixtureFamily.IDENTITY_CAUSAL_SWEEP.value) == ()


# ---------------------------------------------------------------------------
# 実 render/measure を伴う 1 本のみ slow（F0_CONTROL は F0 依存候補が無く軽い）
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_cli_real_render_measure_f0_control(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    exit_code = diagnose.main(
        ["--family", _F0_FAMILY, "--max-cells", "6", "--repeats", "1"]
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    report = json.loads(out)
    assert report["schema"] == "diagnose/0.1"
    assert report["family"] == _F0_FAMILY
    assert report["claimable"] is False
    assert len(report["cells"]) <= 6
    assert report["candidates"], "F0_CONTROL has 5 registry candidates"
    for candidate_report in report["candidates"]:
        assert candidate_report["verdict"] in {
            "PASS",
            "FAIL_POSITIVE",
            "FAIL_NEGATIVE",
            "NO_CEILING",
            "NOT_EVALUABLE",
        }
    assert not (fake_home / ".vg_cal").exists()
