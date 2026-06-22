"""Materialize a content-addressed corpus manifest into screener input.

This loader intentionally does not talk to Google Drive.  `drive_file_id` is
provenance metadata for humans or remote jobs; local resolution is only by the
manifest's sha256 pin against files already present in `--source-dir`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DEFAULT_SOURCE_DIR = ROOT / "examples" / "roundtrip" / "cache" / "_drop"
DEFAULT_CACHE_DIR = ROOT / "examples" / "roundtrip" / "cache"
HASH_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class SourceFile:
    path: Path
    sha256: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_filename_stem(raw: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", raw.strip())
    return stem.strip("._") or "take"


def iter_source_files(source_dir: Path) -> list[SourceFile]:
    if not source_dir.is_dir():
        return []
    files = [path for path in source_dir.rglob("*") if path.is_file()]
    return [SourceFile(path=path, sha256=sha256_file(path)) for path in sorted(files)]


def iter_manifest_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        return []

    records: list[dict[str, Any]] = []
    for key in ("songs", "takes", "abc_experiment"):
        rows = data.get(key)
        if isinstance(rows, list):
            records.extend(row for row in rows if isinstance(row, dict))
    return records


def pin_for(record: dict[str, Any]) -> str | None:
    pin = record.get("audio_sha256") or record.get("audio_hash")
    return str(pin).lower() if pin else None


def media_type_for(record: dict[str, Any]) -> str:
    return str(record.get("media_type") or "audio")


def is_excluded(record: dict[str, Any]) -> bool:
    """`excluded: true` を立てた record は materialization 対象から外す。

    バイト未取得で取得予定もないテイク（例: corpus から落とした 8 本目）を、
    `not_found` として unresolved に計上し続けないための明示マーカー。record 自体は
    screen 実測値・sha256 の記録として manifest に残し、status では `reason: excluded`
    として透明に分類する（silent drop しない）。
    """
    return bool(record.get("excluded"))


def value_from_intent(record: dict[str, Any], key: str) -> Any:
    intent = record.get("intent")
    if not isinstance(intent, dict):
        return None
    value = intent.get(key)
    if isinstance(value, dict):
        return value.get("value")
    return value


def stated_value(record: dict[str, Any], key: str) -> Any:
    if key in record:
        return record.get(key)
    stated = record.get("stated")
    if isinstance(stated, dict) and key in stated:
        return stated.get(key)
    return value_from_intent(record, key)


def record_id(record: dict[str, Any]) -> str:
    for key in ("id", "take_id", "slug", "title"):
        value = record.get(key)
        if value:
            return str(value)
    locator = record.get("audio") or record.get("audio_locator")
    if locator:
        return Path(str(locator)).stem
    return "take"


def candidate_locator(record: dict[str, Any]) -> str | None:
    for key in ("audio", "audio_locator", "filename", "path"):
        value = record.get(key)
        if value:
            return str(value)
    return None


def build_name_index(source_files: list[SourceFile], source_dir: Path) -> dict[str, SourceFile]:
    by_name: dict[str, SourceFile] = {}
    for item in source_files:
        rel = item.path.relative_to(source_dir).as_posix()
        for name in (rel, item.path.name):
            by_name.setdefault(name, item)
    return by_name


def find_locator_candidate(
    record: dict[str, Any],
    source_dir: Path,
    by_name: dict[str, SourceFile],
) -> SourceFile | None:
    raw = candidate_locator(record)
    if not raw:
        return None
    path = Path(raw)
    candidates = [path] if path.is_absolute() else [source_dir / path]
    for candidate in candidates:
        if candidate.is_file():
            return SourceFile(path=candidate, sha256=sha256_file(candidate))
    return by_name.get(path.as_posix()) or by_name.get(path.name)


def materialize(source: SourceFile, record_name: str, expected_sha: str, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.path.suffix or ".bin"
    stem = safe_filename_stem(record_name)
    target = cache_dir / f"{stem}{suffix}"
    if target.is_file():
        if sha256_file(target) == expected_sha:
            return target
        target = cache_dir / f"{stem}-{expected_sha[:12]}{suffix}"
        if target.is_file() and sha256_file(target) == expected_sha:
            return target
        if target.is_file():
            target = cache_dir / f"{stem}-{expected_sha}{suffix}"
    shutil.copy2(source.path, target)
    return target


def resolved_song(record: dict[str, Any], audio_path: Path, expected_sha: str) -> dict[str, Any]:
    song: dict[str, Any] = {
        "id": record_id(record),
        "audio": str(audio_path.resolve()),
        "audio_sha256": expected_sha,
        "media_type": media_type_for(record),
    }
    for key in ("bpm", "key", "mode", "time_signature"):
        value = stated_value(record, key)
        if value is not None:
            song[key] = value
    if record.get("drive_file_id"):
        song["drive_file_id"] = str(record["drive_file_id"])
    return song


def resolve_manifest(
    manifest_path: Path,
    source_dir: Path,
    cache_dir: Path,
) -> dict[str, Any]:
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    records = iter_manifest_records(data)
    source_files = iter_source_files(source_dir)
    by_sha: dict[str, SourceFile] = {}
    for item in source_files:
        by_sha.setdefault(item.sha256, item)
    by_name = build_name_index(source_files, source_dir) if source_dir.is_dir() else {}

    songs: list[dict[str, Any]] = []
    status: list[dict[str, Any]] = []
    for record in records:
        rid = record_id(record)
        if is_excluded(record):
            row: dict[str, Any] = {
                "id": rid,
                "resolved": False,
                "reason": "excluded",
                "media_type": media_type_for(record),
            }
            pin = pin_for(record)
            if pin:
                row["audio_sha256"] = pin
            status.append(row)
            continue
        expected_sha = pin_for(record)
        if not expected_sha:
            status.append(
                {
                    "id": rid,
                    "resolved": False,
                    "reason": "missing_hash",
                    "media_type": media_type_for(record),
                }
            )
            continue

        source = by_sha.get(expected_sha)
        if source is None:
            candidate = find_locator_candidate(record, source_dir, by_name)
            if candidate is not None and candidate.sha256 == expected_sha:
                source = candidate
        if source is None:
            reason = "sha256_mismatch" if candidate is not None else "not_found"
            row: dict[str, Any] = {
                "id": rid,
                "resolved": False,
                "reason": reason,
                "audio_sha256": expected_sha,
                "media_type": media_type_for(record),
            }
            if candidate is not None:
                row["source"] = str(candidate.path.resolve())
                row["source_sha256"] = candidate.sha256
            status.append(row)
            continue

        cache_path = materialize(source, rid, expected_sha, cache_dir)
        songs.append(resolved_song(record, cache_path, expected_sha))
        status.append(
            {
                "id": rid,
                "resolved": True,
                "reason": "resolved",
                "audio_sha256": expected_sha,
                "media_type": media_type_for(record),
                "source": str(source.path.resolve()),
                "cache": str(cache_path.resolve()),
            }
        )

    return {
        "schema_version": "1.0",
        "source_manifest": str(manifest_path.resolve()),
        "songs": songs,
        "status": status,
    }


def dump_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def status_report(data: dict[str, Any]) -> dict[str, Any]:
    status = data["status"]
    resolved = sum(1 for row in status if row.get("resolved"))
    excluded = sum(1 for row in status if row.get("reason") == "excluded")
    return {
        "resolved": resolved,
        "excluded": excluded,
        "unresolved": len(status) - resolved - excluded,
        "status": status,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="corpus manifest YAML")
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path(os.environ.get("SVP_CORPUS_SOURCE_DIR", DEFAULT_SOURCE_DIR)),
        help="directory containing downloaded or uploaded source media",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help="gitignored materialization cache",
    )
    parser.add_argument("--out", type=Path, default=None, help="resolved screener YAML")
    parser.add_argument("--json", action="store_true", help="print resolution status as JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    resolved = resolve_manifest(args.manifest, args.source_dir, args.cache_dir)

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(dump_yaml(resolved), encoding="utf-8")

    if args.json:
        print(json.dumps(status_report(resolved), ensure_ascii=False, indent=2))
    elif args.out is None:
        print(dump_yaml(resolved), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
