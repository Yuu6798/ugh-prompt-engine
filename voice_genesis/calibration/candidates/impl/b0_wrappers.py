"""B0 baseline candidates — `voice_genesis/harness/*` の既存 meter を
**無改変 import** で MeterAdapter へ配線する（設計正本 §8: `B0_CURRENT` を
必ず含める。IMPLEMENTATION_MAP_v1.md タスク境界: harness 側は read-only
import のみで一切変更しない）。

import 方式は `voice_genesis/singer/gate_checks.py` 等、既存の複数箇所で
確立済みのパターン（`voice_genesis/harness` を `sys.path` に追加して
sibling import する。harness モジュールは互いに bare import
(`import measure as m` 等) をしているため、パッケージ相対 import は使えない）
をそのまま踏襲する。最小侵襲のため新しいラップ機構は導入しない。

5 つの harness 関数を 5 つの B0 candidate へ 1:1 で配線する
（[UNDERSPEC-CAL-C01] 設計正本 §8 は各 B0 candidate の具体的な配線元
関数までは指定しないため、以下の対応を採った。候補名との整合を判断根拠とする):

- `F0-B0-CURRENT` → `measure.estimate_f0_hps`
  （名称は HPS だが実装は自己相関/YIN。harness 側コメント "関数名は HPS
  由来だが実装は NACF" と同じ命名慣行を踏襲）。
- `M3-B0-CURRENT-CENTROID` / `M4-B0-CURRENT-CENTROID` → いずれも
  `measure_v3.formant_centroid_and_f1`。harness の「現行」実装は centroid
  型の 1 種類しか持たないため、construct が異なる M3 (formants) /
  M4 (resonance) の両方へ同一の現行実装を診断的に再利用する
  （設計正本 §8 が M3/M4 の B0 を明示的に DIAGNOSTIC_ONLY / construct
  不一致の正当な結果として許容している方針と整合）。
- `M2T-B0-CURRENT-HYBRID` → `measure_v3.source_tilt_v2`。この関数は
  「K>=3 倍音が取れれば dB/oct の回帰勾配」「取れなければ H1-H2 の
  dB 差分（回帰ではない別 construct/別 unit）」を同一の `value` field に
  返す（`tilt_estimator` で分岐を判別）。これは設計正本 §8 が
  「unit 混在のためそのままでは INVALID」と述べる欠陥そのものであり、
  最も自然に "HYBRID" 候補として一致する。`measure.spectral_tilt_db_per_oct`
  （常に dB/oct 回帰のみを返す独立実装）は補助的な legacy 参照値として
  `diagnostics` に同梱する（wrap 対象の 3 関数のうち未配線のものを作らない
  ため。単独の候補としては採用しない）。
- `M2A-B0-AUTOCORR-PERIODICITY` → `measure.hnr_db_approx`。名称上は
  "AUTOCORR" だが実装は harmonic/noise 帯域エネルギー比（FFT ベース）。
  現行 harness に periodicity 系の別実装がないため、既存の HNR 近似器を
  aperiodicity family の B0 として配線する（F0-B0 と同型の命名慣行踏襲）。
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

import numpy as np

from ... import vocab
from ..adapter import MeterOutput

_HERE = Path(__file__).resolve().parent
_VT_HARNESS_DIR = _HERE.parent.parent.parent / "harness"
if str(_VT_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_VT_HARNESS_DIR))

import measure as _m  # noqa: E402  (harness、無改変・import 流用のみ)
import measure_v3 as _m3  # noqa: E402  (harness、無改変・import 流用のみ)


def measure_f0_b0(signal: np.ndarray, sr: int, params: Mapping[str, object]) -> MeterOutput:
    """F0-B0-CURRENT: `measure.estimate_f0_hps` をそのまま呼ぶ。"""
    f0 = _m.estimate_f0_hps(signal, sr=sr)
    if not np.isfinite(f0):
        return MeterOutput(missing_reason=vocab.MissingReason.OUTPUT_MISSING)
    return MeterOutput(values={"f0_hz": float(f0)})


def _formant_centroid_output(signal: np.ndarray, sr: int) -> MeterOutput:
    f0 = _m.estimate_f0_hps(signal, sr=sr)
    result = _m3.formant_centroid_and_f1(signal, sr, f0)
    centroid = result["formant_centroid_hz"]
    f1 = result["f1_est_hz"]
    if not np.isfinite(centroid) and not np.isfinite(f1):
        return MeterOutput(
            missing_reason=vocab.MissingReason.OUTPUT_MISSING,
            diagnostics={"n_peaks_found": result["n_peaks_found"]},
        )
    values: dict[str, float] = {}
    if np.isfinite(centroid):
        values["formant_centroid_hz"] = float(centroid)
    if np.isfinite(f1):
        values["f1_est_hz"] = float(f1)
    return MeterOutput(
        values=values,
        diagnostics={
            "n_peaks_found": result["n_peaks_found"],
            "f1_est_fallback": result["f1_est_fallback"],
        },
    )


def measure_m3_b0_centroid(
    signal: np.ndarray, sr: int, params: Mapping[str, object]
) -> MeterOutput:
    """M3-B0-CURRENT-CENTROID (DIAGNOSTIC_ONLY): centroid は F1/F2/F3 個別値の代用にならない。"""
    return _formant_centroid_output(signal, sr)


def measure_m4_b0_centroid(
    signal: np.ndarray, sr: int, params: Mapping[str, object]
) -> MeterOutput:
    """M4-B0-CURRENT-CENTROID (DIAGNOSTIC_ONLY): M3 と同一実装を resonance construct へ流用。"""
    return _formant_centroid_output(signal, sr)


def measure_m2t_b0_hybrid(
    signal: np.ndarray, sr: int, params: Mapping[str, object]
) -> MeterOutput:
    """M2T-B0-CURRENT-HYBRID: そのままでは INVALID（unit 混在。value の単位が
    `tilt_estimator` により dB/oct（回帰）または dB（H1-H2 差分）へ動的に切り替わる）。
    """
    f0 = _m.estimate_f0_hps(signal, sr=sr)
    formant = _m3.formant_centroid_and_f1(signal, sr, f0)
    tilt = _m3.source_tilt_v2(
        signal, sr, f0, formant["f1_est_hz"], formant["f1_est_fallback"]
    )
    legacy_tilt = _m.spectral_tilt_db_per_oct(signal, sr, f0)
    value = tilt["value"]
    diagnostics: dict[str, object] = {
        "tilt_estimator": tilt["tilt_estimator"],
        "n_harmonics_used": tilt["n_harmonics_used"],
    }
    if np.isfinite(legacy_tilt):
        diagnostics["legacy_reference_db_per_oct"] = float(legacy_tilt)
    if not np.isfinite(value):
        return MeterOutput(
            missing_reason=vocab.MissingReason.OUTPUT_MISSING, diagnostics=diagnostics
        )
    return MeterOutput(values={"value": float(value)}, diagnostics=diagnostics)


def measure_m2a_b0_periodicity(
    signal: np.ndarray, sr: int, params: Mapping[str, object]
) -> MeterOutput:
    """M2A-B0-AUTOCORR-PERIODICITY: `measure.hnr_db_approx` を配線する。"""
    f0 = _m.estimate_f0_hps(signal, sr=sr)
    hnr = _m.hnr_db_approx(signal, sr, f0)
    if not np.isfinite(hnr):
        return MeterOutput(missing_reason=vocab.MissingReason.OUTPUT_MISSING)
    return MeterOutput(values={"hnr_db": float(hnr)})
