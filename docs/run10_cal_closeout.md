# RUN10-CAL closeout — meter 校正負債キャンペーンの失敗終端

**日付:** 2026-09-10（User 裁定同日）
**状態:** **CLOSED_FAILED** — meter 校正は不成立。`debt_discharged = false` のまま確定する。
**対象:** RUN10-CAL / VG-METER-CAL-DEBT（設計 v1.0〜v1.4）。凍結済み campaign は
`voice_genesis/calibration/campaigns/` にある **6 件すべて**（下表）。

| campaign | 到達段階 | 終端 | `compute_used`(s) |
|---|---|---|---|
| `20260903-9bcbbf86` | C1 fixture_valid まで | `campaign_closed` 未記帳 | 12,380.9 |
| `20260903-591cadcd` | selection_frozen + gate3_accepted まで | 同上 | 44,437.4 |
| `20260904-862dec28` | close まで | **`CAMPAIGN_CLOSED`** | 33,797.9 |
| `20260905-410b25f2` | selection_frozen まで | `campaign_closed` 未記帳 | 50,678.4 |
| `20260906-a4ed65c1` | split_frozen + render 開始のみ | 同上 | 3,412.1 |
| `20260908-2dde4014` | close まで | **`CAMPAIGN_CLOSED`**・ただし authorization 未確立で `QUARANTINED` | 65,146.9 |

完走 2（うち 1 は隔離）/ 部分実行 4。合計 **209,853.6 s ≈ 58.3 時間**。各値は
`campaigns/<id>/counters.json` の `compute_used`、到達段階は同 `ledger.jsonl` の
event kind から再現できる。

**証拠保全:** `voice_genesis/calibration/` 配下のコード・`campaigns/**`・設計 v1.x は
**変更しない**。本記録は終端の宣言であって、実測記録の書き換えではない。

## 1. 終端理由（User 逐語）

> 測定、検証系をオーバーエンジニアリングしすぎたことで生成の進捗が得られずスタックした

計器（meter）が正しい値を返すかを判定する装置を **6 campaign・計算 58.3 時間**ぶん回し、
その装置の堅牢化にコードと設計文書を積み増したが、**生成の進捗は得られなかった**。
装置の完成度ではなく、生成が進まなかったことが終端の理由である。

## 2. meter 別の判定

| meter | 判定 | 内容 |
|---|---|---|
| `M2_SPECTRAL_TILT` | **計器欠陥** | `tilt_harmonic.harmonic_amplitudes_db` が `k*f0` の bin を固定で読みピーク探索をしないため、渡された f0 の誤差が次数 `k` 倍に増幅される。段差は測定器起因と確定（v1.4 §Y1 の fixture grid + δ 掃引） |
| `M3_FORMANTS` | **不成立** | claim scope から除外済み（v1.3 #350）。校正到達せず |
| `M5_TRANSITION` | **不成立** | 校正到達せず |
| `M2_APERIODICITY` | **未解決** | DIRECTIONAL sweep が holdout で解決不能（D77）。棄権・極性の宣言は unarmed census が支持するが、校正には至らず |
| `F0_CONTROL` | **供給役のみ** | 上流 control として下流へ F0 を供給する役割に留まる。選定は split 依存で fail-closed し、下流ファミリーが連鎖 fail-closed した |
| `M4_RESONANCE` | `DIAGNOSTIC_ONLY` | 非 claim-critical。campaign `862dec28` の終端値 |
| `M6_IDENTITY` | `NOT_EVALUABLE` | 非 claim-critical。ABSOLUTE ceiling 未達 |

claim-critical 3 本（`M2_SPECTRAL_TILT` / `M2_APERIODICITY` / `M3_FORMANTS`）はいずれも
校正に到達していない。よって `debt_discharged` は **false** で確定する。

## 3. 波及

- **RUN11 入場は不許可のまま**。校正済み meter を前提とする測定エントリは開かない。
- **RUN10 Phase B gate は不通過のまま**。
- **meter 依存の過去主張は未校正扱い**。RUN10-CAL の meter で得た数値を、校正済みの
  絶対量・方向量として引用してはならない。
- campaign `RUN10-CAL-20260908-2dde4014` は 2026-09-09 の execution-boundary correction により
  `QUARANTINED` / `claimable=false` のまま。本 closeout はその隔離を解除しない。
- Meter Bench（PR #358、ブランチ `claude/voicegenesis-meter-bench-v0-v1ulhr`）は同裁定により
  **中断・close**。ブランチは削除せず残置し、TILT の欠陥再現と修正案は履歴に保全する。

## 4. 再入条件

計器が **生成→実測のサイクルの中で動くと示されたとき**、**新しい run ID** で再入する。

- 「計器を先に完成させてから生成に戻る」順序では再入しない（それが今回の失敗そのもの）。
- 設計 v1.5 以降の起草・再 freeze・campaign 実行・D 台帳追記は行わない。
- 再入時も `voice_genesis/calibration/` の既存資産は歴史証拠として不変のまま扱う。

## 5. 今後の開発指針（User 逐語）

1. 「生成→実測→検証→修正→再生成 のサイクルを厳守すること」
2. 「レビューによる機能追加は時系列を先立って行わない。」
3. 「もし行う場合は 1 の追加機能で 3 のメリットが得られる改善レビューに限定する。」
4. 「実コード破壊、クリティカルバグ、将来汚染も同じ時系列の中で判断する 3 原則で、
   生成物の開発中に 3 原則にあたるからと測定器や検証器の改善や機能追加を行わないこと。」

正本は [`CLAUDE.md`](../CLAUDE.md) の「開発サイクルと機能追加の原則」節。
