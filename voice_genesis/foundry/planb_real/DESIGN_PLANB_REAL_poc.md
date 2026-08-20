# DESIGN Real-Corpus PoC — Ritsu Identity × PJS Performance

- 起草・第 1 走行: 2026-08-20（User 指示書 `Real-Corpus PoC Execution Instruction v1.0` の機械化）
- 前段: surrogate PoC = [`../planb/DESIGN_PLANB_poc.md`](../planb/DESIGN_PLANB_poc.md)
  （**結果は別 ledger。上書きしない** = instruction §9）
- 実測記録: [`REAL_CORPUS_POC_RECORD.md`](REAL_CORPUS_POC_RECORD.md)

---

## 0. 本トラックが答える問い

> Ritsu 由来の音響 Identity を主体として維持しながら、
> PJS 実歌唱から抽出した Performance 成分を交換できるか？

完全な disentanglement は要求しない。成功主張の上限は
**「部分的に分離された実歌唱形質を、Identity とは別経路で交換可能」**まで（instruction §0）。

## 1. 実行原則の実装対応

| instruction §0 の原則 | 実装 |
|---|---|
| 1. fail-closed | `pr_status.StopRule` + 各ゲートが `BLOCKED` を返し exit code 非ゼロ |
| 2. canonical source read-only | 展開先は scratchpad。コードは読み取りのみ |
| 3. raw corpus を repo へ入れない | `pr_manifest.gate_material` が資材の repo 内配置を**機械拒否** |
| 4. provenance を SHA-256 で固定 | `source_manifest.json`（archive / 規約文書）+ 各 rung の**実 wav バイト列 sha256** |
| 5. テクスチャを Performance 経路で混ぜない | `RealPerformanceTrack` は 1 次元のみ（型で拒否）+ 合成器アクセスの tripwire |
| 6. 成分を個別 ON/OFF | `PerformanceToggles(f0, duration, energy, release)` |
| 7. surrogate と real を別結果に | `planb/results_pb0/` と `planb_real/results/` を分離 |
| 8. 既存 TRF primary axis を結果で変えない | `pr_gates.REGISTERED_PRIMARY_AXIS = "nasal_gain_db"` 固定 |
| 9. `nasal_gain_shape_db` は exploratory | ゲート計算に一切入れず、evidence に併記のみ |
| 10. 耳と機械を上書き統合しない | `gate_ear` は回答ファイルが無い限り `BLOCKED` |

## 2. 分離の実装境界

```text
Identity Track   (Ritsu 実歌唱)   : spectral envelope / aperiodicity / 音韻テクスチャ
Performance Track (PJS 実歌唱)    : timing / pitch / dynamics / release  ← **1 次元のみ**
```

Performance の型（instruction §4 の語彙をそのままフィールド名にしている）:

| 群 | フィールド |
|---|---|
| timing | `note_duration_s` `phoneme_duration_s` `consonant_duration_s` `vowel_duration_s` |
| pitch | `f0_dev_cents` `onset_glide_cents` `vibrato_rate_hz` `vibrato_depth_cents` `terminal_pitch_motion_cents` |
| dynamics | `energy_db` `attack_db_per_s` `sustain_db` `terminal_decay_db` |
| release | `voiced_tail_s` `terminal_decay_shape_db` `release_to_silence_s` `terminal_f0_persistence` |

禁止ペイロード（raw waveform / spectral envelope / mel / speaker embedding）は
**型で入らない**。`assert_no_spectral_payload` が 2 次元配列を拒否する。

## 3. 正規化転写（§6）

絶対値を写さない。移すのは比率と形だけ:

- duration: PJS の `consonant_share` を **Ritsu のノート総尺**へ配分（総尺は Ritsu のまま）
- pitch: note-relative cents。**絶対 Hz を写さない**ので歌手の音域は移らない
- dynamics: unit 内平均 0 の正規化 dB
- release: 正規化した終端カーブ

音素対応は phoneme-aligned（子音→子音 / 母音→母音）。

**構造的な no-op**: PJS と Ritsu の子音/母音比が同じ場合、および probe ノートに
先行子音が無い場合（撥音 /N/ など）、duration 転写は定義上何も動かさない。
`timing_transplant_is_noop` として record に出す（黙って通すと「交換した」と誤読される）。

## 4. 実コーパスのラベル規約（実測で確定した事実）

決め打ちせず census で確かめた結果、次の 2 点を**明示的に**コードへ入れた:

- **促音 `cl` はフレーズ境界ではない**。境界に入れると語中モーラが terminal に化け、
  Ritsu の terminal /ri/ が 107 件へ水増しされる（正しくは 95 件）
- **無声化母音は大文字 AIUEO**（Ritsu readme (9)）。F0 が測れないので probe から除外
- `Edge` / `GlottalStop` は ENUNU 追加ラベル。境界ではない付随ラベルとして読み飛ばす
- 頭子音 `n` を撥音 `N` へ寄せない

lab が wav より長いファイルの probe は**機械的に除外**する（fail-closed 方向）。

## 5. ゲート（§10）

| gate | 内容 |
|---|---|
| G-MATERIAL | 資材 2 点の取得・SHA 一致・repo 外配置・展開の 4 点 |
| G-LICENSE | 正本規約の固定 + 全確認項目 + `answered_by` + 逐語抜粋（未記入は未確認として BLOCKED） |
| G-CENSUS | wav↔lab 対応・時間単位の確定・primary probe の両側実在 |
| G1 determinism | 同一入力で R0–R4 のサンプル列 sha256 が再現 |
| G2 structural | Performance に 2 次元配列が無い + 合成器のアクセス集合が宣言内 |
| G3 intervention | 各段で**意図した軸だけ**が動いたか |
| G4 donor isolation | R0→R4 で PJS テクスチャへ単調接近するだけなら FAIL |
| G5 attribution | 各段の TRF 変化量を probe ペア別に記録 |
| G6 identity | 全段で出力が PJS より Ritsu に近い（margin > 0） |
| G7 TRF | **R0 のみ**から凍結した閾値に対する R4 の判定 |
| G-ear | 人の耳（4 設問）。回答が無い限り BLOCKED |

### 事前登録プロトコル

軸（`nasal_gain_db`）と決定ルールは surrogate から**不変**。閾値の数値だけを
実コーパスの R0 から改めて凍結する（surrogate の R0 は別素材なので数値は移せない）。
`failure_present = R0 >= 1.0 dB` が False のときは `NOT_EVALUABLE` とし、
**baseline に破綻が無いのに改善を主張しない**。

## 6. 停止規則（§16）

次のいずれかで `BLOCKED` を返し、原因と次アクションだけを残す:

source 取得失敗 / license 不明 / SHA 不一致 / corpus 構造が想定外 /
Performance へのテクスチャ漏れ / canonical source の変更 /
登録済みゲートを結果後に変える必要が生じた。

**合理的推測で突破しない。**

## 7. 未解決（第 1 走行時点）

1. G3（介入の直交性）が FAIL。軸の交絡（duration 変更で測定窓が動く / F0 変更で
   包絡推定が動く）が原因の可能性が高いが**未切り分け**。軸の再定義は次版の登録として行う
2. G-ear 未実施。機械では代替しない
3. PJS 側の probe が薄い（terminal /ri/ = 4 件、terminal /su/ = 0 件）
4. §11 の `nasal_gain_shape_db` 実データ validation は gain robustness のみ充足
