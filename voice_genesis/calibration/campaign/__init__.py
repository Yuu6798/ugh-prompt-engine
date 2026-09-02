"""Phase D2 — campaign runner（IMPLEMENTATION_MAP_v1.md §6.4）。

このサブパッケージは **凍結済み campaign dir**（`c0_freeze.py` の
`armed_freeze()` が公開した `campaigns/<campaign_id>/` 一式）を読み込み、
手続 Gate 単位（C1 fixtures → C2 baseline → C3a F0 selection → C3b selection →
unseal → C4 holdout → close）でキャンペーンを進行させる runner を提供する。

授権境界（`IMPLEMENTATION_MAP_v1.md` §0 / 本パッケージの Task Brief）:

- C0 freeze の実行・secret の生成保存は一切行わない（凍結済み campaign dir を
  受け取るのみ。secret は `state.load_frozen_campaign()` が凍結済み secret dir
  から読むだけで、本パッケージは一切生成しない）
- 462×5 の実 campaign 実行・selection/holdout の実測走行は本 Phase の目的外
  （既定は dry-run — 武装サブコマンドを明示的に選び `--armed` + 環境変数 +
  Gate 1 承認が揃わない限り副作用ゼロ）
- 既存 meter（`voice_genesis/harness/*`）・`candidates/impl/*` の変更は一切
  行わない（`candidates.registry`/`candidates.adapter` を読み取り専用で呼ぶ）
- RUN11 関連の一切を含まない

サブモジュール:

- `state.py` — 凍結 campaign dir の読み込み・orphan 検出・手続フェーズ導出
- `workunits.py` — C1/C2/C3a/C3b/C4 の work unit 決定論的列挙 + `plan` 集計
- `render_stage.py` / `_render_worker.py` — C1/C4 の fresh-process 二重 render
  + determinism 検査 + leakage 検査
- `measure_stage.py` / `_measure_worker.py` — within/fresh-process meter call
- `baseline_stage.py` — C2 baseline audit + tolerance 導出
- `selection_stage.py` — C3a (F0) / C3b (他 family) selection
- `unseal.py` — §7 5-sha 相互参照 + Gate 3 束縛
- `holdout_stage.py` — C4 gate 判定 + 終端 status cascade
- `close.py` — CAMPAIGN_CLOSED + `debt_discharged` 導出 + M6
- `cli.py` — `python -m voice_genesis.calibration.campaign <subcommand> ...`
"""

from __future__ import annotations
