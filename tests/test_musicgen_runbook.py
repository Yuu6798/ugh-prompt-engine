"""tests/test_musicgen_runbook.py — collect_musicgen_takes.py smoke tests.

The module MUST import cleanly without torch/transformers installed
(guarded import inside `generate_takes`/`_import_musicgen_stack`, never at
module top level) — CI never installs the `musicgen` extra. Plan
validation and seed derivation are pure logic and stay in the fast test
loop; the `extract` path performs a real RPE extraction on synthesized
audio and is marked `slow` per-test.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import runpy
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from pydantic import ValidationError

from scripts.collect_musicgen_takes import (
    GenerationPlan,
    extract_fixture,
    generator_label,
    iter_plan_takes,
    load_plan,
    load_takes_manifest,
    main,
    merge_takes_manifests,
    plan_chunk_manifest_stub,
    profile_scope_advisory,
    resolve_perform_target,
    sample_seed,
    validate_merge_compatibility,
)

PLAN_PATH = Path("examples/control/k2_musicgen/plan.yaml")
SEGMENTS_PLAN_PATH = Path("examples/control/k2_musicgen_segments/plan.yaml")
SCORE_PATH = Path("examples/roundtrip/synth_01_source.yaml")

# K2-seg 全セル共通のベース文言（compose の結合様式 ". " でセグメントを追記する）
_SEGMENTS_BASE = (
    "instrumental electronic music, steady four-on-the-floor drum beat, "
    "at 120 beats per minute"
)


def test_profile_scope_advisory_none_for_measured_model_and_revision() -> None:
    """実測済みモデル + 実測 revision の組でのみ advisory を出さない。"""
    assert (
        profile_scope_advisory(
            "facebook/musicgen-small", "4c8334b02c6ec4e8664a91979669a501ec497792"
        )
        is None
    )


def test_profile_scope_advisory_fires_for_unmeasured_variant() -> None:
    """未計測バリアントでは device profile / grip の実測スコープ外を知らせる。

    Codex #136 P2: `device_profiles/musicgen.yaml` は backend seam でキーされ、
    コンパイル側は演奏モデルの粒度を知らない。モデル id を知る唯一の場所である
    runbook が stderr 向け文言を返す（生成・プロンプト本文は不変）。
    """
    advisory = profile_scope_advisory("facebook/musicgen-medium", None)
    assert advisory is not None
    assert "facebook/musicgen-small" in advisory
    assert "facebook/musicgen-medium" in advisory
    assert "device_profiles/musicgen.yaml" in advisory


def test_profile_scope_advisory_fires_for_unpinned_revision() -> None:
    """model id が一致しても revision 未 pin（None）は HF head ドリフトを知らせる。

    Codex #136 P2（第2ラウンド）: fixture / profile は revision 4c8334b0… の実測。
    未 pin 実行は現行 head に解決され実測スコープを外れうるため、pin を促す。
    """
    advisory = profile_scope_advisory("facebook/musicgen-small", None)
    assert advisory is not None
    assert "--model-revision 4c8334b02c6ec4e8664a91979669a501ec497792" in advisory


def test_profile_scope_advisory_fires_for_different_revision() -> None:
    """model id が一致しても別 revision は実測スコープ外として知らせる。"""
    advisory = profile_scope_advisory("facebook/musicgen-small", "deadbeef")
    assert advisory is not None
    assert "deadbeef" in advisory
    assert "4c8334b0" in advisory


def test_generator_label_follows_requested_model_id() -> None:
    """Codex #135 P2: manifest の generator ラベルは要求モデルから導出する。

    medium/large の反復バッチが `musicgen-small` 名義でレポートされると
    機種間比較（device profile / grip の帰属）が汚れる。
    """
    assert generator_label("facebook/musicgen-small") == "musicgen-small"
    assert generator_label("facebook/musicgen-medium") == "musicgen-medium"
    assert generator_label("facebook/musicgen-large") == "musicgen-large"
    # 名前空間なしの model_id はそのままラベルになる
    assert generator_label("musicgen-stereo-small") == "musicgen-stereo-small"


def test_module_imports_without_torch() -> None:
    """Importing the module (or running it via runpy) must not require torch."""
    module = importlib.import_module("scripts.collect_musicgen_takes")
    assert hasattr(module, "generate_takes")
    assert hasattr(module, "extract_fixture")

    # runpy re-executes the module body from scratch, independent of any
    # cached sys.modules entry — this is the strongest "imports cleanly"
    # check available without actually uninstalling torch in this env.
    namespace = runpy.run_path(str(Path("scripts/collect_musicgen_takes.py")))
    assert "generate_takes" in namespace
    assert "extract_fixture" in namespace


def test_plan_yaml_loads() -> None:
    plan = load_plan(PLAN_PATH)
    assert isinstance(plan, GenerationPlan)
    assert plan.fixture_id == "k2_musicgen_mini"
    assert plan.model_id == "facebook/musicgen-small"
    assert plan.repetitions == 8
    assert len(plan.knobs) == 2
    names = {knob.name for knob in plan.knobs}
    assert names == {"bpm", "brightness"}


def test_plan_knob_kind_defaults_to_effect_size() -> None:
    """K2 既存 plan（kind 記載なし）のノブは既定 kind="effect_size" にフォールバック。"""
    plan = load_plan(PLAN_PATH)
    assert all(knob.kind == "effect_size" for knob in plan.knobs)


def test_k2_segments_plan_yaml_loads_with_verbatim_prompts() -> None:
    """K2-seg plan: 5 ノブ・pin 済み revision・compose 文言 verbatim（設計メモのノブ表）。"""
    plan = load_plan(SEGMENTS_PLAN_PATH)
    assert plan.fixture_id == "k2_musicgen_segments"
    assert plan.model_id == "facebook/musicgen-small"
    assert plan.model_revision == "4c8334b02c6ec4e8664a91979669a501ec497792"
    assert plan.duration_seconds == 12.0
    assert plan.guidance_scale == 3.0
    assert plan.repetitions == 8

    by_name = {knob.name: knob for knob in plan.knobs}
    assert set(by_name) == {
        "active_rate_target",
        "valley_depth_target",
        "semantic_avoid",
        "semantic_core",
        "time_signature",
    }

    # categorical は time_signature のみ、他は既定 effect_size
    assert by_name["time_signature"].kind == "categorical"
    for name in ("active_rate_target", "valley_depth_target", "semantic_avoid", "semantic_core"):
        assert by_name[name].kind == "effect_size"

    # 文言 verbatim: ベース + ". " 区切りのセグメント追記（folded scalar の
    # 改行アーティファクトや二重スペースが混入していないことを全文一致で確認）
    assert by_name["active_rate_target"].prompt_low == f"{_SEGMENTS_BASE}. Active rate 0.55."
    assert by_name["active_rate_target"].prompt_high == f"{_SEGMENTS_BASE}. Active rate 0.92."
    assert by_name["valley_depth_target"].prompt_low == f"{_SEGMENTS_BASE}. Valley depth 0.15."
    assert by_name["valley_depth_target"].prompt_high == f"{_SEGMENTS_BASE}. Valley depth 0.70."
    # semantic_avoid の low は「ベースのみ」（存在 A/B）
    assert by_name["semantic_avoid"].prompt_low == _SEGMENTS_BASE
    assert (
        by_name["semantic_avoid"].prompt_high
        == f"{_SEGMENTS_BASE}. Avoid: bright shimmering sparkling highs."
    )
    assert by_name["semantic_avoid"].expected_sign == -1
    assert (
        by_name["semantic_core"].prompt_low
        == f"{_SEGMENTS_BASE}. Calm introspective melancholic night atmosphere."
    )
    assert (
        by_name["semantic_core"].prompt_high
        == f"{_SEGMENTS_BASE}. Euphoric energetic festival atmosphere."
    )
    assert by_name["semantic_core"].sensor == "onset_density"
    assert by_name["time_signature"].prompt_low == f"{_SEGMENTS_BASE}. 4/4 time."
    assert by_name["time_signature"].prompt_high == f"{_SEGMENTS_BASE}. 3/4 time."
    assert by_name["time_signature"].low_level == "4/4"
    assert by_name["time_signature"].high_level == "3/4"


def test_plan_missing_required_key_fails_fast(tmp_path: Path) -> None:
    raw = {
        "schema_version": "1.0",
        "fixture_id": "broken",
        "generator": "musicgen-small",
        "model_id": "facebook/musicgen-small",
        # duration_seconds intentionally omitted
        "guidance_scale": 3.0,
        "repetitions": 2,
        "knobs": [],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_plan(plan_path)


def test_plan_unknown_key_fails_fast(tmp_path: Path) -> None:
    raw = {
        "schema_version": "1.0",
        "fixture_id": "broken",
        "generator": "musicgen-small",
        "model_id": "facebook/musicgen-small",
        "duration_seconds": 12.0,
        "guidance_scale": 3.0,
        "repetitions": 2,
        "knobs": [],
        "unexpected_field": "nope",
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_plan(plan_path)


def test_knob_unknown_key_fails_fast(tmp_path: Path) -> None:
    raw = {
        "schema_version": "1.0",
        "fixture_id": "broken",
        "generator": "musicgen-small",
        "model_id": "facebook/musicgen-small",
        "duration_seconds": 12.0,
        "guidance_scale": 3.0,
        "repetitions": 2,
        "knobs": [
            {
                "name": "bpm",
                "sensor": "bpm",
                "low_level": "90",
                "high_level": "170",
                "expected_sign": 1,
                "prompt_low": "a",
                "prompt_high": "b",
                "surprise": True,
            }
        ],
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_plan(plan_path)


def test_sample_seed_is_deterministic() -> None:
    assert sample_seed(0, 0, 0) == sample_seed(0, 0, 0)
    assert sample_seed(1, 1, 3) == sample_seed(1, 1, 3)


def test_sample_seed_unique_across_all_combinations() -> None:
    seeds = set()
    for knob_index in range(4):
        for level_index in range(2):
            for repeat in range(8):
                seed = sample_seed(knob_index, level_index, repeat)
                assert seed not in seeds, (knob_index, level_index, repeat)
                seeds.add(seed)


_SR = 22050


def _click_track(*, duration: float, bpm: float, sample_rate: int = _SR) -> np.ndarray:
    """A percussive click track at a known BPM, so librosa's beat tracker
    reports a finite tempo instead of ``None`` (a plain sine tone is too
    stationary for tempo detection — see `tests/test_spectral_bands.py`).
    """
    y = np.zeros(int(sample_rate * duration), dtype=np.float32)
    interval = 60.0 / bpm
    pulse = np.hanning(64).astype(np.float32)
    for time in np.arange(0.0, duration, interval):
        start = int(time * sample_rate)
        end = min(start + pulse.size, y.size)
        y[start:end] += pulse[: end - start]
    return y


def _write_wav(
    path: Path, *, freq: float, bpm: float = 120.0, seconds: float = 8.0, sample_rate: int = _SR
) -> None:
    t = np.linspace(0, seconds, int(round(seconds * sample_rate)), endpoint=False)
    tone = (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    y = _click_track(duration=seconds, bpm=bpm, sample_rate=sample_rate) + tone
    sf.write(str(path), y, sample_rate)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_takes_manifest(audio_dir: Path) -> dict:
    low_path = audio_dir / "bpm_low_00.wav"
    high_path = audio_dir / "bpm_high_00.wav"
    _write_wav(low_path, freq=220.0)
    _write_wav(high_path, freq=440.0)

    return {
        "schema_version": "1.0",
        "fixture_id": "k2_musicgen_mini",
        "generator": "musicgen-small",
        "plan": {
            "schema_version": "1.0",
            "fixture_id": "k2_musicgen_mini",
            "generator": "musicgen-small",
            "model_id": "facebook/musicgen-small",
            "model_revision": None,
            "duration_seconds": 12.0,
            "guidance_scale": 3.0,
            "repetitions": 1,
            "knobs": [
                {
                    "name": "bpm",
                    "sensor": "bpm",
                    "low_level": "90",
                    "high_level": "170",
                    "expected_sign": 1,
                    "prompt_low": "low prompt",
                    "prompt_high": "high prompt",
                }
            ],
        },
        "samples": [
            {
                "sample_id": "bpm_low_00",
                "knob": "bpm",
                "level": "90",
                "repeat": 0,
                "seed": sample_seed(0, 0, 0),
                "prompt": "low prompt",
                "audio_path": "bpm_low_00.wav",
                "audio_sha256": _sha256(low_path),
                "model_id": "facebook/musicgen-small",
                "model_revision": None,
                "duration_seconds": 12.0,
                "guidance_scale": 3.0,
            },
            {
                "sample_id": "bpm_high_00",
                "knob": "bpm",
                "level": "170",
                "repeat": 0,
                "seed": sample_seed(0, 1, 0),
                "prompt": "high prompt",
                "audio_path": "bpm_high_00.wav",
                "audio_sha256": _sha256(high_path),
                "model_id": "facebook/musicgen-small",
                "model_revision": None,
                "duration_seconds": 12.0,
                "guidance_scale": 3.0,
            },
        ],
    }


@pytest.mark.slow
def test_extract_fixture_matches_k2_schema(tmp_path: Path) -> None:
    manifest = _build_takes_manifest(tmp_path)

    fixture = extract_fixture(manifest, audio_dir=tmp_path)

    assert fixture["schema_version"] == "1.0"
    assert fixture["fixture_id"] == "k2_musicgen_mini"
    assert fixture["generator"] == "musicgen-small"
    assert fixture["repetitions"] == 1
    assert fixture["knobs"] == [
        {
            "name": "bpm",
            "sensor": "bpm",
            "low_level": "90",
            "high_level": "170",
            "expected_sign": 1,
        }
    ]
    assert len(fixture["samples"]) == 2
    for sample in fixture["samples"]:
        assert set(sample) == {"sample_id", "knob", "level", "audio_sha256", "features"}
        features = sample["features"]
        assert set(features) == {
            "bpm",
            "key",
            "spectral_centroid",
            "spectral_profile",
            "active_rate",
            "valley_depth",
            # K2-seg: compose プロンプト欄 grip スクリーン用の追加 3 キー
            "onset_density",
            "time_signature",
            "time_signature_confidence",
        }
        assert "brightness" in features["spectral_profile"]
        assert isinstance(features["onset_density"], float)
        assert isinstance(features["time_signature"], str)
        assert "/" in features["time_signature"]
        assert 0.0 <= features["time_signature_confidence"] <= 1.0


@pytest.mark.slow
def test_extract_fixture_is_readable_by_measure_grip(tmp_path: Path) -> None:
    from scripts.measure_grip import analyze_fixture

    manifest = _build_takes_manifest(tmp_path)
    fixture = extract_fixture(manifest, audio_dir=tmp_path)

    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    reloaded = json.loads(fixture_path.read_text(encoding="utf-8"))

    # 1 repetition per level is enough to exercise the parsing path even
    # though grip_effect_size on n=1 is not statistically meaningful.
    report = analyze_fixture(reloaded)
    assert report["fixture_id"] == "k2_musicgen_mini"
    assert len(report["results"]) == 1
    assert report["results"][0]["knob"] == "bpm"


def test_extract_fixture_propagates_categorical_kind() -> None:
    """plan の knob に kind があれば fixture の knob spec へ透過し、無ければ付与しない。

    samples を空にすると音声抽出を伴わず knob spec の組み立てだけを検証できる
    （高速ループ）。既存 K2 系 manifest（kind 記載なし）の fixture スキーマが
    従来どおり 5 キーのままであることも同時に固定する。
    """
    manifest = {
        "schema_version": "1.0",
        "fixture_id": "kind_passthrough",
        "generator": "musicgen-small",
        "plan": {
            "schema_version": "1.0",
            "fixture_id": "kind_passthrough",
            "generator": "musicgen-small",
            "model_id": "facebook/musicgen-small",
            "model_revision": None,
            "duration_seconds": 12.0,
            "guidance_scale": 3.0,
            "repetitions": 8,
            "knobs": [
                {
                    "name": "time_signature",
                    "sensor": "time_signature",
                    "kind": "categorical",
                    "low_level": "4/4",
                    "high_level": "3/4",
                    "expected_sign": 0,
                    "prompt_low": "a",
                    "prompt_high": "b",
                },
                {
                    "name": "bpm",
                    "sensor": "bpm",
                    "low_level": "90",
                    "high_level": "170",
                    "expected_sign": 1,
                    "prompt_low": "a",
                    "prompt_high": "b",
                },
            ],
        },
        "samples": [],
    }

    fixture = extract_fixture(manifest, audio_dir=Path("/nonexistent-unused"))

    assert fixture["knobs"] == [
        {
            "name": "time_signature",
            "sensor": "time_signature",
            "low_level": "4/4",
            "high_level": "3/4",
            "expected_sign": 0,
            "kind": "categorical",
        },
        {
            "name": "bpm",
            "sensor": "bpm",
            "low_level": "90",
            "high_level": "170",
            "expected_sign": 1,
        },
    ]


def test_extract_fixture_fails_fast_on_sha256_mismatch(tmp_path: Path) -> None:
    manifest = _build_takes_manifest(tmp_path)
    manifest["samples"][0]["audio_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="audio_sha256 does not match"):
        extract_fixture(manifest, audio_dir=tmp_path)


def test_load_takes_manifest_round_trips(tmp_path: Path) -> None:
    manifest = _build_takes_manifest(tmp_path)
    manifest_path = tmp_path / "takes_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    loaded = load_takes_manifest(manifest_path)

    assert loaded["fixture_id"] == "k2_musicgen_mini"
    assert len(loaded["samples"]) == 2


# ---------------------------------------------------------------------------
# チャンク分割実行（--only-level / --only-repeat / --append、torch 不要の純ロジック）
# ---------------------------------------------------------------------------


def test_iter_plan_takes_filters_preserve_absolute_seeds() -> None:
    """フィルタは列挙の部分集合であり、seed / インデックスは一括実行と同一。

    チャンク分割（バッチ M1 の 1 クリップ 1 コール実行）が一括実行と同じ
    (sample_id, seed) 系列を作ることの根拠。
    """
    plan = load_plan(SEGMENTS_PLAN_PATH)

    full = {
        (take.knob.name, take.level_token, take.repeat): take.seed
        for take in iter_plan_takes(plan)
    }
    assert len(full) == len(plan.knobs) * 2 * plan.repetitions

    filtered = list(iter_plan_takes(plan, only_level="high", only_repeat=3))
    assert len(filtered) == len(plan.knobs)
    for take in filtered:
        assert take.level_token == "high"
        assert take.repeat == 3
        assert take.seed == full[(take.knob.name, "high", 3)]
        assert take.seed == sample_seed(take.knob_index, 1, 3)


def test_iter_plan_takes_rejects_invalid_filters() -> None:
    plan = load_plan(SEGMENTS_PLAN_PATH)

    with pytest.raises(ValueError, match="only_level"):
        list(iter_plan_takes(plan, only_level="mid"))
    with pytest.raises(ValueError, match="only_repeat"):
        list(iter_plan_takes(plan, only_repeat=plan.repetitions))
    with pytest.raises(ValueError, match="only_repeat"):
        list(iter_plan_takes(plan, only_repeat=-1))


# チャンク系テスト共通の pin 済み revision（--append は未 pin plan を生成前に
# 拒否するため、append 系シナリオのヘルパーは pin 済みを既定にする）。
_CHUNK_REVISION = "cafef00d5eed5eed5eed5eed5eed5eed5eed5eed"


def _chunk_manifest(samples: list[dict]) -> dict:
    """merge テスト用の最小チャンク manifest（ヘッダ共通・samples のみ差替え）。"""
    return {
        "schema_version": "1.0",
        "fixture_id": "m1_chunks",
        "generator": "musicgen-small",
        "plan": {
            "schema_version": "1.0",
            "fixture_id": "m1_chunks",
            "generator": "musicgen-small",
            "model_id": "facebook/musicgen-small",
            "model_revision": _CHUNK_REVISION,
            "duration_seconds": 30.6,
            "guidance_scale": 3.0,
            "repetitions": 2,
            "knobs": [
                {
                    "name": "structure",
                    "sensor": "rms_section_pattern",
                    "low_level": "low",
                    "high_level": "high",
                    "expected_sign": 1,
                    "prompt_low": "a",
                    "prompt_high": "b",
                    # load_plan().model_dump() は既定 kind を明示するため、
                    # 実 generate_takes が書くヘッダと同形にしておく
                    # （--append 事前検証のヘッダ一致テストが実物と同じ形で走る）。
                    "kind": "effect_size",
                }
            ],
        },
        "samples": samples,
    }


def _chunk_sample(level_token: str, repeat: int) -> dict:
    level_index = 0 if level_token == "low" else 1
    return {
        "sample_id": f"structure_{level_token}_{repeat:02d}",
        "knob": "structure",
        "level": level_token,
        "repeat": repeat,
        "seed": sample_seed(0, level_index, repeat),
        "prompt": "a" if level_token == "low" else "b",
        "audio_path": f"structure_{level_token}_{repeat:02d}.wav",
        "audio_sha256": "0" * 64,
        "model_id": "facebook/musicgen-small",
        "model_revision": _CHUNK_REVISION,
        "duration_seconds": 30.6,
        "guidance_scale": 3.0,
    }


def test_merge_takes_manifests_restores_canonical_order_regardless_of_chunk_order() -> None:
    """逆順チャンク合流でも、samples は一括実行と同じ正準順（level→repeat）になる。"""
    merged = merge_takes_manifests(
        _chunk_manifest([_chunk_sample("high", 1)]),
        _chunk_manifest([_chunk_sample("low", 0)]),
    )
    merged = merge_takes_manifests(merged, _chunk_manifest([_chunk_sample("high", 0)]))
    merged = merge_takes_manifests(merged, _chunk_manifest([_chunk_sample("low", 1)]))

    assert [sample["sample_id"] for sample in merged["samples"]] == [
        "structure_low_00",
        "structure_low_01",
        "structure_high_00",
        "structure_high_01",
    ]


def test_merge_takes_manifests_fails_fast_on_duplicate_or_plan_mismatch() -> None:
    base = _chunk_manifest([_chunk_sample("low", 0)])

    with pytest.raises(ValueError, match="duplicate sample_id"):
        merge_takes_manifests(base, _chunk_manifest([_chunk_sample("low", 0)]))

    other_plan = _chunk_manifest([_chunk_sample("high", 0)])
    other_plan["plan"] = dict(other_plan["plan"], duration_seconds=12.0)
    with pytest.raises(ValueError, match="plan differs"):
        merge_takes_manifests(base, other_plan)


def test_merge_rejects_mixed_sample_model_revisions() -> None:
    """サンプルレベルの解決済み model_revision が混合する manifest の統合を拒否する
    （#171 P2 3巡目: 宣言 plan が同一（例: 両方 null 宣言）でも、実行間で HF が
    異なるコミットへ解決したチャンク同士が 1 fixture に暗黙混合され grip 計測を
    汚染する経路を、merged manifest を書く前に塞ぐ多重防御）。"""
    base = _chunk_manifest([_chunk_sample("low", 0)])
    drifted = _chunk_manifest([_chunk_sample("high", 0)])
    drifted["samples"][0]["model_revision"] = "0123456789abcdef0123456789abcdef01234567"

    with pytest.raises(ValueError, match="mixed resolved model_id/model_revision"):
        merge_takes_manifests(base, drifted)

    # model_id の混合も同様に拒否される。
    drifted_id = _chunk_manifest([_chunk_sample("high", 0)])
    drifted_id["samples"][0]["model_id"] = "facebook/musicgen-medium"
    with pytest.raises(ValueError, match="mixed resolved model_id/model_revision"):
        merge_takes_manifests(base, drifted_id)

    # 単一 revision（全サンプル一致）は従来どおり統合できる（回帰）。
    merged = merge_takes_manifests(base, _chunk_manifest([_chunk_sample("high", 0)]))
    assert len(merged["samples"]) == 2


# ---------------------------------------------------------------------------
# `--append` の生成前検証（#171 P2 採用: WAV を 1 byte も書く前に fail-fast）
# ---------------------------------------------------------------------------


def _write_append_scene(tmp_path: Path, existing_manifest: dict) -> tuple[Path, Path, Path]:
    """plan ファイル + 既存 manifest + 未作成 output_dir を配置する（torch 不要）。

    plan ファイルの内容は `_chunk_manifest` のヘッダ `plan` と同一 —
    `plan_chunk_manifest_stub` のヘッダが既存 manifest と一致する正常系を基準に、
    テスト側で既存 manifest を汚して違反ケースを作る。
    """
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_chunk_manifest([])["plan"]), encoding="utf-8")
    manifest_out = tmp_path / "manifest.json"
    manifest_out.write_text(
        json.dumps(existing_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output_dir = tmp_path / "takes"  # 生成前 fail-fast なら作成すらされない
    return plan_path, manifest_out, output_dir


def _run_generate_append(
    plan_path: Path, output_dir: Path, manifest_out: Path, *, level: str, repeat: int
) -> int:
    return main(
        [
            "generate",
            "--plan", str(plan_path),
            "--output-dir", str(output_dir),
            "--manifest-out", str(manifest_out),
            "--only-level", level,
            "--only-repeat", str(repeat),
            "--append",
        ]
    )


@pytest.mark.parametrize(
    "revision",
    [
        None,  # 未 pin: HF 現行 head へ解決
        "main",  # 可動 branch ref: チャンク間で別コミットへ解決し得る
        "v1.0",  # 可動 tag ref: 同上（タグは付け替え可能）
        "4C8334B02C6EC4E8664A91979669A501EC497792",  # 大文字 hex は正規形でない
        "4c8334b0",  # 短縮ハッシュ: 不変性はあるが 40 桁の正規形を要求
    ],
)
def test_generate_append_requires_immutable_commit_hash_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    revision: str | None,
) -> None:
    """--append は不変コミットハッシュ（40 桁小文字 hex）の model_revision を必須と
    し、null だけでなく可動 ref（main / タグ等）も生成前に fail-fast する
    （#171 P2 4巡目: 可動 ref は非 None 文字列として null 拒否を素通りするが、
    チャンク実行間で別コミットへ解決し revision 混合を生む — 検出が生成後の
    サンプルレベル検査だけでは「音声を書く前に fail する」意図が果たせない）。

    既存 manifest の有無に依存しない（最初のチャンクから拒否 — 混合は 2 回目
    以降でなく初回実行の時点で予防する）。
    """
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "transformers", None)

    movable_plan = dict(_chunk_manifest([])["plan"], model_revision=revision)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(movable_plan), encoding="utf-8")
    manifest_out = tmp_path / "manifest.json"  # 存在しない = 初回チャンク
    output_dir = tmp_path / "takes"

    exit_code = _run_generate_append(
        plan_path, output_dir, manifest_out, level="low", repeat=0
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "immutable commit hash" in captured.err
    assert repr(revision) in captured.err
    assert "no WAV written" in captured.err
    assert not output_dir.exists()
    assert not manifest_out.exists()


def test_generate_append_rejects_duplicate_chunk_before_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """同一チャンクの誤再実行は **生成前** に fail-fast し、WAV も manifest も
    触らない（#171 P2: 生成後の merge 検査だけでは同名 WAV を先に上書きしてから
    重複で失敗し、旧 manifest が stale な sha256 を指したまま残る）。

    torch を欠落させた環境で exit 2（事前検証）になることが順序の証明 —
    検証が生成より後なら torch import が先に走り exit 1（ImportError）になる。
    """
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "transformers", None)

    existing = _chunk_manifest([_chunk_sample("low", 0)])
    plan_path, manifest_out, output_dir = _write_append_scene(tmp_path, existing)
    manifest_bytes_before = manifest_out.read_bytes()

    exit_code = _run_generate_append(
        plan_path, output_dir, manifest_out, level="low", repeat=0
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "duplicate sample_id" in captured.err
    assert "no WAV written" in captured.err
    # WAV は 1 byte も書かれない（output_dir は作成すらされない）し、
    # 既存 manifest も byte 単位で不変。
    assert not output_dir.exists()
    assert manifest_out.read_bytes() == manifest_bytes_before


def test_generate_append_rejects_header_mismatch_before_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """既存 manifest とのヘッダ不整合（merge と同一基準）も生成前に拒否する。"""
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "transformers", None)

    existing = _chunk_manifest([_chunk_sample("low", 0)])
    existing["plan"] = dict(existing["plan"], duration_seconds=12.0)
    plan_path, manifest_out, output_dir = _write_append_scene(tmp_path, existing)

    # 重複しないチャンク（high 0）でもヘッダ不整合だけで拒否される。
    exit_code = _run_generate_append(
        plan_path, output_dir, manifest_out, level="high", repeat=0
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "plan differs" in captured.err
    assert not output_dir.exists()


def test_generate_append_compatible_chunk_proceeds_to_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """整合するチャンクは事前検証を通過して生成段階へ進む（回帰: torch 欠落環境
    では ImportError の exit 1 に到達する = 事前検証が正常 append を塞がない）。
    既存 manifest は生成失敗時も不変。"""
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "transformers", None)

    existing = _chunk_manifest([_chunk_sample("low", 0)])
    plan_path, manifest_out, output_dir = _write_append_scene(tmp_path, existing)
    manifest_bytes_before = manifest_out.read_bytes()

    exit_code = _run_generate_append(
        plan_path, output_dir, manifest_out, level="high", repeat=0
    )

    assert exit_code == 1  # 事前検証は pass し、torch 欠落の ImportError まで到達
    captured = capsys.readouterr()
    assert "musicgen" in captured.err
    assert manifest_out.read_bytes() == manifest_bytes_before


def test_plan_chunk_manifest_stub_header_matches_generate_output_shape() -> None:
    """計画 stub のヘッダ/sample_id は `generate_takes` の manifest と同一系列
    （検証の同値性 — stub が実生成と違う id を作ると事前検証がすり抜ける）。"""
    plan = load_plan(SEGMENTS_PLAN_PATH)

    stub = plan_chunk_manifest_stub(plan, only_level="low", only_repeat=2)

    assert stub["schema_version"] == "1.0"
    assert stub["fixture_id"] == plan.fixture_id
    assert stub["generator"] == plan.generator
    assert stub["plan"] == plan.model_dump()
    assert [s["sample_id"] for s in stub["samples"]] == [
        f"{take.knob.name}_low_02"
        for take in iter_plan_takes(plan, only_level="low", only_repeat=2)
    ]
    # 整合する stub は validate_merge_compatibility を素通しする（正常系）。
    full_stub = plan_chunk_manifest_stub(plan, only_level="high", only_repeat=2)
    validate_merge_compatibility(stub, full_stub)


# ---------------------------------------------------------------------------
# `perform` subcommand (R3 repetition harness takes generator)
# ---------------------------------------------------------------------------


def test_resolve_perform_target_renders_prompt_and_slug_from_score() -> None:
    """torch 不要の経路: score のロード + ExternalPromptAdapter レンダリングのみ。

    generate_takes 相当のユニットとして manifest に入る `prompt` /
    `score_path` の元になる値を検証する（モック不使用）。
    """
    from svp_rpe.compose import load_composition_score
    from svp_rpe.compose.prompt_renderer import ExternalPromptAdapter

    score, prompt_text, slug = resolve_perform_target(SCORE_PATH)

    expected_score = load_composition_score(SCORE_PATH)
    expected_prompt = ExternalPromptAdapter().render(expected_score).text

    assert prompt_text == expected_prompt
    assert slug == "synth_01_roundtrip_source"
    assert score.meta.title == expected_score.meta.title


def test_resolve_perform_target_honors_explicit_fixture_id() -> None:
    _, _, slug = resolve_perform_target(SCORE_PATH, fixture_id="custom_rep_id")

    assert slug == "custom_rep_id"


def test_filename_safe_slug_sanitizes_path_separators_and_edges() -> None:
    """Codex #135 P2: `meta.title` 由来 slug の `/` 等がネストパス書き込みに
    化けて高コストな生成後に落ちないよう、単一ファイル名コンポーネントへ正規化。"""
    from scripts.collect_musicgen_takes import _filename_safe_slug

    assert _filename_safe_slug("edm/rock") == "edm-rock"
    assert _filename_safe_slug("a\\b:c*d") == "a-b-c-d"
    assert _filename_safe_slug("--weird--..") == "weird"
    assert _filename_safe_slug("///") == "score"
    assert _filename_safe_slug("already_safe-1.2") == "already_safe-1.2"
    # 明示 --fixture-id も同じ正規化を通る
    _, _, slug = resolve_perform_target(SCORE_PATH, fixture_id="custom/rep id")
    assert slug == "custom-rep-id"


def test_perform_takes_rejects_repetitions_below_two() -> None:
    """Codex #135 P2: n<2 は roundtrip-rep で拒否されるため、モデルロード・
    生成前（torch 不要のまま）に fail-fast する。"""
    from scripts.collect_musicgen_takes import perform_takes

    for repetitions in (0, 1):
        with pytest.raises(ValueError, match="repetitions"):
            perform_takes(
                SCORE_PATH,
                repetitions=repetitions,
                output_dir=Path("/nonexistent-unused"),
            )


def test_perform_subcommand_reaches_import_error_without_torch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without torch/transformers, `perform` must parse args, load the score
    (torch-free), and fail with an install-hint ImportError — not a traceback.
    """
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "transformers", None)

    output_dir = tmp_path / "out"
    manifest_out = output_dir / "takes_manifest.json"

    exit_code = main(
        [
            "perform",
            "--score",
            str(SCORE_PATH),
            "--repetitions",
            "2",
            "--output-dir",
            str(output_dir),
            "--manifest-out",
            str(manifest_out),
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "musicgen" in captured.err
    assert not manifest_out.exists()
