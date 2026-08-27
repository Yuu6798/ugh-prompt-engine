#!/usr/bin/env python3
"""education_lesson_builder.py — RUN9-L0-HARNESS-3b checkout-stable fixture:
PJS Technique lesson bundle の repo-contained canonical builder.

**背景**: HARNESS-3b の実測記録（`HARNESS3B_EDUCATION_LESSON_RECORD.md`）が
参照する抽出コード（`education_lesson_extractor.py`）とバッチドライバ
（`run_batch_extract.py`）は session workdir（repo 外）にのみ存在し、fresh
checkout では `inputs/education_technique_lesson_manifest.json` が pin する
出力 hash（`training_technique_lesson_sha256`/`validation_technique_lesson_
sha256`）を再構成・検証できなかった。本モジュールはその抽出ロジックを
repo 内 checkout-stable fixture として新設し、`builder_provenance.
builder_sha256` として manifest へ pin することで「fresh checkout から実測を
再現できる」契約を回復する（`speaker_map_builder.py`/`practice_split_
builder.py` と同じ前例体裁: repo-contained ビルダー + 自己適用 validator +
単体テスト）。

**ロジック同一性**: 抽出式・アラインメント規則・直列化は session workdir
`education_lesson_extractor.py`（HARNESS-3b 実測に実際に使用したスクリプト、
`h3b_extractor_spec_draft.md` v1.1 = 本 repo 収載 `HARNESS3B_EXTRACTOR_SPEC.md`
の literal 実装）から**逐語移植**した — WAV 24-bit 手動デコード式、
resample_poly(up=147, down=160)、pyworld.harvest(frame_period=FRAME_PERIOD_MS)、
.lab モーラグルーピング、musicxml パース + tie/メリスマ併合、フレーズ境界
（.lab の `pau`）、アラインメント総数一致ゲート、relative_F0/duration_ratio/
energy_envelope/onset_offset の4チャネル計算式、バンドル直列化
（`json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
+ "\\n"`）は一切変更していない — バンドル byte 再現性が本モジュールの要件
である。変更したのはパス解決（workdir 絶対パス → CLI 引数 + repo 相対
既定値）と CLI 構造（`education_lesson_extractor.py` + `run_batch_extract.py`
2 ファイルの単一ファイル統合）のみ。

**freeze_selfcheck() の意味論変更（重要、逐語移植からの唯一の意図的差分）**:
workdir 原本の `freeze_selfcheck()` は spec sha256 **と** 自身のコード
sha256（`education_lesson_extractor.py` 自身のファイルバイト）の両方を
freeze record と照合していた。本 builder はそのうち spec sha256 の照合の
みを保持し、自身のコード sha256 の自己照合は行わない — 理由: freeze record
（`inputs/h3b_freeze_record.json`）の `extractor_sha256` は HARNESS-3b 実測
当時の **session workdir** `education_lesson_extractor.py` の実バイト
sha256（`ba972ba7...`）を凍結した、attempt-scoped な履歴的証跡であり、本
repo 収載 freeze record はそれを byte-identical にコピーしたものである
（履歴の書き換え禁止）。本 builder（`education_lesson_builder.py`）はその
後に新設された別ファイル（ファイル名・CLI 構造が異なる、必然的に別バイト
列）であり、この履歴的 `extractor_sha256` と一致し得ない — 一致を要求する
と fresh checkout での再現実行が構造的に不可能になる。本 builder 自身の
identity は freeze record ではなく `inputs/education_technique_lesson_
manifest.json` の `builder_provenance.builder_sha256`
（`run9_schema.load_pinned_education_lesson_manifest()` が cross-check）が
別途担う。「凍結が抽出に先行」という spec §6 の不変条件は spec sha256 照合
（本 builder が今も実行する抽出が凍結済み spec のどの版に基づくかを保証
する）で引き続き機械強制される。

禁止事項（HARNESS-3b spec §7・裁定 §2 逐語、継続）:
  - sealed_holdout row_ids はいかなるコードパスにも現れない —
    `load_training_validation_ids()` は split manifest の
    `row_ids.training`/`row_ids.validation` のみを読み、
    `row_ids.sealed_holdout` は一切参照しない（PR #329 第1巡レビュー
    指摘1, P1, 採用対応で `run9_schema.load_pinned_practice_split_
    manifest()` 経由の pin 検証を追加してからも不変——sealed_holdout は
    3集合非交差検証にのみ間接的に関与し、training/validation の抽出
    対象集合には決して現れない）。`extract-song` CLI も同じ frozen
    training∪validation 集合外の song_id（sealed_holdout 含む）を
    decode 前に拒否する。
  - advisory 6 channel（vibrato/breath_placement/release_persistence/
    terminal_mel_persistence/HNR/vowel_drift）のコードパスを実装しない。
  - corpus 統計正規化を実装しない（energy_envelope は per-phrase 自己
    正規化のみ）。
  - stdlib + numpy + scipy + pyworld のみに依存する（抽出ロジック本体）。
    librosa import なし。抽出式が消費する定数は svp_rpe / voice_genesis
    の実装モジュールを import せず Read による転記のみで得る（下記
    FRAME_PERIOD_MS 参照）。`run9_schema`（run9 系 sibling モジュール、
    抽出ロジックではなく split-manifest/contract 検証専用）のみ例外的に
    import する（PR #329 第1巡レビュー指摘1 対応、`practice_split_
    builder.py`/`speaker_map_builder.py` と同じ sibling import 前例）。

出力: training/validation 各1本の JSON バンドル（`run9-technique-lesson-
bundle/1.0`）。バンドル実体ファイル自体は repo にコミットしない（rights
制約 — 実 PJS 音源からの derived artifact のため。sha256 のみ
`inputs/education_technique_lesson_manifest.json` へ pin する）。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import struct
import sys
import tempfile
import types
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.signal import resample_poly

import run9_schema  # noqa: E402  (sibling import — repo-wide run9_* convention;
# PR #329 第1巡レビュー指摘1, P1, 採用対応で新設: sealed-holdout 境界の機械
# 強制には RUN9_CONTRACT.yaml の practice_audio_split_manifest_sha pin と
# `run9_schema.validate_practice_split_manifest()` が要る。これは抽出式・
# アラインメント・直列化のロジック（module docstring「svp_rpe/voice_genesis
# の実装モジュールを import しない」の対象）ではなく検証・公開経路の話であり、
# `practice_split_builder.py`/`speaker_map_builder.py` が既に採用している
# run9_schema sibling import の前例に倣う（両ファイルは `import run9_schema
# as m` だが、本ファイルは mora を指すローカル変数 `m` を全域で多用するため
# エイリアスなしで import する——`ruff` F402 "Import shadowed by loop
# variable" 回避）。

# pyworld is an optional dependency of this builder (repo-wide convention,
# CLAUDE.md "Error Handling": try/except ModuleNotFoundError -> flag, then
# fail-fast only at the call site that actually needs it — same pattern as
# voice_genesis/foundry/adapter/donor_bank.py's sibling modules' tests via
# `requires_world = pytest.mark.skipif(not _has_pyworld(), ...)`). The repo's
# own test/lint environment does not install pyworld (it is only present in
# the isolated `venv_h3b` this builder was originally run under), so a hard
# top-level `import pyworld` would make this entire module — including its
# schema constants, `assemble_bundle()`, `freeze_selfcheck()`, and the CLI's
# non-audio subcommands — uncollectable/unimportable under `pytest`/`ruff`.
# Only `compute_world_f0()` (the WORLD F0 extraction step) actually needs it.
try:
    import pyworld as pw
    PYWORLD_AVAILABLE = True
except ModuleNotFoundError:
    pw = None  # type: ignore[assignment]
    PYWORLD_AVAILABLE = False

_THIS_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# repo-relative default paths (workdir 版は全て CLI 必須引数の絶対パスだった
# — checkout-stable canonical builder として repo 相対の既定値を追加する)。
# ---------------------------------------------------------------------------
DEFAULT_SPLIT_MANIFEST_PATH = _THIS_DIR / "inputs" / "practice_audio_split_manifest.json"
DEFAULT_SPEC_PATH = _THIS_DIR / "HARNESS3B_EXTRACTOR_SPEC.md"
DEFAULT_FREEZE_RECORD_PATH = _THIS_DIR / "inputs" / "h3b_freeze_record.json"
# PR #329 第2巡レビュー指摘2-4（P1、採用）新設: extract_song() が消費前に
# 実バイト照合する per-file sha256 pin（inputs/pjs_consumed_inputs_sha256.json
# = run9_schema.PJS_CONSUMED_INPUTS_MANIFEST_PATH と同一パス）。
DEFAULT_CONSUMED_INPUTS_MANIFEST_PATH = _THIS_DIR / "inputs" / "pjs_consumed_inputs_sha256.json"

# ---------------------------------------------------------------------------
# Constants transcribed BY READ (not import) from repo, per spec Step 1 / §2.
# ---------------------------------------------------------------------------

# voice_genesis/foundry/adapter/donor_bank.py: FRAME_PERIOD_MS = 5.0 (line 34)
FRAME_PERIOD_MS = 5.0

# spec v1.1 §2-1: WAV header requirement (24-bit / mono / 48000 Hz).
REQUIRED_AUDIO_FORMAT_PCM = 1  # WAVE_FORMAT_PCM
REQUIRED_CHANNELS = 1
REQUIRED_SAMPLE_RATE = 48000
REQUIRED_BITS_PER_SAMPLE = 24

# spec §2-2: resample_poly(x, up=147, down=160) : 48000 -> 44100
RESAMPLE_UP = 147
RESAMPLE_DOWN = 160
TARGET_SR = 44100

# spec §2-4: .lab time unit = 100ns
LAB_TIME_UNIT_S = 1e-7

# --- schema/vocab constants, transcribed BY READ from
# voice_genesis/evolution/run9_dual_founder_pjs/run9_schema.py (NOT imported
# by this file — validate_lesson_record() is run separately in the repo's
# own python3, not by this builder, matching the original workdir Step 5).
SCHEMA_LESSON_RECORD = "run9-lesson-record/1.0"
BUNDLE_FORMAT = "run9-technique-lesson-bundle/1.0"

# spec §1: extracted_traits = 4 canonical PERFORMANCE_RESIDUAL_VOCAB names
# (release_behavior NOT used, per 裁定 §4 / spec §1).
EXTRACTED_TRAITS = ["relative_F0", "duration_ratio", "energy_envelope", "onset_offset"]

# run9_schema.py IDENTITY_EXCLUDED_TRAIT_VOCAB (7 items, transcribed verbatim).
IDENTITY_EXCLUDED_TRAIT_VOCAB = (
    "speaker_embedding",
    "timbre_identity",
    "formant_identity",
    "spectral_identity",
    "voice_genome",
    "source_specific_identity_representation",
    "identity_vector",
)

# run9_schema.py sentinel literals (transcribed verbatim, used only as
# non-triggering references/documentation here — no <PENDING_USER_ATTESTATION>
# is ever emitted by this builder, per spec §5 / validate_lesson_record()).
SENTINEL_UNRESOLVED_EXTERNAL = "<UNRESOLVED_EXTERNAL>"

# spec §1 三系統対応表 (channel_vocabulary_map source of truth). Kept in sync
# with run9_schema.TECHNIQUE_LESSON_CHANNEL_VOCABULARY_MAP by test coverage
# — this literal is intentionally NOT derived from `m.TECHNIQUE_LESSON_
# CHANNEL_VOCABULARY_MAP` at import time (the two copies are compared
# byte-for-content in tests/test_education_lesson_builder.py instead). This
# file DOES import run9_schema now (see top-of-file import, PR #329 第1巡
# レビュー指摘1 対応) for split-manifest pin validation — that import is
# unrelated to this constant, which stays an independent literal per the
# module docstring's extraction-logic self-containment goal.
CHANNEL_VOCABULARY_MAP = [
    {
        "physical_channel": "relative F0 contour",
        "extracted_trait": "relative_F0",
        "education_allowed_channel": "pitch_trajectory",
    },
    {
        "physical_channel": "note/mora duration ratio",
        "extracted_trait": "duration_ratio",
        "education_allowed_channel": "phoneme_note_duration_relation",
    },
    {
        "physical_channel": "phrase-normalized energy envelope",
        "extracted_trait": "energy_envelope",
        "education_allowed_channel": "dynamics_energy_trajectory",
    },
    {
        "physical_channel": "attack timing",
        "extracted_trait": "onset_offset",
        "education_allowed_channel": "timing",
    },
    {
        "physical_channel": "phrase-end timing",
        "extracted_trait": "onset_offset",
        "education_allowed_channel": "phrase_end_control",
    },
]


class ExtractorStopError(RuntimeError):
    """Raised for any condition the spec requires an immediate stop for
    (WAV header mismatch, missing tempo, structural musicxml anomaly not
    covered by spec, freeze-record mismatch, etc). Never caught-and-adapted
    — callers must surface this to the operator."""


class WavHeaderError(ExtractorStopError):
    pass


class LabError(ExtractorStopError):
    pass


class MusicXmlError(ExtractorStopError):
    pass


class FreezeCheckError(ExtractorStopError):
    pass


# ---------------------------------------------------------------------------
# Opaque pin-verified types (PR #329 第3巡レビュー指摘2, P1, 採用対応):
# `extract_song()` が生の Sequence[str]/Mapping を受理する限り、その値が
# `load_training_validation_ids()`/`load_consumed_inputs_pins()`（RUN9_
# CONTRACT.yaml pin との read-once sha256 照合を経由する canonical loader）
# を通ったものか、呼び出し元が自作した未検証の list/dict かを `extract_
# song()` 自身は区別できなかった——CLI（`_cmd_extract_song`/`_cmd_build`）
# はゲート済み値を渡すが、別スクリプトからの直接 import 呼び出しは無検証
# の集合をそのまま渡せてしまう構造的な穴があった。
#
# `FrozenSplitPins`/`ConsumedInputPins` は上記2関数の戻り値専用の不透明型
# として新設する。`extract_song()` は isinstance 検査でこの型のみを受理し、
# 生の list/set/tuple/dict は `ExtractorStopError` で拒否する。
#
# **正直な宣言（Python の限界）**: Python は private な直接構築を完全には
# 防げない —— 呼び出し元が `FrozenSplitPins(training_ids=(...),
# validation_ids=(...))` を悪意を持って直接構築すれば、isinstance 検査は
# 通ってしまう（Python にはコンストラクタを呼び出し元ごとに制限する言語
# 機構が無い）。本ゲートが機械強制するのは「repo 内の**全**コードパスが
# `load_training_validation_ids()`/`load_consumed_inputs_pins()`（そして
# その内部で `run9_schema.load_pinned_practice_split_manifest()`/`load_
# pinned_consumed_inputs_manifest()` の pin/構造検証）を経由する」という
# **構造的規約**であり、Python の型システムによる封印ではない —— 境界は
# 「本ファイル内で `FrozenSplitPins(`/`ConsumedInputPins(` を直接呼び出す
# のはこの2つの canonical loader のみである」ことをレビュー・grep 監査で
# 目視確認できる、という前提の上に立つ。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrozenSplitPins:
    """`load_training_validation_ids()`（canonical loader）のみが構築する
    べき不透明型。pin 検証済み training/validation row_id 集合を保持する
    （`row_ids.sealed_holdout` はこの型に一切現れない——`load_training_
    validation_ids()` が既に切り落として渡す）。

    `__iter__` は `(training_ids, validation_ids)` の2-tuple を返す —
    既存の `training_ids, validation_ids = load_training_validation_ids()`
    という呼び出し慣用句をそのまま維持するための互換シム（本来の値の
    出処追跡は isinstance 検査で行う——unpack 後の生 tuple は
    `extract_song()` の直接引数としては受理されない、下記 `frozen_
    allowed_ids` 経由の使用のみが正規経路）。
    """

    training_ids: Tuple[str, ...]
    validation_ids: Tuple[str, ...]

    def __iter__(self) -> Iterator[Tuple[str, ...]]:
        return iter((self.training_ids, self.validation_ids))

    @property
    def frozen_allowed_ids(self) -> Tuple[str, ...]:
        """training ∪ validation（昇順・重複排除済み）。"""
        return tuple(sorted(set(self.training_ids) | set(self.validation_ids)))


@dataclass(frozen=True)
class ConsumedInputPins:
    """`load_consumed_inputs_pins()`（canonical loader）のみが構築するべき
    不透明型。pin 検証済み per-song consumed-input sha256 辞書
    （`{song_id: {"lab_sha256": ..., "musicxml_sha256": ..., "wav_sha256":
    ...}}`）を保持する。設計意図・Python 境界の宣言はモジュール冒頭の
    コメント（`FrozenSplitPins` 直前）と同型。

    `__post_init__` で内部辞書を `types.MappingProxyType` の入れ子へ
    変換し、コンストラクタへ渡された生 dict への外部からの参照越し変更が
    格納後の値へ波及しないようにする（frozen dataclass はフィールドの
    再代入は禁止するが、参照先オブジェクトの可変性までは保証しないため）。
    """

    pins: Mapping[str, Mapping[str, str]]

    def __post_init__(self) -> None:
        frozen_pins = types.MappingProxyType(
            {song_id: types.MappingProxyType(dict(entry)) for song_id, entry in self.pins.items()}
        )
        object.__setattr__(self, "pins", frozen_pins)

    def get(self, song_id: str) -> Optional[Mapping[str, str]]:
        return self.pins.get(song_id)


# ---------------------------------------------------------------------------
# sha256 helpers
# ---------------------------------------------------------------------------

def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_of_self() -> str:
    """本 builder 自身の実バイト sha256。freeze_selfcheck() のゲートには
    使わない（モジュール docstring「freeze_selfcheck() の意味論変更」参照
    — 本 builder 自身の identity は `inputs/education_technique_lesson_
    manifest.json` の `builder_provenance.builder_sha256` が別途担う）。
    レポート出力用の informational な値としてのみ提供する。"""
    return sha256_of_file(Path(__file__).resolve())


def freeze_selfcheck(freeze_record_path: Path, spec_path: Path) -> Dict[str, Any]:
    """spec §2 extractor description の一部を実装する: 起動時に freeze
    record を読み、spec sha256 を照合してから走る（照合失敗は即停止）。

    workdir 原本との差分（モジュール docstring「freeze_selfcheck() の意味論
    変更」参照）: freeze record の `extractor_sha256`（HARNESS-3b 実測当時の
    session workdir `education_lesson_extractor.py` の履歴的 sha256）との
    自己照合は行わない — 本 builder は別ファイルであり必然的にバイト列が
    異なるため。spec sha256 照合のみを「凍結が抽出に先行」の機械強制として
    保持する。
    """
    if not freeze_record_path.exists():
        raise FreezeCheckError(f"freeze record not found: {freeze_record_path}")
    record = json.loads(freeze_record_path.read_text(encoding="utf-8"))
    expected_spec_sha = record.get("spec_sha256")
    actual_spec_sha = sha256_of_file(spec_path)
    if expected_spec_sha != actual_spec_sha:
        raise FreezeCheckError(
            f"spec sha256 mismatch: freeze record has {expected_spec_sha!r}, "
            f"actual {spec_path} has {actual_spec_sha!r}"
        )
    return record


# ---------------------------------------------------------------------------
# WAV header (metadata-only, no decode) + load
# ---------------------------------------------------------------------------

def read_wav_fmt_header(path: Path) -> Dict[str, Any]:
    """Read only the RIFF/fmt chunk of a WAV file — metadata, not decode.
    Never reads PCM sample bytes."""
    with open(path, "rb") as fh:
        riff = fh.read(12)
        if len(riff) < 12 or riff[0:4] != b"RIFF" or riff[8:12] != b"WAVE":
            raise WavHeaderError(f"{path}: not a RIFF/WAVE file (header={riff!r})")
        fmt: Optional[Dict[str, Any]] = None
        data_size: Optional[int] = None
        while True:
            hdr = fh.read(8)
            if len(hdr) < 8:
                break
            chunk_id = hdr[0:4]
            chunk_size = struct.unpack("<I", hdr[4:8])[0]
            if chunk_id == b"fmt ":
                fmt_bytes = fh.read(chunk_size)
                if len(fmt_bytes) < 16:
                    raise WavHeaderError(f"{path}: fmt chunk too short ({len(fmt_bytes)} bytes)")
                (audio_format, nch, sr, byte_rate, block_align, bits) = struct.unpack(
                    "<HHIIHH", fmt_bytes[:16]
                )
                fmt = dict(
                    audio_format=audio_format, channels=nch, sample_rate=sr,
                    byte_rate=byte_rate, block_align=block_align, bits_per_sample=bits,
                )
                if chunk_size % 2:
                    fh.read(1)
            elif chunk_id == b"data":
                data_size = chunk_size
                fh.seek(chunk_size + (chunk_size % 2), 1)
            else:
                fh.seek(chunk_size + (chunk_size % 2), 1)
        if fmt is None:
            raise WavHeaderError(f"{path}: no fmt chunk found")
        fmt["data_chunk_size"] = data_size
        fmt["path"] = str(path)
        return fmt


def _validate_wav_fmt_or_stop(hdr: Dict[str, Any], label: str) -> Dict[str, Any]:
    """spec v1.1 §2-1: RIFF PCM を検査し 24-bit/mono/48000Hz を要求。不一致は
    即停止（`hdr` は既にパース済みの fmt dict — `check_wav_header_or_stop()`/
    `check_wav_header_or_stop_bytes()` が共有する検証本体）。"""
    if hdr["audio_format"] != REQUIRED_AUDIO_FORMAT_PCM:
        raise WavHeaderError(
            f"{label}: audio_format={hdr['audio_format']} (expected PCM={REQUIRED_AUDIO_FORMAT_PCM})"
        )
    mismatches = []
    if hdr["channels"] != REQUIRED_CHANNELS:
        mismatches.append(f"channels={hdr['channels']} (required {REQUIRED_CHANNELS})")
    if hdr["sample_rate"] != REQUIRED_SAMPLE_RATE:
        mismatches.append(f"sample_rate={hdr['sample_rate']} (required {REQUIRED_SAMPLE_RATE})")
    if hdr["bits_per_sample"] != REQUIRED_BITS_PER_SAMPLE:
        mismatches.append(f"bits_per_sample={hdr['bits_per_sample']} (required {REQUIRED_BITS_PER_SAMPLE})")
    if mismatches:
        raise WavHeaderError(f"{label}: WAV header mismatch — " + "; ".join(mismatches))
    return hdr


def check_wav_header_or_stop(path: Path) -> Dict[str, Any]:
    """path ベース版（`probe-header` 系ユーティリティ・既存呼び出し互換
    用、`read_wav_fmt_header()` のストリーミング skip 読みをそのまま使う
    — 1ファイル1回呼び出しの独立ユーティリティであり、read-once-then-
    decode の TOCTOU 対象ではない）。`extract_song()` は代わりに
    `check_wav_header_or_stop_bytes()` を使う（下記参照）。"""
    return _validate_wav_fmt_or_stop(read_wav_fmt_header(path), str(path))


def check_wav_header_or_stop_bytes(buf: bytes, label: str) -> Dict[str, Any]:
    """spec v1.1 §2-1 の検証を、既に read_bytes() 済みの `buf`（wav 全バイト）
    に対して行う（PR #329 第3巡レビュー指摘3, P2, 採用対応: TOCTOU 閉鎖）。
    `extract_song()` はファイルを1回だけ read_bytes() した同一バッファを
    本関数と `load_wav_24bit_mono_48k_bytes()` の両方へ渡すことで、
    sha256 照合・ヘッダ検証・decode の全段がファイル再 open なしに同一
    バイト列に対して行われることを保証する（`speaker_map_builder.py`
    verified self-exec dispatch の read-once パターンと同型）。"""
    return _validate_wav_fmt_or_stop(_parse_wav_fmt_from_buffer(buf, label), label)


def _parse_wav_fmt_from_buffer(buf: bytes, label: str) -> Dict[str, Any]:
    """`buf`（read_bytes() 済み wav 全バイト）から RIFF/fmt チャンクのみを
    解析する。`read_wav_fmt_header()`（probe-header 専用、data チャンク
    本体を読まないストリーミング skip 版）とは独立の実装 — こちらは
    `check_wav_header_or_stop_bytes()`/`_parse_wav_fmt_and_data_from_buffer()`
    が共有する read-once 前提のヘルパー。"""
    fh = io.BytesIO(buf)
    riff = fh.read(12)
    if len(riff) < 12 or riff[0:4] != b"RIFF" or riff[8:12] != b"WAVE":
        raise WavHeaderError(f"{label}: not a RIFF/WAVE file (header={riff!r})")
    fmt: Optional[Dict[str, Any]] = None
    data_size: Optional[int] = None
    while True:
        hdr = fh.read(8)
        if len(hdr) < 8:
            break
        chunk_id = hdr[0:4]
        chunk_size = struct.unpack("<I", hdr[4:8])[0]
        if chunk_id == b"fmt ":
            fmt_bytes = fh.read(chunk_size)
            if len(fmt_bytes) < 16:
                raise WavHeaderError(f"{label}: fmt chunk too short ({len(fmt_bytes)} bytes)")
            (audio_format, nch, sr, byte_rate, block_align, bits) = struct.unpack(
                "<HHIIHH", fmt_bytes[:16]
            )
            fmt = dict(
                audio_format=audio_format, channels=nch, sample_rate=sr,
                byte_rate=byte_rate, block_align=block_align, bits_per_sample=bits,
            )
            if chunk_size % 2:
                fh.read(1)
        elif chunk_id == b"data":
            data_size = chunk_size
            fh.seek(chunk_size + (chunk_size % 2), 1)
        else:
            fh.seek(chunk_size + (chunk_size % 2), 1)
    if fmt is None:
        raise WavHeaderError(f"{label}: no fmt chunk found")
    fmt["data_chunk_size"] = data_size
    fmt["path"] = label
    return fmt


def _parse_wav_fmt_and_data_from_buffer(buf: bytes, label: str) -> Tuple[Dict[str, Any], bytes]:
    """`buf`（read_bytes() 済み wav 全バイト）から fmt チャンク**と** data
    チャンクの実バイトの両方を解析する（decode 直前の read-once ヘルパー
    — PR #329 第3巡レビュー指摘3, P2, 採用対応）。"""
    fh = io.BytesIO(buf)
    riff = fh.read(12)
    if len(riff) < 12 or riff[0:4] != b"RIFF" or riff[8:12] != b"WAVE":
        raise WavHeaderError(f"{label}: not a RIFF/WAVE file (header={riff!r})")
    fmt: Optional[Dict[str, Any]] = None
    data_bytes: Optional[bytes] = None
    while True:
        hdr = fh.read(8)
        if len(hdr) < 8:
            break
        chunk_id = hdr[0:4]
        chunk_size = struct.unpack("<I", hdr[4:8])[0]
        if chunk_id == b"fmt ":
            fmt_bytes = fh.read(chunk_size)
            (audio_format, nch, sr, byte_rate, block_align, bits) = struct.unpack(
                "<HHIIHH", fmt_bytes[:16]
            )
            fmt = dict(audio_format=audio_format, channels=nch, sample_rate=sr,
                       byte_rate=byte_rate, block_align=block_align, bits_per_sample=bits)
            if chunk_size % 2:
                fh.read(1)
        elif chunk_id == b"data":
            data_bytes = fh.read(chunk_size)
            if chunk_size % 2:
                fh.read(1)
        else:
            fh.seek(chunk_size + (chunk_size % 2), 1)
    if fmt is None or data_bytes is None:
        raise WavHeaderError(f"{label}: missing fmt or data chunk")
    return fmt, data_bytes


def _read_wav_fmt_and_data(path: Path) -> Tuple[Dict[str, Any], bytes]:
    """path ベース版（既存の単体呼び出し互換用）。1回 `read_bytes()` して
    bytes 版 `_parse_wav_fmt_and_data_from_buffer()` へ委譲する。"""
    buf = path.read_bytes()
    return _parse_wav_fmt_and_data_from_buffer(buf, str(path))


def _decode_wav_24bit_from_fmt_and_data(fmt: Dict[str, Any], data_bytes: bytes, label: str) -> np.ndarray:
    """spec v1.1 §2-1 pinned manual 24-bit byte decode（library-dependent
    24-bit WAV decoding is avoided as ambiguous）:
      - data bytes reshaped to (-1, 3) via np.frombuffer(..., dtype=uint8)
      - v = b0 | (b1<<8) | (b2<<16), little-endian, assembled as int32
      - v >= 2**23 -> v -= 2**24 (two's-complement sign extension)
      - x = v.astype(float64) / 8388608.0  (2**23; value range [-1, 1))
    """
    if fmt["bits_per_sample"] != REQUIRED_BITS_PER_SAMPLE or fmt["channels"] != REQUIRED_CHANNELS \
            or fmt["sample_rate"] != REQUIRED_SAMPLE_RATE or fmt["audio_format"] != REQUIRED_AUDIO_FORMAT_PCM:
        # Should be unreachable if check_wav_header_or_stop{,_bytes}() was called first.
        raise WavHeaderError(f"{label}: fmt chunk disagrees with prior header probe: {fmt}")

    raw = np.frombuffer(data_bytes, dtype=np.uint8)
    n_samples = len(raw) // 3
    if n_samples * 3 != len(raw):
        raise WavHeaderError(f"{label}: data chunk size {len(raw)} is not a multiple of 3 bytes (24-bit mono)")
    raw = raw[: n_samples * 3].reshape(-1, 3)
    b0 = raw[:, 0].astype(np.int32)
    b1 = raw[:, 1].astype(np.int32)
    b2 = raw[:, 2].astype(np.int32)
    v = b0 | (b1 << 8) | (b2 << 16)
    v = np.where(v >= (1 << 23), v - (1 << 24), v)
    x = v.astype(np.float64) / 8388608.0  # 2**23
    return x


def load_wav_24bit_mono_48k_bytes(buf: bytes, label: str) -> np.ndarray:
    """Decode PCM samples from an already-read `buf`. Only ever called AFTER
    `check_wav_header_or_stop_bytes()` has passed for this exact `buf`
    (PR #329 第3巡レビュー指摘3, P2, 採用対応: `extract_song()` はこの
    関数へ header 検証と同じ `buf` を渡すことでファイル再 open を排除
    する)。"""
    fmt, data_bytes = _parse_wav_fmt_and_data_from_buffer(buf, label)
    return _decode_wav_24bit_from_fmt_and_data(fmt, data_bytes, label)


def load_wav_24bit_mono_48k(path: Path) -> np.ndarray:
    """path ベース版（既存の単体呼び出し互換用）。1回 `read_bytes()` して
    bytes 版 `load_wav_24bit_mono_48k_bytes()` へ委譲する。Only ever called
    AFTER check_wav_header_or_stop() has passed for this exact file."""
    buf = path.read_bytes()
    return load_wav_24bit_mono_48k_bytes(buf, str(path))


def resample_to_44100(x48k: np.ndarray) -> np.ndarray:
    """spec §2-2: scipy.signal.resample_poly(x, up=147, down=160) -> 44100 Hz."""
    return resample_poly(x48k, RESAMPLE_UP, RESAMPLE_DOWN)


def compute_world_f0(x44k: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """spec §2-3: pyworld.harvest(x_44k, 44100, frame_period=FP)."""
    if not PYWORLD_AVAILABLE:
        raise ModuleNotFoundError(
            "compute_world_f0(): pyworld is not installed in this Python environment — "
            "run this builder under the isolated venv used for HARNESS-3b extraction "
            "(see HARNESS3B_EDUCATION_LESSON_RECORD.md §3 dependency pins), not the repo's "
            "default test/lint environment."
        )
    f0, temporal_positions = pw.harvest(x44k, TARGET_SR, frame_period=FRAME_PERIOD_MS)
    return f0, temporal_positions


# ---------------------------------------------------------------------------
# .lab parsing + mora grouping (independent reimplementation — reference
# read: voice_genesis/foundry/adapter/donor_bank_lab.py parse_lab_text() /
# group_lab_to_morae(), NOT imported).
# ---------------------------------------------------------------------------

VOWELS_5 = ("a", "i", "u", "e", "o")
_LAB_BOUNDARY_LABELS = ("pau", "xx")  # both reset the pending-consonant buffer
_LAB_PHRASE_BOUNDARY_LABEL = "pau"  # spec §3: phrase 分割の正本 = .lab の pau 境界 only


@dataclass(frozen=True)
class LabPhoneme:
    start_s: float
    end_s: float
    phoneme: str


def _parse_lab_text(text: str, label: str) -> List[LabPhoneme]:
    out: List[LabPhoneme] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) != 3:
            raise LabError(f"{label}: malformed .lab line (expected 3 columns): {line!r}")
        start_raw, end_raw, phoneme = parts
        out.append(LabPhoneme(int(start_raw) * LAB_TIME_UNIT_S, int(end_raw) * LAB_TIME_UNIT_S, phoneme))
    if not out:
        raise LabError(f"{label}: empty .lab file")
    return out


def parse_lab_bytes(buf: bytes, label: str) -> List[LabPhoneme]:
    """`buf`（read_bytes() 済み .lab 全バイト）を text へ decode してから
    解析する（PR #329 第3巡レビュー指摘3, P2, 採用対応: TOCTOU 閉鎖 —
    `extract_song()` は sha256 照合に使ったのと同一の `buf` をそのまま
    渡す。ファイル再 open を排除）。"""
    return _parse_lab_text(buf.decode("utf-8"), label)


def parse_lab_file(path: Path) -> List[LabPhoneme]:
    """path ベース版（既存の単体呼び出し互換用）。1回 `read_bytes()` して
    bytes 版 `parse_lab_bytes()` へ委譲する。"""
    buf = path.read_bytes()
    return parse_lab_bytes(buf, str(path))


@dataclass(frozen=True)
class LabMora:
    onset: Optional[str]
    vowel: Optional[str]  # a/i/u/e/o or "N"
    onset_start_s: Optional[float]
    onset_end_s: Optional[float]
    vowel_start_s: float
    vowel_end_s: float
    phrase_index: int

    @property
    def t0(self) -> float:
        # [IMPL-CHOICE] mora time span used for alignment/formulas (spec §4
        # t0_i/t1_i): onset consonant start if present, else vowel-core
        # start — i.e. the mora's full (consonant+vowel) extent, matching
        # the "mora unit" grouping (donor_bank_lab.LabMora groups onset+
        # vowel together as one record).
        return self.onset_start_s if self.onset_start_s is not None else self.vowel_start_s

    @property
    def t1(self) -> float:
        return self.vowel_end_s


def group_lab_to_morae_with_phrases(phonemes: Sequence[LabPhoneme]) -> List[LabMora]:
    """Reimplementation of donor_bank_lab.group_lab_to_morae() grouping
    semantics (pau/xx reset pending consonant; cl accumulates into pending
    without becoming the onset label itself; N is a standalone mora),
    EXTENDED with phrase_index assignment per spec §3 (phrase = maximal run
    of non-pau morae; only "pau" — not "xx" — is a phrase boundary, matching
    the reference's separate concerns: mora-buffer reset vs phrase split)."""
    morae: List[LabMora] = []
    pending_onset: Optional[str] = None
    pending_start: Optional[float] = None
    phrase_index = 0
    morae_in_current_phrase = 0

    for ph in phonemes:
        if ph.phoneme == _LAB_PHRASE_BOUNDARY_LABEL:
            if morae_in_current_phrase > 0:
                phrase_index += 1
                morae_in_current_phrase = 0
            pending_onset, pending_start = None, None
            continue
        if ph.phoneme == "xx":
            # boundary for mora-buffer purposes only (matches reference);
            # NOT a phrase boundary per spec §3.
            pending_onset, pending_start = None, None
            continue
        if ph.phoneme == "cl":
            if pending_onset is None:
                pending_start = ph.start_s
            continue
        if ph.phoneme == "N":
            morae.append(LabMora(
                onset=None, vowel="N", onset_start_s=None, onset_end_s=None,
                vowel_start_s=ph.start_s, vowel_end_s=ph.end_s, phrase_index=phrase_index,
            ))
            morae_in_current_phrase += 1
            pending_onset, pending_start = None, None
            continue
        if ph.phoneme in VOWELS_5:
            morae.append(LabMora(
                onset=pending_onset, vowel=ph.phoneme,
                onset_start_s=pending_start,
                onset_end_s=(ph.start_s if pending_onset is not None else None),
                vowel_start_s=ph.start_s, vowel_end_s=ph.end_s, phrase_index=phrase_index,
            ))
            morae_in_current_phrase += 1
            pending_onset, pending_start = None, None
            continue
        # consonant (incl. palatalized): buffer it
        if pending_start is None:
            pending_start = ph.start_s
        pending_onset = ph.phoneme
    return morae


# ---------------------------------------------------------------------------
# musicxml parsing (stdlib xml.etree only)
# ---------------------------------------------------------------------------

_STEP_BASE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def midi_to_hz(step: str, alter: int, octave: int) -> float:
    midi = _STEP_BASE[step] + alter + 12 * (octave + 1)
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


@dataclass
class RawNote:
    voice: str
    beat_onset: float
    beat_duration: float
    is_rest: bool
    pitch_step: Optional[str]
    pitch_alter: int
    pitch_octave: Optional[int]
    tie_types: Tuple[str, ...]
    lyric_text: Optional[str]
    order_index: int


def _parse_musicxml_root(root: ET.Element, label: str) -> Tuple[List[Tuple[float, float]], List[RawNote]]:
    """Returns (tempo_events, raw_notes) from an already-parsed `<score-
    partwise>` root element. tempo_events = list of (beat_position,
    tempo_bpm) in document-encounter order. raw_notes in document order
    with a shared cursor (beat_onset) computed by walking <note>/<backup>/
    <forward> sequentially regardless of <voice> — this is standard
    MusicXML cursor semantics, not an invention (verified against
    pjs064's backup=48/forward=21+3+18 passage, which reconciles exactly
    under this rule)."""
    if root.tag != "score-partwise":
        raise MusicXmlError(f"{label}: unsupported root <{root.tag}> (expected score-partwise)")
    parts = root.findall("part")
    if len(parts) != 1:
        raise MusicXmlError(f"{label}: expected exactly 1 <part>, found {len(parts)}")
    part = parts[0]

    divisions: Optional[float] = None
    beat_cursor = 0.0
    tempo_events: List[Tuple[float, float]] = []
    raw_notes: List[RawNote] = []
    order_index = 0

    for measure in part.findall("measure"):
        for child in measure:
            tag = child.tag
            if tag == "attributes":
                div_el = child.find("divisions")
                if div_el is not None:
                    divisions = float(div_el.text)
            elif tag == "direction":
                sound_el = child.find("sound")
                if sound_el is not None and sound_el.get("tempo") is not None:
                    tempo_events.append((beat_cursor, float(sound_el.get("tempo"))))
            elif tag == "backup":
                dur_el = child.find("duration")
                if dur_el is None:
                    raise MusicXmlError(f"{label}: <backup> missing <duration>")
                if divisions is None:
                    raise MusicXmlError(f"{label}: <backup> before <divisions> known")
                beat_cursor -= float(dur_el.text) / divisions
            elif tag == "forward":
                dur_el = child.find("duration")
                if dur_el is None:
                    raise MusicXmlError(f"{label}: <forward> missing <duration>")
                if divisions is None:
                    raise MusicXmlError(f"{label}: <forward> before <divisions> known")
                beat_cursor += float(dur_el.text) / divisions
            elif tag == "note":
                if child.find("grace") is not None:
                    raise MusicXmlError(f"{label}: <grace> note — not covered by spec, stop")
                if child.find("chord") is not None:
                    raise MusicXmlError(f"{label}: <chord> note — not covered by spec, stop")
                dur_el = child.find("duration")
                if dur_el is None:
                    raise MusicXmlError(f"{label}: <note> missing <duration>")
                if divisions is None:
                    raise MusicXmlError(f"{label}: <note> before <divisions> known")
                beat_duration = float(dur_el.text) / divisions
                voice_el = child.find("voice")
                voice = voice_el.text if voice_el is not None else "1"
                is_rest = child.find("rest") is not None
                pitch_step = pitch_alter = pitch_octave = None
                if not is_rest:
                    pitch_el = child.find("pitch")
                    if pitch_el is None:
                        raise MusicXmlError(f"{label}: non-rest <note> missing <pitch> (unpitched unsupported)")
                    step_el = pitch_el.find("step")
                    octave_el = pitch_el.find("octave")
                    if step_el is None or octave_el is None:
                        raise MusicXmlError(f"{label}: <pitch> missing step/octave")
                    pitch_step = step_el.text
                    alter_el = pitch_el.find("alter")
                    pitch_alter = int(alter_el.text) if alter_el is not None else 0
                    pitch_octave = int(octave_el.text)
                tie_types = tuple(t.get("type") for t in child.findall("tie"))
                lyric_el = child.find("lyric")
                lyric_text = None
                if lyric_el is not None:
                    text_el = lyric_el.find("text")
                    if text_el is not None and text_el.text:
                        lyric_text = text_el.text
                raw_notes.append(RawNote(
                    voice=voice, beat_onset=beat_cursor, beat_duration=beat_duration,
                    is_rest=is_rest, pitch_step=pitch_step, pitch_alter=pitch_alter or 0,
                    pitch_octave=pitch_octave, tie_types=tie_types, lyric_text=lyric_text,
                    order_index=order_index,
                ))
                order_index += 1
                beat_cursor += beat_duration
            # other tags (print, barline, harmony, sound w/o tempo, ...) ignored deliberately.

    if not tempo_events:
        raise MusicXmlError(f"{label}: no <sound tempo=...> found anywhere — tempo must not be invented, stop")
    return tempo_events, raw_notes


def parse_musicxml_bytes(buf: bytes, label: str) -> Tuple[List[Tuple[float, float]], List[RawNote]]:
    """`buf`（read_bytes() 済み .musicxml 全バイト）を `xml.etree.ElementTree.
    fromstring()` で直接パースする（PR #329 第3巡レビュー指摘3, P2, 採用
    対応: TOCTOU 閉鎖 — `extract_song()` は sha256 照合に使ったのと同一の
    `buf` をそのまま渡す。ファイル再 open を排除）。"""
    try:
        root = ET.fromstring(buf)
    except ET.ParseError as exc:
        raise MusicXmlError(f"{label}: XML parse error: {exc}") from exc
    return _parse_musicxml_root(root, label)


def parse_musicxml(path: Path) -> Tuple[List[Tuple[float, float]], List[RawNote]]:
    """path ベース版（既存の単体呼び出し互換用）。1回 `read_bytes()` して
    bytes 版 `parse_musicxml_bytes()` へ委譲する。"""
    buf = path.read_bytes()
    return parse_musicxml_bytes(buf, str(path))


def build_tempo_segments(tempo_events: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Piecewise tempo function as (segment_start_beat, tempo_bpm), sorted
    ascending by start beat; ties at the same position resolve to the
    later-encountered event (stable sort + last-wins dedup). If the
    earliest known tempo event is not at beat 0 (never observed in this
    corpus — always the very first direction, before any note), the same
    tempo is assumed retroactively back to beat 0 [IMPL-CHOICE] rather than
    leaving beat range [0, first_event) undefined."""
    indexed = list(enumerate(tempo_events))
    indexed.sort(key=lambda pair: (pair[1][0], pair[0]))
    dedup: List[Tuple[float, float]] = []
    for _, (pos, tempo) in indexed:
        if dedup and dedup[-1][0] == pos:
            dedup[-1] = (pos, tempo)
        else:
            dedup.append((pos, tempo))
    if dedup[0][0] != 0.0:
        dedup.insert(0, (0.0, dedup[0][1]))
    return dedup


def beats_to_seconds(beat_position: float, segments: Sequence[Tuple[float, float]]) -> float:
    if beat_position < -1e-9:
        raise MusicXmlError(f"negative beat position {beat_position} — corrupt cursor arithmetic")
    beat_position = max(0.0, beat_position)
    seconds = 0.0
    n = len(segments)
    for i in range(n):
        seg_start, tempo = segments[i]
        seg_end = segments[i + 1][0] if i + 1 < n else None
        if seg_end is not None and beat_position >= seg_end:
            seconds += (seg_end - seg_start) * 60.0 / tempo
            continue
        seconds += (beat_position - seg_start) * 60.0 / tempo
        return seconds
    return seconds


@dataclass
class ScoreMora:
    voice: str
    onset_beat: float
    end_beat: float
    pitch_step: str
    pitch_alter: int
    pitch_octave: int
    lyric_text: str
    order_index: int


def merge_score_morae(raw_notes: Sequence[RawNote], path_for_errors: str) -> List[ScoreMora]:
    """spec §2-5: tie で連結された note は1つの発音noteに併合。lyric を
    持たない発音note（メリスマ継続）は直前のlyric noteに併合し持続を延長。
    rest は数えない.

    [IMPL-CHOICE] Empirically verified against the full 100-song musicxml
    corpus: every lyric-less pitched note has the SAME pitch as the note it
    continues (no pitch-changing melisma exists in this corpus), and the
    only trigger that matters is lyric-presence (not the <tie> tag itself —
    e.g. pjs060 has an un-tied lyric-less same-pitch continuation note).
    Grouping is done independently PER VOICE (voice tag defines the
    monophonic sub-stream a tie/melisma chain lives in), then the resulting
    per-voice mora lists are merged into one globally chronological
    sequence sorted by onset_beat (tie-break: voice asc, then original
    document order) — this only matters for pjs064, the sole file with a
    second overlapping voice."""
    by_voice: Dict[str, List[RawNote]] = {}
    for n in raw_notes:
        by_voice.setdefault(n.voice, []).append(n)

    result: List[ScoreMora] = []
    for voice, notes in by_voice.items():
        current: Optional[ScoreMora] = None
        for n in notes:
            if n.is_rest:
                current = None
                continue
            if n.lyric_text is not None:
                current = ScoreMora(
                    voice=voice, onset_beat=n.beat_onset, end_beat=n.beat_onset + n.beat_duration,
                    pitch_step=n.pitch_step, pitch_alter=n.pitch_alter, pitch_octave=n.pitch_octave,
                    lyric_text=n.lyric_text, order_index=n.order_index,
                )
                result.append(current)
            else:
                if current is None:
                    raise MusicXmlError(
                        f"{path_for_errors}: lyric-less note with no open mora to extend "
                        f"(voice {voice!r}, order_index={n.order_index}) — stop, no invention"
                    )
                if (n.pitch_step, n.pitch_alter, n.pitch_octave) != (
                    current.pitch_step, current.pitch_alter, current.pitch_octave
                ):
                    raise MusicXmlError(
                        f"{path_for_errors}: lyric-less continuation changes pitch "
                        f"(voice {voice!r}, order_index={n.order_index}) — pitch-changing melisma "
                        "not covered by spec §2-5's same-pitch assumption, stop"
                    )
                current.end_beat = n.beat_onset + n.beat_duration

    result.sort(key=lambda m: (m.onset_beat, m.voice, m.order_index))
    return result


@dataclass(frozen=True)
class ScoreMoraTimed:
    onset_s: float
    end_s: float
    hz: float
    lyric: str


def finalize_score_morae(
    merged: Sequence[ScoreMora], tempo_segments: Sequence[Tuple[float, float]], path_for_errors: str
) -> List[ScoreMoraTimed]:
    out = []
    for m in merged:
        onset_s = beats_to_seconds(m.onset_beat, tempo_segments)
        end_s = beats_to_seconds(m.end_beat, tempo_segments)
        if end_s - onset_s <= 0.0:
            raise MusicXmlError(
                f"{path_for_errors}: score mora {m.lyric_text!r} has non-positive duration "
                f"({end_s - onset_s}) — stop (score_duration_i must be > 0)"
            )
        hz = midi_to_hz(m.pitch_step, m.pitch_alter, m.pitch_octave)
        out.append(ScoreMoraTimed(onset_s=onset_s, end_s=end_s, hz=hz, lyric=m.lyric_text))
    return out


# ---------------------------------------------------------------------------
# Alignment (fail-closed) — spec §3
# ---------------------------------------------------------------------------

@dataclass
class Phrase:
    phrase_index: int
    lab_indices: List[int]  # indices into lab_morae
    offset_p_s: float


@dataclass
class AlignmentResult:
    status: str  # "aligned" | "count_mismatch"
    reason: Optional[str]
    lab_mora_count: int
    score_mora_count: int
    phrases: List[Phrase] = field(default_factory=list)


def align_song(lab_morae: List[LabMora], score_morae: List[ScoreMoraTimed]) -> AlignmentResult:
    if len(lab_morae) != len(score_morae):
        return AlignmentResult(
            status="count_mismatch",
            reason=(
                f"lab_mora_count={len(lab_morae)} != score_mora_count={len(score_morae)} "
                "— no interpolation/guessed alignment performed"
            ),
            lab_mora_count=len(lab_morae),
            score_mora_count=len(score_morae),
        )
    # group lab_morae indices by phrase_index (already assigned during .lab grouping)
    phrase_to_indices: Dict[int, List[int]] = {}
    for i, m in enumerate(lab_morae):
        phrase_to_indices.setdefault(m.phrase_index, []).append(i)
    phrases: List[Phrase] = []
    for p_idx in sorted(phrase_to_indices):
        indices = phrase_to_indices[p_idx]
        first_i = indices[0]
        offset_p = lab_morae[first_i].t0 - score_morae[first_i].onset_s
        phrases.append(Phrase(phrase_index=p_idx, lab_indices=indices, offset_p_s=offset_p))
    return AlignmentResult(
        status="aligned", reason=None,
        lab_mora_count=len(lab_morae), score_mora_count=len(score_morae),
        phrases=phrases,
    )


# ---------------------------------------------------------------------------
# Channel computation — spec §4
# ---------------------------------------------------------------------------

def compute_relative_f0(
    lab_morae: List[LabMora], f0: np.ndarray, temporal_positions: np.ndarray,
) -> List[Dict[str, Any]]:
    """Per aligned mora: list of {t_s, voiced, value_hz (None if unvoiced)}
    for WORLD frames within [t0_i, t1_i)."""
    out = []
    for m in lab_morae:
        frames = []
        # searchsorted for efficiency across long songs
        lo = int(np.searchsorted(temporal_positions, m.t0, side="left"))
        hi = int(np.searchsorted(temporal_positions, m.t1, side="left"))
        for k in range(lo, hi):
            t = float(temporal_positions[k])
            f0_val = float(f0[k])
            voiced = f0_val > 0.0
            frames.append({"t_s": t, "voiced": voiced, "value_hz": (f0_val if voiced else None)})
        out.append({"frames": frames})
    return out


def compute_duration_ratio(lab_morae: List[LabMora], score_morae: List[ScoreMoraTimed]) -> List[float]:
    out = []
    for m, s in zip(lab_morae, score_morae):
        score_duration = s.end_s - s.onset_s
        out.append((m.t1 - m.t0) / score_duration)
    return out


def compute_energy_envelope(
    x44k: np.ndarray, phrases: List[Phrase], lab_morae: List[LabMora],
) -> Dict[int, Dict[str, Any]]:
    """spec §4: hop = round(44100*FP/1000) 非重畳ブロックRMS。phrase 内で
    max 正規化。max==0 の phrase は not_extracted.

    [IMPL-CHOICE] block membership in a phrase = block START time within
    [phrase_lab_start_s, phrase_lab_end_s), where phrase_lab_start_s/end_s
    are derived from the phrase's own lab_indices' first t0 / last t1."""
    hop = round(TARGET_SR * FRAME_PERIOD_MS / 1000.0)
    n_blocks = len(x44k) // hop
    if n_blocks == 0:
        block_rms = np.zeros(0, dtype=np.float64)
    else:
        trimmed = x44k[: n_blocks * hop]
        blocks = trimmed.reshape(n_blocks, hop)
        block_rms = np.sqrt(np.mean(blocks.astype(np.float64) ** 2, axis=1))

    result: Dict[int, Dict[str, Any]] = {}
    for phrase in phrases:
        first_i, last_i = phrase.lab_indices[0], phrase.lab_indices[-1]
        phrase_start_s = lab_morae[first_i].t0
        phrase_end_s = lab_morae[last_i].t1
        k_lo = int(np.floor(phrase_start_s * TARGET_SR / hop))
        k_hi_excl = int(np.ceil(phrase_end_s * TARGET_SR / hop))
        k_lo = max(0, k_lo)
        k_hi_excl = min(n_blocks, k_hi_excl)
        selected_k = [k for k in range(k_lo, k_hi_excl) if phrase_start_s <= (k * hop / TARGET_SR) < phrase_end_s]
        if not selected_k:
            result[phrase.phrase_index] = {"status": "not_extracted", "reason": "no WORLD/energy blocks in phrase lab time range"}
            continue
        values = np.array([block_rms[k] for k in selected_k], dtype=np.float64)
        max_val = float(values.max())
        if max_val == 0.0:
            result[phrase.phrase_index] = {"status": "not_extracted", "reason": "max energy within phrase == 0"}
            continue
        blocks_out = [
            {"k": int(k), "t_s": float(k * hop / TARGET_SR), "value": float(block_rms[k] / max_val)}
            for k in selected_k
        ]
        result[phrase.phrase_index] = {"status": "extracted", "blocks": blocks_out}
    return result


def compute_attack_timing(lab_morae: List[LabMora], score_morae: List[ScoreMoraTimed], phrases: List[Phrase]) -> List[float]:
    offset_by_index: Dict[int, float] = {}
    for phrase in phrases:
        for i in phrase.lab_indices:
            offset_by_index[i] = phrase.offset_p_s
    out = []
    for i, (m, s) in enumerate(zip(lab_morae, score_morae)):
        out.append(m.t0 - s.onset_s - offset_by_index[i])
    return out


def compute_phrase_end_timing(lab_morae: List[LabMora], score_morae: List[ScoreMoraTimed], phrases: List[Phrase]) -> Dict[int, float]:
    out = {}
    for phrase in phrases:
        last_i = phrase.lab_indices[-1]
        out[phrase.phrase_index] = lab_morae[last_i].t1 - score_morae[last_i].end_s - phrase.offset_p_s
    return out


# ---------------------------------------------------------------------------
# Per-song extraction driver
# ---------------------------------------------------------------------------

def extract_song(
    song_dir: Path,
    song_id: str,
    *,
    frozen_split_pins: FrozenSplitPins,
    consumed_inputs_pins: ConsumedInputPins,
) -> Dict[str, Any]:
    """Extract one song. Only decodes audio if the WAV header check passes.
    Returns a per-song intermediate dict (see bundle assembly for full
    schema); on count_mismatch, channels are recorded as not_extracted with
    a reason, per spec §3 (this is NOT a stop condition by itself).

    `frozen_split_pins`/`consumed_inputs_pins` は必須 keyword-only 引数
    （PR #329 第2巡レビュー指摘「Enforce the frozen split in the assemble
    command」, P1, 採用対応）: 旧実装はこれらのゲートを `run_build()`/
    `_cmd_extract_song()` などの**呼び出し元だけ**が持っており、ゲートを
    持たない直接 `extract_song()` API 呼び出し（例: 別スクリプトからの
    import）は素通りして sealed 中間物を生成し得た。本関数はゲートを
    自身に内蔵することで、どの経路から呼ばれても decode/抽出前に必ず
    (1) `song_id` が凍結済み training∪validation に属すること、(2) 消費
    3入力（lab/musicxml/wav）の実バイトが pin と一致することを検証する。

    `frozen_split_pins`/`consumed_inputs_pins` は生の Sequence[str]/Mapping
    ではなく `FrozenSplitPins`/`ConsumedInputPins`（`load_training_
    validation_ids()`/`load_consumed_inputs_pins()` canonical loader のみが
    構築するべき不透明型）を要求する（PR #329 第3巡レビュー指摘2, P1,
    採用対応）。isinstance 検査で拒否する境界の Python 上の限界は両型の
    定義コメント（モジュール中盤）を参照。
    """
    if not isinstance(frozen_split_pins, FrozenSplitPins):
        raise ExtractorStopError(
            "extract_song(): frozen_split_pins must be a FrozenSplitPins instance produced by "
            "load_training_validation_ids() (the canonical pin-verified loader) — a raw "
            f"list/set/tuple is rejected fail-closed (got {type(frozen_split_pins).__name__})"
        )
    if not isinstance(consumed_inputs_pins, ConsumedInputPins):
        raise ExtractorStopError(
            "extract_song(): consumed_inputs_pins must be a ConsumedInputPins instance produced "
            "by load_consumed_inputs_pins() (the canonical pin-verified loader) — a raw dict is "
            f"rejected fail-closed (got {type(consumed_inputs_pins).__name__})"
        )
    _require_song_ids_within_frozen_split(
        [song_id], frozen_split_pins.frozen_allowed_ids, context="extract_song",
    )
    wav_path = song_dir / f"{song_id}_song.wav"
    lab_path = song_dir / f"{song_id}.lab"
    xml_path = song_dir / f"{song_id}.musicxml"
    for p in (wav_path, lab_path, xml_path):
        if not p.exists():
            raise ExtractorStopError(f"{song_id}: required input missing: {p}")

    # PR #329 第3巡レビュー指摘3（P2、採用対応）: 各ファイルを1回だけ
    # `read_bytes()` し、その同一バイト列に対して sha 照合 → parse/decode
    # の両方を行う（ファイル再 open の排除・TOCTOU 閉鎖 —
    # `speaker_map_builder.py` verified self-exec dispatch と同型の
    # read-once パターン）。
    wav_buf = wav_path.read_bytes()
    lab_buf = lab_path.read_bytes()
    xml_buf = xml_path.read_bytes()
    _require_consumed_input_bytes_match_bytes(
        song_id, wav_buf, lab_buf, xml_buf, consumed_inputs_pins, context="extract_song",
    )

    # STOPS the whole run on mismatch (raises) — validated against wav_buf,
    # the exact same bytes decode()d below.
    wav_header = check_wav_header_or_stop_bytes(wav_buf, str(wav_path))

    phonemes = parse_lab_bytes(lab_buf, str(lab_path))
    lab_morae = group_lab_to_morae_with_phrases(phonemes)

    tempo_events, raw_notes = parse_musicxml_bytes(xml_buf, str(xml_path))
    tempo_segments = build_tempo_segments(tempo_events)
    merged_score_morae = merge_score_morae(raw_notes, str(xml_path))
    score_morae = finalize_score_morae(merged_score_morae, tempo_segments, str(xml_path))

    alignment = align_song(lab_morae, score_morae)

    song_out: Dict[str, Any] = {
        "song_id": song_id,
        "alignment_status": alignment.status,
        "lab_mora_count": alignment.lab_mora_count,
        "score_mora_count": alignment.score_mora_count,
        "wav_header": {
            "channels": wav_header["channels"], "sample_rate": wav_header["sample_rate"],
            "bits_per_sample": wav_header["bits_per_sample"],
        },
    }
    if alignment.status != "aligned":
        song_out["reason"] = alignment.reason
        song_out["channels"] = {
            trait: {"status": "not_extracted", "reason": f"alignment_status={alignment.status}: {alignment.reason}"}
            for trait in EXTRACTED_TRAITS
        }
        return song_out

    x48k = load_wav_24bit_mono_48k_bytes(wav_buf, str(wav_path))
    x44k = resample_to_44100(x48k)
    f0, temporal_positions = compute_world_f0(x44k)

    relative_f0 = compute_relative_f0(lab_morae, f0, temporal_positions)

    relative_f0_out = []
    for i, m in enumerate(lab_morae):
        frames_raw = relative_f0[i]["frames"]
        score_hz = score_morae[i].hz
        frames = [
            {
                "t_s": fr["t_s"], "voiced": fr["voiced"],
                "value_hz": (fr["value_hz"] - score_hz) if fr["voiced"] else None,
            }
            for fr in frames_raw
        ]
        relative_f0_out.append({"mora_index": i, "frames": frames})

    duration_ratio = compute_duration_ratio(lab_morae, score_morae)
    energy_by_phrase = compute_energy_envelope(x44k, alignment.phrases, lab_morae)
    attack_timing = compute_attack_timing(lab_morae, score_morae, alignment.phrases)
    phrase_end_timing = compute_phrase_end_timing(lab_morae, score_morae, alignment.phrases)

    song_out["phrases"] = [
        {"phrase_index": p.phrase_index, "offset_p_s": p.offset_p_s, "lab_mora_indices": p.lab_indices}
        for p in alignment.phrases
    ]
    song_out["channels"] = {
        "relative_F0": {"status": "extracted", "morae": relative_f0_out},
        "duration_ratio": {
            "status": "extracted",
            "morae": [{"mora_index": i, "value": v} for i, v in enumerate(duration_ratio)],
        },
        "energy_envelope": {
            "status": "extracted",
            "phrases": [
                {"phrase_index": p_idx, **energy_by_phrase[p_idx]}
                for p_idx in sorted(energy_by_phrase)
            ],
        },
        "onset_offset": {
            "status": "extracted",
            "attack_timing": {
                "status": "extracted",
                "morae": [{"mora_index": i, "value_s": v} for i, v in enumerate(attack_timing)],
            },
            "phrase_end_timing": {
                "status": "extracted",
                "phrases": [{"phrase_index": p_idx, "value_s": phrase_end_timing[p_idx]} for p_idx in sorted(phrase_end_timing)],
            },
        },
    }
    return song_out


# ---------------------------------------------------------------------------
# Bundle assembly — spec §5
# ---------------------------------------------------------------------------

def build_lesson_record(lesson_id: str) -> Dict[str, Any]:
    return {
        "schema": SCHEMA_LESSON_RECORD,
        "lesson_id": lesson_id,
        "performance_source": (
            "PJS corpus ver1.1 (each pjsNNN_song.wav is one recorded performance from the "
            "corpus of 100 songs) — PJS README.md “100 short songs that the first "
            "author composed using the reading sentences as lyrics”; licensed CC BY-SA 4.0."
        ),
        "voice_source": SENTINEL_UNRESOLVED_EXTERNAL,
        "performance_author": SENTINEL_UNRESOLVED_EXTERNAL,
        "composition_source": (
            "Junya Koguchi (Meiji University), first author of the PJS corpus / paper, "
            "composed the 100 songs using the reading-sentence text as lyrics "
            "(PJS README.md “Description” / “Contributors”)."
        ),
        "recording_source": SENTINEL_UNRESOLVED_EXTERNAL,
        "extracted_traits": list(EXTRACTED_TRAITS),
        "explicitly_excluded_identity_traits": list(IDENTITY_EXCLUDED_TRAIT_VOCAB),
        "rights_manifest": (
            "scratchpad/run9_user_adjudication_pjs_lesson_freeze.md §1,§3 "
            "(scoped session re-acquisition + Rights-Gate-pending approval); "
            "PJS_corpus_ver1.1/README.md (CC BY-SA 4.0); "
            "raw zip sha256=683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca"
        ),
        "provenance_manifest": (
            "harness_work/h3b/e1_inventory.json (per-file sha256 provenance, 607 files); "
            "harness_work/h3b/h3b_freeze_record.json "
            "(expanded_corpus_identity_sha256 pin + spec/extractor sha256 freeze)"
        ),
    }


# [BYTE-PIN] These two literals (build_lesson_record()'s rights_manifest/
# provenance_manifest strings above, and RIGHTS_STATUS_DECLARATION below) are
# copied VERBATIM from the session workdir education_lesson_extractor.py —
# including their scratchpad-relative path wording — because the bundle
# byte-reproducibility requirement (run1/run2/run3 sha256 equality) covers
# these exact string literals too. They are historical-run-scoped wording
# (referring to the workdir path the original extraction ran from), not a
# claim about this repo's own layout; do not "fix" the paths to be
# repo-relative without re-deriving new pinned sha256 values end-to-end
# (spec/extraction-formula/serialization are unchanged, but any text literal
# baked into the bundle changes its bytes and therefore its sha256).
RIGHTS_STATUS_DECLARATION = (
    "Technique artifact generated under session-scoped User adjudication "
    "(run9_user_adjudication_pjs_lesson_freeze.md §1/§3: scoped approval for "
    "session-scoped PJS re-acquisition and Technique extraction; NOT an external-rights-holder "
    "attestation, NOT resolution of unresolved provenance, NOT an R9-G1 Rights Gate waiver). "
    "This artifact is technically generated and hash-frozen; it is NOT declared rights-clean or "
    "learning-eligible until the existing R9-G1 Rights Gate is independently satisfied."
)


def assemble_bundle(split: str, song_ids_sorted: Sequence[str], songs: Sequence[Dict[str, Any]], spec_sha256: str) -> Dict[str, Any]:
    songs_by_id = {s["song_id"]: s for s in songs}
    missing = set(song_ids_sorted) - set(songs_by_id)
    if missing:
        raise ExtractorStopError(f"assemble_bundle: missing song outputs for {sorted(missing)}")
    ordered_songs = [songs_by_id[sid] for sid in song_ids_sorted]
    count_mismatch_ids = [s["song_id"] for s in ordered_songs if s["alignment_status"] != "aligned"]
    return {
        "format": BUNDLE_FORMAT,
        "lesson_record": build_lesson_record(f"run9-h3b-technique-lesson/{split}"),
        "rights_status_declaration": RIGHTS_STATUS_DECLARATION,
        "channel_vocabulary_map": CHANNEL_VOCABULARY_MAP,
        "spec_freeze": {"spec_sha256": spec_sha256, "freeze_record_ref": "h3b_freeze_record.json"},
        "split": split,
        "songs": ordered_songs,
        "not_extracted_summary": {
            "count_mismatch_song_ids": sorted(count_mismatch_ids),
            "count_mismatch_count": len(count_mismatch_ids),
            "aligned_count": len(ordered_songs) - len(count_mismatch_ids),
        },
    }


def _serialize_bundle_json(obj: Dict[str, Any]) -> bytes:
    """spec §5 の直列化式（`json.dumps(obj, ensure_ascii=False, sort_keys=True,
    separators=(",", ":")) + "\\n"`、UTF-8）を bytes で返す。`write_bundle_
    json()`（非 atomic・単本書き込み）と `publish_bundle_pair()`（atomic・
    2本組書き込み）が共有する唯一の直列化実装 — 呼び出し経路が違っても
    バンドルの実バイトが一致することを保証する。"""
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def write_bundle_bytes(data: bytes, path: Path) -> None:
    """既に直列化済みのバンドルバイト列 `data` を `path` へ atomic に
    書き込む（`write_bundle_json()` の実体。`assemble` サブコマンドが
    pinned manifest 照合用に sha256 を先に計算した同一バイト列をそのまま
    書き込めるよう、直列化とファイル書き込みを分離する——PR #329 第4巡
    レビュー指摘, P1, 採用対応。二重直列化を避ける）。

    PR #329 第3巡レビュー指摘5（P2、採用対応）: 旧実装は `Path(path).
    write_bytes()` の直書きで、書き込み途中の失敗（ディスク枯渇・
    プロセス kill 等）で旧世代 artifact が破損した部分書き込みバイト列で
    上書きされ得た。`_atomic_write_bytes()`（`publish_bundle_pair()` が
    使うのと同じ staging+fsync ヘルパー）で同一ディレクトリ内の一意な
    staging ファイルへ書き切ってから `os.replace()` する構成へ変更する
    ——`path` の既存実バイトは最後の一括 rename までは一切変更されない
    ため、staging 段の失敗時は旧世代 artifact が無傷のまま残る。"""
    path = Path(path)
    tmp_path = _atomic_write_bytes(path, data)
    try:
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_bundle_json(obj: Dict[str, Any], path: Path) -> None:
    """単本バンドルの atomic 書き込み（`assemble` サブコマンド専用 —
    training/validation を対にして公開する `build` サブコマンドは atomic
    ペア公開が必要なため `publish_bundle_pair()` を使う、下記参照）。
    直列化 + 書き込みは `write_bundle_bytes()` へ委譲する。"""
    write_bundle_bytes(_serialize_bundle_json(obj), Path(path))


def _atomic_write_bytes(path: Path, data: bytes) -> Path:
    """`data` を `path` と同一ディレクトリ内の一意な staging ファイルへ
    書き切り fsync する（`path` 自体には一切触れない —— 最終名への
    `os.replace()` は呼び出し側の責務）。`speaker_map_builder._atomic_
    write_bytes()` と同型の staging+fsync パターン（run9 系は svp_rpe 側の
    `utils/atomic_io` を import しない独立構成のため、同型の最小実装を
    本 builder 内へ自足させる。PR #329 第1巡レビュー指摘2, P1, 採用対応）。

    失敗時（`BaseException` 含む）は staging ファイルを best-effort で
    削除してから re-raise する — `path` の既存実バイトには一切触れない。
    複数バンドルの staging をすべて済ませてから `os.replace()` を一括
    実行することで、呼び出し側は複数ファイルの atomic ペア公開を構築
    できる（`publish_bundle_pair()` 参照）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return tmp_path


def _backup_existing(path: Path) -> Optional[Path]:
    """`path` に既存ファイルがあれば同一ディレクトリ内の一意な backup 名へ
    `os.replace()`（rename、同一ファイルシステム内であれば atomic）で
    退避し、その backup パスを返す。存在しなければ `None` を返す
    （`publish_bundle_pair()` のロールバック用スナップショット。PR #329
    第2巡レビュー指摘2-2, P1, 採用対応）。

    `mkstemp()` は退避先の空プレースホルダファイルを作成してから
    `os.replace()` する構成のため、その後の `os.replace()` 自体が失敗
    （PR #329 第3巡レビュー指摘4, P1, 採用対応 — 例: `path` がディレクトリ
    で rename が構造的に失敗するケース）すると、空プレースホルダだけが
    孤児として残置され得た。`os.replace()` の失敗時は、この関数自身が
    作った空プレースホルダを best-effort で削除してから re-raise する
    ——`publish_bundle_pair()` 側のロールバックが「残骸なし」を達成する
    ためには、本関数自身が自分の中間生成物の後始末をする必要がある。"""
    if not path.exists():
        return None
    fd, backup_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".prevgen.tmp")
    os.close(fd)
    backup_path = Path(backup_name)
    try:
        os.replace(path, backup_path)
    except BaseException:
        try:
            backup_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return backup_path


def _rollback_to_backup(path: Path, backup: Optional[Path]) -> None:
    """`publish_bundle_pair()` の失敗時ロールバック: `backup` があれば
    `path` へ戻す（旧世代の復元、`_backup_existing()` の逆操作）。
    `backup` が `None`（= publish 開始前は `path` が存在しなかった）なら、
    途中まで置換された可能性のある `path` を削除する（新世代のみが部分的
    にでも観測される窓を閉じる）。いずれも best-effort（`OSError` は
    握りつぶす —— ロールバック自体の失敗で本来の例外を握りつぶさない
    ため、呼び出し側が元の例外を re-raise する）。"""
    try:
        if backup is not None:
            os.replace(backup, path)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def _discard_backup(backup: Optional[Path]) -> None:
    """publish 成功時、もう不要になった旧世代 backup を破棄する
    （best-effort）。"""
    if backup is not None:
        try:
            backup.unlink(missing_ok=True)
        except OSError:
            pass


def publish_bundle_pair(
    training_path: Path, training_bytes: bytes, validation_path: Path, validation_bytes: bytes,
) -> None:
    """training/validation バンドル2本を atomic ペアとして公開する
    （PR #329 第1巡レビュー指摘2 + 第2巡レビュー指摘2-2、いずれも P1、
    採用対応）。

    旧実装は `write_bundle_json(training_bundle, training_out)` の後に
    `write_bundle_json(validation_bundle, validation_out)` を実行して
    おり、training の書き込み成功後に validation の書き込みが失敗すると
    「新世代 training + 旧世代（または欠落）validation」という混合世代
    ペアが最終出力ディレクトリに観測され得た（第1巡指摘2）。

    第1巡修正は2本とも staging（`_atomic_write_bytes()`）へ書き切って
    から両方成功時のみ `os.replace()` する構成にしたが、**2本の
    `os.replace()` 自体**は依然として atomic なペアではなかった——
    training の rename 成功後に validation の rename が失敗すると、
    新世代 training + 旧世代（または欠落）validation という同型の混合
    世代が観測され得た上、validation の staging ファイルも残置され得た
    （第2巡指摘2-2、新鮮な証跡）。

    本関数は staging 完了後、公開前に両ファイルの**既存内容を
    `_backup_existing()` で退避**してから2本の `os.replace()` を実行する。
    退避2回 + rename2回の**4操作すべてを同一の `BaseException` ロール
    バック/cleanup トランザクションに含める**（PR #329 第3巡レビュー
    指摘4, P1, 採用対応）——第1巡/第2巡修正では2回の `_backup_existing()`
    呼び出し自体が `try` の**外側**にあり、1本目（training）の退避が
    成功した直後に2本目（validation）の退避が失敗する経路（例:
    `validation_path` が通常ファイルでなくディレクトリで `os.replace()`
    が構造的に失敗するケース）で、training は既に backup 名へ rename
    済み（= 最終名 `training_path` が一時的に欠落した状態）にもかかわらず、
    その例外がロールバック処理を一切経由せず `publish_bundle_pair()` の
    外へそのまま伝播していた——training の復元も staging（`training_tmp`/
    `validation_tmp`）の破棄も行われず、「公開前ペアが無傷のまま残る」
    という本関数の契約が破られる具体的な穴があった。
    `BaseException` を含むいずれかの退避/rename の失敗時は、**両方の**
    最終名を `_rollback_to_backup()` で publish 開始前の状態（backup が
    取れていれば旧世代のバイトへ、取れていなければ未存在に）へ復元し、
    残った staging ファイルも破棄したうえで re-raise する —— 「training
    だけ新世代・validation は旧世代または欠落」という混合世代はもちろん、
    「training だけ backup 名へ退避されたまま最終名が欠落」という部分
    退避の窓も最終的に観測されることはない。両方成功時は退避した backup
    を破棄する。
    """
    training_tmp = _atomic_write_bytes(training_path, training_bytes)
    try:
        validation_tmp = _atomic_write_bytes(validation_path, validation_bytes)
    except BaseException:
        try:
            training_tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    training_backup: Optional[Path] = None
    validation_backup: Optional[Path] = None
    try:
        training_backup = _backup_existing(training_path)
        validation_backup = _backup_existing(validation_path)
        os.replace(training_tmp, training_path)
        os.replace(validation_tmp, validation_path)
    except BaseException:
        _rollback_to_backup(training_path, training_backup)
        _rollback_to_backup(validation_path, validation_backup)
        try:
            training_tmp.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            validation_tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    else:
        _discard_backup(training_backup)
        _discard_backup(validation_backup)


# ---------------------------------------------------------------------------
# split manifest reading (row_ids.training / row_ids.validation ONLY —
# row_ids.sealed_holdout is never accessed by this function or any other
# code path in this file, per spec §7 / 裁定 §2). PR #329 第1巡レビュー
# 指摘1（P1、採用対応）: `run9_schema.load_pinned_practice_split_
# manifest()` 経由でのみ manifest を読む（RUN9_CONTRACT.yaml
# practice_audio_split_manifest_sha pin との read-once sha256 照合 +
# `validate_practice_split_manifest()` の3集合非交差検証）。
# ---------------------------------------------------------------------------

_PRACTICE_SPLIT_EXPECTED_COUNTS = {"training": 70, "validation": 15, "sealed_holdout": 15}


def load_training_validation_ids(
    split_manifest_path: Optional[Path] = None,
    *,
    contract_path: Optional[Path] = None,
) -> FrozenSplitPins:
    """training/validation row_ids を pin 検証済みで読み、`FrozenSplitPins`
    （canonical loader 専用の不透明型、PR #329 第3巡レビュー指摘2, P1,
    採用対応）として返す。`training_ids, validation_ids = load_training_
    validation_ids(...)` という既存の unpack 慣用句は `FrozenSplitPins.
    __iter__` により引き続き成立する——`extract_song()` へ渡す際は
    unpack せず本関数の戻り値をそのまま `frozen_split_pins=` へ渡すこと
    （unpack 後の生 tuple は isinstance 検査で拒否される）。

    旧実装は `split_manifest_path` が指す任意の JSON を無検証で
    `json.loads()` していたため、(a) sealed ID が training/validation へ
    混入した manifest、(b) 構造が壊れた manifest、(c) 件数が PJS corpus
    ver1.1 の固定分割（training 70 / validation 15 / sealed_holdout 15,
    裁定 §1）と食い違う manifest のいずれを渡されても、そのまま
    decode/抽出へ進んでしまう穴があった（PR #329 第1巡レビュー指摘1,
    P1, 採用対応）。

    本関数は `run9_schema.load_pinned_practice_split_manifest()`（`RUN9_
    CONTRACT.yaml` の `practice_audio_split_manifest_sha` pin との
    read-once sha256 照合 + `validate_practice_split_manifest()` の構造/
    3集合非交差検証、他の `load_pinned_*` 系と同型の3層防御）経由での
    み manifest を読む —— `split_manifest_path` が pin 値と byte-identical
    でない限り fail-closed で拒否するため、sealed ID 混入・改ざん
    manifest を別パスへ差し替えて渡す迂回は成立しない。読み込み後、
    training/validation/sealed_holdout の件数を機械強制する。

    `split_manifest_path`/`contract_path` を省略すると、それぞれ正典
    `DEFAULT_SPLIT_MANIFEST_PATH`（= `inputs/practice_audio_split_
    manifest.json`）/ `run9_schema.RUN9_CONTRACT_YAML_PATH` を使う。
    """
    effective_manifest_path = (
        split_manifest_path if split_manifest_path is not None else DEFAULT_SPLIT_MANIFEST_PATH
    )
    effective_contract_path = contract_path if contract_path is not None else run9_schema.RUN9_CONTRACT_YAML_PATH
    contract = run9_schema.load_run9_contract_from_yaml_path(effective_contract_path)
    data = run9_schema.load_pinned_practice_split_manifest(
        contract, manifest_path=effective_manifest_path, contract_path=effective_contract_path,
    )
    row_ids = data["row_ids"]
    training_ids = sorted(row_ids["training"])
    validation_ids = sorted(row_ids["validation"])
    sealed_holdout_ids = row_ids["sealed_holdout"]
    actual_counts = {
        "training": len(training_ids), "validation": len(validation_ids),
        "sealed_holdout": len(sealed_holdout_ids),
    }
    if actual_counts != _PRACTICE_SPLIT_EXPECTED_COUNTS:
        raise ExtractorStopError(
            "load_training_validation_ids(): split manifest row_ids counts must be exactly "
            f"{_PRACTICE_SPLIT_EXPECTED_COUNTS} (PJS corpus ver1.1 100-song fixed split, 裁定 §1), "
            f"got {actual_counts} — stop, no partial/renegotiated split accepted"
        )
    return FrozenSplitPins(training_ids=tuple(training_ids), validation_ids=tuple(validation_ids))


def load_consumed_inputs_pins(
    consumed_inputs_manifest_path: Optional[Path] = None,
    *,
    contract_path: Optional[Path] = None,
) -> ConsumedInputPins:
    """consumed-inputs per-file sha256 pin（pin 検証済み）を読み、
    `ConsumedInputPins`（canonical loader 専用の不透明型、PR #329 第3巡
    レビュー指摘2, P1, 採用対応）として返す。`load_training_validation_
    ids()` と同型: `run9_schema.load_pinned_consumed_inputs_manifest()`
    （`RUN9_CONTRACT.yaml` の `pjs_consumed_inputs_manifest_sha` pin との
    read-once sha256 照合 + `validate_pjs_consumed_inputs_manifest()` の
    構造/件数/値整形式検証）経由でのみ manifest を読む。`ConsumedInputPins.
    pins` は `{song_id: {"lab_sha256": ..., "musicxml_sha256": ...,
    "wav_sha256": ...}}`（training70+validation15=85曲分、sealed_holdout
    は含まれない）。

    `consumed_inputs_manifest_path`/`contract_path` を省略すると、それぞれ
    正典 `DEFAULT_CONSUMED_INPUTS_MANIFEST_PATH`（=
    `inputs/pjs_consumed_inputs_sha256.json`）/
    `run9_schema.RUN9_CONTRACT_YAML_PATH` を使う。
    """
    effective_manifest_path = (
        consumed_inputs_manifest_path
        if consumed_inputs_manifest_path is not None
        else DEFAULT_CONSUMED_INPUTS_MANIFEST_PATH
    )
    effective_contract_path = contract_path if contract_path is not None else run9_schema.RUN9_CONTRACT_YAML_PATH
    contract = run9_schema.load_run9_contract_from_yaml_path(effective_contract_path)
    data = run9_schema.load_pinned_consumed_inputs_manifest(
        contract, manifest_path=effective_manifest_path, contract_path=effective_contract_path,
    )
    return ConsumedInputPins(pins=data["songs"])


def _require_song_ids_within_frozen_split(
    song_ids: Sequence[str], allowed_ids: Sequence[str], *, context: str,
) -> None:
    """`song_ids` の全要素が凍結済み `allowed_ids`（= training ∪
    validation）に属することを decode/抽出前に検証する（PR #329 第1巡
    レビュー指摘1, P1, 採用対応）。集合外の id（sealed_holdout を含む）が
    1件でもあれば `ExtractorStopError` で即停止し、decode/抽出は一切
    実行しない——`run_build()`/`extract-song` CLI の両方がこのゲートを
    共有する単一実装（別々に実装すると将来どちらか一方だけが改修されて
    判定が食い違う穴を防ぐ）。`extract_song()` 自身もこのゲートを内蔵
    する（PR #329 第2巡レビュー指摘「assemble を強化する」対応 —
    「CLI だけがゲートを持ち、直接 API 呼び出しは素通り」という穴を
    関数本体側で閉じる）。"""
    allowed = set(allowed_ids)
    offenders = sorted(set(song_ids) - allowed)
    if offenders:
        raise ExtractorStopError(
            f"{context}: song_id(s) not a member of the pinned training ∪ validation split "
            f"(sealed_holdout song_ids — and any id outside the frozen split — are rejected "
            f"fail-closed before decode/extraction): {offenders}"
        )


def _require_exact_frozen_split_membership(
    song_ids: Sequence[str], expected_ids: Sequence[str], *, split: str, context: str,
) -> None:
    """`song_ids` が凍結済み `split`（"training"/"validation"）の ID 集合と
    **厳密に一致**することを検証する（PR #329 第2巡レビュー指摘「Enforce
    the frozen split in the assemble command」, P1, 採用対応）。

    `_require_song_ids_within_frozen_split()` は「song_ids ⊆ training∪
    validation」という部分集合検証のみを行う——`assemble` サブコマンドが
    要求する「song_ids == 選択 split の凍結 ID 集合」という厳密集合一致
    までは強制しないため、(a) sealed_holdout ID の混入、(b) 凍結集合からの
    欠落、(c) 凍結集合を超える過剰指定、(d) training/validation の
    取り違え（例: --split training で validation の一部だけを渡す）の
    いずれも通り得た——ゲートを持たない直接 `extract_song()` API 呼び出し
    等で生成された sealed 中間物が、任意の ID リストとともに training/
    validation バンドルへ梱包され得る具体的経路だった。

    不一致（上記いずれか1件でも）があれば、中間物ファイルを1つも読む前に
    `ExtractorStopError` で即停止する。"""
    requested = set(song_ids)
    expected = set(expected_ids)
    missing = sorted(expected - requested)
    unexpected = sorted(requested - expected)
    if missing or unexpected:
        raise ExtractorStopError(
            f"{context}: requested song_ids do not exactly match the pinned frozen {split!r} "
            f"split (missing={missing}, unexpected={unexpected} — 'unexpected' includes any "
            "sealed_holdout id, any id from the other split, or any unknown id; assemble accepts "
            "only the full, exact frozen ID set for the declared --split, verified before reading "
            "any intermediates)"
        )


def _require_no_duplicate_song_ids(song_ids: Sequence[str], *, context: str) -> None:
    """`song_ids`（`--song-ids-json` から読み込んだ生リスト）に重複が無い
    ことを、`set()` を経由するいかなる検証（`_require_exact_frozen_split_
    membership()` を含む——同関数は内部で `set(song_ids)` へ変換するため
    重複を検出できない）よりも前に確認する（PR #329 第3巡レビュー指摘1,
    P1, 採用対応）。

    旧実装は `song_ids_sorted = sorted(song_ids)` の時点で重複を保持した
    まま以降の処理へ進み、`_require_exact_frozen_split_membership()` の
    `set(song_ids)` 変換で重複が黙って吸収されていた——結果、凍結 split
    の ID 集合と（重複を除けば）完全一致するリストであっても、
    `assemble_bundle()` の `ordered_songs = [songs_by_id[sid] for sid in
    song_ids_sorted]` は `song_ids_sorted` の重複をそのまま辿るため、
    同一曲が2回以上 bundle の `"songs"` 配列へ混入し得た。

    重複が1件でもあれば、中間物ファイルを1つも読む前に `ExtractorStopError`
    で即停止する（重複した song_id を列挙する）。"""
    seen: Dict[str, int] = {}
    for sid in song_ids:
        seen[sid] = seen.get(sid, 0) + 1
    duplicates = sorted(sid for sid, count in seen.items() if count > 1)
    if duplicates:
        raise ExtractorStopError(
            f"{context}: --song-ids-json contains duplicate song_id(s) (each song_id must appear "
            f"exactly once; the frozen split membership check that follows would otherwise silently "
            f"absorb duplicates via set()): {duplicates}"
        )


def _require_consumed_input_bytes_match_bytes(
    song_id: str,
    wav_buf: bytes,
    lab_buf: bytes,
    xml_buf: bytes,
    consumed_inputs_pins: "ConsumedInputPins",
    *,
    context: str,
) -> None:
    """`song_id` が消費する3入力（lab/musicxml/wav）の実バイト sha256 が
    `consumed_inputs_pins`（`load_consumed_inputs_pins()` が返す pin 検証
    済み `ConsumedInputPins`）の値と一致することを decode 前に検証する
    （PR #329 第2巡レビュー指摘2-4, P1, 採用対応）。`donor_bank_lab.py` の
    `corpus_identity_hash()` は `.lab` + 対の `_song.wav` のみを被覆し
    musicxml を被覆しないため、musicxml 単体の改ざん（duration/F0 lesson
    を変え得る）が検出されない穴があった——本関数はその穴を builder 消費
    入力3種の完全被覆で閉じる。pin に song_id のエントリが無い場合
    （sealed_holdout や凍結集合外の id）も同じく fail-closed で拒否する
    （通常は `_require_song_ids_within_frozen_split()`/
    `_require_exact_frozen_split_membership()` が先に拒否するため二重
    防御）。不一致は `ExtractorStopError` で即停止し、decode は一切
    実行しない。

    PR #329 第3巡レビュー指摘3（P2、採用対応、TOCTOU 閉鎖）: `wav_buf`/
    `lab_buf`/`xml_buf` は呼び出し元（`extract_song()`）が既に1回だけ
    `read_bytes()` 済みのバッファを渡す——本関数はそのバッファから直接
    sha256 を算出するのみで、ファイルを再 open しない（旧実装は
    `path.read_bytes()` をここで独自に行っており、`extract_song()` が
    その後さらに別途ファイルを開いて parse/decode する構成だったため、
    sha 照合とその後の parse/decode が異なる読み取り時点のバイト列に
    対して行われ得た）。"""
    pins = consumed_inputs_pins.get(song_id)
    if pins is None:
        raise ExtractorStopError(
            f"{context}: {song_id} has no pinned consumed-input sha256 entry in "
            "pjs_consumed_inputs_sha256.json (sealed_holdout ids and any id outside the frozen "
            "training/validation split are never present in this pin) — extraction is "
            "fail-closed refused without a matching pinned entry"
        )
    for buf, key, name in (
        (lab_buf, "lab_sha256", f"{song_id}.lab"),
        (xml_buf, "musicxml_sha256", f"{song_id}.musicxml"),
        (wav_buf, "wav_sha256", f"{song_id}_song.wav"),
    ):
        expected = pins[key]
        actual = hashlib.sha256(buf).hexdigest()
        if actual != expected:
            raise ExtractorStopError(
                f"{context}: {song_id} {name} の実バイト sha256 ({actual!r}) が "
                f"pjs_consumed_inputs_sha256.json の {key} pin 値 ({expected!r}) と一致しない — "
                "改ざんされた corpus 入力（musicxml を含む）は decode 前に fail-closed で拒否する"
            )


# ---------------------------------------------------------------------------
# --out corpus-alias 拒否（PR #329 第4巡レビュー指摘, P1, 採用対応）:
# `--out` が対象曲の消費入力（wav/lab/musicxml）や split manifest/contract/
# consumed-inputs pin ファイルと同一実体（symlink 経由の alias 含む）を
# 指すと、抽出後の JSON 書き込みが pin 済み corpus 入力を破壊し得た——
# `speaker_map_builder.py` の `_resolve_alias_conflict()`/
# `_check_out_does_not_alias_inputs()`（PR #328 Codex レビュー第2巡指摘4/
# 第8巡指摘16、いずれも P1、採用対応で確立済み前例）と同型のロジックを
# 本ビルダーの3つの出力サブコマンド（extract-song/assemble/probe-header）
# へ導入する。
#
# `speaker_map_builder._resolve_alias_conflict()` は `Path.resolve()`
# （symlink 解決込みの絶対化）のみで比較していたが、本実装はそれに加えて
# `os.path.abspath()`（symlink 未解決の lexical 絶対化）でも比較する二重
# チェックとする——`out_path` の親ディレクトリが存在しない場合や
# `Path.resolve(strict=False)` の挙動がプラットフォーム/Python バージョン
# 間で異なり得ることに依存しない、filesystem 状態非依存の防御を追加する
# （Fable 設計方針: 「lexical + resolved の二重」）。
# ---------------------------------------------------------------------------

def _lexical_absolute_path(path: Path) -> Path:
    """symlink 解決を行わない、絶対化のみの比較用パス。`os.path.abspath()`
    は文字列操作のみで存在しないパスにも安全に適用できる——`Path.resolve()`
    と異なりファイルシステムへ一切アクセスしない比較軸を提供する。"""
    return Path(os.path.abspath(os.fspath(path)))


def _resolve_output_alias_conflict(
    out_path: Path, protected_paths: Sequence[Path],
) -> Optional[Path]:
    """`out_path` が `protected_paths` のいずれかと (a) 同一実体（symlink
    経由の alias 含む、`Path.resolve()` 比較）または (b) 同一 lexical 絶対
    パス（symlink 未解決、`os.path.abspath()` 比較）であれば、その
    protected path を返す（衝突でなければ `None`）。

    3つの出力コマンド（`_cmd_extract_song`/`_cmd_assemble`/
    `_cmd_probe_header`）が共有する alias 判定ロジックの単一実装
    ——`speaker_map_builder._resolve_alias_conflict()` と同じく、別々に
    実装すると将来どれか1つだけが改修されて判定が食い違う穴を防ぐ。"""
    out_lexical = _lexical_absolute_path(out_path)
    out_resolved = out_path.resolve()
    for protected in protected_paths:
        if out_lexical == _lexical_absolute_path(protected):
            return protected
        if out_resolved == protected.resolve():
            return protected
    return None


def _require_out_does_not_alias_protected_paths(
    out_path: Path, protected_paths: Sequence[Path], *, context: str,
) -> None:
    """`out_path` が `protected_paths`（当該曲の消費3入力・split
    manifest・contract・consumed-inputs pin ファイル・spec 等、コマンドが
    これから読む/読んだ全入力）のいずれとも同一実体でないことを、
    **読み取り前の preflight として** 検証する（PR #329 第4巡レビュー
    指摘, P1, 採用対応）。1件でも衝突すれば `ExtractorStopError` で即座に
    拒否し、以降の読み取り・decode・書き込みは一切実行しない——`--out` が
    corpus 入力や pin 済み manifest を指す（直接指定・symlink 経由の
    alias いずれも）ことで、抽出後の JSON 書き込みが pin 済み入力を
    破壊する経路を書き込み前に閉じる。"""
    conflict = _resolve_output_alias_conflict(out_path, protected_paths)
    if conflict is not None:
        raise ExtractorStopError(
            f"{context}: --out ({out_path}) resolves to the same file as a protected input path "
            f"({conflict}), resolved={out_path.resolve()}, lexical={_lexical_absolute_path(out_path)} "
            "— fail-closed 拒否（pin 済み corpus 入力・split manifest・contract・consumed-inputs "
            "pin ファイル等への破壊的書き込みは、直接指定・symlink 経由の alias いずれも拒否する）"
        )


def _extract_song_protected_paths(args: argparse.Namespace) -> List[Path]:
    """`extract-song` が読む全入力パス（当該曲の消費3入力 + split
    manifest + contract + consumed-inputs pin ファイル + freeze record +
    spec）。`--out` の alias preflight 対象集合。

    `getattr(..., None)` 経由で読む（`Namespace.attr` の直接参照ではなく）
    ——実 CLI（argparse）は全属性を必ず設定するが、テスト層が直接構築する
    最小 `argparse.Namespace`（他の属性のみを設定した fixture）に対しても
    `AttributeError` ではなく「未指定 = 既定値」として振る舞う方が、
    本関数を preflight 目的で単体呼び出しする側にとって安全。"""
    song_dir = Path(args.corpus_root) / args.song_id
    raw_split_manifest = getattr(args, "split_manifest", None)
    split_manifest_path = Path(raw_split_manifest) if raw_split_manifest else DEFAULT_SPLIT_MANIFEST_PATH
    raw_contract_path = getattr(args, "contract_path", None)
    contract_path = Path(raw_contract_path) if raw_contract_path else run9_schema.RUN9_CONTRACT_YAML_PATH
    raw_consumed_inputs_manifest = getattr(args, "consumed_inputs_manifest", None)
    consumed_inputs_manifest_path = (
        Path(raw_consumed_inputs_manifest)
        if raw_consumed_inputs_manifest
        else DEFAULT_CONSUMED_INPUTS_MANIFEST_PATH
    )
    raw_freeze_record = getattr(args, "freeze_record", None)
    freeze_record_path = Path(raw_freeze_record) if raw_freeze_record else DEFAULT_FREEZE_RECORD_PATH
    raw_spec_path = getattr(args, "spec_path", None)
    spec_path = Path(raw_spec_path) if raw_spec_path else DEFAULT_SPEC_PATH
    return [
        song_dir / f"{args.song_id}_song.wav",
        song_dir / f"{args.song_id}.lab",
        song_dir / f"{args.song_id}.musicxml",
        split_manifest_path,
        contract_path,
        consumed_inputs_manifest_path,
        freeze_record_path,
        spec_path,
    ]


def _probe_header_protected_paths(args: argparse.Namespace) -> List[Path]:
    """`probe-header` が読む全入力パス（各 `--song-ids` の WAV + split
    manifest + contract）。`--out` の alias preflight 対象集合。"""
    split_manifest_path = Path(args.split_manifest) if args.split_manifest else DEFAULT_SPLIT_MANIFEST_PATH
    contract_path = Path(args.contract_path) if args.contract_path else run9_schema.RUN9_CONTRACT_YAML_PATH
    paths: List[Path] = [split_manifest_path, contract_path]
    for song_id in args.song_ids:
        paths.append(Path(args.corpus_root) / song_id / f"{song_id}_song.wav")
    return paths


def _assemble_protected_paths(args: argparse.Namespace, song_ids_sorted: Sequence[str]) -> List[Path]:
    """`assemble` が読む全入力パス（spec + split manifest + contract +
    `--song-ids-json` + 選択 split の全中間物 JSON）。`--out` の alias
    preflight 対象集合。`song_ids_sorted` は凍結 split 厳密一致検証を
    通過済みの確定リストを渡すこと（検証前の生リストを渡すと、まだ
    正規化されていない song_id から中間物パスを構築してしまう）。"""
    split_manifest_path = Path(args.split_manifest) if args.split_manifest else DEFAULT_SPLIT_MANIFEST_PATH
    contract_path = Path(args.contract_path) if args.contract_path else run9_schema.RUN9_CONTRACT_YAML_PATH
    spec_path = Path(args.spec_path) if args.spec_path else DEFAULT_SPEC_PATH
    paths: List[Path] = [spec_path, split_manifest_path, contract_path, Path(args.song_ids_json)]
    paths.extend(Path(args.intermediates_dir) / f"{sid}.json" for sid in song_ids_sorted)
    return paths


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_probe_header(args: argparse.Namespace) -> int:
    # PR #329 第3巡レビュー指摘6（P2、採用対応、凍結 split ゲート）:
    # `probe-header` は「メタデータのみ・decode なし」であっても、WAV
    # ファイルを1バイトでも open する処理そのものが 裁定 §2「sealed は
    # 完全性 hash と ID 確認以外の処理禁止」に抵触し得る——本コマンドは
    # ID 確認そのもの（audio_format/channels/sample_rate/bits_per_sample
    # の適合可否の報告）を目的とするが、`--song-ids` に sealed_holdout や
    # 凍結 training∪validation split 外の song_id が1件でも含まれていれば、
    # いずれの WAV も open する前に全件一括で拒否する（他のゲートと同型:
    # 一部だけ open してから拒否、という中途半端な状態を作らない）。
    split_manifest_path = Path(args.split_manifest) if args.split_manifest else None
    contract_path = Path(args.contract_path) if args.contract_path else None
    frozen_split_pins = load_training_validation_ids(split_manifest_path, contract_path=contract_path)
    _require_song_ids_within_frozen_split(
        args.song_ids, frozen_split_pins.frozen_allowed_ids, context="probe-header",
    )
    # PR #329 第4巡レビュー指摘（P1、採用対応）: `--out` が対象曲の WAV や
    # split manifest/contract と同一実体（symlink 経由の alias 含む）を
    # 指す場合、いずれの WAV も open する前に一括拒否する。
    _require_out_does_not_alias_protected_paths(
        Path(args.out), _probe_header_protected_paths(args), context="probe-header",
    )

    out = {}
    for song_id in args.song_ids:
        wav_path = Path(args.corpus_root) / song_id / f"{song_id}_song.wav"
        try:
            hdr = read_wav_fmt_header(wav_path)
            out[song_id] = {
                "audio_format": hdr["audio_format"], "channels": hdr["channels"],
                "sample_rate": hdr["sample_rate"], "bits_per_sample": hdr["bits_per_sample"],
                "byte_rate": hdr["byte_rate"], "block_align": hdr["block_align"],
                "matches_requirement": (
                    hdr["audio_format"] == REQUIRED_AUDIO_FORMAT_PCM
                    and hdr["channels"] == REQUIRED_CHANNELS
                    and hdr["sample_rate"] == REQUIRED_SAMPLE_RATE
                    and hdr["bits_per_sample"] == REQUIRED_BITS_PER_SAMPLE
                ),
            }
        except WavHeaderError as e:
            out[song_id] = {"error": str(e)}
    Path(args.out).write_text(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    n_match = sum(1 for v in out.values() if v.get("matches_requirement"))
    print(f"probe-header: {n_match}/{len(out)} match required 24-bit/mono/48000Hz header", file=sys.stderr)
    return 0


def _cmd_extract_song(args: argparse.Namespace) -> int:
    # PR #329 第4巡レビュー指摘（P1、採用対応）: `--out` が対象曲の消費3
    # 入力（wav/lab/musicxml、symlink 経由の alias 含む）や split
    # manifest/contract/consumed-inputs pin/freeze record/spec のいずれかと
    # 同一実体を指す場合、freeze self-check を含むいかなる読み取りより前に
    # 一括拒否する——抽出後の JSON 書き込みが pin 済み corpus 入力を破壊
    # する経路を書き込み前に閉じる。
    _require_out_does_not_alias_protected_paths(
        Path(args.out), _extract_song_protected_paths(args), context="extract-song",
    )
    freeze_selfcheck(Path(args.freeze_record), Path(args.spec_path))
    # PR #329 第1巡レビュー指摘1（P1、採用対応）: `--song-id` に sealed_
    # holdout や split manifest に一切現れない任意 ID を渡しても、旧実装は
    # そのまま decode/抽出へ進んでいた。凍結済み training∪validation 集合
    # 外の song_id は decode 前に fail-closed で拒否する。
    split_manifest_path = Path(args.split_manifest) if args.split_manifest else None
    contract_path = Path(args.contract_path) if args.contract_path else None
    frozen_split_pins = load_training_validation_ids(
        split_manifest_path, contract_path=contract_path,
    )
    _require_song_ids_within_frozen_split(
        [args.song_id], frozen_split_pins.frozen_allowed_ids, context="extract-song",
    )
    # PR #329 第2巡レビュー指摘2-4（P1、採用対応）: 消費3入力（lab/
    # musicxml/wav）の実バイト sha256 を decode 前に照合する。
    consumed_inputs_manifest_path = (
        Path(args.consumed_inputs_manifest) if args.consumed_inputs_manifest else None
    )
    consumed_inputs_pins = load_consumed_inputs_pins(
        consumed_inputs_manifest_path, contract_path=contract_path,
    )
    song_dir = Path(args.corpus_root) / args.song_id
    result = extract_song(
        song_dir, args.song_id,
        frozen_split_pins=frozen_split_pins, consumed_inputs_pins=consumed_inputs_pins,
    )
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(f"extract-song {args.song_id}: alignment_status={result['alignment_status']}", file=sys.stderr)
    return 0


def _cmd_assemble(args: argparse.Namespace) -> int:
    # PR #329 第2巡レビュー指摘「Enforce the frozen split in the assemble
    # command」（P1、採用対応）: 中間物を1つも読む前に凍結 split をロード
    # し、要求 ID 集合が選択 split（training なら凍結70件、validation
    # なら凍結15件）と厳密集合一致することを検証する。不一致（sealed 混入
    # ・欠落・過剰・未知 ID いずれも）は decode/読み込み前に拒否する。
    split_manifest_path = Path(args.split_manifest) if args.split_manifest else None
    contract_path = Path(args.contract_path) if args.contract_path else None
    frozen_split_pins = load_training_validation_ids(
        split_manifest_path, contract_path=contract_path,
    )
    expected_ids = (
        frozen_split_pins.training_ids if args.split == "training" else frozen_split_pins.validation_ids
    )

    song_ids = json.loads(Path(args.song_ids_json).read_text(encoding="utf-8"))
    # PR #329 第3巡レビュー指摘1（P1、採用対応）: `--song-ids-json` の
    # set 化（`_require_exact_frozen_split_membership()` 内部の `set(
    # song_ids)`）より前に重複を明示検出して拒否する——set 化は重複を
    # 黙って吸収するため、旧実装は凍結 split と重複除けば完全一致する
    # リストであっても、`assemble_bundle()` の `ordered_songs` 生成が
    # `song_ids_sorted` の重複をそのまま辿ることで、同一曲が2回以上
    # bundle の `"songs"` 配列へ混入し得た。
    _require_no_duplicate_song_ids(song_ids, context="assemble")
    song_ids_sorted = sorted(song_ids)
    _require_exact_frozen_split_membership(
        song_ids_sorted, expected_ids, split=args.split, context="assemble",
    )
    # PR #329 第4巡レビュー指摘（P1、採用対応）: `--out` が spec/split
    # manifest/contract/`--song-ids-json`/選択 split の全中間物 JSON の
    # いずれかと同一実体（symlink 経由の alias 含む）を指す場合、中間物を
    # 1つも読む前に一括拒否する。
    _require_out_does_not_alias_protected_paths(
        Path(args.out), _assemble_protected_paths(args, song_ids_sorted), context="assemble",
    )

    songs = []
    for sid in song_ids_sorted:
        songs.append(json.loads((Path(args.intermediates_dir) / f"{sid}.json").read_text(encoding="utf-8")))
    spec_sha256 = sha256_of_file(Path(args.spec_path))
    bundle = assemble_bundle(args.split, song_ids_sorted, songs, spec_sha256)
    bundle_bytes = _serialize_bundle_json(bundle)
    actual_sha256 = hashlib.sha256(bundle_bytes).hexdigest()

    # PR #329 第4巡レビュー指摘（P1、採用対応）: `assemble` は pinned
    # education manifest と照合せず canonical 形式で成功出力し得た——
    # `run_build()`/`_require_bundle_bytes_match_pinned_manifest()` と同型
    # の publish 前 fail-closed 照合を単一 split 版として導入する（下記
    # `_require_single_split_bundle_bytes_match_pinned_manifest()`）。
    pinned_manifest_check = "SKIPPED_UNPINNED"
    if not args.allow_unpinned:
        _require_single_split_bundle_bytes_match_pinned_manifest(
            args.split, actual_sha256, contract_path=contract_path,
        )
        pinned_manifest_check = "PASS"
    else:
        print(
            "assemble(): --allow-unpinned set — skipping pinned-manifest hash cross-check; "
            "output is UNPINNED and non-canonical until "
            "inputs/education_technique_lesson_manifest.json is repinned to match "
            f"(actual {args.split} sha256={actual_sha256!r})",
            file=sys.stderr,
        )

    write_bundle_bytes(bundle_bytes, Path(args.out))
    print(
        f"assemble: split={args.split} songs={len(songs)} "
        f"pinned_manifest_check={pinned_manifest_check} -> {args.out}",
        file=sys.stderr,
    )
    return 0


def _require_bundle_bytes_match_pinned_manifest(
    actual_training_sha: str,
    actual_validation_sha: str,
    *,
    contract_path: Optional[Path],
    manifest_path: Optional[Path] = None,
) -> None:
    """生成した training/validation バンドルバイトの sha256 が pinned
    education lesson manifest の `training_technique_lesson_sha256`/
    `validation_technique_lesson_sha256` と一致することを publish 前に
    検証する（PR #329 第2巡レビュー指摘2-3, P1, 採用対応）。

    旧実装の `run_build()` は pinned education manifest を一切ロード・
    照合せず、依存挙動のドリフト（例: 別ビルドの scipy/pyworld が異なる
    float を生成）が起きても両バンドルを publish して成功終了し得た——
    「正準の再現手段」として案内されているコマンドが、下流消費者が拒否
    すべき非正準 artifact を成功として報告する経路だった。

    `run_build(..., allow_unpinned=False)`（既定）からのみ呼ばれる。
    `education_lesson_builder.py` 自身の実装ロジック（module docstring
    「svp_rpe/voice_genesis の実装モジュールを import しない」の対象）
    ではなく検証経路の話であるため、`run9_schema.load_pinned_education_
    lesson_manifest()`（唯一の正規消費経路、5点の builder_provenance
    cross-check を含む）を呼ぶ——このため本関数の呼び出しには repo 収載の
    `education_lesson_builder.py` 自身の実バイトが manifest の
    `builder_provenance.builder_sha256` pin と一致していることが前提
    となる（通常運用では常に成立: builder バイトを変更したら manifest
    側の同 pin を追随更新する連鎖更新規約——本 PR 自身もこの規約に従う）。
    不一致は `ExtractorStopError` で publish 前に拒否する（実測 sha を
    両方表示）。`manifest_path` は通常 CLI からは渡されない（正典
    `EDUCATION_MANIFEST_PATH` を使う——education manifest はユーザー
    差し替え対象ではない）が、テスト層が改ざん済み合成 manifest を注入
    できるよう省略可能な引数として残す。
    """
    effective_contract_path = (
        contract_path if contract_path is not None else run9_schema.RUN9_CONTRACT_YAML_PATH
    )
    edu_contract = run9_schema.load_run9_contract_from_yaml_path(effective_contract_path)
    edu_manifest = run9_schema.load_pinned_education_lesson_manifest(
        edu_contract, manifest_path=manifest_path, contract_path=effective_contract_path,
    )
    expected_training_sha = edu_manifest["training_technique_lesson_sha256"]
    expected_validation_sha = edu_manifest["validation_technique_lesson_sha256"]
    mismatches = []
    if actual_training_sha != expected_training_sha:
        mismatches.append(("training", actual_training_sha, expected_training_sha))
    if actual_validation_sha != expected_validation_sha:
        mismatches.append(("validation", actual_validation_sha, expected_validation_sha))
    if mismatches:
        raise ExtractorStopError(
            "run_build(): generated bundle bytes do not match the pinned education lesson "
            "manifest's training_technique_lesson_sha256/validation_technique_lesson_sha256 "
            f"— publish is blocked fail-closed (staging discarded): mismatches(split, actual, "
            f"expected)={mismatches!r}. Pass --allow-unpinned for a deliberate new-attempt "
            "regeneration under the same design revision; the output is then UNPINNED "
            "(non-canonical) until inputs/education_technique_lesson_manifest.json is repinned "
            "to match."
        )


def _require_single_split_bundle_bytes_match_pinned_manifest(
    split: str,
    actual_sha256: str,
    *,
    contract_path: Optional[Path],
    manifest_path: Optional[Path] = None,
) -> None:
    """`assemble` 経路（training/validation いずれか1本のみを生成）専用の
    pinned education lesson manifest 照合（PR #329 第4巡レビュー指摘, P1,
    採用対応）。`_require_bundle_bytes_match_pinned_manifest()`（`run_build()`
    専用、training/validation 2本を同時に要求する）とは別関数——`assemble`
    は1本しか手元にバイトを持たないため、もう一方の split の実測 sha を
    持ち合わせない/偽装できてしまう構成を避け、要求された `split` 側の
    pin 値のみを照合する。

    旧実装の `_cmd_assemble()` は pinned education manifest を一切ロード・
    照合せず、中間物ディレクトリの内容がどのようなもの（依存挙動の
    ドリフトで生じた非正準バイト、あるいは改ざんされた中間物）であっても
    canonical な `run9-technique-lesson-bundle/1.0` 形式に整形できてさえ
    いれば成功終了し得た——「training/validation バンドルの正準な組立
    手段」として案内されているコマンドが、下流消費者が拒否すべき非正準
    artifact を成功として発行する経路だった。

    `contract_path`/`manifest_path` の意味・`--allow-unpinned` エスケープ
    ハッチの設計意図は `_require_bundle_bytes_match_pinned_manifest()` と
    同型（同 docstring 参照）。不一致は `ExtractorStopError` で publish
    前に拒否する（実測 sha を表示）。
    """
    effective_contract_path = (
        contract_path if contract_path is not None else run9_schema.RUN9_CONTRACT_YAML_PATH
    )
    edu_contract = run9_schema.load_run9_contract_from_yaml_path(effective_contract_path)
    edu_manifest = run9_schema.load_pinned_education_lesson_manifest(
        edu_contract, manifest_path=manifest_path, contract_path=effective_contract_path,
    )
    key = "training_technique_lesson_sha256" if split == "training" else "validation_technique_lesson_sha256"
    expected_sha256 = edu_manifest[key]
    if actual_sha256 != expected_sha256:
        raise ExtractorStopError(
            f"assemble(): generated {split} bundle bytes do not match the pinned education lesson "
            f"manifest's {key} — publish is blocked fail-closed (staging discarded): "
            f"actual={actual_sha256!r} expected={expected_sha256!r}. Pass --allow-unpinned for a "
            "deliberate new-attempt regeneration under the same design revision; the output is "
            "then UNPINNED (non-canonical) until inputs/education_technique_lesson_manifest.json "
            "is repinned to match."
        )


def run_build(
    *,
    corpus_root: Path,
    out_dir: Path,
    freeze_record_path: Path = DEFAULT_FREEZE_RECORD_PATH,
    spec_path: Path = DEFAULT_SPEC_PATH,
    split_manifest_path: Optional[Path] = None,
    contract_path: Optional[Path] = None,
    consumed_inputs_manifest_path: Optional[Path] = None,
    allow_unpinned: bool = False,
) -> Dict[str, Any]:
    """`run_batch_extract.py`（workdir 版）のバッチドライバロジックを
    path-resolved 引数の関数として統合したもの。freeze self-check → 全曲
    抽出（song_id 昇順） → training/validation バンドル組立 → pinned
    education manifest 照合 → atomic ペア公開、の順序。row_ids の取得元は
    pin 検証済みの `load_training_validation_ids()`（row_ids.sealed_
    holdout 非参照、spec §7 継続。PR #329 第1巡レビュー指摘1 対応で pin
    検証を追加）。抽出直前に対象 song_id 全数が凍結済み training∪
    validation に属することを `_require_song_ids_within_frozen_split()`
    で再確認する（構成上 `all_ids` は既にその集合から導出されているため
    冗長だが、指摘1の「run_build()/extract-song とも抽出前に検証」を
    明示的な防御として満たす。`extract_song()` 自身も同じゲートを内蔵
    する——第2巡レビュー指摘, P1, 採用対応）。各曲の decode 前に消費3入力
    （lab/musicxml/wav）の実バイトを `consumed_inputs_pins` と照合する
    （PR #329 第2巡レビュー指摘2-4, P1, 採用対応）。

    バンドル2本を直列化した後、`allow_unpinned=False`（既定）なら
    publish 前に生成バイトの sha256 を `load_pinned_education_lesson_
    manifest()` の `training_technique_lesson_sha256`/`validation_
    technique_lesson_sha256` と照合し、不一致なら publish せず非ゼロ終了
    する（PR #329 第2巡レビュー指摘2-3, P1, 採用対応）——`run_build()` は
    「正準の再現手段」として案内されているにもかかわらず、依存挙動の
    ドリフト（例: 別ビルドの scipy/pyworld）で生じた非正準バイトを検知
    せずに成功終了し得た穴を閉じる。`allow_unpinned=True` は将来「同一
    design revision 下での新規 attempt 再生成」を意図的に行うための
    エスケープハッチで、既定 off。使用時は出力が UNPINNED（manifest が
    repin されるまで非正準）である旨を stderr へ明示する。

    バンドル2本は `publish_bundle_pair()` で atomic ペア公開する（PR #329
    第1巡レビュー指摘2 + 第2巡レビュー指摘2-2, いずれも P1, 採用対応）。
    戻り値は run_log 相当の機械可読サマリ。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    freeze_selfcheck(freeze_record_path, spec_path)

    # unpack（`FrozenSplitPins.__iter__` 経由——テスト層が monkeypatch で
    # 生 tuple を返す簡易 stub を注入するケースとも互換）してから自前で
    # `FrozenSplitPins` を再構築する。こうすることで `load_training_
    # validation_ids()` の実際の戻り値の型に関わらず、`extract_song()` へ
    # 渡す値は常に本物の `FrozenSplitPins` になる（PR #329 第3巡レビュー
    # 指摘2, P1, 採用対応）。
    training_ids, validation_ids = load_training_validation_ids(
        split_manifest_path, contract_path=contract_path,
    )
    frozen_split_pins = FrozenSplitPins(
        training_ids=tuple(training_ids), validation_ids=tuple(validation_ids),
    )
    all_ids = sorted(set(training_ids) | set(validation_ids))
    _require_song_ids_within_frozen_split(
        all_ids, frozen_split_pins.frozen_allowed_ids, context="run_build",
    )

    consumed_inputs_pins = load_consumed_inputs_pins(
        consumed_inputs_manifest_path, contract_path=contract_path,
    )

    songs_by_id: Dict[str, Dict[str, Any]] = {}
    for song_id in all_ids:
        songs_by_id[song_id] = extract_song(
            corpus_root / song_id, song_id,
            frozen_split_pins=frozen_split_pins, consumed_inputs_pins=consumed_inputs_pins,
        )

    spec_sha256 = sha256_of_file(spec_path)
    training_bundle = assemble_bundle(
        "training", training_ids, [songs_by_id[sid] for sid in training_ids], spec_sha256,
    )
    validation_bundle = assemble_bundle(
        "validation", validation_ids, [songs_by_id[sid] for sid in validation_ids], spec_sha256,
    )
    training_bytes = _serialize_bundle_json(training_bundle)
    validation_bytes = _serialize_bundle_json(validation_bundle)
    actual_training_sha = hashlib.sha256(training_bytes).hexdigest()
    actual_validation_sha = hashlib.sha256(validation_bytes).hexdigest()

    pinned_manifest_check = "SKIPPED_UNPINNED"
    if not allow_unpinned:
        _require_bundle_bytes_match_pinned_manifest(
            actual_training_sha, actual_validation_sha, contract_path=contract_path,
        )
        pinned_manifest_check = "PASS"
    else:
        print(
            "run_build(): --allow-unpinned set — skipping pinned-manifest hash cross-check; "
            "output is UNPINNED and non-canonical until "
            "inputs/education_technique_lesson_manifest.json is repinned to match these bytes "
            f"(actual training sha256={actual_training_sha!r}, "
            f"validation sha256={actual_validation_sha!r})",
            file=sys.stderr,
        )

    training_out = out_dir / "training_bundle.json"
    validation_out = out_dir / "validation_bundle.json"
    publish_bundle_pair(training_out, training_bytes, validation_out, validation_bytes)

    return {
        "training_bundle": {
            "path": str(training_out), "sha256": actual_training_sha, "song_count": len(training_ids),
        },
        "validation_bundle": {
            "path": str(validation_out), "sha256": actual_validation_sha,
            "song_count": len(validation_ids),
        },
        "pinned_manifest_check": pinned_manifest_check,
    }


def _cmd_build(args: argparse.Namespace) -> int:
    result = run_build(
        corpus_root=Path(args.corpus_root),
        out_dir=Path(args.out_dir),
        freeze_record_path=Path(args.freeze_record) if args.freeze_record else DEFAULT_FREEZE_RECORD_PATH,
        spec_path=Path(args.spec_path) if args.spec_path else DEFAULT_SPEC_PATH,
        split_manifest_path=Path(args.split_manifest) if args.split_manifest else None,
        contract_path=Path(args.contract_path) if args.contract_path else None,
        consumed_inputs_manifest_path=(
            Path(args.consumed_inputs_manifest) if args.consumed_inputs_manifest else None
        ),
        allow_unpinned=bool(args.allow_unpinned),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe-header", help="metadata-only WAV header probe (no decode)")
    p.add_argument("--corpus-root", required=True)
    p.add_argument("--song-ids", nargs="+", required=True)
    p.add_argument(
        "--split-manifest", default=None,
        help=f"default: {DEFAULT_SPLIT_MANIFEST_PATH} (pin-verified against RUN9_CONTRACT.yaml "
        "practice_audio_split_manifest_sha — every --song-ids entry must be a member of its "
        "training/validation row_ids; sealed_holdout song_ids are rejected before any WAV is "
        "opened, per 裁定 §2)",
    )
    p.add_argument("--contract-path", default=None, help="default: RUN9_CONTRACT.yaml (repo canonical)")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_cmd_probe_header)

    p = sub.add_parser("extract-song", help="extract one song to an intermediate JSON")
    p.add_argument("--corpus-root", required=True)
    p.add_argument("--song-id", required=True)
    p.add_argument("--freeze-record", default=str(DEFAULT_FREEZE_RECORD_PATH))
    p.add_argument("--spec-path", default=str(DEFAULT_SPEC_PATH))
    p.add_argument(
        "--split-manifest", default=None,
        help=f"default: {DEFAULT_SPLIT_MANIFEST_PATH} (pin-verified against RUN9_CONTRACT.yaml "
        "practice_audio_split_manifest_sha — --song-id must be a member of its training/validation "
        "row_ids; sealed_holdout song_ids are rejected)",
    )
    p.add_argument("--contract-path", default=None, help="default: RUN9_CONTRACT.yaml (repo canonical)")
    p.add_argument(
        "--consumed-inputs-manifest", default=None,
        help=f"default: {DEFAULT_CONSUMED_INPUTS_MANIFEST_PATH} (pin-verified against RUN9_"
        "CONTRACT.yaml pjs_consumed_inputs_manifest_sha — --song-id's lab/musicxml/wav actual "
        "bytes must match this pin's sha256, verified before decode)",
    )
    p.add_argument("--out", required=True)
    p.set_defaults(func=_cmd_extract_song)

    p = sub.add_parser("assemble", help="assemble per-song intermediates into a bundle")
    p.add_argument("--split", required=True, choices=["training", "validation"])
    p.add_argument("--song-ids-json", required=True)
    p.add_argument("--intermediates-dir", required=True)
    p.add_argument("--spec-path", default=str(DEFAULT_SPEC_PATH))
    p.add_argument(
        "--split-manifest", default=None,
        help=f"default: {DEFAULT_SPLIT_MANIFEST_PATH} (pin-verified against RUN9_CONTRACT.yaml "
        "practice_audio_split_manifest_sha — --song-ids-json must exactly equal the frozen "
        "training/validation ID set for --split; sealed_holdout/missing/extra/unknown ids are "
        "rejected before reading any intermediates)",
    )
    p.add_argument("--contract-path", default=None, help="default: RUN9_CONTRACT.yaml (repo canonical)")
    p.add_argument("--out", required=True)
    p.add_argument(
        "--allow-unpinned", action="store_true",
        help="skip the pinned education-lesson-manifest hash cross-check for this split before "
        "publish (default off — the produced bundle is then UNPINNED/non-canonical until "
        "inputs/education_technique_lesson_manifest.json is repinned to match; intended only for "
        "a deliberate new-attempt regeneration under the same design revision)",
    )
    p.set_defaults(func=_cmd_assemble)

    p = sub.add_parser("build", help="full batch build: split manifest -> training/validation bundles")
    p.add_argument("--corpus-root", required=True, help="PJS expanded corpus root (pjsNNN/ dirs)")
    p.add_argument("--out-dir", required=True, help="output directory for training_bundle.json / validation_bundle.json")
    p.add_argument("--freeze-record", default=None, help=f"default: {DEFAULT_FREEZE_RECORD_PATH}")
    p.add_argument("--spec-path", default=None, help=f"default: {DEFAULT_SPEC_PATH}")
    p.add_argument("--split-manifest", default=None, help=f"default: {DEFAULT_SPLIT_MANIFEST_PATH}")
    p.add_argument("--contract-path", default=None, help="default: RUN9_CONTRACT.yaml (repo canonical)")
    p.add_argument(
        "--consumed-inputs-manifest", default=None,
        help=f"default: {DEFAULT_CONSUMED_INPUTS_MANIFEST_PATH} (pin-verified; each song's lab/"
        "musicxml/wav actual bytes must match before decode)",
    )
    p.add_argument(
        "--allow-unpinned", action="store_true",
        help="skip the pinned education-lesson-manifest hash cross-check before publish (default "
        "off — the produced bundles are then UNPINNED/non-canonical until "
        "inputs/education_technique_lesson_manifest.json is repinned to match; intended only for "
        "a deliberate new-attempt regeneration under the same design revision)",
    )
    p.set_defaults(func=_cmd_build)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ExtractorStopError as e:
        print(f"STOP: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    except run9_schema.Run9ValidationError as e:
        # PR #329 第1巡レビュー指摘1（P1、採用対応）: split manifest の
        # pin/構造検証は `run9_schema.Run9ValidationError` を送出する
        # （`ExtractorStopError` 系とは別階層）——CLI としては同じ「STOP」
        # 扱いで終了コード2にする。
        print(f"STOP: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
