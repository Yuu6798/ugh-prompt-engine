"""tests/test_roundtrip_repetition.py — R3 stochastic performer repetition harness.

Pure aggregation/selection logic on synthetic `RepetitionTakeResult` objects
stays in the fast loop. Anything that runs the real RPE extractor (via
`run_repetition_batch` on synthesized audio, or the CLI end-to-end) is marked
`@pytest.mark.slow` per-test.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from svp_rpe.cli import app
from svp_rpe.compose import load_composition_score
from svp_rpe.perform import FAITHFUL_TAKE, FIRST_TAKE, perform, sha256_bytes, wav_bytes
from svp_rpe.roundtrip import (
    FieldRepetitionSummary,
    RepetitionRankingEntry,
    RepetitionReport,
    RepetitionTakeResult,
    SelectionResult,
    TakeFieldObservation,
    load_takes_for_repetition,
    render_repetition_text,
    run_repetition_batch,
)
from svp_rpe.roundtrip.repetition import R3_SELECTION_FIELDS, _select, _summarize_fields

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SCORE = ROOT / "examples" / "roundtrip" / "synth_01_source.yaml"

runner = CliRunner()


def _assert_no_outcome_keys(value: Any) -> None:
    if isinstance(value, dict):
        for forbidden in ("verdict", "passed", "pass", "failed", "fail", "ok", "loss"):
            assert forbidden not in value
        for item in value.values():
            _assert_no_outcome_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_outcome_keys(item)


def _observation(field: str, diagnosis: str) -> TakeFieldObservation:
    return TakeFieldObservation(
        field=field,
        expected_value=1,
        observed_value=1 if diagnosis == "preserved" else 2,
        diagnosis=diagnosis,
        preserved=diagnosis == "preserved",
    )


def _take(take_id: str, diagnoses: dict[str, str]) -> RepetitionTakeResult:
    fields = [_observation(field, diagnosis) for field, diagnosis in diagnoses.items()]
    return RepetitionTakeResult(
        take_id=take_id,
        audio_sha256="0" * 64,
        fields=fields,
        preserved_count=sum(1 for f in fields if f.preserved),
    )


# ---------------------------------------------------------------------------
# Pure logic: field summary + selection, on synthetic takes (fast)
# ---------------------------------------------------------------------------


def test_field_summary_aggregates_counts_and_preserved_rate():
    takes = [
        _take("t0", {"bpm": "preserved", "key": "preserved"}),
        _take("t1", {"bpm": "preserved", "key": "calibration_disagreement"}),
        _take("t2", {"bpm": "sensor_blind", "key": "calibration_disagreement"}),
    ]

    summaries = _summarize_fields(takes)

    by_field = {item.field: item for item in summaries}
    assert set(by_field) == {"bpm", "key"}

    bpm = by_field["bpm"]
    assert bpm.n == 3
    assert bpm.preserved_count == 2
    assert bpm.preserved_rate == pytest.approx(2 / 3, abs=1e-4)
    assert sum(bpm.diagnosis_counts.values()) == bpm.n
    assert len(bpm.observed_values) == bpm.n

    key = by_field["key"]
    assert key.n == 3
    assert key.preserved_count == 1
    assert key.preserved_rate == pytest.approx(1 / 3, abs=1e-4)
    assert sum(key.diagnosis_counts.values()) == key.n


def test_selection_ranks_by_preserved_count_descending_with_take_id_tiebreak():
    takes = [
        _take("b", {"brightness": "preserved", "key": "preserved"}),
        _take("a", {"brightness": "preserved", "key": "preserved"}),
        _take("c", {"brightness": "sensor_blind", "key": "sensor_blind"}),
    ]

    selection = _select(takes, R3_SELECTION_FIELDS)

    assert selection.basis == "preserved_field_count"
    assert selection.selection_fields == ["key", "brightness"]
    assert [entry.take_id for entry in selection.ranking] == ["a", "b", "c"]
    assert [entry.preserved_count for entry in selection.ranking] == [2, 2, 0]
    # tie between "a" and "b" (both preserved_count=2) breaks on take_id ascending
    assert selection.selected_take_id == "a"


def test_run_repetition_batch_rejects_underpowered_batches(tmp_path: Path):
    # Codex #135 P2: R3-2/R3-3 は n>1 の計器。n=0/1 のバッチはレポートを
    # 作らず fail-fast する（音声に触る前に落ちる = 実在しないパスで検証）。
    score = load_composition_score(SOURCE_SCORE)
    missing = tmp_path / "does_not_exist.wav"

    for takes in ([], [("only_take", missing)]):
        with pytest.raises(ValueError, match="n>1"):
            run_repetition_batch(
                score,
                takes,
                generator="musicgen-small",
                score_ref=str(SOURCE_SCORE),
            )


def test_selection_scope_excludes_untrusted_fields_from_the_count():
    # Codex #135 P2: bpm/time_signature/active_rate を大量に保存するテイクが、
    # R3 の信頼ノブ（key/brightness）を保存するテイクより上位に来てはならない。
    takes = [
        _take(
            "aux_heavy",
            {
                "bpm": "preserved",
                "time_signature": "preserved",
                "active_rate_target": "preserved",
                "key": "calibration_disagreement",
                "brightness": "calibration_disagreement",
            },
        ),
        _take(
            "trusted",
            {
                "bpm": "calibration_disagreement",
                "time_signature": "calibration_disagreement",
                "active_rate_target": "calibration_disagreement",
                "key": "preserved",
                "brightness": "preserved",
            },
        ),
    ]

    selection = _select(takes, R3_SELECTION_FIELDS)

    assert selection.selected_take_id == "trusted"
    counts = {entry.take_id: entry.preserved_count for entry in selection.ranking}
    assert counts == {"trusted": 2, "aux_heavy": 0}


def test_render_repetition_text_and_json_carry_no_outcome_keys():
    report = RepetitionReport(
        score_ref="examples/roundtrip/synth_01_source.yaml",
        generator="musicgen-small",
        n_takes=2,
        fields=[
            FieldRepetitionSummary(
                field="bpm",
                n=2,
                preserved_count=1,
                preserved_rate=0.5,
                diagnosis_counts={"preserved": 1, "calibration_disagreement": 1},
                observed_values=[60, 90],
            )
        ],
        takes=[
            _take("t0", {"bpm": "preserved"}),
            _take("t1", {"bpm": "calibration_disagreement"}),
        ],
        selection=SelectionResult(
            ranking=[
                RepetitionRankingEntry(take_id="t0", preserved_count=1),
                RepetitionRankingEntry(take_id="t1", preserved_count=0),
            ],
            selected_take_id="t0",
        ),
    )

    text = render_repetition_text(report)
    assert "Repetition Roundtrip" in text
    assert "t0" in text and "t1" in text
    for forbidden in ("verdict", "passed", "failed", " ok ", "loss"):
        assert forbidden not in text.lower()

    payload = report.model_dump(mode="json")
    assert payload["schema_version"] == "1.0"
    _assert_no_outcome_keys(payload)


# ---------------------------------------------------------------------------
# load_takes_for_repetition: manifest hygiene (fast, no extraction)
# ---------------------------------------------------------------------------


def _write_dummy_audio(path: Path, payload: bytes = b"not-really-a-wav-but-bytes") -> bytes:
    path.write_bytes(payload)
    return payload


def test_load_takes_for_repetition_fails_fast_on_sha256_mismatch(tmp_path: Path):
    audio_path = tmp_path / "take_0.wav"
    _write_dummy_audio(audio_path)
    manifest = {
        "samples": [
            {
                "sample_id": "take_0",
                "audio_path": "take_0.wav",
                "audio_sha256": "0" * 64,
            }
        ]
    }

    with pytest.raises(ValueError, match="audio_sha256 does not match"):
        load_takes_for_repetition(manifest, audio_dir=tmp_path)


def test_load_takes_for_repetition_skips_excluded_samples(tmp_path: Path):
    kept_path = tmp_path / "take_0.wav"
    kept_bytes = _write_dummy_audio(kept_path)
    excluded_path = tmp_path / "take_1.wav"
    excluded_bytes = _write_dummy_audio(excluded_path, b"other-bytes")

    manifest = {
        "samples": [
            {
                "sample_id": "take_0",
                "audio_path": "take_0.wav",
                "audio_sha256": sha256_bytes(kept_bytes),
            },
            {
                "sample_id": "take_1",
                "audio_path": "take_1.wav",
                "audio_sha256": sha256_bytes(excluded_bytes),
                "excluded": True,
            },
        ]
    }

    takes = load_takes_for_repetition(manifest, audio_dir=tmp_path)

    assert [take_id for take_id, _ in takes] == ["take_0"]


def test_load_takes_for_repetition_treats_missing_excluded_key_as_included(tmp_path: Path):
    audio_path = tmp_path / "take_0.wav"
    audio_bytes = _write_dummy_audio(audio_path)
    manifest = {
        "samples": [
            {
                "sample_id": "take_0",
                "audio_path": "take_0.wav",
                "audio_sha256": sha256_bytes(audio_bytes),
            }
        ]
    }

    takes = load_takes_for_repetition(manifest, audio_dir=tmp_path)

    assert [take_id for take_id, _ in takes] == ["take_0"]


# ---------------------------------------------------------------------------
# run_repetition_batch: real deterministic-performer takes (slow: real extraction)
# ---------------------------------------------------------------------------


def _build_takes_manifest(tmp_path: Path, score) -> dict:
    """Synthesize 3 takes with the deterministic performer (2 faithful, 1 deviant).

    This stands in for stochastic generator output without requiring
    MusicGen/torch: the harness under test only cares that takes are
    independent audio files with a pinned manifest, not how they were
    produced.
    """
    styles = [FAITHFUL_TAKE, FAITHFUL_TAKE, FIRST_TAKE]
    samples = []
    for index, style in enumerate(styles):
        take_bytes = wav_bytes(perform(score, style))
        audio_path = tmp_path / f"take_{index}.wav"
        audio_path.write_bytes(take_bytes)
        samples.append(
            {
                "sample_id": f"take_{index}",
                "audio_path": audio_path.name,
                "audio_sha256": sha256_bytes(take_bytes),
            }
        )
    return {
        "schema_version": "1.0",
        "fixture_id": "synth_01_repetition",
        "generator": "musicgen-small",
        "score_path": str(SOURCE_SCORE),
        "prompt": "stand-in prompt",
        "samples": samples,
    }


@pytest.mark.slow
def test_run_repetition_batch_reports_field_and_selection_statistics(tmp_path: Path):
    score = load_composition_score(SOURCE_SCORE)
    manifest = _build_takes_manifest(tmp_path, score)
    takes = load_takes_for_repetition(manifest, audio_dir=tmp_path)

    report = run_repetition_batch(
        score, takes, generator="musicgen-small", score_ref=str(SOURCE_SCORE)
    )

    assert report.n_takes == 3
    assert len(report.takes) == 3

    for take in report.takes:
        assert take.preserved_count == sum(1 for f in take.fields if f.preserved)

    for summary in report.fields:
        assert summary.n == report.n_takes
        assert 0.0 <= summary.preserved_rate <= 1.0
        assert summary.preserved_rate == pytest.approx(
            summary.preserved_count / summary.n, abs=1e-4
        )
        assert sum(summary.diagnosis_counts.values()) == summary.n
        assert len(summary.observed_values) == summary.n

    ranking = report.selection.ranking
    preserved_counts = [entry.preserved_count for entry in ranking]
    assert preserved_counts == sorted(preserved_counts, reverse=True)
    assert report.selection.basis == "preserved_field_count"
    assert report.selection.selected_take_id == ranking[0].take_id

    _assert_no_outcome_keys(report.model_dump(mode="json"))


@pytest.mark.slow
def test_run_repetition_batch_is_deterministic_across_runs(tmp_path: Path):
    score = load_composition_score(SOURCE_SCORE)
    manifest = _build_takes_manifest(tmp_path, score)
    takes = load_takes_for_repetition(manifest, audio_dir=tmp_path)

    first = run_repetition_batch(
        score, takes, generator="musicgen-small", score_ref=str(SOURCE_SCORE)
    )
    second = run_repetition_batch(
        score, takes, generator="musicgen-small", score_ref=str(SOURCE_SCORE)
    )

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


# ---------------------------------------------------------------------------
# CLI smoke test (roundtrip-rep)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_roundtrip_rep_cli_emits_schema_version_json(tmp_path: Path):
    score = load_composition_score(SOURCE_SCORE)
    manifest = _build_takes_manifest(tmp_path, score)
    manifest_path = tmp_path / "takes_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "roundtrip-rep",
            str(SOURCE_SCORE),
            str(manifest_path),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "1.0"
    assert payload["n_takes"] == 3
    _assert_no_outcome_keys(payload)
