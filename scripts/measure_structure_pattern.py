"""scripts/measure_structure_pattern.py — K2-seg バッチ 2 structure 欄 grip 計測 CLI。

`docs/controllability_poc.md` §「K2-seg バッチ 2: structure 欄センサー設計」の
判定規約の計測側実装。比較器本体（区間 RMS 符号パターン一致率）は
`svp_rpe.control.structure_pattern` に repo 昇格済みで再実装しない。novelty
境界数は `svp_rpe.rpe.structure_novelty`、効果量は `svp_rpe.control.grip_effect_size`
をそのまま import する。

判定ラベル（tight/loose/dead）はこのスクリプトの責務外 — 生成した数値に対して
plan.yaml / order_sheet の事前登録規約を設計側が適用する。

音声読み込みは canonical RPE 経路（`svp_rpe.io.audio_loader.load_audio`、
`extract_physical_from_file` と同一条件 = 22050 リサンプル）で行う。novelty
計器はサンプルレート感応であり、native SR（Suno mp3 の 44.1/48 kHz）のまま
測ると extractor 系計測と別波形になるため（Codex #166 P2 第 3 ラウンド採用）。

事前登録の短尺カットオフ（order_sheet §4: 曲長 30 秒未満は除外）は本 CLI が
集計**前**に執行する（`--min-duration`、既定 30.0）。除外テイクは出力 YAML の
`excluded:` 節に明示記録し、セルの受入が 0 本になったら fail-fast する
（silent corruption 防止、Codex #166 P2 採用）。さらにカットオフ適用後の各セル
受入数が事前登録 repetitions（`--expected-per-cell`、既定 4 =
structure_plan.yaml）と不一致の場合も fail-fast し、n=3 や不揃い n の不完全
バッチから schema-valid な集計値を黙って出さない（Codex #166 P2 第 2 ラウンド採用）。
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
from svp_rpe.io.audio_loader import load_audio  # noqa: E402
from svp_rpe.rpe.structure_novelty import compute_novelty_curve, find_boundaries  # noqa: E402

SCHEMA_VERSION = "1.0"
PRESCRIBED_PATTERN: list[str] = ["low", "high", "low"]

# batch-2 事前登録カットオフ（order_sheet §4: 構造が物理的に展開し得ない極端な
# 短尺として曲長 30 秒未満のテイクを除外する）。--min-duration 0 で明示無効化可。
DEFAULT_MIN_DURATION_SEC = 30.0

# batch-2 事前登録 repetitions（structure_plan.yaml: repetitions: 4。除外発火時は
# 同セル補充で R=4 を充足する規定）。カットオフ適用後の各セル受入数がこれと
# 不一致なら fail-fast — n=3 や不揃い n の不完全バッチから schema-valid な
# match_rate / novelty_d を黙って出さない。--expected-per-cell 0 で明示無効化可。
DEFAULT_EXPECTED_PER_CELL = 4


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
    # canonical RPE 経路と同一の 22050 リサンプル波形で計測する
    # （extract_physical_from_file と同じ load_audio 既定。Codex #166 P2 対応）。
    audio = load_audio(path)
    y = audio.y_mono
    sr = audio.sr
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


def _apply_duration_cutoff(
    paths: list[Path],
    cell: str,
    min_duration_sec: float,
) -> tuple[list[Path], list[dict[str, Any]]]:
    """事前登録の短尺カットオフを集計前に適用する（silent corruption 防止）。

    除外テイクは出力 YAML の `excluded:` 節に明示記録し、セルの受入が 0 本に
    なった場合は fail-fast（ValueError）する。`min_duration_sec <= 0` は無効化。
    """
    if min_duration_sec <= 0:
        return list(paths), []

    accepted: list[Path] = []
    excluded: list[dict[str, Any]] = []
    for path in paths:
        duration_sec = round(float(librosa.get_duration(path=str(path))), 4)
        if duration_sec < min_duration_sec:
            excluded.append(
                {
                    "path": str(path),
                    "cell": cell,
                    "duration_sec": duration_sec,
                    "reason": f"duration < {min_duration_sec:g}s cutoff (preregistered)",
                }
            )
        else:
            accepted.append(path)

    if not accepted:
        raise ValueError(
            f"{cell} cell has no accepted takes after the {min_duration_sec:g}s "
            "preregistered duration cutoff (all takes excluded)"
        )
    return accepted, excluded


def _enforce_expected_per_cell(
    cells: dict[str, list[Path]],
    expected_per_cell: int,
) -> None:
    """カットオフ適用後の各セル受入数が事前登録 repetitions と一致するか検査する。

    不一致セルがあれば ValueError で fail-fast（どのセルが n いくつかを明示）。
    `expected_per_cell <= 0` は無効化。
    """
    if expected_per_cell <= 0:
        return

    mismatches = [
        f"{cell} cell has {len(accepted)} accepted takes"
        for cell, accepted in cells.items()
        if len(accepted) != expected_per_cell
    ]
    if mismatches:
        raise ValueError(
            f"{'; '.join(mismatches)} after cutoff, expected {expected_per_cell} per cell "
            "(preregistered repetitions — supply same-cell replacement takes, "
            "or pass --expected-per-cell 0 to disable this gate)"
        )


def build_report(
    low_paths: list[Path],
    high_paths: list[Path],
    *,
    prescribed: list[str] | None = None,
    min_duration_sec: float = DEFAULT_MIN_DURATION_SEC,
    expected_per_cell: int = DEFAULT_EXPECTED_PER_CELL,
) -> dict[str, Any]:
    prescribed_pattern = prescribed if prescribed is not None else PRESCRIBED_PATTERN

    low_accepted, low_excluded = _apply_duration_cutoff(low_paths, "low", min_duration_sec)
    high_accepted, high_excluded = _apply_duration_cutoff(high_paths, "high", min_duration_sec)
    _enforce_expected_per_cell({"low": low_accepted, "high": high_accepted}, expected_per_cell)

    low_songs = measure_cell(low_accepted, prescribed_pattern)
    high_songs = measure_cell(high_accepted, prescribed_pattern)

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
        "excluded": low_excluded + high_excluded,
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
    parser.add_argument(
        "--min-duration",
        type=float,
        default=DEFAULT_MIN_DURATION_SEC,  # batch-2 事前登録カットオフ（order_sheet §4）
        metavar="SEC",
        help=(
            "曲長カットオフ秒数。未満のテイクは集計から除外し excluded: 節に記録する"
            f"（既定: {DEFAULT_MIN_DURATION_SEC:g} = batch-2 事前登録カットオフ。"
            "0 で明示無効化）"
        ),
    )
    parser.add_argument(
        "--expected-per-cell",
        type=int,
        # batch-2 事前登録 repetitions（structure_plan.yaml: repetitions: 4）
        default=DEFAULT_EXPECTED_PER_CELL,
        metavar="N",
        help=(
            "カットオフ適用後の各セル受入数の要求値。不一致は fail-fast"
            f"（既定: {DEFAULT_EXPECTED_PER_CELL} = structure_plan.yaml の"
            "事前登録 repetitions。0 で明示無効化）"
        ),
    )
    args = parser.parse_args(argv)

    prescribed = [token.strip() for token in args.pattern.split(",")]
    try:
        report = build_report(
            args.low,
            args.high,
            prescribed=prescribed,
            min_duration_sec=args.min_duration,
            expected_per_cell=args.expected_per_cell,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(render_yaml(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
