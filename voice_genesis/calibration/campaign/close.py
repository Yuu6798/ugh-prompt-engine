"""CAMPAIGN_CLOSED + `debt_discharged` 導出 + M6（IMPLEMENTATION_MAP_v1.md
§6.4, 設計正本 §1 D1, §12）。

`debt_discharged` は D1 の裁定により **宣言フィールド化を禁止**されている
（`vocab.debt_discharged()` docstring 参照）。本モジュールは `campaign_closed`
ledger event の payload に `derived.debt_discharged` として **派生値の写し**
のみを記録する — 権威ある算出は常に `vocab.debt_discharged()` の再計算で
あり、この写しは監査可読性のためだけに存在する。

M6 は §12「CLAIM_CRITICAL_SET の全 member が CALIBRATED_ABSOLUTE のときのみ」
計算する。呼び出し側が identity causal sweep の 2 founder 分の component
値/E_use を明示的に与えない限り M6 は評価しない（`m6_components_a`/
`m6_components_b`/`m6_e_use` が省略された場合は `M6Result` を含めない —
本 Phase は identity sweep pairing の実測配線までは含まない、モジュール
docstring 末尾の gap 記述参照）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from voice_genesis.calibration.campaign.state import FrozenCampaign
from voice_genesis.calibration.m6_identity import M6Result, Norm, m6_distance
from voice_genesis.calibration.provenance import LedgerEntry
from voice_genesis.calibration.vocab import (
    CLAIM_CRITICAL_SET,
    MeterId,
    TerminalStatus,
    campaign_closed,
    debt_discharged,
)


class CampaignNotClosableError(RuntimeError):
    """全 `vocab.MeterId` が終端 status に到達していない状態での close 試行。"""


class RevealBeforeCloseError(RuntimeError):
    """`campaign_closed` event が記帳される前に split_secret reveal を試みた。"""


def _terminal_status_map(
    per_meter: Mapping[str, Mapping[str, object]],
) -> dict[MeterId, TerminalStatus]:
    out: dict[MeterId, TerminalStatus] = {}
    for meter_id, entry in per_meter.items():
        try:
            meter = MeterId(meter_id)
        except ValueError:
            continue
        try:
            out[meter] = TerminalStatus(entry.get("terminal_status"))
        except ValueError:
            continue
    return out


def campaign_closed_ok(per_meter: Mapping[str, Mapping[str, object]]) -> bool:
    """`vocab.campaign_closed()`: 全 `vocab.MeterId` が終端 status を持つか。"""
    return campaign_closed(_terminal_status_map(per_meter))


@dataclass(frozen=True)
class CloseResult:
    campaign_closed_entry_sha: str
    debt_discharged: bool
    m6: M6Result | None


def close_campaign(
    campaign: FrozenCampaign,
    holdout_result_payload: Mapping[str, object],
    *,
    m6_components_a: Mapping[MeterId, float] | None = None,
    m6_components_b: Mapping[MeterId, float] | None = None,
    m6_e_use: Mapping[MeterId, float] | None = None,
    m6_norm: Norm = "L1",
    invocation_id: str | None = None,
) -> CloseResult:
    """`holdout_stage.run_holdout_stage()` が記帳した `holdout_executed_valid`
    event の payload（`{"per_meter": {...}}`）から `CAMPAIGN_CLOSED` 判定を
    行い記帳する。全 `vocab.MeterId` が終端 status を持たなければ
    `CampaignNotClosableError`（fail-closed。部分クローズは許さない）。"""
    per_meter = holdout_result_payload.get("per_meter")
    if not isinstance(per_meter, Mapping):
        raise CampaignNotClosableError(
            "holdout_executed_valid payload has no per_meter section"
        )
    if not campaign_closed_ok(per_meter):
        raise CampaignNotClosableError(
            "not all vocab.MeterId have reached a terminal status; campaign is not closable"
        )

    terminal = _terminal_status_map(per_meter)
    discharged = debt_discharged(terminal)

    m6_result: M6Result | None = None
    all_critical_absolute = all(
        terminal.get(m) == TerminalStatus.CALIBRATED_ABSOLUTE for m in CLAIM_CRITICAL_SET
    )
    if (
        all_critical_absolute
        and m6_components_a is not None
        and m6_components_b is not None
        and m6_e_use is not None
    ):
        m6_result = m6_distance(m6_components_a, m6_components_b, m6_e_use, terminal, m6_norm)

    payload = {
        "kind": "campaign_closed",
        "per_meter": {k: dict(v) for k, v in per_meter.items()},
        "derived": {
            "debt_discharged": discharged,
            "m6_status": m6_result.status.value if m6_result is not None else None,
            "m6_distance": m6_result.distance if m6_result is not None else None,
        },
        "invocation_id": invocation_id,
    }
    entry = campaign.ledger.append(payload)
    return CloseResult(
        campaign_closed_entry_sha=entry.entry_sha, debt_discharged=discharged, m6=m6_result
    )


def _campaign_closed_entry_exists(campaign: FrozenCampaign) -> bool:
    for entry in campaign.ledger.entries:
        payload = entry.payload
        if isinstance(payload, Mapping) and payload.get("kind") == "campaign_closed":
            return True
    return False


def reveal_split_secret(
    campaign: FrozenCampaign, *, invocation_id: str | None = None
) -> LedgerEntry:
    """`[UNDERSPEC-CAL-D09]`: `split_secret` の commit-reveal（`--reveal-split
    -secret` フラグでのみ CLI から発火する任意操作。設計正本 §7 は commit
    (sha256) のみを義務付け、reveal 手続きは規定しない）。CAMPAIGN_CLOSED
    **後**にのみ許可する（`RevealBeforeCloseError` で fail-closed）。secret
    平文を ledger へ記録するのは、キャンペーン完了後の監査可能性のための
    最終ステップであり、それ以前に呼ぶと unseal 前の secret 露出と同じ
    リスクプロファイルになる。"""
    if not _campaign_closed_entry_exists(campaign):
        raise RevealBeforeCloseError(
            "reveal_split_secret: no campaign_closed event found; refusing to reveal before close"
        )
    sha = hashlib.sha256(campaign.split_secret).hexdigest()
    return campaign.ledger.append(
        {
            "kind": "split_secret_revealed",
            "split_secret_hex": campaign.split_secret.hex(),
            "split_secret_sha256": sha,
            "invocation_id": invocation_id,
        }
    )


__all__ = [
    "CampaignNotClosableError",
    "RevealBeforeCloseError",
    "campaign_closed_ok",
    "CloseResult",
    "close_campaign",
    "reveal_split_secret",
]
