"""VG-F1c Performance Transplant: 連続歌唱の包絡軌跡を保ったまま、マクロ音高だけを
さくら旋律へ差し替える対照実験。

F1b 耳判定「identity は転写・発声は破綻」を受け、破綻の最後の候補
「時間的整合性（連続な包絡軌跡 + 人間的 f0 微細構造）」を単独で切り分ける。

4 条件（すべて同一窓 W の sp/ap をそのまま・時間軸無改変で使用。振幅アーチは
掛けない = ドナーのダイナミクス保持。ピーク 0.6 正規化のみ）:

  (iv) roundtrip  — 窓 W を自身の f0/sp/ap で再合成（WORLD 透明性の天井）
  (i)  transplant — f0 をさくらマクロ音高 * ドナー微細構造(micro_cents) に差し替え
  (ii) robotf0    — 同じ sp/ap にロボット f0（ビブラート30c版・ドナー有声ゲート）を乗せる
  (iii) 参考       — 既存 f1b_nn_neutral.wav（合成不要・結果表に再掲のみ）

決定論: 乱数不使用。窓選択は 0.5s 刻みの決定論的グリッドサーチ（同率は最小 offset）。
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pyworld as pw
import soundfile as sf
from scipy.signal import resample_poly

OUT = "/tmp/claude-0/-home-user-ugh-prompt-engine/1c025cfe-cb3e-592b-b577-d0be9640c799/scratchpad/foundry_f1c"
DONOR_WAV = "/tmp/claude-0/-home-user-ugh-prompt-engine/1c025cfe-cb3e-592b-b577-d0be9640c799/scratchpad/foundry_f1b/vocadito/Audio/vocadito_2.wav"
NOTE_TRACK_VIB0 = f"{OUT}/f1c_note_track.npz"
NOTE_TRACK_VIB30 = f"{OUT}/f1c_note_track_vib30.npz"

SR = 24000
FRAME_PERIOD_MS = 5.0
WINDOW_STEP_SEC = 0.5
MACRO_MEDIAN_MS = 200.0  # ドナーマクロ音高の移動median窓幅
SHORT_GAP_MS = 300.0  # 短い無声ギャップとみなす上限（線形補間で橋渡し）
MICRO_CLIP_CENTS = 150.0


def sha256_of(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def load_donor_24k() -> tuple[np.ndarray, int]:
    x, sr = sf.read(DONOR_WAV)
    if x.ndim > 1:
        x = x.mean(axis=1)
    assert sr == 44100, f"unexpected donor sr={sr}"
    # 44100 -> 24000: 有理比 80/147 (gcd(24000,44100)=300)。F1b と同一手順。
    y = resample_poly(x, up=80, down=147)
    return np.ascontiguousarray(y.astype(np.float64)), SR


def analyze_donor(x: np.ndarray, sr: int) -> dict:
    """ドナー全体を WORLD (harvest/cheaptrick/d4c, 5ms) で分析する。"""
    f0, t = pw.harvest(x, sr, frame_period=FRAME_PERIOD_MS)
    sp = pw.cheaptrick(x, f0, t, sr)
    ap = pw.d4c(x, f0, t, sr)
    voiced_mask = f0 > 0
    return dict(f0=f0, t=t, sp=sp, ap=ap, voiced_mask=voiced_mask)


def n_frames_for(total_samples: int, sr: int) -> int:
    duration = total_samples / sr
    return int(np.floor(duration / (FRAME_PERIOD_MS / 1000.0))) + 1


def select_window(donor_voiced: np.ndarray, n_target_frames: int) -> tuple[int, float, list[dict]]:
    """0.5s 刻みで窓をスライドし、有声フレーム率最大の窓を選ぶ。

    同率は最小 offset を採用（argmax の既定動作で決定論）。
    """
    n_donor_frames = len(donor_voiced)
    step_frames = int(round(WINDOW_STEP_SEC / (FRAME_PERIOD_MS / 1000.0)))
    starts = list(range(0, n_donor_frames - n_target_frames + 1, step_frames))
    assert starts, "donor too short for target window length"
    ratios = []
    candidates = []
    for s in starts:
        r = float(donor_voiced[s:s + n_target_frames].mean())
        ratios.append(r)
        candidates.append(dict(start_frame=s, start_sec=s * FRAME_PERIOD_MS / 1000.0,
                                voiced_ratio=round(r, 6)))
    best_i = int(np.argmax(ratios))  # tie -> smallest index -> smallest offset
    return starts[best_i], ratios[best_i], candidates


def bridge_gaps_log2(f0_win: np.ndarray, max_gap_frames: int) -> tuple[np.ndarray, dict]:
    """log2(f0) の無声区間を、短いギャップのみ線形補間で橋渡しする。

    長いギャップ（max_gap_frames 超）は端の有効値で hold（edge-hold）する
    （窓は有声率最大で選定済みのため通常発生しない想定。発生数を記録する）。
    """
    n = len(f0_win)
    voiced = f0_win > 0
    log2f0 = np.full(n, np.nan, dtype=np.float64)
    log2f0[voiced] = np.log2(f0_win[voiced])

    bridged = log2f0.copy()
    gap_lengths: list[int] = []
    n_long_gap_frames = 0

    i = 0
    while i < n:
        if voiced[i]:
            i += 1
            continue
        j = i
        while j < n and not voiced[j]:
            j += 1
        gap_len = j - i
        gap_lengths.append(gap_len)
        left_ok = i > 0
        right_ok = j < n
        if gap_len <= max_gap_frames and left_ok and right_ok:
            lo, hi = log2f0[i - 1], log2f0[j]
            w = np.linspace(0.0, 1.0, gap_len + 2)[1:-1]
            bridged[i:j] = lo + (hi - lo) * w
        elif left_ok:
            bridged[i:j] = log2f0[i - 1]
            n_long_gap_frames += gap_len
        elif right_ok:
            bridged[i:j] = log2f0[j]
            n_long_gap_frames += gap_len
        else:
            bridged[i:j] = 0.0  # 窓全体無声（発生しない想定）の防御的フォールバック
            n_long_gap_frames += gap_len
        i = j

    stats = dict(n_gaps=len(gap_lengths), gap_lengths=gap_lengths,
                 max_gap_frames_threshold=max_gap_frames,
                 n_long_gap_frames=n_long_gap_frames)
    return bridged, stats


def moving_median(x: np.ndarray, window_frames: int) -> np.ndarray:
    """奇数窓の移動 median（端は利用可能な範囲で縮小窓、nearest 相当）。"""
    half = window_frames // 2
    n = len(x)
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out[i] = np.median(x[lo:hi])
    return out


def ffill_nonzero(x: np.ndarray) -> tuple[np.ndarray, int]:
    """0 のサンプルを直近の非ゼロ値で hold する（先頭が 0 の場合は最初の非ゼロ値で埋める）。"""
    out = x.copy()
    n_held = int((x == 0).sum())
    nz = np.flatnonzero(x != 0)
    if len(nz) == 0:
        return out, n_held
    if nz[0] > 0:
        out[:nz[0]] = x[nz[0]]
    last = out[nz[0]]
    for i in range(nz[0], len(out)):
        if x[i] != 0:
            last = x[i]
        else:
            out[i] = last
    return out, n_held


def synth_peaknorm(f0_seq: np.ndarray, sp_seq: np.ndarray, ap_seq: np.ndarray, sr: int) -> np.ndarray:
    y = pw.synthesize(np.ascontiguousarray(f0_seq), np.ascontiguousarray(sp_seq),
                       np.ascontiguousarray(ap_seq), sr, FRAME_PERIOD_MS)
    y = np.asarray(y, dtype=np.float64)
    peak = np.max(np.abs(y))
    if peak > 0:
        y = y / peak * 0.6
    return y


def main() -> None:
    log: list[str] = []

    # --- ドナー読込 + 24kHz resample + WORLD 全体分析 ---
    donor_wav, sr = load_donor_24k()
    log.append(f"donor loaded: {len(donor_wav)} samples @ {sr}Hz "
               f"({len(donor_wav)/sr:.3f}s), resampled from 44100Hz via resample_poly(80/147)")
    log.append(f"donor wav sha256={sha256_of(DONOR_WAV)}")

    donor = analyze_donor(donor_wav, sr)
    n_donor_frames = len(donor["f0"])
    n_donor_voiced = int(donor["voiced_mask"].sum())
    log.append(f"donor WORLD analysis: n_frames={n_donor_frames} voiced_frames={n_donor_voiced} "
               f"({n_donor_voiced/n_donor_frames:.4f}) frame_period={FRAME_PERIOD_MS}ms")

    # --- ノートトラック読込 ---
    nt0 = np.load(NOTE_TRACK_VIB0)
    nt30 = np.load(NOTE_TRACK_VIB30)
    ctrl0_f0 = nt0["f0"]
    ctrl30_f0 = nt30["f0"]
    total_samples = int(nt0["total_samples"][0])
    ctrl_sr = int(nt0["sr"][0])
    assert ctrl_sr == SR
    assert int(nt30["total_samples"][0]) == total_samples
    log.append(f"note track vib0 loaded: total_samples={total_samples} ({total_samples/ctrl_sr:.3f}s) "
               f"voiced_frac={(ctrl0_f0>0).mean():.4f}")
    log.append(f"note track vib30 loaded: voiced_frac={(ctrl30_f0>0).mean():.4f}")

    n_target_frames = n_frames_for(total_samples, ctrl_sr)
    log.append(f"n_target_frames={n_target_frames} (== window length in donor frames, no time-stretch)")

    # --- 窓選定 ---
    start_frame, voiced_ratio, candidates = select_window(donor["voiced_mask"], n_target_frames)
    start_sec = start_frame * FRAME_PERIOD_MS / 1000.0
    log.append(f"window search: {len(candidates)} candidates @ step={WINDOW_STEP_SEC}s "
               f"(offsets {candidates[0]['start_sec']:.1f}s..{candidates[-1]['start_sec']:.1f}s)")
    log.append(f"window W selected: start_frame={start_frame} start_sec={start_sec:.3f}s "
               f"voiced_ratio={voiced_ratio:.4f} (max among candidates)")
    top5 = sorted(candidates, key=lambda c: -c["voiced_ratio"])[:5]
    log.append(f"top5 candidate windows (start_sec, voiced_ratio): "
               f"{[(c['start_sec'], c['voiced_ratio']) for c in top5]}")

    f0_w = donor["f0"][start_frame:start_frame + n_target_frames]
    sp_w = donor["sp"][start_frame:start_frame + n_target_frames]
    ap_w = donor["ap"][start_frame:start_frame + n_target_frames]
    voiced_w = f0_w > 0
    assert len(f0_w) == n_target_frames

    # --- ドナーのマクロ音高（移動median, log2領域, 短ギャップ橋渡し） ---
    max_gap_frames = int(round(SHORT_GAP_MS / FRAME_PERIOD_MS))
    bridged_log2, gap_stats = bridge_gaps_log2(f0_w, max_gap_frames)
    median_window_frames = int(round(MACRO_MEDIAN_MS / FRAME_PERIOD_MS))
    if median_window_frames % 2 == 0:
        median_window_frames += 1
    macro_log2 = moving_median(bridged_log2, median_window_frames)
    macro_d = 2.0 ** macro_log2
    log.append(f"macro pitch: moving median window={median_window_frames} frames "
               f"({median_window_frames*FRAME_PERIOD_MS:.0f}ms), gap-bridge threshold={max_gap_frames} "
               f"frames ({SHORT_GAP_MS:.0f}ms)")
    log.append(f"gap stats in window W: n_gaps={gap_stats['n_gaps']} "
               f"lengths_frames={gap_stats['gap_lengths']} "
               f"n_long_gap_frames(edge-hold, > threshold)={gap_stats['n_long_gap_frames']}")

    # --- 微細構造 ---
    micro_cents_raw = np.zeros(n_target_frames, dtype=np.float64)
    micro_cents_raw[voiced_w] = 1200.0 * np.log2(f0_w[voiced_w] / macro_d[voiced_w])
    n_clipped = int((np.abs(micro_cents_raw[voiced_w]) > MICRO_CLIP_CENTS).sum())
    micro_cents = np.clip(micro_cents_raw, -MICRO_CLIP_CENTS, MICRO_CLIP_CENTS)
    log.append(f"micro_cents: computed at {int(voiced_w.sum())} donor-voiced frames, "
               f"clipped(+-{MICRO_CLIP_CENTS}c) triggered on {n_clipped} frames "
               f"({n_clipped/max(1,int(voiced_w.sum())):.4f})")

    # --- フレーム時刻 -> ノートトラック per-sample index ---
    frame_t = np.arange(n_target_frames) * (FRAME_PERIOD_MS / 1000.0)
    sample_idx = np.minimum(np.round(frame_t * ctrl_sr).astype(np.int64), total_samples - 1)

    ctrl0_f0_ffilled, n_held0 = ffill_nonzero(ctrl0_f0)
    note_hz_at_frame = ctrl0_f0_ffilled[sample_idx]
    n_frames_in_breath_gate = int(((ctrl0_f0[sample_idx] == 0) & voiced_w).sum())
    log.append(f"note track (vib0) zero-hold: n_zero_samples_globally={n_held0} "
               f"({n_held0/total_samples:.4f} of samples). "
               f"At donor-voiced frames landing inside a sakura breath (note_hz==0, held): "
               f"n={n_frames_in_breath_gate}/{int(voiced_w.sum())} "
               f"({n_frames_in_breath_gate/max(1,int(voiced_w.sum())):.4f}) "
               f"-> policy: donor voice is allowed through on held note_hz (per brief)")

    ctrl30_f0_at_frame = ctrl30_f0[sample_idx]
    n_robot_gap_zero = int(((ctrl30_f0_at_frame == 0) & voiced_w).sum())
    log.append(f"note track (vib30) for robotf0: at donor-voiced frames where the vib30 track "
               f"itself is 0 (inside a sakura breath, NOT held per brief): n={n_robot_gap_zero}/"
               f"{int(voiced_w.sum())} ({n_robot_gap_zero/max(1,int(voiced_w.sum())):.4f}) "
               f"-> those frames synthesize as f0=0 despite donor voicing (recorded, not corrected)")

    # === 条件 (iv) roundtrip ===
    f0_iv = f0_w.copy()
    y_iv = synth_peaknorm(f0_iv, sp_w, ap_w, ctrl_sr)
    sf.write(f"{OUT}/f1c_roundtrip.wav", y_iv, ctrl_sr, subtype="PCM_16")
    log.append(f"wrote f1c_roundtrip.wav: {len(y_iv)} samples ({len(y_iv)/ctrl_sr:.3f}s), "
               f"window's own f0/sp/ap, no modification, peak-norm 0.6")

    # === 条件 (i) transplant ===
    f0_i = np.zeros(n_target_frames, dtype=np.float64)
    f0_i[voiced_w] = note_hz_at_frame[voiced_w] * (2.0 ** (micro_cents[voiced_w] / 1200.0))
    y_i = synth_peaknorm(f0_i, sp_w, ap_w, ctrl_sr)
    sf.write(f"{OUT}/f1c_transplant.wav", y_i, ctrl_sr, subtype="PCM_16")
    log.append(f"wrote f1c_transplant.wav: {len(y_i)} samples ({len(y_i)/ctrl_sr:.3f}s), "
               f"f0=note_hz(vib0,ffilled)*2^(micro_cents/1200) at donor-voiced frames, "
               f"f0=0 at donor-unvoiced frames, window's own sp/ap unmodified, peak-norm 0.6")

    # === 条件 (ii) robotf0 ===
    f0_ii = np.zeros(n_target_frames, dtype=np.float64)
    f0_ii[voiced_w] = ctrl30_f0_at_frame[voiced_w]
    y_ii = synth_peaknorm(f0_ii, sp_w, ap_w, ctrl_sr)
    sf.write(f"{OUT}/f1c_robotf0.wav", y_ii, ctrl_sr, subtype="PCM_16")
    log.append(f"wrote f1c_robotf0.wav: {len(y_ii)} samples ({len(y_ii)/ctrl_sr:.3f}s), "
               f"f0=note_hz(vib30, unheld) at donor-voiced frames, f0=0 at donor-unvoiced frames "
               f"(and at donor-voiced frames landing in a sakura breath, see above), "
               f"window's own sp/ap unmodified, peak-norm 0.6")

    # --- f0 統計: (i) のノートトラックからの偏差 cents ---
    dev = micro_cents[voiced_w]  # clip 済み。定義上ノートトラックからの偏差そのもの
    dev_abs = np.abs(dev)
    f0_stats_i = dict(
        n_donor_voiced_frames=int(voiced_w.sum()),
        deviation_cents_median=float(np.median(dev)),
        deviation_cents_abs_median=float(np.median(dev_abs)),
        deviation_cents_abs_p90=float(np.percentile(dev_abs, 90)),
        deviation_cents_min=float(dev.min()),
        deviation_cents_max=float(dev.max()),
        clip_trigger_rate=n_clipped / max(1, int(voiced_w.sum())),
    )
    log.append(f"(i) transplant f0 deviation from note track (cents, clipped +-{MICRO_CLIP_CENTS}): "
               f"median={f0_stats_i['deviation_cents_median']:.2f} "
               f"abs_median={f0_stats_i['deviation_cents_abs_median']:.2f} "
               f"abs_p90={f0_stats_i['deviation_cents_abs_p90']:.2f} "
               f"range=[{f0_stats_i['deviation_cents_min']:.2f}, {f0_stats_i['deviation_cents_max']:.2f}]")

    for line in log:
        print(line)

    with open(f"{OUT}/f1c_glue_run_log.json", "w") as f:
        json.dump(dict(
            log=log,
            window=dict(start_frame=start_frame, start_sec=start_sec, voiced_ratio=voiced_ratio,
                        n_target_frames=n_target_frames, top5_candidates=top5),
            gap_stats=gap_stats,
            micro_cents_clip_stats=dict(n_donor_voiced=int(voiced_w.sum()), n_clipped=n_clipped),
            note_track_zero_hold=dict(n_held_samples_global=n_held0,
                                       n_frames_in_breath_gate_transplant=n_frames_in_breath_gate,
                                       n_frames_zero_gap_robotf0=n_robot_gap_zero),
            f1c_i_transplant_f0_deviation_cents=f0_stats_i,
        ), f, indent=2, ensure_ascii=False)
    log_lines = len(open(__file__).readlines())
    print(f"\nglue_transplant.py line count: {log_lines}")


if __name__ == "__main__":
    main()
