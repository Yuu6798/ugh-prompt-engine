# WI0-b 実推論計測 結果

作成日 (UTC): 2026-07-20T15:41:57Z（`date -u` 実測）

事前登録: [`plan.md`](plan.md)（`Registered at (UTC): 2026-07-20T15:15:24Z`、
本計測の結果を見る前に固定）。再現手順の verbatim ログ: [`commands.md`](commands.md)。
決定論レンダリングスクリプト: [`render_faithful.py`](render_faithful.py)（`svprpe perform`
という CLI サブコマンドは存在しないため、fixture 内 runbook スクリプトとして収載 —
`svp_rpe.perform.FAITHFUL_TAKE` / `perform()` / `wav_bytes()` を直接呼ぶ）。

## 1. 生値

### melody（`examples/arrangement/midnight_signal/observed/wi0b_synth/observed/wi0b_melody_observation.json`）

| 項目 | 値 |
|---|---|
| `pitch_lcs_ratio` | 0.6 |
| `interval_lcs_ratio` | 0.4444 |
| `canonical_length` | 10 |
| `observed_length` | 108 |
| `observed_head` | `[36, 36, 55, 51, 48, 51, 48, 55]`（先頭 8 MIDI 値。うち 36 は C1、48 は C2、51 は Eb2/D#2、55 は G2 — 正典の音域より低い/和音構成音的なピッチが混在） |
| `pitch_sequence_exact_match` | `false` |
| `adherence_status` / `determination` | `not_observed` / `deferred` |

### 決定論確認

- **render**: `render_faithful.py` を別プロセスで 2 回実行（`faithful_take_run1.wav` /
  `faithful_take_run2.wav`）、sha256 が完全一致（2/2）:
  `4d8c83f67c1b2441e09fa84debdc47ec0131c1a13ee1b813b0ef55e874903e90`
  （committed wav は非コミット・このハッシュに pin。決定論再現可能なため wav 自体は
  同梱しない — score + `render_faithful.py` から再生成できる）
- **observe**: 同一 wav に対する `svprpe observe` 2 回実行の出力 report がバイト列
  完全一致（2/2）: sha256 `4d7a53279c2524e15dd0cc983c81d7444217dff65236aa8b8625b300bbaacf25`
  （`observed/wi0b_melody_observation.json` はこの run1 の出力そのもの。run2 との
  byte 一致は上記 sha256 の一致で裏付け済み — diff なし、`commands.md` 参照）

### 環境

- Python 3.11.15
- basic-pitch 0.4.0（`pip install -e ".[pitch]"`、legacy `pretty-midi` wheel
  ビルドの `install_layout` 非互換を `SETUPTOOLS_USE_DISTUTILS=stdlib` で回避。
  詳細: `commands.md`）
- faster-whisper 1.2.1 / ctranslate2 4.8.1（`pip install -e ".[lyrics]"`）
- モデル: faster-whisper `small`（`compute_type: int8`, `device: cpu`, デフォルト
  未指定）、Demucs `htdemucs_ft`（デフォルトの vocal separation、
  `--lyrics-no-separate` は未使用）

## 2. 事前登録ルール適用の判定

`plan.md` の採否ルール:

- `pitch_lcs_ratio >= 0.8` → melody 軸を WI2 v0 採用候補とする
- `pitch_lcs_ratio < 0.8` → v0 から除外する（理由を記録する）

実測 `pitch_lcs_ratio = 0.6 < 0.8` のため、**melody 軸を WI2 v0 から除外する**
（v0 被覆明細では `not_observed` 扱い）。

### 原因判読

正典（`identity/melody_notes.json`, `note-events/0.1`）は 10 音の単旋律列である
のに対し、観測列は `observed_length = 108` と 10 倍以上に膨らんでいる。
`observed_head` を見ると 36（C1）や 48（C2）など正典の音域より低い、伴奏/ベース
帯域や和音構成音とみられるピッチが先頭から混在している。これは basic-pitch が
**ミックス全体をポリフォニックに採譜している**ことを示す — 単旋律の主旋律だけを
抜き出す分離処理を挟んでいない。

つまり `pitch_lcs_ratio = 0.6` が示しているのは basic-pitch というセンサー自体の
精度不足ではなく、**v0 の比較設計の盲点**（全ミックス採譜 vs 単旋律正典を
分離層なしで直接比較している）である。

### 再入条件

旋律分離層（stem 分離 / 旋律パート単独トラックのレンダリングとの比較 / 旋律抽出
アルゴリズム）を導入した上で本計測を再実行し、`pitch_lcs_ratio >= 0.8` を確認する
こと。それまでは melody 軸を WI2 v0 の軸集合に含めない。

## 3. lyrics 境界記録

対象: `observed/wi0b_lyrics_smoke_observation.json`（`svprpe observe` 経由の要約統計）
+ `observed/lyrics_anchor_extracted.json`（同 report の lyrics anchor 抜粋、verbatim）。

instrumental な `faithful_take.wav`（歌なし・melody 計測と同一ファイル）に対して
faster-whisper `small`（int8）で転写した結果:

- **`no_speech_prob` は 0.92–0.94 の高値域を自己申告している**（seg 単位実測:
  0.9309 / 0.9425 / 0.9218 — instrumental な区間であることをモデル自身が正しく
  示唆している）
- **にもかかわらず abstain せず、ウェールズ語（`language: "cy"`,
  `language_probability: 0.5536`）のハルシネーション文を emit する**
  （`"Felly, mae'n gweithio, ..."` の反復。`overall_similarity = 0.0056` と
  英語正典歌詞（`Midnight Signal` 他）とはほぼ無関係）
- `svprpe observe` の判定は `adherence_status: not_observed` /
  `determination: deferred` — **計器としては正しい動作**（存在しない一致を
  `preserved` と誤判定していない）

この挙動は #149 で得られていた「no_speech_prob が honesty 信号として機能する」
という知見を、identity anchor 観測パイプライン経由で実測追認するものである。
一方で「転写された**文字列**でなく `no_speech_prob` を下流のゲート（abstain 判定）
に使うべき」という設計含意は、本 fixture では**記録のみに留め、verdict や閾値は
導入しない**（今後の設計入力として WI1/WI4 に持ち越す）。

精度そのものの実測（歌入り音源での歌詞転写精度）は、歌入り + 歌詞 pin 済みの
音源が手元にないため素材律速で defer する（`plan.md` に明記の事前制約どおり）。

## 関連

- 事前登録: [`plan.md`](plan.md)
- 再現コマンド verbatim ログ: [`commands.md`](commands.md)
- 決定論レンダリングスクリプト: [`render_faithful.py`](render_faithful.py)
- fixture 整合テスト: `tests/test_wi0b_synth_observed_fixture.py`
- ロードマップ: [`docs/work_identity_roadmap.md`](../../../../../docs/work_identity_roadmap.md) WI0 節
