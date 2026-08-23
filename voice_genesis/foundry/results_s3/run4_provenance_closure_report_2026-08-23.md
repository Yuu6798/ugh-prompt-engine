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
