"""M2T-HARMONIC-OLS / M2T-HARMONIC-THEILSEN algorithm families（設計正本 §8）。

倍音振幅取得方式を単一の方法に凍結する（§8: 「harmonic amplitude 取得方式は
1案に凍結」）: rFFT ビンのうち `k*f0` に最も近いビンを中心とした 3 点
（直前・直後ビンを含む）の対数振幅（dB）へ放物線補間を適用し、`k*f0` 位置
での振幅（dB）を推定する。

`K` 本未満の倍音しか取得できない場合は **縮退せず missing** とする
（§8 の明記事項。H1-H2 へのフォールバックは行わない = 「H1-H2 は別
construct として selection 競争から除外」）。

回帰は OLS（最小二乗、`np.polyfit`）と Theil-Sen（`scipy.stats.theilslopes`、
外れ値に頑健な中央値ベース勾配）の 2 系列を提供する。

## RUN10-CAL v1.3 §X1: `hnr_acf_db` 補助フィールド

`values` には primary output `tilt_db_per_oct` に加えて harmonicity 補助値
`hnr_acf_db`（`aperiodicity.hnr_acf_db`、frame 25 ms / hop 10 ms / hann、
F0 非依存）を同梱する。これは `registry` 側で宣言する
`DetectionPredicate(field="hnr_acf_db", min_value=-5.0)` の入力であり、
**primary output（`measure_stage.PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY`
= `tilt_db_per_oct`）と selection の意味論は一切変わらない** — 変わるのは
`fixtures.controls.detected()` の fire 判定だけである。

パラメータ（frame/hop/window）は候補グリッドの軸ではなく v1.3 §X1 が凍結
した定数であり、段階 2 実測（WP-A、`scratchpad/v13/wpa_report.md` §3 H-T1）
の probe と同一値である（正例 [-1.92, +0.75] dB vs NOISE_ONLY
[-9.88, -9.76] dB、余裕 7.8 dB）。非有限値（例: SILENCE）はキー自体を
省略する — `detected()` は field 欠落を非発火へ一様に写像する。
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
from scipy.signal.windows import blackmanharris, hann
from scipy.stats import theilslopes

from ... import vocab
from ..adapter import MeterOutput
from .aperiodicity import hnr_acf_db

_WINDOWS = {"hann": hann, "blackman_harris": blackmanharris}

#: v1.3 §X1 が凍結する `hnr_acf_db` 補助フィールドの計測パラメータ
#: （WP-A probe と同一 = `scratchpad/v13/hnr_probe.py` の `PARAMS`）。
#: 候補パラメータグリッドの軸ではない（`registry` の `parameters` には
#: 現れない）ため、`candidate_space_sha` はこの定数を含まない — 変更は
#: 次 revision の preregistration 経由。
HNR_DETECTION_FRAME_MS: float = 25.0
HNR_DETECTION_HOP_MS: float = 10.0
HNR_DETECTION_WINDOW: str = "hann"


def detection_hnr_acf_db(signal: np.ndarray, sr: int) -> float:
    """v1.3 §X1 の凍結パラメータで `hnr_acf_db()` を評価する（`measure()` と
    テスト・診断 probe が同じ入口を使うための薄い wrapper）。"""
    return hnr_acf_db(
        signal,
        sr,
        frame_ms=HNR_DETECTION_FRAME_MS,
        hop_ms=HNR_DETECTION_HOP_MS,
        window=HNR_DETECTION_WINDOW,
    )


def _analysis_window(signal: np.ndarray, start_frac: float = 0.15, end_frac: float = 0.9) -> np.ndarray:
    n = len(signal)
    a = int(n * start_frac)
    b = int(n * end_frac)
    return signal[a:b] if b > a else signal


def _parabolic_interp_db(log_mag_db: np.ndarray, target_bin: float) -> float | None:
    """`target_bin`（非整数可）近傍 3 点の放物線補間により dB 値を推定する。"""
    idx = int(round(target_bin))
    if idx <= 0 or idx >= len(log_mag_db) - 1:
        return None
    y0, y1, y2 = log_mag_db[idx - 1], log_mag_db[idx], log_mag_db[idx + 1]
    delta = target_bin - idx  # in [-0.5, 0.5]
    # 2 次多項式 y = a*x^2 + b*x + c を (-1,y0),(0,y1),(1,y2) で決定し x=delta で評価
    a = 0.5 * (y0 + y2) - y1
    b = 0.5 * (y2 - y0)
    c = y1
    return float(a * delta**2 + b * delta + c)


def harmonic_amplitudes_db(
    signal: np.ndarray, sr: int, f0_hz: float, k_max: int, window_name: str
) -> list[float | None]:
    """k=1..k_max の各倍音について `20*log10(A_k)`（推定振幅、dB）を返す。

    取得できない倍音（対象周波数が Nyquist 超過 or 境界近傍）は None。
    """
    if not np.isfinite(f0_hz) or f0_hz <= 0:
        return [None] * k_max
    analysis = _analysis_window(np.asarray(signal, dtype=float))
    window_fn = _WINDOWS[window_name]
    window = window_fn(len(analysis))
    spec = np.abs(np.fft.rfft(analysis * window))
    eps = 1e-12
    log_mag_db = 20.0 * np.log10(spec + eps)
    freqs = np.fft.rfftfreq(len(analysis), d=1.0 / sr)
    bin_hz = freqs[1] - freqs[0] if len(freqs) > 1 else 1.0

    out: list[float | None] = []
    for k in range(1, k_max + 1):
        target_hz = k * f0_hz
        if target_hz >= sr / 2.0 * 0.98:
            out.append(None)
            continue
        target_bin = target_hz / bin_hz
        out.append(_parabolic_interp_db(log_mag_db, target_bin))
    return out


#: `harmonic_amplitudes_db_peak()` のピーク探索半径（`k*f0` に対する相対幅）。
#: RUN10-CAL リセット設計 v0 §2 が既定値として指定する 0.03。
#: 安全条件は `PEAK_SEARCH_TOL * k < 0.5`（半径が倍音間隔の半分未満 = 隣接倍音を
#: 拾わない）で、`registry.M2T_K` の最大 8 に対し 0.24 < 0.5 と余裕がある。
#: **既知の限界**: 探索半径は計器へ渡された `f0_hz` 基準で `k*f0_hz*tol` のため、
#: f0 が真値より `p` だけ低いとき真の第 k 倍音は `k*f0_hz*p/(1-p)` 離れる——
#: `p = tol` ちょうどで境界を僅かに超える（3% 誤差 → 3.093% の隔たり）。
#: Meter Bench 実測でこれが顕在化するのは K8 × `err-3pct` × f0=440Hz の 4 case
#: だけ: 半径は bin 単位へ `ceil` で切り上げるため f0=110/220Hz では窓が僅かに
#: 足りて第 8 倍音が入る（110Hz: 必要 26.4Hz vs 半径 26.7Hz）が、440Hz では
#: 足りない（必要 105.6Hz vs 半径 102.7Hz）。第 8 倍音 1 点を取り違えると最小
#: 二乗がそこへ引きずられて 12〜13 dB/oct 外れる一方、中央値ベースの Theil-Sen
#: は K8 でも許容内（worst 0.39〜0.70 dB/oct）に留まり、K4/K6 は回帰が第 8 倍音を
#: 含まないため OLS も許容内。**ここは緩めない** — 半径を広げれば通る候補は
#: 増えるが、それは「宣言 f0 不確かさ ≥ 探索半径」という設計条件を先に立てる
#: べき論点であり、次の memo の仕事。
PEAK_SEARCH_TOL: float = 0.03


def harmonic_amplitudes_db_peak(
    signal: np.ndarray,
    sr: int,
    f0_hz: float,
    k_max: int,
    window_name: str,
    tol: float = PEAK_SEARCH_TOL,
) -> list[float | None]:
    """`harmonic_amplitudes_db()` のピーク探索版（RUN10-CAL リセット設計 v0 §2）。

    既存の `harmonic_amplitudes_db()` は `k*f0` の bin を**固定で読む**ため、
    渡された `f0_hz` の誤差が次数 `k` 倍に増幅されて振幅を取り違える
    （Meter Bench 実測: f0 誤差 ±1〜3% で slope を 5〜27 dB/oct 外す）。本関数は
    `k*f0` の周囲 `±ceil(k*f0*tol/bin_hz)` bin を探索して局所ピークを取り、
    その頂点で放物線補間する。

    リセット設計 v0 §2 の式との差分は 1 点のみ: 設計は `_parabolic_interp_db
    (log_mag_db, idx)`（整数 bin = 補間なし）と書くが、それでは窓のスキャロップ
    損失（ピークが bin 間に落ちたときの振幅の目減り）が補正されず、実測の worst
    が 1.58〜14.36 dB/oct で許容 1.0 を満たさない。3 点放物線の**頂点位置** `d`
    を求めてから `idx + d` で評価すると worst 0.40〜0.86（K4/K6）へ下がる。

    `harmonic_amplitudes_db()` は一切変更しない（歴史 campaign が registry sha と
    実装を pin しているため、変更ではなく追加）。取得できない倍音は None。
    """
    if not np.isfinite(f0_hz) or f0_hz <= 0:
        return [None] * k_max
    analysis = _analysis_window(np.asarray(signal, dtype=float))
    window = _WINDOWS[window_name](len(analysis))
    spec = np.abs(np.fft.rfft(analysis * window))
    log_mag_db = 20.0 * np.log10(spec + 1e-12)
    freqs = np.fft.rfftfreq(len(analysis), d=1.0 / sr)
    bin_hz = freqs[1] - freqs[0] if len(freqs) > 1 else 1.0

    out: list[float | None] = []
    for k in range(1, k_max + 1):
        target_hz = k * f0_hz
        if target_hz >= sr / 2.0 * 0.98:
            out.append(None)
            continue
        center = int(round(target_hz / bin_hz))
        half = max(1, int(np.ceil(target_hz * tol / bin_hz)))
        lo, hi = center - half, center + half + 1
        if lo <= 0 or hi >= len(log_mag_db) - 1:
            out.append(None)
            continue
        idx = lo + int(np.argmax(log_mag_db[lo:hi]))
        out.append(_parabolic_interp_db(log_mag_db, idx + _vertex_offset(log_mag_db, idx)))
    return out


def _vertex_offset(log_mag_db: np.ndarray, idx: int) -> float:
    """`idx` 近傍 3 点が張る放物線の頂点の bin オフセット（[-0.5, 0.5] へクランプ）。"""
    y0, y1, y2 = log_mag_db[idx - 1], log_mag_db[idx], log_mag_db[idx + 1]
    denominator = y0 - 2.0 * y1 + y2
    if denominator == 0.0:
        return 0.0
    return float(np.clip(0.5 * (y0 - y2) / denominator, -0.5, 0.5))


def _regression_inputs(amplitudes_db: list[float | None]) -> tuple[np.ndarray, np.ndarray] | None:
    xs, ys = [], []
    for k, amp_db in enumerate(amplitudes_db, start=1):
        if amp_db is None:
            return None  # 縮退せず missing（K 本すべて揃わなければ全体を missing とする）
        xs.append(np.log2(k))
        ys.append(amp_db)
    return np.array(xs), np.array(ys)


def tilt_ols_db_per_oct(signal: np.ndarray, sr: int, f0_hz: float, *, k: int, window: str) -> float | None:
    amps = harmonic_amplitudes_db(signal, sr, f0_hz, k, window)
    inputs = _regression_inputs(amps)
    if inputs is None:
        return None
    xs, ys = inputs
    slope, _intercept = np.polyfit(xs, ys, 1)
    return float(slope)


def tilt_theilsen_db_per_oct(signal: np.ndarray, sr: int, f0_hz: float, *, k: int, window: str) -> float | None:
    amps = harmonic_amplitudes_db(signal, sr, f0_hz, k, window)
    inputs = _regression_inputs(amps)
    if inputs is None:
        return None
    xs, ys = inputs
    slope, _intercept, _lo, _hi = theilslopes(ys, xs)
    return float(slope)


def tilt_ols_peak_db_per_oct(
    signal: np.ndarray, sr: int, f0_hz: float, *, k: int, window: str
) -> float | None:
    inputs = _regression_inputs(harmonic_amplitudes_db_peak(signal, sr, f0_hz, k, window))
    if inputs is None:
        return None
    xs, ys = inputs
    slope, _intercept = np.polyfit(xs, ys, 1)
    return float(slope)


def tilt_theilsen_peak_db_per_oct(
    signal: np.ndarray, sr: int, f0_hz: float, *, k: int, window: str
) -> float | None:
    inputs = _regression_inputs(harmonic_amplitudes_db_peak(signal, sr, f0_hz, k, window))
    if inputs is None:
        return None
    xs, ys = inputs
    slope, _intercept, _lo, _hi = theilslopes(ys, xs)
    return float(slope)


def _measure(
    signal: np.ndarray, sr: int, params: Mapping[str, object], estimator
) -> MeterOutput:
    f0_hz = float(params.get("f0_hz", float("nan")))
    k = int(params["k"])
    window = str(params["window"])
    slope = estimator(signal, sr, f0_hz, k=k, window=window)
    if slope is None:
        return MeterOutput(missing_reason=vocab.MissingReason.OUTPUT_MISSING)
    values: dict[str, float] = {"tilt_db_per_oct": slope}
    # v1.3 §X1: 検出判定用の harmonicity 補助値。非有限（SILENCE 等）は
    # キーを出さない = `detected()` の「field 欠落は非発火」へ落とす。
    hnr = detection_hnr_acf_db(signal, sr)
    if np.isfinite(hnr):
        values["hnr_acf_db"] = float(hnr)
    return MeterOutput(values=values)


def measure_ols(signal: np.ndarray, sr: int, params: Mapping[str, object]) -> MeterOutput:
    return _measure(signal, sr, params, tilt_ols_db_per_oct)


def measure_theilsen(signal: np.ndarray, sr: int, params: Mapping[str, object]) -> MeterOutput:
    return _measure(signal, sr, params, tilt_theilsen_db_per_oct)


def measure_ols_peak(signal: np.ndarray, sr: int, params: Mapping[str, object]) -> MeterOutput:
    return _measure(signal, sr, params, tilt_ols_peak_db_per_oct)


def measure_theilsen_peak(
    signal: np.ndarray, sr: int, params: Mapping[str, object]
) -> MeterOutput:
    return _measure(signal, sr, params, tilt_theilsen_peak_db_per_oct)
