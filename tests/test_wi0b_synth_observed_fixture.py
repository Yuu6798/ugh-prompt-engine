"""WI0-b 実推論初計測 (2026-07-20, 決定論 synth performer + basic-pitch/faster-whisper
実推論) fixture self-test.

`examples/arrangement/midnight_signal/observed/wi0b_synth/` に committed された、
WI0-b 事前登録計測（`plan.md`）の成果物一式（`plan.md` / `commands.md` /
`render_faithful.py` / `results.md` / `observed/wi0b_melody_observation.json` /
`observed/wi0b_lyrics_smoke_observation.json` / `observed/lyrics_anchor_extracted.json` /
`observed/wi0b_lyrics_extract.json` / `observed/provenance.yaml`）を、
`tests/test_ar4_observed_fixture.py` の型を踏襲して schema readback + sha256 整合で
固定する回帰テスト。

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

PR #199 レビュー Codex P2 2 件目対応（"Link the extract evidence to the pinned
audio hash"）: `observed/provenance.yaml` を新設し、上記 4 観測ファイル
（melody 観測 / lyrics smoke 観測 / lyrics_anchor_extracted / wi0b_lyrics_extract）が
同一 wav（`source_audio_sha256` pin）から同一セッションで生成された旨を機械可読で
固定した。本ファイルではその sidecar を (a) YAML としてロード可能であること、
(b) `source_audio_sha256` が両観測 JSON の `generated_artifact.sha256` と一致する
こと、(c) sidecar に記録された各ファイル sha256 が committed ファイルの実 sha256 と
一致すること（stale 差し替え検出）を検査する。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

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
PROVENANCE_PATH = FIXTURE_DIR / "observed" / "provenance.yaml"

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


# --- 7. provenance サイドカー: 音源 hash 接続の機械的突合 -----------------------------
# Codex P2 (PR #199 review, 2 件目): "Link the extract evidence to the pinned audio
# hash" — melody 観測 / lyrics smoke 観測 / lyrics_anchor_extracted / wi0b_lyrics_extract
# の 4 観測ファイルが同一 wav（`source_audio_sha256`）から同一セッションで生成された
# ことを、散文 (results.md) だけでなく機械可読 sidecar で固定する。


def _load_provenance() -> dict:
    return yaml.safe_load(PROVENANCE_PATH.read_text(encoding="utf-8"))


def test_provenance_sidecar_is_loadable_and_pins_source_audio_sha256() -> None:
    provenance = _load_provenance()
    assert provenance["format"] == "wi0b-observation-provenance/0.1"
    assert provenance["source_audio_sha256"] == RENDERED_WAV_SHA256

    observed_paths = {entry["path"] for entry in provenance["observed_files"]}
    assert observed_paths == {
        MELODY_OBSERVATION_PATH.name,
        LYRICS_SMOKE_OBSERVATION_PATH.name,
        LYRICS_ANCHOR_EXTRACTED_PATH.name,
        LYRICS_EXTRACT_PATH.name,
    }


def test_provenance_source_audio_sha256_matches_both_observation_reports() -> None:
    """`source_audio_sha256` == melody / lyrics smoke 観測 JSON の
    `generated_artifact.sha256`（機械的突合）。lyrics_anchor_extracted.json と
    wi0b_lyrics_extract.json はそれぞれ抜粋 / RPEBundle スキーマ（sha256 field
    なし）のため、この直接突合の対象は ObservationReport 2 本に限る。
    """
    provenance = _load_provenance()

    for path in (MELODY_OBSERVATION_PATH, LYRICS_SMOKE_OBSERVATION_PATH):
        report = _load_json(path)
        assert report["generated_artifact"]["sha256"] == provenance["source_audio_sha256"], (
            f"{path}: generated_artifact.sha256 does not match provenance.yaml "
            "source_audio_sha256"
        )


def test_provenance_recorded_file_hashes_match_committed_files() -> None:
    """provenance.yaml に記録された各ファイルの sha256 が committed ファイルの実
    sha256 と一致することを固定する（stale 差し替え検出: 観測 JSON を更新したのに
    provenance.yaml の pin を更新し忘れた場合にここで落ちる）。
    """
    provenance = _load_provenance()
    entries_by_path = {entry["path"]: entry for entry in provenance["observed_files"]}

    for path in (
        MELODY_OBSERVATION_PATH,
        LYRICS_SMOKE_OBSERVATION_PATH,
        LYRICS_ANCHOR_EXTRACTED_PATH,
        LYRICS_EXTRACT_PATH,
    ):
        entry = entries_by_path[path.name]
        assert entry["sha256"] == _sha256(path), (
            f"{path.name}: provenance.yaml sha256 pin is stale relative to the "
            "committed file"
        )


def test_results_md_references_provenance_sidecar() -> None:
    results_text = RESULTS_PATH.read_text(encoding="utf-8")
    assert "provenance.yaml" in results_text


def test_provenance_extract_entry_attested_hash_matches_source_audio_sha256() -> None:
    """PR #199 Codex P2 3件目対応 ("Pin the extract input hash"): `wi0b_lyrics_extract.json`
    エントリの `source_audio_sha256_ref` はもう `null` ではなく、`source_audio_sha256` と
    同値の実 hash を明示する。RPEBundle スキーマに入力音声 hash 欄が無いため、他 3
    エントリのような file-in-file の機械照合はできない（`input_audio_sha256_basis` が
    根拠種別 "same-session attestation" を明記）— この非対称性自体を固定する。
    """
    provenance = _load_provenance()
    entries_by_path = {entry["path"]: entry for entry in provenance["observed_files"]}

    extract_entry = entries_by_path[LYRICS_EXTRACT_PATH.name]
    assert extract_entry["source_audio_sha256_ref"] == provenance["source_audio_sha256"]
    assert "attestation" in extract_entry["input_audio_sha256_basis"]

    for path in (MELODY_OBSERVATION_PATH, LYRICS_SMOKE_OBSERVATION_PATH):
        entry = entries_by_path[path.name]
        assert entry["input_audio_sha256_basis"] == "mechanical (generated_artifact.sha256 in-file)"


# --- 8. 決定論部分の機械的接地: render -> extract を実行し committed fixture と突合 -------
# PR #199 Codex P2 3件目 ("Pin the extract input hash") への層 2 対応。RPEBundle が入力
# 音声の hash を保持しないため、extract 証跡と pin 済み wav の完全な機械的紐付けは
# schema 上不可能 — 代わりに、決定論レンダリング (render_faithful.py) の出力 wav の
# sha256 が pin と一致すること、かつその wav に対するルールベース抽出
# (`extract_rpe_from_file`, --lyrics なし・learned なし) の決定論 physical 値が committed
# `wi0b_lyrics_extract.json` と一致することを実行時に検証する。これにより「pin された
# wav -> committed extract 証跡の決定論部分」の連鎖が機械的に閉じる（attestation を
# 実行で裏付ける）。render も rule-based extract も heavy extras（basic-pitch /
# faster-whisper / torch / demucs）を要さないため slow だが CI 安全。


@pytest.mark.slow
def test_render_faithful_and_rule_based_extract_match_committed_deterministic_fixture(
    tmp_path: Path,
) -> None:
    """render_faithful.py を documented どおり別プロセスで実行し (`commands.md` の
    invocation を再現)、出力 wav の sha256 が pin と一致することを確認したうえで、
    その wav に対しルールベース抽出 (`extract_rpe_from_file`, --lyrics なし・learned
    なし) を実行して committed `wi0b_lyrics_extract.json` の決定論 physical 欄と
    突合する。
    """
    score_path = Path("examples/arrangement/midnight_signal/composition_score.yaml")
    wav_path = tmp_path / "faithful_take.wav"

    result = subprocess.run(
        [sys.executable, str(RENDER_SCRIPT_PATH), str(score_path), str(wav_path)],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"render_faithful.py failed (stdout={result.stdout!r}, stderr={result.stderr!r})"
    )
    assert wav_path.is_file()

    rendered_sha256 = _sha256(wav_path)
    assert rendered_sha256 == RENDERED_WAV_SHA256, (
        "render_faithful.py output does not reproduce the sha256 pinned in "
        "provenance.yaml / results.md — deterministic re-render is broken"
    )

    from svp_rpe.rpe.extractor import extract_rpe_from_file

    bundle = extract_rpe_from_file(str(wav_path))
    physical = bundle.physical

    committed = _load_json(LYRICS_EXTRACT_PATH)["physical"]

    assert physical.duration_sec == pytest.approx(committed["duration_sec"])
    assert physical.bpm == pytest.approx(committed["bpm"])
    assert physical.key == committed["key"]
    assert physical.mode == committed["mode"]
    assert physical.spectral_centroid == pytest.approx(committed["spectral_centroid"])
    assert physical.rms_mean == pytest.approx(committed["rms_mean"])
    assert physical.dynamic_range_db == pytest.approx(committed["dynamic_range_db"])
