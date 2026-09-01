# voice_genesis/calibration — RUN10-CAL

campaign_id: `RUN10-CAL`

## 目的

VoiceGenesis の meter（M2 spectral tilt / M2 aperiodicity / M3 formants / M4
resonance / M5 transition / M6 identity / F0 control）を、正解付き合成 fixture
上で校正する技術負債返済キャンペーンの基盤コード。設計正本の核心:

> 正解付き世界で測定器自身を試験し、使える範囲・誤差・失敗条件を先に固定して
> から、VoiceGenesis の個体や遺伝を測る。

正本ドキュメント:

- [`DESIGN_VG_METER_CAL_DEBT_v1.0.md`](DESIGN_VG_METER_CAL_DEBT_v1.0.md) —
  VG-METER-CAL-DEBT-DESIGN-v1.0。技術内容の唯一の正本。
- [`IMPLEMENTATION_MAP_v1.md`](IMPLEMENTATION_MAP_v1.md) — 実装マップ
  (Design Memo)。モジュール → 設計正本 §番号の対応表。

## 授権境界（§0）

設計正本は `execution_authorized: false` / `meter_changes_authorized: false` /
`run11_measurement_entry_authorized: false`。本パッケージ（Phase A: framework
core）は **インフラ実装のみ** であり、以下は一切含まない:

- C0 freeze の実行（manifest/registry の凍結・freeze event 記録）
- secret（`split_secret` / `render_root_secret`）の生成・保存
  （全 API は secret を呼び出し側の引数として受け取るのみ。テストはテスト内
  生成のダミー secret を使う）
- 456×5 campaign の実測走行・selection/holdout の実行
- 既存 meter（`voice_genesis/harness/*` 等）の変更
- RUN11 関連の一切

`c0_validate.py`（Phase C）は dry-run 検証のみを行う（書込なし・secret 生成
なし）。武装版 freeze スクリプトは §18 Gate 2 承認後の別 PR。

## モジュール一覧（Phase A: framework core — 実装済み）

| module | 内容 | 正本 § |
|---|---|---|
| `vocab.py` | 全閉語彙（終端 status・手続 Gate・BLOCKED code・missing 理由・independence tier/claim ceiling・evidence class・Domain/Split/MeterId）、`CLAIM_CRITICAL_SET`、`debt_discharged()` / `campaign_closed()` 純導出関数 | §1, §3.3, §4.1, §10.2, §11 |
| `canonical.py` | 独自正規形 `vgcal-canon/1`（sorted keys・最小区切り・NaN/Inf 拒否・float 最短往復・`-0.0`→`0.0` 正規化）+ `row_id` / `manifest_sha` | §7, §3.3 |
| `streams.py` | HKDF-SHA256 (RFC 5869) による RNG stream 分離、`derive_seed`/`derive_generator`、`RngLedger`（secret を記録しない） | §3.3, §7 |
| `splitter.py` | HMAC-SHA256 昇順 → stratum 内 largest-remainder 50/25/25 → family 合計補正 → coverage 制約検査 → 決定的最小 swap 修復 → `RealizedSplitMap` + `verify_split` | §7 |
| `tolerance.py` | pooled dispersion（family×condition class）・`tolerance=max(k·SD,floor)`・floor 機械導出・`UNSTABLE_CELL`・`TOLERANCE_FLOOR_LIMITED` | §6 |
| `observables.py` | 二段 median・`e/AE/RE`・BIAS/MAE/q95(linear)・`U_rep`/`U_proc`（singleton 除外込み）・nuisance `dS`・FDR0/FNR1・failure boundary | §10.1 |
| `gates.py` | threshold budget `M[i]`・ABSOLUTE gate 1/2'/max'/3/4'/5・DIRECTIONAL 単位健全二連言 (+可換時合算式) resolvability | §10.2–10.4 |
| `selection.py` | 族別 lexicographic 選択（丸め後比較）・`select_across_ceilings` ceiling 階級間裁定・`SELECTION_FAILED_CLOSED` | §9, §2.6 |
| `status.py` | 終端 status first-match cascade・missing→(status,reason) 一意写像・手続 Gate 単調性検査 | §11, §1 |
| `m6_identity.py` | `u_X[j]`・sum-of-norms pairwise uncertainty・`T_null`・`distinct()`・CLAIM_CRITICAL_SET 全 member ABSOLUTE 必須（部分構成での distance 出力禁止） | §12 |
| `provenance.py` | §13 provenance schema（nested frozen dataclass）+ append-only JSONL ledger（fcntl 排他 + fsync、entry_sha 連鎖、truncated tail / tamper 区別）+ leakage 検査 | §7, §13 |

## fixtures（Phase B）

_(Phase B が担当。axes / matrix / controls / generators / determinism。
このセクションは Phase B 実装エージェントが追記する。)_

## candidates（Phase C）

_(Phase C が担当。registry / adapter / impl / b0_wrappers / c0_validate。
このセクションは Phase C 実装エージェントが追記する。)_

## UNDERSPEC 台帳

設計正本が数値・グリッド・エンコーディングを確定していない箇所について、
実装マップ §3 の規約（最も単純で安全な選択を採り docstring にタグ記録）に
従って導入した判断の一覧。C0 freeze 承認時のユーザーレビュー対象であり、
コードが設計正本を上書きするものではない。

| tag | module | 内容 |
|---|---|---|
| `UNDERSPEC-CAL-01` | `vocab.py` | `IndependenceTier.CROSS_IMPLEMENTATION` の claim ceiling 写像を `ABSOLUTE`（tier が許す上限。実際の到達は gate 判定で下方に絞られる）とした |
| `UNDERSPEC-CAL-02` | `streams.py` | HKDF `info` のフィールド連結を、区切り文字連結ではなく衝突耐性のある長さ接頭辞（4-byte big-endian）方式で実装した |
| `UNDERSPEC-CAL-03` | `splitter.py` | stratum 内 largest-remainder の closed-form 導出（`n mod 4` で場合分け）と、SEL/HOLD 端数 tie（構造的に常に同値）を stratum 内 HMAC 順位最大行の末尾ニブル偶奇で決める規則 |
| `UNDERSPEC-CAL-04` | `splitter.py` | family 合計の厳密一致は pairwise swap だけでは原理的に不可能なため、`reason="family_total"` の片道移動を導入し `reason="coverage"` の真の 2 行交換と区別した |
| `UNDERSPEC-CAL-05` | `tolerance.py` | floor 導出式を「PCM 量子化半ステップ・float eps bound・meter 宣言分解能の `max()`」として機械導出した |
| `UNDERSPEC-CAL-06` | `splitter.py` | coverage repair の donor 選択に、victim と対称な `_safe_to_remove` 安全性検査を適用した（同一行が違反間を往復し repair が収束しない振動を防ぐ） |
| `UNDERSPEC-CAL-07` | `provenance.py` | ledger `entry_sha` の digest 対象に `payload`+`prev_sha` に加えて `seq` も含めた（chain 上の位置を署名に取り込む保守的選択） |
| `UNDERSPEC-CAL-08` | `m6_identity.py` | M6 component の識別子型を `vocab.MeterId` に固定した（他 meter の校正 status と直接突合できる一貫性を優先） |

以下は Codex レビュー（2026-09-01、複数巡）で採用され `IMPLEMENTATION_MAP_v1.md`
に凍結された仕様であり、上表の UNDERSPEC には数えない（正本の一部として実装
済み）: `canonical.py` の `vgcal-canon/1` 版管理と `-0.0` 正規化、
`provenance.py` の単一 writer 境界（fcntl 排他 + fsync）と `verify_chain()` の
truncated/tamper 区別、`gates.py` の DIRECTIONAL resolvability 単位健全二連言
分解、`splitter.py` の重複 `row_id` 拒否、`selection.py` の ceiling 階級間裁定
規則、`observables.py` の `u_rep` singleton 除外、`m6_identity.py` の
CLAIM_CRITICAL_SET 全 member ABSOLUTE 必須化。

## テスト

```bash
python -m pytest voice_genesis/calibration/tests -q
ruff check voice_genesis/calibration/
```

依存は numpy / scipy / stdlib のみ（`svp_rpe` / `voice_genesis.harness` を
import しない）。
