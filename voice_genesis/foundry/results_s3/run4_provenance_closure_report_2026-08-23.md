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
  `5c7b8c32...` と完全一致。展開後、`gate_synth.py` が canon ディレクトリから
  実際に読む **4 ファイル**（linguistic.onnx / dsdur/dur.onnx /
  dspitch/pitch.onnx / phonemes.txt。`gate_synth.py:647-650, 1891`）の sha256 を
  個別記録し、そのマニフェストハッシュを closure の主値とした（第 8 巡 P2。
  詳細と算法は「判断に迷った点」1 節を参照）。
  `dsconfig.yaml` は **本件では canon 側から読まれていない** — `acoustic_dsconfig_path`
  は export 出力側（`gate_synth.py:2007`）を指し、canon 側が読まれるのは
  (a) `--skip-export` の S0 互換経路（`gate_synth.py:1981`）、または
  (b) **export 出力側に `dsconfig.yaml` が無い場合のフォールバック**
  （`gate_synth.py:2007-2009`）の 2 経路である（＜(b) の存在は第 12 巡 P2 で判明。
  それまで「export 経路では canon 側を読まない」と**断定**していたのは誤り＞）。
  本件が (a)(b) いずれにも該当しないことは実測で確認した:
  **run4 当時** = `gate40k.log` に `| export configs => .../dsconfig.yaml` とあり
  exporter が自前の dsconfig.yaml を出力している。**本実測** = 全 8 run の
  `gate_synth_summary.json` が `acoustic_dsconfig_yaml = e00aadb2...`
  （export 出力側の実体）を記録しており、canon top-level の `8af86d46...` とは
  一致しない。**したがってマニフェストの完全性は分岐依存であり**、exporter が
  dsconfig.yaml を出力しない run ではフォールバックが成立して canon 側
  `dsconfig.yaml` が消費入力に加わる — その場合は消費集合とマニフェストに
  加えること（＜第 8 巡 P2 で消費 5→4 へ訂正、第 10 巡 P2 で本節へ反映、
  第 12 巡 P2 で分岐条件を明示＞）。
  canon top-level `dsconfig.yaml` と zip 同梱の `acoustic.onnx`（canon 側の
  自前 acoustic）はいずれもこのレシピでは未消費で、記録上は「参考値」として
  残した。
- **vocoder**: `nsf_hifigan.oudep`（52,847,838 bytes）を取得 → sha256 が
  provision.sh の pin `e22f8400...` と完全一致。展開後の
  `nsf_hifigan.onnx` の sha256（`a3e26672...`）も provision.sh の
  `VOC_WANT` と完全一致（これは --vocoder-dir から実際に読まれる唯一の
  ファイルであり曖昧性なし）。
- **gate_synth_run4.py**: Phase 0 で同定した pin コミット候補 `cda36b9f` から
  `git show <commit>:...gate_synth_run4.py | sha256sum` で
  **リポジトリ側バイト（＝当時バイトの推定候補）** を算出（`006cd867...`）。
  **これを「当時バイト」と呼んではならない** — run4 の Pod に未コミット編集が
  あった可能性を排除できず、`git show` はリポジトリ候補しかハッシュできない
  ため、コミット同定の確度は `high_but_unproven` に留まる（JSON 側 item の
  `note` も同旨）。現行 HEAD のバイト（`579f7f0b...`）も参考値として別途記録し、
  両者を明確に区別した（現行 HEAD を当時バイトの代用として扱うことはしない）。
  ＜PR #307 Codex 第 2 巡 P2 指摘により表現を訂正＞

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
  と一致した。ただし**これは要約統計の一致に過ぎず、内容の類似性・不一致の
  規模・不一致の原因はいずれも未測定**である（記録済み波形実体が無いため。
  ＜PR #307 Codex 第 5–6 巡 P2 指摘により、当初の「同じ演奏内容だが波形バイト
  レベルで非同一」「別内容ではない」「環境差由来の数値非決定性」という
  characterization を撤回＞）。本実測環境
  （DiffSinger e2307b1 / torch 2.13.0+cpu / onnxruntime 1.29.0 / numpy 1.26.4）と
  run4 実行時の Pod 環境との版差は**未記録・不明**である。版差が数値レベルの
  非決定性を生む機構自体は既知だが（`s3_record_2026-08-17.md` §2 が記録する
  numpy SIMD dispatch 1 LSB 分岐と同型の脆弱性クラス）、**それが本件 0/6 の
  原因であるとも、他の候補より確からしいとも言わない** — 原因は未測定であり
  順位付けもしない（＜第 6 巡 P2 で原因帰属を撤回済み。残っていた「可能性が
  高い」の順位表現も第 7 巡 P2 の規律で撤回＞）。
- 結論: producer chain の機能的証明（wav 再生成一致）は**成立しなかった**。
  1 番（`acoustic_onnx.run4_onnx_gate_40000.sha256`）は `measured_only`
  とし、「2026-08-23 再 export 値であり当時バイトとの同一性は未確立」と
  明記した。

## Phase 3: run3系6件

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
- `voice_genesis/foundry/tests/test_run4_provenance_closure.py`（新規。件数は最終節「Phase 5」を参照）
- `pyproject.toml` testpaths へ追加
- `voice_genesis/foundry/debt/debt_ledger.yaml` VG-DEBT-008 更新
  （evidence_delivered + note 追記。**本実測時点では** status を
  `in_progress` に据え置いた——6 件が not_closable のまま残るため、
  「10 件すべて reproduced/measured_only で閉じた場合のみ repaid」の
  条件を満たさない。**最終的な status は Phase 5（Fable 裁定）で
  `accepted_residual` に確定した**）

検証コマンドと結果:

```
ruff check .                                          # clean
python -m pytest voice_genesis/foundry/tests/test_run4_provenance_closure.py -q
                                                        # pass（件数は Phase 5 参照）
python -m pytest voice_genesis/foundry/tests/test_debt_ledger_shape.py -q
                                                        # 全 pass
python -m pytest tests/discipline/ -q                  # 全 pass
python -m pytest --collect-only -q                     # collection error なし
```

（実測値は本文書末尾ではなく、実行ログのまま次節に転記する。）

## 判断に迷った点

1. **canon_model.namine_ritsu_diffsinger.sha256 の対応先（zip vs 消費
   メンバー）**: 当初は zip 全体の sha256 を主値とし、消費ファイルの sha256 は
   materials へ副次記録していた（フィールド名が zip 名と一致し、provision.sh も
   zip 単位で pin しているため）。**第 8 巡 P2 でこれを改めた** — 閉じるべき ref は
   `--canon-model-dir` という**ランタイム入力**であり、zip のアーカイブハッシュは
   それを同定しない（再パックすれば同一メンバーでもアーカイブハッシュは変わり、
   別アーカイブなら消費メンバーも変わり得る）。現在の主値は**消費メンバーの
   決定論的マニフェストハッシュ** `b524ac52...`（`value_kind:
   consumed_member_manifest_sha256`）で、zip 全体の sha256 は materials に
   残す。マニフェスト算法は
   `materials.canon_consumed_member_manifest.algorithm` が正。
   併せて**消費集合そのものの誤りも 1 件訂正した**: 従来 5 ファイルとして
   `dsconfig.yaml` を含めていたが、gate_synth.py で `dsconfig.yaml` が読まれるのは
   `acoustic_dsconfig_path` であり、**export 経路**（run4 anchor 合成・本実測
   Phase 2 の両方が該当）ではこれは export 出力側（`gate_synth.py:2007`）で、
   canon 側（`gate_synth.py:1981`）が読まれるのは `--skip-export` の S0 互換経路
   のみ**ではない** — export 出力側に `dsconfig.yaml` が無い場合は canon 側へ
   フォールバックする分岐が `gate_synth.py:2007-2009` に実在する（＜第 12 巡 P2＞。
   本件では run4 当時・本実測とも不成立を実測確認済み。Phase 1 節を参照）。
   さらに zip 内には `dsconfig.yaml` が 3 個（top-level / dsdur /
   dspitch）あり、ラベル自体が曖昧だった。消費集合は
   **linguistic.onnx / dsdur/dur.onnx / dspitch/pitch.onnx / phonemes.txt の 4 件**
   （`gate_synth.py:647-650, 1891`）へ訂正し、canon top-level `dsconfig.yaml` は
   非消費参照値として materials に残した。実測は Phase 2 で `--canon-model-dir`
   に渡した展開実体から直接算出し、配布 zip の同名メンバーと **4/4 バイト一致**
   することを独立照合している。
2. **gate_synth_run4.py の pin コミット同定を「証拠」としてよいか**:
   git history からの状況証拠（前後のコミットタイムスタンプの挟み込み）
   のみで、Pod 上の実行時バイトと完全に同一という直接証拠は無い。
   `measured_only` に留め、`high_but_unproven` という confidence ラベルを
   明示した。
3. **wav 不一致を「不一致」のまま報告するか、原因調査を続けるか**: 予算・
   時間の制約から一次診断で止めた。ONNX バイト単位の diff や複数
   onnxruntime バージョンでの掃引は行っていない。
   ＜当初ここには「rms/dur/サイズは一致 → 環境差による数値非決定性の可能性が
   高い」と書いていたが、この原因帰属は記録済み波形実体が無い以上確立できず、
   PR #307 Codex 第 5–6 巡 P2 指摘により撤回した。**不一致の原因は未特定**が
   正しい＞

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
しかし**契約の SIMD 条項に相当する AVX512 無効化を近似適用した状態**
（＝契約の充足ではない。上記「契約充足の主張範囲」を参照）でも**記録済み
sha256 とは一致しなかった**（0/6 のまま）。したがって「環境契約の
未充足だけが原因」という仮説について、**検証されたのは「SIMD 条項の
近似適用のみでは不十分」という範囲まで**である（numpy 2.4.6 stack pin を含む
4 ゲート全体・未記録の runtime revision は未制御のため、「環境契約全体の
未充足が原因」という広い仮説は**未検証**。＜PR #307 Codex 第 5 巡 P2 指摘に
より、当初の「反証された」という広い結論を検証済みの範囲へ縮小＞）——SIMD 状態は一因
（かつ実測でサンプルの最大約 11% に影響する非小さい要因）ではあるが、
単独では run4 当時バイトの再現を説明・達成するには不十分である。

列挙するに留めた未制御要因（掃引はしない）:

1. **acoustic ONNX export 時のデバイス（GPU vs CPU）**: 第 7 巡 P2 を受け、
   pin 済み exporter の device 選択経路を実査した。DiffSinger
   `e2307b1080b00f3999702ce9017cfd75c7f862fe` の `scripts/export.py` は
   device を選ぶ CLI オプションを持たず、`acoustic` サブコマンドは
   `device=torch.device('cuda' if torch.cuda.is_available() else 'cpu')`
   （L138。`variance`/`nsf_hifigan` の L217/L282 も同型）で**実行時に自動
   選択**する。`deployment/exporters/acoustic_exporter.py:21` の既定
   `'cpu'` は呼び出し側のこの引数で上書きされる。呼び出し元
   `gate_synth.py:1084-1091` も device フラグを渡さず
   `CUDA_VISIBLE_DEVICES` の scoping もせず、親環境をそのまま継承した
   subprocess として起動する。したがって**この経路に CPU を強制する機構は
   存在しない** — export プロセスで `torch.cuda.is_available()` が真なら
   CUDA でトレースされる。`S1_GPU_RUNBOOK.md` §5.2 の「CPU で足りる」は
   **十分性の記述**であって実行記録ではなく、強制もしない。
   **未記録なのは述語そのもの**である: run4 の export プロセスにおける
   torch ビルド（CUDA 版か CPU 専用版か）と GPU の可視性は
   `gate40k.log`/`gate_run3_anchor_v2.log` のいずれにも記録が無い。
   本実測は `torch==2.13.0+cpu`（CPU 専用ビルド）で export しており、
   CUDA vs CPU のトレース経路差（cuDNN kernel 由来の定数畳み込み値の
   丸め差等）が ONNX グラフの定数値レベルで生じ得るため、acoustic ONNX の
   sha256 が当時バイトと異なる**機構としては成立しうる**（かつ SIMD 要因より
   上流＝ export 段に位置する）。**ただし順位は付けない** — 実行時デバイスが
   未記録である以上、他の未制御要因（2–4）より確からしいと言う根拠が無い
   （＜第 7 巡 P2 により「最有力候補」「可能性が高い」の順位付けを**撤回**し、
   列挙候補の 1 つとして残す＞）。GPU 環境が本セッションに無いため検証は
   実行不能。
2. onnxruntime バージョン（本実測 1.29.0。当時版の記録・証拠は無い）
3. DiffSinger (openvpi) revision（本実測 e2307b1。当時の git revision は
   `gate40k.log`/`gate_run3_anchor_v2.log` のいずれにも記録が無く不明）
4. OS/libm/コンテナベースイメージの差（本セッションのコンテナと
   RunPod Pod は別物）

**境界宣言**: run4 当時バイトの再現は本環境では未達（0/6）。
**記録済み WAV との差分の規模は未測定** — 記録済み run4 anchor WAV の波形
実体は repo にも本セッションにも無く（残っているのは sha256 と rms/dur の
記録のみ）、サンプルレベルの比較ができないためである。記録済みとの関係で
言えるのは rms（4 桁）・dur（3 桁）・ファイルサイズが一致することまでで、
これは**要約統計の一致**に過ぎず、**内容の類似性は未測定**である
——無関係な波形でもこれら 3 統計は一致しうるため、「全く別内容ではない」とは
言えない（＜PR #307 Codex 第 5 巡 P2 指摘により当初の主張を撤回＞）。
**約 11%・最大 ±6 LSB は再実行同士（AVX512 有効 vs 無効）の比較値であり、
歴史的不一致の規模ではない**（＜PR #307 Codex 第 3 巡 P2 指摘により訂正。
当初は後者を前者の根拠として書いていた＞）。これ以上の掃引（onnxruntime 版 /
DiffSinger revision / GPU export 環境の用意）は本実測のスコープ外とし、
item 1（acoustic ONNX sha）は `measured_only` に据え置く。

**契約充足の主張範囲**: 本追試は run4 環境契約の**充足ではなく、SIMD 条項の
近似適用**である。契約（`S3_RUN4_RUNBOOK.md` §2.2）は numpy 2.4.6 の stack pin
を含む 4 ゲート全体を要求し、かつ D3 再生成にスコープされていて anchor 合成
経路には配線されていない。本実測の venv は DiffSinger 互換のため numpy 1.26.4
固定で、X86_V3/X86_V4 のトークン語彙自体を持たないため個別 AVX512 トークン列で
代替した。詳細は closure JSON の `contract_compliance_scope`。

### 台帳・記録更新

- `run4_provenance_closure_2026-08-23.json` に `phase4_env_contract_retest`
  節を追記（既存の Phase 0-3 記録・`items`/`wav_regeneration` は無改変。
  item 1・canon・vocoder・generation_script の `note` フィールドのみ本追試の
  参照を追記）
- `test_run4_provenance_closure.py` に Phase 4 節の形状テストを追加
- `debt_ledger.yaml` VG-DEBT-008 の `note` を最新結果に合わせて更新
  （この時点では status を `in_progress` 据え置き——run3 系 6 件が残るため。
  **確定は Phase 5 を参照**）

## Phase 5: Fable 裁定と PR #307 セルフレビュー対応（2026-08-23）

### 裁定: VG-DEBT-008 = `accepted_residual`

実測 2 巡（Phase 0–4）を判読した結果、以下を理由に **`accepted_residual`
（証拠境界の明示）で確定**した。元の `close_condition`（全リンクが missing
無しで閉じている）は満たさないまま、条件不充足を明示して閉じる形であり、
VG-DEBT-010 と同型の扱いである。

1. 閉じられる範囲は閉じた — canon 消費メンバー manifest / vocoder onnx / gate_synth_run4.py
   pin-commit / run4 acoustic ONNX 再 export の 4 件を実測値つきで記録
   （`closure: measured_only`）
2. run3 系 6 件は run3 checkpoint・onnx_export41 が Drive 含め所在不明で
   構造的に閉鎖不能（`closure: not_closable`）
3. run4 側の「当時バイトとの同一性」は本環境で未達。**Fable 自身の
   SIMD 主因仮説について、Phase 4 で検証されたのは「SIMD 条項の近似適用の
   みでは不十分」という範囲まで**（広い仮説＝環境契約全体の未充足が原因、は
   未検証。＜第 5 巡 P2 により縮小＞）。残余候補の 1 つは
   GPU（run4 = RunPod GPU pod）vs CPU（本実測 = torch cpu）の export
   device 差だが、**順位は付けない**（run4 の export 実行時デバイスは未記録。
   ＜第 7 巡 P2＞）。検証は GPU 課金を要するため逓減領域として掃引しない

`reentry_condition` を台帳へ記録した（**正本は `debt_ledger.yaml` の
`reentry_condition`**。本段落はその要約であり、乖離した場合は台帳が勝つ）:

- **(a-1)** item 1 が `reproduced` へ昇格できるのは、**当時の ONNX 実体の回収** /
  **当時 sha の記録としての発見** / **producer chain の全消費入力（ckpt・実行時ラッパー
  `gate_synth_run4.py` の実バイト・exporter revision・実行時 device・数値
  スタック・canon 消費メンバー・vocoder、およびここに列挙されていない消費入力を
  含むすべて）が証拠づけられての再 export バイト同一性の提示** のいずれかが
  成立した場合のみ。**この列挙は閉じた集合ではない** — `gate_synth_run4.py` の
  実行時バイトは現状 `high_but_unproven`（リポジトリ側バイトからの推定）であり、
  未 pin のラッパーは export コマンドや入力の選択自体を変え得る。列挙因子が
  揃っても他の消費入力が未証拠なら `reproduced` にしない（＜第 11 巡 P2＞）。**anchor WAV 6 本の一致では
  昇格しない** — WAV 一致は 6 入力に対する振る舞いの一致であって、当時 sha が
  未記録の上流 ONNX バイトとの同一性の証拠ではない（＜第 8 巡 P2＞）
- **(a-2)** 未制御要因のいずれかを制御した環境で WAV が記録済み sha と一致した
  場合は、producer chain の**機能的裏付け**として別途記録する。**item 1 の
  closure は動かさない**。export device 差はその候補の 1 つであって確定した
  昇格経路ではない（＜第 7 巡 P2 により GPU 前提の記述を撤回＞）
- **(b)** run3 checkpoint が発見されれば run3 系 6 件は再開可能

### セルフレビュー（PR #307・high）指摘 8 件を全採用

| # | 指摘 | 対応 |
|---|---|---|
| 1 | 本報告が `in_progress` と書いており台帳の `accepted_residual` と矛盾 | 本節を正本として追加し、Phase 3/4 の該当記述に「確定は Phase 5」を明記 |
| 2 | closure JSON の sha256 pin が immutability テストに未登録（機械強制なし） | `test_committed_artifacts_immutable.py` に VG-DEBT-008 分を追加（台帳から動的に読む既存方式） |
| 3 | `run4_anchor_provenance.json` の sha256 を acceptance が宣言しているのに未検証 | closure テストで実ファイルと照合 |
| 4 | Phase 4 の `match_count` 相互検査に bool 型検査が無く、キー名取り違えで vacuous pass | `isinstance(..., bool)` を追加（phase2=`match` / phase4=`match_recorded` のキー名差も明示検査） |
| 5 | `measured_only` に value 制約が無く `value: null` が通る | `measured_only` は 64-hex の value 必須を強制 |
| 6 | 「13 test」が stale（Phase 4 で 14 に） | 実数への追随をやめ、件数は本節参照へ変更 |
| 7 | 見出し「Phase 3: run3系5件」が本文・JSON・台帳の 6 件と不一致 | 「run3系6件」へ修正 |
| 8 | acceptance が Phase 0–3 を「無改変・追記のみ」と主張するが `wav_regeneration.results` が再シリアライズされていた | acceptance の文言を実態（値は同一・整形は変化しうる）へ訂正 |

### Codex レビュー第 2–12 巡（PR #307）も全採用

| 巡 | 指摘 | 対応 |
|---|---|---|
| 2 | `git show` の結果を「当時バイト」と呼ぶのは `high_but_unproven` と矛盾 | 「リポジトリ側バイト（＝推定候補）」へ訂正 |
| 2 | 凍結 hash が可変な台帳の中にしかなく、artifact と台帳の協調書き換えで素通り | 台帳外の独立アンカー `FROZEN_SHA256` を追加し二重照合化（3 凍結物すべて） |
| 3 | 11%/±6LSB は再実行同士の比較値なのに歴史的不一致の規模として使っていた | 「記録済みとの差分は**未測定**」へ訂正し、測定値の適用範囲を JSON/report 双方に明記 |
| 3 | 台帳が「環境契約を充足」と書くが実際は numpy 2.4.6 pin を欠く近似 | 「SIMD 条項の近似適用」へ訂正し `contract_compliance_scope` を新設 |
| 4 | 「契約充足」の訂正が `note` 止まりで `evidence_delivered`・JSON item notes・report 本文に残存 | **ファミリー全数掃討**（下記「終端宣言」）|
| 5 | 「仮説は反証された」は広すぎる（検証したのは SIMD 条項の近似のみ） | 「SIMD 条項の近似適用では不十分」まで**結論を縮小**し、広い仮説は**未検証**と明記（全数掃討）|
| 5 | rms/dur/サイズ一致から「全く別内容ではない」は根拠不足 | **主張を撤回**し「要約統計の一致に過ぎず内容の類似性は未測定」へ（無関係な波形でも 3 統計は一致しうる）|
| 6 | Phase 2 の `characterization` が「同じ演奏内容」と書き、不一致を環境差に帰属していた | 内容同等性と原因帰属の両方を**撤回**し `characterization_withdrawn_2026-08-23` を新設（掃討語彙を拡張して再掃討）|
| 7 | GPU/CPU の export device 差を「最有力」と順位付けているが実行時デバイスは未記録 | pin 済み exporter の device 選択経路を**実査**して機構を記録し、**順位付けは撤回**（下記「終端宣言（追補 2）」）|
| 8 | canon の closure 値が配布 zip のアーカイブハッシュで、合成が消費するランタイム入力を同定していない | **消費メンバーの決定論的マニフェストハッシュ**へ変更（`value_kind` を全 item に新設）。併せて消費集合の誤り（`dsconfig.yaml` は export 経路では canon 側を読まない）を訂正し 5→4 件へ |
| 8 | `reentry_condition` (a) が WAV 一致だけで item 1 を `reproduced` へ昇格させ得る | 昇格条件を**上流バイト証拠**（当時実体の回収 / 当時 sha の発見 / producer chain 全証拠）に限定し、WAV 一致は (a-2) の**機能的裏付け**として closure を動かさない扱いへ分離 |
| 9 | 台帳の (a-1)/(a-2) 分離を report 本文の要約段落へ反映し損ねており、旧「WAV 一致で昇格可」規則が残存 | 該当段落を (a-1)/(a-2)/(b) の 3 分割へ書き換え、**台帳が正本**であることを明記（掃討: `昇格`/`reproduced へ` 全数）|
| 10 | closure JSON の `acceptance`（スキーマ水準の判定契約）に旧「WAV 一致で reproduced」規則が残存 | 判定規則を (a-1) 相当へ書き換え、**台帳が正本**と明記。掃討語彙を `昇格` から **`reproduced`** 全件へ拡大して再掃討 |
| 10 | Phase 1 が canon 消費を 5 ファイル（`dsconfig.yaml` 含む）と記述したままで、同一 report 内に 2 つの定義が併存 | Phase 1 を **4 ファイル**へ訂正し、`dsconfig.yaml` が canon 側から読まれない条件も明記（掃討: `5 ファイル`/`dsconfig` 全数）|
| 11 | (a-1) の `producer chain 全体` が 4 因子の閉じた列挙で、未 pin の実行時ラッパー（`gate_synth_run4.py` = `high_but_unproven`）を含まず、4 因子の回収だけで `reproduced` を許す | 列挙を**全消費入力**（実行時ラッパーの実バイトを明示）へ拡張し、**閉じた集合ではない**ことを明記。上限 10 巡到達後だが「新しい具体経路を示す指摘」として 3 分類基準で採用（CLAUDE.md）|
| 12 | 「export 経路では canon 側 dsconfig を読まない」は断定として誤り（`gate_synth.py:2007-2009` に canon 側フォールバックが実在）。分岐次第で 4 メンバー manifest は消費入力を取りこぼす | 断定を撤回し**分岐条件**を明記。併せて**分岐を通っていないことを実測**（run4 当時 = `gate40k.log` の `export configs` 行 / 本実測 = 全 8 run が `acoustic_dsconfig_yaml = e00aadb2...` を記録、canon `8af86d46...` と不一致）し、完全性が分岐依存であることを JSON/report 双方に記録。3 分類基準で採用 |

検証（Phase 5 時点）:

```
ruff check .                                                   # clean
python -m pytest voice_genesis/foundry/tests/test_run4_provenance_closure.py \
    voice_genesis/foundry/tests/test_committed_artifacts_immutable.py \
    voice_genesis/foundry/tests/test_debt_ledger_shape.py tests/discipline -q
python -m pytest --collect-only -q                             # collection error 0
```

### 終端宣言: 「環境契約を充足」系の表現（2026-08-23）

Codex 第 3–4 巡の指摘を受け、本 PR の成果物 3 ファイル
（`debt_ledger.yaml` / `run4_provenance_closure_2026-08-23.json` /
本 report）を `充足した状態` `を満たした状態` `契約充足後` `契約を満たし`
で全数 grep し、**該当箇所をすべて「SIMD 条項の近似適用」へ訂正した**
（台帳の `evidence_delivered` と `note`、JSON の item notes 2 件・
`conclusion`・`verdict`、report 本文 2 箇所）。

掃討後に残る唯一のヒットは `contract_compliance_scope` の
「run4 環境契約の【充足】ではなく」という**否定文脈**であり、正しい記述である。

以後この系統の表現を追加する場合は `contract_compliance_scope` と整合させること。
本系統はこれをもって終端とする。

### 終端宣言（追補）: 主張強度に関する 2 系統（2026-08-23）

Codex 第 5 巡を受け、以下 2 系統も成果物 3 ファイルで全数 grep して掃討した。

1. **「反証された」系** — Phase 4 が検証したのは「SIMD 条項の近似適用のみでは
   不十分」までであり、numpy 2.4.6 stack pin を含む 4 ゲート全体や未記録の
   runtime revision（onnxruntime 版 / DiffSinger revision / export device）は
   未制御のままである。したがって「環境契約全体の未充足が原因」という広い仮説は
   **未検証**であり、これを「反証」と書いてはならない。
2. **「別内容ではない」系** — rms・dur・ファイルサイズの一致は**要約統計の一致**
   に過ぎず、無関係な波形でも成立しうる。記録済み WAV の波形実体が無い以上、
   内容の類似性は**未測定**であり、内容同等性を主張してはならない。

掃討後に残るヒットはいずれも「当初こう書いたが縮小・撤回した」という**訂正の
記録**であり、主張そのものではない。本 2 系統もこれをもって終端とする。

### 終端宣言（追補 2）: 残余候補の順位付け（2026-08-23）

Codex 第 7 巡を受け、成果物 3 ファイルを `最有力` `有力` `可能性が高い`
`第一候補` `主因` で全数 grep し、**残余候補への順位付けをすべて撤回した**
（report 本文 2 箇所、JSON の
`other_uncontrolled_factors_enumerated_not_swept` item 1、台帳の
`evidence_delivered` と `note`、および GPU 前提で書かれていた
`reentry_condition` (a)）。

撤回の根拠は「候補が否定された」ことではない。pin 済み exporter の実査で
**機構は成立しうる**ことが分かった（device は `torch.cuda.is_available()`
による自動選択で、この経路に CPU 強制機構は無い）一方、run4 の export
プロセスでその述語が真だったかは**未記録**であり、他の未制御要因より
確からしいと言う根拠が無いためである。**機構の成立可能性と順位は別物**であり、
実行時デバイスが証拠づけられるまで順位を書いてはならない。

掃討で追加検出した残余が 1 件あった: Phase 2 の「版差に由来する数値非決定性の
**可能性が高い**と判断した」— 第 6 巡で原因帰属を撤回した後も参考情報として
順位表現が残っていた。これも撤回し「機構は既知だが原因とも順位とも言わない」へ
訂正した。

掃討後に残るヒットは 3 種のみで、いずれも主張ではない: (i)「本命実測」
（Phase 2 見出し＝どの実測が主目的かのスコープ表現であって因果順位ではない）、
(ii) Phase 4 の起動理由・JSON `trigger` に引用された**当時の Fable 指摘**
（「契約を満たしていなかった可能性が高い」= 再実測を起こした過去の判断の記録）、
(iii)「SIMD 主因仮説」という**仮説の呼称**および上記の**撤回の記録**。
本系統もこれをもって終端とする。

### 終端宣言（追補 3）: 訂正の他成果物への追随（2026-08-23）

第 8–10 巡で 3 回続けて「台帳では直したが他の成果物に旧記述が残る」型を突かれた
（第 9 巡 = report 要約段落、第 10 巡 = closure JSON の `acceptance` と Phase 1）。
掃討語彙が狭かったことが原因である: `昇格` で掃討したが `acceptance` は
`reproduced` としか書いておらず、`消費 5 ファイル` の訂正も「判断に迷った点」節
だけに入れて Phase 1 本文を見ていなかった。

第 10 巡でこの 2 系統を成果物 3 ファイル全数で再掃討した:

1. **`reproduced` 昇格規則** — `reproduced` を**全件**（`昇格` ではなく）grep。
   残るヒットは新規則本体・訂正の記録・台帳 `close_condition`
   （「全リンクが reproduced/measured_only で閉じた場合のみ repaid」= 別の規則）・
   `wav_regeneration.results` の `reproduced_sha256`/`reproduced_rms`
   （**再生成物の値**を指すフィールド名であって closure 語彙ではない）のみ。
2. **canon 消費集合** — `5 ファイル` / `dsconfig` を全件 grep。残るヒットは
   訂正後の 4 ファイル記述と、撤回の記録のみ。

**規律**: 判定規則・数え上げを直すときは、`debt_ledger.yaml` /
`*_closure_*.json`（`acceptance` を含む）/ 本 report の**3 面すべて**を、
訂正後の語ではなく**訂正前の語彙**で grep してから終端宣言すること。
正本は台帳の `reentry_condition` であり、乖離時は台帳が勝つ。
本系統もこれをもって終端とする。

以上により、本 PR で指摘された「実測は正しいが記録の言葉が実測より強い」
という類型（契約充足 / 測定値の適用範囲 / 反証の広さ / 内容同等性 /
残余候補の順位付け / 訂正の追随漏れ）は 6 系統すべて掃討・終端した。
