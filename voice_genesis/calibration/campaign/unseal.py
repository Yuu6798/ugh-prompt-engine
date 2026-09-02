"""unseal: §7 の 5 sha 相互参照検査 + Gate 3 束縛（IMPLEMENTATION_MAP_v1.md
§6.4）。

設計正本 §7: `unseal 条件は baseline_audit_sha + candidate_space_sha +
selection_rule_sha + selected_candidate_sha + selection_freeze_event_sha
の存在と相互参照一致`。本モジュールは:

1. ledger 上の `selection_frozen` event（`selection_stage.run_c3b_selection`
   が記帳したもの）を探し、その 4 前提 sha（いずれも参照先イベントの
   `entry_sha`）が実在し正しい `kind` を指すことを検証する
   （`verify_unseal_prerequisites`）。
2. Gate 3（`approvals.Gate.GATE3_SEAL_ACCEPTANCE`）の
   `seal_protection_level_accepted` 承認を読み、`GATE3_ACCEPTED` ledger
   event（承認ファイルの content sha256 を記録）を **unseal より前に** 記帳
   する（memo §6.4「seal 保護水準の受容」承認）。
3. `holdout_unseal` event を記帳する（`selection_freeze_event_sha` +
   `selection_frozen` と同一の 4 前提 sha を copy）。これは
   `provenance.Ledger.check_leakage` が使う暗号学的な unseal 境界そのもの
   （`provenance._verified_holdout_unseal_seq`）を満たす。

Gate 3 承認・5-sha いずれかが欠ければ `UnsealError` を送出し、ledger には
何も追記しない（fail-closed。1 でも欠けた状態で部分的な event を残さない
ため、検証を先に完了させてから `campaign.ledger.append` を呼ぶ）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from voice_genesis.calibration.approvals import Gate, load_approval
from voice_genesis.calibration.campaign.state import FrozenCampaign
from voice_genesis.calibration.provenance import LedgerEntry

_PREREQUISITE_KIND_FOR_KEY: Mapping[str, str] = {
    "baseline_audit_sha": "baseline_audit",
    "candidate_space_sha": "candidate_space",
    "selection_rule_sha": "selection_rule",
    "selected_candidate_sha": "selected_candidate",
}


class UnsealError(RuntimeError):
    """5-sha 相互参照検査または Gate 3 承認が失敗した際の fail-closed error。"""


def _find_last_entry_of_kind(entries: list[LedgerEntry], kind: str) -> LedgerEntry | None:
    result: LedgerEntry | None = None
    for entry in entries:
        payload = entry.payload
        if isinstance(payload, Mapping) and payload.get("kind") == kind:
            result = entry
    return result


def verify_unseal_prerequisites(campaign: FrozenCampaign) -> LedgerEntry:
    """`selection_frozen` event を見つけ、その 4 前提 sha（`entry_sha` 参照）
    が実在し正しい `kind` を指すことを検証する。見つからない/壊れていれば
    `UnsealError`。成功すれば `selection_frozen` の `LedgerEntry` を返す。"""
    entries = list(campaign.ledger.entries)
    entries_by_sha = {e.entry_sha: e for e in entries}
    sf_entry = _find_last_entry_of_kind(entries, "selection_frozen")
    if sf_entry is None:
        raise UnsealError("no selection_frozen event found in ledger")
    payload = sf_entry.payload
    if not isinstance(payload, Mapping):
        raise UnsealError("selection_frozen event payload is malformed")
    for key, expected_kind in _PREREQUISITE_KIND_FOR_KEY.items():
        ref = payload.get(key)
        if not isinstance(ref, str) or not ref:
            raise UnsealError(f"selection_frozen is missing {key}")
        prerequisite = entries_by_sha.get(ref)
        if prerequisite is None:
            raise UnsealError(
                f"selection_frozen.{key}={ref!r} does not reference an existing ledger entry"
            )
        prereq_payload = prerequisite.payload
        if not isinstance(prereq_payload, Mapping) or prereq_payload.get("kind") != expected_kind:
            raise UnsealError(f"{key} does not reference a kind={expected_kind!r} event")
        if not isinstance(prereq_payload.get("artifact_sha"), str):
            raise UnsealError(f"{key} prerequisite event is missing artifact_sha")
    return sf_entry


@dataclass(frozen=True)
class UnsealResult:
    gate3_accepted_entry_sha: str
    holdout_unseal_entry_sha: str
    selection_frozen_entry_sha: str


def unseal_campaign(campaign: FrozenCampaign, *, approval_dir: Path) -> UnsealResult:
    """§7 の 5-sha 相互参照検査 → Gate 3 承認検証 → `GATE3_ACCEPTED` event →
    `holdout_unseal` event の順で実行する。いずれかが失敗すれば `UnsealError`
    を送出し ledger には一切書き込まない。"""
    sf_entry = verify_unseal_prerequisites(campaign)

    gate3_result = load_approval(Gate.GATE3_SEAL_ACCEPTANCE, approval_dir)
    if (
        not gate3_result.approved
        or gate3_result.record is None
        or not gate3_result.record.seal_protection_level_accepted
    ):
        reasons = "; ".join(gate3_result.reasons) or "seal_protection_level_accepted is not true"
        raise UnsealError(f"Gate 3 (seal acceptance) not approved: {reasons}")

    gate3_entry = campaign.ledger.append(
        {
            "kind": "gate3_accepted",
            "approval_content_sha256": gate3_result.content_sha256,
            "seal_protection_level_accepted": gate3_result.record.seal_protection_level_accepted,
            "approver": gate3_result.record.approver,
            "approved_at_utc": gate3_result.record.approved_at_utc,
        }
    )

    payload = sf_entry.payload
    assert isinstance(payload, Mapping)  # verified by verify_unseal_prerequisites
    unseal_entry = campaign.ledger.append(
        {
            "kind": "holdout_unseal",
            "selection_freeze_event_sha": sf_entry.entry_sha,
            "baseline_audit_sha": payload["baseline_audit_sha"],
            "candidate_space_sha": payload["candidate_space_sha"],
            "selection_rule_sha": payload["selection_rule_sha"],
            "selected_candidate_sha": payload["selected_candidate_sha"],
            "gate3_accepted_sha": gate3_entry.entry_sha,
        }
    )
    return UnsealResult(
        gate3_accepted_entry_sha=gate3_entry.entry_sha,
        holdout_unseal_entry_sha=unseal_entry.entry_sha,
        selection_frozen_entry_sha=sf_entry.entry_sha,
    )


__all__ = [
    "UnsealError",
    "verify_unseal_prerequisites",
    "UnsealResult",
    "unseal_campaign",
]
