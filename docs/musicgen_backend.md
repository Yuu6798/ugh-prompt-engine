# MusicGen ローカル生成トラック — 設計 doc

Status: PR A / PR B / PR C 実装完了（PR B 実測 2026-07-03、§7）。§7.3 で R3 n=20
スケールアップ（key 保存率 0.15 確定）、§7.4 で K3-2b フル直交性行列、§7.5 で CLAP
相互検証②（MusicGen バッチへの学習版 grip 拡張）、§7.6 で K2-seg（compose プロンプト欄
grip スクリーン）、§7.7 で バッチ M2（§7.6 交絡 2 件の解消・30.6 秒再計測）を追加実測済み
Scope: `facebook/musicgen-*`（transformers 経路）を第二生成器として組み込む計画

## 1. 目的と位置づけ

- **人手律速の解体**: Suno は人手 UI 生成が律速。MusicGen はローカル API 経由で
  バッチ生成でき、K/R 系トラックのサンプル数を機械的に増やせる。
- **第二機種の一般性実証**: `docs/control_profile.md` / `compose/device_profile.py`
  の device profile 機構は Suno 1 機種だけでは「Suno の癖」と「一般則」を区別
  できない。MusicGen は複数機種初実証の第一歩。
- **R3（確率的往復）の自動化**: `docs/roadmap_goal2.md` の R3 は生成の非決定性を
  n>1 で束ねる設計。人手生成では反復数が絞られるが、ローカル API なら
  バッチで反復できる。

MusicGen は**演奏者（生成側）**であり、`docs/learned_models_policy.md` の
annotation 隔離原則（学習モデルの出力を RPE evidence に混入させない）は対象外
——生成された音声は他の生成器と同様に librosa ベースの既存 RPE 抽出を通るだけで、
RPE の evidence 層に MusicGen 由来のラベルが直接書き込まれることはない。
ただし OSS ライセンス管理は本 doc の §5 で `docs/learned_models_policy.md` G4
（ライセンス gate）に準拠した verbatim 記録を行う。

## 2. 決定論契約（DD-A 踏襲）

`docs/controllability_poc.md` §4 の DD-A を継承する:

- 生成（MusicGen 推論）はリポジトリ外・非決定論のベストエフォート seed pin
  （`torch.manual_seed`）に留める。GPU/CPU・transformers バージョン間の完全
  一致は保証しない。
- 音声ファイル（WAV）は**コミットしない**。ローカルの生成物のみ。
- **fixture（数値）→ grip の区間のみが決定論・CI 対象**。fixture JSON
  （per-sample 数値特徴量）はコミットしてよく、`scripts/measure_grip.py` が
  それを読んで効果量を計算する処理は決定論的・単体テスト対象。
- CI は `musicgen` extra を一切インストールせず、torch なしで全テスト pass
  することが契約（`tests/test_musicgen_runbook.py` の実抽出テストのみ
  `@pytest.mark.slow`、plan 検証・seed 導出は torch 非依存の高速テスト）。

## 3. 既存配線の現状

- `musicgen` backend descriptor は `src/svp_rpe/compose/prompt_renderer.py`
  の `_BACKEND_DESCRIPTORS` に既存（`profile_key="musicgen"`,
  `negative_channel="negative_prompt"`）。`GeneratedPrompt.backend` の
  `Literal` にも既存。本 PR ではこの配線を一切変更しない。
- `examples/control/k0/musicgen_rpe_fixture.json`（`generator:
  "musicgen_fixture"`）は**手作りの最小 fixture であり実測ではない**。
  K0 は方法実証目的（`docs/controllability_poc.md` §4）で、K2 相当の実測
  fixture は本トラックの PR B が担う。

## 4. PR 分割

- **PR A（実装済み）**: runbook（`scripts/collect_musicgen_takes.py`）+
  `musicgen` extra + 本 doc。実推論は行わない・CI 安全な「器」のみ。
- **PR B（実装完了・2026-07-03 実測）**: `scripts/collect_musicgen_takes.py
  generate` の実バッチ実行 → K2 型 fixture
  （`examples/control/k2_musicgen/fixture.json`）+ `expected_grip.json` +
  `config/device_profiles/musicgen.yaml`（`docs/control_profile.md` の device
  profile 形式）。前提条件だった §5 の G4 ライセンス目視確認も完了済み。
  実測結果は §7。
- **PR C（実装済み・実測待ち）**: R3-1/2/3 ハーネス（§6 参照）。

## 5. License

- **Code**: transformers（Apache-2.0）経由の MusicGen パイプライン
  （`MusicgenForConditionalGeneration`）を採用する。`audiocraft`
  （Meta 公式リファレンス実装、MIT だが依存が重い）には依存しない。
- **Weights**: `facebook/musicgen-small` — **VERIFIED**（verbatim findings,
  verified 2026-07-03, PR B）:
  - Hugging Face モデルカード（`https://huggingface.co/facebook/musicgen-small`）
    の repository-level license badge は `cc-by-nc-4.0`。モデルカード本文の
    ライセンス文は "Code is released under MIT, model weights are released
    under CC-BY-NC 4.0."（HF API `cardData.license` も `cc-by-nc-4.0` で一致）。
  - 確認時点のモデル repo revision:
    `4c8334b02c6ec4e8664a91979669a501ec497792`（PR B の生成バッチはこの
    revision に pin して実行した）。
  - 期待値どおり **CC-BY-NC-4.0**（非商用条項）につき、MusicGen の重みは
    **研究計器限定**の扱い——プロダクト同梱（学習済み重みのリポジトリ同梱や
    商用配布物への組み込み）は不可。ローカル runbook で HuggingFace から
    実行時取得するだけの現行の使い方（重みを一切同梱しない）はこの制約下でも
    問題ない。
- G4 確認完了（上記）により PR B（実バッチ生成 → fixture コミット）の前提条件は
  充足済み。

## 6. R3 接続 — 実装（PR C）

R3（確率的往復、`docs/roadmap_goal2.md`）の計器は実装済み:

- `src/svp_rpe/roundtrip/repetition.py` が本体。`corpus_batch.py` の
  `_regenerate_measured` パターン（音声 → `extract_rpe_from_file` →
  `draft_score` → 物理フィールド辞書）と同型の経路で各テイクを
  `diagnose_roundtrip`（`roundtrip/diagnose.py`）に通し、
  `TakeFieldObservation` へ蒸留する。`grip_map` は既定 `None`
  （＝ `diagnose_roundtrip` に空マッピングを渡し K1 grip map の既定ロードを
  回避する）。K1 は決定論シンセ演奏者の実測であり、確率的生成器の
  disagreement を誤って `knob_dead` に分類してしまうため。将来 MusicGen 専用
  grip fixture ができたら明示的に渡せる。
- `FieldRepetitionSummary`（`n` / `preserved_count` / `preserved_rate` /
  `diagnosis_counts` / `observed_values`）が n>1 のフィールド別往復一致率
  （R3-2）。`roundtrip/compare.py` の 4 値診断語彙（preserved /
  knob_dead / sensor_blind / calibration_disagreement）をそのまま使う。
- `SelectionResult`（`basis="preserved_field_count"`、`ranking`、
  `selected_take_id`）が rejection sampling（R3-3）。同数 `preserved_count`
  は `take_id` 昇順で決定論的にタイブレークする。**`selected_take_id` は
  「楽譜に最も近いテイクの機械的特定」であり品質判定ではない**（verdict
  語彙は一切出さない）。
- `load_takes_for_repetition` が PR A の takes manifest（`samples[]` の
  `audio_path`/`audio_sha256`）を読み、バイト再計算 sha256 と manifest pin
  の照合を fail-fast する（`collect_clap_fixture.py` 規約）。`excluded`
  サンプルはスキップする。
- テイク生成側は `scripts/collect_musicgen_takes.py perform` サブコマンド
  （PR A ファイルの拡張）: `ExternalPromptAdapter` で score をレンダリング
  し（`score.rendering.target_backend` に従う・backend override はしない）、
  `seed = seed_base + i` で N テイクを生成する。
- CLI: `svprpe roundtrip-rep <composition_score.yaml> <takes_manifest.json>`
  （`docs/cli.md` 参照）。
- ~~**未実施**: 実バッチ生成・実測（MusicGen ローカル or Suno 手動）は本 PR の
  範囲外。計器の検証はテストの決定論シンセ演奏者による合成テイクで行った
  （honesty 維持・MusicGen 不要）。~~ → **PR B（2026-07-03）で実測完了、§7.2**。

## 7. PR B 実測結果（2026-07-03）

実測条件: `facebook/musicgen-small`（revision `4c8334b0…` に pin・§5）、CPU、
12 秒クリップ、`guidance_scale=3.0`、seed は `sample_seed` / `seed_base+i` の
決定論導出（DD-A ベストエフォート pin — 環境間の完全一致は保証しない）。
音声はコミットせず、fixture / manifest / レポート（数値）のみコミット。

### 7.1 K2 型 grip（`examples/control/k2_musicgen/expected_grip.json`）

| knob | sensor | low/high | grip | class | Suno 比（#117） |
|---|---|---|---:|---|---|
| bpm | bpm | 90 / 170 | 0.21 | **loose** | Suno は tight 1.61（水準 90/140） |
| brightness | spectral_centroid | dark / bright | 2.25 | **tight** | Suno は 0.86 — MusicGen の方が強い |

- **bpm loose は knob_dead ではなく抽出器 halving の交絡が支配的**: low(90) 側は
  8 本中 7 本が ~89.1（ほぼ的中）。high(170) 側は既定 prior で 86.13×4
  （≈172.27 の半折り）/ 117.45×2（3:2 subharmonic アトラクタ）/ 172.27×1 の読みに
  割れるが、**高 prior 再推定（`start_bpm=180`）では 8 本中 7 本が 172.27 に回復**。
  R2（`roundtrip_corpus_screen.md`）の「高速曲の低 BPM は生成器不忠実でなく抽出器
  halving」が**第二生成器でも再現**した。device profile には R2 closeout の規律
  （faster-side 回復は post-hoc 緩和でありコンパイル時保証に使わない）に従い
  素朴センサー読みの loose で記録し、halving 診断は `knob_quirks` の advisory に
  格納した（`config/device_profiles/musicgen.yaml`）。
- **brightness は Suno より強い tight で、絶対 dark 帯にも到達可能**: dark 指定で
  centroid ≤1200Hz へ 3/8 到達（615.4 / 1067.4 / 398.5Hz。Suno は 0/4 で不到達）。
  ただし分散は大きい（dark 指定で 4517Hz の外れ値 1 本）。
- K3 直交性（非対角クロス効果）と genre bias（spectral_biases）は未計測のため
  device profile に記録していない（空リスト＝honesty）。
- **実測スコープは `facebook/musicgen-small` のみ**（Codex #136 P2）。
  `device_profiles/musicgen.yaml` は backend seam（`target_backend: musicgen`）で
  キーされ、コンパイル側は演奏モデルの粒度を知らない（Suno のバージョン粗さと
  同じ受容済みの粒度）。medium / large 等の未計測バリアントへの defaults 転移は
  保証しないため、モデル id / revision を知る唯一の場所である runbook が
  `profile_scope_advisory`（`scripts/collect_musicgen_takes.py`）で
  `--model-id` ≠ small・revision 未 pin（HF head ドリフトの可能性）・
  別 revision のいずれでも stderr に注意喚起する（プロンプト本文・生成は
  不変 — #128 の本文不変規律）。実測スコープ内は
  `facebook/musicgen-small @ 4c8334b0…` の組のみ。バリアントを実測したら、
  その時点でプロファイルのモデル別分割を再検討する。

### 7.2 R3 初実測（`examples/roundtrip/musicgen_r3_rep_2026-07-03.json`）

`examples/roundtrip/musicgen_r3_source.yaml`（C major / bright / 120bpm・
`target_backend: musicgen`）から `perform` で n=5 テイクを生成し、
`svprpe roundtrip-rep` で R3-1/2/3 を初実測（takes manifest は
`examples/roundtrip/musicgen_r3_takes_manifest.json`、音声はローカルのみ）。
コミット済み artifacts は **musicgen device profile 込みのコンパイル経路**
（brightness が tight 先頭昇格・bpm が loose 末尾）で生成した本バッチ
（Codex #136 P2 指摘により profile 導入前プロンプトの初回バッチから再生成）:

| field | preserved | rate | 備考 |
|---|---:|---:|---|
| key | 0/5 | 0.0 | 全て calibration_disagreement（F minor×2 / F♯ minor×2 / A minor） |
| brightness | 4/5 | 0.8 | 1 本 neutral band（sensor_blind） |
| bpm | 3/5 | 0.6 | 117≈120×3、162×1、undetected×1（R3 選抜からは除外済みのノブ） |
| time_signature | 5/5 | 1.0 | 4/4 |
| active_rate_target | 0/5 | 0.0 | 全テイク 0.96–1.00 — MusicGen は壁一面の密度で鳴らす |
| stereo_width | 0/5 | 0.0 | **MusicGen small はモノラル出力**＝5/5 sensor_blind |

- **R3-3（rejection sampling = 「選択 = 制御」）の初実測**: `preserved_field_count`
  （選抜フィールド key / brightness）は 1/1/1/1/0 で並び、`take_id` 昇順の決定論
  タイブレークで `selected_take_id = musicgen_r3_source_00` が機械特定された。
  同時に**選抜の限界も初観測**: 本バッチは key を保存するテイクが 1 本も存在せず
  （0/5）、rejection sampling は「存在するものから最良を拾う」保険であって、
  生成分布が届かない欄は救えない（verdict ではなく計器の読みの記録）。
- **バッチ間の揺れ（記録）**: profile 導入前プロンプト（bpm 先頭・brightness 後方）の
  初回バッチでは key 2/5・brightness 3/5 で、両フィールドを保存する唯一のテイクが
  特定されていた。プロンプト序列の因果か生成ノイズかは n=5 では分離不能——
  n=5 点推定はバッチ間でこの程度揺れる、という R3 の分散感の最初のデータ点。

### 7.3 R3 n=20 スケールアップ（`examples/roundtrip/musicgen_r3_rep_n20_2026-07-03.json`）

§7.2 の n=5 バッチ間の揺れ（key 2/5 → 0/5）を解像するため、同一スコア・同一
profile 済みプロンプトで n=20（seed 5000–5019）を実測
（manifest: `examples/roundtrip/musicgen_r3_takes_manifest_n20.json`）:

| field | preserved | rate | n=5 の 2 バッチ |
|---|---:|---:|---|
| key | 3/20 | 0.15 | 0.4 / 0.0 — 揺れは二項ノイズ内、**真の保存率は低い**と確定 |
| brightness | 14/20 | 0.7 | 0.6 / 0.8 |
| bpm | 14/20 | 0.7 | 0.4 / 0.6（選抜からは除外済みのノブ） |
| time_signature | 19/20 | 0.95 | 1.0 / 1.0 |
| active_rate_target | 2/20 | 0.1 | 0.0 / 0.0 |
| valley_depth_target | 14/20 | 0.7 | 0.6 / 0.6 |
| stereo_width | 0/20 | 0.0 | モノラル出力＝全数 sensor_blind |

- **rejection sampling は n=20 で完全回復**: 選抜フィールド両方を保存するテイクが
  20 中 2 本現れ（take_11 / take_13）、決定論タイブレークで take_11（bpm まで
  preserved）が機械特定された。§7.2 の n=5 で「key 保存テイクが存在せず選抜が
  救えない」ことと合わせ、**「選択 = 制御」の実効には per-field 保存率に見合う
  n が必要**（key rate≈0.15 なら n=5 の両フィールド保存期待は ~0.4 本）という
  R3-3 の運用条件が定量化された。
- **決定論性の副次観測**: n=20 の seed 5000–5004 は §7.2 バッチと同一 seed であり、
  **5/5 が byte 単位で同一 WAV（sha256 一致）** — DD-A の「ベストエフォート
  seed pin」は同一マシン・同一環境では完全再現に達する（環境間の一致は
  引き続き未保証）。

### 7.4 K3-2b フル直交性行列（2026-07-03）

PR B が開いた「バッチ自動生成」の最初の応用として、K3-2b の設計指示
（dead 行同梱 / R≥8 / key センサー化）を MusicGen 80 クリップで充足した。
結果と読みは [`controllability_poc.md`](controllability_poc.md) §5.5:
ノイズ天井計器の初稼働（天井 |d|=0.848・解像 3 セルのみ）、key 対角 dead
（§7.3 の R3 key 保存率 0.15 と独立整合）、K3-2a 符号反転問題は MusicGen では
unresolved。成果物は `examples/control/k3/musicgen_matrix_*` +
`expected_orthogonality_musicgen.json` + `orthogonality_map_musicgen.md`。
- 付随観測: MusicGen 出力はモノラル（`stereo_width` はセンサー盲）、
  `active_rate_target` は常に上限貼り付き（0.90–0.95 指定に対し 0.96–1.00）。
  楽譜からの MusicGen 演奏では両欄は現状制御不能として扱うのが妥当。

### 7.5 CLAP 相互検証② — 学習版 grip の第二生成器拡張（2026-07-03）

PR2b の CLAP 学習センサー（`contrast_fit`、#131/#132 と同一 checkpoint
`music_audioset_epoch_15_esc_90.14.pt`・sha256 pin 一致・G4 済み cc0-1.0）を
§7.1 の K2 バッチ 32 テイクへ適用した（manifest / fixture は
`examples/learned/clap/musicgen_k2_contrast_*`。音源は cache materialize —
本バッチは同一環境で決定論再生成可能な点が PR2b-2 の Drive 律速と異なる）。
サンプルごとの 512 次元 `audio_embedding` は `musicgen_k2_contrast_fixture.embeddings.json`
サイドカーに退避済み（provenance 保持、`cosines`/`contrast_fit` は本体 fixture に pin）。

| knob | ルール版 grip | CLAP 学習版 grip | 読み |
|---|---|---|---|
| bpm | 0.21 loose（halving 交絡・§7.1） | **2.60 tight・分布重なりゼロ** | fast/slow テキスト対比が 90/170 を完全分離 |
| brightness | 2.25 tight（centroid） | 1.50 tight（方向一致） | 病理のない物理欄では物理センサー優位 |

- **bpm halving 交絡の第三の独立証拠**: 素朴 bpm センサー（d=0.21）・高 prior
  再推定（7/8 回復・§7.1）・K3 対角（0.851・§7.4）に加え、CLAP は 170bpm
  テイク群を「速い」と読み分布重なりゼロで分離した。「生成は効いていた・
  素朴センサーが半折りした」の証拠系が 3 経路で揃った。
- **相互検証①（#132）への重要なニュアンス**: 意味層（vocal contrast）では
  CLAP が桁で有利だったが、物理層では**センサーは階層でなく相補** —
  ルール版に既知の病理がある欄（bpm）では学習版が解像し、クリーンな欄
  （brightness/centroid）では物理センサーが勝つ。学習センサーは「ルール版の
  病理帯域を埋める補助計器」として位置づくのが実測的に正確。
- 数値は `tests/test_clap_similarity.py` の学習版 grip pin テストで固定
  （fixture が変われば docs 再検証を強制）。

### 7.6 K2-seg: compose プロンプト欄 grip スクリーン（2026-07-05）

**目的**: compose（`prompt_renderer.py::_segments_for`）が実際に送出しているのに
grip 未計測だったプロンプト欄（active rate / valley depth / Avoid / semantic.core /
time signature）が「そもそも効いているのか」を一次スクリーニングする。「もっと効く
文言」の探索はしない — 問いは「今エンジンが出しているプロンプトが効くか」のみ。

**条件**: `facebook/musicgen-small`（revision `4c8334b0…`、§5 実測スコープに pin
踏襲）、12 秒クリップ、`guidance_scale=3.0`、5 ノブ × 2 セル × R=8 = 80 クリップ。
ベース文言（全セル共通）は `instrumental electronic music, steady four-on-the-floor
drum beat, at 120 beats per minute` に、compose の結合様式 `". "` で検証セグメントを
追記した（`examples/control/k2_musicgen_segments/plan.yaml`）。音声はコミットせず、
fixture / manifest / expected_grip のみコミット（DD-A 踏襲、§2）。

#### 結果表（`expected_grip.json` verbatim）

| knob | sensor | low/high | grip | class | 読み |
|---|---|---|---:|---|---|
| `active_rate_target` | `active_rate` | 0.55 / 0.92 | 0.39 | **loose** | ベース素材で low セル既に 0.967（headroom 0.033）— 有効帯域の天井効果 |
| `valley_depth_target` | `valley_depth` | 0.15 / 0.70 | 0.15 | **dead** | 12s 定常ビート素材では valley 床（0.078）— ツマミ死と素材制約の分離未解決 |
| `semantic_avoid`（本文 "Avoid: …"）| `spectral_centroid` | none / avoid_bright_highs | +1.10（符号逆）| **dead** | 下記 headline finding 参照 |
| `semantic_core`（物理センサー）| `onset_density` | calm / euphoric | −0.70 | **dead** | 物理センサー盲。CLAP 第二センサー参照 |
| `time_signature` | `time_signature`（categorical・match_rate） | 4/4 / 3/4 | 0.5（combined） | **dead**（honesty 判定） | 3/4 指定 0/8 不達（全観測 4/4）。combined match 0.5 は 4/4 セルの一致率が押し上げただけで loose 表記は誤読注意 |
| `structure`（バッチ M1, 2026-07-12 追補）| `rms_section_pattern`（match_rate・副 `novelty_boundaries`）| structure なし / loud-quiet-loud 3 区間散文 | match 0.583 vs 0.417（novelty d +0.34）| **loose** | 30.54s×R=8・compose 実出力 verbatim（290/155 字）。ヌルゲート非発火・quiet breakdown が主担体（中央区間実現 6/8 vs 3/8）・完全形状一致 0/8。Suno バッチ 3 dead との end-to-end backend 経路比較（同一計器/規約/処方内容。生成器交換に加え backend 別プロンプト整形＝欄順・structure 挿入位置の差込み・#171 P2）— 散文 dead は生成器一般の性質ではないが帰属（機種か整形か）は未分離。fixture: `examples/control/musicgen_structure/m1_expected_grip.json` |

`summary`: tight 0 / loose 2 / dead 3（`config/device_profiles/musicgen.yaml`
`control_defaults` に反映済み）。structure（M1 追補・loose）は control_profile
許可キー外のため config 非掲載 — 設計反映は `omit_structure_prose` を musicgen へ
展開しない（送出継続・根拠を「実測なし」から「実測 loose」へ更新、
`compose/prompt_renderer.py` / `docs/control_profile.md` 参照）。

#### semantic_core の 2 センサー所見

物理センサー（`onset_density`, d=−0.70・dead）と CLAP 第二センサー（`semantic_core`
16 テイクの `contrast_fit`、`examples/learned/clap/musicgen_segments_semantic_core_contrast_fixture.json`
/ `..._valence_contrast_fixture.json`）を並べると:

| 軸 | mean(calm) | mean(euphoric) | Cohen's d | 分類 |
|---|---:|---:|---:|---|
| energy | −0.0662 | 0.0752 | **+1.90** | tight 域 |
| valence（v1.1 初実地読み） | 0.0361 | 0.0916 | +0.60 | loose 域・方向正 |

（生データ: `scratchpad/k2seg_full/clap_semantic_core_stats.yaml`。checkpoint
`music_audioset_epoch_15_esc_90.14.pt`、sha256 `fae3e9c0…`、#131/§7.5 と同一 pin。）

**「物理 dead × 意味層センサー生存 = センサー盲診断の実例」**: calm/euphoric の
テキスト指定は onset_density（打点の粗密）には現れないが、CLAP の意味軸（energy/
valence）は方向どおりに分離する。ツマミ自体は死んでおらず、物理センサーが盲目
だっただけ。ただし CLAP energy の d=+1.90 は数値上 tight 域だが、**学習センサー
由来の意味層ノブの tight 昇格は SEM-1 の昇格ゲート制度（`control_profile.md` DD-4、
#126）に従い loose 固定 honesty を踏襲する**（`config/device_profiles/musicgen.yaml`
`semantic.core` は `grip_class: loose`）。valence 軸は v1.1 バッテリーの初の実地読みで
方向は正だが energy より弱く、探索位置づけのまま。

#### semantic_avoid の attractor 所見（本 PR の headline finding）

本文中の `Avoid: bright shimmering sparkling highs.` を追記したセルは、Avoid *なし*
のセルより **spectral_centroid が上昇**した（low_mean 2817.1Hz → high_mean 3712.5Hz、
d=+1.10）。expected_sign は −1（Avoid が効くなら centroid は低下するはず）のため、
**実測は符号が完全に逆** — MusicGen は否定語（"Avoid"）を無視し、後続の内容語
（"bright shimmering sparkling highs"）をそのまま attractor として正方向に引き寄せる。
`knob_quirks.semantic.avoid` に advisory として記録済み（`config/device_profiles/musicgen.yaml`）:
本文 Avoid を負方向の制御として使わないこと、負方向制御が要件なら生成後実測での
確認を推奨する。grip 値の符号逆転はミスリードを避けるため `control_defaults` の
`grip` キーには入れず、quirk の advisory/description にのみ記録した（honesty judgement）。

**後始末（#152 フォローアップ、2026-07-06）**: 実測された害（attractor 化）を計器が記録
するだけで放置せず、送出側を是正した。`compose/prompt_renderer.py` の `BackendDescriptor`
に `omit_body_negative: bool` を追加し musicgen backend のみ True にすることで、
`ExternalPromptAdapter` は musicgen 向け compile で本文 `semantic.avoid`
（`Avoid: X.`）セグメントを送出しない。`GeneratedPrompt.negative_tags` には引き続き
avoid を記録する（楽譜の意図の保全・消費可否は下流の責務）。送出停止は字数超過の
`dropped_elements` とは区別されるルーティング判断（監査可能性は本 quirk advisory の
改訂文言と descriptor 単体テストで担保）。本追記の時点（2026-07-06）では suno /
external の compile 出力は影響を受けなかった（実測は musicgen 限定・機種依存の効きを
証拠なしに横展開しない）。**その後 suno 自身の実測が確定し（#162, 2026-07-09）、
suno も同様に本文 Avoid 送出を停止した — `external` のみ不変のまま。詳細は下記
「機種依存の注意」参照。**

#### スコープ外（構造的理由）

- **`stereo_width`**: MusicGen small はモノラル出力＝sensor_blind が既に実証済み
  （§7.2 R3 初実測、0/5・0/20）。本スクリーンでは生成せず既知限界として記録するのみ。
- **structure セクション記述**（intro/verse/chorus）: 12 秒クリップに曲構造は現れない。
  長尺生成器（Suno 人手トラック）向けの課題として繰越記録する。→ **バッチ M1
  （2026-07-12）で解消**: 30.54s ローカルクリップ（duration_seconds 30.6・
  delay-pattern の −3 フレーム込み）で structure 欄を実測し loose を確定
  （上記結果表の追補行）。

#### 運用ノート

初回バッチ生成中に孤児プロセスが残り kill → resume する運用事故があったが、
`sample_seed`/`seed_base+i` の決定論導出（DD-A ベストエフォート pin）のおかげで
再生成した manifest がコミット済み manifest と **diff ゼロ**だった。per-sample seed
設計が生成パイプラインの障害に対して復旧耐性を持つことの実地実証になった。

#### 機種依存の注意

MusicGen small の dead は Suno の dead と同一ではない（K3-2a §5.4 の非対角クロス
効果符号反転が前例）。本スクリーンの dead 判定（valley_depth_target / semantic_avoid /
semantic_core[物理] / time_signature）を Suno へそのまま転移させてよいかは
**バッチ 1（2026-07-09）で semantic_avoid / semantic_core の 2 欄について再実測済み**
（`examples/control/k2_suno_segments/`、`docs/controllability_poc.md` K2-seg 節）。
**Suno 本文 Avoid の効き**は本文 Avoid 単独効果として再実測し、attractor が
MusicGen より強い形（d=+4.03 vs +1.10）で再現することを確認した。
**Exclude Styles チャネルとの重複込み条件は本バッチでも未検証のまま**
（Exclude Styles 空で生成＝測ったのは本文 Avoid 単独効果 —
`examples/control/k2_suno_segments/README.md` honesty (c) 参照。また生成は Suno の
ユーザーオリジナル・カスタムモデルでの実測であり標準モデルへの一般化は未検証 —
同 honesty (g)）→ **追試を試みたが同一モデル隔離が取れず未確定のまま**
（2026-07-09、Codex #164 P2 レビュー指摘で訂正）: Exclude 欄併用追試（セル
`calm_avoid_excl`、R=4）で比較 1（Exclude 欄チャネルの grip: `calm_avoid_excl`
2794.3 vs `calm_avoid` 3079.4 → d=-1.66・tight 域）・比較 2（正味効果:
`calm_avoid_excl` vs `calm` 2438.0 → d=+1.64）を判読した。**部分的前進だが交絡に
より未確定（同一モデル isolated データ待ち）**: excl セルはモデル/生成フロー
未確認のブラウザフロー生成（`excl_plan.yaml` の `model:` 欄に自ら「未検証」と
記録済み）である一方、比較対象の `calm_avoid`/`calm` はバッチ 1（user-custom
モデル）からの再利用であり、両比較（d=-1.66 / d=+1.64）はいずれも excl セルと
batch-1 baseline という異なりうる生成条件を跨ぐ。したがってこの差分は Exclude
Styles 欄の効果ではなく generator/model の変化でも説明できてしまい（交絡・
非隔離）、「Exclude 欄はチャネルとして実際に効く」という因果断定は示唆にとどまる
（confounded）。ただし `omit_body_negative`（#163）の妥当性自体は、本文
Avoid = attractor というバッチ内実測（#162, d=+4.03）から独立に成立する別論拠で
立つため、この実装判断（本文 Avoid を止め Exclude 欄のみ使う）は Exclude grip の
確定を待たずに維持できる。詳細は
[`controllability_poc.md`](controllability_poc.md) 「K2-seg Exclude 欄併用追試
（2026-07-09・バッチ 1 増補セル）」節を参照。なお標準モデルへの一般化（honesty (g)）は
本追試でも未検証のまま残る。再アップロード音源の再抽出で fixture 収載済み
（`examples/control/k2_suno_segments/excl_rpe_fixture.json`）— 残る未収載は
発注書 verbatim のみ（セッション環境消失により復旧不能、同節 honesty 注記）。
`valley_depth_target` / `time_signature` はバッチ 2 へ繰越（未検証のまま）。
本文 Avoid については、当初の方針（musicgen の attractor 実測のみを根拠に suno 側の
送出を止めることはしない・#152 フォローアップ、2026-07-06）を、Suno 自身の実測
（#162 K2-seg Suno バッチ 1・d=+4.03・事前登録規約の attractor 確定閾値 d>=+0.8 該当）
を根拠として停止する判断に更新した。`compose/prompt_renderer.py` の
`_BACKEND_DESCRIPTORS["suno"].omit_body_negative` を True にし、suno backend は本文
"Avoid: X" セグメントの送出を止める（`negative_tags` は従来どおり保持）。musicgen と
同じ規律で、suno 実測が確定した欄のみを是正しており、`valley_depth_target` /
`time_signature` へは横展開していない。

#### M2（2026-07-13）で supersede された箇所

本節（§7.6）の `active_rate_target` / `valley_depth_target` / `time_signature`
3 欄は **バッチ M2（2026-07-13、§7.7）で再計測され supersede された**:
active_rate 天井交絡は解消（loose 確定、天井アーティファクトではない）、
valley 床（12s 定常ビート素材の物理制約）は「ツマミ死」ではなかったと確定
（dead→loose）、time_signature は per-cell ヌルゲートにより dead 維持
（3/4 初達成 1/8、§7.6 は 0/8）。上記の §7.6 実測値・本文は履歴として削除しない
（旧計測条件（12s・手組みプロンプト）での事実として残す）。設計反映済みの現行値は
`config/device_profiles/musicgen.yaml` および §7.7 を参照。

### 7.7 バッチ M2: 3 ノブ天井/床/ヌルゲート確定（2026-07-13）

**目的**: §7.6（K2-seg・12 秒手組みプロンプト計測）で疑われた交絡 2 件を解消する。
(a) `valley_depth_target` の dead 判定（観測床 0.078）が「12 秒定常ビート素材の
物理制約」か「ツマミ自体の死」かを、素材長を伸ばして分離する。(b)
`active_rate_target` の low セル天井（旧処方 "0.55" で観測 mean 0.967・headroom
0.033 しか残らず grip を過小評価しうる）を、low セル処方を拡大して解消する。
`time_signature` は §7.6 と同一処方（4/4 / 3/4）のまま再計測し、ヌルゲート規約の
機械適用を確定する。

**方法**: M1（structure 欄、§7.6 末尾の追補行 / `examples/control/musicgen_structure/`）
と同一規律 — `facebook/musicgen-small`（revision `4c8334b0…` pin）、30.6 秒クリップ
（実出力 30.54s、delay-pattern 込み・M1 と同一根拠）、`guidance_scale=3.0`、3 ノブ ×
2 セル × R=8 = 48 クリップ、`svprpe compose --format json` 実出力 verbatim のプロンプト
（手組み禁止）。セル定義: `active_rate_target` low "0.30"（§7.6 の "0.55" から拡大）/
high "0.92"（§7.6 と同値）。`valley_depth_target` low "0.15" / high "0.70"（§7.6 と
同値のまま据え置き — 変数を素材長のみにする対照実験）。`time_signature` low "4/4" /
high "3/4"（§7.6 と同値）。canonical 経路は AGENTS.md §8「ローカル決定論バッチの
canonical 条件」（#172、M1 が適用第一号・本バッチが適用第二号）— ABBA/均衡ゲートは
事前登録により非適用、fresh-process 決定論スポット検証（最遠 2 クリップの sha256 byte
一致）で出力が壁時計順序と無関係であることを実測確認、補充ゼロ（48/48）・秒単位
UTC タイムスタンプ記録の 2 条件は充足。詳細: `examples/control/musicgen_m2_knobs/`
（`m2_plan.yaml` / `m2_results_fixture.json` / `m2_measure_raw_2026-07-13.yaml` /
`m2_expected_grip.json` / `m2_takes_manifest.json` / `m2_generation_timestamps.yaml` /
`m2_determinism_spot_check.yaml`）。

#### 結果表

| knob | sensor | low/high | grip | class | 読み |
|---|---|---|---:|---|---|
| `active_rate_target` | `active_rate` | 0.30 / 0.92 | 0.414395 | **loose** | headroom を 0.033→0.192 に拡大しても grip は §7.6 の 0.394025 とほぼ同値 — 天井アーティファクトでないことが確定（canonical） |
| `valley_depth_target` | `valley_depth` | 0.15 / 0.70 | 0.3518 | **loose**（§7.6 dead を supersede） | セル値据え置き・素材長 12s→30.6s のみ変更で dead（0.152499）→loose に反転。旧 dead は valley 床（12s 定常ビート素材の物理制約）との合流で「ツマミ死」ではなかった（canonical）。ただし観測絶対値（low mean 0.100837 / high mean 0.139687）は処方値 0.15/0.70 への追従としてなお部分的 |
| `time_signature` | `time_signature`（categorical・match_rate） | 4/4 / 3/4 | combined 0.5625（machine: loose）→ **dead**（per-cell ヌルゲート発火） | **dead**（honesty 判定・§7.6 dead を維持） | per-cell: low match_rate 1.0 / high match_rate 0.125。事前登録ヌルゲート（high <= low）が 0.125 <= 1.0 で発火し combined 機械分類 loose を上書き。3/4 の初達成は high セル 1/8（time_signature_high_06、§7.6 は 0/8）— 完全不達ではなくなったが帰属（生成器不達 vs 抽出器 4/4 バイアス）は未分離 |

`summary`（機械分類・ヌルゲート適用前）: tight 0 / loose 3 / dead 0。ヌルゲート適用後の
`primary_verdict` は tight 0 / loose 2（active_rate_target・valley_depth_target）/
dead 1（time_signature）。3 欄とも `config/device_profiles/musicgen.yaml`
`control_defaults` へ反映済み（`config_reflected: true`、§7.6 の structure 欄が
`config_reflected: false` で設計判読待ちだったのとは異なり、本バッチは判定確定と
同時に config へ配線した）。

**canonical 経路**: 3 ノブ全ての verdict が `verdict_canonical: true`
（`m2_expected_grip.json` `canonical_conditions`）。fresh-process 決定論スポット検証は
バッチ最初（`active_rate_target_low_00`）/ 最後（`time_signature_high_07`）の 2 クリップ
で sha256 完全一致（2/2、`m2_determinism_spot_check.yaml`）。

**判定根拠（詳細は本節冒頭の結果表・`m2_expected_grip.json` `verdicts` を参照）**:
天井/床という 2 つの「有効帯域アーティファクト疑い」がどちらも、セル処方値を変えずに
実験条件（headroom・素材長）だけを動かすことで診断・解消できた点が本バッチの主眼。
一方 `time_signature` は combined match_rate だけを見ると loose に誤読しうる
（§7.6 の 0.5 誤読前例と同型のリスク）ため、per-cell 値の併記とヌルゲート規約の
機械適用を honesty の柱として維持した。

**計器 encode（PR #173 Codex P2、2026-07-13）**: 本節の事前登録ヌルゲートは当初
`m2_expected_grip.json` への手動転記のみだったが、`scripts/measure_grip.py` の
categorical 経路へ additive フィールド（`null_gate_fired` / `gated_classification` /
トップレベル `summary_gated`）として encode した。計器のゲート条件は **strict
`high_mean < low_mean`** — 等号は自動発火しない（両セルが各自の処方を完全実現する
ケース（K1 key: 1.0/1.0 tight）を dead に誤格下げするため。等号時の裁定は per-cell
値を見て人間側で行う。M2 の事前登録は ≤ 表記だが実データ 0.125 < 1.0 は strict でも
発火し裁定不変）。生の `classification`（stock 分類）は温存したまま、raw 出力
（`m2_measure_raw_2026-07-13.yaml`）にも `gated_classification: dead` が計器の
自動算出として記録されるようになり、将来バッチが手動反映を忘れるリスクを構造的に
縮小した。

## 8. 関連ドキュメント

- [`controllability_poc.md`](controllability_poc.md) — DD-A、K0-K3 の grip
  計測パターン全般
- [`control_profile.md`](control_profile.md) — `control_profile` / device
  profile スキーマ、PR1.5 のコンパイル配線
- [`learned_models_policy.md`](learned_models_policy.md) — annotation
  隔離原則、ライセンス gate（G4）
- [`roadmap_goal2.md`](roadmap_goal2.md) — R0-R5、R3 の位置づけ
