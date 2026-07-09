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
（CLAP のみ生存 → loose 固定から）。**追記（2026-07-09 follow-up → 撤回、
Codex #164 P2）**: 本 PR 後の follow-up で一度 loose として config 反映したが、
生成器が (g) の通りユーザーのカスタムモデルで標準 stock モデルへの一般化が
未検証のため撤回・保留した。測定値（本節の d 値）自体は事実として保持する。

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

## 追試: Exclude 欄併用（バッチ 1 増補セル calm_avoid_excl、2026-07-09）

`docs/musicgen_backend.md` §7.6 で残っていた「Exclude Styles チャネルとの重複込み
条件は未検証」の留保の解消を試みた追試（結果は交絡により未確定 — honesty (f) 参照）。
バッチ 1 の `calm_avoid`（本文 `Avoid:` のみ）
に対し、Suno の Exclude Styles 欄にも avoid 語彙を追加投入した「重複込み」セル
`calm_avoid_excl`（R=4）を新規計測し、`calm` / `calm_avoid`（いずれもバッチ 1 と
同一の canonical_id・audio_sha256・features を再利用）との 2 本の事前登録比較を
測る。fixture は `excl_rpe_fixture.json`（新規）、expected grip は
`excl_expected_grip.json`（新規）、判定規約は `excl_plan.yaml`（新規）、
回帰スナップショットは `tests/test_grip.py` に追加。

### Provenance

- ユーザーが実音源 mp3 4 本をセッションへ再アップロード（2026-07-09）。
  `svprpe extract`（`--separate` / `--clap-semantic` / `--clap-sections` /
  `--lyrics` なしの物理センサーのみパス、バッチ 1 と同一計測条件）で再抽出し、
  前セッションの事前登録比較の判読値（d=-1.66 / d=+1.64）と完全一致を確認した。
- 音源 mp3 はリポジトリに非同梱（content-addressed、sha256 のみ fixture に
  インライン保全）。

### per-file 表

| canonical_id | file_id | spectral_centroid | bpm | bpm_candidates | フラグ | key |
|---|---|---:|---:|---|---|---|
| `k2seg_suno_excl_01` | file01 | 2641.10 | 123.05 | `[]` | なし | F# minor |
| `k2seg_suno_excl_02` | file03 | 3027.54 | 123.05 | `[]` | なし | F# minor |
| `k2seg_suno_excl_03` | file04 | 2563.44 | 234.91 | `[123.05, 234.91]` | `bpm_octave_ambiguous` | A minor |
| `k2seg_suno_excl_04` | file02 | 2945.18 | 234.91 | `[161.5, 234.91]` | `bpm_octave_ambiguous` + `bpm_prior_disagreement`（R2-2f） | A minor |

### 判定結果（事前登録比較 2 本、canonical 実測）

| 比較 | 対象セル A (low) | 対象セル B (high) | mean A | mean B | grip d | 判定 |
|---|---|---|---:|---:|---:|---|
| 比較1（Exclude 欄チャネルの grip） | `calm_avoid` | `calm_avoid_excl` | 3079.3925 | 2794.315 | **-1.656645** | tight・負方向=期待どおりの値だが**交絡あり・未確定**（honesty (f) 参照） |
| 比較2（正味効果） | `calm` | `calm_avoid_excl` | 2438.0075 | 2794.315 | **+1.642929** | dead・事前登録極性（成功なら負方向）に対し符号反転（正味では打ち消せず、まだ明るい）。ただしこの解釈も**交絡あり・未確定**（honesty (f) 参照）— excl セルと `calm`（バッチ 1 流用）が異なる生成条件を跨ぐため、この符号反転自体が Exclude 欄の効果と generator/model の変化のどちらに由来するか分離できない。`omit_body_negative`（#163）の妥当性は本文 Avoid=attractor のバッチ 1 内実測（d=+4.03、同一モデル）に立つため、本比較の結果には依存しない |

d 値は `scripts/measure_grip.py`（canonical 経路）実測であり、
`scratchpad/excl_extract/summary.json` の事前算出値（-1.6566449476718548 /
1.642929272618472）と一致する。

### honesty 注記

- **(a) 発注書 verbatim 消失**: 元の発注書は本追試のセッション環境消失により
  失われている。本追試の比較設計・判定規約は `docs/controllability_poc.md`
  K2-seg 節に残っていた判読記録からの再登録（`excl_plan.yaml`、2026-07-09）で
  あり、原本の完全な再現ではない。
- **(b) excl_01/excl_02 の take 順は便宜的割当**: フラグ署名（bpm 候補・
  octave 曖昧・prior_disagreement）で確定できるのは excl_03（file04）/
  excl_04（file02）のみ。excl_01/excl_02（file01/file03、いずれもフラグなし・
  F# minor）はどちらが先録りかフラグ署名から再構成不能なため、
  `summary.json` のファイルリスト順による便宜的割当を採用した。
- **(c) octave 曖昧フラグは excl_03/excl_04 の 2 本**: `bpm_octave_ambiguous=true`
  は excl_03（file04）・excl_04（file02）の 2/4。うち R2-2f
  `bpm_prior_disagreement` が実際に発火したのは excl_04（file02）のみ
  （候補比 1.4546、バッチ 1 `calm_04` と同一の候補対 161.5/234.91 が別セルで
  再出現）。
- **(d) key は F#m 2/4・Am 2/4**: excl_01/excl_02 は F# minor、excl_03/excl_04 は
  A minor。指定 key（A minor、バッチ 1 と同じ処方）に対し F# minor という
  新しいドリフト先が観測された（`docs/lyrics_semantic_anchor.md` 系の key grip
  論点と同種、相対調ではない乖離）。
- **(e) CLAP 軸は未計測**: 本追試は物理センサーのみで、`suno_rpe_fixture.json`
  の `clap_semantic_axes` 節に相当するデータは `excl_rpe_fixture.json` に
  含めていない。
- **(f) excl セルと baseline のモデル/フロー同一性が未確認（交絡・confounded、
  Codex #164 P2 レビュー指摘・採用）**: `calm_avoid_excl`（excl セル）はモデル/
  生成フロー同一性が未確認のブラウザフローで生成された（`excl_plan.yaml` の
  `model:` 欄に「未検証」と自ら記録済み）のに対し、比較対象の `calm_avoid`/
  `calm` はバッチ 1（user-custom モデル）からの再利用である。したがって上表の
  比較 1（d=-1.66）・比較 2（d=+1.64）はいずれも excl セルと batch-1 baseline
  という異なりうる生成条件を跨いでおり、観測差分は Exclude Styles 欄の効果と
  generator/model の変化を分離できていない（非隔離）。「Exclude 欄はチャネルと
  して実際に効く」という因果帰属は示唆にとどまり、Exclude-channel 単独 grip の
  確定には同一モデルで excl と baseline を揃えた isolated 追試が必要。なお
  R2-2f 候補対（161.5/234.91）が excl_04 とバッチ 1 `calm_04` で同一だった点
  （honesty (c) 参照）は同一生成器の**弱い**示唆にはなるが、これをもって
  交絡が解消されたとは言えない（過剰解釈しない）。

## 関連

- `docs/controllability_poc.md` K2-seg 節（Suno 転移結果表、追試節）
- `docs/musicgen_backend.md` §7.6（キュー解消の起点、追試は交絡により未確定
  — honesty (f) 参照）
