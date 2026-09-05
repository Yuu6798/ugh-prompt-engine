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
from typing import NoReturn

from voice_genesis.calibration.cost_caps import (
    BudgetAccountingUndeclaredError,
    CapCounters,
    CostCaps,
    cost_caps_from_mapping,
)
from voice_genesis.calibration.cost_caps import check as cost_caps_check
from voice_genesis.calibration.provenance import Ledger, LedgerEntry

COUNTERS_FILENAME = "counters.json"

#: round 13 finding (adopted, category ③, `[UNDERSPEC-CAL-D79]`): number of
#: distinct `(repeat_kind, repeat_index)` `meter_call` records one
#: `(row_id, probe_index, candidate_id)` work unit's group is COMPLETE at —
#: mirrors `measure_stage.WITHIN_PROCESS_REPEATS + measure_stage.
#: FRESH_PROCESS_REPEATS` (3 + 3). Duplicated here as a literal, matching
#: `measure_stage.py`'s own local `WITHIN_PROCESS_REPEATS`/
#: `FRESH_PROCESS_REPEATS` literals, rather than imported, because
#: `measure_stage.py` imports FROM `caps.py` (see that module's import
#: block) — importing back would be circular.
_METER_REPEAT_KEY_COUNT = 6


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


#: round 24 ADOPT (1) (`[UNDERSPEC-CAL-D55]`): closed vocabulary for
#: `worker_failed.failure_kind` — the 3 post-spawn ways a fresh-process
#: worker (`_measure_worker.py`/`_render_worker.py`) can fail to deliver a
#: usable result, per the finding text (round 24 PR review).
WORKER_FAILURE_KIND_TIMEOUT = "timeout"
WORKER_FAILURE_KIND_NONZERO_EXIT = "nonzero_exit"
WORKER_FAILURE_KIND_MALFORMED_OUTPUT = "malformed_output"
WORKER_FAILURE_KINDS: frozenset[str] = frozenset(
    {WORKER_FAILURE_KIND_TIMEOUT, WORKER_FAILURE_KIND_NONZERO_EXIT, WORKER_FAILURE_KIND_MALFORMED_OUTPUT}
)


def reported_cpu_seconds_or_none(stdout: str | None) -> float | None:
    """round 24 ADOPT (1) (`[UNDERSPEC-CAL-D55]`): on a nonzero-exit worker,
    `subprocess.run(check=True, capture_output=True)` still attaches
    whatever the worker wrote to stdout before exiting onto
    `CalledProcessError.stdout` — best-effort extraction of a well-formed
    `cpu_seconds` from it, so the caller charges the worker's own reported
    figure instead of the coarser parent-observed `RUSAGE_CHILDREN` delta
    when one is actually available. `None` (not a fallback value) on any
    failure to parse — missing/empty stdout, invalid JSON, a non-object
    payload, or a missing/non-finite/negative `cpu_seconds` — so the caller
    unambiguously knows to fall back to the delta itself rather than
    silently charging 0."""
    if not stdout:
        return None
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, Mapping):
        return None
    try:
        return validate_worker_cpu_seconds(
            raw.get("cpu_seconds"), context="campaign.caps: nonzero-exit worker stdout"
        )
    except WorkerCpuSecondsInvalidError:
        return None


def charge_worker_failure(
    ledger: Ledger,
    campaign_dir: Path,
    *,
    cap_counters: CapCounters | None,
    cost_caps: CostCaps | None,
    stage: str,
    row_id: str,
    probe_index: int,
    candidate_id: str | None,
    failure_kind: str,
    compute: float,
    cause: BaseException,
    invocation_id: str | None = None,
) -> NoReturn:
    """round 24 ADOPT (1) (`[UNDERSPEC-CAL-D55]`): charge a post-spawn
    fresh-process worker failure (measure or render — `stage` distinguishes
    them) BEFORE the failure that caused it propagates, so retries of a
    crashing candidate/instance can no longer consume unbounded worker CPU
    uncharged (mirrors the existing round 23 ADOPT (2) `render_nondeterministic`
    charge-before-raise pattern for the analogous "attempted work must not be
    free" reasoning).

    Appends a `worker_failed` ledger event unconditionally (`cap_counters`
    absent or not — same posture as `render`/`render_nondeterministic`: the
    ledger is the durable record even when no in-memory counters are being
    tracked this run) carrying `failure_kind` and the charged `cpu_seconds`
    (`storage_bytes` is always 0 — a failed worker attempt never produces
    persisted output). When `cap_counters` is given: charges `compute` and
    one budget work unit (per the frozen `budget_accounting_mode`, storage
    0), persists immediately, then runs the cap check — a cap breach raises
    `CostCapExceededError` in preference to `cause` (same priority as every
    other charge-then-check call site in this package). Otherwise (or if no
    breach), re-raises `cause` unchanged so the caller sees exactly the
    original `subprocess.TimeoutExpired`/`CalledProcessError`/
    `JSONDecodeError` — this function never masks it, only charges before it
    propagates.

    round 25 (`[UNDERSPEC-CAL-D57]`): kept as a thin single-attempt
    convenience wrapper — delegates to `charge_worker_attempts_before_raising()`
    with an empty `successes` and this one `(failure_kind, compute, cause)`
    as its sole `failures` entry, so the two functions can never drift on
    the `worker_failed` event shape or the charge-then-persist-then-check
    sequencing."""
    charge_worker_attempts_before_raising(
        ledger,
        campaign_dir,
        cap_counters=cap_counters,
        cost_caps=cost_caps,
        stage=stage,
        row_id=row_id,
        probe_index=probe_index,
        candidate_id=candidate_id,
        successes=[],
        failures=[(failure_kind, compute, cause)],
        invocation_id=invocation_id,
    )


def charge_worker_attempts_before_raising(
    ledger: Ledger,
    campaign_dir: Path,
    *,
    cap_counters: CapCounters | None,
    cost_caps: CostCaps | None,
    stage: str,
    row_id: str,
    probe_index: int,
    candidate_id: str | None,
    successes: Sequence[float],
    failures: Sequence[tuple[str, float, BaseException]],
    invocation_id: str | None = None,
) -> NoReturn:
    """round 25 (`[UNDERSPEC-CAL-D57]`): unified worker-attempt accounting —
    every spawned attempt in one repeat/worker batch (measure's N
    fresh-process repeats, render's 2-worker pair) is charged exactly once,
    whatever its outcome, BEFORE the batch's first failure propagates.
    Supersedes the round 24 ADOPT (1) (`[UNDERSPEC-CAL-D55]`)
    `charge_worker_failure()` posture of charging (and raising) only the ONE
    attempt that happened to fail — that silently discarded every
    already-completed sibling attempt (a successful repeat that finished
    before the failing one, or, under `--workers > 1`, every other future
    the executor had already started) uncharged, a free retry loop by the
    same reasoning `charge_worker_failure` itself closed for the single-
    failure case.

    Callers collect the outcome of EVERY attempt in the batch first — never
    cancel a started one, never stop the batch early on its first failure —
    then pass:

    - `successes`: the reported `cpu_seconds` of each attempt that
      completed with a usable result but whose batch nonetheless failed (so
      that result itself is discarded — never turned into a `meter_call`/
      `render` event, per the existing resume/single-writer contract).
      Recorded as one `worker_attempts_discarded` ledger event carrying
      `discarded_success_attempts` (one `{"cpu_seconds": ...}` entry per
      discarded success, so `cap_counters_from_ledger()` can recover both
      the per-attempt compute and the per-attempt budget-unit count from the
      ledger alone) — appended only when `successes` is non-empty.
    - `failures`: `(failure_kind, compute, cause)` per failed attempt, in
      the order attempts were started — each charged via the same
      `worker_failed` ledger event shape `charge_worker_failure()` uses (one
      event per failed attempt, no batching; `cap_counters_from_ledger()`
      already sums these without dedup). Must be non-empty — a batch with no
      failure at all has nothing to charge-then-raise here; the ordinary
      all-succeeded path charges through its own normal (non-failure) route.

    Charging (`cap_counters.add()`: compute = the sum of every attempt's own
    charge across both `successes` and `failures`, storage always 0) happens
    once for the whole batch, after every ledger event above is appended,
    then persists and runs the cap check ONCE (a breach raises
    `CostCapExceededError` in preference to the original failure, same
    priority as every other charge-then-check call site in this package).
    Finally re-raises `failures[0][2]` — the first failed attempt in batch
    (i.e. start) order — unchanged, exactly as `charge_worker_failure` does
    for a single failure.

    round 26 ADOPT (3) (`[UNDERSPEC-CAL-D59]`) "Charge failed batches as one
    outer work unit": budget is charged **once per batch** (this one call =
    one attempted render/measurement invocation, the frozen definition of a
    work unit — `cost_caps.CostCaps.budget_charge_per_work_unit()` docstring,
    IMPLEMENTATION_MAP_v1.md §6.4), never once per individual attempt inside
    it. The round 25 (`[UNDERSPEC-CAL-D57]`) revision that introduced this
    function charged `budget_per_unit * (len(successes) + len(failures))` —
    under `budget_accounting_mode="per_unit_fixed"` a failed 3-attempt
    measure batch charged 3x the budget a same-shaped *successful* batch
    charges (1x, via the ordinary `cap_counters.add(budget=budget_per_unit)`
    call at each success call site), contradicting the frozen work-unit
    definition. CPU stays charged **per attempt** (`total_compute` below is
    unchanged, still the sum across every attempt) — only the budget
    dimension collapses to 1 unit for the whole batch. The 1-unit charge is
    recorded on exactly one ledger event (`budget_units: 1`; every other
    event in the batch carries `budget_units: 0`) so
    `cap_counters_from_ledger()` can recover the batch-level (not
    attempt-level) unit count purely from the ledger — a `(row_id,
    probe_index, candidate_id, stage)` key cannot be deduped the way
    `meter_call`'s resume-guaranteed-unique key can, because a caller that
    retries a fully-failed batch (no `meter_call`/`render` was ever
    persisted, so resume sees the instance as unstarted and reattempts it)
    legitimately produces a SECOND batch of `worker_failed`/
    `worker_attempts_discarded` events at that same key — each such retry is
    its own attempted work unit and must charge its own 1 budget unit, so
    grouping by key alone would under-count real retries.

    round 8 finding #2 (R8-2, category ③, `[UNDERSPEC-CAL-D79]`): `invocation_id`
    (the calling process's own, from `cli.py` `main()` — see
    `cap_counters_from_ledger()`'s module-level pairing-rule note) is stamped
    on both `worker_failed` and `worker_attempts_discarded` events, matching
    every other cap-accounting ledger event this package writes."""
    if not failures:
        raise ValueError(
            "campaign.caps.charge_worker_attempts_before_raising: "
            "failures must be non-empty"
        )
    # round 26 (`[UNDERSPEC-CAL-D59]`): exactly one event in this batch
    # carries the batch's single budget unit -- the `worker_attempts_discarded`
    # event when present (it already represents "the rest of the batch"
    # alongside the failures), otherwise the first `worker_failed` event.
    batch_budget_units = 1
    if successes:
        discarded_payload: dict[str, object] = {
            "kind": "worker_attempts_discarded",
            "stage": stage,
            "row_id": row_id,
            "probe_index": probe_index,
            "discarded_success_attempts": [
                {"cpu_seconds": cpu_seconds} for cpu_seconds in successes
            ],
            "storage_bytes": 0,
            "budget_units": batch_budget_units,
            "invocation_id": invocation_id,
        }
        batch_budget_units = 0
        if candidate_id is not None:
            discarded_payload["candidate_id"] = candidate_id
        ledger.append(discarded_payload)
    for failure_kind, compute, _cause in failures:
        failure_payload: dict[str, object] = {
            "kind": "worker_failed",
            "stage": stage,
            "row_id": row_id,
            "probe_index": probe_index,
            "failure_kind": failure_kind,
            "cpu_seconds": compute,
            "storage_bytes": 0,
            "budget_units": batch_budget_units,
            "invocation_id": invocation_id,
        }
        batch_budget_units = 0
        if candidate_id is not None:
            failure_payload["candidate_id"] = candidate_id
        ledger.append(failure_payload)

    first_cause = failures[0][2]
    if cap_counters is not None:
        total_compute = sum(successes) + sum(compute for _fk, compute, _c in failures)
        budget_per_unit = cost_caps.budget_charge_per_work_unit() if cost_caps is not None else 0.0
        # round 26 (`[UNDERSPEC-CAL-D59]`): 1 work unit for the whole batch,
        # not `len(successes) + len(failures)`.
        cap_counters.add(compute=total_compute, storage=0, budget=budget_per_unit)
        # Persist before the breach check (finding #1 pattern): the whole
        # batch's consumption is never lost even when this same batch trips
        # fail-closed below.
        save_cap_counters(campaign_dir, cap_counters)
        if cost_caps is not None:
            decision = cost_caps_check(cap_counters, cost_caps)
            if decision is not None:
                ledger.append({**decision.event_payload, "invocation_id": invocation_id})
                raise CostCapExceededError(decision.detail) from first_cause
    raise first_cause


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
    - `render_nondeterministic` events (`render_stage.render_instance`,
      round 23 ADOPT (2), `[UNDERSPEC-CAL-D52]`): each event is 1 *attempted*
      render work unit whose 2 fresh-process workers disagreed — counted
      toward `render_units` (so it contributes 1 work unit of `budget`, same
      as a completed `render`) and its `cpu_seconds` summed the same way;
      `storage_bytes` is always `0` on these events (no PCM is ever
      persisted on a mismatch).
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
    - `meter_call_group_discarded` events, `discarded_within_cpu_seconds`
      field (round 6 finding #3, adopted, category ③, `[UNDERSPEC-CAL-D79]`;
      **pairing rule superseded by round 8 finding #2 (R8-2), same category,
      `[UNDERSPEC-CAL-D79]`** — round 7 finding #1's `dispatch_epoch`/
      `last_meter_epoch` ledger-order heuristic is retired in favor of
      explicit invocation identity, see below): the round-16
      `within_cpu_seconds` subtraction above (on `meter_call`) assumes the
      within-process CPU it excludes is always recovered via a
      `stage_summary`/`slice_summary` event instead — but that assumption
      is false for a *discarded* partial group whose writing invocation
      never reached `cli.py` `main()`'s `finally` block at all (a hard
      kill — SIGKILL/OOM/power loss — is the only way that happens; see
      the invariant below), and the group's within-process CPU would
      otherwise be permanently unrecoverable (silently understating
      `compute_used` — a false-success path past the frozen compute cap).
      `measure_stage.run_measurement_for_instance` records that CPU on the
      `meter_call_group_discarded` event itself (`discarded_within_cpu_
      seconds` — the shared within-process CPU aggregate of the discarded
      group's surviving records; see `measure_stage._partial_group_
      within_cpu_seconds` docstring for why this is one shared value, not
      a per-record sum).

      **Exactly-once invariant, round 8 finding #2 (R8-2)**: `cli.py`
      `main()` generates one `invocation_id` (`uuid.uuid4().hex`) per
      process and stamps it on every ledger event that process appends
      which participates in cap accounting (`meter_call`, `render`,
      `slice_summary`, `stage_summary`, `meter_call_group_discarded`,
      `worker_attempts_discarded`/`worker_failed`, `stop_event`). Pairing
      rule: a discarded partial group's within CPU is charged on the
      discard event iff NO `stage_summary`/`slice_summary` carrying the
      SAME `invocation_id` as the partial group's own `meter_call` records
      exists anywhere in the ledger — otherwise that summary's
      `parent_cpu_seconds` (a `RUSAGE_SELF` delta on that SAME process)
      already covers it. This function tracks it in one forward pass:
      `invocation_ids_with_summary` accumulates every `stage_summary`/
      `slice_summary` event's `invocation_id` as the scan reaches it, and
      `last_meter_invocation[key]` records the `invocation_id` a key's
      `meter_call` records were written under (all 6 records of one work
      unit share one invocation — see `run_measurement_for_instance` — so
      the last-seen value is stable regardless of which of the 6 happens
      to update it last). At a `meter_call_group_discarded` event for
      `key`, `last_meter_invocation.pop(key, None)` both looks up and
      performs the round 6 dedup reset (mirrors `seen_meter_keys.discard
      (key)` on the same line): if that `invocation_id` is `None` (no
      `meter_call` history for this key at all — a ledger fragment) or is
      not present in `invocation_ids_with_summary`, charge; otherwise not.

      This directly replaces the round 7 `dispatch_epoch` ordering
      heuristic, which conflated "some invocation's summary has since
      closed" with "THIS group's writer's own summary closed it" — a
      SIGKILL'd writer (invocation A, no summary, ever) followed by an
      operator retry WITHOUT `--discard-partial-groups` (invocation B,
      raises `StaleMeasurementError` but still appends ITS OWN
      `stage_summary` in `main()`'s `finally`) advanced `dispatch_epoch`
      past A's epoch even though B's summary covers none of A's
      within-process CPU — a later discard (invocation C) would then have
      wrongly charged 0 under the round 7 rule. Because pairing now keys
      on invocation identity rather than ledger position, B's unrelated
      summary can never be mistaken for A's, and the discard still charges
      A's recovered CPU correctly. Every `stage_summary`/`slice_summary`
      with no `invocation_id` (pre-round-8 legacy shape — not a concern in
      production, see module docstring) is simply never added to
      `invocation_ids_with_summary`, so a discard whose key's own records
      also lack the field (`None`) always charges — the same overcount-
      safe, fail-closed direction this module already takes for every
      other legacy/partial-data fallback.

      **Round 12 finding (adopted, category ③, `[UNDERSPEC-CAL-D79]`) —
      generalized to a COMPLETE, never-discarded group**: the pairing rule
      above only recovers a killed writer's within-process CPU when an
      operator explicitly discards the partial group. A process killed
      right after appending the group's SIXTH (final) `meter_call` record
      — but before `cli.py` `main()`'s `finally` block ever runs — leaves a
      COMPLETE group in the ledger with no `meter_call_group_discarded`
      event at all (there is nothing partial to discard) and no
      `stage_summary`/`slice_summary` either (the process never reached
      `finally`). On resume, that group's 6 records already satisfy the
      work unit, so it is silently treated as done and no remeasurement,
      discard, or summary ever appends for it — its within-process CPU
      would otherwise be permanently lost, the same false-undercount this
      round 12 finding demonstrated on a *complete* group specifically
      (rather than the partial-and-discarded ones round 6/7/8 covered).

      **Exactly-once invariant (generalized to every `meter_call` group,
      complete or discarded)**: within-process CPU is charged from exactly
      one of two sources, never both — (1) a *discarded* group's own
      `meter_call_group_discarded` event, via its
      `discarded_within_cpu_seconds` field (the pairing rule above,
      unchanged), or (2) a group whose key is still present in the
      per-key writer-invocation map at the end of the ledger scan (i.e.
      never popped by a discard event for that key) is charged directly
      from its own first `meter_call` record's `within_cpu_seconds` in a
      deferred pass after the forward scan completes — deferred because
      the writer's own `stage_summary`/`slice_summary`, when it exists, is
      always appended to the ledger AFTER that writer's `meter_call`
      records, so the pairing test cannot be decided inline while still
      scanning those records. Source (2) is skipped (covered by the
      summary instead) iff that group's writer `invocation_id` appears in
      `invocation_ids_with_summary`. A discarded key is never visited by
      source (2) (discard pops it from the writer-invocation map), and a
      key visited by source (2) was by definition never discarded (still
      present in the map) — so the two sources partition every group's
      within-process CPU with no overlap. This subsumes the discard-path
      docstring's `discarded_within_cpu_seconds` field, which remains the
      charge for a *discarded partial* group specifically; it is not
      re-derived from source (2) for that same key, since the discard
      event's pop already removes it from the deferred pass entirely.

      **Round 13 finding (adopted, category ③, `[UNDERSPEC-CAL-D79]`) —
      deferred pass restricted to COMPLETE groups**: source (2) above, as
      originally written, charged ANY key still present in the writer-
      invocation map at scan end whose writer was never summarized —
      including a still-PARTIAL group (fewer than the expected 6
      `meter_call` records for its key) whose writer was hard-killed
      mid-write and has not yet been discarded. On `--discard-partial-
      groups` recovery, `cli.py main()` reconciles `cap_counters` from the
      ledger (this function) BEFORE `_discard_partial_group()` ever runs
      (the `meter_call_group_discarded` event does not exist in the ledger
      yet at that point), so this deferred pass already charged the
      partial group's within CPU once via source (2); `_discard_partial_
      group()` then charges the SAME within CPU to the SAME live counter
      again via `discarded_within_cpu_seconds` — a double charge that
      could trip a false `COST_CAP_EXCEEDED` on the recovery path.

      Source (2) is now gated on group completeness:
      `meter_group_repeat_keys[key]` — every distinct `(repeat_kind,
      repeat_index)` pair observed for that key anywhere in the ledger,
      reset alongside the same discard-time pop as `last_meter_invocation`/
      `meter_group_within_cpu` — must reach all `_METER_REPEAT_KEY_COUNT`
      (`WITHIN_PROCESS_REPEATS + FRESH_PROCESS_REPEATS` = 6) pairs before
      source (2) charges it. A still-partial, undiscarded group now
      contributes nothing to either source until it is discarded — it
      remains charged exclusively through its eventual discard event,
      which is the only path that can ever account for it (an undiscarded
      partial group is unusable and keeps the campaign fail-closed until
      discarded).

      **Invariant**: within CPU of a group from an unsummarized writer is
      charged exactly once — complete groups via the deferred pass
      (source (2)), partial groups via their discard event
      (`discarded_within_cpu_seconds`, via `_discard_partial_group`);
      summarized writers are covered by their own summary's
      `parent_cpu_seconds` in either case.
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
    - `parent_cpu_checkpoint` events (R7 P1 fix: `cli._checkpoint_parent_cpu_
      before_transition()`): a mid-dispatch checkpoint of a stage's own
      cumulative parent-side CPU since dispatch start, appended immediately
      before the cap check that gates a phase-transition event
      (`fixture_valid`/`baseline_audited`/`f0_selection_frozen`/
      `selection_frozen`/`holdout_unseal`/`holdout_executed_valid`/
      `campaign_closed`) — closing a gap where a process killed right after
      that transition event (but before its own eventual `stage_summary`/
      `slice_summary`, appended only in `cli.main()`'s `finally` block) left
      the checkpointed CPU durably charged in-memory to that invocation's
      `cap_counters` but recorded nowhere in the ledger, so a subsequent
      reconstruction from the ledger alone silently dropped it. Every
      current call site invokes the checkpoint function at most once per
      invocation (immediately before that invocation's single phase-
      transition append), so the event's own `parent_cpu_seconds` (the
      checkpoint's delta since dispatch start) already equals this
      invocation's *cumulative* parent CPU to that point — the same
      quantity a `stage_summary`/`slice_summary` would eventually record
      for a normally-completing dispatch.

      Same-identity dedup as the `meter_call_group_discarded`/`meter_call`
      pairing rule above: this reconstruction must never add a checkpoint's
      CPU on top of that SAME invocation's own `stage_summary`/
      `slice_summary` (whose `parent_cpu_seconds` already covers the full
      dispatch, checkpoint included — see `main()`'s `finally` block
      `_checkpoint_before_summary()`) — that would double count. Tracked in
      a deferred pass mirroring the `meter_group_within_cpu` one above: the
      *maximum* `parent_cpu_seconds` seen per `invocation_id` (never a sum —
      each event already carries the cumulative-since-dispatch-start value,
      not a delta to add to previous checkpoints) is charged once per
      invocation, but only for an `invocation_id` that never appears in
      `invocation_ids_with_summary` by the end of the scan (a `stage_summary`/
      `slice_summary` is always appended strictly after any checkpoint from
      the same writer, so the deferred pass — run after the full forward
      scan — sees the complete picture). A missing/`None` `invocation_id`
      is always charged (fail-closed default, matching every other
      identity-keyed fallback in this module). `storage`/`budget` are
      unaffected — a checkpoint is not a completed work unit.

      **R9 fix (PR #346 round 9, overlap with the `meter_group_within_cpu`
      deferred pass above)**: `parent_cpu_checkpoint.parent_cpu_seconds` is a
      `RUSAGE_SELF` delta on the SAME process/invocation as its own
      within-process `meter_call` work — so for an unsummarized invocation
      that both wrote a checkpoint AND completed a `meter_call` group, the
      checkpoint's cumulative value already includes that group's
      `within_cpu_seconds` whenever the group finished writing (in ledger
      order) BEFORE the checkpoint was recorded; the source-(2) deferred
      pass above must then skip that group, or the same within CPU is
      charged twice (checkpoint sum + `meter_group_within_cpu`) — a false
      `COST_CAP_EXCEEDED`. A group that finishes writing AFTER the
      checkpoint's `entry.seq`, however, cannot possibly be reflected in
      that earlier checkpoint's snapshot, so it must still be charged (the
      opposite bug — silently dropping it — would be a false pass through
      the frozen cap). The rule is therefore ledger-order, not presence:
      compare the completing group's own last `meter_call` record's
      `entry.seq` (`_meter_group_last_seq`, tracked alongside
      `meter_group_repeat_keys`/`meter_group_within_cpu`, reset on the same
      discard-time pop) against that invocation's checkpoint `entry.seq`
      (`_checkpoint_seq_by_invocation`, tracked alongside
      `checkpoint_cpu_by_invocation`'s own max-value update, since CPU is
      monotonic non-decreasing within one invocation so the max-value
      checkpoint is also the chronologically-latest one) — skip iff the
      group's seq is strictly less than the checkpoint's seq, charge
      otherwise. An invocation with no checkpoint at all is unaffected
      (falls through to the pre-R9 behaviour unchanged).
    - `worker_failed` events (round 24 ADOPT (1), `[UNDERSPEC-CAL-D55]`,
      `caps.charge_worker_failure()`): each event is 1 charged *attempt* at
      a fresh-process worker call (measure or render) that failed post-spawn
      (timeout / nonzero exit / malformed JSON) — unlike `meter_call`, there
      is no 6-records-per-work-unit aggregation to dedup here, each event's
      `cpu_seconds` is independently one attempt's own compute, so all of
      them sum without dedup (repeated retries of a crashing
      candidate/instance each contribute their own compute charge, by
      design — that is the point of the fix). `storage_bytes` is always `0`
      on these events (no output is ever persisted on a failed attempt).

      round 26 ADOPT (3) (`[UNDERSPEC-CAL-D59]`): unlike `cpu_seconds`,
      `budget` is NOT 1 unit per `worker_failed`/`worker_attempts_discarded`
      event — `charge_worker_attempts_before_raising()` stamps exactly one
      event per batch with `budget_units: 1` (every other event in that same
      batch carries `budget_units: 0`), so the batch's single work-unit
      charge is recovered by summing `budget_units` across both event kinds
      rather than counting events/attempts. A `budget_units` field missing
      (an event predating this revision) defaults to `1` — an overcount
      relative to the true frozen-work-unit semantics for that specific
      legacy event, but it matches this module's existing fail-closed
      direction for reconstructing pre-revision ledger data (same posture as
      the `within_cpu_seconds`-missing fallback above).
    - `worker_attempts_discarded` events (round 25, `[UNDERSPEC-CAL-D57]`,
      `caps.charge_worker_attempts_before_raising()`): recorded alongside a
      `worker_failed` event whenever a batch of worker attempts (measure's N
      fresh-process repeats, render's 2-worker pair) had at least one
      attempt succeed but the batch as a whole still failed (a sibling
      attempt in the same batch failed) — the sibling successes' results
      are discarded (never a `meter_call`/`render` event) but the compute
      they already spent is not free. Each entry of
      `discarded_success_attempts` is its own charged attempt for `compute`
      (own `cpu_seconds`, summed without dedup — the same per-attempt
      granularity `worker_failed` uses, just carried as a list on 1 event
      instead of 1 event each, since the discarded attempts have no
      `failure_kind` to key separate events on). `storage_bytes` is always
      `0` (a discarded attempt's result is never persisted). `budget` comes
      from this event's own `budget_units` field exactly like `worker_failed`
      above (see round 26 note there) — NOT from the count of
      `discarded_success_attempts` entries.
    - `budget`: reconstructed as `budget_charge_per_work_unit() ×
      (completed render units + completed meter-call work units + Σ
      `budget_units` across `worker_failed`/`worker_attempts_discarded`
      events)` per the frozen `budget_accounting_mode` (round 13 finding #3;
      round 24 ADOPT (1) extended the unit count to include `worker_failed`;
      round 25 extended it again to include `worker_attempts_discarded`;
      round 26 ADOPT (3) (`[UNDERSPEC-CAL-D59]`) changed the per-attempt
      `worker_failed`/`worker_attempts_discarded` unit count to a
      per-*batch* `budget_units` sum — see the round 26 note above). `None`
      `cost_caps` (Gate 1 not yet frozen with cost caps) reconstructs
      `budget_used=0.0` — informational only, nothing enforces it either.
    """
    compute = 0.0
    storage = 0
    render_units = 0
    meter_units = 0
    worker_batch_budget_units = 0
    seen_meter_keys: set[tuple[object, object, object]] = set()
    # round 8 finding #2 (R8-2, `[UNDERSPEC-CAL-D79]`): explicit invocation
    # identity replaces the round 7 `dispatch_epoch` ordering heuristic (see
    # the docstring paragraph above) — `invocation_ids_with_summary`
    # accumulates every `stage_summary`/`slice_summary` event's
    # `invocation_id` seen so far; `last_meter_invocation` records, per
    # `meter_call` key, the `invocation_id` its records were written under,
    # so a later `meter_call_group_discarded` for that key can tell whether
    # THAT SPECIFIC writer's own summary (not merely some intervening one)
    # has appeared anywhere in the ledger.
    invocation_ids_with_summary: set[object] = set()
    last_meter_invocation: dict[tuple[object, object, object], object] = {}
    # round 12 finding (adopted, category ③, `[UNDERSPEC-CAL-D79]`): the
    # round-16 `within_cpu_seconds` subtraction on `meter_call` (below)
    # assumes a `stage_summary`/`slice_summary` from the SAME writer
    # invocation always shows up later in the ledger to recover it — true
    # for the round 8 discard path (which reads the aggregate off the
    # discard event's own `discarded_within_cpu_seconds` field instead) but
    # NOT for a group that completes all 6 records and is never discarded:
    # a writer killed right after appending its 6th record (before
    # `cli.py` `main()`'s `finally` ever runs) leaves a COMPLETE group with
    # no discard event and no summary — its within-process CPU would
    # otherwise be silently lost. `meter_group_within_cpu` records each
    # key's shared within-process aggregate from its own first record (the
    # same "one shared value per group" reading `discarded_within_cpu_
    # seconds` captures for the discard path — see `measure_stage._
    # partial_group_within_cpu_seconds`), so it can be charged after the
    # scan for any key still active (i.e. not popped by a
    # `meter_call_group_discarded` reset) whose writer never got summarized
    # — round 13 finding (below): gated to COMPLETE groups only, via
    # `meter_group_repeat_keys`.
    meter_group_within_cpu: dict[tuple[object, object, object], float] = {}
    # round 13 finding (adopted, category ③, `[UNDERSPEC-CAL-D79]`): every
    # distinct `(repeat_kind, repeat_index)` pair observed for a key,
    # anywhere in the ledger — used by the deferred pass below to tell a
    # COMPLETE group (all `_METER_REPEAT_KEY_COUNT` pairs present) from a
    # still-PARTIAL one (see the docstring's round 13 paragraph for why this
    # gate exists). Reset alongside the same discard-time pop as
    # `last_meter_invocation`/`meter_group_within_cpu` so a remeasurement
    # after a discard starts its own fresh count.
    meter_group_repeat_keys: dict[tuple[object, object, object], set[tuple[object, object]]] = {}
    # R9 fix: the `entry.seq` of the most recent `meter_call` record seen for
    # each key (every record, not just the first — mirrors
    # `meter_group_repeat_keys`'s own per-record tracking) — used by the
    # deferred pass below to tell whether a COMPLETE group finished writing
    # before or after that group's writer invocation's own
    # `parent_cpu_checkpoint` (see the docstring's R9 paragraph). Reset
    # alongside the same discard-time pop as `meter_group_within_cpu`/
    # `meter_group_repeat_keys` so a remeasurement after a discard starts its
    # own fresh count.
    meter_group_last_seq: dict[tuple[object, object, object], int] = {}
    # R7 P1 fix: per-`invocation_id` maximum `parent_cpu_checkpoint.
    # parent_cpu_seconds` seen so far — see the docstring paragraph above.
    # Charged in a deferred pass (mirrors `meter_group_within_cpu`) once the
    # full scan has determined which invocations ever got summarized.
    checkpoint_cpu_by_invocation: dict[object, float] = {}
    # R9 fix: the `entry.seq` of the checkpoint event holding each
    # invocation's charged (max-value) `parent_cpu_seconds` — updated
    # alongside `checkpoint_cpu_by_invocation` itself (same update
    # condition), since CPU is monotonic non-decreasing within one
    # invocation's own dispatch, so the max-value checkpoint is also the
    # chronologically-latest one and its `entry.seq` is the correct
    # "everything before this ledger position is already covered" boundary.
    checkpoint_seq_by_invocation: dict[object, int] = {}
    for entry in ledger_entries:
        payload = entry.payload
        if not isinstance(payload, Mapping):
            continue
        kind = payload.get("kind")
        if kind == "render":
            compute += _finite_nonneg_float(payload.get("cpu_seconds"))
            storage += _finite_nonneg_int(payload.get("pcm_bytes"))
            render_units += 1
        elif kind == "render_nondeterministic":
            # round 23 ADOPT (2) (`[UNDERSPEC-CAL-D52]`): the attempted work
            # both fresh-process workers already spent before their outputs
            # were found to disagree — charged the same way a completed
            # `render` event is (1 work unit toward `budget`), just with
            # `storage_bytes` always 0 (no PCM is ever persisted on a
            # mismatch, unlike `render`'s `pcm_bytes`).
            compute += _finite_nonneg_float(payload.get("cpu_seconds"))
            storage += _finite_nonneg_int(payload.get("storage_bytes"))
            render_units += 1
        elif kind == "meter_call_group_discarded":
            # R1 (design memo `design_runner_robustness.md`,
            # `[UNDERSPEC-CAL-D79]`) reconstruction rule, applied here too
            # ("... incl. ... caps counters"): a discard event resets the
            # dedup epoch for its (row_id, probe_index, candidate_id) key,
            # so the *next* `meter_call` record for that key after this
            # event is treated as the first-of-a-new-epoch again — the
            # discarded (killed-mid-append) attempt's own `cpu_seconds`
            # (already computed as the full per-work-unit aggregate before
            # any of its records were appended — see
            # `measure_stage.run_measurement_for_instance`) is charged from
            # its own surviving first record exactly once, and the
            # subsequent full remeasurement is charged again from its own
            # first record — matching "records before [a discard] stay in
            # the ledger and are still charged".
            key = (payload.get("row_id"), payload.get("probe_index"), payload.get("candidate_id"))
            seen_meter_keys.discard(key)
            # round 13 finding: reset this key's completeness tracking too —
            # mirrors the `last_meter_invocation`/`meter_group_within_cpu`
            # reset below, so a remeasurement after this discard starts its
            # own fresh count of distinct repeat keys.
            meter_group_repeat_keys.pop(key, None)
            # R9 fix: reset this key's last-seen seq too — mirrors the resets
            # above so a remeasurement after this discard starts its own
            # fresh tracking (the discarded attempt's seq must never leak
            # into the remeasurement's completeness/seq comparison).
            meter_group_last_seq.pop(key, None)
            # round 8 finding #2 (R8-2, `[UNDERSPEC-CAL-D79]`, supersedes
            # round 7 finding #1): `discarded_within_cpu_seconds` is charged
            # iff no `stage_summary`/`slice_summary` carrying the SAME
            # `invocation_id` as this key's writer exists in the ledger —
            # see the dedicated docstring paragraph above for why identity
            # (not ledger-position "epoch" ordering) is the correct
            # exactly-once test.
            record_invocation_id = last_meter_invocation.pop(key, None)
            if record_invocation_id is None or record_invocation_id not in invocation_ids_with_summary:
                compute += _finite_nonneg_float(payload.get("discarded_within_cpu_seconds"))
            # else: a `stage_summary`/`slice_summary` from the SAME
            # invocation that wrote this key's records already closed it and
            # its `parent_cpu_seconds` already covers this within-process
            # CPU (charged below); charging it again here would double
            # count.
        elif kind == "meter_call":
            storage += _finite_nonneg_int(payload.get("storage_bytes"))
            key = (payload.get("row_id"), payload.get("probe_index"), payload.get("candidate_id"))
            # round 8 finding #2 (R8-2): record which invocation wrote this
            # key's records, regardless of dedup state, so a discard later
            # reset to this same key can look it up even if this particular
            # record is itself a 2nd..6th (deduped) one. All 6 records of one
            # work unit share a single `invocation_id` (one
            # `run_measurement_for_instance` call writes all of them), so the
            # last-seen value is stable.
            last_meter_invocation[key] = payload.get("invocation_id")
            # round 13 finding: track this key's completeness regardless of
            # dedup state — every one of a group's up-to-6 records carries
            # its own distinct `(repeat_kind, repeat_index)`, so this must
            # run for every record seen, not just the first (the dedup
            # `continue` below is only about the shared per-work-unit
            # `cpu_seconds`/`within_cpu_seconds` aggregate, which the first
            # record already carries in full).
            meter_group_repeat_keys.setdefault(key, set()).add(
                (payload.get("repeat_kind"), payload.get("repeat_index"))
            )
            # R9 fix: track this key's most-recent record position too, for
            # every record (not just the first) — same reasoning as
            # `meter_group_repeat_keys` just above: the group's completion
            # point in ledger order is whichever record is seen last, which
            # is only known once the scan has passed it. `entry.seq` is a
            # `LedgerEntry` dataclass field (always an `int`), unlike the
            # loosely-typed `payload` fields this module otherwise guards
            # with `isinstance`.
            meter_group_last_seq[key] = entry.seq
            if key in seen_meter_keys:
                continue
            seen_meter_keys.add(key)
            # round 16 finding #3 (`[UNDERSPEC-CAL-D35]`): exclude the
            # within-process portion — see the docstring above.
            within_cpu_seconds = _finite_nonneg_float(payload.get("within_cpu_seconds"))
            fresh_cpu_seconds = _finite_nonneg_float(payload.get("cpu_seconds")) - within_cpu_seconds
            if fresh_cpu_seconds < 0.0:
                fresh_cpu_seconds = 0.0
            compute += fresh_cpu_seconds
            meter_units += 1
            # round 12 finding (`[UNDERSPEC-CAL-D79]`): capture this key's
            # shared within-process aggregate off its own first record — the
            # post-scan pass below charges it iff this key's writer never
            # gets a discard event (which would pop it and charge via
            # `discarded_within_cpu_seconds` instead) and never gets
            # summarized.
            meter_group_within_cpu[key] = within_cpu_seconds
        elif kind == "stage_summary":
            compute += _finite_nonneg_float(payload.get("parent_cpu_seconds"))
            # round 8 finding #2 (R8-2): this invocation's own summary now
            # covers any within-process CPU its own `meter_call` records
            # spent — see the pairing rule above.
            invocation_id = payload.get("invocation_id")
            if invocation_id is not None:
                invocation_ids_with_summary.add(invocation_id)
        elif kind == "slice_summary":
            # Codex PR #345 finding #2 (adopted, category ③,
            # `[UNDERSPEC-CAL-D79]`): a `PARTIAL_SLICE` dispatch (`cli.py`
            # `main()`'s `finally` block) charges its own parent CPU to
            # `cap_counters`/`counters.json` exactly like a completing
            # dispatch's `stage_summary`, but appends this distinct kind
            # instead (no phase transition happened, so it is not a
            # `stage_summary`). Summed 1:1 per event, same as
            # `stage_summary` above — a `PARTIAL_SLICE` dispatch never also
            # appends `stage_summary`, so summing both kinds across every
            # dispatch of a stage reconstructs the same total the persisted
            # cache accumulated, with no overlap between them.
            compute += _finite_nonneg_float(payload.get("parent_cpu_seconds"))
            # round 8 finding #2 (R8-2): `slice_summary` covers its
            # invocation's within-process CPU the same way `stage_summary`
            # does (see above) — both are the one summary event `cli.py
            # main()`'s `finally` block appends per dispatch, on every exit
            # path (including a caught interruption), just tagged by
            # whether a phase transition happened.
            invocation_id = payload.get("invocation_id")
            if invocation_id is not None:
                invocation_ids_with_summary.add(invocation_id)
        elif kind == "parent_cpu_checkpoint":
            # R7 P1 fix: track the maximum cumulative value seen per
            # `invocation_id` — never summed (each event already carries the
            # full cumulative-since-dispatch-start figure, not a delta on top
            # of prior checkpoints; see docstring). Charged in the deferred
            # pass below, gated on that invocation never being summarized.
            checkpoint_invocation_id = payload.get("invocation_id")
            checkpoint_value = _finite_nonneg_float(payload.get("parent_cpu_seconds"))
            existing = checkpoint_cpu_by_invocation.get(checkpoint_invocation_id)
            if existing is None or checkpoint_value > existing:
                checkpoint_cpu_by_invocation[checkpoint_invocation_id] = checkpoint_value
                # R9 fix: record this checkpoint's own ledger position
                # alongside its value — see `checkpoint_seq_by_invocation`'s
                # declaration-site docstring above.
                checkpoint_seq_by_invocation[checkpoint_invocation_id] = entry.seq
        elif kind == "worker_failed":
            # round 24 ADOPT (1) (`[UNDERSPEC-CAL-D55]`): compute — no dedup,
            # every event is its own charged attempt.
            compute += _finite_nonneg_float(payload.get("cpu_seconds"))
            storage += _finite_nonneg_int(payload.get("storage_bytes"))
            # round 26 ADOPT (3) (`[UNDERSPEC-CAL-D59]`): budget — per-BATCH
            # `budget_units` (0 or 1 on this event), not 1 per event. Missing
            # field (pre-round-26 event) defaults to 1 (see docstring above).
            worker_batch_budget_units += _finite_nonneg_int(payload.get("budget_units", 1))
        elif kind == "worker_attempts_discarded":
            # round 25 (`[UNDERSPEC-CAL-D57]`): compute — each entry of
            # `discarded_success_attempts` is its own charged attempt, summed
            # the same way `worker_failed` events are.
            attempts = payload.get("discarded_success_attempts")
            if isinstance(attempts, Sequence) and not isinstance(attempts, (str, bytes)):
                for attempt in attempts:
                    if not isinstance(attempt, Mapping):
                        continue
                    compute += _finite_nonneg_float(attempt.get("cpu_seconds"))
            storage += _finite_nonneg_int(payload.get("storage_bytes"))
            # round 26 ADOPT (3) (`[UNDERSPEC-CAL-D59]`): budget — same
            # per-BATCH `budget_units` field as `worker_failed` above (NOT
            # the count of `discarded_success_attempts` entries).
            worker_batch_budget_units += _finite_nonneg_int(payload.get("budget_units", 1))
    # round 12 finding (adopted, category ③, `[UNDERSPEC-CAL-D79]`): charge
    # the within-process CPU of every key still active at scan end (i.e.
    # never popped by a `meter_call_group_discarded` reset — see that
    # branch above) whose writer invocation never appears in
    # `invocation_ids_with_summary`. This must run as a deferred pass, not
    # inline inside the `meter_call` branch, because the writer's own
    # `stage_summary`/`slice_summary` (the normal, expected case) is
    # appended to the ledger AFTER that writer's `meter_call` records, not
    # before. Exactly-once with the discard path above: a key popped by
    # `meter_call_group_discarded` is absent from `last_meter_invocation`
    # here, so its within CPU (already charged via that event's own
    # `discarded_within_cpu_seconds`) is never revisited; a key charged
    # here was never discarded, so it was never charged there either.
    #
    # round 13 finding (adopted, category ③, `[UNDERSPEC-CAL-D79]`): gated
    # to COMPLETE groups only (`len(meter_group_repeat_keys[key]) >=
    # _METER_REPEAT_KEY_COUNT`, i.e. all `WITHIN_PROCESS_REPEATS +
    # FRESH_PROCESS_REPEATS` distinct `(repeat_kind, repeat_index)` pairs
    # present) — a still-PARTIAL group (fewer records; writer hard-killed
    # mid-write, not yet discarded) must NOT be charged here even though it
    # is unpopped and unsummarized, because `--discard-partial-groups`
    # recovery reconciles `cap_counters` from the ledger (this function)
    # BEFORE `_discard_partial_group()` ever runs — charging it here would
    # double-count once that discard event separately charges
    # `discarded_within_cpu_seconds` for the same key (a false
    # `COST_CAP_EXCEEDED` risk). **Invariant**: within CPU of a group from
    # an unsummarized writer is charged exactly once — complete groups via
    # this deferred pass, partial groups via their discard event
    # (`_discard_partial_group`); summarized writers are covered by their
    # summary. See the docstring's round 13 paragraph above for the full
    # account of the bug this closes.
    for key, invocation_id in last_meter_invocation.items():
        if invocation_id is None or invocation_id not in invocation_ids_with_summary:
            if len(meter_group_repeat_keys.get(key, ())) < _METER_REPEAT_KEY_COUNT:
                continue
            # R9 fix: skip iff this group's writer invocation also recorded a
            # `parent_cpu_checkpoint` whose ledger position comes AFTER this
            # group finished writing — that checkpoint's own cumulative
            # `parent_cpu_seconds` (charged below) already includes this
            # group's within-process CPU, so charging it again here would
            # double count. A group that finished writing AFTER its
            # invocation's checkpoint (or an invocation with no checkpoint at
            # all) is unaffected and still charged here — see the
            # docstring's R9 paragraph for the full ledger-order rule. A
            # `None` `invocation_id` means "unidentified writer", not "same
            # writer as some other `None`-tagged checkpoint" — never treated
            # as a match, so two distinct unidentified legacy writers can
            # never be cross-matched into a false skip here.
            checkpoint_seq = (
                checkpoint_seq_by_invocation.get(invocation_id)
                if invocation_id is not None
                else None
            )
            if checkpoint_seq is not None and meter_group_last_seq.get(key, -1) < checkpoint_seq:
                continue
            compute += meter_group_within_cpu.get(key, 0.0)
    # R7 P1 fix: charge each invocation's own `parent_cpu_checkpoint` maximum
    # exactly once, but only for an invocation that never got a
    # `stage_summary`/`slice_summary` (whose own `parent_cpu_seconds` already
    # covers this same CPU — double counting otherwise). See the
    # `parent_cpu_checkpoint` docstring paragraph above.
    for checkpoint_invocation_id, checkpoint_value in checkpoint_cpu_by_invocation.items():
        if (
            checkpoint_invocation_id is None
            or checkpoint_invocation_id not in invocation_ids_with_summary
        ):
            compute += checkpoint_value
    budget = 0.0
    if cost_caps is not None:
        per_unit = cost_caps.budget_charge_per_work_unit()
        if per_unit:
            budget = per_unit * (render_units + meter_units + worker_batch_budget_units)
    return CapCounters(compute_used=compute, storage_used=storage, budget_used=budget)


def is_invocation_id_summarized(
    ledger_entries: Sequence[LedgerEntry], invocation_id: object
) -> bool:
    """round 8 finding #1 (R8-1, category ③, `[UNDERSPEC-CAL-D79]`): the
    same pairing predicate `cap_counters_from_ledger()` computes inline
    during full ledger reconstruction, exposed standalone so
    `measure_stage.run_measurement_for_instance()` can ask it AT DISCARD
    TIME — before choosing whether to charge the live in-memory
    `cap_counters` for `discarded_within_cpu_seconds` and enforce the cap
    before remeasuring (see that function's docstring). `invocation_id`
    is the WRITER's id (the partial group's own `meter_call` records'
    `invocation_id`, from `StaleMeasurementError.invocation_id` — see
    `measure_stage._partial_group_invocation_id()`), never the discarding
    process's own id. Returns `True` iff a `stage_summary`/`slice_summary`
    event carrying that exact `invocation_id` already exists anywhere in
    the ledger (that invocation's own `parent_cpu_seconds` already covers
    the within-process CPU its `meter_call` records spent — charging it
    again here would double count); `False` for `invocation_id is None`
    (no writer identity on record — the fail-closed default this module
    uses throughout, matching `cap_counters_from_ledger()`'s own handling
    of a `None`/missing key)."""
    if invocation_id is None:
        return False
    for entry in ledger_entries:
        payload = entry.payload
        if not isinstance(payload, Mapping):
            continue
        if payload.get("kind") not in ("stage_summary", "slice_summary"):
            continue
        if payload.get("invocation_id") == invocation_id:
            return True
    return False


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
    "WORKER_FAILURE_KIND_TIMEOUT",
    "WORKER_FAILURE_KIND_NONZERO_EXIT",
    "WORKER_FAILURE_KIND_MALFORMED_OUTPUT",
    "WORKER_FAILURE_KINDS",
    "validate_worker_cpu_seconds",
    "reported_cpu_seconds_or_none",
    "charge_worker_failure",
    "charge_worker_attempts_before_raising",
    "counters_path",
    "cost_caps_from_manifest",
    "load_cap_counters",
    "save_cap_counters",
    "cap_counters_from_ledger",
    "is_invocation_id_summarized",
    "reconcile_cap_counters",
]
