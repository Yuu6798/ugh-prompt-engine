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


def _manifest_sha(root: Path, *, voicebank_version: str = "test") -> str:
    document = subject.build_manifest(root, voicebank_version=voicebank_version)
    return hashlib.sha256(subject.canonical_json_bytes(document)).hexdigest()


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
    expected = _manifest_sha(root, voicebank_version="UTAU Default 1.2")
    first_bytes = subject.write_manifest(
        root,
        staging,
        first,
        voicebank_version="UTAU Default 1.2",
        expected_manifest_sha256=expected,
    )
    second_bytes = subject.write_manifest(
        root,
        staging,
        second,
        voicebank_version="UTAU Default 1.2",
        expected_manifest_sha256=expected,
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
    _write_wav(root / "required.wav")
    (root / "oto.ini").write_text("required.wav=x,0,0,0,0,0\n", encoding="utf-8")
    (root / "character.txt").write_text("name=test\n", encoding="utf-8")
    for name in ("a10.txt", "B2.txt", "a2.txt", "10.txt"):
        (root / name).write_text(name, encoding="utf-8")
    document = subject.build_manifest(root, voicebank_version="test")
    paths = [entry["path"] for entry in document["files"]]
    assert [path for path in paths if path.endswith(".txt") and path != "character.txt"] == [
        "10.txt",
        "B2.txt",
        "a10.txt",
        "a2.txt",
    ]


def test_audio_fields_are_measured(tmp_path: Path) -> None:
    root = _voicebank(tmp_path)
    _write_wav(root / "stereo.wav", sample_rate=44100, channels=2)
    entry = next(
        entry
        for entry in subject.build_manifest(root, voicebank_version="test")["files"]
        if entry["path"] == "stereo.wav"
    )
    assert entry["audio"] == {
        "sample_rate": 44100,
        "bit_depth": 16,
        "channels": 2,
        "frame_count": 2,
    }


def test_unreadable_wav_is_counted_not_silently_zeroed(tmp_path: Path) -> None:
    root = _voicebank(tmp_path)
    (root / "bad.wav").write_bytes(b"not a wave")
    document = subject.build_manifest(root, voicebank_version="test")
    bad = next(entry for entry in document["files"] if entry["path"] == "bad.wav")
    assert "audio" not in bad
    assert document["aggregates"]["audio_unreadable"] == 1


@pytest.mark.parametrize("missing", ["wav", "oto.ini", "character.txt"])
def test_incomplete_voicebank_is_rejected(tmp_path: Path, missing: str) -> None:
    root = _voicebank(tmp_path)
    if missing == "wav":
        (root / "a.wav").unlink()
    else:
        (root / missing).unlink()
    with pytest.raises(ValueError, match="incomplete"):
        subject.build_manifest(root, voicebank_version="test")


def test_out_outside_staging_root_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PrivateBoundaryError):
        subject.write_manifest(
            _voicebank(tmp_path),
            tmp_path / "private",
            tmp_path / "public" / "manifest.json",
            voicebank_version="test",
            expected_manifest_sha256="0" * 64,
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
            expected_manifest_sha256="0" * 64,
        )


def test_out_cannot_replace_a_voicebank_input(tmp_path: Path) -> None:
    root = _voicebank(tmp_path)
    with pytest.raises(PrivateBoundaryError, match="voicebank input"):
        subject.write_manifest(
            root,
            tmp_path,
            root / "oto.ini",
            voicebank_version="test",
            expected_manifest_sha256="0" * 64,
        )
    assert (root / "oto.ini").read_text(encoding="utf-8").startswith("a.wav=")


def test_out_cannot_replace_the_source_zip(tmp_path: Path) -> None:
    root = _voicebank(tmp_path)
    archive = tmp_path / "A0.zip"
    digest = _zip_voicebank(root, archive)
    before = archive.read_bytes()
    with pytest.raises(PrivateBoundaryError, match="source ZIP"):
        subject.write_manifest(
            root,
            tmp_path,
            archive,
            voicebank_version="test",
            expected_manifest_sha256="0" * 64,
            zip_path=archive,
            zip_sha256=digest,
        )
    assert archive.read_bytes() == before


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
    (root / "readme.txt").unlink()

    with pytest.raises(ValueError, match="missing_from_root=.*readme.txt"):
        subject.build_manifest(
            root,
            voicebank_version="test",
            zip_path=archive,
            zip_sha256=digest,
        )


def test_zip_validates_directory_entries_before_ignoring_them(tmp_path: Path) -> None:
    root = _voicebank(tmp_path)
    archive = tmp_path / "A0.zip"
    _zip_voicebank(root, archive)
    with zipfile.ZipFile(archive, "a") as handle:
        handle.writestr("../escape/", b"")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="escapes its archive root"):
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
    subject.write_manifest(
        root,
        staging,
        out,
        voicebank_version="test",
        expected_manifest_sha256=_manifest_sha(root),
    )
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == subject.SCHEMA


def test_manifest_pin_mismatch_does_not_publish(tmp_path: Path) -> None:
    root = _voicebank(tmp_path)
    staging = tmp_path / "private"
    out = staging / "a0_voicebank_manifest.json"
    with pytest.raises(ValueError, match="manifest sha256 mismatch"):
        subject.write_manifest(
            root,
            staging,
            out,
            voicebank_version="test",
            expected_manifest_sha256="0" * 64,
        )
    assert not out.exists()


@pytest.mark.parametrize("mutation", ["add", "remove", "change"])
def test_post_snapshot_voicebank_mutation_does_not_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root = _voicebank(tmp_path)
    staging = tmp_path / "private"
    out = staging / "a0_voicebank_manifest.json"
    expected = _manifest_sha(root)
    canonical_json_bytes = subject.canonical_json_bytes

    def mutate_after_snapshot(document: dict[str, object]) -> bytes:
        if mutation == "add":
            (root / "late.bin").write_bytes(b"late")
        elif mutation == "remove":
            (root / "readme.txt").unlink()
        else:
            (root / "character.txt").write_text(
                "name=changed after snapshot\n", encoding="utf-8"
            )
        return canonical_json_bytes(document)

    monkeypatch.setattr(subject, "canonical_json_bytes", mutate_after_snapshot)
    with pytest.raises(ValueError, match="changed after its manifest snapshot"):
        subject.write_manifest(
            root,
            staging,
            out,
            voicebank_version="test",
            expected_manifest_sha256=expected,
        )
    assert not out.exists()


def test_cli_manifest_pin_comes_from_the_run_contract() -> None:
    assert subject._pinned_manifest_sha256() == (
        "042813936caf759f3fc95a29a6655a07c76a3a302bd6705538443ca5d08fe01f"
    )
