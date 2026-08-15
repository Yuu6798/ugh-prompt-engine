"""VG-F1b Round 3 手順1: H1（NN選択の暗い区間集中）の定量確認。

- ドナー全有声フレームの env_ratio 分布 (median/p10/p90)
- 260-400Hz帯（有声）全フレームの env_ratio 分布
- Round 2 の NN 選択で実際に選ばれたフレーム(sel_idx_seq, 出力フレーム単位=多重度あり)の
  env_ratio 分布
- 選択がドナークリップ内のどの秒レンジに集中したか（1秒ビン、多重度で重み付け、上位5)

R11 で再現可能化のためパスをパラメータ化（元の実行は record 記載の環境で実施）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

# R11: `glue_template_nn` は本リポジトリ内の同ディレクトリモジュール（repo import）。
# cwd 依存の暗黙 import に頼らず、__file__ 相対でこのディレクトリを明示的に
# sys.path へ追加してから import する（元は実行時にコピーされた scratchpad 上の
# コピーを import していたが、この repo import は本ファイルと同じディレクトリの
# 正本 glue_template_nn.py を指す）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from glue_template_nn import load_donor_24k, analyze_donor, select_nn_sequence, FRAME_PERIOD_MS  # noqa: E402

# 本スクリプトが --out 配下へ書く固定出力名（衝突検査対象）
OUTPUT_NAMES = ("f1b_h1_diagnosis.json",)


class OutputCollisionError(ValueError):
    """固定出力名が --donor-wav / --control-npz と衝突する場合に送出する（fail-closed）。"""


def _reject_output_collision(out_path: str | Path, protected_inputs: list[str | Path]) -> None:
    """P1 修正 (review #262 R13): `glue_template.py` と同型の衝突ガード
    （詳細意図はそちら参照）。`--donor-wav`/`--control-npz` が本スクリプトの
    固定出力名と衝突する場合、書き込み開始前に fail-closed で拒否する。
    """
    out_resolved = Path(out_path).resolve()
    for p in protected_inputs:
        if p is None:
            continue
        p_path = Path(p)
        if not p_path.exists():
            continue
        if out_resolved == p_path.resolve():
            raise OutputCollisionError(
                f"出力 ({out_path}) が保護入力 ({p}) と衝突しています（fail-closed で拒否）"
            )


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="出力ディレクトリ（f1b_h1_diagnosis.json を書く）")
    parser.add_argument("--donor-wav", required=True, help="ドナー WAV パス（例: .../vocadito/Audio/vocadito_2.wav）")
    parser.add_argument("--control-npz", required=True, help="共通 f0/amp npz パス（f1a_control.npz）")
    args = parser.parse_args()
    out_dir = args.out

    # --- 出力衝突検査（書き込み開始前・fail-closed） ---
    protected_inputs = [args.donor_wav, args.control_npz]
    for name in OUTPUT_NAMES:
        _reject_output_collision(f"{out_dir}/{name}", protected_inputs)

    log = []
    donor_audio, sr = load_donor_24k(args.donor_wav)
    donor = analyze_donor(donor_audio, sr)
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
    ctrl = np.load(args.control_npz)
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

    with open(f"{out_dir}/f1b_h1_diagnosis.json", "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
