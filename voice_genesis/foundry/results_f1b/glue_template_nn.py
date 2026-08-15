"""VG-F1b Round 2: ドナー実フレーム最近傍選択（連続性優先）による合成。

Round 1 (`glue_template.py`) の半音ビン median プーリングは、母音ごとに位置が
異なる F2 帯のフォルマントを打ち消し、b1k_3k を実ドナー 0.383 -> 0.012-0.039 に
潰した。median をやめ、ドナーの有声フレーム列そのもの（f0_d[i], sp_d[i], ap_d[i]）
を保持し、ターゲット各フレームに対して以下で選択する:

1. 貪欲連続性選択: 直前に選んだドナーフレーム index が i なら、まず i+1 を候補にし、
   |semitone(f0_d[i+1], f0_t)| <= 1 半音 なら i+1 を採用（自然な母音軌跡・時間構造を保つ）。
   だめなら全有声フレームから距離最小のものへジャンプ（同率なら index が小さい方=決定論）。
2. ジャンプ回数を記録する（生ログへ。ジャンプ過多=ワブル源の正直会計）。
3. sp / ap は選択後に時間方向 3 フレーム移動平均で軽く平滑化する。
4. 無声ターゲットフレームは Round 1 と同じ carry-forward 扱い。
5. Genome 変形（周波数軸伸縮・傾斜・ap 底上げ）は Round 1 と同じ演算子を選択後の
   sp/ap に適用する。

決定論: 乱数不使用。argmin の同率は index 昇順（numpy argmin の既定動作）で確定する。

R11 で再現可能化のためパスをパラメータ化（元の実行は record 記載の環境で実施）。
"""
from __future__ import annotations

import argparse
import json
import hashlib

import numpy as np
import pyworld as pw
import soundfile as sf
from scipy.signal import resample_poly
from scipy.ndimage import uniform_filter1d

SR = 24000
FRAME_PERIOD_MS = 5.0
CONTINUITY_SEMITONE_THRESHOLD = 1.0  # i+1 継続採用の許容半音差


def semitone_dist(f_a: float, f_b: float) -> float:
    return float(abs(12.0 * np.log2(f_a / f_b)))


def load_donor_24k(donor_wav: str) -> tuple[np.ndarray, int]:
    x, sr = sf.read(donor_wav)
    if x.ndim > 1:
        x = x.mean(axis=1)
    # 44100 -> 24000: 有理比 80/147 (gcd(24000,44100)=300 -> 24000/300=80, 44100/300=147)
    assert sr == 44100, f"unexpected donor sr={sr}"
    y = resample_poly(x, up=80, down=147)
    return np.ascontiguousarray(y.astype(np.float64)), SR


def analyze_donor(x: np.ndarray, sr: int):
    """donor 波形を pyworld で分析し、フレーム列（f0/sp/ap）をそのまま返す（プーリングなし）。"""
    f0, t = pw.harvest(x, sr, frame_period=FRAME_PERIOD_MS)
    sp = pw.cheaptrick(x, f0, t, sr)
    ap = pw.d4c(x, f0, t, sr)
    voiced_mask = f0 > 0
    voiced_idx = np.flatnonzero(voiced_mask)
    return dict(f0=f0, t=t, sp=sp, ap=ap, voiced_mask=voiced_mask, voiced_idx=voiced_idx)


def select_nn_sequence(donor: dict, ctrl_f0: np.ndarray, ctrl_amp: np.ndarray,
                        total_samples: int, sr: int):
    """score f0 (per-sample @sr) を 5ms フレームへ落とし、ドナー実フレームを NN 選択で引く。"""
    f0_d = donor["f0"]
    sp_d = donor["sp"]
    ap_d = donor["ap"]
    voiced_idx = donor["voiced_idx"]
    n_donor_frames = len(f0_d)
    f0_voiced = f0_d[voiced_idx]  # 全有声ドナー f0（探索候補集合、ジャンプ時に使用）

    duration = total_samples / sr
    n_frames = int(np.floor(duration / (FRAME_PERIOD_MS / 1000.0))) + 1
    frame_t = np.arange(n_frames) * (FRAME_PERIOD_MS / 1000.0)

    n_bins = sp_d.shape[1]
    f0_seq = np.zeros(n_frames, dtype=np.float64)
    sp_seq = np.zeros((n_frames, n_bins), dtype=np.float64)
    ap_seq = np.zeros((n_frames, n_bins), dtype=np.float64)
    sel_idx_seq = np.full(n_frames, -1, dtype=np.int64)

    last_sel: int | None = None  # 直前に選んだドナーフレーム index（無声中も凍結保持）
    last_sp = sp_d[voiced_idx[0]]
    last_ap = ap_d[voiced_idx[0]]
    n_unvoiced_carry = 0
    n_continuity = 0  # i+1 継続採用（初回選択・ジャンプ以外）
    n_jump = 0
    n_initial = 0

    for i, tt in enumerate(frame_t):
        idx = min(int(round(tt * sr)), total_samples - 1)
        f0_t = ctrl_f0[idx]
        if f0_t > 0:
            chosen = None
            if last_sel is not None:
                cand = last_sel + 1
                if cand < n_donor_frames and f0_d[cand] > 0:
                    if semitone_dist(f0_d[cand], f0_t) <= CONTINUITY_SEMITONE_THRESHOLD:
                        chosen = cand
                        n_continuity += 1
            if chosen is None:
                dists = np.abs(12.0 * np.log2(f0_voiced / f0_t))
                j = int(np.argmin(dists))  # 同率なら最小 index (numpy argmin の既定動作で決定論)
                chosen = int(voiced_idx[j])
                if last_sel is None:
                    n_initial += 1
                else:
                    n_jump += 1
            last_sel = chosen
            last_sp = sp_d[chosen]
            last_ap = ap_d[chosen]
            f0_seq[i] = f0_t
            sp_seq[i] = last_sp
            ap_seq[i] = last_ap
            sel_idx_seq[i] = chosen
        else:
            # 無声/無音フレーム: f0=0 とし、直前の有声選択を carry-forward する
            # (Round 1 と同じ設計判断。last_sel は凍結保持 = 次有声フレームでまず i+1 を試す)
            n_unvoiced_carry += 1
            f0_seq[i] = 0.0
            sp_seq[i] = last_sp
            ap_seq[i] = last_ap
            sel_idx_seq[i] = -1

    stats = dict(n_frames=n_frames, n_unvoiced_carry=n_unvoiced_carry,
                 n_initial=n_initial, n_continuity=n_continuity, n_jump=n_jump)
    return f0_seq, sp_seq, ap_seq, sel_idx_seq, stats


def smooth_seq(seq: np.ndarray, window: int = 3) -> np.ndarray:
    return uniform_filter1d(seq, size=window, axis=0, mode="nearest")


def freq_warp(sp: np.ndarray, scale: float, sr: int) -> np.ndarray:
    """スペクトル包絡の周波数軸を scale 倍に伸縮する（線形補間で再標本化）。

    scale<1 (例 0.94): フォルマントを低域側へ寄せる(dark)。
    scale>1 (例 1.06): フォルマントを高域側へ寄せる(bright)。
    """
    n_frames, n_bins = sp.shape
    freqs = np.linspace(0.0, sr / 2.0, n_bins)
    src_freqs = freqs / scale
    warped = np.empty_like(sp)
    for i in range(n_frames):
        warped[i] = np.interp(src_freqs, freqs, sp[i], left=sp[i, 0], right=sp[i, -1])
    return warped


def spectral_tilt(sp: np.ndarray, sr: int, db_per_octave: float, ref_hz: float = 1000.0) -> np.ndarray:
    n_frames, n_bins = sp.shape
    freqs = np.maximum(np.linspace(0.0, sr / 2.0, n_bins), 20.0)
    tilt_db = db_per_octave * np.log2(freqs / ref_hz)
    gain = 10.0 ** (tilt_db / 10.0)  # sp はパワー領域
    return sp * gain[None, :]


def synth_and_shape(f0_seq: np.ndarray, sp_seq: np.ndarray, ap_seq: np.ndarray,
                     amp_full: np.ndarray, sr: int) -> np.ndarray:
    y = pw.synthesize(np.ascontiguousarray(f0_seq), np.ascontiguousarray(sp_seq),
                       np.ascontiguousarray(ap_seq), sr, FRAME_PERIOD_MS)
    y = np.asarray(y, dtype=np.float64)
    # amp envelope (共通振幅包絡, per-sample @sr) を y の長さへ線形補間で合わせる
    n_amp = len(amp_full)
    n_y = len(y)
    x_src = np.linspace(0.0, 1.0, n_amp)
    x_dst = np.linspace(0.0, 1.0, n_y)
    amp_y = np.interp(x_dst, x_src, amp_full)
    y = y * amp_y
    peak = np.max(np.abs(y))
    if peak > 0:
        y = y / peak * 0.6
    return y


def sha256_of(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="I/O ディレクトリ（f1b_nn_*.wav / f1b_nn_glue_run_log.json を書く）")
    parser.add_argument("--donor-wav", required=True, help="ドナー WAV パス（例: .../vocadito/Audio/vocadito_2.wav）")
    parser.add_argument("--control-npz", required=True, help="共通 f0/amp npz パス（f1a_control.npz）")
    args = parser.parse_args()
    out_dir = args.out

    log: list[str] = []

    # --- ドナー読込 + 24kHz resample ---
    donor_audio, sr = load_donor_24k(args.donor_wav)
    log.append(f"donor loaded: {len(donor_audio)} samples @ {sr}Hz "
               f"({len(donor_audio)/sr:.3f}s), resampled from 44100Hz via resample_poly(80/147)")

    # --- ドナー分析（プーリングなし、フレーム列そのまま保持） ---
    donor = analyze_donor(donor_audio, sr)
    n_voiced = int(donor["voiced_mask"].sum())
    log.append(f"donor analyzed: n_frames={len(donor['f0'])} voiced_frames={n_voiced} "
               f"(no binning/pooling — raw frame sequence retained)")

    # --- score f0/amp 読込 ---
    ctrl = np.load(args.control_npz)
    ctrl_f0 = ctrl["f0"]
    ctrl_amp = ctrl["amp"]
    ctrl_sr = int(ctrl["sr"][0])
    total_samples = int(ctrl["total_samples"][0])
    assert ctrl_sr == SR, f"control sr mismatch: {ctrl_sr} != {SR}"
    log.append(f"control loaded: total_samples={total_samples} ({total_samples/ctrl_sr:.3f}s) "
               f"sr={ctrl_sr}, voiced_frac={(ctrl_f0>0).mean():.4f}")

    ctrl_f0_median = float(np.median(ctrl_f0[ctrl_f0 > 0]))
    log.append(f"control f0 median (voiced) = {ctrl_f0_median:.2f} Hz")

    # --- NN 選択によるターゲットフレーム列構築 ---
    f0_seq, sp_seq_neutral, ap_seq_neutral, sel_idx_seq, stats = select_nn_sequence(
        donor, ctrl_f0, ctrl_amp, total_samples, ctrl_sr
    )
    log.append(f"target frames: n_frames={stats['n_frames']} "
               f"n_unvoiced_carry={stats['n_unvoiced_carry']} "
               f"n_initial={stats['n_initial']} n_continuity={stats['n_continuity']} "
               f"n_jump={stats['n_jump']} "
               f"(continuity_threshold={CONTINUITY_SEMITONE_THRESHOLD} semitone)")
    n_voiced_target = stats['n_initial'] + stats['n_continuity'] + stats['n_jump']
    jump_rate = stats['n_jump'] / n_voiced_target if n_voiced_target else float('nan')
    log.append(f"jump rate among voiced target frames = {jump_rate:.4f} "
               f"({stats['n_jump']}/{n_voiced_target})")

    # --- 時間方向スムージング (window=3) ---
    sp_seq_neutral_sm = smooth_seq(sp_seq_neutral, window=3)
    ap_seq_neutral_sm = smooth_seq(ap_seq_neutral, window=3)

    results = {}

    # (a) neutral
    y_neutral = synth_and_shape(f0_seq, sp_seq_neutral_sm, ap_seq_neutral_sm, ctrl_amp, ctrl_sr)
    sf.write(f"{out_dir}/f1b_nn_neutral.wav", y_neutral, ctrl_sr, subtype="PCM_16")
    results["neutral"] = dict(n_samples=len(y_neutral), dur_s=len(y_neutral) / ctrl_sr)
    log.append(f"wrote f1b_nn_neutral.wav: {len(y_neutral)} samples "
               f"({len(y_neutral)/ctrl_sr:.3f}s), peak-normalized to 0.6")

    # (b) dark: formant_scale 0.94 + tilt -2dB/oct
    sp_dark = freq_warp(sp_seq_neutral_sm, scale=0.94, sr=ctrl_sr)
    sp_dark = spectral_tilt(sp_dark, sr=ctrl_sr, db_per_octave=-2.0)
    ap_dark = freq_warp(ap_seq_neutral_sm, scale=0.94, sr=ctrl_sr)
    ap_dark = np.clip(ap_dark, 0.0, 1.0)
    y_dark = synth_and_shape(f0_seq, sp_dark, ap_dark, ctrl_amp, ctrl_sr)
    sf.write(f"{out_dir}/f1b_nn_dark.wav", y_dark, ctrl_sr, subtype="PCM_16")
    results["dark"] = dict(n_samples=len(y_dark), dur_s=len(y_dark) / ctrl_sr)
    log.append(f"wrote f1b_nn_dark.wav: {len(y_dark)} samples "
               f"({len(y_dark)/ctrl_sr:.3f}s), formant_scale=0.94 + tilt=-2dB/oct, peak-norm 0.6")

    # (c) bright_breathy: formant_scale 1.06 + ap +0.15 (clip)
    sp_bright = freq_warp(sp_seq_neutral_sm, scale=1.06, sr=ctrl_sr)
    ap_bright = freq_warp(ap_seq_neutral_sm, scale=1.06, sr=ctrl_sr)
    ap_bright = np.clip(ap_bright + 0.15, 0.0, 1.0)
    y_bright = synth_and_shape(f0_seq, sp_bright, ap_bright, ctrl_amp, ctrl_sr)
    sf.write(f"{out_dir}/f1b_nn_bright_breathy.wav", y_bright, ctrl_sr, subtype="PCM_16")
    results["bright_breathy"] = dict(n_samples=len(y_bright), dur_s=len(y_bright) / ctrl_sr)
    log.append(f"wrote f1b_nn_bright_breathy.wav: {len(y_bright)} samples "
               f"({len(y_bright)/ctrl_sr:.3f}s), formant_scale=1.06 + ap+0.15(clip), peak-norm 0.6")

    # ドナー転写距離 (score f0 median vs donor 実測 f0 median, semitone) — Round1 と同一定義
    donor_f0_median = float(np.median(donor["f0"][donor["f0"] > 0]))
    dist = 12 * np.log2(donor_f0_median / ctrl_f0_median)
    log.append(f"donor transcription distance: donor f0 median={donor_f0_median:.2f}Hz vs "
               f"control f0 median={ctrl_f0_median:.2f}Hz -> {dist:.3f} semitone")

    for line in log:
        print(line)

    with open(f"{out_dir}/f1b_nn_glue_run_log.json", "w") as f:
        json.dump(dict(log=log, results=results, stats=stats,
                        control_f0_median=ctrl_f0_median, donor_f0_median=donor_f0_median,
                        semitone_distance=dist, jump_rate=jump_rate), f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
