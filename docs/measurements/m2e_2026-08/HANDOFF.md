# M2e 引き継ぎ（2026-08-01 時点）

**この文書は次のセッション／実行者が最初に読むもの。** 会話にしか無い知見を repo へ
移すために書いている。設計の正本は
[`docs/DESIGN_M2e_vremix_real_bed.md`](../../DESIGN_M2e_vremix_real_bed.md)（**rev.6**）、
手順は [`docs/m2e_provisioning_runbook.md`](../../m2e_provisioning_runbook.md)。

> **M2e の帯セル実測は 0 件である。** 1280 セルのうち完了は **0**。
> 素材選定（r2）と事前登録（r3）は完了しているが、測定は 1 セルも走っていない。

---

## 1. 段階の状態

| 段階 | 内容 | 状態 |
|---|---|---|
| r0 (P-a) | 設計正本 + runbook + 決裁/撤回記録 | **完了** |
| r1 (P-b) | ハーネス配線・条件 block 検証・`make_vremix_fixtures.py` | **完了** |
| — (P-b′) | **F2** セル単位チェックポイント（設計 §8.7） | **完了** |
| — (P-b″) | **C4** `env_digest` に CPU 同一性（rev.6 §8.9.3） | **完了** |
| r2 | 全 50 曲スクリーニング → 採用 2 件 | **完了**（[`r2_screening.md`](r2_screening.md)） |
| r3 (P-c) | `m2e_bed_fixtures.yaml` / `m2e_accuracy_bars.yaml` | **完了** |
| — | **C2** store 分離（rev.6 §8.9.2-(1)） | **未着手** ← 次はここ |
| — | **C3** evaluate の並列化（rev.6 §8.9.2-(2)） | **未着手** |
| r4 | r2-0（`P` / 並列不変性ゲート / `S`・`T_*` / `env_digest` / lockfile） | 未実施 |
| r5 (P-c′) | `m2e_r2_shard_map.yaml` | 未実施 |
| r6 (P-d) | 本測定（**code change 厳禁**） | 未実施 |
| r7 | 破断曲線 + stem アーム 4 点（**昇格宣言をしない**） | 未実施 |

**C2 / C3 は r6 開始前に landing させること**（r6 は code change を禁じているため、
r6 に入ってからでは入れられない。F2 と同じ論法・rev.6 §8.9.4）。

---

## 2. 残タスクの仕様

### C2 — store 分離（rev.6 §8.9.2-(1)）

```
run フェーズ      → store_A に書く / store_A のみ読む
evaluate（検証）  → store_B に書く / store_B のみ読む   ← run の結果は一切読まない
publish 条件      → store_A と store_B を突き合わせる（両方とも完全な独立計算）
```

現状: F2 が実装した `--cell-store` は run 用（= `store_A`）。
`_run_external_verification_in_fresh_process` は子へ `--cell-store` を**渡していない**
（そのため evaluate は再開不能）。

やること: evaluate 用の `store_B` を受け取る経路を足し、検証の子へ `store_B` を渡す。
**`store_A` を渡す経路は作らない**（現在の禁止は `store_A` に対してのみ維持する）。
再開可否は `env_digest` 一致で fail-closed（§8.7 の再開規則をそのまま適用）。

fail-closed 要件:

- `store_A` と `store_B` が同一パス、または一方が他方の配下にある場合は拒否する。
- 検証の子のコマンドラインに `store_A` が現れないことをテストで固定する
  （F2 の `test_reverification_child_never_receives_cell_store` を、
  「`store_A` は渡らない／`store_B` は渡る」へ発展させる）。

> **独立性は「store を分ける」ことで保たれるのであって、「再開できない」ことで
> 保たれるのではない。** ここを取り違えると、F2 で塞いだ穴（publish がキャッシュを
> 自分自身と比較する）を別の形で開けることになる。

### C3 — evaluate の並列化（rev.6 §8.9.2-(2)）

現状の evaluate は実効 `P = 1`（10.0 h ÷ 160 セル = 225 s/セルが単セル実測 254.2 s と
一致する）。`P = 4` で **10.0 h → 約 2.9 h**。`B_session` も実行環境も変更しない。

**publish が要求するのは「fresh process であること」と「run の結果を読まないこと」で
あって、逐次であることではない。** run を `P = 4` で回しながら検証だけ `P = 1` を
要求するのは、§8.3 の並列不変性ゲートがまさにその同一性を検査している以上、一貫しない。

実装上の注意:

- `_reverify_external_category_measurement` の `for index in range(repeats)` は
  逐次に子を起こしている。ここと、`_run_external_category` の manifest ループの
  両方が並列化の対象になりうる。
- **どちらを並列化しても、セルごとの結果が `P` に依存してはならない。** 各ワーカーで
  `OMP_NUM_THREADS=1` / `MKL_NUM_THREADS=1` / `torch.set_num_threads(1)` を固定する
  （下記 §3 の実測を参照。3 点目を欠くと stem の bytes が run 間で変わる）。
- §8.3 の並列不変性ゲート（`P=1` と `P=決定値` で**ピッチ軌跡の sha256 が完全一致**）を
  テストで固定すること。**精度値の一致では不十分**。

---

## 3. 実測で分かった落とし穴（これを知らないと必ず踏む）

### 3.1 `torch.set_num_threads(1)` を欠くと stem が bit 一致しない

`OMP_NUM_THREADS=1` / `MKL_NUM_THREADS=1` だけでは足りない。3 点目を設定しないと
demucs の vocals stem の `stem_sha256` が run 間で変わる（`residual_db` は 1e-6 dB で
安定するのに bytes は変わる）。ハーネスは `stem_sha256` を model stack 署名に含めるため、
**stem アームの repeats が「別 model stack」として fail-closed になる**。

実証: `raw/thread_test.json`（`torch.set_num_threads(1)` で 2 回とも
`717fcd2c…09` に一致）。詳細は `r2_screening.md` §4.6.4。

### 3.2 単位コストはコンテナ実体に固有（2.2×）

同一セル（bed_20）の壁時計が別インスタンス間で **254.2 s → 552.4 s**。原因は CPU の
入れ替わり（`Xeon @ 2.80GHz` → `Xeon @ 2.10GHz`・AVX-512 あり）。

- **`T_*` をセッション跨ぎで再利用しないこと**（rev.6 §8.4）。各セッション開始時に
  2 波を回して測り直し、未完セルについて §8.5 の地図を引き直す。
- 「run は 7 シャードで収まる」という私の投影も**この実体に固有の暫定値**である。
  22 h でありうるし、その場合 `N_shards ≒ 15 > R_max = 12` になる。確定値として
  扱わないこと。
- **理想スケーリング（`総時間 / P`）の外挿値を計画値として提出しない。** `P` の効果は
  §8.3 の飽和判定とは別に実測比を取って示す。

### 3.3 evaluate は測定をもう一度やる（台帳の 2 倍）

`_reverify_external_category_measurement` が `repeats_min`(=2) 回フルカテゴリを
測り直す。総セル実測回数 = 1280 × 2 = **2560**。これは publish の独立性の代償として
**設計どおり**であり、削る方向の提案は不要（裁定済み）。

### 3.4 長時間待機を伴う下請けは commit まで到達したか外形で確認する

実装を委譲した下請けがポーリングループから抜けられず、**未コミットの変更を残したまま
「完了」通知だけ返した**（2 回）。報告文を完了の証拠と見なさない。
`git log --oneline -1` と `git status --short` の 2 つで足りる。runbook §0'.1 に記載済み。

### 3.4' 凍結定義と実装の照合は「読み合わせ」でしか捕まらない

(d) の算出器は、**凍結定義が明示的に棄却した基準量（peak）を分母に使っていた**
（Codex 4 巡目・`r2_screening.md` §4.8）。テストは通り、数値は出て、監査表まで
書けていた——**内部整合しているので下流からは見えない**。閾値を触っていないから
一方向規律にも触れない。捕まえる手段は、凍結文と実装を 1 行ずつ突き合わせること
だけである。M2e の残りの凍結量（`residual_db` / `n_max` / 水準式 / `factor`）にも
同じ照合を一度かけること。

### 3.4'' 測定値を訂正したら **5 箇所**を同じ回で掃く

(d) の訂正で **3 巡続けて掃き残しを指摘された**（登録 pin → README の digest →
記録の散文）。同じ量が 5 箇所にあるためで、注意力ではなく手順で閉じる。訂正が
発生したら以下を 1 つのチェックリストとして回すこと:

| # | 場所 | 例 | 機械検証 |
|---|---|---|---|
| 1 | 生データ | `raw/reason_d_26_corrected.json` | — |
| 2 | 登録 pin | `tests/fixtures/melody_bench/m2e_bed_fixtures.yaml` | **あり**（生データと一致） |
| 3 | 記録の**表** | `r2_screening.md` §4 の 50 行 | — |
| 4 | 記録の**散文** | §4.1 の目視理由・生存候補の順位・件数 | **無理**（人が読む） |
| 5 | README | 採用 2 件の値・pin 表の digest | **あり**（登録 pin と一致） |

**4 が最も残る。** 表を書き換えても散文の「N_drop = 238 だから飛ばした」「3 番目の
生存候補」「実施 5 件 / 未実施 45 件」は自動では動かない。訂正後は §4.1 を通しで
読み直すこと。

### 3.5 プロビジョニングの 2 つの罠

- **demucs**: `get_model("htdemucs_ft")` は HF Hub から safetensors を取るが、
  リポジトリのゲートは torch hub の `.th` を探す。canonical な `.th` を明示取得する
  （runbook §2.2）。
- **crepe**: setuptools 81 以降が `pkg_resources` を削除したため導入に失敗する。
  `pip install "setuptools<81"` を先に実行（runbook §1）。

---

## 4. 確定している値（再測定不要）

| 項目 | 値 |
|---|---|
| 採用ベッド 1 | **Angels In Amplifiers - I'm Alright**（`residual_db` −48.288419 / `N_drop` 36） |
| 採用ベッド 2 | **Arise - Run Run Run**（−52.255730 / `N_drop` 48） |
| `n_max` | **1,708,258 samples = 38.736009 s** @44100（40 clip 基準で固定） |
| vocadito | 40 clip 全部が `m2c_external_fixtures.yaml` の pin と一致（mismatch 0） |
| ラダー | 4 点（+12 / +6 / 0 / −6 dB）。**縮退なし**（§3.6.1 は発動せず） |
| `weights_sha256`（demucs） | `bf1218da42cb354bb995fb41b0a1dc8fa3cd47d63ccdaefec12dad03f8377b86` |

**未実測のまま残っているもの**（宣言された穴）:

- `T_direct`（crepe 側）— 律速ではないが未実測。r2-0 で測る
- `S`（起動〜モデルロード）— 仮置き 90 s。htdemucs_ft は 4 モデルのバッグなので
  **過小の疑いが強い**。r2-0 で実測して確定させる
- `archive_sha256_local` — `null` + dated 理由（§9.1 の宣言された穴。埋めない）

---

## 5. 次にやる順序

1. **C2 / C3 を実装**（本 PR とは別 PR）。r6 前に landing。
2. **ミックス生成**: `make_vremix_fixtures.py build` で 320 本
   （40 clip × 2 bed × 4 水準）+ manifest/fixtures/生成記録。runbook §5。
3. **r4（r2-0）**: `P` 決定・並列不変性ゲート・`S`/`T_*` 校正・`env_digest` 確定・
   lockfile commit。**3 点スレッド固定を必ず適用**。
4. **r5**: `m2e_r2_shard_map.yaml` を本測定開始前に commit。
5. **r6**: 本測定（code change 厳禁）。`N_shards > R_max = 12` なら §8.8 で User 決裁へ。
6. **r7**: 破断曲線 + stem アーム 4 点。**昇格宣言をしない。M4 G2 は解錠しない。**

報告規律（§11）: **1280 セルが揃うまで帯の判定を出さない。** 部分集合での平均 RPA・
途中の破断曲線・見通しの表明は禁止。出せるのは census のみ。
