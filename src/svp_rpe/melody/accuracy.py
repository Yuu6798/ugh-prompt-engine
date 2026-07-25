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
   `_build_monophonic` と同じセグメント順序（note → gap → ... → phrase_gap）で
   時間を進めることで、正解タイムラインが実際に合成される波形と整合する。

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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "DEFAULT_TOLERANCE_CENTS",
    "DEFAULT_HOP_SEC",
    "MelodyAccuracyResult",
    "evaluate_melody_accuracy",
    "midi_to_hz",
    "monophonic_note_intervals",
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


def monophonic_note_intervals(spec: Mapping[str, Any]) -> List[Tuple[float, float, float]]:
    """`kind: monophonic` spec から (start_sec, end_sec, hz) のノート区間列を導出する。

    `scripts/build_melody_bench.py:_build_monophonic` と全く同じ順序で時間を
    進める（ノート → note_gap の無音 → ... → フレーズ末尾の phrase_gap の無音、
    これを最終フレーズ含め毎回行う）。この順序がずれると、導出した正解タイム
    ラインが実際に合成された波形とフレームずれを起こす。
    """
    note_dur = float(spec["note_dur_sec"])
    note_gap = float(spec.get("note_gap_sec", 0.0))
    phrase_gap = float(spec.get("phrase_gap_sec", 0.0))
    phrases: Sequence[Sequence[float]] = spec["phrases"]

    intervals: List[Tuple[float, float, float]] = []
    t = 0.0
    for phrase in phrases:
        for midi in phrase:
            start = t
            end = t + note_dur
            intervals.append((start, end, midi_to_hz(float(midi))))
            t = end
            if note_gap > 0.0:
                t += note_gap
        if phrase_gap > 0.0:
            t += phrase_gap
    return intervals


def monophonic_total_duration_sec(spec: Mapping[str, Any]) -> float:
    """`monophonic_note_intervals` と同じ時間進行で総尺を返す（無音の尾も含む）。"""
    note_dur = float(spec["note_dur_sec"])
    note_gap = float(spec.get("note_gap_sec", 0.0))
    phrase_gap = float(spec.get("phrase_gap_sec", 0.0))
    phrases: Sequence[Sequence[float]] = spec["phrases"]
    t = 0.0
    for phrase in phrases:
        for _midi in phrase:
            t += note_dur
            if note_gap > 0.0:
                t += note_gap
        if phrase_gap > 0.0:
            t += phrase_gap
    return t


def reference_f0_from_monophonic_spec(
    spec: Mapping[str, Any],
    *,
    hop_sec: float = DEFAULT_HOP_SEC,
    total_duration_sec: Optional[float] = None,
) -> Tuple[Tuple[float, ...], Tuple[float, ...]]:
    """`kind: monophonic` spec を 10ms hop の f0 系列（Hz・無声=0）へ決定論変換する。

    正解 = spec そのもの（設計 §3 カテゴリ S）。フレーム ``i`` の時刻は
    ``i * hop_sec``。区間 ``[start, end)`` にあるノートの周波数を採用し、
    ノート間の無音（note_gap/phrase_gap）は ``0.0``（無声）。

    ``total_duration_sec`` を指定すると（例: 伴奏ミックスの実尺に合わせる）、
    spec 由来の尺より長い範囲まで無声フレームを敷き詰める（短い場合は無視: 正解は
    旋律の実区間を超えて伸びない）。

    決定論: 乱数を用いない。同一 spec → 同一出力（bit 一致）。
    """
    if spec.get("kind") != "monophonic":
        raise ValueError(
            f"reference_f0_from_monophonic_spec only supports kind='monophonic' "
            f"specs (got {spec.get('kind')!r}); a chord_pad spec has no single "
            "melody f0 to serve as ground truth"
        )
    if hop_sec <= 0.0:
        raise ValueError(f"hop_sec must be positive, got {hop_sec!r}")

    intervals = monophonic_note_intervals(spec)
    duration = monophonic_total_duration_sec(spec)
    if total_duration_sec is not None:
        duration = max(duration, float(total_duration_sec))

    n_frames = max(0, int(round(duration / hop_sec)))
    times: List[float] = [round(i * hop_sec, 6) for i in range(n_frames)]
    freqs: List[float] = []
    for t in times:
        hz = 0.0
        for start, end, note_hz in intervals:
            if start <= t < end:
                hz = note_hz
                break
        freqs.append(hz)
    return tuple(times), tuple(freqs)
