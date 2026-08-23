# DESIGN VG-DET0 — run 7 決定論反復（run-level reproducibility の観測研究）

**Status: DRAFT（事前登録ドラフト・User 承認前。本書は実行を認可しない）**

- 対象負債: `voice_genesis/foundry/debt/debt_ledger.yaml` **VG-DEBT-001**（run7 の bit 一致再現性が未検査）
- 起草: 2026-08-23（Fable 設計）
- 実行形態: machine-dependent（RunPod GPU・有料 ≈$1.4/走行）。**実行は User の予算承認と注入物を要する**

## 0. 位置づけ（Run 8 の継続版ではない）

Run 8 は 2026-08-22 User 裁定で **CLOSED**（`results_s7/s7_run8_closeout.md` §3）。
同 closeout §5 は「継続が必要な観測は Run 8 の継続版ではなく**別の観測研究として
再事前登録する**」と定める。本書はその規定に従う**新 experiment ID = `VG-DET0`** の
事前登録であり、run 8-R / 8-B / 8-R2 のいずれでもない。

問いはひとつ: **同一契約で学習を再実行したとき、run 7 の checkpoint はバイト再現
するか**（run-level determinism）。これは s5_record §6.2 が「帰属の確定には同一契約の
反復走行（未実施）が要る」と自ら記録した未払い分であり、`DESIGN_S7_run8.md` §9-0 が
因果裁定の前提として要求したまま未実施に終わった測定である。

本研究はどの結果でも **Run 8 を再開しない**。H0–H5 の遡及裁定もしない。得られるのは
走行間ドリフトの実測（`causal_evidence_strength: descriptive` の観測記録）である。

## 1. 契約（凍結対象）

### 1-1. 入力 = run 7 の未改変データセット

- dataset pins: `voice_genesis/foundry/results_s3/run7_dataset_pins.json` を**逐語使用**
  （本書起草時点の sha256 = `6b9c9054ff4209b5364d46cbb1831a31400236b714840cf981de6ed0d1d63db3`）
- 起動時 assert（fail-closed・§9-0 の control manifest 規律と同型）:
  `digest(VG-DET0.dataset) == digest(run7.dataset)`
- 教師構成・spk_id map・正規化・素材 pin はすべて run 7 契約
  （`DESIGN_S6_run7.md` §2）の逐語継承。**新規の設計判断はゼロ**

### 1-2. 実行 = run7 プロファイルの命名 3 点のみ差し替え

`voice_genesis/foundry/scripts/run5_bootstrap.py` の `RUN_PROFILES` に新プロファイル
`vgdet0` を追加する。**run7 プロファイルとの許容差分は命名 3 点のみ**:

| フィールド | run7 | vgdet0 | 差分の理由 |
|---|---|---|---|
| `run_id` / `exp_a` / `exp_b` | `s6_run7` 系 | `vgdet0` 系 | 実験 ID の分離 |
| `remote_prefix` | `run7` | `vgdet0` | **run 7 成果物の上書き防止**（run 5 で実測した phase A/B 同名 push 衝突の教訓） |
| `prev_manifest_prefixes` | `("run7","run6")` | `("vgdet0","run7")` | resume/比較の探索順（学習入力ではない） |

`dataset_pins` / `assemble_profile` / `expected_spk_map` は run7 と**同一値**。
上記 3 点以外のコード差分は禁止。bootstrap は実行時の repo HEAD commit を記帳する。
予算 cap $4 / 24h 自己停止 / NaN・pin 不一致 fail-closed は run 7 から全継承。

### 1-3. 環境同等性ゲート（L1 帰属の前提・fail-closed）

学習環境が run 7 と同等でない場合、L1 の bit 不一致は**学習の非決定論に帰属できない**
（環境差が原因でありうる。Codex 2026-08-23 P1 指摘の採用）。次を凍結する:

- **run 7 側の記帳値**: GPU 型 = RTX 3090（COMMUNITY・`results_s6/s6_record_2026-08-20.md`
  §1）、成功した run 7 実行 manifest の repo HEAD pin =
  `7df3a5fe5e34129218d5f3f0cc33ce332eebfff3`（`results_s7/s7_provisioning_runbook.md`
  冒頭の正本）、学習側 numeric stack = run 7 実行 manifest の
  `environment_versions`（Drive 保全分。bootstrap が記帳する）
- **ゲート（環境項目）**: VG-DET0 の実行 manifest と run 7 記帳値を項目ごとに照合し、
  **全一致が確認できた場合のみ** L1 不一致を「学習の非決定論」候補として扱える。
  1 項目でも不一致・または run 7 側が未記帳の項目が学習に影響しうる場合、
  L1 不一致の判定は **`confounded`**（§3）とする
- **repo HEAD の扱い（宣言済みコード差分ゲート）**: §1-2 が vgdet0 プロファイルの
  追加コミットを要求する以上、VG-DET0 の HEAD は
  `7df3a5fe5e34129218d5f3f0cc33ce332eebfff3` と**一致しないのが正常**
  であり、HEAD の単純一致を環境項目に含めると `confounded` 以外へ到達できなくなる
  （Codex 2026-08-23 第 2 巡 P1 指摘の採用）。代わりに
  `git diff 7df3a5fe5e34129218d5f3f0cc33ce332eebfff3..<VG-DET0 HEAD>` が次のみで
  構成されることを実行前に機械確認する:
  (a) `RUN_PROFILES` への `vgdet0` エントリ追加（値の拘束は §1-2 表が正）、
  (b) 学習経路外のファイル（docs / VG-DET0 用 remeasure spec・runner 拡張 /
  results 系）の追加・変更。
  **学習経路のコード**（bootstrap の profile 表以外の本体・binarize・assemble・
  DiffSinger pin・学習設定）に差分がある場合はゲート不一致 = `confounded` とする
- **正直会計**: run 7 側で未記帳の環境項目（コンテナイメージ digest 等、実行
  manifest に無いもの）は「未制御」として列挙し、その存在自体が
  `bit_identical` 以外の裁定に限定を付す（未記帳項目は事後に同等と宣言しない）

## 2. 比較プロトコル（レベル別 — pin 意味論は `s7_reproducibility_finding.md` が正本）

`samples_sha256` は再 export を跨ぐと成立せず、`wav_sha256` は libsndfile PEAK
チャンクの書き出し時刻で同一セッション内でも汚染される（いずれも実測済み）。
したがって**裁定レベルと記帳のみのレベルを事前に分離する**:

| レベル | 比較 | 参照値 | 役割 |
|---|---|---|---|
| **L1（裁定）** | 40K checkpoint の sha256 | run7 pin `518df090a8154e61f28b529f731418f4f97d47c3b56d1326d354e6be4629fa93`（556,022,498 bytes・`results_s7/s7_0b_probe_spec.json` expansion.generations.run7） | **bit 一致の主判定** |
| L1b（参考） | 中間 checkpoint（5K/10K/20K）sha256 | Drive 保全分の pin があれば照合 | 分岐点の局在化（判定には使わない） |
| L2（記帳のみ） | acoustic ONNX sha256 | `results_s7/probe_0b_groups/run7_*.json` の `model_sha256.acoustic_onnx` | 非裁定 — ONNX シリアライズ差は既知（finding §1） |
| L3（記帳のみ） | fixed probe の samples_sha256 / wav_sha256 | 同上 `cells[].samples_sha256` / `wav_sha256` | 非裁定 — 完全性検査。wav は容器レベルで参考のみ |
| **L4（裁定・機能）** | TRF spec 1.2 voicing 3 軸の再測定 | **`debt/d4/d4_results_2026-08-22.json` の run7 4 群 144 セル記帳値（spec 1.2 で測定済みの正本）** | **\|Δ\| ≤ ε の機能再現判定**。spec は `results_s7/trf_measurement_spec_1_2.json` 凍結値・再チューニング禁止 |

L4 の基準値に `probe_0b_groups/run7_*.json` の軸値を**使わない**: 同ファイル群の
`measurement_spec.spec_version` は 1.0 であり、1.2 は voicing 検出器を変更している。
1.0 記帳値との比較は検出器版差を「ドリフト」として偽報告する経路になる
（Codex 2026-08-23 P1 指摘の採用）。probe_0b_groups は L2/L3 の sha 参照にのみ使う。

L4 のレンダ・測定経路は 8-0b probe と同一系。ただし `debt/d4/d4_runner.py` は
`--generation` を run5/6/7 に、群集合を `d4_remeasure_spec.json` の 10 群に
fail-closed で束縛しているため、**現行のままでは VG-DET0 の群を実行できない**
（Codex 2026-08-23 P1 指摘の採用）。実行前に **VG-DET0 専用 remeasure spec**
（`d4_remeasure_spec.json` と同型・vgdet0 4 群 144 セル・spec 1.2 と計器 pin を
逐語継承）を事前登録し、runner の generation / 群検証を**その spec へ束縛する
拡張**（検証の緩和ではなく束縛先の追加）を同一 PR で凍結する。

## 3. 裁定語彙（事前固定）

| 判定 | 条件 | 帰結 |
|---|---|---|
| `bit_identical` | L1 一致 | run-level determinism 成立。走行間 sigma_between = 0 が実証される。**k = 1 で打ち止め**（§9-0 の経済順序）。環境非同等でも一致は一致（ただし「この環境対でも一致した」以上に一般化しない） |
| `functionally_reproducible` | L1 不一致 かつ §1-3 ゲート全一致 かつ L4 全軸・全セル \|Δ\| ≤ ε | 学習は非決定論だが結論レベルでは再現。**この単独では VG-DEBT-001 は close しない**（close_condition は bit 一致 or sigma_between 推定つき k ≥ 2）。k = 2 の要否を User 裁定へ |
| `drifted` | L1 不一致 かつ §1-3 ゲート全一致 かつ L4 に \|Δ\| > ε の軸/セルあり | ドリフト幅の実測値を記帳。k = 2 の要否を User 裁定へ |
| `confounded` | L1 不一致 かつ §1-3 ゲート不一致（または学習に影響しうる未記帳項目あり） | **学習の非決定論に帰属しない**。環境差と学習非決定論を分離できないため、L4 の実測値のみ記帳し裁定は保留。環境を揃えた再走行の要否を User 裁定へ |

いずれの判定でも因果の断定語（「効いた」「転移した」）は使わない。
k = 2 実施時は `sigma_between` を推定し、close_condition の後段を充足させる。

## 4. evidence_required（台帳逐語 — 全件を新 experiment ID の成果物として pin）

1. VG-DET0 checkpoint sha256（L1 の実測値）
2. VG-DET0 acoustic ONNX sha256（L2）
3. VG-DET0 fixed probe wav sha256（L3。samples_sha256 も併記）
4. `digest(VG-DET0.dataset) == digest(run7.dataset)` の assert 通過記録

成果物は `results_s7/` 系の既存慣行（machine-readable JSON + 実行報告 md・
確定後はバイト不変保護）に従って収載する。

## 5. 実行条件と停止規則

- **BLOCKED_ON_USER**: RunPod 無人ブートストラップ（run 5–7 実績方式）。起動には
  User の予算承認（cap $4）と注入物が要る
- 事前登録の効力: 本書が User 承認を得て凍結された後にのみ実行できる。
  実行後の本書改訂は erratum 追記のみ（判定規則の事後変更禁止）
- fail-closed で 3 回起動失敗した場合は原因記録を残して User へ返す（run 5 実績の運用）

## 6. 言わないこと（主張上限）

- 本研究は run 7 の**学習パイプライン**の再現性を測る。モデル品質・破綻の有無・
  介入効果については何も言わない
- `bit_identical` でも「run 5/6 も決定論だった」とは言わない（世代ごとの環境差は未制御）
- L2/L3 の不一致は決定論の破れの証拠にしない（pin 意味論の既知の限界）
