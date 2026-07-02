# Composition PoC Planning — 実装計画

**Status**: C0–C4 完了（MVP 完走）— C5/C6 は将来枠  
**Created**: 2026-05-26  
**Updated**: 2026-05-27 — プロダクトブリーフとの整合性改訂  
**Upstream**: [`composition_score_product_brief.md`](composition_score_product_brief.md)（プロダクト定義）  
**Relates to**: [`ai_music_daw_vision.md`](ai_music_daw_vision.md), [`semantic_ci_product_v1.md`](semantic_ci_product_v1.md)

---

## 本ドキュメントの位置付け

本ドキュメントは **実装計画** であり、プロダクトの「何を・なぜ作るか」は
上位文書の [`composition_score_product_brief.md`](composition_score_product_brief.md) が定義する。

```text
ai_music_daw_vision.md          — 最長期ビジョン（SVP as AI music MIDI）
composition_score_product_brief.md — プロダクト定義（Composition Score とは何か）
composition_poc_planning.md       — 実装計画（本文書: どう作るか）
```

本計画のすべての設計判断は、ブリーフの以下の原則に従う:

1. **作曲言語が主役、監査は補助** — PoC の第一目標は Composition Score が作曲言語として成立すること
2. **AIは演奏者** — 作品本体は Score 側にある
3. **MVP は ExternalPromptAdapter のみ** — MusicGen/MIDI は後続フェーズ
4. **三層構造の必須性** — 物理層 + 意味層 + 構造層が揃って初めて作曲になる

---

## PoC ゴールの階層

ブリーフが定義する PoC 1–5 と、本計画の実装フェーズ C0–C4 の対応:

```text
Brief PoC 1 (Score が書ける)     ← C1 (schema) + example YAML
Brief PoC 2 (Prompt に変換)      ← C2 (compose CLI + ExternalPromptAdapter)
Brief PoC 3 (Layer Manipulation) ← C5 (将来)
Brief PoC 4 (複数レンダラ)       ← C6 (将来: MusicGen/MIDI 追加)
Brief PoC 5 (RPE Feedback)       ← C0 (adapter) + C3 (audit) + C4 (demo)
```

**MVP（最初の PR）の範囲**: PoC 1 + PoC 2 = C1 + C2

---

## 既存資産の棚卸し

### そのまま再利用できるもの

| 資産 | 用途 | ファイル |
|---|---|---|
| `TargetSVP` | CompositionScore からの変換先 | `semantic_ci/models.py` |
| `generate_expected_rpe()` | Score → 期待 RPE 導出（将来の audit 用） | `semantic_ci/core.py` |
| `compare_expected_observed()` | 期待 vs 実測の diff（将来の audit 用） | `semantic_ci/core.py` |
| `generate_repair_svp()` | 差分 → 修復提案（将来の audit 用） | `semantic_ci/core.py` |
| `run_semantic_ci()` | 全パイプラインオーケストレータ（将来の audit 用） | `semantic_ci/core.py` |
| `render_markdown()` | 監査レポート出力（将来の audit 用） | `semantic_ci/report.py` |
| RPE 抽出パイプライン | 音源 → 物理特徴量（将来の audit 用） | `rpe/extractor.py` |
| `SemanticRPE` 導出 | 物理 → 意味層マッピング（将来の audit 用） | `rpe/semantic_rules.py` |
| `SVPForGeneration` | プロンプトテキスト生成（参考） | `svp/models.py` |

### ギャップ（新規実装が必要）

**MVP（C1 + C2）で必要:**

| ギャップ | 内容 | 規模 |
|---|---|---|
| **G1**: CompositionScore スキーマ | 三層 + rendering 設定の Pydantic モデル | M |
| **G2**: YAML ローダー | Score YAML の読み込みとバリデーション | S |
| **G3**: TargetSVP 変換 | CompositionScore → TargetSVP（将来の semantic_ci 接続用） | S |
| **G4**: ExternalPromptAdapter | Score → 生成器向けプロンプト変換 | M |
| **G5**: `svprpe compose` CLI | compose コマンドの追加 | S |

**後続フェーズで必要:**

| ギャップ | 内容 | フェーズ |
|---|---|---|
| **G6**: RPEBundle → ObservedRPE アダプタ | 型階層の橋渡し | C0 → PoC 5 |
| **G7**: `svprpe audit` コマンド | Score + 音源 → ΔE レポート | C3 → PoC 5 |
| **G8**: MusicGen/MIDI バックエンド | 追加レンダラ | C6 → PoC 4 |
| **G9**: Layer Manipulator | 層ごとの変奏機能 | C5 → PoC 3 |

---

## 正規スキーマ

ブリーフ §6 のモデルを正規とする。既存計画からの主な変更点:

| 項目 | 旧（初版計画） | 新（ブリーフ準拠） |
|---|---|---|
| 物理層の形式 | ネスト（`dynamics.range_db`, `spectral.brightness`） | フラット（`brightness`, `active_rate_target`） |
| 構造層のフィールド | `note` | `role` + `physical` |
| delta_e の表現 | `transition_type` + `intensity`（構造化） | `overall`（自由テキスト） |
| rendering 設定 | なし | あり（`target_backend`, `prompt_max_chars`, `priority`） |
| tolerances | スキーマ内 | 将来の audit 用（MVP では省略可） |

正規 YAML:

```yaml
meta:
  title: "Midnight Signal"
  version: 0.1

semantic:
  core: "introspective night drive"
  grv:
    primary: "deep_house"
    secondary: "ambient"
  delta_e:
    overall: "gradual build from solitude to release"
  avoid:
    - "bright festival EDM"
    - "comic vocal delivery"

physical:
  bpm: 128
  key: "C minor"
  time_signature: "4/4"
  active_rate_target: "0.90-0.93"
  valley_depth_target: "0.15-0.25"
  brightness: "dark"
  stereo_width: "wide"

structure:
  - section: intro
    bars: 8
    role: "establish loneliness"
    physical: "low density, sub bass only"

  - section: verse
    bars: 16
    role: "restrained movement"
    physical: "sparse drums, short phrases, clear rests"

  - section: chorus
    bars: 16
    role: "emotional release"
    physical: "full energy, wide stereo, focused layers"

  - section: bridge
    bars: 8
    role: "near silence and reflection"
    physical: "no kick, no bass, minimal texture"

rendering:
  target_backend: "external"
  prompt_max_chars: 500
  priority:
    - semantic.core
    - semantic.grv
    - physical.bpm
    - physical.key
    - structure
    - physical.optional
```

---

## フェーズ設計

### Phase C1: CompositionScore スキーマ + TargetSVP 変換（G1–G3）— MVP

**Goal**: ブリーフ §6 の正規スキーマを Pydantic モデルとして実装し、
作曲者が書く YAML を機械が読めるようにする。

**PoC 対応**: PoC 1 (Layered Composition Score)

**実装**:

- `src/svp_rpe/compose/__init__.py`
- `src/svp_rpe/compose/models.py` — `CompositionScore`, `Meta`, `SemanticLayer`, `PhysicalLayer`, `StructureSection`, `RenderingConfig`, `GeneratedPrompt`
- `src/svp_rpe/compose/loader.py` — YAML 読み込み + Pydantic validation
- `src/svp_rpe/compose/convert.py` — `composition_to_target_svp(CompositionScore) -> TargetSVP`

**フィールドマッピング（CompositionScore → TargetSVP）**:

| CompositionScore | TargetSVP | 変換 |
|---|---|---|
| `semantic.core` | `core` | そのまま |
| `semantic.grv.primary/secondary` | `grv` | リスト化 |
| `semantic.delta_e.overall` | `delta_e_profile` | そのまま |
| `semantic.avoid` | `avoid` | そのまま |
| `physical.bpm` | `metric_targets["bpm"]` | 数値 |
| `physical.key` | `metric_targets["key"]` | 文字列 |
| `physical.*` (その他) | `metric_targets[key]` | 各フィールド |
| `structure` | `notes` | セクション情報を notes に格納 |

**Acceptance Criteria**:

- [ ] ブリーフ §6 の正規 YAML を `CompositionScore.model_validate()` でロードできる
- [ ] `composition_to_target_svp()` で有効な TargetSVP に変換できる
- [ ] physical のフィールドが `metric_targets` に正しくマッピングされる
- [ ] structure がメタデータとして保持される
- [ ] rendering 設定がモデルに含まれる

**推定規模**: 1 日

---

### Phase C2: `svprpe compose` + ExternalPromptAdapter（G4–G5）— MVP

**Goal**: Composition Score → 生成器向けプロンプトを決定論的に変換する。

**PoC 対応**: PoC 2 (Layer-to-Prompt Composition)

**設計方針**:

ブリーフ D3 に従い、MVP は ExternalPromptAdapter のみ。MusicGen / MIDI は
PoC 4 (C6) で追加する。バックエンド差し替え可能な構造にはするが、
最初の実装は 1 アダプタに限定する。

**ExternalPromptAdapter の圧縮戦略**:

1. Score の要素を `rendering.priority` 順に並べる
2. `rendering.prompt_max_chars` に収まるまで低優先要素を切り落とす
3. 切り落とした要素は `dropped_elements` として記録

```text
[ジャンル/ムード] [テンポ/キー] [楽器/音色] [構成] [制約]

例（フル）:
"Deep house / ambient track. Introspective night drive atmosphere.
128 BPM, C minor. Start with sparse sub bass and distant pads.
Gradually increase density toward a wide, emotional chorus.
Bridge should be near-silent with no kick and no bass.
Avoid bright festival EDM or comic vocal delivery."

例（圧縮 — 200文字制限）:
"Deep house ambient, introspective night drive. 128 BPM Cm.
Synth pad, sub bass. Gradual build to full chorus."
```

**共通出力モデル**:

```python
class GeneratedPrompt(BaseModel):
    text: str
    tags: list[str]
    negative_tags: list[str]
    dropped_elements: list[str]
    backend: Literal["external", "musicgen", "midi"]
```

**実装**:

- `src/svp_rpe/compose/prompt_renderer.py` — `ExternalPromptAdapter`
- CLI: `svprpe compose score.yaml [-o prompt.txt] [--format text|json] [--max-chars N]`
- `examples/composition/midnight_signal/composition_score.yaml`
- `examples/composition/midnight_signal/generated_prompt.txt`

**Acceptance Criteria**:

- [ ] 同一 Score → 同一 Prompt（決定論的）
- [ ] physical layer がテンポ・キー・密度指示へ変換される
- [ ] semantic layer がムード・主題・grv へ変換される
- [ ] structure が展開指示へ変換される
- [ ] `--max-chars` で圧縮が動作し、`dropped_elements` が記録される
- [ ] `--format json` で構造化出力

**推定規模**: 1 日

---

### Phase C0: RPEBundle → ObservedRPE アダプタ（G6）— 後続

**Goal**: 音源から抽出した RPEBundle を semantic_ci パイプラインに接続する。

**PoC 対応**: PoC 5 (RPE Feedback / Audit) の前提

**設計方針**:

- `semantic_ci/observed_adapter.py` に `rpe_bundle_to_observed(bundle, *, id) -> ObservedRPE` を実装
- PhysicalRPE の数値フィールド → `metrics` dict に変換
- SemanticRPE の por_core / por_surface / grv / delta_e → `signals` list に変換
- 変換ロジックは決定論的

**Acceptance Criteria**:

- [ ] `rpebundle_to_observed_rpe()` が RPEBundle の全主要フィールドを変換
- [ ] `run_semantic_ci(target_svp, observed_rpe)` に渡して SemanticCIRun が得られる
- [ ] 往復テスト: synth サンプルの RPEBundle → ObservedRPE → semantic_ci 完走

**推定規模**: 0.5 日

---

### Phase C3: `svprpe audit` コマンド（G7）

**Status**: IMPLEMENTED — `svprpe audit`（`cli.py`）、`semantic_ci/audit.py` の
`build_audit_report` / `render_audit_text`、`semantic_ci/observed_adapter.py` で実装済み。
（運用フェーズでの実曲検証は別途。）

**Goal**: Composition Score + 生成音源 → ΔE 監査レポートをワンショットで出力。

**PoC 対応**: PoC 5 (RPE Feedback / Audit)

**前提**: C0 + C1 + C2 完了

**設計方針**:

- 内部フロー: Score → TargetSVP → Audio/RPEBundle → ObservedRPE → knob needles → Control Panel Report
- audit は合否判定ではなく制御盤レポート。verdict / pass-fail / loss は出さず、終了コードは常に 0。

```
svprpe audit score.yaml generated_track.wav [-o report.md] [--format text|json]
```

JSON fixture mode is also supported for deterministic tests: `svprpe audit score.yaml extracted_rpe.json --format json`.

**Acceptance Criteria**:

- [ ] Score + WAV/MP3 → 制御盤レポート出力
- [ ] レポートにツマミごとの target / observed / deviation / score が含まれる
- [ ] `--format text|json` で表示形式が切り替わる
- [ ] 終了コードは常に 0（verdict / pass-fail / loss は出さない）

**推定規模**: 1 日

---

### Phase C4: エンドツーエンドデモ — 後続

**Status**: DONE (2026-06-12, 決定論パス) — 結果と考察は
[`composition_poc_report.md`](composition_poc_report.md)、成果物は
`examples/composition/midnight_signal/e2e/`。手順 3 の手動生成は決定論的シンセ演奏者
（`scripts/compose_e2e_demo.py`）で代替し、手順 5–6（レポートに基づく Score 修正 →
再生成 → 改善確認）は「Score 不変・演奏側が変わる」2 テイク比較で置き換えた。
Score 側を動かす修正ループは C5 に合流。

**Goal**: 1 曲分のフルループを実行し、PoC 仮説を検証する。

**PoC 対応**: PoC 1–5 の統合検証

**手順**:

1. サンプル Composition Score を作成（Midnight Signal）
2. `svprpe compose` でプロンプト生成
3. Suno/Udio で音源生成（手動）
4. `svprpe audit` で監査レポート生成
5. レポートの RepairSVP に基づいて Score を修正
6. 再生成 → 再監査で ΔE が改善することを確認

**成果物**:

- `examples/composition/midnight_signal/` に Score + プロンプト + レポートを格納
- `docs/composition_poc_report.md` に結果と考察を記録

**推定規模**: 1 日

---

### Phase C5: Layer Manipulator — 将来

**Goal**: 層を操作することで曲想の差分を作れることを示す。

**PoC 対応**: PoC 3 (Layer Manipulation)

**推定規模**: 1 日

---

### Phase C6: 追加レンダラ（MusicGen / MIDI）— 将来

**Goal**: 同じ Score を複数レンダラに演奏させる。

**PoC 対応**: PoC 4 (Renderer as Performer)

**バックエンド**:

| バックエンド | 品質 | 制御性 | 外部依存 | 用途 |
|---|---|---|---|---|
| External (Suno/Udio) | 高 | 低 | サービス依存 | 最終デモ |
| MusicGen | 中 | 中 | GPU 推奨 | 自動ループ検証 |
| MIDI + FluidSynth | 低 | 高 | なし | CI / 回帰テスト |

**推定規模**: 2 日

---

## クリティカルパス

```text
MVP:
  C1 (schema, 1d) → C2 (compose, 1d) → [MVP 完了: PoC 1+2]

後続:
  C0 (adapter, 0.5d) ─┐
  C2 完了 ────────────┼→ C3 (audit, 1d) → C4 (demo, 1d) → [PoC 5]
                       │
                       └→ C5 (manipulator, 1d) → [PoC 3]
                       └→ C6 (renderers, 2d)   → [PoC 4]
```

**MVP 最短所要時間**: 2 日（C1 + C2）

**全 PoC 所要時間**: ~7.5 日

---

## 設計判断ログ

### D1: CompositionScore を新設する（TargetSVP を拡張しない）

**採用**: CompositionScore 新設 + TargetSVP への変換レイヤー

**理由**: ブリーフ §13 D1。TargetSVP は semantic_ci 内部モデル、CompositionScore は作曲者向け UX。

### D2: 最初は監査を主役にしない

**採用**: MVP は compose のみ。audit は後続フェーズ。

**理由**: ブリーフ §8。「PoCの第一目標は監査CIではなく、Composition Scoreが
『曲を書くための言語』として成立すること」

**旧計画からの変更**: 旧 PoC ゴール「監査レポートで乖離を定量化する」を
PoC 5 に後退。MVP の成功基準は「Score が読めて、Prompt に変換できる」に変更。

### D3: MVP は ExternalPromptAdapter のみ

**採用**: 3 バックエンド構造は維持するが、実装は 1 つずつ。

**理由**: ブリーフ §13 D3。最短で「Score から曲を作る」体験を示せる。

**旧計画からの変更**: 旧 C2 は 3 バックエンドを 2 日で実装する計画だったが、
MVP を ExternalPromptAdapter 1 つに限定。MusicGen/MIDI は C6 へ。

### D4: 構造層を必ず入れる

**採用**: 構造層は MVP から必須。

**理由**: ブリーフ §13 D4。物理層だけでは音響スペック、意味層だけではプロンプト。

### D5: スキーマはブリーフ §6 を正規とする

**採用**: フラットな物理層、`role` + `physical` の構造層、`rendering` 設定。

**理由**: ブリーフがプロダクト定義として上位にあるため、スキーマはブリーフに合わせる。

**旧計画からの変更**:
- 物理層: ネスト形式 → フラット形式
- 構造層: `note` → `role` + `physical`
- delta_e: `transition_type` + `intensity` → `overall`（自由テキスト）
- rendering: 新設

### D6: AIを作曲家ではなく演奏者として扱う

**採用**: Score = 作品本体、AI生成 = 演奏。

**理由**: ブリーフ §7。作者性が Score 側に戻り、生成器の確率性を演奏差として扱える。

### D7: C1 は全フィールド required、層間整合性チェックなし、delta_e は semantic 層に保持

**採用**: C1 の CompositionScore スキーマは全フィールド required とし、list
フィールドはキー必須・空リスト可とする。`delta_e` は semantic 層の仕様として保持し、
structure はその仕様を実装する曲構造として扱う。`composition_to_target_svp()` では
physical と semantic の整合性検証を行わない。

**理由**: C1 は「作曲者が書いた正規 YAML を機械が読める」ことを最優先する。BPM と
意味ラベルの矛盾判定は作曲意図を過剰に弾く可能性があり、PoC 後の UX / lint / audit
フェーズで扱う方が安全。

---

## リスクと緩和策

| リスク | 影響 | 緩和策 |
|---|---|---|
| ExternalPromptAdapter の圧縮で情報が欠落しすぎる | 生成が意図を反映しない | `dropped_elements` 追跡 + 圧縮前後のプロンプト比較テスト |
| Score のスキーマが作曲者に刺さらない | PoC 後の展開が困難 | Midnight Signal の実例で早期にフィードバック収集 |
| TargetSVP への変換で情報損失 | audit が不正確になる | 変換テストで全フィールドのマッピングを検証 |
| rendering.priority の順序が不適切 | 重要な要素が切り落とされる | priority はスキーマで明示化、テストで検証 |

---

## 関連ドキュメント

- [`composition_score_product_brief.md`](composition_score_product_brief.md) — **上位文書**: プロダクト定義
- [`ai_music_daw_vision.md`](ai_music_daw_vision.md) — 長期ビジョン（SVP as AI music MIDI）
- [`semantic_ci_product_v1.md`](semantic_ci_product_v1.md) — semantic CI V1 仕様
- [`roadmap_goal1.md`](roadmap_goal1.md) — Goal 1 定量観測ロードマップ
