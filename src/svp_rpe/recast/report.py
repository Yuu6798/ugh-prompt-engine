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

from typing import Dict, List, Literal, Optional, Sequence

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from svp_rpe.arrange.identity import AnchorDomain
from svp_rpe.arrange.models import JsonValue, PreservationMode
from svp_rpe.arrange.observe import (
    Determination,
    ObservationAdherenceStatus,
    ObservationReport,
    SensorRecord,
)
from svp_rpe.arrange.package import PerformancePackage
from svp_rpe.arrange.pathsafe import PathConfinementError, validate_relative_locator
from svp_rpe.recast.experimental import ExperimentalAnchorEntry
from svp_rpe.recast.report_base import RecastReportModel

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


class RecastReportTake(RecastReportModel):
    """観測対象の take（生成音声）の provenance 記録。"""

    path: str
    sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("path")
    @classmethod
    def _validate_relative_path(cls, value: str) -> str:
        """`path` は project 相対の locator（Codex P2, #210 round 12 指摘15）:
        絶対パスや `../`/`..\\` traversal を schema 検証なしで受理すると、
        手編集/別経路の report が project 外のファイルを「証明済み take」と
        して `recast_summary.md` に表示できてしまう（sha256 突合は行われる
        が、そもそも project 外を指すパス文字列自体を拒否すべき）。
        `arrange/pathsafe.py::validate_relative_locator`（lexical・base 不要
        — filesystem へ触れずに絶対パス/net-upward `..` を判定する）を適用
        する。`build_recast_report`（発行時）は常に `os.path.relpath` で
        project_dir 相対の文字列を渡すため、正常な発行経路は自然にこの
        validator を通過する（builder→dump→validate の読み戻し規律）。"""
        try:
            validate_relative_locator(value)
        except PathConfinementError as exc:
            raise ValueError(
                f"RecastReportTake.path must be a project-relative path without "
                f"parent traversal: {value!r} ({exc})"
            ) from exc
        return value


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
    # M4c (Design Memo M4 §5, additive): melody 等の experimental anchor
    # 翻訳結果。**本会計（`coverage` の verified/violated/not_observed 分母）
    # には一切算入しない**——`_tally_anchor_coverage`/
    # `_validate_coverage_matches_anchors` は `self.anchors`（main）のみを
    # 見る。旧レポート（このフィールドを持たない JSON）は default（空リスト）
    # で読み戻せる。DD-8: 空のときは serialize に現れない（下記
    # `_omit_empty_experimental_anchors` 参照）——既存 golden fixture の
    # バイト不変を壊さない。
    experimental_anchors: List[ExperimentalAnchorEntry] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _omit_empty_experimental_anchors(self, handler: SerializerFunctionWrapHandler) -> Dict:
        """DD-8: `experimental_anchors` が空のときは出力 dict に現れない
        （`model_dump`/`model_dump_json` いずれの呼び出し経路でも一貫させる
        単一箇所——呼び出し側で個別に pop する必要がない）。melody anchor が
        1 件でもあれば通常どおりリストとして出力する。"""
        data = handler(self)
        if not self.experimental_anchors:
            data.pop("experimental_anchors", None)
        return data

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

    @model_validator(mode="after")
    def _validate_unique_anchor_ids(self) -> "RecastReport":
        """`arrange/observe.py:ObservationReport._validate_unique_anchor_ids`
        / `arrange/identity.py:IdentityManifest._validate_unique_anchor_ids`
        と同型（Codex P2, #210 round 11 指摘14）: identity/observation の
        両 sidecar が持つ「anchor_id は重複しない」不変条件を `RecastReport`
        だけが欠いていた。重複を許すと、例えば harmony を 2 行（両方
        `coverage="verified"`）に複製した report が `coverage` 集計
        （`_tally_anchor_coverage`）上は辻褄が合う（2 verified）ため、上の
        `_validate_coverage_matches_anchors` を素通りしてしまう —
        「harmony が 2 回観測された」という虚偽の被覆を検出できない。
        `build_recast_report` は `report.anchors`（`ObservationReport` 側で
        既に一意性が保証された集合）から 1 対 1 で構築するため、正常な
        発行経路は自然にこの validator を通過する（builder→dump→validate
        の読み戻し規律）。"""
        seen: set[str] = set()
        duplicates: set[str] = set()
        for anchor in self.anchors:
            if anchor.anchor_id in seen:
                duplicates.add(anchor.anchor_id)
            seen.add(anchor.anchor_id)
        if duplicates:
            raise ValueError(
                f"duplicate anchor_id(s) in recast report: {', '.join(sorted(duplicates))}"
            )
        return self

    @model_validator(mode="after")
    def _validate_unique_experimental_anchor_ids(self) -> "RecastReport":
        """R2-4 (Codex round2 P2): `experimental_anchors`（M4c、本会計とは別集合
        ——会計分離）内の `anchor_id` にも一意性を強制する。`_validate_unique_
        anchor_ids`（本会計 `anchors` 側）と同型の読み戻し安全網——複製行が
        虚偽の被覆・虚偽の重複観測を作りうるという同じ不変条件が experimental
        側にも当てはまる（こちらは coverage 集計の対象外だが、同一 anchor_id
        の複製行がそのまま `recast_summary.md` の Experimental anchors 節に
        並ぶこと自体が誤った観測記録になる）。`build_recast_report` は
        `collect_melody_experimental_anchors` が manifest anchor 宣言順で
        重複なく組んだ entry 列をそのまま転記するため、正常な発行経路は
        自然にこの validator を通過する（builder→dump→validate の読み戻し
        規律）。"""
        seen: set[str] = set()
        duplicates: set[str] = set()
        for entry in self.experimental_anchors:
            if entry.anchor_id in seen:
                duplicates.add(entry.anchor_id)
            seen.add(entry.anchor_id)
        if duplicates:
            raise ValueError(
                "duplicate anchor_id(s) in recast report experimental_anchors: "
                f"{', '.join(sorted(duplicates))}"
            )
        return self

    @model_validator(mode="after")
    def _validate_no_cross_list_anchor_id_overlap(self) -> "RecastReport":
        """R4-5 (Codex round4 P2): `anchors`（本会計）と `experimental_anchors`
        （M4c、会計分離された別集合）の間でも anchor_id が重複してはならない
        （読み戻し安全網の完成形——`_validate_unique_anchor_ids`/
        `_validate_unique_experimental_anchor_ids` はそれぞれのリスト**内**の
        一意性しか見ないため、同じ anchor_id が両方のリストに 1 件ずつ現れる
        ケースはどちらの validator も検出できない）。同じ anchor が本会計と
        experimental の両方に載ると、`recast_summary.md` が同一 anchor を
        Coverage 集計対象/対象外の両方として二重に報告し、会計分離の趣旨
        （`ExperimentalAnchorEntry` docstring 参照）そのものが崩れる。

        正常な発行経路（`build_recast_report`）は、experimental 側
        （`collect_melody_experimental_anchors`、axis_policy opt-in — DD-3）
        と本会計側（`resolve_main_observation_anchor_scope` が axis_policy
        付き melody anchor を観測スコープから除外する）が互いに素な集合に
        なるよう既に配線されているため、正常発行はこの validator を自然に
        通過する（builder→dump→validate の読み戻し規律）。"""
        main_ids = {anchor.anchor_id for anchor in self.anchors}
        experimental_ids = {entry.anchor_id for entry in self.experimental_anchors}
        overlap = main_ids & experimental_ids
        if overlap:
            raise ValueError(
                "anchor_id(s) present in both recast report anchors (main accounting) "
                f"and experimental_anchors: {', '.join(sorted(overlap))}"
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
    observation_anchors: Sequence[str] = (),
    experimental_anchors: Sequence[ExperimentalAnchorEntry] = (),
) -> RecastReport:
    """`ObservationReport`（`observe_generated_artifact` が組み立てた計器出力）+
    `package`（`anchor_statuses[].requested_mode` 由来の policy_mode）から
    `RecastReport` を構築する（純粋関数、ディスクへの副作用なし — publish は
    呼び出し側 CLI の責務）。

    anchor の順序は `report.anchors`（= manifest.anchors の宣言順）をそのまま
    保つ — 決定論契約（同一 checkout + 同一観測結果なら常に同じ順序）。

    `observation_anchors`（`RecastProject.observation.anchors`、PR6）が非空の
    場合、`report.anchors` をその集合に絞り込んでから `RecastReport` を組み立てる
    — coverage 集計もこの絞り込み後の部分集合に対して行う。空（既定）は
    「絞り込みなし＝全 anchor」という `ObservationConfig.anchors` の既存契約
    （schema 側の docstring 参照）をそのまま踏襲する。未知 anchor id を渡した
    場合の fail-closed 検証は呼び出し側の責務（`recast/plan.py` の
    `build_recast_plan_artifacts` が manifest ロード直後に行う — 本関数は
    純粋なフィルタリングのみで、ここでは検証しない）。

    `experimental_anchors`（M4c、additive）は呼び出し側が別途
    `recast/experimental.py:collect_melody_experimental_anchors` で組み立てた
    entry 列をそのまま転記するだけ——`report.anchors`/`coverage`（本会計）とは
    完全に独立で、ここでのフィルタリング・集計の対象にならない（会計分離）。
    """
    policy_by_anchor: Dict[str, Optional[PreservationMode]] = {
        status.anchor_id: status.requested_mode for status in package.anchor_statuses
    }
    allowed_anchor_ids = set(observation_anchors) if observation_anchors else None

    anchors: List[RecastReportAnchor] = []
    for observation in report.anchors:
        if allowed_anchor_ids is not None and observation.anchor_id not in allowed_anchor_ids:
            continue
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
        experimental_anchors=list(experimental_anchors),
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
    if report.experimental_anchors:
        # M4c (DD-8): melody anchor が 1 件以上あるときだけ節を出す（空のときは
        # 何も描画しない — バイト不変契約）。会計分離を明文化: この節は
        # 上の Coverage 集計に一切寄与しない。
        lines += [
            "",
            "## Experimental anchors (melody)",
            "",
            "比較器の evidence を契約の axis_policy へ機械的に写した実験的な観測です"
            "（本レポートの Coverage 集計には含まれません — 単一の同一性スコアは"
            "出しません）。",
            "",
            "| anchor_id | domain | adherence_status | axes | axis_policy | reasons |",
            "|---|---|---|---|---|---|",
        ]
        for entry in report.experimental_anchors:
            axes_text = ", ".join(
                f"{axis}={value:.3f}" if value is not None else f"{axis}=-"
                for axis, value in sorted(entry.axes.items())
            )
            policy_text = ", ".join(f"{axis}={mode}" for axis, mode in sorted(entry.axis_policy.items()))
            reasons_text = "; ".join(entry.reasons) if entry.reasons else "-"
            lines.append(
                f"| {entry.anchor_id} | {entry.domain} | {entry.adherence_status} | "
                f"{axes_text or '-'} | {policy_text or '-'} | {reasons_text} |"
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
