"""recast-report 側スキーマの共通基底（`report.py` と `experimental.py` の両方が使う）。

`RecastReportModel` をこの独立モジュールへ切り出す理由: `recast/report.py`
（`RecastReport`）と `recast/experimental.py`（`ExperimentalAnchorEntry`、
`RecastReportModel` を基底に使う）は M4c で相互参照になる
（`RecastReport.experimental_anchors: List[ExperimentalAnchorEntry]`）。
共通基底だけをこの独立モジュールへ抽出することで `report.py` ⇄
`experimental.py` の import 循環そのものを構造的に無くす（import 順序に
依存する遅延 import のハックを持ち込まない — フェーズ 1 引き継ぎ事項が
提案した「`RecastReportModel` クラス定義の後に import」方式は
`svp_rpe.recast.experimental` を先に import する経路
（`tests/test_recast_experimental.py` 等）で `ImportError` になることが
実測で判明したため、本モジュール抽出へ変更した — 最終報告に明記）。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RecastReportModel(BaseModel):
    """recast-report 側スキーマの共通基底。未知 key を拒否する。"""

    model_config = ConfigDict(extra="forbid")
