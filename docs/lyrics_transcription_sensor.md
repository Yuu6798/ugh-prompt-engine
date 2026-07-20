# 歌詞転写センサー (Lyrics Transcription Sensor)

Status: 実装済み。fake-backend テストに加え、2026-07-05 スモーク（§8）と
2026-07-20 WI0-b（§9、identity anchor observe 経由の実 E2E・vocals 分離込み）で
実推論を初計測済み。実ボーカル（歌入り）音源での精度実測は引き続き素材律速で
未計測（§7 参照）
Scope: `rpe/learned/lyrics_adapter.py`（faster-whisper アダプタ）、
`eval/lyrics_match.py`（照合計器）、`svprpe extract --lyrics` /
`svprpe lyrics-adherence` CLI
Audience: 歌詞転写センサーを利用・拡張するコントリビューター

## 1. 動機

[`docs/lyrics_semantic_anchor.md`](lyrics_semantic_anchor.md) が発見した通り、
歌詞は音楽の**意味層のアンカー**でありながら、これまで機械化された計器が
存在せず「耳が唯一のセンサー」だった。2026-07-03 のデモセッションで
faster-whisper + 既存 Demucs vocals stem による歌詞転写を手動で実証し、
本ドキュメントはそれを再現可能な計器として制度化する。

### CLAP との相補性

| | CLAP 意味軸センサー (`semantic_axes.py`) | 歌詞転写センサー (本モジュール) |
|---|---|---|
| 読むもの | 連続値の grip（A/B `contrast_fit`） | 記号列（順序付き歌詞テキスト） |
| 例 | `vocal_presence` が高い/低い | 歌詞が「意図した通りの語順で」歌われているか |
| 検証方法 | cosine 適合度 | 文字列一致（`difflib.SequenceMatcher`） |
| Verdict | なし（grip として読む） | なし（計器として ratio を返すのみ） |

CLAP はボーカルの存在感という連続量を読めるが、歌詞の**内容**（どの単語が
どの順で歌われたか）は読めない。本センサーはその欠落を埋める記号列側の
計器であり、どちらも `LearnedAudioAnnotations` に隔離され、置き換えではなく
並存する。

## 2. 入力側: `svprpe extract --lyrics`

```bash
svprpe extract track.wav --lyrics -o rpe.json
svprpe extract track.wav --lyrics --lyrics-no-separate -o rpe.json
svprpe extract track.wav --lyrics --lyrics-model medium -o rpe.json
svprpe extract track.wav --clap-semantic --lyrics -o rpe.json
```

`--lyrics` は opt-in（`lyrics` extra が必要）。デフォルトでは既存の
Demucs 分離器（`io/source_separator.separate_stems`）で vocals stem を
切り出してから転写する（歌詞認識はフルミックスより vocals stem の方が
大幅に精度が高いため、`--separate` と同じ理由付け）。`--lyrics-no-separate`
でフルミックスを直接転写する（`separate` extra への依存を回避できる）。

出力は `LearnedAudioAnnotations.lyrics_transcription`
（`LearnedLyricsTranscription`）に隔離され、`--clap-semantic` /
`--clap-sections` と併用した場合は同じ `LearnedAudioAnnotations` レコードに
両方の読み取りが載る（`enabled_models` に `laion_clap` と
`faster_whisper` の両方が並ぶ）。

## 3. 出力側: `svprpe lyrics-adherence` — 検収計器（verdict なし）

```bash
svprpe lyrics-adherence generated_track.wav --expected lyrics.txt
svprpe lyrics-adherence generated_track.wav --expected lyrics.txt -o report.yaml
```

生成された音声が期待した歌詞を「順序通りに」歌っているかを、期待行ごとの
最良一致率として報告する（`eval/lyrics_match.match_lyrics`）。
`roundtrip` / `score-adherence` / `audit` と同じ方針で、pass/fail や
閾値判定は一切出力しない — 計器であって verdict ではない。

読みは 2 系統で相補的: 行ごとの `best_ratio` は**順序不問の存在読み**
（「この行はどこかで歌われたか」）、順序は行ごとの `match_offset` /
`out_of_order` と report 全体の `matched_offset_sequence` / `order_ratio`
が担う（**文字オフセットカーソル方式**: 正規化済み全文トランスクリプト内の
最良ウィンドウ開始位置を追い、前行より厳密に手前へ戻ったら `out_of_order`。
セグメント境界はデコーダのアーティファクトなので、1 セグメント内で逆順に
歌われたケースもオフセットで検出できる=セグメント境界非依存）+ 元々順序
敏感な `overall_similarity`。既知の限界: 同一歌詞の繰り返し（コーラス等）は
最初の出現オフセットにタイし、タイは非後退扱いのため順序統計では繰り返しを
区別できない。`best_match_index` / `matched_index_sequence` は存在読みの
デバッグ診断として残る。`-o` の YAML レポートには転写の `inference_config`
に加えて解決済み weights/license の `model` レコードも記録される
（provenance 監査用）。

## 4. 隔離ポリシー

`LearnedLyricsTranscription` の内容は `SemanticRPE.por_surface` /
`PhysicalRPE.*` / `SVPForGeneration.style_tags` に一切書き込まれない。
`LearnedAudioAnnotations.lyrics_transcription` に隔離されたまま。
詳細は [`docs/learned_models_policy.md`](learned_models_policy.md)
Section 2 を参照。

## 5. 決定論

`WhisperModel.transcribe(...)` は `temperature=0.0` かつ
`condition_on_previous_text=False` で呼ぶ — 貪欲デコードにし、各セグメント
のデコードが直前のテキストに依存しないようにする。これは CLAP アダプタの
決定論契約と同様に**同一マシン契約**である: CTranslate2 のカーネル選択は
ハードウェア/ビルドによって浮動小数点経路が変わりうるため、同じ貪欲
デコード目標を追っていても別マシンでの再実行がビット完全に再現する保証は
ない。

## 6. 依存関係

- `pip install -e ".[lyrics]"` — faster-whisper + demucs（Whisper 重みは初回
  `WhisperModel(...)` 構築時に遅延ダウンロード）。
- デフォルト経路（`extract --lyrics` / `lyrics-adherence`）は vocals 分離を
  先に行うため、`lyrics` extra は demucs を同梱する —
  **`pip install -e ".[lyrics]"` だけでデフォルト経路（vocals 分離込み）が
  立つことが契約**（`semantic-embed` extra が torch を明示同梱するのと同じ
  精神。[`docs/learned_models_policy.md`](learned_models_policy.md) 参照）。
- `--lyrics-no-separate` は demucs 不要のランタイム opt-out として残る
  （フルミックスを直接転写。demucs が import されない経路）。

## 7. 既知の限界

- 実ボーカル音源の committed fixture が存在しない — 実音源律速（他の
  learned センサーと同様、fake-backend テストのみで real-audio 実測は
  別途必要）。
- Whisper 系モデルはハルシネーション傾向がある: 無音区間や器楽区間で
  存在しない歌詞を生成することがある。`LearnedLyricsSegment.no_speech_prob`
  を記録しているのはこのため — 高い `no_speech_prob` を伴うセグメントは
  ハルシネーション疑いとして下流で扱うことができる（本モジュール自体は
  フィルタリングや verdict を行わない）。

## 8. 実推論結果（2026-07-05 初回スモーク）

`Systran/faster-whisper-small`（int8 / CPU / beam_size=5 / temperature=0）で
実推論スモークを実施した。素材は英語スピーチ 10 秒
（LibriSpeech 系 PD サンプル、16 kHz）と器楽合成曲
（`examples/sample_input/synth_05_fast_bright_d_major.wav`）。

- **転写品質・決定論**: スピーチは全文完全転写（言語検出 en 0.999）。
  `transcribe_lyrics` 2 回実行で `model_dump()` 完全一致
  （同一マシン決定論の実測確認）。
- **検収計器**: 発注 4 行 vs 転写の `lyrics-adherence` で
  per-line `best_ratio` 全行 1.0000 / `overall_similarity` 1.0000。
  なお初期実装は per-line が 0.38–0.40 に落ちる欠陥があった —
  whisper が長い 1 セグメントを返すと `SequenceMatcher.ratio` の
  長さペナルティで「完全に含まれる行」が低スコア化する。
  `_partial_ratio`（期待行長ウィンドウの最良一致）で是正済み。
  この経緯が「セグメント境界はデコーダの都合であり歌詞の行境界ではない」
  という設計注意の実測根拠。
- **ハルシネーションと honesty 信号**: 器楽曲（ボーカル無し）では
  「Oooo…」型の典型的ハルシネーションが 1 セグメント出るが、
  **`no_speech_prob` = 0.814**（スピーチでは 0.006）と綺麗に分離する。
  一方 `avg_logprob` は判別しない（器楽 −0.067 vs スピーチ −0.154）。
  → per-segment `no_speech_prob` を記録する設計の実測裏付け。
  下流はこれをハルシネーション疑いの目印に使える（本計器は verdict しない）。
- **隔離の実測確認**: `extract --lyrics` の出力 JSON で、ハルシネーション
  テキストが `learned_annotations` の外（`SemanticRPE`/`SVP` 等）に
  一切リークしないことを確認。
- **未検証（環境律速）→ 2026-07-20 WI0-b で解消**: vocals stem 分離経由の実 E2E は
  当時の検証環境に ffmpeg が無く未実施だった（`--lyrics-no-separate` のフルミックス
  経路のみ実推論確認）。2026-07-20 の WI0-b（§9）で `pip install -e ".[lyrics]"`
  環境を再構築し、デフォルトの `htdemucs_ft` 分離込みで実 E2E を実施した
  （instrumental 入力のため精度実測ではなく境界挙動の記録）。歌入り実音源
  （Drive 保管）での分離込み**精度**検証は引き続き実音源律速の follow-up。

## 9. 実推論結果（2026-07-20 WI0-b: identity anchor observe 経由の実 E2E）

WI0-b（[`docs/work_identity_roadmap.md`](work_identity_roadmap.md) WI0 節）の
一環として、`svprpe observe` の lyrics anchor 経由で faster-whisper `small`
（int8 / CPU）+ Demucs `htdemucs_ft`（デフォルトの vocals 分離、
`--lyrics-no-separate` は未使用）の実 E2E を実施した。fixture:
[`examples/arrangement/midnight_signal/observed/wi0b_synth/`](../examples/arrangement/midnight_signal/observed/wi0b_synth/results.md)。

- **入力**: 決定論 synth performer が生成した instrumental な faithful take
  （歌なし。`composition_score.yaml` + `render_faithful.py` から再生成可能、
  sha256 pin あり）
- **`no_speech_prob` は 0.92–0.94 台の高値域を正しく自己申告**（seg 単位実測:
  0.9464 / 0.9404 / 0.9471、証跡 JSON
  [`observed/wi0b_lyrics_extract.json`](../examples/arrangement/midnight_signal/observed/wi0b_synth/observed/wi0b_lyrics_extract.json)
  に pin）— §7/§8 の「honesty 信号」としての機能を instrumental 入力かつ
  vocals 分離込みの経路で追認
- **一方で abstain はしない**: ウェールズ語（`language: "cy"`,
  `language_probability: 0.5489`）のハルシネーション文
  （`"Felly, mae'n gweithio, ..."` の反復）を emit する。英語正典歌詞との
  `overall_similarity` は `0.0126`
- 上記の値は 2026-07-20 の相対パス安定化 re-run（PR #199 Codex P2 対応、
  `commands.md` / `results.md` の Re-run 節）時点のもの。faster-whisper は
  `temperature=0.0` でも同一プロセス外の別実行間で完全決定論ではなく、初回計測時
  （no_speech_prob 0.9309/0.9425/0.9218、`overall_similarity` 0.0056）から数値が
  微変動した。境界挙動の結論（`no_speech_prob` 高値域の自己申告 + abstain しない
  ハルシネーション）自体は両実行で不変
- `svprpe observe` の判定は `adherence_status: not_observed` /
  `determination: deferred` — 計器としては正しい動作（存在しない一致を
  `preserved` と誤判定していない）
- **設計含意（記録のみ・本センサー自体は verdict しない）**: 「転写文字列そのもの
  でなく `no_speech_prob` を下流の abstain ゲートに使う」という方向性が、今回の
  実測で補強された。閾値化・ゲート実装は本ドキュメントの範囲外（WI1/WI4 の
  設計入力として持ち越す）
- **精度実測は引き続き素材律速**: 歌入り + 歌詞 pin 済みの音源が手元にないため、
  今回は境界挙動の記録に留め精度主張はしない（`plan.md` の事前制約どおり）
