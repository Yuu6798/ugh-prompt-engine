"""Golden path fixture: CI 全経路回帰（PR6、指示書 §7 recast トラック最終 PR）。

`examples/recast/golden_project/` は 1 作品（score+identity: harmony+structure
の 2 anchor）× 1 編曲（`golden` variant）× deterministic backend × **2 take**
の committed fixture:

- take-01: `deterministic` backend（local invocation, PR3 `DeterministicInvoker`
  固定 style, seed=12）— `recast plan` → `recast run` の local 経路。
- take-02: `deterministic_manual` backend（manual invocation）— `recast plan`
  → `recast run`（注文書 6 ファイル）→ 別 seed（99）の `PerformanceStyle` で
  `perform()` した音源を `recast ingest --audio` で収蔵する manual フロー代役
  （指示書: 「これで local と manual の両フローが 1 fixture で回る」）。

`observation.enabled: true` かつ `observation.anchors: []`（全 anchor）のため、
manual フロー（take-02）は ingest が observe→report まで CLI 単体で完走する。
local フロー（take-01）は `recast run` が `generated` までしか進めない仕様
（PR5 の observe→report 自動化は manual backend の ingest 限定）のため、この
テストは CLI の ingest 尾部と同じ手順（`observe_generated_artifact` →
`build_recast_report` → atomic publish → `record_state`）を直接呼んで take-01
分の report も作る — 「両 take の report まで」という指示書の要求を満たす。

committed 固定は「軽量成果物+sha256 pin」方式（wav はコミットしない）:
`examples/recast/golden_project/expected/` に `recast_plan.json`（project 直下
の便宜コピー — 最後に publish された plan、この flow では
`golden@deterministic_manual`）/ `plans/golden@<backend>/recast_plan.json`
（両 backend の正典 per-run plan ファイル、Codex P2 review・PR #212 指摘で
per-run 公開先へ変更）/ 注文書 6 ファイル/ 両 backend の
`recast_report.json`+`recast_summary.md`/ 両 take の sha256 pin
（`expected/takes.json`）を commit し、本テストが全経路を実行して byte 一致
（JSON/md）+ take sha256 一致を検証する。

決定論契約: 独立した 2 回の tmp_path 実行（`_run_golden_path` を 2 回呼ぶ）が
byte-for-byte 同一の成果物を生成することも確認する（他 recast E2E テスト —
`tests/test_recast_ingest_report.py:test_ingest_observe_report_e2e_is_byte_identical_across_reruns`
— と同じ規律）。
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from svp_rpe.arrange.observe import observe_generated_artifact
from svp_rpe.arrange.package import PERFORMANCE_PACKAGE_FILENAME
from svp_rpe.cli import app
from svp_rpe.perform.performer import PerformanceStyle, perform
from svp_rpe.perform.synth import sha256_bytes, wav_bytes
from svp_rpe.recast import load_recast_project
from svp_rpe.recast.backend import atomic_publish_bytes_bundle
from svp_rpe.recast.plan import RECAST_PLAN_FILENAME, build_recast_plan_artifacts
from svp_rpe.recast.report import (
    RECAST_REPORT_FILENAME,
    RECAST_SUMMARY_FILENAME,
    build_recast_report,
    render_recast_summary_markdown,
)
from svp_rpe.recast.run_paths import resolve_packages_dir, resolve_reports_dir
from svp_rpe.recast.state import load_recast_state, record_state

GOLDEN_PROJECT = Path("examples/recast/golden_project")
EXPECTED = GOLDEN_PROJECT / "expected"

# take-02 (manual flow stand-in) の PerformanceStyle: take-01 の PR3
# `DeterministicInvoker` 固定 style（seed=12）とは別 seed を使う（指示書:
# 「別 seed の PerformanceStyle で perform した音源」）。値を変えると
# `expected/takes.json` の take02 sha256 / report が pin と食い違うため固定する。
TAKE2_STYLE = PerformanceStyle(name="recast_golden_take2", seed=99)

runner = CliRunner()


@dataclass(frozen=True)
class GoldenPathResult:
    project_dir: Path
    take01_sha256: str
    take02_sha256: str


def _copy_golden_project(tmp_path: Path, *, label: str) -> Path:
    """`golden_project` の入力一式（`expected/` を除く）を tmp_path 配下へ
    コピーする（`expected/` は比較専用の committed 期待値のため、テストが
    自由に破壊改変する作業コピーには混ぜない — 他 recast テストと同じ規律）。"""
    dest = tmp_path / f"golden_project_{label}"
    dest.mkdir()
    shutil.copy(GOLDEN_PROJECT / "project.yaml", dest / "project.yaml")
    shutil.copy(GOLDEN_PROJECT / "composition_score.yaml", dest / "composition_score.yaml")
    shutil.copy(GOLDEN_PROJECT / "identity.yaml", dest / "identity.yaml")
    shutil.copytree(GOLDEN_PROJECT / "identity", dest / "identity")
    shutil.copytree(GOLDEN_PROJECT / "arrangements", dest / "arrangements")
    return dest / "project.yaml"


def _invoke(args: list[str]) -> str:
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return result.output


def _run_golden_path(tmp_path: Path, *, label: str) -> GoldenPathResult:
    """golden_project の全経路を実行する: plan → run（take-01, local）→
    [take-01 の report を CLI ingest 尾部と同じ手順で直接組み立てる] →
    plan → run（注文書）→ perform（take-02）→ ingest（take-02, manual,
    observe→report まで自動）。"""
    project_path = _copy_golden_project(tmp_path, label=label)
    project_dir = project_path.parent

    # --- take-01: deterministic (local invocation) --------------------------
    _invoke(
        ["recast", "plan", str(project_path), "--variant", "golden", "--backend", "deterministic"]
    )
    _invoke(
        ["recast", "run", str(project_path), "--variant", "golden", "--backend", "deterministic"]
    )
    state_after_take01_run = load_recast_state(project_dir)
    assert state_after_take01_run.runs["golden@deterministic"].state == "generated"

    take01_path = project_dir / "builds" / "takes" / "golden@deterministic" / "take-01.wav"
    take01_sha256 = sha256_bytes(take01_path.read_bytes())

    # local backend の `recast run` は observe→report まで進めない（PR5 の
    # 自動化は manual backend の ingest 限定）— CLI ingest 尾部
    # （`svp_rpe.cli.recast_cmd.recast_ingest_cmd`）と同じ手順をここで直接
    # 呼び、take-01 分の recast_report.json/recast_summary.md も作る。
    loaded = load_recast_project(project_path)
    # `publish=True`（recast-pr5 base 更新: `build_recast_plan_artifacts` は
    # `publish` が必須引数になった — CLI 3 コマンド（plan/run/ingest）と同じ
    # 「package/report を builds_root へ永続公開する」経路を再現する。
    # `publish=False`（`build_recast_plan` 経由）は読み取り専用で package を
    # builds_root へ残さないため、後段の `observe_generated_artifact` が読む
    # `performance_package.json` が存在しなくなる）。
    artifacts_take01 = build_recast_plan_artifacts(
        loaded, variant="golden", backend="deterministic", publish=True
    )
    assert artifacts_take01.result.plan.state_reached == "verified", artifacts_take01.result.text
    # `RecastPlanResult.protected_inputs`（plan 段の single-read 束から副作用
    # なく再構成済み）を使う — `collect_protected_input_paths` を独立に呼んで
    # manifest を再 parse しない（recast-pr5 base 更新後の CLI と同じ規約）。
    protected_inputs_take01 = artifacts_take01.result.protected_inputs

    package_path_take01 = (
        resolve_packages_dir(loaded, "golden", "deterministic") / PERFORMANCE_PACKAGE_FILENAME
    )
    take01_relative = os.path.relpath(take01_path, project_dir)
    observation_take01 = observe_generated_artifact(
        package_path=package_path_take01,
        manifest_path=loaded.identity_manifest_path,
        audio_path=take01_path,
        generated_artifact_path=take01_relative,
        expected_audio_sha256=take01_sha256,
    )
    record_state(
        project_dir,
        "golden",
        "deterministic",
        "observed",
        note=(
            f"anchors={len(observation_take01.report.anchors)} "
            f"package_sha256={observation_take01.report.package_sha256[:12]}"
        ),
        inputs_digest=artifacts_take01.result.inputs_digest,
        plan_sha256=None,
        protected_inputs=protected_inputs_take01,
    )
    recast_report_take01 = build_recast_report(
        project_id=loaded.project.project.id,
        variant="golden",
        backend="deterministic",
        package=observation_take01.package,
        report=observation_take01.report,
        take_path_relative=take01_relative,
        take_sha256=take01_sha256,
        observation_anchors=loaded.project.observation.anchors,
    )
    summary_take01 = render_recast_summary_markdown(recast_report_take01)
    report_take01_bytes = (
        json.dumps(
            recast_report_take01.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    reports_dir_take01 = resolve_reports_dir(loaded, "golden", "deterministic")
    atomic_publish_bytes_bundle(
        reports_dir_take01,
        {
            RECAST_REPORT_FILENAME: report_take01_bytes,
            RECAST_SUMMARY_FILENAME: summary_take01.encode("utf-8"),
        },
        protected_inputs=protected_inputs_take01,
    )
    record_state(
        project_dir,
        "golden",
        "deterministic",
        "reported",
        note=os.path.relpath(reports_dir_take01 / RECAST_SUMMARY_FILENAME, project_dir),
        inputs_digest=artifacts_take01.result.inputs_digest,
        plan_sha256=None,
        protected_inputs=protected_inputs_take01,
    )
    state_after_take01_report = load_recast_state(project_dir)
    assert state_after_take01_report.runs["golden@deterministic"].state == "reported"

    # --- take-02: deterministic_manual (manual flow stand-in) ---------------
    _invoke(
        [
            "recast", "plan", str(project_path),
            "--variant", "golden", "--backend", "deterministic_manual",
        ]
    )
    _invoke(
        [
            "recast", "run", str(project_path),
            "--variant", "golden", "--backend", "deterministic_manual",
        ]
    )
    state_after_take02_run = load_recast_state(project_dir)
    assert state_after_take02_run.runs["golden@deterministic_manual"].state == "awaiting_generation"

    loaded_take02 = load_recast_project(project_path)
    # `publish=False`: 上の CLI `recast run` 呼び出しが既に package を
    # publish 済み — ここは `derived_score` を読むためだけの診断再計算なので
    # 永続公開しない読み取り専用経路で十分（recast-pr5 base 更新後の
    # `build_recast_plan_artifacts` の必須 `publish` 引数）。
    artifacts_take02 = build_recast_plan_artifacts(
        loaded_take02, variant="golden", backend="deterministic_manual", publish=False
    )
    assert artifacts_take02.derived_score is not None
    samples_take02 = perform(artifacts_take02.derived_score, TAKE2_STYLE)
    supplied_audio = project_dir.parent / f"{label}-take02-supplied.wav"
    supplied_audio.write_bytes(wav_bytes(samples_take02))
    take02_sha256 = sha256_bytes(supplied_audio.read_bytes())

    _invoke(
        [
            "recast", "ingest", str(project_path),
            "--variant", "golden", "--backend", "deterministic_manual",
            "--audio", str(supplied_audio),
        ]
    )
    state_after_take02_ingest = load_recast_state(project_dir)
    assert state_after_take02_ingest.runs["golden@deterministic_manual"].state == "reported"

    return GoldenPathResult(
        project_dir=project_dir, take01_sha256=take01_sha256, take02_sha256=take02_sha256
    )


def _expected_takes() -> dict:
    return json.loads((EXPECTED / "takes.json").read_text(encoding="utf-8"))


def _assert_bytes_match_expected(actual_path: Path, expected_path: Path) -> None:
    assert actual_path.is_file(), f"missing artifact: {actual_path}"
    assert expected_path.is_file(), f"missing committed expected fixture: {expected_path}"
    assert actual_path.read_bytes() == expected_path.read_bytes(), (
        f"{actual_path} does not byte-match committed {expected_path}"
    )


_ORDER_FILENAMES = (
    "prompt.json",
    "lyrics.txt",
    "section_tags.txt",
    "expected_artifacts.json",
    "next_command.txt",
    "order_sheet.md",
)


def _assert_matches_committed_expected(result: GoldenPathResult) -> None:
    expected_takes = _expected_takes()
    assert result.take01_sha256 == expected_takes["deterministic"]["sha256"]
    assert result.take02_sha256 == expected_takes["deterministic_manual"]["sha256"]

    # project 直下の便宜コピー（最後に評価された run — この flow では
    # `golden@deterministic_manual`）と、両 backend の正典 per-run plan
    # ファイル（`<builds_root>/plans/golden@<backend>/recast_plan.json`）の
    # 両方を pin と突き合わせる（Codex P2 review, PR #212 指摘: 正典公開先が
    # per-run 化されたため、その committed fixture も追加した）。
    _assert_bytes_match_expected(
        result.project_dir / RECAST_PLAN_FILENAME, EXPECTED / RECAST_PLAN_FILENAME
    )
    for backend in ("deterministic", "deterministic_manual"):
        plans_dir = result.project_dir / "builds" / "plans" / f"golden@{backend}"
        expected_plans_dir = EXPECTED / "plans" / f"golden@{backend}"
        _assert_bytes_match_expected(
            plans_dir / RECAST_PLAN_FILENAME, expected_plans_dir / RECAST_PLAN_FILENAME
        )

    order_dir = result.project_dir / "builds" / "orders" / "golden@deterministic_manual"
    expected_order_dir = EXPECTED / "orders" / "golden@deterministic_manual"
    for filename in _ORDER_FILENAMES:
        _assert_bytes_match_expected(order_dir / filename, expected_order_dir / filename)

    for backend in ("deterministic", "deterministic_manual"):
        reports_dir = result.project_dir / "builds" / "reports" / f"golden@{backend}"
        expected_reports_dir = EXPECTED / "reports" / f"golden@{backend}"
        _assert_bytes_match_expected(
            reports_dir / RECAST_REPORT_FILENAME, expected_reports_dir / RECAST_REPORT_FILENAME
        )
        _assert_bytes_match_expected(
            reports_dir / RECAST_SUMMARY_FILENAME, expected_reports_dir / RECAST_SUMMARY_FILENAME
        )


@pytest.mark.slow
def test_golden_path_matches_committed_expected(tmp_path: Path) -> None:
    result = _run_golden_path(tmp_path, label="a")
    _assert_matches_committed_expected(result)

    # coverage 実測: e2e_project 由来の実測（決定論シンセ演奏者は harmony/
    # structure いずれも exact match しない — chords: 移調済みメジャー転回,
    # structure: 生成セクションラベルが正典と不一致）と同型の not_observed。
    report_take01 = json.loads(
        (
            result.project_dir / "builds" / "reports" / "golden@deterministic" / "recast_report.json"
        ).read_text(encoding="utf-8")
    )
    assert report_take01["coverage"] == {"verified": 0, "violated": 0, "not_observed": 2}
    report_take02 = json.loads(
        (
            result.project_dir
            / "builds"
            / "reports"
            / "golden@deterministic_manual"
            / "recast_report.json"
        ).read_text(encoding="utf-8")
    )
    assert report_take02["coverage"] == {"verified": 0, "violated": 0, "not_observed": 2}
    assert {a["anchor_id"] for a in report_take01["anchors"]} == {"harmony", "structure"}
    assert {a["anchor_id"] for a in report_take02["anchors"]} == {"harmony", "structure"}


@pytest.mark.slow
def test_golden_path_is_deterministic_across_independent_reruns(tmp_path: Path) -> None:
    """2 回目の全経路実行でも同一（`recast_plan.json`/両 report/両 take
    sha256 の byte-for-byte 一致）— 決定論契約の実測確認。"""
    result_a = _run_golden_path(tmp_path, label="rerun_a")
    result_b = _run_golden_path(tmp_path, label="rerun_b")

    assert result_a.take01_sha256 == result_b.take01_sha256
    assert result_a.take02_sha256 == result_b.take02_sha256

    plan_a = (result_a.project_dir / RECAST_PLAN_FILENAME).read_bytes()
    plan_b = (result_b.project_dir / RECAST_PLAN_FILENAME).read_bytes()
    assert plan_a == plan_b

    for backend in ("deterministic", "deterministic_manual"):
        plans_dir_a = result_a.project_dir / "builds" / "plans" / f"golden@{backend}"
        plans_dir_b = result_b.project_dir / "builds" / "plans" / f"golden@{backend}"
        assert (plans_dir_a / RECAST_PLAN_FILENAME).read_bytes() == (
            plans_dir_b / RECAST_PLAN_FILENAME
        ).read_bytes()

    for backend in ("deterministic", "deterministic_manual"):
        reports_dir_a = result_a.project_dir / "builds" / "reports" / f"golden@{backend}"
        reports_dir_b = result_b.project_dir / "builds" / "reports" / f"golden@{backend}"
        assert (reports_dir_a / RECAST_REPORT_FILENAME).read_bytes() == (
            reports_dir_b / RECAST_REPORT_FILENAME
        ).read_bytes()
        assert (reports_dir_a / RECAST_SUMMARY_FILENAME).read_bytes() == (
            reports_dir_b / RECAST_SUMMARY_FILENAME
        ).read_bytes()

    order_dir_a = result_a.project_dir / "builds" / "orders" / "golden@deterministic_manual"
    order_dir_b = result_b.project_dir / "builds" / "orders" / "golden@deterministic_manual"
    for filename in _ORDER_FILENAMES:
        assert (order_dir_a / filename).read_bytes() == (order_dir_b / filename).read_bytes()


def test_golden_project_fixture_loads_and_reaches_verified_for_both_backends(
    tmp_path: Path,
) -> None:
    """golden_project fixture が schema 検証を通り、両 backend が `verified`
    に到達することの高速な sanity チェック（実抽出・実 CLI 実行を伴わないため
    non-slow — 全体の golden path 実行は上記 @pytest.mark.slow 側が担う）。

    `build_recast_plan` は compile 済み `PerformancePackage` を
    `<builds_root>/packages/` へ publish する副作用を持つため（plan.py
    docstring 参照 — `recast_plan.json`/state の publish だけが CLI 側の責務で、
    packages の publish は plan 段自身が行う）、committed fixture ディレクトリを
    直接汚さないよう他の recast テストと同じく tmp_path へコピーしてから使う。
    """
    from svp_rpe.recast.plan import build_recast_plan

    project_path = _copy_golden_project(tmp_path, label="sanity")
    loaded = load_recast_project(project_path)
    assert set(loaded.project.variants) == {"golden"}
    assert set(loaded.project.backends) == {"deterministic", "deterministic_manual"}
    for backend in ("deterministic", "deterministic_manual"):
        result = build_recast_plan(loaded, variant="golden", backend=backend)
        assert result.plan.blocked is None, result.plan.blocked
        assert result.plan.state_reached == "verified"
