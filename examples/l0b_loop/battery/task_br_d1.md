# L0b-R 事前登録課題 — BR-D1（単一レバー・既実証機構 / 回帰セル）

**Status**: 事前登録（2026-08-06）。陽性対照ゲート通過後に凍結。
正本 = [`docs/llm_adapter_planning.md`](../../../docs/llm_adapter_planning.md)
§5 と `battery/ledger_l0br.yaml`（バッテリー台帳・pin の一次資料）。
凍結後の変更は禁止（変更が必要になったら別実験として記録し直す）。

## 難易度軸上の位置と登録動機

- **難易度**: `single_lever_proven` — 要求は L0B-T2 と同一（4 セクション交互
  対比・D minor・dark）。この課題には**既実証の成立機構**が存在する
  （L0b clean_branch round 5c: sub bass 単一トグルの全境界統一 =
  `docs/l0b_closed_loop_record.md` §3.3）。
- **動機**: L0b の成立は改善 1 件の最小実証だった。同一要求への**新規系列**で
  (a) 到達率が再現するか（回帰確認）、(b) 別個体著者が同じ機構へ再到達するか
  （機構の再発見可能性）を測る。バッテリー内の基準セルとなる。

## 課題文（著者可視・機械転写の対象）

`battery/statement_br_d1.md` が課題文の正本（content hash は台帳の
`tasks[br_d1].statement` に pin）。本 doc には転記しない（例文重複による
乖離リスクを避ける — AGENTS.md §8「例文も仕様」）。要求は L0B-T2 課題文
（`task_t2.md`）と同一の R1/R2/R3。

## 判定器（凍結・L0b と同一）

`scripts/run_round.py --section-map frozen/section_map_t2.json`（T2 経路）+
`scripts/pareto_eval.py`。**両スクリプトとも無改変**（判定器の同一性維持が
本実験の前提条件。sha256 は台帳 `judge` 節に pin）。

## 3 チェック（正本 §4.1 登録前必須）

- **チェック 1（信頼軸）**: L0B-T2 チェック 1 と同一（key・brightness(dark)・
  structure の 3 軸のみ）。→ **通過**
- **チェック 2（観測語彙）**: L0B-T2 チェック 2 と同一（4 セクション検出時、
  中間 2 件は必ず両方 Chorus = 要求列は語彙内かつ 4 検出時の唯一列）。→ **通過**
- **チェック 3（観測粒度）**: L0B-T2 チェック 3 と同一（R1 = ラベル列単位、
  R2/R3 = トラック単位）。→ **通過**

## 予想される困難（登録時仮説）

L0B-T2 の実測と同一: 中間 2 セクションの境界検出は音響対比がなければ融合し
3 要素列へ縮退する（Levenshtein 距離 1）。既実証機構は存在するが、それを
**別個体著者が契約 v1 + 差分報告のみから再発見できるか**は未知 — これが
本セルの測定対象である。

## 陽性対照ゲート

L0B-T2 の陽性対照（`positive_control_t2/`・pin 済み）が本課題の達成可能性を
そのまま実証する（要求が同一のため）。新規探索は行わず、**pin 済み陽性対照の
再現確認**（`run_round.py` T2 経路で report が pin 値とバイト一致）を開始前に
1 回実施し、結果を台帳 `tasks[br_d1].positive_control` に dated 記録する。
