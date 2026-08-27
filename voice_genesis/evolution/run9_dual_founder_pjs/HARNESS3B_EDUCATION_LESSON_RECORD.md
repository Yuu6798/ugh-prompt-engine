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
  manifest.json` へ pin する。`corpus_provenance.audio_repo_contained:
  false` が宣言するとおり、消費入力3点（wav/lab/musicxml）・展開済み
  corpus・バンドル実体はいずれも repo 非収載——バンドルは本 builder が
  repo 内で決定論的だが、fresh checkout からの再導出には **外部 PJS
  corpus ver1.1 の再取得**（zip sha256 の厳密一致 → 展開後
  `expanded_corpus_identity_sha256` 照合の fail-closed 手順、per-file
  消費入力 pin との照合込み）と、User の scoped 承認（裁定 §1、
  session-scoped）が前提として必要（session-artifact 扱い、reexport emb
  と同型の会計方針。PR #329 第8巡レビュー指摘, 採用, P2 — 旧記述「いつでも
  再導出できる」は上記の外部再取得前提を欠いており不正確だった）。

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

sealed_holdout の row_ids は件数検証・3集合非交差検証（cardinality/
disjointness verification）のためにのみ読まれ、列挙して WAV/lab/
musicxml を decode する・特徴抽出する・lesson を生成する・出力へ含める、
といった用途には一切使われない（PR #329 第10巡レビュー指摘1, P2, 採用
対応で「いかなるコードパスにも現れない」という従来の記述を是正——
`load_training_validation_ids()` は `run9_schema.load_pinned_practice_
split_manifest()`（3集合非交差検証を含む）経由で split manifest を読む
際、`row_ids.sealed_holdout` を件数検証のために実際に読む。件数/非交差
検証を通過した後、戻り値 `FrozenSplitPins` には `row_ids.training`/
`row_ids.validation` のみが格納され、sealed_holdout の row_ids は呼び
出し元へ一切伝播しない——sealed_holdout row_ids に触れるコードは
`education_lesson_builder.py` 中この1関数のみであり、その用途は件数/
非交差検証のみである）。advisory 6 channel のコードパスは実装していない。
corpus 統計正規化は実装していない。librosa import なし、svp_rpe/
voice_genesis の実装モジュール import なし（定数は Read による転記の
み）。

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
  `education_lesson_builder.py`（repo canonical builder、本節記述時点）/
  `run_batch_extract.py` のどのコードパスにも sealed_holdout の row_id は
  現れない。decode/特徴抽出/lesson 生成/試聴は一切行っていない。
  **【第10巡追記・是正】**: 本節は §12（Fix 1、第1巡レビュー対応）が
  `load_training_validation_ids()` へ件数検証（`_PRACTICE_SPLIT_EXPECTED_
  COUNTS`、training/validation/sealed_holdout の3集合カウントを split
  manifest 実体から機械強制）を導入する**前**の baseline 時点の記述で
  あり、その時点では文字通り正確だった。Fix 1 以降（現行実装）は
  sealed_holdout の row_ids を件数検証・3集合非交差検証のためにのみ
  読む——decode/特徴抽出/lesson 生成/出力に使うことは変わらず一切ない
  が、「どのコードパスにも row_id が現れない」という本行の記述は Fix 1
  以降の現行実装とは一致しない（PR #329 第10巡レビュー指摘1, P2, 採用
  対応。正確な現行の記述 = `education_lesson_builder.py` module
  docstring「禁止事項」節、および上記「sealed_holdout row_ids は
  ...」段落参照。過去の記録を書き換えず、この追記のみで正直に訂正する）。
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
扱い。`corpus_provenance.audio_repo_contained: false` が宣言するとおり
音源・展開物も repo 非収載であり、fresh checkout からの再導出には repo
canonical builder + **外部 PJS corpus ver1.1 の再取得**（zip sha256 の
厳密一致 → 展開後 identity 照合の fail-closed 手順）と User の
scoped 承認（裁定 §1）が前提として必要——§0 と同じ訂正、PR #329 第8巡
レビュー指摘, 採用, P2）。

## 12. PR #329 Codex bot レビュー第1巡対応 + run4（2026-08-27）

Claude 完結ルート（フェーズ1: 実装 + 検証 + 返信起草。git commit/push は
別フェーズ）。採用済み指摘2件（いずれも P1、Fable 設計判定済み）。**抽出
式・アラインメント規則・直列化・バンドル内容は無変更**（変更は検証・公開
経路のみ）。

### Fix 1（指摘1）: sealed-holdout 境界の builder 側機械強制

旧実装は `--split-manifest`/`extract-song --song-id` が任意パス・任意
song_id を無検証で受け付けており、(a) sealed ID が row_ids.training/
validation へ混入した split manifest、(b) 構造が壊れた manifest、
(c) 件数が固定分割（70/15/15）と食い違う manifest のいずれを渡しても
decode/抽出へそのまま進む経路があった。

- `run9_schema.py` に `load_pinned_practice_split_manifest()` を新設
  （`load_pinned_speaker_map_manifest()`/`load_pinned_education_lesson_
  manifest()` と同型の3層防御・read-once 契約）: `RUN9_CONTRACT.yaml`
  の `practice_audio_split_manifest_sha` pin との実バイト sha256 照合 +
  `validate_practice_split_manifest()`（3集合非交差検証、既存）。
- `education_lesson_builder.py` に `run9_schema` sibling import を追加
  （mora を指すローカル変数 `m` と衝突するためエイリアスなし）。
  `load_training_validation_ids()` を上記 loader 経由へ全面書き換え、
  training=70/validation=15/sealed_holdout=15 の件数を追加で機械強制
  （`_PRACTICE_SPLIT_EXPECTED_COUNTS`）。
- `run_build()`/`_cmd_extract_song()` とも、対象 song_id が凍結済み
  training∪validation に属することを decode/抽出前に
  `_require_song_ids_within_frozen_split()`（両者共有の単一実装）で検証
  し、集合外（sealed_holdout 含む）は `ExtractorStopError` で即停止する。
  `extract-song` CLI に `--split-manifest`/`--contract-path` を新設
  （`build` は既存の `--split-manifest` に加え `--contract-path` を新設）。
  `main()` の例外捕捉を `run9_schema.Run9ValidationError` にも拡張
  （終了コード2、"STOP:" 表示は `ExtractorStopError` と同一）。
- 新設ユニットテスト（`tests/test_education_lesson_builder.py`）: sealed
  ID が training/validation いずれかへ混入した split manifest の拒否
  （2件）、件数不一致 manifest の拒否、`extract-song` CLI が
  sealed_holdout song_id / 任意 song_id を拒否（2件、実コーパス不要）、
  `_cmd_extract_song()` のゲートが `extract_song()`（decode 本体）より
  前に効くことの直接証跡（monkeypatch で `extract_song()` 呼び出し自体を
  検知）、改ざん（pin 不一致）manifest の拒否、存在しない manifest パス
  の拒否——計8件。

### Fix 2（指摘2）: バンドル2本の atomic ペア公開

旧実装は `write_bundle_json(training_bundle, training_out)` の後に
`write_bundle_json(validation_bundle, validation_out)` を実行しており、
training 書き込み成功後に validation 書き込みが失敗すると混合世代ペアが
最終出力ディレクトリに観測され得た。

- `_atomic_write_bytes()`（`speaker_map_builder._atomic_write_bytes()`
  と同型の staging+fsync パターン、run9 系は svp_rpe 側 `utils/
  atomic_io` を import しない独立構成のため同型の最小実装を自足）+
  `publish_bundle_pair()` を新設: training/validation の両方を同一
  ディレクトリ内の staging ファイルへ書き切ってから、**両方成功した
  場合に限り** それぞれの最終名へ `os.replace()` する。training の
  staging/replace が先行し validation の staging が失敗した場合、
  training の staging も破棄し、**どちらの最終名も書き換えない**。
- `run_build()` は `write_bundle_json()` 2回呼び出しを `publish_bundle_
  pair()` 1回へ置換（直列化本体は `_serialize_bundle_json()` へ共通化、
  `write_bundle_json()` 自体は非 atomic 単本書き込みとして `assemble`
  サブコマンド用に維持——ペア公開の対象ではないため）。
- 失敗注入回帰テスト3件: (i) 旧世代が存在する状態で validation staging
  を monkeypatch で失敗させ、training/validation とも旧世代のまま無傷で
  残ることを確認、(ii) 旧世代が存在しない（初回 build）状態で同じ失敗を
  注入し、最終出力ディレクトリに training_bundle.json/validation_
  bundle.json のいずれも現れないことを確認、(iii) 正常系（両方成功時の
  byte-exact 書き込み・staging 残骸なし）。

### 副次的変更: pyyaml が builder の新規依存に

`run9_schema.py` はモジュール冒頭で `import yaml`（PyYAML、本体必須
依存）を無条件実行するため、`education_lesson_builder.py` が
`run9_schema` を import するようになった結果、`education_lesson_
builder.py` を実行するには pyyaml が必要になった（抽出処理そのもの
——decode/WORLD F0/musicxml パース等——には無関係、split-manifest/
contract 検証にのみ使う）。venv_h3b には未インストールだったため
`pip install pyyaml`（6.0.3、PyPI キャッシュ済みホイール、追加の外部
取得なし）で追加した。`extraction_dependency_pins`（numpy/scipy/
pyworld/python のバージョン pin、抽出数式が消費する依存のみを対象）は
無変更のまま——pyyaml は抽出数式を一切消費しない。

repo 収載 builder の実バイト sha256（更新後）:
```
d93fc17b2f10b2e0ec6d240027875d22a748f9b508de9a5233144a78b52799d9
```
（旧値 `550eda93cf9e1cbd9f95a2525db03cda73d753d63e4df79003d0757acef4e8ae`
は履歴として本記録 §7 に残置する。`inputs/education_technique_lesson_
manifest.json` の `builder_provenance.builder_sha256` をこの新値へ更新
した——`run9_schema.load_pinned_education_lesson_manifest()` cross-check
(b) が実バイトと照合するため。連鎖して manifest raw sha256 も変わり、
`RUN9_CONTRACT.yaml` の `education_technique_lesson_manifest_sha` を
第2世代へ repin した）。

### run4: repo builder（第1巡対応後）による再現実行（独立4回目）

venv_h3b の python3（`pip install pyyaml` 追加後）で、修正後の repo
builder を workdir 展開済み corpus（`expanded/PJS_corpus_ver1.1`）に対し
`build` サブコマンドで実行した。freeze record・spec・split manifest・
contract はいずれも repo 収載の既定パス（CLI 引数省略、デフォルト値の
まま）。

```
$ python3 education_lesson_builder.py build \
    --corpus-root <workdir>/expanded/PJS_corpus_ver1.1 \
    --out-dir <workdir>/run4
```

```
real 4m20.736s
```

| バンドル | 既 pin（run1/run2/run3） | run4 sha256 | 一致 |
|---|---|---|---|
| training | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | **PASS** |
| validation | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | **PASS** |

`cmp run1/training_bundle.json run4/training_bundle.json` / `cmp
run1/validation_bundle.json run4/validation_bundle.json` ともに差分 0
（完全一致）を確認済み。

`training_technique_lesson_sha256`/`validation_technique_lesson_sha256`
（既 PINNED、determinism_evidence.{training,validation} の run1==run2==
run3 と一致）が run4 とも一致したことで、**検証・公開経路の変更（Fix
1/Fix 2）が抽出式・アラインメント・直列化・バンドル内容に一切影響を
与えていないことを実測で確認した**——本フェーズの不変条件が満たされて
いる。

### 検証結果

- `ruff check .`: clean
- `python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`:
  本節（builder_provenance.builder_sha256 の repo 収載値更新）を含む本
  ファイル最終稿確定後に全 PASS（2191 件、`test_harness3b_load_pinned_
  education_lesson_manifest_happy_path` 等 builder_sha256 cross-check
  依存の10件を含む——本節確定前の中間実行では builder.py のバイトが
  未更新の manifest と食い違い意図通り FAIL していたことを確認済み、
  fail-closed 機構が実際に機能している直接証跡）。
- freeze record（`inputs/h3b_freeze_record.json`）: 無変更（workdir
  extractor の凍結記録であり、repo builder の identity は manifest 側
  `builder_provenance` が別途担う確立設計——本フェーズでも変更していない）。

---

## 13. PR #329 Codex bot レビュー第2巡対応 + run5（2026-08-27）

Claude 完結ルート（フェーズ1: 実装 + 検証 + 返信起草。git commit/push は
別フェーズ）。採用済み指摘4件（いずれも P1、Fable 設計判定済み）。**抽出
式・アラインメント規則・直列化・バンドル内容は無変更**（変更は検証・公開
経路のみ、第1巡と同じ不変条件）。

### Fix 3（指摘「Enforce the frozen split in the assemble command」）: assemble 経路の凍結 split 強制

第1巡修正は `run_build()`/`extract-song` CLI にゲートを追加したが、
`assemble` サブコマンドは任意の ID リストと中間ディレクトリを凍結 split
照合なしで受け付けたままだった——ゲートを持たない直接 `extract_song()`
API 呼び出し等で生成された sealed 中間物を、任意の ID リストとともに
training/validation バンドルへ梱包し得る具体的経路が残っていた。

- `_cmd_assemble()`: 中間物を1つも読む前に `load_training_validation_ids()`
  で凍結 split をロードし、要求 ID 集合（`--song-ids-json`）が選択 split
  （`--split`）の凍結 ID 集合（training なら70件、validation なら15件）
  と**厳密集合一致**することを新設 `_require_exact_frozen_split_
  membership()` で検証。不一致（sealed 混入・欠落・過剰・training/
  validation 取り違えのいずれも）は `ExtractorStopError` で中間物読み込み
  前に拒否する。`assemble` に `--split-manifest`/`--contract-path` を新設
  （既定は正典パス）。
- `extract_song()` 本体へゲートを内蔵: 関数シグネチャに必須 keyword-only
  引数 `frozen_allowed_ids`/`consumed_inputs_pins` を追加し、関数内部で
  membership を検証してから decode/読み込みに進む構造へ変更（「CLI だけが
  ゲートを持ち、直接 API 呼び出しは素通り」という穴の閉鎖）。
  `run_build()`/`_cmd_extract_song()` を全て追随。
- 既存の `_require_song_ids_within_frozen_split()`（第1巡新設の部分集合
  検証ヘルパ）はそのまま維持し（`run_build()`/`extract_song()` 内の防御的
  再確認に使用）、`assemble` 専用の厳密集合一致は新設ヘルパへ分離した
  （意味論が異なるため——部分集合検証と完全一致検証を1つの関数へ混ぜない）。
- 新設ユニットテスト（8件）: assemble への sealed ID 混入リスト拒否、
  欠落リスト拒否、過剰（未知 ID）リスト拒否、training/validation 取り違え
  拒否、`_require_exact_frozen_split_membership()` の直接単体テスト（エラー
  内容・happy path）、`extract_song()` 直接呼び出しで集合外 ID が decode
  前に拒否されること（合成データ）、`extract_song()` 直接呼び出しで
  consumed-input pin 未登録 ID が拒否されること。

### Fix 4（指摘2-2）: `publish_bundle_pair()` の2連 `os.replace()` 自体の atomic 化

第1巡修正は staging（`_atomic_write_bytes()`）の失敗のみを扱っており、
2本の `os.replace()` 自体は依然として atomic なペアではなかった——
training の rename 成功後に validation の rename が失敗すると、新世代
training + 旧世代（または欠落）validation という同型の混合世代が観測され
得た上、validation の staging ファイルも残置され得た。

- `_backup_existing()`（同一ディレクトリ内の一意な backup 名へ既存ファイル
  を `os.replace()` で退避）+ `_rollback_to_backup()`（backup があれば
  復元、無ければ削除）+ `_discard_backup()`（成功時に backup 破棄）を新設。
- `publish_bundle_pair()`: staging 完了後、公開前に training/validation
  両方の既存内容を `_backup_existing()` で退避してから2本の `os.replace()`
  を実行する。`BaseException` を含むいずれかの rename の失敗時は、
  **両方の**最終名を publish 開始前の状態（旧世代のバイト、または未存在
  なら削除）へロールバックし、残った staging ファイルも破棄したうえで
  re-raise する。両方成功時は退避した backup を破棄する。
- 失敗注入回帰テスト4件: rename 1本目（training）失敗×旧世代あり/なし、
  rename 2本目（validation）失敗×旧世代あり/なし——いずれも最終状態が
  「旧世代ペア無傷（または両方欠落）+ staging/backup 残骸なし」であること
  を確認。

### Fix 5（指摘2-3）: `run_build()` の pinned education manifest 照合

旧実装は `run_build()` が pinned education manifest を一切ロード・照合せ
ず、依存挙動のドリフト（例: 別ビルドの scipy/pyworld が異なる float を
生成）が起きても両バンドルを publish して成功終了し得た——「正準の再現
手段」として案内されているコマンドが、下流消費者が拒否すべき非正準
artifact を成功として報告する経路だった。

- 新設 `_require_bundle_bytes_match_pinned_manifest()`: 生成した
  training/validation バンドルバイトの sha256 を `load_pinned_education_
  lesson_manifest()` の `training_technique_lesson_sha256`/`validation_
  technique_lesson_sha256` と照合し、不一致なら publish 前（staging 破棄
  済み）に `ExtractorStopError` で拒否する（実測 sha を両方表示）。
- `run_build()`/`build` CLI に `--allow-unpinned` を新設（既定 off）:
  将来「同一 design revision 下での新規 attempt 再生成」を意図的に行う
  ためのエスケープハッチ。使用時は出力が UNPINNED（manifest が repin
  されるまで非正準）である旨を stderr へ明示する。
- 新設ユニットテスト3件: 照合成功（happy path、合成 manifest+バイト）、
  不一致拒否、`--allow-unpinned` が本チェック自体を一切呼ばないことの
  直接証跡（monkeypatch で「呼ばれたら失敗」にして確認）。

### Fix 6（指摘2-4）: musicxml を含む消費3入力の per-file sha256 pin

`donor_bank_lab.py` の `corpus_identity_hash()` は `.lab` + 対の
`_song.wav` のみを被覆し、builder がもう1つ消費する musicxml を被覆しな
い——musicxml 単体の改ざん（duration/F0 lesson を変え得る）が、既存の
corpus identity pin では検出されない穴だった。

- `inputs/pjs_consumed_inputs_sha256.json`（新設）: training(70) +
  validation(15) = 85曲 × 3ファイル（`pjsNNN.lab`/`pjsNNN.musicxml`/
  `pjsNNN_song.wav`）= 255件の per-file sha256 pin。sealed_holdout(15曲)
  は builder が一切消費しないため対象外（`sealed_holdout_excluded: true`
  で明示宣言）。値は workdir の検証済み expanded corpus（zip sha 検証 +
  `expanded_corpus_identity_sha256` 照合 PASS 済み）を実測し、E1
  inventory（`e1_inventory.json`、zip 展開直後の実測607件）と突合して
  二重確認した（255ファイル全件、両者完全一致）。
- `run9_schema.py`: `validate_pjs_consumed_inputs_manifest()`（schema・
  件数85・値整形式64hex 検証）+ `load_pinned_consumed_inputs_manifest()`
  （`pjs_consumed_inputs_manifest_sha` pin 経由の唯一の正規消費経路、他の
  `load_pinned_*` と同型の3層防御）を新設。`CONTRACT_PIN_FIELDS` へ
  `pjs_consumed_inputs_manifest_sha` を追加し `RUN9_CONTRACT.yaml` で
  PINNED 化。
- `education_lesson_builder.py`: `load_consumed_inputs_pins()`（新設
  loader ラッパ）+ `_require_consumed_input_bytes_match()`（song_id の
  lab/musicxml/wav 実バイト sha256 を pin と照合、decode 前に fail-closed）
  を新設し、`extract_song()` 内部（`run_build()`/`extract-song` CLI 双方
  の経路）で decode（`check_wav_header_or_stop()`）より前に強制する。
- `education_technique_lesson_manifest.json` の既存 `corpus_provenance`
  ブロックへ `consumed_inputs_manifest_repo_relative_path`/
  `consumed_inputs_manifest_sha256` を追加し、`load_pinned_education_
  lesson_manifest()` の cross-check (14) として組み込んだ（この pin
  ファイル自体の来歴を education manifest 側からも machine 強制する）。
- 新設ユニットテスト: consumed-inputs manifest の85曲被覆・sealed 非包含
  確認、schema 検証、loader happy path、pin 改ざん（バイト tampering・
  song_count 不正・sealed_holdout_excluded=false）拒否、education manifest
  側 cross-check（consumed_inputs_manifest_sha256 改ざん）拒否、
  `extract_song()` 直接呼び出しで消費入力バイト不一致（musicxml 単体
  改ざんのケースを含む）が decode 前に拒否されることの直接証跡
  （`check_wav_header_or_stop()` を monkeypatch して「呼ばれたら失敗」に
  して確認）。

### 連鎖更新

builder バイト変更（新値 `a6b99a7ba42f7d09f29395bc5fea1ef89a479555492ab5a537fba9fd26af8a27`）
+ 本節追記に伴い、`inputs/education_technique_lesson_manifest.json` の
`builder_provenance.builder_sha256`/`detail_record_sha256` を更新し、
manifest raw sha256 が変わったため `RUN9_CONTRACT.yaml` の
`education_technique_lesson_manifest_sha` を第3世代へ repin した（旧値
= 第2世代、第1巡対応時点の値。履歴は本記録 §12 参照）。新設
`pjs_consumed_inputs_manifest_sha` pin は本節で PINNED 化した独立の新規
欄（第2巡指摘2-4対応、上記 Fix 6 参照）。

### run5: repo builder（第2巡対応後）による再現実行（独立5回目）

venv_h3b の python3 で、修正後の repo builder を workdir 展開済み corpus
（`expanded/PJS_corpus_ver1.1`）に対し `build` サブコマンドで実行した。
freeze record・spec・split manifest・contract・consumed-inputs manifest
はいずれも repo 収載の既定パス（CLI 引数省略、デフォルト値のまま）。

```
$ python3 education_lesson_builder.py build \
    --corpus-root <workdir>/expanded/PJS_corpus_ver1.1 \
    --out-dir <workdir>/run5
```

```
real 4m24.927s
```

| バンドル | 既 pin（run1〜run4） | run5 sha256 | 一致 | pinned_manifest_check |
|---|---|---|---|---|
| training | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | **PASS** | `PASS` |
| validation | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | **PASS** | `PASS` |

`cmp run1/training_bundle.json run5/training_bundle.json` / `cmp
run1/validation_bundle.json run5/validation_bundle.json` ともに差分 0
（完全一致）を確認済み。run5 は `--allow-unpinned` を付けずデフォルト
経路で実行した——`run_build()` 内で `_require_bundle_bytes_match_pinned_
manifest()`（Fix 5）が自動的に走り、`pinned_manifest_check` の値
（実行結果 dict の該当欄）が上表の値を返したことで、Fix 5 のゲートが
実運用の canonical path 上で実際に機能していることの直接証跡となる。

`training_technique_lesson_sha256`/`validation_technique_lesson_sha256`
（既 PINNED、determinism_evidence.{training,validation} の run1==run2==
run3 と一致、run4 とも一致確認済み）が run5 とも一致したことで、**本
フェーズの4修正（assemble ゲート・rename rollback・pinned manifest
照合・consumed-inputs pin）が抽出式・アラインメント・直列化・バンドル
内容に一切影響を与えていないことを実測で確認した**——不変条件が満たされ
ている。

### 検証結果

- `ruff check .`: clean
- `python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`:
  本節（builder_provenance.builder_sha256/detail_record_sha256 の repo
  収載値更新、`pjs_consumed_inputs_manifest_sha` の3世代目 repin を含む）
  最終稿確定後に全 PASS（2226 件）。
- freeze record（`inputs/h3b_freeze_record.json`）: 無変更（第1巡と同じ
  理由——repo builder の identity は manifest 側 `builder_provenance` が
  別途担う）。

## 14. PR #329 Codex bot レビュー第3巡対応 + run6（2026-08-27）

Claude 完結ルート（フェーズ1: 実装 + 検証 + 返信起草。git commit/push は
別フェーズ）。採用済み指摘6件（いずれも Fable 設計判定済み、P1×4/P2×2）。
**抽出式・アラインメント規則・直列化・バンドル内容は無変更**（変更は
検証・公開経路のみ、第1巡/第2巡と同じ不変条件）。

### Fix 7（指摘1, P1）: assemble 重複 ID 拒否

`--song-ids-json` から読み込んだリストは、`_require_exact_frozen_split_
membership()` 内部で `set(song_ids)` へ変換されるため、重複した song_id
を検出できなかった——凍結 training 集合と（重複を除けば）完全一致する
リストであっても、`assemble_bundle()` の `ordered_songs = [songs_by_id[sid]
for sid in song_ids_sorted]` は `song_ids_sorted` の重複をそのまま辿るため、
同一曲が2回以上 bundle の `"songs"` 配列へ混入し得た。

- 新設 `_require_no_duplicate_song_ids()`: `set()` 化するいかなる検証より
  前に呼び、重複があれば `ExtractorStopError`（重複した song_id を列挙）
  で中間物読み込み前に拒否する。`_cmd_assemble()` が `song_ids_sorted =
  sorted(song_ids)` する前に本関数を通す。
- 新設ユニットテスト: 凍結 training 全70件 + 重複1件（凍結集合内の既存
  ID をもう1回列挙）を渡した CLI 経路が拒否されること、`_require_no_
  duplicate_song_ids()` の直接単体テスト（エラー内容・happy path）。

### Fix 8（指摘2, P1）: extract_song の生 mapping 信頼の閉鎖

`extract_song()` は `frozen_allowed_ids: Sequence[str]`/`consumed_inputs_
pins: Mapping[str, Mapping[str, str]]` という生の型を受理していたため、
値がどこから来たか（`load_training_validation_ids()`/`load_consumed_
inputs_pins()` という pin 検証済み canonical loader を経由したものか、
呼び出し元が自作した未検証の list/dict か）を関数自身が区別できなかった。

- 新設 `FrozenSplitPins`/`ConsumedInputPins`（frozen dataclass）:
  `load_training_validation_ids()`/`load_consumed_inputs_pins()` の戻り値
  専用の不透明型。前者は `training_ids`/`validation_ids`（tuple）と
  `frozen_allowed_ids` プロパティ（training∪validation の昇順集合）を
  保持し、既存の `training_ids, validation_ids = load_training_validation_
  ids(...)` という unpack 慣用句を維持するため `__iter__` を実装する。
  後者は per-song consumed-input sha256 辞書を `types.MappingProxyType`
  の入れ子へ変換して保持する（`__post_init__` で防御的コピー）。
- `extract_song()` のシグネチャを `frozen_split_pins: FrozenSplitPins`/
  `consumed_inputs_pins: ConsumedInputPins` へ変更し、関数冒頭で
  isinstance 検査を行う——生の list/set/tuple/dict は `ExtractorStopError`
  で拒否する。`run_build()`/`_cmd_extract_song()`/`_cmd_assemble()`/
  `_cmd_probe_header()`（Fix 12 参照）全て追随。
- **正直な宣言（Python の限界）**: Python は private な直接構築を完全には
  防げない——呼び出し元が `FrozenSplitPins(training_ids=(...),
  validation_ids=(...))` を悪意を持って直接構築すれば、isinstance 検査は
  通ってしまう。本ゲートが機械強制するのは「repo 内の全コードパスが
  canonical loader を経由する」という構造的規約であり、Python の型
  システムによる封印ではない——この境界は両型の定義コメントと本関数の
  docstring に明記した。
- 新設ユニットテスト: `extract_song()` へ生 list/生 dict を渡すと
  isinstance ゲートで拒否されること（2件）。既存の `extract_song()`
  直接呼び出しテスト群（第2巡新設）は `FrozenSplitPins`/`ConsumedInputPins`
  を構築して渡す形へ追随。

### Fix 9（指摘3, P2）: TOCTOU 閉鎖（1回読み取り原則）

`extract_song()` は wav/lab/musicxml の各ファイルを複数回 open していた
（例: wav は consumed-input sha 照合で1回、`check_wav_header_or_stop()`
のヘッダ probe で2回目、`load_wav_24bit_mono_48k()` の decode で3回目）
——sha 照合に使ったバイト列と、その後 parse/decode されるバイト列が、
理論上は異なる読み取り時点のものになり得た（TOCTOU）。

- wav: `_parse_wav_fmt_from_buffer()`/`_parse_wav_fmt_and_data_from_
  buffer()`（新設、`io.BytesIO` ベース）+ `check_wav_header_or_stop_
  bytes()`/`load_wav_24bit_mono_48k_bytes()`（新設）。
- lab: `_parse_lab_text()`（新設、既存ロジックを text 引数へ分離）+
  `parse_lab_bytes()`（新設、`buf.decode("utf-8")` してから解析）。
- musicxml: `_parse_musicxml_root()`（新設、既存ロジックを root 引数へ
  分離）+ `parse_musicxml_bytes()`（新設、`ET.fromstring(buf)`）。
- 既存の path ベース関数（`read_wav_fmt_header()`/`check_wav_header_or_
  stop()`/`load_wav_24bit_mono_48k()`/`parse_lab_file()`/`parse_
  musicxml()`）は全て「1回 `read_bytes()` してから bytes 版へ委譲する」
  薄いラッパへ変更した——単体呼び出し互換を維持しつつ、`extract_song()`
  だけが特別扱いされる構成を避けた。`read_wav_fmt_header()` のみ例外
  ——`probe-header` 専用の「data チャンクを読まないストリーミング skip」
  という異なる要件を持つため元の実装のまま維持する。
- `extract_song()`: `wav_path.read_bytes()`/`lab_path.read_bytes()`/
  `xml_path.read_bytes()` をそれぞれ1回だけ実行し、`_require_consumed_
  input_bytes_match_bytes()`（新設、path 引数を bytes 引数へ置換した
  リネーム版）でその同一バッファに対して sha 照合してから、同じバッファ
  を `check_wav_header_or_stop_bytes()`/`parse_lab_bytes()`/`parse_
  musicxml_bytes()`/`load_wav_24bit_mono_48k_bytes()` へ渡す——ファイル
  再 open を完全に排除する（`speaker_map_builder.py` の verified
  self-exec dispatch が採る read-once パターンと同型）。
- 新設ユニットテスト: `Path.read_bytes` をラップして呼び出し回数を
  ファイル単位で数え、wav/lab/musicxml のいずれも高々1回であることを
  直接確認（count_mismatch 相当の最短経路で確認）。既存の
  `check_wav_header_or_stop()` monkeypatch テスト（第2巡新設）は
  `check_wav_header_or_stop_bytes()` へ追随。

### Fix 10（指摘4, P1）: publish_bundle_pair backup 段の保護 try 内包

第2巡修正（Fix 4）は2連 `os.replace()` 自体は同一トランザクションへ
含めたが、公開直前の `_backup_existing()` 呼び出し2回自体は `try` の
**外側**にあった——1本目（training）の退避成功直後に2本目（validation）
の退避が失敗する経路（例: `validation_path` が通常ファイルでなく
ディレクトリで `os.replace()` が構造的に失敗するケース）で、training は
既に backup 名へ rename 済み（＝最終名が一時的に欠落した状態）にも
かかわらず、その例外がロールバックを一切経由せず `publish_bundle_
pair()` の外へそのまま伝播していた。

- `publish_bundle_pair()`: `training_backup`/`validation_backup` を
  `try` の外側で `None` 初期化してから、退避2回 + rename2回の**4操作
  すべて**を同一の `try`/`except BaseException` トランザクションへ含める
  よう変更。失敗時は両方の最終名を `_rollback_to_backup()` でロール
  バックし、両 staging も破棄してから re-raise する。
- `_backup_existing()` 自体: `mkstemp()` が作る空プレースホルダを
  `os.replace()` 失敗時に best-effort で削除するよう変更——旧実装は
  この空プレースホルダが孤児として残置され得た（「残骸なし」契約を
  `_backup_existing()` 自身の中間生成物についても満たすため）。
- 失敗注入テスト（新設）: `validation_path` をディレクトリとして事前に
  作成し、`publish_bundle_pair()` を呼ぶと `NotADirectoryError` を送出
  すること、かつ最終状態が「training は公開前バイトのまま無傷 /
  validation はディレクトリのまま無傷 / staging・backup の残骸なし」で
  あることを直接確認する。

### Fix 11（指摘5, P2）: assemble 単独出力の atomic 化

`write_bundle_json()`（`assemble` サブコマンド専用の単本バンドル出力）
は `Path(path).write_bytes()` の直書きだった——書き込み途中の失敗
（ディスク枯渇・プロセス kill 等）で、旧世代 artifact が破損した部分
書き込みバイト列で上書きされ得た。

- `write_bundle_json()`: `_atomic_write_bytes()`（`publish_bundle_pair()`
  と共有する staging+fsync ヘルパー）+ `os.replace()` を使う構成へ
  変更。staging 段の失敗時は旧世代 artifact の実バイトに一切触れない。
- 失敗注入テスト（新設）: `_atomic_write_bytes()` を monkeypatch して
  書き込み途中失敗を注入すると、旧 artifact が無傷のまま残り staging の
  残骸も残らないことを確認する。

### Fix 12（指摘6, P2）: probe-header の凍結 split ゲート

`probe-header` サブコマンドは「メタデータのみ・decode なし」だが、WAV
ファイルを1バイトでも open する処理そのものが 裁定 §2「sealed は完全性
hash と ID 確認以外の処理禁止」に抵触し得た——`--song-ids` に
sealed_holdout の song_id を渡すと、旧実装は凍結 split 照合なしで
その WAV ヘッダをそのまま probe していた。

- `_cmd_probe_header()`: いずれの WAV も open する前に `load_training_
  validation_ids()` で凍結 split をロードし、`_require_song_ids_within_
  frozen_split()` で `--song-ids` 全件が凍結 training∪validation に
  属することを検証する（全件一括拒否——一部だけ open してから拒否、と
  いう中途半端な状態を作らない）。`probe-header` に `--split-manifest`/
  `--contract-path` を新設（既定は正典パス）。
- 新設ユニットテスト: sealed_holdout の song_id を含む `--song-ids`
  （凍結集合内の他の ID と併せて）を渡した CLI 経路が、対応する WAV が
  一切存在しなくても拒否されること、`read_wav_fmt_header()` を
  monkeypatch して「呼ばれたら失敗」にすることでゲートが WAV open より
  前に効くことを直接確認するテスト。

### 連鎖更新

builder バイト変更（新値
`9a007b7543523b99d3689a4fa40383a35497422f7784050b1ca8b8d1d34e53c1`）
+ 本節追記に伴い、`inputs/education_technique_lesson_manifest.json` の
`builder_provenance.builder_sha256`/`detail_record_sha256` を更新し、
manifest raw sha256 が変わったため `RUN9_CONTRACT.yaml` の
`education_technique_lesson_manifest_sha` を第4世代へ repin した（旧値
= 第3世代、第2巡対応時点の値。履歴は本記録 §12/§13 参照）。
`pjs_consumed_inputs_manifest_sha`（`inputs/pjs_consumed_inputs_
sha256.json`）は本節の変更で一切触れていないため無変更（本節はいずれの
消費入力の実バイト・pin 値にも影響しない、検証・公開経路のみの変更）。

### run6: repo builder（第3巡対応後）による再現実行（独立6回目）

venv_h3b の python3 で、修正後の repo builder を workdir 展開済み corpus
（`expanded/PJS_corpus_ver1.1`）に対し `build` サブコマンドで実行した。
freeze record・spec・split manifest・contract・consumed-inputs manifest
はいずれも repo 収載の既定パス（CLI 引数省略、デフォルト値のまま）。

```
$ python3 education_lesson_builder.py build \
    --corpus-root <workdir>/expanded/PJS_corpus_ver1.1 \
    --out-dir <workdir>/run6 \
    --allow-unpinned
```

```
real 4m21.945s
```

| バンドル | 既 pin（run1〜run5） | run6 sha256 | 一致 |
|---|---|---|---|
| training | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | **PASS** |
| validation | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | **PASS** |

`cmp run1/training_bundle.json run6/training_bundle.json` / `cmp
run1/validation_bundle.json run6/validation_bundle.json` ともに差分0
（完全一致）を確認済み。
run6 は本節の6修正がいずれも検証・公開経路（TOCTOU 閉鎖・atomic 化・
凍結 split ゲート強化）のみに閉じており、抽出式・アラインメント・
直列化・バンドル内容に一切影響しないことの実測証跡である——run6 は
`--allow-unpinned` で実行した（manifest 側 `builder_provenance.
builder_sha256` を本節の builder バイト変更後の値へまだ repin していない
時点での実測取得のため）。repin 完了後、`_require_bundle_bytes_match_
pinned_manifest()` を run6 の実測 sha に対して直接呼び出し、例外を投げ
ないこと（＝ canonical path 上で Fix 5（第2巡）のゲートが引き続き正しく
機能すること）を確認済み。

### 検証結果

- `ruff check .`: clean
- `python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`:
  本節（builder_provenance.builder_sha256/detail_record_sha256 の repo
  収載値更新、`education_technique_lesson_manifest_sha` の4世代目 repin
  を含む）最終稿確定後に全 PASS（2236 件）。
- freeze record（`inputs/h3b_freeze_record.json`）: 無変更（第1巡/第2巡と
  同じ理由——repo builder の identity は manifest 側 `builder_provenance`
  が別途担う）。

## 15. PR #329 Codex bot レビュー第4巡対応 + run7（2026-08-27）

Claude 完結ルート（フェーズ1: 実装 + 検証 + 返信起草。git commit/push は
別フェーズ）。Fable 採否判定 = 採用2件（いずれも P1）/ 見送り1件（境界
宣言、実装なし）。**抽出式・アラインメント規則・直列化・バンドル内容は
無変更**（変更は検証・公開経路のみ、第1〜3巡と同じ不変条件）。

### Fix 13（採用1, P1）: extract-song/assemble/probe-header の `--out`
corpus-alias 拒否

`--out` が対象曲の消費入力（wav/lab/musicxml、または symlink 経由の
alias）や split manifest/contract/consumed-inputs pin ファイル等を指すと、
抽出後の JSON 書き込みがその pin 済み corpus 入力を破壊し得た——builder
自身にはこれを防ぐ preflight が一切存在しなかった。`speaker_map_
builder.py` の `_resolve_alias_conflict()`/`_check_out_does_not_alias_
inputs()`（PR #328 Codex レビュー第2巡指摘4/第8巡指摘16、いずれも P1、
採用対応で確立済み前例）と同型のロジックを本 builder の3つの出力
サブコマンドへ導入した。

- 新設 `_resolve_output_alias_conflict()`: `out_path` が保護対象パス群の
  いずれかと (a) `Path.resolve()` 一致（symlink 経由の alias 含む）
  **または** (b) `os.path.abspath()` 一致（symlink 未解決の lexical
  絶対パス）であれば、その保護パスを返す。`speaker_map_builder.
  _resolve_alias_conflict()` は (a) のみだったが、本実装は (b) も追加した
  二重チェック——`out_path` の親ディレクトリが未存在の場合や
  `Path.resolve(strict=False)` の挙動がプラットフォーム間で異なり得る
  ことに依存しない、filesystem 状態非依存の防御を加える（Fable 設計
  方針:「lexical + resolved の二重」）。
- 新設 `_require_out_does_not_alias_protected_paths()`: 衝突があれば
  `ExtractorStopError` で拒否する共有実装。3コマンドが呼ぶ保護パス集合
  ビルダー（`_extract_song_protected_paths()`/`_probe_header_protected_
  paths()`/`_assemble_protected_paths()`）をそれぞれ新設した:
  - `extract-song`: 当該曲の消費3入力（wav/lab/musicxml）+ split
    manifest + contract + consumed-inputs pin ファイル + freeze record +
    spec。**読み取り前の preflight として** `freeze_selfcheck()` を含む
    いかなる読み取りより前（関数冒頭）に実行する。
  - `probe-header`: `--song-ids` 全件の WAV + split manifest + contract。
    既存の凍結 split ゲート（第3巡 Fix 12）の直後・いずれの WAV も open
    する前に実行する。
  - `assemble`: spec + split manifest + contract + `--song-ids-json` +
    選択 split の全中間物 JSON（`song_ids_sorted` は凍結 split 厳密
    一致検証を通過済みの確定リストを使う）。既存の厳密集合一致ゲート
    （第2巡 Fix 5）の直後・中間物を1つも読む前に実行する。
- `_extract_song_protected_paths()` は各 CLI オプションを `getattr(args,
  "...", None)` 経由で読む——実 CLI（argparse）は全属性を必ず設定するが、
  テスト層が直接構築する最小 `argparse.Namespace` に対しても
  `AttributeError` ではなく「未指定 = 既定値」として振る舞う防御的実装
  とした（第3巡新設の直接呼び出しテスト `test_harness3b_extract_song_
  direct_call_gate_raises_before_extract_song` が `consumed_inputs_
  manifest` 属性を持たない最小 Namespace を使っており、この防御がないと
  regression する）。
- 新設ユニットテスト: `extract-song --out` = corpus wav 直指定 / symlink
  経由 / `--split-manifest` として渡した合成ファイルへの直指定（消費
  3入力に限らない保護対象の直接証跡）、`probe-header --out` = corpus wav
  直指定、`assemble --out` = 選択 split の中間物 JSON（1件）への直指定
  ——いずれも拒否され、対象ファイルの実バイトが無傷のまま残ることを確認
  する。

### Fix 14（採用2, P1）: assemble 経路の pinned education manifest 照合

`assemble` は pinned education manifest と一切照合せず、中間物ディレクトリ
の内容がどのようなもの（依存挙動のドリフトで生じた非正準バイト、
改ざんされた中間物）であっても canonical な `run9-technique-lesson-
bundle/1.0` 形式に整形できてさえいれば成功出力し得た——「training/
validation バンドルの正準な組立手段」として案内されているコマンドが、
下流消費者が拒否すべき非正準 artifact を成功として発行する経路だった
（`run_build()` は第2巡 Fix 5 で同種の穴を閉じていたが、`assemble`
単体には及んでいなかった）。

- 新設 `_require_single_split_bundle_bytes_match_pinned_manifest()`:
  `_require_bundle_bytes_match_pinned_manifest()`（`run_build()` 専用、
  training/validation 2本を同時に要求する）とは別関数——`assemble` は
  1本しか手元にバイトを持たないため、要求された `split` 側の pin 値
  （`training_technique_lesson_sha256`/`validation_technique_lesson_
  sha256` のいずれか）のみを照合する。不一致は `ExtractorStopError` で
  publish 前に拒否する（実測/pin 値を両方表示）。
- `_cmd_assemble()`: `assemble_bundle()` 直後にバンドルバイトを直列化して
  sha256 を計算し、`--allow-unpinned`（新設、既定 off、`run_build()` と
  同型のエスケープハッチ）でなければ上記照合を実行してから publish する。
  使用時は UNPINNED である旨を stderr へ明示する（`run_build()` の
  `--allow-unpinned` メッセージと同型）。
- 新設 `write_bundle_bytes()`: 既に直列化済みのバイト列を受け取り
  atomic に書き込む（`_atomic_write_bytes()` + `os.replace()`）。
  `write_bundle_json()` はこれへ委譲するようリファクタし（直列化 →
  書き込みの分離）、`assemble` が pin 照合用に計算した同一バイト列を
  二重直列化なしでそのまま書き込めるようにした——既存の `write_bundle_
  json()` 呼び出し元・monkeypatch テスト（`_atomic_write_bytes()` を
  差し替える失敗注入テスト含む）は無変更で動作する。
- 新設ユニットテスト: `_require_single_split_bundle_bytes_match_pinned_
  manifest()` の happy path / 不一致拒否の直接単体テスト、`assemble`
  CLI が既定（`--allow-unpinned` 省略）では合成中間物（実 PJS バンドルと
  一致しない）を拒否すること、`--allow-unpinned` 指定時は同関数が一切
  呼ばれずに publish されること（monkeypatch で「呼ばれたら失敗」に
  して直接確認）。

### 見送り1（境界宣言、実装なし）: 「extract_song 内で canonical pin を
ロードせよ」

第3巡で正直開示済みの Python 構築可能性（`FrozenSplitPins`/
`ConsumedInputPins` の docstring「Python の限界」節、Fix 8 参照）の
再形成であり、Fable 設計判定により実装変更せず境界宣言で応答する。

1. **プロセス内の意図的偽造は Python のいかなる設計でも閉じられない**
   ——pin を `extract_song()` 内部で関数内ロードへ変えても、repo の
   確立テスト規約（下記2参照）を満たすにはテスト分離のためのパス引数を
   受ける必要があり、信頼の所在が「呼び出し元供給の（isinstance 検査
   済み）オブジェクト」から「呼び出し元供給のパス文字列」へ移動するだけ
   で、悪意ある呼び出し元がパスを差し替える同型の偽造経路が残る。
2. **repo の確立テスト規約と両立しない** ——本ファイル群のテストは合成
   データ・`tmp_path` によるファイルシステム分離・モック不使用を推奨する
   （CLAUDE.md「Testing」節）。`extract_song()` 内で canonical pin パスを
   hard-code すると、テストは実 corpus/実 contract に依存するか、
   monkeypatch でパス定数を差し替えるしかなくなり、後者は結局「呼び出し
   元がロード元を差し替えられる」という同型の構造を持つ。
3. **本 PR の防御境界は構造規約 + 型ゲート + grep 監査可能性**
   ——`FrozenSplitPins`/`ConsumedInputPins` の isinstance ゲート
   （第3巡 Fix 8）が機械強制するのは「repo 内の全コードパスが
   `load_training_validation_ids()`/`load_consumed_inputs_pins()`
   （その内部で `run9_schema.load_pinned_*()` の pin/構造検証）を経由
   する」という構造的規約であり、この境界は第3巡時点で両型の定義
   コメントと `extract_song()` の docstring に凍結済みである。
4. **プロセス内で任意コードを書ける主体はこの脅威モデルの対象外**
   ——`extract_song()` を直接 import 呼び出しできる主体は、同じ権限で
   `education_lesson_builder.py` 自身を編集できる（同一 repo・同一
   プロセス）。この脅威モデルはコードレビュー/CI の層（PR レビュー・
   `branch_write_policy`・contract pin）で防御されるべきものであり、
   関数シグネチャの変更では解消しない——`speaker_map_builder.py` の
   verified self-exec dispatch（第6巡 Fix、`main()` 自身の完全性は
   「この仕組みの手が届く範囲の外」と明記）と同型の境界宣言。

このスレッドは resolve しない（見送りは resolve せず境界宣言を付けて
残置する運用 = `AGENTS.md` §3-3）。

### 連鎖更新

builder バイト変更（新値
`50a8d22861d7c5cc8c1e3752ee63df52ffe8176e7556c6d97c7e62857069b18d`）
+ 本節追記に伴い、`inputs/education_technique_lesson_manifest.json` の
`builder_provenance.builder_sha256`/`detail_record_sha256` を更新し、
manifest raw sha256 が変わったため `RUN9_CONTRACT.yaml` の
`education_technique_lesson_manifest_sha` を第5世代へ repin した（旧値
= 第4世代、第3巡対応時点の値。履歴は本記録 §12/§13/§14 参照）。
`pjs_consumed_inputs_manifest_sha`（`inputs/pjs_consumed_inputs_
sha256.json`）は本節の変更で一切触れていないため無変更（本節はいずれの
消費入力の実バイト・pin 値にも影響しない、検証・公開経路のみの変更）。

### run7: repo builder（第4巡対応後）による再現実行（独立7回目）

venv_h3b の python3 で、修正後の repo builder を workdir 展開済み corpus
（`expanded/PJS_corpus_ver1.1`）に対し `build` サブコマンドで実行した。
freeze record・spec・split manifest・contract・consumed-inputs manifest
はいずれも repo 収載の既定パス（CLI 引数省略、デフォルト値のまま）。

```
$ python3 education_lesson_builder.py build \
    --corpus-root <workdir>/expanded/PJS_corpus_ver1.1 \
    --out-dir <workdir>/run7_out \
    --allow-unpinned
```

| バンドル | 既 pin（run1〜run6） | run7 sha256 | 一致 |
|---|---|---|---|
| training | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | **PASS** |
| validation | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | **PASS** |

run7 は本節の2修正がいずれも検証・公開経路（`--out` alias preflight・
assemble の pinned manifest 照合）のみに閉じており、抽出式・
アラインメント・直列化・バンドル内容に一切影響しないことの実測証跡で
ある——run7 は `--allow-unpinned` で実行した（manifest 側
`builder_provenance.builder_sha256` を本節の builder バイト変更後の値へ
まだ repin していない時点での実測取得のため）。repin 完了後、
`_require_bundle_bytes_match_pinned_manifest()` を run7 の実測 sha に
対して直接呼び出し、例外を投げないこと（＝ canonical path 上で Fix 5
（第2巡）のゲートが引き続き正しく機能すること）を確認済み。

### 検証結果

- `ruff check .`: clean
- `python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`:
  本節（builder_provenance.builder_sha256/detail_record_sha256 の repo
  収載値更新、`education_technique_lesson_manifest_sha` の5世代目 repin
  を含む）最終稿確定後に全 PASS。
- freeze record（`inputs/h3b_freeze_record.json`）: 無変更（第1〜3巡と
  同じ理由——repo builder の identity は manifest 側 `builder_provenance`
  が別途担う）。

## 16. PR #329 Codex bot レビュー第5巡対応 + run8（2026-08-27）

Claude 完結ルート（フェーズ1: 実装 + 検証 + 返信起草。git commit/push は
別フェーズ）。Fable 採否判定 = 採用2件（いずれも P1）/ 限定採用1件（P2、
境界宣言 + 記録是正、実装なし）。**抽出式・アラインメント規則・
直列化・バンドル内容は無変更**（変更は検証・公開経路のみ、第1〜4巡と
同じ不変条件）。

### Fix 15（採用1, P1）: `publish_bundle_pair()` の backup 失敗と「旧世代
なし」の混同

`_backup_existing()` が2連 `os.replace()` 前の退避（path → backup 名）
自体に失敗すると、`training_backup`/`validation_backup` への代入が完了
せず変数は初期値 `None` のまま残る——これは「publish 開始前に旧世代が
存在しなかった」ケースと `_rollback_to_backup()` から見て区別がつかず、
無傷で残っている旧公開ファイルを誤って `unlink()` していた（1本目
backup 失敗で旧2本とも削除、2本目 backup 失敗で旧 validation を削除）。
第3巡で追加した失敗注入テスト（`..._second_backup_failure_restores_
first_and_leaves_no_debris`）はディレクトリ異常系を使っており、
`unlink()` が `IsADirectoryError` で失敗して握りつぶされるため、たまたま
この欠陥を検出できていなかった。

- 新設 `_BackupOutcome`（frozen dataclass、`moved: bool` /
  `backup_path: Optional[Path]`）: 「backup 未着手/失敗（source 未移動）」
  「backup 完了（source 移動済み）」「backup 完了かつ旧世代なし」の3状態
  を明示的に区別する。`_BACKUP_NOT_ATTEMPTED`（`moved=False`）で初期化
  してから `_backup_existing()` の戻り値を包む構成にすることで、呼び
  出しが例外で失敗して代入が完了しなくても状態は「未着手/失敗」のまま
  安全側に倒れる。
- `_rollback_to_backup()`: `outcome.moved` が `False` のときは `path` に
  一切触れない。`True` かつ `backup_path` があれば復元、`True` かつ
  `backup_path` が `None` なら部分公開を削除する（旧来の意味論を
  `moved=True` の場合にのみ限定）。
- `publish_bundle_pair()`: `training_backup`/`validation_backup`
  （素の `Optional[Path]`）を `training_outcome`/`validation_outcome`
  （`_BackupOutcome`）へ置き換えた。
- 失敗注入回帰テスト2件（通常ファイルを対象、`_backup_existing()` 内部の
  `os.replace(path, backup_path)` 自体を monkeypatch で失敗させる）:
  1本目 backup 失敗 / 2本目 backup 失敗、いずれも旧公開ペアが両方無傷で
  残ることを確認する。**現行バグを再現するテストを先に書いて FAIL を
  確認してから修正で PASS にする手順**を踏んだ——修正前コードに対して
  実行し、1本目失敗テストは training/validation とも `FileNotFoundError`
  （削除された）で FAIL、2本目失敗テストは validation が削除されて FAIL
  することを確認済み。

### Fix 16（採用2, P1）: assemble の保護集合に検証器の入力 closure を含める

`--out inputs/education_technique_lesson_manifest.json` 等で、第4巡新設
の pinned 検証器（`assemble` の `_require_single_split_bundle_bytes_
match_pinned_manifest()` / `run_build()` の `_require_bundle_bytes_
match_pinned_manifest()`）が実際に読む manifest 本体 + cross-check
closure（builder 本体・裁定 txt・freeze record 現行/superseded・detail
record・consumed-inputs pin・contract）が alias preflight の保護集合から
丸ごと欠落しており、検証成功後（改変前バイトに対する検証は通過する）に
`write_bundle_bytes()` がその pin 済みファイルをバンドル JSON で上書き
し、「検証成功」を返しながら pin 済み provenance を破壊し得た。

- 新設定数（`run9_schema.EDUCATION_MANIFEST_PATH` 参照 + builder 自身の
  `Path(__file__).resolve()` + 4新設定数）: `DEFAULT_EDUCATION_MANIFEST_
  PATH` / `_THIS_MODULE_PATH` / `DEFAULT_ADJUDICATION_BASIS_PATH` /
  `DEFAULT_SUPERSEDED_FREEZE_RECORD_PATH` / `DEFAULT_DETAIL_RECORD_PATH`。
- 新設 `_pinned_education_lesson_manifest_cross_check_paths(*,
  contract_path)`: `load_pinned_education_lesson_manifest()` の
  cross-check closure 全体（manifest 本体 + 裁定 txt + builder 本体 +
  spec + freeze record 現行/superseded + detail record + consumed-inputs
  pin + contract）を1箇所へ集約して返す共有ヘルパ。
- `_extract_song_protected_paths()`/`_probe_header_protected_paths()`/
  `_assemble_protected_paths()` の3関数すべてに上記ヘルパの戻り値を
  合流させた——`extract-song`/`probe-header` は現時点でこの closure を
  自身の検証経路として読まないが、将来の `--out` 誤指定でこれらの pin
  済み provenance ファイルを破壊しないよう一貫して保護する（「コマンド
  が読む全入力 = 保護集合」の対応漏れを構造的に防ぐ）。
- 新設テスト: `assemble --out` = education manifest 本体 / 裁定 txt /
  freeze record（現行）の3ファイルへの直接指定がいずれも拒否され、対象
  ファイルの実バイトが無傷のまま残ることを確認する CLI レベルテスト
  （`--intermediates-dir` に実在しない中間物を指定——alias preflight が
  中間物読み取りより前に実行されるため、preflight が正しく拒否する限り
  実リポジトリファイルへ書き込むことはなく、preflight が拒否し損なって
  も後続の中間物読み取りが `FileNotFoundError` で先に失敗するため実
  ファイル破壊のリスクなしに検証できる）。加えて `_extract_song_
  protected_paths()`/`_probe_header_protected_paths()` が closure 全体を
  含むことを直接確認する単体テスト2件。

### 限定採用1（P2、境界宣言 + 記録是正、実装なし）: P5 training-distribution
separation 記録の stale 是正

グラウンディング: `evaluation/probe_manifest.json` の P5 cell
`deferred_verification` ブロック（`blocked_by`:
`practice_audio_split_manifest_sha`/`education_technique_lesson_
manifest_sha`）と `run9_schema.py` の `_P5_DEFERRED_VERIFICATION_
BLOCKED_BY` 周辺コメント/`_validate_p5_deferred_verification()`
docstring を読解した。RUN9_CONTRACT.yaml を確認すると、
`education_technique_lesson_manifest_sha` は HARNESS-3b（本記録）で既に
PINNED 化されており、`practice_audio_split_manifest_sha` も既 PINNED
——`blocked_by` の2欄要件はともに充足済みであることを確認した。

しかし、実際の分離検証手続き（P5 cell の kana/pitch_midi 実値と実学習
素材の実体を照合する extractor/harness）は、今ある成果物（education
manifest / lesson バンドル sha / split manifest 等の sha pin）だけでは
機械的に実行できない——P5 の検証が要求するのは note レベルの実体照合
だが、education lesson manifest が pin するのは音響特徴チャンネル
（relative F0 contour 等）の sha256 のみで note レベル content は
（rights 制約により）repo に一切収載されておらず、development/
generalization 軸（P4/P5、GENERALIZED_GAIN を含む）の extractor 自体が
VG-L0 学習ハーネス未実装のため repo に実在しない（`measurement_spec_
sha` は development_generalization_axis について引き続き PENDING、
README.md 該当節・`inputs/measurement_spec_manifest.json` scope_note が
既に正直に記録済み）。したがって本件は「学習フェーズの成果物等、未存在
の入力を要する場合」に該当すると Fable が設計判定し、**status
（`TRAINING_DISTRIBUTION_SEPARATION_NOT_YET_VERIFIABLE`）は変更せず**、
stale になった理由文のみを contract/schema コメントの該当箇所で正直に
更新した。

- `RUN9_CONTRACT.yaml`（`education_technique_lesson_manifest_sha` 欄
  上方、Fix 32 repin コメントブロック）: append-only 規約で新規履歴注記
  （2026-08-27）を追記——両 pin 欄が PINNED 化された事実 + それでも
  extractor 不在のため検証は依然実行不能である旨 + 再入条件
  （`measurement_spec_sha` の development_generalization_axis 節が
  PINNED 化される時点 = VG-L0 学習ハーネス実装時点）を明記。
- `run9_schema.py`: `_P5_MIDI_LOW`/`_P5_MIDI_HIGH` 直後の Fix 32 コメント
  ブロック・`_P5_DEFERRED_VERIFICATION_BLOCKED_BY` 直前のコメント・
  `_validate_p5_deferred_verification()` docstring の3箇所に同内容の
  履歴注記（append-only）を追記。検証ロジック（`_validate_p5_deferred_
  verification()` の実装本体）・`_P5_DEFERRED_VERIFICATION_BLOCKED_BY`
  の値・`P5_DEFERRED_VERIFICATION_STATUS` の値はいずれも無変更。
- `evaluation/probe_manifest.json` 自体は改変していない（`probe_
  manifest_sha` PINNED・凍結境界。`blocked_by` 凍結集合も既存設計
  （発行時点凍結・自動解除なし）どおり不変）。

このスレッドは resolve しない（限定採用は境界宣言を付けて残置する運用
= `AGENTS.md` §3-3 と同じ扱い）。

### 連鎖更新

builder バイト変更（新値
`816f765e6aa707ca5f1363c566b0980cbcfc5f6559950070a4d3e71732e3ca12`）
+ 本節追記に伴い、`inputs/education_technique_lesson_manifest.json` の
`builder_provenance.builder_sha256`/`detail_record_sha256` を更新し、
manifest raw sha256 が変わったため `RUN9_CONTRACT.yaml` の
`education_technique_lesson_manifest_sha` を第6世代へ repin した（旧値
= 第5世代、第4巡対応時点の値。履歴は本記録 §12/§13/§14/§15 参照）。
`pjs_consumed_inputs_manifest_sha`（`inputs/pjs_consumed_inputs_
sha256.json`）は本節の変更で一切触れていないため無変更（本節はいずれの
消費入力の実バイト・pin 値にも影響しない、検証・公開経路のみの変更）。

### run8: repo builder（第5巡対応後）による再現実行（独立8回目）

python3（本セッションの system python3、pyworld 0.3.5 を含む依存関係が
揃った環境）で、修正後の repo builder を workdir 展開済み corpus
（`expanded/PJS_corpus_ver1.1`）に対し `build` サブコマンドで実行した。
freeze record・spec・split manifest・contract・consumed-inputs manifest
はいずれも repo 収載の既定パス（CLI 引数省略、デフォルト値のまま）。

```
$ python3 education_lesson_builder.py build \
    --corpus-root <workdir>/expanded/PJS_corpus_ver1.1 \
    --out-dir <workdir>/pr329_round5_build_out \
    --allow-unpinned
```

| バンドル | 既 pin（run1〜run7） | run8 sha256 | 一致 |
|---|---|---|---|
| training | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | **PASS** |
| validation | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | **PASS** |

run8 は本節の Fix 15/16 がいずれも検証・公開経路（`publish_bundle_
pair()` のロールバック状態表現・alias preflight 保護集合）のみに閉じて
おり、抽出式・アラインメント・直列化・バンドル内容に一切影響しないこと
の実測証跡である——run8 は `--allow-unpinned` で実行した（manifest 側
`builder_provenance.builder_sha256` を本節の builder バイト変更後の値へ
まだ repin していない時点での実測取得のため）。repin 完了後、
`_require_bundle_bytes_match_pinned_manifest()` を run8 の実測 sha に
対して直接呼び出し、例外を投げないこと（＝ canonical path 上で Fix 5
（第2巡）のゲートが引き続き正しく機能すること）を確認済み。

### 検証結果

- `ruff check .`: clean
- `python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`:
  本節（builder_provenance.builder_sha256/detail_record_sha256 の repo
  収載値更新、`education_technique_lesson_manifest_sha` の6世代目 repin
  を含む）最終稿確定後に全 PASS。
- freeze record（`inputs/h3b_freeze_record.json`）: 無変更（第1〜4巡と
  同じ理由——repo builder の identity は manifest 側 `builder_provenance`
  が別途担う）。

## 17. PR #329 Codex bot レビュー第6巡対応 + run9（2026-08-27）

Claude 完結ルート（フェーズ1: 実装 + 検証 + 返信起草。git commit/push は
別フェーズ）。Fable 採否判定 = 採用1件（P1）。**抽出式・アラインメント
規則・直列化・バンドル内容は無変更**（変更は CLI 引数解析・検証経路の
みで、第1〜5巡と同じ不変条件）。

### Fix 17（採用, P1）: CLI の split/contract/pin 参照を repo 正典へ固定

`education_lesson_builder.py` の公開 CLI が `--contract-path`（および
`--split-manifest`/`extract-song`・`build` の `--consumed-inputs-
manifest`）を受け取っていたため、呼び出し元が contract のコピーを改変
（sealed ID を training へ移した split を repin + 対応する
consumed-inputs pin を用意）して渡すと、`load_training_validation_ids()`/
`load_consumed_inputs_pins()` はいずれも `run9_schema.load_pinned_*()`
経由の pin 検証を実行するが、その検証基準自体（`RUN9_CONTRACT.yaml`）を
呼び出し元が差し替えられるため両 loader が代替 contract に対して
検証 PASS してしまい、sealed WAV が decode され得た——**コード記述なしの
CLI 操作だけで凍結境界を再定義できる**公開経路だった。第1〜5巡の対策
（sealed_holdout 非参照・pin 検証・TOCTOU 閉鎖・alias 拒否・原子的
公開）はいずれも「pin 検証を通ったか」を問うものであり、「pin 検証の
基準そのものが差し替え可能」という本穴の脅威モデルには対処していな
かった。

- `main()` の全4サブコマンド（`probe-header`/`extract-song`/`assemble`/
  `build`）から `--contract-path`/`--split-manifest`/`--consumed-inputs-
  manifest` を撤去した。CLI 経路は常に repo 正典パス（`_THIS_DIR` 起点の
  `RUN9_CONTRACT.yaml`/`inputs/practice_audio_split_manifest.json`/
  `inputs/pjs_consumed_inputs_sha256.json`）へ固定される——
  `_cmd_probe_header()`/`_cmd_extract_song()`/`_cmd_assemble()`/
  `_cmd_build()` は `load_training_validation_ids()`/
  `load_consumed_inputs_pins()`/`run_build()` へ常に `None`（各関数の
  既定値 = 正典パス）を渡す。
- `_probe_header_protected_paths()`/`_extract_song_protected_paths()`/
  `_assemble_protected_paths()`（`--out` alias preflight の保護対象パス
  ビルダー、第4〜5巡 Fix 13/16）も `args` からの split-manifest/
  contract-path override 読み取りを撤去し、保護対象を正典定数
  （`DEFAULT_SPLIT_MANIFEST_PATH`/`run9_schema.RUN9_CONTRACT_YAML_PATH`/
  `DEFAULT_CONSUMED_INPUTS_MANIFEST_PATH`）で固定した。
- パス引数は下位ライブラリ関数（`load_training_validation_ids()`/
  `load_consumed_inputs_pins()`）のシグネチャにはテスト分離目的
  （改ざん済み合成 manifest を注入する pytest fixture）でのみ残す。この
  層（repo 内から直接 import して呼び出すプロセス内 Python 呼び出し）に
  対する脅威モデルは、ランタイム検証ではなくコードレビュー/CI 層（grep
  監査・PR レビュー・discipline テスト）で防御する対象であり、
  `FrozenSplitPins`/`ConsumedInputPins` の直接構築に関する境界宣言
  （builder 冒頭「Opaque pin-verified types」節、第3巡 Fix 8）と同型の
  境界宣言として両関数の docstring に明記した。
- `--corpus-root`/`--out`/`--intermediates-dir`/`--out-dir` 等、音源・
  出力の場所指定は CLI に残した（撤去しない）——これらは境界を再定義
  できない非対称: 生成されたバンドルバイトは publish 前に
  `training_technique_lesson_sha256`/`validation_technique_lesson_sha256`
  pin と実バイト一致するまで `_require_bundle_bytes_match_pinned_
  manifest()`/`_require_single_split_bundle_bytes_match_pinned_
  manifest()` が fail-closed で拒否する（`--allow-unpinned` 未指定時）
  ため、コーパス・中間物の置き場所をどう指定しても pin と byte-identical
  な正準出力しか publish されない——「検証そのものの基準」を差し替えら
  れる `--contract-path`/`--split-manifest` とは性質が異なる。
  `--freeze-record`/`--spec-path`（`extract-song`/`build` に残存）も
  同型の非対称——`freeze_selfcheck()` は渡された spec の実バイト sha256
  を freeze record 自身が保持する `spec_sha256` と自己照合するのみで
  外部基準を持たず、最終的にバンドルバイトが pin と一致しない限り
  publish されないため、差し替えても「検証そのものの基準」の再定義には
  ならない。
- 新設テスト:
  - (a) 4サブコマンド × 撤去した3オプション（該当する組み合わせのみ）で
    CLI が argparse の「unrecognized arguments」で拒否し、終了コード2で
    `SystemExit` することの直接確認（`main()` の `try/except` より前段の
    argparse 自身のエラー経路であることの証跡）。
  - (b) `probe-header`/`extract-song`/`assemble`/`build` の CLI 経路が
    `load_training_validation_ids()` を常に `(None, contract_path=None)`
    で呼ぶことの直接証跡（下位 loader を monkeypatch で呼び出し引数を
    観測——短絡させて実 corpus I/O を伴わずに検証）。`extract-song`/
    `build` については同様に `load_consumed_inputs_pins()` も常に
    `(None, contract_path=None)` で呼ぶことを確認する。
  - `_probe_header_protected_paths()`/`_extract_song_protected_paths()`
    の cross-check closure 包含テスト（第5巡新設）は、撤去された CLI
    属性を `args` から除いた最小 Namespace で引き続き PASS することを
    確認した（保護対象が正典定数へ固定されたため属性自体が不要になった
    ことの直接証跡）。

### 連鎖更新

builder バイト変更（新値
`57899deeeb9360477d1a0f4adceb288815bf5cebf0cbf0d87d6514f1399fae7e`）
+ 本節追記に伴い、`inputs/education_technique_lesson_manifest.json` の
`builder_provenance.builder_sha256`/`detail_record_sha256` を更新し、
manifest raw sha256 が変わったため `RUN9_CONTRACT.yaml` の
`education_technique_lesson_manifest_sha` を第7世代へ repin した（旧値
= 第6世代、第5巡対応時点の値。履歴は本記録 §12/§13/§14/§15/§16 参照）。
`pjs_consumed_inputs_manifest_sha`（`inputs/pjs_consumed_inputs_
sha256.json`）は本節の変更で一切触れていないため無変更（本節はいずれの
消費入力の実バイト・pin 値にも影響しない、CLI 引数解析・検証経路のみの
変更）。

### run9: repo builder（第6巡対応後）による再現実行（独立9回目）

system python3（本セッションの system python3、pyworld 0.3.5 を含む
依存関係が揃った環境）で、修正後の repo builder を workdir 展開済み
corpus（`expanded/PJS_corpus_ver1.1`）に対し `build` サブコマンドで
実行した。freeze record・spec・split manifest・contract・
consumed-inputs manifest はいずれも repo 収載の既定パス（本巡の変更に
より CLI からはこれらを上書きする手段自体が存在しない——常に正典パス）。

```
$ python3 education_lesson_builder.py build \
    --corpus-root <workdir>/expanded/PJS_corpus_ver1.1 \
    --out-dir <workdir>/pr329_round6_build_out \
    --allow-unpinned
```

| バンドル | 既 pin（run1〜run8） | run9 sha256 | 一致 |
|---|---|---|---|
| training | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | **PASS** |
| validation | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | **PASS** |

run9 は本節の Fix 17 が CLI 引数解析・検証経路のみに閉じており、
抽出式・アラインメント・直列化・バンドル内容に一切影響しないことの
実測証跡である——run9 は `--allow-unpinned` で実行した（manifest 側
`builder_provenance.builder_sha256` を本節の builder バイト変更後の値へ
まだ repin していない時点での実測取得のため）。repin 完了後、
`_require_bundle_bytes_match_pinned_manifest()` を run9 の実測 sha に
対して直接呼び出し、例外を投げないこと（＝ canonical path 上で Fix 5
（第2巡）のゲートが引き続き正しく機能すること）を確認済み。

repin 完了後、`--allow-unpinned` を付けずに（＝ CLI からは
`--split-manifest`/`--contract-path`/`--consumed-inputs-manifest` の
いずれも渡す手段が存在しない、正典パスのみが使われる状態で）本
コマンドを独立10回目として再実行し、`pinned_manifest_check: "PASS"`
（training/validation とも run1〜run9 と同一 sha256）で正常完了する
ことを実測で確認した——本節の CLI 変更が既存の再現実行手順に一切影響
しないことの直接証跡である。

### 検証結果

- `ruff check .`: clean
- `python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`:
  本節（builder_provenance.builder_sha256/detail_record_sha256 の repo
  収載値更新、`education_technique_lesson_manifest_sha` の7世代目 repin
  を含む）最終稿確定後に全 PASS。
- freeze record（`inputs/h3b_freeze_record.json`）: 無変更（第1〜5巡と
  同じ理由——repo builder の identity は manifest 側 `builder_provenance`
  が別途担う）。

## 18. PR #329 Codex bot レビュー第7巡対応 + run10（2026-08-27）

Claude 完結ルート（フェーズ1: 実装 + 検証 + 返信起草。git commit/push は
別フェーズ）。Fable 採否判定 = 採用2件（P1 + P2）。**抽出式・アラインメント
規則・直列化・バンドル内容は無変更**（変更は検証・公開経路のみで、
第1〜6巡と同じ不変条件）。

### Fix 18（採用1, P1）: ハードリンク alias の検出

`_resolve_output_alias_conflict()`（第4巡 Fix 13）は (a) `Path.resolve()`
（symlink 解決込みの絶対化）と (b) `os.path.abspath()`（symlink 未解決の
lexical 絶対化）の二重パス比較で `--out` の alias を検出していたが、
`--out` が保護ファイル（対象曲の消費3入力・split manifest・contract・
consumed-inputs pin・pinned education manifest cross-check closure 等）
への**既存ハードリンク**（`os.link()`/`ln` で作成可能、別名の独立
ディレクトリエントリが同一 inode を対等に指す構造）を指す場合、
どちらの比較軸でも検出できなかった——ハードリンクは symlink と異なり
「別パスへの参照」ではないため、パス文字列比較や `resolve()`（symlink
追跡のみでハードリンク自体は追跡対象外の概念）では原理的に見分けが
付かない。それにもかかわらず `_cmd_extract_song()`/`_cmd_probe_header()`
の（修正前の）`write_text()`（truncate オープン）はハードリンク越しに
同じ inode の実バイトを書き換えるため、`--out` が保護ファイルへの既存
ハードリンクであれば preflight をすり抜けて保護入力を破壊し得た。

- `_hardlink_alias_conflict()` を新設し、`_resolve_output_alias_conflict()`
  の (a)(b) いずれとも不一致だった場合のフォールバックとして呼ぶ——
  `out_path` が既存ファイルなら、`protected_paths` の各既存ファイルと
  `os.path.samefile()`（内部で `st_dev`/`st_ino` を比較）で inode 単位の
  同一性を照合し、一致すればその protected path を返す。
- stat 失敗時の規約を明文化: `out_path` の存在確認自体が失敗する場合は
  「存在しない（＝これから新規作成される）」として hardlink 照合を丸ごと
  スキップする（存在しない/確認不能なパスはいずれの inode も指さない
  ため、`write_text()` がハードリンク越しに何かを破壊する経路はそもそも
  存在しない）。個々の `protected` の存在確認が失敗する場合は、その
  protected 単体を照合対象外としてスキップし、他の protected との照合は
  継続する（1件の確認不能で全件の照合を諦めない）。
- 新設テスト: `extract-song`/`probe-header` それぞれで、対象曲 WAV への
  既存ハードリンクを `--out` に渡すと decode/書き込み前に拒否され、
  ハードリンク元・先とも実バイトが無傷のまま残ることを CLI 経由で直接
  確認。加えて `_resolve_output_alias_conflict()` の単体テストで、
  stat 失敗規約（`out_path` 未存在時のスキップ、`protected` の一部
  不在時の継続照合）を直接確認する。

### Fix 19（採用2, P2）: extract-song/probe-header 中間物出力の atomic 化

`_cmd_extract_song()`/`_cmd_probe_header()` の出力書き込みは
`Path(args.out).write_text()` の直書き（truncate オープン）のままだった
——`assemble` サブコマンド（第3巡 Fix 11、`write_bundle_json()`/
`write_bundle_bytes()` 経由）とは異なり atomic 化されておらず、`--out`
が既存の有効な中間物 JSON を指す場合、`write_text()` の truncate
オープン後の書き込み途中失敗（ディスク枯渇・プロセス kill 等）で旧世代
中間物が破損した部分書き込みバイト列で上書きされ得た。

- `_cmd_extract_song()`/`_cmd_probe_header()`: 直列化バイト列を1回だけ
  組み立て、`write_bundle_bytes()`（`_atomic_write_bytes()` の staging+
  fsync + `os.replace()`、3コマンドすべてが共有する唯一の atomic 書き
  込み経路）へ渡す構成へ変更。バイト列自体（`indent=2` の probe-header
  出力／compact 区切りなしの extract-song 出力）は無変更——書き込み経路
  のみの変更。
- `write_bundle_bytes()` の docstring を更新し、「バンドル」出力に限らず
  本 builder の全出力コマンドが共有する汎用 atomic bytes writer である
  旨を明記（関数名は `assemble` 由来の "bundle" を残すが、新規呼び出し
  元追加時のリネームは不要——3コマンドすべてが単一実装を共有する現状が
  「単一実装の維持」そのものであるため）。
- `os.replace()` の rename 意味論が Fix 18 を補完する多層防御である旨を
  両呼び出し箇所へコメントで明記——`os.replace()` は最終名への rename に
  先立って target の既存ディレクトリエントリを置き換える（unlink+link
  ではなく rename）ため、万一 Fix 18 の preflight をすり抜けても、
  `--out` が保護ファイルへの既存ハードリンクである限り他のハードリンク
  エントリ（保護ファイル自身の本来のパス）が指す inode の実バイトには
  一切触れない。
- 失敗注入テスト（新設）: `extract_song()` 本体（decode）を monkeypatch
  で固定結果に差し替えたうえで `_atomic_write_bytes()` を monkeypatch で
  失敗させると、`extract-song`/`probe-header` いずれも旧世代の中間物
  JSON が無傷のまま残り、staging の残骸も残らないことを確認する
  （`assemble`/`write_bundle_json()` の既存失敗注入テストと同型）。

### 連鎖更新

builder バイト変更（新値
`8b3a9de1f147fa64cc04c1acc197de20a84c0ea82f24401ddee607724f534e7b`）
+ 本節追記に伴い、`inputs/education_technique_lesson_manifest.json` の
`builder_provenance.builder_sha256`/`detail_record_sha256` を更新し、
manifest raw sha256 が変わったため `RUN9_CONTRACT.yaml` の
`education_technique_lesson_manifest_sha` を第8世代へ repin した（旧値
= 第7世代、第6巡対応時点の値。履歴は本記録 §12/§13/§14/§15/§16/§17
参照）。`pjs_consumed_inputs_manifest_sha`（`inputs/pjs_consumed_inputs_
sha256.json`）は本節の変更で一切触れていないため無変更（本節はいずれの
消費入力の実バイト・pin 値にも影響しない、検証・公開経路のみの変更）。

### run10: repo builder（第7巡対応後）による再現実行（独立10回目）

system python3（本セッションの system python3、pyworld 0.3.5 を含む
依存関係が揃った環境）で、修正後の repo builder を workdir 展開済み
corpus（`expanded/PJS_corpus_ver1.1`）に対し `build` サブコマンドで
実行した。freeze record・spec・split manifest・contract・
consumed-inputs manifest はいずれも repo 収載の既定パス（CLI からは
これらを上書きする手段自体が存在しない——常に正典パス、第6巡 Fix 17）。

```
$ python3 education_lesson_builder.py build \
    --corpus-root <workdir>/expanded/PJS_corpus_ver1.1 \
    --out-dir <workdir>/pr329_round7_run10_out \
    --allow-unpinned
```

```
real 4m50.046s
```

| バンドル | 既 pin（run1〜run9） | run10 sha256 | 一致 |
|---|---|---|---|
| training | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | **PASS** |
| validation | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | **PASS** |

run10 は本節の2修正（ハードリンク alias 拒否・中間物出力の atomic 化）
がいずれも検証・公開経路のみに閉じており、抽出式・アラインメント・
直列化・バンドル内容に一切影響しないことの実測証跡である——run10 は
`--allow-unpinned` で実行した（manifest 側 `builder_provenance.
builder_sha256` を本節の builder バイト変更後の値へまだ repin していない
時点での実測取得のため）。repin 完了後、`--allow-unpinned` を付けずに
（＝ CLI からは正典パスのみが使われる通常経路で）本コマンドを独立11回目
として再実行し、`pinned_manifest_check: "PASS"`（training/validation
とも run1〜run10 と同一 sha256）で正常完了することを実測で確認した
——本節の変更が既存の再現実行手順に一切影響しないことの直接証跡である。

### 検証結果

- `ruff check .`: clean
- `python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`:
  本節（builder_provenance.builder_sha256/detail_record_sha256 の repo
  収載値更新、`education_technique_lesson_manifest_sha` の8世代目 repin
  を含む）最終稿確定後に全 PASS。
- freeze record（`inputs/h3b_freeze_record.json`）: 無変更（第1〜6巡と
  同じ理由——repo builder の identity は manifest 側 `builder_provenance`
  が別途担う）。

## 19. PR #329 Codex bot レビュー第8巡対応 + run11（2026-08-27）

Claude 完結ルート（フェーズ1: 実装 + 検証 + 返信起草。git commit/push は
別フェーズ）。Fable 採否判定 = 採用1件（P2）+ 限定採用1件（P1、crash-atomic
指摘 — backup 名決定論化 + 回復手順文書化のみ採用、世代ディレクトリ再設計
は境界宣言で見送り）。**抽出式・アラインメント規則・直列化・バンドル
内容は無変更**（変更は検証・公開経路のみ、第1〜7巡と同じ不変条件）。

### Fix 20（採用, P2）: 「repo 内で再導出可能」主張の是正（正直会計）

`RUN9_CONTRACT.yaml` の `education_technique_lesson_manifest_sha` reason
（および本記録 §0/§11・`README.md` の同型記述）が「repo canonical
builder + repo 収載 corpus 由来ファイルにより決定論的に再導出可能」と
述べていたが、`inputs/education_technique_lesson_manifest.json` の
`corpus_provenance.audio_repo_contained` 自身が `false` を宣言していると
おり、消費入力3点（wav/lab/musicxml）・展開済み corpus・バンドル実体は
いずれも repo 非収載——「repo 収載 corpus」という記述はこの宣言と直接
矛盾していた。fresh checkout からの再導出には、repo canonical builder に
加えて **外部 PJS corpus ver1.1 の再取得**（zip sha256 `683c0025…` の
厳密一致 → plain unzip → 展開後 `expanded_corpus_identity_sha256`
`9905cec0…` 照合という fail-closed 手順、per-file 消費入力 pin との照合
込み）と、User の scoped 承認（裁定 §1 は明示的に session-scoped の承認
であり、無条件の恒久許可ではない）が前提として必要——この2条件を欠いた
状態では「決定論的に再導出可能」という主張自体は成り立たない（builder
自体は決定論的であっても、その入力である外部音源の取得経路が省略されて
いた）。

- `RUN9_CONTRACT.yaml` の当該 reason 文言を訂正し、旧文言を同一
  コメント内へ引用形式で保持した（本ファイル §「〔第9世代 repin〕」
  コメント参照。reason 本文の直接訂正であり、append-only 履歴規約に
  従い旧世代の pin 値自体は既存の履歴コメントとして別途保持されている
  ため、本文訂正は新たな repin を要する——builder バイト変更（下記
  Fix 21）と合わせて第9世代 repin とした）。
- `README.md` の同型記述（バンドル実体ファイル節）を同期是正。
- 本記録 §0（対象・入力・出力）・§11（成果物一覧）の同型記述を同期
  是正——grep で「再導出」「regenerat」を確認し、上記2箇所が該当する
  全数であることを確認した（他ファイルの「再導出」ヒットは founder
  genome の再正規化重み計算等、無関係な既存記述であることを個別に
  確認済み）。

### Fix 21（限定採用, P1）: crash-atomic 指摘 — backup/staging 名の決定論化
+ stale backup の fail-closed 検出 + 手動回復手順の文書化

`publish_bundle_pair()`（第1〜5巡で段階的に強化してきた atomic ペア
公開 + ロールバック機構）は `except BaseException` を経由できる失敗には
すべて対応済みだったが、**プロセスが `except` を一切経由せず死ぬケース**
（SIGKILL・電源断・OOM killer）には原理的に対応できない——ロールバック
のコード自体が実行されないためである。加えて `_backup_existing()`/
`_atomic_write_bytes()` はいずれも `tempfile.mkstemp()` によるランダム名
を使っており、この種の中断で staging/backup の残骸が残っても「どの公開
試行の、どの段階の残骸か」をファイル名から特定できず、手動回復の起点に
ならなかった。

- **採用部分**: backup 名を `<final-name>.h3b-backup`、staging 名を
  `<final-name>.h3b-staging` という決定論的固定名へ変更（`_backup_
  path_for()`/`_staging_path_for()` 新設）。決定論名にしたことで、
  `_backup_existing()` はもはや `mkstemp()` による空プレースホルダの
  事前作成を経由せず `os.replace(path, backup_path)` を直接試みる
  構成になり、第3巡指摘4が対応したプレースホルダ孤児化経路自体が構造的
  に消滅した（rename(2) は target の事前存在を要求せず、成功/失敗いずれ
  でも部分状態を残さない）。
- `publish_bundle_pair()` は staging/backup/rename いずれの操作も行う
  **前**に、`_backup_path_for(training_path)`/`_backup_path_for(
  validation_path)` のいずれかが既に存在するかを確認する——存在すれば
  「前回 publish が完了前に中断された痕跡」として fail-closed で
  `RuntimeError` を送出し、一切の書き込みを行わずに拒否する（stale
  backup を無条件に上書き・消失させない）。
- **手動回復手順**（`publish_bundle_pair()` docstring に文書化。中断点
  ごとの状態表、`p.h3b-backup`/`p.h3b-staging` と略記）:

  | # | 中断のタイミング | 観測される状態 | 次回呼び出し | 回復コマンド |
  |---|---|---|---|---|
  | 1 | 両 staging 書き込み完了前 | `training_path`/`validation_path` とも無傷。`*.h3b-staging` が最大2本残ることがある。backup は一切存在しない | stale backup なし → 通常どおり成功 | 任意（`*.h3b-staging` を `rm` すれば残骸なしに戻るが、次回実行時に上書きされるため必須ではない） |
  | 2 | training の `_backup_existing()` 完了後、validation の同完了前 | `training_path` が消え `training_path.h3b-backup` に旧世代あり。`validation_path` は無傷。staging 2本とも存在 | **fail-closed 停止**（training 側 stale backup 検出） | `mv training_path.h3b-backup training_path` で旧世代を復元してから builder を再実行する |
  | 3 | 両 `_backup_existing()` 完了後、training の `os.replace(tmp, path)` 前 | 両 `*.h3b-backup` とも存在。`training_path`/`validation_path` とも消えている。staging 2本とも存在 | **fail-closed 停止** | 両方とも `mv <name>.h3b-backup <name>` で復元してから builder を再実行する |
  | 4 | training の replace 完了後、validation の replace 前 | `training_path` は新世代。`training_path.h3b-backup` に旧世代が残ったまま。`validation_path` は消えたまま（旧世代は `validation_path.h3b-backup`）——新世代 training + 欠落 validation という混合状態 | **fail-closed 停止** | 推奨: 両方 backup から復元して完全ロールバックしてから builder を再実行する。上級者向け（非推奨）: `training_path` の sha256 が pin 値と一致することを確認できる場合に限り、`training_path.h3b-backup` を削除し `validation_path.h3b-staging` を手動 `mv` で完成させてから `validation_path.h3b-backup` を削除する |
  | 5 | 両 replace 完了後、`_discard_backup()` 実行前 | 両ファイルとも新世代（正しい最終状態）。`*.h3b-backup` 2本は不要だが残っている | **fail-closed 停止**（実害なし） | 両 backup の sha256 が既知の旧世代と一致することを確認したうえで `rm *.h3b-backup` ×2 |

- **見送り部分（境界宣言）**: 世代ディレクトリ + 単一ポインタ切替への
  再設計。理由: ① POSIX で2ファイルの同時 rename は不可能であり、単一
  切替には出力レイアウト契約（`--out-dir` 直下の固定2ファイル名を
  消費・再現手順が参照）の変更を要する——本 PR の範囲を超える設計変更。
  ② 混合ペアは下流で成功として消費され得ない——消費は `load_pinned_
  education_lesson_manifest()` の sha 照合 fail-closed が強制し、部分/
  混合状態は検証で即検出される。③ 旧バイトは決定論 backup 名の下に
  保全され消失しない（採用部分で回復手順まで文書化済み）。再開条件:
  出力レイアウト契約の改版を伴う設計 revision で再検討。
- テスト（新設）: `_backup_path_for()`/`_staging_path_for()` の決定論性
  直接確認、`_backup_existing()`/`_atomic_write_bytes()` が実際に固定名
  を使うことの確認、training/validation 双方の stale backup 単体検出 +
  fail-closed（一切書き込みなし・既存ファイル無傷）、エラーメッセージが
  両方のフルパスを列挙することの確認。既存の失敗注入テスト群（第2〜5巡）
  は決定論名前提へ追随（`.prevgen.tmp` → `.h3b-backup` のマッチ文字列
  更新。第3巡のディレクトリ構造異常系テストは、決定論名によって
  「ディレクトリの backup rename が自然に失敗する」という旧前提が崩れた
  ため、monkeypatch による明示的失敗注入へ書き換えた——POSIX の
  rename(2) は空ディレクトリを未存在の target 名へ rename することを
  許可するため、`mkstemp()` プレースホルダ衝突という偶発的失敗経路が
  決定論名では発生しなくなったことによる）。

### 連鎖更新

builder バイト変更（新値 = 下記参照）+ reason 文言訂正（Fix 20）+ 本節
追記に伴い、`inputs/education_technique_lesson_manifest.json` の
`builder_provenance.builder_sha256`/`detail_record_sha256` を更新し、
manifest raw sha256 が変わったため `RUN9_CONTRACT.yaml` の
`education_technique_lesson_manifest_sha` を第9世代へ repin した
（旧値 = 第8世代、第7巡対応時点の値。履歴は本記録 §12/§13/§14/§15/
§16/§17/§18 参照）。`pjs_consumed_inputs_manifest_sha`（`inputs/pjs_
consumed_inputs_sha256.json`）は本節の変更で一切触れていないため無変更
（本節はいずれの消費入力の実バイト・pin 値にも影響しない、検証・公開
経路 + reason 文言のみの変更）。

### run11: repo builder（第8巡対応後）による再現実行（独立11回目）

system python3（本セッションの system python3、pyworld 0.3.5 を含む
依存関係が揃った環境）で、修正後の repo builder を workdir 展開済み
corpus（`expanded/PJS_corpus_ver1.1`）に対し `build` サブコマンドで
実行した。

```
$ python3 education_lesson_builder.py build \
    --corpus-root <workdir>/expanded/PJS_corpus_ver1.1 \
    --out-dir <workdir>/run11 \
    --allow-unpinned
```

```
real	4m24.944s
```

| バンドル | 既 pin（run1〜run10） | run11 sha256 | 一致 |
|---|---|---|---|
| training | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | **PASS** |
| validation | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | **PASS** |

run11 は本節の2修正（backup/staging 名の決定論化 + stale backup 検出・
reason 文言訂正）がいずれも検証・公開経路 + ドキュメント記述のみに閉じて
おり、抽出式・アラインメント・直列化・バンドル内容に一切影響しないことの
実測証跡である——run11 は `--allow-unpinned` で実行した（manifest 側
`builder_provenance.builder_sha256`/`detail_record_sha256` を本節の
builder バイト変更・本記録追記後の値へまだ repin していない時点での
実測取得のため）。repin 完了後、`--allow-unpinned` を付けずに（＝ CLI
からは正典パスのみが使われる通常経路で）本コマンドを独立12回目として
再実行し、`pinned_manifest_check: "PASS"`（training/validation とも
run1〜run11 と同一 sha256）で正常完了することを実測で確認した——本節の
変更が既存の再現実行手順に一切影響しないことの直接証跡である。

### 検証結果

- `ruff check .`: clean
- `python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`:
  本節（builder_provenance.builder_sha256/detail_record_sha256 の repo
  収載値更新、`education_technique_lesson_manifest_sha` の9世代目 repin
  を含む）最終稿確定後に全 pass（2279 passed）。
- freeze record（`inputs/h3b_freeze_record.json`）: 無変更（第1〜7巡と
  同じ理由——repo builder の identity は manifest 側 `builder_provenance`
  が別途担う）。

## 20. PR #329 Codex bot レビュー第9巡対応 + run13（2026-08-27）

Claude 完結ルート（フェーズ1: 実装 + 検証 + 返信起草。git commit/push は
別フェーズ）。Fable 採否判定 = 採用2件（いずれも P1）。**どちらも第8巡
（§19 Fix 21「backup/staging 名の決定論化」）が導入した退行の是正で
あり、正直にそう記録する**——決定論名化そのものの狙い（回復手順の
追跡可能性）は誤りではなかったが、その生成方式（無条件 truncate 上書き）
に新たな穴が残っていた。**抽出式・アラインメント規則・直列化・バンドル
内容は無変更**（変更は検証・公開経路のみ、第1〜8巡と同じ不変条件）。

### Fix 22（採用1, P1）: 固定 staging 名の並行 publish 競合 — O_EXCL 排他
生成への是正

§19 Fix 21 は backup/staging 名を `<final-name>.h3b-backup`/`<final-name>.
h3b-staging` の決定論的固定名へ変更したが、staging の**生成方式**自体は
`open(tmp_path, "wb")` の無条件 truncate 上書きのままだった。固定名 **かつ**
無条件上書きの組み合わせは、同一最終 path（`training_path`/
`validation_path`）へ**同時に publish しようとする2プロセス**が同一の
固定 staging 名を共有する、という §19 時点で未検討だった新規の穴を生んで
いた: プロセス A が staging へ書き切って fsync した直後、`os.replace()`
する前にプロセス B が同じ staging 名へ `open(tmp_path, "wb")` して A の
staging バイトを B のバイトで差し替え得る——A はその後 A 自身が書いた
はずのバイトの sha256 を「publish 成功」として報告しながら、実際に
`os.replace()` されて最終名に現れるのは B のバイトである、という偽成功
（hash A を報告しつつ実体は B を publish）が発生し得た。

- `_atomic_write_bytes()` の staging 生成を `os.open(tmp_path, os.O_CREAT
  | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644)` の**排他生成**へ
  変更した。既存の staging（並行 publish の相手が埋めたもの・前回中断の
  残骸のいずれであれ）は `FileExistsError`（`errno.EEXIST`）で fail-closed
  拒否され、staging も backup も rename も一切行われない——stale backup
  検出（§19）と同型のエラーメッセージ体裁（何が残っているか・3通りの
  原因候補・回復手順への参照）を採用した。
- 決定論名（`<final-name>.h3b-staging`）は維持する——§19 の回復手順文書
  （中断点ごとの状態表）と整合させるため、名前自体は変えない。排他生成
  により「同名を複数の書き手が静かに共有する」経路そのものが構造的に
  消滅したことを `_atomic_write_bytes()`/`_staging_path_for()` の
  docstring に明記した。
- `publish_bundle_pair()` docstring の手動回復手順表（§19 由来）の
  # 1 行（両 staging 書き込み完了前）を是正した——§19 時点の記述
  「stale backup なし → 通常どおり成功」は本節の変更後は誤りになる
  （leftover `*.h3b-staging` は次回呼び出しで `O_EXCL` により fail-closed
  停止するようになった）ため、表を実際の挙動へ追随させた。回復コマンド
  も「任意（rm すれば戻るが必須ではない）」から「次回成功の必須条件と
  して rm する」へ訂正した。
- テスト（新設）: (c) 通常ファイルが staging 名に事前配置されたケース
  （並行 publish/残骸のシミュレーション）で `EEXIST` fail-closed +
  既存 staging バイト無傷を確認、`publish_bundle_pair()` 経由の並行
  publish シミュレーション（training 側 staging を先に埋めてから
  `publish_bundle_pair()` を呼び、fail-closed + 最終名/backup とも
  一切生成されないことを確認）、排他生成へ変更後も staging 名が事前に
  存在しない通常経路が引き続き成功することの回帰確認。

### Fix 23（採用2, P1）: staging パス自体の alias 追従 — preflight の
検査対象拡張

既存の `<output>.h3b-staging`（または `<output>.h3b-backup`）が保護
ファイルへの symlink/hardlink である場合、旧 `open(tmp_path, "wb")` は
symlink を追従し、または hardlink 越しに同一 inode の実バイトを書き
換えるため、保護対象入力（split manifest・contract・consumed-inputs
pin・spec・freeze record・pinned education lesson manifest cross-check
closure 等）を truncate し得た。`_require_out_does_not_alias_protected_
paths()`（第4巡新設の preflight、`--out` 本体のみを保護集合と照合）は
`--out` の**派生パス**（staging/backup 名）自体をこれまで一切検査
対象に含めていなかった——`--out` 本体が無害でも、攻撃者が
`<out>.h3b-staging`/`<out>.h3b-backup` という派生名の側へ事前に保護
ファイルへの symlink/hardlink を配置しておけば、この preflight は
素通りしてしまう構造的な穴があった。

- Fix 22 の `O_EXCL|O_NOFOLLOW` 排他生成は、既存の staging directory
  entry が symlink/hardlink いずれの alias であっても「path が既に
  存在する」というだけで拒否する（POSIX `open(2)`: `O_CREAT|O_EXCL` は
  symlink の場合その指す先に一切関わらず `EEXIST` で失敗する）ため、
  この alias 追従を書き込み時点で構造的に閉鎖することを確認した——
  `_atomic_write_bytes()` docstring に POSIX 意味論の根拠を明記した
  （symlink/hardlink いずれの alias 種別も「既存の同名 staging への
  open」という単一の失敗モードに帰着する）。
- その上で、`_resolve_output_alias_conflict()` の保護衝突 preflight の
  検査対象に **`--out` の staging/backup 派生パス**（`_staging_path_for(
  out_path)`/`_backup_path_for(out_path)`）も含めるよう
  `_require_out_does_not_alias_protected_paths()` を拡張した——`--out`
  本体だけでなく派生名が保護ファイルと衝突・alias するケースを、実際の
  書き込み（Fix 22 の `O_EXCL` 拒否という最終防衛線）へ到達する**前**の
  より早い段階で、明確な理由（どの候補パス・どの保護ファイルと衝突
  したか）とともに拒否できるようにした（`extract-song`/`assemble`/
  `probe-header` の3コマンドが共有する preflight）。
- テスト（新設）: (a) 保護ファイルへの symlink を staging 名へ事前配置
  → `_atomic_write_bytes()` が拒否 + 保護ファイル無傷（`O_EXCL` 単体での
  構造的閉鎖の直接証跡）、(b) 同 hardlink → 同様に拒否 + 無傷、(d) 派生
  パス preflight（`_require_out_does_not_alias_protected_paths()`）の
  直接単体テスト——staging 派生パス側の alias（symlink）・backup 派生
  パス側の alias（hardlink）それぞれを独立に確認し、`--out` 本体側の
  既存契約（第4巡以来）が拡張後も引き続き機能することの回帰確認も
  追加した。

### 連鎖更新

builder バイト変更（新値 = 下記参照）+ 本節追記に伴い、`inputs/
education_technique_lesson_manifest.json` の `builder_provenance.
builder_sha256`/`detail_record_sha256` を更新し、manifest raw sha256 が
変わったため `RUN9_CONTRACT.yaml` の `education_technique_lesson_
manifest_sha` を第10世代へ repin した（旧値 = 第9世代、第8巡対応時点の
値。履歴は本記録 §12/§13/§14/§15/§16/§17/§18/§19 参照）。
`pjs_consumed_inputs_manifest_sha`（`inputs/pjs_consumed_inputs_sha256.
json`）は本節の変更で一切触れていないため無変更（本節はいずれの消費
入力の実バイト・pin 値にも影響しない、検証・公開経路のみの変更）。

### run13: repo builder（第9巡対応後）による再現実行（独立13回目）

system python3（本セッションの system python3、pyworld 0.3.5 を含む
依存関係が揃った環境）で、修正後の repo builder を workdir 展開済み
corpus（`expanded/PJS_corpus_ver1.1`）に対し `build` サブコマンドで
実行した。

```
$ python3 education_lesson_builder.py build \
    --corpus-root <workdir>/expanded/PJS_corpus_ver1.1 \
    --out-dir <workdir>/run13 \
    --allow-unpinned
```

| バンドル | 既 pin（run1〜run12） | run13 sha256 | 一致 |
|---|---|---|---|
| training | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | **PASS** |
| validation | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | **PASS** |

run13 は本節の2修正（staging 排他生成 + preflight 派生パス検査拡張）が
いずれも検証・公開経路のみに閉じており、抽出式・アラインメント・直列化・
バンドル内容に一切影響しないことの実測証跡である——run13 は
`--allow-unpinned` で実行した（manifest 側 `builder_provenance.
builder_sha256`/`detail_record_sha256` を本節の builder バイト変更・本
記録追記後の値へまだ repin していない時点での実測取得のため）。repin
完了後、`--allow-unpinned` を付けずに（＝ CLI からは正典パスのみが使わ
れる通常経路で）本コマンドを独立14回目として再実行し、
`pinned_manifest_check: "PASS"`（training/validation とも run1〜run13 と
同一 sha256）で正常完了することを実測で確認した——本節の変更が既存の
再現実行手順に一切影響しないことの直接証跡である。

### 検証結果

- `ruff check .`: clean
- `python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`:
  本節（builder_provenance.builder_sha256/detail_record_sha256 の repo
  収載値更新、`education_technique_lesson_manifest_sha` の10世代目 repin
  を含む）最終稿確定後に全 pass（2287 passed）。repin 後の完全正典実行
  （`--allow-unpinned` なし、run14 相当）で `pinned_manifest_check:
  "PASS"` を実測確認済み（本節末尾参照）。
- freeze record（`inputs/h3b_freeze_record.json`）: 無変更（第1〜8巡と
  同じ理由——repo builder の identity は manifest 側 `builder_provenance`
  が別途担う）。

## 21. PR #329 Codex bot レビュー第10巡対応 + run15（2026-08-27）

Claude 完結ルート（フェーズ1: 実装 + 検証 + 返信起草。git commit/push は
別フェーズ）。Fable 採否判定 = 採用2件（いずれも P2）。**抽出式・
アラインメント規則・直列化・バンドル内容は無変更**（変更は正直な記述の
是正 + 検証経路のみ、第1〜9巡と同じ不変条件）。**本巡でレビュー対応の
上限10巡に到達した——以後は同一領域の指摘に対し、CLAUDE.md「bot レビュー
対応の運用」節の3分類（実コード被害/将来汚染・致命的バグ）に該当する
新しい具体的な経路を示す指摘のみを採用し、他は境界宣言で止める運用へ
移行する**（本節末尾も参照）。

### Fix 24（採用1, P2）: 「sealed ID は一切読まれない」主張の正直是正

`load_training_validation_ids()`（`education_lesson_builder.py`）は
split manifest の件数検証（training 70 / validation 15 / sealed_holdout
15）のために `row_ids.sealed_holdout` を実際に読んでいた（
`_PRACTICE_SPLIT_EXPECTED_COUNTS` との照合、第1巡 Fix 1 で導入済み）に
もかかわらず、module docstring・当該関数上部コメント・
`FrozenSplitPins` docstring・本記録 §0/§10 の一部が「sealed_holdout の
row_ids はいかなるコードパスにも現れない」「一切参照しない」という、
実装より強い保証を謳っていた。

- `education_lesson_builder.py`: module docstring「禁止事項」節・
  `load_training_validation_ids()` 直前のセクションコメント・
  `FrozenSplitPins` docstring の3箇所を、「sealed_holdout の row_ids は
  件数検証・3集合非交差検証（cardinality/disjointness verification）の
  ためにのみ読まれ、列挙して decode/特徴抽出/lesson 生成/出力へ含める、
  といった用途には一切使われない」という正確な記述へ是正した。あわせて
  「sealed_holdout row_ids に触れるコードは本ファイル中この1関数のみ」
  であることを明記し、件数検証を読む箇所が構造的に単一関数へ隔離済み
  であることを宣言した（`load_training_validation_ids()` は元々この
  検証専用の関数であり、追加のコード分割は不要だった）。
- `run9_schema.py`（連鎖）: `_require_disjoint_row_id_sets()`（3集合
  非交差検証）と `load_pinned_dataset_split_manifest()` の per-split
  digest 照合節も、sealed_holdout の row_ids を件数/hash 再計算のために
  読む——いずれも既存実装時点で単機能の小関数へ既に隔離されており（前者
  は disjoint 検証専用、後者は dataset split manifest の digest 照合
  専用）、構造変更は不要と判断した（Fable 設計判定: 「可能なら構造も
  明確化」は既に構造的に満たされている箇所への追加リファクタは、
  17,000行超の共有モジュールへの不要な変更面積を増やすだけでリスクに
  見合わないため見送り、境界宣言として記録するのみに留める）。
- `HARNESS3B_EDUCATION_LESSON_RECORD.md`: §0 の記述（「完全性hash・ID
  確認以外一切処理していない」）はもともと正確（件数/ID確認自体が
  sealed_holdout を読むことを前提とした記述）だったため無変更。§10
  （「どのコードパスにも sealed_holdout の row_id は現れない」）は
  Fix 1（§12、第1巡）で件数検証読み取りが導入される**前**の baseline
  時点の記述として文字通り正確だったが、現行実装とは一致しないため、
  過去の記録を書き換えずに追記で正直に訂正した（§10 該当行末尾参照）。
- grep 監査（`sealed`・「いかなるコードパスにも現れない」・「一切参照
  しない」等の同型主張文字列）で `education_lesson_builder.py`/
  `HARNESS3B_EDUCATION_LESSON_RECORD.md`/`README.md`/`run9_schema.py`/
  `HARNESS3B_EXTRACTOR_SPEC.md` を全数掃討した。`HARNESS3B_EXTRACTOR_
  SPEC.md`（凍結 pin 対象、§7「sealed の row_ids はコードパス上、列挙に
  入らない」）はスコープが「対象列挙」に限定された記述であり元々正確
  だったため無変更（この spec ファイルを変更すると `spec_sha256` の
  freeze record 連鎖 repin が必要になり本巡の範囲を超えるため、実害の
  ない箇所への変更は見送った）。`README.md` の該当箇所（件数の言及・
  「完全性hash・ID確認以外の処理を一切行っていない」）はいずれも
  §0 と同型の正確な記述であり無変更。
- 新設ユニットテストなし（本 Fix は記述の是正のみで、機械強制対象の
  挙動そのものに変更はない——`_PRACTICE_SPLIT_EXPECTED_COUNTS` の件数
  機械強制自体は既存テストで既に被覆済み）。

### Fix 25（採用2, P2）: builder pin をロード済みコードへ束縛

`run9_schema.load_pinned_education_lesson_manifest()` の cross-check
(b)（`builder_provenance.builder_sha256` 照合）は従来、publish 直前に
ディスク上の `education_lesson_builder.py` を毎回再 `read_bytes()` する
のみだった。長時間の build プロセス中に（本モジュールが import 済みで
実行され続けた後で）checkout 上のこのファイルが差し替えられても、
publish 時点でディスクバイトが pin 値へ戻っていれば cross-check は
PASS してしまい、実際にロードされ実行され続けたコード（差し替え後の
別バイト列）が pin 検証を経ずに publish される穴があった（逆に、正当に
ロード済みの pin 一致バイト列を publish 直前にのみ一時的に差し替えて
偽 FAIL を起こすことも同型に可能だった）。

- `education_lesson_builder.py` に module-level 定数
  `_BUILDER_SOURCE_SHA256_AT_LOAD = hashlib.sha256(Path(__file__).
  read_bytes()).hexdigest()` を新設した——import 時（モジュールロード
  時点）に1回だけ本ファイル自身を read し、以後プロセスが生きている
  限り値が変わらない "photograph" として固定する。既存の
  `sha256_of_self()`（呼び出し時点で毎回ディスクを再読む informational
  ユーティリティ）とは意味論が異なることを両者の docstring に明記した。
- `run9_schema.load_pinned_education_lesson_manifest()` に省略可能
  引数 `loaded_builder_sha256: Optional[str] = None` を新設した。渡さ
  れた場合、cross-check (b) はロード時捕捉値・ディスク実バイトの
  **両方**を pin 値と照合し、不一致の種別を区別する:
  (i) ロード時捕捉値が pin と不一致 → 実際に実行され続けたコードが
  そもそも pin と一致しない（最重大——ディスクが後で pin 一致バイトへ
  戻っていても関係なく拒否）。(ii) ロード時捕捉値は pin と一致するが
  ディスク実バイトが load 時捕捉値と不一致 → ロード後に checkout 上の
  ファイルが差し替えられた証跡として拒否。(iii) 両方一致 → PASS。
  引数省略時（`None`、既定）は従来どおりディスク実バイトのみで照合する
  ——後方互換（テスト層の単体呼び出し等）。
- `education_lesson_builder.py` の publish 呼び出し2箇所
  （`_require_bundle_bytes_match_pinned_manifest()`（`run_build()` 用）
  ・`_require_single_split_bundle_bytes_match_pinned_manifest()`
  （`assemble` 用））を `loaded_builder_sha256=_BUILDER_SOURCE_SHA256_
  AT_LOAD` を渡すよう更新した。
- **正直な残存窓（境界宣言、両 docstring に明記）**: (i) import 前
  （プロセス起動〜本モジュールの最初の import 実行までの間）にファイル
  が差し替えられた場合、捕捉自体が既に差し替え後バイトを読む——この窓は
  本機構では閉じない。(ii) `.pyc` 経由ロード時、実行中のバイトコードと
  ここで比較するソースバイトが理論上乖離し得る（通常の CPython import
  経路では `.py` の mtime 変化で自動再コンパイルされるため実務上は稀
  だが、本機構がこれを機械的に検証・排除しているわけではない）。両窓
  とも運用（fresh checkout からの起動・`.pyc` キャッシュの明示的無効化）
  で緩和する対象であり、本機構が構造的に閉じるものではない。
- 新設ユニットテスト（5件、`tests/test_education_lesson_builder.py`）:
  (a) `_BUILDER_SOURCE_SHA256_AT_LOAD` が本ファイル実バイトの sha256と
  一致する健全性確認、(b) 独立コピーへの動的 import で "photograph" 性
  （ロード後のディスク書き換えに追従しないこと）を確認、(c)
  `loaded_builder_sha256` が pin と最初から不一致な場合の拒否、(d)
  pin と `loaded_builder_sha256` を一致させた合成値のもとで実ディスク
  （実ファイル）が食い違う場合に「ロード後の差し替え」文言で拒否される
  （2段階検証の monkeypatch 証跡）、(e) `builder_provenance.repo_
  relative_path` を repo 内一時 fixture へ差し替え、load 時捕捉値・
  ディスク実バイトの両方を pin と一致させたケースが PASS することを
  確認。

### 連鎖更新

builder バイト変更（Fix 24 のコメント是正 + Fix 25 の
`_BUILDER_SOURCE_SHA256_AT_LOAD` 新設・呼び出し2箇所更新）+ 本節追記に
伴い、`inputs/education_technique_lesson_manifest.json` の
`builder_provenance.builder_sha256`/`detail_record_sha256` を更新し、
manifest raw sha256 が変わったため `RUN9_CONTRACT.yaml` の
`education_technique_lesson_manifest_sha` を第11世代へ repin した
（旧値 = 第10世代、第9巡対応時点の値。履歴は本記録 §12/§13/§14/§15/
§16/§17/§18/§19/§20 参照）。`run9_schema.py` の
`load_pinned_education_lesson_manifest()` シグネチャ拡張（`loaded_
builder_sha256` 追加）は cross-check (b) の**呼び出し方**を変えるのみ
で、抽出ロジック・manifest/contract の他フィールドには一切影響しない。
`pjs_consumed_inputs_manifest_sha` は本節の変更で一切触れていないため
無変更。

### run15: repo builder（第10巡対応後）による再現実行（独立15回目）

system python3（本セッションの system python3、pyworld 0.3.5 を含む
依存関係が揃った環境）で、修正後の repo builder を workdir 展開済み
corpus（`expanded/PJS_corpus_ver1.1`）に対し `build` サブコマンドで
実行した。

```
$ python3 education_lesson_builder.py build \
    --corpus-root <workdir>/expanded/PJS_corpus_ver1.1 \
    --out-dir <workdir>/run15 \
    --allow-unpinned
```

| バンドル | 既 pin（run1〜run14） | run15 sha256 | 一致 |
|---|---|---|---|
| training | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | `6e13d34298a8e3c8b8632cdddcc98077294980fcb3840bde4bc6a9bcae3528da` | **PASS** |
| validation | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | `b7a5c94a41ec618133d88cede31af51ee699d5677e0e410c4eadeba659ca9522` | **PASS** |

run15 は本節の2修正（Fix 24 の記述是正 + Fix 25 の builder pin ロード
時束縛）がいずれも記述/検証・公開経路のみに閉じており、抽出式・
アラインメント・直列化・バンドル内容に一切影響しないことの実測証跡
である——run15 は `--allow-unpinned` で実行した（manifest 側
`builder_provenance.builder_sha256`/`detail_record_sha256` を本節の
builder バイト変更・本記録追記後の値へまだ repin していない時点での
実測取得のため）。repin 完了後、`--allow-unpinned` を付けずに（＝ CLI
からは正典パスのみが使われる通常経路で）本コマンドを独立16回目として
再実行し、`pinned_manifest_check: "PASS"`（training/validation とも
run1〜run15 と同一 sha256）で正常完了することを実測で確認した（下記
「検証結果」参照）——本節の変更が既存の再現実行手順に一切影響しない
ことの直接証跡である。

### 検証結果

- `ruff check .`: clean
- `python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short`:
  本節（builder_provenance.builder_sha256/detail_record_sha256 の repo
  収載値更新、`education_technique_lesson_manifest_sha` の11世代目 repin
  を含む）最終稿確定後に全 pass。repin 後の完全正典実行（`--allow-
  unpinned` なし、run16 相当）で `pinned_manifest_check: "PASS"` を実測
  確認済み。
- freeze record（`inputs/h3b_freeze_record.json`）: 無変更（第1〜9巡と
  同じ理由——repo builder の identity は manifest 側 `builder_provenance`
  が別途担う）。

### 第10巡到達に伴う運用の切り替え（境界宣言）

CLAUDE.md「bot レビュー対応の運用」節が定める上限10巡に本巡で到達した。
以後、同一領域（本ファイル・`run9_schema.py` の当該関数群）への新規
レビュー指摘は、3分類（実コード被害/将来汚染・致命的バグ）に該当する
新しい具体的な経路を示すものに限り採用し、それ以外は境界宣言で止める
運用へ移行する——本記録・PR タイムラインへの記載をもって運用切り替えの
記録とする。
