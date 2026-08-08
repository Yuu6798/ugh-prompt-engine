# M3d 校正実測 — 事前登録（実測前凍結）

**日付:** 2026-08-07
**正本:** docs/DESIGN_M3_melody_comparator.md §6（本書は §6 の実行パラメータ具体化のみ。§6 と矛盾したら §6 が勝つ）
**状態:** ハーネス/レジストリ/テストは #233 でマージ済み。残 = 実測のみ。
**順序証明:** 本書 + `tests/fixtures/melody_bench/m3d_pairs_manifest.yaml`（98 対、
単一 manifest）の commit が実測開始前の事前登録点。tuning run → evaluate → 凍結
commit → holdout run の順序は git 履歴とハーネスの holdout ロック（凍結前は
holdout 行をスタブ化し音声を読まない）で証明する。

**実装時に確定した事項（実測開始前・本 commit 時点）:**
- 規模は §1.1 のフル規模（4 変形/clip）を採用。縮約規則は不使用（変形範囲 ±2..5 半音 /
  rate 0.87–1.12 の被覆を外挿なしで主張するため）
- pair の sha256 pin と material 区分はハーネス凍結スキーマ（pair 6 キー固定・未知キー拒否）
  により manifest 非同梱とし、sidecar `build/external_m3d/m3d_pairs_pins.json` + pair_id
  命名規則（`_real_` / `_synth_`）で保持する
- 変形 WAV の書き出しは PCM_24 subtype（FLOAT subtype は libsndfile が PEAK chunk に
  壁時計を埋めるためバイト決定論が壊れる — 実測で発見。librosa 変形自体は bit 決定論）
- 狙い撃ち negative（rhythm/interval）は spec 直記述の fixture 対（`m3d_synth_specs.yaml`）
- **manifest は単一ファイルのまま維持する**（Codex レビュー R2 対応・設計判定）:
  当初は real_voice/synthetic への 2 ファイル分割を検討したが、
  `run_melody_comparison._validate_manifest_composition`（tuning に狙い撃ち
  negative 必須・holdout に negative 必須、等）は単一 98 対 manifest を前提にした
  素材構成契約であり、分割後の real-only/synth-only manifest はいずれも単体では
  このローダを通らないことが実装検証で判明した（real-only は狙い撃ち negative が
  synth 専用のため tuning 狙い撃ち negative 0 件、synth-only は negative_cross を
  持たないため holdout negative 0 件）。ハーネスの構成契約を変えずに R2（Codex
  指摘: 全 positive 単一バケット・synth の not_comparable が real 由来の凍結提案
  まで巻き込む問題）へ対応するため、**素材別会計は `run_melody_comparison.py` の
  evaluate phase 側**（`_partition_pairs_by_material` / `material_accounting`）
  で行う——manifest は単一のまま、pair_id の `_real_`/`_synth_` マーカーで
  evaluator が real_voice（校正の唯一の入力）/ synthetic（診断専用。
  not_comparable は not_measured として正直会計するのみで凍結可否/holdout 判定に
  一切影響しない）を読み分ける。§1.3/§2 の別会計はこの機構で機械強制される
- sidecar（`build/external_m3d/m3d_pairs_pins.json`、非コミット）に manifest の
  sha256 を記録し、`--check-only` がスキーマ検証 + digest 照合（manifest 1 件 +
  全 WAV pin）を fail-closed で行う（Codex レビュー R1 対応。従来は sha256 を
  記録するのみで再照合していなかった）。限界の明記: 本 sidecar はビルド生成物で
  あり、順序証明の最終根拠は git 履歴 + ハーネスの holdout ロックのまま——本照合は
  事故的ドリフトを fail-closed 化する計器である
- 生成物一式（変形 WAV・manifest・pins sidecar）は staging ディレクトリ（out_dir
  と同一ファイルシステム上）へ全生成 → 全検証成功後に一括 atomic publish する
  （Codex レビュー R3 対応）。途中失敗時は既存の公開済みセットを無傷で残す保証を
  「再ビルド時」にも拡張した
- **（Codex レビュー第 2 ラウンド追補）** sidecar のスキーマを `m3d-pairs-pins/0.3`
  へ改版し、build 入力（`m2c_external_fixtures.yaml` / `m3d_synth_specs.yaml`）
  双方の sha256 を記録・`--check-only` で再照合する（従来は fixtures 側のみ・
  synth specs は無 pin だった）。両入力とも hash 計算とパースを同一バイト列から
  行う構造（TOCTOU 解消）へ変更した
- 生成（librosa/build_signal 呼び出し）を開始する前に、公開予定の全出力先
  （out_dir 配下の生成 WAV・manifest-out・pins-out）を resolve() し、出力同士の
  重複・出力と入力（fixtures yaml・synth specs yaml・vocadito WAV 全件）の衝突を
  fail-closed で拒否する
- アトミック公開の publish ループが成功した場合、退避しておいた `.prev`
  snapshot を全て削除する（失敗時のロールバック経路の挙動は不変更）
- `run_melody_comparison.py` の evaluate phase 側 `material_accounting.synthetic`
  に holdout split の synth pair 全件を pair_id → 行単位の状態
  （`locked_skipped`/`not_comparable`/`measured`+evidence/axes）で列挙する
  per-row 診断テーブルを追加した。holdout ロック中（凍結前）は evidence に一切
  触れず `locked_skipped` として列挙するのみで、既存の holdout ロック規律を
  厳守する。calibration verdict への影響はゼロ（診断専用）
- **（Codex レビュー第 3 ラウンド追補）** vocadito 音声読込経路（`librosa.load`）
  は無改造のまま、staging が完成し公開を開始する直前に全 vocadito 入力 WAV を
  再度 `verify_vocadito_pins` で pin 照合する（T2 対応）。**境界宣言の撤回**:
  第 2 ラウンド時点では「pin 照合〜`librosa.load` 読込の窓でファイルが差し
  替えられた場合、未承認バイト由来の変形 WAV が公開されうる」ことを対応範囲外
  の残存懸念として記録していたが、本工程により実質解消した——読込経路自体は
  変わらないため生成は起こり得るが、1 件でも不一致なら公開自体を fail-closed
  で中止する（既公開セットは無傷）。**（第 4 ラウンド追補・F1）** この
  publish 直前再照合は `svp_rpe.utils.hashing.file_sha256` を
  `use_cache=False` で呼ぶよう明示した——同関数は既定で (path, size,
  mtime_ns) をキーに digest をプロセス内キャッシュするため、既定のままだと
  size/mtime を保った内容差し替え（`os.utime` で mtime を復元する改ざん）を
  見逃し、この再照合工程自体が無効化される穴があった。`--check-only`
  （`check_existing`）の vocadito pin 再照合も同じ観点でキャッシュバイパスへ
  統一した（manifest/build 入力の digest 照合はもともと `file_sha256` を
  経由せず毎回生バイトを読む実装だったため対象外）。初回照合（`run_build`
  内・生成開始前）はキャッシュ有効のまま維持する（正当な高速化）
- fixtures（`m2c_external_fixtures.yaml`）ロード直後に全 clip_id を、
  `m3d_synth_specs.yaml` ロード直後に全 fixture id を、それぞれ許可文字集合
  （英数字・アンダースコア・ハイフンのみ）で字句検証する（T3 対応）。パス
  区切り・`..`・絶対パスはこの集合の外にあるため機械的に拒否される。SYNTH_*
  module 定数にもインポート時に同じ検証を適用し、加えて生成先ファイルパスを
  実際に構築する箇所でも resolve() 後のパスが staging dir/out_dir 配下に
  内包されることを確認する多層防御を追加した
- `run_melody_comparison.py` の manifest ロード検証（`_validate_manifest`。
  run phase）に、material 判別マーカー（`_real_`/`_synth_`）を持たない
  pair_id の manifest を拒否する検査を前倒しした（T1 対応）——従来は
  evaluate phase でのみ検査しており、マーカー無し manifest でも高価な抽出
  run が最後まで走ってしまっていた。後方互換でマーカー無しを許容する道は
  採らない（別会計の fail-closed 規律を崩すため）。evaluate phase 側の検査は
  defense-in-depth として残す
- **（Codex レビュー第 4 ラウンド追補・F2）** アトミック公開
  （`_publish_staged_bundle`）の rollback が、退避 rename
  （`final_path` → `snapshot_path`）自体が失敗/中断した場合に mkstemp の
  空 placeholder を有効な退避物として誤って復元し、無傷の destination を
  空ファイルで上書き（切り詰め）てしまう穴を修正した。rename は atomic な
  ため、rollback 実行時点の `final_path` の存在有無で「退避 rename が実際に
  完了したか」を判定できる——存在するなら退避は未完了（destination は無傷の
  まま）で placeholder のみ削除し、存在しないなら snapshot からの復元が正当。
  成功時の `.prev` 掃除（N2）・従来の rollback（R3）の既存挙動は変更しない
- **（Codex レビュー第 5 ラウンド追補・G1）** `m3d_synth_specs.yaml` の
  パースを素の `yaml.safe_load` から、`m2c_external_fixtures.yaml` で既に
  使っていた重複 mapping キー拒否ローダ（`_NoDupSafeLoader` /
  `_yaml_load_no_dup_keys`）へ切り替えた。素の `safe_load` は重複キーで
  最後の値を黙って採用するため、事前登録済み刺激（狙い撃ち negative の
  rhythm/interval spec 等）が無警告で置換されうる穴があった。同ローダは
  `yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG` に対して登録されている
  ため、トップレベルの `fixtures` キー重複だけでなく個々の fixture spec 内の
  ネストしたキー重複も再帰的に拒否する。`m2c_external_fixtures.yaml` 側は
  既に同ローダを使用済みのため無改造（ファミリー掃討の確認のみ）
- **（Codex レビュー第 5 ラウンド追補・G2）** `--summary-out` を preflight の
  保護対象に編入した（`_reject_output_input_collisions` の出力同士の重複
  検査・出力と入力の衝突検査の両方、`run_build`/`check_existing` 双方の
  経路）——従来は無検査で公開完了後に書かれており、`--check-only
  --summary-out <manifest>` のような指定は検証直後に manifest を自壊させる
  穴があった。書き込み自体も atomic 規律に統合する: `run_and_publish` 経路
  は summary を manifest/pins/生成 WAV と**同じ** `_publish_staged_bundle`
  atomic bundle へ 1 エントリとして編入する（同関数は既に「全部揃って初めて
  意味を持つ 1 組」を一括公開する構造を持つため、summary もそこへ乗せるのが
  自然——独立の別 atomic write を追加すると manifest/pins は公開済みだが
  summary だけ失敗する新たな部分成功状態を作ってしまう）。一方
  `--check-only`（`check_existing`）は生成を行わない読み取り専用経路であり
  atomic bundle インフラを使わないため、衝突検査のみ `check_existing` 内で
  行い、実際の単独書き込みは従来どおり `main()` 側の `_atomic_write_bytes`
  で行う（読み取り専用経路に新たな publish 状態を持ち込まない設計判定）
- **（Codex レビュー第 6 ラウンド追補・H1）** pins サイドカーの `material`
  マップは従来 mapping であることしか検証しておらず、`--check-only` は中身
  （キー/値）を manifest と一切突合していなかった——real_voice→synthetic
  への書換・エントリ削除・stale エントリ追加のいずれも `OK` で素通りする穴
  があった。検証済み manifest（pair_id の `_real_`/`_synth_` マーカー）から
  `_expected_material_map` で期待値を独立に再計算し、sidecar 記録との
  **完全一致**（キー集合・値とも）を要求するよう `check_existing` を拡張した。
  `run_and_publish` 側の記録経路も同じ関数を通すよう統一（単一の真実源）。
  `audio_sha256` 側の同種の「余剰キー」完全性は独立検証を追加していない
  ——stale な余剰 `audio_sha256` エントリが単独で発生するのは manifest.yaml
  自体の改変を伴う場合のみで、その場合は `manifest_sha256` 不一致が既に
  fail-closed で捕捉する（`material` は manifest.yaml に一切現れないため
  この保護が構造的に効かず、H1 は distinct な穴だった）
- **（Codex レビュー第 6 ラウンド追補・H2）** summary はアトミックバンドルの
  一員として公開されるようになった（G2）が、pins_doc に path/digest が記録
  されておらず、公開後の事後編集が `--check-only` に不可視だった。
  `--summary-out` 指定時は pins_doc へ `summary_path`（repo-relative）/
  `summary_sha256` を **optional** フィールドとして記録する（スキーマ
  version は `m3d-pairs-pins/0.3` のまま bump しない——必須フィールド追加や
  既存フィールドの意味論変更を伴わない純粋な追加的緩和であり、summary 非
  使用ビルドの既存 sidecar はそのまま有効という 0.3 の後方互換規約に合致
  するため）。`check_existing` は記録がある場合のみ現物とのバイト sha256 を
  fail-closed 検証（記録があるのに現物欠落も fail）し、記録が無ければ
  スキップする。この検証は今回の呼び出しに渡す `summary_out` 引数の有無とは
  独立——sidecar が過去に記録した summary の整合性そのものを守るための照合
  である。`--check-only` は pins.json を一切書き換えない（read-only 原則を
  維持——summary pin の記録は `run_and_publish` 経路でのみ行う）契約へ整理
  し、「検証（sidecar 記録の整合性 + G2 の衝突検査）→ 書込」の順序は
  `check_existing`/`main()` の呼び出し順そのものから構造的に保証されるため、
  追加の順序制御コードは不要とした

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
