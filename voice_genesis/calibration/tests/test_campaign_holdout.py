"""`campaign/holdout_stage.py` のテスト: 合成 observable による gate 判定 +
終端 status cascade（Task Brief: "use synthetic observables where real
meters are too slow"）。高速。
"""

from __future__ import annotations

import hashlib
import statistics
from dataclasses import replace
from pathlib import Path

import pytest

from voice_genesis.calibration import e_use_table
from voice_genesis.calibration.campaign import holdout_stage, measure_stage, selection_stage
from voice_genesis.calibration.campaign.state import load_frozen_campaign
from voice_genesis.calibration.candidates.adapter import MeterOutput
from voice_genesis.calibration.candidates.registry import candidate_by_id, candidates_for_meter
from voice_genesis.calibration.fixtures.controls import DetectionPredicate
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
    """R13 対応（Codex 第 13 巡 P1 採用、2026-09-05）: gate1 は pass（全
    PRIMARY instance が揃っており eligible）——ここで fail するのは gate4'
    のみ（invariance 軸が 1 つも宣言されていない）。設計正本 §11 の
    `OUTPUT_MISSING` は「PRIMARY 一部 output missing」専用であり、gate1 が
    通った上での他 gate の正直な fail は理由コード無し（`None`）が正しい
    （旧実装はここも一律 `OUTPUT_MISSING` としており、本 PR で是正）。"""
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
    assert result.reason_code is None
    assert result.gate_detail["passed"] is False


def test_evaluate_absolute_meter_diagnostic_only_output_missing_when_gate1_fails() -> None:
    """R13 対応の対照ケース: gate1 自体が fail（観測 PRIMARY instance 集合が
    凍結宣言と一致しない——ここでは 1 instance を丸ごと落とす）した場合のみ
    `reason_code == "OUTPUT_MISSING"` になることを固定する。"""
    observations = _absolute_pass_observations()
    margins = holdout_stage.build_instance_margins(observations)
    instance_ids = [m.instance_id for m in margins]
    # declare one extra expected instance that has no observation at all.
    expected_ids = [*instance_ids, "missing-instance"]

    pairs = [
        InvariancePair(pair_id=f"p{i}", axis="axis-a", ds=0.0, e_use_i0=1.0, e_use_ia=1.0)
        for i in range(5)
    ]
    result = holdout_stage.evaluate_absolute_meter(
        MeterId.M2_SPECTRAL_TILT.value,
        ClaimCeiling.ABSOLUTE,
        selected_candidate_id="FAKE-CAND",
        per_instance_margins=margins,
        u_rep=0.01,
        u_proc=0.005,
        invariance_pairs_by_axis={"axis-a": pairs},
        declared_invariance_axes=("axis-a",),
        expected_primary_instance_ids=expected_ids,
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.terminal_status == TerminalStatus.DIAGNOSTIC_ONLY.value
    assert result.reason_code == "OUTPUT_MISSING"
    assert result.gate_detail["passed"] is False
    assert any("gate1" in reason for reason in result.gate_detail["failure_reasons"])


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


def test_evaluate_directional_meter_diagnostic_only_reason_none_when_sweep_fully_observed() -> None:
    """R13 対応（Codex 第 13 巡 P1 採用、2026-09-05）: 宣言済み sweep は
    5 件の observed pair を全て持つ（欠落なし）——control failure だけで
    正直に fail する場合、設計正本 §11 の `OUTPUT_MISSING`（PRIMARY 一部
    output missing 専用）は該当せず `reason_code` は `None`。"""
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
        negative_control_failures=1,  # honest control failure, not a coverage gap
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.terminal_status == TerminalStatus.DIAGNOSTIC_ONLY.value
    assert result.reason_code is None
    assert result.gate_detail["passed"] is False
    assert "negative control failures != 0" in result.gate_detail["failure_reasons"]


def test_evaluate_directional_meter_output_missing_when_expected_sweep_unobserved() -> None:
    """R13 対応の対照ケース: `expected_sweep_ids` に宣言された sweep のうち
    1 件が観測 pair を 1 件も持たない（真の PRIMARY output 欠落）場合のみ
    `reason_code == "OUTPUT_MISSING"` になることを固定する。"""
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
        expected_sweep_ids=("sweep-a", "sweep-b"),  # sweep-b has 0 observed pairs
        expected_adjacent_pair_ids={"sweep-a": [p.pair_id for p in pairs], "sweep-b": ["x"]},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.terminal_status == TerminalStatus.DIAGNOSTIC_ONLY.value
    assert result.reason_code == "OUTPUT_MISSING"
    assert result.gate_detail["passed"] is False


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


def test_declared_u_gt_u_num_for_family_reads_real_c0_freeze_manifest() -> None:
    """v1.1 §V3.3 (WP2e): `c0_freeze._fixture_specs()` now populates
    `u_gt_bound`/`u_num_bound` for the 5 non-ABSENT families, and this is
    the exact reader `declared_u_gt_u_num_for_family()` consumes — no
    consumer-side format change was needed (the producer matches the
    existing plain-number scalar contract). ABSENT families (RESONANCE_GT /
    IDENTITY_CAUSAL_SWEEP) correctly read back as `None` (their frozen value
    is the non-numeric string `"ABSENT:<reason>"`)."""
    from voice_genesis.calibration import c0_freeze

    manifest = c0_freeze.build_manifest(
        c0_freeze._REPO_ROOT, approvals={}, campaign_date_utc="2026-09-02"
    )
    for family_name in (
        "F0_CONTROL",
        "FORMANT_GT",
        "TILT_GT",
        "APERIODICITY_GT",
        "TRANSITION_GT",
    ):
        result = holdout_stage.declared_u_gt_u_num_for_family(manifest, family_name)
        assert result is not None, family_name
        u_gt, u_num = result
        assert u_gt >= 0.0 and u_num >= 0.0, family_name
    for family_name in ("RESONANCE_GT", "IDENTITY_CAUSAL_SWEEP"):
        assert holdout_stage.declared_u_gt_u_num_for_family(manifest, family_name) is None


# ---------------------------------------------------------------------------
# R18 対応（Codex PR #346 第 18 巡 P1 採用、2026-09-05）:
# `units_commensurate_for_family()` の単位可換性の機械導出、および
# `evaluate_directional_meter_from_campaign()` がそれを既定で消費すること
# （旧実装は `units_commensurate: bool = False` を `cli._run_c4` が一度も
# 上書きせず、本番で §10.4 条件 (c) が常に無効だった）。
# ---------------------------------------------------------------------------


def test_units_commensurate_for_family_missing_manifest_keys_returns_false() -> None:
    assert holdout_stage.units_commensurate_for_family({}, "TILT_GT", "db_per_oct") is False
    manifest_no_family = {"frozen_design": {"fixture_spec": {}}}
    assert (
        holdout_stage.units_commensurate_for_family(manifest_no_family, "TILT_GT", "db_per_oct")
        is False
    )
    manifest_no_unit = {"frozen_design": {"fixture_spec": {"TILT_GT": {"u_gt_bound": 0.0}}}}
    assert (
        holdout_stage.units_commensurate_for_family(manifest_no_unit, "TILT_GT", "db_per_oct")
        is False
    )
    # ABSENT family: unit key is the literal "n/a" sentinel, never numeric.
    manifest_absent = {
        "frozen_design": {"fixture_spec": {"RESONANCE_GT": {"u_gt_bound_unit": "n/a"}}}
    }
    assert (
        holdout_stage.units_commensurate_for_family(manifest_absent, "RESONANCE_GT", "hz") is False
    )


def test_units_commensurate_for_family_matches_same_unit() -> None:
    manifest = {
        "frozen_design": {"fixture_spec": {"TILT_GT": {"u_gt_bound_unit": "db_per_oct"}}}
    }
    assert holdout_stage.units_commensurate_for_family(manifest, "TILT_GT", "db_per_oct") is True


def test_units_commensurate_for_family_normalizes_known_synonym_and_notation() -> None:
    """`fraction`（candidate 側）と `dimensionless_fraction`（C0 凍結 truth
    unit）は同義語表で吸収する。大小文字・`/`（`dB/oct` 形式）・`-` の表記
    ゆれも正規化する。"""
    manifest = {
        "frozen_design": {
            "fixture_spec": {"APERIODICITY_GT": {"u_gt_bound_unit": "dimensionless_fraction"}}
        }
    }
    assert (
        holdout_stage.units_commensurate_for_family(manifest, "APERIODICITY_GT", "fraction")
        is True
    )
    manifest_notation = {
        "frozen_design": {"fixture_spec": {"TILT_GT": {"u_gt_bound_unit": "dB/Oct"}}}
    }
    assert (
        holdout_stage.units_commensurate_for_family(manifest_notation, "TILT_GT", "db_per_oct")
        is True
    )


def test_units_commensurate_for_family_rejects_unknown_mismatch() -> None:
    """未知の組（同義語表に無い）は保守側で `False`——例: APERIODICITY_GT の
    DIRECTIONAL-ceiling 候補 (HNR, unit `db`) は truth 側 `dimensionless_
    fraction` と単位が異なる（dB は対数スケール、fraction は線形）。"""
    manifest = {
        "frozen_design": {
            "fixture_spec": {"APERIODICITY_GT": {"u_gt_bound_unit": "dimensionless_fraction"}}
        }
    }
    assert holdout_stage.units_commensurate_for_family(manifest, "APERIODICITY_GT", "db") is False


def test_units_commensurate_for_family_reads_real_c0_freeze_manifest() -> None:
    """実際の `c0_freeze.build_manifest()` が populate する `u_gt_bound_unit`
    を読む——TILT_GT の凍結 truth unit (`db_per_oct`) は候補宣言 unit と
    一致するが、無関係な unit を渡せば False のまま。"""
    from voice_genesis.calibration import c0_freeze

    real_manifest = c0_freeze.build_manifest(
        c0_freeze._REPO_ROOT, approvals={}, campaign_date_utc="2026-09-02"
    )
    real_tilt_unit = real_manifest["frozen_design"]["fixture_spec"]["TILT_GT"]["u_gt_bound_unit"]
    assert real_tilt_unit == "db_per_oct"
    assert (
        holdout_stage.units_commensurate_for_family(real_manifest, "TILT_GT", "db_per_oct")
        is True
    )
    assert holdout_stage.units_commensurate_for_family(real_manifest, "TILT_GT", "hz") is False


def test_evaluate_directional_meter_from_campaign_derives_units_commensurate_by_default() -> None:
    """R18 対応: `units_commensurate` を明示せずに呼んだとき、`evaluate_
    directional_meter_from_campaign()` は `units_commensurate_for_family()`
    経由で凍結 fixture truth unit と `candidate.unit` から機械導出した値を
    §10.4 条件 (c) へ実際に適用する——同一の (a)/(b) 通過・(c) のみ不通過な
    合成 pair 集合を使い、manifest の `u_gt_bound_unit` を候補 unit と
    一致させる/させないだけで終端 status が変わることを実測する（単位表記の
    差だけが gate 判定を左右する、というのが本 fix の主張そのもの）。"""
    candidate = _tilt_candidate()
    assert candidate.unit == "db_per_oct"

    # 3 truth levels 0.0/0.12/0.24 db_per_oct (adjacent delta=0.12, endpoint
    # delta=0.24). u_gt_bound=0.05/u_num_bound=0.0 -> r_truth=0.1 per pair
    # (both sides share the same family-level bound). Repeat spread is
    # engineered so U_rep=U_proc=0.01 (2*(u_rep+u_proc)=0.04): (a) truth-side
    # resolvability (delta_truth > 0.1) passes for all 3 pairs; (b) output
    # significance (|delta_output| > 0.04) passes for all 3 (deltas are
    # 10.0/20.0/10.0); only the combined v1.0 formula (c)
    # (delta_truth > 0.1 + 0.04 = 0.14) discriminates: the two adjacent
    # 0.12-apart pairs fail it, leaving only 1 resolvable pair (< the
    # sweep's required minimum of 3).
    rows = {"t0": (0.0, 99.98), "t1": (0.12, 109.98), "t2": (0.24, 119.98)}
    matrix_rows = [
        _matrix_row(rid, family="TILT_GT", block="TRUTH_CORE", slope_db_per_oct=truth)
        for rid, (truth, _x) in rows.items()
    ]
    row_by_id = {mr.row_id: mr.row for mr in matrix_rows}
    assignment = {rid: Split.HOLDOUT for rid in rows}

    records: list[measure_stage.MeasurementRecord] = []
    for row_id, (_truth, x) in rows.items():
        for probe_index in range(5):
            records += _wf_record(
                candidate.candidate_id, row_id, probe_index, field="tilt_db_per_oct",
                within_1=x, within_2=x + 0.02, fresh=x + 0.03,
            )
    usable_primary_instances = {(row_id, p) for row_id in rows for p in range(5)}
    expected_sweep_member_row_ids = {"sweep-a": list(rows)}

    def _run(u_gt_bound_unit: str) -> holdout_stage.MeterHoldoutResult:
        manifest = {
            "frozen_design": {
                "fixture_spec": {
                    "TILT_GT": {
                        "u_gt_bound": 0.05,
                        "u_num_bound": 0.0,
                        "u_gt_bound_unit": u_gt_bound_unit,
                    }
                }
            }
        }
        return holdout_stage.evaluate_directional_meter_from_campaign(
            meter_id=MeterId.M2_SPECTRAL_TILT.value,
            family="TILT_GT",
            candidate=candidate,
            manifest=manifest,
            row_by_id=row_by_id,
            matrix_rows=matrix_rows,
            assignment=assignment,
            records=records,
            usable_primary_instances=usable_primary_instances,
            expected_sweep_member_row_ids=expected_sweep_member_row_ids,
            # units_commensurate intentionally omitted -> must be derived.
        )

    commensurate = _run("db_per_oct")  # matches candidate.unit -> True
    assert commensurate.terminal_status == TerminalStatus.DIAGNOSTIC_ONLY.value, commensurate.gate_detail
    assert commensurate.gate_detail["passed"] is False
    assert any(
        "resolvable pair count" in reason for reason in commensurate.gate_detail["failure_reasons"]
    )

    mismatched = _run("hz")  # does not match candidate.unit -> False
    assert mismatched.terminal_status == TerminalStatus.CALIBRATED_DIRECTIONAL.value, mismatched.gate_detail
    assert mismatched.gate_detail["passed"] is True


def test_evaluate_directional_meter_from_campaign_explicit_override_wins_over_derivation() -> None:
    """明示的な `units_commensurate=True/False` は既存呼び出し側（テスト等）
    との後方互換のため、機械導出より優先される。"""
    candidate = _tilt_candidate()
    rows = {"t0": (0.0, 99.98), "t1": (0.12, 109.98), "t2": (0.24, 119.98)}
    matrix_rows = [
        _matrix_row(rid, family="TILT_GT", block="TRUTH_CORE", slope_db_per_oct=truth)
        for rid, (truth, _x) in rows.items()
    ]
    row_by_id = {mr.row_id: mr.row for mr in matrix_rows}
    assignment = {rid: Split.HOLDOUT for rid in rows}
    records: list[measure_stage.MeasurementRecord] = []
    for row_id, (_truth, x) in rows.items():
        for probe_index in range(5):
            records += _wf_record(
                candidate.candidate_id, row_id, probe_index, field="tilt_db_per_oct",
                within_1=x, within_2=x + 0.02, fresh=x + 0.03,
            )
    usable_primary_instances = {(row_id, p) for row_id in rows for p in range(5)}
    expected_sweep_member_row_ids = {"sweep-a": list(rows)}
    # manifest declares a MATCHING unit (would derive True), but the
    # explicit override below forces False -> reaches CALIBRATED_DIRECTIONAL
    # anyway (condition (c) not applied), proving the override wins.
    manifest = {
        "frozen_design": {
            "fixture_spec": {
                "TILT_GT": {"u_gt_bound": 0.05, "u_num_bound": 0.0, "u_gt_bound_unit": "db_per_oct"}
            }
        }
    }
    result = holdout_stage.evaluate_directional_meter_from_campaign(
        meter_id=MeterId.M2_SPECTRAL_TILT.value,
        family="TILT_GT",
        candidate=candidate,
        manifest=manifest,
        row_by_id=row_by_id,
        matrix_rows=matrix_rows,
        assignment=assignment,
        records=records,
        usable_primary_instances=usable_primary_instances,
        expected_sweep_member_row_ids=expected_sweep_member_row_ids,
        units_commensurate=False,
    )
    assert result.terminal_status == TerminalStatus.CALIBRATED_DIRECTIONAL.value, result.gate_detail


def test_evaluate_absolute_meter_reaches_calibrated_absolute_with_c0_frozen_tilt_bounds() -> None:
    """E2E-lite (WP2e AC-d): reproduces
    `test_evaluate_absolute_meter_passes_with_clean_synthetic_data` but with
    `u_gt`/`u_num` swapped from ad hoc test doubles (0.001/0.001) for the
    *actual* v1.1 §V3.3 C0-frozen TILT_GT bounds (`c0_freeze.build_manifest()`
    → `declared_u_gt_u_num_for_family()`) — proving the real frozen numbers
    are small enough, relative to a realistic E_use, to let a clean candidate
    reach `CALIBRATED_ABSOLUTE` (the debt this WP exists to unblock; WP2c's
    `declared_u_gt_u_num_for_family()` returned `None` for every production
    manifest before this WP populated the keys)."""
    from voice_genesis.calibration import c0_freeze

    real_manifest = c0_freeze.build_manifest(
        c0_freeze._REPO_ROOT, approvals={}, campaign_date_utc="2026-09-02"
    )
    u_gt, u_num = holdout_stage.declared_u_gt_u_num_for_family(real_manifest, "TILT_GT")
    real_tilt_spec = real_manifest["frozen_design"]["fixture_spec"]["TILT_GT"]
    assert (u_gt, u_num) == (real_tilt_spec["u_gt_bound"], real_tilt_spec["u_num_bound"])
    assert u_gt == 0.0
    assert u_num < 1.0  # far smaller than a realistic E_use -- gate2'/gate3 stay slack

    observations = [
        holdout_stage.RawInstanceObservation(
            instance_id=f"i{i}",
            domain=Domain.PRIMARY,
            truth=100.0 + i,
            per_process_repeats={
                "within-process": [100.0 + i, 100.01 + i, 99.99 + i],
                "fresh-process-0": [100.005 + i],
                "fresh-process-1": [100.005 + i],
            },
            u_gt=u_gt,
            u_num=u_num,
            e_use=1.0,
        )
        for i in range(12)
    ]
    margins = holdout_stage.build_instance_margins(observations)
    instance_ids = [m.instance_id for m in margins]
    pairs = [
        InvariancePair(pair_id=f"p{i}", axis="axis-a", ds=0.0, e_use_i0=1.0, e_use_ia=1.0)
        for i in range(5)
    ]
    result = holdout_stage.evaluate_absolute_meter(
        MeterId.M2_SPECTRAL_TILT.value,
        ClaimCeiling.ABSOLUTE,
        selected_candidate_id="FAKE-TILT-CAND",
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
    quiet_valid: bool = False,
    extra_values: dict[str, float] | None = None,
) -> list[measure_stage.MeasurementRecord]:
    """`quiet_valid=True` builds a `MeterOutput(values={})` — a well-formed
    output (`missing_reason=None`, `ineligible=False`) that reports no
    finding at all, as opposed to `missing=True`'s `missing_reason`-tagged
    "couldn't produce a value" state (v1.1 §V3.6 round 20 finding #2: the two
    are no longer interchangeable for negative-control detection — only the
    former is a legitimate "valid output exists, and it did not fire").

    `extra_values` merges additional `values` fields alongside `field`
    (v1.3 §X1: the TILT harmonic candidates carry a `hnr_acf_db` auxiliary
    field that their declared `detection_predicate` reads).
    """
    values: dict[str, float | None] = {field: value}
    if extra_values:
        values.update(extra_values)
    output = (
        MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING)
        if missing
        else MeterOutput(values={}) if quiet_valid else MeterOutput(values=values)
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
    """HARMONIC_OLS の実 registry 候補を、`detection_predicate` を明示的に外して
    返す。本ファイルの gate/母集団ロジックのテストは合成 record に
    `tilt_db_per_oct` のみを載せるため、v1.3 §X1 が宣言した
    `hnr_acf_db >= -5.0` predicate をそのまま適用すると「HNR 列が無いので
    全件 non-fire」という別の理由でしか判定できなくなる（検査したいのは
    fire 定義ではなく gate/母集団の算術）。predicate が実際に伝播することは
    `test_control_detection_for_family_applies_candidate_detection_predicate`
    と `test_control_detection_for_family_uses_the_registry_declared_predicate`
    が別途固定する。"""
    return replace(
        next(
            c
            for c in candidates_for_meter(MeterId.M2_SPECTRAL_TILT)
            if c.algorithm_family == "HARMONIC_OLS"
        ),
        detection_predicate=None,
    )


def test_control_detection_for_family_counts_from_holdout_records() -> None:
    """gate5 の control 母集団: negative control は split に依らず全件、
    positive control は `Split.HOLDOUT` に home する truth-core 行のみ
    （v1.1 §V3.2 の一次事実）。negative control 側は `quiet_valid=True`
    （`missing_reason=None` の空 `values`）を使う——round 20 finding #2 採用
    後、`missing=True`（`missing_reason` 付き）は無条件に失敗へ倒れるため
    （`test_control_detection_for_family_negative_control_all_missing_
    reason_is_failure` 参照）、本テストが検証したい「クリーンな non-fire
    母集団で FDR0=0」を表現するには非検出だが有効な出力が要る。"""
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
                quiet_valid=True,
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


def test_control_detection_for_family_uses_the_registry_declared_predicate() -> None:
    """v1.3 §X1: registry が TILT harmonic 候補へ宣言した
    `hnr_acf_db >= -5.0` が holdout gate5 の fire 判定へ実際に効くこと
    （`_tilt_candidate()` が predicate を外している分の往復側）。

    `tilt_db_per_oct` は positive/negative とも有限値を載せる——predicate 無し
    の既定分岐なら両方 fire になる組み合わせで、`hnr_acf_db` の値だけが
    positive を fire・negative を non-fire に分ける（WP-A 実測レンジの
    代表値 -1.0 dB / -9.8 dB を使う）。"""
    candidate = next(
        c
        for c in candidates_for_meter(MeterId.M2_SPECTRAL_TILT)
        if c.algorithm_family == "HARMONIC_OLS"
    )
    assert candidate.detection_predicate == DetectionPredicate(
        field="hnr_acf_db", min_value=-5.0
    )
    pos1 = _matrix_row("pos-1", family="TILT_GT", block="TRUTH_CORE", positive_control=True)
    neg1 = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="NOISE_ONLY",
    )
    matrix_rows = [pos1, neg1]
    assignment = {"pos-1": Split.HOLDOUT, "neg-1": Split.HOLDOUT}

    records: list[measure_stage.MeasurementRecord] = []
    for probe_index in range(5):
        records += _within_fresh_record(
            candidate.candidate_id, "pos-1", probe_index, field="tilt_db_per_oct", value=-6.0,
            extra_values={"hnr_acf_db": -1.0},
        )
        records += _within_fresh_record(
            candidate.candidate_id, "neg-1", probe_index, field="tilt_db_per_oct", value=4.5,
            extra_values={"hnr_acf_db": -9.8},
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
    )
    assert detection.fdr0 == 0.0
    assert detection.fnr1 == 0.0
    assert detection.negative_control_failures == 0
    assert detection.positive_control_failures == 0


def test_control_detection_for_family_applies_candidate_detection_predicate() -> None:
    """#349 第2巡 P2 採用: `control_detection_for_family()` は候補の
    `detection_predicate`（宣言時）をそのまま fire 判定へ渡さなければならない。
    従来は `fixture_controls.detected(r.output)` を predicate 無しで固定呼び
    しており、registry が宣言した非既定 predicate が holdout gate5 で無視
    されていた（`candidate_space_sha()` には記帳されるが判定に反映されない
    静かな不一致）。

    ここでは `min_value=0.0` の predicate を宣言した合成候補で、閾値未満の
    有限値（`tilt_db_per_oct=-3.0`）を negative control 行に与える——
    predicate 無しの既定分岐なら「有限値あり」で fire（false fire）と判定
    されるところ（`test_control_detection_for_family_counts_false_fire_and_
    non_fire` が同じ値でそれを固定している）、predicate 有りでは
    `-3.0 < 0.0` のため non-fire（成功）にならなければならない。positive
    control 側も対称に、閾値以上の値（`5.0`）で fire（成功）することを
    固定する。"""
    candidate = replace(
        _tilt_candidate(),
        detection_predicate=DetectionPredicate(field="tilt_db_per_oct", min_value=0.0),
    )
    pos1 = _matrix_row("pos-1", family="TILT_GT", block="TRUTH_CORE", positive_control=True)
    neg1 = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="NOISE_ONLY",
    )
    matrix_rows = [pos1, neg1]
    assignment = {"pos-1": Split.HOLDOUT, "neg-1": Split.HOLDOUT}

    records: list[measure_stage.MeasurementRecord] = []
    for probe_index in range(5):
        # below the declared predicate threshold: a finite value that the
        # predicate-less default policy would treat as "detected" (false
        # fire), but the declared predicate must treat as non-fire.
        records += _within_fresh_record(
            candidate.candidate_id, "neg-1", probe_index, field="tilt_db_per_oct", value=-3.0
        )
        # at/above the declared predicate threshold: must fire (success).
        records += _within_fresh_record(
            candidate.candidate_id, "pos-1", probe_index, field="tilt_db_per_oct", value=5.0
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
    )
    assert detection.fdr0 == 0.0
    assert detection.fnr1 == 0.0
    assert detection.negative_control_failures == 0
    assert detection.positive_control_failures == 0


def test_control_detection_for_family_negative_control_fully_missing_counts_as_failure() -> None:
    """v1.1 §V3.6 (Codex round 12 P1 ADOPT): a negative-control instance with
    zero own records at all (never measured — e.g. dropped by
    `render_and_measure_holdout()`) must count as a failure (fired=True,
    FDR0 numerator), not as a clean non-detection success. The old `all()`
    -based predicate mapped an empty group to `False` (=success)."""
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
        records += _within_fresh_record(
            candidate.candidate_id, "pos-1", probe_index, field="tilt_db_per_oct", value=-6.0
        )
        # neg-1 has zero own records for every probe -> fully missing instances.

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
    )
    assert detection.n_neg == 5
    assert detection.fdr0 == 1.0
    assert detection.negative_control_failures == 5


def test_control_detection_for_family_negative_control_mixed_detection_and_missing_is_failure() -> None:
    """v1.1 §V3.6 (Codex round 12 P1 ADOPT): within one negative-control
    instance's repeats, a genuine false-fire mixed with missing/invalid
    repeats must still count as a failure (any-fire semantics,
    `candidates.adapter.negative_control_false_fire()`). The old `all()`
    -based predicate let the missing repeats mask the real false-fire
    (`all([True, False, False]) == False` -> incorrectly "success").

    Padding probes use `quiet_valid=True` (not `missing=True`): after round 20
    finding #2, a `missing_reason`-tagged repeat is itself an unconditional
    failure (see `test_control_detection_for_family_negative_control_all_
    missing_reason_is_failure`), so `missing=True` padding would no longer
    isolate this test's actual subject (a real detection surviving mixed with
    missing repeats) — every padding probe would fail too, for an unrelated
    reason."""
    candidate = _tilt_candidate()
    neg1 = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="NOISE_ONLY",
    )
    matrix_rows = [neg1]
    assignment = {"neg-1": Split.HOLDOUT}

    # one probe_index's repeat group mixes a real detection with 2
    # missing/invalid repeats -- not uniformly missing, not uniformly fired.
    records = [
        measure_stage.MeasurementRecord(
            row_id="neg-1",
            probe_index=0,
            candidate_id=candidate.candidate_id,
            repeat_kind="within",
            repeat_index=0,
            process_id="within-process",
            output=MeterOutput(values={"tilt_db_per_oct": -3.0}),  # real false-fire
        ),
        measure_stage.MeasurementRecord(
            row_id="neg-1",
            probe_index=0,
            candidate_id=candidate.candidate_id,
            repeat_kind="within",
            repeat_index=1,
            process_id="within-process",
            output=MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING),
        ),
        measure_stage.MeasurementRecord(
            row_id="neg-1",
            probe_index=0,
            candidate_id=candidate.candidate_id,
            repeat_kind="fresh",
            repeat_index=0,
            process_id="fresh-process-0",
            output=MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING),
        ),
    ]
    # pad with 4 more cleanly-quiet (valid, non-detected) probes so N_neg
    # does not gate the result and so the padding itself does not fail.
    for probe_index in range(1, 5):
        records += _within_fresh_record(
            candidate.candidate_id, "neg-1", probe_index, field="tilt_db_per_oct", value=None,
            quiet_valid=True,
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
    )
    assert detection.n_neg == 5
    assert detection.negative_control_failures == 1
    assert detection.fdr0 == pytest.approx(1.0 / 5.0)


def test_control_detection_for_family_negative_control_all_missing_reason_is_failure() -> None:
    """v1.1 §V3.6 (Codex round 20 P1 ADOPT — fresh evidence after round 12):
    a negative-control instance whose group is *non-empty* but every repeat
    is `missing_reason`-tagged (own records exist, none is a genuine
    detection) must still count as a failure, not a "clean non-detection"
    success. Round 12 only fixed the *empty group* case (round 12's own
    `..._fully_missing_counts_as_failure` test above); it left this
    non-empty-but-entirely-missing case mapped to success, which is the exact
    gap the round 20 review flagged (`any(_detected_output(...))` over an
    all-False sequence is still `False`). Test (a) of the round 20 fix set."""
    candidate = _tilt_candidate()
    neg1 = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="NOISE_ONLY",
    )
    matrix_rows = [neg1]
    assignment = {"neg-1": Split.HOLDOUT}

    records: list[measure_stage.MeasurementRecord] = []
    for probe_index in range(5):
        records += _within_fresh_record(
            candidate.candidate_id, "neg-1", probe_index, field="tilt_db_per_oct", value=None,
            missing=True,
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
    )
    assert detection.n_neg == 5
    assert detection.negative_control_failures == 5
    assert detection.fdr0 == 1.0


def test_control_detection_for_family_negative_control_one_invalid_repeat_fails_despite_rest_quiet() -> None:
    """v1.1 §V3.6 (Codex round 20 P1 ADOPT): a single missing/invalid repeat
    enters the failure numerator even when every *other* repeat in the same
    instance group is a genuine, valid non-detection (`quiet_valid`) — the
    contract is "any missing/invalid repeat", not "the majority" or "the
    aggregate". Test (b) of the round 20 fix set."""
    candidate = _tilt_candidate()
    neg1 = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="NOISE_ONLY",
    )
    matrix_rows = [neg1]
    assignment = {"neg-1": Split.HOLDOUT}

    records = [
        measure_stage.MeasurementRecord(
            row_id="neg-1",
            probe_index=0,
            candidate_id=candidate.candidate_id,
            repeat_kind="within",
            repeat_index=0,
            process_id="within-process",
            output=MeterOutput(values={}),  # valid, quiet
        ),
        measure_stage.MeasurementRecord(
            row_id="neg-1",
            probe_index=0,
            candidate_id=candidate.candidate_id,
            repeat_kind="within",
            repeat_index=1,
            process_id="within-process",
            output=MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING),  # the one invalid repeat
        ),
        measure_stage.MeasurementRecord(
            row_id="neg-1",
            probe_index=0,
            candidate_id=candidate.candidate_id,
            repeat_kind="fresh",
            repeat_index=0,
            process_id="fresh-process-0",
            output=MeterOutput(values={}),  # valid, quiet
        ),
    ]
    # pad with 4 more genuinely clean (valid, quiet) probes.
    for probe_index in range(1, 5):
        records += _within_fresh_record(
            candidate.candidate_id, "neg-1", probe_index, field="tilt_db_per_oct", value=None,
            quiet_valid=True,
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
    )
    assert detection.n_neg == 5
    assert detection.negative_control_failures == 1
    assert detection.fdr0 == pytest.approx(1.0 / 5.0)


# ---------------------------------------------------------------------------
# RUN10-CAL-v1.4 §前提 2 経路 (B) (`DESIGN_VG_METER_CAL_DEBT_v1.4.md`):
# `fixtures.controls.abstained()` — a *non-empty* group whose missing_reason
# is explained by a candidate-declared `Candidate.abstention_reasons` is
# present-and-non-fired, narrowing the round 20 "any missing/invalid repeat
# fails" contract for declaring candidates only.
# ---------------------------------------------------------------------------


def _tilt_candidate_with_abstention(reasons: frozenset) -> object:
    return replace(_tilt_candidate(), abstention_reasons=reasons)


def test_control_detection_for_family_declared_output_missing_on_negative_is_non_fired() -> None:
    """v1.4 §前提 2 経路 (B): every repeat of a non-empty negative-control
    group is `missing_reason=OUTPUT_MISSING`, and the candidate declares
    `abstention_reasons={OUTPUT_MISSING}` — this must now be present-and-
    non-fired (not the round 20 unconditional failure that applied before
    v1.4 / that still applies to a non-declaring candidate, see
    `test_control_detection_for_family_negative_control_all_missing_reason_
    is_failure` above using the same fixture shape)."""
    candidate = _tilt_candidate_with_abstention(frozenset({MissingReason.OUTPUT_MISSING}))
    neg1 = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="NOISE_ONLY",
    )
    matrix_rows = [neg1]
    assignment = {"neg-1": Split.HOLDOUT}

    records: list[measure_stage.MeasurementRecord] = []
    for probe_index in range(5):
        records += _within_fresh_record(
            candidate.candidate_id, "neg-1", probe_index, field="tilt_db_per_oct", value=None,
            missing=True,
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
    )
    assert detection.n_neg == 5
    assert detection.negative_control_failures == 0
    assert detection.fdr0 == 0.0
    assert detection.negative_control_sanctioned_abstentions == 5


def test_control_detection_for_family_undeclared_reason_on_negative_stays_failure() -> None:
    """v1.4 §前提 2 経路 (B): the candidate declares `abstention_reasons`
    but for a *different* reason (`INPUT_MISSING`) than the one actually
    observed (`OUTPUT_MISSING`) — `fixtures.controls.abstained()` requires
    an exact match, so this instance stays a round 20 failure exactly as an
    undeclaring candidate would."""
    candidate = _tilt_candidate_with_abstention(frozenset({MissingReason.INPUT_MISSING}))
    neg1 = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="NOISE_ONLY",
    )
    matrix_rows = [neg1]
    assignment = {"neg-1": Split.HOLDOUT}

    records: list[measure_stage.MeasurementRecord] = []
    for probe_index in range(5):
        records += _within_fresh_record(
            candidate.candidate_id, "neg-1", probe_index, field="tilt_db_per_oct", value=None,
            missing=True,
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
    )
    assert detection.n_neg == 5
    assert detection.negative_control_failures == 5
    assert detection.fdr0 == 1.0
    assert detection.negative_control_sanctioned_abstentions == 0


def test_control_detection_for_family_declared_reason_on_positive_stays_failure() -> None:
    """v1.4 §前提 2 経路 (B): `abstained()` is consulted only by
    `_negative_fired()` — the positive-control side (`_positive_detected()`)
    is unaffected by any `Candidate.abstention_reasons` declaration. A
    positive-control instance whose repeats are all `missing_reason=
    OUTPUT_MISSING` must stay a failure (non-detected) even when the
    candidate declares `abstention_reasons={OUTPUT_MISSING}` — declaring
    abstention never turns a positive-control miss into a success."""
    candidate = _tilt_candidate_with_abstention(frozenset({MissingReason.OUTPUT_MISSING}))
    pos1 = _matrix_row(
        "pos-1", family="TILT_GT", block="TRUTH_CORE", domain=Domain.PRIMARY,
        positive_control=True,
    )
    matrix_rows = [pos1]
    assignment = {"pos-1": Split.HOLDOUT}

    records: list[measure_stage.MeasurementRecord] = []
    for probe_index in range(5):
        records += _within_fresh_record(
            candidate.candidate_id, "pos-1", probe_index, field="tilt_db_per_oct", value=None,
            missing=True,
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
    )
    assert detection.n_pos == 5
    assert detection.positive_control_failures == 5
    assert detection.fnr1 == 1.0


def test_control_detection_for_family_declared_reason_negative_does_not_mask_real_fire() -> None:
    """v1.4 §前提 2 経路 (B): a sanctioned-abstention repeat in one instance
    must not suppress a genuine false fire in a *different* repeat within
    the same instance's group -- mirrors `test_v12_sanctioned_abstention_
    does_not_mask_false_fire_on_other_row` (selection_stage) at the
    holdout-side, single-instance granularity: probe_index 0 has one
    abstained repeat (missing_reason=OUTPUT_MISSING) and one repeat that
    genuinely fires (predicate=None default fire semantics: any finite
    value)."""
    candidate = _tilt_candidate_with_abstention(frozenset({MissingReason.OUTPUT_MISSING}))
    neg1 = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="NOISE_ONLY",
    )
    matrix_rows = [neg1]
    assignment = {"neg-1": Split.HOLDOUT}

    records = [
        measure_stage.MeasurementRecord(
            row_id="neg-1",
            probe_index=0,
            candidate_id=candidate.candidate_id,
            repeat_kind="within",
            repeat_index=0,
            process_id="within-process",
            output=MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING),  # abstained
        ),
        measure_stage.MeasurementRecord(
            row_id="neg-1",
            probe_index=0,
            candidate_id=candidate.candidate_id,
            repeat_kind="within",
            repeat_index=1,
            process_id="within-process",
            output=MeterOutput(values={"tilt_db_per_oct": -3.0}),  # real fire
        ),
        measure_stage.MeasurementRecord(
            row_id="neg-1",
            probe_index=0,
            candidate_id=candidate.candidate_id,
            repeat_kind="fresh",
            repeat_index=0,
            process_id="fresh-process-0",
            output=MeterOutput(values={"tilt_db_per_oct": -3.0}),  # real fire
        ),
    ]
    for probe_index in range(1, 5):
        records += _within_fresh_record(
            candidate.candidate_id, "neg-1", probe_index, field="tilt_db_per_oct", value=None,
            quiet_valid=True,
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
    )
    assert detection.n_neg == 5
    assert detection.negative_control_failures == 1
    assert detection.fdr0 == pytest.approx(1.0 / 5.0)


def test_control_detection_for_family_negative_control_all_valid_and_quiet_is_success() -> None:
    """v1.1 §V3.6 (Codex round 20 P1 ADOPT): the only remaining "non-fire
    (success)" shape after the round 20 fix — every repeat produces a
    well-formed output (`missing_reason=None`, `ineligible=False`) that
    reports no finding at all (`values={}`), i.e. "a valid output exists, and
    it did not fire". Test (c) of the round 20 fix set — this is the
    regression guard proving the fix does not collapse into "always fail"."""
    candidate = _tilt_candidate()
    neg1 = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="NOISE_ONLY",
    )
    matrix_rows = [neg1]
    assignment = {"neg-1": Split.HOLDOUT}

    records: list[measure_stage.MeasurementRecord] = []
    for probe_index in range(5):
        records += _within_fresh_record(
            candidate.candidate_id, "neg-1", probe_index, field="tilt_db_per_oct", value=None,
            quiet_valid=True,
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
    )
    assert detection.n_neg == 5
    assert detection.negative_control_failures == 0
    assert detection.fdr0 == 0.0


def test_control_detection_for_family_sanctioned_abstention_silence_f0_unusable_not_fired() -> None:
    """v1.3 (Codex #350 round 3 P1 ADOPT): a SILENCE negative-control instance
    an F0-dependent candidate was never called on (own group entirely empty
    -- an `F0_UNUSABLE` skip, not a call that came back silent) is the
    single sanctioned abstention (`fixtures.controls.SANCTIONED_ABSTENTIONS`
    == `{(SILENCE, "F0_UNUSABLE")}`) and must be counted as present-but-
    non-fired (FDR0 denominator only), not as a failure. This is the exact
    scenario PR #350 round 3 P1 identified: every TILT harmonic ABSOLUTE
    candidate was structurally unable to reach `CALIBRATED_ABSOLUTE` because
    `FDR0` could never be `0` for a newly-eligible F0-dependent candidate.
    probe_index 0 is entirely missing (the sanctioned instance); probes 1-4
    are padded with genuinely quiet, valid output so `N_neg==5` without
    themselves being sanctioned or fired -- isolating the count to exactly
    one sanctioned instance."""
    candidate = _tilt_candidate()
    neg1 = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="SILENCE",
    )
    matrix_rows = [neg1]
    assignment = {"neg-1": Split.HOLDOUT}
    # probe_index 0 has zero own records at all (F0_UNUSABLE skip).
    records: list[measure_stage.MeasurementRecord] = []
    for probe_index in range(1, 5):
        records += _within_fresh_record(
            candidate.candidate_id, "neg-1", probe_index, field="tilt_db_per_oct", value=None,
            quiet_valid=True,
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
        control_class_by_negative_row_id={"neg-1": "SILENCE"},
        missing_reason_by_negative_row_id={"neg-1": "F0_UNUSABLE"},
    )
    assert detection.n_neg == 5
    assert detection.negative_control_failures == 0
    assert detection.fdr0 == 0.0
    assert detection.negative_control_sanctioned_abstentions == 1


def test_control_detection_for_family_sanctioned_abstention_noise_only_f0_unusable_not_fired() -> None:
    """v1.4 §前提 3 (`DESIGN_VG_METER_CAL_DEBT_v1.4.md`, P2 census PASS —
    `scratchpad/v14/p23/p23_report.txt` §5.1): `fixtures.controls.
    SANCTIONED_ABSTENTIONS` now includes `(NOISE_ONLY, "F0_UNUSABLE")`
    alongside `(SILENCE, "F0_UNUSABLE")` (無声対照に F0 は存在しないため、
    F0 依存候補の NOISE_ONLY skip は SILENCE と同じ理由で正しい棄権)。an
    entirely-missing NOISE_ONLY instance whose reason is `F0_UNUSABLE` must
    now be counted as present-but-non-fired (FDR0 denominator only), mirroring
    the pre-existing SILENCE test above (supersedes the pre-v1.4
    `..._excludes_noise_only` test that pinned the narrower v1.2/v1.3
    vocabulary)."""
    candidate = _tilt_candidate()
    neg1 = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="NOISE_ONLY",
    )
    matrix_rows = [neg1]
    assignment = {"neg-1": Split.HOLDOUT}
    records: list[measure_stage.MeasurementRecord] = []
    for probe_index in range(1, 5):
        records += _within_fresh_record(
            candidate.candidate_id, "neg-1", probe_index, field="tilt_db_per_oct", value=None,
            quiet_valid=True,
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
        control_class_by_negative_row_id={"neg-1": "NOISE_ONLY"},
        missing_reason_by_negative_row_id={"neg-1": "F0_UNUSABLE"},
    )
    assert detection.n_neg == 5
    assert detection.negative_control_failures == 0
    assert detection.fdr0 == 0.0
    assert detection.negative_control_sanctioned_abstentions == 1


def test_control_detection_for_family_sanctioned_abstention_closed_vocabulary_excludes_pure_sine() -> None:
    """the closed vocabulary is still closed -- `(PURE_SINE, "F0_UNUSABLE")`
    is not a member of `fixtures.controls.SANCTIONED_ABSTENTIONS` (v1.4
    §前提 3 explicitly does not extend sanctioning beyond SILENCE/NOISE_ONLY:
    `DESIGN_VG_METER_CAL_DEBT_v1.4.md` 前提 3 "PURE_SINE 等には拡張しない"),
    so an entirely-missing PURE_SINE instance must still be counted as a
    failure exactly as before."""
    candidate = _tilt_candidate()
    neg1 = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="PURE_SINE",
    )
    matrix_rows = [neg1]
    assignment = {"neg-1": Split.HOLDOUT}
    records: list[measure_stage.MeasurementRecord] = []
    for probe_index in range(1, 5):
        records += _within_fresh_record(
            candidate.candidate_id, "neg-1", probe_index, field="tilt_db_per_oct", value=None,
            quiet_valid=True,
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
        control_class_by_negative_row_id={"neg-1": "PURE_SINE"},
        missing_reason_by_negative_row_id={"neg-1": "F0_UNUSABLE"},
    )
    assert detection.n_neg == 5
    assert detection.negative_control_failures == 1
    assert detection.fdr0 == pytest.approx(1.0 / 5.0)
    assert detection.negative_control_sanctioned_abstentions == 0


def test_control_detection_for_family_sanctioned_abstention_closed_vocabulary_excludes_output_missing() -> None:
    """v1.3 (Codex #350 round 3 P1 ADOPT): the closed vocabulary is not
    extended by this change -- `(SILENCE, "OUTPUT_MISSING")` is not a member
    of `fixtures.controls.SANCTIONED_ABSTENTIONS` either (only `(SILENCE,
    "F0_UNUSABLE")` is), so an entirely-missing SILENCE instance whose
    ledger reason is `OUTPUT_MISSING` must still be counted as a failure."""
    candidate = _tilt_candidate()
    neg1 = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="SILENCE",
    )
    matrix_rows = [neg1]
    assignment = {"neg-1": Split.HOLDOUT}
    records: list[measure_stage.MeasurementRecord] = []
    for probe_index in range(1, 5):
        records += _within_fresh_record(
            candidate.candidate_id, "neg-1", probe_index, field="tilt_db_per_oct", value=None,
            quiet_valid=True,
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
        control_class_by_negative_row_id={"neg-1": "SILENCE"},
        missing_reason_by_negative_row_id={"neg-1": "OUTPUT_MISSING"},
    )
    assert detection.n_neg == 5
    assert detection.negative_control_failures == 1
    assert detection.fdr0 == pytest.approx(1.0 / 5.0)
    assert detection.negative_control_sanctioned_abstentions == 0


def test_control_detection_for_family_sanctioned_abstention_kwargs_omitted_keeps_old_behavior() -> None:
    """v1.3 (Codex #350 round 3 P1 ADOPT): backward compatibility -- when
    `control_class_by_negative_row_id`/`missing_reason_by_negative_row_id`
    are omitted (the default `None`), a SILENCE instance that would
    otherwise qualify as a sanctioned abstention must still be counted as a
    failure, byte-for-byte the pre-v1.3 behavior (mirrors
    `..._negative_control_fully_missing_counts_as_failure` above, using a
    SILENCE control_class specifically to prove the sanctioning logic
    requires the new kwargs, not merely the control class)."""
    candidate = _tilt_candidate()
    neg1 = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="SILENCE",
    )
    matrix_rows = [neg1]
    assignment = {"neg-1": Split.HOLDOUT}
    records: list[measure_stage.MeasurementRecord] = []
    for probe_index in range(1, 5):
        records += _within_fresh_record(
            candidate.candidate_id, "neg-1", probe_index, field="tilt_db_per_oct", value=None,
            quiet_valid=True,
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
    )
    assert detection.n_neg == 5
    assert detection.negative_control_failures == 1
    assert detection.fdr0 == pytest.approx(1.0 / 5.0)
    assert detection.negative_control_sanctioned_abstentions == 0


def test_control_detection_for_family_sanctioned_abstention_only_applies_to_entirely_missing_group() -> None:
    """v1.3 (Codex #350 round 3 P1 ADOPT): the design ruling restricts
    reclassification to the *entirely-missing* branch (`not group`) only --
    a non-empty group that contains a `missing_reason`-tagged record stays a
    failure exactly as the round 20 contract requires, even when the
    sanctioned mapping matches that row_id. This instance has an own record
    (the candidate *was* called), one of whose repeats is `missing_reason`-
    tagged -- the round 20 "any missing/invalid repeat fails" rule applies,
    unaffected by the v1.3 sanctioning (which only ever inspects
    entirely-empty groups)."""
    candidate = _tilt_candidate()
    neg1 = _matrix_row(
        "neg-1", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="SILENCE",
    )
    matrix_rows = [neg1]
    assignment = {"neg-1": Split.HOLDOUT}

    records = [
        measure_stage.MeasurementRecord(
            row_id="neg-1",
            probe_index=0,
            candidate_id=candidate.candidate_id,
            repeat_kind="within",
            repeat_index=0,
            process_id="within-process",
            output=MeterOutput(values={}),  # valid, quiet
        ),
        measure_stage.MeasurementRecord(
            row_id="neg-1",
            probe_index=0,
            candidate_id=candidate.candidate_id,
            repeat_kind="within",
            repeat_index=1,
            process_id="within-process",
            output=MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING),
        ),
        measure_stage.MeasurementRecord(
            row_id="neg-1",
            probe_index=0,
            candidate_id=candidate.candidate_id,
            repeat_kind="fresh",
            repeat_index=0,
            process_id="fresh-process-0",
            output=MeterOutput(values={}),  # valid, quiet
        ),
    ]
    for probe_index in range(1, 5):
        records += _within_fresh_record(
            candidate.candidate_id, "neg-1", probe_index, field="tilt_db_per_oct", value=None,
            quiet_valid=True,
        )

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
        control_class_by_negative_row_id={"neg-1": "SILENCE"},
        missing_reason_by_negative_row_id={"neg-1": "F0_UNUSABLE"},
    )
    assert detection.n_neg == 5
    assert detection.negative_control_failures == 1
    assert detection.fdr0 == pytest.approx(1.0 / 5.0)
    assert detection.negative_control_sanctioned_abstentions == 0


def test_control_detection_for_family_positive_control_audit_no_analogous_hole() -> None:
    """v1.1 §V3.6 (Codex round 20 P1 ADOPT, positive-side audit): unlike
    `_negative_fired()`, `_positive_detected()`'s `all()`-based predicate has
    no "all not-detected -> success" fallback to misclassify — a single
    missing/invalid (or, per the `_detected_output()` refinement, a valid but
    empty `values={}`) repeat already makes `all()` False, correctly landing
    on non-fire (failure). This test fixes 3 instances covering the 3 ways a
    repeat can be non-detected (missing_reason, ineligible, valid-but-empty)
    each mixed with an otherwise-fully-detected group, confirming all 3 are
    failures — no round 20 style gap exists on the positive side.
    Test (d) of the round 20 fix set."""
    candidate = _tilt_candidate()
    pos1 = _matrix_row("pos-1", family="TILT_GT", block="TRUTH_CORE", positive_control=True)
    pos2 = _matrix_row(
        "pos-2", family="TILT_GT", block="TRUTH_CORE", positive_control=True, domain=Domain.PRIMARY
    )
    pos3 = _matrix_row(
        "pos-3", family="TILT_GT", block="TRUTH_CORE", positive_control=False, domain=Domain.PRIMARY
    )
    matrix_rows = [pos1, pos2, pos3]
    assignment = {"pos-1": Split.HOLDOUT, "pos-2": Split.HOLDOUT, "pos-3": Split.HOLDOUT}

    records: list[measure_stage.MeasurementRecord] = []
    for probe_index in range(5):
        # pos-1: one repeat missing_reason-tagged, rest detected.
        records += [
            measure_stage.MeasurementRecord(
                row_id="pos-1", probe_index=probe_index, candidate_id=candidate.candidate_id,
                repeat_kind="within", repeat_index=0, process_id="within-process",
                output=MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING),
            ),
            measure_stage.MeasurementRecord(
                row_id="pos-1", probe_index=probe_index, candidate_id=candidate.candidate_id,
                repeat_kind="within", repeat_index=1, process_id="within-process",
                output=MeterOutput(values={"tilt_db_per_oct": -6.0}),
            ),
            measure_stage.MeasurementRecord(
                row_id="pos-1", probe_index=probe_index, candidate_id=candidate.candidate_id,
                repeat_kind="fresh", repeat_index=0, process_id="fresh-process-0",
                output=MeterOutput(values={"tilt_db_per_oct": -6.0}),
            ),
        ]
        # pos-2: one repeat ineligible, rest detected.
        records += [
            measure_stage.MeasurementRecord(
                row_id="pos-2", probe_index=probe_index, candidate_id=candidate.candidate_id,
                repeat_kind="within", repeat_index=0, process_id="within-process",
                output=MeterOutput(ineligible=True, ineligible_reason="INELIGIBLE_DEPENDENCY_ABSENT"),
            ),
            measure_stage.MeasurementRecord(
                row_id="pos-2", probe_index=probe_index, candidate_id=candidate.candidate_id,
                repeat_kind="within", repeat_index=1, process_id="within-process",
                output=MeterOutput(values={"tilt_db_per_oct": -6.0}),
            ),
            measure_stage.MeasurementRecord(
                row_id="pos-2", probe_index=probe_index, candidate_id=candidate.candidate_id,
                repeat_kind="fresh", repeat_index=0, process_id="fresh-process-0",
                output=MeterOutput(values={"tilt_db_per_oct": -6.0}),
            ),
        ]
        # pos-3: one repeat valid-but-empty, rest detected.
        records += [
            measure_stage.MeasurementRecord(
                row_id="pos-3", probe_index=probe_index, candidate_id=candidate.candidate_id,
                repeat_kind="within", repeat_index=0, process_id="within-process",
                output=MeterOutput(values={}),
            ),
            measure_stage.MeasurementRecord(
                row_id="pos-3", probe_index=probe_index, candidate_id=candidate.candidate_id,
                repeat_kind="within", repeat_index=1, process_id="within-process",
                output=MeterOutput(values={"tilt_db_per_oct": -6.0}),
            ),
            measure_stage.MeasurementRecord(
                row_id="pos-3", probe_index=probe_index, candidate_id=candidate.candidate_id,
                repeat_kind="fresh", repeat_index=0, process_id="fresh-process-0",
                output=MeterOutput(values={"tilt_db_per_oct": -6.0}),
            ),
        ]

    detection = holdout_stage.control_detection_for_family(
        matrix_rows=matrix_rows,
        assignment=assignment,
        family="TILT_GT",
        candidate=candidate,
        records=records,
    )
    assert detection.n_pos == 15  # pos-1 + pos-2 + pos-3, 5 probes each
    assert detection.positive_control_failures == 15  # every instance non-fires
    assert detection.fnr1 == 1.0


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


def test_build_invariance_pairs_for_family_pairs_even_when_anchor_homes_outside_holdout() -> None:
    """R15 対応（Codex 第 15 巡 P1 採用、2026-09-05）: anchor 行は split
    非依存の共有 control として扱う——anchor の home split が CALIBRATION
    でも、varied（CONFOUND）行さえ HOLDOUT に home すれば pair は
    構造的に消えない（5 件 = PROBE_REPEATS）。"""
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
    # anchor homes to CALIBRATION -- the pre-fix implementation required
    # both sides in HOLDOUT and would silently drop every pair here.
    assignment = {"anchor-1": Split.CALIBRATION, "confound-1": Split.HOLDOUT}

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
    assert len(pairs_by_axis["sr_hz"]) == 5  # 1 pair per probe_index


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


def test_evaluate_absolute_meter_from_campaign_control_detection_and_margins_summary_v14() -> None:
    """RUN10-CAL-v1.4 §前提 6 (`DESIGN_VG_METER_CAL_DEBT_v1.4.md`, Test
    Strategy): a small synthetic ledger/records fixture reproducing the
    `fdr0=0.2 / fnr1=0.0 / n_neg=10 / n_pos=25 / sanctioned=5` pattern the
    memo's regression test calls for (the memo explicitly directs synthetic
    reproduction over fixture-izing the real 2dde4014 read path: "tests は
    小さな合成 ledger で同じ数値パターンを固定する"). Confirms `gate_detail
    ["control_detection"]`/`gate_detail["margins_summary"]` are always-
    present, self-describing blocks with the exact expected values.

    - `neg-a` (SILENCE): 5 probes, every repeat `missing_reason=
      OUTPUT_MISSING` -- the candidate declares `abstention_reasons=
      {OUTPUT_MISSING}` (v1.4 §前提 2 経路 (B)), so all 5 are present-and-
      non-fired (sanctioned).
    - `neg-b` (NOISE_ONLY): 2 real fires + 3 genuine quiet non-detections ->
      2 failures / 5 instances.
    - n_neg = 5 + 5 = 10; negative_control_failures = 2; fdr0 = 2/10 = 0.2.
    - 5 TRUTH_CORE rows x 5 probes, all cleanly detected at the declared
      truth -> n_pos = 25, fnr1 = 0.0, zero AE/bias (margins_summary is all
      zeros except the frozen U_GT+U_num/E_use/G constants)."""
    candidate = replace(
        _tilt_candidate(), abstention_reasons=frozenset({MissingReason.OUTPUT_MISSING})
    )
    truth_core_rows = [
        _matrix_row(f"pos-{i}", family="TILT_GT", block="TRUTH_CORE", slope_db_per_oct=-6.0)
        for i in range(5)
    ]
    neg_a = _matrix_row(
        "neg-a", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="SILENCE",
    )
    neg_b = _matrix_row(
        "neg-b", family="TILT_GT", block="NEGATIVE_CONTROL", domain=Domain.BOUNDARY,
        control_class="NOISE_ONLY",
    )
    matrix_rows = truth_core_rows + [neg_a, neg_b]
    row_by_id = {mr.row_id: mr.row for mr in matrix_rows}
    assignment = {mr.row_id: Split.HOLDOUT for mr in matrix_rows}

    records: list[measure_stage.MeasurementRecord] = []
    for row in truth_core_rows:
        for probe_index in range(5):
            records += _within_fresh_record(
                candidate.candidate_id, row.row_id, probe_index, field="tilt_db_per_oct",
                value=-6.0,
            )
    for probe_index in range(5):
        records += _within_fresh_record(
            candidate.candidate_id, "neg-a", probe_index, field="tilt_db_per_oct", value=None,
            missing=True,
        )
    for probe_index in range(2):
        records += _within_fresh_record(
            candidate.candidate_id, "neg-b", probe_index, field="tilt_db_per_oct", value=-3.0,
        )
    for probe_index in range(2, 5):
        records += _within_fresh_record(
            candidate.candidate_id, "neg-b", probe_index, field="tilt_db_per_oct", value=None,
            quiet_valid=True,
        )

    e_use_row = replace(
        _e_use_row(candidate.construct, mode="absolute"),
        unit=candidate.unit, domain=candidate.domain, e_use_value=2.0,
    )
    manifest = {
        "frozen_design": {"fixture_spec": {"TILT_GT": {"u_gt_bound": 0.01, "u_num_bound": 0.01}}}
    }
    expected_primary_instances = {(row.row_id, p) for row in truth_core_rows for p in range(5)}

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

    control_detection = result.gate_detail["control_detection"]
    assert control_detection == {
        "fdr0": 0.2,
        "fnr1": 0.0,
        "n_neg": 10,
        "n_pos": 25,
        "min_count_met": True,
        "negative_control_failures": 2,
        "positive_control_failures": 0,
        "negative_control_sanctioned_abstentions": 5,
    }
    margins = result.gate_detail["margins_summary"]
    assert margins["n"] == 25
    assert margins["ae_q50"] == pytest.approx(0.0)
    assert margins["ae_q95"] == pytest.approx(0.0)
    assert margins["ae_max"] == pytest.approx(0.0)
    assert margins["abs_bias"] == pytest.approx(0.0)
    assert margins["u_gt_plus_u_num"] == pytest.approx(0.02)
    assert margins["e_use_median"] == pytest.approx(2.0)
    # G[i] = AE+U_GT+U_num+U_rep+U_proc-E_use = 0+0.01+0.01+0+0-2.0 = -1.98
    assert margins["g_q95"] == pytest.approx(-1.98)
    assert margins["g_max"] == pytest.approx(-1.98)
    assert "g_values" not in result.gate_detail  # raw list popped, summary only


def _wf_record(
    candidate_id: str,
    row_id: str,
    probe_index: int,
    *,
    field: str,
    within_1: float,
    within_2: float,
    fresh: float,
) -> list[measure_stage.MeasurementRecord]:
    """`_within_fresh_record()` と異なり、within-process の 2 repeat と
    fresh-process-0 の 1 repeat に別々の値を与える（R18 regression test 専用
    — instance ごとに process 間で非対称な値を作れないと、pooled
    two_stage_median との差異を作れない）。"""
    return [
        measure_stage.MeasurementRecord(
            row_id=row_id, probe_index=probe_index, candidate_id=candidate_id,
            repeat_kind="within", repeat_index=0, process_id="within-process",
            output=MeterOutput(values={field: within_1}),
        ),
        measure_stage.MeasurementRecord(
            row_id=row_id, probe_index=probe_index, candidate_id=candidate_id,
            repeat_kind="within", repeat_index=1, process_id="within-process",
            output=MeterOutput(values={field: within_2}),
        ),
        measure_stage.MeasurementRecord(
            row_id=row_id, probe_index=probe_index, candidate_id=candidate_id,
            repeat_kind="fresh", repeat_index=0, process_id="fresh-process-0",
            output=MeterOutput(values={field: fresh}),
        ),
    ]


def test_build_directional_gate_inputs_uses_per_instance_two_stage_median_not_pooled() -> None:
    """R18 対応（Codex PR #346 第 18 巡 P1 採用、2026-09-05）: 旧実装は
    `two_stage_median()` へ渡す `per_process` バケットを row 単位でしか
    分けておらず、同じ truth level の 5 probe instance
    (`fixture_controls.PROBE_REPEATS`) の repeat が同一 process バケットへ
    黙って併合されていた（`m[i] = median_p(median_r(x))` の `i` は 1 probe
    instance を指すはずが、5 instance ぶんの生値が「1 instance の複数
    repeat」であるかのように pool されていた）。

    truth=1.0 の sweep member（5 probe instance）を、instance ごとの
    within/fresh の値を意図的に非対称にして構成する: 個別に two_stage_median
    を取ると全 instance が m[i]=[10.5相当の分布]（探索で見つけた具体値）に
    なるが、pooled（旧実装のバグ）で計算すると別の値になる——truth=2.0 の
    もう 1 sweep member（全 instance が within=fresh=15.0 の一様値、pooled/
    per-instance いずれの集計でも 15.0 で一致する対照）と組み合わせると、
    delta_output の符号が新旧で反転する:

    - 新実装（per-instance m[i] の median、本 fix）: level(truth=1.0) =
      median([m_0..m_4]) = 10.5、delta_output = 15.0 - 10.5 = +4.5
      （delta_truth も + なので `correct_sign=True`）。
    - 旧実装（pooled、bug）: level(truth=1.0) = 20.0
      （`observables.two_stage_median()` を pooled dict へ直接適用すると
      再現できる——下記アサーションで明示的に検算する）、delta_output =
      15.0 - 20.0 = -5.0（`correct_sign=False` になり、偽の
      DIAGNOSTIC_ONLY/逆符号を生む）。
    """
    candidate = next(
        c
        for c in candidates_for_meter(MeterId.M5_TRANSITION)
        if c.algorithm_family == "WAVE_DISCONTINUITY"
    )
    row_low = _matrix_row("t-low", family="TRANSITION_GT", block="TRUTH_CORE", discontinuity_magnitude=1.0)
    row_high = _matrix_row(
        "t-high", family="TRANSITION_GT", block="TRUTH_CORE", discontinuity_magnitude=2.0
    )
    matrix_rows = [row_low, row_high]
    row_by_id = {mr.row_id: mr.row for mr in matrix_rows}
    assignment = {"t-low": Split.HOLDOUT, "t-high": Split.HOLDOUT}
    manifest = {
        "frozen_design": {"fixture_spec": {"TRANSITION_GT": {"u_gt_bound": 0.001, "u_num_bound": 0.001}}}
    }

    # per-instance (within_1, within_2, fresh) values for truth=1.0's 5
    # probes, found by search to maximize |per-instance-median vs pooled|.
    low_instance_values = [
        (3.0, 20.0, 20.0),
        (20.0, 2.0, 20.0),
        (20.0, 20.0, 0.0),
        (20.0, 20.0, 0.0),
        (1.0, 1.0, 20.0),
    ]
    records: list[measure_stage.MeasurementRecord] = []
    for probe_index, (w1, w2, f) in enumerate(low_instance_values):
        records += _wf_record(
            candidate.candidate_id, "t-low", probe_index, field="magnitude",
            within_1=w1, within_2=w2, fresh=f,
        )
    for probe_index in range(5):
        records += _wf_record(
            candidate.candidate_id, "t-high", probe_index, field="magnitude",
            within_1=15.0, within_2=15.0, fresh=15.0,
        )

    usable_primary_instances = {("t-low", p) for p in range(5)} | {("t-high", p) for p in range(5)}
    expected_sweep_member_row_ids = {"sweep-a": ["t-low", "t-high"]}

    bundle = holdout_stage.build_directional_gate_inputs(
        family="TRANSITION_GT",
        candidate=candidate,
        row_by_id=row_by_id,
        matrix_rows=matrix_rows,
        assignment=assignment,
        records=records,
        usable_primary_instances=usable_primary_instances,
        expected_sweep_member_row_ids=expected_sweep_member_row_ids,
        manifest=manifest,
    )
    assert len(bundle.pairs) == 1
    # Codex #350 round 4 P2 採用: no NEGATIVE_CONTROL rows in this fixture,
    # so the sanctioned-abstention accounting field is a plain 0 (the field
    # is still populated -- not silently dropped -- for a self-describing
    # bundle).
    assert bundle.negative_control_sanctioned_abstentions == 0
    pair = bundle.pairs[0]
    assert pair.delta_truth == pytest.approx(1.0)
    # new (fixed) per-instance aggregation: level(truth=1.0) = median of the
    # 5 per-instance two_stage_median values, not a pooled two_stage_median
    # over all 5 instances' raw repeats combined.
    per_instance_m = [
        holdout_stage.two_stage_median({"within-process": [w1, w2], "fresh-process-0": [f]})
        for (w1, w2, f) in low_instance_values
    ]
    expected_new_level_low = statistics.median(per_instance_m)
    assert expected_new_level_low == pytest.approx(10.5)
    assert pair.delta_output == pytest.approx(15.0 - expected_new_level_low)
    assert pair.correct_sign is True

    # explicit regression check against the OLD (buggy) pooled computation:
    # merging all 5 probe instances' repeats into shared per-process buckets
    # (ignoring the (row_id, probe_index) instance boundary) produces a
    # DIFFERENT level value with the OPPOSITE delta_output sign.
    pooled_within = [v for (w1, w2, _f) in low_instance_values for v in (w1, w2)]
    pooled_fresh = [f for (_w1, _w2, f) in low_instance_values]
    old_pooled_level_low = holdout_stage.two_stage_median(
        {"within-process": pooled_within, "fresh-process-0": pooled_fresh}
    )
    assert old_pooled_level_low == pytest.approx(20.0)
    old_delta_output = 15.0 - old_pooled_level_low
    assert old_delta_output == pytest.approx(-5.0)
    assert (pair.delta_output > 0) != (old_delta_output > 0)  # sign flip, as documented above


# ---------------------------------------------------------------------------
# RUN10-CAL-v1.4 §前提 5 (`DESIGN_VG_METER_CAL_DEBT_v1.4.md`):
# `build_directional_gate_inputs` applies `observables.apply_polarity()` to
# `delta_output` (and `DirectionalPair.correct_sign`) for a candidate that
# declares `truth_polarity`.
# ---------------------------------------------------------------------------


def _aperiodicity_directional_bundle(candidate) -> object:
    """2-level TRUTH_CORE sweep for APERIODICITY_GT: hnr_db decreases
    (12.0 -> 9.0) as injected_noise_fraction increases (0.0 -> 0.1) -- the
    physically-correct inverse relationship `M2A-B0-AUTOCORR-PERIODICITY`'s
    `truth_polarity=-1` declares."""
    row_low = _matrix_row(
        "a-low", family="APERIODICITY_GT", block="TRUTH_CORE", injected_noise_fraction=0.0
    )
    row_high = _matrix_row(
        "a-high", family="APERIODICITY_GT", block="TRUTH_CORE", injected_noise_fraction=0.1
    )
    matrix_rows = [row_low, row_high]
    row_by_id = {mr.row_id: mr.row for mr in matrix_rows}
    assignment = {"a-low": Split.HOLDOUT, "a-high": Split.HOLDOUT}
    manifest = {
        "frozen_design": {
            "fixture_spec": {"APERIODICITY_GT": {"u_gt_bound": 0.001, "u_num_bound": 0.001}}
        }
    }
    records: list[measure_stage.MeasurementRecord] = []
    for probe_index in range(5):
        records += _within_fresh_record(
            candidate.candidate_id, "a-low", probe_index, field="hnr_db", value=12.0
        )
        records += _within_fresh_record(
            candidate.candidate_id, "a-high", probe_index, field="hnr_db", value=9.0
        )
    usable_primary_instances = {("a-low", p) for p in range(5)} | {("a-high", p) for p in range(5)}
    expected_sweep_member_row_ids = {"sweep-a": ["a-low", "a-high"]}
    return holdout_stage.build_directional_gate_inputs(
        family="APERIODICITY_GT",
        candidate=candidate,
        row_by_id=row_by_id,
        matrix_rows=matrix_rows,
        assignment=assignment,
        records=records,
        usable_primary_instances=usable_primary_instances,
        expected_sweep_member_row_ids=expected_sweep_member_row_ids,
        manifest=manifest,
    )


def test_build_directional_gate_inputs_applies_declared_polarity() -> None:
    candidate = candidate_by_id("M2A-B0-AUTOCORR-PERIODICITY")
    assert candidate.truth_polarity == -1
    bundle = _aperiodicity_directional_bundle(candidate)
    assert len(bundle.pairs) == 1
    pair = bundle.pairs[0]
    assert pair.delta_truth == pytest.approx(0.1)
    # raw delta_output = 9.0 - 12.0 = -3.0; apply_polarity(-3.0, -1) = 3.0.
    assert pair.delta_output == pytest.approx(3.0)
    assert pair.correct_sign is True


def test_build_directional_gate_inputs_no_polarity_leaves_raw_delta_output() -> None:
    """the same physically-correct-inverse shape as above, but the
    candidate declares no polarity — raw (mis-signed, from this construct's
    perspective) `delta_output`/`correct_sign` must be used unchanged,
    matching pre-v1.4 behavior exactly."""
    candidate = replace(candidate_by_id("M2A-B0-AUTOCORR-PERIODICITY"), truth_polarity=None)
    bundle = _aperiodicity_directional_bundle(candidate)
    assert len(bundle.pairs) == 1
    pair = bundle.pairs[0]
    assert pair.delta_truth == pytest.approx(0.1)
    assert pair.delta_output == pytest.approx(-3.0)
    assert pair.correct_sign is False


def test_evaluate_directional_meter_from_campaign_pairs_summary_resolvable_count_v14() -> None:
    """RUN10-CAL-v1.4 §前提 6 (`DESIGN_VG_METER_CAL_DEBT_v1.4.md`, Test
    Strategy): a small synthetic ledger/records fixture whose shape is
    derived from the **frozen matrix itself** (allowed evidence per the v1.4
    doc's §Y-0): APERIODICITY_GT declares `bandwise_band` held-fixed sweep
    contexts over the 6 `injected_noise_fraction` truth levels
    `0.0/0.01/0.03/0.1/0.3/0.6` (`fixtures/axes.py`, the same 6-level ladder
    the P2 census walks), and the family's declared `u_gt_bound`/`u_num_bound`
    fix the resolvability threshold. The same `resolvable_count=18` was also
    seen in quarantined campaign `RUN10-CAL-20260908-2dde4014`
    (`status: QUARANTINED`, `claimable=false`; 2026-09-09 execution-boundary
    correction) — that observation is **evidence-only and is not the
    justification for this test**; the number below follows from the
    synthetic fixture's own structure. 2 sweeps x C(6,2)=15 possible pairs
    each = 30 total, of which exactly 9
    per sweep clear the truth-resolvability threshold `Delta_truth >
    2*(U_GT+U_num)` (only pairs against the {0.3, 0.6} levels, given
    `U_GT+U_num=0.064` here) -> 9*2=18 (the memo's §前提 8 note: "微小段
    0->0.01->0.03->0.1 は...構造的に解像不能" — this fixture reproduces that
    same resolution boundary). `U_rep`/`U_num` are forced to 0 (uniform
    repeats) so output-side significance never gates a truth-resolvable pair
    out. This test exercises the memo's Implementation Approach directly
    (the fixture reproduces the *pattern*, not a fixture-ized replay of the
    real ledger — memo Test Strategy: "tests は小さな合成 ledger で同じ数値
    パターンを固定する")."""
    candidate = candidate_by_id("M2A-B0-AUTOCORR-PERIODICITY")
    assert candidate.truth_polarity == -1
    levels = [0.0, 0.01, 0.03, 0.1, 0.3, 0.6]
    hnr_by_level = {0.0: 12.0, 0.01: 12.4, 0.03: 11.3, 0.1: 9.1, 0.3: 2.7, 0.6: -15.0}

    matrix_rows = []
    expected_sweep_member_row_ids: dict[str, list[str]] = {}
    for sweep_name in ("sweep-a", "sweep-b"):
        row_ids = []
        for level in levels:
            row_id = f"{sweep_name}-{level}"
            matrix_rows.append(
                _matrix_row(
                    row_id, family="APERIODICITY_GT", block="TRUTH_CORE",
                    injected_noise_fraction=level,
                )
            )
            row_ids.append(row_id)
        expected_sweep_member_row_ids[sweep_name] = row_ids

    row_by_id = {mr.row_id: mr.row for mr in matrix_rows}
    assignment = {mr.row_id: Split.HOLDOUT for mr in matrix_rows}
    records: list[measure_stage.MeasurementRecord] = []
    usable_primary_instances: set[tuple[str, int]] = set()
    for mr in matrix_rows:
        level = mr.row.injected_noise_fraction
        records += _within_fresh_record(
            candidate.candidate_id, mr.row_id, 0, field="hnr_db", value=hnr_by_level[level],
        )
        usable_primary_instances.add((mr.row_id, 0))

    manifest = {
        "frozen_design": {
            "fixture_spec": {
                "APERIODICITY_GT": {"u_gt_bound": 0.05, "u_num_bound": 0.014}
            }
        }
    }

    result = holdout_stage.evaluate_directional_meter_from_campaign(
        meter_id=MeterId.M2_APERIODICITY.value,
        family="APERIODICITY_GT",
        candidate=candidate,
        manifest=manifest,
        row_by_id=row_by_id,
        matrix_rows=matrix_rows,
        assignment=assignment,
        records=records,
        usable_primary_instances=usable_primary_instances,
        expected_sweep_member_row_ids=expected_sweep_member_row_ids,
        units_commensurate=False,
    )
    assert result.gate_detail["resolvable_count"] == 18
    pairs_summary = result.gate_detail["pairs_summary"]
    assert pairs_summary["resolvable_count"] == 18
    assert pairs_summary["polarity"] == -1
    # correct_count + reversal_count spans *all* observed pairs (C(6,2)=15
    # per sweep x 2 = 30 total), not just the 18 resolvable ones -- see
    # pairs_summary()'s docstring (descriptive stat over `bundle.pairs`).
    assert pairs_summary["correct_count"] + pairs_summary["reversal_count"] == 30
    assert pairs_summary["kendall_tau"] is not None
    control_detection = result.gate_detail["control_detection"]
    assert control_detection["n_neg"] == 0
    assert control_detection["n_pos"] == len(matrix_rows) * 5  # fixture_controls.PROBE_REPEATS


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
    # Codex #350 round 4 P2 採用: serialized gate_detail always carries the
    # sanctioned-abstention count (0 here -- no NEGATIVE_CONTROL rows).
    assert passing.gate_detail["negative_control_sanctioned_abstentions"] == 0
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
    assert failing.gate_detail["negative_control_sanctioned_abstentions"] == 0


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
