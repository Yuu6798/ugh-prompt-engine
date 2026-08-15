"""VG-F1b Round 3 手順1: H1（NN選択の暗い区間集中）の定量確認。

- ドナー全有声フレームの env_ratio 分布 (median/p10/p90)
- 260-400Hz帯（有声）全フレームの env_ratio 分布
- Round 2 の NN 選択で実際に選ばれたフレーム(sel_idx_seq, 出力フレーム単位=多重度あり)の
  env_ratio 分布
- 選択がドナークリップ内のどの秒レンジに集中したか（1秒ビン、多重度で重み付け、上位5)
"""
from __future__ import annotations

import json
import sys

import numpy as np

sys.path.insert(0, "/tmp/claude-0/-home-user-ugh-prompt-engine/1c025cfe-cb3e-592b-b577-d0be9640c799/scratchpad/foundry_f1b")
from glue_template_nn import load_donor_24k, analyze_donor, select_nn_sequence, FRAME_PERIOD_MS

OUT = "/tmp/claude-0/-home-user-ugh-prompt-engine/1c025cfe-cb3e-592b-b577-d0be9640c799/scratchpad/foundry_f1b"
CONTROL_NPZ = "/tmp/claude-0/-home-user-ugh-prompt-engine/1c025cfe-cb3e-592b-b577-d0be9640c799/scratchpad/foundry_f1a/f1a_control.npz"


def env_ratio_rows(sp: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    m_1k3k = (freqs >= 1000) & (freqs < 3000)
    m_500_1k = (freqs >= 500) & (freqs < 1000)
    num = sp[:, m_1k3k].sum(axis=1)
    den = sp[:, m_500_1k].sum(axis=1)
    return num / (den + 1e-20)


def dist_stats(arr: np.ndarray) -> dict:
    return dict(n=int(len(arr)), median=float(np.median(arr)),
                p10=float(np.percentile(arr, 10)), p90=float(np.percentile(arr, 90)))


def main() -> None:
    log = []
    donor_wav, sr = load_donor_24k()
    donor = analyze_donor(donor_wav, sr)
    f0_d = donor["f0"]
    sp_d = donor["sp"]
    voiced_idx = donor["voiced_idx"]
    n_bins = sp_d.shape[1]
    freqs = np.linspace(0.0, sr / 2.0, n_bins)

    env_all = env_ratio_rows(sp_d[voiced_idx], freqs)
    log.append(f"n_bins={n_bins} sr={sr}")

    # 260-400Hz band voiced subset
    f0_voiced = f0_d[voiced_idx]
    band_mask = (f0_voiced >= 260) & (f0_voiced < 400)
    env_band = env_all[band_mask]

    stats_all = dist_stats(env_all)
    stats_band = dist_stats(env_band)
    log.append(f"[full-clip voiced] {stats_all}")
    log.append(f"[260-400Hz voiced] {stats_band}")

    # --- NN選択再実行 (Round2と同一ロジック) ---
    ctrl = np.load(CONTROL_NPZ)
    ctrl_f0 = ctrl["f0"]
    ctrl_amp = ctrl["amp"]
    ctrl_sr = int(ctrl["sr"][0])
    total_samples = int(ctrl["total_samples"][0])
    f0_seq, sp_seq, ap_seq, sel_idx_seq, stats = select_nn_sequence(
        donor, ctrl_f0, ctrl_amp, total_samples, ctrl_sr
    )
    log.append(f"select stats: {stats}")

    # 選ばれたフレーム集合 = sel_idx_seq の有効値(!=-1)、出力フレーム単位=多重度あり
    sel_valid = sel_idx_seq[sel_idx_seq >= 0]
    log.append(f"n_selected_instances(with multiplicity)={len(sel_valid)} "
               f"n_unique_donor_frames_used={len(np.unique(sel_valid))}")

    env_sel = env_ratio_rows(sp_d[sel_valid], freqs)
    stats_sel = dist_stats(env_sel)
    log.append(f"[NN-selected, with multiplicity] {stats_sel}")

    # unique版も参考記録
    sel_unique = np.unique(sel_valid)
    env_sel_unique = env_ratio_rows(sp_d[sel_unique], freqs)
    stats_sel_unique = dist_stats(env_sel_unique)
    log.append(f"[NN-selected, unique donor frames] {stats_sel_unique}")

    # --- 時間帯集中: donor時間を1秒ビンに分け、多重度で重み付けカウント ---
    t_d = donor["t"]  # フレームごとのドナー内時刻(秒)
    sel_times = t_d[sel_valid]
    duration = float(t_d[-1]) + FRAME_PERIOD_MS / 1000.0
    n_bins_time = int(np.ceil(duration)) + 1
    bin_idx = np.floor(sel_times).astype(int)
    bin_idx = np.clip(bin_idx, 0, n_bins_time - 1)
    counts = np.bincount(bin_idx, minlength=n_bins_time)
    top5 = np.argsort(counts)[::-1][:5]
    top5_ranges = []
    for b in top5:
        lo, hi = float(b), float(b + 1)
        cnt = int(counts[b])
        frac = cnt / len(sel_valid)
        # そのビン内のf0中央値・env_ratio中央値も併記
        mask_bin = (bin_idx == b)
        idx_in_bin = sel_valid[mask_bin]
        f0_med = float(np.median(f0_d[idx_in_bin])) if len(idx_in_bin) else float("nan")
        env_med = float(np.median(env_ratio_rows(sp_d[idx_in_bin], freqs))) if len(idx_in_bin) else float("nan")
        top5_ranges.append(dict(range_s=[lo, hi], count=cnt, frac=round(frac, 4),
                                 f0_median=round(f0_med, 2), env_ratio_median=round(env_med, 4)))
    log.append("top5 donor time bins (1s) by selection count:")
    for r in top5_ranges:
        log.append(f"  {r}")

    result = dict(
        stats_full_clip_voiced=stats_all,
        stats_260_400_voiced=stats_band,
        stats_nn_selected_multiplicity=stats_sel,
        stats_nn_selected_unique=stats_sel_unique,
        n_selected_instances=int(len(sel_valid)),
        n_unique_donor_frames_used=int(len(sel_unique)),
        top5_time_bins=top5_ranges,
    )

    for line in log:
        print(line)

    with open(f"{OUT}/f1b_h1_diagnosis.json", "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
