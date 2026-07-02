# Design Memo: R2-2b — BPM octave-ambiguity 検出の近傍探索一般化

> Claude Code → Codex ハンドオフ。AGENTS.md §1 フォーマット。起草: 2026-06-16。
> 根拠データ: `examples/roundtrip/screen_2026-06-16.yaml`, `docs/roundtrip_corpus_screen.md`。

## Phase
roadmap_goal2.md R2（BPM 校正 / R2-2 の後続スライス）。前提だった R1-audio は
2026-06-16 に入手済（`examples/roundtrip/screen_2026-06-16.yaml` に実 Suno 6 テイクを
sha256 固定で記録）。本メモはその実音源調査が特定した検出器バグの修正。

## Goal
`detect_bpm_octave_ambiguity` を「ちょうど 2× の単一 lag」比較から「1.8×–2.2× 近傍の
支配ピーク探索」へ一般化し、BPM グリッド量子化でちょうど 2× を外れた halving
（例: 検出 89.1 / 真パルス 172.3 = 1.93×）を取りこぼさず flag する。reported bpm の
値補正は本スライスでは行わない（後述 Open Decision）。

## Acceptance Criteria
- [ ] 新規 unit test: 真テンポ ~172 BPM のインパルス列 `y` に対し `bpm=89.1`（halved 値）を
      渡すと `detect_bpm_octave_ambiguity(y, sr, 89.1).is_ambiguous == True` を返し、
      `candidates` に ~172 近傍の回復テンポを含む。
- [ ] 同 unit test で「旧実装（ちょうど 2×=178.2 の lag のみ）なら is_ambiguous=False に
      なる」ことをコメント/別アサートで記録し、修正対象バグを固定する。
- [ ] 既存 `tests/test_bpm_octave_ambiguity.py` の正常テンポ fixtures が引き続き
      `is_ambiguous == False`（false-positive 回帰なし）。
- [ ] `pytest tests/test_bpm_octave_ambiguity.py tests/test_rpe_extractor.py tests/test_snapshot_determinism.py -q` green。
- [ ] `compute_bpm` の返す bpm 値・`PhysicalRPE.bpm` は不変（snapshot 系テスト無変更で pass）。

## Implementation Approach
対象は `src/svp_rpe/rpe/physical_features.py::detect_bpm_octave_ambiguity` のみ。
- 現状: `subdivision_lag = _lag(bpm * 2.0)` の単一 lag で `ac[subdivision_lag]` を見る。
- 変更: faster 側の lag 窓 `[_lag(bpm * 2.2), _lag(bpm * 1.8)]`（lag は tempo に反比例なので
  2.2× が小 lag・1.8× が大 lag、`primary_lag` より faster=小 lag 側のみ）を走査し、
  overlap 正規化 `ac[lag] / (n - lag)` が最大の lag を subdivision とみなす。
- `subdivision_strength / primary_strength ≥ BPM_OCTAVE_RATIO_THRESHOLD`(1.15) で
  `is_ambiguous=True`、`candidates = sorted({round(bpm,2), round(回復テンポ,2)})`、
  `alt_strength_ratio = round(ratio, 4)`。回復テンポ = `60*sr/(hop*argmax_lag)`。
- 窓幅 1.8–2.2 は近傍定数 `BPM_OCTAVE_NEIGHBORHOOD = (1.8, 2.2)` 等で明示。
- `extractor.py` の合成・`PhysicalRPE`（`bpm_candidates`/`bpm_octave_ambiguous`）・
  transcribe trust gate（`score_draft._bpm_untrusted`）は既存配線のまま。flag recall が
  上がるだけで下流挙動は不変（flagged → sensor-blind は #80 で配線済）。

## Risks
- **false-positive 増**: 近傍最大を取ると通常の subdivided music（八分音符等の subdivision
  onset）で ratio が上がりうる。窓を 1.8–2.2 に狭く保ち、threshold 1.15 を据え置き、
  Q1-3 synth fixtures が unflagged のまま（回帰ガード）であることを必須条件とする。
- **grid 量子化が本バグの核**: `bpm*2` と実ピークが別 lag bin に落ちる現象を窓化で吸収する
  のが目的。窓が狭すぎると依然外す／広すぎると false-positive。1.8–2.2 で両実ケース
  （1.93×, および 2.0 ちょうど）を含むことを確認。
- **値補正は非対象**: reported bpm を上げると snapshot / transcribe / screen 出力に波及する
  ため本スライス OUT（Open Decision 参照）。

## Test Strategy
- 単体テスト観点: (a) 1.93× halving を flag、(b) ちょうど 2.0× も従来通り flag、
  (c) 正常テンポ（subdivision はあるが真値が primary）は unflagged、(d) 窓境界
  （1.8× 未満 / 2.2× 超のピーク）は拾わない。`y` は決定論的インパルス列で合成
  （`detect_bpm_octave_ambiguity` は y,sr,bpm を取るので halved bpm を直接渡せる）。
- 回帰テスト観点: `tests/test_bpm_octave_ambiguity.py` 既存正常ケースの unflagged 契約を pin。
  `compute_bpm` 出力不変を `tests/test_snapshot_determinism.py` で担保。
- 既存テストへの影響: スナップショット更新は原則不要（bpm 値・extractor 出力は不変、flag のみ
  recall 向上）。flag が変わる既存 fixture があれば期待値更新が必要だが、現行 fixtures は
  正常テンポ=unflagged のはずで無影響を確認すること。

## Scope
- IN: `src/svp_rpe/rpe/physical_features.py`（`detect_bpm_octave_ambiguity` + 近傍定数）、
  `tests/test_bpm_octave_ambiguity.py`（新規ケース追加）。
- OUT: `compute_bpm` の bpm 値とその prior、`extractor.py` の bpm 合成ロジック、
  `PhysicalRPE` スキーマ（既存 `bpm_candidates`/`bpm_octave_ambiguous` で充足）、
  グローバル tempo prior、`scripts/screen_corpus.py`（measurement 側は別タスク）。

## Open Decision（着手前に User が確定）
reported `bpm` 値そのものを回復テンポへ**補正するか**:
- **Option A（本メモの既定・推奨）**: flag recall のみ改善。値は不変、flagged は trust gate が
  sensor-blind 化。低リスク・スナップショット不変・0.5–1 日。
- **Option B（別スライス R2-2c 推奨）**: flagged 時に reported bpm を回復テンポへ補正。
  screen の保存率も改善するが snapshot/transcribe へ波及し回帰検証が増える。
本メモは A で記述。B を今やるなら別 Design Memo で。

## Allowed Dependencies
なし（`librosa` 既存。新規依存追加は escalation）。

## Required Outputs
- ブランチ名: `codex/r2-2b-bpm-octave-neighborhood`
- PR タイトル: `feat(rpe): BPM octave 検出を近傍探索化（グリッド量子化 halving を捕捉）`
- 期待する変更ファイル: `src/svp_rpe/rpe/physical_features.py`,
  `tests/test_bpm_octave_ambiguity.py`

## Done When
- Acceptance Criteria 全て ✓
- CI green（`ruff check .` + `pytest -q --tb=short`）
- PR 本文が Completion Summary 規約（AGENTS.md §2 / CLAUDE.md PR 本文必須記述）準拠
