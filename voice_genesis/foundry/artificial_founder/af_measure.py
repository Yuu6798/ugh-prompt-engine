"""af_measure.py — Body / re-expression の**盲目**測定器（設計書 §14 / §15）。

設計書 §14 の測定器分離をコードで担保する。

```text
禁止: af_measure.py -> founder_specs/AF0.json
許可: af_compare.py -> AF0.json
```

そのため本モジュールは:

- `af_spec` / `af_synth` / `af_compare` を **import しない**（AF0 の設計値が
  import 閉包から到達不能であることを機械的に保証する）。
- 受け取ってよい設定は `criteria.metric_definitions` 部分木だけ。ここには
  「どう測るか」しか入っていない。
- ファイルパスを受け取る入口に read tripwire を置き、Founder Genome / criteria
  本体 / controls / probes を開こうとしたら送出する。

**結果を見て peak finder を変更しない**（§15.3）。本ファイルの推定器はすべて
AF0 の測定を走らせる前に §17 の control で方向弁別を検証する。

主要な推定器の設計理由（いずれも事前登録）:

- **包絡モデル当てはめ**（`fit_envelope_model`）。F0 = 261.6 Hz の倍音格子は
  261 Hz 間隔でしかスペクトルを標本化しないので、ピーク拾いでは /i/ の
  F1 = 280 Hz も、F3 と 100 Hz しか離れていない AR-α も分離できない。§15.2 の
  「複数倍音を band-normalized aggregation」・§15.3 の「既知の局所 band model」を、
  「源ロールオフ + parity 段差 + 5 個の Lorentzian dB 山」という 1 つの模型族へ
  まとめ、線形係数は厳密最小二乗・非線形係数は決定論的座標降下で解く。
- **残光プローブ**（`_afterglow_probe`）。§10 の設計により終端 35 ms は AR-α
  単独の純音なので、AR-α 中心はここから母音に依らず同定できる。
- **半コサインの線形化**（`fit_half_cosine_decay`）。t90/t10 の 2 点交差は
  勾配が浅く包絡リップルで数 ms 滑るため使わない。
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import soundfile as sf
from scipy.signal import butter, get_window, sosfiltfilt

#: read tripwire。これらを含むパスを `af_measure` が開いたら Source of Truth
#: 汚染なので即座に落とす（§14 / §29 test 49）。
FORBIDDEN_PATH_TOKENS: Tuple[str, ...] = (
    "founder_specs", "AF0.json", "AF_P0_CRITERIA.json", "AF_P0_CONTROLS.json",
    "AF_P0_PROBES.json", "unit_truth.json", "founder_manifest.json",
)


class MeterTripwire(RuntimeError):
    """測定器が Ground Truth 側の成果物へ触れようとした（§14 違反）。"""


def guard_read_path(path: str | Path) -> Path:
    p = Path(path)
    lowered = p.as_posix()
    for token in FORBIDDEN_PATH_TOKENS:
        if token in lowered:
            raise MeterTripwire(
                f"af_measure must not read {token!r} (§14 measurement isolation): {p}")
    return p


# ---------------------------------------------------------------------------
# 低レベルユーティリティ
# ---------------------------------------------------------------------------
def _db(x: float) -> float:
    return float("-inf") if x <= 0.0 else 20.0 * math.log10(x)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x ** 2))) if x.size else 0.0


def _parabolic_peak(y: np.ndarray, i: int) -> Tuple[float, float]:
    """離散ピーク周りの放物線頂点 `(offset, value)` を返す（-0.5 <= offset <= 0.5）。"""
    if i <= 0 or i >= y.size - 1:
        return 0.0, float(y[i])
    a, b, c = float(y[i - 1]), float(y[i]), float(y[i + 1])
    denom = a - 2.0 * b + c
    if denom == 0.0:
        return 0.0, b
    off = 0.5 * (a - c) / denom
    off = max(-0.5, min(0.5, off))
    return off, b - 0.25 * (a - c) * off


def rms_envelope(x: np.ndarray, sr: int, hop_ms: float, win_ms: float) -> Tuple[np.ndarray,
                                                                               np.ndarray]:
    """(時刻 ms, RMS) を返す中心合わせ短時間 RMS 包絡。"""
    hop = max(1, int(round(hop_ms * sr / 1000.0)))
    win = max(2, int(round(win_ms * sr / 1000.0)))
    half = win // 2
    pad = np.pad(x, (half, half), mode="constant")
    idx = np.arange(0, x.size, hop)
    sq = np.cumsum(np.concatenate(([0.0], pad ** 2)))
    env = np.sqrt(np.maximum(sq[idx + win] - sq[idx], 0.0) / float(win))
    return idx * 1000.0 / sr, env


def _longest_run(mask: np.ndarray) -> Tuple[int, int]:
    """`mask` が True の最長連続区間 `[start, end)` を返す（無ければ (0, 0)）。"""
    best = (0, 0)
    start: Optional[int] = None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start > best[1] - best[0]:
                best = (start, i)
            start = None
    if start is not None and mask.size - start > best[1] - best[0]:
        best = (start, mask.size)
    return best


def _first_index_at_or_above(y: np.ndarray, level: float) -> Optional[int]:
    """`y` が初めて `level` 以上になる索引（立ち上がり探索窓の終端に使う）。"""
    idx = np.nonzero(y >= level)[0]
    return int(idx[0]) if idx.size else None


def _crossing_time(t: np.ndarray, y: np.ndarray, level: float, lo: int, hi: int,
                   rising: bool) -> Optional[float]:
    """`[lo, hi)` で `level` を（rising なら上向きに、最後に）横切る時刻。"""
    idxs = range(hi - 1, lo, -1) if rising else range(lo + 1, hi)
    for i in idxs:
        a, b = float(y[i - 1]), float(y[i])
        if rising and a < level <= b:
            frac = (level - a) / (b - a) if b != a else 0.0
            return float(t[i - 1] + frac * (t[i] - t[i - 1]))
        if not rising and a >= level > b:
            frac = (a - level) / (a - b) if a != b else 0.0
            return float(t[i - 1] + frac * (t[i] - t[i - 1]))
    return None


# ---------------------------------------------------------------------------
# スペクトル
# ---------------------------------------------------------------------------
def magnitude_spectrum_db(x: np.ndarray, sr: int, nfft: int,
                          window: str) -> Tuple[np.ndarray, np.ndarray]:
    n = x.size
    if n < 64:
        return np.zeros(0), np.zeros(0)
    nfft = max(nfft, 1 << int(np.ceil(np.log2(n))))
    w = get_window(window, n, fftbins=True)
    spec = np.abs(np.fft.rfft(x * w, nfft))
    return np.fft.rfftfreq(nfft, 1.0 / sr), 20.0 * np.log10(np.maximum(spec, 1e-30))


def harmonic_peaks(freqs: np.ndarray, mag_db: np.ndarray, f0_hz: float,
                   harmonic_max: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """倍音 h=1..H の (次数, 周波数, 振幅 dB) をピーク補間つきで返す。"""
    if freqs.size == 0 or f0_hz <= 0.0:
        return np.zeros(0), np.zeros(0), np.zeros(0)
    df = float(freqs[1] - freqs[0])
    nyq = float(freqs[-1])
    hs: List[float] = []
    fs: List[float] = []
    ms: List[float] = []
    for h in range(1, harmonic_max + 1):
        target = h * f0_hz
        if target >= nyq - 2 * df:
            break
        lo = max(1, int((target - 0.4 * f0_hz) / df))
        hi = min(mag_db.size - 2, int((target + 0.4 * f0_hz) / df))
        if hi <= lo:
            break
        i = int(np.argmax(mag_db[lo:hi + 1])) + lo
        off, val = _parabolic_peak(mag_db, i)
        hs.append(float(h))
        fs.append((i + off) * df)
        ms.append(val)
    return np.asarray(hs), np.asarray(fs), np.asarray(ms)


def refine_f0_from_harmonics(orders: np.ndarray, freqs: np.ndarray,
                             index_range: Sequence[int]) -> Optional[float]:
    """倍音位置の最小二乗から F0 を精密化する（`F0 = Σ h·f_h / Σ h²`）。

    自己相関だけでは 0.2% (≈3.5 cent) 程度の誤差が残り、§18.1 の `<= 5 cent` に
    対して余裕が無い。倍音位置を使うと 20 本の平均で 0.3 cent 程度まで下がる。
    """
    lo, hi = int(index_range[0]), int(index_range[1])
    m = (orders >= lo) & (orders <= hi)
    if not m.any():
        return None
    h, f = orders[m], freqs[m]
    denom = float(np.dot(h, h))
    return float(np.dot(h, f) / denom) if denom > 0.0 else None


# ---------------------------------------------------------------------------
# §15.1 / §15.2 / §15.3 事前登録の包絡モデル
# ---------------------------------------------------------------------------
def _lorentz(freqs: np.ndarray, center: float, bandwidth: float) -> np.ndarray:
    """dB 領域の Lorentzian 山（頂点 1.0、半値全幅 = bandwidth）。peaking EQ 用。"""
    return 1.0 / (1.0 + (2.0 * (freqs - center) / max(bandwidth, 1e-6)) ** 2)


def _resonator_response(freqs: np.ndarray, center: float, bandwidth: float,
                        sr: int) -> np.ndarray:
    """共鳴周波数で振幅 1 に正規化した 2 極共鳴器の複素応答（模型族の素片）。"""
    r = math.exp(-math.pi * max(bandwidth, 1e-6) / sr)
    theta = 2.0 * math.pi * center / sr
    a1 = -2.0 * r * math.cos(theta)
    a2 = r * r
    zc = np.exp(-1j * theta)
    b0 = abs(1.0 + a1 * zc + a2 * zc * zc)
    z = np.exp(-2j * math.pi * np.asarray(freqs, dtype=np.float64) / sr)
    return b0 / (1.0 + a1 * z + a2 * z * z)


def parallel_bank_db(freqs: np.ndarray, centers: Sequence[float], bandwidths: Sequence[float],
                     sr: int, polarity: Sequence[float]) -> np.ndarray:
    """並列 formant バンクの対数振幅応答（事前登録の generic phonetic topology）。"""
    h = np.zeros(freqs.size, dtype=np.complex128)
    for i, (c, b) in enumerate(zip(centers, bandwidths)):
        h = h + float(polarity[i % len(polarity)]) * _resonator_response(freqs, c, b, sr)
    return 20.0 * np.log10(np.maximum(np.abs(h), 1e-30))


def _solve_linear(design: np.ndarray, target: np.ndarray,
                  nonneg_from: int) -> Tuple[np.ndarray, float]:
    """線形係数を最小二乗で解く。`nonneg_from` 以降の係数が負なら 0 に固定して解き直す。"""
    def _ls(a: np.ndarray) -> np.ndarray:
        # 正規方程式 + 微小 Tikhonov。列数は 6 固定なので lstsq より一桁速く、
        # かつ同一入力に対して決定論的（座標降下を何万回も回すため効く）。
        ata = a.T @ a
        ata.flat[:: ata.shape[0] + 1] += 1e-9 * (float(np.trace(ata)) / ata.shape[0] + 1.0)
        return np.linalg.solve(ata, a.T @ target)

    coef = _ls(design)
    neg = {i for i in range(nonneg_from, design.shape[1]) if coef[i] < 0.0}
    if neg:
        keep = [i for i in range(design.shape[1]) if i not in neg]
        coef = np.zeros(design.shape[1], dtype=np.float64)
        if keep:
            coef[keep] = _ls(design[:, keep])
    resid = float(np.mean((design @ coef - target) ** 2))
    return coef, resid


def fit_envelope_model(orders: np.ndarray, freqs: np.ndarray, mag_db: np.ndarray, sr: int,
                       mem: Mapping[str, Any], mfm: Mapping[str, Any], mar: Mapping[str, Any],
                       alpha_center_fixed: Optional[float]) -> Optional[Dict[str, Any]]:
    """倍音振幅（dB）へ事前登録モデルを当て、formant / AR / parity を同時推定する。

    模型族（`criteria.metric_definitions.envelope_model`）:

    ```text
    M(f_h) = C + D·parity(h) - 20p·log10(h)
             + 20log10|Σ_i s_i R(f_h; F_i, B_i)|        (並列 formant バンク)
             + A_alpha·L(f_h; F_alpha, B_alpha)         (AR-alpha peaking)
             + A_beta ·L(f_h; F_beta,  B_beta)          (AR-beta  peaking)
    ```

    `C / D / p / A_alpha / A_beta` は dB 上で線形なので厳密最小二乗で解き、
    非線形（中心・帯域）だけを**決定論的な座標降下グリッド**で詰める。乱数も
    時刻も使わないので、同じ入力からは常に同じ解が出る（§27 決定論）。

    F0 = 261.6 Hz の倍音格子はスペクトルを 261 Hz 間隔でしか標本化しないので、
    /i/ の F1 = 280 Hz はピーク拾いでは取れない。模型全体の形が F1 を拘束する
    ことで初めて回収できる（§15.1 が要求する精度に対する事前登録の手当）。
    """
    lo_h, hi_h = (int(v) for v in mem["harmonic_index_range"])
    m = (orders >= lo_h) & (orders <= hi_h) & np.isfinite(mag_db)
    if m.sum() < 12:
        return None
    o, f, y = orders[m], freqs[m], mag_db[m]

    f_bounds = [(float(b[0]), float(b[1])) for b in mfm["search_hz"]]
    sep = float(mfm["min_separation_hz"])
    fb = tuple(float(v) for v in mem["formant_bandwidth_bounds_hz"])
    ab = tuple(float(v) for v in mem["ar_alpha_bandwidth_bounds_hz"])
    bb = tuple(float(v) for v in mem["ar_beta_bandwidth_bounds_hz"])
    a_lo, a_hi = (float(v) for v in mar["alpha_search_hz"])
    b_lo, b_hi = (float(v) for v in mar["beta_search_hz"])
    polarity = [float(v) for v in mem["parallel_polarity"]]

    parity = np.where(o % 2 == 1, 1.0, -1.0)
    rolloff = -20.0 * np.log10(np.maximum(o, 1.0))

    # 初期化: 低次トレンドを引いた残差のピークから F1/F2/F3 を置く。
    # 並び: [F1,B1,F2,B2,F3,B3,Fa,Ba,Fb,Bb]。中心と帯域を交互に詰める（中心だけを
    # 先に全部動かすと、初期帯域が外れているときに誤った局所解へ落ちる）。
    bounds = [f_bounds[0], fb, f_bounds[1], fb, f_bounds[2], fb,
              (a_lo, a_hi), ab, (b_lo, b_hi), bb]
    frozen = {6} if alpha_center_fixed is not None else set()
    alpha_init = alpha_center_fixed if alpha_center_fixed is not None else 0.5 * (a_lo + a_hi)

    def _design(p: Sequence[float]) -> np.ndarray:
        bank = parallel_bank_db(f, (p[0], p[2], p[4]), (p[1], p[3], p[5]), sr, polarity)
        return np.stack([np.ones_like(f), parity, rolloff, bank,
                         _lorentz(f, p[6], p[7]), _lorentz(f, p[8], p[9])], axis=1)

    def _cost(p: Sequence[float]) -> float:
        if not (p[0] + sep <= p[2] and p[2] + sep <= p[4]):
            return float("inf")
        _, r = _solve_linear(_design(p), y, nonneg_from=4)
        return r

    def _descend(p0: List[float], sweeps: int, n_grid: int,
                 shrink0: float) -> Tuple[List[float], float]:
        """局所座標降下。`shrink0` は各パラメータの探索幅（境界幅に対する比）。

        screening 段は **局所** に留める（shrink0 を小さくする）。全域を掃くと
        どの初期点からも同じ極小へ落ちてしまい、多点初期化の意味が消えるため。
        """
        p = list(p0)
        best = _cost(p)
        for sweep in range(sweeps):
            shrink = shrink0 * (0.5 ** sweep)
            for k in range(len(p)):
                if k in frozen:
                    continue
                lo_b, hi_b = bounds[k]
                span = (hi_b - lo_b) * shrink
                grid = np.linspace(max(lo_b, p[k] - 0.5 * span),
                                   min(hi_b, p[k] + 0.5 * span), n_grid)
                best_v = p[k]
                for v in grid:
                    trial = list(p)
                    trial[k] = float(v)
                    c = _cost(trial)
                    if c < best:
                        best, best_v = c, float(v)
                p[k] = best_v
        return p, best

    # 決定論的多点初期化: 低次トレンドを引いた残差の上位ピークから候補中心を作る。
    trend = np.polyval(np.polyfit(np.log10(np.maximum(f, 1.0)), y, 2),
                       np.log10(np.maximum(f, 1.0)))
    resid0 = y - trend
    cand: List[List[float]] = []
    n_top = [int(v) for v in mem["init_candidates_per_band"]]
    min_gap = float(mem["init_candidate_min_gap_hz"])
    for k, (blo, bhi) in enumerate(f_bounds):
        sel = np.nonzero((f >= blo) & (f <= bhi))[0]
        if sel.size == 0:
            cand.append([0.5 * (blo + bhi)])
            continue
        # 候補は残差の **極大** に限る。単純な上位 N だと 1 つの山の肩（例 /u/ の
        # F3 ピーク両側）が候補枠を占有し、別の formant（F2=950 Hz）の候補が
        # 落ちる。極大に限ったうえで、互いに `min_gap` 以上離れた点を貪欲に選ぶ。
        local = [i for i in sel
                 if (i == 0 or resid0[i] > resid0[i - 1])
                 and (i == resid0.size - 1 or resid0[i] >= resid0[i + 1])]
        picks: List[float] = []
        for pool in (np.asarray(local, dtype=int) if local else sel[:0], sel):
            if pool.size == 0:
                continue
            for i in pool[np.argsort(-resid0[pool])]:
                fi = float(f[i])
                if all(abs(fi - q) >= min_gap for q in picks):
                    picks.append(fi)
                if len(picks) >= n_top[k]:
                    break
            if len(picks) >= n_top[k]:
                break
        picks.append(0.5 * (blo + bhi))
        cand.append(sorted(set(round(v, 3) for v in picks)))

    n_grid = int(mem["grid_points"])
    starts: List[List[float]] = []
    for c1 in cand[0]:
        for c2 in cand[1]:
            for c3 in cand[2]:
                if c1 + sep <= c2 and c2 + sep <= c3:
                    starts.append([c1, 90.0, c2, 120.0, c3, 160.0,
                                   alpha_init, 0.5 * (ab[0] + ab[1]),
                                   0.5 * (b_lo + b_hi), 0.5 * (bb[0] + bb[1])])
    if not starts:
        starts = [[0.5 * (a + b) for a, b in f_bounds][0], 90.0,
                  0.5 * sum(f_bounds[1]), 120.0, 0.5 * sum(f_bounds[2]), 160.0,
                  alpha_init, 0.5 * (ab[0] + ab[1]), 0.5 * (b_lo + b_hi),
                  0.5 * (bb[0] + bb[1])]
        starts = [starts]

    screen_shrink = float(mem["screen_shrink"])
    refine_shrink = float(mem["refine_shrink"])
    screened = sorted((_descend(s, 3, max(9, n_grid // 2), screen_shrink) for s in starts),
                      key=lambda r: (r[1], r[0]))
    finals = [_descend(s, int(mem["sweeps"]), n_grid, refine_shrink)
              for s, _ in screened[:int(mem["refine_top_k"])]]
    params, _ = min(finals, key=lambda r: (r[1], r[0]))

    coef, resid = _solve_linear(_design(params), y, nonneg_from=4)
    return {
        "formants_hz": [params[0], params[2], params[4]],
        "formant_bandwidths_hz": [params[1], params[3], params[5]],
        "ar_alpha_center_hz": params[6],
        "ar_alpha_bandwidth_hz": params[7],
        "ar_alpha_gain_db": float(coef[4]),
        "ar_beta_center_hz": params[8],
        "ar_beta_bandwidth_hz": params[9],
        "ar_beta_gain_db": float(coef[5]),
        "level_db": float(coef[0]),
        "parity_db": float(coef[1]),
        "rolloff_power": float(coef[2]),
        "bank_gain": float(coef[3]),
        "residual_db2": resid,
        "n_harmonics": int(o.size),
    }


# ---------------------------------------------------------------------------
# §15.7 Release
# ---------------------------------------------------------------------------
def fit_half_cosine_decay(t: np.ndarray, env: np.ndarray, level: float, start_idx: int,
                          frac_range: Sequence[float]) -> Optional[Dict[str, float]]:
    """`env = level·0.5(1+cos(π(t-t0)/T))` を線形化して閉形式で解く。

    `u = acos(2·env/level - 1)/π` は `t` の一次関数 `(t - t0)/T` なので、
    有効域の全点を使った最小二乗直線が `(t0, T)` を与える。2 点交差法と違い
    包絡リップルが平均化される。
    """
    lo_f, hi_f = float(frac_range[0]), float(frac_range[1])
    ratio = env / max(level, 1e-12)
    idx = np.arange(env.size)
    m = (idx > start_idx) & (ratio >= lo_f) & (ratio <= hi_f)
    if m.sum() < 8:
        return None
    # 立ち下がりの **最長** 連続塊を使う。plateau のリップルが有効域へ数点だけ
    # 落ち込むことがあり、「最初の塊」を採ると本体の taper を取り逃がす。
    runs: List[Tuple[int, int]] = []
    s: Optional[int] = None
    for i, v in enumerate(m):
        if v and s is None:
            s = i
        elif not v and s is not None:
            runs.append((s, i))
            s = None
    if s is not None:
        runs.append((s, m.size))
    if not runs:
        return None
    a, b = max(runs, key=lambda r: r[1] - r[0])
    sel = np.arange(a, b)
    if sel.size < 8:
        return None
    u = np.arccos(np.clip(2.0 * ratio[sel] - 1.0, -1.0, 1.0)) / math.pi
    tt = t[sel]
    slope, intercept = np.polyfit(tt, u, 1)
    if slope <= 0.0:
        return None
    T = 1.0 / float(slope)
    t0 = -float(intercept) * T
    pred = (tt - t0) / T
    denom = float(np.std(u) * np.std(pred))
    corr = float(np.mean((u - u.mean()) * (pred - pred.mean())) / denom) if denom > 0 else None
    return {"release_ms": T, "taper_start_ms": t0, "fit_corr": corr,
            "n_points": int(sel.size)}


# ---------------------------------------------------------------------------
# F0 トラック
# ---------------------------------------------------------------------------
def f0_track(x: np.ndarray, sr: int, f_lo: float, f_hi: float, frame_ms: float,
             hop_ms: float, voiced_min: float) -> Tuple[np.ndarray, np.ndarray]:
    """正規化自己相関による F0 トラック `(時刻 ms, f0 Hz; 無声は 0)`。"""
    n = max(64, int(round(frame_ms * sr / 1000.0)))
    hop = max(1, int(round(hop_ms * sr / 1000.0)))
    lag_lo = max(2, int(sr / f_hi))
    lag_hi = min(n - 2, int(sr / f_lo))
    win = np.hanning(n)
    nfft = 1 << int(np.ceil(np.log2(2 * n)))
    times: List[float] = []
    f0s: List[float] = []
    for s in range(0, max(1, x.size - n + 1), hop):
        seg = x[s:s + n]
        if seg.size < n:
            break
        seg = (seg - seg.mean()) * win
        times.append((s + n / 2.0) * 1000.0 / sr)
        ac = np.fft.irfft(np.abs(np.fft.rfft(seg, nfft)) ** 2, nfft)[:n]
        if ac[0] <= 0.0 or lag_hi <= lag_lo:
            f0s.append(0.0)
            continue
        i = int(np.argmax(ac[lag_lo:lag_hi + 1])) + lag_lo
        if ac[i] / ac[0] < voiced_min:
            f0s.append(0.0)
            continue
        off, _ = _parabolic_peak(ac, i)
        f0s.append(sr / (i + off))
    return np.asarray(times), np.asarray(f0s)


# ---------------------------------------------------------------------------
# §15.8 Afterglow
# ---------------------------------------------------------------------------
def band_envelope(x: np.ndarray, sr: int, band: Sequence[float], hop_ms: float,
                  win_ms: float, order: int = 6) -> Tuple[np.ndarray, np.ndarray]:
    lo = max(20.0, float(band[0]))
    hi = min(0.5 * sr - 50.0, float(band[1]))
    if hi <= lo:
        return np.zeros(0), np.zeros(0)
    sos = butter(order, [lo / (0.5 * sr), hi / (0.5 * sr)], btype="bandpass", output="sos")
    y = np.asarray(sosfiltfilt(sos, x), dtype=np.float64)
    return rms_envelope(y, sr, hop_ms, win_ms)


def _disappearance_ms(t: np.ndarray, env: np.ndarray, threshold_db: float,
                      ref_window_ms: Optional[Sequence[float]] = None) -> Optional[float]:
    """基準レベルに対し `threshold_db` を最後に下回る時刻。

    基準は **sustain 区間の中央値**（`ref_window_ms`）を使う。全体のピークを基準に
    すると、子音 onset（/k/ の burst は 3.4 kHz 帯にも漏れる）が基準を持ち上げ、
    残光の消失時刻が unit ごとに数 ms ずれる。
    """
    if env.size == 0:
        return None
    ref = None
    if ref_window_ms is not None:
        m = (t >= float(ref_window_ms[0])) & (t <= float(ref_window_ms[1]))
        if m.any():
            ref = float(np.median(env[m]))
    if not ref or ref <= 0.0:
        ref = float(np.max(env))
    if ref <= 0.0:
        return None
    level = ref * (10.0 ** (threshold_db / 20.0))
    above = np.nonzero(env >= level)[0]
    if above.size == 0:
        return None
    i = int(above[-1])
    if i + 1 < env.size:
        a, b = float(env[i]), float(env[i + 1])
        frac = (a - level) / (a - b) if a != b else 0.0
        return float(t[i] + max(0.0, min(1.0, frac)) * (t[i + 1] - t[i]))
    return float(t[i])


def _afterglow_probe(x: np.ndarray, sr: int, mar: Mapping[str, Any], sig_end_ms: float,
                     window: str) -> Tuple[Optional[float], Optional[float]]:
    """終端窓のスペクトルから AR-α 中心と卓立を取る（§10 の設計で純音になる）。"""
    a_lo, a_hi = (float(v) for v in mar["alpha_search_hz"])
    probe_n = int(round(float(mar["afterglow_probe_ms"]) * sr / 1000.0))
    end_i = min(x.size, int(round(sig_end_ms * sr / 1000.0)) + 1)
    seg = x[max(0, end_i - probe_n):end_i]
    if seg.size < 64:
        return None, None
    w = get_window(window, seg.size, fftbins=True)
    nfft = 1 << int(np.ceil(np.log2(seg.size * 8)))
    sp = 20.0 * np.log10(np.maximum(np.abs(np.fft.rfft(seg * w, nfft)), 1e-30))
    ff = np.fft.rfftfreq(nfft, 1.0 / sr)
    m = (ff >= a_lo) & (ff <= a_hi)
    r_lo, r_hi = (float(v) for v in mar["afterglow_probe_reference_band_hz"])
    ref = (ff >= r_lo) & (ff <= min(r_hi, 0.5 * sr - 100.0)) & ~m
    if not m.any() or not ref.any():
        return None, None
    idx = np.nonzero(m)[0]
    j = int(idx[int(np.argmax(sp[idx]))])
    off, val = _parabolic_peak(sp, j)
    prom = float(val - np.median(sp[ref]))
    if prom < float(mar["afterglow_probe_min_prominence_db"]):
        return None, prom
    return float((j + off) * (ff[1] - ff[0])), prom


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------
def measure_unit(x: np.ndarray, sr: int, md: Mapping[str, Any], stem: str) -> Dict[str, Any]:
    """1 unit の全 metric を測る。**AF0 の設計値は一切参照しない。**

    `stem` は WAV ファイル名（Body の一部であって Ground Truth ではない）。
    どの alias でどの family を測るかは `criteria.metric_definitions` の
    `measured_on_aliases` が事前登録している。
    """
    x = np.asarray(x, dtype=np.float64)
    out: Dict[str, Any] = {"stem": stem, "sr": int(sr), "n_samples": int(x.size)}
    ana, mdur, men = md["analysis"], md["duration"], md["energy"]
    mrel, mf0, mhl = md["release"], md["f0"], md["harmonic_lattice"]
    mar, mfm, mag = md["founder_resonance"], md["formants"], md["afterglow"]
    mem, msi = md["envelope_model"], md["spectral_identity"]

    # --- 無音・終端 digital zero -------------------------------------------
    thr = 10.0 ** (float(mdur["silence_threshold_dbfs"]) / 20.0)
    nz = np.nonzero(np.abs(x) > thr)[0]
    if nz.size == 0:
        out["error"] = "silent unit"
        return out
    sig_start_ms = float(nz[0]) * 1000.0 / sr
    sig_end_ms = float(nz[-1]) * 1000.0 / sr
    exact_nz = np.nonzero(x != 0.0)[0]
    out["signal_start_ms"] = sig_start_ms
    out["signal_end_ms"] = sig_end_ms
    out["trailing_zero_ms"] = (float(x.size - 1 - int(exact_nz[-1])) * 1000.0 / sr
                               if exact_nz.size else float(x.size) * 1000.0 / sr)
    out["final_sample_is_zero"] = bool(x[-1] == 0.0)

    # --- 包絡・plateau ------------------------------------------------------
    t_env, env = rms_envelope(x, sr, float(men["envelope_hop_ms"]),
                              float(men["envelope_window_ms"]))
    peak_env = float(np.percentile(env, float(ana["plateau_peak_percentile"])))
    plateau_lo, plateau_hi = _longest_run(env >= float(ana["plateau_threshold"]) * peak_env)
    if plateau_hi - plateau_lo < 4:
        out["error"] = "no sustain plateau"
        return out
    frac_lo, frac_hi = ana["sustain_probe_fraction"]
    span = plateau_hi - plateau_lo
    probe_lo = plateau_lo + int(round(frac_lo * span))
    probe_hi = min(env.size - 1, plateau_lo + int(round(frac_hi * span)))
    plateau_level = float(np.median(env[probe_lo:max(probe_hi, probe_lo + 1)]))
    s_lo = int(round(t_env[probe_lo] * sr / 1000.0))
    s_hi = min(x.size, int(round(t_env[probe_hi] * sr / 1000.0)))
    sustain = x[s_lo:s_hi]
    out["sustain_probe_ms"] = [float(t_env[probe_lo]), float(t_env[probe_hi])]

    # --- Release（§15.7）---------------------------------------------------
    t_rel, e_rel = rms_envelope(x, sr, float(mrel["envelope_hop_ms"]),
                                float(mrel["envelope_window_ms"]))
    rel_start = int(np.searchsorted(t_rel, t_env[probe_hi]))
    fit = fit_half_cosine_decay(t_rel, e_rel, plateau_level, rel_start,
                                mrel["fit_range_fraction"])
    release_ms = fit["release_ms"] if fit else None
    taper_start_ms = fit["taper_start_ms"] if fit else None
    out["main_release_ms"] = release_ms
    out["articulation_end_ms"] = taper_start_ms
    out["release_curve_corr"] = fit["fit_corr"] if fit else None

    # --- Duration（§15.5）--------------------------------------------------
    t_dur, e_dur = rms_envelope(x, sr, float(mdur["envelope_hop_ms"]),
                                float(mdur["envelope_window_ms"]))
    # 立ち上がり側の探索窓は「包絡が初めて sustain の 90% に達する点」までとする。
    # plateau_lo で打ち切ると交差を取り逃がし（/ro/ の onset・全母音の attack が
    # None になる）、sustain 全体まで広げると /e/ /o/ のように F1-F2 が近い母音で
    # 包絡リップルが 50% を再交差して境界が 40 ms 後ろへずれる。立ち上がりの
    # 終端で閉じるのが両方に対して安全。
    # 窓の終端は **その包絡自身**が初めて 90% に達する点で決める。別の包絡
    # （窓長が違う）から時刻を持ってくると、境界の直前で窓が閉じて交差を
    # 取り逃がしうる。同じ系列で決めれば「0 から 90% まで上がった」ので
    # 50% 交差が窓内に必ず 1 つ以上ある。
    dur_rise_end = _first_index_at_or_above(e_dur, 0.9 * plateau_level)
    dur_rise_end = e_dur.size if dur_rise_end is None else min(dur_rise_end + 1, e_dur.size)
    onset_ms: Optional[float] = 0.0
    if stem in mdur["measured_on_aliases"]:
        bound = _crossing_time(t_dur, e_dur,
                               float(mdur["onset_boundary_fraction"]) * plateau_level,
                               0, min(dur_rise_end, e_dur.size), True)
        onset_ms = (bound - sig_start_ms) if bound is not None else None
    out["onset_ms"] = onset_ms
    vowel_ms = None
    if taper_start_ms is not None and onset_ms is not None:
        vowel_ms = taper_start_ms - (sig_start_ms + onset_ms)
    out["vowel_ms"] = vowel_ms
    out["r_onset_share"] = (onset_ms / (onset_ms + vowel_ms)
                            if (onset_ms is not None and vowel_ms is not None
                                and onset_ms + vowel_ms > 0.0) else None)
    out["articulation_ms"] = (taper_start_ms - sig_start_ms) if taper_start_ms is not None else None

    # --- Energy（§15.6）----------------------------------------------------
    out["sustain_dbfs"] = _db(_rms(sustain)) if sustain.size else None
    a_lo_f, a_hi_f = men["attack_low_high"]
    if stem in men["measured_on_aliases"]:
        env_rise_end = _first_index_at_or_above(env, 0.95 * plateau_level)
        env_rise_end = env.size if env_rise_end is None else min(env_rise_end + 1, env.size)
        t_a = _crossing_time(t_env, env, a_lo_f * plateau_level, 0, env_rise_end, True)
        t_b = _crossing_time(t_env, env, a_hi_f * plateau_level, 0, env_rise_end, True)
        out["attack_ms"] = (t_b - t_a) if (t_a is not None and t_b is not None) else None
    else:
        out["attack_ms"] = None

    # --- F0（§15.4）--------------------------------------------------------
    f_lo, f_hi = mf0["search_hz"]
    t_f0, f0 = f0_track(x, sr, float(f_lo), float(f_hi), float(mf0["frame_ms"]),
                        float(mf0["hop_ms"]), float(mf0["voiced_autocorr_min"]))
    core_lo_ms = float(t_env[plateau_lo])
    core_hi_ms = core_lo_ms
    core_hz: Optional[float] = None
    if taper_start_ms is not None:
        c0, c1 = (float(v) for v in ana["core_region_fraction"])
        width = taper_start_ms - core_lo_ms
        core_lo_ms, core_hi_ms = core_lo_ms + c0 * width, core_lo_ms + c1 * width
        m = (t_f0 >= core_lo_ms) & (t_f0 <= core_hi_ms) & (f0 > 0)
        if m.any():
            core_hz = float(np.median(f0[m]))

    # --- コア区間のスペクトル ------------------------------------------------
    c_lo = max(0, int(round(core_lo_ms * sr / 1000.0)))
    c_hi = min(x.size, int(round(core_hi_ms * sr / 1000.0)))
    core = x[c_lo:c_hi]
    out["core_probe_ms"] = [core_lo_ms, core_hi_ms]
    freqs, mag_db = magnitude_spectrum_db(core, sr, int(ana["spectrum_nfft"]),
                                          str(ana["fft_window"]))
    orders = fr = mg = np.zeros(0)
    if core_hz and freqs.size:
        hmax = int(mem["harmonic_index_range"][1]) + 2
        orders, fr, mg = harmonic_peaks(freqs, mag_db, core_hz, hmax)
        refined = refine_f0_from_harmonics(orders, fr, mf0["harmonic_refine_index_range"])
        if refined:
            core_hz = refined
            orders, fr, mg = harmonic_peaks(freqs, mag_db, core_hz, hmax)
    out["core_f0_hz"] = core_hz

    term_delta: Optional[float] = None
    if taper_start_ms is not None and core_hz and f0.size:
        p_lo, p_hi = mf0["terminal_probe_after_articulation_ms"]
        mt = (t_f0 >= taper_start_ms + float(p_lo)) & (t_f0 <= taper_start_ms + float(p_hi)) \
            & (f0 > 0)
        if mt.any():
            term_hz = float(np.median(f0[mt]))
            out["terminal_f0_hz"] = term_hz
            term_delta = 1200.0 * math.log2(term_hz / core_hz)
    out["terminal_f0_delta_cents"] = term_delta

    # --- 残光プローブ -> AR-α 中心 ------------------------------------------
    a_center, a_prom = _afterglow_probe(x, sr, mar, sig_end_ms, str(ana["fft_window"]))
    out["ar_alpha_afterglow_prominence_db"] = a_prom
    out["ar_alpha_route"] = "afterglow" if a_center is not None else "envelope_model"

    # --- 包絡モデル当てはめ -------------------------------------------------
    # 包絡モデルは母音 unit だけで走らせる（formant / HL / AR-β は §15.1–15.3 の
    # とおり 5 母音でのみ判定する。子音 unit の AR-α 中心は残光プローブから取る）。
    model = fit_envelope_model(orders, fr, mg, sr, mem, mfm, mar, a_center) \
        if (orders.size and stem in mem["measured_on_aliases"]) else None
    out["envelope_model"] = model
    if model:
        out["formants_hz"] = model["formants_hz"]
        out["ar_alpha_center_hz"] = a_center if a_center is not None \
            else model["ar_alpha_center_hz"]
        out["ar_alpha_prominence_db"] = model["ar_alpha_gain_db"]
        out["ar_beta_center_hz"] = model["ar_beta_center_hz"]
        out["ar_beta_prominence_db"] = model["ar_beta_gain_db"]
        out["ar_alpha_detected"] = bool(out["ar_alpha_center_hz"] is not None
                                        and model["ar_alpha_gain_db"] > 0.0)
        ga, gb = model["ar_alpha_gain_db"], model["ar_beta_gain_db"]
        out["beta_alpha_ratio"] = float(10.0 ** ((gb - ga) / 10.0)) if ga > 0.0 else None
        if stem in mhl["measured_on_aliases"]:
            out["hl_even_odd_ratio"] = float(10.0 ** (-2.0 * model["parity_db"] / 20.0))
        else:
            out["hl_even_odd_ratio"] = None
    else:
        for k in ("formants_hz", "ar_alpha_prominence_db", "ar_beta_center_hz",
                  "ar_beta_prominence_db", "beta_alpha_ratio", "hl_even_odd_ratio"):
            out[k] = None
        out["ar_alpha_center_hz"] = a_center
        out["ar_alpha_detected"] = bool(a_center is not None)

    # --- HL-α の交差確認（局所近傍法。診断値）------------------------------
    out["hl_even_odd_ratio_local"] = _local_parity_ratio(orders, mg, mhl) \
        if (orders.size and stem in mhl["measured_on_aliases"]) else None

    # --- Afterglow（§15.8）-------------------------------------------------
    out.update(_measure_afterglow(x, sr, mag, out.get("ar_alpha_center_hz"),
                                  out["sustain_probe_ms"]))

    # --- 再表現比較用スペクトル同一性ベクトル --------------------------------
    out["spectral_identity"] = _log_band_vector(freqs, mag_db, msi)
    return out


def _local_parity_ratio(orders: np.ndarray, mag_db: np.ndarray,
                        mhl: Mapping[str, Any]) -> Optional[float]:
    lo, hi = (int(v) for v in mhl["harmonic_index_range"])
    offs = [int(o) for o in mhl["neighbour_offsets"]]
    odd: List[float] = []
    even: List[float] = []
    idx = {int(h): i for i, h in enumerate(orders)}
    for h in range(lo, hi + 1):
        if h not in idx:
            continue
        neigh = [mag_db[idx[h + o]] for o in offs if (h + o) in idx]
        if len(neigh) != len(offs):
            continue
        res = float(mag_db[idx[h]] - np.mean(neigh))
        (odd if h % 2 == 1 else even).append(res)
    if not odd or not even:
        return None
    return float(10.0 ** ((np.mean(even) - np.mean(odd)) / 20.0))


def _measure_afterglow(x: np.ndarray, sr: int, mag: Mapping[str, Any],
                       alpha_center: Optional[float],
                       ref_window_ms: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    """§15.8: `AR-α 消失時刻 - main 広帯域消失時刻`。"""
    out: Dict[str, Any] = {"afterglow_extra_ms": None, "main_disappearance_ms": None,
                           "ar_alpha_disappearance_ms": None}
    hop, win = float(mag["envelope_hop_ms"]), float(mag["envelope_window_ms"])
    thr = float(mag["disappearance_threshold_db"])
    t_m, e_m = band_envelope(x, sr, mag["main_band_hz"], hop, win, order=8)
    d_main = _disappearance_ms(t_m, e_m, thr, ref_window_ms)
    out["main_disappearance_ms"] = d_main
    if alpha_center is None or not np.isfinite(alpha_center):
        return out
    half = float(mag["alpha_band_half_width_hz"])
    t_a, e_a = band_envelope(x, sr, (alpha_center - half, alpha_center + half), hop, win, order=6)
    d_alpha = _disappearance_ms(t_a, e_a, thr, ref_window_ms)
    out["ar_alpha_disappearance_ms"] = d_alpha
    if d_main is not None and d_alpha is not None:
        out["afterglow_extra_ms"] = float(d_alpha - d_main)
    return out


def _log_band_vector(freqs: np.ndarray, mag_db: np.ndarray,
                     msi: Mapping[str, Any]) -> Optional[List[float]]:
    """log 帯域包絡（平均除去済み dB）。Body <-> re-expression の cosine 用。"""
    if freqs.size == 0:
        return None
    power = 10.0 ** (mag_db / 10.0)
    lo, hi = (float(v) for v in msi["compare_band_hz"])
    n = int(msi["n_log_bands"])
    edges = np.geomspace(lo, hi, n + 1)
    vals: List[float] = []
    for i in range(n):
        m = (freqs >= edges[i]) & (freqs < edges[i + 1])
        s = float(np.sum(power[m])) if m.any() else 0.0
        vals.append(10.0 * math.log10(max(s, 1e-30)))
    arr = np.asarray(vals)
    if bool(msi.get("mean_removed_cosine", True)):
        arr = arr - float(np.mean(arr))
    return [float(v) for v in arr]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    x, y = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    if x.size == 0 or x.size != y.size:
        return None
    na, nb = float(np.linalg.norm(x)), float(np.linalg.norm(y))
    if na <= 0.0 or nb <= 0.0:
        return None
    return float(np.dot(x, y) / (na * nb))


def read_wav_mono(path: str | Path) -> Tuple[np.ndarray, int]:
    """WAV を float64 モノラルで読む（read tripwire つき）。"""
    p = guard_read_path(path)
    data, sr = sf.read(str(p), dtype="float64", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return np.ascontiguousarray(data, dtype=np.float64), int(sr)


def measure_wav_file(path: str | Path, md: Mapping[str, Any],
                     stem: Optional[str] = None) -> Dict[str, Any]:
    p = guard_read_path(path)
    x, sr = read_wav_mono(p)
    return measure_unit(x, sr, md, stem if stem is not None else p.stem)
