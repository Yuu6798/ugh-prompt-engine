"""F0-PYIN algorithm family（設計正本 §8, IMPLEMENTATION_MAP_v1.md §2.6）。

`librosa.pyin` を frame/hop の 2 軸グリッドで呼ぶ。fmin/fmax は §8 が明示的に
固定する (`fmin=80, fmax=600`)。有声フレームの中央値を F0 推定値として返す
（`b0_wrappers` の `estimate_f0_hps` と同じ「フレーム列 → 中央値」集約方針に
揃える）。
"""

from __future__ import annotations

from collections.abc import Mapping

import librosa
import numpy as np

from ... import vocab
from ..adapter import MeterOutput

FMIN_HZ = 80.0
FMAX_HZ = 600.0


def estimate_f0_pyin(
    signal: np.ndarray, sr: int, *, frame_length: int, hop_length: int
) -> float:
    """有声フレームの pyin F0 推定値の中央値。無声/失敗のみなら NaN。"""
    y = np.asarray(signal, dtype=float)
    if len(y) < frame_length:
        # pyin はフレーム長より短い入力を扱えないため missing 扱いにする。
        return float("nan")
    f0, voiced_flag, _voiced_prob = librosa.pyin(
        y,
        fmin=FMIN_HZ,
        fmax=FMAX_HZ,
        sr=sr,
        frame_length=frame_length,
        hop_length=hop_length,
    )
    voiced = f0[voiced_flag & np.isfinite(f0)]
    if voiced.size == 0:
        return float("nan")
    return float(np.median(voiced))


def measure(signal: np.ndarray, sr: int, params: Mapping[str, object]) -> MeterOutput:
    frame_length = int(params["frame_length"])
    hop_length = int(params["hop_length"])
    f0 = estimate_f0_pyin(signal, sr, frame_length=frame_length, hop_length=hop_length)
    if not np.isfinite(f0):
        return MeterOutput(missing_reason=vocab.MissingReason.OUTPUT_MISSING)
    return MeterOutput(values={"f0_hz": f0})
