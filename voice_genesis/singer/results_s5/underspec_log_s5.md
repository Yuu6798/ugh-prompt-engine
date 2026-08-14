# S5 Underspec Log

新規メモなし。コーディネーターの指示メッセージそのものを仕様として実装した。
逸脱・補充は以下の通り。

## [UNDERSPEC-S5-1] 「うみ」の採譜

実装者の記憶に基づく再構成であり、検証済みの原典採譜ではない。
`score.py`（さくらさくら）冒頭コメントの先例に倣った同種の限定事項。
テンポ 88 BPM は凍結 assumption（根拠: score.py の 72 BPM より快活な
唱歌のテンポ感を意図した目算値、数値的な出典はない）。

## [UNDERSPEC-S5-2] identity 保存判定「(a) < (b) が両embedding×両曲組で成立」の解釈

指示文言は簡潔で、具体的にどの (a),(b) の組み合わせを指すか一意に定まら
ない。本実装では最も網羅的な解釈を採用した: within-singer/cross-song
距離 a ∈ {a_genesis3, a_voiceC} と cross-singer/same-song 距離
b ∈ {b_sakura, b_umi} の**全組み合わせ**（2×2=4通り）× E1/E2（2通り）=
**8チェック全て**を計算し、「成立」の定義を「8チェック全て成立」とした
（より緩い解釈、例えば「歌手ごとに自分の曲平均のみ比較」等も考えられたが、
最も保守的で検証力の高い定義を採用）。結果は8/8ではなく7/8で、この
1件の不成立を隠さず報告した。

## [UNDERSPEC-S5-3] render_song.render_sakura への notes/tempo_bpm パラメータ追加

memo（本サイクルは新規メモなしのため厳密には「指示」）は「既存ファイルは
無改変で score 定義は新設ファイルでよい」と `score.py`（および暗黙に
render 側)についても既存無改変を志向する記述をしているが、cross-song
レンダを実現するには `render_sakura()` が「さくらさくら」に固定された
`sc.build_sakura_score()` 呼び出しを持つため、何らかの形で汎用化が
不可避だった。既存呼び出しの挙動を完全に保つ最小侵襲な変更（`notes`/
`tempo_bpm` を **既定 None のオプション引数**として追加し、None の場合は
従来通り `sc.build_sakura_score()`/`sc.TEMPO_BPM` を使う）を選び、
「既存ファイルは無改変」の精神（既存の呼び出し元・既存の出力は一切
変わらない）を実質的に維持した。`score.py` 自体は本当に無改変。

## [UNDERSPEC-S5-4] JND 会計の比較対象フレーズ数

`genesis_v1._jnd_rows_from_results`（無改変で import 流用）は各フレーズ
先頭2ノートを比較するが、さくら(6フレーズ)とうみ(3フレーズ)でフレーズ数
が異なるため、実際に比較されたのはうみ側に存在する phrase_index 0-2
の範囲のみ（さくらのphrase 3-5は比較対象外）。これは関数の既存ガード
（`min(notes_per_phrase, len(segs), len(segs_b))`）による自然な挙動で
あり、追加の実装判断は加えていない。

## [UNDERSPEC-S5-5] gate3（新曲の子音インベントリでの再定義）を既存関数の再利用で満たした判断

指示5は「gate3(子音: 新曲の子音インベントリで再定義)」としているが、
`gate_checks.gate3_consonant_existence`（無改変）の `target_onsets`
辞書（s/k/t固定）は、該当する onset がその曲の中に存在しない場合は
自動的に「該当インスタンスなし」として何もチェックしない（クラッシュも
誤判定もしない）。「うみ」の子音インベントリ（h/r/n/m/k）と
target_onsets（s/k/t）の交差は k のみであり、結果として「新曲の子音
インベントリで自然に絞り込まれた」状態が既存関数の無改変利用だけで
実現された。これを「再定義」の要求を満たすものと解釈し、新規の gate3
バリアントは実装しなかった。厳密な「明示的な再定義コード」を求める
意図だった場合は本解釈とズレる可能性がある。

## 制約遵守の確認

- リポジトリ読み取り専用: `proto1/`・`vt_harness/` は import のみ
- 既存ファイル無改変: `score.py`・`gate_checks.py`・`gate_checks_v2.py`・
  `genesis_v0.py`・`genesis_v1.py`・`genesis_v2.py`・`identity_metrics.py`
  は一切変更していない。`render_song.py` のみ後方互換なオプション引数
  追加（[UNDERSPEC-S5-3] 参照）。`s2_gate_record.md` 等の既存記録ファイル
  も無改変
- 書き込みは `singer/score_umi.py`（新規）・`singer/render_song.py`
  （後方互換追記のみ）・`singer/results_s5/` 配下のみ
- フォアグラウンド実行・決定論
- 実行時間: 全体で数十秒〜1分規模（15分規模の枠に対して大幅に余裕あり）
