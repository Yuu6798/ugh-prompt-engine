"""scripts/measure_structure_pattern.py — K2-seg バッチ 2 structure 欄 grip 計測 CLI。

`docs/controllability_poc.md` §「K2-seg バッチ 2: structure 欄センサー設計」の
判定規約の計測側実装。比較器本体（区間 RMS 符号パターン一致率）は
`svp_rpe.control.structure_pattern` に repo 昇格済みで再実装しない。novelty
境界数は `svp_rpe.rpe.structure_novelty`、効果量は `svp_rpe.control.grip_effect_size`
をそのまま import する。

判定ラベル（tight/loose/dead）はこのスクリプトの責務外 — 生成した数値に対して
plan.yaml / order_sheet の事前登録規約を設計側が適用する。
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import librosa
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from svp_rpe.control import grip_effect_size  # noqa: E402
from svp_rpe.control.structure_pattern import (  # noqa: E402
    pattern_match_rate,
    rms_to_db,
    sign_pattern,
    split_section_rms,
)
from svp_rpe.rpe.structure_novelty import compute_novelty_curve, find_boundaries  # noqa: E402

SCHEMA_VERSION = "1.0"
PRESCRIBED_PATTERN: list[str] = ["low", "high", "low"]


@dataclass(frozen=True)
class SongMeasurement:
    path: str
    duration_sec: float
    section_rms_db: list[float]
    sign_pattern: list[str]
    match_rate: float
    novelty_boundary_count: int


def _novelty_boundary_count(y: np.ndarray, sr: int) -> int:
    """extractor.py と同一の canonical 経路で novelty 境界数を数える。

    `find_boundaries` は常に先頭 0.0 と末尾 duration を含めて返すため、
    「検出された」境界数はそれらを除いた区間内部の個数。
    """
    duration = len(y) / sr
    novelty = compute_novelty_curve(y, sr)
    boundaries = find_boundaries(novelty, sr, duration)
    return max(0, len(boundaries) - 2)


def measure_song(path: Path, prescribed: list[str] | None = None) -> SongMeasurement:
    prescribed_pattern = prescribed if prescribed is not None else PRESCRIBED_PATTERN
    y, sr = librosa.load(str(path), sr=None, mono=True)
    duration_sec = round(float(len(y) / sr), 4) if sr else 0.0

    # 事前登録規約: 符号化は線形 RMS の算術平均比較（dB は出力 YAML の表示用のみ）。
    section_rms = split_section_rms(y, len(prescribed_pattern))
    section_rms_db = [rms_to_db(value) for value in section_rms]
    observed_pattern = sign_pattern(section_rms)
    match = round(pattern_match_rate(observed_pattern, prescribed_pattern), 6)
    novelty_boundary_count = _novelty_boundary_count(y, sr)

    return SongMeasurement(
        path=str(path),
        duration_sec=duration_sec,
        section_rms_db=section_rms_db,
        sign_pattern=observed_pattern,
        match_rate=match,
        novelty_boundary_count=novelty_boundary_count,
    )


def measure_cell(paths: list[Path], prescribed: list[str]) -> list[SongMeasurement]:
    return [measure_song(path, prescribed) for path in paths]


def build_report(
    low_paths: list[Path],
    high_paths: list[Path],
    *,
    prescribed: list[str] | None = None,
) -> dict[str, Any]:
    prescribed_pattern = prescribed if prescribed is not None else PRESCRIBED_PATTERN

    low_songs = measure_cell(low_paths, prescribed_pattern)
    high_songs = measure_cell(high_paths, prescribed_pattern)

    match_rate_low_cell = float(np.mean([s.match_rate for s in low_songs])) if low_songs else None
    match_rate_high_cell = (
        float(np.mean([s.match_rate for s in high_songs])) if high_songs else None
    )

    novelty_d: float | None = None
    if low_songs and high_songs:
        novelty_d = grip_effect_size(
            [s.novelty_boundary_count for s in low_songs],
            [s.novelty_boundary_count for s in high_songs],
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "prescribed_pattern": prescribed_pattern,
        "songs": {
            "low": [asdict(s) for s in low_songs],
            "high": [asdict(s) for s in high_songs],
        },
        "aggregate": {
            "match_rate_low_cell": (
                round(match_rate_low_cell, 6) if match_rate_low_cell is not None else None
            ),
            "match_rate_high_cell": (
                round(match_rate_high_cell, 6) if match_rate_high_cell is not None else None
            ),
            "novelty_d": round(novelty_d, 6) if novelty_d is not None else None,
        },
    }


def render_yaml(report: dict[str, Any]) -> str:
    return yaml.safe_dump(report, sort_keys=False, allow_unicode=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "K2-seg batch2 structure 欄 grip 計測: low/high セルの音源リストを受け取り "
            "区間 RMS 符号パターン一致率 + novelty 境界数の d を YAML で stdout に出力する。"
        ),
    )
    parser.add_argument(
        "--low",
        nargs="+",
        type=Path,
        required=True,
        metavar="PATH",
        help="low セルの音源ファイルパス（複数可）",
    )
    parser.add_argument(
        "--high",
        nargs="+",
        type=Path,
        required=True,
        metavar="PATH",
        help="high セルの音源ファイルパス（複数可）",
    )
    parser.add_argument(
        "--pattern",
        default=",".join(PRESCRIBED_PATTERN),
        help=f"処方パターン（カンマ区切り、既定: {','.join(PRESCRIBED_PATTERN)}）",
    )
    args = parser.parse_args(argv)

    prescribed = [token.strip() for token in args.pattern.split(",")]
    report = build_report(args.low, args.high, prescribed=prescribed)
    sys.stdout.write(render_yaml(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
