# DESIGN F1 — Foundry Adapter v0（音節規模 unit-selection レンダラ + Performance 遺伝子 v0）

- 日付: 2026-08-15
- 根拠: F0/F1a/F1b/F1c スパイク梯子の実測（`results_f0/`〜`results_f1c/` の各 record）
- 状態: 実装指示（Claude 完結ルート: Fable 設計 → Sonnet 実装 → Fable 判定）

## 0. 実測で凍結済みの設計判断（再交渉しない）

1. 合成コア = **WORLD（pyworld）**。roundtrip がほぼ自然（F1c 耳判定）= 透明性十分
2. 包絡軌跡は**音節規模の連続単位**で運ぶ。フレーム規模の繋ぎは発声破綻（F1b）、
   連続歌唱は歌声成立（F1c）→ 最適点は中間
3. f0 微細構造は**精度軸**。Performance 遺伝子 v0 を同梱し、ロボット f0 からの改善を
   耳で測れる形にする
4. テクスチャ供給 = 実歌唱テンプレート（vocadito clip 2・CC BY 4.0・sha 照合済み）。
   単一ドナー = 準クローンは既知の暫定措置（§8 型ゲートは F1.1 以降）
5. **帯域指標での最適化は禁止**（F1b 教訓）。計測は記録のみ、判定は耳

## 1. モジュール構成（新設 `voice_genesis/foundry/adapter/`）

```
adapter/
├── donor_bank.py   # ドナー分析 + 音節単位の切り出し + npz キャッシュ（キャッシュは scratchpad 側・非コミット）
├── units.py        # 単位選択（target cost + concatenation cost・貪欲・決定論）
├── joins.py        # 単位接合（WORLD パラメータ領域の 30ms クロスフェード）
├── perf_genes.py   # Performance 遺伝子 v0（f0 微細構造の決定論生成）
├── voice_spec.py   # FoundryVoiceSpec v0（JSON・schema "foundry-voice/0.1"）+ 変形演算子
├── render.py       # パイプライン: score → f0/units/joins/warp → WORLD → WAV
└── presets/        # voice JSON プリセット 2 種（neutral / warped）
```

singer/ の score・performance は read-only import（既存の sibling sys.path 方式）。
既存ファイルの変更は一切なし。

## 2. 各モジュール仕様

### donor_bank.py

- 入力: vocadito clip 2 の 24kHz wav（scratchpad `foundry_f1b/vocadito/` を既定探索。
  無ければ Zenodo から再取得: zip md5 `dea40fd18f14d899643c4ba221b33a46` を照合）
- WORLD 分析（harvest/cheaptrick/d4c・5ms）
- **単位切り出し**: vocadito 同梱の notes アノテーション CSV があれば note onset/offset を
  単位境界に採用（音節規模が構造的に得られる）。無ければ fallback = 有声連続区間を
  エネルギー谷で分割（250ms–1.2s に収まるよう再帰分割）
- 各 unit: frames [start,end)・median f0・duration・先頭/末尾の log-sp 境界ベクトル
  （接合コスト用に 24 log 帯域へ縮約）
- キャッシュ: npz（クリップ sha256 + 分析パラメータをキーに含める）。**リポには
  コミットしない**（数十 MB 級）

### units.py（選択・決定論）

- ターゲット = score ノート列（pitch/duration）
- 候補 = |Δpitch| ≤ 3 半音の unit（空なら段階拡張、拡張履歴を結果に記録）
- コスト = w_p·|Δsemitone| + w_d·|log(dur 比)| + w_c·(直前選択 unit 末尾と候補先頭の
  log-sp 距離)。貪欲逐次 argmin・tie は unit index 昇順（決定論）
- 尺合わせ: フレーム index の線形リサンプルで note 長へ伸縮（比率 [0.5, 2.0] にキャップ。
  超える長音は unit 中央 50% 区間の往復ループで持続）
- F1b の「明るさフロア」は導入しない（帯域最適化禁止。接合コストが整合性を担う）

### joins.py

- 隣接 unit 間 30ms を WORLD パラメータ領域でクロスフェード: log-sp 線形・ap 線形
- f0 は**ドナー由来を使わない**（下記 perf_genes が全区間を生成 = F2 軸の単離。
  ドナー micro-f0 の unit 持ち込みは F1.1 の選択肢として記録のみ）

### perf_genes.py（v0・全て決定論 seed 付き）

パラメータ（spec JSON の `perf` 節）: onset_glide {depth_cents:-80, time_ms:60} /
vibrato {rate_hz, depth_cents, onset_ms:150, **phase_reset_per_note:true**} /
drift {depth_cents:10, rate_hz:0.4}（seed 付き帯域制限ランダムウォーク・i.i.d. 方式 =
glottal.py の UNDERSPEC-S1-4 教訓踏襲）/ jitter_cents:3 / portamento_ms:55。
ベースは singer/performance.py の note track（read-only import）に乗算合成。

### voice_spec.py

```json
{"schema": "foundry-voice/0.1",
 "donor": {"dataset": "vocadito", "clip": 2, "sha256": "..."},
 "warp": {"formant_scale": 1.0, "tilt_db_oct": 0.0, "breath_lift": 0.0},
 "perf": {"vibrato": {"rate_hz": 5.2, "depth_cents": 25}, "...": "..."},
 "seed": 11}
```

変形演算子は results_f1b の実装（freq_warp / spectral_tilt / ap 底上げ）を移植・共通化。
presets: `neutral.json` と `warped.json`（formant_scale 0.96・tilt −1.5・vibrato 別値・
別 seed = 新スタック初の「作り分け」デモ）。

### render.py

CLI: `python -m adapter.render --score sakura --voice presets/neutral.json --out x.wav`
（相対 import 問題を避ける実行形態は実装時に確定してよい。--score は sakura / umi）。
出力 24kHz PCM_16・ピーク 0.6。同一 spec + seed → **同一バイト列**（決定論契約）。

## 3. Acceptance Criteria

- [ ] `render` が sakura / umi × neutral / warped の 4 WAV を生成し、同一入力で
  bit 一致（決定論テストで enforce）
- [ ] 単体テスト（合成ミニドナーで高速・実 vocadito 非依存）: unit 切り出し境界 /
  選択の決定論 / 伸縮キャップとループ / 接合クロスフェードの連続性（境界フレームの
  log-sp 跳びが単位内部の跳び分布を超えない）/ perf_genes の位相リセットと seed 再現
- [ ] E2E（実 vocadito・slow 扱いで通常実行から分離）: 4 WAV 生成 + sha 記録
- [ ] `ruff check .` pass・既存テスト非破壊（本体 testpaths 不変更）
- [ ] `results_f1/`（新設）に record: 実装決定・provenance・4 WAV の sha256・
  耳判定素材の提出記録
- [ ] 耳判定素材を User へ提出（sakura neutral / warped + F1c transplant を比較用に再掲）

## 4. Scope

- IN: `voice_genesis/foundry/adapter/**`（新設）・`foundry/tests/`（追加）・
  `foundry/results_f1/`（新設）
- OUT: `voice_genesis/{harness,proto1,singer}/**`・`src/svp_rpe/**`・`pyproject.toml`・
  既存 results_*。帯域指標をコスト関数に入れること
- 非目標（F1.1 以降へ記録）: 多ドナー混合・identity ゲート・子音の明示合成
  （unit 境界の子音残渣は容認）・ドナー micro-f0 の持ち込み・歌詞

## 5. Risks

- vocadito notes CSV の粒度が粗い/欠損 → fallback 分割で吸収（発動を record に記録）
- 長音ループの継ぎ目が新たな不自然さ源 → ループ境界もクロスフェード・耳判定に委ねる
- 接合コストの重み w_* は初期値（1.0/0.3/1.0）から耳判定で調整（帯域では調整しない）

---

## 追補 F1.1 — 音素ゲート対応（2026-08-15・v0 耳判定「日本語発声になってない」を受けて）

背景 = `results_f1/f1_record_2026-08-15.md` §6b。耳ゲート階層（声 → 言語 → 精度）の
第 2 ゲートを通す。ドナーはスペイン語（日本語と同じ 5 母音体系）につき、母音骨格は
現ドナーで日本語互換にできる。凍結事項 §0 は全て継続。

### F1.1-A 母音クラス選択（adapter/vowel_class.py 新設 + units.py 拡張）

1. 各 unit の中央 50% フレームの median log-sp から F1/F2 を推定
   （F1: 200–1200Hz / F2: 700–3000Hz のピーク・F2 > F1+150Hz 制約・平滑化後）
2. 5 母音プロトタイプ（女声近似の (F1,F2) Hz: a(850,1500) e(500,2100) i(350,2500)
   o(500,900) u(380,800)）への log-Hz 距離で分類。マージン小は低信頼クラス "x"
3. 選択コストに母音不一致ペナルティ w_v を追加（既定 10.0・強制力のある大きさ）。
   目標母音の unit が音域内に無い場合は近縁母音（a↔o, i↔e, u↔o）へ
   半ペナルティでフォールバック（発動を記録）
4. 目標母音時系列は score の mora から導出（F1a glue の母音解決を共通化）

### F1.1-B 子音オンセット移植（adapter/consonants.py 新設）

singer/ S6–S9 の耳検証済みレシピを WORLD パラメータ領域へ移植する（短時間過渡は
手設計規則が耳を通ることを S6–S9 が実証済み — 「規則=構造・データ=テクスチャ」境界の
例外ではなく整合）。合成前の (sp, ap, f0) フレーム列に対し、mora の子音種別ごとに
ノート頭 20–80ms を加工:

- 破裂/摩擦（k,t,s 等）: 帯域整形ノイズバースト（ap→1 + 整形 sp・render_song.py の
  `_band_noise` の帯域設計を参照）
- /r/: 短い振幅ディップ + locus（S6 レシピ）
- 鼻音 /n,m/: murmur（低 F1 ≈250Hz・高域減衰）→ locus F2 出発点（n=1700 / m=1000Hz）
  → 10ms 開放（S8/S9 実測値をそのまま移植）
- /h/: 後続母音の包絡で整形した気息ノイズ
- /y,w/: 短い i/u 系包絡からの遷移（グライド）
- 実装対象は sakura / umi の歌詞に現れる子音種のみ（全音素体系は非目標）

### F1.1 Acceptance 追加分（実装済み 2026-08-15・record §8）

- [ ] 母音分類の 5 クラスが実ドナーで全て非空（分布を record に記録）
- [ ] 選択結果の母音一致率 100%（フォールバック発動は件数記録）
- [ ] 子音オンセットの単体テスト（種別ごとの形状・決定論）
- [ ] sakura / umi の新 4 WAV + 決定論 bit 一致・v0 との差分 sha 記録
- [ ] 耳判定素材の提出（質問 = 「日本語の発声・歌詞に聞こえるか」）

---

## 追補 F1.2 — 日本語ドナー導入（2026-08-15・User 承認: 波音リツ主 + PJS 副）

精査 = `jp_corpus_survey_2026-08-15.md`。ライセンス上の採用判断は User 決裁済み。

### F1.2-A 取得と provenance

- 波音リツ（canon-voice.com 配布の UTAU 音源。多音階の連続音/単独音のうち実際の
  配布物を調査して選定・記録）と PJS（Google Drive 配布 ver.1.1）を取得
- 各 zip/アーカイブの sha256・取得 URL・取得日を record に記録。ライセンス/規約
  ページの本文スナップショットを `results_f1_2/licenses/` に保存
- attribution: リツ = 規約準拠のクレジット表記、PJS = CC BY-SA 4.0（**出力派生物への
  SA 継承を attribution に明記**）

### F1.2-B UTAU 銀行ローダー（adapter/donor_bank_utau.py）

- oto.ini を解析（offset / consonant / cutoff / preutterance / overlap）し、
  各サンプルの**母音安定区間**を unit 化、**子音区間は録音済み子音オンセット素材**
  として別保持（合成子音 S6–S9 層より録音優先）
- 音高: 音源フォルダ構成（多音階サフィックス）または frq から解決して unit に付与
- WORLD 分析・bank スキーマは既存 donor_bank と互換（キャッシュは scratchpad・非コミット）
- 音素ラベルは oto エイリアス（かな/CV/VCV）から正規化（判断は record に記録）

### F1.2-C PJS 銀行ローダー（adapter/donor_bank_lab.py）

- .lab 音素ラベルから音素区間 → mora 単位（子音 + 母音核）を unit 化
- 男声につき score との音域差は移調で吸収（オクターブ単位を基本・移調量を記録）

### F1.2-D 配線と出力

- render に `--donor {vocadito,ritsu,pjs}` と `--consonant-source {recorded,synthetic,none}` を追加
  （recorded が無い音素は synthetic へフォールバック・件数記録）
- 出力: sakura / umi × ritsu / pjs（neutral）+ 従来 vocadito 版の比較保持

### F1.2 Acceptance

- [ ] 両ドナーの取得・sha256・ライセンススナップショット保存
- [ ] リツ bank: 音素ラベル付き unit 数・音高被覆・かな正規化表を record に記録
- [ ] PJS bank: mora unit 数・移調量を record に記録
- [ ] sakura / umi × 2 ドナーの WAV + 決定論 bit 一致
- [ ] oto/lab パーサの単体テスト（合成 fixture・実データ非依存）
- [ ] ruff / foundry テスト / singer 38 本非破壊
- [ ] identity 留意（リツ = 既知キャラ声 → Genome 変形最低量の適用対象）を record に明記
- [ ] 耳判定素材提出（質問: 日本語発声ゲートを通ったか）
