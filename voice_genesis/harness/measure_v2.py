"""v0.3 計器修復（design memo §A-4 / §B）。

制約: 既存の `measure.py`（自前 F0 推定器 = YIN 式差分関数）は不変のまま
再利用する（呼び出しのみ）。本ファイルは v0.2 VT-3 で実証された 2 つの
計器アーティファクトの修復と、grip v2（§A）が必要とする物理単位変換を追加
する。

修復 1（§B-1 periodicity）: 固定倍音帯域窓の HNR 近似は、フォルマント/tilt/
vibrato が変化すると帯域切り出し自体が動くアーティファクトを持つ
（v0.2 実測: grip 全軸で hnr_db が dominant side feature になっていた）。
フレーム毎の正規化自己相関 r（YIN 推定 F0 のラグでの ACF 値）に基づく
periodicity_db = 10*log10(r/(1-r)) に差し替える。

修復 2（§B-2 vibrato_depth）: フレーム F0 系列の外れ値（±600 cents 超）を
棄却してから頑健 std（MAD*1.4826）を取ることで、低音域×大深度 vibrato で
発生していたオクターブ誤りによる異常値化を抑制する。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import numpy as np

import measure as m  # 既存 measure.py を無改変で再利用

SR_DEFAULT = m.SR_DEFAULT
EPS = 1e-12


# ---------------------------------------------------------------------------
# フレーム分割ユーティリティ（measure.py と同一パラメータ）
# ---------------------------------------------------------------------------

_FRAME_LEN_SEC = 0.093
_HOP_SEC = 0.023


def _iter_frames(sig: np.ndarray, sr: int):
    analysis = m._analysis_window(sig, sr)
    frame_len = int(_FRAME_LEN_SEC * sr)
    hop = max(int(_HOP_SEC * sr), 1)
    if len(analysis) < frame_len:
        frame_len = len(analysis)
    for start in range(0, max(len(analysis) - frame_len, 1), hop):
        frame = analysis[start : start + frame_len]
        if len(frame) < frame_len:
            break
        yield frame


def _acf_full(frame: np.ndarray) -> np.ndarray:
    frame = frame - frame.mean()
    n = len(frame)
    window = np.hanning(n)
    framed = frame * window
    n_fft = int(2 ** np.ceil(np.log2(2 * n)))
    spec = np.fft.rfft(framed, n=n_fft)
    acf = np.fft.irfft(spec * np.conj(spec), n=n_fft)[:n]
    return acf


def _r_at_lag(acf: np.ndarray, lag_float: float) -> float:
    """acf[0] で正規化した相関係数を、非整数ラグに線形補間して返す。"""
    if acf[0] <= EPS or lag_float <= 0:
        return 0.0
    acf_norm = acf / acf[0]
    lag0 = int(np.floor(lag_float))
    frac = lag_float - lag0
    if lag0 + 1 >= len(acf_norm):
        idx = min(lag0, len(acf_norm) - 1)
        return float(np.clip(acf_norm[idx], -1.0, 1.0))
    r = acf_norm[lag0] * (1 - frac) + acf_norm[lag0 + 1] * frac
    return float(np.clip(r, -1.0, 1.0))


# ---------------------------------------------------------------------------
# 修復 1: periodicity（HNR 近似の差替え、§B-1）
# ---------------------------------------------------------------------------


@dataclass
class PeriodicityTrack:
    f0_values: np.ndarray  # フレーム毎 F0（NaN=無声/検出不能）
    r_values: np.ndarray  # フレーム毎 正規化自己相関 r（有声フレームのみ有効値、無声は 0.0）
    periodicity_db_values: np.ndarray  # フレーム毎 10*log10(r/(1-r))
    r_median: float  # 有声フレームの r の中央値（VT-1 v2 plausibility 判定用）
    periodicity_db_median: float  # 代表値（VT-3 v2 grip 特徴量として使用）
    n_frames: int
    n_voiced_frames: int


def periodicity_track(
    sig: np.ndarray,
    sr: int = SR_DEFAULT,
    fmin: float = 55.0,
    fmax: float = 2200.0,
) -> PeriodicityTrack:
    f0_vals = []
    r_vals = []
    db_vals = []
    for frame in _iter_frames(sig, sr):
        f0 = m._nacf_frame(frame, sr, fmin, fmax)
        f0_vals.append(f0)
        if np.isfinite(f0) and f0 > 0:
            acf = _acf_full(frame)
            r = _r_at_lag(acf, sr / f0)
            r = float(np.clip(r, 0.0, 0.999))  # 負の相関は非周期性として 0 に丸める
        else:
            r = 0.0
        r_vals.append(r)
        r_clamped = float(np.clip(r, 0.01, 0.99))
        db = 10.0 * np.log10(r_clamped / (1.0 - r_clamped))
        db_vals.append(db)

    f0_arr = np.array(f0_vals, dtype=float)
    r_arr = np.array(r_vals, dtype=float)
    db_arr = np.array(db_vals, dtype=float)
    voiced_mask = np.isfinite(f0_arr) & (f0_arr > 0)

    r_median = float(np.median(r_arr[voiced_mask])) if voiced_mask.any() else 0.0
    db_median = float(np.median(db_arr)) if len(db_arr) else float("nan")

    return PeriodicityTrack(
        f0_values=f0_arr,
        r_values=r_arr,
        periodicity_db_values=db_arr,
        r_median=r_median,
        periodicity_db_median=db_median,
        n_frames=len(f0_arr),
        n_voiced_frames=int(voiced_mask.sum()),
    )


def periodicity_db(sig: np.ndarray, sr: int = SR_DEFAULT, fmin: float = 55.0, fmax: float = 2200.0) -> float:
    return periodicity_track(sig, sr=sr, fmin=fmin, fmax=fmax).periodicity_db_median


# ---------------------------------------------------------------------------
# 修復 2: vibrato_depth の頑健化（§B-2）
# ---------------------------------------------------------------------------


@dataclass
class VibratoRobust:
    depth_cents: float
    reject_rate: float
    n_frames: int
    n_accepted: int


def vibrato_depth_robust(
    sig: np.ndarray,
    sr: int = SR_DEFAULT,
    fmin: float = 55.0,
    fmax: float = 2200.0,
    reject_threshold_cents: float = 600.0,
) -> VibratoRobust:
    track = m.estimate_f0_track_hps(sig, sr=sr, fmin=fmin, fmax=fmax)
    valid = track[np.isfinite(track) & (track > 0)]
    n_frames = len(track)
    if len(valid) < 4:
        return VibratoRobust(depth_cents=float("nan"), reject_rate=1.0, n_frames=n_frames, n_accepted=0)

    med = float(np.median(valid))
    cents = 1200.0 * np.log2(valid / med)

    accept_mask = np.abs(cents) <= reject_threshold_cents
    accepted = cents[accept_mask]
    n_rejected = int((~accept_mask).sum())
    reject_rate = n_rejected / len(cents) if len(cents) else 1.0

    if len(accepted) < 4:
        return VibratoRobust(depth_cents=float("nan"), reject_rate=reject_rate, n_frames=n_frames, n_accepted=len(accepted))

    mad = float(np.median(np.abs(accepted - np.median(accepted))))
    robust_std = mad * 1.4826
    return VibratoRobust(depth_cents=robust_std, reject_rate=reject_rate, n_frames=n_frames, n_accepted=len(accepted))


# ---------------------------------------------------------------------------
# grip v2 用の統合特徴抽出（物理単位・transform 済み値も同梱）
# ---------------------------------------------------------------------------


@dataclass
class FeaturesV2:
    mean_f0_hz: float
    mean_f0_cents: float  # = 1200*log2(f0_hz)  （原点は任意。差分だけが意味を持つ）
    spectral_centroid_hz: float
    spectral_centroid_log2hz: float  # = log2(centroid_hz)  （§A-1: "centroid は log2 Hz で測る"）
    spectral_tilt_db_per_oct: float
    periodicity_db: float
    periodicity_r_median: float
    rms: float
    rms_db: float  # = 20*log10(rms)
    vibrato_depth_cents: float
    vibrato_reject_rate: float
    measured_with_caveat: bool  # F0 トラックにオクターブ跳躍系の外れ値棄却が多発 (§A-4)


# grip v2 で「差分を取る前に必ずこの空間へ写像する」変換（§A-1 / §A-2 の単位を
# 揃えるための実装上の工夫。メモは transform 手順を明記していないため
# underspec_log_v2.md に記録する）。
FEATURE_TRANSFORM_KEYS = {
    "mean_f0": "mean_f0_cents",
    "spectral_centroid": "spectral_centroid_log2hz",
    "spectral_tilt": "spectral_tilt_db_per_oct",
    "periodicity": "periodicity_db",
    "rms": "rms_db",
    "vibrato_depth": "vibrato_depth_cents",
}

REF_SCALE = {
    "mean_f0": 25.0,  # cents
    "spectral_centroid": 0.10,  # octave (log2 Hz)
    "spectral_tilt": 1.5,  # dB/oct
    "periodicity": 3.0,  # dB
    "rms": 1.0,  # dB
    "vibrato_depth": 10.0,  # cents
}


def extract_all_features_v2(
    sig: np.ndarray,
    sr: int = SR_DEFAULT,
    fmin: float = 55.0,
    fmax: float = 2200.0,
    caveat_reject_threshold: float = 0.05,
) -> FeaturesV2:
    f0 = m.estimate_f0_hps(sig, sr=sr, fmin=fmin, fmax=fmax)
    centroid_hz = m.spectral_centroid_hz(sig, sr=sr)
    tilt = m.spectral_tilt_db_per_oct(sig, sr=sr, f0_hz=f0)
    ptrack = periodicity_track(sig, sr=sr, fmin=fmin, fmax=fmax)
    rms = m.rms_loudness(sig)
    vib = vibrato_depth_robust(sig, sr=sr, fmin=fmin, fmax=fmax)

    mean_f0_cents = 1200.0 * np.log2(f0) if np.isfinite(f0) and f0 > 0 else float("nan")
    centroid_log2 = np.log2(centroid_hz) if np.isfinite(centroid_hz) and centroid_hz > 0 else float("nan")
    rms_db = 20.0 * np.log10(max(rms, EPS))

    caveat = bool(vib.reject_rate > caveat_reject_threshold)

    return FeaturesV2(
        mean_f0_hz=f0,
        mean_f0_cents=float(mean_f0_cents),
        spectral_centroid_hz=centroid_hz,
        spectral_centroid_log2hz=float(centroid_log2),
        spectral_tilt_db_per_oct=tilt,
        periodicity_db=ptrack.periodicity_db_median,
        periodicity_r_median=ptrack.r_median,
        rms=rms,
        rms_db=float(rms_db),
        vibrato_depth_cents=vib.depth_cents,
        vibrato_reject_rate=vib.reject_rate,
        measured_with_caveat=caveat,
    )


def features_v2_to_dict(f: FeaturesV2) -> Dict[str, float]:
    return {
        "mean_f0_hz": f.mean_f0_hz,
        "mean_f0_cents": f.mean_f0_cents,
        "spectral_centroid_hz": f.spectral_centroid_hz,
        "spectral_centroid_log2hz": f.spectral_centroid_log2hz,
        "spectral_tilt_db_per_oct": f.spectral_tilt_db_per_oct,
        "periodicity_db": f.periodicity_db,
        "periodicity_r_median": f.periodicity_r_median,
        "rms": f.rms,
        "rms_db": f.rms_db,
        "vibrato_depth_cents": f.vibrato_depth_cents,
        "vibrato_reject_rate": f.vibrato_reject_rate,
        "measured_with_caveat": f.measured_with_caveat,
    }
