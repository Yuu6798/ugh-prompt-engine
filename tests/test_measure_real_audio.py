"""Tests for scripts/measure_real_audio.py."""
from __future__ import annotations

from pathlib import Path

from scripts import measure_real_audio as script


def _write_manifest(tmp_path: Path, *, audio_name: str = "track.wav") -> Path:
    audio_path = tmp_path / audio_name
    audio_path.write_bytes(b"not decoded in this test")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        f"""
schema_version: "1.0"
tracks:
  - id: real_track
    path: "{audio_name}"
    baseline: acoustic
    notes: "fixture"
""".strip(),
        encoding="utf-8",
    )
    return manifest


def test_load_manifest_resolves_paths_relative_to_manifest(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)

    tracks = script.load_manifest(manifest, default_baseline="pro")

    assert len(tracks) == 1
    assert tracks[0].track_id == "real_track"
    assert tracks[0].audio_path == (tmp_path / "track.wav").resolve()
    assert tracks[0].baseline == "acoustic"


def test_run_manifest_writes_track_outputs_and_summary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(tmp_path)

    def fake_render_track_outputs(track: script.TrackSpec, **_kwargs):
        return (
            {
                "rpe.json": '{"rpe": true}',
                "svp.yaml": "schema_version: '1.0'\n",
                "evaluation.json": '{"score": 1.0}',
            },
            script.TrackSummary(
                track_id=track.track_id,
                audio_path=str(track.audio_path),
                baseline=track.baseline,
                status="ok",
                output_dir="",
                duration_sec=30.0,
                sample_rate=44100,
                channels=2,
                bpm=120.0,
                key="C",
                mode="major",
                time_signature="4/4",
                section_count=3,
                rpe_score=0.8,
                ugher_score=0.7,
                integrated_score=0.75,
            ),
        )

    monkeypatch.setattr(script, "render_track_outputs", fake_render_track_outputs)

    report = script.run_manifest(
        manifest,
        output_dir=tmp_path / "runs",
        run_id="fixed-run",
        valley_method="hybrid",
        baseline="pro",
        include_stems=False,
        separation_model="fake",
        separation_device="cpu",
        include_learned=False,
    )

    track_dir = tmp_path / "runs" / "fixed-run" / "real_track"
    assert (track_dir / "rpe.json").read_text(encoding="utf-8") == '{"rpe": true}'
    assert (track_dir / "svp.yaml").is_file()
    assert (track_dir / "evaluation.json").is_file()
    assert (tmp_path / "runs" / "fixed-run" / "summary.json").is_file()
    assert (tmp_path / "runs" / "fixed-run" / "summary.md").is_file()
    assert report["summary"] == {"total": 1, "ok": 1, "error": 0}
    assert report["tracks"][0]["output_dir"] == str(track_dir)


def test_run_manifest_records_track_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(tmp_path)

    def raise_decode_error(_track: script.TrackSpec, **_kwargs):
        raise RuntimeError("decode failed")

    monkeypatch.setattr(script, "render_track_outputs", raise_decode_error)

    report = script.run_manifest(
        manifest,
        output_dir=tmp_path / "runs",
        run_id="fixed-run",
        valley_method="hybrid",
        baseline="pro",
        include_stems=False,
        separation_model="fake",
        separation_device="cpu",
        include_learned=False,
    )

    assert report["summary"] == {"total": 1, "ok": 0, "error": 1}
    assert report["tracks"][0]["status"] == "error"
    assert "RuntimeError: decode failed" == report["tracks"][0]["error"]
