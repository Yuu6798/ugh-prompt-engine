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
- **（Codex レビュー第 7 ラウンド追補・P1）** 事前登録の実行手順（本節）は
  生成 → `run_melody_comparison.py` へ直行するが、従来ハーネスは
  `m3d_pairs_pins.json` を一切読まなかった——公開後に WAV/manifest が改変
  されても run×2 が改変バイトをそのまま新 digest として記録し、evaluate も
  その manifest に束縛されるため、fail-closed 信号なしで改変刺激が校正に
  混入し得た。pin 検証を任意の standalone（`--check-only`）から、ハーネスの
  **強制 preflight** へ昇格した: `run_comparison` に `pins_path` 引数（CLI
  `--pins`）を追加し、publishable な run（`route_runner` 非注入 = 実抽出
  経路）はこれを必須化（未指定は抽出開始前に fail-closed）。抽出
  （`runner()` 呼び出し）を一切開始する前に (a) sidecar のスキーマ/必須
  フィールド検証 (b) manifest 現物のバイト sha256 と sidecar 記録の一致
  (c) manifest が参照する全 audio path の sidecar 記録存在 + 現物 digest
  一致（キャッシュを踏まない実バイト読み）を検証し、1 件でも不一致・欠落
  なら run 全体を中止する。読み取りロジックは builder（
  `scripts/build_m3d_pairs.py`）の権威スキーマを、ハーネス側で必要最小限
  （schema/manifest_sha256/audio_sha256 の 3 フィールドのみ）に独立複製する
  （builder はハーネスを import しない設計のため——`_material_of_pair_id`
  と同じパターン）。holdout ロック規律との整合: preflight の digest 照合は
  「音声をセンサーに掛ける」のではなくバイト検証のため事前登録の順序証明を
  壊さない——安全側の判断として、ロック中でも holdout pair の WAV を含め
  **全 pair** を照合する。run report には `pins_preflight_verified`（bool）・
  `pins_path`・`pins_sha256` を記録する（evaluate 側での追加検証は今回
  スコープ外・記録のみ）。`--check-only` は builder 側の独立した点検計器
  として引き続き残置する（本 preflight は必要最小限の検証のみで build 入力
  pin・material マップ・summary pin 等 builder 固有の完全性検査までは代替
  しない）。手順を「生成 → **pins preflight 付き** run×2 → evaluate → …」
  へ改訂（下記 §0 参照）
- **（同追補・第 8 ラウンド継続対応・J1・二段構え）** 第 7 ラウンドの一括
  preflight は抽出開始**前**の一度きりの検証であり、preflight と実際の
  抽出消費（`_freeze_audio_copy` が pair ごとに読む bytes）の間には時間の
  窓が空く——長時間 run 中に WAV が置換されると、`pins_preflight_verified:
  true` を掲げたまま置換バイトがそのまま記録・評価されてしまう（両 repeat
  で持続すれば未承認刺激が校正へ混入し得る）。`_freeze_audio_copy` が既に
  計算している凍結コピー（=抽出器が実消費する bytes）の digest を、pair
  ごとに sidecar の期待値（`_run_pins_preflight` が読み込み済みの
  `audio_sha256` テーブルを再利用——ファイルの再読みはしない）と再照合する
  工程を追加した（`_verify_frozen_copy_against_pins`。`runner()` 呼び出しの
  直前に置き、不一致は fail-closed で run 全体を中止）。preflight（一括
  事前検証・早期失敗の利便）と本工程（pair ごとの実消費バイト拘束・真の
  保証）は**二段構え**であり、片方だけでは TOCTOU の窓を閉じきれない
- **（同追補・第 8 ラウンド継続対応・J2・pins の出力衝突保護 + スコープ
  ノート訂正）** run CLI の `--out` protected-path 集合に `--pins`（resolve
  済み）を編入した——未編入のままだと `--out` = `--pins` の場合、preflight
  検証成功後に report の `_atomic_write_text` が sidecar 自体を run report で
  上書き破壊し、成功終了したように見えてしまう。**第 7 ラウンドの final
  report で「pins は preflight 完了後に書き込みが起こるだけだから実害なし」
  と記した判断を本ラウンドで撤回する**——その判断は事前登録の運用（同一
  `--pins` を run×2 で 2 回使う）の **2 回目 repeat** を見落としていた:
  1 回目の `--out`=`--pins` 実行で sidecar 自体が破壊されれば、2 回目の run
  はもはや正しい pins sidecar を読めない

## 0. 完走の定義（STATUS.md P2 キューの 5 手順）

1. pairs manifest 作成（vocadito positive 変形対 + negative_cross/rhythm/interval、tuning/holdout split）→ **実測前に commit（事前登録）**
2. `run_melody_comparison.py` **pins preflight 付き** run ×2（repeats、crepe_direct 経路・`--pins` に builder が公開した `m3d_pairs_pins.json` を指定——未指定・pin 不一致は抽出開始前に fail-closed。Codex レビュー第 7 ラウンド対応）
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

### 1.2 negative_cross（実声・異曲対）
- tuning: tuning clip の original 同士を id 昇順で環状ペア（i, i+1）= 12 対
- holdout: 同規則 6 対
- 追加抽出コストゼロ（original を再利用）

### 1.3 合成素材（synthetic。M2 の S-direct=fail 実測を踏まえ**別会計**）
- 生成: `build_melody_bench.build_signal` を library 利用、M3d 専用 spec
  `tests/fixtures/melody_bench/m3d_synth_specs.yaml`（新規。凍結済み synthesis_specs.yaml は不変更）
- positive: 合成旋律 2 本（tuning 1 / holdout 1）× 移調 +3 / 変速 0.9 = 4 対
- **狙い撃ち negative**（軸単独弁別の診断）:
  - negative_rhythm: 同音程列・別リズム（フレーズ内 note タイミングを変えた spec 対）tuning 2 対。
    **（Codex レビュー第 9 ラウンド追補・K2・実測前の事前登録修正）**
    当初案は「フレーズ内一様なテンポ差」（note_dur_sec/note_gap_sec を一様スケール）
    で b 側を作る設計だったが、M3 の IOI/duration 表現（`build_sequences`）は
    連続比の log2 のみを見る**テンポ不変**設計のため、一様スケールされたテンポ差は
    両側とも全ゼロの log 比列に潰れ、rhythm 軸の類似度が構造的に 1.0 になり
    「同音程・別リズム」の診断が成立しない欠陥があった（保証された非分離を比較器の
    欠陥と誤帰属しうる）。加えて対 2 の当初案はフレーズ間 gap の差にも依拠していたが、
    比較器はフレーズ分割後に軸を計算するためフレーズ間 gap の差は比較対象に現れない
    （不可視）。実測開始前（本実測データ収集前）に是正: b 側をフレーズ内非一様
    （長短交互パターン。`scripts/build_melody_bench.py` に `note_durs_sec`——フレーズ内
    ノート単位で note_dur_sec を上書きできる任意フィールド——を新設）に設計変更した。
    「診断が構造的に成立しうる fixture である」ことは `tests/test_melody_comparison.py`
    の `test_m3d_negative_rhythm_pair{1,2}_is_structurally_diagnosable` が
    `build_sequences` を直接通した機械アサートで保証する（同音程列を保った上で
    duration/IOI の log2 比列が両側とも全ゼロにはならず実質的に相違することを確認）。
    manifest の pair 構成（pair_id・パス）は builder（`scripts/build_m3d_pairs.py`）の
    静的な命名規則にのみ依存し spec の note タイミング値には依存しないため、本修正で
    不変のまま（構造上自明——確認済み）。
  - negative_interval: 同リズム・別音程列（phrases の音程だけ差し替え）tuning 2 対
- **フォールバック意味論（事前登録）**: 合成素材が M1 観測ゲートまたは crepe 抽出で
  not_comparable に落ちた場合、当該対は「not_measured」として正直会計し、
  **軸校正の成否判定は vocadito 系（1.1+1.2）のみで行う**。合成の狙い撃ち検証は
  診断情報（成立すれば軸弁別の傍証、落ちれば S-direct 帯の既知欠測）であり、
  校正成立のゲートには入れない。

### 1.4 crepe 起動数会計（pair 基準。Codex レビュー第 9 ラウンド追補・K3）

**訂正の経緯**: 従来の §1.1 は「18 original + 72 variant = 90 file × run2 = 180 crepe 起動」
というファイル基準の誤算だった——ハーネス（`run_melody_comparison.run_comparison`）は
観測を音声ファイル単位でキャッシュせず、**pair ごとに `route_runner` を 2 回呼ぶ**
（`audio_a`/`audio_b` それぞれ 1 回。同一ファイルが複数 pair から参照されても
pair の数だけ呼ばれる——例えば negative_cross は original ファイルの再利用でも
「追加抽出コストゼロ」ではなく pair 数 × 2 回の起動が発生する）。holdout ロック中
（凍結前）の holdout pair は音声を一切読まずスタブ化される（`status:
holdout_locked_until_frozen`）ため 0 起動。以下は manifest 実物（98 対。
`build_m3d_pairs.crosstab` で確認済み）から数えた正確な内訳。

**pair 内訳**（tuning / holdout）:

| kind | tuning | holdout | 計 |
|---|---|---|---|
| positive_transform（vocadito） | 48 | 24 | 72 |
| negative_cross（vocadito） | 12 | 6 | 18 |
| positive_transform（synth） | 2 | 2 | 4 |
| negative_rhythm（synth） | 2 | 0 | 2 |
| negative_interval（synth） | 2 | 0 | 2 |
| **計** | **66** | **32** | **98** |

**起動数（full 規模。§0 の 5 手順に対応）**:

| フェーズ | 対象 pair | 1 run あたり起動数（pair×2） | 回数 | 小計 |
|---|---|---|---|---|
| tuning phase run（手順 2。holdout ロック中） | tuning 66 対 | 132 | ×2（repeats） | 264 |
| holdout 検証 run（手順 5。holdout unlock 後・全 98 対処理） | 98 対（tuning 66 + holdout 32） | 196 | ×1（一度検証） | 196 |
| **総計** | | | | **460** |

（holdout 検証 run は「holdout pair だけ」を選んで走らせる仕組みをハーネスは持たず、
manifest 全体を毎回処理する——unlock 後は tuning 66 対も再度起動される。tuning phase
run の内訳は negative_cross のみ切り出すと 12 対 × 2 = 24 起動/run、のように pair 種別
ごとに機械的に算出できる。）

**半減 fallback 適用時**（variant を pitch+3st / rate x0.87 の 2 種へ縮約。timing 実測で
規模調整が必要な場合のみ・調整判断は実測前に確定): positive_transform（vocadito）が
tuning 24・holdout 12（計 36）へ半減し、他 kind は不変。

| kind | tuning | holdout | 計 |
|---|---|---|---|
| positive_transform（vocadito・半減） | 24 | 12 | 36 |
| negative_cross（vocadito） | 12 | 6 | 18 |
| positive_transform（synth） | 2 | 2 | 4 |
| negative_rhythm（synth） | 2 | 0 | 2 |
| negative_interval（synth） | 2 | 0 | 2 |
| **計** | **42** | **20** | **62** |

| フェーズ | 1 run あたり起動数 | 回数 | 小計 |
|---|---|---|---|
| tuning phase run | 84 | ×2 | 168 |
| holdout 検証 run（全 62 対） | 124 | ×1 | 124 |
| **総計** | | | **292** |

**観測再利用（キャッシュ）は実装しない**（判断・K3）: ハーネスへ音声ファイル単位の
observation キャッシュを追加すれば起動数は削減できる（例: negative_cross の original
再利用や、同一 clip が複数 positive 対の base として参照される場合）が、本ラウンドでは
**採用しない**。理由: (a) 凍結済みハーネス（第 7/8/9 ラウンドの pins preflight・凍結
コピー照合を含む TOCTOU 対策一式）へキャッシュ層を追加する変更は、正しさへのリスク
（キャッシュキーの取り違え・pin 検証済みバイトとキャッシュ内容の不一致等の新規攻撃面）
が計算節約の利得を上回る。(b) 起動数会計そのものの単純さ（pair × 2 固定・ファイル単位の
重複を考慮しない）自体が検証可能性に資する——「pair 数を数えれば起動数が機械的に
算出できる」という性質を、キャッシュ層は壊す（ヒット/ミスの実行時依存性が入り、
事前登録時点で起動数を確定できなくなる）。full 規模採用の判断自体は維持し（既存の
事前登録パラメータ・変形範囲は不変更）、算術のみを本ラウンドで訂正した。

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
