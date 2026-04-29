"""scripts/regenerate_expected.py — Regenerate examples/expected_output reference.

Runs the full pipeline (extract → generate → evaluate) for every WAV listed in
`examples/sample_input/ground_truth.yaml` and writes the canonical
`rpe.json` / `svp.yaml` / `evaluation.json` triples under
`examples/expected_output/<song_id>/`. Also (re)writes a `hashes.txt` with
SHA-256 checksums for snapshot comparison.

Usage:
    python scripts/regenerate_expected.py            # regenerate (overwrite)
    python scripts/regenerate_expected.py --check    # verify only, exit 1 on mismatch
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

from svp_rpe.eval.scorer_integrated import score_integrated
from svp_rpe.eval.scorer_rpe import score_rpe
from svp_rpe.eval.scorer_ugher import score_ugher
from svp_rpe.rpe.extractor import extract_rpe_from_file
from svp_rpe.svp.generator import generate_svp
from svp_rpe.svp.render_yaml import render_yaml

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "examples" / "sample_input"
EXPECTED_DIR = ROOT / "examples" / "expected_output"
GROUND_TRUTH = SAMPLE_DIR / "ground_truth.yaml"
HASH_FILE = EXPECTED_DIR / "hashes.txt"
README_FILE = EXPECTED_DIR / "README.md"
VALLEY_METHOD = "hybrid"
ARTEFACT_FILES = ("rpe.json", "svp.yaml", "evaluation.json")


def load_song_ids() -> list[tuple[str, Path, str]]:
    """Return [(song_id, abs_audio_path, repo_relative_audio_path), ...]."""
    data = yaml.safe_load(GROUND_TRUTH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"unexpected ground_truth.yaml structure: {type(data)}")
    songs: list[tuple[str, Path, str]] = []
    for entry in data:
        song_id = entry["id"]
        audio_path = SAMPLE_DIR / entry["filename"]
        if not audio_path.is_file():
            raise FileNotFoundError(f"missing sample WAV: {audio_path}")
        rel_path = audio_path.relative_to(ROOT).as_posix()
        songs.append((song_id, audio_path, rel_path))
    return songs


def render_outputs(audio_path: Path, audio_path_rel: str) -> dict[str, str]:
    """Run pipeline once, return {filename: serialized_text} mapping.

    The repo-relative `audio_path_rel` overrides absolute path fields in the
    output bundles so the bytes are independent of the checkout location.
    """
    rpe_bundle = extract_rpe_from_file(str(audio_path), valley_method=VALLEY_METHOD)
    rpe_bundle.audio_file = audio_path_rel

    svp_bundle = generate_svp(rpe_bundle)
    if svp_bundle.data_lineage.source_artifact is not None:
        svp_bundle.data_lineage.source_artifact.path = audio_path_rel
    if svp_bundle.data_lineage.source_audio is not None:
        svp_bundle.data_lineage.source_audio = audio_path_rel

    rpe_score = score_rpe(rpe_bundle.physical)
    ugher_score = score_ugher(rpe_bundle, svp_bundle)
    integrated = score_integrated(ugher_score, rpe_score)

    rpe_json = json.dumps(rpe_bundle.model_dump(), ensure_ascii=False, indent=2)
    svp_yaml = render_yaml(svp_bundle)
    eval_json = json.dumps(
        {
            "rpe_score": rpe_score.model_dump(),
            "ugher_score": ugher_score.model_dump(),
            "integrated_score": integrated.model_dump(),
        },
        ensure_ascii=False,
        indent=2,
    )
    return {"rpe.json": rpe_json, "svp.yaml": svp_yaml, "evaluation.json": eval_json}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def collect_artefacts(
    songs: list[tuple[str, Path, str]],
) -> list[tuple[str, str, str]]:
    """Run the pipeline for every song and return [(rel_path, sha256, text), ...].

    Materialised eagerly so a partial failure leaves disk untouched.
    """
    artefacts: list[tuple[str, str, str]] = []
    for song_id, audio_path, audio_path_rel in songs:
        outputs = render_outputs(audio_path, audio_path_rel)
        for filename in ARTEFACT_FILES:
            text = outputs[filename]
            rel = f"{song_id}/{filename}"
            artefacts.append((rel, sha256_text(text), text))
    return artefacts


def write_outputs(artefacts: list[tuple[str, str, str]]) -> tuple[list[tuple[str, str]], list[str]]:
    """Write all artefacts + hashes.txt and sweep orphans.

    Returns ([(rel_path, sha256), ...], [removed_rel_path, ...]).
    """
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    summary: list[tuple[str, str]] = []
    for rel, digest, text in artefacts:
        out_path = EXPECTED_DIR / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        summary.append((rel, digest))
    HASH_FILE.write_text(
        "".join(f"{digest}  {rel}\n" for rel, digest in summary),
        encoding="utf-8",
    )
    canonical = {rel for rel, _, _ in artefacts}
    removed = sweep_orphans(canonical)
    return summary, removed


def sweep_orphans(canonical: set[str]) -> list[str]:
    """Delete files / empty dirs under EXPECTED_DIR that are not canonical.

    Preserves `hashes.txt` and `README.md`. Returns the list of removed paths
    (repo-relative under EXPECTED_DIR) so callers can report what was cleaned.
    """
    removed: list[str] = []
    orphans = sorted(discover_disk_artefacts() - canonical)
    for rel in orphans:
        path = EXPECTED_DIR / rel
        path.unlink()
        removed.append(rel)
    for path in sorted(EXPECTED_DIR.rglob("*"), key=lambda p: -len(p.parts)):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return removed


def parse_hash_file() -> dict[str, str]:
    if not HASH_FILE.is_file():
        return {}
    expected: dict[str, str] = {}
    for line in HASH_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, rel = line.partition("  ")
        if rel:
            expected[rel] = digest
    return expected


def discover_disk_artefacts() -> set[str]:
    """Return repo-relative artefact paths currently on disk under EXPECTED_DIR."""
    if not EXPECTED_DIR.is_dir():
        return set()
    found: set[str] = set()
    for path in EXPECTED_DIR.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(EXPECTED_DIR).as_posix()
        if path == HASH_FILE or path == README_FILE:
            continue
        found.add(rel)
    return found


def check_outputs(songs: list[tuple[str, Path, str]]) -> int:
    """Compare regenerated artefacts against committed files. 0=ok, 1=mismatch."""
    expected_hashes = parse_hash_file()
    if not expected_hashes:
        print("[check] hashes.txt is missing or empty; run without --check first.",
              file=sys.stderr)
        return 1

    artefacts = collect_artefacts(songs)
    mismatches: list[str] = []
    seen: set[str] = set()
    for rel, digest, text in artefacts:
        seen.add(rel)
        committed_path = EXPECTED_DIR / rel
        committed_text = (
            committed_path.read_text(encoding="utf-8")
            if committed_path.is_file()
            else None
        )
        committed_hash = expected_hashes.get(rel)

        if committed_text is None:
            mismatches.append(f"{rel}: file missing on disk")
            continue
        if committed_hash is None:
            mismatches.append(f"{rel}: not listed in hashes.txt")
            continue
        committed_text_hash = sha256_text(committed_text)
        if committed_text_hash != committed_hash:
            mismatches.append(
                f"{rel}: file on disk does not match hashes.txt "
                f"(disk={committed_text_hash[:12]}…, hashes.txt={committed_hash[:12]}…)"
            )
        if digest != committed_hash:
            mismatches.append(
                f"{rel}: pipeline output drifted from hashes.txt "
                f"(pipeline={digest[:12]}…, hashes.txt={committed_hash[:12]}…)"
            )

    for rel in sorted(set(expected_hashes) - seen):
        mismatches.append(f"{rel}: listed in hashes.txt but not produced (stale entry)")

    orphans = sorted(discover_disk_artefacts() - seen)
    for rel in orphans:
        mismatches.append(f"{rel}: orphan file on disk (not produced by current ground_truth)")

    if mismatches:
        print("[check] FAIL - expected_output is out of sync:", file=sys.stderr)
        for line in mismatches:
            print(f"  - {line}", file=sys.stderr)
        print(
            "\nRun `python scripts/regenerate_expected.py` to refresh, then "
            "review the diff before committing.",
            file=sys.stderr,
        )
        return 1

    print(f"[check] OK - {len(seen)} artefacts match hashes.txt")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate or verify examples/expected_output snapshots.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify expected_output matches current pipeline output; exit 1 on mismatch.",
    )
    args = parser.parse_args(argv)

    songs = load_song_ids()

    if args.check:
        return check_outputs(songs)

    artefacts = collect_artefacts(songs)
    summary, removed = write_outputs(artefacts)
    print(f"[regenerate] wrote {len(summary)} artefacts under {EXPECTED_DIR.relative_to(ROOT)}/")
    for rel, digest in summary:
        print(f"  {digest[:12]}…  {rel}")
    if removed:
        print(f"[regenerate] swept {len(removed)} orphan file(s):")
        for rel in removed:
            print(f"  - {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
