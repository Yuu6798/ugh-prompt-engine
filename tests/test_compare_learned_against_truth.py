"""Tests for scripts/compare_learned_against_truth.py.

The real optional learned-model packages are not loaded in CI. Tests patch the
harness at its adapter boundaries and exercise the metric / winner code paths.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from scripts import compare_learned_against_truth as cl
from svp_rpe.io.audio_loader import AudioData, AudioMetadata
from svp_rpe.rpe.models import (
    DeltaEProfile,
    GrvAnchor,
    LearnedAudioAnnotations,
    LearnedModelInfo,
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


def _make_bundle(*, downbeats: list[float], contour: MelodyContour | None) -> RPEBundle:
    return RPEBundle(
        physical=PhysicalRPE(
            duration_sec=2.0,
            sample_rate=22050,
            downbeat_times=downbeats,
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
        audio_file="song.wav",
        audio_duration_sec=2.0,
        audio_sample_rate=22050,
        audio_channels=1,
        audio_format="wav",
    )


def _fake_audio() -> AudioData:
    return AudioData(
        metadata=AudioMetadata(
            file_path="song.wav",
            duration_sec=2.0,
            sample_rate=22050,
            channels=1,
            format="wav",
        ),
        y_mono=np.zeros(22050, dtype=np.float32),
        y_stereo=None,
        sr=22050,
    )


def _truth_song() -> cl.TruthSong:
    return cl.TruthSong(
        song_id="fake_song",
        audio_path=Path("song.wav"),
        downbeats_sec=[0.0, 1.0],
        melody_events=[
            {
                "note": "C4",
                "frequency_hz": 261.6256,
                "start_sec": 0.0,
                "end_sec": 1.0,
            }
        ],
    )


def test_unavailable_learned_models_emit_skipped_metrics(monkeypatch):
    contour = MelodyContour(
        times=[0.0, 0.1, 0.2, 0.3],
        frequencies_hz=[261.6256, 261.6256, 261.6256, 261.6256],
        voicing=[1.0, 1.0, 1.0, 1.0],
    )
    monkeypatch.setattr(cl, "extract_rpe_from_file", lambda *args, **kwargs: _make_bundle(
        downbeats=[0.0, 1.0],
        contour=contour,
    ))
    monkeypatch.setattr(cl, "load_audio", lambda *args, **kwargs: _fake_audio())

    def raise_unavailable(*args, **kwargs):
        raise cl.LearnedModelUnavailable("optional dependency missing")

    monkeypatch.setattr(cl, "extract_beat_this_annotations", raise_unavailable)
    monkeypatch.setattr(cl, "extract_basic_pitch_annotations", raise_unavailable)

    result = cl.evaluate_song(_truth_song())

    assert result.downbeat.learned.skipped == "optional dependency missing"
    assert result.note.learned.skipped == "optional dependency missing"
    assert result.downbeat.winner == "skipped"
    assert result.note.winner == "skipped"


def test_cli_json_exits_zero_when_learned_models_are_unavailable(
    monkeypatch,
    capsys,
):
    contour = MelodyContour(
        times=[0.0, 0.1, 0.2, 0.3],
        frequencies_hz=[261.6256, 261.6256, 261.6256, 261.6256],
        voicing=[1.0, 1.0, 1.0, 1.0],
    )
    monkeypatch.setattr(cl, "load_truth", lambda: [_truth_song()])
    monkeypatch.setattr(
        cl,
        "extract_rpe_from_file",
        lambda *args, **kwargs: _make_bundle(
            downbeats=[0.0, 1.0],
            contour=contour,
        ),
    )
    monkeypatch.setattr(cl, "load_audio", lambda *args, **kwargs: _fake_audio())
    monkeypatch.setattr(cl, "write_report", lambda payload: None)

    def raise_unavailable(*args, **kwargs):
        raise cl.LearnedModelUnavailable("optional dependency missing")

    monkeypatch.setattr(cl, "extract_beat_this_annotations", raise_unavailable)
    monkeypatch.setattr(cl, "extract_basic_pitch_annotations", raise_unavailable)

    assert cl.main(["--json", "--song", "fake_song"]) == 0
    payload = cl.json.loads(capsys.readouterr().out)

    assert payload["songs"][0]["downbeat"]["learned"]["skipped"] == (
        "optional dependency missing"
    )
    assert payload["songs"][0]["note"]["learned"]["skipped"] == (
        "optional dependency missing"
    )
    assert payload["songs"][0]["downbeat"]["winner"] == "skipped"
    assert payload["songs"][0]["note"]["winner"] == "skipped"
    assert payload["summary"]["downbeat_wins"] == {
        "deterministic": 0,
        "learned": 0,
        "tie": 0,
        "skipped": 1,
    }
    assert payload["summary"]["note_wins"] == {
        "deterministic": 0,
        "learned": 0,
        "tie": 0,
        "skipped": 1,
    }


def test_fake_learned_annotations_can_win_against_deterministic(monkeypatch):
    wrong_pitch = cl._midi_to_frequency(62)
    contour = MelodyContour(
        times=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
        frequencies_hz=[wrong_pitch] * 6,
        voicing=[1.0] * 6,
    )
    monkeypatch.setattr(cl, "extract_rpe_from_file", lambda *args, **kwargs: _make_bundle(
        downbeats=[0.2, 1.2],
        contour=contour,
    ))
    monkeypatch.setattr(cl, "load_audio", lambda *args, **kwargs: _fake_audio())
    monkeypatch.setattr(
        cl,
        "extract_beat_this_annotations",
        lambda *args, **kwargs: LearnedAudioAnnotations(
            time_events=[
                LearnedTimeEvent(time_sec=0.0, event_type="downbeat", source_model="fake"),
                LearnedTimeEvent(time_sec=1.0, event_type="downbeat", source_model="fake"),
            ]
        ),
    )
    monkeypatch.setattr(
        cl,
        "extract_basic_pitch_annotations",
        lambda *args, **kwargs: LearnedAudioAnnotations(
            note_events=[
                LearnedNoteEvent(
                    start_sec=0.0,
                    end_sec=1.0,
                    pitch_midi=60,
                    confidence=0.9,
                    source_model="fake",
                ),
                LearnedNoteEvent(
                    start_sec=1.3,
                    end_sec=1.8,
                    pitch_midi=64,
                    confidence=0.7,
                    source_model="fake",
                ),
            ]
        ),
    )

    result = cl.evaluate_song(_truth_song())

    assert result.downbeat.deterministic.f_measure_70ms == 0.0
    assert result.downbeat.learned.f_measure_70ms == 1.0
    assert result.downbeat.winner == "learned"
    assert result.note.deterministic.onset_pitch_f == 0.0
    assert result.note.learned.n_estimated == 2
    assert result.note.learned.onset_pitch_f == 0.6667
    assert result.note.winner == "learned"


def test_frame_level_melody_contour_is_binned_into_note_events():
    contour = MelodyContour(
        times=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
        frequencies_hz=[
            cl._midi_to_frequency(60),
            cl._midi_to_frequency(60),
            cl._midi_to_frequency(60),
            cl._midi_to_frequency(62),
            cl._midi_to_frequency(62),
            0.0,
        ],
        voicing=[1.0, 1.0, 1.0, 1.0, 1.0, 0.0],
    )

    notes = cl._bin_melody_contour_to_notes(contour)

    assert notes == [
        cl.NoteEvent(start_sec=0.0, end_sec=0.3, pitch_midi=60),
        cl.NoteEvent(start_sec=0.3, end_sec=0.5, pitch_midi=62),
    ]


def test_winner_uses_tie_threshold_and_skipped_state():
    assert cl._winner(0.5, 0.509, learned_skipped=False) == "tie"
    assert cl._winner(0.5, 0.511, learned_skipped=False) == "learned"
    assert cl._winner(0.5, 0.0, learned_skipped=True) == "skipped"


def test_merge_annotations_namespaces_inference_config_by_model():
    merged = cl._merge_annotations(
        [
            LearnedAudioAnnotations(
                enabled_models=[
                    LearnedModelInfo(name="beat_this", task="beat_downbeat")
                ],
                inference_config={
                    "source": "beat_this",
                    "entry_point": "Audio2Beats",
                },
            ),
            LearnedAudioAnnotations(
                enabled_models=[LearnedModelInfo(name="basic_pitch", task="pitch")],
                inference_config={
                    "source": "basic_pitch",
                    "entry_point": "predict",
                },
            ),
        ]
    )

    assert merged.inference_config == {
        "beat_this": {
            "source": "beat_this",
            "entry_point": "Audio2Beats",
        },
        "basic_pitch": {
            "source": "basic_pitch",
            "entry_point": "predict",
        },
    }
