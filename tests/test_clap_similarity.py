"""tests/test_clap_similarity.py — pure-numpy CLAP similarity instrument tests.

`similarity.py` imports no `laion_clap` / `torch` — these tests run against
plain float lists and must pass with neither dependency installed.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

import pytest

from scripts.collect_clap_fixture import load_fixture
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
    fixture = load_fixture(fixture_path)

    assert fixture["schema_version"] == "1.1"
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


def test_musicgen_k2_clap_fixture_learned_grip_direction_and_magnitude():
    """CLAP 相互検証② — MusicGen K2 バッチ (32 takes) の学習版 grip を pin する。

    docs/musicgen_backend.md §7.5: 学習版センサー（CLAP contrast_fit）は
    - bpm ノブを d≈2.60 の tight で分離（素朴 bpm センサーは halving 交絡で
      d=0.21 loose）— 「生成は効いていた・センサーが折った」の第三の独立証拠
    - brightness ノブを d≈1.50 の tight で分離（ルール版 centroid d=2.25 と方向一致・
      病理のない物理欄では物理センサーが優位）
    committed fixture が変われば数値 pin が落ち、docs §7.5 の再検証を強制する。
    """
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "learned"
        / "clap"
        / "musicgen_k2_contrast_fixture.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert fixture["schema_version"] == "1.1"
    assert fixture["model"]["checkpoint"] == "music_audioset_epoch_15_esc_90.14.pt"
    # #131 と同一 checkpoint（G4 済み cc0-1.0）に pin されていること
    assert fixture["model"]["checkpoint_sha256"] == (
        "fae3e9c087f2909c28a09dc31c8dfcdacbc42ba44c70e972b58c1bd1caf6dedd"
    )

    from svp_rpe.control import classify_grip, grip_effect_size

    by_condition: dict[tuple[str, str], list[float]] = {}
    for sample in fixture["samples"]:
        condition = sample["condition"]
        key = (condition["knob"], condition["level"])
        by_condition.setdefault(key, []).append(sample["contrast_fit"])
        # contrast_fit の自己整合（mean(pos) - mean(neg) の 6 桁丸め）
        pos = [sample["cosines"][p] for p in sample["prompt_groups"]["positive"]]
        neg = [sample["cosines"][p] for p in sample["prompt_groups"]["negative"]]
        expected_contrast = round(
            sum(pos) / len(pos) - sum(neg) / len(neg), 6
        )
        # cosines は 6 桁丸め後の値のため、丸め順序差で末桁が 1 ULP ずれうる
        assert sample["contrast_fit"] == pytest.approx(expected_contrast, abs=5e-6)

    assert all(len(values) == 8 for values in by_condition.values())

    bpm_grip = grip_effect_size(by_condition[("bpm", "90")], by_condition[("bpm", "170")])
    brightness_grip = grip_effect_size(
        by_condition[("brightness", "dark")], by_condition[("brightness", "bright")]
    )

    assert classify_grip(bpm_grip, 1) == "tight"
    assert classify_grip(brightness_grip, 1) == "tight"
    assert bpm_grip == pytest.approx(2.603, abs=0.01)
    assert brightness_grip == pytest.approx(1.501, abs=0.01)
    # bpm: 学習版は分布の重なりゼロ（低水準の最大 < 高水準の最小）
    assert max(by_condition[("bpm", "90")]) < min(by_condition[("bpm", "170")])


@pytest.mark.parametrize(
    ("fixture_name", "expected_d", "expected_classification"),
    [
        ("musicgen_segments_semantic_core_contrast_fixture.json", 1.895555, "tight"),
        # valence 軸は方向は正だが energy より弱く loose 域（v1.1 の初実地読み・探索位置づけ）。
        ("musicgen_segments_semantic_core_valence_contrast_fixture.json", 0.600649, "loose"),
    ],
)
def test_k2_seg_semantic_core_clap_fixture_schema_and_self_consistency(
    fixture_name: str, expected_d: float, expected_classification: str
) -> None:
    """K2-seg（2026-07-05）CLAP 第二センサー fixture（energy / valence 軸）のスキーマ
    妥当性 + 自己整合（`docs/musicgen_backend.md` §7.6・`scratchpad/k2seg_full/
    clap_semantic_core_stats.yaml` 転記元）。既存 §7.5 (`musicgen_k2_contrast_fixture.json`)
    のテスト様式を踏襲する。
    """
    fixture_path = (
        Path(__file__).resolve().parents[1] / "examples" / "learned" / "clap" / fixture_name
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert fixture["schema_version"] == "1.1"
    assert fixture["model"]["checkpoint"] == "music_audioset_epoch_15_esc_90.14.pt"
    assert fixture["model"]["checkpoint_sha256"] == (
        "fae3e9c087f2909c28a09dc31c8dfcdacbc42ba44c70e972b58c1bd1caf6dedd"
    )
    assert len(fixture["samples"]) == 16

    from svp_rpe.control import classify_grip, grip_effect_size

    by_level: dict[str, list[float]] = {}
    for sample in fixture["samples"]:
        condition = sample["condition"]
        assert condition["knob"] == "semantic_core"
        by_level.setdefault(condition["level"], []).append(sample["contrast_fit"])
        groups = sample["prompt_groups"]
        cosines = sample["cosines"]
        assert set(groups) == {"positive", "negative"}
        pos = [cosines[prompt] for prompt in groups["positive"]]
        neg = [cosines[prompt] for prompt in groups["negative"]]
        expected_contrast = round(sum(pos) / len(pos) - sum(neg) / len(neg), 6)
        assert sample["contrast_fit"] == pytest.approx(expected_contrast, abs=5e-6)

    assert set(by_level) == {"calm", "euphoric"}
    assert all(len(values) == 8 for values in by_level.values())

    d = grip_effect_size(by_level["calm"], by_level["euphoric"])
    assert d == pytest.approx(expected_d, abs=1e-5)
    assert classify_grip(d, 1) == expected_classification


def test_v2_symmetric_block_clap_fixture_links_to_physical_measurements_and_pins_direction():
    """CLAP ③ closeout（2026-07-08 対称ブロック）の整合性テスト。

    `test_cross_validation_clap_and_mid_ratio_agree_on_direction` と同じ pattern を
    v2 データ（instrumental alt 込み n≥2×2 セル）に適用する:

    1. CLAP v2 fixture（examples/learned/clap/lyrics_vocal_contrast_v2_fixture.json）と
       物理 measured ログ（examples/real_audio_validation/
       lyrics_symmetric_block_2026-07-08.yaml）を同一 sample_id 間で audio_sha256 が
       一致することを検証する（2 系統のデータが同一バイト列を計測している保証）。
    2. 各ジャンルで min(present の contrast_fit) > max(absent の contrast_fit) を pin する
       （docs/lyrics_semantic_anchor.md「2026-07-08 対称ブロック」節: CLAP vocal contrast
       は両ジャンルで完全分離＝mid_ratio 棄却に対し充足したセンサー）。

    committed データが変わったらこのテストが落ち、docs の再検証を強制する。
    """
    import yaml

    root = Path(__file__).resolve().parents[1]
    fixture = json.loads(
        (root / "examples" / "learned" / "clap" / "lyrics_vocal_contrast_v2_fixture.json")
        .read_text(encoding="utf-8")
    )
    physical_log = yaml.safe_load(
        (
            root
            / "examples"
            / "real_audio_validation"
            / "lyrics_symmetric_block_2026-07-08.yaml"
        ).read_text(encoding="utf-8")
    )

    physical_by_id = {take["id"]: take for take in physical_log["takes"]}
    assert len(fixture["samples"]) == 8

    by_genre: dict[str, dict[str, list[float]]] = {}
    for sample in fixture["samples"]:
        sample_id = sample["sample_id"]
        take = physical_by_id.get(sample_id)
        assert take is not None, (
            f"sample {sample_id}: 物理 measured ログ側に同一 id が見つからない"
            "（2 系統のデータのリンクが切れた＝整合性の前提が崩れている）"
        )
        assert sample["audio_sha256"] == take["audio_sha256"], (
            f"sample {sample_id}: CLAP fixture と物理ログの audio_sha256 が不一致"
        )

        genre = str(sample["condition"]["genre"])
        lyrics = str(sample["condition"]["lyrics"])
        bucket = "present" if lyrics.startswith("present") else "absent"
        by_genre.setdefault(genre, {}).setdefault(bucket, []).append(
            float(sample["contrast_fit"])
        )

    assert set(by_genre) == {"EDM", "Rock"}
    for genre, buckets in by_genre.items():
        assert set(buckets) == {"present", "absent"}, f"{genre}: 条件セル欠落"
        assert len(buckets["present"]) == 2 and len(buckets["absent"]) == 2, (
            f"{genre}: n≥2×2 セルが崩れている"
        )
        present_min = min(buckets["present"])
        absent_max = max(buckets["absent"])
        assert present_min > absent_max, (
            f"{genre}: CLAP vocal contrast の present > absent 完全分離が崩れた — "
            "committed データが変わったなら docs の 2026-07-08 節を再検証すること"
        )
