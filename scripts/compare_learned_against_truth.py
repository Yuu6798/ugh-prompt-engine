"""Compare learned annotations against deterministic RPE estimates.

This is a read-only validation harness for the synthetic sample corpus. It
compares deterministic fields (`PhysicalRPE.downbeat_times`,
`PhysicalRPE.melody_contour`) and learned annotations (`beat_this` time events,
`basic-pitch` note events) against `examples/sample_input/ground_truth.yaml`.

The script never writes learned output back into `PhysicalRPE` or `SemanticRPE`.
Generated reports are development artifacts under `examples/learned_validation/`.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import mir_eval.beat
import mir_eval.transcription
import mir_eval.util
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from svp_rpe.io.audio_loader import AudioData, load_audio  # noqa: E402
from svp_rpe.rpe.extractor import extract_rpe_from_file  # noqa: E402
from svp_rpe.rpe.learned import (  # noqa: E402
    LearnedModelUnavailable,
    attach_learned_annotations,
)
from svp_rpe.rpe.learned.basic_pitch_adapter import (  # noqa: E402
    extract_basic_pitch_annotations,
)
from svp_rpe.rpe.learned.beat_this_adapter import (  # noqa: E402
    extract_beat_this_annotations,
)
from svp_rpe.rpe.models import (  # noqa: E402
    LearnedAudioAnnotations,
    LearnedNoteEvent,
    LearnedTimeEvent,
    MelodyContour,
)

SCHEMA_VERSION = "1.0"
SAMPLE_DIR = ROOT / "examples" / "sample_input"
GROUND_TRUTH = SAMPLE_DIR / "ground_truth.yaml"
OUTPUT_DIR = ROOT / "examples" / "learned_validation"
PER_SONG_DIR = OUTPUT_DIR / "per_song"

VALLEY_METHOD = "hybrid"
DOWNBEAT_F_WINDOW_SEC = 0.07
DOWNBEAT_HIT_WINDOW_SEC = 0.35
NOTE_ONSET_TOLERANCE_SEC = 0.05
NOTE_PITCH_TOLERANCE_CENTS = 50.0
NOTE_OFFSET_RATIO = 0.2
MIN_NOTE_DURATION_SEC = 0.1
WIN_TIE_EPSILON = 0.01


@dataclass
class TruthSong:
    song_id: str
    audio_path: Path
    downbeats_sec: list[float]
    melody_events: list[dict[str, Any]]


@dataclass
class NoteEvent:
    start_sec: float
    end_sec: float
    pitch_midi: int


@dataclass
class DownbeatMetrics:
    n_reference: int
    n_estimated: int
    precision_70ms: float
    recall_70ms: float
    f_measure_70ms: float
    hit_rate_350ms: float
    mean_abs_error_sec: float | None
    skipped: str | None = None


@dataclass
class NoteMetrics:
    n_reference: int
    n_estimated: int
    onset_f_50ms: float
    onset_pitch_f: float
    onset_pitch_offset_f: float
    mean_abs_cents: float | None
    skipped: str | None = None


@dataclass
class DownbeatComparison:
    deterministic: DownbeatMetrics
    learned: DownbeatMetrics
    winner: str


@dataclass
class NoteComparison:
    deterministic: NoteMetrics
    learned: NoteMetrics
    winner: str


@dataclass
class SongComparison:
    song_id: str
    downbeat: DownbeatComparison
    note: NoteComparison


def load_truth() -> list[TruthSong]:
    raw = yaml.safe_load(GROUND_TRUTH.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"unexpected ground_truth.yaml structure: {type(raw)}")

    songs: list[TruthSong] = []
    for entry in raw:
        audio_path = SAMPLE_DIR / entry["filename"]
        if not audio_path.is_file():
            raise FileNotFoundError(f"missing sample WAV: {audio_path}")
        songs.append(
            TruthSong(
                song_id=str(entry["id"]),
                audio_path=audio_path,
                downbeats_sec=[float(t) for t in entry.get("downbeats_sec", [])],
                melody_events=list(entry.get("melody_events", [])),
            )
        )
    return songs


def _frequency_to_midi(frequency_hz: float) -> float:
    return 69.0 + 12.0 * float(np.log2(frequency_hz / 440.0))


def _midi_to_frequency(pitch_midi: float) -> float:
    return 440.0 * float(2.0 ** ((pitch_midi - 69.0) / 12.0))


def _truth_notes_from_melody_events(events: list[dict[str, Any]]) -> list[NoteEvent]:
    notes: list[NoteEvent] = []
    for event in events:
        if "frequency_hz" not in event:
            continue
        frequency_hz = float(event["frequency_hz"])
        if frequency_hz <= 0.0:
            continue
        notes.append(
            NoteEvent(
                start_sec=float(event["start_sec"]),
                end_sec=float(event["end_sec"]),
                pitch_midi=int(round(_frequency_to_midi(frequency_hz))),
            )
        )
    return notes


def _learned_note_events_to_notes(events: Iterable[LearnedNoteEvent]) -> list[NoteEvent]:
    return [
        NoteEvent(
            start_sec=float(event.start_sec),
            end_sec=float(event.end_sec),
            pitch_midi=int(event.pitch_midi),
        )
        for event in events
    ]


def _bin_melody_contour_to_notes(
    contour: MelodyContour | None,
    *,
    min_duration_sec: float = MIN_NOTE_DURATION_SEC,
) -> list[NoteEvent]:
    if contour is None or not contour.times:
        return []

    times = [float(t) for t in contour.times]
    frequencies = [float(f) for f in contour.frequencies_hz]
    voicing = [float(v) for v in contour.voicing]
    hop = float(np.median(np.diff(times))) if len(times) > 1 else 0.0

    notes: list[NoteEvent] = []
    current_start: float | None = None
    current_end: float | None = None
    current_midi: int | None = None

    def flush() -> None:
        nonlocal current_start, current_end, current_midi
        if current_start is None or current_end is None or current_midi is None:
            return
        if current_end - current_start >= min_duration_sec:
            notes.append(
                NoteEvent(
                    start_sec=current_start,
                    end_sec=current_end,
                    pitch_midi=current_midi,
                )
            )
        current_start = None
        current_end = None
        current_midi = None

    for index, (time_sec, frequency_hz, voice) in enumerate(
        zip(times, frequencies, voicing)
    ):
        frame_end = times[index + 1] if index + 1 < len(times) else time_sec + hop
        voiced = voice >= 0.5 and frequency_hz > 0.0
        if not voiced:
            flush()
            continue

        midi = int(round(_frequency_to_midi(frequency_hz)))
        if current_midi == midi and current_end is not None:
            current_end = frame_end
            continue

        flush()
        current_start = time_sec
        current_end = frame_end
        current_midi = midi

    flush()
    return notes


def _arrays_for_notes(notes: list[NoteEvent]) -> tuple[np.ndarray, np.ndarray]:
    if not notes:
        return np.zeros((0, 2), dtype=float), np.zeros(0, dtype=float)
    intervals = np.array(
        [[note.start_sec, note.end_sec] for note in notes],
        dtype=float,
    )
    pitches_hz = np.array(
        [_midi_to_frequency(note.pitch_midi) for note in notes],
        dtype=float,
    )
    return intervals, pitches_hz


def _match_rate_and_error(
    reference: np.ndarray,
    estimated: np.ndarray,
    *,
    window_sec: float,
) -> tuple[float, float, float | None]:
    if reference.size == 0:
        return 0.0, 0.0, None
    if estimated.size == 0:
        return 0.0, 0.0, None

    matches = mir_eval.util.match_events(reference, estimated, window_sec)
    precision = len(matches) / float(estimated.size) if estimated.size else 0.0
    recall = len(matches) / float(reference.size) if reference.size else 0.0
    errors = [abs(float(reference[ref_i] - estimated[est_i])) for ref_i, est_i in matches]
    mean_error = float(np.mean(errors)) if errors else None
    return precision, recall, mean_error


def evaluate_downbeat_times(
    estimated_downbeats: list[float],
    truth_downbeats: list[float],
    *,
    skipped: str | None = None,
) -> DownbeatMetrics:
    reference = np.sort(np.array(truth_downbeats, dtype=float))
    estimated = np.sort(np.array(estimated_downbeats, dtype=float))

    if skipped is not None:
        return DownbeatMetrics(
            n_reference=int(reference.size),
            n_estimated=0,
            precision_70ms=0.0,
            recall_70ms=0.0,
            f_measure_70ms=0.0,
            hit_rate_350ms=0.0,
            mean_abs_error_sec=None,
            skipped=skipped,
        )

    if reference.size == 0 or estimated.size == 0:
        return DownbeatMetrics(
            n_reference=int(reference.size),
            n_estimated=int(estimated.size),
            precision_70ms=0.0,
            recall_70ms=0.0,
            f_measure_70ms=0.0,
            hit_rate_350ms=0.0,
            mean_abs_error_sec=None,
        )

    precision_70ms, recall_70ms, _ = _match_rate_and_error(
        reference,
        estimated,
        window_sec=DOWNBEAT_F_WINDOW_SEC,
    )
    _, hit_recall_350ms, mean_error = _match_rate_and_error(
        reference,
        estimated,
        window_sec=DOWNBEAT_HIT_WINDOW_SEC,
    )
    return DownbeatMetrics(
        n_reference=int(reference.size),
        n_estimated=int(estimated.size),
        precision_70ms=round(precision_70ms, 4),
        recall_70ms=round(recall_70ms, 4),
        f_measure_70ms=round(
            float(
                mir_eval.beat.f_measure(
                    reference,
                    estimated,
                    f_measure_threshold=DOWNBEAT_F_WINDOW_SEC,
                )
            ),
            4,
        ),
        hit_rate_350ms=round(hit_recall_350ms, 4),
        mean_abs_error_sec=round(mean_error, 4) if mean_error is not None else None,
    )


def _select_best_overlapping_predictions(
    predictions: list[NoteEvent],
    truth_notes: list[NoteEvent],
) -> list[NoteEvent]:
    selected: list[NoteEvent] = []
    used_indices: set[int] = set()
    for truth in truth_notes:
        best_index: int | None = None
        best_overlap = 0.0
        for index, pred in enumerate(predictions):
            if index in used_indices:
                continue
            overlap = max(
                0.0,
                min(truth.end_sec, pred.end_sec) - max(truth.start_sec, pred.start_sec),
            )
            if overlap > best_overlap:
                best_index = index
                best_overlap = overlap
        if best_index is not None and best_overlap > 0.0:
            used_indices.add(best_index)
            selected.append(predictions[best_index])
    return selected


def _f_measure_transcription(
    truth_notes: list[NoteEvent],
    predicted_notes: list[NoteEvent],
    *,
    pitch_tolerance: float,
    offset_ratio: float | None,
) -> float:
    if not truth_notes or not predicted_notes:
        return 0.0
    ref_intervals, ref_pitches = _arrays_for_notes(truth_notes)
    est_intervals, est_pitches = _arrays_for_notes(predicted_notes)
    _, _, f_measure, _ = mir_eval.transcription.precision_recall_f1_overlap(
        ref_intervals,
        ref_pitches,
        est_intervals,
        est_pitches,
        onset_tolerance=NOTE_ONSET_TOLERANCE_SEC,
        pitch_tolerance=pitch_tolerance,
        offset_ratio=offset_ratio,
    )
    return round(float(f_measure), 4)


def _mean_abs_cents(
    truth_notes: list[NoteEvent],
    predicted_notes: list[NoteEvent],
) -> float | None:
    if not truth_notes or not predicted_notes:
        return None

    ref_starts = np.array([note.start_sec for note in truth_notes], dtype=float)
    est_starts = np.array([note.start_sec for note in predicted_notes], dtype=float)
    matches = mir_eval.util.match_events(
        ref_starts,
        est_starts,
        NOTE_ONSET_TOLERANCE_SEC,
    )
    if not matches:
        return None

    cents: list[float] = []
    for ref_i, est_i in matches:
        ref_hz = _midi_to_frequency(truth_notes[ref_i].pitch_midi)
        est_hz = _midi_to_frequency(predicted_notes[est_i].pitch_midi)
        cents.append(abs(1200.0 * float(np.log2(est_hz / ref_hz))))
    return round(float(np.mean(cents)), 4) if cents else None


def evaluate_note_predictions(
    predicted_notes: list[NoteEvent],
    truth_notes: list[NoteEvent],
    *,
    skipped: str | None = None,
) -> NoteMetrics:
    if skipped is not None:
        return NoteMetrics(
            n_reference=len(truth_notes),
            n_estimated=0,
            onset_f_50ms=0.0,
            onset_pitch_f=0.0,
            onset_pitch_offset_f=0.0,
            mean_abs_cents=None,
            skipped=skipped,
        )

    return NoteMetrics(
        n_reference=len(truth_notes),
        n_estimated=len(predicted_notes),
        onset_f_50ms=_f_measure_transcription(
            truth_notes,
            predicted_notes,
            pitch_tolerance=1_000_000.0,
            offset_ratio=None,
        ),
        onset_pitch_f=_f_measure_transcription(
            truth_notes,
            predicted_notes,
            pitch_tolerance=NOTE_PITCH_TOLERANCE_CENTS,
            offset_ratio=None,
        ),
        onset_pitch_offset_f=_f_measure_transcription(
            truth_notes,
            predicted_notes,
            pitch_tolerance=NOTE_PITCH_TOLERANCE_CENTS,
            offset_ratio=NOTE_OFFSET_RATIO,
        ),
        mean_abs_cents=_mean_abs_cents(truth_notes, predicted_notes),
    )


def _winner(
    deterministic_score: float,
    learned_score: float,
    *,
    learned_skipped: bool,
) -> str:
    if learned_skipped:
        return "deterministic"
    diff = learned_score - deterministic_score
    if abs(diff) < WIN_TIE_EPSILON:
        return "tie"
    return "learned" if diff > 0.0 else "deterministic"


def _merge_annotations(
    annotations: Iterable[LearnedAudioAnnotations],
) -> LearnedAudioAnnotations:
    enabled_models = []
    labels = []
    time_events: list[LearnedTimeEvent] = []
    note_events: list[LearnedNoteEvent] = []
    inference_config: dict[str, Any] = {}
    license_metadata: dict[str, str] = {}

    for annotation in annotations:
        enabled_models.extend(annotation.enabled_models)
        labels.extend(annotation.labels)
        time_events.extend(annotation.time_events)
        note_events.extend(annotation.note_events)
        for info in annotation.enabled_models:
            inference_config[info.name] = annotation.inference_config
        license_metadata.update(annotation.license_metadata)

    return LearnedAudioAnnotations(
        enabled_models=enabled_models,
        labels=labels,
        time_events=time_events,
        note_events=note_events,
        inference_config=inference_config,
        license_metadata=license_metadata,
    )


def _extract_learned_annotations(
    song: TruthSong,
    audio: AudioData,
) -> tuple[LearnedAudioAnnotations, str | None, str | None]:
    annotations: list[LearnedAudioAnnotations] = []
    beat_skip: str | None = None
    pitch_skip: str | None = None

    try:
        annotations.append(extract_beat_this_annotations(audio.y_mono, audio.sr))
    except LearnedModelUnavailable as exc:
        beat_skip = str(exc)

    try:
        annotations.append(extract_basic_pitch_annotations(song.audio_path))
    except LearnedModelUnavailable as exc:
        pitch_skip = str(exc)

    return _merge_annotations(annotations), beat_skip, pitch_skip


def evaluate_song(song: TruthSong) -> SongComparison:
    bundle = extract_rpe_from_file(str(song.audio_path), valley_method=VALLEY_METHOD)
    audio = load_audio(song.audio_path)
    learned_annotations, beat_skip, pitch_skip = _extract_learned_annotations(song, audio)
    bundle = attach_learned_annotations(bundle, learned_annotations)
    learned = bundle.learned_annotations or LearnedAudioAnnotations()

    truth_notes = _truth_notes_from_melody_events(song.melody_events)
    deterministic_notes = _bin_melody_contour_to_notes(bundle.physical.melody_contour)
    learned_notes = _select_best_overlapping_predictions(
        _learned_note_events_to_notes(learned.note_events),
        truth_notes,
    )
    learned_downbeats = [
        event.time_sec
        for event in learned.time_events
        if event.event_type == "downbeat"
    ]

    det_downbeat = evaluate_downbeat_times(
        bundle.physical.downbeat_times,
        song.downbeats_sec,
    )
    learned_downbeat = evaluate_downbeat_times(
        learned_downbeats,
        song.downbeats_sec,
        skipped=beat_skip,
    )
    det_note = evaluate_note_predictions(deterministic_notes, truth_notes)
    learned_note = evaluate_note_predictions(
        learned_notes,
        truth_notes,
        skipped=pitch_skip,
    )

    return SongComparison(
        song_id=song.song_id,
        downbeat=DownbeatComparison(
            deterministic=det_downbeat,
            learned=learned_downbeat,
            winner=_winner(
                det_downbeat.f_measure_70ms,
                learned_downbeat.f_measure_70ms,
                learned_skipped=learned_downbeat.skipped is not None,
            ),
        ),
        note=NoteComparison(
            deterministic=det_note,
            learned=learned_note,
            winner=_winner(
                det_note.onset_pitch_f,
                learned_note.onset_pitch_f,
                learned_skipped=learned_note.skipped is not None,
            ),
        ),
    )


def _win_counts(results: list[SongComparison], area: str) -> dict[str, int]:
    counts = {"deterministic": 0, "learned": 0, "tie": 0}
    for result in results:
        comparison = result.downbeat if area == "downbeat" else result.note
        counts[comparison.winner] += 1
    return counts


def build_payload(results: list[SongComparison]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "songs": [asdict(result) for result in results],
        "summary": {
            "downbeat_wins": _win_counts(results, "downbeat"),
            "note_wins": _win_counts(results, "note"),
        },
    }


def write_report(payload: dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PER_SONG_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    generated_at = payload["generated_at"]
    for song in payload["songs"]:
        per_song_payload = {
            "schema_version": payload["schema_version"],
            "generated_at": generated_at,
            "song": song,
        }
        (PER_SONG_DIR / f"{song['song_id']}.json").write_text(
            json.dumps(per_song_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def render_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _format_metric(value: float | None, skipped: str | None = None) -> str:
    if skipped is not None:
        return "skipped"
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Learned vs deterministic validation",
        "",
        "| song_id | downbeat F det | downbeat F learned | downbeat winner | "
        "note onset+pitch F det | note onset+pitch F learned | note winner |",
        "|---|---:|---:|---|---:|---:|---|",
    ]
    for song in payload["songs"]:
        downbeat = song["downbeat"]
        note = song["note"]
        lines.append(
            f"| {song['song_id']} "
            f"| {_format_metric(downbeat['deterministic']['f_measure_70ms'])} "
            f"| {_format_metric(downbeat['learned']['f_measure_70ms'], downbeat['learned'].get('skipped'))} "
            f"| {downbeat['winner']} "
            f"| {_format_metric(note['deterministic']['onset_pitch_f'])} "
            f"| {_format_metric(note['learned']['onset_pitch_f'], note['learned'].get('skipped'))} "
            f"| {note['winner']} |"
        )
    lines.append("")
    lines.append("Summary:")
    lines.append(f"- downbeat wins: {payload['summary']['downbeat_wins']}")
    lines.append(f"- note wins: {payload['summary']['note_wins']}")
    return "\n".join(lines) + "\n"


def _select_songs(songs: list[TruthSong], song_id: str | None) -> list[TruthSong]:
    if song_id is None:
        return songs
    selected = [song for song in songs if song.song_id == song_id]
    if not selected:
        valid = ", ".join(song.song_id for song in songs)
        raise ValueError(f"unknown song id: {song_id}; valid ids: {valid}")
    return selected


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(
        description="Compare learned annotations against synthetic ground truth.",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    parser.add_argument("--song", help="evaluate a single song id")
    args = parser.parse_args(argv)

    try:
        songs = _select_songs(load_truth(), args.song)
    except ValueError as exc:
        parser.error(str(exc))

    results = [evaluate_song(song) for song in songs]
    payload = build_payload(results)
    write_report(payload)
    sys.stdout.write(render_json(payload) if args.json else render_markdown(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
