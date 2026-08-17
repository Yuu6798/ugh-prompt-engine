"""singer/score_d3_kana.py — S3 Phase B1 (D3): かな短句譜（VCV 被覆内・拗音/撥音を含む）。

`voice_genesis/foundry/DESIGN_S3_backfill.md` §2.1 item3「かな短句譜（新規）:
リツ VCV 被覆内のかな連続（促音・拗音・ん を含む）短フレーズ数本」に対応する。

## VCV 被覆確認（実測・[実装決定・record 記録]）

波音リツ強連続音 Ver1.5.1 の oto.ini（A3/F4 両 pitch dir）を
`adapter.donor_bank_utau.parse_oto_ini` + `parse_alias_mora` で実解析し、
本スコアの全モーラ遷移文脈 (prev_vowel, mora) が A3/F4 両方に存在する
（`unit_contexts` 完全一致 = `match_kind="exact"`）ことを事前検証した
（検証スクリプトは実行のみ・非コミット。結果は S3 record に転記）。

**促音「っ」は対象外**（設計逸脱・要 User/Fable 確認、record に明記):
`phoneme_jp.kana_to_morae` は「っ」を未対応文字として `ValueError` を送出し
（`phoneme_jp.py` 冒頭コメント「対応外」記載どおり）、
`donor_bank_utau.normalize_mora_kana` も "っ" を `status="sokuon"` として
明示的に未マップのまま返す（`(None, None, "sokuon")`）。加えて oto.ini 実測
（A3/F4 とも）で促音専用のエイリアス（follows-cutoff/geminate 用の特殊
セグメンテーション）は 1 件も存在しない。促音の音響表現（前音節末の破裂閉鎖
音を伸ばす等）には音素モデル・タイミングモデル双方の拡張が要り、B1 の
スコープ（registry 化・timing export・新譜追加）を超えるため、本スコアでは
拗音・撥音（ん）のみを採用し、促音を含む短句は見送った。促音対応は
S3 の別 Open Question として設計へ差し戻す。

## 撥音「ん」直後の VCV 文脈トークン修正（`adapter.units.vcv_context_sequence`）

oto.ini の実エイリアス（例 `n わ`）は「ん の直後」の prev トークンとして
小文字 `"n"` を使う（UTAU 慣習）。一方 `phoneme_jp.Mora` の撥音は
`vowel="N"`（大文字、母音でなく疑似母音のプレースホルダ）のため、
`vcv_context_sequence` が撥音直後の prev_vowel にそのまま `mora.vowel` を
使うと `"N" != "n"` で厳密一致が常に外れ、`near` フォールバック（mora の
みの一致・文脈粒度が落ちる）へ縮退していた（sakura/umi の歌詞には「ん」
が一切出現しないため、これまで未発動のまま潜在していた分岐——本モジュール
の導入で初めて撥音直後の文脈が実際にレンダリングされる）。
`vcv_context_sequence` に撥音直後は prev トークンを `"n"` へ変換する分岐を
追加した（sakura/umi は無影響 = 両曲とも「ん」を含まないため分岐そのものが
発火しない。byte-identity 回帰で実証済み）。修正後、本スコアの全 21 モーラ
遷移が A3/F4 両方で `match_kind="exact"` になることを確認済み（修正前は
撥音直後の 6 遷移が `near` へ縮退していた）。

## 内容

6 短句（拗音 5 種 きょ/ぎょ/しゃ/ひゃ/りょ・撥音「ん」を全句に含む）。
音高はさくら/うみと同帯（`score.py` 都節音階の度数 = A3=57/Bb3=58/D4=62/
E4=64/F4=65。新規音律なし）。テンポ 84 BPM（[実装決定・assumption]
「短句」らしい快活さのため score.py(72)/score_umi.py(88) の中間寄りの
値を採用）。1 モーラ=1 ノート（score.py と同じ制約）、フレーズ末モーラは
2 拍（他は 1 拍）。
"""
from __future__ import annotations

from typing import List

import phoneme_jp as pj
from score import ScoreNote  # 無改変・import 流用（型を共有するだけ）

TEMPO_BPM = 84.0

# フレーズ = (かな文字列, [midi, ...])。最終モーラは duration_beats=2.0、他は 1.0
# （score.py のフレーズ末長め慣習を踏襲）。
_PHRASES = [
    ("さんかく", [57.0, 58.0, 62.0, 64.0]),      # 三角：さ/ん/か/く（撥音）
    ("きょうみ", [62.0, 65.0, 62.0]),            # 興味：きょ/う/み（拗音 きょ）
    ("にんぎょう", [57.0, 58.0, 62.0, 64.0]),    # 人形：に/ん/ぎょ/う（撥音+拗音 ぎょ）
    ("しゃしん", [64.0, 62.0, 58.0]),            # 写真：しゃ/し/ん（拗音 しゃ+撥音）
    ("ひゃくえん", [57.0, 62.0, 64.0, 65.0]),    # 百円：ひゃ/く/え/ん（拗音 ひゃ+撥音）
    ("りょかん", [65.0, 62.0, 58.0]),            # 旅館：りょ/か/ん（拗音 りょ+撥音）
]


def build_d3_kana_score() -> List[ScoreNote]:
    notes: List[ScoreNote] = []
    for phrase_idx, (kana, pitch_plan) in enumerate(_PHRASES):
        morae = pj.kana_to_morae(kana)
        assert len(morae) == len(pitch_plan), (kana, len(morae), len(pitch_plan))
        for i, (mora, midi) in enumerate(zip(morae, pitch_plan)):
            is_final = i == len(morae) - 1
            notes.append(
                ScoreNote(
                    midi=midi, duration_beats=(2.0 if is_final else 1.0), mora=mora,
                    phrase_index=phrase_idx, is_phrase_final=is_final,
                )
            )
    return notes


def beats_to_seconds(beats: float, tempo_bpm: float = TEMPO_BPM) -> float:
    return beats * 60.0 / tempo_bpm
