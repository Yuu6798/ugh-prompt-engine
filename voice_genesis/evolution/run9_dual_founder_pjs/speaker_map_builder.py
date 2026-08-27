"""speaker_map_builder.py — RUN9-L0-HARNESS-3a checkout-stable fixture:
speaker map 合成 embedding の repo-contained 再現ビルダー。

**背景（PR #328 Codex レビュー第1巡指摘1、P1、採用・Fable 確定方針）**:
HARNESS-3a の実測記録（`HARNESS3A_SPEAKER_MAP_RECORD.md`）が参照する
合成スクリプトと生成 embedding は session workdir（repo 外）にのみ存在し、
fresh checkout では `inputs/speaker_map_manifest.json` が pin する
出力 hash（`synthesized_embedding.sha256`、両 founder）を再構成・検証
できなかった。本モジュールはその合成ロジックを repo 内 checkout-stable
fixture として新設し、`builder_sha256` として manifest へ pin することで
「fresh checkout から実測を再現できる」契約を回復する
（`practice_split_builder.py` と同じ前例体裁: repo-contained ビルダー +
自己適用 validator + 単体テスト）。

**ロジック同一性**: 合成式・演算順序・dtype 処理は session workdir
`synth_speaker_map.py`（HARNESS-3a 実測に実際に使用したスクリプト）から
逐語移植した——`ritsu_vec`/`user_vec` = `np.frombuffer(bytes,
dtype=np.float32)`（再解釈のみ、値の変換・キャストなし）、workdir 原本は
`w_r`/`w_u` = `np.float32(eval(expr, {"__builtins__": {}}))` で重みを評価
していた。本 builder では eval() を `run9_schema._evaluate_closed_weight_
expr()`（eval() を使わない閉じた文法パーサ、10進小数リテラル or 単純分数
`'A/B'` のみ許容）へ置き換えている（PR #328 Codex レビュー第2巡指摘5、P2、
採用対応——manifest が現に収載する expr 形式（`'0.75'`/`'1.0/3.0'` 等）は
いずれもこの閉じた文法に含まれるため、数値結果は workdir 原本と同一）。
`synth = (w_r * ritsu_vec + w_u * user_vec)` の単一式・この順序で固定。
L2正規化・摂動・ランダム成分・重み調整は一切行わない（裁定「RUN9 User裁定
— AF0 runtime mapping」逐語の禁止4項目、`inputs/speaker_map_manifest.json`
`synthesis_formula.prohibited` と同一）。

**重みの取得元**: 重み式（`w_ritsu_expr`/`w_user_expr`）は本 builder が
独自の定数として保持しない——pin 済み `inputs/speaker_map_manifest.json`
の `founders.<id>.renormalized_runtime_weights` からそのまま取得する。
単一の正本（manifest）から重みを取得することで、builder 側の重複定義・
将来の数値乖離リスクを構造的に排除する（`renormalized_runtime_weights`
自体は `run9_schema.validate_speaker_map_manifest()` が `coords_raw` から
の機械再導出一致を既に強制している——builder はその検証済みの値を
そのまま消費するだけで、独自に coords から再計算しない）。

**fail-closed 契約**:
  (i)  入力 ritsu/user emb の実測 sha256 を manifest の
       `input_embeddings.{ritsu,user}_emb_sha256` pin 値と照合する
       （不一致は拒否 — 別の emb を渡している/差し替えられている疑い）。
  (ii) 合成結果（384-dim float32 raw バイナリ）の実測 sha256 を manifest
       の `synthesized_embedding.sha256` pin 値と照合する（一致 = 再現
       成功、不一致 = fail-closed 非ゼロ終了）。
  (iii) `--out` の書き込み先が `--ritsu-emb`/`--user-emb` のいずれかと
       同一実体（symlink 経由の alias 含む）でないことを、書き込み前に
       3パスを `Path.resolve()` で解決・比較して確認する（同一実体なら
       書き込みせず拒否 — 検証済み入力 emb の破壊を防ぐ、PR #328 Codex
       レビュー第2巡指摘4、P1、採用対応。`_check_out_does_not_alias_
       inputs()`）。
  (iv) `--out` への書き込みは atomic（同一ディレクトリの一意な staging
       ファイルへ書いて fsync 後 `os.replace()` で置換）——既存の正当な
       emb がある状態で書き込みが中断しても truncate/partial 出力を
       残さない（PR #328 Codex レビュー第3巡指摘7、P2、採用対応。
       `_atomic_write_bytes()`。svp_rpe 側の `utils/atomic_io` 集約実装と
       同型だが、本 run9 系は svp_rpe を import しない独立構成のため builder
       内へ自足させた最小実装）。`_atomic_write_bytes()` は保護対象の入力
       パス群（`protected_paths`）を**必須引数**として受け取り、書き込み
       直前に (iii) と同じ alias 判定ロジック（`_resolve_alias_conflict()`）
       で再チェックする——CLI 側の (iii) preflight は維持した上での二重
       防御であり、将来 `_atomic_write_bytes()` が preflight を経由せず
       直接呼び出される/リファクタされても保護入力を `os.replace()` で
       破壊しない（PR #328 Codex レビュー第8巡指摘16、P1、採用対応）。
  (v)  manifest は `load_canonical_speaker_map_manifest()`（本 builder CLI
       の**唯一の**正規 manifest 取得経路。`synthesize()` の
       `manifest=None`（CLI 既定経路）から呼ばれる）を経由してのみ読む
       ——`run9_schema.load_pinned_speaker_map_manifest()` で (a) manifest
       実バイトが `RUN9_CONTRACT.yaml` の `expected_speaker_map_sha` pin
       と一致すること、(b) 発行済み Founder Genome・reexport/execution_
       profile 等との全 cross-check、を synthesis 前に fail-closed で
       強制する（PR #328 Codex レビュー第4巡指摘9、P1、採用対応——旧実装
       `load_local_speaker_map_manifest()` は manifest の**内部構造検証
       のみ**を行い、同じ untrusted ファイル内の期待値どうしを比較して
       いたため、改変された manifest + 入力 + 期待値の組で偽の
       `reproduced: true` を印字できる穴があった。manifest への直接
       `json.load()` 経路は CLI から排除した）。
  (vi) **実行中の builder 自身の同一性**は `main()` の **verified
       self-exec dispatch**（PR #328 Codex レビュー第6巡指摘13、P1、
       採用・Fable 確定対応方針）が保証する——(a) `main()` が
       `Path(__file__).read_bytes()` を**1回だけ**読み（`source_bytes`）、
       (b) その sha256 を manifest の `builder_provenance.builder_sha256`
       pin と照合し、(c) **照合に使った同一の `source_bytes` オブジェクト**
       を `compile(source_bytes, __file__, "exec")` → 隔離名前空間へ exec
       して得た `synthesize()`/`_check_out_does_not_alias_inputs()`/
       `_atomic_write_bytes()` のみを処理に用いる。これにより「hash した
       バイト列 == 実行されるバイト列」が同一オブジェクトで保証される
       ——旧実装（`load_canonical_speaker_map_manifest()` 内で自己照合
       直前に `Path(__file__).resolve()` を**再度ディスクから読み直して
       いた**）は、import 済みモジュールが束縛する実行コードと自己照合が
       読むバイト列が別物になり得る TOCTOU を構造的に抱えていた
       （import 後・照合前にファイルが置換されると、実行中の旧コードが
       新しいディスクバイトを hash して照合を通し、偽の `reproduced:
       true` を印字できた）。**境界宣言**: この verified self-exec
       dispatch を実装する `main()` 自身の完全性（本 builder が repo から
       どう起動されたか）はこの仕組みの手が届く範囲の外にあり、無限後退
       は解消不能——repo 機構（branch_write_policy + PR レビュー +
       contract pin）を信頼根とする（`run9_schema.py` の各
       `load_pinned_*` 関数が持つ信頼根境界宣言と同型）。
  (vii) **正常系の repo コミット可能なテスト**（PR #328 Codex レビュー第
       7巡指摘14、P1、採用対応）: (i)/(ii) の cross-check (8) は
       `inputs/reexport_manifest.json` の実 emb sha256 pin（rights 制約で
       repo 非同梱の実バイナリの digest）と `input_embeddings` の一致を
       強制するため、canonical loader 経由では実 emb バイナリなしに正常系
       （`reproduced: true`）へ到達できない。`main()` の `manifest_
       override`（テスト専用 kwarg、CLI フラグ非公開）は canonical loader
       呼び出しのみを省略し、self-exec 照合 (a)/(b)・compile/exec
       dispatch (c)・隔離名前空間の本物の `synthesize()` は tmp_path 完結
       の自己整合フィクスチャに対して実際に実行する——`tests/test_
       speaker_map_builder.py` 参照。

出力: 384-dim float32 raw バイナリ（`.tobytes()`、既存 emb と同形式）。
emb バイナリ自体は repo にコミットしない（rights 制約、session workdir /
`reexport_out/onnx_gate_40000/` 等のローカル入力に対してのみ実行する）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

import run9_schema as m  # noqa: E402  (sibling import — repo-wide run9_* convention)

EMB_DIM = 384


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_canonical_speaker_map_manifest(
    *,
    contract_path: Optional[Path] = None,
    manifest_path: Optional[Path] = None,
    rights_manifest_path: Optional[Path] = None,
    identity_domain_path: Optional[Path] = None,
    adjudication_basis_path: Optional[Path] = None,
    gate_synth_py_path: Optional[Path] = None,
    detail_record_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """本 builder CLI が speaker map manifest を得る**唯一の**正規経路
    （PR #328 Codex レビュー第4巡指摘9、P1、採用対応）。

    旧 `load_local_speaker_map_manifest()`（本関数の前身、削除済み）は
    `inputs/speaker_map_manifest.json` を直接 `json.load()` し、
    `run9_schema.validate_speaker_map_manifest()` による**内部構造検証
    のみ**を行っていた——manifest 実バイトが `RUN9_CONTRACT.yaml` の
    `expected_speaker_map_sha` pin と一致するかは確認せず、`synthesize()`
    は同じ untrusted ファイル内が自己申告する
    `synthesized_embedding.sha256` を「期待値」として比較していた。この
    ため manifest・入力 emb・期待 sha256 の3点をまとめて改変すれば、
    builder は改変後の組同士が一致する偽の `reproduced: true` を印字
    できた（本指摘の核心）。

    本関数は `run9_schema.load_run9_contract_from_yaml_path()` で contract
    を、`run9_schema.load_run9_identity_domain()` で identity domain を、
    `run9_schema.load_rights_manifest_json()` で rights manifest をそれぞれ
    正典パスから厳密 parse で読み、`run9_schema.load_pinned_speaker_map_
    manifest()`（`expected_speaker_map_sha` pin の唯一の正規消費経路——
    manifest 実バイトの pin 一致、発行済み Founder Genome・reexport_
    manifest・execution_profile_manifest・gate_synth.py 実ファイルとの
    全 cross-check を fail-closed で強制する）へ渡す。

    **builder 自身の自己照合はこの関数の責務ではない**（2026-08-27、
    PR #328 Codex レビュー第6巡指摘13、P1、採用対応で `main()` へ移設）:
    旧版はここで `Path(__file__).resolve()` を自己照合の**直前に読み
    直して**いたため、import 済みモジュールが束縛する実行コードと自己
    照合が読むバイト列が別物になり得る TOCTOU を抱えていた（`running_
    builder_path` 引数はこの旧自己照合専用だったため削除した）。
    自己照合は現在 `main()` の verified self-exec dispatch（本モジュール
    docstring 契約 (vi) 参照: `main()` が読んだ `source_bytes` を
    compile→exec して得た隔離名前空間の関数のみを処理に用いることで、
    「hash したバイト列 == 実行されるバイト列」を同一オブジェクトで
    保証する）が担う。

    `contract_path`/`manifest_path`/`rights_manifest_path`/
    `identity_domain_path`/`adjudication_basis_path`/`gate_synth_py_path`/
    `detail_record_path`（PR #328 レビュー第8巡指摘17対応で追加）はいずれも
    テスト専用の override 引数——production 呼び出し（全省略）は repo 相対
    の正典パスのみを消費する（他の `load_pinned_*` 系と同じ override 規約）。
    """
    effective_contract_path = (
        contract_path if contract_path is not None else m.RUN9_CONTRACT_YAML_PATH
    )
    effective_rights_manifest_path = (
        rights_manifest_path if rights_manifest_path is not None else m.RIGHTS_MANIFEST_PATH
    )
    effective_identity_domain_path = (
        identity_domain_path if identity_domain_path is not None else m.RUN9_IDENTITY_DOMAIN_PATH
    )
    if not effective_rights_manifest_path.is_file():
        raise m.Run9ValidationError(
            "speaker_map_builder.load_canonical_speaker_map_manifest(): rights manifest source "
            f"{effective_rights_manifest_path} does not exist"
        )

    contract = m.load_run9_contract_from_yaml_path(effective_contract_path)
    domain = m.load_run9_identity_domain(effective_identity_domain_path)
    rights_manifest = m.load_rights_manifest_json(
        effective_rights_manifest_path.read_text(encoding="utf-8")
    )

    data = m.load_pinned_speaker_map_manifest(
        contract,
        domain=domain,
        rights_manifest=rights_manifest,
        manifest_path=manifest_path,
        contract_path=effective_contract_path,
        adjudication_basis_path=adjudication_basis_path,
        gate_synth_py_path=gate_synth_py_path,
        detail_record_path=detail_record_path,
    )
    return data


def synthesize(
    founder_id: str,
    ritsu_emb_path: Path,
    user_emb_path: Path,
    *,
    manifest: Optional[Dict[str, Any]] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """`founder_id` 指定 + ritsu/user emb ファイルパスから合成 embedding を
    決定論的に再構築する。`manifest` を省略すると `load_canonical_speaker_
    map_manifest()` で contract pin・発行済み Founder Genome 等の全
    cross-check 済みの manifest を読む（テストは合成 manifest を明示的に
    渡すことでこの重い canonical 経路を bypass できる——`manifest` 引数は
    テスト専用の穴ではなく、`synthesize()` 自体を純粋な数値検証関数として
    単体テストするための既存の穴あき設計をそのまま維持する）。CLI
    （`main()`）は常に verified self-exec dispatch 経由で `manifest=` を
    明示的に渡して呼ぶ（モジュール docstring 契約 (vi) 参照 — 実行中の
    builder 自身の自己照合は `main()` 側の責務であり、本関数はそれを
    前提としない）。

    戻り値は `(synth, report)`——`report` は入力/出力 sha256・使用した
    重み式・再現成否を含む機械可読レポート（`synth_speaker_map.py`
    workdir 原本のレポート形式を踏襲）。fail-closed 違反はすべて
    `run9_schema.Run9ValidationError` を送出する（部分出力を書かない）。
    """
    data = manifest if manifest is not None else load_canonical_speaker_map_manifest()
    if founder_id not in m.CONTRACT_FOUNDER_IDS:
        raise m.Run9ValidationError(
            f"speaker_map_builder.synthesize(): unknown founder_id {founder_id!r}, "
            f"expected one of {sorted(m.CONTRACT_FOUNDER_IDS)}"
        )
    founder = data["founders"][founder_id]

    ritsu_bytes = ritsu_emb_path.read_bytes()
    user_bytes = user_emb_path.read_bytes()
    ritsu_sha = _sha256_bytes(ritsu_bytes)
    user_sha = _sha256_bytes(user_bytes)

    expected_emb = founder["input_embeddings"]
    if ritsu_sha != expected_emb["ritsu_emb_sha256"]:
        raise m.Run9ValidationError(
            f"speaker_map_builder.synthesize(): {founder_id} 入力 ritsu emb ({ritsu_emb_path}) "
            f"の実測 sha256 ({ritsu_sha!r}) が speaker_map_manifest.json の "
            f"input_embeddings.ritsu_emb_sha256 pin 値 ({expected_emb['ritsu_emb_sha256']!r}) と "
            "一致しない — fail-closed 拒否（別の emb を渡している/差し替えられている疑い）"
        )
    if user_sha != expected_emb["user_emb_sha256"]:
        raise m.Run9ValidationError(
            f"speaker_map_builder.synthesize(): {founder_id} 入力 user emb ({user_emb_path}) "
            f"の実測 sha256 ({user_sha!r}) が speaker_map_manifest.json の "
            f"input_embeddings.user_emb_sha256 pin 値 ({expected_emb['user_emb_sha256']!r}) と "
            "一致しない — fail-closed 拒否（別の emb を渡している/差し替えられている疑い）"
        )

    ritsu_vec = np.frombuffer(ritsu_bytes, dtype=np.float32)
    user_vec = np.frombuffer(user_bytes, dtype=np.float32)
    if ritsu_vec.shape != (EMB_DIM,):
        raise m.Run9ValidationError(
            f"speaker_map_builder.synthesize(): ritsu_vec shape {ritsu_vec.shape} != ({EMB_DIM},)"
        )
    if user_vec.shape != (EMB_DIM,):
        raise m.Run9ValidationError(
            f"speaker_map_builder.synthesize(): user_vec shape {user_vec.shape} != ({EMB_DIM},)"
        )

    weights = founder["renormalized_runtime_weights"]
    # w_ritsu_expr/w_user_expr は m._evaluate_closed_weight_expr()（eval() を
    # 使わない閉じた文法パーサ、10進小数リテラル or 単純分数 'A/B' のみ許容）
    # で評価する——validator（run9_schema.validate_speaker_map_manifest()）
    # と同じパーサを共有することで、builder/validator 間のパーサ乖離による
    # 数値不一致を構造的に排除する（PR #328 Codex レビュー第2巡指摘5、P2、
    # 採用対応）。
    w_r = np.float32(
        m._evaluate_closed_weight_expr(  # noqa: SLF001 - sibling module, see module docstring
            weights["w_ritsu_expr"], field=f"{founder_id}.renormalized_runtime_weights.w_ritsu_expr"
        )
    )
    w_u = np.float32(
        m._evaluate_closed_weight_expr(  # noqa: SLF001 - sibling module, see module docstring
            weights["w_user_expr"], field=f"{founder_id}.renormalized_runtime_weights.w_user_expr"
        )
    )

    # 裁定逐語の1式・この順序で固定（workdir 原本 synth_speaker_map.py と
    # 逐語同一）。L2正規化・摂動・ランダム成分・重み調整は行わない。
    synth = (w_r * ritsu_vec + w_u * user_vec)

    if synth.dtype != np.float32 or synth.shape != (EMB_DIM,):
        raise m.Run9ValidationError(
            "speaker_map_builder.synthesize(): synth dtype/shape invariant violated "
            f"(dtype={synth.dtype}, shape={synth.shape})"
        )
    if not bool(np.all(np.isfinite(synth))):
        raise m.Run9ValidationError(
            "speaker_map_builder.synthesize(): synth contains non-finite value(s)"
        )

    out_bytes = synth.tobytes()
    out_sha = _sha256_bytes(out_bytes)
    expected_sha = founder["synthesized_embedding"]["sha256"]
    report: Dict[str, Any] = {
        "founder_id": founder_id,
        "ritsu_emb_path": str(ritsu_emb_path),
        "user_emb_path": str(user_emb_path),
        "ritsu_emb_sha256": ritsu_sha,
        "user_emb_sha256": user_sha,
        "w_ritsu_expr": weights["w_ritsu_expr"],
        "w_user_expr": weights["w_user_expr"],
        "out_bytes_len": len(out_bytes),
        "out_sha256": out_sha,
        "expected_sha256_per_manifest": expected_sha,
        "reproduced": out_sha == expected_sha,
    }
    if out_sha != expected_sha:
        raise m.Run9ValidationError(
            f"speaker_map_builder.synthesize(): {founder_id} 再構築した合成 embedding の sha256 "
            f"({out_sha!r}) が inputs/speaker_map_manifest.json の "
            f"founders.{founder_id}.synthesized_embedding.sha256 pin 値 ({expected_sha!r}) と "
            f"一致しない — fail-closed（再現失敗。report={report!r}）"
        )
    return synth, report


def _resolve_alias_conflict(out_path: Path, protected_paths: Sequence[Path]) -> Optional[Path]:
    """`out_path` を `Path.resolve()` で解決したうえで、`protected_paths`
    のいずれかと同一実体（symlink 経由の alias 含む）であれば、その
    protected path を返す（alias でなければ `None`）。

    `_check_out_does_not_alias_inputs()`（CLI 呼び出し前の preflight）と
    `_atomic_write_bytes()`（書き込み直前の内部 re-check）が共有する
    alias 判定ロジックの単一実装（PR #328 Codex レビュー第8巡指摘16、P1、
    採用対応）——別々に実装すると、将来どちらか一方だけが改修されて判定が
    食い違う穴を防ぐ。"""
    out_resolved = out_path.resolve()
    for protected in protected_paths:
        if out_resolved == protected.resolve():
            return protected
    return None


def _check_out_does_not_alias_inputs(
    out_path: Path, ritsu_emb_path: Path, user_emb_path: Path,
) -> Optional[str]:
    """`out_path` が `ritsu_emb_path`/`user_emb_path` のいずれかと同一実体
    （symlink 経由の alias 含む）であれば拒否理由の文字列を返す（alias で
    なければ `None`）。`_resolve_alias_conflict()` で3パスを
    `Path.resolve()` 解決してから比較する——`--out` が `--ritsu-emb`/
    `--user-emb` と同一実体（symlink 経由の alias 含む）の場合、無条件
    `write_bytes()` が検証済み入力 emb を破壊する穴を書き込み前に閉じる
    （PR #328 Codex レビュー第2巡指摘4、P1、採用対応）。この preflight は
    `_atomic_write_bytes()` 内部の再チェック（PR #328 レビュー第8巡指摘16
    対応）と同じ判定ロジックを共有する二重防御の1層目である。"""
    conflict = _resolve_alias_conflict(out_path, (ritsu_emb_path, user_emb_path))
    if conflict is None:
        return None
    out_resolved = out_path.resolve()
    if conflict == ritsu_emb_path:
        return (
            f"--out ({out_path}) resolves to the same file as --ritsu-emb ({ritsu_emb_path}), "
            f"resolved={out_resolved} — fail-closed 拒否（同一実体/symlink alias への書き込みは "
            "検証済み入力 emb の破壊を招くため書き込みを拒否する）"
        )
    return (
        f"--out ({out_path}) resolves to the same file as --user-emb ({user_emb_path}), "
        f"resolved={out_resolved} — fail-closed 拒否（同一実体/symlink alias への書き込みは "
        "検証済み入力 emb の破壊を招くため書き込みを拒否する）"
    )


def _atomic_write_bytes(path: Path, data: bytes, protected_paths: Sequence[Path]) -> None:
    """`path` へ `data` を atomic に書き込む（同一ディレクトリ内の一意な
    staging ファイルへ書き、fsync で durable にしてから `os.replace()` で
    置換する。PR #328 Codex レビュー第3巡指摘7、P2、採用対応）。

    旧実装は `--out` へ直接 `Path.write_bytes()` していたため、既存の
    正当な emb が既に置かれている状態で書き込みが中断すると
    truncate/partial 出力が残るおそれがあった。run9 系は
    `src/svp_rpe/utils/atomic_io`（svp_rpe パッケージ）を import しない
    独立構成（本モジュール docstring 参照）のため、同型の最小実装を
    builder 内へ自足させる。

    `protected_paths`（**必須引数**、PR #328 Codex レビュー第8巡指摘16、
    P1、採用対応）: 書き込み保護対象の入力パス群（ritsu/user emb 等）。
    呼び出し元（`_execute_cli()`）は既に `_check_out_does_not_alias_
    inputs()` で preflight 済みだが、`_atomic_write_bytes()` 単体は従来
    `path`/`data` のみを受け取り preflight を信頼するだけだったため、
    将来この関数が preflight を経由せず直接呼び出される/リファクタされる
    と、検証済み保護入力を `os.replace()` で破壊し得る穴があった。本関数
    自身が `_resolve_alias_conflict()`（`_check_out_does_not_alias_
    inputs()` と同一ロジックを共有）で書き込み直前に alias を再チェック
    することで、preflight を省略した呼び出しに対しても fail-closed で
    拒否する（CLI 側の preflight は維持——二重防御。空 sequence を渡せば
    保護なしの旧来動作と等価だが、production 呼び出し（`_execute_cli()`）
    は常に `(args.ritsu_emb, args.user_emb)` を渡す）。alias が見つかれば
    `Run9ValidationError` を送出し、staging ファイルを一切作らずに拒否
    する（`path` の既存実バイトにも一切触れない）。

    失敗時（`BaseException` 含む——`KeyboardInterrupt`/`SystemExit` でも
    staging を残さない）は staging ファイルを best-effort で削除してから
    re-raise する。`os.replace()` 呼び出し前は `path` に一切触れないため、
    失敗しても既存の `path` の実バイトは無傷のまま残る——本関数の
    呼び出し元（`main()`）はこの契約を alias 拒否チェック
    （`_check_out_does_not_alias_inputs()`）の**後**に呼ぶことで、staging
    書き込み自体が alias 判定を迂回して検証済み入力 emb を破壊する経路を
    構造的に持たない。
    """
    conflict = _resolve_alias_conflict(path, protected_paths)
    if conflict is not None:
        raise m.Run9ValidationError(
            f"speaker_map_builder._atomic_write_bytes(): out path ({path}) resolves to the same "
            f"file as a protected input path ({conflict}), resolved={path.resolve()} — fail-closed "
            "拒否（保護入力パスへの破壊的 os.replace() を防ぐ内部 re-check、PR #328 レビュー第8巡"
            "指摘16対応 — preflight を経由しない直接呼び出しでも保護入力を破壊しない）"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _build_argument_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--founder", required=True, choices=sorted(m.CONTRACT_FOUNDER_IDS))
    ap.add_argument("--ritsu-emb", type=Path, required=True)
    ap.add_argument("--user-emb", type=Path, required=True)
    ap.add_argument(
        "--out", type=Path, default=None,
        help="合成 embedding の書き出し先（省略時は再現検証のみ行い、書き出さない）。",
    )
    return ap


# exec 済み隔離名前空間の `__name__` に設定する sentinel。`"__main__"` とは
# 意図的に異なる値にすることで、`compile()`+`exec()` 実行中にモジュール末尾
# の `if __name__ == "__main__": raise SystemExit(main())` が発火せず、
# `main()` の再帰起動が起きない（PR #328 Codex レビュー第6巡指摘13、P1、
# 採用対応。設計意図: 通常の `import` 経路と異なり `exec()` は
# `sys.modules` へ登録しないため、この名前空間内で完結する）。
_VERIFIED_NAMESPACE_NAME = "__speaker_map_builder_verified__"


def _compile_and_exec_verified_source(source_bytes: bytes, file_path: Path) -> Dict[str, Any]:
    """`source_bytes`（呼び出し元が sha256 を pin と照合**済み**の実バイト
    列そのもの）をこのプロセス内で `compile()`→`exec()` し、得られた隔離
    名前空間 dict を返す（PR #328 Codex レビュー第6巡指摘13、P1、採用・
    Fable 確定対応方針 手順1(c)/手順2）。

    `main()` の verified self-exec dispatch の中核: 呼び出し元が pin と
    照合した**同一の** `source_bytes` オブジェクトをそのまま渡すことで、
    「hash したバイト列 == 実行されるバイト列」が同一オブジェクトで保証
    される——`sys.modules["speaker_map_builder"]`（import 済みの、pin
    照合時点とは別バイト列を束縛しているかもしれない旧コード）を一切
    経由しない。`__name__` は `_VERIFIED_NAMESPACE_NAME`（"__main__" では
    ない専用の sentinel）に設定し、exec 中にモジュール末尾の
    `if __name__ == "__main__":` ガードが発火して `main()` が再帰起動する
    ことを防ぐ（設計意図はモジュール上部 docstring 契約 (vi) 参照）。
    """
    code = compile(source_bytes, str(file_path), "exec")
    namespace: Dict[str, Any] = {
        "__name__": _VERIFIED_NAMESPACE_NAME,
        "__file__": str(file_path),
    }
    exec(code, namespace)  # noqa: S102 - verified dispatch: hashed bytes == executed bytes, see docstring
    return namespace


def _execute_cli(
    args: argparse.Namespace,
    *,
    synthesize_fn: Callable[[str, Path, Path], Tuple[np.ndarray, Dict[str, Any]]],
    check_alias_fn: Callable[[Path, Path, Path], Optional[str]],
    atomic_write_fn: Callable[[Path, bytes, Sequence[Path]], None],
) -> int:
    """`main()` のオーケストレーション本体（synth 計算 → alias 判定 →
    atomic write → report 出力）。呼び出し元から関数群を注入させる形に
    することで、production 経路（`main()`）は verified self-exec dispatch
    で得た隔離名前空間の `synthesize`/`_check_out_does_not_alias_inputs`/
    `_atomic_write_bytes` を注入し、単体テストは実 founder embedding
    データ非依存の fake 関数を注入できる（PR #328 Codex レビュー第6巡
    指摘13、P1、採用対応 — 旧実装は `main()` 自体がこれらの処理を直接
    行っており、`smb.synthesize` を monkeypatch すれば足りたが、
    verified self-exec dispatch 後は隔離名前空間の関数のみが実処理に
    使われるため、その注入点をテスト可能な形で切り出した）。"""
    try:
        synth, report = synthesize_fn(args.founder, args.ritsu_emb, args.user_emb)
    except m.Run9ValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.out is not None:
        alias_error = check_alias_fn(args.out, args.ritsu_emb, args.user_emb)
        if alias_error is not None:
            print(f"ERROR: speaker_map_builder.main(): {alias_error}", file=sys.stderr)
            return 1
        atomic_write_fn(args.out, synth.tobytes(), (args.ritsu_emb, args.user_emb))
        report["out_path"] = str(args.out)

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def main(
    argv: Optional[List[str]] = None,
    *,
    running_builder_path: Optional[Path] = None,
    contract_path: Optional[Path] = None,
    manifest_path: Optional[Path] = None,
    rights_manifest_path: Optional[Path] = None,
    identity_domain_path: Optional[Path] = None,
    adjudication_basis_path: Optional[Path] = None,
    gate_synth_py_path: Optional[Path] = None,
    detail_record_path: Optional[Path] = None,
    manifest_override: Optional[Dict[str, Any]] = None,
) -> int:
    """CLI エントリポイント。**verified self-exec dispatch**（PR #328
    Codex レビュー第6巡指摘13、P1、採用・Fable 確定対応方針）で処理を
    完遂する:

    (a) 実行中のこの builder ファイルの実バイト列を**1回だけ**読む
        （`source_bytes` — 既定 `Path(__file__).resolve()`、
        `running_builder_path` はテスト専用の override）。
    (b) canonical loader（`load_canonical_speaker_map_manifest()`、
        contract pin・発行済み Founder Genome 等の全 cross-check 込み）
        で得た manifest の `builder_provenance.builder_sha256` と
        `sha256(source_bytes)` を照合し、不一致は fail-closed で拒否
        する。
    (c) **照合に使った同一の `source_bytes` オブジェクト**を
        `_compile_and_exec_verified_source()` で隔離名前空間へ
        compile→exec し、その名前空間の `synthesize()`/
        `_check_out_does_not_alias_inputs()`/`_atomic_write_bytes()` の
        みを `_execute_cli()` へ注入して処理を完遂する。

    これにより「hash したバイト列 == 実行されるバイト列」が同一
    オブジェクトで保証され、旧実装（`load_canonical_speaker_map_
    manifest()` 内で自己照合の**直前に** `Path(__file__).resolve()` を
    再度ディスクから読み直していたため、import 済みモジュール内の実行
    コードと自己照合が読んだバイト列が別物になり得た——import 後・照合
    前にファイルが置換されると、実行中の旧コードが新しいディスクバイト
    を hash して照合を通し、偽の `reproduced: true` を印字できる
    TOCTOU）のパス再読込構造を構造的に閉じる。

    **境界宣言**: この検証コード自体（`main()` 自身が repo からどう
    起動されたか）の完全性は本関数の手が届く範囲の外にあり、無限後退は
    解消不能——repo 機構（branch_write_policy + PR レビュー +
    contract pin）を信頼根とする。`run9_schema.py` の各 `load_pinned_*`
    関数が持つ信頼根境界宣言と同型。

    `running_builder_path`/`contract_path`/`manifest_path`/
    `rights_manifest_path`/`identity_domain_path`/
    `adjudication_basis_path`/`gate_synth_py_path`/`detail_record_path`
    はいずれもテスト専用の override 引数——production 呼び出し（全省略）は
    repo 相対の正典パスのみを消費する。

    `manifest_override`（テスト専用、PR #328 Codex レビュー第7巡指摘14、
    P1、採用対応で新設）: 指定すると `load_canonical_speaker_map_
    manifest()` 呼び出し自体を省略し、渡した dict をそのまま (b)/(c) の
    `data` として使う。**必要な理由**: `load_pinned_speaker_map_
    manifest()` の cross-check (8)（`founders.<id>.input_embeddings` が
    `inputs/reexport_manifest.json` の実 emb sha256 pin と一致すること）
    は repo 内の実在パス（`REEXPORT_MANIFEST_PATH`、override 引数なし）
    にのみ照合するため、この cross-check を経由する限り正常系
    （`reproduced: true`）の到達には実 ritsu/user emb バイナリが要る——
    しかしそのバイナリは rights 制約により repo にコミットできない
    （モジュール docstring 参照）。一方 (a)/(b) の self-exec 照合と (c) の
    compile/exec dispatch 自体は `data` の出所（canonical loader か
    override か）に依存しない独立した防御であり、`manifest_override` は
    その2つ + 隔離名前空間の本物の `synthesize()` を、tmp_path で完結する
    自己整合フィクスチャに対して実際に実行させるための穴——`argparse` の
    CLI フラグとしては公開しない（production 呼び出しからは到達不能、
    テストの直接キーワード引数経由でのみ使う）。省略時（`None`）の挙動は
    従来と完全に同一（`load_canonical_speaker_map_manifest()` を経由）。
    """
    args = _build_argument_parser().parse_args(argv)

    effective_running_builder_path = (
        running_builder_path if running_builder_path is not None else Path(__file__).resolve()
    )
    if not effective_running_builder_path.is_file():
        print(
            "ERROR: speaker_map_builder.main(): verified self-exec source "
            f"{effective_running_builder_path} (実行中の builder) does not exist",
            file=sys.stderr,
        )
        return 1
    # (a) 1回だけ読む——以降このバッファ（`source_bytes`）のみを hash・
    # compile・exec する。パスを再読込しない（TOCTOU 対策の核心）。
    source_bytes = effective_running_builder_path.read_bytes()

    if manifest_override is not None:
        data = manifest_override
    else:
        try:
            data = load_canonical_speaker_map_manifest(
                contract_path=contract_path,
                manifest_path=manifest_path,
                rights_manifest_path=rights_manifest_path,
                identity_domain_path=identity_domain_path,
                adjudication_basis_path=adjudication_basis_path,
                gate_synth_py_path=gate_synth_py_path,
                detail_record_path=detail_record_path,
            )
        except m.Run9ValidationError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    # (b) 照合に使う sha256 は (a) で読んだ同一バッファから導出する。
    actual_sha = _sha256_bytes(source_bytes)
    pinned_sha = data["builder_provenance"]["builder_sha256"]
    if actual_sha != pinned_sha:
        print(
            "ERROR: speaker_map_builder.main(): 実行対象の builder 実バイト sha256 "
            f"({actual_sha!r}) が manifest の builder_provenance.builder_sha256 pin 値 "
            f"({pinned_sha!r}) と一致しない — verified self-exec dispatch が synthesis 前に "
            "fail-closed で拒否する（PR #328 Codex レビュー第6巡指摘13、P1、採用対応）",
            file=sys.stderr,
        )
        return 1

    # (c) 照合に使った同一 source_bytes オブジェクトを compile→exec し、
    # 隔離名前空間の関数群のみを処理に用いる（実行対象バイト == 照合対象
    # バイトを同一オブジェクトで保証する）。
    namespace = _compile_and_exec_verified_source(source_bytes, effective_running_builder_path)

    def _verified_synthesize(founder_id: str, ritsu_emb_path: Path, user_emb_path: Path) -> Any:
        return namespace["synthesize"](founder_id, ritsu_emb_path, user_emb_path, manifest=data)

    return _execute_cli(
        args,
        synthesize_fn=_verified_synthesize,
        check_alias_fn=namespace["_check_out_does_not_alias_inputs"],
        atomic_write_fn=namespace["_atomic_write_bytes"],
    )


if __name__ == "__main__":
    raise SystemExit(main())
