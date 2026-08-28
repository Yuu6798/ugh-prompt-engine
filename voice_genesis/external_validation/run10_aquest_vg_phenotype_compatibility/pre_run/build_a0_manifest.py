"""Build the private RUN10 A0 voicebank manifest.

The manifest contains private voicebank filenames and per-file hashes.  It may
only be written below an explicitly supplied private staging root outside the
Git repository.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    compute_file_sha256,
)
from svp_rpe.utils.atomic_io import atomic_write_bytes  # noqa: E402

SCHEMA = "voicegenesis-run10-a0-voicebank-manifest/0.1"
ORDERING_RULE = "root-relative POSIX paths sorted by UTF-8 byte sequence"


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


def _audio_header(path: Path) -> Optional[Dict[str, int]]:
    try:
        with wave.open(str(path), "rb") as handle:
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
    files = [path for path in root.rglob("*") if path.is_file()]
    return sorted(
        files,
        key=lambda path: path.relative_to(root).as_posix().encode("utf-8"),
    )


def _file_order_sha256(paths: List[str]) -> str:
    payload = "".join(f"{path}\n" for path in paths).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validated_output_path(out: Path, staging_root: Path) -> Path:
    resolved = assert_private_staging_path(out, staging_root)
    repository = repo_root(_RUN10_DIR).resolve()
    if resolved == repository or repository in resolved.parents:
        raise PrivateBoundaryError(
            f"A0 manifest must remain outside the Git repository: {resolved}"
        )
    return resolved


def _validated_zip(zip_path: Optional[Path], expected_sha256: Optional[str]) -> Optional[str]:
    if (zip_path is None) != (expected_sha256 is None):
        raise ValueError("--zip-path and --zip-sha256 must be supplied together")
    if zip_path is None:
        return None
    if not zip_path.is_file():
        raise FileNotFoundError(f"A0 ZIP does not exist: {zip_path}")
    expected = expected_sha256.lower()
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ValueError("--zip-sha256 must be a 64-character hexadecimal digest")
    actual = compute_file_sha256(zip_path)
    if actual != expected:
        raise ValueError(f"A0 ZIP sha256 mismatch: expected {expected}, got {actual}")
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

    verified_zip_sha256 = _validated_zip(zip_path, zip_sha256)
    acquisition: Dict[str, str] = {}
    if obtained_at is not None:
        acquisition["obtained_at"] = obtained_at
    if verified_zip_sha256 is not None:
        acquisition["zip_sha256"] = verified_zip_sha256

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
        relative = path.relative_to(root).as_posix()
        kind = _kind(path)
        kind_counts[kind] += 1
        entry: Dict[str, Any] = {
            "path": relative,
            "size_bytes": path.stat().st_size,
            "sha256": compute_file_sha256(path),
            "kind": kind,
        }
        if kind == "wav":
            audio = _audio_header(path)
            if audio is None:
                unreadable_audio += 1
            else:
                entry["audio"] = audio
        entries.append(entry)

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
    obtained_at: Optional[str] = None,
    zip_path: Optional[Path] = None,
    zip_sha256: Optional[str] = None,
) -> bytes:
    destination = _validated_output_path(Path(out), Path(staging_root))
    document = build_manifest(
        Path(voicebank_root),
        voicebank_version=voicebank_version,
        obtained_at=obtained_at,
        zip_path=Path(zip_path) if zip_path is not None else None,
        zip_sha256=zip_sha256,
    )
    payload = canonical_json_bytes(document)
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
        obtained_at=args.obtained_at,
        zip_path=args.zip_path,
        zip_sha256=args.zip_sha256,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
