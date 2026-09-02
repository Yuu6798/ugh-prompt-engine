"""`campaign/render_stage.py` のテスト: fresh-process 2 重 render + determinism
+ resume + leakage 検査（IMPLEMENTATION_MAP_v1.md §6.4）。fresh-process
subprocess を伴うため `@pytest.mark.slow`。
"""

from __future__ import annotations

import hashlib
import json
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
    records the reason — instead of silently charging 0 or wall time."""
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
