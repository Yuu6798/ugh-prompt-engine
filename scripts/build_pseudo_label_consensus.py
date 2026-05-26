"""Build pseudo-label consensus reports for local real-audio tracks.

This is a read-only harness for real audio without human ground truth. It
compares the current deterministic RPE outputs against optional learned-model
annotations and reports only machine-to-machine agreement. High agreement is a
triage signal, not validation accuracy, and this script never writes learned
output back into `PhysicalRPE` or `SemanticRPE`.

Usage:
    python scripts/build_pseudo_label_consensus.py real_audio_manifest.yaml
    python scripts/build_pseudo_label_consensus.py real_audio_manifest.yaml --json
    python scripts/build_pseudo_label_consensus.py real_audio_manifest.yaml --track song_id
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import mir_eval.util
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.measure_real_audio import (  # noqa: E402
    TrackSpec,
    _track_output_dirs,
    extract_learned_annotations,
    load_manifest,
)
from svp_rpe.io.audio_loader import load_audio  # noqa: E402
from svp_rpe.rpe.extractor import extract_rpe_from_file  # noqa: E402
from svp_rpe.rpe.models import (  # noqa: E402
    ChordEvent,
    LearnedAudioAnnotations,
    LearnedNoteEvent,
    MelodyContour,
)

SCHEMA_VERSION = "1.0"
DEFAULT_OUTPUT_DIR = ROOT / "examples" / "real_audio_validation" / "consensus"
PER_TRACK_DIRNAME = "per_track"
VALLEY_METHOD = "hybrid"

DOWNBEAT_F_WINDOW_SEC = 0.07
DOWNBEAT_HIT_WINDOW_SEC = 0.35
NOTE_ONSET_TOLERANCE_SEC = 0.05
NOTE_PITCH_TOLERANCE_MIDI = 0.5
MIN_NOTE_DURATION_SEC = 0.1

PITCH_CLASS_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
TRIAD_INTERVALS = {
    "major": (0, 4, 7),
    "minor": (0, 3, 7),
}


@dataclass(frozen=True)
class NoteEvent:
    start_sec: float
    end_sec: float
    pitch_midi: int


@dataclass(frozen=True)
class InferredChord:
    root: str
    quality: str
    start_sec: float
    end_sec: float
    confidence: float


@dataclass
class AgreementMetrics:
    deterministic_count: int
    learned_count: int
    matched_count: int
    precision: float
    recall: float
    f_measure: float
    mean_abs_error_sec: float | None = None
    mean_abs_cents: float | None = None
    confidence: float = 0.0
    skipped: str | None = None


@dataclass
class TrackConsensus:
    track_id: str
    audio_path: str
    status: str
    duration_sec: float | None
    bpm: float | None
    key: str | None
    mode: str | None
    time_signature: str | None
    learned_skipped: dict[str, str]
    downbeat: AgreementMetrics
    melody: AgreementMetrics
    chord: AgreementMetrics
    overall_confidence: float
    caveats: list[str]
    error: str | None = None


def _frequency_to_midi(frequency_hz: float) -> float:
    return 69.0 + 12.0 * float(np.log2(frequency_hz / 440.0))


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return slug.strip("._-") or "track"


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _f_measure(precision: float, recall: float) -> float:
    if precision <= 0.0 or recall <= 0.0:
        return 0.0
    return float(2.0 * precision * recall / (precision + recall))


def _skipped_metrics(
    reason: str,
    *,
    deterministic_count: int,
    learned_count: int = 0,
) -> AgreementMetrics:
    return AgreementMetrics(
        deterministic_count=deterministic_count,
        learned_count=learned_count,
        matched_count=0,
        precision=0.0,
        recall=0.0,
        f_measure=0.0,
        confidence=0.0,
        skipped=reason,
    )


def _match_times(
    deterministic: Iterable[float],
    learned: Iterable[float],
    *,
    window_sec: float,
) -> tuple[int, float | None]:
    reference = np.sort(np.array(list(deterministic), dtype=float))
    estimated = np.sort(np.array(list(learned), dtype=float))
    if reference.size == 0 or estimated.size == 0:
        return 0, None
    matches = mir_eval.util.match_events(reference, estimated, window_sec)
    errors = [abs(float(reference[ref_i] - estimated[est_i])) for ref_i, est_i in matches]
    mean_error = float(np.mean(errors)) if errors else None
    return len(matches), mean_error


def evaluate_downbeat_consensus(
    deterministic_downbeats: list[float],
    learned_annotations: LearnedAudioAnnotations,
    *,
    skipped: str | None,
) -> AgreementMetrics:
    learned_downbeats = [
        float(event.time_sec)
        for event in learned_annotations.time_events
        if event.event_type == "downbeat"
    ]
    if skipped is not None and not learned_downbeats:
        return _skipped_metrics(
            skipped,
            deterministic_count=len(deterministic_downbeats),
            learned_count=0,
        )
    if not deterministic_downbeats:
        return _skipped_metrics(
            "deterministic downbeat_times are empty",
            deterministic_count=0,
            learned_count=len(learned_downbeats),
        )
    if not learned_downbeats:
        return _skipped_metrics(
            "learned downbeat events are empty",
            deterministic_count=len(deterministic_downbeats),
            learned_count=0,
        )

    matched_70ms, _ = _match_times(
        deterministic_downbeats,
        learned_downbeats,
        window_sec=DOWNBEAT_F_WINDOW_SEC,
    )
    _, mean_error = _match_times(
        deterministic_downbeats,
        learned_downbeats,
        window_sec=DOWNBEAT_HIT_WINDOW_SEC,
    )
    precision = matched_70ms / float(len(learned_downbeats))
    recall = matched_70ms / float(len(deterministic_downbeats))
    f_measure = _f_measure(precision, recall)
    return AgreementMetrics(
        deterministic_count=len(deterministic_downbeats),
        learned_count=len(learned_downbeats),
        matched_count=matched_70ms,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f_measure=round(f_measure, 4),
        mean_abs_error_sec=_round(mean_error),
        confidence=round(f_measure, 4),
    )


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

    times = [float(time) for time in contour.times]
    frequencies = [float(frequency) for frequency in contour.frequencies_hz]
    voicing = [float(value) for value in contour.voicing]
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


def _match_notes(
    deterministic_notes: list[NoteEvent],
    learned_notes: list[NoteEvent],
) -> tuple[int, float | None]:
    candidates: list[tuple[float, int, int, float]] = []
    for det_i, det in enumerate(deterministic_notes):
        for learn_i, learned in enumerate(learned_notes):
            onset_error = abs(det.start_sec - learned.start_sec)
            pitch_error = abs(det.pitch_midi - learned.pitch_midi)
            if onset_error <= NOTE_ONSET_TOLERANCE_SEC and pitch_error <= NOTE_PITCH_TOLERANCE_MIDI:
                candidates.append((onset_error + pitch_error, det_i, learn_i, pitch_error))

    used_det: set[int] = set()
    used_learned: set[int] = set()
    cents: list[float] = []
    for _, det_i, learn_i, pitch_error in sorted(candidates):
        if det_i in used_det or learn_i in used_learned:
            continue
        used_det.add(det_i)
        used_learned.add(learn_i)
        cents.append(100.0 * pitch_error)

    mean_cents = float(np.mean(cents)) if cents else None
    return len(used_det), mean_cents


def evaluate_melody_consensus(
    contour: MelodyContour | None,
    learned_annotations: LearnedAudioAnnotations,
    *,
    skipped: str | None,
) -> AgreementMetrics:
    deterministic_notes = _bin_melody_contour_to_notes(contour)
    learned_notes = _learned_note_events_to_notes(learned_annotations.note_events)
    if skipped is not None and not learned_notes:
        return _skipped_metrics(
            skipped,
            deterministic_count=len(deterministic_notes),
            learned_count=0,
        )
    if not deterministic_notes:
        return _skipped_metrics(
            "deterministic melody_contour produced no note bins",
            deterministic_count=0,
            learned_count=len(learned_notes),
        )
    if not learned_notes:
        return _skipped_metrics(
            "learned note_events are empty",
            deterministic_count=len(deterministic_notes),
            learned_count=0,
        )

    matched, mean_cents = _match_notes(deterministic_notes, learned_notes)
    precision = matched / float(len(learned_notes))
    recall = matched / float(len(deterministic_notes))
    f_measure = _f_measure(precision, recall)
    return AgreementMetrics(
        deterministic_count=len(deterministic_notes),
        learned_count=len(learned_notes),
        matched_count=matched,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f_measure=round(f_measure, 4),
        mean_abs_cents=_round(mean_cents),
        confidence=round(f_measure, 4),
    )


def _pitch_class_name(pitch_midi: int) -> str:
    return PITCH_CLASS_NAMES[pitch_midi % 12]


def _parse_root(root: str) -> str | None:
    normalized = root.strip().replace("♯", "#").replace("♭", "b")
    flat_to_sharp = {
        "Db": "C#",
        "Eb": "D#",
        "Gb": "F#",
        "Ab": "G#",
        "Bb": "A#",
    }
    normalized = flat_to_sharp.get(normalized, normalized)
    return normalized if normalized in PITCH_CLASS_NAMES else None


def _overlap_seconds(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _infer_chord_from_notes(
    chord_event: ChordEvent,
    learned_notes: Iterable[LearnedNoteEvent],
) -> InferredChord | None:
    weights = {name: 0.0 for name in PITCH_CLASS_NAMES}
    for note in learned_notes:
        overlap = _overlap_seconds(
            chord_event.start_sec,
            chord_event.end_sec,
            float(note.start_sec),
            float(note.end_sec),
        )
        if overlap <= 0.0:
            continue
        weights[_pitch_class_name(int(note.pitch_midi))] += overlap * float(note.confidence)

    active_pcs = {pitch_class for pitch_class, weight in weights.items() if weight > 0.0}
    if len(active_pcs) < 2:
        return None

    best: tuple[float, str, str] | None = None
    for root_index, root in enumerate(PITCH_CLASS_NAMES):
        for quality, intervals in TRIAD_INTERVALS.items():
            triad = {PITCH_CLASS_NAMES[(root_index + interval) % 12] for interval in intervals}
            score = sum(weights[pitch_class] for pitch_class in triad)
            if best is None or score > best[0]:
                best = (score, root, quality)

    if best is None or best[0] <= 0.0:
        return None
    total_weight = sum(weights.values())
    confidence = best[0] / total_weight if total_weight > 0.0 else 0.0
    return InferredChord(
        root=best[1],
        quality=best[2],
        start_sec=float(chord_event.start_sec),
        end_sec=float(chord_event.end_sec),
        confidence=round(confidence, 4),
    )


def evaluate_chord_consensus(
    chord_events: list[ChordEvent],
    learned_annotations: LearnedAudioAnnotations,
    *,
    skipped: str | None,
) -> AgreementMetrics:
    learned_notes = learned_annotations.note_events
    if skipped is not None and not learned_notes:
        return _skipped_metrics(
            skipped,
            deterministic_count=len(chord_events),
            learned_count=0,
        )
    if not chord_events:
        return _skipped_metrics(
            "deterministic chord_events are empty",
            deterministic_count=0,
            learned_count=len(learned_notes),
        )
    if not learned_notes:
        return _skipped_metrics(
            "learned note_events are empty",
            deterministic_count=len(chord_events),
            learned_count=0,
        )

    inferred = [
        chord
        for chord_event in chord_events
        if (chord := _infer_chord_from_notes(chord_event, learned_notes)) is not None
    ]
    if not inferred:
        return _skipped_metrics(
            "learned note_events did not provide enough overlap to infer chords",
            deterministic_count=len(chord_events),
            learned_count=0,
        )

    matched = 0
    inferred_by_start = {round(chord.start_sec, 6): chord for chord in inferred}
    for chord_event in chord_events:
        inferred_chord = inferred_by_start.get(round(float(chord_event.start_sec), 6))
        if inferred_chord is None:
            continue
        root = _parse_root(chord_event.root)
        if root == inferred_chord.root and chord_event.quality == inferred_chord.quality:
            matched += 1

    precision = matched / float(len(inferred))
    recall = matched / float(len(chord_events))
    f_measure = _f_measure(precision, recall)
    return AgreementMetrics(
        deterministic_count=len(chord_events),
        learned_count=len(inferred),
        matched_count=matched,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f_measure=round(f_measure, 4),
        confidence=round(f_measure, 4),
    )


def _overall_confidence(metrics: Iterable[AgreementMetrics]) -> float:
    values = [metric.confidence for metric in metrics if metric.skipped is None]
    if not values:
        return 0.0
    return round(float(np.mean(values)), 4)


def evaluate_track(track: TrackSpec) -> TrackConsensus:
    audio = load_audio(str(track.audio_path))
    bundle = extract_rpe_from_file(
        str(track.audio_path),
        valley_method=VALLEY_METHOD,
        preloaded_audio=audio,
    )
    learned, learned_skipped = extract_learned_annotations(track, audio)
    learned = learned or LearnedAudioAnnotations()

    downbeat = evaluate_downbeat_consensus(
        bundle.physical.downbeat_times,
        learned,
        skipped=learned_skipped.get("beat_this"),
    )
    melody = evaluate_melody_consensus(
        bundle.physical.melody_contour,
        learned,
        skipped=learned_skipped.get("basic_pitch"),
    )
    chord = evaluate_chord_consensus(
        bundle.physical.chord_events,
        learned,
        skipped=learned_skipped.get("basic_pitch"),
    )
    caveats = [
        "machine consensus only; not human ground truth",
        "learned outputs are not written into PhysicalRPE or SemanticRPE",
    ]
    if chord.skipped is None:
        caveats.append("chord consensus is inferred from learned note events and is conservative")

    return TrackConsensus(
        track_id=track.track_id,
        audio_path=str(track.audio_path),
        status="ok",
        duration_sec=bundle.physical.duration_sec,
        bpm=bundle.physical.bpm,
        key=bundle.physical.key,
        mode=bundle.physical.mode,
        time_signature=bundle.physical.time_signature,
        learned_skipped=learned_skipped,
        downbeat=downbeat,
        melody=melody,
        chord=chord,
        overall_confidence=_overall_confidence((downbeat, melody, chord)),
        caveats=caveats,
    )


def _error_track(track: TrackSpec, error: Exception) -> TrackConsensus:
    skipped = _skipped_metrics(
        "track evaluation failed",
        deterministic_count=0,
        learned_count=0,
    )
    return TrackConsensus(
        track_id=track.track_id,
        audio_path=str(track.audio_path),
        status="error",
        duration_sec=None,
        bpm=None,
        key=None,
        mode=None,
        time_signature=None,
        learned_skipped={},
        downbeat=skipped,
        melody=skipped,
        chord=skipped,
        overall_confidence=0.0,
        caveats=["machine consensus only; not human ground truth"],
        error=f"{type(error).__name__}: {error}",
    )


def _metric_summary(tracks: list[TrackConsensus], metric_name: str) -> dict[str, Any]:
    metrics = [getattr(track, metric_name) for track in tracks if track.status == "ok"]
    observed = [metric for metric in metrics if metric.skipped is None]
    skipped = [metric for metric in metrics if metric.skipped is not None]
    return {
        "observed": len(observed),
        "skipped": len(skipped),
        "mean_confidence": _round(float(np.mean([m.confidence for m in observed]))) if observed else None,
    }


def build_payload(
    *,
    manifest_path: Path,
    output_dir: Path,
    tracks: list[TrackConsensus],
) -> dict[str, Any]:
    ok_tracks = [track for track in tracks if track.status == "ok"]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "manifest": str(manifest_path),
        "output_dir": str(output_dir),
        "truth_status": "pseudo_label_consensus_only",
        "tracks": [asdict(track) for track in tracks],
        "summary": {
            "total": len(tracks),
            "ok": len(ok_tracks),
            "error": sum(1 for track in tracks if track.status == "error"),
            "downbeat": _metric_summary(tracks, "downbeat"),
            "melody": _metric_summary(tracks, "melody"),
            "chord": _metric_summary(tracks, "chord"),
            "mean_overall_confidence": _round(
                float(np.mean([track.overall_confidence for track in ok_tracks]))
            )
            if ok_tracks
            else None,
        },
    }


def evaluate_manifest(
    manifest_path: Path,
    *,
    output_dir: Path,
    baseline: str,
    track_id: str | None = None,
) -> dict[str, Any]:
    tracks = load_manifest(manifest_path, default_baseline=baseline)
    if track_id is not None:
        tracks = [track for track in tracks if track.track_id == track_id]
        if not tracks:
            raise ValueError(f"track not found in manifest: {track_id}")
    _track_output_dirs(tracks)

    results: list[TrackConsensus] = []
    for track in tracks:
        try:
            results.append(evaluate_track(track))
        except Exception as exc:  # noqa: BLE001 - manual harness records per-track failures.
            results.append(_error_track(track, exc))

    return build_payload(
        manifest_path=manifest_path,
        output_dir=output_dir,
        tracks=results,
    )


def write_report(payload: dict[str, Any], output_dir: Path) -> None:
    per_track_dir = output_dir / PER_TRACK_DIRNAME
    per_track_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "summary.md").write_text(render_markdown(payload), encoding="utf-8")
    generated_at = payload["generated_at"]
    for track in payload["tracks"]:
        per_track_payload = {
            "schema_version": payload["schema_version"],
            "generated_at": generated_at,
            "truth_status": payload["truth_status"],
            "track": track,
        }
        filename = f"{_slug(track['track_id'])}.json"
        (per_track_dir / filename).write_text(
            json.dumps(per_track_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _format_metric(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f}"


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Pseudo-label consensus",
        "",
        "**Machine consensus only; not human ground truth.**",
        "",
        f"- manifest: `{payload['manifest']}`",
        f"- tracks: {payload['summary']['ok']} ok / {payload['summary']['error']} error",
        "",
        "| track_id | status | downbeat F | melody F | chord F | overall | caveat |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for track in payload["tracks"]:
        lines.append(
            "| {track_id} | {status} | {downbeat} | {melody} | {chord} | {overall} | {caveat} |".format(
                track_id=track["track_id"],
                status=track["status"],
                downbeat="skipped"
                if track["downbeat"]["skipped"]
                else _format_metric(track["downbeat"]["f_measure"]),
                melody="skipped"
                if track["melody"]["skipped"]
                else _format_metric(track["melody"]["f_measure"]),
                chord="skipped"
                if track["chord"]["skipped"]
                else _format_metric(track["chord"]["f_measure"]),
                overall=_format_metric(track["overall_confidence"]),
                caveat="; ".join(track["caveats"]),
            )
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build machine-only pseudo-label consensus reports for real audio.",
    )
    parser.add_argument("manifest", type=Path, help="YAML manifest with a `tracks` list")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for ignored consensus artifacts",
    )
    parser.add_argument("--baseline", default="pro", help="Default RPE baseline profile")
    parser.add_argument("--track", dest="track_id", help="Only evaluate one manifest track id")
    parser.add_argument("--json", action="store_true", help="Print summary JSON instead of markdown")
    args = parser.parse_args(argv)

    output_dir = args.output_dir.resolve()
    payload = evaluate_manifest(
        args.manifest.resolve(),
        output_dir=output_dir,
        baseline=args.baseline,
        track_id=args.track_id,
    )
    write_report(payload, output_dir)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(payload), end="")
    return 1 if payload["summary"]["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
