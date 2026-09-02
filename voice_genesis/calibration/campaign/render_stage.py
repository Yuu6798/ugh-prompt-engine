"""C1/C4 render stage（IMPLEMENTATION_MAP_v1.md §6.4）。

- 各 instance を `render_root_secret` から streams 派生した RNG で 2 回
  fresh-process render し（subprocess worker `_render_worker.py`）、byte 一致
  を要求する（違反 → 両 worker の実測 `cpu_seconds`/budget 1 work unit 分を
  `render_nondeterministic` ledger event で先に課金・記帳してから
  `BLOCKED_C1_GENERATOR_NONDETERMINISTIC` stop event を記帳し
  `RenderNondeterministicError` で fail-closed。round 23 ADOPT (2)
  `[UNDERSPEC-CAL-D52]` — 課金前に raise すると、cap を一切消費しないまま
  同一の非決定的 instance へ何度でも retry できてしまうため）。
- 一致すれば PCM を `renders/<row_id>/<probe_index>.pcm` へ書き、sha256 を
  `renders/<row_id>/<probe_index>.sha256` として併記、ledger `render` event
  (`row_id`/`probe_index`/`sha256`) を記帳する。
- **resume**: 既に `render` event が記帳済みの instance は、現在の pcm
  ファイルの sha256 が ledger 記録値と一致する場合のみスキップする。ファイル
  欠損または sha 不一致は stale として fail-closed（`RenderStaleError`。
  無言スキップ・無言再 render のいずれも禁止 — memo §6.4）。
- **leakage 検査**: holdout 非 control instance の render を試みる **前**に
  `provenance.Ledger.check_leakage()` を呼び、unseal 前なら
  `BLOCKED_LEAKAGE` で拒否する（§7）。C1 は holdout instance を対象にしない
  ため本検査を行わない。
"""

from __future__ import annotations

import hashlib
import json
import resource
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from voice_genesis.calibration.campaign import workunits
from voice_genesis.calibration.campaign.caps import (
    CostCapExceededError,
    WorkerCpuSecondsInvalidError,
    charge_worker_failure,
    reported_cpu_seconds_or_none,
    save_cap_counters,
    validate_worker_cpu_seconds,
)
from voice_genesis.calibration.campaign.state import FrozenCampaign
from voice_genesis.calibration.cost_caps import CapCounters, CostCaps
from voice_genesis.calibration.cost_caps import check as cost_caps_check
from voice_genesis.calibration.fixtures.controls import control_row_ids
from voice_genesis.calibration.fixtures.matrix import FixtureRow, MatrixRow
from voice_genesis.calibration.provenance import Ledger, LedgerEntry
from voice_genesis.calibration.splitter import RowInput
from voice_genesis.calibration.vocab import BlockedCode, Split

#: `campaign.caps.CostCapExceededError` を本モジュール名前空間へ再公開する
#: （finding #1: render_stage/measure_stage で単一の cap 超過 error 型を
#: 共有する）。

#: `c0_freeze.STRATUM_FACTOR_NAMES` と同一の stratum 化因子（`c0_freeze.py` は
#: 他 agent が並行編集中のため import せず、値のみをここに複製する — この値
#: 自体は既に凍結済みの decision であり本モジュール独自の UNDERSPEC ではない）。
STRATUM_FACTOR_NAMES: tuple[str, ...] = ("truth_level", "boundary_class")


def _row_inputs_for_split(
    matrix_rows: Sequence[MatrixRow], stratum_factor_names: Sequence[str]
) -> list[RowInput]:
    """`provenance.Ledger.check_leakage` の `canonical_split_inputs` 構築と
    同一の規約で `RowInput` を組み立てる（`c0_freeze._row_inputs_for_split`
    と同一ロジック）。"""
    out: list[RowInput] = []
    for mrow in matrix_rows:
        fr = mrow.row
        stratum: dict[str, object] = {}
        for name in stratum_factor_names:
            if name == "truth_level":
                stratum[name] = fr.block
            elif name in ("boundary_class", "domain"):
                stratum[name] = mrow.domain.value
            elif name == "generator_impl":
                stratum[name] = fr.generator_impl
            else:
                stratum[name] = getattr(fr, name, None)
        out.append(
            RowInput(
                row_id=mrow.row_id,
                family=fr.family,
                stratum=stratum,
                truth_level=fr.block,
                generator_impl=fr.generator_impl,
                boundary_class=mrow.domain.value,
            )
        )
    return out


def _children_cpu_seconds() -> float:
    """round 24 ADOPT (1) (`[UNDERSPEC-CAL-D55]`): this process's cumulative
    child user+sys CPU seconds (`resource.getrusage(RUSAGE_CHILDREN)`) —
    the parent-observed fallback used to charge a fresh-process render
    worker that failed post-spawn (timeout / nonzero exit / malformed JSON)
    and so never reported its own `cpu_seconds`. Mirrors
    `measure_stage._children_cpu_seconds()`; see that function's docstring
    for the POSIX timeout-reaping note."""
    ru_children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return ru_children.ru_utime + ru_children.ru_stime


class RenderNondeterministicError(RuntimeError):
    """generator determinism 検査違反（`BLOCKED_C1_GENERATOR_NONDETERMINISTIC`）。"""

    def __init__(self, row_id: str, probe_index: int) -> None:
        self.row_id = row_id
        self.probe_index = probe_index
        super().__init__(
            f"render_stage: generator nondeterministic for row_id={row_id!r} "
            f"probe_index={probe_index}"
        )


class RenderStaleError(RuntimeError):
    """resume 時、ledger 記録済み sha と現ファイルの sha が不一致（fail-closed）。"""

    def __init__(self, row_id: str, probe_index: int, detail: str) -> None:
        self.row_id = row_id
        self.probe_index = probe_index
        super().__init__(
            f"render_stage: stale render for row_id={row_id!r} probe_index={probe_index}: "
            f"{detail}"
        )


class RenderLeakageBlockedError(RuntimeError):
    """`BLOCKED_LEAKAGE`: unseal 前の holdout 非 control render 要求。"""

    def __init__(self, detail: str) -> None:
        super().__init__(f"render_stage: BLOCKED_LEAKAGE: {detail}")


@dataclass(frozen=True)
class RenderOutcome:
    row_id: str
    probe_index: int
    status: str  # "rendered" | "skipped_resume"
    sha256: str
    #: round 14 finding #2: `cpu_seconds` is the sum of the 2 fresh-process
    #: render workers' own reported CPU time (the value actually charged to
    #: the compute cap); `wall_seconds` is the wall-clock elapsed for those
    #: same 2 renders, kept informational-only. Both are 0.0 for
    #: `status="skipped_resume"` (no new work was done).
    wall_seconds: float = 0.0
    cpu_seconds: float = 0.0
    #: round 15 finding #3 (`[UNDERSPEC-CAL-D31]`): PCM byte count charged
    #: to `storage_used` for this render (0 for `status="skipped_resume"`).
    pcm_bytes: int = 0


def _recorded_render_sha(
    ledger_entries: Sequence[LedgerEntry], row_id: str, probe_index: int
) -> str | None:
    for entry in ledger_entries:
        payload = entry.payload
        if not isinstance(payload, Mapping):
            continue
        if (
            payload.get("kind") == "render"
            and payload.get("row_id") == row_id
            and payload.get("probe_index") == probe_index
        ):
            sha = payload.get("sha256")
            return sha if isinstance(sha, str) else None
    return None


def _pcm_path(campaign: FrozenCampaign, row_id: str, probe_index: int) -> Path:
    return campaign.renders_dir / row_id / f"{probe_index}.pcm"


def render_instance(
    campaign: FrozenCampaign,
    row: FixtureRow,
    *,
    family: str,
    split: Split,
    row_id: str,
    probe_index: int,
    timeout_s: float = 60.0,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
) -> RenderOutcome:
    """1 instance を resume 判定 → (必要なら) 2 回 fresh-process render →
    byte 比較 → 書込の順で処理する。

    `cap_counters`/`cost_caps`（finding #1）: 実際に render した（resume で
    skip しなかった）unit のみ compute/storage（書き込んだ PCM bytes 数）を
    計上する。cap 超過を検出したら `stop_event` ledger event を記帳し
    `CostCapExceededError` で fail-closed する — 呼び出し元
    `run_render_stage` の次 unit には進まない。**非決定性検出時**（round 23
    ADOPT (2)、`[UNDERSPEC-CAL-D52]`）も同様に、2 worker が既に消費した
    compute（両者の `cpu_seconds` 合計）と budget 1 work unit 分を
    `RenderNondeterministicError` を送出する**前**に課金・persist・cap 再検査
    まで行う（storage は PCM を書かないため常に 0）——検出済みの
    nondeterminism を charge せず raise すると、cap を一切消費しない retry
    ループが可能になってしまうため。cap 超過を検出すればここでも
    `CostCapExceededError` を優先して送出する（nondeterminism error より cap
    breach が優先される既存の完了 render 経路と同じ優先順位）。

    **compute 課金**（round 14 finding #2）: compute へ課金する値は
    2 回の fresh-process render worker（`_render_worker.py`）が自ら報告した
    `cpu_seconds`（`resource.getrusage` RUSAGE_SELF+RUSAGE_CHILDREN）の合計
    であり、wall-clock 経過時間ではない（Gate 1 が定義する compute cap の
    単位は CPU 秒数であり、wall time は並行実行時に過小計上する —
    `campaign/caps.py` の `validate_worker_cpu_seconds()` docstring 参照）。
    wall time は `RenderOutcome.wall_seconds` として informational にのみ
    保持し、呼び出し元 `run_render_stage` がそれを ledger `render` event へ
    転記する。worker が無効な `cpu_seconds` を報告した場合は
    `WorkerCpuSecondsInvalidError` を送出する（測定は完了しているが
    stale/invalid unit として fail-closed — PCM は書き込まない）。

    **worker 失敗時の課金**（round 24 ADOPT (1)、`[UNDERSPEC-CAL-D55]`）:
    上記の「無効な `cpu_seconds`」とは異なり、worker が起動後に timeout /
    nonzero exit / malformed JSON でそもそも well-formed な結果を返さなかった
    場合は、`caps.charge_worker_failure()` でその 1 attempt が実際に消費した
    compute（可能なら worker 自身の報告値、無ければ親プロセスの
    `RUSAGE_CHILDREN` 差分）を `worker_failed` ledger event として記帳・
    （`cap_counters` があれば）課金・cap 再検査してから元の例外を再送出する
    （cap 超過を検出すれば `CostCapExceededError` を優先 — 既存の他
    charge-then-check 呼び出し箇所と同じ優先順位）。課金前に raise していた
    旧実装は、失敗する instance への retry が worker CPU を無制限・無課金で
    消費できてしまっていた。
    """
    pcm_path = _pcm_path(campaign, row_id, probe_index)
    recorded_sha = _recorded_render_sha(campaign.ledger.entries, row_id, probe_index)
    if recorded_sha is not None:
        if not pcm_path.is_file():
            raise RenderStaleError(row_id, probe_index, f"ledger has sha but {pcm_path} is missing")
        current_sha = hashlib.sha256(pcm_path.read_bytes()).hexdigest()
        if current_sha != recorded_sha:
            raise RenderStaleError(
                row_id,
                probe_index,
                f"current file sha256={current_sha!r} != ledger sha256={recorded_sha!r}",
            )
        return RenderOutcome(row_id=row_id, probe_index=probe_index, status="skipped_resume", sha256=current_sha)

    payload = {
        "row_json": json.dumps(row.to_canonical_dict()),
        "secret_hex": campaign.render_root_secret.hex(),
        "campaign_id": campaign.campaign_id,
        "family": family,
        "split": split.value,
        "row_id": row_id,
        "probe_index": probe_index,
    }
    payload_json = json.dumps(payload)
    argv = [
        sys.executable,
        "-m",
        "voice_genesis.calibration.campaign._render_worker",
        payload_json,
    ]
    wall_t0 = time.perf_counter()
    outputs: list[str] = []
    cpu_seconds_total = 0.0
    for _ in range(2):
        # round 24 ADOPT (1) (`[UNDERSPEC-CAL-D55]`): a worker that times
        # out, exits nonzero, or emits JSON that fails to parse must charge
        # the attempted work (`caps.charge_worker_failure()`) BEFORE the
        # failure propagates — compute is the worker's own reported
        # `cpu_seconds` when a well-formed report is actually recoverable (a
        # nonzero-exit worker's captured stdout, via
        # `caps.reported_cpu_seconds_or_none()`), otherwise the
        # parent-observed `RUSAGE_CHILDREN` delta around this call
        # (`_children_cpu_seconds()`) — mirrors
        # `measure_stage._run_one_fresh_call()`. Distinct from — and does
        # not touch — the existing `WorkerCpuSecondsInvalidError` path below
        # (a worker that exits 0 with parseable JSON but an invalid
        # `cpu_seconds` field): that stays uncharged, per the pre-existing
        # round 14 finding #2 contract.
        children_t0 = _children_cpu_seconds()
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout_s, check=True
            )
        except subprocess.TimeoutExpired as exc:
            charge_worker_failure(
                campaign.ledger,
                campaign.campaign_dir,
                cap_counters=cap_counters,
                cost_caps=cost_caps,
                stage="render",
                row_id=row_id,
                probe_index=probe_index,
                candidate_id=None,
                failure_kind="timeout",
                compute=_children_cpu_seconds() - children_t0,
                cause=exc,
            )
        except subprocess.CalledProcessError as exc:
            compute = reported_cpu_seconds_or_none(exc.stdout)
            if compute is None:
                compute = _children_cpu_seconds() - children_t0
            charge_worker_failure(
                campaign.ledger,
                campaign.campaign_dir,
                cap_counters=cap_counters,
                cost_caps=cost_caps,
                stage="render",
                row_id=row_id,
                probe_index=probe_index,
                candidate_id=None,
                failure_kind="nonzero_exit",
                compute=compute,
                cause=exc,
            )
        try:
            worker_result = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            charge_worker_failure(
                campaign.ledger,
                campaign.campaign_dir,
                cap_counters=cap_counters,
                cost_caps=cost_caps,
                stage="render",
                row_id=row_id,
                probe_index=probe_index,
                candidate_id=None,
                failure_kind="malformed_output",
                compute=_children_cpu_seconds() - children_t0,
                cause=exc,
            )
        cpu_seconds_total += validate_worker_cpu_seconds(
            worker_result.get("cpu_seconds"),
            context=f"render_stage: fresh-process worker for row_id={row_id!r} "
            f"probe_index={probe_index}",
        )
        outputs.append(worker_result["pcm_hex"])
    wall_seconds = time.perf_counter() - wall_t0
    a, b = outputs
    if a != b:
        # round 23 ADOPT (2) (`[UNDERSPEC-CAL-D52]`): both fresh-process
        # workers already ran and reported real cpu_seconds by this point —
        # discarding that before raising let a caller retry the same
        # nondeterministic instance indefinitely without ever charging the
        # compute it actually spent (a "free" cap-bypass loop). Charge the
        # attempted work — compute = both workers' summed cpu_seconds,
        # storage = 0 (no PCM is ever persisted on a mismatch), budget = 1
        # work unit per the frozen `budget_accounting_mode` (same rule as a
        # completed render) — persist, and run the cap check BEFORE raising,
        # mirroring the completed-render charge path below. A
        # `render_nondeterministic` ledger event carries the same charges so
        # `campaign.caps.cap_counters_from_ledger()` can reconstruct them
        # from the ledger alone even if `counters.json` is lost — it is
        # appended unconditionally (not only when `cap_counters` is passed),
        # matching how a `render` event's `cpu_seconds` is always recorded.
        budget_charge = cost_caps.budget_charge_per_work_unit() if cost_caps is not None else 0.0
        campaign.ledger.append(
            {
                "kind": "render_nondeterministic",
                "row_id": row_id,
                "probe_index": probe_index,
                "cpu_seconds": cpu_seconds_total,
                "storage_bytes": 0,
            }
        )
        if cap_counters is not None:
            cap_counters.add(compute=cpu_seconds_total, storage=0, budget=budget_charge)
            save_cap_counters(campaign.campaign_dir, cap_counters)
            if cost_caps is not None:
                decision = cost_caps_check(cap_counters, cost_caps)
                if decision is not None:
                    campaign.ledger.append(decision.event_payload)
                    raise CostCapExceededError(decision.detail)
        raise RenderNondeterministicError(row_id, probe_index)

    pcm_bytes = bytes.fromhex(a)
    pcm_path.parent.mkdir(parents=True, exist_ok=True)
    pcm_path.write_bytes(pcm_bytes)
    sha = hashlib.sha256(pcm_bytes).hexdigest()
    pcm_path.with_suffix(".sha256").write_text(sha, encoding="utf-8")

    if cap_counters is not None:
        # round 13 finding #3: this render (1 instance = 2 fresh-process
        # renders) is 1 budget work unit — charge it per the frozen
        # `budget_accounting_mode` (0 under local_zero_cost, `budget_unit_cost`
        # under per_unit_fixed). No cost_caps declared -> no budget dimension
        # tracked (same "cap not yet frozen" posture as compute/storage).
        budget_charge = cost_caps.budget_charge_per_work_unit() if cost_caps is not None else 0.0
        cap_counters.add(compute=cpu_seconds_total, storage=len(pcm_bytes), budget=budget_charge)
        # Persist immediately (finding #1: counters must survive across
        # subcommands) — before the breach check below.
        save_cap_counters(campaign.campaign_dir, cap_counters)
        if cost_caps is not None:
            decision = cost_caps_check(cap_counters, cost_caps)
            if decision is not None:
                campaign.ledger.append(decision.event_payload)
                raise CostCapExceededError(decision.detail)

    return RenderOutcome(
        row_id=row_id,
        probe_index=probe_index,
        status="rendered",
        sha256=sha,
        wall_seconds=wall_seconds,
        cpu_seconds=cpu_seconds_total,
        pcm_bytes=len(pcm_bytes),
    )


def _refuse_if_pre_unseal_holdout(
    campaign: FrozenCampaign, matrix_rows: Sequence[MatrixRow]
) -> None:
    """C4 render の前提: `provenance.Ledger.check_leakage` を呼び、unseal 前
    なら `RenderLeakageBlockedError` で拒否する（§7）。"""
    control_ids = control_row_ids(matrix_rows)
    holdout_row_ids = frozenset(
        rid for rid, split in campaign.realized_split.assignment.items() if split == Split.HOLDOUT
    )
    split_verification_rows = _row_inputs_for_split(matrix_rows, STRATUM_FACTOR_NAMES)
    result = Ledger.check_leakage(
        campaign.ledger.entries,
        holdout_row_ids,
        None,
        control_row_ids=control_ids,
        realized_split_map=campaign.realized_split,
        split_verification_rows=split_verification_rows,
        split_secret=campaign.split_secret,
    )
    if result.blocked is not None:
        raise RenderLeakageBlockedError(
            f"blocked_code={result.blocked.value} "
            f"(control_excluded_count={result.control_excluded_count})"
        )


def run_render_stage(
    campaign: FrozenCampaign,
    matrix_rows: Sequence[MatrixRow],
    *,
    stage: str,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
) -> tuple[RenderOutcome, ...]:
    """`stage='c1'` または `stage='c4'`。C4 は render を試みる前に leakage
    検査を行う。determinism 違反時は既に render 済みの outcome を保ったまま
    `BLOCKED_C1_GENERATOR_NONDETERMINISTIC` stop event を ledger へ記帳し、
    `RenderNondeterministicError` を再送出する（fail-closed。以降の instance
    は render しない）。`cap_counters`/`cost_caps`（finding #1）は
    `render_instance` へ素通しし、cap 超過時は同様に以降の instance へ進まず
    `CostCapExceededError` を伝播する。"""
    assignment = campaign.realized_split.assignment
    if stage == "c1":
        units = workunits.enumerate_c1_render_units(matrix_rows, assignment)
    elif stage == "c4":
        _refuse_if_pre_unseal_holdout(campaign, matrix_rows)
        units = workunits.enumerate_c4_render_units(matrix_rows, assignment)
    else:
        raise ValueError(f"run_render_stage: unknown stage {stage!r}")

    rows_by_id = {mr.row_id: mr.row for mr in matrix_rows}
    outcomes: list[RenderOutcome] = []
    for unit in units:
        row = rows_by_id[unit.row_id]
        try:
            outcome = render_instance(
                campaign,
                row,
                family=unit.family,
                split=unit.split,
                row_id=unit.row_id,
                probe_index=unit.probe_index,
                cap_counters=cap_counters,
                cost_caps=cost_caps,
            )
        except RenderNondeterministicError as exc:
            campaign.ledger.append(
                {
                    "kind": "stop_event",
                    "reason": BlockedCode.BLOCKED_C1_GENERATOR_NONDETERMINISTIC.value,
                    "row_id": unit.row_id,
                    "probe_index": unit.probe_index,
                    "stage": stage,
                }
            )
            raise exc
        except WorkerCpuSecondsInvalidError as exc:
            # round 14 finding #2: a fresh-process render worker reported a
            # missing/non-finite/negative cpu_seconds — stale/invalid unit,
            # fail-closed (no PCM was published for it).
            campaign.ledger.append(
                {
                    "kind": "stop_event",
                    "reason": "INVALID_RENDER_WORKER_CPU_SECONDS",
                    "row_id": unit.row_id,
                    "probe_index": unit.probe_index,
                    "stage": stage,
                    "detail": str(exc),
                }
            )
            raise exc
        outcomes.append(outcome)
        if outcome.status == "rendered":
            campaign.ledger.append(
                {
                    "kind": "render",
                    "row_id": unit.row_id,
                    "family": unit.family,
                    "split": unit.split.value,
                    "probe_index": unit.probe_index,
                    "sha256": outcome.sha256,
                    "stage": stage,
                    # round 14 finding #2: cpu_seconds is what was charged to
                    # the compute cap; wall_seconds is informational only.
                    "wall_seconds": outcome.wall_seconds,
                    "cpu_seconds": outcome.cpu_seconds,
                    # round 15 finding #3 (`[UNDERSPEC-CAL-D31]`): the PCM
                    # byte count charged to `storage_used` for this render,
                    # so `campaign.caps.cap_counters_from_ledger()` can
                    # reconstruct storage from the ledger alone (a bare
                    # `sha256` cannot recover the byte count of the file it
                    # hashes).
                    "pcm_bytes": outcome.pcm_bytes,
                }
            )

    if stage == "c1":
        campaign.ledger.append(
            {
                "kind": "fixture_valid",
                "instance_count": len({(u.row_id, u.probe_index) for u in units}),
            }
        )
    return tuple(outcomes)


__all__ = [
    "STRATUM_FACTOR_NAMES",
    "RenderNondeterministicError",
    "RenderStaleError",
    "RenderLeakageBlockedError",
    "RenderOutcome",
    "render_instance",
    "run_render_stage",
]
