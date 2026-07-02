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


def test_cross_validation_clap_and_mid_ratio_agree_on_direction():
    """相互検証①（docs/lyrics_semantic_anchor.md）: CLAP contrast と mid_ratio が
    条件レベルで同方向（present > absent）を指すことを、committed データ 2 系統の
    audio_sha256 突き合わせで pin する。どちらかのデータが変わったら再検証を強制。
    n=6 の方向一致確認であり統計的 validation ではない（順序・相関は主張しない）。
    """
    import yaml

    root = Path(__file__).resolve().parents[1]
    fixture = json.loads(
        (root / "examples" / "learned" / "clap" / "lyrics_vocal_contrast_fixture.json")
        .read_text(encoding="utf-8")
    )
    lyrics_log = yaml.safe_load(
        (
            root
            / "examples"
            / "real_audio_validation"
            / "lyrics_arrange_demo_2026-07-01.yaml"
        ).read_text(encoding="utf-8")
    )

    # 計測ログから sha256 -> take dict を回収（構造に寛容な再帰 walk）
    takes_by_sha: dict[str, dict] = {}

    def walk(node):
        if isinstance(node, dict):
            if "audio_sha256" in node:
                takes_by_sha[str(node["audio_sha256"])] = node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(lyrics_log)

    def find_mid_ratio(node):
        if isinstance(node, dict):
            if "mid_ratio" in node:
                return float(node["mid_ratio"])
            for value in node.values():
                found = find_mid_ratio(value)
                if found is not None:
                    return found
        return None

    # genre -> lyrics 条件 -> [(mid_ratio, contrast_fit)]
    by_genre: dict[str, dict[str, list[tuple[float, float]]]] = {}
    for sample in fixture["samples"]:
        take = takes_by_sha.get(str(sample["audio_sha256"]))
        assert take is not None, (
            f"sample {sample['sample_id']}: audio_sha256 が計測ログ側に見つからない"
            "（2 系統のデータのリンクが切れた＝相互検証の前提が崩れている）"
        )
        mid_ratio = find_mid_ratio(take)
        assert mid_ratio is not None, f"sample {sample['sample_id']}: mid_ratio 欠落"
        genre = str(sample["condition"]["genre"])
        lyrics = str(sample["condition"]["lyrics"])
        bucket = "present" if lyrics.startswith("present") else "absent"
        by_genre.setdefault(genre, {}).setdefault(bucket, []).append(
            (mid_ratio, float(sample["contrast_fit"]))
        )

    assert set(by_genre) == {"edm", "rock"}
    for genre, buckets in by_genre.items():
        assert set(buckets) == {"present", "absent"}, f"{genre}: 条件セル欠落"
        # 保守的規約: present 側は最小値で代表（n=3 追試 #124 と同じ）
        for axis, name in ((0, "mid_ratio"), (1, "clap_contrast_fit")):
            present_min = min(pair[axis] for pair in buckets["present"])
            absent_max = max(pair[axis] for pair in buckets["absent"])
            assert present_min > absent_max, (
                f"{genre}/{name}: present > absent の方向が崩れた — "
                "committed データが変わったなら docs の相互検証①を再検証すること"
            )
