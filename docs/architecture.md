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

## Modules

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

## Config Files

| File | Purpose |
|------|---------|
| config/pro_baseline.yaml | Pro reference values for RPE scoring |
| config/semantic_rules.yaml | Physical → semantic mapping rules |
| config/svp_templates.yaml | SVP generation templates |
| config/synonym_map.yaml | Synonym groups for semantic similarity |

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
