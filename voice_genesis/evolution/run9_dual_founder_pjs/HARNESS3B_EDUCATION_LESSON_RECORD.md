# RUN9-L0-HARNESS-3b Education Technique Lesson Record

（起草: 2026-08-27、Claude 完結ルート — User 裁定「RUN9 User裁定 —
PJS再取得およびLesson Channel Freeze」（2026-08-27、repo 内収載
[`USER_ADJUDICATION_20260827_PJS_LESSON_FREEZE.txt`](./USER_ADJUDICATION_20260827_PJS_LESSON_FREEZE.txt)）
に基づく PJS Technique lesson バンドル実測記録。spec 正本 =
[`HARNESS3B_EXTRACTOR_SPEC.md`](./HARNESS3B_EXTRACTOR_SPEC.md)（v1.1、凍結対象）。
HARNESS1_PROVISION_RECORD.md / HARNESS2_REEXPORT_SMOKE_RECORD.md /
HARNESS3A_SPEAKER_MAP_RECORD.md と同格の記録文書。

workdir（repo 外、session scratchpad）: `<session workdir（repo外）>/harness_work/h3b/`
（PJS raw zip・展開済み音源・venv_h3b を隔離。実音源・音源由来の生 WAV
バイトは一切 repo にコミットしていない——`git status --porcelain` で
本フェーズ開始前の repo 側の変更ゼロを確認済み）。

---

## 総合判定

**E1（偵察）2 点 PASS → v1 spec 停止（正しい挙動）→ Fable 設計判定で
spec を v1.1 へ訂正 → v1.1 で再凍結 → PJS 85 曲抽出を独立 2 回（workdir、
run1/run2）完遂・byte 一致 → repo canonical builder
（`education_lesson_builder.py`）による独立 3 回目の再現実行（run3）で
byte 一致 → schema 検証 4/4 PASS。FAIL なし。**

| # | 検証項目 | 判定 |
|---|---|---|
| 1 | E1: raw zip SHA256 一致 | PASS |
| 2 | E1: expanded corpus identity SHA256 一致 | PASS |
| 3 | v1 spec 下 WAV ヘッダ全数実測 → fail-closed 停止（16-bit 要求 vs 実測 24-bit、0/85 一致） | 停止（正しい挙動） |
| 4 | v1.1 spec 訂正 + 再凍結（音響 decode は v1 下で一度も実行されていない — 「凍結が抽出に先行」不変条件保持） | 完了 |
| 5 | v1.1 下 WAV ヘッダ全数実測（85/85 一致） | PASS |
| 6 | run1/run2（workdir、独立プロセス） training/validation バンドル byte 一致 | PASS |
| 7 | run3（repo canonical builder、独立 3 回目）training/validation バンドル byte 一致（run1/run2 と完全一致） | PASS |
| 8 | schema 検証（`run9_schema.validate_lesson_record()`、repo python3）run1/run2 × training/validation 4 本 | 4/4 PASS |

---

## 0. 対象・入力・出力

- 対象範囲: PJS corpus ver1.1、practice split の **training 70 曲 + validation 15 曲**
  のみ（`inputs/practice_audio_split_manifest.json` `row_ids.training`/
  `row_ids.validation`）。sealed_holdout 15 曲は完全性 hash・ID 確認以外
  一切処理していない（裁定 §2）。
- 消費入力（曲あたり3点）: `pjsNNN_song.wav` + `pjsNNN.lab` + `pjsNNN.musicxml`
  （spec §0、score 正本 = musicxml。`.mid`/`pjs015.xml`/`.txt`/`_speech.wav`/
  `background_noise/` は非消費）。
- 出力: training/validation 各1本の JSON バンドル（`run9-technique-lesson-
  bundle/1.0`）。**バンドル実体ファイルは rights 制約により repo に
  コミットしない**（実 PJS 音源からの derived artifact）——本記録が保持
  するのは sha256 という証跡のみで、`inputs/education_technique_lesson_
  manifest.json` へ pin する。バンドルは本 builder を使い決定論的に
  いつでも再導出できる（session-artifact 扱い、reexport emb と同型の
  会計方針）。

## 1. E1（偵察）— 2 点 PASS

`e1_recon_report.md`（workdir）参照。raw zip sha256 =
`683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca`、
expanded corpus identity sha256 =
`9905cec08fbaf43fa545400498a7908ef28567e8f60a5ba005fb2e00d526f996`、
いずれも裁定 §1 の要求値と厳密一致（PASS）。

## 2. v1 spec 下の停止 → v1.1 訂正

spec v1 §2-1 は WAV を 16-bit と仮定していたが、E2 の初回実行で全 85 曲の
WAV ヘッダを metadata のみで実測（decode なし）したところ 85/85 曲が
24-bit/mono/48000Hz であり、16-bit に一致した曲は 0 だった。spec v1 の
明文規定（不一致があれば即停止・変換規則を発明しない）に従い、PJS 音源の
decode には一度も進まずに停止した（旧 freeze record =
`inputs/h3b_freeze_record.superseded.1.json` の `stop_event` に保存、repo
byte-identical コピー収載済み）。

コーディネーターより設計判定を受領: 停止は正しい挙動、24-bit 全数実測は
spec 側の物理層仮定の誤り。channel 意味論・抽出法・正規化法は無変更のため
裁定 §5 の design revision 事項に該当しない。spec を v1.1 へ訂正
（`HARNESS3B_EXTRACTOR_SPEC.md` §2-1 は 24-bit を要求し、24-bit 手動 byte
復号式を明示的に pin している）。音響抽出は v1 凍結下で一切実施されて
いないため、「凍結が抽出に先行」の不変条件を保ったまま v1.1 で再凍結した。

## 3. 依存確定

```
python:  3.11.15
pyworld: 0.3.5
numpy:   1.26.4
scipy:   1.17.1
```

venv: `<workdir>/venv_h3b`（repo・システム python には一切触れていない。
pyworld 0.3.5 は numpy>=2 と非互換のため事前に numpy<2 へ pin）。

## 4. freeze record（v1.1、`inputs/h3b_freeze_record.json`）

repo へ byte-identical コピー収載（`h3b_freeze_record.superseded.1.json`
＝ v1 も破棄せず収載、正直会計）。

```
metric_version    = h3b-extractor-spec/1.1
spec_sha256       = 8f78ccdb275a9acca6b08ec75535d26863bb0464c6e23150829146339f2ff39c
extractor_sha256  = ba972ba70906f9d7387fafdb314391863dae4c5d4f216b9f935254a36e8cb4f5
  （HARNESS-3b 実測当時の session workdir education_lesson_extractor.py の
  履歴的 sha256 — 本 repo 収載 freeze record は byte-identical コピーで
  あり値を書き換えていない。repo canonical builder
  education_lesson_builder.py は後続で新設した別ファイルのため必然的に
  別バイト列であり、この履歴的値と一致しない — 詳細は §7 参照）
supersedes        = h3b_freeze_record.superseded.1.json
wav_header_requirement = {audio_format_pcm:1, channels:1, sample_rate:48000, bits_per_sample:24}
```

## 5. WAV ヘッダ全数再実測（v1.1 要求との照合）

全 85 曲（training 70 + validation 15）を `probe-header` で再実測
（RIFF/fmt チャンクのみ、decode なし）: **85/85 曲全数が spec v1.1 の
要求と一致**（`wav_header_probe_85_v1_1.json`、workdir）。

## 6. 抽出（workdir、独立 2 回：run1/run2）

`education_lesson_extractor.py`（workdir 版）+ `run_batch_extract.py`
（バッチドライバ）を、run1/run2 それぞれ別の Bash 呼び出し・別プロセスで
実行（中間 JSON もディレクトリを分離し一切共有していない）。

| バンドル | run1 sha256 | run2 sha256 | 一致 |
|---|---|---|---|
| training | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | **PASS** |
| validation | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | **PASS** |

`cmp` によるバイト単位の直接差分照合でも差分 0（完全一致）を確認済み。
バンドルサイズ: training 20,338,375 bytes, validation 3,755,277 bytes。

### aligned / count_mismatch

| split | aligned | count_mismatch | song_id |
|---|---|---|---|
| training (70曲) | 68 | 2 | `pjs008`, `pjs064` |
| validation (15曲) | 15 | 0 | （なし） |
| **合計 (85曲)** | **83** | **2** | |

- **pjs008**: `lab_mora_count=39 != score_mora_count=40`。原因は補間・推測
  で埋めず、件数・song_id のみを正直に記録する（原コーパスの
  score/annotation 間の食い違いと推測されるが、原因のさらなる特定は
  行わない）。
- **pjs064**: `lab_mora_count=34 != score_mora_count=35`。全 100 曲中
  唯一 `<backup>`/`<forward>` と 2 つ目の `<voice>2</voice>` を持つ曲——
  voice を区別せず全 `<note>` を時刻順に採用する spec 準拠実装により、
  重複 voice が score 側 mora 数を +1 し count_mismatch として正しく
  検出・除外された。

aligned 83 曲は全曲・4 channel（relative_F0/duration_ratio/
energy_envelope/onset_offset）すべて `extracted`。空バンドル生成による
成功偽装の懸念なし。

## 7. repo canonical builder への統合（`education_lesson_builder.py`）

workdir 版 `education_lesson_extractor.py`（抽出ロジック本体）+
`run_batch_extract.py`（バッチドライバ）を単一の checkout-stable
canonical builder へ統合した。**抽出式・アラインメント規則・直列化
（バンドルへ書き込まれる文字列リテラル込み）は一切変更していない** —
バンドル byte 再現性が要件のため、`build_lesson_record()` の
`rights_manifest`/`provenance_manifest` および `RIGHTS_STATUS_DECLARATION`
の文言（workdir 相対パスの言い回しを含む、履歴的な run-scoped 文言）も
逐語のまま維持した。変更したのはパス解決（workdir 絶対パス → CLI 引数 +
repo 相対既定値）と CLI 構造（2 ファイル → 1 ファイル、`build` サブ
コマンド新設）のみ。

**`freeze_selfcheck()` の意味論変更（唯一の意図的差分）**: workdir 版は
spec sha256 **と** 自身のコード sha256（`education_lesson_extractor.py`
自身の実バイト）の両方を freeze record と照合していた。本 builder は
spec sha256 照合のみを保持し、自身のコード sha256 自己照合は行わない —
freeze record の `extractor_sha256` は HARNESS-3b 実測当時の session
workdir `education_lesson_extractor.py` の履歴的 sha256（`ba972ba7...`、
§4 参照）であり、本 builder は後で新設された別ファイル（別名・別 CLI
構造、必然的に別バイト列）であるため一致し得ない。「凍結が抽出に先行」
という spec §6 の不変条件は spec sha256 照合で引き続き機械強制される。
本 builder 自身の identity は freeze record ではなく
`inputs/education_technique_lesson_manifest.json` の
`builder_provenance.builder_sha256` が別途担い、
`run9_schema.load_pinned_education_lesson_manifest()` が実バイト sha256
と照合する（下記 §9）。

sealed_holdout row_ids はいかなるコードパスにも現れない
（`load_training_validation_ids()` は `row_ids.training`/
`row_ids.validation` のみ参照）。advisory 6 channel のコードパスは実装
していない。corpus 統計正規化は実装していない。librosa import なし、
svp_rpe/voice_genesis の実装モジュール import なし（定数は Read による
転記のみ）。

**pyworld のオプショナル import 化**（パス解決・CLI 化に付随する適応、
抽出式は無変更）: 本リポジトリの標準テスト/lint 環境には pyworld が
インストールされていない（`venv_h3b` のみに分離 install 済み——§3 参照）。
workdir 版は module 冒頭で `import pyworld as pw` を無条件実行しており、
そのまま repo へ持ち込むと repo テストスイート全体から本 builder を
import できなくなる（schema 定数・`assemble_bundle()`・
`freeze_selfcheck()` など音声処理を伴わない機能まで道連れで
import-error になる）。`voice_genesis/foundry/adapter/donor_bank.py` 系列
が既に採用している repo 規約
（`try/except ModuleNotFoundError` でフラグ化し、実際に必要な呼び出し点
でのみ fail-fast する——`artificial_founder/tests/test_artificial_founder_p0.py`
の `requires_world` skipif パターンと同型）に倣い、`import pyworld` を
`try/except ModuleNotFoundError` で囲み `PYWORLD_AVAILABLE` フラグ化した。
`compute_world_f0()`（pyworld を実際に呼ぶ唯一の関数）だけがこのフラグを
検査し、未インストール時は明示的な `ModuleNotFoundError` を送出する。
pyworld が利用可能な環境（venv_h3b 等）での挙動・数値は一切変更していない
（`PYWORLD_AVAILABLE=True` のときの分岐は workdir 版と同一コード）。

repo 収載 builder の実バイト sha256:
```
550eda93cf9e1cbd9f95a2525db03cda73d753d63e4df79003d0757acef4e8ae
```

## 8. run3: repo builder による再現実行（独立 3 回目）

venv_h3b の python3 で repo builder（上記 sha256、`build` サブコマンド）を
workdir 展開済み corpus（`expanded/PJS_corpus_ver1.1`）に対し実行した。
freeze record・spec・split manifest はいずれも repo 収載の既定パス
（`inputs/h3b_freeze_record.json` / `HARNESS3B_EXTRACTOR_SPEC.md` /
`inputs/practice_audio_split_manifest.json`）を使用（CLI 引数省略、
デフォルト値のまま）。

```
$ python3 education_lesson_builder.py build \
    --corpus-root <workdir>/expanded/PJS_corpus_ver1.1 \
    --out-dir <workdir>/run3
real 4m22.124s
```

| バンドル | run1/run2 sha256 | run3 sha256 | 一致 |
|---|---|---|---|
| training | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | **PASS** |
| validation | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | **PASS** |

`cmp run1/training_bundle.json run3/training_bundle.json` / `cmp
run1/validation_bundle.json run3/validation_bundle.json` ともに差分 0
（完全一致）を確認済み。**「fresh checkout から実測を再現できる」契約を
満たした**（試行 1 回目は逐語移植の際に文字列リテラルを誤って
repo 相対の言い回しへ書き換えてしまい sha256 不一致となったため、§7 の
逐語文言を workdir 版へ復元して再実行し一致を得た——正直会計として記録
する）。

## 9. schema 検証（実 PJS バンドル、repo python3、read-only）

run1/run2 × training/validation = 4 本の `lesson_record` すべてに対し、
repo のシステム python3 で `run9_schema.validate_lesson_record()` を実行:

| バンドル | 結果 |
|---|---|
| run1/training | **PASS** |
| run1/validation | **PASS** |
| run2/training | **PASS** |
| run2/validation | **PASS** |

repo への書き込みは一切なし（この検証は Read-only 実行）。

## 10. 禁止事項の遵守確認

- sealed_holdout 15 曲: `education_lesson_extractor.py`（workdir 版）/
  `education_lesson_builder.py`（repo canonical builder）/
  `run_batch_extract.py` のどのコードパスにも sealed_holdout の row_id は
  現れない。decode/特徴抽出/lesson 生成/試聴は一切行っていない。
- advisory 6 channel（vibrato/breath_placement/release_persistence/
  terminal_mel_persistence/HNR/vowel_drift）のコードパスは実装していない。
- corpus 統計正規化は実装していない（energy_envelope は per-phrase 自己
  正規化のみ）。
- score/.lab に無い情報の補間・推測・発明は行っていない。
- 実 PJS 音源・展開物・バンドル実体ファイルはいずれも repo 配下へ置いて
  いない（session workdir 限定、rights 制約）。

## 11. 成果物一覧（repo 収載）

1. 本ファイル（`HARNESS3B_EDUCATION_LESSON_RECORD.md`）
2. `USER_ADJUDICATION_20260827_PJS_LESSON_FREEZE.txt`（裁定逐語）
3. `HARNESS3B_EXTRACTOR_SPEC.md`（spec v1.1、byte-identical コピー）
4. `inputs/h3b_freeze_record.json`（v1.1、現行、byte-identical コピー）
5. `inputs/h3b_freeze_record.superseded.1.json`（v1、破棄せず保存、
   byte-identical コピー）
6. `education_lesson_builder.py`（repo canonical builder）
7. `inputs/education_technique_lesson_manifest.json`（本記録が pin する
   実測 sha256 群を収載）
8. `tests/test_education_lesson_builder.py`（新設ユニットテスト、合成
   ミニバンドル使用、実 PJS データ不使用）

バンドル実体ファイル（training_bundle.json / validation_bundle.json、
run1/run2/run3 × 2 = 6 本）は rights 制約により repo 非収載——sha256 の
みを本記録・manifest へ pin する（reexport emb と同型の session-artifact
扱い。repo canonical builder + repo 収載 corpus 由来ファイルにより決定論
的に再導出可能）。
