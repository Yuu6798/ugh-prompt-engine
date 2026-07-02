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
    load_plan,
    load_takes_manifest,
    main,
    resolve_perform_target,
    sample_seed,
)

PLAN_PATH = Path("examples/control/k2_musicgen/plan.yaml")
SCORE_PATH = Path("examples/roundtrip/synth_01_source.yaml")


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
        }
        assert "brightness" in features["spectral_profile"]


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


@pytest.mark.slow
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
