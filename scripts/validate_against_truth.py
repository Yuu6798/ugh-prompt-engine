"""scripts/validate_against_truth.py — Compare svp-rpe output against ground truth.

For every song in `examples/sample_input/ground_truth.yaml`, runs the RPE
extractor and scores BPM / key / section-boundaries against the recorded
truth using `mir_eval`. Outputs either a markdown table (default) or a
machine-readable JSON document (`--json`).

`--check` enforces minimum-quality thresholds (BPM err <5, key score ≥0.5,
segment F@3s ≥0.5) and exits 1 on any violation; without `--check` the
script reports the numbers and exits 0 so developers can inspect drift
without breaking a workflow.

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

from svp_rpe.eval.scorer_rpe import score_rpe
from svp_rpe.rpe.extractor import extract_rpe_from_file
from svp_rpe.rpe.models import PhysicalRPE

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "examples" / "sample_input"
GROUND_TRUTH = SAMPLE_DIR / "ground_truth.yaml"
VALLEY_METHOD = "hybrid"

# Thresholds for --check (kept conservative; tunable per Q0-5 evidence)
BPM_MAX_ABS_DIFF = 5.0
KEY_MIN_SCORE = 0.5
SEGMENT_F_MIN_AT_3S = 0.5


@dataclass
class TruthSong:
    song_id: str
    audio_path: Path
    bpm: float
    key: str
    mode: str
    baseline_profile: str
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
    seg_result = evaluate_segments(phys, song.sections)
    baseline_result = evaluate_baseline_score(phys, song.baseline_profile)

    failures: list[str] = []
    if bpm_result.abs_diff is None or bpm_result.abs_diff >= BPM_MAX_ABS_DIFF:
        failures.append(f"BPM diff {bpm_result.abs_diff} >= {BPM_MAX_ABS_DIFF}")
    if key_result.weighted_score < KEY_MIN_SCORE:
        failures.append(f"Key score {key_result.weighted_score:.3f} < {KEY_MIN_SCORE}")
    if seg_result.f_at_3_0s < SEGMENT_F_MIN_AT_3S:
        failures.append(f"Segment F@3s {seg_result.f_at_3_0s:.3f} < {SEGMENT_F_MIN_AT_3S}")

    return SongValidation(
        song_id=song.song_id,
        bpm=bpm_result,
        key=key_result,
        segments=seg_result,
        baseline_score=baseline_result,
        passes_thresholds=not failures,
        threshold_failures=failures,
    )


def render_markdown(results: list[SongValidation]) -> str:
    lines: list[str] = []
    lines.append("# Validation against ground truth\n")
    lines.append("| song_id | BPM est / ref / delta | tempo p | key est / ref / score | "
                 "seg F@0.5s | seg F@3s | baseline / score | check |")
    lines.append("|---|---|---|---|---|---|---|---|")
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
        },
        "songs": [asdict(r) for r in results],
        "summary": {
            "total": len(results),
            "passing": sum(1 for r in results if r.passes_thresholds),
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
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
