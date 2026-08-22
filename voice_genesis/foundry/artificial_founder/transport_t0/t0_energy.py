"""t0_energy.py — Energy transport 候補 E0 / E1 / E2（設計書 §10 / §12 / §15）。

局在化の実測（S1 → S3 で sustain が上がる）が示すとおり、Energy の損失は WORLD
合成段のゲイン差。`pyworld.synthesize` は入力振幅を保存しない。

```text
E0  Native baseline                現行（sidecar なし）
E1  Global WORLD Gain Calibration  P0 control (-18 / -6 dBFS) **だけ** から
                                   global gain を 1 個決める。AF0 の -12 dBFS を
                                   fit に使わない（§12）
E2  Unit Energy Sidecar Restoration  E1 が holdout FAIL の場合のみ。Body sustain を
                                   sidecar で運び、WORLD synth 後へ **単一 scalar
                                   gain のみ** 適用
```

§10 で明示的に禁止:

```text
time-varying EQ / spectral shaping / until-pass loop
```

したがって本モジュールが返す操作は **スカラー倍のみ**。`apply_e1` / `apply_e2` は
どちらも `y * gain` の形からはみ出さない。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np


class EnergyCalibrationError(RuntimeError):
    """較正が成立しない（§36-5 calibration cannot discriminate）。"""


def _finite(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def fit_global_gain_db(calibration_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """§10 E1。calibration fixture **だけ** から global gain を 1 個決める。

    各 calibration fixture について「Body sustain − 再表現 sustain」を取り、
    その中央値を global gain（dB）とする。中央値なのは 1 fixture の外れ値に
    引きずられないため。**AF0 の行を渡してはならない**（呼び出し側が
    `t0_run` で分離しており、混入は `assert_no_af0_in_calibration` で落ちる）。

    引数の各 row は `{"id", "body_sustain_dbfs", "reexpressed_sustain_dbfs"}`。
    """
    deltas: List[float] = []
    used: List[Dict[str, Any]] = []
    for row in calibration_rows:
        b = _finite(row.get("body_sustain_dbfs"))
        r = _finite(row.get("reexpressed_sustain_dbfs"))
        if b is None or r is None:
            continue
        d = b - r
        deltas.append(d)
        used.append({"id": row.get("id"), "body_dbfs": b, "reexpressed_dbfs": r,
                     "delta_db": round(d, 6)})
    if not deltas:
        raise EnergyCalibrationError(
            "energy calibration produced no usable rows (§36-5)")
    gain_db = float(np.median(deltas))
    spread = float(max(deltas) - min(deltas))
    return {
        "operator": "E1", "gain_db": round(gain_db, 6),
        "gain_linear": round(float(10.0 ** (gain_db / 20.0)), 9),
        "n_rows": len(deltas), "rows": used,
        "spread_db": round(spread, 6),
        "fit_inputs": "calibration_only",
    }


def assert_no_af0_in_calibration(calibration_rows: Sequence[Mapping[str, Any]],
                                 af0_sustain_dbfs: float,
                                 tol: float = 1e-9) -> None:
    """§12。AF0 の target が fit 入力に混じっていないことを構造で確認する。"""
    for row in calibration_rows:
        b = _finite(row.get("body_sustain_dbfs"))
        if b is not None and abs(b - float(af0_sustain_dbfs)) <= tol:
            raise EnergyCalibrationError(
                f"AF0 sustain target {af0_sustain_dbfs} dBFS appeared in the energy "
                f"calibration inputs (row {row.get('id')!r}) — §12 で禁止")


def apply_e0(y: np.ndarray, sr: int, sidecar: Mapping[str, Any],
             calibration: Optional[Mapping[str, Any]] = None
             ) -> Tuple[np.ndarray, Dict[str, Any]]:
    """E0 Native baseline。"""
    return np.asarray(y, dtype=np.float64).copy(), {
        "operator": "E0", "mode": "native", "changed": False, "gain_db": 0.0}


def apply_e1(y: np.ndarray, sr: int, sidecar: Mapping[str, Any],
             calibration: Optional[Mapping[str, Any]] = None
             ) -> Tuple[np.ndarray, Dict[str, Any]]:
    """E1 Global gain。**全 unit へ同じスカラー**を掛ける。"""
    if not calibration or "gain_linear" not in calibration:
        raise EnergyCalibrationError("E1 requires a fitted global gain (§10 E1)")
    g = float(calibration["gain_linear"])
    out = np.asarray(y, dtype=np.float64) * g
    return np.ascontiguousarray(out), {
        "operator": "E1", "mode": "sidecar", "changed": True,
        "gain_db": float(calibration["gain_db"]), "gain_linear": g,
        "scope": "global"}


def apply_e2(y: np.ndarray, sr: int, sidecar: Mapping[str, Any],
             calibration: Optional[Mapping[str, Any]] = None
             ) -> Tuple[np.ndarray, Dict[str, Any]]:
    """E2 Unit sidecar restoration。**単一 scalar gain のみ**（§10 の禁止事項）。

    sidecar が運ぶ Body sustain（dBFS）と、この波形の sustain 帯 RMS の比から
    スカラーを 1 個決める。時変 EQ もスペクトル整形も行わない。until-pass
    ループも持たない（1 回で決まる閉じた式）。
    """
    y = np.asarray(y, dtype=np.float64)
    target_dbfs = _finite((sidecar.get("energy") or {}).get("sustain_dbfs"))
    if target_dbfs is None:
        raise EnergyCalibrationError(
            "E2 requires sidecar.energy.sustain_dbfs (§9 body_measurement)")
    cur = _sustain_rms_dbfs(y, sr)
    if cur is None:
        return y.copy(), {"operator": "E2", "mode": "sidecar", "changed": False,
                          "reason": "no measurable sustain"}
    gain_db = float(target_dbfs) - float(cur)
    g = float(10.0 ** (gain_db / 20.0))
    return np.ascontiguousarray(y * g), {
        "operator": "E2", "mode": "sidecar", "changed": True,
        "gain_db": round(gain_db, 6), "gain_linear": round(g, 9),
        "scope": "per_unit", "target_dbfs": float(target_dbfs),
        "observed_dbfs": round(float(cur), 6)}


def _sustain_rms_dbfs(y: np.ndarray, sr: int,
                      hop_ms: float = 5.0, win_ms: float = 25.0) -> Optional[float]:
    """持続部の RMS（dBFS）。

    E2 が使う「今の音量」の推定。**blind meter とは独立の内部量**であり、
    `t0_compare` の合否には使わない（合否は必ず `af_measure` 側で測る）。
    ここで meter を再利用すると「評価器を見て補正する」形になるため、
    意図的に単純な包絡ピークの平坦部だけを見る。
    """
    y = np.asarray(y, dtype=np.float64)
    if y.size == 0:
        return None
    hop = max(1, int(round(hop_ms * sr / 1000.0)))
    win = max(hop, int(round(win_ms * sr / 1000.0)))
    n = 1 + max(0, (y.size - win) // hop)
    if n < 4:
        rms = float(np.sqrt(np.mean(y ** 2)))
        return 20.0 * math.log10(rms) if rms > 0 else None
    env = np.empty(n, dtype=np.float64)
    for i in range(n):
        seg = y[i * hop:i * hop + win]
        env[i] = float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0
    peak = float(np.percentile(env, 95.0))
    if peak <= 0.0:
        return None
    keep = env >= 0.7 * peak
    if not keep.any():
        return None
    rms = float(np.median(env[keep]))
    return 20.0 * math.log10(rms) if rms > 0 else None


ENERGY_OPERATORS = {"E0": apply_e0, "E1": apply_e1, "E2": apply_e2}

__all__ = ["EnergyCalibrationError", "ENERGY_OPERATORS", "fit_global_gain_db",
           "assert_no_af0_in_calibration", "apply_e0", "apply_e1", "apply_e2"]
