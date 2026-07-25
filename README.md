# ugh-prompt-engine

**Composition Score（三層楽譜）を軸に、AI 音楽生成器の制御可能性を実測する研究計器。**

## Concept

このリポジトリは AI 音楽生成器（Suno / MusicGen 等）を **"演奏者"**、
`CompositionScore`（物理層 + 意味層 + 事象層の三層構造）を **"楽譜"** とみなす。

```
楽譜 (CompositionScore) → コンパイル (prompt) → 演奏 (生成器) → 測定 (RPE 抽出) → 楽譜 (draft)
```

この往復ループを実際に回し、「どのフィールドがどの生成器で本当に効くか（grip）」
「往復させても保存されるか（roundtrip preservation）」を決定論的な計器で実測する。
狙いは独自理論ではなく、**「AI が演奏者として使える楽譜」という実用物**を作ること。
背景・思想は [Composition Score Product Brief](docs/composition_score_product_brief.md) と
[AI-Performer Score Roadmap](docs/ai_performer_score_roadmap.md) を参照。

音声 → RPE（物理特徴量 + ルールベース意味層）→ SVP（構造化プロンプト）→ 評価、という
**旧来の計測三層パイプライン**は、このループを支える計測層の基盤として現役で稼働している
（`svprpe extract` / `generate` / `evaluate` / `compare`）。API キー不要、LLM 不要、
同一入力 → 同一出力の完全決定論的パイプライン。

関連プロジェクト: [ugh-audit-core](https://github.com/Yuu6798/ugh-audit-core)

## Current Status

- **Current status: PoC**
- 楽譜ループは **Suno** と **MusicGen** の 2 生成器で「楽譜→演奏→測定→楽譜」の往復が
  閉じている（`compose` → 生成器 → `measure`/`transcribe` → `roundtrip-rep`）。
  MusicGen はローカル API のためバッチ実測が可能（n=20 まで実施済み）、
  Suno は人手 UI 生成が律速で反復数が少ない。`score-adherence` CLI は
  **決定論演奏者経路**のコンパイル保持 + 往復保存チェック（外部生成テイクの
  検査は `roundtrip-rep` が担当）
- 旧来の RPE/SVP 評価スコアは **本番の音楽品質評価器として未検証**
- 各トラックの実測範囲・既知の限界は [`docs/roadmap_goal2.md`](docs/roadmap_goal2.md)（再現実証）、
  [`docs/controllability_poc.md`](docs/controllability_poc.md)（制御性 grip）、
  [`docs/musicgen_backend.md`](docs/musicgen_backend.md)（MusicGen 実測）を参照

See [Validation Status](docs/validation.md) and [Measurement Coverage](docs/coverage.md) for the
legacy pipeline's validation boundary, measurement coverage, and required ground truth.

## Setup

```bash
pip install -e ".[dev]"
```

## Quick Start

### Score track（楽譜 → 演奏 → 測定 → 診断）

```bash
# 楽譜を外部生成器向けプロンプトへコンパイル
svprpe compose examples/composition/midnight_signal/composition_score.yaml

# (プロンプトを生成器に渡して演奏を得る — Suno UI / MusicGen ローカル推論など)

# 演奏の物理フィールドを計測 / draft スコアを起票
svprpe measure track.wav
svprpe transcribe track.wav --output draft_score.yaml

# 楽譜が宣言した tight フィールドの準拠診断（決定論演奏者経路 — 外部テイクは対象外）
svprpe score-adherence examples/composition/midnight_signal/composition_score.yaml

# 決定論演奏者での往復保存性診断 (R0) / 外部生成テイク群の反復診断 (R3)
svprpe roundtrip examples/roundtrip/synth_01_source.yaml
svprpe roundtrip-rep examples/roundtrip/synth_01_source.yaml takes_manifest.json
```

### Legacy measurement track（RPE/SVP）

```bash
# Full pipeline: extract → generate → evaluate
svprpe run track.wav --output-dir ./output

# Individual steps
svprpe extract track.wav -o rpe.json
svprpe generate rpe.json --format yaml
svprpe evaluate --audio track.wav

# Compare reference vs candidate
svprpe compare --reference-audio ref.wav --candidate-audio gen.wav

# Deterministic semantic CI fixture check
svprpe ci-check examples/semantic_ci/pass_perfect/target_svp.json \
  examples/semantic_ci/pass_perfect/observed_rpe.json

# Batch processing
svprpe batch ./audio_files --svp-dir ./designs --mode compare --output-dir ./results

# Help
svprpe --help
```

全コマンド・全オプションのリファレンスは [CLI Reference](docs/cli.md) を参照
（`score-adherence` / `roundtrip-corpus` / `roundtrip-rep` / `genre-calibrate` /
`genre-audit` 等を含む）。

## Project Structure

```
src/svp_rpe/                # Main package (src layout)
├── cli/                    # typer CLI (svprpe command。コマンド別モジュール)
├── keys.py                 # 調ラベル一致度 (weighted_key_score / keys_enharmonically_equal)
├── sentinels.py            # 共有 sentinel 値
├── io/                     # WAV/MP3 loading + optional Demucs source separation
├── rpe/                    # RPE 抽出層 (physical/semantic/structure/valley/learned)
├── svp/                    # SVP 生成層 (RPE → SVPBundle)
├── eval/                   # 評価層 (RPE score / UGHer score / comparison)
├── batch/                  # 複数ファイルの一括処理
├── compose/                # CompositionScore: models/loader/convert/fixity/renderer
│                           #   (control_profile・ExternalPromptAdapter を含む)
├── transcribe/             # 演奏音声 → CompositionScore 物理フィールド計測/起票
├── perform/                # 決定論的 CompositionScore 演奏者 (synth + performer)
├── roundtrip/              # 楽譜往復保存性診断 (R0 harness / R1 corpus / R3 repetition)
├── semantic_ci/            # Target SVP → Expected RPE → Diff → Repair SVP
├── control/                # 制御トラック: grip 効果量 / K3 直交性行列
├── calibration/            # ジャンル/楽器語彙コーパス校正 (genre-calibrate / genre-audit)
├── arrange/                # ArrangementSpec: 決定論的 override/resolve/compile + identity/capability/observation sidecar (AR 系列)
├── config/                 # config/*.yaml のパッケージ同梱コピー
└── utils/                  # config loader・clamp 等の共通ユーティリティ

config/                     # External configuration (src/svp_rpe/config/ と同期)
├── pro_baseline.yaml       # RPE Pro baseline values
├── *_baseline.yaml         # ドメイン別 baseline (acoustic/edm/loud_pop)
├── semantic_rules.yaml     # Physical → semantic rules
├── synonym_map.yaml        # Synonym groups (UGHer scorer)
├── domain_profiles/        # ドメインプロファイル (music)
└── device_profiles/        # 生成器別 control_profile 初期値 (suno.yaml / musicgen.yaml)

tests/                      # pytest
docs/                       # Design documents
examples/                   # sample_input/ + expected_output/ + composition/roundtrip/calibration fixtures
```

## Key Metrics (one-line definitions)

- **RPE score / UGHer score** — 旧来の物理/意味整合性スコア。定義は [Metrics](docs/metrics.md)
- **grip** — ある楽譜フィールドを動かすと生成器の出力が実際に動く効果量（体温計ではなくハンドル）。
  定義・実測は [Controllability PoC](docs/controllability_poc.md)
- **roundtrip preservation** — 楽譜 → 演奏 → 抽出 → draft 楽譜の往復でフィールド値が保たれるか
  の 4 値診断（`preserved` / `calibration_disagreement` / `sensor_blind` / ...）。verdict では
  なく計器。定義は [Roundtrip Preservation](docs/roundtrip_preservation.md)
- **device profile / control_profile** — 生成器ごとに「どのフィールドが tight/loose で効くか」
  を楽譜自身に持たせる自己記述。定義は [control_profile](docs/control_profile.md)

## Development

```bash
# Lint
ruff check .

# Test
pytest -q --tb=short

# CLI help
svprpe --help
```

## Documentation

- [Validation Status](docs/validation.md) — PoC label, unvalidated metrics, and required ground truth
- [Measurement Coverage](docs/coverage.md) — What the pipeline can measure, partially measure, and cannot measure
- [Architecture](docs/architecture.md) — Two-part design (measurement three-layer + score track), module overview
- [Metrics](docs/metrics.md) — Physical metric definitions and Pro baseline
- [Migration Notes](docs/migration.md) — RPE schema migration: SemanticRPE 1.0→2.0 (por_surface to evidence-bearing SemanticLabel), fail-fast policy, regeneration steps
- [CLI Reference](docs/cli.md) — Command usage (all `svprpe` subcommands)
- [Semantic CI Product V1](docs/semantic_ci_product_v1.md) — Target SVP → Expected RPE → Diff → Repair SVP core
- [Roadmap](docs/roadmap.md) — PoC milestones (M0–M5) + Pre-prototype plan (P1–P5)
- [Goal 1 Roadmap](docs/roadmap_goal1.md) — Quantitative observation completion plan (Q0–Q5)
- [Goal 2 Roadmap](docs/roadmap_goal2.md) — Reproduction-proof completion plan (R0–R5): bidirectional reproducibility decomposed (grip × calibration × transcription), round-trip preservation across the K/Q/T/C tracks
- [Learned Models Policy](docs/learned_models_policy.md) — Adopt/reject/hold policy for learned audio-annotation models (Q4'): isolation into LearnedAudioAnnotations, no mixing into rule evidence, OSS license constraints
- [AI Music DAW Vision](docs/ai_music_daw_vision.md) — Extension track: SVP as "MIDI for AI music" standard, survivor性 framework, score-vs-performance separation, PoC (1) integration into Q0
- [Composition Score Product Brief](docs/composition_score_product_brief.md) — Composition Score product definition: three-layer composition language, canonical schema, MVP scope, PoC 1–5 roadmap
- [Composition PoC Planning](docs/composition_poc_planning.md) — Implementation plan for Composition Score: C1–C6 phases, design decisions
- [Composition PoC Report](docs/composition_poc_report.md) — C4 end-to-end demo results (deterministic path): synth performer, two-take needle comparison, sensor findings
- [Controllability PoC Planning](docs/controllability_poc.md) — Control track PoC (K-series): parameters as control knobs not eval values, grip effect-size definition, K0 → K2 Suno transfer, K3 orthogonality matrix (DCI/MIG effect-size reformulation, real Suno mini-matrix, device-specific sign flip, MusicGen full matrix with in-batch noise ceiling)
- [AI-Performer Score Roadmap](docs/ai_performer_score_roadmap.md) — Merge roadmap (4 PRs; PR1.5 added in the 2026-06-29 design discussion) toward an "AI-as-performer score": merges prior art (MIR/CLAP/DCI-MIG/controllability-eval/EPR) with accumulated knowledge (K-series grip / roundtrip fixity / genre bias). PR1 = `control_profile` schema (score knows its honored channels), PR1.5 = control_profile-aware compile (wire the existing `ExternalPromptAdapter` so the score→performance loop closes on Suno — the practical artifact's core), PR2 = score-adherence test + CLAP (repositioned as a semantic-layer reader), PR3 = K3 orthogonality (DCI/MIG) + per-generator device profiles. Determinism = physical-layer guarantees / non-determinism = semantic-layer advice; additional generators come after the Suno route is established
- [control_profile](docs/control_profile.md) — PR1/PR1.5: `CompositionScore.control_profile` schema (per-generator grip_class self-description), sparse-allowed validation (vs fixity's full coverage), field→backend channel mapping, K2-derived Suno initial data; PR2 = `score-adherence` check
- [Score-centric Planning](docs/score_centric_planning.md) — Score-first reorganization: bidirectional reproducibility principle, transcription track (T-series, T0–T2, implemented), Q-series redefined as instrument calibration, semantic-layer sensors as future scope
- [Event Roundtrip](docs/event_roundtrip.md) — R4 event-level roundtrip admission plan for chord progression: `chord_progression` × `compute_chord_events` × chord-series match rate
- [Round-trip Case Studies](docs/roundtrip_case_studies.md) — Real-Suno round-trip/controllability results log and R1 corpus manifest path: instrument effective-band, physical-fixed/semantic-swapped A/B, bidirectional test success (BPM caveat), BPM 89.1 attractor suspicion
- [Roundtrip Preservation](docs/roundtrip_preservation.md) - R0 deterministic score -> performance -> transcription preservation diagnostics and K1 cross-check
- [Metamorphic Probe](docs/metamorphic_probe.md) — Sweeps render_sample × real extract to auto-verify grip/calibration/orthogonality/determinism (Hypothesis): centroid=tight grip, high-band brightness=blind sensor, bpm octave error unflagged by R2-2a
- [R1 Corpus Screen](docs/roundtrip_corpus_screen.md) — Real Suno screen + A/B/C control experiment: fast tracks' low BPM is extractor halving, not Suno infidelity (start_bpm=180 recovers ~172); the "attractor" is prior × BPM-grid selection; breakbeat hypothesis falsified; R2-2a misses grid-quantized 1.93× halvings
- [Genre Calibration Planning](docs/genre_calibration_planning.md) — Semantic genre/instrument vocabulary expansion: config-ize the hardcoded `cultural_context` mislabel (orchestral→bass-music), build a Suno-generated auto-labeled corpus to bypass the licensing bottleneck (correct generator bias with real anchors), split into Phase A (config-ization) / B (corpus) / C (verification)
- [Lyrics as Semantic Anchor](docs/lyrics_semantic_anchor.md) — 2026-07-01 arrange-demo finding: vocals/lyrics perturb key/BPM readings (confound real, direction not fixed — n=1 "vocal anchors tonic" refuted at n=2) while the perceived "メリハリ" (song-like contrast) they add is invisible to physical dynamic_range — lyrics anchor the *semantic* layer, currently only the ear can sense it; hypothesis + n≥3 test design
- [MusicGen Backend](docs/musicgen_backend.md) — Local MusicGen generation track: PR A (manual runbook + `musicgen` extra, no real inference, CI-safe) / PR B (real batch → K2-shaped fixture + `device_profiles/musicgen.yaml` + R3 measurement at n=5 and n=20) / PR C (R3 harness); K3-2b full orthogonality matrix (§7.4) and CLAP cross-validation② (§7.5) added on the same track; DD-A deterministic contract (fixture→grip only in CI), generator-side so annotation isolation policy does not apply, weights license verified CC-BY-NC-4.0 (research-instrument only, weights never bundled)
- [Semantic Sensor: CLAP](docs/semantic_sensor_clap.md) — Wires CLAP as an extraction-stage semantic sensor (`svprpe extract --clap-semantic`): reads SOURCE audio against a fixed semantic-axis battery (`config/semantic_probe_axes.yaml`) via A/B `contrast_fit`, isolated in `LearnedAudioAnnotations.semantic_axes` (schema_version 1.1) — extends the prior post-hoc fixture comparator (generated-output only) into true extraction-time semantic sensing; axis calibration (2026-07-04, real inference, `scripts/calibrate_semantic_axes.py`): vocal/energy proven, brightness bpm-confounded, acousticness/warmth exploratory, valid band = real produced music
- [Lyrics Transcription Sensor](docs/lyrics_transcription_sensor.md) — faster-whisper symbolic lyrics-content sensor complementary to CLAP's continuous grips: input side (`svprpe extract --lyrics`, isolated in `LearnedAudioAnnotations.lyrics_transcription`, schema_version 1.2) and output side (`svprpe lyrics-adherence` adherence instrument, no verdict); fake-backend only, real inference pending
- [Arrangement Identity Planning](docs/arrangement_identity_planning.md) — AR0–AR4 plan for arrangement while preserving work identity: sidecar-first policy, deterministic score derivation,
  identity artifacts, backend delivery, and post-generation observation. M1 is limited to Score-level preservation of `semantic.core` and `physical.key`;
  auditory identity remains a later milestone.
- [Work Identity Roadmap](docs/work_identity_roadmap.md) — WI0–WI4 identity-judgment track: defines work identity via declarative contracts × discriminability judgment × human calibration × honest coverage accounting; melody/lyrics sensors → D-1 thresholds → discriminability harness → identity proxy v0 → institutionalization
- [WI1 D-1 Thresholds](docs/wi1_d1_thresholds.md) — WI1 deviation-distribution + D-1 threshold Design Memo: MusicGen unattended n=20 structure/harmony deviation distribution, §4 edit-decomposition algorithm, §5 D-1 classification policy v0 (within 14 / outside 6, reference policy), harmony left unclassified in v0
- [WI2 Discrimination Harness](docs/wi2_discrimination_harness.md) — WI2 discrimination-judgment harness: 4-cell 13-clip MusicGen discrimination batch, 5-axis identity-rank instrument, cell P's byte-identical channel-death proof, 12/12 deterministic rank reproduction; discrimination succeeds only on bpm (structure/harmony/brightness non-discriminating, key unstable)
- [WI3 Human Calibration](docs/wi3_human_calibration.md) — WI3 human calibration v0: 12 pre-registered pairs judged by a single-judge baseline (blind seed 8400); identity proxy v0 is the empty set (0/5 axes adopted); even a same-score regeneration pair was judged "different song"; the three instrument errors all mispredict P3 negative-control pairs as "same" but via axis-specific mechanisms (bpm attractor / structure length bias / key parallel-mode drift); the real Suno pair (P5) is deferred to a second tranche
- [Recast Phase 0 Melody Spike](docs/recast_phase0_melody_spike.md) — Recast Phase 0 gate spike: deterministic pyin note extraction degenerates (1–4 notes/take), same-song vs cross-song similarity distributions fully overlap → gate fails; PR4 hard anchor replaced with chords+structure, melody marked `not_observed` (existing D-1 `no_sensor` path, no new vocabulary)
- [Recast Workspace](docs/recast_workspace.md) — Recast track summary (PR0–PR6): the `recast-project/0.1` schema, the run-state machine, the CLI flow (init→plan→run→ingest→status), the `invocation_mode` axis (cover vs. prompt_only measured differences), the Phase 0 gate outcome (melody=not_observed; chords+structure are the hard pillar), the "we only promise what we can measure" posture (D-1, no single identity score), and how to run the golden path fixture
- [Melodia Confidence Scale Diagnosis](docs/melodia_confidence_scale.md) — why the Melodia route returned `voiced_coverage 0.000` on every M1-real material: a clash between the extractor's confidence semantics and the frozen gate. Melodia's own pre-gate output on the actual `demucs_vocals_then_melodia` route carries pitch on 48.1% of frames (and pyin clears the floor on 1100 frames of the very same stem), so the stem-mismatch hypothesis is ruled out by measurement; sampleRate/dtype misreads are ruled out by ±0.1-cent pitch accuracy. A parameter sweep then overturned the initial verdict: `magnitudeCompression` (adapter uses Essentia's default 1.0, and exposes no way to change it — the route itself never clears the floor) moves the confidence range: direct Essentia probes on the route's own stem clear the floor on 2548 frames at 0.8 and 28633 at 0.5, so the root cause is a three-way relation between the frozen floor, the adapter's configuration, and Melodia's range, not a two-way clash (across **every input measured**, Melodia's `pitchConfidence` peaked between 0.1487 and 0.2947 — raw mixes 0.176/0.254, the synthetic fixture 0.2947, the real Demucs vocals stems 0.1487–0.2216 — and never reached the frozen `voiced_confidence_floor` of 0.30, leaving zero voiced frames; pyin's `voiced_prob` tops out at 1.0, so the two are not on a scale a single floor can judge). Pitch itself is recovered correctly — the instrument was never connected. No universal upper bound is proven, so the claim is scoped to the measured corpus. How to normalize is a pre-registration matter, left undecided (diagnosis only)
- [Melody Observability M0/M1](docs/melody_observability.md) — melody observation sensor (finding the observable band): the observation gate (`melody/observability.py`: `MelodyObservation`→`MelodyObservabilityReport`; asks "can we observe it?" before any comparison), input-kind→extractor routing, optional slow-lane extractors (CREPE=MIT code but bundled weights unpinned → manual, no extra / Melodia=**AGPL** essentia, also manual / Demucs vocals wrapper=`separate` extra, `LearnedModelUnavailable` when absent), a pre-registered `melody_bench` registry + synthetic fixtures. M1c Go/No-Go: the pyin route's gate is established by measurement (monophonic=sufficient / chord pad=insufficient); the real target band (Suno vocals stems) is machine-dependent and deferred to the slow lane — no auto-advance to M2. M1-real measurement wiring: a weight-provisioning gate (availability is 3-valued — not installed / weights provisioned / **installed but weights not provisioned** → `unavailable`, never a runtime download) plus stem/weights hash emit so the evaluator's provenance pins can actually be recorded
- [AGENTS.md](AGENTS.md) — Claude × Codex orchestration protocol (Task Brief / Completion Summary templates)

## License

MIT
