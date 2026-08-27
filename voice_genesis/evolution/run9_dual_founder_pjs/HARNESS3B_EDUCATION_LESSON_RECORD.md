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
