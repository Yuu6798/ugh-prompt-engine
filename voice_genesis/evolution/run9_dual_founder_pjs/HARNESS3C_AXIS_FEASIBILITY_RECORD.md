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
