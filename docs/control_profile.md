# control_profile — 楽譜が「効くチャネル」を知る（PR1）

`CompositionScore.control_profile` は、**どの生成器でどの物理フィールドが効くか**
（`grip_class`）を楽譜自身に持たせるための optional ブロック。これにより楽譜は
**自己記述的**になり、「AI が演奏できる楽譜」の核心的差別化点となる。

本ドキュメントは PR1（スキーマ + 初期データ）と PR1.5（`control_profile` を
`ExternalPromptAdapter` の優先度に配線する実コンパイル）の範囲を記す。時系列条件付け
への実コンパイル（M5）は引き続き forward work。
ロードマップ全体は [`ai_performer_score_roadmap.md`](ai_performer_score_roadmap.md) を参照。

## スキーマ

```yaml
control_profile:                      # key = 生成器名（"suno" / "musicgen" / ...）
  suno:                               # GeneratorProfile = dict[field_name -> ControlGrip]
    bpm:                              # field_name は PhysicalLayer のキー
      grip_class: tight              # tight=保証 / loose=助言 / dead=無効（必須）
      grip: 1.61                     # 効果量 d（K 系列 measure_grip 出力・optional）
      sensor: bpm                    # 観測センサー名（optional）
      evidence: examples/control/k2/expected_grip.json   # 出所参照（optional）
    brightness:
      grip_class: tight
      grip: 0.86
      sensor: spectral_centroid
      evidence: examples/control/k2/expected_grip.json
```

- **`grip_class`**（必須）: `tight`（保証チャネル）/ `loose`（助言・弱い grip）/
  `dead`（無効＝センサー盲 or ツマミ死）。
- **`grip` / `sensor` / `evidence`**（optional）: 値が無ければ serialize から除外される
  （リポジトリの「空なら非 serialize」方針を踏襲）。

## 検証ルール（fixity との差異）

`control_profile` は `fixity` ブロックのパターンを踏襲するが、**網羅必須は引き継がない**。

| ルール | fixity | control_profile |
|---|---|---|
| 未知 field キーを fail-fast | ○ | ○ |
| 全 physical フィールド網羅必須 | ○ | **×（疎を許容）** |
| 不正な値（grip_class）を fail-fast | ○（Literal）| ○（Literal）|

疎を許容するのは、K2（#117）由来の Suno プロファイルが `bpm` / `brightness` の
**2 フィールドのみ**であり、網羅必須だと初期データ自体が弾かれるため。未プロファイルの
フィールドは「未計測＝助言扱い」として正当で、PR1.5 で `rendering.priority` フォールバックに回る。

## フィールド → backend 条件付けチャネル対応表

楽譜の各フィールドが、各生成器でどの条件付けチャネルに乗るかの対応表。
実コンパイラ（チャネルへの実際の流し込み）は PR1.5（テキストプロンプト）以降の範囲で、
本表は設計の見取り図。

| score field | Suno（テキスト生成器）| MusicGen（条件付け生成器）| 備考 |
|---|---|---|---|
| `bpm` | Style 欄 "NNN BPM" | text + melody tempo | K2 で tight 実証 |
| `key` | Style 欄 "Key minor/major" | text | grip 未実証（loose 扱い）|
| `time_signature` | Style 欄 | text | grip 未実証 |
| `brightness` | Style 欄音色語（dark/bright）| text + 音色条件 | K2 で tight 実証 |
| `stereo_width` | Style 欄（弱）| 後処理 | センサー/grip 未実証 |
| `active_rate_target` | 構造記述（弱）| 密度条件（M5）| grip 未実証 |
| `valley_depth_target` | 構造記述（弱）| 制御曲線（M5）| grip 未実証 |

> **正直な限界**: 2026-06 時点で Suno の tight は `bpm` / `brightness` の 2 本のみ。
> 残りは grip 未実証で、PR1.5 では助言（fallback）に格下げされる。芯は K 系列 grip の
> 拡張で後から厚くなり、その都度コード変更なしに助言→保証へ昇格する。

## PR1.5: control_profile-aware compile

`ExternalPromptAdapter` が `control_profile` を見て、保証チャネルを守って演奏プロンプトへ
コンパイルする（`compose/prompt_renderer.py`）。

**backend selector**: `rendering.target_backend` → `BackendDescriptor`（`resolve_backend_descriptor`）。
`external` / `suno` は `profile_key="suno"` を引き、`control_profile.suno` を決定論的に
解決する。これにより `target_backend: external` の Score が「未プロファイルの external
render」へ黙って落ちない。未知 backend は素の descriptor（`profile_key=backend 名`）へ
フォールバック。Suno 固有の制約（Style 字数・Exclude 欄＝`negative_channel`）は薄い
descriptor に隔離し、アダプタ core から生成器直書きを排除する。

**backend 別ルーティング（K2-seg 後始末、#152 フォローアップ、2026-07-06。#162 で suno へ波及、
2026-07-09）**: `BackendDescriptor` に `omit_body_negative: bool` を追加し、musicgen / suno
backend で True。K2-seg 実測（本文 "Avoid: X" が X の attractor になる・musicgen centroid
d=+1.10 #152・suno centroid d=+4.03 #162、事前登録規約の attractor 確定閾値 d>=+0.8 該当）
を受けて、両 backend とも本文 `semantic.avoid` セグメントの送出自体を止める（`negative_tags`
は従来どおり保持し楽譜の意図は失わない）。分岐は descriptor 参照のみで backend 名の直書きは
増やさない。`external` は不変 — 実測は Suno 生成そのものに対するものであり、汎用 external
へは実測なき横展開をしない（#153 と同じ規律。Suno 本文 Avoid の Exclude Styles チャネルとの
重複込みの効きは 2026-07-09 の Exclude 欄併用追試で計測を試みたが、excl セルがモデル/フロー
未確認のブラウザ生成・baseline がバッチ 1 流用で交絡しており**未確定** — docs/musicgen_backend.md
§7.6 の留保は未解消のまま・後述の「negative チャネル欠落経路での avoid の扱い」参照）。

**negative チャネル欠落経路での avoid の扱い**: 生成経路によっては Exclude 欄
（negative チャネル）自体が UI に存在しない（実測例: Suno カスタムモデルの生成フロー、
2026-07-09 ユーザー申告）——との申告が当初あったが、同日中のブラウザ版 UI 確認で
**Suno には Exclude Styles 欄が存在する**ことが判明し、上記申告は経路/表示の
見落としだったと訂正する。したがって Suno
では `semantic.avoid` の配送先は Exclude 欄で確保される。Exclude 欄併用追試
（`calm_avoid_excl` vs `calm_avoid`: grip d=-1.66・tight 域）は Exclude 欄が
チャネルとして効くことを**示唆**するが、excl セルと baseline の生成モデル/フロー
同一性が未確認で交絡しており確定はしていない（[`controllability_poc.md`](controllability_poc.md)
「K2-seg Exclude 欄併用追試（2026-07-09・バッチ 1 増補セル）」節の交絡 caveat 参照）。ただし
Exclude 併用でも本文 Avoid の attractor（#162: d=+4.03）を正味では打ち消しきれない
（同追試の正味効果比較: d=+1.64）ため、`omit_body_negative`（#163）で本文 Avoid の
送出自体を止める方針が優先される。

以下は **negative チャネルが本当に存在しない経路での一般 fallback 指針**として
スコープを狭めて残す: そのような経路では `semantic.avoid` は配送先ゼロ — 本文
Avoid は attractor（#162: d=+4.03）・Exclude 欄は不在 — であり、除外要求は
**肯定形へのリフレーズでのみ表現可能**（例: 「bright highs を避ける」でなく
"dark warm muffled tone, soft rounded highs" と書く。K2 brightness dark セルが
実測で効いた書式）。

**フィールド粒度の drop accounting**: 旧 `physical.optional` 束（brightness / stereo_width /
active_rate_target / valley_depth_target を 1 トークンに束ねていた）をフィールド粒度の
独立した文へ分解。トークン ID は `PhysicalLayer.model_fields` 正式名に一致し、`dropped_elements`
もフィールド単位で返る。これにより tight な brightness を残しつつ loose な valley_depth_target
を落とす**独立した keep/drop** が検証可能になる。

**grip_class 駆動の優先度**: control_profile が覆う物理フィールドを 3 ティアで順位付け。

| tier | 対象 | 描画 | drop |
|---|---|---|---|
| tight | 保証チャネル | 芯として**先頭へ昇格** | 最後まで残す |
| (fallback) | 未プロファイル（semantic.* / structure / 未掲載物理フィールド）| `rendering.priority` 順 | priority 低優先から |
| loose / dead | 助言チャネル | 末尾 | **真っ先に落とす** |

**priority エイリアスマップ**: 既存 `rendering.priority` は旧セグメントトークン
（`physical.bpm` / `physical.key` / `physical.optional`）で記述されている。分割後も drop 順位
契約を保つため、`physical.bpm`→`bpm` / `physical.key`→`key` / `physical.optional`→
`[brightness, stereo_width, active_rate_target, valley_depth_target]`（順序保存展開）で
field トークンへ正規化する。

**正直な限界**: 現状 Suno の tight は bpm/brightness の 2 本のみ。PR1.5 はこの薄さを正直に
可視化するだけで、フィールドを grip させはしない。芯は K 系列 grip の拡張で後から厚くなり、
その都度コード変更なしに助言→保証へ昇格する。

## PR2: 楽譜準拠テスト（score-adherence）

`control_profile` が tight と宣言したフィールドが実際に守られたかを判定する計器
（`src/svp_rpe/roundtrip/adherence.py`、CLI `svprpe score-adherence`）。tight 宣言を
2 つの側面で照合する:

1. **コンパイル側**: `ExternalPromptAdapter` が tight フィールドをプロンプトへ保持したか
   （PR1.5 の保証＝tight は drop されない）を `dropped_elements` で検証 → `compiled_kept`。
2. **演奏側**: 楽譜→演奏→抽出→draft のラウンドトリップ 4 値診断が `preserved` か
   → `roundtrip_diagnosis` / `preserved`。

生成側 backend は PR1.5 の backend selector を共有して解決し、`RoundtripReport` は決定論
performer 由来でも実 Suno corpus take 由来でもよい（path 非依存）。`RoundtripReport` と
同様に**計器であって verdict ではない**＝グローバルな pass/fail は出さず、フィールド単位の
保持/非保持と件数（`compiled_kept_count` / `preserved_count` / `total_tight`）のみを返す。

> **正直な限界**: 決定論 performer の roundtrip は実コンパイル経路（楽譜→アダプタ→Suno→
> extract）の自動代理であり、実 Suno 経路の準拠判定は corpus take（人手生成）を渡して同じ
> 判定器にかける。学習センサー（CLAP）による意味層読解は **PR2b closeout 済**:
> PR2b-1 で隔離配線（fixture 駆動 similarity、実推論なし）、PR2b-2 で実推論・実 fixture
> 採取、#131/#132 で相互検証①（CLAP × mid_ratio）、#138 で MusicGen バッチへの拡張まで
> 実運用済み（`docs/learned_models_policy.md` §3.1 / §9、`docs/musicgen_backend.md` §7.5）。

## 初期データの出所

`examples/composition/midnight_signal/composition_score.yaml` の
`control_profile.suno` は K2（#117）の `examples/control/k2/expected_grip.json` から:

| knob | sensor | grip d | class |
|---|---|---:|---|
| bpm | bpm | 1.61 | tight |
| brightness | spectral_centroid | 0.86 | tight |

詳細・留保（BPM prior アトラクタ／brightness の非対称）は
[`controllability_poc.md`](controllability_poc.md) §5.2 を参照。

## SEM-1: 意味層の制御チャネル（`lyrics_presence`）

`control_profile` が自己記述するノブは PhysicalLayer フィールドに限らない。楽譜が
「歌詞の有無」という**意味層**の制御チャネルを持てるよう、`SemanticLayer.lyrics_presence`
（`"present"` / `"absent"` / 未指定）を追加した。

- **DD-1（配置）**: `PhysicalLayer` ではなく `SemanticLayer` に置く。`fixity` は
  PhysicalLayer の全網羅制約を持つため（本ドキュメント上表参照）、意味層フィールドを
  fixity 対象に混ぜると網羅制約が壊れる。`lyrics_presence` は fixity キーとして
  受理されない（従来どおり `ValueError`）。
- **DD-2（許可キー）**: `SEMANTIC_CONTROL_FIELDS` を `compose/models.py` に定義し、
  `control_profile` の許可キー集合を `set(PhysicalLayer.model_fields) |
  SEMANTIC_CONTROL_FIELDS` へ拡張。未知キー fail-fast と疎許容（網羅必須なし）は不変。
  当初は `{"lyrics_presence"}` のみだったが、K2-seg（2026-07-05）で `_segments_for` の
  実描画トークンと一致する `semantic.avoid` / `semantic.core`（ドット表記のまま）を
  追加し、`{"lyrics_presence", "semantic.avoid", "semantic.core"}` へ拡張した
  （下記「デバイスプロファイル」節参照）。
- **DD-3（初期データ）**: `examples/composition/midnight_signal/composition_score.yaml`
  の `control_profile.suno.lyrics_presence` は当初 `grip_class: loose`（`sensor: mid_ratio`,
  `grip` は未算出のため省略）で投入した。出所は
  [`lyrics_semantic_anchor.md`](lyrics_semantic_anchor.md) n=3 追試と
  `examples/real_audio_validation/lyrics_arrange_demo_2026-07-01.yaml`（当時の証拠水準
  では tight を主張しない＝下記昇格ゲート未達）。**2026-07-08 更新**: DD-4 の両条件
  充足を受けて `grip_class: tight` / `sensor: clap_vocal_contrast` へ昇格済み
  （`config/device_profiles/suno.yaml` の `control_defaults` もパッケージ同梱コピー込みで
  同期。判定の経緯は下記 DD-4 の 2026-07-08 判定を参照）。
- **コンパイル**: `ExternalPromptAdapter` は `lyrics_presence` 設定時のみセグメントを
  描画する（`present`→"With vocals."、`absent`→"Instrumental, no vocals."）。`absent`
  のとき `GeneratedPrompt.tags` 末尾へ `"instrumental"` を追加する。`_rank_key_factory`
  は物理フィールドと同じ grip_class 駆動の 3 ティア（tight/fallback/loose・dead）で
  `lyrics_presence` トークンを扱う。
- **DD-4（tight 昇格ゲート・doc 記載のみ、未実装）**: 以下の両方を満たすまで
  `lyrics_presence` を tight に昇格しない。
  1. 各ジャンルで instrumental の alt（別取り）込み **n≥2×2 セル**で効果が
     再生成ノイズを上回ること。
  2. K3 直交性（[`controllability_poc.md`](controllability_poc.md) §5.3）の語彙で
     ジャンル干渉と分離できること（`mid_ratio` はジャンルでも動く: Rock 0.208–0.245 /
     EDM 0.217–0.226 — [`lyrics_semantic_anchor.md`](lyrics_semantic_anchor.md) 結論2）。

  **2026-07-08 判定（CLAP ③ closeout）**: 条件 1 は n≥2×2 セル（EDM/Rock 各
  instrumental alt 込み、
  [`lyrics_semantic_anchor.md`](lyrics_semantic_anchor.md) 「2026-07-08 対称ブロック」節）
  で再検証し、**mid_ratio では両ジャンルとも棄却**（EDM 効果 0.010 < ノイズ 0.025、
  Rock 効果 0.017 < ノイズ 0.019）が確定した一方、**CLAP vocal contrast では両ジャンル
  とも充足**（EDM 0.136 > 0.064 = 2.1×、Rock 0.155 > 0.083 = 1.9×）。よって条件 1 の
  ゲートセンサー定義を `mid_ratio` から **CLAP vocal contrast**
  （`svprpe extract --clap-semantic` の vocal_presence 軸 / #131 と同じ
  `contrast_fit` 方式、[`semantic_sensor_clap.md`](semantic_sensor_clap.md)）へ改訂する
  設計判断をここに明文化する。ただし **tight 昇格の config 反映は未実施** — 条件 2
  （K3 直交性の formal 評価）と、grip_class 変更がコンパイル優先順位（`_rank_key_factory`
  の 3 ティア）に波及するための回帰確認が follow-up として残る。
  `config/device_profiles/suno.yaml` および
  `examples/composition/midnight_signal/composition_score.yaml` の
  `control_profile.suno.lyrics_presence`（当時 `grip_class: loose`）は上記判定の
  時点では変更しなかった。

  **2026-07-08 条件 2 formal 判定（K3 干渉分離）**: 条件 2 は以下の規約を計算前に
  固定した（**事前登録**。グルーピング・丸めの後決めを排除する）:

  - **ジャンル干渉** = 同一歌詞条件内の |mean(EDM セル) − mean(Rock セル)|
  - **ノイズ** = その条件内の max(EDM セル内スプレッド, Rock セル内スプレッド)
  - **分離成立** = 両条件で「干渉 < ノイズ」かつ「歌詞効果（条件 1 の判定値）> 干渉」

  データは `examples/learned/clap/lyrics_vocal_contrast_v2_fixture.json`（CLAP vocal
  contrast `contrast_fit`、8 サンプル）と
  `examples/real_audio_validation/lyrics_symmetric_block_2026-07-08.yaml`（対照
  `mid_ratio`）。数値は厳密計算し最終表示のみ 3–4 桁へ丸める。計算値:

  | センサー | 歌詞条件 | 干渉 | ノイズ | 干渉<ノイズ | 歌詞効果 (EDM/Rock) | 効果>干渉（倍率） |
  |---|---|---:|---:|---|---|---|
  | CLAP vocal contrast | present | 0.0142 | 0.0320 | ✓ | 0.136 / 0.155 | ✓（9.6× / 10.9×） |
  | CLAP vocal contrast | absent | 0.0343 | 0.0826 | ✓ | 0.136 / 0.155 | ✓（4.0× / 4.5×） |
  | `mid_ratio`（対照） | present | 0.0195 | — | — | 0.010 / 0.017 | ✗（効果 ≤ 干渉） |

  CLAP vocal contrast は present / absent の両条件で「干渉 < ノイズ」かつ
  「歌詞効果 > 干渉」を満たし、**条件 2 の分離成立**。対照の `mid_ratio` は present
  条件のジャンル干渉（≈0.0195）が歌詞効果（EDM 0.010 / Rock 0.017）以上で分離不成立
  — 条件 1 での `mid_ratio` 棄却が条件 2 でも裏付けられた。

  **条件 1・条件 2 の両充足につき `lyrics_presence` の tight 昇格を実施**（2026-07-08、
  本 PR で config 反映）: `config/device_profiles/suno.yaml`（+ `src/svp_rpe/config/`
  同梱コピー）の `control_defaults.lyrics_presence` と
  `examples/composition/midnight_signal/composition_score.yaml` の
  `control_profile.suno.lyrics_presence` を `grip_class: tight` /
  `sensor: clap_vocal_contrast` へ更新した（`grip` は効果量 d 未算出のため引き続き
  省略）。grip_class 変更が `_rank_key_factory` の 3 ティア優先順位（tight 先頭昇格）
  へ波及する回帰は全件 pytest で確認した。

## デバイスプロファイル（PR3 後半）

`control_profile` は「楽譜が効くチャネルを知る」ための自己記述だが、K3-2a
（[`controllability_poc.md`](controllability_poc.md) §5.4）で
**bpm→spectral_centroid の非対角クロス結合が生成器で符号反転する**ことが実測され、
「機種ごとの癖は普遍則ではない」という実証的動機が生まれた。`compose/device_profile.py`
の `DeviceProfile`（`config/device_profiles/<generator>.yaml`）は、K2 grip（tight
既定値）/ K3-2a 非対角クロス効果（未解決記録）/ genre calibration バイアス（方向所見）を
1 生成器 = 1 YAML にまとめ、コンパイルへ 2 経路で接続する。

### スキーマ

```yaml
schema_version: "1.0"
generator: "suno"
control_defaults:                # GeneratorProfile と同型（field -> ControlGrip）
  bpm: {grip_class: tight, grip: 1.61, sensor: bpm, evidence: ...}
  brightness: {grip_class: tight, grip: 0.86, sensor: spectral_centroid, evidence: ...}
  lyrics_presence: {grip_class: loose, sensor: mid_ratio, evidence: ...}
knob_quirks:                     # advisory 発火条件（発火してもプロンプト本文は変えない）
  - field: bpm
    status: observed
    applies_below: 100
    advisory: "Suno は低 bpm 指定を prior アトラクタへ引き上げる実績（K2）。..."
    description: "低 bpm の prior アトラクタ圧縮"
    evidence: "docs/controllability_poc.md §5.2"
cross_couplings:                 # K3-2a §5.4 の非対角記録。advisory は出さない
  - {knob: bpm, sensor: spectral_centroid, effect: 2.33, status: unresolved, evidence: ...}
spectral_biases:                 # genre calibration（Phase C）由来の方向所見
  - {name: over_brightening, description: "...", direction: "...", status: directional, evidence: ...}
notes: "生成器バイアスの方向・量はジャンルで割れるため単一補正係数化はしない。"
```

- **`status`**: `observed`（K2/K3 grip で直接実証）/ `directional`（方向は一定だが量は
  ジャンル依存・genre calib）/ `unresolved`（記録のみで解釈が確定していない・K3-2a 非対角）。
- **`load_device_profile(generator)`**: `config_loader` の local→packaged パターンで
  `device_profiles/<generator>.yaml` を読む。ファイルが無ければ `None`（フォールバック
  チェーン。未知生成器は正当に「プロファイル無し」）。スキーマ違反は fail-fast。

### merge semantics（score 宣言が device 既定に勝つ）

`ExternalPromptAdapter.render` は backend descriptor 解決後に `load_device_profile` を呼び、
`device.control_defaults` を `dict()` コピーしてから `score.control_profile[profile_key]`
で上書きする。**score が同一フィールドを宣言していれば常に score が勝つ**。score が
未宣言のフィールド（または `control_profile` ブロック自体が無い score）は device defaults
だけで tight/loose/dead ティアが決まる。K2 由来の suno device defaults は `bpm` /
`brightness` が tight のため、**`control_profile` を書かない score でも suno backend では
この 2 フィールドが芯として先頭に昇格する**（PR1.5 の 3 ティア優先度は不変、詳細は上記
「PR1.5」節）。第二機種プロファイルは `device_profiles/musicgen.yaml`
（MusicGen PR B の実測 K2 型 grip 由来 — [`musicgen_backend.md`](musicgen_backend.md)）。

**K2-seg（2026-07-05）**: `device_profiles/musicgen.yaml` の `control_defaults` に
compose プロンプト欄 5 本（active rate / valley depth / Avoid / semantic.core /
time signature）の実測 defaults を追記した（tight 0 / loose 2 / dead 3）。`Avoid: X`
文言は X を正方向に引き寄せる符号反転が実測され（`semantic.avoid` quirk、advisory
非 null）、MusicGen で本文 Avoid を負方向制御として使わないよう明記した。詳細は
[`musicgen_backend.md`](musicgen_backend.md) §7.6。

**K2-seg Suno 転移バッチ 1（#162, 2026-07-09）→ 撤回（Codex #164 P2）**: `semantic.core`
は測定自体は達成した（物理 onset_density d=+0.230909・CLAP energy d=+2.446820、判定は
[`examples/control/k2_suno_segments/README.md`](../examples/control/k2_suno_segments/README.md)）
が、生成器がユーザーのカスタムモデル（同 README honesty (g)）由来で標準 stock モデルへの
一般化が未検証のため、`device_profiles/suno.yaml` の `control_defaults` への反映は
撤回・保留した（一度 loose で入場させたが Codex #164 P2 レビューで撤回）。stock 検証、
または実測の generator scope（custom vs. stock）を区別する model-scope 機構の新設まで
保留する。CLAP は tight 域の値だが、そもそも SEM-1 ゲート（学習センサー由来ノブの自動
tight 昇格禁止）に従えば grip_class は loose 止まりであり、tight 昇格には DD-4 条件 2
相当の formal な充足確認が別途必要という点も変わらない。

### advisory 規則（自動補正はしない）

`knob_quirks` を quirk 定義順に走査し、`advisory` が非 `null` かつ発火条件（`applies_to_values`
への `str(score 値)` の完全一致、または `applies_below`/`applies_above` の数値閾値。数値比較は
int 値のときのみ行い `TODO(transcribe):` センチネル文字列は自然にスキップされる）を満たせば
`GeneratedPrompt.advisories` へ文言を追加する。**プロンプト text / tags / negative_tags /
dropped_elements は advisory によって一切変わらない** — 計器であって補正器ではない。
`cross_couplings` は `status: unresolved`（R=4・dead 行なしでセル単位の解釈が未確定）のため
advisory を出さない設計とし、誤って「補正済み」の印象を与えない。
CLI (`svprpe compose`) の text 出力モードでは advisory を stderr にのみ出す
（stdout / `-o` ファイルは Suno 等へそのまま貼り付けるプロンプト成果物のため純粋なまま保つ）。
JSON 出力モードでは `GeneratedPrompt.advisories` フィールドとして構造化データに保持する。

### adherence との非対称（follow-up）

[`score-adherence`](#pr2-楽譜準拠テストscore-adherence)（PR2）は **score が明示的に宣言した
tight フィールド**の保持/非保持のみを判定する。device defaults 由来の tight（score が
黙っているフィールド）は adherence の判定対象**外**——「device が黙って埋めた保証」を
「楽譜が主張した保証」と同列に検証してよいかは未決の設計判断であり、対象化する場合は
別途 follow-up とする。
