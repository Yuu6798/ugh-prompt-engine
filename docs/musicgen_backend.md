# MusicGen ローカル生成トラック — 設計 doc

Status: PR A / PR B / PR C 実装完了（PR B 実測 2026-07-03、§7）
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
  保証しないため、モデル id を知る唯一の場所である runbook が
  `profile_scope_advisory`（`scripts/collect_musicgen_takes.py`）で
  `--model-id` ≠ small のとき stderr に注意喚起する（プロンプト本文・生成は
  不変 — #128 の本文不変規律）。バリアントを実測したら、その時点で
  プロファイルのモデル別分割を再検討する。

### 7.2 R3 初実測（`examples/roundtrip/musicgen_r3_rep_2026-07-03.json`）

`examples/roundtrip/musicgen_r3_source.yaml`（C major / bright / 120bpm・
`target_backend: musicgen`）から `perform` で n=5 テイクを生成し、
`svprpe roundtrip-rep` で R3-1/2/3 を初実測（takes manifest は
`examples/roundtrip/musicgen_r3_takes_manifest.json`、音声はローカルのみ）:

| field | preserved | rate | 備考 |
|---|---:|---:|---|
| key | 2/5 | 0.4 | 外れは E minor / F minor×2（calibration_disagreement） |
| brightness | 3/5 | 0.6 | 1 本 neutral band（sensor_blind）・1 本 dark |
| bpm | 2/5 | 0.4 | 117≈120 で保存 2、96×2、undetected 1（R3 選抜からは除外済みのノブ） |
| time_signature | 5/5 | 1.0 | 4/4 |
| active_rate_target | 0/5 | 0.0 | 全テイク 0.98–1.00 — MusicGen は壁一面の密度で鳴らす |
| stereo_width | 0/5 | 0.0 | **MusicGen small はモノラル出力**＝5/5 sensor_blind |

- **R3-3（rejection sampling = 「選択 = 制御」）が実データで初めて機能**:
  `selected_take_id = musicgen_r3_source_04`（選抜フィールド key / brightness の
  両方を保存する唯一のテイク）が `preserved_field_count` 基準で機械的に特定された。
  単発生成では key 保存率 0.4 だが、n=5 生成 + 計器選抜で楽譜に最も近いテイクを
  決定論的に拾える——grip が弱いノブでもセンサーのみで制御チャネルが回復する、
  という R3-3 の設計仮説の最初の実証点（verdict ではなく計器の読みの記録）。
- 付随観測: MusicGen 出力はモノラル（`stereo_width` はセンサー盲）、
  `active_rate_target` は常に上限貼り付き（0.90–0.95 指定に対し 0.98–1.00）。
  楽譜からの MusicGen 演奏では両欄は現状制御不能として扱うのが妥当。

## 8. 関連ドキュメント

- [`controllability_poc.md`](controllability_poc.md) — DD-A、K0-K3 の grip
  計測パターン全般
- [`control_profile.md`](control_profile.md) — `control_profile` / device
  profile スキーマ、PR1.5 のコンパイル配線
- [`learned_models_policy.md`](learned_models_policy.md) — annotation
  隔離原則、ライセンス gate（G4）
- [`roadmap_goal2.md`](roadmap_goal2.md) — R0-R5、R3 の位置づけ
