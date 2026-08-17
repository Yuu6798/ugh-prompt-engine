# S3 run 4 GPU 実行 runbook（クロー向け・S1 runbook 差分方式）

設計正本: [`DESIGN_S3_backfill.md`](DESIGN_S3_backfill.md) §4「Phase D — run 4 GPU
実行」。**本書は `S1_GPU_RUNBOOK.md` の内容を変更しない**（S1 runbook は S1 の
正本のまま不変。参照のみ）。本書と設計が食い違ったら**設計
（`DESIGN_S3_backfill.md`）が勝つ**。`S1_GPU_RUNBOOK.md` の規律（§0 冒頭）を
踏襲する: 本書は手順のみを記述し、閾値・予算上限・学習規模は設計側が凍結する。

**方針**: 音声データセットの転送は行わない。全素材・全データセットは**決定論
pin からの再生成**でクローへ受け渡す。実体そのもの（wav/csv）はコミットしない。
pin 表 = [`results_s3/run4_dataset_pins.json`](results_s3/run4_dataset_pins.json)
（D3/user 各データセットの transcriptions.csv・wavs 全本・(user のみ)
exclusions.json の sha256 実測値。手打ちなし）。

**実装状況（R4 改訂 2026-08-17）**: 当初「未実装」としていた 3 話者アセンブリ
（`s1_dataprep/assemble_run4.py`）・user 話者合成（`s1_gate/gate_synth_run4.py`）・
三角補間フォージ（`s1_gate/forge_triangle.py`）は**すべて実装済み**（§3/§5 参照。
GPU 実測のみ未実施 = run 4 が初実行）。残る未解決ギャップは **run 3 実
config.yaml の一次照合（AI-Drive 退避先）のみ**。§8 に全件を集約する。

---

## 0. 予算・承認状態

`DESIGN_S3_backfill.md` §4・§7 Q2 のとおり:

- 見積 **$2–5**、**上限 $8 で打ち切り**（`S1_GPU_RUNBOOK.md` の $15 上限とは
  別値 — run 4 は run 3 のパラメータ踏襲 + 2 データセット追加のみで学習規模
  自体は変わらないため、S1 実測（run3 40K = 実測 ~4.3h ≈ $1.1〜2.2、RTX 3090
  Community $0.22/h）を踏まえた run 4 固有の低い上限）。
- **User 承認済み（2026-08-17、タイミング任意）**。着手はクロー稼働タイミング
  待ちのみ。
- 費用記録の作法は `S1_GPU_RUNBOOK.md` §6 と同一（節目ごとに UTC 時刻・累積
  経過時間・累積費用実測・備考を記録する。自己申告ではなくダッシュボード実測
  額を使う）。

---

## 1. 素材表（S1 runbook §2 との差分）

| # | 素材 | 出典 | 差分 |
|---|---|---|---|
| 1–3 | 波音リツ強連続音 Ver1.5.1 / PJS corpus ver1.1 / リツ公式 DiffSinger 配布 zip | `S1_GPU_RUNBOOK.md` §2 の pin 表をそのまま使用 | 変更なし |
| 4（新規） | User 音源 17 本（donor_A/donor_B mp3 2 本 + m4a 15 本。Drive「音楽サンプル」原本） | `recording_kit/user_donor_ledger.json`（`user-donor-ledger/0.1`）の各エントリ `source_sha256` が正 | 本書で新規追加。取得は User 側（原本は Drive 保管、本セッションは非保有） |

素材 4 の pin 照合は「17 本すべての `source_sha256` が台帳の値と一致すること」
（台帳を手打ちしない・改変しない）。台帳のフィールド定義・カード対応表は
`recording_kit/intake_records/intake_record_2026-08-17.md` §2/§6 が正本。

---

## 2. データセット再生成手順

**照合不一致はそこで停止する（黙って続行しない）**。以下 (a)(b)(c) はすべて
このルールに従う。

### 2.1 (a) D1 (PJS) / D2 (リツ) — S1 と同一

`S1_GPU_RUNBOOK.md` §2〜§3 をそのまま実行する（`convert_ritsu.py` /
`convert_pjs.py` の呼び出し・照合値は無変更）。差分なし。

### 2.2 (b) D3 再生成 → `convert_d3.py` → pin 照合

**本スクリプトで実行する（2026-08-17 追加・R8 レイアウト修正込み）**: 下記
手順 2〜3（40 セル render + tripwire 先行照合 + 全数 sha256 照合）は
`scripts/run_d3_cells.py` 1 コマンドで置き換えられる（`render.py`/
`converter` 群には一切触れない新規追加スクリプト。ローカル実測 = 40/40
PASS、tripwire 2 件一致、所要 約 7 分 CPU のみ・GPU 不要）:

```bash
python voice_genesis/foundry/scripts/run_d3_cells.py \
    --voicebank-root <波音リツ強連続音Ver1.5.1 展開先> \
    --out-dir <出力ディレクトリ>
```

`--preset`/`--manifest`/`--results` は既定でリポ内正本
（`adapter/presets/ritsu_neutral.json`/`results_s3/d3_manifest.json`/
`results_s3/d3_manifest_results.json`）を指すため省略可。spec 変種は
`json.dumps` によるJSON再シリアライズではなく `"seed"` 数値部分のみの
テキスト置換で生成し、元 seed 値（=11）のセルが base preset とバイト
同一であることを render 前に assert する。tripwire（sakura/umi の
seed=11）が manifest の tripwire sha256 と不一致の場合は「環境ドリフト・
全セル無効」として残り 38 セルを render せず即座に非 0 exit する。
40 セル render 後、wav/timing csv の sha256 を
`d3_manifest_results.json` と全数照合し、per-cell PASS/FAIL 表を
stdout と `<out-dir>/verify_report.txt` へ出力する（1 件でも不一致
〔または当該セッションでの render 失敗〕なら非 0 exit。既存 `--out-dir`
への再実行で render が失敗しても前回実行の残存ファイルで false PASS には
ならない）。**出力は `<out-dir>/render/` に wav + timing csv を同一 stem で
同居させる**（`convert_d3.py` の `discover_pairs()` が単一ディレクトリ
非再帰で pair を発見する契約のため）ので、手順 4 の `--render-dir` には
**`<out-dir>/render` をそのまま渡す**。以下の手順 2〜3 は参考情報として
残す（手順 1・4・5 は本スクリプトの対象外 — 変更なし）。

1. リツ voicebank を `s1_dataprep/README.md` §0 の pin（zip
   `88c7b3ef…df66dde76`）で取得・照合済みのものを使う（(a) で取得済みの実体を
   再利用してよい）。
2. `results_s3/d3_manifest.json`（40 セル事前登録殻: 4 スコア
   `sakura`/`umi`/`d3_sustain`/`d3_kana` × 10 seed
   `11,101,211,307,401,503,601,701,809,907`、base preset
   `adapter/presets/ritsu_neutral.json` の `seed` フィールドのみを差し替えた
   spec 変種）に従い、各セルを `adapter/render.py`（`SCORE_REGISTRY` 対応済み）
   で render する:

   ```bash
   python -m adapter.render \
       --score <score> --voice <ritsu_neutral の seed 差し替え版 spec> \
       --donor ritsu --voicebank-root <波音リツ展開先> \
       --out <out>/<score>_seed<seed>.wav \
       --timing-out <out>/<score>_seed<seed>.csv
   ```

   spec 変種の生成方法・具体のファイル配置は
   `results_s3/d3_manifest_results.json` の各セルの `spec_path`（例:
   `.../specs/ritsu_neutral_seed11.json`）が実例。**`seed=11` の `sakura`/`umi`
   セルは `d3_manifest.json` の tripwire sha256（`sakura_seed11_wav_sha256` /
   `umi_seed11_wav_sha256`）とバイト一致することを最初に確認する**（不一致 =
   環境ドリフトとして全セル無効・そこで停止）。
3. 生成した 40 セル（wav + timing csv）の sha256 を、`d3_manifest_results.json`
   の対応セル（`wav_sha256`/`timing_csv_sha256`）と全数照合する。
4. `s1_dataprep/convert_d3.py` で変換する:

   ```bash
   python voice_genesis/foundry/s1_dataprep/convert_d3.py \
       --render-dir <40 セルの wav+csv を含むディレクトリ> \
       --out-dir <D3 dataset out>
   ```

   出力 `<D3 dataset out>/transcriptions.csv` + `wavs/`（44.1kHz）。
5. `<D3 dataset out>/transcriptions.csv` の sha256 と `wavs/` 40 本各々の
   sha256 を `results_s3/run4_dataset_pins.json` の `d3.transcriptions_csv_sha256`
   / `d3.wav_sha256` と全数照合する。

   ```bash
   sha256sum <D3 dataset out>/transcriptions.csv
   ( cd <D3 dataset out>/wavs && sha256sum *.wav )
   # run4_dataset_pins.json の d3.wav_sha256 と diff
   ```

再現手順の完全版・実効分数の参照値（ph_dur 合計 1200.50s = 20.008 分）=
`results_s3/d3_dataset_record.md` §4。

### 2.3 (c) user 再生成 → `intake.py` → `convert_user.py` → pin 照合

1. User 原本 17 本（Drive「音楽サンプル」）を取得し、各ファイルの sha256 を
   `recording_kit/user_donor_ledger.json` の `source_sha256` と 17/17 照合する
   （台帳に列挙された `card_id`/`source_filename` 対応表 =
   `intake_records/intake_record_2026-08-17.md` §2）。
2. 正規化 wav の **replay 再生成**（Codex R2 P1 対応・2026-08-17 改訂:
   コミット済み台帳へ `intake.py` を再実行すると `_check_existing_artifacts`
   （正規化 wav 未存在）と `_check_duplicate_sources`（source_sha256 既存）で
   **必ず fail-closed 停止する**。intake.py は新規受領用であり replay 用では
   ない）。replay は台帳を書き換えず、ffmpeg 直接変換で行う:

   ```bash
   # 各原本について（パラメータ = intake_record_2026-08-17.md §4 と同一）
   ffmpeg -y -i <原本> -ac 1 -ar 24000 -sample_fmt s16 <台帳 normalized_path のファイル名>
   ```

   代替: 空の一時台帳を指定して intake.py を実行し、生成エントリを
   コミット済み台帳とフィールド比較してもよい（一時台帳は破棄する）。

   いずれの場合も、生成した正規化 wav 17 本の sha256 を台帳 `sha256`
   フィールドと照合する
   （`intake_record_2026-08-17.md` §4 のとおり、ffmpeg 版差でバイト不一致に
   なった場合は黙って台帳値を書き換えず、一致する版を探すか再 intake を設計
   する）。
3. `s1_dataprep/convert_user.py` で変換する（`--dsdict` はリツ公式
   DiffSinger 配布 zip の `dsdur/dsdict.yaml`、`S1_GPU_RUNBOOK.md` 素材 3 の
   pin `5c7b8c32…` で事前照合済みのものを使う）:

   ```bash
   python voice_genesis/foundry/s1_dataprep/convert_user.py \
       --normalized-dir <正規化 wav 17 本のディレクトリ> \
       --ledger voice_genesis/foundry/recording_kit/user_donor_ledger.json \
       --dsdict <NamineRitsu_DiffSinger 展開先>/dsdur/dsdict.yaml \
       --out-dir <user dataset out>
   ```

   出力 = `transcriptions.csv`（15/17 採用）+ `wavs/`（44.1kHz）+
   `exclusions.json`（UC-009/UC-010 の除外理由）。
4. `<user dataset out>/transcriptions.csv` / `exclusions.json` / `wavs/` 15 本
   各々の sha256 を `results_s3/run4_dataset_pins.json` の `user.*` と全数照合
   する。

再現手順の全文（tier 別アラインメント戦略・dsdict グラフェム音素化の詳細・
実効分数 ph_dur 合計 233.395s = 3.890 分・音素被覆 33 種）=
`results_s3/user_dataset_record.md`（全文）。

---

## 3. 3 話者アセンブリ — `assemble_run4.py`（実装済み 2026-08-17）

**（R3 改訂: 本節の旧版は「結合スクリプト未実装」と記述していたが、
`s1_dataprep/assemble_run4.py` の実装完了に伴い全面差し替え。§8.1 と整合）**

`s1_dataprep/assemble_run4.py` が run 4 の 3 話者 raw 構成を一括で行う
（`build_dataset.py` は無変更のまま、その検証関数群を read-only import で再利用）:

```bash
python voice_genesis/foundry/s1_dataprep/assemble_run4.py \
    --ritsu-raw-dir <D2 = convert_ritsu 出力> \
    --d3-raw-dir    <D3 = convert_d3 出力> \
    --pjs-raw-dir   <D1 = convert_pjs 出力> \
    --user-raw-dir  <user = convert_user 出力> \
    --out-dir       <run4_raw>
```

- **D3→ritsu 合流**（transcriptions.csv 行連結 + wavs/ マージ）を内部で実施。
  name 列・wav 実ファイル名の**全数比較による無衝突検査**込み（衝突 =
  fail-closed。ローカル実測: ritsu 456 + D3 40 = 496 行・衝突 0）
- spk_id は **ritsu(D2+D3)=0 / pjs=1 / user=2** 固定（既存 2 話者 checkpoint の
  順序を保存し user を新規 3 番目に置く。根拠は同スクリプト docstring）
- 3 話者それぞれに validate_speaker / check_ph_dur_duration /
  check_note_dur_consistency を実行し、全問題収集 → 1 件でも fail-closed
- 統合辞書・assembly_manifest.json（各話者 row/wav 数・合計秒数・
  transcriptions sha256・衝突検査結果・user の exclusions.json sha256・
  生成 config の sha256〔live/normalized 両方〕）も出力（review #265 R11
  P1/P2、schema `run4-assembly-manifest/0.3`）
- **3 話者学習 config も自動生成する**（R7 改訂・§4 参照）: `build_dataset.py`
  `build_config_yaml()`（`speakers` 引数は話者数非依存の汎用実装）を
  read-only 再利用し、`<out-dir>/run4_config_datasets.yaml`（実行時 config）
  + `<out-dir>/run4_config_datasets.yaml.normalized.yaml`（host 非依存の pin
  用コピー）を出力する。`datasets:` は ritsu(0)/pjs(1)/user(2) の 3 エントリ・
  `num_spk: 3`。`--binary-data-dir`/`--n-test-prefixes`/`--max-updates`/
  `--val-check-interval`/`--num-ckpt-keep` で上書き可（既定値は
  `build_dataset.py` と同一）

注意: 本セッションのローカル実測は D1 のみ合成ミニフィクスチャ
（`--pjs-is-fixture`）で行った。**本番は実 PJS（convert_pjs 出力）を渡し
`--pjs-is-fixture` を付けないこと**。

---

## 4. 学習構成（run 3 踏襲）— 設定の所在に関する注意

**3 話者版 `datasets:`/`num_spk`/学習規模 config は `assemble_run4.py`
実行で自動生成される**（R7 改訂・実装済み。§3 参照）: `--out-dir` 実行後、
`<out-dir>/run4_config_datasets.yaml`（実行時 config）と
`<out-dir>/run4_config_datasets.yaml.normalized.yaml`（pin 用コピー）が
既に揃っている。クローが本節の作業として行うのは、この `datasets:` 節
（3 話者・`num_spk: 3`。生成済みのため編集不要）を土台に、**下記の
LR/finetune/精度/勾配クリップの 4 項目のみを run 3 実 config.yaml から
手動移植**すること（`build_dataset.py`/`assemble_run4.py` いずれの CLI にも
この 4 項目のフックは無い——理由は次段落）。

`DESIGN_S3_backfill.md` §4: 「finetune 機構・bf16+clip・LR 0.0002・40K・各 5K
節目 NaN スキャン」を run 3 から踏襲する。**これらは `build_dataset.py` の CLI
引数では設定できない**（同スクリプトが CLI で公開する学習規模フィールドは
`--max-updates`/`--val-check-interval`/`--num-ckpt-keep` の 3 つのみ —
`build_dataset.py:854-867`。LR/finetune/精度/clip の CLI フックは無い。
`assemble_run4.py` の config 生成も同じ 3 フィールドのみを公開し、これら
4 項目は生成しない — 意図的な役割分担: `datasets:`/`num_spk`/学習規模の
自動導出可能な節は生成器が担い、run 3 由来で自動導出できない 4 項目は
クローの手動移植に委ねる）。

**この 4 項目の手動移植は `run4_config_datasets.yaml`（live config）のみへ
行い、その直後に `refresh-config-pin` サブコマンドを実行して
`.normalized.yaml`（pin 用コピー）を再生成すること**（review #265 R9 で
新設・実装済み）:

```bash
python voice_genesis/foundry/s1_dataprep/assemble_run4.py refresh-config-pin \
    --config <out-dir>/run4_config_datasets.yaml
```

手動編集を live config のみへ加えて pin 副本を放置すると、`.normalized.yaml`
は編集前の内容のまま取り残され、実行された学習の実 config を証明する pin
として機能しなくなる（R9 で判明した R7 の残穴）。`refresh-config-pin` は
`datasets:`/`num_spk` 等のパス系フィールドを再正規化した上で、パス系以外の
全キー・全値が live config と完全一致すること（＝手動追記した 4 項目が
そのまま反映されていること）と、`datasets[].speaker`/`spk_id` の対応が
既定マッピング（ritsu=0/pjs=1/user=2）から崩れていないことを再読込検証し、
不一致があれば `.normalized.yaml` へは一切書き込まずに fail-closed する。
運用手順は **「手動編集 → `refresh-config-pin` → 学習開始」** の順を厳守する。

`refresh-config-pin` 成功時、`assembly_manifest.json`（`<out-dir>` 直下に
存在する場合）の `config.config_sha256`/`config.normalized_config_sha256` を
実測値へ自動更新する（review #265 R11 P1）。**学習開始前に
`sha256sum <out-dir>/run4_config_datasets.yaml` を実測し、
`assembly_manifest.json` の `config.config_sha256` と一致することを目視
確認すること**（記帳された pin と実際に学習へ渡す config が一致している
ことの最終確認 — 手動編集後に `refresh-config-pin` の実行を忘れた場合は
ここで不一致として検出できる）。

`results_s1/s1_record_2026-08-15.md` の記述（§ run3 起動・§ config.yaml の
逸脱と対処、行 452-495）によれば、run 3 ではこれらは **GPU インスタンス側で
`config.yaml` を手動編集**して設定された:

- `finetune_enabled: True`
- `finetune_ckpt_path: <run 1 の 5K checkpoint パス>`（run 4 では対応する
  「直近の安定 checkpoint」を指す値に読み替える必要がある — run 3 40K
  checkpoint そのものを指すのか、run 3 のどこかの中間 checkpoint を指すのか
  は `DESIGN_S3_backfill.md` に明記が無い）
- `pl_trainer_precision: bf16-mixed`
- `optimizer_args.lr: 0.0002`
- 勾配クリッピング 1.0（s1_record では `gradient_clip_val=1.0` と表記される
  が、config.yaml 上の正確なキー名 — トップレベルか `pl_trainer_*` 配下か —
  は s1_record 本文に明記が無い）

**run 3 実際の `config.yaml` 実体は本リポジトリに存在しない**（AI-Drive
`/s1_ritsu_pjs_acoustic_v1/run3/` へ退避済みと s1_record にあるが、本セッション
はそこへアクセスできない）。したがって上記キー名・finetune 元 checkpoint の
具体パスは s1_record の文章記述からの引用であり、**実 YAML との一次照合は
できていない**。クロー側で run 3 の実 `config.yaml`（AI-Drive 退避先）を確認し、
run 4 の `config.yaml`（§3 の 3 話者化拡張後の `build_dataset.py` が生成する
版）へ同一キー・同一値を移植することを要確認とする（§8）。

40K steps・各 5K 節目 NaN スキャンは `S1_GPU_RUNBOOK.md` §5 の早期打ち切り
ゲート手順と同一の運用（5K/10K/20K/40K の節目で checkpoint 回収 + state_dict
の非有限値チェック）をそのまま適用する。

---

## 5. ゲート判定材料の生成手順（① ~ ④）— User 耳判定の受け皿

`DESIGN_S3_backfill.md` §4 のとおり、判定そのものは User が行う。クローは
判定材料 wav の生成までを担う。

### ①リツ極再現（り→ん判定用の合成）

`s1_gate/gate_synth.py run`（`--speaker ritsu`・既定）で sakura/umi を合成し、
り→ん破綻（長母音サステインの崩壊、`DESIGN_S3_backfill.md` §0-1 の仮説）が
D3 投入前後でどう変化したかを聴取できる形で揃える。手順自体は
`S1_GPU_RUNBOOK.md` §5.2/§5.3 と同一（`--ckpt-dir`/`--step`/`--exp-name` を
run 4 の checkpoint に差し替えるのみ）。

### ②spk3 アンカー単独合成（第 3 声の立ち）

**実装済み（2026-08-17 更新）**: `s1_gate/gate_synth_run4.py` を使う
（`gate_synth.py run` と同一引数の委譲ラッパーで `--speaker user` を受理。
gate_synth の speaker 解決は `*.{speaker}.emb` の純文字列 glob のため、
run 4 checkpoint から export した `*.user.emb` を置けば既存経路で合成される。
GPU 実測は未実施 — run 4 の 5K 早期ゲートが初実行）。

### ③既存 2 アンカーの回帰（S1 ゲート 5 点）

`results_s1/s1_record_2026-08-15.md` §「S1 ゲート判定」の 5 判定ポイント
（①歌声か ②日本語か ③接合ノイズ不在の維持 ④S0 との差が量的か ⑤2 話者の
描き分け）を、run 4 の checkpoint による sakura/umi × ritsu/pjs 合成 4 本
（①と同じ `gate_synth.py run` 呼び出しを `--speaker pjs` でも実行）に対して
再実施する。回帰対象はこの 5 点であり、材料生成の手順自体は run 3 判定時と
同一。

### ④三角形内部補間バッチ（S2 と同じブラインド規律への参照）

`results_s2/s2_record_2026-08-16.md`（2 アンカー間 lerp/slerp・4 候補分割・
ブラインドシャッフル・隠しコントロール `H'` の規律）を「同じ規律」の参照先
とする。**実装済み（2026-08-17 更新）**: `s1_gate/forge_triangle.py` を使う
（3 アンカー重心座標補間 + S2 ブラインド規律の機械化: 対応表 sha256 封印分離・
4 候補バッチ・隠しコントロール・無識別命名。genome 台帳は S2 同一 schema の
VG-S3 系列）。GPU 実測は未実施 — 実 embed 入力は run 4 checkpoint export が
初実行。規律の一次ソースは引き続き `s2_record_2026-08-16.md`。

---

## 6. 成果物の持ち帰り様式

`S1_GPU_RUNBOOK.md` §6 の費用記録表と同一書式に加え、run 4 は下記を
`results_s3/s3_record_<date>.md`（`DESIGN_S3_backfill.md` §6 Acceptance
Criteria が要求する出口記録）へ記帳する:

| 区分 | 記帳内容 |
|---|---|
| 学習ログ | train.log・TensorBoard events（5K 節目ごとの NaN スキャン結果を含む） |
| checkpoint | 5K/10K/20K/40K（打ち切り時は直近）の sha256・退避先 |
| 判定材料 wav | ①~④ 各材料の wav sha256・生成コマンド・(④のみ) ブラインド対応表（封印） |
| 費用実測 | `S1_GPU_RUNBOOK.md` §6 表と同一書式（起動・各節目・終了/打ち切り理由） |
| D3/spk3 の効果帰属 | User 耳判定逐語 + Claude 側の解釈（Fable 設計判断） |
| Open Questions | 未解決事項（§8 の「要確認」で解消しなかった項目を含む） |

---

## 7. 検証（本セッションで実施済み）

- `results_s3/run4_dataset_pins.json` の全 sha256（transcriptions.csv 2 件・
  exclusions.json 1 件・wav 55 本）を、実体（`d3_dataset_run/dataset/` /
  `c_verify/user_dataset_v2/`）に対して独立再計算し、**全件一致を確認済み**
  （不一致 0 件）。
- 本書に記載した全コマンド・パス・config 項目は、以下の一次ソースへ接地
  確認済み: `S1_GPU_RUNBOOK.md`（§1 参照コマンド）、`build_dataset.py`（CLI
  引数・`speakers` 直書き・`num_spk` 算出・学習規模フィールドの範囲）、
  `convert_d3.py`/`convert_user.py`（CLI 引数）、`gate_synth.py`（`--speaker`
  choices）、`render.py`（`SCORE_REGISTRY`/`--timing-out`）、
  `d3_manifest.json`/`d3_manifest_results.json`、`user_donor_ledger.json`、
  `intake_record_2026-08-17.md`、`s1_record_2026-08-15.md`、
  `s2_record_2026-08-16.md`。接地できなかった項目（run 3 実 config.yaml の
  キー名・finetune 元 checkpoint の具体パス）は §4/§8 で「要確認」と明記した。

---

## 8. クロー側で要確認（実装ギャップ・未接地事項の一覧）

1. **3 話者アセンブリ**（§3）: **ローカル実装済みへ更新（2026-08-17）** —
   `s1_dataprep/assemble_run4.py`（新規ファイル方式・build_dataset.py 非接触）
   が D3→ritsu 合流（ファイル名無衝突の全数実測込み）+ 3 話者 raw 構成 +
   検証ゲート + **3 話者学習 config 生成**（R7 追加。`build_dataset.py`
   `build_config_yaml()` を read-only 再利用し `run4_config_datasets.yaml`
   [+ `.normalized.yaml`] を出力）を担う。使い方は同スクリプトの docstring
   とテストを参照。
2. **run 3 の実 `config.yaml`**（§4）: `datasets:`/`num_spk`/学習規模 3
   フィールドの節は `assemble_run4.py` の config 生成で解消済み（R7）。
   **残る要確認事項は LR/finetune/精度/勾配クリップの 4 項目のみ**
   （§4 のとおり自動生成の対象外）: AI-Drive
   `/s1_ritsu_pjs_acoustic_v1/run3/` 退避先の実体を確認し、
   `finetune_enabled`/`finetune_ckpt_path`/`pl_trainer_precision`/
   `optimizer_args.lr`/勾配クリッピングの正確なキー名・値を一次ソースから
   再確認したうえで、生成済み `run4_config_datasets.yaml` へ手動移植する
   （s1_record の文章記述からの引用のみで未接地）。
3. **run 4 の `finetune_ckpt_path`**（§4）: **裁定済み（Fable 2026-08-17）** —
   run 4 は run 3 checkpoint を継続せず、**run 3 レシピの完全再現**（スクラッチ
   開始 + 5K 節目で optimizer 新品の finetune 機構を再適用）とする。理由 =
   run 4 と run 3 の差分を「データのみ」に閉じ、D3/spk3 の効果帰属を清潔に保つ
   （S2 の単一要因教訓）。`finetune_ckpt_path` は run 4 自身の 5K checkpoint を指す。
4. **`gate_synth.py --speaker user` 対応**（§5②）: **実装済み（2026-08-17）**
   — `s1_gate/gate_synth_run4.py`（委譲ラッパー・gate_synth.py 非接触）。
5. **3 アンカー三角補間フォージスクリプト**（§5④）: **実装済み（2026-08-17）**
   — `s1_gate/forge_triangle.py`（新規・ブラインド規律機械化込み）。
6. **D3/ritsu 結合時のファイル名無衝突**（§3）: `assemble_run4.py` が結合時に
   全数比較で実測検査する（fail-closed）。本書の目視照合は参考情報へ格下げ。
