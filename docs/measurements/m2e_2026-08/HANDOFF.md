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
| — | **C2** store 分離（rev.6 §8.9.2-(1)） | **完了**（2026-08-02・`--eval-cell-store`） |
| — | **C3** evaluate の並列化（rev.6 §8.9.2-(2)） | **完了**（2026-08-02・`--workers` + `--pin-threads`） |
| — | **C5** 水準横断 census 集計 | **完了**（2026-08-02・`--census`） |
| r4 | r2-0（`P` / 並列不変性ゲート / `S`・`T_*` / `env_digest` / lockfile） | 未実施 |
| r5 (P-c′) | `m2e_r2_shard_map.yaml` | 未実施 |
| r6 (P-d) | 本測定（**code change 厳禁**） | 未実施 |
| r7 | 破断曲線 + stem アーム 4 点（**昇格宣言をしない**） | 未実施 |

**C2 / C3 は r6 開始前に landing させること**（r6 は code change を禁じているため、
r6 に入ってからでは入れられない。F2 と同じ論法・rev.6 §8.9.4）。
**→ 2026-08-02 に landing 済み。** 設計判断 D-1（軌跡 digest）/ D-2（`--workers` の
非対称）/ D-3（スレッド固定の対称性）/ D-4・D-5（独立性 3 層）の裁定と根拠は
rev.6 §8.9.4 実装ノート。

**C5 も同日 landing した**（rev.6 §8.9.5・設計判断 E-1〜E-5）。C5 は r7 で使う集計器で
r6 の code freeze の制約下には無かったが、先に入れておくことで r6 以降に必要な
code change が無くなる。**r6 前に入れるべきコード変更はすべて完了している。**

---

## 2. 残タスクの仕様

### C2 — store 分離（rev.6 §8.9.2-(1)）— **完了（2026-08-02）**

```
run フェーズ      → store_A に書く / store_A のみ読む   （--cell-store）
evaluate（検証）  → store_B に書く / store_B のみ読む   （--eval-cell-store）
publish 条件      → store_A と store_B を突き合わせる（両方とも完全な独立計算）
```

実装: `--eval-cell-store PATH`（evaluate phase 専用）。
`_run_external_verification_in_fresh_process` は指定時に子へ
`--cell-store <store_B> --repeat-index <i>` を積む。**`store_A` を渡す経路は無い**。
再開可否は従来どおり `env_digest` 一致で fail-closed（§8.7 の再開規則をそのまま適用）。

fail-closed（すべて **resolve 後**のパスで判定・各 1 テスト）:

- `store_A == store_B`
- 一方が他方の配下（両方向）
- `--eval-cell-store` を run phase（`--evaluate` なし）で指定
- `--out` が `store_B` 配下

子のコマンドラインに `store_A` が現れないことは
`test_reverification_child_never_receives_the_run_cell_store` が
「`store_A` は渡らない／`store_B` は渡る」の両側で固定する。

> **独立性は「store を分ける」ことで保たれるのであって、「再開できない」ことで
> 保たれるのではない。** ここを取り違えると、F2 で塞いだ穴（publish がキャッシュを
> 自分自身と比較する）を別の形で開けることになる。

### C5 — 水準横断の census 集計（帯の判定を出す唯一の場所）— **完了（2026-08-02）**

`evaluate_m2_bars` は M2e カテゴリに `pass` / `fail` を出さない（設計 §6.2）。
`gate_level` でバーは当たるが結果は `bar_satisfied` / `failures` に残り `status` は
`census_pending` に留まる——**1 回の evaluate は 1 水準しか見ない**ため。

実装: `aggregate_m2e_census`（CLI `--census VERDICT.json...`）。**帯の判定が出る唯一の
場所**。4 水準 × 2 アームの verdict を集め、census 完全性を検査し、揃っているときにだけ
帯の判定を出す。

```bash
set -o pipefail  # tee が Python の非ゼロ exit（fail-closed 拒否）を隠さないようにする
python scripts/run_melody_accuracy.py \
    --out docs/measurements/m2e_2026-08/census.json \
    --census docs/measurements/m2e_2026-08/verdict_p12.json \
             docs/measurements/m2e_2026-08/verdict_p06.json \
             docs/measurements/m2e_2026-08/verdict_p00.json \
             docs/measurements/m2e_2026-08/verdict_m06.json \
    | tee docs/measurements/m2e_2026-08/census_stdout.txt
```

stdout の `census sha256:` 行が公開 bytes の pin。`*_stdout.txt` として dated record に
保存すること（保存しない実行は pin を残さない）。`pipefail` を欠くと `tee` の exit 0 が
census の fail-closed 拒否を隠す——自動化はこの行ごとコピーすること。

- 期待セル数は**積として再計算**する（80 × 4 水準 × 2 アーム × `repeats_min` = 1280）。
  `1280` を定数で書いていない（設計判断 E-2）。
- **揃わないときは metrics が文書に存在しない**（`band_verdict` / `level_response` は
  `null`、`cells` は件数のみ）。部分集合の平均 RPA・途中の破断曲線・見通しは
  「出さない」のではなく**書き込まない**（E-3・§11）。
- 全 verdict の `env_digest` 一致を要求する（E-4・§8.7）。**4 水準を別インスタンスで
  測ると CPU 差で census は揃わない**——不便ではなく §8.7 の要求そのもの。
  そのため `evaluate_m2_bars` は M2e verdict に `env_digest` を刻むようになった。
- 揃ったときの出力: `gate_level` での帯判定（アーム別 pass/fail）+ **4 水準全点**の
  `level_response`（§11「事後に一番良かった水準を選ばない」）+ `promotes_route: false` /
  `unlocks_m4_g2: false` / `declared_limits`（§7.2 の 4 点）。

**部分集合で平均 RPA や破断曲線を出さない**（§11）。集計器が無い間は判定が存在しない
状態が正しい——その状態は解消されたが、**census が揃うまでは依然として判定は出ない。**

### C3 — evaluate の並列化（rev.6 §8.9.2-(2)）— **完了（2026-08-02）**

C2/C3 以前の evaluate は実効 `P = 1`（10.0 h ÷ 160 セル = 225 s/セルが単セル実測
254.2 s と一致する）。`--workers P` が evaluate phase の**実効並列度**になった。
`B_session` も実行環境も変更していない。

> **実効値は `min(P, repeats_min)` で頭打ちになる**（PR #240 Codex P1・宣言された限界）。
> 1 カテゴリの測り直しは `repeats_min` 本の子しか起こさず、凍結 `repeats_min = 2`
> なので **`--workers 4` を渡しても実効 2**。したがって rev.6 §8.9.2-(2) が見積もった
> 「10.0 h → 約 2.9 h」ではなく、**現状で得られるのは 10.0 h → 約 5.0 h**。
> `2.9 h` へ届かせるには repeat より下の粒度（clip / シャード / カテゴリ）の並列化が
> 要り、それは測り直しの成果物の形（1 子 = 1 カテゴリ row）を変えるため**別ブリーフ**。
> verdict の `evaluate_execution.effective_workers_per_category` に実効値を刻む
> （黙って頭打ちにしない）。

**publish が要求するのは「fresh process であること」と「run の結果を読まないこと」で
あって、逐次であることではない。**

実装（設計判断は rev.6 §8.9.4 実装ノート）:

- 並列化したのは `_reverify_external_category_measurement`（検証の子）**だけ**。
  `_run_external_category` の clip ループ（run phase）は**逐次のまま据え置いた**
  ——run 側のスケーリングは r5 のシャード地図が担う設計であり、実行形態を変えると
  r4 で校正する `T_*` の意味が変わる（D-2）。したがって **`--workers` は run phase では
  宣言値 / evaluate phase では実効値**という非対称な意味を持つ。
- スレッド 3 点固定は `--pin-threads` として **run / evaluate の両方**に露出する（D-3）。
  env 2 点は起動前に設定されている必要があり（未設定は fail-closed）、3 点目は
  ハーネスが適用する。evaluate は評価対象 report が同じ固定を名乗ることを照合し、
  子へも同じ固定を伝えて子 report の申告を再照合する。
- 並列不変性ゲート（§8.3）は `est_trajectory_sha256`（M2e row 限定の新フィールド・D-1）で
  `P=1` と `P=4` の**完全一致**を固定した。**精度値の一致では代替していない。**

**r4/r6 の起動形**（3 点固定を必ず適用すること・§3.1）:

```bash
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python scripts/run_melody_accuracy.py \
    --out build/m2e/run_p12_r0.json --categories V_remix_real_direct --level +12dB \
    --external-manifest build/m2e/manifest_p12.json --external-fixtures build/m2e/fixtures_p12.yaml \
    --cell-store build/m2e/store_A --repeat-index 0 --pin-threads

OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python scripts/run_melody_accuracy.py \
    --out build/m2e/verdict_p12.json --evaluate build/m2e/run_p12_r0.json build/m2e/run_p12_r1.json \
    --external-manifest build/m2e/manifest_p12.json --external-fixtures build/m2e/fixtures_p12.yaml \
    --eval-cell-store build/m2e/store_B --workers 2 --pin-threads
```

`--workers 2` なのは上記の頭打ち（`repeats_min = 2`）による。`--eval-cell-store` には
**run が使った `store_A` を渡してはならない**——渡すと測り直しの子が run のセルを
resume し、独立検証が自分自身との比較に化ける。evaluate は提出 report の
`cell_store_relative` と突き合わせて fail-closed に拒否する。

**`store_A` をコピーして `store_B` を作るのも禁止**（経路検査は通ってしまうが、
コピーされたレコードは run 由来なので独立計算ではない）。セルレコードは
`store_role`（`run` / `evaluate`）を持ち、役割の異なるレコードは resume されず
再測定される（rev.6 §8.9.4 D-5）——コストは戻るが、正しさは戻る。
`store_B` は**空ディレクトリから始めること**。

**`P` の効果は実測比で示すこと**（`総時間 / P` の外挿値を成果として書かない・§3.2）。
C2/C3 の PR では fake backend の実測比を記載した。r4 では実スタックで測り直す。

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

1. ~~**C2 / C3 を実装**。r6 前に landing。~~ **完了（2026-08-02）**
   ~~残るのは C5~~ **C5 も完了（2026-08-02・`--census`）**。r6 前に必要な code change は
   すべて landing した。以降の段階は code change を伴わない。
2. **ミックス生成**: `make_vremix_fixtures.py build` で 320 本
   （40 clip × 2 bed × 4 水準）+ manifest/fixtures/生成記録。runbook §5。
3. **r4（r2-0）**: `P` 決定・並列不変性ゲート・`S`/`T_*` 校正・`env_digest` 確定・
   lockfile commit。**3 点スレッド固定を必ず適用**。
4. **r5**: `m2e_r2_shard_map.yaml` を本測定開始前に commit。
5. **r6**: 本測定（code change 厳禁）。`N_shards > R_max = 12` なら §8.8 で User 決裁へ。
6. **r7**: 破断曲線 + stem アーム 4 点。**昇格宣言をしない。M4 G2 は解錠しない。**

報告規律（§11）: **1280 セルが揃うまで帯の判定を出さない。** 部分集合での平均 RPA・
途中の破断曲線・見通しの表明は禁止。出せるのは census のみ。
