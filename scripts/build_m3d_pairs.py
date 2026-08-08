"""build_m3d_pairs.py — M3d 校正実測: pairs manifest builder（決定論・乱数不使用）。

`docs/DESIGN_M3_melody_comparator.md` §6（正本）と M3d 実行設計メモ §1〜§2 に従い、
`scripts/run_melody_comparison.py`（M3d ハーネス）が読む pairs manifest
（`m3-comparison-pairs/0.1`）を機械生成する。

生成物:

1. vocadito（実声・40 clip 中の選定 18 clip）の pitch/time 変形 WAV
   （`make_melody_pairs.make_variants` を流用。librosa 決定論）
2. 合成旋律（`m3d_synth_specs.yaml` + `build_melody_bench.build_signal`）の
   positive 変形 WAV + 狙い撃ち negative（rhythm/interval）WAV
3. 上記を束ねる pairs manifest（単一ファイル。
   `tests/fixtures/melody_bench/m3d_pairs_manifest.yaml`）
4. 生成 WAV 全件 + manifest の sha256 pin サイドカー
   （`build/external_m3d/m3d_pairs_pins.json`）
   — pairs manifest スキーマ（`run_melody_comparison._REQUIRED_PAIR_KEYS`）は
   pair あたり厳密に 6 キー（pair_id/kind/split/audio_a/audio_b/expected）のみを
   許可し未知キーを fail-closed で拒否するため、sha256 pin と material 区分
   （vocadito=real_voice / synthetic）は manifest の pair dict へ埋め込まず、
   本サイドカーへ分離する（pair_id の `_real_`/`_synth_` 接頭辞でも material は
   判別できる — 設計メモ §2）。

vocadito pin 照合は **全数** 実施し、1 件でも不一致/欠落があれば生成を一切開始
せず fail-closed で終了する（exit 非 0・部分出力なし）— manifest / WAV / pin
サイドカーのいずれも書き込まれない。

**manifest は単一ファイルのまま維持する（Codex レビュー R2 対応の設計判定）**:
当初は real_voice/synthetic への 2 ファイル分割を検討したが、
`run_melody_comparison._validate_manifest_composition`（tuning に狙い撃ち
negative 必須・holdout に negative 必須、等）は単一 98 対 manifest を前提にした
素材構成契約であり、分割後の real-only/synth-only manifest はいずれも単体では
このローダを通らないことが実装検証で判明した（real-only は狙い撃ち negative
が synth 専用のため tuning 狙い撃ち negative 0 件、synth-only は
negative_cross を持たないため holdout negative 0 件）。ハーネスの構成契約を
変えずに R2（Codex 指摘: 全 positive 単一バケット・synth の not_comparable が
real 由来の凍結提案まで巻き込む問題）へ対応するため、**素材別会計は
`run_melody_comparison.py` の evaluate phase 側**（`_partition_pairs_by_
material` / `material_accounting`）で行う——manifest 自体は単一のまま、
pair_id の `_real_`/`_synth_` マーカーで evaluator が読み分ける。

**アトミック公開（Codex レビュー R3 対応）**: 生成物一式（変形 WAV・manifest・
pins サイドカー）は out_dir と同一ファイルシステム上の staging ディレクトリへ
全生成 → 全検証成功後に `_publish_staged_bundle`（snapshot + rollback 付き、
`svp_rpe.utils.atomic_io.atomic_publish_bundle` と同じ流儀）で一括公開する。
途中失敗時は publish 済み分を削除し退避済みの既存ファイルを元位置へ復元する
ため、既存の公開済みセットは「初回ビルド」だけでなく「再ビルド」時にも無傷で
残る。

**--check-only の digest 照合（Codex レビュー R1 対応）**: 従来は WAV pin のみを
再照合し、manifest 自体のバイト内容は pins サイドカーへ記録するだけで再照合
していなかった。ここで (a) sidecar のスキーマ検証 (b) sidecar 記録の
manifest sha256 と現物 manifest ファイルのバイト sha256 の一致検証 (c) 全 WAV
pin の一致検証を fail-closed で行う。限界の明記: sidecar は非コミットのビルド
生成物であり、順序証明の最終根拠は git 履歴 + ハーネスの holdout ロックの
まま。本照合は事故的ドリフトを fail-closed 化する計器である。

**Codex レビュー第 2 ラウンド対応**:

- **N1（build 入力 pin の完全化）**: 各 build 入力（`m2c_external_fixtures.yaml`
  / `m3d_synth_specs.yaml`）は `_read_bytes_and_sha256` でバイト列を一度だけ
  読み、その同一バイトから hash 計算とパースの両方を行う（TOCTOU 解消）。
  sidecar に両入力の sha256 を記録する（スキーマ `m3d-pairs-pins/0.3`）。
  `--check-only` は記録済みの全 build 入力 digest を現物と再照合し、
  不一致/欠落は fail-closed とする。
- **N3（公開先衝突の拒否）**: 生成（librosa/build_signal 呼び出し）を開始する
  前に、公開予定の全出力先（`out_dir` 配下の生成 WAV・manifest-out・
  pins-out）を resolve() し、(a) 出力同士の重複 (b) 出力と入力（fixtures
  yaml・synth specs yaml・vocadito WAV 全件）の衝突、のいずれかがあれば
  fail-closed で拒否する（`_reject_output_input_collisions`）。
- **N2（成功時 .prev 残置の解消）**: `_publish_staged_bundle` の publish
  ループが例外なく完走したら、退避しておいた `.prev` snapshot を全て削除する
  （rollback 経路の挙動は不変更）。

**Codex レビュー第 3 ラウンド対応**:

- **T2（vocadito バイトの公開直前再照合・境界宣言の更新）**: 音声読込経路
  （`librosa.load`）は無改造のまま、staging が完成し（＝以後 librosa 等の
  読込を一切行わない状態）公開を開始する直前に、全 vocadito 入力 WAV を
  `verify_vocadito_pins` で再度 pin と照合する（`run_and_publish`）。
  第 2 ラウンド時点では「pin 照合〜`librosa.load` 読込の窓でファイルが
  差し替えられた場合、未承認バイト由来の変形 WAV が公開されうる」ことを
  対応範囲外の残存懸念として記録していたが、本対応でその境界宣言を撤回
  する——読込経路自体は変えないため生成は起こり得るが、**公開はされない**
  （1 件でも不一致なら fail-closed で公開を中止し、staging を破棄・既公開
  セットを無傷のまま残す）。
- **T3（clip ID / fixture ID の path traversal 拒否）**: fixtures
  （`m2c_external_fixtures.yaml`）ロード直後に全 clip_id を、
  `m3d_synth_specs.yaml` ロード直後に全 fixture id を、それぞれ字句検証する
  （`_validate_id_lexical`。許可文字集合: 英数字・アンダースコア・ハイフンの
  み——実在の vocadito clip_id 全 40 件で確認済み）。パス区切り・`..`・絶対
  パスはこの許可文字集合の外にあるため機械的に拒否される。SYNTH_* 定数
  （Python 側ハードコード id）にもモジュールインポート時に同じ検証を適用する。
  加えて多層防御として、生成先ファイルパスを実際に構築する箇所
  （`_write_wav_in_dir`/`_resolve_within`）でも resolve() 後のパスが
  staging dir / out_dir 配下に内包されることを確認し、内包外は fail-closed
  で拒否する。

使い方::

    python scripts/build_m3d_pairs.py
    python scripts/build_m3d_pairs.py --vocadito-dir /path/to/vocadito --out-dir /tmp/m3d_pairs
    python scripts/build_m3d_pairs.py --check-only
    python scripts/build_m3d_pairs.py --summary-out manifest_summary.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import librosa
import numpy as np
import soundfile as sf
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import build_melody_bench as bmb  # noqa: E402
import run_melody_comparison as harness  # noqa: E402
from make_melody_pairs import make_variants  # noqa: E402
from svp_rpe.utils.hashing import file_sha256  # noqa: E402

# --------------------------------------------------------------------------- #
# 既定パス
# --------------------------------------------------------------------------- #
DEFAULT_VOCADITO_DIR = ROOT / "build" / "external_m3d" / "vocadito"
DEFAULT_OUT_DIR = ROOT / "build" / "external_m3d" / "m3d_pairs"
DEFAULT_MANIFEST_OUT = ROOT / "tests" / "fixtures" / "melody_bench" / "m3d_pairs_manifest.yaml"
DEFAULT_PINS_OUT = ROOT / "build" / "external_m3d" / "m3d_pairs_pins.json"
M2C_FIXTURES_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m2c_external_fixtures.yaml"
M3D_SYNTH_SPECS_PATH = ROOT / "tests" / "fixtures" / "melody_bench" / "m3d_synth_specs.yaml"

# --------------------------------------------------------------------------- #
# 事前登録パラメータ（M3d 実行設計メモ §1、新値を発明しない）
# --------------------------------------------------------------------------- #
TUNING_COUNT = 12
HOLDOUT_COUNT = 6

# vocadito positive_transform の変形: pitch +3st / -5st・time rate 0.87 / 1.12
# （設計メモ §1.1）。`make_variants` の返す key（`pitch_{n:+g}st` /
# `time_x{rate:g}`）→ ファイル名・pair_id に使う短縮ラベルの対応を固定する。
VOCADITO_SEMITONES: Tuple[float, ...] = (3.0, -5.0)
VOCADITO_TIME_RATES: Tuple[float, ...] = (0.87, 1.12)
VOCADITO_VARIANT_LABELS: Dict[str, str] = {
    "pitch_+3st": "pitch_p3",
    "pitch_-5st": "pitch_m5",
    "time_x0.87": "rate_087",
    "time_x1.12": "rate_112",
}
# 生成順（manifest の決定論的な行順のため、dict 順ではなく明示リストを使う）
VOCADITO_VARIANT_ORDER: Tuple[str, ...] = (
    "pitch_+3st",
    "pitch_-5st",
    "time_x0.87",
    "time_x1.12",
)

# 合成 positive の変形: 移調 +3 / 変速 0.9（設計メモ §1.3）
SYNTH_POS_SEMITONES: Tuple[float, ...] = (3.0,)
SYNTH_POS_TIME_RATES: Tuple[float, ...] = (0.9,)
SYNTH_POS_VARIANT_LABELS: Dict[str, str] = {
    "pitch_+3st": "pitch_p3",
    "time_x0.9": "rate_090",
}
SYNTH_POS_VARIANT_ORDER: Tuple[str, ...] = ("pitch_+3st", "time_x0.9")

SYNTH_TUNING_POS_ID = "m3d_synth_tuning_pos"
SYNTH_HOLDOUT_POS_ID = "m3d_synth_holdout_pos"

# 狙い撃ち negative（合成・tuning のみ、設計メモ §1.3）: (kind, pair 内 idx, a, b)
SYNTH_NEGATIVE_RHYTHM_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("m3d_synth_rhythm_neg1_a", "m3d_synth_rhythm_neg1_b"),
    ("m3d_synth_rhythm_neg2_a", "m3d_synth_rhythm_neg2_b"),
)
SYNTH_NEGATIVE_INTERVAL_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("m3d_synth_interval_neg1_a", "m3d_synth_interval_neg1_b"),
    ("m3d_synth_interval_neg2_a", "m3d_synth_interval_neg2_b"),
)

_MANIFEST_SCHEMA = harness._EXPECTED_MANIFEST_SCHEMA  # "m3-comparison-pairs/0.1"（凍結スキーマそのものを参照。値の複製をしない）
# R2R 対応 N1（Codex レビュー第 2 ラウンド・build 入力 pin の完全化）: 従来
# （0.2）は `m2c_external_fixtures_sha256` のみを記録し `m3d_synth_specs.yaml`
# は無 pin だった。ここで両 build 入力（fixtures yaml / synth specs yaml）の
# sha256 を必須フィールド化し、かつ双方とも hash 計算とパースを同一バイト列
# から行う（TOCTOU 解消・`_read_bytes_and_sha256` 参照）契約を示すため
# スキーマ版を上げる。
_PINS_SCHEMA = "m3d-pairs-pins/0.3"
_REQUIRED_PINS_KEYS = {
    "schema",
    "generated_utc",
    "m2c_external_fixtures_sha256",
    "m3d_synth_specs_sha256",
    "manifest_sha256",
    "audio_sha256",
    "material",
}


class BuildM3dPairsError(RuntimeError):
    """builder 固有の fail-closed エラー（vocadito pin 不一致・check-only 不整合など）。"""


# --------------------------------------------------------------------------- #
# T3（Codex レビュー第 3 ラウンド・path traversal 拒否）: id 字句検証 +
# 生成先パスの内包検証（多層防御）
# --------------------------------------------------------------------------- #
# 許可文字集合: 英数字・アンダースコア・ハイフンのみ。実在の vocadito
# clip_id 全 40 件（`vocadito_1`〜`vocadito_40`）と、m3d_synth_specs.yaml の
# 全 fixture id（`m3d_synth_tuning_pos` 等）はいずれもこの集合に収まることを
# 確認済み。パス区切り（`/`・`\\`）・`..`・絶対パス（先頭 `/` 等）・NUL は
# いずれもこの許可文字集合の外にあるため、字句検証だけで機械的に拒否される。
_ID_LEXICAL_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


def _validate_id_lexical(value: str, *, source: str) -> None:
    """clip_id/fixture_id の字句検証（fail-closed。T3 対応）。

    `out_dir`/staging ディレクトリ配下へファイル名の一部として直接埋め込む
    前提の id（vocadito clip_id・synth fixture_id）に対する境界防御の第一層。
    第二層は生成先ファイルパスを実際に構築する箇所での内包検証
    （`_resolve_within`）——字句検証をすり抜けた場合の多層防御として独立に効く。
    """
    if not _ID_LEXICAL_PATTERN.fullmatch(value):
        raise BuildM3dPairsError(
            f"{source}: id {value!r} が許可文字集合（英数字・アンダースコア・"
            "ハイフンのみ）に違反している (fail-closed; path traversal 疑い)"
        )


def _resolve_within(path: Path, *, container: Path, label: str) -> Path:
    """`path` を resolve() し、`container` の resolve() 済みパス配下に内包
    されていることを確認して返す（fail-closed。T3 対応・多層防御の第二層）。

    clip_id/fixture_id の字句検証（`_validate_id_lexical`）は正当な id が
    `container / filename` の形で決して `container` の外を指さないことを
    構造的に保証するが、本関数は生成先ファイルパスを実際に構築する箇所でも
    独立に内包を確認する——字句検証の穴（将来の実装変更・別経路からの id 混入
    等）があってもここで検出できるようにする最後の砦。
    """
    resolved = path.resolve()
    container_resolved = container.resolve()
    try:
        resolved.relative_to(container_resolved)
    except ValueError:
        raise BuildM3dPairsError(
            f"{label}: 生成先パス {resolved} が想定ディレクトリ "
            f"{container_resolved} の外を指している (fail-closed; path traversal 疑い)"
        ) from None
    return resolved


def _write_wav_in_dir(container: Path, filename: str, y: np.ndarray, sample_rate: int) -> None:
    """`container / filename` の内包を確認してから `_atomic_write_wav` で書く
    （T3 対応。`_resolve_within` 参照）。
    """
    path = _resolve_within(container / filename, container=container, label=f"WAV 書き込み先 {filename!r}")
    _atomic_write_wav(path, y, sample_rate)


# SYNTH_* 定数（Python 側でハードコードされた id）にも m3d_synth_specs.yaml
# 由来 id と同じ字句検証を適用する（T3 対応・モジュールインポート時に一度だけ
# 検証。将来の定数編集で誤って traversal 可能な値が入っても import 時点で
# 即座に検出できる）。
_SYNTH_ID_CONSTANTS: Tuple[str, ...] = (
    SYNTH_TUNING_POS_ID,
    SYNTH_HOLDOUT_POS_ID,
    *[
        fixture_id
        for pair in (SYNTH_NEGATIVE_RHYTHM_PAIRS + SYNTH_NEGATIVE_INTERVAL_PAIRS)
        for fixture_id in pair
    ],
)
for _synth_id in _SYNTH_ID_CONSTANTS:
    _validate_id_lexical(_synth_id, source="SYNTH_* module constants")
del _synth_id


# --------------------------------------------------------------------------- #
# 小道具
# --------------------------------------------------------------------------- #
def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """`data` を `path` へ atomic に書く（`run_melody_comparison._atomic_write_text` と同型）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _atomic_write_wav(path: Path, y: np.ndarray, sample_rate: int) -> None:
    """WAV を atomic に書く（staging ファイル → os.replace。build_melody_bench と同流儀）。

    subtype は意図的に **`PCM_24`**（`build_melody_bench.py` / `make_melody_pairs.py`
    が使う `FLOAT` ではない）: libsndfile は `FLOAT`/`DOUBLE` subtype の WAV に
    `PEAK` チャンク（壁時計 Unix タイムスタンプを埋め込む）を自動付加するため、
    サンプル値が完全に同一でも書き出し時刻が秒境界をまたぐと **ファイルの bytes
    が変わる**（実測で確認 — libsndfile 由来の非決定性であり、librosa の pitch_shift/
    time_stretch 自体は決定論のまま）。本 builder は「同一入力→同一 manifest
    （バイト一致）」を要求される（実行設計・本タスクの test 要件 (e)）ため、
    PEAK チャンクを持たない整数 PCM へ切り替える。24bit は音声の実用上ほぼ
    無損失（crepe 等の下流抽出に有意な影響を与えない解像度）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp.wav", dir=str(path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        sf.write(tmp_path, y, sample_rate, subtype="PCM_24")
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _repo_rel(path: "str | Path") -> str:
    """`path` をリポジトリルート相対の POSIX スタイル文字列に正規化する。"""
    resolved = Path(path).resolve()
    return Path(os.path.relpath(resolved, ROOT)).as_posix()


class _NoDupSafeLoader(yaml.SafeLoader):
    """重複 mapping キーを拒否する SafeLoader（`run_melody_comparison.py` と同型）。"""


def _no_dup_construct_mapping(loader: "yaml.SafeLoader", node: Any, deep: bool = False) -> Dict[Any, Any]:
    mapping: Dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise BuildM3dPairsError(f"duplicate YAML mapping key {key!r} (fail-closed)")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_NoDupSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup_construct_mapping
)


def _yaml_load_no_dup_keys(data: bytes) -> Any:
    return yaml.load(data, Loader=_NoDupSafeLoader)  # noqa: S506 (dup-key 拒否付き SafeLoader)


def _pair(
    pair_id: str, kind: str, split: str, audio_a: str, audio_b: str, expected: str
) -> Dict[str, str]:
    """manifest pair dict を組む。フィールド集合はハーネスの `_REQUIRED_PAIR_KEYS`
    と厳密一致させる（多くも少なくもしない — 未知キーは fail-closed で拒否される）。
    """
    pair = {
        "pair_id": pair_id,
        "kind": kind,
        "split": split,
        "audio_a": audio_a,
        "audio_b": audio_b,
        "expected": expected,
    }
    assert set(pair) == harness._REQUIRED_PAIR_KEYS, "pair dict がハーネス必須キー集合と不一致"
    return pair


# --------------------------------------------------------------------------- #
# vocadito pin 照合（全数・fail-closed）
# --------------------------------------------------------------------------- #
def _read_bytes_and_sha256(path: "str | Path") -> Tuple[bytes, str]:
    """`path` を一度だけ読み、(bytes, sha256 hex) を返す。

    R2R 対応 N1（Codex レビュー第 2 ラウンド・build 入力 pin の TOCTOU 解消）:
    呼び出し側はこの関数が返す `data` をそのままパーサへ渡し、path を再度
    読み直してはならない——hash 計算とパースが別々の読み取りに基づくと、
    その間にファイルが差し替えられても検出できない（pin した bytes と実際に
    消費した bytes が一致する保証がなくなる）。
    """
    data = Path(path).read_bytes()
    return data, hashlib.sha256(data).hexdigest()


def _parse_m2c_fixtures(data: bytes, *, source: "str | Path") -> Dict[str, str]:
    """`m2c_external_fixtures.yaml` の生バイト列をパースする（読み取り自体は
    呼び出し側が 1 回だけ行う——`_read_bytes_and_sha256` 参照）。

    T3 対応（Codex レビュー第 3 ラウンド・path traversal 拒否）: 全 clip_id を
    fixtures ロード直後に字句検証する（`_validate_id_lexical`）——この時点で
    validated した clip_id が以降の `select_clips`/`verify_vocadito_pins`/
    `generate_vocadito_variants`/`build_pairs` 全てで再利用される唯一の
    authoritative な値のため、ロード時 1 箇所の検証で下流全てを守れる。
    """
    doc = _yaml_load_no_dup_keys(data)
    if not isinstance(doc, dict) or doc.get("schema_version") != "m2c-external-fixtures/0.1":
        raise BuildM3dPairsError(
            f"{source}: schema_version が m2c-external-fixtures/0.1 でない (fail-closed)"
        )
    fixtures = doc.get("fixtures")
    if not isinstance(fixtures, dict) or not fixtures:
        raise BuildM3dPairsError(f"{source}: 'fixtures' が非空 mapping でない (fail-closed)")
    result: Dict[str, str] = {}
    for clip_id, entry in fixtures.items():
        clip_id = str(clip_id)
        _validate_id_lexical(clip_id, source=f"{source} fixtures key")
        if not isinstance(entry, dict) or "expected_audio_sha256" not in entry:
            raise BuildM3dPairsError(
                f"{source}: fixtures[{clip_id!r}] に expected_audio_sha256 がない (fail-closed)"
            )
        result[clip_id] = str(entry["expected_audio_sha256"])
    return result


def load_m2c_fixtures(path: Path = M2C_FIXTURES_PATH) -> Tuple[Dict[str, str], str]:
    """`m2c_external_fixtures.yaml` を一度だけ読み、{clip_id: expected_audio_sha256}
    と入力ファイル自体の sha256 を返す（R2R N1・TOCTOU 解消: hash 計算とパースを
    同一バイト列から行う）。
    """
    data, digest = _read_bytes_and_sha256(path)
    return _parse_m2c_fixtures(data, source=path), digest


def _load_synth_specs(path: Path) -> Tuple[Dict[str, Any], str]:
    """`m3d_synth_specs.yaml` を一度だけ読み、specs dict と sha256 を返す。

    R2R N1（Codex レビュー第 2 ラウンド）: `build_melody_bench.load_specs` は
    内部で独自に `open(path)` するため、そのまま呼ぶと本 builder が計算する
    sha256 pin と実際にパースへ渡ったバイト列が別読みになり得る（未 pin でも
    あった）。同関数のパース処理自体は `yaml.safe_load` を素通しするだけで
    意味的な差異がないため、ここで同一バイト列に対して pin + パースを行う
    ことで代替する（`build_melody_bench.py` 自体は無改造）。

    T3 対応（Codex レビュー第 3 ラウンド・path traversal 拒否）: `specs
    ["fixtures"]` の全キー（m3d_synth_specs.yaml 由来の fixture id）も同じ
    字句検証を通す（`_validate_id_lexical`）。`SYNTH_TUNING_POS_ID` 等の
    Python 定数側は本モジュールのインポート時に別途検証済み（下記
    `_SYNTH_ID_CONSTANTS` 参照）——両方の id ソースを独立に検証する。

    R2R 対応 G1（Codex レビュー第 5 ラウンド・重複キー拒否）: パースには
    `yaml.safe_load` ではなく `_yaml_load_no_dup_keys`（`_parse_m2c_fixtures`
    と同じ `_NoDupSafeLoader`）を使う——素の `yaml.safe_load` は重複 mapping
    キーで最後の値を黙って採用するため、事前登録済み刺激（狙い撃ち
    negative の rhythm/interval spec 等）が無警告で置換されうる穴があった。
    `_NoDupSafeLoader` は DEFAULT_MAPPING_TAG に対して登録されているため、
    トップレベルの `fixtures` キー重複だけでなく、個々の fixture spec 内の
    ネストしたキー重複（例: 1 つの fixture 定義内で `kind` を 2 回書く）も
    再帰的に拒否する。
    """
    data, digest = _read_bytes_and_sha256(path)
    specs = _yaml_load_no_dup_keys(data)
    fixtures = specs.get("fixtures") if isinstance(specs, dict) else None
    if isinstance(fixtures, dict):
        for fixture_id in fixtures:
            _validate_id_lexical(str(fixture_id), source=f"{path} fixtures key")
    return specs, digest


def vocadito_audio_path(vocadito_dir: Path, clip_id: str) -> Path:
    """vocadito 配布物のレイアウト（`Audio/<clip_id>.wav`）に従い音声パスを返す。

    レイアウトは `docs/m2e_provisioning_runbook.md` §3 の実配布物確認と同一。
    """
    return Path(vocadito_dir) / "Audio" / f"{clip_id}.wav"


def verify_vocadito_pins(
    vocadito_dir: Path, fixtures: Dict[str, str], *, use_cache: bool = True
) -> Dict[str, Path]:
    """`fixtures` の全 clip の音声 sha256 を照合する。1 件でも不一致/欠落なら
    fail-closed（`BuildM3dPairsError`）で例外を送出する（部分出力を防ぐため、
    呼び出し側はこの関数が例外なく戻るまで一切の出力を書いてはならない）。

    R2R 対応 F1（Codex レビュー第 4 ラウンド・キャッシュバイパス）: `use_cache`
    は `svp_rpe.utils.hashing.file_sha256` へそのまま渡す。既定
    （`use_cache=True`）は生成開始前の初回照合向け——同一プロセス内で同じ
    vocadito 音声を複数回読む場面（実測本番の複数 route 実行等）でのキャッシュの
    恩恵は正当。一方「窓を閉じる目的の検証」（改変検出そのものが目的の再照合
    ——`check_existing`/publish 直前の再照合）は `use_cache=False` で必ず実
    バイトを読み直さなければならない: `file_sha256` は (path, size, mtime_ns)
    でキャッシュするため、既定のままだと size/mtime を保存した置換（例:
    `shutil.copystat` 相当で元の mtime を復元しつつ内容だけ差し替える改ざん）を
    見逃す——古い digest がキャッシュから返り、再照合が無効化されてしまう。
    """
    mismatches: List[str] = []
    resolved: Dict[str, Path] = {}
    for clip_id in sorted(fixtures):
        expected_sha256 = fixtures[clip_id]
        audio_path = vocadito_audio_path(vocadito_dir, clip_id)
        if not audio_path.exists():
            mismatches.append(f"{clip_id}: MISSING ({audio_path})")
            continue
        actual_sha256 = file_sha256(audio_path, use_cache=use_cache)
        if actual_sha256 != expected_sha256:
            mismatches.append(
                f"{clip_id}: sha256 mismatch (actual={actual_sha256} expected={expected_sha256})"
            )
            continue
        resolved[clip_id] = audio_path
    if mismatches:
        raise BuildM3dPairsError(
            "vocadito pin 照合に失敗 (fail-closed; 部分出力なし): " + "; ".join(mismatches)
        )
    return resolved


# --------------------------------------------------------------------------- #
# clip 選定（決定論。設計メモ §1.1）
# --------------------------------------------------------------------------- #
def select_clips(fixtures: Dict[str, str]) -> Tuple[List[str], List[str]]:
    """40 clip（以上）から tuning 12 / holdout 6 を決定論選定する。

    規則（設計メモ §1.1・実行設計 §1.1）: clip id 昇順に並べた列を、
    sha256(clip_id)（clip_id 文字列そのものの UTF-8 バイト列）の hex 表現昇順で
    並べ替え、先頭 12 を tuning・次の 6 を holdout とする。sha256 の衝突は実務上
    起きないため、事前の id 昇順整列は安定ソートの tie-break 用（意味を持つ余地は
    実質ない）。
    """
    if len(fixtures) < TUNING_COUNT + HOLDOUT_COUNT:
        raise BuildM3dPairsError(
            f"fixtures に登録された clip 数 {len(fixtures)} が tuning+holdout "
            f"({TUNING_COUNT + HOLDOUT_COUNT}) 未満 (fail-closed)"
        )
    id_ascending = sorted(fixtures)
    ranked = sorted(
        id_ascending, key=lambda clip_id: hashlib.sha256(clip_id.encode("utf-8")).hexdigest()
    )
    tuning = ranked[:TUNING_COUNT]
    holdout = ranked[TUNING_COUNT : TUNING_COUNT + HOLDOUT_COUNT]
    return tuning, holdout


def circular_pairs(clip_ids: List[str]) -> List[Tuple[str, str]]:
    """`clip_ids` を id 昇順に並べ、環状に隣接ペア (i, i+1) を作る（設計メモ §1.2）。"""
    ordered = sorted(clip_ids)
    n = len(ordered)
    if n < 2:
        return []
    return [(ordered[i], ordered[(i + 1) % n]) for i in range(n)]


# --------------------------------------------------------------------------- #
# 出力 WAV ファイル名の命名規則（単一の真実源）
# --------------------------------------------------------------------------- #
# R2R 対応 N3（Codex レビュー第 2 ラウンド・公開先衝突の拒否）: 「これから
# 生成する WAV が最終的にどのファイル名で `out_dir` へ publish されるか」を
# 実際の音声処理（librosa/build_signal）を一切行わずに算出できる必要がある
# ——衝突検査は生成開始前に終える設計のため。以下 4 つの命名ヘルパーを、
# 生成本体（`generate_*`）と衝突検査（`_expected_output_wav_filenames`）の
# **両方**が参照する単一の真実源にする（ファイル名の組み立て規則を 2 箇所に
# 複製しない）。
def _vocadito_variant_filename(clip_id: str, variant_key: str) -> str:
    return f"{clip_id}__{VOCADITO_VARIANT_LABELS[variant_key]}.wav"


def _synth_positive_base_filename(base_id: str) -> str:
    return f"{base_id}.wav"


def _synth_positive_variant_filename(base_id: str, variant_key: str) -> str:
    return f"{base_id}__{SYNTH_POS_VARIANT_LABELS[variant_key]}.wav"


def _synth_negative_filename(fixture_id: str) -> str:
    return f"{fixture_id}.wav"


def _expected_output_wav_filenames(tuning_ids: List[str], holdout_ids: List[str]) -> List[str]:
    """`generate_vocadito_variants`/`generate_synth_positive`/
    `generate_synth_negatives` が最終的に `out_dir` へ publish する WAV
    ファイル名の完全集合を、実際の音声処理を一切行わずに（命名規則のみから）
    算出する（N3 対応）。`tuning_ids`/`holdout_ids` は `select_clips` の戻り値
    （音声 I/O 不要・決定論）だけで足りる。
    """
    filenames: List[str] = []
    for clip_id in tuning_ids + holdout_ids:
        for variant_key in VOCADITO_VARIANT_ORDER:
            filenames.append(_vocadito_variant_filename(clip_id, variant_key))
    for base_id in (SYNTH_TUNING_POS_ID, SYNTH_HOLDOUT_POS_ID):
        filenames.append(_synth_positive_base_filename(base_id))
        for variant_key in SYNTH_POS_VARIANT_ORDER:
            filenames.append(_synth_positive_variant_filename(base_id, variant_key))
    for a, b in SYNTH_NEGATIVE_RHYTHM_PAIRS + SYNTH_NEGATIVE_INTERVAL_PAIRS:
        filenames.append(_synth_negative_filename(a))
        filenames.append(_synth_negative_filename(b))
    return filenames


# --------------------------------------------------------------------------- #
# N3: 公開先衝突の拒否（生成開始前・fail-closed）
# --------------------------------------------------------------------------- #
def _reject_output_input_collisions(
    *,
    out_dir: Path,
    manifest_out: Path,
    pins_out: Path,
    expected_wav_filenames: List[str],
    fixtures_path: Path,
    synth_specs_path: Path,
    vocadito_audio_paths: "List[Path]",
    summary_out: Optional[Path] = None,
) -> None:
    """公開予定の全出力先（`out_dir` 配下の生成 WAV・`manifest_out`・
    `pins_out`・（指定時）`summary_out`）の resolve() 済み絶対パス集合と、全
    build 入力（fixtures yaml・synth specs yaml・vocadito WAV 全件）の
    resolve() 済み絶対パス集合を突き合わせ、(a) 出力同士の重複 (b) 出力と
    入力の衝突、のいずれかがあれば fail-closed で拒否する（R2R N3）。

    生成（`staging_wav_dir` への物理書き込み・librosa/build_signal 呼び出し）を
    一切開始する前に呼ぶ——公開後に入力を破壊しうる誤設定（例: `--out-dir` を
    誤って vocadito 配布物のディレクトリに向ける・`--manifest-out` と
    `--pins-out` を同じパスに向ける）を、コストのかかる生成処理より前に断つ。

    R2R 対応 G2（Codex レビュー第 5 ラウンド・`--summary-out` の衝突検査
    編入）: 従来 `--summary-out` はこの preflight の対象外で、公開完了後に
    無検査で書かれていた——manifest/pins/生成 WAV/入力ファイルを指すと
    成功終了のまま上書き破壊しうる穴があった。`summary_out` を渡した場合は
    他の出力（manifest_out/pins_out/生成 WAV）と同じ扱いで衝突検査へ編入する。
    """
    outputs: Dict[Path, str] = {}

    def _register_output(path: Path, label: str) -> None:
        resolved = path.resolve()
        if resolved in outputs:
            raise BuildM3dPairsError(
                f"公開先が重複している (fail-closed): {label!r} と "
                f"{outputs[resolved]!r} が同じ解決済みパスを指す: {resolved}"
            )
        outputs[resolved] = label

    _register_output(manifest_out, "manifest_out")
    _register_output(pins_out, "pins_out")
    if summary_out is not None:
        _register_output(summary_out, "summary_out")
    for filename in expected_wav_filenames:
        _register_output(out_dir / filename, f"out_dir 配下の生成 WAV ({filename!r})")

    inputs: Dict[Path, str] = {
        fixtures_path.resolve(): f"fixtures_path ({fixtures_path})",
        synth_specs_path.resolve(): f"synth_specs_path ({synth_specs_path})",
    }
    for audio_path in vocadito_audio_paths:
        inputs.setdefault(Path(audio_path).resolve(), f"vocadito WAV ({audio_path})")

    collisions = sorted(set(outputs) & set(inputs), key=str)
    if collisions:
        details = "; ".join(
            f"{resolved}: 出力={outputs[resolved]!r} 入力={inputs[resolved]!r}"
            for resolved in collisions
        )
        raise BuildM3dPairsError(f"公開先が入力と衝突している (fail-closed): {details}")


# --------------------------------------------------------------------------- #
# 生成: vocadito 変形
# --------------------------------------------------------------------------- #
def generate_vocadito_variants(
    clip_ids: List[str], resolved_audio: Dict[str, Path], out_dir: Path, write_dir: Path
) -> Dict[str, Dict[str, Path]]:
    """選定 clip（id 昇順）ごとに 4 変形 WAV を書き出す。

    R3 対応（アトミック公開）: 物理書き込み先は `write_dir`（out_dir と同一
    ファイルシステム上の staging ディレクトリ）。戻り値の Path は **`out_dir`
    基準**（manifest の pair dict へ記録される最終公開先の参照パス。
    `_repo_rel` が `out_dir` 側の Path から相対パス文字列を作る）——staging
    と最終公開先を意図的に分離する（呼び出し側 `_publish_staged_bundle` が
    全検証成功後に一括で `write_dir/<name>` → `out_dir/<name>` へ publish する）。

    戻り値: {clip_id: {variant_key(make_variants の key): out_dir 基準 Path}}
    （base は含まない）。
    """
    result: Dict[str, Dict[str, Path]] = {}
    for clip_id in sorted(clip_ids):
        audio_path = resolved_audio[clip_id]
        y, sr = librosa.load(audio_path, sr=None, mono=True)
        variants = make_variants(
            y, sr, semitones=list(VOCADITO_SEMITONES), time_rates=list(VOCADITO_TIME_RATES)
        )
        clip_variants: Dict[str, Path] = {}
        for variant_key in VOCADITO_VARIANT_ORDER:
            filename = _vocadito_variant_filename(clip_id, variant_key)
            _write_wav_in_dir(write_dir, filename, variants[variant_key], sr)
            _resolve_within(out_dir / filename, container=out_dir, label=f"out_dir 参照先 {filename!r}")
            clip_variants[variant_key] = out_dir / filename
        result[clip_id] = clip_variants
    return result


# --------------------------------------------------------------------------- #
# 生成: 合成素材
# --------------------------------------------------------------------------- #
def generate_synth_positive(
    specs: Dict[str, Any], out_dir: Path, write_dir: Path
) -> Dict[str, Tuple[Path, Dict[str, Path]]]:
    """合成 positive base（tuning/holdout）+ その変形版を書き出す（R3: staging は
    `write_dir`、戻り値の Path は `out_dir` 基準 — `generate_vocadito_variants`
    と同じ分離）。戻り値: {base_id: (base_path, {variant_key: variant_path})}。
    """
    result: Dict[str, Tuple[Path, Dict[str, Path]]] = {}
    for base_id in (SYNTH_TUNING_POS_ID, SYNTH_HOLDOUT_POS_ID):
        y, sr = bmb.build_signal(base_id, specs)
        base_filename = _synth_positive_base_filename(base_id)
        _write_wav_in_dir(write_dir, base_filename, y, sr)
        _resolve_within(
            out_dir / base_filename, container=out_dir, label=f"out_dir 参照先 {base_filename!r}"
        )
        base_path = out_dir / base_filename
        variants = make_variants(
            y, sr, semitones=list(SYNTH_POS_SEMITONES), time_rates=list(SYNTH_POS_TIME_RATES)
        )
        variant_paths: Dict[str, Path] = {}
        for variant_key in SYNTH_POS_VARIANT_ORDER:
            filename = _synth_positive_variant_filename(base_id, variant_key)
            _write_wav_in_dir(write_dir, filename, variants[variant_key], sr)
            _resolve_within(out_dir / filename, container=out_dir, label=f"out_dir 参照先 {filename!r}")
            variant_paths[variant_key] = out_dir / filename
        result[base_id] = (base_path, variant_paths)
    return result


def generate_synth_negatives(specs: Dict[str, Any], out_dir: Path, write_dir: Path) -> Dict[str, Path]:
    """狙い撃ち negative（rhythm/interval）の合成 fixture を書き出す（R3: staging
    は `write_dir`、戻り値の Path は `out_dir` 基準）。"""
    fixture_ids: List[str] = []
    for a, b in SYNTH_NEGATIVE_RHYTHM_PAIRS + SYNTH_NEGATIVE_INTERVAL_PAIRS:
        fixture_ids.extend([a, b])
    result: Dict[str, Path] = {}
    for fixture_id in fixture_ids:
        y, sr = bmb.build_signal(fixture_id, specs)
        filename = _synth_negative_filename(fixture_id)
        _write_wav_in_dir(write_dir, filename, y, sr)
        _resolve_within(out_dir / filename, container=out_dir, label=f"out_dir 参照先 {filename!r}")
        result[fixture_id] = out_dir / filename
    return result


# --------------------------------------------------------------------------- #
# manifest 組み立て
# --------------------------------------------------------------------------- #
def build_pairs(
    *,
    tuning_ids: List[str],
    holdout_ids: List[str],
    resolved_audio: Dict[str, Path],
    vocadito_variants: Dict[str, Dict[str, Path]],
    synth_positive: Dict[str, Tuple[Path, Dict[str, Path]]],
    synth_negative: Dict[str, Path],
) -> List[Dict[str, str]]:
    pairs: List[Dict[str, str]] = []

    # positive_transform（vocadito・real_voice）: split ごとに clip id 昇順 ×
    # 変形順（VOCADITO_VARIANT_ORDER）で決定論的に列挙する。
    for split, clip_ids in (("tuning", tuning_ids), ("holdout", holdout_ids)):
        for clip_id in sorted(clip_ids):
            base_path = resolved_audio[clip_id]
            for variant_key in VOCADITO_VARIANT_ORDER:
                label = VOCADITO_VARIANT_LABELS[variant_key]
                variant_path = vocadito_variants[clip_id][variant_key]
                pairs.append(
                    _pair(
                        pair_id=f"pt_real_{split}_{clip_id}_{label}",
                        kind="positive_transform",
                        split=split,
                        audio_a=_repo_rel(base_path),
                        audio_b=_repo_rel(variant_path),
                        expected="same",
                    )
                )

    # negative_cross（vocadito・real_voice）: tuning 12 対 / holdout 6 対
    for split, clip_ids in (("tuning", tuning_ids), ("holdout", holdout_ids)):
        for idx, (a, b) in enumerate(circular_pairs(clip_ids)):
            pairs.append(
                _pair(
                    pair_id=f"nc_real_{split}_{idx:02d}_{a}_{b}",
                    kind="negative_cross",
                    split=split,
                    audio_a=_repo_rel(resolved_audio[a]),
                    audio_b=_repo_rel(resolved_audio[b]),
                    expected="different",
                )
            )

    # positive_transform（合成・synthetic）: tuning 2 対 + holdout 2 対
    for split, base_id in (("tuning", SYNTH_TUNING_POS_ID), ("holdout", SYNTH_HOLDOUT_POS_ID)):
        base_path, variant_paths = synth_positive[base_id]
        for variant_key in SYNTH_POS_VARIANT_ORDER:
            label = SYNTH_POS_VARIANT_LABELS[variant_key]
            pairs.append(
                _pair(
                    pair_id=f"pt_synth_{split}_{base_id}_{label}",
                    kind="positive_transform",
                    split=split,
                    audio_a=_repo_rel(base_path),
                    audio_b=_repo_rel(variant_paths[variant_key]),
                    expected="same",
                )
            )

    # negative_rhythm（合成・tuning のみ・2 対）
    for idx, (a, b) in enumerate(SYNTH_NEGATIVE_RHYTHM_PAIRS):
        pairs.append(
            _pair(
                pair_id=f"nr_synth_tuning_{idx:02d}_{a}_{b}",
                kind="negative_rhythm",
                split="tuning",
                audio_a=_repo_rel(synth_negative[a]),
                audio_b=_repo_rel(synth_negative[b]),
                expected="different",
            )
        )

    # negative_interval（合成・tuning のみ・2 対）
    for idx, (a, b) in enumerate(SYNTH_NEGATIVE_INTERVAL_PAIRS):
        pairs.append(
            _pair(
                pair_id=f"ni_synth_tuning_{idx:02d}_{a}_{b}",
                kind="negative_interval",
                split="tuning",
                audio_a=_repo_rel(synth_negative[a]),
                audio_b=_repo_rel(synth_negative[b]),
                expected="different",
            )
        )

    return pairs


def _material_of(pair_id: str) -> str:
    if "_real_" in pair_id:
        return "real_voice"
    if "_synth_" in pair_id:
        return "synthetic"
    raise BuildM3dPairsError(f"pair_id {pair_id!r} から material 区分を判別できない")


def _expected_material_map(pairs: List[Dict[str, str]]) -> Dict[str, str]:
    """`pairs`（ハーネス検証済み manifest）から audio path → material の期待値を
    決定論的に再計算する（R2R 対応 H1・Codex レビュー第 6 ラウンド）。

    manifest の pair_id 命名規則（`_real_`/`_synth_`）が material 区分の唯一の
    authoritative な源であり、pins サイドカーの `material` フィールドはこの値の
    単なるキャッシュに過ぎない——`check_existing` は sidecar 側の記録値を無条件に
    信頼せず、本関数で manifest から独立に再計算した期待値と突き合わせる。同一
    audio path が複数 pair から参照される場合（例: circular pairs の隣接共有・
    positive_transform の base 共有）は先勝ちで採用する（`run_and_publish` の
    従来ループと同じ規約——正当なデータでは同一 path が real/synth 混在で参照
    されることはない）。
    """
    material: Dict[str, str] = {}
    for pair in pairs:
        pair_material = _material_of(pair["pair_id"])
        for key in ("audio_a", "audio_b"):
            rel = pair[key]
            material.setdefault(rel, pair_material)
    return material


def crosstab(pairs: List[Dict[str, str]]) -> Dict[str, Dict[str, int]]:
    """kind × split の対数集計（`material` 内訳つき）を返す。"""
    counts: "Counter[Tuple[str, str]]" = Counter()
    material_counts: "Counter[Tuple[str, str]]" = Counter()
    for pair in pairs:
        counts[(pair["kind"], pair["split"])] += 1
        material_counts[(pair["kind"], _material_of(pair["pair_id"]))] += 1
    table: Dict[str, Dict[str, int]] = {}
    for (kind, split), n in sorted(counts.items()):
        table.setdefault(kind, {})[split] = n
    material_table: Dict[str, Dict[str, int]] = {}
    for (kind, material), n in sorted(material_counts.items()):
        material_table.setdefault(kind, {})[material] = n
    return {"by_kind_split": table, "by_kind_material": material_table, "total": len(pairs)}


def all_audio_paths(pairs: List[Dict[str, str]]) -> List[str]:
    paths: List[str] = []
    seen: set = set()
    for pair in pairs:
        for key in ("audio_a", "audio_b"):
            rel = pair[key]
            if rel not in seen:
                seen.add(rel)
                paths.append(rel)
    return sorted(paths)


# --------------------------------------------------------------------------- #
# R3: staging → atomic bundle publish
# --------------------------------------------------------------------------- #
def _publish_staged_bundle(entries: "List[Tuple[Path, Path]]") -> None:
    """staging 済みファイル一式を最終位置へ atomic に一括公開する（snapshot +
    rollback 付き）。`entries` は (staged_path, final_path) の列。

    `svp_rpe.utils.atomic_io.atomic_publish_bundle` と同じ流儀（全部揃って初めて
    意味を持つ 1 組として snapshot → publish → 失敗時ロールバック、各 replace の
    **前**に snapshot/published を登録する非同期シグナル安全策も同型）を踏襲
    するが、同関数は単一 `output_dir` 直下のフラットなファイル名のみを契約とする
    のに対し、本 builder の生成物（変形 WAV は `out_dir`、manifest は
    `tests/fixtures/melody_bench/`、pins サイドカーは `build/external_m3d/` と、
    互いに異なるディレクトリへ publish する必要があるため、任意の
    (staged_path, final_path) ペア列を受ける本関数を用意する（既存関数の
    無改造のまま再利用できないための意図的な複製 — ロールバック挙動は同型）。

    途中で例外が飛べば、既に publish 済みの分を削除し、退避済みの既存ファイルを
    元位置へ復元してから re-raise する——既存の公開済みセットは初回ビルドだけで
    なく **再ビルド時にも** 無傷で残る（R3 対応）。

    R2R 対応 N2（Codex レビュー第 2 ラウンド・成功時 .prev 残置）: publish
    ループが例外なく完走した場合、退避しておいた `.prev` snapshot はもう不要
    ——削除せずに残すと出力ディレクトリにビルドのたびゴミが蓄積する。全件の
    publish が成功した後（rollback 経路には影響しない — 例外時は従来どおり
    snapshot を使って復元する）、ベストエフォートで削除する。

    R2R 対応 F2（Codex レビュー第 4 ラウンド・未初期化 snapshot での上書き
    防止）: `tempfile.mkstemp` は呼んだ瞬間に空ファイルを作る——`snapshots`
    への登録は（非同期シグナル安全のため）その直後・実際の退避 rename
    （`os.replace(final_path, snapshot_path)`）の**前**に行われる。この
    rename 自体が失敗/中断（`KeyboardInterrupt` 含む）した場合、`final_path`
    は退避されておらず元の内容のまま存在し続けるが、`snapshots` にはこの
    「中身が空の mkstemp placeholder」がまだ登録されている。rollback が
    これを無条件に「有効な退避物」として `final_path` へ復元すると、無傷の
    destination を空ファイルで上書き（切り詰め）してしまう。rename は
    atomic（同一ファイルシステム上の単一 rename syscall）であるため、
    「rename が実際に完了したか」は Python 側のフラグではなく **rollback
    実行時点の `final_path` の存在有無**で判定できる（後述）: 先行する
    `published` の unlink ループが完了した後、`final_path` が依然として
    存在するなら退避 rename は一度も成功していない（destination は無傷）
    ——placeholder を削除するだけに留め、復元は行わない。`final_path` が
    存在しない（unlink で消えた、または退避 rename 自体が成功していた）なら
    snapshot からの復元が正当。
    """
    snapshots: Dict[Path, Path] = {}
    published: List[Path] = []
    try:
        for staged_path, final_path in entries:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            if final_path.exists():
                fd, snap_name = tempfile.mkstemp(
                    prefix=f".{final_path.name}.", suffix=".prev", dir=str(final_path.parent)
                )
                os.close(fd)
                snapshot_path = Path(snap_name)
                # 登録は replace の**前**（Codex P2 review 由来の流儀。非同期
                # シグナルが replace 完了直後〜登録実行前に割り込んでも追跡漏れ
                # が生じない）。rename が実際に完了したかどうかは rollback 側で
                # `final_path.exists()` を見て判定する（F2 参照・上記 docstring）。
                snapshots[final_path] = snapshot_path
                os.replace(final_path, snapshot_path)
            published.append(final_path)
            os.replace(staged_path, final_path)
    except BaseException:
        for final_path in published:
            try:
                os.unlink(final_path)
            except OSError:
                pass
        for final_path, snapshot_path in snapshots.items():
            if final_path.exists():
                # F2: 退避 rename が完了していない（destination は無傷のまま
                # 残っている）——mkstemp が作った空 placeholder を復元対象として
                # 使ってはならない。placeholder のみ掃除する。
                try:
                    snapshot_path.unlink()
                except OSError:
                    pass
                continue
            try:
                os.replace(snapshot_path, final_path)
            except OSError:
                pass
        raise
    else:
        # N2: publish 全体が成功した——退避済み snapshot はもう不要（削除失敗は
        # publish 自体の成功を無効化しない・ベストエフォート）。
        for snapshot_path in snapshots.values():
            try:
                snapshot_path.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# pins サイドカーの読み込み + 検証（R1: --check-only digest 照合 fail-closed 化）
# --------------------------------------------------------------------------- #
def _load_and_validate_pins(path: Path) -> Dict[str, Any]:
    """pins サイドカーを読み、スキーマ・構造を検証する（fail-closed）。

    限界の明記: 本サイドカー（`build/external_m3d/m3d_pairs_pins.json`）は
    **非コミットのビルド生成物**であり、順序証明の最終根拠は git 履歴 +
    ハーネスの holdout ロックのままである。本照合（`--check-only`）は
    「manifest/WAV がビルド後に事故的に改変されていないか」を fail-closed で
    検出する計器であって、順序証明そのものの代替ではない。
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BuildM3dPairsError(
            f"{path}: pins サイドカーの読み込みに失敗 (fail-closed): {exc}"
        ) from exc
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BuildM3dPairsError(
            f"{path}: pins サイドカーの JSON parse に失敗 (fail-closed): {exc}"
        ) from exc
    if not isinstance(doc, dict) or doc.get("schema") != _PINS_SCHEMA:
        raise BuildM3dPairsError(
            f"{path}: schema が {_PINS_SCHEMA!r} でない (fail-closed): "
            f"{doc.get('schema') if isinstance(doc, dict) else doc!r}"
        )
    missing = _REQUIRED_PINS_KEYS - set(doc)
    if missing:
        raise BuildM3dPairsError(f"{path}: 必須キー欠落 (fail-closed): {sorted(missing)}")
    for sha_field in (
        "manifest_sha256",
        "m2c_external_fixtures_sha256",
        "m3d_synth_specs_sha256",
    ):
        sha_value = doc[sha_field]
        if not isinstance(sha_value, str) or len(sha_value) != 64:
            raise BuildM3dPairsError(
                f"{path}: {sha_field} が不正な形式 (fail-closed): {sha_value!r}"
            )
    if not isinstance(doc.get("audio_sha256"), dict):
        raise BuildM3dPairsError(f"{path}: 'audio_sha256' が mapping でない (fail-closed)")
    if not isinstance(doc.get("material"), dict):
        raise BuildM3dPairsError(f"{path}: 'material' が mapping でない (fail-closed)")

    # R2R 対応 H2（Codex レビュー第 6 ラウンド・summary の pin 化）: `summary_path`/
    # `summary_sha256` は `--summary-out` 使用時のみ書かれる **optional** な
    # フィールドペアである（`_REQUIRED_PINS_KEYS` には含めない——summary 非使用
    # ビルドの既存 pins サイドカーが引き続き 0.3 のまま有効であるべきため、
    # スキーマ version は bump しない。必須フィールド集合・既存フィールドの
    # 意味論はいずれも変わらない純粋な追加的緩和のため、0.3 の後方互換規約
    # （必須キー変更時のみ bump）に合致する）。ただし存在する場合は両方揃って
    # いなければならない（片方のみの記録は sidecar 自体の破損を意味する）。
    has_summary_path = "summary_path" in doc
    has_summary_sha = "summary_sha256" in doc
    if has_summary_path != has_summary_sha:
        raise BuildM3dPairsError(
            f"{path}: 'summary_path' と 'summary_sha256' は両方存在するか両方欠落かの "
            "いずれかでなければならない (fail-closed; 片方のみの記録は sidecar 破損)"
        )
    if has_summary_sha:
        summary_sha_value = doc["summary_sha256"]
        if not isinstance(summary_sha_value, str) or len(summary_sha_value) != 64:
            raise BuildM3dPairsError(
                f"{path}: 'summary_sha256' が不正な形式 (fail-closed): {summary_sha_value!r}"
            )
        summary_path_value = doc["summary_path"]
        if not isinstance(summary_path_value, str) or not summary_path_value:
            raise BuildM3dPairsError(
                f"{path}: 'summary_path' が不正な形式 (fail-closed): {summary_path_value!r}"
            )
    return doc


# --------------------------------------------------------------------------- #
# check-only
# --------------------------------------------------------------------------- #
def check_existing(
    *,
    manifest_out: Path,
    pins_out: Path,
    vocadito_dir: Path,
    fixtures_path: Path,
    synth_specs_path: Path,
    summary_out: Optional[Path] = None,
) -> Dict[str, Any]:
    """既存の manifest + pins サイドカーが現在のファイル内容と整合するかだけを
    再照合する（生成は一切行わない）。1 件でも不整合なら `BuildM3dPairsError`。

    R1 対応（Codex レビュー・digest 照合 fail-closed 化）:
    (a) sidecar のスキーマ検証（`_load_and_validate_pins`）
    (b) sidecar 記録の manifest sha256 と現物 manifest ファイルのバイト sha256
        の一致検証（従来は記録するのみで再照合していなかった）
    (c) 全 WAV pin の一致検証（従来どおり）
    のいずれか 1 件でも不一致/欠落で fail-closed。

    R2R 対応 N1（Codex レビュー第 2 ラウンド・build 入力 pin の完全化）:
    (d) sidecar 記録の m2c_external_fixtures.yaml / m3d_synth_specs.yaml の
        sha256 と現物ファイルのバイト sha256 の一致検証も同様に fail-closed で
        行う（従来は fixtures 側のみ記録し、synth specs は無 pin だった）。

    `summary_out`（R2R G2・Codex レビュー第 5 ラウンド）: 指定時は manifest の
    整合性検証が終わった後（＝ `pairs` が読み取り確定した後）、`--summary-out`
    が manifest/pins/build 入力/vocadito 原音・既公開 WAV のいずれとも衝突
    しないことを `_reject_output_input_collisions` で確認してから呼び出し側
    （`main()`）へ返す——`run_and_publish` 経路と異なり `check_existing` は
    生成を一切行わない読み取り専用経路のため、`_publish_staged_bundle` の
    ような atomic bundle には乗せず、`main()` が本関数の戻り値を使って単独の
    `_atomic_write_bytes` で書く（読み取り専用経路に新たに publish 状態を
    持ち込まない設計判定 — 実装詳細は `main()` docstring 相当のコメント参照）。

    R2R 対応 H1（Codex レビュー第 6 ラウンド・material map の内容検証）:
    (e) sidecar 記録の `material` マップ（audio path → real_voice/synthetic）を
        検証済み manifest から独立に再計算した期待値（`_expected_material_map`）
        と**完全一致**（キー集合・値とも）で照合する——従来は `material` が
        mapping であることしか見ておらず、real_voice→synthetic への書換・
        エントリ削除・stale エントリ追加のいずれも `--check-only: OK` で
        素通りしていた。

    R2R 対応 H2（Codex レビュー第 6 ラウンド・summary の pin 化）:
    (f) sidecar に summary の記録（`summary_path`/`summary_sha256`）がある
        場合、記録された path の現物とバイト sha256 を fail-closed で照合する
        （記録があるのに現物欠落も fail）。記録が無い場合（summary 非使用
        ビルド）は検証をスキップする。この検証は今回の呼び出しに渡された
        `summary_out` 引数の有無とは独立——sidecar が過去に記録した summary の
        整合性そのものを守るための照合であり、今回の書込み対象とは別の話。
        --check-only は pins.json を一切書き換えない（read-only 原則を維持
        ——summary pin の記録は `run_and_publish` 経路でのみ行う）。
    """
    fixtures, actual_fixtures_sha = load_m2c_fixtures(fixtures_path)
    # F1 対応: --check-only は改変検出そのものが目的の照合のため、キャッシュを
    # バイパスして実バイトを読み直す（vocadito 側の drift も再確認）。
    verify_vocadito_pins(vocadito_dir, fixtures, use_cache=False)
    _, actual_synth_specs_sha = _read_bytes_and_sha256(synth_specs_path)

    if not manifest_out.exists():
        raise BuildM3dPairsError(f"{manifest_out}: manifest が存在しない (fail-closed; --check-only)")
    if not pins_out.exists():
        raise BuildM3dPairsError(f"{pins_out}: pins サイドカーが存在しない (fail-closed; --check-only)")

    pins_doc = _load_and_validate_pins(pins_out)

    manifest_bytes = manifest_out.read_bytes()
    actual_manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    expected_manifest_sha = pins_doc["manifest_sha256"]

    problems: List[str] = []
    if actual_manifest_sha != expected_manifest_sha:
        problems.append(
            f"{manifest_out}: manifest sha256 mismatch "
            f"(actual={actual_manifest_sha} expected={expected_manifest_sha})"
        )
    expected_fixtures_sha = pins_doc["m2c_external_fixtures_sha256"]
    if actual_fixtures_sha != expected_fixtures_sha:
        problems.append(
            f"{fixtures_path}: m2c_external_fixtures sha256 mismatch "
            f"(actual={actual_fixtures_sha} expected={expected_fixtures_sha})"
        )
    expected_synth_specs_sha = pins_doc["m3d_synth_specs_sha256"]
    if actual_synth_specs_sha != expected_synth_specs_sha:
        problems.append(
            f"{synth_specs_path}: m3d_synth_specs sha256 mismatch "
            f"(actual={actual_synth_specs_sha} expected={expected_synth_specs_sha})"
        )

    # R2R 対応 H2（Codex レビュー第 6 ラウンド・summary の pin 化）: sidecar に
    # summary の記録がある場合（＝過去に `--summary-out` 付きでビルドされた場合）、
    # 記録された path（repo-relative）の現物とバイト sha256 を fail-closed で
    # 照合する。記録があるのに現物が存在しない場合も fail（H2 要件どおり）。
    # 記録が無い場合（summary 非使用ビルド）は検証をスキップする——本チェックは
    # 常に実行し、今回の呼び出しに渡された `summary_out` 引数の有無には依存
    # させない（sidecar が記録している過去の summary の整合性そのものを守る
    # ため、今回そのファイルを書くかどうかとは独立した話）。
    if "summary_sha256" in pins_doc:
        recorded_summary_path = pins_doc.get("summary_path")
        expected_summary_sha = pins_doc["summary_sha256"]
        abs_summary_path = ROOT / recorded_summary_path if recorded_summary_path else None
        if abs_summary_path is None or not abs_summary_path.exists():
            problems.append(
                f"{recorded_summary_path}: pins サイドカーに記録された summary "
                "ファイルが存在しない (fail-closed)"
            )
        else:
            actual_summary_sha = file_sha256(abs_summary_path, use_cache=False)
            if actual_summary_sha != expected_summary_sha:
                problems.append(
                    f"{recorded_summary_path}: summary sha256 mismatch "
                    f"(actual={actual_summary_sha} expected={expected_summary_sha})"
                )

    manifest_doc = _yaml_load_no_dup_keys(manifest_bytes)
    pairs = harness._validate_manifest(manifest_doc)
    audio_sha256 = pins_doc.get("audio_sha256", {})
    for rel_path in all_audio_paths(pairs):
        abs_path = ROOT / rel_path
        expected = audio_sha256.get(rel_path)
        if expected is None:
            problems.append(f"{rel_path}: pins サイドカーに未登録")
            continue
        if not abs_path.exists():
            problems.append(f"{rel_path}: ファイルが存在しない")
            continue
        actual = file_sha256(abs_path, use_cache=False)
        if actual != expected:
            problems.append(f"{rel_path}: sha256 mismatch (actual={actual} expected={expected})")

    # R2R 対応 H1（Codex レビュー第 6 ラウンド・material map の内容検証）:
    # 従来 `_load_and_validate_pins` は `material` が mapping であることしか
    # 見ておらず、`check_existing` は中身（キー/値）を一切突合していなかった
    # ——real_voice→synthetic への書換・エントリ削除・stale エントリ追加が
    # すべて `--check-only: OK` で素通りする穴があった。検証済み manifest
    # （`pairs`）から `_expected_material_map` で期待値を再計算し、sidecar
    # 記録との**完全一致**（キー集合・値とも）を要求する。
    expected_material = _expected_material_map(pairs)
    recorded_material = pins_doc.get("material", {})
    missing_material = sorted(set(expected_material) - set(recorded_material))
    if missing_material:
        problems.append(f"material マップに欠落エントリ (fail-closed): {missing_material}")
    extra_material = sorted(set(recorded_material) - set(expected_material))
    if extra_material:
        problems.append(f"material マップに余剰エントリ (fail-closed; stale): {extra_material}")
    mismatched_material = sorted(
        rel
        for rel in set(expected_material) & set(recorded_material)
        if expected_material[rel] != recorded_material[rel]
    )
    if mismatched_material:
        detail = "; ".join(
            f"{rel}: expected(manifest 由来)={expected_material[rel]!r} "
            f"recorded(sidecar)={recorded_material[rel]!r}"
            for rel in mismatched_material
        )
        problems.append(f"material マップ不一致 (fail-closed): {detail}")

    if problems:
        raise BuildM3dPairsError(
            "--check-only 整合性チェック失敗 (fail-closed): " + "; ".join(problems)
        )

    if summary_out is not None:
        # G2: --check-only 経路でも --summary-out が manifest/pins/build 入力/
        # vocadito 原音・既公開 WAV のいずれとも衝突しないことを、実際に書く前
        # に確認する（`out_dir` は本経路では新規生成しないため空集合、既公開
        # WAV は `all_audio_paths(pairs)` から repo-relative パスを解決して
        # `vocadito_audio_paths` 相当の保護対象へ折り込む）。
        published_audio_paths = [ROOT / rel_path for rel_path in all_audio_paths(pairs)]
        _reject_output_input_collisions(
            out_dir=manifest_out.parent,
            manifest_out=manifest_out,
            pins_out=pins_out,
            expected_wav_filenames=[],
            fixtures_path=fixtures_path,
            synth_specs_path=synth_specs_path,
            vocadito_audio_paths=published_audio_paths,
            summary_out=summary_out,
        )

    return crosstab(pairs)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def run_build(
    *,
    vocadito_dir: Path,
    out_dir: Path,
    manifest_out: Path,
    pins_out: Path,
    staging_wav_dir: Path,
    fixtures_path: Path,
    synth_specs_path: Path,
    summary_out: Optional[Path] = None,
) -> Tuple[List[Dict[str, str]], Dict[str, Path], Dict[str, str], Dict[str, str]]:
    """公開先衝突検査 → pin 照合 → 選定 → 生成（`staging_wav_dir` への物理
    書き込み）→ manifest 組み立てまでを行う。書き込みは `staging_wav_dir` 配下
    のみ（`out_dir` 自体へは一切書き込まない — R3 対応: 最終公開は呼び出し側の
    `_publish_staged_bundle` が全検証成功後に一括で行う）。

    戻り値: `(pairs, audio_source_lookup, build_input_sha256, fixtures)`。
    `audio_source_lookup` は manifest に記録される repo-relative パス文字列 →
    実バイトを読める絶対 Path（生成物は staging 側、vocadito 原音は既存の
    入力そのもの）の対応表——pins サイドカーの sha256 計算がまだ publish 前の
    staging から読めるようにする。`build_input_sha256` は
    `{"m2c_external_fixtures_sha256": ..., "m3d_synth_specs_sha256": ...}`
    （R2R N1・TOCTOU 解消: いずれもパースに使ったのと同一バイト列から計算済み）。
    `fixtures` は {clip_id: expected_audio_sha256}——呼び出し側が T2 対応
    （publish 直前の vocadito バイト再照合）で `verify_vocadito_pins` に
    再度渡すために返す（fixtures_path を再読み込みしない——ロード済みの
    同一 dict を再利用する）。

    `summary_out`（R2R G2・Codex レビュー第 5 ラウンド）: 指定時は他の公開先
    （manifest_out/pins_out/生成 WAV）と同じ扱いで `_reject_output_input_
    collisions` の衝突検査に編入する——`--summary-out` が manifest/pins/生成
    WAV/入力ファイルを指す誤設定を、生成開始前に fail-closed で拒否する。
    """
    fixtures, fixtures_sha256 = load_m2c_fixtures(fixtures_path)
    resolved_audio = verify_vocadito_pins(vocadito_dir, fixtures)  # fail-closed gate（全数）

    tuning_ids, holdout_ids = select_clips(fixtures)

    # R2R 対応 N3（Codex レビュー第 2 ラウンド・公開先衝突の拒否）: 生成
    # （staging への物理書き込み・librosa/build_signal 呼び出し）を開始する前に、
    # 公開予定の全出力先が入力および互いと衝突しないことを確認する。
    _reject_output_input_collisions(
        out_dir=out_dir,
        manifest_out=manifest_out,
        pins_out=pins_out,
        expected_wav_filenames=_expected_output_wav_filenames(tuning_ids, holdout_ids),
        fixtures_path=fixtures_path,
        synth_specs_path=synth_specs_path,
        vocadito_audio_paths=list(resolved_audio.values()),
        summary_out=summary_out,
    )

    specs, synth_specs_sha256 = _load_synth_specs(synth_specs_path)

    staging_wav_dir.mkdir(parents=True, exist_ok=True)
    vocadito_variants = generate_vocadito_variants(
        tuning_ids + holdout_ids, resolved_audio, out_dir, staging_wav_dir
    )

    synth_positive = generate_synth_positive(specs, out_dir, staging_wav_dir)
    synth_negative = generate_synth_negatives(specs, out_dir, staging_wav_dir)

    pairs = build_pairs(
        tuning_ids=tuning_ids,
        holdout_ids=holdout_ids,
        resolved_audio=resolved_audio,
        vocadito_variants=vocadito_variants,
        synth_positive=synth_positive,
        synth_negative=synth_negative,
    )

    # 生成物が実際にハーネスのスキーマ検証を通ることを自前でも確認する（防御的
    # 二重チェック——スキーマの権威は常にハーネス側のコード）。
    manifest_doc = {"schema": _MANIFEST_SCHEMA, "pairs": pairs}
    harness._validate_manifest(manifest_doc)

    audio_source_lookup: Dict[str, Path] = {}
    for clip_id, path in resolved_audio.items():
        audio_source_lookup[_repo_rel(path)] = path
    for clip_id, variants in vocadito_variants.items():
        for variant_key, final_path in variants.items():
            audio_source_lookup[_repo_rel(final_path)] = staging_wav_dir / final_path.name
    for base_id, (base_path, variant_paths) in synth_positive.items():
        audio_source_lookup[_repo_rel(base_path)] = staging_wav_dir / base_path.name
        for variant_key, variant_path in variant_paths.items():
            audio_source_lookup[_repo_rel(variant_path)] = staging_wav_dir / variant_path.name
    for fixture_id, path in synth_negative.items():
        audio_source_lookup[_repo_rel(path)] = staging_wav_dir / path.name

    build_input_sha256 = {
        "m2c_external_fixtures_sha256": fixtures_sha256,
        "m3d_synth_specs_sha256": synth_specs_sha256,
    }
    return pairs, audio_source_lookup, build_input_sha256, fixtures


def run_and_publish(
    *,
    vocadito_dir: Path,
    out_dir: Path,
    manifest_out: Path,
    pins_out: Path,
    fixtures_path: Path,
    synth_specs_path: Path,
    summary_out: Optional[Path] = None,
) -> Dict[str, Any]:
    """`run_build` を staging ディレクトリ（`out_dir` と同一ファイルシステム上）
    で走らせ、manifest + pins サイドカーを組み立てて全検証成功後に
    `_publish_staged_bundle` で一括公開する（R3 対応）。

    途中で例外が飛んだ場合、staging ディレクトリを破棄するのみで既存の
    公開済みセット（`out_dir` の WAV 一式・`manifest_out`・`pins_out`）には
    一切触れない——初回ビルドだけでなく **再ビルド時** にも「部分出力なし」の
    保証を拡張する。

    T2 対応（Codex レビュー第 3 ラウンド・publish 直前の vocadito バイト
    再照合）: 音声読込経路（`librosa.load`。`generate_vocadito_variants` 内）は
    無改造のまま、staging が完成し公開を開始する直前に全 vocadito 入力 WAV を
    再度 `verify_vocadito_pins` で照合する。境界宣言の更新（前ラウンドの
    残存懸念の解消）: 「pin 照合〜`librosa.load` 読込の間の窓でファイルが
    差し替えられた場合、未承認バイト由来の変形 WAV が生成されうる」経路自体は
    引き続き存在するが（読込経路は変えない設計判定）、**公開はされない**——
    本工程が生成後・公開前に同じ pin で全数を再照合し、1 件でも不一致なら
    fail-closed で公開自体を中止する（staging は破棄・既公開セットは無傷）。

    `summary_out`（R2R G2・Codex レビュー第 5 ラウンド）: 指定時は
    `run_build` の衝突検査へ編入した上で、crosstab summary の JSON を
    manifest/pins/生成 WAV と**同じ atomic bundle**（`_publish_staged_bundle`）
    で公開する——`_publish_staged_bundle` は既に「全部揃って初めて意味を持つ
    1 組」を staging → 一括公開する構造を持つため、summary もここへ 1 エント
    リ追加するのが最も自然（単独の別 atomic write を追加すると、manifest/pins
    は公開済みだが summary だけ失敗する部分成功状態を新たに作ってしまう）。

    R2R 対応 H2（Codex レビュー第 6 ラウンド・summary の pin 化）: `summary_out`
    指定時は pins_doc へ `summary_path`（repo-relative）/`summary_sha256` を
    optional フィールドとして記録する——従来は summary がアトミックバンドルの
    一員として公開されるだけで pin されておらず、公開後の事後編集が
    `--check-only` に不可視だった。記録は必須フィールドの追加ではなく既存
    フィールドの意味論変更も伴わない純粋な追加的緩和のため、スキーマ version
    は `m3d-pairs-pins/0.3` のまま bump しない（summary 非使用ビルドの既存
    sidecar は引き続き有効）。
    """
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(
        tempfile.mkdtemp(prefix=f".{out_dir.name}.staging_", dir=str(out_dir.parent))
    )
    try:
        staging_wav_dir = staging_root / "wav"
        pairs, audio_source_lookup, build_input_sha256, fixtures = run_build(
            vocadito_dir=vocadito_dir,
            out_dir=out_dir,
            manifest_out=manifest_out,
            pins_out=pins_out,
            staging_wav_dir=staging_wav_dir,
            fixtures_path=fixtures_path,
            synth_specs_path=synth_specs_path,
            summary_out=summary_out,
        )

        manifest_doc = {"schema": _MANIFEST_SCHEMA, "pairs": pairs}
        manifest_yaml = yaml.safe_dump(manifest_doc, sort_keys=False, allow_unicode=True).encode(
            "utf-8"
        )
        staged_manifest = staging_root / "manifest.yaml"
        _atomic_write_bytes(staged_manifest, manifest_yaml)

        audio_sha256: Dict[str, str] = {}
        for pair in pairs:
            for key in ("audio_a", "audio_b"):
                rel = pair[key]
                if rel not in audio_sha256:
                    src = audio_source_lookup[rel]
                    audio_sha256[rel] = file_sha256(src, use_cache=False)
        # R2R 対応 H1（Codex レビュー第 6 ラウンド）: material map は
        # `_expected_material_map`（`check_existing` の再計算と同じ関数）から
        # 導出する——ビルド時点の記録経路とチェック時点の再計算経路が同じ
        # 単一の真実源を通ることを保証する（2 箇所に規約を複製しない）。
        material = _expected_material_map(pairs)

        # R2R 対応 G2（Codex レビュー第 5 ラウンド）: summary を manifest/pins
        # と同じ atomic bundle へ 1 エントリとして編入する（`run_and_publish`
        # docstring 参照 — 独立の別 atomic write にすると部分成功状態を新たに
        # 作ってしまうため）。summary の中身は既に組み立て済みの `pairs` から
        # 決定論的に導出できるため、公開前のこの時点で計算して staging へ書く。
        # R2R 対応 H2（Codex レビュー第 6 ラウンド・summary の pin 化）: summary
        # バイト列は pins_doc 組み立てより前に確定させる必要がある——pins_doc
        # へ summary の path/digest を optional フィールドとして記録するため。
        summary = crosstab(pairs)
        staged_summary: Optional[Path] = None
        summary_pin_fields: Dict[str, str] = {}
        if summary_out is not None:
            summary_bytes = json.dumps(summary, indent=2, sort_keys=True).encode("utf-8")
            staged_summary = staging_root / "summary.json"
            _atomic_write_bytes(staged_summary, summary_bytes)
            summary_pin_fields = {
                "summary_path": _repo_rel(summary_out),
                "summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
            }

        pins_doc = {
            "schema": _PINS_SCHEMA,
            "generated_utc": _utc_now(),
            **build_input_sha256,
            "manifest_sha256": hashlib.sha256(manifest_yaml).hexdigest(),
            "audio_sha256": dict(sorted(audio_sha256.items())),
            "material": dict(sorted(material.items())),
            **summary_pin_fields,
        }
        staged_pins = staging_root / "pins.json"
        _atomic_write_bytes(
            staged_pins, json.dumps(pins_doc, indent=2, sort_keys=True).encode("utf-8")
        )

        entries: List[Tuple[Path, Path]] = [
            (wav_path, out_dir / wav_path.name)
            for wav_path in sorted(staging_wav_dir.glob("*.wav"))
        ]
        entries.append((staged_manifest, manifest_out))
        entries.append((staged_pins, pins_out))
        if staged_summary is not None:
            entries.append((staged_summary, summary_out))

        # T2 対応（Codex レビュー第 3 ラウンド）: staging が完成した = これ以降
        # 生成処理（librosa.load 等）は一切行わない時点で、全 vocadito 入力 WAV
        # を pin と再照合する。ここで 1 件でも不一致なら公開せず fail-closed
        # （staging は finally で破棄・既公開セットは無傷のまま）。
        # F1 対応（Codex レビュー第 4 ラウンド）: 「窓を閉じる目的の検証」その
        # ものであるため `use_cache=False` で必ず実バイトを読み直す——既定の
        # キャッシュ有効のままでは、生成開始前の初回照合（`run_build` 内。
        # size/mtime をキーに記録済み）と size/mtime が偶然一致する置換
        # （元の mtime を復元しつつ内容だけ差し替える改ざん）を見逃し、この
        # 再照合工程自体が無効化されてしまう。
        verify_vocadito_pins(vocadito_dir, fixtures, use_cache=False)

        _publish_staged_bundle(entries)
        return summary
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vocadito-dir", type=Path, default=DEFAULT_VOCADITO_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--manifest-out", type=Path, default=DEFAULT_MANIFEST_OUT)
    parser.add_argument("--pins-out", type=Path, default=DEFAULT_PINS_OUT)
    parser.add_argument("--fixtures", type=Path, default=M2C_FIXTURES_PATH)
    parser.add_argument("--synth-specs", type=Path, default=M3D_SYNTH_SPECS_PATH)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="生成済み manifest/WAV/pins サイドカーの整合性のみ再照合する（生成しない）",
    )
    parser.add_argument(
        "--summary-out",
        type=Path,
        default=None,
        help="kind×split（+material 内訳）の対数集計 JSON を書き出す（任意）",
    )
    args = parser.parse_args()

    # R2R 対応 G2（Codex レビュー第 5 ラウンド）: `--summary-out` の書き込み
    # 経路は分岐で異なる——`run_and_publish` 経路は summary を manifest/pins/
    # 生成 WAV と同じ atomic bundle 内で公開済み（`run_and_publish` 内で
    # 完結。ここでの追加書き込みは不要かつ二重書きになる）。`--check-only`
    # 経路は生成を行わない読み取り専用経路のため、`check_existing` が
    # 衝突検査のみ行い、実際の書き込みは（従来どおり）ここで単独の
    # `_atomic_write_bytes` として行う。
    #
    # R2R 対応 H2（Codex レビュー第 6 ラウンド・順序整理）: `--check-only` は
    # `pins_out` を一切書き換えない（read-only 原則を維持——summary pin の
    # 記録は `run_and_publish` 経路でのみ行う）。`check_existing` は summary
    # の pin 記録（もしあれば）を必ず先に検証し（H2 の (f)。記録が無ければ
    # skip）、続けて衝突検査を行ってから正常に return する——例外なく戻った
    # 場合にのみ、ここで初めて `--summary-out` への書き込みが起こる。つまり
    # 「検証（sidecar 記録の整合性 + 衝突）→ 書込」の順序は `check_existing`
    # と `main()` の呼び出し順そのものから構造的に保証されており、追加の
    # 明示的な順序制御コードは不要（この書込みは pins.json を更新しないため、
    # 「pins 更新」ステップ自体が本経路には存在しない——read-only 原則を崩さず
    # 「--check-only は summary 照合のみ・pins への書込みは run 経路のみ」へ
    # 寄せる整理を採用した。summary_out 自体への書込みは既存 G2 契約を維持
    # する: 生成物ではなく利便のための副次出力であり、pins.json という
    # 信頼アンカーには一切触れないため）。
    try:
        if args.check_only:
            summary = check_existing(
                manifest_out=args.manifest_out,
                pins_out=args.pins_out,
                vocadito_dir=args.vocadito_dir,
                fixtures_path=args.fixtures,
                synth_specs_path=args.synth_specs,
                summary_out=args.summary_out,
            )
            print(f"--check-only: OK ({summary['total']} pairs, sha256 全一致)")
            if args.summary_out is not None:
                _atomic_write_bytes(
                    args.summary_out,
                    json.dumps(summary, indent=2, sort_keys=True).encode("utf-8"),
                )
                print(f"wrote crosstab summary to {args.summary_out}")
        else:
            summary = run_and_publish(
                vocadito_dir=args.vocadito_dir,
                out_dir=args.out_dir,
                manifest_out=args.manifest_out,
                pins_out=args.pins_out,
                fixtures_path=args.fixtures,
                synth_specs_path=args.synth_specs,
                summary_out=args.summary_out,
            )
            print(f"wrote {summary['total']} pairs to {args.manifest_out}")
            print(f"wrote pins sidecar to {args.pins_out}")
            if args.summary_out is not None:
                print(f"wrote crosstab summary to {args.summary_out}")
    except BuildM3dPairsError as exc:
        print(f"FAIL-CLOSED: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
