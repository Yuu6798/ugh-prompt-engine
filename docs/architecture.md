# Architecture

**Status**: 計測三層（v0.2 由来: RPE 抽出 / SVP 生成 / 評価）は実装済み。楽譜トラック
（compose / transcribe / perform / roundtrip / control / calibration）は
`docs/score_centric_planning.md` / `docs/ai_performer_score_roadmap.md` 由来で
同じく実装済み — 本ドキュメントは両方をカバーする二部構成（計測三層 + 楽譜トラック）。

## Pipeline

```
Audio (WAV/MP3) → RPE Extraction → SVP Generation → Evaluation
                  ├── Physical     ├── 4 blocks      ├── RPE Score
                  ├── Semantic     └── Minimal SVP    ├── UGHer Score (4-component)
                  └── Structure                       ├── Integrated
                                                      └── Comparison (vs external SVP)
```

## Three-Layer Design (ugh-audit-core pattern)

| Layer | ugh-audit-core | svp-rpe |
|-------|---------------|---------|
| Detection | `detect()` → Evidence | `extract()` → RPEBundle |
| Calculation | `calculate()` → State | `generate()` → SVPBundle |
| Decision | `decide()` → verdict | `evaluate()` / `compare()` → scores + action_hints |
| Data structure | `frozen dataclass` | `Pydantic BaseModel` |
| Config | `YAML registry` | `config/*.yaml` |

## Modules

以下は主要ディレクトリ/ファイルの責務を 1–2 行で要約する overview。設計思想・
計算式・検証結果などの詳細は各 `docs/<topic>.md` が一次資料であり、ここでは
複製しない（各エントリの doc ポインタを参照）。

### cli/
- `svprpe` エントリポイント（typer + rich）。コマンド別モジュール
  （`extract_cmd.py` / `compose_cmd.py` / `eval_cmd.py` / `roundtrip_cmd.py` /
  `transcribe_cmd.py` / `arrange_cmd.py` / `package_cmd.py` / `recast_cmd.py` /
  `observe_cmd.py` / `verify_cmd.py` / `corpus_cmd.py` / `builds_audit.py`）+
  `builds_root.py`（builds-root 発行の共有ヘルパー、`atomic_publish_bundle`
  委譲ラッパーを含む）
- コマンドリファレンスは [`cli.md`](cli.md) が一次資料

### utils/
- `clamp.py` / `hashing.py` / `atomic_io.py` / `config_loader.py`: 全レイヤーが
  依存できるリーフユーティリティ（循環依存を避けるための集約先）
- `atomic_io.py`: atomic write（`atomic_write_bytes` / `atomic_write_text`）と
  bundle publish（`atomic_publish_bundle` — snapshot + rollback 付き、
  fail-closed な `protected_inputs` 契約とフラットファイル名の字句検証を持つ）
  の共通実装。元々 7 箇所に独立実装されていたロジックの集約先（モジュール
  docstring 参照）

### keys.py / sentinels.py
- `keys.py`: 調ラベル一致度（`weighted_key_score` = grip 用連続値 /
  `keys_enharmonically_equal` = roundtrip 用二値）
- `sentinels.py`: `transcribe` TODO センチネル（`` TODO(transcribe): `` ）の
  single source of truth

### io/audio_loader.py
- WAV/MP3/FLAC loading via librosa + soundfile
- Mono/stereo support, resampling
- AudioData + AudioMetadata models

### rpe/physical_features.py
- 10+ physical metrics (RMS, crest, valley, thickness, spectral, BPM, key, onset)
- All deterministic, same waveform → same output

### rpe/valley.py (v0.2 — strategy pattern)
- 3 methods: `rms_percentile`, `section_ar`, `hybrid` (default)
- ValleyDiagnostics with rms_p90/p10, ar_main/min, confidence

### rpe/structure_novelty.py (v0.2 — multi-feature)
- Combined novelty curve: RMS derivative + onset strength + spectral flux + chroma change
- Improved boundary detection vs v0.1 RMS-only

### rpe/structure_labels.py
- Heuristic section labels: Intro / Verse / Chorus / Bridge / Outro
- Based on energy profile ranking

### rpe/section_features.py
- Per-section feature vectors (RMS, active rate, spectral, onset, flux, chroma)

### rpe/semantic_rules.py
- Rule-based physical → semantic mapping
- Rules externalized in config/semantic_rules.yaml

### rpe/extractor.py
- Integrates physical + semantic + structure v2 + valley strategy → RPEBundle

### svp/generator.py
- RPEBundle → SVPBundle (4 blocks + MinimalSVP = 5 content fields:
  data_lineage / analysis_rpe / svp_for_generation / evaluation_criteria / minimal_svp)
- Deterministic transformation

### svp/parser.py (v0.2)
- Parse external SVP files (YAML or text/markdown)
- Returns ParsedSVP for comparison

### eval/scorer_*.py
- RPE: physical quality vs Pro baseline
- UGHer: semantic consistency (4-component: por_similarity / grv_consistency / delta_e_assessment / physical_accuracy)
- Integrated: weighted combination

### eval/comparison.py (v0.2)
- Reference RPE vs candidate SVP comparison
- SemanticDiff + PhysicalDiff + action_hints generation
- Self and cross-comparison modes

### semantic_ci/ (v1)
- Deterministic Target SVP → Expected RPE fixture comparison core
- Splits SemanticDiff into missing / preserved / over_changed
- Emits budgeted RepairSVP with preserve / restore / reduce / lock
- Records RoundTripLog hashes for reproducibility

### eval/semantic_similarity.py (v0.2)
- Token + synonym overlap for PoR similarity
- Synonym map config: config/synonym_map.yaml

### eval/anchor_matcher.py (v0.2)
- GRV anchor alignment (primary, BPM, key, duration, terms)

### eval/delta_e_alignment.py (v0.2)
- ΔE profile type + intensity matching

### batch/runner.py (v0.2)
- Multi-file batch processing
- Ranking, summary CSV/JSON, next_action.md generation

## Modules — Score Track (楽譜トラック)

Composition Score を "楽譜"、外部生成器を "演奏者" とみなす往復ループ
(`compose` → 演奏 → `measure`/`transcribe` → 診断) を実装するモジュール群。
詳細は [`score_centric_planning.md`](score_centric_planning.md) /
[`ai_performer_score_roadmap.md`](ai_performer_score_roadmap.md) を参照。

### compose/
- `models.py` / `loader.py`: `CompositionScore` 正規スキーマ + fail-fast loader
- `convert.py` / `fixity.py`: RPE 相互変換 + フィールド保存性チェック
- `device_profile.py`: 生成器別 `control_profile`（grip_class 自己記述、PR1）
- `prompt_renderer.py`: `ExternalPromptAdapter`（楽譜 → 外部生成器プロンプト、PR1.5）

### transcribe/
- `measure.py`: 音声から `CompositionScore.physical` 必須7フィールドを計測（センサー名・
  raw 値・単位・校正ノートつき）
- `score_draft.py`: loader-valid な draft Score YAML を生成（意味/散文欄は TODO sentinel）

### perform/
- `synth.py` / `performer.py`: 決定論的 `CompositionScore` 演奏者（R0 の往路）

### roundtrip/
- `harness.py` / `compare.py`: R0 往復保存性診断（Score → perform → extract → draft）
- `corpus_batch.py` / `manifest.py`: R1 再実行可能 corpus
- `repetition.py`: R3 確率的演奏者の n>1 反復バッチ + rejection sampling 選抜
- `adherence.py`: `control_profile`-tight フィールドのコンパイル保持 + roundtrip 保存判定（PR2）

### control/
- `grip.py`: K 系列の grip 効果量（操作可能パラメータの効き具合）
- `orthogonality.py`: K3 直交性行列（DCI/MIG 効果量）

### calibration/
- `manifest.py`: ジャンル/楽器語彙のラベル付きコーパス manifest
- `analyze.py` / `render.py`: `genre-calibrate`（ジャンル別特徴統計 + pair separability）
- `audit.py`: `genre-audit`（現行ルールの misfire 計測、verdict なし）

## Modules — Arrangement Track (AR 系列)

`ArrangementSpec`（既存 `CompositionScore` を破壊せず部分 override する入力）から
決定論的に derived score / provenance / diff / identity sidecar / observation を
導出するモジュール群。詳細は
[`arrangement_identity_planning.md`](arrangement_identity_planning.md) を参照。

### arrange/
- `models.py` / `loader.py`: `ArrangementSpec` スキーマ（`extra="forbid"`）+ YAML loader
- `resolver.py` / `bundle.py`: 決定論的 resolve（`DerivedScore` + field-level diff）+ compile 中核
- `identity.py`: `IdentityManifest`（hash 付き sidecar、`CompositionScore` を import しない、AR2-1）
- `contract.py`: `PreservationContract`（identity anchor と保持方針の cross-validate、AR2-2）
- `capabilities.py`: `InputCapabilityProfile`（生成器が受け取れる入力チャネルの自己記述、`control_profile` とは別モデル）
- `observe.py`: `ObservationReport`（生成後 anchor 観測の計器、verdict なし、AR4）
- `package.py`: 生成器へのハンドオフパッケージを決定論的に compile
- `pathsafe.py`: sidecar loader 共有のパス閉じ込めプリミティブ
- `section_map.py` / `verify.py`: セクション対応マップ + パッケージ検証
  （`svprpe verify`）

## Modules — Melody Track (M 系列)

主旋律の観測（M0/M1）・抽出精度検証（M2）・比較（M3）を担うモジュール群。
詳細は [`melody_observability.md`](melody_observability.md) /
[`DESIGN_M2_extraction_accuracy.md`](DESIGN_M2_extraction_accuracy.md) /
[`m2_error_model.md`](m2_error_model.md) /
[`DESIGN_M3_melody_comparator.md`](DESIGN_M3_melody_comparator.md) /
[`melody_comparator.md`](melody_comparator.md) を参照。

### melody/
- `observability.py`: `MelodyObservation` → `MelodyObservabilityReport`
  （比較を呼ばない観測ゲート。note/phrase/被覆/信頼/オクターブ誤り）
- `routing.py`: 入力種別 → 抽出経路のルーティング
- `extractors.py`: 波形 → `MelodyObservation`（optional 抽出器: CREPE /
  Melodia / Demucs vocals ラッパ、未導入時は `LearnedModelUnavailable`）
- `provenance.py`: 抽出器が読んだ重み artifact の content pin
- `accuracy.py`: M2 抽出精度検証（RPA/RCA、`mir_eval` 指標、誤差モデル）
- `representation.py` / `alignment.py` / `comparison.py`: M3 旋律比較器
  （正規化 → NW 対応付け → 多軸類似度、`MelodyComparisonReport`）

## Modules — Recast Track

`RecastProject`（既存 sidecar（`CompositionScore`/`IdentityManifest`/
`ArrangementSpec`/`InputCapabilityProfile`）への参照 + 実行方針のみの
ワークスペース定義、`recast-project/0.1`）を実装するモジュール群。詳細は
[`recast_workspace.md`](recast_workspace.md) /
[`recast_phase0_melody_spike.md`](recast_phase0_melody_spike.md) を参照。

### recast/
- `models.py` / `loader.py`: `RecastProject` スキーマ + fail-fast loader
- `plan.py`: `svprpe recast plan`/`status` の状態機械（`recast-state/0.1`）+
  `mode_overrides`（`invocation_mode` 軸、`mode-overrides/0.1`）
- `backend.py` / `backends/`: `BackendInvoker` 抽象 + `manual`（注文書）/
  `deterministic`（in-process 演奏者）/ `musicgen` バックエンド
- `report.py`: `svprpe recast ingest` の observe → report 拡張
  （`recast-report/0.1`。単一同一性スコアなし）
- `state.py` / `run_paths.py`: 実行状態の永続化 + 出力パス解決

## Config Files

| File | Purpose |
|------|---------|
| config/pro_baseline.yaml | Pro reference values for RPE scoring |
| config/acoustic_baseline.yaml / edm_baseline.yaml / loud_pop_baseline.yaml | ドメイン別 baseline |
| config/semantic_rules.yaml | Physical → semantic mapping rules |
| config/synonym_map.yaml | Synonym groups for semantic similarity |
| config/semantic_probe_axes.yaml | CLAP 意味軸バッテリー（`docs/semantic_sensor_clap.md`） |
| config/domain_profiles/ | ドメインプロファイル（music 等） |
| config/device_profiles/ | 生成器別 `control_profile` 初期値（`docs/control_profile.md`） |
| config/capability_profiles/ / config/mode_overrides/ | recast/arrange 側の capability・invocation_mode override |

（`config/*.yaml` はリポジトリ直下 + `src/svp_rpe/config/` に同梱コピーを同期
——変更時は両方を更新する契約。`config.md` に相当する独立 doc はなく、
CLAUDE.md の Architecture 節ファイル一覧が並行の参照先）

## Known Limitations (v0.2)

- Key detection uses Krumhansl-Kessler templates (no deep learning)
- Semantic layer is heuristic rule-based, not trained
- por_similarity uses token + synonym overlap (embedding in future)
- Section labels are energy-heuristic (not ML-based)
- Time signature fixed at 4/4 (low confidence)
- Batch mode is sequential (no parallel processing)
- **Scorer / comparison は semantic v2.0 の新情報（`SemanticLabel.confidence` /
  `evidence` / `layer`）を未活用** — `eval/scorer_ugher.py` と
  `eval/comparison.py` は `por_core` / `grv_anchor` / `delta_e_profile` のみを
  参照する。PR #8 で導入された evidence-bearing 層の confidence 重み付けや
  layer ごとのスコア分離は未実装。改善余地として
  [`roadmap_goal1.md`](roadmap_goal1.md) Q4 の "未活用 / フォローアップ余地"
  節（Q4-fu1）を参照
