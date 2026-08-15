"""VG-F1b Round 3: 明るさフロア付き NN 選択（H1 修正案）。

Round 3 前診断で H1（NN 選択が 325Hz 近傍の暗い区間に集中）が定量的に支持された
（選択フレーム集合の env_ratio 中央値 0.111 は フルクリップ有声 0.236 / 260-400Hz帯
0.188 の約半分。上位濃集ビンの42%が env_ratio 0.037-0.070 の暗い区間）。

対策: 各ドナー有声フレームの env_ratio = Σsp[1k-3k]/Σsp[500Hz-1k] を前計算し、
候補集合を「env_ratio >= フルクリップ有声フレームの p40 値」に制限した上で、
Round 2 と同じ貪欲連続性 NN（i+1 優先・±1半音・外れたら argmin |Δlog2 f0|・
決定論 tie-break）を行う。フロアで候補が空になるターゲットフレームはフロアなし
候補（全有声フレーム）へフォールバックし、回数を記録する。

Round 2 (`glue_template_nn.py`) との差分は選択ロジックのみ。ドナー分析・
score読込・平滑化・Genome変形・synth は同一演算子を再利用（import）。
決定論: 乱数不使用。

R11 で再現可能化のためパスをパラメータ化（元の実行は record 記載の環境で実施）。
"""
from __future__ import annotations

import argparse
import json
import hashlib
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

# R11: `glue_template_nn` は本リポジトリ内の同ディレクトリモジュール（repo import）。
# cwd 依存の暗黙 import に頼らず、__file__ 相対でこのディレクトリを明示的に
# sys.path へ追加してから import する。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from glue_template_nn import (  # noqa: E402
    SR, FRAME_PERIOD_MS, CONTINUITY_SEMITONE_THRESHOLD,
    semitone_dist, load_donor_24k, analyze_donor, smooth_seq, freq_warp, synth_and_shape,
)

FLOOR_PERCENTILE = 40.0


def env_ratio_rows(sp: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    m_1k3k = (freqs >= 1000) & (freqs < 3000)
    m_500_1k = (freqs >= 500) & (freqs < 1000)
    num = sp[:, m_1k3k].sum(axis=1)
    den = sp[:, m_500_1k].sum(axis=1)
    return num / (den + 1e-20)


def select_nn_sequence_v2(donor: dict, ctrl_f0: np.ndarray, ctrl_amp: np.ndarray,
                           total_samples: int, sr: int, floor_percentile: float = FLOOR_PERCENTILE):
    """Round 2 の貪欲連続性 NN に明るさフロア（env_ratio >= p{floor_percentile}）を追加。"""
    f0_d = donor["f0"]
    sp_d = donor["sp"]
    ap_d = donor["ap"]
    voiced_idx = donor["voiced_idx"]
    n_donor_frames = len(f0_d)

    n_bins = sp_d.shape[1]
    freqs = np.linspace(0.0, sr / 2.0, n_bins)
    env_voiced = env_ratio_rows(sp_d[voiced_idx], freqs)  # フルクリップ有声フレームのenv_ratio
    floor_val = float(np.percentile(env_voiced, floor_percentile))

    floor_mask = env_voiced >= floor_val
    floor_idx = voiced_idx[floor_mask]  # 明るさフロア通過候補（グローバル固定集合）
    floor_idx_set = set(int(v) for v in floor_idx)
    f0_floor = f0_d[floor_idx]  # 候補f0（ジャンプ探索用）

    f0_voiced_all = f0_d[voiced_idx]  # フォールバック用（フロアなし全有声候補）

    duration = total_samples / sr
    n_frames = int(np.floor(duration / (FRAME_PERIOD_MS / 1000.0))) + 1
    frame_t = np.arange(n_frames) * (FRAME_PERIOD_MS / 1000.0)

    f0_seq = np.zeros(n_frames, dtype=np.float64)
    sp_seq = np.zeros((n_frames, n_bins), dtype=np.float64)
    ap_seq = np.zeros((n_frames, n_bins), dtype=np.float64)
    sel_idx_seq = np.full(n_frames, -1, dtype=np.int64)

    last_sel: int | None = None
    last_sp = sp_d[voiced_idx[0]]
    last_ap = ap_d[voiced_idx[0]]
    n_unvoiced_carry = 0
    n_continuity = 0
    n_jump = 0
    n_initial = 0
    n_floor_empty_fallback = 0  # フロア候補が空でフォールバックした回数

    for i, tt in enumerate(frame_t):
        idx = min(int(round(tt * sr)), total_samples - 1)
        f0_t = ctrl_f0[idx]
        if f0_t > 0:
            chosen = None
            if last_sel is not None:
                cand = last_sel + 1
                if (cand < n_donor_frames and f0_d[cand] > 0
                        and cand in floor_idx_set):
                    if semitone_dist(f0_d[cand], f0_t) <= CONTINUITY_SEMITONE_THRESHOLD:
                        chosen = cand
                        n_continuity += 1
            if chosen is None:
                if len(floor_idx) > 0:
                    dists = np.abs(12.0 * np.log2(f0_floor / f0_t))
                    j = int(np.argmin(dists))
                    chosen = int(floor_idx[j])
                else:
                    # フロア候補が空 -> フロアなし全有声候補へフォールバック
                    n_floor_empty_fallback += 1
                    dists = np.abs(12.0 * np.log2(f0_voiced_all / f0_t))
                    j = int(np.argmin(dists))
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
            n_unvoiced_carry += 1
            f0_seq[i] = 0.0
            sp_seq[i] = last_sp
            ap_seq[i] = last_ap
            sel_idx_seq[i] = -1

    stats = dict(n_frames=n_frames, n_unvoiced_carry=n_unvoiced_carry,
                 n_initial=n_initial, n_continuity=n_continuity, n_jump=n_jump,
                 n_floor_empty_fallback=n_floor_empty_fallback,
                 floor_percentile=floor_percentile, floor_val=floor_val,
                 n_floor_candidates=int(len(floor_idx)), n_voiced_total=int(len(voiced_idx)))
    return f0_seq, sp_seq, ap_seq, sel_idx_seq, stats


def sha256_of(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="I/O ディレクトリ（f1b_nnv2_*.wav / f1b_nnv2_glue_run_log.json を書く）")
    parser.add_argument("--donor-wav", required=True, help="ドナー WAV パス（例: .../vocadito/Audio/vocadito_2.wav）")
    parser.add_argument("--control-npz", required=True, help="共通 f0/amp npz パス（f1a_control.npz）")
    args = parser.parse_args()
    out_dir = args.out

    log: list[str] = []

    donor_audio, sr = load_donor_24k(args.donor_wav)
    log.append(f"donor loaded: {len(donor_audio)} samples @ {sr}Hz "
               f"({len(donor_audio)/sr:.3f}s), resampled from 44100Hz via resample_poly(80/147)")

    donor = analyze_donor(donor_audio, sr)
    n_voiced = int(donor["voiced_mask"].sum())
    log.append(f"donor analyzed: n_frames={len(donor['f0'])} voiced_frames={n_voiced} "
               f"(no binning/pooling — raw frame sequence retained)")

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

    f0_seq, sp_seq_neutral, ap_seq_neutral, sel_idx_seq, stats = select_nn_sequence_v2(
        donor, ctrl_f0, ctrl_amp, total_samples, ctrl_sr, floor_percentile=FLOOR_PERCENTILE
    )
    log.append(f"floor: percentile={stats['floor_percentile']} val={stats['floor_val']:.4f} "
               f"n_floor_candidates={stats['n_floor_candidates']}/{stats['n_voiced_total']} "
               f"({stats['n_floor_candidates']/stats['n_voiced_total']:.3f})")
    log.append(f"target frames: n_frames={stats['n_frames']} "
               f"n_unvoiced_carry={stats['n_unvoiced_carry']} "
               f"n_initial={stats['n_initial']} n_continuity={stats['n_continuity']} "
               f"n_jump={stats['n_jump']} n_floor_empty_fallback={stats['n_floor_empty_fallback']} "
               f"(continuity_threshold={CONTINUITY_SEMITONE_THRESHOLD} semitone)")
    n_voiced_target = stats['n_initial'] + stats['n_continuity'] + stats['n_jump']
    jump_rate = stats['n_jump'] / n_voiced_target if n_voiced_target else float('nan')
    log.append(f"jump rate among voiced target frames = {jump_rate:.4f} "
               f"({stats['n_jump']}/{n_voiced_target})")

    sp_seq_neutral_sm = smooth_seq(sp_seq_neutral, window=3)
    ap_seq_neutral_sm = smooth_seq(ap_seq_neutral, window=3)

    results = {}

    # (a) neutral
    y_neutral = synth_and_shape(f0_seq, sp_seq_neutral_sm, ap_seq_neutral_sm, ctrl_amp, ctrl_sr)
    sf.write(f"{out_dir}/f1b_nnv2_neutral.wav", y_neutral, ctrl_sr, subtype="PCM_16")
    results["neutral"] = dict(n_samples=len(y_neutral), dur_s=len(y_neutral) / ctrl_sr)
    log.append(f"wrote f1b_nnv2_neutral.wav: {len(y_neutral)} samples "
               f"({len(y_neutral)/ctrl_sr:.3f}s), peak-normalized to 0.6")

    # (b) bright_breathy: formant_scale 1.06 + ap +0.15 (clip)  -- dark は省略
    sp_bright = freq_warp(sp_seq_neutral_sm, scale=1.06, sr=ctrl_sr)
    ap_bright = freq_warp(ap_seq_neutral_sm, scale=1.06, sr=ctrl_sr)
    ap_bright = np.clip(ap_bright + 0.15, 0.0, 1.0)
    y_bright = synth_and_shape(f0_seq, sp_bright, ap_bright, ctrl_amp, ctrl_sr)
    sf.write(f"{out_dir}/f1b_nnv2_bright_breathy.wav", y_bright, ctrl_sr, subtype="PCM_16")
    results["bright_breathy"] = dict(n_samples=len(y_bright), dur_s=len(y_bright) / ctrl_sr)
    log.append(f"wrote f1b_nnv2_bright_breathy.wav: {len(y_bright)} samples "
               f"({len(y_bright)/ctrl_sr:.3f}s), formant_scale=1.06 + ap+0.15(clip), peak-norm 0.6")

    donor_f0_median = float(np.median(donor["f0"][donor["f0"] > 0]))
    dist = 12 * np.log2(donor_f0_median / ctrl_f0_median)
    log.append(f"donor transcription distance: donor f0 median={donor_f0_median:.2f}Hz vs "
               f"control f0 median={ctrl_f0_median:.2f}Hz -> {dist:.3f} semitone")

    for line in log:
        print(line)

    with open(f"{out_dir}/f1b_nnv2_glue_run_log.json", "w") as f:
        json.dump(dict(log=log, results=results, stats=stats,
                        control_f0_median=ctrl_f0_median, donor_f0_median=donor_f0_median,
                        semitone_distance=dist, jump_rate=jump_rate), f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
