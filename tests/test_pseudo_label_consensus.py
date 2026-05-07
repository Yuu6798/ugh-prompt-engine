"""Tests for scripts/build_pseudo_label_consensus.py."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from scripts import build_pseudo_label_consensus as pc
from svp_rpe.io.audio_loader import AudioData, AudioMetadata
from svp_rpe.rpe.models import (
    ChordEvent,
    DeltaEProfile,
    GrvAnchor,
    LearnedAudioAnnotations,
    LearnedNoteEvent,
    LearnedTimeEvent,
    MelodyContour,
    PhysicalRPE,
    RPEBundle,
    SectionMarker,
    SemanticLabel,
    SemanticRPE,
    SpectralProfile,
)


def _fake_audio(path: Path) -> AudioData:
    return AudioData(
        metadata=AudioMetadata(
            file_path=str(path),
            duration_sec=2.0,
            sample_rate=22050,
            channels=1,
            format="wav",
        ),
        y_mono=np.zeros(44100, dtype=np.float32),
        y_stereo=None,
        sr=22050,
    )


def _make_bundle(
    *,
    downbeats: list[float],
    contour: MelodyContour | None,
    chord_events: list[ChordEvent] | None = None,
) -> RPEBundle:
    return RPEBundle(
        physical=PhysicalRPE(
            duration_sec=2.0,
            sample_rate=22050,
            downbeat_times=downbeats,
            chord_events=chord_events or [],
            melody_contour=contour,
            structure=[SectionMarker(label="section_01", start_sec=0.0, end_sec=2.0)],
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
            grv_anchor=GrvAnchor(primary="bright"),
            delta_e_profile=DeltaEProfile(
                transition_type="flat",
                intensity=0.3,
                description="steady",
            ),
            cultural_context=["pop"],
            instrumentation_summary="synths",
            production_notes=["compressed"],
            confidence_notes=["rule"],
        ),
        audio_file="song.wav",
        audio_duration_sec=2.0,
        audio_sample_rate=22050,
        audio_channels=1,
        audio_format="wav",
    )


def _contour_for_midi(pitch_midi: int) -> MelodyContour:
    frequency = 440.0 * float(2.0 ** ((pitch_midi - 69.0) / 12.0))
    return MelodyContour(
        times=[0.0, 0.1, 0.2, 0.3, 0.4],
        frequencies_hz=[frequency] * 5,
        voicing=[1.0] * 5,
    )


def _write_manifest(tmp_path: Path) -> tuple[Path, Path]:
    audio_path = tmp_path / "song.wav"
    audio_path.write_bytes(b"placeholder")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "schema_version: '1.0'\n"
        "tracks:\n"
        "  - id: fake_song\n"
        "    path: song.wav\n",
        encoding="utf-8",
    )
    return manifest, audio_path


def test_unavailable_learned_models_are_reported_as_skipped(monkeypatch, tmp_path):
    manifest, audio_path = _write_manifest(tmp_path)
    loaded_audio = _fake_audio(audio_path)
    load_calls = 0

    def fake_load_audio(*args, **kwargs):
        nonlocal load_calls
        load_calls += 1
        return loaded_audio

    def fake_extract_rpe_from_file(*args, **kwargs):
        assert kwargs["preloaded_audio"] is loaded_audio
        return _make_bundle(
            downbeats=[0.0, 1.0],
            contour=_contour_for_midi(60),
            chord_events=[
                ChordEvent(
                    chord="C major",
                    root="C",
                    quality="major",
                    start_sec=0.0,
                    end_sec=1.0,
                    confidence=0.8,
                )
            ],
        )

    monkeypatch.setattr(pc, "load_audio", fake_load_audio)
    monkeypatch.setattr(pc, "extract_rpe_from_file", fake_extract_rpe_from_file)
    monkeypatch.setattr(
        pc,
        "extract_learned_annotations",
        lambda *args, **kwargs: (
            None,
            {"beat_this": "beat extra missing", "basic_pitch": "pitch extra missing"},
        ),
    )

    payload = pc.evaluate_manifest(
        manifest,
        output_dir=tmp_path / "out",
        baseline="pro",
    )

    track = payload["tracks"][0]
    assert track["downbeat"]["skipped"] == "beat extra missing"
    assert track["melody"]["skipped"] == "pitch extra missing"
    assert track["chord"]["skipped"] == "pitch extra missing"
    assert track["overall_confidence"] == 0.0
    assert payload["summary"]["downbeat"]["skipped"] == 1
    assert load_calls == 1


def test_fake_learned_annotations_create_high_consensus(monkeypatch, tmp_path):
    manifest, audio_path = _write_manifest(tmp_path)
    monkeypatch.setattr(pc, "load_audio", lambda *args, **kwargs: _fake_audio(audio_path))
    monkeypatch.setattr(
        pc,
        "extract_rpe_from_file",
        lambda *args, **kwargs: _make_bundle(
            downbeats=[0.0, 1.0],
            contour=_contour_for_midi(60),
            chord_events=[
                ChordEvent(
                    chord="C major",
                    root="C",
                    quality="major",
                    start_sec=0.0,
                    end_sec=1.0,
                    confidence=0.8,
                )
            ],
        ),
    )
    learned = LearnedAudioAnnotations(
        time_events=[
            LearnedTimeEvent(time_sec=0.01, event_type="downbeat", source_model="fake"),
            LearnedTimeEvent(time_sec=1.02, event_type="downbeat", source_model="fake"),
        ],
        note_events=[
            LearnedNoteEvent(
                start_sec=0.0,
                end_sec=0.5,
                pitch_midi=60,
                confidence=0.95,
                source_model="fake",
            ),
            LearnedNoteEvent(
                start_sec=0.0,
                end_sec=1.0,
                pitch_midi=64,
                confidence=0.9,
                source_model="fake",
            ),
            LearnedNoteEvent(
                start_sec=0.0,
                end_sec=1.0,
                pitch_midi=67,
                confidence=0.9,
                source_model="fake",
            ),
        ],
    )
    monkeypatch.setattr(
        pc,
        "extract_learned_annotations",
        lambda *args, **kwargs: (learned, {}),
    )

    payload = pc.evaluate_manifest(
        manifest,
        output_dir=tmp_path / "out",
        baseline="pro",
    )

    track = payload["tracks"][0]
    assert track["downbeat"]["f_measure"] == 1.0
    assert track["melody"]["matched_count"] == 1
    assert track["melody"]["f_measure"] > 0.0
    assert track["chord"]["f_measure"] == 1.0
    assert track["overall_confidence"] > 0.0


def test_disagreement_lowers_consensus(monkeypatch, tmp_path):
    manifest, audio_path = _write_manifest(tmp_path)
    monkeypatch.setattr(pc, "load_audio", lambda *args, **kwargs: _fake_audio(audio_path))
    monkeypatch.setattr(
        pc,
        "extract_rpe_from_file",
        lambda *args, **kwargs: _make_bundle(
            downbeats=[0.0, 1.0],
            contour=_contour_for_midi(60),
            chord_events=[
                ChordEvent(
                    chord="C major",
                    root="C",
                    quality="major",
                    start_sec=0.0,
                    end_sec=1.0,
                    confidence=0.8,
                )
            ],
        ),
    )
    learned = LearnedAudioAnnotations(
        time_events=[
            LearnedTimeEvent(time_sec=0.4, event_type="downbeat", source_model="fake"),
            LearnedTimeEvent(time_sec=1.4, event_type="downbeat", source_model="fake"),
        ],
        note_events=[
            LearnedNoteEvent(
                start_sec=0.4,
                end_sec=0.9,
                pitch_midi=62,
                confidence=0.9,
                source_model="fake",
            )
        ],
    )
    monkeypatch.setattr(
        pc,
        "extract_learned_annotations",
        lambda *args, **kwargs: (learned, {}),
    )

    payload = pc.evaluate_manifest(
        manifest,
        output_dir=tmp_path / "out",
        baseline="pro",
    )

    track = payload["tracks"][0]
    assert track["downbeat"]["f_measure"] == 0.0
    assert track["melody"]["f_measure"] == 0.0
    assert track["chord"]["f_measure"] == 0.0
    assert track["overall_confidence"] == 0.0


def test_frame_level_melody_contour_is_binned_into_notes():
    contour = MelodyContour(
        times=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
        frequencies_hz=[
            440.0 * float(2.0 ** ((60 - 69.0) / 12.0)),
            440.0 * float(2.0 ** ((60 - 69.0) / 12.0)),
            440.0 * float(2.0 ** ((60 - 69.0) / 12.0)),
            440.0 * float(2.0 ** ((62 - 69.0) / 12.0)),
            440.0 * float(2.0 ** ((62 - 69.0) / 12.0)),
            0.0,
        ],
        voicing=[1.0, 1.0, 1.0, 1.0, 1.0, 0.0],
    )

    notes = pc._bin_melody_contour_to_notes(contour)

    assert notes == [
        pc.NoteEvent(start_sec=0.0, end_sec=0.3, pitch_midi=60),
        pc.NoteEvent(start_sec=0.3, end_sec=0.5, pitch_midi=62),
    ]
