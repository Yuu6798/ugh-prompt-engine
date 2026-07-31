"""tests/test_recast_m4d_melody_e2e.py — M4d golden path E2E（Design Memo M4 §6/§7）。

`examples/recast/demo_project`（既存・不変更）を tmp_path へコピーし、その場で
melody experimental 配線に要る追加だけを YAML 辞書ミューテーションで足す
（`tests/test_recast_ingest_report.py`/`tests/test_recast_backend.py` の
tmp_path パッチ方式を踏襲。既存 `examples/recast/golden_project/` は一切
触れない — golden path のバイト不変は `tests/test_recast_golden_path.py` が
別途担保する）。

E2E が踏む分岐:
- 校正済み分岐: テスト内生成 frozen registry（`M3ComparisonConfig.from_registry`
  を通る形。凍結 registry 実ファイルは不変更・不複製改変）+ extractor 注入で
  preserved / changed_within_policy / changed_outside_policy の 3 判定
- G1 不成立分岐: 実 uncalibrated registry（`tests/fixtures/melody_bench/
  m3_comparison_registry.yaml` の unmodified コピー）で
  not_observed(comparator_uncalibrated)、かつ抽出（route_runner）が一切
  呼ばれないこと
- 決定論: 同一入力の 2 回の独立実行で recast_report.json バイト一致
- `recast plan` の melody warning 行（G1/G2/config 診断）

実 pyin 抽出は行わない（Phase 0 縮退既知・Design Memo M4 §6 で必須ではない）
——テイク側はすべて `route_runner` 注入（fake extractor）。
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
import yaml
from typer.testing import CliRunner

from svp_rpe.arrange.observe import observe_generated_artifact
from svp_rpe.arrange.package import PERFORMANCE_PACKAGE_FILENAME
from svp_rpe.cli import app
from svp_rpe.melody.observability import MelodyNote, MelodyObservation
from svp_rpe.recast import load_recast_project
from svp_rpe.recast.experimental import ExperimentalAnchorEntry, collect_melody_experimental_anchors
from svp_rpe.recast.plan import build_recast_plan_artifacts
from svp_rpe.recast.report import (
    RECAST_REPORT_FILENAME,
    RECAST_SUMMARY_FILENAME,
    build_recast_report,
    render_recast_summary_markdown,
)
from svp_rpe.recast.run_paths import resolve_packages_dir, resolve_reports_dir
from svp_rpe.recast.state import load_recast_state, record_state

DEMO_PROJECT = Path("examples/recast/demo_project")
BENCH_DIR = Path("tests") / "fixtures" / "melody_bench"
REAL_M3_UNCALIBRATED_REGISTRY = BENCH_DIR / "m3_comparison_registry.yaml"
REAL_M1_REGISTRY = BENCH_DIR / "registry.yaml"

VARIANT_NAME = "melody_e2e"
BPM = 60.0

runner = CliRunner()

# 記号旋律の正典（score_reference の note-events/0.1 artifact）。bpm=60 に
# 合わせているため start_beat をそのまま秒として扱える
# （`tests/test_recast_experimental.py` の changed_outside_policy 実測パターンを
# 踏襲 — sharps 込みの 10 ノートは折返し(chroma_fold)込みで整列が安定する
# ことを probe 済み）。
_REFERENCE_NOTES: List[Tuple[float, str, float]] = [
    (0.0, "C4", 0.25),
    (0.3, "D4", 0.25),
    (0.6, "E4", 0.25),
    (0.9, "F#4", 0.25),
    (1.2, "G#4", 0.25),
    (2.45, "Bb4", 0.25),
    (2.75, "G#4", 0.25),
    (3.05, "F#4", 0.25),
    (3.35, "E4", 0.25),
    (3.65, "D4", 0.25),
]
_REFERENCE_PITCHES = [60.0, 62.0, 64.0, 66.0, 68.0, 70.0, 68.0, 66.0, 64.0, 62.0]
# hard(contour) 破壊なし・elastic(interval) だけ 1 半音ずらす（index1 のみ）
# — 実測: contour=1.0(strong)・interval=0.75(weak) → changed_within_policy。
_WITHIN_POLICY_PITCHES = [60.0, 61.0, 64.0, 66.0, 68.0, 70.0, 68.0, 66.0, 64.0, 62.0]
# hard(contour) を全滅させる大跳躍（`test_recast_experimental.py`
# `test_full_pipeline_octave_scaled_take_is_changed_outside_policy` と同型の
# 実測済みパターン）— contour=0.0(none)・interval=1.0(strong)。
_OUTSIDE_POLICY_PITCHES = [60.0, 50.0, 40.0, 30.0, 20.0, 70.0, 80.0, 90.0, 100.0, 110.0]

_AXIS_POLICY = {"contour": "hard", "interval": "elastic", "rhythm": "free"}

_M3_REGISTRY_BASE: Dict[str, Any] = {
    "schema": "m3-comparison/0.1",
    "registered_utc": "2026-07-31T00:00:00Z",
    "representation": {
        "pitch_quantization_semitones": 1,
        "contour_small_max_semitones": 2,
        "ioi_ratio_log2_step": 0.25,
        "duration_ratio_log2_step": 0.25,
        "chroma_fold_semitones": 12,
        "octave_artifact_divergence": 0.10,
    },
    "alignment": {
        "match_score": 1.0,
        "mismatch_score": -1.0,
        "gap_open": -1.0,
        "gap_extend": -0.5,
        "traceback_preference": ["diag", "up", "left"],
        "phrase_gap_sec": 0.6,
        "phrase_gap_score": 0.25,
    },
    "coverage": {"floor": 0.5, "floor_status": "frozen"},
    "separation_margin": {"min_same_minus_cross_margin": 0.15},
}


def _note_events_bytes() -> bytes:
    payload = {
        "schema": "note-events/0.1",
        "notes": [
            {"start_beat": start, "pitch": pitch, "duration_beats": duration}
            for start, pitch, duration in _REFERENCE_NOTES
        ],
    }
    return json.dumps(payload).encode("utf-8")


def _take_observation(pitches: List[float], *, name: str) -> MelodyObservation:
    notes = tuple(
        MelodyNote(start_sec=start, end_sec=start + duration, pitch_midi=pitch, confidence=0.9)
        for (start, _pitch_name, duration), pitch in zip(_REFERENCE_NOTES, pitches)
    )
    return MelodyObservation(route=name, source_model="test:fake", notes=notes)


def _route_runner(observation: MelodyObservation):
    def _runner(audio_path: str):
        return observation, {}

    return _runner


def _forbidden_route_runner():
    """G1 不成立分岐で「抽出が一切呼ばれない」ことを確認するための route_runner
    ——呼ばれたら即 fail する。"""

    def _runner(audio_path: str):  # pragma: no cover - must never execute
        raise AssertionError("route_runner must not be invoked when G1 fails")

    return _runner


def _build_fixture_project(tmp_path: Path, *, label: str, calibrated: bool) -> Path:
    """`demo_project`（不変更・コピーのみ）をベースに、melody experimental 配線
    に要る追加を YAML 辞書ミューテーションで足した作業コピーを組み立てる。"""
    dest = tmp_path / f"melody_e2e_{label}"
    dest.mkdir()
    shutil.copy(DEMO_PROJECT / "project.yaml", dest / "project.yaml")
    shutil.copy(DEMO_PROJECT / "composition_score.yaml", dest / "composition_score.yaml")
    shutil.copy(DEMO_PROJECT / "identity.yaml", dest / "identity.yaml")
    shutil.copytree(DEMO_PROJECT / "identity", dest / "identity")
    shutil.copytree(DEMO_PROJECT / "arrangements", dest / "arrangements")

    # 1) melody artifact を制御された note-events へ差し替え、identity.yaml の
    #    melody anchor sha256 をその場で再計算して同期する（source/lyrics/
    #    harmony 側の pin は無変更のまま — melody 以外の anchor に影響ゼロ）。
    melody_bytes = _note_events_bytes()
    (dest / "identity" / "melody_notes.json").write_bytes(melody_bytes)
    melody_sha256 = hashlib.sha256(melody_bytes).hexdigest()

    identity_data = yaml.safe_load((dest / "identity.yaml").read_text(encoding="utf-8"))
    melody_anchor = next(a for a in identity_data["anchors"] if a["id"] == "melody")
    melody_anchor["sha256"] = melody_sha256
    (dest / "identity.yaml").write_text(yaml.safe_dump(identity_data, sort_keys=False), encoding="utf-8")

    # 2) `edm.yaml` を deterministic backend 用の variant 専用 arrangement へ
    #    複製 + パッチ（`tests/test_recast_backend.py:_add_target_backend_variant`
    #    と同じ動機・辞書ミューテーション版）: target_backend override + bpm=60
    #    （score_reference の beat→秒換算をテスト側で手計算した値に固定するため
    #    — composition_score.yaml 自体は無変更） + melody axis_policy 追加。
    arrangement_data = yaml.safe_load(
        (dest / "arrangements" / "edm.yaml").read_text(encoding="utf-8")
    )
    arrangement_data.setdefault("target", {}).setdefault("rendering", {})["target_backend"] = (
        "deterministic"
    )
    arrangement_data["target"]["physical"]["bpm"] = BPM
    arrangement_data["preservation"].setdefault("score_fields", {})["rendering.target_backend"] = (
        "free"
    )
    arrangement_data["preservation"]["identity_anchors"]["melody"]["axis_policy"] = dict(
        _AXIS_POLICY
    )
    arrangement_path = dest / "arrangements" / f"{VARIANT_NAME}.yaml"
    arrangement_path.write_text(yaml.safe_dump(arrangement_data, sort_keys=False), encoding="utf-8")

    # 3) registries: M1 は常に実 registry の unmodified コピー。M3 は
    #    calibrated=True ならテスト内生成の frozen registry（3 軸）、False なら
    #    実 uncalibrated registry の unmodified コピー（凍結 fixture は
    #    不変更・不複製改変の対象外 — 単純ファイルコピーのみ）。
    (dest / "registry.yaml").write_bytes(REAL_M1_REGISTRY.read_bytes())
    if calibrated:
        m3_mapping = dict(_M3_REGISTRY_BASE)
        m3_mapping["evidence_thresholds"] = {
            "status": "frozen",
            "axes": {
                axis: {"strong_min": 0.8, "none_max": 0.3}
                for axis in ("contour", "interval", "rhythm")
            },
        }
        (dest / "m3_comparison_registry.yaml").write_text(
            yaml.safe_dump(m3_mapping, sort_keys=False), encoding="utf-8"
        )
    else:
        (dest / "m3_comparison_registry.yaml").write_bytes(
            REAL_M3_UNCALIBRATED_REGISTRY.read_bytes()
        )

    # 4) project.yaml: 新 variant + melody_take_band + observation.melody。
    project_data = yaml.safe_load((dest / "project.yaml").read_text(encoding="utf-8"))
    project_data["variants"][VARIANT_NAME] = {"arrangement": f"arrangements/{VARIANT_NAME}.yaml"}
    project_data["backends"]["deterministic"]["melody_take_band"] = "clear_lead"
    project_data["observation"]["enabled"] = True
    project_data["observation"]["melody"] = {
        "reference": "score",
        "comparison_registry": "m3_comparison_registry.yaml",
        "m1_registry": "registry.yaml",
        "route": "crepe_direct",
    }
    (dest / "project.yaml").write_text(yaml.safe_dump(project_data, sort_keys=False), encoding="utf-8")

    return dest / "project.yaml"


def _invoke(args: list[str]) -> str:
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return result.output


@dataclass(frozen=True)
class _RunResult:
    project_dir: Path
    contract: Any
    backend_ref: Any
    derived_score: Any
    channel_artifact_bytes: Dict[str, bytes]
    take_path: Path
    package_path: Path
    plan_warnings: List[str]


def _run_plan_and_generate(tmp_path: Path, *, label: str, calibrated: bool) -> _RunResult:
    """`recast plan` → `recast run`（deterministic/local backend）まで CLI で
    実行し、melody experimental 配線に要る plan 段の副産物（contract 等）を
    single-read 束から再取得する（`tests/test_recast_golden_path.py` の
    take-01 tail と同じ「local backend は run までしか進めない」前提）。"""
    project_path = _build_fixture_project(tmp_path, label=label, calibrated=calibrated)
    project_dir = project_path.parent

    _invoke(["recast", "plan", str(project_path), "--variant", VARIANT_NAME, "--backend", "deterministic"])
    _invoke(["recast", "run", str(project_path), "--variant", VARIANT_NAME, "--backend", "deterministic"])
    state = load_recast_state(project_dir)
    assert state.runs[f"{VARIANT_NAME}@deterministic"].state == "generated"

    loaded = load_recast_project(project_path)
    artifacts = build_recast_plan_artifacts(
        loaded, variant=VARIANT_NAME, backend="deterministic", publish=True
    )
    assert artifacts.result.plan.state_reached == "verified", artifacts.result.text
    assert artifacts.contract is not None
    assert artifacts.derived_score is not None

    take_path = (
        project_dir / "builds" / "takes" / f"{VARIANT_NAME}@deterministic" / "take-01.wav"
    )
    package_path = (
        resolve_packages_dir(loaded, VARIANT_NAME, "deterministic") / PERFORMANCE_PACKAGE_FILENAME
    )

    return _RunResult(
        project_dir=project_dir,
        contract=artifacts.contract,
        backend_ref=artifacts.backend_ref,
        derived_score=artifacts.derived_score,
        channel_artifact_bytes=artifacts.channel_artifact_bytes,
        take_path=take_path,
        package_path=package_path,
        plan_warnings=list(artifacts.result.plan.warnings),
    )


def _collect(run: _RunResult, *, route_runner) -> List[ExperimentalAnchorEntry]:
    loaded = load_recast_project(run.project_dir / "project.yaml")
    return collect_melody_experimental_anchors(
        contract=run.contract,
        melody_config=loaded.project.observation.melody,
        project_dir=run.project_dir,
        backend_ref=run.backend_ref,
        score=run.derived_score,
        channel_artifact_bytes=run.channel_artifact_bytes,
        take_audio_path=run.take_path,
        route_runner=route_runner,
    )


def _build_report_bytes(run: _RunResult, entries: List[ExperimentalAnchorEntry]) -> Tuple[bytes, Any]:
    take_sha256 = hashlib.sha256(run.take_path.read_bytes()).hexdigest()
    take_relative = str(run.take_path.relative_to(run.project_dir))
    loaded = load_recast_project(run.project_dir / "project.yaml")
    observation = observe_generated_artifact(
        package_path=run.package_path,
        manifest_path=loaded.identity_manifest_path,
        audio_path=run.take_path,
        generated_artifact_path=take_relative,
        expected_audio_sha256=take_sha256,
    )
    recast_report = build_recast_report(
        project_id=loaded.project.project.id,
        variant=VARIANT_NAME,
        backend="deterministic",
        package=observation.package,
        report=observation.report,
        take_path_relative=take_relative,
        take_sha256=take_sha256,
        observation_anchors=loaded.project.observation.anchors,
        experimental_anchors=entries,
    )
    report_bytes = (
        json.dumps(
            recast_report.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    return report_bytes, recast_report


# --------------------------------------------------------------------------- #
# 校正済み分岐: preserved / changed_within_policy / changed_outside_policy
# --------------------------------------------------------------------------- #
@pytest.mark.slow
def test_m4d_calibrated_branch_reaches_all_three_adherence_statuses(tmp_path: Path) -> None:
    run = _run_plan_and_generate(tmp_path, label="calibrated", calibrated=True)

    # plan warning: G1/G2/config が全て通れば "ok"。
    assert any(
        w == "melody anchor 'melody': experimental observability — ok" for w in run.plan_warnings
    )

    preserved_entries = _collect(
        run, route_runner=_route_runner(_take_observation(_REFERENCE_PITCHES, name="preserved"))
    )
    within_entries = _collect(
        run,
        route_runner=_route_runner(_take_observation(_WITHIN_POLICY_PITCHES, name="within")),
    )
    outside_entries = _collect(
        run,
        route_runner=_route_runner(_take_observation(_OUTSIDE_POLICY_PITCHES, name="outside")),
    )

    assert [e.anchor_id for e in preserved_entries] == ["melody"]
    assert preserved_entries[0].adherence_status == "preserved"
    assert within_entries[0].adherence_status == "changed_within_policy"
    assert outside_entries[0].adherence_status == "changed_outside_policy"

    # report/summary 統合 + 会計分離（coverage 分母不変）を preserved 分岐で確認。
    report_bytes, recast_report = _build_report_bytes(run, preserved_entries)
    assert recast_report.experimental_anchors == preserved_entries
    main_anchor_count = len(recast_report.anchors)
    main_coverage_total = (
        recast_report.coverage.verified
        + recast_report.coverage.violated
        + recast_report.coverage.not_observed
    )
    assert main_coverage_total == main_anchor_count  # experimental は分母に無関係

    summary_markdown = render_recast_summary_markdown(recast_report)
    assert "## Experimental anchors (melody)" in summary_markdown
    assert "preserved" in summary_markdown

    reports_dir = resolve_reports_dir(
        load_recast_project(run.project_dir / "project.yaml"), VARIANT_NAME, "deterministic"
    )
    from svp_rpe.recast.backend import atomic_publish_bytes_bundle

    atomic_publish_bytes_bundle(
        reports_dir,
        {
            RECAST_REPORT_FILENAME: report_bytes,
            RECAST_SUMMARY_FILENAME: summary_markdown.encode("utf-8"),
        },
        protected_inputs=(),
    )
    record_state(
        run.project_dir,
        VARIANT_NAME,
        "deterministic",
        "reported",
        note="m4d e2e",
        inputs_digest=None,
        plan_sha256=None,
        protected_inputs=(),
    )
    state = load_recast_state(run.project_dir)
    assert state.runs[f"{VARIANT_NAME}@deterministic"].state == "reported"


@pytest.mark.slow
def test_m4d_calibrated_branch_is_deterministic_across_independent_reruns(tmp_path: Path) -> None:
    """同一入力の 2 回の独立実行（別 tmp_path 副ディレクトリ）で
    recast_report.json バイト一致（preserved 分岐で確認）。"""
    run_a = _run_plan_and_generate(tmp_path, label="det_a", calibrated=True)
    run_b = _run_plan_and_generate(tmp_path, label="det_b", calibrated=True)

    entries_a = _collect(
        run_a, route_runner=_route_runner(_take_observation(_REFERENCE_PITCHES, name="preserved"))
    )
    entries_b = _collect(
        run_b, route_runner=_route_runner(_take_observation(_REFERENCE_PITCHES, name="preserved"))
    )

    bytes_a, _ = _build_report_bytes(run_a, entries_a)
    bytes_b, _ = _build_report_bytes(run_b, entries_b)
    assert bytes_a == bytes_b
    assert run_a.take_path.read_bytes() == run_b.take_path.read_bytes()


# --------------------------------------------------------------------------- #
# G1 不成立分岐: comparator_uncalibrated（抽出は一切呼ばれない）
# --------------------------------------------------------------------------- #
@pytest.mark.slow
def test_m4d_uncalibrated_registry_is_not_observed_and_never_extracts(tmp_path: Path) -> None:
    run = _run_plan_and_generate(tmp_path, label="uncalibrated", calibrated=False)

    assert any(
        w == "melody anchor 'melody': experimental observability — "
        "not expected (comparator_uncalibrated)"
        for w in run.plan_warnings
    )

    entries = _collect(run, route_runner=_forbidden_route_runner())
    assert len(entries) == 1
    assert entries[0].adherence_status == "not_observed"
    assert entries[0].reasons == ["comparator_uncalibrated"]
