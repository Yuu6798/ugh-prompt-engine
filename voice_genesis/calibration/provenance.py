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
import json
import os
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, is_dataclass
from pathlib import Path
from typing import Any

from voice_genesis.calibration.canonical import canonical_json, manifest_sha
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
    truncated/不完全行と prev_sha 不一致（sibling 分岐・改竄）を区別する。"""

    ok: bool
    entries_verified: int
    truncated_tail: bool
    tamper_at_seq: int | None
    detail: str


def _entry_sha(seq: int, prev_sha: str, payload: Mapping[str, Any]) -> str:
    return manifest_sha({"seq": seq, "prev_sha": prev_sha, "payload": _jsonable(payload)})


class Ledger:
    """append-only JSONL 台帳。

    各行 = `{"seq": n, "prev_sha": ..., "entry_sha": sha256(canonical_json(
    {"seq": n, "prev_sha": ..., "payload": ...})), "payload": ...}`。

    [UNDERSPEC-CAL-07] 設計正本 §7 は `entry_sha = sha256(canonical_json(payload
    + prev_sha))` とだけ書く。本実装は `seq` も digest 対象へ含める（chain 上の
    位置そのものを署名に取り込み、同一 payload+prev_sha を異なる位置へ複製する
    攻撃の余地を減らすため）。より広い対象を digest する保守的な選択であり、
    設計正本の要求を弱めていない。

    `append()` は `fcntl.flock(LOCK_EX)` で排他制御し、書込は
    write → flush → `os.fsync` の順で行ってからロックを解放する（単一 writer
    境界の契約。モジュール docstring 参照）。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries: list[LedgerEntry] = list(self._read_all()) if path.exists() else []

    def _read_all(self) -> Iterable[LedgerEntry]:
        """既存ファイルから読み込む（キャッシュ用途）。

        末尾の truncated/不完全な行は静かにスキップする（クラッシュさせない）。
        破損の有無・種別の権威ある判定は `verify_chain()` が独立にファイル全体
        を読み直して行う。ここで例外を投げると `Ledger(path)` の構築自体が
        破損ファイルに対して失敗し、`verify_chain()` を呼ぶ前にクラッシュして
        しまう（それでは「破損を検出して報告する」という契約を満たせない）。
        """
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    raw = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                yield LedgerEntry(
                    seq=raw["seq"],
                    prev_sha=raw["prev_sha"],
                    entry_sha=raw["entry_sha"],
                    payload=raw["payload"],
                )

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    def append(self, payload: Mapping[str, Any]) -> LedgerEntry:
        payload_j = _jsonable(payload)
        prev_sha = self._entries[-1].entry_sha if self._entries else GENESIS_PREV_SHA
        seq = len(self._entries)
        entry_sha = _entry_sha(seq, prev_sha, payload_j)
        entry = LedgerEntry(seq=seq, prev_sha=prev_sha, entry_sha=entry_sha, payload=payload_j)
        line = canonical_json(
            {"seq": entry.seq, "prev_sha": entry.prev_sha, "entry_sha": entry.entry_sha,
             "payload": entry.payload}
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(line)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        self._entries.append(entry)
        return entry

    def verify_chain(self) -> ChainVerification:
        """entry_sha 連鎖を検証する。

        - `truncated_tail`: ファイル末尾の 1 行が JSON として不完全（write が
          中断された痕跡）。この場合、末尾行を除いた prefix は正当な可能性がある。
        - `tamper_at_seq`: `prev_sha` 不一致（同一 chain 上の sibling 分岐、
          または内容改竄）を検出した最初の seq。
        """
        truncated_tail = False
        raw_lines: list[str] = []
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as f:
                content = f.read()
            if content and not content.endswith("\n"):
                truncated_tail = True
            raw_lines = [ln for ln in content.splitlines() if ln.strip()]
            if truncated_tail and raw_lines:
                # 最後の非空行が truncated 行そのもの: JSON parse を試し、失敗
                # すればそれを truncated 行として除外する。parse に成功する
                # （＝改行だけが欠けた完全な行だった）場合は truncated 扱いを
                # 取り消す。
                try:
                    json.loads(raw_lines[-1])
                    truncated_tail = False
                except json.JSONDecodeError:
                    raw_lines = raw_lines[:-1]

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
                )
            if raw.get("seq") != i:
                return ChainVerification(
                    ok=False,
                    entries_verified=verified,
                    truncated_tail=truncated_tail,
                    tamper_at_seq=i,
                    detail=f"seq mismatch at position {i}: expected {i}, got {raw.get('seq')}",
                )
            if raw.get("prev_sha") != prev:
                return ChainVerification(
                    ok=False,
                    entries_verified=verified,
                    truncated_tail=truncated_tail,
                    tamper_at_seq=i,
                    detail=f"prev_sha mismatch at seq {i} (sibling branch or tamper)",
                )
            expected = _entry_sha(i, prev, raw.get("payload", {}))
            if expected != raw.get("entry_sha"):
                return ChainVerification(
                    ok=False,
                    entries_verified=verified,
                    truncated_tail=truncated_tail,
                    tamper_at_seq=i,
                    detail=f"entry_sha mismatch at seq {i} (content tamper)",
                )
            prev = raw["entry_sha"]
            verified += 1

        return ChainVerification(
            ok=True,
            entries_verified=verified,
            truncated_tail=truncated_tail,
            tamper_at_seq=None,
            detail="chain verified" if not truncated_tail else "chain verified up to truncated tail",
        )

    @staticmethod
    def check_leakage(
        ledger_entries: Sequence[LedgerEntry],
        holdout_row_ids: Iterable[str],
        unseal_seq: int | None,
        control_row_ids: Collection[str] = (),
    ) -> LeakageCheckResult:
        """holdout row_id が unseal (`unseal_seq`) より前の render/meter-call
        entry に初出した場合 `BLOCKED_LEAKAGE`。`unseal_seq=None` は
        「unseal 未実施」を意味し、holdout row への一切のアクセスが違反となる。

        `control_row_ids`（IMPLEMENTATION_MAP_v1.md §2.7「control 共有契約」:
        negative/positive control 行は sweep truth を運ばず、family 全体に
        直交する固定 fixture として split を跨いで再利用されるため、たとえ
        holdout 側に属していても事前アクセスは leakage ではない）に含まれる
        row_id は、unseal 前の render/meter-call entry に現れても
        `BLOCKED_LEAKAGE` の対象から除外する。除外が実際に働いたことを
        `LeakageCheckResult.control_excluded_count` で判別できるようにする
        （0 なら「除外ロジックが単に発火しなかった」と「control が空」を区別
        できないが、非 0 なら確実に除外が適用されたことを示す）。
        """
        holdout_set = set(holdout_row_ids)
        control_set = set(control_row_ids)
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
            if unseal_seq is None or entry.seq < unseal_seq:
                return LeakageCheckResult(
                    blocked=BlockedCode.BLOCKED_LEAKAGE,
                    control_excluded_count=control_excluded_count,
                )
        return LeakageCheckResult(blocked=None, control_excluded_count=control_excluded_count)
