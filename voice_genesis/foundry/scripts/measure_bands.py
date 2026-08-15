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
import os
import tempfile
from pathlib import Path
from typing import Any, List, Optional

import numpy as np
import scipy.signal as sig
import soundfile as sf


class OutputCollisionError(ValueError):
    """P1 修正 (review #262 R4・`r3789341847`): `--out` が計測対象の入力 WAV の
    いずれかと衝突する（fail-closed。書き込み前に検出する。
    § adapter/render.py `OutputCollisionError` と対称の設計）。"""


_BAND_KEYS = ("b0_500", "b500_1k", "b1k_3k", "b3k_5k", "b5k_8k", "b8k_nyq")


def band_shares(x: np.ndarray, sr: int) -> dict[str, Any]:
    """Welch PSD（既定 nperseg=4096, noverlap=2048）の帯域別パワー比を返す。

    帯域: [0,500)/[500,1k)/[1k,3k)/[3k,5k)/[5k,8k)/[8k,nyq)。
    加えて `tilt_db_per_decade_1k_8k`（PSD dB の log10(f) 回帰勾配、1k-8kHz）。

    P2 修正 (review #262 R14・`r3789636063`): 入力が 2,048 サンプル以下
    （24kHz で約85ms）だと、SciPy は `nperseg` を内部で信号長へ縮小する一方
    `noverlap` は既定の 2048 のまま渡されるため `noverlap >= nperseg` エラーで
    クラッシュしていた（短い donor/計測クリップで計測 CLI が例外終了する）。
    `nperseg` を利用可能サンプル数から導出し、`noverlap` を常に `nperseg`
    未満に保つことでクラッシュを避ける。空入力は `welch` を一切呼ばず、
    `hnr_median_db` の「計測不能は NaN（JSON では null）」規約と対称に、
    全帯域 `None` + `status` フィールドで明示的に報告する（fail-fast の例外
    ではなく計測不能の記録）。
    """
    n = len(x)
    if n == 0:
        result: dict[str, Any] = {k: None for k in _BAND_KEYS}
        result["tilt_db_per_decade_1k_8k"] = None
        result["status"] = "empty_input"
        return result

    nperseg = min(4096, n)
    noverlap = nperseg // 2
    f, p = sig.welch(x, sr, nperseg=nperseg, noverlap=noverlap)
    p_db = 10 * np.log10(p + 1e-20)
    tot = np.sum(p)

    def band(lo: float, hi: float) -> float:
        m = (f >= lo) & (f < hi)
        return float(np.sum(p[m]) / (tot + 1e-20))

    m = (f >= 1000) & (f <= 8000)
    # 短入力は周波数分解能が粗く（bin 間隔 = sr/nperseg）、1k-8kHz 帯に 2 点
    # 未満しか入らないことがある（回帰に最低 2 点必要）。その場合は None。
    slope: Optional[float] = float(np.polyfit(np.log10(f[m]), p_db[m], 1)[0]) if np.sum(m) >= 2 else None

    result = {
        "b0_500": band(0, 500),
        "b500_1k": band(500, 1000),
        "b1k_3k": band(1000, 3000),
        "b3k_5k": band(3000, 5000),
        "b5k_8k": band(5000, 8000),
        "b8k_nyq": band(8000, sr / 2),
        "tilt_db_per_decade_1k_8k": slope,
        "status": "ok" if nperseg == 4096 else "short_input",
    }
    return result


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


def _reject_output_collision(out_path: str | Path, input_paths: List[str | Path]) -> None:
    """P1 修正 (review #262 R4・`r3789341847`): `--out` が `wav_paths`（位置引数の
    計測対象 WAV）のいずれかと衝突する場合は、分析後の書き込み前に fail-closed
    で拒否する（例: `measure_bands.py donor.wav --out donor.wav` は分析結果の
    JSON で donor.wav を上書きし、計測対象そのものを破壊してしまう）。

    resolved 比較（symlink 解決後の完全一致）で判定する（AGENTS.md Persistent
    Artifact Safety Gate 項目2）。存在しない入力パス（この時点までに
    `analyze_wav` が既に読めているはずだが、念のため防御的に）はスキップする。
    """
    out_resolved = Path(out_path).resolve()
    for p in input_paths:
        p_path = Path(p)
        if not p_path.exists():
            continue
        if out_resolved == p_path.resolve():
            raise OutputCollisionError(
                f"--out ({out_path}) が計測対象の入力 ({p}) と衝突しています（fail-closed で拒否）"
            )


def _atomic_write_text(text: str, out_path: str | Path) -> None:
    """P2 修正 (review #262 R4・`r3789341850`): staging + `os.replace` で計測
    レポート JSON を atomic 公開する（AGENTS.md Persistent Artifact Safety
    Gate 項目6「全構築後公開」準拠。§ adapter/render.py `_atomic_write_wav`
    と同じ流儀 —— `except BaseException` で staging を best-effort 削除して
    から re-raise）。
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=out_path.parent, prefix=f"{out_path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, out_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _round_floats(d: dict[str, Any], ndigits: int = 5) -> dict[str, Any]:
    """float 値を丸める。

    P2 修正 (review #262 R11・`r3789543163`): NaN/Inf は標準 JSON のトークン
    ではない（`hnr_median_db` は無声入力で NaN を返す仕様 — `hnr_median_db`
    docstring 参照）。ここで非有限値を `None`（JSON `null`）へ正規化し、
    `main()` 側の `json.dumps(..., allow_nan=False)` と合わせて非標準
    `NaN`/`Infinity` トークンが出力に混入しないようにする。
    """
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, float):
            out[k] = None if not np.isfinite(v) else round(v, ndigits)
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

    # P2 修正 (review #262 R11・`r3789543163`): allow_nan=False で標準外
    # トークン（NaN/Infinity）の混入を二重に防ぐ（`_round_floats` が事前に
    # None へ正規化済みだが、将来 `_round_floats` を経由しない値が混入しても
    # ここで fail-fast する防御）。
    text = json.dumps(results, indent=2, ensure_ascii=False, allow_nan=False)
    if args.out:
        # P1 修正 (review #262 R4・`r3789341847`): 公開（書き込み）前に入力との
        # 衝突を fail-closed で拒否する。
        _reject_output_collision(args.out, list(args.wav_paths))
        # P2 修正 (review #262 R4・`r3789341850`): staging + os.replace で atomic 公開。
        _atomic_write_text(text + "\n", args.out)
    else:
        print(text)


if __name__ == "__main__":
    main()
