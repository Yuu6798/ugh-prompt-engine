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

from voice_genesis.calibration.campaign.caps import (
    CapCounters,
    CountersCorruptError,
    cap_counters_from_ledger,
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
