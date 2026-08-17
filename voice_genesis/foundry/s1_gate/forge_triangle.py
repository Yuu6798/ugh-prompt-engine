"""S3 run 4 ゲート判定材料④ — 3 アンカー重心座標補間バッチの機械化鍛造。

**GPU 実測未実施・run 4の5K早期ゲートが初実行**（本ファイルはロジック層
（重心座標補間の数学・ブラインド規律の機械化・genome 台帳出力）のみを本環境
で検証済み。実際の音声合成 — `gate_synth.py` の `run_pipeline()` 呼び出し —
は onnxruntime 未導入・実 checkpoint 非保有のため本環境では未実行。下記
「GPU 実測として残る範囲」参照）。

対応: `S3_RUN4_RUNBOOK.md` §5④・`DESIGN_S3_backfill.md` §4・§8「クロー側で
要確認」#5。

## 何をするスクリプトか

S2（`results_s2/s2_record_2026-08-16.md`）はリツ/PJS 2 アンカー間の**線分**
補間（lerp/slerp + 直交摂動）を `s2_forge.py`（scratchpad 完結・非コミット、
本リポジトリには現存しない）で鍛造した。run 4 は第 3 話者（user, spk_id=2）
が加わり、補間空間はアンカー3点が張る**三角形（2-単体, 2-simplex）**になる。
本スクリプトはその内部点を **重心座標 (barycentric coordinates)**
`w_r・e_ritsu + w_p・e_pjs + w_u・e_user`（`w_r+w_p+w_u=1, w>=0`）で生成する
S2 の一般化である。

## gate_synth.py との関係（read-only・onnxruntime 非依存）

`s2_forge.py` は `gate_synth.py` を import して `run_pipeline()` を直接呼び、
補間ベクトルを `speaker_embed_vector` として渡すことで合成本体
（5セッション構築・二重符号化・reflow spk_embed 配線）を一切再実装しな
かった（`s2_record_2026-08-16.md` §1「実装」）。本スクリプトは**その設計を
踏襲しつつ、合成呼び出し自体は行わない**（本環境に onnxruntime が無く
`gate_synth.py` はモジュールトップレベルで `import onnxruntime` するため
import 自体が失敗する — `gate_synth_run4.py` と同じ制約）。本スクリプトが
書き出すのは各候補の spk_embed（384-dim float32 raw、`gate_synth.py:675`
`load_speaker_embed_vector` と同一バイト契約）のみであり、これを
`gate_synth.run_pipeline(..., speaker_embed_vector=<候補ベクトル>, ...)`
（または `gate_synth_run4.py` 経由）へ渡して実合成するのは run 4 クロー側の
次工程（GPU 環境・実 checkpoint が必要）。embed のロード/書き出しロジック
（`load_embed_vector*`/`write_embed_vector`）自体は `np.frombuffer`
1行の軽量再実装であり、`gate_synth.py` を import しない（onnxruntime を
要求しないため、本ファイルはロジック層全体が本環境でテスト可能）。

## ブラインド規律の機械化（`s2_record_2026-08-16.md`/`DESIGN_S2_foundry_loop.md`
§5 を一次ソースとして同一規律を実装）

1. **対応表 sha256 封印**: `build_correspondence_table()` がラベル→
   voice_id/method/weights の対応表を組み、`seal_correspondence()` が
   `correspondence.json`（内容）と `correspondence.sha256`（ハッシュ値のみの
   別ファイル）を分離して書き出す。公開 record に書けるのは後者のみ
   （S2 の「対応表の中身は記載しない、sha256 のみ記載」規律と同型）。
   `verify_sealed_correspondence()` で開封時の改変なし確認を機械化する。
2. **4 候補バッチ分割**: `build_blind_batch()` は常に厳密 4 候補
   （interior 3 + hidden control 1）を受け取り、`shuffle_seed` から導出した
   決定論的シャッフルでラベル A〜D へ割り当てる（User 決裁「一度の判定は
   4 候補に絞って分割」`DESIGN_S2_foundry_loop.md` §4 を踏襲）。
3. **隠しコントロール**: `make_hidden_control_from_anchor()`（アンカー
   埋め込みそのものを候補として混入。S2 VG-S2-008 と同型）と
   `make_hidden_control_from_prior()`（既判定個体の spk_embed を混入 — S2 の
   確立バッチには無かったが、DESIGN_S3 が新規に要求する「既判定個体」
   コントロール種別、run 4 では S2 の既判定 10 個体のいずれかの embed を
   再利用する運用を想定）。いずれも genome 台帳上は `identity_latent_ref`
   を `null` のまま維持する（S2 VG-S2-008 の設計理由と同一 — 台帳は恒久
   git 管理下にあり、実ベクトルのハッシュを記載すると `.emb` ファイルとの
   照合で正体が逆算されるため。`s2_record_2026-08-16.md` §9.2 参照）。
4. **候補ファイル名の非漏洩**: `write_blind_candidate_embeds()` は
   `candidate_<label>.emb`（label=A〜D）でのみ書き出し、voice_id・method・
   重み等の識別情報をファイル名に一切含めない。

## genome 台帳（`results_s2/genome_ledger.json` と同型 schema）

`build_genome_ledger()` は `results_s2/genome_ledger.json`
（`voicegenesis-genome-ledger/0.1`）と同一 schema・同一必須キー集合
（`tests/test_genome_ledger_shape.py` の `REQUIRED_TOP_LEVEL_KEYS`/
`REQUIRED_GENOME_KEYS` が定義する形状）で出力する。voice_id は run 4 用の
`VG-S3-xxx` 系列（S2 の `VG-S2-xxx` と衝突しない別系列）。
`tests/test_forge_triangle.py` は `test_genome_ledger_shape.py` の検証関数を
**そのまま import して本スクリプトが生成した台帳へ適用**し、shape 適合を
機械的に確認する（検証ロジックの複製・分岐を避けるため）。

## 決定論

`--seed`（重み・ブラインドシャッフルに使う RNG シード）と `--perturb-seed`
（直交摂動方向のシード、任意）はいずれも CLI 引数として明示する
（wall-clock 由来の値は一切使わない。日付フィールドも `--date` で明示的に
渡す — リポジトリの決定論規約、`CLAUDE.md` Coding Conventions
「タイムスタンプ」節、および `S3_RUN4_RUNBOOK.md` §8 の rename/move
記帳禁止と同じ「実測・明示のみ」原則に従う）。`np.random.default_rng(seed)`
（摂動方向）と `random.Random(seed)`（ブラインドシャッフル）はいずれも
同一シードで同一出力を返すことを保証された標準 PRNG であり、同一入力
（アンカー embed バイト列 + 重み + seed）から常に同一の候補ベクトル・
同一のブラインド割当が再現される。

## GPU 実測として本環境に残る範囲（正直明記）

- `gate_synth.run_pipeline()`（または `gate_synth_run4.py`）を実際に呼んで
  本スクリプトが書き出した候補 spk_embed から WAV を合成する工程 —
  onnxruntime 未導入・実 acoustic checkpoint 非保有のため本環境では未実行。
- 実 run 4 acoustic checkpoint から export された実 `*.{ritsu,pjs,user}.emb`
  を入力に使った鍛造そのもの — 本環境のテストはすべて `tmp_path` に
  書き出したフェイク embed（決定論的疑似乱数生成、384-dim float32）を
  入力に使う「入出力契約のテスト」までであり、実 embed の値・分布特性は
  未検証。
- 隠しコントロールに「既判定個体」を使う運用（S2 の既判定 10 個体の実
  embed を run 4 個体と混合する）は、実 S2 embed ファイル（scratchpad 非
  コミット、本環境に非保有）を入力にした実測は未実施。

## review #265（PR #265 Codex 第2波レビュー、採用済み）

- **P1**: `load_embed_vector`/`load_embed_vector_with_sha` に fail-closed
  検証を追加（`_validate_embed_vector`）。バイト列が `EMBED_DIM`(384) 個の
  float32 に厳密に整列すること・全要素が有限（NaN/Inf 不可）であることを
  アンカー embed・`--control-prior-embed` の両読み込み経路で全数検証する。
  従来は切り詰められた float 整列長や NaN/Inf を含む破損 embed を無検証で
  受理し、`candidate_*.emb` を成功出力してしまっていた。
- **P2**: `barycentric_interpolate` の重み検証順序を変更し、負重み・和=1
  検査より**先に**有限性検査を行う。NaN は Python の比較演算子が常に
  `False` を返すため、`nan < -atol`（負重み検査）も
  `abs(nan+...-1.0) > atol`（和=1 検査）もすり抜け、`--weights nan,0.5,0.5`
  のような入力が simplex 検査全体を素通りしていた。

## review #265 R3（PR #265 Codex 第3波レビュー、採用済み）

- **#8 (P1)**: `run_generate` が correspondence/candidate embed/genome
  ledger を `out_dir` へ逐次直接書きしていたため、途中失敗で新旧混合の
  出力ディレクトリが残り得た（ブラインド評価の汚染源）。
  `<out_dir>.staging-<pid>` へ全成果物を完全構築してから
  `convert_d3._swap_into_place`（read-only import・原子 rename + 失敗時
  ロールバック）で公開する方式へ変更。途中失敗（`BaseException`）は
  staging を破棄し、既存 `out_dir` を無傷のまま残す。
- **#9 (P1)**: candidate 出力（`out_dir` 配下）が入力 embed
  （3 アンカー・`--control-prior-embed`）を上書きし得る穴を閉じた。
  実害例: 前回バッチの `candidate_A.emb` を次回バッチの
  `--control-prior-embed` として同一 `--out-dir` で渡すケース。
  `_reject_embed_input_collisions`（新規・`convert_d3.OutputCollisionError`
  を再利用）が `out_dir`/`<out_dir>.old`/staging の 3 管理パスいずれについ
  ても「入力 embed がそのディレクトリ配下に包含される」向きの検査を
  staging 構築前に fail-closed で行う。
- **#10 (P2)**: `--perturb-magnitude` の非有限値（NaN/Inf）・負値を候補構築
  前に拒否（`run_generate` 早期チェック + 消費点 `apply_perturbation`
  双方で検証、P1/P2 と同じ「消費点で検証する」設計を踏襲）。数値 CLI 引数
  の有限性を全数棚卸しした結果: `--seed`/`--voice-id-start`/`--generation`/
  `--perturb-seed` は `type=int` で `int("nan")`/`int("inf")` が
  `ValueError`（argparse 段階で `SystemExit`）になるため元から安全、
  `--weights` の各成分は既存 P2（`barycentric_interpolate`）で有限性検査
  済み。`--perturb-magnitude`（唯一の追加 `type=float` 引数）だけが未検査
  だったため、本項目で閉じた。

## review #265 R4（PR #265 Codex 第4波レビュー、採用済み）

- **#12 (P1)**: `run_generate` が 3 アンカー embed を `load_embed_vector`
  （ベクトルのみ）で読み込み、入力ファイルの sha256 を捨てていたため、
  台帳には norms/cosines と呼び出し側自己申告の `backbone_checkpoint`
  文字列しか残らず、異なる checkpoint 由来の embed 混入・アンカー差し替え
  が起きても台帳上は同一 provenance を主張してしまっていた。3 アンカー
  （`_load_anchors_with_sha`）・prior control（`_build_control_candidate`）
  の読み込みを `load_embed_vector_with_sha` に統一し、各 sha256 を記録する:
  アンカーは非秘匿（三角形の頂点そのもの・S2 の `VG-S2-ANCHOR-*` と同型で
  常に公開）のため genome 台帳の `anchors` セクションへ `embed_sha256` として
  そのまま公開する。隠しコントロール（anchor-copy / prior-individual-copy）
  の入力ファイル sha256 は、公開すると開封前にブラインド秘匿が破れる
  （台帳は恒久 git 管理、正体が sha256 相関で逆算されうる — S2
  VG-S2-008 と同じ非交差方針）ため、公開 genome エントリ
  （`identity_latent_ref` は従来どおり null）には**出さず**、封印済み
  `correspondence.json`（開封後にのみ意味を持つ）へ `source_embed_sha256`
  として記録する。`test_genome_ledger_shape.py` の必須キー集合は変更せず
  追加フィールドとして配置（既存 shape 検証に影響なし、実測で確認）。
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
import random
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

_THIS_DIR = Path(__file__).resolve().parent
_S1_DATAPREP_DIR = _THIS_DIR.parent / "s1_dataprep"

# --- sibling import: s1_dataprep/convert_d3.py（read-only import。原子公開
# `_swap_into_place`・衝突検査 `_reject_output_collision`・
# `OutputCollisionError` を再利用する — review #265 R3 P1（#8/#9）。
# convert_d3.py は numpy/onnxruntime に依存しない軽量スクリプト
# （argparse/csv/math/os/shutil/subprocess/sys/wave のみ）のため、
# `gate_synth.py` と違いモジュールトップレベルで import して問題ない
# （`assemble_run4.py`/`convert_user.py` と同じ sibling-import 前例を踏襲）。
if str(_S1_DATAPREP_DIR) not in sys.path:
    sys.path.insert(0, str(_S1_DATAPREP_DIR))
import convert_d3  # noqa: E402

# gate_synth.py 契約（load_speaker_embed_vector, gate_synth.py:675-688）:
# raw float32、ヘッダなし、話者ベクトル1本。384 は S1/S2 run3 実測値
# （s2_record_2026-08-16.md §1）。
EMBED_DIM = 384

GENOME_LEDGER_SCHEMA = "voicegenesis-genome-ledger/0.1"
GENOME_LEDGER_SCHEMA_REF = "VISION_evolution_theory_v0.1.md 付録A.1 VoiceGenome"

ANCHOR_NAMES: Tuple[str, str, str] = ("ritsu", "pjs", "user")
ANCHOR_VOICE_IDS: Dict[str, str] = {
    "ritsu": "VG-S3-ANCHOR-RITSU",
    "pjs": "VG-S3-ANCHOR-PJS",
    "user": "VG-S3-ANCHOR-USER",
}

# S2 (results_s2/genome_ledger.json) の rights_class 判断を 3 アンカーへ拡張:
# PJS = CC BY-SA 4.0（最も制約が強く share-alike を継承）・リツ = 権利者配布
# 規約準拠（商用可）・user = 本人音源（本人利用は自由、recording_kit/README.md
# §8「配合後の権利会計は S2 ループ側の責務」）。三者混合の合成声は最も制約の
# 強い PJS 条項を継承しつつ user 由来である旨を明記する。
RIGHTS_CLASS_TRIANGLE = "SYNTHETIC_INTERP_TRIANGLE_PJS_CC_BY_SA_INHERITED_USER_OWN_VOICE"

BLIND_LABELS: Tuple[str, str, str, str] = ("A", "B", "C", "D")


# ---------------------------------------------------------------------------
# embed I/O — gate_synth.py と同一バイト契約の軽量再実装（onnxruntime 非依存
# にするため gate_synth.py は import しない。契約の一次ソースは docstring
# 冒頭参照）
# ---------------------------------------------------------------------------


def _validate_embed_vector(vector: np.ndarray, *, source: str) -> None:
    """review #265 P1: バイト列が `EMBED_DIM`(384) 個の float32 に**厳密に**
    整列し、かつ全要素が有限（NaN/Inf 不可）であることを fail-closed で強制
    する。従来はこの検証が無く、切り詰められた float 整列長（例: 383 要素分
    のバイト数しかない破損ファイル）や NaN/Inf を含む embed をそのまま
    受理して `candidate_*.emb` を成功出力してしまっていた（forge 成果物の
    公開前に全数検証する設計判断 — PR #265 Codex 第2波レビュー採用）。
    アンカー embed・`--control-prior-embed` の両方の読み込み経路
    （`load_embed_vector`/`load_embed_vector_with_sha`）を通る。
    """
    if vector.shape != (EMBED_DIM,):
        raise ValueError(
            f"malformed embed at {source}: expected exactly {EMBED_DIM} float32 elements, "
            f"got {vector.shape[0] if vector.ndim == 1 else vector.shape} "
            f"(byte length not aligned to {EMBED_DIM} float32 values, or wrong dim — refusing to "
            f"silently truncate/pad, fail-closed)"
        )
    if not np.all(np.isfinite(vector)):
        n_nonfinite = int(np.count_nonzero(~np.isfinite(vector)))
        raise ValueError(
            f"malformed embed at {source}: contains {n_nonfinite} non-finite value(s) "
            f"(NaN/Inf) out of {EMBED_DIM} — refusing to forge from a non-finite anchor/control "
            f"embed, fail-closed"
        )


def load_embed_vector(path: Path) -> np.ndarray:
    vector = np.frombuffer(path.read_bytes(), dtype=np.float32).copy()
    _validate_embed_vector(vector, source=str(path))
    return vector


def load_embed_vector_with_sha(path: Path) -> Tuple[np.ndarray, str]:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    vector = np.frombuffer(data, dtype=np.float32).copy()
    _validate_embed_vector(vector, source=str(path))
    return vector, digest


def vector_sha256(vector: np.ndarray) -> str:
    return hashlib.sha256(vector.astype(np.float32).tobytes()).hexdigest()


def write_embed_vector(path: Path, vector: np.ndarray) -> None:
    path.write_bytes(vector.astype(np.float32).tobytes())


# ---------------------------------------------------------------------------
# 重心座標補間（3 アンカー）+ 直交摂動（S2 の一般化）
# ---------------------------------------------------------------------------


def barycentric_interpolate(
    e_r: np.ndarray, e_p: np.ndarray, e_u: np.ndarray,
    w_r: float, w_p: float, w_u: float, *, atol: float = 1e-9,
) -> np.ndarray:
    """`w_r*e_r + w_p*e_p + w_u*e_u`。重み `w_r+w_p+w_u=1` かつ全て `>=0`
    （三角形の閉じた内部 = 頂点・辺を含む 2-単体）を検証してから合成する。
    頂点そのもの（例: w_r=1, w_p=w_u=0）はアンカーを厳密に再現する。

    review #265 P2: 有限性チェックを simplex 検査（負重み・和=1）より**先に**
    行う。NaN は Python の `<`/`==` 比較が常に `False` を返すため、
    `nan < -atol` も `abs(nan + ... - 1.0) > atol`（`nan > atol` も `False`）
    もすり抜け、`--weights nan,0.5,0.5` のような入力が両チェックを素通り
    してしまっていた（PR #265 Codex 第2波レビュー採用）。Inf も同様に
    `total` を非有限化し和チェックをすり抜けうるため同じ扱いとする。
    """
    for name, w in (("w_r", w_r), ("w_p", w_p), ("w_u", w_u)):
        if not np.isfinite(w):
            raise ValueError(f"non-finite weight rejected (barycentric requires finite w): {name}={w}")
    for name, w in (("w_r", w_r), ("w_p", w_p), ("w_u", w_u)):
        if w < -atol:
            raise ValueError(f"negative weight rejected (barycentric requires w>=0): {name}={w}")
    total = w_r + w_p + w_u
    if abs(total - 1.0) > atol:
        raise ValueError(f"weights must sum to 1 (barycentric constraint), got w_r+w_p+w_u={total}")
    return (w_r * e_r + w_p * e_p + w_u * e_u).astype(np.float32)


def orthogonal_perturbation_vector(
    span_vectors: Sequence[np.ndarray], dim: int, seed: int,
) -> np.ndarray:
    """`span_vectors`（三角形が張る面を規定する差分ベクトル群。通常
    `[e_p-e_r, e_u-e_r]` の 2 本）に直交する単位ベクトルを Gram-Schmidt で
    構築する（S2 バッチ2 `orthogonal_perturb_vector` の一般化 — S2 は
    2 アンカー間の diff 1 本にのみ直交させたが、三角形は面 = 2 本のベクトル
    が張る部分空間に直交させる必要がある）。`span_vectors` 自身が直交して
    いる保証はない（`e_p-e_r`/`e_u-e_r` は一般に非直交）ため、まず
    `span_vectors` を Gram-Schmidt で正規直交基底へ変換し、その正規直交基底
    に対して `v` の射影を 1 パスで除去する（正規直交基底同士は互いに直交
    なので除去の順序に依存しない — 素朴に「span_vectors を順に 1 回ずつ引く」
    実装は span_vectors 自体が非直交だと 2 本目の除去で 1 本目への直交性を
    壊す。実測で確認済み・単体テストで固定）。決定論:
    `np.random.default_rng(seed)` から標準正規サンプルを 1 本引く。
    """
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float64)
    orthonormal_basis: List[np.ndarray] = []
    for span in span_vectors:
        w = span.astype(np.float64).copy()
        for b in orthonormal_basis:
            w = w - np.dot(w, b) * b
        w_norm = float(np.linalg.norm(w))
        if w_norm > 1e-12:
            orthonormal_basis.append(w / w_norm)
    for b in orthonormal_basis:
        v = v - np.dot(v, b) * b
    norm = float(np.linalg.norm(v))
    if norm == 0.0:
        raise ValueError(
            "orthogonal_perturbation_vector degenerated to the zero vector for this seed; "
            "choose a different --perturb-seed"
        )
    return (v / norm).astype(np.float32)


def apply_perturbation(base: np.ndarray, direction_unit: np.ndarray, magnitude: float) -> np.ndarray:
    """review #265 R3 P2 (#10): `magnitude` の有限性・非負性をここ（実際の
    消費点）で検証する。CLI 層（`run_generate`）でも早期に同じ検査を行うが、
    `apply_perturbation` は直接 API 呼び出しからも到達可能なため、P1/P2
    （embed 形状・有限性 / 重心座標の非有限重み拒否）と同じ「消費点で検証
    する」設計を踏襲し、CLI を経由しない呼び出しも fail-closed にする。
    """
    if not math.isfinite(magnitude):
        raise ValueError(f"non-finite perturbation magnitude rejected: {magnitude}")
    if magnitude < 0.0:
        raise ValueError(f"negative perturbation magnitude rejected: {magnitude}")
    return (base.astype(np.float32) + direction_unit.astype(np.float32) * np.float32(magnitude))


# ---------------------------------------------------------------------------
# Genome candidate / blind batch
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class GenomeCandidate:
    voice_id: str
    vector: np.ndarray
    method: str
    weights: Optional[Dict[str, float]]
    mutation_ops: List[str]
    seed: int
    rights_class: str
    note: str
    parent_ids: Tuple[str, ...]
    is_hidden_control: bool = False
    # review #265 R4 #12: 隠しコントロール（アンカー由来 / 既判定個体由来）の
    # **入力ファイルの** sha256（`load_embed_vector_with_sha` 実測値。生成した
    # `vector` 自身の sha ではなく、読み込みに使った実ファイルの sha —
    # 両者は理論上一致するはずだが、実ファイル由来の値を明示的に運ぶことで
    # 「読み込み・検証を経た実ファイルそのもの」の provenance を保つ）。
    # 公開 genome 台帳（`identity_latent_ref`）には**入れない**
    # （ブラインド秘匿の維持 — #12 の докstring 参照）。封印済み
    # correspondence.json（開封後のみ意味を持つ）にのみ記録する。
    # interior/barycentric candidate（3 アンカーの線形結合）には単一の
    # 「入力ファイル」という概念が無いため None のまま
    # （3 アンカー自身の sha は `build_anchor_section` が公開ledgerへ記録する）。
    source_embed_sha256: Optional[str] = None


@dataclasses.dataclass(frozen=True)
class BlindBatch:
    label_to_candidate: Dict[str, GenomeCandidate]


def build_blind_batch(candidates: Sequence[GenomeCandidate], *, shuffle_seed: int) -> BlindBatch:
    """`candidates` を厳密に `len(BLIND_LABELS)`（=4）本受け取り、
    `shuffle_seed` から導出した決定論的シャッフルでラベル A〜D へ割り当てる
    （User 決裁「一度の判定は4候補に絞って分割」`DESIGN_S2_foundry_loop.md`
    §4 を踏襲）。
    """
    if len(candidates) != len(BLIND_LABELS):
        raise ValueError(
            f"a blind batch must contain exactly {len(BLIND_LABELS)} candidates, got {len(candidates)}"
        )
    order = list(range(len(candidates)))
    random.Random(shuffle_seed).shuffle(order)
    return BlindBatch(
        label_to_candidate={label: candidates[i] for label, i in zip(BLIND_LABELS, order)}
    )


def make_interior_candidate(
    anchors: Dict[str, np.ndarray], *,
    voice_id: str, w_r: float, w_p: float, w_u: float, seed: int,
    rights_class: str = RIGHTS_CLASS_TRIANGLE,
    perturbation: Optional[Tuple[np.ndarray, float, int]] = None,
) -> GenomeCandidate:
    """三角形内部点の候補を 1 体構築する。`perturbation` は
    `(direction_unit, magnitude, perturb_seed)` を渡すと重心座標点に直交
    摂動を加える（S2 バッチ2/3 の直交摂動オプションと同型）。
    """
    vector = barycentric_interpolate(
        anchors["ritsu"], anchors["pjs"], anchors["user"], w_r, w_p, w_u,
    )
    mutation_ops = [
        f"INTERP_BARYCENTRIC_W_RITSU_{w_r:.2f}_W_PJS_{w_p:.2f}_W_USER_{w_u:.2f}"
    ]
    note = "barycentric interior point"
    method = "barycentric"
    if perturbation is not None:
        direction_unit, magnitude, perturb_seed = perturbation
        vector = apply_perturbation(vector, direction_unit, magnitude)
        mutation_ops.append(
            f"PLUS_ORTHOGONAL_PERTURB_MAGNITUDE_{magnitude:.6f}_SEED_{perturb_seed}"
        )
        note = "barycentric interior point + orthogonal perturbation"
        method = "barycentric_plus_orthogonal_perturbation"
    return GenomeCandidate(
        voice_id=voice_id,
        vector=vector,
        method=method,
        weights={"ritsu": w_r, "pjs": w_p, "user": w_u},
        mutation_ops=mutation_ops,
        seed=seed,
        rights_class=rights_class,
        note=note,
        parent_ids=(
            ANCHOR_VOICE_IDS["ritsu"], ANCHOR_VOICE_IDS["pjs"], ANCHOR_VOICE_IDS["user"],
        ),
    )


def make_hidden_control_from_anchor(
    anchors: Dict[str, np.ndarray], anchor_name: str, *,
    voice_id: str, seed: int, rights_class: str = RIGHTS_CLASS_TRIANGLE,
    anchor_shas: Optional[Dict[str, str]] = None,
) -> GenomeCandidate:
    """アンカー埋め込みそのものを隠しコントロールとして候補に混入する
    （S2 VG-S2-008 `hidden_anchor_control` と同型。`identity_latent_ref` は
    台帳側で null のまま維持する — docstring 冒頭「ブラインド規律の機械化」
    #3 参照）。`anchor_shas`（review #265 R4 #12。`_load_anchors_with_sha` の
    戻り値）を渡すと、選ばれたアンカーの入力ファイル sha256 を
    `source_embed_sha256` として候補へ記録する（公開台帳には出さない —
    `GenomeCandidate.source_embed_sha256` docstring 参照。省略時は従来どおり
    `None`）。"""
    if anchor_name not in ANCHOR_NAMES:
        raise ValueError(f"unknown anchor_name {anchor_name!r}; expected one of {ANCHOR_NAMES}")
    return GenomeCandidate(
        voice_id=voice_id,
        vector=anchors[anchor_name],
        method="hidden_anchor_control",
        weights=None,
        mutation_ops=["HIDDEN_CONTROL_ANCHOR_COPY_SEALED"],
        seed=seed,
        rights_class=rights_class,
        note="hidden control (anchor identity sealed in correspondence.json until unsealed)",
        parent_ids=(ANCHOR_VOICE_IDS[anchor_name],),
        is_hidden_control=True,
        source_embed_sha256=anchor_shas.get(anchor_name) if anchor_shas is not None else None,
    )


def make_hidden_control_from_prior(
    prior_vector: np.ndarray, *,
    voice_id: str, prior_voice_id: str, seed: int, rights_class: str = RIGHTS_CLASS_TRIANGLE,
    prior_embed_sha256: Optional[str] = None,
) -> GenomeCandidate:
    """既判定個体（例: S2 の VG-S2-xxx）の spk_embed を隠しコントロールとして
    候補に混入する。`prior_voice_id` は正体そのもの（`parent_ids` に記録され
    るが、genome 台帳の `identity_latent_ref` は null のまま — アンカー版と
    同じ非交差方針）。`prior_embed_sha256`（review #265 R4 #12。
    `load_embed_vector_with_sha` の実測値）を渡すと `source_embed_sha256` と
    して候補へ記録する（公開台帳には出さない。省略時は従来どおり `None`）。
    """
    return GenomeCandidate(
        voice_id=voice_id,
        vector=prior_vector,
        method="hidden_prior_individual_control",
        weights=None,
        mutation_ops=["HIDDEN_CONTROL_PRIOR_INDIVIDUAL_COPY_SEALED"],
        seed=seed,
        rights_class=rights_class,
        note="hidden control (previously-judged individual identity sealed in correspondence.json until unsealed)",
        parent_ids=(prior_voice_id,),
        is_hidden_control=True,
        source_embed_sha256=prior_embed_sha256,
    )


# ---------------------------------------------------------------------------
# ブラインド対応表の封印
# ---------------------------------------------------------------------------


def build_correspondence_table(batch: BlindBatch) -> Dict[str, Dict[str, object]]:
    """review #265 R4 #12: `source_embed_sha256`（隠しコントロールの入力
    ファイル sha256。interior candidate は None）を含める。これは
    correspondence.json（封印・開封まで非公開）にのみ載る値であり、公開
    genome 台帳（`genome_entry`）には出さない — アンカー差し替え・
    checkpoint 混入を**開封後に**監査できるようにしつつ、開封前のブラインド
    秘匿は維持する。
    """
    table: Dict[str, Dict[str, object]] = {}
    for label, cand in batch.label_to_candidate.items():
        table[label] = {
            "voice_id": cand.voice_id,
            "method": cand.method,
            "weights": cand.weights,
            "is_hidden_control": cand.is_hidden_control,
            "note": cand.note,
            "source_embed_sha256": cand.source_embed_sha256,
        }
    return table


def seal_correspondence(
    correspondence: Dict[str, object], out_dir: Path, *, basename: str = "correspondence",
) -> Tuple[Path, Path, str]:
    """`correspondence.json`（内容）と `<basename>.sha256`（ハッシュ値のみ）
    を分離して書き出す。record に載せてよいのは後者のみ（S2 の「対応表の
    中身は記載しない、sha256 のみ記載」規律、docstring 冒頭 #1 参照）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    correspondence_path = out_dir / f"{basename}.json"
    payload = json.dumps(correspondence, ensure_ascii=False, indent=2, sort_keys=True)
    correspondence_path.write_text(payload, encoding="utf-8")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    sha_path = out_dir / f"{basename}.sha256"
    sha_path.write_text(digest + "\n", encoding="utf-8")
    return correspondence_path, sha_path, digest


def verify_sealed_correspondence(correspondence_path: Path, sha_path: Path) -> bool:
    """封印された対応表が改変されていないことを確認する（sha256 再計算 ==
    別ファイルの記録値）。開封時の実施内容を機械化したもの
    （`s2_record_2026-08-16.md` §11.2/§13.2「sha256 を独立再計算し...完全一致
    を確認」と同型の検証）。"""
    payload = correspondence_path.read_text(encoding="utf-8")
    recomputed = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    recorded = sha_path.read_text(encoding="utf-8").strip()
    return recomputed == recorded


def write_blind_candidate_embeds(batch: BlindBatch, out_dir: Path) -> Dict[str, Path]:
    """`candidate_<label>.emb`（label=A〜D）のみでファイルを書き出す。
    voice_id・method・重み等の識別情報はファイル名に一切含めない
    （docstring 冒頭「ブラインド規律の機械化」#4 参照）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, Path] = {}
    for label, cand in batch.label_to_candidate.items():
        path = out_dir / f"candidate_{label}.emb"
        write_embed_vector(path, cand.vector)
        paths[label] = path
    return paths


# ---------------------------------------------------------------------------
# genome 台帳（results_s2/genome_ledger.json と同型 schema）
# ---------------------------------------------------------------------------


def genome_entry(cand: GenomeCandidate, *, generation: int, backbone_checkpoint: str) -> Dict[str, object]:
    identity: Dict[str, object] = {"interpolation_space": "spk_embed_384d", "method": cand.method}
    if cand.weights is not None:
        identity["weights"] = cand.weights
    if cand.note:
        identity["note"] = cand.note
    identity_latent_ref = (
        None if cand.is_hidden_control else f"blob:sha256:{vector_sha256(cand.vector)}"
    )
    return {
        "voice_id": cand.voice_id,
        "generation": generation,
        "parent_ids": list(cand.parent_ids),
        "backbone_checkpoint": backbone_checkpoint,
        "identity_latent_ref": identity_latent_ref,
        "identity": identity,
        "phonation": {},
        "performance_prior": {},
        "mutation_ops": list(cand.mutation_ops),
        "seed": cand.seed,
        "rights_class": cand.rights_class,
        "status": "CANDIDATE",
    }


def build_anchor_section(
    anchors: Dict[str, np.ndarray], anchor_shas: Optional[Dict[str, str]] = None,
) -> Dict[str, object]:
    """review #265 R4 #12: `anchor_shas`（`_load_anchors_with_sha` の戻り値。
    各アンカー**入力ファイル**の sha256）を渡すと `embed_sha256` フィールド
    として各アンカーエントリへ記録する。アンカーの正体は元々非秘匿（三角形
    の頂点そのもの・S2 の `VG-S2-ANCHOR-*` と同型で常に公開）のため、
    隠しコントロールと異なり公開台帳へそのまま記録してよい —
    「異なる checkpoint 由来の embed 混入・アンカー差し替え」を台帳の
    self-reported `backbone_checkpoint` 文字列だけでなく実ファイルの sha256
    でも検出可能にする（省略時は従来どおり `embed_sha256` キー無し =
    後方互換）。
    """
    section: Dict[str, object] = {}
    for name in ANCHOR_NAMES:
        vector = anchors[name]
        entry: Dict[str, object] = {
            "description": f"run 4 acoustic checkpoint の spk_embed (話者={name}, {vector.shape[0]}次元)。",
            "embed_l2_norm": float(np.linalg.norm(vector)),
        }
        if anchor_shas is not None and name in anchor_shas:
            entry["embed_sha256"] = anchor_shas[name]
        section[ANCHOR_VOICE_IDS[name]] = entry
    pairwise: Dict[str, float] = {}
    for a, b in (("ritsu", "pjs"), ("ritsu", "user"), ("pjs", "user")):
        va, vb = anchors[a], anchors[b]
        denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
        cosine = float(np.dot(va, vb) / denom) if denom > 0.0 else 0.0
        pairwise[f"{a}_{b}"] = cosine
    section["anchor_pairwise_cosine_similarity"] = pairwise
    return section


def build_genome_ledger(
    candidates: Sequence[GenomeCandidate], anchors: Dict[str, np.ndarray], *,
    batch: str, date: str, backbone_checkpoint: str, generation: int = 1,
    anchor_shas: Optional[Dict[str, str]] = None,
) -> Dict[str, object]:
    """`results_s2/genome_ledger.json`（`voicegenesis-genome-ledger/0.1`）と
    同一 schema・同一必須キー集合で run 4 の genome 台帳を組み立てる
    （`tests/test_genome_ledger_shape.py` の検証関数をそのまま適用して形状
    適合を確認する — `tests/test_forge_triangle.py` 参照）。`anchor_shas`
    （review #265 R4 #12）は `build_anchor_section` へそのまま渡す。
    """
    return {
        "schema": GENOME_LEDGER_SCHEMA,
        "schema_ref": GENOME_LEDGER_SCHEMA_REF,
        "batch": batch,
        "date": date,
        "backbone_checkpoint": backbone_checkpoint,
        "genomes": [
            genome_entry(c, generation=generation, backbone_checkpoint=backbone_checkpoint)
            for c in candidates
        ],
        "anchors": build_anchor_section(anchors, anchor_shas),
    }


# ---------------------------------------------------------------------------
# 原子的公開（convert_d3._swap_into_place と同型）+ 入出力衝突検査
# ---------------------------------------------------------------------------


def _reject_embed_input_collisions(
    managed_dir: Path, protected_embed_paths: Sequence[Optional[Path]],
) -> None:
    """review #265 R3 P1 (#9): `managed_dir`（`out_dir`/`<out_dir>.old`/
    `<out_dir>.staging-<pid>` のいずれか — 原子公開の過程で丸ごと置換・削除
    され得るディレクトリ）が、入力 embed ファイル（3 アンカー embed・
    `--control-prior-embed`）を配下に含んでいる場合、公開前に fail-closed
    で拒否する。

    具体例（設計判断・レビュー指摘の実害例）: 前回バッチの
    `<out_dir>/candidate_A.emb` を次回バッチの `--control-prior-embed` として
    **同一 `--out-dir`** で渡すと、`_swap_into_place` は `out_dir` 全体を
    `.old` へ退避してから `staging_dir` を `out_dir` へ差し替える —
    入力ファイル自体は直ちには破壊されないが（`.old` へ退避されるだけ）、
    それに気づかず次回以降の実行で `.old` が `shutil.rmtree` される時点で
    元の入力ファイルは失われる。「今回は壊れないから安全」という暗黙の
    前提に依存させず、入口で拒否する。

    `convert_d3._reject_output_collision` は `protected_roots`（ディレクトリ
    同士の双方向包含判定）と `protected_files`（単一ファイルの完全一致判定
    のみ）の 2 種を提供するが、「単一の入力ファイルが出力ディレクトリ
    **配下に包含される**」向きの判定は持たないため、本関数で補う
    （判定結果は `convert_d3.OutputCollisionError` に統一する — 呼び出し側
    が単一の例外型で捕捉できるようにする）。
    """
    if not managed_dir.exists():
        return
    managed_dir_resolved = managed_dir.resolve()
    for raw in protected_embed_paths:
        if raw is None:
            continue
        p = Path(raw)
        if not p.exists():
            continue
        resolved = p.resolve()
        if resolved == managed_dir_resolved:
            raise convert_d3.OutputCollisionError(
                f"input embed {p} IS the managed output path {managed_dir}"
                f"（fail-closed で拒否）"
            )
        try:
            resolved.relative_to(managed_dir_resolved)
        except ValueError:
            continue
        raise convert_d3.OutputCollisionError(
            f"input embed {p} is inside {managed_dir}, which this run's atomic staging "
            f"publish will replace/discard wholesale（fail-closed で拒否。前回バッチの "
            f"candidate embed を同一 out-dir 配下の --control-prior-embed 等として渡す "
            f"ケースを含む）"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_weights_triple(text: str) -> Tuple[float, float, float]:
    parts = text.split(",")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            f"--weights expects 'w_r,w_p,w_u' (3 comma-separated floats), got {text!r}"
        )
    try:
        w_r, w_p, w_u = (float(p) for p in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--weights values must be floats: {text!r}") from exc
    return w_r, w_p, w_u


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_gen = sub.add_parser(
        "generate",
        help="3 アンカー重心座標補間の 4 候補ブラインドバッチを 1 本鍛造する",
    )
    p_gen.add_argument("--ritsu-embed", required=True, help="<exp_name>.ritsu.emb のパス")
    p_gen.add_argument("--pjs-embed", required=True, help="<exp_name>.pjs.emb のパス")
    p_gen.add_argument("--user-embed", required=True, help="<exp_name>.user.emb のパス")
    p_gen.add_argument("--out-dir", required=True, help="候補 embed・対応表・台帳の出力先")
    p_gen.add_argument("--seed", type=int, required=True, help="ブラインドシャッフル用シード（wall-clock 禁止）")
    p_gen.add_argument("--batch-name", required=True, help="genome 台帳 batch フィールド (例: S3-batch1)")
    p_gen.add_argument("--date", required=True, help="genome 台帳 date フィールド (例: 2026-08-17。CLI 明示、システム時計は使わない)")
    p_gen.add_argument("--backbone-checkpoint", required=True, help="run 4 acoustic checkpoint sha256 (例: sha256:<hex>)")
    p_gen.add_argument(
        "--weights", action="append", type=_parse_weights_triple, required=True,
        help="'w_r,w_p,w_u' 形式で重心座標を指定。三角形内部候補 3 体分ちょうど 3 回指定する",
    )
    p_gen.add_argument("--voice-id-start", type=int, required=True, help="VG-S3-<N> の開始番号 (4 体分連番で消費)")
    p_gen.add_argument("--generation", type=int, default=1)
    p_gen.add_argument("--rights-class", default=RIGHTS_CLASS_TRIANGLE)
    p_gen.add_argument("--control-kind", choices=("anchor", "prior"), required=True)
    p_gen.add_argument("--control-anchor", choices=ANCHOR_NAMES, default=None,
                        help="--control-kind anchor の場合必須")
    p_gen.add_argument("--control-prior-embed", default=None,
                        help="--control-kind prior の場合必須: 既判定個体の spk_embed パス")
    p_gen.add_argument("--control-prior-voice-id", default=None,
                        help="--control-kind prior の場合必須: 既判定個体の voice_id (例: VG-S2-004)")
    p_gen.add_argument("--perturb-magnitude", type=float, default=0.0,
                        help="0.0(既定) = 摂動なし。>0 で 3 内部候補全てに同一方向の直交摂動を加える")
    p_gen.add_argument("--perturb-seed", type=int, default=None,
                        help="--perturb-magnitude>0 の場合必須（直交摂動方向の決定論シード）")
    p_gen.set_defaults(func="generate")
    return parser


def _load_anchors_with_sha(args: argparse.Namespace) -> Tuple[Dict[str, np.ndarray], Dict[str, str]]:
    """review #265 R4 #12: 3 アンカーの読み込みを `load_embed_vector_with_sha`
    に統一し、ベクトルと**入力ファイルの** sha256 の両方を返す（従来の
    `_load_anchors` はベクトルのみ返し sha を捨てていた — 異なる checkpoint
    由来の embed 混入・アンカー差し替えが起きても台帳上は self-reported
    `backbone_checkpoint` 文字列しか残らず検出できなかった）。
    """
    vectors: Dict[str, np.ndarray] = {}
    shas: Dict[str, str] = {}
    for name, raw_path in (
        ("ritsu", args.ritsu_embed), ("pjs", args.pjs_embed), ("user", args.user_embed),
    ):
        vector, sha = load_embed_vector_with_sha(Path(raw_path))
        vectors[name] = vector
        shas[name] = sha
    return vectors, shas


def _build_control_candidate(
    args: argparse.Namespace, anchors: Dict[str, np.ndarray], voice_id: str,
    anchor_shas: Dict[str, str],
) -> GenomeCandidate:
    if args.control_kind == "anchor":
        if args.control_anchor is None:
            raise SystemExit("error: --control-kind anchor requires --control-anchor")
        return make_hidden_control_from_anchor(
            anchors, args.control_anchor, voice_id=voice_id, seed=args.seed,
            rights_class=args.rights_class, anchor_shas=anchor_shas,
        )
    if args.control_kind == "prior":
        if args.control_prior_embed is None or args.control_prior_voice_id is None:
            raise SystemExit(
                "error: --control-kind prior requires --control-prior-embed and --control-prior-voice-id"
            )
        # review #265 R4 #12: prior 個体も load_embed_vector_with_sha に統一する。
        prior_vector, prior_sha = load_embed_vector_with_sha(Path(args.control_prior_embed))
        return make_hidden_control_from_prior(
            prior_vector, voice_id=voice_id, prior_voice_id=args.control_prior_voice_id,
            seed=args.seed, rights_class=args.rights_class, prior_embed_sha256=prior_sha,
        )
    raise SystemExit(f"error: unknown --control-kind {args.control_kind!r}")


def run_generate(args: argparse.Namespace) -> Dict[str, object]:
    if len(args.weights) != 3:
        raise SystemExit(
            f"error: --weights must be given exactly 3 times (3 interior candidates), got {len(args.weights)}"
        )
    # review #265 R3 P2 (#10): --perturb-magnitude の有限性・非負性を候補
    # 構築前に検証する（数値 CLI 引数の有限性ファミリー — embed(P1)/重み(P2)
    # の残り 1 軸。他の数値引数は type=int のため int() が "nan"/"inf" を
    # ValueError で拒否し argparse 段階で既に閉じている — 「全数確認」の
    # 棚卸し結果は本関数 docstring 末尾参照）。
    if not math.isfinite(args.perturb_magnitude):
        raise SystemExit(
            f"error: --perturb-magnitude must be finite, got {args.perturb_magnitude}"
        )
    if args.perturb_magnitude < 0.0:
        raise SystemExit(
            f"error: --perturb-magnitude must be non-negative, got {args.perturb_magnitude}"
        )
    if args.perturb_magnitude > 0.0 and args.perturb_seed is None:
        raise SystemExit("error: --perturb-magnitude>0 requires --perturb-seed")

    # --- 1. 入力ロード + 候補構築（すべてインメモリ。出力ファイルは一切
    #        触れない — P1/P2/#10 の検証はここで完結する） -----------------
    anchors, anchor_shas = _load_anchors_with_sha(args)  # review #265 R4 #12
    dim = anchors["ritsu"].shape[0]

    perturbation = None
    if args.perturb_magnitude > 0.0:
        span_vectors = (anchors["pjs"] - anchors["ritsu"], anchors["user"] - anchors["ritsu"])
        direction_unit = orthogonal_perturbation_vector(span_vectors, dim, args.perturb_seed)
        perturbation = (direction_unit, args.perturb_magnitude, args.perturb_seed)

    voice_ids = [f"VG-S3-{args.voice_id_start + i:03d}" for i in range(4)]
    interior_candidates = [
        make_interior_candidate(
            anchors, voice_id=voice_ids[i], w_r=w_r, w_p=w_p, w_u=w_u, seed=args.seed,
            rights_class=args.rights_class, perturbation=perturbation,
        )
        for i, (w_r, w_p, w_u) in enumerate(args.weights)
    ]
    control_candidate = _build_control_candidate(args, anchors, voice_ids[3], anchor_shas)

    candidates = interior_candidates + [control_candidate]
    batch = build_blind_batch(candidates, shuffle_seed=args.seed)

    # --- 2. 衝突検査（staging 構築の前に fail-closed。#9） ------------------
    out_dir = Path(args.out_dir)
    old_dir = out_dir.parent / f"{out_dir.name}.old"
    staging_dir = out_dir.parent / f"{out_dir.name}.staging-{os.getpid()}"

    protected_embeds: List[Optional[Path]] = [
        Path(args.ritsu_embed), Path(args.pjs_embed), Path(args.user_embed),
    ]
    if args.control_kind == "prior" and args.control_prior_embed is not None:
        protected_embeds.append(Path(args.control_prior_embed))

    convert_d3._reject_output_collision([out_dir, old_dir, staging_dir], protected_roots=[])
    for managed_dir in (out_dir, old_dir, staging_dir):
        _reject_embed_input_collisions(managed_dir, protected_embeds)

    # --- 3. staging ディレクトリへ全成果物を完全構築 → 原子スワップ
    #        （#8。convert_d3.convert()/_swap_into_place と同型。途中失敗は
    #        BaseException で捕捉して staging を破棄し、既存 out_dir は
    #        無傷のまま残す — ブラインド評価を汚染する「新旧混合の出力
    #        ディレクトリ」を作らない） -----------------------------------
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)
    try:
        correspondence = build_correspondence_table(batch)
        correspondence_path, sha_path, digest = seal_correspondence(correspondence, staging_dir)
        embed_paths = write_blind_candidate_embeds(batch, staging_dir)

        ledger = build_genome_ledger(
            candidates, anchors, batch=args.batch_name, date=args.date,
            backbone_checkpoint=args.backbone_checkpoint, generation=args.generation,
            anchor_shas=anchor_shas,
        )
        ledger_path = staging_dir / "genome_ledger_run4.json"
        ledger_path.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=False), encoding="utf-8",
        )

        convert_d3._swap_into_place(staging_dir, out_dir)
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    # staging_dir は _swap_into_place 完了時点で out_dir へ rename 済み
    # （もう存在しない）。返す成果物パスは公開後の最終位置（out_dir 配下）
    # へ揃える。
    final_correspondence_path = out_dir / correspondence_path.name
    final_sha_path = out_dir / sha_path.name
    final_embed_paths = {label: out_dir / p.name for label, p in embed_paths.items()}
    final_ledger_path = out_dir / ledger_path.name

    print(f"| forge_triangle: batch={args.batch_name} candidates={len(candidates)} seed={args.seed}")
    print(f"| correspondence sealed: {final_correspondence_path} (sha256={digest})")
    for label in BLIND_LABELS:
        print(f"| {final_embed_paths[label]}")
    print(f"| genome ledger: {final_ledger_path}")

    return {
        "correspondence_path": final_correspondence_path,
        "sha_path": final_sha_path,
        "embed_paths": final_embed_paths,
        "ledger_path": final_ledger_path,
        "ledger": ledger,
    }


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.cmd == "generate":
        run_generate(args)
    else:  # pragma: no cover - argparse `required=True` already guards this
        raise SystemExit(f"error: unknown command {args.cmd!r}")


if __name__ == "__main__":
    main()
