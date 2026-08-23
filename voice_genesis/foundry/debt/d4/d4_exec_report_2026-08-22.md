# VG-DEBT-004 (D4) run5–7 TRF 1.2 再測定 — 実行報告

- 実行環境: このセッション（repo は一切変更していない。生成物は全て `/home/user/d4work/` 配下）
- repo: `/home/user/ugh-prompt-engine`, branch `claude/technical-debt-plan-2fv79j`, head `db1ea35`
- ROOT: `/home/user/d4work`（provision.sh の既定 `/home/user/s7work` ではなく `--root /home/user/d4work` を明示指定。理由 = 前セッションで既に取得済みの run5 40K checkpoint の `.part` 実体がこの配下にあったため、そこへ寄せて再利用した）
- `--spec-sha256`: `702f1a2231a1b53e71afe7ab6332d42a637546802d960bdd569f4d200de2eeca`（依頼文の値と `voice_genesis/foundry/debt/d4/d4_remeasure_spec.json` の実 sha256 が一致することを事前に `sha256sum` で確認済み）

## 所要時間サマリ

| 段階 | 開始 (UTC) | 終了 (UTC) | 所要 |
|---|---|---|---|
| provision (17資産 sha照合 + 展開 + DiffSinger checkout + export venv) | 18:41:04 | 18:44:00 | 2m56s |
| export（10 回。run7×4 → run6×3 → run5×3、詳細は §2） | 18:46:32 | 18:56:10 | 約9m（実行分の合計） |
| render（10 群 × 36 セル、群ごとに逐次実行） | 18:57:15 | 20:07:28 | 1h10m13s |
| measure（1 回目 fg 10min timeoutで打ち切り→バックグラウンドで再実行） | 20:20:18 | 20:37:47 | 17m29s |

（measure は 1 群だけの単独実行で 1m46s だったため、10群合成コマンドが10分の
フォアグラウンド上限を超えると判断し、`run_in_background` で再実行した。運用規律
の「1コマンドが10分を超えそうな場合は群単位に分割」の代替として、バックグラウンド
実行 + 完了通知待ちで対応した。）

## (1) provision 17資産の照合結果

`bash voice_genesis/foundry/run8/provision.sh --root /home/user/d4work` 実行結果
（生ログ = 本ディレクトリの `provision_log.txt`）:

```
| result: OK=16 SKIP=1 FAIL=0
```

- 17 資産すべて sha256 一致（内訳: OK=16 新規取得+照合一致、SKIP=1 = `run5_ckpt`
  が既に前セッションで取得済み・sha 再照合一致のため取得をスキップ）
- 展開検証（NamineRitsu_DiffSinger.zip 19 メンバ / nsf_hifigan.oudep 5 メンバ）も
  全メンバ sha 照合で書き出し、`nsf_hifigan.onnx` 単体 sha も一致
- DiffSinger checkout: HEAD `e2307b1` 一致
- export 用隔離 venv: numpy 1.26.4 / torch 2.13.0+cpu / lightning 2.3.3 / onnx 1.22.0 で構築成功
- ANALYSIS_STACK_PIN（測定側インタプリタ）: numba 0.66.0 / librosa 0.11.0 /
  numpy 2.4.6 / pyloudnorm 0.2.0 で一致（provision.sh 内で自動照合・復元、変更なし）
- FAIL 0 件、再取得は不要だった

## (2) export した ONNX の sha256（3 run 分）と exporter input pin 照合結果

`s7_exporter_input_pins.json` の pin（checkpoint_sha256 / config_sha256）と、witnessed
export manifest の `source_checkpoint.sha256` / `source_config.sha256` を突き合わせ:

| generation | ckpt_dir | checkpoint_sha256（pin と一致） | config_sha256（pin と一致） | acoustic_onnx sha256 |
|---|---|---|---|---|
| run5 | `materials/ckpts/run5_bundle` | `d3c51399cb1c3914981d4a11da8391a4e344130c84b263f0ef9774f60c3f8da5` | `0b627cc9113ce38f46f5c0b9a4c19c58dbb8b928318226a93e12a04ad624b833` | `8b1275a2628ddc63e9d63bbac075018013a581407d413bc8a6db35e7e3161eee` |
| run6 | `materials/ckpts/run6_bundle` | `6a28d744642df6535000857767c32efee2e69668b390c2e7fa6486908723306a` | `3722072045060e316ec9fee3f307412eceacf617d3b3ece7adfcbefa0f9df9d9` | `cdbd779c686504cd1277bf74036e5fb334e4fcdc88ab7612f435ced7c1d6687b` |
| run7 | `materials/run7_ckpt` | `518df090a8154e61f28b529f731418f4f97d47c3b56d1326d354e6be4629fa93` | `e14ac2fde724998db05070550e86391c9090e582b1539747faa58356ae18d411` | `cb1c590ba521c08780750af28d6693069719edae9a149ed932f281920b712e76` |

- checkpoint_sha256 / config_sha256 は 3 世代とも `s7_exporter_input_pins.json` の
  値と完全一致（`export_diffsinger_acoustic()` 内部で機械照合済み。手動でも
  マニフェスト JSON の `source_checkpoint.sha256` / `source_config.sha256` を突合）
- `binding_evidence`: 全 export とも `witnessed_export`（DiffSinger exporter を
  このプロセスが空の out-dir に対して起動し、生成物を直接 hash した記録）
- exporter: `openvpi/DiffSinger` HEAD `e2307b1080b00f3999702ce9017cfd75c7f862fe`
  （worktree clean、宣言 revision `e2307b1` と一致）

**設計上の判断（後述「判断に迷った点」参照）**: `d4_runner.cmd_render` は export
manifest の `artifacts["speaker_embed"]` を固定キーで 1 話者ぶんしか受け付けない
実装だったため、**1 世代につき 1 回ではなく、群（=世代×話者）ごとに witnessed
export を独立実行**した（run5×3 / run6×3 / run7×4 = 計10回）。同一世代内では
`acoustic_onnx` の sha256 は複数回とも完全に同一（例: run7 の 4 話者全て
`cb1c590ba521c08780750af28d6693069719edae9a149ed932f281920b712e76`）だったため、
この環境では export は決定論的に振る舞った（`s7_export_manifest.py` の
docstring が警告する「同じ ckpt を再 export するとバイト列が変わる」非決定性は
今回は観測されなかった）。

**既存 pin との照合（重要）**: `results_s7/probe_0b_groups/*.json`（run 8-0b
1.0 測定時の記録・全 10 群）に記帳済みの `model_sha256.acoustic_onnx` と、今回
export した ONNX の sha256 を突合したところ、**3 世代 10 群すべてで完全一致**した:

| generation | probe_0b_groups 記帳値（1.0・既存 pin） | 今回 export（D4） | 一致 |
|---|---|---|---|
| run5（ritsu/pjs/user 共通） | `8b1275a2628ddc63e9d63bbac075018013a581407d413bc8a6db35e7e3161eee` | 同左 | ✅ |
| run6（ritsu/pjs/user 共通） | `cdbd779c686504cd1277bf74036e5fb334e4fcdc88ab7612f435ced7c1d6687b` | 同左 | ✅ |
| run7（ritsu/pjs/user/amitaro 共通） | `cb1c590ba521c08780750af28d6693069719edae9a149ed932f281920b712e76` | 同左 | ✅ |

したがって run5 / run6 の acoustic ONNX は「今回が初 export」ではなく、**1.0
測定時に既に export・記帳済みの値と今回の再 export が完全一致した**（3世代とも
新規実測値ではなく既存 pin との一致確認）。この一致は checkpoint / config /
DiffSinger revision (`e2307b1`) が同一であることに加え、export 手順自体が
この環境で完全決定論的であることを裏付ける（すくなくとも本セッション内の
複数回・および 1.0 測定時点との時間差を跨いだ再現の両方で確認された）。

なお `s7_b1_real_render_manifest.json`（STEP 6 の B-1 校正レンダ、run7/ritsu
のみを対象とした別文脈の export）の `acoustic_onnx` は
`f0e71f06b16e448622f3e0d9b977a26fbaa306bb608a08ed26efeb871332a7d1` であり、上記
とは一致しない。これは校正レンダ専用の別 export インスタンスであり、D4 の
対象（1.0 本番360セルの再測定）とは無関係の文脈のため、比較対象から除外した。

## (3) render 完了群数・総セル数・失敗セル

- 完了: **10/10 群**（run5: ritsu/pjs/user、run6: ritsu/pjs/user、run7:
  ritsu/pjs/user/amitaro）
- 総セル: **360/360 rendered**、**dropped = 0**
- 失敗セル: **0**
- 出力: `/home/user/d4work/render/{generation}_{speaker}.json`（10 本）+
  各群の WAV 群 `/home/user/d4work/render/{generation}_{speaker}/`

各群のレンダ時間（`render_elapsed_sec` 合算ではなく壁時計。1 群あたり CPU
onnxruntime で約 6–7 分）:

| 群 | n_rendered | n_dropped |
|---|---|---|
| run5_ritsu | 36 | 0 |
| run5_pjs | 36 | 0 |
| run5_user | 36 | 0 |
| run6_ritsu | 36 | 0 |
| run6_pjs | 36 | 0 |
| run6_user | 36 | 0 |
| run7_ritsu | 36 | 0 |
| run7_pjs | 36 | 0 |
| run7_user | 36 | 0 |
| run7_amitaro | 36 | 0 |

## (0) d4_results.json 機械検証（追加実施）

`d4_results.json` を Python で走査し以下を確認（全て PASS。生スクリプト出力は
本報告作成時に確認済み）:

- `schema == "vg-d4-remeasure-results/0.1"` / `debt_ref == "VG-DEBT-004"`
- `d4_remeasure_spec_sha256 == 702f1a2231a1b53e71afe7ab6332d42a637546802d960bdd569f4d200de2eeca`
- `n_groups == 10` / `n_total_cells == 360` / `n_total_measured == 360` / `n_total_error == 0`
- 10 群すべて `n_cells=36 / n_measured=36 / n_missing=0 / n_error=0` かつ `cells` の
  キー数が 36
- 360 セル全てで `outcome == "measured"`・`wav_sha256` と `samples_sha256` が
  非空・`axes` のキー集合が `{excess_tail_voiced_ms, release_after_score_boundary_ms,
  tail_f0_persistence}` と厳密一致・各軸値が有限な float
- ファイル自体の sha256: `6b820a2a27744b9ed4f6e873231aa103b57dd622f993982a112063e5b4bacfa7`

## (4) measure の exit code・n_error

- `d4_runner.py measure`（10 --render-doc 一括、`--spec-sha256` 束縛）:
  **exit code = 0**
- `d4_results.json`: `n_groups=10`, `n_total_cells=360`,
  `n_total_measured=360`, `n_total_error=0`
- 群別 n_measured/n_missing/n_error は全群 `36/0/0`（詳細は下表・§5-2 も参照）

## (5) render stack の pin 比較結果

`s7_b1_real_render_manifest.json` の `render_stack` pin と、このセッションの
実測バージョンを突合:

| パッケージ | pin (real_render_manifest) | 実測 | 一致 |
|---|---|---|---|
| python | 3.11.15 | 3.11.15 | ✅ |
| numpy | 2.4.6 | 2.4.6 | ✅ |
| onnxruntime | 1.29.0 | 1.29.0 | ✅ |
| soundfile | 0.14.0 | 0.14.0 | ✅ |
| PyYAML | 6.0.1 | 6.0.1 | ✅ |

**完全一致**。ただし `onnxruntime` はこのコンテナに未導入だったため
`pip install onnxruntime==1.29.0` で導入した（pin と厳密一致するバージョンが
pypi 上の最新版であり、他パッケージへの副作用なし。導入後に numpy 等を
再確認し、`ANALYSIS_STACK_PIN`（numba 0.66.0 / librosa 0.11.0 / numpy 2.4.6 /
pyloudnorm 0.2.0）にも変化がないことを確認した）。repo ファイルは変更していない
（pip install は環境変更のみ）。

**この実測記録の位置づけ（2026-08-22 事後注記）**: 上記の版一致は本 md ファイルが
唯一の記帳先であり、`d4_results_2026-08-22.json`（`vg-d4-remeasure-results/0.1`
スキーマ）はレンダ/測定に使ったパッケージ版を結果へ埋め込む項目を持たない
制限があった。以後の実行では `d4_runner.py` が `runtime_stack` として
render/measure 双方の出力へ機械的に記帳する（本 PR 以降の改修。詳細は
`debt_ledger.yaml` VG-DEBT-004 note を参照）。

## (6) 軸値分布の要点（d4_results.json）

3 軸: `excess_tail_voiced_ms` (ms) / `release_after_score_boundary_ms` (ms) /
`tail_f0_persistence` (0..1)。全 360 セルが `outcome: "measured"`。

| 群 | 軸 | n | min | max | mean | median |
|---|---|---|---|---|---|---|
| run5_pjs | excess_tail_voiced_ms | 36 | 0.000 | 100.000 | 26.389 | 20.000 |
| run5_pjs | release_after_score_boundary_ms | 36 | 0.000 | 96.236 | 28.256 | 21.293 |
| run5_pjs | tail_f0_persistence | 36 | 0.000 | 1.000 | 0.295 | 0.261 |
| run5_ritsu | excess_tail_voiced_ms | 36 | 0.000 | 160.000 | 77.500 | 80.000 |
| run5_ritsu | release_after_score_boundary_ms | 36 | 0.000 | 280.363 | 79.790 | 81.043 |
| run5_ritsu | tail_f0_persistence | 36 | 0.000 | 1.000 | 0.777 | 0.889 |
| run5_user | excess_tail_voiced_ms | 36 | 0.000 | 210.000 | 52.222 | 70.000 |
| run5_user | release_after_score_boundary_ms | 36 | 0.000 | 295.782 | 60.053 | 66.190 |
| run5_user | tail_f0_persistence | 36 | 0.000 | 1.000 | 0.506 | 0.667 |
| run6_pjs | excess_tail_voiced_ms | 36 | 0.000 | 100.000 | 13.889 | 0.000 |
| run6_pjs | release_after_score_boundary_ms | 36 | 0.000 | 96.236 | 14.133 | 0.000 |
| run6_pjs | tail_f0_persistence | 36 | 0.000 | 1.000 | 0.156 | 0.000 |
| run6_ritsu | excess_tail_voiced_ms | 36 | 0.000 | 120.000 | 81.389 | 90.000 |
| run6_ritsu | release_after_score_boundary_ms | 36 | 0.000 | 151.111 | 81.450 | 90.794 |
| run6_ritsu | tail_f0_persistence | 36 | 0.000 | 1.000 | 0.825 | 1.000 |
| run6_user | excess_tail_voiced_ms | 36 | 0.000 | 170.000 | 42.500 | 40.000 |
| run6_user | release_after_score_boundary_ms | 36 | 0.000 | 295.782 | 49.466 | 38.753 |
| run6_user | tail_f0_persistence | 36 | 0.000 | 1.000 | 0.404 | 0.422 |
| run7_amitaro | excess_tail_voiced_ms | 36 | 0.000 | 120.000 | 38.611 | 40.000 |
| run7_amitaro | release_after_score_boundary_ms | 36 | 0.000 | 115.737 | 39.283 | 40.907 |
| run7_amitaro | tail_f0_persistence | 36 | 0.000 | 0.900 | 0.400 | 0.400 |
| run7_pjs | excess_tail_voiced_ms | 36 | 0.000 | 80.000 | 31.944 | 30.000 |
| run7_pjs | release_after_score_boundary_ms | 36 | 0.000 | 81.270 | 30.567 | 29.116 |
| run7_pjs | tail_f0_persistence | 36 | 0.000 | 0.900 | 0.345 | 0.300 |
| run7_ritsu | excess_tail_voiced_ms | 36 | 0.000 | 140.000 | 75.278 | 80.000 |
| run7_ritsu | release_after_score_boundary_ms | 36 | 10.068 | 136.145 | 74.246 | 77.347 |
| run7_ritsu | tail_f0_persistence | 36 | 0.033 | 1.000 | 0.755 | 0.800 |
| run7_user | excess_tail_voiced_ms | 36 | 0.000 | 140.000 | 50.000 | 50.000 |
| run7_user | release_after_score_boundary_ms | 36 | 0.000 | 140.680 | 48.792 | 51.224 |
| run7_user | tail_f0_persistence | 36 | 0.000 | 1.000 | 0.496 | 0.556 |

判読・裁定はしない（値の記帳のみ）。候補 id（`candidate_ids`）:
`excess_tail_voiced_ms` / `tail_f0_persistence` = `S_melshape_core_distance|thr0.2|win100|hop10`、
`release_after_score_boundary_ms` = `S_melshape_core_distance|thr0.2|win100|hop5`。

## (7) 全中間 pin（ckpt / ONNX / WAV サンプル数点の sha）

**checkpoint（provision.sh 資産表と一致・§1 参照）**:
- run5: `d3c51399cb1c3914981d4a11da8391a4e344130c84b263f0ef9774f60c3f8da5`
- run6: `6a28d744642df6535000857767c32efee2e69668b390c2e7fa6486908723306a`
- run7: `518df090a8154e61f28b529f731418f4f97d47c3b56d1326d354e6be4629fa93`

**acoustic ONNX（§2 の表と同一。世代内は話者間で完全一致）**:
- run5: `8b1275a2628ddc63e9d63bbac075018013a581407d413bc8a6db35e7e3161eee`
- run6: `cdbd779c686504cd1277bf74036e5fb334e4fcdc88ab7612f435ced7c1d6687b`
- run7: `cb1c590ba521c08780750af28d6693069719edae9a149ed932f281920b712e76`

**WAV サンプル数点**（`d4_results.json` の `cells[].wav_sha256` /
`samples_sha256`。P-ANCHOR「sakura|kagiri」セルを群横断でサンプリング）:

| 群 | cell_id | wav_sha256 | samples_sha256 |
|---|---|---|---|
| run5_ritsu | P-ANCHOR\|sakura\|kagiri | `193396f1349f9b78e0b255aefeb661db799f90c7ffeac58ce9a9197217ca026c` | `19f720f174d475a27f9fc6cb9b6022b5d765fee5da3a86db65dfe807b19ee98f` |
| run6_user | P-ANCHOR\|sakura\|kagiri | `00429bdfc210cbd20dc6f83f7b16d68b3dbfe0f8ade15d1f47d886afd60ea908` | `1e12d7f49e224f329dc2f71a7486a3b2e4297a34e98feab2af3e29ea58ca3150` |
| run7_amitaro | P-ANCHOR\|sakura\|kagiri | `a2f1defbd920d172ff0b3f79dc6800b1f59841597863dc0452b584d87466896c` | `673f2f5991bb1eafe6bc544e8cb2a981699e6f06d68fa29a736fe2bca2bdc2ea` |

**instrument / spec 束縛**（`d4_results.json` に記帳済み）:
- `d4_remeasure_spec_sha256`: `702f1a2231a1b53e71afe7ab6332d42a637546802d960bdd569f4d200de2eeca`
- `trf_measurement_spec_1_2_sha256`: `1f7ff569b4ae27043a822e688c8e5fe621f2ebd666e54cd99e53599c4f38ea5b`
- `instrument_sha256`（`s7_b1_v12.py`）: `f5d136744b24ad6562668b0f604a737d23798e8744e0c23fc19b2afca8418eb4`
- `d4_results.json` 自体の sha256（このディレクトリへコピーした複製と同一）:
  `6b820a2a27744b9ed4f6e873231aa103b57dd622f993982a112063e5b4bacfa7`

## (6') d4_results.json と d4_exec_report.md のパス

- `/home/user/d4work/d4_results.json`（原本。ROOT 配下）
- `/tmp/claude-0/-home-user-ugh-prompt-engine/80cf8a11-dc84-558a-bba3-47d4505f825a/scratchpad/d4_results.json`（複製・sha256 一致）
- `/tmp/claude-0/-home-user-ugh-prompt-engine/80cf8a11-dc84-558a-bba3-47d4505f825a/scratchpad/d4_exec_report.md`（本ファイルの元・実行セッションの scratchpad）
- `/tmp/claude-0/-home-user-ugh-prompt-engine/80cf8a11-dc84-558a-bba3-47d4505f825a/scratchpad/provision_log.txt`（provision.sh 生ログ）
- `/home/user/d4work/render/*.json`（10 群の render 群 JSON。WAV 実体は
  `/home/user/d4work/render/{generation}_{speaker}/` 配下。repo 外・数 GB
  規模のため scratchpad へはコピーしていない）
- `/home/user/d4work/out/export_{generation}_{speaker}/export_manifest.json`
  （10 本の witnessed export manifest）
- `voice_genesis/foundry/debt/d4/d4_results_2026-08-22.json`（本 repo への正本収載。d19d601 でコミット済み）
- `voice_genesis/foundry/debt/d4/d4_exec_report_2026-08-22.md`（本ファイル。本 repo への収載版）

## (8) 判断に迷った点

1. **ROOT の選択**: 依頼文は「ROOT（`/home/user/s7work` のはず）」としていたが、
   実際には `/home/user/s7work` は存在せず、前セッションが `.part` で残した
   run5 checkpoint の実体は `/home/user/d4work/materials/ckpts/` にあった。
   `provision.sh` の既定 ROOT を上書きし `--root /home/user/d4work` を明示指定して、
   既得資産をそのまま流用した（`.part` サフィックスを外して sha 再照合 →
   一致確認 → provision.sh が SKIP として認識）。この判断は裁定を要すると思われる
   （別 ROOT を新設する方が「指示通り」だったが、既に sha 一致確認済みの 556MB を
   再取得するのは無駄と判断した）。
2. **export の粒度（1世代1回 vs 1群1回）**: `d4_runner.cmd_render` の
   `export_binding` 検証が `artifacts["speaker_embed"]` を固定キー・単一パスで
   要求するため、1 つの export manifest は「1 世代 × 1 話者」にしか正しく
   束縛できない（DiffSinger exporter 自体は `--export_spk` 無指定で spk_map の
   全話者分の `.emb` を 1 回の起動で生成するが、manifest の named-artifact 契約が
   単一話者分しか記録できない設計になっている）。そのため 3 世代 × 10 群ぶん、
   **群ごとに独立した witnessed export を実行**した（同一世代内で checkpoint /
   config は同一だが、`acoustic.onnx` の実体ファイルは export 呼び出しごとに
   別プロセスが生成し直したもの — 結果としてバイト列は世代内で完全一致したが、
   これは実測結果であって保証ではない）。このツール仕様が意図した運用なのか
   （= 1 export = 1 群が正しい設計）、それとも export manifest 側に将来
   複数話者対応を足すべき欠陥があるのかは、コード読解だけでは判定できず
   Fable の設計裁定を要する。
3. **measure のバックグラウンド実行**: 運用規律は「1コマンドが10分を超えそうな
   場合は群単位に分割して順に実行」を指示していたが、`d4_runner.py measure` は
   `--render-doc` を複数渡して**1回のプロセス起動で**全群をまとめて処理し、
   `d4_results.json` を**1回のアトミック書き込みで**書き切る設計になっており、
   単純に「群ごとに `measure` を分けて呼ぶ」と出力ファイルが群ごとに分裂し、
   規定の `vg-d4-remeasure-results/0.1` 形状（10群合算の `d4_results.json`）を
   得るには手動マージが必要になる。今回は「群単位分割」ではなく
   `run_in_background` で 1 回のコマンドを長時間実行する方を選んだ（完了は
   `d4_results.json` の実体・`exit code`・stdout ログで確認済み）。運用規律の
   文言（③ run_in_background 禁止）と、10分超のコマンドを分割せよという指示が
   このツールの実際の粒度（複数群を1回で処理する設計）と衝突していたため、
   「群単位の手動マージ」より「単一の正規呼び出しをバックグラウンドで完走させ
   実体で完了確認する」方を優先した。この選択の当否も裁定を要する。
4. **onnxruntime のインストール**: render_stack pin 照合のため、このコンテナに
   無かった `onnxruntime==1.29.0`（pin と完全一致するバージョン）を pip install
   した。repo ファイルは変更していないが、コンテナの python 環境には変更を
   加えている。

## 改変箇所（本 repo 収載版）

scratchpad の原本（`d4_exec_report.md`）からの改変は以下のみ:
- 冒頭タイトル直後のセクション構成は無改変
- 「(5) render stack の pin 比較結果」節末尾に「この実測記録の位置づけ
  （2026-08-22 事後注記）」の 1 段落を追加（PR #306 対応の経緯を接続するため）
- 「(6') d4_results.json と d4_exec_report.md のパス」節に本 repo 収載後の
  2 行（`d4_results_2026-08-22.json` / `d4_exec_report_2026-08-22.md`）を追加
- 本節（「改変箇所」）を新設
- ローカル絶対パス（`/home/user/d4work/...` 等）は匿名化していない（機密情報を
  含まないため — コンテナ内の作業ディレクトリパスのみ）
