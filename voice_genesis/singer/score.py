"""singer/score.py — S4 (VG-011): Score+Lyrics 定義。

題材（凍結）: パブリックドメイン俗謡「さくらさくら」冒頭 2 フレーズ
（明治期の旧歌詞版: さくら さくら / やよいの そらは / みわたす かぎり）。

[UNDERSPEC-S4-1: 採譜は本実装者の再構成であり、検証済みの原典採譜ではない]
実際の民謡「さくらさくら」は「都節音階（miyako-bushi / in 音階）」で歌われる
ことで知られる。本スコアはルート A3(=MIDI57) を主音とする in 音階
（半音差 0,1,5,7,8 = A, Bb, D, E, F）上に、歌唱者の記憶に基づく旋律の
輪郭（「さくらさくら」の同音反復→やや上行→フレーズ末で主音に回帰する
終止）を再構成したものである。厳密な原典スコアとの一致は検証していない
（memo が「採譜内容はmemo逸脱不可、実装時の音高列はunderspec_logに明記」と
明記しており、この委任に基づく）。

拍子・テンポは 4/4、四分音符=72 BPM（凍結・assumption、memo は明記せず）。
1 モーラ = 1 ノート（メリスマ・促音は本段階で扱わない、§S3 と同じ制約）。
フレーズ末モーラは長めのデュレーションを割り当て、フレーズ間に
performance.py 側でブレスを挿入する（フレーズ境界の情報として
`phrase_index` を各ノートに持たせる）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import phoneme_jp as pj

TEMPO_BPM = 72.0
BEATS_PER_BAR = 4

# in 音階（都節音階）on A3: 半音オフセット 0,1,5,7,8,12
_SCALE_ROOT_MIDI = 57.0  # A3
_SCALE_SEMITONES = {1: 0, 2: 1, 3: 5, 4: 7, 5: 8, 6: 12}


def _deg(n: int, octave_shift: int = 0) -> float:
    return _SCALE_ROOT_MIDI + _SCALE_SEMITONES[n] + 12 * octave_shift


@dataclass(frozen=True)
class ScoreNote:
    midi: float
    duration_beats: float
    mora: pj.Mora
    phrase_index: int
    is_phrase_final: bool


# フレーズ = (かな文字列, [(scale_degree, duration_beats, octave_shift), ...])
# scale_degree: 1=A3(0), 2=Bb3(+1), 3=D4(+5), 4=E4(+7), 5=F4(+8), 6=A4(+12)
_PHRASES = [
    ("さくら", [(4, 1, 0), (4, 1, 0), (5, 2, 0)]),
    ("さくら", [(4, 1, 0), (4, 1, 0), (3, 2, 0)]),
    ("やよいの", [(5, 1, 0), (6, 1, 0), (6, 1, 0), (5, 2, 0)]),
    ("そらは", [(4, 1, 0), (3, 1, 0), (3, 2, 0)]),
    ("みわたす", [(4, 1, 0), (5, 1, 0), (4, 1, 0), (3, 2, 0)]),
    ("かぎり", [(4, 1, 0), (3, 1, 0), (1, 4, 0)]),
]


def build_sakura_score() -> List[ScoreNote]:
    notes: List[ScoreNote] = []
    for phrase_idx, (kana, pitch_plan) in enumerate(_PHRASES):
        morae = pj.kana_to_morae(kana)
        assert len(morae) == len(pitch_plan), (kana, len(morae), len(pitch_plan))
        for i, (mora, (deg, dur_beats, oct_shift)) in enumerate(zip(morae, pitch_plan)):
            is_final = i == len(morae) - 1
            notes.append(
                ScoreNote(
                    midi=_deg(deg, oct_shift),
                    duration_beats=float(dur_beats),
                    mora=mora,
                    phrase_index=phrase_idx,
                    is_phrase_final=is_final,
                )
            )
    return notes


def beats_to_seconds(beats: float, tempo_bpm: float = TEMPO_BPM) -> float:
    return beats * 60.0 / tempo_bpm


def total_duration_beats(notes: List[ScoreNote]) -> float:
    return sum(n.duration_beats for n in notes)
