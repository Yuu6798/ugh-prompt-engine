"""adapter/voice_spec.py — VG-F1: FoundryVoiceSpec v0（JSON, schema "foundry-voice/0.1"）
+ 変形演算子。

設計書 §2 voice_spec.py に対応する。変形演算子（freq_warp / spectral_tilt /
ap 底上げ）は `results_f1b/glue_template_nn.py` の実装を移植・共通化したもの。
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Set, Tuple

import numpy as np

SCHEMA = "foundry-voice/0.1"

_TOP_LEVEL_ALLOWED_KEYS: Set[str] = {"schema", "donor", "warp", "perf", "seed"}
_DONOR_ALLOWED_KEYS: Set[str] = {
    "dataset", "clip", "sha256",
    "voicebank_name", "voicebank_sha256",
    "corpus_name", "corpus_sha256",
}
_DONOR_STR_KEYS: Set[str] = {
    "dataset", "sha256", "voicebank_name", "voicebank_sha256", "corpus_name", "corpus_sha256",
}
_WARP_ALLOWED_KEYS: Set[str] = {"formant_scale", "tilt_db_oct", "breath_lift"}
_PERF_ALLOWED_KEYS: Set[str] = {"onset_glide", "vibrato", "drift", "jitter_cents", "portamento_ms"}
_ONSET_GLIDE_ALLOWED_KEYS: Set[str] = {"depth_cents", "time_ms"}
_VIBRATO_ALLOWED_KEYS: Set[str] = {"rate_hz", "depth_cents", "onset_ms", "phase_reset_per_note"}
_DRIFT_ALLOWED_KEYS: Set[str] = {"depth_cents", "rate_hz"}


def _require_dict(value: Any, field: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"voice spec field {field!r} must be an object, got {type(value).__name__}")
    return value


def _reject_unknown_keys(d: Dict[str, Any], allowed: Set[str], field: str) -> None:
    unknown = set(d.keys()) - allowed
    if unknown:
        raise ValueError(f"voice spec field {field!r} has unknown key(s): {sorted(unknown)}")


def _require_str(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"voice spec field {field!r} must be a string, got {type(value).__name__}")
    return value


def _require_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"voice spec field {field!r} must be an int, got {type(value).__name__}")
    return value


def _require_finite_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"voice spec field {field!r} must be a number, got {type(value).__name__}")
    out = float(value)
    if not math.isfinite(out):
        raise ValueError(f"voice spec field {field!r} must be finite, got {out!r}")
    return out


def _require_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"voice spec field {field!r} must be a bool, got {type(value).__name__}")
    return value


def _validate_donor(value: Any) -> Dict[str, Any]:
    donor = _require_dict(value, "donor")
    _reject_unknown_keys(donor, _DONOR_ALLOWED_KEYS, "donor")
    if "dataset" not in donor:
        raise ValueError("voice spec field 'donor' missing required key 'dataset'")
    out: Dict[str, Any] = {}
    for key, val in donor.items():
        if key in _DONOR_STR_KEYS:
            out[key] = _require_str(val, f"donor.{key}")
        elif key == "clip":
            out[key] = _require_int(val, "donor.clip")
        else:  # pragma: no cover - unreachable, all allowed keys are handled above
            out[key] = val
    return out


def _validate_warp(value: Any) -> Dict[str, float]:
    """P2 修正 (review #262 R10・`r3789520249`): `formant_scale` は 0/負値でも
    有限数チェックだけを通過していた。`freq_warp`（本ファイル下部）は
    `src_freqs = freqs / scale` でスケールを分母に使うため、0 は周波数格子を
    ゼロ除算して DC に NaN を注入し、負値は全周波数を単一境界値（DC ビンの
    値）へ折り畳んでスペクトルを崩壊させる（破損 WAV が検証を素通りして
    公開される実害）。`formant_scale` にのみ `> 0` を要求する（他の warp/perf
    フィールドは調査済み。tilt_db_oct・breath_lift・vibrato rate/depth 等は
    使用箇所側で既にクランプ/早期リターン/no-op で安全に処理されており、
    同型の実害が確認できないため対象外とした。判断根拠は
    `results_f1_4/f1_4_record_2026-08-15.md`「R10 対応」節に記録）。
    """
    warp = _require_dict(value, "warp")
    _reject_unknown_keys(warp, _WARP_ALLOWED_KEYS, "warp")
    out = {key: _require_finite_float(val, f"warp.{key}") for key, val in warp.items()}
    if "formant_scale" in out and out["formant_scale"] <= 0.0:
        raise ValueError(
            f"voice spec field 'warp.formant_scale' must be > 0, got {out['formant_scale']!r}"
        )
    return out


def _validate_scalar_group(value: Any, allowed: Set[str], field: str) -> Dict[str, Any]:
    group = _require_dict(value, field)
    _reject_unknown_keys(group, allowed, field)
    out: Dict[str, Any] = {}
    for key, val in group.items():
        if key == "phase_reset_per_note":
            out[key] = _require_bool(val, f"{field}.{key}")
        else:
            out[key] = _require_finite_float(val, f"{field}.{key}")
    return out


def _validate_perf(value: Any) -> Dict[str, Any]:
    perf = _require_dict(value, "perf")
    _reject_unknown_keys(perf, _PERF_ALLOWED_KEYS, "perf")
    out: Dict[str, Any] = {}
    if "onset_glide" in perf:
        out["onset_glide"] = _validate_scalar_group(
            perf["onset_glide"], _ONSET_GLIDE_ALLOWED_KEYS, "perf.onset_glide"
        )
    if "vibrato" in perf:
        out["vibrato"] = _validate_scalar_group(perf["vibrato"], _VIBRATO_ALLOWED_KEYS, "perf.vibrato")
    if "drift" in perf:
        out["drift"] = _validate_scalar_group(perf["drift"], _DRIFT_ALLOWED_KEYS, "perf.drift")
    if "jitter_cents" in perf:
        out["jitter_cents"] = _require_finite_float(perf["jitter_cents"], "perf.jitter_cents")
    if "portamento_ms" in perf:
        out["portamento_ms"] = _require_finite_float(perf["portamento_ms"], "perf.portamento_ms")
    return out


@dataclass(frozen=True)
class FoundryVoiceSpec:
    schema: str
    donor: Dict[str, Any]
    warp: Dict[str, float]
    perf: Dict[str, Any]
    seed: int

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "FoundryVoiceSpec":
        """P2 修正 (review #262 R7・`r3789451883`): 手編集 preset の型不正
        （`"warp": []` / `"perf": []` / `"seed": "11"` / 未知キー / NaN 等）を
        既定値へ黙って落とさず fail-closed で拒否する。必須オブジェクト形状・
        キー集合・スカラー型・有限数値域を検証してから spec を構築する。
        """
        if not isinstance(d, dict):
            raise ValueError(f"voice spec document must be an object, got {type(d).__name__}")
        if d.get("schema") != SCHEMA:
            raise ValueError(f"unsupported voice spec schema: {d.get('schema')!r} (expected {SCHEMA!r})")
        _reject_unknown_keys(d, _TOP_LEVEL_ALLOWED_KEYS, "<root>")
        for required in ("donor", "warp", "perf", "seed"):
            if required not in d:
                raise ValueError(f"voice spec missing required field: {required!r}")
        return FoundryVoiceSpec(
            schema=d["schema"],
            donor=_validate_donor(d["donor"]),
            warp=_validate_warp(d["warp"]),
            perf=_validate_perf(d["perf"]),
            seed=_require_int(d["seed"], "seed"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(schema=self.schema, donor=self.donor, warp=self.warp, perf=self.perf, seed=self.seed)


def load_voice_spec(path: str | Path) -> FoundryVoiceSpec:
    with open(path, "r", encoding="utf-8") as f:
        return FoundryVoiceSpec.from_dict(json.load(f))


def save_voice_spec(spec: FoundryVoiceSpec, path: str | Path) -> None:
    """P2 修正 (review #262 R7・`r3789451885`): staging + `os.replace` で
    preset JSON を atomic 公開する（AGENTS.md Persistent Artifact Safety
    Gate 項目6「全構築後公開」準拠。`measure_bands._atomic_write_text` /
    `render._atomic_write_wav` と同じ流儀 —— `except BaseException` で
    staging を best-effort 削除してから re-raise し、途中失敗時に既存の
    有効な preset を破壊しない）。
    """
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=out_path.parent, prefix=f"{out_path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(spec.to_dict(), f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_name, out_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


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
