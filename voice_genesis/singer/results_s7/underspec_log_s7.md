# S7 Underspec Log

新規メモなし。コーディネーターの指示メッセージそのものを仕様として実装した。
逸脱・補充は以下の通り。

## [UNDERSPEC-S7-1] 撥音N（moraic nasal）は consonant_checks_v3 の
onset ベース検出で識別不能

`render_song.SubSegmentOut.onset` は `seg.note.mora.onset` から設定される
が、撥音N（moraic nasal）は `mora.onset=None`（`is_moraic_nasal=True` で
区別）であり、`subsegments_out` の onset フィールドだけでは通常の母音
開始モーラと区別できない。`consonant_checks_v3.py` の
`w_discrimination_report` は onset ベースのグルーピングのため、撥音Nの
振幅ディップ/高域減衰は検査対象外のまま（`render_song_v3.py` 側の
`render_sakura_v3` 後処理では `result.segments` から `mora.is_moraic_nasal`
を直接見て処理しているため**レンダリング自体はNにも適用済み**——検査
（consonant_checks_v3）側だけが未対応）。現行の題材曲2曲（さくら・うみ）
にNの実例が無いため実害はないが、将来Nを含む曲を扱う際は
`consonant_checks_v3` の識別方法を拡張する必要がある。

## [UNDERSPEC-S7-2] identity 非退行条件（S6の6/8を下回らない）が未達（5/8）

memo item3 の非退行要求を満たせなかった。実測による切り分けで、原因は
鼻音修正の3要素（振幅ディップ・反共振ノッチ・高域減衰）**いずれか単体
でも** voiceC の within-singer/cross-song E1 距離を有意に押し上げること
と特定した（`nasal_fix_report.md` §5 参照、個別寄与の実測表を含む）。
/n//m/ は両曲に頻出する音素であり、その音響的実現の変更が集約
embeddingに与える影響を完全に回避する余地が見当たらなかった（S6の
助詞オーバーライドと同型の構造的緊張）。修正を弱めて非退行を優先する
選択肢も検討したが、耳判定で明確に指摘された実在の欠陥（/n/→/w/の
音響的縮退、旧実装で /w/ とほぼ同一のディップ量だったインスタンスの
実測確認込み）を是正することを優先し、5/8という結果を隠さず報告する
判断を取った。

## [UNDERSPEC-S7-3] 鼻音スペクトル整形を「後段DSP」として実装した判断

memo (b) の「鼻音スペクトル構造（反共振・高域減衰）」を実現するには、
`formant_tv.py` の共振（Lorentzian peak）型フィルタに真の反共振（零点）
を追加する改修が本来望ましいが、時間制約と「既存ファイル無改変」の
制約から、`formant_tv.py` は一切変更せず、鼻音区間の**レンダ後waveform**
へFFTベースの notch+高域減衰を後段DSPとして適用する設計を採った。
物理的な声道共鳴モデルとしての正確性は簡略化されているが、目標とする
音響的コントラスト（反共振帯域の減衰・高域減衰）自体は実現できている
ことを実測（§3）で確認済み。

## [UNDERSPEC-S7-4] 撥音Nの nasal_dur タイミング

`render_sakura_v3` の後処理で撥音Nの鼻音区間を「ノート全体
（`c_end = seg.end_sample`）」として扱った（撥音は子音+母音の分割が
そもそも無く、ノート全体が鼻音のため）。`_CONSONANT_TIMING_MS` に
"N" のエントリは無く（既存 `build_note_subsegments` の
`mora.is_moraic_nasal` 分岐がタイミング辞書を使わずノート全体を1
セグメントとして返す設計を踏襲）、本サイクルでも新規タイミング値を
追加しなかった。

## 制約遵守の確認

- リポジトリ読み取り専用: `proto1/`・`vt_harness/` は import のみ
- 既存ファイル無改変: `phoneme_jp.py`・`score.py`・`score_umi.py`・
  `score_umi_v2.py`・`render_song.py`・`render_song_v2.py`・
  `gate_checks.py`・`consonant_checks_v2.py`・`identity_metrics.py`・
  `formant_tv.py` は一切変更していない
- 書き込みは `singer/render_song_v3.py`・`singer/consonant_checks_v3.py`・
  `singer/results_s7/` 配下のみ
- フォアグラウンド実行・決定論（4音源全てで決定論チェック再確認済み）
- 実行時間: 較正+最終レンダ+ゲート再検証+identity再計測+切り分け実験の
  合計で数分規模（15分規模の枠内）
