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
    `_load_split_ids()` は split manifest の `row_ids.training`/
    `row_ids.validation` のみを読み、`row_ids.sealed_holdout` は一切
    参照しない。
  - advisory 6 channel（vibrato/breath_placement/release_persistence/
    terminal_mel_persistence/HNR/vowel_drift）のコードパスを実装しない。
  - corpus 統計正規化を実装しない（energy_envelope は per-phrase 自己
    正規化のみ）。
  - stdlib + numpy + scipy + pyworld のみに依存する。librosa import なし。
    svp_rpe / voice_genesis の実装モジュールを import しない（定数は Read
    による転記のみ、下記 FRAME_PERIOD_MS 参照）。

出力: training/validation 各1本の JSON バンドル（`run9-technique-lesson-
bundle/1.0`）。バンドル実体ファイル自体は repo にコミットしない（rights
制約 — 実 PJS 音源からの derived artifact のため。sha256 のみ
`inputs/education_technique_lesson_manifest.json` へ pin する）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.signal import resample_poly

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
# (this file does not import run9_schema, per the module docstring's
# no-svp_rpe/voice_genesis-import constraint interpreted broadly for sibling
# run9 modules too — the two copies are compared byte-for-content in
# tests/test_education_lesson_builder.py).
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


def check_wav_header_or_stop(path: Path) -> Dict[str, Any]:
    """spec v1.1 §2-1: RIFF PCM を検査し 24-bit/mono/48000Hz を要求。不一致は即停止。"""
    hdr = read_wav_fmt_header(path)
    if hdr["audio_format"] != REQUIRED_AUDIO_FORMAT_PCM:
        raise WavHeaderError(
            f"{path}: audio_format={hdr['audio_format']} (expected PCM={REQUIRED_AUDIO_FORMAT_PCM})"
        )
    mismatches = []
    if hdr["channels"] != REQUIRED_CHANNELS:
        mismatches.append(f"channels={hdr['channels']} (required {REQUIRED_CHANNELS})")
    if hdr["sample_rate"] != REQUIRED_SAMPLE_RATE:
        mismatches.append(f"sample_rate={hdr['sample_rate']} (required {REQUIRED_SAMPLE_RATE})")
    if hdr["bits_per_sample"] != REQUIRED_BITS_PER_SAMPLE:
        mismatches.append(f"bits_per_sample={hdr['bits_per_sample']} (required {REQUIRED_BITS_PER_SAMPLE})")
    if mismatches:
        raise WavHeaderError(f"{path}: WAV header mismatch — " + "; ".join(mismatches))
    return hdr


def _read_wav_fmt_and_data(path: Path) -> Tuple[Dict[str, Any], bytes]:
    """Read the fmt chunk AND the data chunk's raw bytes (this is the
    decode-adjacent read — only called after check_wav_header_or_stop() has
    already passed for this exact file)."""
    with open(path, "rb") as fh:
        riff = fh.read(12)
        if len(riff) < 12 or riff[0:4] != b"RIFF" or riff[8:12] != b"WAVE":
            raise WavHeaderError(f"{path}: not a RIFF/WAVE file (header={riff!r})")
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
            raise WavHeaderError(f"{path}: missing fmt or data chunk")
        return fmt, data_bytes


def load_wav_24bit_mono_48k(path: Path) -> np.ndarray:
    """Decode PCM samples. Only ever called AFTER check_wav_header_or_stop()
    has passed for this exact file.

    spec v1.1 §2-1 pinned manual 24-bit byte decode (library-dependent
    24-bit WAV decoding is avoided as ambiguous):
      - data bytes reshaped to (-1, 3) via np.frombuffer(..., dtype=uint8)
      - v = b0 | (b1<<8) | (b2<<16), little-endian, assembled as int32
      - v >= 2**23 -> v -= 2**24 (two's-complement sign extension)
      - x = v.astype(float64) / 8388608.0  (2**23; value range [-1, 1))
    """
    fmt, data_bytes = _read_wav_fmt_and_data(path)
    if fmt["bits_per_sample"] != REQUIRED_BITS_PER_SAMPLE or fmt["channels"] != REQUIRED_CHANNELS \
            or fmt["sample_rate"] != REQUIRED_SAMPLE_RATE or fmt["audio_format"] != REQUIRED_AUDIO_FORMAT_PCM:
        # Should be unreachable if check_wav_header_or_stop() was called first.
        raise WavHeaderError(f"{path}: fmt chunk disagrees with prior header probe: {fmt}")

    raw = np.frombuffer(data_bytes, dtype=np.uint8)
    n_samples = len(raw) // 3
    if n_samples * 3 != len(raw):
        raise WavHeaderError(f"{path}: data chunk size {len(raw)} is not a multiple of 3 bytes (24-bit mono)")
    raw = raw[: n_samples * 3].reshape(-1, 3)
    b0 = raw[:, 0].astype(np.int32)
    b1 = raw[:, 1].astype(np.int32)
    b2 = raw[:, 2].astype(np.int32)
    v = b0 | (b1 << 8) | (b2 << 16)
    v = np.where(v >= (1 << 23), v - (1 << 24), v)
    x = v.astype(np.float64) / 8388608.0  # 2**23
    return x


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


def parse_lab_file(path: Path) -> List[LabPhoneme]:
    text = path.read_text(encoding="utf-8")
    out: List[LabPhoneme] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) != 3:
            raise LabError(f"{path}: malformed .lab line (expected 3 columns): {line!r}")
        start_raw, end_raw, phoneme = parts
        out.append(LabPhoneme(int(start_raw) * LAB_TIME_UNIT_S, int(end_raw) * LAB_TIME_UNIT_S, phoneme))
    if not out:
        raise LabError(f"{path}: empty .lab file")
    return out


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


def parse_musicxml(path: Path) -> Tuple[List[Tuple[float, float]], List[RawNote]]:
    """Returns (tempo_events, raw_notes). tempo_events = list of
    (beat_position, tempo_bpm) in document-encounter order. raw_notes in
    document order with a shared cursor (beat_onset) computed by walking
    <note>/<backup>/<forward> sequentially regardless of <voice> — this is
    standard MusicXML cursor semantics, not an invention (verified against
    pjs064's backup=48/forward=21+3+18 passage, which reconciles exactly
    under this rule)."""
    tree = ET.parse(path)
    root = tree.getroot()
    if root.tag != "score-partwise":
        raise MusicXmlError(f"{path}: unsupported root <{root.tag}> (expected score-partwise)")
    parts = root.findall("part")
    if len(parts) != 1:
        raise MusicXmlError(f"{path}: expected exactly 1 <part>, found {len(parts)}")
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
                    raise MusicXmlError(f"{path}: <backup> missing <duration>")
                if divisions is None:
                    raise MusicXmlError(f"{path}: <backup> before <divisions> known")
                beat_cursor -= float(dur_el.text) / divisions
            elif tag == "forward":
                dur_el = child.find("duration")
                if dur_el is None:
                    raise MusicXmlError(f"{path}: <forward> missing <duration>")
                if divisions is None:
                    raise MusicXmlError(f"{path}: <forward> before <divisions> known")
                beat_cursor += float(dur_el.text) / divisions
            elif tag == "note":
                if child.find("grace") is not None:
                    raise MusicXmlError(f"{path}: <grace> note — not covered by spec, stop")
                if child.find("chord") is not None:
                    raise MusicXmlError(f"{path}: <chord> note — not covered by spec, stop")
                dur_el = child.find("duration")
                if dur_el is None:
                    raise MusicXmlError(f"{path}: <note> missing <duration>")
                if divisions is None:
                    raise MusicXmlError(f"{path}: <note> before <divisions> known")
                beat_duration = float(dur_el.text) / divisions
                voice_el = child.find("voice")
                voice = voice_el.text if voice_el is not None else "1"
                is_rest = child.find("rest") is not None
                pitch_step = pitch_alter = pitch_octave = None
                if not is_rest:
                    pitch_el = child.find("pitch")
                    if pitch_el is None:
                        raise MusicXmlError(f"{path}: non-rest <note> missing <pitch> (unpitched unsupported)")
                    step_el = pitch_el.find("step")
                    octave_el = pitch_el.find("octave")
                    if step_el is None or octave_el is None:
                        raise MusicXmlError(f"{path}: <pitch> missing step/octave")
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
        raise MusicXmlError(f"{path}: no <sound tempo=...> found anywhere — tempo must not be invented, stop")
    return tempo_events, raw_notes


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

def extract_song(song_dir: Path, song_id: str) -> Dict[str, Any]:
    """Extract one song. Only decodes audio if the WAV header check passes.
    Returns a per-song intermediate dict (see bundle assembly for full
    schema); on count_mismatch, channels are recorded as not_extracted with
    a reason, per spec §3 (this is NOT a stop condition by itself)."""
    wav_path = song_dir / f"{song_id}_song.wav"
    lab_path = song_dir / f"{song_id}.lab"
    xml_path = song_dir / f"{song_id}.musicxml"
    for p in (wav_path, lab_path, xml_path):
        if not p.exists():
            raise ExtractorStopError(f"{song_id}: required input missing: {p}")

    wav_header = check_wav_header_or_stop(wav_path)  # STOPS the whole run on mismatch (raises)

    phonemes = parse_lab_file(lab_path)
    lab_morae = group_lab_to_morae_with_phrases(phonemes)

    tempo_events, raw_notes = parse_musicxml(xml_path)
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

    x48k = load_wav_24bit_mono_48k(wav_path)
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


def write_bundle_json(obj: Dict[str, Any], path: Path) -> None:
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# split manifest reading (row_ids.training / row_ids.validation ONLY —
# row_ids.sealed_holdout is never accessed by this function or any other
# code path in this file, per spec §7 / 裁定 §2).
# ---------------------------------------------------------------------------

def load_training_validation_ids(split_manifest_path: Path) -> Tuple[List[str], List[str]]:
    data = json.loads(split_manifest_path.read_text(encoding="utf-8"))
    row_ids = data["row_ids"]
    training_ids = sorted(row_ids["training"])
    validation_ids = sorted(row_ids["validation"])
    return training_ids, validation_ids


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_probe_header(args: argparse.Namespace) -> int:
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
    freeze_selfcheck(Path(args.freeze_record), Path(args.spec_path))
    song_dir = Path(args.corpus_root) / args.song_id
    result = extract_song(song_dir, args.song_id)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(f"extract-song {args.song_id}: alignment_status={result['alignment_status']}", file=sys.stderr)
    return 0


def _cmd_assemble(args: argparse.Namespace) -> int:
    song_ids = json.loads(Path(args.song_ids_json).read_text(encoding="utf-8"))
    song_ids_sorted = sorted(song_ids)
    songs = []
    for sid in song_ids_sorted:
        songs.append(json.loads((Path(args.intermediates_dir) / f"{sid}.json").read_text(encoding="utf-8")))
    spec_sha256 = sha256_of_file(Path(args.spec_path))
    bundle = assemble_bundle(args.split, song_ids_sorted, songs, spec_sha256)
    write_bundle_json(bundle, Path(args.out))
    print(f"assemble: split={args.split} songs={len(songs)} -> {args.out}", file=sys.stderr)
    return 0


def run_build(
    *,
    corpus_root: Path,
    out_dir: Path,
    freeze_record_path: Path = DEFAULT_FREEZE_RECORD_PATH,
    spec_path: Path = DEFAULT_SPEC_PATH,
    split_manifest_path: Path = DEFAULT_SPLIT_MANIFEST_PATH,
) -> Dict[str, Any]:
    """`run_batch_extract.py`（workdir 版）のバッチドライバロジックを
    path-resolved 引数の関数として統合したもの。freeze self-check → 全曲
    抽出（song_id 昇順） → training/validation バンドル組立、の順序は
    workdir 版から一切変更していない。row_ids の取得元は
    `load_training_validation_ids()`（row_ids.sealed_holdout 非参照、spec
    §7 継続）。戻り値は run_log 相当の機械可読サマリ。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    freeze_selfcheck(freeze_record_path, spec_path)

    training_ids, validation_ids = load_training_validation_ids(split_manifest_path)
    all_ids = sorted(set(training_ids) | set(validation_ids))

    songs_by_id: Dict[str, Dict[str, Any]] = {}
    for song_id in all_ids:
        songs_by_id[song_id] = extract_song(corpus_root / song_id, song_id)

    spec_sha256 = sha256_of_file(spec_path)
    training_bundle = assemble_bundle(
        "training", training_ids, [songs_by_id[sid] for sid in training_ids], spec_sha256,
    )
    validation_bundle = assemble_bundle(
        "validation", validation_ids, [songs_by_id[sid] for sid in validation_ids], spec_sha256,
    )
    training_out = out_dir / "training_bundle.json"
    validation_out = out_dir / "validation_bundle.json"
    write_bundle_json(training_bundle, training_out)
    write_bundle_json(validation_bundle, validation_out)

    return {
        "training_bundle": {
            "path": str(training_out), "sha256": sha256_of_file(training_out),
            "song_count": len(training_ids),
        },
        "validation_bundle": {
            "path": str(validation_out), "sha256": sha256_of_file(validation_out),
            "song_count": len(validation_ids),
        },
    }


def _cmd_build(args: argparse.Namespace) -> int:
    result = run_build(
        corpus_root=Path(args.corpus_root),
        out_dir=Path(args.out_dir),
        freeze_record_path=Path(args.freeze_record) if args.freeze_record else DEFAULT_FREEZE_RECORD_PATH,
        spec_path=Path(args.spec_path) if args.spec_path else DEFAULT_SPEC_PATH,
        split_manifest_path=Path(args.split_manifest) if args.split_manifest else DEFAULT_SPLIT_MANIFEST_PATH,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe-header", help="metadata-only WAV header probe (no decode)")
    p.add_argument("--corpus-root", required=True)
    p.add_argument("--song-ids", nargs="+", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=_cmd_probe_header)

    p = sub.add_parser("extract-song", help="extract one song to an intermediate JSON")
    p.add_argument("--corpus-root", required=True)
    p.add_argument("--song-id", required=True)
    p.add_argument("--freeze-record", default=str(DEFAULT_FREEZE_RECORD_PATH))
    p.add_argument("--spec-path", default=str(DEFAULT_SPEC_PATH))
    p.add_argument("--out", required=True)
    p.set_defaults(func=_cmd_extract_song)

    p = sub.add_parser("assemble", help="assemble per-song intermediates into a bundle")
    p.add_argument("--split", required=True, choices=["training", "validation"])
    p.add_argument("--song-ids-json", required=True)
    p.add_argument("--intermediates-dir", required=True)
    p.add_argument("--spec-path", default=str(DEFAULT_SPEC_PATH))
    p.add_argument("--out", required=True)
    p.set_defaults(func=_cmd_assemble)

    p = sub.add_parser("build", help="full batch build: split manifest -> training/validation bundles")
    p.add_argument("--corpus-root", required=True, help="PJS expanded corpus root (pjsNNN/ dirs)")
    p.add_argument("--out-dir", required=True, help="output directory for training_bundle.json / validation_bundle.json")
    p.add_argument("--freeze-record", default=None, help=f"default: {DEFAULT_FREEZE_RECORD_PATH}")
    p.add_argument("--spec-path", default=None, help=f"default: {DEFAULT_SPEC_PATH}")
    p.add_argument("--split-manifest", default=None, help=f"default: {DEFAULT_SPLIT_MANIFEST_PATH}")
    p.set_defaults(func=_cmd_build)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ExtractorStopError as e:
        print(f"STOP: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
