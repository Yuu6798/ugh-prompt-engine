"""adapter/voice_spec.py — VG-F1: FoundryVoiceSpec v0（JSON, schema "foundry-voice/0.1"）
+ 変形演算子。

設計書 §2 voice_spec.py に対応する。変形演算子（freq_warp / spectral_tilt /
ap 底上げ）は `results_f1b/glue_template_nn.py` の実装を移植・共通化したもの。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np

SCHEMA = "foundry-voice/0.1"


@dataclass(frozen=True)
class FoundryVoiceSpec:
    schema: str
    donor: Dict[str, Any]
    warp: Dict[str, float]
    perf: Dict[str, Any]
    seed: int

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "FoundryVoiceSpec":
        if d.get("schema") != SCHEMA:
            raise ValueError(f"unsupported voice spec schema: {d.get('schema')!r} (expected {SCHEMA!r})")
        return FoundryVoiceSpec(
            schema=d["schema"], donor=dict(d["donor"]), warp=dict(d["warp"]),
            perf=dict(d["perf"]), seed=int(d["seed"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(schema=self.schema, donor=self.donor, warp=self.warp, perf=self.perf, seed=self.seed)


def load_voice_spec(path: str | Path) -> FoundryVoiceSpec:
    with open(path, "r", encoding="utf-8") as f:
        return FoundryVoiceSpec.from_dict(json.load(f))


def save_voice_spec(spec: FoundryVoiceSpec, path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec.to_dict(), f, indent=2, ensure_ascii=False)
        f.write("\n")


# --- 変形演算子（results_f1b/glue_template_nn.py の実装を移植・共通化） ---

def freq_warp(sp: np.ndarray, scale: float, sr: int) -> np.ndarray:
    """スペクトル包絡の周波数軸を scale 倍に伸縮する（線形補間で再標本化）。

    scale<1: フォルマントを低域側へ寄せる(dark)。scale>1: 高域側へ寄せる(bright)。
    """
    if scale == 1.0:
        return sp
    n_frames, n_bins = sp.shape
    freqs = np.linspace(0.0, sr / 2.0, n_bins)
    src_freqs = freqs / scale
    warped = np.empty_like(sp)
    for i in range(n_frames):
        warped[i] = np.interp(src_freqs, freqs, sp[i], left=sp[i, 0], right=sp[i, -1])
    return warped


def spectral_tilt(sp: np.ndarray, sr: int, db_per_octave: float, ref_hz: float = 1000.0) -> np.ndarray:
    if db_per_octave == 0.0:
        return sp
    n_frames, n_bins = sp.shape
    freqs = np.maximum(np.linspace(0.0, sr / 2.0, n_bins), 20.0)
    tilt_db = db_per_octave * np.log2(freqs / ref_hz)
    gain = 10.0 ** (tilt_db / 10.0)  # sp はパワー領域
    return sp * gain[None, :]


def breath_lift(ap: np.ndarray, lift: float) -> np.ndarray:
    """ap（非周期性指標）を lift だけ底上げする（息っぽさの付与、[0,1] にクリップ）。"""
    if lift == 0.0:
        return ap
    return np.clip(ap + lift, 0.0, 1.0)


def apply_warp(sp: np.ndarray, ap: np.ndarray, sr: int, warp: Dict[str, float]) -> Tuple[np.ndarray, np.ndarray]:
    scale = float(warp.get("formant_scale", 1.0))
    tilt = float(warp.get("tilt_db_oct", 0.0))
    lift = float(warp.get("breath_lift", 0.0))
    sp_out = spectral_tilt(freq_warp(sp, scale, sr), sr, tilt)
    ap_out = breath_lift(freq_warp(ap, scale, sr), lift)
    ap_out = np.clip(ap_out, 0.0, 1.0)
    return sp_out, ap_out
