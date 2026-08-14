"""v0.3.1 計器強化・特徴量セット v2（design memo v031 §A / §B）。

制約: 既存の `measure.py` / `measure_v2.py` は不変のまま再利用する
（呼び出しのみ、内容は一切変更しない）。

§B 推定器強化:
  B-1 (放物線補間): measure.py の `_nacf_frame` に既に実装済み（YIN 差分
      関数の最小点への parabolic interpolation）。measure.py は凍結なので
      そのまま再利用するだけで B-1 は満たされる。
  B-2 (オクターブ曖昧性解消・スペクトル櫛照合)【本ファイルの新規実装】:
      `measure._nacf_frame` が返す時間領域候補 l に対し、{l, 2l, l/2} の
      各候補周波数でスペクトル倍音櫛エネルギー（FFT 上の k*f0, k=1..5 の
      ピーク和）を比較し、最良の候補を採用する。

§A 特徴量セット v2:
  - formant_centroid（新）: ケプストラム平滑化した対数振幅スペクトル包絡
    から 300-4000Hz 帯域のピークを検出し、上位 2 点（振幅降順）の周波数の
    幾何平均を log2 で測る。
  - source_tilt（新）: k=1..min(8, floor(0.9*F1_est/f0)) の低次倍音のみで
    回帰した spectral tilt。使用可能な倍音が 3 本未満なら H1-H2 (dB) に
    自動フォールバックする。F1_est はケプストラム包絡の 300-4000Hz 帯域内
    最下（最低周波数）ピーク。
  - periodicity / rms / vibrato_depth は §B の強化推定器の上に v0.3 と同じ
    定義（正規化自己相関ベースの periodicity、頑健 vibrato_depth）を再構築
    する（measure_v2.py 自体は変更しないが、内部で使う F0 推定を強化版に
    差し替えるため、periodicity/vibrato はこのファイルで再実装する）。
  - 広帯域 spectral_centroid は特徴量セットから除外（versioned instrument
    change。v0.3 の grip_report_v2.json との直接比較はできない）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

import measure as m  # 無改変で再利用（_nacf_frame, _analysis_window 等）
import measure_v2 as m2  # 無改変で再利用（_acf_full, _r_at_lag 等）

SR_DEFAULT = m.SR_DEFAULT
EPS = 1e-12

_FRAME_LEN_SEC = 0.093
_HOP_SEC = 0.023


# ---------------------------------------------------------------------------
# §B-2: オクターブ曖昧性解消（スペクトル櫛照合）
# ---------------------------------------------------------------------------


def _frame_spectrum(frame: np.ndarray, sr: int) -> Tuple[np.ndarray, np.ndarray]:
    frame = frame - frame.mean()
    window = np.hanning(len(frame))
    framed = frame * window
    spec = np.abs(np.fft.rfft(framed))
    freqs = np.fft.rfftfreq(len(framed), d=1.0 / sr)
    return freqs, spec


def _comb_energy(freqs: np.ndarray, spec: np.ndarray, f0_candidate: float, sr: int, k_max: int = 5) -> float:
    """候補 f0 の調波櫛（k=1..k_max）に沿ったスペクトルピークエネルギーの平均。"""
    if not np.isfinite(f0_candidate) or f0_candidate <= 0:
        return 0.0
    band_limit = min(sr / 2.0 * 0.95, 5000.0)
    bin_hz = freqs[1] - freqs[0] if len(freqs) > 1 else 1.0
    energies = []
    for k in range(1, k_max + 1):
        target = k * f0_candidate
        if target >= band_limit:
            break
        half_width = max(2 * bin_hz, target * 0.04)
        lo = np.searchsorted(freqs, target - half_width)
        hi = np.searchsorted(freqs, target + half_width)
        if hi > lo:
            energies.append(spec[lo:hi].max())
    if not energies:
        return 0.0
    return float(np.mean(energies))


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


def raw_frame_f0_track(sig: np.ndarray, sr: int, fmin: float, fmax: float) -> np.ndarray:
    """measure._nacf_frame（YIN + 放物線補間、無改変）によるフレーム毎の生 F0（オクターブ補正前）。"""
    return np.array([m._nacf_frame(frame, sr, fmin, fmax) for frame in _iter_frames(sig, sr)], dtype=float)


def _note_spectrum(sig: np.ndarray, sr: int) -> Tuple[np.ndarray, np.ndarray]:
    """解析区間全体（複数フレーム分、~0.78秒）から 1 本の高分解能スペクトルを作る。"""
    analysis = m._analysis_window(sig, sr)
    window = np.hanning(len(analysis))
    framed = analysis * window
    spec = np.abs(np.fft.rfft(framed))
    freqs = np.fft.rfftfreq(len(framed), d=1.0 / sr)
    return freqs, spec


def _robust_cluster_anchor(values_hz: np.ndarray, merge_cents: float = 150.0) -> float:
    """フレーム毎 F0 が二峰性（bimodal）に割れるケースへの頑健な代表値。

    [UNDERSPEC-v3] 単純な median は、フレーム間で「正しいクラスタ」と
    「誤ったクラスタ」がほぼ半々に割れる場合（実測: voice_A R0.1 MIDI93、
    約 580Hz クラスタ=1/3 分周の誤検出 と 約 1780Hz クラスタ=正解付近 が
    ほぼ 50/50）、どちらのクラスタの代表値でもない「隙間の値」を返して
    しまう（実測: median=1314Hz、真値 1760Hz から 505 cent も外れる）。
    single-linkage クラスタリング（隣接フレームの log2 差が 150 cent 以内
    なら同一クラスタとみなす）で最大クラスタを検出し、そのクラスタの
    median を代表値とすることでこの縮退を回避する。150 cent
    （=1.5半音）はフレーム内 jitter/vibrato の通常変動幅より広く、
    誤検出クラスタ間の典型的な分離幅（実測ではオクターブや3倍音相当、
    数百〜千 cent 差）より狭い値として選んだ。
    """
    if len(values_hz) == 0:
        return float("nan")
    logs = np.sort(np.log2(values_hz))
    clusters: List[List[float]] = []
    current = [logs[0]]
    for v in logs[1:]:
        if (v - current[-1]) * 1200.0 <= merge_cents:
            current.append(v)
        else:
            clusters.append(current)
            current = [v]
    clusters.append(current)
    largest = max(clusters, key=len)
    return float(2.0 ** np.median(largest))


def note_level_octave_ratio(
    sig: np.ndarray,
    sr: int,
    fmin: float,
    fmax: float,
    margin_factor: float = 1.5,
    raw_track: Optional[np.ndarray] = None,
) -> Tuple[float, Dict]:
    """§B-2: オクターブ曖昧性解消をノート単位（フレーム単位ではなく）で 1 回だけ判定する。

    [UNDERSPEC-v3] memo は「候補ラグ l に対し {l, 2l, l/2} の...倍音櫛
    エネルギーを比較し最大の候補を採用する」とのみ記述し、フレーム単位か
    ノート単位かを明記していない。当初フレーム単位で実装したところ、
    低音域の一部ノート（voice_A MIDI39, 77.78Hz）でフレーム間の comb
    energy 比が閾値付近（margin_factor 前後）で揺らぎ、ノート内で
    "l を採択するフレーム" と "2l を誤って採択するフレーム" が混在し、
    フレーム track の中央値が不安定な bimodal 分布になる新規のオクターブ
    誤りを引き起こすことを実測で発見した。ノート単位（フレーム毎の YIN
    中央値を代表候補とし、解析区間全体の高分解能スペクトルで 1 回だけ照合）
    に変更したところこの揺らぎが解消したため、本実装はノート単位を採用する。

    また [UNDERSPEC-v3] 素朴な argmax（マージンなし）は、フォルマントに
    基音が強く減衰される missing-fundamental 構造（低音域）で正しい候補を
    誤って上書きする新規オクターブ誤りを起こすことも実測で判明した
    （誤った 2l 選択のエネルギー比は l に対し実測 1.04〜1.45 倍、一方
    MIDI93/96 の正当な補正=R0.1 高音域回帰の是正では 1.54〜2.21 倍で、
    2 群がほぼ分離）。`margin_factor=1.5` を「YIN 自身の候補を上書きする
    ために必要な最低エネルギー比」として採用した。
    """
    if raw_track is None:
        raw_track = raw_frame_f0_track(sig, sr, fmin, fmax)
    valid = raw_track[np.isfinite(raw_track) & (raw_track > 0)]
    if len(valid) == 0:
        return 1.0, {"chosen": "l", "reason": "no_valid_frames", "median_yin": float("nan")}

    median_yin = _robust_cluster_anchor(valid)
    candidates = {"l": median_yin, "2l": 2.0 * median_yin, "l/2": median_yin / 2.0}
    valid_c = {name: f for name, f in candidates.items() if fmin <= f <= fmax}
    if "l" not in valid_c:
        return 1.0, {"chosen": "l", "reason": "l_out_of_range", "median_yin": median_yin}

    freqs, spec = _note_spectrum(sig, sr)
    energies = {name: _comb_energy(freqs, spec, f, sr) for name, f in valid_c.items()}
    l_energy = energies["l"]

    best_name, best_energy = "l", l_energy
    for name, e in energies.items():
        if name == "l":
            continue
        if e > best_energy and (l_energy <= 0 or e >= margin_factor * l_energy):
            best_name, best_energy = name, e

    ratio = {"l": 1.0, "2l": 2.0, "l/2": 0.5}[best_name]
    return ratio, {
        "candidates": valid_c,
        "energies": energies,
        "chosen": best_name,
        "margin_factor": margin_factor,
        "median_yin": median_yin,
        "ratio": ratio,
    }


def _scrub_track_to_anchor(track: np.ndarray, anchor_hz: float, tolerance_cents: float = 300.0) -> np.ndarray:
    """アンカーから大きく外れたフレーム（{l,2l,l/2} 以外の誤検出クラスタ）を NaN に落とす。

    [UNDERSPEC-v3] `note_level_octave_ratio` はノート代表アンカーの選定
    には頑健クラスタリングを使うが、それだけでは個々のフレーム値そのもの
    は補正されない。実測（voice_A R0.1 MIDI93）で「アンカーは正しく選べて
    いるのに、per-frame track 自体の中央値がなお別クラスタに引きずられて
    誤答する」ケースが残ることを確認したため、アンカーから
    `tolerance_cents`（300 cent = 3 半音、クラスタリングの merge_cents=150
    より緩く、{l,2l,l/2} の候補には反応しないが明らかな別クラスタ
    （実測: 3 倍音相当で ~1900 cent 差）は弾く値として設定）を超えて外れる
    フレームは「このフレームの推定は信頼できない」として NaN 化する
    （オクターブ補正ではなく除外。誤った値を偽装して埋めない）。
    """
    out = np.full_like(track, np.nan)
    if not np.isfinite(anchor_hz) or anchor_hz <= 0:
        return track
    for i, v in enumerate(track):
        if np.isfinite(v) and v > 0:
            cents_diff = abs(1200.0 * np.log2(v / anchor_hz))
            if cents_diff <= tolerance_cents:
                out[i] = v
    return out


def estimate_f0_track_v3(sig: np.ndarray, sr: int = SR_DEFAULT, fmin: float = 55.0, fmax: float = 2200.0) -> np.ndarray:
    """§B: フレーム毎 YIN（放物線補間込み）にノート単位のオクターブ補正 + 外れクラスタ除外を適用したトラック。"""
    raw_track = raw_frame_f0_track(sig, sr, fmin, fmax)
    ratio, diag = note_level_octave_ratio(sig, sr, fmin, fmax, raw_track=raw_track)
    corrected = raw_track * ratio
    anchor_hz = diag.get("candidates", {}).get(diag.get("chosen", "l"), float("nan"))
    return _scrub_track_to_anchor(corrected, anchor_hz)


def estimate_f0_track_v3_with_diag(
    sig: np.ndarray, sr: int = SR_DEFAULT, fmin: float = 55.0, fmax: float = 2200.0
) -> Tuple[np.ndarray, Dict]:
    raw_track = raw_frame_f0_track(sig, sr, fmin, fmax)
    ratio, diag = note_level_octave_ratio(sig, sr, fmin, fmax, raw_track=raw_track)
    corrected = raw_track * ratio
    anchor_hz = diag.get("candidates", {}).get(diag.get("chosen", "l"), float("nan"))
    scrubbed = _scrub_track_to_anchor(corrected, anchor_hz)
    diag["n_frames_scrubbed"] = int(np.isfinite(corrected).sum() - np.isfinite(scrubbed).sum())
    diag["n_frames_total"] = len(corrected)
    return scrubbed, diag


def estimate_f0_v3(sig: np.ndarray, sr: int = SR_DEFAULT, fmin: float = 55.0, fmax: float = 2200.0) -> float:
    """ノート代表 F0（オクターブ補正込みフレーム track の中央値）。measure.estimate_f0_hps の v3 版。"""
    track = estimate_f0_track_v3(sig, sr=sr, fmin=fmin, fmax=fmax)
    valid = track[np.isfinite(track) & (track > 0)]
    if len(valid) == 0:
        return float("nan")
    return float(np.median(valid))


# ---------------------------------------------------------------------------
# periodicity / vibrato_depth（強化 F0 推定器の上に再構築。定義自体は v0.3 と同一）
# ---------------------------------------------------------------------------


@dataclass
class PeriodicityTrackV3:
    r_median: float
    periodicity_db_median: float
    n_frames: int
    n_voiced_frames: int


def periodicity_track_v3(sig: np.ndarray, sr: int = SR_DEFAULT, fmin: float = 55.0, fmax: float = 2200.0) -> PeriodicityTrackV3:
    frames = list(_iter_frames(sig, sr))
    f0_arr = estimate_f0_track_v3(sig, sr=sr, fmin=fmin, fmax=fmax)

    r_vals, db_vals = [], []
    for frame, f0 in zip(frames, f0_arr):
        if np.isfinite(f0) and f0 > 0:
            acf = m2._acf_full(frame)
            r = m2._r_at_lag(acf, sr / f0)
            r = float(np.clip(r, 0.0, 0.999))
        else:
            r = 0.0
        r_vals.append(r)
        r_c = float(np.clip(r, 0.01, 0.99))
        db_vals.append(10.0 * np.log10(r_c / (1.0 - r_c)))

    voiced = np.isfinite(f0_arr) & (f0_arr > 0)
    r_arr = np.array(r_vals, dtype=float)
    db_arr = np.array(db_vals, dtype=float)

    return PeriodicityTrackV3(
        r_median=float(np.median(r_arr[voiced])) if voiced.any() else 0.0,
        periodicity_db_median=float(np.median(db_arr)) if len(db_arr) else float("nan"),
        n_frames=len(f0_arr),
        n_voiced_frames=int(voiced.sum()),
    )


@dataclass
class VibratoRobustV3:
    depth_cents: float
    reject_rate: float
    n_frames: int
    n_accepted: int


def vibrato_depth_robust_v3(
    sig: np.ndarray, sr: int = SR_DEFAULT, fmin: float = 55.0, fmax: float = 2200.0, reject_threshold_cents: float = 600.0
) -> VibratoRobustV3:
    track = estimate_f0_track_v3(sig, sr=sr, fmin=fmin, fmax=fmax)
    valid = track[np.isfinite(track) & (track > 0)]
    n_frames = len(track)
    if len(valid) < 4:
        return VibratoRobustV3(float("nan"), 1.0, n_frames, 0)

    med = float(np.median(valid))
    cents = 1200.0 * np.log2(valid / med)
    accept_mask = np.abs(cents) <= reject_threshold_cents
    accepted = cents[accept_mask]
    reject_rate = float((~accept_mask).sum()) / len(cents) if len(cents) else 1.0

    if len(accepted) < 4:
        return VibratoRobustV3(float("nan"), reject_rate, n_frames, len(accepted))

    mad = float(np.median(np.abs(accepted - np.median(accepted))))
    return VibratoRobustV3(mad * 1.4826, reject_rate, n_frames, len(accepted))


# ---------------------------------------------------------------------------
# §A: formant_centroid / source_tilt（ケプストラム平滑化包絡）
# ---------------------------------------------------------------------------


def cepstral_envelope_db(
    sig: np.ndarray, sr: int, f0_hz: float, lifter_ratio: float = 0.7, min_lifter_samples: int = 4
) -> Tuple[np.ndarray, np.ndarray]:
    """対数振幅スペクトル(dB)をケプストラム low-time liftering で平滑化した包絡を返す。

    [UNDERSPEC-v3] lifter 次数（quefrency カットオフ）は memo が実装自由度と
    明記する箇所。カットオフ = lifter_ratio * (推定周期のサンプル数) とし、
    基本周期の 70% 未満の quefrency のみを保持することで調波リップルを除去
    しつつフォルマット構造は残す設計とした（f0 が不明/不正な場合は固定
    quefrency=64 サンプルにフォールバック）。
    """
    analysis = m._analysis_window(sig, sr)
    n = len(analysis)
    window = np.hanning(n)
    framed = analysis * window

    spec = np.fft.fft(framed)
    log_mag_db = 20.0 * np.log10(np.abs(spec) + EPS)
    cep = np.real(np.fft.ifft(log_mag_db))

    if np.isfinite(f0_hz) and f0_hz > 0:
        cutoff = int(lifter_ratio * sr / f0_hz)
    else:
        cutoff = 64
    cutoff = max(cutoff, min_lifter_samples)
    cutoff = min(cutoff, n // 2 - 1)

    liftered = np.zeros_like(cep)
    liftered[:cutoff] = cep[:cutoff]
    if cutoff > 1:
        liftered[-(cutoff - 1) :] = cep[-(cutoff - 1) :]

    env_db_full = np.real(np.fft.fft(liftered))
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    env_db = env_db_full[: len(freqs)]
    return freqs, env_db


def _find_local_peaks(freqs: np.ndarray, env_db: np.ndarray, band_lo: float, band_hi: float) -> List[Tuple[float, float]]:
    lo_idx = np.searchsorted(freqs, band_lo)
    hi_idx = np.searchsorted(freqs, band_hi)
    peaks = []
    for i in range(max(lo_idx, 1), min(hi_idx, len(env_db) - 1)):
        if env_db[i] >= env_db[i - 1] and env_db[i] >= env_db[i + 1]:
            peaks.append((float(freqs[i]), float(env_db[i])))
    return peaks


def formant_centroid_and_f1(
    sig: np.ndarray, sr: int, f0_hz: float, band_lo: float = 300.0, band_hi: float = 4000.0
) -> Dict:
    """§A: formant_centroid（上位2ピークの幾何平均, log2 Hz）と F1_est（最下ピーク）を同時算出。"""
    freqs, env_db = cepstral_envelope_db(sig, sr, f0_hz)
    peaks = _find_local_peaks(freqs, env_db, band_lo, band_hi)

    if not peaks:
        return {
            "formant_centroid_hz": float("nan"),
            "formant_centroid_log2hz": float("nan"),
            "f1_est_hz": float("nan"),
            "n_peaks_found": 0,
            "f1_est_fallback": True,
        }

    peaks_by_mag = sorted(peaks, key=lambda p: p[1], reverse=True)
    top2 = peaks_by_mag[: min(2, len(peaks_by_mag))]
    if len(top2) == 2:
        geomean_hz = math.sqrt(top2[0][0] * top2[1][0])
    else:
        geomean_hz = top2[0][0]

    f1_est_hz = min(p[0] for p in peaks)  # 最下（最低周波数）ピーク

    return {
        "formant_centroid_hz": geomean_hz,
        "formant_centroid_log2hz": float(np.log2(geomean_hz)) if geomean_hz > 0 else float("nan"),
        "f1_est_hz": f1_est_hz,
        "n_peaks_found": len(peaks),
        "f1_est_fallback": False,
    }


def _harmonic_peak_amp(freqs: np.ndarray, spec: np.ndarray, f0: float, k: int, bin_hz: float) -> Optional[float]:
    target = k * f0
    half_width = max(2 * bin_hz, target * 0.05)
    lo = np.searchsorted(freqs, target - half_width)
    hi = np.searchsorted(freqs, target + half_width)
    if hi <= lo:
        return None
    peak = spec[lo:hi].max()
    return float(peak) if peak > 1e-9 else None


def source_tilt_v2(sig: np.ndarray, sr: int, f0_hz: float, f1_est_hz: float, f1_est_fallback: bool) -> Dict:
    """§A: 低次倍音限定回帰の source_tilt。3 本未満なら H1-H2 にフォールバック。"""
    if not np.isfinite(f0_hz) or f0_hz <= 0:
        return {"value": float("nan"), "tilt_estimator": "invalid_f0", "n_harmonics_used": 0, "k_max_dynamic": 0, "f1_est_fallback": f1_est_fallback}

    analysis = m._analysis_window(sig, sr)
    window = np.hanning(len(analysis))
    spec = np.abs(np.fft.rfft(analysis * window))
    freqs = np.fft.rfftfreq(len(analysis), d=1.0 / sr)
    bin_hz = freqs[1] - freqs[0] if len(freqs) > 1 else 1.0

    if not f1_est_fallback and np.isfinite(f1_est_hz) and f1_est_hz > 0:
        k_max_dynamic = min(8, int(math.floor(0.9 * f1_est_hz / f0_hz)))
    else:
        k_max_dynamic = 8

    xs, ys = [], []
    for k in range(1, max(k_max_dynamic, 0) + 1):
        amp = _harmonic_peak_amp(freqs, spec, f0_hz, k, bin_hz)
        if amp is None:
            continue
        xs.append(math.log2(k))
        ys.append(20.0 * math.log10(amp))

    if len(xs) >= 3:
        slope, _intercept = np.polyfit(np.array(xs), np.array(ys), 1)
        return {
            "value": float(slope),
            "tilt_estimator": "regression",
            "n_harmonics_used": len(xs),
            "k_max_dynamic": k_max_dynamic,
            "f1_est_fallback": f1_est_fallback,
        }

    amp1 = _harmonic_peak_amp(freqs, spec, f0_hz, 1, bin_hz)
    amp2 = _harmonic_peak_amp(freqs, spec, f0_hz, 2, bin_hz)
    if amp1 is not None and amp2 is not None:
        h1h2_db = 20.0 * math.log10(amp1 / amp2)
        return {
            "value": float(h1h2_db),
            "tilt_estimator": "h1h2",
            "n_harmonics_used": 2,
            "k_max_dynamic": k_max_dynamic,
            "f1_est_fallback": f1_est_fallback,
        }

    return {
        "value": float("nan"),
        "tilt_estimator": "h1h2_failed",
        "n_harmonics_used": 0,
        "k_max_dynamic": k_max_dynamic,
        "f1_est_fallback": f1_est_fallback,
    }


# ---------------------------------------------------------------------------
# 統合特徴抽出（v2 特徴量セット、§A 表のとおり 6 特徴）
# ---------------------------------------------------------------------------


REF_SCALE_V3 = {
    "mean_f0": 25.0,  # cents
    "formant_centroid": 0.10,  # octave (log2 Hz)
    "source_tilt_regression": 1.5,  # dB/oct
    "source_tilt_h1h2": 2.0,  # dB
    "periodicity": 3.0,  # dB
    "rms": 1.0,  # dB
    "vibrato_depth": 10.0,  # cents
}

FEATURE_NAMES_V3 = ["mean_f0", "formant_centroid", "source_tilt", "periodicity", "rms", "vibrato_depth"]


@dataclass
class FeaturesV3:
    mean_f0_hz: float
    mean_f0_cents: float
    formant_centroid_hz: float
    formant_centroid_log2hz: float
    f1_est_hz: float
    f1_est_fallback: bool
    n_formant_peaks_found: int
    source_tilt_value: float
    tilt_estimator: str
    n_harmonics_used_tilt: int
    k_max_dynamic_tilt: int
    periodicity_db: float
    periodicity_r_median: float
    rms: float
    rms_db: float
    vibrato_depth_cents: float
    vibrato_reject_rate: float
    measured_with_caveat: bool


def extract_all_features_v3(
    sig: np.ndarray, sr: int = SR_DEFAULT, fmin: float = 55.0, fmax: float = 2200.0, caveat_reject_threshold: float = 0.05
) -> FeaturesV3:
    f0 = estimate_f0_v3(sig, sr=sr, fmin=fmin, fmax=fmax)
    mean_f0_cents = 1200.0 * np.log2(f0) if np.isfinite(f0) and f0 > 0 else float("nan")

    fc = formant_centroid_and_f1(sig, sr, f0)
    tilt = source_tilt_v2(sig, sr, f0, fc["f1_est_hz"], fc["f1_est_fallback"])

    ptrack = periodicity_track_v3(sig, sr=sr, fmin=fmin, fmax=fmax)
    rms = m.rms_loudness(sig)
    rms_db = 20.0 * np.log10(max(rms, EPS))
    vib = vibrato_depth_robust_v3(sig, sr=sr, fmin=fmin, fmax=fmax)

    caveat = bool(vib.reject_rate > caveat_reject_threshold)

    return FeaturesV3(
        mean_f0_hz=f0,
        mean_f0_cents=float(mean_f0_cents),
        formant_centroid_hz=fc["formant_centroid_hz"],
        formant_centroid_log2hz=fc["formant_centroid_log2hz"],
        f1_est_hz=fc["f1_est_hz"],
        f1_est_fallback=fc["f1_est_fallback"],
        n_formant_peaks_found=fc["n_peaks_found"],
        source_tilt_value=tilt["value"],
        tilt_estimator=tilt["tilt_estimator"],
        n_harmonics_used_tilt=tilt["n_harmonics_used"],
        k_max_dynamic_tilt=tilt["k_max_dynamic"],
        periodicity_db=ptrack.periodicity_db_median,
        periodicity_r_median=ptrack.r_median,
        rms=rms,
        rms_db=float(rms_db),
        vibrato_depth_cents=vib.depth_cents,
        vibrato_reject_rate=vib.reject_rate,
        measured_with_caveat=caveat,
    )


def transformed_value(feat: FeaturesV3, feature_name: str) -> float:
    """grip v2 の E_note 計算に使う「単位を揃えた値」を返す（v0.3 の transform 方針を継承）。"""
    if feature_name == "mean_f0":
        return feat.mean_f0_cents
    if feature_name == "formant_centroid":
        return feat.formant_centroid_log2hz
    if feature_name == "source_tilt":
        return feat.source_tilt_value
    if feature_name == "periodicity":
        return feat.periodicity_db
    if feature_name == "rms":
        return feat.rms_db
    if feature_name == "vibrato_depth":
        return feat.vibrato_depth_cents
    raise KeyError(feature_name)


def ref_scale_for(feat: FeaturesV3, feature_name: str) -> float:
    if feature_name == "source_tilt":
        return REF_SCALE_V3["source_tilt_h1h2"] if feat.tilt_estimator == "h1h2" else REF_SCALE_V3["source_tilt_regression"]
    return REF_SCALE_V3[feature_name]
