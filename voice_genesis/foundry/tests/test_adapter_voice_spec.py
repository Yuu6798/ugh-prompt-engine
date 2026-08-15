"""test_adapter_voice_spec.py — FoundryVoiceSpec のロード/保存 + 変形演算子の基本動作。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapter"))

import numpy as np

import voice_spec as vs

_PRESETS_DIR = Path(__file__).resolve().parent.parent / "adapter" / "presets"


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
