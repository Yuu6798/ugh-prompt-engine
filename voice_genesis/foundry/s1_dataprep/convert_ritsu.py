"""D2: 波音リツ VCV 録音 (強連続音 Ver1.5.1) -> DiffSinger acoustic 学習形式変換器。

`DESIGN_S1_p2poc.md` D2（データ工場・リツ VCV -> 音素粒度変換）の実装。
`oto.ini`（offset/consonant/blank/preutterance/overlap）から音素粒度
ph_seq/ph_dur を、録音ストリング単位（1 wav = 1 セグメント）で組み立てる。
scratchpad スパイク `s1b_ritsu_dataset/d2_convert.py` の清書版
（ハードコードパス排除・argparse 化・型ヒント）。境界導出規則・fallback table・
実装決定の根拠は `s1b_dataset_record.md` に逐語記録済みのため、本 docstring は
規則の要約のみに留める。

境界導出規則（一次ソースは `s1b_dataset_record.md` §2）:

1. 1 wav ファイル内の全 oto エイリアスを offset_ms 昇順に並べる。この順序は
   録音ストリングのモーラ順序と一致する。
2. alias_i の対象モーラの音素列 phonemes_i を辞書（Ritsu 公式 617 語彙
   dsdur/dsdict.yaml + 本スクリプトの fallback table）から引く。
3. alias_i が占有する非重複区間を [offset_i, boundary_{i+1}) と定義する。
   boundary_{i+1} は次エイリアスの offset（存在すれば）、最終エイリアスは
   `cutoff_position_ms(offset_i, blank_i, wav_duration_ms)`
   （`donor_bank_utau.cutoff_position_ms` を read-only import）。
4. phonemes_i の内訳で分割: 1 音素は区間全体、2 音素は
   `consonant_end_ms = offset_i + consonant_ms_i` で子音/母音に分割、
   3 音素（fallback 由来のみ）は `[offset_i, consonant_end_ms)` を均等按分
   したうえで母音区間を追加する（oto の境界は 1 つしかないための近似。
   実装決定・`s1b_dataset_record.md` §2 参照）。
5. 先頭/末尾の無音は `SP_THRESHOLD_MS` を超えたときのみ `SP` として挿入する。
6. AP（息継ぎ）検出は本 D2 では実施しない（UTAU oto.ini に相当情報源が無い。
   既知の制限）。

review #263 R5 P1: `--out-dir` が `--voicebank-root`/`--dsdict` と衝突・
内包関係にある場合、staging 構築を始める前に fail-closed で拒否する
（`build_dataset.py` R4 の `OutputCollisionError` と同型）。

review #263 R7 で 2 件追加修正（`convert_pjs.py` と同型のファミリー修正）:
(1) 衝突ガードの包含判定を双方向化し、保護 root が出力配下にある場合
（例: `--out-dir=/tmp/work`, `--voicebank-root=/tmp/work/voicebank`）も
拒否する。(2) `_swap_into_place` の 2 段 rename を `try/except
BaseException` で保護し、新世代 rename 失敗時は退避済み旧世代を元パスへ
復元してから再送出する。

review #263 R9 P1（`convert_pjs.py` と同一のファミリー修正）: 衝突ガード
が `--out-dir` 本体のみを検査し、`_swap_into_place` が実際に削除・rename
する派生パス（`<out-dir>.old`・`<out-dir>.staging-<pid>`）を対象外に
していた。例えば `--out-dir=/tmp/published`,
`--voicebank-root=/tmp/published.old` は事前チェックを素通りしたのち、
公開時に `old_dir` の `rmtree` が `voicebank_root`（保護入力）そのものを
削除する。派生パスもガード対象に含める。

review #263 R13 P2: 共有アダプタ `donor_bank_utau.parse_oto_text` は
フィールド数不一致・非数値値の行を黙って skip する実装決定（同モジュール
docstring 参照）のため、`oto.ini` が truncated/malformed な行を含んでいても
`parse_oto_ini` は例外を出さず縮小したエントリ集合を返す。参照先 WAV が
実在すれば `missing_wavs` ガードも発火しないため、音素区間が黙ってコーパス
から欠落したまま成功報告してしまう。本ファイルは（共有モジュールの既存
skip 仕様は他の呼び出し元のため変更せず）全 pitch dir の oto.ini をあらかじめ
走査し、`parse_oto_text` と同一規則で棄却される行（pitch dir・行番号・内容）
を全収集してから、実際の WAV 読み込み/書き込みを一切始める前に fail-closed
で拒否する（`missing_wavs`/`wav_ref_violations` と同じ「全収集してから公開前
に止める」設計）。

review #263 R14 P2: R13 の `_find_malformed_oto_lines` は行が残っているが
malformed（フィールド数不一致・非数値値）な場合のみ検出できる。有効な行が
丸ごと削除された場合（`=` を含む行自体が存在しない）は R13 検査を素通りし、
`parse_oto_text` も件数が減ったことを黙って許容するため、縮小コーパスの
まま成功報告してしまう余地が残っていた。素材 pin 済み voicebank（強連続音
Ver1.5.1）の実測値（`EXPECTED_OTO_ENTRIES` = A3/F4 各 1237 エントリ）を
期待契約として保持し、パース後の実エントリ総数と照合する。不一致があれば
（行削除・truncation による `=` 喪失のいずれでも）全 pitch dir 分を集約して
から、実際の読み込み/書き込みを一切始める前に fail-closed で拒否する
（`--expected-oto-entries` で override 可。別 voicebank を意図的に使う運用
のための脱出弁で、既定は厳格）。

review #263 R15 P2: `_find_malformed_oto_lines` の非数値判定は Python の
`float()` に委ねていたが、`float("nan")`/`float("inf")`/`float("-inf")` は
`ValueError` を送出せず受理されるため、oto.ini の数値フィールドが
非有限値の行は malformed として検出されず、件数も減らないため R14 の
`EXPECTED_OTO_ENTRIES` 契約チェックもすり抜ける。境界計算
（`build_segment_for_wav` の offset/consonant/blank 演算）が非有限値で汚染
され、非有限 `ph_dur` を生成し得る。`math.isfinite()` を追加し、非有限値も
malformed 行として列挙する（`build_dataset.validate_speaker` 側の下流検証も
同時に是正、同ファイル該当箇所参照）。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import sys
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import soundfile as sf

# donor_bank_utau は本リポジトリ内の既存アダプタを read-only import する
# （移植しない。境界計算のバグ修正 (review #262) を経た実績コードのため）。
_ADAPTER_DIR = Path(__file__).resolve().parents[1] / "adapter"
if str(_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_DIR))
import donor_bank_utau as dbu  # noqa: E402  read-only import

DEFAULT_PITCH_DIRS: Tuple[str, ...] = ("A3", "F4")
SP_THRESHOLD_MS = 20.0
MIN_PHONEME_MS = 1.0  # 退化区間 (<=0ms) のガード用フロア

# 素材 pin 済み voicebank（波音リツ 強連続音 Ver1.5.1）の実測エントリ総数
# (review #263 R14 P2)。`_convert_into` はパース後の実エントリ総数をこの
# 契約値と照合し、不一致（行削除・truncation による `=` 喪失）を公開前に
# fail-closed で検出する。別 voicebank を意図的に使う場合は
# `--expected-oto-entries` で override（`none` で無効化）する。
EXPECTED_OTO_ENTRIES: Dict[str, int] = {"A3": 1237, "F4": 1237}

# ---------------------------------------------------------------------------
# 1. Ritsu 公式辞書 (617 語彙) パーサ（PyYAML の bool 誤変換回避のため手書き）
# ---------------------------------------------------------------------------


def parse_dsdict(path: Path) -> Dict[str, List[str]]:
    """dsdur/dsdict.yaml (617 entries) を手書きパースする。

    PyYAML の `safe_load` は YAML1.1 の bool リテラル解決規則により grapheme
    "no" を `False` へ誤変換する（実測。`s1b_dataset_record.md` §2）。ここでは
    正規表現による行単位パースで回避する。重複キー（実測 29 件、"へ" のみ値が
    `[h,e]`/`[h,E]` で不一致・他は同一値の重複）は最初の出現を採用する
    （"へ" は非無声化の `[h,e]` が先に出現し、より基本的な発音のため妥当）。
    """
    entries: "OrderedDict[str, List[str]]" = OrderedDict()
    grapheme: Optional[str] = None
    phonemes: List[str] = []

    def flush() -> None:
        if grapheme is not None and grapheme not in entries:
            entries[grapheme] = phonemes

    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.rstrip("\n")
            m = re.match(r"^- grapheme:\s*(.*)$", s)
            if m:
                flush()
                grapheme = m.group(1).strip()
                phonemes = []
                continue
            m2 = re.match(r"^\s*-\s+(\S+)\s*$", s)
            if m2 and s.strip() != "phonemes:":
                phonemes.append(m2.group(1).strip())
    flush()
    return dict(entries)


# ---------------------------------------------------------------------------
# 2. fallback table（辞書 617 語彙に無い拡張カタカナ/2 文字グライド表記。
#    実測: A3 oto.ini 1237 エイリアス中 41 モーラ種・285 エイリアス (23.0%)
#    が該当。写像差分表は `s1b_dataset_record.md` §3.1 に転記済み）
# ---------------------------------------------------------------------------

FALLBACK_MORA_PHONEMES: Dict[str, List[str]] = {
    # う行 + 小書き母音（w オンセット。"を"->[w,o] の辞書パターンを外挿）
    "うぃ": ["w", "i"], "うぇ": ["w", "e"], "うぉ": ["w", "o"],
    # す/ず + 小書き（さ行 z/s オンセットの拡張。"ず"->[z,u] を外挿）
    "すぃ": ["s", "i"],
    "ずぃ": ["z", "i"], "ずぃぇ": ["z", "y", "e"], "ずゃ": ["z", "y", "a"],
    "ずゅ": ["z", "y", "u"], "ずょ": ["z", "y", "o"],
    # づ行（辞書実測: "づ"->[d,u]。"ず"[z,u]とは異なる=d オンセット系列）
    "づぁ": ["d", "a"], "づぃ": ["d", "i"], "づぃぇ": ["d", "y", "e"],
    "づぇ": ["d", "e"], "づぉ": ["d", "o"],
    "づゃ": ["d", "y", "a"], "づゅ": ["d", "y", "u"], "づょ": ["d", "y", "o"],
    # で行 2 文字グライド（"でぃ"->[d,i] は辞書にあるが "でぃぇ" は無い）
    "でぃぇ": ["d", "y", "e"],
    # ふ行拡張
    "ふぃぇ": ["f", "y", "e"], "ふゃ": ["f", "y", "a"], "ふょ": ["f", "y", "o"],
    # く/ぐ + 小書き母音（labialized kw/gw。標準ローマ字表記に準拠）
    "クァ": ["k", "w", "a"], "クィ": ["k", "w", "i"], "クゥ": ["k", "w", "u"],
    "クェ": ["k", "w", "e"], "クォ": ["k", "w", "o"],
    "グァ": ["g", "w", "a"], "グィ": ["g", "w", "i"], "グゥ": ["g", "w", "u"],
    "グェ": ["g", "w", "e"], "グォ": ["g", "w", "o"],
    # 撥音カタカナ表記（"ん"->[N] と同義）
    "ン": ["N"],
    # ヴ行（唇歯摩擦音 v。phonemes.txt に "vf" が予約済みだが dsdict 617 語彙
    # には ヴ 系エントリが 1 件も無い。"vf" 単独 + 母音/グライドで構成する）
    "ヴ": ["vf", "u"], "ヴぁ": ["vf", "a"], "ヴぃ": ["vf", "i"],
    "ヴぃぇ": ["vf", "y", "e"], "ヴぇ": ["vf", "e"], "ヴぉ": ["vf", "o"],
    "ヴゃ": ["vf", "y", "a"], "ヴゅ": ["vf", "y", "u"], "ヴょ": ["vf", "y", "o"],
}


def build_mora_dict(dsdict_path: Path) -> Dict[str, List[str]]:
    merged = dict(parse_dsdict(dsdict_path))
    merged.update(FALLBACK_MORA_PHONEMES)
    return merged


# ---------------------------------------------------------------------------
# 3. 1 wav ファイル -> ph_seq/ph_dur 変換
# ---------------------------------------------------------------------------


def _wav_ref_is_basename(wav_filename: str) -> bool:
    """oto.ini の wav 参照が pitch dir 直下の basename であるかを判定する
    （`adapter/donor_bank_utau._wav_ref_is_basename` と同型判定。review #263
    R12 P1: リポジトリの既存慣例（record スクリプト群は共有モジュール新設
    ではなく各ファイルへコピペ実装する）に倣い、本ファイルへ複製する）。"""
    if "/" in wav_filename or "\\" in wav_filename:
        return False
    if wav_filename in ("", ".", ".."):
        return False
    lexical = Path(wav_filename)
    if lexical.is_absolute() or ".." in lexical.parts:
        return False
    return lexical.name == wav_filename


def _reject_wav_paths_outside_root(
    pdir: Path, pdir_name: str, wav_filenames: Sequence[str], root: Path
) -> List[str]:
    """oto.ini が参照する WAV ファイル名を検査し、voicebank root 外を指す
    参照を検出する（review #263 R12 P1: `adapter/donor_bank_utau.
    _reject_wav_paths_outside_root` と同型のファミリー修正）。

    二段検査: (1) 語彙的検査（絶対パス・`..` 成分・非 basename の `/`/`\\`
    区切りネスト参照を即座に拒否）→ (2) `.resolve()`（symlink 解決込み）した
    上で voicebank root 配下に収まっているかを検査。語彙的検査を先に行うのは
    `donor_bank_utau` と同じ理由（symlink 解決前に脱出パターンを弾く。かつ
    resolve だけでは `A3/../F4/foo.wav` のように root 配下には留まりながら
    自分の pitch dir を脱出する参照を見逃す）。

    黙って除外せず、違反エントリ（`<pdir_name>/<wav_filename>` 形式）の
    一覧を返す。呼び出し側が全 pitch dir 分の違反を集約してから、実際の
    読み込みを一切始める前に一括で fail-closed する。
    """
    root_resolved = root.resolve()
    violations: List[str] = []
    for wav_filename in wav_filenames:
        if not _wav_ref_is_basename(wav_filename):
            violations.append(f"{pdir_name}/{wav_filename}")
            continue
        candidate = pdir / wav_filename
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError):
            violations.append(f"{pdir_name}/{wav_filename}")
            continue
        if not resolved.is_relative_to(root_resolved):
            violations.append(f"{pdir_name}/{wav_filename}")
    return violations


class WavPathEscapeError(ValueError):
    """P1 修正 (review #263 R12): oto.ini が voicebank root 外を指す WAV 参照
    （絶対パス・`..` 脱出・非 basename ネスト参照・symlink 逃避）を含む場合に
    送出する（fail-closed。実際の読み込みを一切始める前、`_convert_into` の
    冒頭で全 pitch dir 分の違反を集約検出してから送出する）。"""


class MalformedOtoEntryError(ValueError):
    """P2 修正 (review #263 R13): `dbu.parse_oto_text` が黙って skip する
    malformed 行（フィールド数不一致・非数値値。同モジュールの実装決定）を
    本変換器は許容しない。棄却行を pitch dir・行番号・内容ごと全収集して
    から、実際の読み込み/書き込みを一切始める前に fail-closed で拒否する
    （`WavPathEscapeError`/`MissingSourceWavError` と同じ「全収集してから
    公開前に止める」設計）。"""


class UnexpectedOtoEntryCountError(ValueError):
    """P2 修正 (review #263 R14): pitch dir ごとにパースされた oto エントリ
    総数が期待契約（既定: `EXPECTED_OTO_ENTRIES`、素材 pin 済み voicebank
    強連続音 Ver1.5.1 の実測値）と一致しない場合に送出する（fail-closed）。

    `_find_malformed_oto_lines`（R13）は行が残っているが malformed な場合
    のみ検出でき、有効な行が丸ごと削除された場合（`=` を含む行自体が消える）
    は検出できない。パース後の実エントリ総数を契約値と照合することで、
    行削除・truncation による `=` 喪失のいずれも公開前に検出する。全
    pitch dir 分を集約してから、実際の読み込み/書き込みを一切始める前に
    fail-closed で拒否する（`MalformedOtoEntryError`/`WavPathEscapeError`
    と同じ「全収集してから公開前に止める」設計）。"""


def _parse_expected_oto_entries_arg(value: str) -> Dict[str, int]:
    """`--expected-oto-entries` の値を解釈する (review #263 R14)。

    - `"none"`（大小文字無視）: 期待件数契約を無効化する（空 dict を返す =
      どの pitch dir も件数チェック対象外になる）
    - `"A3=1237,F4=1237"`: 明示的な override dict を返す。素材 pin 済み
      voicebank（強連続音 Ver1.5.1）以外を意図的に使う場合の脱出弁。
    """
    if value.strip().lower() == "none":
        return {}
    result: Dict[str, int] = {}
    for pair in value.split(","):
        pair = pair.strip()
        if not pair:
            continue
        key, sep, val = pair.partition("=")
        key = key.strip()
        val = val.strip()
        if not sep or not key or not val:
            raise ValueError(f"invalid --expected-oto-entries entry: {pair!r}")
        try:
            result[key] = int(val)
        except ValueError as exc:
            raise ValueError(
                f"invalid --expected-oto-entries count for {key!r}: {val!r}"
            ) from exc
    return result


def _find_malformed_oto_lines(text: str) -> List[Tuple[int, str]]:
    """`dbu.parse_oto_text` と同一の解析規則で判定し、黙って skip される
    malformed 行を行番号（1-origin）付きで列挙する。

    共有モジュール `donor_bank_utau.parse_oto_text` を変更すると他の
    呼び出し元（既存の skip 仕様に依存し得る）の挙動まで変わってしまう
    ため、判定規則のみを本ファイルへ複製する（`_wav_ref_is_basename` と
    同じ既存パターン。R12 P1 コメント参照）。空行・`"="` を含まない行
    （コメント/区切りとして無視される）は対象外。

    review #263 R15 P2: `float()` は `"nan"`/`"inf"`/`"-inf"`（大小文字・
    符号違いを含む）を例外なく受理するため、非有限値は `math.isfinite()`
    で別途弾く（offset/consonant/blank の境界演算がこれらの値で汚染される
    のを公開前に検出するため）。"""
    malformed: List[Tuple[int, str]] = []
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        _wav_filename, _, rest = line.partition("=")
        parts = rest.split(",")
        if len(parts) != 6:
            malformed.append((lineno, raw_line))
            continue
        try:
            values = [float(value) for value in parts[1:]]
        except ValueError:
            malformed.append((lineno, raw_line))
            continue
        if not all(math.isfinite(value) for value in values):
            malformed.append((lineno, raw_line))
    return malformed


class MissingSourceWavError(FileNotFoundError):
    """P2 修正 (review #263 R4): oto が参照する WAV が実在しない場合、黙って
    skip して縮小コーパスの成功を報告するのではなく、欠落ファイル名を全収集
    してから公開前に fail-closed で拒否する（`_convert_into` が例外を送出
    すると `convert()` の `except BaseException` が staging を削除して
    `out_dir` を swap しないため、既存の有効な成果物は無事のまま残る）。"""


def _ms_to_s(ms: float) -> float:
    return max(0.0, ms) / 1000.0


def build_segment_for_wav(
    entries: List["dbu.OtoEntry"],
    pitch_dirs: Sequence[str],
    mora_dict: Dict[str, List[str]],
    wav_duration_ms: float,
) -> Tuple[List[str], List[float], dict]:
    """1 wav の全エイリアス（offset 昇順）から (ph_seq, ph_dur[s], stats) を返す。"""
    # 実測 (F4 _ががぎがぐげが.wav): alias 末尾に "↑"/"↓" が付く代替ピッチ
    # ベンドエイリアスが同一 offset に重複して存在する場合がある（1237×2
    # エントリ中 1 件のみ実測）。同一 offset は「同一音声位置への別名」なので
    # offset 昇順で最初に出現したエイリアス（矢印なし正規名が先）のみ採用し、
    # 以降の同一 offset エントリは破棄する（実装決定・record 記録）。
    ordered_all = sorted(entries, key=lambda e: e.offset_ms)
    ordered: List["dbu.OtoEntry"] = []
    seen_offsets: set = set()
    n_duplicate_offset = 0
    for e in ordered_all:
        if e.offset_ms in seen_offsets:
            n_duplicate_offset += 1
            continue
        seen_offsets.add(e.offset_ms)
        ordered.append(e)

    ph_seq: List[str] = []
    ph_dur: List[float] = []
    n_unmapped = 0
    n_sokuon = 0
    used_moras: List[str] = []

    boundaries: List[float] = []
    for i, e in enumerate(ordered):
        if i + 1 < len(ordered):
            boundaries.append(ordered[i + 1].offset_ms)
        else:
            boundaries.append(dbu.cutoff_position_ms(e.offset_ms, e.blank_ms, wav_duration_ms))

    first_offset = ordered[0].offset_ms if ordered else 0.0
    if first_offset > SP_THRESHOLD_MS:
        ph_seq.append("SP")
        ph_dur.append(_ms_to_s(first_offset))

    for e, boundary_ms in zip(ordered, boundaries):
        _prev, mora_kana, _is_init = dbu.parse_alias_mora(e.alias, pitch_dirs)
        if mora_kana == "っ":
            n_sokuon += 1
            continue
        phonemes = mora_dict.get(mora_kana)
        if phonemes is None:
            n_unmapped += 1
            continue
        used_moras.append(mora_kana)

        seg_start = max(0.0, e.offset_ms)
        seg_end = max(seg_start, boundary_ms)
        consonant_end = min(max(seg_start, e.offset_ms + e.consonant_ms), seg_end)

        if len(phonemes) == 1:
            spans = [(seg_start, seg_end)]
        elif len(phonemes) == 2:
            spans = [(seg_start, consonant_end), (consonant_end, seg_end)]
        else:  # len == 3 (fallback クラスタ、按分)
            mid = seg_start + (consonant_end - seg_start) / 2.0
            spans = [(seg_start, mid), (mid, consonant_end), (consonant_end, seg_end)]

        for ph, (s0, s1) in zip(phonemes, spans):
            dur_s = max(MIN_PHONEME_MS, (s1 - s0)) / 1000.0
            ph_seq.append(ph)
            ph_dur.append(dur_s)

    last_boundary = boundaries[-1] if boundaries else 0.0
    if wav_duration_ms - last_boundary > SP_THRESHOLD_MS:
        ph_seq.append("SP")
        ph_dur.append(_ms_to_s(wav_duration_ms - last_boundary))

    stats = dict(
        n_entries=len(ordered), n_unmapped=n_unmapped, n_sokuon=n_sokuon,
        n_duplicate_offset=n_duplicate_offset, used_moras=used_moras,
    )
    return ph_seq, ph_dur, stats


# ---------------------------------------------------------------------------
# 4. 全 pitch dir 走査 -> transcriptions.csv + 統計
# ---------------------------------------------------------------------------


def _swap_into_place(staging_dir: Path, out_dir: Path) -> None:
    """`staging_dir`（完全に構築済み）を `out_dir` へ原子的に差し替える。

    途中失敗時の新旧世代混在防止（review #263 R3 P1）: 旧 `out_dir` は削除
    せず `<out_dir>.old` へ退避してから `staging_dir` を最終名へ rename する
    （POSIX の `rename(2)` はディレクトリの置換をアトミックに行う）。次回
    実行時に前回の `.old` が残っていれば先に削除してから退避する。

    review #263 R7 P2（`gate_synth.py` の同型ヘルパーと同一のファミリー
    修正）: 2 段 rename は独立した 2 操作のため、旧世代の退避
    （`out_dir` -> `.old`）が成功した後に新世代の rename
    （`staging_dir` -> `out_dir`）が失敗（`KeyboardInterrupt` 含む）すると、
    正規パス `out_dir` が消失したまま旧世代は `.old` にしか存在しない状態に
    なる。新世代 rename を `except BaseException` で保護し、失敗時は退避
    済みの旧世代を `out_dir` へ復元してから再送出する。

    review #263 R8 P2（`gate_synth.py` の同型ヘルパーと同一のファミリー
    修正）: R7 の保護は新世代 rename のみを try で囲んでいたため、退避
    rename（`out_dir` -> `.old`）が完了した直後・try 進入前にも中断窓が
    残っていた。加えて、退避完了の判定を `evicted_old = True` という後続
    代入で行っていたため、`rename(2)` 自体は成功しているのに代入文自体が
    実行されない極めて狭い窓も理論上残る。退避 rename から公開 rename
    までの遷移全体を単一の try/except BaseException で覆うと同時に、
    「退避が完了したか」の判定をフラグ変数ではなく `old_dir.exists()` と
    いう実ファイルシステム状態の観測へ置き換える（このメソッド冒頭で
    `.old` を消去済みのため、except 到達時点で `old_dir` が存在するのは
    今回の退避 rename が成功した場合のみであり、フラグの代入タイミングに
    依存しない）。これにより両方の中断窓を閉じる。
    """
    old_dir = out_dir.parent / f"{out_dir.name}.old"
    if old_dir.exists():
        shutil.rmtree(old_dir)
    try:
        if out_dir.exists():
            out_dir.rename(old_dir)
        staging_dir.rename(out_dir)
    except BaseException:
        if old_dir.exists() and not out_dir.exists():
            old_dir.rename(out_dir)
        raise


def convert(
    voicebank_root: Path,
    dsdict_path: Path,
    out_dir: Path,
    pitch_dirs: Sequence[str] = DEFAULT_PITCH_DIRS,
    expected_oto_entries: Optional[Dict[str, int]] = None,
) -> dict:
    """リツ voicebank の pitch dir 群を変換し、`out_dir` に
    `transcriptions.csv` / `wavs/` / `provenance.json` / `d2_stats.json` を
    書く。冪等（既存 out_dir を再実行すると同一内容で上書きされる）。

    出力は `<out_dir>.staging-<pid>` に完全構築してから、成功時のみ
    `out_dir` と原子的に swap する（review #263 R3 P1: 途中失敗や再実行時に
    旧世代 WAV/CSV が新世代と混在するのを防ぐ）。失敗時は staging を削除し
    既存の `out_dir` はそのまま残す。統計 dict を返す（呼び出し側で JSON
    保存・print する）。

    `expected_oto_entries`（review #263 R14）: pitch dir 名 -> 期待エントリ
    総数。`None`（省略時）は既定の `EXPECTED_OTO_ENTRIES` を使う。空 dict
    (`{}`) を渡すと契約チェックを無効化する。"""
    out_dir = Path(out_dir)
    if expected_oto_entries is None:
        expected_oto_entries = dict(EXPECTED_OTO_ENTRIES)
    staging_dir = out_dir.parent / f"{out_dir.name}.staging-{os.getpid()}"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)
    try:
        summary = _convert_into(
            voicebank_root, dsdict_path, staging_dir, pitch_dirs, expected_oto_entries
        )
        _swap_into_place(staging_dir, out_dir)
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return summary


def _convert_into(
    voicebank_root: Path,
    dsdict_path: Path,
    out_dir: Path,
    pitch_dirs: Sequence[str],
    expected_oto_entries: Dict[str, int],
) -> dict:
    """`convert()` の本体。`out_dir`（呼び出し元では fresh な staging dir）
    に `transcriptions.csv` / `wavs/` / `provenance.json` / `d2_stats.json`
    を書き込む。"""
    mora_dict = build_mora_dict(dsdict_path)
    out_wavs = out_dir / "wavs"
    out_wavs.mkdir(parents=True, exist_ok=True)

    # review #263 R12 P1: 各 pitch dir の oto.ini を先に全件パースし、参照 WAV
    # の経路検査（字句検査 + resolve 後 root 包含検査の二段）を全 pitch dir
    # 分集約してから、実際の読み込み/書き込みを一切始める前に一括で
    # fail-closed する（`missing_wavs` と同じ「全収集してから公開前に止める」
    # 設計。字句・resolve のいずれかで違反した wav_filename は、後段の
    # 読み込みループでも同じファイルを読むため、ここで弾けば実際に不正な
    # パスを読むことはない）。
    pitch_dir_entries: Dict[str, "OrderedDict[str, List[dbu.OtoEntry]]"] = {}
    wav_ref_violations: List[str] = []
    malformed_oto_lines: List[str] = []
    oto_entry_count_violations: List[str] = []
    for pdir_name in pitch_dirs:
        pdir = voicebank_root / pdir_name
        oto_path = pdir / "oto.ini"
        # dbu.parse_oto_ini(path) と等価に decode + parse するが、テキストを
        # 一度だけ read してここでの malformed 行検査と共有する（二重 read
        # を避ける）。
        oto_text = dbu.decode_oto_bytes(oto_path.read_bytes())
        malformed_oto_lines.extend(
            f"{pdir_name}/oto.ini:{lineno}: {content}"
            for lineno, content in _find_malformed_oto_lines(oto_text)
        )
        oto_entries = dbu.parse_oto_text(oto_text)
        # review #263 R14 P2: 有効な行が丸ごと削除された場合（malformed 行
        # として検出されない）を捕捉するため、パース後の実エントリ総数を
        # 期待契約 `expected_oto_entries` と照合する。契約が存在しない
        # pitch dir（override で対象外にした場合）はチェックしない。
        expected_n = expected_oto_entries.get(pdir_name)
        if expected_n is not None and len(oto_entries) != expected_n:
            oto_entry_count_violations.append(
                f"{pdir_name}: parsed {len(oto_entries)} entries, expected {expected_n}"
            )
        entries_by_wav: "OrderedDict[str, List[dbu.OtoEntry]]" = OrderedDict()
        for e in oto_entries:
            entries_by_wav.setdefault(e.wav_filename, []).append(e)
        pitch_dir_entries[pdir_name] = entries_by_wav
        wav_ref_violations.extend(
            _reject_wav_paths_outside_root(
                pdir, pdir_name, list(entries_by_wav.keys()), voicebank_root
            )
        )

    if malformed_oto_lines:
        raise MalformedOtoEntryError(
            f"{len(malformed_oto_lines)} malformed oto.ini row(s) detected "
            "(dbu.parse_oto_text would silently skip these — fail-closed "
            "before any read/write, corpus not published): "
            + "; ".join(sorted(malformed_oto_lines))
        )

    if oto_entry_count_violations:
        raise UnexpectedOtoEntryCountError(
            f"{len(oto_entry_count_violations)} pitch dir(s) have unexpected oto "
            "entry count(s) (row deletion or truncated '=' loss would silently "
            "reduce the corpus — fail-closed before any read/write, corpus not "
            "published): " + "; ".join(sorted(oto_entry_count_violations))
        )

    if wav_ref_violations:
        raise WavPathEscapeError(
            "oto.ini references WAV file(s) that resolve outside the voicebank "
            f"root {voicebank_root.resolve()} (rejected as path traversal / "
            "symlink escape, fail-closed before any read — corpus not "
            f"published): {sorted(wav_ref_violations)}"
        )

    rows: List[dict] = []
    missing_wavs: List[str] = []
    global_stats = dict(
        n_wav_total=0, n_entries_total=0, n_unmapped_total=0, n_sokuon_total=0,
        total_audio_s=0.0, total_sp_s=0.0, total_voiced_s=0.0,
        phoneme_symbol_counter=Counter(), mora_counter=Counter(),
        per_dir=dict(),
    )

    for pdir_name in pitch_dirs:
        pdir = voicebank_root / pdir_name
        entries_by_wav = pitch_dir_entries[pdir_name]

        dir_stats = dict(n_wav=0, n_entries=0, n_unmapped=0, n_sokuon=0, audio_s=0.0)

        for idx, (wav_filename, entries) in enumerate(sorted(entries_by_wav.items()), start=1):
            wav_path = pdir / wav_filename
            if not wav_path.exists():
                # [P2 修正] 黙って skip すると oto が参照する音素区間ごと
                # コーパスから欠落し、縮小コーパスのまま成功報告してしまう
                # （review #263 R4）。全 pitch dir 走査後に一括で fail する
                # ため、ここでは欠落ファイル名を収集するのみに留める。
                missing_wavs.append(str((pdir / wav_filename).relative_to(voicebank_root)))
                continue
            info = sf.info(str(wav_path))
            wav_duration_ms = info.frames / info.samplerate * 1000.0

            ph_seq, ph_dur, stats = build_segment_for_wav(
                entries, pitch_dirs, mora_dict, wav_duration_ms
            )
            if not ph_seq:
                continue

            seg_name = f"ritsu_{pdir_name}_{idx:03d}"
            data, sr = sf.read(str(wav_path))
            sf.write(str(out_wavs / f"{seg_name}.wav"), data, sr, subtype="PCM_16")

            rows.append(dict(
                name=seg_name,
                ph_seq=" ".join(ph_seq),
                ph_dur=" ".join(f"{d:.7g}" for d in ph_dur),
                source_wav=wav_filename,
                pitch_dir=pdir_name,
            ))

            dir_stats["n_wav"] += 1
            dir_stats["n_entries"] += stats["n_entries"]
            dir_stats["n_unmapped"] += stats["n_unmapped"]
            dir_stats["n_sokuon"] += stats["n_sokuon"]
            dir_stats["audio_s"] += wav_duration_ms / 1000.0

            global_stats["phoneme_symbol_counter"].update(ph_seq)
            global_stats["mora_counter"].update(stats["used_moras"])
            global_stats["total_sp_s"] += sum(d for p, d in zip(ph_seq, ph_dur) if p == "SP")
            global_stats["total_voiced_s"] += sum(d for p, d in zip(ph_seq, ph_dur) if p != "SP")

        global_stats["per_dir"][pdir_name] = dir_stats
        global_stats["n_wav_total"] += dir_stats["n_wav"]
        global_stats["n_entries_total"] += dir_stats["n_entries"]
        global_stats["n_unmapped_total"] += dir_stats["n_unmapped"]
        global_stats["n_sokuon_total"] += dir_stats["n_sokuon"]
        global_stats["total_audio_s"] += dir_stats["audio_s"]

    if missing_wavs:
        # [P2 修正] (review #263 R4) 欠落 WAV があれば、書き込み開始前に
        # 全欠落ファイル名を報告して fail-closed で拒否する（縮小コーパスの
        # 成功報告を禁止。完全な抽出では missing_wavs は空のまま挙動不変）。
        raise MissingSourceWavError(
            f"{len(missing_wavs)} oto-referenced wav file(s) missing under "
            f"{voicebank_root} (fail-closed, corpus not published): "
            + ", ".join(sorted(missing_wavs))
        )

    with open(out_dir / "transcriptions.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "ph_seq", "ph_dur"])
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in ("name", "ph_seq", "ph_dur")})

    with open(out_dir / "provenance.json", "w", encoding="utf-8") as f:
        json.dump(
            [
                {"name": r["name"], "source_wav": r["source_wav"], "pitch_dir": r["pitch_dir"]}
                for r in rows
            ],
            f, ensure_ascii=False, indent=1,
        )

    summary = dict(
        n_wav_total=global_stats["n_wav_total"],
        n_entries_total=global_stats["n_entries_total"],
        n_unmapped_total=global_stats["n_unmapped_total"],
        n_sokuon_total=global_stats["n_sokuon_total"],
        total_audio_s=round(global_stats["total_audio_s"], 3),
        total_sp_s=round(global_stats["total_sp_s"], 3),
        total_voiced_s=round(global_stats["total_voiced_s"], 3),
        effective_minutes=round(global_stats["total_voiced_s"] / 60.0, 3),
        n_segments=len(rows),
        per_dir=global_stats["per_dir"],
        phoneme_symbol_counts=dict(global_stats["phoneme_symbol_counter"]),
        n_unique_moras_used=len(global_stats["mora_counter"]),
        mora_counts=dict(global_stats["mora_counter"]),
    )
    with open(out_dir / "d2_stats.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    return summary


class OutputCollisionError(ValueError):
    """P1 修正 (review #263 R5): `--out-dir` が `--voicebank-root`/`--dsdict`
    と衝突する場合に送出する（fail-closed。`_swap_into_place` による
    rename/`.old` 退避・rmtree の前に検出する。`build_dataset.py` R4 の
    `OutputCollisionError` と同型判定（record スクリプト群の既存慣例に倣い、
    共有モジュール新設ではなく各ファイル内へコピペ実装。`f1_4_record_2026-08-15.md`
    §1 の Design Memo 判断を踏襲）。"""


def _reject_output_collision(out_paths: Sequence[Path], protected_roots: Sequence[Path]) -> None:
    """`out_paths`（resolve 後）を相互および `protected_roots`（存在する
    もののみ、resolve 後）と照合し、衝突があれば公開前に fail-closed で
    拒否する。

    `--out-dir` が `--voicebank-root`/`--dsdict` と一致・内包関係にある
    場合、`_swap_into_place` の旧 `out_dir` を `.old` へ退避する処理や
    次回実行時の `.old` rmtree が、波音リツ voicebank 本体や公式 dsdict.yaml
    そのものを破壊し得る（`build_dataset.py` `_reject_output_collision` と
    同一の resolved 比較ロジック）。

    review #263 R7 P1（`convert_pjs.py` の同型ガードと同一のファミリー
    修正）: 包含判定は双方向で行う。出力が保護 root 配下にある場合
    （従来判定）に加え、**保護 root が出力配下にある場合**（例:
    `--out-dir=/tmp/work`, `--voicebank-root=/tmp/work/voicebank`）も拒否
    する。後者を見落とすと、公開時に保護 root ごと `.old` へ退避されて
    しまい voicebank 本体や dsdict.yaml が破壊される。
    """
    resolved_outs = [(p, p.resolve()) for p in out_paths]

    for i, (p_i, r_i) in enumerate(resolved_outs):
        for p_j, r_j in resolved_outs[i + 1 :]:
            if r_i == r_j:
                raise OutputCollisionError(
                    f"output paths collide with each other: {p_i} == {p_j}（fail-closed で拒否）"
                )

    for root in protected_roots:
        if not root.exists():
            continue
        root_resolved = root.resolve()
        for p, r in resolved_outs:
            if r == root_resolved:
                raise OutputCollisionError(
                    f"output path {p} collides with protected input root {root}（fail-closed で拒否）"
                )
            try:
                r.relative_to(root_resolved)
            except ValueError:
                pass
            else:
                raise OutputCollisionError(
                    f"output path {p} is inside protected input root {root}（fail-closed で拒否）"
                )
            try:
                root_resolved.relative_to(r)
            except ValueError:
                continue
            raise OutputCollisionError(
                f"protected input root {root} is inside output path {p}"
                f"（fail-closed で拒否。出力側の公開処理が保護 root を巻き込む）"
            )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--voicebank-root", type=Path, required=True,
        help="展開済み '波音リツ強連続音Ver1.5.1/' のパス (A3/ F4/ の親ディレクトリ)",
    )
    parser.add_argument(
        "--dsdict", type=Path, required=True,
        help="リツ公式 DiffSinger 配布 zip 内の dsdur/dsdict.yaml のパス (617 語彙)",
    )
    parser.add_argument(
        "--out-dir", type=Path, required=True,
        help="出力先 (transcriptions.csv / wavs/ / provenance.json / d2_stats.json を書く)",
    )
    parser.add_argument(
        "--pitch-dirs", nargs="+", default=list(DEFAULT_PITCH_DIRS),
        help=f"走査する pitch dir 名 (既定: {list(DEFAULT_PITCH_DIRS)})",
    )
    parser.add_argument(
        "--expected-oto-entries", default=None,
        help="pitch dir ごとの期待 oto エントリ総数契約 (review #263 R14)。"
             f"既定は素材 pin 済み voicebank（強連続音 Ver1.5.1）の実測値 "
             f"{EXPECTED_OTO_ENTRIES}。'A3=1237,F4=1237' 形式で override、"
             "'none' で無効化できる（別 voicebank を意図的に使う場合）。",
    )
    args = parser.parse_args(argv)

    if args.expected_oto_entries is None:
        expected_oto_entries = dict(EXPECTED_OTO_ENTRIES)
    else:
        try:
            expected_oto_entries = _parse_expected_oto_entries_arg(args.expected_oto_entries)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    # [P1 修正] (review #263 R5) --out-dir が --voicebank-root/--dsdict と
    # 重なる場合、staging 構築や swap を始める前（公開開始前）に fail-closed
    # で拒否する。--out-dir は成功時に丸ごと rename/`.old` 退避/rmtree
    # されるため、保護入力と重なっていると voicebank 本体や dsdict.yaml
    # そのものを破壊し得る。
    #
    # [P1 修正] (review #263 R9) 上記は --out-dir 本体のみを検査していたが、
    # `_swap_into_place` が実際に削除・rename するのは `<out-dir>.old`
    # （毎回 rmtree）と `<out-dir>.staging-<pid>`（成功時に --out-dir へ
    # rename）という派生パスであり、これらはチェック対象外だった。派生
    # パスを算出してガード対象に加える（staging_dir は `convert()` 内部と
    # 同じ pid 由来の同一パスを再算出するのみで、実際の構築先はここでは
    # 作らない＝重複作成なし）。
    out_dir: Path = args.out_dir
    old_dir = out_dir.parent / f"{out_dir.name}.old"
    staging_dir = out_dir.parent / f"{out_dir.name}.staging-{os.getpid()}"
    try:
        _reject_output_collision(
            [out_dir, old_dir, staging_dir], protected_roots=[args.voicebank_root, args.dsdict]
        )
    except OutputCollisionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    summary = convert(
        args.voicebank_root, args.dsdict, args.out_dir, args.pitch_dirs, expected_oto_entries
    )

    print(f"n_segments={summary['n_segments']}")
    print(f"n_wav_total={summary['n_wav_total']}")
    print(f"n_entries_total={summary['n_entries_total']}")
    print(f"n_unmapped_total={summary['n_unmapped_total']}")
    print(f"n_sokuon_total={summary['n_sokuon_total']}")
    print(f"total_audio_s={summary['total_audio_s']:.2f}")
    print(
        f"total_voiced_s={summary['total_voiced_s']:.2f} "
        f"({summary['effective_minutes']:.2f} min)"
    )
    print(f"total_sp_s={summary['total_sp_s']:.2f}")
    print(f"n_unique_phoneme_symbols={len(summary['phoneme_symbol_counts'])}")
    print(f"n_unique_moras_used={summary['n_unique_moras_used']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
