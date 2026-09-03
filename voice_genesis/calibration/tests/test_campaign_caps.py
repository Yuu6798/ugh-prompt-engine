"""`campaign/caps.py` のユニットテスト（round 15 findings #1/#3/#5、
`[UNDERSPEC-CAL-D31]`）。

- finding #1: `load_cap_counters()` は永続化された `counters.json` の
  非 finite・負値・`storage_used` への bool 混入を `CountersCorruptError`
  で拒否する。
- finding #3: `cap_counters_from_ledger()`/`reconcile_cap_counters()` は
  ledger を正本として compute/storage/budget を再導出し、`counters.json`
  との次元ごと `max()` を実効値とする。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from voice_genesis.calibration.campaign.caps import (
    CapCounters,
    CostCapExceededError,
    CountersCorruptError,
    cap_counters_from_ledger,
    charge_worker_attempts_before_raising,
    counters_path,
    load_cap_counters,
    reconcile_cap_counters,
    save_cap_counters,
)
from voice_genesis.calibration.cost_caps import CostCaps
from voice_genesis.calibration.provenance import Ledger

# ---------------------------------------------------------------------------
# finding #1: corrupt persisted counters.json -> CountersCorruptError
# ---------------------------------------------------------------------------


def _write_raw_counters(campaign_dir: Path, data: dict) -> None:
    campaign_dir.mkdir(parents=True, exist_ok=True)
    counters_path(campaign_dir).write_text(json.dumps(data), encoding="utf-8")


def test_load_cap_counters_rejects_nan_compute_used(tmp_path: Path) -> None:
    _write_raw_counters(
        tmp_path, {"compute_used": math.nan, "storage_used": 0, "budget_used": 0.0}
    )
    try:
        load_cap_counters(tmp_path)
        raise AssertionError("expected CountersCorruptError")
    except CountersCorruptError as exc:
        assert exc.CODE == "COUNTERS_CORRUPT"


def test_load_cap_counters_rejects_negative_infinity_budget_used(tmp_path: Path) -> None:
    _write_raw_counters(
        tmp_path, {"compute_used": 0.0, "storage_used": 0, "budget_used": -math.inf}
    )
    try:
        load_cap_counters(tmp_path)
        raise AssertionError("expected CountersCorruptError")
    except CountersCorruptError:
        pass


def test_load_cap_counters_rejects_negative_compute_used(tmp_path: Path) -> None:
    _write_raw_counters(
        tmp_path, {"compute_used": -1.0, "storage_used": 0, "budget_used": 0.0}
    )
    try:
        load_cap_counters(tmp_path)
        raise AssertionError("expected CountersCorruptError")
    except CountersCorruptError:
        pass


def test_load_cap_counters_rejects_negative_storage_used(tmp_path: Path) -> None:
    _write_raw_counters(
        tmp_path, {"compute_used": 0.0, "storage_used": -5, "budget_used": 0.0}
    )
    try:
        load_cap_counters(tmp_path)
        raise AssertionError("expected CountersCorruptError")
    except CountersCorruptError:
        pass


def test_load_cap_counters_rejects_bool_storage_used(tmp_path: Path) -> None:
    """`storage_used: true` must not silently coerce to `1` via `int(True)`."""
    _write_raw_counters(
        tmp_path, {"compute_used": 0.0, "storage_used": True, "budget_used": 0.0}
    )
    try:
        load_cap_counters(tmp_path)
        raise AssertionError("expected CountersCorruptError")
    except CountersCorruptError:
        pass


def test_load_cap_counters_rejects_float_storage_used_no_coercion(tmp_path: Path) -> None:
    """`storage_used: 5.9` must not silently truncate via `int(5.9) == 5`."""
    _write_raw_counters(
        tmp_path, {"compute_used": 0.0, "storage_used": 5.9, "budget_used": 0.0}
    )
    try:
        load_cap_counters(tmp_path)
        raise AssertionError("expected CountersCorruptError")
    except CountersCorruptError:
        pass


def test_load_cap_counters_accepts_well_formed_counters(tmp_path: Path) -> None:
    _write_raw_counters(
        tmp_path, {"compute_used": 12.5, "storage_used": 100, "budget_used": 0.0}
    )
    counters = load_cap_counters(tmp_path)
    assert counters.compute_used == 12.5
    assert counters.storage_used == 100
    assert counters.budget_used == 0.0


def test_load_cap_counters_missing_file_returns_zero_counters(tmp_path: Path) -> None:
    counters = load_cap_counters(tmp_path)
    assert counters == CapCounters()


# ---------------------------------------------------------------------------
# finding #3: cap_counters_from_ledger() — ledger-derived reconstruction
# ---------------------------------------------------------------------------


def _fake_render_event(row_id: str, probe_index: int, *, cpu_seconds: float, pcm_bytes: int) -> dict:
    return {
        "kind": "render",
        "row_id": row_id,
        "family": "F0_CONTROL",
        "split": "calibration",
        "probe_index": probe_index,
        "sha256": "0" * 64,
        "stage": "c1",
        "wall_seconds": cpu_seconds,
        "cpu_seconds": cpu_seconds,
        "pcm_bytes": pcm_bytes,
    }


def _fake_meter_call_event(
    row_id: str,
    probe_index: int,
    candidate_id: str,
    repeat_kind: str,
    repeat_index: int,
    *,
    cpu_seconds: float,
    storage_bytes: int,
) -> dict:
    return {
        "kind": "meter_call",
        "row_id": row_id,
        "probe_index": probe_index,
        "candidate_id": candidate_id,
        "repeat_kind": repeat_kind,
        "repeat_index": repeat_index,
        "wall_seconds": cpu_seconds,
        "cpu_seconds": cpu_seconds,
        "storage_bytes": storage_bytes,
    }


def test_cap_counters_from_ledger_sums_render_events_1to1(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(_fake_render_event("r1", 0, cpu_seconds=1.5, pcm_bytes=1000))
    ledger.append(_fake_render_event("r1", 1, cpu_seconds=2.5, pcm_bytes=2000))
    derived = cap_counters_from_ledger(ledger.entries, None)
    assert derived.compute_used == 4.0
    assert derived.storage_used == 3000
    assert derived.budget_used == 0.0


def test_cap_counters_from_ledger_dedups_meter_call_cpu_seconds_per_work_unit(
    tmp_path: Path,
) -> None:
    """6 records (within3 + fresh3) for the same (row_id, probe_index,
    candidate_id) share the *same* per-work-unit `cpu_seconds` aggregate —
    summing naively would 6x overcount. `storage_bytes` is per-record and
    additive."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for i in range(3):
        ledger.append(
            _fake_meter_call_event(
                "r1", 0, "F0-B0-CURRENT", "within", i, cpu_seconds=6.0, storage_bytes=100
            )
        )
    for i in range(3):
        ledger.append(
            _fake_meter_call_event(
                "r1", 0, "F0-B0-CURRENT", "fresh", i, cpu_seconds=6.0, storage_bytes=120
            )
        )
    derived = cap_counters_from_ledger(ledger.entries, None)
    # compute: counted once for the whole work unit, not once per record.
    assert derived.compute_used == 6.0
    # storage: additive per record (3 * 100 + 3 * 120).
    assert derived.storage_used == 3 * 100 + 3 * 120


def _fake_meter_call_group_discarded_event(
    row_id: str, probe_index: int, candidate_id: str
) -> dict:
    return {
        "kind": "meter_call_group_discarded",
        "row_id": row_id,
        "probe_index": probe_index,
        "candidate_id": candidate_id,
        "discarded_repeat_keys": [["within", 0], ["within", 1]],
        "discarded_count": 2,
        "reason": "operator_discard_partial_group_after_interrupt",
        "stage": "c2",
    }


def test_cap_counters_from_ledger_charges_discarded_group_and_remeasure_separately(
    tmp_path: Path,
) -> None:
    """R1 reconstruction rule (design memo `design_runner_robustness.md`,
    `[UNDERSPEC-CAL-D79]`), applied to `cap_counters_from_ledger()` too: a
    `meter_call_group_discarded` event resets the per-key dedup epoch, so
    the killed-mid-append attempt's own `cpu_seconds` (charged from its
    surviving first record) and the subsequent full remeasurement's
    `cpu_seconds` are BOTH charged — "records before [a discard] stay in the
    ledger and are still charged" (memo R1), not silently absorbed into a
    single dedup key the way a same-epoch duplicate would be."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    # the killed attempt: only 2 of 6 records survived the kill, but each
    # already carries the FULL work-unit cpu_seconds aggregate (computed
    # before any of the 6 records were appended — see
    # `measure_stage.run_measurement_for_instance`).
    for i in range(2):
        ledger.append(
            _fake_meter_call_event(
                "r1", 0, "F0-B0-CURRENT", "within", i, cpu_seconds=6.0, storage_bytes=100
            )
        )
    ledger.append(_fake_meter_call_group_discarded_event("r1", 0, "F0-B0-CURRENT"))
    # the full remeasurement.
    for i in range(3):
        ledger.append(
            _fake_meter_call_event(
                "r1", 0, "F0-B0-CURRENT", "within", i, cpu_seconds=9.0, storage_bytes=110
            )
        )
    for i in range(3):
        ledger.append(
            _fake_meter_call_event(
                "r1", 0, "F0-B0-CURRENT", "fresh", i, cpu_seconds=9.0, storage_bytes=130
            )
        )
    derived = cap_counters_from_ledger(ledger.entries, None)
    # compute: the discarded attempt's 6.0 (from its first surviving record)
    # + the remeasurement's 9.0 (from ITS first record) — not just 9.0 alone
    # (which a naive un-reset dedup-by-key would produce).
    assert derived.compute_used == 6.0 + 9.0
    # storage: every record is additive regardless of epoch (unaffected by
    # the discard-reset rule — matches the pre-existing per-record summing).
    assert derived.storage_used == 2 * 100 + 3 * 110 + 3 * 130
    # the discard event itself carries no charge of its own.
    ledger_only_discard = Ledger(tmp_path / "discard_only.jsonl")
    ledger_only_discard.append(_fake_meter_call_group_discarded_event("r1", 0, "F0-B0-CURRENT"))
    assert cap_counters_from_ledger(ledger_only_discard.entries, None) == CapCounters(
        compute_used=0.0, storage_used=0, budget_used=0.0
    )


def test_cap_counters_from_ledger_includes_stage_summary_parent_cpu(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append({"kind": "stage_summary", "stage": "c1-fixtures", "parent_cpu_seconds": 0.75})
    ledger.append({"kind": "stage_summary", "stage": "c2-baseline", "parent_cpu_seconds": 0.25})
    derived = cap_counters_from_ledger(ledger.entries, None)
    assert derived.compute_used == 1.0


def test_cap_counters_from_ledger_budget_uses_frozen_accounting_mode(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(_fake_render_event("r1", 0, cpu_seconds=1.0, pcm_bytes=10))
    for i in range(3):
        ledger.append(
            _fake_meter_call_event(
                "r1", 0, "F0-B0-CURRENT", "within", i, cpu_seconds=1.0, storage_bytes=10
            )
        )
    for i in range(3):
        ledger.append(
            _fake_meter_call_event(
                "r1", 0, "F0-B0-CURRENT", "fresh", i, cpu_seconds=1.0, storage_bytes=10
            )
        )
    caps = CostCaps(
        compute=1000.0,
        storage=1000,
        budget=1000.0,
        budget_accounting_mode="per_unit_fixed",
        budget_unit_cost=2.0,
    )
    derived = cap_counters_from_ledger(ledger.entries, caps)
    # 1 render unit + 1 meter-call work unit = 2 units * 2.0/unit.
    assert derived.budget_used == 4.0


def test_cap_counters_from_ledger_ignores_malformed_fields(tmp_path: Path) -> None:
    """Malformed ledger fields contribute 0 rather than raising (the ledger
    is trusted provenance, unlike counters.json's finding #1 strictness).
    `canonical_json` itself already refuses NaN/Infinity at append time
    (`provenance.py`), so the malformed shapes exercised here are the ones
    that *can* reach the ledger: a non-numeric string and a negative int."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        {
            "kind": "render",
            "row_id": "r1",
            "probe_index": 0,
            "cpu_seconds": "not-a-number",
            "pcm_bytes": -5,
        }
    )
    derived = cap_counters_from_ledger(ledger.entries, None)
    assert derived.compute_used == 0.0
    assert derived.storage_used == 0


# ---------------------------------------------------------------------------
# finding #3: reconcile_cap_counters() — max(persisted, ledger-derived)
# ---------------------------------------------------------------------------


def test_reconcile_missing_counters_and_no_ledger_work_is_not_reconstructed(
    tmp_path: Path,
) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append({"kind": "fixture_valid", "instance_count": 0})
    effective, reconstructed = reconcile_cap_counters(tmp_path, ledger.entries, None)
    assert effective == CapCounters()
    assert reconstructed is False


def test_reconcile_missing_counters_with_ledger_work_reconstructs(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(_fake_render_event("r1", 0, cpu_seconds=3.0, pcm_bytes=500))
    ledger.append(_fake_render_event("r1", 1, cpu_seconds=2.0, pcm_bytes=500))
    assert not counters_path(tmp_path).is_file()

    effective, reconstructed = reconcile_cap_counters(tmp_path, ledger.entries, None)
    assert reconstructed is True
    assert effective.compute_used == 5.0
    assert effective.storage_used == 1000


def test_reconcile_stale_lower_persisted_counters_max_wins_toward_ledger(
    tmp_path: Path,
) -> None:
    """A rolled-back/stale `counters.json` that undercounts vs. the ledger
    must not win — the ledger-derived (higher) value must."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(_fake_render_event("r1", 0, cpu_seconds=10.0, pcm_bytes=10_000))
    save_cap_counters(tmp_path, CapCounters(compute_used=1.0, storage_used=1, budget_used=0.0))

    effective, reconstructed = reconcile_cap_counters(tmp_path, ledger.entries, None)
    assert reconstructed is False
    assert effective.compute_used == 10.0
    assert effective.storage_used == 10_000


def test_reconcile_persisted_higher_than_ledger_keeps_persisted_fail_closed(
    tmp_path: Path,
) -> None:
    """A persisted value the ledger alone cannot currently prove (e.g. a
    stop_event breach already recorded) is kept as-is, never errored on."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(_fake_render_event("r1", 0, cpu_seconds=1.0, pcm_bytes=10))
    save_cap_counters(
        tmp_path, CapCounters(compute_used=999_999.0, storage_used=0, budget_used=0.0)
    )

    effective, reconstructed = reconcile_cap_counters(tmp_path, ledger.entries, None)
    assert reconstructed is False
    assert effective.compute_used == 999_999.0
    # storage: ledger-derived (10) exceeds persisted (0) -> max wins there too.
    assert effective.storage_used == 10


def test_reconcile_propagates_counters_corrupt_error_for_malformed_persisted_file(
    tmp_path: Path,
) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    _write_raw_counters(
        tmp_path, {"compute_used": math.inf, "storage_used": 0, "budget_used": 0.0}
    )
    try:
        reconcile_cap_counters(tmp_path, ledger.entries, None)
        raise AssertionError("expected CountersCorruptError")
    except CountersCorruptError:
        pass


# ---------------------------------------------------------------------------
# round 25 (`[UNDERSPEC-CAL-D57]`): unified worker-attempt accounting --
# `charge_worker_attempts_before_raising()` charges a whole batch (every
# spawned attempt, success or failure) in one shot before re-raising the
# first failure, and `cap_counters_from_ledger()` reconstructs both
# `worker_failed` and the new `worker_attempts_discarded` event kinds.
# ---------------------------------------------------------------------------


def _fake_worker_attempts_discarded_event(
    stage: str, row_id: str, probe_index: int, *, cpu_seconds_list: list, budget_units: int = 1
) -> dict:
    return {
        "kind": "worker_attempts_discarded",
        "stage": stage,
        "row_id": row_id,
        "probe_index": probe_index,
        "discarded_success_attempts": [{"cpu_seconds": c} for c in cpu_seconds_list],
        "storage_bytes": 0,
        "budget_units": budget_units,
    }


def test_cap_counters_from_ledger_worker_attempts_discarded_budget_is_batch_level(
    tmp_path: Path,
) -> None:
    """round 26 ADOPT (3) (`[UNDERSPEC-CAL-D59]`): `compute` still sums every
    entry of `discarded_success_attempts` (per-attempt, unchanged), but
    `budget` comes from the event's own `budget_units` field (the whole
    batch's single work-unit charge, per the real
    `charge_worker_attempts_before_raising()` emitter) -- NOT the count of
    discarded entries. A standalone discarded event carrying 2 entries and
    `budget_units: 1` charges budget for 1 unit, not 2."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        _fake_worker_attempts_discarded_event(
            "measure", "r1", 0, cpu_seconds_list=[2.0, 3.0], budget_units=1
        )
    )
    caps = CostCaps(
        compute=1000.0,
        storage=1000,
        budget=1000.0,
        budget_accounting_mode="per_unit_fixed",
        budget_unit_cost=2.0,
    )
    derived = cap_counters_from_ledger(ledger.entries, caps)
    assert derived.compute_used == 5.0
    assert derived.storage_used == 0
    assert derived.budget_used == 2.0  # 1 batch unit * 2.0/unit, not 2 attempts.


def test_cap_counters_from_ledger_combines_worker_failed_and_discarded_charges_batch_once(
    tmp_path: Path,
) -> None:
    """A single failed-batch charge (1 `worker_failed` carrying the batch's
    `budget_units: 1` + 1 `worker_attempts_discarded` carrying 2 discarded
    successes with `budget_units: 0`, exactly as the real
    `charge_worker_attempts_before_raising()` emitter stamps them) must
    reconstruct to exactly 1 work unit of budget -- round 26 ADOPT (3)
    (`[UNDERSPEC-CAL-D59]`), superseding the round 25 shape where this same
    3-attempt batch reconstructed to 3 budget units."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        {
            "kind": "worker_failed",
            "stage": "measure",
            "row_id": "r1",
            "probe_index": 0,
            "candidate_id": "F0-B0-CURRENT",
            "failure_kind": "timeout",
            "cpu_seconds": 1.5,
            "storage_bytes": 0,
            "budget_units": 1,
        }
    )
    ledger.append(
        _fake_worker_attempts_discarded_event(
            "measure", "r1", 0, cpu_seconds_list=[2.0, 2.0], budget_units=0
        )
    )
    caps = CostCaps(
        compute=1000.0,
        storage=1000,
        budget=1000.0,
        budget_accounting_mode="per_unit_fixed",
        budget_unit_cost=5.0,
    )
    derived = cap_counters_from_ledger(ledger.entries, caps)
    assert derived.compute_used == pytest.approx(1.5 + 2.0 + 2.0)
    assert derived.budget_used == pytest.approx(1 * 5.0)


def test_cap_counters_from_ledger_worker_failed_legacy_missing_budget_units_defaults_to_one(
    tmp_path: Path,
) -> None:
    """A `worker_failed` event predating round 26 (no `budget_units` field
    at all) must not silently reconstruct to 0 budget -- it defaults to 1
    (the same fail-closed/overcount-for-legacy-events direction this module
    already uses for a `meter_call` missing `within_cpu_seconds`)."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        {
            "kind": "worker_failed",
            "stage": "measure",
            "row_id": "r1",
            "probe_index": 0,
            "candidate_id": "F0-B0-CURRENT",
            "failure_kind": "timeout",
            "cpu_seconds": 1.5,
            "storage_bytes": 0,
        }
    )
    caps = CostCaps(
        compute=1000.0,
        storage=1000,
        budget=1000.0,
        budget_accounting_mode="per_unit_fixed",
        budget_unit_cost=5.0,
    )
    derived = cap_counters_from_ledger(ledger.entries, caps)
    assert derived.budget_used == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# `charge_worker_attempts_before_raising()`
# ---------------------------------------------------------------------------


def test_charge_worker_attempts_before_raising_charges_batch_and_reraises_first_failure(
    tmp_path: Path,
) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    counters = CapCounters()
    caps = CostCaps(
        compute=1000.0,
        storage=1000,
        budget=1000.0,
        budget_accounting_mode="per_unit_fixed",
        budget_unit_cost=5.0,
    )
    first_cause = TimeoutError("attempt 2 timed out")
    second_cause = TimeoutError("attempt 4 timed out")

    try:
        charge_worker_attempts_before_raising(
            ledger,
            tmp_path,
            cap_counters=counters,
            cost_caps=caps,
            stage="measure",
            row_id="r1",
            probe_index=0,
            candidate_id="F0-B0-CURRENT",
            successes=[2.0, 2.0],
            failures=[("timeout", 1.5, first_cause), ("timeout", 1.0, second_cause)],
        )
        raise AssertionError("expected the first failure's cause to be re-raised")
    except TimeoutError as exc:
        assert exc is first_cause  # the FIRST failure, not the second

    worker_failed = [e.payload for e in ledger.entries if e.payload.get("kind") == "worker_failed"]
    assert len(worker_failed) == 2
    assert [w["cpu_seconds"] for w in worker_failed] == [1.5, 1.0]
    # round 26 ADOPT (3) (`[UNDERSPEC-CAL-D59]`): the batch's single budget
    # unit is on the `worker_attempts_discarded` event (appended first, since
    # `successes` is non-empty) -- both `worker_failed` events carry 0.
    assert [w["budget_units"] for w in worker_failed] == [0, 0]

    discarded = [
        e.payload for e in ledger.entries if e.payload.get("kind") == "worker_attempts_discarded"
    ]
    assert len(discarded) == 1
    assert discarded[0]["discarded_success_attempts"] == [
        {"cpu_seconds": 2.0},
        {"cpu_seconds": 2.0},
    ]
    assert discarded[0]["budget_units"] == 1

    # compute: 2 successes (2.0 each) + 2 failures (1.5, 1.0) = 6.5.
    assert counters.compute_used == pytest.approx(6.5)
    # budget: round 26 ADOPT (3) (`[UNDERSPEC-CAL-D59]`) -- 1 work unit for
    # the WHOLE batch (this one call = one attempted measurement invocation),
    # not 1 unit per attempt (4 attempts here would have been 4 * 5.0 under
    # the round 25 shape this supersedes).
    assert counters.budget_used == pytest.approx(1 * 5.0)
    assert counters.storage_used == 0

    # persisted immediately (finding #1 pattern).
    reloaded = load_cap_counters(tmp_path)
    assert reloaded.compute_used == pytest.approx(counters.compute_used)

    derived = cap_counters_from_ledger(ledger.entries, caps)
    assert derived.compute_used == pytest.approx(counters.compute_used)
    assert derived.budget_used == pytest.approx(counters.budget_used)


def test_charge_worker_attempts_before_raising_cap_breach_takes_priority(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    counters = CapCounters()
    tiny_caps = CostCaps(
        compute=1e-6, storage=1000, budget=1000.0, budget_accounting_mode="local_zero_cost"
    )
    cause = TimeoutError("boom")

    with pytest.raises(CostCapExceededError):
        charge_worker_attempts_before_raising(
            ledger,
            tmp_path,
            cap_counters=counters,
            cost_caps=tiny_caps,
            stage="render",
            row_id="r1",
            probe_index=0,
            candidate_id=None,
            successes=[2.0],
            failures=[("timeout", 1.0, cause)],
        )

    assert counters.compute_used == pytest.approx(3.0)
    stop_events = [e.payload for e in ledger.entries if e.payload.get("kind") == "stop_event"]
    assert len(stop_events) == 1
    assert stop_events[0]["reason"] == "COST_CAP_EXCEEDED"


def test_charge_worker_attempts_before_raising_requires_nonempty_failures(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    with pytest.raises(ValueError, match="failures must be non-empty"):
        charge_worker_attempts_before_raising(
            ledger,
            tmp_path,
            cap_counters=None,
            cost_caps=None,
            stage="measure",
            row_id="r1",
            probe_index=0,
            candidate_id=None,
            successes=[1.0],
            failures=[],
        )
    # nothing appended -- fail fast on programmer error, no partial charge.
    assert ledger.entries == ()
