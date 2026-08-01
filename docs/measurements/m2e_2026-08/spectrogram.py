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
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")   # ヘッドレス（表示バックエンド差で絵が変わらないように固定）

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("mk", REPO / "scripts" / "make_vremix_fixtures.py")
mk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mk)

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


def render(path: Path, out_png: Path, title: str, *, expected_sha256: str) -> None:
    y, sample_rate = sf.read(str(path), dtype="float64", always_2d=False)
    # 目視の会計は「50 件揃っている」ことではなく「**登録されたコホートを見た**」
    # ことを主張する。入力が drift していると、揃った digest map がその主張の
    # 証拠にならない。描く前に事前登録の bed window pin へ結び付ける。
    actual = mk.waveform_sha256(y)
    if actual != expected_sha256:
        raise ValueError(
            f"{path}: bed window sha256 {actual} が事前登録 pin {expected_sha256} と "
            "不一致; 登録コホートでない素材の画像を目視記録にしない (fail-closed)"
        )
    # `waveform_sha256` は**サンプル列しか**畳まない。同じ PCM を 48 kHz header で
    # 包み直すと pin は通るが、時間軸・周波数軸と 0–4 kHz パネルに入る bin が変わる
    # ——凍結していない幾何で目視記録を作ることになる。
    if int(sample_rate) != mk.CANONICAL_SAMPLE_RATE:
        raise ValueError(
            f"{path}: sample rate {sample_rate} != {mk.CANONICAL_SAMPLE_RATE}; "
            "描画条件は 44100 前提で凍結してある (fail-closed)"
        )
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
    # 画像は**リポジトリ外**へ出す規律（本モジュール docstring）。MUSDB18-HQ 由来で
    # 非商用研究ライセンス、かつ高分解能スペクトログラムは近似復元を許すため、
    # checkout 内へ出すと通常の `git add` で入りうる。
    resolved = out.resolve()
    if resolved == REPO or REPO in resolved.parents:
        raise ValueError(
            f"--out {resolved} がリポジトリ（{REPO}）配下; 目視記録の画像は commit "
            "しない規律なのでリポジトリ外へ出す (fail-closed)"
        )
    if out.exists() and any(out.iterdir()):
        # 50 枚描いた**あと**で気づくのでは遅い（rename 直前にも同じ検査をする）。
        raise ValueError(
            f"{out} が空でない; 前回の目視記録と世代が混ざるのを避けるため、"
            "空の出力先を指定するか既存を退避すること (fail-closed)"
        )
    tracks = json.loads(Path(args.tracks).read_text(encoding="utf-8"))

    # コホートそのものを登録簿へ束縛する（一覧が 49 件でも「正常終了」しないように）。
    mk.require_registered_track_list(tracks)
    registered = mk.load_registered_beds()

    # **staging → rename** で一式を公開する。既存の `--out` へ描き直して途中で落ちると、
    # 前半だけ新しい PNG・後半と `spectrogram_sha256.json` は前回のもの、という
    # **世代混在の目視記録**が外から見える状態で残る（前回の完全な一式と区別できない）。
    # 一意名なので後片付けもこの実行が持つ（`BaseException` で掃除して再送出）。
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=str(out.parent), prefix=f".{out.name}.staging-"))
    try:
        digests: dict[str, str] = {}
        for index, track in enumerate(tracks):
            src = beds / f"bed_{index:02d}.wav"
            if not src.is_file():
                # 取得・配置が途中でも「短い digest map を出して成功終了」してしまうと、
                # 部分出力が完了コホートと見分けられなくなる（§3.4.5 は 50 件全部の
                # 視覚的会計を要求する）。欠落は停止であってスキップではない。
                raise FileNotFoundError(
                    f"{src} が無い（[{index:02d}] {track}）; 全 50 件が揃わないまま "
                    "部分的な digest map を出さない (fail-closed)"
                )
            pin = registered.get(track, {}).get("bed_window_sha256")
            if not pin:
                raise ValueError(f"{track!r}: bed_window_sha256 が事前登録に無い (fail-closed)")
            png = staging / f"bed_{index:02d}.png"
            render(src, png, f"[{index:02d}] {track}  —  bed window [0, n_max]",
                   expected_sha256=pin)
            digests[f"bed_{index:02d}.png"] = hashlib.sha256(png.read_bytes()).hexdigest()
            print(f"[{index:02d}] {track}", flush=True)

        (staging / "spectrogram_sha256.json").write_text(
            json.dumps(digests, indent=1, sort_keys=True), encoding="utf-8"
        )
        if out.exists():
            if any(out.iterdir()):
                raise ValueError(
                    f"{out} が空でない; 前回の目視記録と世代が混ざるのを避けるため、"
                    "空の出力先を指定するか既存を退避すること (fail-closed)"
                )
            out.rmdir()
        os.rename(staging, out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(f"rendered {len(digests)} images")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
