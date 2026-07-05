"""tests/test_lyrics_match.py — lyrics-adherence instrument (pure logic, no learned imports)."""
from __future__ import annotations

from svp_rpe.eval.lyrics_match import match_lyrics, normalize_lyrics_text


class TestNormalizeLyricsText:
    def test_casefold_and_whitespace_collapse(self):
        assert normalize_lyrics_text("  Hello   WORLD  ") == "hello world"

    def test_strips_ascii_punctuation(self):
        assert normalize_lyrics_text("Hello, world!") == "hello world"

    def test_strips_japanese_punctuation_via_nfkc_and_category(self):
        assert normalize_lyrics_text("「こんにちは、世界。」") == "こんにちは世界"

    def test_nfkc_folds_fullwidth_characters(self):
        # Full-width ASCII digits/letters fold to their standard form under NFKC.
        assert normalize_lyrics_text("Ｈｅｌｌｏ　１２３") == "hello 123"

    def test_empty_after_normalization(self):
        assert normalize_lyrics_text("...!!!　、。") == ""


class TestMatchLyrics:
    def test_exact_match_english(self):
        expected = ["hello world", "second line"]
        transcribed = "hello world\nsecond line"
        report = match_lyrics(expected, transcribed)

        assert report["overall_similarity"] == 1.0
        assert report["n_expected_lines"] == 2
        assert report["n_skipped_empty_lines"] == 0
        for line in report["lines"]:
            assert line["best_ratio"] == 1.0
        assert report["params"] == {
            "normalization": "nfkc_casefold_nopunct_nospace",
            "matcher": "difflib.SequenceMatcher+partial",
            "order_stats": (
                "char_offset_cursor (best window start in normalized full transcript)"
            ),
        }

    def test_exact_match_japanese(self):
        expected = ["こんにちは世界", "また明日"]
        transcribed = "こんにちは世界\nまた明日"
        report = match_lyrics(expected, transcribed)

        assert report["overall_similarity"] == 1.0
        for line in report["lines"]:
            assert line["best_ratio"] == 1.0

    def test_partial_match_scores_between_zero_and_one(self):
        expected = ["hello world"]
        transcribed = "hello word"
        report = match_lyrics(expected, transcribed)

        assert 0.0 < report["lines"][0]["best_ratio"] < 1.0
        assert report["lines"][0]["best_match"] == "hello word"

    def test_punctuation_case_and_width_invariance(self):
        expected = ["Hello, World!"]
        transcribed = "ｈｅｌｌｏ world"
        report = match_lyrics(expected, transcribed)

        assert report["lines"][0]["best_ratio"] == 1.0

    def test_empty_expected_lines_are_skipped_and_counted(self):
        expected = ["hello world", "", "   ", "!!!"]
        transcribed = "hello world"
        report = match_lyrics(expected, transcribed)

        assert report["n_expected_lines"] == 4
        assert report["n_skipped_empty_lines"] == 3
        assert len(report["lines"]) == 1
        assert report["lines"][0]["expected"] == "hello world"

    def test_best_match_considers_full_text_not_only_per_line(self):
        # The expected line spans what whisper split into two segments/lines;
        # matching only per-line would score low, but matching against the
        # full joined text should score much better.
        expected = ["hello world second line"]
        transcribed = "hello world\nsecond line"
        report = match_lyrics(expected, transcribed)

        assert report["lines"][0]["best_ratio"] == 1.0
        assert report["lines"][0]["best_match"] == transcribed

    def test_line_contained_in_single_long_segment_scores_one_english(self):
        # Real-inference regression: whisper returned ONE long segment, so
        # per-line candidates reduced to the full text and the plain ratio
        # length-penalized a perfectly-sung line (0.38 while
        # overall_similarity was 1.0). Containment must score ~1.0.
        expected = ["hello world"]
        transcribed = "the singer began softly hello world and then kept going for a while"
        report = match_lyrics(expected, transcribed)

        assert report["lines"][0]["best_ratio"] == 1.0
        # overall_similarity stays a plain global-agreement ratio, so the
        # surrounding extra text keeps it well below 1.0 here.
        assert report["overall_similarity"] < 1.0

    def test_line_contained_in_single_long_segment_scores_one_japanese(self):
        expected = ["こんにちは世界"]
        transcribed = "前奏のあとで、こんにちは世界、と歌ってからさらに続いた"
        report = match_lyrics(expected, transcribed)

        assert report["lines"][0]["best_ratio"] == 1.0
        assert report["overall_similarity"] < 1.0

    def test_overall_similarity_is_not_substring_tolerant(self):
        # The per-line partial ratio must NOT leak into overall_similarity:
        # a short expected sheet inside a much longer transcript keeps a low
        # global-agreement ratio even though the line itself scores 1.0.
        expected = ["hello world"]
        padding = "la " * 30
        transcribed = f"{padding}hello world {padding}"
        report = match_lyrics(expected, transcribed)

        assert report["lines"][0]["best_ratio"] == 1.0
        assert report["overall_similarity"] < 0.5

    def test_no_match_when_transcribed_text_is_empty(self):
        expected = ["hello world"]
        report = match_lyrics(expected, "")

        assert report["lines"][0]["best_ratio"] == 0.0
        assert report["lines"][0]["best_match"] == ""
        assert report["overall_similarity"] == 0.0

    def test_all_expected_lines_empty_yields_empty_lines_list(self):
        report = match_lyrics(["", "..."], "hello world")
        assert report["lines"] == []
        assert report["n_skipped_empty_lines"] == 2
        assert report["n_expected_lines"] == 2


class TestMatchLyricsOrder:
    """Order read (Codex P2 #149, round 6): per-line best_ratio stays
    order-free (presence); ordering is reported via the CHAR-OFFSET cursor
    (match_offset / out_of_order / matched_offset_sequence / order_ratio)
    within the normalized full transcript — segment-boundary independent.
    best_match_index / matched_index_sequence remain as diagnostics.
    Still no verdict.
    """

    def test_reversed_lines_in_single_segment_flag_out_of_order(self):
        # THE key case for the offset cursor: whisper returned ONE segment
        # containing both lines, reversed. Candidate-index granularity
        # would tie (same candidate) and miss the reversal; character
        # offsets separate them.
        expected = ["hello", "world"]
        transcribed = "world hello"
        report = match_lyrics(expected, transcribed)

        assert report["lines"][0]["best_ratio"] == 1.0
        assert report["lines"][1]["best_ratio"] == 1.0
        # Normalized full transcript is "worldhello": hello @5, world @0.
        assert report["lines"][0]["match_offset"] == 5
        assert report["lines"][1]["match_offset"] == 0
        assert report["lines"][0]["out_of_order"] is False
        assert report["lines"][1]["out_of_order"] is True
        assert report["matched_offset_sequence"] == [5, 0]
        assert report["order_ratio"] == 0.0

    def test_reversed_transcript_flags_out_of_order(self):
        expected = ["hello", "world"]
        transcribed = "world\nhello"
        report = match_lyrics(expected, transcribed)

        # Presence read stays order-free: both lines exist somewhere.
        assert report["lines"][0]["best_ratio"] == 1.0
        assert report["lines"][1]["best_ratio"] == 1.0
        # Order read (offset cursor): "hello" sits at offset 5 of the
        # normalized full transcript "worldhello", then "world" regresses
        # the cursor to offset 0.
        assert report["lines"][0]["out_of_order"] is False
        assert report["lines"][1]["out_of_order"] is True
        assert report["matched_offset_sequence"] == [5, 0]
        assert report["order_ratio"] == 0.0
        # Index diagnostics still reflect the per-candidate view.
        assert report["matched_index_sequence"] == [1, 0]
        # The concatenation ratio also reflects the reversal.
        assert report["overall_similarity"] < 1.0

    def test_correct_order_reports_order_ratio_one(self):
        expected = ["hello world", "second line"]
        transcribed = "hello world\nsecond line"
        report = match_lyrics(expected, transcribed)

        assert all(line["out_of_order"] is False for line in report["lines"])
        assert report["matched_offset_sequence"] == [0, 10]
        assert report["matched_index_sequence"] == [0, 1]
        assert report["order_ratio"] == 1.0

    def test_correct_order_within_single_segment(self):
        expected = ["hello world", "second line"]
        transcribed = "hello world second line"
        report = match_lyrics(expected, transcribed)

        assert all(line["out_of_order"] is False for line in report["lines"])
        assert report["matched_offset_sequence"] == [0, 10]
        assert report["order_ratio"] == 1.0

    def test_single_line_degenerates_to_order_ratio_one(self):
        report = match_lyrics(["hello world"], "hello world")

        assert report["lines"][0]["best_match_index"] == 0
        assert report["lines"][0]["match_offset"] == 0
        assert report["lines"][0]["out_of_order"] is False
        assert report["matched_offset_sequence"] == [0]
        # Documented degenerate case: <= 1 match has no adjacent pairs.
        assert report["order_ratio"] == 1.0

    def test_no_match_yields_null_offsets_and_degenerate_order_ratio(self):
        report = match_lyrics(["hello world"], "")

        assert report["lines"][0]["best_match_index"] is None
        assert report["lines"][0]["match_offset"] is None
        assert report["lines"][0]["out_of_order"] is False
        assert report["matched_index_sequence"] == []
        assert report["matched_offset_sequence"] == []
        assert report["order_ratio"] == 1.0

    def test_repeated_identical_lines_tie_at_same_offset(self):
        # Known limitation (documented): identical repeated lines greedily
        # match the SAME best window (earliest offset wins on ties), so a
        # repeated chorus ties at one offset — a tie is not a regression
        # and order stats cannot distinguish the repetitions.
        expected = ["hello", "hello"]
        transcribed = "hello hello"
        report = match_lyrics(expected, transcribed)

        offsets = [line["match_offset"] for line in report["lines"]]
        assert offsets[0] == offsets[1] == 0
        assert all(line["out_of_order"] is False for line in report["lines"])
        assert report["order_ratio"] == 1.0

    def test_ties_on_same_candidate_are_not_regressions(self):
        # Two overlapping expected lines matching the same candidate: the
        # index diagnostics tie, while the offset cursor advances (0 -> 5)
        # — neither is a regression.
        expected = ["hello world second", "world second line"]
        transcribed = "hello world second line"
        report = match_lyrics(expected, transcribed)

        indexes = [line["best_match_index"] for line in report["lines"]]
        assert indexes[0] == indexes[1]
        assert report["matched_offset_sequence"] == [0, 5]
        assert all(line["out_of_order"] is False for line in report["lines"])
        assert report["order_ratio"] == 1.0
