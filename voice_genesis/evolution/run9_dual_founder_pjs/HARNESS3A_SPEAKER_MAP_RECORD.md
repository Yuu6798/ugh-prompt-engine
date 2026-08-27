# RUN9-L0-HARNESS-3a Speaker Map Record

（起草: 2026-08-26、Claude 完結ルート — User 裁定「RUN9 User裁定 — AF0
runtime mapping」（2026-08-26、repo 内収載
[`USER_ADJUDICATION_20260826_AF0_RUNTIME_MAPPING.txt`](./USER_ADJUDICATION_20260826_AF0_RUNTIME_MAPPING.txt)）
に基づく方式Aの speaker map manifest 合成 embedding 実測記録。Design
Memo = DESIGN_RUN9_REVISION_0.5.md。HARNESS1_PROVISION_RECORD.md /
HARNESS2_REEXPORT_SMOKE_RECORD.md と同格の記録文書。

workdir（repo 外、session scratchpad）: `<session workdir（repo外）>/h3a/`
（合成スクリプト・合成 emb・render 出力を隔離。実資産バイナリは一切 repo
にコミットしていない——`git status --porcelain` で repo 側の変更ゼロを
確認済み。本実行フェーズ自体は repo ファイルを一切変更していない。本記録
文書とその後 repo へ反映する manifest/contract/schema/test 群の変更は、
本記録が repo 収載される PR そのものが担う）。`gate_synth.py` は
read-only CLI 実行のみ（編集していない。実行時 sha256 =
`a7404da3b7ea53b94b8d0b694552610e852af2d25d88f7b5d497b58fd30f7894`、
HARNESS-2 実測と同一値）。

---

## 総合判定

**検証6点すべて PASS。FAIL なし。**

| # | 検証項目 | 判定 |
|---|---|---|
| 1 | 入力 hash 照合 | PASS |
| 2 | 合成 + 384-dim float32 有限性 | PASS |
| 3 | byte 決定論 | PASS |
| 4 | 二体相異 | PASS |
| 5 | smoke render 成立 | PASS |
| 6 | render replay 決定論 | PASS |

---

## 前提: Founder coords（`voice_genesis/evolution/run9_dual_founder_pjs/founders/*.json`、Read のみ）

| founder | af0 | ritsu | user | profile_label |
|---|---|---|---|---|
| R9F-01 | 0.6 | 0.3 | 0.1 | AF0_DOMINANT |
| R9F-02 | 0.1 | 0.3 | 0.6 | USER_DOMINANT |

再正規化（`ritsu / (ritsu+user)`, `user / (ritsu+user)`）は User 裁定の
R9F-01 = ritsu 0.75 / user 0.25、R9F-02 = ritsu 1/3 / user 2/3 と一致する
（0.3/0.4=0.75, 0.1/0.4=0.25 / 0.3/0.9=1/3, 0.6/0.9=2/3）。

## 検証1: 入力 hash 照合

対象: `<session workdir（repo外）>/reexport_out/onnx_gate_40000/` の
`s5_run6_acoustic_v1.ritsu.emb` / `s5_run6_acoustic_v1.user.emb`。
pin 参照元: repo
`voice_genesis/evolution/run9_dual_founder_pjs/inputs/reexport_manifest.json`
（Read のみ、`artifacts.ritsu_emb.sha256_run1` / `artifacts.user_emb.sha256_run1`）。

```
$ sha256sum s5_run6_acoustic_v1.ritsu.emb s5_run6_acoustic_v1.user.emb
ce4b87b99ac8aa7de7857feba6ca163d4ccf76a27f8fce2ac51740c2bb7b3e4c  s5_run6_acoustic_v1.ritsu.emb
588913b74d6c16e01f4f33223698cd165ac686012e7d878475a3799ccee8bde0  s5_run6_acoustic_v1.user.emb
```

| ファイル | 実測 sha256 | manifest pin (sha256_run1) | 一致 |
|---|---|---|---|
| ritsu.emb | `ce4b87b9...b7b3e4c` | `ce4b87b9...b7b3e4c` | 一致 |
| user.emb | `588913b7...9ccee8bde0` | `588913b7...9ccee8bde0` | 一致 |

**判定: PASS**（fail-closed 分岐に入らず続行）。

## 検証2: 合成 + 384-dim float32 有限性

合成スクリプト: `<session workdir（repo外）>/h3a/synth_speaker_map.py`
（新規、workdir 限定。`synth = (w_r * ritsu_vec + w_u * user_vec)` を
`ritsu_vec`/`user_vec` = `np.frombuffer(bytes, dtype=np.float32)`、
`w_r`/`w_u` = `np.float32(...)` として全 float32 で計算。L2正規化・摂動・
ランダム成分・重み調整なし）。

コマンド逐語（R9F-01、独立プロセス run1）:
```
python3 h3a/synth_speaker_map.py \
  --ritsu-emb reexport_out/onnx_gate_40000/s5_run6_acoustic_v1.ritsu.emb \
  --user-emb reexport_out/onnx_gate_40000/s5_run6_acoustic_v1.user.emb \
  --w-ritsu "0.75" --w-user "0.25" \
  --out h3a/synth_r9f01_run1.emb
```
コマンド逐語（R9F-02、独立プロセス run1、`--w-ritsu`/`--w-user` のみ差替）:
```
  --w-ritsu "1.0/3.0" --w-user "2.0/3.0" \
  --out h3a/synth_r9f02_run1.emb
```

| founder | w_ritsu (float32 値) | w_ritsu hex | w_user (float32 値) | w_user hex | dim | dtype | isfinite全数 | bytes | sha256 |
|---|---|---|---|---|---|---|---|---|---|
| R9F-01 | 0.75 | `3f400000` | 0.25 | `3e800000` | 384 | float32 | True | 1536 | `fc7b73fd...dade6e1e9` |
| R9F-02 | 0.3333333432674408 | `3eaaaaab` | 0.6666666865348816 | `3f2aaaab` | 384 | float32 | True | 1536 | `0a681a2c...45b425c2a1` |

`np.float32(1.0/3.0)` / `np.float32(2.0/3.0)` の float32 実バイト表現
（big-endian hex）= `3eaaaaab` / `3f2aaaab`（Python float 表示は
`0.3333333432674408` / `0.6666666865348816`）。

**判定: PASS**（両 founder とも dim==384 / dtype==float32 / isfinite 全数
True / bytes==1536 を満たす）。

## 検証3: byte 決定論（独立2回実行）

各 founder について合成スクリプトを別プロセス・別出力ファイルで独立に2回実行。

```
$ sha256sum h3a/synth_r9f01_run1.emb h3a/synth_r9f01_run2.emb \
            h3a/synth_r9f02_run1.emb h3a/synth_r9f02_run2.emb
fc7b73fd98ef77f7caeba44761bdfe2933228cd9869bc6b27131230dade6e1e9  synth_r9f01_run1.emb
fc7b73fd98ef77f7caeba44761bdfe2933228cd9869bc6b27131230dade6e1e9  synth_r9f01_run2.emb
0a681a2c419295c739f6040316412e1cc5b6d16ee496e7f58ee36c45b425c2a1  synth_r9f02_run1.emb
0a681a2c419295c739f6040316412e1cc5b6d16ee496e7f58ee36c45b425c2a1  synth_r9f02_run2.emb
```

**判定: PASS**（R9F-01: run1==run2、R9F-02: run1==run2、いずれも byte 完全一致）。

## 検証4: 二体相異

R9F-01 sha256 = `fc7b73fd98ef77f7caeba44761bdfe2933228cd9869bc6b27131230dade6e1e9`
R9F-02 sha256 = `0a681a2c419295c739f6040316412e1cc5b6d16ee496e7f58ee36c45b425c2a1`

**判定: PASS**（両者は相異）。

## 検証5・6: smoke render 成立 + render replay 決定論（計4 render）

供給方式: `reexport_out/onnx_gate_40000/` の隔離コピーを作成
（`h3a/acoustic_r9f01/`、`h3a/acoustic_r9f02/`、`cp -a` によるフルコピー。
原本 `reexport_out/` は非接触）。各コピー内の
`s5_run6_acoustic_v1.ritsu.emb` を対応する founder の合成 emb で置換
（配置後に sha256 で置換結果を確認済み、下表）。それ以外のファイル
（acoustic.onnx / dsconfig.yaml / phonemes.json 等）はコピーのまま無変更
（`s5_run6_acoustic_v1.onnx` の sha256 を置換後に再確認し原本と一致 =
`cdbd779c...c1d6687b`、他ファイル不変を確認）。

| founder | 配置した emb sha256 | 配置後の acoustic_r9f0X/s5_run6_acoustic_v1.ritsu.emb 実測 sha256 | 一致 |
|---|---|---|---|
| R9F-01 | `fc7b73fd...` | `fc7b73fd...` | 一致 |
| R9F-02 | `0a681a2c...` | `0a681a2c...` | 一致 |

コマンド逐語（HARNESS-2 前例と同一構成、`--speaker` は供給経路ラベルであり
実消費 emb は summary の `input_sha256.speaker_embed` で照合。system
interpreter = onnxruntime==1.29.0 側、read-only 実行）:

```
cd /home/user/ugh-prompt-engine
python3 voice_genesis/foundry/s1_gate/gate_synth.py run \
  --skip-export \
  --acoustic-dir <session workdir（repo外）>/h3a/acoustic_r9f0X \
  --canon-model-dir <session workdir（repo外）>/url/extracted_ds/NamineRitsu_DiffSinger \
  --vocoder-dir <session workdir（repo外）>/url/extracted_voc \
  --out-dir <session workdir（repo外）>/h3a/render_r9f0X_runN \
  --song sakura --notes-limit 6 \
  --speaker ritsu
```

各 founder につき同一コマンド・同一入力を独立に2回実行（run1/run2、別プロセス・別出力ディレクトリ）。計4 render。

| founder | run | wav sha256 | speaker_embed (summary.input_sha256) と合成 emb sha 一致 | total_elapsed_sec |
|---|---|---|---|---|
| R9F-01 | run1 | `bf643c4eaad6e79c7b82784684165c71a9eeaf06cd210bb4ff910d516019ff16` | 一致（`fc7b73fd...`） | 21.20077395439148 |
| R9F-01 | run2 | `bf643c4eaad6e79c7b82784684165c71a9eeaf06cd210bb4ff910d516019ff16` | 一致（`fc7b73fd...`） | 20.19456434249878 |
| R9F-02 | run1 | `381ea5a3a94b5abbe8d5d07deaecd4f3a71fdcf295a817ab7f20d484a8548b9f` | 一致（`0a681a2c...`） | 20.38052487373352 |
| R9F-02 | run2 | `381ea5a3a94b5abbe8d5d07deaecd4f3a71fdcf295a817ab7f20d484a8548b9f` | 一致（`0a681a2c...`） | 20.559069395065308 |

WAV は独立 `sha256sum` でも再確認済み（上表と完全一致）。
`wav_duration_sec` は両 founder とも `6.873106575963718`（HARNESS-2 前例と同一 — 入力ノート数固定のため妥当）。
`n_phonemes=12`, `stage3_mode=reflow_multi_speaker`, `stage3_depth=0.6`,
`stage3_steps=20` は両 founder・全4 render で共通（HARNESS-2 baseline と同一挙動）。

**`dual_encoding_diverged=True` は本 harness で新規に生じた異常ではない**
——HARNESS-2 baseline
（`<session workdir（repo外）>/smoke_render1/gate_sakura_ritsu_n6_record.json`）
と同じ値であり、own/canon token セット差に由来する既知の baseline 挙動
である。

**判定: PASS**（4 render すべて完走・WAV 生成成功。各 founder の
speaker_embed input sha が合成 emb sha と一致 = 供給経路が意図通り機能。
run1/run2 の WAV sha256 完全一致 = render replay 決定論成立）。

## 環境（実測、repo_git_head は summary から読取り）

- Python: 3.11.15 / onnxruntime: 1.29.0（HARNESS-2 と同一 system python3）
- repo_git_head（summary.input_sha256.repo_git_head、全4 render で共通）:
  `a2a39909f956b5f590edd7732f477fb33e6918fe`
  （`git -C /home/user/ugh-prompt-engine rev-parse HEAD` と一致確認済み。
  HARNESS-2 実測時点の HEAD `d7c66f8c...` から進んでいるのは別 PR マージに
  よるものであり、本 harness は repo に対し read-only）
- gate_synth_py sha256（summary.input_sha256.gate_synth_py、全4 render で
  共通）: `a7404da3b7ea53b94b8d0b694552610e852af2d25d88f7b5d497b58fd30f7894`
  （HARNESS-2 実測値と同一 = 未編集の確認）
- repo `git status --porcelain`: 空（変更ゼロ）

## 生成物（すべて workdir 限定、repo 外）

- `<session workdir（repo外）>/h3a/synth_speaker_map.py`（合成スクリプト、新規）
- `<session workdir（repo外）>/h3a/synth_r9f0{1,2}_run{1,2}.emb`（合成 emb 4本）
- `<session workdir（repo外）>/h3a/synth_r9f0{1,2}_run{1,2}.report.json`（生成レポート）
- `<session workdir（repo外）>/h3a/acoustic_r9f0{1,2}/`（隔離コピー2本、`reexport_out/` 非接触）
- `<session workdir（repo外）>/h3a/render_r9f0{1,2}_run{1,2}/`（render 出力4本、WAV + summary + record）

## repo への反映

上記実測値はすべて
[`inputs/speaker_map_manifest.json`](./inputs/speaker_map_manifest.json)
（schema `run9-speaker-map/1.0`）へ逐語収載し、`RUN9_CONTRACT.yaml`
`expected_speaker_map_sha` を同 manifest の raw byte sha256 で `PINNED`
化した（本 PR の直接の成果物）。`run9_schema.validate_speaker_map_
manifest()`/`load_pinned_speaker_map_manifest()` が manifest の全構造・
cross-check（裁定 txt 実バイト照合・両 founder の coords_raw と発行済み
Founder Genome document との一致・再正規化重みの機械再導出・
unrealized_mass 整合・入力 emb sha と `reexport_manifest.json` pin との
cross-manifest 照合・pre_pin_verification_summary 6点 PASS 整合・
禁止4項目/非主張逐語の存在・contract pin との実バイト sha256 一致）を
fail-closed で強制する——詳細は `run9_schema.py` docstring 参照。

## 追記: PR #328 Codex レビュー第1巡指摘1（P1、採用）対応 — checkout-stable
## fixture 化 + fresh checkout 再現実測（2026-08-27、Claude 完結ルート）

上記の合成スクリプト・生成 embedding は session workdir（repo 外）限定
のままだったため、fresh checkout では manifest の PINNED 出力 hash
（`fc7b73fd...`/`0a681a2c...`）を再構成・検証できない穴があった。本追記
は同スクリプトのロジック（演算式・順序・dtype 処理）を逐語移植した
checkout-stable fixture
[`speaker_map_builder.py`](./speaker_map_builder.py) を repo 内へ新設し、
manifest 側へ新設 `builder_provenance` 節
（`builder_sha256`/`logical_name`/`repo_relative_path`）として builder の
実バイト sha256 を追記した上で、`RUN9_CONTRACT.yaml`
`expected_speaker_map_sha` を第2世代へ repin した結果を記録する。

### builder 再現実測（両 founder、workdir 現存の入力 emb に対して実行）

入力: `<session workdir（repo外）>/reexport_out/onnx_gate_40000/
s5_run6_acoustic_v1.{ritsu,user}.emb`（検証1 節と同一ファイル。実測 sha256
は同節の値と完全一致することを本追記でも再確認済み）。

```
$ python3 speaker_map_builder.py --founder R9F-01 \
    --ritsu-emb <session workdir（repo外）>/reexport_out/onnx_gate_40000/s5_run6_acoustic_v1.ritsu.emb \
    --user-emb  <session workdir（repo外）>/reexport_out/onnx_gate_40000/s5_run6_acoustic_v1.user.emb
$ python3 speaker_map_builder.py --founder R9F-02 \
    --ritsu-emb <session workdir（repo外）>/reexport_out/onnx_gate_40000/s5_run6_acoustic_v1.ritsu.emb \
    --user-emb  <session workdir（repo外）>/reexport_out/onnx_gate_40000/s5_run6_acoustic_v1.user.emb
```

| founder | w_ritsu_expr | w_user_expr | 再構築した out_sha256 | manifest pin (`synthesized_embedding.sha256`) | 一致 |
|---|---|---|---|---|---|
| R9F-01 | `0.75` | `0.25` | `fc7b73fd98ef77f7caeba44761bdfe2933228cd9869bc6b27131230dade6e1e9` | `fc7b73fd98ef77f7caeba44761bdfe2933228cd9869bc6b27131230dade6e1e9` | 一致 |
| R9F-02 | `1.0/3.0` | `2.0/3.0` | `0a681a2c419295c739f6040316412e1cc5b6d16ee496e7f58ee36c45b425c2a1` | `0a681a2c419295c739f6040316412e1cc5b6d16ee496e7f58ee36c45b425c2a1` | 一致 |

**判定: PASS**（両 founder とも builder が再構築した合成 embedding の
sha256 が manifest pin 値と完全一致——`reproduced: true`。builder 自身が
`synthesize()` 内で pin 値との fail-closed 照合を行うため、上記コマンド
が非ゼロ終了しないこと自体が一致の直接証拠でもある）。fail-closed 経路
（入力 emb を意図的に入れ替える等）も実測確認済み——単体テスト
[`tests/test_speaker_map_builder.py`](./tests/test_speaker_map_builder.py)
参照（固定小ベクトルでの加重和数値検証・入力 sha 不一致時の拒否を
合成 manifest fixture で machine 検証する）。emb バイナリ自体は repo に
コミットしていない（rights 制約、上記コマンドはいずれも session workdir
限定のローカル入力に対して実行した）。

### manifest/contract への反映

- `inputs/speaker_map_manifest.json`: `builder_provenance` 節を新設
  （`builder_sha256` = `speaker_map_builder.py` の実バイト sha256、
  `logical_name` = `"speaker_map_builder"`、`repo_relative_path` =
  `"voice_genesis/evolution/run9_dual_founder_pjs/speaker_map_builder.py"`）。
  既存の founder 実測値（合成 embedding sha256・render sha256・秒数・
  `pre_pin_verification_summary` 6点）は一切変更していない。
- `RUN9_CONTRACT.yaml` `expected_speaker_map_sha`: manifest 実バイトが
  変わったため第2世代へ repin（旧値 = 第1世代、`3f34dc71b34d9fa9445
  28aba4432501d036d8c26b035a14b40938a255ea182f2` は履歴として contract
  コメントに保持）。
- `run9_schema.py`: `validate_speaker_map_manifest()` へ `builder_
  provenance` の shape 検証を追加、`load_pinned_speaker_map_manifest()`
  へ cross-check (j)（builder の実バイト sha256 照合、
  `_resolve_repo_contained_path()` 経由の repo-containment guard 込み）を
  追加。同関数へ cross-check (b) の拡張として `genome_id` 照合も追加した
  （PR #328 第1巡指摘2、P2、採用——別項）。

## 逸脱・停止事由

なし。検証6点すべて PASS、repo ファイル変更ゼロ、`gate_synth.py` は
read-only 実行のみ。上記追記の builder 再現実測も両 founder とも PASS。
