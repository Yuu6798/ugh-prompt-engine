"""Static guards for the quarantined RUN9 alternate diagnostic preflight."""
from __future__ import annotations

from pathlib import Path


RUN_DIR = Path(__file__).resolve().parents[1]
PREFLIGHT = RUN_DIR / "RUN9_ALTERNATE_DIAGNOSTIC_PREFLIGHT_20260830.md"
README = RUN_DIR / "README.md"

EXPECTED_CANDIDATE = "80a40f9ebee3f486de8e48c3911b188a6a4652147dd9e02dfcd90ef2f9eac646"
OBSERVED_ACOUSTIC = "463d04839b342ea666cac1f5dcd8248d1cbe825494a1ddf646581b9a0ac6ca53"


def test_preflight_records_exact_candidate_mismatch_and_hard_stop() -> None:
    text = PREFLIGHT.read_text(encoding="utf-8")

    assert text.startswith("signed_by: GPT\n")
    assert text.rstrip().endswith("-- GPT")
    assert EXPECTED_CANDIDATE in text
    assert OBSERVED_ACOUSTIC in text
    assert "| preflight disposition | `CANDIDATE_BYTE_IDENTITY_MISMATCH` |" in text
    assert "| diagnostic renders admitted | `0 / 84` |" in text
    assert "| candidate-bound diagnostic issued | no |" in text
    assert "no second export was run" in text


def test_preflight_preserves_non_adjudicative_boundary() -> None:
    text = PREFLIGHT.read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    for boundary in (
        "is not a formal RUN9 attempt",
        "is not a Birth Gate result",
        "does not amend the append-only conclusion",
        "remain unchanged",
        "No Birth scientific outcome (`ESTABLISHED` or `NOT_ESTABLISHED`) is asserted",
    ):
        assert boundary in normalized
    assert "| learning progression | prohibited |" in text


def test_readme_links_the_preflight_and_records_zero_renders() -> None:
    text = README.read_text(encoding="utf-8")

    assert "`RUN9_ALTERNATE_DIAGNOSTIC_PREFLIGHT_20260830.md`" in text
    assert "84-render診断は0件" in text
    assert "`463d048...`" in text
