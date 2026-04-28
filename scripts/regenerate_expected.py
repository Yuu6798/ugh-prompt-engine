"""scripts/regenerate_expected.py — Regenerate examples/expected_output reference.

For each WAV in `examples/sample_input/` listed in `ground_truth.yaml`, runs the
full pipeline (extract → generate → evaluate) and writes the canonical
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
from typing import Iterable, Tuple

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
VALLEY_METHOD = "hybrid"


def load_song_ids() -> list[Tuple[str, Path]]:
    """Return [(song_id, audio_path), ...] from ground_truth.yaml."""
    data = yaml.safe_load(GROUND_TRUTH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"unexpected ground_truth.yaml structure: {type(data)}")
    songs: list[Tuple[str, Path]] = []
    for entry in data:
        song_id = entry["id"]
        audio_path = SAMPLE_DIR / entry["filename"]
        if not audio_path.is_file():
            raise FileNotFoundError(f"missing sample WAV: {audio_path}")
        songs.append((song_id, audio_path))
    return songs


def render_outputs(audio_path: Path) -> dict[str, str]:
    """Run pipeline once, return {filename: serialized_text} mapping.

    Uses the same serialization parameters as `svprpe run --output-dir` so that
    the byte-level output is identical to the CLI artefact.
    """
    rpe_bundle = extract_rpe_from_file(str(audio_path), valley_method=VALLEY_METHOD)
    svp_bundle = generate_svp(rpe_bundle)
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
    return {
        "rpe.json": rpe_json,
        "svp.yaml": svp_yaml,
        "evaluation.json": eval_json,
    }


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def iter_artefacts(songs: Iterable[Tuple[str, Path]]) -> Iterable[Tuple[str, str, str]]:
    """Yield (relative_path, sha256, text) for every (song, file) pair."""
    for song_id, audio_path in songs:
        outputs = render_outputs(audio_path)
        for filename in ("rpe.json", "svp.yaml", "evaluation.json"):
            text = outputs[filename]
            rel = f"{song_id}/{filename}"
            yield rel, sha256_text(text), text


def write_outputs(songs: list[Tuple[str, Path]]) -> list[Tuple[str, str]]:
    """Regenerate all expected_output files and return [(rel_path, sha256), ...]."""
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    summary: list[Tuple[str, str]] = []
    for rel, digest, text in iter_artefacts(songs):
        out_path = EXPECTED_DIR / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        summary.append((rel, digest))
    HASH_FILE.write_text(
        "".join(f"{digest}  {rel}\n" for rel, digest in summary),
        encoding="utf-8",
    )
    return summary


def parse_hash_file() -> dict[str, str]:
    if not HASH_FILE.is_file():
        return {}
    expected: dict[str, str] = {}
    for line in HASH_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, rel = line.partition("  ")
        if not rel:
            continue
        expected[rel] = digest
    return expected


def check_outputs(songs: list[Tuple[str, Path]]) -> int:
    """Compare regenerated artefacts against committed files.

    Returns process exit code (0=ok, 1=mismatch).
    """
    expected_hashes = parse_hash_file()
    if not expected_hashes:
        print("[check] hashes.txt is missing or empty; run without --check first.",
              file=sys.stderr)
        return 1

    mismatches: list[str] = []
    seen: set[str] = set()
    for rel, digest, text in iter_artefacts(songs):
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

    extra = sorted(set(expected_hashes) - seen)
    for rel in extra:
        mismatches.append(f"{rel}: listed in hashes.txt but not produced (stale entry)")

    if mismatches:
        print("[check] FAIL — expected_output is out of sync:", file=sys.stderr)
        for line in mismatches:
            print(f"  - {line}", file=sys.stderr)
        print(
            "\nRun `python scripts/regenerate_expected.py` to refresh, then "
            "review the diff before committing.",
            file=sys.stderr,
        )
        return 1

    print(f"[check] OK — {len(seen)} artefacts match hashes.txt")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify expected_output matches current pipeline output; exit 1 on mismatch.",
    )
    args = parser.parse_args(argv)

    songs = load_song_ids()

    if args.check:
        return check_outputs(songs)

    summary = write_outputs(songs)
    print(f"[regenerate] wrote {len(summary)} artefacts under {EXPECTED_DIR.relative_to(ROOT)}/")
    for rel, digest in summary:
        print(f"  {digest[:12]}…  {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
