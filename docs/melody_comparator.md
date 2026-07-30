# 旋律比較器 M3 — 軸別類似・校正ハーネス

状態: M3a/M3b/M3c 実装完了（表現・整列・比較器）。M3d は**校正ハーネス
（run/evaluate 二相）の機構のみ**実装済みで、slow-lane 実測（実 crepe + vocadito
コーパスによる tuning/holdout 校正）は**本セッションでは未実施**（dated で
追記予定）。
起点: 設計書 [`DESIGN_M3_melody_comparator.md`](DESIGN_M3_melody_comparator.md)
（発行元: Cowork）。設計拘束の根: [`m2_error_model.md`](m2_error_model.md)（M2d・
第 1 入力）+ User 決裁 2026-07-30（`DESIGN_M3_melody_comparator.md` §0 / M3-0 で
dated 記録）。

M1「聞こえるか」→ M2「聞き間違えていないか」→ **M3「同じ旋律か」**。M3 は
移調・テンポ変化に**不変**で、抽出誤差（M2d 実測）を**許容**し、編曲差分には
**反応**する、軸別の類似判定を返す決定論比較器である。単一の同一性%は恒久的に
出さない。

## 1. モジュール構成

| 資産 | 場所 | 役割 |
|---|---|---|
| 表現(M3a) | `src/svp_rpe/melody/representation.py` | `MelodyNote` 列 → 正規化系列（`MelodySequences`: 音程列・輪郭列・IOI 比列・音長比列）+ `m3_comparison_registry.yaml` のロード（未知/欠落キー fail-fast） |
| 整列(M3b) | `src/svp_rpe/melody/alignment.py` | ノート層アフィンギャップ NW（Gotoh・`align_intervals`）+ フレーズ層 NW（`align_melodies`）+ 被覆信号（`AlignmentCoverage`） |
| 比較器(M3c) | `src/svp_rpe/melody/comparison.py` | `compare_melodies`: M1 観測ゲート → 表現 → 整列 → 被覆下限ゲート → 軸類似 → オクターブ折返しガード → evidence 導出 → `MelodyComparisonReport` |
| 校正ハーネス(M3d) | `scripts/run_melody_comparison.py` | pairs manifest から run report を作る（run phase）+ 複数 report からマージン表 / holdout ロックを導出する（evaluate phase） |
| 凍結レジストリ | `tests/fixtures/melody_bench/m3_comparison_registry.yaml` | M3 の事前登録定数（`registry.yaml` / `m2_accuracy_bars.yaml` とは別ファイル・M1/M2 の凍結値には一切触れない） |

`observability.py` / `accuracy.py` / `registry.yaml` / `m2_accuracy_bars.yaml` の
凍結値は M3 の全モジュールから**一切変更しない**（呼ぶだけ）。

## 2. `MelodySequences`（M3a の正規化系列）

`build_sequences(notes, config)` が返す 6 系列（いずれも移調・変速に対して以下の
性質を持つ）:

| フィールド | 定義 | 不変性 |
|---|---|---|
| `pitch_semitones` | `floor(pitch_midi + 0.5)`（一側タイブレーク・移調同変。レビュー対応 2026-07-30 第 11 ラウンド: 偶数丸め `round()` はタイブレークが移調で不変でなかったため変更） | なし（移調で変わる） |
| `intervals_raw` | 隣接ノートの半音差 | 移調不変 |
| `intervals_folded` | `((d + 6) mod 12) - 6` | 移調不変・オクターブ折返し |
| `contour` | raw 音程の粗ビン（`flat`/`up_small`/`up_large`/`down_small`/`down_large`、境界は `contour_small_max_semitones`） | 移調不変 |
| `ioi_log2_ratios` | `log2(IOI_i / IOI_{i-1})` を `ioi_ratio_log2_step` 刻みで丸め | テンポ不変 |
| `duration_log2_ratios` | `log2(dur_{i+1} / dur_i)` を `duration_ratio_log2_step` 刻みで丸め | テンポ不変 |

`split_phrases(notes, phrase_gap_sec)` は `observability._phrase_count` と同じ
ギャップ規則でフレーズ分割する（同期テストで enforce）。`sequence_sha256` は
系列内容の決定論 hash（provenance pin 用）。

## 3. 整列（M3b）

- ノート層: `intervals_folded` 上のアフィンギャップ Needleman–Wunsch（Gotoh 3
  行列）。装飾音の挿入・削除はギャップとして吸収する。tie-break は
  `traceback_preference`（既定 `[diag, up, left]`）で決定論的に解決。
- フレーズ層: `phrase_gap_sec`（M1 registry と同値）でフレーズ分割し、フレーズ間
  類似 `matched_columns / max(len_a, len_b)` を substitution スコアに、
  `phrase_gap_score` を（ペナルティでなく）加点ギャップとして線形 NW を行う。
  未対応フレーズは被覆の欠損として記録する（ペナルティにしない）。
- 被覆信号（`AlignmentCoverage`）: `aligned_note_fraction_a/b`（整列ノート数 /
  全ノート数）、`phrase_coverage_a/b`（対応フレーズ数 / 全フレーズ数）。会計整合
  （aligned + unaligned = 全数）はテストで担保。

## 4. `MelodyComparisonReport`（M3c）

```text
axes: {"contour": float|None, "interval": float|None, "rhythm": float|None}
coverage: {aligned_note_fraction_a, _b, phrase_coverage_a, _b}
octave_artifact_suspected: bool
evidence: "strong" | "weak" | "none" | "not_comparable"
axis_evidence: {"contour": str, "interval": str, "rhythm": str}   # 軸別 "strong"|"weak"|"none"|"uncalibrated"
reasons: [str]
provenance: {route_a, route_b, sequence_sha256_a/b, aligned_pair_count, ...}
```

`compare_melodies(observation_a, observation_b, *, observability_thresholds,
config, provenance_extra=None)` の処理順:

1. **M1 観測ゲート**（`assess_observability`）: どちらかが insufficient なら
   `evidence="not_comparable"`（axes 全 `None`・理由に `observation_gate_
   insufficient_a/b` を記録）。
2. ノート導出（ゲート判定と同じ分岐）→ M3a 表現 → M3b 整列。
3. **被覆下限ゲート**: `min(aligned_note_fraction_a, _b) < coverage.floor` なら
   `not_comparable(insufficient_overlap)`（被覆自体は観測事実として出す）。
4. 軸類似（整列カラム上のみ）:
   - `interval` = 折返し量子化音程の一致率
   - `contour` = raw 音程由来 contour ビンの一致率
   - `rhythm` = duration 比較（全整列カラム）+ IOI 比較（直前カラムも連続整列の
     カラムのみ）をプールした一致率
5. **オクターブ折返しガード**: 折返し一致率 − 生一致率が
   `octave_artifact_divergence` を超えたら `octave_artifact_suspected=True`
   （判定は折返し側=`interval` 軸を用い、乖離は reason/provenance に残す）。
6. **evidence 導出**（機械導出のみ）— 次節。
7. provenance: `route_a/b`・`sequence_sha256_a/b`・`aligned_pair_count`・
   フレーズ対応数など（`provenance_extra` で registry hash 等を上書きマージ可）。

### evidence の意味論

- **総合同一性スコアは恒久的に作らない**。軸間重み付き平均も禁止
  （`docs/DESIGN_M3_melody_comparator.md` §8）。`evidence` は軸別 verdict の
  **最強値**（strong > weak > none）であって加重平均ではない。
- `evidence_thresholds.status == "uncalibrated"`（現在の凍結値）の間は、軸の生値
  （`axes`）は報告するが `axis_evidence` は全軸 `"uncalibrated"`、`evidence` は
  `"none"` を返す（理由 `evidence_thresholds_uncalibrated`）。**未校正の計器は
  証拠を出せない**——これは「正直な沈黙」であり、値が低いことを意味しない
  （自己比較でも axes は全て 1.0 になりうる）。
- 校正後（M3d の tuning split で `axes.<axis>.strong_min` / `none_max` を導出し
  holdout 前に凍結した後）は、軸別に `sim >= strong_min → "strong"` /
  `sim <= none_max → "none"` / それ以外 `"weak"` を機械導出する。軸が割れたら
  そのまま `axis_evidence` に残し、`reasons` に `axes_disagree(...)` を積む
  （隠さない）。

## 5. 凍結値表（`m3_comparison_registry.yaml`、schema `m3-comparison/0.1`）

| 節 | キー | 値 | 意味 |
|---|---|---|---|
| representation | `pitch_quantization_semitones` | 1 | 半音量子化（M2d 打ち切り統計拘束によりこれ未満へ狭めない） |
| representation | `contour_small_max_semitones` | 2 | 輪郭ビンの小/大境界 |
| representation | `ioi_ratio_log2_step` / `duration_ratio_log2_step` | 0.25 | IOI比・音長比の log2 丸め刻み |
| representation | `chroma_fold_semitones` | 12 | オクターブ折返し周期 |
| representation | `octave_artifact_divergence` | 0.10 | 折返し−生の乖離フラグ閾値 |
| alignment | `match_score` / `mismatch_score` / `gap_open` / `gap_extend` | 1.0 / -1.0 / -1.0 / -0.5 | アフィンギャップ NW コスト |
| alignment | `traceback_preference` | `[diag, up, left]` | 同点 tie-break の決定論順序 |
| alignment | `phrase_gap_sec` | 0.6 | M1 registry と同値（同期テストで enforce・独自変更禁止） |
| alignment | `phrase_gap_score` | 0.25 | フレーズ層 NW のギャップ加点 |
| coverage | `floor` | 0.5（`floor_status: provisional_until_m3d`） | 被覆下限ゲート。M3d tuning で導出し holdout 前に凍結予定 |
| evidence_thresholds | `status` | `uncalibrated` | M3d で軸別に凍結するまで evidence は `"none"` |
| separation_margin | `min_same_minus_cross_margin` | 0.15 | M0 registry `separation_gate` から継承（新値を発明しない） |

これらの値は M3a/M3b/M3c いずれのモジュールからも変更しない。値の変更は M3d の
tuning→凍結手続き（本ドキュメントの校正状態節）でのみ許される。

## 6. 校正ハーネス（M3d・`scripts/run_melody_comparison.py`）

standalone script（`svprpe` サブコマンド化しない）。`run_melody_accuracy.py`
（M2）の 7000 行級 anti-tamper 要塞は**複製しない**——本ハーネスが踏襲するのは
以下 4 点のみ:

1. **atomic write**（temp file → `os.replace`）
2. **レジストリ sha256 pin**（`load_m3_registry` / M1 registry の生バイト hash）
3. **route_runner 注入 seam**: `run_comparison(route_runner=...)` は
   `(audio_path) -> (MelodyObservation, provenance dict)` を受け取る抽出器
   非依存インターフェース。既定は実抽出器（`observe_via_route_with_provenance`
   を clear_lead 経路にバインドしたもの）。注入時は report に
   `route_runner_injected: true` を刻み、`evaluate_comparison` はそれを検出して
   **calibration verdict（マージン表・凍結提案）の発行を拒否**する
   （`scripts/run_melody_accuracy.py` の `_require_publishable_runs` と同じ規律）
4. **protected-path**: `--out` が pairs manifest / registry / `--evaluate` 入力と
   同じパスを指していたら fail-closed

### run phase

```bash
python scripts/run_melody_comparison.py --pairs pairs.yaml --out run1.json
```

pairs manifest（YAML, schema `m3-comparison-pairs/0.1`）の 1 行:
`pair_id` / `kind`（`positive_transform` | `negative_cross` | `negative_rhythm` |
`negative_interval`）/ `split`（`tuning` | `holdout`）/ `audio_a` / `audio_b` /
`expected`（`same` | `different`）。未知/欠落キー・重複 `pair_id`・無効な
enum 値はすべて fail-closed。

### evaluate phase

```bash
python scripts/run_melody_comparison.py --evaluate run1.json run2.json --out verdict.json
```

1. **sequence hash pin**: repeats（n>=2）間で `sequence_sha256_a/b` と `axes` が
   pair 単位で bit 一致することを確認する（軌跡レベル決定論の実測確立・M2d
   残課題を閉じる測定）。不一致は `ValueError`（fail）。
2. **route_runner_injected 拒否**: いずれかの report が注入 runner 由来なら
   `calibration_verdict_status: "rejected_route_runner_injected"` を返し、
   マージン表・凍結提案を一切出さない。
3. **tuning-only マージン表**: `split == "tuning"` の pair のみを用い、軸別に
   `positive_min - negative_max`（`expected` で positive/negative を分ける）を
   算出し、`separation_margin.min_same_minus_cross_margin`（0.15）以上なら
   `calibrated_candidate: true` として `freeze_proposal.<axis> = {strong_min:
   positive_min, none_max: negative_max}` を emit する（**凍結は人間の registry
   更新 commit**であり、本ハーネスは提案を出すのみ）。
4. **holdout ロック**: `evidence_thresholds.status != "frozen"` の間は
   `holdout_locked_until_frozen: true` + `holdout_pair_ids_skipped` を記録し、
   holdout split の pair をマージン計算に一切混ぜない（tuning→凍結→holdout の
   順序を記録で証明可能にする）。
5. **coverage floor 候補**: tuning split の positive pair の
   `aligned_note_fraction_a/b` 分布から `candidate`（最小値）/ `min` / `max` /
   `mean` を emit する（凍結は別途 registry 更新）。

## 7. 適用帯域（clean lead 限定・User 決裁 2026-07-30）

本設計の適用は**単離済み clean lead**（合成単旋律・単独歌唱）で校正された範囲に
限る。demucs vocals stem 帯は M2 誤差モデルの被覆外（V-fullstack 未測定）のため
適用外、**フルミックス直は禁止**のまま
（[`DESIGN_M2_extraction_accuracy.md`](DESIGN_M2_extraction_accuracy.md) §7 改訂 /
[`m2_error_model.md`](m2_error_model.md) 参照）。校正ハーネスの既定 `route_runner`
も `clear_lead` 経路のみを解決対象にする（`_resolve_route` が構造的に強制）。

## 8. 校正状態

- **`evidence_thresholds.status = "uncalibrated"`**（本ドキュメント作成時点の
  凍結値）。軸別閾値（`strong_min` / `none_max`）は**未導出**。
- **M3d slow-lane 実測は未実施**（実 crepe + vocadito コーパスによる
  tuning/holdout 校正・positive/negative pair 生成は本セッションの範囲外。
  実施後、本節へ dated で追記する）。
- `coverage.floor`（0.5）は `floor_status: provisional_until_m3d` のまま
  ——実測後に holdout 前で凍結する。
- M3d ハーネス自体（run/evaluate 二相・hash pin・holdout ロック機構）は fake
  route_runner によるメカニズムテストのみで検証済み。実抽出器での slow-lane
  run は未実行。

## 9. やってはいけないこと（`DESIGN_M3_melody_comparator.md` §8 転記）

- 総合同一性スコアの導入（恒久禁止）。軸間重み付き平均も同罪。
- ±50 cent より細かい音高許容の導入（再実測なしに）。
- 抽出器の voicing を信頼した判定・VFA の推論時重み利用。
- holdout を見てからの閾値・コスト調整。マージン 0.15 の緩和。
- stem 帯・フルミックス帯への適用や外挿（dated 実測まで）。
- insufficient 観測同士の比較で類似値を出す（`not_comparable` を返す）。
- melodia の混入（#222 裁定前）。M1/M2 凍結値の変更。
