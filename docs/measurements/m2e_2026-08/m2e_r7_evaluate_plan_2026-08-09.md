# M2e r7 (evaluate → store_B) 実行計画案 — 設計側承認待ち

- **作成日**: 2026-08-09 (JST)
- **状態**: **提案のみ・未開始**（予算値の設計側確認後に起動する）
- **追記（2026-08-10）**: §6 の予算値（単価 335 s/cell・全 4 水準 ≈119h・run 上限
  水準 18/全体 72・単価 >500 s/cell 2 チャンク連続で停止）は**設計側承認済み**。
  §3 の run 回数上限も 18/72 で確定。裁定記録 =
  [`m2e_r7_adjudication_2026-08-10.md`](m2e_r7_adjudication_2026-08-10.md) §5–§6。
  **本計画は起動可能**（step0 blocker も同 memo §0–§4 のとおり解消済み）。
- **前提裁定**: 腹案（時間予算区切り + 同一ストア再開）は方向承認済み（2026-08-09）。
  C2/C3 committed 経路の使用・census(C5) のみが帯判定を出すことを厳守。

## 1. 経路（C2/C3 committed 経路の遵守）

HANDOFF.md の evaluate 形を逐語で使う。1 水準 = 1 evaluate（両アーム・repeat 0/1 を含む）:

```bash
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python scripts/run_melody_accuracy.py \
    --out docs/measurements/m2e_2026-08/verdict_<lvl>.json \
    --evaluate <run report(s) for lvl> \
    --external-manifest build/m2e/manifest_<lvl>.json \
    --external-fixtures tests/fixtures/melody_bench/m2e_vremix_fixtures_<lvl>.yaml \
    --eval-cell-store build/m2e/store_B --workers 2 --pin-threads
```

- `--eval-cell-store build/m2e/store_B`: **空ディレクトリから開始**。store_A のコピー禁止。
- `store_role` 束縛: store_B レコードは `evaluate` role。run role のセルは resume されない（rev.6 §8.9.4 D-5）。
- `--workers 2`（repeats_min=2 の頭打ち）・`--pin-threads`（OMP/MKL=1 + torch 1 スレッド強制）。
- 4 水準（p12 → p06 → p00 → m06）を順次。store_B は 4 水準共用（セルキーが水準を含むため衝突しない）。

## 2. 予算値（単位コストドリフト織り込み）

r6 実測より:
- 校正想定: ≈ 83.5 s/cell（(T_direct+T_stem)/2, P=2）
- r6 序盤実測 (shard 0–4): 91.3 s/cell
- **r6 定常実測 (shard 5–18): 291.8 s/cell（最大 337.2）** ← 校正比 ×3.2 ドリフト

**採用単価: 335 s/cell**（= r6 定常平均 +15% ≒ r6 最悪 shard 実測。
設計側推奨の「校正基準 ×3 安全率」83.5×3=250.5 s/cell より保守的な側を取る）。

| 対象 | セル数 | 見積り壁時計 |
|---|---|---|
| 1 水準 | 320 | 320 × 335s ≈ 29.8h |
| 全 4 水準 | 1280 | 1280 × 335s ≈ **119h ≈ 5.0 日** |

（参考: ドリフトが無ければ 1280 × 91.3s ≈ 32.5h。「実測 10h 級」の従来観は
ドリフト前の単価に基づくため、本計画では採用しない。）

## 3. 分割粒度・再開

- **区切り**: B_session = 7200s + hang 上限 600s（r6 と同値）。1 チャンク ≤ 2h で
  打ち切り、同一 `--eval-cell-store build/m2e/store_B` を再指定して再開
  （§8.7 セル台帳 + store_role 束縛が再開の正しさを担保）。
- **想定チャンク数**: 水準あたり ceil(29.8h / 2h) ≈ **15 run**、全体 ≈ **60 run**。
- **run 回数上限（提案）**: 水準あたり 15 + 3 = **18 run**、全体 **72 run** を上限とし、
  超過したら停止して設計側へ報告（r6 の R_max = N+2 の趣旨を run 単位に翻訳。値は設計側裁定に従う）。
- 各チャンク終了時に shard-run 相当の記録 JSON（`--out` の evaluate report は最終 verdict のみ
  なので、チャンク毎の console ログ + store_B セル数を進捗記録とする）。

## 4. 監視・停止条件（r6 と同型）

- スワップ監視: vmstat 60s 間隔 → `build/m2e/swap_watch_r7.log`。**so>0 で即報告**。
- 進捗 cron: 定期チェック（store_B セル数 / console tail / プロセス生存 / so=0）。
- fail-closed: 非ゼロ exit・cells_unavailable 非空・store 経路検査失敗で即停止・報告。
- 単位コスト監視: チャンク毎に s/cell を記録し、**採用単価 335 s/cell を +50% 超過
  （>500 s/cell）が 2 チャンク連続**したら停止して報告（新たなドリフトの検出）。

## 5. 報告規律

- **帯の判定・水準別の精度数値は census(C5) のみが出す。**
  evaluate 進行中・完了時の報告には完了セル数・逸脱・単価実測のみを書き、
  store_B / verdict の水準別数字の先読み・要約は**書かない**。
- verdict JSON は `docs/measurements/m2e_2026-08/verdict_<lvl>.json` に出力
  （dated record として commit、m2b/m2c 前例どおり）。判読は設計側。

## 6. 開始条件

本計画の予算値（単価 335 s/cell・総 119h・チャンク 60±・上限 72 run）について
設計側の確認を得てから起動する。
