"""`campaign/measure_stage.py` のテスト: within/fresh-process meter call +
cap 超過 → stop event（IMPLEMENTATION_MAP_v1.md §6.4）。fresh-process
subprocess を伴うため `@pytest.mark.slow`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_genesis.calibration.campaign import measure_stage, render_stage
from voice_genesis.calibration.campaign.caps import load_cap_counters
from voice_genesis.calibration.campaign.state import load_frozen_campaign
from voice_genesis.calibration.candidates.adapter import MeterOutput
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
    caps = CostCaps(
        compute=1e-6, storage=1_000_000, budget=1000.0, budget_accounting_mode="local_zero_cost"
    )
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


# ---------------------------------------------------------------------------
# round 14 finding #2: compute must be charged as the SUM of each
# fresh-process worker's own reported cpu_seconds, never wall-clock elapsed
# (which undercounts once `--workers > 1` runs them concurrently).
# ---------------------------------------------------------------------------


class _FakeCompletedProcess:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def _fake_subprocess_run_with_cpu_seconds(cpu_seconds: object):
    """Stub for `measure_stage.subprocess.run` whose worker JSON always
    reports `cpu_seconds`. Exercises the real `_run_one_fresh_call` parsing
    + `caps.validate_worker_cpu_seconds()` validation path (only the actual
    subprocess boundary is mocked), so both the happy and fail-closed tests
    below are real regression coverage of that wiring, not of the mock."""

    def _run(cmd, **kwargs):
        return _FakeCompletedProcess(
            stdout=json.dumps(
                {
                    "values": {"f0_hz": 130.0},
                    "missing_reason": None,
                    "ineligible": False,
                    "ineligible_reason": None,
                    "cpu_seconds": cpu_seconds,
                }
            )
        )

    return _run


@pytest.mark.slow
def test_measure_workers_3_compute_charged_equals_sum_of_worker_cpu_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 14 finding #2 regression: with `max_workers=3` (fresh-process
    calls run concurrently under `ThreadPoolExecutor`), the compute counter
    must equal the SUM of every worker's own reported `cpu_seconds` — never
    wall-clock elapsed, which would undercount concurrent CPU usage. Both
    the fresh-process subprocess boundary and the within-process CPU delta
    are pinned to exact known values so the expected total is fully
    deterministic (not dependent on real OS scheduling noise)."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")

    fresh_cpu_seconds_per_call = 2.0
    monkeypatch.setattr(
        measure_stage.subprocess, "run", _fake_subprocess_run_with_cpu_seconds(fresh_cpu_seconds_per_call)
    )
    within_cpu_ticks = iter([100.0, 100.25])  # before, after -> exact delta 0.25
    monkeypatch.setattr(measure_stage, "_process_cpu_seconds", lambda: next(within_cpu_ticks))

    counters = CapCounters()
    caps = CostCaps(
        compute=1e9, storage=1_000_000_000, budget=1e9, budget_accounting_mode="local_zero_cost"
    )
    records = measure_stage.run_measurement_for_instance(
        campaign,
        candidate,
        row_id=row.row_id,
        probe_index=0,
        sr_hz=row.row.sr_hz,
        cap_counters=counters,
        cost_caps=caps,
        max_workers=3,
    )
    assert len(records) == measure_stage.WITHIN_PROCESS_REPEATS + measure_stage.FRESH_PROCESS_REPEATS

    expected_compute = 0.25 + measure_stage.FRESH_PROCESS_REPEATS * fresh_cpu_seconds_per_call
    assert counters.compute_used == pytest.approx(expected_compute)

    meter_calls = [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"]
    assert len(meter_calls) == len(records)
    # informational field: recorded on every one of the 6 records for this
    # work unit (see run_measurement_for_instance docstring).
    assert all(m["cpu_seconds"] == pytest.approx(expected_compute) for m in meter_calls)
    assert all(isinstance(m["wall_seconds"], float) and m["wall_seconds"] >= 0.0 for m in meter_calls)


@pytest.mark.slow
def test_measure_invalid_worker_cpu_seconds_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 14 finding #2 fail-closed path: a fresh-process worker
    reporting a missing/non-finite/negative `cpu_seconds` must refuse the
    whole work unit as a stale/invalid unit — no meter_call ledger events,
    no compute charged — rather than silently falling back to 0 or to wall
    time (either would reopen the undercounting hole this fix closes)."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")
    monkeypatch.setattr(
        measure_stage.subprocess, "run", _fake_subprocess_run_with_cpu_seconds(None)
    )

    counters = CapCounters()
    with pytest.raises(measure_stage.WorkerCpuSecondsInvalidError):
        measure_stage.run_measurement_for_instance(
            campaign,
            candidate,
            row_id=row.row_id,
            probe_index=0,
            sr_hz=row.row.sr_hz,
            cap_counters=counters,
        )

    assert counters.compute_used == pytest.approx(0.0)
    meter_calls = [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"]
    assert meter_calls == []
    stop_events = [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "stop_event"]
    assert len(stop_events) == 1
    assert stop_events[0]["reason"] == "INVALID_MEASURE_WORKER_CPU_SECONDS"


# ---------------------------------------------------------------------------
# finding #1: frozen cost caps — counters persisted to counters.json and
# reloaded across separate invocations (not just held in-process).
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_cost_cap_breach_stops_after_first_unit_and_persists_counters(tmp_path: Path) -> None:
    """Regression test for finding #1: a tiny compute cap breaches after the
    very first unit; the stage stops with a ledger stop event; counters
    consumed by that single unit are persisted to `counters.json` and can be
    read back by a fresh `load_cap_counters()` call — simulating "the next
    invocation" of the CLI resuming from where the previous one stopped."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")
    tiny_caps = CostCaps(
        compute=1e-6,
        storage=1_000_000_000,
        budget=1000.0,
        budget_accounting_mode="local_zero_cost",
    )

    counters = load_cap_counters(campaign.campaign_dir)
    assert counters.compute_used == pytest.approx(0.0)

    with pytest.raises(measure_stage.CostCapExceededError):
        measure_stage.run_measure_stage(
            campaign,
            [(row.row_id, 0), (row.row_id, 1)],
            [candidate],
            sr_by_row={row.row_id: row.row.sr_hz},
            cap_counters=counters,
            cost_caps=tiny_caps,
        )

    # only the first unit's worth of meter_call events was appended — the
    # second instance was never attempted (fail-closed before the next unit).
    meter_calls = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"
    ]
    assert len(meter_calls) == measure_stage.WITHIN_PROCESS_REPEATS + measure_stage.FRESH_PROCESS_REPEATS
    assert {(m["row_id"], m["probe_index"]) for m in meter_calls} == {(row.row_id, 0)}

    stop_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "stop_event"
    ]
    assert len(stop_events) == 1
    assert stop_events[0]["reason"] == "COST_CAP_EXCEEDED"

    # "reloaded by the next invocation": a fresh load_cap_counters() call
    # (as cli.main() would issue on its next invocation) sees the same
    # non-zero usage that was just persisted, not a reset to zero.
    reloaded = load_cap_counters(campaign.campaign_dir)
    assert reloaded.compute_used > 0.0
    assert reloaded.compute_used == pytest.approx(counters.compute_used)
    assert reloaded.storage_used == counters.storage_used


# ---------------------------------------------------------------------------
# finding #9 (第 10 巡採用): 測定段の resume
# ---------------------------------------------------------------------------


def _fake_meter_call(candidate_id, row_id, probe_index, repeat_kind, repeat_index, value):
    return {
        "kind": "meter_call",
        "row_id": row_id,
        "probe_index": probe_index,
        "candidate_id": candidate_id,
        "repeat_kind": repeat_kind,
        "repeat_index": repeat_index,
        **measure_stage.meter_output_to_dict(MeterOutput(values={"f0_hz": value})),
    }


def test_completed_meter_call_records_returns_none_when_unstarted(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    assert (
        measure_stage._completed_meter_call_records(
            campaign.ledger.entries, "r1", 0, "F0-B0-CURRENT"
        )
        is None
    )


def test_completed_meter_call_records_resumes_when_fully_complete(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    for i in range(measure_stage.WITHIN_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "within", i, 100.0 + i))
    for i in range(measure_stage.FRESH_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "fresh", i, 200.0 + i))

    records = measure_stage._completed_meter_call_records(
        campaign.ledger.entries, "r1", 0, "F0-B0-CURRENT"
    )
    assert records is not None
    assert len(records) == 6
    assert {r.repeat_kind for r in records} == {"within", "fresh"}
    assert all(r.output.values["f0_hz"] is not None for r in records)


def test_completed_meter_call_records_partial_is_stale(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    # only 2 of the required 3 within-process repeats -> partial.
    campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "within", 0, 100.0))
    campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "within", 1, 100.0))

    with pytest.raises(measure_stage.StaleMeasurementError):
        measure_stage._completed_meter_call_records(campaign.ledger.entries, "r1", 0, "F0-B0-CURRENT")


def test_completed_meter_call_records_duplicate_key_is_stale(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    for i in range(measure_stage.WITHIN_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "within", i, 100.0))
    for i in range(measure_stage.FRESH_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "fresh", i, 200.0))
    # duplicate write to the same (repeat_kind, repeat_index) key.
    campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "within", 0, 999.0))

    with pytest.raises(measure_stage.StaleMeasurementError):
        measure_stage._completed_meter_call_records(campaign.ledger.entries, "r1", 0, "F0-B0-CURRENT")


@pytest.mark.slow
def test_resume_skips_already_completed_instance_and_only_appends_missing(tmp_path: Path) -> None:
    """finding #9 regression: resuming an interrupted campaign at the
    `run_measure_stage` level re-appends only the not-yet-measured
    (instance, candidate) pair — the already-fully-measured one is skipped
    (no re-measurement, no duplicate ledger entries), and both instances
    end up with exactly within3+fresh3 in the final ledger."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")
    instances = [(row.row_id, 0), (row.row_id, 1)]

    # "first run" (interrupted after probe_index=0 completed, in a real
    # campaign this would be a crash/cap breach before probe_index=1 ran).
    measure_stage.run_measurement_for_instance(
        campaign, candidate, row_id=row.row_id, probe_index=0, sr_hz=row.row.sr_hz
    )
    per_instance_calls = measure_stage.WITHIN_PROCESS_REPEATS + measure_stage.FRESH_PROCESS_REPEATS
    meter_calls_after_first_run = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"
    ]
    assert len(meter_calls_after_first_run) == per_instance_calls

    # "resume": re-invoke run_measure_stage over BOTH instances again.
    records = measure_stage.run_measure_stage(
        campaign, instances, [candidate], sr_by_row={row.row_id: row.row.sr_hz}
    )
    assert len(records) == 2 * per_instance_calls  # returned records cover both instances

    meter_calls_after_resume = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"
    ]
    # only probe_index=1's 6 calls were newly appended — probe_index=0's
    # were NOT re-measured/re-appended (still exactly 6, not 12).
    assert len(meter_calls_after_resume) == 2 * per_instance_calls
    for probe_index in (0, 1):
        calls = [
            m
            for m in meter_calls_after_resume
            if m["row_id"] == row.row_id and m["probe_index"] == probe_index
        ]
        assert len(calls) == per_instance_calls
        assert len({(c["repeat_kind"], c["repeat_index"]) for c in calls}) == per_instance_calls
