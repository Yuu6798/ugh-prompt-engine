"""tests/test_beat_this_adapter.py — beat_this adapter tests.

The real beat_this package is NOT required to run these tests. We monkeypatch
`sys.modules["beat_this"]` and `sys.modules["beat_this.inference"]` with a fake
backend so the adapter contract (dbn=False, time_events shape, isolation) can
be pinned without any optional install.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from svp_rpe.rpe.models import (
    DeltaEProfile,
    GrvAnchor,
    LearnedAudioAnnotations,
    LearnedTimeEvent,
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


def _install_fake_beat_this(
    monkeypatch: pytest.MonkeyPatch,
    *,
    beats: list[float],
    downbeats: list[float],
) -> dict:
    """Install a fake `beat_this.inference` module backed by FakeFile2Beats.

    Returns a dict that captures init kwargs and call args so tests can
    assert on what the adapter actually sent to the upstream API.
    """
    captured: dict = {}

    class FakeFile2Beats:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = dict(kwargs)

        def __call__(self, audio, sample_rate):
            captured["call_args"] = {
                "audio_shape": tuple(audio.shape),
                "sample_rate": sample_rate,
            }
            return list(beats), list(downbeats)

    fake_inference = types.ModuleType("beat_this.inference")
    fake_inference.File2Beats = FakeFile2Beats
    fake_root = types.ModuleType("beat_this")
    fake_root.inference = fake_inference

    monkeypatch.setitem(sys.modules, "beat_this", fake_root)
    monkeypatch.setitem(sys.modules, "beat_this.inference", fake_inference)
    return captured


def _force_beat_this_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # A None entry in sys.modules makes import_module raise ImportError per
    # documented Python semantics.
    monkeypatch.setitem(sys.modules, "beat_this", None)
    monkeypatch.setitem(sys.modules, "beat_this.inference", None)


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


class TestAdapterUnavailable:
    def test_raises_with_install_hint_when_beat_this_missing(self, monkeypatch):
        _force_beat_this_unavailable(monkeypatch)

        from svp_rpe.rpe.learned.beat_this_adapter import (
            LearnedModelUnavailable,
            extract_beat_this_annotations,
        )

        with pytest.raises(LearnedModelUnavailable, match="beat_this"):
            extract_beat_this_annotations(
                np.zeros(1024, dtype=np.float32), 22050
            )


class TestAdapterDbnFalseEnforcement:
    def test_dbn_false_is_passed_to_upstream(self, monkeypatch):
        captured = _install_fake_beat_this(monkeypatch, beats=[0.5], downbeats=[0.5])

        from svp_rpe.rpe.learned.beat_this_adapter import extract_beat_this_annotations

        extract_beat_this_annotations(np.zeros(1024, dtype=np.float32), 22050)

        assert captured["init_kwargs"]["dbn"] is False

    def test_caller_cannot_override_dbn(self, monkeypatch):
        captured = _install_fake_beat_this(monkeypatch, beats=[], downbeats=[])

        from svp_rpe.rpe.learned.beat_this_adapter import extract_beat_this_annotations

        # The adapter signature does not accept a dbn argument. Asserting the
        # signature stays narrow protects against a future "convenience" kwarg
        # that would let madmom DBN back in via the policy back door.
        with pytest.raises(TypeError):
            extract_beat_this_annotations(  # type: ignore[call-arg]
                np.zeros(1024, dtype=np.float32),
                22050,
                dbn=True,
            )
        # Sanity: even after the failed call, the previously captured init
        # kwargs (from the call earlier in this test, if any) are unaffected.
        # And a normal call still pins dbn=False.
        captured.clear()
        extract_beat_this_annotations(np.zeros(1024, dtype=np.float32), 22050)
        assert captured["init_kwargs"]["dbn"] is False


class TestAdapterOutputShape:
    def test_produces_expected_time_events(self, monkeypatch):
        _install_fake_beat_this(
            monkeypatch,
            beats=[0.5, 1.0, 1.5, 2.0],
            downbeats=[0.5, 2.0],
        )

        from svp_rpe.rpe.learned.beat_this_adapter import extract_beat_this_annotations

        result = extract_beat_this_annotations(
            np.zeros(1024, dtype=np.float32), 22050
        )

        assert isinstance(result, LearnedAudioAnnotations)
        assert all(isinstance(e, LearnedTimeEvent) for e in result.time_events)

        beat_times = [e.time_sec for e in result.time_events if e.event_type == "beat"]
        downbeat_times = [
            e.time_sec for e in result.time_events if e.event_type == "downbeat"
        ]
        assert beat_times == [0.5, 1.0, 1.5, 2.0]
        assert downbeat_times == [0.5, 2.0]
        assert all(e.source_model == "beat_this" for e in result.time_events)

    def test_records_provenance(self, monkeypatch):
        _install_fake_beat_this(monkeypatch, beats=[1.0], downbeats=[1.0])

        from svp_rpe.rpe.learned.beat_this_adapter import extract_beat_this_annotations

        result = extract_beat_this_annotations(
            np.zeros(1024, dtype=np.float32), 22050
        )

        assert len(result.enabled_models) == 1
        info = result.enabled_models[0]
        assert info.name == "beat_this"
        assert info.task == "beat_downbeat"
        assert info.license == "MIT"
        assert result.inference_config["dbn"] is False
        assert result.inference_config["source"] == "beat_this"
        assert result.license_metadata["beat_this"] == "MIT"

    def test_empty_beats_produces_empty_events(self, monkeypatch):
        _install_fake_beat_this(monkeypatch, beats=[], downbeats=[])

        from svp_rpe.rpe.learned.beat_this_adapter import extract_beat_this_annotations

        result = extract_beat_this_annotations(
            np.zeros(1024, dtype=np.float32), 22050
        )

        assert result.time_events == []
        # Provenance still set even with empty events.
        assert len(result.enabled_models) == 1


# ---------------------------------------------------------------------------
# Isolation tests: learned output stays out of rule-based RPE / SVP / scoring
# ---------------------------------------------------------------------------


class TestIsolation:
    def test_attach_does_not_mutate_input_bundle(self, monkeypatch):
        _install_fake_beat_this(monkeypatch, beats=[0.5], downbeats=[0.5])

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.beat_this_adapter import extract_beat_this_annotations

        bundle = _make_bundle()
        annotations = extract_beat_this_annotations(np.zeros(1024), 22050)
        enriched = attach_learned_annotations(bundle, annotations)

        assert bundle.learned_annotations is None
        assert enriched.learned_annotations is not None
        assert enriched is not bundle

    def test_physical_rpe_unchanged_by_learned_attach(self, monkeypatch):
        _install_fake_beat_this(
            monkeypatch,
            beats=[0.5, 1.0, 1.5],
            downbeats=[0.5, 1.5],
        )

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.beat_this_adapter import extract_beat_this_annotations

        bundle = _make_bundle()
        enriched = attach_learned_annotations(
            bundle,
            extract_beat_this_annotations(np.zeros(1024), 22050),
        )

        # The two PhysicalRPE fields the policy doc explicitly forbids merging:
        assert enriched.physical.downbeat_times == bundle.physical.downbeat_times == []
        assert enriched.physical.time_signature == "4/4"
        # And the rest of physical is structurally identical.
        assert enriched.physical.model_dump() == bundle.physical.model_dump()

    def test_semantic_rpe_unchanged_by_learned_attach(self, monkeypatch):
        _install_fake_beat_this(monkeypatch, beats=[0.5], downbeats=[0.5])

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.beat_this_adapter import extract_beat_this_annotations

        bundle = _make_bundle()
        enriched = attach_learned_annotations(
            bundle,
            extract_beat_this_annotations(np.zeros(1024), 22050),
        )
        assert enriched.semantic.model_dump() == bundle.semantic.model_dump()

    def test_generate_svp_output_identical_with_and_without_learned(self, monkeypatch):
        _install_fake_beat_this(
            monkeypatch,
            beats=[0.5, 1.0, 1.5],
            downbeats=[0.5, 1.5],
        )

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.beat_this_adapter import extract_beat_this_annotations

        bundle = _make_bundle()
        enriched = attach_learned_annotations(
            bundle,
            extract_beat_this_annotations(np.zeros(1024), 22050),
        )

        assert generate_svp(bundle).model_dump() == generate_svp(enriched).model_dump()

    def test_learned_time_event_does_not_serialize_into_svp(self, monkeypatch):
        # Use a sentinel timestamp that would never legitimately appear elsewhere.
        sentinel = 987654321.0
        _install_fake_beat_this(
            monkeypatch,
            beats=[sentinel],
            downbeats=[sentinel],
        )

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.beat_this_adapter import extract_beat_this_annotations

        bundle = _make_bundle()
        enriched = attach_learned_annotations(
            bundle,
            extract_beat_this_annotations(np.zeros(1024), 22050),
        )
        svp_json = generate_svp(enriched).model_dump_json()
        assert str(sentinel) not in svp_json
        assert repr(sentinel) not in svp_json


class TestSerializerRegression:
    def test_bundle_without_learned_annotations_still_omits_field_in_dump(self):
        bundle = _make_bundle()
        assert "learned_annotations" not in bundle.model_dump()

    def test_bundle_with_time_events_includes_field_in_dump(self, monkeypatch):
        _install_fake_beat_this(monkeypatch, beats=[0.5], downbeats=[0.5])

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.beat_this_adapter import extract_beat_this_annotations

        bundle = _make_bundle()
        enriched = attach_learned_annotations(
            bundle,
            extract_beat_this_annotations(np.zeros(1024), 22050),
        )
        dumped = enriched.model_dump()
        assert "learned_annotations" in dumped
        time_events = dumped["learned_annotations"]["time_events"]
        assert {e["event_type"] for e in time_events} == {"beat", "downbeat"}
