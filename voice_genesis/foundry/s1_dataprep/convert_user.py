"""S3 Phase C: 第三ドナー（User）音源 -> DiffSinger acoustic 学習形式変換器。

`DESIGN_S3_backfill.md` §3 / §3.1「tier 別アラインメント戦略」の実装。入力は
`recording_kit/intake.py` が生成する正規化 wav 17 本（24kHz mono s16、
`--normalized-dir`）と台帳 `user_donor_ledger.json`（`--ledger`）。出力は
`convert_d3.py` と同一契約の `transcriptions.csv`（name,ph_seq,ph_dur,ph_num,
note_seq,note_dur）+ `wavs/`（44.1kHz mono s16、ffmpeg 決定論変換）。

## tier 別アラインメント（§3.1 のとおり）

- **T0**（UC-001 さくら / UC-002 うみ、通し歌唱）: `singer/score.py`
  `build_sakura_score()`（6 フレーズ）/ `singer/score_umi.py`
  `build_umi_score()`（3 フレーズ）が持つ **かな・onset/vowel・拍配分比**
  （カード文言そのものではない）を正とする。RMS ゲートで検出した音声側の
  フレーズ区間（無音ギャップ >= 0.3s、`batch1_inspection.md` の手法と同型）を
  スコアのフレーズ数へ位置対応させ、フレーズ内はスコアの `duration_beats` 比で
  実時間へ配分する。区間数がスコアの +1（`batch1_inspection.md` で UC-002 が
  4 区間・スコア 3 フレーズだった既知差）の場合のみ、ギャップ最小の隣接ペアを
  結合して吸収する。それ以外の不一致は fail-closed でそのカードを除外する。
- **T1**（UC-003〜007、母音のばし）: 20ms フレーム RMS ゲート（`batch2_t1_inspection.md`
  と同型・閾値 -40dBFS）で 3 段の持続母音区間を検出する（1 秒未満の短区間は
  グリッチとして除外）。3 段ちょうどでなければ fail-closed でそのカードを除外する
  （黙って 2 段で通さない）。各段 = 単一母音音素（カード対応は `cards.md` T1 表で
  固定）。note は段内 f0 中央値（`librosa.pyin`）の最近傍整数 MIDI。
- **T2**（UC-008〜017、短句）: **[C2 改訂]** `cards.md` の歌詞（引用符内の文言
  のみ。UC-017 の「（息つぎ）」注記はフレーズ区切りとして解釈し除去）を
  `--dsdict`（正本 = リツ公式 DiffSinger 配布 zip の `dsdur/dsdict.yaml`、
  617 グラフェムエントリ）へのグラフェム→音素 lookup で音素列へ確定する
  （下記「dsdict.yaml によるグラフェム音素化（C2）」節参照）。フレーズ数と
  一致する有声ラン（無音ギャップ >= 0.1s、`batch3_t2_inspection.md` の手法と
  同型。**ラン数 > フレーズ数の場合のみ、最小ギャップの隣接ペア併合を数が
  合うまで決定論的に繰り返して吸収する**。ラン数 < フレーズ数は従来どおり
  fail-closed）が検出できた場合のみラン内をグラフェム重み比例 + 子音定率
  （`CONSONANT_FRACTION`）で配分する。note はラン内 f0 中央値 MIDI（フレーズ
  単位）。

## dsdict.yaml によるグラフェム音素化（C2、`DESIGN_S3_backfill.md` §3.1 last
   bullet / §7 Q5 の後継）

C1（初版）は `s1_dataprep` の統合辞書の実体（`dsdict.yaml`）が本実装環境に
存在せず、促音記号の実在を確認できなかったため `phoneme_jp.py`（ひらがな限定
の最小サブセット）を使い、促音・カタカナ外来語音・濁音行ば/だ/ざ・半濁音ぱ行
を未対応として T2 の大半を除外した。C2 では正本 `dsdict.yaml`（リツ公式
DiffSinger 配布 zip、`S1_GPU_RUNBOOK.md` 素材 3 の pin `5c7b8c328180ea29…`
と一致確認済み・617 エントリ）を一次ソース確認した上で採用する:

- **促音「っ」**: `dsdict.yaml` に `grapheme: っ -> phonemes: [cl]` が実在する
  （一次ソース確認済み）。**`cl` を emit する**（C1 の「促音記号を一切 emit
  しない」方針を撤回）。durationは「短い子音相当」の配分として通常グラフェム
  より小さい重み（`SOKUON_WEIGHT = 0.5`）を与える。
- **濁音行 ば/だ/ざ・半濁音ぱ行**: `dsdict.yaml` に全行が実在する（b/d/z/p +
  母音）。通常のグラフェムとして 2 音素（onset+vowel）で扱う。
- **カタカナ外来語音**（ティ/ディ/ファ/フィ/フェ/フォ等）: `dsdict.yaml` に
  カタカナ表記・ローマ字表記の両方が実在する（例: `ティ -> [t, I]`）。**最長
  一致トークン化**（2 文字グラフェムを 1 文字グラフェムより優先）で正しく
  1 トークンとして拾う（`phoneme_jp.py` の 1 文字ずつの逐次判定では「ティ」の
  先頭「テ」だけで既に未対応判定になっていた — C1 からの改善点）。母音は
  大文字（`I`/`A`/`U`/`E`/`O`、`dsdict.yaml` `symbols:` で `type: stop` に
  分類される外来語専用の register）を用いる（辞書のとおり、ひらがな小文字の
  母音と混同しない）。
- **カタカナ促音「ッ」/ カタカナ撥音「ン」**: `dsdict.yaml` にはひらがな
  「っ」「ん」の grapheme エントリのみが存在し、カタカナ単独形「ッ」「ン」は
  実在しない（一次ソース確認済み、`grep` で該当なし）。これは "別の音への
  代用" ではなく、同一の音素（`cl` / `N`）を指す**表記ゆれ**（外来語をカタカナ
  で書く際の促音・撥音は日本語表記としてひらがな「っ」「ん」と完全に同一の
  発音であり、辞書側がひらがな形のみを収録しているだけ）と判断し、
  `_SOKUON_NASAL_KATAKANA_TO_HIRAGANA`（`ッ`→`っ`, `ン`→`ん`）でトークン化前に
  正規化する。UC-008「カップ」・UC-009「ファンファーレ」がこれに該当し、
  正規化なしでは辞書引きに失敗する（実測確認済み）。
- **長音記号「ー」**: `dsdict.yaml` に grapheme エントリが無い（一次ソース
  確認: `grep` で該当なし）。`phoneme_jp.py` と同じ意味論（直前トークンの
  母音を延長するマーカー、独自の音素は追加しない）で扱う。durationは直前
  トークンへ加算する重み（`CHOON_EXTRA_WEIGHT = 1.0`）として実装する。
- **ヴ系グラフェム（ヴァ/ヴィ/ヴェ/ヴ 等）**: `dsdict.yaml` に一切実在しない
  （一次ソース確認済み、`grep -n "ヴ"` で 0 件）。**代用マッピングは禁止**
  （例: ASR 誤認識の「パ/ピ/ペ」等へ寄せない）。UC-010 は除外を継続し、理由を
  「ヴ系グラフェムが正本辞書に非対応」へ更新する。

対象カード再評価（cards.md の実文言を dsdict でトークン化した結果、一次ソース
突合済み）: T2 復旧対象 = UC-008/009/011/012/013/014/015/016/017 の 9 枚
（すべて dsdict で全文字マッピング可能）。UC-010 のみ除外継続（ヴ非対応）。

## 家風の踏襲

`convert_d3.py`（B3・同一出力契約）と同型:

- 全収集してから公開前に fail-closed（台帳突合の欠落/余剰/sha256 不一致は
  全件収集してから一括で拒否する）
- `<out-dir>.staging-<pid>` に完全構築してから `<out-dir>` へ原子的に swap
  （`convert_d3._swap_into_place` をそのまま read-only import して再利用）
- `--out-dir` が `--normalized-dir`/`--ledger` 親ディレクトリと衝突・内包関係に
  ある場合は staging 構築前に fail-closed で拒否する
  （`convert_d3._reject_output_collision` を read-only import して再利用）
- rename/move の成否を Python 変数で記帳しない（`convert_d3._swap_into_place`
  の `old_dir.exists()` 観測パターンをそのまま踏襲）
- 決定論（同一入力 2 回実行でバイト一致。RMS ゲート・pyin・ffmpeg いずれも
  乱数を使わない）

## 子音/母音比の参考値

`adapter/consonants.py` `MAX_CONSONANT_FRACTION = 0.45`（子音部分がノート長に
対して超えてはならない上限比率、singer/render_song.py の耳検証済みレシピに
由来）を上限の参考として、それより保守的な `CONSONANT_FRACTION = 0.3` を
本変換器の固定値として採用する（convert_d3 はタイミングを自前計算しない
ため、"convert_d3 の timing 実測" は直接には存在しない。代わりに同じ
`singer/` 系譜のこの定数を参考値として使う）。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import soundfile as sf
import librosa
import yaml

# --- sibling import: convert_d3.py（同ディレクトリ。resample/atomic-swap/
# collision-guard を read-only import で再利用する。触れない・複製しない）。
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
import convert_d3  # noqa: E402

# --- sibling import: voice_genesis/singer（既存 sibling sys.path 方式。
# `results_f1c/make_note_tracks.py` 等と同一パターン）。
_SINGER_DIR = Path(__file__).resolve().parents[2] / "singer"
if str(_SINGER_DIR) not in sys.path:
    sys.path.insert(0, str(_SINGER_DIR))
import phoneme_jp as pj  # noqa: E402
from score import build_sakura_score  # noqa: E402
from score_umi import build_umi_score  # noqa: E402

TARGET_SAMPLE_RATE = 44100

# `DESIGN_S3_backfill.md` §3 item4: ph_dur 合計と実 wav 長の許容乖離（50ms 級）。
DEFAULT_DURATION_TOLERANCE_SEC = 0.05

# RMS ゲート共通パラメータ（20ms フレーム、batch2/batch3 検査と同型）。
FRAME_SEC = 0.02
SILENCE_DB = -40.0

# T1: 3 段検出（`batch2_t1_inspection.md` §3 と同型: 1 秒未満の短区間を除外）。
T1_MIN_RUN_SEC = 1.0
T1_VOWEL_BY_CARD: Dict[str, str] = {
    "UC-003": "a", "UC-004": "i", "UC-005": "u", "UC-006": "e", "UC-007": "o",
}

# T0: フレーズ境界の無音ギャップ閾値（`batch1_inspection.md` §1 と同型）。
T0_MIN_GAP_SEC = 0.3

# T2: フレーズ境界の内部ギャップ閾値（`batch3_t2_inspection.md` §1 と同型）。
T2_MIN_GAP_SEC = 0.1
# T2 のみに適用する短区間除外（batch3 手法自体には明記が無い安全マージン。
# グリッチ的な極短ラン誤検出を防ぐ）。
T2_MIN_RUN_SEC = 0.05

# 子音/母音比（モジュール docstring「子音/母音比の参考値」節参照）。
CONSONANT_FRACTION = 0.3

# --- dsdict.yaml トークン化（T2 専用、C2）: 定数は「dsdict.yaml による
# グラフェム音素化（C2）」節参照。 ------------------------------------------

# 促音「っ」= cl の duration 重み（"短い子音相当の配分"。通常グラフェムの
# 重み 1.0 より小さい固定値。dsdict 上は cl も他グラフェムと同格の 1 エントリ
# だが、実際の音価は他モーラより短いため duration 配分でのみ差を付ける）。
SOKUON_WEIGHT = 0.5

# 長音記号「ー」が直前トークンへ加算する追加 duration 重み（1.0 = 通常
# モーラ 1 個分の伸長。母音を「もう1モーラ分」保持するという近似）。
CHOON_EXTRA_WEIGHT = 1.0

_CHOON_MARK = "ー"

# カタカナ促音/撥音 -> ひらがな正規化（モジュール docstring 節参照。
# "別の音への代用" ではなく同一音素の表記ゆれの正規化）。
_SOKUON_NASAL_KATAKANA_TO_HIRAGANA: Dict[str, str] = {"ッ": "っ", "ン": "ん"}

# `cards.md` T2 節「そのまま歌う文句」列から引用符内の文言のみを抽出し、
# 空白区切りでフレーズ化したもの（UC-017 のみ「（息つぎ）」注記をフレーズ
# 区切りとして解釈し除去）。cards.md 本文が一次ソース、本表はその写し。
T2_PHRASES: Dict[str, List[str]] = {
    "UC-008": ["ティーカップ", "かたてに", "ディナーの", "メロディー"],
    "UC-009": ["ファンファーレ", "フィナーレ", "フェスタで", "フォルテ"],
    "UC-010": ["ヴァイオリン", "ヴィオラの", "ヴェールの", "ひびき"],
    "UC-011": ["きっと", "ずっと", "まって", "いつか", "きっと"],
    "UC-012": ["みわたすかぎり", "ひかりかがやく"],
    "UC-013": ["きゃくせん", "ぎゃくふう", "きょうも", "ゆく"],
    "UC-014": ["しゃぼんだま", "じゃんぷ", "ちゃいろの", "ちょうちょ"],
    "UC-015": ["ぱっと", "ぴかっと", "ぷかぷか", "ぽっかり"],
    "UC-016": ["ばらの", "はなびら", "だんだん", "ざわめく"],
    "UC-017": ["はるのかぜ", "そらをゆく", "とりのうた"],
}

CARD_TIER: Dict[str, str] = {
    "UC-001": "T0", "UC-002": "T0",
    "UC-003": "T1", "UC-004": "T1", "UC-005": "T1", "UC-006": "T1", "UC-007": "T1",
    "UC-008": "T2", "UC-009": "T2", "UC-010": "T2", "UC-011": "T2", "UC-012": "T2",
    "UC-013": "T2", "UC-014": "T2", "UC-015": "T2", "UC-016": "T2", "UC-017": "T2",
}


class LedgerMismatchError(ValueError):
    """台帳 (`--ledger`) と `--normalized-dir` の突合で欠落・余剰・sha256 不一致が
    見つかった場合に送出する（全違反を収集してから fail-closed、`convert_d3.py`
    の「全収集してから公開前に止める」設計と同型）。"""


class AllCardsExcludedError(ValueError):
    """17 枚全カードがアラインメント不能で除外され、公開できる行が 1 件も
    残らなかった場合に送出する（黙殺せず fail-closed で止める）。"""


# ---------------------------------------------------------------------------
# 1. 台帳突合（欠落・余剰・sha256 不一致を全収集してから fail-closed）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerEntry:
    card_id: str
    filename: str
    sha256: str
    path: Path


def load_ledger(ledger_path: Path) -> List[Dict[str, object]]:
    with open(ledger_path, encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise LedgerMismatchError(
            f"{ledger_path}: 'entries' list not found or malformed (fail-closed)"
        )
    return entries


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def reconcile_ledger(
    normalized_dir: Path, ledger_entries: Sequence[Dict[str, object]]
) -> Dict[str, LedgerEntry]:
    """台帳の `normalized_path` のファイル名 + `sha256` を正として
    `normalized_dir` と突合する。欠落・sha256 不一致・（台帳に無い）余剰 wav の
    いずれも全件収集してから `LedgerMismatchError` で fail-closed する。"""
    problems: List[str] = []
    by_card: Dict[str, LedgerEntry] = {}
    referenced_filenames: set = set()

    for entry in ledger_entries:
        card_id = entry.get("card_id")
        norm_path = entry.get("normalized_path")
        expected_sha = entry.get("sha256")
        if not card_id or not norm_path or not expected_sha:
            problems.append(
                "malformed ledger entry (missing card_id/normalized_path/sha256): "
                f"{entry!r}"
            )
            continue
        filename = Path(str(norm_path)).name
        referenced_filenames.add(filename)
        candidate = normalized_dir / filename
        if not candidate.exists():
            problems.append(f"{card_id}: expected file missing under {normalized_dir}: {filename}")
            continue
        actual_sha = _sha256_of(candidate)
        if actual_sha != expected_sha:
            problems.append(
                f"{card_id}: sha256 mismatch for {filename} "
                f"(ledger={expected_sha}, actual={actual_sha})"
            )
            continue
        by_card[str(card_id)] = LedgerEntry(
            card_id=str(card_id), filename=filename, sha256=str(expected_sha), path=candidate
        )

    actual_wavs = {p.name for p in normalized_dir.glob("*.wav")}
    extras = sorted(actual_wavs - referenced_filenames)
    if extras:
        problems.append(
            f"{len(extras)} extra wav(s) in {normalized_dir} not referenced by ledger: {extras}"
        )

    if problems:
        raise LedgerMismatchError(
            f"ledger/normalized-dir reconciliation failed ({len(problems)} problem(s), "
            "fail-closed, nothing published): " + " | ".join(problems)
        )
    return by_card


# ---------------------------------------------------------------------------
# 1.5 dsdict.yaml ロード + グラフェム最長一致トークン化（T2 専用、C2）
# ---------------------------------------------------------------------------


class DsDictError(ValueError):
    """`--dsdict` の読み込み・構文が不正な場合に送出する（fail-closed。
    T2 全カード除外の原因になり得るため CLI/`convert()` レベルで扱う）。"""


class DsDictTokenizeError(ValueError):
    """フレーズ中に `dsdict.yaml` のどのグラフェムにも一致しない文字が
    見つかった場合に送出する（カード単位の除外理由として使う。ヴ系等、
    辞書に実在しない音は代用マッピングしない — モジュール docstring
    「dsdict.yaml によるグラフェム音素化（C2）」節参照）。"""


@dataclass(frozen=True)
class DsDict:
    path: Path
    sha256: str
    table: Dict[str, List[str]]  # grapheme -> phonemes（辞書内で最初に現れた対応を正とする）
    max_grapheme_len: int


def load_dsdict(path: Path) -> DsDict:
    """`dsdict.yaml`（`entries: [{grapheme, phonemes}, ...]`）を読み込み、
    グラフェム -> 音素列の lookup テーブルを構築する。ファイル全体の sha256 も
    併せて計算する（`--dsdict` の provenance として summary/exclusions.json へ
    記帳するため）。"""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DsDictError(f"{path}: cannot read dsdict file ({exc})") from None
    sha = hashlib.sha256(raw).hexdigest()
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise DsDictError(f"{path}: not valid YAML ({exc})") from None
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise DsDictError(f"{path}: missing top-level 'entries' list (fail-closed)")

    table: Dict[str, List[str]] = {}
    for entry in data["entries"]:
        if not isinstance(entry, dict):
            continue
        grapheme = entry.get("grapheme")
        phonemes = entry.get("phonemes")
        if not grapheme or not isinstance(phonemes, list) or not phonemes:
            continue
        # 辞書内の重複 grapheme（ローマ字/かな 2 表記が同じ音素列を指す等）は
        # 先勝ちで固定する（決定論。実測: `dsdict.yaml` に "cha"/"ja" 等の
        # 重複あり、いずれも同一音素列を指すため実害なし）。
        table.setdefault(str(grapheme), [str(p) for p in phonemes])

    if not table:
        raise DsDictError(f"{path}: no usable grapheme entries found (fail-closed)")

    max_len = max(len(g) for g in table)
    return DsDict(path=path, sha256=sha, table=table, max_grapheme_len=max_len)


def _normalize_sokuon_nasal(text: str) -> str:
    """カタカナ促音「ッ」/ 撥音「ン」をひらがな「っ」/「ん」へ正規化する
    （`_SOKUON_NASAL_KATAKANA_TO_HIRAGANA` docstring 参照。同一音素の表記ゆれの
    正規化であり、辞書に無い別の音への代用ではない）。"""
    return "".join(_SOKUON_NASAL_KATAKANA_TO_HIRAGANA.get(ch, ch) for ch in text)


def tokenize_with_dsdict(dsdict: DsDict, phrase: str) -> List[Tuple[str, List[str]]]:
    """`phrase`（カタカナ促音/撥音の正規化後）を `dsdict` のグラフェムで
    最長一致トークン化する。長音記号「ー」は独立トークン `("ー", [])`
    （音素を追加しないマーカー）として返す。戻り値は `(grapheme, phonemes)`
    の列。どのグラフェムにも一致しない文字が見つかった場合は
    `DsDictTokenizeError` を送出する（代用マッピングはしない）。"""
    text = _normalize_sokuon_nasal(phrase)
    n = len(text)
    out: List[Tuple[str, List[str]]] = []
    i = 0
    while i < n:
        if text[i] == _CHOON_MARK:
            out.append((_CHOON_MARK, []))
            i += 1
            continue
        matched = False
        max_try = min(dsdict.max_grapheme_len, n - i)
        for length in range(max_try, 0, -1):
            candidate = text[i:i + length]
            phonemes = dsdict.table.get(candidate)
            if phonemes is not None:
                out.append((candidate, phonemes))
                i += length
                matched = True
                break
        if not matched:
            raise DsDictTokenizeError(
                f"unmapped grapheme in dsdict lookup: {text[i]!r} (phrase={phrase!r})"
            )
    return out


def weighted_tokens_for_phrase(dsdict: DsDict, phrase: str) -> List[Tuple[List[str], float]]:
    """`phrase` を dsdict でトークン化し、`(phonemes, duration_weight)` の列を
    返す。長音「ー」は独立ノートを持たず、直前トークンの重みへ
    `CHOON_EXTRA_WEIGHT` を加算する。促音「っ」(=`["cl"]`) は
    `SOKUON_WEIGHT`、それ以外は通常重み 1.0（モーラ数比例）。"""
    raw = tokenize_with_dsdict(dsdict, phrase)
    weighted: List[List[object]] = []  # [phonemes, weight] (mutable for in-place +=)
    for grapheme, phonemes in raw:
        if grapheme == _CHOON_MARK:
            if not weighted:
                raise DsDictTokenizeError(
                    f"phrase starts with a long-vowel mark, no preceding token to extend: "
                    f"{phrase!r}"
                )
            weighted[-1][1] = weighted[-1][1] + CHOON_EXTRA_WEIGHT
            continue
        weight = SOKUON_WEIGHT if phonemes == ["cl"] else 1.0
        weighted.append([list(phonemes), weight])
    return [(phonemes, weight) for phonemes, weight in weighted]


# ---------------------------------------------------------------------------
# 2. 音声解析プリミティブ（RMS ゲート・f0 中央値。乱数不使用、決定論）
# ---------------------------------------------------------------------------


def _load_mono(path: Path) -> Tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), dtype="float64", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return np.asarray(data, dtype=np.float64), int(sr)


def _frame_rms_db(samples: np.ndarray, sr: int, frame_sec: float = FRAME_SEC) -> np.ndarray:
    frame_len = max(1, int(round(sr * frame_sec)))
    n_frames = len(samples) // frame_len
    if n_frames == 0:
        return np.array([], dtype=np.float64)
    trimmed = samples[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt(np.mean(trimmed * trimmed, axis=1))
    with np.errstate(divide="ignore"):
        db = 20.0 * np.log10(np.maximum(rms, 1e-12))
    return db


def _voiced_runs(
    samples: np.ndarray, sr: int, *,
    silence_db: float = SILENCE_DB, frame_sec: float = FRAME_SEC, min_run_sec: float = 0.0,
) -> List[Tuple[float, float]]:
    """20ms フレーム RMS ゲートで有声区間 `(start_sec, end_sec)` の列を返す
    （`min_run_sec` 未満の短区間は除外。`batch2_t1_inspection.md` §3 と同型）。"""
    db = _frame_rms_db(samples, sr, frame_sec)
    if db.size == 0:
        return []
    voiced = db > silence_db
    frame_len = max(1, int(round(sr * frame_sec)))
    runs: List[Tuple[int, int]] = []
    start: Optional[int] = None
    for i, v in enumerate(voiced):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(voiced)))
    out: List[Tuple[float, float]] = []
    for s, e in runs:
        dur = (e - s) * frame_len / sr
        if dur >= min_run_sec:
            out.append((s * frame_len / sr, e * frame_len / sr))
    return out


def _segments_by_gap(
    samples: np.ndarray, sr: int, *,
    silence_db: float, frame_sec: float, min_gap_sec: float, min_run_sec: float = 0.0,
) -> List[Tuple[float, float]]:
    """有声ランを検出したのち、`min_gap_sec` 未満の無音は前後のランへ吸収して
    フレーズ区間を返す（`batch1_inspection.md`/`batch3_t2_inspection.md` の
    『Xs 以上のギャップだけを区切りとみなす』手法と同型）。"""
    runs = _voiced_runs(samples, sr, silence_db=silence_db, frame_sec=frame_sec, min_run_sec=0.0)
    if not runs:
        return []
    merged: List[List[float]] = [list(runs[0])]
    for s, e in runs[1:]:
        gap = s - merged[-1][1]
        if gap < min_gap_sec:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged if (e - s) >= min_run_sec]


def _f0_median_hz(samples: np.ndarray, sr: int, start_sec: float, end_sec: float) -> Optional[float]:
    seg = samples[int(round(start_sec * sr)): int(round(end_sec * sr))]
    if seg.size < int(sr * 0.05):
        return None
    f0, voiced_flag, _voiced_prob = librosa.pyin(
        seg, sr=sr, fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C6"),
    )
    if voiced_flag is not None:
        candidates = f0[voiced_flag.astype(bool)]
    else:
        candidates = f0
    candidates = candidates[~np.isnan(candidates)]
    if candidates.size == 0:
        return None
    return float(np.median(candidates))


def _hz_to_midi_round(hz: float) -> int:
    return int(round(69.0 + 12.0 * math.log2(hz / 440.0)))


# ---------------------------------------------------------------------------
# 3. モーラ -> 音素/長さ配分（`s1_gate/gate_synth.py` の [onset,vowel] 規約と
#    同型。参照: モジュール docstring「子音/母音比の参考値」節）
# ---------------------------------------------------------------------------


def _phonemes_for_mora(mora: "pj.Mora") -> List[str]:
    if mora.onset is not None:
        return [mora.onset, mora.vowel]
    return [mora.vowel]


def _durations_for_mora(mora: "pj.Mora", mora_dur: float) -> List[float]:
    if mora.onset is not None:
        cons_dur = mora_dur * CONSONANT_FRACTION
        return [cons_dur, mora_dur - cons_dur]
    return [mora_dur]


def _durations_for_phonemes(phonemes: Sequence[str], note_dur: float) -> List[float]:
    """dsdict トークン（T2, C2）用の汎用版: 2 音素（onset+vowel、促音 `cl`
    単体を含む 1 音素トークンとは別枠）は `_durations_for_mora` と同じ
    `CONSONANT_FRACTION` 分割、1 音素はそのまま全長を割り当てる。"""
    if len(phonemes) == 2:
        cons_dur = note_dur * CONSONANT_FRACTION
        return [cons_dur, note_dur - cons_dur]
    if len(phonemes) == 1:
        return [note_dur]
    raise ValueError(f"unexpected phoneme count in dsdict token: {phonemes!r}")


def _fmt_dur(value: float) -> str:
    """`convert_d3.py`/`convert_pjs.py` と同一の慣行（`str(round(d, 12))`）。"""
    return str(round(value, 12))


@dataclass
class _RowBuilder:
    ph_seq: List[str] = field(default_factory=list)
    ph_dur: List[float] = field(default_factory=list)
    ph_num: List[int] = field(default_factory=list)
    note_seq: List[str] = field(default_factory=list)
    note_dur: List[float] = field(default_factory=list)

    def add_note(self, phonemes: Sequence[str], durations: Sequence[float], midi: int, note_dur: float) -> None:
        assert len(phonemes) == len(durations)
        self.ph_seq.extend(phonemes)
        self.ph_dur.extend(durations)
        self.ph_num.append(len(phonemes))
        self.note_seq.append(str(midi))
        self.note_dur.append(note_dur)

    def add_rest(self, duration: float) -> None:
        # ゼロ/負長の rest は emit しない（`validate_speaker` の non-positive
        # ph_dur 拒否を誘発しないため）。合計には影響しない（0 寄与のため）。
        if duration <= 0.0:
            return
        self.ph_seq.append("SP")
        self.ph_dur.append(duration)
        self.ph_num.append(1)
        self.note_seq.append("rest")
        self.note_dur.append(duration)

    def to_row(self, name: str) -> Dict[str, str]:
        return {
            "name": name,
            "ph_seq": " ".join(self.ph_seq),
            "ph_dur": " ".join(_fmt_dur(d) for d in self.ph_dur),
            "ph_num": " ".join(str(n) for n in self.ph_num),
            "note_seq": " ".join(self.note_seq),
            "note_dur": " ".join(_fmt_dur(d) for d in self.note_dur),
        }


# ---------------------------------------------------------------------------
# 4. T0（通し歌唱: score.py / score_umi.py のフレーズ構造へ対応付け）
# ---------------------------------------------------------------------------


def _score_phrases(build_fn) -> List[List[object]]:
    notes = build_fn()
    phrases: Dict[int, List[object]] = {}
    for n in notes:
        phrases.setdefault(n.phrase_index, []).append(n)
    return [phrases[i] for i in sorted(phrases)]


def _reconcile_segment_count(
    segments: List[Tuple[float, float]], expected: int
) -> Tuple[Optional[List[Tuple[float, float]]], Optional[str]]:
    """検出区間数がスコアのフレーズ数と一致しない場合の吸収規則
    （`DESIGN_S3_backfill.md` §3.1: `B の +1 は隣接フレーズ結合/分割で吸収し、
    対応不能なら fail-closed`）。+1（過分割）のみ、ギャップ最小の隣接ペアを
    結合して吸収する。それ以外は対応不能として `None` + 理由文字列を返す。"""
    n = len(segments)
    if n == expected:
        return segments, None
    if n == expected + 1 and n >= 2:
        gaps = [(segments[i + 1][0] - segments[i][1], i) for i in range(n - 1)]
        _, i = min(gaps, key=lambda pair: pair[0])
        merged = segments[:i] + [(segments[i][0], segments[i + 1][1])] + segments[i + 2:]
        if len(merged) == expected:
            return merged, None
    return None, f"expected {expected} phrase segment(s) (score phrase count), got {n}"


def _process_t0_card(
    card_id: str, samples: np.ndarray, sr: int, build_fn
) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    phrases = _score_phrases(build_fn)
    segs = _segments_by_gap(
        samples, sr, silence_db=SILENCE_DB, frame_sec=FRAME_SEC,
        min_gap_sec=T0_MIN_GAP_SEC, min_run_sec=0.0,
    )
    segs, err = _reconcile_segment_count(segs, len(phrases))
    if err is not None:
        return None, f"{card_id}: {err} (fail-closed exclusion; only a +1-segment merge is attempted)"

    builder = _RowBuilder()
    total_dur = len(samples) / sr
    prev_end = 0.0
    for phrase_notes, (start, end) in zip(phrases, segs):  # type: ignore[assignment]
        builder.add_rest(start - prev_end)
        seg_dur = end - start
        total_beats = sum(n.duration_beats for n in phrase_notes)  # type: ignore[union-attr]
        if total_beats <= 0:
            return None, f"{card_id}: score phrase has non-positive total duration_beats (fail-closed)"
        for note in phrase_notes:  # type: ignore[union-attr]
            note_dur = seg_dur * (note.duration_beats / total_beats)
            if note_dur <= 0.0:
                return None, f"{card_id}: allocated non-positive note duration (fail-closed)"
            midi = int(round(note.midi))
            phonemes = _phonemes_for_mora(note.mora)
            durations = _durations_for_mora(note.mora, note_dur)
            builder.add_note(phonemes, durations, midi, note_dur)
        prev_end = end
    builder.add_rest(total_dur - prev_end)
    return builder.to_row(card_id), None


# ---------------------------------------------------------------------------
# 5. T1（母音のばし: RMS ゲートで 3 段検出）
# ---------------------------------------------------------------------------


def _process_t1_card(card_id: str, samples: np.ndarray, sr: int) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    vowel = T1_VOWEL_BY_CARD[card_id]
    runs = _voiced_runs(
        samples, sr, silence_db=SILENCE_DB, frame_sec=FRAME_SEC, min_run_sec=T1_MIN_RUN_SEC,
    )
    if len(runs) != 3:
        return None, (
            f"{card_id}: expected 3 sustained-vowel segments (20ms/{SILENCE_DB}dB RMS gate, "
            f">= {T1_MIN_RUN_SEC}s), found {len(runs)} (fail-closed exclusion; not silently "
            "accepted with fewer/more steps)"
        )

    builder = _RowBuilder()
    total_dur = len(samples) / sr
    prev_end = 0.0
    for start, end in runs:
        builder.add_rest(start - prev_end)
        f0 = _f0_median_hz(samples, sr, start, end)
        if f0 is None:
            return None, f"{card_id}: no voiced f0 found in segment ({start:.3f}-{end:.3f}s) (fail-closed)"
        midi = _hz_to_midi_round(f0)
        # T1 の 1 段 = 単一母音音素（onset なし）。`_phonemes_for_mora`/
        # `_durations_for_mora` の一般経路と等価だが、子音が存在しないため
        # 直接 1 音素 1 duration で追加する（`pj.Mora(onset=None, ...)` を
        # 経由しても結果は同一）。
        builder.add_note([vowel], [end - start], midi, end - start)
        prev_end = end
    builder.add_rest(total_dur - prev_end)
    return builder.to_row(card_id), None


# ---------------------------------------------------------------------------
# 6. T2（短句: dsdict.yaml グラフェム音素化 [C2] + 有声ラン境界のフレーズ割当）
# ---------------------------------------------------------------------------


def _phrase_tokens_dsdict(
    card_id: str, phrases: Sequence[str], dsdict: DsDict
) -> Tuple[Optional[List[List[Tuple[List[str], float]]]], Optional[str]]:
    result: List[List[Tuple[List[str], float]]] = []
    for phrase in phrases:
        try:
            result.append(weighted_tokens_for_phrase(dsdict, phrase))
        except DsDictTokenizeError as exc:
            return None, f"{card_id}: unmapped grapheme in dsdict for phrase {phrase!r}: {exc}"
    return result, None


def _reconcile_t2_segment_count(
    segments: List[Tuple[float, float]], expected: int
) -> Tuple[Optional[List[Tuple[float, float]]], Optional[str]]:
    """T2 専用の吸収規則（C2、`DESIGN_S3_backfill.md` §3.1 と別枠。T0 の
    `_reconcile_segment_count`（+1 のみ・1 回きり）とは独立の関数 — T0/T1 の
    出力バイト不変を保証するため、既存関数は一切変更しない）。ラン数 >
    期待フレーズ数の場合のみ、ギャップ最小の隣接ペア併合を数が合うまで
    決定論的に繰り返して吸収する。ラン数 < フレーズ数は吸収不能として
    fail-closed のまま。"""
    segs = list(segments)
    if len(segs) < expected:
        return None, (
            f"expected {expected} voiced run(s), got {len(segs)} "
            "(fail-closed; too few runs to merge down to the expected count)"
        )
    while len(segs) > expected:
        gaps = [(segs[i + 1][0] - segs[i][1], i) for i in range(len(segs) - 1)]
        _, merge_at = min(gaps, key=lambda pair: pair[0])
        segs = segs[:merge_at] + [(segs[merge_at][0], segs[merge_at + 1][1])] + segs[merge_at + 2:]
    return segs, None


def _process_t2_card(
    card_id: str, samples: np.ndarray, sr: int, dsdict: DsDict
) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    phrases = T2_PHRASES[card_id]
    tokens_per_phrase, err = _phrase_tokens_dsdict(card_id, phrases, dsdict)
    if err is not None:
        return None, err
    assert tokens_per_phrase is not None

    segs = _segments_by_gap(
        samples, sr, silence_db=SILENCE_DB, frame_sec=FRAME_SEC,
        min_gap_sec=T2_MIN_GAP_SEC, min_run_sec=T2_MIN_RUN_SEC,
    )
    segs, err = _reconcile_t2_segment_count(segs, len(phrases))
    if err is not None:
        return None, f"{card_id}: {err}"
    assert segs is not None

    builder = _RowBuilder()
    total_dur = len(samples) / sr
    prev_end = 0.0
    for tokens, (start, end) in zip(tokens_per_phrase, segs):
        builder.add_rest(start - prev_end)
        seg_dur = end - start
        f0 = _f0_median_hz(samples, sr, start, end)
        if f0 is None:
            return None, f"{card_id}: no voiced f0 found in phrase run ({start:.3f}-{end:.3f}s) (fail-closed)"
        midi = _hz_to_midi_round(f0)
        if not tokens:
            return None, f"{card_id}: phrase produced zero dsdict tokens (fail-closed)"
        total_weight = sum(weight for _, weight in tokens)
        if total_weight <= 0:
            return None, f"{card_id}: phrase has non-positive total token weight (fail-closed)"
        unit_dur = seg_dur / total_weight
        for phonemes, weight in tokens:
            note_dur = unit_dur * weight
            if note_dur <= 0.0:
                return None, f"{card_id}: allocated non-positive note duration (fail-closed)"
            durations = _durations_for_phonemes(phonemes, note_dur)
            builder.add_note(phonemes, durations, midi, note_dur)
        prev_end = end
    builder.add_rest(total_dur - prev_end)
    return builder.to_row(card_id), None


# ---------------------------------------------------------------------------
# 7. 統括: 台帳突合 -> tier 別変換 -> duration 自己検査 -> 原子的公開
# ---------------------------------------------------------------------------


def _dispatch_card(
    card_id: str, samples: np.ndarray, sr: int, dsdict: DsDict
) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    tier = CARD_TIER.get(card_id)
    if tier is None:
        return None, f"{card_id}: no tier assignment (unknown card_id, fail-closed exclusion)"
    if tier == "T0":
        build_fn = build_sakura_score if card_id == "UC-001" else build_umi_score
        return _process_t0_card(card_id, samples, sr, build_fn)
    if tier == "T1":
        return _process_t1_card(card_id, samples, sr)
    return _process_t2_card(card_id, samples, sr, dsdict)


def convert(
    normalized_dir: Path,
    ledger_path: Path,
    out_dir: Path,
    dsdict_path: Path,
    duration_tolerance_sec: float = DEFAULT_DURATION_TOLERANCE_SEC,
    ffmpeg_bin: Optional[str] = None,
) -> Dict[str, object]:
    """`normalized_dir` の User 音源 17 本を `out_dir` へ変換する
    （`transcriptions.csv` + `wavs/` + `exclusions.json`）。台帳突合の違反や
    全カード除外・`--dsdict` の読み込み失敗は fail-closed で例外を送出する。
    それ以外の個別カードのアラインメント失敗は当該カードのみ除外し、他カードの
    変換は継続する。`dsdict_path` は T2（短句）のグラフェム音素化にのみ使う
    （T0/T1 は無改変、C2 参照）。"""
    normalized_dir = Path(normalized_dir)
    out_dir = Path(out_dir)

    entries = load_ledger(Path(ledger_path))
    by_card = reconcile_ledger(normalized_dir, entries)  # fail-closed はここで完結
    dsdict = load_dsdict(Path(dsdict_path))  # fail-closed はここで完結

    rows: List[Dict[str, str]] = []
    included: List[str] = []
    excluded: List[Dict[str, str]] = []
    wav_paths: Dict[str, Path] = {}

    for card_id in sorted(by_card):
        entry = by_card[card_id]
        samples, sr = _load_mono(entry.path)
        tier = CARD_TIER.get(card_id, "unknown")
        row, reason = _dispatch_card(card_id, samples, sr, dsdict)

        if row is None:
            excluded.append({"card_id": card_id, "tier": tier, "reason": reason or "unknown"})
            continue

        wav_dur = len(samples) / sr
        ph_total = math.fsum(float(x) for x in row["ph_dur"].split())
        diff = abs(ph_total - wav_dur)
        if diff > duration_tolerance_sec:
            excluded.append({
                "card_id": card_id, "tier": tier,
                "reason": (
                    f"internal duration self-check failed: ph_dur total {ph_total:.6f}s vs "
                    f"wav duration {wav_dur:.6f}s (diff {diff:.6f}s > tolerance "
                    f"{duration_tolerance_sec:.6f}s)"
                ),
            })
            continue

        rows.append(row)
        included.append(card_id)
        wav_paths[card_id] = entry.path

    if not rows:
        raise AllCardsExcludedError(
            f"all {len(by_card)} card(s) excluded, nothing to publish (fail-closed): "
            + json.dumps(excluded, ensure_ascii=False)
        )

    staging_dir = out_dir.parent / f"{out_dir.name}.staging-{os.getpid()}"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)
    try:
        summary = _publish_into(rows, included, excluded, wav_paths, staging_dir, ffmpeg_bin, dsdict)
        convert_d3._swap_into_place(staging_dir, out_dir)
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return summary


def _publish_into(
    rows: List[Dict[str, str]],
    included: List[str],
    excluded: List[Dict[str, str]],
    wav_paths: Dict[str, Path],
    out_dir: Path,
    ffmpeg_bin: Optional[str],
    dsdict: DsDict,
) -> Dict[str, object]:
    out_wavs = out_dir / "wavs"
    out_wavs.mkdir(parents=True, exist_ok=True)

    rows_sorted = sorted(rows, key=lambda r: r["name"])
    with open(out_dir / "transcriptions.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["name", "ph_seq", "ph_dur", "ph_num", "note_seq", "note_dur"]
        )
        writer.writeheader()
        for row in rows_sorted:
            writer.writerow(row)

    for card_id in sorted(included):
        convert_d3.resample_to_44k1(wav_paths[card_id], out_wavs / f"{card_id}.wav", ffmpeg_bin=ffmpeg_bin)

    n_note_rows = 0
    n_rest_rows = 0
    total_ph_dur_s = 0.0
    voiced_ph_dur_s = 0.0
    phoneme_symbols: set = set()
    for row in rows_sorted:
        ph_tokens = row["ph_seq"].split()
        phoneme_symbols.update(ph_tokens)
        for token in row["note_seq"].split():
            if token == "rest":
                n_rest_rows += 1
            else:
                n_note_rows += 1
        durs = [float(x) for x in row["ph_dur"].split()]
        total_ph_dur_s += math.fsum(durs)
        for tok, d in zip(ph_tokens, durs):
            if tok != "SP":
                voiced_ph_dur_s += d

    excluded_sorted = sorted(excluded, key=lambda e: e["card_id"])
    dsdict_provenance = {"path": str(dsdict.path), "sha256": dsdict.sha256, "n_graphemes": len(dsdict.table)}
    exclusions_report = {
        "schema": "convert-user-exclusions/0.2",
        "dsdict": dsdict_provenance,
        "n_included": len(included),
        "n_excluded": len(excluded_sorted),
        "included_cards": sorted(included),
        "excluded": excluded_sorted,
    }
    (out_dir / "exclusions.json").write_text(
        json.dumps(exclusions_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return dict(
        n_segments=len(rows_sorted),
        n_note_rows=n_note_rows,
        n_rest_rows=n_rest_rows,
        total_ph_dur_s=round(total_ph_dur_s, 3),
        effective_minutes=round(total_ph_dur_s / 60.0, 3),
        voiced_ph_dur_s=round(voiced_ph_dur_s, 3),
        voiced_effective_minutes=round(voiced_ph_dur_s / 60.0, 3),
        phoneme_symbols=sorted(phoneme_symbols - {"SP"}),
        included_cards=sorted(included),
        excluded_cards=excluded_sorted,
        dsdict=dsdict_provenance,
    )


# ---------------------------------------------------------------------------
# 8. CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--normalized-dir", type=Path, required=True,
        help="User 正規化 wav (24kHz mono s16) のディレクトリ",
    )
    parser.add_argument(
        "--ledger", type=Path, required=True,
        help="user_donor_ledger.json（正: normalized_path のファイル名 + sha256）",
    )
    parser.add_argument(
        "--out-dir", type=Path, required=True,
        help="出力先 (transcriptions.csv / wavs/ / exclusions.json を書く)",
    )
    parser.add_argument(
        "--dsdict", type=Path, required=True,
        help=(
            "リツ公式 DiffSinger 配布 zip の dsdur/dsdict.yaml（T2 のグラフェム"
            "音素化に使用。sha256 は s1_dataprep/README.md の素材3 pin で"
            "事前照合すること）"
        ),
    )
    parser.add_argument(
        "--duration-tolerance-sec", type=float, default=DEFAULT_DURATION_TOLERANCE_SEC,
        help=f"ph_dur 合計と実 wav 長の許容乖離 秒 (既定: {DEFAULT_DURATION_TOLERANCE_SEC})",
    )
    parser.add_argument(
        "--ffmpeg-bin", default=None,
        help="ffmpeg バイナリのパス (既定: PATH 上の 'ffmpeg' を shutil.which で解決)",
    )
    args = parser.parse_args(argv)

    out_dir: Path = args.out_dir
    old_dir = out_dir.parent / f"{out_dir.name}.old"
    staging_dir = out_dir.parent / f"{out_dir.name}.staging-{os.getpid()}"
    try:
        convert_d3._reject_output_collision(
            [out_dir, old_dir, staging_dir],
            protected_roots=[args.normalized_dir, args.ledger.parent],
        )
    except convert_d3.OutputCollisionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        summary = convert(
            args.normalized_dir, args.ledger, args.out_dir, args.dsdict,
            args.duration_tolerance_sec, args.ffmpeg_bin,
        )
    except (
        LedgerMismatchError,
        AllCardsExcludedError,
        DsDictError,
        convert_d3.FfmpegNotFoundError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"n_segments={summary['n_segments']}")
    print(f"n_note_rows={summary['n_note_rows']}")
    print(f"n_rest_rows={summary['n_rest_rows']}")
    print(f"total_ph_dur_s={summary['total_ph_dur_s']:.2f} ({summary['effective_minutes']:.2f} min)")
    print(
        f"voiced_ph_dur_s={summary['voiced_ph_dur_s']:.2f} "
        f"({summary['voiced_effective_minutes']:.2f} min)"
    )
    print(f"included_cards={summary['included_cards']}")
    print(f"excluded_cards={[e['card_id'] for e in summary['excluded_cards']]}")
    print(f"dsdict_sha256={summary['dsdict']['sha256']} n_graphemes={summary['dsdict']['n_graphemes']}")
    if summary["excluded_cards"]:
        print("exclusion reasons:", file=sys.stderr)
        for e in summary["excluded_cards"]:
            print(f"  - {e['card_id']} ({e['tier']}): {e['reason']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
