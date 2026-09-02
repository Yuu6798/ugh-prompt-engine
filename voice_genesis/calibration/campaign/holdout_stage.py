"""C4 holdout stage: gate 判定 + 終端 status cascade
（IMPLEMENTATION_MAP_v1.md §6.4, 設計正本 §10, §11）。

2 層構成:

- **building blocks**（`build_instance_margins`/`build_directional_pairs`/
  `evaluate_absolute_meter`/`evaluate_directional_meter`）は
  `observables.py`/`gates.py`/`status.py` の純関数をそのまま呼ぶ薄いラッパー
  であり、pre-computed な観測値（真値・repeat 生値・E_use・U_GT/U_num）さえ
  与えれば real audio を経由せず単体テストできる（Task Brief「holdout stage
  producing terminal statuses (use synthetic observables where real meters
  are too slow)」）。
- **orchestration**（`render_and_measure_holdout`/`run_holdout_stage`）は
  `render_stage`/`measure_stage` を呼んで実 audio 上で上記 building blocks を
  組み立てる、実キャンペーン実行時の経路。

`HOLDOUT_EXECUTED_VALID` は **全 meter の終端 status + 理由コードをまとめた
単一 ledger event**（`per_meter` mapping）として記帳する（設計正本 §1: 手続
Gate は meter status とは別軸の 1 つの手続完了イベント）。

**meter 被覆検証**（finding #10, 第 10 巡採用）: `run_holdout_stage` は記帳
前に、渡された `results` が `vocab.MeterId` の全 7 値をちょうど 1 回ずつ
含むことを検証する（`per_meter` は `meter_id` キーの dict comprehension で
組み立てるため、検証なしでは重複が黙って上書きされ欠落を検出できない）。
欠落・重複・未知の meter_id はいずれも `HoldoutCoverageError` で
fail-closed する。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from voice_genesis.calibration.campaign import measure_stage, workunits
from voice_genesis.calibration.campaign.render_stage import run_render_stage
from voice_genesis.calibration.campaign.state import FrozenCampaign
from voice_genesis.calibration.candidates.registry import Candidate, candidate_by_id
from voice_genesis.calibration.cost_caps import CapCounters, CostCaps
from voice_genesis.calibration.e_use_table import row_from_dict
from voice_genesis.calibration.fixtures.matrix import MatrixRow
from voice_genesis.calibration.gates import (
    AbsoluteGateResult,
    DirectionalGateResult,
    DirectionalPair,
    EUseEvidenceRow,
    InstanceMargin,
    InvariancePair,
    absolute_gates,
    directional_gates,
)
from voice_genesis.calibration.observables import error_terms, two_stage_median
from voice_genesis.calibration.provenance import LedgerEntry
from voice_genesis.calibration.status import terminal_status
from voice_genesis.calibration.vocab import ClaimCeiling, Domain, MeterId, MissingReason, TerminalStatus

# ---------------------------------------------------------------------------
# frozen fixture_spec からの invariance 軸宣言 [UNDERSPEC-CAL-D18]
# ---------------------------------------------------------------------------

#: `[UNDERSPEC-CAL-D18]` 設計正本/`c0_freeze._fixture_specs()` は per-family
#: の「変動しうる軸名」を `frozen_design.fixture_spec.<FAMILY>.confound_axes`
#: として宣言するが、gate4' invariance axis・DIRECTIONAL gate の sweep_id と
#: して直接使う契約までは規定しない。本モジュールはこの `confound_axes` 列を
#: そのまま invariance axis 宣言（gate4'）/ sweep_id 宣言（directional gate）
#: として再利用する、最も単純な写像を採る。
def declared_axes_for_family(manifest: Mapping[str, object], family: str) -> tuple[str, ...]:
    frozen_design = manifest.get("frozen_design")
    if not isinstance(frozen_design, Mapping):
        return ()
    fixture_spec = frozen_design.get("fixture_spec")
    if not isinstance(fixture_spec, Mapping):
        return ()
    family_spec = fixture_spec.get(family)
    if not isinstance(family_spec, Mapping):
        return ()
    axes = family_spec.get("confound_axes")
    if not isinstance(axes, list):
        return ()
    return tuple(str(a) for a in axes)


# ---------------------------------------------------------------------------
# E_use table: relative/absolute row split
#
# `gates.EUseEvidenceRow.e_use_mode`（`[UNDERSPEC-CAL-D11]`、`gates.py`/
# `e_use_table.py` — 他 agent が同一セッションで並行実装した凍結列）が
# absolute/relative の権威ある判別列。本モジュールはそれをそのまま消費する
# だけで、独自の mode 推測ロジックは持たない。
# ---------------------------------------------------------------------------


class StaleEUseTableError(RuntimeError):
    """round 20 採用 (2): `load_e_use_rows()` が読んだ `e_use_table.json` の
    バイト列が、凍結 manifest の `frozen_inputs.e_use_table_sha256` pin と
    一致しない、または pin/ファイル自体が欠落している場合の fail-closed
    error（凍結後の改竄・欠落・pin 未設定のいずれも同じ経路で検出する）。"""


def _read_and_verify_e_use_table_bytes(campaign: FrozenCampaign) -> tuple[Path, bytes]:
    """`e_use_table.json` を **1 回だけ** `read_bytes()` し、そのバッファの
    sha256 を凍結 manifest の `frozen_inputs.e_use_table_sha256` pin と照合
    する。検証に使ったのと同一バッファを呼び出し側へ返す（TOCTOU 排除:
    `c0_freeze._check_e_use_table()`/`measure_stage._verify_and_load_rendered_pcm()`
    と同じ「1 read → 検証 → 同じバッファをパース」規約）。不一致・欠落は
    いずれも `StaleEUseTableError`。"""
    frozen_inputs = campaign.manifest.get("frozen_inputs")
    expected_sha256 = (
        frozen_inputs.get("e_use_table_sha256") if isinstance(frozen_inputs, Mapping) else None
    )
    if not isinstance(expected_sha256, str) or not expected_sha256:
        raise StaleEUseTableError(
            "load_e_use_rows: frozen manifest is missing a non-blank "
            "frozen_inputs.e_use_table_sha256 pin"
        )
    path = campaign.campaign_dir / "e_use_table.json"
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise StaleEUseTableError(f"load_e_use_rows: cannot read {path}: {exc}") from exc
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if actual_sha256 != expected_sha256:
        raise StaleEUseTableError(
            f"load_e_use_rows: {path} sha256 ({actual_sha256!r}) does not match frozen "
            f"manifest frozen_inputs.e_use_table_sha256 pin ({expected_sha256!r}) — "
            "the frozen E_use evidence table appears to have been mutated after freeze"
        )
    return path, data


def _parse_e_use_table_bytes(path: Path, data: bytes) -> list[EUseEvidenceRow]:
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StaleEUseTableError(f"load_e_use_rows: {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise StaleEUseTableError(
            f"load_e_use_rows: {path}: must contain a JSON array of row objects"
        )
    rows: list[EUseEvidenceRow] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise StaleEUseTableError(f"load_e_use_rows: {path}[{i}]: row must be a JSON object")
        try:
            rows.append(row_from_dict(entry))
        except (KeyError, ValueError, TypeError) as exc:
            raise StaleEUseTableError(f"load_e_use_rows: {path}[{i}]: {exc}") from exc
    return rows


def load_e_use_rows(campaign: FrozenCampaign) -> tuple[EUseEvidenceRow, ...]:
    """凍結 campaign dir 直下の `e_use_table.json`（`e_use_table.load_e_use_table`
    が読む 14 列 JSON 配列。armed `c0_freeze.armed_freeze()` がここへ配置する）
    を読む。

    round 20 採用 (2): 従来は欠落時に空タプルへ fail-soft していたが
    （「REQUIRED_BLOCKING の判定は C0 freeze 側の責務」という理由付け）、
    これは凍結後にファイルが差し替えられた/削除された場合でも holdout gate
    が「E_use evidence 無し」を静かに受理してしまう欠陥だった（レビュー
    round 20 finding #2）。本関数は now: (1) `e_use_table.json` を 1 回だけ
    `read_bytes()` し、(2) その sha256 を凍結 manifest の
    `frozen_inputs.e_use_table_sha256` pin と照合し、(3) 検証に使った同一
    バッファをパースする。ファイル欠落・pin 欠落・sha256 不一致はいずれも
    `StaleEUseTableError` を送出しつつ ledger `stop_event`
    （`reason: "E_USE_TABLE_STALE_OR_MUTATED"`）を記帳した上で fail-closed
    する（`measure_stage.run_measurement_for_instance` の
    `StaleMeasurementError`/`StaleRenderError` と同じ「catch → stop_event
    記帳 → re-raise」規約。空タプルを返す経路はもう存在しない）。"""
    try:
        path, data = _read_and_verify_e_use_table_bytes(campaign)
        rows = _parse_e_use_table_bytes(path, data)
    except StaleEUseTableError as exc:
        campaign.ledger.append(
            {
                "kind": "stop_event",
                "reason": "E_USE_TABLE_STALE_OR_MUTATED",
                "detail": str(exc),
            }
        )
        raise
    return tuple(rows)


def split_e_use_rows_by_mode(
    rows: Sequence[EUseEvidenceRow],
) -> tuple[tuple[EUseEvidenceRow, ...], tuple[EUseEvidenceRow, ...]]:
    """`(absolute_rows, relative_rows)`。`EUseEvidenceRow.e_use_mode`
    （`gates.E_USE_MODE_VALUES`）で分割する。"absolute" 行の `e_use_value` は
    `InstanceMargin.e_use`/`threshold_margin()` へ絶対量としてそのまま渡せる。
    "relative" 行は construct 単位の 1 スカラー相対誤差であり、instance 単位の
    絶対 E_use 展開（`e_use_value * declared_truth`）は呼び出し側
    （`build_instance_margins` 呼び出し前の前処理）の責務のまま残る
    （`gates.EUseEvidenceRow` docstring 参照）。"""
    absolute_rows = tuple(r for r in rows if r.e_use_mode == "absolute")
    relative_rows = tuple(r for r in rows if r.e_use_mode == "relative")
    return absolute_rows, relative_rows


# ---------------------------------------------------------------------------
# building blocks: raw repeat outputs -> InstanceMargin / DirectionalPair
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawInstanceObservation:
    """1 instance の観測入力（すでに集計済み）。`per_process_repeats` は
    `{process_id: [raw_value, ...]}`（`observables.two_stage_median` の入力
    形そのもの）。"""

    instance_id: str
    domain: Domain
    truth: float
    per_process_repeats: Mapping[str, Sequence[float]]
    u_gt: float
    u_num: float
    e_use: float
    zero_guard: float = 1e-9
    eligible: bool = True


def build_instance_margins(
    observations: Sequence[RawInstanceObservation],
) -> list[InstanceMargin]:
    """`observables.two_stage_median`/`error_terms` を通して `InstanceMargin`
    を組み立てる（`gates.absolute_gates` の直接入力）。"""
    margins: list[InstanceMargin] = []
    for obs in observations:
        m = two_stage_median(obs.per_process_repeats)
        et = error_terms(m, obs.truth, obs.zero_guard)
        margins.append(
            InstanceMargin(
                instance_id=obs.instance_id,
                domain=obs.domain,
                eligible=obs.eligible,
                ae=et.ae,
                e=et.e,
                u_gt=obs.u_gt,
                u_num=obs.u_num,
                e_use=obs.e_use,
            )
        )
    return margins


@dataclass(frozen=True)
class RawDirectionalObservation:
    pair_id: str
    sweep_id: str
    delta_truth: float
    delta_output: float
    u_gt_i: float
    u_num_i: float
    u_gt_j: float
    u_num_j: float
    correct_sign: bool
    is_adjacent: bool


def build_directional_pairs(
    observations: Sequence[RawDirectionalObservation],
) -> list[DirectionalPair]:
    return [
        DirectionalPair(
            pair_id=o.pair_id,
            delta_truth=o.delta_truth,
            delta_output=o.delta_output,
            u_gt_i=o.u_gt_i,
            u_num_i=o.u_num_i,
            u_gt_j=o.u_gt_j,
            u_num_j=o.u_num_j,
            correct_sign=o.correct_sign,
            is_adjacent=o.is_adjacent,
            sweep_id=o.sweep_id,
        )
        for o in observations
    ]


# ---------------------------------------------------------------------------
# per-meter evaluation -> terminal status
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeterHoldoutResult:
    meter_id: str
    terminal_status: str
    reason_code: str | None
    ceiling: str
    selected_candidate_id: str | None
    gate_detail: Mapping[str, object]


def evaluate_absolute_meter(
    meter_id: str,
    ceiling: ClaimCeiling,
    *,
    selected_candidate_id: str | None,
    per_instance_margins: Sequence[InstanceMargin],
    u_rep: float,
    u_proc: float,
    invariance_pairs_by_axis: Mapping[str, Sequence[InvariancePair]],
    declared_invariance_axes: Collection[str],
    expected_primary_instance_ids: Collection[str],
    fdr0: float,
    fnr1: float,
    min_count_met: bool,
    procedure_breach: bool = False,
    evaluable: bool = True,
) -> MeterHoldoutResult:
    """§10.3 ABSOLUTE holdout gate 一式 → §11 first-match cascade。"""
    if not evaluable or not per_instance_margins:
        status = terminal_status(
            procedure_breach=procedure_breach,
            evaluable=False,
            ceiling=ceiling,
            absolute_gates_passed=False,
            directional_gates_passed=False,
        )
        reason = (
            MissingReason.PROCEDURE.value
            if procedure_breach
            else MissingReason.OUTPUT_NOT_EVALUABLE.value
        )
        return MeterHoldoutResult(
            meter_id=meter_id,
            terminal_status=status.value,
            reason_code=reason,
            ceiling=ceiling.value,
            selected_candidate_id=selected_candidate_id,
            gate_detail={"reason": "no evaluable PRIMARY instance"},
        )

    gate: AbsoluteGateResult = absolute_gates(
        per_instance_margins,
        u_rep=u_rep,
        u_proc=u_proc,
        invariance_pairs_by_axis=invariance_pairs_by_axis,
        declared_invariance_axes=declared_invariance_axes,
        expected_primary_instance_ids=expected_primary_instance_ids,
        fdr0=fdr0,
        fnr1=fnr1,
        min_count_met=min_count_met,
    )
    status = terminal_status(
        procedure_breach=procedure_breach,
        evaluable=True,
        ceiling=ceiling,
        absolute_gates_passed=gate.passed,
        directional_gates_passed=False,
    )
    reason = None
    if status == TerminalStatus.DIAGNOSTIC_ONLY:
        reason = MissingReason.OUTPUT_MISSING.value
    return MeterHoldoutResult(
        meter_id=meter_id,
        terminal_status=status.value,
        reason_code=reason,
        ceiling=ceiling.value,
        selected_candidate_id=selected_candidate_id,
        gate_detail={
            "passed": gate.passed,
            "failure_reasons": list(gate.failure_reasons),
        },
    )


def evaluate_directional_meter(
    meter_id: str,
    ceiling: ClaimCeiling,
    *,
    selected_candidate_id: str | None,
    pairs: Sequence[DirectionalPair],
    u_rep: float,
    u_proc: float,
    expected_sweep_ids: Collection[str],
    expected_adjacent_pair_ids: Mapping[str, Collection[str]] | None,
    negative_control_failures: int,
    positive_control_failures: int,
    units_commensurate: bool,
    procedure_breach: bool = False,
    evaluable: bool = True,
) -> MeterHoldoutResult:
    """§10.4 DIRECTIONAL holdout gate 一式 → §11 first-match cascade。"""
    if not evaluable or not pairs:
        status = terminal_status(
            procedure_breach=procedure_breach,
            evaluable=False,
            ceiling=ceiling,
            absolute_gates_passed=False,
            directional_gates_passed=False,
        )
        reason = (
            MissingReason.PROCEDURE.value
            if procedure_breach
            else MissingReason.OUTPUT_NOT_EVALUABLE.value
        )
        return MeterHoldoutResult(
            meter_id=meter_id,
            terminal_status=status.value,
            reason_code=reason,
            ceiling=ceiling.value,
            selected_candidate_id=selected_candidate_id,
            gate_detail={"reason": "no evaluable directional pair"},
        )

    gate: DirectionalGateResult = directional_gates(
        pairs,
        u_rep=u_rep,
        u_proc=u_proc,
        expected_sweep_ids=expected_sweep_ids,
        expected_adjacent_pair_ids=expected_adjacent_pair_ids,
        negative_control_failures=negative_control_failures,
        positive_control_failures=positive_control_failures,
        units_commensurate=units_commensurate,
    )
    status = terminal_status(
        procedure_breach=procedure_breach,
        evaluable=True,
        ceiling=ceiling,
        absolute_gates_passed=False,
        directional_gates_passed=gate.passed,
    )
    reason = MissingReason.OUTPUT_MISSING.value if status == TerminalStatus.DIAGNOSTIC_ONLY else None
    return MeterHoldoutResult(
        meter_id=meter_id,
        terminal_status=status.value,
        reason_code=reason,
        ceiling=ceiling.value,
        selected_candidate_id=selected_candidate_id,
        gate_detail={
            "passed": gate.passed,
            "failure_reasons": list(gate.failure_reasons),
            "resolvable_count": gate.resolvable_count,
        },
    )


def diagnostic_only_close(
    meter_id: str, *, selected_candidate_id: str | None = None, reason: str = "§16-1"
) -> MeterHoldoutResult:
    """§16-1: M4 は全候補 DIAGNOSTIC_ONLY 上限で closes（selection を回さない）。"""
    return MeterHoldoutResult(
        meter_id=meter_id,
        terminal_status=TerminalStatus.DIAGNOSTIC_ONLY.value,
        reason_code=None,
        ceiling=ClaimCeiling.DIAGNOSTIC_ONLY.value,
        selected_candidate_id=selected_candidate_id,
        gate_detail={"reason": reason},
    )


def selection_failed_closed_meter(meter_id: str) -> MeterHoldoutResult:
    """§9: 全候補 fail → `SELECTION_FAILED_CLOSED`、meter は NOT_EVALUABLE。"""
    return MeterHoldoutResult(
        meter_id=meter_id,
        terminal_status=TerminalStatus.NOT_EVALUABLE.value,
        reason_code=MissingReason.OUTPUT_NOT_EVALUABLE.value,
        ceiling=ClaimCeiling.NONE.value,
        selected_candidate_id=None,
        gate_detail={"reason": "SELECTION_FAILED_CLOSED"},
    )


# ---------------------------------------------------------------------------
# orchestration: real render + measure on the holdout split
# ---------------------------------------------------------------------------


def render_and_measure_holdout(
    campaign: FrozenCampaign,
    matrix_rows: Sequence[MatrixRow],
    *,
    candidates_by_family: Mapping[str, Sequence[Candidate]],
    max_workers: int = 1,
    f0_by_instance: Mapping[tuple[str, int], float] | None = None,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
) -> dict[str, list[measure_stage.MeasurementRecord]]:
    """C4: holdout 非 control 行を render（determinism 検査つき。§7 leakage
    検査は `render_stage.run_render_stage` が行う）→ family ごとに指定
    candidate（選択済み候補 + B0）で測定する。戻り値は
    `{family: [MeasurementRecord, ...]}`。`f0_by_instance`（finding #2）は
    素通しで `measure_stage.run_measure_stage` へ渡す — 呼び出し元
    (`cli._run_c4`) が C3b と同じ規約（選択済み F0 candidate の instance 単位
    実測、fixture truth は使わない）で構築する。`cap_counters`/`cost_caps`
    （finding #1）は render/measure 双方へ素通しする。"""
    run_render_stage(campaign, matrix_rows, stage="c4", cap_counters=cap_counters, cost_caps=cost_caps)

    assignment = campaign.realized_split.assignment
    sr_by_row = {mr.row_id: mr.row.sr_hz for mr in matrix_rows}
    results: dict[str, list[measure_stage.MeasurementRecord]] = {}
    for family, candidates in sorted(candidates_by_family.items()):
        instances = workunits.c4_holdout_instances(matrix_rows, assignment, family=family)
        results[family] = measure_stage.run_measure_stage(
            campaign,
            instances,
            candidates,
            sr_by_row=sr_by_row,
            f0_by_instance=f0_by_instance,
            max_workers=max_workers,
            cap_counters=cap_counters,
            cost_caps=cost_caps,
        )
    return results


def resolve_candidates(candidate_ids: Sequence[str]) -> tuple[Candidate, ...]:
    return tuple(candidate_by_id(cid) for cid in candidate_ids)


# ---------------------------------------------------------------------------
# HOLDOUT_EXECUTED_VALID event
# ---------------------------------------------------------------------------


class HoldoutCoverageError(RuntimeError):
    """finding #10: `run_holdout_stage` に渡された `results` が
    `vocab.MeterId` の全 7 値をちょうど 1 回ずつ含まない場合の fail-closed
    error（欠落・重複・未知の meter_id のいずれか）。"""


def _validate_meter_coverage(results: Sequence[MeterHoldoutResult]) -> None:
    seen = [r.meter_id for r in results]
    counts = Counter(seen)
    duplicates = sorted(m for m, n in counts.items() if n > 1)
    expected = {m.value for m in MeterId}
    missing = sorted(expected - set(seen))
    unexpected = sorted(set(seen) - expected)
    if duplicates or missing or unexpected:
        raise HoldoutCoverageError(
            "run_holdout_stage: results must cover exactly the "
            f"{len(expected)} vocab.MeterId values with no duplicates "
            f"(duplicates={duplicates!r}, missing={missing!r}, unexpected={unexpected!r})"
        )


def run_holdout_stage(
    campaign: FrozenCampaign, results: Sequence[MeterHoldoutResult]
) -> LedgerEntry:
    """全 meter の評価結果を単一 `holdout_executed_valid` event として記帳
    する（設計正本 §1: 手続 Gate は meter status とは別軸だが、本 event 自体
    は全 meter の終端 status + reason code を payload に持つ）。

    finding #10: 記帳前に `_validate_meter_coverage` で `results` の被覆を
    検証する（`per_meter` は `meter_id` キーの dict comprehension のため、
    検証なしでは重複が黙って上書きされ欠落を検出できない — fail-closed で
    `HoldoutCoverageError` を送出する）。
    """
    _validate_meter_coverage(results)
    per_meter = {
        r.meter_id: {
            "terminal_status": r.terminal_status,
            "reason_code": r.reason_code,
            "ceiling": r.ceiling,
            "selected_candidate_id": r.selected_candidate_id,
            "gate_detail": dict(r.gate_detail),
        }
        for r in results
    }
    return campaign.ledger.append({"kind": "holdout_executed_valid", "per_meter": per_meter})


__all__ = [
    "declared_axes_for_family",
    "StaleEUseTableError",
    "load_e_use_rows",
    "split_e_use_rows_by_mode",
    "RawInstanceObservation",
    "build_instance_margins",
    "RawDirectionalObservation",
    "build_directional_pairs",
    "MeterHoldoutResult",
    "evaluate_absolute_meter",
    "evaluate_directional_meter",
    "diagnostic_only_close",
    "selection_failed_closed_meter",
    "render_and_measure_holdout",
    "resolve_candidates",
    "HoldoutCoverageError",
    "run_holdout_stage",
]
