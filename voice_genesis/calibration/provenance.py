"""provenance スキーマと append-only ledger（設計正本 §7, §13）。

**保護水準の正直な宣言**（設計正本 §7）: この ledger が検出するのは「事故的
leakage」と「事後改竄」のみである。外部鍵管理を伴わない本構成
（2 エージェント + 共有 Drive）では、台帳の外側で動く敵対的な実行者を防ぐ
ことはできない（ユーザー判断 3）。

**単一 writer 境界の契約**（Codex レビュー 2026-09-01 採用）: `Ledger.append()`
は `fcntl.flock` による排他ロック + write → flush → `os.fsync` で直列化される。
これは「1 プロセスが同時に複数 append を行っても壊れない」ことを保証するが、
「複数プロセスが同時に append してもレースなく統合される」ことまでは保証する
（OS ファイルロックにより直列化される）ものの、**並列 meter 実行**（設計正本
§14 のような大規模並列実行）は、各 worker が per-worker の一時記録を書き、
それを単一の writer プロセスが順次 `append()` する集約方式を前提とする。
複数プロセスが同一 `Ledger` インスタンスを介さず同一ファイルへ直接 `append()`
を呼ぶ運用は、flock による直列化により安全ではあるが、本モジュールの契約は
あくまで **1 台帳 = 1 論理 writer**（プロセス内 or flock 経由で直列化された
複数プロセス）である。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, is_dataclass
from pathlib import Path
from typing import Any

from voice_genesis.calibration.canonical import canonical_json, manifest_sha
from voice_genesis.calibration.splitter import RealizedSplitMap, RowInput, verify_split
from voice_genesis.calibration.vocab import BlockedCode


# ---------------------------------------------------------------------------
# §13 provenance schema (nested frozen dataclasses)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CampaignIdentity:
    campaign_id: str
    campaign_parent_id: str | None
    event_id: str
    event_time_utc: str
    actor: str
    authorization_flags: Mapping[str, bool]


@dataclass(frozen=True)
class CodeIdentity:
    source_document_ids: tuple[str, ...]
    source_document_hashes: tuple[str, ...]
    repo_url: str
    code_sha: str
    dirty_state: bool
    dependency_lock_hash: str
    runtime_image_hash: str | None = None


@dataclass(frozen=True)
class CandidateIdentity:
    candidate_id: str
    algorithm_family: str
    implementation_hash: str
    parameter_json_hash: str
    claim_ceiling: str
    complexity_rank: int


@dataclass(frozen=True)
class FixtureIdentity:
    fixture_family: str
    row_id: str
    instance_id: str
    generator_spec_hash: str
    generator_code_hash: str
    render_hash: str
    truth: Mapping[str, Any]
    u_gt: float
    u_num: float
    domain_tag: str


@dataclass(frozen=True)
class SeedIdentity:
    seed_derivation_scheme: str
    public_seed_identifier: str  # secret は記録しない
    realized_split_map_hash: str
    seal_commitment: str | None
    unseal_event_id: str | None


@dataclass(frozen=True)
class MeasurementPayload:
    raw_repeat_process_outputs: Mapping[str, Any]
    missing_invalid_reason: str | None
    control_eligibility: bool
    pair_membership: tuple[str, ...] = ()


@dataclass(frozen=True)
class SelectionRecord:
    e_use_value: float | None
    evidence_row_hash: str
    raw_criterion_vector: tuple[Any, ...]
    rounded_criterion_vector: tuple[Any, ...]
    selection_rank: int
    selection_frozen_event_id: str


@dataclass(frozen=True)
class GateRecord:
    gate_inputs: Mapping[str, Any]
    gate_outputs: Mapping[str, Any]
    n_neg: int
    n_pos: int
    invariance_pair_count: int
    resolvable_pair_count: int
    three_pair_warning: bool


@dataclass(frozen=True)
class FinalStatusRecord:
    final_meter_status: str
    reason_code: str
    claim_text: str
    prohibited_interpretations: tuple[str, ...]


@dataclass(frozen=True)
class ResourceCounters:
    meter_calls: int
    storage_bytes: int
    runtime_seconds: float
    cap_values: Mapping[str, float]
    stop_event: str | None
    stop_reason: str | None


@dataclass(frozen=True)
class ProvenanceRecord:
    """§13 必須 field を束ねた provenance record。phase 依存の要素は Optional。

    追加(§13 追記事項): `control_gate` 宣言列 / `unstable_cell` flag /
    `tolerance_floor_limited` 付記 / `split_swaps` (splitter.SwapRecord 群の
    dict 表現)。
    """

    campaign: CampaignIdentity
    code: CodeIdentity
    candidate: CandidateIdentity | None = None
    fixture: FixtureIdentity | None = None
    seed: SeedIdentity | None = None
    measurement: MeasurementPayload | None = None
    selection: SelectionRecord | None = None
    gate: GateRecord | None = None
    final_status: FinalStatusRecord | None = None
    resources: ResourceCounters | None = None
    control_gate: str = "APPLICABLE"
    unstable_cell: bool = False
    tolerance_floor_limited: bool = False
    split_swaps: tuple[Mapping[str, Any], ...] = ()


def _jsonable(obj: Any) -> Any:
    """dataclass / Mapping / tuple を canonical_json が受理できる形へ変換する。"""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _jsonable(getattr(obj, f.name)) for f in _dc_fields(obj)}
    if isinstance(obj, Mapping):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def _dc_fields(obj: Any) -> tuple[Any, ...]:
    from dataclasses import fields

    return fields(obj)


def provenance_record_to_dict(record: ProvenanceRecord) -> dict[str, Any]:
    return _jsonable(record)


# ---------------------------------------------------------------------------
# Append-only JSONL ledger
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    prev_sha: str
    entry_sha: str
    payload: Mapping[str, Any]


GENESIS_PREV_SHA = "0" * 64

#: `LedgerEntry` として扱うために各行が最低限持つべき必須フィールド
#: （Codex レビュー 2026-09-01 P1 finding #3）。JSON としてはパース可能でも
#: これらを欠く行（例: `{"seq": 0}`）は「構造的に malformed」とみなす。
_ENTRY_REQUIRED_KEYS = frozenset({"seq", "prev_sha", "entry_sha", "payload"})


@dataclass(frozen=True)
class MalformedLedgerLine:
    """`Ledger._read_all()` が検出した、JSON としてはパース可能だが
    `LedgerEntry` に必須のフィールド（`_ENTRY_REQUIRED_KEYS`）を欠く/型が
    不正な行の記録（Codex レビュー 2026-09-01 P1 finding #3）。

    従来 `_read_all()` は `raw["prev_sha"]` のような直接インデックスで
    行を `LedgerEntry` 化しており、`{"seq": 0}` のような parseable-but-
    malformed な行に対して `KeyError` を送出していた。この例外は
    `Ledger.__init__` から呼び出し側へそのまま伝播するため、
    `verify_chain()` を呼ぶ前に台帳の構築自体がクラッシュし、
    「破損を検出して報告する」という契約を満たせなかった。
    `_read_all()` はこの種の行を `LedgerEntry` としては構築せず、代わりに
    `MalformedLedgerLine` として記録する（クラッシュしない）。chain 全体の
    正当性についての権威ある判定は引き続き `verify_chain()` が独立に行う。
    """

    line_index: int
    raw: str
    reason: str


@dataclass(frozen=True)
class LeakageCheckResult:
    """`Ledger.check_leakage` の戻り値。`control_excluded_count` が非 0 なら
    control 共有契約（IMPLEMENTATION_MAP_v1.md §2.7）による除外が実際に
    適用されたことを判別できる。"""

    blocked: BlockedCode | None
    control_excluded_count: int


@dataclass(frozen=True)
class ChainVerification:
    """`verify_chain()` の詳細結果。単に True/False だけでなく、末尾の
    truncated/不完全行と prev_sha 不一致（sibling 分岐・改竄）を区別する。

    **fail-closed 契約**（Codex レビュー 2026-09-01 採用）: `truncated_tail=True`
    は常に `ok=False` を伴う（末尾が中断された台帳は「検証できない」のであって
    「検証に成功した」のではない）。`truncated_tail` フィールドは、その `ok=False`
    が改竄由来 (`tamper_at_seq` 有り) なのか write 中断由来なのかを呼び出し側が
    区別できるよう残す。

    `missing_final_newline`（Codex レビュー 2026-09-01 P1 追加）: 最終行が
    改行未終端だが JSON としては完全にパース可能だった場合に `True`。この
    ケースは **fail-closed の対象ではない**（`ok` は content が正当なら
    `True` のまま）: 1エントリも失われておらず、欠けているのは区切りの
    改行のみだからである（情報提供目的のフラグ。`Ledger.append()` は次回
    追記時にこれを自己修復する）。"""

    ok: bool
    entries_verified: int
    truncated_tail: bool
    tamper_at_seq: int | None
    detail: str
    missing_final_newline: bool = False


class LedgerChainInvalidError(RuntimeError):
    """`Ledger.append()` が、既存台帳の完全な行 (`_split_complete_lines` が
    返す `raw_lines`) 全体を chain 検証した結果、末尾行だけでなく途中の行に
    不整合（`entry_sha`/`prev_sha` 不一致 = 改竄、`seq` 欠番、JSON 破損）を
    検出した際に送出する（Codex レビュー 2026-09-01 P1 finding #4）。

    従来の `append()` は末尾 1 行の `seq`/`entry_sha` のみを読んで次の
    `seq`/`prev_sha` を導出しており、途中のどこかの行が改竄された台帳へも
    気付かずに新エントリを継ぎ足せてしまっていた（改竄箇所より後ろの行は
    改竄後に再計算された "整合する" chain として偽装できるため、末尾のみの
    検査では検出できない）。本チェックは `fcntl.flock` を保持したまま
    `verify_chain()` と同じ判定ロジック (`_verify_chain_prefix`) を
    on-disk の現在の内容に対して再実行し、prefix 全体が正当な場合のみ
    追記を許す。この例外が送出された時点でファイルには一切書き込みが
    行われていない（fail-closed）。O(n)（n=台帳の既存エントリ数）の
    追加コストは、append 1 回ごとに全 chain の完全性を保証するための
    設計正本 §7 の優先事項として許容する。
    """

    def __init__(self, path: Path, detail: str, tamper_at_seq: int | None) -> None:
        self.path = path
        self.detail = detail
        self.tamper_at_seq = tamper_at_seq
        super().__init__(
            f"Ledger.append refused: existing chain integrity check failed: {detail} (path={path})"
        )


class LedgerTruncatedTailError(RuntimeError):
    """`Ledger.append()` が、既存ファイルの末尾が truncated（write 中断で不完全な
    最終行）だと検出した際に送出する。既存の破損した bytes へ盲目的に追記して
    さらなる破損を積み重ねることを防ぐ fail-closed 契約（Codex レビュー
    2026-09-01 採用）。呼び出し側は台帳を手動で修復（truncated tail の切除、
    または別経路での再構成）してから再試行すること。"""

    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"Ledger.append refused: {detail} (path={path})")


def _entry_sha(seq: int, prev_sha: str, payload: Mapping[str, Any]) -> str:
    return manifest_sha({"seq": seq, "prev_sha": prev_sha, "payload": _jsonable(payload)})


def _split_complete_lines(content: str) -> tuple[list[str], bool, bool]:
    """ファイル内容を「改行区切りの完全な行」の列と「末尾 truncated 判定」に
    分解する。`verify_chain()` と `Ledger.append()` の両方が使う共通ロジック
    （Codex レビュー 2026-09-01 採用: append 側も同じ判定を再利用することで、
    truncated tail への追記と検証の判定基準を一致させる）。

    戻り値は `(raw_lines, truncated_tail, missing_final_newline)`。

    `missing_final_newline`（Codex レビュー 2026-09-01 P1 追加）: ファイルの
    最終行が改行で終端されていないが、JSON としては完全にパース可能な場合
    （＝末尾で write が中断されたのではなく、単に区切り改行だけが欠けている
    場合）に `True` となる。この場合 `truncated_tail` は `False` に戻す
    （chain としては正当に検証できるため）が、`Ledger.append()` 側はこの
    フラグを見て区切り改行を自己修復してから追記する必要がある（さもないと
    次の JSON 行と `}{` のように直接連結し破損する）。
    """
    truncated_tail = False
    missing_final_newline = False
    if content and not content.endswith("\n"):
        truncated_tail = True
    raw_lines = [ln for ln in content.splitlines() if ln.strip()]
    if truncated_tail and raw_lines:
        try:
            json.loads(raw_lines[-1])
            truncated_tail = False
            missing_final_newline = True
        except json.JSONDecodeError:
            raw_lines = raw_lines[:-1]
    return raw_lines, truncated_tail, missing_final_newline


def _verify_chain_prefix(
    raw_lines: Sequence[str], truncated_tail: bool, missing_final_newline: bool
) -> ChainVerification:
    """`raw_lines`（`_split_complete_lines` が返す、改行区切りの完全な行の列）
    に対して entry_sha 連鎖検証を行う純関数（I/O なし）。`verify_chain()`
    （ファイルを読んでから呼ぶ）と `Ledger.append()`（既に排他ロック下で
    読んだ内容をそのまま渡す、Codex レビュー 2026-09-01 P1 finding #4）の
    両方が共有する判定ロジック本体。`truncated_tail`/`missing_final_newline`
    は呼び出し側が `_split_complete_lines` から得た値をそのまま渡す。
    """
    prev = GENESIS_PREV_SHA
    verified = 0
    for i, line in enumerate(raw_lines):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            return ChainVerification(
                ok=False,
                entries_verified=verified,
                truncated_tail=True,
                tamper_at_seq=None,
                detail=f"unparseable line at position {i}",
                missing_final_newline=missing_final_newline,
            )
        missing_keys = (
            sorted(_ENTRY_REQUIRED_KEYS - set(raw.keys()))
            if isinstance(raw, Mapping)
            else sorted(_ENTRY_REQUIRED_KEYS)
        )
        if missing_keys:
            return ChainVerification(
                ok=False,
                entries_verified=verified,
                truncated_tail=truncated_tail,
                tamper_at_seq=i,
                detail=(
                    f"malformed entry at position {i} (seq slot {i}): "
                    f"missing required field(s) {missing_keys}"
                ),
                missing_final_newline=missing_final_newline,
            )
        if raw.get("seq") != i:
            return ChainVerification(
                ok=False,
                entries_verified=verified,
                truncated_tail=truncated_tail,
                tamper_at_seq=i,
                detail=f"seq mismatch at position {i}: expected {i}, got {raw.get('seq')}",
                missing_final_newline=missing_final_newline,
            )
        if raw.get("prev_sha") != prev:
            return ChainVerification(
                ok=False,
                entries_verified=verified,
                truncated_tail=truncated_tail,
                tamper_at_seq=i,
                detail=f"prev_sha mismatch at seq {i} (sibling branch or tamper)",
                missing_final_newline=missing_final_newline,
            )
        expected = _entry_sha(i, prev, raw.get("payload", {}))
        if expected != raw.get("entry_sha"):
            return ChainVerification(
                ok=False,
                entries_verified=verified,
                truncated_tail=truncated_tail,
                tamper_at_seq=i,
                detail=f"entry_sha mismatch at seq {i} (content tamper)",
                missing_final_newline=missing_final_newline,
            )
        prev = raw["entry_sha"]
        verified += 1

    return ChainVerification(
        ok=not truncated_tail,
        entries_verified=verified,
        truncated_tail=truncated_tail,
        tamper_at_seq=None,
        detail=(
            "chain verified"
            if not truncated_tail and not missing_final_newline
            else "chain verified up to truncated tail (fail-closed: ok=False)"
            if truncated_tail
            else "chain verified (final line missing trailing newline, self-healed on next append)"
        ),
        missing_final_newline=missing_final_newline,
    )


_UNSEAL_PREREQUISITE_KINDS: Mapping[str, str] = {
    "baseline_audit_sha": "baseline_audit",
    "candidate_space_sha": "candidate_space",
    "selection_rule_sha": "selection_rule",
    "selected_candidate_sha": "selected_candidate",
}
_UNSEAL_COMMITMENT_KEYS: tuple[str, ...] = tuple(_UNSEAL_PREREQUISITE_KINDS)


def _is_sha256_hex(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _valid_unseal_prerequisite_payload(
    payload: Mapping[str, Any], expected_kind: str
) -> bool:
    """Validate the minimum frozen event envelope for an unseal prerequisite.

    The canonical design requires every procedural evidence event to carry the
    hash of the object it attests to.  A kind-only ledger row therefore cannot
    satisfy an unseal prerequisite.  The selected-candidate prerequisite also
    needs the selected candidate identity; otherwise it is not a candidate
    selection record at all.  Deeper artifact semantics remain committed by the
    64-hex ``artifact_sha`` and are outside this ledger-level seal check.
    """
    if payload.get("kind") != expected_kind:
        return False
    if not _is_sha256_hex(payload.get("artifact_sha")):
        return False
    if expected_kind == "selected_candidate":
        candidate_id = payload.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            return False
    return True


def _references_prior_prerequisites(
    payload: Mapping[str, Any],
    prior_entries_by_sha: Mapping[str, LedgerEntry],
) -> bool:
    """Resolve each frozen prerequisite SHA to its canonical prior ledger event.

    The four §7 commitment fields are entry-SHA references, not arbitrary digest
    strings.  Their canonical event kind is the field name without the `_sha`
    suffix.  Because `prior_entries_by_sha` is populated only from entries that
    precede the payload being validated, this simultaneously proves existence,
    ordering, kind, and cryptographic linkage into the already-verified chain.
    """
    for key, expected_kind in _UNSEAL_PREREQUISITE_KINDS.items():
        ref = payload.get(key)
        if not _is_sha256_hex(ref):
            return False
        prerequisite = prior_entries_by_sha.get(ref)
        if prerequisite is None:
            return False
        prerequisite_payload = prerequisite.payload
        if not isinstance(prerequisite_payload, Mapping):
            return False
        if not _valid_unseal_prerequisite_payload(prerequisite_payload, expected_kind):
            return False
    return True


def _verified_holdout_unseal_seq(ledger_entries: Sequence[LedgerEntry]) -> int | None:
    """Return the first valid holdout-unseal boundary, or None fail-closed.

    A valid unseal is in a fully verified ledger chain and has the strict order:
    four canonical prerequisite events -> `selection_frozen` -> `holdout_unseal`.
    Both selection and unseal must carry the same four entry-SHA references, and
    `holdout_unseal` must reference the prior selection event by entry SHA.  A
    caller-supplied integer or four merely well-formed 64-hex strings can never
    create an unseal boundary.
    """
    prev_sha = GENESIS_PREV_SHA
    for expected_seq, entry in enumerate(ledger_entries):
        if entry.seq != expected_seq or entry.prev_sha != prev_sha:
            return None
        expected_sha = _entry_sha(entry.seq, entry.prev_sha, entry.payload)
        if entry.entry_sha != expected_sha:
            return None
        prev_sha = entry.entry_sha

    prior_entries_by_sha: dict[str, LedgerEntry] = {}
    frozen_by_sha: dict[str, Mapping[str, Any]] = {}
    for entry in ledger_entries:
        payload = entry.payload
        if not isinstance(payload, Mapping):
            prior_entries_by_sha[entry.entry_sha] = entry
            continue
        kind = payload.get("kind")
        if kind == "selection_frozen":
            if _references_prior_prerequisites(payload, prior_entries_by_sha):
                frozen_by_sha[entry.entry_sha] = payload
            prior_entries_by_sha[entry.entry_sha] = entry
            continue
        if kind == "holdout_unseal":
            freeze_sha = payload.get("selection_freeze_event_sha")
            if _is_sha256_hex(freeze_sha):
                frozen = frozen_by_sha.get(freeze_sha)
                if (
                    frozen is not None
                    and _references_prior_prerequisites(payload, prior_entries_by_sha)
                    and all(
                        payload.get(key) == frozen.get(key)
                        for key in _UNSEAL_COMMITMENT_KEYS
                    )
                ):
                    return entry.seq
        prior_entries_by_sha[entry.entry_sha] = entry
    return None


def _verified_split_freeze_commitment(
    ledger_entries: Sequence[LedgerEntry],
) -> tuple[str, str] | None:
    """Return the unique pre-measurement split-freeze commitments, fail closed.

    The ledger is the existing provenance authority boundary.  A valid
    ``split_frozen`` event must be in a fully valid chain, occur before any render
    or meter call, and carry both the realized-map hash and the SHA-256 commitment
    of the runtime split secret.  Multiple/ill-shaped freeze declarations are
    ambiguous and therefore rejected.
    """
    prev_sha = GENESIS_PREV_SHA
    for expected_seq, entry in enumerate(ledger_entries):
        if entry.seq != expected_seq or entry.prev_sha != prev_sha:
            return None
        if entry.entry_sha != _entry_sha(entry.seq, entry.prev_sha, entry.payload):
            return None
        prev_sha = entry.entry_sha

    frozen: tuple[str, str] | None = None
    for entry in ledger_entries:
        payload = entry.payload
        if not isinstance(payload, Mapping):
            continue
        kind = payload.get("kind")
        if kind in ("render", "meter_call") and frozen is None:
            return None
        if kind != "split_frozen":
            continue
        if frozen is not None:
            return None
        realized_hash = payload.get("realized_split_map_hash")
        seal_commitment = payload.get("seal_commitment")
        if not _is_sha256_hex(realized_hash) or not _is_sha256_hex(seal_commitment):
            return None
        frozen = (realized_hash, seal_commitment)
    return frozen


class Ledger:
    """append-only JSONL 台帳。

    各行 = `{"seq": n, "prev_sha": ..., "entry_sha": sha256(canonical_json(
    {"seq": n, "prev_sha": ..., "payload": ...})), "payload": ...}`。

    [UNDERSPEC-CAL-07] 設計正本 §7 は `entry_sha = sha256(canonical_json(payload
    + prev_sha))` とだけ書く。本実装は `seq` も digest 対象へ含める（chain 上の
    位置そのものを署名に取り込み、同一 payload+prev_sha を異なる位置へ複製する
    攻撃の余地を減らすため）。より広い対象を digest する保守的な選択であり、
    設計正本の要求を弱めていない。

    `append()` は `fcntl.flock(LOCK_EX)` を **先に** 獲得し、そのロックを保持した
    まま on-disk の tail を読み直して `seq`/`prev_sha` を導出し、
    write → flush → `os.fsync` の順で行ってからロックを解放する（単一 writer
    境界の契約。モジュール docstring 参照）。プロセス内キャッシュ
    (`self._entries`) から `seq`/`prev_sha` を先に決めてしまうと、同一パスへの
    複数 `Ledger` インスタンスの交互 append で兄弟 `seq=0` が発生しうるため
    （Codex レビュー 2026-09-01 採用）。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries: list[LedgerEntry] = []
        self._malformed: list[MalformedLedgerLine] = []
        if path.exists():
            self._entries, self._malformed = self._read_all()

    def _read_all(self) -> tuple[list[LedgerEntry], list[MalformedLedgerLine]]:
        """既存ファイルから読み込む（キャッシュ用途）。`(entries, malformed)`
        を返す。

        末尾の truncated/不完全な行は静かにスキップする（クラッシュさせない。
        `_split_complete_lines` を再利用し、`verify_chain()`/`append()` と
        同じ行分割・truncated 判定基準を共有する）。

        JSON としてはパース可能だが `LedgerEntry` に必須のフィールド
        （`_ENTRY_REQUIRED_KEYS`）を欠く「構造的 malformed」な行（例:
        `{"seq": 0}`）に対しても、`raw["prev_sha"]` のような直接インデックス
        で `KeyError` を送出してはならない（Codex レビュー 2026-09-01 P1
        finding #3: 従来はここで `KeyError` が `Ledger.__init__` へそのまま
        伝播し、`verify_chain()` を呼ぶ前に構築自体がクラッシュしていた ―
        「破損を検出して報告する」という契約を満たせていなかった）。
        malformed な行は `LedgerEntry` としては構築せず、`MalformedLedgerLine`
        として別途記録して読み進める。

        破損の有無・種別についての権威ある判定は本メソッドの責務ではなく、
        `verify_chain()` がファイル全体を独立に読み直して行う
        （`_verify_chain_prefix` が同じ `_ENTRY_REQUIRED_KEYS` 検査を chain
        順序の文脈で再実行し、malformed な行の位置を `ok=False` +
        `tamper_at_seq` として報告する）。
        """
        content = self.path.read_text(encoding="utf-8")
        raw_lines, _truncated_tail, _missing_final_newline = _split_complete_lines(content)

        entries: list[LedgerEntry] = []
        malformed: list[MalformedLedgerLine] = []
        for i, line in enumerate(raw_lines):
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, Mapping) or not _ENTRY_REQUIRED_KEYS.issubset(raw.keys()):
                missing = (
                    sorted(_ENTRY_REQUIRED_KEYS - set(raw.keys()))
                    if isinstance(raw, Mapping)
                    else sorted(_ENTRY_REQUIRED_KEYS)
                )
                malformed.append(
                    MalformedLedgerLine(
                        line_index=i,
                        raw=line,
                        reason=f"missing required field(s) {missing}",
                    )
                )
                continue
            entries.append(
                LedgerEntry(
                    seq=raw["seq"],
                    prev_sha=raw["prev_sha"],
                    entry_sha=raw["entry_sha"],
                    payload=raw["payload"],
                )
            )
        return entries, malformed

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    @property
    def malformed_lines(self) -> tuple[MalformedLedgerLine, ...]:
        """`_read_all()` が検出した構造的 malformed 行（Codex レビュー
        2026-09-01 P1 finding #3）。空タプルは「malformed 行なし」を意味する。
        権威ある chain 検証結果は `verify_chain()` を使うこと（本 property は
        `Ledger(path)` 構築時点でのスナップショットに過ぎず、on-disk 内容が
        構築後に変化していれば古くなる）。"""
        return tuple(self._malformed)

    def append(self, payload: Mapping[str, Any]) -> LedgerEntry:
        """payload を append する。

        **単一 writer 境界の契約**（Codex レビュー 2026-09-01 採用、再修正）:
        `seq`/`prev_sha` は、この呼び出し **以前** にプロセス内でキャッシュされた
        `self._entries`（他インスタンスの append を反映しない可能性がある）から
        導出してはならない。`fcntl.flock(LOCK_EX)` を先に獲得し、そのロックを
        保持したまま on-disk の現在のファイル末尾を読み直して `seq`/`prev_sha`
        を導出する。これにより、同一パスに対する複数 `Ledger` インスタンス
        （プロセス内・プロセス間を問わない）が交互に `append()` しても、
        兄弟 `seq=0` の重複や chain 分岐が起きない。

        **末尾改行の自己修復**（Codex レビュー 2026-09-01 P1 採用）: 既存
        ファイルの最終行が改行未終端でも、JSON としては完全にパース可能なら
        （＝write 中断ではなく、区切り改行だけが欠けている）`append()` はこれを
        拒否せず、排他ロックを保持したまま欠けた `"\n"` をまず書いてから通常
        どおり追記する。1 件もエントリを失っていない正当な chain を
        `LedgerTruncatedTailError` で reject するより安全（かつ、この自己修復
        をしないと次の追記が `}{` のように直接連結して破損する）。
        `verify_chain()` はこの状態を `missing_final_newline=True` として
        報告する（fail-closed の対象外）。

        **全 chain 検証**（Codex レビュー 2026-09-01 P1 finding #4）: 排他
        ロックを保持したまま、既読の全行 (`raw_lines`) に対して
        `verify_chain()` と同じ判定 (`_verify_chain_prefix`) を再実行し、
        途中の 1 行でも改竄・seq 欠番・破損があれば `LedgerChainInvalidError`
        を送出して書込を一切行わない（fail-closed）。コストは append 1 回
        あたり O(n)（n=既存エントリ数）だが、台帳全体の完全性を append の
        都度保証する設計正本 §7 の優先事項として許容する（末尾 1 行のみの
        検査では、改竄行より後ろを改竄後に再計算した "整合する" chain へ
        差し替えられていた場合に検出できない）。
        """
        payload_j = _jsonable(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.seek(0)
                content = f.read()
                raw_lines, truncated_tail, missing_final_newline = _split_complete_lines(content)
                if truncated_tail:
                    raise LedgerTruncatedTailError(
                        self.path,
                        "existing ledger tail is truncated (incomplete final line); "
                        "refusing to append onto partial bytes",
                    )
                chain_check = _verify_chain_prefix(
                    raw_lines, truncated_tail=False, missing_final_newline=missing_final_newline
                )
                if not chain_check.ok:
                    raise LedgerChainInvalidError(
                        self.path, chain_check.detail, chain_check.tamper_at_seq
                    )
                if missing_final_newline:
                    f.seek(0, os.SEEK_END)
                    f.write("\n")
                if raw_lines:
                    try:
                        tail_raw = json.loads(raw_lines[-1])
                        seq = int(tail_raw["seq"]) + 1
                        prev_sha = str(tail_raw["entry_sha"])
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                        raise LedgerTruncatedTailError(
                            self.path,
                            "existing ledger tail line is unparseable/malformed; refusing to append",
                        ) from exc
                else:
                    seq = 0
                    prev_sha = GENESIS_PREV_SHA

                entry_sha = _entry_sha(seq, prev_sha, payload_j)
                entry = LedgerEntry(
                    seq=seq, prev_sha=prev_sha, entry_sha=entry_sha, payload=payload_j
                )
                line = canonical_json(
                    {
                        "seq": entry.seq,
                        "prev_sha": entry.prev_sha,
                        "entry_sha": entry.entry_sha,
                        "payload": entry.payload,
                    }
                )
                f.seek(0, os.SEEK_END)
                f.write(line)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        self._entries, self._malformed = self._read_all()
        return entry

    def verify_chain(self) -> ChainVerification:
        """entry_sha 連鎖を検証する。

        - `truncated_tail`: ファイル末尾の 1 行が JSON として不完全（write が
          中断された痕跡）。この場合、末尾行を除いた prefix は正当な可能性がある。
        - `tamper_at_seq`: `prev_sha` 不一致（同一 chain 上の sibling 分岐、
          または内容改竄）を検出した最初の seq。
        - `missing_final_newline`（Codex レビュー 2026-09-01 P1 追加）: 最終行が
          改行未終端だが JSON としては完全にパース可能だった場合に `True`。
          1 エントリも失われていないため fail-closed の対象ではない
          （content が正当であれば `ok=True` のまま。情報提供目的のみ）。
        """
        truncated_tail = False
        missing_final_newline = False
        raw_lines: list[str] = []
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as f:
                content = f.read()
            raw_lines, truncated_tail, missing_final_newline = _split_complete_lines(content)

        return _verify_chain_prefix(raw_lines, truncated_tail, missing_final_newline)

    @staticmethod
    def check_leakage(
        ledger_entries: Sequence[LedgerEntry],
        holdout_row_ids: Iterable[str],
        unseal_seq: int | None,
        control_row_ids: Collection[str] = (),
        realized_split_map: RealizedSplitMap | None = None,
        split_verification_rows: Sequence[RowInput] = (),
        split_secret: bytes | None = None,
    ) -> LeakageCheckResult:
        """Fail closed on pre-unseal holdout access.

        The authoritative unseal boundary is derived from a cryptographically
        verified `holdout_unseal` ledger event with matching frozen prerequisite
        references.  `unseal_seq` is retained only as an optional expected-sequence
        assertion for compatibility; it can never grant access by itself.

        The protected row set is derived only after four independent checks agree:
        (1) the verification rows contain the complete canonical frozen matrix row-id
        set, (2) the realized map covers that same closed set, (3) `verify_split`
        mechanically reproduces the realized map, and (4) a valid pre-measurement
        `split_frozen` ledger event binds both `realized_sha` and SHA-256(split_secret).
        Thus neither caller-supplied rows, secret, nor a self-consistent reduced split
        can shrink the seal.  `holdout_row_ids` is only an equality assertion against
        the authenticated map.
        """
        verified_unseal_seq = _verified_holdout_unseal_seq(ledger_entries)
        if unseal_seq is not None and unseal_seq != verified_unseal_seq:
            verified_unseal_seq = None

        declared_holdout_set = set(holdout_row_ids)
        if (
            realized_split_map is None
            or split_secret is None
            or not split_verification_rows
            or not realized_split_map.assignment
        ):
            return LeakageCheckResult(blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0)

        from voice_genesis.calibration.fixtures.matrix import build_matrix

        canonical_matrix = build_matrix()
        canonical_by_id = {row.row_id: row for row in canonical_matrix}
        canonical_row_ids = set(canonical_by_id)
        verification_row_ids = [row.row_id for row in split_verification_rows]
        if (
            len(verification_row_ids) != len(set(verification_row_ids))
            or set(verification_row_ids) != canonical_row_ids
            or set(realized_split_map.assignment) != canonical_row_ids
        ):
            return LeakageCheckResult(blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0)

        stratum_factor_names = tuple(realized_split_map.stratum_factor_names)
        if (
            any(
                not isinstance(name, str) or not name.strip()
                for name in stratum_factor_names
            )
            or len(stratum_factor_names) != len(set(stratum_factor_names))
        ):
            return LeakageCheckResult(blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0)

        canonical_split_inputs: dict[str, RowInput] = {}
        for matrix_row in canonical_matrix:
            fixture_row = matrix_row.row
            dataclass_fields = type(fixture_row).__dataclass_fields__
            canonical_stratum: dict[str, Any] = {}
            for factor_name in stratum_factor_names:
                if factor_name == "truth_level":
                    factor_value = fixture_row.block
                elif factor_name in {"boundary_class", "domain"}:
                    factor_value = matrix_row.domain.value
                elif factor_name in dataclass_fields:
                    factor_value = getattr(fixture_row, factor_name)
                else:
                    return LeakageCheckResult(
                        blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0
                    )
                try:
                    hash(factor_value)
                except TypeError:
                    return LeakageCheckResult(
                        blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0
                    )
                canonical_stratum[factor_name] = factor_value

            canonical_split_inputs[matrix_row.row_id] = RowInput(
                row_id=matrix_row.row_id,
                family=fixture_row.family,
                stratum=canonical_stratum,
                truth_level=fixture_row.block,
                generator_impl=fixture_row.generator_impl,
                boundary_class=matrix_row.domain.value,
            )

        for supplied in split_verification_rows:
            expected = canonical_split_inputs[supplied.row_id]
            if (
                supplied.family != expected.family
                or dict(supplied.stratum) != dict(expected.stratum)
                or supplied.truth_level != expected.truth_level
                or supplied.generator_impl != expected.generator_impl
                or supplied.boundary_class != expected.boundary_class
            ):
                return LeakageCheckResult(
                    blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0
                )

        try:
            split_verified = verify_split(
                split_verification_rows,
                split_secret,
                realized_split_map,
            )
        except (KeyError, TypeError, ValueError):
            split_verified = False
        if not split_verified:
            return LeakageCheckResult(blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0)

        frozen_split = _verified_split_freeze_commitment(ledger_entries)
        if frozen_split is None:
            return LeakageCheckResult(blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0)
        frozen_map_hash, frozen_secret_commitment = frozen_split
        if (
            frozen_map_hash != realized_split_map.realized_sha
            or frozen_secret_commitment != hashlib.sha256(split_secret).hexdigest()
        ):
            return LeakageCheckResult(blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0)

        from voice_genesis.calibration.vocab import Split

        authenticated_holdout_set = {
            row_id
            for row_id, split in realized_split_map.assignment.items()
            if split == Split.HOLDOUT or split == Split.HOLDOUT.value
        }
        if not authenticated_holdout_set or declared_holdout_set != authenticated_holdout_set:
            return LeakageCheckResult(blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0)
        holdout_set = authenticated_holdout_set
        # ``control_row_ids`` is not an authority boundary.  A caller may request
        # exemption only for rows that the committed frozen matrix independently
        # identifies as truth-free negative controls.  Truth-bearing/unknown rows
        # supplied here remain sealed.
        from voice_genesis.calibration.fixtures.controls import negative_control_row_ids
        from voice_genesis.calibration.fixtures.matrix import build_matrix

        frozen_negative_controls = negative_control_row_ids(build_matrix())
        control_set = set(control_row_ids) & set(frozen_negative_controls)
        control_excluded_count = 0
        for entry in ledger_entries:
            payload = entry.payload
            row_id = payload.get("row_id") if isinstance(payload, Mapping) else None
            if row_id is None or row_id not in holdout_set:
                continue
            kind = payload.get("kind") if isinstance(payload, Mapping) else None
            if kind not in ("render", "meter_call"):
                continue
            if row_id in control_set:
                control_excluded_count += 1
                continue
            if verified_unseal_seq is None or entry.seq < verified_unseal_seq:
                return LeakageCheckResult(
                    blocked=BlockedCode.BLOCKED_LEAKAGE,
                    control_excluded_count=control_excluded_count,
                )
        return LeakageCheckResult(blocked=None, control_excluded_count=control_excluded_count)
