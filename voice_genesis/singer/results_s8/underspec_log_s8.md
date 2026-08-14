# S8 Underspec Log

新規メモなし。コーディネーターの指示メッセージそのものを仕様として実装した。
逸脱・補充は以下の通り。

## [UNDERSPEC-S8-1] consonant_checks_v4 の計測方式変更（オーディオ解析→制御信号直接計測）

当初 memo の「開放速度」「マーマー閉鎖度」をレンダ済みオーディオの
スペクトル重心追跡で実装したが、`apply_time_varying_formant_filter`
自身のSTFT平滑化（20ms窓/10ms hop）と干渉し、旧実装(v3)と新実装(v4)を
安定に弁別できないことが実測で判明した（v3のnで release_frac=0.899と
いう「速い」誤判定になり、diagnosis節で直接確認した約50msの遅い遷移
という事実と矛盾）。そのため、レンダラが実際に使う commanded フォルマント
タイムライン（`formant_tv.interpolate_formant_timeline` の出力。決定論的な
制御信号そのもの）を一時的な spy で捕捉し直接測定する方式に変更した。
これは S4 の gate6-v2（score-informed QC: 検査者は命令値を知ってよい、
ブラインド推定の性能保証は別トラックの責務）の原理を本サイクルに援用した
判断であり、`consonant_checks_v4.py` の docstring にも明記した。
`formant_tv.py`・`render_song.py`・`render_song_v3.py`・`render_song_v4.py`
のいずれも本変更のために追加改変していない（計測モジュール側のみの
方式変更）。

## [UNDERSPEC-S8-2] 鼻音開放境界の検出ヒューリスティック

`render_song_v4.interpolate_formant_timeline_v4` は「現区間の F1 が
NASAL_FORMANTS_HZ[0](250Hz)の近傍(±20Hz)、かつ次区間のF1がそこから
明確に離れている」場合を鼻音開放境界と判定する。この方式は
formant_scale=1.0（安全域ボックスで保証済み、S4以降の全genome）を
前提としており、formant_scale が大きく異なるgenomeでは閾値の再調整が
必要になる可能性がある。現行の題材（voice_C・genesis3）はいずれも
formant_scale=1.0のため実害はない。

## [UNDERSPEC-S8-3] 撥音N（moraic nasal）への短絡境界の未適用

`render_song_v4` の鼻音開放短絡は「マーマー→母音」という2区間分割
構造（n/m onsetの通常モーラ）を前提にしており、撥音N（`build_note_
subsegments_v3`で単一SubSegmentとして返る）には境界自体が存在しない
ため短絡ロジックの対象外（該当する境界がそもそも無い）。現行の題材曲
2曲にNの実例が無いため実害はないが、将来Nを含む曲で「N→次モーラ」の
開放が同様の問題を起こす可能性は本サイクルでは未検証のまま
（S7の[UNDERSPEC-S7-1]と同型の限定事項）。

## [UNDERSPEC-S8-4] release_fraction計測の +50ms→+60ms オフセット変更

初期実装（オーディオベース）は+50ms時点を「最終値」の基準としていたが、
制御信号ベースの再実装では境界からの遷移幅が最大60ms（旧一律値）である
ことを踏まえ、確実に遷移完了後の値を基準にするため+60msへ変更した。
この変更は較正のやり直しに伴う実装上の調整であり、検査の意図
（「+10ms時点でどれだけ変化が完了しているか」）自体は変えていない。

## 制約遵守の確認

- リポジトリ読み取り専用: `proto1/`・`vt_harness/` は import のみ
- 既存ファイル無改変: `phoneme_jp.py`・`score.py`・`score_umi.py`・
  `score_umi_v2.py`・`render_song.py`・`render_song_v2.py`・
  `render_song_v3.py`・`formant_tv.py`・`gate_checks.py`・
  `consonant_checks_v2.py`・`consonant_checks_v3.py`・
  `identity_metrics.py` は一切変更していない
- 書き込みは `singer/render_song_v4.py`・`singer/consonant_checks_v4.py`・
  `singer/results_s8/` 配下のみ
- フォアグラウンド実行・決定論（4音源全てで決定論チェック再確認済み）
- 実行時間: 診断（formant timeline実測）+ 検査実装較正（オーディオ版の
  失敗と制御信号版への切替を含む）+ 最終レンダ+ゲート再検証+identity
  再計測の合計で15分規模の枠内
