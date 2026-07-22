# CLAUDE.md — ugh-prompt-engine (svp-rpe)

このファイルは Claude Code / Claude Agent SDK がこのリポジトリで作業する際の
普遍的な運用ポリシーをまとめる。リポジトリ固有の設計詳細は
`docs/<topic>.md` と各 `README.md` に分離する。

## Project Overview

UGHer エコシステムのプロンプト基盤。
音楽ファイル（WAV/MP3）から RPE を抽出し、決定論的に SVP を生成し、
UGHer 系 + RPE 系の二系統評価を行うローカル完結型ツール。

- **RPE (Reverse Prompt Engineering)**: 音声 → 物理特徴量 + 意味層
- **SVP (Semantic Vector Prompt)**: RPE → 構造化プロンプト生成
- **Eval**: UGHer 系 + RPE 系スコアリング

API キー不要、LLM 不要、同一入力 → 同一出力の完全決定論的パイプライン。

関連: [ugh-audit-core](https://github.com/Yuu6798/ugh-audit-core)
実装プラン: [svp_rpe_implementation_plan.md](https://github.com/Yuu6798/ugh-audit-core/blob/main/docs/svp_rpe_implementation_plan.md)

## Tech Stack

- **Language**: Python 3.11+
- **Build**: setuptools (pyproject.toml, src layout)
- **Lint**: ruff (line-length=100, target py311)
- **Test**: pytest
- **CI**: GitHub Actions (Python 3.11/3.12)
- **Audio**: librosa + soundfile
- **Models**: Pydantic v2
- **CLI**: typer + rich
- **Config**: YAML (PyYAML)
- **License**: MIT

## Advisor Strategy（モデル運用方針）

**2026-07-05 改訂**（Fable=設計専任化。高価な Fable を設計判断に最大集中させる措置）:

開発フローは **2 本柱**: Claude→Codex ルート（Workflow 節）と **Claude 完結ルート**
（Codex 非経由で in-session 完走）。Claude 完結実装の本ルートは
**「Fable 設計 → Opus/Sonnet 実装・実行・検証 → Fable 判定」**。

- **メインエージェント**: Fable 5 — **設計・設計判断のみ**（Design Memo 起草、
  レビュー指摘の採否判定と対応方針の設計、結果解釈・数値判読、メモリ管理）。
  **実装・実行・検証など実際に手を動かす作業はサブエージェントに委譲し、
  Fable 自身は行わない**。Fable 非稼働セッションでは Opus が代行
- **実装・探索サブエージェント**: Sonnet 固定（実装、探索・読み取り中心の調査タスク）
- **実行・検証・非設計分析サブエージェント**: Opus または Sonnet（実測検証・
  E2E/スモーク実行、設計判断を伴わないレビュー指摘の分析・トリアージ）

運用細則（委譲固定費の逆ザヤ防止。Web ツール含む全ツールに適用）:

- **マイクロ操作例外**: 単発の状態確認・git 操作・メモリ読み書き・質問など **1–2
  コールかつ結果ペイロードが軽い操作のみ** Fable 直接可。実装・実行・検証・探索・
  複数ソース Web 調査に加え、**レビュースレッド取得/返信投稿など結果が重い操作は
  コール数によらず委譲**（#149 実測: diff_hunk 同梱の戻り値が主燃焼源）
- **生データ様式**: 検証・計測の委譲時は生データをファイル保存させ Fable が直接
  Read して判読する（要約経由の判断劣化防止。判読=設計判断で Fable の職務）
- **事後監査**: wrap-up で「Fable が直接実行した作業」を振り返りに明記（skill 参照）
- **長時間検証の委譲は自動バックグラウンド化前提で設計**: 同期実行 3 点固定文言
  （①フォアグラウンド ②timeout は Bash ツールの timeout パラメータで明示・シェル
  `timeout` ラッパ代用不可 ③run_in_background / `&` / nohup 禁止）は維持するが、
  ハーネスが長時間 Bash を自動バックグラウンド化する実観測（07-09）により文言遵守を
  完了保証と見なさない。完了判定は報告文でなく成果物（結果ファイル・exit code）で行う
- **孤児化の回復手順**: 委譲した検証が停止・環境再起動等で孤児化したら、SendMessage で
  当該エージェントに同期再実行を指示して回復する（新規 spawn より文脈を温存できる）

Agent ツールで spawn する際は必ず `model` を明示する（省略 NG: メインと同モデルで
動きコスト効率が下がる。実装・探索は Sonnet 固定）。

### レビュー対応の振り分けルール（2026-07-02 新設、2026-07-05 改訂）

- **マシン非依存**（コード・テスト・docs・fixture メタデータ）= **Claude 側で対応**
  （採否判定・方針設計=Fable / 修正の実装・検証=Sonnet/Opus 委譲）。
  `codex/*` ブランチへの push 可、対応内容をレビュースレッドに明記する
- **マシン依存**（実音源・実重みハッシュ・Suno 生成・G4 ライセンス目視）= **Codex / User**
- 判断が割れたら Fable が設計判定を先に出して振り分ける

## Workflow（Codex × Claude × User 分業オーケストレーション）

このリポジトリは **設計レビューと実装を分業** する。**2026-06-02 改訂**:
通常開発の主フローを **Claude Code が設計、Codex が実装** に戻す。
Claude Code は仕様整理とレビューに集中し、Codex がローカル実装・検証・PR 作成・
指摘対応を担当する。

- **Claude Code (Fable 5 / 非稼働時 Opus)** — タスク Brief 読解、Design Memo 起案、実装方針 / 受け入れ条件 / リスク / テスト観点の整理、PR レビュー、再レビュー、メモリ管理
- **Codex** — Design Memo を受けて実装、PR 作成、レビュー指摘対応、セルフレビュー
- **User** — エージェント間の橋渡し、最終マージ判断、ループのトリガー

サイクル:

1. Claude Code が `AGENTS.md` 規定の **Task Brief** を読み、**Design Memo**（実装方針 /
   受け入れ条件 / リスク / テスト観点）を起こす
2. User が Design Memo を Codex に渡して実装依頼
3. Codex が `codex/<topic>` ブランチで実装 → PR 作成（本文は **Completion Summary** 形式）
4. User が PR URL を Claude Code に共有
5. Claude Code が PR をレビュー → 指摘コメント
6. Codex が指摘対応してコミット追加 → User が Claude Code に再レビュー依頼
7. Claude Code が再レビュー（Approve または再指摘）
8. User がマージ → 次の Task Brief へ

**Claude Code は通常、本リポジトリで実装コードを書かない**（PR レビューコメント、
Design Memo、設計仕様、メモリ管理は担当）。例外的な小規模 fix-up のみ `claude/<topic>`
ブランチで行う。Codex は docs / AGENTS.md / 設計仕様 / 実装を担当できる。
コミュニケーション・フォーマット規約の詳細: [`AGENTS.md`](AGENTS.md)

## Session Memory（永続記憶ワークフロー）

セッション間の記憶喪失を防ぐため、`.claude/memory/` にセッションサマリーを蓄積する。

### 起動時ルール

1. セッション開始時に `.claude/memory/_index.md` を読み、過去の決定事項・コンテキストを把握する
2. 直近 3 件のサマリーファイルは必要に応じて詳細を参照する
3. 過去の設計判断に関する質問には、サマリーを確認してから回答する

### 終了時ルール（自動トリガー）

ユーザーがセッション終了を示す発言（「今日はここまで」「セッション終了」
「また明日」「お疲れ様」「done for today」「that's all」）をしたら、または
`/wrap-up` が実行されたら、**確認なしで wrap-up skill を実行する**。

`.claude/skills/wrap-up/SKILL.md` が終了手順全体の **source of truth**
（8 ステップ: reflection 保存 → `_index.md` 追記 → archive → STATUS.md
sweep → discipline ゲート。アーカイブ TTL 表・サマリーフォーマット・
アンチパターン集も skill 側に集約）。本ファイルと skill が乖離した場合は
**skill が勝つ** — このポインタを直し、skill を古い CLAUDE.md に合わせて
編集してはならない。

discipline ゲート: `.claude/memory/` の直 main push の前に必ず
`python -m pytest tests/discipline/ -q` を全パスさせる（例外は post-hoc
検出のみのため、違反は main を直接赤くする）。

## Architecture

```
src/svp_rpe/
├── cli/                       # typer CLI (svprpe command。コマンド別モジュール + builds_root ヘルパー)
├── keys.py                     # 調ラベル一致度: weighted_key_score (grip 用連続値) / keys_enharmonically_equal (roundtrip 用二値)
├── sentinels.py                 # transcribe TODO センチネル (`TODO(transcribe):`) の single source of truth
├── io/
│   └── audio_loader.py        # WAV/MP3 loading + AudioMetadata
├── rpe/                       # RPE 抽出層
│   ├── models.py              # PhysicalRPE, SemanticRPE, RPEBundle
│   ├── extractor.py           # 統合パイプライン
│   ├── physical_features.py   # librosa-based 物理特徴量
│   ├── semantic_rules.py      # ルールベース意味層
│   ├── structure.py           # セグメント分割
│   ├── structure_labels.py    # セクションラベル付与
│   ├── structure_novelty.py   # novelty 検出
│   ├── section_features.py    # セクション粒度特徴
│   ├── valley.py              # valley 検出 (--valley-method)
│   └── learned/               # 学習モデルアダプタ (basic_pitch / beat_this / panns / lyrics_adapter)
├── svp/                       # SVP 生成層
│   ├── models.py              # SVPBundle, MinimalSVP
│   ├── generator.py           # RPE → SVP 変換
│   ├── parser.py              # 既存 SVP の読み込み (compare 用)
│   ├── render_yaml.py         # YAML 出力
│   └── render_text.py         # Markdown/TXT 出力
├── eval/                      # 評価層
│   ├── models.py              # RPEScore, UGHerScore, IntegratedScore
│   ├── scorer_rpe.py          # RPE 物理スコア
│   ├── scorer_ugher.py        # UGHer 意味スコア
│   ├── scorer_integrated.py   # 重み付き統合
│   ├── anchor_matcher.py      # アンカーマッチング
│   ├── comparison.py          # compare コマンド本体
│   ├── delta_e_alignment.py   # ΔE 整列
│   ├── diff_models.py         # diff データ構造
│   ├── semantic_similarity.py # 意味類似度
│   └── lyrics_match.py        # 歌詞転写照合計器 (lyrics-adherence, verdict なし)
├── compose/                   # CompositionScore: models/loader/convert/fixity/renderer
├── semantic_ci/                # Target SVP → Expected RPE → Diff → Repair SVP
├── transcribe/                 # CompositionScore physical field measurement
├── perform/                    # 決定論的 CompositionScore 演奏者 (synth + performer)
├── roundtrip/                  # R0 往復保存性診断 (harness / compare / corpus_batch)
├── control/                    # grip 効果量 (制御トラック K 系列)
├── calibration/                # ジャンル/楽器語彙コーパス校正 (genre-calibrate / genre-audit)
├── arrange/                    # ArrangementSpec: 決定論的 override/resolve/compile + identity/capability/observation sidecar (AR 系列。models/resolver/bundle/contract/identity/capabilities/observe/package/verify/pathsafe/loader)
├── recast/                    # RecastProject: 既存 sidecar (CompositionScore/IdentityManifest/ArrangementSpec/InputCapabilityProfile) への参照+実行方針のみのワークスペース定義 (recast-project/0.1。models/loader/plan/state。PR1 = schema+loader+fixture、PR2 = `svprpe recast plan`/`status` CLI + 状態機械 (recast-state/0.1) + mode_overrides (★invocation_mode 軸、mode-overrides/0.1))
├── batch/                     # バッチ処理
│   ├── runner.py              # batch コマンド本体
│   └── discovery.py           # 入力ファイル発見
└── utils/
    ├── config_loader.py       # YAML config loading
    └── clamp.py                # float クランプ共通化 (max(lo,min(hi,v)); 旧 8 重複 _clamp の集約先)

config/                        # リポジトリ直下 + src/svp_rpe/config/ に同梱コピー (同期)
├── pro_baseline.yaml          # RPE Pro baseline values
├── acoustic_baseline.yaml     # ドメイン別 baseline (acoustic)
├── edm_baseline.yaml          # ドメイン別 baseline (EDM)
├── loud_pop_baseline.yaml     # ドメイン別 baseline (loud pop)
├── semantic_rules.yaml        # physical → semantic mapping rules
├── synonym_map.yaml           # 同義語マップ (UGHer scorer 用)
├── domain_profiles/music.yaml # ドメインプロファイル (music)
└── device_profiles/           # 生成器別 control_profile 初期値 (suno.yaml / musicgen.yaml)

tests/                         # pytest
docs/                          # design documents
examples/                      # sample_input/ + expected_output/
```

### 設計ドキュメント索引

新規 `docs/<topic>.md` を作成したらこの表に 1 行追加する（README の同様の表も同期）。

| ドキュメント | 内容 |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | パイプライン三層設計、モジュール責務、config 役割、v0.2 既知の制限 |
| [`docs/metrics.md`](docs/metrics.md) | RPE 物理指標の定義式、Pro baseline 値、UGHer 4 成分スコアリング、valley 3 戦略 |
| [`docs/migration.md`](docs/migration.md) | RPE スキーマ移行ノート: SemanticRPE 1.0→2.0（`por_surface` を evidence-bearing `SemanticLabel` 化）、fail-fast 移行方針、RPE 再生成手順 |
| [`docs/cli.md`](docs/cli.md) | CLI コマンドのリファレンス: extract / generate / compose / measure / transcribe / evaluate / compare / ci-check / run / batch / audit / roundtrip / roundtrip-corpus / roundtrip-rep / score-adherence / genre-calibrate / genre-audit / verify |
| [`docs/semantic_ci_product_v1.md`](docs/semantic_ci_product_v1.md) | semantic CI V1: Target SVP → Expected RPE → fixture比較 → Repair SVP |
| [`docs/roadmap.md`](docs/roadmap.md) | PoC (達成済み) と Pre-prototype マイルストーン (P1–P5)、推奨実行順 |
| [`docs/roadmap_goal1.md`](docs/roadmap_goal1.md) | 目的1（定量観測）完成までのフェーズ Q0–Q5、完成定義、クリティカルパス |
| [`docs/roadmap_goal2.md`](docs/roadmap_goal2.md) | 目的2（再現実証）完成までのフェーズ R0–R5、双方向再現性の分解（grip×校正×採譜）、K/Q/T/C 各トラックを束ねる往復保存性の実証計画 |
| [`docs/learned_models_policy.md`](docs/learned_models_policy.md) | 学習モデル音声アノテーション層の採用/不採用/保留ポリシー（Q4'）: `LearnedAudioAnnotations` への隔離原則、ルール evidence 非混入の規約、OSS ライセンス制約 |
| [`docs/validation.md`](docs/validation.md) | Q0-5 baseline: 5 曲の対真値比較（BPM / key / segment）、Q0 完了基準のチェック、Coverage Matrix |
| [`docs/coverage.md`](docs/coverage.md) | 計測可能 / 部分的 / 計測不可の三分割マトリクス、`rpe_score` / `ugher_score` の解釈ルール、validation データセット概要 |
| [`docs/ai_music_daw_vision.md`](docs/ai_music_daw_vision.md) | 拡張検証トラック: SVP を「AI 音楽の MIDI」標準として確立し DAW の核とする長期ビジョン、survivor 性概念、楽譜/演奏分離、PoC (1) の Q0 統合 |
| [`docs/composition_score_product_brief.md`](docs/composition_score_product_brief.md) | Composition Score プロダクト定義: 三層作曲言語の思想、正規スキーマ、MVP 範囲、PoC 1–5 ロードマップ |
| [`docs/composition_poc_planning.md`](docs/composition_poc_planning.md) | Composition PoC 実装計画: C1–C6 フェーズ、ブリーフ下流の実装詳細・設計判断ログ |
| [`docs/composition_poc_report.md`](docs/composition_poc_report.md) | C4 E2E デモ結果: 決定論的シンセ演奏者による 2 テイク針比較、センサー帯域の発見、PoC 5 の決定論パス実証 |
| [`docs/controllability_poc.md`](docs/controllability_poc.md) | 制御トラック PoC 計画 (K 系列): パラメータ=効くツマミの読み替え、grip 効果量の定義、K0〜K2 Suno 転移、K3 直交性行列（DCI/MIG 効果量再定式化・実 Suno ミニ行列・機種結合の符号反転発見・MusicGen フル行列=ノイズ天井初稼働） |
| [`docs/score_centric_planning.md`](docs/score_centric_planning.md) | 楽譜中心の再編成: 双方向再現性の通底原理、採譜トラック (T 系列) T0–T2、Q 系列の計器校正への再定義、意味層センサーの将来枠 |
| [`docs/event_roundtrip.md`](docs/event_roundtrip.md) | R4 事象レベル欄（コード進行）の往復入場計画: `chord_progression` × `compute_chord_events` × コード系列一致率、fixity/4値診断への適用 |
| [`docs/roundtrip_case_studies.md`](docs/roundtrip_case_studies.md) | Suno 往復テストケース結果 (個別ログ) と R1 corpus manifest の入口: 計器の有効帯域、物理固定・意味差替の制御性 A/B、双方向性成功 (BPM 留保)、BPM 89.1 アトラクタ疑い |
| [`docs/roundtrip_preservation.md`](docs/roundtrip_preservation.md) | R0 deterministic roundtrip preservation: Score -> perform -> extract -> draft Score diagnostics and K1 cross-check |
| [`docs/metamorphic_probe.md`](docs/metamorphic_probe.md) | メタモルフィック計器: render_sample×実extract を掃引し grip/校正/直交性/決定論を Hypothesis で自動検証。centroid=tight grip / 高域 brightness=センサー盲 / bpm オクターブ誤検出が R2-2a 未フラグ を計測 |
| [`docs/roundtrip_corpus_screen.md`](docs/roundtrip_corpus_screen.md) | R1 corpus screen + 対照実験 A/B/C: 高速曲の低 BPM は **Suno 不忠実でなく抽出器 halving**(start_bpm=180 で 172.3 回復)。「アトラクタ」の正体は prior×BPM グリッド選択。breakbeat 仮説は反証。R2-2a は 2×固定 lag でグリッド量子化(1.93×)を外す。データ: examples/roundtrip/screen_2026-06-16.yaml |
| [`docs/genre_calibration_planning.md`](docs/genre_calibration_planning.md) | 意味層のジャンル/楽器語彙拡張計画: `cultural_context` ハードコード誤判定(管弦→bass-music)を config 化で是正する Tier 2、Suno 生成のラベル自動付きコーパスで licensing 律速を回避(生成器バイアスは本物アンカーで補正)、Phase A(config 化)/B(コーパス)/C(検証)分割 |
| [`docs/ai_performer_score_roadmap.md`](docs/ai_performer_score_roadmap.md) | 「AI が演奏者として使う楽譜」マージロードマップ(PR 4 本, 2026-06-29 壁打ちで PR1.5 新設): 既存研究(MIR/CLAP/DCI-MIG/制御性評価/EPR)と蓄積知見(K 系列 grip/roundtrip fixity/genre bias)をマージ。PR1=control_profile スキーマ(楽譜が効くチャネルを知る・fixity 前例踏襲・K2 初期データ)、PR1.5=control_profile-aware compile(既存 ExternalPromptAdapter 配線で楽譜→演奏ループを Suno で閉じる・実用物の核)、PR2=楽譜準拠テスト+CLAP(意味層読解器)、PR3=K3 直交性(DCI/MIG)+機種デバイスプロファイル。決定論=物理層保証/非決定論=意味層助言の層分離、多生成器は Suno ルート確立後 |
| [`docs/control_profile.md`](docs/control_profile.md) | PR1/PR1.5 実装: `CompositionScore.control_profile`(生成器→物理フィールド→grip_class の自己記述)スキーマ・検証(未知キー fail-fast だが fixity と違い疎を許容)・K2(#117)由来の Suno 初期データ(bpm/brightness tight)。PR1.5=control_profile-aware compile(backend selector external→suno・フィールド粒度 drop accounting・grip_class 駆動の3ティア優先度で tight 先頭昇格/drop最後・priority エイリアス・backend descriptor 隔離)。PR2=楽譜準拠テスト(`svprpe score-adherence`: tight 宣言フィールドのコンパイル保持+roundtrip 保存をフィールド単位で判定・計器であって verdict なし・path 非依存・CLAP=PR2b は依存律速) |
| [`docs/lyrics_semantic_anchor.md`](docs/lyrics_semantic_anchor.md) | 2026-07-01 アレンジ・デモ発見: ボーカル/歌詞が key/BPM 読みを揺らす(交絡は実在するが方向不定＝n=1「ボーカル＝主音の錨」を n=2 で棄却・halving も非法則化)。歌詞が付与する「メリハリ(曲らしさ)」は物理 dynamic_range に写らない(むしろ逆)＝歌詞は**意味層**のアンカーで現状は耳が唯一のセンサー。**n=3 追試(07-01 S2/#124)で `dynamic_range`=歌詞アンカー説は棄却**(EDM 限定・Rock で反転かつ再生成ノイズ未満)、`mid_ratio` は最有力だが noise 超えは Rock のみ・EDM は directional(instrumental alt 未取得)＝昇格は n≥2×2 セル要件。BPM grip=確度×精度2軸・調号は grip/進行は非再現。genre pop 帯欠落/低sub EDM 誤判定も付随記録 |
| [`docs/musicgen_backend.md`](docs/musicgen_backend.md) | MusicGen ローカル生成トラック: PR A(runbook+`musicgen` extra・実推論なし・CI 安全) / PR B(実バッチ→K2 型 fixture+`device_profiles/musicgen.yaml`+R3 初実測) / PR C(R3 ハーネス)。DD-A 決定論契約(fixture→grip のみ CI 対象)、annotation 隔離原則は対象外(生成側)、weights ライセンスは CC-BY-NC-4.0 実確認済(研究計器限定・重み非同梱) |
| [`docs/semantic_sensor_clap.md`](docs/semantic_sensor_clap.md) | CLAP を抽出段階の意味層センサーとして配線(`svprpe extract --clap-semantic`): SOURCE 音声を固定の意味軸バッテリー(`config/semantic_probe_axes.yaml`)に対し A/B `contrast_fit` で計測、`LearnedAudioAnnotations.semantic_axes`(schema_version 1.1)に隔離、post-hoc fixture 比較(生成物対象)からの拡張として抽出時 SOURCE 音声を読む真の意味層センシングを実現。軸校正(2026-07-04 実推論・`scripts/calibrate_semantic_axes.py`): vocal/energy 実証・brightness は bpm 交絡・acousticness/warmth 探索扱い、有効帯域=実制作音楽 |
| [`docs/lyrics_transcription_sensor.md`](docs/lyrics_transcription_sensor.md) | 歌詞転写センサー: faster-whisper + 既存 Demucs vocals stem で歌詞を機械化(CLAP の連続値 grip と相補的な記号列センサー)。入力側`svprpe extract --lyrics`→`LearnedAudioAnnotations.lyrics_transcription`(schema_version 1.2)、出力側`svprpe lyrics-adherence`(`eval/lyrics_match.py`, learned import なし・計器であって verdict なし)。fake-backend のみ、実推論は未計測 |
| [`docs/arrangement_identity_planning.md`](docs/arrangement_identity_planning.md) | Arrangement Identity Track 計画: AR0–AR4、sidecar-first（D1）、M1=意味核とキーの Score-level identity preservation、artifact 配送と生成後観測への段階分離 |
| [`docs/work_identity_roadmap.md`](docs/work_identity_roadmap.md) | 同一性判定トラック（WI0–WI4）: 作品同一性を宣言的契約×弁別判定×人間校正×被覆正直会計で実測定義する計画。melody/lyrics センサー→D-1 閾値→弁別ハーネス→identity proxy v0→制度化 |
| [`docs/wi1_d1_thresholds.md`](docs/wi1_d1_thresholds.md) | WI1 逸脱分布と D-1 閾値 Design Memo: MusicGen 無人 n=20 の structure/harmony 逸脱分布、§4 編集分解アルゴリズム、§5 D-1 分類規約 v0（within 14/outside 6）、harmony は v0 未分類 |
| [`docs/wi2_discrimination_harness.md`](docs/wi2_discrimination_harness.md) | WI2 弁別判定ハーネス: 4 セル 13 clips の MusicGen 弁別バッチ、identity-rank 5 軸判定、cell P のチャネル死 byte 証明、順位表決定論再現 12/12、弁別成立は bpm のみ（structure/harmony/brightness は非弁別、key は不安定） |
| [`docs/wi3_human_calibration.md`](docs/wi3_human_calibration.md) | WI3 人間校正 v0: 事前登録 12 ペア判定（基準器 n=1・ブラインド seed 8400）、identity proxy v0 は空集合（採用 0/5 軸）、同一スコア再生成ペアすら「別の曲」判定、誤り 3 件は P3 same 誤予測（機構は bpm アトラクタ/長さバイアス/平行調流れの軸別）、実 Suno ペア(P5)は第 2 トランシェへ繰延 |

## ドキュメント管理ポリシー

**CLAUDE.md はリポジトリ横断の普遍的内容のみ記述する (目標: 400 行以内)。**

新機能・新仕様を追加する際のドキュメント作成ルール:

1. **機能・仕様の詳細は `docs/<topic>.md` を新規作成して記述する**
   - 設計思想、計算式、パラメータ、検証結果、使用例など
   - CLAUDE.md に詳細を追加してはならない
2. **CLAUDE.md への追記は最小限に留める**
   - ファイル配置の一覧に 1 行
   - 設計ドキュメント索引表に 1 行（新 doc へのリンク）
   - それ以外の詳細は追加しない
3. **既存の task-specific 内容を見つけたら対応する `docs/` に移管する**
   - CLAUDE.md が肥大化していないか定期的に精査する

**判断基準**:
- **普遍的 (CLAUDE.md に残す)**: 開発環境、コーディング規約、git workflow、
  ファイル配置の一覧、ドキュメント索引 — どの作業者・どの機能でも参照する内容
- **task-specific (`docs/` に分離)**: 1 コンポーネントの実装詳細、1 指標の校正結果、
  1 機能の API スキーマ、1 実験の検証データ — 特定タスクの深掘り情報

## README 管理ポリシー

**README.md は入口情報に限定し、再膨張を防ぐ (目標: 300 行以内、hard limit: 350 行)。**

README の運用ルール:

1. **単一 section が 30 行を超えたら `docs/<topic>.md` へ抽出する**
   - README にはリンク + 2-3 行の要約のみ残す
2. **新規 docs を作成したら索引を 2 箇所更新する**
   - README の「設計ドキュメント」表に 1 行追加
   - CLAUDE.md の設計ドキュメント索引表に 1 行追加
3. **README と docs の責務を混ぜない**
   - README: 5 分で全体像を掴む入口情報、コンセプト図、クイックスタート、
     主要指標の一行定義、設計 docs への索引
   - docs: 仕様詳細、検証データ、1 コンポーネントの仕様詳細、
     トラブルシューティング事例、実装 recipe

## Commands

```bash
pip install -e ".[dev]"
ruff check .
pytest -q --tb=short              # 全件（音声合成+抽出を含むため数分）
pytest -m "not slow" -q           # 日常の反復用の高速サブセット（重い統合/コーパス/property を除外）
svprpe --help
```

## Coding Conventions

### Style

- ruff 準拠 (line-length=100)
- 型ヒント必須: `Optional`, `List`, `Dict` を使用
- `from __future__ import annotations` を全モジュール先頭に記述
- docstring / コメントは日本語 OK
- float 表示は小数点 3–4 桁に丸める

### Patterns

- **Frozen dataclass / pydantic model**: 値オブジェクトは不変で定義する
- **フォールバックチェーン**: import 時に try/except でフラグ設定、実行時に分岐
- **値のクランプ**: 正規化が必要な float 値は `max(lo, min(hi, value))` で範囲内に収める
- **タイムスタンプ**: UTC, ISO 8601 形式で保存
- **Optional + confidence pattern**: 不確実性のある値は Optional + confidence で表現
- **config の二重コピー同期**: `config/*.yaml` を変更したら `src/svp_rpe/config/` の
  パッケージ同梱コピーも同期する（インストール実行時はそちらへフォールバックする。
  `tests/test_config.py` の同期テストが enforce）

### Error Handling

- 明示的な例外送出は避け、フォールバックチェーンで吸収する
- オプショナル依存の import は `try/except ModuleNotFoundError` でモジュール名を
  確認してからフラグ設定（transitive 依存エラーは fail-fast）
- リソース（DB 接続・ファイル・ネットワーク）はコンテキストマネージャで管理する

### Testing

- テストファイル: `tests/test_*.py`
- `tmp_path` でファイルシステムを分離
- ヘルパーファクトリでオブジェクト生成（モック不使用を推奨）
- `pytest.approx()` で float 比較
- **`slow` マーカー**: 音声合成 + 実抽出を回す重いテストに `@pytest.mark.slow` を付け、
  日常は `pytest -m "not slow"` で除外（CI と push 前は全件実行。CI が `slow` を
  skip しないこと）。**抽出を伴うテスト単位**で付けるのが原則で、純ロジック / 合成
  bundle / monkeypatch の安価なテストは同じファイル内でも高速ループに残す
  （`test_metamorphic_probe` / `test_screen_corpus` / `test_snapshot` /
  `test_transcribe_measure` / `test_transcribe_draft` は per-test 指定）。例外は
  `test_validation_script`: 全テストが共有の重い `_results()` に依存するため module 単位

## Git Workflow

### Branches

- `main` — 安定版。直接 push しない（例外: `.claude/memory/` の運用ログは直接 commit 可）
- `codex/*` — Codex が実装する作業ブランチ
- `claude/*` — Claude Code が例外的に小規模 fix-up を行う作業ブランチ

### Commit Messages

- Conventional Commits 形式: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`
- 日本語メッセージ可

### Pull Request

**コード・ドキュメント変更は必ず Pull Request で実施する**。`main` への直接 push は
原則禁止（唯一の例外は Branches 節に記載した `.claude/memory/` 運用ログ）。
Claude Code が例外的に PR を作成する場合はリンク発行で作成する
（`gh pr create` は使わない）。Codex が PR を作成する場合は `AGENTS.md` §2 の
Completion Summary 規約に従い、利用可能な GitHub CLI / connector を使ってよい。

```bash
# 1. ブランチを push
git push -u origin <branch-name>

# 2. PR リンクを提示
# https://github.com/Yuu6798/ugh-prompt-engine/compare/main...<branch-name>?expand=1
```

#### PR 本文の必須記述

PR を作成するときは、**本文を必ず作成する**（リンクのみ提示で本文を空にしない）。
GitHub MCP の `mcp__github__create_pull_request` で本文を渡すか、リンク経由で
User が作成する場合も同等の本文を担当エージェントが事前に提示する。

本文に最低限含める要素:

```markdown
## Summary
<2–4 行で「何を / なぜ」変更したかを記述>

## Changes
- <主要な変更点を箇条書き、ファイル単位 or 機能単位>

## Verification
- [ ] `ruff check .` pass
- [ ] `pytest -q --tb=short` pass
- [ ] <該当する場合> 手動検証手順とその結果

## Related
- Phase: <roadmap_goal1.md の Q-ID 等>
- Brief / Issue: <該当する場合のリンク>

## Notes for Reviewer
<逸脱事項、未解決課題、次のループへの引き継ぎ等。なければ "None">
```

ドキュメント単独 PR の場合は `Verification` を「該当なし（docs のみ）」で省略可。
Codex が PR を作成する場合は [`AGENTS.md`](AGENTS.md) §2 の Completion Summary
フォーマットを本フォーマットの代わりに使ってよい（情報量は等価）。

## CI 基本方針

- Push / PR で lint（`ruff check .`）+ test（`pytest -q --tb=short`）が通ることを必須とする
- CI 通過 = lint clean + 全テスト pass
- CI 固有のワークフロー詳細は `.github/workflows/*.yml` と `docs/` に記述する
