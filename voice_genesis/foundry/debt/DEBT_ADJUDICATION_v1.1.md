# DEBT_ADJUDICATION_v1.1 — VoiceGenesis 技術・研究負債返済計画 v1.0 訂正記録

**策定日:** 2026-08-22
**裁定者:** User
**対象:** [`DEBT_REPAYMENT_PLAN_v1.0.md`](DEBT_REPAYMENT_PLAN_v1.0.md)（正本・無改変収載）
**機械可読正本:** 同ディレクトリ [`debt_adjudication_v1.1.yaml`](debt_adjudication_v1.1.yaml)
（`voicegenesis-debt-adjudication/1.1`）

## 0. 位置づけ

v1.0 は User 策定の実行可能性評価を受け、5 件の指摘のうち **1〜4 を採用・5（run4）は
「大部分返済済み」を採用し「残作業は台帳転記だけ」を棄却**する形で裁定された。
v1.0 本文は直接改変しない（正本の無改変性を保つ）。本記録は v1.0 への
**correction record（追補）**であり、**本文と v1.1 が乖離する場合は v1.1 が勝つ**。

## 1. 裁定 1 — 強度ラベル（v1.0 §5「因果主張の強度ラベル」への訂正）

v1.0 §5 が新設した **C0–C3 記号の導入は禁止**する。理由: 既存 repo で
`C1`/`C2`/`C3` が既に別の意味で使用済み（例: `voice_genesis/README.md` の
検証ラウンド番号 C1–C6 等）であり、また既存の裁定語彙
（`SUPPORTED`/`REFUTED`/`UNDETERMINED`・`confounded`/`provisional`・
`HUMAN_CONFIRMED`/`machine_effect_only`・`NOT_ESTABLISHED` 等）は
同一の強弱軸上に並ぶものではなく、単純な変換規則で C0–C3 へ落とせない。

**代替**: claim 単位の独立フィールド

```yaml
causal_evidence_strength: descriptive | suggestive | moderate | strong
```

を追加する。既存ラベルは書き換えず `source_verdict` として原文のまま保存する
（2 つのフィールドが並立し、どちらも消えない）。

**v1.0 §9 Phase D5 の読み替え**: 「C0–C3 へ再分類」ではなく
「**既存 verdict を保持したまま、claim 単位で `causal_evidence_strength` を
付与する**」へ変更する。

## 2. 裁定 2 — fixed probe（v1.0 §3 A-3・§6 Run Contract「fixed probe set」への訂正）

WAV 本体の Git 保存は要求しない。既定の凍結様式は
`freeze_mode: pinned_regenerable` とする。

固定対象（machine-verifiable な pin 一式）:

- WAV sha256 / PCM sha256
- checkpoint sha256
- ONNX / config / input の sha256
- code commit
- seed
- execution profile
- 再生成コマンド
- 再生成後の hash 一致（照合結果）

`voice_genesis/README.md` の「WAV は非同梱（決定論再生成可能）」方針は維持する。
再生成が不能な場合に限り、immutable artifact store 等の代替手段を例外的に
利用してよい。それも不可能な場合は `BLOCKED` として記帳する（推測で埋めない）。

## 3. 裁定 3 — Track C（Genome Architecture PoC、v1.0 §3 Track C への訂正）

R0–R4（`Ritsu Identity + PJS F0/Duration/Release` の PoC セル）の**新規実行は
禁止**する。

**理由**: 既存証拠に包摂される（`subsumed_by_existing_evidence`）。

- Genome S3（`genome_s3/results/S3_RECORD.md`）= F0 / Duration / Energy / Release
  の 4 gene が `SUPPORTED`
- S3.5（`genome_s35/`）= f0・duration の知覚差確認
- S4（`genome_s4/`）= Identity 保持・機械 Gate PASS だが ABX 3/4 で
  `NOT_ESTABLISHED`
- S 系列は 2026-08-21 に凍結済み

**初期スコープの状態**: `initial_scope_status: PASS_WITH_RESIDUAL`。

**未解決の残課題**: 複数 gene の知覚的共発現のみ。

**再入条件**: 今後どうしても追実験が必要になった場合は、**旧 S4 の記録を
上書きせず**、新しい experiment ID で別実験として事前登録すること。

## 4. 裁定 4 — Run Contract（v1.0 §6「新規runの負債を増やさない契約」への訂正）

新しい実験契約の「正本」文書は新設しない。**`DESIGN_<run>.md` を唯一の
実験契約正本**とし、その下に機械層を追加する:

```
DESIGN_<run>.md（実験契約の正本・人間可読）
      ↓ hash-bound
機械層:
  RUN_CONTRACT_SCHEMA_v1.json        （projection の必須フィールド定義）
  conformance tests                  （fail-closed 検査）
  DESIGN_<run>.contract_projection.json
    （projection 文書。design_doc_sha256 で正本に hash-bound）
      ↓
  runbook（実行手順。DESIGN を上書き禁止）
      ↓
  artifact / manifest / pins
      ↓
  run record / status / ledger
```

**runbook は DESIGN の内容を上書きしてはならない**
（`runbook_may_override_design: false`）。

**形状テストで fail-closed にする条件**:

- DESIGN の SHA 不一致（DESIGN 本文が改変されたのに projection が
  追随していない）
- baseline run が不明
- intervention 数が単一介入（1）と一致しない
- measurement spec（TRF 測定仕様）が未凍結
- hypothesis 裁定代数（H0–H5 等）が未凍結
- speaker map が不一致
- pin（checkpoint/config/dataset 等）が欠落
- runbook による DESIGN の改変

## 5. 裁定 5 — run4（v1.0 §4 Tier 2「run4」への訂正）

**status = `PASS_WITH_RESIDUAL`**（実行可能性評価の指摘5「大部分返済済み」を
採用、「残作業は台帳転記だけ」は棄却）。

**返済済み（`s3_record_2026-08-17.md` §7.4 で実体照合済み）**:

- checkpoint 5K/10K/20K/40K の実体 + sha256（§7.4-1・4/4 OK）
- dataset pins（`run4_dataset_pins.json`）
- train log（`train_run4.log.gz`）+ TensorBoard（`tb_events_run4.tar.gz`）の
  sha256（§7.4-2）
- anchor WAV 10/10 の hash（§7.1・生成ログ側からも一致確認済み = §7.4-3）
- wav 生成コマンド（5 起動・逐語転記、§7.4-3）
- 複数介入（D3 追加 + User 追加）から個別因果を主張しない規律
  （v1.0 §4 Tier 2「run4で禁止する主張」を維持）
- 過去台帳転記の主要部分（§7.3 の回収指示書 1〜3 が 2026-08-20 に実行・
  ただし 3 は「一部に宣言済みの欠落あり」で閉じている）

**残債 3 件**（`debt_ledger.yaml` VG-DEBT-007/008/009 に対応）:

1. **checkpoint `state_dict` の非有限値検査未実施** — `S3_RUN4_RUNBOOK.md:388-390`
   が定める節目ゲート（`torch.isfinite` を 4 checkpoint 全部へ適用）は
   `s3_record_2026-08-17.md` §7.4-2 の時点で「未実施のまま」と明記されている。
   実施したのは学習ログの文字列走査（`NaN`/`Inf` 不在）までで、
   `state_dict` 自体の非有限値検査ではない。
2. **anchor WAV producer provenance の一部未閉鎖** — checkpoint → ONNX →
   config → generation script → WAV の生成系統のうち、acoustic ONNX /
   canon（NamineRitsu_DiffSinger）/ vocoder のバイト pin と
   `gate_synth_run4.py` 実行時バイトの pin が無い（`s3_record_2026-08-17.md`
   §7.4-3 の限定注記）。run4 側 6 本は「ONNX が 40K checkpoint に由来する」
   ところまで確立、run3 側 4 本はそこも未確立。
3. **exact cost record 欠落** — `s3_record_2026-08-17.md` §7.4-4「未取得」。
   概算 ≈$2.95 のみで確定値は無い。v1.1 裁定で **P3 相当・研究結論に影響しない**
   と位置づける。

**複数介入交絡（D3 追加 + User 追加）は返済不能**につき
`accepted_residual` として確定し、`claim_ceiling: descriptive_observation_only`
を付す。run4 の再学習は不要。

## 6. 実行順（v1.1 確定）

```
1. v1.1 correction record 追加（本ファイル + debt_adjudication_v1.1.yaml）
2. DEBT_LEDGER 語彙・状態更新（debt_ledger.yaml）
3. Run Contract schema + 形状テスト実装
4. run4 state_dict finite 検査（スクリプト用意。実行は checkpoint 保管環境）
5. 必要範囲だけ producer provenance 補完（missing は正直記載のまま残してよい）
6. D0 PASS
7. D1/D2 以降へ進行
8. Track C R0–R4 は再実行しない
```

**D0 は v1.1 裁定追補が正典に入るまで `BLOCKED`**（本コミットで解消する）。

## 7. 変更されないもの

- v1.0 本文は無改変（`DEBT_REPAYMENT_PLAN_v1.0.md` 冒頭の正本注記を参照）
- v1.0 §1–§4・§7–§12 のうち本記録が明示的に触れていない箇所（優先度 P0/P1/P2
  の分類、Track A/B の実行順、Debt Ledger のスキーマ形状、Phase D0–D6 の
  実行順の骨格）はそのまま有効
