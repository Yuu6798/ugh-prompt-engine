# RUN10-CAL 実装マップ v1（Design Memo — Fable 設計）

正本: [`DESIGN_VG_METER_CAL_DEBT_v1.0.md`](DESIGN_VG_METER_CAL_DEBT_v1.0.md)
（VG-METER-CAL-DEBT-DESIGN-v1.0。以下「設計正本」。§番号は全て設計正本を指す）。

## 0. 授権境界（最重要）

設計正本は `execution_authorized: false` / `meter_changes_authorized: false`。
ユーザーの今回指示は「設計に基づく **コード実装**」であり、以下は本実装に**含めない**:

- C0 freeze の実行（manifest/registry の凍結 artifact 生成・freeze event 記録）
- secret（`split_secret` / `render_root_secret`）の生成・保存。全 API は secret を
  **引数として受け取る**のみ。テストはテスト内生成のダミー secret を使う
- 456×5 の campaign 実行・selection/holdout の実測走行
- 既存 meter（`voice_genesis/harness/*` / `af_measure.py` 等）の変更。B0 wrapper は
  **無改変 import** のみ
- RUN11 関連の一切

`c0_validate.py` は **dry-run 検証のみ**（書込なし・secret 生成なし）。武装版 freeze
スクリプトは §18 Gate 2 承認後の別 PR とする。

## 1. 配置と依存

- パッケージ: `voice_genesis/calibration/`（`__init__.py` あり。import は
  `voice_genesis.calibration.*` の完全修飾。実 import 可否は pytest で実証する）
- svp-rpe 本体（`src/`）を import しない（voice_genesis 非依存宣言に従う）
- 依存: numpy / scipy / librosa / soundfile / stdlib（hashlib, hmac, json, subprocess）。
  pyworld は **guarded import**（不在 → 当該候補 ineligible。§3.3 pyworld 特則）
- 値オブジェクトは frozen dataclass（voice_genesis 規約。pydantic 不使用）
- 全モジュール `from __future__ import annotations`・型ヒント必須・ruff (E4,E7,E9,F) clean

## 2. モジュール → 設計正本 対応表

| module | 実装内容（正本§） |
|---|---|
| `vocab.py` | 閉語彙全部: 終端 status・手続 Gate（§1）・BLOCKED codes（§3.3）・missing 理由コード（§11）・independence tier / claim ceiling（§4.1）・evidence_class（§10.2）・Domain/Split enum・`CLAIM_CRITICAL_SET` 定数・`debt_discharged()` 純導出関数（D1） |
| `canonical.py` | バージョン付き正規形 **`vgcal-canon/1`**（sorted keys・区切り最小・NaN/Inf 拒否・float は Python repr 最短往復 + `-0.0`→`0.0` 正規化）+ `row_id = sha256(row_json)` + `manifest_sha`。設計正本 §7 の「RFC 8785 相当」は **byte 互換を主張しない**: RFC 8785 と数値表記が異なる（例 `1e-07` vs `1e-7`）ため、正規形を独自名で版管理し、検証器は同一実装で照合する（Codex レビュー 2026-09-01 採用） |
| `streams.py` | HKDF-SHA256 stream 分離（`info` = 長さ接頭辞付き field 連結: campaign_id/family/split/row_id/probe_index/purpose）→ 64bit seed → `np.random.Generator(PCG64)`。RNG 台帳 dataclass（§3.3, §7） |
| `splitter.py` | HMAC-SHA256(secret,row_id) 昇順 → stratum 内 largest-remainder 50/25/25 → 端数の偶奇交互配分 → 制約検査 → 決定的最小 swap 修復 + swap 記録 → 実現済み row→split 表と検証器（§7） |
| `tolerance.py` | pooled dispersion（family×condition class）・`tolerance=max(k·pooled_SD, floor)`・floor 機械導出・`UNSTABLE_CELL`・`TOLERANCE_FLOOR_LIMITED`（§6） |
| `observables.py` | 二段 median `m[i]`・`e/AE/RE`（zero guard）・BIAS/MAE/q95(linear 固定)・`U_rep`/`U_proc`（多 process 一般化）・nuisance `dS`・FDR0/FNR1（missing/invalid を分子算入・最小数 N≥10）・failure boundary（§10.1） |
| `gates.py` | threshold budget `M[i]=E_use−U_GT−U_num`（加算・非RSS）・ABSOLUTE gate 1/2'/max'/3/4'/5（per-instance margin `G[i]`）・DIRECTIONAL `R_ij` / resolvable / ≥3 pairs（=3 は provenance flag）（§10.2–10.4） |
| `selection.py` | 族別 lexicographic（ABSOLUTE 系列 / DIRECTIONAL 系列）・丸め後比較（error=有効3桁, rate=0.001, complexity=int）・丸め前後 vector 記録・`SELECTION_FAILED_CLOSED`（§9） |
| `status.py` | first-match cascade（§11 の 5 段）・missing→(status, reason) 一意写像・手続 Gate 単調性検査（§1 R1） |
| `m6_identity.py` | `u_X[j]`・sum-of-norms `U_obs_pair`・`T_null=q95(D_null+U_null_pair)`・`distinct()`・空 critical set → NOT_EVALUABLE・等重み L1/L2 のみ（§12） |
| `provenance.py` | §13 必須 field の dataclass schema + append-only JSONL ledger（entry sha 連鎖）+ leakage 検査（holdout row の unseal 前初出 → `BLOCKED_LEAKAGE`）（§7, §13）。**単一 writer 境界を契約とする**: append は fcntl 排他ロック + flush/fsync で直列化し、並列 meter 実行（§14）は per-worker 記録 → 直列 append の集約で行う。`verify_chain()` は途中破損（truncated 末尾行・sibling 分岐）を検出して報告する（Codex レビュー 2026-09-01 採用） |
| `fixtures/axes.py` | §5.1 primary/boundary 軸水準の凍結定数 |
| `fixtures/matrix.py` | 456 logical cell の**明示列挙**（family 別内訳・cal/sel/holdout 数は §5.2 表と厳密一致をテストで enforce）・domain tag 導出・F0 帯域整合検査 → BOUNDARY 再タグ（§3.3）・nuisance block（一因子主効果 + 6 targeted interactions の全行列挙） |
| `fixtures/controls.py` | family 別 negative control + 対 positive control（両側条件）（§4.2） |
| `fixtures/generators/*` | family 別決定論 generator（§4.2）。FORMANT_GT は **resonator 非共有の2実装**（filter cascade 系 / 閉形式加算合成系）。PCM 量子化・dBFS gain・ramp/context は `common.py` に集約 |
| `fixtures/determinism.py` | generator repeat=2 fresh process（subprocess）byte 一致検査 → 違反 `BLOCKED_C1_GENERATOR_NONDETERMINISTIC`（§3.3, §6） |
| `candidates/registry.py` | 99 候補の宣言的定義（§8。数の検算: F0 5 + M3 43 + M2T 13 + M2A 24 + M4 5 + M5 7 + M6 2 = 99）。candidate_id/construct/unit/family/params/domain/missing rule/tier/ceiling/complexity rank |
| `candidates/adapter.py` | meter adapter interface + 共通 fail filter（schema 違反・非有限値・within/fresh 不一致・negative 偽検出・positive 不発火）（§8） |
| `candidates/impl/*` | 候補 algorithm family 実装（pyin=librosa / cepstral poles / Burg LPC（`fs'=2*max_formant` 決定的 resample 必須）/ harmonic OLS・Theil-Sen / ACF HNR / harmonic residual / D4C=pyworld guarded / local prominence / wave discontinuity / spectral flux）+ `b0_wrappers.py`（harness 無改変 import） |
| `c0_validate.py` | C0 manifest の dry-run 検証（REQUIRED_BLOCKING / RECORDED_OR_ABSENT の二層判定・BLOCKED_* code 発行・書込なし）（§3） |

## 3. underspec の解決規約

設計正本が数値・グリッドを確定していない箇所（例: family 別 truth×confound の因子直積の
具体形、M2A-HNR-ACF 8 / M2A-HARMONIC-RESIDUAL 12 / M4 4 候補のパラメタグリッド、HKDF の
field 区切り）は、**§5.2 / §8 の件数と厳密一致する最も単純な選択**を採り、当該モジュール
docstring に `[UNDERSPEC-CAL-nn]` タグで記録し、`README.md` の UNDERSPEC 台帳に集約する。
これらは C0 freeze 承認時のユーザーレビュー対象であり、コードが正本を上書きするものではない。

## 4. テスト要件

- `voice_genesis/calibration/tests/` を pyproject `testpaths` に追加（依存は本体必須依存
  のみ）。`tests/discipline/` と `.github/test-paths/foundry-core.txt` の同期規律に抵触
  しないことを実行で確認
- 高速性: CI で音声を扱うテストは少数 row の短尺生成に限定。456×5 の全展開は行わない
  （matrix は列挙とカウント検証のみ）
- 必須検証: 456/228/114/114 の厳密一致・99 候補数・split の決定性と制約充足・gate 式の
  境界ケース（G[i]=0 は PASS 側）・status cascade の網羅/排他・M6 空集合→NOT_EVALUABLE・
  canonical JSON の安定性（key 順・float 往復・`-0.0` 正規化）・generator の同 seed 再現
- **式単位の独立数値オラクル**（Codex レビュー 2026-09-01 採用）: 結果を左右する全式
  （二段 median / q95 margin `G[i]` / gate3 / gate4' invariance / `R_ij`・resolvable /
  FDR0・FNR1 / selection 丸め / M6 sum-of-norms・`T_null`）に、実装から独立に手計算した
  入力ベクトル→期待値の固定オラクルを 1 件以上与え、不一致は即 fail とする
- generator determinism の fresh-process 検査は `@pytest.mark.slow` 可

## 5. 実装フェーズ（委譲単位）

- **A: framework core** — vocab / canonical / streams / splitter / tolerance /
  observables / gates / selection / status / m6_identity / provenance + tests + 配線
- **B: fixtures** — axes / matrix / controls / generators / determinism + tests
- **C: candidates + c0_validate** — registry / adapter / impl / b0_wrappers /
  c0_validate + tests

B・C は A 完了後に並行。ファイル所有は排他（共有ファイルは A が作る `__init__.py` と
README のみ。B/C は README の自セクションのみ追記）。
