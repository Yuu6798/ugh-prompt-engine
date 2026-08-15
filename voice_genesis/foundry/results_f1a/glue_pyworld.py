"""VG-F1a 経路C: pyworld 直駆動（完全決定論の対照点）。
共通 f0/amp/母音タイムライン（f1a_control.npz）から WORLD の f0/sp/ap を
自前構築し `pyworld.synthesize` で合成する。学習モデル不使用・完全決定論。
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/home/user/ugh-prompt-engine/voice_genesis/singer")

import numpy as np
import pyworld as pw
import soundfile as sf

import formant_tv as ftv

OUT = "/tmp/claude-0/-home-user-ugh-prompt-engine/1c025cfe-cb3e-592b-b577-d0be9640c799/scratchpad/foundry_f1a"
SR = 24000
FRAME_PERIOD_MS = 5.0
FFT_SIZE = pw.get_cheaptrick_fft_size(SR)  # 1024 -> 513 bins
N_BINS = FFT_SIZE // 2 + 1


def build_formant_timeline(vowel_table, T, sr):
    segs = []
    for row in vowel_table:
        s, e, v = int(row["start"]), int(row["end"]), str(row["vowel"])
        if v == "N":
            freqs, bws = ftv.nasal_targets(1.0, (0.0, 0.0, 0.0, 0.0))
        else:
            freqs, bws = ftv.scaled_vowel_targets(v, 1.0, (0.0, 0.0, 0.0, 0.0))
        segs.append(ftv.FormantSegment(s, e, freqs, bws))
    return ftv.interpolate_formant_timeline(segs, T, sr)


def lorentz_gain_grid(freqs, formants, bandwidths):
    total = 0.0
    for i in range(4):
        F = formants[..., i : i + 1]
        B = bandwidths[..., i : i + 1]
        gamma = B / 2.0
        total = total + 1.0 / (1.0 + ((freqs - F) / gamma) ** 2)
    rolloff = 1.0 / (1.0 + (freqs / ftv.GAIN_FLOOR_CUTOFF_HZ) ** 6)
    return total + ftv.GAIN_FLOOR * rolloff


def build_aperiodicity(n_frames: int) -> np.ndarray:
    """0-3kHz=0.05, 3-6kHz で 0.05->0.5 線形, 6kHz以上=0.7（時間不変・周波数プロファイル）。"""
    freq_axis = np.linspace(0.0, SR / 2, N_BINS)
    ap_profile = np.full(N_BINS, 0.05)
    mid = (freq_axis >= 3000.0) & (freq_axis < 6000.0)
    ap_profile[mid] = 0.05 + (freq_axis[mid] - 3000.0) / 3000.0 * (0.5 - 0.05)
    ap_profile[freq_axis >= 6000.0] = 0.7
    return np.tile(ap_profile, (n_frames, 1))


def main() -> None:
    d = np.load(f"{OUT}/f1a_control.npz", allow_pickle=True)
    f0, amp, vowel_table, sr, T = d["f0"], d["amp"], d["vowel_table"], int(d["sr"][0]), int(d["total_samples"][0])
    assert sr == SR

    formants_t, bandwidths_t = build_formant_timeline(vowel_table, T, sr)

    n_frames = int(np.ceil(T / sr / (FRAME_PERIOD_MS / 1000.0))) + 1
    frame_samples = np.minimum(
        (np.arange(n_frames) * FRAME_PERIOD_MS / 1000.0 * sr).astype(np.int64), T - 1
    )
    f0_frames = f0[frame_samples].astype(np.float64)

    freq_axis = np.linspace(0.0, sr / 2, N_BINS)
    gain = lorentz_gain_grid(freq_axis[None, :], formants_t[frame_samples], bandwidths_t[frame_samples])
    sp = gain * (1e-2 / max(gain.max(), 1e-9))  # ピーク ~1e-2、クリップしない範囲

    ap = build_aperiodicity(n_frames)

    y = pw.synthesize(
        np.ascontiguousarray(f0_frames),
        np.ascontiguousarray(sp),
        np.ascontiguousarray(ap),
        sr,
        FRAME_PERIOD_MS,
    )

    # 共通振幅包絡（サンプルレート）を出力長に合わせて乗算
    amp_aligned = np.zeros(len(y), dtype=np.float64)
    n = min(len(y), len(amp))
    amp_aligned[:n] = amp[:n]
    y = y * amp_aligned

    peak = np.abs(y).max()
    if peak > 0.9:
        y = y / peak * 0.9
    sf.write(f"{OUT}/f1a_pyworld_direct.wav", y.astype(np.float32), sr)
    print(f"saved f1a_pyworld_direct.wav len={len(y)/sr:.3f}s peak={np.abs(y).max():.4f} n_frames={n_frames}")


if __name__ == "__main__":
    main()
