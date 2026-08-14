"""singer/formant_tv.py — S2: 日本語 5 母音フォルマント目標表（凍結）+
時変フォルマントフィルタ（コアーティキュレーション補間、クリックなし・決定論）。

母音フォルマント目標表は文献一般に見られる日本語 5 母音の F1-F4 概算値を
参考にした凍結定数であり、実測データではない（underspec_log_s1.md に明記）。
genome.resonance.formant_scale / formant_offsets はこの目標表への
乗算・加算として作用させ、Genome 契約（v0 系 bridge と同じ乗算合成式）を
維持する。

時変フィルタは短時間 STFT ベースの overlap-add で実装する: 各ブロックの
中心時刻でコアーティキュレーション補間済みのフォルマント目標を評価し、
並列ローレンツ型共鳴の振幅ゲイン（harness の formant_filter_gain と
同型の式、本ファイルで独立実装）を励振信号のブロック FFT に掛けて
overlap-add で合成する。ゲインは実数（ゼロ位相）で、ブロック境界は Hann
窓の overlap-add が滑らかに接続するためクリックは生じない。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# 日本語 5 母音フォルマント目標表（凍結定数）[UNDERSPEC-S2-1: 実測値ではなく
# 文献一般の概算値を参考にした assumption。underspec_log_s1.md 参照]
# 値は (F1, F2, F3, F4) Hz。帯域幅は全母音・全フォルマント共通の概算値
# （BANDWIDTHS_HZ）を使う（v0 R0 の帯域幅オーダーを踏襲）。
# ---------------------------------------------------------------------------

VOWEL_FORMANTS_HZ: Dict[str, Tuple[float, float, float, float]] = {
    "a": (800.0, 1200.0, 2600.0, 3400.0),
    "i": (280.0, 2300.0, 3000.0, 3600.0),
    "u": (300.0, 1200.0, 2200.0, 3300.0),
    "e": (500.0, 1900.0, 2700.0, 3400.0),
    "o": (500.0, 900.0, 2600.0, 3400.0),
}

BANDWIDTHS_HZ: Tuple[float, float, float, float] = (80.0, 90.0, 120.0, 130.0)

# 鼻音・撥音「ん」用の疑似フォルマント目標（低域マーマー近似、§S2）。
# F1 を下げ、高次フォルマントを大きく減衰させることで「こもった」鼻音的
# スペクトルを近似する（正式な反共振モデルではない簡易近似。
# underspec_log_s1.md に明記）。
NASAL_FORMANTS_HZ: Tuple[float, float, float, float] = (250.0, 1000.0, 2200.0, 3000.0)
NASAL_BANDWIDTHS_HZ: Tuple[float, float, float, float] = (100.0, 150.0, 200.0, 220.0)


def scaled_vowel_targets(
    vowel: str, formant_scale: float, formant_offsets: Sequence[float], bandwidth_scale: float = 1.0
) -> Tuple[Tuple[float, float, float, float], Tuple[float, float, float, float]]:
    """genome.resonance の乗算合成式を適用した母音フォルマント目標を返す。

    effective_formant_hz[i] = base[i] * formant_scale * (1 + formant_offsets[i])
    （v0 bridge.py と同じ式。Genome 契約を維持する。）
    """
    base = VOWEL_FORMANTS_HZ[vowel]
    freqs = tuple(b * formant_scale * (1.0 + o) for b, o in zip(base, formant_offsets))
    bws = tuple(bw * bandwidth_scale for bw in BANDWIDTHS_HZ)
    return freqs, bws  # type: ignore[return-value]


def nasal_targets(
    formant_scale: float, formant_offsets: Sequence[float], bandwidth_scale: float = 1.0
) -> Tuple[Tuple[float, float, float, float], Tuple[float, float, float, float]]:
    freqs = tuple(b * formant_scale * (1.0 + o) for b, o in zip(NASAL_FORMANTS_HZ, formant_offsets))
    bws = tuple(bw * bandwidth_scale for bw in NASAL_BANDWIDTHS_HZ)
    return freqs, bws  # type: ignore[return-value]


GAIN_FLOOR = 0.10
GAIN_FLOOR_CUTOFF_HZ = 3500.0
# [UNDERSPEC-S1-2] フォルマントゲインの下駄（floor）。ローレンツ型共鳴の
# ゲインは基音がどのフォルマントからも遠い場合（低い f0 に対し F1 が
# 相対的に高い母音、例えば /a/ の F1=800Hz vs f0=330Hz）に基音を
# 2 次以上の倍音に対し 1 桁以上弱く抑圧し、measure_v3（harness、無改変
# 前提）の F0 推定器が「基音喪失」型のオクターブ誤り（2 倍音を基音と誤認）
# を起こす実害を machine gate 実測で確認した（詳細: underspec_log_s1.md）。
# 声道フィルタは物理的にも周波数を完全にゼロへ減衰させることはない
# （常に何らかの透過が残る）ことを踏まえ、ゲインに小さな下駄
# `GAIN_FLOOR` を加算し、基音が倍音に対して極端に弱くなる縮退を防ぐ。
# 値は「gate 1 が通る最小限」として実測で調整した。
#
# 当初は全帯域一律の加算だったが、それはナイキスト近傍の高域まで底上げ
# してしまい、S1 が要求する aliasing 対策（>0.45*sr で -40dB 未満）を
# 悪化させる実害を machine gate 実測で確認した。基音喪失問題は基音・低次
# 倍音（数百 Hz 帯）の相対バランスの問題であり高域には無関係なため、
# 下駄を `GAIN_FLOOR_CUTOFF_HZ` 以下に限定し、それ以上はなだらかに
# ゼロへロールオフさせる（高域の底上げを避け aliasing 悪化を防ぐ）。


def lorentz_gain(freqs: np.ndarray, formants: Sequence[float], bandwidths: Sequence[float]) -> np.ndarray:
    """並列ローレンツ型共鳴の振幅ゲイン（harness measure_v4 と同型の式）+ 低域限定の下駄。"""
    total = np.zeros_like(freqs, dtype=np.float64)
    for F, B in zip(formants, bandwidths):
        gamma = B / 2.0
        total += 1.0 / (1.0 + ((freqs - F) / gamma) ** 2)

    # 下駄を低域限定にするロールオフ（cutoff 以下は 1、以上はなだらかに 0 へ）
    rolloff = 1.0 / (1.0 + (freqs / GAIN_FLOOR_CUTOFF_HZ) ** 6)
    return total + GAIN_FLOOR * rolloff


# ---------------------------------------------------------------------------
# コアーティキュレーション付き時変フォルマント目標のタイムライン
# ---------------------------------------------------------------------------


@dataclass
class FormantSegment:
    start_sample: int
    end_sample: int
    formants: Tuple[float, float, float, float]
    bandwidths: Tuple[float, float, float, float]


def interpolate_formant_timeline(
    segments: List[FormantSegment], n_samples: int, sr: int, transition_ms: float = 60.0
) -> Tuple[np.ndarray, np.ndarray]:
    """区分定数のフォルマント目標列を、境界で `transition_ms` の線形補間クロス
    フェードを持つ連続タイムライン（サンプル毎の F1..F4 / B1..B4）に変換する。

    戻り値: (formants_over_time[n_samples,4], bandwidths_over_time[n_samples,4])
    """
    formants_t = np.zeros((n_samples, 4), dtype=np.float64)
    bandwidths_t = np.zeros((n_samples, 4), dtype=np.float64)
    half_trans = int(sr * transition_ms / 1000.0 / 2.0)

    # デフォルト値でまず全体を埋める（ブレス区間等、どのセグメントにも
    # 覆われないサンプルが残っても帯域幅 0 による 0 除算(NaN)を起こさない
    # ようにするための安全な既定値。無音区間なので値そのものは聴感に影響
    # しない）。
    default_formants = np.array(VOWEL_FORMANTS_HZ["a"])
    default_bandwidths = np.array(BANDWIDTHS_HZ)
    formants_t[:, :] = default_formants
    bandwidths_t[:, :] = default_bandwidths

    # 区分定数で埋める
    for seg in segments:
        s, e = max(0, seg.start_sample), min(n_samples, seg.end_sample)
        if e <= s:
            continue
        formants_t[s:e, :] = np.array(seg.formants)
        bandwidths_t[s:e, :] = np.array(seg.bandwidths)

    # 境界ごとにクロスフェード（前セグメント終端 - half_trans 〜 次セグメント
    # 開始 + half_trans の範囲を線形補間）
    for i in range(len(segments) - 1):
        cur, nxt = segments[i], segments[i + 1]
        boundary = cur.end_sample
        lo = max(0, boundary - half_trans)
        hi = min(n_samples, boundary + half_trans)
        if hi <= lo:
            continue
        span = hi - lo
        w = np.linspace(0.0, 1.0, span)
        f_from = np.array(cur.formants)
        f_to = np.array(nxt.formants)
        b_from = np.array(cur.bandwidths)
        b_to = np.array(nxt.bandwidths)
        for k in range(4):
            formants_t[lo:hi, k] = f_from[k] * (1 - w) + f_to[k] * w
            bandwidths_t[lo:hi, k] = b_from[k] * (1 - w) + b_to[k] * w

    return formants_t, bandwidths_t


# ---------------------------------------------------------------------------
# STFT ベースの時変フィルタ（overlap-add、クリックなし・決定論）
# ---------------------------------------------------------------------------


def apply_time_varying_formant_filter(
    sig: np.ndarray,
    formants_over_time: np.ndarray,
    bandwidths_over_time: np.ndarray,
    sr: int,
    block_ms: float = 20.0,
    hop_ms: float = 10.0,
) -> np.ndarray:
    """短時間 FFT overlap-add により、サンプル毎に変化するフォルマント目標を
    ブロック単位で近似しつつゲインを適用する（ブロック中心の目標値を使用）。
    """
    n = len(sig)
    block_len = max(int(sr * block_ms / 1000.0), 8)
    hop = max(int(sr * hop_ms / 1000.0), 4)
    window = np.hanning(block_len)
    win_sum = np.zeros(n + block_len, dtype=np.float64)
    out = np.zeros(n + block_len, dtype=np.float64)

    freqs = np.fft.rfftfreq(block_len, d=1.0 / sr)

    padded = np.concatenate([sig, np.zeros(block_len)])

    for start in range(0, n, hop):
        end = start + block_len
        block = padded[start:end] * window
        center = min(start + block_len // 2, n - 1)
        formants = formants_over_time[center]
        bandwidths = bandwidths_over_time[center]
        gain = lorentz_gain(freqs, formants, bandwidths)
        spec = np.fft.rfft(block)
        filtered = np.fft.irfft(spec * gain, n=block_len)
        out[start:end] += filtered * window
        win_sum[start:end] += window**2

    win_sum[win_sum < 1e-8] = 1.0
    result = out / win_sum
    return result[:n]
