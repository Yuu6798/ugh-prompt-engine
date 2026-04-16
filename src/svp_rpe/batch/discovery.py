"""batch/discovery.py — Discover audio files and SVP candidates."""
from __future__ import annotations

from pathlib import Path
from typing import List


AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac"}
SVP_EXTENSIONS = {".yaml", ".yml", ".txt", ".md"}


def discover_audio_files(directory: str) -> List[Path]:
    """Find all audio files in a directory (non-recursive)."""
    d = Path(directory)
    if not d.is_dir():
        return []
    files = []
    for p in sorted(d.iterdir()):
        if p.suffix.lower() in AUDIO_EXTENSIONS and p.is_file():
            files.append(p)
    return files


def discover_svp_files(directory: str) -> List[Path]:
    """Find all SVP files in a directory."""
    d = Path(directory)
    if not d.is_dir():
        return []
    files = []
    for p in sorted(d.iterdir()):
        if p.suffix.lower() in SVP_EXTENSIONS and p.is_file():
            files.append(p)
    return files


def match_audio_to_svp(
    audio_files: List[Path],
    svp_files: List[Path],
) -> List[tuple[Path, List[Path]]]:
    """Match each audio file to candidate SVP files by stem prefix.

    Returns [(audio_path, [matching_svp_paths])].
    If no SVP matches an audio file, it gets an empty list.
    """
    result = []
    for audio in audio_files:
        stem = audio.stem.lower()
        matches = [
            svp for svp in svp_files
            if svp.stem.lower().startswith(stem) or stem.startswith(svp.stem.lower())
        ]
        result.append((audio, matches))
    return result
