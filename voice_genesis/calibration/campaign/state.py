"""凍結 campaign dir の読み込み + 手続フェーズ導出（`c0_freeze.py` §6.2 layout,
`provenance.py` ledger, `IMPLEMENTATION_MAP_v1.md` §6.4）。

`load_frozen_campaign()` は `c0_freeze.armed_freeze()` が公開した
`campaigns/<campaign_id>/{c0_manifest.json, realized_split.json, ledger.jsonl}`
一式 + `<secret_dir>/<campaign_id>/{split_secret.bin, render_root_secret.bin}`
を読み込み、以下を検証する（いずれか 1 つでも失敗すれば fail-closed で
`CampaignStateError` を送出する。書込は一切行わない）:

- `ledger.jsonl` の chain 検証（`provenance.Ledger.verify_chain`）
- ledger の先頭行が `kind="c0_freeze"` かつ manifest の `campaign_id` と一致
- **orphan 検出**（`c0_freeze.detect_orphans()` と同じ fail-closed 意味論を
  読み込み時点で単体適用する — campaign dir はあるが対応する secret dir が
  無ければ実行を拒否する。本モジュールは orphan secret dir の削除は行わない
  — それは `c0_freeze.detect_orphans()` 自身の責務であり、他 agent が編集中の
  `c0_freeze.py` には一切触れない）
- secret file 2 件の実バイト列が manifest `commitments` の sha256 と一致
- `realized_split` インライン表の `realized_sha` が manifest 記載値と一致

`[UNDERSPEC-CAL-D19]` 設計正本 / IMPLEMENTATION_MAP は D2 の手続 Gate 状態を
`vocab.ProcedureGate`（5 値: PREPARATION_VALID/FIXTURE_VALID/BASELINE_AUDITED/
SELECTION_FROZEN/HOLDOUT_EXECUTED_VALID）としてのみ閉じるが、Task Brief は
D2 runner 固有の中間状態（F0_SELECTION_FROZEN/UNSEALED/CAMPAIGN_CLOSED）も
要求する。ここでは `vocab.ProcedureGate` を書き換えず、D2 runner 専用の
`CampaignPhase` 拡張 8 値を新規定義し、`vocab.ProcedureGate` と 1:1 対応する
部分列（5 値）についてのみ `vocab.procedure_gates_monotonic()` へ委譲して
単調性を検査する（Task Brief 「vocab.ProcedureGate monotonicity enforcement」
の要求はこの委譲で満たす）。拡張 8 値全体の単調性は本モジュール自身の
`CAMPAIGN_PHASE_ORDER` で追加検査する。

ledger event の `kind` → `CampaignPhase` 写像はここで新規に定める語彙であり
（`render_stage.py`/`baseline_stage.py`/`selection_stage.py`/`unseal.py`/
`holdout_stage.py`/`close.py` がこれらの kind で event を記帳する）、
`provenance.py` の unseal 5-sha 相互参照が要求する予約 kind
（`baseline_audit`/`candidate_space`/`selection_rule`/`selected_candidate`/
`selection_frozen`/`holdout_unseal`）とは別レイヤの「フェーズ到達の目印」で
ある点に注意（両者は多くの kind 名を共有するが、フェーズ導出は kind の存在
のみを見る単純な集合演算であり、`provenance.Ledger.check_leakage` が行う
暗号学的な相互参照検証を代替しない）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from voice_genesis.calibration.provenance import Ledger, LedgerEntry
from voice_genesis.calibration.splitter import RealizedSplitMap, SwapRecord
from voice_genesis.calibration.vocab import ProcedureGate, Split, procedure_gates_monotonic


class CampaignStateError(RuntimeError):
    """凍結 campaign dir の読み込み・整合性検証が失敗した際の fail-closed error。"""


class CampaignPhase(str, Enum):
    """D2 runner 固有の手続フェーズ（`[UNDERSPEC-CAL-D19]`、モジュール docstring
    参照）。前半 5 値は `vocab.ProcedureGate` と同名同順で 1:1 対応する。"""

    PREPARATION_VALID = "PREPARATION_VALID"
    FIXTURE_VALID = "FIXTURE_VALID"
    BASELINE_AUDITED = "BASELINE_AUDITED"
    F0_SELECTION_FROZEN = "F0_SELECTION_FROZEN"
    SELECTION_FROZEN = "SELECTION_FROZEN"
    UNSEALED = "UNSEALED"
    HOLDOUT_EXECUTED_VALID = "HOLDOUT_EXECUTED_VALID"
    CAMPAIGN_CLOSED = "CAMPAIGN_CLOSED"


CAMPAIGN_PHASE_ORDER: tuple[CampaignPhase, ...] = tuple(CampaignPhase)

#: `CampaignPhase` のうち `vocab.ProcedureGate` と 1:1 対応する部分（設計正本
#: §1 の 5 gate そのもの）。
_VOCAB_GATE_FOR_PHASE: Mapping[CampaignPhase, ProcedureGate] = {
    CampaignPhase.PREPARATION_VALID: ProcedureGate.PREPARATION_VALID,
    CampaignPhase.FIXTURE_VALID: ProcedureGate.FIXTURE_VALID,
    CampaignPhase.BASELINE_AUDITED: ProcedureGate.BASELINE_AUDITED,
    CampaignPhase.SELECTION_FROZEN: ProcedureGate.SELECTION_FROZEN,
    CampaignPhase.HOLDOUT_EXECUTED_VALID: ProcedureGate.HOLDOUT_EXECUTED_VALID,
}

#: ledger event `payload["kind"]` → 到達した `CampaignPhase`。本パッケージの
#: 各 stage モジュールがこれらの kind で event を記帳する契約。
LEDGER_KIND_FOR_PHASE: Mapping[str, CampaignPhase] = {
    "c0_freeze": CampaignPhase.PREPARATION_VALID,
    "fixture_valid": CampaignPhase.FIXTURE_VALID,
    "baseline_audited": CampaignPhase.BASELINE_AUDITED,
    "f0_selection_frozen": CampaignPhase.F0_SELECTION_FROZEN,
    "selection_frozen": CampaignPhase.SELECTION_FROZEN,
    "holdout_unseal": CampaignPhase.UNSEALED,
    "holdout_executed_valid": CampaignPhase.HOLDOUT_EXECUTED_VALID,
    "campaign_closed": CampaignPhase.CAMPAIGN_CLOSED,
}


def phases_passed(ledger_entries: Sequence[LedgerEntry]) -> frozenset[CampaignPhase]:
    """ledger 全 entry を走査し、到達済み `CampaignPhase` 集合を返す
    （event の存在のみを見る — 暗号学的な相互参照検証は
    `unseal.verify_unseal_prerequisites()`/`provenance.Ledger.check_leakage`
    の責務）。"""
    passed: set[CampaignPhase] = set()
    for entry in ledger_entries:
        payload = entry.payload
        if not isinstance(payload, Mapping):
            continue
        kind = payload.get("kind")
        if not isinstance(kind, str):
            continue
        phase = LEDGER_KIND_FOR_PHASE.get(kind)
        if phase is not None:
            passed.add(phase)
    return frozenset(passed)


def gate_monotonicity_ok(passed: frozenset[CampaignPhase]) -> bool:
    """`passed` の単調性を 2 段で検査する: (1) `vocab.ProcedureGate` と 1:1
    対応する部分列を `vocab.procedure_gates_monotonic()` へ委譲、(2)
    `CampaignPhase` 拡張 8 値全体を `CAMPAIGN_PHASE_ORDER` 上で同じ規則
    （後段 PASS は前段全 PASS を含意する）で検査する。"""
    vocab_subset = {_VOCAB_GATE_FOR_PHASE[p] for p in passed if p in _VOCAB_GATE_FOR_PHASE}
    if not procedure_gates_monotonic(vocab_subset):
        return False
    for phase in passed:
        idx = CAMPAIGN_PHASE_ORDER.index(phase)
        for earlier in CAMPAIGN_PHASE_ORDER[:idx]:
            if earlier not in passed:
                return False
    return True


def current_phase(passed: frozenset[CampaignPhase]) -> CampaignPhase | None:
    """到達済み最終フェーズ（`CAMPAIGN_PHASE_ORDER` 上で最も後段のもの）。
    1 件も到達していなければ `None`。"""
    reached = [p for p in CAMPAIGN_PHASE_ORDER if p in passed]
    return reached[-1] if reached else None


# ---------------------------------------------------------------------------
# realized_split の manifest インライン表 <-> RealizedSplitMap
# ---------------------------------------------------------------------------


def _realized_split_from_manifest(d: Mapping[str, Any]) -> RealizedSplitMap:
    """`c0_freeze._realized_split_from_dict` と同一の manifest インライン形状
    (`stratum_factor_names`/`assignment`/`swaps`/`realized_sha`) を読む独立実装
    （`c0_freeze.py` は他 agent が並行編集中のため import しない）。"""
    try:
        assignment = {str(rid): Split(val) for rid, val in d["assignment"].items()}
        swaps = tuple(
            SwapRecord(
                row_id=s["row_id"],
                from_split=Split(s["from_split"]),
                to_split=Split(s["to_split"]),
                reason=s["reason"],
                hmac_key=s["hmac_key"],
                detail=s["detail"],
            )
            for s in d["swaps"]
        )
        return RealizedSplitMap(
            stratum_factor_names=tuple(d["stratum_factor_names"]),
            assignment=assignment,
            swaps=swaps,
            realized_sha=str(d["realized_sha"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CampaignStateError(f"malformed realized_split payload: {exc}") from exc


# ---------------------------------------------------------------------------
# FrozenCampaign
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrozenCampaign:
    """凍結 campaign dir + secret dir から読み込んだ、読み取り専用の実行文脈。"""

    campaign_id: str
    campaign_dir: Path
    secret_dir: Path
    manifest: Mapping[str, Any]
    realized_split: RealizedSplitMap
    split_secret: bytes
    render_root_secret: bytes
    ledger: Ledger
    freeze_event: Mapping[str, Any]

    @property
    def ledger_path(self) -> Path:
        return self.campaign_dir / "ledger.jsonl"

    @property
    def renders_dir(self) -> Path:
        return self.campaign_dir / "renders"

    @property
    def measurements_dir(self) -> Path:
        return self.campaign_dir / "measurements"

    def phases_passed(self) -> frozenset[CampaignPhase]:
        return phases_passed(self.ledger.entries)

    def current_phase(self) -> CampaignPhase | None:
        return current_phase(self.phases_passed())


def _read_json(path: Path, *, what: str) -> Any:
    if not path.is_file():
        raise CampaignStateError(f"missing {what}: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignStateError(f"cannot read {what} at {path}: {exc}") from exc


def load_frozen_campaign(campaign_dir: Path, secret_dir_root: Path) -> FrozenCampaign:
    """凍結 campaign dir を読み込み検証する。fail-closed（1 検証でも失敗すれば
    `CampaignStateError`）。書込は一切行わない。

    `secret_dir_root` は `c0_freeze.default_secret_dir()`（既定
    `~/.vg_cal/secrets/`）と同じ意味の親ディレクトリ — 実際の secret は
    `<secret_dir_root>/<campaign_id>/` 配下にある。
    """
    campaign_dir = Path(campaign_dir)
    manifest = _read_json(campaign_dir / "c0_manifest.json", what="c0_manifest.json")
    if not isinstance(manifest, Mapping):
        raise CampaignStateError("c0_manifest.json must contain a JSON object")

    campaign_id = manifest.get("campaign_id")
    if not isinstance(campaign_id, str) or not campaign_id:
        raise CampaignStateError("manifest is missing a non-blank campaign_id")

    ledger = Ledger(campaign_dir / "ledger.jsonl")
    chain = ledger.verify_chain()
    if not chain.ok:
        raise CampaignStateError(f"ledger chain invalid: {chain.detail}")
    entries = ledger.entries
    if not entries:
        raise CampaignStateError("ledger has no entries (missing c0_freeze event)")
    freeze_event = entries[0].payload
    if not isinstance(freeze_event, Mapping) or freeze_event.get("kind") != "c0_freeze":
        raise CampaignStateError("ledger does not open with a kind='c0_freeze' event")
    if freeze_event.get("campaign_id") != campaign_id:
        raise CampaignStateError(
            "freeze event campaign_id does not match manifest campaign_id"
        )

    # Orphan detection (read-only mirror of `c0_freeze.detect_orphans()` fail-closed
    # semantics): a campaign dir without a matching secret dir is refused outright.
    secret_dir = Path(secret_dir_root) / campaign_id
    if not secret_dir.is_dir():
        raise CampaignStateError(
            f"orphan campaign: {campaign_dir} has no matching secret dir at "
            f"{secret_dir} (refusing to run against a campaign with missing secrets)"
        )

    split_secret_path = secret_dir / "split_secret.bin"
    render_root_secret_path = secret_dir / "render_root_secret.bin"
    if not split_secret_path.is_file() or not render_root_secret_path.is_file():
        raise CampaignStateError(
            f"secret dir {secret_dir} is missing split_secret.bin/render_root_secret.bin"
        )
    split_secret = split_secret_path.read_bytes()
    render_root_secret = render_root_secret_path.read_bytes()

    commitments = manifest.get("commitments")
    if not isinstance(commitments, Mapping):
        raise CampaignStateError("manifest is missing a commitments section")
    if hashlib.sha256(split_secret).hexdigest() != commitments.get("split_secret_sha256"):
        raise CampaignStateError("split_secret does not match manifest commitment")
    if (
        hashlib.sha256(render_root_secret).hexdigest()
        != commitments.get("render_root_secret_sha256")
    ):
        raise CampaignStateError("render_root_secret does not match manifest commitment")

    realized_split_raw = manifest.get("realized_split")
    if not isinstance(realized_split_raw, Mapping):
        raise CampaignStateError("manifest is missing a realized_split section")
    realized_split = _realized_split_from_manifest(realized_split_raw)
    if realized_split.realized_sha != manifest.get("realized_split_sha"):
        raise CampaignStateError("realized_split_sha does not match manifest")

    return FrozenCampaign(
        campaign_id=campaign_id,
        campaign_dir=campaign_dir,
        secret_dir=secret_dir,
        manifest=manifest,
        realized_split=realized_split,
        split_secret=split_secret,
        render_root_secret=render_root_secret,
        ledger=ledger,
        freeze_event=freeze_event,
    )


__all__ = [
    "CampaignStateError",
    "CampaignPhase",
    "CAMPAIGN_PHASE_ORDER",
    "LEDGER_KIND_FOR_PHASE",
    "phases_passed",
    "gate_monotonicity_ok",
    "current_phase",
    "FrozenCampaign",
    "load_frozen_campaign",
]
