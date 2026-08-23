# run4 export-device probe — 実行報告（2026-08-23）

- 根拠: `debt_ledger.yaml` VG-DEBT-008 `reentry_condition` (a-2)（正本）。
  PR #307 で GPU 課金律速により掃引を見送った残余候補「acoustic ONNX export 時の
  デバイス差（GPU vs CPU）」を、User 承認のもと RunPod GPU pod 上で単一要因
  掃引した。
- 正典データ: [`run4_export_device_probe_2026-08-23.json`](run4_export_device_probe_2026-08-23.json)
  （本 report はその要約であり、乖離した場合は JSON と台帳が勝つ）
- probe 実装: `voice_genesis/foundry/scripts/run4_export_device_probe_pod.sh` /
  `run4_export_device_probe_runner.py`（実行 commit `26373e3`）

## 設計（単一要因制御）

repo checkout を pin commit `cda36b9f`（`gate_synth_run4.py` blob `006cd867…` ほか
消費ファイル 5 点の blob sha 照合）、素材（run4 40K ckpt・config 4 点・canon 消費
4 メンバー・vocoder・DiffSinger `e2307b1`）を全数 sha256 fail-closed 照合、venv を
既往実測（torch 2.13.0）と同版の **+cu126 CUDA build** に差し替え、run4 同一パス
root `/root/s1work` で原記録どおりの 3 起動を再現する。操作変数は export プロセスの
CUDA 可視性のみ。

| アーム | device | 産物 |
|---|---|---|
| G1 | CUDA | acoustic.onnx + anchor WAV 6 本 |
| G2 | CUDA（再実行） | 決定論チェック |
| C1 | CPU 強制（同一 pod・同一 venv） | 対照 |
| C1b | CPU・別パス root（C1 不一致時のみ） | パス埋め込み感度 |

## 実行経過（4 起動・計 ≈ $0.38）

1. **run 1**（`ihlx25tiqiwfec`・67 分・≈$0.25）: 回収失敗。原因は最安 CPU pod での
   チャネル単体実験で実測確定——`*.proxy.runpod.net` 前段の Cloudflare が
   `User-Agent: Python-urllib/*` を UA 文字列のみで一律 403（error 1010）にする。
   runner の pod 向け全リクエストが urllib 既定 UA だったため、pod の状態に依らず
   回収が構造的に不可能だった。UA 注入 + t=0 回収窓 + volume 保険を実装して根治
2. **run 2**（`fb4g2z50g6ai8y`・8 分・≈$0.03）: nvidia-smi 成功にもかかわらず
   venv A の CUDA init が「CUDA unknown error」→ CUDA ゲートが設計どおり
   fail-closed。機構は未診断（診断ステージは本 run 後に追加）。CUDA 診断 +
   3-way ゲート（system torch フォールバック）を実装
3. **run 3**（`9vqeutstuut6lj`・24 分・≈$0.09）: 別ホスト（driver 580.65.06）で
   venv A CUDA 可用 → 本来計画の全アームを完走。全成果物を回収し、バイナリは
   手元再計算 sha256 と記録値の一致を確認。全 pod の terminate を都度確認
   （課金放置・二重起動ゼロ）

## 結果

| 比較 | 結果 |
|---|---|
| G1 (GPU) onnx vs C1 (CPU) onnx（同一 pod・同一 venv） | **不一致**（`3e6c3a3a…` vs `53ad8c43…`） |
| G1 WAV 6 本 vs 記録済み run4 sha256 | **6/6 バイト一致**（rms・dur も記録値と一致） |
| C1 WAV vs 記録済み | 0/2 不一致 |
| G2（GPU 再実行） | ONNX・WAV とも G1 と bit 一致（決定論） |
| C1b（別パス root） | C1 と bit 一致（パス埋め込み感度なし） |
| C1 onnx vs session CPU export (`a6da561a…`) | 不一致（未解決・列挙のみ、下記） |

**実測で確定した知見**（詳細列挙は JSON `findings_measured`）:

- **F1**: export の実行時デバイスは acoustic ONNX のバイトを変える
- **F2**: GPU export 経路の anchor WAV 6 本が記録済み sha256 と **6/6 一致** =
  `reentry_condition` **(a-2) の機能的裏付けが成立**
- **F3**: 合成環境を固定した pod 内比較で、WAV の一致/不一致は **export device
  のみで反転**した（CPU-export ONNX → 0/2、GPU-export ONNX → 6/6）
- **F6**: CPU export 経路は 2 環境・2 通りの ONNX バイトでいずれも記録 WAV に
  不一致（session 0/6・pod 0/2）

## 主張範囲（正直会計）

**主張しないこと**（JSON `non_claims` が正）:

- run4 当時の export プロセスで CUDA が可視だったという**歴史的述語**（当時の
  device・torch ビルド・onnxruntime 版は依然未記録）
- GPU-export ONNX `3e6c3a3a…` が**当時 ONNX バイトと同一**であること（当時 sha の
  記録が無く判定不能）
- 「GPU export ならば常に再現する」という一般化（本 pod 環境 1 点の実測）
- 残余候補への順位付け（PR #307 終端宣言（追補 2）を継承。本件は順位でなく
  単一要因操作の実測）

**未解決のまま列挙に留めるもの**: pod CPU export と session CPU export の不一致
（wheel ビルド種別 / OS / ホスト CPU の交絡）、run 2 ホストの CUDA init 失敗機構。

## 台帳への影響

`reentry_condition` (a-2) の定めどおり:

- **(a-2) 機能的裏付けとして記帳**（本 report + JSON）
- **item 1 は `measured_only` に据え置き**（(a-1): WAV 一致では昇格しない——
  6 入力に対する振る舞いの一致であって、当時 sha が未記録の上流 ONNX バイトとの
  同一性の証拠ではない）
- **VG-DEBT-008 の status は `accepted_residual` のまま不変**

## 副産物（運用知見）

- RunPod proxy 前段 Cloudflare の `Python-urllib/*` UA ban（error 1010）。
  runner は全 urllib 呼び出しに UA を注入する実装へ恒久修正済み
- 回収窓を exit trap でのみ開く設計は「課金全額消費後にしか疎通検証できない」
  構造欠陥だった → t=0 常時 serve + heartbeat + volume 保険 + stop→回収→delete
  順序化へ恒久修正済み
- 同一イメージ・同一 GPU 種でもホストにより venv の CUDA init が失敗し得る
  （run 2）。CUDA 診断ステージと 3-way ゲート（system torch フォールバック）を
  probe に恒久組込み済み
