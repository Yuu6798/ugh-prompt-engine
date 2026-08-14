"""VG-020 frontend の凍結 fixture テスト（かな -> モーラ -> 音素）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import phoneme_jp as pj


def test_simple_consonant_vowel_mora():
    morae = pj.kana_to_morae("さくら")
    assert [(m.kana, m.onset, m.vowel) for m in morae] == [
        ("さ", "s", "a"), ("く", "k", "u"), ("ら", "r", "a"),
    ]


def test_vowel_only_mora():
    morae = pj.kana_to_morae("あい")
    assert [(m.onset, m.vowel) for m in morae] == [(None, "a"), (None, "i")]


def test_yoon_digraph():
    morae = pj.kana_to_morae("きゃきゅきょ")
    assert [(m.kana, m.onset, m.vowel) for m in morae] == [
        ("きゃ", "k", "a"), ("きゅ", "k", "u"), ("きょ", "k", "o"),
    ]


def test_choon_extends_previous_vowel():
    morae = pj.kana_to_morae("らーめん")
    assert morae[0].onset == "r" and morae[0].vowel == "a"
    assert morae[1].is_long_vowel_mark is True
    assert morae[1].vowel == "a"  # 直前母音を継承
    assert morae[2].onset == "m" and morae[2].vowel == "e"
    assert morae[3].is_moraic_nasal is True
    assert morae[3].vowel == "N"


def test_moraic_nasal_alone():
    morae = pj.kana_to_morae("ん")
    assert len(morae) == 1
    assert morae[0].is_moraic_nasal is True
    assert morae[0].onset is None
    assert morae[0].vowel == "N"


def test_unsupported_char_raises():
    try:
        pj.kana_to_morae("A")
        assert False, "should have raised"
    except ValueError:
        pass


def test_sakura_lyrics_20_morae():
    phrases = ["さくら", "さくら", "やよいの", "そらは", "みわたす", "かぎり"]
    morae = pj.lyrics_to_morae(phrases)
    assert len(morae) == 20
    kanas = [m.kana for m in morae]
    assert kanas == list("さくらさくら") + list("やよいの") + list("そらは") + list("みわたす") + list("かぎり")
    # 子音インベントリの範囲内であることを確認
    for m in morae:
        if m.onset is not None:
            assert m.onset in pj.CONSONANTS
        assert m.vowel in pj.VOWELS or m.vowel == "N"
