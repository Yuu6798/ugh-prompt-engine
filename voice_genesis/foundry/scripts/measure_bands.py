"""帯域占有率 + HNR 計測 CLI（VG-F0 計測計器）。

`results_f0/v5_diagnosis_2026-08-15.md` の v5 診断記録と同一手法（Welch PSD の
帯域別パワー比 + 自己相関ベース HNR）で数値互換な清書版。依存は numpy / scipy /
soundfile のみ（librosa 不使用・torch 不使用）。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import scipy.signal as sig
import soundfile as sf


def band_shares(x: np.ndarray, sr: int) -> dict[str, float]:
    """Welch PSD（nperseg=4096, noverlap=2048）の帯域別パワー比を返す。

    帯域: [0,500)/[500,1k)/[1k,3k)/[3k,5k)/[5k,8k)/[8k,nyq)。
    加えて `tilt_db_per_decade_1k_8k`（PSD dB の log10(f) 回帰勾配、1k-8kHz）。
    """
    f, p = sig.welch(x, sr, nperseg=4096, noverlap=2048)
    p_db = 10 * np.log10(p + 1e-20)
    tot = np.sum(p)

    def band(lo: float, hi: float) -> float:
        m = (f >= lo) & (f < hi)
        return float(np.sum(p[m]) / (tot + 1e-20))

    m = (f >= 1000) & (f <= 8000)
    slope = float(np.polyfit(np.log10(f[m]), p_db[m], 1)[0])

    return {
        "b0_500": band(0, 500),
        "b500_1k": band(500, 1000),
        "b1k_3k": band(1000, 3000),
        "b3k_5k": band(3000, 5000),
        "b5k_8k": band(5000, 8000),
        "b8k_nyq": band(8000, sr / 2),
        "tilt_db_per_decade_1k_8k": slope,
    }


def hnr_median_db(x: np.ndarray, sr: int) -> float:
    """自己相関ベースの HNR（有声フレームの中央値、dB）。

    40ms 窓 / 10ms hop / lag 範囲 = sr/700〜sr/80。フレーム RMS < 1e-3 は無声。
    フレーム平均を除去した正規化自己相関のピーク r（r>0.35 を有声）から
    HNR = 10*log10(r/(1-r))。有声フレームが無ければ nan を返す。
    """
    win = int(0.040 * sr)
    hop = int(0.010 * sr)
    lag_min, lag_max = int(sr / 700), int(sr / 80)
    hnrs: list[float] = []
    for start in range(0, len(x) - win, hop):
        fr = x[start : start + win]
        e = np.sqrt(np.mean(fr**2))
        if e < 1e-3:
            continue
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, mode="full")[len(fr) - 1 :]
        ac = ac / (ac[0] + 1e-20)
        seg = ac[lag_min:lag_max]
        if len(seg) == 0:
            continue
        k = int(np.argmax(seg)) + lag_min
        r = float(ac[k])
        if r <= 0.35:
            continue
        rr = min(max(r, 1e-6), 1 - 1e-6)
        hnrs.append(10 * np.log10(rr / (1 - rr)))
    if not hnrs:
        return float("nan")
    return float(np.median(hnrs))


def analyze_wav(path: str | Path) -> dict[str, Any]:
    """WAV を読み込み、帯域占有率 + HNR + 基本統計量を返す。

    Persistent Artifact Safety Gate（AGENTS.md 項目1）: 単一 read で parse +
    hash する（decode 用 `sf.read` と hash 用 `read_bytes` を別々に呼ぶと、
    両者の間にファイルが書き換えられた場合に記録される sha256 が実際に
    解析したサンプル列と食い違う TOCTOU 窓が生まれるため）。1 回の
    `read_bytes()` で得た同一バイト列から、`io.BytesIO` 経由のデコードと
    sha256 の両方を導出する。
    """
    data = Path(path).read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    x, sr = sf.read(io.BytesIO(data), dtype="float64")
    if x.ndim > 1:
        x = x.mean(axis=1)

    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    rms = float(np.sqrt(np.mean(x**2))) if len(x) else 0.0

    result: dict[str, Any] = {
        "sr": int(sr),
        "dur_s": len(x) / sr if sr else 0.0,
        "peak_dbfs": 20 * np.log10(peak + 1e-12),
        "rms_dbfs": 20 * np.log10(rms + 1e-12),
        "crest_db": 20 * np.log10(peak / (rms + 1e-12)),
        "sha256": sha256,
    }
    result.update(band_shares(x, sr))
    result["hnr_median_db"] = hnr_median_db(x, sr)
    return result


def _round_floats(d: dict[str, Any], ndigits: int = 5) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, float):
            out[k] = v if np.isnan(v) else round(v, ndigits)
        else:
            out[k] = v
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="帯域占有率 + HNR 計測 CLI")
    parser.add_argument("wav_paths", nargs="+", help="計測対象 WAV パス（複数可）")
    parser.add_argument("--out", default=None, help="JSON 保存先（省略時 stdout）")
    args = parser.parse_args()

    results = []
    for p in args.wav_paths:
        r = analyze_wav(p)
        r = {"file": Path(p).name, **_round_floats(r)}
        results.append(r)

    text = json.dumps(results, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
