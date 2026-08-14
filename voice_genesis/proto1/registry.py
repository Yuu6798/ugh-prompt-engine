"""registry.py — P4 (VG-010): Genome registry + lineage。

JSONL append-only ストア + sidecar 様式 `genome-registry/0.1`。1 行 = 1 エントリ:

  genome_id（内容 sha256 の先頭 12 桁）/ version / created_at(UTC ISO8601) /
  parents: [] / op(sample|mutate|crossover) / seed / renderer_version("R0.1") /
  feature_set_version / eval: {plausibility, grip_ref, novelty} /
  audit（reference_set_hash, linkability_report_id, residual_gate_passed） /
  genome 本体。

フィールド意味論の補充判断は underspec_log_p1.md [UNDERSPEC-P1-7]・
[UNDERSPEC-P1-8] を参照。

`genome_id` の content hash は genome の正規形 JSON（`genome.to_dict`）を
`hashing.sha256_of_canonical_json` した先頭 12 桁。physio_range / audit を
含む全フィールドが対象（=同一パラメータでも audit 情報が異なれば別 ID になる。
これは意図的: 監査結果が紐付いた genome の同一性は audit を含めて識別すべき
という設計判断）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from genome import GenomeValidationError, VoiceGenome, from_dict, to_dict
from hashing import sha256_of_canonical_json

REGISTRY_SCHEMA = "genome-registry/0.1"
RENDERER_VERSION = "R0.1"
FEATURE_SET_VERSION = "measure_v3"

VALID_OPS = ("sample", "mutate", "crossover")

# PR#261 レビュー R24: op ごとに parents の個数は一意に決まる不変条件
# （sample は起点=親なし、mutate は単一親からの摂動、crossover は 2 親の
# フィールド単位継承。sampler.py の各関数の実装そのものがこの契約）。
# op="mutate" なのに parents=[] や parents 2 個、op="crossover" なのに
# parents 1 個、といった不整合は「由来の主張」と「実際の系譜構造」が
# 食い違う破損/改ざん状態であり、`lineage()` の遡上や来歴解釈を誤らせる。
EXPECTED_PARENT_COUNT = {"sample": 0, "mutate": 1, "crossover": 2}


class RegistryError(ValueError):
    pass


class DuplicateGenomeError(RegistryError):
    """append 時に既存 genome_id と衝突した（PR#261 レビュー C3）。"""


class MissingParentError(RegistryError):
    """parents が registry に存在しない genome_id を指している（PR#261 レビュー R10）。"""


def genome_content_hash(genome: VoiceGenome) -> str:
    """内容 sha256 の先頭 12 桁（genome_id）。"""
    return sha256_of_canonical_json(to_dict(genome))[:12]


@dataclass
class RegistryEntry:
    genome_id: str
    version: str
    created_at: str
    parents: List[str]
    op: str
    seed: Optional[int]
    renderer_version: str
    feature_set_version: str
    # P1 では {plausibility, grip_ref, novelty} は None プレースホルダのみ
    # だったが（[UNDERSPEC-P1-8]）、F1 (final_assembly_memo) の E2E デモは
    # 実測の構造化オブジェクト（例: grip_ref = {"ref": "...", "gate_semantics_version": "..."})
    # を渡すため Any に緩めてある。float 単体を渡す旧来の使い方も引き続き有効。
    eval: Dict[str, Any]
    audit: Dict[str, Any]
    genome: Dict[str, Any]
    registry_schema: str = REGISTRY_SCHEMA

    def to_json_line(self) -> str:
        return json.dumps(_entry_to_dict(self), sort_keys=True, ensure_ascii=True)


def _entry_to_dict(entry: RegistryEntry) -> Dict[str, Any]:
    return {
        "registry_schema": entry.registry_schema,
        "genome_id": entry.genome_id,
        "version": entry.version,
        "created_at": entry.created_at,
        "parents": entry.parents,
        "op": entry.op,
        "seed": entry.seed,
        "renderer_version": entry.renderer_version,
        "feature_set_version": entry.feature_set_version,
        "eval": entry.eval,
        "audit": entry.audit,
        "genome": entry.genome,
    }


def entry_to_dict(entry: RegistryEntry) -> Dict[str, Any]:
    """`_entry_to_dict` の公開エイリアス（proto1_demo.py 等、外部モジュールから
    レジストリエントリを JSON 化するための公開 API）。"""
    return _entry_to_dict(entry)


def _entry_from_dict(data: Dict[str, Any]) -> RegistryEntry:
    # sidecar 様式のバージョン（PR#261 レビュー C5/C6 の同型穴掃討: genome.py の
    # schema_version 検証と同じ理由で、registry_schema も宣言値を無条件に信頼
    # せず、未知/不一致なら現行レイアウトでの誤解釈を拒否する）。キー自体が
    # 欠落している場合も現行版へのデフォルト補完は行わず明示的に拒否する
    # （PR#261 レビュー R12: `.get(..., REGISTRY_SCHEMA)` は「キー欠落」を
    # 「現行版を宣言」として fail-open してしまっていた同型の穴）。
    if "registry_schema" not in data:
        raise RegistryError("registry_schema キーが存在しない（欠落）")
    registry_schema = data["registry_schema"]
    if registry_schema != REGISTRY_SCHEMA:
        raise RegistryError(
            f"registry_schema は {REGISTRY_SCHEMA!r} でなければならない（実際: {registry_schema!r}）"
        )

    # PR#261 レビュー R3(a): 埋め込み genome を genome.from_dict() で検証する
    # （schema_version 不一致・physio_range 改ざんの検査が read 時にも効くようにする）。
    raw_genome = data["genome"]
    try:
        parsed_genome = from_dict(raw_genome)
    except GenomeValidationError as exc:
        raise RegistryError(f"エントリの genome が不正: {exc}") from exc

    # PR#261 レビュー R14: エントリ直下の version（`RegistryEntry.version`。
    # モジュール docstring の「version / created_at(...) / ...」参照）が
    # 埋め込み genome の schema_version と一致するか検証する。R3(c) の
    # audit と同様、2 箇所に同じ情報（genome のスキーマ版）を持つ設計である
    # 以上、食い違いを許すと「エントリはスキーマ版 A のつもりだが中身は
    # 版 B」という改ざん・破損状態を検証なしで通してしまう。
    entry_version = data["version"]
    if entry_version != parsed_genome.schema_version:
        raise RegistryError(
            "エントリの version が埋め込み genome.schema_version と不一致（エントリ側: "
            f"{entry_version!r} / genome 側: {parsed_genome.schema_version!r}）"
        )

    # R3(b): genome_id が埋め込み genome の content hash と一致するか再検証する
    # （genome_id はエントリの一次キーであり、genome 本体との不整合は
    # 「別物を指すエントリ」を許してしまう改ざん経路になる）。
    genome_id = data["genome_id"]
    recomputed_id = genome_content_hash(parsed_genome)
    if recomputed_id != genome_id:
        raise RegistryError(
            "genome_id が埋め込み genome の content hash と不一致（宣言値: "
            f"{genome_id!r} / 再計算値: {recomputed_id!r}）"
        )

    # R3(c): エントリ直下の audit（append() が genome.audit を写した重複表現。
    # モジュール docstring 冒頭のフィールド一覧を参照）が genome 側の audit
    # セクションと一致するか検証する。2 箇所に同じ情報を持つ設計である以上、
    # 食い違いを許すと「監査結果の異なる 2 通りの主張」が同一エントリに同居する。
    entry_audit = dict(data.get("audit", {}))
    expected_audit = {
        "reference_set_hash": parsed_genome.audit.reference_set_hash,
        "linkability_report_id": parsed_genome.audit.linkability_report_id,
        "residual_gate_passed": parsed_genome.audit.residual_gate_passed,
    }
    if entry_audit != expected_audit:
        raise RegistryError(
            f"エントリの audit が genome.audit と不一致（エントリ側: {entry_audit!r} / "
            f"genome 側: {expected_audit!r}）"
        )

    # PR#261 レビュー R22: op フィールドは `append()`（書き込み時）では
    # `VALID_OPS` で検証されるが、`_entry_from_dict()`（読み込み時）は
    # 宣言値をそのまま信頼していた。手編集・インポートされた JSONL 行が
    # `op="delete"` のような未定義値を持っていても、書き込み経路を経由
    # しなければ検証されずに通ってしまう非対称性があった（append 側の
    # 制約が読み込み側で骨抜きにされる）。
    op = data["op"]
    if op not in VALID_OPS:
        raise RegistryError(f"op は {VALID_OPS} のいずれかでなければならない（実際: {op!r}）")

    # PR#261 レビュー R24: op 別の parent 数不変条件（EXPECTED_PARENT_COUNT
    # コメント参照）を読み込み時にも検証する。`append()` は書き込み時に
    # 検証するが、手編集・インポートされた JSONL 行はその経路を通らないため、
    # 同型の非対称性（R22 の op 検証と同じ構図）を防ぐ。
    parent_ids = list(data.get("parents", []))
    expected_count = EXPECTED_PARENT_COUNT[op]
    if len(parent_ids) != expected_count:
        raise RegistryError(
            f"op={op!r} は parents を {expected_count} 個持たなければならない"
            f"（実際: {len(parent_ids)} 個 {parent_ids!r}）"
        )

    return RegistryEntry(
        genome_id=genome_id,
        version=entry_version,
        created_at=data["created_at"],
        parents=parent_ids,
        op=op,
        seed=data.get("seed"),
        # renderer_version/feature_set_version は PR#261 レビュー R12 の grep 掃討で
        # 同じ `.get(..., 定数)` パターンとして見つかったが、schema_version/
        # registry_schema と違い「これに従ってドキュメント全体の構造を解釈する」
        # 契約バージョンではなく、単なる由来メタデータ（どのレンダラ/特徴量セット
        # で作られたか）。欠落時に現行値で埋めても構造誤読は起きないため、
        # デフォルト補完のまま維持する（境界宣言）。
        renderer_version=data.get("renderer_version", RENDERER_VERSION),
        feature_set_version=data.get("feature_set_version", FEATURE_SET_VERSION),
        eval=dict(data.get("eval", {})),
        audit=entry_audit,
        genome=raw_genome,
        registry_schema=registry_schema,
    )


class GenomeRegistry:
    """JSONL append-only ストアのラッパ。"""

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)

    def append(
        self,
        genome: VoiceGenome,
        op: str,
        seed: Optional[int] = None,
        parents: Optional[List[str]] = None,
        eval_scores: Optional[Dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> RegistryEntry:
        """genome を 1 行追記する。genome_id はここで content hash から計算する。

        `now` はテスト用の注入ポイント（省略時は wall-clock。§CLAUDE.md の
        「wall-clock 依存は created_at 記録のみ許可」の唯一の使用箇所）。
        """
        if op not in VALID_OPS:
            raise RegistryError(f"op は {VALID_OPS} のいずれかでなければならない（実際: {op!r}）")

        # PR#261 レビュー R24: op 別の parent 数不変条件を検証する（I/O 不要な
        # 純粋な入力検証のため、registry 参照が要る後続チェックより先に行う）。
        parent_ids = list(parents or [])
        expected_count = EXPECTED_PARENT_COUNT[op]
        if len(parent_ids) != expected_count:
            raise RegistryError(
                f"op={op!r} は parents を {expected_count} 個持たなければならない"
                f"（実際: {len(parent_ids)} 個 {parent_ids!r}）"
            )

        genome_id = genome_content_hash(genome)
        if self.get(genome_id) is not None:
            # append-only ストアは genome_id の再出現を「来歴の書き換え」として扱う
            # （get()/lineage() は末尾優先で解決するため、同一 genome_id を異なる
            # op/seed/parents で再 append すると既存エントリの来歴が黙って上書き
            # されたように見えてしまう。PR#261 レビュー C3）。
            raise DuplicateGenomeError(
                f"genome_id は既にレジストリに存在するため追記を拒否する（実際: {genome_id!r}）"
            )

        # PR#261 レビュー R10(a): parents の各 ID が既に registry に存在する
        # ことを検証する。存在しない親を指すエントリを許すと、lineage() が
        # 遡上を完走できない（親が見つからず途中で打ち切られる）来歴の欠損を
        # 作ってしまう。
        for parent_id in parent_ids:
            if self.get(parent_id) is None:
                raise MissingParentError(
                    f"parents に registry 未登録の genome_id が含まれる（実際: {parent_id!r}）"
                )

        created_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()

        eval_scores = eval_scores or {"plausibility": None, "grip_ref": None, "novelty": None}
        audit = {
            "reference_set_hash": genome.audit.reference_set_hash,
            "linkability_report_id": genome.audit.linkability_report_id,
            "residual_gate_passed": genome.audit.residual_gate_passed,
        }

        entry = RegistryEntry(
            genome_id=genome_id,
            version=genome.schema_version,
            created_at=created_at,
            parents=parent_ids,
            op=op,
            seed=seed,
            renderer_version=RENDERER_VERSION,
            feature_set_version=FEATURE_SET_VERSION,
            eval=eval_scores,
            audit=audit,
            genome=to_dict(genome),
        )

        # PR#261 レビュー R28: 書き込み前に「直列化 → _entry_from_dict() での
        # 再読み込み」というラウンドトリップを試し、load_all() が将来拒否
        # するであろうエントリは append() の時点で拒否する（書読対称性の
        # 保証）。append() は呼び出し元から渡された `genome`（VoiceGenome
        # オブジェクト）を `to_dict()` するだけで、`from_dict()` 側にしか
        # 実装されていない意味論検証（例: R26 の source_mode 閉じた語彙、
        # R18 の float 有限性、C5 の physio_range 再計算一致）を一切通さない。
        # frozen dataclass は構築時に値を検証しないため、呼び出し元が
        # `SourceSection(source_mode="robotic")` のような不正な値を直接
        # 組み立てて渡すと、append() 自体は成功するのに後で load_all() が
        # そのエントリを拒否する「書けるが読めない」非対称な状態を生み得た。
        # この事前ラウンドトリップは `_entry_from_dict()` を経由するため、
        # 埋め込み genome に対する `from_dict()` の全検証（R3/R12/R16/R18/
        # R26 等）を含め、読み込み側の全規律を書き込み前に前倒しで適用する。
        #
        # 性能注記: 現状（registry 規模は数〜数十エントリ）では 1 回の JSON
        # シリアライズ + パース + 全検証のオーバーヘッドは無視できるが、
        # 将来 append() が高頻度・大規模 registry で使われるようになった
        # 場合はこの検証コストが append 1 回あたりの定数オーバーヘッドとして
        # 効いてくる点に留意すること。
        line = entry.to_json_line()
        try:
            _entry_from_dict(json.loads(line))
        except RegistryError as exc:
            raise RegistryError(
                "append() が構築したエントリが load_all() 側の検証を通過しない"
                f"（書読対称性違反。genome_id={genome_id!r}）: {exc}"
            ) from exc

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.write("\n")
        return entry

    def load_all(self) -> List[RegistryEntry]:
        """全エントリを読み込む。同一 genome_id の複数行は拒否する（PR#261
        レビュー R5）: `append()` 自身は重複を拒否するが、append() を経由せず
        直接編集・結合された JSONL ファイル（過去バージョンからの引き継ぎや
        手動修復等）に重複が紛れ込んだ場合、`get()`/`lineage()` が末尾優先で
        暗黙に解決してしまうと来歴が黙って上書きされたように見える危険が
        append() 単体の防御では防ぎきれない。ロード時にも同じ規律を敷く。

        また append-only ストアの順序不変条件「親は自エントリより前に出現
        していること」も検証する（PR#261 レビュー R10(b)）。`append()`
        自体は R10(a) で親の存在を書き込み時に確認するが、それは append()
        経由の書き込みしか守らない。直接編集・結合された JSONL ファイルの
        行順が入れ替わった場合、`lineage()` の親鎖遡上が意味を失う
        （by_id 辞書には全件揃うため見た目は動くが、「先に生まれた親が
        後から追記される」という append-only の意味論そのものが壊れている）
        ため、ロード時にも同じ規律を敷く。
        """
        if not self.path.exists():
            return []
        entries = []
        seen_ids: set = set()
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                entry = _entry_from_dict(json.loads(line))
                if entry.genome_id in seen_ids:
                    raise DuplicateGenomeError(
                        f"registry に重複 genome_id を検出（実際: {entry.genome_id!r}）"
                    )
                for parent_id in entry.parents:
                    if parent_id not in seen_ids:
                        raise MissingParentError(
                            f"genome_id={entry.genome_id!r} の親 {parent_id!r} が自エントリより"
                            "前に出現していない（append-only の順序不変条件違反）"
                        )
                seen_ids.add(entry.genome_id)
                entries.append(entry)
        return entries

    def get(self, genome_id: str) -> Optional[RegistryEntry]:
        """genome_id に一致する最新（末尾）のエントリを返す（無ければ None）。"""
        match = None
        for entry in self.load_all():
            if entry.genome_id == genome_id:
                match = entry
        return match

    def get_genome(self, genome_id: str) -> Optional[VoiceGenome]:
        entry = self.get(genome_id)
        return from_dict(entry.genome) if entry is not None else None

    def lineage(self, genome_id: str) -> List[RegistryEntry]:
        """genome_id から親鎖を遡上し、[最古の祖先, ..., genome_id 自身] の順で返す。

        循環参照防止のため訪問済み genome_id を記録する（append-only ストアに
        循環は起こらないはずだが、防御的に対応する）。
        """
        by_id: Dict[str, RegistryEntry] = {}
        for entry in self.load_all():
            # load_all() が重複 genome_id を既に拒否する（R5）ため、ここでの
            # 代入は常に 1 対 1 の登録になる。
            by_id[entry.genome_id] = entry

        if genome_id not in by_id:
            raise RegistryError(f"genome_id が見つからない: {genome_id!r}")

        chain: List[RegistryEntry] = []
        visited = set()
        cursor: Optional[str] = genome_id
        while cursor is not None:
            if cursor in visited:
                raise RegistryError(f"lineage に循環参照を検出: {cursor!r}")
            visited.add(cursor)
            entry = by_id.get(cursor)
            if entry is None:
                break
            chain.append(entry)
            parents = entry.parents
            cursor = parents[0] if parents else None  # sample/mutate は親 1 個。crossover は先頭を主系列とする

        chain.reverse()
        return chain
