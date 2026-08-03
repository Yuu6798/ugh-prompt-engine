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
| — | **C6** シャード実行機（§8.6 の実行契約: shard_id 起動・地図の消費・時間許可・打ち切り） | **完了**（2026-08-02・地図生成器 `--make-shard-map` + 実行機 `--shard-id`/`--shard-map`） |
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
code change を減らせる。

**C6（シャード実行機）も landing した**（2026-08-02・`.claude/briefs/M2E-C6-shard-runner.md`）。
設計 §8.6 の実行契約——`shard_id` を引数に取り当該 shard のセルのみを対象とする・
`elapsed + cost(cell) <= B_session` の開始許可・`B_session + 600s` 打ち切り・
shard_id 昇順実行——を `scripts/run_melody_accuracy.py` の `--make-shard-map`
（地図生成器）/ `--shard-id --shard-map`（実行機）として実装済み。実装ノートは
rev.6 §8.9.4 D-6。**これで r6 前に必要な code change はすべて完了した**（r4/r5 の
実測残タスクのみが残る）。

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
    | tee "$(mktemp "docs/measurements/m2e_2026-08/census_stdout_$(date -u +%Y%m%dT%H%M%SZ)_XXXXXX.txt")"
```

stdout の `census sha256:` 行が公開 bytes の pin。stdout は**実行ごとに別ファイル**へ
保存する（固定名だと再実行の truncate が、失敗した実行でも前回の pin 記録を消す）。
`mktemp` が呼び出しごとに一意名を保証する（`$$` は永続シェルの PID のため同一シェル
からの同一秒リトライでは不変であり防護にならない）。**census を並行実行しないこと**
——`census.json` 自体が単一の固定パスであり、stdout 名の衝突回避では
主成果物の競合は防げない。census は 1 キャンペーンの最終集計を 1 回行う逐次ステップで
あり、並行自動化の対象にしない。成功した実行の `census.json` と、その実行の
`census_stdout_*.txt` を**対で** dated record として commit すること（保存しない実行は
pin を残さない）。`pipefail` を欠くと
`tee` の exit 0 が census の fail-closed 拒否を隠す——自動化はこの行ごとコピーすること。

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

### C6 — シャード実行機（rev.6 §8.6）— **完了（2026-08-02）**

設計 §8.6「1回の実行の契約」が要求する実行機を
`.claude/briefs/M2E-C6-shard-runner.md` に基づき実装した（実装ノート: rev.6
§8.9.4 D-6）。要求だった 5 点はすべて満たす:

- `--shard-id N --shard-map PATH` で起動し、その shard のセルのみを対象とする
  （**1回 = 1シャード**）。
- 地図生成器 `--make-shard-map`（入力: `--campaign` / `--t-direct` / `--t-stem` /
  `--startup-cost` / `--session-budget`）が §8.5 の凍結擬似コードを逐語実装し、
  `cell → shard_id` の全対応表を持つ地図 YAML を生成する（同一入力からバイト一致）。
- **新しいセルを開始してよいのは `elapsed + cost(cell) <= B_session` のときのみ**と
  いう開始許可式を、親プロセスの単調クロックで動的キュー（1 セルずつ配布）として
  実装した。
- 実行中セルが `B_session + 600s` を超えたら、multiprocessing pool を
  `terminate()` して打ち切り、失敗値でなく「未完」として shard 実行記録に残す
  （セルレコードは書かない）。
- `shard_id` の昇順で実行し、飛ばせるのはその shard が全セル digest 一致で
  完了済みの場合のみ（`_require_prior_m2e_shards_complete` が既存の
  `_cell_store_record_path` / `_cell_record_mismatches` をそのまま再利用——
  resume 互換 AC の根拠）。

campaign ファイル（`m2e-campaign/0.1`。各水準の external manifest / external
fixtures の所在のみを持つ）は
[`docs/measurements/m2e_2026-08/m2e_campaign.example.yaml`](m2e_campaign.example.yaml)
がテンプレート。実ファイルの manifest パスは `build/m2e/` 配下の想定パス（非
commit の作業成果物）でよく、fixtures パスは committed pin ファイルを指す
（存在検証は実行時）。

shard モードは run report / verdict / census のいずれも出さない——成果物は
(a) `--cell-store` 配下のセルレコード、(b) `--out` の shard 実行記録
（dated JSON）のみ。per-level の run report は、全セル完了後に既存の「1 水準
まるごと」run が store から 100% resume して生成する（下記 §5 r6 レシピ参照）。

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

**前提**: `build/m2e/` は runbook §5 の `make_vremix_fixtures.py build --out-dir`
の出力先（`manifest_p12.json` / `fixtures_p12.yaml` 等が水準ごとに揃う）。このうち
`--external-fixtures` が指す pin ファイルだけは、回す前に**下記の committed パスへ
commit しておくこと**——`build/` 配下は gitignored（非 commit の作業成果物）であり、
evaluate の `_require_attested_external_fixtures_registration` は fixtures blob が
HEAD の祖先 commit に無いと fail-closed で拒否する（§5「生成した
`fixtures_<tag>.yaml` は測定前に repo へ commit すること」）。manifest・音声・run
report・セルストアは非 commit の作業成果物のままでよい（fixtures だけが git 履歴
立証の対象）。

```bash
# 1 水準ぶんの run（repeat 0/1 × 両アームを 1 run 報告に収める。--categories は複数可）
for r in 0 1; do
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python scripts/run_melody_accuracy.py \
      --out build/m2e/run_p12_r${r}.json \
      --categories V_remix_real_direct V_remix_real_stem --level +12dB \
      --external-manifest build/m2e/manifest_p12.json \
      --external-fixtures tests/fixtures/melody_bench/m2e_vremix_fixtures_p12.yaml \
      --cell-store build/m2e/store_A --repeat-index ${r} --pin-threads
done

OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python scripts/run_melody_accuracy.py \
    --out docs/measurements/m2e_2026-08/verdict_p12.json \
    --evaluate build/m2e/run_p12_r0.json build/m2e/run_p12_r1.json \
    --external-manifest build/m2e/manifest_p12.json \
    --external-fixtures tests/fixtures/melody_bench/m2e_vremix_fixtures_p12.yaml \
    --eval-cell-store build/m2e/store_B --workers 2 --pin-threads
```

両アームを 1 run に収めるのは census がアーム対の同一 manifest を要求するため（E-6）。
verdict の `--out` は `docs/measurements/m2e_2026-08/` 配下——verdict は最終的に
commit される dated record であり（m2b/m2c の前例どおり）、上の C5 census コマンドは
まさにこの場所から 4 水準ぶんを読む。run report・store・manifest・音声は作業成果物
として `build/m2e/` のままでよい。残り 3 水準（p06/p00/m06）も同じ形で繰り返して 4
verdict を census へ渡す——この 4 行を 1 セッションで回すという意味ではない。
**中断復帰はセル台帳（§8.7・F2）が機構として担う。** 分割・実行順は §8.6 の実行契約
（`shard_id` 起動）による。上の run コマンドは r4 の校正・スモーク用の「1 水準まるごと」
の形であり、**r6 本測定は C6 実装済みの `shard_id` 起動（下記レシピ）で回す。**

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

**r6 の起動形**（C6・§8.6 の実行契約。`shard_id` 昇順で 1 回 = 1 shard）:

r4（r2-0）で確定した `S` / `T_direct` / `T_stem` を使って地図を生成し、**本測定開始前に
commit する**（§8.5「確定した `cell → shard_id` の全対応表を `m2e_r2_shard_map.yaml`
として本測定の開始前に commit する」）。campaign ファイルは
[`m2e_campaign.example.yaml`](m2e_campaign.example.yaml) をコピーして実パスに
合わせる（`build/m2e/` は非 commit の作業成果物、fixtures は committed pin）。

```bash
set -e -o pipefail  # E-120: -e で地図生成の非ゼロ exit を含むレシピ全体を即停止する
# （pipefail だけでは `$?` にその非ゼロが載るだけでシェルは止まらず、手順 1 が
# 失敗しても手順 2 の until ループへ素通りしうる）。以下の until ループ内で
# `STATUS`/`CHECK_STATUS` を明示判定する箇所は `cmd || VAR=$?`（`||` の右辺は
# errexit の対象外）で -e と両立させている——`cmd; VAR=$?` の素朴な形だと `cmd`
# の非ゼロで `VAR=$?` に到達する前にシェルごと落ち、E-88/E-113 の診断分岐が
# 効かなくなる。

# E-130（PR #242 第30巡 Codex 是正）・E-132（PR #242 第31巡 Codex 是正）:
# T_DIRECT/T_STEM/S/P は r4（r2-0 校正）の実測値——**このブロックを実行する前に
# シェルへ export しておくこと**（例: `export T_DIRECT=5.2 T_STEM=9.8 S=1.9 P=2`。
# 具体的な数値はプレースホルダではなくシェルの実際の代入文として書く）。
# §8.4 により**セッション毎に測り直す**——前セッションの値をシェル履歴等から
# 持ち越して使い回してはならない（校正が古い実行環境を代表しなくなる）。
# E-132: 以前は `T_DIRECT=<r4 実測>` のようなプレースホルダ代入行をここに
# 書いていたが、`<`/`>` は bash のリダイレクト演算子であり、このブロックを
# そのまま bash へコピーすると構文エラーになる（実測確認済み）。代入行は
# 削除し、事前 export の要求 + 下記の空検証のみへ改めた。`set -e` だけでは
# 未定義変数の展開（空文字列化）を検出できない（`-u`/`nounset` 相当が無いと
# `--t-direct ""` のように黙って壊れた引数を渡してしまう）ため、`:?` で
# 明示的に空検証する。
: "${T_DIRECT:?r4 の実測値を export しておくこと}" \
  "${T_STEM:?r4 の実測値を export しておくこと}" \
  "${S:?r4 の実測値を export しておくこと}" \
  "${P:?校正時と同じ並列度を export しておくこと}"

# 1. 地図生成（r2-0 で確定した S/T_direct/T_stem/P を渡す。同一入力ならバイト一致）。
#    --workers "$P"（E-59）: T_direct/T_stem を校正したときの P を地図へ記録する
#    （§8.4「production と同じ P」の契約）。手順 2 の実行機は省略時にこの値を採用し、
#    明示指定時は一致を要求する。
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python scripts/run_melody_accuracy.py \
    --make-shard-map \
    --campaign docs/measurements/m2e_2026-08/m2e_campaign.yaml \
    --t-direct "$T_DIRECT" --t-stem "$T_STEM" --startup-cost "$S" --workers "$P" \
    --out docs/measurements/m2e_2026-08/m2e_r2_shard_map.yaml \
    | tee "$(mktemp "docs/measurements/m2e_2026-08/shard_map_stdout_$(date -u +%Y%m%dT%H%M%SZ)_XXXXXX.txt")"
# → commit する（本測定開始前・§8.5）。N_shards > R_max(12) なら §8.8 の 3 択へ
#   User 決裁（生成器がここで fail-closed に停止する）。
# E-118（PR #242 第24巡 Codex 是正）: 地図は生成時刻を bytes に含めない（E-67）ため
# `generated at` / `shard map sha256` は stdout にしか出ない——census/shard 実行記録と
# 同じ流儀で dated log を tee し、地図（`m2e_r2_shard_map.yaml`）とこの
# `shard_map_stdout_*.txt` を対で commit すること。

# 2. shard を昇順に実行する（N は 0 から N_shards-1 まで）。§8.6「未完セルは次回の
#    実行でそのまま resume される」の「次回の実行」とは**同一 shard_id の再実行**
#    を指す——1 回の実行が B_session に収まらず unavailable/truncated/not_started
#    のいずれかが残った場合、until ループで実行記録を検査し、当該 shard の全セルが
#    完了する（cells_completed == cells_total）までは同じ shard_id を再実行してから
#    次の shard_id へ進む（E-73）。素朴に for ループで N をインクリメントするだけの
#    旧レシピでは、未完のまま次 shard へ進んで先行 shard 完了検査に必ず落ちていた。
#    E-88（PR #242 第12巡 Codex 是正）: リトライは「exit 0 かつ実行記録 JSON が
#    正常に書かれており未完セルが残っている」場合のみに限定する——**非ゼロ exit は
#    即座にレシピ全体を終了する**（失敗の永久リトライを禁止）。claim 衝突・pin
#    失敗・不正地図はいずれもリトライでは解決しないオペレータ判断案件であり、
#    黙って回し続けると気付かれないまま資源を浪費する。`--out`（mktemp 予約）が
#    exit 0 なのに 0 バイトのまま（実行記録が書き出されていない）場合も異常として
#    即終了する。
for N in $(seq 0 $(( $(python -c "import yaml,sys; print(yaml.safe_load(open('docs/measurements/m2e_2026-08/m2e_r2_shard_map.yaml'))['n_shards'] - 1)") ))); do
  while :; do
    OUT="$(mktemp "build/m2e/shard_run_${N}_$(date -u +%Y%m%dT%H%M%SZ)_XXXXXX.json")"
    STATUS=0
    OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python scripts/run_melody_accuracy.py \
        --shard-id "$N" \
        --shard-map docs/measurements/m2e_2026-08/m2e_r2_shard_map.yaml \
        --campaign docs/measurements/m2e_2026-08/m2e_campaign.yaml \
        --cell-store build/m2e/store_A --workers "$P" \
        --out "$OUT" \
        | tee "$(mktemp "build/m2e/shard_run_${N}_stdout_$(date -u +%Y%m%dT%H%M%SZ)_XXXXXX.txt")" \
        || STATUS=$?
    # `set -o pipefail`（冒頭で宣言済み）により、`STATUS` は tee ではなく実行機
    # 自体の exit status を指す。非ゼロは即終了（E-88・失敗の永久リトライ禁止）。
    # `|| STATUS=$?`（E-120）: `set -e` 下でパイプライン自体に素朴に `STATUS=$?`
    # を続けると、非ゼロで捕捉前にシェルごと落ちる——`||` の右辺で捕捉する。
    if [ "$STATUS" -ne 0 ]; then
      echo "shard $N の実行が非ゼロ exit ($STATUS) で終了した。claim 衝突・pin 失敗・" >&2
      echo "不正地図等の可能性がある——原因を確認してから再実行すること (fail-closed・E-88)" >&2
      exit "$STATUS"
    fi
    if [ ! -s "$OUT" ]; then
      echo "shard $N: --out $OUT が exit 0 なのに空のまま（実行記録が書かれていない）。" >&2
      echo "異常終了の疑い——原因を確認してから再実行すること (fail-closed・E-88)" >&2
      exit 1
    fi
    # 実行記録を検査する: cells_completed == cells_total（未完 3 種がすべて空）に
    # なっていれば shard N は完了——次の shard_id へ進む。E-113（PR #242 第22巡
    # Codex 是正）: リトライ継続は「未完が truncated / not_started のみ」の場合に
    # 限定する——cells_unavailable（CREPE/Demucs/重み不在等、環境起因で消えない
    # 未完）が非空なら、盲目的に再実行し続けず即座にレシピを終了しオペレータ対応へ
    # 回す（失敗の永久リトライを禁じた E-88 の精神を cells_unavailable にも適用）。
    # exit 0 = shard 完了（break）／1 = 検証済みで truncated・not_started のみ残存
    # （再実行）／2 = cells_unavailable 非空（即時終了）／3 = パース失敗・キー欠損・
    # 検証失敗（fatal・即停止）。E-127（PR #242 第29巡 Codex 是正）: 旧レシピは
    # JSON パース失敗や必須キー欠損を素通しし、Python の素の未捕捉例外が既定で
    # exit 1 を返すため「truncated/not_started のみ残存」（意図的な exit 1）と
    # 区別が付かず、壊れた実行記録を検証済みの未完と誤認して盲目的に再実行し
    # 続けてしまっていた（cells_unavailable と同型の「リトライで直らない失敗」を
    # 見逃す穴）。パース・検証は明示的に try/except で捕捉し、3 という別コードへ
    # 分離する——シェル側は 0/1/2 以外（3 を含む未知のコードすべて）を fatal と
    # みなして即座に終了する。
    # E-134（PR #242 第33巡 Codex 是正）: E-127 は「パース失敗・キー欠損」しか
    # 検査しておらず、0/1 を返す**前**の検証が cells_unavailable/cells_completed/
    # cells_total の 3 キーの**存在**（dict 添字アクセスが KeyError で例外化する
    # ことに依存）に留まっていた——schema_version の不一致（別世代の実行機/別
    # スキーマの record）・shard_id の不一致（別 shard の record を誤って渡す
    # コピペ事故）・型崩れ（cells_total が文字列化されている等）・会計の破綻
    # （cells_measured/cells_resumed/cells_unavailable/cells_truncated/
    # cells_not_started の内訳が cells_total と合わない壊れた record）のいずれも
    # 素通しし、0/1 判定（「完了」/「truncated・not_started のみ残存」）を汚染
    # された record に対して下してしまいうる穴だった。0/1 を返す前に、
    # schema_version 一致・shard_id 一致（期待値は `$N`）・cells_total/
    # cells_completed が非 bool 整数・cells_measured/cells_resumed/
    # cells_unavailable/cells_truncated/cells_not_started がいずれも list・
    # 会計不変条件（`len(measured) + len(resumed) == completed` かつ
    # `completed + len(unavailable) + len(truncated) + len(not_started) ==
    # total`——`execute_m2e_shard` の実フィールド名・E-92 が確立した
    # measured/resumed の相互排他分割に合わせる。`cells_resumed` は
    # `cells_completed` の**部分集合**であり加算対象ではない——単純に
    # `completed + resumed + 未完各種` を合計すると二重計上になる）を全数検証し、
    # いずれかの失敗も exit 3（fatal・E-127 の規約を踏襲）へ落とす。
    # `|| CHECK_STATUS=$?`（E-120）: 上と同じ理由——`set -e` 下では非ゼロ sys.exit
    # を素朴に `CHECK_STATUS=$?` で捕捉する形は機能しない。
    CHECK_STATUS=0
    python -c "
import json, sys

EXPECTED_SCHEMA = 'm2e-shard-run/0.1'
EXPECTED_SHARD_ID = $N


def fatal(msg):
    print(f'shard record validation failed: {msg}', file=sys.stderr)
    sys.exit(3)


try:
    with open('$OUT') as f:
        r = json.load(f)
except Exception as exc:
    fatal(f'JSON parse failed: {exc!r}')

if not isinstance(r, dict):
    fatal(f'shard record is not a JSON object: {r!r}')
if r.get('schema_version') != EXPECTED_SCHEMA:
    fatal(f'schema_version {r.get(\"schema_version\")!r} != {EXPECTED_SCHEMA!r}')


def require_int(key):
    v = r.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        fatal(f'{key} is not a non-bool int: {v!r}')
    return v


def require_list(key):
    v = r.get(key)
    if not isinstance(v, list):
        fatal(f'{key} is not a list: {v!r}')
    return v


shard_id = require_int('shard_id')
if shard_id != EXPECTED_SHARD_ID:
    fatal(f'shard_id {shard_id!r} != expected {EXPECTED_SHARD_ID!r}')

total = require_int('cells_total')
completed = require_int('cells_completed')
measured = require_list('cells_measured')
resumed = require_list('cells_resumed')
unavailable = require_list('cells_unavailable')
truncated = require_list('cells_truncated')
not_started = require_list('cells_not_started')

# E-92 の相互排他分割: 1 セルは measured か resumed のどちらか一方にのみ属す。
if len(measured) + len(resumed) != completed:
    fatal(
        f'cells_measured({len(measured)}) + cells_resumed({len(resumed)}) != '
        f'cells_completed({completed})'
    )
# cells_resumed は cells_completed の部分集合（加算対象ではない）。
if completed + len(unavailable) + len(truncated) + len(not_started) != total:
    fatal(
        f'cells_completed({completed}) + cells_unavailable({len(unavailable)}) + '
        f'cells_truncated({len(truncated)}) + cells_not_started({len(not_started)}) != '
        f'cells_total({total})'
    )

if unavailable:
    sys.exit(2)
sys.exit(0 if completed == total else 1)
" || CHECK_STATUS=$?
    if [ "$CHECK_STATUS" -eq 0 ]; then
      break
    elif [ "$CHECK_STATUS" -eq 1 ]; then
      : # 検証済みで truncated/not_started のみが残存——同一 shard_id を再実行する。
    elif [ "$CHECK_STATUS" -eq 2 ]; then
      echo "shard $N: cells_unavailable が非空——CREPE/Demucs/重み不在等はリトライで直らない。" >&2
      echo "原因を確認してから対応すること (fail-closed・E-113)" >&2
      exit 1
    else
      echo "shard $N: 実行記録の検査自体が失敗した (CHECK_STATUS=$CHECK_STATUS)。schema_version・" >&2
      echo "shard_id・型・会計不変条件のいずれかの検証に失敗した——リトライでは直らない。原因を" >&2
      echo "確認してから対応すること (fail-closed・E-127/E-134)" >&2
      exit 1
    fi
  done
done
```

- **E-55**: `mktemp` は 0 バイトの予約ファイルを先に作る——実行機はこれを上書き対象
  として許容する（非空の既存ファイルのみ拒否・fail-closed）。上記の `--out`/tee 先
  レシピはこの前提で書かれている。
- **E-74（同一 shard の排他 claim）**: `--cell-store build/m2e/store_A` 配下に
  `shard_<N>.claim` が作られ、同じ `shard_id` の並行実行を防ぐ。**クラッシュ孤児**
  （コンテナ強制終了等で claim が残ったまま実行機が終了した場合）に遭遇したら、
  `rm build/m2e/store_A/shard_<N>.claim` で手動削除してから再実行する（claim の中身
  は PID + 時刻の診断情報のみで、削除しても測定済みセルレコードには影響しない）。
- `--workers "$P"`（E-59）: 省略すれば手順 1 が地図に記録した `P` を採用する。明示
  指定するなら地図の `P` と完全一致する必要がある（不一致は fail-closed）——上記の
  ように手順 1・2 で同じ `$P` を使えば自動的に一致する。
- shard 実行記録（`--out`）は run report ではない——run report / verdict / census の
  いずれも出さない（成果物は `--cell-store` のセルレコードとこの実行記録のみ）。
  `build/m2e/` は非 commit の作業成果物なので、実行記録は dated として残すだけでよい
  （commit 対象ではない）。
- 先行 shard（`shard_id < N`）に digest 一致で完了していないセルが 1 つでもあると
  fail-closed で拒否する——**昇順を飛ばさない**こと。1 回の実行が
  `B_session`（既定 7200s = 2.0h・§8.2）で終わらなくても延長しない
  （超過は異常ではなく通常状態・§8.6）。未完セルは次回の実行でそのまま resume される。
- **全 shard 完了後**、既存の「1 水準まるごと」run（上の r4/r6 起動形の `for r in 0 1`
  ブロック）を同じ `--cell-store build/m2e/store_A` で回すと、shard 実行機が書いた
  セルが digest 一致で 100% resume され、水準ごとの run report が生成される
  （resume 互換 AC）。そこから先は上記 C5 census レシピと同じ形。
- `T_*`/`S`/`B_session` を変更した場合は**未完セルについてのみ**地図を引き直す
  （§8.5・完了済みセルのレコードは影響を受けない）。セル台帳（fixtures）自体を
  変えてはならない——変えると地図生成器・実行機の両方が `_require_m2e_shard_map_
  matches_registry` で fail-closed になる（意図どおり）。
- **E-115**: 上記の地図引き直しで `--cell-store` 配下の残セルが 0 件（全セル
  完了済み）になっている場合、地図生成器は空の地図を作らず明示エラーで拒否する
  （fail-closed）——その時点で r6 は完了しており、次フェーズ（r7）へ進む。

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
   ~~残るのは C5~~ **C5 も完了（2026-08-02・`--census`）**。
   ~~C6（シャード実行機）が未着手~~ **C6 も完了（2026-08-02・`--make-shard-map` /
   `--shard-id`）**——「以降の段階は code change を伴わない」は 2026-08-02 時点の
   過大な記述だった（PR #241 レビューで顕在化・撤回）が、**今度こそ r6 前に必要な
   code change はすべて完了した**。
2. **ミックス生成**: `make_vremix_fixtures.py build` で 320 本
   （40 clip × 2 bed × 4 水準）+ manifest/fixtures/生成記録。runbook §5。
3. **r4（r2-0）**: `P` 決定・並列不変性ゲート・`S`/`T_*` 校正・`env_digest` 確定・
   lockfile commit。**3 点スレッド固定を必ず適用**。
4. ~~**C6 を実装**（別ブリーフ）~~ **完了（2026-08-02）**。地図生成器
   `--make-shard-map` と実行機 `--shard-id`/`--shard-map` は landing 済み
   （§2 C6 節・§5 上記の r6 起動形レシピ参照）。
5. **r5**: `m2e_r2_shard_map.yaml` を本測定開始前に commit（`--make-shard-map` で
   r4 の `S`/`T_direct`/`T_stem` から生成する。上記 r6 起動形レシピの手順 1）。
6. **r6**: 本測定（code change 厳禁）。`shard_id` 起動（C6・上記レシピの手順 2）で
   回す。`N_shards > R_max = 12` なら §8.8 で User 決裁へ。
7. **r7**: 破断曲線 + stem アーム 4 点。**昇格宣言をしない。M4 G2 は解錠しない。**

報告規律（§11）: **1280 セルが揃うまで帯の判定を出さない。** 部分集合での平均 RPA・
途中の破断曲線・見通しの表明は禁止。出せるのは census のみ。
