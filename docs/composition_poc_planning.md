# Composition PoC Planning — 物理層×意味層レイヤー作曲

**Status**: PLANNING  
**Created**: 2026-05-26  
**Relates to**: `docs/ai_music_daw_vision.md`, `docs/semantic_ci_product_v1.md`

## 背景と目的

### 問題

AI 音楽生成（Suno, Udio 等）は「テキストプロンプト → ブラックボックス → 音声」の
パイプラインで動く。中間表現がないため：

- 生成結果の **検査・修正** ができない（再生成ガチャ）
- 作曲意図と生成結果の **乖離を定量化** できない
- 「バイブコーディング」のような **構造化されたバイブミュージック** が成立しない

### 仮説

RPE/SVP を音楽の「ソースコード」として使えば、宣言的な作曲 → 生成 → 監査の
フィードバックループが成立する。物理層（BPM, key, dynamics 等）と意味層
（core, surface, grv, delta_e 等）の二軸で曲を定義する新しい作曲スタイル。

### PoC のゴール

**1 曲分の Composition Score を書き、AI 生成し、監査レポートで乖離を定量化する。**

成功基準：
- [ ] Composition Score (YAML) → 自然言語プロンプト変換が動作する
- [ ] 生成された音源の RPE 抽出 → Composition Score との ΔE レポートが出力される
- [ ] レポートから「何を直すべきか」が読み取れる（RepairSVP が機能する）
- [ ] 上記が既存の決定論パイプライン上で完結する（LLM 不使用）

---

## 既存資産の棚卸し

### そのまま再利用できるもの

| 資産 | 用途 | ファイル |
|---|---|---|
| `TargetSVP` | Composition Score のベースモデル | `semantic_ci/models.py` |
| `generate_expected_rpe()` | Score → 期待 RPE 導出 | `semantic_ci/core.py` |
| `compare_expected_observed()` | 期待 vs 実測の diff | `semantic_ci/core.py` |
| `generate_repair_svp()` | 差分 → 修復提案 | `semantic_ci/core.py` |
| `run_semantic_ci()` | 全パイプラインオーケストレータ | `semantic_ci/core.py` |
| `render_markdown()` | 監査レポート出力 | `semantic_ci/report.py` |
| RPE 抽出パイプライン | 音源 → 物理特徴量 | `rpe/extractor.py` |
| `SemanticRPE` 導出 | 物理 → 意味層マッピング | `rpe/semantic_rules.py` |
| `SVPForGeneration` | プロンプトテキスト生成 | `svp/models.py` |
| `ci-check` CLI | CI ゲート統合 | `cli.py` |

### ギャップ（新規実装が必要）

| ギャップ | 内容 | 規模 |
|---|---|---|
| **G1**: RPEBundle → ObservedRPE アダプタ | 型階層が異なる。RPEBundle の物理/意味特徴を ObservedRPE の signals + metrics に変換する橋渡し | S |
| **G2**: Composition Score スキーマ拡張 | TargetSVP に物理層制約（BPM, key, dynamics 等の metric_targets）とセクション構造を追加 | M |
| **G3**: `svprpe compose` + 生成バックエンド | Score → プロンプト変換 + 3バックエンド（External/MusicGen/MIDI） | L |
| **G4**: `svprpe audit` コマンド | Composition Score + 音源 → RPE 抽出 → ΔE レポートのワンショット実行 | S |

---

## フェーズ設計

### Phase C0: RPEBundle → ObservedRPE アダプタ（G1）

**Goal**: 音源から抽出した RPEBundle を semantic_ci パイプラインに接続する。

**設計方針**:
- `semantic_ci/adapter.py` に `rpebundle_to_observed_rpe(RPEBundle) -> ObservedRPE` を実装
- PhysicalRPE の数値フィールド → `metrics` dict に変換
- SemanticRPE の por_core / por_surface / grv / delta_e → `signals` list に変換
- 変換ロジックは決定論的（同一 RPEBundle → 同一 ObservedRPE）

**Acceptance Criteria**:
- [ ] `rpebundle_to_observed_rpe()` が RPEBundle の全主要フィールドを変換
- [ ] `run_semantic_ci(target_svp, observed_rpe)` に渡して SemanticCIRun が得られる
- [ ] 往復テスト: synth サンプルの RPEBundle → ObservedRPE → semantic_ci 完走

**推定規模**: 0.5 日

---

### Phase C1: Composition Score スキーマ拡張（G2）

**Goal**: 作曲者が「物理層 + 意味層で曲を宣言する」ためのスキーマを定義する。

**設計方針**:

TargetSVP を直接拡張するのではなく、`CompositionScore` を上位モデルとして新設し、
内部で TargetSVP に変換する。理由：

1. TargetSVP は semantic_ci の内部モデルで、作曲者向けの UX とは責務が異なる
2. 物理層制約（BPM, key 等）は TargetSVP の `metric_targets` にマッピングできるが、
   作曲者にはドメイン固有の語彙（「BPM: 128」「Key: Cm」）で書かせたい
3. セクション構造（Intro → Verse → Chorus → ...）は TargetSVP にない概念

```yaml
# composition_score.yaml — 作曲者が書くファイル
meta:
  title: "Midnight Signal"
  version: 1

physical:
  bpm: 128
  key: Cm
  time_signature: "4/4"
  duration_target_sec: 210
  dynamics:
    range_db: ">12"
    energy_curve: ascending     # ascending / descending / arc / flat
  spectral:
    centroid_tendency: low       # low / mid / high
    brightness: dark             # dark / neutral / bright

semantic:
  core: "introspective night drive"
  surface:
    - synth_pad
    - sub_bass
    - vinyl_crackle
    - reverb_tail
  grv:
    primary: deep_house
    secondary: ambient
  delta_e:
    transition_type: gradual
    intensity: moderate

structure:
  - section: intro
    bars: 8
    note: "minimal, sub bass only"
  - section: verse
    bars: 16
    note: "add pad, keep sparse"
  - section: chorus
    bars: 16
    note: "full energy, all elements"
  - section: outro
    bars: 8
    note: "strip back to intro texture"

tolerances:
  bpm: 3.0
  key: exact
  duration_sec: 30.0
  spectral_centroid: 500.0
```

**実装**:
- `src/svp_rpe/compose/models.py` — `CompositionScore` Pydantic モデル
- `src/svp_rpe/compose/convert.py` — `composition_to_target_svp(CompositionScore) -> TargetSVP`
- `src/svp_rpe/compose/loader.py` — YAML ファイル読み込み

**Acceptance Criteria**:
- [ ] 上記 YAML を `CompositionScore.model_validate()` でロードできる
- [ ] `composition_to_target_svp()` で有効な TargetSVP に変換できる
- [ ] physical セクションの制約が `metric_targets` + `tolerances` に正しくマッピングされる
- [ ] structure セクションがメタデータとして保持される

**推定規模**: 1 日

---

### Phase C2: `svprpe compose` コマンド + 生成バックエンド（G3）

**Goal**: Composition Score → 音源生成。バックエンドを差し替え可能にして外部サービス障害に備える。

**設計方針 — バックエンド抽象化**:

外部サービス（Suno, Udio）はプロンプト文字数制限・パラメータ制御の限界・サービス停止
リスクがある。単一の生成手段に依存するとPoCが外部要因で頓挫するため、
生成バックエンドを差し替え可能な構造にする。

```
CompositionScore
    ↓
PromptRenderer (共通インターフェース)
    ├── ExternalPromptAdapter  → Suno/Udio 向け（文字数圧縮、タグ形式）
    ├── MusicGenAdapter        → Meta MusicGen（transformers 直接、WAV 出力）
    └── MidiAdapter            → pretty_midi + FluidSynth（完全制御、決定論的）
```

**3 バックエンドの使い分け**:

| バックエンド | 品質 | 制御性 | 外部依存 | 用途 |
|---|---|---|---|---|
| External (Suno/Udio) | 高 | 低（プロンプト制約あり） | サービス依存 | 最終デモ |
| MusicGen | 中 | 中（プロンプト自由） | GPU 推奨 | 自動ループ検証 |
| MIDI + FluidSynth | 低 | 高（全パラメータ制御） | なし | CI / 回帰テスト / フォールバック |

**ExternalPromptAdapter の設計**:

Suno 等のプロンプト制約に対応する圧縮戦略：
1. Score の要素を優先度順に並べる（core > grv > physical > surface > structure）
2. 文字数上限に収まるまで低優先要素を切り落とす
3. 切り落とした要素は `dropped_elements` として記録 → audit 時に「この要素は生成に伝達されていない」と報告可能

```
[ジャンル/ムード] [テンポ/キー] [楽器/音色] [構成] [制約]

例（フル）:
"Deep house / ambient track. Introspective night drive atmosphere.
128 BPM, C minor. Synth pad, sub bass, vinyl crackle with reverb.
Gradual build from minimal intro to full chorus, then strip back.
Duration: around 3:30."

例（圧縮 — 200文字制限）:
"Deep house ambient, introspective night drive. 128 BPM Cm.
Synth pad, sub bass, vinyl crackle. Gradual build to full chorus."
```

**MusicGenAdapter の設計**:
- `transformers` の `MusicgenForConditionalGeneration` を使用
- プロンプト長制限なし、`max_new_tokens` で duration 制御
- GPU がなければ CPU フォールバック（低速だが動作する）
- オプショナル依存: `pip install svp-rpe[musicgen]`

**MidiAdapter の設計**:
- Score の physical 制約（BPM, key, time_signature）を直接 MIDI に反映
- structure のセクション → MIDI トラックのリージョンにマッピング
- surface の楽器名 → General MIDI プログラムに変換（best-effort）
- 意味層は MIDI では表現不可 → audit 時に物理層のみ検証対象とし、意味層は skip

**共通出力モデル**:

```python
class GeneratedPrompt(BaseModel):
    text: str
    tags: list[str]
    negative_tags: list[str]
    dropped_elements: list[str]  # 文字数制限で切り落とされた要素
    backend: Literal["external", "musicgen", "midi"]
```

**実装**:
- `src/svp_rpe/compose/prompt_renderer.py` — 共通インターフェース + ExternalPromptAdapter
- `src/svp_rpe/compose/musicgen_adapter.py` — MusicGen 統合（オプショナル依存）
- `src/svp_rpe/compose/midi_adapter.py` — MIDI + FluidSynth レンダリング
- CLI: `svprpe compose score.yaml [-o prompt.txt] [--format text|json] [--backend external|musicgen|midi]`

**Acceptance Criteria**:
- [ ] ExternalPromptAdapter: Score → 読みやすいプロンプト、文字数上限指定で圧縮動作
- [ ] MusicGenAdapter: Score → WAV ファイル出力（GPU/CPU 両対応）
- [ ] MidiAdapter: Score → MIDI → WAV 出力（FluidSynth）
- [ ] 全バックエンド: 同一 Score → 同一出力（決定論。MusicGen は seed 固定時）
- [ ] `--format json` で dropped_elements を含む構造化出力
- [ ] バックエンド非可用時に明確なエラーメッセージ（「musicgen 未インストール」等）

**推定規模**: 2 日（ExternalPromptAdapter 0.5d + MusicGenAdapter 0.5d + MidiAdapter 0.5d + CLI統合 0.5d）

---

### Phase C3: `svprpe audit` コマンド（G4）

**Goal**: Composition Score + 生成音源 → ΔE 監査レポートをワンショットで出力。

**設計方針**:
- 既存パイプラインの組み合わせ（新規ロジック最小）
- 内部フロー: Score → TargetSVP → ExpectedRPE / Audio → RPEBundle → ObservedRPE → SemanticDiff → RepairSVP → Report

```
svprpe audit score.yaml generated_track.wav [-o report.md] [--threshold 0.3]
```

**実装**:
- `src/svp_rpe/compose/audit.py` — `audit_composition(CompositionScore, audio_path) -> SemanticCIRun`
- CLI 追加: `svprpe audit`
- レポートは既存の `render_markdown()` を拡張して物理層の差分も含める

**Acceptance Criteria**:
- [ ] Score + WAV/MP3 → Markdown レポート出力
- [ ] レポートに Signal Diff（意味層）+ Metric Diff（物理層）+ Repair Plan が含まれる
- [ ] `--threshold` で pass/repair 判定が切り替わる
- [ ] 終了コード: pass=0, repair=1（CI 統合可能）

**推定規模**: 1 日

---

### Phase C4: エンドツーエンドデモ（統合検証）

**Goal**: 1 曲分のフルループを実行し、PoC 仮説を検証する。

**バックエンドフォールバック戦略**:

デモは最高品質のバックエンドから試し、失敗したら降格する。

```
1st: Suno/Udio（最高品質、ただしプロンプト制約あり）
  ↓ プロンプトが制約に収まらない or 生成が意図と大きく乖離
2nd: MusicGen（中品質、プロンプト自由、ループ自動化可能）
  ↓ GPU 非可用 or 品質不足
3rd: MIDI + FluidSynth（低品質、完全制御、全環境動作）
```

各バックエンドでの audit 結果を比較し、「生成品質」と「ΔE 精度」の関係も記録する。

**手順**:
1. サンプル Composition Score を作成（上記 "Midnight Signal" を使用）
2. `svprpe compose` でプロンプト生成（全バックエンド）
3. 各バックエンドで音源生成
4. `svprpe audit` で各バックエンドの監査レポート生成
5. レポートの RepairSVP に基づいて Score を修正
6. 再生成 → 再監査で ΔE が改善することを確認
7. 3 バックエンド間の audit 結果を比較分析

**自動ループ検証**（MusicGen / MIDI バックエンド）:
- Score → compose → generate → audit → repair → 再 Score のループを N 回自動実行
- ΔE の収束曲線をプロット（仮説3の定量検証）

**成果物**:
- `examples/composition/midnight_signal/` に Score + プロンプト + レポートを格納
- `docs/composition_poc_report.md` に結果と考察を記録

**成功判定**:
- [ ] 監査レポートが生成意図との乖離を具体的に指摘できる
- [ ] Score 修正 → 再生成で ΔE が改善する（定量的な改善を確認）
- [ ] 最低 1 つのバックエンドでフルループが完走する
- [ ] フローが「バイブミュージックのソースコード」として機能する感触がある

**推定規模**: 1 日（3 バックエンド比較 + 自動ループ検証込み）

---

## クリティカルパス

```
C0 (adapter, 0.5d) ──────────────────→ C3 (audit, 1d) ──→ C4 (demo, 1d)
C1 (schema, 1d) ──→ C2 (compose+gen, 2d) ──→ C3
```

**最短所要時間**: C1 と C0 を並行で 1 日 → C2 で 2 日 → C3 で 1 日 → C4 で 1 日 = **5 日**

## 設計判断ログ

### D1: TargetSVP を直接拡張するか、CompositionScore を新設するか

**採用**: CompositionScore 新設 + TargetSVP への変換レイヤー

**理由**:
- TargetSVP は semantic_ci の内部モデル。作曲者 UX とは責務が異なる
- 物理層制約は `metric_targets` にマッピング可能だが、YAML の書き心地が悪い
- 変換レイヤーがあれば、CompositionScore のスキーマを自由に進化させられる
- TargetSVP 側への変更が不要 = semantic_ci の既存テストが壊れない

**却下案**: TargetSVP に physical フィールドを追加
- 却下理由: semantic_ci の全テスト・全消費者への影響が大きすぎる

### D2: プロンプト生成に LLM を使うか

**採用**: テンプレートベース（LLM 不使用）

**理由**:
- プロジェクトの哲学原則（決定論 / LLM 不使用 / API キー不要）に準拠
- PoC 段階では「構造的に正しいプロンプト」で十分
- 将来 LLM 版を追加する場合はプラグイン拡張点を設ける

### D3: セクション構造の粒度

**採用**: セクション単位（bars + note テキスト）

**理由**:
- PoC ではセクションレベルの構造宣言で十分
- ビート単位やフレーズ単位は Pre-prototype 以降で検討
- 既存の `SectionMarker` モデルと対応させやすい

### D4: audit コマンドのレポートに物理層差分を含めるか

**採用**: 含める（semantic_ci の Signal Diff に加えて Metric Diff を表示）

**理由**:
- 作曲者にとって「BPM が 128 のつもりが 140 だった」は最も直感的なフィードバック
- 既存の `compare_metric_values()` がそのまま使える

### D5: 生成バックエンドを差し替え可能にするか、Suno 一本で行くか

**採用**: 3 バックエンド差し替え可能（External / MusicGen / MIDI）

**理由**:
- Suno/Udio のプロンプト文字数制限・パラメータ制御の限界で PoC が外部要因で頓挫するリスクが高い
- MusicGen を組み込めばループ全体がコード内で閉じ、自動ループ検証（仮説3）が可能になる
- MIDI バックエンドは最終防衛線 + CI 回帰テストの基盤になる
- バックエンド間の audit 結果比較自体が「生成品質と ΔE 精度の関係」という知見を生む

**却下案**: Suno/Udio のみ
- 却下理由: 外部サービスへの完全依存。プロンプト制約で情報欠落 → 仮説3 が検証不能になる最悪ケースに対処できない

**トレードオフ**: C2 の工数が 1 日 → 2 日に増加。ただし各アダプタは独立しているため、
ExternalPromptAdapter を先に作り、MusicGen / MIDI は後追いで追加可能。

---

## リスクと緩和策

| リスク | 影響 | 緩和策 |
|---|---|---|
| AI 生成音源の品質が低く RPE 抽出が不安定 | audit 結果がノイジー | synth サンプルでの事前検証を C0 に含める |
| metric_targets のマッピングが不完全 | 物理層 ΔE が不正確 | C1 で既存 PhysicalRPE フィールドとの対応表を明示的にテスト |
| Suno/Udio のプロンプト文字数制限で Score の情報が欠落 | 生成が意図を反映しない | ExternalPromptAdapter の優先度付き圧縮 + dropped_elements 追跡で「何が伝わらなかったか」を可視化 |
| Suno/Udio がプロンプトに忠実に従わない | 反復改善ループが機能しない | MusicGen / MIDI バックエンドにフォールバック。少なくとも 1 つのバックエンドでループ成立を確認 |
| MusicGen の GPU 非可用 | 中品質バックエンドが使えない | CPU フォールバック（低速）+ MIDI バックエンドが最終防衛線 |
| Composition Score の UX が作曲者に刺さらない | PoC 後の展開が困難 | C4 の成果物を基にユーザーフィードバックを収集 |

---

## 関連ドキュメント

- [`docs/ai_music_daw_vision.md`](ai_music_daw_vision.md) — 長期ビジョン（SVP as AI music MIDI）
- [`docs/semantic_ci_product_v1.md`](semantic_ci_product_v1.md) — semantic CI V1 仕様
- [`docs/roadmap_goal1.md`](roadmap_goal1.md) — Goal 1 定量観測ロードマップ
