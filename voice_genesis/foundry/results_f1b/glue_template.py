"""VG-F1b テンプレート路線スパイク: 実歌唱由来スペクトル包絡テンプレート + Genome変形。

ドナー(vocadito_2, 実歌唱, CC BY 4.0) を pyworld で分析し、有声フレームを
semitone ビンへ量子化して median sp/ap テンプレートを構築する。
さくら score 由来 f0/amp (f1a_control.npz) をテンプレートバンクに通し、
Genome 変形（formant_scale / spectral tilt / ap 底上げ）3種を合成する。

設計判断（生ログに転記): 音素ラベルを使わずピッチのみで索引するため、
出力は「ラで歌う」相当の単一音色になる。F1b の問いは「声に聴こえるか」であり
歌詞明瞭度ではないため、これで良いという判断のもとで実装する。

決定論: 乱数不使用（seed 固定も不要 — 完全決定論パイプライン）。

R11 で再現可能化のためパスをパラメータ化（元の実行は record 記載の環境で実施）。
"""
from __future__ import annotations

import argparse
import json
import hashlib
from pathlib import Path

import numpy as np
import pyworld as pw
import soundfile as sf
from scipy.signal import resample_poly
from scipy.ndimage import uniform_filter1d

SR = 24000
FRAME_PERIOD_MS = 5.0
SPARSE_THRESHOLD = 3  # このフレーム数未満のビンは疎とみなし隣接ビンへ拡張検索

# 本スクリプトが --out 配下へ書く固定出力名（衝突検査対象）
OUTPUT_NAMES = (
    "f1b_donor_excerpt.wav",
    "f1b_template_neutral.wav",
    "f1b_template_dark.wav",
    "f1b_template_bright_breathy.wav",
    "f1b_glue_run_log.json",
)


class OutputCollisionError(ValueError):
    """固定出力名が --donor-wav / --control-npz と衝突する場合に送出する（fail-closed）。"""


def _reject_output_collision(out_path: str | Path, protected_inputs: list[str | Path]) -> None:
    """P1 修正 (review #262 R13): `--donor-wav`/`--control-npz` に本スクリプトの
    固定出力名（`OUTPUT_NAMES`）と同じパスを渡すと、書き込みが実験の入力そのもの
    を破壊する（`--donor-wav <out>/f1b_template_neutral.wav` 等）。全固定出力を
    書き込み開始前に全保護入力へ resolved 比較（symlink 解決後の完全一致）で
    突き合わせ、衝突があれば fail-closed で拒否する
    （`measure_bands._reject_output_collision`/`render._reject_output_collision`
    と同型の軽量実装。詳細判断は `results_f1_4/f1_4_record_2026-08-15.md`
    「R13 対応」節参照）。
    """
    out_resolved = Path(out_path).resolve()
    for p in protected_inputs:
        if p is None:
            continue
        p_path = Path(p)
        if not p_path.exists():
            continue  # 未使用/未指定パス
        if out_resolved == p_path.resolve():
            raise OutputCollisionError(
                f"出力 ({out_path}) が保護入力 ({p}) と衝突しています（fail-closed で拒否）"
            )


def hz_to_bin(f0: np.ndarray) -> np.ndarray:
    """f0(Hz) -> 1半音幅の整数ビン（MIDI note 相当。A4=440Hz -> 69）。"""
    f0 = np.asarray(f0, dtype=np.float64)
    out = np.zeros_like(f0, dtype=np.int64)
    voiced = f0 > 0
    out[voiced] = np.round(69 + 12 * np.log2(f0[voiced] / 440.0)).astype(np.int64)
    return out


def load_donor_24k(donor_wav: str) -> tuple[np.ndarray, int]:
    x, sr = sf.read(donor_wav)
    if x.ndim > 1:
        x = x.mean(axis=1)
    # 44100 -> 24000: 有理比 80/147 (gcd(24000,44100)=300 -> 24000/300=80, 44100/300=147)
    assert sr == 44100, f"unexpected donor sr={sr}"
    y = resample_poly(x, up=80, down=147)
    return np.ascontiguousarray(y.astype(np.float64)), SR


def build_template_bank(x: np.ndarray, sr: int):
    """donor 波形を pyworld で分析し、semitone ビン毎の median sp/ap テンプレートを構築する。"""
    f0, t = pw.harvest(x, sr, frame_period=FRAME_PERIOD_MS)
    sp = pw.cheaptrick(x, f0, t, sr)
    ap = pw.d4c(x, f0, t, sr)

    bins = hz_to_bin(f0)
    voiced_mask = f0 > 0

    bank_sp: dict[int, np.ndarray] = {}
    bank_ap: dict[int, np.ndarray] = {}
    bank_count: dict[int, int] = {}
    for b in sorted(set(bins[voiced_mask].tolist())):
        sel = voiced_mask & (bins == b)
        bank_sp[b] = np.median(sp[sel], axis=0)
        bank_ap[b] = np.median(ap[sel], axis=0)
        bank_count[b] = int(sel.sum())

    return dict(f0=f0, t=t, sp=sp, ap=ap, bins=bins, voiced_mask=voiced_mask,
                bank_sp=bank_sp, bank_ap=bank_ap, bank_count=bank_count)


def lookup_bin(bank_sp: dict[int, np.ndarray], bank_ap: dict[int, np.ndarray],
               bank_count: dict[int, int], target_bin: int, sparse_threshold: int):
    """target_bin のテンプレートを返す。疎（count<threshold）または不在なら隣接ビンへ拡張検索。"""
    if target_bin in bank_count and bank_count[target_bin] >= sparse_threshold:
        return bank_sp[target_bin], bank_ap[target_bin], target_bin, False
    # 拡張検索: 距離昇順で十分なフレーム数を持つ最寄りビンを探す
    candidates = sorted(bank_count.keys(), key=lambda b: abs(b - target_bin))
    for b in candidates:
        if bank_count[b] >= 1:
            return bank_sp[b], bank_ap[b], b, (b != target_bin)
    raise RuntimeError("empty template bank")


def build_target_sequences(bank: dict, ctrl_f0: np.ndarray, ctrl_amp: np.ndarray,
                            total_samples: int, sr: int):
    """score f0/amp (per-sample @sr) を 5ms フレームへ落とし、テンプレートを引く。"""
    duration = total_samples / sr
    n_frames = int(np.floor(duration / (FRAME_PERIOD_MS / 1000.0))) + 1
    frame_t = np.arange(n_frames) * (FRAME_PERIOD_MS / 1000.0)

    n_bins = next(iter(bank["bank_sp"].values())).shape[0]
    f0_seq = np.zeros(n_frames, dtype=np.float64)
    sp_seq = np.zeros((n_frames, n_bins), dtype=np.float64)
    ap_seq = np.zeros((n_frames, n_bins), dtype=np.float64)

    last_sp = next(iter(bank["bank_sp"].values()))
    last_ap = next(iter(bank["bank_ap"].values()))
    n_unvoiced_carry = 0
    n_extended = 0
    used_bins: dict[int, int] = {}

    for i, tt in enumerate(frame_t):
        idx = min(int(round(tt * sr)), total_samples - 1)
        f0 = ctrl_f0[idx]
        if f0 > 0:
            b = int(np.round(69 + 12 * np.log2(f0 / 440.0)))
            sp_t, ap_t, used_b, extended = lookup_bin(
                bank["bank_sp"], bank["bank_ap"], bank["bank_count"], b, SPARSE_THRESHOLD
            )
            if extended:
                n_extended += 1
            used_bins[used_b] = used_bins.get(used_b, 0) + 1
            last_sp, last_ap = sp_t, ap_t
            f0_seq[i] = f0
            sp_seq[i] = sp_t
            ap_seq[i] = ap_t
        else:
            # 無声/無音フレーム: f0=0 とし、直前の有声テンプレートを carry-forward する
            # (設計判断: ビン境界を跨がないための単純策。副作用は生ログに記録)
            n_unvoiced_carry += 1
            f0_seq[i] = 0.0
            sp_seq[i] = last_sp
            ap_seq[i] = last_ap

    stats = dict(n_frames=n_frames, n_unvoiced_carry=n_unvoiced_carry,
                 n_extended=n_extended, used_bins=used_bins)
    return f0_seq, sp_seq, ap_seq, stats


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
    parser.add_argument("--out", required=True, help="I/O ディレクトリ（f1b_*.wav / f1b_glue_run_log.json を書く）")
    parser.add_argument("--donor-wav", required=True, help="ドナー WAV パス（例: .../vocadito/Audio/vocadito_2.wav）")
    parser.add_argument("--control-npz", required=True, help="共通 f0/amp npz パス（f1a_control.npz）")
    args = parser.parse_args()
    out_dir = args.out

    # --- 出力衝突検査（書き込み開始前・fail-closed） ---
    protected_inputs = [args.donor_wav, args.control_npz]
    for name in OUTPUT_NAMES:
        _reject_output_collision(f"{out_dir}/{name}", protected_inputs)

    log: list[str] = []

    # --- ドナー読込 + 24kHz resample ---
    donor, sr = load_donor_24k(args.donor_wav)
    log.append(f"donor loaded: {len(donor)} samples @ {sr}Hz "
               f"({len(donor)/sr:.3f}s), resampled from 44100Hz via resample_poly(80/147)")

    # 参照用: ドナー冒頭10秒抜粋を書き出す
    excerpt = donor[: int(10.0 * sr)]
    sf.write(f"{out_dir}/f1b_donor_excerpt.wav", excerpt, sr, subtype="PCM_16")
    log.append(f"wrote f1b_donor_excerpt.wav: {len(excerpt)} samples ({len(excerpt)/sr:.3f}s)")

    # --- テンプレートバンク構築 ---
    bank = build_template_bank(donor, sr)
    n_bins_bank = len(bank["bank_count"])
    log.append(f"template bank: {n_bins_bank} semitone bins populated "
               f"(voiced frames={int(bank['voiced_mask'].sum())}/{len(bank['voiced_mask'])})")
    counts_str = ", ".join(f"{b}:{c}" for b, c in sorted(bank["bank_count"].items()))
    log.append(f"bin frame counts: {counts_str}")
    sparse_bins = [b for b, c in bank["bank_count"].items() if c < SPARSE_THRESHOLD]
    log.append(f"sparse bins (<{SPARSE_THRESHOLD} frames, extension lookup applies when targeted): "
               f"{sorted(sparse_bins)}")

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

    # --- target フレーム列構築（neutral base） ---
    f0_seq, sp_seq_neutral, ap_seq_neutral, stats = build_target_sequences(
        bank, ctrl_f0, ctrl_amp, total_samples, ctrl_sr
    )
    log.append(f"target frames: n_frames={stats['n_frames']} "
               f"n_unvoiced_carry={stats['n_unvoiced_carry']} n_extended_lookup={stats['n_extended']}")
    used_bins_str = ", ".join(f"{b}:{c}" for b, c in sorted(stats["used_bins"].items()))
    log.append(f"target bins used (post-extension): {used_bins_str}")

    # --- 時間方向スムージング (window=3) ---
    sp_seq_neutral_sm = smooth_seq(sp_seq_neutral, window=3)
    ap_seq_neutral_sm = smooth_seq(ap_seq_neutral, window=3)

    results = {}

    # (a) neutral
    y_neutral = synth_and_shape(f0_seq, sp_seq_neutral_sm, ap_seq_neutral_sm, ctrl_amp, ctrl_sr)
    sf.write(f"{out_dir}/f1b_template_neutral.wav", y_neutral, ctrl_sr, subtype="PCM_16")
    results["neutral"] = dict(n_samples=len(y_neutral), dur_s=len(y_neutral) / ctrl_sr)
    log.append(f"wrote f1b_template_neutral.wav: {len(y_neutral)} samples "
               f"({len(y_neutral)/ctrl_sr:.3f}s), peak-normalized to 0.6")

    # (b) dark: formant_scale 0.94 + tilt -2dB/oct
    sp_dark = freq_warp(sp_seq_neutral_sm, scale=0.94, sr=ctrl_sr)
    sp_dark = spectral_tilt(sp_dark, sr=ctrl_sr, db_per_octave=-2.0)
    ap_dark = freq_warp(ap_seq_neutral_sm, scale=0.94, sr=ctrl_sr)
    ap_dark = np.clip(ap_dark, 0.0, 1.0)
    y_dark = synth_and_shape(f0_seq, sp_dark, ap_dark, ctrl_amp, ctrl_sr)
    sf.write(f"{out_dir}/f1b_template_dark.wav", y_dark, ctrl_sr, subtype="PCM_16")
    results["dark"] = dict(n_samples=len(y_dark), dur_s=len(y_dark) / ctrl_sr)
    log.append(f"wrote f1b_template_dark.wav: {len(y_dark)} samples "
               f"({len(y_dark)/ctrl_sr:.3f}s), formant_scale=0.94 + tilt=-2dB/oct, peak-norm 0.6")

    # (c) bright_breathy: formant_scale 1.06 + ap +0.15 (clip)
    sp_bright = freq_warp(sp_seq_neutral_sm, scale=1.06, sr=ctrl_sr)
    ap_bright = freq_warp(ap_seq_neutral_sm, scale=1.06, sr=ctrl_sr)
    ap_bright = np.clip(ap_bright + 0.15, 0.0, 1.0)
    y_bright = synth_and_shape(f0_seq, sp_bright, ap_bright, ctrl_amp, ctrl_sr)
    sf.write(f"{out_dir}/f1b_template_bright_breathy.wav", y_bright, ctrl_sr, subtype="PCM_16")
    results["bright_breathy"] = dict(n_samples=len(y_bright), dur_s=len(y_bright) / ctrl_sr)
    log.append(f"wrote f1b_template_bright_breathy.wav: {len(y_bright)} samples "
               f"({len(y_bright)/ctrl_sr:.3f}s), formant_scale=1.06 + ap+0.15(clip), peak-norm 0.6")

    # ドナー転写距離 (score f0 median vs donor 実測 f0 median, semitone)
    donor_f0, _ = pw.harvest(donor, sr, frame_period=FRAME_PERIOD_MS)
    donor_f0_median = float(np.median(donor_f0[donor_f0 > 0]))
    semitone_dist = 12 * np.log2(donor_f0_median / ctrl_f0_median)
    log.append(f"donor transcription distance: donor f0 median={donor_f0_median:.2f}Hz vs "
               f"control f0 median={ctrl_f0_median:.2f}Hz -> {semitone_dist:.3f} semitone")

    for line in log:
        print(line)

    with open(f"{out_dir}/f1b_glue_run_log.json", "w") as f:
        json.dump(dict(log=log, results=results,
                        control_f0_median=ctrl_f0_median, donor_f0_median=donor_f0_median,
                        semitone_distance=semitone_dist), f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
