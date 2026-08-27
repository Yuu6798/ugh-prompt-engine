# RUN9 HARNESS-3c — Axis Feasibility Record（W1 + W1b 統合、2026-08-27）

Design 根拠: 【RUN9 User裁定 — Learning Recipe 残5キー】§1
（`USER_ADJUDICATION_20260827_LEARNING_RECIPE_5KEYS.txt`）——「正確な
axis ID・単位・値域・量子化・変換式は、PJS validation や Founder学習結果を
使わない fixture/smoke 検証後に axis catalog として凍結する」の実測記録。
本ファイルは workdir 実測記録 `w1_axis_feasibility_report.md`（Step 1-4:
score 変換プロトタイプ + 11 variant の render 実測 + 3 軸実効性の結論）と
`w1b_report.md`（凍結前 grid 検証: 未検証格子点 render + 変換器レベル
unit 検証17ケース + calibration scale 実測）を統合し repo へ収載した
detail record であり、`inputs/score_axis_catalog_v1.json` /
`inputs/loss_evaluator_spec_v1.json` の `provenance` が sha256 pin する
正本である。

**repo（`/home/user/ugh-prompt-engine`）は W1/W1b いずれの実測作業でも
一切書き換えていない**（両フェーズとも `git status --porcelain` 空、
`gate_synth.py` 実行前後で sha256 = `a7404da3b7ea53b94b8d0b694552610e852af2d25d88f7b5d497b58fd30f7894`
不変）。作業はすべて session workdir 隔離（`h3c/singer_variants/<variant>/`
= `voice_genesis/singer/` の隔離コピー、`gate_synth.py` 本体・
`phoneme_jp.py` は無改変のままコピー）。話者 embedding は既存 ritsu emb
（`reexport_out/onnx_gate_40000/`、read-only。Founder 合成 emb・PJS
validation は不使用）。

## 総合判定

**3 軸すべて実測完了。(a) note単位の音高偏差・(b) phrase内の拍・音価配分
= 出力に実効。(c) phrase境界制御 = 現行 gate_synth では
NOT_EXPRESSIBLE_ON_CURRENT_WIRING（配線ギャップ、静的解析と実測の両方で
確認）。**render 合計 28 本（W1: 18本、W1b: 10本）中 27 本成功・1 本失敗
（`pitch_nan`、境界ケース想定通り）。成功した全 variant で run1/run2 の
wav sha256 完全一致（決定論成立）。変換器レベルの catalog 制約 unit 検証
17ケース全 PASS。calibration scale は training 68 曲（aligned）の
5 channel 母標準偏差を独立2プロセスで完全一致するよう実測した。

---

## 第1部（W1）: score 変換プロトタイプ + 11 variant render 実測

### Step 1: render 実行系の再現とスコア構造

コマンド系（HARNESS-3a と同型、`--singer-dir` を追加指定）:

```
python3 voice_genesis/foundry/s1_gate/gate_synth.py run \
  --skip-export \
  --acoustic-dir <workdir>/reexport_out/onnx_gate_40000 \
  --canon-model-dir <workdir>/url/extracted_ds/NamineRitsu_DiffSinger \
  --vocoder-dir <workdir>/url/extracted_voc \
  --singer-dir <workdir>/h3c/singer_variants/<variant> \
  --out-dir <workdir>/h3c/renders/<variant>_run<N> \
  --song sakura --notes-limit 6 \
  --speaker ritsu
```

`--singer-dir`（`gate_synth.py:2587`）は `voice_genesis/singer/` の隔離
コピー。`load_song_module()`（`gate_synth.py:222`）が `singer_dir/score.py`
と `singer_dir/phoneme_jp.py` を `compile()`/`exec()` で読む（`import` 文を
使わないため `__pycache__` の stale `.pyc` を踏まない設計）。

`ScoreNote` 型（`voice_genesis/singer/score.py:40-46`、Read のみ）:

```python
@dataclass(frozen=True)
class ScoreNote:
    midi: float
    duration_beats: float
    mora: pj.Mora
    phrase_index: int
    is_phrase_final: bool
```

- `midi: float`（int ではない）。`_deg()` が `_SCALE_ROOT_MIDI(=57.0) +
  半音 + 12*oct` を返し既に float。
- `duration_beats: float`（`build_sakura_score()` 内で `float(dur_beats)`
  キャスト）。
- `mora`: `phoneme_jp.Mora`（kana/onset/vowel/is_long_vowel_mark）。
- `phrase_index: int`、`is_phrase_final: bool`。

`run_pipeline()` の note 消費経路（`gate_synth.py:1163-1250`）:

```python
class _NoteWithMs:
    def __init__(self, note, dur_ms: float):
        self.midi = note.midi
        self.mora = note.mora
        self._dur_ms = dur_ms
```

`_NoteWithMs` が保持するのは **`midi` / `mora` / `_dur_ms` の3つだけ**。
`phrase_index` / `is_phrase_final` はコピーされず、以降のパイプラインへ
一切渡らない（`grep -c 'phrase_index\|is_phrase_final'
voice_genesis/foundry/s1_gate/gate_synth.py` = 0、静的解析で確認済み。
既存の同一指摘あり: `voice_genesis/evolution/probes/vgl0_control_axis_probe.py:40-45`
「gate_synth の配線ギャップ」）。

- `duration_beats` → `beats_to_seconds(duration_beats, tempo_bpm) *
  1000.0` で ms 化 → `frames_from_ms(ms, frame_ms) = max(int(round(ms /
  frame_ms)), 0)` でフレーム整数へ量子化（`gate_synth.py:321-322`,
  `:1152`）。`frame_ms = 1000 * hop_size / sample_rate = 1000*512/44100
  ≈ 11.60998 ms`（`gate_synth.py:1235-1237`）。量子化粒度はビート単位
  ではなく**フレーム単位 (~11.61ms)**。丸めは note ごとに独立
  （`round()`）— 全体合計は保存されない。
- `midi` は 2 箇所で異なる型に落ちる:
  - Stage1 (duration predictor): `note_tones = [float(note.midi) ...]`
    → `ph_midi1` → `np.array([ph_midi1], dtype=np.int64)`
    （`gate_synth.py:1151,1264,1277`）**int64 キャスト**（fractional は
    truncate、`NaN` は例外）。
  - Stage2 (pitch predictor): `note_midi2` → `np.array([note_midi2],
    dtype=np.float32)`（`gate_synth.py:1309,1320`）**float32 のまま**、
    pitch contour に直接反映。

### Step 2: score 変換プロトタイプの Composition 不変条件検証

workdir `h3c/score_transform_probe.py`（新規）: repo 原本 `score.py` を
read-only import して `build_sakura_score()[:6]`（= `--notes-limit 6` と
同一断片、フレーズ0「さくら」3ノート + フレーズ1「さくら」3ノート）を
baseline note spec（kana/midi/duration_beats/phrase_index/is_phrase_final
の平坦 dict）へ落とし、局所 override を適用した variant spec から
variant 用 `score.py` を機械生成 → `voice_genesis/singer/` の隔離コピー
配下へ配置。`verify_invariants()` が各 variant 生成時に fail-closed で
assert:

1. `len(variant_specs) == len(baseline_specs)`（note 数不変）
2. `[s["kana"] for s in variant_specs] == [s["kana"] for s in
   baseline_specs]`（kana 列 = lyrics/phoneme 列・note 順序が完全一致 —
   mora 自体も `pj.kana_to_morae("さくら")` を baseline と全く同じ呼び出し
   で再構築するため `mora.onset`/`vowel`/`is_long_vowel_mark` も自動的に
   不変）
3. override が触れてよいフィールドを `{"midi","duration_beats",
   "phrase_index","is_phrase_final"}` のみに制限

実行結果: **11 variant すべて invariants PASS**（`score_variants_manifest.json`
に baseline/variant 双方の note spec を記録）。

### Step 3: smoke render 実測（W1、11 variant × 最大2 run）

計 18 render 実行。exit_code=0 が 17 本、exit_code=1 が 1 本
（`pitch_nan`、想定通りの失敗）。

| variant | 軸 | 変換内容 | run1 wav sha256(先頭16桁/末尾10桁) | run1==run2 | baselineと相異 | wav_duration_sec |
|---|---|---|---|---|---|---|
| baseline | - | 無変換 | `c7e1dcdf...8fedd379e` | 一致 | - | 6.873107 |
| pitch_plus1 | (a) | note[2] midi 65→66 (int+1) | `9acea206...389946287` | 一致 | 相異 | 6.873107 |
| pitch_minus2 | (a) | note[2] midi 65→63 (int-2) | `29baa91c...1aab0d2fa` | 一致 | 相異 | 6.873107 |
| pitch_plus12 | (a) | note[2] midi 65→77 (int+12) | `ba276f31...a83d0f9e7` | 一致 | 相異 | 6.873107 |
| pitch_float_half | (a) | note[2] midi 65→65.5 (float) | `6b4ea380...a0278933` | 一致 | 相異 | 6.873107 |
| pitch_nan | (a)境界 | note[2] midi 65→NaN | — (exit_code=1) | N/A | N/A | N/A |
| dur_int_redist | (b) | note[1]1→2/note[2]2→1 (int) | `d7fc76f5...7da12eba4` | 一致 | 相異 | 6.873107 |
| dur_float_redist | (b) | note[1]1→0.5/note[2]2→2.5 (float) | `7c84ef52...6e3c637e5` | 一致 | 相異 | 6.861497 (-1frame) |
| dur_zero | (b)境界 | note[1]1→0/note[2]2→3 | `0ff7d17c...6d3897ab7` | (1回のみ) | 相異 | 6.861497 (-1frame) |
| phrase_c1 | (c) | note[1] is_phrase_final F→T | `c7e1dcdf...8fedd379e` | (1回のみ) | **baseline完全一致** | 6.873107 |
| phrase_c2 | (c) | 全6note phrase_index→0 | `c7e1dcdf...8fedd379e` | (1回のみ) | **baseline完全一致** | 6.873107 |

機械可読版: `w1_render_shas.json`（本ファイルと同じ diff から抽出した生記録、
本 record が sha256 pin する）。

`pitch_nan` の失敗: `ValueError: cannot convert float NaN to integer`
（Stage1 `ph_midi` int64 cast、`gate_synth.py:1277`）。

### 量子化の実測考察（duration 軸）

`frame_ms ≈ 11.60998 ms`、72 BPM での 1 beat = 833.333 ms。
`dur_int_redist`（beats 2,1）と baseline（beats 1,2）は個々のフレーム数が
`round(2*833.333/11.60998)=144` / `round(1*833.333/11.60998)=72` で対称な
ため合計フレーム数 216 が両者一致し `wav_duration_sec` は同一値になった
（wav バイト内容自体は異なる sha ⇒ フレーム配分順が変わるため上流の
duration predictor 入力・音素境界が変わり出力は変わる）。一方
`dur_float_redist`（beats 0.5,2.5）は `round(35.889)=36` /
`round(179.444)=179` で合計 215 フレームとなり baseline の 216 フレームより
**1 フレーム（11.60998ms）短い** — 実測 `wav_duration_sec` 差分
`6.873107-6.861497=0.011610` 秒が計算値と一致。**note ごとの独立丸めのため
合計 beats が同じでも合計フレーム数が保存されない**ことを実測で確認
（量子化制約として catalog に記録した事実）。

### Step 4: 結論（axis catalog v1 設計材料）

- **`midi`**: 型は float。値域は Stage1(duration predictor) へは int64 へ
  暗黙キャスト（fractional は truncate、`NaN`/`inf` は例外で fail）、
  Stage2(pitch predictor) へは float32 のまま渡り pitch contour に連続値
  として反映される。int 変換（±1/±2/±12 半音）・float 変換（+0.5）いずれも
  render 成功・baseline と相異する出力を確認。`NaN` は Stage1 で即例外。
- **`duration_beats`**: 型は float。フレーム量子化は `frame_ms≈11.60998ms`
  単位（ビート単位ではない）、note ごとに独立丸め（合計非保存）。
  ゼロ値はクラッシュせず 0 フレームへクランプされる（`frames_from_ms` の
  `max(...,0)`）。int/float 再配分いずれも render 成功・出力相異を確認。
- **`phrase_index` / `is_phrase_final`**: `_NoteWithMs` にコピーされず
  `run_pipeline` へ一切渡らない（grep=0、静的解析確認済み）。render sha
  実測でも `phrase_c1`/`phrase_c2` は baseline と**完全に同一の wav
  sha256**となり、現行 gate_synth では出力へ無効であることを直接確認した。
- **(a) note単位の音高偏差**: **実効**。
- **(b) phrase内の拍・音価配分**: **実効**（量子化は frame 単位・note
  ごと独立丸め・合計非保存という制約あり。ゼロ長ノートも失敗しない）。
- **(c) phrase境界制御**: **NOT_EXPRESSIBLE_ON_CURRENT_WIRING**。
  `is_phrase_final`/`phrase_index` の変更は render は成功するが出力 wav が
  baseline と byte-for-byte 同一になり、パイプラインに一切影響しない
  （`_NoteWithMs` の配線ギャップ）。axis catalog v1 に (c) を実効な変換式
  として収載するには `_NoteWithMs`/`run_pipeline` 側の拡張が前提になる
  （製品側実装が必要 = 新規注入点 = 新 design revision 要、
  `vgl0_control_axis_probe.py` の申し送りと同じ結論）。

---

## 第2部（W1b）: 凍結前 grid 検証 + calibration scale 実測

Design 根拠: `USER_ADJUDICATION_20260827_LEARNING_RECIPE_5KEYS.txt` §1。
実測正本の前段 = 第1部（W1、本記録の前半節）。

### 総合判定

**Task 1（AX-P1 grid）: 全 PASS**（render 3 variant × 2 run = 6 render
全て byte 一致 + baseline と相異、変換器レベルの range外/非格子/NaN/inf
拒否 unit 検証 10 ケース全 PASS）。
**Task 2（AX-D1 grid）: 全 PASS**（render 2 variant × 2 run = 4 render
全て byte 一致 + baseline と相異、変換器レベルの合計非0/min-duration違反/
非0.25格子拒否 unit 検証 7 ケース全 PASS）。
**Task 3（calibration scale）: 実測完了、5 channel 中 3 channel
（duration_ratio/attack_timing/phrase_end_timing）は標本単位が一意に
確定値、2 channel（relative_f0/normalized_energy）は標本単位（frame 単位
か mora/phrase 単位か）の設計判断が必要だったため両案の値・標本数を実測し
Fable へ判断を委ねた——値自体は独立2プロセス実行で完全一致（決定論確認
済み）。**

render 追加合計 = 10 本（AX-P1: 6, AX-D1: 4）、budget「10 本以内」ちょうど。
全 render exit_code=0、run1/run2 wav sha256 完全一致。

### Task 1: AX-P1 grid — 未検証格子点 render sha 表

（note[2]「ら」の midi = 65.0 を起点にオフセット）

| variant | offset(半音) | midi | run1 wav sha256 | run1==run2 | baselineと相異 |
|---|---|---|---|---|---|
| pitch_offset_minus_0_5 | -0.5 | 64.5 | `2c6aa58a094ca72a4d19b26733547172a59f545668fdedb3c8f23348b12160b4` | 一致 | 相異 |
| pitch_offset_plus_1_5 | +1.5 | 66.5 | `53c72553d9096a8143583bdc407e90c1e51c8831576100aeb667dd6344f52c7d` | 一致 | 相異 |
| pitch_offset_minus_2_0 | -2.0（range下限境界） | 63.0 | `29baa91c7150d89ae48a0a13d69d801d033f09b286e2d0550cff2ca1aab0d2fa` | 一致 | 相異 |

機械可読版: `w1b_render_shas.json`。`pitch_offset_minus_2_0`（float -2.0
オフセット、midi=63.0）の wav sha256 は W1 の `pitch_minus2`（int -2 半音
オフセット、midi=63）と**完全一致**した — float オフセット `65.0 - 2.0`
と int オフセット `65 - 2` が同じ数値 `63.0` へ収束するため render
パイプラインが区別しないという既知の型挙動の追加確認であり、異常ではない。

これで AX-P1 の 0.5 刻み格子 9 値（-2.0, -1.5, -1.0, -0.5, 0, +0.5, +1.0,
+1.5, +2.0）のうち -1.0/+1.0(相当済みint版)/+2.0 を除く全格子点が W1+W1b
の実 render で検証済み。変換式自体は W1/W1b の全ケースで一貫して render
成功・出力相異を示しており、変換器の単純さ（`midi' = midi + offset` の
単純加算、条件分岐なし）から残り格子点も同型に動作すると判断できる
（実装コスト対効果でこれ以上の格子網羅は打ち切り）。

### 変換器レベル: range外・非格子・NaN/inf 拒否の unit 検証（AX-P1、10ケース）

| ケース | offset | 期待 | 結果 |
|---|---|---|---|
| range_over_plus_2_5 | 2.5 | 拒否 | PASS（out of range） |
| range_over_minus_2_5 | -2.5 | 拒否 | PASS（out of range） |
| non_grid_plus_0_3 | 0.3 | 拒否 | PASS（not on 0.5-semitone grid） |
| nan | NaN | 拒否 | PASS（must not be NaN/inf） |
| inf | +inf | 拒否 | PASS（must not be NaN/inf） |
| neg_inf | -inf | 拒否 | PASS（must not be NaN/inf） |
| valid_boundary_plus_2_0 | 2.0 | 受理 | PASS |
| valid_boundary_minus_2_0 | -2.0 | 受理 | PASS |
| valid_half_grid_plus_0_5 | 0.5 | 受理 | PASS |
| valid_zero | 0.0 | 受理 | PASS |

機械可読版: `catalog_constraint_unit_test_results.json`（`ax_p1` キー）。

### Task 2: AX-D1 grid — 0.25刻み再配分 + min-duration境界 render sha 表

（note[1]「く」/note[2]「ら」、合計3拍を保存）

| variant | note[1] dur | note[2] dur | run1 wav sha256 | run1==run2 | baselineと相異 | 備考 |
|---|---|---|---|---|---|---|
| dur_quarter_redist | 0.75 | 2.25 | `810e09d7079ec0985b4a2170372c308bdf3292367ae52424a89acb480fdc9cb0` | 一致 | 相異 | 0.25刻み未検証粒度 |
| dur_min_boundary | 0.25 | 2.75 | `399d93e9c5e8e69aa469cc241dff1448f400c30d045d67c1510bd460a68526f1` | 一致 | 相異 | 変換後duration=0.25 beat（catalog min制約ちょうど境界）でも render成功 |

機械可読版: `w1b_render_shas.json`。両 variant とも合計3拍は保存されて
いるが `wav_duration_sec` が baseline より 1 フレーム（≈11.60998ms）短い
— W1 の `dur_float_redist` で確認済みの「note ごと独立丸めのため合計
フレーム数が保存されない」量子化制約が 0.25 刻みでも同様に再現することを
確認した。`dur_min_boundary` は catalog の「各note duration >= 0.25 beat」
制約のちょうど境界値（0.25）でも render がクラッシュせず成功することを
実測確認。

### 変換器レベル: 合計非0・min-duration違反・非0.25格子 拒否の unit 検証（AX-D1、7ケース）

baseline durations `[note1=1.0, note2=2.0]` を土台に:

| ケース | deltas | 期待 | 結果 |
|---|---|---|---|
| sum_nonzero | [+0.5,-0.25] (合計+0.25) | 拒否 | PASS（sum must be 0） |
| post_below_min | [-1.0,+1.0] (note1 post=0.0) | 拒否 | PASS（post < min 0.25） |
| non_grid_delta | [+0.1,-0.1] | 拒否 | PASS（not on 0.25-beat grid） |
| nan_delta | [NaN,0.0] | 拒否 | PASS（must not be NaN/inf） |
| valid_quarter_redist | [-0.25,+0.25] | 受理 | PASS |
| valid_min_boundary | [-0.75,+0.75] (note1 post=0.25 境界) | 受理 | PASS |
| valid_zero_delta | [0.0,0.0] | 受理 | PASS |

機械可読版: `catalog_constraint_unit_test_results.json`（`ax_d1` キー、
`unit_test_results["all_pass"] = true`、AX-P1 10 + AX-D1 7 = 17 ケース
全 PASS）。

### Task 3: calibration scale 実測（training バンドル 5 channel の母標準偏差）

供給ファイルの sha256 照合:

```
入力: harness_work/h3b/run1/training_bundle.json
期待sha256: 6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da
実測sha256: 6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da
一致: True
```

`validation_bundle.json` は本フェーズで一切 open/import していない。

eligible 母集団: `training_bundle.json` は 70 曲収録、
`not_extracted_summary.aligned_count = 68`、`count_mismatch_song_ids =
["pjs008", "pjs064"]`。68 曲全曲・全 channel で `status == "extracted"`、
欠損値（None）は 0 件（duration_ratio morae 2748件、attack_timing morae
2748件、phrase_end_timing phrases 201件、relative_F0 voiced frame
160501件、energy_envelope block 166053件、いずれも None 0件を実測確認）。

バンドル内 channel 構造（`channel_vocabulary_map`、physical_channel ↔
extracted_trait 対応）:

| physical_channel（loss_evaluator_spec名） | extracted_trait（バンドル実装名） |
|---|---|
| relative_f0 | `relative_F0` |
| duration_ratio | `duration_ratio` |
| normalized_energy | `energy_envelope` |
| attack_timing | `onset_offset.attack_timing` |
| phrase_end_timing | `onset_offset.phrase_end_timing` |

各 channel の実データ構造:

- `duration_ratio.morae[j].value`: mora単位のスカラー1値。1標本=1mora に
  曖昧性なし。
- `onset_offset.attack_timing.morae[j].value_s`: mora単位のスカラー1値。
  1標本=1mora に曖昧性なし。
- `onset_offset.phrase_end_timing.phrases[j].value_s`: phrase単位の
  スカラー1値。1標本=1phrase に曖昧性なし。
- `relative_F0.morae[j].frames[k]`: mora**内側**にさらに frame 配列
  （5msグリッド実測、`voiced`/`value_hz`）。morae 数=2748に対し frame
  総数=165656（voiced=160501）— mora単位のスカラーが存在せず、frame粒度
  が生データの実際の単位。
- `energy_envelope.phrases[j].blocks[k]`: phrase**内側**にさらに
  block(frame)配列。phrases数=201に対しblock総数=166053。

### 算出結果（母標準偏差、ddof=0、numpy float64、丸めなし、全桁）

**曖昧性なし（3 channel、確定値）**:

| channel | sample_unit | n_samples | population_std（全桁） | mean（全桁） |
|---|---|---|---|---|
| duration_ratio | mora | 2748 | `0.26757779133213067` | `1.0266980782881285` |
| attack_timing | mora | 2748 | `0.046215471651767655` | `0.007203579777563855` |
| phrase_end_timing | phrase | 201 | `0.04277885307503042` | `0.06490550525415459` |

**設計判断が必要だった2 channel（両案実測、Fable判定 = frame粒度採用）**:

relative_f0（`relative_F0.morae[].frames[].value_hz`, voiced==true のみ,
unvoiced はゼロ補完せず除外。frame総数165656中voiced=160501）:

| 案 | sample_unit | n_samples | population_std（全桁） | mean（全桁） |
|---|---|---|---|---|
| (A) frame **[採用]** | frame | 160501 | `28.68858178404701` | `-0.9890442489423432` |
| (B) mora（参考、不採用） | mora | 2746 | `20.185403077101824` | `-1.688564980473217` |

normalized_energy（`energy_envelope.phrases[].blocks[].value`, 欠損0件,
block総数166053）:

| 案 | sample_unit | n_samples | population_std（全桁） | mean（全桁） |
|---|---|---|---|---|
| (A) frame **[採用]** | frame(block) | 166053 | `0.22003129791359613` | `0.35856642997423976` |
| (B) phrase（参考、不採用） | phrase | 201 | `0.08926916409743947` | `0.38747662785282244` |

**Fable 判定（2026-08-27）**: scale の粒度 = 当該 channel の loss に入る
原子残差標本の粒度。`relative_f0`/`normalized_energy` は lesson が frame
契約の contour を保持し loss も frame 単位比較のため frame プール（案A）
を採用する — mora/phrase 平均集約（案B）は lesson に存在しない新規導出
統計を挟むため不採用。この判定理由と両案の値・標本数を
`loss_evaluator_spec_v1.json` の `calibration_scale.derivation` 欄に
逐語で併記して凍結する。validation は算出に一切使わない
（裁定 §2「training splitのみから決定論的calibration scaleを作り」）。

### 独立2プロセス再計算による一致確認

`calibration_scale_probe.py` を独立した2回の `python3` プロセス起動
（`calibration_scale_result_run1.json` / `calibration_scale_result_run2.json`）
で実行し、出力JSONファイルの内容を diff — **完全一致**（バイト差分0）。
出力ファイル sha256 も両者一致:
`79107217eb21d965b48b7e67fa4c90c33986274ba82c8f22f0242872c27e6323`。
決定論成立を確認。

---

## 生成物一覧（すべて workdir 限定、repo 外——本 record が唯一の repo 収載記録）

- `h3c/score_transform_probe.py`（score 変換プロトタイプ + catalog制約
  検査関数 `validate_ax_p1_offset()`/`validate_ax_d1_delta_vector()`）
- `h3c/score_variants_manifest.json`（baseline/variant note spec +
  score.py sha256）
- `h3c/singer_variants/<11 variant>/`（`voice_genesis/singer/` 隔離コピー）
- `h3c/run_renders.sh` / `h3c/run_renders_w1b.sh`（render ドライバ）
- `h3c/renders/<variant>_run<N>/`（render 出力、計28件 + stdout/stderrログ）
- `h3c/w1_render_shas.json` / `h3c/w1b_render_shas.json`（機械可読 sha 記録）
- `h3c/catalog_constraint_unit_test_results.json`（unit検証17ケース）
- `h3c/calibration_scale_probe.py`（母標準偏差算出スクリプト）
- `h3c/calibration_scale_result_run1.json` / `_run2.json`（独立2回実行の生出力）
- `h3c/w1b_calibration_values.json`（Task3 機械可読最終記録）
- `h3c/w1_axis_feasibility_report.md` / `h3c/w1b_report.md`（本 record の元記録）

## 逸脱・停止事由

なし。repo ファイル変更ゼロ（W1/W1b とも `git status --porcelain` 空、
`gate_synth.py` sha256 実行前後で不変）。`pitch_nan` の exit_code=1 は
境界ケースとして想定内の失敗であり harness の異常ではない。render 追加は
W1b で10本ちょうど（budget「10本以内」を超過せず）。
`validation_bundle.json` は不読了。

## PR #331 Codex bot レビュー第1巡対応（2026-08-27、Claude 完結ルート）

5 指摘全て Fable 採用判定（機械汚染防止領域）。実装・検証・返信起草は
Sonnet に委譲、コミット/push は Fable が別途実行する（本追記フェーズでは
未実施）。

1. **AX-D1 重複 note index 拒否（P1）**: `score_axis_transform.apply_ax_d1()`
   に、`note_indices` 内の重複を変換前に fail-closed 拒否するチェックを
   追加した（`[0,0]` + delta `[0.25,-0.25]` は zero-sum 検査を通過するが、
   同一 note への代入上書きで beat 合計保存が silent に破れるため）。
   `ScoreAxisTransformError` を送出し、重複 index を列挙する。
2. **欠測 channel trial の NOT_SCORABLE 化（P1）**:
   `inputs/loss_evaluator_spec_v1.json` の `missing_policy.not_measurable_
   definition` を、「いずれかの channel が eligible == 0 の candidate は
   NOT_SCORABLE として candidate selection から除外する」定義へ改訂した
   （旧定義はゼロ補完相当で部分計測 candidate が完全計測 candidate に
   勝ち得る欠陥があった）。全 candidate が NOT_SCORABLE の trial の扱い
   （best 更新なしで次 trial へ、render 予算は消費済み計上）も明文化し、
   `spec_correction_note` で本改訂が学習開始前の是正であり
   `DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md` §11.4 Freeze
   Rule に抵触しない旨を明記した。
3. **candidate 比率矛盾の解消（P2）**:
   `inputs/candidate_generation_spec_v1.json` の `proposal_schedule_table`
   （trial 2-32）を、設計正本
   （`scratchpad/h3c_five_manifests_spec.md`「近傍列挙 + hash 由来の探査
   候補の固定比率（3:1）」）どおり「candidate 0-2 = 近傍 / candidate 3 =
   探査」（3:1）へ統一し、`subsequent_trial_schedule` の記述と完全一致
   させた（旧表は候補0-1/2-3の2:2表記で「3:1」宣言と矛盾していた）。
4. **branch uses 集合の厳密一致検証（P2）**: `run9_schema.
   validate_learning_data_binding_manifest()` の `branch_usage.practice.
   uses`/`branch_usage.education.uses` 検査を、単一要素の混入禁止のみの
   検査から厳密集合一致（`practice.uses` = `{practice_audio_split_
   manifest_sha, pjs_consumed_inputs_manifest_sha}` ちょうど、
   `education.uses` = `{education_technique_lesson_manifest_sha}` ちょうど
   ——空・欠落・過剰を全て拒否）へ強化した。既存の「education pin の
   practice 混入禁止」検査はこの厳密集合一致に包含される。
5. **AX-D1 の same-phrase 検査（P1）**: `score_axis_transform.
   apply_ax_d1()` に、`note_indices` が単一 `phrase_index` に属することを
   fail-closed で検証するチェックを追加した（異なる phrase 間の
   再配分は global zero-sum は通過するが「phrase 内再配分」という凍結
   定義に違反するため）。違反時は phrase_index の内訳を列挙するエラーを
   送出する。

**連鎖更新**: `loss_evaluator_spec_v1.json`/`candidate_generation_spec_v1.
json` のバイト変更に伴い `RUN9_CONTRACT.yaml` の `loss_evaluator_spec_sha`
/`candidate_generation_spec_sha` を第2世代へ repin した（旧値は
`RUN9_CONTRACT.yaml` 側に世代履歴コメントとして append-only 保持）。
`score_axis_transform.py` の変更は catalog 側 sha pin を持たない
（`score_axis_catalog_v1.json` は `repo_module` へのパス参照のみで
transformer 自体の sha は pin しない設計——`run9_schema.
validate_score_axis_catalog_manifest()` の live import による機能的
cross-check がフルテストスイート内で通過することで整合を確認する）ため
repin 不要。

**検証**: `ruff check .` clean（リポジトリ全体）。
`pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`
2414 件全 pass（新規テスト12件を含む: AX-D1 重複拒否1件・合計保存性質
テスト4件・same-phrase 拒否/回帰2件、branch uses 厳密一致5件、他既存回帰）。

## PR #331 Codex bot レビュー第2巡対応（2026-08-27、Claude 完結ルート）

2 指摘全て Fable 採用判定（機械汚染防止領域）。実装・検証・返信起草は
Sonnet に委譲、コミット/push は Fable が別途実行する（本追記フェーズでは
未実施）。

1. **digest→candidate 写像の byte レベル凍結（P1）**:
   `inputs/candidate_generation_spec_v1.json` `proposal` 節へ、実装が選べる
   余地を残さない byte レベルの写像規則を逐語で追記した。
   - **正準候補列 L**（`candidate_ordering`）: 有効な単一軸候補
     （AX-P1: `(axis_id, note_index 昇順, offset 昇順〔0除外8グリッド値〕)`。
     AX-D1: `(axis_id, phrase_index 昇順, 同一phrase内note indexペア(i,j)
     〔i昇順→j昇順、i から j へ delta を移す ordered pair〕, delta 昇順
     〔0.25刻み・min-duration 0.25 制約を満たす最大値まで〕)`）の全列挙を、
     タプル比較 `(axis_id, 第2キー, 第3キー…)` の辞書順で凍結した
     （`"AX-D1" < "AX-P1"` のため AX-D1 群が先）。
   - **探査候補**（`exploratory_candidate_rule`）:
     `digest = sha256(UTF-8(f"{seed}:{arm}:{founder_id}:{trial}:{candidate}"))`
     の先頭8バイトを big-endian uint64 として解釈し `idx = u mod len(L)`、
     評価済み・無効な場合は `idx+1, idx+2, …`（mod len(L)）の決定論線形
     プロービングで次の未評価有効候補へ、全滅時は当該 candidate を
     NOT_PROPOSABLE として記録する（発明しない）。
   - **近傍候補**（`neighborhood_candidate_rule`）: 現 best（trial 1 直後は
     trial 1 の最良候補——恒等候補である場合を含む）から、index キー
     （note_index／phrase_index+(i,j)）を固定したまま値キー（offset／
     delta）のみ ±1量子化ステップ変化させた L の要素を、L と同じ辞書順で
     列挙し未評価の先頭3件を採用、3件未満の不足分は探査規則で補充する。
     恒等が現 best の場合の近傍（`identity_neighbor_rule`）も、この
     ルールを値キー初期値0のベクトルへ機械的に適用した結果として明記
     した。
   - 上記の generator-agnostic な部分（L 構築・digest→index 写像・
     プロービング・近傍列挙）を参照実装
     `voice_genesis/evolution/run9_dual_founder_pjs/candidate_proposal.py`
     として新設した（実際の探索ループ——PRACTICE actor 内候補適用・
     render・loss 評価・trial 跨ぎ best 更新・trace 保存——は指摘原文が
     述べる「次の PR」の対象として本モジュールのスコープ外に置く）。
2. **validator の proposal schedule 形状強制（P2）**:
   `run9_schema.validate_candidate_generation_spec_manifest()` が
   `proposal` 節を一切検査していなかった欠落を埋めた。新設
   `_validate_candidate_generation_proposal_schedule()` が
   `proposal_schedule_table`（trial1=恒等+探査3件／trial2-32=近傍3件+
   探査1件の3:1 被覆）を凍結定数との完全一致で検査し、digest 写像規則の
   必須キー（`digest_formula`/`digest_encoding` のテンプレート文字列、
   `exploratory_candidate_rule.byte_to_integer` のエンディアン規則、
   `probing_rule` のプロービング規則）の存在と値一致を強制し、
   `candidate_ordering`/`neighborhood_candidate_rule` の必須キー欠落も
   拒否する。32×4=128 整合も trial1(1+3) + trial2-32((3+1)*31) の内訳
   から独立に再導出して structure 節の値と cross-check する。これにより
   repin で恒等候補脱落・比率復元（2:2 等）が起きても fail-closed で
   拒否される。

**連鎖更新**: `inputs/candidate_generation_spec_v1.json` のバイト変更に
伴い `RUN9_CONTRACT.yaml` の `candidate_generation_spec_sha` を第3世代へ
repin した。さらに本節を新設したことに伴う `HARNESS3C_AXIS_FEASIBILITY_
RECORD.md` 自体の実バイト sha256 変更により、5 manifest 共通の
`provenance.detail_record.sha256` 参照値が全て追随更新となるため、
5 manifest 全て（`score_axis_catalog_sha`/`loss_evaluator_spec_sha`/
`candidate_generation_spec_sha`/`compute_budget_manifest_sha`/
`learning_data_binding_manifest_sha`）を第1巡と同型の cascade repin した
（旧値は `RUN9_CONTRACT.yaml` 側に世代履歴コメントとして append-only
保持）。

**検証**: `ruff check .` clean（リポジトリ全体）。
`pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`
2450 件全 pass（新規テスト36件を含む: `candidate_proposal.py` 参照実装の
決定論・辞書順・プロービング境界テスト26件、validator 形状拒否/回帰
テスト10件、他既存回帰）。

## PR #331 Codex bot レビュー第3巡対応（2026-08-27、Claude 完結ルート）

3 指摘全て Fable 採用判定（機械汚染防止領域）。実装・検証・返信起草は
Sonnet に委譲、コミット/push は Fable が別途実行する（本追記フェーズでは
未実施）。

1. **近傍 3:1 の構造的達成可能化（P1）**:
   旧 `neighborhood_candidate_rule.neighbor_value_perturbation` および
   参照実装 `candidate_proposal.neighbors_of()` は、現 best の index キー
   を固定したまま値キーを ±1 量子化ステップした2候補のみを近傍として
   いた。非恒等 best では最大2件しか近傍枠（凍結 3:1 比率の3枠）を
   満たせず、trial 2-32 の近傍3枠が構造的に達成不能という欠陥だった
   （**参照実装が凍結表を構造的に不可能にしていた**ことを正直に認める
   ——第2巡で「値キー±1量子化ステップ」と定義した時点で、この構造的
   不足は数学的に自明だった）。
   - `inputs/candidate_generation_spec_v1.json` `proposal.
     neighborhood_candidate_rule.neighbor_value_perturbation` を、近傍
     候補の優先順位リストへ改訂した: best B = (axis, index キー, 値キー
     v) に対し (1) v+1量子化ステップ (2) v-1量子化ステップ (3) v+2量子化
     ステップ (4) v-2量子化ステップ (5) 同 axis・同値キーで index キーが
     L 順で1つ前の候補 (6) 同 axis・同値キーで index キーが L 順で1つ後
     の候補、の順に評価し、catalog 制約内で有効・L に存在・未評価の
     ものを列挙する。先頭3件を近傍枠に採用し、6件を尽くしても3件に
     満たない場合（値キーが domain 端かつ index キーが L 内の該当 axis
     distinct index キー列の端という**端点の場合に限る**）のみ探査規則
     で補充する。内部領域では3:1 が構造的に達成可能になったことが
     本修正の要点であり、`shortfall_handling` にこの端点限定の例外を
     明記した。`identity_neighbor_rule`（恒等 best の近傍）は既存定義
     （値キー初期値0を全 index キーに適用した結果）を上記優先順位リスト
     の項目(1)(2)の一般化として整合する形へ記述を統合した（挙動は不変
     ——恒等の近傍数は通常3件を十分上回るため）。`proposal_schedule_table`
     の trial 2-32 candidate 0..2 の rule 文言も新定義に合わせて改訂した。
   - 参照実装 `candidate_proposal.neighbors_of()` を同一の優先順位リスト
     へ書き換えた（`_index_key()`/`_value_key()`/`_make_candidate()`/
     `_sorted_index_keys()` を新設し、L 内の distinct index キー列から
     隣接キーを導出する）。
   - **検証**: 決定論性テスト（同一入力→同一近傍列を2回計算一致で確認）
     + 非恒等 best が内部領域（値キー・index キーいずれも端でない）で
     優先順位どおり5件（うち先頭3件を近傍枠採用）を得ることの直接テスト
     + 値キー端点のみ・index キー端点のみでは依然3件を達成できること
     （どちらか一方のみの端点では shortfall が起きない）の分離テスト
     + range端とindex端が同時に揃う真の端点で2件（AX-P1）・唯一ペアで
     0件（AX-D1）に shortfall することの直接テストを
     `tests/test_candidate_proposal.py` に追加した（既存の
     `test_neighbors_of_ax_p1_boundary_has_single_neighbor`/
     `test_neighbors_of_ax_d1_boundary_at_minimum_delta` 等、旧「単一
     近傍」を正としていたテストは新定義下での正しい期待値へ更新した）。
2. **NOT_SCORABLE 政策の validator 形状強制（P2）**:
   `run9_schema.validate_loss_evaluator_spec_manifest()` は
   `missing_policy.zero_fill_prohibited`/`eligible_count_required_per_
   channel` の2 boolean のみを検査しており、第1巡で是正した
   `not_measurable_definition`（candidate 単位 NOT_SCORABLE + selection
   除外 + 全 NOT_SCORABLE trial の扱い）の文言そのものは一切検査して
   いなかった。repin で旧「部分 channel 採点」文言へ差し戻されても
   通過し得る欠陥だった。凍結定数
   `_LOSS_EVALUATOR_EXPECTED_NOT_MEASURABLE_DEFINITION` を新設し、
   逐語一致で検査するよう改めた（不一致・欠落を fail-closed で拒否）。
3. **actor_boundary の厳密検証（P2）**:
   同 validator は `actor_boundary` をトップレベル必須キーとしてのみ
   検査しており、`practice`/`education` の中身（PRACTICE = raw audio
   から Founder 自身が抽出・education lesson / precomputed teacher
   feature の入力禁止／EDUCATION = 凍結 lesson 使用）は一切検査して
   いなかった。空・欠落・緩和文言（education lesson を PRACTICE へ
   許可する等）が repin で通過し得る欠陥だった。凍結定数
   `_LOSS_EVALUATOR_EXPECTED_ACTOR_BOUNDARY_PRACTICE`/`_EDUCATION` を
   新設し、逐語一致で検査するよう改めた。

**連鎖更新**: `inputs/candidate_generation_spec_v1.json` のバイト変更に
伴い `RUN9_CONTRACT.yaml` の `candidate_generation_spec_sha` を第4世代へ
repin した（指摘2・3は `loss_evaluator_spec_v1.json` の内容自体を変更せず
validator 強化のみ）。さらに本節を新設したことに伴う
`HARNESS3C_AXIS_FEASIBILITY_RECORD.md` 自体の実バイト sha256 変更により、
5 manifest 共通の `provenance.detail_record.sha256` 参照値が全て追随更新と
なるため、5 manifest 全て（`score_axis_catalog_sha`/`loss_evaluator_spec_
sha`/`candidate_generation_spec_sha`/`compute_budget_manifest_sha`/
`learning_data_binding_manifest_sha`）を第1/2巡と同型の cascade repin した
（旧値は `RUN9_CONTRACT.yaml` 側に世代履歴コメントとして append-only
保持）。

**検証**: `ruff check .` clean（リポジトリ全体）。
`pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`
2464 件全 pass（第2巡時点の2450件から+14件: `candidate_proposal.py` 近傍
優先順位リストの決定論・内部領域3件達成・端点 shortfall テスト、
validator の `not_measurable_definition`/`actor_boundary` 逐語一致拒否
テスト、他既存回帰）。

## PR #331 Codex bot レビュー第4巡対応（2026-08-27、Claude 完結ルート）

2 指摘全て Fable 採用判定（機械汚染防止領域）。実装・検証・返信起草は
Sonnet に委譲、コミット/push は Fable が別途実行する（本追記フェーズでは
未実施）。

1. **shortfall 主張の正直是正（P2）**:
   第3巡で `inputs/candidate_generation_spec_v1.json`
   `proposal.neighborhood_candidate_rule.shortfall_handling` に書いた
   「shortfall は値キー・index キーの両方が同時に端という端点の場合に
   限られる」という主張は偽だった。優先順位リスト6項目のうち3件以上が
   catalog 制約内で有効な**内部領域**の current best でも、それらが
   同一 (seed, arm, founder_id) の探索内で既に評価済みであれば
   `select_neighborhood_candidates()` の `is_evaluated` フィルタ後に
   3件未満へ減る——既存テスト
   `test_select_neighborhood_candidates_shortfall_when_all_evaluated`
   （第3巡で追加済み）がこれを直接実証していたにもかかわらず、
   spec 文言・validator 定数・参照実装 docstring はいずれも「端点のみ」
   という誤った不変条件を主張したままだった（参照実装が凍結表を構造的に
   不可能にしていたことを認めた第3巡と同種の正直是正）。
   - `shortfall_handling` を、shortfall の発生源が (a) 幾何的端点（値キー
     が domain 端かつ index キーが L 内の該当 axis 列の端）と (b) 評価済み
     枯渇（内部領域の best でも優先順位候補が同一探索内で既に評価済みの
     ため未評価残数が3件未満になる）の**両方**であると認める定義へ
     置換した。3:1 は「近傍優先3スロット + 探査1スロット」の決定論
     スロットテンプレートであり、trial 内 candidate 数は常に4だが、近傍
     スロットが埋まらない場合は理由を問わず探査規則が決定論的に補充する
     ——宣言比率と実測内訳の乖離を隠さないため、各 trial の実際の内訳
     （candidate 0..3 それぞれが近傍・探査・NOT_PROPOSABLE のいずれで
     充足されたか）を探索 trace（`practice_actor_binding.trace_storage`
     が定める trial log）へ必須記録する旨を明記した。
   - `proposal_schedule_table`（trial 2-32 candidate 0..2）の rule 文言
     から「exploratory shortfall at boundary current-bests only」という
     同じ誤った不変条件を除き、幾何的端点・評価済み枯渇いずれの理由でも
     決定論的に探査規則がバックフィルし、実際の内訳を trial log へ記録
     する旨の文言へ改訂した。`run9_schema.
     _CANDIDATE_GENERATION_EXPECTED_PROPOSAL_SCHEDULE_TABLE` を逐語で
     追随更新した。
   - 参照実装 `candidate_proposal.py` の `select_neighborhood_candidates()`
     docstring（同じ「端点でのみ3件未満」という誤った主張の発生源の一つ）
     を、`neighbors_of()` の生出力（catalog 制約内で有効な候補集合。
     こちらは実際に端点限定で3件未満になるという主張は正しい——
     `is_evaluated` フィルタを未適用のため）と、本関数が返す評価済み
     フィルタ後の実際の結果（内部領域の best でも評価済み枯渇で3件未満に
     なり得る）を明確に区別する記述へ改めた。モジュール冒頭の docstring
     にも本巡の是正内容を追記した。
2. **tie-break の実行可能な全順序凍結（P1）**:
   `selection.tie_break` の旧定義「(objective, 軸ベクトルの辞書順)」は
   座標順・表現・恒等候補の位置が未定義であり、実行可能な全順序ではな
   かった（`selection` 節はそもそも validator の検査対象外でもあった —
   トップレベル必須キーとしてのみ存在確認され中身は無検査だった）。
   - tie-break キーを **(objective, candidate_ordinal)** へ置換凍結した。
     `candidate_ordinal(候補)`: 恒等候補（`trial1_candidate0_rule` の
     baseline）は `-1`。非恒等候補 c（`candidate_ordering` が定める正準
     候補列 L の要素）は c が L（既に凍結済みの total_order — AX-D1 群が
     先、タプル辞書順）において占めるインデックス（0始まり）。同
     objective なら candidate_ordinal の小さい方が勝つ。恒等・AX-P1・
     AX-D1 の全候補型を単一整数キーで被覆する実行可能な定義。
   - 参照実装 `candidate_proposal.candidate_ordinal()` を新設した
     （恒等=-1、非恒等候補は `all_candidates`＝L 内の線形探索で位置を
     返し、L に存在しない候補は `ValueError` で拒否する）。
   - `run9_schema.py` に `_validate_candidate_generation_selection()` を
     新設し、`validate_candidate_generation_spec_manifest()` から呼び出す
     配線を追加した——`selection` 節の中身が一切検査されていなかった
     欠落（`objective`/`tie_break` 必須キー検査 + `tie_break` の逐語一致
     検査）を埋めた。
   - **テスト**: `candidate_ordinal()` の恒等=-1・非恒等=L内インデックス
     一致・決定論性・L 非所属候補の拒否、および tie-break 決定的勝者
     テスト（同 objective の恒等 vs 非恒等・AX-D1 vs AX-P1・L 内前後）を
     `tests/test_candidate_proposal.py` に追加した。`selection.tie_break`
     の逐語一致拒否・`selection` 節欠落キー拒否・非object拒否テストを
     `tests/test_h3c_learning_recipe_manifests.py` に追加した。

**連鎖更新**: `inputs/candidate_generation_spec_v1.json` のバイト変更に
伴い `RUN9_CONTRACT.yaml` の `candidate_generation_spec_sha` を第5世代へ
repin した。さらに本節を新設したことに伴う `HARNESS3C_AXIS_FEASIBILITY_
RECORD.md` 自体の実バイト sha256 変更により、5 manifest 共通の
`provenance.detail_record.sha256` 参照値が全て追随更新となるため、
5 manifest 全て（`score_axis_catalog_sha`/`loss_evaluator_spec_sha`/
`candidate_generation_spec_sha`/`compute_budget_manifest_sha`/
`learning_data_binding_manifest_sha`）を第1-3巡と同型の cascade repin
した（旧値は `RUN9_CONTRACT.yaml` 側に世代履歴コメントとして append-only
保持）。

**検証**: `ruff check .` clean（リポジトリ全体）。
`pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`
2474 件全 pass（第3巡時点の2464件から+10件: `candidate_proposal.py`
`candidate_ordinal()`/tie-break 決定的勝者テスト7件、validator
`selection.tie_break` 逐語一致拒否テスト3件、他既存回帰）。

## PR #331 Codex bot レビュー第5巡対応（2026-08-27、Claude 完結ルート）

2 指摘全て Fable 採用判定（機械汚染防止領域）。実装・検証・返信起草は
Sonnet に委譲、コミット/push は Fable が別途実行する（本追記フェーズでは
未実施）。

1. **proposal 定数の pinned catalog への束縛（P2）**:
   `candidate_proposal.py` は AX-P1/AX-D1 の offset domain・quantization
   step・min-duration を本モジュール内にハードコード定数
   （`AX_P1_OFFSET_DOMAIN`/`AX_P1_QUANTIZATION_STEP`/
   `AX_D1_QUANTIZATION_STEP`/`AX_D1_MIN_DURATION_BEATS`）として持っており、
   `score_axis_catalog_v1.json`（catalog）が正当な理由で repin されて
   range/step の値が変わっても、これらの定数は追随せず旧値のまま漂流し
   得た。`run9_schema.load_pinned_score_axis_catalog_manifest()` の
   catalog↔変換器 cross-check は `score_axis_transform.apply_ax_p1()`/
   `apply_ax_d1()` が catalog 値を正しく消費することしか検証しておらず、
   `candidate_proposal.py` 側のハードコード定数までは照合していなかった
   ——単一情報源（catalog）が正であるべき箇所に、参照実装だけが追随しない
   第2の情報源が存在していた。
   - ハードコード定数を全廃し、`score_axis_transform.py` と同型の catalog
     消費規約（`build_ax_p1_ordering()`/`build_ax_d1_ordering()`/
     `build_candidate_ordering()`/`neighbors_of()`/
     `select_neighborhood_candidates()` がいずれも `catalog: Mapping[str,
     Any]` を必須キーワード引数として受け取り、offset domain・
     quantization step・min-duration を都度 catalog から導出する）へ
     置換した。本番経路では呼び出し側が `run9_schema.
     load_pinned_score_axis_catalog_manifest()` の戻り値を渡す。
   - **テスト**: `tests/test_candidate_proposal.py` に catalog 実データ
     （`score_axis_catalog_v1.json` を直接読んだ辞書、pin 検証は経由しない
     ——`test_h3c_learning_recipe_manifests.py::_manifest_data()` と同型の
     軽量パターン）を注入する `CATALOG` fixture を新設し、既存テスト全件を
     `catalog=CATALOG` を渡す形へ追随させた。さらに catalog の
     range/step/min-duration を改変すると L（候補列）の内容が実際に追随
     することを直接検証するテスト2件（AX-P1 range 縮小 → offset domain
     縮小、AX-D1 min-duration 引き上げ → donor 側 delta 上限縮小）を追加
     した。
2. **schedule summary の stale「±1 のみ」文の是正（P2）**:
   `inputs/candidate_generation_spec_v1.json` トップレベル
   `proposal.subsequent_trial_schedule` に、第3巡で `neighborhood_
   candidate_rule.neighbor_value_perturbation` を値キー ±1/±2 量子化
   ステップ + 隣接 index キーの6項目優先順位リストへ拡張する前の
   「現 best の近傍 ± 1 量子化ステップ」という stale 文言が残存しており、
   詳細凍結規則（`neighbor_value_perturbation`/`proposal_schedule_table`
   trial 2-32 行）と矛盾していた。`run9_schema.
   _CANDIDATE_GENERATION_PROPOSAL_TOP_LEVEL_REQUIRED_KEYS` は
   `subsequent_trial_schedule` をキー存在としてのみ検査しており（文言の
   逐語一致検査は無い）、この矛盾は validator を素通りしていた。
   - `subsequent_trial_schedule` を「値キー ±1/±2 量子化ステップ + 隣接
     index キーの優先順位リストから未評価の先頭3件、幾何的端点・評価済み
     枯渇いずれの理由の不足分も探査規則で決定論的に補充」する旨へ改訂し、
     詳細は `neighborhood_candidate_rule`/`exploratory_candidate_rule` が
     定める旨を明記した。
   - spec 全体を grep 掃討し、同種の stale「±1 のみ」文が他に残存しない
     ことを確認した（`neighbor_value_perturbation`/`identity_neighbor_
     rule` 内の「旧定義は±1のみだった」という記述は、いずれも第3-4巡が
     是正した旧状態を指す歴史的記述であり、現在の規則を誤って主張する
     stale 文ではない）。`run9_schema.py` は `subsequent_trial_schedule`
     の中身を逐語一致検査していないため、本改訂に伴う validator 定数の
     追随更新は不要だった。

**連鎖更新**: `inputs/candidate_generation_spec_v1.json` のバイト変更に
伴い `RUN9_CONTRACT.yaml` の `candidate_generation_spec_sha` を第6世代へ
repin した。さらに本節を新設したことに伴う `HARNESS3C_AXIS_FEASIBILITY_
RECORD.md` 自体の実バイト sha256 変更により、5 manifest 共通の
`provenance.detail_record.sha256` 参照値が全て追随更新となるため、
5 manifest 全て（`score_axis_catalog_sha`/`loss_evaluator_spec_sha`/
`candidate_generation_spec_sha`/`compute_budget_manifest_sha`/
`learning_data_binding_manifest_sha`）を第1-4巡と同型の cascade repin
した（旧値は `RUN9_CONTRACT.yaml` 側に世代履歴コメントとして append-only
保持）。

**検証**: `ruff check .` clean（リポジトリ全体）。
`pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`
2477 件全 pass（第4巡時点の2474件から+3件: `candidate_proposal.py` catalog
消費テスト3件〔offset domain fixture 整合1件・catalog 改変反映2件〕、他
既存回帰）。

## PR #331 Codex bot レビュー第6巡対応（2026-08-27、Claude 完結ルート）

2 指摘全て Fable 採用判定（P1×2、機械汚染防止領域）。実装・検証・返信起草は
Sonnet に委譲、コミット/push は Fable が別途実行する（本追記フェーズでは
未実施）。

1. **frame 対応関係の凍結（mora 単位集約、P1）**: lesson 側 contour と
   render 側 contour は frame 数が一般に一致しない（AX-D1 の duration
   再配分では保証的に不一致）ため、`residual_definition` が暗黙に前提と
   していた frame elementwise の RMS は未定義だった。新設トップレベル
   `residual_correspondence` に対応の単位を aligned mora へ凍結し
   （1:1 mora アラインメントは lesson 側 = HARNESS-3b の .lab×musicxml
   アラインメント、render 側 = render 入力 score の note 区間そのもの
   〔score 変換は note 数・順序を不変に保つため mora 対応は恒等〕で両側
   既知）、channel 別集約規則（`per_channel_aggregation`）を明記した:
   `relative_f0` は mora 区間内 voiced frame の算術平均（float64、voiced
   frame が両側いずれかでゼロの mora は除外し除外数を eligible 会計へ
   記録）、`normalized_energy` は phrase 正規化後の mora 区間内 block-RMS
   の算術平均、`duration_ratio`/`attack_timing`/`phrase_end_timing` は
   既に mora/phrase 単位スカラーのため対応規則は恒等。残差は
   `residual_formula` で「対応の単位=aligned mora の channel 別集約後、
   mora 単位スカラー列の差の RMS。両側 mora 数は恒等対応で常に一致——
   不一致時は実装エラーとして fail-closed 停止し比較を続行しない」旨を
   凍結した。warping・リサンプリング・truncation はいずれも不採用（発明
   しない）と明記した。5 channel の `residual_definition` を本節参照へ
   書き換えた。`run9_schema.validate_loss_evaluator_spec_manifest()` へ
   `residual_correspondence` の逐語一致検査（definition_note/unit/
   residual_formula/per_channel_aggregation 5件）を新設した。
   - **内部整合性の懸念（一次実装時に検出）と Fable 判定による解消**:
     `channels[relative_f0/normalized_energy].calibration_scale.
     derivation.decision_rationale`（99bb670b で確定、本 PR 以前からの
     既存フリーズ文言）は「scale の粒度 = 当該 channel の loss に入る
     原子残差標本の粒度」という原則のもと、「loss も frame 単位比較の
     ため frame プール（案A）を採用する — mora/phrase平均集約（案B）は
     lesson に存在しない新規導出統計を挟むため不採用」と明記していた。
     本節の mora 対応凍結は、まさにこの「新規導出統計」（mora 区間内の
     算術平均）を loss へ入る残差の原子単位として採用するものであり、
     `decision_rationale` が案Aを選んだ理由（「loss も frame 単位比較」）
     を事実として否定する内部矛盾が生じた。**Fable 判定（2026-08-27、
     第6巡内継続）**: 同じ decision_rationale が掲げる原則（「scale の
     粒度 = loss に入る原子残差標本の粒度」）が正であり、本節の mora
     集約凍結後は frame プール維持の方が原理違反——両 channel とも
     mora 粒度へ切替する。
     - `relative_f0`: W1b Task3 で実測済みの mora プール値
       （population_std=20.185403077101824, mean=-1.688564980473217,
       n=2746、voiced frame 0件の mora 2件/2748件は除外済み）へ
       calibration_scale を repin。derivation に v1（frame プール
       28.68858178404701採用、99bb670b 時点）→ 第6巡切替の経緯・
       両案の値・切替理由を正直に併記した。
     - `normalized_energy`: W1b Task3 で実測されていたのは frame プール
       （採用、166053件）と phrase プール（不採用参考、201件）の2案のみ
       で、mora プールは未実測だった。本節で追加実測: training バンドル
       （`harness_work/h3b/run1/training_bundle.json`、使用前に raw
       sha256 = `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da`
       照合——一致確認済み）から、第6巡凍結の集約規則（phrase 正規化
       〔extractor 側で phrase 内 max 正規化として実装済み・block 値に
       既に反映済み〕を先に適用した block-RMS 値の mora 区間内算術平均）
       どおりに、各 mora の [t0, t1) 区間（`education_lesson_extractor.
       py` の `parse_lab_file`/`group_lab_to_morae_with_phrases` を
       import して .lab 再パースで取得——音声デコード・RMS 再計算は
       行わず、bundle に既に保存済みの block 値をそのまま使用）に入る
       block（t_s が半開区間内）を振り分けて mora 単位スカラー列を構成、
       母標準偏差（ddof=0, numpy float64, 丸めなし）を算出した:
       population_std=0.1571927766940749, mean=0.343244778448749,
       n=2747（68 曲・全2748 mora 中、mora 区間内に block が1件も
       入らなかった mora が1件——pjs096 phrase_index=0 mora_index=1
       ——のみ除外。block 総数166053件は W1b Task3 の frame プール母数と
       完全一致——同一の block 母集団から集計していることを確認済み）。
       `energy_mora_calibration_probe.py`（session workdir 限定、repo
       非収載）を独立2回の `python3` プロセス起動で実行し、出力 JSON の
       sha256 が完全一致することを確認済み（決定論成立）。derivation に
       frame（採用済みだった旧値, n=166053）・phrase（不採用参考,
       n=201）・mora（新規実測・今回採用, n=2747）の三案を併記し、選定
       理由（同じ decision_rationale の粒度原則）を明記した。
     - 残り3 channel（duration_ratio/attack_timing/phrase_end_timing）は
       元々 mora/phrase 単位スカラーで対応規則が恒等（本節1本文冒頭
       参照）——mora 対応凍結は既存の比較粒度を変えないため、W1b Task3
       の値（変更なし）のまま整合する。各 channel の calibration_scale.
       derivation に「mora 対応凍結による影響なし・値変更不要」の旨を
       追記した。
     - `run9_schema.LOSS_EVALUATOR_CALIBRATION_SCALE_V1` の `relative_f0`/
       `normalized_energy` 値を上記の新採用値へ更新し、
       `test_h3c_loss_evaluator_calibration_matches_frozen_constant` 等
       既存テストが新値との一致を検証する形で追随した。
2. **枝別 reference_source の凍結（P1）**: 全 channel の
   `residual_definition` が旧「lesson 対 render」表記で、
   `actor_boundary.practice`（education lesson / precomputed teacher
   feature の入力禁止）と矛盾していた。新設トップレベル
   `reference_source` に比較対象を枝別凍結した: `education` = 凍結済み
   Technique lesson bundle の値（sha pin 供給）、`practice` =
   Founder-local actor が PJS training raw audio
   （`PRACTICE_ALLOWED_DATA_INPUTS.pjs_training_audio`）から同一の抽出式
   （HARNESS-3b spec v1.1 と同式）で抽出した reference 特徴
   （precomputed teacher feature の供給は引き続き禁止——抽出という行為が
   Founder 側で実行されることが要件。式の共有自体は PoR §3.2
   「同じ feature extractor コードを利用すること自体は禁止しない」
   〔`run9_schema.PRACTICE_REQUIRED_AUTONOMOUS_OPERATIONS` コメント〕に
   より actor 制約と両立）。`common` に residual 式・対応/集約規則
   （採用1）・calibration_scale・重みは両枝共通である旨、および
   `actor_boundary` 節との相互参照を明記した。5 channel の
   `residual_definition` を「reference_source 対 render の残差の RMS」へ
   書き換えた（`actor_boundary.practice`/`.education` 自体の凍結文言は
   第3巡で逐語一致検査済みのため無改訂）。
   `run9_schema.validate_loss_evaluator_spec_manifest()` へ
   `reference_source` の逐語一致検査（education/practice/common 3件）を
   新設した。PoR §3.2 の当該一文は `DESIGN_RUN9_REVISION_0.3.md` にも
   同旨の記載があり（「『同じ feature extractor コードを利用する』こと
   自体は禁止しない」）、actor 制約との両立に矛盾はない。

**連鎖更新**: `inputs/loss_evaluator_spec_v1.json` のバイト変更に伴い
`RUN9_CONTRACT.yaml` の `loss_evaluator_spec_sha` を第7世代へ repin した。
さらに本節を新設したことに伴う `HARNESS3C_AXIS_FEASIBILITY_RECORD.md`
自体の実バイト sha256 変更により、5 manifest 共通の
`provenance.detail_record.sha256` 参照値が全て追随更新となるため、
5 manifest 全て（`score_axis_catalog_sha`/`loss_evaluator_spec_sha`/
`candidate_generation_spec_sha`/`compute_budget_manifest_sha`/
`learning_data_binding_manifest_sha`）を第1-5巡と同型の cascade repin
した（旧値は `RUN9_CONTRACT.yaml` 側に世代履歴コメントとして append-only
保持）。

**検証**: `ruff check .` clean（リポジトリ全体）。
`pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`
2487 件全 pass（第5巡時点の2477件から+10件: `residual_correspondence`/
`reference_source` 逐語一致検査10件〔正常系2件・欠落/改変拒否8件〕、他
既存回帰）。

## PR #331 Codex bot レビュー第7巡対応（2026-08-27、Claude 完結ルート）

指摘1件、P2、Fable 採用判定（実装・検証・返信起草は Sonnet に委譲、
コミット/push は Fable が別途実行する。本追記フェーズでは未実施）。

**旧 frame 採用の stale 要約の掃討（P2）**: 第6巡で `residual_
correspondence` を aligned mora 単位へ凍結し `channels[relative_f0/
normalized_energy].calibration_scale`（トップレベル `value`/`sample_
unit`/`n_samples`、および `derivation.option_*.adopted_v2`）は既に
mora 粒度（`sample_unit: "mora"`, `adopted_v2: true`）へ repin 済み
だったが、以下3箇所に「Fable は frame 粒度を採用」という**非履歴文脈の
stale 要約**が残存していた:

- `README.md`（`inputs/loss_evaluator_spec_v1.json` の説明段落）:
  「両案を実測しFableがframe粒度を採用（lessonがframe契約のcontourを
  保持するため）」——第6巡切替を反映しない現況要約。
- `inputs/loss_evaluator_spec_v1.json` の `provenance.detail_record.
  summary`（本 record への言及要約）:「Fable判定（frame採用）を記録」
  ——W1b Task3 時点の判定をそのまま現況として提示していた。
- `RUN9_CONTRACT.yaml` の `loss_evaluator_spec_v1:` 冒頭サマリコメント
  （日付なし・第1巡以前から無改訂）:「Fableがframe粒度を採用（lessonが
  frame契約のcontourを保持し loss もframe単位比較のため）」——同型の
  stale 要約。

3箇所とも、W1b Task3 時点では frame 採用が事実だったが PR #331 第6巡で
`residual_correspondence` の mora 単位凍結により frame 採用の前提が
成立しなくなり mora 粒度（`sample_unit: mora` / `adopted_v2: true`）へ
切替済みである旨を明記する記述へ是正した。旧判定（frame 採用）への言及は
残したが、いずれも「W1b Task3 時点では…していたが、第6巡で…切替済み」と
明示的な履歴・訂正文脈の中でのみ残るよう書き換えた——非履歴文脈で
「frame 採用」を現況として読める記述はゼロにした。`README`/`inputs/
loss_evaluator_spec_v1.json`/`RUN9_CONTRACT.yaml`/`run9_schema.py` を
「frame」「案A」「frame-level calibration」等で grep 全数掃討し、他に
非履歴文脈の stale 記述が残存しないことを確認した（`inputs/loss_
evaluator_spec_v1.json` の `decision_rationale`（第6巡で既に history+
訂正文脈で記述済み）・`run9_schema.py` の `LOSS_EVALUATOR_CALIBRATION_
SCALE_V1` コメント（同）・本 record の W1b Task3 節と第6巡対応節
（append-only の日付付き記録であり改変不要）はいずれも要修正なし）。

`channels[].calibration_scale` の `value`/`sample_unit`/`n_samples`/
`derivation` 自体（実測値・adopted フラグ）は第6巡で既に正しく、本巡は
文書上の要約テキストのみの是正であり数値・判定結果の変更はない。

**連鎖更新**: `inputs/loss_evaluator_spec_v1.json` の `provenance.
detail_record.summary` バイト変更に伴い `RUN9_CONTRACT.yaml` の
`loss_evaluator_spec_sha` を repin した。さらに本節を新設したことに伴う
`HARNESS3C_AXIS_FEASIBILITY_RECORD.md` 自体の実バイト sha256 変更により、
5 manifest 共通の `provenance.detail_record.sha256` 参照値が全て追随
更新となるため、5 manifest 全て（`score_axis_catalog_sha`/
`loss_evaluator_spec_sha`/`candidate_generation_spec_sha`/
`compute_budget_manifest_sha`/`learning_data_binding_manifest_sha`）を
第1-6巡と同型の cascade repin した（旧値は `RUN9_CONTRACT.yaml` 側に
世代履歴コメントとして append-only 保持）。

**検証**: `ruff check .` clean（リポジトリ全体）。
`pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`
2487 件全 pass（第6巡から件数不変——文書テキストのみの是正で検証ロジック
の新設・変更なし）。

## PR #331 Codex bot レビュー第8巡対応（2026-08-27、Claude 完結ルート）

指摘3件（P2/P1/P2）、Fable 採用判定（実装・検証・返信起草は Sonnet に
委譲、コミット/push は Fable が別途実行する）。対象は
`inputs/candidate_generation_spec_v1.json` + `candidate_proposal.py` +
`run9_schema.py`。

1. **undersized L の run 前拒否ゲート（P2）**: L の有効非恒等候補数
   （score の note/phrase 構成に依存し実行時に構築される）が全提案
   スロット数（`structure.units_per_founder_per_arm` = 128）を下回ると、
   trial 2..32 の近傍・探査スロットが NOT_PROPOSABLE を頻発し、render
   数が契約を下回ったまま run が完走し得た。新設トップレベル
   `run_precondition` へ「run 開始前の前提条件として |L| ≥ 127
   （= units_per_founder_per_arm - 1、恒等スロット1件を除いた最小値）を
   要求し、不足時は fail-closed で停止する（代替挙動・予算追加・
   range 拡張のいずれも発明しない）」を凍結した。
   `candidate_proposal.require_sufficient_candidate_space()` を新設し
   （render_budget を引数に取り required_minimum = render_budget - 1 を
   下回れば `ValueError`）、`run9_schema.
   validate_candidate_generation_spec_manifest()` へ `run_precondition`
   の逐語一致検査（`_validate_candidate_generation_run_precondition()`、
   `structure.units_per_founder_per_arm - 1 == 127` の cross-check
   込み）を新設した。

2. **同一 trial 内の予約集合の凍結（P1）**: 重複回避が「評価済み」のみを
   見る旧実装では、trial 内 4 候補を一括計算する batch 提案と、1件ずつ
   render/評価してから次を計算する逐次提案とで trace が分岐し得た
   （近傍スロットで選ばれた候補がまだ評価されていないため、同一 trial
   内の探査スロットの digest 由来プロービングがそれを重複して選び得た）。
   `proposal.reservation_semantics` を新設し、「trial 内の候補提案は
   candidate_index 0 -> 1 -> 2 -> 3 の逐次順で行い、各候補は提案された
   時点で（render/評価を待たず）予約集合（proposed-or-evaluated）へ
   加える。近傍列挙・探査規則の線形プロービングとも予約集合をスキップ
   対象とする」旨を凍結した。`candidate_proposal.
   propose_trial_candidates()` を trial-level 参照実装として新設した
   （candidate_index 逐次順で近傍優先度リストと探査規則を組み合わせ、
   予約集合を都度更新する）。実測で seed=909002/arm="arm-a"/
   founder_id="R9F-01"/current_best=("AX-P1",1,1.0) の3-note fixture
   において、trial=26 の candidate_index=3 の探査 digest が初期 index で
   近傍候補0番目（`("AX-P1",1,1.5)`）と衝突するケースを発見し、旧
   「評価済みのみ」semantics（is_acceptable=常に True）ではこの衝突が
   そのまま重複選出されること、`propose_trial_candidates()`（予約集合
   semantics）では線形プロービングで次候補（`("AX-P1",2,-2.0)`）へ
   進み重複が起きないことを直接検証した——batch（`propose_trial_
   candidates()` を1回呼ぶ）と逐次（近傍キュー・探査プロービングを
   candidate_index ごとに手動シミュレートする、同一の予約集合更新規則を
   使う）が同一の4件を再現することも確認した。

3. **spec リテラル domain の catalog 連合 cross-check（P2）**:
   catalog（`score_axis_catalog_v1.json`）が正当な理由で repin されて
   `axes.AX-P1`/`axes.AX-D1` の値が変わっても、`candidate_generation_
   spec_v1.json` 側のリテラル domain/step/min-duration 記述
   （`ax_p1.offset_domain`/新設 `ax_d1.quantization_step_beats`/
   `ax_d1.min_duration_beats`）は追随せず旧値のまま両 loader
   （`validate_candidate_generation_spec_manifest()`/`load_pinned_
   score_axis_catalog_manifest()`）を通過し得た——旧 validator は
   `candidate_ordering` 直下キーの存在のみを検査し、`ax_p1`/`ax_d1`
   サブキーの値自体は一切検査対象外だった。`ax_d1` へ
   `quantization_step_beats`（0.25）・`min_duration_beats`（0.25）の
   リテラル数値フィールドを新設し、`run9_schema.
   load_pinned_candidate_generation_spec_manifest()` へ `_candidate_
   generation_cross_check_axis_catalog()` を新設した。本関数は
   `load_pinned_score_axis_catalog_manifest()`（catalog pin の唯一の
   正規消費経路）経由で pinned catalog をロードし、
   `candidate_proposal.ax_p1_offset_domain_from_catalog()`
   （`_ax_p1_offset_domain()` の公開ラッパー、単一情報源）が catalog
   から独立に再導出した offset_domain と spec 側のリテラル
   `offset_domain` を比較、また `ax_d1.quantization_step_beats`/
   `min_duration_beats` を catalog の対応値と直接比較し、いずれかが
   不一致なら fail-closed で拒否する。`validate_candidate_generation_
   spec_manifest()` 側にも `ax_p1`/`ax_d1` サブキーの存在・型検査
   （catalog 非依存の構造検査のみ）を新設した。

**連鎖更新**: `inputs/candidate_generation_spec_v1.json` の内容変更
（`run_precondition` 新設・`proposal.reservation_semantics` 新設・
`ax_p1`/`ax_d1` への `catalog_cross_check_note` 追加・`ax_d1` への
`quantization_step_beats`/`min_duration_beats` 追加）に伴い
`RUN9_CONTRACT.yaml` の `candidate_generation_spec_sha` を repin した。
さらに本節を新設したことに伴う `HARNESS3C_AXIS_FEASIBILITY_RECORD.md`
自体の実バイト sha256 変更により、5 manifest 共通の `provenance.
detail_record.sha256` 参照値が全て追随更新となるため、5 manifest 全て
（`score_axis_catalog_sha`/`loss_evaluator_spec_sha`/
`candidate_generation_spec_sha`/`compute_budget_manifest_sha`/
`learning_data_binding_manifest_sha`）を第1-7巡と同型の cascade repin
した（旧値は `RUN9_CONTRACT.yaml` 側に世代履歴コメントとして
append-only 保持）。`score_axis_catalog_v1.json`/`loss_evaluator_spec_
v1.json`/`compute_budget_manifest_v1.json`/`learning_data_binding_
manifest_v1.json` 自体の内容（`provenance.detail_record.sha256` 以外）
は本巡で無改訂。

**検証**: `ruff check .` clean（リポジトリ全体）。
`pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`
全 pass（新設検査・参照実装テストの追加分だけ第7巡から件数増）。

## PR #331 Codex bot レビュー第10巡対応（2026-08-27、Claude 完結ルート）

指摘1件、P1、Fable 採用判定（機械汚染防止領域）。実装・検証・返信起草は
Sonnet に委譲、コミット/push は Fable が別途実行する（本追記フェーズでは
未実施）。

1. **aggregate loss の実行可能な結合式の凍結（P1）**: `aggregate_scope`
   （aggregate の使用範囲を candidate selection 用 search objective に
   限定する旨）は第1巡以前から凍結済みだったが、channel RMS・
   calibration_scale・weight を「どう結合するか」の式そのものは未凍結
   だったため、実装ごとに異なる best が生じ得た。新設トップレベル
   `aggregate_formula` に実行可能な式を逐語凍結した:
   `search_objective(candidate) = Σ_{c∈measurable} weight_c ×
   (residual_RMS_c / calibration_scale_c.value)`。`measurable` は
   当該 candidate で eligible > 0 の channel 集合とし、
   `missing_policy.not_measurable_definition` の候補単位 NOT_SCORABLE
   規則が優先する（いずれかの channel が eligible == 0 なら candidate
   自体が NOT_SCORABLE として記録され、本式はその候補について評価
   されない）旨を明記して両節を相互参照させた。各項の定義:
   `weight_c` = 0.2（全 channel 固定、裁定 §2「正規化後の固定重みを
   各 1/5 とする」により既凍結、`channels[].weight` 参照）、
   `residual_RMS_c` = `residual_correspondence` 節が定義する channel c
   の残差 RMS（relative_f0/normalized_energy/duration_ratio/
   attack_timing は mora 単位、phrase_end_timing のみ phrase 単位、
   `residual_correspondence.residual_formula`/`per_channel_aggregation`
   参照）、`calibration_scale_c.value` = 当該 channel の
   `calibration_scale.value`（training split のみから決定論導出済み、
   凍結済み値）。演算は `dtype: float64`、加算順序は `channels` 配列の
   記載順（relative_f0, duration_ratio, normalized_energy,
   attack_timing, phrase_end_timing）で固定し、浮動小数点の結合順まで
   決定論化した（`summation_order`）。`objective_direction` に
   search_objective が最小化目的であること、
   `candidate_generation_spec_v1.json` の `selection.objective`/
   `selection.tie_break`（`(objective, candidate_ordinal)`）との接続、
   `aggregate_scope`/`final_scientific_judgment_note` との相互参照を
   明記した。`run9_schema.validate_loss_evaluator_spec_manifest()` へ
   `_validate_loss_evaluator_aggregate_formula()` を新設し、
   `aggregate_formula` の逐語一致検査（formula・measurable_definition・
   term_definitions 3件・dtype・summation_order・objective_direction）
   を追加した——calibration_scale による正規化を落とした省略形など
   未凍結の別式への repin 差し替えを fail-closed で拒否する。

**連鎖更新**: `inputs/loss_evaluator_spec_v1.json` のバイト変更に伴い
`RUN9_CONTRACT.yaml` の `loss_evaluator_spec_sha` を repin した。さらに
本節を新設したことに伴う `HARNESS3C_AXIS_FEASIBILITY_RECORD.md` 自体の
実バイト sha256 変更により、5 manifest 共通の `provenance.
detail_record.sha256` 参照値が全て追随更新となるため、5 manifest 全て
（`score_axis_catalog_sha`/`loss_evaluator_spec_sha`/
`candidate_generation_spec_sha`/`compute_budget_manifest_sha`/
`learning_data_binding_manifest_sha`）を第1-8巡と同型の cascade repin
した（旧値は `RUN9_CONTRACT.yaml` 側に世代履歴コメントとして
append-only 保持）。`score_axis_catalog_v1.json`/`candidate_generation_
spec_v1.json`/`compute_budget_manifest_v1.json`/`learning_data_binding_
manifest_v1.json` 自体の内容（`provenance.detail_record.sha256` 以外）
は本巡で無改訂。

**検証**: `ruff check .` clean（リポジトリ全体）。
`pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`
2542 件全 pass（第9巡時点の2532件から+10件: `aggregate_formula` 逐語一致
検査10件〔正常系1件・欠落/改変拒否9件〕、他既存回帰）。

## PR #331 Codex bot レビュー第11巡対応（2026-08-27、Claude 完結ルート）

指摘2件、いずれも P1、いずれも Fable 採用判定（機械汚染防止領域）。上限
10巡（`AGENTS.md` §3-4／CLAUDE.md「上限10ラウンド」）到達後の指摘だが、
3分類（実コード被害/将来汚染・致命的バグ）のうち「致命的バグ」（指摘1=
評価時挙動が未定義のまま仕様矛盾を放置すれば eligible 会計が実装依存で
分岐する／指摘2=偽成功経路——実際には何も学習できていない run を恒等勝者
であるかのように記録し得る）に該当する新規具体経路であるため、打ち切り
（CLAUDE.md「打ち切りは3分類を上書きしない」）の対象外として採用する。
実装・検証・返信起草は Sonnet に委譲、コミット/push は Fable が別途実行
する（本追記フェーズでは未実施）。

1. **normalized_energy のペア除外規則の未定義是正（P1）**:
   `residual_correspondence.per_channel_aggregation.relative_f0` は
   「voiced frame が両側いずれかでゼロの mora は当該 channel の比較から
   除外し、除外数を eligible 会計へ記録する」というペア除外規則を既に
   凍結していたが、同節の `normalized_energy` にはこの規則が存在せず、
   lesson 側 mora に energy block が1つも無い場合の評価時挙動（空平均で
   NaN になるのか、mora ごと破棄するのか）が未定義のまま残っていた——
   `calibration_scale.derivation.option_c_mora_pool` は pjs096 の
   zero-block mora 1件を除外した実測（n=2747）を記録していたにも
   かかわらず、この除外が評価時のどの規則に基づくものか本文からは
   読み取れなかった。`normalized_energy` の集約規則を二段構造へ逐語
   凍結し直した: (a) 構造検査 = aligned mora 数の両側完全一致（既存
   fail-closed を維持）、(b) channel 固有 eligibility = lesson 側・
   render 側の双方に energy block が1件以上存在する mora のみ eligible
   とする（ペア除外——relative_f0 の voiced frame ペア規則と同型）。
   除外 mora の index 列挙を trial 記録へ必須収載し、eligible 件数は
   ペア除外後の件数と明記、`missing_policy.not_measurable_definition`
   （eligible==0 の candidate は NOT_SCORABLE）と接続した。calibration
   導出欄の pjs096 除外注記にも、当該除外が本ペア除外規則の lesson 側
   単独適用と一致する旨を追記した。`run9_schema.
   _LOSS_EVALUATOR_EXPECTED_RESIDUAL_CORRESPONDENCE_PER_CHANNEL
   ["normalized_energy"]` を新文言と逐語一致させ（既存の per_channel
   汎用ループ検査がそのまま適用される）、
   `test_h3c_loss_evaluator_residual_correspondence_normalized_energy_
   pairing_rule_tamper_rejected` を新設して規則を落とした旧文言への
   改ざんが拒否されることを確認した。

2. **best 不在と恒等候補の混同の是正（P1）**: `candidate_proposal.py` の
   `neighbors_of()`/`propose_trial_candidates()` は `current_best:
   Optional[Candidate]` の `None` を恒等（identity）専用の意味で使って
   いたが、`missing_policy` が定める「trial 内の全 candidate が
   NOT_SCORABLE の場合、best 更新なしで次 trial へ進む」規則により、
   trial 1 の恒等候補を含む全4候補が NOT_SCORABLE となった場合（または
   それ以降も scorable な候補が一度も確定しない場合）、「best が定まって
   いない」状態を表す値が存在しなかった——`None` を「恒等が best」と
   「best 不在」の両方に流用すると、`neighbors_of()` がこれを取り違え、
   一度も scorable と確認されていない恒等の近傍を生成してしまう
   （架空の best からの探索という偽成功経路）。コードを実読し、
   `candidate_proposal.py` 自体には現時点で全4候補 NOT_SCORABLE の実行
   ループ（best 追跡込みの generator）はまだ配線されていない
   （モジュール docstring が明記する既知のスコープ境界: 「実際の探索
   ループ...は本モジュールの対象外であり別途配線する」）ことを確認した
   うえで、`current_best` の型契約そのものに存在した欠陥（`None` の
   多義性）を是正した——将来配線される generator が同じ欠陥を踏まない
   ための型レベルの予防措置である。`NO_BEST` sentinel（`_NoBestSentinel`
   クラスの単一インスタンス、`IDENTITY`=`None` とは別個の id）を新設し、
   `neighbors_of()` は `current_best is NO_BEST` の場合、近傍候補集合を
   常に空リストで返す（近傍3スロットは全欠 = shortfall、既存の
   shortfall 経路が `exploratory_candidate_rule` で4枠すべてを決定論的に
   充当する）。恒等候補の暗黙の再提案はしない——恒等は trial1_candidate0_
   rule が定める trial 1 candidate 0 の1回のみで、NO_BEST から自動的に
   恒等へ復帰することはない。`candidate_generation_spec_v1.json`
   `proposal.neighborhood_candidate_rule` へ `no_best_handling` を新設し
   （NO_BEST の定義・shortfall 全欠・mix 記録への理由
   "NO_SCORABLE_BEST" の必須収載を凍結）、`current_best_definition`/
   `current_best_may_be_identity` にも NO_BEST との区別を明記する改訂を
   加えた。さらに `selection` 節へ
   `no_scorable_candidate_terminal_state` を新設し、32 trial 終了時点
   まで一度も scorable な candidate が確定しなかった場合の run 終端状態
   `NO_SCORABLE_CANDIDATE`（勝者なし・暗黙の恒等採用なし・当該
   Founder/arm の学習結果を正直に記録する）を凍結した。`run9_schema.
   validate_candidate_generation_spec_manifest()` へ
   `current_best_definition`/`current_best_may_be_identity`/
   `no_best_handling`/`no_scorable_candidate_terminal_state` の逐語一致
   検査を新設した（旧版はこれらのキーの存在のみを検査し中身は無検査
   だった欠落を埋める）。`tests/test_candidate_proposal.py` へ
   バグ再現系（`test_neighbors_of_none_still_means_identity_not_
   absence`／`test_neighbors_of_no_best_returns_empty_list_not_
   identity_neighbors`）・NO_BEST 挙動（`test_no_best_is_not_none`／
   `test_select_neighborhood_candidates_no_best_returns_empty`／
   `test_propose_trial_candidates_no_best_backfills_all_four_via_
   exploratory`／`test_propose_trial_candidates_no_best_deterministic_
   across_two_calls`）・NO_SCORABLE_CANDIDATE 終端の直接検証
   （`test_propose_trial_candidates_no_best_never_revives_identity_
   across_full_run`、127要件を満たす136要素 fixture で32 trial 完走
   させ恒等が trial 1 の1回のみであることを確認）を追加した。

**連鎖更新**: `inputs/loss_evaluator_spec_v1.json`（指摘1）・
`inputs/candidate_generation_spec_v1.json`（指摘2）のバイト変更に伴い
`RUN9_CONTRACT.yaml` の `loss_evaluator_spec_sha`/`candidate_generation_
spec_sha` を repin した。さらに本節を新設したことに伴う
`HARNESS3C_AXIS_FEASIBILITY_RECORD.md` 自体の実バイト sha256 変更により、
5 manifest 共通の `provenance.detail_record.sha256` 参照値が全て追随
更新となるため、5 manifest 全て（`score_axis_catalog_sha`/
`loss_evaluator_spec_sha`/`candidate_generation_spec_sha`/
`compute_budget_manifest_sha`/`learning_data_binding_manifest_sha`）を
第1-10巡と同型の cascade repin した（旧値は `RUN9_CONTRACT.yaml` 側に
世代履歴コメントとして append-only 保持）。`score_axis_catalog_v1.json`/
`compute_budget_manifest_v1.json`/`learning_data_binding_manifest_v1.json`
自体の内容（`provenance.detail_record.sha256` 以外）は本巡で無改訂。

**検証**: `ruff check .` clean（リポジトリ全体）。
`python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q
--tb=short` 全 pass（新設検査・参照実装テストの追加分だけ第10巡から件数
増）。

## PR #331 Codex bot レビュー第12巡対応（2026-08-27、Claude 完結ルート）

指摘1件、P1、Fable 採用判定（致命的バグ = 第11巡改訂が導入した spec 内
矛盾）。上限10巡（`AGENTS.md` §3-4／CLAUDE.md「上限10ラウンド」）到達後の
指摘だが、CLAUDE.md「打ち切りは3分類を上書きしない」（新しい具体経路を
示す指摘は巡数に依らず採用）に該当するため採用する。実装・検証・返信
起草は Sonnet に委譲、コミット/push は Fable が別途実行する（本追記
フェーズでは未実施）。

1. **exploratory_candidate_rule.applies_to の適用範囲宣言の是正（P1）**:
   第11巡で `neighborhood_candidate_rule.no_best_handling` を新設した際、
   NO_BEST（current_best 不在）の trial では「candidate 0..3 の4枠すべて
   を exploratory_candidate_rule で決定論的に充当する」と規定したが、同じ
   `proposal.exploratory_candidate_rule.applies_to` は第1巡以来「trial 1
   の candidate 1..3、および trial 2..32 の candidate 3」にしか触れて
   おらず、trial 2..32 の candidate 0..2 への適用（NO_BEST 時の全欠
   バックフィルに加え、第3/4巡で凍結済みの `shortfall_handling` が定める
   近傍優先順位リスト不足分の通常バックフィルも同様）を宣言していな
   かった——適用範囲を宣言する節自体が、それを呼び出す2つの規則
   （`no_best_handling`/`shortfall_handling`）と矛盾する spec 内矛盾
   だった。参照実装 `candidate_proposal.propose_trial_candidates()` を
   実読し、shortfall 発生時（`candidate_index` が 0..2 のいずれかで近傍
   キューが尽きた場合）に `select_exploratory_candidate()` を**実際の
   `candidate_index`（0/1/2）のまま**呼び出しており 3 へ差し替えていない
   ことを確認した——正しいのは実装側で、`applies_to` の適用範囲宣言だけ
   が誤って狭かった。`applies_to` を (a) 正規スロット（trial 1
   candidate 1..3・trial 2..32 candidate 3）+ (b) `shortfall_handling`/
   `no_best_handling` がバックフィルを要求する近傍スロット（trial 2..32
   candidate 0..2）の2項列挙へ逐語改訂し、(a)(b) いずれのスロットにも
   `reservation_semantics` が定める予約集合・`probing_rule` の線形
   プロービング・重複棄却が同一適用される旨を明記した。双方向参照として
   `no_best_handling`（既存の「exploratory_candidate_rule（探査ストリーム、
   hash 系列）」の直後）と `shortfall_handling`（既存の「exploratory_
   candidate_rule の手順」の直後）にもそれぞれ `exploratory_candidate_
   rule.applies_to (b) が定める` の一句を追記した。`run9_schema.py` へ
   `_CANDIDATE_GENERATION_EXPECTED_EXPLORATORY_APPLIES_TO`/
   `_CANDIDATE_GENERATION_EXPECTED_SHORTFALL_HANDLING` を新設し、
   `validate_candidate_generation_spec_manifest()` に
   `exploratory_candidate_rule.applies_to`/`neighborhood_candidate_rule.
   shortfall_handling` の逐語一致検査を追加した（旧版はいずれも必須
   キーの存在のみを検査し中身は無検査だった欠落を埋める）。
   `tests/test_h3c_learning_recipe_manifests.py` へ
   `test_h3c_candidate_generation_exploratory_applies_to_narrowed_to_
   candidate3_only_rejected`（旧・矛盾していた狭い文言への改ざん拒否）・
   `test_h3c_candidate_generation_shortfall_handling_applies_to_cross_
   reference_dropped_rejected`（相互参照を落とした shortfall_handling
   への改ざん拒否）を新設した。`tests/test_candidate_proposal.py` の
   既存 `test_propose_trial_candidates_shortfall_backfilled_by_
   exploratory`／`test_propose_trial_candidates_no_best_backfills_all_
   four_via_exploratory` が、参照実装 `candidate_proposal.py` の shortfall
   バックフィル挙動（candidate 0..2 スロットが exploratory ストリームで
   充当される）を既に実測確認済みであり、本巡の spec 文言改訂はこの
   既存実装挙動と整合したことを確認した（実装側の変更は無し）。

**連鎖更新**: `inputs/candidate_generation_spec_v1.json` のバイト変更に
伴い `RUN9_CONTRACT.yaml` の `candidate_generation_spec_sha` を repin
した。さらに本節を新設したことに伴う `HARNESS3C_AXIS_FEASIBILITY_
RECORD.md` 自体の実バイト sha256 変更により、5 manifest 共通の
`provenance.detail_record.sha256` 参照値が全て追随更新となるため、5
manifest 全て（`score_axis_catalog_sha`/`loss_evaluator_spec_sha`/
`candidate_generation_spec_sha`/`compute_budget_manifest_sha`/
`learning_data_binding_manifest_sha`）を第1-11巡と同型の cascade repin
した（旧値は `RUN9_CONTRACT.yaml` 側に世代履歴コメントとして
append-only 保持）。`score_axis_catalog_v1.json`/`loss_evaluator_spec_
v1.json`/`compute_budget_manifest_v1.json`/`learning_data_binding_
manifest_v1.json` 自体の内容（`provenance.detail_record.sha256` 以外）
は本巡で無改訂。

**検証**: `ruff check .` clean（リポジトリ全体）。
`python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q
--tb=short` 2552 件全 pass（第11巡時点の2550件から+2件: `applies_to`/
`shortfall_handling` 逐語一致検査の改ざん拒否テスト2件、他は repin 反映
後の既存回帰）。
