"""`campaign/measure_stage.py` のテスト: within/fresh-process meter call +
cap 超過 → stop event（IMPLEMENTATION_MAP_v1.md §6.4）。fresh-process
subprocess を伴うため `@pytest.mark.slow`。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_genesis.calibration.campaign import measure_stage, render_stage
from voice_genesis.calibration.campaign.state import load_frozen_campaign
from voice_genesis.calibration.candidates.registry import candidate_by_id
from voice_genesis.calibration.cost_caps import CapCounters, CostCaps

from ._campaign_fixture import build_tiny_campaign, small_matrix_subset


@pytest.mark.slow
def test_measure_instance_records_within_and_fresh_repeats(tmp_path: Path) -> None:
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = measure_stage.run_measurement_for_instance(
        campaign, candidate, row_id=row.row_id, probe_index=0, sr_hz=row.row.sr_hz
    )
    assert len(records) == measure_stage.WITHIN_PROCESS_REPEATS + measure_stage.FRESH_PROCESS_REPEATS
    within = [r for r in records if r.repeat_kind == "within"]
    fresh = [r for r in records if r.repeat_kind == "fresh"]
    assert len(within) == measure_stage.WITHIN_PROCESS_REPEATS
    assert len(fresh) == measure_stage.FRESH_PROCESS_REPEATS
    assert all(r.output.values.get("f0_hz") for r in records)
    assert all(120.0 < r.output.values["f0_hz"] < 140.0 for r in records)  # truth ~= 130.813 Hz

    meter_call_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"
    ]
    assert len(meter_call_events) == len(records)
    assert {e["repeat_kind"] for e in meter_call_events} == {"within", "fresh"}


@pytest.mark.slow
def test_measure_stage_over_two_candidates(tmp_path: Path) -> None:
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidates = [candidate_by_id("F0-B0-CURRENT"), candidate_by_id("F0-PYIN-FRAME2048-HOP256")]
    records = measure_stage.run_measure_stage(
        campaign,
        [(row.row_id, 0)],
        candidates,
        sr_by_row={row.row_id: row.row.sr_hz},
    )
    assert len(records) == len(candidates) * (
        measure_stage.WITHIN_PROCESS_REPEATS + measure_stage.FRESH_PROCESS_REPEATS
    )
    seen_candidate_ids = {r.candidate_id for r in records}
    assert seen_candidate_ids == {c.candidate_id for c in candidates}


@pytest.mark.slow
def test_measure_instance_missing_render_raises(tmp_path: Path) -> None:
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    # no render_stage call: pcm file does not exist
    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")
    with pytest.raises(FileNotFoundError):
        measure_stage.run_measurement_for_instance(
            campaign, candidate, row_id=row.row_id, probe_index=0, sr_hz=row.row.sr_hz
        )


@pytest.mark.slow
def test_cost_cap_breach_raises_and_records_stop_event(tmp_path: Path) -> None:
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")
    caps = CostCaps(compute=1e-6, storage=1_000_000, budget=1000.0)
    counters = CapCounters()
    with pytest.raises(measure_stage.CostCapExceededError):
        measure_stage.run_measurement_for_instance(
            campaign,
            candidate,
            row_id=row.row_id,
            probe_index=0,
            sr_hz=row.row.sr_hz,
            cap_counters=counters,
            cost_caps=caps,
        )
    stop_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "stop_event"
    ]
    assert len(stop_events) == 1
    assert stop_events[0]["reason"] == "COST_CAP_EXCEEDED"
    assert "compute" in stop_events[0]["exceeded_dims"]
