"""AR4 tests: ObservationReport models, harmony sensor, D-3 chain verification, CLI.

Fast tests cover the Pydantic models (schema/Literal/64hex/extra=forbid), the
per-anchor sensor dispatch (`_observe_harmony` / `_observe_unavailable`) via
synthetic `RPEBundle` fixtures (no audio synthesis), and the CLI's D-3
provenance-chain verification failure modes (all of which fail *before* any
audio is touched, so they need no real extraction either).

The one test that actually performs + extracts real audio
(`test_observe_cli_e2e_harmony_measurement_is_pinned_and_deterministic`) is
marked `@pytest.mark.slow` per repo convention.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from svp_rpe.arrange.identity import IdentityAnchor, IdentityManifest
from svp_rpe.arrange.observe import (
    OBSERVATION_REPORT_SCHEMA_VERSION,
    AnchorObservation,
    GeneratedArtifactRef,
    ObservationReport,
    SensorRecord,
    _observe_anchor,
    _observe_harmony,
    _observe_unavailable,
    build_observation_report,
)
from svp_rpe.arrange.package import PerformancePackage
from svp_rpe.cli import app
from svp_rpe.compose.models import CompositionScore
from svp_rpe.perform import FAITHFUL_TAKE, perform, wav_bytes
from svp_rpe.rpe.models import ChordEvent, PhysicalRPE, RPEBundle, SectionMarker, SpectralProfile
from svp_rpe.rpe.semantic_rules import generate_semantic

FIXTURE_DIR = Path("examples/arrangement/midnight_signal")
BASE_SCORE = FIXTURE_DIR / "composition_score.yaml"
IDENTITY_MANIFEST = FIXTURE_DIR / "identity_manifest.yaml"
EDM_IDENTITY_SPEC = FIXTURE_DIR / "edm.identity.arrangement.yaml"
SUNO_PROFILE_PATH = Path("config/capability_profiles/suno.yaml")
DERIVED_SCORE_PATH = FIXTURE_DIR / "expected" / "edm" / "derived_score.yaml"
CANONICAL_PROGRESSION = [
    ("C", "minor"),
    ("G#", "major"),
    ("A#", "major"),
    ("C", "minor"),
]


def _bundle_with_chords(chords: list[tuple[str, str]]) -> RPEBundle:
    """Synthetic RPEBundle carrying only the chord_events needed by `_observe_harmony`."""
    chord_events = [
        ChordEvent(
            chord=f"{root} {quality}",
            root=root,
            quality=quality,
            start_sec=float(index),
            end_sec=float(index + 1),
            confidence=0.9,
        )
        for index, (root, quality) in enumerate(chords)
    ]
    physical = PhysicalRPE(
        bpm=120.0,
        bpm_confidence=0.9,
        key="C",
        mode="minor",
        key_confidence=0.9,
        duration_sec=8.0,
        sample_rate=44100,
        time_signature="4/4",
        time_signature_confidence=0.9,
        structure=[SectionMarker(label="full", start_sec=0.0, end_sec=8.0)],
        rms_mean=0.2,
        peak_amplitude=0.8,
        crest_factor=4.0,
        active_rate=0.73,
        valley_depth=0.18,
        thickness=1.0,
        spectral_centroid=900.0,
        spectral_profile=SpectralProfile(
            centroid=900.0, low_ratio=0.4, mid_ratio=0.5, high_ratio=0.1, brightness=0.1,
        ),
        stereo_profile=None,
        onset_density=2.0,
        chord_events=chord_events,
    )
    return RPEBundle(
        physical=physical,
        semantic=generate_semantic(physical),
        audio_file="fixture.wav",
        audio_duration_sec=8.0,
        audio_sample_rate=44100,
        audio_channels=1,
        audio_format="wav",
    )


def _anchor(anchor_id: str, domain: str, artifact: str, artifact_type: str) -> IdentityAnchor:
    return IdentityAnchor(
        id=anchor_id,
        domain=domain,
        artifact=artifact,
        artifact_type=artifact_type,
        media_type="application/json",
        format_version=None,
        sha256=hashlib.sha256(anchor_id.encode()).hexdigest(),
        required=True,
    )


# --- 1. model schema (extra=forbid / 64hex pattern / Literal) ----------------------


def test_sensor_record_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SensorRecord.model_validate({"name": "x", "available": True, "extra": "no"})


def test_anchor_observation_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AnchorObservation.model_validate(
            {
                "anchor_id": "a",
                "domain": "harmony",
                "sensor": {"name": "x", "available": True},
                "measurements": {},
                "adherence_status": "not_observed",
                "determination": "no_sensor",
                "extra": "no",
            }
        )


def test_anchor_observation_rejects_invalid_determination() -> None:
    with pytest.raises(ValidationError):
        AnchorObservation.model_validate(
            {
                "anchor_id": "a",
                "domain": "harmony",
                "sensor": {"name": "x", "available": True},
                "measurements": {},
                "adherence_status": "not_observed",
                "determination": "changed_within_policy",  # not one of the 3 D-1 branches
            }
        )


def test_generated_artifact_ref_requires_64_hex_sha256() -> None:
    GeneratedArtifactRef(path="a.wav", sha256="0" * 64)
    with pytest.raises(ValidationError):
        GeneratedArtifactRef(path="a.wav", sha256="not-a-hash")


def test_observation_report_requires_64_hex_package_sha256() -> None:
    with pytest.raises(ValidationError):
        ObservationReport(
            schema_version=OBSERVATION_REPORT_SCHEMA_VERSION,
            work_id="w",
            package_sha256="short",
            generated_artifact=GeneratedArtifactRef(path="a.wav", sha256="0" * 64),
            anchors=[],
        )


def test_observation_report_has_no_verdict_fields() -> None:
    # D-1: the schema itself must not carry a global verdict/pass-fail/count key.
    assert set(ObservationReport.model_fields) == {
        "schema_version",
        "work_id",
        "package_sha256",
        "generated_artifact",
        "anchors",
    }


# --- 2. no_sensor dispatch (lyrics / melody / other domains) -----------------------


def test_observe_unavailable_lyrics_is_no_sensor_with_reason() -> None:
    observation = _observe_unavailable(_anchor("lyrics", "lyrics", "lyrics.txt", "lyrics_text"))
    assert observation.sensor.available is False
    assert observation.sensor.name == "lyrics_transcription"
    assert "lyrics" in str(observation.sensor.reason)
    assert observation.adherence_status == "not_observed"
    assert observation.determination == "no_sensor"
    assert observation.measurements == {}


def test_observe_unavailable_melody_is_no_sensor_with_reason() -> None:
    observation = _observe_unavailable(
        _anchor("melody", "melody", "melody.json", "note_events_json")
    )
    assert observation.sensor.available is False
    assert observation.sensor.name == "note_events"
    assert "basic-pitch" in str(observation.sensor.reason)
    assert observation.determination == "no_sensor"


def test_observe_unavailable_unmapped_domain_falls_back_to_generic_reason() -> None:
    observation = _observe_unavailable(_anchor("beat", "rhythm", "beat.json", "section_map"))
    assert observation.sensor.name == "rhythm_sensor"
    assert observation.sensor.available is False
    assert "rhythm" in str(observation.sensor.reason)
    assert observation.determination == "no_sensor"
    assert observation.adherence_status == "not_observed"


def test_observe_anchor_dispatches_non_harmony_domains_to_unavailable() -> None:
    observation = _observe_anchor(
        _anchor("structure", "structure", "structure.json", "section_map"),
        manifest_dir=Path("."),
        work_id="w",
        bundle=_bundle_with_chords([]),
    )
    assert observation.determination == "no_sensor"


# --- 3. harmony sensor: D-1's 3-way branch on real chord_sequence_match_rate -------


def test_observe_harmony_exact_match_is_preserved() -> None:
    anchor = _anchor("harmony", "harmony", "chord_progression.json", "chord_sequence_json")
    bundle = _bundle_with_chords(CANONICAL_PROGRESSION)

    observation = _observe_harmony(
        anchor, manifest_dir=FIXTURE_DIR / "identity", work_id="midnight-signal", bundle=bundle
    )

    assert observation.sensor.available is True
    assert observation.sensor.name == "chord_sequence_match"
    assert observation.measurements == {
        "chord_sequence_match_rate": 1.0,
        "repeated_chord_sequence_match_rate": 1.0,
        "canonical_length": 4,
        "observed_length": 4,
        "collapsed_observed_length": 4,
        "matched_cycle_prefix_length": 4,
        "collapsed_match_fraction": 1.0,
        "unmatched_tail_length": 0,
        "unmatched_tail_head": [],
    }
    assert observation.adherence_status == "preserved"
    assert observation.determination == "exact_match"
    assert observation.note is None


def test_observe_harmony_mismatch_is_deferred_with_raw_measurements() -> None:
    anchor = _anchor("harmony", "harmony", "chord_progression.json", "chord_sequence_json")
    mismatched = [("A", "minor"), ("D", "minor"), ("E", "minor"), ("A", "minor")]
    bundle = _bundle_with_chords(mismatched)

    observation = _observe_harmony(
        anchor, manifest_dir=FIXTURE_DIR / "identity", work_id="midnight-signal", bundle=bundle
    )

    assert observation.sensor.available is True
    assert observation.measurements["chord_sequence_match_rate"] == 0.0
    assert observation.measurements["repeated_chord_sequence_match_rate"] == 0.0
    # None of the canonical progression's cycle even gets a first-chord match
    # (canonical starts on C minor, observed starts on A minor), so the
    # cycle-alignment prefix is 0 and the entire (already-collapsed, no
    # adjacent duplicates here) 4-entry sequence is an unmatched tail.
    assert observation.measurements["canonical_length"] == 4
    assert observation.measurements["observed_length"] == 4
    assert observation.measurements["collapsed_observed_length"] == 4
    assert observation.measurements["matched_cycle_prefix_length"] == 0
    assert observation.measurements["collapsed_match_fraction"] == 0.0
    assert observation.measurements["unmatched_tail_length"] == 4
    assert observation.measurements["unmatched_tail_head"] == [
        ["A", "minor"],
        ["D", "minor"],
        ["E", "minor"],
        ["A", "minor"],
    ]
    # D-1: not a threshold judgment — status stays not_observed, only the
    # determination records that classification is deferred.
    assert observation.adherence_status == "not_observed"
    assert observation.determination == "deferred"
    assert "matches 0 full canonical cycle(s); 4 trailing entries" in str(observation.note)
    assert "deferred to a future threshold Design Memo" in str(observation.note)


# --- 4. build_observation_report: single shared extraction + determinism ----------


def _minimal_manifest() -> IdentityManifest:
    return IdentityManifest.model_validate(
        {
            "schema_version": "identity-manifest/0.1",
            "meta": {"work_id": "midnight-signal", "version": "0.1"},
            "source": {
                "locator": "composition_score.yaml",
                "sha256": hashlib.sha256(BASE_SCORE.read_bytes()).hexdigest(),
                "rights_basis": "original",
            },
            "anchors": [
                {
                    "id": "lyrics",
                    "domain": "lyrics",
                    "artifact": "identity/lyrics.txt",
                    "artifact_type": "lyrics_text",
                    "media_type": "text/plain",
                    "sha256": hashlib.sha256(
                        (FIXTURE_DIR / "identity" / "lyrics.txt").read_bytes()
                    ).hexdigest(),
                    "required": True,
                },
                {
                    "id": "melody",
                    "domain": "melody",
                    "artifact": "identity/melody_notes.json",
                    "artifact_type": "note_events_json",
                    "media_type": "application/json",
                    "format_version": "note-events/0.1",
                    "sha256": hashlib.sha256(
                        (FIXTURE_DIR / "identity" / "melody_notes.json").read_bytes()
                    ).hexdigest(),
                    "required": True,
                },
                {
                    "id": "harmony",
                    "domain": "harmony",
                    "artifact": "identity/chord_progression.json",
                    "artifact_type": "chord_sequence_json",
                    "media_type": "application/json",
                    "format_version": "chord-sequence/0.1",
                    "sha256": hashlib.sha256(
                        (FIXTURE_DIR / "identity" / "chord_progression.json").read_bytes()
                    ).hexdigest(),
                    "required": True,
                },
            ],
        }
    )


def _fake_package() -> PerformancePackage:
    return PerformancePackage.model_validate_json(
        (FIXTURE_DIR / "expected" / "e2e_edm" / "performance_package.json").read_bytes()
    )


def test_build_observation_report_shares_a_single_extraction_across_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = {"n": 0}

    def fake_extract(path: str) -> RPEBundle:
        call_count["n"] += 1
        return _bundle_with_chords(CANONICAL_PROGRESSION)

    monkeypatch.setattr("svp_rpe.arrange.observe.extract_rpe_from_file", fake_extract)

    report = build_observation_report(
        package=_fake_package(),
        manifest=_minimal_manifest(),
        manifest_path=IDENTITY_MANIFEST,
        audio_path=Path("unused.wav"),
        package_sha256="a" * 64,
        audio_sha256="b" * 64,
        generated_artifact_path="unused.wav",
    )

    assert call_count["n"] == 1
    assert [anchor.anchor_id for anchor in report.anchors] == ["lyrics", "melody", "harmony"]
    harmony = next(a for a in report.anchors if a.anchor_id == "harmony")
    assert harmony.adherence_status == "preserved"


def test_build_observation_report_is_byte_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_extract(path: str) -> RPEBundle:
        return _bundle_with_chords(CANONICAL_PROGRESSION)

    monkeypatch.setattr("svp_rpe.arrange.observe.extract_rpe_from_file", fake_extract)

    kwargs: dict[str, Any] = {
        "package": _fake_package(),
        "manifest": _minimal_manifest(),
        "manifest_path": IDENTITY_MANIFEST,
        "audio_path": Path("unused.wav"),
        "package_sha256": "a" * 64,
        "audio_sha256": "b" * 64,
        "generated_artifact_path": "unused.wav",
    }
    first = build_observation_report(**kwargs)
    second = build_observation_report(**kwargs)

    assert json.dumps(first.model_dump(mode="json"), sort_keys=True) == json.dumps(
        second.model_dump(mode="json"), sort_keys=True
    )


# --- 5. CLI D-3 provenance chain: negative paths (no audio needed — fail first) ----


def _cli_package_args(output_dir: Path) -> list[str]:
    return [
        "package",
        str(BASE_SCORE),
        str(IDENTITY_MANIFEST),
        str(EDM_IDENTITY_SPEC),
        "--capability-profile",
        str(SUNO_PROFILE_PATH),
        "--output-dir",
        str(output_dir),
    ]


def test_observe_cli_rejects_manifest_sha_mismatch(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir))
    assert result.exit_code == 0, result.output

    # Byte-different manifest (a trailing comment) still has a valid schema but
    # a different sha256 than the one recorded in package.inputs.identity_manifest.
    mutated_manifest = tmp_path / "identity_manifest.yaml"
    mutated_manifest.write_bytes(
        IDENTITY_MANIFEST.read_bytes() + b"\n# tampered for test\n"
    )

    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(pkg_dir / "performance_package.json"),
            str(tmp_path / "does-not-exist.wav"),
            "--manifest",
            str(mutated_manifest),
            "-o",
            str(tmp_path / "report.json"),
        ],
    )

    assert result2.exit_code == 1
    assert "sha256 does not match" in result2.stderr
    assert not (tmp_path / "report.json").exists()


def test_observe_cli_rejects_tampered_anchor_artifact(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir))
    assert result.exit_code == 0, result.output

    # Byte-identical manifest copy (same sha256 as the one the package was
    # compiled with) but a corrupted anchor artifact alongside it.
    scratch_dir = tmp_path / "scratch"
    (scratch_dir / "identity").mkdir(parents=True)
    scratch_manifest = scratch_dir / "identity_manifest.yaml"
    scratch_manifest.write_bytes(IDENTITY_MANIFEST.read_bytes())
    (scratch_dir / "composition_score.yaml").write_bytes(BASE_SCORE.read_bytes())
    (scratch_dir / "identity" / "lyrics.txt").write_bytes(
        (FIXTURE_DIR / "identity" / "lyrics.txt").read_bytes()
    )
    (scratch_dir / "identity" / "melody_notes.json").write_bytes(
        (FIXTURE_DIR / "identity" / "melody_notes.json").read_bytes()
    )
    (scratch_dir / "identity" / "chord_progression.json").write_bytes(b'{"schema": "tampered"}')

    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(pkg_dir / "performance_package.json"),
            str(tmp_path / "does-not-exist.wav"),
            "--manifest",
            str(scratch_manifest),
            "-o",
            str(tmp_path / "report.json"),
        ],
    )

    assert result2.exit_code == 1
    assert "sha256 mismatch" in result2.stderr
    assert "harmony" in result2.stderr
    assert not (tmp_path / "report.json").exists()


def test_observe_cli_rejects_malformed_package_json(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir))
    assert result.exit_code == 0, result.output

    package_data = json.loads((pkg_dir / "performance_package.json").read_text())
    del package_data["anchor_statuses"]  # required field -> schema validation failure
    corrupted_package = tmp_path / "corrupted_package.json"
    corrupted_package.write_text(json.dumps(package_data), encoding="utf-8")

    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(corrupted_package),
            str(tmp_path / "does-not-exist.wav"),
            "--manifest",
            str(IDENTITY_MANIFEST),
            "-o",
            str(tmp_path / "report.json"),
        ],
    )

    assert result2.exit_code == 1
    assert "schema validation" in result2.stderr
    assert not (tmp_path / "report.json").exists()


def test_observe_cli_rejects_missing_package_file(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "observe",
            str(tmp_path / "no-such-package.json"),
            str(tmp_path / "no-such-audio.wav"),
            "--manifest",
            str(IDENTITY_MANIFEST),
            "-o",
            str(tmp_path / "report.json"),
        ],
    )
    assert result.exit_code == 1
    assert not (tmp_path / "report.json").exists()


# --- 6. E2E (real perform + extract): pinned harmony measurement + determinism -----


@pytest.mark.slow
def test_observe_cli_e2e_harmony_measurement_is_pinned_and_deterministic(
    tmp_path: Path,
) -> None:
    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir))
    assert result.exit_code == 0, result.output

    score = CompositionScore.model_validate(
        yaml.safe_load(DERIVED_SCORE_PATH.read_text(encoding="utf-8"))
    )
    wav_path = tmp_path / "take.wav"
    wav_path.write_bytes(wav_bytes(perform(score, FAITHFUL_TAKE)))

    def _invoke_observe(report_path: Path) -> Any:
        return CliRunner().invoke(
            app,
            [
                "observe",
                str(pkg_dir / "performance_package.json"),
                str(wav_path),
                "--manifest",
                str(IDENTITY_MANIFEST),
                "-o",
                str(report_path),
            ],
        )

    report_path_1 = tmp_path / "report1.json"
    result2 = _invoke_observe(report_path_1)
    assert result2.exit_code == 0, result2.output

    report = json.loads(report_path_1.read_text(encoding="utf-8"))
    anchors = {anchor["anchor_id"]: anchor for anchor in report["anchors"]}

    lyrics = anchors["lyrics"]
    assert lyrics["sensor"]["available"] is False
    assert lyrics["determination"] == "no_sensor"

    melody = anchors["melody"]
    assert melody["sensor"]["available"] is False
    assert melody["determination"] == "no_sensor"

    harmony = anchors["harmony"]
    assert harmony["sensor"]["available"] is True
    # Pinned after confirming a fresh-process x2 match (verify_impl/
    # ar4_fixture_hash_compare.txt): the deterministic FAITHFUL_TAKE performance
    # of expected/edm/derived_score.yaml against the canonical
    # identity/chord_progression.json progression. The raw frame-level
    # chord_sequence_match_rate (0.08) and repeated_chord_sequence_match_rate
    # (0.0, cycles=1) are both structurally low-signal for a repeated
    # progression — retained for transparency but not the D-1 identity gate.
    # The collapsed cycle-alignment prefix tells the real story: the verse +
    # chorus sections (2 non-drone sections) recover the canonical progression
    # for 2 full cycles (7 of the 10 collapsed entries) before a 3-entry tail
    # (from the drone-only intro/bridge sections) diverges.
    assert harmony["measurements"] == {
        "chord_sequence_match_rate": 0.08,
        "repeated_chord_sequence_match_rate": 0.0,
        "canonical_length": 4,
        "observed_length": 25,
        "collapsed_observed_length": 10,
        "matched_cycle_prefix_length": 7,
        "collapsed_match_fraction": 0.7,
        "unmatched_tail_length": 3,
        "unmatched_tail_head": [["C", "major"], ["C", "minor"], ["C", "major"]],
    }
    assert harmony["adherence_status"] == "not_observed"
    assert harmony["determination"] == "deferred"
    assert "matches 2 full canonical cycle(s); 3 trailing entries" in harmony["note"]

    # Determinism: same inputs, a second observe invocation -> byte-identical report.
    report_path_2 = tmp_path / "report2.json"
    result3 = _invoke_observe(report_path_2)
    assert result3.exit_code == 0, result3.output
    assert report_path_1.read_bytes() == report_path_2.read_bytes()
