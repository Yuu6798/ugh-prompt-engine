"""Phase 0 melody sensor spike: deterministic melody similarity raw-data dump.

このスクリプトは判定を行わない。3 本のベーススコア（S1=既存 fixture、
S2/S3=無関係曲級にスクリプト内で決定論的に構築した派生曲）を各 3 テイク
（base / transposed_up / transposed_down）演奏し、`compute_melody_contour`
（librosa.pyin ベース）でメロディ輪郭を抽出、音程列に落として DTW / LCS の
2 指標で類似度を計算する。出力は生データの JSON のみで、tight/loose 等の
分類・閾値判定はここでは行わない（判定は設計側の役割）。

決定論性: 乱数は `PerformanceStyle.seed` のみで、`perform()` 内部で
`np.random.default_rng(seed=...)` として消費される。wall-clock や環境依存の
値は出力に含めない。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from scipy.signal import medfilt

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from svp_rpe.compose.loader import load_composition_score  # noqa: E402
from svp_rpe.compose.models import CompositionScore  # noqa: E402
from svp_rpe.perform.performer import PerformanceStyle, perform  # noqa: E402
from svp_rpe.perform.synth import SAMPLE_RATE, sha256_bytes, wav_bytes  # noqa: E402
from svp_rpe.rpe.models import MelodyContour  # noqa: E402
from svp_rpe.rpe.physical_features import compute_melody_contour  # noqa: E402

SCHEMA_VERSION = "1.0"
S1_PATH = ROOT / "examples" / "composition" / "midnight_signal" / "composition_score.yaml"

# 演奏テイク定義（Design Memo 指定どおり: seed のみが乱数源）。
STYLE_A = PerformanceStyle(name="base", seed=20)
STYLE_B1 = PerformanceStyle(name="transposed_up", transpose=3, bpm_bias=25.0, seed=21)
STYLE_B2 = PerformanceStyle(name="transposed_down", transpose=-4, bpm_bias=-20.0, seed=22)
STYLES = (STYLE_A, STYLE_B1, STYLE_B2)

MEDFILT_KERNEL = 5
MIN_RUN_LENGTH = 3
MIN_VOICING = 0.5


# ---------------------------------------------------------------------------
# S2 / S3: 無関係曲級のベーススコアをスクリプト内で決定論的に構築
# ---------------------------------------------------------------------------


def _build_variant_score(
    base: CompositionScore,
    *,
    title: str,
    core: str,
    grv_primary: str,
    grv_secondary: str,
    delta_e_overall: str,
    avoid: list[str],
    key: str,
    bpm: int,
    progression: list[tuple[str, str]],
    bars_overrides: dict[str, int],
) -> CompositionScore:
    """base（S1）を土台に、無関係曲級の別スコアを構築する。

    CompositionScore のスキーマ検証（``extra="forbid"``）を通すため、
    ``model_dump()`` → 辞書改変 → ``model_validate()`` の経路を使う。
    """
    data = base.model_dump()
    data.pop("control_profile", None)  # S1 固有の K2 由来 grip 情報は引き継がない
    data["meta"]["title"] = title
    data["semantic"]["core"] = core
    data["semantic"]["grv"]["primary"] = grv_primary
    data["semantic"]["grv"]["secondary"] = grv_secondary
    data["semantic"]["delta_e"]["overall"] = delta_e_overall
    data["semantic"]["avoid"] = avoid
    data["physical"]["key"] = key
    data["physical"]["bpm"] = bpm
    for section in data["structure"]:
        if section["section"] in bars_overrides:
            section["bars"] = bars_overrides[section["section"]]
    data["events"] = {
        "chord_progression": [
            {"root": root, "quality": quality} for root, quality in progression
        ]
    }
    return CompositionScore.model_validate(data)


def build_scores() -> dict[str, CompositionScore]:
    s1 = load_composition_score(S1_PATH)

    # S2: Ab major / 96 BPM / Ab-Fm-Db-Eb 系（CHORD_NAMES はシャープ表記のみ許可
    # のため異名同音で G#-Fm-C#-D# に変換）。
    s2 = _build_variant_score(
        s1,
        title="Amber Static",
        core="daylight market haggling energy",
        grv_primary="afrobeat",
        grv_secondary="funk",
        delta_e_overall="steady bustle, no build",
        avoid=["dark ambient drone", "minor key melancholy"],
        key="Ab major",
        bpm=96,
        progression=[
            ("G#", "major"),
            ("F", "minor"),
            ("C#", "major"),
            ("D#", "major"),
        ],
        bars_overrides={"verse": 12, "chorus": 20, "bridge": 6},
    )

    # S3: E minor / 150 BPM / Em-C-G-D。
    s3 = _build_variant_score(
        s1,
        title="Glass Sprint",
        core="frantic downhill chase",
        grv_primary="drum_and_bass",
        grv_secondary="breakcore",
        delta_e_overall="relentless acceleration, brief gasp mid-track",
        avoid=["lounge jazz", "slow ballad phrasing"],
        key="E minor",
        bpm=150,
        progression=[
            ("E", "minor"),
            ("C", "major"),
            ("G", "major"),
            ("D", "major"),
        ],
        bars_overrides={"intro": 4, "verse": 10, "chorus": 12, "bridge": 4},
    )
    return {"S1": s1, "S2": s2, "S3": s3}


# ---------------------------------------------------------------------------
# メロディ輪郭 → ノート列 → 音程列（純関数群）
# ---------------------------------------------------------------------------


def extract_note_pitches(contour: MelodyContour) -> list[int]:
    """MelodyContour からノート（半音単位の MIDI ピッチ）列を抽出する。

    手順（Design Memo 指定）:
    1. voicing >= 0.5 のフレームのみ採用
    2. freq → MIDI 実数 (69 + 12*log2(f/440))
    3. 長さ 5 の median filter
    4. 半音へ丸め
    5. 連続同一半音のランをノートへ集約（最小 3 フレーム未満のランは棄却）
    """
    freqs = np.asarray(contour.frequencies_hz, dtype=float)
    voicing = np.asarray(contour.voicing, dtype=float)
    mask = (voicing >= MIN_VOICING) & (freqs > 0.0)
    if not np.any(mask):
        return []

    midi_real = 69.0 + 12.0 * np.log2(freqs[mask] / 440.0)

    if midi_real.size >= MEDFILT_KERNEL:
        filtered = medfilt(midi_real, kernel_size=MEDFILT_KERNEL)
    else:
        # フレーム数が median filter の窓幅未満の場合はフィルタをかけずに進む
        # （scipy.signal.medfilt は kernel_size > データ長で使えないため）。
        filtered = midi_real

    rounded = np.round(filtered).astype(int)

    notes: list[int] = []
    run_value = int(rounded[0])
    run_length = 1
    for value in rounded[1:]:
        value = int(value)
        if value == run_value:
            run_length += 1
            continue
        if run_length >= MIN_RUN_LENGTH:
            notes.append(run_value)
        run_value = value
        run_length = 1
    if run_length >= MIN_RUN_LENGTH:
        notes.append(run_value)
    return notes


def fold_interval(diff: int) -> int:
    """音程差分をオクターブ折返しで [-6, 6] に正規化する。

    ``diff % 12`` は Python の仕様で常に 0..11 の非負値を返すため、
    6 半音（トライトーン、上下いずれの表記も等価）は常に +6 側の代表値へ
    畳み込まれる（達成域は [-5, 6]）。
    """
    wrapped = diff % 12
    if wrapped > 6:
        wrapped -= 12
    return wrapped


def note_pitches_to_intervals(notes: Sequence[int]) -> list[int]:
    if len(notes) < 2:
        return []
    return [fold_interval(notes[i + 1] - notes[i]) for i in range(len(notes) - 1)]


# ---------------------------------------------------------------------------
# 類似度: DTW（音程列、折返し距離コスト） / LCS
# ---------------------------------------------------------------------------


def _interval_distance(a: int, b: int) -> int:
    diff = abs(a - b)
    return min(diff, 12 - diff)


def dtw_align(a: Sequence[int], b: Sequence[int]) -> tuple[float, int]:
    """音程列同士の DTW。戻り値は (総コスト, パス長)。"""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        raise ValueError("dtw_align requires non-empty sequences")

    inf = float("inf")
    cost = np.full((n + 1, m + 1), inf, dtype=float)
    cost[0, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            step_cost = _interval_distance(a[i - 1], b[j - 1])
            cost[i, j] = step_cost + min(cost[i - 1, j], cost[i, j - 1], cost[i - 1, j - 1])

    # バックトラックしてパス長（訪問セル数）を数える。
    i, j = n, m
    path_len = 0
    while i > 0 or j > 0:
        path_len += 1
        if i == 0:
            j -= 1
        elif j == 0:
            i -= 1
        else:
            choices = (cost[i - 1, j - 1], cost[i - 1, j], cost[i, j - 1])
            k = int(np.argmin(choices))
            if k == 0:
                i, j = i - 1, j - 1
            elif k == 1:
                i -= 1
            else:
                j -= 1
    return float(cost[n, m]), path_len


def lcs_length(a: Sequence[int], b: Sequence[int]) -> int:
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[n][m]


def compute_similarity(
    intervals_a: Sequence[int], intervals_b: Sequence[int]
) -> dict[str, Any] | None:
    """DTW / LCS 類似度を計算する。どちらかの音程列が空なら None（計算不能）。"""
    if len(intervals_a) == 0 or len(intervals_b) == 0:
        return None
    total_cost, path_len = dtw_align(intervals_a, intervals_b)
    normalized_distance = total_cost / path_len if path_len > 0 else 0.0
    sim_dtw = 1.0 / (1.0 + normalized_distance)
    lcs_len = lcs_length(intervals_a, intervals_b)
    sim_lcs = lcs_len / max(len(intervals_a), len(intervals_b))
    return {
        "sim_dtw": round(sim_dtw, 4),
        "sim_lcs": round(sim_lcs, 4),
        "dtw_total_cost": round(total_cost, 4),
        "dtw_path_len": path_len,
        "lcs_len": lcs_len,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _score_summary(score: CompositionScore) -> dict[str, Any]:
    progression = []
    if score.events is not None:
        progression = [
            {"root": chord.root, "quality": chord.quality}
            for chord in score.events.chord_progression
        ]
    return {
        "title": score.meta.title,
        "key": score.physical.key,
        "bpm": score.physical.bpm,
        "chord_progression": progression,
        "structure_bars": [
            {"section": section.section, "bars": section.bars} for section in score.structure
        ],
    }


def _render_take(score: CompositionScore, style: PerformanceStyle) -> dict[str, Any]:
    samples = perform(score, style)
    wav = wav_bytes(samples)
    sha256 = sha256_bytes(wav)
    y = samples.astype(np.float32) / 32768.0
    contour = compute_melody_contour(y, SAMPLE_RATE)

    entry: dict[str, Any] = {
        "wav_sha256": sha256,
        "sample_count": int(samples.shape[0]),
        "melody_contour_none": contour is None,
    }
    if contour is None:
        entry["notes"] = []
        entry["intervals"] = []
        return entry

    notes = extract_note_pitches(contour)
    intervals = note_pitches_to_intervals(notes)
    entry["contour_frame_count"] = len(contour.times)
    entry["notes"] = notes
    entry["note_count"] = len(notes)
    entry["intervals"] = intervals
    entry["interval_count"] = len(intervals)
    return entry


def run_spike() -> dict[str, Any]:
    scores = build_scores()
    score_order = ("S1", "S2", "S3")

    audio: dict[str, dict[str, Any]] = {}
    skips: list[dict[str, Any]] = []
    for score_key in score_order:
        score = scores[score_key]
        for style in STYLES:
            take_key = f"{score_key}:{style.name}"
            entry = _render_take(score, style)
            audio[take_key] = entry
            if entry["melody_contour_none"]:
                skips.append(
                    {
                        "reason": "melody_contour_none",
                        "take": take_key,
                    }
                )
            elif entry.get("interval_count", 0) == 0:
                skips.append(
                    {
                        "reason": "insufficient_notes_for_intervals",
                        "take": take_key,
                        "note_count": entry.get("note_count", 0),
                    }
                )

    def _sim_record(
        take_a: str, take_b: str, category: str
    ) -> dict[str, Any] | None:
        entry_a = audio[take_a]
        entry_b = audio[take_b]
        if entry_a["melody_contour_none"] or entry_b["melody_contour_none"]:
            skips.append(
                {
                    "reason": "similarity_skipped_melody_contour_none",
                    "pair": [take_a, take_b],
                    "category": category,
                }
            )
            return None
        intervals_a = entry_a.get("intervals", [])
        intervals_b = entry_b.get("intervals", [])
        result = compute_similarity(intervals_a, intervals_b)
        if result is None:
            skips.append(
                {
                    "reason": "similarity_skipped_empty_intervals",
                    "pair": [take_a, take_b],
                    "category": category,
                }
            )
            return None
        record = {"a": take_a, "b": take_b, "category": category}
        record.update(result)
        return record

    similarity_pairs: list[dict[str, Any]] = []

    # 同曲・変形: sim(A_i, B_i1), sim(A_i, B_i2)
    for score_key in score_order:
        base_take = f"{score_key}:{STYLE_A.name}"
        for style in (STYLE_B1, STYLE_B2):
            record = _sim_record(base_take, f"{score_key}:{style.name}", "same_song_variant")
            if record is not None:
                similarity_pairs.append(record)

    # 無関係: 各 i について、i != j の全 j に対し sim(A_i, A_j) と sim(A_i, B_j*)
    for score_key_i in score_order:
        take_a_i = f"{score_key_i}:{STYLE_A.name}"
        for score_key_j in score_order:
            if score_key_j == score_key_i:
                continue
            take_a_j = f"{score_key_j}:{STYLE_A.name}"
            record = _sim_record(take_a_i, take_a_j, "cross_song")
            if record is not None:
                similarity_pairs.append(record)
            for style in (STYLE_B1, STYLE_B2):
                take_b_j = f"{score_key_j}:{style.name}"
                record = _sim_record(take_a_i, take_b_j, "cross_song")
                if record is not None:
                    similarity_pairs.append(record)

    return {
        "schema_version": SCHEMA_VERSION,
        "styles": {
            "base": {"transpose": STYLE_A.transpose, "bpm_bias": STYLE_A.bpm_bias, "seed": STYLE_A.seed},
            "transposed_up": {
                "transpose": STYLE_B1.transpose,
                "bpm_bias": STYLE_B1.bpm_bias,
                "seed": STYLE_B1.seed,
            },
            "transposed_down": {
                "transpose": STYLE_B2.transpose,
                "bpm_bias": STYLE_B2.bpm_bias,
                "seed": STYLE_B2.seed,
            },
        },
        "scores": {key: _score_summary(scores[key]) for key in score_order},
        "audio": audio,
        "similarity_pairs": similarity_pairs,
        "skips": skips,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 0 melody sensor spike: raw DTW/LCS similarity data dump.",
    )
    parser.add_argument("--out", type=Path, required=True, help="output JSON path")
    args = parser.parse_args(argv)

    report = run_spike()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
