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
import os
import tempfile
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
    _cycle_alignment,
    _load_section_map,
    _normalize_canonical_section_label,
    _normalize_observed_section_label,
    _observe_anchor,
    _observe_harmony,
    _observe_structure,
    _observe_unavailable,
    build_observation_report,
    is_structure_sensor_anchor,
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


def _chord_artifact_bytes(chords: list[tuple[str, str]], *, schema: str = "chord-sequence/0.1") -> bytes:
    payload = {
        "schema": schema,
        "chords": [{"root": root, "quality": quality} for root, quality in chords],
    }
    return json.dumps(payload).encode("utf-8")


def _bundle_with_structure(labels: list[str]) -> RPEBundle:
    """Synthetic RPEBundle carrying only the `physical.structure` sequence
    needed by `_observe_structure` (`chord_events` left empty — the structure
    sensor never reads them, mirroring `_bundle_with_chords`'s narrow-fixture
    style for the harmony sensor)."""
    markers = [
        SectionMarker(label=label, start_sec=float(index), end_sec=float(index + 1))
        for index, label in enumerate(labels)
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
        structure=markers,
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
        chord_events=[],
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


def _section_map_artifact_bytes(
    sections: list[str], *, schema_version: str = "section-map/0.1"
) -> bytes:
    payload = {"schema_version": schema_version, "sections": sections}
    return json.dumps(payload).encode("utf-8")


def _anchor(
    anchor_id: str,
    domain: str,
    artifact: str,
    artifact_type: str,
    *,
    format_version: str | None = None,
) -> IdentityAnchor:
    return IdentityAnchor(
        id=anchor_id,
        domain=domain,
        artifact=artifact,
        artifact_type=artifact_type,
        media_type="application/json",
        format_version=format_version,
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


def test_anchor_observation_rejects_status_outside_0_1_vocabulary() -> None:
    """PR #187 review round 12: observation-report/0.1 only ever emits
    `preserved` / `not_observed`. `changed_outside_policy` is part of
    package.py's wider 5-value `AdherenceStatus` (a future
    observation-report/0.2 vocabulary, once a threshold Design Memo defines
    it) and must be rejected on read-back, not silently accepted from a
    tampered or hand-authored sidecar."""
    with pytest.raises(ValidationError):
        AnchorObservation.model_validate(
            {
                "anchor_id": "a",
                "domain": "harmony",
                "sensor": {"name": "x", "available": True},
                "measurements": {},
                "adherence_status": "changed_outside_policy",
                "determination": "deferred",
            }
        )


def test_anchor_observation_rejects_preserved_paired_with_deferred() -> None:
    """The round-12 pairing validator: `adherence_status` and
    `determination` must agree on which D-1 branch produced this
    observation — `preserved` requires `exact_match`."""
    with pytest.raises(ValidationError):
        AnchorObservation.model_validate(
            {
                "anchor_id": "a",
                "domain": "harmony",
                "sensor": {"name": "x", "available": True},
                "measurements": {},
                "adherence_status": "preserved",
                "determination": "deferred",
            }
        )


def test_anchor_observation_rejects_not_observed_paired_with_exact_match() -> None:
    """Contrast case: `not_observed` requires `determination` in
    `("no_sensor", "deferred")` — pairing it with `exact_match` is rejected
    the same way."""
    with pytest.raises(ValidationError):
        AnchorObservation.model_validate(
            {
                "anchor_id": "a",
                "domain": "harmony",
                "sensor": {"name": "x", "available": True},
                "measurements": {},
                "adherence_status": "not_observed",
                "determination": "exact_match",
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


def test_observation_report_rejects_missing_schema_version() -> None:
    """PR #187 review round 14: `schema_version` has no pydantic default (the
    same shape as `IdentityManifest.schema_version`), so a hand-authored or
    tampered sidecar missing that field must fail on read-back, not be
    silently treated as the current `observation-report/0.1` version."""
    with pytest.raises(ValidationError):
        ObservationReport.model_validate(
            {
                "work_id": "w",
                "package_sha256": "0" * 64,
                "generated_artifact": {"path": "a.wav", "sha256": "0" * 64},
                "anchors": [],
            }
        )


def test_observation_report_rejects_duplicate_anchor_ids() -> None:
    """PR #187 review round 14: the same anchor_id-uniqueness safety net
    `IdentityManifest._validate_unique_anchor_ids` has, applied to
    `ObservationReport.anchors` — including on read-back (`model_validate`),
    not just at construction time."""
    duplicated_anchor = {
        "anchor_id": "harmony",
        "domain": "harmony",
        "sensor": {"name": "chord_sequence_match", "available": True},
        "measurements": {},
        "adherence_status": "not_observed",
        "determination": "no_sensor",
    }
    with pytest.raises(ValidationError):
        ObservationReport.model_validate(
            {
                "schema_version": OBSERVATION_REPORT_SCHEMA_VERSION,
                "work_id": "w",
                "package_sha256": "0" * 64,
                "generated_artifact": {"path": "a.wav", "sha256": "0" * 64},
                "anchors": [duplicated_anchor, duplicated_anchor],
            }
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


def test_observe_anchor_dispatches_unwired_domains_to_unavailable() -> None:
    """`structure` (with a `section_map` artifact_type) is no longer routed
    here — it has its own wired sensor now (`_observe_structure`). `rhythm`
    has no sensor at all regardless of artifact_type, so it still falls
    through `_observe_anchor` to the generic `_observe_unavailable` path."""
    observation = _observe_anchor(
        _anchor("beat", "rhythm", "beat.json", "section_map"),
        manifest_dir=Path("."),
        work_id="w",
        bundle=_bundle_with_chords([]),
        artifact_bytes_by_id={},
    )
    assert observation.determination == "no_sensor"


def test_observe_unavailable_harmony_domain_with_non_chord_artifact_type() -> None:
    """PR #187 review round 5: the harmony sensor is wired to the
    (domain, artifact_type) pair `("harmony", "chord_sequence_json")`
    specifically, not the "harmony" domain alone. A harmony anchor with a
    different, schema-legal artifact_type (e.g. `audio_excerpt`) gets a
    reason naming that artifact_type, not the generic lyrics/melody-style
    message."""
    observation = _observe_unavailable(
        _anchor("harmony_excerpt", "harmony", "excerpt.wav", "audio_excerpt")
    )
    assert observation.sensor.available is False
    assert observation.sensor.name == "harmony_sensor"
    assert "audio_excerpt" in str(observation.sensor.reason)
    assert observation.determination == "no_sensor"
    assert observation.adherence_status == "not_observed"


def test_observe_anchor_dispatches_harmony_domain_with_non_chord_artifact_type_to_unavailable() -> (
    None
):
    observation = _observe_anchor(
        _anchor("harmony_excerpt", "harmony", "excerpt.wav", "audio_excerpt"),
        manifest_dir=Path("."),
        work_id="w",
        bundle=_bundle_with_chords([]),
        artifact_bytes_by_id={},
    )
    assert observation.determination == "no_sensor"
    assert observation.sensor.available is False


def test_build_observation_report_isolates_non_chord_harmony_anchor_from_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manifest with a harmony anchor of the wrong artifact_type alongside
    the normal lyrics/melody/harmony(chord_sequence_json) anchors must not
    disrupt observation of the others: the run succeeds, the odd anchor is
    no_sensor, and the real harmony anchor is measured exactly as before."""

    def fake_extract(path: str) -> RPEBundle:
        return _bundle_with_chords(CANONICAL_PROGRESSION)

    monkeypatch.setattr("svp_rpe.arrange.observe.extract_rpe_from_file", fake_extract)

    manifest_data = _minimal_manifest().model_dump(mode="json")
    manifest_data["anchors"].append(
        {
            "id": "harmony_excerpt",
            "domain": "harmony",
            # Content is never read for this anchor (routing diverts it to
            # _observe_unavailable before any artifact bytes are touched), so
            # reusing an existing fixture file's bytes/sha256 pair is fine.
            "artifact": "identity/lyrics.txt",
            "artifact_type": "audio_excerpt",
            "media_type": "audio/wav",
            "sha256": hashlib.sha256(
                (FIXTURE_DIR / "identity" / "lyrics.txt").read_bytes()
            ).hexdigest(),
            "required": True,
        }
    )
    manifest = IdentityManifest.model_validate(manifest_data)

    report = build_observation_report(
        package=_fake_package(),
        manifest=manifest,
        manifest_path=IDENTITY_MANIFEST,
        artifact_bytes={"harmony": _chord_artifact_bytes(CANONICAL_PROGRESSION)},
        audio_path=Path("unused.wav"),
        package_sha256="a" * 64,
        audio_sha256="b" * 64,
        generated_artifact_path="unused.wav",
    )

    anchors = {observation.anchor_id: observation for observation in report.anchors}
    assert anchors["harmony_excerpt"].determination == "no_sensor"
    assert anchors["harmony_excerpt"].sensor.available is False
    assert "audio_excerpt" in str(anchors["harmony_excerpt"].sensor.reason)
    assert anchors["harmony"].adherence_status == "preserved"
    assert anchors["harmony"].determination == "exact_match"
    assert anchors["lyrics"].determination == "no_sensor"
    assert anchors["melody"].determination == "no_sensor"


# --- 3. harmony sensor: D-1's 3-way branch on real chord_sequence_match_rate -------


def test_observe_harmony_exact_match_is_preserved() -> None:
    anchor = _anchor("harmony", "harmony", "chord_progression.json", "chord_sequence_json")
    bundle = _bundle_with_chords(CANONICAL_PROGRESSION)
    artifact_bytes = _chord_artifact_bytes(CANONICAL_PROGRESSION)

    observation = _observe_harmony(
        anchor,
        manifest_dir=FIXTURE_DIR / "identity",
        work_id="midnight-signal",
        bundle=bundle,
        artifact_bytes=artifact_bytes,
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
        "full_cycles": 1,
        "collapsed_match_fraction": 1.0,
        "unmatched_tail_length": 0,
        "unmatched_tail_head": [],
    }
    assert observation.adherence_status == "preserved"
    assert observation.determination == "exact_match"
    # PR #187 review round 9: preserved's note states the fact the identity
    # gate relied on (full_cycles), self-descriptive from the sidecar alone.
    assert observation.note == (
        "collapsed observed sequence matches the canonical alternation "
        "exactly (1 full cycle(s))."
    )


def test_observe_harmony_mismatch_is_deferred_with_raw_measurements() -> None:
    anchor = _anchor("harmony", "harmony", "chord_progression.json", "chord_sequence_json")
    mismatched = [("A", "minor"), ("D", "minor"), ("E", "minor"), ("A", "minor")]
    bundle = _bundle_with_chords(mismatched)
    artifact_bytes = _chord_artifact_bytes(CANONICAL_PROGRESSION)

    observation = _observe_harmony(
        anchor,
        manifest_dir=FIXTURE_DIR / "identity",
        work_id="midnight-signal",
        bundle=bundle,
        artifact_bytes=artifact_bytes,
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
    assert observation.measurements["full_cycles"] == 0
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


def test_cycle_alignment_proper_prefix_below_one_cycle_is_not_a_full_cycle() -> None:
    """Unit-level check of the round-3 fix's core arithmetic: a collapsed
    observed sequence that matches only a *proper prefix* of the canonical
    progression (here: just its first chord) is a full prefix match
    (`matched_prefix_length == collapsed_observed_length`) but `full_cycles`
    stays 0 — the signal `_observe_harmony` uses to withhold `preserved`."""
    matched_prefix_length, full_cycles = _cycle_alignment(
        CANONICAL_PROGRESSION, [CANONICAL_PROGRESSION[0]]
    )
    assert matched_prefix_length == 1
    assert full_cycles == 0


def test_observe_harmony_proper_prefix_below_one_cycle_stays_deferred() -> None:
    """A drone/truncated output whose collapsed chord_events sequence is a
    single chord — a proper prefix of the canonical progression, matched
    exactly — must NOT be `preserved`: it hasn't observed one full canonical
    cycle, so D-1's identity gate (round 3: `full_cycles >= 1`) keeps it
    `deferred` even though the (short) prefix matches perfectly."""
    anchor = _anchor("harmony", "harmony", "chord_progression.json", "chord_sequence_json")
    bundle = _bundle_with_chords([CANONICAL_PROGRESSION[0]])
    artifact_bytes = _chord_artifact_bytes(CANONICAL_PROGRESSION)

    observation = _observe_harmony(
        anchor,
        manifest_dir=FIXTURE_DIR / "identity",
        work_id="midnight-signal",
        bundle=bundle,
        artifact_bytes=artifact_bytes,
    )

    assert observation.measurements["collapsed_observed_length"] == 1
    assert observation.measurements["matched_cycle_prefix_length"] == 1
    assert observation.adherence_status == "not_observed"
    assert observation.determination == "deferred"
    assert "less than one full canonical cycle" in str(observation.note)


def test_cycle_alignment_counts_trailing_internal_duplicate_as_full_cycle() -> None:
    """Round-4 fix: canonical=[C, G, G] has an internal duplicate (the two
    G's) right after the position where the 2-entry observed sequence
    [C, G] stops. Collapse semantics fold that trailing duplicate into the
    same "G" entry already matched, so this genuinely is one full canonical
    cycle observed — not the kind of incomplete proper-prefix the round-3
    gate (`full_cycles >= 1`) is meant to catch. Before the round-4 fix,
    `matched_raw_index` stopped at the first raw position of that "G" entry
    (2) instead of consuming the trailing duplicate too (3), so
    `full_cycles` undercounted as 0 instead of 1."""
    canonical = [("C", "major"), ("G", "major"), ("G", "major")]
    matched_prefix_length, full_cycles = _cycle_alignment(
        canonical, [("C", "major"), ("G", "major")]
    )
    assert matched_prefix_length == 2
    assert full_cycles == 1


def test_cycle_alignment_short_prefix_without_trailing_duplicate_stays_below_one_cycle() -> None:
    """Contrast case (round-3 gate must still hold): canonical=[C, G] has no
    internal duplicate, so matching only its first chord genuinely is less
    than one full cycle."""
    canonical = [("C", "major"), ("G", "major")]
    matched_prefix_length, full_cycles = _cycle_alignment(canonical, [("C", "major")])
    assert matched_prefix_length == 1
    assert full_cycles == 0


def test_observe_harmony_trailing_internal_duplicate_reaches_preserved() -> None:
    """End-to-end confirmation through `_observe_harmony`: with the round-4
    fix, a canonical progression with a trailing internal duplicate whose
    observed collapsed sequence covers exactly one full (collapse-aware)
    cycle reaches `preserved` — not `deferred`, which the round-3 gate would
    have wrongly forced without this fix (full_cycles undercounted at 0)."""
    anchor = _anchor("harmony", "harmony", "chord_progression.json", "chord_sequence_json")
    canonical = [("C", "major"), ("G", "major"), ("G", "major")]
    bundle = _bundle_with_chords([("C", "major"), ("G", "major")])
    artifact_bytes = _chord_artifact_bytes(canonical)

    observation = _observe_harmony(
        anchor,
        manifest_dir=FIXTURE_DIR / "identity",
        work_id="midnight-signal",
        bundle=bundle,
        artifact_bytes=artifact_bytes,
    )

    assert observation.measurements["collapsed_observed_length"] == 2
    assert observation.measurements["matched_cycle_prefix_length"] == 2
    assert observation.measurements["full_cycles"] == 1
    assert observation.adherence_status == "preserved"
    assert observation.determination == "exact_match"
    assert observation.note == (
        "collapsed observed sequence matches the canonical alternation "
        "exactly (1 full cycle(s))."
    )


def test_cycle_alignment_degenerate_canonical_does_not_hang_and_diverging_chord_is_tail() -> (
    None
):
    """PR #187 review round 8 (real hang bug): canonical=[C, C] collapses to a
    single repeated chord — there is no *other* chord canonical ever
    produces. Before the round-8 fix, the in-loop search for "the next
    distinct canonical entry" had no bound and spun forever once the observed
    sequence diverged (here: a second entry, G, that can never match
    anything canonical produces). Bounded to one cycle's worth of steps
    (`length`), the search now correctly gives up and treats the diverging
    chord as the start of the unmatched tail instead of hanging."""
    canonical = [("C", "major"), ("C", "major")]
    matched_prefix_length, full_cycles = _cycle_alignment(
        canonical, [("C", "major"), ("G", "major")]
    )
    # Pinned against the actual (fixed) implementation's behavior: the first
    # "C" matches, then the search for a distinct entry to compare "G"
    # against exhausts one full cycle without ever finding one, so matching
    # stops there — "G" never gets compared and becomes the unmatched tail.
    assert matched_prefix_length == 1
    assert full_cycles == 1


def test_cycle_alignment_degenerate_canonical_single_chord_prefix_is_one_cycle() -> None:
    """Companion case: canonical=[C, C], observed=[C] alone. The round-4
    trailing-duplicate skip (bounded the same way) consumes canonical's
    second "C" after the match, so the single observed chord already counts
    as one full (collapse-aware) cycle — pinned against the actual
    implementation's behavior, per the round-8 review request."""
    canonical = [("C", "major"), ("C", "major")]
    matched_prefix_length, full_cycles = _cycle_alignment(canonical, [("C", "major")])
    assert matched_prefix_length == 1
    assert full_cycles == 1


def test_cycle_alignment_single_chord_canonical_exact_match_is_one_cycle_not_two() -> None:
    """PR #187 review round 10 (real over-count bug): canonical=[C] (length
    1) matched by a single observed [C] must be exactly 1 full cycle, not 2.
    Before the round-10 fix, the round-4 trailing sweep's bound
    (`trailing_steps < length`, i.e. `< 1`) still let it take one step past
    an already-complete cycle — because with `length == 1` every raw
    canonical position trivially equals `last_chord` (there's only one chord
    `canonical` can ever produce), the sweep couldn't tell "duplicate within
    the same cycle" from "first position of the next cycle" and always
    advanced once, inflating `matched_raw_index` from 1 to 2 and
    `full_cycles` from 1 to 2."""
    canonical = [("C", "major")]
    matched_prefix_length, full_cycles = _cycle_alignment(canonical, [("C", "major")])
    assert matched_prefix_length == 1
    assert full_cycles == 1


def test_cycle_alignment_single_chord_canonical_diverging_chord_is_tail() -> None:
    """Companion case: canonical=[C], observed=[C, G]. The bounded in-loop
    search (round 8) can't find anything but "C" past the first match (there
    is nothing else `canonical=[C]` could ever produce), so "G" is never
    compared — it starts the unmatched tail — while the single matched "C"
    still correctly counts as one full cycle (round 10)."""
    canonical = [("C", "major")]
    matched_prefix_length, full_cycles = _cycle_alignment(
        canonical, [("C", "major"), ("G", "major")]
    )
    assert matched_prefix_length == 1
    assert full_cycles == 1


def test_observe_harmony_degenerate_canonical_diverging_chord_stays_deferred() -> None:
    """End-to-end confirmation through `_observe_harmony`: the round-8 fix
    must not change the *outcome* for this case, only stop it from hanging —
    a diverging second observed chord against a single-chord-collapsed
    canonical still isn't a full prefix match, so this stays `deferred`."""
    anchor = _anchor("harmony", "harmony", "chord_progression.json", "chord_sequence_json")
    canonical = [("C", "major"), ("C", "major")]
    bundle = _bundle_with_chords([("C", "major"), ("G", "major")])
    artifact_bytes = _chord_artifact_bytes(canonical)

    observation = _observe_harmony(
        anchor,
        manifest_dir=FIXTURE_DIR / "identity",
        work_id="midnight-signal",
        bundle=bundle,
        artifact_bytes=artifact_bytes,
    )

    assert observation.measurements["collapsed_observed_length"] == 2
    assert observation.measurements["matched_cycle_prefix_length"] == 1
    assert observation.adherence_status == "not_observed"
    assert observation.determination == "deferred"


def test_observe_harmony_degenerate_canonical_single_chord_reaches_preserved() -> None:
    """Companion end-to-end case: observed collapses to exactly canonical's
    one distinct chord — a full prefix match with `full_cycles == 1` via the
    trailing-duplicate skip, so this reaches `preserved`."""
    anchor = _anchor("harmony", "harmony", "chord_progression.json", "chord_sequence_json")
    canonical = [("C", "major"), ("C", "major")]
    bundle = _bundle_with_chords([("C", "major")])
    artifact_bytes = _chord_artifact_bytes(canonical)

    observation = _observe_harmony(
        anchor,
        manifest_dir=FIXTURE_DIR / "identity",
        work_id="midnight-signal",
        bundle=bundle,
        artifact_bytes=artifact_bytes,
    )

    assert observation.measurements["collapsed_observed_length"] == 1
    assert observation.measurements["matched_cycle_prefix_length"] == 1
    assert observation.measurements["full_cycles"] == 1
    assert observation.adherence_status == "preserved"
    assert observation.determination == "exact_match"
    assert observation.note == (
        "collapsed observed sequence matches the canonical alternation "
        "exactly (1 full cycle(s))."
    )


# --- 3b. structure sensor: D-1's 3-way branch on section-sequence position match --


def test_normalize_canonical_section_label_lowercases_only() -> None:
    """Unit-level check of the canonical-side normalization rule (structure
    Design Memo section 3, revised Codex 3R P2): lowercase only, never strip
    trailing digits. Author-written identifiers (including a hand-authored
    numbering convention like `chorus1`/`chorus2`) are kept verbatim so a
    reordering of them is never masked by normalization."""
    assert _normalize_canonical_section_label("Verse2") == "verse2"
    assert _normalize_canonical_section_label("CHORUS") == "chorus"
    assert _normalize_canonical_section_label("bridge") == "bridge"
    assert _normalize_canonical_section_label("Chorus1") == "chorus1"
    assert _normalize_canonical_section_label("Chorus2") == "chorus2"
    assert _normalize_canonical_section_label("section_01") == "section_01"
    assert _normalize_canonical_section_label("SECTION_01") == "section_01"


def test_normalize_observed_section_label_strips_verse_stem_only() -> None:
    """Unit-level check of the observed-side normalization rule (structure
    Design Memo section 3, revised Codex 3R P2): lowercase always; trailing
    digits are stripped only when the stripped stem is exactly `verse` — the
    single word `assign_labels` (rpe/structure_labels.py) ever auto-numbers.
    Chorus/Bridge/Intro/Outro are never emitted with a numeric suffix by the
    extractor, so their trailing digits (if any, e.g. from a synthetic
    fixture) are left untouched rather than stripped."""
    assert _normalize_observed_section_label("Verse2") == "verse"
    assert _normalize_observed_section_label("verse3") == "verse"
    assert _normalize_observed_section_label("CHORUS") == "chorus"
    assert _normalize_observed_section_label("bridge") == "bridge"
    # Chorus/Bridge/Intro/Outro stems are excluded from stripping even though
    # they were in the pre-Codex-3R vocabulary: the extractor never emits
    # them numbered, so a numeric suffix on these stems keeps its digits.
    assert _normalize_observed_section_label("chorus3") == "chorus3"
    assert _normalize_observed_section_label("outro2") == "outro2"
    assert _normalize_observed_section_label("bridge2") == "bridge2"
    # Stems outside the extractor's vocabulary entirely keep their trailing
    # digits too, so distinct identifier-style labels never collapse.
    assert _normalize_observed_section_label("interlude2") == "interlude2"
    assert _normalize_observed_section_label("section_01") == "section_01"
    assert _normalize_observed_section_label("section_02") == "section_02"
    assert _normalize_observed_section_label("SECTION_01") == "section_01"


def test_observe_structure_exact_match_is_preserved() -> None:
    canonical = ["intro", "verse", "chorus", "outro"]
    anchor = _anchor("structure", "structure", "section_map.json", "section_map")
    bundle = _bundle_with_structure(canonical)
    artifact_bytes = _section_map_artifact_bytes(canonical)

    observation = _observe_structure(
        anchor,
        manifest_dir=FIXTURE_DIR / "identity",
        work_id="midnight-signal",
        bundle=bundle,
        artifact_bytes=artifact_bytes,
    )

    assert observation.sensor.available is True
    assert observation.sensor.name == "section_sequence_match"
    assert observation.measurements == {
        "canonical_sections": canonical,
        "canonical_length": 4,
        "observed_sections": canonical,
        "observed_sections_raw": canonical,
        "observed_length": 4,
        "position_match_rate": 1.0,
        "sequence_exact_match": True,
    }
    assert observation.adherence_status == "preserved"
    assert observation.determination == "exact_match"
    assert observation.note == (
        "normalized canonical and observed section sequences match exactly."
    )


def test_observe_structure_normalization_makes_raw_mismatch_an_exact_match() -> None:
    """`Intro`/`intro` casing and the extractor's `verse1`/`verse` trailing-
    digit convention are both absorbed by normalization — the raw sequences
    differ, but the normalized ones match exactly, so this still reaches
    `preserved`."""
    anchor = _anchor("structure", "structure", "section_map.json", "section_map")
    canonical_raw = ["Intro", "VERSE", "Chorus"]
    observed_raw = ["intro", "verse1", "chorus"]
    bundle = _bundle_with_structure(observed_raw)
    artifact_bytes = _section_map_artifact_bytes(canonical_raw)

    observation = _observe_structure(
        anchor,
        manifest_dir=FIXTURE_DIR / "identity",
        work_id="midnight-signal",
        bundle=bundle,
        artifact_bytes=artifact_bytes,
    )

    assert observation.measurements["canonical_sections"] == ["intro", "verse", "chorus"]
    assert observation.measurements["observed_sections"] == ["intro", "verse", "chorus"]
    assert observation.measurements["observed_sections_raw"] == observed_raw
    assert observation.measurements["sequence_exact_match"] is True
    assert observation.adherence_status == "preserved"
    assert observation.determination == "exact_match"


def test_observe_structure_canonical_chorus_numbering_stays_distinct() -> None:
    """Regression for Codex 3R P2
    (https://github.com/Yuu6798/ugh-prompt-engine/pull/192#discussion_r3610383648
    follow-up): a hand-authored canonical using its own `chorus1`/`chorus2`
    repeat-numbering convention must never be collapsed by normalization —
    the canonical side only lowercases, it never strips trailing digits. A
    reordering of `chorus1`/`chorus2` (which the pre-Codex-3R five-word-stem
    strip would have silently absorbed into a false `sequence_exact_match`)
    must therefore surface as a real mismatch."""
    canonical = ["intro", "chorus1", "chorus2", "outro"]
    observed = ["intro", "chorus2", "chorus1", "outro"]
    anchor = _anchor("structure", "structure", "section_map.json", "section_map")
    bundle = _bundle_with_structure(observed)
    artifact_bytes = _section_map_artifact_bytes(canonical)

    observation = _observe_structure(
        anchor,
        manifest_dir=FIXTURE_DIR / "identity",
        work_id="midnight-signal",
        bundle=bundle,
        artifact_bytes=artifact_bytes,
    )

    assert observation.measurements["canonical_sections"] == canonical
    assert observation.measurements["observed_sections"] == observed
    assert observation.measurements["sequence_exact_match"] is False
    assert observation.adherence_status == "not_observed"
    assert observation.determination == "deferred"


def test_observe_structure_canonical_verse2_is_not_collapsed_to_verse() -> None:
    """The canonical side never strips trailing digits, so an author-written
    `verse2` identifier stays `verse2` after normalization and does not match
    an observed (extractor) `verse` label — canonical-side authors who want
    to express a second verse occurrence use enumeration (`["verse",
    "chorus", "verse", "chorus"]`), not extractor-style numbering; a literal
    `verse2` in the canonical is treated as a distinct identifier, not as
    "the second verse"."""
    canonical = ["intro", "verse2", "outro"]
    observed = ["intro", "verse", "outro"]
    anchor = _anchor("structure", "structure", "section_map.json", "section_map")
    bundle = _bundle_with_structure(observed)
    artifact_bytes = _section_map_artifact_bytes(canonical)

    observation = _observe_structure(
        anchor,
        manifest_dir=FIXTURE_DIR / "identity",
        work_id="midnight-signal",
        bundle=bundle,
        artifact_bytes=artifact_bytes,
    )

    assert observation.measurements["canonical_sections"] == ["intro", "verse2", "outro"]
    assert observation.measurements["observed_sections"] == ["intro", "verse", "outro"]
    assert observation.measurements["sequence_exact_match"] is False
    assert observation.adherence_status == "not_observed"
    assert observation.determination == "deferred"


def test_observe_structure_observed_chorus_digit_is_not_stripped() -> None:
    """If a `Chorus2`-style label ever appears on the observed side (the real
    extractor never emits it numbered, but this guards the normalization
    contract directly rather than relying on that invariant), the trailing
    digit must NOT be stripped — only the `verse` stem is. A canonical
    `chorus` therefore does not match an observed `Chorus2`."""
    canonical = ["intro", "chorus", "outro"]
    observed = ["Intro", "Chorus2", "Outro"]
    anchor = _anchor("structure", "structure", "section_map.json", "section_map")
    bundle = _bundle_with_structure(observed)
    artifact_bytes = _section_map_artifact_bytes(canonical)

    observation = _observe_structure(
        anchor,
        manifest_dir=FIXTURE_DIR / "identity",
        work_id="midnight-signal",
        bundle=bundle,
        artifact_bytes=artifact_bytes,
    )

    assert observation.measurements["canonical_sections"] == canonical
    assert observation.measurements["observed_sections"] == ["intro", "chorus2", "outro"]
    assert observation.measurements["sequence_exact_match"] is False
    assert observation.adherence_status == "not_observed"
    assert observation.determination == "deferred"


def test_observe_structure_numbered_identifier_labels_stay_distinct() -> None:
    """Regression for Codex 2R P2
    (https://github.com/Yuu6798/ugh-prompt-engine/pull/192#discussion_r3610383648):
    generic identifier-style labels like `section_01`/`section_02` are outside
    the extractor's known vocabulary, so normalization must NOT strip their
    trailing digits. A reordered `section_01`/`section_02` sequence must
    therefore stay a mismatch, not a false-positive `sequence_exact_match`."""
    canonical = ["section_01", "section_02", "section_03"]
    observed = ["section_02", "section_01", "section_03"]
    anchor = _anchor("structure", "structure", "section_map.json", "section_map")
    bundle = _bundle_with_structure(observed)
    artifact_bytes = _section_map_artifact_bytes(canonical)

    observation = _observe_structure(
        anchor,
        manifest_dir=FIXTURE_DIR / "identity",
        work_id="midnight-signal",
        bundle=bundle,
        artifact_bytes=artifact_bytes,
    )

    assert observation.measurements["canonical_sections"] == canonical
    assert observation.measurements["observed_sections"] == observed
    assert observation.measurements["sequence_exact_match"] is False
    assert observation.measurements["position_match_rate"] == 0.3333
    assert observation.adherence_status == "not_observed"
    assert observation.determination == "deferred"


def test_observe_structure_reordered_sections_is_deferred_with_match_rate() -> None:
    canonical = ["intro", "verse", "chorus", "outro"]
    observed = ["intro", "chorus", "verse", "outro"]
    anchor = _anchor("structure", "structure", "section_map.json", "section_map")
    bundle = _bundle_with_structure(observed)
    artifact_bytes = _section_map_artifact_bytes(canonical)

    observation = _observe_structure(
        anchor,
        manifest_dir=FIXTURE_DIR / "identity",
        work_id="midnight-signal",
        bundle=bundle,
        artifact_bytes=artifact_bytes,
    )

    assert observation.measurements["position_match_rate"] == 0.5
    assert observation.measurements["sequence_exact_match"] is False
    assert observation.adherence_status == "not_observed"
    assert observation.determination == "deferred"
    assert "deferred to a future threshold Design Memo" in str(observation.note)


def test_observe_structure_missing_trailing_sections_is_deferred_with_match_rate() -> None:
    """Observed sequence shorter than canonical (e.g. a truncated take that
    never reaches the tail sections): position_match_rate divides by the
    longer (canonical) length, and the missing positions never contribute a
    match."""
    canonical = ["intro", "verse", "chorus", "outro"]
    observed = ["intro", "verse"]
    anchor = _anchor("structure", "structure", "section_map.json", "section_map")
    bundle = _bundle_with_structure(observed)
    artifact_bytes = _section_map_artifact_bytes(canonical)

    observation = _observe_structure(
        anchor,
        manifest_dir=FIXTURE_DIR / "identity",
        work_id="midnight-signal",
        bundle=bundle,
        artifact_bytes=artifact_bytes,
    )

    assert observation.measurements["canonical_length"] == 4
    assert observation.measurements["observed_length"] == 2
    assert observation.measurements["position_match_rate"] == 0.5
    assert observation.measurements["sequence_exact_match"] is False
    assert observation.adherence_status == "not_observed"
    assert observation.determination == "deferred"


def test_observe_structure_extra_observed_sections_is_deferred_with_match_rate() -> None:
    """Observed sequence longer than canonical (e.g. extra sections the
    extractor found beyond what the identity manifest declares):
    position_match_rate still divides by the longer (observed) length."""
    canonical = ["intro", "verse"]
    observed = ["intro", "verse", "chorus", "outro"]
    anchor = _anchor("structure", "structure", "section_map.json", "section_map")
    bundle = _bundle_with_structure(observed)
    artifact_bytes = _section_map_artifact_bytes(canonical)

    observation = _observe_structure(
        anchor,
        manifest_dir=FIXTURE_DIR / "identity",
        work_id="midnight-signal",
        bundle=bundle,
        artifact_bytes=artifact_bytes,
    )

    assert observation.measurements["canonical_length"] == 2
    assert observation.measurements["observed_length"] == 4
    assert observation.measurements["position_match_rate"] == 0.5
    assert observation.measurements["sequence_exact_match"] is False
    assert observation.adherence_status == "not_observed"
    assert observation.determination == "deferred"


def test_load_section_map_rejects_unknown_schema_version() -> None:
    artifact_bytes = _section_map_artifact_bytes(["intro"], schema_version="section-map/0.2")
    with pytest.raises(ValueError, match="section-map/0.2"):
        _load_section_map(artifact_bytes, artifact_path=Path("section_map.json"))


def test_load_section_map_rejects_empty_sections() -> None:
    artifact_bytes = json.dumps(
        {"schema_version": "section-map/0.1", "sections": []}
    ).encode("utf-8")
    with pytest.raises(ValueError):
        _load_section_map(artifact_bytes, artifact_path=Path("section_map.json"))


def test_load_section_map_rejects_unknown_key() -> None:
    artifact_bytes = json.dumps(
        {"schema_version": "section-map/0.1", "sections": ["intro"], "extra": "no"}
    ).encode("utf-8")
    with pytest.raises(ValueError):
        _load_section_map(artifact_bytes, artifact_path=Path("section_map.json"))


def test_load_section_map_rejects_missing_schema_version() -> None:
    artifact_bytes = json.dumps({"sections": ["intro"]}).encode("utf-8")
    with pytest.raises(ValueError):
        _load_section_map(artifact_bytes, artifact_path=Path("section_map.json"))


def test_observe_anchor_dispatches_structure_domain_with_non_section_map_artifact_type_to_unavailable() -> (
    None
):
    """Same shape as harmony's `("harmony", "audio_excerpt")` case: a
    structure anchor with a schema-legal but unwired artifact_type (e.g.
    `audio_excerpt`) is not routed to `_observe_structure` and falls back to
    the generic no_sensor path unchanged (structure Design Memo section 2 —
    routes on the (domain, artifact_type) pair, not the domain alone)."""
    anchor = _anchor("structure_excerpt", "structure", "excerpt.wav", "audio_excerpt")
    assert is_structure_sensor_anchor(anchor) is False

    observation = _observe_anchor(
        anchor,
        manifest_dir=Path("."),
        work_id="w",
        bundle=_bundle_with_structure(["intro"]),
        artifact_bytes_by_id={},
    )
    assert observation.determination == "no_sensor"
    assert observation.sensor.available is False


def test_is_structure_sensor_anchor_requires_section_map_0_1_format_version() -> None:
    """PR #192 review round 1: `artifact_type == "section_map"` alone isn't
    enough to route to `_observe_structure` — the anchor must also declare
    the exact `format_version` its JSON parser understands
    (`section-map/0.1`). `format_version=None` is the common case (the
    schema doesn't require declaring one at all — precedent:
    `tests/test_identity_manifest.py`'s YAML `section_map` anchor), and
    `format_version="section-map/1"` is a distinct declared format
    (precedent: `tests/test_performance_package.py`). Neither should match."""
    unversioned = _anchor("structure", "structure", "structure.yaml", "section_map")
    assert unversioned.format_version is None
    assert is_structure_sensor_anchor(unversioned) is False

    other_format = _anchor(
        "structure",
        "structure",
        "section_tags.json",
        "section_map",
        format_version="section-map/1",
    )
    assert is_structure_sensor_anchor(other_format) is False

    wired = _anchor(
        "structure",
        "structure",
        "section_map.json",
        "section_map",
        format_version="section-map/0.1",
    )
    assert is_structure_sensor_anchor(wired) is True


def test_observe_anchor_falls_back_to_no_sensor_for_yaml_section_map_precedent() -> None:
    """Regression guard for PR #192 review round 1: a `section_map` anchor
    shaped like `tests/test_identity_manifest.py`'s (YAML `media_type`, no
    declared `format_version`) must keep getting the `no_sensor` fallback it
    always had — not be force-fed into `_observe_structure`'s JSON parser and
    abort the whole `observe` run."""
    anchor = _anchor("structure", "structure", "structure.yaml", "section_map")
    observation = _observe_anchor(
        anchor,
        manifest_dir=Path("."),
        work_id="w",
        bundle=_bundle_with_structure(["intro"]),
        artifact_bytes_by_id={},
    )
    assert observation.determination == "no_sensor"
    assert observation.sensor.available is False
    assert observation.sensor.name == "structure_sensor"
    assert "section-map/0.1" in str(observation.sensor.reason)
    assert "None" in str(observation.sensor.reason)


def test_observe_anchor_falls_back_to_no_sensor_for_other_format_version_section_map() -> None:
    """Regression guard for PR #192 review round 1: repo precedent
    (`tests/test_performance_package.py`) declares `section_map` anchors with
    `format_version="section-map/1"` — a different, non-JSON-guaranteed
    format this sensor doesn't understand — which must also keep the
    `no_sensor` fallback rather than being routed to the section-map/0.1
    parser."""
    anchor = _anchor(
        "structure",
        "structure",
        "section_tags.json",
        "section_map",
        format_version="section-map/1",
    )
    observation = _observe_anchor(
        anchor,
        manifest_dir=Path("."),
        work_id="w",
        bundle=_bundle_with_structure(["intro"]),
        artifact_bytes_by_id={},
    )
    assert observation.determination == "no_sensor"
    assert observation.sensor.available is False
    assert "section-map/1" in str(observation.sensor.reason)


def test_observe_anchor_wired_section_map_0_1_with_invalid_content_still_fails_closed() -> None:
    """The format_version gate only decides *routing* — an anchor that does
    declare `section-map/0.1` but carries schema-invalid bytes still fails
    closed inside `_observe_structure` / `_load_section_map`, exactly like
    harmony's `chord-sequence/0.1` contract (same fail-closed boundary as
    `test_load_section_map_rejects_unknown_schema_version`, exercised here at
    the `_observe_anchor` dispatch level instead of calling `_load_section_map`
    directly)."""
    anchor = _anchor(
        "structure",
        "structure",
        "section_map.json",
        "section_map",
        format_version="section-map/0.1",
    )
    assert is_structure_sensor_anchor(anchor) is True
    bad_bytes = json.dumps(
        {"schema_version": "section-map/0.2", "sections": ["intro"]}
    ).encode("utf-8")

    with pytest.raises(ValueError, match="section-map/0.2"):
        _observe_anchor(
            anchor,
            manifest_dir=Path("."),
            work_id="w",
            bundle=_bundle_with_structure(["intro"]),
            artifact_bytes_by_id={"structure": bad_bytes},
        )


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
        artifact_bytes={"harmony": _chord_artifact_bytes(CANONICAL_PROGRESSION)},
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
        "artifact_bytes": {"harmony": _chord_artifact_bytes(CANONICAL_PROGRESSION)},
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


def test_build_observation_report_skips_extraction_when_no_wired_sensor_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR #187 review round 12's extraction gate: if no manifest anchor
    matches a wired sensor's (domain, artifact_type) predicate — here, a
    manifest with only lyrics/melody anchors and no harmony anchor at all —
    `extract_rpe_from_file` must never be called. The generated audio's
    content is irrelevant to a report that's entirely `no_sensor`; only its
    already-computed sha256 (passed in separately) is recorded."""

    def fail_extract(path: str) -> RPEBundle:
        raise AssertionError(
            "extract_rpe_from_file must not be called when no anchor needs a wired sensor"
        )

    monkeypatch.setattr("svp_rpe.arrange.observe.extract_rpe_from_file", fail_extract)

    manifest_data = _minimal_manifest().model_dump(mode="json")
    manifest_data["anchors"] = [
        anchor_data
        for anchor_data in manifest_data["anchors"]
        if anchor_data["domain"] != "harmony"
    ]
    manifest = IdentityManifest.model_validate(manifest_data)

    report = build_observation_report(
        package=_fake_package(),
        manifest=manifest,
        manifest_path=IDENTITY_MANIFEST,
        artifact_bytes={},
        audio_path=Path("unused.wav"),
        package_sha256="a" * 64,
        audio_sha256="b" * 64,
        generated_artifact_path="unused.wav",
    )

    assert [anchor.anchor_id for anchor in report.anchors] == ["lyrics", "melody"]
    assert all(anchor.determination == "no_sensor" for anchor in report.anchors)
    assert all(anchor.adherence_status == "not_observed" for anchor in report.anchors)


# --- 5. CLI D-3 provenance chain: negative paths (no audio needed — fail first) ----


def _cli_package_args(output_dir: Path, *, identity_yaml: Path = IDENTITY_MANIFEST) -> list[str]:
    return [
        "package",
        str(BASE_SCORE),
        str(identity_yaml),
        str(EDM_IDENTITY_SPEC),
        "--capability-profile",
        str(SUNO_PROFILE_PATH),
        "--output-dir",
        str(output_dir),
    ]


def _build_scratch_identity_dir(tmp_path: Path, *, chord_artifact_bytes: bytes) -> Path:
    """Copy the midnight_signal identity fixture set into `tmp_path`, replacing
    the harmony anchor's artifact bytes with `chord_artifact_bytes` and
    updating the manifest's declared sha256 to match — so provenance-chain
    hash verification passes and a test failure is isolated to whatever the
    replaced artifact's content actually triggers (e.g. a schema check),
    rather than a sha256 mismatch."""
    (tmp_path / "identity").mkdir()
    (tmp_path / "composition_score.yaml").write_bytes(BASE_SCORE.read_bytes())
    for name in ("lyrics.txt", "melody_notes.json"):
        (tmp_path / "identity" / name).write_bytes(
            (FIXTURE_DIR / "identity" / name).read_bytes()
        )
    (tmp_path / "identity" / "chord_progression.json").write_bytes(chord_artifact_bytes)

    manifest_data = yaml.safe_load(IDENTITY_MANIFEST.read_text(encoding="utf-8"))
    for anchor_data in manifest_data["anchors"]:
        if anchor_data["id"] == "harmony":
            anchor_data["sha256"] = hashlib.sha256(chord_artifact_bytes).hexdigest()
    manifest_path = tmp_path / "identity_manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(manifest_data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return manifest_path


def _build_scratch_identity_dir_with_structure_anchor(
    tmp_path: Path, *, canonical_sections: list[str]
) -> Path:
    """Copy the midnight_signal identity fixture set into `tmp_path`
    unmodified (lyrics/melody/harmony artifacts and their declared sha256
    values are untouched — same-shaped copy `_build_scratch_identity_dir`
    does), then append a new `structure` anchor whose `section-map/0.1`
    artifact declares `canonical_sections`.

    `examples/arrangement/midnight_signal/` itself is never written to —
    AR2-3 froze that fixture set, so a structure anchor's manifest entry
    only exists in this synthetic tmp_path copy (structure Design Memo
    section 5's slow e2e). The arrangement spec `edm.identity.arrangement.yaml`
    used by `_cli_package_args` doesn't reference a `structure` anchor id in
    its `identity_anchors` preservation policy, so `package` records it as
    `requested_mode=None` / `not_requested` — harmless, since `observe`
    measures independently of delivery/preservation-contract status.
    """
    (tmp_path / "identity").mkdir()
    (tmp_path / "composition_score.yaml").write_bytes(BASE_SCORE.read_bytes())
    for name in ("lyrics.txt", "melody_notes.json", "chord_progression.json"):
        (tmp_path / "identity" / name).write_bytes(
            (FIXTURE_DIR / "identity" / name).read_bytes()
        )
    section_map_bytes = _section_map_artifact_bytes(canonical_sections)
    (tmp_path / "identity" / "section_map.json").write_bytes(section_map_bytes)

    manifest_data = yaml.safe_load(IDENTITY_MANIFEST.read_text(encoding="utf-8"))
    manifest_data["anchors"].append(
        {
            "id": "structure",
            "domain": "structure",
            "artifact": "identity/section_map.json",
            "artifact_type": "section_map",
            "media_type": "application/json",
            "format_version": "section-map/0.1",
            "sha256": hashlib.sha256(section_map_bytes).hexdigest(),
            # `required=False`: `edm.identity.arrangement.yaml` (the fixture
            # arrangement spec `_cli_package_args` compiles against, and
            # AR2-3-frozen — not editable to add a `structure` preservation
            # policy entry) only declares identity_anchors for
            # lyrics/melody/harmony. `build_preservation_contract` demands a
            # policy for every `required=True` anchor
            # (`arrange/contract.py`'s "required anchor(s) have no
            # preservation policy" check), so a required structure anchor
            # would fail `package` before `observe` is ever reached.
            # `required=False` makes it legitimately unrequested
            # (`requested_mode=None` / `not_requested`), which `observe`
            # doesn't care about — the sensor measures every manifest anchor
            # regardless of its preservation-contract status.
            "required": False,
        }
    )
    manifest_path = tmp_path / "identity_manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(manifest_data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return manifest_path


def _package_with_patched_manifest_sha256(pkg_dir: Path, manifest_sha256: str) -> Path:
    """Build a normal, validly-compiled package, then hand-patch its recorded
    `inputs.identity_manifest.sha256` to `manifest_sha256`.

    Used to make the D-3 sha256 check pass against a manifest file that could
    never have been validly compiled through `package` in the first place
    (because its content is deliberately malformed/schema-invalid) — this
    isolates the failure under test to the manifest *parse* step, not the
    earlier sha256-mismatch check."""
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir))
    assert result.exit_code == 0, result.output
    package_data = json.loads((pkg_dir / "performance_package.json").read_text())
    package_data["inputs"]["identity_manifest"]["sha256"] = manifest_sha256
    patched_path = pkg_dir / "performance_package_patched.json"
    patched_path.write_text(json.dumps(package_data), encoding="utf-8")
    return patched_path


def test_observe_cli_rejects_malformed_yaml_manifest_with_matching_sha(tmp_path: Path) -> None:
    """PR #187 review round 6: a manifest whose bytes hash-match the
    package's recorded sha256 (so D-3's sha256 check passes) but whose
    content is invalid YAML must fail cleanly — "Error: ..." on stderr and
    exit 1, not an uncaught `yaml.YAMLError` traceback."""
    pkg_dir = tmp_path / "pkg"
    malformed_manifest = tmp_path / "malformed.yaml"
    malformed_manifest.write_bytes(b"schema_version: [unclosed\nmeta: {work_id: x\n")
    manifest_sha256 = hashlib.sha256(malformed_manifest.read_bytes()).hexdigest()
    patched_package = _package_with_patched_manifest_sha256(pkg_dir, manifest_sha256)

    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")
    report_path = tmp_path / "report.json"

    result = CliRunner().invoke(
        app,
        [
            "observe",
            str(patched_package),
            str(audio_path),
            "--manifest",
            str(malformed_manifest),
            "-o",
            str(report_path),
        ],
    )

    assert result.exit_code == 1
    assert result.stderr.startswith("Error:")
    assert "Traceback" not in result.stderr
    assert not report_path.exists()


def test_observe_cli_rejects_schema_invalid_manifest_with_matching_sha(tmp_path: Path) -> None:
    """Same shape as the malformed-YAML case, but the content is valid YAML
    — a mapping — that's missing required IdentityManifest fields (`meta`,
    `source`, `anchors`). This is a pydantic `ValidationError`, not a
    `yaml.YAMLError`; both must be caught the same way."""
    pkg_dir = tmp_path / "pkg"
    schema_invalid_manifest = tmp_path / "schema_invalid.yaml"
    schema_invalid_manifest.write_bytes(b"schema_version: identity-manifest/0.1\n")
    manifest_sha256 = hashlib.sha256(schema_invalid_manifest.read_bytes()).hexdigest()
    patched_package = _package_with_patched_manifest_sha256(pkg_dir, manifest_sha256)

    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")
    report_path = tmp_path / "report.json"

    result = CliRunner().invoke(
        app,
        [
            "observe",
            str(patched_package),
            str(audio_path),
            "--manifest",
            str(schema_invalid_manifest),
            "-o",
            str(report_path),
        ],
    )

    assert result.exit_code == 1
    assert result.stderr.startswith("Error:")
    assert "Traceback" not in result.stderr
    assert not report_path.exists()


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


def test_observe_cli_rejects_work_id_mismatch(tmp_path: Path) -> None:
    """PR #187 review round 10: the sha256 chain check only proves the
    manifest *bytes* the package recorded and the `--manifest` file agree —
    it doesn't independently prove they describe the same work. Patch the
    package's own `work_id` (leaving `inputs.identity_manifest.sha256`
    correct, so the earlier sha256 check still passes) to simulate that
    mismatch; `observe` must reject it before measuring anything."""
    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir))
    assert result.exit_code == 0, result.output

    package_data = json.loads((pkg_dir / "performance_package.json").read_text())
    original_work_id = package_data["work_id"]
    package_data["work_id"] = "some-other-work"
    patched_package = pkg_dir / "performance_package_patched.json"
    patched_package.write_text(json.dumps(package_data), encoding="utf-8")

    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")
    report_path = tmp_path / "report.json"

    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(patched_package),
            str(audio_path),
            "--manifest",
            str(IDENTITY_MANIFEST),
            "-o",
            str(report_path),
        ],
    )

    assert result2.exit_code == 1
    assert "work_id" in result2.stderr
    assert "some-other-work" in result2.stderr
    assert original_work_id in result2.stderr
    assert not report_path.exists()


def test_observe_cli_rejects_package_anchor_set_missing_an_anchor(tmp_path: Path) -> None:
    """PR #187 review round 15: sha256 and work_id matching don't rule out a
    package whose `anchor_statuses` anchor_id set no longer matches the
    manifest's anchors. Here, one status entry ("melody") is deleted from an
    otherwise validly-compiled package (sha256/work_id both still correct);
    `observe` must reject the mismatch before measuring anything."""
    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir))
    assert result.exit_code == 0, result.output

    package_data = json.loads((pkg_dir / "performance_package.json").read_text())
    package_data["anchor_statuses"] = [
        status for status in package_data["anchor_statuses"] if status["anchor_id"] != "melody"
    ]
    patched_package = pkg_dir / "performance_package_patched.json"
    patched_package.write_text(json.dumps(package_data), encoding="utf-8")

    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")
    report_path = tmp_path / "report.json"

    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(patched_package),
            str(audio_path),
            "--manifest",
            str(IDENTITY_MANIFEST),
            "-o",
            str(report_path),
        ],
    )

    assert result2.exit_code == 1
    assert "anchor_id set does not match" in result2.stderr
    assert "missing from package.anchor_statuses" in result2.stderr
    assert "melody" in result2.stderr
    assert not report_path.exists()


def test_observe_cli_rejects_package_anchor_set_with_an_extra_anchor(tmp_path: Path) -> None:
    """Contrast case: an extra `anchor_statuses` entry the manifest has no
    corresponding anchor for must be rejected the same way."""
    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir))
    assert result.exit_code == 0, result.output

    package_data = json.loads((pkg_dir / "performance_package.json").read_text())
    package_data["anchor_statuses"].append(
        {
            "anchor_id": "extra-anchor",
            "requested_mode": None,
            "allow": [],
            "delivery": {"channel": None, "status": "not_requested"},
            "control": {"status": "unknown"},
            "observation": {"status": "not_observed"},
        }
    )
    patched_package = pkg_dir / "performance_package_patched.json"
    patched_package.write_text(json.dumps(package_data), encoding="utf-8")

    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")
    report_path = tmp_path / "report.json"

    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(patched_package),
            str(audio_path),
            "--manifest",
            str(IDENTITY_MANIFEST),
            "-o",
            str(report_path),
        ],
    )

    assert result2.exit_code == 1
    assert "anchor_id set does not match" in result2.stderr
    assert "extra in package.anchor_statuses" in result2.stderr
    assert "extra-anchor" in result2.stderr
    assert not report_path.exists()


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


# --- 5b. PR #187 review: -o input/output collision guard --------------------------


def test_observe_cli_rejects_output_path_colliding_with_manifest(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir))
    assert result.exit_code == 0, result.output

    # A tmp_path copy (with its source/anchor artifacts alongside it, so
    # parse_identity_manifest's own hash checks succeed) — never the real
    # fixture path, so a regression here can never actually clobber a
    # repo-tracked file even if the guard is broken.
    (tmp_path / "identity").mkdir()
    manifest_copy = tmp_path / "identity_manifest.yaml"
    manifest_copy.write_bytes(IDENTITY_MANIFEST.read_bytes())
    (tmp_path / "composition_score.yaml").write_bytes(BASE_SCORE.read_bytes())
    for name in ("lyrics.txt", "melody_notes.json", "chord_progression.json"):
        (tmp_path / "identity" / name).write_bytes(
            (FIXTURE_DIR / "identity" / name).read_bytes()
        )
    original_bytes = manifest_copy.read_bytes()
    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")

    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(pkg_dir / "performance_package.json"),
            str(audio_path),
            "--manifest",
            str(manifest_copy),
            "-o",
            str(manifest_copy),
        ],
    )

    assert result2.exit_code == 1
    assert "collides with output artifact path" in result2.stderr
    assert manifest_copy.read_bytes() == original_bytes


def test_observe_cli_rejects_output_path_colliding_with_package(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir))
    assert result.exit_code == 0, result.output
    package_path = pkg_dir / "performance_package.json"
    original_bytes = package_path.read_bytes()
    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")

    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(package_path),
            str(audio_path),
            "--manifest",
            str(IDENTITY_MANIFEST),
            "-o",
            str(package_path),
        ],
    )

    assert result2.exit_code == 1
    assert "collides with output artifact path" in result2.stderr
    assert package_path.read_bytes() == original_bytes


def test_observe_cli_rejects_output_path_hard_linked_to_manifest(tmp_path: Path) -> None:
    """PR #187 review round 3: resolved-path equality alone misses a hard
    link — two distinct path names that are the same inode, neither a symlink
    to the other, so `Path.resolve()` leaves them unequal. `-o` set to a hard
    link of the manifest must still be rejected, and the manifest content
    (reachable via either name — they're the same file) must stay untouched.
    """
    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir))
    assert result.exit_code == 0, result.output

    (tmp_path / "identity").mkdir()
    manifest_copy = tmp_path / "identity_manifest.yaml"
    manifest_copy.write_bytes(IDENTITY_MANIFEST.read_bytes())
    (tmp_path / "composition_score.yaml").write_bytes(BASE_SCORE.read_bytes())
    for name in ("lyrics.txt", "melody_notes.json", "chord_progression.json"):
        (tmp_path / "identity" / name).write_bytes(
            (FIXTURE_DIR / "identity" / name).read_bytes()
        )
    original_bytes = manifest_copy.read_bytes()

    hard_link_path = tmp_path / "output_via_hardlink.yaml"
    try:
        os.link(manifest_copy, hard_link_path)
    except OSError as exc:
        pytest.skip(f"filesystem does not support hard links here: {exc}")

    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")

    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(pkg_dir / "performance_package.json"),
            str(audio_path),
            "--manifest",
            str(manifest_copy),
            "-o",
            str(hard_link_path),
        ],
    )

    assert result2.exit_code == 1
    assert "collides with output artifact path" in result2.stderr
    assert manifest_copy.read_bytes() == original_bytes
    assert hard_link_path.read_bytes() == original_bytes


# --- 5c. PR #187 review: manifest single-read discipline + audio snapshot ----------


def test_observe_cli_reads_manifest_bytes_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: `observe` must parse the manifest from the same bytes
    it hashed for the D-3 sha256 check, not re-read the file a second time
    (e.g. via `load_identity_manifest`, which re-reads internally)."""
    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir))
    assert result.exit_code == 0, result.output

    def fake_extract(path: str) -> RPEBundle:
        return _bundle_with_chords(CANONICAL_PROGRESSION)

    monkeypatch.setattr("svp_rpe.arrange.observe.extract_rpe_from_file", fake_extract)

    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")

    resolved_manifest = IDENTITY_MANIFEST.resolve()
    read_counts: dict[Path, int] = {}
    original_read_bytes = Path.read_bytes

    def counting_read_bytes(self: Path) -> bytes:
        if self.resolve() == resolved_manifest:
            read_counts[resolved_manifest] = read_counts.get(resolved_manifest, 0) + 1
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    report_path = tmp_path / "report.json"
    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(pkg_dir / "performance_package.json"),
            str(audio_path),
            "--manifest",
            str(IDENTITY_MANIFEST),
            "-o",
            str(report_path),
        ],
    )

    assert result2.exit_code == 0, result2.output
    assert read_counts.get(resolved_manifest) == 1


def test_observe_cli_extracts_from_a_byte_identical_audio_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bytes handed to extraction must be exactly the bytes hashed into
    `generated_artifact.sha256` — verified via a snapshot file, not the
    original `audio_path` (so a report survives even if something else were to
    mutate the original file between hashing and extraction). The snapshot
    must be a temp file distinct from `audio_path`, cleaned up afterward, and
    must never leak into `generated_artifact.path` (which stays the original
    user-supplied string)."""
    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir))
    assert result.exit_code == 0, result.output

    audio_path = tmp_path / "fake.wav"
    audio_bytes = b"unused-placeholder-audio-bytes-for-snapshot-check"
    audio_path.write_bytes(audio_bytes)
    expected_sha256 = hashlib.sha256(audio_bytes).hexdigest()

    captured_paths: list[str] = []

    def fake_extract(path: str) -> RPEBundle:
        captured_paths.append(path)
        assert Path(path).read_bytes() == audio_bytes
        return _bundle_with_chords(CANONICAL_PROGRESSION)

    monkeypatch.setattr("svp_rpe.arrange.observe.extract_rpe_from_file", fake_extract)

    report_path = tmp_path / "report.json"
    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(pkg_dir / "performance_package.json"),
            str(audio_path),
            "--manifest",
            str(IDENTITY_MANIFEST),
            "-o",
            str(report_path),
        ],
    )

    assert result2.exit_code == 0, result2.output
    assert len(captured_paths) == 1
    assert Path(captured_paths[0]) != audio_path
    assert not Path(captured_paths[0]).exists()  # snapshot cleaned up in `finally`

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["generated_artifact"]["path"] == str(audio_path)
    assert report["generated_artifact"]["sha256"] == expected_sha256


def test_observe_cli_skips_snapshot_tempfile_when_no_wired_sensor_anchors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #187 review round 13: a manifest with only a lyrics anchor (no
    harmony/chord_sequence_json anchor at all) must not create the audio
    snapshot tempfile — confirmed here by making `tempfile.mkstemp` fail —
    and must not call `extract_rpe_from_file` either (round 12's gate).
    `observe` must still succeed: nothing needs the audio's content, only its
    already-computed sha256 for provenance.

    Built by hand-constructing a minimal `PerformancePackage` (matching
    work_id + `inputs.identity_manifest.sha256` to this manifest) rather than
    compiling one via `svprpe package`, since the existing arrangement specs
    all request a harmony policy this lyrics-only manifest doesn't declare an
    anchor for."""
    (tmp_path / "identity").mkdir()
    source_bytes = b"source-audio-placeholder"
    (tmp_path / "source.wav").write_bytes(source_bytes)
    lyrics_bytes = (FIXTURE_DIR / "identity" / "lyrics.txt").read_bytes()
    (tmp_path / "identity" / "lyrics.txt").write_bytes(lyrics_bytes)

    manifest_data = {
        "schema_version": "identity-manifest/0.1",
        "meta": {"work_id": "lyrics-only-work", "version": "0.1"},
        "source": {
            "locator": "source.wav",
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "rights_basis": "original",
        },
        "anchors": [
            {
                "id": "lyrics",
                "domain": "lyrics",
                "artifact": "identity/lyrics.txt",
                "artifact_type": "lyrics_text",
                "media_type": "text/plain",
                "sha256": hashlib.sha256(lyrics_bytes).hexdigest(),
                "required": True,
            },
        ],
    }
    manifest_path = tmp_path / "identity_manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(manifest_data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    manifest_bytes = manifest_path.read_bytes()

    package = PerformancePackage(
        work_id="lyrics-only-work",
        generator="suno",
        generator_variant="standard",
        inputs={
            "identity_manifest": {"sha256": hashlib.sha256(manifest_bytes).hexdigest()},
            "preservation_contract": {"sha256": "c" * 64},
            "capability_profile": {"sha256": "d" * 64},
            "derived_score": {"sha256": "e" * 64},
            "device_profile": {"generator": "suno", "status": "not_found"},
        },
        # Round 15's anchor_id set cross-check requires anchor_statuses to
        # cover exactly the manifest's anchors ("lyrics" here) — an
        # unrequested anchor's status shape, matching what
        # build_performance_package itself produces for one.
        anchor_statuses=[
            {
                "anchor_id": "lyrics",
                "requested_mode": None,
                "allow": [],
                "tolerance_profile": None,
                "delivery": {"channel": None, "status": "not_requested"},
                "control": {"status": "unknown"},
                "observation": {"status": "not_observed"},
            }
        ],
    )
    package_path = tmp_path / "performance_package.json"
    package_path.write_bytes(package.model_dump_json(indent=2).encode("utf-8"))

    # `_write_observation_report_atomically` (round 6) also calls
    # `tempfile.mkstemp` — for the *report* JSON, always expected to run.
    # Only the audio-snapshot call (distinguished here by not passing
    # `dir=`, which the report-write call always does) must not happen.
    original_mkstemp = tempfile.mkstemp

    def guarded_mkstemp(*args: Any, **kwargs: Any) -> Any:
        if "dir" not in kwargs:
            raise AssertionError(
                "tempfile.mkstemp must not be called for an audio snapshot "
                "when no anchor needs a wired sensor"
            )
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr("tempfile.mkstemp", guarded_mkstemp)

    def fail_extract(path: str) -> RPEBundle:
        raise AssertionError("extract_rpe_from_file must not be called either")

    monkeypatch.setattr("svp_rpe.arrange.observe.extract_rpe_from_file", fail_extract)

    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")
    report_path = tmp_path / "report.json"

    result = CliRunner().invoke(
        app,
        [
            "observe",
            str(package_path),
            str(audio_path),
            "--manifest",
            str(manifest_path),
            "-o",
            str(report_path),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert [anchor["anchor_id"] for anchor in report["anchors"]] == ["lyrics"]
    assert report["anchors"][0]["determination"] == "no_sensor"
    assert report["anchors"][0]["adherence_status"] == "not_observed"


def test_observe_cli_reports_snapshot_mkstemp_oserror_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #187 review round 17: an `OSError` from the audio-snapshot
    `tempfile.mkstemp` call (disk full, unwritable temp dir, etc.) must be
    reported through the same "Error: ..." + exit 1 handling every other
    failure path in this command uses, not escape as a raw traceback."""
    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir))
    assert result.exit_code == 0, result.output

    # `_write_observation_report_atomically` (round 6) also calls
    # `tempfile.mkstemp` (always with `dir=`) — that call must be unaffected;
    # only the audio-snapshot call (no `dir=`) fails here, and since it fails
    # before any report is built, the report-write path is never reached.
    original_mkstemp = tempfile.mkstemp

    def failing_mkstemp(*args: Any, **kwargs: Any) -> Any:
        if "dir" not in kwargs:
            raise OSError("no space left on device (simulated)")
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr("tempfile.mkstemp", failing_mkstemp)

    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")
    report_path = tmp_path / "report.json"

    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(pkg_dir / "performance_package.json"),
            str(audio_path),
            "--manifest",
            str(IDENTITY_MANIFEST),
            "-o",
            str(report_path),
        ],
    )

    assert result2.exit_code == 1
    assert result2.stderr.startswith("Error:")
    assert "Traceback" not in result2.stderr
    assert not report_path.exists()
    # mkstemp itself never created a file, so there is nothing to leave behind.
    assert list(tmp_path.glob("*.tmp")) == []


def test_observe_cli_reports_snapshot_write_oserror_cleanly_and_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Companion case: `mkstemp` succeeds but the snapshot write itself
    raises `OSError` (e.g. the disk fills mid-write) — same clean
    Error+exit 1 handling, and the partially-written temp file is removed
    (best-effort cleanup in the outer `finally`) rather than left behind."""
    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir))
    assert result.exit_code == 0, result.output

    created_paths: list[str] = []
    original_mkstemp = tempfile.mkstemp

    def capturing_mkstemp(*args: Any, **kwargs: Any) -> Any:
        fd, name = original_mkstemp(*args, **kwargs)
        if "dir" not in kwargs:
            created_paths.append(name)
        return fd, name

    monkeypatch.setattr("tempfile.mkstemp", capturing_mkstemp)

    def failing_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        os.close(fd)
        raise OSError("no space left on device (simulated, mid-write)")

    monkeypatch.setattr("os.fdopen", failing_fdopen)

    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")
    report_path = tmp_path / "report.json"

    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(pkg_dir / "performance_package.json"),
            str(audio_path),
            "--manifest",
            str(IDENTITY_MANIFEST),
            "-o",
            str(report_path),
        ],
    )

    assert result2.exit_code == 1
    assert result2.stderr.startswith("Error:")
    assert "Traceback" not in result2.stderr
    assert not report_path.exists()
    assert len(created_paths) == 1
    assert not Path(created_paths[0]).exists()  # cleaned up in the outer `finally`


# --- 5d. PR #187 review round 2: harmony artifact bytes reuse + schema fail-closed -


def test_observe_harmony_uses_the_provided_artifact_bytes_without_reopening_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard at the sensor level: `_observe_harmony` must use the
    `artifact_bytes` it was given, never re-reading the artifact file itself.
    Reads the real fixture bytes *before* monkeypatching `Path.read_bytes` to
    always raise, so any attempt to reopen anything through `Path.read_bytes`
    afterward fails the test immediately."""
    anchor = _anchor("harmony", "harmony", "chord_progression.json", "chord_sequence_json")
    bundle = _bundle_with_chords(CANONICAL_PROGRESSION)
    artifact_bytes = (FIXTURE_DIR / "identity" / "chord_progression.json").read_bytes()

    def _boom(self: Path) -> bytes:
        raise AssertionError(
            f"_observe_harmony must not re-read the artifact file from disk: {self}"
        )

    monkeypatch.setattr(Path, "read_bytes", _boom)

    observation = _observe_harmony(
        anchor,
        manifest_dir=FIXTURE_DIR / "identity",
        work_id="midnight-signal",
        bundle=bundle,
        artifact_bytes=artifact_bytes,
    )

    assert observation.adherence_status == "preserved"


def test_observe_cli_harmony_measurement_does_not_reread_artifact_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI-level counterpart: the chord_progression.json artifact must be read
    from disk exactly once for the whole `observe` invocation (during the D-3
    anchor-hash verification inside `parse_identity_manifest_with_artifacts`),
    not a second time when the harmony sensor measures against it."""
    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir))
    assert result.exit_code == 0, result.output

    def fake_extract(path: str) -> RPEBundle:
        return _bundle_with_chords(CANONICAL_PROGRESSION)

    monkeypatch.setattr("svp_rpe.arrange.observe.extract_rpe_from_file", fake_extract)

    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")

    resolved_chord_artifact = (FIXTURE_DIR / "identity" / "chord_progression.json").resolve()
    read_counts: dict[Path, int] = {}
    original_read_bytes = Path.read_bytes

    def counting_read_bytes(self: Path) -> bytes:
        if self.resolve() == resolved_chord_artifact:
            read_counts[resolved_chord_artifact] = read_counts.get(resolved_chord_artifact, 0) + 1
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    report_path = tmp_path / "report.json"
    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(pkg_dir / "performance_package.json"),
            str(audio_path),
            "--manifest",
            str(IDENTITY_MANIFEST),
            "-o",
            str(report_path),
        ],
    )

    assert result2.exit_code == 0, result2.output
    assert read_counts.get(resolved_chord_artifact) == 1


def test_observe_cli_rejects_chord_artifact_missing_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chord_bytes = b'{"chords": [{"root": "C", "quality": "minor"}]}'
    manifest_path = _build_scratch_identity_dir(tmp_path, chord_artifact_bytes=chord_bytes)

    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir, identity_yaml=manifest_path))
    assert result.exit_code == 0, result.output

    def fake_extract(path: str) -> RPEBundle:
        return _bundle_with_chords(CANONICAL_PROGRESSION)

    monkeypatch.setattr("svp_rpe.arrange.observe.extract_rpe_from_file", fake_extract)

    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")
    report_path = tmp_path / "report.json"

    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(pkg_dir / "performance_package.json"),
            str(audio_path),
            "--manifest",
            str(manifest_path),
            "-o",
            str(report_path),
        ],
    )

    assert result2.exit_code == 1
    assert "unsupported schema" in result2.stderr
    assert not report_path.exists()


def test_observe_cli_rejects_chord_artifact_unknown_schema_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chord_bytes = (
        b'{"schema": "chord-sequence/0.2", '
        b'"chords": [{"root": "C", "quality": "minor"}]}'
    )
    manifest_path = _build_scratch_identity_dir(tmp_path, chord_artifact_bytes=chord_bytes)

    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir, identity_yaml=manifest_path))
    assert result.exit_code == 0, result.output

    def fake_extract(path: str) -> RPEBundle:
        return _bundle_with_chords(CANONICAL_PROGRESSION)

    monkeypatch.setattr("svp_rpe.arrange.observe.extract_rpe_from_file", fake_extract)

    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")
    report_path = tmp_path / "report.json"

    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(pkg_dir / "performance_package.json"),
            str(audio_path),
            "--manifest",
            str(manifest_path),
            "-o",
            str(report_path),
        ],
    )

    assert result2.exit_code == 1
    assert "chord-sequence/0.2" in result2.stderr
    assert not report_path.exists()


def test_observe_cli_rejects_chord_artifact_with_null_chords(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR #187 review round 7: `chords: null` — valid JSON, correct schema,
    hash-matching artifact — must fail cleanly ("Error: ..." + exit 1), not
    crash with an uncaught `TypeError` from iterating `None`."""
    chord_bytes = b'{"schema": "chord-sequence/0.1", "chords": null}'
    manifest_path = _build_scratch_identity_dir(tmp_path, chord_artifact_bytes=chord_bytes)

    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir, identity_yaml=manifest_path))
    assert result.exit_code == 0, result.output

    def fake_extract(path: str) -> RPEBundle:
        return _bundle_with_chords(CANONICAL_PROGRESSION)

    monkeypatch.setattr("svp_rpe.arrange.observe.extract_rpe_from_file", fake_extract)

    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")
    report_path = tmp_path / "report.json"

    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(pkg_dir / "performance_package.json"),
            str(audio_path),
            "--manifest",
            str(manifest_path),
            "-o",
            str(report_path),
        ],
    )

    assert result2.exit_code == 1
    assert result2.stderr.startswith("Error:")
    assert "'chords' must be a list" in result2.stderr
    assert "Traceback" not in result2.stderr
    assert not report_path.exists()


def test_observe_cli_rejects_chord_artifact_with_string_chords(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same shape as the null case, but `chords` is a string — without the
    isinstance(list) guard, iterating it would silently walk over individual
    characters and fail with a confusing per-character pydantic error instead
    of a direct, on-topic message."""
    chord_bytes = b'{"schema": "chord-sequence/0.1", "chords": "abc"}'
    manifest_path = _build_scratch_identity_dir(tmp_path, chord_artifact_bytes=chord_bytes)

    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir, identity_yaml=manifest_path))
    assert result.exit_code == 0, result.output

    def fake_extract(path: str) -> RPEBundle:
        return _bundle_with_chords(CANONICAL_PROGRESSION)

    monkeypatch.setattr("svp_rpe.arrange.observe.extract_rpe_from_file", fake_extract)

    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")
    report_path = tmp_path / "report.json"

    result2 = CliRunner().invoke(
        app,
        [
            "observe",
            str(pkg_dir / "performance_package.json"),
            str(audio_path),
            "--manifest",
            str(manifest_path),
            "-o",
            str(report_path),
        ],
    )

    assert result2.exit_code == 1
    assert result2.stderr.startswith("Error:")
    assert "'chords' must be a list" in result2.stderr
    assert "Traceback" not in result2.stderr
    assert not report_path.exists()


# --- 5e. PR #187 review round 6: atomic -o publish -----------------------------------


def test_observe_cli_overwrites_an_existing_report_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 6: `-o` is staged + `os.replace`'d rather than written in place.
    Confirm the normal "re-observe into the same path" use case still works
    (an existing report file gets fully replaced, not corrupted/appended),
    and that the result is byte-identical to a fresh run with the same
    inputs written to a brand new path."""
    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir))
    assert result.exit_code == 0, result.output

    def fake_extract(path: str) -> RPEBundle:
        return _bundle_with_chords(CANONICAL_PROGRESSION)

    monkeypatch.setattr("svp_rpe.arrange.observe.extract_rpe_from_file", fake_extract)

    audio_path = tmp_path / "fake.wav"
    audio_path.write_bytes(b"unused-placeholder-audio-bytes")

    report_path = tmp_path / "report.json"
    report_path.write_text("stale placeholder content from a previous run", encoding="utf-8")

    observe_args = [
        "observe",
        str(pkg_dir / "performance_package.json"),
        str(audio_path),
        "--manifest",
        str(IDENTITY_MANIFEST),
        "-o",
        str(report_path),
    ]
    result2 = CliRunner().invoke(app, observe_args)
    assert result2.exit_code == 0, result2.output
    overwritten_content = report_path.read_text(encoding="utf-8")
    assert "stale placeholder" not in overwritten_content
    report = json.loads(overwritten_content)
    assert report["schema_version"] == OBSERVATION_REPORT_SCHEMA_VERSION

    fresh_path = tmp_path / "report_fresh.json"
    result3 = CliRunner().invoke(app, observe_args[:-1] + [str(fresh_path)])
    assert result3.exit_code == 0, result3.output
    assert fresh_path.read_bytes() == report_path.read_bytes()


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
        "full_cycles": 2,
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


@pytest.mark.slow
def test_observe_cli_e2e_structure_measurement_is_pinned_and_deterministic(
    tmp_path: Path,
) -> None:
    """AR2-3 解凍条件 (a) の実証: structure Design Memo section 5. Same shape as
    `test_observe_cli_e2e_harmony_measurement_is_pinned_and_deterministic`
    (real `perform` + real `extract_rpe_from_file`, no monkeypatch), but with
    a synthetic manifest carrying a `structure` anchor —
    `examples/arrangement/midnight_signal/` itself stays untouched (AR2-3
    froze it; only `_build_scratch_identity_dir_with_structure_anchor`'s
    tmp_path copy gains the new anchor)."""
    canonical_sections = ["intro", "chorus", "bridge", "verse", "chorus", "verse", "outro"]
    manifest_path = _build_scratch_identity_dir_with_structure_anchor(
        tmp_path, canonical_sections=canonical_sections
    )

    pkg_dir = tmp_path / "pkg"
    result = CliRunner().invoke(app, _cli_package_args(pkg_dir, identity_yaml=manifest_path))
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
                str(manifest_path),
                "-o",
                str(report_path),
            ],
        )

    report_path_1 = tmp_path / "report1.json"
    result2 = _invoke_observe(report_path_1)
    assert result2.exit_code == 0, result2.output

    report = json.loads(report_path_1.read_text(encoding="utf-8"))
    anchors = {anchor["anchor_id"]: anchor for anchor in report["anchors"]}

    # harmony is still measured too (extraction is shared across anchors) —
    # exact values are already pinned by the harmony e2e test above; here we
    # only confirm co-existence didn't disturb it (still ran, still available).
    harmony = anchors["harmony"]
    assert harmony["sensor"]["available"] is True

    structure = anchors["structure"]
    assert structure["sensor"]["available"] is True
    assert structure["sensor"]["name"] == "section_sequence_match"
    # Pinned real-extraction values (`extract_rpe_from_file` against the
    # deterministic FAITHFUL_TAKE performance of expected/edm/derived_score.yaml
    # — the same audio the harmony e2e test above measures): the extractor's
    # real section labels are `Intro/Chorus/Bridge/Verse/Chorus/Verse2/Outro`
    # (mixed case, with the repeated "Verse" section auto-numbered "Verse2").
    # `canonical_sections` was chosen to equal the *normalized* form of that
    # real observed sequence exactly, so this pin demonstrates the
    # normalization rule (lowercase + strip trailing digits) doing real work
    # against real extractor output: the raw sequences differ (case,
    # "Verse2" vs "verse") but the normalized ones match exactly.
    assert structure["measurements"] == {
        "canonical_sections": canonical_sections,
        "canonical_length": 7,
        "observed_sections": [
            "intro",
            "chorus",
            "bridge",
            "verse",
            "chorus",
            "verse",
            "outro",
        ],
        "observed_sections_raw": [
            "Intro",
            "Chorus",
            "Bridge",
            "Verse",
            "Chorus",
            "Verse2",
            "Outro",
        ],
        "observed_length": 7,
        "position_match_rate": 1.0,
        "sequence_exact_match": True,
    }
    assert structure["adherence_status"] == "preserved"
    assert structure["determination"] == "exact_match"
    assert structure["note"] == (
        "normalized canonical and observed section sequences match exactly."
    )

    # Determinism: same inputs, a second observe invocation -> byte-identical report.
    report_path_2 = tmp_path / "report2.json"
    result3 = _invoke_observe(report_path_2)
    assert result3.exit_code == 0, result3.output
    assert report_path_1.read_bytes() == report_path_2.read_bytes()
