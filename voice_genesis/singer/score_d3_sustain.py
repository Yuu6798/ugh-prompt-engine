"""singer/score_d3_sustain.py — S3 Phase B1 (D3): サステイン譜（5 母音 × 3 段ロングトーン）。

`voice_genesis/foundry/DESIGN_S3_backfill.md` §2.1 item2「サステイン譜（新規）:
5 母音 × 低→中→高 3 段ロングトーン」に対応する。狙いは D3（リツ極の「歌唱と
しての」データ）のうち、長母音サステインの崩壊（り→ん破綻）対策の主力データ
——各母音を低・中・高の 3 音高で単独ロングトーンとして歌わせ、母音定常部の
伸縮/接続を広い音高レンジで学習データ化する。

音高: `score.py` の都節音階（in 音階 on A3=MIDI57、半音オフセット
0,1,5,7,8,12）と同じ 3 度数（deg1=A3=57 / deg3=D4=62 / deg5=F4=65）を低/中/高
に割り当てる（[実装決定] 新規の音律を導入せず、既存 sakura/umi と同じ
「A3 バンク帯中心」の音高集合を再利用。F4=65 は donor bank の 2 pitch dir
（A3/F4）のうち高い方と一致し、そのまま実測比較しやすい）。

拍子・テンポ: 4/4、四分音符=60 BPM（[実装決定・assumption] 1 拍=1 秒となる
値を選び、`duration_beats=4.0`（=4.0 秒。設計書の「各段 3〜5 秒相当」の範囲内）
を全 15 フレーズ共通で使う。score.py/score_umi.py 同様、1 モーラ=1 ノート。

各母音単独ロングトーンはそれぞれ独立の 1 モーラ 1 フレーズとして扱う
（`phrase_index` を全 15 ノートで別々に振る）——`performance.build_timeline`
のフレーズ境界機構（フレーズ先頭にブレス・フレーズ末に減衰フェード）を
そのまま使うことで、各ロングトーンが「息を吸って持続して離す」という
歌唱としての自然なアタック/リリース形状を得る（追加実装なし・
[実装決定・record 記録]）。

かな文字は母音のみ（あ/い/う/え/お、`phoneme_jp._PLAIN_TABLE` 対応・
onset=None）を使うため、拗音・撥音・促音は本スコアの対象外
（`DESIGN_S3_backfill.md` §2.1 item3「かな短句譜」側の責務）。
"""
from __future__ import annotations

from typing import List

import phoneme_jp as pj
from score import ScoreNote  # 無改変・import 流用（型を共有するだけ）

TEMPO_BPM = 60.0

_LOW_MIDI = 57.0  # A3（score.py 都節音階 deg1）
_MID_MIDI = 62.0  # D4（deg3）
_HIGH_MIDI = 65.0  # F4（deg5・donor bank の高音側 pitch dir と一致）

_TIER_MIDI = (_LOW_MIDI, _MID_MIDI, _HIGH_MIDI)
_TIER_NAMES = ("low", "mid", "high")

_VOWEL_KANA = ("あ", "い", "う", "え", "お")

_DUR_BEATS = 4.0  # 60 BPM で 4.0 秒（設計書「各段 3〜5 秒相当」の範囲内）


def build_d3_sustain_score() -> List[ScoreNote]:
    notes: List[ScoreNote] = []
    phrase_idx = 0
    for kana in _VOWEL_KANA:
        morae = pj.kana_to_morae(kana)
        assert len(morae) == 1, (kana, morae)
        mora = morae[0]
        for midi in _TIER_MIDI:
            notes.append(
                ScoreNote(
                    midi=midi, duration_beats=_DUR_BEATS, mora=mora,
                    phrase_index=phrase_idx, is_phrase_final=True,
                )
            )
            phrase_idx += 1
    return notes


def beats_to_seconds(beats: float, tempo_bpm: float = TEMPO_BPM) -> float:
    return beats * 60.0 / tempo_bpm
