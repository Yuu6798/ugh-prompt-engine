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

## バッチ 2: structure 欄 grip（2026-07-10、同一バッチ隔離設計の初適用）

`docs/controllability_poc.md` §「K2-seg バッチ 2: structure 欄センサー設計
（2026-07-09 事前設計）」の実測 closeout。compose が送出する structure セクション
記述（`intro: ...; role=...`）が Suno の実生成で「構造」（quiet–loud–quiet の
区間エネルギー・パターン）として実現されるかを、low（`structure: []`）/ high
（3 区間）とも本バッチで新規生成した同一バッチ・同一モデル比較で測る。
AGENTS.md §8「計測比較の交絡隔離規律」（#165）の初適用 — バッチ 1 の `calm` を
low セルに再利用する経済化案は「追試: Exclude 欄併用」節と同型の cross-batch
交絡を招くため設計段階で不採用にし、low/high とも 4 本ずつ新規生成した
（追加生成 8 曲）。

fixture は `structure_plan.yaml`（判定規約・プロンプト verbatim）、
`structure_results_fixture.json`（per-song 計測値 + aggregate + 除外テイク記録）、
`structure_expected_grip.json`（判定結果）、
`structure_batch_metadata_2026-07-10.yaml`（ユーザー申告の生成メタデータ一次記録）。
比較器本体（比例分割 RMS 符号パターン）は
`src/svp_rpe/control/structure_pattern.py` に repo 昇格し、
`tests/test_structure_pattern.py` が fixture→判定の内部整合をスナップショット
固定する。

### 判定結果（事前登録規約の機械適用）

計測条件（canonical）: 全計測は canonical RPE 経路（`svp_rpe.io.audio_loader.load_audio`、
`extract_physical_from_file` と同一の 22050 リサンプル）で実施。初回計測は native
48kHz で行われ match_rate_low_cell=0.75（`low_2_1` = [low,high,low] 1.0）・
novelty_d=0.58554 だったが、SR 是正（Codex #166 P2 第 3 ラウンド採用）に伴い
canonical 値へ全面差し替えた（旧値は git 履歴・scratchpad
`results_structure_2026-07-10.yaml` に保全）。

**主センサー（比例分割 RMS 符号パターン一致率）**: low セル match_rate=**0.666667**、
high セル match_rate=**0.666667**。事前登録済みの経験的ヌル格下げ規則（high セル
match_rate ≤ low セル match_rate）が**境界一致（0.666667 ≤ 0.666667）で発火**し、
機械適用の結果は **dead**（`preregistered_rule_outcome` として記録保全）。
**primary verdict は「測定済みだが confounded・未確定」**（非 canonical、
#164 Exclude 追試と同じ棚）。**判定履歴（3 段・訂正の透明性、
`correction_history`）**: (1) #166 では納品順（high 全件 → low 全件）から
「交互生成順は未実施＝順序が cell と完全共線」と推定し confounded へ格下げ。
(2) 2026-07-10 の生成者本人への追確認で、実際の生成順は **run 単位の厳密交互**
（low run→high run→low run→high run・各 run 2 テイク・補充 2 本は末尾）と証言が
得られ、完全共線推定は誤りと訂正（証言は生成後の追確認・独立証跡〔UI タイム
スタンプ〕未取得の attestation-tier、honesty (3)）。(3) 同日、証言を根拠に
canonicity 復元（dead canonical）を試みたが、Codex レビュー（#168 P2）で
**run 交互は drift-balanced ではない**と指摘され撤回 — 採用テイクの生成位置平均は
low {1,2,6,9}=4.5 vs high {3,7,8,10}=7.0（補充末尾・take 順仮定込み）で high が
系統的に遅く、単調な生成セッションドリフトとの残留交絡はタイムスタンプ証跡なしに
排除不能。交絡は「完全共線」から「残留順序非対称」へ弱まったが verdict は
confounded を維持する。high セル単独への規約適用は 0.666667 → loose だが、
low セル（ヌル）が同値 = chance floor のため単独読みを grip の証拠にはしない。
処方 outro 静音化の実現 **0/4**・完全一致ゼロ（下記「解釈」）は処方非実現の
記述的証拠として併記（`descriptive_evidence`）。**確定には ABBA カウンター
バランス + 生成タイムスタンプ記録を遵守した追試（バッチ 3）が必要**。

**副センサー（novelty 境界数の d）**: low=[5, 4, 7, 4]（mean 5.0）、
high=[3, 6, 7, 7]（mean 5.75）、pooled-SD Cohen's d = **+0.4489**（loose 帯、
expected_sign +1 と同方向）。ただし **曲長交絡 caveat**: セル平均曲長は
low 54.5s vs high 94.7s と大きく異なり、境界数は曲長と連動するため、
この d が structure 欄由来か曲長由来かは事前登録外につき確定不能。

**解釈**: canonical 条件では完全一致ゼロ — 受入 8 本全てが match_rate 2/3。
第 1〜2 区間は生成器デフォルト形状 `[low, high, ...]`（曲は末尾に向けて
エネルギーが上がる傾向を持ち、structure 指定の有無を問わない）で自動一致し、
処方どおりの outro 静音化は high セルで **0/4**。初回 native 計測で唯一の完全一致
に見えた low セル `low_2_1` は knife-edge 計測だった（計器知見参照）。

### 同一バッチ隔離設計の効果（対比・限定付き）

「追試: Exclude 欄併用」節（バッチ 1 増補）は excl セルとバッチ 1 baseline を
跨ぐ cross-batch 交絡により、観測された d が両方とも「測定済みだが confounded」
に格下げされた。本バッチ 2 は low/high を同一日・同一モデルで新規生成したことで
**#164 型の cross-batch 再利用交絡を回避**した。ただしバッチ内の生成順は
run 単位の交互（証言ベース、honesty (3)）であって take 単位の交互でも
ランダム化でもなく、採用テイクの生成位置平均が low 4.5 vs high 7.0 と high 側に
系統的に遅い**残留順序非対称**が残るため、ヌル格下げ比較（cross-cell）は単調
ドリフトと分離不能 — primary verdict は **confounded・未確定**（#164 と同じ棚、
`correction_history` に 3 段の判定履歴を透明に記録）。教訓: 納品順は生成順の
証跡にならない（provenance は生成時に記録すべき）。**次バッチ（バッチ 3）要件:
ABBA カウンターバランス（low→high→high→low の run 順 — 線形ドリフトの位置平均が
厳密に釣り合う）+ 生成タイムスタンプの記録必須（証言でなく証跡）**。補充経路も
事前登録済み（#168 P2 第 2〜5 ラウンド採用で最終化）: スロット保存補充（各 run
直後に曲長 ≥30s を確認し、短尺は次の run へ進む前に同セルを即時再生成）は位置
バランスの**保存でなく乱れの最小化** — 内部 run での除外→即時再生成でも後続
run が非対称にずれる（例: 第 1 high テイク除外で採用位置 low={1,2,8,9} vs
high={4,5,6,7}）。最終規則 4 点: (1) **いかなる補充が発生したバッチも grip
verdict について一律 non-canonical**（計測・記録は完走。high セル読み vs 解析的
chance floor〔直交処方で 1/3 と既知〕の対比は qualified な記述的報告としてのみ
保全 — 解析的 floor はデフォルト形状の出現率安定を仮定しており、セッション
ドリフトが処方パターン側へ寄ると偽の grip を示し得るため、同一バッチ経験的
ヌルの代替にならない）。(2) **canonical verdict への唯一の経路 = 補充ゼロで
完走し、かつ (4) のタイムスタンプ均衡ゲートを通過した ABBA バッチ**。(3) 補充
発生時、位置均衡は事後修復不能のため canonical 判定には**新規バッチの再走**を
要する（restart 規則の事前登録。paired rerun / filler は既存受入テイクを捨てない
限り位置平均が回復しないため不採用維持）。(4) **タイムスタンプ均衡ゲート**:
4 run の生成時刻 t1..t4（ABBA 順）に対し **B = |(t2+t3) − (t1+t4)| / (2·(t4−t1))**
を受領時に計算し、**B ≤ 0.1 で均衡（canonical 資格）/ B > 0.1 で confounded を
pre-mark**。B は**セル平均時刻差のセッション全長比** — low セル平均 (t1+t4)/2 と
high セル平均 (t2+t3)/2 の差をバッチ全長 (t4−t1) で正規化した量（完全等間隔
ABBA なら B=0。初出式は分母の係数 2 が欠落しており #168 R6 で訂正）。序数的
ABBA でも実時刻非対称（キュー遅延等）ならドリフト交絡が残るため、タイムスタンプ
は記録だけでなくゲートにする。閾値 0.1 は「セル平均オフセットを全セッション幅
ドリフトの 1/10 未満に抑える」保守慣行値（K 系列の 0.2/0.8 と同じ規約閾値として
事前登録・閾値は不変）。実務含意: 4 run は間隔を空けず連続実行。
(5) **canonical verdict の保護スコープ（#168 R7）**: ABBA + 均衡ゲートが打ち消す
のは**線形**ドリフトのみ — 凸/凹の曲線ドリフトでは中央の high 2 run と両端の
low 2 run で平均ニュイサンスが異なり、等間隔（B=0）でもゲートを通過する。4 run
設計に曲率同定の自由度はないため、canonical verdict は「線形ドリフト保護下」と
限定して読む。**定常性の記述的注記**を必須併記: low run 1 vs low run 4 の
within-cell 差（match_rate・主要物理値）— 曲線ドリフト実在の一次手がかり
（閾値なし・記述的 annotation）。ミラー二重バッチ（ABBA+BAAB）は人手コスト
比例性から不採用。

### 計器知見（次バッチへの申し送り）

- **3 区間 match_rate の離散粗さ**: 3 区間・処方 `[low, high, low]` は
  取り得る match_rate が `{0, 0.333, 0.667, 1.0}` の 4 値しかなく、0.3–0.7
  loose 帯にほぼ全テイクが機械的に落ちる（本バッチ canonical 計測で 8/8 が 0.667）。
  分解能の粗さがヌル格下げ規則を実質的な主判定にしている。
- **符号量子化は平均近傍で不安定（knife-edge）**: `low_2_1` の第 1 区間線形 RMS は
  3 区間平均の ±0.06% 境界上にあり、リサンプル条件で符号が反転した
  （canonical 22050: 0.15277 vs 平均 0.15268 → high / native 48kHz: 0.153414 vs
  平均 0.153506 → low）。初回 native 計測の「唯一の完全一致」はこの knife-edge の
  産物で、canonical 条件では消滅した。margin（|RMS−mean|/mean）の併記が次バッチの
  計器改善候補。
- 次バッチでは **loud–quiet–loud** 等、生成器デフォルト形状
  （`[low, high, high]`、末尾でエネルギーが上がる傾向）と処方パターンが
  正面から対立する直交処方の方が判別力が高い可能性がある。

### honesty 事前申告

- **(1) モデル/生成条件はユーザー申告**: バッチ 1 honesty (g) と同型。当該
  カスタムモデル下の実測であり、Suno 標準モデルへの一般化は未検証
  （ユーザー申告の一次記録は本ディレクトリの
  `structure_batch_metadata_2026-07-10.yaml` honesty 節参照）。
- **(2) stock モデルへの一般化は未検証**（(1) と表裏）。
- **(3) 生成順は run 単位の厳密交互（証言ベース・当初推定を訂正）**: 納品順が
  high 4 本 → low 4 本だったため、当初は「order_sheet §2-4 の交互生成順は未実施
  ＝順序が cell と完全共線」と推定し記録した（#166）。2026-07-10 の生成者本人への
  追確認で、実際の生成順は run 単位の厳密交互（low run→high run→low run→
  high run・各 run 2 テイク・補充 2 本は末尾）と証言が得られ、**当初推定は誤りと
  訂正**（#166 マージ後訂正）。証言は生成後の追確認であり独立証跡
  （UI タイムスタンプ）は未取得 — model 申告（honesty (1)）と同格の
  attestation-tier として記録する。納品順は生成順の証跡にならないことが教訓。
  **強度格付け**: run 交互は「完全共線」推定を訂正するが drift-balanced では
  ない — 採用テイクの生成位置平均は low 4.5 vs high 7.0 で high が系統的に遅く、
  単調ドリフトとの残留交絡はタイムスタンプ証跡なしに排除不能（#168 P2 採用）。
- **(4) 発注書は同日事前登録**: 発行日 = 生成日 = 2026-07-10。規約は生成前に
  固定されている。
- **(5) novelty d は曲長交絡で確定不能**: 上記「判定結果」節の caveat 参照。
- **(6) 初回計測は native 48kHz（SR 是正で canonical 値へ差し替え済み）**: 初回
  計測は native SR のまま行われ、`low_2_1` の第 1 区間が knife-edge（線形 RMS が
  3 区間平均の ±0.06% 境界上）だったため主センサー値がリサンプル条件で反転した
  （0.75/1.0 → 0.666667/0.666667。機械適用のヌル格下げ発火は不変・計器知見参照）。
  現 fixture は canonical 22050 計測値のみを収載し、旧値は git 履歴と scratchpad
  に保全。
- **除外テイク**: `high_1_1`（18.5s）/ `low_2`（29.0s）が事前登録の <30s
  除外規則に該当し除外。同日・同モデルで補充テイク `high_2_2`（94.1s）/
  `low_2_2`（37.3s）を生成し R=4 を充足した（`structure_results_fixture.json`
  の `excluded` 節に sha256 とともに記録）。

## バッチ 3: structure 欄 grip 確定追試（2026-07-10、ABBA カウンターバランス — dead・canonical 保留）

バッチ 2（#166/#168）で「測定済みだが confounded・未確定」に据え置かれた
structure 欄 grip を、順序交絡を排した設計で確定させる追試。バッチ 2 レビューで
確定した設計変更 3 点を適用: (1) **ABBA カウンターバランス生成順**
（run1 low → run2 high → run3 high → run4 low・単純交互では片セルの平均生成
時刻が系統的に遅れるため不十分と #168 レビューで確定）、(2) **処方の直交化**
（quiet–loud–quiet → **loud–quiet–loud**・生成器デフォルト形状 `[low,high,high]`
との一致が 2/3 → 1/3 に落ち判別ヘッドルームが 2 倍）、(3) **margin 併記**
（knife-edge 検出、#167 で計器凍結済み）。

fixture は `structure3_plan.yaml`（判定規約・プロンプト verbatim・canonical
条件の 4 点定義）、`structure3_results_fixture.json`（per-song 計測値 + margin +
aggregate）、`structure3_expected_grip.json`（判定結果・canonical 条件充足記録）、
`structure3_batch_metadata_2026-07-10.yaml`（scratchpad 一次記録をそのまま収載
— #166 P2-3 の教訓）。

### 判定結果（dead・canonical 保留 — 時刻粒度により均衡ゲート検証不能・復元条件付き）

**主センサー**: high セル match_rate=**0.333333**、low セル match_rate=**0.416667**。
事前登録済みの経験的ヌル格下げ規則（high セル match_rate ≤ low セル match_rate）が
**発火**（0.333333 ≤ 0.416667）し、機械適用の結果は **dead**
（`preregistered_rule_outcome`）。**primary_verdict は dead・verdict_canonical:
false（canonical 保留）** ── canonical 4 条件のうち 3 条件は充足したが、均衡
ゲートが時刻粒度により検証不能（下記 4・Codex #169 P2 採用）。

**canonical 条件の充足記録（3 点充足・1 点検証不能）**:

1. **ABBA 順（充足）**: run1 low → run2 high → run3 high → run4 low。端末
   ダウンロード一覧スクリーンショット（1000004931.png・順序一致）+ 各 run の
   ダウンロード時刻（生成直後ダウンロード規約による代理証跡）。
2. **補充ゼロ（充足）**: 全 4 run・8 テイクが 30 秒以上（52.24s–148.2s）で
   除外・補充なし。
3. **タイムスタンプ記録（充足）**: run1 23:31 / run2 23:34 / run3 23:37 /
   run4 23:39（JST）。
4. **均衡ゲート（検証不能 → 未充足扱い・Codex #169 P2 採用）**:
   B = |(t2+t3) − (t1+t4)| / (2·(t4−t1)) = |9−8| / 16 = **0.0625（点推定）** は
   閾値 0.1 内だが、唯一の時刻証跡が**分単位**のダウンロード時刻であるため、
   粒度誤差の最悪ケース（各時刻 ±0.5 分・順序制約込みの joint 最悪化で
   B = 3/16 = 0.1875 ≈ 0.19、分子/分母を独立に最悪化した保守上界で
   3/14 ≈ 0.21）が閾値を超え、**B ≤ 0.1 の充足を現証跡では検証できない**。
   点推定の額面値通過を canonical 根拠にしたのは誤り（当初判定を撤回）──
   ゲート未充足扱いとし、verdict_canonical は false。
   **canonical 復元条件**: B ≤ 0.1 を検証可能にする精度の時刻証跡（秒単位、
   または分解能 ≤ バッチ全長/40 ≈ 12 秒）の追加提出。他 3 条件は充足済みの
   ため、**時刻証跡のみで復元可**（音源の再生成は不要。
   `structure3_expected_grip.json` の `canonical_blocked_by` /
   `canonical_restoration_condition` 参照）。

**解析的 floor 照合**: high セル観測値 0.333333 は処方 `[high,low,high]` と
デフォルト形状 `[low,high,high]` の解析的一致率（chance floor = 1/3）と
**正確に一致** ── 直交処方の high セルが解析的ヌル以上の grip を示さなかった
ことの直接証拠。low セル観測値 0.416667 も order_sheet §4 の事前予測
（≈0.33）と整合方向。

**記述的核心**: 処方「loud イントロ」の実現は high セル **0/4**（全 4 曲とも
第 1 区間 margin が負）。8 本中 **5 本**が生成器デフォルト形状
`[low, high, high]` と完全一致し、残り 3 本もデフォルト形状に近い変種
（第 1 区間はいずれも low）── 発注書ドラフト時の見積り「6 本」は fixture
再集計により 5 本へ訂正した。knife_edge フラグは全 8 曲・全区間でゼロ
（最小 |margin| = 0.006783 > KNIFE_EDGE_MARGIN 0.005）。

**副センサー（novelty 境界数の d）**: low=[7,4,4,7]（mean 5.5）、
high=[7,5,7,7]（mean 6.5）、pooled-SD Cohen's d = **+0.707107**（loose 帯・
tight 閾値 0.8 未満、expected_sign +1 と同方向）。**曲長交絡は実質不在**:
セル平均曲長 low 80.5s vs high 77.77s とほぼ等値（バッチ 2 の low 54.5s vs
high 94.7s と対照的）── 発注書ドラフト時の見積り「77.75s」は fixture
再集計により 77.77s へ訂正した。ただし d は tight 閾値未満のため loose 止まりで
あり、確定的な grip 証拠としての昇格はしない（記述的・確定なし）。

**定常性の記述的注記（規則 5・線形ドリフト保護スコープの限定）**: low run1
（`low_1_2` / `low_1_3`、match_rate ともに 0.333333）vs low run4
（`low_2_3` / `low_2_4`、match_rate 0.666667 / 0.333333）── RMS 水準含め
顕著なドリフトの兆候はない。4 run 設計は曲率同定の自由度を持たないため、
これは示唆に留まる（閾値なし）。

**バッチ 2 との関係**: 同方向 ── バッチ 2 で「測定済みだが confounded・未確定」
だった dead 判定が、順序交絡を排した本バッチで**保留付き dead** として再現された。
**バッチ 2 の primary_verdict（confounded・非 canonical）は本バッチによって
遡って変更しない**（`structure_expected_grip.json` は不変）。本バッチ自身も
時刻粒度により canonical 保留のため、structure grip の canonical 確定は
時刻証跡の追加提出（復元条件）または次バッチ待ち。

**config 反映**: `device_profiles/suno.yaml` への反映は行わない（dead ノブは
config 非掲載の既定方針どおり、`config_reflected: false`）。

### honesty 事前申告

- **モデル/生成条件はユーザー申告**（バッチ 1 honesty (g) / バッチ 2 honesty (1)
  と同型）。当該カスタムモデル限定の実測であり Suno 標準モデルへの一般化は
  未検証。
- **タイムスタンプはダウンロード時刻の代理**: 各 run 生成直後にダウンロードする
  運用規約により、生成時刻そのものでなくダウンロード時刻を代理証跡として用いる
  （`structure3_batch_metadata_2026-07-10.yaml` provenance_notes 参照）。独立の
  UI タイムスタンプ取得ではなく、model 申告と同格の attestation-tier。
- **均衡ゲートは時刻粒度により検証不能（Codex #169 P2 採用・当初判定を撤回）**:
  B の点推定 0.0625 は閾値 0.1 内だが、タイムスタンプが分単位粒度のため最悪
  ケース（joint 0.19 / 独立上界 ~0.21）が閾値を超え、B ≤ 0.1 の充足を現証跡では
  検証できない。当初 PR #169 は額面値判定（PASS・caveat 併記）で
  verdict_canonical: true としていたが、レビュー指摘の採用によりゲート未充足
  扱い・canonical 保留（false）へ改訂した。復元条件は「判定結果」節 4 参照。
- **stock モデルへの一般化は未検証**（上記モデル申告と表裏）。
- **記述的数値 2 点の訂正**: 発注書ドラフト時点の見積り「デフォルト形状一致
  8 本中 6 本」「high セル平均曲長 77.75s」は、fixture 収載時の再集計で
  それぞれ「5 本」「77.77s」に訂正した（`structure3_results_fixture.json` /
  `structure3_expected_grip.json` の実測値が正）。

## 関連

- `docs/controllability_poc.md` K2-seg 節（Suno 転移結果表、バッチ 2 / バッチ 3
  実測結果小節）
- `docs/musicgen_backend.md` §7.6（キュー解消の起点、追試は交絡により未確定
  — honesty (f) 参照）
- `docs/control_profile.md`（structure 欄の測定記録と config 非反映の方針）
