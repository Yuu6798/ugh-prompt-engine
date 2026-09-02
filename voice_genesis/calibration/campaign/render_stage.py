"""C1/C4 render stage（IMPLEMENTATION_MAP_v1.md §6.4）。

- 各 instance を `render_root_secret` から streams 派生した RNG で 2 回
  fresh-process render し（subprocess worker `_render_worker.py`）、byte 一致
  を要求する（違反 → `BLOCKED_C1_GENERATOR_NONDETERMINISTIC` stop event を
  ledger へ記帳し `RenderNondeterministicError` で fail-closed）。
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
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from voice_genesis.calibration.campaign import workunits
from voice_genesis.calibration.campaign.caps import CostCapExceededError, save_cap_counters
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
    skip しなかった）unit のみ compute（2 回の subprocess render の実測
    経過時間）/storage（書き込んだ PCM bytes 数）を計上する。cap 超過を
    検出したら `stop_event` ledger event を記帳し `CostCapExceededError` で
    fail-closed する — 呼び出し元 `run_render_stage` の次 unit には進まない。
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
    t0 = time.perf_counter()
    outputs: list[str] = []
    for _ in range(2):
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "voice_genesis.calibration.campaign._render_worker",
                payload_json,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=True,
        )
        outputs.append(proc.stdout.strip())
    elapsed = time.perf_counter() - t0
    a, b = outputs
    if a != b:
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
        cap_counters.add(compute=elapsed, storage=len(pcm_bytes), budget=budget_charge)
        # Persist immediately (finding #1: counters must survive across
        # subcommands) — before the breach check below.
        save_cap_counters(campaign.campaign_dir, cap_counters)
        if cost_caps is not None:
            decision = cost_caps_check(cap_counters, cost_caps)
            if decision is not None:
                campaign.ledger.append(decision.event_payload)
                raise CostCapExceededError(decision.detail)

    return RenderOutcome(row_id=row_id, probe_index=probe_index, status="rendered", sha256=sha)


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
