# DESIGN S4 — run 5（D3 話者分離・単一介入）+ RunPod API 無人実行

- 起草: 2026-08-18（Claude 設計）。前提 record =
  [`results_s3/s3_record_2026-08-17.md`](results_s3/s3_record_2026-08-17.md)
  （run 4 の効果帰属・評価器ギャップ・Q6/Q7）、前提 runbook =
  [`S3_RUN4_RUNBOOK.md`](S3_RUN4_RUNBOOK.md)（4 ゲート決定論環境規約・pin 台帳）、
  前提設計 = [`DESIGN_S3_backfill.md`](DESIGN_S3_backfill.md)
  （多介入同時投入の既定不可を run 4 実測で確定済み）
- 位置づけ: S 系列の第 4 設計書。run 5 = **VoiceGenesis 第 5 学習走行**の
  実験契約と、**クロー非経由の RunPod API 無人実行**（2026-08-17 User 決定
  事項）の初回実装契約を兼ねる

## 0. 裁定（本書の設計判断の要約）

1. **run 5 は単一介入 = D3 話者分離のみ**とする。user 宅録の投入前
   ラウドネス正規化と T3 追加収録は **run 6 へ送る**。
   根拠: run 4 は D3 混入 + user 追加の 2 介入同時投入で効果帰属が
   交絡した（s3_record §5・DESIGN_S3_backfill 改訂注記）。Q7（ノイズ混入の
   発生源分解）は ablation 専用走行を立てるより **ablation-by-sequence**
   （逐次単一介入の系列で自然に分解する）が安い:
   run 5 で D3 を分離してもノイズが残れば発生源は user 宅録側、消えれば
   D3 側という**強い方向性示唆**が得られる。どちらに転んでも Q7 は 1 走行で
   前進する。ただし run 5 は別の学習走行であり GPU 学習の確率性による
   走行間変動は分離できない — 判定は「実証」でなく方向性示唆として記録する
   （s3_record §5 の帰属限界規律を継承）
2. **④ 三角形補間バッチは run 5 checkpoint で生成し、VG-E1 第 0 世代を
   兼ねる**（run 4 checkpoint では生成しない — run 4 は品質劣化交絡を
   抱えたまま第 0 世代の基準にできない）
3. **実行はクロー非経由・RunPod API 無人ブートストラップ**（User 決定
   事項 2026-08-17）。Pod 作成時の起動スクリプト注入で
   clone → 4 ゲート → 再生成 → pin 照合 → 学習 → 退避 → 自動停止を無人完走する
4. 予算 cap: **$8**（run 4 と同額。run 4 実績 ≈$2.95 に対し余裕 2.7 倍）

## 1. 介入の定義（spk_id map v2）

### 1.1 変更内容

D3（合成教師データ）を ritsu の話者 ID から分離し、専用話者にする:

| speaker | spk_id v1 (run 4) | spk_id v2 (run 5) |
|---|---|---|
| ritsu | 0（runbook §4 表記: ritsu(D2+D3)=0） | 0（D2 のみ） |
| pjs | 1 | 1 |
| user | 2 | 2 |
| **d3synth** | —（ritsu へマージ） | **3（新設・合成教師）** |

- 既存話者の spk_id は**不変**（run 3/run 4 checkpoint との embed 互換の
  ため既存 ID の変更は恒久禁止 — assemble_run4.py §「spk_id は固定」の
  規律を継承し、**追加は末尾のみ**とする）
- d3synth は「**合成教師**」: 共有デコーダ経由の音素矯正効果（run 4 で
  日本語破綻が全面改善した機構的標的）を保ちつつ、リツの声色分布から
  合成音のアーティファクトを退避する仮説（= Q6 の検証）
- データ内容は run 4 と**同一**（D3 wav 群・user 宅録・D2・pjs すべて
  run 4 pin のまま）。変わるのは**帰属ラベルと、その帰結としての
  `num_spk: 4`（spk_embed テーブルへの行 1 本追加）**。これを 1 変数と
  数え、run 4 との差分をこの 1 変数に閉じる
- **学習開始点 = スクラッチ**（run 4 裁定の継承 — runbook §8:
  run 3 レシピ完全再現 = スクラッチ開始 + 5K 節目で optimizer 新品の
  finetune 機構再適用。`finetune_ckpt_path` は run 5 自身の 5K checkpoint
  を指す）。run 4 checkpoint からの warm-start は**行わない** —
  num_spk 3→4 で spk_embed 形状が変わり、拡張ロードという追加介入を
  持ち込むため（単一介入原則に反する）

### 1.2 検証仮説（run 5 の主検証点）

- **Q6**: D3 を別話者に分離しても①（日本語破綻の改善）は保存されるか。
  保存されれば「共有デコーダ経由の矯正」仮説が強まり、消失すれば
  「ritsu 話者への直接混入が必要」と判明する — どちらでも設計知識になる
- **Q7**: run 4 比のノイズ・音量交絡が run 5 で消えるか。
  消える → D3 混入起因の方向性示唆（user 正規化の優先度を下げられる）。
  残る → user 宅録起因の方向性示唆（run 6 = user 正規化 + T3 の根拠が
  立つ）。いずれも走行間変動と交絡しうるため「実証」とは記録しない
  （§0-1 の帰属限界注記）

## 2. 必要コード変更（マシン非依存・事前実装）

すべて run 5 実行前にリポジトリへ入れる（PR 経由・レビュー対象）:

1. **`assemble_run4.py` の 4 話者化**: `SPK_IDS` v2
   （`{"ritsu": 0, "pjs": 1, "user": 2, "d3synth": 3}`）。
   `datasets:` 4 エントリ・`num_spk: 4`・生リスト形状検査
   （エントリ数=4・重複無し・期待順序）の追随。v1/v2 の切替フラグは
   作らない（run 5 以降 v2 のみ。過去 run の再現は git 履歴が担う）
2. **D3 分離 = D2/D3 マージサブシステムの撤去**（変更規模注意: 「配線
   変更」ではない）: 現行の `--d3-raw-dir` は D2(ritsu) への行連結マージ
   専用経路（ヘッダ接頭辞検査・`_write_merged_ritsu_csv` の CSV 連結・
   D2/D3 間 name/wav 衝突検査 — `_assemble_into()` 実装）。run 5 では
   このマージロジックを撤去し、ritsu は D2 のみを pjs/user と同型の
   「バイト単位コピー」経路で、`--d3-raw-dir` は d3synth の第 4 エントリ
   （同じくバイト単位コピー）として組み立てる。convert_d3 の
   `discover_pairs()` 単一ディレクトリ規約は不変・出力配置先のみ
   d3synth 専用 raw ディレクトリへ変更
3. **`refresh-config-pin` の 4 話者検証**: 生リスト検査の期待値を
   `SPK_IDS` v2 に追随（重複畳み込み黙認バグの再導入をテストで防止）
4. **`gate_synth_run4.py` の d3synth 対応**: `--speaker d3synth` で
   合成教師声そのものの立ちを聴けるようにする（ゲート判定材料の 1 系統
   追加。三角形 Identity 空間には入れない — §4）
5. テスト: 上記の追随テスト（既存 `tests/test_gate_synth_run4.py` 系の
   4 話者版・config 生リスト形状・pin 再正規化）

## 3. 実行契約（RunPod API 無人ブートストラップ）

### 3.1 方式

- **Pod 作成時に起動スクリプトを注入**し、SSH 対話なしで完走させる。
  クローの対話操作は使わない（決定事項）。稼働中の介入手段は持たない
  前提で設計する（fail-closed: 異常時は成果物を残して自己停止）
- ブートストラップの段階（runbook §2.2 の 4 ゲートを先頭に固定）:
  1. repo clone（run 5 実行用ブランチの pin コミットを checkout）
  2. **ゲート 1–4**（数値スタック版 pin / SIMD 受け入れ X86_V3 /
     silent no-op 検査 / cache 来歴）— 1 つでも fail なら学習に入らず
     ログを退避して自己停止
  3. 素材取得 + sha256 照合（§3.2）
  4. D3 再生成 → convert_d3（d3synth 配置）→ assemble（4 話者）→
     `refresh-config-pin` → assembly_manifest pin 照合
  5. 学習（run 4 と同一の学習規模フィールド・max_updates 40K・
     開始点 = §1.1 のスクラッチ裁定）
  6. 節目 checkpoint（5K/10K/20K/40K）+ config/辞書/log/TB の退避（§3.3）
  7. **自動停止**（成功・失敗いずれでも。Pod 放置課金を構造的に排除）
- **監視 = RunPod API ポーリング + Drive heartbeat**。RunPod API は
  Pod 内ファイルを読めないため、API ポーリングは Pod 生死・課金時間の
  確認に限定し、**進捗の正はステージ毎のマーカー + exit code ファイルを
  節目毎に Google Drive へ heartbeat push したもの**（§3.3 と同経路）と
  する。報告文でなく成果物で完了判定する（CLAUDE.md「完了判定は成果物で
  行う」の適用）

### 3.2 素材の搬入経路（無人取得可能な形に限定）

| 素材 | 経路 | 照合 |
|---|---|---|
| ffmpeg static n6.1.2 | BtbN 公開 URL（pin 済み） | tarball sha256 = 既存 pin |
| D2（波音リツ強連続音 Ver1.5.1） | 公開 URL curl（S1_GPU_RUNBOOK §2） | 既存 sha256 pin |
| D3 | **搬入しない — Pod 上でその場 render 生成**（入力 = 上記 D2 voicebank・ゲート 1–4 通過後 = §3.1 段階 4） | render 出力を run 4 の D3 pin と照合 |
| pjs（PJS corpus ver1.1） | Google Drive ファイル ID + `gdown`（S1_GPU_RUNBOOK §2。D2 と別機構 — gdown 依存をブートストラップに含める） | 既存 sha256 pin |
| user 宅録 | Google Drive 直リンク（`uc?export=download&id=` 形式・実証済み） | run 4 dataset pin |
| config/辞書 | **リポジトリ内へ格納**（run 5 から。Drive/AI-Drive 手渡しを廃止） | git 内容アドレス |
| vocoder（nsf_hifigan.onnx） | openvpi 公開 URL（run 4 と同一） | run 4 使用時の URL + sha256 は **repo docs 未転記**（クロー報告値のみ）。**run 5 起動前の pin 転記を必須の先行タスク**とする（§5 AC の同時転記対象） |

- user 宅録の Drive リンクは起動スクリプトへ**環境変数として注入**し、
  スクリプト本文（リポジトリにコミットされる）へは書かない

### 3.3 成果物の持ち帰り経路（設計判断）

- クロー引退により AI-Drive 経路は使えない。**候補: (a) Pod から
  Google Drive へ rclone/API push、(b) HuggingFace private repo、
  (c) 停止のみで Pod ディスクに残置し必要分だけ後日回収**
- **裁定: (a) Google Drive push を主経路**とする（User が既にダッシュ
  ボードで扱える・直リンク取得の実証済み・追加アカウント不要）。
  認証トークンは起動時環境変数で注入し、**成果物専用フォルダに権限を
  限定したスコープ**とする（Drive 全域トークンの注入は不可 — Pod 側
  侵害時の被害面を成果物フォルダに閉じる）。push 後に sha256 サイドカーを
  併置し、record への転記は wav pin と同形式で行う。
  (c) を保険として併用（push 失敗時も停止前にディスクへ完全退避）
- 判定材料 wav（run 5 ゲート・run 4/run 3 アンカー比較）は tar.gz +
  provenance.sha256 の既存様式を踏襲する

### 3.4 予算と停止条件

- cap **$8**。ポーリングで経過時間 × 単価を概算し、cap 80%（$6.4）到達で
  最寄りの節目 checkpoint 退避 → 停止を仕込む（起動スクリプト内の
  wall-clock 上限としても実装: 単価 $0.22/h なら 29h 上限 → 余裕を見て
  **24h でタイムアウト自己停止**）
- NaN 検知（5K 節目毎の loss スキャン）で即退避・停止（run 4 と同じ
  fail-closed 規律）

## 4. 判定材料とゲート（run 4 様式の継承 + ④の合流)

1. **①系**: 日本語破綻ゲート（さくら/うみ 各話者）— run 4 と同一文面・
   同一 seed。Q6 の判定材料
2. **②系**: user 第三の声 — A/B/C 同一 5 秒区間 + 区間単位 LUFS 完全一致
   方式（run 4 で確立した 4 段プロトコルの最終形を標準とする）
3. **③系**: run 3 / run 4 / run 5 の三世代アンカー比較（ノイズ・音量交絡の
   消長 = Q7 の判定材料）
4. **④ 三角形補間バッチ = VG-E1 第 0 世代**: run 5 の 40K checkpoint で
   ritsu/pjs/user 三角形の補間個体群を生成し、VG-E0 台帳の創始個体
   （`anchors_provenance` 凍結）へ接続する。**d3synth は三角形 Identity
   空間から除外**（合成教師は Identity ドナーではない — VG-E0 §1 の
   3 頂点凍結と整合）
- 耳判定は User。LRA 単独を品質 Gate にしない（s3_record §6 の評価器
  ギャップを継承。ノイズ・区間レベルの定性判定を必須併置）

## 5. Acceptance Criteria（run 5 出口）

- [ ] §2 のコード変更が PR で main に入っている（4 話者 assemble +
  refresh-config-pin v2 + d3synth 配置 + テスト）
- [ ] ブートストラップスクリプトがリポジトリ入りし、ゲート 1–4 →
  素材照合 → 再生成 → pin 照合 → 学習 → 退避 → 自動停止を単一実行で表現
- [ ] 学習 40K 完走（NaN ゼロ）または fail-closed 停止の証跡
- [ ] 判定材料 ①②③④ の生成と User 耳判定の記録（s3 record 様式の
  s4 record 起草）
- [ ] 成果物 pin の record 転記（checkpoint sha / log / 生成コマンド対応表 /
  費用 — run 4 で未転記残となった 4 項目を **run 5 では同時転記で完了**させる）
- [ ] 費用 ≤ $8

## 6. Open Questions

- Q6 / Q7: §1.2 の通り run 5 の主検証点
- 新規 Q8: 無人ブートストラップの停止保証 — RunPod 側の障害で起動
  スクリプトが走らない・途中死する場合の課金露出（24h タイムアウトは
  スクリプト内実装のため、スクリプト自体が死ぬと効かない）。ポーリング側
  からの API stop を第 2 防衛線とするが、Claude セッション不在時間帯の
  露出は残る。cap $8 に対し最悪 24h ≈ $5.3 で予算内に収まることを
  設計上の受容とする
- 新規 Q9: 第 0 世代（④）の評価軸語彙 — VG-E1 で凍結（VG-E0 §8）。
  run 5 の耳判定逐語がその語彙選定の一次資料になる

## 7. run 6 への送り（本書で確定した延期事項）

- user 宅録の投入前ラウドネス正規化（convert_user 変換段・決定論）
- T3 追加収録（UC-018〜020・UC-009 再録）
- Q7 の判定結果次第で優先度を再裁定（run 5 でノイズが消えれば正規化の
  緊急度は下がる）
