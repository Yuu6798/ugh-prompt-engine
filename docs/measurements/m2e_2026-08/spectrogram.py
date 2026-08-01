#!/usr/bin/env python3
"""spectrogram.py — M2e r2 のベッド目視記録用スペクトログラム生成器（**条件凍結**）。

設計: `docs/DESIGN_M2e_vremix_real_bed.md` §3.4.5。

規律:

- **生成条件（窓長・hop・周波数軸・dB レンジ・カラーマップ）はここで凍結する。**
  実行者の裁量を残さない——裁量が残ると「見え方を変えて気に入る絵を出す」余地になる。
- **画像本体はリポジトリへ commit しない**（波形非コミット規律 / MUSDB18-HQ の
  非商用研究ライセンス / 高分解能スペクトログラムの近似復元性）。
  commit するのは**本スクリプト・各画像の sha256・1 行判定**のみ。
- 描画対象は **screening と同一のベッド窓 `[0, n_max]`**（別の区間を見ない）。
- 目視は**棄却方向にのみ効く一方向オーバーライド**。本スクリプトは判定を出さない
  ——数値も閾値も一切読まない（絵を数値で色付けしない）。

出力: `<out-dir>/bed_<index:02d>.png` と `<out-dir>/spectrogram_sha256.json`。

使い方:
    python spectrogram.py --beds <bed wav dir> --out <リポジトリ外の出力先>
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")   # ヘッドレス（表示バックエンド差で絵が変わらないように固定）

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

# --- 凍結パラメータ（設計 §3.4.5「生成条件はスクリプトに凍結」）-----------------
N_FFT = 4096            # 周波数分解能 ≈ 10.8 Hz @44.1kHz（調波列の分離に足りる）
HOP = 1024              # 時間分解能 ≈ 23.2 ms
WINDOW = "hann"
TOP_DB = 80.0           # dB レンジ（ピークからの深さ）
CMAP = "magma"
FIG_W_IN, FIG_H_IN, DPI = 16.0, 9.0, 110
# 周波数軸は**線形**に固定する。事由 (e)（16 kHz 付近の帯域打ち切り）は対数軸だと
# 高域が圧縮されて見落としやすい。事由 (a)/(b) の調波列・フォルマントは低域の
# 密度で読むため、低域を別パネルに拡大して併載する（2 段構成）。
LOW_BAND_HZ = 4000.0    # 上段: 0–4 kHz 拡大（vocadito 声の F0 帯 + 低次フォルマント）


def stft_db(y: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """凍結条件での STFT → dB。`(freqs, times, S_db)` を返す。"""
    win = np.hanning(N_FFT) if WINDOW == "hann" else np.ones(N_FFT)
    n_frames = 1 + (len(y) - N_FFT) // HOP
    frames = np.stack([y[k * HOP: k * HOP + N_FFT] * win for k in range(n_frames)], axis=1)
    spec = np.abs(np.fft.rfft(frames, n=N_FFT, axis=0))
    ref = float(spec.max()) or 1.0
    s_db = 20.0 * np.log10(np.maximum(spec, ref * 10.0 ** (-TOP_DB / 20.0)) / ref)
    freqs = np.fft.rfftfreq(N_FFT, d=1.0 / sample_rate)
    times = np.arange(n_frames) * HOP / sample_rate
    return freqs, times, s_db


def render(path: Path, out_png: Path, title: str) -> None:
    y, sample_rate = sf.read(str(path), dtype="float64", always_2d=False)
    freqs, times, s_db = stft_db(np.asarray(y, dtype=np.float64), int(sample_rate))
    fig, axes = plt.subplots(2, 1, figsize=(FIG_W_IN, FIG_H_IN), dpi=DPI, sharex=True)
    low = freqs <= LOW_BAND_HZ
    for ax, mask, label in (
        (axes[0], low, f"0–{int(LOW_BAND_HZ / 1000)} kHz (harmonics / formants)"),
        (axes[1], np.ones_like(freqs, dtype=bool), f"0–{sample_rate // 2} Hz (full band)"),
    ):
        ax.pcolormesh(times, freqs[mask], s_db[mask, :], cmap=CMAP,
                      vmin=-TOP_DB, vmax=0.0, shading="auto")
        ax.set_ylabel(f"Hz\n{label}", fontsize=8)
    axes[1].set_xlabel("time (s)")
    axes[0].set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(out_png)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--beds", required=True, help="ベッド窓 WAV のディレクトリ")
    parser.add_argument("--out", required=True, help="出力先（**リポジトリ外**）")
    parser.add_argument("--tracks", required=True, help="lexical order のトラック名 JSON")
    args = parser.parse_args()

    beds = Path(args.beds)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tracks = json.loads(Path(args.tracks).read_text(encoding="utf-8"))

    digests: dict[str, str] = {}
    for index, track in enumerate(tracks):
        src = beds / f"bed_{index:02d}.wav"
        if not src.is_file():
            continue
        png = out / f"bed_{index:02d}.png"
        render(src, png, f"[{index:02d}] {track}  —  bed window [0, n_max]")
        digests[f"bed_{index:02d}.png"] = hashlib.sha256(png.read_bytes()).hexdigest()
        print(f"[{index:02d}] {track}", flush=True)

    (out / "spectrogram_sha256.json").write_text(
        json.dumps(digests, indent=1, sort_keys=True), encoding="utf-8"
    )
    print(f"rendered {len(digests)} images")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
