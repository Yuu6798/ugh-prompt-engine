"""adapter/perf_genes.py — VG-F1: Performance 遺伝子 v0（f0 微細構造の決定論生成）。

設計書 §2 perf_genes.py に対応する。singer/performance.py の note track
（ビブラート無し・ポルタメント込み、read-only import）をベースに、
onset_glide / vibrato（ノート毎位相リセット）/ drift / jitter を乗算合成する。

決定論: 乱数は全て `np.random.default_rng(seed)`。drift/jitter は
singer/glottal.py の [UNDERSPEC-S1-4] 教訓を踏襲し、cumsum 累積ではなく
ブロック単位の i.i.d. 乱数を線形補間する方式（局所変化率がバッファ長に
依存しない）を使う。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Sequence

import numpy as np

_HERE = Path(__file__).resolve().parent
_SINGER_DIR = _HERE.parent.parent / "singer"
if str(_SINGER_DIR) not in sys.path:
    sys.path.insert(0, str(_SINGER_DIR))

import performance as perf  # noqa: E402  (singer、read-only import)

EPS = 1e-9


def _iid_block_walk_cents(n: int, sr: int, block_sec: float, depth_cents: float, seed: int) -> np.ndarray:
    """帯域制限された i.i.d. ブロック乱数 -> 線形補間ウォーク（cents 単位）。

    glottal.py [UNDERSPEC-S1-4]: cumsum 累積ウォークは局所変化率がバッファ長に
    統計的に依存し、自己相関ベース F0 推定でオクターブ誤りを誘発することが
    実測されている。各ブロックへ独立な（i.i.d.）値を割り当ててから補間する
    方式はこの縮退を避ける。
    """
    if depth_cents <= 0.0 or n <= 0:
        return np.zeros(max(n, 0), dtype=np.float64)
    rng = np.random.default_rng(seed)
    block = max(int(sr * block_sec), 1)
    n_blocks = n // block + 2
    coarse = rng.normal(0.0, 1.0, size=n_blocks)
    coarse -= coarse.mean()
    coarse_t = np.arange(n_blocks) * block
    sample_t = np.arange(n)
    walk = np.interp(sample_t, coarse_t, coarse)
    denom = np.max(np.abs(walk)) + EPS
    return (walk / denom) * depth_cents


def _onset_glide_cents(
    segments: Sequence["perf.TimelineSegment"], total_samples: int, sr: int, depth_cents: float, time_ms: float
) -> np.ndarray:
    """各ノート先頭で depth_cents から 0 へ time_ms かけて線形に戻る「立ち上がりの
    音程の垂れ（scoop）」を加算する（onset_glide.depth_cents は負値が既定 = 下から
    入る）。"""
    out = np.zeros(total_samples, dtype=np.float64)
    ramp_len_full = max(int(round(sr * time_ms / 1000.0)), 1)
    for seg in segments:
        note_len = seg.end_sample - seg.start_sample
        ramp_len = min(ramp_len_full, note_len)
        if ramp_len <= 0:
            continue
        w = np.linspace(0.0, 1.0, ramp_len)  # 0 -> 1 : depth_cents -> 0
        out[seg.start_sample:seg.start_sample + ramp_len] += depth_cents * (1.0 - w)
    return out


def _vibrato_cents_phase_reset(
    segments: Sequence["perf.TimelineSegment"], total_samples: int, sr: int,
    rate_hz: float, depth_cents: float, onset_ms: float,
) -> np.ndarray:
    """ノート毎に位相をリセットするビブラート（singer 本体の連続位相ビブラートとは
    別レイヤー。設計書 §0-3/§2 の phase_reset_per_note:true 仕様）。"""
    out = np.zeros(total_samples, dtype=np.float64)
    onset_len_full = max(int(round(sr * onset_ms / 1000.0)), 1)
    for seg in segments:
        note_len = seg.end_sample - seg.start_sample
        if note_len <= 0:
            continue
        t_rel = np.arange(note_len) / sr  # ノート先頭 (=0) でリセットされる位相
        phase = 2.0 * np.pi * rate_hz * t_rel
        sinus = depth_cents * np.sin(phase)
        onset_len = min(onset_len_full, note_len)
        env = np.ones(note_len, dtype=np.float64)
        if onset_len > 0:
            env[:onset_len] = np.linspace(0.0, 1.0, onset_len)
        out[seg.start_sample:seg.end_sample] = sinus * env
    return out


def _vibrato_cents_continuous_phase(
    segments: Sequence["perf.TimelineSegment"], total_samples: int, sr: int,
    rate_hz: float, depth_cents: float, onset_ms: float,
) -> np.ndarray:
    """曲頭 (t=0) からの絶対時間で位相を連続させるビブラート
    （`phase_reset_per_note: false`。F1a 以前の旧挙動相当・singer/performance.py
    `build_f0_contour` のビブラート位相計算（`t_full = np.arange(total_samples)/sr`）
    と同じ絶対時間基準。review #262 R9・`r3789495254`）。

    振幅エンベロープ（ノート先頭から onset_ms かけて 0→1 に立ち上がる線形ランプ）は
    `phase_reset_per_note` の値と独立な仕様（vib.onset_ms）のため、ここでも
    `_vibrato_cents_phase_reset` と同じ per-note ランプを維持する。異なるのは
    「位相をどの原点から測るか」のみ（ノート先頭で毎回リセットする vs 曲頭一回のみ）。
    """
    out = np.zeros(total_samples, dtype=np.float64)
    onset_len_full = max(int(round(sr * onset_ms / 1000.0)), 1)
    t_full = np.arange(total_samples) / sr  # 曲頭からの絶対時間（ノート境界でリセットしない）
    phase_full = 2.0 * np.pi * rate_hz * t_full
    sinus_full = depth_cents * np.sin(phase_full)
    for seg in segments:
        note_len = seg.end_sample - seg.start_sample
        if note_len <= 0:
            continue
        onset_len = min(onset_len_full, note_len)
        env = np.ones(note_len, dtype=np.float64)
        if onset_len > 0:
            env[:onset_len] = np.linspace(0.0, 1.0, onset_len)
        out[seg.start_sample:seg.end_sample] = sinus_full[seg.start_sample:seg.end_sample] * env
    return out


def build_perf_f0(
    segments: Sequence["perf.TimelineSegment"],
    total_samples: int,
    sr: int,
    perf_spec: Dict[str, Any],
    seed: int,
    portamento_ms: float = 55.0,
) -> np.ndarray:
    """singer/performance.py のベース note track（ビブラート無し・ポルタメント込み）に
    Performance 遺伝子 v0（onset_glide / vibrato位相リセット / drift / jitter）を
    乗算合成した per-sample f0 (Hz) 配列を返す。無声区間（ブレス）は 0 のまま。
    """
    segments = list(segments)
    base_f0 = perf.build_f0_contour(
        segments, total_samples, sr,
        vibrato_rate_hz=5.0, vibrato_depth_cents=0.0,
        portamento_ms=portamento_ms,
    )

    onset = perf_spec.get("onset_glide", {})
    cents = _onset_glide_cents(
        segments, total_samples, sr,
        depth_cents=float(onset.get("depth_cents", -80.0)),
        time_ms=float(onset.get("time_ms", 60.0)),
    )

    vib = perf_spec.get("vibrato", {})
    # review #262 R9 (`r3789495254`): phase_reset_per_note を honor する。
    # true（既定・presets 全件）= 従来どおり `_vibrato_cents_phase_reset`
    # （ノート毎位相リセット・bit 不変）。false = `_vibrato_cents_continuous_phase`
    # （曲頭からの絶対時間位相・F1a 以前の旧挙動相当・新規実装）。
    vibrato_fn = (
        _vibrato_cents_phase_reset
        if bool(vib.get("phase_reset_per_note", True))
        else _vibrato_cents_continuous_phase
    )
    cents = cents + vibrato_fn(
        segments, total_samples, sr,
        rate_hz=float(vib.get("rate_hz", 5.2)),
        depth_cents=float(vib.get("depth_cents", 25.0)),
        onset_ms=float(vib.get("onset_ms", 150.0)),
    )

    drift = perf_spec.get("drift", {})
    drift_rate_hz = max(float(drift.get("rate_hz", 0.4)), EPS)
    cents = cents + _iid_block_walk_cents(
        total_samples, sr, block_sec=1.0 / (2.0 * drift_rate_hz),
        depth_cents=float(drift.get("depth_cents", 10.0)), seed=seed,
    )

    jitter_cents = float(perf_spec.get("jitter_cents", 3.0))
    cents = cents + _iid_block_walk_cents(
        total_samples, sr, block_sec=0.010, depth_cents=jitter_cents, seed=seed + 1,
    )

    f0 = base_f0 * (2.0 ** (cents / 1200.0))
    f0[base_f0 <= 0.0] = 0.0
    return f0
