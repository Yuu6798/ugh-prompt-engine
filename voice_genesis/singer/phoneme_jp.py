"""singer/phoneme_jp.py — S3 (VG-020): ひらがな列 -> モーラ列 -> 音素列。

対応範囲（凍結・題材曲「さくらさくら」に必要な範囲 + fixture テスト用の
最小一般化）:
  - 単純モーラ（子音+母音 or 母音のみ）
  - 拗音（きゃ等、小書きゃゅょ）: 直前子音 + 半母音 y として onset を合成
    （音響エンジン側は onset の "manner"（最初の子音文字）で分岐するため、
    拗音は基底子音と同じ調音で近似する。厳密な口蓋化は本段階では扱わない
    [UNDERSPEC-S3-1]）
  - 長音「ー」: 直前モーラの母音を延長するモーラとして扱う（onset=None,
    vowel=直前と同じ, is_long_vowel_mark=True）
  - 撥音「ん」: 独立モーラ、onset=None, vowel="N"（疑似母音、鼻音レンダラで
    処理）

対応外（明記・本段階では扱わない）:
  - メリスマ（1モーラ=1ノート固定）、促音「っ」（本段階の題材曲に出現しない
    ため未実装。将来拡張点として名前のみ予約）

子音インベントリは r09_design_memo.md §S2 の最小集合
（s,k,t,g,r,m,n,w,y）に、題材曲「さくらさくら」の歌詞に必要な /h/
（「は」）を実装上の追加として含める（[UNDERSPEC-S3-2]、underspec_log_s1.md
に記録）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

VOWELS = ("a", "i", "u", "e", "o")

# 子音インベントリ: memo の最小集合 {s,k,t,g,r,m,n,w,y} + 実装上の追加 {h}
CONSONANTS = ("s", "k", "t", "g", "r", "m", "n", "w", "y", "h")


@dataclass(frozen=True)
class Mora:
    kana: str
    onset: Optional[str]  # None = 母音のみ / 撥音 / 長音
    vowel: str  # a/i/u/e/o、または撥音の疑似母音 "N"
    is_long_vowel_mark: bool = False
    is_moraic_nasal: bool = False


# --- 五十音 + 濁音・半濁音 + 拗音の最小対応表（凍結 fixture） ---
# 直音（子音+母音 / 母音のみ）
_PLAIN_TABLE = {
    "あ": (None, "a"), "い": (None, "i"), "う": (None, "u"), "え": (None, "e"), "お": (None, "o"),
    "か": ("k", "a"), "き": ("k", "i"), "く": ("k", "u"), "け": ("k", "e"), "こ": ("k", "o"),
    "が": ("g", "a"), "ぎ": ("g", "i"), "ぐ": ("g", "u"), "げ": ("g", "e"), "ご": ("g", "o"),
    "さ": ("s", "a"), "し": ("s", "i"), "す": ("s", "u"), "せ": ("s", "e"), "そ": ("s", "o"),
    "た": ("t", "a"), "ち": ("t", "i"), "つ": ("t", "u"), "て": ("t", "e"), "と": ("t", "o"),
    "な": ("n", "a"), "に": ("n", "i"), "ぬ": ("n", "u"), "ね": ("n", "e"), "の": ("n", "o"),
    "は": ("h", "a"), "ひ": ("h", "i"), "ふ": ("h", "u"), "へ": ("h", "e"), "ほ": ("h", "o"),
    "ま": ("m", "a"), "み": ("m", "i"), "む": ("m", "u"), "め": ("m", "e"), "も": ("m", "o"),
    "や": ("y", "a"), "ゆ": ("y", "u"), "よ": ("y", "o"),
    "ら": ("r", "a"), "り": ("r", "i"), "る": ("r", "u"), "れ": ("r", "e"), "ろ": ("r", "o"),
    "わ": ("w", "a"), "を": (None, "o"),
}

# 拗音: 基底子音 + 小書きゃゅょ -> onset は "子音+y" として合成表記
_YOON_BASE = {
    "きゃ": ("k", "a"), "きゅ": ("k", "u"), "きょ": ("k", "o"),
    "しゃ": ("s", "a"), "しゅ": ("s", "u"), "しょ": ("s", "o"),
    "ちゃ": ("t", "a"), "ちゅ": ("t", "u"), "ちょ": ("t", "o"),
    "にゃ": ("n", "a"), "にゅ": ("n", "u"), "にょ": ("n", "o"),
    "ひゃ": ("h", "a"), "ひゅ": ("h", "u"), "ひょ": ("h", "o"),
    "みゃ": ("m", "a"), "みゅ": ("m", "u"), "みょ": ("m", "o"),
    "りゃ": ("r", "a"), "りゅ": ("r", "u"), "りょ": ("r", "o"),
    "ぎゃ": ("g", "a"), "ぎゅ": ("g", "u"), "ぎょ": ("g", "o"),
}

_CHOON_MARK = "ー"
_MORAIC_NASAL = "ん"


def kana_to_morae(text: str) -> List[Mora]:
    """ひらがな列をモーラ列へ分割する（拗音・長音・撥音対応）。"""
    morae: List[Mora] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        if ch == _CHOON_MARK:
            prev_vowel = morae[-1].vowel if morae else "a"
            morae.append(Mora(kana=ch, onset=None, vowel=prev_vowel, is_long_vowel_mark=True))
            i += 1
            continue

        if ch == _MORAIC_NASAL:
            morae.append(Mora(kana=ch, onset=None, vowel="N", is_moraic_nasal=True))
            i += 1
            continue

        # 拗音（2文字先読み）
        if i + 1 < n and (ch + text[i + 1]) in _YOON_BASE:
            digraph = ch + text[i + 1]
            onset, vowel = _YOON_BASE[digraph]
            morae.append(Mora(kana=digraph, onset=onset, vowel=vowel))
            i += 2
            continue

        if ch in _PLAIN_TABLE:
            onset, vowel = _PLAIN_TABLE[ch]
            morae.append(Mora(kana=ch, onset=onset, vowel=vowel))
            i += 1
            continue

        raise ValueError(f"未対応のかな文字です（フロントエンドの対応範囲外）: {ch!r} in {text!r}")

    return morae


def lyrics_to_morae(phrases: List[str]) -> List[Mora]:
    """フレーズ（単語区切りの かな文字列）のリストをまとめてモーラ列に変換する。"""
    result: List[Mora] = []
    for phrase in phrases:
        result.extend(kana_to_morae(phrase))
    return result
