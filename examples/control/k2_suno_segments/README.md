# K2-seg Suno — compose プロンプト欄 grip 転移バッチ 1

`examples/control/k2_musicgen_segments/`（K2-seg MusicGen スクリーン、
`docs/musicgen_backend.md` §7.6）で計測した compose プロンプト欄のうち、裁定価値が
最も高い 2 欄を Suno（製品級生成器）へ転移検証する第 1 弾。

- **①本文 `Avoid:` セグメント** — #153 `omit_body_negative` を suno backend へも
  波及させるかの裁定（MusicGen 限定の attractor 是正を Suno へ横展開してよいかは
  §7.6 末尾「機種依存の注意」で未検証と明記されていたキュー）。
- **②`semantic.core`** — MusicGen では物理センサー dead × CLAP センサー生存
  だった意味層ノブ。Suno でも同じ「センサー盲」構図が起きるかを対照する。

`active_rate_target` / `valley_depth_target` / `time_signature` / `structure` は
バッチ 2 へ繰越（Suno 側の数値文字列理解度・長尺構造は別設計が必要）。

K0/K2 系と同じく **fixture-driven**: 音源生成と RPE 抽出は事前に完了済みで、
コミットするのは `suno_rpe_fixture.json`（サンプルごとの数値特徴量、CLAP energy 軸
の生値含む）と `expected_grip.json`（`scripts/measure_grip.py --json` の canonical
出力そのまま）のみ。`fixture → grip` は決定論で
`tests/test_grip.py::test_k2_seg_suno_segments_fixture_snapshot` が回帰固定する。

## 設計 — 3 セル × R=4 = 12 曲

プロンプトは手組みでなく `svprpe compose`（suno/external descriptor）の実出力を
`final_prompt_{calm,calm_avoid,euph}.txt` から verbatim pin した（`plan.yaml` 参照）。
calm セルが `semantic_avoid` ノブと `semantic_core` ノブ両方の low セルを兼ねる。

| セル | 役割 | Style 欄（verbatim） |
|---|---|---|
| `calm` | 共有 low セル | `120 BPM. Instrumental, no vocals. Brightness balanced. Calm atmosphere. synthwave track. A minor. 4/4 time. Wide stereo. Active rate 0.8. Valley depth 0.3.` |
| `calm_avoid` | `semantic_avoid` high | `120 BPM. Instrumental, no vocals. Brightness balanced. Calm atmosphere. synthwave track. A minor. Avoid: bright shimmering sparkling highs. 4/4 time. Wide stereo. Active rate 0.8. Valley depth 0.3.` |
| `euph` | `semantic_core` high | `120 BPM. Instrumental, no vocals. Brightness balanced. Euphoric energetic festival atmosphere. synthwave track. A minor. 4/4 time. Wide stereo. Active rate 0.8. Valley depth 0.3.` |

Exclude Styles 欄は発注書では全セル同一固定を指定していたが、**実際の生成は
Exclude Styles 空**（ユーザー申告）— 測ったのは本文 Avoid の単独効果である
（honesty (c) 参照）。

## 検算照合ゲート（実測）

設計側 Fable の事前計算値と、本 PR で canonical 経路（`scripts/measure_grip.py`
の `analyze_fixture`／`svp_rpe.control.grip_effect_size`、pooled SD 分母
`n1+n2-2`）を用いて算出した値は許容誤差内で一致した。

| 指標 | 設計側事前計算 | canonical 実測 | 差 | 許容 |
|---|---:|---:|---:|---:|
| avoid d（spectral_centroid） | +4.0295 | +4.029548 | 0.00005 | ±0.05 |
| core 物理 d（onset_density） | +0.2309 | +0.230909 | 0.00001 | ±0.02 |
| core CLAP energy d | +2.4468 | +2.446820 | 0.00002 | ±0.05 |

## 判定結果（事前登録規約の機械適用）

### semantic_avoid（sensor: `spectral_centroid`, expected_sign −1）

d = **+4.03**。事前登録規約「d≥+0.8: attractor 確定」に該当 →
**「suno backend へ `omit_body_negative=True` を提案」が判定結果**（本 PR では
コード変更しない。docs 記録まで、コード反映は follow-up PR）。

MusicGen（§7.6, d=+1.10）と方向は一致（本文 `Avoid:` が内容語 attractor として
正方向に働く）だが、**Suno の d=+4.03 は MusicGen の約 3.7 倍強い** — Suno は
Avoid 文の否定を無視するだけでなく、後続の内容語（"bright shimmering sparkling
highs"）への引き寄せが MusicGen よりも顕著。`scripts/measure_grip.py` の汎用
`classify_grip`（expected_sign との符号一致を要求）はこれを機械的に "dead" と
ラベル付けするが、これは K 系列の一般閾値と本注文書の attractor 専用ルーブリック
が別物であるため（`expected_grip.json` の `classification` は前者、上記の
attractor 判定は発注書 verbatim の後者）。

### semantic_core（物理センサー: `onset_density`, expected_sign +1）

d = **+0.23（loose、正方向）**。MusicGen の同ノブ（§7.6, d=−0.70, dead/物理センサー
盲）と対照的に、Suno では物理センサーが既に方向どおり弱く生存している。

### semantic_core（CLAP 第二センサー: energy 軸, expected_sign +1）

d = **+2.45（tight 域、正方向）**。MusicGen の同軸（§7.6, d=+1.90, tight 域）より
更に強い分離。

**機種間対照**: MusicGen = 物理 dead × CLAP tight（「センサー盲」構図）、
Suno = 物理 loose × CLAP tight（物理センサーも方向どおり弱く生きている）。
`semantic.core` の効き方そのものは両機種で正方向に生きているが、物理センサーの
感度が機種依存で異なる。config 反映（`device_profiles/suno.yaml` への
`semantic.core` 追記）は SEM-1 昇格ゲート（#126）準拠で本 PR ではしない
（CLAP のみ生存 → loose 固定から）。

## honesty 事前申告

- **(a) セル共有**: `calm` が `semantic_avoid` ノブと `semantic_core` ノブ両方の
  low セルを兼ねるため、2 つの d 推定は完全独立でない（screening 用途として許容）。
  fixture 内では `calm` の 4 テイクがそれぞれ 2 行（`semantic_avoid`/`semantic_core`
  タグ）として重複登場する（K1 fixture の `brightness`/`brightness_band_ratio`
  重複パターンを踏襲） — `samples` は 16 行だが参照する実音源は 12 本（ユニーク
  `audio_sha256` 12 種）。
- **(b) スキーマ必須欄の定数尾部**: `Brightness balanced.` / `4/4 time.` /
  `Wide stereo.` / `Active rate 0.8.` / `Valley depth 0.3.` は omit 不可欄の固定値で
  全セル共通。特に "Brightness balanced." は centroid センサーと同軸の語であり
  avoid 効果の感度を下げうる（交絡ではないが減衰要因として記録）。
- **(c) Exclude Styles 空で生成（ユーザー申告）**: 発注書は全セル同一の Exclude
  固定を指定していたが、実際の生成は Exclude Styles 欄が空だった。したがって
  **測ったのは本文 Avoid の単独効果**であり、§7.6 の「Exclude Styles チャネルとの
  重複込み」条件は本バッチでも未検証のまま残る。
- **(d)** `grv.secondary=""` の空文字ワークアラウンド使用（`GrvSpec.secondary` は
  スキーマ上必須のためキー省略不可。tags 配列に空要素 1 個が残るが Style 本文には
  無影響、`final_prompt_*.txt` の raw JSON 参照）。
- **(e) 曲長は統制不能**: 18.8〜117.1 秒（Suno 依存、テイク間共変量）。
  `plan.yaml`/`suno_rpe_fixture.json` の `features.duration_sec` に生値を保全。
- **(f) 音源は repo 非コミット**: mp3 実体は session 添付のみ・sha256 で
  provenance を保全（`suno_rpe_fixture.json` の `audio_sha256` 全 12 本インライン）。
- **(g) 生成モデルはユーザーのカスタムモデル**: 本判定（特に avoid=attractor
  d=+4.03）は当該カスタムモデル下の実測であり、Suno 標準モデルへの一般化は未検証。
  ただし MusicGen でも同符号の attractor（d=+1.10, #152）が実測済みで、否定語盲は
  生成器横断の機序である可能性が高い。

## Provenance

- **生成器**: Suno（製品級・確率的・リポジトリ外）。**Suno のユーザーオリジナル・
  カスタムモデルで生成（ユーザー申告 2026-07-09。標準 stock モデルではない）**。
  一般化の留保は honesty (g) 参照。
- **CLAP チェックポイント**: `music_audioset_epoch_15_esc_90.14.pt`、
  sha256 `fae3e9c087f2909c28a09dc31c8dfcdacbc42ba44c70e972b58c1bd1caf6dedd`。
  `docs/musicgen_backend.md` §7.5 / `docs/semantic_sensor_clap.md` の校正ログと
  **同一 pin**（G4 済み cc0-1.0）。`laion_clap` 1.1.7 / `HTSAT-base` /
  `enable_fusion=False` / semantic axes battery v1.1（7 軸）。
- **音源**: session 添付 mp3 12 本。repo にはコミットしない（content-addressed、
  `audio_sha256` を fixture にインライン保全）。

## 副次観測（自動記録・判定対象外）

- **R2-2f live 初発火**: `calm_04` で bpm 候補 `[161.5, 234.91]`
  （比 1.4546）が `bpm_prior_disagreement=true` として実測中に発火した
  （07-08 の同種スクリーンでは非発火だった対）。
- **key は相対調で揺れる**: A minor 指定に対し観測は C major が優勢
  （`calm`: C major/A minor/C major/C major、`calm_avoid`: A major/E major/A minor/
  A minor、`euph`: F major/A minor/C major/A minor）— 指定 A minor の相対長調
  C major への集中は既知の相対調ペア混同（`docs/lyrics_semantic_anchor.md` 系の
  key grip 論点）と一致。
- **`semantic.core` → centroid の交差結合**: `euph` セルの `spectral_centroid`
  （3176.66〜3312.40）が `calm_avoid` セル（2960.73〜3166.85）より高い域に出た
  — `semantic.core` ノブ（意図的操作対象は onset_density/energy）が
  `spectral_centroid`（`semantic_avoid` ノブの計測対象センサー）にも副作用として
  効いている疑い。機種依存の直交性欠如の追加観測として記録（K3 系列の関心事）。

## 関連

- `docs/controllability_poc.md` K2-seg 節（Suno 転移結果表）
- `docs/musicgen_backend.md` §7.6（キュー解消の起点）
