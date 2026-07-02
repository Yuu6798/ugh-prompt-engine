"""tests/test_clap_similarity.py — pure-numpy CLAP similarity instrument tests.

`similarity.py` imports no `laion_clap` / `torch` — these tests run against
plain float lists and must pass with neither dependency installed.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

import pytest

from svp_rpe.rpe.learned.similarity import (
    contrast_fit,
    cosine_similarity,
    prompt_audio_fit,
)


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_zero_vector_is_zero_safe(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0
        assert cosine_similarity([1.0, 2.0], [0.0, 0.0]) == 0.0
        assert cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0

    def test_near_zero_norm_is_zero_safe(self):
        assert cosine_similarity([1e-13, 0.0], [1.0, 1.0]) == 0.0

    def test_result_clamped_to_valid_range(self):
        a = [1.0, 1e-16]
        b = [1.0, -1e-16]
        result = cosine_similarity(a, b)
        assert -1.0 <= result <= 1.0


class TestPromptAudioFit:
    def test_returns_one_cosine_per_text(self):
        audio = [1.0, 0.0]
        texts = [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]]
        fits = prompt_audio_fit(audio, texts)
        assert fits == pytest.approx([1.0, 0.0, -1.0])

    def test_empty_text_list_returns_empty(self):
        assert prompt_audio_fit([1.0, 0.0], []) == []


class TestContrastFit:
    def test_positive_minus_negative_mean(self):
        audio = [1.0, 0.0]
        positives = [[1.0, 0.0], [1.0, 0.0]]  # cos = 1.0, 1.0
        negatives = [[-1.0, 0.0]]  # cos = -1.0
        assert contrast_fit(audio, positives, negatives) == pytest.approx(1.0 - (-1.0))

    def test_empty_positive_raises(self):
        with pytest.raises(ValueError, match="positive_texts_emb"):
            contrast_fit([1.0, 0.0], [], [[0.0, 1.0]])

    def test_empty_negative_raises(self):
        with pytest.raises(ValueError, match="negative_texts_emb"):
            contrast_fit([1.0, 0.0], [[1.0, 0.0]], [])

    def test_deterministic_on_fixture_like_float_lists(self):
        audio = [0.12, 0.98, -0.15]
        positives = [[0.1, 0.9, -0.1], [0.2, 0.95, -0.2]]
        negatives = [[-0.9, 0.1, 0.4]]
        first = contrast_fit(audio, positives, negatives)
        second = contrast_fit(audio, positives, negatives)
        assert first == second


def test_real_clap_fixture_cosines_and_contrast_are_self_consistent():
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "learned"
        / "clap"
        / "lyrics_vocal_contrast_fixture.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert fixture["schema_version"] == "1.0"
    assert fixture["model"]["name"] == "laion_clap"
    assert fixture["manifest"] == "examples/learned/clap/lyrics_vocal_contrast_manifest.yaml"
    assert fixture["model"]["checkpoint"] == "music_audioset_epoch_15_esc_90.14.pt"
    checkpoint_sha256 = fixture["model"]["checkpoint_sha256"]
    assert len(checkpoint_sha256) == 64
    int(checkpoint_sha256, 16)
    assert (
        fixture["model"]["info"][0]["weights_license"]
        == "Hugging Face repository-level license badge: cc0-1.0; "
        "no additional checkpoint-specific license text found in the empty model card "
        "(verified 2026-07-02, PR2b-2)"
    )
    assert "C:\\Users\\" not in fixture_path.read_text(encoding="utf-8")
    assert len(fixture["samples"]) == 6

    for sample in fixture["samples"]:
        assert sample["audio_embedding"]
        assert sample["audio_path"] == (
            f"../../roundtrip/cache/pr2b-2-clap/{sample['sample_id']}.mp3"
        )
        groups = sample["prompt_groups"]
        cosines = sample["cosines"]
        assert set(groups) == {"positive", "negative"}

        for score in cosines.values():
            assert -1.0 <= score <= 1.0

        pos = [cosines[prompt] for prompt in groups["positive"]]
        neg = [cosines[prompt] for prompt in groups["negative"]]
        expected_contrast = round(mean(pos) - mean(neg), 6)
        assert sample["contrast_fit"] == pytest.approx(expected_contrast, abs=2e-6)
