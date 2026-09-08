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
import sys
import time
from pathlib import Path

import pytest

from voice_genesis.calibration.campaign import holdout_stage, render_stage
from voice_genesis.calibration.campaign.caps import cap_counters_from_ledger
from voice_genesis.calibration.campaign.state import load_frozen_campaign
from voice_genesis.calibration.campaign.time_budget import TimeBudget
from voice_genesis.calibration.candidates.registry import candidates_for_meter
from voice_genesis.calibration.cost_caps import CapCounters, CostCaps
from voice_genesis.calibration.vocab import MeterId

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
def test_c1_render_time_budget_partial_slice_then_resume(tmp_path: Path) -> None:
    """R2（design memo `design_runner_robustness.md`, `[UNDERSPEC-CAL-D79]`）:
    an essentially-zero `time_budget` still lets the first (already
    in-flight) unit finish, then stops dispatching before the second —
    `completed_all=False`, `instances_completed_this_run>=1`,
    `instances_remaining>0`, and (unlike an uninterrupted run) no
    `fixture_valid` event. Re-running without a budget finishes every
    remaining unit (existing resume path) and appends exactly one
    `fixture_valid` event — the same phase-transition shape as an
    uninterrupted run."""
    subset = small_matrix_subset(2, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    outcomes, slice_status = render_stage.run_render_stage(
        campaign, subset, stage="c1", time_budget=TimeBudget.start_now(0.01)
    )
    assert slice_status.completed_all is False
    assert slice_status.instances_completed_this_run >= 1
    assert slice_status.instances_remaining > 0
    assert slice_status.time_budget_seconds == pytest.approx(0.01)
    assert len(outcomes) == slice_status.instances_completed_this_run
    assert not any(
        e.payload.get("kind") == "fixture_valid" for e in campaign.ledger.entries
    )
    # the units that DID complete are real, verified renders — not stubs.
    for o in outcomes:
        assert o.status == "rendered"
        pcm_path = campaign.renders_dir / o.row_id / f"{o.probe_index}.pcm"
        assert pcm_path.is_file()

    # resume without a budget: finishes every remaining unit and performs
    # the phase transition exactly once.
    resumed_outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")
    total_units = slice_status.instances_completed_this_run + slice_status.instances_remaining
    assert len(resumed_outcomes) == total_units
    fixture_valid_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "fixture_valid"
    ]
    assert len(fixture_valid_events) == 1


@pytest.mark.slow
def test_c1_render_time_budget_remaining_matches_true_completed_state(tmp_path: Path) -> None:
    """rehearsal 4 finding G (adopted, `[UNDERSPEC-CAL-D79]`): `instances_
    remaining` must equal `total_units - true_complete_count` (the index
    built from the ledger at the top of this invocation, plus any units
    newly rendered by it) — not `len(units) - len(outcomes)`, which
    silently degenerated to `len(units)` whenever `outcomes` stayed empty
    (the budget already expired before the loop's first boundary check).
    Rehearsal 4 observed `instances_remaining` jump BACKWARD 77->85 at a
    0.001s budget on a half-complete campaign — this pins the fix: with
    half of `subset` already rendered by a prior invocation, a call whose
    budget is guaranteed already-expired must report the TRUE remaining
    count (`total - already_complete`) and zero newly-completed — never
    treat the untouched half-complete prefix as if it were still fully
    unrendered.

    Codex PR #345 round 4 finding #3 (adopted, category ③,
    `[UNDERSPEC-CAL-D79]`): `outcomes` itself is no longer empty here — the
    O(1) index skip for the already-completed half is now checked BEFORE
    the budget boundary, so every one of those units still comes back
    `skipped_resume` even though the budget is already expired; only the
    genuinely pending (not-yet-rendered) other half is blocked by the
    expired budget and never dispatched."""
    subset = small_matrix_subset(4, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    # c1 only renders CALIBRATION/SELECTION-split (+ control) rows, so
    # derive the "half" split from the rows actually reachable by c1 --
    # not `subset[:half]`, which may include a HOLDOUT-only row that never
    # renders at all.
    all_units = render_stage.workunits.enumerate_c1_render_units(
        subset, campaign.realized_split.assignment
    )
    total_units = len({(u.row_id, u.probe_index) for u in all_units})
    assert total_units > 0
    renderable_row_ids = sorted({u.row_id for u in all_units})
    assert len(renderable_row_ids) >= 2  # need a genuine "half" split
    half_row_ids = set(renderable_row_ids[: len(renderable_row_ids) // 2])
    half_subset = [mr for mr in subset if mr.row_id in half_row_ids]

    half_outcomes = render_stage.run_render_stage(campaign, half_subset, stage="c1")
    assert half_outcomes
    assert all(o.status == "rendered" for o in half_outcomes)
    true_completed = len(half_outcomes)
    assert total_units > true_completed  # genuinely only part done

    budget = TimeBudget.start_now(0.001)
    time.sleep(0.05)  # guarantee expiry regardless of machine speed/scheduling
    outcomes, slice_status = render_stage.run_render_stage(
        campaign, subset, stage="c1", time_budget=budget
    )
    # every already-completed unit is skipped regardless of the expired
    # budget (index consulted before the budget check) — none of them are
    # newly rendered, and no not-yet-rendered unit is dispatched.
    assert len(outcomes) == true_completed
    assert all(o.status == "skipped_resume" for o in outcomes)
    assert slice_status.completed_all is False
    assert slice_status.instances_completed_this_run == 0
    assert slice_status.instances_remaining == total_units - true_completed
    # not the pre-fix bug: remaining must NOT regress to the full unit count
    # just because this call's own loop never walked a single unit.
    assert slice_status.instances_remaining < total_units


@pytest.mark.slow
def test_c1_render_resume_skips_completed_prefix_and_still_makes_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex PR #345 finding #3 (adopted, category ③, `[UNDERSPEC-CAL-D79]`):
    a resumed slice's already-completed units must never re-enter
    `render_instance()` — pre-fix, `run_render_stage()`'s loop called
    `render_instance()` for every unit including already-completed ones,
    and each such call did its own full-ledger `_recorded_render_sha()`
    rescan plus a PCM read+sha256, so a growing completed prefix made a
    fixed `--time-budget-seconds` slice do less and less new work (in the
    worst case, expiring before any unfinished unit was even reached).

    This test renders a smaller row subset first (the "already completed"
    prefix from a prior invocation), then resumes with a wider subset — the
    extra rows are genuinely new work the same campaign has not touched
    yet — under a modest time budget, and asserts: (1) `render_instance()`
    is called exactly once per newly-rendered unit, never once for any of
    the already-completed prefix units (proving the fast index-based skip,
    not `render_instance()`'s own resume check, is what handles them), and
    (2) real progress happens (at least 1 new unit actually renders) within
    the budget despite the completed prefix."""
    resumed_subset = small_matrix_subset(4, family="F0_CONTROL")
    completed_subset = resumed_subset[:2]
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=resumed_subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    completed_outcomes = render_stage.run_render_stage(campaign, completed_subset, stage="c1")
    assert completed_outcomes
    assert all(o.status == "rendered" for o in completed_outcomes)

    render_call_count = 0
    orig_render_instance = render_stage.render_instance

    def _counting_render_instance(*args, **kwargs):
        nonlocal render_call_count
        render_call_count += 1
        return orig_render_instance(*args, **kwargs)

    monkeypatch.setattr(render_stage, "render_instance", _counting_render_instance)

    outcomes, slice_status = render_stage.run_render_stage(
        campaign, resumed_subset, stage="c1", time_budget=TimeBudget.start_now(15.0)
    )

    already_completed_keys = {(o.row_id, o.probe_index) for o in completed_outcomes}
    newly_rendered = [
        o
        for o in outcomes
        if o.status == "rendered" and (o.row_id, o.probe_index) not in already_completed_keys
    ]
    # progress: new work was actually reached and rendered within budget,
    # despite the completed prefix ahead of it in `resumed_subset`.
    assert newly_rendered

    # every unit belonging to the completed prefix comes back
    # `skipped_resume` (the fast index-based path), never re-rendered.
    for o in outcomes:
        if (o.row_id, o.probe_index) in already_completed_keys:
            assert o.status == "skipped_resume"

    # `render_instance()` was called exactly once per newly-rendered unit —
    # zero times for any of the (many more) already-completed prefix units.
    assert render_call_count == len(newly_rendered)


@pytest.mark.slow
def test_c1_render_slice_status_counts_only_newly_rendered_units(tmp_path: Path) -> None:
    """Codex PR #345 round 3 finding F6 (adopted, category ②): pre-fix,
    `SliceStatus.instances_completed_this_run` was `len(outcomes)`, which
    includes every `skipped_resume` entry from the O(1) index skip — so a
    resumed slice overcounted its own progress (and a slice that touched
    only the already-completed prefix still reported nonzero progress).
    It must count only units newly rendered by THIS invocation;
    `instances_remaining` is unaffected."""
    completed_subset = small_matrix_subset(2, family="F0_CONTROL")
    wider_subset = small_matrix_subset(4, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=wider_subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    completed_outcomes = render_stage.run_render_stage(campaign, completed_subset, stage="c1")
    assert all(o.status == "rendered" for o in completed_outcomes)

    outcomes, slice_status = render_stage.run_render_stage(
        campaign, wider_subset, stage="c1", time_budget=TimeBudget.start_now(30.0)
    )
    newly_rendered = [o for o in outcomes if o.status == "rendered"]
    skipped = [o for o in outcomes if o.status == "skipped_resume"]
    assert len(skipped) == len(completed_outcomes)  # the already-completed prefix
    assert newly_rendered  # genuinely new work in the wider subset
    assert slice_status.completed_all is True
    assert slice_status.instances_completed_this_run == len(newly_rendered)
    assert slice_status.instances_completed_this_run != len(outcomes)  # would over-count pre-fix
    assert slice_status.instances_remaining == 0

    # zero-new-unit slice: every unit in `wider_subset` is now already
    # rendered — a further resumed call must report exactly 0 progress, not
    # `len(outcomes)` (all `skipped_resume`).
    zero_new_outcomes, zero_new_status = render_stage.run_render_stage(
        campaign, wider_subset, stage="c1", time_budget=TimeBudget.start_now(30.0)
    )
    assert all(o.status == "skipped_resume" for o in zero_new_outcomes)
    assert zero_new_status.instances_completed_this_run == 0
    assert zero_new_status.instances_remaining == 0
    assert zero_new_status.completed_all is True


@pytest.mark.slow
def test_c4_slice_remaining_excludes_prior_c1_render_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex PR #345 round 4 finding #2 (adopted, category ②,
    `[UNDERSPEC-CAL-D79]`): `completed_units` (`_render_index_from_ledger`)
    indexes every `render` event in the WHOLE ledger, so on a sliced
    `c4-holdout` render sub-phase it also holds every prior C1 render
    event — those belong to a disjoint unit set (`units` here holds only
    C4 units) and must not count toward C4's own completed/remaining
    tally. Pre-fix, `instances_remaining = len(units) - (len(completed_
    units) + newly_rendered_count)` used the WHOLE index size, so with
    more C1 renders on the ledger than C4 has units at all (asserted
    below), `instances_remaining` went negative even on the very first
    C4 slice."""
    subset = small_matrix_subset(4, family="APERIODICITY_GT")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    c1_outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")
    assert c1_outcomes
    assert all(o.status == "rendered" for o in c1_outcomes)

    from voice_genesis.calibration.campaign import workunits

    c4_units = workunits.enumerate_c4_render_units(subset, campaign.realized_split.assignment)
    total_c4_units = len({(u.row_id, u.probe_index) for u in c4_units})
    assert total_c4_units > 0
    # sanity: the C1 ledger already holds MORE render events than C4 has
    # units at all — the exact condition that drove the pre-fix formula
    # negative (`total_c4_units - (len(c1_events) + 0)`).
    assert len(c1_outcomes) >= total_c4_units

    monkeypatch.setattr(render_stage, "_refuse_if_pre_unseal_holdout", lambda *a, **kw: None)

    # a modest budget: real progress on C4, but not necessarily every unit.
    outcomes1, slice1 = render_stage.run_render_stage(
        campaign, subset, stage="c4", time_budget=TimeBudget.start_now(0.01)
    )
    rendered_so_far = sum(1 for o in outcomes1 if o.status == "rendered")
    assert 0 <= rendered_so_far <= total_c4_units
    # exact intersection-scoped count — never derived from (nor inflated
    # by) the C1 render events sharing the same ledger.
    assert slice1.instances_remaining == total_c4_units - rendered_so_far
    assert slice1.instances_remaining >= 0

    # resume with a generous budget: finishes every remaining C4 unit.
    outcomes2, slice2 = render_stage.run_render_stage(
        campaign, subset, stage="c4", time_budget=TimeBudget.start_now(30.0)
    )
    assert slice2.completed_all is True
    assert slice2.instances_remaining == 0
    total_c4_rendered = len(
        {(o.row_id, o.probe_index) for o in (*outcomes1, *outcomes2) if o.status == "rendered"}
    )
    assert total_c4_rendered == total_c4_units


@pytest.mark.slow
def test_c1_render_all_units_complete_transitions_despite_zero_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex PR #345 round 4 finding #3 (adopted, category ③,
    `[UNDERSPEC-CAL-D79]`): a campaign whose ledger already has every C1
    unit rendered but no `fixture_valid` event yet (mirrors a process
    interrupted after the final unit was written but before the phase
    transition) must still transition on a resumed call, EVEN with a
    `--time-budget-seconds` so small it is already expired before the
    call's own index rebuild finishes — the budget check must never block
    a unit that is already complete, only a genuinely pending dispatch."""
    subset = small_matrix_subset(2, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    # simulate the interrupted-before-transition state directly: render
    # every unit (each `render` event goes through the real, disk-backed
    # `Ledger.append()`), but swallow only the trailing `fixture_valid`
    # event — `entries` has no public setter (append-only ledger, `entries`
    # is a read-only property over the on-disk-backed cache), so this is
    # the faithful way to leave "every unit rendered, stage not yet
    # transitioned" on both the in-memory and on-disk ledger.
    real_append = campaign.ledger.append

    def _append_except_fixture_valid(payload):
        if payload.get("kind") == "fixture_valid":
            return None
        return real_append(payload)

    monkeypatch.setattr(campaign.ledger, "append", _append_except_fixture_valid)
    outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")
    assert outcomes
    assert all(o.status == "rendered" for o in outcomes)
    assert not any(e.payload.get("kind") == "fixture_valid" for e in campaign.ledger.entries)
    monkeypatch.undo()  # restore real `append()` before the resumed call below

    # force the budget to already be expired by the time the loop's first
    # boundary check runs, regardless of how long the index rebuild takes.
    monkeypatch.setattr(render_stage.TimeBudget, "expired", lambda self: True)

    resumed_outcomes, slice_status = render_stage.run_render_stage(
        campaign, subset, stage="c1", time_budget=TimeBudget.start_now(0.0001)
    )
    assert all(o.status == "skipped_resume" for o in resumed_outcomes)
    assert slice_status.completed_all is True
    assert slice_status.instances_remaining == 0
    fixture_valid_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "fixture_valid"
    ]
    assert len(fixture_valid_events) == 1


def _render_instance_directly(campaign, subset, outcome):
    mr = next(mr for mr in subset if mr.row_id == outcome.row_id)
    split = campaign.realized_split.assignment[outcome.row_id]
    return render_stage.render_instance(
        campaign,
        mr.row,
        family=mr.row.family,
        split=split,
        row_id=outcome.row_id,
        probe_index=outcome.probe_index,
    )


@pytest.mark.slow
def test_c1_render_resume_stale_fails_closed_on_corrupted_file(tmp_path: Path) -> None:
    """Codex PR #345 round 3 finding F5 (adopted, category ③,
    `[UNDERSPEC-CAL-D79]`): with a single-unit subset, the resumed
    `run_render_stage()` call IS the completing invocation (`completed_all`
    — not a `PARTIAL_SLICE` exit), so its post-loop validation pass
    (`_validate_skipped_resume_outcomes`) now catches this corrupted PCM
    before the transition and raises `RenderResumeIndexIntegrityError`
    instead of silently returning a `skipped_resume` outcome for it (the
    pre-fix behavior this test used to assert — see round 3 finding F5).
    `render_instance()` called directly is unchanged: its own resume/stale
    check still fails closed (matching the module docstring's resume
    contract), and measurement-time integrity
    (`measure_stage._verify_and_load_rendered_pcm`) remains a second,
    independent fail-closed net every rendered unit passes through before
    being measured."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")
    target = outcomes[0]
    pcm_path = campaign.renders_dir / target.row_id / f"{target.probe_index}.pcm"
    pcm_path.write_bytes(b"\x00\x01corrupted-bytes")

    with pytest.raises(render_stage.RenderResumeIndexIntegrityError) as exc_info:
        render_stage.run_render_stage(campaign, subset, stage="c1")
    assert len(exc_info.value.failing_units) == 1
    failing_row_id, failing_probe_index, failing_detail = exc_info.value.failing_units[0]
    assert (failing_row_id, failing_probe_index) == (target.row_id, target.probe_index)
    # round 5 finding S4 (adopted, `[UNDERSPEC-CAL-D79]`): the detail wording
    # now comes from the shared `render_stage._verify_pcm_sidecar()` helper
    # (also used by `measure_stage._verify_and_load_rendered_pcm()`).
    # Corrupting only the `.pcm` file (not its `.sha256` sidecar, which
    # still holds the ORIGINAL correct sha256) now fails the sidecar check
    # first — a strictly earlier, stricter checkpoint than the old
    # render_stage-only "current file sha256=" wording this test used to
    # assert, which compared straight against the ledger-pinned sha.
    assert "does not match sidecar" in failing_detail

    # transition blocked: still exactly the 1 `fixture_valid` from the first
    # (clean) run — no second one from the failed resume — and a
    # `stop_event` recording the mismatch was appended instead.
    fixture_valid_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "fixture_valid"
    ]
    assert len(fixture_valid_events) == 1
    stop_events = [
        e.payload
        for e in campaign.ledger.entries
        if e.payload.get("kind") == "stop_event"
        and e.payload.get("reason") == "RENDER_RESUME_INDEX_INTEGRITY_MISMATCH"
    ]
    assert len(stop_events) == 1
    assert stop_events[0]["units"] == [
        {
            "row_id": target.row_id,
            "probe_index": target.probe_index,
            "detail": failing_detail,
        }
    ]

    with pytest.raises(render_stage.RenderStaleError):
        _render_instance_directly(campaign, subset, target)

    # recovery: render is deterministic (module docstring), so writing the
    # byte-identical PCM back (what a manual re-render of this exact
    # instance would reproduce) — WITHOUT touching the ledger — is the
    # documented recovery path. Once bytes match the pinned sha again, the
    # next invocation validates clean and transitions normally.
    pcm_path.write_bytes(bytes.fromhex(_render_pcm_hex(campaign, subset, target)))
    recovered = render_stage.run_render_stage(campaign, subset, stage="c1")
    assert all(o.status == "skipped_resume" for o in recovered)
    fixture_valid_events_after = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "fixture_valid"
    ]
    assert len(fixture_valid_events_after) == 2


@pytest.mark.slow
def test_c1_render_resume_stale_fails_closed_on_missing_file(tmp_path: Path) -> None:
    """Same round 3 finding F5 distinction as the corrupted-file test above:
    a single-unit resumed call is the completing invocation, so the missing
    PCM is now caught by the post-loop validation pass and blocks the
    transition — `render_instance()` called directly still fails closed
    independently."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")
    target = outcomes[0]
    pcm_path = campaign.renders_dir / target.row_id / f"{target.probe_index}.pcm"
    pcm_path.unlink()

    with pytest.raises(render_stage.RenderResumeIndexIntegrityError) as exc_info:
        render_stage.run_render_stage(campaign, subset, stage="c1")
    assert exc_info.value.failing_units[0][:2] == (target.row_id, target.probe_index)

    fixture_valid_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "fixture_valid"
    ]
    assert len(fixture_valid_events) == 1

    with pytest.raises(render_stage.RenderStaleError):
        _render_instance_directly(campaign, subset, target)


def _render_pcm_hex(campaign, subset, outcome) -> str:
    """Deterministically reproduces the exact PCM bytes for `outcome` by
    invoking the same fresh-process worker `render_instance()` uses,
    bypassing the ledger-based resume check entirely (mirrors the manual
    recovery path documented on `RenderResumeIndexIntegrityError`: regen
    off-ledger, then restore the file). Returns hex so callers can round
    -trip via `bytes.fromhex()`."""
    mr = next(mr for mr in subset if mr.row_id == outcome.row_id)
    split = campaign.realized_split.assignment[outcome.row_id]
    payload = {
        "row_json": json.dumps(mr.row.to_canonical_dict()),
        "secret_hex": campaign.render_root_secret.hex(),
        "campaign_id": campaign.campaign_id,
        "family": mr.row.family,
        "split": split.value,
        "row_id": outcome.row_id,
        "probe_index": outcome.probe_index,
    }
    argv = [
        sys.executable,
        "-m",
        "voice_genesis.calibration.campaign._render_worker",
        json.dumps(payload),
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60.0, check=True)
    raw = json.loads(proc.stdout)
    return raw["pcm_hex"]


@pytest.mark.slow
def test_c1_render_resume_stale_lists_all_failing_units_then_recovers(tmp_path: Path) -> None:
    """Codex PR #345 round 3 finding F5 (adopted, category ③,
    `[UNDERSPEC-CAL-D79]`), full scenario from the task brief: one recorded
    PCM deleted, one corrupted, one left intact. The completing invocation
    must stop (no `fixture_valid`) with a single `stop_event` listing BOTH
    failing units (not just the first one hit) — then, after the documented
    manual recovery (byte-identical PCM restored off-ledger), a subsequent
    run transitions normally."""
    subset = small_matrix_subset(3, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")
    assert len(outcomes) >= 3  # `PROBE_REPEATS` fans a 3-row subset out further
    deleted_target, corrupted_target, intact_target = outcomes[0], outcomes[1], outcomes[2]

    deleted_path = campaign.renders_dir / deleted_target.row_id / f"{deleted_target.probe_index}.pcm"
    corrupted_path = (
        campaign.renders_dir / corrupted_target.row_id / f"{corrupted_target.probe_index}.pcm"
    )
    deleted_path.unlink()
    corrupted_path.write_bytes(b"\x00\x01corrupted-bytes")

    with pytest.raises(render_stage.RenderResumeIndexIntegrityError) as exc_info:
        render_stage.run_render_stage(campaign, subset, stage="c1")

    failing_keys = {(rid, pidx) for rid, pidx, _detail in exc_info.value.failing_units}
    assert failing_keys == {
        (deleted_target.row_id, deleted_target.probe_index),
        (corrupted_target.row_id, corrupted_target.probe_index),
    }
    assert (intact_target.row_id, intact_target.probe_index) not in failing_keys

    fixture_valid_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "fixture_valid"
    ]
    assert len(fixture_valid_events) == 1  # unchanged: no second transition
    stop_events = [
        e.payload
        for e in campaign.ledger.entries
        if e.payload.get("kind") == "stop_event"
        and e.payload.get("reason") == "RENDER_RESUME_INDEX_INTEGRITY_MISMATCH"
    ]
    assert len(stop_events) == 1
    reported_keys = {(u["row_id"], u["probe_index"]) for u in stop_events[0]["units"]}
    assert reported_keys == failing_keys

    # recovery: restore byte-identical PCM off-ledger for both broken units
    # (what a manual re-render of each exact instance reproduces).
    deleted_path.write_bytes(bytes.fromhex(_render_pcm_hex(campaign, subset, deleted_target)))
    corrupted_path.write_bytes(bytes.fromhex(_render_pcm_hex(campaign, subset, corrupted_target)))

    recovered = render_stage.run_render_stage(campaign, subset, stage="c1")
    assert all(o.status == "skipped_resume" for o in recovered)
    fixture_valid_events_after = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "fixture_valid"
    ]
    assert len(fixture_valid_events_after) == 2


@pytest.mark.slow
def test_c1_render_resume_stale_fails_closed_on_missing_sidecar(tmp_path: Path) -> None:
    """Codex PR #345 round 5 finding S4 (adopted, category ③,
    `[UNDERSPEC-CAL-D79]`): the pre-fix `_validate_skipped_resume_outcomes()`
    only recomputed the PCM's own sha256 and compared it to the recorded
    ledger sha — it never read the `.sha256` sidecar at all, so an intact
    PCM whose sidecar was deleted/corrupted passed resume validation
    cleanly and only surfaced later as a completely different error
    (`StaleRenderError` at measure time, well past `fixture_valid`/
    `NOOP_ALREADY_COMPLETE`, with no resumable path back to render). With
    the shared `_verify_pcm_sidecar()` check in place, a missing sidecar on
    an otherwise byte-identical PCM now fails the completing invocation's
    resume validation directly."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")
    target = outcomes[0]
    pcm_path = campaign.renders_dir / target.row_id / f"{target.probe_index}.pcm"
    sha_path = pcm_path.with_suffix(".sha256")
    assert pcm_path.is_file()
    sha_path.unlink()

    with pytest.raises(render_stage.RenderResumeIndexIntegrityError) as exc_info:
        render_stage.run_render_stage(campaign, subset, stage="c1")
    assert len(exc_info.value.failing_units) == 1
    failing_row_id, failing_probe_index, failing_detail = exc_info.value.failing_units[0]
    assert (failing_row_id, failing_probe_index) == (target.row_id, target.probe_index)
    assert "sha256 sidecar missing" in failing_detail

    # transition still blocked: no second `fixture_valid`, and the mismatch
    # is recorded as a `stop_event` — same contract as the corrupted/missing
    # PCM cases above.
    fixture_valid_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "fixture_valid"
    ]
    assert len(fixture_valid_events) == 1
    stop_events = [
        e.payload
        for e in campaign.ledger.entries
        if e.payload.get("kind") == "stop_event"
        and e.payload.get("reason") == "RENDER_RESUME_INDEX_INTEGRITY_MISMATCH"
    ]
    assert len(stop_events) == 1

    # recovery: restore the sidecar (what a fresh render would write
    # alongside the byte-identical PCM) — no ledger mutation required.
    sha_path.write_text(hashlib.sha256(pcm_path.read_bytes()).hexdigest(), encoding="utf-8")
    recovered = render_stage.run_render_stage(campaign, subset, stage="c1")
    assert all(o.status == "skipped_resume" for o in recovered)
    fixture_valid_events_after = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "fixture_valid"
    ]
    assert len(fixture_valid_events_after) == 2


@pytest.mark.slow
def test_c1_render_resume_stale_fails_closed_on_wrong_sidecar(tmp_path: Path) -> None:
    """Same round 5 finding S4 coverage as the missing-sidecar test above,
    for the sidecar-content-mismatch branch of `_verify_pcm_sidecar()`: an
    intact PCM whose sidecar content no longer matches the PCM's own
    (still ledger-correct) sha256 must also fail the completing
    invocation's resume validation, not just pass silently through the
    old PCM-vs-ledger-sha-only check."""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    outcomes = render_stage.run_render_stage(campaign, subset, stage="c1")
    target = outcomes[0]
    pcm_path = campaign.renders_dir / target.row_id / f"{target.probe_index}.pcm"
    sha_path = pcm_path.with_suffix(".sha256")
    assert pcm_path.is_file()
    original_sidecar = sha_path.read_text(encoding="utf-8")
    sha_path.write_text("0" * 64, encoding="utf-8")

    with pytest.raises(render_stage.RenderResumeIndexIntegrityError) as exc_info:
        render_stage.run_render_stage(campaign, subset, stage="c1")
    assert len(exc_info.value.failing_units) == 1
    failing_row_id, failing_probe_index, failing_detail = exc_info.value.failing_units[0]
    assert (failing_row_id, failing_probe_index) == (target.row_id, target.probe_index)
    assert "does not match sidecar" in failing_detail

    fixture_valid_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "fixture_valid"
    ]
    assert len(fixture_valid_events) == 1

    # recovery: restore the original sidecar content.
    sha_path.write_text(original_sidecar, encoding="utf-8")
    recovered = render_stage.run_render_stage(campaign, subset, stage="c1")
    assert all(o.status == "skipped_resume" for o in recovered)
    fixture_valid_events_after = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "fixture_valid"
    ]
    assert len(fixture_valid_events_after) == 2


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


@pytest.mark.slow
def test_c4_render_phase_valid_marker_skips_rehash_on_measure_only_slices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex PR #345 round 5 finding S3 (adopted, category ③,
    `[UNDERSPEC-CAL-D79]`): once C4 rendering is fully done but the paired
    measure sub-phase (`holdout_stage.render_and_measure_holdout()`) still
    has slices left, EVERY subsequent call re-enters `render_stage.
    run_render_stage(stage="c4", ...)`, which pre-fix re-ran its
    `completed_all` block (nothing left to render -> `completed_all=True`
    on every call) and re-read+re-hashed every already-rendered PCM from
    scratch — competing with measurement for the same `time_budget` instead
    of running once at the real render->measure transition.

    `_refuse_if_pre_unseal_holdout()` is stubbed out for the same reason
    `test_c4_f0_unusable_selected_candidate_through_real_holdout_path_
    closes_not_evaluable` above stubs it: `provenance.Ledger.check_leakage()`
    hard-requires the full canonical matrix row-id set, which a tiny
    per-test fixture can never satisfy, and is an orthogonal, already
    dedicated-tested concern."""
    subset = small_matrix_subset(12, family="APERIODICITY_GT")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    independent_candidate = next(
        c for c in candidates_for_meter(MeterId.M2_APERIODICITY) if "-B0-" in c.candidate_id
    )
    monkeypatch.setattr(render_stage, "_refuse_if_pre_unseal_holdout", lambda *a, **kw: None)

    # Complete the C4 render sub-phase up front (no `time_budget` -- this
    # call IS the render->measure transition: it validates every
    # `skipped_resume`/newly-rendered unit once and records the durable
    # `RENDER_PHASE_VALID_KIND` marker).
    render_stage.run_render_stage(campaign, subset, stage="c4")
    render_phase_valid_events = [
        e.payload
        for e in campaign.ledger.entries
        if e.payload.get("kind") == render_stage.RENDER_PHASE_VALID_KIND
    ]
    assert len(render_phase_valid_events) == 1
    assert render_phase_valid_events[0]["stage"] == "c4"

    # count invocations of the completing-invocation resume validator itself
    # (not `_verify_pcm_sidecar`, which `measure_stage._verify_and_load_
    # rendered_pcm()` also legitimately calls once per real measurement
    # dispatch -- that per-measurement read is unrelated to what S3 fixes:
    # the render validator's own full `skipped_resume` rescan re-running on
    # every slice).
    call_count = {"n": 0}
    orig_validate = render_stage._validate_skipped_resume_outcomes

    def _counting_validate(*args, **kwargs):
        call_count["n"] += 1
        return orig_validate(*args, **kwargs)

    monkeypatch.setattr(render_stage, "_validate_skipped_resume_outcomes", _counting_validate)

    candidates_by_family = {"APERIODICITY_GT": (independent_candidate,)}

    # Slice 1: render sub-phase re-enters `run_render_stage(stage="c4")`
    # (nothing pending -> `completed_all=True` immediately, marker already
    # present -> the validation scan is skipped entirely) and the whole
    # small budget goes to measurement dispatch.
    budget = TimeBudget.start_now(0.1)
    records1, status1 = holdout_stage.render_and_measure_holdout(
        campaign,
        subset,
        candidates_by_family=candidates_by_family,
        max_workers=1,
        time_budget=budget,
    )
    assert call_count["n"] == 0
    assert status1.completed_all is False  # more work remains after 0.05s

    # Slice 2: same contract -- no re-hash, real NEW measurement progress
    # under the same small per-slice budget (round 5 finding S2's counter
    # fix is what makes this progress visible/nonzero rather than an
    # over/under count).
    records2, status2 = holdout_stage.render_and_measure_holdout(
        campaign,
        subset,
        candidates_by_family=candidates_by_family,
        max_workers=1,
        time_budget=TimeBudget.start_now(0.1),
    )
    assert call_count["n"] == 0
    assert status2.instances_completed_this_run > 0
    assert len(records2["APERIODICITY_GT"]) > 0

    # sanity: render's own sub-phase status never reports new work on
    # either measure-only slice (everything was already rendered up front).
    assert status1.instances_remaining >= 0
    assert status2.instances_remaining >= 0

    # eventually finishing the stage (a generous, effectively-unbounded
    # budget) does not append a second marker.
    _records, status = holdout_stage.render_and_measure_holdout(
        campaign,
        subset,
        candidates_by_family=candidates_by_family,
        max_workers=1,
        time_budget=TimeBudget.start_now(3600.0),
    )
    assert status.completed_all is True
    assert call_count["n"] == 0
    render_phase_valid_events_after = [
        e.payload
        for e in campaign.ledger.entries
        if e.payload.get("kind") == render_stage.RENDER_PHASE_VALID_KIND
    ]
    assert len(render_phase_valid_events_after) == 1


def test_c4_leakage_error_carries_check_leakage_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#348 第 3 巡 P2: `_refuse_if_pre_unseal_holdout()` が `check_leakage()`
    の `reason` を捨てず例外へ載せることの回帰テスト。

    実 C4 経路（`run_render_stage(stage="c4")`）に、行入力だけを改変した
    matrix（`nuisance_tag` を書き換え、`row_id` は凍結時のまま）を渡すと
    `check_leakage()` は `SPLIT_VERIFICATION_ROW_MISMATCH` を返す。修正前は
    例外メッセージが `blocked_code=BLOCKED_LEAKAGE (control_excluded_count=0)`
    だけで、unseal 未実施との区別がつかなかった。

    canonical-row-coverage 検査（§7）を通すため、campaign は v1.2 WP2 の
    rehearsal 行列 **全体** の上に組み立て、`set_rehearsal_mode(True)` で
    `provenance.check_leakage()` 側の canonical matrix も同じ行集合に揃える。
    """
    from dataclasses import replace as dataclass_replace

    from voice_genesis.calibration.fixtures import matrix as fixture_matrix

    monkeypatch.setattr(fixture_matrix, "_REHEARSAL_MODE", True)
    rows = fixture_matrix.build_rehearsal_matrix()
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=rows)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    # 単一 CONFOUND 行の nuisance_tag だけを改変する（row_id は据え置き =
    # 「凍結時と同じ行だと名乗るが行入力が違う」D105 型の不一致）。
    mutated: list = []
    mutated_one = False
    for mr in rows:
        if not mutated_one and fixture_matrix.single_axis_nuisance_tag_axis(mr.row) is not None:
            mutated.append(
                dataclass_replace(mr, row=dataclass_replace(mr.row, nuisance_tag="context=other"))
            )
            mutated_one = True
            continue
        mutated.append(mr)
    assert mutated_one

    with pytest.raises(render_stage.RenderLeakageBlockedError) as excinfo:
        render_stage.run_render_stage(campaign, mutated, stage="c4")

    assert excinfo.value.reason == "SPLIT_VERIFICATION_ROW_MISMATCH"
    assert "SPLIT_VERIFICATION_ROW_MISMATCH" in str(excinfo.value)
    assert not campaign.renders_dir.exists() or not any(campaign.renders_dir.iterdir())
