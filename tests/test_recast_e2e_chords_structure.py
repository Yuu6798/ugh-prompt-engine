"""PR4: chords（harmony）+ structure 縦切り E2E（deterministic 経路）。

Phase 0 ゲート判定（PR #205）の帰結: メロディセンサーは pyin 縮退により
不成立（#199 既往）のため、本 PR の縦切り hard anchor は harmony
（chord_sequence_json）+ structure（section_map）の 2 つに限る。

`examples/recast/e2e_project/`（Codex P2 review round 2, PR4 #209）を使う:
`examples/recast/demo_project/` は lyrics/melody anchor を持つ suno manual
デモ用の fixture のまま（PR4 以前の姿）に保ち、本 E2E 専用の committed
project を別ディレクトリに新設した — manifest（identity.yaml）が
harmony+structure の 2 anchor のみを持つため、working copy を tmp へ
コピーしただけで（テスト側での anchor 除去ロジックなしに）人が直接
`examples/recast/e2e_project/` を実行する committed 経路そのものが
learned adapter（faster-whisper/basic-pitch）非依存になる（round 1 の
tmp 派生 strip では committed デモ経路自体は救われない、という round 2
指摘への対応）。

一連の実行を 1 つの E2E として検証する:
  1. `svprpe recast plan <project> --variant deterministic_e2e --backend
     deterministic` → `verified` 到達、`recast_plan.json` の anchors に
     harmony/structure が `policy_mode="hard"` で載る
  2. `svprpe recast run ...` → deterministic invoke（in-process 演奏者）で
     take-01.wav を合成 → 状態 `generated`
  3. `svprpe observe`（`builds/packages/.../performance_package.json` +
     `identity.yaml` + `builds/takes/.../take-01.wav`）で生成後観測
  4. 実測した `adherence_status`/`determination`/`measurements` を pin する
     （事前に仮定で書かず、実行結果をそのまま固定した値 — この計器は
     harmony/structure いずれも `preserved` に到達しない: exact match しない
     限り `not_observed`（`determination="deferred"`）が正直な出力であり、
     それ自体が本 E2E の受け入れ条件を満たす）
  5. 決定論: 独立したプロジェクトコピーで同じ一連の操作をもう一度実行し、
     take-01.wav の sha256 と観測結果の per-anchor
     （adherence_status/determination/sensor.available/measurements）が
     一致することを確認する

perform + 実 extract を伴うため `@pytest.mark.slow`（実測 wall time 目安:
plan+run 数秒 + observe（perform ~4s + extract ~23s）で 1 サイクルあたり
30 秒程度 — 60 秒ガイドラインの対象は「テスト内の合成」であり、本テストの
支配的コストは perform ではなく extract 側）。tmp_path へのコピーは書き込み
分離（`builds/`・`recast_plan.json`・`recast_state.json` の並行 test 実行間
干渉防止）のためだけであり、`examples/recast/e2e_project/` 自体の
manifest/arrangement は無加工のまま使う。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from svp_rpe.cli import app
from svp_rpe.recast.state import load_recast_state

E2E_PROJECT = Path("examples/recast/e2e_project")

runner = CliRunner()


def _copy_e2e_project(tmp_path: Path, *, label: str) -> Path:
    """`examples/recast/e2e_project/` を書き込み分離のためだけに tmp_path へ
    コピーする（manifest/arrangement/project.yaml はいずれも無加工）。"""
    dest = tmp_path / f"e2e_project_{label}"
    dest.mkdir()
    shutil.copy(E2E_PROJECT / "project.yaml", dest / "project.yaml")
    shutil.copy(E2E_PROJECT / "composition_score.yaml", dest / "composition_score.yaml")
    shutil.copy(E2E_PROJECT / "identity.yaml", dest / "identity.yaml")
    shutil.copytree(E2E_PROJECT / "identity", dest / "identity")
    shutil.copytree(E2E_PROJECT / "arrangements", dest / "arrangements")
    return dest / "project.yaml"


def _anchor_policy_modes(project_dir: Path) -> dict[str, Any]:
    """`recast_plan.json`（`svprpe recast plan` が publish した canonical JSON）
    から anchor_id -> policy_mode を読む — Rich table のカラム幅依存な CLI
    出力文字列（折り返しでヘッダが truncate される）をパースするより頑健。"""
    plan = json.loads((project_dir / "recast_plan.json").read_text(encoding="utf-8"))
    return {anchor["anchor_id"]: anchor.get("policy_mode") for anchor in plan["anchors"]}


def _run_plan_then_run(project_path: Path) -> tuple[Path, Path]:
    """`recast plan` → `recast run`（deterministic backend）を CLI で実行し、
    (performance_package.json のパス, take-01.wav のパス) を返す。両コマンドの
    exit_code が 0 であること、state が `generated` へ到達することを検証する。
    `e2e_project` の manifest は harmony/structure の 2 anchor のみを持つ。"""
    plan_result = runner.invoke(
        app,
        [
            "recast",
            "plan",
            str(project_path),
            "--variant",
            "deterministic_e2e",
            "--backend",
            "deterministic",
        ],
    )
    assert plan_result.exit_code == 0, plan_result.output
    assert "State reached: verified" in plan_result.output

    policy_modes = _anchor_policy_modes(project_path.parent)
    assert policy_modes == {"harmony": "hard", "structure": "hard"}

    run_result = runner.invoke(
        app,
        [
            "recast",
            "run",
            str(project_path),
            "--variant",
            "deterministic_e2e",
            "--backend",
            "deterministic",
        ],
    )
    assert run_result.exit_code == 0, run_result.output

    state_file = load_recast_state(project_path.parent)
    run = state_file.runs["deterministic_e2e@deterministic"]
    assert run.state == "generated"

    package_path = (
        project_path.parent
        / "builds"
        / "packages"
        / "deterministic_e2e@deterministic"
        / "performance_package.json"
    )
    take_path = (
        project_path.parent
        / "builds"
        / "takes"
        / "deterministic_e2e@deterministic"
        / "take-01.wav"
    )
    assert package_path.is_file()
    assert take_path.is_file()

    take_json = json.loads(
        (take_path.parent / "take.json").read_text(encoding="utf-8")
    )
    assert take_json["source"] == "local"

    return package_path, take_path


def _observe(
    project_path: Path, package_path: Path, take_path: Path, report_path: Path
) -> dict[str, Any]:
    result = runner.invoke(
        app,
        [
            "observe",
            str(package_path),
            str(take_path),
            "--manifest",
            str(project_path.parent / "identity.yaml"),
            "-o",
            str(report_path),
        ],
    )
    assert result.exit_code == 0, result.output
    return json.loads(report_path.read_text(encoding="utf-8"))


# 実測して pin した期待値（`docs/wi1_d1_thresholds.md` 系の裁定と同じく、この
# 計器の出力を仮定で書かない — 事前に一度実行して得た実測値をそのまま固定
# する。`e2e_project` は `demo_project` と同一 base score/同一 harmony・
# structure anchor のため、round 1（demo_project + tmp 派生 strip）で実測した
# 値と一致することを確認済み — take-01.wav の合成対象は CompositionScore
# 由来で identity manifest の anchor 集合に依存しない）。
_EXPECTED_HARMONY_MEASUREMENTS = {
    "chord_sequence_match_rate": 0.0833,
    "repeated_chord_sequence_match_rate": 0.0,
    "canonical_length": 4,
    "observed_length": 24,
    "collapsed_observed_length": 10,
    "matched_cycle_prefix_length": 7,
    "collapsed_match_fraction": 0.7,
    "unmatched_tail_length": 3,
    "unmatched_tail_head": [["C", "major"], ["C", "minor"], ["C", "major"]],
    "full_cycles": 2,
}
_EXPECTED_STRUCTURE_MEASUREMENTS = {
    "canonical_sections": ["intro", "verse", "chorus", "bridge"],
    "canonical_length": 4,
    "observed_sections": [
        "intro",
        "verse",
        "verse",
        "bridge",
        "verse",
        "chorus",
        "chorus",
        "outro",
    ],
    "observed_sections_raw": [
        "Intro",
        "Verse",
        "Verse2",
        "Bridge",
        "Verse3",
        "Chorus",
        "Chorus",
        "Outro",
    ],
    "observed_length": 8,
    "position_match_rate": 0.375,
    "sequence_exact_match": False,
}


def _assert_pinned_anchors(report: dict[str, Any]) -> None:
    anchors = {anchor["anchor_id"]: anchor for anchor in report["anchors"]}
    # e2e_project の manifest は harmony/structure のみを宣言している
    # （learned adapter を要する lyrics/melody は存在しない）。
    assert set(anchors) == {"harmony", "structure"}

    # harmony/structure: hard 宣言した 2 anchor。いずれも exact match しない
    # ため `not_observed`（`determination="deferred"`）が正直な出力 —
    # `preserved`/`exact_match` を仮定で書かない（実測結果をそのまま pin）。
    harmony = anchors["harmony"]
    assert harmony["sensor"]["available"] is True
    assert harmony["adherence_status"] == "not_observed"
    assert harmony["determination"] == "deferred"
    assert harmony["measurements"] == _EXPECTED_HARMONY_MEASUREMENTS

    structure = anchors["structure"]
    assert structure["sensor"]["available"] is True
    assert structure["adherence_status"] == "not_observed"
    assert structure["determination"] == "deferred"
    assert structure["measurements"] == _EXPECTED_STRUCTURE_MEASUREMENTS


@pytest.mark.slow
def test_e2e_deterministic_chords_structure_reaches_generated_and_observes(
    tmp_path: Path,
) -> None:
    project_path = _copy_e2e_project(tmp_path, label="a")

    package_path, take_path = _run_plan_then_run(project_path)

    report_path = tmp_path / "observation_report_a.json"
    report = _observe(project_path, package_path, take_path, report_path)

    assert report["work_id"] == "midnight-signal"
    _assert_pinned_anchors(report)


@pytest.mark.slow
def test_e2e_deterministic_chords_structure_is_reproducible_across_full_reruns(
    tmp_path: Path,
) -> None:
    """受け入れ条件 §5: 独立したプロジェクトコピーで plan/run/observe の
    一連の操作をもう一度実行し、take-01.wav の sha256 と観測結果の
    per-anchor（adherence_status/determination/sensor.available/measurements）
    が一致することを確認する（決定論の実測確認 — deterministic backend の
    `PerformanceStyle(seed=12)` 固定と、observe の計器自体の決定論、両方が
    合成された結果としての E2E 全体の再現性）。"""
    import hashlib

    project_path_a = _copy_e2e_project(tmp_path, label="rerun_a")
    _package_a, take_a = _run_plan_then_run(project_path_a)
    report_a = _observe(
        project_path_a, _package_a, take_a, tmp_path / "observation_report_rerun_a.json"
    )

    project_path_b = _copy_e2e_project(tmp_path, label="rerun_b")
    package_b, take_b = _run_plan_then_run(project_path_b)
    report_b = _observe(
        project_path_b, package_b, take_b, tmp_path / "observation_report_rerun_b.json"
    )

    take_sha_a = hashlib.sha256(take_a.read_bytes()).hexdigest()
    take_sha_b = hashlib.sha256(take_b.read_bytes()).hexdigest()
    assert take_sha_a == take_sha_b

    anchors_a = {a["anchor_id"]: a for a in report_a["anchors"]}
    anchors_b = {a["anchor_id"]: a for a in report_b["anchors"]}
    assert set(anchors_a) == set(anchors_b)
    for anchor_id, anchor_a in anchors_a.items():
        anchor_b = anchors_b[anchor_id]
        assert anchor_a["adherence_status"] == anchor_b["adherence_status"], anchor_id
        assert anchor_a["determination"] == anchor_b["determination"], anchor_id
        assert anchor_a["sensor"]["available"] == anchor_b["sensor"]["available"], anchor_id
        assert anchor_a["measurements"] == anchor_b["measurements"], anchor_id

    _assert_pinned_anchors(report_a)
    _assert_pinned_anchors(report_b)
