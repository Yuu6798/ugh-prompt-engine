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
from pathlib import Path

import pytest

from voice_genesis.calibration.campaign import measure_stage, render_stage
from voice_genesis.calibration.campaign.caps import cap_counters_from_ledger, load_cap_counters
from voice_genesis.calibration.campaign.state import load_frozen_campaign
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
