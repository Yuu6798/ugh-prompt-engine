# Task Brief: C2 — `svprpe compose` + ExternalPromptAdapter

## Phase
composition_poc_planning.md Phase C2（Brief PoC 2: Layer-to-Prompt Composition）— MVP

## Goal
Composition Score を生成器向けプロンプトへ**決定論的に**変換する ExternalPromptAdapter と、
それを呼ぶ `svprpe compose` CLI を実装する。これで MVP（PoC 1 + 2）が完成する。

## ⚠️ 先に解決する不整合（設計オーナー指示 — 本 Brief で正規化）
C1 マージ済コードと planning C2 / ブリーフ §12 の間に 3 点の不整合がある。本 Brief の指示を正とする。

1. **`GeneratedPrompt` を再定義する**
   - C1 が `compose/models.py` に追加した `GeneratedPrompt(text, char_count, dropped_elements)` を、
     ブリーフ §12 正規形に**置き換える**:
     ```python
     class GeneratedPrompt(CompositionModel):
         backend: Literal["external", "musicgen", "midi"]
         text: str
         tags: List[str]
         negative_tags: List[str]
         dropped_elements: List[str]
     ```
   - `char_count` は削除（`len(text)` で導出可、正規 JSON に無い）。
2. **examples をディレクトリ形式へ移行する**（ブリーフ §12）
   - `examples/composition/midnight_signal.yaml`
     → `examples/composition/midnight_signal/composition_score.yaml` に **git mv**。
   - C1 テスト `tests/test_compose_schema.py` の `SAMPLE_PATH` を新パスに更新（1 行）。
   - 同ディレクトリに `generated_prompt.txt`（text 出力）と `generated_prompt.json`（full JSON）を追加。
3. **Prompt renderer の入力は `CompositionScore`（`TargetSVP` ではない）**
   - 理由（C1 レビュー P3）: `TargetSVP` の list フィールドは小文字化 + アルファベット順ソートされ、
     **grv の primary/secondary 優先順位・structure の時間順・avoid の大文字**が失われる。
   - renderer はこれらの順序・case を保持する必要があるため `CompositionScore` を直接読む。
     `composition_to_target_svp()` は本タスクでは使わない（C3 audit 用）。

## Acceptance Criteria
- [ ] `ExternalPromptAdapter.render(score: CompositionScore, max_chars: int | None) -> GeneratedPrompt`
- [ ] 同一 Score → 同一 `GeneratedPrompt`（決定論。ソート・dict 順序・浮動小数に依存しない）
- [ ] `physical` が テンポ・キー・密度/明度指示へ変換される（bpm / key / brightness / stereo_width）
- [ ] `semantic` が ムード（core）・grv・negative へ変換される
- [ ] `structure` が**時間順のまま**展開指示へ変換される（intro→verse→chorus→bridge）
- [ ] `tags` = `[grv.primary, grv.secondary, brightness, f"{stereo_width}_stereo"]`（この順、ソートしない）
- [ ] `negative_tags` = `semantic.avoid`（順序・大文字を**そのまま**保持）
- [ ] 文字数超過時に `rendering.priority` 低位から要素を切り落とし、`dropped_elements` に token を記録
- [ ] `--max-chars N` が `rendering.prompt_max_chars` を上書きする
- [ ] `--format text`（既定）= text のみ / `--format json` = `GeneratedPrompt` の JSON
- [ ] `-o/--output FILE` でファイル出力、無指定で stdout（既存 `generate` コマンドに倣う）
- [ ] `svprpe compose examples/composition/midnight_signal/composition_score.yaml` が動作
- [ ] 既存テスト全 pass（`GeneratedPrompt` 変更・パス移動の影響を含め green）

## 圧縮アルゴリズム（決定論）
1. text を `rendering.priority` 順のセグメント列として組み立てる（下表マッピング）。
2. `priority` に列挙されない要素（constraints / avoid 文）は末尾に置き、**最初に切られる**扱い。
3. 連結後の `len(text)` が上限を超える間、**優先度が最も低いセグメントから 1 つずつ除去**し、
   除去したセグメントの token 名を `dropped_elements` に追加する。
4. `negative_tags` / `tags` は text 圧縮の影響を受けない（JSON には常に全量保持）。

| priority token | text セグメント |
|---|---|
| `semantic.core` | ムード文（例: "Dark, {core} atmosphere."）|
| `semantic.grv` | ジャンル（例: "{primary} / {secondary} track."）|
| `physical.bpm` | "{bpm} BPM" |
| `physical.key` | "{key}" |
| `structure` | 各セクションを時間順に 1 文ずつ（例: "{section}: {physical}."）|
| `physical.optional` | brightness / stereo_width / active_rate_target / valley_depth_target 由来の補助文 |

**重要**: ブリーフ §9.2 / §12 の散文例（"128 BPM C minor deep house..."）は **到達イメージであり
byte 一致は求めない**。NLG は不要。決定論的なテンプレート連結で、必須要素が含まれ、圧縮が
機能すれば AC 達成。文言の正確な join は実装者の決定論的選択に委ねる。

## Scope
- IN:
  - `src/svp_rpe/compose/prompt_renderer.py`（`ExternalPromptAdapter`）
  - `src/svp_rpe/compose/models.py`（`GeneratedPrompt` 再定義のみ）
  - `src/svp_rpe/compose/__init__.py`（`ExternalPromptAdapter` を export）
  - `src/svp_rpe/cli.py`（`compose` サブコマンド追加）
  - `examples/composition/midnight_signal/composition_score.yaml`（C1 サンプルを git mv）
  - `examples/composition/midnight_signal/generated_prompt.txt`
  - `examples/composition/midnight_signal/generated_prompt.json`
  - `tests/test_compose_schema.py`（`SAMPLE_PATH` 更新のみ）
  - `tests/test_compose_prompt_renderer.py`（新規）
- OUT:
  - `composition_to_target_svp()` / `convert.py`（不変更、本タスクで未使用）
  - `MusicGenAdapter` / `MidiAdapter`（C6）
  - `svprpe audit` / `target_svp.json` 生成（C3 / C4）
  - `semantic_ci/**`

## Allowed Dependencies
- なし（typer / rich / pydantic / PyYAML は既存）。新規依存は escalation。

## Implementation Hints
- CLI は既存 `generate` コマンドのパターンを踏襲（`@app.command()`、`-o/--output`、
  `--format`、`console.print` / ファイル書き出し分岐）。コマンド関数名は `compose`。
- `backend` は MVP では常に `"external"`（`Literal` の既定。CLI オプション化しない）。
- 決定論担保: dict を回さず属性アクセスで組む。float フォーマットが要る箇所は小数 3–4 桁固定。
- `GeneratedPrompt` も `CompositionModel`（`extra="forbid"`）を基底にして整合を取る。
- 圧縮の token 名は priority token をそのまま使う（`dropped_elements=["physical.optional", ...]`）。

## Required Outputs
- ブランチ名: `codex/c2-compose-prompt-renderer`
- PR タイトル: `feat(compose): svprpe compose + ExternalPromptAdapter (C2)`
- PR 本文: AGENTS.md §2 Completion Summary 形式

## Done When
- 上記 Acceptance Criteria が全て ✓
- CI green（`ruff check .` + `pytest -q --tb=short`）
- PR 本文に「フル出力」と「`--max-chars` で圧縮した出力（`dropped_elements` 付き）」の
  2 例を JSON で掲載

## Open Questions（着手前に確認可）
- `tags` に `active_rate_target` / `valley_depth_target` を含めるか（現状: 含めない。tags は
  人間可読ジャンル/ムードに限定し、数値レンジ系は text/negative に委ねる方針）
- 圧縮で `semantic.grv` や `physical.bpm` 等の高優先セグメントしか残らない極端な `--max-chars`
  の下限挙動（現状: 全セグメント除去後も `tags`/`negative_tags` は JSON に残るため許容）
