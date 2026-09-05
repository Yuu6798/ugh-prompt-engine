"""RUN10-CAL: VoiceGenesis meter 校正キャンペーン基盤 (`voice_genesis.calibration`).

campaign_id: RUN10-CAL

授権状態: `execution_authorized: false`（インフラ実装のみ）。本パッケージは
設計正本 `DESIGN_VG_METER_CAL_DEBT_v1.0.md`（VG-METER-CAL-DEBT-DESIGN-v1.0、
read-only 基底 — v1.0 §0 の改訂規約により in-place 改変禁止）+
`DESIGN_VG_METER_CAL_DEBT_v1.1.md`（VG-METER-CAL-DEBT-DESIGN-v1.1、統治
revision — Gate 承認が pin する現行の設計正本。v1.1 に明記のない全ての節は
v1.0 が引き続き正）の Phase A（framework core）を実装したものであり、以下は
本パッケージのいずれの API からも一切生成・実行されない:

- C0 freeze の実行（manifest/registry の凍結 artifact 生成・freeze event 記録）
- secret（`split_secret` / `render_root_secret`）の生成・保存
  （全 API は secret を呼び出し側からの引数として受け取るのみ）
- 456×5 campaign の実測走行・selection/holdout 実行
- 既存 meter（`voice_genesis/harness/*` 等）の変更
- RUN11 関連の一切

設計正本: `voice_genesis/calibration/DESIGN_VG_METER_CAL_DEBT_v1.0.md`（read-only
基底）+ `voice_genesis/calibration/DESIGN_VG_METER_CAL_DEBT_v1.1.md`（統治
revision。Gate 承認の pin 対象、`approvals.DESIGN_DOC_RELATIVE_PATH`）
実装マップ: `voice_genesis/calibration/IMPLEMENTATION_MAP_v1.md`
入口ドキュメント: `voice_genesis/calibration/README.md`

このモジュール自体は重量級サブモジュールを re-export しない
（`from voice_genesis.calibration import vocab` のように個別 import すること）。
"""

from __future__ import annotations

CAMPAIGN_ID = "RUN10-CAL"
EXECUTION_AUTHORIZED = False
