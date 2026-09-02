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
from voice_genesis.calibration.campaign import holdout_stage, selection_stage
from voice_genesis.calibration.campaign.state import load_frozen_campaign
from voice_genesis.calibration.gates import DirectionalPair, EUseEvidenceRow, InvariancePair
from voice_genesis.calibration.vocab import ClaimCeiling, Domain, EvidenceClass, MeterId, TerminalStatus

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
