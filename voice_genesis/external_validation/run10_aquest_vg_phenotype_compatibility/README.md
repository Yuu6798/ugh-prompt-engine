# RUN10 — AQUEST / VoiceGenesis External Synthetic Voice Phenotype Compatibility Audit

`run_id: RUN10` / `experiment_id: VG-R10-AQUEST-VG-PHENOTYPE-COMPATIBILITY`

AquesTalk 由来 UTAU デフォルト音声を**未知標本**として観測し、VoiceGenesis の
Source / Identity 形質体系と測定器を同じ条件で適用したときに、どの形質が
直接互換 / 類似互換 / 部分互換 / 非対応 / 測定不能となり、どの安定特徴が
現行 VoiceGenesis 形質語彙では説明できないかを比較地図として記録する Run。

**AquesTalk の Voice Identity や Performance を VoiceGenesis へコピーする Run
ではない。** Phase B（生成検証）は自動開始せず、Phase A と独立校正が成立し
`PHASE_B_ENTRY` Gate を通過した場合にのみ起動する。

## 正本設計

正本は Google Drive 上の凍結文書である。

| 項目 | 値 |
|---|---|
| 題名 | `VoiceGenesis_RUN10_AQUEST_VG_Phenotype_Compatibility_Audit_v0.4.md` |
| 実バイト sha256 | `cc05f0254aa1ee4a7302edac847a3d07d2fd385f115865185bcfe1343a350957` |
| サイズ | 95,709 bytes |
| 場所 | Drive `run10/00_design/`（private） |

**設計文書本文は本リポジトリへ commit しない。** 本リポジトリ
`Yuu6798/ugh-prompt-engine` は public であり、設計 §2.2 が
「分析表、集計値、設計文書の外部公開可否は今回の回答だけでは確定しないため、
本Runでは公開しない」と規定しているためである。参照は上記 sha256 pin による。

旧 RUN10 案 `VoiceGenesis_RUN10_Known_Performance_Trainability_Test_v0.1` は
実行前に `SUPERSEDED_BEFORE_EXECUTION`（設計 §1.2、`RUN10_CONTRACT.yaml`
の `supersedes`）。同じ `RUN10` として併走させてはならない。

## 公開境界（R10-PUB-1）— User 裁定済み 2026-08-27

| | |
|---|---|
| 争点 | RUN10 実装ツリーが置かれた本リポジトリは **public** である。設計 §2.2 / §26 は `publication_scope: PRIVATE_ONLY` を要求する。 |
| 関係する Stop Rule | §32-2（private-only storage cannot be guaranteed）/ §32-16（public upload detected） |
| 裁定者 | User（§33） |
| 裁定 | **`APPROVED_CODE_ONLY_PUBLIC`**（2026-08-27）— 「実装コードのみ public で継続」 |
| 常設方針 | AQUEST 由来資産・音声・モデル・blind map・測定値・集計表・設計文書本文は public リポジトリへ置かず Drive 側 private に留める。public に置くのは実装コード・契約 YAML・AF01 ハッシュ台帳・測定値を含まない構造 manifest のみ。`private_boundary.py` が機械強制する。 |
| 残件 | §26 private staging root の実体が未確定のため `private_storage_policy_sha` は依然 PENDING（`residual_unresolved`）。 |

機械可読な正本は `inputs/private_storage_policy.json` の `blocking_question`。

## 現在の Gate 状態

| Gate | 状態 | 理由 |
|---|---|---|
| R10-G0 `RUN_CONTRACT_COMPLETE` | **BLOCKED** | Core pin 欄に PENDING が残る（§31 cost cap 4 欄を含む） |
| R10-G1 `RIGHTS_AND_PRIVATE_BOUNDARY` | **BLOCKED** | R10-PUB-1 は裁定済み。AQUEST 回答アーカイブ未固定 / private staging root 未確定 |
| R10-G2 `PRE_RUN_INVENTORY_COMPLETE` | **BLOCKED** | A0 未取得 / AF01 bundle 実体未取得 / meter 未実装 |
| R10-G3 以降 | 未到達 | — |

`pre_run/inventory.json` が機械可読な正本（`gate_state: BLOCKED`）。

### 実測済みで PASS しているもの

AF01 v1.0 の **payload ledger 検証**（§29 手順 6 の第 1 段）は実バイトで完了している。

```bash
python voice_genesis/external_validation/run10_aquest_vg_phenotype_compatibility/af01_freeze_verifier.py
# → verdict: PASS （15/15 checks）
```

- 同梱台帳 `inputs/af01_payload_sha256sums.txt`（8,025 bytes）の実バイト sha256
  が `af01_payload_ledger_sha256 = d447aa1b…` と一致
- 台帳 101 エントリの構造が `FREEZE_REGISTRATION.json` の宣言と一致
  （75 unit WAV = 25 alias × C3/C4/G4 / 9 E0 fixture + truth manifest /
  6 aggregate probe / oto.ini × 3）
- canonical 4 点（`AF01.json` / `generator_AF01_SF1.py` / `founder_manifest.json` /
  `AF01_all25_units_C4.wav`）が設計 §7.3 の凍結値と一致

未実行なのは bundle 実体との照合（§29 手順 6 の第 2 段）と決定論的 payload
replay（手順 7）である。いずれも 8.9 MB の bundle 実体を要し、Drive MCP 経由では
取得できなかった（セッションが落ちる）。bundle をローカルに置ければ

```bash
python af01_freeze_verifier.py --bundle-root <AF01_v1.0 展開先>
python af01_freeze_verifier.py --bundle-root <AF01_v1.0 展開先> --replay
```

で実行できる。

## ディレクトリ

```text
run10_aquest_vg_phenotype_compatibility/
├── README.md                     # 本ファイル
├── RUN10_CONTRACT.yaml           # §23 Run Contract（現状 R10-G0 = BLOCKED）
├── run10_schema.py               # 契約ローダ / 分類 enum / Gate 登録簿（fail-closed）
├── private_boundary.py           # §2.2 / §24 / §26 公開境界の機械強制
├── af01_freeze_verifier.py       # §29 手順 6/7 の AF01 凍結検証
├── inputs/
│   ├── af01_payload_sha256sums.txt   # AF01 v1.0 凍結台帳（実バイト同一）
│   ├── rights_manifest.json          # §2.2 権利境界（DRAFT_NOT_FROZEN）
│   └── private_storage_policy.json   # 保管方針（R10-PUB-1 裁定済／staging root 残件）
├── pre_run/
│   ├── build_pre_run_inventory.py    # §29 手順 3/5
│   └── inventory.json                # R10-G2 の機械可読状態
├── results/                      # §26 private bundle（.gitignore 以外を commit しない）
└── tests/                        # §28 最低テストの静的検証可能サブセット（158 件）
```

設計 §24 が挙げる `calibration/` `measurement/` `evaluation/`
`synthesis_validation/` `corpus/` は未着手のため作成していない
（空ディレクトリで「実装済み」に見せない）。

## 次の実装単位

`§29 実行順` に対する現在位置と、次に着手できる単位:

| 手順 | 内容 | 律速 |
|---|---|---|
| 0 | v0.4 承認と Core Run Contract freeze | **User 裁定**（cost cap 4 欄。R10-PUB-1 は裁定済み） |
| 1 | AQUEST 回答と権利境界の archive/pin | User（原文の private archive 化） |
| 2 | repository / dependencies / private storage の検証 | **完了**（R10-PUB-1 = 裁定済み。staging root のみ残件） |
| 3 | Pre-Run Inventory 実行 | **実装済み**（結果は BLOCKED） |
| 4 | A0 voicebank の inventory と hash | **User 供給待ち**（machine-dependent） |
| 5 | Evolution Theory 参照の解決 | v0.3 本体がリポジトリ内に不在 |
| 6 | AF01 payload ledger 等の検証 | **台帳段階まで完了**／実体照合は bundle 待ち |
| 7 | AF01 決定論的 payload replay | bundle 実体待ち |
| 8 | AF01 V1 生成 | transport 経路の選定が未裁定 |
| 9 | E0 の truth / code independence 検証 | bundle 実体待ち |
| 10–11 | neutral carrier manifest と Performance 不在検証 | resampler / wavtool 選定待ち |
| 12–16 | 内部校正 → E0 外部校正 → 数値判定則 freeze | §11 measurement family の実装が前提 |

機械側だけで進められる次の単位は **§11 measurement family（M0–M6）の実装**
と **§12 内部校正 fixture の生成**である。ただし E0 外部校正（手順 14）は
AF01 bundle 実体を要する。

## 主張の天井（§5.3）

```yaml
measurement_compatibility_claim: C2
external_schema_validity_claim: C1-C2
trait_identity_equivalence_claim: C0    # 未確立
performance_claim: C0                   # 対象外
transfer_or_reconstruction_claim: C0    # 未確立
generative_trait_compatibility_claim: C1-C2
```

単一 `total_score` は恒久禁止（§14.5）。`run10_schema.assert_no_forbidden_score_field()`
が再帰的に強制する。

## 設計文書内 erratum

1. **`design_revision`** — 文書ヘッダと §37 は v0.4 だが、§23 Run Contract 雛形と
   §27 results schema は `0.3` のまま。v0.4 で AF01 v1.0 凍結登録という実体的改訂が
   入っているため、本実装は **`"0.4"` を正典**とする。
2. **章番号 `# 37` の重複** — 「37. 最終原則」と「37. v0.4 Revision Record」が併存する。
   本実装は章番号ではなく見出し文字列で参照する。

いずれも凍結ハッシュ・Gate 集合・enum には影響しない。

## レビュー由来の設計強化（PR #330 Codex 第 1 巡）

自動レビュー 7 件（P1×5 / P2×2）を全件採用した。いずれも「偽成功経路 / 将来汚染」
に該当する。

1. **公開境界を閉世界 allowlist へ反転** — 拒否リストでは将来の `measurement/`
   `calibration/` `evaluation/` に置かれる測定値・集計表が素通りしていた。
   公開してよいものを列挙し、それ以外を拒否する方式へ変更
   （`private_boundary.PUBLISHABLE_DATA_FILES` へ明示登録しない JSON/TXT は commit 不可）。
2. **results の evidence 要求** — 構造だけの空文書で `protocol_verdict: PASS` +
   `COMPATIBILITY_MAP_ESTABLISHED` を記録できた。outcome ごとに必要な evidence 節と
   R10-G0..G14 の Gate 台帳全 PASS を要求する。
3. **PINNED 値の形式契約** — `pinned::resampler_sha` のようなプレースホルダで
   R10-G0 を開けた。sha256 は小文字 16 進 64 桁、`repository_commit_sha` は git object
   形式、`minimum_generatable_traits` は正の整数、cost cap は正の数を要求する。
4. **replay 前の generator 認証** — 台帳だけ検証して bundle 側の generator を実行して
   いたため、drift を検出するはずの検証器が drift した任意の Python を実行する経路に
   なっていた。実行前に実バイトを認証し、実行後に再 hash して mutation の窓を閉じる。
5. **収録ピッチ inventory は未実施なら blocking** — 「未実施だから cross-pitch を
   要求しない」は成立しない。単一ピッチが**確定した**場合にのみ NOT_EVALUABLE へ routing する。
6. **`FREEZE_REGISTRATION.json` の形状検証** — 存在するだけで required item を満たして
   いた。schema / 凍結ハッシュ / 構造量の宣言を凍結値と照合する。
7. **cost cap を R10-G0 の対象へ** — cap が PENDING のまま G0 が PASS すると上限なしで
   課金作業を開始できた（§31 / §32 Stop Rule 20）。
