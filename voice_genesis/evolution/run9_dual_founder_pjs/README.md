# RUN9 — Tri-Donor Dual-Founder Common-Teacher Learning

**状態: Preregistered / Phase 0.2（design_revision 0.2）。本学習未開始。**

正本設計書: [`DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md`](./DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md)
（uploads 原本とバイト同一・**byte-pin 不変**。sha256 は `RUN9_CONTRACT.yaml` の
`design_doc_sha256` が PINNED で保持する）。v0.1 に対する差分は
[`DESIGN_RUN9_REVISION_0.2.md`](./DESIGN_RUN9_REVISION_0.2.md)（2026-08-24
User 裁定5件、`design_revision_doc_sha256` が PINNED で保持する）が規定する。

AF0・Ritsu・User Donor の三点 Identity から `TRI_CROSSOVER` で二体の
Founder 候補（`R9F-01` = AF0 優勢、`R9F-02` = User 優勢）を出生させ、
同一の PJS 由来 Performance Lesson を同一予算で個体学習させる実験。
詳細は設計書 §0/§1 を参照。

## 2026-08-24 User 裁定5件（Revision 0.2）— 要約

詳細・逐語は [`DESIGN_RUN9_REVISION_0.2.md`](./DESIGN_RUN9_REVISION_0.2.md) を正とする。

1. **学習アーキテクチャ**: `LEARN_PERFORMANCE` の書き込み先を Performance
   Adapter から Founder ごとの versioned **Performance ControlProfile**
   （非ニューラル・明示制御パラメータ）へ変更。Adapter への自動昇格は禁止。
   凍結対象（Backbone/Genome/Identity coordinate/speaker embedding/model
   weights）は不変。v0.1 §13.2 の正規の scope downgrade 記録手続きに基づく。
2. **AF0 anchor 規約**: `anchor_hashes.af0` は `inputs/af0_anchor_manifest.json`
   の正規形 sha256 を pin（**PINNED 済み**）。WAV 実体再生成時は
   `SHA256SUMS.txt` との全件一致必須・不一致時は repin せず停止。
3. **PJS provenance 規約**: source archive pin と expanded corpus pin を
   役割別に区別（**どちらも正しい値・矛盾ではなかった** — 旧ブロッカー(3)
   は誤認と判明し解消）。RUN9 消費用の Lesson manifest は別途生成し
   `lesson_sha` として pin。
4. **User donor rights 規約**: `inputs/rights_manifest.json`
   （Fable 起草・User attest 方式）を追加。`rights_class`/`consent_status`
   = `PENDING_USER_ATTESTATION`。raw 公開・モデル一般配布は別承認
   （初期値 `not_granted`）。
5. **Shared Backbone**: RUN6 phase B 40K checkpoint を採用
   （`backbone_checkpoint_sha` **PINNED 済み**、直接記録4件一致。RUN7 は
   教師交代混入回避のため不使用）。`inputs/backbone_runtime_bundle.json`
   に config/speaker map/phoneme dictionary/vocoder/render 用 DiffSinger
   commit まで含めて記録するが、**`backbone_runtime_bundle_sha` 自体は
   PENDING**（bundle 内 `render_code_commit` が `INFERRED_UNCONFIRMED` —
   Codex bot レビュー PR #316 第1巡指摘採用。ブロッカー(5)参照）。

## 実行順 §22 に対する現在地マップ

設計書 §22 は 0–20 の実行順を規定する。Phase 0.2 時点の現在地:

| step | 内容 | 状態 |
|---|---|---|
| 0 | freeze Run Contract | **部分 pin が拡大**（`design_doc_sha256` / `design_revision_doc_sha256` / `backbone_checkpoint_sha` が新たに PINNED。`backbone_runtime_bundle_sha` は bundle 内 `render_code_commit` が INFERRED_UNCONFIRMED のため PENDING のまま。他も正直に PENDING。`gate_state()` は依然 `BLOCKED`） |
| 1 | verify repository / dependency pins | 未着手（backbone 側は pin 済み。VG-L0 ハーネス自体の依存 pin は未着手） |
| 2 | verify donor and teacher rights / manifests | **AF0/Ritsu は pin 済み・PJS は役割別2値を整理して解消**。**User donor のみ rights attest 待ち**（ブロッカー(1)参照） |
| 3 | build run9 Identity Domain | **af0/ritsu が PINNED、user/metric_space_sha はプレースホルダのまま**（`domains/identity_domain_run9_v1.json`、`is_pinned() == False`） |
| 4 | generate R9F-01:r0 and R9F-02:r0 | **未着手**（`run9_schema.build_founder()` は未 pin domain を構造的に ValueError で拒否する — step 3→4 の機械強制。user anchor 未 pin のため依然ブロック） |
| 5–20 | render / freeze / lesson / learning / evaluation / verdict | 未着手（VG-L0 学習ハーネス自体が未実装 — ブロッカー(3)参照） |

## ブロッカー一覧（正直な現状）

**解消済み（2026-08-24 User 裁定）**:
- ~~AF0 canonical Body hash がローカル results のみ~~ → `inputs/af0_anchor_manifest.json`
  経由で `anchor_hashes.af0` を PINNED 化（AF-P0 の NOT_ESTABLISHED 判定・
  Duration/Energy/AG-alpha 非保持は不変のまま継承）。
- ~~PJS corpus sha256 の二値不一致~~ → 誤認と判明。source archive pin
  （zip 全体）と expanded corpus pin（前処理後コーパス）という**別の対象**
  を指す2つの正しい値であり、矛盾する同一対象への2値ではなかった。
- ~~backbone checkpoint 選定未~~ → RUN6 phase B 40K checkpoint を採用し
  `backbone_checkpoint_sha` を PINNED（`backbone_runtime_bundle_sha` は
  ブロッカー(5)参照 — 未解消）。

**残存**:

1. **User donor rights attest 待ち**: `inputs/rights_manifest.json`
   （Fable 起草済み）は `rights_class`/`consent_status` =
   `PENDING_USER_ATTESTATION`。User の確認前は `anchor_hashes.user` を
   pin しない（DESIGN_RUN9_REVISION_0.2.md 改訂4）。raw 音源公開・モデル
   一般配布は rights anchor 使用可否とは別承認（初期 `not_granted`）。
2. **`metric_space_sha` 未 pin**: identity domain の3つ目の必須 pin
   （anchor_hashes 3件とは別欄）。校正/採用する metric space の選定が
   未着手。
3. **VG-L0 学習ハーネス未実装**: `LEARN_PERFORMANCE` エッジ（改訂1で
   書き込み先を Performance ControlProfile へ変更済みだが、ハーネス自体
   の実装は未着手）。設計書 §13 の Adapter Entry Gate 相当（control-layer
   ceiling evidence / calibrated Identity audit route / learning replay
   harness / rights-clean curriculum / fixed compute budget / frozen
   recipe / rollback path）はどれも準備段階にすら入っていない。
4. **PJS Performance Lesson build 未実施**: 改訂3で pin 方針
   （source archive pin / expanded corpus pin とは別の Lesson manifest を
   生成し `lesson_sha` として pin）は確定したが、Lesson build 自体は
   VG-L0 ハーネス実装待ち。
5. **`render_code_commit` の確定待ち**（Codex bot レビュー PR #316 第1巡
   指摘採用）: `inputs/backbone_runtime_bundle.json` の
   `render_code_commit`（`openvpi/DiffSinger @ e2307b1...`）は
   `status: "INFERRED_UNCONFIRMED"` — run4〜8 全体での単一リビジョン一貫
   使用・反証なしという状況証拠のみで、RUN6 export の直接記録
   （`results_s5/s5_record_2026-08-20.md`）自体にはこの commit が明記され
   ていない。**直接記録の発掘、または User attestation で確定するまで
   `backbone_runtime_bundle_sha` は PENDING のまま**（`backbone_checkpoint_sha`
   単体は直接記録4件一致のため PINNED 継続 — 対象を混同しない）。

**erratum（設計書内部の記述不一致、Codex bot レビュー PR #315 第6巡指摘1
— 上記5裁定とは別件）**: DESIGN_RUN9 §6 は `parent_designs` を5件宣言する
が、同じ設計書 §23 の Run Contract 雛形は3件しか列挙しておらず、依存2件
（VoiceGenesis Singing Baseline v0.1 / VoiceGenesis Supplement A・
Selection Pressure Routing）が欠落していた。設計書は byte-pin 済みのため
一切編集せず、完全側の §6 を正として `RUN9_CONTRACT.yaml` の
`parent_designs` を5件へ是正した（v0.2 改訂時に §23 を §6 へ同期すべき）。

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

**改訂1（Performance ControlProfile）を CompositionScore の
`control_profile`（`docs/control_profile.md`）と混同しない**:

両者は偶然の同名だが**別スキーマ・別ドメイン**。CompositionScore 側は
生成器（Suno/MusicGen）ごとの `grip_class` 自己記述ブロックであり、RUN9
側は VoiceGenesis Founder の Performance 制御パラメータの版付き集合。
`DESIGN_RUN9_REVISION_0.2.md` 改訂1に明記済み。

## ディレクトリ構成（Phase 0.2 時点）

```
run9_dual_founder_pjs/
├── DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md   # 正本（バイト同一コピー・不変）
├── DESIGN_RUN9_REVISION_0.2.md                                 # v0.1 差分メモ（2026-08-24 User 裁定5件）
├── RUN9_CONTRACT.yaml                                          # §23 Run Contract（部分 pin。af0/ritsu/backbone は PINNED）
├── README.md                                                   # 本ファイル
├── run9_schema.py                                              # domain / TRI_CROSSOVER / contract の run-local 正本
├── domains/
│   └── identity_domain_run9_v1.json                            # af0/ritsu PINNED・user/metric_space_sha はプレースホルダ
├── inputs/
│   ├── af0_anchor_manifest.json                                # AF-P0 正典証拠の複合参照 manifest（anchor_hashes.af0 の入力）
│   ├── rights_manifest.json                                    # User donor rights（PENDING_USER_ATTESTATION）
│   └── backbone_runtime_bundle.json                            # RUN6 backbone の checkpoint/config/vocoder/render commit 一式
├── tests/
│   └── test_run9_contract.py                                   # §27 最低テストの静的検証可能サブセット + Revision 0.2 対応テスト
└── results/
    └── .gitignore                                              # 実測結果は非同梱（§25 Atomic Results Bundle 用の空ディレクトリ）
```

設計書 §24 が推奨する `founders/` / `lesson/` / `learning/` / `evaluation/`
は、それぞれに実体を置く段階（実行順 step 4 以降）になってから作成する。
内容の伴わない骨組みは実装が進んだという誤った印象を与えるため、
先行して同梱しない。
