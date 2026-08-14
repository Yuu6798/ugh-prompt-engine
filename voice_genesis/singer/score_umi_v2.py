"""singer/score_umi_v2.py — S6: 「うみ」score に助詞「は→わ」発音上書きを適用。

`score_umi.py`（S5、無改変）のフレーズ定義・音高計画は完全に再利用し、
`pronunciation_override.py`（新設）で「うみは」の「は」（助詞。歴史的
仮名遣いにより [wa] と発音するのが正しい日本語）のみモーラを差し替える。
"""
from __future__ import annotations

from typing import List

import phoneme_jp as pj
import pronunciation_override as po
from score import ScoreNote
from score_umi import _PHRASES, TEMPO_BPM  # noqa: F401  (無改変 import 流用)


def build_umi_score_v2() -> List[ScoreNote]:
    notes: List[ScoreNote] = []
    for phrase_idx, (kana, pitch_plan) in enumerate(_PHRASES):
        morae = pj.kana_to_morae(kana)
        assert len(morae) == len(pitch_plan), (kana, len(morae), len(pitch_plan))
        if kana == "うみは":
            # 「は」= 3モーラ目（0始まりindex=2）は助詞 → わ と発音する
            morae = po.apply_overrides(morae, {2: po.PARTICLE_HA_AS_WA})
        for i, (mora, (midi, dur_beats)) in enumerate(zip(morae, pitch_plan)):
            is_final = i == len(morae) - 1
            notes.append(
                ScoreNote(
                    midi=midi, duration_beats=float(dur_beats), mora=mora,
                    phrase_index=phrase_idx, is_phrase_final=is_final,
                )
            )
    return notes
