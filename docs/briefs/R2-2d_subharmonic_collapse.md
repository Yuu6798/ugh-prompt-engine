# Design Memo: R2-2d — Subharmonic (sub-octave) BPM collapse の検出・補正

> Claude→（通常 Codex）ハンドオフ。AGENTS.md §1 フォーマット。起草: 2026-06-17。
> **Codex 不在のため実装も Claude 単独で担う**（ブランチは claude 系を使用）。
> 根拠データ: `examples/roundtrip/screen_2026-06-16.yaml`,
> `docs/roundtrip_corpus_screen.md` §「アトラクタの正体＝BPM グリッド × prior」。

## Phase
roadmap_goal2.md R2（BPM 校正）。R2-2 検出器系列の継続:
R2-2a(×2検出, #80) → R2-2b(近傍探索, #82) → R2-2c(値補正, #83) → **R2-2d(subharmonic)**。
※ roadmap の `R2-3`（校正メモ反映）とは別物。本タスクは検出器スライス。

## Goal
真テンポ ~172 が **117.45（≈ 2/3×, 1.47×）** で報告される非オクターブ崩壊
（「117.45 アトラクタ」= librosa の tempo prior がグリッド点 ~120 を選ぶ機構）を
検出器が捕捉し、R2-2c の補正機構で回復テンポへ補正できるようにする。原理的に
÷2 モデル非対応だった 1.47× collapse（roundtrip_corpus_screen.md §3）を射程に入れる。

## Acceptance Criteria
- [x] 真 ~172 BPM のインパルス列を `bpm=117.45` で渡すと
      `detect_bpm_octave_ambiguity(...).is_ambiguous == True`、`candidates` に
      ~172 近傍の回復テンポを含む（新規 unit test）。
- [x] `extract_rpe` 経由で flagged 時に `phys.bpm` が回復テンポへ補正され
      （R2-2c 機構の再利用）、元の崩壊値は `min(bpm_candidates)` に残る。
- [x] **false-positive 回帰ガード**: 全 Q1-3 synth fixture（特に `synth_04` waltz=3/4 と
      `synth_01`=実 117.5 BPM）が `is_ambiguous == False` のまま。実 117.5 と
      崩壊 117.45 を弁別できることを `synth_01` で pin。
- [x] R2-2b/R2-2c の既存テストが引き続き green（窓拡張で octave 検出が劣化しない）。
- [x] `compute_bpm` の bpm 値・unflagged fixture の `PhysicalRPE.bpm` は不変。

## Implementation Approach
対象は `src/svp_rpe/rpe/physical_features.py::detect_bpm_octave_ambiguity` のみ。
`BPM_OCTAVE_NEIGHBORHOOD` の下限を **1.8 → 1.4** へ広げ `(1.4, 2.2)` とし、
octave(≈2×) と subharmonic(≈1.47×=3:2) の両方を 1 つの「faster-tempo dominance」走査で
包摂する。overlap 正規化・`BPM_OCTAVE_RATIO_THRESHOLD=1.15` 据え置き・回復テンポ算出は
R2-2b のまま。R2-2c の extractor 補正・flag・confidence cap・transcribe trust gate は
既存配線のまま自動適用（下流コード変更なし）。docstring を「octave のみ」→
「faster-tempo collapse（octave or 3:2 subharmonic）」へ更新。

empirical 事前確認済（着手前）: synth 全 5 曲が新窓で ratio≈0.99–1.00（unflag）、
真172@117.45 は強く flag・回復 172.3、89.1 octave も unified 窓で ratio 2.74 維持。

## Risks
- **triplet / compound meter の false-positive**: 1.4–1.6× 窓は 3:2 / 付点・三連符と
  関係が近い。synth fixtures（waltz 含む）は threshold 1.15 で安全（実測 ≤1.002）だが、
  **実 triplet 主体音源は未検証**（R1-audio 不在）。将来 R1 corpus で監視。
- **窓拡張が octave 検出を劣化させないか** → R2-2b/2c テスト再 green が条件（達成）。
- **principled fix は tempo prior**: doc は真因を prior グリッド選択と特定。本スライスは
  post-hoc 検出器による **部分緩和**（prior 改修＝別タスク・高回帰・OUT）。
- **degenerate 比率**: 崩壊報告値の lag が onset-AC のトラフに落ちると primary_strength≈0 で
  ratio 極大化。既存 `primary_strength <= 0.0` ガードで吸収。

## Test Strategy
- 単体: (a) 真172@117.45 subharmonic flag + 回復候補、(b) Q1-3 全曲 unflag、
  (c) `synth_01`(実117.5) unflag = 真/崩壊の弁別 pin、(d) 窓境界更新: 既存
  `test_peak_outside_neighborhood_is_not_flagged` は 1.6× を使うが**新窓 [1.4,2.2] の内側**
  になるため outside 値を 1.3× へ更新。
- 回帰: R2-2b 分離テスト・R2-2c extractor 補正テストの再 green。`compute_bpm` 不変。
- 既存テストへの影響: 境界値更新のみ必須。

## Scope
- IN: `src/svp_rpe/rpe/physical_features.py`（`BPM_OCTAVE_NEIGHBORHOOD` 下限 + docstring）、
  `tests/test_bpm_octave_ambiguity.py`、`docs/metrics.md`（半折り節を現行化）。
- OUT: `compute_bpm` の bpm 値・tempo prior 改修（別タスク・高回帰）、`extractor.py` の
  補正ロジック（R2-2c 済を再利用）、`PhysicalRPE` スキーマ、`scripts/screen_corpus.py`、
  実音源での再 screen（音源不在で本セッション不可）。

## Schema Admission
新フィールド追加なし（既存 `bpm_octave_ambiguous`/`bpm_candidates` を流用）。
フラグ名は subharmonic も含むよう意味が広がるが、スキーマ移行回避のため**据え置き**
（docstring で「octave or subharmonic collapse」と注記）。リネームは下流 churn 大で OUT。

## Allowed Dependencies
なし（`librosa` 既存）。

## Required Outputs
- ブランチ名: `codex/r2-2d-subharmonic-collapse`（実装は Claude 単独のため
  実際は割当 `claude/codex-unavailability-b7rhxw` を使用）
- PR タイトル: `feat(rpe): BPM subharmonic (3:2) collapse を検出窓へ統合（R2-2d）`
- 期待する変更ファイル: `src/svp_rpe/rpe/physical_features.py`,
  `tests/test_bpm_octave_ambiguity.py`, `docs/metrics.md`

## Done When
- Acceptance Criteria 全て ✓
- CI green（`ruff check .` + `pytest -q --tb=short`）
- PR 本文が Completion Summary 規約準拠

## Open Decisions（着手前に User 確定 → 推奨デフォルトで確定済）
1. **窓下限の値**: `1.4`（採用）/ 1.45 / 1.5。
2. **名前据え置き vs リネーム**: 据え置き（採用）。
3. **flag-only vs 補正**: 補正込み（採用・R2-2c 機構流用、octave と同一扱い）。
