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
    truncated/不完全行と prev_sha 不一致（sibling 分岐・改竄）を区別する。

    **fail-closed 契約**（Codex レビュー 2026-09-01 採用）: `truncated_tail=True`
    は常に `ok=False` を伴う（末尾が中断された台帳は「検証できない」のであって
    「検証に成功した」のではない）。`truncated_tail` フィールドは、その `ok=False`
    が改竄由来 (`tamper_at_seq` 有り) なのか write 中断由来なのかを呼び出し側が
    区別できるよう残す。

    `missing_final_newline`（Codex レビュー 2026-09-01 P1 追加）: 最終行が
    改行未終端だが JSON としては完全にパース可能だった場合に `True`。この
    ケースは **fail-closed の対象ではない**（`ok` は content が正当なら
    `True` のまま）: 1 エントリも失われておらず、欠けているのは区切りの
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
            f"Ledger.append refused: existing chain integrity check failed: {detail} "
            f"(path={path})"
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
        super().__init__(
            f"Ledger.append refused: {detail} (path={path})"
        )


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
        # 最後の非空行が truncated 行そのもの: JSON parse を試し、失敗すれば
        # それを truncated 行として除外する。parse に成功する（＝改行だけが
        # 欠けた完全な行だった）場合は truncated 扱いを取り消し、代わりに
        # missing_final_newline を立てる。
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

    # fail-closed（Codex レビュー 2026-09-01 採用）: 末尾が truncated な台帳は
    # 「(改竄ではないが) 検証未完了」であり、正当な chain として ok=True を
    # 返してはならない。有効な prefix が検証済みであることは
    # `entries_verified` / `truncated_tail` で呼び出し側に伝える。
    # `missing_final_newline` は fail-closed の対象外（Codex レビュー
    # 2026-09-01 P1）: 1 件も失われていない正当な chain であり、`ok` は
    # 通常どおり `not truncated_tail` に従う。
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
    複数 `Ledger` インスタンスの交互 append で兄弟 `seq` が発生しうるため
    （Codex レビュー 2026-09-01 採用）。
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
        # "a+" は存在しなければファイルを作成し、読み書き両方を許す。POSIX 上
        # 'a' モードは O_APPEND を伴うため、write() は（read でファイル位置が
        # どこへ動いていても）常に真の EOF へ書き込まれる。read → write の間に
        # 明示的な seek を挟むのは、C stdio の「読み書きモードでは read と
        # write の間に fseek/fflush が必要」という契約を満たすため。
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
                # 全 chain 検証（Codex レビュー 2026-09-01 P1 finding #4）: 従来
                # ここでは末尾行の seq/entry_sha だけを読んで次エントリを
                # 導出しており、途中の行が改竄されていても検出せずに追記して
                # しまっていた（改竄行より後ろが改竄後に再計算された "整合
                # する" prev_sha 連鎖で偽装されていれば、末尾のみの検査では
                # 気付けない）。ロックを保持したまま `verify_chain()` と同じ
                # 判定ロジックを既読の `raw_lines` に対して再実行し、prefix
                # 全体が正当な場合のみ以降の追記処理へ進む。
                chain_check = _verify_chain_prefix(
                    raw_lines, truncated_tail=False, missing_final_newline=missing_final_newline
                )
                if not chain_check.ok:
                    raise LedgerChainInvalidError(
                        self.path, chain_check.detail, chain_check.tamper_at_seq
                    )
                if missing_final_newline:
                    # 自己修復（Codex レビュー 2026-09-01 P1 採用）: 最終行が
                    # 改行未終端でも JSON としては完全にパースできる（＝1件も
                    # 失われていない）場合は、単に区切り改行が欠けているだけ
                    # なので拒否せず、まず欠けた "\n" を書いてから通常どおり
                    # 追記する。これを怠ると次に書く行と `}{` のように直接
                    # 連結して破損する。既存の正当な chain を拒否するより
                    # 安全な選択（有効な chain を fail-closed で reject しない）。
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
                            "existing ledger tail line is unparseable/malformed; "
                            "refusing to append",
                        ) from exc
                else:
                    seq = 0
                    prev_sha = GENESIS_PREV_SHA

                entry_sha = _entry_sha(seq, prev_sha, payload_j)
                entry = LedgerEntry(
                    seq=seq, prev_sha=prev_sha, entry_sha=entry_sha, payload=payload_j
                )
                line = canonical_json(
                    {"seq": entry.seq, "prev_sha": entry.prev_sha, "entry_sha": entry.entry_sha,
                     "payload": entry.payload}
                )
                f.seek(0, os.SEEK_END)
                f.write(line)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        # in-memory cache は on-disk の真の状態から再構築する（他インスタンスの
        # append をキャッシュへ反映するため。プロセス内キャッシュを信頼しない）。
        self._entries = list(self._read_all())
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
