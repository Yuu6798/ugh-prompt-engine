"""scripts/validate_against_truth.py — Compare svp-rpe output against ground truth.

For every song in `examples/sample_input/ground_truth.yaml`, runs the RPE
extractor and scores BPM / key / section-boundaries against the recorded
truth using `mir_eval`; time_signature is checked by exact match against
ground_truth.yaml. Outputs either a markdown table (default) or a
machine-readable JSON document (`--json`).

`--check` enforces minimum-quality thresholds (BPM err <5, key score ≥0.5,
time_signature exact match, downbeat hit-rate ≥0.8, chord hit-rate ≥0.75,
segment F@3s ≥0.5) and exits 1 on any violation; without `--check` the script
reports the numbers and exits 0 so developers can inspect drift without
breaking a workflow.

Usage:
    python scripts/validate_against_truth.py            # markdown table to stdout
    python scripts/validate_against_truth.py --json     # JSON to stdout
    python scripts/validate_against_truth.py --check    # enforce thresholds, exit 1 on miss
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

import mir_eval.key
import mir_eval.segment
import mir_eval.tempo

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from svp_rpe.eval.scorer_rpe import score_rpe  # noqa: E402
from svp_rpe.rpe.extractor import extract_rpe_from_file  # noqa: E402
from svp_rpe.rpe.models import PhysicalRPE  # noqa: E402

SAMPLE_DIR = ROOT / "examples" / "sample_input"
GROUND_TRUTH = SAMPLE_DIR / "ground_truth.yaml"
VALLEY_METHOD = "hybrid"

# Thresholds for --check (kept conservative; tunable per Q0-5 evidence)
BPM_MAX_ABS_DIFF = 5.0
KEY_MIN_SCORE = 0.5
SEGMENT_F_MIN_AT_3S = 0.5
TIME_SIGNATURE_REQUIRE_MATCH = True
DOWNBEAT_WINDOW_SEC = 0.35
DOWNBEAT_HIT_RATE_MIN = 0.8
CHORD_EVENT_HIT_RATE_MIN = 0.75
MELODY_PITCH_ACCURACY_MIN = 0.80
MELODY_VOICING_RECALL_MIN = 0.50
MELODY_CENTS_TOLERANCE = 50.0


@dataclass
class TruthSong:
    song_id: str
    audio_path: Path
    bpm: float
    key: str
    mode: str
    baseline_profile: str
    time_signature: str
    downbeats_sec: list[float]
    chord_events: list[dict[str, Any]]
    melody_events: list[dict[str, Any]]
    sections: list[tuple[float, float]]


@dataclass
class BPMResult:
    estimated: float | None
    reference: float
    abs_diff: float | None
    p_score: float


@dataclass
class KeyResult:
    estimated: str | None
    reference: str
    weighted_score: float


@dataclass
class TimeSignatureResult:
    estimated: str
    reference: str
    confidence: float
    match: bool


@dataclass
class DownbeatResult:
    n_reference: int
    n_estimated: int
    hit_rate: float
    mean_abs_error_sec: float | None
    window_sec: float


@dataclass
class ChordResult:
    n_reference: int
    n_estimated: int
    event_hit_rate: float
    unique_reference: list[str]
    unique_matched: list[str]


@dataclass
class MelodyResult:
    n_reference_frames: int
    n_voiced_frames: int
    voicing_recall: float
    pitch_accuracy_50c: float
    mean_abs_cents: float | None


@dataclass
class SegmentResult:
    n_reference: int
    n_estimated: int
    f_at_0_5s: float
    p_at_0_5s: float
    r_at_0_5s: float
    f_at_3_0s: float
    p_at_3_0s: float
    r_at_3_0s: float


@dataclass
class BaselineScoreResult:
    profile: str
    overall: float
    rms_score: float
    active_rate_score: float
    crest_factor_score: float
    valley_score: float
    thickness_score: float


@dataclass
class SongValidation:
    song_id: str
    bpm: BPMResult
    key: KeyResult
    time_signature: TimeSignatureResult
    downbeats: DownbeatResult
    chords: ChordResult
    melody: MelodyResult
    segments: SegmentResult
    baseline_score: BaselineScoreResult
    passes_thresholds: bool
    threshold_failures: list[str]


def load_truth() -> list[TruthSong]:
    raw = yaml.safe_load(GROUND_TRUTH.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"unexpected ground_truth.yaml structure: {type(raw)}")

    songs: list[TruthSong] = []
    for entry in raw:
        audio_path = SAMPLE_DIR / entry["filename"]
        if not audio_path.is_file():
            raise FileNotFoundError(f"missing sample WAV: {audio_path}")
        sections = [
            (float(sec["start_sec"]), float(sec["end_sec"]))
            for sec in entry["sections"]
        ]
        songs.append(
            TruthSong(
                song_id=entry["id"],
                audio_path=audio_path,
                bpm=float(entry["bpm"]),
                key=str(entry["key"]),
                mode=str(entry["mode"]),
                baseline_profile=str(entry.get("baseline_profile", "pro")),
                time_signature=str(entry["time_signature"]),
                downbeats_sec=[float(t) for t in entry.get("downbeats_sec", [])],
                chord_events=list(entry.get("chord_events", [])),
                melody_events=list(entry.get("melody_events", [])),
                sections=sections,
            )
        )
    return songs


def _format_key(key: str | None, mode: str | None) -> str | None:
    if not key or not mode:
        return None
    return f"{key} {mode}"


def evaluate_bpm(phys: PhysicalRPE, gt_bpm: float) -> BPMResult:
    est = phys.bpm
    if est is None:
        return BPMResult(estimated=None, reference=gt_bpm, abs_diff=None, p_score=0.0)
    ref_arr = np.array([gt_bpm, 0.0])
    est_arr = np.array([est, 0.0])
    p_score, _, _ = mir_eval.tempo.detection(ref_arr, 1.0, est_arr)
    return BPMResult(
        estimated=est,
        reference=gt_bpm,
        abs_diff=abs(est - gt_bpm),
        p_score=float(p_score),
    )


def evaluate_key(phys: PhysicalRPE, gt_key: str, gt_mode: str) -> KeyResult:
    ref_str = f"{gt_key} {gt_mode}"
    est_str = _format_key(phys.key, phys.mode)

    if est_str is None:
        return KeyResult(estimated=None, reference=ref_str, weighted_score=0.0)

    try:
        scores = mir_eval.key.evaluate(ref_str, est_str)
        weighted = float(scores["Weighted Score"])
    except ValueError:
        weighted = 0.0
    return KeyResult(estimated=est_str, reference=ref_str, weighted_score=weighted)


def evaluate_time_signature(phys: PhysicalRPE, gt_time_signature: str) -> TimeSignatureResult:
    est = phys.time_signature
    return TimeSignatureResult(
        estimated=est,
        reference=gt_time_signature,
        confidence=phys.time_signature_confidence,
        match=est == gt_time_signature,
    )


def evaluate_downbeats(
    phys: PhysicalRPE,
    gt_downbeats: list[float],
    *,
    window_sec: float = DOWNBEAT_WINDOW_SEC,
) -> DownbeatResult:
    ref = np.array(gt_downbeats, dtype=float)
    est_all = np.array(phys.downbeat_times, dtype=float)

    if ref.size == 0:
        return DownbeatResult(
            n_reference=0,
            n_estimated=int(est_all.size),
            hit_rate=0.0,
            mean_abs_error_sec=None,
            window_sec=window_sec,
        )

    if est_all.size == 0:
        return DownbeatResult(
            n_reference=int(ref.size),
            n_estimated=0,
            hit_rate=0.0,
            mean_abs_error_sec=None,
            window_sec=window_sec,
        )

    lo = float(np.min(ref)) - window_sec
    hi = float(np.max(ref)) + window_sec
    est = est_all[(est_all >= lo) & (est_all <= hi)]
    if est.size == 0:
        return DownbeatResult(
            n_reference=int(ref.size),
            n_estimated=0,
            hit_rate=0.0,
            mean_abs_error_sec=None,
            window_sec=window_sec,
        )

    used: set[int] = set()
    errors: list[float] = []
    for ref_time in ref:
        idx = int(np.argmin(np.abs(est - ref_time)))
        err = abs(float(est[idx] - ref_time))
        if err <= window_sec and idx not in used:
            used.add(idx)
            errors.append(err)

    return DownbeatResult(
        n_reference=int(ref.size),
        n_estimated=int(est.size),
        hit_rate=round(len(errors) / float(ref.size), 4),
        mean_abs_error_sec=round(float(np.mean(errors)), 4) if errors else None,
        window_sec=window_sec,
    )


def _event_overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def evaluate_chords(
    phys: PhysicalRPE,
    gt_chord_events: list[dict[str, Any]],
) -> ChordResult:
    ref_events = [
        event
        for event in gt_chord_events
        if "chord" in event and "start_sec" in event and "end_sec" in event
    ]
    if not ref_events:
        return ChordResult(
            n_reference=0,
            n_estimated=len(phys.chord_events),
            event_hit_rate=0.0,
            unique_reference=[],
            unique_matched=[],
        )

    matched_labels: list[str] = []
    for ref in ref_events:
        ref_start = float(ref["start_sec"])
        ref_end = float(ref["end_sec"])
        candidates = [
            pred
            for pred in phys.chord_events
            if _event_overlap(ref_start, ref_end, pred.start_sec, pred.end_sec) > 0.0
        ]
        if not candidates:
            continue
        best = max(
            candidates,
            key=lambda pred: _event_overlap(ref_start, ref_end, pred.start_sec, pred.end_sec),
        )
        if best.chord == str(ref["chord"]):
            matched_labels.append(best.chord)

    unique_reference = sorted({str(event["chord"]) for event in ref_events})
    unique_matched = sorted(set(matched_labels))
    return ChordResult(
        n_reference=len(ref_events),
        n_estimated=len(phys.chord_events),
        event_hit_rate=round(len(matched_labels) / len(ref_events), 4),
        unique_reference=unique_reference,
        unique_matched=unique_matched,
    )


def _melody_frequency_at(
    melody_events: list[dict[str, Any]],
    time_sec: float,
) -> float | None:
    for event in melody_events:
        if "frequency_hz" not in event:
            continue
        start = float(event["start_sec"])
        end = float(event["end_sec"])
        if start <= time_sec < end:
            return float(event["frequency_hz"])
    return None


def evaluate_melody(
    phys: PhysicalRPE,
    gt_melody_events: list[dict[str, Any]],
) -> MelodyResult:
    contour = phys.melody_contour
    if contour is None or not gt_melody_events:
        return MelodyResult(
            n_reference_frames=0,
            n_voiced_frames=0,
            voicing_recall=0.0,
            pitch_accuracy_50c=0.0,
            mean_abs_cents=None,
        )

    hits = 0
    reference_frames = 0
    voiced_frames = 0
    cents_errors: list[float] = []
    for time_sec, freq_hz, voicing in zip(
        contour.times,
        contour.frequencies_hz,
        contour.voicing,
    ):
        expected_hz = _melody_frequency_at(gt_melody_events, float(time_sec))
        if expected_hz is None:
            continue
        reference_frames += 1
        if float(voicing) < 0.5 or float(freq_hz) <= 0.0:
            continue
        voiced_frames += 1
        cents = abs(1200.0 * float(np.log2(float(freq_hz) / expected_hz)))
        cents_errors.append(cents)
        if cents <= MELODY_CENTS_TOLERANCE:
            hits += 1

    return MelodyResult(
        n_reference_frames=reference_frames,
        n_voiced_frames=voiced_frames,
        voicing_recall=round(voiced_frames / reference_frames, 4) if reference_frames else 0.0,
        pitch_accuracy_50c=round(hits / voiced_frames, 4) if voiced_frames else 0.0,
        mean_abs_cents=round(float(np.mean(cents_errors)), 4) if cents_errors else None,
    )


def evaluate_segments(
    phys: PhysicalRPE, gt_sections: list[tuple[float, float]]
) -> SegmentResult:
    ref = np.array(gt_sections, dtype=float)
    est_pairs = [(float(s.start_sec), float(s.end_sec)) for s in phys.structure]
    est = np.array(est_pairs, dtype=float) if est_pairs else np.zeros((0, 2))

    if est.shape[0] == 0:
        return SegmentResult(
            n_reference=int(ref.shape[0]),
            n_estimated=0,
            f_at_0_5s=0.0,
            p_at_0_5s=0.0,
            r_at_0_5s=0.0,
            f_at_3_0s=0.0,
            p_at_3_0s=0.0,
            r_at_3_0s=0.0,
        )

    p05, r05, f05 = mir_eval.segment.detection(ref, est, window=0.5)
    p3, r3, f3 = mir_eval.segment.detection(ref, est, window=3.0)
    return SegmentResult(
        n_reference=int(ref.shape[0]),
        n_estimated=int(est.shape[0]),
        f_at_0_5s=float(f05),
        p_at_0_5s=float(p05),
        r_at_0_5s=float(r05),
        f_at_3_0s=float(f3),
        p_at_3_0s=float(p3),
        r_at_3_0s=float(r3),
    )


def evaluate_baseline_score(phys: PhysicalRPE, baseline_profile: str) -> BaselineScoreResult:
    score = score_rpe(phys, baseline=baseline_profile)
    return BaselineScoreResult(
        profile=score.baseline_profile,
        overall=score.overall,
        rms_score=score.rms_score,
        active_rate_score=score.active_rate_score,
        crest_factor_score=score.crest_factor_score,
        valley_score=score.valley_score,
        thickness_score=score.thickness_score,
    )


def evaluate_song(song: TruthSong) -> SongValidation:
    rpe = extract_rpe_from_file(str(song.audio_path), valley_method=VALLEY_METHOD)
    phys = rpe.physical

    bpm_result = evaluate_bpm(phys, song.bpm)
    key_result = evaluate_key(phys, song.key, song.mode)
    time_signature_result = evaluate_time_signature(phys, song.time_signature)
    downbeat_result = evaluate_downbeats(phys, song.downbeats_sec)
    chord_result = evaluate_chords(phys, song.chord_events)
    melody_result = evaluate_melody(phys, song.melody_events)
    seg_result = evaluate_segments(phys, song.sections)
    baseline_result = evaluate_baseline_score(phys, song.baseline_profile)

    failures: list[str] = []
    if bpm_result.abs_diff is None or bpm_result.abs_diff >= BPM_MAX_ABS_DIFF:
        failures.append(f"BPM diff {bpm_result.abs_diff} >= {BPM_MAX_ABS_DIFF}")
    if key_result.weighted_score < KEY_MIN_SCORE:
        failures.append(f"Key score {key_result.weighted_score:.3f} < {KEY_MIN_SCORE}")
    if TIME_SIGNATURE_REQUIRE_MATCH and not time_signature_result.match:
        failures.append(
            f"Time signature {time_signature_result.estimated} != "
            f"{time_signature_result.reference}"
        )
    if downbeat_result.hit_rate < DOWNBEAT_HIT_RATE_MIN:
        failures.append(
            f"Downbeat hit-rate {downbeat_result.hit_rate:.3f} < "
            f"{DOWNBEAT_HIT_RATE_MIN}"
        )
    if chord_result.event_hit_rate < CHORD_EVENT_HIT_RATE_MIN:
        failures.append(
            f"Chord hit-rate {chord_result.event_hit_rate:.3f} < "
            f"{CHORD_EVENT_HIT_RATE_MIN}"
        )
    if melody_result.voicing_recall < MELODY_VOICING_RECALL_MIN:
        failures.append(
            f"Melody voicing recall {melody_result.voicing_recall:.3f} < "
            f"{MELODY_VOICING_RECALL_MIN}"
        )
    if melody_result.pitch_accuracy_50c < MELODY_PITCH_ACCURACY_MIN:
        failures.append(
            f"Melody pitch accuracy {melody_result.pitch_accuracy_50c:.3f} < "
            f"{MELODY_PITCH_ACCURACY_MIN}"
        )
    if seg_result.f_at_3_0s < SEGMENT_F_MIN_AT_3S:
        failures.append(f"Segment F@3s {seg_result.f_at_3_0s:.3f} < {SEGMENT_F_MIN_AT_3S}")

    return SongValidation(
        song_id=song.song_id,
        bpm=bpm_result,
        key=key_result,
        time_signature=time_signature_result,
        downbeats=downbeat_result,
        chords=chord_result,
        melody=melody_result,
        segments=seg_result,
        baseline_score=baseline_result,
        passes_thresholds=not failures,
        threshold_failures=failures,
    )


def render_markdown(results: list[SongValidation]) -> str:
    lines: list[str] = []
    lines.append("# Validation against ground truth\n")
    lines.append("| song_id | BPM est / ref / Δ | tempo p | key est / ref / score | "
                 "meter est / ref / conf | downbeat hit | chord hit | "
                 "melody acc | melody recall | "
                 "seg F@0.5s | seg F@3s | "
                 "baseline / score | check |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        bpm_diff = "n/a" if r.bpm.abs_diff is None else f"{r.bpm.abs_diff:.2f}"
        bpm_est = "n/a" if r.bpm.estimated is None else f"{r.bpm.estimated:.2f}"
        key_est = r.key.estimated or "n/a"
        check = "pass" if r.passes_thresholds else "fail"
        lines.append(
            f"| {r.song_id} "
            f"| {bpm_est} / {r.bpm.reference:.2f} / {bpm_diff} "
            f"| {r.bpm.p_score:.2f} "
            f"| {key_est} / {r.key.reference} / {r.key.weighted_score:.2f} "
            f"| {r.time_signature.estimated} / {r.time_signature.reference} / "
            f"{r.time_signature.confidence:.2f} "
            f"| {r.downbeats.hit_rate:.2f} "
            f"| {r.chords.event_hit_rate:.2f} "
            f"| {r.melody.pitch_accuracy_50c:.2f} "
            f"| {r.melody.voicing_recall:.2f} "
            f"| {r.segments.f_at_0_5s:.2f} "
            f"| {r.segments.f_at_3_0s:.2f} "
            f"| {r.baseline_score.profile} / {r.baseline_score.overall:.2f} "
            f"| {check} |"
        )
    failed = [r for r in results if not r.passes_thresholds]
    if failed:
        lines.append("")
        lines.append("## Threshold failures")
        for r in failed:
            lines.append(f"- **{r.song_id}**: {'; '.join(r.threshold_failures)}")
    return "\n".join(lines) + "\n"


def render_json(results: list[SongValidation]) -> str:
    payload: dict[str, Any] = {
        "thresholds": {
            "bpm_max_abs_diff": BPM_MAX_ABS_DIFF,
            "key_min_score": KEY_MIN_SCORE,
            "segment_f_min_at_3s": SEGMENT_F_MIN_AT_3S,
            "time_signature_require_match": TIME_SIGNATURE_REQUIRE_MATCH,
            "downbeat_window_sec": DOWNBEAT_WINDOW_SEC,
            "downbeat_hit_rate_min": DOWNBEAT_HIT_RATE_MIN,
            "chord_event_hit_rate_min": CHORD_EVENT_HIT_RATE_MIN,
            "melody_pitch_accuracy_min": MELODY_PITCH_ACCURACY_MIN,
            "melody_voicing_recall_min": MELODY_VOICING_RECALL_MIN,
            "melody_cents_tolerance": MELODY_CENTS_TOLERANCE,
        },
        "songs": [asdict(r) for r in results],
        "summary": {
            "total": len(results),
            "passing": sum(1 for r in results if r.passes_thresholds),
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _configure_stdio() -> None:
    """Use UTF-8 for emoji/status output on Windows terminals when possible."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(
        description="Validate svp-rpe output against ground_truth.yaml using mir_eval.",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if any song misses minimum thresholds",
    )
    args = parser.parse_args(argv)

    songs = load_truth()
    results = [evaluate_song(s) for s in songs]

    output = render_json(results) if args.json else render_markdown(results)
    sys.stdout.write(output)

    if args.check:
        failed = [r for r in results if not r.passes_thresholds]
        if failed:
            print(
                f"[validate] {len(failed)} song(s) below threshold; exiting 1",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
