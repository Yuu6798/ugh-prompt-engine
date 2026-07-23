"""`svp_rpe.recast.report` の純ロジックテスト（PR5、非 slow）。

`build_recast_report`/`render_recast_summary_markdown`/`_coverage_for` は
ディスク I/O を持たない純関数のため、`ObservationReport`/`PerformancePackage`
の最小合成フィクスチャで検証する（実抽出・実 CLI 実行は
`tests/test_recast_ingest_report.py` の @pytest.mark.slow 側が担う）。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from svp_rpe.arrange.observe import (
    OBSERVATION_REPORT_SCHEMA_VERSION,
    AnchorObservation,
    GeneratedArtifactRef,
    ObservationReport,
)
from svp_rpe.arrange.package import PackageAnchorStatus, PerformancePackage
from svp_rpe.recast.report import (
    RECAST_REPORT_SCHEMA_VERSION,
    RecastReport,
    RecastReportCoverage,
    RecastReportTake,
    _coverage_for,
    build_recast_report,
    render_recast_summary_markdown,
)


def _anchor_observation(
    anchor_id: str,
    domain: str,
    adherence_status: str,
    determination: str,
    *,
    sensor_available: bool = True,
) -> AnchorObservation:
    return AnchorObservation(
        anchor_id=anchor_id,
        domain=domain,
        sensor={"name": f"{anchor_id}_sensor", "available": sensor_available},
        measurements={"foo": 1},
        adherence_status=adherence_status,
        determination=determination,
    )


def _observation_report(anchors: list[AnchorObservation]) -> ObservationReport:
    return ObservationReport(
        schema_version=OBSERVATION_REPORT_SCHEMA_VERSION,
        work_id="w",
        package_sha256="0" * 64,
        generated_artifact=GeneratedArtifactRef(
            path="builds/takes/v@b/take-01.wav", sha256="1" * 64
        ),
        anchors=anchors,
    )


def _package(anchor_statuses: list[PackageAnchorStatus]) -> PerformancePackage:
    # `build_recast_report` only reads `.anchor_statuses` — `model_construct`
    # (validation-skipping) avoids fabricating the rest of the required
    # PerformancePackage fields this unit test has no use for.
    return PerformancePackage.model_construct(anchor_statuses=anchor_statuses)


# --- _coverage_for -----------------------------------------------------------


def test_coverage_for_maps_all_four_d1_vocabulary_words() -> None:
    """被覆写像は D-1 の 4 語彙全てを受ける契約（docstring 参照）— 現行の
    `ObservationAdherenceStatus` が発行できるのは 2 語彙のみだが、
    `changed_within_policy`/`changed_outside_policy` も既に正しく写像できる
    ことを検証する（将来 observation-report/0.2 が到達可能にする時への備え）。
    """
    assert _coverage_for("preserved") == "verified"
    assert _coverage_for("changed_within_policy") == "verified"
    assert _coverage_for("changed_outside_policy") == "violated"
    assert _coverage_for("not_observed") == "not_observed"


def test_coverage_for_rejects_unknown_adherence_status() -> None:
    with pytest.raises(ValueError):
        _coverage_for("sensor_blind")


# --- build_recast_report -------------------------------------------------------


def test_build_recast_report_maps_policy_mode_and_tallies_coverage() -> None:
    report = _observation_report(
        [
            _anchor_observation("harmony", "harmony", "preserved", "exact_match"),
            _anchor_observation("structure", "structure", "not_observed", "deferred"),
            _anchor_observation(
                "lyrics", "lyrics", "not_observed", "no_sensor", sensor_available=False
            ),
        ]
    )
    package = _package(
        [
            PackageAnchorStatus.model_construct(anchor_id="harmony", requested_mode="hard"),
            PackageAnchorStatus.model_construct(anchor_id="structure", requested_mode="hard"),
            PackageAnchorStatus.model_construct(anchor_id="lyrics", requested_mode="free"),
        ]
    )

    recast_report = build_recast_report(
        project_id="p",
        variant="v",
        backend="b",
        package=package,
        report=report,
        take_path_relative="builds/takes/v@b/take-01.wav",
        take_sha256="2" * 64,
    )

    assert [a.anchor_id for a in recast_report.anchors] == ["harmony", "structure", "lyrics"]
    assert recast_report.anchors[0].policy_mode == "hard"
    assert recast_report.anchors[0].coverage == "verified"
    assert recast_report.anchors[1].policy_mode == "hard"
    assert recast_report.anchors[1].coverage == "not_observed"
    assert recast_report.anchors[2].policy_mode == "free"
    assert recast_report.anchors[2].coverage == "not_observed"
    assert recast_report.coverage == RecastReportCoverage(verified=1, violated=0, not_observed=2)
    assert recast_report.identity_assessment.enabled is False
    assert recast_report.work_id == "w"
    assert recast_report.package_sha256 == "0" * 64
    assert recast_report.take.path == "builds/takes/v@b/take-01.wav"
    assert recast_report.take.sha256 == "2" * 64


def test_build_recast_report_policy_mode_none_when_anchor_missing_from_package() -> None:
    """`package.anchor_statuses` に対応する anchor_id が無い（あり得ない状態
    だが fail-closed に None を出す方が silent な誤帰属より安全）場合、
    `policy_mode` は `None`（`AnchorPlanEntry` と同じ optional 規約）。"""
    report = _observation_report(
        [_anchor_observation("harmony", "harmony", "not_observed", "deferred")]
    )
    package = _package([])

    recast_report = build_recast_report(
        project_id="p",
        variant="v",
        backend="b",
        package=package,
        report=report,
        take_path_relative="take.wav",
        take_sha256="a" * 64,
    )
    assert recast_report.anchors[0].policy_mode is None


# --- render_recast_summary_markdown ---------------------------------------------


def test_render_recast_summary_markdown_is_deterministic_and_names_no_single_score() -> None:
    report = _observation_report(
        [_anchor_observation("harmony", "harmony", "preserved", "exact_match")]
    )
    package = _package(
        [PackageAnchorStatus.model_construct(anchor_id="harmony", requested_mode="hard")]
    )
    recast_report = build_recast_report(
        project_id="p",
        variant="v",
        backend="b",
        package=package,
        report=report,
        take_path_relative="take.wav",
        take_sha256="a" * 64,
    )
    md_a = render_recast_summary_markdown(recast_report)
    md_b = render_recast_summary_markdown(recast_report)
    assert md_a == md_b
    assert "harmony" in md_a
    # D-1: no single identity score is ever rendered.
    assert "enabled: false" in md_a


# --- schema fail-closed ----------------------------------------------------------


def test_recast_report_rejects_unknown_top_level_key() -> None:
    with pytest.raises(ValidationError):
        RecastReport.model_validate(
            {
                "schema_version": RECAST_REPORT_SCHEMA_VERSION,
                "project_id": "p",
                "variant": "v",
                "backend": "b",
                "work_id": "w",
                "take": {"path": "take.wav", "sha256": "0" * 64},
                "package_sha256": "0" * 64,
                "anchors": [],
                "coverage": {"verified": 0, "violated": 0, "not_observed": 0},
                "identity_assessment": {"enabled": False},
                "unexpected_field": True,
            }
        )


def test_recast_report_take_requires_64_hex_sha256() -> None:
    RecastReportTake(path="take.wav", sha256="0" * 64)
    with pytest.raises(ValidationError):
        RecastReportTake(path="take.wav", sha256="not-a-hash")


def test_recast_report_schema_version_defaults_to_current() -> None:
    """`recast/plan.py:RecastPlan` / `recast/state.py:RecastStateFile` と同じ
    recast-module 規約（generated-output artifact は default を持つ — 手書き/
    改竄された author 向け入力の `IdentityManifest`/`ObservationReport` とは
    異なる posture）: 省略時は現行バージョンへ default 補完される。"""
    report = RecastReport.model_validate(
        {
            "project_id": "p",
            "variant": "v",
            "backend": "b",
            "work_id": "w",
            "take": {"path": "take.wav", "sha256": "0" * 64},
            "package_sha256": "0" * 64,
            "anchors": [],
            "coverage": {"verified": 0, "violated": 0, "not_observed": 0},
            "identity_assessment": {"enabled": False},
        }
    )
    assert report.schema_version == RECAST_REPORT_SCHEMA_VERSION


# --- coverage/anchors consistency validator (Codex P2, #210 round 5 指摘7) -----


def test_recast_report_round_trip_preserves_consistent_coverage() -> None:
    """`build_recast_report` が発行する report は dump→`model_validate` の
    読み戻しでも validator を素通りする（builder→dump→validate の読み戻し
    規律 — 正常発行経路が誤って弾かれないことの機械 assert）。"""
    report = _observation_report(
        [
            _anchor_observation("harmony", "harmony", "preserved", "exact_match"),
            _anchor_observation("structure", "structure", "not_observed", "deferred"),
        ]
    )
    package = _package(
        [
            PackageAnchorStatus.model_construct(anchor_id="harmony", requested_mode="hard"),
            PackageAnchorStatus.model_construct(anchor_id="structure", requested_mode="hard"),
        ]
    )
    recast_report = build_recast_report(
        project_id="p",
        variant="v",
        backend="b",
        package=package,
        report=report,
        take_path_relative="take.wav",
        take_sha256="a" * 64,
    )
    round_tripped = RecastReport.model_validate(recast_report.model_dump(mode="json"))
    assert round_tripped == recast_report


def test_recast_report_rejects_coverage_inconsistent_with_anchor_rows() -> None:
    """`coverage` が `anchors[].coverage` の実集計と食い違う（手編集/別生成の
    report を想定 — ここでは `violated` を実際には 0 件なのに 1 件あると
    偽装する）場合、読み戻し（`model_validate`）は fail-closed で拒否する。"""
    payload = {
        "schema_version": RECAST_REPORT_SCHEMA_VERSION,
        "project_id": "p",
        "variant": "v",
        "backend": "b",
        "work_id": "w",
        "take": {"path": "take.wav", "sha256": "0" * 64},
        "package_sha256": "0" * 64,
        "anchors": [
            {
                "anchor_id": "harmony",
                "domain": "harmony",
                "policy_mode": "hard",
                "adherence_status": "not_observed",
                "determination": "deferred",
                "sensor": {"name": "chord_sequence_match", "available": True},
                "measurements": {},
                "coverage": "not_observed",
            }
        ],
        # 実際の anchor 行は not_observed=1 のみだが、coverage は
        # violated=1（ずらした値）を主張する — 不整合。
        "coverage": {"verified": 0, "violated": 1, "not_observed": 0},
        "identity_assessment": {"enabled": False},
    }
    with pytest.raises(ValidationError):
        RecastReport.model_validate(payload)


def _harmony_anchor_payload(*, adherence_status: str, determination: str, coverage: str) -> dict:
    return {
        "anchor_id": "harmony",
        "domain": "harmony",
        "policy_mode": "hard",
        "adherence_status": adherence_status,
        "determination": determination,
        "sensor": {"name": "chord_sequence_match", "available": True},
        "measurements": {},
        "coverage": coverage,
    }


def test_recast_report_accepts_anchor_row_whose_coverage_matches_adherence_status() -> None:
    """正常な読み戻し: 行の `coverage` が `adherence_status` の写像結果と
    一致していれば `model_validate` は pass する（round 6 の行単位 validator
    が正常経路を誤って弾かないことの機械 assert）。"""
    payload = {
        "schema_version": RECAST_REPORT_SCHEMA_VERSION,
        "project_id": "p",
        "variant": "v",
        "backend": "b",
        "work_id": "w",
        "take": {"path": "take.wav", "sha256": "0" * 64},
        "package_sha256": "0" * 64,
        "anchors": [
            _harmony_anchor_payload(
                adherence_status="not_observed", determination="deferred", coverage="not_observed"
            )
        ],
        "coverage": {"verified": 0, "violated": 0, "not_observed": 1},
        "identity_assessment": {"enabled": False},
    }
    report = RecastReport.model_validate(payload)
    assert report.anchors[0].coverage == "not_observed"


def test_recast_report_rejects_anchor_row_coverage_forged_independent_of_status() -> None:
    """Codex P2（#210 round 6 指摘8）: 行の `coverage` が `adherence_status`
    と独立に偽装される（`adherence_status="not_observed"` なのに
    `coverage="verified"`）場合、上位の集計（`RecastReport.coverage`）だけ
    辻褄を合わせても（round 5 の集計 validator は per-row `coverage` の生値
    をそのまま合計するため、この偽装 1 件だけなら `verified: 1` の宣言と
    一致してしまう）、行単位の写像 validator が独立に検出して拒否する。"""
    payload = {
        "schema_version": RECAST_REPORT_SCHEMA_VERSION,
        "project_id": "p",
        "variant": "v",
        "backend": "b",
        "work_id": "w",
        "take": {"path": "take.wav", "sha256": "0" * 64},
        "package_sha256": "0" * 64,
        "anchors": [
            _harmony_anchor_payload(
                adherence_status="not_observed", determination="deferred", coverage="verified"
            )
        ],
        # 集計側は偽装した行の coverage（"verified"）とちょうど辻褄が合う
        # よう仕組んである — round 5 の集計 validator 単体では通過し得る。
        "coverage": {"verified": 1, "violated": 0, "not_observed": 0},
        "identity_assessment": {"enabled": False},
    }
    with pytest.raises(ValidationError):
        RecastReport.model_validate(payload)
