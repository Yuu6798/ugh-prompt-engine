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

## 追記: PR #328 Codex レビュー第2巡3件（P1×1・P2×2、いずれも採用）対応 —
## fail-closed 強化 + fresh checkout 再現実測の再確認（2026-08-27、Claude 完結ルート）

第1巡で新設した `speaker_map_builder.py`/`validate_speaker_map_manifest()`
に対し、第2巡で以下3件の穴が指摘された（いずれも採用・Fable 確定方針）。

- **指摘4（P1）**: `speaker_map_builder.py` の `--out` が `--ritsu-emb`/
  `--user-emb` と同一実体（symlink 経由の alias 含む）の場合、無条件
  `write_bytes()` が検証済み入力 emb を読み取り後に破壊していた。
- **指摘5（P2）**: manifest の `w_ritsu_expr`/`w_user_expr`（builder が
  実際に評価して合成に使う実効値）を `validate_speaker_map_manifest()`
  が検証しておらず、`*_float32_hex`/`*_repr` さえ正しければ expr 改竄を
  素通りさせていた。
- **指摘6（P2）**: README.md 冒頭の現在地バナーと revision chain が
  design_revision 0.4 のまま残置されていた（別項、本ファイル対象外）。

### 対応

- **指摘4**: `_check_out_does_not_alias_inputs()` を新設し、書き込み前に
  `--out`/`--ritsu-emb`/`--user-emb` の3パスを `Path.resolve()` で解決・
  比較、同一実体なら fail-closed で拒否（書き込みしない）するよう
  `main()` へ配線した。負例テスト（同一パス2系統・symlink alias 2系統、
  計4件）を `tests/test_speaker_map_builder.py` へ追加。
- **指摘5**: eval() を使わない閉じた文法パーサ
  `run9_schema._evaluate_closed_weight_expr()`（許容形式は (a) 10進小数
  リテラル `'0.75'` 等の `float()` 直パース、(b) 単純分数 `'A/B'`/`'A.0/B.0'`
  の分子・分母 `float()` パース除算、の2形式のみ——それ以外は拒否）を
  新設し、`validate_speaker_map_manifest()` へ expr 評価結果の float32
  hex/repr が coords_raw 由来の再導出重みと厳密一致することを強制する
  検証を追加した。`speaker_map_builder.py` 側の eval() 呼び出しも同じ
  共有パーサへ置き換えた（builder が既存の依存方向どおり `run9_schema`
  を import する側のため、パーサ本体は `run9_schema.py` に置き、循環
  import は発生しない）。負例テスト（expr 改変で hex/repr は正しいまま
  → reject、許容外形式 `'0.5+0.25'` → reject）を
  `tests/test_run9_contract.py` へ追加。
- **指摘6**: README.md 冒頭バナー・revision chain を design_revision 0.5
  へ更新（別項）。

### builder 再現実測の再確認（両 founder、workdir 現存の入力 emb に対して実行）

指摘4・5対応で `speaker_map_builder.py` の実バイトが変わったため
（合成ロジック自体は不変）、builder 再現実測を再実行し、両 founder の
出力 sha256 が manifest pin 値と不変のまま一致することを再確認した。

入力: `<session workdir（repo外）>/reexport_out/onnx_gate_40000/
s5_run6_acoustic_v1.{ritsu,user}.emb`（上記追記と同一ファイル。実測
sha256 は `ce4b87b99ac8aa7de7857feba6ca163d4ccf76a27f8fce2ac51740c2bb7b3e4c`/
`588913b74d6c16e01f4f33223698cd165ac686012e7d878475a3799ccee8bde0` で
pin 値と完全一致することを再確認済み）。

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

**判定: PASS**（両 founder とも変わらず一致——`reproduced: true`。合成
ロジック自体・重みの実効値・出力 embedding は指摘4・5の対応によって一切
変化しないことの直接証拠）。alias 拒否経路（`--out` を `--ritsu-emb` と
同一パスに指定・`--out` を `--user-emb` への symlink に指定の2系統）も
実測確認済み——いずれも非ゼロ終了し、入力 emb の実バイトは無傷のまま
残った（書き込みが実行されなかったことの直接証拠）。

### manifest/contract への反映

- `inputs/speaker_map_manifest.json`: `builder_provenance.builder_sha256`
  を `speaker_map_builder.py` の新しい実バイト sha256
  （`2a10a6d6db4d180c8d27d0c29d521247b19cae1e5519a3029dc5c60d71c7b248`）へ
  更新。既存の founder 実測値（合成 embedding sha256・render sha256・
  秒数・`pre_pin_verification_summary` 6点・重み式そのもの）は一切変更
  していない。
- `RUN9_CONTRACT.yaml` `expected_speaker_map_sha`: manifest 実バイトが
  変わったため第3世代へ repin（
  `ab2a98e99320bc4e93cab48c002b3c3e6546a371e6a390cd70b54ce026c6962d`。
  旧値 = 第2世代、`3057e5bb8a1b0da1834315b953477ead98b2ab401404d4f76262
  bd93689070b0` は履歴として contract コメントに保持）。
- `run9_schema.py`: `_evaluate_closed_weight_expr()` を新設し、
  `validate_speaker_map_manifest()` の `renormalized_runtime_weights`
  検証へ expr 評価一致の強制を追加。
- `speaker_map_builder.py`: `_check_out_does_not_alias_inputs()` を新設
  し `main()` へ配線、`synthesize()` 内の重み評価を共有パーサ呼び出しへ
  置き換え。

## 追記: PR #328 Codex レビュー第3巡2件（P2×2、いずれも採用）対応 —
## atomic write 化 + gate_synth.py provenance cross-check 追加 +
## fresh checkout 再現実測の再確認（2026-08-27、Claude 完結ルート）

第2巡対応後の `speaker_map_builder.py`/`run9_schema.py` に対し、第3巡で
以下2件の穴が指摘された（いずれも採用・Fable 確定方針）。

- **指摘7（P2）**: `speaker_map_builder.py` の `--out` へ `Path.
  write_bytes()` を直接呼んでおり、既存の正当な emb がある状態で書き込み
  が中断すると truncate/partial 出力が残るおそれがあった（非 atomic
  write）。
- **指摘8（P2）**: `run9_schema.py` の `repo_state.gate_synth_py_sha256`
  は 64hex 形式のみ検証されており、実ファイル・execution profile の
  render_code pin のどちらとも照合していなかった——smoke WAV の
  provenance が実在しないコードへ帰属され得る穴があった。

### 対応

- **指摘7**: 同一ディレクトリ内の一意な staging ファイルへ書き、fsync で
  durable にしてから `os.replace()` で atomic 置換する
  `_atomic_write_bytes()` を新設し、`main()` の `write_bytes()` 直接呼び
  出しを置き換えた。run9 系は `src/svp_rpe/utils/atomic_io`（svp_rpe
  パッケージ）を import しない独立構成のため（本ファイル冒頭・
  `speaker_map_builder.py` docstring 参照）、同型の最小実装を builder 内へ
  自足させた（`practice_split_builder.py` と同じ独立実装の前例踏襲）。
  failure-injection テスト（`os.replace()` 直前・staging 書き込み中の
  双方に例外を注入し、いずれも既存の旧出力バイトが無傷で残ることを確認）
  + 正常系（atomic 置換後の bytes 一致・親ディレクトリ自動作成・staging
  ファイルの cleanup）+ staging ファイルが `--ritsu-emb`/`--user-emb` の
  いずれとも別実体であること（alias 拒否チェックが staging 書き込みでは
  迂回されないことの直接証拠）を `tests/test_speaker_map_builder.py` へ
  追加した。alias 拒否チェック（`_check_out_does_not_alias_inputs()`、
  第2巡指摘4）は `main()` 内で `_atomic_write_bytes()` 呼び出しの**前**に
  実行される順序を維持しており、staging ファイルの書き込み自体が
  alias 判定を迂回する経路は存在しない。
- **指摘8**: `load_pinned_speaker_map_manifest()` の cross-check (l) として
  以下2点を追加した: (i) `voice_genesis/foundry/s1_gate/gate_synth.py`
  （`GATE_SYNTH_PY_REFERENCE_PATH`、テスト用に `gate_synth_py_path` で
  上書き可能）の実バイト sha256 を実計算し `repo_state.
  gate_synth_py_sha256` と一致することを強制、(ii) `load_pinned_
  execution_profile_manifest()`（同じ `contract` 経由で execution_
  profile_sha pin の全検証込みで読む——直接 `json.load()` はしない）で
  読んだ `additional_measurements.render_code_commit` が `MEASURED` の
  とき、その `file_sha256` が `repo_state.gate_synth_py_sha256` と
  cross-manifest で一致することを強制。負例テスト（manifest 側 sha 改竄→
  拒否、実ファイル相違〔オーバーライドで偽ファイル注入〕→拒否、
  execution profile との cross-manifest 不一致→拒否〔(i) は通過させつつ
  (ii) のみを狙い撃ちする構成〕、オーバーライドパス欠落→拒否）を
  `tests/test_run9_contract.py` へ追加した。

### builder 再現実測の再確認（両 founder、workdir 現存の入力 emb に対して実行）

指摘7対応で `speaker_map_builder.py` の実バイトが変わったため（合成
ロジック自体は不変——`_atomic_write_bytes()` は `--out` 指定時の書き込み
経路のみに関与し、`synthesize()` の演算には一切触れない）、builder
再現実測を再実行し、両 founder の出力 sha256 が manifest pin 値と不変の
まま一致することを再確認した（指摘8は `run9_schema.py`/`tests/` のみの
変更で `speaker_map_builder.py` の実バイトには影響しない）。

入力: `<session workdir（repo外）>/reexport_out/onnx_gate_40000/
s5_run6_acoustic_v1.{ritsu,user}.emb`（上記追記と同一ファイル。実測
sha256 は `ce4b87b99ac8aa7de7857feba6ca163d4ccf76a27f8fce2ac51740c2bb7b3e4c`/
`588913b74d6c16e01f4f33223698cd165ac686012e7d878475a3799ccee8bde0` で
pin 値と完全一致することを再確認済み）。

```
$ python3 speaker_map_builder.py --founder R9F-01 \
    --ritsu-emb <session workdir（repo外）>/reexport_out/onnx_gate_40000/s5_run6_acoustic_v1.ritsu.emb \
    --user-emb  <session workdir（repo外）>/reexport_out/onnx_gate_40000/s5_run6_acoustic_v1.user.emb \
    --out <session workdir（repo外）>/repro4/r9f01.emb
$ python3 speaker_map_builder.py --founder R9F-02 \
    --ritsu-emb <session workdir（repo外）>/reexport_out/onnx_gate_40000/s5_run6_acoustic_v1.ritsu.emb \
    --user-emb  <session workdir（repo外）>/reexport_out/onnx_gate_40000/s5_run6_acoustic_v1.user.emb \
    --out <session workdir（repo外）>/repro4/r9f02.emb
```

| founder | w_ritsu_expr | w_user_expr | 再構築した out_sha256 | manifest pin (`synthesized_embedding.sha256`) | 一致 |
|---|---|---|---|---|---|
| R9F-01 | `0.75` | `0.25` | `fc7b73fd98ef77f7caeba44761bdfe2933228cd9869bc6b27131230dade6e1e9` | `fc7b73fd98ef77f7caeba44761bdfe2933228cd9869bc6b27131230dade6e1e9` | 一致 |
| R9F-02 | `1.0/3.0` | `2.0/3.0` | `0a681a2c419295c739f6040316412e1cc5b6d16ee496e7f58ee36c45b425c2a1` | `0a681a2c419295c739f6040316412e1cc5b6d16ee496e7f58ee36c45b425c2a1` | 一致 |

**判定: PASS**（両 founder とも変わらず一致——`reproduced: true`、
今回も `--out` 指定で atomic 置換された出力ファイルの実バイト sha256 が
上記と完全一致することを `sha256sum` で追加確認済み。合成ロジック自体・
重みの実効値・出力 embedding は指摘7・8の対応によって一切変化しない
ことの直接証拠）。

### manifest/contract への反映

- `inputs/speaker_map_manifest.json`: `builder_provenance.builder_sha256`
  を `speaker_map_builder.py` の新しい実バイト sha256
  （`4d23893e6783e8f457b8174a145141de46ba0893e0e4f79b0f704612935cc717`）へ
  更新。既存の founder 実測値（合成 embedding sha256・render sha256・
  秒数・`pre_pin_verification_summary` 6点・重み式そのもの）は一切変更
  していない。
- `RUN9_CONTRACT.yaml` `expected_speaker_map_sha`: manifest 実バイトが
  変わったため第4世代へ repin（
  `6a6d6e2fe5737bcc2847c66e9420e42c818a682630feb140860951195fd227cd`。
  旧値 = 第3世代、`ab2a98e99320bc4e93cab48c002b3c3e6546a371e6a390cd70b54
  ce026c6962d` は履歴として contract コメントに保持）。
- `run9_schema.py`: `load_pinned_speaker_map_manifest()` に cross-check
  (l)（gate_synth.py 実ファイル + execution_profile_manifest.json との
  cross-manifest 照合）を新設し、`gate_synth_py_path` テスト用オーバー
  ライド引数を追加。`validate_speaker_map_manifest()`/`load_pinned_
  speaker_map_manifest()` の docstring へ (l)/(m) を追記。
- `speaker_map_builder.py`: `_atomic_write_bytes()` を新設し `main()` の
  `write_bytes()` 直接呼び出しを置き換え。

## 追記: PR #328 Codex レビュー第4巡指摘9（P1、採用）対応 —
## canonical loader 経由化 + builder 自己照合の新設（2026-08-27、Claude 完結ルート）

第3巡対応後の `speaker_map_builder.py` に対し、第4巡で以下1件（P1）が
指摘された（採用・Fable 確定方針）。

- **指摘9（P1）**: builder CLI は manifest の**内部構造検証のみ**
  （`load_local_speaker_map_manifest()` が `inputs/speaker_map_manifest.
  json` を直接 `json.load()`）で完結しており、(i) manifest 実バイトが
  `RUN9_CONTRACT.yaml` の `expected_speaker_map_sha` pin と一致するか、
  (ii) 実行中の builder 自身が `builder_provenance.builder_sha256` と
  一致するか、のいずれも確認せずに同じ untrusted ファイル内の期待値
  どうしを比較して `reproduced: true` を報告し得た——改変された
  manifest + 入力 + 期待値の組で偽の再現成功が印字される穴があった。

### 対応

builder の manifest 読み込みを新設 `load_canonical_speaker_map_manifest()`
（`speaker_map_builder.py`）経由へ置き換えた。同関数は
`run9_schema.load_run9_contract_from_yaml_path()`/`load_run9_identity_
domain()`/`load_rights_manifest_json()` で contract/domain/rights_
manifest を正典パスから厳密 parse で読み、`run9_schema.load_pinned_
speaker_map_manifest()`（`expected_speaker_map_sha` pin の唯一の正規
消費経路——manifest 実バイト pin 一致・発行済み Founder Genome・
reexport_manifest・execution_profile_manifest・gate_synth.py 実ファイル
との全 cross-check を fail-closed で強制する）へ渡す。manifest への直接
`json.load()` 経路（旧 `load_local_speaker_map_manifest()`）は builder
から削除した。`synthesize()` の `manifest=None`（CLI 既定経路）は本関数を
呼ぶ——`main()` 自体への変更は不要だった（例外伝播経路が既に
`m.Run9ValidationError` を捕捉していたため）。

さらに builder 自身の自己照合を追加した: `load_pinned_speaker_map_
manifest()` の cross-check (j) は `builder_provenance.repo_relative_path`
を repo-containment guard 経由で解決した実ファイルの sha256 を照合する
が、これに加えて **実際に実行中のこの builder ファイル**（既定
`Path(__file__).resolve()`、`running_builder_path` はテスト専用の
override 引数）の実バイト sha256 を manifest の `builder_provenance.
builder_sha256` と直接照合し、不一致なら synthesis 前に fail-closed で
拒否する（「pin 済み builder 以外での再現主張」を構造的に禁止する）。

**鶏卵性の解消手順**（Fable 確定方針の指示どおり）: builder のコード
確定 → 実バイト sha256 実測（`34b2106b558cb3407dc6159d939b588f6c190f56
039710c04f6a11fb072f30ba`）→ `inputs/speaker_map_manifest.json`
`builder_provenance.builder_sha256` 更新 → manifest 実バイト sha256 実測
（`43e68fde685d55a190e6fab0caa18381bdc2e31456558287f2b0d176df2c3167`）→
`RUN9_CONTRACT.yaml` `expected_speaker_map_sha` 第5世代 repin、の順で
直列に実施し、`load_canonical_speaker_map_manifest()`（自己照合込み）が
実 repo pin 済みデータに対して例外なく通過することを実測確認した。

負例テスト（`tests/test_speaker_map_builder.py` へ追加）:

- (a) manifest 改変（内部整合は保ったまま、実ファイル末尾へ改行1byte
  追加）→ `manifest_path` override 経由で渡すと、改変後の実バイト
  sha256 が contract pin と食い違うため fail-closed で拒否される
  （`test_load_canonical_speaker_map_manifest_tampered_manifest_
  contract_pin_mismatch_rejected`）。
- (b) builder バイト改変（実ファイルのコピーへ1byte追加）→
  `running_builder_path` override 経由で渡すと、manifest/contract 側は
  本物のまま（cross-check (j) は通過する）にもかかわらず、自己照合が
  fail-closed で拒否する（`test_load_canonical_speaker_map_manifest_
  builder_self_check_tampered_rejected`）——cross-check (j) と自己照合が
  独立した防御であることの直接証拠。
- (c) 正常系: `load_canonical_speaker_map_manifest()`/`synthesize()` の
  `manifest=None` 既定経路が、実 repo pin 済み manifest に対して例外なく
  canonical 経路を完走すること（`test_load_canonical_speaker_map_
  manifest_happy_path_real_repo_data`/`test_synthesize_default_manifest_
  none_uses_canonical_loader_real_repo_data`）。

### builder 再現実測（両 founder）— 新 canonical CLI 経路での再実行

本対応は `synthesize()` の合成ロジック（`w_r * ritsu_vec + w_u *
user_vec` の式・演算順序・dtype 処理）を一切変更していない——変更対象は
manifest 取得経路（`load_local_speaker_map_manifest()` →
`load_canonical_speaker_map_manifest()`）と builder 自己照合の新設のみ。

第5世代 repin（builder 確定 → builder sha 実測 → manifest 更新 →
contract repin の順で鶏卵性を解消）後、workdir 現存の実 ritsu/user emb
（`reexport_out/onnx_gate_40000/s5_run6_acoustic_v1.{ritsu,user}.emb`、
入力 sha256 = `ce4b87b9...`/`588913b7...`、reexport manifest pin と一致）
に対し、**新 canonical CLI 経路（contract load → 全 cross-check + builder
自己照合 → 合成）で両 founder の再現実測を再実行し、いずれも
`reproduced: true`（出力 sha256 が manifest pin `fc7b73fd...`/
`0a681a2c...` と完全一致・exit 0）を確認した**。canonical loader の統合
動作（全 cross-check + 自己照合）は実 repo pin 済み manifest に対する
自動テストでも例外なく通過することを確認済み（上記「対応」節）。

### manifest/contract への反映

- `inputs/speaker_map_manifest.json`: `builder_provenance.builder_sha256`
  を `speaker_map_builder.py` の新しい実バイト sha256
  （`34b2106b558cb3407dc6159d939b588f6c190f56039710c04f6a11fb072f30ba`）へ
  更新。既存の founder 実測値（合成 embedding sha256・render sha256・
  秒数・`pre_pin_verification_summary` 6点・重み式そのもの）は一切変更
  していない。
- `RUN9_CONTRACT.yaml` `expected_speaker_map_sha`: manifest 実バイトが
  変わったため第5世代へ repin（
  `43e68fde685d55a190e6fab0caa18381bdc2e31456558287f2b0d176df2c3167`。
  旧値 = 第4世代、`6a6d6e2fe5737bcc2847c66e9420e42c818a682630feb140860
  951195fd227cd` は履歴として contract コメントに保持）。
- `speaker_map_builder.py`: `load_local_speaker_map_manifest()` を削除し
  `load_canonical_speaker_map_manifest()`（contract/domain/rights_
  manifest 経由の全 cross-check + builder 自己照合）を新設。
  `synthesize()` の `manifest=None` 既定経路をこの新関数へ切り替え。
  `run9_schema.py` への変更なし（cross-check (j) は既存実装を再利用）。
  manifest（`builder_provenance` 更新）と contract（第5世代 repin）は
  本節冒頭に記録のとおり変更済み——schema/cross-check の追加変更はない。

## 逸脱・停止事由

**第1〜3巡分**: なし。検証6点すべて PASS、repo ファイル変更ゼロ、
`gate_synth.py` は read-only 実行のみ。上記追記の builder 再現実測も両
founder とも PASS。第2巡対応後の再確認実測も両 founder とも PASS（出力
sha256 不変）。第3巡対応後の再確認実測も両 founder とも PASS（出力
sha256 不変）。

**第4巡分（指摘9）**: なし。実装エージェントが一時「実 emb 不在のため
再現実測を再実行できない」と報告したが、これは探索誤りであり（実 emb は
session workdir に現存）、最終検分時に新 canonical CLI 経路で両 founder の
再現実測を再実行し `reproduced: true` を確認済み（上記節参照）。合成
ロジック自体・両 founder の実測出力 sha256 は本対応で変更していない。
ruff/pytest は全件 PASS。
