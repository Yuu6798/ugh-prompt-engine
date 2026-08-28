"""Tests for the private RUN10 A0 voicebank manifest generator."""
from __future__ import annotations

import hashlib
import json
import sys
import wave
import zipfile
from pathlib import Path

import pytest

_THIS_DIR = Path(__file__).resolve().parent
_RUN10_DIR = _THIS_DIR.parent
if str(_RUN10_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN10_DIR))
if str(_RUN10_DIR / "pre_run") not in sys.path:
    sys.path.insert(0, str(_RUN10_DIR / "pre_run"))

import build_a0_manifest as subject  # noqa: E402
from private_boundary import (  # noqa: E402
    PrivateBoundaryError,
    classify_violation,
    repo_root,
)


def _write_wav(path: Path, *, sample_rate: int = 22050, channels: int = 1) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * channels * 2)


def _voicebank(tmp_path: Path) -> Path:
    root = tmp_path / "voicebank"
    root.mkdir()
    _write_wav(root / "a.wav")
    (root / "a.frq").write_bytes(b"frq")
    (root / "oto.ini").write_text("a.wav=a,0,0,0,0,0\n", encoding="utf-8")
    (root / "character.txt").write_text("name=Default\n", encoding="utf-8")
    (root / "readme.txt").write_text("private\n", encoding="utf-8")
    return root


def _zip_voicebank(root: Path, archive: Path) -> str:
    with zipfile.ZipFile(archive, "w") as handle:
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            handle.writestr(f"{root.name}/{relative}", path.read_bytes())
    return hashlib.sha256(archive.read_bytes()).hexdigest()


def test_manifest_has_the_declared_shape(tmp_path: Path) -> None:
    document = subject.build_manifest(
        _voicebank(tmp_path), voicebank_version="UTAU Default 1.2"
    )
    assert document["schema"] == subject.SCHEMA
    assert set(document) == {
        "schema",
        "run_id",
        "experiment_id",
        "design_revision",
        "acquisition",
        "voicebank",
        "ordering",
        "files",
        "aggregates",
    }
    assert document["ordering"]["count"] == len(document["files"])
    assert all(
        {"path", "size_bytes", "sha256", "kind"} <= set(entry)
        for entry in document["files"]
    )


def test_manifest_is_byte_deterministic(tmp_path: Path) -> None:
    root = _voicebank(tmp_path)
    staging = tmp_path / "private"
    first = staging / "first.json"
    second = staging / "second.json"
    first_bytes = subject.write_manifest(
        root, staging, first, voicebank_version="UTAU Default 1.2"
    )
    second_bytes = subject.write_manifest(
        root, staging, second, voicebank_version="UTAU Default 1.2"
    )
    assert first_bytes == second_bytes == first.read_bytes() == second.read_bytes()


def test_file_order_sha_matches_the_listed_paths(tmp_path: Path) -> None:
    document = subject.build_manifest(
        _voicebank(tmp_path), voicebank_version="UTAU Default 1.2"
    )
    paths = [entry["path"] for entry in document["files"]]
    expected = hashlib.sha256(
        "".join(f"{path}\n" for path in paths).encode("utf-8")
    ).hexdigest()
    assert document["ordering"]["file_order_sha256"] == expected


def test_ordering_is_locale_independent(tmp_path: Path) -> None:
    root = tmp_path / "voicebank"
    root.mkdir()
    for name in ("a10.txt", "B2.txt", "a2.txt", "10.txt"):
        (root / name).write_text(name, encoding="utf-8")
    document = subject.build_manifest(root, voicebank_version="test")
    assert [entry["path"] for entry in document["files"]] == [
        "10.txt",
        "B2.txt",
        "a10.txt",
        "a2.txt",
    ]


def test_audio_fields_are_measured(tmp_path: Path) -> None:
    root = tmp_path / "voicebank"
    root.mkdir()
    _write_wav(root / "stereo.wav", sample_rate=44100, channels=2)
    entry = subject.build_manifest(root, voicebank_version="test")["files"][0]
    assert entry["audio"] == {
        "sample_rate": 44100,
        "bit_depth": 16,
        "channels": 2,
        "frame_count": 2,
    }


def test_unreadable_wav_is_counted_not_silently_zeroed(tmp_path: Path) -> None:
    root = tmp_path / "voicebank"
    root.mkdir()
    (root / "bad.wav").write_bytes(b"not a wave")
    document = subject.build_manifest(root, voicebank_version="test")
    assert "audio" not in document["files"][0]
    assert document["aggregates"]["audio_unreadable"] == 1


def test_out_outside_staging_root_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PrivateBoundaryError):
        subject.write_manifest(
            _voicebank(tmp_path),
            tmp_path / "private",
            tmp_path / "public" / "manifest.json",
            voicebank_version="test",
        )


def test_out_inside_repository_is_refused(tmp_path: Path) -> None:
    repository = repo_root(_THIS_DIR)
    staging = repository / ".run10-private-test"
    with pytest.raises(PrivateBoundaryError):
        subject.write_manifest(
            _voicebank(tmp_path),
            staging,
            staging / "a0_voicebank_manifest.json",
            voicebank_version="test",
        )


def test_zip_sha_mismatch_fails_closed(tmp_path: Path) -> None:
    archive = tmp_path / "A0.zip"
    archive.write_bytes(b"archive")
    with pytest.raises(ValueError, match="sha256 mismatch"):
        subject.build_manifest(
            _voicebank(tmp_path),
            voicebank_version="test",
            zip_path=archive,
            zip_sha256="0" * 64,
        )


def test_verified_zip_is_recorded(tmp_path: Path) -> None:
    root = _voicebank(tmp_path)
    archive = tmp_path / "A0.zip"
    digest = _zip_voicebank(root, archive)
    document = subject.build_manifest(
        root,
        voicebank_version="test",
        zip_path=archive,
        zip_sha256=digest,
    )
    assert document["acquisition"]["zip_sha256"] == digest


def test_verified_zip_must_match_the_inventoried_voicebank(tmp_path: Path) -> None:
    root = _voicebank(tmp_path)
    archive = tmp_path / "A0.zip"
    digest = _zip_voicebank(root, archive)
    (root / "character.txt").write_text("name=stale extraction\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hash_mismatch=.*character.txt"):
        subject.build_manifest(
            root,
            voicebank_version="test",
            zip_path=archive,
            zip_sha256=digest,
        )


def test_verified_zip_rejects_unarchived_extra_files(tmp_path: Path) -> None:
    root = _voicebank(tmp_path)
    archive = tmp_path / "A0.zip"
    digest = _zip_voicebank(root, archive)
    (root / "injected.bin").write_bytes(b"not from the archive")

    with pytest.raises(ValueError, match="extra_in_root=.*injected.bin"):
        subject.build_manifest(
            root,
            voicebank_version="test",
            zip_path=archive,
            zip_sha256=digest,
        )


def test_verified_zip_rejects_missing_extracted_files(tmp_path: Path) -> None:
    root = _voicebank(tmp_path)
    archive = tmp_path / "A0.zip"
    digest = _zip_voicebank(root, archive)
    (root / "character.txt").unlink()

    with pytest.raises(ValueError, match="missing_from_root=.*character.txt"):
        subject.build_manifest(
            root,
            voicebank_version="test",
            zip_path=archive,
            zip_sha256=digest,
        )


def test_obtained_at_is_only_recorded_when_explicit(tmp_path: Path) -> None:
    root = _voicebank(tmp_path)
    without = subject.build_manifest(root, voicebank_version="test")
    with_value = subject.build_manifest(
        root, voicebank_version="test", obtained_at="2026-08-28"
    )
    assert "obtained_at" not in without["acquisition"]
    assert with_value["acquisition"]["obtained_at"] == "2026-08-28"


def test_manifest_is_not_publishable() -> None:
    reason = classify_violation("pre_run/a0_voicebank_manifest.json")
    assert reason is not None


def test_written_manifest_is_valid_json(tmp_path: Path) -> None:
    root = _voicebank(tmp_path)
    staging = tmp_path / "private"
    out = staging / "a0_voicebank_manifest.json"
    subject.write_manifest(root, staging, out, voicebank_version="test")
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == subject.SCHEMA
