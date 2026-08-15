"""test_adapter_voice_spec.py — FoundryVoiceSpec のロード/保存 + 変形演算子の基本動作。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapter"))

import numpy as np

import voice_spec as vs

_PRESETS_DIR = Path(__file__).resolve().parent.parent / "adapter" / "presets"
_ALL_PRESETS = sorted(_PRESETS_DIR.glob("*.json"))


def _valid_dict() -> dict:
    return json.loads((_PRESETS_DIR / "neutral.json").read_text(encoding="utf-8"))


def test_load_neutral_and_warped_presets() -> None:
    neutral = vs.load_voice_spec(_PRESETS_DIR / "neutral.json")
    warped = vs.load_voice_spec(_PRESETS_DIR / "warped.json")
    assert neutral.schema == vs.SCHEMA
    assert warped.schema == vs.SCHEMA
    assert neutral.seed != warped.seed
    assert neutral.warp["formant_scale"] != warped.warp["formant_scale"]


def test_save_load_roundtrip(tmp_path: Path) -> None:
    spec = vs.load_voice_spec(_PRESETS_DIR / "neutral.json")
    out = tmp_path / "roundtrip.json"
    vs.save_voice_spec(spec, out)
    reloaded = vs.load_voice_spec(out)
    assert reloaded == spec


def test_freq_warp_identity_at_scale_one() -> None:
    sp = np.random.default_rng(0).random((10, 32)) + 0.1
    out = vs.freq_warp(sp, 1.0, sr=24000)
    assert np.array_equal(out, sp)


def test_spectral_tilt_zero_is_identity() -> None:
    sp = np.random.default_rng(0).random((10, 32)) + 0.1
    out = vs.spectral_tilt(sp, sr=24000, db_per_octave=0.0)
    assert np.array_equal(out, sp)


def test_breath_lift_raises_and_clips() -> None:
    ap = np.full((5, 4), 0.95)
    out = vs.breath_lift(ap, 0.2)
    assert np.all(out <= 1.0)
    assert np.allclose(out, 1.0)


def test_apply_warp_shapes_preserved() -> None:
    sp = np.random.default_rng(0).random((10, 32)) + 0.1
    ap = np.full((10, 32), 0.1)
    warp = dict(formant_scale=0.96, tilt_db_oct=-1.5, breath_lift=0.08)
    sp_out, ap_out = vs.apply_warp(sp, ap, 24000, warp)
    assert sp_out.shape == sp.shape
    assert ap_out.shape == ap.shape
    assert np.all(ap_out >= 0.0) and np.all(ap_out <= 1.0)


# --- R7 P2: 不正形状の fail-closed 検証 (review #262 r3789451883) ---


@pytest.mark.parametrize("preset_path", _ALL_PRESETS, ids=lambda p: p.name)
def test_all_presets_pass_validation(preset_path: Path) -> None:
    """既存 4 presets は全て検証を通過する（fail-closed 化の非破壊確認）。"""
    spec = vs.load_voice_spec(preset_path)
    assert spec.schema == vs.SCHEMA


def test_warp_as_list_rejected() -> None:
    d = _valid_dict()
    d["warp"] = []
    with pytest.raises(ValueError, match="warp"):
        vs.FoundryVoiceSpec.from_dict(d)


def test_perf_as_list_rejected() -> None:
    d = _valid_dict()
    d["perf"] = []
    with pytest.raises(ValueError, match="perf"):
        vs.FoundryVoiceSpec.from_dict(d)


def test_donor_as_list_rejected() -> None:
    d = _valid_dict()
    d["donor"] = []
    with pytest.raises(ValueError, match="donor"):
        vs.FoundryVoiceSpec.from_dict(d)


def test_seed_as_string_rejected() -> None:
    d = _valid_dict()
    d["seed"] = "11"
    with pytest.raises(ValueError, match="seed"):
        vs.FoundryVoiceSpec.from_dict(d)


def test_seed_as_bool_rejected() -> None:
    d = _valid_dict()
    d["seed"] = True
    with pytest.raises(ValueError, match="seed"):
        vs.FoundryVoiceSpec.from_dict(d)


def test_unknown_top_level_key_rejected() -> None:
    d = _valid_dict()
    d["extra_field"] = 1
    with pytest.raises(ValueError, match="unknown key"):
        vs.FoundryVoiceSpec.from_dict(d)


def test_unknown_warp_key_rejected() -> None:
    d = _valid_dict()
    d["warp"]["bogus_axis"] = 1.0
    with pytest.raises(ValueError, match="unknown key"):
        vs.FoundryVoiceSpec.from_dict(d)


def test_unknown_perf_vibrato_key_rejected() -> None:
    d = _valid_dict()
    d["perf"]["vibrato"]["bogus"] = 1.0
    with pytest.raises(ValueError, match="unknown key"):
        vs.FoundryVoiceSpec.from_dict(d)


def test_warp_nan_rejected() -> None:
    d = _valid_dict()
    d["warp"]["formant_scale"] = math.nan
    with pytest.raises(ValueError, match="finite"):
        vs.FoundryVoiceSpec.from_dict(d)


def test_perf_jitter_inf_rejected() -> None:
    d = _valid_dict()
    d["perf"]["jitter_cents"] = math.inf
    with pytest.raises(ValueError, match="finite"):
        vs.FoundryVoiceSpec.from_dict(d)


def test_donor_missing_dataset_rejected() -> None:
    d = _valid_dict()
    del d["donor"]["dataset"]
    with pytest.raises(ValueError, match="dataset"):
        vs.FoundryVoiceSpec.from_dict(d)


def test_donor_clip_wrong_type_rejected() -> None:
    d = _valid_dict()
    d["donor"]["clip"] = "2"
    with pytest.raises(ValueError, match="donor.clip"):
        vs.FoundryVoiceSpec.from_dict(d)


def test_vibrato_phase_reset_wrong_type_rejected() -> None:
    d = _valid_dict()
    d["perf"]["vibrato"]["phase_reset_per_note"] = "yes"
    with pytest.raises(ValueError, match="bool"):
        vs.FoundryVoiceSpec.from_dict(d)


def test_missing_required_field_rejected() -> None:
    d = _valid_dict()
    del d["seed"]
    with pytest.raises(ValueError, match="seed"):
        vs.FoundryVoiceSpec.from_dict(d)


def test_non_dict_document_rejected() -> None:
    with pytest.raises(ValueError, match="object"):
        vs.FoundryVoiceSpec.from_dict([])  # type: ignore[arg-type]


# --- R7 P2: save_voice_spec の atomic 化 (review #262 r3789451885) ---


def test_save_voice_spec_atomic_failure_leaves_existing_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """json.dump 注入失敗時、既存の有効な preset を破壊しない。"""
    spec = vs.load_voice_spec(_PRESETS_DIR / "neutral.json")
    out = tmp_path / "preset.json"
    vs.save_voice_spec(spec, out)
    original = out.read_text(encoding="utf-8")

    other_spec = vs.load_voice_spec(_PRESETS_DIR / "warped.json")

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected failure")

    monkeypatch.setattr(vs.json, "dump", _boom)
    with pytest.raises(RuntimeError, match="injected failure"):
        vs.save_voice_spec(other_spec, out)

    assert out.read_text(encoding="utf-8") == original
    # staging tempfile は best-effort で削除され、残骸を残さない
    leftovers = list(tmp_path.glob("preset.json.*.tmp"))
    assert leftovers == []


def test_save_voice_spec_atomic_failure_no_partial_file_on_new_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新規パスへの保存が注入失敗した場合、部分書き込みの出力を残さない。"""
    spec = vs.load_voice_spec(_PRESETS_DIR / "neutral.json")
    out = tmp_path / "new_preset.json"

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected failure")

    monkeypatch.setattr(vs.json, "dump", _boom)
    with pytest.raises(RuntimeError, match="injected failure"):
        vs.save_voice_spec(spec, out)

    assert not out.exists()
    leftovers = list(tmp_path.glob("new_preset.json.*.tmp"))
    assert leftovers == []
