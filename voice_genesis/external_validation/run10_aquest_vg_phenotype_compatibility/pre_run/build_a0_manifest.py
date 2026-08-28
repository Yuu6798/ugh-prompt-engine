"""Build the private RUN10 A0 voicebank manifest.

The manifest contains private voicebank filenames and per-file hashes.  It may
only be written below an explicitly supplied private staging root outside the
Git repository.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import stat
import sys
import wave
import zipfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Dict, List, Mapping, Optional

_THIS_DIR = Path(__file__).resolve().parent
_RUN10_DIR = _THIS_DIR.parent
if str(_RUN10_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN10_DIR))

from private_boundary import (  # noqa: E402
    PrivateBoundaryError,
    assert_private_staging_path,
    repo_root,
)
from run10_schema import (  # noqa: E402
    DESIGN_REVISION,
    EXPERIMENT_ID,
    RUN_ID,
    canonical_json_bytes,
    load_run10_contract,
)
from svp_rpe.utils.atomic_io import atomic_write_bytes  # noqa: E402

SCHEMA = "voicegenesis-run10-a0-voicebank-manifest/0.1"
ORDERING_RULE = "root-relative POSIX paths sorted by UTF-8 byte sequence"
CONTRACT_PATH = _RUN10_DIR / "RUN10_CONTRACT.yaml"


def _kind(path: Path) -> str:
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix == ".wav":
        return "wav"
    if suffix == ".frq":
        return "frq"
    if name == "oto.ini":
        return "oto_ini"
    if name == "character.txt":
        return "character_txt"
    if name == "readme.txt":
        return "readme_txt"
    return "other"


def _audio_header(payload: bytes) -> Optional[Dict[str, int]]:
    try:
        with wave.open(io.BytesIO(payload), "rb") as handle:
            if handle.getcomptype() != "NONE":
                return None
            return {
                "sample_rate": handle.getframerate(),
                "bit_depth": handle.getsampwidth() * 8,
                "channels": handle.getnchannels(),
                "frame_count": handle.getnframes(),
            }
    except (EOFError, OSError, wave.Error):
        return None


def _ordered_files(root: Path) -> List[Path]:
    files = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"A0 voicebank must not contain symlinks: {path}")
        if path.is_file():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise ValueError(f"A0 voicebank path escapes its root: {path}")
            files.append(path)
    return sorted(
        files,
        key=lambda path: path.relative_to(root).as_posix().encode("utf-8"),
    )


def _file_order_sha256(paths: List[str]) -> str:
    payload = "".join(f"{path}\n" for path in paths).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalized_sha256(value: str, label: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(
        char not in "0123456789abcdef" for char in normalized
    ):
        raise ValueError(f"{label} must be a 64-character hexadecimal digest")
    return normalized


def _pinned_manifest_sha256() -> str:
    contract = load_run10_contract(CONTRACT_PATH)
    pin = contract.pin("aquest_voicebank_manifest_sha")
    if not pin.pinned or not isinstance(pin.value, str):
        raise ValueError(
            "RUN10_CONTRACT.yaml must PIN aquest_voicebank_manifest_sha before "
            "publishing an A0 manifest"
        )
    return _normalized_sha256(pin.value, "aquest_voicebank_manifest_sha")


def _validate_required_shape(kind_counts: Mapping[str, int]) -> None:
    required = {
        "wav": "at least one WAV",
        "oto_ini": "oto.ini",
        "character_txt": "character.txt",
    }
    missing = [label for kind, label in required.items() if kind_counts[kind] == 0]
    if missing:
        raise ValueError(
            "A0 voicebank is incomplete; missing required material: "
            + ", ".join(missing)
        )


def _assert_voicebank_snapshot_unchanged(
    root: Path, entries: List[Mapping[str, Any]]
) -> None:
    expected = {str(entry["path"]): str(entry["sha256"]) for entry in entries}
    actual: Dict[str, str] = {}
    for path in _ordered_files(root):
        relative = path.relative_to(root).as_posix()
        actual[relative] = hashlib.sha256(path.read_bytes()).hexdigest()

    added = sorted(set(actual) - set(expected))
    removed = sorted(set(expected) - set(actual))
    changed = sorted(
        path
        for path in set(actual) & set(expected)
        if actual[path] != expected[path]
    )
    if added or removed or changed:
        raise ValueError(
            "A0 voicebank changed after its manifest snapshot: "
            f"added={added[:5]!r}, removed={removed[:5]!r}, "
            f"hash_mismatch={changed[:5]!r}"
        )


def _validated_output_path(out: Path, staging_root: Path) -> Path:
    resolved = assert_private_staging_path(out, staging_root)
    repository = repo_root(_RUN10_DIR).resolve()
    if resolved == repository or repository in resolved.parents:
        raise PrivateBoundaryError(
            f"A0 manifest must remain outside the Git repository: {resolved}"
        )
    return resolved


def _assert_output_does_not_replace_input(
    destination: Path,
    voicebank_root: Path,
    zip_path: Optional[Path],
) -> None:
    root = voicebank_root.resolve()
    if destination == root or destination.is_relative_to(root):
        raise PrivateBoundaryError(
            f"A0 manifest output must not replace a voicebank input: {destination}"
        )
    if zip_path is not None and destination == zip_path.resolve():
        raise PrivateBoundaryError(
            f"A0 manifest output must not replace the source ZIP: {destination}"
        )


def _zip_member_path(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename
    if "\\" in name:
        raise ValueError(f"A0 ZIP member must use POSIX separators: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"A0 ZIP member escapes its archive root: {name!r}")
    if path.parts[0].endswith(":"):
        raise ValueError(f"A0 ZIP member has a drive-qualified path: {name!r}")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise ValueError(f"A0 ZIP member must not be a symlink: {name!r}")
    return path


def _validated_zip(
    zip_path: Optional[Path],
    expected_sha256: Optional[str],
    *,
    root_name: str,
    extracted_hashes: Mapping[str, str],
) -> Optional[str]:
    if (zip_path is None) != (expected_sha256 is None):
        raise ValueError("--zip-path and --zip-sha256 must be supplied together")
    if zip_path is None:
        return None
    if not zip_path.is_file():
        raise FileNotFoundError(f"A0 ZIP does not exist: {zip_path}")
    expected = _normalized_sha256(expected_sha256, "--zip-sha256")
    archive_bytes = zip_path.read_bytes()
    actual = hashlib.sha256(archive_bytes).hexdigest()
    if actual != expected:
        raise ValueError(f"A0 ZIP sha256 mismatch: expected {expected}, got {actual}")

    archive_hashes: Dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        inspected = [
            (info, _zip_member_path(info)) for info in archive.infolist()
        ]
        members_and_paths = [pair for pair in inspected if not pair[0].is_dir()]
        members = [pair[0] for pair in members_and_paths]
        paths = [pair[1] for pair in members_and_paths]
        strip_root = bool(paths) and all(
            len(path.parts) >= 2 and path.parts[0] == root_name for path in paths
        )
        for info, path in zip(members, paths):
            relative = PurePosixPath(*path.parts[1:]) if strip_root else path
            key = relative.as_posix()
            if key in archive_hashes:
                raise ValueError(f"A0 ZIP contains a duplicate member path: {key}")
            archive_hashes[key] = hashlib.sha256(archive.read(info)).hexdigest()

    missing = sorted(set(archive_hashes) - set(extracted_hashes))
    unexpected = sorted(set(extracted_hashes) - set(archive_hashes))
    changed = sorted(
        path
        for path in set(archive_hashes) & set(extracted_hashes)
        if archive_hashes[path] != extracted_hashes[path]
    )
    if missing or unexpected or changed:
        raise ValueError(
            "A0 ZIP contents do not match the inventoried voicebank root: "
            f"missing_from_root={missing[:5]!r}, extra_in_root={unexpected[:5]!r}, "
            f"hash_mismatch={changed[:5]!r}"
        )
    return actual


def build_manifest(
    voicebank_root: Path,
    *,
    voicebank_version: str,
    obtained_at: Optional[str] = None,
    zip_path: Optional[Path] = None,
    zip_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    root = Path(voicebank_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"A0 voicebank root does not exist: {root}")
    if not voicebank_version.strip():
        raise ValueError("voicebank_version must not be empty")

    kind_counts = {
        "wav": 0,
        "frq": 0,
        "oto_ini": 0,
        "character_txt": 0,
        "readme_txt": 0,
        "other": 0,
    }
    unreadable_audio = 0
    entries: List[Dict[str, Any]] = []
    for path in _ordered_files(root):
        payload = path.read_bytes()
        relative = path.relative_to(root).as_posix()
        kind = _kind(path)
        kind_counts[kind] += 1
        entry: Dict[str, Any] = {
            "path": relative,
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "kind": kind,
        }
        if kind == "wav":
            audio = _audio_header(payload)
            if audio is None:
                unreadable_audio += 1
            else:
                entry["audio"] = audio
        entries.append(entry)

    _validate_required_shape(kind_counts)
    verified_zip_sha256 = _validated_zip(
        zip_path,
        zip_sha256,
        root_name=root.name,
        extracted_hashes={entry["path"]: entry["sha256"] for entry in entries},
    )
    acquisition: Dict[str, str] = {}
    if obtained_at is not None:
        acquisition["obtained_at"] = obtained_at
    if verified_zip_sha256 is not None:
        acquisition["zip_sha256"] = verified_zip_sha256

    listed_paths = [entry["path"] for entry in entries]
    return {
        "schema": SCHEMA,
        "run_id": RUN_ID,
        "experiment_id": EXPERIMENT_ID,
        "design_revision": DESIGN_REVISION,
        "acquisition": acquisition,
        "voicebank": {
            "version": voicebank_version,
            "root_name": root.name,
        },
        "ordering": {
            "rule": ORDERING_RULE,
            "count": len(entries),
            "file_order_sha256": _file_order_sha256(listed_paths),
        },
        "files": entries,
        "aggregates": {
            "file_count": len(entries),
            "kind_counts": kind_counts,
            "audio_unreadable": unreadable_audio,
        },
    }


def write_manifest(
    voicebank_root: Path,
    staging_root: Path,
    out: Path,
    *,
    voicebank_version: str,
    expected_manifest_sha256: str,
    obtained_at: Optional[str] = None,
    zip_path: Optional[Path] = None,
    zip_sha256: Optional[str] = None,
) -> bytes:
    destination = _validated_output_path(Path(out), Path(staging_root))
    _assert_output_does_not_replace_input(
        destination,
        Path(voicebank_root),
        Path(zip_path) if zip_path is not None else None,
    )
    document = build_manifest(
        Path(voicebank_root),
        voicebank_version=voicebank_version,
        obtained_at=obtained_at,
        zip_path=Path(zip_path) if zip_path is not None else None,
        zip_sha256=zip_sha256,
    )
    payload = canonical_json_bytes(document)
    expected = _normalized_sha256(
        expected_manifest_sha256, "expected_manifest_sha256"
    )
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise ValueError(
            f"A0 manifest sha256 mismatch: expected {expected}, got {actual}"
        )
    _assert_voicebank_snapshot_unchanged(
        Path(voicebank_root).resolve(), document["files"]
    )
    atomic_write_bytes(destination, payload)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voicebank-root", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--voicebank-version", required=True)
    parser.add_argument("--obtained-at")
    parser.add_argument("--zip-path", type=Path)
    parser.add_argument("--zip-sha256")
    return parser


def main() -> int:
    args = _parser().parse_args()
    write_manifest(
        args.voicebank_root,
        args.staging_root,
        args.out,
        voicebank_version=args.voicebank_version,
        expected_manifest_sha256=_pinned_manifest_sha256(),
        obtained_at=args.obtained_at,
        zip_path=args.zip_path,
        zip_sha256=args.zip_sha256,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
