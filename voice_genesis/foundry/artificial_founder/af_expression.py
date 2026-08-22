"""af_expression.py — Energy / main Release / terminal F0 fall / Afterglow（設計書 §10 / §22）。

**振幅側の形質だけを持つ。** F0 の terminal fall そのものは `af_source.f0_trajectory`
が作る（源の性質のため）が、その「いつ落とすか」= terminal window の位置は本
モジュールが `af_spec.UnitTimeline` から与える。

§10 の並びをそのまま実装する。

```text
main voiced body
      ↓  terminal F0 fall = -100 cent   （articulation の末尾で完了する）
      ↓  main amplitude half-cosine taper = 120 ms
      ↓  AR-α resonance remains +35 ms  （AR 分岐だけ 155 ms の taper を使う）
      ↓  digital zero
```

**全帯域を 35 ms 延長してはいけない**（§10）。そのため main 包絡と AR-α 包絡は
別配列で、taper 長だけが違う。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from af_spec import AliasUnit, FounderGenome, UnitTimeline

#: 10–90% 立ち上がり時間を `attack_ms` と一致させるための線形ランプ長の係数。
#: 線形ランプ長 T に対し 10–90% は 0.8T なので T = attack_ms / 0.8。
ATTACK_RAMP_FACTOR = 1.0 / 0.8

#: 子音 onset から母音本体への境界ランプ長（ms）。50% 通過点が **ちょうど**
#: onset 境界に来るよう中心を合わせる（§15.5 の onset 測定が ±2 ms を要求する）。
ONSET_STEP_MS = 1.0


def half_cosine_taper(n: int) -> np.ndarray:
    """`0.5(1 + cos(π k / n))` を k = 0..n-1 で返す。

    区間の**次の**サンプル（k = n）がちょうど 0 になる位相割り付け。したがって
    配列の最終要素は 0 そのものではなく約 `(π/2n)²/2`（n=5292 で -141 dB）で、
    包絡側は `main[main_end:] = 0` を明示的に置いて digital zero を保証する。
    """
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    t = np.arange(n, dtype=np.float64) / float(n)
    return 0.5 * (1.0 + np.cos(np.pi * t))


def half_cosine_rise(n: int) -> np.ndarray:
    """`0.5(1 - cos(π t / T))`、t=0 で 0.0、t=T で 1.0。"""
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    t = np.arange(n, dtype=np.float64) / float(n)
    return 0.5 * (1.0 - np.cos(np.pi * t))


@dataclass(frozen=True)
class Envelopes:
    """1 unit 分の 2 本の包絡（main / AR-α）と、正規化に使う sustain 区間。"""

    main: np.ndarray
    ar_alpha: np.ndarray
    sustain_start: int
    sustain_end: int


def build_envelopes(genome: FounderGenome, unit: AliasUnit, tl: UnitTimeline) -> Envelopes:
    """main 包絡と AR-α 包絡を作る。

    - 母音 unit: `lead` から `attack_ms/0.8` の線形ランプ（10–90% が `attack_ms`）。
    - 子音 unit: 子音そのものが立ち上がりなので、母音本体は `onset_end` を 50%
      通過点とする 1 ms の半コサイン段差で始まる。onset 区間の振幅は
      `af_synth` 側が子音ごとに与える（main 包絡ではここを 0 にしておく）。
    - 両者とも `art_end` から taper。main は `main_taper_ms`、AR-α は
      `main_taper_ms + ar_alpha_afterglow_extra_ms`。
    """
    sr = tl.sr
    n = tl.total
    main = np.zeros(n, dtype=np.float64)

    body_start = tl.onset_end
    if unit.has_onset:
        step = max(1, int(round(ONSET_STEP_MS * sr / 1000.0)))
        half = step // 2
        rise_lo = max(tl.lead, body_start - half)
        rise_hi = min(tl.art_end, rise_lo + step)
        main[rise_lo:rise_hi] = half_cosine_rise(rise_hi - rise_lo)
        main[rise_hi:tl.art_end] = 1.0
        sustain_start = rise_hi
    else:
        ramp = max(1, int(round(genome.attack_ms * ATTACK_RAMP_FACTOR * sr / 1000.0)))
        ramp = min(ramp, tl.art_end - body_start)
        main[body_start:body_start + ramp] = np.linspace(
            0.0, 1.0, ramp, endpoint=False, dtype=np.float64) + 1.0 / ramp
        main[body_start + ramp:tl.art_end] = 1.0
        sustain_start = body_start + ramp

    taper_n = tl.main_end - tl.art_end
    main[tl.art_end:tl.main_end] = half_cosine_taper(taper_n)

    ar = main.copy()
    ar_taper_n = tl.ag_end - tl.art_end
    ar[tl.art_end:tl.ag_end] = half_cosine_taper(ar_taper_n)
    ar[tl.ag_end:] = 0.0
    main[tl.main_end:] = 0.0

    sustain_end = tl.art_end
    return Envelopes(main=main, ar_alpha=ar,
                     sustain_start=min(sustain_start, sustain_end - 1),
                     sustain_end=sustain_end)


def regularity_gain(signal: np.ndarray, sr: int, start: int, end: int,
                    period_s: float, min_window_ms: float = 15.0) -> np.ndarray:
    """出力側の短時間 RMS を平坦化する時変ゲイン曲線を返す（§2 "Energy: highly regular"）。

    Energy は AF0 の **phenotype** であり、`sustain_ripple_db = 0` が仕様である。
    一方 terminal F0 fall は倍音格子を下へずらすので、母音共鳴（例 /a/ の F1=720 Hz,
    BW=70 Hz）を通した出力振幅は F0 の下降だけで数 dB 動く。源側だけを一定にすると
    仕様の Energy 形質が出力に現れない。そこで **出力の短時間 RMS を設計包絡へ
    合わせるゲイン**をここで作る。

    時変スカラー倍なので、スペクトル比（HL-α の奇偶比・AR の卓立・formant 位置）は
    一切変えない。ゲインは `end` 以降は `end` の値を保持し、残光まで連続に効く。
    """
    n = signal.size
    win = max(int(round(min_window_ms * sr / 1000.0)), int(round(4.0 * period_s * sr)))
    win = max(win, 8)
    half = win // 2
    pad = np.pad(signal, (half, half), mode="edge")
    sq = np.cumsum(np.concatenate(([0.0], pad ** 2)))
    idx = np.arange(n)
    rms = np.sqrt(np.maximum(sq[idx + win] - sq[idx], 0.0) / float(win))
    k = np.hanning(win + 2)[1:-1]
    k = k / k.sum()
    rms = np.convolve(np.pad(rms, (half, half), mode="edge"), k, mode="same")[half:half + n]
    lo, hi = max(0, start), min(n, end)
    if hi - lo < 8:
        return np.ones(n, dtype=np.float64)
    ref = float(np.median(rms[lo:hi]))
    gain = np.ones(n, dtype=np.float64)
    safe = np.maximum(rms[lo:hi], 1e-12)
    gain[lo:hi] = ref / safe
    gain[:lo] = gain[lo]
    gain[hi:] = gain[hi - 1]
    return gain


def normalization_gain(signal: np.ndarray, sustain_start: int, sustain_end: int,
                       sustain_dbfs: float) -> float:
    """sustain 区間の RMS が `sustain_dbfs` になる正規化係数を返す。"""
    seg = signal[sustain_start:sustain_end]
    rms = float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0
    if rms <= 0.0:
        raise ValueError("cannot normalize a silent sustain region")
    return (10.0 ** (float(sustain_dbfs) / 20.0)) / rms
