# RunPod 無人測定 probe 運用 runbook（実行者非依存）

短時間・無人・単発の GPU 測定 pod（「probe」）を RunPod 上で安全に回すための
運用知見集。**全項目が 2026-08-23 の実測に基づく**（PR #312: run4
export-device probe・4 起動 計 ≈ $0.38。動く実例 =
`scripts/run4_export_device_probe_pod.sh` / `scripts/run4_export_device_probe_runner.py`）。
長時間学習 run の無人実行は `S4_RUN5_RUNBOOK.md`（run5 bootstrap 10 段）が正本で、
本書はそれを置き換えない。数値・挙動はすべて実測時点のもので、RunPod 側の
仕様変更で変わり得る（再確認の一次ソースを各節に明記）。

## 1. 回収チャネル（最重要の落とし穴）

- **`https://<podId>-<port>.proxy.runpod.net` の前段 Cloudflare は
  `User-Agent: Python-urllib/*` を UA 文字列のみで一律 403（body =
  `error code: 1010`）にする**。curl 既定・Mozilla・空文字列 UA は同一 URL に
  即 200（UA 分離実験で確定）。**Python urllib を使うクライアントは全リクエストに
  カスタム UA を注入すること**（runner は `_build_request()` に集約し、UA なし
  リクエストの構築経路自体を排除 + source-scan テストで再発防止）
- proxy の応答コードは pod 内サービスの readiness 情報を持たない:
  403（UA ban）は listener の有無と無関係で、pod 削除後に 404 へ変わる。
  「403 → 404」の遷移だけでは何も診断できない
- チャネル性能（実測）: listener は pod 起動 3 秒で bind 可・warm-up なし・
  5MB ≈ 1 秒で転送可（WAV/ONNX 回収に十分）。`python3 -m http.server` の
  directory listing は href scrape で機械回収できる

## 2. 回収アーキテクチャ原則

1. **結果サーバは t=0 から常時 serve する**（exit trap でのみ開く設計は
   「課金を全額消費した後にしか疎通検証できない」構造欠陥。初回 run で
   $0.25 の測定データを喪失した実測起源）。heartbeat ファイル（UTC + stage）
   と console log の live コピーを同じディレクトリで配信し、runner は起動後
   10 分以内に heartbeat 200 を確認できなければ即 terminate（損失 ~$0.05 で
   打ち切り）
2. **volume を保険に張る**（`volumeInGb` ≥ 10 + `volumeMountPath: /workspace`）。
   container disk のみだと pod 停止と同時に結果が消滅する。回収失敗時は
   **delete でなく stop で保持** → 再起動サルベージ → 回収確認後に delete
3. **volume 永続化の副作用**: 再起動時に前 run の完了マーカー
   （status.json 等）が残存し、runner が stale を今回の成果として受理し得る。
   pod script は trap 確立直後・サーバ起動前に既存成果物を
   `prev_run_<UTC>/` へ退避する（マーカー優先）。runner 側も `--out` 再利用時に
   `prev_fetch_<UTC>/` へ退避する
4. **成功条件の要求集合は閉世界契約（固定定数）で書く** — `AGENTS.md` §8
   「回収・検収系の成功条件は閉世界契約で書く」が正本。listing 由来・record
   由来の要求集合は publish 失敗・schema 差・digest 欠落で空洞化し、
   「何も検査せず exit 0」の偽成功経路になる
5. 回収したバイナリは **sha256 を手元で再計算して記録値と照合**してから
   pod を delete する（terminate 後は検証可能な証拠が恒久喪失する）

## 3. 課金制御（構造で保証する）

- **同時 1 pod 厳守**・pod id は作成応答から即ファイル記録。id を抽出できない
  201 応答は成功扱いにせず exit（課金 pod が存在するのに自動化が id なし成功を
  記録する経路の閉塞）。作成 POST の曖昧な失敗（timeout/5xx）後は必ず
  GET /pods で既存 pod を確認してから再試行
- **POST /pods は自動リトライしない**（タイムアウト後の再送で二重課金 pod が
  生まれる）。GET/DELETE はリトライ可。ネットワーク例外の捕捉は
  `(URLError, TimeoutError, ConnectionError)` のタプルで統一（`URLError` 単独では
  素の `TimeoutError` を取り逃がし stop→DELETE fallback が届かない）
- **self-stop は全 exit 経路 + 二重化**: trap の `runpodctl stop` は 5 回
  リトライ・失敗時は明示警告。独立の wall-clock watchdog（例 10800s）を
  trap より前に確立（trap 設置前の exit 経路が 1 つでもあると self-stop 契約が
  破れる）。on_exit は回収窓を守るため watchdog を kill してから serve する
- **起動コマンドの curl は `--retry 5 --retry-all-errors` + ファイル落とし実行 +
  取得失敗時 self-stop**（`curl | bash` 単発は取得失敗で空実行 exit 0 =
  課金だけ発生する silent no-op になる）

## 4. fail-closed 測定規律

- 全素材（ckpt・config・canon・vocoder・スクリプト blob・repo revision）を
  埋め込み pin と sha256 照合し、1 件でも不一致なら**測定せず** self-stop
- GPU 前提の測定は `torch.cuda.is_available()` を明示ゲートし、偽なら
  **CPU へ黙ってフォールバックせず停止**（測定条件の静かな変質の防止）
- runner の fetch は status.json の `status` を解析し、`failed` なら診断を
  全回収した上で **exit 非ゼロ**（自動化への偽成功報告の禁止）

## 5. CUDA ホスト差（同一イメージでも割れる）

- **`nvidia-smi` 成功 ≠ venv の CUDA init 成功**。実測: 同一イメージ・同一
  GPU 種（RTX 3090）で、driver 580.95.05 のホストは venv torch 2.13.0+cu126 が
  「CUDA unknown error」で `is_available()=False`、driver 580.65.06 の別ホストは
  同一手順で可用。機構は未特定（ホスト依存要因の存在までが実測）
- 対策: **CUDA 診断ステージ**（/dev/nvidia*・env・ldconfig・system/venv 両
  torch の init エラー全文・pip freeze を常時記録）+ **3-way ゲート**
  （venv 可 → 本計画 / system torch のみ可 → フォールバック昇格 + スタック内
  CPU 対照 / 両方不可 → 専用 stage 名で早期停止 = 別ホストで再起動）。
  再起動は 1 回まで・2 ホスト連続不可なら停止して人間へ

## 6. RunPod REST の実測事実（再確認先: `GET /v1/openapi.json`）

- pod 作成: `POST https://rest.runpod.io/v1/pods`（Bearer 認証）。
  `ports` は文字列配列（例 `["8000/http"]`）・`dockerStartCmd` は文字列配列。
  201 でも payload の妥当性が全検証されるとは限らない
- CPU pod は `containerDiskInGb` ≤ 20 の hard cap（超過は 500）。
  `cpuFlavorIds` 列挙: `cpu3c, cpu3g, cpu3m, cpu5c, cpu5g, cpu5m`。
  チャネル系の切り分け実験は最安 CPU pod（~$0.01）で先に行う価値がある
- REST v1 に pod ログ取得エンドポイントは無い（openapi 全走査で確認）——
  ログが要るなら pod 自身に配信させる（§2 の t=0 serve）
- image は tag 参照で digest pin ではない = 環境再現性の保証境界
  （凍結記録の再現性主張は「同一 pod 内 bit 一致」までに留める）

## 7. 凍結記録の訂正手順（三面協調更新）

凍結済み正典記録（immutability テストの二重アンカー下にあるもの）を
レビュー採用で訂正するときは、**(1) record 本体 → (2) 台帳
`evidence_delivered` の sha256 → (3) `test_committed_artifacts_immutable.py` の
`FROZEN_SHA256` 定数**の三面を同一コミットで更新する（PR #312 で 2 回運用し
機能を確認）。訂正後は訂正**前**の語彙で 3 面を grep して掃討する
（`AGENTS.md` §8 撤回・訂正の同語横断全数掃討）。
