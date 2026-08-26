# RUN9-L0-HARNESS-1 Provisioning Record

（起草: 2026-08-26、Claude 完結ルート — User 裁定「必要な素材はドライブに
あるのでClaudeで完結できるはずだ。Claudeルートで作成して」（2026-08-26、
`scratchpad/run9_user_adjudication_pin2.md` 末尾）に基づく。Design Memo =
RUN9-L0-HARNESS-1。**コミット・push は本セッションで実施していない**——
本記録・`inputs/dependency_pins_manifest.json`・`RUN9_CONTRACT.yaml`・
`README.md`・`run9_schema.py`・`tests/test_run9_contract.py` の変更は
working tree 上の変更として残す）。

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
`3392656474b4538e9ed05bfda0d57bc7845bcca3cfa20ffaacdaa94b5fb695e1` で
PINNED 化した。`run9_schema.py` に
`validate_dependency_pins_manifest()` / `DEPENDENCY_PINS_MANIFEST_REQUIRED_KEYS`
/ `load_pinned_dependency_pins_manifest()`（probe/seed_policy と同型の
4段構成 + `backbone_runtime_bundle.json` との cross-check）を追加した。
`execution_profile_sha` は §3/§4 の理由により PENDING を維持する。

`gate_state()` は依然 `BLOCKED`（pre-run PENDING 9欄）。

---

## 6. テスト・lint

- `ruff check .` — pass
- `python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests/ tests/discipline/ -q`
  — 1742 passed, 1 failed（唯一の失敗は §7 参照、本 harness の変更と無関係）

---

## 7. 逸脱・ブロッカー

1. **acoustic export companions 未取得**（§1-4）— 最大の未解決ブロッカー。
   HARNESS-2 以降で再調達方法（別 Drive フォルダの探索、または export 再
   実行の User 裁定）を要する。
2. **smoke render / budget estimate が BLOCKED**（§3/§4）— (1) に従属。
3. **pjs/user speaker embedding が未 pin**（§1-4）— 候補 sha256 は記録
   済みだが pin 化しておらず、User 裁定を要する（af0 embedding の写像
   方式設計とあわせて後続 Memo の対象）。
4. **pre-existing 環境ドリフト（本 harness の作業と無関係）**:
   `test_pin2r2_fix2_adjudication_source_body_byte_identical_to_scratchpad_origin`
   が本セッション開始時点（onnxruntime 導入前）から既に fail していた。
   原因は `scratchpad/run9_user_adjudication_pin2.md` が本 Memo の準備
   として「追加 User 裁定（2026-08-26、HARNESS 前提）」節を追記された
   ことで、PIN-2 実装時に repo へ収載した
   `USER_ADJUDICATION_20260825_PIN2_LEARNING_BUDGET.txt` の内容と
   scratchpad 原本が乖離したため。本 Memo の Scope OUT
   （既存 inputs JSON・PIN-2 領域）のため修正していない——User/Fable へ
   引き継ぎ事項として報告する。
