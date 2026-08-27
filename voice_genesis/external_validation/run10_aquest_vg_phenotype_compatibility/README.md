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
└── tests/                        # §28 最低テストの静的検証可能サブセット（301 件）
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

### 第 2 巡（P1×3 / P2×2、全件採用）

うち 2 件は第 1 巡の修正が新たに作った穴である。

1. **`.ini` の suffix bypass** — allowlist 反転時に `.ini` を一律公開可にしたため、
   AQUEST voicebank の `oto.ini` が素通りしていた。`.ini` / `.cfg` を拡張子
   allowlist から外し、`oto.ini` / `character.txt` / `prefix.map` /
   `readme.txt` を常時 private 扱いにした。
2. **evidence 節がスカラを受理** — 「空でなければよい」判定だったため
   `compatibility_matrix: placeholder` が通り、mapping でないためエントリ検証も
   飛んでいた。evidence は**非空 mapping** を要求する。
3. **R10-G15 が要求 Gate 集合から欠落** — Phase B を authorize する Gate 自体を
   落としたまま `GENERATIVE_COMPATIBILITY_ESTABLISHED` を名乗れた。
4. **`--replay` が bundle 無しで exit 0** — 自動化が §29 手順 7 を「成功」として
   記録できた。`--bundle-root` を必須にし、手順 6 を通過した場合にのみ手順 7 へ進む。
5. **非有限 cost cap** — YAML の `.inf` は `<= 0` を通り抜け、`.nan` はあらゆる
   順序比較が False になるため、有限の上限なしに R10-G0 が開いた。`math.isfinite` を要求する。

### 第 3 巡（P1×3 / P2×1、全件採用 + ファミリー終端宣言）

1. **公開境界を「コード以外はパス列挙」へ最終化** — 第 2 巡で `.ini` を外したが、
   `.md` / `.yaml` は拡張子で一律許可のままだったため
   `evaluation/aggregate_table.md` や `measurement/compatibility_matrix.yaml` が
   ツリーのどこにでも置けた。拡張子だけで公開可になるのは `.py` のみとし、
   それ以外は `PUBLISHABLE_DATA_FILES` へのパス単位列挙を必須にした。
   **この方式変更で suffix bypass のファミリーを終端する。**
2. **evidence 節の形状契約** — 非空 mapping であるだけでは `{"placeholder": true}` が
   通っていた。設計が節ごとに明示する固定欄（§7.6/§12.5 の E0 回収と overfit 信号、
   §14.6 の 4 欄、§18 の ΔA/ΔV/D_output、§14.2 の replay 3 欄）を要求し、
   `compatibility_matrix` の各行にも §15.1/§20.1 の `support` / `calibration` /
   `holdout` を要求する。
3. **Phase B 由来 outcome の不変条件** — `GENERATIVE_COMPATIBILITY_ESTABLISHED` だけを
   `phase_b_entry=ENTER` に縛っていたため、`MEASUREMENT_ONLY_COMPATIBILITY`（§16.3）を
   SKIP のまま名乗れた。synthesis 由来の結論を一括で縛る。
4. **bundle 外への symlink** — 外部ファイルへの symlink を並べた薄いディレクトリが
   「完全な AF01 bundle」として通り得た。台帳パスの字句検査と解決後の
   containment を hash 前に要求する。

#### 境界宣言 — evidence 検証の深さ

evidence 節の形状契約は、**DESIGN_RUN10 が節ごとに明示している欄まで**を要求する。
各欄の内側の値域・単位・数値妥当性は、§11 measurement family と §14.6
`measurement_decision_spec` が実装されるまで検証しない。存在しない測定契約を
先取りして発明しないためであり、深化は measurement 層の実装 PR で
`_EVIDENCE_SECTION_SHAPE` を拡張して行う。同一領域 3 巡（AGENTS.md §3-4）に
達したため、本 PR ではこの境界で終端する。

### 第 4 巡（P1×4 / P2×1、全件採用）

第 3 巡で置いた境界宣言は「evidence 節の**内側の深さ**」に関するもので、今回の
5 件はいずれも別軸のため採用した。

1. **§16 enum が宣言だけで未適用** — `GENERATIVE_STATUS` を一度も使っておらず、
   `synthesis_status: NOT_A_REAL_STATUS` のまま生成互換の成立を記録できた。
   `assert_generative_entry()` を新設し、§15.5 の
   AQUEST_ONLY_CANDIDATE → NOT_SYNTHESIS_ELIGIBLE 拘束も同時に強制する。
2. **`MEASUREMENT_OVERFIT_DETECTED` に evidence 要求が無かった** — 最小の
   BLOCKED 文書だけで Outcome C を名乗れた。あわせて「成立側 outcome」
   （`_ESTABLISHED_OUTCOMES`）と「evidence 要求表」（`_EVIDENCE_FOR_OUTCOME`）を
   分離した。overfit は成立の主張ではなく有効な否定的診断であり、
   BLOCKED でも成り立つが evidence は要る、という二つの性質を両立させるため。
3. **台帳の TOCTOU 窓** — hash した後に読み直していたため、構造量と canonical
   4 点さえ保てば残りの payload 集合をすり替えられた。`read_and_verify_ledger()`
   が 1 回だけ読み、そのバッファを hash して同じバッファを parse する。
4. **claim ceiling の語彙が未凍結** — 任意の非空文字列を許していたため
   `performance_claim: C2` が通った。§5.3 の凍結値と完全一致を要求する。
5. **inventory の非 atomic write** — 追跡中の正典を in-place truncate していた。
   リポジトリの atomic write 集約実装（`svp_rpe.utils.atomic_io`）へ委譲する。

### 第 5 巡（P1×2、全件採用 + 同型の逆方向も併せて掃討）

指摘は 2 件だが、いずれも「evidence が結論と矛盾したまま正典結果を記録できる」
同型だったため、outcome × Run 状態量の整合を 4 規則の閉世界表へまとめて終端した。

| 規則 | 内容 | 由来 |
|---|---|---|
| 1 | synthesis 由来 outcome は `phase_b_entry=ENTER` が前提 | 第 3 巡 |
| 2 | `PHASE_B_NOT_ENTERED` は `ENTER` と両立しない | **第 5 巡 指摘** |
| 3 | `MEASUREMENT_OVERFIT_DETECTED` は `measurement_overfit_signal=true` を要求 | **第 5 巡 指摘** |
| 4 | `measurement_overfit_signal=true` のまま成立側 outcome は名乗れない | 同型の逆方向（当方で追加） |

規則 3 は、第 4 巡で「false を欠落と混同しない」ために存在判定へ直した副作用で
`measurement_overfit_signal: false` が通っていたもの。規則 4 は指摘に含まれないが、
片方だけ塞ぐと矛盾記録の穴が残るため同時に掃討した（§12.6 / §21 R10-G7:
E0 校正に失敗した meter は `CALIBRATED_EXTERNAL` になれず、§15.1 はそれを
`DIRECT_COMPATIBLE` の必要条件にしている）。

#### 境界宣言 — 整合検証の範囲

縛るのは outcome と **Run 全体の状態量**（`phase_b_entry` /
`measurement_overfit_signal`）の整合までである。trait 単位の値と outcome の整合
（例: 全行 `NO_STABLE_MAPPING` なのに `COMPATIBILITY_MAP_ESTABLISHED` を名乗る）は、
§14.6 の判定則が数値で freeze されるまで検証しない。何をもって「地図が成立した」と
するかは `measurement_decision_spec` が定義するものであり、実装側が発明しない。

### 第 6 巡（P1×1 / P2×1、全件採用 + 2 ファミリーを終端）

1. **outcome 同士の矛盾** — `scientific_outcome` は list であり、各要素を enum 照合
   するだけだったため `COMPATIBILITY_MAP_ESTABLISHED` と
   `NO_STABLE_CROSS_SYSTEM_MAPPING` を並べた正典結果が通った。設計本文から一意に
   矛盾と読める 14 対を閉世界で拒否し、重複記載も拒否する。矛盾検査は evidence の
   充足より**先**に走らせる（矛盾した結論に「evidence が足りない」と報告するのは誤導）。
2. **登録ファイルの containment** — 第 3 巡で payload 側の symlink は塞いだが、
   台帳対象外の `FREEZE_REGISTRATION.json` は除外扱いのまま symlink を辿っていた。
   containment 判定を `containment_violation()` の単一実装へ寄せ、payload と
   登録ファイルの両方に同じ規則を適用する。**これで symlink ファミリーを終端する。**

#### 境界宣言 — outcome 組み合わせの検証範囲

拒否するのは設計本文から一意に矛盾と読める対だけである。許容される組み合わせの
完全な束は DESIGN_RUN10 が定義していないため実装側で発明しない。特に
`GENERATIVE_COMPATIBILITY_ESTABLISHED` と `MEASUREMENT_ONLY_COMPATIBILITY` は
§16.1 / §16.3 が trait 単位の分類であり、Run 単位で相互排他とは判定しない。

### 第 7 巡（P1×2、全件採用）

1. **overfit 信号と R10-G7 / verdict の整合** — 提出された Gate 台帳が G0..G14 を
   PASS と主張するだけで `protocol_verdict: PASS` の overfit 結果が成立していた。
   §21 R10-G7 / §12.6 により E0 校正に失敗した meter は外的妥当性を確立しないため、
   信号が true なら R10-G7 を PASS にできない（結果として verdict PASS も成立しない）。
2. **実行バイトの構成的な束縛** — 第 4 巡で入れた before/after hash は、hash 直後に
   差し替えて実行後に戻せば両方一致したまま任意の Python が走るため、実行バイトを
   束縛していなかった。generator を **1 回だけ読んでその buffer を hash し、
   認証済み複製を専用ディレクトリへ書き出してそれを実行する**方式へ変更。
   hash と open の窓自体を無くした。bundle 側の書き換えは
   `bundle_generator_unchanged_after_run` が追加診断として捉える。

この変更で自己書き換え検出の意味論が変わった。複製の自己書き換えは「既に認証済み
バイトを実行した後」の出来事となり判定へ影響しない。該当テストは新しい意味論を
検証する内容へ書き換えた（旧テストは bundle 側 hash に依存していた）。

### 第 8 巡（P1×1）— **初の見送り + 脅威モデル境界宣言**

指摘: 認証済み複製も pathname 経由で再 open するため、hash と open の間に
同一ユーザ権限の並行プロセスが差し替えれば任意の Python が走る。

**技術的には正しいが見送る。** generator 認証軸はこれで 3 巡目
（第 4 巡「実行前に認証せよ」→ 第 7 巡「複製を実行せよ」→ 第 8 巡「複製も
再 open するな」）であり、毎巡で想定攻撃者が強くなっている。今回の攻撃者は
**検証プロセスと同一ユーザ権限を持つ能動的攻撃者**で、この能力があれば
python インタプリタの差し替え・LD_PRELOAD・ptrace・検証器自体の書き換え・
レポートの偽造ができる。in-process の緩和で防げる相手ではない。

`af01_freeze_verifier.py` の docstring に脅威モデル境界を明文化した
（`docs/DESIGN_M2_extraction_accuracy.md` §6 が同型の争点に対して 2026-07-29 に
確立した境界と同じ線）。本検証器が守るのは **受動的ドリフトの tamper-evidence**
であり、能動的攻撃者への完全な tamper-proofing ではない。

**再入条件**: RUN10 の検証を multi-tenant / 共有ユーザ環境で実行する運用へ
変わった場合は本境界が無効になり、子プロセスを pathname ではなく認証済み
buffer 自体（stdin 経由、または inode を掴んだ fd 経由）へ束縛する実装へ改める。

### 第 9 巡（P1×3、全件採用）

1. **設計文書 hash が契約欄でなかった** — 契約は題名しか持たず、`DESIGN_DOC_SHA256`
   は YAML コメント（= parse 時に捨てられる）にしかなかった。同題名で差し替えられた
   Drive 文書と区別できず、どの v0.4 が gate と erratum を供給したか証明できない。
   `design_doc_sha256` を必須構造欄にし、`verify_design_document()` で手元の文書を
   実バイト照合できるようにした。設計文書は repo に置かない（§2.2）ため、これが
   唯一の来歴束縛である。
2. **決定論的 replay が独立の必須項目でなかった** — `--af01-bundle-root` を渡すと
   手順 6 のバイト照合だけで `af01_complete_bundle` を PRESENT にしており、
   凍結 generator が payload を再生成できない参照の上で R10-G2 が COMPLETE に
   なり得た。`af01_deterministic_replay` を独立項目として新設し、未実行は
   UNRESOLVED かつ blocking で残す（`--af01-replay` で実行）。
3. **R10-G15 を ENTER のときだけ要求していた** — §29 手順 35 の Entry 裁定は
   ENTER でも SKIP でも一度行われる。裁定 Gate の実在を PASS の要件にしないと、
   裁定を経ていない SKIP を正典化できた。

3 について 1 点、指摘の字義から離れた判断をした。指摘は「every passing result で
G15 を要求せよ」だが、**値まで PASS を要求してはならない**。§21 R10-G15 の条件が
不成立なら `PHASE_B_ENTRY=SKIP` となり、§22.1 はそれでも Protocol PASS を認めて
いる。値の PASS を要求すると SKIP が原理的に記録できなくなるため、要求するのは
**裁定結果の実在**であって PASS ではない、とした。

### 第 10 巡（P1×2、全件採用）— **レビュー上限に到達・ここで打ち切り**

1. **replay の値が結論を否定できた** — 欄の実在だけを見ていたため
   `replay: {same_process: FAIL, cross_process: FAIL}` を添えたまま比較地図の
   成立を記録できた。§21 R10-G14 は Phase A PASS の要件として same-process /
   cross-process 双方の再現を求めている。値まで固定する欄を
   `_EVIDENCE_FIELD_REQUIRED_VALUES` に閉世界で列挙した。
2. **R10-G15 の値が裁定を表していなくても通った** — 第 9 巡で実在だけを要求した
   結果、`R10-G15: FABRICATED` や null が通っていた。§20.4 の Entry 状態と
   Gate 台帳の値を一対一で束縛する（ENTER→PASS / SKIP→SKIP / BLOCKED→BLOCKED）。
   あわせて `protocol_verdict: PASS` で `phase_b_entry: NOT_REACHED`（裁定が
   行われていない状態）も拒否する。

## レビュー打ち切り（CLAUDE.md 上限 10 巡）

bot レビューは 10 巡・計 33 件で上限に達した。**採用 32 / 見送り 1**。
見送りは第 8 巡の generator TOCTOU のみで、脅威モデル境界として
`af01_freeze_verifier.py` docstring に明文化し、当該スレッドは resolve せず残置した。

置いた境界宣言は 5 件:

| 領域 | 境界 | 再入条件 |
|---|---|---|
| evidence 検証の深さ | 設計が節ごとに明示する固定欄まで。欄の内側の値域・単位・数値妥当性は検証しない | §11 measurement family と §14.6 の実装 |
| outcome 組み合わせ | 設計本文から一意に矛盾と読める対のみ拒否 | 許容組み合わせの束を設計が定義したとき |
| 整合検証の範囲 | outcome と Run 全体の状態量まで。trait 単位の値と outcome の整合は検証しない | §14.6 判定則の数値 freeze |
| 脅威モデル | 受動的ドリフトの tamper-evidence。同一ユーザ権限の能動的攻撃者は境界外 | multi-tenant / 共有ユーザ環境での実行 |
| replay 除外集合 | generator ソース 1 件のみ。それ以外の除外は実測の裏付けと明示登録を要する | bundle 実測で非出力 payload が判明したとき |

### 第 11 巡（P1×1 / P2×1）— 上限超過だが 3 分類該当のため採用

CLAUDE.md は「打ち切りは 3 分類を上書きしない（新しい具体経路を示す指摘は巡数に
依らず採用）」と定めており、2 件とも新規の具体経路であるため採用した。

1. **PASS が BLOCKED な Entry 裁定の上に立てた** — §22.1 が PASS との両立を明示
   しているのは `SKIP` までで、`BLOCKED`（裁定そのものが未解決）ではない。
   第 10 巡で導入した ENTER/SKIP/BLOCKED 写像が、この経路を新たに到達可能に
   していた（自分の修正が作った穴）。PASS と両立する Entry 状態を
   `_ENTRY_STATES_COMPATIBLE_WITH_PASS = ("ENTER", "SKIP")` で閉世界に固定した。
2. **`--json-out` が非 atomic** — 既存の検証レポートを in-place truncate しており、
   中断・容量不足で前の有効な証拠を壊して部分 JSON を残した。inventory 側と同じく
   `svp_rpe.utils.atomic_io` へ委譲した（第 4 巡 P2 と同型の残り 1 箇所）。

打ち切り宣言そのものは維持する。以降も、3 分類（実コード被害 / 将来汚染 /
致命的バグ）に該当する新規経路のみ対応し、それ以外は境界宣言で止める。

### 第 12 巡（P1×2 / P2×1）— 3 分類該当のため採用

1. **rights 節が閉世界形状でなかった** — 必須 2 欄しか見ていなかったため、
   `private_only: true` と同時に `public_audio_release: true` を宣言した正典結果が
   通った。R10-PUB-1 で User が裁定した境界そのものを結果文書側で骨抜きにできる。
   禁止側 4 欄を含む閉世界形状にし、未知欄も拒否する。
2. **overfit 信号が非 bool でも通った** — 整合規則 3/4/5 はすべて `signal is True`
   で判定するため、`measurement_overfit_signal: 1` はどの規則にも掛からず、
   信号を立てたまま成立側 outcome と `R10-G7: PASS` を記録できた。真偽値を要求する。
3. **A0 presence が `character.txt` を要求していなかった** — §7.1 は
   character.txt SHA256 を必須 pin に挙げており、未取得メッセージにも
   rights_manifest にも同じ 3 点が書いてあるのに、presence 判定は WAV と oto.ini
   だけを見ていた。不完全な voicebank のまま R10-G2 が COMPLETE になり得た。

### 第 13 巡（P1×2）— 「開いたキー集合」ファミリーの終端

第 12 巡（rights 節）と同型が 2 巡続いたため、検証で扱う mapping の開口部を
一括で閉じてファミリーを終端した。

1. **`staged_intervention` の入れ子が開いていた** — 外側のキー集合だけ閉じて
   内側を `get()` で拾っていたため、`phase_a.activation: INTERVENTIONAL` や
   `phase_a.identity_copy: ALLOWED` を足した契約が通り、非介入・Identity 除外の
   不変条件と矛盾したまま R10-G0 が PASS になり得た。
2. **results の top-level キー集合が開いていた** — `publication_scope: PUBLIC` や
   `public_dataset_release: true` のような対立する機械可読宣言を、必須の
   private 宣言と同居させたまま正典結果にできた。§27 が列挙する 22 欄の
   閉世界 allowlist にした。

`test_no_validated_mapping_is_left_open` が閉世界表の存在をテストで固定する。

### 第 14 巡（P1×3）

1. **SKIP なのに Phase B gate が全 PASS** — Gate 台帳が「Phase B へ入らなかった」と
   「その実行 Gate は全部 PASS」を同時に主張できた。§21 は R10-G16..G22 を
   ENTER 時のみ必須と規定しており、入らなかった Run で走っていない Gate を
   合格にしてはならない（規則 2b）。
2. **`FREEZE_REGISTRATION.json` の全欄照合が抜けていた** — aggregate hash だけ
   一致していれば通る状態だったため、`canonical_body.pitch: G4` と C4 の
   aggregate hash を同時に宣言した自己矛盾登録が R10-G2 を通過した。
   top-level と `canonical_body` の両方を閉世界にした。
3. **`phase_b_eligible` が非 bool の truthy 値を受理** — `is True` だけを見ていた
   ため `1` や `"yes"` が素通りした。第 12 巡の overfit 信号と同型。

**第 13 巡の「全数掃討」宣言の訂正**: 2 は第 13 巡で閉じたと述べた範囲の
取りこぼしである。あのとき閉じたのは契約ローダと results 検証の mapping で、
`_check_freeze_registration()` は「特定の欄を照合する」実装のままだった。
「全数掃討」は契約・results 検証に限った話であり、登録ファイル検証は含まれて
いなかった。3 についても、真偽値欄の型検査を `_require_boolean_field()` へ
一本化し、第 12 巡の対象（overfit 信号）も同ヘルパへ寄せた。
