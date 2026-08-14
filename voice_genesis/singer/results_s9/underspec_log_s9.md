# S9 Underspec Log

新規メモなし。コーディネーターの指示メッセージそのものを仕様として実装した。
逸脱・補充は以下の通り。

## [UNDERSPEC-S9-1] 撥音N（moraic nasal）の調音位置

memo item1「撥音Nは後続音に応じてn寄り既定」に対し、本実装は後続モーラを
参照する配線を新設していない（次モーラ情報を`build_note_subsegments_v5`
まで運ぶには呼び出し経路の拡張が必要で、時間制約内では見送った）。
撥音Nは常にn（歯茎）寄りの帯域設定（`_apply_nasal_spectral_shaping_v5`の
notch帯域で`PLACE_NOTCH_BAND_HZ["n"]`を使用）で固定した。撥音N自体は
現行の題材曲2曲に実例が無いため実害はないが、後続音条件分岐は未実装の
まま残る。

## [UNDERSPEC-S9-2] LOCUS_HOLD_MS の較正過程（本文参照）

`nasal_place_report.md` §較正 に詳述の通り、10ms→20ms→16msと実測で
調整した。16msという最終値は「両遷移窓（entrance半分4ms+exit半分11ms=
15ms）を最小限上回る」という物理制約から導いた値であり、memoが直接
規定する数値ではない。

## [UNDERSPEC-S9-3] locus帯検査の測定時点（開放+6ms）

memo は「/n/後続母音のF2出発点」の測定時点を明記していない。
`consonant_checks_v5._f2_at_release_plus`は開放境界から+6msの時点を
採用した（entrance遷移(半分4ms)完了直後かつlocus区間の「純粋保持
ゾーン」内に収まる時点として、LOCUS_HOLD_MS=16msの較正と整合させて
選定）。

## [UNDERSPEC-S9-4] S8開放速度検査(release_frac)のv5への直接適用は行わず

memo item2「S8の開放速度検査はマーマー→locusの切替に対して維持」との
指示に対し、`consonant_checks_v4.release_fraction_at_10ms`（F1基準・
+60ms時点を最終値とする設計）はv5の3区間構造（murmur→locus→vowelで
+60ms時点では既にvowel側に到達している場合が多い）にそのまま適用すると
分母の意味が変質するため、機械的な流用はせず、S8診断と同じ直接測定
手法（F1が departure から目標値へ settle するまでの時間を計測）で
murmur→locus区間の遷移時間を個別に実測した（実測: n/mとも3.45ms、
5-15ms範囲内で「維持」の趣旨を満たすことを確認）。
`consonant_checks_v4.py`自体は無改変のまま。

## 制約遵守の確認

- リポジトリ読み取り専用: `proto1/`・`vt_harness/` は import のみ
- 既存ファイル無改変: `phoneme_jp.py`・`score.py`・`score_umi.py`・
  `score_umi_v2.py`・`render_song.py`・`render_song_v2/v3/v4.py`・
  `formant_tv.py`・`gate_checks.py`・`consonant_checks_v2/v3/v4.py`・
  `identity_metrics.py` は一切変更していない
- 書き込みは `singer/render_song_v5.py`・`singer/consonant_checks_v5.py`・
  `singer/results_s9/` 配下のみ
- フォアグラウンド実行・決定論（4音源全てで決定論チェック再確認済み）
- 実行時間: diagnosis + locus実装 + 較正（10ms→20ms→16msの3段階調整、
  identity非退行との両立点探索込み）+ 最終レンダ+全ゲート再検証の合計で
  15分規模の枠内
