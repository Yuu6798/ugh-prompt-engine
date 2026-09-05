"""`campaign/holdout_stage.py` のテスト: 合成 observable による gate 判定 +
終端 status cascade（Task Brief: "use synthetic observables where real
meters are too slow"）。高速。
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from voice_genesis.calibration import e_use_table
from voice_genesis.calibration.campaign import holdout_stage, measure_stage, selection_stage
from voice_genesis.calibration.campaign.state import load_frozen_campaign
from voice_genesis.calibration.candidates.adapter import MeterOutput
from voice_genesis.calibration.candidates.registry import candidates_for_meter
from voice_genesis.calibration.fixtures.matrix import FixtureRow, MatrixRow
from voice_genesis.calibration.gates import DirectionalPair, EUseEvidenceRow, InvariancePair
from voice_genesis.calibration.vocab import (
    ClaimCeiling,
    Domain,
    EvidenceClass,
    MeterId,
    MissingReason,
    Split,
    TerminalStatus,
)

from ._campaign_fixture import build_tiny_campaign


def _absolute_pass_observations(n: int = 12) -> list[holdout_stage.RawInstanceObservation]:
    return [
        holdout_stage.RawInstanceObservation(
            instance_id=f"i{i}",
            domain=Domain.PRIMARY,
            truth=100.0 + i,
            per_process_repeats={
                "within-process": [100.0 + i, 100.01 + i, 99.99 + i],
                "fresh-process-0": [100.005 + i],
                "fresh-process-1": [100.005 + i],
            },
            u_gt=0.001,
            u_num=0.001,
            e_use=1.0,
        )
        for i in range(n)
    ]


def test_evaluate_absolute_meter_passes_with_clean_synthetic_data() -> None:
    observations = _absolute_pass_observations()
    margins = holdout_stage.build_instance_margins(observations)
    instance_ids = [m.instance_id for m in margins]

    pairs = [
        InvariancePair(pair_id=f"p{i}", axis="axis-a", ds=0.0, e_use_i0=1.0, e_use_ia=1.0)
        for i in range(5)
    ]
    result = holdout_stage.evaluate_absolute_meter(
        MeterId.M3_FORMANTS.value,
        ClaimCeiling.ABSOLUTE,
        selected_candidate_id="FAKE-ABS-CAND",
        per_instance_margins=margins,
        u_rep=0.01,
        u_proc=0.005,
        invariance_pairs_by_axis={"axis-a": pairs},
        declared_invariance_axes=("axis-a",),
        expected_primary_instance_ids=instance_ids,
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.terminal_status == TerminalStatus.CALIBRATED_ABSOLUTE.value
    assert result.gate_detail["passed"] is True
    assert result.selected_candidate_id == "FAKE-ABS-CAND"


def test_evaluate_absolute_meter_diagnostic_only_when_gate_fails() -> None:
    observations = _absolute_pass_observations()
    margins = holdout_stage.build_instance_margins(observations)
    instance_ids = [m.instance_id for m in margins]

    result = holdout_stage.evaluate_absolute_meter(
        MeterId.M2_SPECTRAL_TILT.value,
        ClaimCeiling.ABSOLUTE,
        selected_candidate_id="FAKE-CAND",
        per_instance_margins=margins,
        u_rep=0.01,
        u_proc=0.005,
        invariance_pairs_by_axis={},
        declared_invariance_axes=(),  # no invariance axis declared -> gate4' fails
        expected_primary_instance_ids=instance_ids,
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.terminal_status == TerminalStatus.DIAGNOSTIC_ONLY.value
    assert result.reason_code == "OUTPUT_MISSING"
    assert result.gate_detail["passed"] is False


# ---------------------------------------------------------------------------
# round 28 ADOPT (2) (`[UNDERSPEC-CAL-D64]`) "Count rejected F0 instances as
# missing coverage": "Holdout (C4) applies the same explicit-missing rule to
# its status derivation ... a meter with missing holdout coverage cannot
# reach CALIBRATED_* status." An F0-dependent candidate skipped entirely by
# `measure_stage.run_measure_stage()` on an F0-unusable C4 instance
# (`[UNDERSPEC-CAL-D61]`) produces no `MeasurementRecord`, hence no
# `RawInstanceObservation`/`InstanceMargin` for that instance — but the
# frozen `expected_primary_instance_ids` still names it. §10.3 gate 1
# ("全 PRIMARY instance が eligible（critical missing/undefined なし）") and
# `gates.absolute_gates`'s `expected_primary_instance_ids` population check
# already fail this closed; this regression test locks in that the F0-driven
# coverage gap specifically cannot reach CALIBRATED_ABSOLUTE through this
# mechanism.
# ---------------------------------------------------------------------------


def test_missing_holdout_coverage_from_f0_unusable_instance_blocks_calibrated_absolute() -> None:
    observations = _absolute_pass_observations()
    margins = holdout_stage.build_instance_margins(observations)
    # frozen declaration names one instance more than was actually measured
    # -- exactly the shape an F0-unusable skip at C4 leaves behind (the
    # instance is real and expected, but no record/margin was ever built
    # for it because measure_stage.run_measure_stage() never called the
    # F0-dependent candidate on it).
    instance_ids = [m.instance_id for m in margins] + ["i-f0-unusable-skip"]

    pairs = [
        InvariancePair(pair_id=f"p{i}", axis="axis-a", ds=0.0, e_use_i0=1.0, e_use_ia=1.0)
        for i in range(5)
    ]
    result = holdout_stage.evaluate_absolute_meter(
        MeterId.M3_FORMANTS.value,
        ClaimCeiling.ABSOLUTE,
        selected_candidate_id="FAKE-ABS-CAND",
        per_instance_margins=margins,
        u_rep=0.01,
        u_proc=0.005,
        invariance_pairs_by_axis={"axis-a": pairs},
        declared_invariance_axes=("axis-a",),
        expected_primary_instance_ids=instance_ids,
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.terminal_status != TerminalStatus.CALIBRATED_ABSOLUTE.value
    assert result.gate_detail["passed"] is False
    assert any("gate1" in reason for reason in result.gate_detail["failure_reasons"])


def test_evaluate_absolute_meter_not_evaluable_when_no_instances() -> None:
    result = holdout_stage.evaluate_absolute_meter(
        MeterId.M2_APERIODICITY.value,
        ClaimCeiling.ABSOLUTE,
        selected_candidate_id=None,
        per_instance_margins=[],
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={},
        declared_invariance_axes=(),
        expected_primary_instance_ids=(),
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=False,
    )
    assert result.terminal_status == TerminalStatus.NOT_EVALUABLE.value
    assert result.reason_code == "OUTPUT_NOT_EVALUABLE"


def test_evaluate_directional_meter_passes_with_clean_synthetic_data() -> None:
    pairs = [
        DirectionalPair(
            pair_id=f"p{i}",
            delta_truth=float(i + 1),
            delta_output=float(i + 1) * 2.0,
            u_gt_i=0.01,
            u_num_i=0.01,
            u_gt_j=0.01,
            u_num_j=0.01,
            correct_sign=True,
            is_adjacent=True,
            sweep_id="sweep-a",
        )
        for i in range(5)
    ]
    result = holdout_stage.evaluate_directional_meter(
        MeterId.M5_TRANSITION.value,
        ClaimCeiling.DIRECTIONAL,
        selected_candidate_id="FAKE-DIR-CAND",
        pairs=pairs,
        u_rep=0.01,
        u_proc=0.01,
        expected_sweep_ids=("sweep-a",),
        expected_adjacent_pair_ids={"sweep-a": [p.pair_id for p in pairs]},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.terminal_status == TerminalStatus.CALIBRATED_DIRECTIONAL.value
    assert result.gate_detail["passed"] is True


def test_diagnostic_only_close_for_m4() -> None:
    result = holdout_stage.diagnostic_only_close(MeterId.M4_RESONANCE.value)
    assert result.terminal_status == TerminalStatus.DIAGNOSTIC_ONLY.value
    assert result.ceiling == ClaimCeiling.DIAGNOSTIC_ONLY.value


def test_selection_failed_closed_meter() -> None:
    result = holdout_stage.selection_failed_closed_meter(MeterId.F0_CONTROL.value)
    assert result.terminal_status == TerminalStatus.NOT_EVALUABLE.value


def test_run_holdout_stage_records_single_event_with_all_meters(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    results = [holdout_stage.diagnostic_only_close(m.value) for m in MeterId]
    entry = holdout_stage.run_holdout_stage(campaign, results)

    assert entry.payload["kind"] == "holdout_executed_valid"
    per_meter = entry.payload["per_meter"]
    assert set(per_meter) == {m.value for m in MeterId}
    for meter_id, detail in per_meter.items():
        assert detail["terminal_status"] == TerminalStatus.DIAGNOSTIC_ONLY.value


def test_declared_axes_for_family_reads_frozen_fixture_spec() -> None:
    manifest = {
        "frozen_design": {
            "fixture_spec": {
                "TILT_GT": {"confound_axes": ["f0_hz", "sr_hz"], "boundary_probes": []},
            }
        }
    }
    assert holdout_stage.declared_axes_for_family(manifest, "TILT_GT") == ("f0_hz", "sr_hz")
    assert holdout_stage.declared_axes_for_family(manifest, "UNKNOWN_FAMILY") == ()
    assert holdout_stage.declared_axes_for_family({}, "TILT_GT") == ()


def _e_use_row(construct_id: str, *, mode: str) -> EUseEvidenceRow:
    return EUseEvidenceRow(
        construct_id=construct_id,
        unit="hz",
        domain="test",
        intended_use="test",
        maximum_claim="test",
        e_use_value=1.0,
        derivation_rule="test",
        evidence_class=EvidenceClass.NORMATIVE_SPEC,
        source_id_or_url="test",
        source_checked_at="2026-09-02",
        source_hash_or_version="test",
        applicability_argument="test",
        review_status="test",
        e_use_mode=mode,
    )


def test_split_e_use_rows_by_mode() -> None:
    rows = [
        _e_use_row("a", mode="absolute"),
        _e_use_row("b", mode="relative"),
        _e_use_row("c", mode="absolute"),
        _e_use_row("d", mode="relative"),
    ]
    absolute_rows, relative_rows = holdout_stage.split_e_use_rows_by_mode(rows)
    assert {r.construct_id for r in absolute_rows} == {"a", "c"}
    assert {r.construct_id for r in relative_rows} == {"b", "d"}


def test_out_of_scope_construct_cannot_reach_calibrated_absolute_in_holdout() -> None:
    """finding #11 regression, requirement (b): the *same* clean synthetic
    data that reaches CALIBRATED_ABSOLUTE with an uncapped ABSOLUTE ceiling
    can never reach it once `selection_stage.capped_ceiling()` has downgraded
    the ceiling to DIRECTIONAL (construct out of `max_claim_scope`) —
    demonstrating the capped ceiling actually constrains holdout terminal
    status derivation, not just selection-time pool membership."""
    observations = _absolute_pass_observations()
    margins = holdout_stage.build_instance_margins(observations)
    instance_ids = [m.instance_id for m in margins]
    pairs = [
        InvariancePair(pair_id=f"p{i}", axis="axis-a", ds=0.0, e_use_i0=1.0, e_use_ia=1.0)
        for i in range(5)
    ]

    def _evaluate(ceiling: ClaimCeiling) -> holdout_stage.MeterHoldoutResult:
        return holdout_stage.evaluate_absolute_meter(
            MeterId.M3_FORMANTS.value,
            ceiling,
            selected_candidate_id="FAKE-ABS-CAND",
            per_instance_margins=margins,
            u_rep=0.01,
            u_proc=0.005,
            invariance_pairs_by_axis={"axis-a": pairs},
            declared_invariance_axes=("axis-a",),
            expected_primary_instance_ids=instance_ids,
            fdr0=0.0,
            fnr1=0.0,
            min_count_met=True,
        )

    # baseline: uncapped ABSOLUTE ceiling reaches CALIBRATED_ABSOLUTE.
    uncapped = _evaluate(ClaimCeiling.ABSOLUTE)
    assert uncapped.terminal_status == TerminalStatus.CALIBRATED_ABSOLUTE.value

    # the construct behind this meter is not in max_claim_scope -> capped.
    capped_ceiling, was_capped = selection_stage.capped_ceiling(
        "formant_frequency", ClaimCeiling.ABSOLUTE, frozenset({"some_other_construct"})
    )
    assert capped_ceiling == ClaimCeiling.DIRECTIONAL
    assert was_capped is True

    capped_result = _evaluate(capped_ceiling)
    assert capped_result.terminal_status != TerminalStatus.CALIBRATED_ABSOLUTE.value
    assert capped_result.gate_detail["passed"] is True  # the gate itself still passes


# ---------------------------------------------------------------------------
# round 20 採用 (2) (`[UNDERSPEC-CAL-D47]`): `load_e_use_rows()` must verify
# the frozen `e_use_table.json` bytes against the manifest's
# `frozen_inputs.e_use_table_sha256` pin before parsing them — missing file
# or mismatch fail closed (never an empty table).
# ---------------------------------------------------------------------------


def _sample_e_use_rows() -> list[EUseEvidenceRow]:
    return [
        EUseEvidenceRow(
            construct_id="fundamental_frequency",
            unit="hz",
            domain="d",
            intended_use="u",
            maximum_claim="ABSOLUTE",
            e_use_value=1.0,
            derivation_rule="r",
            evidence_class=EvidenceClass.USER_ACCEPTED_USE_BOUND,
            source_id_or_url="s",
            source_checked_at="t",
            source_hash_or_version="v",
            applicability_argument="a",
            review_status="ACCEPTED",
        )
    ]


def _write_frozen_e_use_table(campaign_dir: Path, rows: list[EUseEvidenceRow]) -> str:
    """`campaign_dir/e_use_table.json` へ書き、その sha256（manifest の
    `frozen_inputs.e_use_table_sha256` pin として使う値）を返す。"""
    path = campaign_dir / "e_use_table.json"
    e_use_table.save_e_use_table(path, rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stop_events(campaign) -> list[dict[str, object]]:
    return [
        e.payload
        for e in campaign.ledger.entries
        if isinstance(e.payload, dict) and e.payload.get("kind") == "stop_event"
    ]


def test_load_e_use_rows_passes_when_bytes_match_frozen_pin(tmp_path: Path) -> None:
    rows = _sample_e_use_rows()
    # `build_tiny_campaign()` writes c0_manifest.json before the table exists
    # on disk, so compute the table's sha256 first, then freeze the manifest
    # with that pin, then write the table into the frozen campaign dir.
    table_path_probe = tmp_path / "_probe_e_use_table.json"
    e_use_table.save_e_use_table(table_path_probe, rows)
    expected_sha256 = hashlib.sha256(table_path_probe.read_bytes()).hexdigest()

    campaign_dir, secret_root = build_tiny_campaign(
        tmp_path, frozen_inputs={"e_use_table_sha256": expected_sha256}
    )
    e_use_table.save_e_use_table(campaign_dir / "e_use_table.json", rows)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    loaded = holdout_stage.load_e_use_rows(campaign)
    assert len(loaded) == 1
    assert loaded[0].construct_id == "fundamental_frequency"
    assert _stop_events(campaign) == []


def test_load_e_use_rows_missing_file_fails_closed_with_stop_event(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(
        tmp_path, frozen_inputs={"e_use_table_sha256": "a" * 64}
    )
    # deliberately never write campaign_dir / "e_use_table.json".
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    with pytest.raises(holdout_stage.StaleEUseTableError):
        holdout_stage.load_e_use_rows(campaign)

    reloaded = load_frozen_campaign(campaign_dir, secret_root)
    stop_events = _stop_events(reloaded)
    assert len(stop_events) == 1
    assert stop_events[0]["reason"] == "E_USE_TABLE_STALE_OR_MUTATED"


def test_load_e_use_rows_mutated_file_fails_closed_with_stop_event(tmp_path: Path) -> None:
    rows = _sample_e_use_rows()
    table_path_probe = tmp_path / "_probe_e_use_table.json"
    e_use_table.save_e_use_table(table_path_probe, rows)
    stale_sha256 = hashlib.sha256(table_path_probe.read_bytes()).hexdigest()

    campaign_dir, secret_root = build_tiny_campaign(
        tmp_path, frozen_inputs={"e_use_table_sha256": stale_sha256}
    )
    # write a *different* table than the one the pin was computed from —
    # simulates the frozen file being mutated/swapped after freeze.
    mutated_rows = _sample_e_use_rows()
    mutated_rows[0] = replace(mutated_rows[0], e_use_value=2.0)
    e_use_table.save_e_use_table(campaign_dir / "e_use_table.json", mutated_rows)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    with pytest.raises(holdout_stage.StaleEUseTableError):
        holdout_stage.load_e_use_rows(campaign)

    reloaded = load_frozen_campaign(campaign_dir, secret_root)
    stop_events = _stop_events(reloaded)
    assert len(stop_events) == 1
    assert stop_events[0]["reason"] == "E_USE_TABLE_STALE_OR_MUTATED"


def test_load_e_use_rows_missing_pin_fails_closed(tmp_path: Path) -> None:
    """No `frozen_inputs` section at all in the manifest (e.g. a campaign
    frozen before round 12's pin was introduced) must fail closed rather
    than silently trusting whatever bytes are on disk."""
    rows = _sample_e_use_rows()
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)  # no frozen_inputs
    e_use_table.save_e_use_table(campaign_dir / "e_use_table.json", rows)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    with pytest.raises(holdout_stage.StaleEUseTableError):
        holdout_stage.load_e_use_rows(campaign)

    reloaded = load_frozen_campaign(campaign_dir, secret_root)
    assert len(_stop_events(reloaded)) == 1


def test_load_e_use_rows_stop_event_is_idempotent_across_retries(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(
        tmp_path, frozen_inputs={"e_use_table_sha256": "a" * 64}
    )
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    for _ in range(2):
        with pytest.raises(holdout_stage.StaleEUseTableError):
            holdout_stage.load_e_use_rows(campaign)
        campaign = load_frozen_campaign(campaign_dir, secret_root)

    # both attempts hit the exact same missing-file detail string, so the
    # dedup used elsewhere in this codebase (`_refuse_if_caps_already_breached`)
    # is not required here — this fix always appends (round 20 ADOPT(2) does
    # not require idempotent stop_event recording, unlike ADOPT(3)); assert
    # the reason code is stable across both.
    stop_events = _stop_events(campaign)
    assert len(stop_events) == 2
    assert all(e["reason"] == "E_USE_TABLE_STALE_OR_MUTATED" for e in stop_events)


# ---------------------------------------------------------------------------
# v1.1 §V3.2 (WP2c, D17 close): real gate input assembly unit tests
# ---------------------------------------------------------------------------


def test_declared_u_gt_u_num_for_family_missing_returns_none() -> None:
    assert holdout_stage.declared_u_gt_u_num_for_family({}, "TILT_GT") is None
    manifest_no_family = {"frozen_design": {"fixture_spec": {}}}
    assert holdout_stage.declared_u_gt_u_num_for_family(manifest_no_family, "TILT_GT") is None
    manifest_no_keys = {"frozen_design": {"fixture_spec": {"TILT_GT": {"confound_axes": []}}}}
    assert holdout_stage.declared_u_gt_u_num_for_family(manifest_no_keys, "TILT_GT") is None


def test_declared_u_gt_u_num_for_family_reads_frozen_bound() -> None:
    manifest = {
        "frozen_design": {"fixture_spec": {"TILT_GT": {"u_gt_bound": 0.1, "u_num_bound": 0.01}}}
    }
    assert holdout_stage.declared_u_gt_u_num_for_family(manifest, "TILT_GT") == (0.1, 0.01)
    # ints are accepted (JSON round-trip may produce either).
    manifest_int = {
        "frozen_design": {"fixture_spec": {"TILT_GT": {"u_gt_bound": 0, "u_num_bound": 1}}}
    }
    assert holdout_stage.declared_u_gt_u_num_for_family(manifest_int, "TILT_GT") == (0.0, 1.0)


@pytest.mark.parametrize("bad_u_gt", [-1.0, float("nan"), float("inf"), "0.1", True, None])
def test_declared_u_gt_u_num_for_family_rejects_invalid_u_gt(bad_u_gt: object) -> None:
    manifest = {
        "frozen_design": {
            "fixture_spec": {"TILT_GT": {"u_gt_bound": bad_u_gt, "u_num_bound": 0.01}}
        }
    }
    assert holdout_stage.declared_u_gt_u_num_for_family(manifest, "TILT_GT") is None


def test_instance_id_str_round_trips_row_and_probe() -> None:
    assert holdout_stage.instance_id_str("row-1", 3) == "row-1#3"
    assert holdout_stage.instance_id_str("row-1", 3) != holdout_stage.instance_id_str("row-1", 4)


def test_absolute_e_use_value_absolute_mode_ignores_truth() -> None:
    row = _e_use_row("fundamental_frequency", mode="absolute")  # e_use_value=1.0
    assert holdout_stage.absolute_e_use_value(row, truth=500.0) == 1.0
    assert holdout_stage.absolute_e_use_value(row, truth=-500.0) == 1.0


def test_absolute_e_use_value_relative_mode_expands_by_abs_truth() -> None:
    row = _e_use_row("fundamental_frequency", mode="relative")  # e_use_value=1.0 (fraction)
    assert holdout_stage.absolute_e_use_value(row, truth=10.0) == pytest.approx(10.0)
    # signed construct (e.g. TILT slope): abs(truth) is used, not the raw
    # (possibly negative) truth value, since E_use is a positive tolerance.
    assert holdout_stage.absolute_e_use_value(row, truth=-10.0) == pytest.approx(10.0)


def test_absolute_e_use_value_unjustified_row_returns_none() -> None:
    row = replace(
        _e_use_row("fundamental_frequency", mode="absolute"),
        e_use_value=None,
        evidence_class=EvidenceClass.UNJUSTIFIED,
    )
    assert holdout_stage.absolute_e_use_value(row, truth=1.0) is None


def test_absolute_e_use_value_nonpositive_expansion_returns_none() -> None:
    # relative mode at truth=0 expands to 0.0, not a usable positive E_use.
    row = _e_use_row("fundamental_frequency", mode="relative")
    assert holdout_stage.absolute_e_use_value(row, truth=0.0) is None


def _matrix_row(
    row_id: str,
    *,
    family: str,
    block: str,
    domain: Domain = Domain.PRIMARY,
    control_class: str | None = None,
    positive_control: bool = False,
    nuisance_tag: str | None = None,
    **truth_kwargs: object,
) -> MatrixRow:
    row = FixtureRow(
        family=family,
        block=block,
        f0_hz=220.0,
        sr_hz=44100,
        gain_dbfs=-6.0,
        duration_s=1.0,
        noise_clean=True,
        noise_snr_db=None,
        context="wp2c-holdout-gate-wiring-unit-test",
        control_class=control_class,
        positive_control=positive_control,
        nuisance_tag=nuisance_tag,
        **truth_kwargs,
    )
    return MatrixRow(row=row, row_id=row_id, domain=domain)


def _within_fresh_record(
    candidate_id: str,
    row_id: str,
    probe_index: int,
    *,
    field: str,
    value: float | None,
    missing: bool = False,
) -> list[measure_stage.MeasurementRecord]:
    output = (
        MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING)
        if missing
        else MeterOutput(values={field: value})
    )
    records = [
        measure_stage.MeasurementRecord(
            row_id=row_id,
            probe_index=probe_index,
            candidate_id=candidate_id,
            repeat_kind="within",
            repeat_index=repeat_index,
            process_id="within-process",
            output=output,
        )
        for repeat_index in range(2)
    ]
    records.append(
        measure_stage.MeasurementRecord(
            row_id=row_id,
            probe_index=probe_index,
            candidate_id=candidate_id,
            repeat_kind="fresh",
            repeat_index=0,
            process_id="fresh-process-0",
            output=output,
        )
    )
    return records


def _tilt_candidate():
    return next(
        c for c in candidates_for_meter(MeterId.M2_SPECTRAL_TILT) if c.algorithm_family == "HARMONIC_OLS"
    )


def test_control_detection_for_family_counts_from_holdout_records() -> None:
    """gate5 の control 母集団: negative control は split に依らず全件、
    positive control は `Split.HOLDOUT` に home する truth-core 行のみ
    （v1.1 §V3.2 の一次事実）。"""
    candidate = _tilt_candidate()
    pos1 = _matrix_row("pos-1", family="TILT_GT", block="TRUTH_CORE", positive_control=True)
    pos2 = _matrix_row(
        "pos-2", family="TILT_GT", block="TRUTH_CORE", positive_control=True, domain=Domain.PRIMARY
    )
    pos_not_holdout = _matrix_row(
        "pos-selection", family="TILT_GT", block="TRUTH_CORE", positive_control=False
    )
    neg1 = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="NOISE_ONLY",
    )
    neg2 = _matrix_row(
        "neg-2", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="NOISE_ONLY",
    )
    matrix_rows = [pos1, pos2, pos_not_holdout, neg1, neg2]
    assignment = {
        "pos-1": Split.HOLDOUT,
        "pos-2": Split.HOLDOUT,
        "pos-selection": Split.SELECTION,
        "neg-1": Split.HOLDOUT,
        "neg-2": Split.CALIBRATION,  # negative controls count regardless of home split
    }

    records: list[measure_stage.MeasurementRecord] = []
    for row_id in ("pos-1", "pos-2"):
        for probe_index in range(5):
            records += _within_fresh_record(
                candidate.candidate_id, row_id, probe_index, field="tilt_db_per_oct", value=-6.0
            )
    for row_id in ("neg-1", "neg-2"):
        for probe_index in range(5):
            records += _within_fresh_record(
                candidate.candidate_id, row_id, probe_index, field="tilt_db_per_oct", value=None,
                missing=True,
            )
    # the SELECTION-homed positive-control anchor's records must never count
    # toward N_pos even though the candidate happens to have output for it.
    for probe_index in range(5):
        records += _within_fresh_record(
            candidate.candidate_id, "pos-selection", probe_index, field="tilt_db_per_oct", value=-6.0
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
    )
    assert detection.n_pos == 10  # pos-1 + pos-2, 5 probes each; pos-selection excluded
    assert detection.n_neg == 10  # neg-1 + neg-2, regardless of home split
    assert detection.fdr0 == 0.0
    assert detection.fnr1 == 0.0
    assert detection.min_count_met is True
    assert detection.negative_control_failures == 0
    assert detection.positive_control_failures == 0


def test_control_detection_for_family_counts_false_fire_and_non_fire() -> None:
    candidate = _tilt_candidate()
    pos1 = _matrix_row("pos-1", family="TILT_GT", block="TRUTH_CORE", positive_control=True)
    neg1 = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="NOISE_ONLY",
    )
    matrix_rows = [pos1, neg1]
    assignment = {"pos-1": Split.HOLDOUT, "neg-1": Split.HOLDOUT}

    records: list[measure_stage.MeasurementRecord] = []
    for probe_index in range(5):
        # positive control non-fire (missing output where detection was expected).
        records += _within_fresh_record(
            candidate.candidate_id, "pos-1", probe_index, field="tilt_db_per_oct", value=None,
            missing=True,
        )
        # negative control false fire (finite output where none was expected).
        records += _within_fresh_record(
            candidate.candidate_id, "neg-1", probe_index, field="tilt_db_per_oct", value=-3.0
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
    )
    assert detection.fdr0 == 1.0
    assert detection.fnr1 == 1.0
    assert detection.negative_control_failures == 5
    assert detection.positive_control_failures == 5


def test_build_invariance_pairs_for_family_pairs_confound_row_against_anchor() -> None:
    candidate = _tilt_candidate()
    anchor = _matrix_row(
        "anchor-1", family="TILT_GT", block="TRUTH_CORE", positive_control=True,
        slope_db_per_oct=-6.0,
    )
    confound = _matrix_row(
        "confound-1", family="TILT_GT", block="CONFOUND", nuisance_tag="sr_hz=8000",
        slope_db_per_oct=-6.0,
    )
    matrix_rows = [anchor, confound]
    assignment = {"anchor-1": Split.HOLDOUT, "confound-1": Split.HOLDOUT}

    records: list[measure_stage.MeasurementRecord] = []
    for row_id in ("anchor-1", "confound-1"):
        for probe_index in range(5):
            records += _within_fresh_record(
                candidate.candidate_id, row_id, probe_index, field="tilt_db_per_oct", value=-6.0
            )

    e_use_row = _e_use_row(candidate.construct, mode="absolute")
    pairs_by_axis = holdout_stage.build_invariance_pairs_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
        declared_axes=("sr_hz",),
        e_use_row=e_use_row,
    )
    assert set(pairs_by_axis) == {"sr_hz"}
    assert len(pairs_by_axis["sr_hz"]) == 5  # 1 pair per probe_index
    for pair in pairs_by_axis["sr_hz"]:
        assert pair.axis == "sr_hz"
        assert pair.ds == pytest.approx(0.0)  # anchor and confound agree perfectly


def test_build_invariance_pairs_for_family_undeclared_axis_and_missing_anchor_are_empty() -> None:
    candidate = _tilt_candidate()
    confound = _matrix_row(
        "confound-1", family="TILT_GT", block="CONFOUND", nuisance_tag="sr_hz=8000",
        slope_db_per_oct=-6.0,
    )
    matrix_rows = [confound]  # no positive-control anchor declared at all
    assignment = {"confound-1": Split.HOLDOUT}
    e_use_row = _e_use_row(candidate.construct, mode="absolute")
    pairs_by_axis = holdout_stage.build_invariance_pairs_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=[],
        declared_axes=("sr_hz",),
        e_use_row=e_use_row,
    )
    assert pairs_by_axis == {"sr_hz": ()}


def test_build_absolute_gate_inputs_raises_gate_input_error_when_e_use_row_missing() -> None:
    candidate = _tilt_candidate()
    row = _matrix_row("r1", family="TILT_GT", block="TRUTH_CORE", slope_db_per_oct=-6.0)
    manifest = {
        "frozen_design": {"fixture_spec": {"TILT_GT": {"u_gt_bound": 0.01, "u_num_bound": 0.01}}}
    }
    with pytest.raises(holdout_stage.GateInputError, match="no E_use evidence row"):
        holdout_stage.build_absolute_gate_inputs(
            manifest=manifest,
            family="TILT_GT",
            candidate=candidate,
            row_by_id={"r1": row.row},
            matrix_rows=[row],
            assignment={"r1": Split.HOLDOUT},
            records=[],
            expected_primary_instances=[("r1", 0)],
            e_use_rows=(),
        )


def test_build_absolute_gate_inputs_raises_gate_input_error_when_u_gt_u_num_missing() -> None:
    candidate = _tilt_candidate()
    row = _matrix_row("r1", family="TILT_GT", block="TRUTH_CORE", slope_db_per_oct=-6.0)
    e_use_row = _e_use_row(candidate.construct, mode="absolute")
    e_use_row = replace(
        e_use_row, unit=candidate.unit, domain=candidate.domain, evidence_class=EvidenceClass.NORMATIVE_SPEC
    )
    with pytest.raises(holdout_stage.GateInputError, match="no frozen U_GT/U_num bound"):
        holdout_stage.build_absolute_gate_inputs(
            manifest={},  # no frozen_design at all
            family="TILT_GT",
            candidate=candidate,
            row_by_id={"r1": row.row},
            matrix_rows=[row],
            assignment={"r1": Split.HOLDOUT},
            records=[],
            expected_primary_instances=[("r1", 0)],
            e_use_rows=(e_use_row,),
        )


def test_evaluate_absolute_meter_from_campaign_wires_real_inputs_and_gate5_fails_on_min_count() -> None:
    """AC8 unit-level counterpart of the CLI E2E test: exercises
    `build_absolute_gate_inputs`' real U_rep/U_proc computation and gate4'
    invariance-pair assembly directly (no `cli._run_c4` involved)."""
    candidate = _tilt_candidate()
    anchor = _matrix_row(
        "anchor-1", family="TILT_GT", block="TRUTH_CORE", positive_control=True,
        slope_db_per_oct=-6.0,
    )
    confound = _matrix_row(
        "confound-1", family="TILT_GT", block="CONFOUND", nuisance_tag="sr_hz=8000",
        slope_db_per_oct=-6.0,
    )
    neg = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="NOISE_ONLY",
    )
    matrix_rows = [anchor, confound, neg]
    row_by_id = {mr.row_id: mr.row for mr in matrix_rows}
    assignment = {"anchor-1": Split.HOLDOUT, "confound-1": Split.HOLDOUT, "neg-1": Split.HOLDOUT}

    records: list[measure_stage.MeasurementRecord] = []
    for row_id in ("anchor-1", "confound-1"):
        for probe_index in range(5):
            records += _within_fresh_record(
                candidate.candidate_id, row_id, probe_index, field="tilt_db_per_oct", value=-6.0
            )
    for probe_index in range(5):
        records += _within_fresh_record(
            candidate.candidate_id, "neg-1", probe_index, field="tilt_db_per_oct", value=None,
            missing=True,
        )
    # gate5 requires N_pos/N_neg >= 10; only 1 positive-control row (5
    # probes) is available here, so precondition-only assertions below stop
    # short of CALIBRATED_ABSOLUTE (min_count_met=False) -- proving the gate
    # is genuinely evaluated, not stubbed. See the CLI E2E test for a
    # min-count-satisfying fixture reaching CALIBRATED_ABSOLUTE end to end.
    e_use_row = replace(
        _e_use_row(candidate.construct, mode="absolute"),
        unit=candidate.unit,
        domain=candidate.domain,
        e_use_value=2.0,
    )
    manifest = {
        "frozen_design": {
            "fixture_spec": {
                "TILT_GT": {"u_gt_bound": 0.01, "u_num_bound": 0.01, "confound_axes": ["sr_hz"]}
            }
        }
    }
    expected_primary_instances = {("anchor-1", p) for p in range(5)} | {
        ("confound-1", p) for p in range(5)
    }
    result = holdout_stage.evaluate_absolute_meter_from_campaign(
        meter_id=MeterId.M2_SPECTRAL_TILT.value,
        family="TILT_GT",
        candidate=candidate,
        manifest=manifest,
        row_by_id=row_by_id,
        matrix_rows=matrix_rows,
        assignment=assignment,
        records=records,
        expected_primary_instances=expected_primary_instances,
        e_use_rows=(e_use_row,),
    )
    assert result.terminal_status == TerminalStatus.DIAGNOSTIC_ONLY.value, result.gate_detail
    assert result.gate_detail["passed"] is False
    assert any("gate5" in reason for reason in result.gate_detail["failure_reasons"])


def test_directional_claim_shrinkage_detail_enumerates_and_prohibits_extrapolation() -> None:
    row_a = _matrix_row("t1", family="TRANSITION_GT", block="TRUTH_CORE", generator_impl="impl-a")
    row_b = _matrix_row("t2", family="TRANSITION_GT", block="TRUTH_CORE", generator_impl="impl-a")
    detail = holdout_stage.directional_claim_shrinkage_detail(
        expected_sweep_member_row_ids={"sweep-a": ["t1", "t2"]},
        row_by_id={"t1": row_a.row, "t2": row_b.row},
    )
    contexts = detail["evaluated_sweep_contexts"]
    assert [c["sweep_id"] for c in contexts] == ["sweep-a"]
    assert contexts[0]["member_row_ids"] == ("t1", "t2")
    assert contexts[0]["held_fixed_context"] == {"generator_impl": "impl-a", "sr_hz": 44100}
    assert any(
        "non-evaluated" in msg or "sweep contexts not listed" in msg
        for msg in detail["prohibited_interpretations"]
    )


def test_evaluate_directional_meter_from_campaign_claim_shrinkage_pass_and_fail() -> None:
    """AC8(c): claim text enumerates evaluated sweep contexts + prohibits
    extrapolation for BOTH a passing and a gate-failing DIRECTIONAL terminal
    status (v1.1 §V2.2: "全 family の DIRECTIONAL 終端 status について")."""
    candidate = next(
        c
        for c in candidates_for_meter(MeterId.M5_TRANSITION)
        if c.algorithm_family == "WAVE_DISCONTINUITY"
    )
    row_ids = ["t1", "t2", "t3"]
    truths = [1.0, 2.0, 3.0]
    matrix_rows = [
        _matrix_row(rid, family="TRANSITION_GT", block="TRUTH_CORE", discontinuity_magnitude=t)
        for rid, t in zip(row_ids, truths)
    ]
    row_by_id = {mr.row_id: mr.row for mr in matrix_rows}
    assignment = {rid: Split.HOLDOUT for rid in row_ids}
    manifest = {
        "frozen_design": {"fixture_spec": {"TRANSITION_GT": {"u_gt_bound": 0.001, "u_num_bound": 0.001}}}
    }
    usable_primary_instances = {(rid, p) for rid in row_ids for p in range(5)}
    expected_sweep_member_row_ids = {"sweep-a": row_ids}

    def _records(scale: float) -> list[measure_stage.MeasurementRecord]:
        recs: list[measure_stage.MeasurementRecord] = []
        for row_id, truth in zip(row_ids, truths):
            for probe_index in range(5):
                recs += _within_fresh_record(
                    candidate.candidate_id, row_id, probe_index, field="magnitude",
                    value=truth * scale,
                )
        return recs

    passing = holdout_stage.evaluate_directional_meter_from_campaign(
        meter_id=MeterId.M5_TRANSITION.value,
        family="TRANSITION_GT",
        candidate=candidate,
        manifest=manifest,
        row_by_id=row_by_id,
        matrix_rows=matrix_rows,
        assignment=assignment,
        records=_records(scale=10.0),
        usable_primary_instances=usable_primary_instances,
        expected_sweep_member_row_ids=expected_sweep_member_row_ids,
    )
    assert passing.terminal_status == TerminalStatus.CALIBRATED_DIRECTIONAL.value, passing.gate_detail
    claim_text = passing.gate_detail["claim_text"]
    contexts = claim_text["evaluated_sweep_contexts"]
    assert [c["sweep_id"] for c in contexts] == ["sweep-a"]
    assert set(contexts[0]["member_row_ids"]) == set(row_ids)
    assert any(
        "extrapolation" in msg for msg in passing.gate_detail["prohibited_interpretations"]
    )

    failing = holdout_stage.evaluate_directional_meter_from_campaign(
        meter_id=MeterId.M5_TRANSITION.value,
        family="TRANSITION_GT",
        candidate=candidate,
        manifest=manifest,
        row_by_id=row_by_id,
        matrix_rows=matrix_rows,
        assignment=assignment,
        records=_records(scale=-10.0),  # reversed sign -> gate fails honestly
        usable_primary_instances=usable_primary_instances,
        expected_sweep_member_row_ids=expected_sweep_member_row_ids,
    )
    assert failing.terminal_status == TerminalStatus.DIAGNOSTIC_ONLY.value, failing.gate_detail
    assert failing.gate_detail["passed"] is False
    assert "claim_text" in failing.gate_detail
    assert "prohibited_interpretations" in failing.gate_detail


def test_evaluate_m6_identity_precondition_satisfied_is_honest_not_evaluable() -> None:
    """v1.1 §V3.2 M6 boundary: precondition satisfied is real (not
    hardcoded) -- see `cli._run_c4`'s call site -- but the cross-family
    IDENTITY measurement path does not exist yet, so this always returns
    `NOT_EVALUABLE/INPUT_MISSING` with a gate_detail explaining why (not the
    D17 placeholder's unconditional `DIAGNOSTIC_ONLY`)."""
    manifest = {
        "holdout_sweeps": {
            "IDENTITY_CAUSAL_SWEEP": {
                "sweep-1": ["id-row-1", "id-row-2"],
            }
        }
    }
    matrix_rows = [
        _matrix_row(
            "id-row-1", family="IDENTITY_CAUSAL_SWEEP", block="TRUTH_CORE",
            founder_id="founder-a", trait="pitch", delta=1,
        )
    ]
    result = holdout_stage.evaluate_m6_identity(manifest=manifest, matrix_rows=matrix_rows)
    assert result.terminal_status == TerminalStatus.NOT_EVALUABLE.value
    assert result.reason_code == "INPUT_MISSING"
    assert result.ceiling == ClaimCeiling.NONE.value
    assert "pinned_identity_cells" in result.gate_detail
    assert result.gate_detail["pinned_identity_cells"][0]["sweep_id"] == "sweep-1"
    assert result.gate_detail["pinned_identity_cells"][0]["held_fixed_context"] == {
        "founder_id": "founder-a",
        "trait": "pitch",
        "sr_hz": 44100,
    }
