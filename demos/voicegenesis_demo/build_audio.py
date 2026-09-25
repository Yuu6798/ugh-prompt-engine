"""VoiceGenesis コンセプト動画: 声の合成・包絡の実測・タイムライン生成.

声はすべてこのスクリプトで合成する（Drive の実データは参照のみ）。
子・孫は「低域／高域を交差点で継ぐ」配合規則で親の包絡から作る。
画面の輪郭は、合成した WAV からケプストラム平滑化で実測した包絡を使う。

出力:
  audio/*.wav        各クリップ（ラウドネスのみ揃える）
  audio/mix.wav      動画用ミックス
  data.js            HTML が読む包絡・タイムライン（window.DATA）
  cue_sheet.json     どのファイルをどこで鳴らしたか
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import numpy as np
import soundfile as sf

SR = 44100
HERE = Path(__file__).resolve().parent
AUDIO = HERE / "audio"
XOVER_HZ = 2520.0  # 「あ」の交差点
XOVER_WIDTH = 100.0  # 遷移帯全幅
FMAX_VIEW = 5000.0
N_VIEW = 241
DB_RANGE = 36.0

# ---------------------------------------------------------------- voices

VOICES: Dict[str, dict] = {
    "Q": dict(
        f0=247.0, vib_rate=5.3, vib_depth=0.22, breath=0.035,
        formants=[(760, 90, 0), (1180, 110, 0), (2780, 170, 1), (3300, 190, 3), (4300, 260, -5)],
    ),
    "R": dict(
        f0=262.0, vib_rate=5.8, vib_depth=0.18, breath=0.055,
        formants=[(840, 80, 0), (1320, 100, 0), (2560, 150, -4), (3950, 180, 5), (4750, 260, -3)],
    ),
    "C03": dict(
        f0=294.0, vib_rate=5.0, vib_depth=0.26, breath=0.075,
        formants=[(900, 100, 0), (1420, 120, 0), (3080, 170, 3), (3700, 240, -8), (4450, 220, 5)],
    ),
}

GRID = np.linspace(0.0, SR / 2, 4097)


def formant_db(formants: List[tuple], f: np.ndarray) -> np.ndarray:
    f = np.maximum(f, 1.0)
    db = np.zeros_like(f)
    for F, B, g in formants:
        h = F * F / np.sqrt((F * F - f * f) ** 2 + (B * f) ** 2)
        db += 20 * np.log10(h) + g * np.exp(-((f - F) / (2.5 * B)) ** 2)
    # 声門源 -12 dB/oct + 放射 +6 dB/oct → 約 -6 dB/oct
    db += -6.0 * np.log2(np.maximum(f, 150.0) / 150.0)
    return db


def crossover(e_low: np.ndarray, e_high: np.ndarray, fc: float, f: np.ndarray) -> np.ndarray:
    """dB 包絡を交差点 fc で継ぐ（遷移帯は二乗余弦）。"""
    x = np.clip((f - (fc - XOVER_WIDTH / 2)) / XOVER_WIDTH, 0.0, 1.0)
    w = 0.5 - 0.5 * np.cos(np.pi * x)
    return (1 - w) * e_low + w * e_high


def env_db(name: str) -> np.ndarray:
    return formant_db(VOICES[name]["formants"], GRID)


def child_spec(low: str, high: str) -> dict:
    lo, hi = VOICES[low], VOICES[high]
    return dict(
        f0=lo["f0"], vib_rate=lo["vib_rate"], vib_depth=lo["vib_depth"],
        breath=0.5 * (lo["breath"] + hi["breath"]),
        env=crossover(lo["env"], hi["env"], XOVER_HZ, GRID),
        low=low, high=high,
    )


for _k in VOICES:
    VOICES[_k]["env"] = env_db(_k)
VOICES["子2"] = child_spec("R", "Q")
VOICES["孫"] = child_spec("子2", "C03")


# ---------------------------------------------------------------- synth

def _smooth_noise(n: int, rng: np.random.Generator, win: int) -> np.ndarray:
    x = rng.standard_normal(n + win)
    k = np.hanning(win)
    k /= k.sum()
    return np.convolve(x, k, mode="valid")[:n]


def synth(voice: dict, dur: float, seed: int, env_fn=None) -> np.ndarray:
    """加算合成。env_fn(t_frames) -> (n_frames, len(GRID)) dB で時間変化する包絡も可。"""
    n = int(round(dur * SR))
    t = np.arange(n) / SR
    rng = np.random.default_rng(seed)
    # F0: しゃくり（半音下から 90 ms）+ 後から入るビブラート + ゆらぎ
    scoop = -1.0 * np.exp(-t / 0.045)
    vib_in = np.clip((t - 0.35) / 0.4, 0, 1) ** 2
    vib = voice["vib_depth"] * vib_in * np.sin(2 * np.pi * voice["vib_rate"] * t)
    jit = 0.06 * _smooth_noise(n, rng, 2205) / 0.05
    semis = scoop + vib + np.clip(jit, -0.15, 0.15)
    f0 = voice["f0"] * 2 ** (semis / 12)
    phi = 2 * np.pi * np.cumsum(f0) / SR
    # 振幅包絡
    a = np.ones(n)
    att, rel = int(0.07 * SR), int(0.22 * SR)
    a[:att] = 0.5 - 0.5 * np.cos(np.pi * np.arange(att) / att)
    a[-rel:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(rel) / rel)
    a *= 1 + 0.03 * _smooth_noise(n, rng, 4410) / 0.05

    hop = 256
    fr_t = np.arange(0, n + hop, hop) / SR
    fr_f0 = np.interp(fr_t, t, f0)
    if env_fn is None:
        env_frames = np.repeat(voice["env"][None, :], len(fr_t), axis=0)
    else:
        env_frames = env_fn(fr_t)
    kmax = int((SR / 2 - 500) / (voice["f0"] * 0.9))
    y = np.zeros(n)
    for k in range(1, kmax + 1):
        fk = k * fr_f0
        if np.all(fk > SR / 2 - 500):
            break
        amp_db = np.array([np.interp(fk[i], GRID, env_frames[i]) for i in range(len(fr_t))])
        amp = 10 ** (amp_db / 20) * (fk < SR / 2 - 500)
        y += np.interp(t, fr_t, amp) * np.sin(k * phi)
    # 息成分: 白色雑音を包絡で整形
    noise = rng.standard_normal(n)
    spec = np.fft.rfft(noise)
    fr = np.fft.rfftfreq(n, 1 / SR)
    shape = 10 ** (np.interp(fr, GRID, env_frames[len(fr_t) // 2]) / 20)
    noise = np.fft.irfft(spec * shape, n)
    y = y / (np.std(y) + 1e-12) + voice["breath"] * 4 * noise / (np.std(noise) + 1e-12)
    return (y * a).astype(np.float64)


def loudness_match(y: np.ndarray, target_dbfs: float = -20.0) -> np.ndarray:
    frame = int(0.05 * SR)
    rms = np.sqrt(np.convolve(y ** 2, np.ones(frame) / frame, mode="same"))
    steady = rms[rms > 0.5 * rms.max()]
    cur = 20 * np.log10(np.sqrt(np.mean(steady ** 2)) + 1e-12)
    y = y * 10 ** ((target_dbfs - cur) / 20)
    peak = np.max(np.abs(y))
    return y * (0.89 / peak) if peak > 0.89 else y


# ---------------------------------------------------------------- measurement

def measure_onset(y: np.ndarray) -> float:
    hop = int(0.002 * SR)
    frame = int(0.01 * SR)
    e = np.array([np.sqrt(np.mean(y[i:i + frame] ** 2)) for i in range(0, len(y) - frame, hop)])
    idx = int(np.argmax(e > 0.1 * e.max()))
    return idx * hop / SR


def measure_offset(y: np.ndarray) -> float:
    rev = measure_onset(y[::-1])
    return len(y) / SR - rev


def measure_envelope(y: np.ndarray, t0: float, t1: float) -> np.ndarray:
    """定常区間の平均パワースペクトル → ケプストラム平滑化 → 0–5 kHz の dB。"""
    nfft, hop = 4096, 512
    a, b = int(t0 * SR), int(t1 * SR)
    seg = y[a:b]
    win = np.hanning(nfft)
    frames = [seg[i:i + nfft] * win for i in range(0, len(seg) - nfft, hop)]
    p = np.mean([np.abs(np.fft.rfft(f)) ** 2 for f in frames], axis=0)
    logp = np.log(p + 1e-12)
    c = np.fft.irfft(logp)
    q = int(0.0016 * SR)
    lifter = np.zeros_like(c)
    lifter[:q] = 1
    lifter[-q + 1:] = 1
    lifter[q - 8:q] = np.hanning(16)[8:]
    lifter[-q + 1:-q + 9] = np.hanning(16)[:8]
    sm = np.fft.rfft(c * lifter).real
    db = 10 / np.log(10) * sm
    freqs = np.fft.rfftfreq(nfft, 1 / SR)
    view = np.linspace(0, FMAX_VIEW, N_VIEW)
    return np.interp(view, freqs, db)


# ---------------------------------------------------------------- handle / sweep

def minjerk(u: np.ndarray) -> np.ndarray:
    u = np.clip(u, 0, 1)
    return u ** 3 * (10 - 15 * u + 6 * u * u)


SPRING = dict(omega=15.0, zeta=0.82)


def spring_step(tau: np.ndarray, omega: float = SPRING["omega"], zeta: float = SPRING["zeta"]):
    tau = np.maximum(tau, 0)
    wd = omega * np.sqrt(1 - zeta * zeta)
    return 1 - np.exp(-zeta * omega * tau) * (np.cos(wd * tau) + zeta * omega / wd * np.sin(wd * tau))


def handle_hz(t: np.ndarray, drag: dict) -> np.ndarray:
    """ハンドル位置（Hz）: 押下→ドラッグ（最小躍度）→離す→スプリングで交差点へ。"""
    u = (t - drag["t_press"]) / (drag["t_release"] - drag["t_press"])
    f = drag["f_start"] + (drag["f_release"] - drag["f_start"]) * minjerk(u)
    after = t >= drag["t_release"]
    f = np.where(
        after,
        drag["f_release"] + (drag["f_snap"] - drag["f_release"]) * spring_step(t - drag["t_release"]),
        f,
    )
    return f


# ---------------------------------------------------------------- timeline

# (id, voice, start_in_video, dur, card)
PLAN = [
    ("c01", "Q", 0.62, 2.55, "親Q"),
    ("c02", "R", 3.37, 2.75, "親R"),
    ("c03", "sweep:R>Q", 6.30, 2.35, "親R×親Q"),
    ("c04", "子2", 8.82, 2.75, "子2"),
    ("c05", "子2", 11.90, 2.05, "子2"),
    ("c06", "C03", 14.18, 2.45, "C03"),
    ("c07", "sweep:子2>C03", 16.88, 2.35, "子2×C03"),
    ("c08", "孫", 19.40, 2.80, "孫"),
    ("c09", "子2", 22.40, 1.10, "子2"),
    ("c10", "孫", 23.68, 1.10, "孫"),
    ("c11", "C03", 24.96, 1.10, "C03"),
    ("c12", "孫", 26.24, 1.10, "孫"),
    ("c13", "Q", 27.66, 0.62, "親Q"),
    ("c14", "R", 28.38, 0.62, "親R"),
    ("c15", "子2", 29.06, 0.62, "子2"),
    ("c16", "C03", 29.74, 0.62, "C03"),
    ("c17", "孫", 30.42, 0.95, "孫"),
]
TOTAL = 31.80


def drag_for(clip_start: float) -> dict:
    return dict(
        t_press=clip_start + 0.05,
        t_release=clip_start + 1.50,
        f_start=FMAX_VIEW,
        f_release=2330.0,  # 少し行き過ぎて離す → スプリングで 2,520 Hz へ戻る
        f_snap=XOVER_HZ,
    )


def main() -> None:
    AUDIO.mkdir(exist_ok=True)
    mix = np.zeros(int(TOTAL * SR) + SR)
    cues = []
    clips = {}
    seed = 20260925
    for cid, voice, start, dur, card in PLAN:
        seed += 1
        drag = None
        if voice.startswith("sweep:"):
            low, high = voice[6:].split(">")
            spec = dict(VOICES[low])
            drag = drag_for(start)

            def env_fn(ft, low=low, high=high, drag=drag, start=start):
                fc = handle_hz(ft + start, drag)
                return np.stack([
                    crossover(VOICES[low]["env"], VOICES[high]["env"], float(c), GRID) for c in fc
                ])

            y = synth(spec, dur, seed, env_fn=env_fn)
        else:
            y = synth(VOICES[voice], dur, seed)
        y = loudness_match(y)
        fname = f"{cid}_{voice.replace(':', '_').replace('>', '-')}.wav"
        sf.write(AUDIO / fname, y.astype(np.float32), SR, subtype="PCM_24")
        on = measure_onset(y)
        off = measure_offset(y)
        a = int(round(start * SR))
        mix[a:a + len(y)] += y
        clips[cid] = dict(
            voice=voice, card=card, file=f"audio/{fname}", start=start, dur=dur,
            onset=round(start + on, 4), offset=round(start + off, 4), drag=drag,
        )
        cues.append(dict(
            id=cid, file=f"audio/{fname}", card=card, voice=voice,
            video_start_s=round(start, 4), duration_s=round(len(y) / SR, 4),
            measured_onset_s=round(start + on, 4), measured_offset_s=round(start + off, 4),
            processing="loudness_match_only",
        ))
        clips[cid]["_y"] = y

    # 無音ギャップ検査（声と声の間 ≤ 0.4 s）
    ordered = sorted(clips.values(), key=lambda c: c["onset"])
    gaps = [round(b["onset"] - a["offset"], 3) for a, b in zip(ordered, ordered[1:])]

    # 実測包絡（各声の最長クリップの定常区間）
    measured = {}
    for name, cid in [("Q", "c01"), ("R", "c02"), ("子2", "c04"), ("C03", "c06"), ("孫", "c08")]:
        c = clips[cid]
        y = c["_y"]
        on = c["onset"] - c["start"]
        off = c["offset"] - c["start"]
        measured[name] = measure_envelope(y, on + 0.4, off - 0.35)
    # 表示用のプリエンファシス（+10 dB/oct）で高域の山も見えるようにする。全カード共通
    view = np.linspace(0, FMAX_VIEW, N_VIEW)
    tilt = 10.0 * np.log2(np.maximum(view, 200.0) / 200.0)
    measured = {k: v + tilt for k, v in measured.items()}
    top = max(float(v.max()) for v in measured.values())
    norm = {k: np.clip((v - (top - DB_RANGE)) / DB_RANGE, 0.0, 1.0) for k, v in measured.items()}

    mix = mix[: int(TOTAL * SR)]
    sf.write(AUDIO / "mix.wav", mix.astype(np.float32), SR, subtype="PCM_24")

    for c in clips.values():
        c.pop("_y")
    data = dict(
        total=TOTAL, fmax=FMAX_VIEW, xover=XOVER_HZ, n=N_VIEW, spring=SPRING,
        env={k: [round(float(x), 4) for x in v] for k, v in norm.items()},
        clips=clips,
    )
    (HERE / "data.js").write_text(
        "window.DATA = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8"
    )
    (HERE / "cue_sheet.json").write_text(json.dumps(dict(
        note="声はすべて build_audio.py で合成。加工はラウドネス合わせのみ。",
        sample_rate=SR, total_s=TOTAL, silence_gaps_s=gaps, cues=cues,
    ), ensure_ascii=False, indent=2), encoding="utf-8")
    print("gaps", gaps, "max", max(gaps))
    for k, v in norm.items():
        print(k, "min", round(float(v.min()), 3), "max", round(float(v.max()), 3))


if __name__ == "__main__":
    main()
