# DESIGN S5 — run 6（user 宅録の投入前ラウドネス正規化・単一介入）

- 起草: 2026-08-19（Claude 設計・**User 承認 2026-08-19**「推奨手順を承認する」
  — run 6 先行 / run 7 = あみたろ教師交代の順序込み）。前提 record =
  [`results_s4/s4_record_2026-08-19.md`](results_s4/s4_record_2026-08-19.md)
  （run 5 closeout: Q6 大部分保存・Q7 ノイズ = D3 混入起因・② 3/3 別人の
  留保付き成立・④ founding 3 体）、前提 runbook =
  [`S4_RUN5_RUNBOOK.md`](S4_RUN5_RUNBOOK.md)（無人ブートストラップ実走済み +
  2026-08-19 強化: phase 別 namespace・train log・実行 manifest・資産化ループ）
- 位置づけ: S 系列の第 5 設計書。run 6 = **VoiceGenesis 第 6 学習走行**の
  実験契約。DX（run 7 = あみたろ教師交代）の直前走行として、耳判定を汚し
  続けてきた音量交絡を根治する

## 0. 裁定（本書で凍結する設計判断）

1. **run 6 は単一介入 = user 宅録の投入前ラウドネス正規化のみ**。
   **T3 追加収録は含めない**（T3 は当初から任意カード — recording_kit
   cards.md「完全に任意・後回しで OK」。User 確認 2026-08-19: run 6 の
   律速にしない。発動判断は run 6 の再判定で不足が出た場合のみ）
2. 動機は run 5 の実測 3 点: (a) ②確定判定の留保の実体 = user 音源の
   時間方向ラウドネス揺れ（さくら合成 raw で区間 -22.0〜-31.0 vs 末尾
   -13.78 LUFS = **1 ファイル内 約 17 LU**）、(b) ④で user 0.70 セル
   （VG-S3-002）のみ「不安定」観測、(c) ③で「音量交絡はまだあるが改善アリ」
   （ノイズ側は D3 分離で解消済み）。**録音データの音量ムラがモデルに
   そのまま学習されている**という帰属で、入口（変換段）で直す
3. **run 7（あみたろ教師交代・D3 引退）より先行**する。理由: (a) 音量交絡は
   介入というより**全判定を汚す測定交絡**であり、先に消すと run 7 の教師
   効果判定がきれいな地面でできる、(b) run 6 は小改修 + 既存 bootstrap
   流用で即走れるのに対し run 7 は intake/変換系の新設を要する、(c) run 6
   走行中に run 7 準備を並行できるため逆順にしても速くならない
   （User 承認 2026-08-19。あみたろ = 教師役の確定裁定は
   [`DESIGN_DONOR_EXPANSION.md`](DESIGN_DONOR_EXPANSION.md) §0 の裁定追記）
4. 予算 cap: **$4**（run 5 実績 ≈$1.35 の約 3 倍。fail-closed 数回分を含む
   余裕。run 4/5 の cap $8 から実績に合わせて半減）

## 1. 介入の定義

### 1.1 変更内容

`convert_user.py` の変換段に**カード（UC-xxx）単位の統合ラウドネス正規化**を
追加する（run 5 とのデータ差分をこの 1 変数に閉じる）:

- **単位 = 出力 wav 1 本（= 学習アイテム 1 本 = カード 1 枚）**。カード内部の
  時間変動（T1 の 3 段強弱など**意図された**ダイナミクス）は保存する —
  消すのはカード間・話者間の水準差
- **方式 = BS.1770 系の統合ラウドネス実測 → 目標値への線形ゲイン**。
  非線形処理（リミッタ・コンプ・区間別ゲイン）は使わない（決定論と
  「録音の中身を変えない」原則。②の A/B/C 組成と同じ流儀）
- **true-peak ガード**: ゲイン適用後のピークが 0 dBFS を超える場合は
  クリップさせず fail-closed（そのカードを除外せずエラー停止 — 目標値の
  再裁定を要求する。黙ってゲインを頭打ちにして「正規化済み」を装わない）
- **目標値 = 既存 2 話者（ritsu/pjs）の学習セグメント統合ラウドネス分布の
  中央値**。数値は実装 PR で実測して凍結する（本書は手続きのみ凍結 —
  user だけを動かして他話者に合わせる。ritsu/pjs/D3 側は一切触らない）
- 実装配置は `convert_user.py` の 44.1kHz 出力生成の直後段（tier 別
  アラインメントの後・transcriptions.csv の時間配分には影響しない）。
  正規化の実測値（カード毎の pre/post LUFS・ゲイン dB・ピーク）は
  変換 manifest に全数記録する

### 1.2 検証仮説（run 6 の主検証点）

- **Q10**: 正規化で②の留保が解消するか — A/B/C 再判定（run 5 と同一様式）で
  「音量の手がかりで言い切れない」が消え、**無留保の「第三の声」確定**に
  到達するか
- **Q11**: ④で観測された user 寄りセルの不安定（VG-S3-002）は消えるか —
  発生源が「データの音量ムラ」なら消え、残れば別因（データ量・音域被覆 =
  T3 の出番）の方向性示唆
- 回帰確認: ①（日本語破綻）と③（ノイズ不在）が run 5 水準から劣化しない
  こと。①の局所退行 2 点（みわたす語尾「す」・d3synth さ→あ）は**本 run の
  介入対象外** — 消長は観測記録のみ（走行間変動の測定にもなる）とし、
  根治は run 7（教師交代）の検証点へ送る

### 1.3 変えないもの

spk_id map v2（4 話者）・D3 分離教師（現状維持・引退は run 7）・学習レシピ
（スクラッチ + 5K finetune 再適用・40K・bf16-mixed / lr 2e-4 / clip 1.0）・
ritsu/pjs/D3 データ・辞書。**開始点 = スクラッチ**（run 4/5 裁定の継承 —
warm-start は正規化効果の帰属を壊す）。

## 2. 必要コード変更（事前実装・PR 経由）

1. **`convert_user.py` の正規化段**（§1.1）+ 変換 manifest への実測記帳 +
   追随テスト（正規化の決定論・true-peak fail-closed・T1 カード内
   ダイナミクス保存）
2. **user 側 dataset pin の更新**: 正規化で user 出力 wav のバイトが変わる
   ため、**run 6 用 pin**（`run6_dataset_pins.json`）を実装 PR のローカル
   実行で生成・凍結する。構成 = user セクションのみ新実測 + d3 セクションは
   run 4 pin を参照継承（`run4_dataset_pins.json` が持つのは **d3/user の
   2 セクションのみ** — ritsu/pjs は dataset pin を持たず、素材 pin
   〔run5_material_pins〕+ assembly manifest 照合で担保される現行方式を
   そのまま継承する）
3. **bootstrap の run 6 対応**: `run5_bootstrap.py` の run 依存定数
   （RUN_ID / exp_name / pin ファイル参照）のパラメータ化 or run 6 派生。
   同 PR に **salvage 追加候補 (c)** を同梱する — 学習 exp dir の
   `spk_map.json` / `lang_map.json` / `dictionary-ja.txt`（ONNX export の
   必須入力。run 5 では未退避で判定材料生成時に一次記録から再作成を要した —
   s4_record §5.4）
4. 素材 pin: 変更なし（ffmpeg/D2/PJS/vocoder は run 5 と同一 pin）

## 3. 実行契約

- **runbook = S4_RUN5_RUNBOOK を run 6 差分で継承**（強化済み bootstrap:
  phase 別 namespace・train.log・run_execution_manifest・pip freeze 捕獲を
  初めてフル装備で実走する回になる）。preflight の前回 manifest 比較は
  run 5 の manifest（Drive 残存）が相手になる — repo_commit / user pin の
  差分が info で出るのが正常系
- 退避先 = 同じ Drive 成果物フォルダ（run 5 成果物と衝突しないよう、
  実装 PR で run 6 の退避 prefix を裁定する — 例: `run6/` 配下 or 別フォルダ。
  heartbeat の正・監視様式は run 5 と同一）
- 予算 cap $4・24h 自己停止・NaN/pin 不一致 fail-closed は全継承。
  **pip freeze から `requirements_run5_pod.lock` の PENDING を解消する**
  （runbook §4.1 の lock 完全化条件 — run 6 の副産物タスク）

## 4. 判定材料（run 5 様式の継承）

1. **② 再判定（主判定・Q10）**: run 6 checkpoint の user/ritsu/pjs さくらから
   run 5 と同一手順（同一区間 3 つ・統合 -22.5 LUFS 完全一致・連結 3 ファイル
   提示）で A/B/C セットを組成。**判定水準も同一**（各区間「別人 / 寄って
   いる / 判別不能」）— run 5 の「3/3 別人・留保付き」との比較が本 run の
   出口
2. **③ 三世代比較**: run 4 / run 5 / run 6 の位置づけで、run 5 との対比較を
   主とする（ノイズ不在の維持・音量の揺れの消長）
3. **① 回帰**: さくら/うみ × ritsu/pjs（+ d3synth 単独）— 局所退行 2 点の
   消長は観測記録
4. **④ は run 6 では生成しない** — founding は run 5 checkpoint 固定
   （VG-E0 台帳接続対象は VG-S3-001..003・s4_record §7）。世代を跨いだ
   再鍛造の要否は VG-E1 で裁定する

## 5. Acceptance Criteria（run 6 出口）— **全充足・closeout 2026-08-20**

出口記録の正本 = [`results_s5/s5_record_2026-08-20.md`](results_s5/s5_record_2026-08-20.md)

- [x] §2 のコード変更が PR で main に入っている（PR #284: 正規化 + pin +
  bootstrap run 6 対応 + salvage (c) + テスト）
- [x] 学習 40K 完走（**NaN ゼロ**・起動 1 回で fail-closed ゼロ完走）
- [x] 判定材料 ①②③ の生成と User 耳判定の記録（record §5.4 / §6.1）
- [x] Q10/Q11 の裁定記帳（**Q10 = ②無留保確定**・Q11 = ④非生成につき直接
  判定は未了だが「入口の水準正規化の射程外」で決着 — record §6.2/§6.3）
- [x] `requirements_run5_pod.lock` の PENDING 解消（gates/binarize 2 段の
  実測 freeze から。Pod 内に版の異なる 2 環境が共存する構造も明記）
- [x] 費用 ≤ $4（**実測 ≈$1.40**）

## 6. Open Questions

- Q10 / Q11: §1.2 の通り run 6 の主検証点 → **裁定済み**（record §6.2/§6.3。
  Q10 = 達成〔②無留保の「第三の声」確定〕/ Q11 = 音量の区間揺れは残存し、
  発生源はカード内ダイナミクスまたはモデル/曲側 = 入口正規化の射程外と確定。
  ④ user 寄りセル不安定の再評価は VG-E1 へ送り・T3 は任意のまま）
- Q12（送り・run 7 の主検証点）: 実録音教師（あみたろ）で①の破綻矯正効果は
  保存されるか + 局所退行 2 点は消えるか（DESIGN_DONOR_EXPANSION §3）

## 7. run 7 への送り（本書で確定した並行準備）

- run 6 走行中に並行可: あみたろコーパス intake（規約ページ逐語 pin +
  取得日時・台帳化）・ITA コーパス → DiffSinger 形式変換系の設計・50% 会計の
  manifest 配線・D3 引退処理の設計（intake 前提 = DX §2-2・D3 引退の受け皿 = DX §3・実装 AC = DX §6）
- T3 は引き続き任意のまま（Q11 が「データ量・音域被覆」側を示した場合のみ
  発動を再検討）
