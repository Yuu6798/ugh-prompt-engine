# RUN9 — Tri-Donor Dual-Founder Common-Teacher Learning

**状態: Preregistered / Phase 0 scaffold のみ。本学習未開始。**

正本設計書: [`DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md`](./DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md)
（uploads 原本とバイト同一。sha256 は `RUN9_CONTRACT.yaml` の
`design_doc_sha256` が PINNED で保持する）。

AF0・Ritsu・User Donor の三点 Identity から `TRI_CROSSOVER` で二体の
Founder 候補（`R9F-01` = AF0 優勢、`R9F-02` = User 優勢）を出生させ、
同一の PJS 由来 Performance Lesson を同一予算で個体学習させる実験。
詳細は設計書 §0/§1 を参照。

## 実行順 §22 に対する現在地マップ

設計書 §22 は 0–20 の実行順を規定する。Phase 0 時点の現在地:

| step | 内容 | 状態 |
|---|---|---|
| 0 | freeze Run Contract | **部分 pin**（`RUN9_CONTRACT.yaml` — `design_doc_sha256` のみ PINNED、他は正直に PENDING。`gate_state()` == `BLOCKED`） |
| 1 | verify repository / dependency pins | 未着手 |
| 2 | verify donor and teacher rights / manifests | 未着手（ブロッカー(1)(3)参照） |
| 3 | build run9 Identity Domain | **ドラフトのみ**（`domains/identity_domain_run9_v1.json` — anchor_hashes はプレースホルダ、`is_pinned() == False`） |
| 4 | generate R9F-01:r0 and R9F-02:r0 | **未着手**（`run9_schema.build_founder()` は未 pin domain を構造的に ValueError で拒否する — step 3→4 の機械強制） |
| 5–20 | render / freeze / lesson / learning / evaluation / verdict | 未着手（VG-L0 学習ハーネス自体が未実装 — ブロッカー(4)参照） |

## ブロッカー一覧（正直な現状）

1. **User donor consent/rights manifest 未整備**: `voice_genesis/foundry/recording_kit/user_donor_ledger.json`
   は17件の `source_sha256`/`sha256`/収録メタデータを持つが、consent /
   rights class の欄が無い。設計書 §7.3「本学習開始前に必須: consent /
   rights class」を満たすには User 裁定が必要。
2. **AF0 canonical Body hash がローカル results のみ**: `voice_genesis/foundry/artificial_founder/founder_specs/AF0.json`
   は spec を持つが、設計書 §7.1 が要求する「AF-P0 canonical Body /
   DonorBank ingestion 成果」のハッシュは gitignore 下のローカル results
   にのみ存在し、repo には同梱されていない。
3. **PJS corpus sha256 の二値不一致**: `voice_genesis/foundry/s1_dataprep/README.md`
   記載の PJS corpus zip 全体 sha256
   （`683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca`）と、
   `voice_genesis/foundry/adapter/presets/pjs_neutral.json` の
   `corpus_sha256`（`9905cec08fbaf43fa545400498a7908ef28567e8f60a5ba005fb2e00d526f996`）
   が一致しない。どちらを `lesson_sha` の権威ソースとするか要裁定
   （zip 全体 vs 前処理後コーパスの違いである可能性があるが未確認）。
4. **VG-L0 学習ハーネス未実装**: `LEARN_PERFORMANCE` エッジ（Performance
   Adapter の訓練・凍結・replay）自体が未実装。設計書 §13 の Adapter
   Entry Gate（control-layer ceiling evidence / calibrated Identity audit
   route / learning replay harness / rights-clean curriculum / fixed
   compute budget / frozen recipe / rollback path）はどれも準備段階にすら
   入っていない。学習ハーネスの実装自体が本 Phase 0 の後続タスク。
5. **backbone checkpoint 選定未**: `backbone_checkpoint_sha` を埋めるための
   run5 系 checkpoint の選定・sha 転記が未実施。

## 設計判断の記録

**`TRI_CROSSOVER` を `voice_genesis/evolution/operators.py`（VG-E0）へ
追加せず run-local（`run9_schema.py`）にした理由**:

- 設計書 §8「既存 VG-E0 の凍結三角形は `ritsu / pjs / user` である。
  PJSを教師専用にしAF0を加えるRUN9では、既存schema・既存台帳をin-place
  変更しない」という明示的な指示に従う。
- `voice_genesis/evolution/models.py` の `ANCHOR_NAMES = ("ritsu", "pjs",
  "user")` / `VALID_OPERATORS` は他の多数の VG-E0 モジュール（`simplex.py`
  / `operators.py` / `ledger.py` / `archive.py` / `bootstrap.py` とその
  台帳データ）が前提とする凍結値であり、`af0` を追加する4点化や `pjs` を
  anchor から teacher へ役割変更する改訂は、VG-E0 の genome_id 計算・
  lineage 判定・archive セルグリッドの意味論を破壊し、既存台帳の
  再検証を要求する非互換変更になる。
- RUN9 は新しい run-local domain `run9-af0-ritsu-user/1.0`
  （anchor_order: af0, ritsu, user）を独立実装することで、VG-E0 の
  schema バージョンを上げずに三点構成を差し替えられる。`run9_schema.py`
  はモジュールレベルで `models.py`/`simplex.py` を import しない
  （`tests/test_run9_contract.py` の回帰テストがこれを直接検証する）。
- 副次的な利点: PJS を Identity anchor 空間から構造的に排除する要件
  （設計書 §27 item 10「PJS coordinate is structurally impossible」）を、
  run9 独自の `Run9Coords`（af0/ritsu/user の3フィールドのみ）で型レベル
  から強制できる。VG-E0 の `Coords` 型（ritsu/pjs/user）を流用すると
  pjs フィールドが常に存在してしまい、この禁止をコード構造で表現できない。

## ディレクトリ構成（Phase 0 時点）

```
run9_dual_founder_pjs/
├── DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md   # 正本（バイト同一コピー）
├── RUN9_CONTRACT.yaml                                          # §23 Run Contract（部分 pin）
├── README.md                                                   # 本ファイル
├── run9_schema.py                                              # domain / TRI_CROSSOVER / contract の run-local 正本
├── domains/
│   └── identity_domain_run9_v1.json                            # anchor_hashes 未 pin のドラフト
├── tests/
│   └── test_run9_contract.py                                   # §27 最低テストの静的検証可能サブセット
└── results/
    └── .gitignore                                              # 実測結果は非同梱（§25 Atomic Results Bundle 用の空ディレクトリ）
```

設計書 §24 が推奨する `inputs/` / `founders/` / `lesson/` / `learning/` /
`evaluation/` は、それぞれに実体を置く段階（実行順 step 1 以降）になって
から作成する。Phase 0 では空ディレクトリを先行して同梱しない（git は
空ディレクトリを追跡できず、内容の伴わない骨組みは実装が進んだという
誤った印象を与えるため）。
