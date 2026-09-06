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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from voice_genesis.calibration.canonical import canonical_json, manifest_sha
from voice_genesis.calibration.splitter import (
    RealizedSplitMap,
    RowInput,
    nuisance_axis_for_row,
    verify_split,
)
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
    適用されたことを判別できる。

    `reason`（round 22 ADOPT, `UNDERSPEC-CAL-D50`）: `blocked` は
    `vocab.BlockedCode` の閉語彙（§3.3、事後追加禁止）に縛られるため、その
    語彙を拡張せずに `BLOCKED_LEAKAGE` の内訳を区別する補助フィールド。既定は
    `None`（従来どおりの汎用 `BLOCKED_LEAKAGE`）。`holdout_unseal` の選択チェーン
    参照は正当だが Gate 3 参照（`gate3_accepted_sha`）の検証に失敗した場合のみ
    `"UNSEAL_GATE3_UNVERIFIED"` を持つ。D105（`UNDERSPEC-CAL-D105`）で追加した
    2 値: `split_verification_rows` の 1 行以上が独立再構築した canonical row
    と（`nuisance_axis` を含む）属性不一致の場合は `"SPLIT_VERIFICATION_ROW_
    MISMATCH"`、行属性は一致するのに `verify_split()` の再導出が凍結済み
    `realized_split_map` と一致しない場合は `"SPLIT_REDERIVATION_MISMATCH"`
    （どちらも汎用 `BLOCKED_LEAKAGE` の内訳区別であり、素通りして無言で
    fail-closed していた D105 実障害の再発時に原因切り分けを速める目的）。"""

    blocked: BlockedCode | None
    control_excluded_count: int
    reason: str | None = None


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


class LedgerArchivedError(LedgerChainInvalidError):
    """`Ledger.append()` が、`self.path`（`ledger.jsonl`）が存在せず、かつ
    同一ディレクトリに archive 成果物（`<name>.gz` および/または
    `<name>.sha256` sidecar。`tools/archive_aborted_ledger.py::
    ensure_archived()` が campaign_closed 後に原本を置換した状態）が
    残っている場合に送出する（Codex レビュー PR #346 round 19 指摘,
    "Refuse to recreate a ledger after archival", 採用）。

    修正前は、欠落した `path` を「まだ何も書かれていない新規 chain」と
    区別せずに `a+b` で `open()` していたため、archive 済み campaign へ
    append すると genesis（`seq=0`）の新しい `ledger.jsonl` を黙って作って
    しまっていた。append 自体は成功を報告するが、その event は既に公開
    済みの gz snapshot には存在せず、以後の `ensure_archived()` 呼び出しは
    新しい `ledger.jsonl` が公開済み gz の厳密な byte-prefix 拡張ではない
    ため非 prefix 乖離として拒否する — 同一 campaign について矛盾する
    2 つの provenance artifact（公開済み gz/sidecar と、それに含まれない
    event を持つ新規 ledger.jsonl）が残ってしまう。

    R25-2 fix (Codex PR #346 round 25 finding (2) 採用, "Reject appends
    when either archive artifact remains"): 修正前はこのガードが gz と
    sidecar の**両方**が揃っている場合にしか発火しなかった（`and`）。
    完了済み archive が sidecar だけを失うと（gz のみ残存）、`path` 不在の
    まま append() が素通りして新しい genesis `ledger.jsonl` を作ってしまい、
    次の `ensure_archived()` 呼び出しはその新規 chain 自体が
    chain-valid であることしか確認しないため、これを canonical な原本だと
    誤認して唯一の正典だった gz を削除しうる（campaign 履歴の永久喪失）。
    gz/sidecar のどちらか一方でも残っていれば「archive 済みだが片方の
    成果物が失われた」状態であり、まっさらな新規 campaign（archive
    成果物が一切無い状態）とは区別できないため、いずれか一方の存在だけで
    fail-closed する（`or`）。この場合、呼び出し側は自動リトライせず、
    欠けた成果物を明示的に復旧（例えば残存 gz+sidecar から
    `ledger.jsonl` を復元する、または `tools/archive_aborted_ledger.py`
    側の手動回復手順を実行する）してから append をやり直す必要がある。

    本チェックは `LedgerChainInvalidError` と同じ安定ロック（`<name>.lock`。
    R14 fix）を保持したまま行うため、`ensure_archived()` の公開+unlink と
    この存在判定はロックにより直列化され、判定が古い状態を見て誤判定する
    競合は生じない。archive 成果物が一切存在しない純粋な新規 campaign の
    初期化（C0 freeze の genesis ledger 作成）はこのエラーの対象外であり、
    従来どおり `append()` から新規 `ledger.jsonl` を作成できる。"""

    def __init__(self, path: Path, *, gz_present: bool = True, sidecar_present: bool = True) -> None:
        gz_name = f"{path.name}.gz"
        sidecar_name = f"{path.name}.sha256"
        if gz_present and sidecar_present:
            artifact_detail = f"an archived pair ({gz_name} + {sidecar_name}) already exists"
        elif gz_present:
            artifact_detail = (
                f"only {gz_name} remains ({sidecar_name} is missing) — partial archive "
                "recovery is required"
            )
        else:
            artifact_detail = (
                f"only {sidecar_name} remains ({gz_name} is missing) — partial archive "
                "recovery is required"
            )
        detail = (
            f"ledger file is missing but {artifact_detail} in {path.parent}; this campaign "
            "has been archived and its ledger must not be recreated — the "
            "archived gz/sidecar pair is the sole canonical record"
        )
        super().__init__(path, detail, None)


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


def _last_nonblank_line_byte_range(content: str) -> tuple[int, int] | None:
    """`content` 内で `_split_complete_lines()` が返す `raw_lines` の最後の
    要素（最後の非空・完全行、改行文字を含まない本文）が占める UTF-8 バイト
    範囲 `(start, end)` を返す。該当行が無ければ `None`。

    `#345` 指摘③ 追補（末尾空行での偽 append 失敗）: `_set_watermark()` は
    従来、watermark 末尾エントリのバイト範囲を `_v_bytes`（ファイル全体の
    検証済みバイト長）から逆算していた（`last_end = v_bytes - 1` 等）。この
    逆算は「最終非空行がファイル末尾（改行未終端なら EOF、そうでなければ
    末尾の `\\n` の直前）で終わる」ことを前提にしており、有効な chain の末尾
    に空行が 1 行以上続く場合に成立しない（空行の分だけ実際の終端よりも
    後ろを指す）。本関数は `content.splitlines(keepends=True)` を 1 パスで
    走査し、`_split_complete_lines()` と同じ「空白のみの行は無視する」判定
    (`stripped.strip()`) で最後の非空行そのものの実バイト範囲を直接返す。
    ファイル全体の長さ (`_v_bytes`) や末尾改行の有無に一切依存しないため、
    末尾空行が何行続いても（`missing_final_newline` の場合を含め）常に
    正しい範囲を指す。"""
    offset = 0
    result: tuple[int, int] | None = None
    for raw_line in content.splitlines(keepends=True):
        line_byte_len = len(raw_line.encode("utf-8"))
        stripped = raw_line.splitlines()[0]
        if stripped.strip():
            result = (offset, offset + len(stripped.encode("utf-8")))
        offset += line_byte_len
    return result


def _verify_chain_prefix(
    raw_lines: Sequence[str],
    truncated_tail: bool,
    missing_final_newline: bool,
    *,
    start_seq: int = 0,
    start_prev_sha: str = GENESIS_PREV_SHA,
) -> ChainVerification:
    """`raw_lines`（`_split_complete_lines` が返す、改行区切りの完全な行の列）
    に対して entry_sha 連鎖検証を行う純関数（I/O なし）。`verify_chain()`
    （ファイルを読んでから呼ぶ）と `Ledger.append()`（既に排他ロック下で
    読んだ内容をそのまま渡す、Codex レビュー 2026-09-01 P1 finding #4）の
    両方が共有する判定ロジック本体。`truncated_tail`/`missing_final_newline`
    は呼び出し側が `_split_complete_lines` から得た値をそのまま渡す。

    `start_seq`/`start_prev_sha`（UNDERSPEC-CAL-D84: `Ledger.append()` の
    増分検証）: 既定 (`0`/`GENESIS_PREV_SHA`) はファイル先頭からの検証
    （`verify_chain()`・旧来の `append()` フル検証と完全に同じ挙動）。
    `Ledger.append()` の watermark 経由の呼び出しは、`raw_lines` に
    watermark **以降**（suffix）の行だけを渡し、`start_seq`/`start_prev_sha`
    に watermark の `seq`/`entry_sha` を渡すことで、chain の続きとして
    検証する。`tamper_at_seq`/`detail` の `seq` は常にグローバルな
    （台帳全体での）`seq` 番号（`start_seq + i`）で報告する。
    """
    prev = start_prev_sha
    verified = 0
    for i, line in enumerate(raw_lines):
        expected_seq = start_seq + i
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
                tamper_at_seq=expected_seq,
                detail=(
                    f"malformed entry at position {i} (seq slot {expected_seq}): "
                    f"missing required field(s) {missing_keys}"
                ),
                missing_final_newline=missing_final_newline,
            )
        if raw.get("seq") != expected_seq:
            return ChainVerification(
                ok=False,
                entries_verified=verified,
                truncated_tail=truncated_tail,
                tamper_at_seq=expected_seq,
                detail=(
                    f"seq mismatch at position {i}: expected {expected_seq}, "
                    f"got {raw.get('seq')}"
                ),
                missing_final_newline=missing_final_newline,
            )
        if raw.get("prev_sha") != prev:
            return ChainVerification(
                ok=False,
                entries_verified=verified,
                truncated_tail=truncated_tail,
                tamper_at_seq=expected_seq,
                detail=f"prev_sha mismatch at seq {expected_seq} (sibling branch or tamper)",
                missing_final_newline=missing_final_newline,
            )
        expected = _entry_sha(expected_seq, prev, raw.get("payload", {}))
        if expected != raw.get("entry_sha"):
            return ChainVerification(
                ok=False,
                entries_verified=verified,
                truncated_tail=truncated_tail,
                tamper_at_seq=expected_seq,
                detail=f"entry_sha mismatch at seq {expected_seq} (content tamper)",
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

#: round 22 ADOPT (`UNDERSPEC-CAL-D50`): `holdout_unseal` must also reference a
#: prior chain-valid `gate3_accepted` event by entry-SHA (the same
#: `gate3_accepted_sha` field `campaign/unseal.py` already emits). Before this,
#: `_verified_holdout_unseal_seq` never inspected `gate3_accepted_sha` at all,
#: so a crafted/legacy `holdout_unseal` row with correct selection-chain
#: references but a missing or arbitrary Gate 3 reference was accepted as an
#: authorized boundary by `Ledger.check_leakage`.
_GATE3_ACCEPTED_KIND = "gate3_accepted"

#: `Ledger.append()` G2（`#345` 指摘③）: これらの `payload["kind"]` を追記する
#: 直前は、watermark 経由の O(1) 増分検証をスキップし、on-disk ledger 全体を
#: seq 0 から `_verify_chain_prefix` でフル検証してから書き込む（fail-closed）。
#: `campaign/state.py::LEDGER_KIND_FOR_PHASE`（D2 runner のフェーズ到達判定が
#: 見る kind 語彙）を権威とし、実際に emit される表記ゆれ（`baseline_audit`
#: 実装 vs `state.py` 側の `baseline_audited`）双方を含める。加えて
#: `campaign/unseal.py`/`render_stage.py`/`close.py` が記帳する `kind`（
#: `LEDGER_KIND_FOR_PHASE` 未収載の `gate3_accepted`/`holdout_render_valid`/
#: `split_secret_revealed`）と、`stage_summary`/`slice_summary`（フェーズでは
#: ないが campaign 進行の要約 terminal event）を明示的に加える。
_TRANSITION_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "fixture_valid",
        "baseline_audit",
        "baseline_audited",
        "f0_selection_frozen",
        "selection_frozen",
        "holdout_render_valid",
        "holdout_executed_valid",
        "gate3_accepted",
        "holdout_unseal",
        "campaign_closed",
        "split_secret_revealed",
        "stage_summary",
        "slice_summary",
    }
)


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


def _is_iso8601_utc_timestamp(value: object) -> bool:
    """round 23 ADOPT (3) (`[UNDERSPEC-CAL-D53]`): minimal ISO 8601 UTC check
    for `gate3_accepted.approved_at_utc`. `approvals._parse_gate3_payload`
    only requires a non-blank string (the approval-file shape check does not
    police format), so this ledger-side verifier is the first point that
    actually rejects a malformed or non-UTC timestamp before it is trusted as
    evidence. Requires an explicit UTC offset (`Z` or `+00:00` — the shape
    `campaign.unseal.unseal_campaign` copies verbatim from the approval
    record); a bare local timestamp is not "UTC" per the field's own name and
    this ruling.
    """
    if not isinstance(value, str) or not value:
        return False
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def _valid_gate3_accepted_payload(payload: Mapping[str, Any]) -> bool:
    """Validate the minimum frozen event envelope for the Gate 3 acceptance
    event a `holdout_unseal` must reference (round 22 ADOPT, `UNDERSPEC-CAL-D50`;
    extended round 23 ADOPT (3), `[UNDERSPEC-CAL-D53]`).

    Mirrors the *producer's* minimum approval envelope exactly as
    `campaign.unseal.unseal_campaign` emits it into the `gate3_accepted`
    ledger event — `approval_content_sha256` (64-char lowercase hex, the
    approval file's own content digest), `approver` (non-blank identity
    string), `approved_at_utc` (ISO 8601 UTC), and
    `seal_protection_level_accepted is True`. Before round 23 this function
    checked only `kind`/`seal_protection_level_accepted`, so a crafted
    `gate3_accepted` row carrying just those two fields — with no approver,
    no approval-content binding, and no timestamp — satisfied
    `_references_prior_gate3_acceptance()` and authorized an unseal boundary.
    `unseal_campaign` has always supplied all four fields (see
    `campaign/unseal.py`), so this tightening rejects only rows that could
    never have been legitimately emitted by it.
    """
    if payload.get("kind") != _GATE3_ACCEPTED_KIND:
        return False
    if payload.get("seal_protection_level_accepted") is not True:
        return False
    if not _is_sha256_hex(payload.get("approval_content_sha256")):
        return False
    approver = payload.get("approver")
    if not isinstance(approver, str) or not approver.strip():
        return False
    return _is_iso8601_utc_timestamp(payload.get("approved_at_utc"))


def _parse_iso8601_utc(value: object) -> datetime | None:
    """`[UNDERSPEC-CAL-D88]`(a) 独立実装: ISO 8601 UTC 文字列を `datetime` へ
    変換する（`Z` または `+00:00` の明示 UTC オフセットのみ許容）。
    `campaign.unseal._parse_iso8601_utc()`/`approvals._is_iso8601_utc_timestamp()`
    と同じ意味論。本モジュールは `unseal.py`/`approvals.py` の編集対象外
    ではないが、両モジュールが既に採用している「ISO 8601 UTC パーサを
    モジュールごとに独立実装として重複させる」方針（`unseal.py`
    `_parse_iso8601_utc()` の docstring 参照）をここでも踏襲する——本モジュール
    の `_is_iso8601_utc_timestamp()`（上記）は bool のみを返し、比較に使う
    `datetime` を返さないため別名で新設する。"""
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


#: `[UNDERSPEC-CAL-D88]`(a) design revision (Codex review, adopted): the
#: same clock-skew tolerance `campaign.unseal.unseal_campaign()`'s
#: `_CLOCK_SKEW_TOLERANCE_SECONDS` allows for a Gate 3 `approved_at_utc` up
#: to 60s ahead of the live check-time clock (`unseal.py:166-183`'s
#: `gate3_time <= now_utc + timedelta(seconds=_CLOCK_SKEW_TOLERANCE_
#: SECONDS)`) means a legitimate, freshly-approved Gate 3 can genuinely be
#: dated up to 60s after the `holdout_unseal` event this replay check
#: compares it against (that event is itself stamped moments later by the
#: *local* clock in the same `unseal_campaign()` call — see `unseal.py`'s
#: own `event_time_utc` field). A strict `gate3_time <= unseal_time` here
#: would therefore falsely reject a normal-path ledger whenever the
#: approval file's clock ran up to 60s ahead of the unsealing process's
#: own clock. Independently redefined here (not imported) per this
#: module's/`unseal.py`'s existing convention of duplicating small ISO
#: 8601 helpers/constants across modules rather than adding a cross-module
#: dependency — kept numerically identical to `unseal.py`'s constant and
#: pinned by tests on both sides.
_CLOCK_SKEW_TOLERANCE_SECONDS = 60


def _c0_freeze_ordering_violation(
    ledger_entries: Sequence[LedgerEntry],
    holdout_unseal_payload: Mapping[str, Any],
    gate3_accepted_payload: Mapping[str, Any],
) -> bool:
    """`[UNDERSPEC-CAL-D88]`(a): replay-verifier parity for the D85
    freeze-ordering check `campaign.unseal.unseal_campaign()` already
    enforces live (`freeze_time < gate3_time`, `unseal.py:166-183`) —
    before this fix, `_verified_holdout_unseal_detail()` never re-checked
    that ordering when independently replaying a ledger, so a `holdout_
    unseal` boundary that (by some means other than the live `unseal_
    campaign()` path — e.g. hand-crafted/corrupted ledger data) references
    a `gate3_accepted` event predating the campaign's own `c0_freeze`
    would still verify as a valid unseal boundary here.

    Only engaged when `ledger_entries[0]` is itself a `c0_freeze` event —
    the shape `campaign.state.load_frozen_campaign()` already requires of
    entry 0 for any ledger that represents an actual frozen campaign
    (`state.py:299`, `freeze_event = entries[0].payload`). A synthetic
    ledger built for narrow prerequisite/gate3-linkage unit tests, which
    never claims to model a full campaign lifecycle and does not start
    with `c0_freeze`, is unaffected by this new check — this function
    returns `False` (no violation *found*, i.e. the check does not apply)
    for it, exactly as `_verified_holdout_unseal_detail()` behaved before
    D88 for every ledger. Every real campaign/replay ledger always starts
    with `c0_freeze` (`c0_freeze.py:1417`), so production use is fully
    covered.

    Returns `True` (a violation — the caller must NOT accept this as a
    valid unseal boundary) iff entry 0 is `c0_freeze` and the ordering
    `freeze_time < gate3_time <= unseal_time + _CLOCK_SKEW_TOLERANCE_
    SECONDS` fails to hold, including when any of the three timestamps is
    missing/unparseable (fail-closed, matching D85's own ruling — see
    `unseal.py:166-183`). The upper bound carries the same 60s clock-skew
    tolerance `unseal.py` itself allows for Gate 3's `approved_at_utc`
    relative to the live check-time clock (see `_CLOCK_SKEW_TOLERANCE_
    SECONDS` above) — a strict `<=` would falsely reject a normal-path
    ledger whenever the approval file's clock ran ahead of the unsealing
    process's own clock by less than that same tolerance.

    R7 P2 fix (Codex PR #346 round 7 finding #2, `[UNDERSPEC-CAL-D79]`):
    `holdout_unseal.event_time_utc` did not exist before v1.1 —
    `campaign.unseal.unseal_campaign()` only started stamping it once this
    D88(a) check needed a local unseal-side timestamp to compare against.
    A v1.0-and-earlier ledger's `holdout_unseal` payload therefore has no
    `event_time_utc` key at all (not merely an unparseable one), and the
    pre-fix code treated that absence exactly like a parse failure —
    `unseal_time is None` — and fail-closed the whole ordering check,
    falsely invalidating replay/audit of every campaign closed before v1.1
    (e.g. the real closed campaign `RUN10-CAL-20260904-862dec28`) even
    though its `freeze_time < gate3_time` ordering genuinely holds. When
    the key is missing (`payload.get(...) is None`, as opposed to present
    but malformed — a malformed-but-present value still fails closed via
    `unseal_time is None` below, unchanged), this function now falls back
    to the pre-v1.1 ordering check: only the lower bound
    (`freeze_time < gate3_time`) is enforced, since there is no local
    unseal-side clock reading to bound the upper side against. Every
    ledger `unseal.py` writes today always includes `event_time_utc`, so
    new campaigns are unaffected and get the full two-sided check."""
    if not ledger_entries:
        return False
    first_payload = ledger_entries[0].payload
    if not isinstance(first_payload, Mapping) or first_payload.get("kind") != "c0_freeze":
        return False
    freeze_time = _parse_iso8601_utc(first_payload.get("event_time_utc"))
    gate3_time = _parse_iso8601_utc(gate3_accepted_payload.get("approved_at_utc"))
    raw_unseal_event_time = holdout_unseal_payload.get("event_time_utc")
    if raw_unseal_event_time is None:
        # R7 P2 fix: legacy (pre-v1.1) `holdout_unseal` — no local
        # unseal-side clock reading exists to bound the upper side against,
        # so fall back to the lower-bound-only ordering check.
        if freeze_time is None or gate3_time is None:
            return True
        return not (freeze_time < gate3_time)
    unseal_time = _parse_iso8601_utc(raw_unseal_event_time)
    if freeze_time is None or gate3_time is None or unseal_time is None:
        return True
    unseal_upper_bound = unseal_time + timedelta(seconds=_CLOCK_SKEW_TOLERANCE_SECONDS)
    return not (freeze_time < gate3_time <= unseal_upper_bound)


def _references_prior_gate3_acceptance(
    payload: Mapping[str, Any],
    prior_entries_by_sha: Mapping[str, LedgerEntry],
) -> bool:
    """Resolve `holdout_unseal.gate3_accepted_sha` to a prior chain-valid
    `gate3_accepted` event (round 22 ADOPT, `UNDERSPEC-CAL-D50`).

    `prior_entries_by_sha` is populated only from entries that precede the
    `holdout_unseal` payload being validated (see `_verified_holdout_unseal_seq`),
    so this simultaneously proves existence, ordering (the Gate 3 event must
    appear *before* the unseal event), and cryptographic linkage into the
    already-verified chain — the same mechanism `_references_prior_prerequisites`
    uses for the four §7 selection prerequisites.
    """
    ref = payload.get("gate3_accepted_sha")
    if not _is_sha256_hex(ref):
        return False
    prerequisite = prior_entries_by_sha.get(ref)
    if prerequisite is None:
        return False
    prerequisite_payload = prerequisite.payload
    if not isinstance(prerequisite_payload, Mapping):
        return False
    return _valid_gate3_accepted_payload(prerequisite_payload)


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


def _verified_holdout_unseal_detail(
    ledger_entries: Sequence[LedgerEntry],
) -> tuple[int | None, bool]:
    """Return `(verified_seq, gate3_candidate_unverified)`, fail-closed.

    A valid unseal is in a fully verified ledger chain and has the strict order:
    four canonical prerequisite events -> `selection_frozen` -> `holdout_unseal`,
    with the `holdout_unseal` event additionally referencing a prior chain-valid
    `gate3_accepted` event via `gate3_accepted_sha` (round 22 ADOPT,
    `UNDERSPEC-CAL-D50`; see `_references_prior_gate3_acceptance`). Both
    selection and unseal must carry the same four entry-SHA references, and
    `holdout_unseal` must reference the prior selection event by entry SHA. A
    caller-supplied integer or four merely well-formed 64-hex strings can never
    create an unseal boundary.

    `gate3_candidate_unverified` is `True` when a `holdout_unseal` event was
    found whose selection-chain linkage (four prerequisites + commitment match
    against a valid `selection_frozen`) was otherwise valid but whose Gate 3
    reference failed verification — i.e. the sole reason no unseal boundary was
    returned is the Gate 3 check. Callers use this to attach a distinct
    fail-closed reason (`UNSEAL_GATE3_UNVERIFIED`) instead of the generic
    `BLOCKED_LEAKAGE`.
    """
    prev_sha = GENESIS_PREV_SHA
    for expected_seq, entry in enumerate(ledger_entries):
        if entry.seq != expected_seq or entry.prev_sha != prev_sha:
            return None, False
        expected_sha = _entry_sha(entry.seq, entry.prev_sha, entry.payload)
        if entry.entry_sha != expected_sha:
            return None, False
        prev_sha = entry.entry_sha

    prior_entries_by_sha: dict[str, LedgerEntry] = {}
    frozen_by_sha: dict[str, Mapping[str, Any]] = {}
    gate3_candidate_unverified = False
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
                    gate3_valid = _references_prior_gate3_acceptance(
                        payload, prior_entries_by_sha
                    )
                    # `[UNDERSPEC-CAL-D88]`(a): a gate3-valid candidate must
                    # also pass the freeze/gate3/unseal ordering re-check
                    # (see `_c0_freeze_ordering_violation()` docstring —
                    # only engaged when this looks like a real, frozen-
                    # campaign ledger). `gate3_valid` already proved
                    # `gate3_accepted_sha` resolves to a well-formed prior
                    # `gate3_accepted` payload, so it is safe to look it up
                    # again here without re-validating its shape.
                    ordering_ok = True
                    if gate3_valid:
                        gate3_entry = prior_entries_by_sha[payload["gate3_accepted_sha"]]
                        gate3_payload = gate3_entry.payload
                        assert isinstance(gate3_payload, Mapping)
                        ordering_ok = not _c0_freeze_ordering_violation(
                            ledger_entries, payload, gate3_payload
                        )
                    if gate3_valid and ordering_ok:
                        return entry.seq, False
                    gate3_candidate_unverified = True
        prior_entries_by_sha[entry.entry_sha] = entry
    return None, gate3_candidate_unverified


def _verified_holdout_unseal_seq(ledger_entries: Sequence[LedgerEntry]) -> int | None:
    """Return the first valid holdout-unseal boundary, or None fail-closed.

    Thin wrapper over `_verified_holdout_unseal_detail()` that drops the Gate 3
    diagnostic flag; kept as a stable, narrowly-scoped entry point for callers
    (and tests) that only need the verified sequence number.
    """
    verified_seq, _gate3_candidate_unverified = _verified_holdout_unseal_detail(ledger_entries)
    return verified_seq


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


def _parse_ledger_lines(
    raw_lines: Sequence[str],
) -> tuple[list[LedgerEntry], list[MalformedLedgerLine]]:
    """`raw_lines`（`_split_complete_lines` が返す、改行区切りの完全な行の列）
    から `LedgerEntry`/`MalformedLedgerLine` を構築する純関数（I/O なし）。
    `Ledger._read_all()`（キャッシュ構築時の唯一の読取）と
    `Ledger.load_with_verification()`（campaign/state.py finding #12:
    1 回の読取から entries 構築 + chain 検証の両方を行う）が共有する。
    挙動は元々 `Ledger._read_all()` 内にあったループそのものであり、抽出に
    伴う変更は無い。"""
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


def _read_and_verify(
    path: Path,
) -> tuple[
    list[LedgerEntry],
    list[MalformedLedgerLine],
    ChainVerification,
    int,
    list[str],
    tuple[int, int] | None,
    tuple[int, int, int] | None,
]:
    """`path` を一度だけ読み、entries 構築 (`_parse_ledger_lines`)・chain 検証
    (`_verify_chain_prefix`)・検証済みバイト長の取得を同一バッファから行う
    純粋 I/O ヘルパー（UNDERSPEC-CAL-D84）。`Ledger.__init__` と
    `Ledger.load_with_verification` の両方が使う — finding #12 が
    `load_with_verification` 用に導入した「1 回読取」パターンを構築経路
    全体へ拡張したもので、ここで得る `ChainVerification`/バイト長が
    `Ledger.append()` の増分検証で使う「検証済み watermark」の元になる。
    ファイルが存在しない場合は空とみなす。戻り値の `raw_lines` は
    `_set_watermark()` が watermark 末尾エントリの生バイト列 fingerprint
    （`#345 指摘③`: rollback/tamper 検出用）を導出するために使う。同じく
    戻り値の `last_line_byte_range`（`#345 指摘③` 追補）は、その fingerprint
    バイト列を次回 `append()` が O(1) で再読取する際の実際のファイル内
    バイト範囲（末尾空行の有無に依存しない）を渡すために使う。

    戻り値の `stat_info`（`(st_ino, st_mtime_ns, st_size)`。ファイルが存在
    しなければ `None`）は `#345` 指摘③ G1（`Ledger.append()` の安価な変更
    検出器）が使う「検証済み時点の stat」の起点。この読取と同じファイル
    ディスクリプタから `os.fstat()` するため、content と stat の間に
    別々の `exists()`/`stat()` 呼び出しが持つ TOCTOU の隙間がない。"""
    stat_info: tuple[int, int, int] | None = None
    if path.exists():
        with path.open("rb") as f:
            st = os.fstat(f.fileno())
            raw_bytes = f.read()
        content = raw_bytes.decode("utf-8")
        stat_info = (st.st_ino, st.st_mtime_ns, st.st_size)
    else:
        content = ""
    raw_lines, truncated_tail, missing_final_newline = _split_complete_lines(content)
    entries, malformed = _parse_ledger_lines(raw_lines)
    chain = _verify_chain_prefix(raw_lines, truncated_tail, missing_final_newline)
    last_line_byte_range = _last_nonblank_line_byte_range(content)
    return (
        entries,
        malformed,
        chain,
        len(content.encode("utf-8")),
        raw_lines,
        last_line_byte_range,
        stat_info,
    )


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
        entries, malformed, chain, content_bytes, raw_lines, last_line_byte_range, stat_info = (
            _read_and_verify(path)
        )
        self._entries = entries
        self._malformed = malformed
        self._set_watermark(
            chain, entries, content_bytes, raw_lines, last_line_byte_range, stat_info
        )

    def _set_watermark(
        self,
        chain: ChainVerification,
        entries: Sequence[LedgerEntry],
        content_bytes: int,
        raw_lines: Sequence[str],
        last_line_byte_range: tuple[int, int] | None = None,
        stat_info: tuple[int, int, int] | None = None,
    ) -> None:
        """`append()` の増分検証（UNDERSPEC-CAL-D84）が使う「検証済み
        watermark」を設定する。`__init__`/`load_with_verification`（構築時）
        と `append()`（追記成功後）の両方から呼ばれる。

        `chain.ok` が `True` の場合のみ `content_bytes`（検証済みバイト長）と
        末尾エントリの `seq`/`entry_sha` を watermark として信用する。
        `chain.ok` が `False`（改竄・truncated tail・malformed 行のいずれか）
        の場合は「未検証」を意味する sentinel `(0, -1, GENESIS_PREV_SHA,
        False)` を設定する — この場合、次回 `append()` は watermark 0 から
        suffix（＝ファイル全体）を検証する旧来の full-verify に自動的に
        フォールバックし、fail-closed 契約を一切弱めない
        （`test_ledger_append_refuses_when_middle_entry_tampered` 等が
        これを検証する）。

        `_v_last_line_len`/`_v_last_line_sha256`（`#345 指摘③` 採用）:
        watermark 末尾エントリ 1 行分の生バイト長と sha256。`append()` は
        次回呼び出し時、これを使って watermark 直下の 1 行だけを O(1) で
        再読取・再検証する（`raw_lines` 全体の再検証ではない）。これにより
        「ファイルサイズは watermark と同じだが末尾エントリの内容だけが
        同じ長さの別内容に差し替えられている」改竄（O(1) suffix 検証では
        素通りする）も fail-closed で検出する。

        `_v_last_line_start`/`_v_last_line_end`（`#345 指摘③` 追補、末尾空行
        での偽 append 失敗の修正）: 上記 fingerprint バイト列の、ファイル内
        での実際の開始/終了バイトオフセット。`last_line_byte_range`
        （`_last_nonblank_line_byte_range()` が `content_bytes` とは独立に
        1 パスで求めた、最後の**非空**完全行そのものの範囲）をそのまま格納
        する。旧実装は `last_end` を `content_bytes`（watermark のファイル
        全体バイト長）から `- 1` 等で逆算していたため、有効な chain の末尾に
        空行が 1 行以上続く場合（`content_bytes` が空行の分だけ実際の終端
        より後ろにずれる）、`append()` の O(1) fingerprint 再照合が誤った
        バイト範囲を読み、正当な台帳を `LedgerChainInvalidError` で拒否して
        いた（`#345` 指摘: append の偽失敗でリカバリを阻害する欠陥）。本
        オフセットは末尾空行の有無や `missing_final_newline` に関わらず常に
        最終非空行そのものを指すため、この逆算が不要になる。

        `_v_ino`/`_v_mtime_ns`/`_v_stat_size`（`#345` 指摘③ G1 採用）: この
        watermark を確立した読取直後（`stat_info`。構築時は `_read_and_verify`
        が同一 fd から `os.fstat` した値、`append()` 成功時は自身の write→
        flush→`fsync` 直後に再 `fstat` した値）の `(st_ino, st_mtime_ns,
        st_size)`。`chain.ok=False`（watermark 未確立）の場合は `None` —
        この場合 `append()` は `v_bytes=0` 経由で常に全体検証へ落ちるため
        G1 の出番がない。mtime 粒度の注意: Linux では `st_mtime_ns` はナノ秒
        単位で記録され、実務上は同一秒内の書き込みでも異なる値になるが、
        OS/ファイルシステム/`utimensat` による意図的な時刻偽装までは
        保証しない（モジュール docstring の保護水準宣言のとおり、台帳の外側
        で動く敵対的な実行者は対象外）。"""
        if chain.ok:
            self._v_bytes = content_bytes
            self._v_ino, self._v_mtime_ns, self._v_stat_size = (
                stat_info if stat_info is not None else (None, None, None)
            )
            if entries:
                tail = entries[-1]
                self._v_seq = tail.seq
                self._v_sha = tail.entry_sha
                last_line_bytes = raw_lines[-1].encode("utf-8") if raw_lines else b""
                self._v_last_line_len = len(last_line_bytes)
                self._v_last_line_sha256 = hashlib.sha256(last_line_bytes).hexdigest()
                if last_line_byte_range is not None:
                    self._v_last_line_start, self._v_last_line_end = last_line_byte_range
                else:
                    self._v_last_line_start = self._v_last_line_end = 0
            else:
                self._v_seq = -1
                self._v_sha = GENESIS_PREV_SHA
                self._v_last_line_len = 0
                self._v_last_line_sha256 = None
                self._v_last_line_start = self._v_last_line_end = 0
            self._v_missing_final_newline = chain.missing_final_newline
        else:
            self._v_bytes = 0
            self._v_seq = -1
            self._v_sha = GENESIS_PREV_SHA
            self._v_missing_final_newline = False
            self._v_last_line_len = 0
            self._v_last_line_sha256 = None
            self._v_last_line_start = self._v_last_line_end = 0
            self._v_ino = self._v_mtime_ns = self._v_stat_size = None

    @classmethod
    def load_with_verification(cls, path: Path) -> tuple["Ledger", ChainVerification]:
        """`path` を **1 回だけ** 読み、同一バッファから entries 構築と
        chain 検証の両方を行う（finding #12: `campaign/state.py::
        load_frozen_campaign` は従来 `Ledger(path)`（1 回読取） →
        `ledger.verify_chain()`（もう 1 回読取）の 2 回読みだった）。
        `Ledger(path)`/`verify_chain()` 単体の挙動・シグネチャは変更しない
        — 本メソッドは追加の入口であり、`_read_and_verify` という既存の
        純関数を薄く呼ぶだけ。返す `chain` はここで確立した watermark の
        根拠でもある（UNDERSPEC-CAL-D84: `Ledger.append()` の増分検証）。"""
        entries, malformed, chain, content_bytes, raw_lines, last_line_byte_range, stat_info = (
            _read_and_verify(path)
        )

        instance = cls.__new__(cls)
        instance.path = path
        instance._entries = entries
        instance._malformed = malformed
        instance._set_watermark(
            chain, entries, content_bytes, raw_lines, last_line_byte_range, stat_info
        )
        return instance, chain

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    @property
    def malformed_lines(self) -> tuple[MalformedLedgerLine, ...]:
        """構築時に検出された構造的 malformed 行（Codex レビュー 2026-09-01
        P1 finding #3）。空タプルは「malformed 行なし」を意味する。権威ある
        chain 検証結果は `verify_chain()` を使うこと（本 property は
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

        **増分 chain 検証**（UNDERSPEC-CAL-D84。旧: Codex レビュー
        2026-09-01 P1 finding #4 の「全 chain 再検証」）: 旧実装は、排他
        ロックを保持したまま既存ファイルの **全行** を毎回 `verify_chain()`
        と同じ判定 (`_verify_chain_prefix`) で再検証し、さらに `_read_all()`
        で全行を再パースしていた（append 1 回あたり O(n)、n=既存エントリ数。
        実測: 本番 campaign RUN10-CAL-20260903-9bcbbf86 の c2-baseline で
        n=11,220 のとき 1 回の append が 912 ms、group あたり 6 回で 5.47 s
        ＝完走に約 112 h CPU / 約 2,700 slices を要する O(n²) が判明した）。

        本実装は、この `Ledger` インスタンスが最後に検証した地点
        （`self._v_bytes`/`_v_seq`/`_v_sha`/`_v_missing_final_newline` —
        `__init__`/`load_with_verification`（構築時）または直前の
        `append()` 成功時に設定される「検証済み watermark」）**以降**の
        suffix だけを `_verify_chain_prefix` で検証する（O(1) — suffix は
        通常 0〜数行）。`fcntl.flock(LOCK_EX)` を先に獲得し、そのロックを
        保持したまま on-disk の現在のファイルを watermark から読み直すため、
        以下は旧実装と同じ強度で検出される（fail-closed を弱めない）:

        - watermark **以降**に他プロセス/他 `Ledger` インスタンスが書いた
          内容の改竄・truncated tail・malformed 行（suffix を通常どおり
          `_verify_chain_prefix` で検証するため）
        - watermark 末尾エントリ 1 行そのものが（truncate を伴わず）同じ
          長さの別内容へ差し替えられている場合（`_v_last_line_sha256` との
          O(1) 再照合で検出。`#345 指摘③` 採用）

        **`#345` 指摘③（fail-closed 化。旧: watermark 未満へのフォール
        バック）**: ファイルが watermark のバイト位置より短くなっている
        場合（restore/sync rollback 等で VALID だが短い prefix へ戻された
        ケースを含む）、旧実装は watermark を `0` にリセットしてファイル
        全体を再検証するフォールバックへ落ちていた。この再検証自体は
        通っても、`append()` 末尾で `self._entries` に suffix（＝ロール
        バック後のファイル全体）を単純 `extend` していたため、ロール
        バック前の古いキャッシュ（例: 3 エントリ）の後ろへロールバック後の
        内容（例: 2 エントリ）が継ぎ足され、`self._entries` の `seq` 列が
        `[0,1,2,0,1]` のように disk（`[0,1]`）と乖離する
        （in-memory キャッシュの再同期）。本実装はこの再同期を行わず、
        watermark 未満へのサイズ減少を **無条件に** rollback/tamper とみなし
        `LedgerChainInvalidError` で fail-closed する（書き込みなし・
        `self._entries` 等の in-memory 状態も不変）。呼び出し側は
        `Ledger(path)`/`load_with_verification(path)` で明示的に作り直して
        から再試行すること。

        **`#345` 指摘③ G1（安価な変更検出器）**: 上記の watermark 直下 1 行
        fingerprint 再照合は、watermark 末尾エントリ「より前」（＝この
        インスタンスが最後に検証した prefix のうち末尾エントリを除いた
        部分）の in-place 同一長改ざんは検出しない — ファイルサイズも
        watermark 末尾行の内容も変わらないため。これを塞ぐため、
        watermark 確立時（構築時読取直後、または直前の自分自身の write→
        flush→`fsync` 直後）に `(st_ino, st_mtime_ns, st_size)` も併せて
        記録する（`self._v_ino`/`_v_mtime_ns`/`_v_stat_size`）。今回の
        `append()` は、まず現在の on-disk stat を `os.fstat()` で取得し、
        これが記録済みの値と（inode・mtime・size のいずれか 1 つでも）
        食い違っていれば——このインスタンス自身の直近の write で説明が
        つかない変化とみなし——O(1) の高速経路（last-line fingerprint 再照合
        + suffix のみの `_verify_chain_prefix`）をスキップし、seq 0 から
        on-disk ledger 全体を `_verify_chain_prefix` でフル再検証してから
        書き込む。フル再検証が失敗すれば `LedgerChainInvalidError` で
        fail-closed（書き込みなし）。成功すれば（例: 単一 writer 境界の契約
        が許す「flock で直列化された複数 `Ledger` インスタンスの正当な交互
        append」）、watermark と `self._entries` をこのフル読取の内容へ
        再同期してから続行する（ロールバック時（`#345 指摘③` 既存の
        「watermark 未満へのサイズ減少」チェックが依然として先に fail-closed
        するため、ここでの再同期は「同じかそれ以上のサイズで、かつ chain
        全体が正当」な場合のみに限られる）。mtime 粒度の注意: Linux では
        `st_mtime_ns` はナノ秒粒度で記録され、実務上は同一秒内の書き込み
        でも異なる値になるが、`utimensat` 等による意図的な mtime 偽装や
        inode 温存トリックまでは検出しない。

        **`#345` 指摘③ G2（遷移 event 前のフル検証）**: `payload["kind"]` が
        `_TRANSITION_EVENT_KINDS`（フェーズ到達を示す terminal event。
        `campaign/state.py::LEDGER_KIND_FOR_PHASE` を権威とし、そこに現れ
        ない `gate3_accepted`/`holdout_render_valid`/`split_secret_revealed`
        と、`stage_summary`/`slice_summary` の 2 summary も加える）に含まれる
        場合、stat が変化していなくても無条件に G1 と同じフル再検証経路へ
        入る（この 1 か所の分岐だけで済むよう、呼び出し側の変更は不要）。
        頻度は 1 campaign あたり高々数十〜数百件（フェーズ遷移 + summary）
        であり、O(n) 再検証を要求しても campaign 全体としては許容可能な
        コストにとどまる（D84 が問題視した「毎 append で O(n)」とは異なる）。

        **残余露出（G1/G2 適用後も残る唯一の隙間）**: watermark 末尾より前の
        in-place 同一長改ざんが、かつ (a) この操作自身の直近 write と
        「区別できない」ほど stat（ino/mtime_ns/size）まで精巧に偽装され、
        かつ (b) 次に追記される payload の `kind` が `_TRANSITION_EVENT_KINDS`
        のいずれでもない（＝ただの `meter_call` 等）場合に限り、その 1 回の
        `append()` では検出されない。この露出は次のいずれかで必ず検出される:
        次の遷移/summary event の append（G2）、プロセス再起動後の
        `Ledger(path)`/`load_with_verification(path)`（構築時は常にフル
        検証）、または明示的な `verify_chain()` 呼び出し。モジュール
        docstring が宣言する保護水準（事故的 leakage / 事後改竄の検出、
        台帳の外側で動く敵対的な実行者は対象外）を弱めるものではない。

        **R14 fix（Codex PR #346 round 14 採用, "Coordinate appenders before
        unlinking the locked inode"）**: ロック対象を `self.path`（ledger
        自身）の fd から、同ディレクトリの安定した専用ロックファイル
        （`<ledger名>.lock`）へ変更した。旧実装は `self.path` を先に
        `open()` してからその fd に `flock` していたため、
        `archive_aborted_ledger.py` の archiver が同じ `self.path` の fd に
        `flock` を保持したまま最終的に `unlink()` する運用と衝突していた:
        archiver がロック保持中に appender が `self.path` を open すると
        （unlink 前なので同じ inode）appender は旧 inode の fd で flock 待ち
        に入り、archiver の `unlink()` でパス名が消えた**後**にこの
        flock を獲得してしまう——その後の書き込みは gz snapshot にも
        ファイルシステム上のどのパスにも存在しない、切り離された inode へ
        行われ、entry が恒久的に失われる。本 fix は `Ledger.append()` と
        archiver の両方に、**ledger 本体を open する前に** この専用ロック
        ファイルを先に取得させることで、この区間の競合そのものを構造的に
        閉じる（archiver は本ロックを保持している間 `self.path` を
        unlink できないため、appender が「unlink 済みの inode で flock を
        獲得する」経路が存在しなくなる）。加えて、ロック獲得後・書込み前に
        `os.fstat(fd).st_ino` と `os.stat(self.path).st_ino`（存在しなければ
        `None`）を照合する defense-in-depth の inode 再検証を行い、万一
        このロック規約に従わない経路で `self.path` が unlink/置換されていた
        場合も `LedgerChainInvalidError` で fail-closed する。
        """
        payload_j = _jsonable(payload)
        transition_kind = payload_j.get("kind") if isinstance(payload_j, Mapping) else None
        force_full_chain_verify = transition_kind in _TRANSITION_EVENT_KINDS
        self.path.parent.mkdir(parents=True, exist_ok=True)
        full_resync = False
        full_entries_for_resync: list[LedgerEntry] = []
        # R14 fix (Codex PR #346 round 14, "Coordinate appenders before
        # unlinking the locked inode"): the lock target is a stable dedicated
        # lock file next to the ledger, never `self.path` itself. The prior
        # design locked `self.path`'s own fd, but `archive_aborted_ledger.py`
        # unlinks `self.path` while holding that same flock; an appender that
        # opened `self.path` (same inode) just before the unlink would still
        # win the flock *after* the unlink removed the path, then write onto
        # the now-detached inode — an entry that is neither in the gz
        # snapshot nor reachable from any path, permanently lost. Both
        # `Ledger.append()` and the archiver now acquire this stable file's
        # lock BEFORE opening the ledger file at all, so the archiver's
        # unlink can never happen between an appender's open() and its own
        # flock acquisition.
        lock_path = self.path.parent / (self.path.name + ".lock")
        with open(lock_path, "a+", encoding="utf-8") as lock_f:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
            try:
                # R19 fix (Codex PR #346 round 19 採用, "Refuse to recreate a
                # ledger after archival"): once `ensure_archived()`
                # (`tools/archive_aborted_ledger.py`) has published a
                # verified `<name>.gz` + `<name>.sha256` sidecar pair for
                # this campaign and unlinked the original `self.path`, that
                # gz/sidecar pair is the sole canonical record. The plain
                # `self.path.open("a+b")` below does not distinguish "this
                # path was never created" (a legitimate C0-freeze genesis
                # ledger) from "this path was archived away" — both look
                # like "file absent" — so it would silently `open()` a brand
                # new, empty chain and let this append() create a fresh
                # `seq=0` genesis ledger. That append reports success, but
                # the event it wrote is absent from the already-published
                # gz snapshot; the next `ensure_archived()` call then finds
                # a `ledger.jsonl` whose content is not a strict byte-prefix
                # extension of the published gz and rejects it as a
                # non-prefix divergence — two irreconcilable provenance
                # artifacts for the same campaign, exactly the corruption
                # this ledger exists to make impossible. Checked here, under
                # the same stable lock file that `ensure_archived()` takes
                # before it reads/unlinks `self.path` (module docstring R14
                # fix), so the "does an archive pair already exist" question
                # is answered atomically with respect to any concurrent
                # archiver: either the archiver's publish+unlink is fully
                # visible here, or it has not started yet and this append()
                # proceeds as an ordinary create against a path that has no
                # archive pair (freeing this branch to fire only when the
                # archive truly is authoritative). A pure fresh-campaign
                # initialization (no archive pair present) is unaffected and
                # still creates a new genesis ledger as before.
                # Existence is probed via `os.stat()` + `except
                # FileNotFoundError` (the same idiom the defense-in-depth
                # inode re-check below uses) rather than `Path.exists()`,
                # which swallows only errno-tagged `OSError`s — a
                # `FileNotFoundError` raised without an errno (as a test
                # double might) would otherwise propagate through
                # `Path.exists()` uncaught.
                try:
                    os.stat(self.path)
                    path_missing = False
                except FileNotFoundError:
                    path_missing = True
                if path_missing:
                    gz_path = self.path.parent / (self.path.name + ".gz")
                    sidecar_path = self.path.parent / (self.path.name + ".sha256")
                    # R25-2 fix (Codex PR #346 round 25 finding (2) 採用,
                    # "Reject appends when either archive artifact
                    # remains"): 修正前は両方 (`and`) 揃っている場合にしか
                    # このガードが発火しなかった。完了済み archive が
                    # sidecar だけを失うと（gz のみ残存）、`ledger.jsonl`
                    # 不在のまま append() が素通りして新しい genesis
                    # ledger.jsonl を作ってしまう——次の `ensure_archived()`
                    # 呼び出しは、この新規 chain 自体が内部的に chain-valid
                    # であることしか見ないため、それを canonical な原本だと
                    # 誤認し、唯一の正典だった gz を破棄しうる（campaign
                    # 履歴の永久喪失）。gz/sidecar のどちらか一方でも残って
                    # いれば、それは「archive 済みだが片方が失われた」状態
                    # であり、新規 genesis を黙って作ってよい「まっさらな
                    # 新規 campaign」とは区別できないため、`or` に変更して
                    # fail-closed する——明示的な archive 回復（欠けた側の
                    # 再生成、または `tools/archive_aborted_ledger.py` 側の
                    # 手動復旧）を先に要求する。
                    gz_present = gz_path.is_file()
                    sidecar_present = sidecar_path.is_file()
                    if gz_present or sidecar_present:
                        raise LedgerArchivedError(
                            self.path, gz_present=gz_present, sidecar_present=sidecar_present
                        )
                with self.path.open("a+b") as f:
                    # R14 fix, defense-in-depth: even under the stable lock
                    # above, re-verify that the path we just opened still
                    # resolves to the fd we hold (nothing unlinked/replaced
                    # it out from under us). This does not by itself close
                    # the race — the stable lock above already does that —
                    # but fails closed instead of silently writing onto a
                    # detached inode if some other path ever unlinks
                    # `self.path` without going through this lock.
                    fd_ino = os.fstat(f.fileno()).st_ino
                    try:
                        path_ino = os.stat(self.path).st_ino
                    except FileNotFoundError:
                        path_ino = None
                    if path_ino != fd_ino:
                        raise LedgerChainInvalidError(
                            self.path,
                            "ledger path no longer resolves to the fd this append() "
                            "just opened (unlinked or replaced while the ledger "
                            "lock file was held); refusing to append onto a "
                            "detached inode",
                            None,
                        )
                    f.seek(0, os.SEEK_END)
                    current_size = f.tell()
                    v_bytes = self._v_bytes
                    v_seq = self._v_seq
                    v_sha = self._v_sha
                    v_missing_final_newline = self._v_missing_final_newline
                    if v_bytes > 0:
                        if current_size < v_bytes:
                            # watermark より短い＝restore/sync rollback 等の
                            # 外部要因によるロールバック（VALID だが短い prefix
                            # を含む）。watermark を再同期して受理してはならない
                            # （#345 指摘③: 旧実装はここで watermark を 0 に
                            # 戻してフォールバック検証していたため、on-disk が
                            # [0,1] へ縮んでも in-memory `self._entries` は
                            # ロールバック前の内容を保持したまま suffix を
                            # extend し、`[0,1,2,0,1]` のような重複キャッシュを
                            # 生んでいた）。in-memory 状態を変更せず、書き込みも
                            # 一切行わずに fail-closed する。
                            raise LedgerChainInvalidError(
                                self.path,
                                f"on-disk ledger size ({current_size} bytes) is smaller than "
                                f"this instance's verified watermark ({v_bytes} bytes, "
                                f"seq={v_seq}): rollback or truncation detected; refusing to "
                                "re-sync onto a shortened chain. Reconstruct via Ledger(path) "
                                "or load_with_verification(path) to re-verify from scratch "
                                "before appending again.",
                                v_seq,
                            )
                        # `#345` 指摘③ G1: watermark 確立時（構築時 or 直前の
                        # 自分自身の write 直後）に記録した stat と現在の on-disk
                        # stat を比較する安価な変更検出器。一致していれば
                        # （＝このインスタンス自身の直近の write 以外、誰も
                        # ファイルへ触れていない）既存の O(1) last-line
                        # fingerprint 再照合だけで十分。`#345` 指摘③ G2: 追記
                        # しようとしている event 自体がフェーズ遷移/terminal
                        # summary（`_TRANSITION_EVENT_KINDS`）なら、stat が
                        # 一致していても無条件にフル再検証へ回す。
                        current_stat = os.fstat(f.fileno())
                        stat_unchanged = (
                            self._v_ino is not None
                            and current_stat.st_ino == self._v_ino
                            and current_stat.st_mtime_ns == self._v_mtime_ns
                            and current_stat.st_size == self._v_stat_size
                        )
                        if stat_unchanged and not force_full_chain_verify:
                            # サイズは watermark 以上でも、watermark 末尾エントリ
                            # そのものが同じ長さの別内容へ差し替えられていれば
                            # （truncate を伴わない改竄）、suffix 検証だけでは
                            # 素通りする（suffix はまさにこの 1 行の直後から読む
                            # ため）。watermark 末尾 1 行分だけを O(1) で読み直し、
                            # 記録済み sha256 と一致するか確認する（#345 指摘③）。
                            # バイト範囲は `_set_watermark()` が構築時に直接記録
                            # した最終非空行の実オフセット
                            # （`_v_last_line_start`/`_end`）をそのまま使う —
                            # `v_bytes`（ファイル全体長）からの逆算ではない。
                            # 逆算は watermark 末尾に空行が続く有効な chain で
                            # 実際の終端より後ろを指し、正当な台帳の append を
                            # 偽の tamper 検出として拒否していた（`#345` 指摘③
                            # 追補: 末尾空行での偽 append 失敗）。
                            # `[UNDERSPEC-CAL-D88]`(b): a "genesis watermark" —
                            # a validated chain of zero JSON entries (the file
                            # holds only blank lines, so `v_bytes` (total file
                            # byte length) is nonzero even though there is no
                            # JSON line to fingerprint). `_v_seq == -1` alone is
                            # not sufficient to identify this case (a tampered/
                            # unverified watermark also sentinels `_v_seq = -1`,
                            # but takes the `v_bytes == 0` branch below instead —
                            # see `_set_watermark()`'s `else` branch), so require
                            # the last-line fingerprint fields to be at their
                            # "no prior line" defaults too. There is nothing to
                            # re-read/compare against, so the O(1) fingerprint
                            # check below (which would otherwise always mismatch
                            # — `hashlib.sha256(b"").hexdigest()` is never equal
                            # to `None`) is skipped and this append proceeds as a
                            # normal first-entry append. Any other `None` sha
                            # (i.e. `_v_seq != -1`, which cannot arise from a
                            # `chain.ok=True` watermark — see `_set_watermark()`)
                            # keeps failing closed via the mismatch below.
                            is_genesis_watermark = (
                                self._v_seq == -1
                                and self._v_last_line_sha256 is None
                                and self._v_last_line_len == 0
                            )
                            if not is_genesis_watermark:
                                last_len = self._v_last_line_len
                                last_start = self._v_last_line_start
                                f.seek(last_start)
                                last_bytes = f.read(last_len)
                                if (
                                    len(last_bytes) != last_len
                                    or hashlib.sha256(last_bytes).hexdigest()
                                    != self._v_last_line_sha256
                                ):
                                    raise LedgerChainInvalidError(
                                        self.path,
                                        f"on-disk content at this instance's verified watermark "
                                        f"(seq={v_seq}) no longer matches what was last verified: "
                                        "content tamper at/behind the watermark; refusing to "
                                        "append without re-verifying the full chain.",
                                        v_seq,
                                    )
                        else:
                            # G1（stat が想定外に変化）または G2（遷移 event）:
                            # watermark より前の in-place 同一長改ざんは stat の
                            # みでは疑わしいと分かっても位置を特定できないため、
                            # on-disk ledger 全体を seq 0 からフル再検証する。
                            f.seek(0)
                            full_raw = f.read()
                            try:
                                full_content = full_raw.decode("utf-8")
                            except UnicodeDecodeError as exc:
                                raise LedgerTruncatedTailError(
                                    self.path,
                                    "existing ledger content is not valid utf-8; refusing "
                                    "to append",
                                ) from exc
                            full_lines, full_truncated_tail, full_missing_final_newline = (
                                _split_complete_lines(full_content)
                            )
                            if full_truncated_tail:
                                raise LedgerTruncatedTailError(
                                    self.path,
                                    "existing ledger tail is truncated (incomplete final "
                                    "line); refusing to append onto partial bytes",
                                )
                            full_check = _verify_chain_prefix(
                                full_lines,
                                truncated_tail=False,
                                missing_final_newline=full_missing_final_newline,
                            )
                            if not full_check.ok:
                                raise LedgerChainInvalidError(
                                    self.path, full_check.detail, full_check.tamper_at_seq
                                )
                            # フル検証済み: watermark とこのインスタンスの
                            # `self._entries` キャッシュを、このフル読取の内容へ
                            # 再同期する（旧キャッシュへの単純 extend は行わない
                            # — `#345` 指摘③ ロールバック fix と同じ理由で、
                            # 一部だけを継ぎ足すと disk と乖離した重複キャッシュ
                            # を生みうる）。
                            full_entries_for_resync, _full_malformed = _parse_ledger_lines(
                                full_lines
                            )
                            full_resync = True
                            v_bytes = len(full_raw)
                            v_missing_final_newline = full_missing_final_newline
                            if full_lines:
                                tail_raw_full = json.loads(full_lines[-1])
                                v_seq = int(tail_raw_full["seq"])
                                v_sha = str(tail_raw_full["entry_sha"])
                            else:
                                v_seq = -1
                                v_sha = GENESIS_PREV_SHA

                    f.seek(v_bytes)
                    suffix_raw = f.read()
                    try:
                        suffix_content = suffix_raw.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise LedgerTruncatedTailError(
                            self.path,
                            "existing ledger suffix is not valid utf-8; refusing to append",
                        ) from exc

                    raw_lines, truncated_tail, missing_final_newline = _split_complete_lines(
                        suffix_content
                    )
                    if truncated_tail:
                        raise LedgerTruncatedTailError(
                            self.path,
                            "existing ledger tail is truncated (incomplete final line); "
                            "refusing to append onto partial bytes",
                        )
                    chain_check = _verify_chain_prefix(
                        raw_lines,
                        truncated_tail=False,
                        missing_final_newline=missing_final_newline,
                        start_seq=v_seq + 1,
                        start_prev_sha=v_sha,
                    )
                    if not chain_check.ok:
                        raise LedgerChainInvalidError(
                            self.path, chain_check.detail, chain_check.tamper_at_seq
                        )

                    if raw_lines:
                        tail_raw = json.loads(raw_lines[-1])
                        seq = int(tail_raw["seq"]) + 1
                        prev_sha = str(tail_raw["entry_sha"])
                    elif v_seq >= 0:
                        seq = v_seq + 1
                        prev_sha = v_sha
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
                    # `suffix_content` が空、かつ watermark 自身が末尾改行未終端
                    # だった場合のみ、欠けた "\n" をまず自己修復として書く
                    # （suffix が非空なら、それを書いた側の append() が既に
                    # 同じ自己修復を行っているため不要）。
                    heal_missing_newline = v_missing_final_newline and not suffix_content
                    heal_prefix = b"\n" if heal_missing_newline else b""
                    new_line_bytes = line.encode("utf-8")
                    new_bytes = heal_prefix + new_line_bytes + b"\n"
                    f.seek(0, os.SEEK_END)
                    write_pos = f.tell()
                    f.write(new_bytes)
                    f.flush()
                    os.fsync(f.fileno())
                    # `#345` 指摘③ G1: 次回 append() の安価な変更検出器が使う
                    # baseline を、この write を fsync した直後の実 stat から
                    # 取り直す（f.tell() からの逆算ではなく、on-disk の実値）。
                    new_stat = os.fstat(f.fileno())
                    new_v_bytes = f.tell()
                    new_line_start = write_pos + len(heal_prefix)
                    new_line_end = new_line_start + len(new_line_bytes)
            finally:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)

        if full_resync:
            # G1/G2 でフル再検証した回: 旧キャッシュへの部分 extend ではなく
            # フル読取の内容で `self._entries` を丸ごと差し替える（disk との
            # 乖離を残さない）。
            self._entries = list(full_entries_for_resync)
        else:
            suffix_entries, _suffix_malformed = _parse_ledger_lines(raw_lines)
            self._entries.extend(suffix_entries)
        self._entries.append(entry)
        self._v_bytes = new_v_bytes
        self._v_seq = entry.seq
        self._v_sha = entry.entry_sha
        self._v_missing_final_newline = False
        self._v_last_line_len = len(new_line_bytes)
        self._v_last_line_sha256 = hashlib.sha256(new_line_bytes).hexdigest()
        self._v_last_line_start = new_line_start
        self._v_last_line_end = new_line_end
        self._v_ino = new_stat.st_ino
        self._v_mtime_ns = new_stat.st_mtime_ns
        self._v_stat_size = new_stat.st_size
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
        references *and* a prior chain-valid `gate3_accepted` reference
        (`gate3_accepted_sha`; round 22 ADOPT, `UNDERSPEC-CAL-D50`).  A
        `holdout_unseal` row lacking a verifiable Gate 3 reference is not an
        authorized boundary even if its selection-chain references are
        otherwise valid, and `blocked=BLOCKED_LEAKAGE` results carry the
        distinct `reason="UNSEAL_GATE3_UNVERIFIED"` in that case (see
        `LeakageCheckResult`).  `unseal_seq` is retained only as an optional
        expected-sequence assertion for compatibility; it can never grant
        access by itself.

        The protected row set is derived only after four independent checks agree:
        (1) the verification rows contain the complete canonical frozen matrix row-id
        set, (2) the realized map covers that same closed set, (3) `verify_split`
        mechanically reproduces the realized map, and (4) a valid pre-measurement
        `split_frozen` ledger event binds both `realized_sha` and SHA-256(split_secret).
        Thus neither caller-supplied rows, secret, nor a self-consistent reduced split
        can shrink the seal.  `holdout_row_ids` is only an equality assertion against
        the authenticated map.
        """
        verified_unseal_seq, gate3_candidate_unverified = _verified_holdout_unseal_detail(
            ledger_entries
        )
        if unseal_seq is not None and unseal_seq != verified_unseal_seq:
            verified_unseal_seq = None
            gate3_candidate_unverified = False

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
                # D105: `nuisance_axis` (v1.1 §V3.5) must be reconstructed the
                # same way `splitter.row_inputs_for_split()` does, and
                # compared below, or a caller-supplied row set that silently
                # differs only in `nuisance_axis` (e.g. a stale local
                # RowInput-building duplicate that never set it) passes this
                # per-row check and only fails much later, silently, inside
                # `verify_split()`'s coverage-repair re-derivation —
                # `campaign RUN10-CAL-20260905-410b25f2`'s C4
                # `BLOCKED_LEAKAGE(control_excluded_count=0)` incident.
                nuisance_axis=nuisance_axis_for_row(fixture_row),
            )

        for supplied in split_verification_rows:
            expected = canonical_split_inputs[supplied.row_id]
            if (
                supplied.family != expected.family
                or dict(supplied.stratum) != dict(expected.stratum)
                or supplied.truth_level != expected.truth_level
                or supplied.generator_impl != expected.generator_impl
                or supplied.boundary_class != expected.boundary_class
                or supplied.nuisance_axis != expected.nuisance_axis
            ):
                return LeakageCheckResult(
                    blocked=BlockedCode.BLOCKED_LEAKAGE,
                    control_excluded_count=0,
                    reason="SPLIT_VERIFICATION_ROW_MISMATCH",
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
            # D105: every row-level attribute (including `nuisance_axis`)
            # supplied here already matched the independently-reconstructed
            # canonical row above, yet re-running the split algorithm on
            # these same rows did not reproduce the frozen realized map —
            # i.e. a split re-derivation mismatch whose row inputs may
            # nonetheless differ from what was actually used at freeze time
            # in some way this function's row-attribute comparison does not
            # cover (e.g. `pinned_holdout_row_ids` drift). Distinct from the
            # `SPLIT_VERIFICATION_ROW_MISMATCH` case above, which is caught
            # before ever calling `verify_split()`.
            return LeakageCheckResult(
                blocked=BlockedCode.BLOCKED_LEAKAGE,
                control_excluded_count=0,
                reason="SPLIT_REDERIVATION_MISMATCH",
            )

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
                    reason="UNSEAL_GATE3_UNVERIFIED" if gate3_candidate_unverified else None,
                )
        return LeakageCheckResult(blocked=None, control_excluded_count=control_excluded_count)
