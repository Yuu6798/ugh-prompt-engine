# RUN9 Success-Only Artifact Admission Harness v1

## 1. 目的

RUN9 Birth measurement を「候補登録 → 照合 → 失敗登録」の繰り返しから分離する。
生成した acoustic export は実行中だけ保持し、固定された出力評価が PASS した場合に
限って、評価に使った同一バイトを成功成果物として登録する。

本変更は合格条件の緩和ではない。測定器は rev 0.6 の84 render、exact replay、
positive reference、finite `d12 > 0`、PJS confuser、C0/C1監査をそのまま実行する。

## 2. 信頼境界

| 境界 | 入力 | 禁止入力 | 出力 |
|---|---|---|---|
| generator | checkpoint、exporter、export環境 | 過去候補の採用指示 | 未登録9ファイル |
| renderer | 未登録9ファイル、固定probe/profile、固定外部資産 | 登録状態 | 84 WAV |
| scientific evaluator | WAV、直列化特徴、固定profile ID、PJS feature | candidate bytes/hash/history/registry/path | PASS または非PASS |
| admission issuer | sealed PASS、同一実行のexport snapshot | 非PASS、呼出者作成の擬似capability | 単回success capability |
| atomic publisher | success capability、9 model、84 WAV、85 feature | failure record、部分bundle、上書き | `successful_*` bundle |

候補hashは renderer が評価した同一バイトを結び付けるため実行内部で計算するが、
scientific evaluator の引数にはならない。判定後に admission record へ付加する。

## 3. 状態遷移

1. 空の一時directoryへ9ファイルをexportする。
2. 9ファイルの完全集合、非空、単一read bytesを確認し実行内snapshotを作る。
3. 外部資産・source commit・runtime・network isolationを照合する。
4. snapshotと同じbytesをrendererへ渡し、固定順で84 renderする。
5. output-only evaluatorが結果をsealする。
6. 非PASSならsuccess capabilityを発行せず終了する。registryへの効果はゼロ。
7. PASSなら単回capabilityを発行する。
8. model 9、WAV 84、feature 85、evidence/admission/markerをstagingへ配置する。
9. inventory再読込とmodel bytes再照合後、atomic renameで1回だけ公開する。

## 4. 失敗時の規律

- 未登録exportはpodのephemeral領域から外へコピーしない。
- 公開領域に候補hash、候補manifest、診断bundle、部分成功bundleを作らない。
- operational statusはstageと終了種別だけを記録し、候補identityを記録しない。
- NOT_ESTABLISHEDとimplementation failureをPASSへ変換しない。
- 既存の成功bundleを上書きしない。

## 5. C1 sham

C1は空profileを横に記録するだけではない。exact-derived `r_sham`を検証し、
`gate_synth.run_pipeline()`が呼ぶduration hookへ渡す。hookは入力durationを不変で
返すため出力を成功方向へ操作しない一方、単回呼出しと同synthesis record上の
`CONSUMED_INERT_ZERO_PROFILE` attestationを強制する。

## 6. Pod境界

podはdigest付きbase image、40桁source commit、固定外部asset SHA、固定export lock、
固定measurement direct dependencyを用いる。全download完了後、render process開始前に
OUTPUT通信を遮断し、ONNX Runtime telemetryもsession生成前に無効化する。成功時も
失敗時もwatchdogとexit trapでself-stopする。pod作成はランチャーの明示確認tokenを
伴う単一POSTだけとし、`prepare`はAPIを呼ばない。

## 7. 実装対応

- policy: `inputs/success_only_admission_policy.json`
- evaluator/admission/publisher: `run9_success_admission.py`
- pod entry: `run9_success_pod_entry.sh`
- prepare/launch boundary: `run9_success_pod_runner.py`
- tests: `tests/test_success_only_admission.py`, `tests/test_run9_success_pod.py`
