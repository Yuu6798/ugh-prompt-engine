# Work Identity 判定トラック（WI 系列）— 作品同一性の実測定義ロードマップ

2026-07-20 の方向性壁打ちで確定した新トラック。AR トラック（AR0–AR4 実装済み）で
「楽譜 → コンパイル → 演奏 → 観測 → 検証」の機構は一周したが、観測は計器であって
verdict を出せない（判定閾値・同一性判定関数が未定義）。本トラックは
[`ai_music_daw_vision.md`](ai_music_daw_vision.md) §3.4 の中期課題「楽譜の概念定義
= 作品同一性の境界条件」と §7 の条件 (3) Discriminability / (4) Identity
preservation の検証を実行計画に落とし、「測れるが判定できない」計器群に
判定能力を与えることを目的とする。

## 位置付け

- **本丸への挑戦**: 部分実証の補完（ノブ・センサー・n の横展開）より、判定関数の
  成立を優先する。補完は本トラックの前提部品になる分だけを拾う（2026-07-20 壁打ち
  の期待値判定）
- 意味の同一性の**一般解は目指さない**。以下の 4 点セットで「作品を運ぶ楽譜」に
  十分な判定を成立させる:
  1. **宣言的契約** — 同一性は作品に内在する普遍量ではなく、作者が identity
     manifest で宣言した契約の履行として定義する（クラシック楽譜と同型）
  2. **弁別判定** — 絶対閾値ではなく順位・対比で判定する（contrast_fit /
     「効果 > 再生成ノイズ」一様適用の前例に従う）
  3. **再構成条件の校正** — 意味は直接測らず、人間判定を基準器として
     「どの構造的制約の保存が『同じ曲』判定を予測するか」を校正する
  4. **被覆の正直会計** — 判定は常に verified / not_observed の明細付き。
     全域判定を主張しない（4 状態分離の思想の延長)

## 設計原則（規律）

- **Goodhart 抑止**: identity proxy は検収の判定器であって、生成の目的関数に
  しない（vision §5.3）。proxy を最適化対象へ使う提案は本トラック範囲外とし、
  別途ゲートを要する
- **ラベル先行付与禁止**: 閾値確定前の観測に分類ラベルを付けない。観測は中立な
  候補入力として扱う（#196 レビューの教訓）
- **事前登録**: 人間判定実験は判定質問文・提示順・軸集合・破壊強度を生成前に
  固定する。判定が割れる／どの軸も予測しない、も事前登録済みの有効な結果とする
- **canonical 規律**: 生成バッチは AGENTS.md §8「ローカル決定論バッチの
  canonical 条件」に従う。人間判定は当面 n=1 につき非 canonical v0 と格付けする

## フェーズ表

| Phase | 内容 | 律速 |
|---|---|---|
| WI0 | 前提センサー配線: melody（basic_pitch）/ lyrics（lyrics_match）を observe へ接続し `no_sensor` 2 domain を解消 | なし（人手ゼロ） |
| WI1 | 逸脱分布と D-1 閾値: MusicGen 無人 n=20 → 逸脱分布 → `changed_within_policy` / `changed_outside_policy` 分類閾値の Design Memo | なし（環境再セットアップ ~20 分） |
| WI2 | 弁別判定ハーネス: anchor 別破壊 recast 行列（決定論生成）× 順位判定計器 | なし |
| WI3 | 人間校正 v0: 事前登録した recast ペア ×「同じ曲か」判定（基準器 n=1）→ 計器軸の予測力校正 → identity proxy v0 | 人間判定（小規模） |
| WI4 | 制度化: proxy の observe / verify への advisory 配線・per-work identity budget・昇格ゲート | WI3 の結果 |

## 各フェーズ詳細

### WI0 — 前提センサー配線（melody / lyrics）

- **仮説**: 人間の同一性判定は旋律への依存が大きい（survivor 性質「記憶可能な
  特異性」も旋律中心）。旋律軸なしで WI3 の校正実験を行うと「どの軸も予測しない、
  なぜなら最重要軸を測っていないから」という予見可能な失敗になる
- 接続点は `observe.py` docstring に明記済み（lyrics = `eval/lyrics_match` +
  lyrics_adapter / melody = basic_pitch）。structure センサー（#192）と同型の手順
- **受け入れ条件**: observe が melody / lyrics anchor で `sensor.available: true`
  を返し実測 fixture を持つ。既存 committed fixture への影響ゼロ。basic_pitch の
  転写精度は実測して記録し、WI2 の軸集合に入れるかは実測で決める（精度不足なら
  not_observed のまま除外＝それも記録）

**2026-07-20 実測完了**: WI0-a（センサー配線、#198）に続き WI0-b（実推論初計測）を
完了した。決定論 synth performer 出力（faithful take, transpose 0）に対する
basic-pitch 実推論で `pitch_lcs_ratio = 0.6 < 0.8`（事前登録閾値）→ **melody 軸は
WI2 v0 の軸集合から除外**する（被覆明細では `not_observed` 扱い）。原因は
センサー品質ではなく比較設計（全ミックスをポリフォニックに採譜 vs 単旋律正典を
分離層なしで直接比較）— 再入条件は旋律分離層の導入後の再計測。lyrics は
instrumental 入力で faster-whisper が `no_speech_prob` 0.92–0.94 を自己申告し
つつ abstain せずハルシネーション文を emit する境界挙動を記録した（精度実測は
歌入り + 歌詞 pin 音源が無く素材律速で defer）。詳細・生値・判定根拠:
[`examples/arrangement/midnight_signal/observed/wi0b_synth/results.md`](../examples/arrangement/midnight_signal/observed/wi0b_synth/results.md)。
**WI2/WI3 への含意**: v0 の軸集合に melody は入らない（被覆明細で `not_observed`
として明示する）。

**follow-up（PR #199 Codex P2, 2026-07-20）**: extract 証跡（`svprpe extract --lyrics`
出力、`RPEBundle` スキーマ）は入力音声の hash 欄を持たないため、pin 済み wav との
完全な機械的紐付けができない — attestation（同一セッション内の手順連続性）と
決定論部分の実行時機械接地（`tests/test_wi0b_synth_observed_fixture.py` の slow
テスト）で代替した（詳細:
[`wi0b_synth/results.md`](../examples/arrangement/midnight_signal/observed/wi0b_synth/results.md)
§5）。`RPEBundle` に `source_audio_sha256` 欄を追加してこの限界を解消する件は
未着手 — WI1 以降で他の schema 変更とまとめて検討する。

### WI1 — 逸脱分布と D-1 閾値 Design Memo

- MusicGen 無人バッチ n=20（canonical 規律・事前登録・fresh-process sha スポット
  検証）で structure / harmony の逸脱分布を取得する
- 分布から分類閾値を導出し D-1 Design Memo として起草。導出過程は Memo に明記し
  独立検算を通す（§8 検算照合ゲート）。実 Suno A/B の A2 / B1
  （`observed/suno/`）は分類設計の**中立な候補入力**として使う
- **受け入れ条件**: 分布 fixture + 閾値 Memo。閾値適用で observe の
  `adherence_status` が `not_observed` から `changed_within_policy` /
  `changed_outside_policy` の分類へ進める設計になっていること（`determination`
  の語彙 `exact_match` / `deferred` / `no_sensor` は不変のまま。実配線は
  WI4 でもよい）

**2026-07-21 実測完了**: MusicGen 無人バッチ n=20（canonical 規律・事前登録・
fresh-process sha スポット検証 2/2 一致）で structure/harmony 逸脱分布を取得し、
D-1 分類規約 v0（参照ポリシー下）を適用して within 14 / outside 6（reorder 2 +
被覆床 4）を確定した。harmony は `full_cycles=0` が 20/20 のため v0 では
分類を定義せず `deferred` を維持する。実 Suno A2/B1 を中立候補入力として適用し
定性判断と一致することも確認済み。詳細・分布表・検算照合:
[`wi1_d1_thresholds.md`](wi1_d1_thresholds.md)。

### WI2 — 弁別判定ハーネス

- `svprpe arrange` で同一 Base Score から「全 anchor 保存」recast と「anchor を
  1 つずつ意図的に破壊」した recast 群を決定論生成し、演奏（MusicGen）→ 抽出まで
  通した行列を作る
- 判定計器: rendering の抽出結果が {正楽譜, 破壊対照群, 参照コーパスの他作品}
  のどれに最も近いかの**順位**を、計器軸ごと（物理欄・事象系列・CLAP 軸・
  歌詞転写・旋律）に出力する。この段階では計器であって verdict なし
- 破壊強度（どの程度壊すか）は事前登録する。弱すぎ／強すぎは弁別実験を自明化する
- **受け入れ条件**: 順位表の決定論再現（sha 一致）。既知の grip 地図（K1 / K2）と
  矛盾しないことのスモーク照合

### WI3 — 人間校正 v0（identity proxy v0）

- 事前登録: 判定質問（「同じ曲のカバーと認めるか」二値 + 確信度）・提示順・
  対象ペア集合・集計規約を、聴取開始前に固定する
- WI2 の recast 行列から選んだペアを人間（基準器 n=1・非 canonical v0）が判定し、
  WI2 の軸別順位がその判定をどこまで予測するかを校正する（「効果 > 再生成ノイズ」
  基準を予測力にも適用）
- **成果物**: 軸別予測力の明細、**identity proxy v0**（予測できた軸集合と重み）、
  被覆明細（not_observed 軸の列挙）。予測ゼロ・判定割れも有効な結果として記録
- 将来の聴者追加（判定者間一致の測定）は WI4 以降の設計項目とし、v0 では
  主張強度を n=1 相応に抑える

### WI4 — 制度化

- proxy を observe のオプション判定層として配線（default off・advisory・本文
  不変の前例に従う）
- per-work **identity budget**（この作品はどの演奏者で何をどこまで固定できるか）
  を楽譜 sidecar として自己記述させる（control_profile の per-work 版）
- 昇格ゲート: proxy 軸の tight 昇格には SEM-1 型の formal 条件（効果 > ノイズ ×
  複数ジャンル × n≥2×2 等）を適用する

## 完成定義

任意の（Score, rendering）対に対して、宣言 anchor ごとに
`preserved / changed_within_policy / changed_outside_policy / not_observed` を返し、
その総体として「同一作品か」の弁別判定と被覆明細を出せること。完成しても意味の
同一性の一般解は主張しない（被覆フロンティアの外は常に not_observed）。

## 既存トラックとの関係

- **AR**: anchor / 契約 / observe / verify を前提部品として使用。AR2-3 の変形語彙
  が WI1 分類の受け皿
- **K / control_profile**: grip 実測知識は WI4 identity budget の材料
- **CLAP / 歌詞転写**: WI2 軸バッテリーの意味層部分（校正済み有効帯域のみ使用）
- **R 系列**: 往復保存性は WI の物理・事象層の先行実証
- **vision PoC ロードマップ**: WI3 の proxy が PoC (4)（survivor SVP 合成・再演の
  判定関数）への入口

## リスクと honesty

- **基準器 n=1 の主観性** — 非 canonical 格付け + 事前登録で管理。判定割れは失敗
  ではなく計測対象（将来の聴者間一致測定へ）
- **旋律センサーの精度限界** — WI0 で実測してから採否を決める。精度不足の場合、
  WI3 は旋律 not_observed の明細付きで実施し、その限界を結果に明記する
- **proxy の Goodhart 化** — 生成目的関数への使用は本トラック範囲外（設計原則参照）
- **破壊 recast の程度問題** — 破壊強度の事前登録で自明化を防ぐ

## 関連ドキュメント

- [`ai_music_daw_vision.md`](ai_music_daw_vision.md) — §3.4 楽譜の概念定義、§7 意味のある仕様の 6 条件
- [`arrangement_identity_planning.md`](arrangement_identity_planning.md) — AR0–AR4、identity anchor と観測計器
- [`ai_performer_score_roadmap.md`](ai_performer_score_roadmap.md) — 楽譜マージロードマップ（PR1–PR3）
- [`control_profile.md`](control_profile.md) — grip_class と昇格ゲート（SEM-1 型）の前例
