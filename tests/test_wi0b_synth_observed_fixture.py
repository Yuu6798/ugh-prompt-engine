"""WI0-b 実推論初計測 (2026-07-20, 決定論 synth performer + basic-pitch/faster-whisper
実推論) fixture self-test.

`examples/arrangement/midnight_signal/observed/wi0b_synth/` に committed された、
WI0-b 事前登録計測（`plan.md`）の成果物一式（`plan.md` / `commands.md` /
`render_faithful.py` / `results.md` / `observed/wi0b_melody_observation.json` /
`observed/wi0b_lyrics_smoke_observation.json` / `observed/lyrics_anchor_extracted.json` /
`observed/wi0b_lyrics_extract.json`）を、`tests/test_ar4_observed_fixture.py` の型を
踏襲して schema readback + sha256 整合で固定する回帰テスト。

本バッチは実推論（basic-pitch 0.4.0 / faster-whisper small int8 + htdemucs_ft）を
伴うため CLI 再実行はしない — committed JSON を読み込んで整合性だけを検査する
（torch/tensorflow 非依存・not slow・CI で実推論は走らせない）。

wav 本体（`faithful_take.wav`）は非コミット: 決定論的レンダリング
（committed `composition_score.yaml` + `render_faithful.py`）から再生成可能なため、
sha256 pin (`4d8c83f67c1b2441e09fa84debdc47ec0131c1a13ee1b813b0ef55e874903e90`) のみを
`results.md` に記録する（`commands.md` の fresh-process 2/2 一致ログが根拠）。

PR #199 レビュー Codex P2 2 件対応（2026-07-20 相対パス再実行、`commands.md` /
`results.md` の Re-run 節参照）: 観測 JSON 3 本 (`wi0b_melody_observation.json` /
`wi0b_lyrics_smoke_observation.json` / `lyrics_anchor_extracted.json`) を stable な
リポジトリ相対パスで再実行した版に差し替え、`generated_artifact.path` が相対パスで
あることを固定した（絶対パス回帰防止）。合わせて no_speech_prob 系の実測証跡
(`observed/wi0b_lyrics_extract.json`, `svprpe extract --lyrics` 出力) を新規収載し、
segment 単位の `no_speech_prob` / `language` / `language_probability` を pin する。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from svp_rpe.arrange.observe import ObservationReport
from svp_rpe.rpe.models import RPEBundle

FIXTURE_DIR = Path("examples/arrangement/midnight_signal/observed/wi0b_synth")

PLAN_PATH = FIXTURE_DIR / "plan.md"
COMMANDS_PATH = FIXTURE_DIR / "commands.md"
RENDER_SCRIPT_PATH = FIXTURE_DIR / "render_faithful.py"
RESULTS_PATH = FIXTURE_DIR / "results.md"

MELODY_OBSERVATION_PATH = FIXTURE_DIR / "observed" / "wi0b_melody_observation.json"
LYRICS_SMOKE_OBSERVATION_PATH = FIXTURE_DIR / "observed" / "wi0b_lyrics_smoke_observation.json"
LYRICS_ANCHOR_EXTRACTED_PATH = FIXTURE_DIR / "observed" / "lyrics_anchor_extracted.json"
LYRICS_EXTRACT_PATH = FIXTURE_DIR / "observed" / "wi0b_lyrics_extract.json"

COMMITTED_PACKAGE_PATH = Path(
    "examples/arrangement/midnight_signal/expected/e2e_edm/performance_package.json"
)

RENDERED_WAV_SHA256 = "4d8c83f67c1b2441e09fa84debdc47ec0131c1a13ee1b813b0ef55e874903e90"

# The `svprpe observe` / `svprpe extract` audio argument used for the 2026-07-20
# relative-path re-run (Codex P2 review round, PR #199) — repo-root relative, stable
# across checkouts. See `commands.md` "Re-run" section.
GENERATED_ARTIFACT_RELATIVE_PATH = (
    "examples/arrangement/midnight_signal/observed/wi0b_synth/faithful_take.wav"
)


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
    assert LYRICS_EXTRACT_PATH.is_file()

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


def test_observations_generated_artifact_path_is_repo_relative() -> None:
    """Codex P2 (PR #199 review): committed observation reports must record a
    stable repo-relative `<audio>` path, not a machine-local `/tmp/...` path —
    otherwise re-running the documented command from a different checkout
    reproduces the pinned hashes but not the report bytes, which would
    invalidate the byte-reproducibility claim in `results.md` / `commands.md`.
    """
    for path in (MELODY_OBSERVATION_PATH, LYRICS_SMOKE_OBSERVATION_PATH):
        report = _load_json(path)
        artifact_path = report["generated_artifact"]["path"]
        assert artifact_path == GENERATED_ARTIFACT_RELATIVE_PATH, (
            f"{path}: generated_artifact.path is not the stable repo-relative "
            "path used by the 2026-07-20 re-run"
        )
        assert not Path(artifact_path).is_absolute()


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
    assert measurements["match_lyrics"]["overall_similarity"] == 0.0126
    assert measurements["canonical_line_count"] == 11

    assert lyrics["adherence_status"] == "not_observed"
    assert lyrics["determination"] == "deferred"


def test_lyrics_anchor_excerpt_matches_pinned_boundary_determination() -> None:
    excerpt = _load_json(LYRICS_ANCHOR_EXTRACTED_PATH)
    assert excerpt["adherence_status"] == "not_observed"
    assert excerpt["determination"] == "deferred"
    assert excerpt["measurements"]["match_lyrics"]["overall_similarity"] == 0.0126


# --- 6. extract 証跡（no_speech_prob 系）の schema readback + pin --------------------
# Codex P2 (PR #199 review): lyrics-boundary の no_speech_prob / language_probability
# claims (results.md §3) を裏付ける `svprpe extract --lyrics` 生出力を fixture へ収載し、
# 再読可能な形で pin する。


def test_lyrics_extract_loads_against_rpe_bundle_schema() -> None:
    raw = _load_json(LYRICS_EXTRACT_PATH)
    bundle = RPEBundle.model_validate(raw)
    assert bundle.audio_file == GENERATED_ARTIFACT_RELATIVE_PATH
    assert not Path(bundle.audio_file).is_absolute()
    assert bundle.learned_annotations is not None
    assert bundle.learned_annotations.lyrics_transcription is not None


def test_lyrics_extract_pins_no_speech_prob_and_language_evidence() -> None:
    """`results.md` §3 の no_speech_prob / language claims をこの fixture から
    再読可能な形で固定する（Codex P2: 証跡未収載の指摘対応）。
    """
    raw = _load_json(LYRICS_EXTRACT_PATH)
    lyrics_transcription = raw["learned_annotations"]["lyrics_transcription"]

    assert lyrics_transcription["language"] == "cy"
    assert lyrics_transcription["language_probability"] == 0.5489

    segments = lyrics_transcription["segments"]
    assert len(segments) == 3
    no_speech_probs = [segment["no_speech_prob"] for segment in segments]
    assert no_speech_probs == [0.9464, 0.9404, 0.9471]
    for prob in no_speech_probs:
        assert prob > 0.9, "no_speech_prob evidence should stay in the high-value band"

    inference_config = lyrics_transcription["inference_config"]
    assert inference_config["model_size"] == "small"
    assert inference_config["temperature"] == 0.0
