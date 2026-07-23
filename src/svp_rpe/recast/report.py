"""RecastReport: `svprpe recast ingest` の観測段が発行する work 単位レポート（PR5）。

`ObservationReport`（`arrange/observe.py`, AR4）は 1 anchor ずつの生観測を
記録する計器 sidecar。本モジュールはそれを (variant, backend) 実行の文脈
（take の provenance / package の同一性 / anchor ごとの保持方針
`policy_mode`）と組み合わせ、被覆（coverage）集計を添えた
`recast_report.json`（機械可読）+ `recast_summary.md`（人間可読）の 1 組へ
束ねる。

D-1 の裁定を継承する: **単一の同一性スコアは出さない**。`identity_assessment`
は `{"enabled": false}` の予約フィールドのみ — 将来 Design Memo が閾値付き
判定を定義するまで、複数 anchor の観測を 1 個のスコアへ縮約しない。

被覆写像（`_ADHERENCE_TO_COVERAGE`）は D-1 の 4 語彙
（`preserved` / `changed_within_policy` / `changed_outside_policy` /
`not_observed`）全てを受ける契約でスキーマ・マッピング関数を用意するが、
`ObservationReport.anchors[].adherence_status` の型（`ObservationAdherenceStatus`
= `Literal["preserved", "not_observed"]`）自体は `arrange/observe.py` の D-1
narrowing をそのまま継承する — 現行の計器が実際に発行できるのはこの 2 語彙
のみで、`changed_within_policy`/`changed_outside_policy` は将来の閾値
Design Memo が `observation-report/0.2` を定義するまで到達不能（マッピング
関数だけを 4 語彙対応にしておくことで、その時点でここを書き換えずに済む）。

決定論契約: `recast_report.json` はタイムスタンプ・絶対パスを含まない
（他 recast 成果物 — `recast_plan.json` 等 — と同じ契約）。`take.path` は
呼び出し側が project 相対パスとして渡す。
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from svp_rpe.arrange.identity import AnchorDomain
from svp_rpe.arrange.models import JsonValue, PreservationMode
from svp_rpe.arrange.observe import (
    Determination,
    ObservationAdherenceStatus,
    ObservationReport,
    SensorRecord,
)
from svp_rpe.arrange.package import PerformancePackage

RECAST_REPORT_SCHEMA_VERSION = "recast-report/0.1"
RECAST_REPORT_FILENAME = "recast_report.json"
RECAST_SUMMARY_FILENAME = "recast_summary.md"

_SHA256_PATTERN = r"^[0-9a-f]{64}$"

CoverageStatus = Literal["verified", "violated", "not_observed"]

# D-1 の 4 語彙を受ける被覆写像（docstring 参照）。ここに列挙した語彙以外の
# `adherence_status` はプログラミングエラー（未知の D-1 分岐）として
# fail-closed に拒否する（`_coverage_for` 参照）。
_ADHERENCE_TO_COVERAGE: Dict[str, CoverageStatus] = {
    "preserved": "verified",
    "changed_within_policy": "verified",
    "changed_outside_policy": "violated",
    "not_observed": "not_observed",
}


def _coverage_for(adherence_status: str) -> CoverageStatus:
    try:
        return _ADHERENCE_TO_COVERAGE[adherence_status]
    except KeyError:
        raise ValueError(
            f"unknown adherence_status for recast report coverage mapping: "
            f"{adherence_status!r} (expected one of {sorted(_ADHERENCE_TO_COVERAGE)})"
        ) from None


class RecastReportModel(BaseModel):
    """recast-report 側スキーマの共通基底。未知 key を拒否する。"""

    model_config = ConfigDict(extra="forbid")


class RecastReportTake(RecastReportModel):
    """観測対象の take（生成音声）の provenance 記録。"""

    path: str
    sha256: str = Field(pattern=_SHA256_PATTERN)


class RecastReportAnchor(RecastReportModel):
    """1 anchor 分の観測 + 被覆判定（`ObservationReport.anchors[]` を
    `package.anchor_statuses[].requested_mode`（保持方針、contract 由来）と
    結合したもの）。"""

    anchor_id: str
    domain: AnchorDomain
    policy_mode: Optional[PreservationMode] = None
    adherence_status: ObservationAdherenceStatus
    determination: Determination
    sensor: SensorRecord
    measurements: Dict[str, JsonValue] = Field(default_factory=dict)
    coverage: CoverageStatus

    @model_validator(mode="after")
    def _validate_coverage_matches_adherence_status(self) -> "RecastReportAnchor":
        """`coverage` は `_coverage_for(adherence_status)` の写像結果と
        一致しなければならない（Codex P2, #210 round 6 指摘8）: 手編集や
        別経路で生成された行が、例えば `adherence_status="not_observed"`
        なのに `coverage="verified"` を主張する — 上位の `RecastReport.
        _validate_coverage_matches_anchors`（round 5 対応）は集計値の
        一致しか見ないため、複数行の偽装が辻褄合わせで打ち消し合うと
        素通りしてしまう。行単位の写像そのものを fail-closed に強制する。
        `build_recast_report` は既に `_coverage_for` の戻り値をそのまま
        `coverage` に渡しているため、正常な発行経路は自然にこの validator
        を通過する（builder→dump→validate の読み戻し規律）。"""
        expected = _coverage_for(self.adherence_status)
        if self.coverage != expected:
            raise ValueError(
                f"RecastReportAnchor '{self.anchor_id}': coverage does not match "
                f"adherence_status={self.adherence_status!r}: declared coverage="
                f"{self.coverage!r}, expected {expected!r}"
            )
        return self


class RecastReportCoverage(RecastReportModel):
    """anchor 単位の被覆集計。"""

    verified: int
    violated: int
    not_observed: int


class IdentityAssessment(RecastReportModel):
    """予約フィールド: 単一の同一性スコアは本 PR の管轄外（D-1 継承）。
    将来 Design Memo が閾値付き判定を定義するまで `enabled=false` のみ。

    `enabled` は `Literal[False]`（Codex P2, #210 round 8 指摘10）: `bool` の
    ままだと手編集/別経路の report が `enabled: true` を主張してもそのまま
    受理されてしまうが、`render_recast_summary_markdown` は無条件で
    `enabled: false` 固定文言を描画するため、report と summary が矛盾した
    ままレビューへ出回る。ツールが実際には計算していない同一性評価を
    掲示できないよう、読み込み時に fail-closed で強制する（WI4 の閾値
    Design Memo が `enabled=true` を許す新スキーマを定義するまで不変）。"""

    enabled: Literal[False] = False


def _tally_anchor_coverage(anchors: List[RecastReportAnchor]) -> RecastReportCoverage:
    """`anchors[].coverage` から 3 カウント（verified/violated/not_observed）を
    集計する — `build_recast_report`（発行時）と `RecastReport` の読み戻し
    validator（下記）の両方が同じ集計ロジックを共有する single source
    （Codex P2, #210 round 5 指摘7: 手編集/別生成の report で `coverage` が
    per-anchor 値と独立に食い違うのを防ぐには、両者を同一関数で計算するのが
    最も drift しにくい）。"""
    counts: Dict[CoverageStatus, int] = {"verified": 0, "violated": 0, "not_observed": 0}
    for anchor in anchors:
        counts[anchor.coverage] += 1
    return RecastReportCoverage(**counts)


class RecastReport(RecastReportModel):
    """`svprpe recast ingest` の観測段が発行する recast-report/0.1 本体。"""

    schema_version: Literal["recast-report/0.1"]
    project_id: str
    variant: str
    backend: str
    work_id: str
    take: RecastReportTake
    package_sha256: str = Field(pattern=_SHA256_PATTERN)
    anchors: List[RecastReportAnchor]
    coverage: RecastReportCoverage
    identity_assessment: IdentityAssessment = Field(default_factory=IdentityAssessment)

    @model_validator(mode="after")
    def _validate_coverage_matches_anchors(self) -> "RecastReport":
        """`coverage` は `anchors[].coverage` から再計算した値と一致しなければ
        ならない（Codex P2, #210 round 5 指摘7）: 手編集や別経路で生成された
        report が `violated: 0` を主張しつつ実際には `violated` な anchor 行を
        持つ、といった不整合を summary の「次の一手」選択（`render_recast_
        summary_markdown` は `coverage` だけを見て分岐する）が素通ししてしまう
        のを防ぐ。構築時（`build_recast_report`）は常にこの再計算結果
        そのものを `coverage` として渡すため、正常な発行経路は自然にこの
        validator を通過する（builder→dump→validate の読み戻し規律）。"""
        expected = _tally_anchor_coverage(self.anchors)
        if self.coverage != expected:
            raise ValueError(
                "RecastReport.coverage does not match anchors[].coverage tally: "
                f"declared={self.coverage!r}, recomputed={expected!r}"
            )
        return self


def build_recast_report(
    *,
    project_id: str,
    variant: str,
    backend: str,
    package: PerformancePackage,
    report: ObservationReport,
    take_path_relative: str,
    take_sha256: str,
) -> RecastReport:
    """`ObservationReport`（`observe_generated_artifact` が組み立てた計器出力）+
    `package`（`anchor_statuses[].requested_mode` 由来の policy_mode）から
    `RecastReport` を構築する（純粋関数、ディスクへの副作用なし — publish は
    呼び出し側 CLI の責務）。

    anchor の順序は `report.anchors`（= manifest.anchors の宣言順）をそのまま
    保つ — 決定論契約（同一 checkout + 同一観測結果なら常に同じ順序）。
    """
    policy_by_anchor: Dict[str, Optional[PreservationMode]] = {
        status.anchor_id: status.requested_mode for status in package.anchor_statuses
    }

    anchors: List[RecastReportAnchor] = []
    for observation in report.anchors:
        coverage = _coverage_for(observation.adherence_status)
        anchors.append(
            RecastReportAnchor(
                anchor_id=observation.anchor_id,
                domain=observation.domain,
                policy_mode=policy_by_anchor.get(observation.anchor_id),
                adherence_status=observation.adherence_status,
                determination=observation.determination,
                sensor=observation.sensor,
                measurements=observation.measurements,
                coverage=coverage,
            )
        )

    return RecastReport(
        schema_version=RECAST_REPORT_SCHEMA_VERSION,
        project_id=project_id,
        variant=variant,
        backend=backend,
        work_id=report.work_id,
        take=RecastReportTake(path=take_path_relative, sha256=take_sha256),
        package_sha256=report.package_sha256,
        anchors=anchors,
        coverage=_tally_anchor_coverage(anchors),
        identity_assessment=IdentityAssessment(enabled=False),
    )


def render_recast_summary_markdown(report: RecastReport) -> str:
    """`RecastReport` を人間可読の Markdown へ描画する（決定論・タイムスタンプ
    なし）。anchor 別表 + 被覆集計 + 次の一手のみ — 単一スコアは出さない。"""
    lines: List[str] = [
        f"# Recast Report: {report.project_id} / {report.variant}@{report.backend}",
        "",
        f"- work_id: {report.work_id}",
        f"- take: {report.take.path} (sha256={report.take.sha256})",
        f"- package_sha256: {report.package_sha256}",
        "",
        "## Anchors",
        "",
        "| anchor_id | domain | policy_mode | adherence_status | determination | coverage |",
        "|---|---|---|---|---|---|",
    ]
    for anchor in report.anchors:
        lines.append(
            f"| {anchor.anchor_id} | {anchor.domain} | {anchor.policy_mode or '-'} | "
            f"{anchor.adherence_status} | {anchor.determination} | {anchor.coverage} |"
        )
    lines += [
        "",
        "## Coverage",
        "",
        f"- verified: {report.coverage.verified}",
        f"- violated: {report.coverage.violated}",
        f"- not_observed: {report.coverage.not_observed}",
        "",
        "## Identity assessment",
        "",
        "- enabled: false (単一の同一性スコアは本レポートの管轄外 — 予約フィールド)",
        "",
        "## Next step",
        "",
    ]
    if report.coverage.violated > 0:
        lines.append(
            "- violated な anchor があります。arrangement/identity 契約を見直してください。"
        )
    elif report.coverage.not_observed > 0:
        lines.append(
            "- not_observed な anchor があります（計器未配線、または exact match "
            "不成立のいずれか — measurements を確認してください。verdict ではありません）。"
        )
    else:
        lines.append("- 全 anchor が verified です。")
    return "\n".join(lines) + "\n"
