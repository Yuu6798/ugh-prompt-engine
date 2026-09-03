"""`campaign/measure_stage.py` のテスト: within/fresh-process meter call +
cap 超過 → stop event（IMPLEMENTATION_MAP_v1.md §6.4）。fresh-process
subprocess を伴うため `@pytest.mark.slow`。
"""

from __future__ import annotations

import itertools
import json
import math
import subprocess
import threading
import time
from pathlib import Path

import pytest

from voice_genesis.calibration.campaign import measure_stage, render_stage
from voice_genesis.calibration.campaign.caps import cap_counters_from_ledger, load_cap_counters
from voice_genesis.calibration.campaign.state import load_frozen_campaign
from voice_genesis.calibration.campaign.time_budget import TimeBudget
from voice_genesis.calibration.candidates import adapter
from voice_genesis.calibration.candidates.adapter import MeterOutput
from voice_genesis.calibration.candidates.registry import candidate_by_id, candidates_for_meter
from voice_genesis.calibration.cost_caps import CapCounters, CostCaps
from voice_genesis.calibration.vocab import MeterId

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
    deterministic (not dependent on real OS scheduling noise).

    round 16 finding #3 (`[UNDERSPEC-CAL-D35]`) revision: the charged
    compute counter must equal the fresh-worker sum ALONE — the
    within-process delta (0.25s here) is no longer part of the *charge*
    (it is already charged once via `cli.py` `main()`'s parent RUSAGE_SELF
    `stage_summary` charge), but stays on the ledger's `meter_call.cpu_seconds`
    combined total and is broken out separately on the new informational
    `within_cpu_seconds` field."""
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

    within_cpu_seconds = 0.25
    expected_fresh = measure_stage.FRESH_PROCESS_REPEATS * fresh_cpu_seconds_per_call
    expected_combined = within_cpu_seconds + expected_fresh
    # round 16 finding #3: only the fresh-worker sum is charged.
    assert counters.compute_used == pytest.approx(expected_fresh)

    meter_calls = [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"]
    assert len(meter_calls) == len(records)
    # informational fields: recorded on every one of the 6 records for this
    # work unit (see run_measurement_for_instance docstring). `cpu_seconds`
    # stays the combined within+fresh total; `within_cpu_seconds` (round 16
    # finding #3) is that total's within-process portion, broken out
    # separately — neither is what gets charged (see the assertion above).
    assert all(m["cpu_seconds"] == pytest.approx(expected_combined) for m in meter_calls)
    assert all(m["within_cpu_seconds"] == pytest.approx(within_cpu_seconds) for m in meter_calls)
    assert all(isinstance(m["wall_seconds"], float) and m["wall_seconds"] >= 0.0 for m in meter_calls)


@pytest.mark.slow
def test_measure_ledger_derived_reconstruction_equals_persisted_compute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 16 finding #3 (`[UNDERSPEC-CAL-D35]`): after a real
    `run_measurement_for_instance` charge (fresh-worker CPU only, within
    excluded), `caps.cap_counters_from_ledger()`'s reconstruction from the
    `meter_call` events it appended must equal what was actually persisted
    to `cap_counters` — the compute-charge composition and its ledger-
    derived reconstruction must stay consistent (the round 15 finding #3
    invariant, re-verified for the round 16 finding #3 revision)."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")
    fresh_cpu_seconds_per_call = 3.0
    monkeypatch.setattr(
        measure_stage.subprocess,
        "run",
        _fake_subprocess_run_with_cpu_seconds(fresh_cpu_seconds_per_call),
    )
    within_cpu_ticks = iter([50.0, 50.1])  # exact within delta 0.1
    monkeypatch.setattr(measure_stage, "_process_cpu_seconds", lambda: next(within_cpu_ticks))

    counters = CapCounters()
    caps = CostCaps(
        compute=1e9, storage=1_000_000_000, budget=1e9, budget_accounting_mode="local_zero_cost"
    )
    measure_stage.run_measurement_for_instance(
        campaign,
        candidate,
        row_id=row.row_id,
        probe_index=0,
        sr_hz=row.row.sr_hz,
        cap_counters=counters,
        cost_caps=caps,
    )
    expected_fresh = measure_stage.FRESH_PROCESS_REPEATS * fresh_cpu_seconds_per_call
    assert counters.compute_used == pytest.approx(expected_fresh)

    derived = cap_counters_from_ledger(campaign.ledger.entries, caps)
    # render_stage.run_render_stage charged its own cpu_seconds too (real,
    # one event per probe repeat) -- so compare the derived total against
    # the persisted total, which by construction is the sum of every
    # render event's cpu_seconds plus this test's own measure charge.
    render_events = [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "render"]
    assert len(render_events) >= 1
    persisted_total = sum(e["cpu_seconds"] for e in render_events) + counters.compute_used
    assert derived.compute_used == pytest.approx(persisted_total)


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
# round 24 ADOPT (1) (`[UNDERSPEC-CAL-D55]`): a fresh-process worker that
# fails post-spawn (timeout / nonzero exit / malformed JSON) must charge the
# attempted work BEFORE the original error propagates — not discard it.
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_measure_fresh_worker_timeout_charges_before_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 25 (`[UNDERSPEC-CAL-D57]`) revision: every worker times out, so
    all `FRESH_PROCESS_REPEATS` attempts run to completion (none is skipped
    just because an earlier one already failed) and each is independently
    charged its own `worker_failed` event."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 0.0))

    monkeypatch.setattr(measure_stage.subprocess, "run", fake_run)
    # 2 ticks (before, after) per attempt, delta 2.5 each, one pair per
    # FRESH_PROCESS_REPEATS attempt (all 3 time out).
    children_cpu_ticks = itertools.count(40.0, 2.5)
    monkeypatch.setattr(measure_stage, "_children_cpu_seconds", lambda: next(children_cpu_ticks))

    counters = CapCounters()
    caps = CostCaps(
        compute=1000.0, storage=1_000_000, budget=1000.0, budget_accounting_mode="local_zero_cost"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        measure_stage.run_measurement_for_instance(
            campaign,
            candidate,
            row_id=row.row_id,
            probe_index=0,
            sr_hz=row.row.sr_hz,
            cap_counters=counters,
            cost_caps=caps,
        )

    expected_total = measure_stage.FRESH_PROCESS_REPEATS * 2.5
    assert counters.compute_used == pytest.approx(expected_total)
    worker_failed = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "worker_failed"
    ]
    assert len(worker_failed) == measure_stage.FRESH_PROCESS_REPEATS
    assert worker_failed[0]["stage"] == "measure"
    assert worker_failed[0]["failure_kind"] == "timeout"
    assert worker_failed[0]["row_id"] == row.row_id
    assert worker_failed[0]["probe_index"] == 0
    assert worker_failed[0]["candidate_id"] == candidate.candidate_id
    assert all(w["cpu_seconds"] == pytest.approx(2.5) for w in worker_failed)
    assert all(w["storage_bytes"] == 0 for w in worker_failed)
    # no meter_call was ever appended for the failed unit.
    assert [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"] == []
    # every attempt failed -- no discarded-success event.
    assert [
        e.payload
        for e in campaign.ledger.entries
        if e.payload.get("kind") == "worker_attempts_discarded"
    ] == []

    derived = cap_counters_from_ledger(campaign.ledger.entries, caps)
    render_events = [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "render"]
    persisted_total = sum(e["cpu_seconds"] for e in render_events) + counters.compute_used
    assert derived.compute_used == pytest.approx(persisted_total)


@pytest.mark.slow
def test_measure_fresh_worker_nonzero_exit_charges_reported_cpu_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nonzero-exit worker's captured stdout still carries a well-formed
    report — the charge must use the worker's own reported `cpu_seconds`
    (not the coarser parent-observed delta) when one is recoverable.

    round 25 (`[UNDERSPEC-CAL-D57]`) revision: every one of the
    `FRESH_PROCESS_REPEATS` attempts fails the same way, so all of them run
    (none skipped) and each is independently charged."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")
    worker_stdout = json.dumps({"values": {}, "cpu_seconds": 4.0})

    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd=cmd, output=worker_stdout, stderr="boom")

    monkeypatch.setattr(measure_stage.subprocess, "run", fake_run)

    counters = CapCounters()
    caps = CostCaps(
        compute=1000.0, storage=1_000_000, budget=1000.0, budget_accounting_mode="local_zero_cost"
    )
    with pytest.raises(subprocess.CalledProcessError):
        measure_stage.run_measurement_for_instance(
            campaign,
            candidate,
            row_id=row.row_id,
            probe_index=0,
            sr_hz=row.row.sr_hz,
            cap_counters=counters,
            cost_caps=caps,
        )

    expected_total = measure_stage.FRESH_PROCESS_REPEATS * 4.0
    assert counters.compute_used == pytest.approx(expected_total)
    worker_failed = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "worker_failed"
    ]
    assert len(worker_failed) == measure_stage.FRESH_PROCESS_REPEATS
    assert all(w["failure_kind"] == "nonzero_exit" for w in worker_failed)
    assert all(w["cpu_seconds"] == pytest.approx(4.0) for w in worker_failed)

    derived = cap_counters_from_ledger(campaign.ledger.entries, caps)
    render_events = [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "render"]
    persisted_total = sum(e["cpu_seconds"] for e in render_events) + counters.compute_used
    assert derived.compute_used == pytest.approx(persisted_total)


@pytest.mark.slow
def test_measure_fresh_worker_malformed_json_charges_before_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 25 (`[UNDERSPEC-CAL-D57]`) revision: every one of the
    `FRESH_PROCESS_REPEATS` attempts returns malformed JSON, so all of them
    run (none skipped) and each is independently charged."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")

    def fake_run(cmd, **kwargs):
        return _FakeCompletedProcess(stdout="{not valid json")

    monkeypatch.setattr(measure_stage.subprocess, "run", fake_run)
    # 2 ticks (before, after) per attempt, delta 0.75 each.
    children_cpu_ticks = itertools.count(5.0, 0.75)
    monkeypatch.setattr(measure_stage, "_children_cpu_seconds", lambda: next(children_cpu_ticks))

    counters = CapCounters()
    caps = CostCaps(
        compute=1000.0, storage=1_000_000, budget=1000.0, budget_accounting_mode="local_zero_cost"
    )
    with pytest.raises(json.JSONDecodeError):
        measure_stage.run_measurement_for_instance(
            campaign,
            candidate,
            row_id=row.row_id,
            probe_index=0,
            sr_hz=row.row.sr_hz,
            cap_counters=counters,
            cost_caps=caps,
        )

    expected_total = measure_stage.FRESH_PROCESS_REPEATS * 0.75
    assert counters.compute_used == pytest.approx(expected_total)
    worker_failed = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "worker_failed"
    ]
    assert len(worker_failed) == measure_stage.FRESH_PROCESS_REPEATS
    assert all(w["failure_kind"] == "malformed_output" for w in worker_failed)
    assert all(w["cpu_seconds"] == pytest.approx(0.75) for w in worker_failed)

    derived = cap_counters_from_ledger(campaign.ledger.entries, caps)
    render_events = [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "render"]
    persisted_total = sum(e["cpu_seconds"] for e in render_events) + counters.compute_used
    assert derived.compute_used == pytest.approx(persisted_total)


@pytest.mark.slow
def test_measure_fresh_worker_failure_cost_cap_breach_raises_cost_cap_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When charging the whole failed batch's attempted compute itself
    breaches the frozen cap, `CostCapExceededError` takes priority over the
    original `TimeoutExpired`/etc. — same priority as every other
    charge-then-check call site in this package (round 23 ADOPT (2)
    `render_nondeterministic`).

    round 25 (`[UNDERSPEC-CAL-D57]`) revision: the cap check now runs ONCE,
    after every one of the `FRESH_PROCESS_REPEATS` attempts (all of which
    fail here) has been charged — not per attempt."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 0.0))

    monkeypatch.setattr(measure_stage.subprocess, "run", fake_run)
    # 2 ticks (before, after) per attempt, delta 1.0 each -> batch total 3.0,
    # well over the tiny compute cap.
    children_cpu_ticks = itertools.count(0.0, 1.0)
    monkeypatch.setattr(measure_stage, "_children_cpu_seconds", lambda: next(children_cpu_ticks))

    counters = CapCounters()
    tiny_caps = CostCaps(
        compute=1e-6, storage=1_000_000, budget=1000.0, budget_accounting_mode="local_zero_cost"
    )
    with pytest.raises(measure_stage.CostCapExceededError):
        measure_stage.run_measurement_for_instance(
            campaign,
            candidate,
            row_id=row.row_id,
            probe_index=0,
            sr_hz=row.row.sr_hz,
            cap_counters=counters,
            cost_caps=tiny_caps,
        )

    expected_total = measure_stage.FRESH_PROCESS_REPEATS * 1.0
    assert counters.compute_used == pytest.approx(expected_total)
    worker_failed = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "worker_failed"
    ]
    assert len(worker_failed) == measure_stage.FRESH_PROCESS_REPEATS
    stop_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "stop_event"
    ]
    assert len(stop_events) == 1
    assert stop_events[0]["reason"] == "COST_CAP_EXCEEDED"


# ---------------------------------------------------------------------------
# round 25 (`[UNDERSPEC-CAL-D57]`): unified worker-attempt accounting — every
# spawned attempt in a repeat batch is charged exactly once, whatever its
# outcome, before the batch's first failure propagates. Supersedes the
# round 24 ADOPT (1) posture of charging only the ONE failing attempt and
# discarding already-completed successful siblings uncharged.
# ---------------------------------------------------------------------------


def _ok_worker_stdout(cpu_seconds: float) -> str:
    return json.dumps(
        {
            "values": {"f0_hz": 130.0},
            "missing_reason": None,
            "ineligible": False,
            "ineligible_reason": None,
            "cpu_seconds": cpu_seconds,
        }
    )


@pytest.mark.slow
def test_measure_repeat_2_of_3_fails_charges_surviving_successes_workers_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """repeat 2 of 3 fails (sequential, `max_workers=1`): repeats 1 and 3
    must still run (not skipped just because repeat 2 already failed) and be
    charged as discarded successes, alongside repeat 2's own `worker_failed`
    charge -- not silently dropped the way the round 24 posture dropped
    already-completed successful siblings."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")
    call_count = {"n": 0}

    def fake_run(cmd, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 0.0))
        return _FakeCompletedProcess(stdout=_ok_worker_stdout(2.0))

    monkeypatch.setattr(measure_stage.subprocess, "run", fake_run)
    children_cpu_ticks = itertools.count(10.0, 1.5)
    monkeypatch.setattr(measure_stage, "_children_cpu_seconds", lambda: next(children_cpu_ticks))

    counters = CapCounters()
    caps = CostCaps(
        compute=1000.0,
        storage=1_000_000,
        budget=1000.0,
        budget_accounting_mode="per_unit_fixed",
        budget_unit_cost=5.0,
    )
    with pytest.raises(subprocess.TimeoutExpired):
        measure_stage.run_measurement_for_instance(
            campaign,
            candidate,
            row_id=row.row_id,
            probe_index=0,
            sr_hz=row.row.sr_hz,
            cap_counters=counters,
            cost_caps=caps,
            max_workers=1,
        )

    worker_failed = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "worker_failed"
    ]
    assert len(worker_failed) == 1
    assert worker_failed[0]["failure_kind"] == "timeout"
    assert worker_failed[0]["cpu_seconds"] == pytest.approx(1.5)

    discarded = [
        e.payload
        for e in campaign.ledger.entries
        if e.payload.get("kind") == "worker_attempts_discarded"
    ]
    assert len(discarded) == 1
    assert discarded[0]["stage"] == "measure"
    assert discarded[0]["row_id"] == row.row_id
    assert discarded[0]["probe_index"] == 0
    assert discarded[0]["candidate_id"] == candidate.candidate_id
    assert discarded[0]["storage_bytes"] == 0
    attempts = discarded[0]["discarded_success_attempts"]
    assert len(attempts) == 2  # repeats 1 and 3
    assert all(a["cpu_seconds"] == pytest.approx(2.0) for a in attempts)

    # 3 attempts total: 2 discarded successes @2.0 + 1 failure @1.5.
    expected_compute = 2 * 2.0 + 1.5
    assert counters.compute_used == pytest.approx(expected_compute)
    # round 26 ADOPT (3) (`[UNDERSPEC-CAL-D59]`): 1 budget unit for the whole
    # failed 3-attempt batch (one attempted measurement invocation), not 1
    # per attempt -- this is the exact "failed 3-attempt batch under
    # per_unit_fixed charges budget exactly once" regression.
    assert counters.budget_used == pytest.approx(1 * 5.0)

    # no meter_call was ever appended for the failed unit.
    assert [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"] == []

    derived = cap_counters_from_ledger(campaign.ledger.entries, caps)
    render_events = [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "render"]
    persisted_total = sum(e["cpu_seconds"] for e in render_events) + counters.compute_used
    assert derived.compute_used == pytest.approx(persisted_total)
    # earlier render_stage.run_render_stage() setup call above was NOT given
    # cap_counters, so its own render units never landed in `counters` --
    # ledger reconstruction still sees them (1 budget unit per render event)
    # alongside this test's own 1 charged batch unit, so compare against that
    # full ledger-derived total rather than `counters.budget_used` directly.
    expected_derived_budget = caps.budget_unit_cost * (len(render_events) + 1)
    assert derived.budget_used == pytest.approx(expected_derived_budget)


@pytest.mark.slow
def test_measure_one_of_three_concurrent_attempts_fails_charges_surviving_successes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1 of 3 concurrent attempts (`max_workers=3`) fails: the `Thread
    PoolExecutor` batch must not cancel/discard the other 2 already-started
    futures -- all 3 outcomes are collected and charged together (2
    discarded successes + 1 failure) before the original failure
    propagates. Which physical call fails is nondeterministic under real
    threading, so this only asserts the AGGREGATE charge (not a specific
    repeat index) -- the workers=1 variant above pins the per-repeat
    identity."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")
    lock = threading.Lock()
    call_count = {"n": 0}

    def fake_run(cmd, **kwargs):
        with lock:
            call_count["n"] += 1
            n = call_count["n"]
        if n == 2:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 0.0))
        return _FakeCompletedProcess(stdout=_ok_worker_stdout(2.0))

    monkeypatch.setattr(measure_stage.subprocess, "run", fake_run)
    children_lock = threading.Lock()
    children_cpu_ticks = itertools.count(10.0, 1.5)

    def _next_children_cpu_seconds() -> float:
        with children_lock:
            return next(children_cpu_ticks)

    monkeypatch.setattr(measure_stage, "_children_cpu_seconds", _next_children_cpu_seconds)

    counters = CapCounters()
    caps = CostCaps(
        compute=1000.0,
        storage=1_000_000,
        budget=1000.0,
        budget_accounting_mode="per_unit_fixed",
        budget_unit_cost=5.0,
    )
    with pytest.raises(subprocess.TimeoutExpired):
        measure_stage.run_measurement_for_instance(
            campaign,
            candidate,
            row_id=row.row_id,
            probe_index=0,
            sr_hz=row.row.sr_hz,
            cap_counters=counters,
            cost_caps=caps,
            max_workers=3,
        )

    worker_failed = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "worker_failed"
    ]
    assert len(worker_failed) == 1
    assert worker_failed[0]["failure_kind"] == "timeout"
    assert worker_failed[0]["cpu_seconds"] == pytest.approx(1.5)

    discarded = [
        e.payload
        for e in campaign.ledger.entries
        if e.payload.get("kind") == "worker_attempts_discarded"
    ]
    assert len(discarded) == 1
    attempts = discarded[0]["discarded_success_attempts"]
    assert len(attempts) == 2
    assert all(a["cpu_seconds"] == pytest.approx(2.0) for a in attempts)

    expected_compute = 2 * 2.0 + 1.5
    assert counters.compute_used == pytest.approx(expected_compute)
    # round 26 ADOPT (3) (`[UNDERSPEC-CAL-D59]`): 1 budget unit for the whole
    # failed batch, not 1 per attempt.
    assert counters.budget_used == pytest.approx(1 * 5.0)

    derived = cap_counters_from_ledger(campaign.ledger.entries, caps)
    render_events = [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "render"]
    persisted_total = sum(e["cpu_seconds"] for e in render_events) + counters.compute_used
    assert derived.compute_used == pytest.approx(persisted_total)
    # earlier render_stage.run_render_stage() setup call above was NOT given
    # cap_counters, so its own render units never landed in `counters` --
    # ledger reconstruction still sees them (1 budget unit per render event)
    # alongside this test's own 1 charged batch unit, so compare against that
    # full ledger-derived total rather than `counters.budget_used` directly.
    expected_derived_budget = caps.budget_unit_cost * (len(render_events) + 1)
    assert derived.budget_used == pytest.approx(expected_derived_budget)


@pytest.mark.slow
@pytest.mark.parametrize("bad_cpu_seconds", ["abc", math.nan, -1.0])
def test_measure_fresh_worker_invalid_cpu_seconds_now_charged_malformed_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_cpu_seconds: object
) -> None:
    """round 25 (`[UNDERSPEC-CAL-D57]`) finding "Charge parseable but
    invalid worker results": a worker exiting 0 with parseable JSON but an
    invalid `cpu_seconds` (non-numeric / NaN / negative) is now a charged
    `malformed_output` `worker_failed` attempt (RUSAGE_CHILDREN fallback,
    since `cpu_seconds` itself is the unusable field) -- reversing the round
    14 finding #2 "stays uncharged" posture for this specific path."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")
    monkeypatch.setattr(
        measure_stage.subprocess, "run", _fake_subprocess_run_with_cpu_seconds(bad_cpu_seconds)
    )
    children_cpu_ticks = itertools.count(0.0, 0.5)
    monkeypatch.setattr(measure_stage, "_children_cpu_seconds", lambda: next(children_cpu_ticks))

    counters = CapCounters()
    caps = CostCaps(
        compute=1000.0, storage=1_000_000, budget=1000.0, budget_accounting_mode="local_zero_cost"
    )
    with pytest.raises(measure_stage.WorkerCpuSecondsInvalidError):
        measure_stage.run_measurement_for_instance(
            campaign,
            candidate,
            row_id=row.row_id,
            probe_index=0,
            sr_hz=row.row.sr_hz,
            cap_counters=counters,
            cost_caps=caps,
        )

    worker_failed = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "worker_failed"
    ]
    assert len(worker_failed) == measure_stage.FRESH_PROCESS_REPEATS
    assert all(w["failure_kind"] == "malformed_output" for w in worker_failed)
    assert counters.compute_used > 0.0  # RUSAGE_CHILDREN fallback, not 0

    stop_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "stop_event"
    ]
    assert len(stop_events) == 1
    assert stop_events[0]["reason"] == "INVALID_MEASURE_WORKER_CPU_SECONDS"

    derived = cap_counters_from_ledger(campaign.ledger.entries, caps)
    render_events = [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "render"]
    persisted_total = sum(e["cpu_seconds"] for e in render_events) + counters.compute_used
    assert derived.compute_used == pytest.approx(persisted_total)


@pytest.mark.slow
def test_measure_fresh_worker_unusable_result_shape_charged_malformed_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 25 (`[UNDERSPEC-CAL-D57]`) finding "Charge parseable but
    invalid worker results": a worker exiting 0 with parseable JSON and a
    VALID `cpu_seconds` but an unusable `MeterOutput` shape (`values` not an
    object) is charged `malformed_output` using its own valid `cpu_seconds`
    (not the RUSAGE_CHILDREN fallback, since that field itself validated
    fine) -- this failure previously escaped `_FreshWorkerFailure`/
    `charge_worker_failure()` entirely, uncharged."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")

    def fake_run(cmd, **kwargs):
        return _FakeCompletedProcess(
            stdout=json.dumps({"values": "not-a-mapping", "cpu_seconds": 1.25})
        )

    monkeypatch.setattr(measure_stage.subprocess, "run", fake_run)

    counters = CapCounters()
    caps = CostCaps(
        compute=1000.0, storage=1_000_000, budget=1000.0, budget_accounting_mode="local_zero_cost"
    )
    with pytest.raises(ValueError, match="values"):
        measure_stage.run_measurement_for_instance(
            campaign,
            candidate,
            row_id=row.row_id,
            probe_index=0,
            sr_hz=row.row.sr_hz,
            cap_counters=counters,
            cost_caps=caps,
        )

    worker_failed = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "worker_failed"
    ]
    assert len(worker_failed) == measure_stage.FRESH_PROCESS_REPEATS
    assert all(w["failure_kind"] == "malformed_output" for w in worker_failed)
    # the reported cpu_seconds (1.25) itself validated fine -- charged as-is,
    # not the RUSAGE_CHILDREN fallback.
    assert all(w["cpu_seconds"] == pytest.approx(1.25) for w in worker_failed)
    assert counters.compute_used == pytest.approx(measure_stage.FRESH_PROCESS_REPEATS * 1.25)

    derived = cap_counters_from_ledger(campaign.ledger.entries, caps)
    render_events = [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "render"]
    persisted_total = sum(e["cpu_seconds"] for e in render_events) + counters.compute_used
    assert derived.compute_used == pytest.approx(persisted_total)


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


# ---------------------------------------------------------------------------
# R1/R3 (design memo `design_runner_robustness.md`, `[UNDERSPEC-CAL-D79]`):
# `StaleMeasurementError.kind`/`.present_keys`, the `meter_call_group_
# discarded` reconstruction rule, and `MeterCallIndex` equivalence with the
# 1-shot rescan. All fast (pure ledger manipulation, no real render/measure).
# ---------------------------------------------------------------------------


def test_stale_measurement_error_kind_distinguishes_partial_from_duplicate(
    tmp_path: Path,
) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "within", 0, 100.0))
    campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "within", 1, 100.0))
    with pytest.raises(measure_stage.StaleMeasurementError) as excinfo:
        measure_stage._completed_meter_call_records(campaign.ledger.entries, "r1", 0, "F0-B0-CURRENT")
    assert excinfo.value.kind == "partial"
    assert excinfo.value.present_keys == frozenset({("within", 0), ("within", 1)})

    campaign2_dir, secret_root2 = build_tiny_campaign(tmp_path / "c2")
    campaign2 = load_frozen_campaign(campaign2_dir, secret_root2)
    for i in range(measure_stage.WITHIN_PROCESS_REPEATS):
        campaign2.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "within", i, 100.0))
    for i in range(measure_stage.FRESH_PROCESS_REPEATS):
        campaign2.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "fresh", i, 200.0))
    campaign2.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "within", 0, 999.0))
    with pytest.raises(measure_stage.StaleMeasurementError) as excinfo2:
        measure_stage._completed_meter_call_records(campaign2.ledger.entries, "r1", 0, "F0-B0-CURRENT")
    assert excinfo2.value.kind == "duplicate"


def test_meter_call_group_discarded_resets_completeness(tmp_path: Path) -> None:
    """R1 reconstruction rule: a `meter_call_group_discarded` event for a
    key resets accumulation — only `meter_call` records appended AFTER it
    count toward completeness/scoring for that key. The pre-discard partial
    records stay in the ledger (append-only) but are invisible to
    `_completed_meter_call_records()` once the discard event is present."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    # a partial group (2 of 6) that would raise StaleMeasurementError alone.
    campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "within", 0, 100.0))
    campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "within", 1, 100.0))
    campaign.ledger.append(
        {
            "kind": measure_stage.METER_CALL_GROUP_DISCARDED_KIND,
            "row_id": "r1",
            "probe_index": 0,
            "candidate_id": "F0-B0-CURRENT",
            "discarded_repeat_keys": [["within", 0], ["within", 1]],
            "discarded_count": 2,
            "reason": "operator_discard_partial_group_after_interrupt",
            "stage": "c2",
        }
    )
    # the full group, re-recorded after the discard.
    for i in range(measure_stage.WITHIN_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "within", i, 300.0 + i))
    for i in range(measure_stage.FRESH_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "fresh", i, 400.0 + i))

    records = measure_stage._completed_meter_call_records(
        campaign.ledger.entries, "r1", 0, "F0-B0-CURRENT"
    )
    assert records is not None
    assert len(records) == measure_stage.WITHIN_PROCESS_REPEATS + measure_stage.FRESH_PROCESS_REPEATS
    within_values = sorted(r.output.values["f0_hz"] for r in records if r.repeat_kind == "within")
    assert within_values == [300.0, 301.0, 302.0]  # only the post-discard values, not 100.0


def test_meter_call_group_discarded_for_unrelated_key_is_ignored(tmp_path: Path) -> None:
    """A discard event for a *different* (row_id, probe_index, candidate_id)
    key must not reset an unrelated key's accumulation."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    for i in range(measure_stage.WITHIN_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "within", i, 100.0 + i))
    for i in range(measure_stage.FRESH_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "fresh", i, 200.0 + i))
    campaign.ledger.append(
        {
            "kind": measure_stage.METER_CALL_GROUP_DISCARDED_KIND,
            "row_id": "OTHER_ROW",
            "probe_index": 0,
            "candidate_id": "F0-B0-CURRENT",
            "discarded_repeat_keys": [],
            "discarded_count": 0,
            "reason": "operator_discard_partial_group_after_interrupt",
            "stage": "c2",
        }
    )

    records = measure_stage._completed_meter_call_records(
        campaign.ledger.entries, "r1", 0, "F0-B0-CURRENT"
    )
    assert records is not None
    assert len(records) == measure_stage.WITHIN_PROCESS_REPEATS + measure_stage.FRESH_PROCESS_REPEATS


def test_meter_call_index_equivalence_with_one_shot_rescan(tmp_path: Path) -> None:
    """R3 equivalence test: an incrementally-updated `MeterCallIndex`
    (`observe_entry()` called once per newly-appended ledger entry, the same
    increment `run_measurement_for_instance()` performs) must answer
    `completed_records()` identically to a 1-shot rescan
    (`_completed_meter_call_records()`) at every point along the way, across
    complete, partial, duplicate, and discarded-then-remeasured groups."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    # key A: complete (within3+fresh3).
    for i in range(measure_stage.WITHIN_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "rA", 0, "within", i, 10.0 + i))
    for i in range(measure_stage.FRESH_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "rA", 0, "fresh", i, 20.0 + i))
    # key B: partial (2 of 6).
    campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "rB", 0, "within", 0, 30.0))
    campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "rB", 0, "within", 1, 30.0))
    # key C: duplicate.
    for i in range(measure_stage.WITHIN_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "rC", 0, "within", i, 40.0 + i))
    for i in range(measure_stage.FRESH_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "rC", 0, "fresh", i, 50.0 + i))
    campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "rC", 0, "within", 0, 999.0))
    # key D: discarded partial, then remeasured to completeness.
    campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "rD", 0, "within", 0, 60.0))
    campaign.ledger.append(
        {
            "kind": measure_stage.METER_CALL_GROUP_DISCARDED_KIND,
            "row_id": "rD",
            "probe_index": 0,
            "candidate_id": "F0-B0-CURRENT",
            "discarded_repeat_keys": [["within", 0]],
            "discarded_count": 1,
            "reason": "operator_discard_partial_group_after_interrupt",
            "stage": "c2",
        }
    )
    for i in range(measure_stage.WITHIN_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "rD", 0, "within", i, 70.0 + i))
    for i in range(measure_stage.FRESH_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "rD", 0, "fresh", i, 80.0 + i))

    index = measure_stage.MeterCallIndex()
    keys = [("rA", 0, "F0-B0-CURRENT"), ("rB", 0, "F0-B0-CURRENT"), ("rC", 0, "F0-B0-CURRENT"), ("rD", 0, "F0-B0-CURRENT")]

    def _outcome(fn, row_id, probe_index, candidate_id):
        try:
            return ("ok", fn(row_id, probe_index, candidate_id))
        except measure_stage.StaleMeasurementError as exc:
            return ("error", exc.kind)

    # observe entries one at a time (simulating run_measurement_for_instance's
    # incremental append() -> observe_entry() flow) and compare against a
    # fresh full rescan after each single entry.
    for i, entry in enumerate(campaign.ledger.entries):
        index.observe_entry(entry)
        prefix = campaign.ledger.entries[: i + 1]
        for row_id, probe_index, candidate_id in keys:
            index_outcome = _outcome(index.completed_records, row_id, probe_index, candidate_id)
            rescan_outcome = _outcome(
                lambda r, p, c: measure_stage._completed_meter_call_records(prefix, r, p, c),
                row_id,
                probe_index,
                candidate_id,
            )
            if index_outcome[0] == "ok" and rescan_outcome[0] == "ok":
                index_records, rescan_records = index_outcome[1], rescan_outcome[1]
                if index_records is None or rescan_records is None:
                    assert index_records is None and rescan_records is None
                else:
                    assert [
                        (r.repeat_kind, r.repeat_index, r.output.values) for r in index_records
                    ] == [
                        (r.repeat_kind, r.repeat_index, r.output.values) for r in rescan_records
                    ]
            else:
                assert index_outcome == rescan_outcome

    # final state sanity check: A and D are complete-post-discard, B stays
    # partial (never resolved), C stays duplicate.
    assert index.completed_records("rA", 0, "F0-B0-CURRENT") is not None
    assert index.completed_records("rD", 0, "F0-B0-CURRENT") is not None
    with pytest.raises(measure_stage.StaleMeasurementError) as excinfo_b:
        index.completed_records("rB", 0, "F0-B0-CURRENT")
    assert excinfo_b.value.kind == "partial"
    with pytest.raises(measure_stage.StaleMeasurementError) as excinfo_c:
        index.completed_records("rC", 0, "F0-B0-CURRENT")
    assert excinfo_c.value.kind == "duplicate"


def test_discard_partial_groups_false_still_raises_on_partial(tmp_path: Path) -> None:
    """Default behaviour (flag absent) is unchanged: a partial group still
    fails closed with `StaleMeasurementError`, records a `stop_event` (not a
    discard event), and performs no measurement."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "within", 0, 100.0))
    campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "within", 1, 100.0))
    candidate = candidate_by_id("F0-B0-CURRENT")

    with pytest.raises(measure_stage.StaleMeasurementError):
        measure_stage.run_measurement_for_instance(
            campaign, candidate, row_id="r1", probe_index=0, sr_hz=16000
        )
    stop_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "stop_event"
    ]
    assert len(stop_events) == 1
    assert stop_events[0]["reason"] == "STALE_MEASUREMENT_STATE"
    assert not any(
        e.payload.get("kind") == measure_stage.METER_CALL_GROUP_DISCARDED_KIND
        for e in campaign.ledger.entries
    )


def test_discard_partial_groups_true_duplicate_still_raises(tmp_path: Path) -> None:
    """R1: `--discard-partial-groups` only covers `kind == "partial"` — a
    duplicate group still fails closed regardless of the flag."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    for i in range(measure_stage.WITHIN_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "within", i, 100.0))
    for i in range(measure_stage.FRESH_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "fresh", i, 200.0))
    campaign.ledger.append(_fake_meter_call("F0-B0-CURRENT", "r1", 0, "within", 0, 999.0))
    candidate = candidate_by_id("F0-B0-CURRENT")

    with pytest.raises(measure_stage.StaleMeasurementError) as excinfo:
        measure_stage.run_measurement_for_instance(
            campaign,
            candidate,
            row_id="r1",
            probe_index=0,
            sr_hz=16000,
            discard_partial_groups=True,
            stage="c2",
        )
    assert excinfo.value.kind == "duplicate"
    stop_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "stop_event"
    ]
    assert len(stop_events) == 1
    assert not any(
        e.payload.get("kind") == measure_stage.METER_CALL_GROUP_DISCARDED_KIND
        for e in campaign.ledger.entries
    )


@pytest.mark.slow
def test_discard_partial_groups_true_discards_partial_and_remeasures(tmp_path: Path) -> None:
    """R1 end-to-end (real measurement): a partial group + the flag appends
    exactly one `meter_call_group_discarded` event carrying the exact
    partial repeat keys, then measures and records the FULL group again —
    the stale records stay in the ledger (append-only) but the returned
    records and the post-discard resume view only see the fresh group."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")
    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")

    # simulate a mid-kill: only 2 of 6 within-process repeats got appended.
    campaign.ledger.append(
        _fake_meter_call(candidate.candidate_id, row.row_id, 0, "within", 0, 999.0)
    )
    campaign.ledger.append(
        _fake_meter_call(candidate.candidate_id, row.row_id, 0, "within", 1, 999.0)
    )

    records = measure_stage.run_measurement_for_instance(
        campaign,
        candidate,
        row_id=row.row_id,
        probe_index=0,
        sr_hz=row.row.sr_hz,
        discard_partial_groups=True,
        stage="c2",
    )
    assert len(records) == measure_stage.WITHIN_PROCESS_REPEATS + measure_stage.FRESH_PROCESS_REPEATS

    discard_events = [
        e.payload
        for e in campaign.ledger.entries
        if e.payload.get("kind") == measure_stage.METER_CALL_GROUP_DISCARDED_KIND
    ]
    assert len(discard_events) == 1
    discarded = discard_events[0]
    assert discarded["row_id"] == row.row_id
    assert discarded["probe_index"] == 0
    assert discarded["candidate_id"] == candidate.candidate_id
    assert discarded["discarded_repeat_keys"] == [["within", 0], ["within", 1]]
    assert discarded["discarded_count"] == 2
    assert discarded["reason"] == "operator_discard_partial_group_after_interrupt"
    assert discarded["stage"] == "c2"

    # the pre-discard stale records stay in the ledger (append-only)...
    all_meter_call_events = [
        e.payload
        for e in campaign.ledger.entries
        if e.payload.get("kind") == "meter_call"
        and e.payload.get("row_id") == row.row_id
        and e.payload.get("candidate_id") == candidate.candidate_id
    ]
    assert len(all_meter_call_events) == 2 + 6  # 2 stale + 6 fresh
    # ...but the resume/scoring view only sees the post-discard group.
    resumed = measure_stage._completed_meter_call_records(
        campaign.ledger.entries, row.row_id, 0, candidate.candidate_id
    )
    assert resumed is not None
    assert len(resumed) == 6
    assert all(v != 999.0 for r in resumed for v in r.output.values.values())


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


@pytest.mark.slow
def test_run_measure_stage_time_budget_partial_slice_then_resume(tmp_path: Path) -> None:
    """R2（design memo `design_runner_robustness.md`, `[UNDERSPEC-CAL-D79]`）:
    instance boundary = 1 `(row_id, probe_index)` (every candidate measured
    for it). An essentially-zero budget still lets the first in-flight
    instance finish, then stops before the second — `completed_all=False`,
    `instances_remaining>0` — with no `measurement_missing`-style silent
    gap (nothing was skipped, just not yet dispatched). Re-running without a
    budget (the existing resume path) finishes every remaining instance."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")
    instances = [(row.row_id, p) for p in range(3)]

    records, slice_status = measure_stage.run_measure_stage(
        campaign,
        instances,
        [candidate],
        sr_by_row={row.row_id: row.row.sr_hz},
        time_budget=TimeBudget.start_now(0.01),
    )
    per_instance_calls = measure_stage.WITHIN_PROCESS_REPEATS + measure_stage.FRESH_PROCESS_REPEATS
    assert slice_status.completed_all is False
    assert slice_status.instances_completed_this_run >= 1
    assert slice_status.instances_remaining > 0
    assert len(records) == slice_status.instances_completed_this_run * per_instance_calls

    # re-run without a budget: resumes and finishes every remaining instance.
    all_records, final_slice_status = measure_stage.run_measure_stage(
        campaign,
        instances,
        [candidate],
        sr_by_row={row.row_id: row.row.sr_hz},
        time_budget=TimeBudget.start_now(3600.0),
    )
    assert final_slice_status.completed_all is True
    assert len(all_records) == len(instances) * per_instance_calls
    meter_call_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"
    ]
    assert len(meter_call_events) == len(instances) * per_instance_calls  # no duplicates


# ---------------------------------------------------------------------------
# rehearsal 4 findings D/G (adopted, `[UNDERSPEC-CAL-D79]`): a resumed
# `run_measure_stage()` slice must treat an already-complete instance as
# O(1) work (an index presence check only — no PCM read, no
# `MeasurementRecord` reconstruction) instead of paying the growing
# reconstruction cost rehearsal 4 measured (c3b parent CPU
# 71.7s->78.9s->84.3s->88.3s across 4 slices doing the same constant
# 2-instance new work), and `instances_remaining` must be computed from the
# ledger-built index (the TRUE post-run completion count) rather than
# `total_instances - instances_completed_this_run`, which silently
# regressed to `total_instances` whenever the budget expired before this
# call's own loop walked even its first instance (rehearsal 4 observed
# `instances_remaining` jump backward 77->85 at a 0.001s budget).
# ---------------------------------------------------------------------------


class _CountingBudget:
    """Deterministic `TimeBudget` double: `expired()` returns `True` only
    once it has been called more than `expire_after_calls` times — lets a
    test control exactly how many R2 instance-boundary checks pass before
    a resumed slice stops, without depending on real wall-clock timing
    (`run_measure_stage()` only ever accesses `.expired()`/`.seconds`/
    `.elapsed()` on its `time_budget` argument, so this satisfies that
    duck-typed contract)."""

    def __init__(self, expire_after_calls: int) -> None:
        self.seconds = 999.0
        self._calls = 0
        self._expire_after_calls = expire_after_calls

    def elapsed(self) -> float:
        return 0.0

    def expired(self) -> bool:
        self._calls += 1
        return self._calls > self._expire_after_calls


@pytest.mark.slow
def test_run_measure_stage_partial_slice_skips_completed_prefix_without_reconstruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rehearsal 4 finding D: walking an already-complete instance during a
    (still-partial) resumed slice must never reconstruct that cell's
    `MeasurementRecord`s (`MeterCallIndex.completed_records()` — the exact
    function whose growing per-slice cost rehearsal 4 measured). Asserts
    the reconstruction function is called exactly 0 times while this slice
    walks a 10-instance already-complete prefix and never reaches
    unfinished work.

    Codex PR #345 round 4 finding #3 (adopted, category ③,
    `[UNDERSPEC-CAL-D79]`): the budget boundary check is now gated on
    `_instance_has_pending_candidate()` — an already-complete instance
    never calls `time_budget.expired()` at all (short-circuited), so the
    budget double below is calibrated to expire on the FIRST call it
    actually receives (from the first genuinely pending instance), not
    after walking half the completed prefix — the whole completed prefix
    is walked for free regardless of the budget.

    Codex PR #345 round 5 finding S2 (adopted, category ②,
    `[UNDERSPEC-CAL-D79]`): `instances_completed_this_run` now counts only
    instances with at least one newly dispatched measurement — the whole
    completed prefix this call walks for free contributes 0 (not
    `len(completed_prefix)`, the pre-fix over-count this test used to
    assert), and the one genuinely pending instance it reaches never gets
    dispatched either (the already-expired budget stops the call before
    it)."""
    subset = small_matrix_subset(3, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")

    candidate = candidate_by_id("F0-B0-CURRENT")
    monkeypatch.setattr(
        measure_stage.subprocess, "run", _fake_subprocess_run_with_cpu_seconds(2.0)
    )

    # c1 only renders CALIBRATION/SELECTION-split (+ control) instances, so
    # derive the measurable instance set from what was actually rendered
    # rather than a naive `row x range(PROBE_REPEATS)` reconstruction.
    all_instances = sorted({(o.row_id, o.probe_index) for o in render_outcomes})
    sr_by_row = {mr.row_id: mr.row.sr_hz for mr in subset}
    # c1 only renders CALIBRATION/SELECTION-split (+ control) instances, so
    # the exact count is split-dependent -- leave 2 genuinely unmeasured so
    # this call's `completed_all` stays `False`.
    completed_prefix = all_instances[:-2]
    assert len(completed_prefix) >= 4  # a meaningfully-sized already-complete prefix
    measure_stage.run_measure_stage(campaign, completed_prefix, [candidate], sr_by_row=sr_by_row)

    call_count = {"n": 0}
    orig_completed_records = measure_stage.MeterCallIndex.completed_records

    def _counting_completed_records(self, *args, **kwargs):
        call_count["n"] += 1
        return orig_completed_records(self, *args, **kwargs)

    monkeypatch.setattr(
        measure_stage.MeterCallIndex, "completed_records", _counting_completed_records
    )

    budget = _CountingBudget(expire_after_calls=0)
    records, slice_status = measure_stage.run_measure_stage(
        campaign, all_instances, [candidate], sr_by_row=sr_by_row, time_budget=budget
    )

    assert slice_status.completed_all is False
    # the entire already-complete prefix is walked for free (never gated
    # by the budget) -- only the first genuinely pending instance trips
    # the (already-expired) budget double and stops the slice.
    # round 5 finding S2: the walked prefix is entirely already-complete
    # (no new dispatch), so this call's "new progress" is 0 -- not
    # `len(completed_prefix)`, the pre-fix over-count.
    assert slice_status.instances_completed_this_run == 0
    # zero reconstructions for the walked already-complete prefix (0, not
    # merely "constant" -- this call never reaches unfinished work at all).
    assert call_count["n"] == 0
    assert records == []


@pytest.mark.slow
def test_run_measure_stage_large_completed_prefix_partial_slice_stays_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rehearsal 4 finding D: a resumed slice with a LARGE already-complete
    prefix ahead of it must still finish within roughly budget + one new
    instance's dispatch time -- not grow with the size of the completed
    prefix (rehearsal 4: c3b parent CPU rose 71.7s->78.9s->84.3s->88.3s
    across 4 slices doing the same constant 2-instance new work, purely
    from re-reconstructing a growing already-complete prefix's
    `MeasurementRecord`s on every call)."""
    subset = small_matrix_subset(4, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")

    candidate = candidate_by_id("F0-B0-CURRENT")
    monkeypatch.setattr(
        measure_stage.subprocess, "run", _fake_subprocess_run_with_cpu_seconds(2.0)
    )

    all_instances = sorted({(o.row_id, o.probe_index) for o in render_outcomes})
    sr_by_row = {mr.row_id: mr.row.sr_hz for mr in subset}
    completed_prefix = all_instances[:-1]  # every instance but the last
    assert len(completed_prefix) >= 10
    measure_stage.run_measure_stage(campaign, completed_prefix, [candidate], sr_by_row=sr_by_row)

    budget_seconds = 0.5
    t0 = time.perf_counter()
    records, slice_status = measure_stage.run_measure_stage(
        campaign,
        all_instances,
        [candidate],
        sr_by_row=sr_by_row,
        time_budget=TimeBudget.start_now(budget_seconds),
    )
    elapsed = time.perf_counter() - t0

    # Bounded by budget + a generous single-instance dispatch margin --
    # NOT proportional to `len(completed_prefix)` (25+ instances here; the
    # pre-fix reconstruction cost scaled with that number on every call).
    assert elapsed < budget_seconds + 10.0
    assert slice_status.completed_all is True
    assert slice_status.instances_remaining == 0
    assert len(records) > 0  # the one genuinely-new instance was measured


@pytest.mark.slow
def test_run_measure_stage_time_budget_remaining_matches_true_completed_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rehearsal 4 finding G: with a campaign half-measured and a budget
    guaranteed already-expired before this call's loop starts,
    `instances_remaining` must equal the TRUE remaining count (`total -
    already_complete`, read from the ledger-built index) -- not
    `total_instances - 0 == total_instances`, which silently ignored every
    instance a PRIOR invocation had already finished (rehearsal 4 observed
    `instances_remaining` jump backward 77->85 at a 0.001s budget).

    Codex PR #345 round 4 finding #3 (adopted, category ③,
    `[UNDERSPEC-CAL-D79]`): `instances_remaining` is no longer inflated
    here -- the already-measured `half` is walked for free regardless of
    the expired budget (`_instance_has_pending_candidate()` gates the
    budget check, so an instance with nothing pending never calls
    `time_budget.expired()`), so this call's own loop DOES walk every one
    of those instances before stopping at the first genuinely pending one
    (which is what lets `instances_remaining` below read the TRUE
    remaining count instead of regressing to `len(all_instances)`).

    Codex PR #345 round 5 finding S2 (adopted, category ②,
    `[UNDERSPEC-CAL-D79]`): walking `half` for free is not the same as
    making new progress on it -- `instances_completed_this_run` counts
    only instances with at least one newly dispatched measurement, so it
    is 0 here (not `len(half)`, the pre-fix over-count this test used to
    assert): `half` was already complete before this call, and the one
    genuinely pending instance this call reaches is never dispatched
    either (the already-expired budget stops it first)."""
    subset = small_matrix_subset(2, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")

    candidate = candidate_by_id("F0-B0-CURRENT")
    monkeypatch.setattr(
        measure_stage.subprocess, "run", _fake_subprocess_run_with_cpu_seconds(2.0)
    )

    all_instances = sorted({(o.row_id, o.probe_index) for o in render_outcomes})
    sr_by_row = {mr.row_id: mr.row.sr_hz for mr in subset}
    half = all_instances[: len(all_instances) // 2]
    assert 0 < len(half) < len(all_instances)
    measure_stage.run_measure_stage(campaign, half, [candidate], sr_by_row=sr_by_row)

    budget = TimeBudget.start_now(0.001)
    time.sleep(0.05)  # guarantee expiry regardless of machine speed/scheduling
    records, slice_status = measure_stage.run_measure_stage(
        campaign, all_instances, [candidate], sr_by_row=sr_by_row, time_budget=budget
    )

    assert records == []
    assert slice_status.completed_all is False
    # round 5 finding S2: no new work happened this call (see docstring).
    assert slice_status.instances_completed_this_run == 0
    true_remaining = len(all_instances) - len(half)
    assert slice_status.instances_remaining == true_remaining
    # not the pre-fix bug: remaining must NOT regress to the full instance
    # count just because this call's own loop never walked an instance.
    assert slice_status.instances_remaining < len(all_instances)


@pytest.mark.slow
def test_run_measure_stage_all_cells_complete_transitions_despite_expired_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex PR #345 round 4 finding #3 (adopted, category ③,
    `[UNDERSPEC-CAL-D79]`, mirrors `render_stage.run_render_stage()`'s
    identical fix): a measure stage whose ledger already has every cell
    measured (mirrors a process interrupted after the final cell was
    recorded but before the caller's own stage-completion handling) must
    still report `completed_all=True` on a resumed call, EVEN with a
    budget that is already expired before the call's own loop reaches its
    first instance boundary check — the budget check must never block an
    instance with nothing pending, only a genuinely pending dispatch."""
    subset = small_matrix_subset(2, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")

    candidate = candidate_by_id("F0-B0-CURRENT")
    monkeypatch.setattr(
        measure_stage.subprocess, "run", _fake_subprocess_run_with_cpu_seconds(2.0)
    )

    all_instances = sorted({(o.row_id, o.probe_index) for o in render_outcomes})
    sr_by_row = {mr.row_id: mr.row.sr_hz for mr in subset}
    # measure every cell first — no `time_budget` at all, so this call is
    # the whole invocation and every cell is genuinely complete afterward.
    measure_stage.run_measure_stage(campaign, all_instances, [candidate], sr_by_row=sr_by_row)

    # already-expired-before-the-first-check budget double (same contract
    # `render_stage`'s equivalent test uses): `.expired()` returns `True`
    # from its very first call, simulating a budget consumed entirely by
    # rebuilding `MeterCallIndex` before the loop's own boundary check runs.
    already_expired_budget = _CountingBudget(expire_after_calls=0)
    records, slice_status = measure_stage.run_measure_stage(
        campaign, all_instances, [candidate], sr_by_row=sr_by_row, time_budget=already_expired_budget
    )

    assert slice_status.completed_all is True
    assert slice_status.instances_remaining == 0
    # the completing invocation rebuilds every already-complete cell's
    # `MeasurementRecord`s once — the caller of a sliced call still needs
    # the full record set when the stage actually completes.
    per_instance_calls = measure_stage.WITHIN_PROCESS_REPEATS + measure_stage.FRESH_PROCESS_REPEATS
    assert len(records) == len(all_instances) * per_instance_calls


# ---------------------------------------------------------------------------
# round 26 ADOPT (1) (`[UNDERSPEC-CAL-D58]`): a candidate emitting NaN/±Inf
# must not crash `Ledger.append()` (canonical JSON forbids non-finite floats)
# uncharged and unrecorded -- `meter_output_to_dict()` sanitizes the value to
# `null` + a `nonfinite_kind` companion field before serialization, and
# `meter_output_from_dict()` reconstructs the exact original float on
# read-back so every downstream consumer (the `unexplained_nonfinite` fail
# filter chief among them) still sees it.
# ---------------------------------------------------------------------------

_NONFINITE_CASES = [
    pytest.param(math.nan, "nan", id="nan"),
    pytest.param(math.inf, "inf", id="inf"),
    pytest.param(-math.inf, "-inf", id="-inf"),
]


@pytest.mark.parametrize("bad_value,kind", _NONFINITE_CASES)
def test_meter_output_to_dict_sanitizes_nonfinite_and_from_dict_roundtrips(
    bad_value: float, kind: str
) -> None:
    output = MeterOutput(values={"f0_hz": bad_value, "f1_hz": 100.0})
    payload = measure_stage.meter_output_to_dict(output)
    assert payload["values"]["f0_hz"] is None
    assert payload["values"]["f1_hz"] == 100.0
    assert payload["nonfinite_kind"] == {"f0_hz": kind}

    # must survive the same canonical serialization `Ledger.append()` uses
    # (this is the exact call that used to raise `ValueError` on the raw
    # non-finite float -- see the `meter_output_to_dict` docstring).
    from voice_genesis.calibration.canonical import canonical_json

    canonical_json(payload)  # must not raise

    reconstructed = measure_stage.meter_output_from_dict(payload)
    if kind == "nan":
        assert math.isnan(reconstructed.values["f0_hz"])
    else:
        assert reconstructed.values["f0_hz"] == bad_value
    assert reconstructed.values["f1_hz"] == 100.0
    assert adapter.unexplained_nonfinite(reconstructed) is True


def test_meter_output_to_dict_all_finite_leaves_nonfinite_kind_none() -> None:
    output = MeterOutput(values={"f0_hz": 130.0})
    payload = measure_stage.meter_output_to_dict(output)
    assert payload["nonfinite_kind"] is None
    reconstructed = measure_stage.meter_output_from_dict(payload)
    assert reconstructed.values == {"f0_hz": 130.0}
    assert adapter.unexplained_nonfinite(reconstructed) is False


def test_meter_output_from_dict_rejects_null_value_without_nonfinite_kind() -> None:
    with pytest.raises(ValueError, match="nonfinite_kind"):
        measure_stage.meter_output_from_dict(
            {
                "values": {"f0_hz": None},
                "missing_reason": None,
                "ineligible": False,
                "ineligible_reason": None,
            }
        )


@pytest.mark.slow
@pytest.mark.parametrize("bad_value,kind", _NONFINITE_CASES)
def test_measure_within_process_nonfinite_charges_and_records_nonfinite_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_value: float, kind: str
) -> None:
    """A within-process call returning NaN/±Inf must not crash
    `run_measurement_for_instance` -- the work unit charges normally, all 6
    `meter_call` events are durably appended (none lost to a failed
    `Ledger.append()`), and the non-finite within-process records carry
    `nonfinite_kind` while the (still finite, stubbed) fresh-process records
    do not."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")

    def _nonfinite_fn(signal, sr, params):
        return MeterOutput(values={"f0_hz": bad_value})

    monkeypatch.setattr(measure_stage, "resolve_measure_callable", lambda ref: _nonfinite_fn)
    monkeypatch.setattr(
        measure_stage.subprocess, "run", _fake_subprocess_run_with_cpu_seconds(2.0)
    )

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
    )
    assert len(records) == measure_stage.WITHIN_PROCESS_REPEATS + measure_stage.FRESH_PROCESS_REPEATS

    meter_calls = [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"]
    assert len(meter_calls) == len(records)
    within_calls = [m for m in meter_calls if m["repeat_kind"] == "within"]
    fresh_calls = [m for m in meter_calls if m["repeat_kind"] == "fresh"]
    assert len(within_calls) == measure_stage.WITHIN_PROCESS_REPEATS
    assert len(fresh_calls) == measure_stage.FRESH_PROCESS_REPEATS
    assert all(m["values"]["f0_hz"] is None for m in within_calls)
    assert all(m["nonfinite_kind"] == {"f0_hz": kind} for m in within_calls)
    assert all(m["values"]["f0_hz"] == 130.0 for m in fresh_calls)
    assert all(m["nonfinite_kind"] is None for m in fresh_calls)

    # the work unit's own CPU (within + fresh) was charged -- not skipped
    # because the ledger write nearly crashed.
    assert counters.compute_used > 0.0

    # the ledger append actually happened (canonical_json did not raise) --
    # the chain is intact.
    assert campaign.ledger.verify_chain().ok is True

    reconstructed = measure_stage.meter_output_from_dict(within_calls[0])
    assert adapter.unexplained_nonfinite(reconstructed) is True


@pytest.mark.slow
@pytest.mark.parametrize("bad_value,kind", _NONFINITE_CASES)
def test_measure_fresh_worker_nonfinite_charges_and_records_nonfinite_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_value: float, kind: str
) -> None:
    """The same fix, exercised from the fresh-process worker side: the
    worker itself calls `meter_output_to_dict()` to build its stdout JSON
    (`_measure_worker.py`), so a NaN/±Inf worker result already arrives at
    `_run_one_fresh_call` sanitized -- this pins that the parent correctly
    forwards the sanitized `null` + `nonfinite_kind` payload straight into
    the `meter_call` ledger event (no re-corruption on the way through)."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")

    def fake_run(cmd, **kwargs):
        return _FakeCompletedProcess(
            stdout=json.dumps(
                {
                    "values": {"f0_hz": None},
                    "missing_reason": None,
                    "ineligible": False,
                    "ineligible_reason": None,
                    "nonfinite_kind": {"f0_hz": kind},
                    "cpu_seconds": 2.0,
                }
            )
        )

    monkeypatch.setattr(measure_stage.subprocess, "run", fake_run)

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
    )
    assert len(records) == measure_stage.WITHIN_PROCESS_REPEATS + measure_stage.FRESH_PROCESS_REPEATS

    meter_calls = [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"]
    fresh_calls = [m for m in meter_calls if m["repeat_kind"] == "fresh"]
    within_calls = [m for m in meter_calls if m["repeat_kind"] == "within"]
    assert len(fresh_calls) == measure_stage.FRESH_PROCESS_REPEATS
    assert all(m["values"]["f0_hz"] is None for m in fresh_calls)
    assert all(m["nonfinite_kind"] == {"f0_hz": kind} for m in fresh_calls)
    assert all(m["nonfinite_kind"] is None for m in within_calls)
    assert counters.compute_used > 0.0
    assert campaign.ledger.verify_chain().ok is True

    reconstructed = measure_stage.meter_output_from_dict(fresh_calls[0])
    assert adapter.unexplained_nonfinite(reconstructed) is True


@pytest.mark.slow
def test_measure_nonfinite_work_unit_resume_reuses_without_remeasuring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a NaN-producing work unit has been charged and recorded, a
    second call for the same (row_id, probe_index, candidate) must resume
    from the durable `meter_call` records -- not re-invoke the measure
    callable (which would re-charge compute the caller already paid for) --
    and the resumed record's non-finite value must reconstruct correctly."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    row = subset[0]
    candidate = candidate_by_id("F0-B0-CURRENT")
    call_count = {"n": 0}

    def _nonfinite_fn(signal, sr, params):
        call_count["n"] += 1
        return MeterOutput(values={"f0_hz": math.nan})

    monkeypatch.setattr(measure_stage, "resolve_measure_callable", lambda ref: _nonfinite_fn)
    monkeypatch.setattr(
        measure_stage.subprocess, "run", _fake_subprocess_run_with_cpu_seconds(2.0)
    )

    counters = CapCounters()
    caps = CostCaps(
        compute=1e9, storage=1_000_000_000, budget=1e9, budget_accounting_mode="local_zero_cost"
    )
    first = measure_stage.run_measurement_for_instance(
        campaign,
        candidate,
        row_id=row.row_id,
        probe_index=0,
        sr_hz=row.row.sr_hz,
        cap_counters=counters,
        cost_caps=caps,
    )
    calls_after_first = call_count["n"]
    compute_after_first = counters.compute_used
    meter_calls_after_first = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"
    ]
    assert len(meter_calls_after_first) == len(first)

    second = measure_stage.run_measurement_for_instance(
        campaign,
        candidate,
        row_id=row.row_id,
        probe_index=0,
        sr_hz=row.row.sr_hz,
        cap_counters=counters,
        cost_caps=caps,
    )
    # resume returned the same records without invoking the measure callable
    # again or re-appending/re-charging.
    assert call_count["n"] == calls_after_first
    assert counters.compute_used == pytest.approx(compute_after_first)
    meter_calls_after_second = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"
    ]
    assert len(meter_calls_after_second) == len(meter_calls_after_first)
    assert len(second) == len(first)
    within_second = [r for r in second if r.repeat_kind == "within"]
    assert all(math.isnan(r.output.values["f0_hz"]) for r in within_second)
    assert all(adapter.unexplained_nonfinite(r.output) for r in within_second)


# ---------------------------------------------------------------------------
# round 27 ADOPT (1) (`[UNDERSPEC-CAL-D61]`): `f0_unusable_instances` must
# make `run_measure_stage()` skip F0-dependent candidates entirely on the
# named instances (never call `measure()`, no ledger `meter_call`), while
# leaving non-F0-dependent candidates and F0-usable instances untouched.
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_run_measure_stage_skips_f0_dependent_candidate_on_unusable_instance(
    tmp_path: Path,
) -> None:
    """An instance in `f0_unusable_instances` must never reach an
    `F0_DEPENDENT_ALGORITHM_FAMILIES` candidate's `measure()` at all — no
    `MeasurementRecord`, no ledger `meter_call` event, no wasted compute
    (`formant_cepstral.py`'s own default-cutoff substitution on invalid F0
    is therefore never reached). A non-F0-dependent candidate on the very
    same instance is unaffected."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")
    row = subset[0]

    formant_candidate = next(
        c
        for c in candidates_for_meter(MeterId.M3_FORMANTS)
        if c.algorithm_family == "CEPSTRAL_POLES"
    )
    independent_candidate = candidate_by_id("M3-B0-CURRENT-CENTROID")
    assert independent_candidate.algorithm_family not in measure_stage.F0_DEPENDENT_ALGORITHM_FAMILIES
    assert formant_candidate.algorithm_family in measure_stage.F0_DEPENDENT_ALGORITHM_FAMILIES

    records = measure_stage.run_measure_stage(
        campaign,
        [(row.row_id, 0)],
        [formant_candidate, independent_candidate],
        sr_by_row={row.row_id: row.row.sr_hz},
        f0_unusable_instances=frozenset({(row.row_id, 0)}),
    )

    seen_candidate_ids = {r.candidate_id for r in records}
    assert seen_candidate_ids == {independent_candidate.candidate_id}
    assert len(records) == measure_stage.WITHIN_PROCESS_REPEATS + measure_stage.FRESH_PROCESS_REPEATS

    meter_calls = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"
    ]
    assert {m["candidate_id"] for m in meter_calls} == {independent_candidate.candidate_id}

    # round 28 ADOPT (2) (`[UNDERSPEC-CAL-D64]`): the skip must not be a
    # silent gap — an explicit, durable `measurement_missing` ledger event
    # records exactly the skipped (row_id, probe_index, candidate_id) cell.
    missing_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "measurement_missing"
    ]
    assert len(missing_events) == 1
    assert missing_events[0]["reason"] == "F0_UNUSABLE"
    assert missing_events[0]["cells"] == [[row.row_id, 0, formant_candidate.candidate_id]]


@pytest.mark.slow
def test_run_measure_stage_missing_reason_defaults_to_f0_unusable_but_is_overridable(
    tmp_path: Path,
) -> None:
    """round 29 ADOPT (`[UNDERSPEC-CAL-D65]`): `missing_reason` names the
    `measurement_missing` event's `reason` field (default `"F0_UNUSABLE"`,
    unchanged behavior). `cli._run_c3b`/`cli._run_c4` pass
    `"F0_SELECTION_FAILED"` when `f0_unusable_instances` covers every
    instance because C3a itself recorded no F0 winner."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")
    row = subset[0]

    formant_candidate = next(
        c
        for c in candidates_for_meter(MeterId.M3_FORMANTS)
        if c.algorithm_family == "CEPSTRAL_POLES"
    )

    measure_stage.run_measure_stage(
        campaign,
        [(row.row_id, 0)],
        [formant_candidate],
        sr_by_row={row.row_id: row.row.sr_hz},
        f0_unusable_instances=frozenset({(row.row_id, 0)}),
        missing_reason="F0_SELECTION_FAILED",
    )

    missing_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "measurement_missing"
    ]
    assert len(missing_events) == 1
    assert missing_events[0]["reason"] == "F0_SELECTION_FAILED"
    assert missing_events[0]["cells"] == [[row.row_id, 0, formant_candidate.candidate_id]]


@pytest.mark.slow
def test_run_measure_stage_missing_coverage_event_is_idempotent_across_resume(
    tmp_path: Path,
) -> None:
    """round 28 ADOPT (2) (`[UNDERSPEC-CAL-D64]`): re-running
    `run_measure_stage()` over the same F0-unusable instance (e.g. a resumed
    campaign) must not re-append a duplicate `measurement_missing` event for
    a cell already recorded as missing."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")
    row = subset[0]

    formant_candidate = next(
        c
        for c in candidates_for_meter(MeterId.M3_FORMANTS)
        if c.algorithm_family == "CEPSTRAL_POLES"
    )
    kwargs = dict(
        campaign=campaign,
        instances=[(row.row_id, 0)],
        candidates=[formant_candidate],
        sr_by_row={row.row_id: row.row.sr_hz},
        f0_unusable_instances=frozenset({(row.row_id, 0)}),
    )
    measure_stage.run_measure_stage(**kwargs)
    measure_stage.run_measure_stage(**kwargs)

    missing_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "measurement_missing"
    ]
    assert len(missing_events) == 1


@pytest.mark.slow
def test_run_measure_stage_measures_f0_dependent_candidate_when_instance_usable(
    tmp_path: Path,
) -> None:
    """Companion to the skip test above: an instance NOT named in
    `f0_unusable_instances` (the default, empty set) is measured normally —
    the guard only ever removes coverage, it never adds a spurious skip."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")
    row = subset[0]

    formant_candidate = next(
        c
        for c in candidates_for_meter(MeterId.M3_FORMANTS)
        if c.algorithm_family == "CEPSTRAL_POLES"
    )

    records = measure_stage.run_measure_stage(
        campaign,
        [(row.row_id, 0)],
        [formant_candidate],
        sr_by_row={row.row_id: row.row.sr_hz},
    )
    assert len(records) == measure_stage.WITHIN_PROCESS_REPEATS + measure_stage.FRESH_PROCESS_REPEATS
    assert {r.candidate_id for r in records} == {formant_candidate.candidate_id}
