# VG-DEBT-008 run4 anchor WAV producer provenance — 実測実行報告（2026-08-23）

- 実行環境: このセッション（repo は run4_provenance_closure_2026-08-23.json /
  test_run4_provenance_closure.py / debt_ledger.yaml の追記のみ変更。既存の確定
  記録 `run4_anchor_provenance.json` / `run4_finite_report_2026-08-22.json` /
  `d4_results_2026-08-22.json` は無改変）
- repo: `/home/user/ugh-prompt-engine`, branch `claude/technical-debt-plan-2fv79j`,
  head（開始時点） `df38507d`
- 作業ディレクトリ: `/home/user/run4_provwork`（新規）+ `/home/user/d4work`
  （D4 実測 = VG-DEBT-004 の既存資産を再利用。venv_export・DiffSinger checkout
  e2307b1・provision.sh 取得済み資材）
- セッション冒頭で `git fetch --unshallow` を実行済み（gate_synth_run4.py の
  pin コミット同定に完全な commit history が必要だったため。作業ツリー・
  index は変更していない読み取り専用操作）

## Phase 0: 一次ソース調査

- `s3_record_2026-08-17.md` §1/§7.4-3 と `run4_anchor_provenance.json` を精読。
  10 件の欠落構造を確認（依頼文の 1-10 番と完全一致）。
- `gate_synth_run4.py` の git 全履歴（unshallow 後）を洗い、run4 の生成活動
  時間帯（学習開始 12:22・40K 完走 16:40・run3 アンカー再合成 19:45、いずれも
  UTC）の直前最終変更コミット `cda36b9f`（2026-08-17T07:55:30Z）と、直後の
  次コミット `07ecc620`（2026-08-17T22:55:58Z、s3_record 未転記項目の転記
  コミットで生成活動後）を特定。両者の間に本ファイルへの他の変更コミットは
  無い。**これは状況証拠であり証明ではない**（Pod 上の未コミット編集を排除
  できない）ため、`generation_script` 項目は `measured_only` に留めた。
  委譲先の `gate_synth.py`（本ファイルが read-only import する被呼び出し側）
  も同時間帯（`dec01733` 以降 08-17 中）無変更であることを確認した。
- Google Drive で run4未転記フォルダ `1mMahUQXDy_TZzYIXlkNRM-9T2bBjFeqw` を
  `parentId` 検索。想定どおり `config.yaml` / `spk_map.json` / `lang_map.json`
  / `dictionary-ja.txt` に加え、**フルテキストの `gate40k.log`（59725 bytes）
  と `gate_run3_anchor_v2.log`（2955 bytes）** を発見・取得できた
  （`run4_untranscribed_audit_2026-08-21.md` という先行監査メモも同フォルダに
  あり、本実測と独立に「acoustic ONNX の sha256 はログに記録されていない」
  ことを裏付けていた）。
  - `config.yaml` の sha256 は既存 pin（`run4_config_yaml`）と完全一致。
  - `dictionary-ja.txt` の sha256 も既存 pin（`run4_dictionary_ja_txt`）と
    完全一致。
  - これらは独立に取得した実体が既存記録と一致するクロスチェックであり、
    本実測の入力材料としての信頼度を上げる材料になった。
  - `gate40k.log` に acoustic ONNX の sha256 記録は無いことを実際に
    grep して確認（既存記録の指摘どおり）。DiffSinger の git revision も
    記録されていない。
- run3 checkpoint / `onnx_export41` は Drive のタイトル検索（`run3` 全般 /
  `onnx_export41` / `s3_run3` / `run3_acoustic` / `export41`）で**発見できず**。
  run3/run4 受け渡しフォルダ `1_Hbi46PQ-fi1wxc6fZWglBjzTecfr0z_` も全件確認
  したが、汎用音源ファイルが大半で voice model 関連は run3/run4 アンカー
  tar.gz とログのみだった。

## Phase 1: 低リスク3件

- **canon**: provision.sh の URL から `NamineRitsu_DiffSinger.zip`
  （421,940,274 bytes）を取得 → sha256 が provision.sh の pin
  `5c7b8c32...` と完全一致。展開後、`gate_synth.py` が実際に読む 5 ファイル
  （linguistic.onnx / dsdur/dur.onnx / dspitch/pitch.onnx / phonemes.txt /
  dsconfig.yaml）の sha256 を個別記録。zip 同梱の `acoustic.onnx`
  （canon 側の自前 acoustic）はこのレシピでは未消費と判断（コード読解で
  確認）— 記録上は「参考値」として残した。
- **vocoder**: `nsf_hifigan.oudep`（52,847,838 bytes）を取得 → sha256 が
  provision.sh の pin `e22f8400...` と完全一致。展開後の
  `nsf_hifigan.onnx` の sha256（`a3e26672...`）も provision.sh の
  `VOC_WANT` と完全一致（これは --vocoder-dir から実際に読まれる唯一の
  ファイルであり曖昧性なし）。
- **gate_synth_run4.py**: Phase 0 で同定した pin コミット `cda36b9f` から
  `git show <commit>:...gate_synth_run4.py | sha256sum` で当時バイトを算出
  （`006cd867...`）。現行 HEAD のバイト（`579f7f0b...`）も参考値として
  別途記録し、両者を明確に区別した（現行 HEAD を当時バイトとして扱うことは
  していない）。

## Phase 2: 本命実測（1番 + 機能的証明）

- D4（VG-DEBT-004）実測の隔離 venv（`/home/user/d4work/venv_export`:
  numpy 1.26.4 / torch 2.13.0+cpu / lightning 2.3.3 / onnx 1.22.0）に
  `onnxruntime==1.29.0` を追加導入（numpy/torch は変更なし・依存衝突なし）。
  gate_synth_run4.py は export（torch）と synth（onnxruntime）を単一
  プロセスで行う一体型スクリプトのため、単一 venv での実行が必要だった。
- run4 の 40K checkpoint（`/home/user/run4work/model_ckpt_steps_40000.ckpt`。
  sha256 が run4_anchor_provenance.json の既存 pin と一致することを再確認
  済み）+ Phase 0 で取得した config.yaml/spk_map.json/lang_map.json/
  dictionary-ja.txt を入力に、pin コミット cda36b9f の
  `gate_synth_run4.py run --diffsinger-repo .../DiffSinger(e2307b1)
  --ckpt-dir <bundle> --exp-name s3_run4_acoustic --step 40000
  --canon-model-dir ... --vocoder-dir ... --out-dir ... --song sakura,umi
  --speaker ritsu` を実行 → **acoustic.onnx export 成功**
  （sha256 `a6da561a...`）+ ritsu 2 曲合成成功。続けて `--skip-export
  --acoustic-dir` で pjs・user も合成（原記録どおりの 3 起動 5 コマンド構成
  を忠実に再現）。
- **ローカル決定論の確認**: 同一手順を独立にもう一度実行し、acoustic.onnx
  の sha256・両 wav の sha256 とも完全一致することを確認（本実測プロセス
  自体は本セッション内で決定論的）。
- **run4 側 anchor wav 6 本の記録済み sha256 との照合**: **0/6 一致**。
  ただし全 6 本とも rms（4 桁）・dur（3 桁）は記録値と完全一致し、
  ファイルサイズも記録済み帯（sakura=2,079,788 bytes / umi=980,012 bytes）
  と一致した。構造的な破損や別内容の生成ではなく、波形バイトレベルでの
  非同一という所見であり、本実測環境（DiffSinger e2307b1 / torch 2.13.0+cpu
  / onnxruntime 1.29.0 / numpy 1.26.4）と run4 実行時の Pod 環境の版差
  （記録が無く不明）に由来する数値レベルの非決定性の可能性が高いと判断した
  （`s3_record_2026-08-17.md` §2 が記録する numpy SIMD dispatch 1 LSB
  分岐と同型の脆弱性クラス）。
- 結論: producer chain の機能的証明（wav 再生成一致）は**成立しなかった**。
  1 番（`acoustic_onnx.run4_onnx_gate_40000.sha256`）は `measured_only`
  とし、「2026-08-23 再 export 値であり当時バイトとの同一性は未確立」と
  明記した。

## Phase 3: run3系5件

- Drive 検索を複数パターンで実施（`run3` 全般・`onnx_export41`・
  `s3_run3`・`run3_acoustic`・`export41`）。run3 の 40K checkpoint も
  `onnx_export41` の実体も**発見できず**。
- したがって 2 番・3 番（acoustic_onnx.run3_onnx_export41.*）と
  7〜10 番（anchor_wavs.run3_gate_*.checkpoint_provenance）の 6 件は
  `not_closable` として記録した。Fable の accepted_residual 裁定材料として
  台帳 note へ引き継いだ。

## 成果物と検証

- `voice_genesis/foundry/results_s3/run4_provenance_closure_2026-08-23.json`
  （新規・schema `voicegenesis-run4-provenance-closure/0.1`）
- `voice_genesis/foundry/tests/test_run4_provenance_closure.py`（新規・13 test）
- `pyproject.toml` testpaths へ追加
- `voice_genesis/foundry/debt/debt_ledger.yaml` VG-DEBT-008 更新
  （evidence_delivered + note 追記。status は `in_progress` を維持——
  6 件が not_closable のまま残るため、依頼文の「10 件すべて
  reproduced/measured_only で閉じた場合のみ repaid」の条件を満たさない）

検証コマンドと結果:

```
ruff check .                                          # clean
python -m pytest voice_genesis/foundry/tests/test_run4_provenance_closure.py -q
                                                        # 13 passed
python -m pytest voice_genesis/foundry/tests/test_debt_ledger_shape.py -q
                                                        # 全 pass
python -m pytest tests/discipline/ -q                  # 全 pass
python -m pytest --collect-only -q                     # collection error なし
```

（実測値は本文書末尾ではなく、実行ログのまま次節に転記する。）

## 判断に迷った点

1. **canon_model.namine_ritsu_diffsinger.sha256 の対応先（zip vs 内部
   acoustic.onnx）**: --canon-model-dir はディレクトリ全体を渡す引数であり、
   gate_synth.py が実際に読むのは linguistic/dur/pitch/phonemes/dsconfig の
   5 ファイルで、zip 同梱の acoustic.onnx は本レシピでは不使用と判断した。
   フィールド名が zip 名（`namine_ritsu_diffsinger`）と一致すること、
   provision.sh がこの単位（zip 全体）で pin していることから、zip の
   sha256 を主値とし、5 消費ファイルの sha256 は materials へ副次記録した。
   この判断は Fable の設計裁定を仰ぐべき余地がある（対応先を honest に
   両論記録する形にした）。
2. **gate_synth_run4.py の pin コミット同定を「証拠」としてよいか**:
   git history からの状況証拠（前後のコミットタイムスタンプの挟み込み）
   のみで、Pod 上の実行時バイトと完全に同一という直接証拠は無い。
   `measured_only` に留め、`high_but_unproven` という confidence ラベルを
   明示した。
3. **wav 不一致を「不一致」のまま報告するか、原因調査を続けるか**: 予算・
   時間の制約から、原因の一次診断（rms/dur/サイズは一致 → 環境差による
   数値非決定性の可能性が高い、というレベル）で止めた。ONNX バイト単位の
   diff や複数 onnxruntime バージョンでの掃引は行っていない
   （追加実測が必要ならFableの設計判断で追加委譲を検討されたい）。

## Phase 4: 環境契約（X86_V4 無効化）下での再試行（2026-08-23 追加・Fable 指摘対応）

Fable が Phase 2/3 の結果を判読し、「run4 の環境契約（PR #266 正本・
`S3_RUN4_RUNBOOK.md` §2.2・`NPY_DISABLE_CPU_FEATURES=X86_V4` + 4 ゲート）を
満たしていなかった可能性が高い」と指摘。head `106c3398` の上に追加実測した。

### gate_synth の SIMD ゲート実装確認

`voice_genesis/foundry/s1_gate/`（`gate_synth.py` / `gate_synth_run4.py`）を
grep したが `NPY_DISABLE_CPU_FEATURES` / `SIMD` / `cpu_dispatch` / `X86_V` の
参照は**一切無い**。4 ゲートは `S3_RUN4_RUNBOOK.md` §2.2 が明記するとおり
**D3 render パイプライン**（`run_d3_cells.py` / `convert_d3.py`。
`run5_bootstrap.py` がプログラム的に `os.environ["NPY_DISABLE_CPU_FEATURES"]
= "X86_V4"` を設定する経路）専用のオペレータ規律であり、`gate_synth_run4.py`
が担う「40K checkpoint からの acoustic ONNX export + anchor wav 合成」経路
には一度も配線されていない。「アサートが無い経路を通った」のではなく、
そもそもこの経路にアサート/ゲート自体が存在しない。

### SIMD 実測（重要な追加発見）

`S3_RUN4_RUNBOOK.md` ゲート 1 は本契約が **numpy 2.4.6 の挙動**を前提とする
と明記している。本セッションの export/synth 用 venv は DiffSinger の
numpy<2 要求により **numpy==1.26.4** を使っており、この版では文字列
`'X86_V4'` はディスパッチ済み最適化グループとして認識されない
（`__cpu_dispatch__` に一致するトークンが無い）ことを実測で確認した:

```
$ NPY_DISABLE_CPU_FEATURES=X86_V4 python -W always -c "import numpy"
ImportWarning: You cannot disable CPU features (X86_V4), since they are not
part of the dispatched optimizations (SSSE3 SSE41 POPCNT SSE42 AVX F16C FMA3
AVX2 AVX512F AVX512CD AVX512_KNL AVX512_KNM AVX512_SKX AVX512_CLX AVX512_CNL
AVX512_ICL).
```

つまり指示された値をそのまま設定しても numpy 1.26.4 環境では**完全な
no-op**（ゲート 3 が numpy 2.4.6 について記す「黙って無視」とは違う失敗
モード＝warning 付き no-op だが、結論は同じ「効かない」）。そこで
`__cpu_dispatch__` に実在する AVX512 系トークンを個別列挙した機能的等価値
`AVX512F,AVX512CD,AVX512_KNL,AVX512_KNM,AVX512_SKX,AVX512_CLX,AVX512_CNL,
AVX512_ICL` へ置き換えたところ、`found` が `AVX2` 止まりになる（AVX512 系が
全て消える）ことを実測で確認した——runbook ゲート 2 が意図する
「X86_V3=AVX2 相当への固定」を genuinely 達成できた状態である。

### WAV 再生成（AVX512 無効化状態）

Phase 2 と同一の入力（同一 export ONNX・canon・vocoder・config・
generation command）で、上記の genuinely-AVX2-only な numpy 環境の下
6 本を再生成:

| 話者 | 曲 | 記録済み sha256 | Phase2（AVX512有効） | Phase4（AVX512無効） | 一致 |
|---|---|---|---|---|---|
| ritsu | sakura | 55bd14c2... | 2a07c12d... | cc3b1d83... | ✗ |
| ritsu | umi | a12ef548... | 80cdb092... | c7f84... | ✗ |
| pjs | sakura | b9ce3454... | 92ee7ba4... | 536d86cf... | ✗ |
| pjs | umi | 43d6bff1... | a23f82a1... | 30d27aad... | ✗ |
| user | sakura | e2f7b270... | d7a67c87... | dc960d04... | ✗ |
| user | umi | ac8b3455... | ef1d021f... | 078b085a... | ✗ |

**0/6 一致**（AVX512 有効時と同じ）。acoustic ONNX の sha256 自体は
AVX512 有効/無効で不変（`a6da561a...`）——export（torch グラフトレース+
simplify）は numpy SIMD dispatch に依存しないことも判明した。

ローカル決定論は AVX512 無効化状態でも確認済み（ritsu を独立に 2 回実行し
sha256 完全一致）。

### サンプル単位の乖離を定量測定

AVX512 有効時と無効時の `gate_sakura_ritsu.wav` を int16 サンプル列で diff:

- 総サンプル数 1,039,872 中 **113,358 サンプル（約 10.9%）が異なる**
- 最大絶対差 **±6 LSB**、非ゼロ差分の約 93%（107,037/113,358）は **±1 LSB**

D3 の事例（1,524,000 サンプル中 1 サンプルのみ・1 LSB）よりも遥かに広範囲
に影響している。これは DiffSinger の reflow/diffusion サンプリングが多段の
反復推論であり、各ステップの微小な浮動小数点差が後続ステップへ伝播・蓄積
するため（WORLD ボコーダの単発合成より分岐点が遥かに多い）と考えられる。
rms/dur が両状態で完全一致するのは、これらが波形全体の集約統計量であり
±数 LSB のサンプル単位ノイズには鈍感なため。

### 結論（境界宣言）

SIMD dispatch 状態（AVX512 有効/無効）は `gate_synth.py` の WAV 出力バイトに
**実測で影響することを確認した**（新規知見——s3_record §2 の「実行環境が
バイト一致に効く」という一般則を、D3 とは別の経路で追試・再確認した形）。
しかし run4 環境契約を満たした状態（AVX512 無効化）でも**記録済み
sha256 とは一致しなかった**（0/6 のまま）。したがって「環境契約の
未充足だけが原因」という仮説は**本実測では反証**された——SIMD 状態は一因
（かつ実測でサンプルの最大約 11% に影響する非小さい要因）ではあるが、
単独では run4 当時バイトの再現を説明・達成するには不十分である。

列挙するに留めた未制御要因（掃引はしない）:

1. **acoustic ONNX export 時のデバイス（GPU vs CPU）**: run4 実行環境は
   RunPod GPU Pod（s3-run4-v2）であり、`config.yaml` の
   `pl_trainer_accelerator: auto` や学習自体が GPU 実行だったことから、
   `export.py` 実行時も CUDA 上で checkpoint をロードし
   `torch.onnx.export` をトレースした可能性が高い（`gate40k.log` に
   device 指定の明記は無いため断定はできない）。本実測は
   `torch==2.13.0+cpu`（CPU 専用ビルド）で export しており、CUDA vs CPU
   のトレース経路差（cuDNN kernel 由来の定数畳み込み値の丸め差等）が
   ONNX グラフの定数値レベルで生じ得る——**これは acoustic ONNX の sha256
   自体が当時バイトと異なる可能性の最有力候補であり、SIMD 要因より上流
   （export 段）に位置する**。GPU 環境が本セッションに無いため実行不能。
2. onnxruntime バージョン（本実測 1.29.0。当時版の記録・証拠は無い）
3. DiffSinger (openvpi) revision（本実測 e2307b1。当時の git revision は
   `gate40k.log`/`gate_run3_anchor_v2.log` のいずれにも記録が無く不明）
4. OS/libm/コンテナベースイメージの差（本セッションのコンテナと
   RunPod Pod は別物）

**境界宣言**: run4 当時バイトの再現は本環境では未達（0/6）。差分は
サンプルの約 11%・最大 ±6 LSB という小さいが非ゼロの規模であり、構造的
破損や別内容の生成ではない。これ以上の掃引（onnxruntime 版 / DiffSinger
revision / GPU export 環境の用意）は本実測のスコープ外とし、
item 1（acoustic ONNX sha）は `measured_only` に据え置く。

### 台帳・記録更新

- `run4_provenance_closure_2026-08-23.json` に `phase4_env_contract_retest`
  節を追記（既存の Phase 0-3 記録・`items`/`wav_regeneration` は無改変。
  item 1・canon・vocoder・generation_script の `note` フィールドのみ本追試の
  参照を追記）
- `test_run4_provenance_closure.py` に Phase 4 節の形状テストを追加
- `debt_ledger.yaml` VG-DEBT-008 の `note` を最新結果に合わせて更新
  （status は `in_progress` 据え置き——run3 系 6 件が残るため）
