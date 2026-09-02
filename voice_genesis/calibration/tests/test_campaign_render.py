"""`campaign/render_stage.py` のテスト: fresh-process 2 重 render + determinism
+ resume + leakage 検査（IMPLEMENTATION_MAP_v1.md §6.4）。fresh-process
subprocess を伴うため `@pytest.mark.slow`。
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import subprocess
from pathlib import Path

import pytest

from voice_genesis.calibration.campaign import render_stage
from voice_genesis.calibration.campaign.caps import cap_counters_from_ledger
from voice_genesis.calibration.campaign.state import load_frozen_campaign
from voice_genesis.calibration.cost_caps import CapCounters, CostCaps

from ._campaign_fixture import build_tiny_campaign, small_matrix_subset


@pytest.mark.slow
def test_c4_render_refuses_leakage_pre_unseal(tmp_path: Path) -> None:
    """holdout render を unseal 前に試みると `BLOCKED_LEAKAGE` で拒否される
    （§7）。tiny subset は全 456 行を被覆しないため `check_leakage` は常に
    fail-closed する — これはテスト対象の性質そのもの（正当な fail-closed
    経路であり、フル matrix を使わずに検証できる）。"""
    subset = small_matrix_subset(6)
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    with pytest.raises(render_stage.RenderLeakageBlockedError):
        render_stage.run_render_stage(campaign, subset, stage="c4")

    # no renders/ledger side effects from the refused attempt
    assert not campaign.renders_dir.exists() or not any(campaign.renders_dir.iterdir())


@pytest.mark.slow
def test_c1_render_determinism_and_resume(tmp_path: Path) -> None:
    subset = small_matrix_subset(2, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")
    assert outcomes
    assert all(o.status == "rendered" for o in outcomes)
    assert all(len(o.sha256) == 64 for o in outcomes)

    # each instance rendered exactly once with a byte-verified sha256 file
    for o in outcomes:
        pcm_path = campaign.renders_dir / o.row_id / f"{o.probe_index}.pcm"
        assert pcm_path.is_file()
        assert hashlib.sha256(pcm_path.read_bytes()).hexdigest() == o.sha256

    render_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "render"
    ]
    assert len(render_events) == len(outcomes)
    # round 14 finding #2: cpu_seconds (what is actually charged to the
    # compute cap) and wall_seconds (informational only) are both recorded.
    for outcome, event in zip(outcomes, render_events, strict=False):
        assert event["cpu_seconds"] == pytest.approx(outcome.cpu_seconds)
        assert event["wall_seconds"] == pytest.approx(outcome.wall_seconds)
        assert event["cpu_seconds"] >= 0.0
        assert event["wall_seconds"] >= 0.0
    fixture_valid_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "fixture_valid"
    ]
    assert len(fixture_valid_events) == 1

    # resume: second run skips every instance without re-rendering
    resumed = render_stage.run_render_stage(campaign, subset, stage="c1")
    assert all(o.status == "skipped_resume" for o in resumed)
    assert {o.sha256 for o in resumed} == {o.sha256 for o in outcomes}

    # a second fixture_valid event is appended per c1 run (procedural marker,
    # not a render side effect) — no new render events should appear though.
    render_events_after = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "render"
    ]
    assert len(render_events_after) == len(outcomes)


@pytest.mark.slow
def test_c1_render_resume_stale_fails_closed_on_corrupted_file(tmp_path: Path) -> None:
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")
    target = outcomes[0]
    pcm_path = campaign.renders_dir / target.row_id / f"{target.probe_index}.pcm"
    pcm_path.write_bytes(b"\x00\x01corrupted-bytes")

    with pytest.raises(render_stage.RenderStaleError):
        render_stage.run_render_stage(campaign, subset, stage="c1")


@pytest.mark.slow
def test_c1_render_resume_stale_fails_closed_on_missing_file(tmp_path: Path) -> None:
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")
    target = outcomes[0]
    pcm_path = campaign.renders_dir / target.row_id / f"{target.probe_index}.pcm"
    pcm_path.unlink()

    with pytest.raises(render_stage.RenderStaleError):
        render_stage.run_render_stage(campaign, subset, stage="c1")


# ---------------------------------------------------------------------------
# round 14 finding #2: compute is charged from each render worker's own
# reported cpu_seconds (never wall-clock elapsed); a missing/non-finite/
# negative cpu_seconds is a stale/invalid unit — fail closed.
# ---------------------------------------------------------------------------


class _FakeCompletedProcess:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


@pytest.mark.slow
def test_c1_render_invalid_worker_cpu_seconds_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh-process render worker reporting an invalid `cpu_seconds`
    (round 14 finding #2) refuses the whole render unit: no PCM is
    published, no `render` ledger event is appended, and a `stop_event`
    records the reason — instead of silently charging 0 or wall time.

    round 25 (`[UNDERSPEC-CAL-D57]`) revision: this is now ALSO a charged
    `malformed_output` `worker_failed` attempt for each of the 2 fresh-
    process workers (both report the same invalid `cpu_seconds` here) —
    reversing the round 14 "stays uncharged" posture for this path (no
    `cap_counters` is passed in this test, so nothing lands in a persisted
    counter, but the ledger events are still appended unconditionally)."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    def fake_run(cmd, **kwargs):
        return _FakeCompletedProcess(stdout=json.dumps({"pcm_hex": "00", "cpu_seconds": -1.0}))

    monkeypatch.setattr(render_stage.subprocess, "run", fake_run)

    with pytest.raises(render_stage.WorkerCpuSecondsInvalidError):
        render_stage.run_render_stage(campaign, subset, stage="c1")

    assert not campaign.renders_dir.exists() or not any(campaign.renders_dir.iterdir())
    render_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "render"
    ]
    assert render_events == []
    stop_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "stop_event"
    ]
    assert len(stop_events) == 1
    assert stop_events[0]["reason"] == "INVALID_RENDER_WORKER_CPU_SECONDS"
    worker_failed = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "worker_failed"
    ]
    assert len(worker_failed) == 2  # both fresh-process workers failed the same way
    assert all(w["failure_kind"] == "malformed_output" for w in worker_failed)


# ---------------------------------------------------------------------------
# round 23 ADOPT (2) (`[UNDERSPEC-CAL-D52]`): a nondeterministic worker pair
# must charge the attempted work (both workers' cpu_seconds, budget per the
# frozen mode, storage 0) BEFORE raising — not discard it.
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_c1_render_nondeterministic_charges_before_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the two fresh-process workers disagree, both workers' reported
    `cpu_seconds` and 1 budget work unit (per the frozen
    `budget_accounting_mode`) must be charged to `cap_counters`, persisted,
    and recorded as a `render_nondeterministic` ledger event that
    `cap_counters_from_ledger()` can reconstruct — all BEFORE
    `RenderNondeterministicError` is raised. Storage stays 0 (no PCM is ever
    persisted on a mismatch)."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    call_count = {"n": 0}

    def fake_run(cmd, **kwargs):
        call_count["n"] += 1
        # 2 fresh-process workers for the same instance disagree on output.
        pcm_hex = "00" if call_count["n"] == 1 else "01"
        cpu_seconds = 1.0 if call_count["n"] == 1 else 2.0
        return _FakeCompletedProcess(
            stdout=json.dumps({"pcm_hex": pcm_hex, "cpu_seconds": cpu_seconds})
        )

    monkeypatch.setattr(render_stage.subprocess, "run", fake_run)

    counters = CapCounters()
    caps = CostCaps(
        compute=1000.0,
        storage=1_000_000,
        budget=1000.0,
        budget_accounting_mode="per_unit_fixed",
        budget_unit_cost=3.0,
    )

    with pytest.raises(render_stage.RenderNondeterministicError):
        render_stage.run_render_stage(
            campaign, subset, stage="c1", cap_counters=counters, cost_caps=caps
        )

    # both workers' cpu_seconds were charged (1.0 + 2.0); storage stayed 0;
    # budget charged 1 work unit at the frozen per-unit cost.
    assert counters.compute_used == pytest.approx(3.0)
    assert counters.storage_used == 0
    assert counters.budget_used == pytest.approx(3.0)

    nondet_events = [
        e.payload
        for e in campaign.ledger.entries
        if e.payload.get("kind") == "render_nondeterministic"
    ]
    assert len(nondet_events) == 1
    assert nondet_events[0]["cpu_seconds"] == pytest.approx(3.0)
    assert nondet_events[0]["storage_bytes"] == 0
    assert nondet_events[0]["row_id"] == subset[0].row_id
    assert nondet_events[0]["probe_index"] == 0

    stop_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "stop_event"
    ]
    assert len(stop_events) == 1
    assert stop_events[0]["reason"] == "BLOCKED_C1_GENERATOR_NONDETERMINISTIC"

    # no PCM was ever persisted for the disagreeing instance.
    assert not campaign.renders_dir.exists() or not any(campaign.renders_dir.iterdir())

    # reconstruction from the ledger alone matches the persisted counters —
    # cap_counters_from_ledger() must extend its reducer to see this kind.
    derived = cap_counters_from_ledger(campaign.ledger.entries, caps)
    assert derived.compute_used == pytest.approx(counters.compute_used)
    assert derived.storage_used == counters.storage_used
    assert derived.budget_used == pytest.approx(counters.budget_used)


# ---------------------------------------------------------------------------
# round 24 ADOPT (1) (`[UNDERSPEC-CAL-D55]`): a fresh-process render worker
# that fails post-spawn (timeout / nonzero exit / malformed JSON) must charge
# the attempted work BEFORE the original error propagates — not discard it.
# ---------------------------------------------------------------------------


class _FakeCompletedProcessRender:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


@pytest.mark.slow
def test_c1_render_worker_timeout_charges_before_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 25 (`[UNDERSPEC-CAL-D57]`) revision: both fresh-process workers
    time out (neither is skipped just because the other already failed), so
    both are independently charged their own `worker_failed` event."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 0.0))

    monkeypatch.setattr(render_stage.subprocess, "run", fake_run)
    # 2 ticks (before, after) per worker, delta 1.5 each -- 2 workers.
    children_cpu_ticks = itertools.count(20.0, 1.5)
    monkeypatch.setattr(render_stage, "_children_cpu_seconds", lambda: next(children_cpu_ticks))

    counters = CapCounters()
    caps = CostCaps(
        compute=1000.0, storage=1_000_000, budget=1000.0, budget_accounting_mode="local_zero_cost"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        render_stage.run_render_stage(
            campaign, subset, stage="c1", cap_counters=counters, cost_caps=caps
        )

    expected_total = 2 * 1.5
    assert counters.compute_used == pytest.approx(expected_total)
    assert counters.storage_used == 0
    worker_failed = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "worker_failed"
    ]
    assert len(worker_failed) == 2
    assert worker_failed[0]["stage"] == "render"
    assert worker_failed[0]["failure_kind"] == "timeout"
    assert worker_failed[0]["row_id"] == subset[0].row_id
    assert worker_failed[0]["probe_index"] == 0
    assert "candidate_id" not in worker_failed[0]
    assert all(w["cpu_seconds"] == pytest.approx(1.5) for w in worker_failed)
    assert all(w["storage_bytes"] == 0 for w in worker_failed)
    assert not campaign.renders_dir.exists() or not any(campaign.renders_dir.iterdir())

    derived = cap_counters_from_ledger(campaign.ledger.entries, caps)
    assert derived.compute_used == pytest.approx(counters.compute_used)
    assert derived.storage_used == counters.storage_used
    assert derived.budget_used == pytest.approx(counters.budget_used)


@pytest.mark.slow
def test_c1_render_worker_nonzero_exit_charges_reported_cpu_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nonzero-exit render worker's captured stdout still carries a
    well-formed report — the charge must use the worker's own reported
    `cpu_seconds` (not the coarser parent-observed delta) when one is
    recoverable.

    round 25 (`[UNDERSPEC-CAL-D57]`) revision: both fresh-process workers
    fail the same way, so both run (neither skipped) and both are charged."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    worker_stdout = json.dumps({"pcm_hex": "00", "cpu_seconds": 3.0})

    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd=cmd, output=worker_stdout, stderr="boom")

    monkeypatch.setattr(render_stage.subprocess, "run", fake_run)

    counters = CapCounters()
    caps = CostCaps(
        compute=1000.0, storage=1_000_000, budget=1000.0, budget_accounting_mode="local_zero_cost"
    )
    with pytest.raises(subprocess.CalledProcessError):
        render_stage.run_render_stage(
            campaign, subset, stage="c1", cap_counters=counters, cost_caps=caps
        )

    expected_total = 2 * 3.0
    assert counters.compute_used == pytest.approx(expected_total)
    worker_failed = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "worker_failed"
    ]
    assert len(worker_failed) == 2
    assert all(w["failure_kind"] == "nonzero_exit" for w in worker_failed)
    assert all(w["cpu_seconds"] == pytest.approx(3.0) for w in worker_failed)

    derived = cap_counters_from_ledger(campaign.ledger.entries, caps)
    assert derived.compute_used == pytest.approx(counters.compute_used)


@pytest.mark.slow
def test_c1_render_worker_malformed_json_charges_before_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 25 (`[UNDERSPEC-CAL-D57]`) revision: both fresh-process workers
    return malformed JSON, so both run (neither skipped) and both are
    charged."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    def fake_run(cmd, **kwargs):
        return _FakeCompletedProcessRender(stdout="{not valid json")

    monkeypatch.setattr(render_stage.subprocess, "run", fake_run)
    # 2 ticks (before, after) per worker, delta 0.25 each -- 2 workers.
    children_cpu_ticks = itertools.count(7.0, 0.25)
    monkeypatch.setattr(render_stage, "_children_cpu_seconds", lambda: next(children_cpu_ticks))

    counters = CapCounters()
    caps = CostCaps(
        compute=1000.0, storage=1_000_000, budget=1000.0, budget_accounting_mode="local_zero_cost"
    )
    with pytest.raises(json.JSONDecodeError):
        render_stage.run_render_stage(
            campaign, subset, stage="c1", cap_counters=counters, cost_caps=caps
        )

    expected_total = 2 * 0.25
    assert counters.compute_used == pytest.approx(expected_total)
    worker_failed = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "worker_failed"
    ]
    assert len(worker_failed) == 2
    assert all(w["failure_kind"] == "malformed_output" for w in worker_failed)
    assert all(w["cpu_seconds"] == pytest.approx(0.25) for w in worker_failed)

    derived = cap_counters_from_ledger(campaign.ledger.entries, caps)
    assert derived.compute_used == pytest.approx(counters.compute_used)


@pytest.mark.slow
def test_c1_render_worker_failure_cost_cap_breach_raises_cost_cap_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When charging the whole failed batch's attempted compute itself
    breaches the frozen cap, `CostCapExceededError` takes priority over the
    original `TimeoutExpired`/etc. — same priority as every other
    charge-then-check call site in this package.

    round 25 (`[UNDERSPEC-CAL-D57]`) revision: the cap check now runs ONCE,
    after both fresh-process workers (both time out here) have been
    charged — not per worker."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 0.0))

    monkeypatch.setattr(render_stage.subprocess, "run", fake_run)
    # 2 ticks (before, after) per worker, delta 1.0 each -> batch total 2.0,
    # well over the tiny compute cap.
    children_cpu_ticks = itertools.count(0.0, 1.0)
    monkeypatch.setattr(render_stage, "_children_cpu_seconds", lambda: next(children_cpu_ticks))

    counters = CapCounters()
    tiny_caps = CostCaps(
        compute=1e-6, storage=1_000_000, budget=1000.0, budget_accounting_mode="local_zero_cost"
    )
    with pytest.raises(render_stage.CostCapExceededError):
        render_stage.run_render_stage(
            campaign, subset, stage="c1", cap_counters=counters, cost_caps=tiny_caps
        )

    expected_total = 2 * 1.0
    assert counters.compute_used == pytest.approx(expected_total)
    worker_failed = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "worker_failed"
    ]
    assert len(worker_failed) == 2
    stop_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "stop_event"
    ]
    assert len(stop_events) == 1
    assert stop_events[0]["reason"] == "COST_CAP_EXCEEDED"


# ---------------------------------------------------------------------------
# round 25 (`[UNDERSPEC-CAL-D57]`): unified worker-attempt accounting for
# render's 2-worker pair -- both workers run to completion regardless of
# either's outcome, and the whole batch is charged together before the
# batch's first failure propagates. Supersedes the round 24 ADOPT (1)
# posture of charging only the ONE failing worker and discarding an
# already-succeeded sibling worker's compute uncharged.
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_c1_render_worker1_ok_worker2_fails_charges_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """worker 1 succeeds, worker 2 times out: worker 1's already-spent
    compute must not be discarded uncharged just because worker 2 failed --
    both are charged (worker 1 via a `worker_attempts_discarded` event,
    worker 2 via its own `worker_failed` event) before the original
    `TimeoutExpired` propagates."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    call_count = {"n": 0}

    def fake_run(cmd, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeCompletedProcess(stdout=json.dumps({"pcm_hex": "00", "cpu_seconds": 2.0}))
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 0.0))

    monkeypatch.setattr(render_stage.subprocess, "run", fake_run)
    children_cpu_ticks = itertools.count(10.0, 1.5)
    monkeypatch.setattr(render_stage, "_children_cpu_seconds", lambda: next(children_cpu_ticks))

    counters = CapCounters()
    caps = CostCaps(
        compute=1000.0,
        storage=1_000_000,
        budget=1000.0,
        budget_accounting_mode="per_unit_fixed",
        budget_unit_cost=5.0,
    )
    with pytest.raises(subprocess.TimeoutExpired):
        render_stage.run_render_stage(
            campaign, subset, stage="c1", cap_counters=counters, cost_caps=caps
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
    assert discarded[0]["stage"] == "render"
    assert "candidate_id" not in discarded[0]
    attempts = discarded[0]["discarded_success_attempts"]
    assert len(attempts) == 1
    assert attempts[0]["cpu_seconds"] == pytest.approx(2.0)

    expected_compute = 2.0 + 1.5
    assert counters.compute_used == pytest.approx(expected_compute)
    assert counters.storage_used == 0
    # round 26 ADOPT (3) (`[UNDERSPEC-CAL-D59]`): 1 budget unit for the whole
    # 2-attempt batch (one attempted render invocation), not 1 per attempt.
    assert counters.budget_used == pytest.approx(1 * 5.0)
    assert not campaign.renders_dir.exists() or not any(campaign.renders_dir.iterdir())

    derived = cap_counters_from_ledger(campaign.ledger.entries, caps)
    assert derived.compute_used == pytest.approx(counters.compute_used)
    assert derived.storage_used == counters.storage_used
    assert derived.budget_used == pytest.approx(counters.budget_used)


@pytest.mark.slow
@pytest.mark.parametrize("bad_cpu_seconds", ["abc", math.nan, -1.0])
def test_c1_render_worker_invalid_cpu_seconds_now_charged_malformed_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_cpu_seconds: object
) -> None:
    """round 25 (`[UNDERSPEC-CAL-D57]`) finding "Charge parseable but
    invalid worker results": an exit-0 render worker with parseable JSON but
    an invalid `cpu_seconds` (non-numeric / NaN / negative) is now a charged
    `malformed_output` `worker_failed` attempt (both workers report the same
    invalid value here) -- reversing the round 14 finding #2 "stays
    uncharged" posture for this specific path."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    def fake_run(cmd, **kwargs):
        return _FakeCompletedProcess(
            stdout=json.dumps({"pcm_hex": "00", "cpu_seconds": bad_cpu_seconds})
        )

    monkeypatch.setattr(render_stage.subprocess, "run", fake_run)
    children_cpu_ticks = itertools.count(0.0, 0.5)
    monkeypatch.setattr(render_stage, "_children_cpu_seconds", lambda: next(children_cpu_ticks))

    counters = CapCounters()
    caps = CostCaps(
        compute=1000.0, storage=1_000_000, budget=1000.0, budget_accounting_mode="local_zero_cost"
    )
    with pytest.raises(render_stage.WorkerCpuSecondsInvalidError):
        render_stage.run_render_stage(
            campaign, subset, stage="c1", cap_counters=counters, cost_caps=caps
        )

    worker_failed = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "worker_failed"
    ]
    assert len(worker_failed) == 2
    assert all(w["failure_kind"] == "malformed_output" for w in worker_failed)
    assert counters.compute_used > 0.0  # RUSAGE_CHILDREN fallback, not 0

    derived = cap_counters_from_ledger(campaign.ledger.entries, caps)
    assert derived.compute_used == pytest.approx(counters.compute_used)


@pytest.mark.slow
def test_c1_render_worker_invalid_pcm_hex_charged_malformed_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 25 (`[UNDERSPEC-CAL-D57]`) finding "Charge parseable but
    invalid worker results": an exit-0 render worker with a VALID
    `cpu_seconds` but an undecodable `pcm_hex` is charged `malformed_output`
    using its own valid `cpu_seconds` (not the RUSAGE_CHILDREN fallback,
    since that field itself validated fine) -- this failure previously
    escaped `_FreshRenderWorkerFailure`/`charge_worker_failure()` entirely,
    surfacing only as a bare, uncharged `bytes.fromhex()` ValueError AFTER
    the byte-equality comparison (which two identically-invalid hex strings
    could even pass undetected)."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    def fake_run(cmd, **kwargs):
        return _FakeCompletedProcess(
            stdout=json.dumps({"pcm_hex": "not-hex-at-all", "cpu_seconds": 1.25})
        )

    monkeypatch.setattr(render_stage.subprocess, "run", fake_run)

    counters = CapCounters()
    caps = CostCaps(
        compute=1000.0, storage=1_000_000, budget=1000.0, budget_accounting_mode="local_zero_cost"
    )
    with pytest.raises(ValueError):
        render_stage.run_render_stage(
            campaign, subset, stage="c1", cap_counters=counters, cost_caps=caps
        )

    worker_failed = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "worker_failed"
    ]
    assert len(worker_failed) == 2
    assert all(w["failure_kind"] == "malformed_output" for w in worker_failed)
    # the reported cpu_seconds (1.25) itself validated fine -- charged as-is.
    assert all(w["cpu_seconds"] == pytest.approx(1.25) for w in worker_failed)
    assert counters.compute_used == pytest.approx(2 * 1.25)
    assert not campaign.renders_dir.exists() or not any(campaign.renders_dir.iterdir())

    derived = cap_counters_from_ledger(campaign.ledger.entries, caps)
    assert derived.compute_used == pytest.approx(counters.compute_used)
