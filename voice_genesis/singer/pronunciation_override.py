"""singer/pronunciation_override.py — S6: モーラ単位の発音上書き機構（新設）。

`phoneme_jp.py`（凍結・無改変）は「は」を無条件に /h,a/ へ変換する
（歴史的仮名遣いに由来する助詞「は→わ」規則を持たない）。本モジュールは
`phoneme_jp.py` を一切変更せず、score 側（歌詞データ）が「このモーラは
助詞として読む」と明示的に指定した位置にだけ、モーラ単位で発音を上書きする
軽量機構を提供する。

**スコープ限定（明記）**: 汎用の形態素解析・自動的な助詞判定は行わない。
score 側が override すべきモーラの位置（インデックス）を明示的に列挙する
方式のみをサポートする。
"""
from __future__ import annotations

from typing import Dict, List

import phoneme_jp as pj

# 助詞「は」の発音上書き: kana 表記は「は」のまま保つ（歌詞表示・採譜上の
# 由来を残す）が、音韻実現を「わ」（onset=w, vowel=a）に差し替える。
PARTICLE_HA_AS_WA = pj.Mora(kana="は", onset="w", vowel="a", is_long_vowel_mark=False, is_moraic_nasal=False)


def apply_overrides(morae: List[pj.Mora], overrides: Dict[int, pj.Mora]) -> List[pj.Mora]:
    """`overrides`（0始まりインデックス -> 差し替え後 Mora）を `morae` に適用する。

    インデックス範囲外の指定は AssertionError（score 側の設定ミスを早期検出
    するため、静かに無視しない）。
    """
    out = list(morae)
    for idx, replacement in overrides.items():
        assert 0 <= idx < len(out), (idx, len(out))
        out[idx] = replacement
    return out
