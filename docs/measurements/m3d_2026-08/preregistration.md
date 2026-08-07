# M3d 校正実測 — 事前登録（実測前凍結）

**日付:** 2026-08-07
**正本:** docs/DESIGN_M3_melody_comparator.md §6（本書は §6 の実行パラメータ具体化のみ。§6 と矛盾したら §6 が勝つ）
**状態:** ハーネス/レジストリ/テストは #233 でマージ済み。残 = 実測のみ。
**順序証明:** 本書 + `tests/fixtures/melody_bench/m3d_pairs_manifest.yaml`（98 対）の commit が
実測開始前の事前登録点。tuning run → evaluate → 凍結 commit → holdout run の順序は
git 履歴とハーネスの holdout ロック（凍結前は holdout 行をスタブ化し音声を読まない）で証明する。

**実装時に確定した事項（実測開始前・本 commit 時点）:**
- 規模は §1.1 のフル規模（4 変形/clip）を採用。縮約規則は不使用（変形範囲 ±2..5 半音 /
  rate 0.87–1.12 の被覆を外挿なしで主張するため）
- pair の sha256 pin と material 区分はハーネス凍結スキーマ（pair 6 キー固定・未知キー拒否）
  により manifest 非同梱とし、sidecar `build/external_m3d/m3d_pairs_pins.json` + pair_id
  命名規則（`_real_` / `_synth_`）で保持する
- 変形 WAV の書き出しは PCM_24 subtype（FLOAT subtype は libsndfile が PEAK chunk に
  壁時計を埋めるためバイト決定論が壊れる — 実測で発見。librosa 変形自体は bit 決定論）
- 狙い撃ち negative（rhythm/interval）は spec 直記述の fixture 対（`m3d_synth_specs.yaml`）

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
