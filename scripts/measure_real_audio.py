"""Run the deterministic pipeline over a local real-audio manifest.

This is a manual validation harness. It does not commit audio files or generated
reports; outputs are written under `examples/real_audio_validation/runs/`,
which is ignored by git.

Usage:
    python scripts/measure_real_audio.py examples/real_audio_validation/manifest.example.yaml
    python scripts/measure_real_audio.py real_audio_manifest.yaml --json
    python scripts/measure_real_audio.py real_audio_manifest.yaml --learned
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from svp_rpe.eval.scorer_integrated import score_integrated  # noqa: E402
from svp_rpe.eval.scorer_rpe import score_rpe  # noqa: E402
from svp_rpe.eval.scorer_ugher import score_ugher  # noqa: E402
from svp_rpe.io.audio_loader import AudioData, load_audio  # noqa: E402
from svp_rpe.io.source_separator import DEFAULT_MODEL, separate_stems  # noqa: E402
from svp_rpe.rpe.extractor import extract_rpe, extract_rpe_from_file  # noqa: E402
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
from svp_rpe.rpe.models import LearnedAudioAnnotations  # noqa: E402
from svp_rpe.svp.generator import generate_svp  # noqa: E402
from svp_rpe.svp.render_yaml import render_yaml  # noqa: E402

SCHEMA_VERSION = "1.0"
DEFAULT_OUTPUT_DIR = ROOT / "examples" / "real_audio_validation" / "runs"
ARTEFACT_FILES = ("rpe.json", "svp.yaml", "evaluation.json")


@dataclass
class TrackSpec:
    track_id: str
    audio_path: Path
    baseline: str = "pro"
    notes: str | None = None


@dataclass
class TrackSummary:
    track_id: str
    audio_path: str
    baseline: str
    status: str
    output_dir: str
    duration_sec: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    bpm: float | None = None
    key: str | None = None
    mode: str | None = None
    time_signature: str | None = None
    section_count: int | None = None
    rpe_score: float | None = None
    ugher_score: float | None = None
    integrated_score: float | None = None
    learned_enabled_models: list[str] = field(default_factory=list)
    learned_skipped: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return slug.strip("._-") or "track"


def _track_output_dirs(tracks: list[TrackSpec]) -> dict[str, str]:
    """Return {track_id: slug} and fail before writes if normalized IDs collide."""
    slug_to_id: dict[str, str] = {}
    track_dirs: dict[str, str] = {}
    for track in tracks:
        slug = _slug(track.track_id)
        previous = slug_to_id.get(slug)
        if previous is not None:
            raise ValueError(
                "track ids produce the same output directory "
                f"`{slug}`: `{previous}` and `{track.track_id}`"
            )
        slug_to_id[slug] = track.track_id
        track_dirs[track.track_id] = slug
    return track_dirs


def _resolve_audio_path(raw_path: str, manifest_path: Path) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = manifest_path.parent / path
    return path.resolve()


def load_manifest(manifest_path: Path, *, default_baseline: str) -> list[TrackSpec]:
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"manifest must be a mapping: {manifest_path}")
    entries = raw.get("tracks")
    if not isinstance(entries, list):
        raise ValueError("manifest must contain a `tracks` list")

    tracks: list[TrackSpec] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"tracks[{index}] must be a mapping")
        track_id = str(entry.get("id") or "").strip()
        raw_path = str(entry.get("path") or "").strip()
        if not track_id:
            raise ValueError(f"tracks[{index}] is missing required `id`")
        if not raw_path:
            raise ValueError(f"tracks[{index}] is missing required `path`")
        audio_path = _resolve_audio_path(raw_path, manifest_path)
        if not audio_path.is_file():
            raise FileNotFoundError(f"missing audio for track `{track_id}`: {audio_path}")
        tracks.append(
            TrackSpec(
                track_id=track_id,
                audio_path=audio_path,
                baseline=str(entry.get("baseline") or default_baseline),
                notes=entry.get("notes"),
            )
        )
    return tracks


def _merge_learned_annotations(
    annotations: list[LearnedAudioAnnotations],
) -> LearnedAudioAnnotations | None:
    if not annotations:
        return None
    merged = LearnedAudioAnnotations()
    inference_config: dict[str, Any] = {}
    license_metadata: dict[str, str] = {}
    for annotation in annotations:
        merged.enabled_models.extend(annotation.enabled_models)
        merged.labels.extend(annotation.labels)
        if annotation.embedding is not None:
            merged.embedding = annotation.embedding
        merged.time_events.extend(annotation.time_events)
        merged.note_events.extend(annotation.note_events)
        inference_config.update(annotation.inference_config)
        license_metadata.update(annotation.license_metadata)
    merged.inference_config = inference_config
    merged.license_metadata = license_metadata
    return merged


def extract_learned_annotations(
    track: TrackSpec,
    audio: AudioData,
) -> tuple[LearnedAudioAnnotations | None, dict[str, str]]:
    annotations: list[LearnedAudioAnnotations] = []
    skipped: dict[str, str] = {}

    try:
        annotations.append(extract_beat_this_annotations(audio.y_mono, audio.sr))
    except LearnedModelUnavailable as exc:
        skipped["beat_this"] = str(exc)

    try:
        annotations.append(extract_basic_pitch_annotations(track.audio_path))
    except LearnedModelUnavailable as exc:
        skipped["basic_pitch"] = str(exc)

    return _merge_learned_annotations(annotations), skipped


def render_track_outputs(
    track: TrackSpec,
    *,
    valley_method: str,
    include_stems: bool,
    separation_model: str,
    separation_device: str,
    include_learned: bool,
) -> tuple[dict[str, str], TrackSummary]:
    if include_stems:
        stem_bundle = separate_stems(
            track.audio_path,
            model=separation_model,
            device=separation_device,
        )
        audio = load_audio(str(track.audio_path), target_sr=stem_bundle.sample_rate)
        rpe_bundle = extract_rpe(audio, valley_method=valley_method, stem_bundle=stem_bundle)
    else:
        audio = load_audio(str(track.audio_path))
        rpe_bundle = extract_rpe_from_file(
            str(track.audio_path),
            valley_method=valley_method,
            preloaded_audio=audio,
        )

    learned_skipped: dict[str, str] = {}
    if include_learned:
        learned, learned_skipped = extract_learned_annotations(track, audio)
        if learned is not None:
            rpe_bundle = attach_learned_annotations(rpe_bundle, learned)

    svp_bundle = generate_svp(rpe_bundle)
    rpe_score = score_rpe(rpe_bundle.physical, baseline=track.baseline)
    ugher_score = score_ugher(rpe_bundle, svp_bundle)
    integrated = score_integrated(ugher_score, rpe_score)

    outputs = {
        "rpe.json": json.dumps(rpe_bundle.model_dump(), ensure_ascii=False, indent=2),
        "svp.yaml": render_yaml(svp_bundle),
        "evaluation.json": json.dumps(
            {
                "rpe_score": rpe_score.model_dump(),
                "ugher_score": ugher_score.model_dump(),
                "integrated_score": integrated.model_dump(),
            },
            ensure_ascii=False,
            indent=2,
        ),
    }
    learned_models = [
        model.name
        for model in (
            rpe_bundle.learned_annotations.enabled_models
            if rpe_bundle.learned_annotations is not None
            else []
        )
    ]
    summary = TrackSummary(
        track_id=track.track_id,
        audio_path=str(track.audio_path),
        baseline=track.baseline,
        status="ok",
        output_dir="",
        duration_sec=rpe_bundle.physical.duration_sec,
        sample_rate=rpe_bundle.physical.sample_rate,
        channels=rpe_bundle.audio_channels,
        bpm=rpe_bundle.physical.bpm,
        key=rpe_bundle.physical.key,
        mode=rpe_bundle.physical.mode,
        time_signature=rpe_bundle.physical.time_signature,
        section_count=len(rpe_bundle.physical.structure),
        rpe_score=rpe_score.overall,
        ugher_score=ugher_score.overall,
        integrated_score=integrated.integrated_score,
        learned_enabled_models=learned_models,
        learned_skipped=learned_skipped,
    )
    return outputs, summary


def write_track_outputs(track_dir: Path, outputs: dict[str, str]) -> None:
    track_dir.mkdir(parents=True, exist_ok=True)
    for filename in ARTEFACT_FILES:
        (track_dir / filename).write_text(outputs[filename], encoding="utf-8")


def measure_tracks(
    tracks: list[TrackSpec],
    *,
    run_dir: Path,
    valley_method: str,
    include_stems: bool,
    separation_model: str,
    separation_device: str,
    include_learned: bool,
) -> list[TrackSummary]:
    summaries: list[TrackSummary] = []
    track_dirs = _track_output_dirs(tracks)
    for track in tracks:
        track_dir = run_dir / track_dirs[track.track_id]
        try:
            outputs, summary = render_track_outputs(
                track,
                valley_method=valley_method,
                include_stems=include_stems,
                separation_model=separation_model,
                separation_device=separation_device,
                include_learned=include_learned,
            )
            write_track_outputs(track_dir, outputs)
            summary.output_dir = str(track_dir)
        except Exception as exc:  # noqa: BLE001 - manual harness records failures per track.
            summary = TrackSummary(
                track_id=track.track_id,
                audio_path=str(track.audio_path),
                baseline=track.baseline,
                status="error",
                output_dir=str(track_dir),
                error=f"{type(exc).__name__}: {exc}",
            )
        summaries.append(summary)
    return summaries


def build_report(
    *,
    manifest_path: Path,
    run_id: str,
    run_dir: Path,
    tracks: list[TrackSummary],
    include_learned: bool,
    include_stems: bool,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest": str(manifest_path),
        "run_id": run_id,
        "run_dir": str(run_dir),
        "include_learned": include_learned,
        "include_stems": include_stems,
        "tracks": [asdict(track) for track in tracks],
        "summary": {
            "total": len(tracks),
            "ok": sum(1 for track in tracks if track.status == "ok"),
            "error": sum(1 for track in tracks if track.status == "error"),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Real-Audio Measurement",
        "",
        f"- manifest: `{report['manifest']}`",
        f"- run_dir: `{report['run_dir']}`",
        f"- tracks: {report['summary']['ok']} ok / {report['summary']['error']} error",
        "",
        "| track_id | status | bpm | key | meter | RPE | UGHer | integrated |",
        "|---|---|---:|---|---|---:|---:|---:|",
    ]
    for track in report["tracks"]:
        key = " ".join(
            part for part in [track.get("key"), track.get("mode")] if part
        ) or "n/a"
        lines.append(
            "| {track_id} | {status} | {bpm} | {key} | {meter} | {rpe} | {ugher} | {integrated} |".format(
                track_id=track["track_id"],
                status=track["status"],
                bpm=_format_optional_float(track.get("bpm")),
                key=key,
                meter=track.get("time_signature") or "n/a",
                rpe=_format_optional_float(track.get("rpe_score")),
                ugher=_format_optional_float(track.get("ugher_score")),
                integrated=_format_optional_float(track.get("integrated_score")),
            )
        )
    return "\n".join(lines) + "\n"


def _format_optional_float(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.4f}"


def run_manifest(
    manifest_path: Path,
    *,
    output_dir: Path,
    run_id: str,
    valley_method: str,
    baseline: str,
    include_stems: bool,
    separation_model: str,
    separation_device: str,
    include_learned: bool,
) -> dict[str, Any]:
    tracks = load_manifest(manifest_path, default_baseline=baseline)
    _track_output_dirs(tracks)
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    summaries = measure_tracks(
        tracks,
        run_dir=run_dir,
        valley_method=valley_method,
        include_stems=include_stems,
        separation_model=separation_model,
        separation_device=separation_device,
        include_learned=include_learned,
    )
    report = build_report(
        manifest_path=manifest_path,
        run_id=run_id,
        run_dir=run_dir,
        tracks=summaries,
        include_learned=include_learned,
        include_stems=include_stems,
    )
    (run_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "summary.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def _default_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run deterministic SVP/RPE measurement over a real-audio manifest.",
    )
    parser.add_argument("manifest", type=Path, help="YAML manifest with a `tracks` list")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for ignored run artifacts",
    )
    parser.add_argument("--run-id", default=None, help="Run directory name")
    parser.add_argument("--valley-method", default="hybrid")
    parser.add_argument("--baseline", default="pro", help="Default RPE baseline profile")
    parser.add_argument("--separate", action="store_true", help="Run optional Demucs stem path")
    parser.add_argument("--separation-model", default=DEFAULT_MODEL)
    parser.add_argument("--separation-device", default="cpu")
    parser.add_argument("--learned", action="store_true", help="Attach optional learned annotations")
    parser.add_argument("--json", action="store_true", help="Print summary JSON instead of markdown")
    args = parser.parse_args(argv)

    report = run_manifest(
        args.manifest.resolve(),
        output_dir=args.output_dir.resolve(),
        run_id=args.run_id or _default_run_id(),
        valley_method=args.valley_method,
        baseline=args.baseline,
        include_stems=args.separate,
        separation_model=args.separation_model,
        separation_device=args.separation_device,
        include_learned=args.learned,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report), end="")
    return 1 if report["summary"]["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
