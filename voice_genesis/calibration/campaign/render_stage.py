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
  無言スキップ・無言再 render のいずれも禁止 — memo §6.4）。`run_render_stage`
  自体は resume 済み unit を `_render_index_from_ledger` の O(1) index で
  skip する（`render_instance` を再度呼ばない）が、そのまま stage 遷移
  （c1 の `fixture_valid` / c4-holdout の render サブフェーズ→measure
  サブフェーズ引き渡し）に進む**completing invocation**でのみ、index が
  skip した unit を一度だけ検証する（`RenderResumeIndexIntegrityError`。
  round 3 ADOPT ③ `[UNDERSPEC-CAL-D79]`。検証自体は `.sha256` sidecar と
  ledger 側 pin の両方を要求する `_verify_pcm_sidecar` 共有 helper に委譲し、
  `measure_stage._verify_and_load_rendered_pcm` と判定を一本化する —
  round 5 finding S4）——`PARTIAL_SLICE` 終了時はこの検証を行わない
  （index skip は O(1) のまま）。`stage="c4"` はこの検証が通ったことを
  `RENDER_PHASE_VALID_KIND`（`"holdout_render_valid"`）ledger event として
  記帳し、以降 render サブフェーズが完了した状態で measure サブフェーズが
  続く呼び出しは検証を再実行しない（round 5 finding S3）。
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
    charge_worker_attempts_before_raising,
    reported_cpu_seconds_or_none,
    save_cap_counters,
    validate_worker_cpu_seconds,
)
from voice_genesis.calibration.campaign.state import FrozenCampaign
from voice_genesis.calibration.campaign.time_budget import SliceStatus, TimeBudget
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


class _FreshRenderWorkerFailure(RuntimeError):
    """round 25 (`[UNDERSPEC-CAL-D57]`): internal-only carrier, mirrors
    `measure_stage._FreshWorkerFailure`. `_run_one_render_worker` raises this
    instead of letting a post-spawn subprocess failure (timeout / nonzero
    exit / malformed JSON / invalid `cpu_seconds` / invalid `pcm_hex`)
    propagate directly, so `render_instance` — which holds the
    ledger/`cap_counters`/`cost_caps` this needs to be charged against — can
    run BOTH fresh-process workers to completion first and charge the whole
    2-attempt batch (`caps.charge_worker_attempts_before_raising()`) before
    re-raising. Never crosses this module's public boundary (not in
    `__all__`, never returned/raised to callers of `render_instance`)."""

    def __init__(self, failure_kind: str, compute: float, cause: BaseException) -> None:
        self.failure_kind = failure_kind
        self.compute = compute
        self.cause = cause
        super().__init__(f"render_stage: fresh worker {failure_kind}: {cause}")


class RenderLeakageBlockedError(RuntimeError):
    """`BLOCKED_LEAKAGE`: unseal 前の holdout 非 control render 要求。"""

    def __init__(self, detail: str) -> None:
        super().__init__(f"render_stage: BLOCKED_LEAKAGE: {detail}")


class RenderResumeIndexIntegrityError(RuntimeError):
    """Codex PR #345 round 3 finding F5 (adopted, category ③,
    `[UNDERSPEC-CAL-D79]`): the O(1) index-based resume-skip fast path
    (`_render_index_from_ledger`) trusts the ledger sha alone and never
    re-reads a `skipped_resume` unit's PCM bytes. Left unguarded, a deleted
    or corrupted PCM under an already-recorded `render` event would let the
    completing invocation still append `fixture_valid` (c1) / let the
    render sub-phase hand off to the measure sub-phase (c4-holdout) — a
    falsely advanced campaign: `c1-fixtures` becomes `NOOP_ALREADY_COMPLETE`
    once FIXTURE_VALID is reached (`cli._stage_already_complete()`), so a
    later C2/measure `StaleRenderError` for that instance would have no
    resumable path back to render.

    Fix: in the completing invocation (`completed_all` — i.e. not a
    `PARTIAL_SLICE` exit), every `skipped_resume` outcome produced by THIS
    call is validated once (file exists + sha256 matches the recorded
    render sha) before the stage transition. A mismatch blocks the
    transition, appends a `stop_event` (existing kind, reason
    `RENDER_RESUME_INDEX_INTEGRITY_MISMATCH`) listing every failing unit,
    and raises this error instead — the `PARTIAL_SLICE` skip path itself is
    unchanged (still O(1) per already-completed unit; this validation only
    runs once, in the invocation that would otherwise transition).

    Recovery: render is a pure deterministic function of
    `render_root_secret` + row + campaign_id + family + split + row_id +
    probe_index (module docstring) — the exact original PCM bytes can
    always be regenerated externally (re-invoke `_render_worker` for the
    same instance, or replay `render_instance()`'s worker call) and written
    back to `renders/<row_id>/<probe_index>.pcm` (+ `.sha256` sidecar)
    WITHOUT touching the ledger. Once the on-disk bytes match the already-
    pinned `render` event's sha256 again, the next invocation's validation
    pass (and `measure_stage._verify_and_load_rendered_pcm`) both pass and
    the stage transitions normally. Appending a SECOND `render` ledger
    event for the same `(row_id, probe_index)` is NOT a valid alternative:
    `_recorded_render_sha()` and `_render_index_from_ledger()` both resolve
    to the FIRST matching `render` event for a key (`entries` iterated in
    ledger order — the loop returns/`setdefault`s on first match), so a
    later duplicate would be silently ignored by every existing reader,
    including measure-time pin verification — restoring the original bytes
    on disk is the only contract-preserving recovery."""

    def __init__(self, stage: str, failing_units: Sequence[tuple[str, int, str]]) -> None:
        self.stage = stage
        self.failing_units = tuple(failing_units)
        detail = "; ".join(
            f"row_id={rid!r} probe_index={pidx}: {msg}" for rid, pidx, msg in failing_units
        )
        super().__init__(
            f"render_stage: completing invocation for stage={stage!r} found "
            f"{len(failing_units)} stale skipped_resume unit(s) in the resume index, "
            f"transition blocked: {detail}"
        )


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


def _render_index_from_ledger(
    ledger_entries: Sequence[LedgerEntry],
) -> dict[tuple[str, int], str]:
    """Codex PR #345 finding #3 (adopted, category ③, `[UNDERSPEC-CAL-D79]`):
    one full ledger scan, building `{(row_id, probe_index): sha256}` for
    every completed `render` event. `run_render_stage()` builds this once
    per invocation and uses it to skip already-completed units before
    dispatching them to `render_instance()` at all — mirroring
    `measure_stage.MeterCallIndex`'s R3 fix for the measure loop. Without
    this, a resumed slice re-entered `render_instance()` for every already-
    completed unit (not just the unfinished ones), and each such call did
    its own full-ledger `_recorded_render_sha()` rescan plus a PCM
    read+sha256 — so a growing completed prefix made a fixed `--time-budget-
    seconds` slice do less and less new work, in the worst case expiring
    before any unfinished unit was even reached (no progress across
    repeated PARTIAL_SLICE exits). First `render` event per key wins (a
    completed unit's resume path never re-appends `render` for the same
    key, so duplicates are not expected in practice)."""
    index: dict[tuple[str, int], str] = {}
    for entry in ledger_entries:
        payload = entry.payload
        if not isinstance(payload, Mapping) or payload.get("kind") != "render":
            continue
        row_id = payload.get("row_id")
        probe_index = payload.get("probe_index")
        sha = payload.get("sha256")
        if not isinstance(row_id, str) or not isinstance(probe_index, int) or not isinstance(sha, str):
            continue
        index.setdefault((row_id, probe_index), sha)
    return index


def _pcm_path(campaign: FrozenCampaign, row_id: str, probe_index: int) -> Path:
    return campaign.renders_dir / row_id / f"{probe_index}.pcm"


#: Codex PR #345 round 5 finding S3 (adopted, category ③, `[UNDERSPEC-CAL-D79]`,
#: memo §6.5.3): ledger event kind marking that `stage`'s completing-
#: invocation resume-index integrity scan (`_validate_skipped_resume_
#: outcomes`, which re-reads and re-hashes every `skipped_resume` PCM) has
#: already run once and passed — mirrors `fixture_valid`'s "phase reached"
#: role but at a finer grain: `fixture_valid` (c1) / `holdout_executed_
#: valid` (c4, the WHOLE stage) only fire once the stage is entirely done,
#: while this marks just the render sub-phase of a `stage="c4"` call.
#: Not one of `state.CampaignPhase`'s 8 gate values (`state.py`'s
#: `LEDGER_KIND_FOR_PHASE`/`gate_monotonicity_ok` machinery is untouched by
#: this event) — a plain idempotency marker, same non-gate tier as
#: `stop_event`/`stale`/`f0_injection_rejected`.
RENDER_PHASE_VALID_KIND = "holdout_render_valid"


def _render_phase_already_valid(ledger_entries: Sequence[LedgerEntry], stage: str) -> bool:
    """O(1)-per-entry, PCM-free scan for `stage`'s `RENDER_PHASE_VALID_KIND`
    marker. Cheap even on a large ledger — unlike the full `skipped_resume`
    PCM rehash it lets a later invocation skip entirely."""
    for entry in ledger_entries:
        payload = entry.payload
        if (
            isinstance(payload, Mapping)
            and payload.get("kind") == RENDER_PHASE_VALID_KIND
            and payload.get("stage") == stage
        ):
            return True
    return False


def _verify_pcm_sidecar(
    campaign: FrozenCampaign, row_id: str, probe_index: int
) -> tuple[bytes, str, str | None]:
    """Codex PR #345 round 5 finding S4 (adopted, category ③,
    `[UNDERSPEC-CAL-D79]`): the single source of truth for "does this
    rendered PCM's `.sha256` sidecar agree with its own bytes", shared by
    `_validate_skipped_resume_outcomes()` below (render_stage's completing-
    invocation resume-index integrity check) and
    `measure_stage._verify_and_load_rendered_pcm()` (the per-measurement
    stale check, which also compares against the ledger-pinned sha —
    that comparison is each caller's own responsibility, not this
    function's, since the two callers source and report a mismatch against
    the ledger-pinned value slightly differently: render_stage already has
    it on hand as `outcome.sha256`, measure_stage looks it up itself via
    `_recorded_render_sha()` and has a distinct message for "no ledger
    event pins this instance at all").

    Pre-fix, `_validate_skipped_resume_outcomes()` re-derived this same
    PCM-vs-ledger-sha check from scratch instead of reusing
    `measure_stage._verify_and_load_rendered_pcm()`'s stricter contract —
    and, unlike that function, never read the `.sha256` sidecar at all. A
    deleted or corrupted sidecar therefore passed render_stage's resume
    validation cleanly (it only ever compared the PCM's own recomputed sha
    to the ledger-pinned one) and only failed downstream at measure time
    with a completely different error (`StaleRenderError`), defeating the
    whole point of validating once at the render→measure transition: C1
    would already read back as `NOOP_ALREADY_COMPLETE`
    (`fixture_valid` reached) by the time C2 discovered the corruption, with
    no resumable path back to render. Reusing one shared check here makes
    the two validators structurally unable to diverge again.

    Reads the PCM file (raises `FileNotFoundError` if absent — a distinct,
    more basic "never rendered" state, not "stale"; both callers already
    branch on this), then checks the `.sha256` sidecar's presence and match
    against the actual bytes. Returns `(pcm_bytes, actual_sha256,
    failure_detail)` where `failure_detail` is `None` iff the sidecar check
    passes — callers still owe their own comparison against whatever
    ledger-pinned sha256 they consider authoritative for this instance."""
    pcm_path = _pcm_path(campaign, row_id, probe_index)
    if not pcm_path.is_file():
        raise FileNotFoundError(
            f"pcm not rendered for row_id={row_id!r} probe_index={probe_index}: {pcm_path}"
        )
    pcm_bytes = pcm_path.read_bytes()
    actual_sha = hashlib.sha256(pcm_bytes).hexdigest()
    sha_path = pcm_path.with_suffix(".sha256")
    if not sha_path.is_file():
        return pcm_bytes, actual_sha, f"sha256 sidecar missing: {sha_path}"
    sidecar_sha = sha_path.read_text(encoding="utf-8").strip()
    if sidecar_sha != actual_sha:
        return (
            pcm_bytes,
            actual_sha,
            f"pcm sha256={actual_sha!r} does not match sidecar {sha_path}={sidecar_sha!r}",
        )
    return pcm_bytes, actual_sha, None


def _validate_skipped_resume_outcomes(
    campaign: FrozenCampaign, outcomes: Sequence[RenderOutcome]
) -> list[tuple[str, int, str]]:
    """F5 (round 3 ADOPT ③, `[UNDERSPEC-CAL-D79]`): one pass, run only in the
    completing invocation (see `RenderResumeIndexIntegrityError`), over the
    `skipped_resume` outcomes THIS call produced via the O(1) index skip —
    not a per-unit ledger rescan (the recorded sha is already on hand from
    `outcome.sha256`; `_recorded_render_sha` is not called again here).
    Returns `(row_id, probe_index, detail)` for every unit whose PCM file is
    missing, whose `.sha256` sidecar is missing/mismatched, or whose
    current sha256 no longer matches the recorded render sha (empty if
    every skipped unit is still intact).

    round 5 finding S4 (adopted, category ③, `[UNDERSPEC-CAL-D79]`): now
    delegates the PCM-vs-sidecar half of this check to `_verify_pcm_
    sidecar()` — see that function's docstring for why the pre-fix version
    (which recomputed the PCM's sha and compared it only against
    `outcome.sha256`, never reading the sidecar) let a deleted/corrupted
    sidecar silently pass."""
    failing: list[tuple[str, int, str]] = []
    for outcome in outcomes:
        if outcome.status != "skipped_resume":
            continue
        try:
            _pcm_bytes, actual_sha, detail = _verify_pcm_sidecar(
                campaign, outcome.row_id, outcome.probe_index
            )
        except FileNotFoundError:
            pcm_path = _pcm_path(campaign, outcome.row_id, outcome.probe_index)
            failing.append((outcome.row_id, outcome.probe_index, f"pcm missing: {pcm_path}"))
            continue
        if detail is None and actual_sha != outcome.sha256:
            detail = (
                f"pcm sha256={actual_sha!r} does not match ledger-pinned render "
                f"sha256={outcome.sha256!r}"
            )
        if detail is not None:
            failing.append((outcome.row_id, outcome.probe_index, detail))
    return failing


def _run_one_render_worker(
    argv: Sequence[str], timeout_s: float, *, row_id: str, probe_index: int
) -> tuple[bytes, float]:
    """Returns `(pcm_bytes, cpu_seconds)` for one fresh-process render
    worker attempt. Mirrors `measure_stage._run_one_fresh_call()`: a worker
    that times out, exits nonzero, or emits JSON that fails to parse raises
    `_FreshRenderWorkerFailure` carrying `failure_kind` and the compute to
    charge for the attempt (the worker's own reported `cpu_seconds` when a
    well-formed report is actually recoverable — a nonzero-exit worker's
    captured stdout via `caps.reported_cpu_seconds_or_none()` — otherwise the
    parent-observed `RUSAGE_CHILDREN` delta around this call).

    round 25 (`[UNDERSPEC-CAL-D57]`) finding "Charge parseable but invalid
    worker results": an exit-0 worker whose JSON parses but whose
    `cpu_seconds` is invalid, or whose `pcm_hex` is missing/non-string/not
    valid hex, ALSO raises `_FreshRenderWorkerFailure("malformed_output", ...)`
    instead of escaping this function's failure contract uncharged (the
    round 14 finding #2 "stays uncharged" posture this supersedes). The
    charged compute prefers the worker's own reported `cpu_seconds` when it
    is itself the part that validated fine (an invalid-`pcm_hex` failure
    after a valid `cpu_seconds`) — only falling back to the
    `RUSAGE_CHILDREN` delta when `cpu_seconds` itself is the unusable
    field."""
    children_t0 = _children_cpu_seconds()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, check=True)
    except subprocess.TimeoutExpired as exc:
        raise _FreshRenderWorkerFailure(
            "timeout", _children_cpu_seconds() - children_t0, exc
        ) from exc
    except subprocess.CalledProcessError as exc:
        compute = reported_cpu_seconds_or_none(exc.stdout)
        if compute is None:
            compute = _children_cpu_seconds() - children_t0
        raise _FreshRenderWorkerFailure("nonzero_exit", compute, exc) from exc
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise _FreshRenderWorkerFailure(
            "malformed_output", _children_cpu_seconds() - children_t0, exc
        ) from exc
    if not isinstance(raw, Mapping):
        raise _FreshRenderWorkerFailure(
            "malformed_output",
            _children_cpu_seconds() - children_t0,
            ValueError(f"render_stage: fresh worker returned non-object JSON: {raw!r}"),
        )
    try:
        cpu_seconds = validate_worker_cpu_seconds(
            raw.get("cpu_seconds"),
            context=f"render_stage: fresh-process worker for row_id={row_id!r} "
            f"probe_index={probe_index}",
        )
    except WorkerCpuSecondsInvalidError as exc:
        raise _FreshRenderWorkerFailure(
            "malformed_output", _children_cpu_seconds() - children_t0, exc
        ) from exc
    pcm_hex = raw.get("pcm_hex")
    if not isinstance(pcm_hex, str):
        raise _FreshRenderWorkerFailure(
            "malformed_output",
            cpu_seconds,
            ValueError(f"render_stage: fresh worker returned non-string pcm_hex: {pcm_hex!r}"),
        )
    try:
        pcm_bytes = bytes.fromhex(pcm_hex)
    except ValueError as exc:
        raise _FreshRenderWorkerFailure("malformed_output", cpu_seconds, exc) from exc
    return pcm_bytes, cpu_seconds


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
    invocation_id: str | None = None,
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

    **worker 失敗時の課金**（round 24 ADOPT (1) `[UNDERSPEC-CAL-D55]`、round 25
    `[UNDERSPEC-CAL-D57]` で統一規則へ改訂）: worker が起動後に timeout /
    nonzero exit / malformed JSON で well-formed な結果を返さなかった場合
    （exit 0 かつ parseable JSON だが `cpu_seconds`/`pcm_hex` が無効——旧実装
    で無課金だった経路——も round 25 でこの扱いに合流した。上記の docstring
    冒頭の「無効な `cpu_seconds`」もこれと同じ扱いになった）は、**両方の**
    fresh-process worker を（片方が失敗しても他方を打ち切らず）最後まで
    実行してから、`caps.charge_worker_attempts_before_raising()` を経由して
    batch 全体を一括課金する: 失敗した worker は `worker_failed` event、
    同一 batch 内で成功したが結果を破棄する worker（他方が失敗したため）は
    `worker_attempts_discarded` event として記帳・（`cap_counters` があれば）
    課金・cap 再検査してから、batch 内で最初に失敗した worker の元の例外を
    再送出する（cap 超過を検出すれば `CostCapExceededError` を優先 — 既存の
    他 charge-then-check 呼び出し箇所と同じ優先順位）。round 24 時点の旧実装
    は失敗した 1 worker のみを課金し、既に成功していたもう一方の worker が
    消費した compute を無課金で破棄していた。
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
    outcomes: list[tuple[bytes, float] | _FreshRenderWorkerFailure] = []
    for _ in range(2):
        try:
            outcomes.append(
                _run_one_render_worker(argv, timeout_s, row_id=row_id, probe_index=probe_index)
            )
        except _FreshRenderWorkerFailure as exc:
            outcomes.append(exc)

    failures = [outcome for outcome in outcomes if isinstance(outcome, _FreshRenderWorkerFailure)]
    if failures:
        # round 25 (`[UNDERSPEC-CAL-D57]`): both fresh-process workers ran to
        # completion above regardless of either's outcome — charge the WHOLE
        # 2-attempt batch (every success's own cpu_seconds via a
        # `worker_attempts_discarded` event, every failure via its own
        # `worker_failed` event) before re-raising the first failure, instead
        # of discarding an already-succeeded sibling worker's compute
        # uncharged (the round 24 posture this supersedes).
        successes = [
            cpu_seconds
            for outcome in outcomes
            if not isinstance(outcome, _FreshRenderWorkerFailure)
            for _pcm_bytes, cpu_seconds in [outcome]
        ]
        charge_worker_attempts_before_raising(
            campaign.ledger,
            campaign.campaign_dir,
            cap_counters=cap_counters,
            cost_caps=cost_caps,
            stage="render",
            row_id=row_id,
            probe_index=probe_index,
            candidate_id=None,
            successes=successes,
            failures=[(exc.failure_kind, exc.compute, exc.cause) for exc in failures],
            invocation_id=invocation_id,
        )

    # No failures reached this point (the branch above always raises) -- both
    # outcomes are real `(pcm_bytes, cpu_seconds)` pairs.
    wall_seconds = time.perf_counter() - wall_t0
    (a, cpu_a), (b, cpu_b) = outcomes  # type: ignore[misc]
    cpu_seconds_total = cpu_a + cpu_b
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
                "invocation_id": invocation_id,
            }
        )
        if cap_counters is not None:
            cap_counters.add(compute=cpu_seconds_total, storage=0, budget=budget_charge)
            save_cap_counters(campaign.campaign_dir, cap_counters)
            if cost_caps is not None:
                decision = cost_caps_check(cap_counters, cost_caps)
                if decision is not None:
                    campaign.ledger.append({**decision.event_payload, "invocation_id": invocation_id})
                    raise CostCapExceededError(decision.detail)
        raise RenderNondeterministicError(row_id, probe_index)

    pcm_bytes = a
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
                campaign.ledger.append({**decision.event_payload, "invocation_id": invocation_id})
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
    time_budget: TimeBudget | None = None,
    invocation_id: str | None = None,
) -> tuple[RenderOutcome, ...] | tuple[tuple[RenderOutcome, ...], SliceStatus]:
    """`stage='c1'` または `stage='c4'`。C4 は render を試みる前に leakage
    検査を行う。determinism 違反時は既に render 済みの outcome を保ったまま
    `BLOCKED_C1_GENERATOR_NONDETERMINISTIC` stop event を ledger へ記帳し、
    `RenderNondeterministicError` を再送出する（fail-closed。以降の instance
    は render しない）。`cap_counters`/`cost_caps`（finding #1）は
    `render_instance` へ素通しし、cap 超過時は同様に以降の instance へ進まず
    `CostCapExceededError` を伝播する。

    R2（design memo `design_runner_robustness.md`, `[UNDERSPEC-CAL-D79]`）:
    `time_budget` が渡されれば instance 境界（1 unit = 1 `(row_id,
    probe_index)` render）で予算超過を検査し、超過していれば以降の unit を
    dispatch せず戻る（既に dispatch 済みの unit は完走する）。この場合、
    `stage="c1"` でも完走しなかったときは `fixture_valid` event を記帳
    しない（phase transition は次回の resume 呼び出しに委ねる）。戻り値は
    `(outcomes, SliceStatus)` の 2-tuple になる。`time_budget` が `None`
    （既定）のときは従来どおり `outcomes` 単体を返す（呼び出し元の挙動・
    シグネチャは不変）。

    Codex PR #345 round 4 finding #3（adopted, category ③,
    `[UNDERSPEC-CAL-D79]`, `measure_stage.run_measure_stage()` と同一修正）:
    上記の予算境界検査は、次に dispatch する unit が既に `completed_units`
    に載っている（＝真に pending ではない）場合は発動しない — index 参照
    を budget 検査より先に行う。直前の呼び出しが最終 unit の render 完了後・
    stage 完了記帳前に中断され、かつ `_render_index_from_ledger()`（全
    ledger スキャン）自体が time_budget を使い切る場合でも、完了済み unit
    の O(1) skip が budget 切れでブロックされず、stage は完了/遷移へ進む。"""
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
    completed_all = True
    # Codex PR #345 finding #3 (adopted, category ③, `[UNDERSPEC-CAL-D79]`):
    # one full ledger scan up front (`_render_index_from_ledger`) instead of
    # letting every already-completed unit below re-enter `render_instance()`
    # (its own full-ledger `_recorded_render_sha()` rescan + a PCM read+
    # sha256) each time this stage resumes.
    completed_units = _render_index_from_ledger(campaign.ledger.entries)
    for unit in units:
        # Codex PR #345 round 4 finding #3 (adopted, category ③,
        # `[UNDERSPEC-CAL-D79]`): the O(1) index skip below is checked
        # BEFORE the budget expiry check — an already-completed unit is
        # never a "genuinely pending" dispatch, so it must not be blocked
        # by an expired budget. Pre-fix ordering had the budget check run
        # first: if rebuilding `completed_units` above (a full ledger scan)
        # itself consumed the whole `time_budget`, the very first loop
        # iteration would see `time_budget.expired() == True` and `break`
        # immediately — even when every remaining unit (up to and including
        # the last one) was already completed and only the phase transition
        # below (`fixture_valid` / handoff to measure) was left. Repeated
        # resumes with the same small budget would then never reach that
        # transition. Consulting the index first makes every already-
        # completed unit's O(1) skip budget-independent; only a unit that
        # actually needs `render_instance()` dispatched is subject to the
        # budget boundary below.
        recorded_sha = completed_units.get((unit.row_id, unit.probe_index))
        if recorded_sha is not None:
            # Already completed in a prior invocation — skip re-entering
            # `render_instance()` entirely: no ledger rescan, no PCM
            # read/hash. This is what makes a resumed slice's completed
            # prefix cost O(1) per unit instead of O(ledger) per unit, so a
            # small `--time-budget-seconds` slice reaches unfinished work
            # instead of expiring on the completed prefix. PCM staleness is
            # still fail-closed at measurement time
            # (`measure_stage._verify_and_load_rendered_pcm`), which every
            # rendered unit passes through before being measured.
            outcomes.append(
                RenderOutcome(
                    row_id=unit.row_id,
                    probe_index=unit.probe_index,
                    status="skipped_resume",
                    sha256=recorded_sha,
                )
            )
            continue
        # R2 instance boundary: checked before dispatching a NEW (genuinely
        # pending) unit — a unit already in flight always runs to
        # completion. An already-completed unit above never reaches this
        # check (see finding #3 note above).
        if time_budget is not None and time_budget.expired():
            completed_all = False
            break
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
                invocation_id=invocation_id,
            )
        except RenderNondeterministicError as exc:
            campaign.ledger.append(
                {
                    "kind": "stop_event",
                    "reason": BlockedCode.BLOCKED_C1_GENERATOR_NONDETERMINISTIC.value,
                    "row_id": unit.row_id,
                    "probe_index": unit.probe_index,
                    "stage": stage,
                    "invocation_id": invocation_id,
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
                    "invocation_id": invocation_id,
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
                    # round 8 finding #2 (R8-2, `[UNDERSPEC-CAL-D79]`): this
                    # process's own invocation_id (see
                    # `caps.cap_counters_from_ledger()`'s pairing-rule
                    # docstring).
                    "invocation_id": invocation_id,
                }
            )

    if completed_all:
        # F5 (round 3 ADOPT ③, `[UNDERSPEC-CAL-D79]`): this is a
        # "completing invocation" — one that is about to transition the
        # stage (c1's `fixture_valid` below, or hand c4-holdout's render
        # sub-phase off to its measure sub-phase). A `PARTIAL_SLICE` exit
        # (`completed_all=False`) never reaches here, so the O(1) index skip
        # above stays untouched for every other invocation. Validate every
        # `skipped_resume` unit THIS call produced once, before allowing the
        # transition, so a deleted/corrupted PCM under an already-recorded
        # `render` event cannot silently ride a false phase transition.
        #
        # round 5 finding S3 (adopted, category ③, `[UNDERSPEC-CAL-D79]`):
        # for `stage="c1"`, "completing invocation" truly means "the only
        # one" — `cli._stage_already_complete()` makes every later c1 call a
        # NOOP before `run_render_stage()` is even invoked again once
        # `fixture_valid` is recorded, so validating unconditionally here is
        # already exactly-once. `stage="c4"` has no such outer guard while
        # its paired measure sub-phase (`holdout_stage.render_and_measure_
        # holdout()`) still has slices left: once every c4 render unit is
        # done, `completed_all=True` on EVERY subsequent call too (nothing
        # left to dispatch), so this block re-ran — and re-hashed every
        # `skipped_resume` PCM from scratch — on every measure-only slice,
        # competing with measurement for the same `time_budget` instead of
        # running once at the real render→measure transition.
        # `RENDER_PHASE_VALID_KIND` makes that one-time validation durable:
        # once recorded for `stage="c4"`, later completing invocations of
        # the SAME stage skip the scan (and the marker append) entirely.
        already_valid = stage == "c4" and _render_phase_already_valid(
            campaign.ledger.entries, stage
        )
        if not already_valid:
            failing_units = _validate_skipped_resume_outcomes(campaign, outcomes)
            if failing_units:
                campaign.ledger.append(
                    {
                        "kind": "stop_event",
                        "reason": "RENDER_RESUME_INDEX_INTEGRITY_MISMATCH",
                        "stage": stage,
                        "units": [
                            {"row_id": rid, "probe_index": pidx, "detail": detail}
                            for rid, pidx, detail in failing_units
                        ],
                        "invocation_id": invocation_id,
                    }
                )
                raise RenderResumeIndexIntegrityError(stage, failing_units)
            if stage == "c4":
                campaign.ledger.append(
                    {
                        "kind": RENDER_PHASE_VALID_KIND,
                        "stage": stage,
                        "instance_count": len({(u.row_id, u.probe_index) for u in units}),
                        "invocation_id": invocation_id,
                    }
                )

    if stage == "c1" and completed_all:
        campaign.ledger.append(
            {
                "kind": "fixture_valid",
                "instance_count": len({(u.row_id, u.probe_index) for u in units}),
                "invocation_id": invocation_id,
            }
        )
    if time_budget is None:
        return tuple(outcomes)
    # F6 (round 3 ADOPT ②): `instances_completed_this_run` counts only units
    # newly rendered by THIS invocation — a resumed slice's `skipped_resume`
    # units (the O(1) index skip above) are prior work, not this run's
    # progress. Pre-fix `len(outcomes)` counted both, so a resumed slice
    # that traversed only the already-completed prefix (0 new renders)
    # still reported nonzero progress.
    newly_rendered_count = sum(1 for o in outcomes if o.status == "rendered")
    # rehearsal 4 finding G (adopted, `[UNDERSPEC-CAL-D79]`): `len(units) -
    # len(outcomes)` over-reported remaining whenever the budget expired
    # before this call's loop walked even its first unit (`outcomes` empty
    # -> `instances_remaining == len(units)`, ignoring every unit already
    # completed in a PRIOR invocation — rehearsal 4 observed
    # `instances_remaining` jump backward 77->85 at a 0.001s budget). The
    # true remaining count is `len(units)` minus the total now complete:
    # `completed_units` (built from the ledger at the TOP of this call, so
    # it already covers every unit finished in an earlier invocation) plus
    # `newly_rendered_count` (this call's own new work) — never dependent
    # on how far this call's own loop happened to walk.
    #
    # Codex PR #345 round 4 finding #2 (adopted, category ②,
    # `[UNDERSPEC-CAL-D79]`): `completed_units` is built from the WHOLE
    # ledger (`_render_index_from_ledger`), so on a sliced `c4-holdout`
    # render sub-phase it also contains every prior `render` event from the
    # `c1` stage — `len(completed_units)` over-counts against `len(units)`,
    # which holds only THIS stage's units. Scope the completed count to the
    # intersection with `units` (this stage's own `(row_id, probe_index)`
    # keys) instead of the raw index size, so a c4 slice run after C1 has
    # already rendered instances no longer under/over-counts (and never
    # goes negative).
    completed_in_stage = sum(
        1 for u in units if (u.row_id, u.probe_index) in completed_units
    )
    instances_remaining = len(units) - (completed_in_stage + newly_rendered_count)
    slice_status = SliceStatus(
        time_budget_seconds=time_budget.seconds,
        elapsed_seconds=time_budget.elapsed(),
        instances_completed_this_run=newly_rendered_count,
        instances_remaining=instances_remaining,
        completed_all=completed_all,
    )
    return tuple(outcomes), slice_status


__all__ = [
    "STRATUM_FACTOR_NAMES",
    "RenderNondeterministicError",
    "RenderStaleError",
    "RenderLeakageBlockedError",
    "RenderResumeIndexIntegrityError",
    "RenderOutcome",
    "RENDER_PHASE_VALID_KIND",
    "render_instance",
    "run_render_stage",
]
