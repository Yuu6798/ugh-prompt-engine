"""melody/accuracy.py — 抽出精度の算出（M2a）。

`docs/DESIGN_M2_extraction_accuracy.md`（設計書 §2）が定める通り、精度指標は
自作しない。**`mir_eval.melody`（MIT・純 Python）が算出する RPA / RCA / VR / VFA /
OA をそのまま採用し、再実装・改変はしない**。本モジュールが独自に持つのは 2 点のみ:

1. `evaluate_melody_accuracy` — `mir_eval.melody.evaluate` の薄いラッパ + mir_eval
   の外で追加算出する「有声かつ chroma 一致フレームの絶対 cent 誤差の中央値」
   （設計 §2 の誤差モデル中心値）。中央値の算出は `mir_eval.melody.to_cent_voicing`
   （mir_eval の公開 API）が返す整列済み配列を読むだけで、mir_eval 内部関数の
   再実装は行わない。
2. `reference_f0_from_monophonic_spec` — カテゴリ S（`tests/fixtures/melody_bench/`
   の合成 spec）の「spec がそのまま正解」という規約を、10ms hop の f0 系列
   （Hz・無声=0）へ決定論変換する関数。`scripts/build_melody_bench.py` の
   `_build_monophonic` と同じセグメント順序（note → gap → ... → phrase_gap）で、
   かつ **同じ整数標本数**（`int(round(sample_rate * dur_sec))`）で時間を進める
   ことで、正解タイムラインが実際に合成される波形と整合する。秒を直接足し込むと
   標本量子化の端数が累積し（gap 0.05s @ 22050Hz = 1102 標本 = 0.049977s）、
   「正解」が実際にレンダされた波形を記述しなくなる（Codex 指摘。実測で
   `m2_s_direct_melody` の 470 フレーム中 2 フレームが誤ラベル）ため、
   **標本数が single source of truth**。

隔離方針（`docs/melody_observability.md` と同型）: 本モジュールは比較器
（M3）ではない。1 本の (ref, est) f0 系列ペアに対する指標算出専用で、抽出器
・経路選択・観測ゲートには関与しない。

やってはいけないこと（設計 §8）:

- 総合 OA 単独で合否を語らない（`overall_accuracy` は参考記録フィールド）。
- 正解を持たない入力（カテゴリ X）に対して RPA/RCA を算出できるインターフェース
  を提供しない — 本モジュールの公開関数はすべて「呼び出し側が正解 f0 系列を
  明示的に用意する」ことを前提にしており、正解なし音声を暗黙に受理する経路は
  存在しない（`scripts/run_melody_accuracy.py` 側でもカテゴリ X は扱わない）。
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "DEFAULT_TOLERANCE_CENTS",
    "DEFAULT_HOP_SEC",
    "MelodyAccuracyResult",
    "evaluate_melody_accuracy",
    "midi_to_hz",
    "monophonic_note_intervals",
    "monophonic_note_sample_intervals",
    "monophonic_total_duration_sec",
    "monophonic_total_samples",
    "reference_f0_from_monophonic_spec",
]

# mir_eval.melody の既定値と揃える（設計 §4: tolerance_cents=50 が m1_real 系と同じ
# mir_eval 標準）。
DEFAULT_TOLERANCE_CENTS = 50.0
# 設計 §2: 正解形式は 10ms hop の f0 系列（Hz、無声=0）。
DEFAULT_HOP_SEC = 0.01
# mir_eval.melody.hz2cents の base_frequency 既定値（0 Hz の log を避ける基準周波数）。
# mir_eval 側のデフォルトと一致させることで、evaluate() が内部で使う cent 変換と
# median cent error 計算が同じ基準を共有する。
_MIR_EVAL_BASE_FREQUENCY_HZ = 10.0


@dataclass(frozen=True)
class MelodyAccuracyResult:
    """1 本の (ref, est) f0 系列ペアに対する精度指標（設計 §2 の表そのもの）。

    Attributes
    ----------
    tolerance_cents
        RPA/RCA/median cent error に共通の許容幅（既定 50 cent = mir_eval 標準）。
    raw_pitch_accuracy, raw_chroma_accuracy
        `mir_eval.melody.raw_pitch_accuracy` / `raw_chroma_accuracy` そのもの。
    octave_gap
        `raw_chroma_accuracy - raw_pitch_accuracy`（オクターブ誤り率の代理。
        設計 §2「RCA − RPA」）。
    voicing_recall, voicing_false_alarm
        `mir_eval.melody.voicing_recall` / `voicing_false_alarm`。
    overall_accuracy
        `mir_eval.melody.overall_accuracy`。**参考記録のみ**（設計 §8: 単独で
        合否を語らない）。
    median_cent_error
        有声かつ chroma 一致フレームの絶対 cent 誤差（オクターブ補正後の残差）の
        中央値。該当フレームが 0 件なら ``None``。
    voiced_chroma_correct_frame_count
        `median_cent_error` の算出に使ったフレーム数（該当なしは 0）。
    """

    tolerance_cents: float
    raw_pitch_accuracy: float
    raw_chroma_accuracy: float
    octave_gap: float
    voicing_recall: float
    voicing_false_alarm: float
    overall_accuracy: float
    median_cent_error: Optional[float]
    voiced_chroma_correct_frame_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tolerance_cents": self.tolerance_cents,
            "raw_pitch_accuracy": self.raw_pitch_accuracy,
            "raw_chroma_accuracy": self.raw_chroma_accuracy,
            "octave_gap": self.octave_gap,
            "voicing_recall": self.voicing_recall,
            "voicing_false_alarm": self.voicing_false_alarm,
            "overall_accuracy": self.overall_accuracy,
            "median_cent_error": self.median_cent_error,
            "voiced_chroma_correct_frame_count": self.voiced_chroma_correct_frame_count,
        }


def _as_1d_float_array(values: Sequence[float], *, name: str) -> np.ndarray:
    array = np.asarray(list(values), dtype=float)
    if array.ndim != 1:
        raise ValueError(f"{name} must be 1-D, got shape {array.shape}")
    return array


def evaluate_melody_accuracy(
    ref_times: Sequence[float],
    ref_freqs_hz: Sequence[float],
    est_times: Sequence[float],
    est_freqs_hz: Sequence[float],
    *,
    tolerance_cents: float = DEFAULT_TOLERANCE_CENTS,
) -> MelodyAccuracyResult:
    """1 本の (ref, est) f0 系列ペアの精度指標を算出する。

    ``ref_*`` / ``est_*`` は (times_sec, freqs_hz) のペア（無声フレームは
    ``freqs_hz == 0``）。時間分解能は呼び出し側の責務——`est` 側は抽出器の
    ネイティブ hop のままでよく、`mir_eval.melody.to_cent_voicing` が `ref` の
    時間軸へ自動リサンプルする（mir_eval 標準の挙動をそのまま使う）。

    RPA/RCA/VR/VFA/OA は `mir_eval.melody.evaluate` の戻り値をそのまま転記する
    （自作・改変しない）。`median_cent_error` のみ、mir_eval の公開関数
    `to_cent_voicing` が返す整列済み配列から本関数が追加算出する。
    """
    import mir_eval.melody as mir_melody

    ref_times_arr = _as_1d_float_array(ref_times, name="ref_times")
    ref_freqs_arr = _as_1d_float_array(ref_freqs_hz, name="ref_freqs_hz")
    est_times_arr = _as_1d_float_array(est_times, name="est_times")
    est_freqs_arr = _as_1d_float_array(est_freqs_hz, name="est_freqs_hz")

    if len(ref_times_arr) != len(ref_freqs_arr):
        raise ValueError(
            f"ref_times and ref_freqs_hz must have the same length "
            f"({len(ref_times_arr)} != {len(ref_freqs_arr)})"
        )
    if len(est_times_arr) != len(est_freqs_arr):
        raise ValueError(
            f"est_times and est_freqs_hz must have the same length "
            f"({len(est_times_arr)} != {len(est_freqs_arr)})"
        )
    if ref_times_arr.size == 0:
        raise ValueError(
            "ref_times/ref_freqs_hz must be non-empty; a reference-less "
            "evaluation (category X) is not a supported call shape here "
            "(設計 §8: 正解なし素材で RPA/RCA を算出しない)"
        )
    if est_times_arr.size == 0:
        raise ValueError("est_times/est_freqs_hz must be non-empty")

    scores = mir_melody.evaluate(
        ref_times_arr,
        ref_freqs_arr,
        est_times_arr,
        est_freqs_arr,
        cent_tolerance=tolerance_cents,
    )
    rpa = float(scores["Raw Pitch Accuracy"])
    rca = float(scores["Raw Chroma Accuracy"])

    # median cent error 用に、mir_eval が RPA/RCA 算出時に使うのと同じ整列済み
    # 配列を取得する（`evaluate` が内部で使う既定引数 base_frequency=10.0,
    # hop=None, kind='linear' と同一呼び出し。mir_eval の公開 API のみを使い、
    # 内部関数は呼ばない）。
    ref_voicing, ref_cent, _est_voicing, est_cent = mir_melody.to_cent_voicing(
        ref_times_arr,
        ref_freqs_arr,
        est_times_arr,
        est_freqs_arr,
        base_frequency=_MIR_EVAL_BASE_FREQUENCY_HZ,
    )
    median_cent_error, voiced_chroma_correct_count = _median_voiced_chroma_cent_error(
        ref_voicing, ref_cent, est_cent, tolerance_cents=tolerance_cents
    )

    return MelodyAccuracyResult(
        tolerance_cents=float(tolerance_cents),
        raw_pitch_accuracy=rpa,
        raw_chroma_accuracy=rca,
        octave_gap=rca - rpa,
        voicing_recall=float(scores["Voicing Recall"]),
        voicing_false_alarm=float(scores["Voicing False Alarm"]),
        overall_accuracy=float(scores["Overall Accuracy"]),
        median_cent_error=median_cent_error,
        voiced_chroma_correct_frame_count=voiced_chroma_correct_count,
    )


def _median_voiced_chroma_cent_error(
    ref_voicing: np.ndarray,
    ref_cent: np.ndarray,
    est_cent: np.ndarray,
    *,
    tolerance_cents: float,
) -> Tuple[Optional[float], int]:
    """有声かつ chroma 一致フレームの絶対 cent 誤差（オクターブ補正後）の中央値。

    「chroma 一致」の判定式は `mir_eval.melody.raw_chroma_accuracy` と同じ式
    （最近傍オクターブへ丸めた差を引いた残差が tolerance 未満）を独立に計算する
    ——mir_eval の private 実装を呼ぶのではなく、mir_eval が公開しているのと同じ
    整列済み配列（`ref_voicing` / `ref_cent` / `est_cent`）に対して、mir_eval
    が算出する RCA と同じ意味論の式をこのモジュール自身の式として適用するだけで、
    mir_eval 本体（RPA/RCA/VR/VFA/OA の算出）には一切手を加えない。
    """
    nonzero = np.logical_and(ref_cent != 0.0, est_cent != 0.0)
    if not np.any(nonzero):
        return None, 0
    diff_cents = np.abs(ref_cent - est_cent)[nonzero]
    voiced = ref_voicing[nonzero] > 0.0
    octave = 1200.0 * np.round(diff_cents / 1200.0)
    residual = np.abs(diff_cents - octave)
    chroma_correct = residual < tolerance_cents
    mask = voiced & chroma_correct
    if not np.any(mask):
        return None, 0
    selected = residual[mask]
    return float(np.median(selected)), int(selected.size)


# ---------------------------------------------------------------------------
# カテゴリ S 正解導出: spec → 10ms hop f0 系列（決定論）
# ---------------------------------------------------------------------------


def midi_to_hz(midi: float) -> float:
    """MIDI ノート番号 → Hz（69 = A4 = 440Hz）。

    `scripts/build_melody_bench.py:midi_to_hz` と同一の式。`src/svp_rpe` は
    installed package として `scripts/` に依存できない（`scripts/` はパッケージ
    データではない）ため、この 1 行の物理変換式のみ独立に保持する
    （両者が乖離した場合は builder 側を正とし、ここを合わせる）。
    """
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def _validated_sample_rate(sample_rate: int) -> int:
    rate = int(sample_rate)
    if rate <= 0:
        raise ValueError(f"sample_rate must be positive, got {sample_rate!r}")
    return rate


def _segment_sample_counts(spec: Mapping[str, Any], sample_rate: int) -> Tuple[int, int, int]:
    """(note, note_gap, phrase_gap) の**整数標本数**を builder と同じ式で返す。

    `scripts/build_melody_bench.py` の `_tone` / `_rest` はいずれも
    ``int(round(sample_rate * dur_sec))`` 標本（負は 0）を生成し、`_rest` は
    ``> 0.0`` の gap に対してのみ呼ばれる。ここで同一の式・同一の条件を使うことで、
    区間境界が実波形の標本境界と bit 単位で一致する。
    """
    note_dur = float(spec["note_dur_sec"])
    note_gap = float(spec.get("note_gap_sec", 0.0))
    phrase_gap = float(spec.get("phrase_gap_sec", 0.0))
    note_samples = max(0, int(round(sample_rate * note_dur)))
    gap_samples = max(0, int(round(sample_rate * note_gap))) if note_gap > 0.0 else 0
    phrase_samples = max(0, int(round(sample_rate * phrase_gap))) if phrase_gap > 0.0 else 0
    return note_samples, gap_samples, phrase_samples


def monophonic_note_sample_intervals(
    spec: Mapping[str, Any], *, sample_rate: int
) -> List[Tuple[int, int, float]]:
    """`kind: monophonic` spec から (start_sample, end_sample, hz) のノート区間列を返す。

    `scripts/build_melody_bench.py:_build_monophonic` と全く同じ順序（ノート →
    note_gap の無音 → ... → フレーズ末尾の phrase_gap の無音、これを最終フレーズ
    含め毎回行う）で、**標本数を整数で累積**する。秒を足し込む実装では
    ``int(round(sample_rate * dur_sec))`` による量子化端数が累積し、境界フレームの
    有声/無声ラベルが実波形とずれる（実測: note_gap 0.05s @ 22050Hz は 1102 標本 =
    0.049977s なので、`m2_s_direct_melody` の gap 10 個で計 5 標本 ≒ 0.23ms 短くなる。
    実際に 470 フレーム中 2 フレームが誤ラベルになっていた）。
    """
    rate = _validated_sample_rate(sample_rate)
    note_samples, gap_samples, phrase_samples = _segment_sample_counts(spec, rate)
    phrases: Sequence[Sequence[float]] = spec["phrases"]

    intervals: List[Tuple[int, int, float]] = []
    cursor = 0
    for phrase in phrases:
        for midi in phrase:
            start = cursor
            end = cursor + note_samples
            intervals.append((start, end, midi_to_hz(float(midi))))
            cursor = end + gap_samples
        cursor += phrase_samples
    return intervals


def monophonic_note_intervals(
    spec: Mapping[str, Any], *, sample_rate: int
) -> List[Tuple[float, float, float]]:
    """`monophonic_note_sample_intervals` を秒に直した (start_sec, end_sec, hz) 列。

    秒は ``標本数 / sample_rate`` で導出する（秒の累積和ではない）ため、境界は
    実波形の標本境界と一致する。
    """
    rate = _validated_sample_rate(sample_rate)
    return [
        (start / rate, end / rate, hz)
        for start, end, hz in monophonic_note_sample_intervals(spec, sample_rate=rate)
    ]


def monophonic_total_samples(spec: Mapping[str, Any], *, sample_rate: int) -> int:
    """`monophonic_note_sample_intervals` と同じ進行で総標本数を返す（無音の尾も含む）。

    `_build_monophonic` が concatenate する全セグメントの標本数の総和と一致する。
    """
    rate = _validated_sample_rate(sample_rate)
    note_samples, gap_samples, phrase_samples = _segment_sample_counts(spec, rate)
    phrases: Sequence[Sequence[float]] = spec["phrases"]
    total = 0
    for phrase in phrases:
        for _midi in phrase:
            total += note_samples + gap_samples
        total += phrase_samples
    return total


def monophonic_total_duration_sec(spec: Mapping[str, Any], *, sample_rate: int) -> float:
    """合成波形の実尺（総標本数 / sample_rate）。"""
    rate = _validated_sample_rate(sample_rate)
    return monophonic_total_samples(spec, sample_rate=rate) / rate


def reference_f0_from_monophonic_spec(
    spec: Mapping[str, Any],
    *,
    sample_rate: int,
    hop_sec: float = DEFAULT_HOP_SEC,
    total_duration_sec: Optional[float] = None,
) -> Tuple[Tuple[float, ...], Tuple[float, ...]]:
    """`kind: monophonic` spec を 10ms hop の f0 系列（Hz・無声=0）へ決定論変換する。

    正解 = spec そのもの（設計 §3 カテゴリ S）。フレーム ``i`` の時刻は
    ``i * hop_sec``。区間 ``[start, end)`` にあるノートの周波数を採用し、
    ノート間の無音（note_gap/phrase_gap）は ``0.0``（無声）。

    ``sample_rate`` は**必須**。区間境界は
    `monophonic_note_sample_intervals` の整数標本数から導く（`build_melody_bench.py`
    が実際にレンダする標本境界そのもの）。有声/無声の判定はフレームの標本位置
    ``i * hop_sec * sample_rate`` を有理数（`fractions.Fraction`）で表して整数境界と
    比較する——秒の float 比較では境界フレームの帰属が丸めの運に依存するため。

    ``total_duration_sec`` を指定すると（例: 伴奏ミックスの実尺に合わせる）、
    spec 由来の尺より長い範囲まで無声フレームを敷き詰める（短い場合は無視: 正解は
    旋律の実区間を超えて伸びない）。

    決定論: 乱数を用いない。同一 (spec, sample_rate) → 同一出力（bit 一致）。
    """
    if spec.get("kind") != "monophonic":
        raise ValueError(
            f"reference_f0_from_monophonic_spec only supports kind='monophonic' "
            f"specs (got {spec.get('kind')!r}); a chord_pad spec has no single "
            "melody f0 to serve as ground truth"
        )
    if hop_sec <= 0.0:
        raise ValueError(f"hop_sec must be positive, got {hop_sec!r}")
    rate = _validated_sample_rate(sample_rate)

    sample_intervals = monophonic_note_sample_intervals(spec, sample_rate=rate)
    duration = monophonic_total_duration_sec(spec, sample_rate=rate)
    if total_duration_sec is not None:
        duration = max(duration, float(total_duration_sec))

    n_frames = max(0, int(round(duration / hop_sec)))
    times: List[float] = [round(i * hop_sec, 6) for i in range(n_frames)]
    # 1 フレームあたりの標本数（有理数・厳密）。
    samples_per_frame = Fraction(hop_sec) * rate
    freqs: List[float] = []
    for i in range(n_frames):
        position = samples_per_frame * i
        hz = 0.0
        for start, end, note_hz in sample_intervals:
            if start <= position < end:
                hz = note_hz
                break
        freqs.append(hz)
    return tuple(times), tuple(freqs)
