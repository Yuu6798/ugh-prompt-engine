# M3d 校正実測 — 事前登録（実測前凍結）

**日付:** 2026-08-07
**正本:** docs/DESIGN_M3_melody_comparator.md §6（本書は §6 の実行パラメータ具体化のみ。§6 と矛盾したら §6 が勝つ）
**状態:** ハーネス/レジストリ/テストは #233 でマージ済み。残 = 実測のみ。
**順序証明:** 本書 + `tests/fixtures/melody_bench/m3d_pairs_manifest.yaml`（98 対、
単一 manifest）の commit が実測開始前の事前登録点。tuning run → evaluate → 凍結
commit → holdout run の順序は git 履歴とハーネスの holdout ロック（凍結前は
holdout 行をスタブ化し音声を読まない）で証明する。

**実装時に確定した事項（実測開始前・本 commit 時点）:**
- 規模は §1.1 のフル規模（4 変形/clip）を採用。縮約規則は不使用（変形範囲 ±2..5 半音 /
  rate 0.87–1.12 の被覆を外挿なしで主張するため）
- pair の sha256 pin と material 区分はハーネス凍結スキーマ（pair 6 キー固定・未知キー拒否）
  により manifest 非同梱とし、sidecar `build/external_m3d/m3d_pairs_pins.json` + pair_id
  命名規則（`_real_` / `_synth_`）で保持する
- 変形 WAV の書き出しは PCM_24 subtype（FLOAT subtype は libsndfile が PEAK chunk に
  壁時計を埋めるためバイト決定論が壊れる — 実測で発見。librosa 変形自体は bit 決定論）
- 狙い撃ち negative（rhythm/interval）は spec 直記述の fixture 対（`m3d_synth_specs.yaml`）
- **manifest は単一ファイルのまま維持する**（Codex レビュー R2 対応・設計判定）:
  当初は real_voice/synthetic への 2 ファイル分割を検討したが、
  `run_melody_comparison._validate_manifest_composition`（tuning に狙い撃ち
  negative 必須・holdout に negative 必須、等）は単一 98 対 manifest を前提にした
  素材構成契約であり、分割後の real-only/synth-only manifest はいずれも単体では
  このローダを通らないことが実装検証で判明した（real-only は狙い撃ち negative が
  synth 専用のため tuning 狙い撃ち negative 0 件、synth-only は negative_cross を
  持たないため holdout negative 0 件）。ハーネスの構成契約を変えずに R2（Codex
  指摘: 全 positive 単一バケット・synth の not_comparable が real 由来の凍結提案
  まで巻き込む問題）へ対応するため、**素材別会計は `run_melody_comparison.py` の
  evaluate phase 側**（`_partition_pairs_by_material` / `material_accounting`）
  で行う——manifest は単一のまま、pair_id の `_real_`/`_synth_` マーカーで
  evaluator が real_voice（校正の唯一の入力）/ synthetic（診断専用。
  not_comparable は not_measured として正直会計するのみで凍結可否/holdout 判定に
  一切影響しない）を読み分ける。§1.3/§2 の別会計はこの機構で機械強制される
- sidecar（`build/external_m3d/m3d_pairs_pins.json`、非コミット）に manifest の
  sha256 を記録し、`--check-only` がスキーマ検証 + digest 照合（manifest 1 件 +
  全 WAV pin）を fail-closed で行う（Codex レビュー R1 対応。従来は sha256 を
  記録するのみで再照合していなかった）。限界の明記: 本 sidecar はビルド生成物で
  あり、順序証明の最終根拠は git 履歴 + ハーネスの holdout ロックのまま——本照合は
  事故的ドリフトを fail-closed 化する計器である
- 生成物一式（変形 WAV・manifest・pins sidecar）は staging ディレクトリ（out_dir
  と同一ファイルシステム上）へ全生成 → 全検証成功後に一括 atomic publish する
  （Codex レビュー R3 対応）。途中失敗時は既存の公開済みセットを無傷で残す保証を
  「再ビルド時」にも拡張した
- **（Codex レビュー第 2 ラウンド追補）** sidecar のスキーマを `m3d-pairs-pins/0.3`
  へ改版し、build 入力（`m2c_external_fixtures.yaml` / `m3d_synth_specs.yaml`）
  双方の sha256 を記録・`--check-only` で再照合する（従来は fixtures 側のみ・
  synth specs は無 pin だった）。両入力とも hash 計算とパースを同一バイト列から
  行う構造（TOCTOU 解消）へ変更した
- 生成（librosa/build_signal 呼び出し）を開始する前に、公開予定の全出力先
  （out_dir 配下の生成 WAV・manifest-out・pins-out）を resolve() し、出力同士の
  重複・出力と入力（fixtures yaml・synth specs yaml・vocadito WAV 全件）の衝突を
  fail-closed で拒否する
- アトミック公開の publish ループが成功した場合、退避しておいた `.prev`
  snapshot を全て削除する（失敗時のロールバック経路の挙動は不変更）
- `run_melody_comparison.py` の evaluate phase 側 `material_accounting.synthetic`
  に holdout split の synth pair 全件を pair_id → 行単位の状態
  （`locked_skipped`/`not_comparable`/`measured`+evidence/axes）で列挙する
  per-row 診断テーブルを追加した。holdout ロック中（凍結前）は evidence に一切
  触れず `locked_skipped` として列挙するのみで、既存の holdout ロック規律を
  厳守する。calibration verdict への影響はゼロ（診断専用）

## 0. 完走の定義（STATUS.md P2 キューの 5 手順）

1. pairs manifest 作成（vocadito positive 変形対 + negative_cross/rhythm/interval、tuning/holdout split）→ **実測前に commit（事前登録）**
2. `run_melody_comparison.py` run ×2（repeats、crepe_direct 経路）
3. evaluate → マージン表 + 凍結提案 + floor 候補
4. registry 凍結 commit（tuning 由来等値は evaluate が検証）
5. holdout 一度検証 → 軸別判定 doc + PR

## 1. 素材インベントリ（事前登録）

### 1.1 vocadito positive_transform（実声・校正の主系）
- clip 選定: m2c_external_fixtures.yaml の 40 clip から **tuning 12 / holdout 6**（計 18、clip 単位で排他）。
  選定規則は決定論: clip id 昇順に並べ、sha256(clip_id) の hex 昇順で先頭 12 を tuning、次の 6 を holdout
  （恣意的選定の余地を消す。実装時に規則ごと manifest builder に焼き込む）
- 変形（make_melody_pairs.make_variants 流用、librosa 決定論）: 各 clip につき
  - pitch: **+3 半音、−5 半音**（±2..5 の範囲内・両方向・非対称）
  - stretch: **rate 0.87、1.12**（±8..15% の範囲内・両方向）
  - → 4 positive 対 / clip（original vs variant）。tuning 48 対・holdout 24 対
- 抽出ファイル数: 18 original + 72 variant = 90 file × run2 = 180 crepe 起動（timing 実測で規模調整可。
  調整する場合は variant を pitch+3 / rate 0.87 の 2 種へ半減 = 90 起動。**調整判断は実測前に確定**）

### 1.2 negative_cross（実声・異曲対）
- tuning: tuning clip の original 同士を id 昇順で環状ペア（i, i+1）= 12 対
- holdout: 同規則 6 対
- 追加抽出コストゼロ（original を再利用）

### 1.3 合成素材（synthetic。M2 の S-direct=fail 実測を踏まえ**別会計**）
- 生成: `build_melody_bench.build_signal` を library 利用、M3d 専用 spec
  `tests/fixtures/melody_bench/m3d_synth_specs.yaml`（新規。凍結済み synthesis_specs.yaml は不変更）
- positive: 合成旋律 2 本（tuning 1 / holdout 1）× 移調 +3 / 変速 0.9 = 4 対
- **狙い撃ち negative**（軸単独弁別の診断）:
  - negative_rhythm: 同音程列・別 IOI（note_dur/gap を変えた spec 対）tuning 2 対
  - negative_interval: 同リズム・別音程列（phrases の音程だけ差し替え）tuning 2 対
- **フォールバック意味論（事前登録）**: 合成素材が M1 観測ゲートまたは crepe 抽出で
  not_comparable に落ちた場合、当該対は「not_measured」として正直会計し、
  **軸校正の成否判定は vocadito 系（1.1+1.2）のみで行う**。合成の狙い撃ち検証は
  診断情報（成立すれば軸弁別の傍証、落ちれば S-direct 帯の既知欠測）であり、
  校正成立のゲートには入れない。

## 2. 事前登録パラメータ（設計書 §6.2 の再確認 + 本実測の追加分）

- separation margin: **0.15**（M0 継承・registry 済み・緩和禁止）
- 変形範囲: ±2..5 半音 / rate 0.85–1.15。範囲外への外挿は主張しない
- repeats: **run ×2**、系列 sha256 pin 完全一致で軌跡レベル決定論を確立（M2d 残課題を閉じる）
- coverage floor: 現 registry の 0.5 は provisional_until_m3d。**tuning split から導出し holdout 前に凍結**
- evidence_thresholds: 軸別 {strong_min, none_max} を tuning マージン表から導出し holdout 前に凍結
- 順序証明: manifest commit → run(tuning) → evaluate → 凍結 commit → holdout run の
  git 履歴 + ハーネスの holdout ロックで機械的に証明
- **判定の別会計（G2 懸念への応答）**: マージン表・判定は material 別
  （vocadito=real_voice / synthetic）に分割して報告。校正の適用範囲宣言は
  「単離済み clean lead・実声」で束ね、合成帯は実測結果の通りに正直記載
  （M4 G2 の帯域語彙解像度是正の一次データになる）

## 3. 判定規約（設計書 §6.3 のまま）

- 校正成立軸 = calibrated axis として registry 凍結 → M4 experimental anchor 候補資格
- 落ちた軸は not-calibrated として除外（部分成立を許す）
- 全滅 = dated 記録して M4 へ進まない（melody トラック closeout 判断へ）

## 4. 実装物（Sonnet 委譲）

1. `scripts/build_m3d_pairs.py`（新規）: vocadito pin 照合 → 変形生成 → 合成生成 →
   pairs manifest（m3-comparison-pairs/0.1）出力。全ファイル sha256 pin。決定論
2. `tests/fixtures/melody_bench/m3d_synth_specs.yaml`（新規）
3. builder の高速テスト（fake 音声で manifest 構造・split 排他・決定論を検証。slow 非依存）

## 5. 成果物

- `docs/measurements/m3d_2026-08/`: run/evaluate/verdict JSON + README（M2c 流儀）
- registry 凍結 diff（coverage.floor 確定値 + evidence_thresholds.axes）
- `docs/m3d_calibration_record.md`（判定 doc: マージン表・軸別判定・material 別会計・
  適用範囲宣言・M4/L0c への引き継ぎ）+ CLAUDE.md 索引 1 行 + docs/README.md 1 行
- STATUS.md キュー消し込み

## 6. リスク

- crepe/TF がこの環境に入らない or vocadito 不達 → その時点で machine-dependent 部分を
  User/Codex へ切り出す報告（ハーネス手順は STATUS 記載の通り実行者非依存）
- crepe CPU 実行時間が過大 → §1.1 の縮約案（事前登録済みの半減規則のみ許可）
- 合成素材の抽出不能 → §1.3 フォールバック意味論で吸収（校正は vocadito 系で成立可能）
