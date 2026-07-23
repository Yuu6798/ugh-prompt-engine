"""make_melody_pairs.py — 正解 MIDI 不要の controlled pair 生成（M0）。

設計 §3.1 ★Cowork 手元データの流用: 実曲（Suno 出力等・歌入り実ミックス）から
正解 MIDI なしで「同じ旋律の移調・変速版」を機械生成し、M1 の弁別材料
（同曲変形 vs 異曲）を著作権上安全な自作素材だけで組む。

- positive ペア（同一旋律）: 入力 1 曲を librosa で ±半音 pitch-shift /
  ±time-stretch した版を生成 → 「同曲の移調・変速版」。
- negative ペア（異旋律）: 別々の入力曲同士（本スクリプトは各入力の変形版を
  出力するだけで、negative は異なる入力曲を並べることで構成する）。

決定論: librosa の pitch_shift / time_stretch は乱数を用いない。同一入力 + 同一
パラメータ → 同一出力。生成物には正解 MIDI が無いので、観測可能性（M1）にのみ
使い、RPA/RCA（正解精度・M2）には使わないこと（設計 §7 禁止事項）。

使い方::

    python scripts/make_melody_pairs.py --input song.wav --out-dir /tmp/pairs \
        --semitones -2 3 --time-rates 0.92 1.08
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

import librosa
import numpy as np
import soundfile as sf


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_variants(
    y: np.ndarray,
    sr: int,
    *,
    semitones: List[float],
    time_rates: List[float],
) -> Dict[str, np.ndarray]:
    """入力 1 曲から決定論的な pitch-shift / time-stretch 変形版を作る。"""
    variants: Dict[str, np.ndarray] = {"base": np.asarray(y, dtype=np.float32)}
    for n in semitones:
        shifted = librosa.effects.pitch_shift(y, sr=sr, n_steps=float(n))
        variants[f"pitch_{n:+g}st"] = np.asarray(shifted, dtype=np.float32)
    for rate in time_rates:
        stretched = librosa.effects.time_stretch(y, rate=float(rate))
        variants[f"time_x{rate:g}"] = np.asarray(stretched, dtype=np.float32)
    return variants


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="入力 WAV/音源")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--semitones",
        type=float,
        nargs="*",
        default=[-2.0, 3.0],
        help="pitch-shift する半音数の列（positive ペア用）",
    )
    parser.add_argument(
        "--time-rates",
        type=float,
        nargs="*",
        default=[0.92, 1.08],
        help="time-stretch の rate 列（>1 で速く / positive ペア用）",
    )
    args = parser.parse_args()

    y, sr = librosa.load(args.input, sr=None, mono=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.input.stem

    variants = make_variants(
        y, sr, semitones=args.semitones, time_rates=args.time_rates
    )
    manifest: Dict[str, Any] = {"source": str(args.input), "sample_rate": sr, "variants": {}}
    for name, wav in variants.items():
        out_path = args.out_dir / f"{stem}__{name}.wav"
        sf.write(out_path, wav, sr, subtype="FLOAT")
        manifest["variants"][name] = {"path": str(out_path), "sha256": _sha256(out_path)}
    (args.out_dir / f"{stem}__pairs_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(
        f"wrote {len(variants)} variants for {stem} to {args.out_dir}; "
        "positive pairs = base×variant (same song), negative pairs = across different sources"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
