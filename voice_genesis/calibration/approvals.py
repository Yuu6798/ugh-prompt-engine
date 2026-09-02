"""Phase D1: 承認ファイル loader + 三要素武装判定（設計正本 §18, IMPLEMENTATION_MAP §6.1）。

承認ファイル（Gate 1–3）は **checkout 外** の `VG_CAL_APPROVAL_DIR`（既定
`~/.vg_cal/approvals/`）に置く。checkout 内の未追跡ファイルは dirty-tree
判定（`c0_validate.REQUIRED_BLOCKING_KEYS` の `repo.dirty_tree`）で武装経路を
自己否定し、コミットすれば HEAD が変わり manifest 派生の campaign identity が
動くため（IMPLEMENTATION_MAP §6.1）。**本モジュールは承認 json をリポジトリへ
一切書き込まない**（loader のみ、read-only）。

## ハッシュ循環の解消（PR レビュー第 2 巡採用）

承認ファイルは `campaign_id` を含まない（campaign_id は manifest 側の派生値
であり、承認ファイルが先に存在しなければならない循環関係を持ち込まない）。
Gate 2 承認は `manifest_core_sha`（`approvals` 節と secret commitment 欄を
除いた manifest の正規形 sha。`c0_freeze.manifest_core_sha` 参照）を束縛する。
Gate 3（seal 保護水準の受容）は C0 freeze **後**に成立する概念のため manifest
には一切含めない — D1 はここに record 型と loader のみ用意し、`c0_freeze.py`
はこの record を C0 manifest / freeze event のいずれにも埋め込まない
（D2 runner が `GATE3_ACCEPTED` ledger event で別途束縛する設計、本 Phase の
範囲外）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from voice_genesis.calibration.cost_caps import CostCaps, cost_caps_from_mapping

#: pre-campaign 拒否コード。`vocab.BlockedCode`（設計正本 §3.3 の C0 manifest
#: fail-closed 語彙）とは別軸: こちらは「武装 3 要素が揃っていない」ことを表す
#: 手続き前ゲートであり、manifest の内容検証結果ではない。
AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

#: `c0_freeze.py` 同様、本ファイルから 2 階層上が repo root。
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 承認 hash が束縛する 2 文書（`c0_freeze.py` と同じ repo root からの相対 path）。
DESIGN_DOC_RELATIVE_PATH = "voice_genesis/calibration/DESIGN_VG_METER_CAL_DEBT_v1.0.md"
MEMO_RELATIVE_PATH = "voice_genesis/calibration/IMPLEMENTATION_MAP_v1.md"

#: `VG_CAL_APPROVAL_DIR` の既定値（checkout 外。IMPLEMENTATION_MAP §6.1）。
DEFAULT_APPROVAL_DIR = Path.home() / ".vg_cal" / "approvals"

APPROVAL_DIR_ENV_VAR = "VG_CAL_APPROVAL_DIR"


def default_approval_dir(env: Mapping[str, str] | None = None) -> Path:
    """`VG_CAL_APPROVAL_DIR` env（未設定なら既定 `~/.vg_cal/approvals/`）を解決する。"""
    source = env if env is not None else os.environ
    override = source.get(APPROVAL_DIR_ENV_VAR)
    if override:
        return Path(override)
    return DEFAULT_APPROVAL_DIR


class Gate(str, Enum):
    """§18 の最終 3 承認 Gate。"""

    GATE1_CAMPAIGN_EXECUTION = "GATE1_CAMPAIGN_EXECUTION"
    GATE2_C0_FREEZE = "GATE2_C0_FREEZE"
    GATE3_SEAL_ACCEPTANCE = "GATE3_SEAL_ACCEPTANCE"


#: gate -> 承認ファイル名（`VG_CAL_APPROVAL_DIR` 直下。IMPLEMENTATION_MAP §6.1）。
APPROVAL_FILENAMES: Mapping[Gate, str] = {
    Gate.GATE1_CAMPAIGN_EXECUTION: "gate1_campaign_execution.json",
    Gate.GATE2_C0_FREEZE: "gate2_c0_freeze.json",
    Gate.GATE3_SEAL_ACCEPTANCE: "gate3_seal_acceptance.json",
}

#: freeze manifest / ledger 上の breadcrumb key 短縮名（`c0_freeze.py` が使う）。
GATE_SHORT_NAME: Mapping[Gate, str] = {
    Gate.GATE1_CAMPAIGN_EXECUTION: "gate1",
    Gate.GATE2_C0_FREEZE: "gate2",
    Gate.GATE3_SEAL_ACCEPTANCE: "gate3",
}

#: gate -> 武装 3 要素のうちの環境変数名（IMPLEMENTATION_MAP §6.1: C0 freeze は
#: `VG_CAL_C0_FREEZE_AUTHORIZED=1`、campaign 実行 [Gate 1 と、freeze 後に
#: runner を続行させる Gate 3] は `VG_CAL_CAMPAIGN_AUTHORIZED=1`）。
GATE_ENV_VAR: Mapping[Gate, str] = {
    Gate.GATE1_CAMPAIGN_EXECUTION: "VG_CAL_CAMPAIGN_AUTHORIZED",
    Gate.GATE2_C0_FREEZE: "VG_CAL_C0_FREEZE_AUTHORIZED",
    Gate.GATE3_SEAL_ACCEPTANCE: "VG_CAL_CAMPAIGN_AUTHORIZED",
}


@dataclass(frozen=True)
class ApprovalRecord:
    """承認ファイル 1 件の内容（`campaign_id` は含まない。ハッシュ循環解消の
    ため、campaign_id は manifest 側の派生値としてのみ存在する）。

    `authorization_nonce`（PR レビュー第 5 巡: 承認の一回性）は Gate 1/Gate 2
    のみ必須。`dry_run()` が呼び出しごとに新規発行する `secrets.token_hex(16)`
    をユーザーが両ファイルへ転記する運用で、Gate 1/Gate 2 が同一 nonce を
    持つことを `check_armed()` が検証する（不一致 → `AUTHORIZATION_REQUIRED`,
    理由 `nonce_mismatch`）。`armed_freeze()` はこの nonce を frozen manifest
    へ記録し、同じ nonce を持つ既公開 campaign が既にあれば freeze を拒否する
    （`nonce_already_used`。副作用なし）。Gate 3 は対象外（manifest に一切
    現れない概念のため）。
    """

    gate: Gate
    approver: str
    approved_at_utc: str
    design_doc_sha256: str
    memo_sha256: str

    # --- gate 1 (GATE1_CAMPAIGN_EXECUTION) / gate 2 (GATE2_C0_FREEZE) ---
    authorization_nonce: str | None = None

    # --- gate 1 (GATE1_CAMPAIGN_EXECUTION) ---
    cost_caps: CostCaps | None = None
    e_use_bound_accepted: bool | None = None
    max_claim_scope: tuple[str, ...] = ()

    # --- gate 2 (GATE2_C0_FREEZE) ---
    manifest_core_sha: str | None = None

    # --- gate 3 (GATE3_SEAL_ACCEPTANCE) ---
    seal_protection_level_accepted: bool | None = None


@dataclass(frozen=True)
class ApprovalLoadResult:
    """`load_approval()` の戻り値。`approved=False` の理由はすべて `reasons` に
    列挙する（ファイル不在・shape 不正・hash 不一致のいずれも「未承認」として
    扱う — fail-closed。`content_sha256` は approved かどうかに関わらず、
    ファイルが読めた場合は常に埋める（manifest breadcrumb の pin 対象は
    「承認された」ことではなく「この内容の承認ファイルが存在した」ことである
    ため、承認済みの場合のみ manifest へ埋め込む — 呼び出し側の責務）。"""

    gate: Gate
    approved: bool
    record: ApprovalRecord | None
    content_sha256: str | None
    reasons: tuple[str, ...]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _current_design_doc_sha256(repo_root: Path) -> str:
    return _sha256_file(repo_root / DESIGN_DOC_RELATIVE_PATH)


def _current_memo_sha256(repo_root: Path) -> str:
    return _sha256_file(repo_root / MEMO_RELATIVE_PATH)


def _require_nonblank_str(payload: Mapping[str, Any], key: str, reasons: list[str]) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str) or value.strip() == "":
        reasons.append(f"{key}: must be a non-blank string")
        return None
    return value


def _require_sha256_hex(payload: Mapping[str, Any], key: str, reasons: list[str]) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str) or _SHA256_HEX_RE.match(value) is None:
        reasons.append(f"{key}: must be a 64-char lowercase hex sha256 string")
        return None
    return value


def _parse_gate1_payload(
    payload: Mapping[str, Any], reasons: list[str]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    nonce = _require_nonblank_str(payload, "authorization_nonce", reasons)
    if nonce is not None:
        out["authorization_nonce"] = nonce
    cost_caps_raw = payload.get("cost_caps")
    if not isinstance(cost_caps_raw, Mapping):
        reasons.append("cost_caps: must be an object with compute/storage/budget")
    else:
        try:
            out["cost_caps"] = cost_caps_from_mapping(dict(cost_caps_raw))
        except (KeyError, TypeError, ValueError) as exc:
            reasons.append(f"cost_caps: {exc}")
    e_use = payload.get("e_use_bound_accepted")
    if not isinstance(e_use, bool):
        reasons.append("e_use_bound_accepted: must be a bool")
    else:
        out["e_use_bound_accepted"] = e_use
    scope = payload.get("max_claim_scope")
    if not isinstance(scope, list) or any(not isinstance(x, str) or not x for x in scope):
        reasons.append("max_claim_scope: must be a list of non-empty construct-id strings")
    else:
        out["max_claim_scope"] = tuple(scope)
    return out


def _parse_gate2_payload(payload: Mapping[str, Any], reasons: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    nonce = _require_nonblank_str(payload, "authorization_nonce", reasons)
    if nonce is not None:
        out["authorization_nonce"] = nonce
    sha = _require_sha256_hex(payload, "manifest_core_sha", reasons)
    if sha is not None:
        out["manifest_core_sha"] = sha
    return out


def _parse_gate3_payload(payload: Mapping[str, Any], reasons: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    accepted = payload.get("seal_protection_level_accepted")
    if not isinstance(accepted, bool):
        reasons.append("seal_protection_level_accepted: must be a bool")
    else:
        out["seal_protection_level_accepted"] = accepted
    return out


_GATE_PAYLOAD_PARSERS = {
    Gate.GATE1_CAMPAIGN_EXECUTION: _parse_gate1_payload,
    Gate.GATE2_C0_FREEZE: _parse_gate2_payload,
    Gate.GATE3_SEAL_ACCEPTANCE: _parse_gate3_payload,
}


def load_approval(
    gate: Gate, approval_dir: Path, *, repo_root: Path | None = None
) -> ApprovalLoadResult:
    """`<approval_dir>/gate{1,2,3}_*.json` を読み、shape 検証 + 実ファイル
    hash 照合を行う。`approval_dir` は呼び出し側が明示的に渡した値のみを使い
    （既定解決は `default_approval_dir()` を呼ぶのは CLI/呼び出し側の責務）、
    本関数自身が env やリポジトリ内 fallback を探索することはない
    （テストが test-local な approval_dir を安全に渡せることを保証するため）。
    """
    root = repo_root if repo_root is not None else _REPO_ROOT
    path = approval_dir / APPROVAL_FILENAMES[gate]
    reasons: list[str] = []

    if not path.is_file():
        return ApprovalLoadResult(
            gate=gate, approved=False, record=None, content_sha256=None,
            reasons=(f"approval file not found: {path}",),
        )

    raw_bytes = path.read_bytes()
    content_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return ApprovalLoadResult(
            gate=gate, approved=False, record=None, content_sha256=content_sha256,
            reasons=(f"malformed JSON: {exc}",),
        )
    if not isinstance(payload, Mapping):
        return ApprovalLoadResult(
            gate=gate, approved=False, record=None, content_sha256=content_sha256,
            reasons=("approval file must contain a JSON object",),
        )

    declared_gate = payload.get("gate")
    if declared_gate != gate.value:
        reasons.append(f"gate: expected {gate.value!r}, got {declared_gate!r}")

    approver = _require_nonblank_str(payload, "approver", reasons)
    approved_at_utc = _require_nonblank_str(payload, "approved_at_utc", reasons)
    declared_design_sha = _require_sha256_hex(payload, "design_doc_sha256", reasons)
    declared_memo_sha = _require_sha256_hex(payload, "memo_sha256", reasons)

    try:
        actual_design_sha = _current_design_doc_sha256(root)
        actual_memo_sha = _current_memo_sha256(root)
    except OSError as exc:
        reasons.append(f"cannot read pinned source documents for hash verification: {exc}")
        actual_design_sha = actual_memo_sha = None

    if (
        declared_design_sha is not None
        and actual_design_sha is not None
        and declared_design_sha != actual_design_sha
    ):
        reasons.append(
            "design_doc_sha256 mismatch: approval pins "
            f"{declared_design_sha!r}, current file is {actual_design_sha!r}"
        )
    if (
        declared_memo_sha is not None
        and actual_memo_sha is not None
        and declared_memo_sha != actual_memo_sha
    ):
        reasons.append(
            "memo_sha256 mismatch: approval pins "
            f"{declared_memo_sha!r}, current file is {actual_memo_sha!r}"
        )

    gate_specific = _GATE_PAYLOAD_PARSERS[gate](payload, reasons)

    if reasons:
        return ApprovalLoadResult(
            gate=gate, approved=False, record=None, content_sha256=content_sha256,
            reasons=tuple(reasons),
        )

    assert approver is not None and approved_at_utc is not None
    assert declared_design_sha is not None and declared_memo_sha is not None
    record = ApprovalRecord(
        gate=gate,
        approver=approver,
        approved_at_utc=approved_at_utc,
        design_doc_sha256=declared_design_sha,
        memo_sha256=declared_memo_sha,
        **gate_specific,
    )
    return ApprovalLoadResult(
        gate=gate, approved=True, record=record, content_sha256=content_sha256, reasons=(),
    )


def load_all_approvals(
    approval_dir: Path, *, repo_root: Path | None = None
) -> dict[Gate, ApprovalLoadResult]:
    """3 Gate 全てを `load_approval()` する。"""
    return {gate: load_approval(gate, approval_dir, repo_root=repo_root) for gate in Gate}


@dataclass(frozen=True)
class ArmingDecision:
    """`check_armed()` の戻り値。`armed=True` は 3 要素すべてが揃った場合のみ。"""

    gate: Gate
    armed: bool
    missing_factors: tuple[str, ...]
    approval: ApprovalRecord | None
    approval_content_sha256: str | None

    @property
    def code(self) -> str | None:
        return None if self.armed else AUTHORIZATION_REQUIRED


def check_armed(
    gate: Gate,
    cli_armed: bool,
    env: Mapping[str, str],
    approval_dir: Path,
    *,
    repo_root: Path | None = None,
) -> ArmingDecision:
    """三要素武装判定: `--armed` フラグ AND 対応する環境変数 `=1` AND 有効な
    承認ファイル。1 つでも欠ければ `armed=False`（`AUTHORIZATION_REQUIRED`）。

    `gate=GATE2_C0_FREEZE` の場合、承認ファイル自体が有効でも、Gate 1 が
    approved かつ `authorization_nonce` が Gate 2 のそれと異なれば
    `approval_file:nonce_mismatch` を追加する（PR レビュー第 5 巡: 承認の
    一回性。Gate 1 が未承認の場合はこの cross-check をスキップする — それは
    別の問題として `armed_freeze()` の manifest validation 側で表面化する）。
    """
    missing: list[str] = []
    if not cli_armed:
        missing.append("cli_flag:--armed")

    env_var = GATE_ENV_VAR[gate]
    if env.get(env_var) != "1":
        missing.append(f"env:{env_var}=1")

    result = load_approval(gate, approval_dir, repo_root=repo_root)
    if not result.approved:
        for reason in result.reasons:
            missing.append(f"approval_file:{reason}")
        if not result.reasons:
            missing.append("approval_file:unknown validation failure")
    elif gate == Gate.GATE2_C0_FREEZE and result.record is not None:
        gate1_result = load_approval(
            Gate.GATE1_CAMPAIGN_EXECUTION, approval_dir, repo_root=repo_root
        )
        if (
            gate1_result.approved
            and gate1_result.record is not None
            and gate1_result.record.authorization_nonce != result.record.authorization_nonce
        ):
            missing.append("approval_file:nonce_mismatch")

    armed = not missing
    return ArmingDecision(
        gate=gate,
        armed=armed,
        missing_factors=tuple(missing),
        approval=result.record if armed else None,
        approval_content_sha256=result.content_sha256,
    )


__all__ = [
    "AUTHORIZATION_REQUIRED",
    "DESIGN_DOC_RELATIVE_PATH",
    "MEMO_RELATIVE_PATH",
    "DEFAULT_APPROVAL_DIR",
    "APPROVAL_DIR_ENV_VAR",
    "default_approval_dir",
    "Gate",
    "APPROVAL_FILENAMES",
    "GATE_SHORT_NAME",
    "GATE_ENV_VAR",
    "ApprovalRecord",
    "ApprovalLoadResult",
    "load_approval",
    "load_all_approvals",
    "ArmingDecision",
    "check_armed",
]
