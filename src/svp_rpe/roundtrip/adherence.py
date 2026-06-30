"""Score-adherence test (PR2): did the楽譜の control_profile-tight 保証が守られたか。

`control_profile` が tight と宣言した物理フィールドについて、

1. **コンパイル側**: `ExternalPromptAdapter` が tight フィールドをプロンプトへ保持したか
   （PR1.5 の保証＝tight は drop されない）を `dropped_elements` で検証。
2. **演奏側**: 楽譜→演奏→抽出→draft のラウンドトリップで保たれたか
   （`RoundtripReport` の 4 値診断が `preserved` か）を判定。

`RoundtripReport` 同様、本レポートも**計器であって verdict ではない**。グローバルな
pass/fail は出さず、フィールド単位の保持/非保持状態と件数のみを返す。実コンパイル経路
（楽譜→アダプタ→生成器→extract）の検証で、生成側が決定論 performer なら自動、実 Suno なら
corpus take 由来の `RoundtripReport` を渡せば同じ判定が効く（path 非依存）。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from svp_rpe.compose.models import CompositionScore
from svp_rpe.compose.prompt_renderer import (
    ExternalPromptAdapter,
    resolve_backend_descriptor,
)
from svp_rpe.roundtrip.harness import run_roundtrip
from svp_rpe.roundtrip.models import RoundtripDiagnosis, RoundtripReport


class TightFieldAdherence(BaseModel):
    """1 つの tight 宣言フィールドのコンパイル保持＋演奏保存の判定行。"""

    model_config = ConfigDict(extra="forbid")

    field: str
    sensor: str | None = None
    grip: float | None = None
    compiled_kept: bool  # コンパイル時にプロンプトへ保持された（drop されなかった）か
    roundtrip_diagnosis: RoundtripDiagnosis | None = None
    preserved: bool  # ラウンドトリップ診断が "preserved" か
    source_value: Any = None
    transcribed_value: Any = None
    note: str | None = None


class ScoreAdherenceReport(BaseModel):
    """楽譜準拠レポート。tight 宣言フィールドの保持/非保持を記述する（verdict ではない）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    backend: str  # 解決された control_profile キー（"suno" 等）
    source_id: str
    tight_fields: list[TightFieldAdherence] = Field(default_factory=list)
    compiled_kept_count: int
    preserved_count: int
    total_tight: int


def score_adherence(
    score: CompositionScore,
    report: RoundtripReport,
    *,
    prompt_max_chars: int | None = None,
) -> ScoreAdherenceReport:
    """`score` の tight フィールドが `report` で保たれたかを判定する。

    生成側 backend は `rendering.target_backend` → `control_profile` キーで決定論的に
    解決する（PR1.5 の backend selector を共有）。`report` は決定論 roundtrip でも
    実 Suno corpus take でもよい。
    """

    descriptor = resolve_backend_descriptor(score.rendering.target_backend)
    profile = (score.control_profile or {}).get(descriptor.profile_key) or {}
    tight_field_names = sorted(
        field for field, grip in profile.items() if grip.grip_class == "tight"
    )

    prompt = ExternalPromptAdapter().render(score, max_chars=prompt_max_chars)
    dropped = set(prompt.dropped_elements)
    by_field = {item.field: item for item in report.fields}

    rows: list[TightFieldAdherence] = []
    for field in tight_field_names:
        grip = profile[field]
        observed = by_field.get(field)
        diagnosis = observed.diagnosis if observed is not None else None
        rows.append(
            TightFieldAdherence(
                field=field,
                sensor=grip.sensor or (observed.sensor if observed else None),
                grip=grip.grip,
                compiled_kept=field not in dropped,
                roundtrip_diagnosis=diagnosis,
                preserved=diagnosis == "preserved",
                source_value=observed.source_value if observed else None,
                transcribed_value=observed.transcribed_value if observed else None,
                note=observed.note if observed else "field absent from roundtrip report",
            )
        )

    return ScoreAdherenceReport(
        backend=descriptor.profile_key,
        source_id=report.source_id,
        tight_fields=rows,
        compiled_kept_count=sum(row.compiled_kept for row in rows),
        preserved_count=sum(row.preserved for row in rows),
        total_tight=len(rows),
    )


def run_score_adherence(
    score: CompositionScore,
    *,
    prompt_max_chars: int | None = None,
) -> ScoreAdherenceReport:
    """決定論 roundtrip を回して楽譜準拠レポートを返す。"""

    report = run_roundtrip(score)
    return score_adherence(score, report, prompt_max_chars=prompt_max_chars)


def render_score_adherence_text(report: ScoreAdherenceReport) -> str:
    """CLI 用のコンパクトな markdown テーブルを描画する。"""

    lines = [
        f"# Score Adherence ({report.backend}) - {report.source_id}",
        "",
        (
            f"tight fields: {report.total_tight} | "
            f"compiled-kept: {report.compiled_kept_count}/{report.total_tight} | "
            f"preserved: {report.preserved_count}/{report.total_tight}"
        ),
        "",
        "| field | grip | sensor | compiled_kept | roundtrip | preserved | note |",
        "|---|---:|---|---|---|---|---|",
    ]
    for row in report.tight_fields:
        lines.append(
            "| "
            f"{row.field} | "
            f"{'' if row.grip is None else f'{row.grip:.6g}'} | "
            f"{row.sensor or ''} | "
            f"{row.compiled_kept} | "
            f"{row.roundtrip_diagnosis or ''} | "
            f"{row.preserved} | "
            f"{row.note or ''} |"
        )
    return "\n".join(lines) + "\n"
