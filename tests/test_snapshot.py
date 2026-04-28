"""tests/test_snapshot.py — Q0-3 hash-based snapshot test.

Verifies that the in-memory pipeline output for each synth WAV in
`examples/sample_input/` matches the committed reference under
`examples/expected_output/` and the hashes recorded in `hashes.txt`.

Pipeline runs once per song (module-scoped fixture); the 15 hash
comparisons are individual parametrized tests so failures pinpoint the
specific (song, file_type) pair that drifted.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.regenerate_expected import (
    ARTEFACT_FILES,
    EXPECTED_DIR,
    HASH_FILE,
    SAMPLE_DIR,
    load_song_ids,
    parse_hash_file,
    render_outputs,
    sha256_text,
)


def _song_artefact_pairs() -> list[tuple[str, str]]:
    if not SAMPLE_DIR.is_dir() or not (SAMPLE_DIR / "ground_truth.yaml").is_file():
        pytest.fail(
            f"sample_input not found at {SAMPLE_DIR}; "
            "Q0-1 must be in place before Q0-3 snapshot tests can run"
        )
    return [
        (song_id, filename)
        for song_id, _, _ in load_song_ids()
        for filename in ARTEFACT_FILES
    ]


SONG_ARTEFACT_PAIRS = _song_artefact_pairs()
SONG_ARTEFACT_IDS = [f"{sid}/{f}" for sid, f in SONG_ARTEFACT_PAIRS]


@pytest.fixture(scope="module")
def pipeline_outputs() -> dict[str, dict[str, str]]:
    """Run the pipeline once per song. {song_id: {filename: text}}."""
    outputs: dict[str, dict[str, str]] = {}
    for song_id, abs_path, rel_path in load_song_ids():
        outputs[song_id] = render_outputs(abs_path, rel_path)
    return outputs


@pytest.fixture(scope="module")
def expected_hashes() -> dict[str, str]:
    if not HASH_FILE.is_file():
        pytest.fail(
            f"{HASH_FILE} missing; run `python scripts/regenerate_expected.py` first"
        )
    hashes = parse_hash_file()
    if not hashes:
        pytest.fail(
            f"{HASH_FILE} is empty; run `python scripts/regenerate_expected.py` to refresh"
        )
    return hashes


@pytest.mark.parametrize(
    ("song_id", "filename"),
    SONG_ARTEFACT_PAIRS,
    ids=SONG_ARTEFACT_IDS,
)
def test_snapshot_matches_reference(
    song_id: str,
    filename: str,
    pipeline_outputs: dict[str, dict[str, str]],
    expected_hashes: dict[str, str],
) -> None:
    """Each (song_id, filename) artefact must match hashes.txt and on-disk file."""
    rel = f"{song_id}/{filename}"

    expected_hash = expected_hashes.get(rel)
    if expected_hash is None:
        pytest.fail(
            f"{rel} not listed in {HASH_FILE.name}; "
            "run `python scripts/regenerate_expected.py` to refresh"
        )

    actual_text = pipeline_outputs[song_id][filename]
    actual_hash = sha256_text(actual_text)

    on_disk_path = EXPECTED_DIR / rel
    if not on_disk_path.is_file():
        pytest.fail(
            f"{rel} missing on disk at {on_disk_path}; "
            "run `python scripts/regenerate_expected.py` to refresh"
        )
    on_disk_text = on_disk_path.read_text(encoding="utf-8")
    on_disk_hash = sha256_text(on_disk_text)

    if actual_hash == expected_hash and on_disk_hash == expected_hash:
        return

    raise AssertionError(
        f"\nSnapshot drift for {rel}:\n"
        f"  hashes.txt expects: {expected_hash}\n"
        f"  pipeline output:    {actual_hash} "
        f"({'match' if actual_hash == expected_hash else 'DRIFT'})\n"
        f"  on-disk file:       {on_disk_hash} "
        f"({'match' if on_disk_hash == expected_hash else 'DRIFT'})\n"
        f"\nRun `python scripts/regenerate_expected.py` to refresh, then "
        f"review the diff before committing."
    )


def test_no_extra_files_in_hashes_txt(expected_hashes: dict[str, str]) -> None:
    """hashes.txt must not list artefacts beyond the canonical (song, file) set."""
    canonical = {f"{sid}/{f}" for sid, f in SONG_ARTEFACT_PAIRS}
    listed = set(expected_hashes.keys())
    stale = sorted(listed - canonical)
    if stale:
        raise AssertionError(
            "hashes.txt contains stale entries not produced by ground_truth:\n"
            + "".join(f"  - {rel}\n" for rel in stale)
            + "\nRun `python scripts/regenerate_expected.py` to refresh."
        )


def test_expected_output_dir_has_no_orphans() -> None:
    """examples/expected_output/ must not contain files outside the canonical set."""
    canonical: set[Path] = {EXPECTED_DIR / "hashes.txt", EXPECTED_DIR / "README.md"}
    for sid, fname in SONG_ARTEFACT_PAIRS:
        canonical.add(EXPECTED_DIR / sid / fname)

    actual = {p for p in EXPECTED_DIR.rglob("*") if p.is_file()}
    orphans = sorted(p.relative_to(EXPECTED_DIR).as_posix() for p in actual - canonical)
    if orphans:
        raise AssertionError(
            "examples/expected_output/ contains orphan files:\n"
            + "".join(f"  - {rel}\n" for rel in orphans)
            + "\nRun `python scripts/regenerate_expected.py` to clean up."
        )
