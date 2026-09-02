"""frozen cost cap の復元 + `CapCounters` の campaign dir 永続化
（`cli.py` finding #1 対応: 「stages call the measurement pipeline without
CapCounters/CostCaps」）。

`cli.py` の全武装 stage はここを経由して:

- `cost_caps_from_manifest()` — 凍結 manifest の `frozen_design.cost_caps`
  （`c0_freeze.build_manifest()` が Gate 1 承認の `cost_caps` payload から
  埋め込んだ canonical 3 キー `compute`/`storage`/`budget`）から
  `cost_caps.CostCaps` を復元する。Gate 1 未承認時の manifest は
  `"ABSENT:GATE1_NOT_APPROVED"`（文字列 sentinel）を持つため、その場合や
  節自体が無い場合は `None` を返す（cap 値の要否判断そのものは行わない —
  値が無ければ強制もしない、という D1 `cost_caps.py` と同じ授権境界）。
- `load_cap_counters()`/`save_cap_counters()` — `<campaign_dir>/counters.json`
  へ `cost_caps.CapCounters` を atomic write（同一ディレクトリ内 tempfile →
  `os.replace`。`approvals.py` の hash refresh と同一パターン）で永続化し、
  次回のサブコマンド起動時に読み戻す。render/measure 各 unit の直後に
  都度上書きするため、stage 途中で cap 超過して fail-closed 終了しても、
  その unit までの消費が失われない。壊れた/型不正な `counters.json` は
  fail-closed でエラーにする（黙って 0 へリセットしない — cap バイパスを
  防ぐ）。

`[UNDERSPEC-CAL-D21]`（round 15 finding #3 `[UNDERSPEC-CAL-D31]` で改訂）:
上記は「`counters.json` を都度 atomic 上書きする単一 mutable ファイル」を
唯一の正本とした旧採用だったが、round 15 finding #3 はこれを覆す —
`counters.json` は checkout 外にも secret にも属さない**単なる mutable
キャッシュ**であり、append-only の ledger（entry_sha 連鎖付き）こそが
正本である。旧採用は「`compute` 次元は event payload から再導出できない」
ことを理由にしていたが、これは round 14 finding #2 (`[UNDERSPEC-CAL-D29]`)
が `render`/`meter_call` event へ `cpu_seconds` を追加したことで既に成立
しなくなっていた前提だった。本改訂は `cap_counters_from_ledger()` で
ledger から compute/storage/budget を再導出し、`reconcile_cap_counters()`
が「次元ごとの `max(persisted, ledger 由来)`」を実効値として `counters.json`
より**ledger を上位**に置く（ロールバックされた/失われた `counters.json`
に対する fail-closed 方向の束縛）。
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from voice_genesis.calibration.cost_caps import (
    BudgetAccountingUndeclaredError,
    CapCounters,
    CostCaps,
    cost_caps_from_mapping,
)
from voice_genesis.calibration.provenance import LedgerEntry

COUNTERS_FILENAME = "counters.json"


class CapStateError(RuntimeError):
    """`counters.json` の読み込みが壊れている場合の fail-closed error。"""


class CountersCorruptError(CapStateError):
    """round 15 finding #1 (`[UNDERSPEC-CAL-D31]`): 永続化された
    `counters.json` の値が finite/non-negative でない、または
    `storage_used` が非 bool int でない（暗黙の型変換で誤魔化さない）場合の
    distinct fail-closed error。`CapStateError` のサブクラスなので既存の
    `except CapStateError` 呼び出し元は変更なしで捕捉できる。呼び出し元
    （`cli.py`）はこの `CODE` をそのまま ledger `stop_event`/CLI 結果の
    `reason` として使う——dispatch を一切行わない（0 work unit 実行）。"""

    CODE = "COUNTERS_CORRUPT"


class CostCapExceededError(RuntimeError):
    """`cost_caps.check()` が超過を検出した際の fail-closed error
    （render_stage/measure_stage 共通 — 単一の型を両モジュールが import して
    使う。個別に定義すると `except` 側が両方を捕捉し損ねる恐れがあるため）。"""


class WorkerCpuSecondsInvalidError(ValueError):
    """round 14 finding #2: a fresh-process worker (`_render_worker.py`/
    `_measure_worker.py`) reported a missing/non-finite/negative
    ``cpu_seconds`` in its JSON result. Treated as a stale/invalid work
    unit — fail-closed, no compute charge, no ledger `render`/`meter_call`
    event for it (render_stage/measure_stage share this single type so a
    caller catching it does not need to know which stage raised it)."""


def validate_worker_cpu_seconds(value: object, *, context: str) -> float:
    """finding #2: `elapsed` (wall-clock) undercounts the compute cap Gate 1
    defines in CPU-seconds once fresh-process work runs concurrently
    (`--workers > 1`) — wall time for the parent does not sum the CPU time
    actually spent across parallel worker subprocesses. Each worker now
    reports its own ``cpu_seconds`` (``resource.getrusage`` RUSAGE_SELF +
    RUSAGE_CHILDREN, i.e. user+sys) in its JSON result; the parent charges
    that value to the compute counter instead of wall time. A missing/
    non-finite/negative value is a stale/invalid unit — fail closed rather
    than silently falling back to 0 or to wall time (either would reopen
    the same undercounting hole this fix closes)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorkerCpuSecondsInvalidError(
            f"{context}: worker reported non-numeric cpu_seconds={value!r}"
        )
    if not math.isfinite(value) or value < 0:
        raise WorkerCpuSecondsInvalidError(
            f"{context}: worker reported invalid cpu_seconds={value!r} "
            "(must be finite and >= 0)"
        )
    return float(value)


def counters_path(campaign_dir: Path) -> Path:
    return Path(campaign_dir) / COUNTERS_FILENAME


def cost_caps_from_manifest(manifest: Mapping[str, object]) -> CostCaps | None:
    """`manifest["frozen_design"]["cost_caps"]` から `CostCaps` を復元する。
    節が無い・mapping でない（例: Gate 1 未承認時の
    `"ABSENT:GATE1_NOT_APPROVED"` 文字列）なら `None`（cap 未凍結 — 値の要否
    判断は行わない、既存の設計判断）。

    round 13 finding #3: 節が mapping として**存在する**にもかかわらず
    `budget_accounting_mode` が欠落/閉語彙外なら、`None` へ丸めて黙って
    budget cap を non-binding 扱いにはしない——
    `cost_caps.BudgetAccountingUndeclaredError` をそのまま呼び出し側
    （`campaign/cli.py`）へ伝播させ、dispatch を fail-closed で拒否させる。
    それ以外の型不正（compute/storage/budget 欠落等）は既存どおり `None` に
    丸める。"""
    frozen_design = manifest.get("frozen_design")
    if not isinstance(frozen_design, Mapping):
        return None
    raw = frozen_design.get("cost_caps")
    if not isinstance(raw, Mapping):
        return None
    try:
        return cost_caps_from_mapping(dict(raw))
    except BudgetAccountingUndeclaredError:
        raise
    except (KeyError, TypeError, ValueError):
        return None


def _validate_finite_nonneg_field(value: object, *, field: str, path: Path) -> float:
    """round 15 finding #1: `compute_used`/`budget_used` must be a real
    (non-bool) number, finite, and >= 0. No silent coercion of strings —
    `isinstance` only."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CountersCorruptError(
            f"{path}: {field} must be a non-bool number, got {value!r} "
            f"({type(value).__name__})"
        )
    if not math.isfinite(value) or value < 0:
        raise CountersCorruptError(
            f"{path}: {field} must be finite and >= 0, got {value!r}"
        )
    return float(value)


def _validate_storage_used_field(value: object, *, path: Path) -> int:
    """round 15 finding #1: `storage_used` must be a non-bool `int` exactly
    (no `int(x)` coercion of a float/str — that would silently accept
    e.g. `5.9` truncated to `5`)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise CountersCorruptError(
            f"{path}: storage_used must be a non-bool int, got {value!r} "
            f"({type(value).__name__})"
        )
    if value < 0:
        raise CountersCorruptError(f"{path}: storage_used must be >= 0, got {value!r}")
    return value


def load_cap_counters(campaign_dir: Path) -> CapCounters:
    """`<campaign_dir>/counters.json` を読み戻す。未作成なら 0 の
    `CapCounters()`（新規 campaign の初回起動）。壊れた JSON・型不正・
    非 finite・負値・`storage_used` への bool 混入は `CountersCorruptError`
    （`CapStateError` のサブクラス。fail-closed — 黙って 0 に戻す/丸めると
    cap を実質バイパスできてしまうため、読み込み不能は明示的な拒否とする。
    round 15 finding #1）。"""
    path = counters_path(campaign_dir)
    if not path.is_file():
        return CapCounters()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CountersCorruptError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise CountersCorruptError(f"{path} must contain a JSON object")
    for required in ("compute_used", "storage_used", "budget_used"):
        if required not in data:
            raise CountersCorruptError(f"{path} missing required key {required!r}")
    return CapCounters(
        compute_used=_validate_finite_nonneg_field(
            data["compute_used"], field="compute_used", path=path
        ),
        storage_used=_validate_storage_used_field(data["storage_used"], path=path),
        budget_used=_validate_finite_nonneg_field(
            data["budget_used"], field="budget_used", path=path
        ),
    )


def save_cap_counters(campaign_dir: Path, counters: CapCounters) -> None:
    """`counters.json` へ atomic write（同一ディレクトリ内 tempfile →
    `os.replace`。`approvals.py` の hash refresh と同一パターン）で永続化
    する。"""
    path = counters_path(campaign_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(counters.as_dict(), f, sort_keys=True)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _finite_nonneg_float(value: object) -> float:
    """round 15 finding #3: best-effort extraction of a compute/CPU field
    from a *ledger* event. Unlike `counters.json` (finding #1: a
    user/operator-writable cache, rejected outright on any corruption), the
    ledger is the trusted append-only provenance record this whole
    reconstruction leans on — an unparsable/invalid field on one event
    contributes 0 rather than aborting reconstruction of every other event."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    if not math.isfinite(value) or value < 0:
        return 0.0
    return float(value)


def _finite_nonneg_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    if value < 0:
        return 0
    return value


def cap_counters_from_ledger(
    ledger_entries: Sequence[LedgerEntry], cost_caps: CostCaps | None
) -> CapCounters:
    """round 15 finding #3 (`[UNDERSPEC-CAL-D31]`): recompute
    `compute_used`/`storage_used`/`budget_used` purely from the append-only
    ledger — the authoritative record; `counters.json` is a derived cache
    (see module docstring).

    - `render` events (`render_stage.run_render_stage`): each event is 1
      completed render work unit. `cpu_seconds` (round 14 finding #2,
      `[UNDERSPEC-CAL-D29]`) and `pcm_bytes` (round 15 finding #3, newly
      recorded alongside it) are summed 1:1 per event.
    - `meter_call` events (`measure_stage.run_measurement_for_instance`):
      **6 ledger records per (row_id, probe_index, candidate_id) work
      unit** (within3 + fresh3). `cpu_seconds` is the *same per-work-unit
      aggregate value* repeated on all 6 records (see that function's
      docstring) — summing it naively would 6x overcount, so only the
      first record seen per `(row_id, probe_index, candidate_id)` key
      counts toward `compute`. round 16 finding #3 (`[UNDERSPEC-CAL-D35]`):
      that first record's `within_cpu_seconds` (the within-process portion
      of `cpu_seconds`, recorded purely as informational provenance since
      round 16) is then subtracted back out, because
      `measure_stage.run_measurement_for_instance` no longer charges it to
      `compute_used` either (it is already charged once, via `cli.py`
      `main()`'s parent RUSAGE_SELF `stage_summary` charge) — reconstruction
      must mirror that or it would diverge from the persisted cache. A
      `meter_call` event that predates round 16 and carries no
      `within_cpu_seconds` field subtracts `0.0` (`_finite_nonneg_float`
      on a missing field), so the whole legacy `cpu_seconds` counts toward
      `compute` — an overcount rather than an undercount for old events,
      matching this module's fail-closed direction (see
      `reconcile_cap_counters` docstring). `storage_bytes` (round 15
      finding #3, newly recorded) is each record's own individual
      serialized size — genuinely additive per record, so it is summed
      without dedup.
    - `stage_summary` events (round 15 finding #5, `[UNDERSPEC-CAL-D31]`):
      the CLI dispatch path's own parent-side CPU for the whole stage,
      not captured by either of the above (matrix build, ledger/JSON I/O,
      hashing, subprocess orchestration overhead) — summed 1:1 per event
      so that finding #3's reconstruction and finding #5's charging stay
      consistent (a lost `counters.json` does not silently drop this).
      round 17 finding #3 (`[UNDERSPEC-CAL-D39]`): "for the whole stage"
      means the **full** dispatch-start-to-dispatch-end parent CPU delta,
      including any pre-transition checkpoint delta(s)
      `_checkpoint_parent_cpu_before_transition()` charges to
      `cap_counters` mid-stage (`c3a`/`c3b`/`c4`/`close`) — `cli.py`
      `main()`'s `finally` block now writes that full delta to this field
      (previously it wrote only the post-checkpoint residual, which
      under-counted this reconstruction relative to the persisted cache
      for any stage with a mid-dispatch checkpoint; the persisted cache
      itself was already correct, since the checkpoint's own delta was
      separately charged to `cap_counters` in-memory when it ran).
    - `budget`: reconstructed as `budget_charge_per_work_unit() ×
      (completed render units + completed meter-call work units)` per the
      frozen `budget_accounting_mode` (round 13 finding #3). `None`
      `cost_caps` (Gate 1 not yet frozen with cost caps) reconstructs
      `budget_used=0.0` — informational only, nothing enforces it either.
    """
    compute = 0.0
    storage = 0
    render_units = 0
    meter_units = 0
    seen_meter_keys: set[tuple[object, object, object]] = set()
    for entry in ledger_entries:
        payload = entry.payload
        if not isinstance(payload, Mapping):
            continue
        kind = payload.get("kind")
        if kind == "render":
            compute += _finite_nonneg_float(payload.get("cpu_seconds"))
            storage += _finite_nonneg_int(payload.get("pcm_bytes"))
            render_units += 1
        elif kind == "meter_call":
            storage += _finite_nonneg_int(payload.get("storage_bytes"))
            key = (payload.get("row_id"), payload.get("probe_index"), payload.get("candidate_id"))
            if key in seen_meter_keys:
                continue
            seen_meter_keys.add(key)
            # round 16 finding #3 (`[UNDERSPEC-CAL-D35]`): exclude the
            # within-process portion — see the docstring above.
            fresh_cpu_seconds = _finite_nonneg_float(
                payload.get("cpu_seconds")
            ) - _finite_nonneg_float(payload.get("within_cpu_seconds"))
            if fresh_cpu_seconds < 0.0:
                fresh_cpu_seconds = 0.0
            compute += fresh_cpu_seconds
            meter_units += 1
        elif kind == "stage_summary":
            compute += _finite_nonneg_float(payload.get("parent_cpu_seconds"))
    budget = 0.0
    if cost_caps is not None:
        per_unit = cost_caps.budget_charge_per_work_unit()
        if per_unit:
            budget = per_unit * (render_units + meter_units)
    return CapCounters(compute_used=compute, storage_used=storage, budget_used=budget)


def reconcile_cap_counters(
    campaign_dir: Path,
    ledger_entries: Sequence[LedgerEntry],
    cost_caps: CostCaps | None,
) -> tuple[CapCounters, bool]:
    """round 15 finding #3 (`[UNDERSPEC-CAL-D31]`): bind the persisted
    `counters.json` cache against rollback/loss by treating the ledger as
    authoritative. Returns `(effective, reconstructed)`:

    - `counters.json` missing, ledger shows no derived usage at all (fresh
      campaign): `(CapCounters(), False)` — nothing to reconstruct.
    - `counters.json` missing, ledger shows derived usage (deleted/lost
      cache with real prior work): `(ledger-derived, True)` — the caller
      must log a `COUNTERS_RECONSTRUCTED` ledger event once and persist
      this value.
    - `counters.json` present: `(per-dimension max(persisted,
      ledger-derived), False)`. A *lower* persisted value never wins over
      the ledger (a rolled-back/stale cache must not undercount); a
      *higher* persisted value is kept as-is rather than treated as an
      error (fail-closed direction — this function never raises for a
      persisted/derived mismatch, only `load_cap_counters` raises, for
      structurally corrupt persisted data per finding #1).
    """
    derived = cap_counters_from_ledger(ledger_entries, cost_caps)
    path = counters_path(campaign_dir)
    if not path.is_file():
        has_derived_work = (
            derived.compute_used > 0.0 or derived.storage_used > 0 or derived.budget_used > 0.0
        )
        return derived, has_derived_work
    persisted = load_cap_counters(campaign_dir)
    effective = CapCounters(
        compute_used=max(persisted.compute_used, derived.compute_used),
        storage_used=max(persisted.storage_used, derived.storage_used),
        budget_used=max(persisted.budget_used, derived.budget_used),
    )
    return effective, False


__all__ = [
    "COUNTERS_FILENAME",
    "CapStateError",
    "CountersCorruptError",
    "CostCapExceededError",
    "WorkerCpuSecondsInvalidError",
    "validate_worker_cpu_seconds",
    "counters_path",
    "cost_caps_from_manifest",
    "load_cap_counters",
    "save_cap_counters",
    "cap_counters_from_ledger",
    "reconcile_cap_counters",
]
