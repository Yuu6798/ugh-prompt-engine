# RUN9-L0-HARNESS-2 Re-export & Smoke Render Record

（起草: 2026-08-26、Claude 完結ルート — User 裁定「RUN9 User裁定 —
acoustic export companions / speaker embeds」（2026-08-26、repo 内収載
`USER_ADJUDICATION_20260826_HARNESS_COMPANIONS_EMBEDS.txt`）に基づく。
Design Memo = RUN9-L0-HARNESS-2。HARNESS1_PROVISION_RECORD.md と同格の
記録文書。

workdir（repo 外、session scratchpad）: `scratchpad/harness_work/`
（`reexport_out/` / `reexport_out2/` / `url/` / `smoke_render1/` /
`smoke_render2/`）。実資産バイナリは一切 repo にコミットしていない
（`git status --porcelain` で repo 側の変更ゼロを確認済み——本実行フェーズ
自体は repo ファイルを一切変更していない。本記録文書とその後 repo へ
反映する manifest/contract/schema/test 群の変更は、本記録が repo 収載
される PR そのものが担う）。

---

## 1. Step A — historical export companions の回収試行: Drive 全域 MISS 確定

User 裁定1の同一性確認条件（既存 pin と以下4値が同一 directory/archive
内で一致すること）:

- `acoustic.onnx` = `aaaff716db116cf3b78b981d4bf5fa6e6ab414988995b25ba43ddc47f0f38706`
- `dsconfig.yaml` = `a7da75f5c403bd347f108ded6ea6925df6260dae83cf72877c5b19018443899c`
- `s5_run6_acoustic_v1.phonemes.json` = `5071e1654c4572d90011a49959b97467b6bed5ecf08c203b71b9aff4b02807a8`
- `s5_run6_acoustic_v1.ritsu.emb` = `ce4b87b99ac8aa7de7857feba6ca163d4ccf76a27f8fce2ac51740c2bb7b3e4c`

RUN9-L0-HARNESS-1（PR #326）が実行した `r6_gate_materials_2026-08-20.tar.gz`
（Drive fileId `1D3R51BeseDYvFvk3voDg9oaSYvW-kxoA`、有力候補として調査で
特定済み）の全数展開・検査は、39ファイルのいずれも上記4点に該当しない
という結果を確定済みである（`HARNESS1_PROVISION_RECORD.md` §1-4、
`inputs/dependency_pins_manifest.json#acoustic_export_companions`
（本改訂前の `NOT_OBTAINED_TARBALL_MISS` 状態）参照）。同記録は
`run_execution_manifest.json`（run6 Drive フォルダ、fileId
`1J6LI1SlIGrkfb7FvUeLDs6S_y3hoGRs6`）の検査でも acoustic.onnx や speaker
embedding の hash 記録が見つからなかったことも確認済みである。

本 HARNESS-2 の実行時点で、Step A（historical `onnx_gate_40000/` 一式の
完全な実体回収）は HARNESS-1 が確定した上記 MISS 結果を再確認し、他に
新たな Drive フォルダ・archive 候補は特定されなかった——**Step A は
MISS のまま確定**（Drive 全域を通じて historical export companions
directory/archive は回収できなかった）。User 裁定2「historical実体を
回収できない場合、checkpoint からの再 export を承認する」に従い、
Step B（再export）へ進む。

---

## 2. Step B — checkpoint からの再export実行（User 裁定2 承認）

### 2-1. 入力・実行環境

- input checkpoint: RUN6 phase B 40K checkpoint
  （`6a28d744642df6535000857767c32efee2e69668b390c2e7fa6486908723306a`、
  使用直前に `RUN9_CONTRACT.yaml backbone_checkpoint_sha` pin と再照合し
  一致を確認済み）
- exporter: `openvpi/DiffSinger` commit
  `e2307b1080b00f3999702ce9017cfd75c7f862fe`（RUN9 前方宣言済み。使用直前に
  `git rev-parse HEAD` で一致を確認済み）
- export venv: torch 隔離 venv（provision.sh §6 export venv pin 準拠）。
  numpy<2 制約下で torch/lightning/onnx/onnxsim を
  `DiffSinger/requirements.txt` 経由で導入し、最後に numpy を 1.26.4 へ
  再pin（provision.sh の既知順序: requirements.txt 導入後に numpy を戻さ
  ないと 2.x へ引き上げられる）:
  ```
  python -m venv venv_export
  pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu
  pip install -r <diffsinger_repo>/DiffSinger/requirements.txt
  pip install numpy==1.26.4
  ```
- 実測環境バージョン: python 3.11.15 / numpy 1.26.4 / torch 2.13.0+cpu /
  lightning 2.3.3 / onnx 1.22.0 / onnxsim v0.7.3 / OS Ubuntu 24.04.4 LTS /
  kernel Linux 6.18.44-fc-v21 x86_64 / arch x86_64 / device CPU
  （GPU 不使用。historical run6 export 実行環境・device は未記録のため
  不明——環境差要因を断定しない）

### 2-2. export コマンド（逐語）

```
python scripts/export.py acoustic \
  --exp s5_run6_acoustic_v1 \
  --ckpt 40000 \
  --out <session workdir（repo外）>/onnx_gate_40000
```

cwd: `<diffsinger_repo clone（session workdir、repo外）>/DiffSinger`

同一 checkpoint に対し本コマンドを独立に2回実行した（`reexport_out` /
`reexport_out2`、いずれもプロセス・出力ディレクトリとも別）。

### 2-3. 独立2回実行の決定論確認（再現性チェック）

全9アーティファクト（acoustic.onnx / dsconfig.yaml /
s5_run6_acoustic_v1.phonemes.json / s5_run6_acoustic_v1.languages.json /
dictionary-ja.txt / s5_run6_acoustic_v1.ritsu.emb /
s5_run6_acoustic_v1.pjs.emb / s5_run6_acoustic_v1.user.emb /
s5_run6_acoustic_v1.d3synth.emb）が run1/run2 間で sha256 完全一致した
——本環境内では export は決定論的である（非決定論ではなく、環境差が
historical との不一致の原因である可能性を示唆する所見。断定はしない）。

| artifact | sha256 (run1 == run2) | bytes | historical pin | 一致 |
|---|---|---|---|---|
| acoustic.onnx | `cdbd779c686504cd1277bf74036e5fb334e4fcdc88ab7612f435ced7c1d6687b` | 279,777,001 | `aaaff716db116cf3b78b981d4bf5fa6e6ab414988995b25ba43ddc47f0f38706` | **不一致**（MISMATCH。捏造して合わせていない） |
| dsconfig.yaml | `a7da75f5c403bd347f108ded6ea6925df6260dae83cf72877c5b19018443899c` | 775 | 同左 | 一致（replay evidence） |
| s5_run6_acoustic_v1.phonemes.json | `5071e1654c4572d90011a49959b97467b6bed5ecf08c203b71b9aff4b02807a8` | 506 | 同左 | 一致（replay evidence） |
| s5_run6_acoustic_v1.languages.json | `a51ee3aa7dafa1905b01a8c6ed2e99ebeecad0071d786493f43effd2438b2fda` | 13 | historical pin なし | N/A |
| dictionary-ja.txt | `b8ea0d99fcf60e82319cc84b162d9e1b4d5ce1146cfa1c6291e025fbb8be14ef` | 204 | historical pin なし | N/A |
| s5_run6_acoustic_v1.ritsu.emb | `ce4b87b99ac8aa7de7857feba6ca163d4ccf76a27f8fce2ac51740c2bb7b3e4c` | 1,536 | 同左 | 一致（replay evidence） |
| s5_run6_acoustic_v1.pjs.emb | `074e09b390c207a7cf98105db549e1006d035a797d57f73e103e848bb3216015` | 1,536 | 候補値（未 pin） | 一致（replay evidence。昇格条件は未充足のまま） |
| s5_run6_acoustic_v1.user.emb | `588913b74d6c16e01f4f33223698cd165ac686012e7d878475a3799ccee8bde0` | 1,536 | 候補値（未 pin） | 一致（replay evidence。昇格条件は未充足のまま） |
| s5_run6_acoustic_v1.d3synth.emb | `10c3964c57a69edb072bd7c9aec36dc7e3b06e06469c5da60332bec793c1dc22` | 1,536 | 候補値（RUN9 対象外・参考） | 一致（replay evidence） |

acoustic.onnx の MISMATCH について: ckpt/exporter commit は pin 一致確認
済みだが、export 実行環境（torch/onnx/onnxsim 具体バージョン・GPU/CPU・
当時の OS）は historical 側で未記録のため、環境差要因を断定できない。

pjs.emb/user.emb は User 裁定3の正式 PINNED 昇格条件（同一 directory/
archive 内で歴史4 sha と同時実在の実測確認）を満たさない——Step A で
その directory/archive 自体が発見できなかったため。本 replay evidence は
昇格条件そのものではなく、候補値の信頼性を補強する傍証として記録する
（裁定3「この2値自体については既存RUN6 probe記録にも同じ値が存在する
ため、値の信頼性には独立した傍証がある」と符合する）。

詳細な実測値の一次記録は `inputs/reexport_manifest.json`
（`reexport_manifest_sha` として新規 PINNED）を正とする。

---

## 3. Smoke Render — 決定論確認（COMPLETED）

### 3-1. 入力

- acoustic dir: 再export成果物（`onnx_gate_40000/`、Step B run1 の出力）
- canon model dir: `NamineRitsu_DiffSinger`（既存 workdir 資産、
  HARNESS-1 で取得・sha 照合済み — `render_asset_ledger` 参照）
- vocoder dir: `nsf_hifigan.onnx` 展開済み（同上）
- speaker: ritsu / song: sakura（notes-limit 6、最小構成）
- 入力 sha256（`gate_synth.py` が実測記録した `input_sha256`、全
  single-read + pre-publish re-hash belt）:
  - acoustic_onnx: `cdbd779c686504cd1277bf74036e5fb334e4fcdc88ab7612f435ced7c1d6687b`
  - acoustic_phonemes_json: `5071e1654c4572d90011a49959b97467b6bed5ecf08c203b71b9aff4b02807a8`
  - acoustic_dsconfig_yaml: `a7da75f5c403bd347f108ded6ea6925df6260dae83cf72877c5b19018443899c`
  - speaker_embed (ritsu): `ce4b87b99ac8aa7de7857feba6ca163d4ccf76a27f8fce2ac51740c2bb7b3e4c`

### 3-2. コマンド（逐語、system interpreter = onnxruntime==1.29.0 側、read-only 実行）

```
python3 voice_genesis/foundry/s1_gate/gate_synth.py run \
  --skip-export \
  --acoustic-dir <workdir>/reexport_out/onnx_gate_40000 \
  --canon-model-dir <workdir>/url/extracted_ds/NamineRitsu_DiffSinger \
  --vocoder-dir <workdir>/url/extracted_voc \
  --out-dir <workdir>/smoke_render{1,2} \
  --song sakura --notes-limit 6 \
  --speaker ritsu
```

同一コマンド・同一入力を独立に2回実行した（`smoke_render1` /
`smoke_render2`、プロセス・出力ディレクトリとも別）。

### 3-3. 決定論結果（同一入力2回render のWAV byte一致）

| | render1 | render2 |
|---|---|---|
| wav sha256 | `c7e1dcdfb7139d490dc19347c21dad5f9966764182cb6ee7e0124ad8fedd379e` | `c7e1dcdfb7139d490dc19347c21dad5f9966764182cb6ee7e0124ad8fedd379e` |
| total_elapsed_sec（gate_synth.py 内部計測） | 23.482061624526978 | 24.721034049987793 |
| wav_duration_sec | 6.873106575963718 | 6.873106575963718 |

**両者の wav sha256 は完全一致**（`sha256sum` で独立に再確認済み）。
`determinism_confirmed: true` を裏付ける監査可能な証拠（出力 sha256
一致、PR #326 第6巡 Fix 15 が要求する shape 相当の実測）。

### 3-4. stage 別内訳（record1、参考）

- stage1 (score parse): 0.0121 sec
- stage2 (linguistic/dur/pitch): 1.373 sec
- stage3 (acoustic reflow diffusion, 20 steps): 10.170 sec
- stage4 (vocoder): 6.094 sec
- （上記 stage 合計 + model_load 等のオーバーヘッドで total 23.48 sec）

### 3-5. 実測秒・予算概算

- render1 total_elapsed_sec: 23.482061624526978
- render2 total_elapsed_sec: 24.721034049987793
- 平均: 24.101547837257385 秒/件
- 予算概算（616件 × 平均実測秒）: **14846.55 秒 ≈ 247.44 分 ≈ 4.12 時間**
  （616 という件数は前巡の返信・過去記録で言及されてきた基準値をそのまま
  踏襲した概算であり、本 PR で新たに確定した値ではない。内訳:
  learning/search loop 512 + birth probe 24 + C0/C1 80 = 616、固定評価分
  （birth baseline/validation/sealed holdout）は未確定のため除く——
  `HARNESS1_PROVISION_RECORD.md` §4 参照）

---

## 4. 環境値一式（`execution_profile_sha` 裁定材料——本記録自体は pin 判断を行わない）

- Python: 3.11.15（render 実行インタプリタ = system python3、venv 不使用）
- OS: Ubuntu 24.04.4 LTS
- kernel: Linux 6.18.44-fc-v21
- arch: x86_64
- onnxruntime: 1.29.0
- onnxruntime providers（実際に渡された値、`gate_synth.py:1218` 固定）:
  `["CPUExecutionProvider"]`（`onnxruntime.get_available_providers()` の
  実測全件は `["AzureExecutionProvider", "CPUExecutionProvider"]` だが、
  `gate_synth.py` はハードコードで `CPUExecutionProvider` のみ渡す）
- torch: render 側インタプリタには torch は存在しない（render は
  onnxruntime 推論のみで torch 非依存。torch は export 側 venv
  （`venv_export`）にのみ存在——バージョンは `inputs/reexport_manifest.json
  #environment_versions.torch` = `2.13.0+cpu` を参照）
- numpy (render 側): 2.4.6（provision.sh §6
  `render_numeric_stack_pin` と一致）
- scipy (render 側): 1.17.1
- soundfile (render 側): 0.14.0
- render entrypoint: `voice_genesis/foundry/s1_gate/gate_synth.py run`
  （§3-2 コマンド逐語）
- device: CPU（GPU 不使用、`CPUExecutionProvider` 固定のため構造的に
  GPU 経路は存在しない）

**`execution_profile_sha` は本記録では裁定・pin しない**（User 裁定4
のとおり、本記録は「smoke 実測を経て初めて execution_profile を裁定する」
ための実測値提供であり、本記録自体が pin 判断を行うものではない。
`RUN9_CONTRACT.yaml execution_profile_sha` の reason を「smoke 実測完了・
User 裁定待ち」へ更新済み）。

---

## 5. fail-closed 確認事項

- 使用した checkpoint sha256 は使用直前に再照合し、`RUN9_CONTRACT.yaml`
  pin（`6a28d744642df6535000857767c32efee2e69668b390c2e7fa6486908723306a`）
  と一致することを確認済み（`inputs/reexport_manifest.json` 参照）。
- DiffSinger repo は使用直前に `git rev-parse HEAD` で
  `e2307b1080b00f3999702ce9017cfd75c7f862fe` と一致することを確認済み。
- `gate_synth.py`/`scripts/export.py` 自体は read-only（CLI 実行のみ、
  編集していない）。
- 再export で得た acoustic.onnx が歴史 pin と不一致であることは正直に
  `OBTAINED_DERIVED_NEW_BYTES`（新 status）として記録し、
  `OBTAINED_VERIFIED_MATCH` を捏造して名乗らせていない
  （`run9_schema.validate_dependency_pins_manifest()` が machine 強制）。
- pjs.emb/user.emb の正式 PINNED 昇格条件（同一 directory/archive 内で
  歴史4 sha との同時実在の実測確認）が未充足であることを明示し、replay
  evidence の追記が暗黙の昇格と誤読されないよう
  `promotion_condition_unmet_note` を機械強制で必須化した。
- repo（`/home/user/ugh-prompt-engine`）側のファイルは、本記録が repo
  収載される PR 以前は一切変更していない（実行フェーズ自体は
  `git status --porcelain` で変更ゼロを確認済み）。
