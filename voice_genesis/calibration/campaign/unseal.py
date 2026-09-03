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
   する（memo §6.4「seal 保護水準の受容」承認）。memo §6.4 は Gate 3 を
   「C0 freeze **後**に成立する」承認として定義するため（`UNDERSPEC-CAL-D85`,
   #345 指摘②）、承認ファイルの `approved_at_utc` が campaign の `c0_freeze`
   ledger event（`FrozenCampaign.freeze_event["event_time_utc"]`）より厳密に
   後であることも検証する — 早い/同時刻/パース不能はすべて fail-closed
   で拒否する（`_parse_iso8601_utc`）。
3. `holdout_unseal` event を記帳する（`selection_freeze_event_sha` +
   `selection_frozen` と同一の 4 前提 sha を copy、加えて 2 の `gate3_accepted`
   event を参照する `gate3_accepted_sha`）。これは `provenance.Ledger.check_leakage`
   が使う暗号学的な unseal 境界そのもの（`provenance._verified_holdout_unseal_seq`）
   を満たす — `gate3_accepted_sha` は round 22 採用（`UNDERSPEC-CAL-D50`）で検証側
   （`_references_prior_gate3_acceptance`）が必須参照として要求するようになった。

Gate 3 承認・5-sha いずれかが欠ければ `UnsealError` を送出し、ledger には
何も追記しない（fail-closed。1 でも欠けた状態で部分的な event を残さない
ため、検証を先に完了させてから `campaign.ledger.append` を呼ぶ）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
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


def _parse_iso8601_utc(value: object) -> datetime | None:
    """`provenance._is_iso8601_utc_timestamp`/`approvals._is_iso8601_utc_timestamp`
    と同じ意味論（`Z` または `+00:00` の明示 UTC オフセットのみ許容）で ISO 8601
    UTC 文字列を `datetime` へ変換する。不正・非 UTC・非文字列なら `None`
    （#345 指摘②, `UNDERSPEC-CAL-D85`: Gate 3 freeze-後発行検証専用）。
    `approvals.py`/`provenance.py` は本 fix の編集対象外のため、両モジュールの
    docstring が既に採用している「独立実装として重複させる」方針を踏襲する
    （import による cross-module 結合を増やさない）。"""
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return parsed


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


def unseal_campaign(
    campaign: FrozenCampaign,
    *,
    approval_dir: Path,
    invocation_id: str | None = None,
) -> UnsealResult:
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

    # #345 指摘②（`UNDERSPEC-CAL-D85`）: IMPLEMENTATION_MAP_v1.md §6.4 は Gate 3
    # を「C0 freeze **後**に成立する」承認として定義するが、この束縛は従来
    # `seal_protection_level_accepted`/booleans/hash のみを検証しており、
    # freeze 前に発行された Gate 3 承認ファイルでも通ってしまっていた。ここで
    # `gate3.approved_at_utc` が campaign の `c0_freeze` ledger event
    # （`campaign.freeze_event["event_time_utc"]`、`c0_freeze.armed_freeze()`
    # が刻む値）より厳密に後であることを要求する。早い/同時刻/どちらかが
    # パース不能ならすべて fail-closed で拒否する（BlockedCode は使わない —
    # 既存の "Gate 3 not approved" 経路と同じ pre-dispatch 拒否スタイル。
    # ledger には一切書き込まない）。
    freeze_time = _parse_iso8601_utc(campaign.freeze_event.get("event_time_utc"))
    gate3_time = _parse_iso8601_utc(gate3_result.record.approved_at_utc)
    if freeze_time is None or gate3_time is None or gate3_time <= freeze_time:
        raise UnsealError(
            "Gate 3 (seal acceptance) not approved: approval_file:gate3_predates_freeze"
        )

    gate3_entry = campaign.ledger.append(
        {
            "kind": "gate3_accepted",
            "approval_content_sha256": gate3_result.content_sha256,
            "seal_protection_level_accepted": gate3_result.record.seal_protection_level_accepted,
            "approver": gate3_result.record.approver,
            "approved_at_utc": gate3_result.record.approved_at_utc,
            "invocation_id": invocation_id,
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
            "invocation_id": invocation_id,
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
