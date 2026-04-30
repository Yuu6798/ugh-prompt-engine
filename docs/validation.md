# Validation Status

Current status: **PoC**.

This repository provides a deterministic local SVP/RPE pipeline. Q0
（検証基盤の確立, [`roadmap_goal1.md`](roadmap_goal1.md)）の一環として、
合成サイン波 5 曲に対する定量検証フレームワークを整備した。
スコア出力は引き続き development / comparison 用の診断ヒューリスティクで
あり、production 音楽品質の真値ラベルとして扱ってはならない。

## 1. Quantitative Validation (Q0-5 baseline, 2026-04-28)

### 1.1 方法論

- **検証セット**: `examples/sample_input/synth_*.wav` の 5 曲（決定論的に
  生成された合成サイン波。BPM / key / 拍子 / 構造境界が事前に既知）
- **真値**: `examples/sample_input/ground_truth.yaml`
- **比較ツール**: [`scripts/validate_against_truth.py`](../scripts/validate_against_truth.py)
  が `mir_eval` で BPM / key / section boundaries を比較し、time_signature は
  exact match、downbeat は ±0.35s window の hit-rate で比較
- **再現コマンド**:
  ```bash
  python scripts/validate_against_truth.py            # markdown
  python scripts/validate_against_truth.py --json     # JSON for downstream
  python scripts/validate_against_truth.py --check    # exit 1 if any threshold miss
  ```

### 1.2 Per-song results

| song_id | BPM est / ref / Δ | tempo p | key score | meter est / ref / conf | downbeat hit | seg F@0.5s | seg F@3s | check |
|---|---|---|---|---|---|---|---|---|
| synth_01_slow_pad_c_major | 123.05 / 60.00 / 63.05 | 0.00 | 1.00 | 4/4 / 4/4 / 0.71 | 1.00 | 0.60 | 0.80 | ❌ |
| synth_02_minor_pulse_a_minor | 89.10 / 90.00 / 0.90 | 1.00 | 1.00 | 4/4 / 4/4 / 1.00 | 0.44 | 0.36 | 0.73 | ❌ |
| synth_03_mid_groove_g_major | 123.05 / 120.00 / 3.05 | 1.00 | 1.00 | 4/4 / 4/4 / 0.58 | 1.00 | 0.36 | 0.73 | ✅ |
| synth_04_waltz_fsharp_minor | 136.00 / 140.00 / 4.00 | 1.00 | 1.00 | 3/4 / 3/4 / 1.00 | 1.00 | 0.36 | 0.73 | ✅ |
| synth_05_fast_bright_d_major | 172.27 / 170.00 / 2.27 | 1.00 | 1.00 | 4/4 / 4/4 / 0.68 | 1.00 | 0.36 | 0.73 | ✅ |

各列の意味:

- **BPM est / ref / Δ**: 推定 BPM / 真値 BPM / 絶対誤差
- **tempo p**: `mir_eval.tempo.detection` の p_score (`tol=0.08`)
- **key score**: `mir_eval.key.evaluate` の Weighted Score（1.0 = 完全一致、
  0.3 = relative key, 0.0 = unrelated）
- **meter est / ref / conf**: 推定拍子 / 真値拍子 / `time_signature_confidence`
- **downbeat hit**: `downbeats_sec` 真値に対する ±0.35s hit-rate
- **seg F@0.5s / F@3s**: `mir_eval.segment.detection` の F-measure
  （boundary tolerance window 0.5s および 3.0s）
- **check**: `--check` モードの thresholds
  (BPM<5, key>=0.5, time_signature exact match, downbeat hit>=0.8,
  segF3>=0.5)

### 1.3 集計

| 指標 | 値 | 備考 |
|---|---|---|
| BPM 平均絶対誤差（5 曲） | 14.65 | synth_01 の octave error 込み |
| BPM 平均絶対誤差（synth_01 除外） | 2.56 | 4 曲のみ |
| BPM 誤差 < 5 BPM の曲数 | 4/5 (80%) | synth_01 のみ未達 |
| Key 完全一致率 | 5/5 (100%) | Krumhansl-Kessler templates が synth に対し全勝 |
| Time signature 完全一致率 | 5/5 (100%) | Q1-2: 4/4 x4 + 3/4 x1 |
| Downbeat hit-rate ≥ 0.8 | 4/5 (80%) | Q2-1 fallback: synth_02 の phase drift のみ未達 |
| Section F@3s 平均 | 0.744 | 全曲が threshold 0.5 を上回る |
| `--check` 通過 | 3/5 (60%) | synth_01 BPM / synth_02 downbeat が未達 |

### 1.4 Q0 完了基準のチェック

[`roadmap_goal1.md`](roadmap_goal1.md) Q0 完了基準と現状:

| 基準 | 目標 | 現状 | 状態 |
|---|---|---|---|
| BPM 誤差 | < 5 BPM | 4/5 曲達成 | ❌（synth_01 未達） |
| Key 一致率 | > 60% | 5/5 = 100% | ✅ |
| snapshot CI | green | green (PR #12) | ✅ |

→ **Q0 の BPM 値検証は synth_01 の octave error が解消されるまで未完了**。
Q1-3 で confidence は再設計済みだが、BPM 推定値そのものの octave 補正は未着手。

### 1.5 既知の問題

- **synth_01 (60 BPM) の octave error**: librosa の beat tracking が slow
  tempo (60 BPM) に対し倍テンポ (123.05 BPM ≈ 2x) を返す既知挙動。
  Q1-3 では confidence を再設計したが、BPM 値の octave 補正は未実装。
  short-term workaround は `mir_eval.tempo.detection` の第 2 候補 tempo を `[gt, gt*2]`
  にして octave hit を partial-credit にする方法もあるが、
  本ベースラインでは **厳しく測る方針** を維持
- **Section detection が 0.5s window で低 F-measure**: 0.36 は section
  境界推定が ±0.5s 精度では弱いことを示す。3.0s window では 0.73 まで
  改善しており、**マクロ構造は捉えているが境界は粗い**。Q2 (時系列深化)
  の `madmom` downbeat 統合で改善余地
- **synth_02 downbeat phase drift**: 軽量 fallback は `librosa` beat grid 上の
  最強拍相を選ぶため、minor pulse fixture では chord/pulse reset と bar grid の
  ずれを十分に扱えず hit-rate 0.44 に落ちる。madmom または局所位相追跡で改善余地

### 1.6 Q1-4 baseline profile observation

`ground_truth.yaml` now records an explicit `baseline_profile` for each
synthetic sample. `validate_against_truth.py --json` reports a
`baseline_score` block for the selected profile, but this is an observed
heuristic score only; it is not included in `--check` thresholds and is not a
production quality label.
The table values below are snapshot values from the current deterministic
pipeline and should be regenerated when scoring logic changes.

| song_id | baseline_profile | rpe baseline score |
|---|---|---:|
| synth_01_slow_pad_c_major | acoustic | 0.54 |
| synth_02_minor_pulse_a_minor | pro | 0.72 |
| synth_03_mid_groove_g_major | loud_pop | 0.74 |
| synth_04_waltz_fsharp_minor | acoustic | 0.57 |
| synth_05_fast_bright_d_major | edm | 0.56 |

## 2. Coverage Matrix

Q0 完了で「定量的に検証済み」になった項目を ✅ で示す。

| Area | Status | What is currently checked |
|---|---|---|
| BPM extraction | ✅ Quantitatively validated (Q0-4) | `mir_eval.tempo.detection` against synth ground truth |
| Key detection | ✅ Quantitatively validated (Q0-4) | `mir_eval.key.evaluate` Weighted Score |
| Time signature detection | ✅ Quantitatively validated (Q1-2) | Exact match against synth ground truth (`4/4` x4, `3/4` x1); `6/8` unit-tested only |
| Downbeat detection | ✅ Partially validated (Q2-1 fallback) | `PhysicalRPE.downbeat_times` hit-rate against synth downbeats; 4/5 ≥ 0.8, madmom deferred |
| Section boundaries | ✅ Partially validated (Q0-4) | `mir_eval.segment.detection` F@0.5s / F@3s |
| Snapshot determinism | ✅ Verified (Q0-2/Q0-3) | 15 件の hash 比較 CI |
| Genre baseline scoring | Partially verified (Q1-4) | 4 profiles load and score deterministically; synth ground truth records explicit baseline_profile and validation JSON reports baseline_score; no genre-labeled validation corpus yet |
| RPE physical scores | Unverified | Heuristic proximity to static baseline |
| UGHer score | Unverified | Token / anchor / Delta-E heuristics |
| SVP YAML output | ✅ Deterministic | Stable hashes for same synthetic input |
| DomainProfile packaging | ✅ Verified | Local + packaged resource fallback tests |

## 3. Interpretation Rules

- `rpe_score` / `ugher_score` / `integrated_score` は production 音楽品質の
  真値ラベルではない
- `--baseline pro|loud_pop|acoustic|edm` は比較基準の切替であり、ジャンルや
  制作品質を自動判定するものではない
- 高スコア = 「実装ヒューリスティクに近い」であり「良い音楽」ではない
- パイプラインは固定環境下で決定論的だが、メトリック妥当性には検証
  データセットが必須
- 評価器として使う前に: BPM / key / 構造 / semantic preservation /
  human quality の真値コーパスを構築する

## 4. Next Validation Work

- **Q0 fix-up**: synth_01 BPM octave error の解消（Q1-3 と同期）
- **Q1**: LUFS / 拍子 / BPM 信頼度の業界標準準拠、ジャンル別 baseline と
  6/8 audio fixture の追加検討
- **Q1-4 follow-up**: genre-labeled validation corpus で `pro` / `loud_pop` /
  `acoustic` / `edm` baseline の妥当性を検証
- **Q2**: downbeat / chord / melody の時系列観測
- **CC0 実音源の追加**: 合成サイン波だけでは genre coverage 不足
- **`rpe_score` / `ugher_score` のキャリブレーション**: 人手ラベル付きの
  reference / candidate ペアで calibration

## 5. Regenerate this document

数値が陳腐化した（extractor 改修 / threshold 調整 / synth 追加など）場合:

```bash
python scripts/validate_against_truth.py --json > /tmp/v.json
# /tmp/v.json の値を上記 1.2 / 1.3 表に転記
```

将来的に validation.md の数値部分を `--json` から自動生成する仕組みを
入れる余地あり（Q0-fu または Q5 で扱う）。
