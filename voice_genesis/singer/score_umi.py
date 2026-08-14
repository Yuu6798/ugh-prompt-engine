"""singer/score_umi.py — S5: cross-song 実証用の第二の曲「うみ」。

題材（新設・score.py 無改変）: パブリックドメイン文部省唱歌「うみ」
（1941年 尋常小学校用、作詞: 林柳波 / 作曲: 井上武士。歌詞・旋律とも
著作権保護期間満了によりパブリックドメイン）冒頭 3 フレーズ
「うみは / ひろいな / おおきいな」。

選定理由:
  1. 既存音素インベントリ（`phoneme_jp.CONSONANTS = (s,k,t,g,r,m,n,w,y,h)`
     + 5 母音 + 撥音N/長音）でカバー可能（実測: う/み/は/ひ/ろ/い/な/お/き
     は全て onset ∈ {None,m,h,r,n,k} でインベントリ内、
     `phoneme_jp.kana_to_morae()` で分解確認済み）
  2. 「さくらさくら」（都節音階・4音構成）とは対照的な、広く親しまれた
     長音階寄りの旋律のため、cross-song 比較で「曲固有の旋律的個性」と
     「歌手固有の声質」を分離する実験（Experiment B 縮約）に適する
  3. 2フレーズ以上（本ファイルは3フレーズ）、メリスマ・促音なし（既存
     score.py と同じ制約、1モーラ=1ノート）

[UNDERSPEC-S5-1: 採譜は本実装者の記憶に基づく再構成であり、検証済みの
原典採譜ではない。score.py 冒頭コメントの先例（さくらさくらの採譜も
「本実装者の再構成」と明記）に倣う。C4=60 を基準とした素朴な長音階
（ドレミファソラシ）の輪郭で、「うみは」の同音反復→「ひろいな」で
やや上行→「おおきいな」でフレーズ末に主音方向へ下行終止、という
一般に流布した旋律の記憶上の骨格を再現したもの。テンポ = 4/4, 四分音符
=88 BPM（凍結・assumption、score.py の72 BPMより快活な唱歌のテンポ感を
意図した目算値）。
"""
from __future__ import annotations

from typing import List

import phoneme_jp as pj
from score import ScoreNote  # 無改変・import 流用（型を共有するだけ）

TEMPO_BPM = 88.0

# フレーズ = (かな文字列, [(midi, duration_beats), ...])
_PHRASES = [
    ("うみは", [(67.0, 1.0), (67.0, 1.0), (64.0, 2.0)]),          # G4 G4 E4
    ("ひろいな", [(67.0, 1.0), (69.0, 1.0), (67.0, 1.0), (64.0, 2.0)]),  # G4 A4 G4 E4
    ("おおきいな", [(67.0, 1.0), (69.0, 1.0), (67.0, 1.0), (64.0, 1.0), (62.0, 3.0)]),  # G4 A4 G4 E4 D4
]


def build_umi_score() -> List[ScoreNote]:
    notes: List[ScoreNote] = []
    for phrase_idx, (kana, pitch_plan) in enumerate(_PHRASES):
        morae = pj.kana_to_morae(kana)
        assert len(morae) == len(pitch_plan), (kana, len(morae), len(pitch_plan))
        for i, (mora, (midi, dur_beats)) in enumerate(zip(morae, pitch_plan)):
            is_final = i == len(morae) - 1
            notes.append(
                ScoreNote(
                    midi=midi, duration_beats=float(dur_beats), mora=mora,
                    phrase_index=phrase_idx, is_phrase_final=is_final,
                )
            )
    return notes


def beats_to_seconds(beats: float, tempo_bpm: float = TEMPO_BPM) -> float:
    return beats * 60.0 / tempo_bpm
