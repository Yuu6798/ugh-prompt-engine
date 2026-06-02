# Task Brief: C1 — CompositionScore スキーマ + TargetSVP 変換

## Phase
composition_poc_planning.md Phase C1（Brief PoC 1: Layered Composition Score）— MVP

## Goal
ブリーフ §6 / planning「正規スキーマ」の Composition Score YAML を Pydantic v2 モデルとして
実装し、作曲者が書く YAML を機械が読めるようにする。あわせて
`composition_to_target_svp()` を実装し、既存 semantic CI 系（`TargetSVP`）へ接続する。

## Acceptance Criteria
- [ ] `examples/composition/midnight_signal.yaml`（ブリーフ §6 の正規 YAML をそのまま採用）を
  `CompositionScore.model_validate()` でロードできる
- [ ] `load_composition_score(path) -> CompositionScore` が YAML を読み Pydantic validation を通す
- [ ] `composition_to_target_svp(score) -> TargetSVP` が有効な `TargetSVP` を返す
  （`TargetSVP` は `id` と `core` が必須 — 下記マッピング参照）
- [ ] `physical.*` の各フィールドが `TargetSVP.metric_targets` に正しくマッピングされる
  （`bpm` は数値、`key` 等は文字列のまま）
- [ ] `structure` の各セクションが `TargetSVP.notes` に決定論的な文字列として保持される
- [ ] `rendering` 設定がモデルに含まれる（C2 の prompt renderer が参照する）
- [ ] 必須フィールド欠落の YAML を渡すと `ValidationError` が送出される（テストで確認）
- [ ] 同一 YAML → 同一 `TargetSVP`（決定論。`id` slug 化・list 整列が安定）
- [ ] 既存テスト全件 pass（新規モジュール追加のみ、`semantic_ci` 等は不変更）

## 正規スキーマ（ブリーフ §6 準拠 — 逸脱しないこと）
planning.md「正規スキーマ」の YAML を唯一の正とする。実装するモデル:

| モデル | フィールド | 型 |
|---|---|---|
| `Meta` | `title` / `version` | `str` / `float`(or `str`) |
| `GrvSpec` | `primary` / `secondary` | `str` / `str` |
| `DeltaESpec` | `overall` | `str` |
| `SemanticLayer` | `core` / `grv:GrvSpec` / `delta_e:DeltaESpec` / `avoid:List[str]` | — |
| `PhysicalLayer` | `bpm:int` / `key:str` / `time_signature:str` / `active_rate_target:str` / `valley_depth_target:str` / `brightness:str` / `stereo_width:str` | — |
| `StructureSection` | `section:str` / `bars:int` / `role:str` / `physical:str` | — |
| `RenderingConfig` | `target_backend:str` / `prompt_max_chars:int` / `priority:List[str]` | — |
| `CompositionScore` | `meta` / `semantic` / `physical` / `structure:List[StructureSection]` / `rendering` | — |
| `GeneratedPrompt` | `text:str` / `char_count:int` / `dropped_elements:List[str]` | C2 が生成、C1 では型定義のみ |

**全フィールド required**（前回セッションの設計判断 #3）。スカラ・ネストモデルはキー必須。
list フィールド（`avoid`, `structure`, `priority`）は **キー必須・空リスト可**。

## フィールドマッピング（CompositionScore → TargetSVP）
| CompositionScore | TargetSVP | 変換 |
|---|---|---|
| `meta.title` | `id` | slug 化（lower / 空白→`-` / 英数と`-`のみ）|
| `semantic.core` | `core` | そのまま |
| `semantic.grv.primary`, `.secondary` | `grv` | `[primary, secondary]`（空文字は除外）|
| `semantic.delta_e.overall` | `delta_e_profile` | そのまま |
| `semantic.avoid` | `avoid` | そのまま |
| `physical.bpm` | `metric_targets["bpm"]` | int |
| `physical.key` 他すべて | `metric_targets[<field>]` | 各フィールドを文字列で格納 |
| `structure[i]` | `notes` | `"<section>(<bars>bars): role=<role> | physical=<physical>"` |
| `rendering` | （マッピングしない）| `CompositionScore` 側に保持、C2 が使用 |

## 設計判断（前回セッション結論 — convert / モデルに反映）
1. **層間整合性チェックをしない** — 例: BPM 60 + 躍動感 を矛盾として弾かない。
   `composition_to_target_svp` も physical↔semantic の妥当性検証を入れない。
2. **`delta_e` は semantic 層に置く（現状維持）** — delta_e=仕様、structure=実装。
3. **全フィールド required** — Optional 化は PoC 後の UX 改善として後段。
   → これら 3 点を planning.md「設計判断ログ」に追記すること（D7 として）。

## Scope
- IN:
  - `src/svp_rpe/compose/__init__.py`
  - `src/svp_rpe/compose/models.py`（上記モデル群）
  - `src/svp_rpe/compose/loader.py`（`load_composition_score`）
  - `src/svp_rpe/compose/convert.py`（`composition_to_target_svp`）
  - `examples/composition/midnight_signal.yaml`（正規 YAML サンプル）
  - `tests/test_compose_schema.py`（ロード / 変換 / validation エラー / 決定論）
  - `docs/composition_poc_planning.md`（設計判断ログに D7 追記のみ）
- OUT:
  - `src/svp_rpe/semantic_ci/**`（不変更。`TargetSVP` は read-only で利用）
  - `svprpe compose` CLI / `ExternalPromptAdapter`（C2 の範囲）
  - `src/svp_rpe/cli.py`（C2 で compose サブコマンド追加）

## Allowed Dependencies
- なし（`pydantic` v2 / `PyYAML` は既存依存）。新規依存が必要になったら escalation。

## Implementation Hints
- 既存パターン踏襲: `from __future__ import annotations`、frozen は不要だが
  pydantic `model_config` で `extra="forbid"`（未知キーを弾き仕様逸脱を検出）。
- `TargetSVP` は `id`（必須・デフォルトなし）と `core` が必須。`id` は title slug 化で供給。
  `TargetSVP` の他フィールド（`surface`/`preserve`/`lock`/`tolerances`/`change_budget`）は
  デフォルトに任せる（C1 では設定しない）。
- `TargetSVP` の `metric_targets` は `Dict[str, Any]`。`bpm` は int、他は str で格納。
- `notes` は `TargetSVP` の field_validator で normalize される点に注意
  （決定論テストは normalize 後の値で assert する）。
- `version` は YAML で `0.1` と書かれる → `float` 受理 or `str` 受理を decide
  （`extra="forbid"` 下で型不一致にならないよう、`str | float` を許容し str 正規化推奨）。

## Required Outputs
- ブランチ名: `codex/c1-composition-score-schema`
- PR タイトル: `feat(compose): CompositionScore schema + TargetSVP conversion (C1)`
- PR 本文: AGENTS.md §2 Completion Summary 形式

## Done When
- 上記 Acceptance Criteria が全て ✓
- CI green（`ruff check .` + `pytest -q --tb=short`）
- PR 本文に「正規 YAML → 変換後 TargetSVP」の実例（before/after）を 1 件掲載

## Open Questions（Codex 着手前に Claude/User へ確認可）
- `meta.version` の型: `str` 固定にするか `str | float` 許容にするか（推奨: `str | float` 受理 → str 化）
- `physical.bpm` 以外で数値化したいフィールドがあるか（現状すべて str 保持で十分と判断）
