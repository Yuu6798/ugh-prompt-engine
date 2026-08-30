# RUN9 Success-Only Pod Prelaunch

## 現在の停止点

この手順はpod作成直前で止める。`prepare`はsource・policy・entry shell・remote branch
一致を検証してpayloadを表示するだけで、RunPod APIを呼ばない。billableなpod作成は
`launch`と完全一致tokenの両方が指定された場合に限る。

## 起動前に満たす条件

1. reviewed treeをcommitする。
2. 同一branchを`origin`へpushする。PRは作らない。
3. working treeがcleanで、local HEADとremote branch HEADが完全一致する。
4. `RUNPOD_API_KEY`を環境変数に設定する。payloadやログへ値を埋め込まない。
5. 次を実行し、`ready: true`、`pod_created: false`を確認する。

```bash
python voice_genesis/evolution/run9_dual_founder_pjs/run9_success_pod_runner.py prepare
```

## 明示起動（この準備作業では実行しない）

```bash
python voice_genesis/evolution/run9_dual_founder_pjs/run9_success_pod_runner.py \
  launch --confirm-launch RUN9_SUCCESS_ONLY
```

このコマンドだけが `POST https://rest.runpod.io/v1/pods` を1回行う。自動retryはない。
pod内bootstrap失敗、admission中断、生成物非PASS、PASSのすべてでself-stop経路を持つ。
成功bundleはpodのHTTP 8000番で取得できる。非PASSでは登録directory自体が存在しない。

## 実行中の観測

- `heartbeat.json`: operational stageとUTC時刻
- `status.json`: success/failed、最終stage、registration作成有無
- `run9_console.log`: 実行log（candidate digestをstatusへ転記しない）
- `successful_run9/`: PASS時だけ存在するatomic success bundle
