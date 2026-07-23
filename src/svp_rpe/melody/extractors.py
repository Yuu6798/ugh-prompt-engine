"""melody/extractors.py — 音声波形 → `MelodyObservation` 変換層。

抽出器を `MelodyObservation`（`observability.py` の共通中間表現）へ正規化する。
CI で回るのは pyin 経路だけ（librosa はコア依存）。CREPE / Melodia / basic-pitch /
Demucs 分離は optional extra 越しの learned adapter へ遅延ディスパッチし、未導入なら
`LearnedModelUnavailable` が伝播する（`docs/learned_models_policy.md` の slow-lane
隔離）。

隔離方針: ここで返す `MelodyObservation` は観測ゲートへの入力に限り、
`PhysicalRPE.melody_contour` 等の決定論 RPE フィールドへは書き戻さない。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Tuple

import numpy as np

from svp_rpe.melody.observability import MelodyNote, MelodyObservation

if TYPE_CHECKING:  # pragma: no cover - typing only
    from svp_rpe.melody.routing import MelodyRoute

__all__ = [
    "extract_pyin_observation",
    "extract_crepe_observation",
    "extract_melodia_observation",
    "extract_basic_pitch_observation",
    "observe_via_route",
]

_PYIN_SOURCE_MODEL = "librosa:pyin"


def extract_pyin_observation(
    y: np.ndarray,
    sr: int,
    *,
    route: str = "pyin_direct",
    source_model: str = _PYIN_SOURCE_MODEL,
) -> MelodyObservation:
    """librosa.pyin で F0 トラックを採り `MelodyObservation` へ正規化する。

    `rpe.physical_features` の pyin 定数（fmin/fmax/hop/最小 voicing）と
    high-pass 前処理を共有し、Phase 0 スパイクと同じセンサー段を再現する。
    無声フレームは ``hz=0.0``、``confidence`` は voiced_prob（NaN→0.0）。
    """
    import librosa

    from svp_rpe.rpe.physical_features import (
        PYIN_FMAX_HZ,
        PYIN_FMIN_HZ,
        PYIN_HOP_LENGTH,
        _highpass_melody_signal,
    )

    y = np.asarray(y, dtype=np.float32)
    if y.size == 0:
        return MelodyObservation(route=route, source_model=source_model, total_duration_sec=0.0)

    duration = float(len(y) / sr)
    try:
        f0, voiced_flag, voiced_prob = librosa.pyin(
            _highpass_melody_signal(y, sr),
            sr=sr,
            fmin=PYIN_FMIN_HZ,
            fmax=PYIN_FMAX_HZ,
            hop_length=PYIN_HOP_LENGTH,
        )
    except Exception:  # pragma: no cover - librosa parameter edge cases
        return MelodyObservation(
            route=route, source_model=source_model, total_duration_sec=duration
        )

    if f0 is None or voiced_prob is None or len(f0) == 0:
        return MelodyObservation(
            route=route, source_model=source_model, total_duration_sec=duration
        )

    voiced = np.asarray(voiced_flag, dtype=bool)
    finite = np.isfinite(f0)
    frequencies = np.where(voiced & finite, np.nan_to_num(f0, nan=0.0), 0.0)
    confidence = np.nan_to_num(np.asarray(voiced_prob, dtype=float), nan=0.0)
    times = librosa.times_like(frequencies, sr=sr, hop_length=PYIN_HOP_LENGTH)

    return MelodyObservation(
        route=route,
        source_model=source_model,
        frame_times=tuple(round(float(t), 4) for t in times),
        frame_hz=tuple(round(float(hz), 3) for hz in frequencies),
        frame_confidence=tuple(round(float(c), 4) for c in confidence),
        total_duration_sec=round(duration, 4),
    )


def extract_crepe_observation(
    y: np.ndarray, sr: int, *, route: str = "crepe_direct"
) -> MelodyObservation:
    """CREPE（optional）で F0 トラックを採り正規化する。未導入なら例外伝播。"""
    from svp_rpe.rpe.learned.crepe_adapter import extract_crepe_f0

    frame_times, frame_hz, frame_confidence, source_model = extract_crepe_f0(y, sr)
    return MelodyObservation(
        route=route,
        source_model=source_model,
        frame_times=tuple(frame_times),
        frame_hz=tuple(frame_hz),
        frame_confidence=tuple(frame_confidence),
        total_duration_sec=round(float(len(y) / sr), 4) if len(y) else 0.0,
    )


def extract_melodia_observation(
    y: np.ndarray, sr: int, *, route: str = "melodia_direct"
) -> MelodyObservation:
    """Essentia PredominantPitchMelodia（optional・AGPL）で採り正規化する。"""
    from svp_rpe.rpe.learned.melodia_adapter import extract_melodia_f0

    frame_times, frame_hz, frame_confidence, source_model = extract_melodia_f0(y, sr)
    return MelodyObservation(
        route=route,
        source_model=source_model,
        frame_times=tuple(frame_times),
        frame_hz=tuple(frame_hz),
        frame_confidence=tuple(frame_confidence),
        total_duration_sec=round(float(len(y) / sr), 4) if len(y) else 0.0,
    )


def extract_basic_pitch_observation(
    audio_path: str, *, route: str = "basic_pitch_direct"
) -> MelodyObservation:
    """basic-pitch（optional）のノート系列を `MelodyObservation.notes` へ正規化する。

    basic-pitch はフレーム F0 でなくノートを返すノート系抽出器なので、
    ``notes`` を直接供給する（フレーム系フィールドは空）。
    """
    from svp_rpe.rpe.learned.basic_pitch_adapter import extract_basic_pitch_annotations

    annotations = extract_basic_pitch_annotations(audio_path)
    notes: Tuple[MelodyNote, ...] = tuple(
        MelodyNote(
            start_sec=round(float(event.start_sec), 4),
            end_sec=round(float(event.end_sec), 4),
            pitch_midi=float(event.pitch_midi),
            confidence=round(float(event.confidence), 4),
        )
        for event in annotations.note_events
    )
    # note-only 表現の被覆分母（総尺）は**実音声の尺**を用いる。最終ノート終端で
    # 近似すると、末尾の無音や無ピッチ伴奏が切り捨てられ、曲頭に旋律が偏った素材でも
    # coverage が過大評価され min_voiced_coverage を誤って通過しうる（Codex 指摘）。
    # librosa.get_duration(path=...) はヘッダ読みで済む場合が多く安価。読めなければ
    # 最終ノート終端へフォールバックする。
    total_duration = _audio_duration_sec(audio_path)
    if total_duration is None:
        total_duration = round(max((n.end_sec for n in notes), default=0.0), 4)
    return MelodyObservation(
        route=route,
        source_model="spotify:basic_pitch",
        notes=notes,
        total_duration_sec=total_duration,
    )


def _audio_duration_sec(audio_path: str) -> "float | None":
    """音声ファイルの実尺（秒）を安価に読む。失敗時は None。"""
    try:
        import librosa

        return round(float(librosa.get_duration(path=audio_path)), 4)
    except Exception:
        return None


def _load_route_waveform(
    audio_path: str, route: "MelodyRoute"
) -> Tuple[np.ndarray, int]:
    """経路の前処理に従い波形を用意する（Demucs 分離 or 素の decode）。

    ``requires_separation`` の経路は learned adapter の
    `isolate_vocals` に委譲し、未導入なら `LearnedModelUnavailable` が伝播する。
    """
    if route.requires_separation:
        from svp_rpe.rpe.learned.source_separation_adapter import isolate_vocals

        waveform, sample_rate = isolate_vocals(audio_path)
        return np.asarray(waveform, dtype=np.float32), int(sample_rate)

    import librosa

    waveform, sample_rate = librosa.load(audio_path, sr=None, mono=True)
    return np.asarray(waveform, dtype=np.float32), int(sample_rate)


def observe_via_route(audio_path: str, route: "MelodyRoute") -> MelodyObservation:
    """1 経路を audio ファイルへ適用して `MelodyObservation` を返す。

    ``applies=False`` の経路（旋律不在入力）は抽出器を一切呼ばず、空観測を
    返す（呼び出し側で `not_observed` へ落とす）。それ以外は前処理（必要なら
    Demucs 分離）→ 抽出器ディスパッチ。optional 依存が未導入なら
    `LearnedModelUnavailable` が伝播する（slow-lane 隔離）。
    """
    if not route.applies:
        return MelodyObservation(route=route.name, source_model="none")

    # basic-pitch は自前で path を読む（前処理は経路上 none のみを想定）。
    if route.extractor == "basic_pitch":
        return extract_basic_pitch_observation(audio_path, route=route.name)

    waveform, sample_rate = _load_route_waveform(audio_path, route)
    if route.extractor == "pyin":
        return extract_pyin_observation(waveform, sample_rate, route=route.name)
    if route.extractor == "crepe":
        return extract_crepe_observation(waveform, sample_rate, route=route.name)
    if route.extractor == "melodia":
        return extract_melodia_observation(waveform, sample_rate, route=route.name)
    raise ValueError(f"unsupported extractor for route {route.name!r}: {route.extractor!r}")
