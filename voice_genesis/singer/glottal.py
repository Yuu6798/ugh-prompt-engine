"""singer/glottal.py — S1: 声帯パルス励振（Rosenberg 系）+ jitter/shimmer +
声門周期同期の呼気ノイズ。

設計方針（r09_design_memo.md §S1）:
  - パルス形状: Rosenberg 型グロータルフロー波形（開区間: 半コサイン立ち上がり、
    閉区間: コサイン減衰、閉鎖区間: 0）。励振信号はこのフロー波形の時間微分
    （声門閉鎖時の急峻な負パルスが声道を励振する、という音源フィルタ理論の
    標準的な取り扱い）。
  - open quotient (Oq) は genome.source.tilt から可読な線形写像で決定する
    （`open_quotient_from_tilt`）。Oq が小さいほど閉鎖が急峻でスペクトルが
    明るくなる（tilt が -3 側=明るい に近いほど Oq を小さくする）。
  - jitter: F0 自体に小さいランダムウォークを重畳（周期ごとの微小揺らぎ）。
  - shimmer（新設）: 周期（サイクル）ごとの振幅揺らぎ。genome 側は
    `microprosody.shimmer_amount`（事前分布 [0,0.1]）としてこのファイルの
    外（bridge 相当）で定義し、ここでは量として受け取るのみ。
  - 呼気ノイズは同じフロー波形を変調エンベロープとして使うことで、開区間で
    強く・閉区間でほぼ 0 になるよう声門周期に同期させる。
  - aliasing 対策: 本モジュールはオーバーサンプリングされた sr（呼び出し側が
    決定、通常 SR_OUT の整数倍）で動作する前提。デシメーションは
    `render_song.py` 側で行う（責務分離）。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EPS = 1e-12


def open_quotient_from_tilt(
    tilt_db_per_oct: float,
    tilt_min: float = -18.0,
    tilt_max: float = -3.0,
    oq_at_tilt_min: float = 0.75,
    oq_at_tilt_max: float = 0.35,
) -> float:
    """genome.source.tilt (dB/oct) -> open quotient Oq の線形写像。

    [可読な式]: tilt が tilt_min（暗い・息っぽい方向）のとき Oq=oq_at_tilt_min
    （開区間が長い=緩やかな閉鎖=スペクトルが暗い）、tilt が tilt_max（明るい・
    張った方向）のとき Oq=oq_at_tilt_max（開区間が短い=急峻な閉鎖=スペクトル
    が明るい）となるよう線形補間する。範囲外は端値にクランプする。
    """
    t = (tilt_db_per_oct - tilt_min) / (tilt_max - tilt_min)
    t = max(0.0, min(1.0, t))
    return oq_at_tilt_min + t * (oq_at_tilt_max - oq_at_tilt_min)


def rosenberg_flow(phase: np.ndarray, oq: float, alpha: float = 0.6) -> np.ndarray:
    """Rosenberg 型グロータルフロー波形（0..1 正規化位相 -> 0..1 flow）。

    phase in [0,1): 0 = 周期開始（声門が開き始める瞬間）。
    Tp = oq*alpha: 開放（半コサイン立ち上がり）区間の長さ（位相比）。
    Tn = oq*(1-alpha): 閉鎖（コサイン減衰）区間の長さ（位相比）。
    phase >= oq: 完全閉鎖区間（flow=0）。
    """
    oq = max(0.05, min(0.95, oq))
    Tp = oq * alpha
    Tn = oq * (1.0 - alpha)
    flow = np.zeros_like(phase)

    open_rise = phase < Tp
    flow[open_rise] = 0.5 * (1.0 - np.cos(np.pi * phase[open_rise] / max(Tp, EPS)))

    open_fall = (phase >= Tp) & (phase < oq)
    flow[open_fall] = np.cos(np.pi * (phase[open_fall] - Tp) / (2.0 * max(Tn, EPS)))

    return flow


@dataclass
class GlottalOutput:
    excitation: np.ndarray  # dg/dt（声道励振信号）
    noise_envelope: np.ndarray  # 0..1、開区間で強い（呼気ノイズ変調用）
    phase_within_cycle: np.ndarray  # 診断用


def _per_cycle_random(cycle_index: np.ndarray, seed: int, amount: float) -> np.ndarray:
    """cycle_index（各サンプルの整数サイクル番号）に対し、サイクルごとに
    決定論的な擬似乱数（seed 固定）を割り当てて振幅化する（shimmer 用）。

    [UNDERSPEC-S1-3] 完全に独立な（i.i.d.）サイクル毎乱数は、machine gate
    実測で低音長音（例: 220Hz・2秒保持）のごく一部の seed で偶然
    「奇数サイクルと偶数サイクルの振幅パターン」に近い構造を生み、
    measure_v3（harness、無改変前提）の自己相関ベース F0 推定器が
    周期を 2 倍に誤認するオクターブ誤りを誘発することを発見した。
    3 点移動平均で軽く平滑化することで隣接サイクル間の急激な振幅相関を
    抑え、この縮退が起きにくくなることを実測で確認した（完全に排除する
    保証はないが、gate 1 の実測範囲では解消した）。
    """
    if amount <= 0.0:
        return np.ones_like(cycle_index, dtype=np.float64)
    n_cycles = int(cycle_index.max()) + 2 if cycle_index.size else 1
    rng = np.random.default_rng(seed)
    raw = rng.normal(0.0, 1.0, size=n_cycles)
    if n_cycles >= 3:
        kernel = np.array([1.0, 1.0, 1.0]) / 3.0
        padded = np.concatenate([[raw[0]], raw, [raw[-1]]])
        smoothed = np.convolve(padded, kernel, mode="valid")[: n_cycles]
    else:
        smoothed = raw
    per_cycle = 1.0 + amount * smoothed
    per_cycle = np.clip(per_cycle, 1.0 - 3 * amount, 1.0 + 3 * amount)
    return per_cycle[cycle_index]


def generate_glottal_excitation(
    f0_contour_hz: np.ndarray,
    sr: int,
    oq: float,
    jitter_amount: float = 0.006,
    shimmer_amount: float = 0.0,
    seed: int = 0,
    alpha: float = 0.6,
) -> GlottalOutput:
    """ピッチ同期の声帯パルス列励振を生成する（jitter/shimmer込み、決定論）。

    f0_contour_hz: サンプル毎の目標 F0（既に performance 層のポルタメント/
    ビブラートを織り込んだ、無声区間は 0 でよい配列）。
    """
    n = len(f0_contour_hz)
    f0_safe = np.maximum(f0_contour_hz, 1e-6)

    # jitter: F0 自体への小さい揺らぎ（決定論・seed 固定）。
    # [UNDERSPEC-S1-4] 当初「10ms 粒度の乱数を累積したランダムウォーク
    # （全体デトレンド）」で実装したが、machine gate 実測で「低音・長い
    # 保持ノート（例: 220Hz・3.3秒）」において、累積ウォークが（バッファ
    # 長に依存して統計的に変化する）局所的に急峻な区間を作り、
    # measure_v3（harness、無改変前提）の自己相関ベース F0 推定器が
    # 周期を 2 倍に誤認するオクターブ誤りを誘発することを発見した
    # （ランダムウォークの最大偏位は sqrt(N) で成長するため、単純な
    # ピーク正規化だけでは局所変化率がバッファ長非依存にならない）。
    # 累積（cumsum）をやめ、各 10ms ブロックに独立な（i.i.d.）値を割り当てて
    # 補間する方式に変更した。i.i.d. 方式は局所変化率の統計的性質が
    # バッファ長に依存しないため、この縮退を解消する。
    if jitter_amount > 0:
        rng = np.random.default_rng(seed + 1)
        block = max(int(sr * 0.01), 1)  # 10ms 粒度で乱数を打ってから補間
        n_blocks = n // block + 2
        coarse = rng.normal(0.0, 1.0, size=n_blocks)
        coarse -= coarse.mean()
        coarse_t = np.arange(n_blocks) * block
        sample_t = np.arange(n)
        jitter_walk = np.interp(sample_t, coarse_t, coarse)
        # 正規化してから jitter_amount（相対比率）でスケール
        denom = np.max(np.abs(jitter_walk)) + EPS
        jitter_rel = (jitter_walk / denom) * jitter_amount
    else:
        jitter_rel = np.zeros(n)

    f0_jittered = f0_safe * (1.0 + jitter_rel)
    f0_jittered = np.maximum(f0_jittered, 1e-6)

    phase_cycles = np.cumsum(f0_jittered) / sr  # 単調非減少、単位: サイクル数
    cycle_index = np.floor(phase_cycles).astype(np.int64)
    phase_within = phase_cycles - cycle_index  # [0,1)

    flow = rosenberg_flow(phase_within, oq, alpha=alpha)

    shimmer_gain = _per_cycle_random(cycle_index, seed=seed + 2, amount=shimmer_amount)
    flow_shimmered = flow * shimmer_gain

    excitation = np.empty(n, dtype=np.float64)
    excitation[0] = 0.0
    excitation[1:] = (flow_shimmered[1:] - flow_shimmered[:-1]) * sr
    # 微分のスケールを周期に依存しない程度に正規化（後段の RMS 正規化に委ねるため
    # ここでは発散だけ防ぐ穏当なクリップに留める）
    peak = np.max(np.abs(excitation)) + EPS
    excitation = excitation / peak

    return GlottalOutput(excitation=excitation, noise_envelope=flow, phase_within_cycle=phase_within)
