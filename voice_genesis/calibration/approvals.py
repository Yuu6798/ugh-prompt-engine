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

import argparse
import contextlib
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from voice_genesis.calibration.cost_caps import CostCaps, cost_caps_from_mapping

#: pre-campaign 拒否コード。`vocab.BlockedCode`（設計正本 §3.3 の C0 manifest
#: fail-closed 語彙）とは別軸: こちらは「武装 3 要素が揃っていない」ことを表す
#: 手続き前ゲートであり、manifest の内容検証結果ではない。
AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"

#: v1.2 WP2 §B(vii): rehearsal（claim を生まない疎通試験）専用の Gate 1
#: `max_claim_scope` sentinel。construct-id ではないため、rehearsal ではない
#: 承認にこれが含まれていれば `load_approval()` が fail-closed で拒否する
#: （逆に rehearsal 経路では registry construct と混在させない = これ単独が
#: 期待形）。`c0_freeze._check_max_claim_scope()` 側の許容も同じ定数を読む。
REHEARSAL_CLAIM_SCOPE_SENTINEL = "REHEARSAL"

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

#: `c0_freeze.py` 同様、本ファイルから 2 階層上が repo root。
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 承認 hash が束縛する統治文書（`c0_freeze.py` と同じ repo root からの相対
#: path）。v1.2 (`DESIGN_VG_METER_CAL_DEBT_v1.2.md` §V6 継承, 2026-09-06
#: 統治文書切替): pin 対象は v1.2 統治文書へ切り替わった。v1.1/v1.0 は
#: read-only の基底文書として残り、`BASE_DESIGN_DOC_RELATIVE_PATH`/
#: `BASE_BASE_DESIGN_DOC_RELATIVE_PATH` 経由で実行時 pin の対象になる
#: （下記 `_verify_base_document_pin()` 参照。2 段連鎖）。
DESIGN_DOC_RELATIVE_PATH = "voice_genesis/calibration/DESIGN_VG_METER_CAL_DEBT_v1.2.md"
#: v1.2 の基底（承継元）文書。v1.2 front matter の `base_document_sha256` が
#: これの実測 sha256 と一致することを `load_approval()` が毎回検証する
#: （信頼の連鎖の第 1 段: 承認 → v1.2 バイト列 → v1.1 バイト列）。
BASE_DESIGN_DOC_RELATIVE_PATH = "voice_genesis/calibration/DESIGN_VG_METER_CAL_DEBT_v1.1.md"
#: v1.1 のさらなる基底（承継元）文書。v1.1 front matter 自身の
#: `base_document_sha256` がこれの実測 sha256 と一致することを検証する
#: （信頼の連鎖の第 2 段: v1.1 バイト列 → v1.0 バイト列。v1.2 で統治文書が
#: 1 世代進んだことにより、v1.1 時代は 1 段だった連鎖が 2 段になった）。
BASE_BASE_DESIGN_DOC_RELATIVE_PATH = "voice_genesis/calibration/DESIGN_VG_METER_CAL_DEBT_v1.0.md"
MEMO_RELATIVE_PATH = "voice_genesis/calibration/IMPLEMENTATION_MAP_v1.md"

#: front matter を区切る `---` 行（先頭固定・複数行 YAML ブロック）を抜き出す
#: 正規表現。`re.DOTALL` で `.` が改行を跨ぐ（front matter 本体の複数行取得）。
_FRONT_MATTER_RE = re.compile(r"\A---\r?\n(.*?\r?\n)---\r?\n", re.DOTALL)


def _parse_front_matter(text: str) -> Mapping[str, Any] | None:
    """先頭の `---`...`---` YAML front matter を `yaml.safe_load` で解析する。
    front matter が存在しない・YAML としてパース不能・トップレベルが
    mapping でない場合は `None`（呼び出し側が fail-closed の reason へ変換
    する）。"""
    match = _FRONT_MATTER_RE.match(text)
    if match is None:
        return None
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    if not isinstance(data, Mapping):
        return None
    return data


def _verify_single_base_pin(
    pinning_doc_path: Path, pinning_doc_bytes: bytes, pinned_doc_path: Path
) -> list[str]:
    """1 リンク分の base pin 検証: `pinning_doc_path`（バイト列
    `pinning_doc_bytes`）の front matter が宣言する `base_document_sha256` と、
    `pinned_doc_path` の実測 sha256 の一致を検証する。不一致・欠落・パース
    不能はすべて fail-closed の reason 文字列として返す（空リストはこの
    リンクの pin が成立していることを意味する）。`_verify_base_document_pin()`
    が v1.2→v1.1→v1.0 の 2 段連鎖を組み立てる際の共通実装。"""
    try:
        pinning_doc_text = pinning_doc_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [
            f"base_document_sha256: cannot decode design doc {pinning_doc_path} "
            f"as utf-8: {exc}"
        ]

    front_matter = _parse_front_matter(pinning_doc_text)
    if front_matter is None:
        return [
            "base_document_sha256: design doc front matter is missing or unparsable "
            f"({pinning_doc_path})"
        ]

    declared_base_sha = front_matter.get("base_document_sha256")
    if not isinstance(declared_base_sha, str) or _SHA256_HEX_RE.match(declared_base_sha) is None:
        return [
            "base_document_sha256: design doc front matter missing/invalid "
            f"base_document_sha256 (must be a 64-char lowercase hex sha256 string) "
            f"({pinning_doc_path})"
        ]

    try:
        actual_base_sha = _sha256_file(pinned_doc_path)
    except OSError as exc:
        return [f"base_document_sha256: cannot read base document {pinned_doc_path}: {exc}"]

    if declared_base_sha != actual_base_sha:
        return [
            "base_document_sha256 mismatch: design doc front matter pins "
            f"{declared_base_sha!r}, current base document ({pinned_doc_path}) is "
            f"{actual_base_sha!r} ({pinning_doc_path})"
        ]
    return []


def _verify_base_document_pin(repo_root: Path, design_doc_bytes: bytes) -> list[str]:
    """v1.2 統治文書（`DESIGN_DOC_RELATIVE_PATH`）の front matter が宣言する
    `base_document_sha256` が checkout 上の v1.1（`BASE_DESIGN_DOC_RELATIVE_PATH`）
    の実測 sha256 と一致し、**かつ** v1.1 自身の front matter が宣言する
    `base_document_sha256` が checkout 上の v1.0
    （`BASE_BASE_DESIGN_DOC_RELATIVE_PATH`）の実測 sha256 と一致することを
    検証する（§V6「基底文書の実行時 pin」の v1.2 拡張: 統治文書が 1 世代
    進んだことで連鎖が 2 段になった）。

    どちらか一方だけを pin すると、承継元文書が承認後・freeze 後に改変されても
    `check_armed()` が無効化されない穴が残るため、両リンクをこの検証で毎回の
    `load_approval()` に組み込む。両リンクは独立に検証し、reasons は連結して
    返す（片方が失敗しても、もう片方の状態も同時に報告する）。空リストは
    2 リンクとも pin が成立していることを意味する — 承認そのものが成り立つかは
    呼び出し側の他の検査と合わせて判定される。

    `design_doc_bytes` は呼び出し側（`load_approval()`）が v1.2 統治文書を
    **1 回だけ** 読み取ったバイト列をそのまま受け取る（第 1 段）。本関数は
    第 2 段（v1.1 → v1.0）のために v1.1 を新たに 1 回だけ読み、そのバイト列を
    hash 算出（第 1 段の相手側）と front matter 解析（第 2 段のピン元）の
    両方に使う——hash 算出用の読取と front matter 解析用の読取を分けると、
    その間隔で文書が差し替わった場合に「hash は版 A・base pin は版 B」の
    組み合わせで承認が成立し得る（Codex PR #346 第 16 巡指摘。承認
    provenance の汚染）という同じ原則を、段を跨いで v1.1 にも適用する。
    """
    design_doc_path = repo_root / DESIGN_DOC_RELATIVE_PATH
    base_doc_path = repo_root / BASE_DESIGN_DOC_RELATIVE_PATH
    reasons = _verify_single_base_pin(design_doc_path, design_doc_bytes, base_doc_path)

    base_base_doc_path = repo_root / BASE_BASE_DESIGN_DOC_RELATIVE_PATH
    try:
        base_doc_bytes = base_doc_path.read_bytes()
    except OSError as exc:
        reasons.append(
            f"base_document_sha256: cannot read base document {base_doc_path} "
            f"for chained pin verification: {exc}"
        )
        return reasons

    reasons.extend(_verify_single_base_pin(base_doc_path, base_doc_bytes, base_base_doc_path))
    return reasons

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


def _read_design_doc(repo_root: Path) -> bytes:
    """v1.2 統治文書（`DESIGN_DOC_RELATIVE_PATH`）を 1 回だけ読み込む。
    呼び出し側（`load_approval()`）はこの同一バイト列から sha256 と front
    matter（`_verify_base_document_pin()`）の両方を導出し、別々の読取に
    基づく TOCTOU（読取間の差し替え）で「hash は版 A・base pin は版 B」が
    承認を通ってしまう穴（Codex PR #346 第 16 巡指摘）を閉じる。"""
    return (repo_root / DESIGN_DOC_RELATIVE_PATH).read_bytes()


def _current_design_doc_sha256(repo_root: Path) -> str:
    return hashlib.sha256(_read_design_doc(repo_root)).hexdigest()


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


def _is_iso8601_utc_timestamp(value: object) -> bool:
    """round 24 ADOPT (2) P2 (`[UNDERSPEC-CAL-D56]`): minimal ISO 8601 UTC
    check for `approved_at_utc`. Requires an explicit UTC offset (`Z` or
    `+00:00`; fractional seconds are optional and pass through
    `datetime.fromisoformat` unchanged) — a naive timestamp or one with a
    non-UTC offset is rejected, since neither is "UTC" per the field's own
    name. Mirrors `provenance._is_iso8601_utc_timestamp` (round 23
    ADOPT (3), `[UNDERSPEC-CAL-D53]`) — duplicated rather than imported to
    keep this shape-check module independent of `provenance.py`'s
    ledger-specific internals (this module has no other dependency on
    `provenance.py`)."""
    if not isinstance(value, str) or not value:
        return False
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def _require_iso8601_utc_timestamp(
    payload: Mapping[str, Any], key: str, reasons: list[str]
) -> str | None:
    """round 24 ADOPT (2) P2 (`[UNDERSPEC-CAL-D56]`): stricter than
    `_require_nonblank_str` — the value must additionally parse as an ISO
    8601 timestamp carrying an explicit UTC offset (`_is_iso8601_utc_timestamp`).
    A non-blank-but-unparsable/naive/non-UTC value (e.g. `"tomorrow"`, a bare
    local timestamp) is rejected with a distinct reason from a merely
    missing/blank field, so `approved=True` can no longer be reached with an
    approval timestamp that was never actually validated as a real UTC
    instant."""
    value = payload.get(key)
    if not isinstance(value, str) or value.strip() == "":
        reasons.append(f"{key}: must be a non-blank string")
        return None
    if not _is_iso8601_utc_timestamp(value):
        reasons.append(
            f"{key}: must be an ISO 8601 timestamp with an explicit UTC offset "
            f"(Z or +00:00), got {value!r}"
        )
        return None
    return value


def _parse_gate1_payload(
    payload: Mapping[str, Any], reasons: list[str], *, rehearsal: bool = False
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
    elif len(scope) != len(set(scope)):
        # 第 11 巡採用: 承認スコープに重複 construct-id があるのは、承認者が
        # 「この construct を claim してよい」という意思表示を意図せず二重に
        # 書いてしまった手入力ミスの兆候であり、`c0_freeze.build_manifest()`
        # がそのまま `frozen_design.max_claim_scope`（core payload の一部）へ
        # 転記した先で無意味な重複として残る。承認内容そのものの shape 検証と
        # して、ここで fail-closed に拒否する（registry construct 集合との
        # 突合— 空/未知 id の検査— は `c0_freeze._check_max_claim_scope()` が
        # 別途 manifest 側で行う責務であり、本モジュールは registry に依存
        # しないため踏み込まない）。
        reasons.append("max_claim_scope: must not contain duplicate construct-id strings")
    elif REHEARSAL_CLAIM_SCOPE_SENTINEL in scope and not rehearsal:
        # v1.2 WP2 §B(vii): rehearsal sentinel は rehearsal 経路でのみ受理する。
        # 本番 freeze/campaign が `["REHEARSAL"]` を含む Gate 1 承認を拾って
        # しまう（= claim を生む経路が「claim しない」承認で武装される）ことを
        # loader 段階で fail-closed に塞ぐ。
        reasons.append(
            f"max_claim_scope: {REHEARSAL_CLAIM_SCOPE_SENTINEL!r} sentinel is only "
            "accepted for a rehearsal (--rehearsal) campaign"
        )
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
    gate: Gate,
    approval_dir: Path,
    *,
    repo_root: Path | None = None,
    rehearsal: bool = False,
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
    # round 24 ADOPT (2) P2 (`[UNDERSPEC-CAL-D56]`): all three gates share
    # this loader, so the stricter ISO 8601 UTC check applies uniformly.
    approved_at_utc = _require_iso8601_utc_timestamp(payload, "approved_at_utc", reasons)
    declared_design_sha = _require_sha256_hex(payload, "design_doc_sha256", reasons)
    declared_memo_sha = _require_sha256_hex(payload, "memo_sha256", reasons)

    # R16 対応: v1.2 は 1 回だけ読み、同じバイト列を hash（ここ）と
    # front matter 解析（`_verify_base_document_pin()`、下方で再利用）の
    # 両方に使う（読取を分けない = TOCTOU 閉塞）。
    design_doc_bytes: bytes | None
    try:
        design_doc_bytes = _read_design_doc(root)
        actual_design_sha = hashlib.sha256(design_doc_bytes).hexdigest()
        actual_memo_sha = _current_memo_sha256(root)
    except OSError as exc:
        reasons.append(f"cannot read pinned source documents for hash verification: {exc}")
        design_doc_bytes = None
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

    if gate is Gate.GATE1_CAMPAIGN_EXECUTION:
        # v1.2 WP2 §B(vii): Gate 1 のみ rehearsal 文脈を受け取る
        # （`max_claim_scope` sentinel の受理可否）。
        gate_specific = _parse_gate1_payload(payload, reasons, rehearsal=rehearsal)
    else:
        gate_specific = _GATE_PAYLOAD_PARSERS[gate](payload, reasons)

    # §V6「基底文書の実行時 pin」(v1.2 で 2 段連鎖に拡張): 承認ファイル自体の
    # shape/hash 検査とは独立に、checkout 上の v1.2/v1.1/v1.0 バイト列の整合を
    # 毎回検証する（承認ファイルの内容に関わらず必須 — v1.1/v1.0 の事後改変を
    # 無効化する経路がこれ以外にない）。design_doc_bytes が None（上の読取が
    # OSError で失敗）のときは、その事実が既に reasons に積まれているため
    # fail-closed は成立済み — ここで改めて v1.2 を読み直しはしない（R16:
    # 読取を 1 回に固定する）。
    if design_doc_bytes is not None:
        reasons.extend(_verify_base_document_pin(root, design_doc_bytes))

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
    approval_dir: Path, *, repo_root: Path | None = None, rehearsal: bool = False
) -> dict[Gate, ApprovalLoadResult]:
    """3 Gate 全てを `load_approval()` する。`rehearsal`（v1.2 WP2）は Gate 1 の
    `max_claim_scope` sentinel 受理可否としてそのまま伝播する。"""
    return {
        gate: load_approval(gate, approval_dir, repo_root=repo_root, rehearsal=rehearsal)
        for gate in Gate
    }


@dataclass(frozen=True)
class HashRefreshResult:
    """`refresh_document_hashes()` の戻り値。承認ファイルに記録されていた
    旧ハッシュ（欠落/非文字列なら `None`）と、書き戻した新ハッシュを保持する。"""

    old_design_doc_sha256: str | None
    new_design_doc_sha256: str
    old_memo_sha256: str | None
    new_memo_sha256: str

    @property
    def changed(self) -> bool:
        return (
            self.old_design_doc_sha256 != self.new_design_doc_sha256
            or self.old_memo_sha256 != self.new_memo_sha256
        )


def refresh_document_hashes(
    approval_path: Path, repo_root: Path | None = None
) -> HashRefreshResult:
    """既存の承認ファイルを再読込し、`design_doc_sha256`/`memo_sha256` を
    現在の `DESIGN_VG_METER_CAL_DEBT_v1.2.md`/`IMPLEMENTATION_MAP_v1.md` の
    実測ハッシュへ書き換えて atomic に書き戻す（他フィールドは一切変更
    しない）。v1.1/v1.0 基底文書の 2 段連鎖 pin（`base_document_sha256`）は
    それぞれ v1.2/v1.1 の front matter 側にあり、本関数の再スタンプ対象では
    ない。

    メモ編集はハッシュ束縛を毎回無効化するため、承認者はメモ編集の都度
    再承認しなければならない（`load_approval()` の hash mismatch 検査）。
    本関数はその機械的な再スタンプのみを行う — **承認者本人がこの新しい
    ハッシュを事前に確認・容認したことにはならない**。呼び出し側は、この
    関数を実行した後で承認者へ改めて確認を求める運用を別途取ること。

    `approval_path` の JSON 構造が壊れていれば `ValueError`/`OSError` を
    fail-closed で送出する（`json.JSONDecodeError` は `ValueError` の
    サブクラス）。
    """
    root = repo_root if repo_root is not None else _REPO_ROOT
    payload = json.loads(approval_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{approval_path}: must contain a JSON object")
    payload = dict(payload)

    old_design = payload.get("design_doc_sha256")
    old_memo = payload.get("memo_sha256")
    new_design = _current_design_doc_sha256(root)
    new_memo = _current_memo_sha256(root)
    payload["design_doc_sha256"] = new_design
    payload["memo_sha256"] = new_memo

    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(approval_path.parent), prefix=f".{approval_path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(tmp_name, approval_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise

    return HashRefreshResult(
        old_design_doc_sha256=old_design if isinstance(old_design, str) else None,
        new_design_doc_sha256=new_design,
        old_memo_sha256=old_memo if isinstance(old_memo, str) else None,
        new_memo_sha256=new_memo,
    )


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
    preloaded: Mapping[Gate, ApprovalLoadResult] | None = None,
    rehearsal: bool = False,
) -> ArmingDecision:
    """三要素武装判定: `--armed` フラグ AND 対応する環境変数 `=1` AND 有効な
    承認ファイル。1 つでも欠ければ `armed=False`（`AUTHORIZATION_REQUIRED`）。

    `gate=GATE2_C0_FREEZE` の場合、承認ファイル自体が有効でも、Gate 1 が
    approved かつ `authorization_nonce` が Gate 2 のそれと異なれば
    `approval_file:nonce_mismatch` を追加する（PR レビュー第 5 巡: 承認の
    一回性。Gate 1 が未承認の場合はこの cross-check をスキップする — それは
    別の問題として `armed_freeze()` の manifest validation 側で表面化する）。

    `preloaded`（PR レビュー第 6 巡 #5）: 呼び出し側が既に `load_all_approvals()`
    で全 Gate を読み込んでいる場合、そのスナップショットをここへ渡すと本関数は
    ディスクから再読込しない（同一承認ファイルの二重読みを避ける）。`None`
    （既定）なら従来どおり `load_approval()` で自前に読み込む。
    """
    missing: list[str] = []
    if not cli_armed:
        missing.append("cli_flag:--armed")

    env_var = GATE_ENV_VAR[gate]
    if env.get(env_var) != "1":
        missing.append(f"env:{env_var}=1")

    result = (
        preloaded[gate]
        if preloaded is not None
        else load_approval(gate, approval_dir, repo_root=repo_root, rehearsal=rehearsal)
    )
    if not result.approved:
        for reason in result.reasons:
            missing.append(f"approval_file:{reason}")
        if not result.reasons:
            missing.append("approval_file:unknown validation failure")
    elif gate == Gate.GATE2_C0_FREEZE and result.record is not None:
        gate1_result = (
            preloaded[Gate.GATE1_CAMPAIGN_EXECUTION]
            if preloaded is not None
            else load_approval(
                Gate.GATE1_CAMPAIGN_EXECUTION,
                approval_dir,
                repo_root=repo_root,
                rehearsal=rehearsal,
            )
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


# ---------------------------------------------------------------------------
# CLI — `python -m voice_genesis.calibration.approvals refresh --gate gate1 ...`
# ---------------------------------------------------------------------------

_SHORT_NAME_TO_GATE: Mapping[str, Gate] = {short: gate for gate, short in GATE_SHORT_NAME.items()}


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m voice_genesis.calibration.approvals",
        description="Approval file (Gate 1-3) utilities.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    refresh = sub.add_parser(
        "refresh",
        help=(
            "Re-stamp design_doc_sha256/memo_sha256 on an existing approval file to "
            "the current DESIGN_VG_METER_CAL_DEBT_v1.2.md/IMPLEMENTATION_MAP_v1.md "
            "file hashes. All other fields untouched. Every memo edit invalidates "
            "the old hash binding; the approver must still re-issue/re-confirm the "
            "approval — this only re-stamps the hash fields mechanically. Note: "
            "the v1.1/v1.0 base documents are pinned separately via the 2-link "
            "base_document_sha256 chain embedded in the v1.2/v1.1 front matter, "
            "not via this refresh."
        ),
    )
    refresh.add_argument(
        "--gate",
        required=True,
        choices=sorted(_SHORT_NAME_TO_GATE),
        help="short gate name (gate1/gate2/gate3)",
    )
    refresh.add_argument("--approval-dir", type=Path, default=None)
    refresh.add_argument("--repo-root", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    if args.command == "refresh":
        approval_dir = (
            args.approval_dir if args.approval_dir is not None else default_approval_dir()
        )
        root = args.repo_root if args.repo_root is not None else _REPO_ROOT
        gate = _SHORT_NAME_TO_GATE[args.gate]
        path = approval_dir / APPROVAL_FILENAMES[gate]
        result = refresh_document_hashes(path, root)
        print(f"file: {path}")
        print(f"design_doc_sha256: {result.old_design_doc_sha256} -> {result.new_design_doc_sha256}")
        print(f"memo_sha256: {result.old_memo_sha256} -> {result.new_memo_sha256}")
        print(f"changed: {result.changed}")
        return 0
    return 1  # pragma: no cover - argparse `required=True` prevents reaching this


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AUTHORIZATION_REQUIRED",
    "REHEARSAL_CLAIM_SCOPE_SENTINEL",
    "DESIGN_DOC_RELATIVE_PATH",
    "BASE_DESIGN_DOC_RELATIVE_PATH",
    "BASE_BASE_DESIGN_DOC_RELATIVE_PATH",
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
    "HashRefreshResult",
    "refresh_document_hashes",
    "ArmingDecision",
    "check_armed",
]
