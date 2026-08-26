# RUN9-L0-HARNESS-1 Provisioning Record

（起草: 2026-08-26、Claude 完結ルート — User 裁定「必要な素材はドライブに
あるのでClaudeで完結できるはずだ。Claudeルートで作成して」（2026-08-26、
`scratchpad/run9_user_adjudication_pin2.md` 末尾）に基づく。Design Memo =
RUN9-L0-HARNESS-1。〔履歴: フェーズ1起草時点（本節起草時）の注記——
「コミット・push は本セッションで実施していない、本記録・
`inputs/dependency_pins_manifest.json`・`RUN9_CONTRACT.yaml`・
`README.md`・`run9_schema.py`・`tests/test_run9_contract.py` の変更は
working tree 上の変更として残す」だった。その後 **PR #326 としてコミット・
push・公開済み**——本節を含む一連の変更は PR #326 のコミット履歴
（第1巡〜第8巡の各巡対応コミット）として repo に確定している。以後の
巡でも本記録・関連ファイルの変更は同様に PR #326 へのコミットとして
反映される〕。

workdir（repo 外、session scratchpad）:
`scratchpad/harness_work/`（`drive/` / `url/` / `tar_gz/` / `diffsinger_repo/`）。
実資産バイナリは一切 repo にコミットしていない。

---

## 1. Provisioning 実行結果（全数）

取得は `voice_genesis/foundry/run8/provision.sh` の方式（`.part` へ staging
→ sha256 一致でのみ正規名へ move、冪等・fail-closed）を踏襲した。期待 sha
は repo 一次ソース（`inputs/backbone_runtime_bundle.json` /
`voice_genesis/foundry/run8/provision.sh`）から本セッションで再確認して
転記した。

### 1-1. Drive 5点（gdown、第一候補で全件成功——MCP フォールバックは不要だった）

| 論理名 | fileId | 取得サイズ | sha256（先頭16桁） | 結果 |
|---|---|---|---|---|
| backbone checkpoint | `1Tm0dxUl_mv6A8-SNO1C72zsdAO8oNHzo` | 556,013,282 B | `6a28d744642df653` | OK |
| config.yaml | `1xeo_m5X3LrcDdPlpsc6sL8kAxjUN_IwQ` | 4,521 B | `3722072045060e31` | OK |
| spk_map.json | `1FaS83o-QJmjwmPRYzKUyp9FxX0_dYS7K` | 47 B | `da9748fabfa721a4` | OK |
| lang_map.json | `1oGfu5qS-Ll0EsgzMCZZWqXCLBamz5wWH` | 9 B | `2a6a227ee65a49f5` | OK |
| dictionary-ja.txt | `1zpxVqbN8SiLqp9qA0WcWfrg0s0C55RhP` | 204 B | `b8ea0d99fcf60e82` | OK |

全5件 `sha256sum` が期待値と完全一致。

### 1-2. URL 2点（curl -L、全件成功）

| 論理名 | URL | 取得サイズ | sha256（先頭16桁） | 結果 |
|---|---|---|---|---|
| NamineRitsu_DiffSinger.zip | canon-voice.com | 421,940,274 B | `5c7b8c328180ea29` | OK |
| nsf_hifigan.oudep | github.com/xunmengshe/OpenUtau | 52,847,838 B | `e22f84009804da2e` | OK |

展開後の内部資産（zip 展開）:

| 論理名 | パス | sha256（先頭16桁） | 結果 |
|---|---|---|---|
| canon linguistic.onnx | `NamineRitsu_DiffSinger/linguistic.onnx` | `1c9ec9f67277a2ba` | OK |
| canon dur.onnx | `NamineRitsu_DiffSinger/dsdur/dur.onnx` | `11bbfad5c489a57e` | OK |
| canon pitch.onnx | `NamineRitsu_DiffSinger/dspitch/pitch.onnx` | `e361ad13053c4b49` | OK |
| canon phonemes.txt | `NamineRitsu_DiffSinger/phonemes.txt` | `1489af3c4806ad2c` | OK |
| vocoder nsf_hifigan.onnx | `nsf_hifigan.onnx`（oudep 展開） | `a3e26672a8c655e3` | OK |

**注記**: canon zip 直下にも `acoustic.onnx` が同梱されているが、これは
NamineRitsu_DiffSinger 配布物自体のacoustic モデル（canon 側）であり、
RUN9 backbone（run6 checkpoint から export した `acoustic.onnx`,
sha `aaaff716…`）とは**別ファイル**。pin 表 §2「acoustic ONNX」はこの
canon 側ファイルではなく run6 export 済みファイルを指す（両者を混同
しない — `backbone_runtime_bundle.json` の記載どおり）。

### 1-3. DiffSinger repo clone + checkout（成功）

```
git clone https://github.com/openvpi/DiffSinger.git
git checkout e2307b1
git rev-parse HEAD
  -> e2307b1080b00f3999702ce9017cfd75c7f862fe
```

pin（`run9_render_code_commit.commit_full`）と厳密一致。

### 1-4. r6_gate_materials_2026-08-20.tar.gz（Drive fileId
`1D3R51BeseDYvFvk3voDg9oaSYvW-kxoA`）— **acoustic export companions は MISS**

- 取得: gdown で成功。サイズ 26,206,427 B（Drive メタデータの事前確認値と
  完全一致）。tar.gz 自身の sha256 =
  `bc6c6574582168e589c3e52784ae60bf2315af63777a08c9c39916778d1096cd`
- 展開: 39ファイル全数を検査し、相対パス・サイズ・sha256 を
  `inputs/dependency_pins_manifest.json#tar_gz_full_member_ledger` へ
  台帳化した（内容: `deliver/`・`deliver_abc/`・`deliver_abc_concat/` の
  wav 出力群、`out_{ritsu,pjs,user,d3synth}/step_40000/
  gate_synth_summary.json`、ラウドネス正規化・ABC 試聴セット生成スクリプト、
  synth ログ）
- **acoustic export companions（acoustic.onnx / dsconfig.yaml /
  s5_run6_acoustic_v1.phonemes.json / s5_run6_acoustic_v1.*.emb）は
  39ファイルのいずれにも該当しなかった** — インベントリ調査（§2-3）が
  「有力候補だが未実証」としていた仮説は**反証**された
- **間接的 provenance**: `out_ritsu/step_40000/gate_synth_summary.json`
  の `input_sha256` 節が、acoustic export companions 4点の sha256 を
  gate_synth.py 実行時の single-read hash として記録しており、repo 側
  pin（`backbone_runtime_bundle.json`）と**全数一致**することを確認した
  （canon 4 資産・vocoder onnx・DiffSinger commit も同時に一致）。これは
  ファイル実体の入手ではなく実行ログの記録であり、pin の代替にはしない。
  `out_pjs`/`out_user`/`out_d3synth` の summary も同じ acoustic 資産群を
  参照しており、speaker_embed のみが話者ごとに異なる値を持つ:

  | 話者 | speaker_embed sha256 |
  |---|---|
  | ritsu（pin あり） | `ce4b87b99ac8aa7de7857feba6ca163d4ccf76a27f8fce2ac51740c2bb7b3e4c`（pin と一致） |
  | pjs（pin なし・候補） | `074e09b390c207a7cf98105db549e1006d035a797d57f73e103e848bb3216015` |
  | user（pin なし・候補） | `588913b74d6c16e01f4f33223698cd165ac686012e7d878475a3799ccee8bde0` |
  | d3synth（RUN9 対象外、参考） | `10c3964c57a69edb072bd7c9aec36dc7e3b06e06469c5da60332bec793c1dc22` |

- **run_execution_manifest.json 探索**（run6 Drive フォルダ、fileId
  `1J6LI1SlIGrkfb7FvUeLDs6S_y3hoGRs6`）: `checkpoint_hashes` /
  `dataset_hashes` / `material_hashes` / `tensorboard_hashes` を検査した
  が、acoustic.onnx や speaker embedding の hash 記録は**見つからなかった**
  （この manifest は run6 の学習実行記録であり、後日の acoustic export
  成果物は範囲外）
- **fail-closed 判断**: Design Memo Risk 節の規定どおり、事実を記録して
  停止した。再export（DiffSinger `scripts/export.py` は torch/lightning/
  onnx/onnxsim を要し、本 Memo の Allowed Dependencies
  〔onnxruntime==1.29.0 のみ〕の範囲外）・代替調達のいずれにも進んでいない

---

## 2. Python 依存導入

導入前 baseline（`voice_genesis/evolution/run9_dual_founder_pjs/tests/` +
`tests/discipline/`）: **1704 passed, 1 failed**（1件は
`test_pin2r2_fix2_adjudication_source_body_byte_identical_to_scratchpad_origin`
— 本 harness の作業と無関係な環境ドリフト。§4 参照）。

```
pip install onnxruntime==1.29.0
```

導入後、同スイート再実行: **1704 passed, 1 failed**（同一の1件、非退行を
確認）。

RENDER_STACK_PIN + ANALYSIS_STACK_PIN 全9パッケージの実測バージョンが pin
と完全一致（機械照合、`inputs/dependency_pins_manifest.json
#python_dependency_pins`）:

| package | pin | observed |
|---|---|---|
| python | 3.11.15 | 3.11.15 |
| numpy | 2.4.6 | 2.4.6 |
| librosa | 0.11.0 | 0.11.0 |
| numba | 0.66.0 | 0.66.0 |
| scipy | 1.17.1 | 1.17.1 |
| soundfile | 0.14.0 | 0.14.0 |
| PyYAML | 6.0.1 | 6.0.1 |
| pyloudnorm | 0.2.0 | 0.2.0 |
| onnxruntime | 1.29.0 | 1.29.0 |

---

## 3. 決定論 smoke render — **BLOCKED**

`gate_synth.py --speaker ritsu` の render 経路（`--acoustic-dir`）は
acoustic.onnx（+ dsconfig.yaml / phonemes.json / speaker_embed のいずれか
一致する形式）を必須とする。§1-4 の tar.gz MISS により、この4点は取得
できなかった。

新規 export（`gate_synth.py run_export_acoustic()` は `sys.executable
scripts/export.py acoustic` を同一インタプリタで subprocess 実行するため、
torch/lightning/onnx/onnxsim + numpy<2 の隔離 venv〔provision.sh §6〕相当
が必要）は、本 Memo の Allowed Dependencies（`onnxruntime==1.29.0` のみ）
の範囲外であり技術的にも実施しなかった。

**結論**: 決定論確認（同一入力2回の WAV byte 一致）・CPU render 実測秒の
いずれも本 Memo では取得できなかった。数値を捏造しない
（`inputs/dependency_pins_manifest.json#smoke_render.status ==
"BLOCKED"`）。gate_synth.py・score.py 等は一切改変していない（未実行の
まま）。

---

## 4. 予算見積り — **BLOCKED**

smoke render が BLOCKED のため、render 1件あたりの実測秒が存在しない。
実測なしに「現実的/非現実的」を判定すると数字を捏造することになるため
判定を行わない。

参考値（pin/見積りの根拠にはしない）: `voice_genesis/foundry/results_s7/
probe_0b_groups/run6_ritsu.json` の `render_elapsed_sec` = 3.7〜7.6秒/件。
この記録は実行プロバイダ（CPU/GPU）を明記しておらず、当時の実行環境
（RunPod GPU pod, NVIDIA GeForce RTX 3090、`run_execution_manifest.json`
§2-2 参照）から GPU 実行だった可能性が高い——本 CPU-only 環境の実測値
としては使えない。

RUN9 総 render 数（設計上の見積り根拠、実測が得られ次第この件数へ実測秒を
乗じる）:
- learning/search loop: 128 logical_render_units × 2 founders（R9F-01/
  R9F-02）× 2 arms（PRACTICE_FROM_AUDIO / TRANSFER_TECHNIQUE） = **512**
  （`USER_ADJUDICATION_20260825_PIN2_LEARNING_BUDGET.txt` §2、
  render_budget=128/Founder/arm、trial_count=32・cache hit も1 unit 計上）
- birth probe: `evaluation/probe_manifest.json` の `probes` 配列を実測
  カウントした（P0=1・P1=8・P2=9・P3=4・P4=1・P5=1 cell、`json.load()` で
  `len(cells)` を probe ごとに集計） = **24 cell**（Founder 単位のスコープ
  は probe manifest 側の記載を要確認だが、本記録では素朴に「1 cell =
  1 render」として計上する——Founder × 2 が必要かは probe_manifest.json
  の `render_contract` を精読する後続作業で確定する）
- C0/C1: `RUN9_CONTRACT.yaml interventions.c0_replay_takes_per_founder` /
  `c1_sham_takes_per_founder` = 各20 × 2 founders × 2（C0+C1） = **80**
- 固定評価（birth baseline/validation/sealed holdout）: 未確定（HARNESS-2
  以降の設計対象）

learning/search loop（512）+ birth probe（24）+ C0/C1（80） = **616 件**
（固定評価分を除く概算）。実測秒が得られ次第、`execution_profile_sha` の
PINNED 化判断に用いる。

---

## 5. dependency_pins_manifest.json 実装

`inputs/dependency_pins_manifest.json`（schema `run9-dependency-pins/1.0`）
を新設し、`RUN9_CONTRACT.yaml dependency_pins_sha` を実バイト sha256
`3392656474b4538e9ed05bfda0d57bc7845bcca3cfa20ffaacdaa94b5fb695e1`（第1世代）
で PINNED 化した。`run9_schema.py` に
`validate_dependency_pins_manifest()` / `DEPENDENCY_PINS_MANIFEST_REQUIRED_KEYS`
/ `load_pinned_dependency_pins_manifest()`（probe/seed_policy と同型の
4段構成 + `backbone_runtime_bundle.json` との cross-check）を追加した。
`execution_profile_sha` は §3/§4 の理由により PENDING を維持する。

`gate_state()` は依然 `BLOCKED`（pre-run PENDING 9欄）。

### 5-1. PR #326 第1巡 Codex bot レビュー対応（2026-08-26、repin 第2世代）

Codex bot 指摘2件（P2×2、いずれも採用——将来汚染: 将来の repin が証拠なしで
成功状態を主張できる validator の穴）を受け、validator を status 判別型
shape へ強化した（実測結果自体は無変更、`generation_note` へ対応記録を
追記したため manifest バイトが変わり repin）。

- **Fix 1**（`acoustic_export_companions`）: `expected_items` は status が
  `OBTAINED_VERIFIED_MATCH` のときのみ `measured_sha256`（実測 digest）を
  必須化し、`expected_sha256` との厳密一致を強制する。
  `NOT_OBTAINED_TARBALL_MISS` のときは `measured_sha256` を禁止する
  （unknown key で拒否）。旧実装は status 文字列を書き換えるだけで
  validator を通過できた。loader 側も `measured_sha256` と bundle pin 値
  の直接照合（三者一致の第3辺）を追加した。
- **Fix 2**（`smoke_render`/`budget_estimate`）: BLOCKED/COMPLETED を
  disjoint な必須キー集合へ分離した。COMPLETED は blocked 専用フィールド
  （`blocked_by` 等）を禁止し、実測フィールド（smoke:
  `determinism_confirmed`=True・`measured_sec_per_render`（正の有限数値）・
  `render_condition`／budget: `total_render_count`（正の整数）・
  `estimated_total_sec`（正の有限数値））を必須化した。

新 sha256（第2世代）: `1d8a8f720bfd5e4999748cd766560ad33fff2d977852e2fda7b7596124539be2`。
本 harness の実測（tar.gz MISS・smoke render BLOCKED・budget estimate
BLOCKED）自体は無変更——両セクションとも引き続き BLOCKED shape で妥当。

### 5-2. PR #326 第2巡 Codex bot レビュー対応（2026-08-26、P1×1・P2×3、
全4件採用）

**Fix 3（P1、`RUN9_CONTRACT.yaml` 側）——`dependency_pins_sha` を PENDING
へ差し戻し**: 指摘は正当だった。第1-2世代の PINNED 化は過大——本 manifest
は render/analysis 層9パッケージ（`python_dependency_pins`）のみを検証
対象としており、これで `dependency_pins_sha` を PINNED にすると、VG-L0
学習ハーネス本体（optimizer/探索コード）の import closure が未確定の
ままでも `gate_state()` が「全実行依存が確定した」証拠として誤って扱って
しまう（`measurement_spec_sha` が RUN9-L0-PIN-1 で同型の理由により
PENDING へ復帰した前例と同型）。対応: `value: null` / `status: PENDING`
へ戻し、reason を正直に更新した（「render/analysis 層 + 実体資産は
実測検証済み。ただし学習ハーネス本体の import closure が未確定。pin は
ハーネス実装時に closure を実測してから」）。manifest 実体・validator・
loader は撤去せず残置（PENDING の間 loader が「not PINNED」で raise する
のが正しい挙動——`measurement_spec_sha` と同型のテストへ追随した）。
manifest 自身の `claim_scope` に「本 manifest は render/analysis 層の
実測記録であり `dependency_pins_sha` の完全な充足を主張しない」ことを
明記した。残 pre-run PENDING は9→10へ戻った（正直な会計、README 追随）。

**Fix 4（P2）——tar member 検査の status 連動化**:
`_validate_tar_gz_full_member_ledger()` が companion section の status を
一切参照せず常時 raise していたため、将来 tarball が repin されて
companions を正当に含み status が `OBTAINED_VERIFIED_MATCH` へ正しく
更新された場合でも、この関数だけは構造的に必ず raise し続け、エラー
メッセージ自身が要求する遷移が不可能だった。対応: `companion_status`/
`companion_items` を引数として受け取るよう拡張し、
`NOT_OBTAINED_TARBALL_MISS` のときのみ従来どおり「companion basename が
見つかれば矛盾」を拒否、`OBTAINED_VERIFIED_MATCH` のときは逆に「対応する
tar member が見つからない・sha256 が不一致なら矛盾」という整合検査へ
切り替えた。

**Fix 5（P2）——budget↔smoke の結合強制**: `budget_estimate` が
`COMPLETED` を名乗るには `smoke_render` も `COMPLETED` でなければ
ならないという前提が machine check されておらず、smoke が `BLOCKED` の
まま budget だけ独立に `COMPLETED` へ repin できてしまっていた。対応:
budget の COMPLETED 分岐で smoke_render.status == COMPLETED を要求し、
`estimated_total_sec` が `measured_sec_per_render × total_render_count`
と `math.isclose`（`rel_tol=1e-9`、意図的に厳しめ——概算・切り捨てを
通さず計算ロジック誤りを検出する）で一致することを検証するようにした。

**Fix 6（P2）——companions の重複 logical_name 拒否**:
`acoustic_export_companions.expected_items` の集合等価判定
（`set(seen_names) == set(expected)`）は重複を潰すため、4種の正しい
logical_name + 1件の重複（計5件）が通過してしまっていた
（`render_asset_ledger` には既にあった `len(list)==len(unique)` の
事前チェックが本節には無かった）。対応: 同型の重複チェックを集合等価
チェックより先に追加した。

manifest バイトはさらに変わった（claim_scope への追記 + Fix 3-6 対応
記録を generation_note へ追記、pin 欄自体は PENDING のため repin ではなく
情報記録として最新実測 sha256 を `RUN9_CONTRACT.yaml` の履歴コメントへ
残した）: `fe8e47b8cb035e8e3795c8bbf5305161985b630f9ae7659a709d6dd5092e0cf5`。
本 harness の実測（tar.gz MISS・smoke render BLOCKED・budget estimate
BLOCKED）自体は無変更。

既存 pin（backbone_checkpoint_sha 等）は無変更を確認済み——本ラウンドで
状態が変わった pin 欄は `dependency_pins_sha`（PINNED→PENDING）のみ。

### 5-3. PR #326 第3巡 Codex bot レビュー対応（2026-08-26、P2×3、全3件
採用——将来汚染/実行不能遷移の同系整合）

**Fix 7（P2）——取得元別の tar membership 要求**: 第2巡 Fix 4 は tar
member 検査を companion status に条件付けたが、OBTAINED 分岐は常に
「この tarball 内に member が実在する」ことを要求しており、本記録 §7 が
記す2つの解除経路（別 Drive フォルダの探索・再export の User 裁定）——
いずれも `r6_gate_materials_2026-08-20.tar.gz` 由来ではない——を構造的に
拒否していた。対応: `acoustic_export_companions.expected_items` の
OBTAINED item へ `acquisition_source`（閉じた語彙 `THIS_TARBALL`/
`DRIVE_DIRECT`/`RE_EXPORT`）を必須化し、tar membership + sha 整合の
要求は `acquisition_source == "THIS_TARBALL"` のときのみに限定した。
`DRIVE_DIRECT`/`RE_EXPORT` は Fix 1 の `measured_sha256 ==
expected_sha256` 強制だけで足りる。

**Fix 8（P2）——smoke COMPLETED ← companions OBTAINED の結合**: Fix 5
（budget↔smoke）と同型の欠陥——`smoke_render` が `COMPLETED` を名乗るのに
`acoustic_export_companions.status == NOT_OBTAINED_TARBALL_MISS` のまま
でも受理されてしまっていた（「存在しないと同時に主張している入力で
render した」自己矛盾）。対応: `_validate_smoke_render_section()` の
COMPLETED 分岐で `companions_status == OBTAINED_VERIFIED_MATCH` を前提
条件として要求するようにした。

**Fix 9（P2）——speaker candidate status の厳密語彙化**:
`speaker_embeddings_unpinned_candidates` の status 判定が
`startswith("UNPINNED_CANDIDATE")` という接頭辞判定のままで、
`UNPINNED_CANDIDATE_PINNED_VERIFIED` のような typo/矛盾混成値を通過
させてしまっていた（`_SPEAKER_EMBED_CANDIDATE_STATUS_VOCAB` が既に
意図する厳密値を定義していたのに実装が使っていなかった）。対応:
entry ごとの厳密な許容集合との完全一致へ変更した（pjs/user は
"UNPINNED_CANDIDATE" のみ、d3synth は
"UNPINNED_CANDIDATE_NOT_A_RUN9_FOUNDER" のみ）。

pin 欄（`dependency_pins_sha`）自体は PENDING のまま——manifest バイトは
generation_note への追記で変わったため、`RUN9_CONTRACT.yaml` の履歴
コメントの情報記録 sha256 を更新した（repin ではない）:
`cb8307da0a2b14c189a58be620003a1c7a89ad6c8e1e4d0997b51ac3a8953ede`。
本 harness の実測結果自体は無変更。既存 pin は無変更を確認済み。

### 5-4. PR #326 第4巡 Codex bot レビュー対応（2026-08-26、P2×2、全2件
採用）

**Fix 10（P2）——tar member ledger の束縛強化**: 指摘の核心は正当——
`tar_gz_full_member_ledger` は「非空の well-formed 行の任意部分集合」
であっても validate を通過してしまい、列挙漏れ（例: acoustic.onnx の
見落とし）があっても `NOT_OBTAINED_TARBALL_MISS` が成立し得た。ただし
tarball 実体（25MB）は repo 外（session scratchpad）にあり、load 時の
再読による完全性検証は CI では構造的に不可能（PIN-2 Fix 8 の corpus
束縛と同型の境界）。対応は3段:

1. 新設 `tar_gz_ledger_integrity` 節に `member_count`/`total_size_bytes`
   （ledger 実体との内部整合を machine 強制——`len(ledger) ==
   member_count`・`sum(size_bytes) == total_size_bytes`）+
   `generation_method`（単一 tarfile read で ledger を構築した宣言）を
   必須化した。
2. **workdir に tarball が現存する今のうちに、実 tar から ledger を
   独立再生成し、現行 manifest の39行と全一致することを実測検証した**
   （下記）。
3. `validate_dependency_pins_manifest()` docstring に信頼根境界を明文化
   した: load 時の tar 再読束縛は archive が repo 外である限り不可能。
   担保は (i) build 時の単一 read 生成 (ii) 本巡の独立再生成一致実測
   (iii) 将来 repin 時は再 provisioning（tar sha 照合 + ledger 再生成）
   が正規経路であり、手編集 ledger は信頼根境界外、の3層。再入条件
   （tarball が将来 repo 内 pin として収載されたら load-time 完全束縛へ
   昇格）も記録した。

**独立再生成の実測結果**（fail-closed 原則どおり、事実をそのまま記録）:

```
tarball: scratchpad/harness_work/tar_gz/r6_gate_materials_2026-08-20.tar.gz
tarball sha256（再確認）: bc6c6574582168e589c3e52784ae60bf2315af63777a08c9c39916778d1096cd
  （attempted_source.actual_sha256 と一致、同一ファイルが provisioning
  時から変化していないことを確認済み）
独立再生成方法: tarfile.open(path, 'r:gz').getmembers() を単一 read し、
  member.isfile() のみを対象に extractfile() で読んだバイト列から
  sha256 を都度計算、path でソート
再生成件数: 39 ファイル
現行 manifest の tar_gz_full_member_ledger（39行）との突き合わせ:
  path・size_bytes・sha256 の3フィールドすべてが39行全数で完全一致
  （不一致 0 件）
結論: EXACT_MATCH — 列挙漏れ（例: acoustic.onnx の見落とし）は
  現行 manifest には存在しない。指摘が懸念したシナリオは、少なくとも
  現世代の ledger には該当しないことを直接実証した。
```

**Fix 11（P2）——record のテスト数の更新**: `HARNESS1_PROVISION_RECORD.md`
の §6 が旧い「1742 passed, 1 failed」のまま最終状態として読めてしまう
矛盾を是正した。§6 を「歴史値（各巡コミット時点のスナップショット、
不変）+ 最新値（直近コミット時点、巡ごとに更新）」の二層表記へ改め、
本巡時点の最新値を追記した。§7 item 4 の逸脱記録（1 failed = scratchpad
drift）も「その後解消済み（原因 = 起草側 scratchpad への追記、repo 側の
欠陥ではなく、その後の分離作業で自然に復旧）」へ追随させた。今後の巡
でも §6 は歴史値+最新値の二層で保つ規約を §6 冒頭に明記した。

pin 欄（`dependency_pins_sha`）自体は引き続き PENDING——manifest バイトは
`tar_gz_ledger_integrity` 節の新設で変わったため、`RUN9_CONTRACT.yaml`
の履歴コメントの情報記録 sha256 を更新した（repin ではない）:
`e8120a77a9b49ad5aa11ab7f9a92d8ee7eea33a1accb7cdbe533a94587271404`。
本 harness の実測結果自体は無変更（tar.gz 全数展開結果・列挙漏れなしの
実証は今回新たに追加された事実であり、既存の「acoustic export
companions 未取得」という結論は変わらない）。既存 pin は無変更を確認
済み。

### 5-5. PR #326 第5巡 Codex bot レビュー対応（2026-08-26、P2×2、全2件
採用）

**Fix 12（P2）——MISS 矛盾判定を digest 一致に限定**: 指摘は正当——
`NOT_OBTAINED_TARBALL_MISS` の矛盾検出が basename 一致だけで発火して
いたため、将来の tarball に同名だが別バイトの無関係ファイル（例: 別
由来の `dsconfig.yaml`）が混入すると、正直な MISS 記録が偽ブロック
されうる欠陥だった。対応: 矛盾判定を「basename 一致 かつ sha256 ==
expected_sha256」の両立時のみに限定した——各 companion item は既に
`expected_sha256` を保持しているため、identity（basename）と
digest（sha256）の両方が一致して初めて「この companion が実は tarball
内に存在した」証拠になる。basename のみ一致し digest が異なる member
は record 上、追加の注記を要しない単なる無関係ファイルとして扱う
（`tar_gz_full_member_ledger` 自体がその member 自身の sha256 を既に
記録しているため）——この設計判断を `validate_dependency_pins_
manifest()` docstring に明記した。

**Fix 13（P2）——claim_scope の PINNED 残存文言の是正**: 指摘は正当——
PR #326 第2巡 Fix 3 で `dependency_pins_sha` が PENDING へ差し戻された
後も、`claim_scope.statement` は「本 manifest が...PINNED 判定を通じて
主張するのは...」という PINNED 前提の書き出しのまま残り、訂正は文末
への追記に留まっていた。対応: statement を「`dependency_pins_sha` は
現在 PENDING である」ことを主表明として書き出す文へ全面改訂し、旧
PINNED 世代（第1-2世代）への言及を新設フィールド
`claim_scope.historical_pinned_generations`（明示的な historical 節、
statement/rationale とは別フィールド）へ分離した。新設
`_validate_claim_scope()` が PENDING 主表明マーカー（`"は現在 PENDING
である"`）が statement の先頭80文字以内に存在することを機械強制する
——文末への追記だけでは通らない。

pin 欄（`dependency_pins_sha`）自体は引き続き PENDING——manifest バイトは
`claim_scope` の全面改訂で変わったため、`RUN9_CONTRACT.yaml` の履歴
コメントの情報記録 sha256 を更新した（repin ではない）:
`06426625792af6649f7b479110cb0c89f7f25205d13664d79398f43e1eea883d`。
本 harness の実測結果自体は無変更。既存 pin は無変更を確認済み。

---

### 5-6. PR #326 第6巡 Codex bot レビュー対応（2026-08-26、P2×2、全2件
採用）

**Fix 14（P2）——companions トップレベルの status 判別 shape 化**: 指摘は
正当——item レベルの `expected_items` は Fix 1/7 で
status（OBTAINED/NOT_OBTAINED）判別 shape 化済みだったが、
`acoustic_export_companions` セクションのトップレベル narrative
フィールド（`verdict`/`fail_closed_disposition`/`acquisition_record`）は
自由記述のまま残っており、`status: OBTAINED_VERIFIED_MATCH` でも
`verdict: "MISS..."` や未取得を示す `fail_closed_disposition` が
残置可能だった（逆方向の混成——`NOT_OBTAINED` なのに取得証跡が残る
——も同様に可能）。対応: トップレベルを status 判別 shape へ改めた——
`NOT_OBTAINED_TARBALL_MISS` 側は `verdict`（`MISS` 始まり必須、非空）+
`fail_closed_disposition`（非空）を必須化・`acquisition_record` を禁止、
OBTAINED 側は `acquisition_record`（`acquired_at`/`acquisition_summary`
の2キーのみ、共に非空文字列）を必須化・MISS 系2フィールドを禁止する
相互排他 shape とした。`_validate_acoustic_export_companions()` が
status を先に読み、対応する必須キー集合との unknown-key/missing-key
両方向を機械強制する。テストヘルパー `_obtain_all_acoustic_companions()`
をトップレベルも整合させる形へ更新し、新設ヘルパー
`_mark_companions_top_level_obtained()` を追加した。負例（status だけ
OBTAINED で MISS narrative 残置、逆方向混成）拒否・正例（整合 shape）
受理のテストを追加した。

**Fix 15（P2）——smoke 決定論の出力 hash 証拠必須化**: 指摘は正当——
`smoke_render` の COMPLETED は `determinism_confirmed: true` + 実測秒 +
条件文の自己申告だけで成立しており、record が定義する「同一入力
2 render の WAV byte 一致」を裏付ける監査可能な証拠（出力の sha256 等）
が要求されていなかった——`determinism_confirmed` は語れるが検証不能な
主張のままだった。対応: COMPLETED shape に
`render_output_sha256_first`/`render_output_sha256_second`（64hex）を
必須化し、validator が両者の厳密一致を機械強制する（不一致は
`determinism_confirmed: true` との自己矛盾として拒否）。テストヘルパー
`_complete_smoke_render()` を追随させ、負例（hash 欠落・不一致）拒否
テストを追加した。

両 Fix とも、既存の7テストが新 shape 前提とずれて failing になったため
（`_obtain_all_acoustic_companions()`/`_complete_smoke_render()` を直接
使わず個別に fixture を組み立てていたテスト）、個別に shape 追随
させた上で、新規10テストを追加した（targeted subset:
139 passed, 1219 deselected）。

pin 欄（`dependency_pins_sha`）自体は引き続き PENDING——manifest バイトは
`generation_note` への Fix 14/15 追記で変わったため、`RUN9_CONTRACT.yaml`
の履歴コメントの情報記録 sha256 を更新した（repin ではない）:
`229493a911b6def9ca47523cfb0345d6066d826ec67e7ead999b098ea6dbc269`。
本 harness の実測結果自体は無変更。既存 pin は無変更を確認済み。

---

### 5-7. PR #326 第7巡 Codex bot レビュー対応（2026-08-26、P2×1、採用）

**Fix 16（P2）——speaker-candidate 全必須フィールド検証の強化**: 指摘は
正当——`_validate_speaker_embed_candidate()` は required_keys の
存在（unknown/missing key）チェックのみで、値そのものの整合は
`candidate_sha256`（64hex 形式）と `source`（非空）と `status`（閉じた
語彙、Fix 9）しか検証していなかった。`candidate_sha256_first16`
（pjs/user のみ）が `candidate_sha256` の先頭16文字と実際に一致するかの
機械照合、`file`（pjs/user）/`note`（d3synth、section 全体の `note` とは
別フィールド）の非空検証が漏れており、矛盾した短縮 digest や空文字列でも
通過しうる状態だった——User 裁定の判断材料となる候補記録の整合検証漏れ
（将来汚染）。対応: `required_keys` に `candidate_sha256_first16` が
含まれる entry（pjs/user）では `candidate_sha256_first16 ==
candidate_sha256[:16]` を機械照合し、不一致を拒否するとともに `file` の
非空文字列検証を追加した。`required_keys` に `note` が含まれる entry
（d3synth）では `note` の非空文字列検証を追加した。entry ごとの許容キー
集合を閉じる unknown-key 拒否は既存実装で対応済みであることを確認した
（変更不要）。正負テストを追加した: first16 矛盾（pjs/user 各）の拒否、
file 空（pjs/user 各）の拒否、d3synth note 空の拒否、未知キー（pjs/user/
d3synth 各）の拒否、現行実データが新検証を通過することの確認（過剰拒否
でないこと）。既存の `test_harness1_speaker_embed_candidates_pjs_user_
identical_rejected` は user の `candidate_sha256` だけを書き換えて
`candidate_sha256_first16` を揃えていなかったため、Fix 16 の first16
矛盾検出が pjs==user 一致検出より先に発火するようになった——本テストの
意図（pjs==user 一致検出）を保つよう `candidate_sha256_first16` も
揃える形へ追随させた。

【付随】manifest 実データ（`inputs/dependency_pins_manifest.json`）を
新検証で再検証した結果、pjs/user の `candidate_sha256_first16` は
`candidate_sha256` の先頭16文字と実際に一致しており、`file`/`note` も
非空だった——**manifest 側に矛盾はなく、修正・情報 sha256 更新は不要**
だった（sha256 は前巡から無変更: `229493a911b6def9ca47523cfb0345d6066
d826ec67e7ead999b098ea6dbc269`）。`dependency_pins_sha` は引き続き
PENDING。既存 pin は無変更を確認済み。

---

## 6. テスト・lint

**規約（PR #326 第4巡 Codex bot レビュー Fix 11、P2、採用、2026-08-26 制定
— 以後の巡でも本節はこの二層で保つ）**: 本節は「歴史値」（各巡コミット
時点の実測値、その巡の Verification として当時記録したもの・以後不変）と
「最新値」（直近コミット時点の実測値、巡が進むたびに追記・更新）の二層
表記とする。歴史値だけを読んで最終状態と誤認しないよう、最新値を必ず
本節末尾に明記する。

- `ruff check .` — 全巡で pass
- `python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests/ tests/discipline/ -q`
  歴史値（各巡コミット時点の Verification 実測、後続巡での改善を反映
  しない当時のスナップショット）:
  - HARNESS-1 初回実装時点: 1742 passed, 1 failed（1件 = `test_pin2r2_
    fix2_adjudication_source_body_byte_identical_to_scratchpad_origin`、
    §7 item 4 参照）
  - PR #326 第1巡（Fix 1/2）: 1760 passed, 0 failed
  - PR #326 第2巡（Fix 3-6）: 1769 passed, 0 failed
  - PR #326 第3巡（Fix 7-9）: 1780 passed, 0 failed
  - PR #326 第4巡（Fix 10/11）: 1787 passed, 0 failed
  - PR #326 第5巡（Fix 12/13）: 1798 passed, 0 failed
  - PR #326 第6巡（Fix 14/15）: 1809 passed, 0 failed
  - **最新値（PR #326 第7巡, Fix 16 対応時点）: 1818 passed, 0 failed**
    （`scratchpad/h1_r8_pytest.txt` に生出力を保存。上記1failedは第1巡
    コミット時点で既に解消済み——原因だった scratchpad 原本と repo 収載
    版の乖離が、その後の作業で分離・復旧された。§7 item 4 追随参照）

---

## 7. 逸脱・ブロッカー

1. **acoustic export companions 未取得**（§1-4）— 最大の未解決ブロッカー。
   HARNESS-2 以降で再調達方法（別 Drive フォルダの探索、または export 再
   実行の User 裁定）を要する。
2. **smoke render / budget estimate が BLOCKED**（§3/§4）— (1) に従属。
3. **pjs/user speaker embedding が未 pin**（§1-4）— 候補 sha256 は記録
   済みだが pin 化しておらず、User 裁定を要する（af0 embedding の写像
   方式設計とあわせて後続 Memo の対象）。
4. **pre-existing 環境ドリフト（本 harness の作業と無関係）— その後解消
   済み（PR #326 第4巡 Fix 11 で追随、2026-08-26）**:
   `test_pin2r2_fix2_adjudication_source_body_byte_identical_to_scratchpad_origin`
   が本セッション開始時点（onnxruntime 導入前）から fail していた
   （§6 歴史値「HARNESS-1 初回実装時点」参照）。原因は
   `scratchpad/run9_user_adjudication_pin2.md` が本 Memo の準備として
   「追加 User 裁定（2026-08-26、HARNESS 前提）」節を追記されたことで、
   PIN-2 実装時に repo へ収載した
   `USER_ADJUDICATION_20260825_PIN2_LEARNING_BUDGET.txt` の内容と
   scratchpad 原本が乖離したためだった。起草側 scratchpad への追記が
   原因であり repo 側の欠陥ではなかったため、その後の作業（scratchpad
   と repo 収載版の分離）で自然に復旧し、PR #326 第1巡コミット時点
   （§6 歴史値参照）以降は fail していない——本 harness の変更で意図的
   に修正した箇所はない（本 Memo の Scope OUT〔既存 inputs JSON・
   PIN-2 領域〕のため）。
