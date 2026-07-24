"""melody — 主旋律観測センサー（M0/M1: 成立帯域の発見）。

このパッケージは「主旋律 preserved を保証する」ことを目的にしない。
`docs/melody_observability.md`（M0/M1 設計）が示すとおり、本パッケージのゴールは
**どの入力 × 抽出器 × 前処理なら旋律をそもそも観測できるか**を測る
観測ゲート（`observability.py`）と、入力種別 → 抽出経路の選択（`routing.py`）
だけを提供することにある。類似度比較（M3）・正解精度評価（M2）・Recast 配線（M4）
は本パッケージには含まれない。

規律（`docs/learned_models_policy.md` 準拠）:

- learned 抽出器（CREPE / Melodia / basic-pitch / Demucs 分離）の出力は
  `PhysicalRPE.melody_contour` など決定論 RPE フィールドへ混入させない。
  本パッケージは観測レポート（`MelodyObservabilityReport`）のみを返す。
- 観測不成立（旋律不在・被覆不足・抽出器不一致）は `status="insufficient"` として
  正直に返し、比較（後段）を呼ばない。preserved と偽称しない。
"""
from __future__ import annotations

from svp_rpe.melody.observability import (
    MelodyNote,
    MelodyObservabilityReport,
    MelodyObservation,
    ObservabilityThresholds,
    assess_observability,
    cross_extractor_agreement,
    notes_from_frames,
)
from svp_rpe.melody.routing import MelodyRoute, select_routes

__all__ = [
    "MelodyNote",
    "MelodyObservation",
    "MelodyObservabilityReport",
    "ObservabilityThresholds",
    "assess_observability",
    "cross_extractor_agreement",
    "notes_from_frames",
    "MelodyRoute",
    "select_routes",
]
