"""planb_real/pr_status.py — 実行状態語彙と停止規則（instruction §16）。

本トラックは fail-closed で動く。判定できない事実を推測で埋めて先へ進むことを
禁止し、詰まったら `BLOCKED` を返して**原因と必要な次アクションだけ**を残す。

    PASS           ゲート通過
    FAIL           ゲート不通過（実測で不合格）
    BLOCKED        前提資材・許諾・構造が揃わず判定に到達できない
    NOT_EVALUABLE  判定対象が存在しない（例: baseline に破綻が無い）
    SKIPPED        上流が BLOCKED のため実行しなかった
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
NOT_EVALUABLE = "NOT_EVALUABLE"
SKIPPED = "SKIPPED"

_TERMINAL_BAD = (FAIL, BLOCKED)


class StopRule(Exception):
    """instruction §16 の停止条件。合理的推測で突破してはならない地点で送出する。"""

    def __init__(self, gate: str, cause: str, next_action: str) -> None:
        super().__init__(f"[{gate}] {cause}")
        self.gate = gate
        self.cause = cause
        self.next_action = next_action

    def as_result(self) -> "GateResult":
        return GateResult(gate=self.gate, status=BLOCKED, detail=self.cause,
                          next_action=self.next_action)


@dataclass(frozen=True)
class GateResult:
    gate: str
    status: str
    detail: str
    next_action: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"gate": self.gate, "status": self.status,
                               "detail": self.detail}
        if self.next_action:
            out["next_action"] = self.next_action
        if self.evidence:
            out["evidence"] = self.evidence
        return out


@dataclass
class RunLedger:
    """1 走行ぶんのゲート結果台帳。surrogate PoC とは別 ledger（instruction §9）。"""

    track: str = "real_corpus"
    gates: List[GateResult] = field(default_factory=list)

    def add(self, result: GateResult) -> GateResult:
        self.gates.append(result)
        return result

    def blocked(self) -> List[GateResult]:
        return [g for g in self.gates if g.status == BLOCKED]

    def failed(self) -> List[GateResult]:
        return [g for g in self.gates if g.status == FAIL]

    def overall(self) -> str:
        if self.failed():
            return FAIL
        if self.blocked():
            return BLOCKED
        if not self.gates:
            return BLOCKED
        return PASS

    def exit_code(self) -> int:
        """FAIL / BLOCKED は非ゼロ。スクリプトや CI が成功と誤読しないため。"""
        return 0 if self.overall() == PASS else 1

    def as_dict(self) -> Dict[str, Any]:
        return {"track": self.track, "overall": self.overall(),
                "gates": [g.as_dict() for g in self.gates]}

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.as_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")


def require(condition: bool, gate: str, cause: str, next_action: str) -> None:
    if not condition:
        raise StopRule(gate, cause, next_action)


def summarize(ledger: RunLedger) -> str:
    lines = [f"overall = {ledger.overall()}"]
    for g in ledger.gates:
        lines.append(f"[{g.status:14s}] {g.gate:22s} {g.detail}")
        if g.status in _TERMINAL_BAD and g.next_action:
            lines.append(f"{'':16s}  -> next: {g.next_action}")
    return "\n".join(lines)


def optional_path(value: Optional[str]) -> Optional[Path]:
    return Path(value).expanduser().resolve() if value else None
