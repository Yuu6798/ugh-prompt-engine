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
| R10-G2 `PRE_RUN_INVENTORY_COMPLETE` | **BLOCKED** | 設計文書の実体未照合 / A0 未取得 / AF01 bundle 実体未取得 / meter 未実装 |
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
│   ├── build_a0_manifest.py           # §29 手順 4（private staging 専用）
│   ├── build_pre_run_inventory.py    # §29 手順 3/5
│   └── inventory.json                # R10-G2 の機械可読状態
├── results/                      # §26 private bundle（.gitignore 以外を commit しない）
└── tests/                        # §28 最低テストの静的検証可能サブセット
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
| 4 | A0 voicebank の inventory と hash | **生成器実装済み**／実体照合は private staging で実行 |
| 5 | Evolution Theory 参照の解決 | **完了**（2026-08-27 実バイト照合 → 契約 pin 済み。本体は private のまま） |
| 6 | AF01 payload ledger 等の検証 | **台帳段階まで完了**／実体照合は bundle 待ち |
| 7 | AF01 決定論的 payload replay | bundle 実体待ち |
| 8 | AF01 V1 生成 | transport 経路の選定が未裁定 |
| 9 | E0 の truth / code independence 検証 | bundle 実体待ち |
| 10–11 | neutral carrier manifest と Performance 不在検証 | resampler / wavtool 選定待ち |
| 12–16 | 内部校正 → E0 外部校正 → 数値判定則 freeze | §11 measurement family の実装が前提 |

機械側だけで進められる次の単位は **§11 measurement family（M0–M6）の実装**
と **§12 内部校正 fixture の生成**である。ただし E0 外部校正（手順 14）は
AF01 bundle 実体を要する。

## A0 voicebank manifest（private staging 専用）

`pre_run/build_a0_manifest.py` は A0 の root 相対ファイル順、全ファイルの
SHA-256、WAV の PCM ヘッダ値を決定論的 JSON にまとめる。出力には private な
ファイル名と per-file hash が含まれるため、Git リポジトリ内への書き出しを拒否し、
明示した private staging root の内側にのみ原子的に書く。

```text
python pre_run/build_a0_manifest.py \
  --voicebank-root <private>/A0/_Default \
  --staging-root <private> \
  --zip-path <private>/A0/RUN10_A0_UTAU_Default.zip \
  --zip-sha256 <verified-zip-sha256> \
  --voicebank-version "UTAUデフォルト音声 Ver1.2" \
  --out <private>/A0/a0_voicebank_manifest.json
```

ZIP の path/hash は同時指定であり、不一致時は manifest を公開しない。
`--obtained-at` を明示しない限り実行時刻を含めないため、同一入力の出力は
バイト一致する。manifest 本体・A0 実体・ファイル名一覧は commit しない。

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

### 第 15 巡（P1×2）

1. **Gate 台帳そのものが閉世界でなかった** — 第 14 巡の規則 2b は
   「走っていない Phase B Gate の `PASS`」だけを拒否したため、`R10-G16: FAIL` や
   `R10-G16: FABRICATED` は素通りし、`R10-G99` のような未知の Gate ID も通った。
   走っていない Gate で不合格を名乗るのは「実施したが落ちた」の偽装であり、
   合格の偽装と同じ重さで §21 の台帳を汚す。`_validate_hard_gates_shape()` で
   **キー集合（R10-G0..G22）・判定値語彙・未実行状態**の三つを同時に閉じた。
   ENTER 時は Gate の値を拘束しない（走った Phase では FAIL も正当な観測）。
2. **公開 allowlist に投機的な事前登録があった** — `pre_run/aquest_pitch_inventory.json`
   を「将来作る予定」で登録していたが、§9.4 の収録ピッチ inventory は
   raw-unit F0 推定を含む**測定成果物**であり §2.2 が非公開と定めるカテゴリだった。
   中身を見ないまま公開を事前承認した状態で、作られた瞬間に CI が素通しする。
   allowlist を**実在ファイルだけ**に切り詰め、`test_allowlist_has_no_speculative_entries`
   が「実在してから 1 行足す」規律を機械強制する。

1 は第 13/14 巡の「開いたキー集合」ファミリーの再発である。前 2 巡で閉じたのは
検証対象 mapping のキー集合であって、**値の語彙**ではなかった。今回は台帳の
キー・値・状態の三面を同時に閉じたので、Gate 台帳としての開口部は残っていない。

### 第 16 巡（P1×1）

**Evolution Theory 正典を「近縁文書の発見」で解決済みにしていた** — §29 手順 5 が
要求するのは §36 が実在を確認した v0.3 本体 `VoiceGenesis_Evolution_Theory_v0.3_ja.md`
だが、判定は `VISION_evolution_theory_v0.3.md`（別名の別文書）の実在で行っていた。
後者が追加された瞬間に `evolution_theory_reference` が PRESENT になり、しかも同じ
detail が「v0.3 本体はリポジトリ内に不在」と言い続ける自己矛盾を出したまま、
他の blocker が解けたら R10-G2 が COMPLETE になり得た（来歴汚染）。

判定を**正典パス + 凍結 sha256 の一致**のみに変えた。近縁文書の発見リストは
報告用の情報として残すが判定材料ではない。凍結 sha256
（`vg_evolution_theory_ref_sha`）は未取得なので、pin を得るまでは正典が置かれても
解決にしない — 「正しい実体が在る」ことを証明できないからである。名前一致だけで
通すと同名の別内容で来歴が汚染される。

**追補（User 裁定 2026-08-27）**: v0.3 本体はリポジトリに載せない。したがって
照合対象は repo 内の固定パスではなく、実行時に `--evolution-theory-path` で渡す
private storage 側の実体である（DESIGN_RUN10 本体に対する `verify_design_document()`
と同じパターン）。**渡されたパス文字列は `inventory.json` に記録しない** —
inventory は commit されるため、private ストレージの構成をそのまま公開することに
なる（§2.2 / §26）。この裁定は AQUEST 由来資産（UTAU デフォルト音源は個人・非公開
でのみ分析／抽出／合成が許諾される）に対する姿勢を、VG 側の設計文書へも保守的に
適用したものである。

### 第 17 巡（P1×2）

1. **inventory 側の台帳が二度読みだった** — `inventory_af01()` は
   `verify_ledger_bytes()` で認証した後、parse のために `PINNED_LEDGER_PATH` を
   読み直していた。二読の間に差し替えられると、`af01_payload_sha256sums` は元
   バイトを「認証済み」と書きながら `af01_ledger_structure` は別バイトから
   導かれる、内部矛盾した正典 inventory ができる。第 4 巡で導入済みの単一読み
   ヘルパ `read_and_verify_ledger()` へ寄せた（同じバッファを hash して parse）。
2. **走らなかった Gate を「省略」で消せた** — 第 15 巡は「未実行は `NOT_REACHED`
   と書く」と定めたが、キー集合を**上からしか**閉じていなかったため、その Gate を
   台帳に書かなければ同じ結果になった。省略と NOT_REACHED は別物である —
   前者は「何も言っていない」であって、走らなかったことの記録ではない。
   正典 PASS の台帳に R10-G0..G22 の**全数**を要求する
   （`_require_complete_gate_ledger`）。BLOCKED / FAILED は途中で止まった Run
   なので全数を要求しない。

2 は第 15 巡の同型で、閉世界の**向き**の取りこぼしだった。上（未知キーの排除）は
閉じていたが下（必須キーの充足）が開いていた。

### 第 18 巡（P1×1）

**Evolution Theory の pin を二重管理していた** — 照合値をモジュール定数
`EVOLUTION_THEORY_CANONICAL_SHA256` に持ちながら、契約 `RUN10_CONTRACT.yaml` の
`vg_evolution_theory_ref_sha` にも同じ digest を書く設計だった。両者が乖離すると、
R10-G0 は契約側の digest を検証しながら R10-G2 は別バイトを PRESENT と書く —
矛盾した来歴のまま Run が進む。

**定数を廃止し、pin の出所を契約 1 箇所に閉じた**。`_evolution_theory_pin()` が
契約をロードして PINNED 値だけを返し、`PENDING` / 契約が読めない / 壊れている
場合はいずれも None（fail-closed）で R10-G2 を塞ぐ。乖離の検出ではなく、
乖離し得る状態そのものを無くす方向で終端した。
`test_module_has_no_independent_digest_constant` が再発を機械強制する。

## §29 手順 5 完了（2026-08-27）

Evolution Theory v0.3 参照が解決した。User が Drive 上の
`VoiceGenesis_Evolution_Theory_v0.3_ja.md` を raw ファイルとして取得し実バイトを
照合。複製 2 件（file ID `1b-RQJl9C8RA7uUiAxcTAmTG53Zs5rHIb` /
`1MC1FPipyyseaXRil6igZOxsqggsyBNVL`）は 25,119 bytes・同一 sha256 で IDENTICAL。

```
vg_evolution_theory_ref_sha = 87f4208ffdd213099977c4b5a1ee5d06852524036c818e4b14ce6b0e355b2e93
```

`RUN10_CONTRACT.yaml` の当該 pin に PINNED として記録した（**digest の出所はここ
1 箇所**。第 18 巡の通り、モジュール定数は置かない）。これにより R10-G0 の CORE
pin 未充足は 37 → 36 件になった。

R10-G2 が要求するのは §29 手順 5 の「Evolution Theory v0.3 **location**」=
参照の同定であり、契約 pin の成立をもって `evolution_theory_reference` は
PRESENT / 非 blocking になる。本体はリポジトリに載せない（User 裁定 2026-08-27）
ため、実体バイトの照合は `--evolution-theory-path` を渡したときの**追加検査**と
した — 必須にすると本体を commit しない限り永久に解決不能になるからである。
渡されたのに一致しない場合は同名の別内容 = 来歴汚染として UNRESOLVED へ落とす。

### 第 19 巡（P1×2）

1. **compatibility_matrix の行が開いていた** — 選んだキーだけ読んで残りを素通し
   していたため、行に `identity_copy: ALLOWED` のような機械可読な対立宣言を
   足せた。top-level は `identity_copy: PROHIBITED` を宣言しているのに trait 単位
   では許可した正典結果になる。`COMPATIBILITY_ENTRY_ALLOWED_FIELDS` で行を
   閉世界にした。第 13 巡（staged_intervention / results top-level）と同型で、
   **行レベルだけが残っていた**。
2. **G15 と Entry の束縛が PASS 経路にしか掛かっていなかった** — 第 10/11 巡の
   一対一束縛は `verdict == "PASS"` の分岐の中だったため、
   `FAILED` + `phase_b_entry: ENTER` + `R10-G15: SKIP` や
   `BLOCKED` + entry `BLOCKED` + `R10-G15: PASS` が通った。失敗した Run の台帳でも、
   Entry 裁定と Gate の値が食い違った記録を正典にしてはならない。
   `_validate_entry_ledger_consistency()` を verdict 分岐の**前**で無条件に呼び、
   (a) 記録された G15 は Entry 状態と一対一、(b) `ENTER` を名乗るなら裁定 Gate が
   台帳に在る、の 2 つを全 verdict へ効かせた。`NOT_REACHED` も対応表に加えて
   第 15 巡の未実行語彙と揃えた。

### 第 20 巡（P1×1）— 「開いたキー集合」ファミリーの全数終端

指摘は `generative_compatibility_matrix` の行が開いていること（`identity_copy:
ALLOWED` / `scope: PUBLIC` を足せる）。第 19 巡で compatibility 行を閉じた直後の
同型で、**このファミリーは第 12/13/15/17/19/20 巡と 6 度再発した**。個別に塞ぐ
のをやめ、全数棚卸しで終端する。

指摘の 1 件に加えて、同じ掃討で以下も閉じた:

- `generative_compatibility_matrix` の行（`GENERATIVE_ENTRY_ALLOWED_FIELDS`）
- `synthesis_validation`（固定 3 欄）とその `controls`（§7.5 の 3 対照のみ）
- キーが設計の固定語彙である evidence 節 — `external_calibration` /
  `decision_rules` / `path_effects` / `replay` / `synthesis_validation`

**分類の導入**が終端の本体である。検証で降りる mapping を 2 種に分ける:

| 種別 | 意味 | 扱い |
|---|---|---|
| `SHAPE` | キーが設計の固定語彙 | 未知キーを拒否する |
| `INDEX` | キーが trait id / case id などのデータ識別子 | 閉じられない。**行**を SHAPE として閉じる |

`INDEX` を閉じると測定結果そのものが記録できなくなるため、閉世界化は
「全部閉じる」ではなく「どちらの種別かを宣言し、SHAPE だけ閉じる」が正しい形である。
`MAPPING_CLOSURE_INVENTORY` が全数を登録し、
`test_every_validated_mapping_is_registered` が `run10_schema.py` の
`_require_mapping()` 呼び出しを走査して**未登録の mapping を追加できなくする**。
新しい mapping を検証対象にしたら、SHAPE / INDEX で分類して登録しない限り
テストが落ちる。

### 第 21 巡（P2×1）

**現在地表が機械状態と食い違っていた** — 「次の実装単位」表の §29 手順 5 の行が
「v0.3 本体がリポジトリ内に不在」を律速として掲げたままだった。本体を置かないのは
User 裁定による**意図的な不在**であり、参照は契約 pin で既に解決している
（`inventory.json` も `evolution_theory_reference: PRESENT`）。この表は「次に何へ
着手できるか」を選ぶために読まれるため、済んだ前提条件を律速だと誤読させる。

行を「完了（実バイト照合 → 契約 pin 済み。本体は private のまま）」に直し、
`test_readme_step5_agrees_with_the_contract_pin` と
`test_readme_step5_matches_the_inventory_state` で**表と機械状態の同期をテストで
固定**した。どちらへ動かしても片方だけ古いままにはできない。

### 第 22 巡（P1×2）

1. **設計文書の実体照合がどの検収経路にも繋がっていなかった** —
   `verify_design_document()` はテストからしか呼ばれておらず、契約ローダは
   YAML の宣言と定数の digest を突き合わせるだけだった。同じ digest を 2 箇所で
   照合しても、**その digest の文書が実在すること**は何も証明しない。Core pin が
   埋まれば「どの設計を実行したか」が未証明のまま R10-G0 が PASS し得た。

   R10-G0 は §21 の定義どおり「Run Contract の pin 充足」のままにし、実体照合は
   **R10-G2 の実在確認に blocking 項目 `design_document_bytes` として追加**した
   （`--design-doc-path`）。どちらの Gate も開かない限り測定は始まらないので、
   設計が未証明のまま Run が進む経路は残らない。照合対象のパス文字列は
   inventory に記録しない（§2.2 / §26 — inventory は commit される）。
2. **裁定済み Entry が R10-G15 の記録を消せた** — 第 19 巡は `ENTER` だけを
   縛ったため、`SKIP` + `hard_gates` 欠落が通り、「一度だけ行った裁定」を
   主張しながらその Gate 記録を消せた。§29 手順 35 の裁定は ENTER / SKIP /
   BLOCKED のいずれでも「行われた」ことを意味するので、記録を省けるのは
   Entry へ到達しなかった `NOT_REACHED` だけである
   （`_ENTRY_STATES_REQUIRING_ADJUDICATION`）。

### 第 23 巡（P1×2）

1. **空コンテナを「evidence が在る」と数えていた** — 成功側 outcome での
   per-trait 必須欄を `is None` だけで見ていたため、`support: []` /
   `calibration: ""` / `holdout: {}` が通り、evidence が空のまま比較地図の
   成立を主張できた。第 5 巡で導入済みの `_is_absent_evidence()`（0 や false は
   在るものとして扱い、空コンテナだけを不在とする）をここにも適用した。
   第 22 巡の `verify_design_document()` と同型で、**ヘルパは在るのに
   適用されていない**箇所だった。
2. **生成行が自己矛盾を載せられた** — 行の `phase_b_entry` を enum とだけ
   照合していたため、`synthesis_status: GENERATIVELY_COMPATIBLE` と
   `phase_b_entry: SKIP` を同じ行が同時に主張できた（「この形質は Phase B を
   回していない」と「生成互換が成立した」）。生成側の成立は Phase B を実際に
   回した行にしか起こり得ないので、`GENERATIVE_SUCCESS_STATUS` の行は
   `ENTER` を要求する。不成立側の行は回していない状態を記録できる。

## フォローアップ（PR #330 マージ後）

### 「宣言されたが適用されていない検証器」ファミリーの終端

PR #330 のレビューで同型が 3 度出た:

| 巡 | 宣言したもの | 適用されていなかった場所 |
|---|---|---|
| 4 | `GENERATIVE_STATUS` | 一度も照合に使われていなかった |
| 22 | `verify_design_document()` | どの検収経路にも繋がっていなかった |
| 23 | `_is_absent_evidence()` | compatibility 行へ適用漏れ |

いずれも「検査の語彙・関数を書いたが、実行経路から参照されていない」ことが
原因である。第 20 巡の「開いたキー集合」と同じく、個別に塞ぐのではなく
**未配線のまま追加できなくする**ことで終端した。

`tests/test_enforcement_wiring.py` が静的解析（`ast`）で次を検査する:

1. 走査対象モジュールの直下 ALL_CAPS 定数は、**到達可能な**関数本体から
   load されるか、load される別の定数へモジュール階層で取り込まれていること
   （推移閉包 — `CORE_PIN_FIELDS` のように `_STAGE_FIELDS` 経由で効くものを
   拾うため）
2. 公開検証器（`assert_*` / `verify_*`）は、**到達可能な**関数本体から
   呼ばれていること

走査範囲と到達可能性は監査の要である（PR #332 Codex 第 1 巡 P2×2）:

- **走査範囲**はハードコードせず、RUN10 ツリーの非テスト `.py` を全数発見する。
  一覧を固定すると、§24 が予定する `measurement/` `calibration/` `evaluation/`
  が追加されたとき、そこの未配線検証器が監査の視界に入らないまま全テストが通る。
- **到達可能性**は公開関数を起点に呼び出しグラフを辿って判定する。全 `def` を
  一律「実行経路」と数えると、どこからも呼ばれない private ヘルパで新しい検査
  定数に触れるだけで「配線済み」になり、本テストが防ごうとしている当の退行が
  素通りする。

例外は `UNWIRED_REGISTRY` に理由付きで登録する。登録簿に無い未配線が現れたら
落ち、配線済みになったのに登録簿へ残っている項目でも落ちる（例外の陳腐化防止）。
現在の登録は 5 件で、うち 2 件は `PENDING_APPLICATION:`（配線先の producer が
まだ実装されていない予約ガード）である。

走査範囲を全モジュールへ広げた結果、`EVOLUTION_THEORY_CANDIDATES`（第 16 巡で
置いた後方互換の別名）が実際に未配線として検出された。呼び出し側が 1 つも無い
死んだ別名だったため削除した。

**主張の範囲（境界宣言）**: 本監査は**必要条件**であって十分条件ではない。
捕まえるのは「宣言された名前がどの実行経路からも参照されていない」ことだけで、
到達可能性は AST の直接呼び出しを辿る近似である。捕捉しない経路は
`AUDIT_LIMITATIONS` に再入条件付きで列挙してある:

| 限界 | 再入条件 |
|---|---|
| 呼び出し元の無い公開関数を起点に数える | CLI / 公開 API の実エントリポイント一覧を宣言する方式へ切り替えるとき |
| private 述語の「ある分岐では使い別の分岐では使わない」を検出しない | private 述語ごとに必須適用箇所を宣言する表を作るとき |
| 同名関数をモジュールで区別しない | モジュール / クラスで修飾した解決を実装するとき |

したがって**「宣言された検証器が正しい分岐で使われているか」は本監査の範囲外**
であり、そちらは各検証器の個別テストが担う。本監査の主張は「未配線の宣言を
無言で増やせない」ことに限る。属性呼び出し（`obj.verify_x()`）は配線と数えない
ため、判定が緩む側には倒れない。

### stage 語彙の単一化

上の監査が `CONTRACT_STAGES` を未配線として検出した。宣言された stage 語彙は
`CONTRACT_STAGES`、実際に検証へ効いていたのは `_STAGE_FIELDS` のキーで、
別々に書かれていた（第 18 巡の pin digest 二重管理と同型）。`CONTRACT_STAGES`
を `_STAGE_FIELDS` から導出し、`missing()` の照合も公開語彙側で行うようにして、
「宣言した語彙」と「検証される語彙」を同じ 1 つにした。
