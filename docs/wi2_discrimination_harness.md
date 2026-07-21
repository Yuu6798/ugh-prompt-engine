# WI2 — 弁別判定ハーネス（identity-rank）と破壊 recast 行列の実測

日付: 2026-07-21（`date -u` 実測確認済み）
状態: WI2 完了報告。計器 `svprpe identity-rank` + 4 セル行列の実測 + 弁別成立軸の確定
起点: [`work_identity_roadmap.md`](work_identity_roadmap.md) WI2 節

## 1. 目的と設計

同一 Base Score（midnight_signal）から「全 anchor 保存」「anchor 破壊」recast と
他作品を決定論生成し、レンダリングの抽出結果が
{正楽譜, 破壊対照, 他作品} のどれに最も近いかの**順位**を計器軸ごとに出す。
計器であって verdict なし（score-adherence / lyrics-adherence /
repetition ranking の系譜）。

### セル構成（同一バッチ・交絡隔離・WI1 fixture 不使用）

| cell | 内容 | n | seed |
|---|---|---|---|
| A | faithful（edm.identity.musicgen arrangement の derived score） | 4 | 8300–8303 |
| C | structure 破壊（verse↔chorus 隣接交換のみ = D-1 語彙外の最弱形） | 4 | 8320–8323 |
| D | 他作品（musicgen_r3_source: bpm120 / C major / [intro,body,outro]） | 4 | 8340–8343 |
| P | チャネル死証明（harmony 破壊 derived score を **seed 8300** で 1 本） | 1 | 8300 |

- musicgen-small @ 4c8334b0…・30s・guidance 3.0・perform 経路
  （`collect_musicgen_takes.py perform` = ExternalPromptAdapter 直接。
  WI1 の package 経路とプロンプト整形が異なるため WI1 との横比較はしない）
- canonical 規律: 事前登録 `plan_confirmed_at_utc = 2026-07-21T06:27:42Z`
  が全生成に先行 + fresh-process スポット検証 2/2 一致
- **B harmony 破壊はレンダリングしない**: chord_progression はプロンプトに
  一切載らない（prompt_renderer に参照なし）ため、生成チャネルが構造的に
  dead。破壊仕様（正典進行 +5 半音・quality 不変）は参照デコイとして順位
  計器側でのみ使用

### 破壊強度の事前登録

弱すぎ/強すぎによる自明化を避けるため中強度で固定（wi2_plan.yaml）。
「弁別が出ない」も事前登録済みの有効な結果とした。

## 2. チャネル死の機械証明（本トラックの新手法）

harmony 破壊 derived_score を faithful と同一 seed 8300 で生成した結果、
**wav sha256 が byte 一致**（`90eb7c68…`、プロンプト文字列も同一）。
「chord_progression は MusicGen 生成に因果到達しない」を統計的 dead 判定
（K1 の効果量ゼロ近傍）ではなく**決定論の直接証明**として確定した。
記録: `wi2_channel_death_proof.yaml`（manifest pin と突合するテスト付き）。
プロンプトに載らない欄の破壊実験を n 増しで回す無駄を恒久に封じる。

## 3. 計器 `svprpe identity-rank`

- 入力: 抽出済み RPE bundle + 参照リスト（`wi2-references/0.1`）
- 軸: structure（位置整合率）/ harmony（repeated_chord_sequence_match_rate）/
  key（weighted_key_score）/ bpm（相対差）/ brightness（宣言帯域との
  centroid 距離・B-3 帯域 + `neutral_band_bounds` 再利用）
- 出力: take × 軸 × 参照の距離と順位。同率 tie は ref_id 昇順の安定
  tie-break + **`tied_with` 明示**（tie-break による機械選出を「弁別による
  1 位」と誤読させない。harmony 軸の実測で必要性が確定した設計）
- 被覆明細: melody（#199 除外）/ lyrics（instrumental）/ CLAP
  （semantic-embed extra 未導入）を excluded_domains として明記
- 決定論: 同一入力 → byte-identical JSON（実測: 別プロセス二巡 12/12 sha 一致）

### 実測で検出し修正した計器欠陥 2 件（透明記録）

1. **key 軸の実データ退化**: `RPEBundle.physical` は key を主音（`key: "C"`）と
   `mode`（`"minor"`）の別フィールドで持つが、計器が素の主音のみを
   `weighted_key_score` に渡し mir_eval が reject → フォールバック 0 で
   全参照一律距離 1.0。合成テストは結合済み文字列を渡していて検出不能だった。
   修正: 結合してから比較 + mode 欠損は not_observed + 実 bundle 形式の
   回帰テスト（完全一致 = 距離 0.0 を恒久 pin）。教訓: **計器軸は実データ
   形式での回帰テストを持つこと**
2. **参照セットの base-vs-derived 交絡**: 初回パスは canonical 参照を base
   composition_score.yaml にしたため、brightness/bpm 軸が「破壊差」でなく
   「base（dark/128）vs derived（bright/132）の編曲差」を測っていた。
   参照を最小差構成（canonical=derived/A・structure_destroyed=derived/C・
   harmony_decoy=derived/Bp）へ是正。教訓: **順位計器の参照セットは対比ごとに
   単一因子差で構成する**（交絡隔離規律の参照側への適用）
3. **harmony 軸の cycle 数不整合**（Codex P2 検出）: performer は進行を
   playable セクションごとに反復レンダーし roundtrip 診断は cycle 数で補償
   するのに、計器が cycles=1 固定で比較していた（忠実 2 周ストリームが
   距離 1.0 になる chain honesty の破れ）。diagnose.py と同規則の公開ヘルパー
   `reference_chord_progression_cycle_count` で是正。**当初「C take1 のみ
   decoy 0.75 単独勝ち」と見えた観測はこのバグの産物**で、正しい cycle=2 では
   12/12 完全 tie に揃った（見かけの弁別が計器誤差だった実例）

## 4. 実測結果（n=12、修正後 v2）

| 軸 | 弁別成立 | 実測内訳 |
|---|---|---|
| **bpm** | **成立（唯一）** | 作品帰属 11/12 正解（A/C→canonical 側 7/8・D→other_work 4/4）。唯一の誤帰属は A take3 = 89.1 アトラクタ。C take3 は 258.4（129.2 の octave 倍化）でも帰属自体は正解（全距離が大きいまま canonical 最小） |
| structure | 不成立 | A/C 間弁別なし。A 群 [Intro,Chorus,Outro] ×3 では destroyed(0.5) < canonical(0.75) の**逆転**（位置整合率の長さバイアス: 3 トークン観測は 4 トークン正典より他作品 3 トークン参照に近く出る） |
| harmony | 不成立 | **12/12 が canonical vs decoy の 1.0 同率 tie**（tie-break 選出であって弁別でない）。cycle 修正前に見えた C take1 の decoy 0.75「単独勝ち」は cycles=1 バグの産物（§3 欠陥 3）— 弁別信号は完全にゼロで、チャネル死証明（§2）と整合 |
| key | 不安定 | 修正後も帰属 3/12 正解程度。センサーが平行調/同主調へ流れる（C major 読み 6/12） |
| brightness | 原理的に不能 | 全参照が bright 宣言のため全行 tie。宣言が一致する行列では帯域内判定しか出ない |

副次記録: verse 未出現が WI1 と合わせ**通算 32/32**（package/perform 両経路 =
プロンプト整形非依存の示唆）。bpm アトラクタ 89.1 / 117.45 / octave 倍化の
再現。C take0/take1 は bright 宣言下で centroid 343/530（dark 域）=
brightness tight（K2 d=2.25）に対する外れ観測候補（n=2 につき主張なし・記録のみ）。

## 5. grip 地図とのスモーク照合（受け入れ条件 2）

矛盾なし: bpm loose+アトラクタ量子化（K2/R2）→ 帰属成功と失敗パターンが
既知病理どおり / structure loose（M1 0.583）→ 順序指定の非転写と整合 /
harmony はセンサー越し弁別なし（WI1 full_cycles=0/20 と整合）/
brightness は宣言一致下で tie（計器仕様どおり）。

## 6. WI3 への含意

- **弁別フロンティアの確定**: この生成器（MusicGen）では「作品の同一性を
  レンダリング経由で弁別できる軸」は実質 bpm のみ。anchor 破壊
  （structure/harmony）は「チャネル死」または「loose+尺切り詰め」で
  レンダリングに転写されない — **WI3 の人間校正ペアは、生成器が実際に
  レンダーできる差（bpm/物理系）と、レンダーできない差（進行・順序）を
  分けて設計する**必要がある
- structure 軸の順位計器は位置整合率でなく **WI1 D-1 編集分解（reorder 検出 +
  被覆）ベース**へ v1 改良する余地（長さバイアスの解消）
- tie 明示（`tied_with`）は WI3 の予測力集計で「tie-break 勝ちを勝ちに
  数えない」ための前提部品

## 7. 受け入れ条件の充足

1. **順位表の決定論再現**: 別プロセス二巡 12/12 sha 一致 +
   `tests/test_wi2_discrimination_fixture.py` が committed bundle から
   再計算して byte 一致を CI で恒久検証
2. **grip 地図スモーク**: §5 のとおり矛盾なし（実測値 pin テスト付き）
3. 付随: チャネル死証明（sha 一致）・spot 検証 2/2・実測値 pin
   （bpm 帰属 / harmony tie / structure 逆転 / key 回帰）

## 8. fixture

`examples/arrangement/midnight_signal/observed/wi2_discrimination/`
（plan / timestamps / takes manifest 13 pin / 抽出 bundle ×12 /
順位表 ×12 / 決定論記録 / チャネル死証明 / スポット検証 / derived scores /
デコイ進行。wav は sha256 pin のみで非コミット）。破壊 arrangement 2 本は
`examples/arrangement/midnight_signal/edm.identity.musicgen.{structure,harmony}_destroyed.arrangement.yaml`（additive 新設・既存 fixture 不変）。
