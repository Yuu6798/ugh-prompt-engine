"""WI0-b 実推論初計測 (2026-07-20, 決定論 synth performer + basic-pitch/faster-whisper
実推論) fixture self-test.

`examples/arrangement/midnight_signal/observed/wi0b_synth/` に committed された、
WI0-b 事前登録計測（`plan.md`）の成果物一式（`plan.md` / `commands.md` /
`render_faithful.py` / `results.md` / `observed/wi0b_melody_observation.json` /
`observed/wi0b_lyrics_smoke_observation.json` / `observed/lyrics_anchor_extracted.json`）
を、`tests/test_ar4_observed_fixture.py` の型を踏襲して schema readback +
sha256 整合で固定する回帰テスト。

本バッチは実推論（basic-pitch 0.4.0 / faster-whisper small int8 + htdemucs_ft）を
伴うため CLI 再実行はしない — committed JSON を読み込んで整合性だけを検査する
（torch/tensorflow 非依存・not slow・CI で実推論は走らせない）。

wav 本体（`faithful_take.wav`）は非コミット: 決定論的レンダリング
（committed `composition_score.yaml` + `render_faithful.py`）から再生成可能なため、
sha256 pin (`4d8c83f67c1b2441e09fa84debdc47ec0131c1a13ee1b813b0ef55e874903e90`) のみを
`results.md` に記録する（`commands.md` の fresh-process 2/2 一致ログが根拠）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from svp_rpe.arrange.observe import ObservationReport

FIXTURE_DIR = Path("examples/arrangement/midnight_signal/observed/wi0b_synth")

PLAN_PATH = FIXTURE_DIR / "plan.md"
COMMANDS_PATH = FIXTURE_DIR / "commands.md"
RENDER_SCRIPT_PATH = FIXTURE_DIR / "render_faithful.py"
RESULTS_PATH = FIXTURE_DIR / "results.md"

MELODY_OBSERVATION_PATH = FIXTURE_DIR / "observed" / "wi0b_melody_observation.json"
LYRICS_SMOKE_OBSERVATION_PATH = FIXTURE_DIR / "observed" / "wi0b_lyrics_smoke_observation.json"
LYRICS_ANCHOR_EXTRACTED_PATH = FIXTURE_DIR / "observed" / "lyrics_anchor_extracted.json"

COMMITTED_PACKAGE_PATH = Path(
    "examples/arrangement/midnight_signal/expected/e2e_edm/performance_package.json"
)

RENDERED_WAV_SHA256 = "4d8c83f67c1b2441e09fa84debdc47ec0131c1a13ee1b813b0ef55e874903e90"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- 0. 事前登録・runbook・結果ドキュメントが存在する ------------------------------


def test_plan_commands_render_script_and_results_exist() -> None:
    assert PLAN_PATH.is_file()
    assert COMMANDS_PATH.is_file()
    assert RENDER_SCRIPT_PATH.is_file()
    assert RESULTS_PATH.is_file()

    plan_text = PLAN_PATH.read_text(encoding="utf-8")
    assert "Registered at (UTC): 2026-07-20T15:15:24Z" in plan_text
    assert "pitch_lcs_ratio >= 0.8" in plan_text
    assert "pitch_lcs_ratio < 0.8" in plan_text


# --- 1. 観測 JSON が ObservationReport schema でロード可能 --------------------------


def test_melody_and_lyrics_smoke_observations_load_against_observation_report_schema() -> None:
    for path in (MELODY_OBSERVATION_PATH, LYRICS_SMOKE_OBSERVATION_PATH):
        raw = _load_json(path)
        report = ObservationReport.model_validate(raw)
        assert report.schema_version == "observation-report/0.1"
        assert report.work_id == "midnight-signal"


def test_lyrics_anchor_extracted_is_a_verbatim_single_anchor_excerpt() -> None:
    """`lyrics_anchor_extracted.json` は `wi0b_lyrics_smoke_observation.json` の
    lyrics anchor 要素を verbatim 抜粋したもの（`commands.md` 記載の手動抜粋）。
    親 report 側の同じ anchor と完全一致することを固定する。
    """
    excerpt = _load_json(LYRICS_ANCHOR_EXTRACTED_PATH)
    parent = _load_json(LYRICS_SMOKE_OBSERVATION_PATH)
    lyrics_anchors = [a for a in parent["anchors"] if a["anchor_id"] == "lyrics"]
    assert len(lyrics_anchors) == 1
    assert excerpt == lyrics_anchors[0]


# --- 2. package_sha256 pin == committed performance_package.json の実 sha256 ------------


def test_observations_agree_on_package_sha256_with_committed_package() -> None:
    package_sha256 = _sha256(COMMITTED_PACKAGE_PATH)

    for path in (MELODY_OBSERVATION_PATH, LYRICS_SMOKE_OBSERVATION_PATH):
        report = _load_json(path)
        assert report["package_sha256"] == package_sha256, (
            f"{path}: package_sha256 does not match committed "
            f"performance_package.json sha256"
        )


# --- 3. 決定論レンダリング wav の sha256 pin が report / results.md と一致 ---------------


def test_observations_generated_artifact_sha256_matches_pinned_render_sha256() -> None:
    for path in (MELODY_OBSERVATION_PATH, LYRICS_SMOKE_OBSERVATION_PATH):
        report = _load_json(path)
        assert report["generated_artifact"]["sha256"] == RENDERED_WAV_SHA256, (
            f"{path}: generated_artifact.sha256 does not match the pinned "
            "deterministic render sha256"
        )

    results_text = RESULTS_PATH.read_text(encoding="utf-8")
    assert RENDERED_WAV_SHA256 in results_text


# --- 4. melody 観測の主要値 pin（WI2 v0 除外判定の根拠データ）------------------------


def test_melody_observation_pins_wi0b_measured_values() -> None:
    report = _load_json(MELODY_OBSERVATION_PATH)
    anchors = {anchor["anchor_id"]: anchor for anchor in report["anchors"]}
    melody = anchors["melody"]

    assert melody["sensor"]["name"] == "note_events"
    assert melody["sensor"]["available"] is True

    measurements = melody["measurements"]
    assert measurements["pitch_lcs_ratio"] == 0.6
    assert measurements["interval_lcs_ratio"] == 0.4444
    assert measurements["canonical_length"] == 10
    assert measurements["observed_length"] == 108
    assert measurements["pitch_sequence_exact_match"] is False
    assert measurements["observed_head"] == [36, 36, 55, 51, 48, 51, 48, 55]

    assert melody["adherence_status"] == "not_observed"
    assert melody["determination"] == "deferred"


def test_melody_pitch_lcs_ratio_is_below_the_preregistered_v0_adoption_threshold() -> None:
    """`plan.md` の事前登録ルール（`pitch_lcs_ratio >= 0.8` -> v0 採用候補）を
    実測値に適用した結果が「除外」側であることを固定する — WI2 v0 の軸集合に
    melody を含めない設計判断の根拠データ（`docs/work_identity_roadmap.md` WI0 節、
    `results.md` §2 参照）。
    """
    report = _load_json(MELODY_OBSERVATION_PATH)
    anchors = {anchor["anchor_id"]: anchor for anchor in report["anchors"]}
    pitch_lcs_ratio = anchors["melody"]["measurements"]["pitch_lcs_ratio"]

    v0_adoption_threshold = 0.8
    assert pitch_lcs_ratio < v0_adoption_threshold


# --- 5. lyrics smoke 観測の determination/adherence pin（境界記録・verdict なし）--------


def test_lyrics_smoke_observation_pins_boundary_determination() -> None:
    report = _load_json(LYRICS_SMOKE_OBSERVATION_PATH)
    anchors = {anchor["anchor_id"]: anchor for anchor in report["anchors"]}
    lyrics = anchors["lyrics"]

    assert lyrics["sensor"]["name"] == "lyrics_transcription"
    assert lyrics["sensor"]["available"] is True

    measurements = lyrics["measurements"]
    assert measurements["match_lyrics"]["overall_similarity"] == 0.0056
    assert measurements["canonical_line_count"] == 11

    assert lyrics["adherence_status"] == "not_observed"
    assert lyrics["determination"] == "deferred"


def test_lyrics_anchor_excerpt_matches_pinned_boundary_determination() -> None:
    excerpt = _load_json(LYRICS_ANCHOR_EXTRACTED_PATH)
    assert excerpt["adherence_status"] == "not_observed"
    assert excerpt["determination"] == "deferred"
    assert excerpt["measurements"]["match_lyrics"]["overall_similarity"] == 0.0056
