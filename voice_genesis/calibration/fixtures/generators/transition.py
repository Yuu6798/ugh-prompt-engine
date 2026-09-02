"""TRANSITION_GT generator（設計正本 §4.2）: 2 つの定常 segment を厳密な
join time で接続する。amplitude step / phase jump / spectral-envelope switch /
crossfade × 3 severities × 2 duration classes。exact join time・投入
discontinuity magnitude を truth として生成時に記録する（`row.join_time_s` /
`row.discontinuity_magnitude`。値そのものは `fixtures/matrix.py` が構築時に
確定済み。本モジュールはそれを波形へ具現化するのみ）。

`duration_class`（join 遷移窓長。short=5ms/long=50ms、`[UNDERSPEC-CAL-B06]`）は
**遷移時間そのものとして物理化する**（`[UNDERSPEC-CAL-B12]`。Codex レビュー
2026-09-01 P1: 従来 `amplitude-step`/`phase-jump`/`spectral-envelope-switch` は
`join_sample` で瞬時に切り替わる実装で `duration_class` を一切参照しておらず、
同一 severity の short/long 行が byte-identical に render されていた）。
4 join type 全てで、severity（discontinuity magnitude、= レベル差そのもの）は
不変のまま、`join_time_s` を中心に `duration_class` 由来の幅を持つ
raised-cosine ramp で 2 つの状態を橋渡しする。`crossfade` も元々
`duration_class` 由来の window で 2 状態を混ぜるパターンだったが、window の
起点が `join_sample`（= 遷移がそこから片側にだけ伸びる）になっており、他
3 join type のように `join_sample` を**中心**に置いていなかった
（`[UNDERSPEC-CAL-B12]` の続き。Codex レビュー 2026-09-01 P1: `crossfade` を
`_blend_envelope` へ揃え、declared `join_time_s` が遷移の中心を指すという
4 join type 共通の規約に統一した）。
recorded truth は `row.join_time_s`（exact join time、不変）+
`row.discontinuity_magnitude`（severity）+ `row.duration_class`（ramp
duration。`[UNDERSPEC-CAL-B06]` の short=5ms/long=50ms 写像により具体秒数が
一意に定まる）の 3 者で ramp duration と magnitude の両方を担保する。
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfiltfilt

from voice_genesis.calibration.fixtures.generators import common

_DURATION_CLASS_S: dict[str, float] = {"short": 0.005, "long": 0.050}


def _steady_tone(f0_hz: float, sr_hz: int, n: int) -> np.ndarray:
    return common.harmonic_pulse_train(f0_hz, sr_hz, n)


def _alt_envelope(x: np.ndarray, sr_hz: int) -> np.ndarray:
    """spectral-envelope switch 用の代替スペクトル包絡（低域強調 lowpass）。"""
    nyquist = sr_hz / 2.0
    cutoff = min(1200.0, nyquist * 0.9) / nyquist
    sos = butter(4, cutoff, btype="lowpass", output="sos")
    return sosfiltfilt(sos, x)


def _ramp_samples_for(row: object, sr_hz: int) -> int:
    """`duration_class`（short=5ms/long=50ms, `[UNDERSPEC-CAL-B06]`）を遷移
    窓のサンプル数へ変換する（`[UNDERSPEC-CAL-B12]`。未指定は long 扱い）。"""
    ramp_s = _DURATION_CLASS_S.get(row.duration_class or "long", 0.050)
    return max(1, int(round(ramp_s * sr_hz)))


def _blend_envelope(n: int, center_sample: int, ramp_samples: int) -> np.ndarray:
    """`center_sample` を中心に幅 `ramp_samples` の raised-cosine で 0→1 へ
    遷移する envelope（ramp 窓外は 0/1 に飽和）。exact join time
    （`row.join_time_s` → `center_sample`）は不変のまま、遷移の物理的な長さ
    だけを `duration_class` に応じて変える（`[UNDERSPEC-CAL-B12]`）。"""
    ramp_samples = max(1, ramp_samples)
    half = ramp_samples // 2
    start = max(0, center_sample - half)
    end = min(n, start + ramp_samples)
    start = max(0, end - ramp_samples)
    env = np.zeros(n, dtype=np.float64)
    env[end:] = 1.0
    span = end - start
    if span > 0:
        env[start:end] = 0.5 * (1.0 - np.cos(np.pi * np.arange(span) / span))
    return env


def _core(row: object) -> np.ndarray:
    sr_hz = row.sr_hz
    f0_hz = row.f0_hz
    n = common.n_samples(row.duration_s, sr_hz)
    join_sample = int(round((row.join_time_s or (row.duration_s / 2.0)) * sr_hz))
    join_sample = max(1, min(join_sample, n - 1))
    mag = row.discontinuity_magnitude if row.discontinuity_magnitude is not None else 0.35

    base = _steady_tone(f0_hz, sr_hz, n)
    join_type = row.join_type

    if join_type == "amplitude-step":
        ramp_samples = _ramp_samples_for(row, sr_hz)
        gain_env = _blend_envelope(n, join_sample, ramp_samples)
        x = base * (1.0 + mag * gain_env)
        return common.peak_normalize(x)

    if join_type == "phase-jump":
        t = np.arange(n, dtype=np.float64) / sr_hz
        phase_shift = mag * np.pi
        second_half = np.zeros(n, dtype=np.float64)
        k = 1
        cutoff = 0.45 * sr_hz
        while k * f0_hz < cutoff:
            second_half += (1.0 / k) * np.sin(2.0 * np.pi * k * f0_hz * t + phase_shift)
            k += 1
        second_half = common.peak_normalize(second_half)
        ramp_samples = _ramp_samples_for(row, sr_hz)
        blend_env = _blend_envelope(n, join_sample, ramp_samples)
        x = base * (1.0 - blend_env) + second_half * blend_env
        return common.peak_normalize(x)

    if join_type == "spectral-envelope-switch":
        alt = common.peak_normalize(_alt_envelope(base, sr_hz)) * (1.0 + mag)
        ramp_samples = _ramp_samples_for(row, sr_hz)
        blend_env = _blend_envelope(n, join_sample, ramp_samples)
        x = base * (1.0 - blend_env) + alt * blend_env
        return common.peak_normalize(x)

    if join_type == "crossfade":
        # `join_sample` を中心に幅 `ramp_samples` の raised-cosine で blend
        # する（他 3 join type と同じ `_blend_envelope` を使用。Codex レビュー
        # 2026-09-01 P1: 従来は `join_sample` から片側にだけ crossfade window
        # が伸びており、declared `join_time_s` が遷移の開始点になっていた —
        # 他 3 type は raised-cosine を join_sample 中心に置くため、declared
        # join_time_s は「遷移の中心」を意味する。crossfade だけこの規約から
        # 外れていた）。
        ramp_samples = _ramp_samples_for(row, sr_hz)
        alt = common.peak_normalize(_steady_tone(f0_hz * (1.0 + mag), sr_hz, n))
        blend_env = _blend_envelope(n, join_sample, ramp_samples)
        x = base * (1.0 - blend_env) + alt * blend_env
        return common.peak_normalize(x)

    raise ValueError(f"unknown TRANSITION_GT join_type: {join_type!r}")


def render(row: object, rng: np.random.Generator) -> np.ndarray:
    n = common.n_samples(row.duration_s, row.sr_hz)
    core = common.negative_control_core(row, rng, n, row.f0_hz)
    if core is None:
        core = _core(row)
    return common.finalize(core, row, rng)
