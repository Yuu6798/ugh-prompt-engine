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
import os
import tempfile
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

    # source の生バイトを一度だけ読んで hash し、その**同じバイト列**を temp file へ
    # 凍結して decode する。hash と decode で別々に open すると、間に source が
    # 再生成・差し替えされた場合に manifest の source_sha256 が実際に変形を生んだ
    # 音と食い違う（Codex 指摘。external audio bytes 凍結と同型・AGENTS §8）。
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.input.stem
    source_bytes = args.input.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()

    # stem 衝突の fail-closed: 出力ファイル名は入力の basename（stem）だけから導くため、
    # 別ディレクトリの同名ソース（例 artist_a/song.wav と artist_b/song.wav）を同じ
    # out_dir へ生成すると後の run が前の `song__*` を黙って上書きし、additive な
    # negative ペア構築を壊す（Codex 指摘・AGENTS §8）。
    #
    # 既存の `<stem>__*` 成果物（manifest または variant WAV）が out_dir にあり、かつ
    # それが**現ソースと同一だと証明できない**場合は fail-closed で reject する。証明
    # できるのは既存 manifest の source_sha256 が現ソースと一致するときだけ（= 同一
    # ソースの冪等再生成）。manifest が無い/破損/pin 無し（prior_sha=None）や別ソース
    # （sha 不一致）は、prior 成果物が同一由来だと証明できないので上書きしない。
    existing_manifest = out_dir / f"{stem}__pairs_manifest.json"
    existing_targets = list(out_dir.glob(f"{stem}__*.wav"))
    if existing_manifest.exists():
        existing_targets.append(existing_manifest)
    if existing_targets:
        prior_sha = None
        if existing_manifest.exists():
            try:
                prior_sha = json.loads(existing_manifest.read_text(encoding="utf-8")).get(
                    "source_sha256"
                )
            except (ValueError, OSError):
                prior_sha = None
        if prior_sha != source_sha256:
            raise ValueError(
                f"pair output stem collision in {out_dir}: existing '{stem}__*' artifacts "
                f"cannot be proven to come from this source "
                f"(existing source_sha256 {prior_sha!r} != {source_sha256}). "
                "別ソース / pin 無し・破損 manifest / manifest 欠落の残骸は上書きしない "
                "fail-closed。別 --out-dir へ出力するか、既存の残骸を除去すること。"
            )

    # 成果物一式を staging ディレクトリに完成させてから out_dir へ os.replace で
    # 公開する。既存 out_dir へ直接書くと、途中で中断された場合に variant WAV と
    # manifest が食い違う半端な成果物が下流に見える（Codex 指摘・AGENTS §8）。
    # 最終 rename は per-file atomic で、manifest を**最後**に move するため、
    # manifest が存在する時点では必ず全 variant が配置済みである。
    #
    # temp は 2 つに分ける:
    #  - src_tmp（既定 /tmp 可）: source の凍結コピー（librosa が読むだけ・out_dir の
    #    fs に載る必要なし）。
    #  - stage_tmp（out_dir.parent 直下 = out_dir と同一 fs）: 公開対象の variant。
    #    既定 temp に置くと out_dir が別マウントのとき os.replace が EXDEV で失敗する
    #    ため、rename が同一 fs 内で成立するよう out_dir の親に作る（Codex 指摘）。
    with tempfile.TemporaryDirectory(prefix="melody-pairs-src-") as src_tmp, \
            tempfile.TemporaryDirectory(
                prefix=f".{stem}.stage-", dir=out_dir.parent
            ) as stage_tmp:
        frozen = Path(src_tmp) / args.input.name
        frozen.write_bytes(source_bytes)
        y, sr = librosa.load(frozen, sr=None, mono=True)

        variants = make_variants(
            y, sr, semitones=args.semitones, time_rates=args.time_rates
        )
        staging = Path(stage_tmp)
        manifest: Dict[str, Any] = {
            "source": str(args.input),
            "source_sha256": source_sha256,
            "sample_rate": sr,
            "variants": {},
        }
        staged: Dict[str, tuple[Path, Path]] = {}
        for name, wav in variants.items():
            filename = f"{stem}__{name}.wav"
            staged_path = staging / filename
            sf.write(staged_path, wav, sr, subtype="FLOAT")
            final_path = out_dir / filename
            staged[name] = (staged_path, final_path)
            manifest["variants"][name] = {"path": str(final_path), "sha256": _sha256(staged_path)}
        manifest_name = f"{stem}__pairs_manifest.json"
        staged_manifest = staging / manifest_name
        staged_manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        # 公開: 全 variant を先に配置し、最後に manifest を配置する。
        for staged_path, final_path in staged.values():
            os.replace(staged_path, final_path)
        os.replace(staged_manifest, out_dir / manifest_name)
    print(
        f"wrote {len(variants)} variants for {stem} to {out_dir}; "
        "positive pairs = base×variant (same song), negative pairs = across different sources"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
