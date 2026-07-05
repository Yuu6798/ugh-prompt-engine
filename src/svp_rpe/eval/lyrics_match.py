"""eval/lyrics_match.py — lyrics-adherence instrument (no learned-model imports).

Output-side counterpart to `rpe/learned/lyrics_adapter.py`: given a
faster-whisper transcription's `.text` (already produced elsewhere) and a
list of expected ordered lyric lines, reports a best-match similarity per
line plus an overall similarity. Pure `difflib` + `unicodedata` logic — no
torch / faster_whisper import here, so this module is always importable
without the `lyrics` extra.

This is an INSTRUMENT, not a verdict: `match_lyrics` returns ratios and
matches only. It never emits pass/fail, a threshold judgment, or a
verdict field — the same "計器であって verdict なし" convention as
`roundtrip` / `score-adherence` / `audit` (see docs/control_profile.md).

Comparison is char-level (not token-level) after normalization, because
Japanese lyrics have no reliable whitespace token boundaries — splitting
on spaces would be meaningless for most JA lyric lines. Normalization
(`normalize_lyrics_text`) is NFKC (folds full-width/half-width variants and
compatibility characters) -> casefold -> strip all Unicode punctuation
(`P*`) and symbol (`S*`) categories -> collapse whitespace, matching the
repo's existing float/text normalization conventions.

Per-line scoring is substring-tolerant (`_partial_ratio`): whisper segment
boundaries are decoder artifacts, not lyric boundaries, so a transcript can
come back as ONE long segment containing many expected lines. A plain
`SequenceMatcher.ratio()` against that long candidate length-penalizes a
perfectly-sung line (real-inference E2E: a verbatim-contained line scored
0.38 while `overall_similarity` was 1.0). For the 検収 question — "was this
line sung?" — containment must not be length-penalized, so when a candidate
is longer than the expected line, the best ratio over equal-length sliding
windows of the candidate is used. `overall_similarity` intentionally stays
the plain full-concatenation ratio: it measures global agreement, not
containment.
"""
from __future__ import annotations

import difflib
import unicodedata
from typing import Any

__all__ = ["normalize_lyrics_text", "match_lyrics"]

_STRIPPED_CATEGORY_PREFIXES = ("P", "S")


def normalize_lyrics_text(text: str) -> str:
    """NFKC-normalize, casefold, strip punctuation/symbols, collapse whitespace.

    Unicode punctuation (`P*`: `Po`, `Pd`, ...) and symbol (`S*`: `Sc`,
    `Sm`, ...) categories are stripped char-by-char via
    `unicodedata.category`, so this normalizes both ASCII and CJK
    punctuation (e.g. `、` `。` `「` `」`) without a hand-maintained
    punctuation table.
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    kept = "".join(
        ch
        for ch in folded
        if not unicodedata.category(ch).startswith(_STRIPPED_CATEGORY_PREFIXES)
    )
    return " ".join(kept.split())


def _char_level(text: str) -> str:
    """`normalize_lyrics_text` with whitespace also stripped, for char-level matching."""
    return normalize_lyrics_text(text).replace(" ", "")


def _partial_ratio(needle: str, haystack: str) -> float:
    """Substring-tolerant `SequenceMatcher.ratio()` of `needle` against `haystack`.

    When `haystack` is longer than `needle`, returns the best plain ratio of
    `needle` against every `len(needle)`-length sliding window of `haystack`
    (step 1; strings are short post-normalization, and a `1.0` window
    early-exits), so a line contained verbatim inside a longer transcript
    scores 1.0 instead of being length-penalized. When `haystack` is not
    longer, this is exactly the plain ratio.
    """
    if len(haystack) <= len(needle):
        return difflib.SequenceMatcher(None, needle, haystack).ratio()
    window = len(needle)
    best = 0.0
    for start in range(len(haystack) - window + 1):
        ratio = difflib.SequenceMatcher(None, needle, haystack[start : start + window]).ratio()
        if ratio > best:
            best = ratio
            if best == 1.0:
                break
    return best


def match_lyrics(expected_lines: list[str], transcribed_text: str) -> dict[str, Any]:
    """Report per-line best-match similarity of `expected_lines` against `transcribed_text`.

    For each non-empty (after normalization) expected line, computes a
    substring-tolerant partial ratio (`_partial_ratio`: char-level,
    normalized/space-stripped; sliding-window when the candidate is longer
    than the line — segment boundaries are whisper decoder artifacts, so a
    verbatim-contained line must not be length-penalized) against every
    transcribed line (`transcribed_text.splitlines()`) AND against the full
    transcribed text, keeping the best. `overall_similarity` is the PLAIN
    (non-partial) ratio between the full normalized concatenation of
    `expected_lines` and the full normalized `transcribed_text` — it
    measures global agreement, not containment.

    This is a descriptive instrument, not a verdict — no threshold, no
    pass/fail, no aggregate judgment beyond the reported ratios.

    Parameters
    ----------
    expected_lines
        Ordered reference lyric lines (e.g. a `CompositionScore` lyric
        sheet or any plain-text reference). A line that normalizes to the
        empty string (blank line, punctuation-only line) is skipped and
        counted in `n_skipped_empty_lines` rather than silently dropped.
    transcribed_text
        `LearnedLyricsTranscription.text` (or any newline-joined
        transcript text) to match against.

    Returns
    -------
    dict with keys:
        lines: list of {expected, best_ratio (4dp), best_match}
        overall_similarity: float (4dp)
        n_expected_lines: int (len(expected_lines), including skipped)
        n_skipped_empty_lines: int
        params: {normalization, matcher}
    """
    transcribed_candidates: list[str] = [
        line for line in transcribed_text.splitlines() if _char_level(line)
    ]
    full_transcribed_norm = _char_level(transcribed_text)
    # The full text is always a candidate (in addition to per-line
    # candidates) — a segment boundary is a whisper artifact, not a lyric
    # boundary, so the best match for a multi-segment expected line may
    # only appear when matched against the whole transcript.
    candidates = [*transcribed_candidates, transcribed_text]

    lines: list[dict[str, Any]] = []
    skipped_empty = 0
    for expected in expected_lines:
        norm_expected = _char_level(expected)
        if not norm_expected:
            skipped_empty += 1
            continue

        best_ratio = 0.0
        best_match = ""
        for candidate in candidates:
            norm_candidate = _char_level(candidate)
            if not norm_candidate:
                continue
            ratio = _partial_ratio(norm_expected, norm_candidate)
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = candidate
        lines.append(
            {
                "expected": expected,
                "best_ratio": round(best_ratio, 4),
                "best_match": best_match,
            }
        )

    expected_norm_concat = _char_level("\n".join(expected_lines))
    overall_similarity = round(
        difflib.SequenceMatcher(None, expected_norm_concat, full_transcribed_norm).ratio(),
        4,
    )

    return {
        "lines": lines,
        "overall_similarity": overall_similarity,
        "n_expected_lines": len(expected_lines),
        "n_skipped_empty_lines": skipped_empty,
        "params": {
            "normalization": "nfkc_casefold_nopunct_nospace",
            "matcher": "difflib.SequenceMatcher+partial",
        },
    }
