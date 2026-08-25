"""practice_split_builder.py — RUN9-BIRTH-PREP-1 §B: PRACTICE_FROM_AUDIO 用
train/validation/sealed-holdout split manifest builder + advisory 音響
inventory sidecar。

`RUN9_CONTRACT.yaml` `practice_audio_split_manifest_sha` が pin する実体
（`inputs/practice_audio_split_manifest.json`、schema
`run9_schema.SCHEMA_PRACTICE_AUDIO_SPLIT_MANIFEST`）を実 PJS コーパスから
決定論的に構築する。本モジュール自体は実 PJS コーパスの実行を要求しない
（fixture コーパスでも動作する純粋なファイル走査 + 決定論割当）。

**PJS コーパス enumeration の規約**: `voice_genesis/foundry/adapter/
donor_bank_lab.py` の `corpus_identity_hash()`（pin 被覆定義の正本）と
**同一規約**（`pjsNNN/pjsNNN.lab` を bytewise 辞書順で列挙し、対応する
`pjsNNN_song.wav` が存在する場合のみ `(相対パス, sha256)` ペアへ含める）で
`expanded_corpus_identity_sha256` を再計算する。ただし `donor_bank_lab.py`
は `donor_bank.py` 経由で `pyworld` を import 時に要求する（本モジュールの
制約「新規依存なし・pyworld 不使用」に抵触する）ため、`corpus_identity_hash()`
自体は import せず、同じアルゴリズム（`aggregate_content_hash()` の
`path:hash` 連結規約を含む）をここへ独立に再実装する——値は
`donor_bank_lab.corpus_identity_hash()` と完全に同じ入力に対して同じ値を
返す（両実装が乖離しないことは `RUN9_CONTRACT.yaml` の
`expanded_corpus_identity_sha256` pin 値との一致で担保される）。

song_id の列挙対象は「pin 被覆 `_song.wav` 集合のみ」——`.lab` はあるが
対応する `_song.wav` が存在しない曲は split 割当の対象から除外する
（`corpus_identity_hash()` のペア構成そのものが同じ条件で wav を含める/
含めないを決めるため、規約は自動的に揃う）。

**割当**: `assign_split()` は User 裁定（2026-08-25）の逐語アルゴリズムを
実装する純関数——`score(song_id) = sha256(f"{song_id}|{LEARNING_SEED}")`
の hex 値で全順序付け、連続分割で training/validation/sealed_holdout を
確定する（独立 hash bucket 方式は不採用・乱数 API 不使用）。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

import run9_schema as m  # noqa: E402  (sibling import — repo-wide run9_* convention)

# ---------------------------------------------------------------------------
# 契約の既 pin 値の転記元（`inputs/rights_manifest.json`
# `recording_master_rights.corpus_pins`、DESIGN_RUN9_REVISION_0.2.md 改訂3）。
# rights_manifest.json は凍結ファイルのため、値をここへハードコードして
# 転記する（`build_practice_split_manifest()` の既定値。fixture テストは
# 引数で上書きできる）。
# ---------------------------------------------------------------------------

PJS_SOURCE_ARCHIVE_SHA256 = "683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca"
EXPANDED_CORPUS_IDENTITY_SHA256 = "9905cec08fbaf43fa545400498a7908ef28567e8f60a5ba005fb2e00d526f996"

# manifest.rights_source_class（`PRACTICE_MANIFEST_REQUIRED_KEYS` 必須欄。
# 非空文字列であることのみ検証される — 値の語彙自体は本モジュールが固定する）。
PRACTICE_RIGHTS_SOURCE_CLASS = "PJS_CC_BY_SA_4_0_RAW_AUDIO_TRAINING_SEGMENT"

# 近似重複検出は実装しない（RUN9-BIRTH-PREP-1 §B 裁定逐語）— PJS 100曲は
# 相異なる楽曲であるため、この境界宣言を manifest 内 note として保存する。
_NEAR_DUPLICATE_BOUNDARY_NOTE = (
    "PJS 100 曲は相異なる楽曲であり近似重複検出は未実装（RUN9-BIRTH-PREP-1 §B、"
    "境界宣言）。"
)


# ---------------------------------------------------------------------------
# corpus enumeration（donor_bank_lab.corpus_identity_hash() と同一規約の
# 独立再実装 — 上記モジュール docstring 参照）。
# ---------------------------------------------------------------------------


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _aggregate_content_hash(paths_and_hashes: Sequence[Tuple[str, str]]) -> str:
    """`donor_bank.aggregate_content_hash()` と同一規約（`path:hash` を `|`
    連結してから sha256）。"""
    material = "|".join(f"{path}:{digest}" for path, digest in sorted(paths_and_hashes))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _reject_paths_outside_corpus_root(paths: Sequence[Path], root: Path) -> None:
    """`donor_bank.reject_paths_outside_root()` と同じ意図（`pjsNNN/` が
    corpus_root 外を指す symlink の場合の脱出防止）の軽量版。resolve 後
    root 包含検査のみ（PJS 側候補は `Path.glob` の実ファイルパスであり
    UTAU 版が持つ語彙的検査は不要 — donor_bank.reject_paths_outside_root
    docstring と同じ判断根拠）。"""
    root_resolved = root.resolve()
    escaped: List[str] = []
    for path in paths:
        try:
            resolved = path.resolve()
            resolved.relative_to(root_resolved)
        except (OSError, ValueError):
            escaped.append(str(path))
    if escaped:
        raise m.Run9ValidationError(
            f"practice_split_builder: path(s) escape corpus_root {root!r}: {sorted(escaped)}"
        )


def _enumerate_pjs_song_ids(root: Path) -> Tuple[str, List[str]]:
    """`root` を PJS コーパスとして走査し `(expanded_corpus_identity_sha256,
    song_ids)` を返す。`song_ids` は「pin 被覆 `_song.wav` 集合のみ」——
    対応する `_song.wav` が存在する `.lab` に限る（列挙は相対パスの
    bytewise 辞書順へ正規化。filesystem が返す順序には依存しない）。
    """
    lab_paths = sorted(root.glob("pjs*/pjs*.lab"), key=lambda p: str(p.relative_to(root)))
    if not lab_paths:
        raise m.Run9ValidationError(f"no pjs*/pjs*.lab found under {root}")
    candidate_paths: List[Path] = []
    for lab_path in lab_paths:
        candidate_paths.append(lab_path)
        candidate_paths.append(lab_path.parent / f"{lab_path.stem}_song.wav")
    _reject_paths_outside_corpus_root(candidate_paths, root)

    pairs: List[Tuple[str, str]] = []
    song_ids: List[str] = []
    for lab_path in lab_paths:
        pairs.append((str(lab_path.relative_to(root)), _sha256_of(lab_path)))
        wav_path = lab_path.parent / f"{lab_path.stem}_song.wav"
        if wav_path.exists():
            pairs.append((str(wav_path.relative_to(root)), _sha256_of(wav_path)))
            song_ids.append(lab_path.stem)
    identity_hash = _aggregate_content_hash(pairs)
    return identity_hash, song_ids


# ---------------------------------------------------------------------------
# split assignment（純関数。song_id 列を入力に取り、音声 I/O を一切行わない
# — N=100 相当のダミー song_id 列でも実 wav fixture 不要でテストできる）。
# ---------------------------------------------------------------------------


def _song_score(song_id: str) -> str:
    """User 裁定（2026-08-25）逐語:
    `score(song_id) = sha256(f"{song_id}|{LEARNING_SEED}".encode("utf-8")).hexdigest()`。
    """
    return hashlib.sha256(f"{song_id}|{m.LEARNING_SEED}".encode("utf-8")).hexdigest()


def assign_split(song_ids: Sequence[str]) -> Dict[str, List[str]]:
    """`song_ids` を `score(song_id)` の hex 値昇順（同値タイブレークは
    `song_id` 自身の辞書順）で全順序付けし、連続分割で
    training/validation/sealed_holdout を確定する純関数。

    件数規則（RUN9-BIRTH-PREP-1 §B 裁定逐語）: `n_val = floor(N*0.15)` /
    `n_holdout = floor(N*0.15)` / `n_train = N - n_val - n_holdout`。
    N=100 → 70/15/15 厳密。いずれかの split が空になる N（N<=6）は
    `Run9ValidationError` で fail-closed（部分出力を書かない——呼び出し元は
    本関数が正常終了した場合にのみ manifest 構築を続けてよい）。

    戻り値の `"row_order"` キーは全順序確定後の song_id 列（rank 順）——
    `row_order_sha256` の入力および `sample_inventory` の rank 表示に使う。
    """
    ids = list(song_ids)
    if not ids:
        raise m.Run9ValidationError("assign_split(): song_ids must be non-empty")
    m._require_no_duplicate_list_items(  # noqa: SLF001 - sibling module, see module docstring
        ids, manifest_kind="practice split assignment", field="song_ids"
    )
    n = len(ids)
    n_val = math.floor(n * 0.15)
    n_holdout = math.floor(n * 0.15)
    n_train = n - n_val - n_holdout
    if n_val == 0 or n_holdout == 0 or n_train <= 0:
        raise m.Run9ValidationError(
            f"assign_split(): N={n} is too small to keep all three splits non-empty "
            f"(n_train={n_train}, n_val={n_val}, n_holdout={n_holdout}) — RUN9-BIRTH-PREP-1 "
            "§B requires N>=7 for a well-formed split; fail-closed, no partial output"
        )
    ranked = sorted(ids, key=lambda sid: (_song_score(sid), sid))
    training = ranked[:n_train]
    validation = ranked[n_train : n_train + n_val]
    sealed_holdout = ranked[n_train + n_val :]
    return {
        "training": training,
        "validation": validation,
        "sealed_holdout": sealed_holdout,
        "row_order": ranked,
    }


# ---------------------------------------------------------------------------
# practice split manifest builder
# ---------------------------------------------------------------------------


def build_practice_split_manifest(
    corpus_root: str | Path,
    *,
    expected_corpus_identity: str,
    pjs_source_archive_sha256: str = PJS_SOURCE_ARCHIVE_SHA256,
) -> Dict[str, Any]:
    """`corpus_root`（PJS コーパスルート、`pjsNNN/pjsNNN.lab` +
    `pjsNNN_song.wav` を含む）から `run9-practice-audio-split-manifest/1.0`
    manifest を決定論的に構築する。

    `expected_corpus_identity` は**必須**引数（デフォルト値なし）——
    再計算した `expanded_corpus_identity_sha256` がこの値と厳密一致しない
    場合 fail-closed 拒否する（RUN9-BIRTH-PREP-1 §B 裁定: 「照合はデフォルト
    有効・fixture テスト用に引数で上書き可能とするが、None/省略で照合
    スキップは不可」）。本番実行では
    `practice_split_builder.EXPANDED_CORPUS_IDENTITY_SHA256`
    （`RUN9_CONTRACT.yaml`/`inputs/rights_manifest.json` の既 pin 値）を
    明示的に渡すこと。

    生成後、`run9_schema.validate_practice_split_manifest()` を自己適用
    してから返す（fail-closed — 自己整合的でない manifest は決して返さない）。

    `sample_inventory` は列挙のみから決まる構造的 inventory に限る（split
    別件数・song_id 列・rank を `"{rank:04d}|{split}|{song_id}"` 形式の
    文字列へエンコードする——`validate_practice_split_manifest()` が
    `sample_inventory` を非空文字列リストとして検証する既存契約に適合
    させるための表現）。音響解析値は一切含めない——本関数のデータフローは
    ファイル列挙 + ハッシュ計算のみで、`librosa`/音響解析呼び出しを一切
    含まない（`build_acoustic_inventory_sidecar()` との型的分離）。
    """
    if not isinstance(expected_corpus_identity, str) or not expected_corpus_identity:
        raise m.Run9ValidationError(
            "build_practice_split_manifest(): expected_corpus_identity must be a non-empty "
            "string — omitting corpus identity verification is not permitted (fail-closed)"
        )
    root = Path(corpus_root)
    identity_hash, song_ids = _enumerate_pjs_song_ids(root)
    if identity_hash != expected_corpus_identity:
        raise m.Run9ValidationError(
            "build_practice_split_manifest(): corpus identity mismatch — recomputed "
            f"expanded_corpus_identity_sha256={identity_hash!r} does not match expected "
            f"{expected_corpus_identity!r} (fail-closed rejection; RUN9-BIRTH-PREP-1 §B — "
            "the supplied corpus_root does not match the pinned PJS corpus)"
        )

    split = assign_split(song_ids)
    row_order = split["row_order"]
    split_of_song: Dict[str, str] = {}
    for split_name in ("training", "validation", "sealed_holdout"):
        for song_id in split[split_name]:
            split_of_song[song_id] = split_name
    sample_inventory = [
        f"{rank:04d}|{split_of_song[song_id]}|{song_id}" for rank, song_id in enumerate(row_order)
    ]

    manifest: Dict[str, Any] = {
        "schema": m.SCHEMA_PRACTICE_AUDIO_SPLIT_MANIFEST,
        "pjs_source_archive_sha256": pjs_source_archive_sha256,
        "expanded_corpus_identity_sha256": expected_corpus_identity,
        "training_split_sha256": _canonical_song_list_sha256(split["training"]),
        "validation_split_sha256": _canonical_song_list_sha256(split["validation"]),
        "sealed_holdout_sha256": _canonical_song_list_sha256(split["sealed_holdout"]),
        "row_order_sha256": _canonical_song_list_sha256(row_order),
        "sample_inventory": sample_inventory,
        "rights_source_class": PRACTICE_RIGHTS_SOURCE_CLASS,
        "is_raw_audio": True,
        "excludes_correct_technique_parameters": True,
        "identical_bytes_and_order_across_founders": True,
        "row_ids": {
            "training": split["training"],
            "validation": split["validation"],
            "sealed_holdout": split["sealed_holdout"],
        },
        "note": _NEAR_DUPLICATE_BOUNDARY_NOTE,
    }
    m.validate_practice_split_manifest(manifest)
    return manifest


def _canonical_song_list_sha256(song_ids: Sequence[str]) -> str:
    """正規形 = `json.dumps(obj, sort_keys=True, ensure_ascii=False,
    separators=(",", ":"))` UTF-8（既存 pin 規約と同一 —
    `run9_schema.compute_file_sha256()` docstring「正規形（canonical）規約」
    節参照）。"""
    return m._compute_canonical_pin_sha256(list(song_ids))  # noqa: SLF001 - sibling module


def dump_practice_split_manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    """manifest を凍結直列化（indent=2, sort_keys=True, ensure_ascii=False
    + 末尾改行）でバイト列化する。`run9_schema.issue_founder_genome_
    document()` と同じ直列化規約を踏襲する。"""
    return (json.dumps(dict(manifest), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


# ---------------------------------------------------------------------------
# advisory 音響 inventory sidecar（契約 pin 対象外）。manifest を入力に
# **できる**が、manifest 生成関数（上記）はこのモジュールの音響解析関数を
# 一切呼ばない——データフローは一方向のみ（sidecar ← manifest、逆方向不可）。
# ---------------------------------------------------------------------------

SCHEMA_PRACTICE_ACOUSTIC_INVENTORY_SIDECAR = "run9-practice-acoustic-inventory-sidecar/1.0"

_VOWELS_5 = ("a", "i", "u", "e", "o")
# HTS 標準の .lab 時刻単位（100ns）。donor_bank_lab.py LAB_TIME_UNIT_S と同一規約。
_LAB_TIME_UNIT_S = 1e-7
_PITCH_ROUND_HZ = 1  # round(hz, 1) — 0.1 Hz 丸め


@dataclass(frozen=True)
class _LabPhonemeLite:
    start_s: float
    end_s: float
    phoneme: str


def _parse_lab_text_lite(text: str) -> List[_LabPhonemeLite]:
    """HTS 形式 .lab（`start_100ns end_100ns phoneme` の空白区切り3列）の
    最小パーサー——`donor_bank_lab.parse_lab_text()` と同一フォーマット
    理解の独立再実装（`donor_bank_lab` は `pyworld` 依存の import 閉包を
    持つため import しない — モジュール docstring 参照）。advisory sidecar
    専用であり `donor_bank_lab` の mora グルーピング等の正典ロジックを
    置き換える意図はない。"""
    out: List[_LabPhonemeLite] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        start_raw, end_raw, phoneme = parts
        out.append(
            _LabPhonemeLite(
                start_s=int(start_raw) * _LAB_TIME_UNIT_S,
                end_s=int(end_raw) * _LAB_TIME_UNIT_S,
                phoneme=phoneme,
            )
        )
    return out


def _phoneme_class(label: str) -> str:
    if label in ("pau", "xx"):
        return "silence_or_unclear"
    if label == "N":
        return "moraic_nasal"
    if label == "cl":
        return "geminate_closure"
    if label in _VOWELS_5:
        return "vowel"
    return "consonant"


def _measure_phoneme_classes(lab_path: Path) -> Dict[str, Any]:
    """phrase 数（`pau` で区切られた非無音区間の連続数）と音素クラス別
    件数を .lab から求める（advisory・非校正計器 — sidecar note 参照）。"""
    phonemes = _parse_lab_text_lite(lab_path.read_text(encoding="utf-8"))
    class_counts: Dict[str, int] = {}
    phrase_count = 0
    in_phrase = False
    for phoneme in phonemes:
        cls = _phoneme_class(phoneme.phoneme)
        class_counts[cls] = class_counts.get(cls, 0) + 1
        if phoneme.phoneme == "pau":
            in_phrase = False
        else:
            if not in_phrase:
                phrase_count += 1
            in_phrase = True
    return {"phrase_count": phrase_count, "phoneme_class_counts": dict(sorted(class_counts.items()))}


def _measure_pitch_range_hz(wav_path: Path) -> Optional[Dict[str, float]]:
    """librosa.pyin による voiced フレームの pitch range（0.1 Hz 丸め）。
    「校正済み計器の主張はしない」——閾値較正・帯域検証は未実施の生の
    pyin 出力そのもの。極短尺 fixture（数百サンプル）等で pyin が例外を
    投げる/全 unvoiced を返す場合は None（advisory な欠測として扱う——
    manifest 側の fail-closed 契約には影響しない）。"""
    import librosa

    try:
        y, sr = librosa.load(str(wav_path), sr=None, mono=True)
        if y.size == 0:
            return None
        f0, voiced_flag, _voiced_prob = librosa.pyin(
            y, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C7"), sr=sr
        )
    except Exception:  # noqa: BLE001 - advisory best-effort measurement, never fail-closed
        return None
    if f0 is None:
        return None
    voiced_mask = voiced_flag if voiced_flag is not None else ~np.isnan(f0)
    voiced = f0[voiced_mask]
    voiced = voiced[~np.isnan(voiced)]
    if voiced.size == 0:
        return None
    return {
        "min_hz": round(float(np.min(voiced)), _PITCH_ROUND_HZ),
        "max_hz": round(float(np.max(voiced)), _PITCH_ROUND_HZ),
    }


def _song_id_is_lexically_safe(song_id: str) -> bool:
    """`song_id` が corpus_root 配下の単純な相対単純名（`pjsNNN` 相当）で
    あるかを語彙的に判定する（P2 修正・PR #321 review: `song_id` は
    パス区切りを含まず・`..` 成分を持たず・絶対パスでなく・空でないこと。
    過度に `pjsNNN` 命名へ結合せず汎用の語彙検査とする——
    `donor_bank_utau._wav_ref_is_basename()` と同じ判定パターン）。
    """
    if not song_id:
        return False
    if "/" in song_id or "\\" in song_id:
        return False
    if song_id in (".", ".."):
        return False
    lexical = Path(song_id)
    if lexical.is_absolute() or ".." in lexical.parts:
        return False
    return lexical.name == song_id


def _reject_song_ids_outside_corpus_root(song_ids: Sequence[str], root: Path) -> None:
    """`song_ids`（`build_acoustic_inventory_sidecar()` の `manifest`
    引数由来 — 外部入力）を read/existence 判定より**前**に二段ガードする:
    ①`_song_id_is_lexically_safe()` による語彙的拒否 ②`resolved =
    (root / song_id).resolve()` が `root.resolve()` 配下にあることの包含
    検査（`Path.is_relative_to`）。`manifest` は
    `validate_practice_split_manifest()` を通過する任意の同形 dict を
    受理するため、`row_ids` の `song_id` が絶対パスや `../` 脱出（例:
    `song_id='/tmp/evil'` → `/tmp/evil.lab`）を含むと `Path` 結合が
    `corpus_root` を破棄/上方解決し、宣言外のファイルを読んで sidecar に
    記録してしまう（PR #321 review 指摘）。1 件でも違反すれば fail-closed
    で `Run9ValidationError`（部分読み取りをしない——呼び出し元は本関数が
    正常終了した場合にのみ read/exists へ進んでよい）。
    """
    root_resolved = root.resolve()
    violations: List[str] = []
    for song_id in song_ids:
        if not _song_id_is_lexically_safe(song_id):
            violations.append(song_id)
            continue
        candidate = root / song_id
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError):
            violations.append(song_id)
            continue
        if not resolved.is_relative_to(root_resolved):
            violations.append(song_id)
    if violations:
        raise m.Run9ValidationError(
            "build_acoustic_inventory_sidecar(): manifest row_ids contain song_id(s) that "
            f"escape corpus_root {root_resolved} (rejected as path traversal / absolute "
            f"path / symlink escape, before any read or existence check): {sorted(violations)}"
        )


def build_acoustic_inventory_sidecar(
    corpus_root: str | Path, manifest: Mapping[str, Any]
) -> Dict[str, Any]:
    """`manifest`（`build_practice_split_manifest()` の出力、または
    `validate_practice_split_manifest()` を通過する任意の同形の dict）の
    `row_ids` を入力に、song ごとの pitch range（librosa.pyin）+ phrase 数・
    音素クラス（.lab 由来）を持つ advisory sidecar を構築する。

    `RUN9_CONTRACT.yaml` の pin 対象**外**（`practice_audio_split_
    manifest_sha` はこの sidecar の内容に一切依存しない）。`manifest` 引数
    は読み取り専用（`validate_practice_split_manifest()` で自己整合性を
    確認するのみ、変更しない）——型的分離: 本関数は manifest を消費する
    が、`build_practice_split_manifest()` は本関数（または `librosa`）を
    一切呼ばない一方向のデータフロー。

    **sidecar の契約**: 本関数が計測を行うのは「本 builder（
    `build_practice_split_manifest()`/`assign_split()`/
    `_enumerate_pjs_song_ids()`）が当該 `corpus_root` から実際に生成した
    manifest」のみである。それ以外（別コーパス由来・手改変・偽造）の
    manifest は、以下 4 層のいずれかで read/existence 判定より**前**に
    fail-closed 拒否する（PR #321 review 三巡にわたる指摘の到達点）:

    1. **corpus identity**（Fix 3）: `corpus_root` 自体が `manifest` の
       由来コーパスであることを検証する。corpus A 用の valid manifest に
       別内容の corpus_root B（同名 song_id を含むが中身が異なる）を渡す
       と、song_id の形式のみを見るガードは素通りし、B を計測して A の
       inventory として返してしまう——sidecar 自体に検証済み corpus
       digest も載らないため provenance が黙って失われる（advisory 計測
       値でも provenance は必須）。`_enumerate_pjs_song_ids()`（
       `build_practice_split_manifest()` が使う既存の identity 再計算
       関数——新規実装しない）で `corpus_root` から
       `expanded_corpus_identity_sha256` を再計算し、`manifest` 側の値
       （`build_practice_split_manifest()` の照合と同一規約——明示値
       必須・照合スキップ不可）と厳密一致しなければ拒否する。一致した
       検証済み値は `verified_expanded_corpus_identity_sha256` として
       sidecar 出力へ保持する。
    2. **split assignment**（Fix 4）: `row_ids`（training/validation/
       sealed_holdout、各 3 split とも順序込み）を、Fix 3 で確定した
       corpus 由来 song_id 列に対して `assign_split()` を再適用した
       決定論割当と完全一致させる。training↔sealed_holdout の ID
       入れ替え等で `row_ids` だけを改変しても（宣言済み 6 hash は
       据え置きでも）この層で拒否される。
    3. **hash pin**（Fix 4）: `training_split_sha256`/
       `validation_split_sha256`/`sealed_holdout_sha256`/
       `row_order_sha256` を builder 自身の
       `_canonical_song_list_sha256()` で再計算し、宣言値と厳密一致を
       照合する。`row_ids` と 6 hash を両方偽造して自己整合させた
       manifest でも、真の決定論割当（層 2）とは一致しないため拒否
       される。
    4. **final path**（Fix 5）: 層 2/3 を通過した song_id から構成する
       最終消費パス（`root/song_id/song_id.lab` /
       `root/song_id/song_id_song.wav`）**それぞれ**を `exists()`/
       `read_text()`/`librosa.load()` より前に resolve し
       `corpus_root` 配下包含検査を通す（`_reject_paths_outside_corpus_
       root()` — `_enumerate_pjs_song_ids()` と共有する既存関数）。
       song_id 自体・その親ディレクトリ（`root/song_id`）が安全でも、
       最終ファイルだけが corpus_root 外を指す symlink である経路は
       layer 1-3 では捕捉できない（symlink の中身が corpus 内の実体と
       バイト同一なら corpus identity も変化しないため）——独立した
       防御層として必須。

    いずれの層も不一致は `Run9ValidationError` で fail-closed（部分計測・
    部分出力なし）。
    """
    manifest_dict = dict(manifest)
    m.validate_practice_split_manifest(manifest_dict)
    root = Path(corpus_root)

    # --- layer 1: corpus identity（Fix 3） -----------------------------
    expected_corpus_identity = manifest_dict["expanded_corpus_identity_sha256"]
    verified_corpus_identity, enumerated_song_ids = _enumerate_pjs_song_ids(root)
    if verified_corpus_identity != expected_corpus_identity:
        raise m.Run9ValidationError(
            "build_acoustic_inventory_sidecar(): corpus identity mismatch — recomputed "
            f"expanded_corpus_identity_sha256={verified_corpus_identity!r} for corpus_root="
            f"{root!r} does not match manifest.expanded_corpus_identity_sha256="
            f"{expected_corpus_identity!r} (fail-closed rejection; PR #321 review Fix 3 — the "
            "supplied corpus_root is not the corpus this manifest was built from)"
        )

    # --- layer 2/3: split assignment + hash pin（Fix 4） ----------------
    row_ids = manifest_dict["row_ids"]
    canonical_split = assign_split(enumerated_song_ids)
    for split_name in ("training", "validation", "sealed_holdout"):
        if list(row_ids[split_name]) != canonical_split[split_name]:
            raise m.Run9ValidationError(
                "build_acoustic_inventory_sidecar(): manifest.row_ids does not match the "
                "deterministic split assignment recomputed from the verified corpus "
                f"inventory (split={split_name!r} differs) — forged/reordered/swapped "
                "row_ids are rejected fail-closed before any measurement (PR #321 review "
                "Fix 4)"
            )
    canonical_song_lists_by_hash_field = {
        "training_split_sha256": canonical_split["training"],
        "validation_split_sha256": canonical_split["validation"],
        "sealed_holdout_sha256": canonical_split["sealed_holdout"],
        "row_order_sha256": canonical_split["row_order"],
    }
    for hash_field, canonical_song_list in canonical_song_lists_by_hash_field.items():
        expected_hash = _canonical_song_list_sha256(canonical_song_list)
        if manifest_dict[hash_field] != expected_hash:
            raise m.Run9ValidationError(
                f"build_acoustic_inventory_sidecar(): manifest.{hash_field}="
                f"{manifest_dict[hash_field]!r} does not match the value recomputed from the "
                f"verified corpus inventory {expected_hash!r} — fail-closed rejection before "
                "any measurement (PR #321 review Fix 4)"
            )

    # 層 1-3 をすべて通過した以上、row_ids は canonical_split（corpus 由来の
    # 決定論割当）と要素・順序ともに同一であることが確定している。以降は
    # 検証済みの canonical_split をそのまま消費する（manifest 側の生値へ
    # 戻らない）。
    _reject_song_ids_outside_corpus_root(
        [
            song_id
            for split_name in ("training", "validation", "sealed_holdout")
            for song_id in canonical_split[split_name]
        ],
        root,
    )

    # --- layer 4: final path containment（Fix 5） -----------------------
    entries_meta: List[Tuple[str, str, Path, Path]] = []
    final_paths: List[Path] = []
    for split_name in ("training", "validation", "sealed_holdout"):
        for song_id in canonical_split[split_name]:
            wav_path = root / song_id / f"{song_id}_song.wav"
            lab_path = root / song_id / f"{song_id}.lab"
            entries_meta.append((split_name, song_id, wav_path, lab_path))
            final_paths.append(wav_path)
            final_paths.append(lab_path)
    _reject_paths_outside_corpus_root(final_paths, root)

    songs: List[Dict[str, Any]] = []
    for split_name, song_id, wav_path, lab_path in entries_meta:
        entry: Dict[str, Any] = {"song_id": song_id, "split": split_name}
        if lab_path.exists():
            entry.update(_measure_phoneme_classes(lab_path))
        else:
            entry["phrase_count"] = None
            entry["phoneme_class_counts"] = None
        entry["pitch_range_hz"] = _measure_pitch_range_hz(wav_path) if wav_path.exists() else None
        songs.append(entry)
    return {
        "schema": SCHEMA_PRACTICE_ACOUSTIC_INVENTORY_SIDECAR,
        "verified_expanded_corpus_identity_sha256": verified_corpus_identity,
        "note": (
            "advisory only — RUN9_CONTRACT.yaml の pin 対象外。校正済み計器の主張はしない"
            "（librosa.pyin 実測値・0.1 Hz 丸め、閾値較正・帯域検証は未実施の生の観測値）。"
            "advisory な計測値であっても provenance（どの corpus_root を計測したか）は必須の"
            "ため、`verified_expanded_corpus_identity_sha256` に manifest の "
            "`expanded_corpus_identity_sha256` と一致検証済みの再計算値を保持する。"
            "入力検証は corpus identity・split assignment・hash pin・最終パス解決の4層で"
            "閉じている（PR #321 review Fix 3/4/5）。"
        ),
        "songs": songs,
    }
