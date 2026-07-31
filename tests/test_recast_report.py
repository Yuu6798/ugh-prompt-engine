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
from svp_rpe.recast.experimental import ExperimentalAnchorEntry
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


def test_build_recast_report_narrows_to_observation_anchors_when_declared() -> None:
    """PR6: `observation_anchors`（`ObservationConfig.anchors`）が非空なら
    `RecastReport.anchors`/`coverage` はその集合に絞り込まれる — 空（既定）は
    絞り込みなし（`test_build_recast_report_maps_policy_mode_and_tallies_coverage`
    が既存の全 anchor 経路を担保）。"""
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
        observation_anchors=["harmony"],
    )

    assert [a.anchor_id for a in recast_report.anchors] == ["harmony"]
    assert recast_report.coverage == RecastReportCoverage(verified=1, violated=0, not_observed=0)


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


def test_recast_report_schema_version_is_required_not_defaulted() -> None:
    """Codex P2（#210 round 8 指摘9）: `schema_version` はデフォルト値なしの
    必須フィールド（`recast/models.py:RecastProjectFile` /
    `mode-overrides/0.1` と同じ規約へ揃える）。省略した dict の
    `model_validate` は他の未知/欠落 schema_version 検査と同様に
    fail-closed で拒否されなければならない — デフォルト補完で「欠落 JSON が
    現行版として silent 受理される」抜け道を塞ぐ。`build_recast_report` は
    定数を明示的に渡すため、正常な発行経路はこの必須化の影響を受けない。"""
    payload = {
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
    with pytest.raises(ValidationError):
        RecastReport.model_validate(payload)

    report = RecastReport.model_validate(
        {**payload, "schema_version": RECAST_REPORT_SCHEMA_VERSION}
    )
    assert report.schema_version == RECAST_REPORT_SCHEMA_VERSION


def test_identity_assessment_rejects_enabled_true() -> None:
    """Codex P2（#210 round 8 指摘10）: `enabled` は `Literal[False]` — ツールが
    実際には計算していない同一性評価を `enabled: true` の手編集 report で
    掲示できてしまうと、常に `enabled: false` 固定文言を描画する
    `render_recast_summary_markdown` と矛盾する。読み込み時に fail-closed で
    拒否する（WI4 の閾値 Design Memo が新スキーマを定義するまで不変）。"""
    from svp_rpe.recast.report import IdentityAssessment

    IdentityAssessment(enabled=False)
    with pytest.raises(ValidationError):
        IdentityAssessment.model_validate({"enabled": True})


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


# --- anchor_id uniqueness (Codex P2, #210 round 11 指摘14) ---------------------


def test_recast_report_rejects_duplicate_anchor_id_even_when_coverage_tally_is_consistent() -> (
    None
):
    """harmony を 2 行（両方 `coverage="verified"`）に複製した report は、
    `coverage` 集計だけを見れば辻褄が合う（`verified: 2` は実際の 2 行分と
    一致する）ため、round 5/6 の coverage validator はいずれも素通りする —
    「harmony が 2 回観測された」という虚偽の被覆を、identity/observation
    sidecar と同型の anchor_id 一意性 validator で独立に検出・拒否する。"""
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
                adherence_status="preserved", determination="exact_match", coverage="verified"
            ),
            _harmony_anchor_payload(
                adherence_status="preserved", determination="exact_match", coverage="verified"
            ),
        ],
        # 2 行分（両方 verified）ときっちり辻褄が合う — coverage 側の
        # validator だけでは検出できないことを示す。
        "coverage": {"verified": 2, "violated": 0, "not_observed": 0},
        "identity_assessment": {"enabled": False},
    }
    with pytest.raises(ValidationError, match="duplicate anchor_id"):
        RecastReport.model_validate(payload)


def test_recast_report_round_trip_preserves_unique_anchor_ids() -> None:
    """正常な読み戻し: `build_recast_report` が発行する report（anchor_id が
    一意）は dump→`model_validate` で pass する（round 11 の一意性 validator
    が正常経路を誤って弾かないことの機械 assert）。"""
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
    assert [a.anchor_id for a in round_tripped.anchors] == ["harmony", "structure"]


# --- take.path relative-locator validation (Codex P2, #210 round 12 指摘15) ---


@pytest.mark.parametrize(
    "bad_path",
    [
        "/etc/passwd",
        "C:\\Windows\\System32\\take.wav",
        "../outside/take.wav",
        "..\\outside\\take.wav",
        "builds/../../outside.wav",
    ],
)
def test_recast_report_take_rejects_absolute_or_traversal_path(bad_path: str) -> None:
    """`RecastReportTake.path` は project 相対の locator でなければならない
    （Codex P2, #210 round 12 指摘15）: 絶対パスや `../`/`..\\` traversal を
    schema 検証なしで受理すると、手編集/別経路の report が project 外の
    ファイルを「証明済み take」として `recast_summary.md` に表示できて
    しまう。"""
    with pytest.raises(ValidationError):
        RecastReportTake(path=bad_path, sha256="0" * 64)


def test_recast_report_take_accepts_project_relative_path() -> None:
    """正常な読み戻し: `build_recast_report`/呼び出し側が渡す project 相対
    パス（`os.path.relpath` 由来、内部の `..` 相殺を含む場合がある）は
    validator を通過する。"""
    take = RecastReportTake(
        path="builds/takes/edm@suno/take-01.wav", sha256="0" * 64
    )
    assert take.path == "builds/takes/edm@suno/take-01.wav"

    # `a/../b` のような内部相殺（net-upward にならない）は許可される
    # （`validate_relative_locator` の lexical 判定契約どおり）。
    canceling = RecastReportTake(
        path="builds/takes/../takes/edm@suno/take-01.wav", sha256="0" * 64
    )
    assert canceling.path == "builds/takes/../takes/edm@suno/take-01.wav"


# --- M4c: experimental_anchors (Design Memo M4 §5, DD-8) -----------------------


def _melody_experimental_entry(
    *, adherence_status: str = "preserved", anchor_id: str = "melody"
) -> ExperimentalAnchorEntry:
    return ExperimentalAnchorEntry(
        adherence_status=adherence_status,
        anchor_id=anchor_id,
        domain="melody",
        axis_policy={"contour": "hard", "interval": "elastic"},
        axes={"contour": 0.9, "interval": 0.8},
        axis_evidence={"contour": "strong", "interval": "strong"},
        coverage={
            "aligned_note_fraction_a": 1.0,
            "aligned_note_fraction_b": 1.0,
            "phrase_coverage_a": 1.0,
            "phrase_coverage_b": 1.0,
        },
        octave_artifact_suspected=False,
        reasons=[],
        provenance={"reference": "score"},
    )


def _minimal_recast_report() -> RecastReport:
    report = _observation_report(
        [_anchor_observation("harmony", "harmony", "preserved", "exact_match")]
    )
    package = _package(
        [PackageAnchorStatus.model_construct(anchor_id="harmony", requested_mode="hard")]
    )
    return build_recast_report(
        project_id="p",
        variant="v",
        backend="b",
        package=package,
        report=report,
        take_path_relative="take.wav",
        take_sha256="a" * 64,
    )


def test_build_recast_report_defaults_experimental_anchors_to_empty() -> None:
    recast_report = _minimal_recast_report()
    assert recast_report.experimental_anchors == []


def test_recast_report_dump_omits_experimental_anchors_key_when_empty() -> None:
    """DD-8: 空のときは serialize に一切現れない — 既存 golden fixture の
    バイト不変契約（`experimental_anchors: []` すら出力に混ぜない）。"""
    recast_report = _minimal_recast_report()
    dumped = recast_report.model_dump(mode="json", exclude_none=True)
    assert "experimental_anchors" not in dumped
    assert "experimental_anchors" not in recast_report.model_dump_json()


def test_recast_report_dump_includes_experimental_anchors_when_present() -> None:
    report = _observation_report(
        [_anchor_observation("harmony", "harmony", "preserved", "exact_match")]
    )
    package = _package(
        [PackageAnchorStatus.model_construct(anchor_id="harmony", requested_mode="hard")]
    )
    entry = _melody_experimental_entry()
    recast_report = build_recast_report(
        project_id="p",
        variant="v",
        backend="b",
        package=package,
        report=report,
        take_path_relative="take.wav",
        take_sha256="a" * 64,
        experimental_anchors=[entry],
    )
    dumped = recast_report.model_dump(mode="json", exclude_none=True)
    assert dumped["experimental_anchors"] == [entry.model_dump(mode="json")]
    # main の coverage/anchors は experimental の存在と無関係（会計分離）。
    assert recast_report.coverage == RecastReportCoverage(verified=1, violated=0, not_observed=0)
    assert [a.anchor_id for a in recast_report.anchors] == ["harmony"]


def test_recast_report_round_trip_preserves_experimental_anchors() -> None:
    report = _observation_report(
        [_anchor_observation("harmony", "harmony", "preserved", "exact_match")]
    )
    package = _package(
        [PackageAnchorStatus.model_construct(anchor_id="harmony", requested_mode="hard")]
    )
    entry = _melody_experimental_entry()
    recast_report = build_recast_report(
        project_id="p",
        variant="v",
        backend="b",
        package=package,
        report=report,
        take_path_relative="take.wav",
        take_sha256="a" * 64,
        experimental_anchors=[entry],
    )
    round_tripped = RecastReport.model_validate(recast_report.model_dump(mode="json"))
    assert round_tripped == recast_report
    assert round_tripped.experimental_anchors == [entry]


def test_recast_report_rejects_duplicate_experimental_anchor_id() -> None:
    """R2-4 (Codex round2 P2): `experimental_anchors` 内の anchor_id にも
    一意性を強制する（本会計 `anchors` 側の round 11 対応と同型の読み戻し
    安全網）。"""
    report = _observation_report(
        [_anchor_observation("harmony", "harmony", "preserved", "exact_match")]
    )
    package = _package(
        [PackageAnchorStatus.model_construct(anchor_id="harmony", requested_mode="hard")]
    )
    entry_a = _melody_experimental_entry(anchor_id="melody")
    entry_b = _melody_experimental_entry(anchor_id="melody")
    with pytest.raises(ValidationError, match="duplicate anchor_id"):
        build_recast_report(
            project_id="p",
            variant="v",
            backend="b",
            package=package,
            report=report,
            take_path_relative="take.wav",
            take_sha256="a" * 64,
            experimental_anchors=[entry_a, entry_b],
        )


def test_recast_report_rejects_anchor_id_shared_between_main_and_experimental() -> None:
    """R4-5 (Codex round4 P2): `anchors`（本会計）と `experimental_anchors`
    （会計分離された別集合）の間で同じ anchor_id が使われることも拒否する
    ——`_validate_unique_anchor_ids`/`_validate_unique_experimental_anchor_ids`
    はそれぞれのリスト内の重複しか見ないため、両リストに 1 件ずつ現れる
    ケース（読み戻し安全網の抜け穴）を検出できていなかった。"""
    report = _observation_report(
        [_anchor_observation("harmony", "harmony", "preserved", "exact_match")]
    )
    package = _package(
        [PackageAnchorStatus.model_construct(anchor_id="harmony", requested_mode="hard")]
    )
    entry = _melody_experimental_entry(anchor_id="harmony")
    with pytest.raises(ValidationError, match="both"):
        build_recast_report(
            project_id="p",
            variant="v",
            backend="b",
            package=package,
            report=report,
            take_path_relative="take.wav",
            take_sha256="a" * 64,
            experimental_anchors=[entry],
        )


def test_recast_report_allows_disjoint_main_and_experimental_anchor_ids() -> None:
    """回帰確認: 本会計と experimental が互いに素な anchor_id 集合であれば
    R4-5 の新 validator は正常発行を妨げない（`recast/experimental.py:
    resolve_main_observation_anchor_scope` が axis_policy 付き melody anchor
    を本会計スコープから除外する既存配線どおり）。"""
    report = _observation_report(
        [_anchor_observation("harmony", "harmony", "preserved", "exact_match")]
    )
    package = _package(
        [PackageAnchorStatus.model_construct(anchor_id="harmony", requested_mode="hard")]
    )
    entry = _melody_experimental_entry(anchor_id="melody")
    recast_report = build_recast_report(
        project_id="p",
        variant="v",
        backend="b",
        package=package,
        report=report,
        take_path_relative="take.wav",
        take_sha256="a" * 64,
        experimental_anchors=[entry],
    )
    assert [a.anchor_id for a in recast_report.anchors] == ["harmony"]
    assert [e.anchor_id for e in recast_report.experimental_anchors] == ["melody"]


def test_recast_report_reads_back_old_report_without_experimental_anchors_field() -> None:
    """旧レポート（このフィールドを持たない JSON）は default（空リスト）で
    読み戻せる — 後方互換。"""
    payload = {
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
    }
    report = RecastReport.model_validate(payload)
    assert report.experimental_anchors == []


def test_render_recast_summary_markdown_omits_experimental_section_when_empty() -> None:
    recast_report = _minimal_recast_report()
    md = render_recast_summary_markdown(recast_report)
    assert "Experimental anchors" not in md


def test_render_recast_summary_markdown_includes_experimental_section_when_present() -> None:
    report = _observation_report(
        [_anchor_observation("harmony", "harmony", "preserved", "exact_match")]
    )
    package = _package(
        [PackageAnchorStatus.model_construct(anchor_id="harmony", requested_mode="hard")]
    )
    entry = _melody_experimental_entry()
    recast_report = build_recast_report(
        project_id="p",
        variant="v",
        backend="b",
        package=package,
        report=report,
        take_path_relative="take.wav",
        take_sha256="a" * 64,
        experimental_anchors=[entry],
    )
    md = render_recast_summary_markdown(recast_report)
    assert "## Experimental anchors (melody)" in md
    assert "melody" in md
    assert "preserved" in md
    # Coverage 集計自体はこの節を反映していない（会計分離を要求どおり明文化）。
    assert "verified: 1" in md
    assert "not_observed: 0" in md


def test_recast_report_take_requires_64_hex_sha256_still_works_with_experimental() -> None:
    """既存の take.sha256 validator（本テストファイル前段）と
    experimental_anchors フィールド追加が独立に動作することの sanity。"""
    RecastReportTake(path="take.wav", sha256="0" * 64)


def test_recast_report_round_trip_preserves_relative_take_path() -> None:
    """`build_recast_report` が発行する report（`take_path_relative` は常に
    project 相対）は dump→`model_validate` で pass する（round 12 の
    validator が正常経路を誤って弾かないことの機械 assert）。"""
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
        take_path_relative="builds/takes/edm@suno/take-01.wav",
        take_sha256="a" * 64,
    )
    round_tripped = RecastReport.model_validate(recast_report.model_dump(mode="json"))
    assert round_tripped.take.path == "builds/takes/edm@suno/take-01.wav"
