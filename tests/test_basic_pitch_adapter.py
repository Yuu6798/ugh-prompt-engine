"""tests/test_basic_pitch_adapter.py — basic-pitch adapter tests.

The real basic-pitch package is NOT required to run these tests. We
monkeypatch `sys.modules["basic_pitch"]` and
`sys.modules["basic_pitch.inference"]` with a fake backend so the adapter
contract (predict shape, note-event mapping, isolation, validation) can
be pinned without any optional install or model download.

The fake mirrors basic-pitch's `predict(audio_path) -> (model_output,
midi_data, note_events)` shape, where `note_events` is a list of tuples
`(start, end, midi, amplitude, pitch_bends)` per upstream >= 0.2.
"""
from __future__ import annotations

import sys
import tomllib
import types
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from svp_rpe.rpe.models import (
    DeltaEProfile,
    GrvAnchor,
    LearnedAudioAnnotations,
    LearnedNoteEvent,
    PhysicalRPE,
    RPEBundle,
    SectionMarker,
    SemanticLabel,
    SemanticRPE,
    SpectralProfile,
)
from svp_rpe.svp.generator import generate_svp


# ---------------------------------------------------------------------------
# Fake backend installation
# ---------------------------------------------------------------------------


def _install_fake_basic_pitch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    note_events: list[tuple] | None = None,
    version: str | None = None,
    predict_result: Any | None = None,
) -> dict:
    """Install a fake `basic_pitch.inference` module with `predict`.

    `note_events` is the third element of the tuple `predict` returns, so
    most tests just pass that. For corner cases (return shape mismatch,
    custom raises) callers can pass `predict_result` instead — that is
    used as the entire return value.
    """
    captured: dict = {}

    def fake_predict(audio_path, *args, **kwargs):
        captured["audio_path"] = audio_path
        captured["args"] = args
        captured["kwargs"] = dict(kwargs)
        if predict_result is not None:
            return predict_result
        events = list(note_events) if note_events is not None else []
        # basic-pitch tuple shape: (model_output, midi_data, note_events)
        return ({}, None, events)

    fake_inference = types.ModuleType("basic_pitch.inference")
    fake_inference.predict = fake_predict
    fake_root = types.ModuleType("basic_pitch")
    fake_root.inference = fake_inference
    if version is not None:
        fake_root.__version__ = version

    monkeypatch.setitem(sys.modules, "basic_pitch", fake_root)
    monkeypatch.setitem(sys.modules, "basic_pitch.inference", fake_inference)
    return captured


def _force_basic_pitch_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "basic_pitch", None)
    monkeypatch.setitem(sys.modules, "basic_pitch.inference", None)


# ---------------------------------------------------------------------------
# Helpers for building a baseline RPEBundle
# ---------------------------------------------------------------------------


def _make_bundle() -> RPEBundle:
    return RPEBundle(
        physical=PhysicalRPE(
            duration_sec=180.0,
            sample_rate=44100,
            structure=[SectionMarker(label="section_01", start_sec=0.0, end_sec=180.0)],
            rms_mean=0.3,
            peak_amplitude=0.9,
            crest_factor=3.0,
            active_rate=0.85,
            valley_depth=0.2,
            thickness=2.0,
            spectral_centroid=3000.0,
            spectral_profile=SpectralProfile(
                centroid=3000.0,
                low_ratio=0.3,
                mid_ratio=0.5,
                high_ratio=0.2,
                brightness=0.28,
            ),
            onset_density=4.5,
        ),
        semantic=SemanticRPE(
            por_core="bright track",
            por_surface=[
                SemanticLabel(
                    label="bright",
                    layer="perceptual",
                    confidence=0.9,
                    evidence=["brightness=0.28"],
                    source_rule="perc.brightness",
                )
            ],
            grv_anchor=GrvAnchor(primary="bass-heavy"),
            delta_e_profile=DeltaEProfile(
                transition_type="flat",
                intensity=0.3,
                description="steady",
            ),
            cultural_context=["electronic"],
            instrumentation_summary="synths",
            production_notes=["compressed"],
            confidence_notes=["rule"],
        ),
        audio_file="test.wav",
        audio_duration_sec=180.0,
        audio_sample_rate=44100,
        audio_channels=2,
        audio_format="wav",
    )


# ---------------------------------------------------------------------------
# Adapter contract tests
# ---------------------------------------------------------------------------


class TestOptionalDependencyMetadata:
    def test_pitch_extra_is_limited_to_verified_python_runtime(self):
        pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

        assert pyproject["project"]["optional-dependencies"]["pitch"] == [
            "basic-pitch==0.4.0; python_version < '3.12'"
        ]


class TestAdapterUnavailable:
    def test_raises_with_install_hint_when_basic_pitch_missing(self, monkeypatch):
        _force_basic_pitch_unavailable(monkeypatch)

        from svp_rpe.rpe.learned import LearnedModelUnavailable
        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        with pytest.raises(LearnedModelUnavailable, match="basic_pitch"):
            extract_basic_pitch_annotations("nonexistent.wav")


class TestAdapterIncompatible:
    """Pin that API-shape mismatches raise the more specific Incompatible error."""

    def test_raises_incompatible_when_predict_function_missing(self, monkeypatch):
        # Install the inference module without `predict`.
        fake_inference = types.ModuleType("basic_pitch.inference")
        fake_root = types.ModuleType("basic_pitch")
        fake_root.inference = fake_inference
        monkeypatch.setitem(sys.modules, "basic_pitch", fake_root)
        monkeypatch.setitem(sys.modules, "basic_pitch.inference", fake_inference)

        from svp_rpe.rpe.learned import LearnedModelIncompatible
        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        with pytest.raises(LearnedModelIncompatible, match="predict"):
            extract_basic_pitch_annotations("test.wav")

    def test_raises_incompatible_when_predict_returns_too_few_elements(
        self, monkeypatch
    ):
        # Two-tuple instead of three-tuple — adapter must surface this as
        # Incompatible, not silently truncate.
        _install_fake_basic_pitch(
            monkeypatch, predict_result=({}, None)
        )

        from svp_rpe.rpe.learned import LearnedModelIncompatible
        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        with pytest.raises(LearnedModelIncompatible):
            extract_basic_pitch_annotations("test.wav")

    def test_raises_incompatible_when_predict_returns_unexpected_type(
        self, monkeypatch
    ):
        # A dict return — wrong shape entirely.
        _install_fake_basic_pitch(
            monkeypatch, predict_result={"notes": []}
        )

        from svp_rpe.rpe.learned import LearnedModelIncompatible
        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        with pytest.raises(LearnedModelIncompatible, match="unexpected type"):
            extract_basic_pitch_annotations("test.wav")

    def test_raises_incompatible_when_note_events_field_is_not_a_sequence(
        self, monkeypatch
    ):
        # The 3-tuple is the right OUTER shape, but the third element is a
        # string. Without the explicit type check, the adapter would iterate
        # the string char-by-char and surface a per-character "unexpected
        # shape" error, which points at the wrong level of the API contract.
        _install_fake_basic_pitch(
            monkeypatch, predict_result=({}, None, "not a list")
        )

        from svp_rpe.rpe.learned import LearnedModelIncompatible
        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        with pytest.raises(LearnedModelIncompatible, match="note_events"):
            extract_basic_pitch_annotations("test.wav")

    def test_raises_incompatible_when_note_event_too_short(self, monkeypatch):
        # 3-element note event (no amplitude) — adapter requires 4+.
        _install_fake_basic_pitch(
            monkeypatch,
            note_events=[(0.0, 1.0, 60)],  # type: ignore[list-item]
        )

        from svp_rpe.rpe.learned import LearnedModelIncompatible
        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        with pytest.raises(LearnedModelIncompatible, match="unexpected shape"):
            extract_basic_pitch_annotations("test.wav")


class TestErrorClassUnification:
    def test_unified_error_class_with_other_adapters(self):
        from svp_rpe.rpe.learned import LearnedModelUnavailable as Unified
        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            LearnedModelUnavailable as FromBasicPitch,
        )
        from svp_rpe.rpe.learned.beat_this_adapter import (
            LearnedModelUnavailable as FromBeat,
        )
        from svp_rpe.rpe.learned.panns_adapter import (
            LearnedModelUnavailable as FromPanns,
        )

        assert FromBasicPitch is Unified
        assert FromBeat is Unified
        assert FromPanns is Unified


class TestAdapterOutputShape:
    def test_note_events_mapped_correctly(self, monkeypatch):
        # basic-pitch >= 0.2 shape: (start, end, midi, amplitude, pitch_bends).
        _install_fake_basic_pitch(
            monkeypatch,
            note_events=[
                (0.00, 0.50, 60, 0.90, []),   # C4
                (0.25, 0.75, 64, 0.80, [-1]),  # E4 with bend
                (0.50, 1.25, 67, 0.70, []),   # G4
            ],
            version="0.4.0",
        )

        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        result = extract_basic_pitch_annotations("song.wav")

        assert isinstance(result, LearnedAudioAnnotations)
        assert all(isinstance(n, LearnedNoteEvent) for n in result.note_events)
        events_unpacked = [
            (n.start_sec, n.end_sec, n.pitch_midi, n.confidence)
            for n in result.note_events
        ]
        assert events_unpacked == [
            (0.00, 0.50, 60, 0.90),
            (0.25, 0.75, 64, 0.80),
            (0.50, 1.25, 67, 0.70),
        ]
        assert all(n.source_model == "basic_pitch" for n in result.note_events)

    def test_model_name_flows_to_note_source_model(self, monkeypatch):
        _install_fake_basic_pitch(
            monkeypatch,
            note_events=[(0.0, 0.5, 60, 0.9, [])],
        )

        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        result = extract_basic_pitch_annotations(
            "song.wav",
            model_name="basic_pitch:icassp_2022",
        )

        assert result.note_events[0].source_model == "basic_pitch:icassp_2022"
        assert result.inference_config["model_name"] == "basic_pitch:icassp_2022"

    def test_predict_called_with_path_string(self, monkeypatch):
        captured = _install_fake_basic_pitch(
            monkeypatch, note_events=[(0.0, 0.5, 60, 0.9, [])]
        )

        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        # Path object is coerced to str before reaching upstream predict —
        # basic-pitch handles either, but we pin the adapter's choice.
        extract_basic_pitch_annotations(Path("a/b/c.wav"))
        assert captured["audio_path"] == str(Path("a/b/c.wav"))

    def test_records_provenance(self, monkeypatch):
        _install_fake_basic_pitch(
            monkeypatch,
            note_events=[(0.0, 0.5, 60, 0.9, [])],
            version="0.4.0",
        )

        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        result = extract_basic_pitch_annotations("song.wav")

        assert len(result.enabled_models) == 1
        info = result.enabled_models[0]
        assert info.name == "basic_pitch"
        assert info.version == "0.4.0"
        assert info.provider == "spotify/basic-pitch"
        assert info.task == "pitch"
        assert info.license == "Apache-2.0"
        # Model-artifact license intentionally NOT asserted as Apache-2.0
        # — see policy doc.
        assert info.weights_license is None

        assert result.inference_config["model_name"] == "basic_pitch"
        assert result.inference_config["source"] == "basic_pitch"
        assert result.inference_config["entry_point"] == "predict"

        license_text = result.license_metadata["basic_pitch"]
        assert "Apache-2.0" in license_text
        assert "model artifact" in license_text.lower()

    def test_empty_note_events_still_yields_provenance(self, monkeypatch):
        _install_fake_basic_pitch(
            monkeypatch, note_events=[], version="0.4.0"
        )

        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        result = extract_basic_pitch_annotations("silent.wav")

        assert result.note_events == []
        assert len(result.enabled_models) == 1
        assert result.enabled_models[0].version == "0.4.0"

    def test_accepts_list_note_events(self, monkeypatch):
        # Some basic-pitch versions return list-of-list rather than list-of-
        # tuple. Adapter accepts either.
        _install_fake_basic_pitch(
            monkeypatch,
            note_events=[[0.0, 0.5, 60, 0.9, []]],  # type: ignore[list-item]
        )

        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        result = extract_basic_pitch_annotations("song.wav")
        assert len(result.note_events) == 1
        assert result.note_events[0].pitch_midi == 60


class TestAdapterVersionDetection:
    def test_version_from_module_attribute(self, monkeypatch):
        _install_fake_basic_pitch(
            monkeypatch, note_events=[], version="0.4.0"
        )

        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        result = extract_basic_pitch_annotations("test.wav")
        assert result.enabled_models[0].version == "0.4.0"

    def test_version_none_when_unavailable(self, monkeypatch):
        _install_fake_basic_pitch(monkeypatch, note_events=[])

        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        result = extract_basic_pitch_annotations("test.wav")
        assert result.enabled_models[0].version is None
        assert result.enabled_models[0].name == "basic_pitch"


# ---------------------------------------------------------------------------
# LearnedNoteEvent validation tests (pin Pydantic-side guards)
# ---------------------------------------------------------------------------


class TestLearnedNoteEventValidation:
    def _kwargs(self, **overrides: Any) -> dict:
        base = {
            "start_sec": 0.0,
            "end_sec": 1.0,
            "pitch_midi": 60,
            "confidence": 0.5,
            "source_model": "basic_pitch",
        }
        base.update(overrides)
        return base

    def test_negative_start_rejected(self):
        with pytest.raises(ValidationError, match="start_sec"):
            LearnedNoteEvent(**self._kwargs(start_sec=-0.1))

    def test_end_before_start_rejected(self):
        with pytest.raises(ValidationError, match="end_sec"):
            LearnedNoteEvent(**self._kwargs(start_sec=1.0, end_sec=0.5))

    def test_end_equal_to_start_accepted(self):
        # Zero-duration is allowed (end == start). Only end < start fails.
        event = LearnedNoteEvent(**self._kwargs(start_sec=1.0, end_sec=1.0))
        assert event.end_sec == event.start_sec

    @pytest.mark.parametrize("bad_midi", [-1, 128, 200])
    def test_midi_out_of_range_rejected(self, bad_midi):
        with pytest.raises(ValidationError, match="pitch_midi"):
            LearnedNoteEvent(**self._kwargs(pitch_midi=bad_midi))

    @pytest.mark.parametrize("bad_confidence", [-0.01, 1.01, 2.0])
    def test_confidence_out_of_range_rejected(self, bad_confidence):
        with pytest.raises(ValidationError, match="confidence"):
            LearnedNoteEvent(**self._kwargs(confidence=bad_confidence))

    def test_adapter_surfaces_validation_error_on_bad_upstream(self, monkeypatch):
        # Upstream-emitted out-of-range value (defective fake / model bug).
        # Adapter must not silently clamp; ValidationError surfaces.
        _install_fake_basic_pitch(
            monkeypatch,
            note_events=[(0.0, 0.5, 200, 0.9, [])],  # midi out of range
        )

        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        with pytest.raises(ValidationError, match="pitch_midi"):
            extract_basic_pitch_annotations("song.wav")


# ---------------------------------------------------------------------------
# Isolation tests: basic-pitch notes stay out of rule-based RPE / SVP / scoring
# ---------------------------------------------------------------------------


class TestIsolation:
    def test_attach_does_not_mutate_input_bundle(self, monkeypatch):
        _install_fake_basic_pitch(
            monkeypatch,
            note_events=[(0.0, 0.5, 60, 0.9, [])],
        )

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        bundle = _make_bundle()
        annotations = extract_basic_pitch_annotations("song.wav")
        enriched = attach_learned_annotations(bundle, annotations)

        assert bundle.learned_annotations is None
        assert enriched.learned_annotations is not None
        assert enriched is not bundle

    def test_physical_rpe_unchanged_by_basic_pitch_attach(self, monkeypatch):
        _install_fake_basic_pitch(
            monkeypatch,
            note_events=[(0.0, 0.5, 60, 0.9, [])],
        )

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        bundle = _make_bundle()
        enriched = attach_learned_annotations(
            bundle,
            extract_basic_pitch_annotations("song.wav"),
        )

        # Two PhysicalRPE fields the policy doc explicitly forbids merging:
        assert enriched.physical.melody_contour == bundle.physical.melody_contour
        assert enriched.physical.chord_events == bundle.physical.chord_events
        # And the rest of physical is structurally identical.
        assert enriched.physical.model_dump() == bundle.physical.model_dump()

    def test_semantic_rpe_unchanged_by_basic_pitch_attach(self, monkeypatch):
        _install_fake_basic_pitch(
            monkeypatch,
            note_events=[(0.0, 0.5, 60, 0.9, [])],
        )

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        bundle = _make_bundle()
        enriched = attach_learned_annotations(
            bundle,
            extract_basic_pitch_annotations("song.wav"),
        )
        assert enriched.semantic.model_dump() == bundle.semantic.model_dump()

    def test_generate_svp_output_identical_with_and_without_notes(self, monkeypatch):
        _install_fake_basic_pitch(
            monkeypatch,
            note_events=[
                (0.0, 0.5, 60, 0.9, []),
                (0.5, 1.0, 64, 0.85, []),
                (1.0, 1.5, 67, 0.8, []),
            ],
        )

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        bundle = _make_bundle()
        enriched = attach_learned_annotations(
            bundle,
            extract_basic_pitch_annotations("song.wav"),
        )
        assert generate_svp(bundle).model_dump() == generate_svp(enriched).model_dump()

    def test_sentinel_source_model_does_not_leak_into_svp(self, monkeypatch):
        # Override the source_model string in the LearnedNoteEvent records.
        # If a future generator ever folds note events into SVP, the sentinel
        # appears in the SVP serialization and this test catches it.
        sentinel_source = "__BASIC_PITCH_LEAK_SENTINEL_MODEL__"
        _install_fake_basic_pitch(
            monkeypatch,
            note_events=[(0.0, 0.5, 60, 0.9, [])],
        )

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        annotations = extract_basic_pitch_annotations("song.wav")
        # Mutate the source_model to an obviously-distinct value before
        # attaching, so any leak path stands out in the SVP JSON.
        replaced = LearnedAudioAnnotations(
            **{
                **annotations.model_dump(),
                "note_events": [
                    {**ev.model_dump(), "source_model": sentinel_source}
                    for ev in annotations.note_events
                ],
            }
        )
        bundle = _make_bundle()
        enriched = attach_learned_annotations(bundle, replaced)

        # Sentinel reaches learned_annotations.note_events...
        assert any(
            ev.source_model == sentinel_source
            for ev in enriched.learned_annotations.note_events
        )
        # ...but never the SVP serialization.
        svp_json = generate_svp(enriched).model_dump_json()
        assert sentinel_source not in svp_json


class TestSerializerRegression:
    def test_bundle_without_learned_annotations_still_omits_field_in_dump(self):
        bundle = _make_bundle()
        assert "learned_annotations" not in bundle.model_dump()

    def test_bundle_with_note_events_includes_field_in_dump(self, monkeypatch):
        _install_fake_basic_pitch(
            monkeypatch,
            note_events=[(0.0, 0.5, 60, 0.9, [])],
        )

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.basic_pitch_adapter import (
            extract_basic_pitch_annotations,
        )

        bundle = _make_bundle()
        enriched = attach_learned_annotations(
            bundle,
            extract_basic_pitch_annotations("song.wav"),
        )
        dumped = enriched.model_dump()
        assert "learned_annotations" in dumped
        notes = dumped["learned_annotations"]["note_events"]
        assert len(notes) == 1
        assert notes[0]["pitch_midi"] == 60
        assert notes[0]["source_model"] == "basic_pitch"
