from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

import scripts.fetch_corpus as fc
import scripts.screen_corpus as sc
from svp_rpe.perform import sha256_bytes


def write_yaml(path: Path, data: object) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_matching_sha_materializes_resolved_screener_yaml(tmp_path: Path) -> None:
    source_dir = tmp_path / "drop"
    source_dir.mkdir()
    source = source_dir / "take.wav"
    source.write_bytes(b"pinned audio")
    digest = sha256_bytes(b"pinned audio")
    manifest = tmp_path / "manifest.yaml"
    write_yaml(
        manifest,
        {
            "songs": [
                {
                    "id": "take_01",
                    "stated": {"bpm": 120, "key": "C major", "time_signature": "4/4"},
                    "audio_sha256": digest,
                    "drive_file_id": "drive-123",
                }
            ]
        },
    )
    out = tmp_path / "resolved.yaml"
    cache_dir = tmp_path / "cache"

    assert fc.main([str(manifest), "--source-dir", str(source_dir), "--cache-dir", str(cache_dir), "--out", str(out)]) == 0

    resolved = load_yaml(out)
    assert resolved["songs"] == [
        {
            "id": "take_01",
            "audio": str((cache_dir / "take_01.wav").resolve()),
            "audio_sha256": digest,
            "media_type": "audio",
            "bpm": 120,
            "key": "C major",
            "time_signature": "4/4",
            "drive_file_id": "drive-123",
        }
    ]
    assert (cache_dir / "take_01.wav").read_bytes() == b"pinned audio"
    assert resolved["status"][0]["resolved"] is True


def test_sha_mismatch_is_reported_without_resolving(tmp_path: Path) -> None:
    source_dir = tmp_path / "drop"
    source_dir.mkdir()
    (source_dir / "stale.wav").write_bytes(b"wrong bytes")
    digest = sha256_bytes(b"expected bytes")
    manifest = tmp_path / "manifest.yaml"
    write_yaml(
        manifest,
        {
            "songs": [
                {
                    "id": "stale",
                    "audio": "stale.wav",
                    "stated": {"bpm": 90, "key": "D minor"},
                    "audio_sha256": digest,
                }
            ]
        },
    )

    resolved = fc.resolve_manifest(manifest, source_dir, tmp_path / "cache")

    assert resolved["songs"] == []
    assert resolved["status"][0]["resolved"] is False
    assert resolved["status"][0]["reason"] == "sha256_mismatch"
    assert resolved["status"][0]["source_sha256"] == sha256_bytes(b"wrong bytes")


def test_matching_locator_outside_source_dir_is_resolved(tmp_path: Path) -> None:
    source_dir = tmp_path / "empty-drop"
    source_dir.mkdir()
    external = tmp_path / "already_resolved.wav"
    external.write_bytes(b"already materialized")
    digest = sha256_bytes(b"already materialized")
    manifest = tmp_path / "manifest.yaml"
    write_yaml(
        manifest,
        {
            "songs": [
                {
                    "id": "external",
                    "audio": str(external),
                    "stated": {"bpm": 100},
                    "audio_sha256": digest,
                }
            ]
        },
    )

    resolved = fc.resolve_manifest(manifest, source_dir, tmp_path / "cache")

    assert resolved["status"][0]["resolved"] is True
    assert resolved["songs"][0]["id"] == "external"
    assert Path(resolved["songs"][0]["audio"]).read_bytes() == b"already materialized"


def test_missing_sha_is_not_found_and_excluded(tmp_path: Path) -> None:
    source_dir = tmp_path / "missing-drop"
    digest = sha256_bytes(b"not present")
    manifest = tmp_path / "manifest.yaml"
    write_yaml(
        manifest,
        {
            "songs": [
                {
                    "id": "absent",
                    "stated": {"bpm": 100, "key": "E major"},
                    "audio_sha256": digest,
                }
            ]
        },
    )

    resolved = fc.resolve_manifest(manifest, source_dir, tmp_path / "cache")

    assert resolved["songs"] == []
    assert resolved["status"][0] == {
        "id": "absent",
        "resolved": False,
        "reason": "not_found",
        "audio_sha256": digest,
        "media_type": "audio",
    }


def test_takes_container_and_audio_hash_are_normalized(tmp_path: Path) -> None:
    source_dir = tmp_path / "drop"
    source_dir.mkdir()
    source = source_dir / "upload.mp3"
    source.write_bytes(b"uploaded bytes")
    digest = sha256_bytes(b"uploaded bytes")
    manifest = tmp_path / "manifest.yaml"
    write_yaml(
        manifest,
        {
            "takes": [
                {
                    "take_id": "r1_box_take",
                    "audio_hash": digest,
                    "media_type": "audio",
                    "intent": {
                        "bpm": {"value": 130},
                        "key": {"value": "D minor"},
                        "time_signature": {"value": "4/4"},
                    },
                }
            ]
        },
    )

    resolved = fc.resolve_manifest(manifest, source_dir, tmp_path / "cache")

    song = resolved["songs"][0]
    assert song["id"] == "r1_box_take"
    assert song["audio_sha256"] == digest
    assert song["bpm"] == 130
    assert song["key"] == "D minor"
    assert song["time_signature"] == "4/4"
    assert Path(song["audio"]).read_bytes() == b"uploaded bytes"


def test_materialization_is_idempotent(tmp_path: Path) -> None:
    source_dir = tmp_path / "drop"
    source_dir.mkdir()
    source = source_dir / "take.mp3"
    source.write_bytes(b"same bytes")
    digest = sha256_bytes(b"same bytes")
    manifest = tmp_path / "manifest.yaml"
    write_yaml(
        manifest,
        {"songs": [{"id": "same", "stated": {"bpm": 120}, "audio_sha256": digest}]},
    )
    cache_dir = tmp_path / "cache"

    first = fc.resolve_manifest(manifest, source_dir, cache_dir)
    second = fc.resolve_manifest(manifest, source_dir, cache_dir)

    assert first["songs"][0]["audio"] == second["songs"][0]["audio"]
    assert len(list(cache_dir.iterdir())) == 1
    assert (cache_dir / "same.mp3").read_bytes() == b"same bytes"


def test_cache_filename_collision_uses_hash_suffix(tmp_path: Path) -> None:
    source_dir = tmp_path / "drop"
    source_dir.mkdir()
    first_source = source_dir / "first.wav"
    second_source = source_dir / "second.wav"
    first_source.write_bytes(b"first bytes")
    second_source.write_bytes(b"second bytes")
    first_hash = sha256_bytes(b"first bytes")
    second_hash = sha256_bytes(b"second bytes")
    manifest = tmp_path / "manifest.yaml"
    write_yaml(
        manifest,
        {
            "songs": [
                {"id": "duplicate", "stated": {"bpm": 90}, "audio_sha256": first_hash},
                {"id": "duplicate", "stated": {"bpm": 120}, "audio_sha256": second_hash},
            ]
        },
    )

    resolved = fc.resolve_manifest(manifest, source_dir, tmp_path / "cache")

    first_audio = Path(resolved["songs"][0]["audio"])
    second_audio = Path(resolved["songs"][1]["audio"])
    assert first_audio.name == "duplicate.wav"
    assert second_audio.name == f"duplicate-{second_hash[:12]}.wav"
    assert first_audio != second_audio
    assert sha256_bytes(first_audio.read_bytes()) == first_hash
    assert sha256_bytes(second_audio.read_bytes()) == second_hash


def test_json_status_can_be_printed_with_out_file(tmp_path: Path, capsys) -> None:
    source_dir = tmp_path / "drop"
    source_dir.mkdir()
    digest = sha256_bytes(b"absent")
    manifest = tmp_path / "manifest.yaml"
    write_yaml(manifest, {"songs": [{"id": "absent", "audio_sha256": digest}]})
    out = tmp_path / "resolved.yaml"

    fc.main([str(manifest), "--source-dir", str(source_dir), "--out", str(out), "--json"])

    printed = yaml.safe_load(capsys.readouterr().out)
    assert printed["resolved"] == 0
    assert printed["unresolved"] == 1
    assert printed["status"][0]["reason"] == "not_found"
    assert load_yaml(out)["songs"] == []


def test_resolved_yaml_is_accepted_by_screen_song_hash_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_dir = tmp_path / "drop"
    source_dir.mkdir()
    source = source_dir / "take.wav"
    source.write_bytes(b"hash-checked audio")
    digest = sha256_bytes(b"hash-checked audio")
    manifest = tmp_path / "manifest.yaml"
    write_yaml(
        manifest,
        {
            "songs": [
                {
                    "id": "screenable",
                    "stated": {"bpm": 120, "key": "C major", "time_signature": "4/4"},
                    "audio_sha256": digest,
                }
            ]
        },
    )
    resolved = fc.resolve_manifest(manifest, source_dir, tmp_path / "cache")
    song = resolved["songs"][0]

    phys = SimpleNamespace(
        bpm=120.0,
        bpm_octave_ambiguous=False,
        key="C",
        mode="major",
        time_signature="4/4",
        spectral_centroid=1000.0,
        spectral_profile=SimpleNamespace(high_ratio=0.1),
    )
    monkeypatch.setattr(sc, "extract_physical_from_file", lambda _: phys)
    monkeypatch.setattr(sc, "load_audio", lambda _: SimpleNamespace(y_mono=[0.0], sr=22050))
    monkeypatch.setattr(sc, "compute_bpm", lambda *_, **__: (120.0, 1.0))

    row = sc.screen_song(song)

    assert row["id"] == "screenable"
    assert row["detected"] is not None
    assert row["bpm_relation"]["status"] == "preserved"
    assert "provenance" not in row
