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
- **observe（初回計測時点の履歴的事実）**: 同一 wav に対する `svprpe observe` 2 回
  実行の出力 report がバイト列完全一致（2/2）: sha256
  `4d7a53279c2524e15dd0cc983c81d7444217dff65236aa8b8625b300bbaacf25`（run2 との byte
  一致は上記 sha256 の一致で裏付け済み — diff なし、`commands.md` 参照）。**この値は
  初回計測時**（絶対パス・lyrics extras 未導入段階、`observed/wi0b_melody_observation.json`
  がこの run1 の出力そのものだった時点）**の pin であり、§4 の相対化再実行
  （commit 3c52b22）により superseded**（`generated_artifact.path` が絶対パスから
  相対パスへ変わり、`sensor.available` も変化したため report のバイト列自体が変わった
  — 詳細は §4「observe/extract 新旧 diff」参照）。
- **observe（現 committed 版, PR #199 Codex P2 対応後）**: 現在 committed されている
  `observed/wi0b_melody_observation.json` の実 sha256 は
  `05ff335ec5bf5f618491652072984175ee6f72788e4b298b368c1d3e0abec554`
  （`observed/provenance.yaml` の同名エントリと一致・相互確認済み）。この値は §4 の
  相対化再実行 1 回のみの出力に対するもので、run1/run2 のような複数回 byte 一致の
  再確認はしていない — **melody / harmony anchor の byte 再現性は §4「決定論再確認」
  記載のとおり、初回計測との新旧 diff が完全一致であることで別途裏付けられている**
  （lyrics anchor のみ whisper 非決定性により変動する。§4 参照）。

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

- **`no_speech_prob` は 0.92–0.94 台の高値域を自己申告している**（seg 単位実測:
  0.9464 / 0.9404 / 0.9471 — instrumental な区間であることをモデル自身が正しく
  示唆している。証跡 JSON: [`observed/wi0b_lyrics_extract.json`](observed/wi0b_lyrics_extract.json)
  に pin。値の出所は §4 Re-run 参照）
- **にもかかわらず abstain せず、ウェールズ語（`language: "cy"`,
  `language_probability: 0.5489`）のハルシネーション文を emit する**
  （`"Felly, mae'n gweithio, ..."` の反復。`overall_similarity = 0.0126` と
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

## 4. Re-run（相対パス安定化・PR #199 Codex P2 対応）

作成日 (UTC): 2026-07-20T17:08:23Z（`date -u` 実測）

PR #199 レビュー（Codex P2 2 件）:

1. 「Use relative paths in committed observation reports」— `generated_artifact.path`
   がこの計測を実施したコンテナのローカル `/tmp/.../scratchpad` パスのままだと、
   別 checkout から `docs/cli.md` の手順を再現しても sha256 は一致するが report の
   バイト列は一致せず、byte-reproducibility の主張が成立しない
2. 「Commit the extract evidence behind no_speech claims」— §3 の `no_speech_prob` /
   `language_probability` 主張の根拠 (`svprpe extract --lyrics` 出力) が非 commit
   だった

対応: committed JSON の手編集はせず、決定論を利用して同一 wav（sha256 pin 一致
確認済み）に対し stable なリポジトリ相対パス
（`examples/arrangement/midnight_signal/observed/wi0b_synth/faithful_take.wav`）で
`svprpe observe`（melody / lyrics smoke）と `svprpe extract --lyrics` を再実行し、
その生出力で fixture 3 本 (`observed/wi0b_melody_observation.json` /
`observed/wi0b_lyrics_smoke_observation.json` /
`observed/lyrics_anchor_extracted.json`) を差し替え、新規に
[`observed/wi0b_lyrics_extract.json`](observed/wi0b_lyrics_extract.json) を追加
収載した。手順の verbatim ログは [`commands.md`](commands.md) の Re-run 節。

### 決定論再確認

- **render**: `render_faithful.py` の出力 wav の sha256 が初回計測時の pin
  (`4d8c83f67c1b2441e09fa84debdc47ec0131c1a13ee1b813b0ef55e874903e90`) と一致
  （決定論の主張どおり、Composition Score から byte 一致で再生成できることを
  再確認）
- **observe/extract 新旧 diff**: `melody` / `harmony` anchor は新旧で完全一致
  （§1/§2 の判定根拠データは無傷）。`lyrics` anchor のみ差分があった。差分の内訳:
  - `wi0b_melody_observation.json`: 初回計測時は `pip install -e ".[pitch]"` のみ
    導入済みで lyrics extra 未導入だったため `sensor.available: false`
    だったが、本 re-run の実行環境には両 extras が事前導入済みのため
    `sensor.available: true` に変わり、`match_lyrics` の実測値が新規に入った
    （incidental な環境差で、melody anchor 自体には影響しない）
  - `wi0b_lyrics_smoke_observation.json` / `lyrics_anchor_extracted.json`:
    `no_speech_prob` (0.9309/0.9425/0.9218 → 0.9464/0.9404/0.9471)、
    `language_probability` (0.5536 → 0.5489)、`overall_similarity`
    (0.0056 → 0.0126) が変動した。faster-whisper は `temperature: 0.0` でも
    別プロセスでの再実行間では完全な bit 決定論ではない（CPU スレッド並列化 /
    beam search の非決定性由来と推測）。ハルシネーション文自体も実行ごとに異なる
    （ウェールズ語である点、`no_speech_prob` が高値域である点、abstain しない点
    という**境界挙動の結論は両実行で不変**）
  - path 系フィールド（`generated_artifact.path` / extract の `audio_file`）は
    いずれもリポジトリ相対パスに変わった（今回の主目的）

上記のとおり、`path` フィールド以外にも差分があったため（whisper 非決定性由来の
数値変動）、committed 値は本 re-run の実測値で統一した（隠さず本節と §3 に記録、
テスト `tests/test_wi0b_synth_observed_fixture.py` で新値を pin）。

### 環境（re-run）

初回計測時と同一コンテナ（pitch / lyrics extras とモデルは導入済みのまま）。
再実行に使った一時 wav (`faithful_take.wav`) はコミットしない方針を維持し、検証後
削除した。

## 関連

- 事前登録: [`plan.md`](plan.md)
- 再現コマンド verbatim ログ: [`commands.md`](commands.md)
- 決定論レンダリングスクリプト: [`render_faithful.py`](render_faithful.py)
- 音源 hash 接続の機械可読 provenance サイドカー（PR #199 Codex P2 対応）:
  [`observed/provenance.yaml`](observed/provenance.yaml)（`source_audio_sha256` +
  4 観測ファイルの sha256 pin。melody/lyrics smoke 観測 JSON の
  `generated_artifact.sha256` との機械的突合は `tests/test_wi0b_synth_observed_fixture.py`）
- fixture 整合テスト: `tests/test_wi0b_synth_observed_fixture.py`
- ロードマップ: [`docs/work_identity_roadmap.md`](../../../../../docs/work_identity_roadmap.md) WI0 節
