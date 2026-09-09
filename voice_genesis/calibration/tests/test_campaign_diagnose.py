"""`campaign.diagnose`（RUN10-CAL-v1.2 WP4/WP4b C-1 探索ステージ）のテスト。

armed campaign を一切経由しない cheap gate であることを検証する 4 本柱:

1. セル選抜（`select_diagnostic_cells`）の決定性・上限・control_class 被覆。
2. 判定（`evaluate_candidate`）の verdict 5 分岐（WP4b で
   `NOT_EVALUABLE(negative_controls_incomplete)` が新設）+ sanctioned
   abstention 計上（合成 `MeterOutput` のみで検証——render/measure を
   一切呼ばない）。
3. F0 prepass 候補の掃引（`run_diagnosis` の `f0_prepass`/`--f0-candidate`）
   の決定性・限定（WP4b 新設）。
4. `campaigns/`・`~/.vg_cal/`・ledger への書き込みゼロ（`@pytest.mark.slow`
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
    """registry 実候補のコピー。**`detection_predicate` は既定で外す** — 本
    ファイルの verdict 分岐テストは合成 `MeterOutput` に primary output だけを
    載せるため、v1.3 §X1 が TILT harmonic 候補へ宣言した
    `hnr_acf_db >= -5.0` をそのまま適用すると「補助フィールドが無いので全件
    non-fire」という別の理由でしか判定できなくなる（検査対象は fire 定義では
    なく verdict 分岐の算術）。registry 宣言が diagnose の判定へ実際に伝播する
    ことは `test_evaluate_candidate_uses_the_registry_declared_predicate` が
    別途固定する。`overrides` で明示すればそちらが優先される。"""
    return dataclasses.replace(
        registry.candidate_by_id(candidate_id), **{"detection_predicate": None, **overrides}
    )


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


def test_evaluate_candidate_uses_the_registry_declared_predicate() -> None:
    """v1.3 §X1: registry が宣言した `hnr_acf_db >= -5.0` が C-1 診断の
    fire 判定へそのまま効くこと（`_candidate()` が predicate を外している分の
    往復側）。`tilt_db_per_oct` は正例・負例とも有限値を載せる——predicate 無し
    の既定分岐なら両方 fire になる組み合わせで、`hnr_acf_db` の値だけが
    正例を fire・負例を non-fire に分ける（WP-A 実測レンジの代表値
    -1.0 dB / -9.8 dB）。"""
    candidate = registry.candidate_by_id("M2T-HARMONIC-OLS-K4-WINHANN")
    assert candidate.detection_predicate is not None
    assert candidate.detection_predicate.field == "hnr_acf_db"
    assert candidate.detection_predicate.min_value == -5.0

    outcomes = [
        _outcome(
            "positive", None,
            MeterOutput(values={"tilt_db_per_oct": -6.0, "hnr_acf_db": -1.0}),
        ),
        _outcome(
            "negative", "SILENCE",
            MeterOutput(values={"tilt_db_per_oct": 4.5, "hnr_acf_db": -9.8}),
        ),
        _outcome(
            "negative", "NOISE_ONLY",
            MeterOutput(values={"tilt_db_per_oct": 1.5, "hnr_acf_db": -9.9}),
        ),
    ]
    report = diagnose.evaluate_candidate(candidate, outcomes)
    assert report["positive_fire_rate"] == 1.0
    assert report["negative_fire_rate"] == 0.0
    assert report["verdict"] == "PASS"

    # 補助フィールドが欠けた出力は（primary output があっても）非発火。
    missing_hnr = [
        _outcome("positive", None, MeterOutput(values={"tilt_db_per_oct": -6.0})),
        _outcome("negative", "SILENCE", MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING)),
        _outcome(
            "negative", "NOISE_ONLY", MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING)
        ),
    ]
    degraded = diagnose.evaluate_candidate(candidate, missing_hnr)
    assert degraded["positive_fire_rate"] == 0.0
    assert degraded["verdict"] == "FAIL_POSITIVE"


def test_evaluate_candidate_dump_values_records_raw_cells_without_changing_verdict() -> None:
    """`--dump-values`（schema v0.3）: セルごとの生 `values` 全フィールドと
    `missing_reason`/`ineligible` が記録され、既定（フラグ無し）は従来どおり
    `cell_values` を持たない。判定（fire rate・verdict）は両者で同一。"""
    candidate = _candidate("M2T-HARMONIC-OLS-K4-WINHANN", claim_ceiling=ClaimCeiling.ABSOLUTE)
    outcomes = [
        diagnose.CellOutcome(
            role="positive",
            control_class=None,
            output=MeterOutput(values={"tilt_db_per_oct": -6.0}),
            missing_reason=None,
            row_id="ROW-P1",
            probe_index=0,
        ),
        diagnose.CellOutcome(
            role="negative",
            control_class="SILENCE",
            output=MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING),
            missing_reason=MissingReason.OUTPUT_MISSING.value,
            row_id="ROW-N1",
            probe_index=0,
        ),
    ]

    baseline = diagnose.evaluate_candidate(candidate, outcomes)
    dumped = diagnose.evaluate_candidate(candidate, outcomes, dump_values=True)

    assert "cell_values" not in baseline
    assert {k: v for k, v in dumped.items() if k != "cell_values"} == baseline

    entries = dumped["cell_values"]
    assert [e["row_id"] for e in entries] == ["ROW-P1", "ROW-N1"]
    assert entries[0] == {
        "row_id": "ROW-P1",
        "probe_index": 0,
        "role": "positive",
        "control_class": None,
        "values": {"tilt_db_per_oct": -6.0},
        "missing_reason": None,
        "ineligible": False,
        "ineligible_reason": None,
        "detected": True,
    }
    assert entries[1]["values"] == {}
    assert entries[1]["missing_reason"] == MissingReason.OUTPUT_MISSING.value
    assert entries[1]["detected"] is False
    # JSON 直列化可能（CLI が json.dumps する経路と同じ制約）。
    json.dumps(dumped, sort_keys=True)


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
    assert report["verdict_reason"] == "no_positive_or_negative_rows"


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
    assert report["verdict_reason"] == "all_ineligible"


def test_evaluate_candidate_sanctioned_abstention_only_still_passes() -> None:
    candidate = _candidate("M2T-HARMONIC-OLS-K4-WINHANN", claim_ceiling=ClaimCeiling.ABSOLUTE)
    outcomes = [
        _outcome("positive", None, MeterOutput(values={"tilt_db_per_oct": -6.0})),
        # F0 prepass unusable on SILENCE: sanctioned (SILENCE, F0_UNUSABLE) —
        # the only class with a skipped row, and it is sanctioned, so PASS
        # remains reachable.
        _outcome("negative", "SILENCE", MeterOutput(), reason=diagnose.F0_UNUSABLE_REASON),
        _outcome("negative", "NOISE_ONLY", MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING)),
    ]
    report = diagnose.evaluate_candidate(candidate, outcomes)
    assert report["sanctioned_abstentions"] == 1
    assert report["missing_by_reason"] == {"F0_UNUSABLE": 1, "OUTPUT_MISSING": 1}
    # sanctioned abstention still contributes zero to the fire rate (correct
    # non-detection), so it does not itself flip the verdict.
    assert report["negative_fire_rate"] == 0.0
    assert report["negative_controls_incomplete_by_class"] == {
        "NOISE_ONLY": False,
        "SILENCE": False,
    }
    assert report["verdict"] == "PASS"
    assert report["verdict_reason"] is None


def test_evaluate_candidate_noise_only_f0_unusable_now_sanctioned_v14() -> None:
    # v1.4 §前提 3 (`DESIGN_VG_METER_CAL_DEBT_v1.4.md`, P2 census PASS —
    # `scratchpad/v14/p23/p23_report.txt` §5.1): `SANCTIONED_ABSTENTIONS` now
    # includes `(NOISE_ONLY, "F0_UNUSABLE")` alongside `(SILENCE,
    # "F0_UNUSABLE")` — a negative control row entirely skipped by the
    # F0-unusable synthesis for NOISE_ONLY is now "present and non-fired"
    # too (supersedes the pre-v1.4
    # `..._non_sanctioned_missing` test that pinned the narrower vocabulary).
    candidate = _candidate("M2T-HARMONIC-OLS-K4-WINHANN", claim_ceiling=ClaimCeiling.ABSOLUTE)
    outcomes = [
        _outcome("positive", None, MeterOutput(values={"tilt_db_per_oct": -6.0})),
        _outcome("negative", "SILENCE", MeterOutput(), reason=diagnose.F0_UNUSABLE_REASON),
        _outcome("negative", "NOISE_ONLY", MeterOutput(), reason=diagnose.F0_UNUSABLE_REASON),
    ]
    report = diagnose.evaluate_candidate(candidate, outcomes)
    assert report["sanctioned_abstentions"] == 2
    assert report["missing_by_reason"] == {"F0_UNUSABLE": 2}
    assert report["negative_fire_rate"] == 0.0
    assert report["negative_controls_incomplete_by_class"] == {
        "NOISE_ONLY": False,
        "SILENCE": False,
    }
    assert report["verdict"] == "PASS"
    assert report["verdict_reason"] is None


def test_evaluate_candidate_not_evaluable_when_negative_control_row_is_non_sanctioned_missing() -> (
    None
):
    # RUN10-CAL-v1.2 WP4b: `c3b_failclosed_analysis.md` §3.2 — a negative
    # control row entirely skipped by the F0-unusable synthesis (no real
    # candidate call at all) is only "present and non-fired" when the
    # (control_class, reason) pair is sanctioned. PURE_SINE/F0_UNUSABLE is
    # NOT in `SANCTIONED_ABSTENTIONS` (v1.4 §前提 3 explicitly does not
    # extend sanctioning beyond SILENCE/NOISE_ONLY), so a real campaign
    # choosing an F0 candidate that renders this row unusable would leave
    # the negative control judgment-less for PURE_SINE — this must NOT read
    # as a clean PASS via a false 0.0 fire rate.
    candidate = _candidate("M2T-HARMONIC-OLS-K4-WINHANN", claim_ceiling=ClaimCeiling.ABSOLUTE)
    outcomes = [
        _outcome("positive", None, MeterOutput(values={"tilt_db_per_oct": -6.0})),
        _outcome("negative", "SILENCE", MeterOutput(), reason=diagnose.F0_UNUSABLE_REASON),
        _outcome("negative", "PURE_SINE", MeterOutput(), reason=diagnose.F0_UNUSABLE_REASON),
    ]
    report = diagnose.evaluate_candidate(candidate, outcomes)
    assert report["sanctioned_abstentions"] == 1
    assert report["missing_by_reason"] == {"F0_UNUSABLE": 2}
    assert report["negative_fire_rate"] == 0.0
    assert report["negative_controls_incomplete_by_class"] == {
        "PURE_SINE": True,
        "SILENCE": False,
    }
    assert report["verdict"] == "NOT_EVALUABLE"
    assert report["verdict_reason"] == "negative_controls_incomplete"


def test_evaluate_candidate_negative_controls_incomplete_beats_fail_negative() -> None:
    # A partially-observed NOISE_ONLY row (one probe usable and firing, one
    # probe unusable) is NOT incomplete (a real record exists) — this stays
    # FAIL_NEGATIVE, not NOT_EVALUABLE, distinguishing "some evidence, and it
    # is bad" from "no evidence at all".
    candidate = _candidate("M2T-HARMONIC-OLS-K4-WINHANN", claim_ceiling=ClaimCeiling.ABSOLUTE)
    outcomes = [
        _outcome("positive", None, MeterOutput(values={"tilt_db_per_oct": -6.0})),
        _outcome("negative", "NOISE_ONLY", MeterOutput(values={"tilt_db_per_oct": 4.5})),
        _outcome("negative", "NOISE_ONLY", MeterOutput(), reason=diagnose.F0_UNUSABLE_REASON),
    ]
    report = diagnose.evaluate_candidate(candidate, outcomes)
    assert report["negative_controls_incomplete_by_class"] == {"NOISE_ONLY": False}
    assert report["verdict"] == "FAIL_NEGATIVE"
    assert report["verdict_reason"] is None


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
    assert report["schema"] == "diagnose/0.3"
    assert report["claimable"] is False
    # M2T-B0-CURRENT-HYBRID does not need F0 injection: no prepass sweep.
    assert report["f0_prepass"] == "not_applicable"
    assert len(report["results"]) == 1
    assert report["results"][0]["f0_candidate"] is None
    assert report["results"][0]["candidates"][0]["candidate_id"] == "M2T-B0-CURRENT-HYBRID"


# ---------------------------------------------------------------------------
# F0 prepass 候補の掃引: 決定性・限定（RUN10-CAL-v1.2 WP4b 新設）
# ---------------------------------------------------------------------------


def test_f0_registry_candidates_is_deterministic_and_sorted() -> None:
    first = diagnose.f0_registry_candidates()
    second = diagnose.f0_registry_candidates()
    assert [c.candidate_id for c in first] == [c.candidate_id for c in second]
    ids = [c.candidate_id for c in first]
    assert ids == sorted(ids)
    assert ids, "registry must declare at least one F0_CONTROL candidate"
    assert all(c.meter == diagnose.MeterId.F0_CONTROL for c in first)
    assert {c.candidate_id for c in first} == {
        c.candidate_id for c in registry.candidates_for_meter(diagnose.MeterId.F0_CONTROL)
    }


def test_run_diagnosis_sweeps_all_f0_registry_candidates_by_default() -> None:
    candidates = [registry.candidate_by_id("M2T-HARMONIC-OLS-K4-WINHANN")]
    assert diagnose.needs_f0_injection(candidates[0])

    report = diagnose.run_diagnosis(_TILT_FAMILY, candidates, 8, 1)

    assert report["f0_prepass"] == "swept"
    expected_ids = [c.candidate_id for c in diagnose.f0_registry_candidates()]
    assert [r["f0_candidate"] for r in report["results"]] == expected_ids
    for result in report["results"]:
        assert result["candidates"][0]["candidate_id"] == "M2T-HARMONIC-OLS-K4-WINHANN"


def test_run_diagnosis_f0_candidate_flag_limits_to_one() -> None:
    candidates = [registry.candidate_by_id("M2T-HARMONIC-OLS-K4-WINHANN")]
    report = diagnose.run_diagnosis(
        _TILT_FAMILY, candidates, 8, 1, f0_candidate_id="F0-PYIN-FRAME2048-HOP512"
    )
    assert report["f0_prepass"] == "single"
    assert len(report["results"]) == 1
    assert report["results"][0]["f0_candidate"] == "F0-PYIN-FRAME2048-HOP512"


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
    assert payload["schema"] == "diagnose/0.3"
    assert payload["f0_prepass"] == "not_applicable"
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


def test_cli_rejects_unknown_f0_candidate_id(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = diagnose.main(
        ["--family", _TILT_FAMILY, "--f0-candidate", "NOT-A-REAL-F0-CANDIDATE"]
    )
    out = capsys.readouterr().out
    assert exit_code == 1
    assert json.loads(out)["result"] == "ERROR"


def test_cli_rejects_f0_candidate_not_in_f0_control_meter(capsys: pytest.CaptureFixture[str]) -> None:
    # M2T-B0-CURRENT-HYBRID is a real registry candidate but belongs to
    # M2_SPECTRAL_TILT, not F0_CONTROL — must be rejected as an F0 prepass
    # candidate regardless of --family.
    exit_code = diagnose.main(
        ["--family", _TILT_FAMILY, "--f0-candidate", "M2T-B0-CURRENT-HYBRID"]
    )
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
    assert report["schema"] == "diagnose/0.3"
    assert report["family"] == _F0_FAMILY
    assert report["claimable"] is False
    assert len(report["cells"]) <= 6
    # none of the F0_CONTROL registry candidates need F0 injection themselves
    # (they *are* the F0 prepass source) — no sweep.
    assert report["f0_prepass"] == "not_applicable"
    assert len(report["results"]) == 1
    result = report["results"][0]
    assert result["f0_candidate"] is None
    assert result["candidates"], "F0_CONTROL has 5 registry candidates"
    for candidate_report in result["candidates"]:
        assert candidate_report["verdict"] in {
            "PASS",
            "FAIL_POSITIVE",
            "FAIL_NEGATIVE",
            "NO_CEILING",
            "NOT_EVALUABLE",
        }
    assert not (fake_home / ".vg_cal").exists()
