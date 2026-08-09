# M3d 校正実測 v2 — 再事前登録(実測前凍結)

**日付:** 2026-08-09
**正本:** docs/DESIGN_M3_melody_comparator.md §6(バー・判定規約は §6 が勝つ)+ 本書(v2 素材選定規則)
**v1 との関係:** `preregistration.md`(v1)の tuning 実測は evaluate が凍結提案を
`rejected_positive_not_comparable` で fail-closed 拒否した(記録 = 本ディレクトリ
README.md / verdict_tuning.json / notcomparable_diagnosis.json、commit af94398)。
**v1 の holdout は一度も unlock されておらず検証権は未消費**。v2 は User 承認
(2026-08-09、選択肢 (b) = M1 観測ゲート由来の素材スクリーニングを組み込んだ再事前登録)
に基づく新規登録であり、v1 の manifest・synth specs・記録は不変更で残す。

## 0. 変えないもの(v1 から全面継承)

- **separation margin 0.15(一方向・緩和禁止)**、軸別判定(部分成立可)、全滅時は dated 記録
- repeats **run×2** + 系列 sha256 pin 完全一致(bit 一致)
- coverage floor / evidence_thresholds は **tuning から導出し holdout 前に凍結**
- holdout は凍結後に**一度だけ**開く(結果によらず再試行禁止)
- pins 強制チェーン(builder 公開 sidecar → run preflight `--pins` 必須 → 凍結コピー照合 →
  evaluate の preflight 証跡要求)、PCM_24、atomic publish、material 別会計
  (real_voice=校正の唯一入力 / synthetic=診断専用)、pair_id マーカー規約
- 変形パラメータ: pitch **+3 / −5 半音**、stretch **rate 0.87 / 1.12**(4 変形/clip)
- M1 観測ゲートの凍結値(phrase_gap_sec=0.6 / min_phrase_count=2 / min_note_count=8 /
  min_voiced_coverage=0.30)と M3 レジストリの凍結スキーマ——**ゲート・比較器には一切手を入れない**

## 1. v1 不通過の原因と v2 の対処(素材選定規則のみ変更)

v1 の敗因は分離不能ではなく、**校正標本が観測ゲートを通らなかった**こと
(real tuning positive 46/48 が `phrase_count 1 < min 2`。診断 =
notcomparable_diagnosis.json: 素材特性 6 clip + crepe voicing false-alarm による
フレーズ融合 4 clip の複合、変形 b 側の追加劣化あり)。v1 の clip 選定は
sha256 順の盲選定で、「ゲートを通り得る素材か」の構造条件を課していなかった。

v2 は素材選定に**観測ゲートそのものによる事前スクリーニング**を導入する。
スクリーニングは観測可能性(sufficient/insufficient)のみを参照し、
**類似値・マージンは一切計算も参照もしない**(M1 の設計原理「比較の前に観測可否を
問う」の選定への適用)。

**透明性(汚染の明示と防御):** v1 tuning で vocadito_17/22 の類似値が観測済みである。
v2 の選定規則は「ゲート sufficient の全数」を機械的に採るため、特定 clip を優遇する
自由度を持たない(17/22 もゲートを通る限り他と同資格で入るのみ)。規則に clip 固有の
項・類似値由来の項は存在しない。

## 2. スクリーニング手順(S1/S2・本書 commit 後に実測)

- **S1**: `m2c_external_fixtures.yaml` の vocadito 40 clip 全数(pin 照合済み原音)に
  M1 観測ゲート(route=crepe_direct、v1 run と同一の M1 registry・同一抽出器 pin)を適用
- **S2**: S1 で sufficient の各 clip について 4 変形(§0 のパラメータ、builder と同一の
  librosa 決定論変形)を生成し、各変形にも同ゲートを適用
- **survivor 定義**: 原音 + 4 変形の**全 5 本が sufficient** の clip
- スクリーニング記録(clip×side 別のゲート結果・audio sha256・registry sha・抽出器
  provenance)を JSON で保存し、manifest v2 と同じ commit で
  `docs/measurements/m3d_2026-08/screening_v2.json` として収載する
- スクリーニングは v2 における **M1-real 系の初の実素材大規模観測記録**を兼ねる
  (Suno stem 経路の M1-real Go バーは素材不在のため引き続き別タスク。registry の
  real_vocal_* pin に対応する音源は本環境に無い)

## 3. 選定・分割規則(決定論・スクリーニング結果に機械適用)

- survivor を `sha256(clip_id)` の hex 昇順に整列(v1 と同じ反恣意性規則)
- **N ≥ 18**: 先頭 12 = tuning / 次の 6 = holdout(v1 と同規模)
- **N < 18**: 先頭 ceil(2N/3) = tuning / 次の floor(N/3) = holdout
- **停止条件**: tuning clip < 6 または holdout clip < 3 → 実測に進まず User 報告
  (バー緩和・ゲート緩和による救済はしない)
- pair 構成(v1 と同型): positive_transform 4 対/clip、negative_cross は split 内
  id 昇順の環状ペア、synth は §4
- 起動数会計は pair×2 規則(v1 §1.4)で survivor 確定後に機械算出し、manifest v2
  commit に記載する

## 4. 合成素材 v2(診断専用・会計不変)

v1 synth 対は全数が観測ゲート不通過(note_count 5 < 8 等)で診断が構造的に成立しな
かった。v2 は `m3d_synth_specs_v2.yaml`(新規。v1 specs は不変更)で**ゲート通過可能な
構造**(2 フレーズ以上・フレーズ間ギャップ ≥ 1.2 s・総 note 数 ≥ 10・voiced coverage
≥ 0.30 を spec 構造上満たす)に再設計する。「診断が成立しうる fixture であること」
(ゲート通過可能性 + rhythm/interval 負例の弁別可能性)は v1 K2 対応と同型の機械
アサートで実測前に保証する。役割は v1 と同一: **診断専用**であり、not_comparable は
not_measured として正直会計、凍結可否・holdout 判定に不影響。

## 5. 成果物パス(v1 と衝突させない)

- manifest: `tests/fixtures/melody_bench/m3d_pairs_manifest_v2.yaml`
- synth specs: `tests/fixtures/melody_bench/m3d_synth_specs_v2.yaml`
- pins sidecar: `build/external_m3d/m3d_pairs_pins_v2.json`(非 commit)
- 変形 WAV: `build/external_m3d/m3d_pairs_v2/`
- スクリーニング記録: `docs/measurements/m3d_2026-08/screening_v2.json`(commit)
- run/evaluate/verdict: `docs/measurements/m3d_2026-08/`(run1_v2.json 等、M2c 流儀)

## 6. 順序証明

commit(本書) → スクリーニング実測(S1/S2) → commit(screening_v2.json +
manifest v2 + synth specs v2 + builder/screener コード) → run×2(pins preflight) →
evaluate → 凍結 commit → holdout 一度 → 判定 doc。git 履歴 + ハーネスの holdout
ロック(v1 と同一機構・v2 manifest に適用)で機械的に証明する。

## 7. 停止条件(v1 継承 + v2 追加)

- §3 の survivor 不足
- run×2 bit 不一致 / pin 検証失敗 / margin < 0.15(軸全滅時は closeout 判断へ)/
  holdout と tuning の乖離が異常に大きい場合
- いずれも即 User 報告・自動続行禁止
