"""VG-F1a 経路A: SawSing DSP コア直駆動（checkpoint 不使用）。
Sins 型（正弦波加算）と SawSinSub 型（のこぎり波減算）を、共通 f0/amp/母音
タイムライン（f1a_control.npz）で明示駆動する。ネットワーク (Mel2Control) は
一切使わない。
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/workspace/yatingmusic/ddsp-singing-vocoders")
sys.path.insert(0, "/home/user/ugh-prompt-engine/voice_genesis/singer")

import numpy as np
import soundfile as sf
import torch

from ddsp.modules import HarmonicOscillator, WaveGeneratorOscillator
from ddsp.core import frequency_filter
import formant_tv as ftv

OUT = "/tmp/claude-0/-home-user-ugh-prompt-engine/1c025cfe-cb3e-592b-b577-d0be9640c799/scratchpad/foundry_f1a"
SR = 24000
N_HARM = 80
NOISE_DB_REL = -20.0  # 倍音ピークに対する白色ノイズ振幅比
NOISE_AMP = 10 ** (NOISE_DB_REL / 20.0)
torch.manual_seed(42)


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
    """freqs, formants[...,i], bandwidths[...,i] は broadcast 可能な形状。戻り値はその broadcast 形状。"""
    total = 0.0
    for i in range(4):
        F = formants[..., i : i + 1]
        B = bandwidths[..., i : i + 1]
        gamma = B / 2.0
        total = total + 1.0 / (1.0 + ((freqs - F) / gamma) ** 2)
    rolloff = 1.0 / (1.0 + (freqs / ftv.GAIN_FLOOR_CUTOFF_HZ) ** 6)
    return total + ftv.GAIN_FLOOR * rolloff


def load_control():
    d = np.load(f"{OUT}/f1a_control.npz", allow_pickle=True)
    return d["f0"], d["amp"], d["vowel_table"], int(d["sr"][0]), int(d["total_samples"][0])


def main() -> None:
    f0, amp, vowel_table, sr, T = load_control()
    assert sr == SR
    formants_t, bandwidths_t = build_formant_timeline(vowel_table, T, sr)  # (T,4)

    # --- Sins（加算合成）: サンプルレート倍音振幅を直接評価 ---
    k = np.arange(1, N_HARM + 1)
    freqs_grid = f0[:, None] * k[None, :]  # (T, N_HARM)
    gain = lorentz_gain_grid(freqs_grid, formants_t, bandwidths_t)
    tilt = 1.0 / k
    harm_amp = gain * tilt[None, :]
    harm_amp *= 0.15 / max(harm_amp.max(), 1e-9)  # クリップ回避のマスターゲイン

    f0_t = torch.from_numpy(f0.astype(np.float32)).view(1, T, 1)
    amp_t = torch.from_numpy(harm_amp.astype(np.float32)).view(1, T, N_HARM)
    sins_synth = HarmonicOscillator(SR)
    sins_sig, _ = sins_synth(f0_t, amp_t)
    noise = NOISE_AMP * (torch.rand(sins_sig.shape) * 2 - 1)
    sins_out = (sins_sig + noise) * torch.from_numpy(amp.astype(np.float32)).view(1, T)
    sins_out = sins_out.squeeze(0).detach().numpy()
    sins_out /= max(np.abs(sins_out).max(), 1e-9) / 0.9
    sf.write(f"{OUT}/f1a_sawsingcore_sins.wav", sins_out.astype(np.float32), SR)
    print(f"sins: peak={np.abs(sins_out).max():.3f} len={len(sins_out)/SR:.3f}s")

    # --- SawSinSub（減算合成）: 固定音色の鋸波励振 + フレームレート減算フィルタ ---
    harm_ratio = torch.tensor([0.4])
    saw_amps = 1.0 / torch.arange(1, N_HARM + 1).float()
    saw_synth = WaveGeneratorOscillator(SR, amplitudes=saw_amps, ratio=harm_ratio)
    saw_sig, _ = saw_synth(f0_t)

    hop = 250  # ~10ms 制御レート。T=590000 を割り切る値を選び fft_convolve の
    # フレーム数整合エラー（audio_frames != ir_frames）を回避（元実装は mel 前処理で
    # 常に整除する長さに揃えているため、直駆動ではこちらで整除値を選ぶ必要がある）
    n_frames = T // hop
    idx = np.minimum(np.arange(n_frames) * hop, T - 1)
    n_freq = 257
    freq_axis = np.linspace(0.0, SR / 2, n_freq)
    mag = lorentz_gain_grid(freq_axis[None, :], formants_t[idx], bandwidths_t[idx])
    mag_t = torch.from_numpy((mag * 0.5).astype(np.float32)).unsqueeze(0)
    saw_filtered = frequency_filter(saw_sig, mag_t)[:, :T]
    noise2 = NOISE_AMP * (torch.rand(saw_filtered.shape) * 2 - 1)
    saw_out = (saw_filtered + noise2) * torch.from_numpy(amp.astype(np.float32)).view(1, T)
    saw_out = saw_out.squeeze(0).detach().numpy()
    saw_out /= max(np.abs(saw_out).max(), 1e-9) / 0.9
    sf.write(f"{OUT}/f1a_sawsingcore_sawsinsub.wav", saw_out.astype(np.float32), SR)
    print(f"sawsinsub: peak={np.abs(saw_out).max():.3f} len={len(saw_out)/SR:.3f}s")


if __name__ == "__main__":
    main()
