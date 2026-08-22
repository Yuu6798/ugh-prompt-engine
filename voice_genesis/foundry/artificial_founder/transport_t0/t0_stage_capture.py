"""t0_stage_capture.py — transport path S0–S5 の観測（設計書 §5 / §17 / §26）。

`af_ingest.reexpress_unit` は S1→S3 を 1 関数で通してしまい、途中段を外から
観測できない。§26 は共有経路（`adapter/donor_bank*.py` / `af_ingest.py` /
`af_measure.py`）の**修正を禁止**しているので、ここでは **同じ下位関数を同じ
順序・同じ引数で呼び直す新しい wrapper** として段を開く。共有コードは
read-only 利用のまま、観測点だけを T0 側に持つ。

段（§5）:

```
S0_BODY_44K          AF0 canonical Body WAV
S1_RESAMPLED_24K     load_donor_24k 直後
S2_WORLD_ANALYSIS    f0 / sp / ap / voiced_mask   ← diagnostic のみ
S3_WORLD_SYNTH_RAW   pyworld.synthesize 直後
S4_POST_NORM_FLOAT   peak > 1 のときだけ peak normalization 後
S5_PCM16_ROUNDTRIP   sf.write 後の実体を再 read
```

**S2 から波形 trait verdict を作らない**（§5 末尾）。S2 の戻り値は
`diagnostics` キーの下にだけ置き、`waveforms` には入れない。この分離は
`stage_capture` の戻り値の形そのもので表現され、`t0_localize` は
`waveforms` しか見ない。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np
import soundfile as sf

_AF = Path(__file__).resolve().parent.parent
_ADAPTER = _AF.parent / "adapter"
for _p in (str(_AF), str(_ADAPTER)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from af_ingest import FRAME_PERIOD_MS, REEXPRESSION_SR, require_pyworld  # noqa: E402

from t0_schema import (DIAGNOSTIC_ONLY_STAGE_IDS, STAGE_IDS,  # noqa: E402
                       WAVEFORM_STAGE_IDS)


class StageCaptureError(RuntimeError):
    """段の観測に失敗した（欠測は T0-G2 で BLOCKED になる）。"""


def _f32_peak(x: np.ndarray) -> float:
    return float(np.max(np.abs(x))) if x.size else 0.0


def capture_unit_stages(wav_path: str | Path,
                        tmp_dir: str | Path) -> Dict[str, Any]:
    """1 unit を S0–S5 で観測する。

    `af_ingest.reexpress_unit` / `reexpress_body` と **同じ下位呼び出し**を
    同じ順序・同じ引数で行う（`load_donor_24k` → `analyze_donor_world` →
    `pw.synthesize` → peak>1 のときだけ正規化 → PCM16 書き出し）。段を開く
    ためだけの再実装であり、挙動を変えてはならない。
    """
    pw = require_pyworld()
    from donor_bank import analyze_donor_world, load_donor_24k

    wav_path = Path(wav_path)
    waveforms: Dict[str, Tuple[np.ndarray, int]] = {}

    # --- S0: canonical Body（44.1 kHz, PCM16 を float へ）--------------------
    x0, sr0 = sf.read(str(wav_path), dtype="float64", always_2d=False)
    if np.ndim(x0) > 1:  # pragma: no cover - AF0 Body は mono
        x0 = np.mean(x0, axis=1)
    waveforms["S0_BODY_44K"] = (np.ascontiguousarray(x0, dtype=np.float64), int(sr0))

    # --- S1: 24 kHz へのリサンプル直後 --------------------------------------
    x1, sr1 = load_donor_24k(wav_path)
    x1 = np.ascontiguousarray(np.asarray(x1, dtype=np.float64))
    waveforms["S1_RESAMPLED_24K"] = (x1, int(sr1))

    # --- S2: WORLD 分析（diagnostic のみ）-----------------------------------
    analysis = analyze_donor_world(x1, sr1, frame_period_ms=FRAME_PERIOD_MS)
    diagnostics = _world_diagnostics(analysis, int(sr1))

    # --- S3: 再合成直後（正規化前を保証する）--------------------------------
    y3 = pw.synthesize(analysis["f0"], analysis["sp"], analysis["ap"], sr1,
                       frame_period=FRAME_PERIOD_MS)
    y3 = np.ascontiguousarray(np.asarray(y3, dtype=np.float64))
    waveforms["S3_WORLD_SYNTH_RAW"] = (y3, int(sr1))

    # --- S4: peak > 1 のときだけ正規化（`reexpress_body` と同一条件）--------
    peak = _f32_peak(y3)
    y4 = y3 / peak if peak > 1.0 else y3
    waveforms["S4_POST_NORM_FLOAT"] = (np.ascontiguousarray(y4), int(sr1))
    normalization = {"pre_norm_peak": peak, "applied": bool(peak > 1.0),
                     "gain": float(1.0 / peak) if peak > 1.0 else 1.0,
                     "post_norm_peak": _f32_peak(y4)}

    # --- S5: PCM16 で書いて読み直した実体 -----------------------------------
    tmp = Path(tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    pcm_path = tmp / f"S5_{wav_path.stem}.wav"
    sf.write(str(pcm_path), y4, int(sr1), subtype="PCM_16", format="WAV")
    y5, sr5 = sf.read(str(pcm_path), dtype="float64", always_2d=False)
    waveforms["S5_PCM16_ROUNDTRIP"] = (
        np.ascontiguousarray(np.asarray(y5, dtype=np.float64)), int(sr5))

    missing = [s for s in WAVEFORM_STAGE_IDS if s not in waveforms]
    if missing:  # pragma: no cover - 上で全段を埋めている
        raise StageCaptureError(f"stage capture incomplete: {missing}")

    return {
        "stem": wav_path.stem,
        "waveforms": waveforms,
        "diagnostics": {"S2_WORLD_ANALYSIS": diagnostics},
        "normalization": normalization,
        "pcm_path": str(pcm_path),
    }


def _world_diagnostics(analysis: Mapping[str, Any], sr: int) -> Dict[str, Any]:
    """§5 の S2 diagnostic 一式。**波形 verdict には使わない。**"""
    f0 = np.asarray(analysis["f0"], dtype=np.float64)
    sp = np.asarray(analysis["sp"], dtype=np.float64)
    ap = np.asarray(analysis["ap"], dtype=np.float64)
    voiced = np.asarray(analysis["voiced_mask"]).astype(bool)
    idx = np.nonzero(voiced)[0]
    frame_ms = FRAME_PERIOD_MS
    # AR-alpha 帯（3400 Hz 中心）の軌跡。sp の周波数軸は 0..sr/2 の線形。
    n_bins = sp.shape[1]
    freqs = np.linspace(0.0, sr / 2.0, n_bins)
    band = (freqs >= 3400.0 - 110.0) & (freqs <= 3400.0 + 110.0)
    ar_traj = sp[:, band].sum(axis=1) if band.any() else np.zeros(sp.shape[0])
    return {
        "n_frames": int(f0.size),
        "frame_period_ms": frame_ms,
        "voiced_onset_frame": int(idx[0]) if idx.size else None,
        "voiced_onset_ms": float(idx[0] * frame_ms) if idx.size else None,
        "voiced_frame_count": int(idx.size),
        "voiced_offset_frame": int(idx[-1]) if idx.size else None,
        "f0_persistence": float(idx.size / f0.size) if f0.size else 0.0,
        "f0_median_hz": float(np.median(f0[voiced])) if idx.size else None,
        "spectral_energy_trajectory": _downsample(sp.sum(axis=1), 24),
        "aperiodicity_trajectory": _downsample(ap.mean(axis=1), 24),
        "ar_alpha_band_trajectory": _downsample(ar_traj, 24),
    }


def _downsample(y: np.ndarray, n: int) -> List[float]:
    """診断用の軌跡を n 点へ落とす（記録サイズを抑えるだけ)。"""
    y = np.asarray(y, dtype=np.float64)
    if y.size == 0:
        return []
    if y.size <= n:
        return [round(float(v), 8) for v in y]
    idx = np.linspace(0, y.size - 1, n).astype(int)
    return [round(float(y[i]), 8) for i in idx]


def stage_measurements(capture: Mapping[str, Any], md: Mapping[str, Any],
                       measure_unit: Any) -> Dict[str, Dict[str, Any]]:
    """S0/S1/S3/S4/S5 へ **同じ blind meter** を当てる（§5）。

    `measure_unit` は呼び出し側（`t0_run`）が `af_measure.measure_unit` を
    渡す。ここで直接 import しないのは、盲目性の境界を呼び出し側で 1 箇所に
    集約し、T0 側で meter を差し替えられないようにするため。
    """
    stem = capture["stem"]
    out: Dict[str, Dict[str, Any]] = {}
    for stage in WAVEFORM_STAGE_IDS:
        x, sr = capture["waveforms"][stage]
        out[stage] = measure_unit(x, int(sr), md, stem)
    return out


def assert_no_waveform_verdict_from_diagnostics(ledger: Mapping[str, Any]) -> None:
    """§5 末尾の構造検査: S2 が波形 metric を持ち込んでいないこと。

    S2 の診断辞書に、波形 metric（`onset_ms` / `sustain_dbfs` /
    `afterglow_extra_ms` など blind meter の出力キー）が現れたら、それは
    「S2 から trait verdict を作れる」形になっているということ。設計が禁じて
    いるので、混入を検査で落とす。
    """
    forbidden = {"onset_ms", "r_onset_share", "sustain_dbfs", "main_release_ms",
                 "afterglow_extra_ms", "ar_alpha_center_hz", "hl_even_odd_ratio",
                 "spectral_identity", "core_f0_hz", "terminal_f0_delta_cents"}
    for stem, row in (ledger.get("units") or {}).items():
        diag = (row.get("diagnostics") or {})
        for stage in DIAGNOSTIC_ONLY_STAGE_IDS:
            payload = diag.get(stage) or {}
            leaked = sorted(forbidden & set(payload))
            if leaked:
                raise StageCaptureError(
                    f"{stem}/{stage}: diagnostic stage must not carry waveform "
                    f"trait metrics {leaked} (§5)")
        # measurements 側に S2 が現れてもいけない。
        for stage in DIAGNOSTIC_ONLY_STAGE_IDS:
            if stage in (row.get("measurements") or {}):
                raise StageCaptureError(
                    f"{stem}: {stage} must not appear in blind-meter measurements (§5)")


def ledger_completeness(ledger: Mapping[str, Any],
                        expected_stems: Optional[List[str]] = None) -> Dict[str, Any]:
    """T0-G2 STAGE_LEDGER_COMPLETE（25 unit × S0–S5 欠測なし）。"""
    units = ledger.get("units") or {}
    stems = sorted(units)
    want = sorted(expected_stems) if expected_stems is not None else stems
    missing_units = [s for s in want if s not in units]
    missing_stages: Dict[str, List[str]] = {}
    for stem in want:
        row = units.get(stem) or {}
        have = set(row.get("measurements") or {})
        have |= set(row.get("diagnostics") or {})
        gaps = [s for s in STAGE_IDS if s not in have]
        if gaps:
            missing_stages[stem] = gaps
    return {
        "n_units": len(units),
        "expected_units": len(want),
        "stage_ids": list(STAGE_IDS),
        "missing_units": missing_units,
        "missing_stages": missing_stages,
        "verdict": "PASS" if (not missing_units and not missing_stages
                              and len(units) == len(want)) else "FAIL",
    }


def reexpressed_reference(capture: Mapping[str, Any]) -> Tuple[np.ndarray, int]:
    """baseline（native transport）の出力 = S5 の実体。"""
    x, sr = capture["waveforms"]["S5_PCM16_ROUNDTRIP"]
    return np.asarray(x, dtype=np.float64), int(sr)


__all__ = ["StageCaptureError", "capture_unit_stages", "stage_measurements",
           "assert_no_waveform_verdict_from_diagnostics", "ledger_completeness",
           "reexpressed_reference", "REEXPRESSION_SR", "FRAME_PERIOD_MS"]
