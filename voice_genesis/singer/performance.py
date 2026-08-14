"""singer/performance.py — S4: Performance 層（Genome とは分離した演奏時パラメータ）。

設計書 §6.2「表現分離」に従い、Genome（声質パラメータ）とは独立に、曲の
演奏表現（レガート・ビブラート・フレーズ強弱・ブレス）をここで扱う。
Genome 側の microprosody.vibrato_rate_hz / vibrato_depth_cents は「その声の
ビブラートの質」を決め、本ファイルは「いつ・どうビブラートを掛けるか
（オンセット遅延）」という演奏判断を担う——両者は別レイヤーである。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

import score as sc

BREATH_DURATION_SEC = 0.25  # 200-300ms 範囲の中央値（memo §S4）
PORTAMENTO_MS = 55.0  # 40-70ms 範囲の中央値
VIBRATO_ONSET_DELAY_MS = 150.0
NOTE_ATTACK_RELEASE_MS = 15.0  # クリック防止用の短い articulation フェード


@dataclass
class TimelineSegment:
    note: sc.ScoreNote
    start_sample: int
    end_sample: int
    is_phrase_first: bool
    is_phrase_last: bool


def build_timeline(notes: List[sc.ScoreNote], sr: int, tempo_bpm: float = sc.TEMPO_BPM) -> Tuple[List[TimelineSegment], int]:
    """ノート列 -> サンプル位置つきタイムライン（フレーズ間ブレス挿入込み）。"""
    segments: List[TimelineSegment] = []
    t = 0
    breath_samples = int(BREATH_DURATION_SEC * sr)
    prev_phrase = None
    for note in notes:
        is_first = note.phrase_index != prev_phrase
        if is_first and prev_phrase is not None:
            t += breath_samples  # フレーズ間ブレス
        dur_sec = sc.beats_to_seconds(note.duration_beats, tempo_bpm)
        dur_samples = int(round(dur_sec * sr))
        segments.append(
            TimelineSegment(
                note=note, start_sample=t, end_sample=t + dur_samples,
                is_phrase_first=is_first, is_phrase_last=note.is_phrase_final,
            )
        )
        t += dur_samples
        prev_phrase = note.phrase_index
    return segments, t


def build_f0_contour(
    segments: List[TimelineSegment],
    total_samples: int,
    sr: int,
    vibrato_rate_hz: float,
    vibrato_depth_cents: float,
    portamento_ms: float = PORTAMENTO_MS,
    vibrato_onset_delay_ms: float = VIBRATO_ONSET_DELAY_MS,
) -> np.ndarray:
    """サンプル毎の F0 (Hz) 配列。ブレス区間は 0（無声）。"""
    f0 = np.zeros(total_samples, dtype=np.float64)

    def midi_to_hz(m: float) -> float:
        return 440.0 * 2.0 ** ((m - 69.0) / 12.0)

    # まず各ノート区間を base pitch（ホールド）で埋める
    for seg in segments:
        f0[seg.start_sample:seg.end_sample] = midi_to_hz(seg.note.midi)

    # レガート・ポルタメント: フレーズ内で連続するノート境界のみ（フレーズ
    # 先頭ノートの前＝ブレス直後には適用しない）
    portamento_samples = int(sr * portamento_ms / 1000.0)
    for i in range(1, len(segments)):
        cur = segments[i]
        if cur.is_phrase_first:
            continue  # ブレス直後はポルタメントなし（レガートは歌詞が連続する場合のみ）
        prev = segments[i - 1]
        boundary = cur.start_sample
        half = portamento_samples // 2
        lo = max(prev.start_sample, boundary - half)
        hi = min(cur.end_sample, boundary + half)
        if hi <= lo:
            continue
        f0_from = midi_to_hz(prev.note.midi)
        f0_to = midi_to_hz(cur.note.midi)
        span = hi - lo
        w = np.linspace(0.0, 1.0, span)
        # 対数周波数（cents）空間で補間する方が知覚的に自然
        f0_seg = f0_from * (f0_to / f0_from) ** w
        f0[lo:hi] = f0_seg

    # ビブラート: ノート先頭から onset_delay 後に深さが立ち上がる（線形ランプ）
    onset_delay_samples = int(sr * vibrato_onset_delay_ms / 1000.0)
    t_full = np.arange(total_samples) / sr
    vibrato_phase = 2.0 * np.pi * vibrato_rate_hz * t_full
    vibrato_cents_full = vibrato_depth_cents * np.sin(vibrato_phase)

    depth_envelope = np.zeros(total_samples, dtype=np.float64)
    for seg in segments:
        note_len = seg.end_sample - seg.start_sample
        ramp_len = min(onset_delay_samples, note_len)
        if ramp_len > 0:
            depth_envelope[seg.start_sample:seg.start_sample + ramp_len] = np.linspace(0.0, 1.0, ramp_len)
        depth_envelope[seg.start_sample + ramp_len:seg.end_sample] = 1.0

    f0 = f0 * (2.0 ** ((vibrato_cents_full * depth_envelope) / 1200.0))
    return f0


def build_amplitude_envelope(segments: List[TimelineSegment], total_samples: int, sr: int) -> np.ndarray:
    """フレーズ単位の緩やかな音量アーチ + フレーズ末減衰 + ノート articulation。"""
    amp = np.zeros(total_samples, dtype=np.float64)

    # フレーズごとにグルーピング
    phrases: dict[int, List[TimelineSegment]] = {}
    for seg in segments:
        phrases.setdefault(seg.note.phrase_index, []).append(seg)

    attack_release = int(sr * NOTE_ATTACK_RELEASE_MS / 1000.0)

    for phrase_idx, segs in phrases.items():
        phrase_start = segs[0].start_sample
        phrase_end = segs[-1].end_sample
        phrase_len = phrase_end - phrase_start
        if phrase_len <= 0:
            continue
        # 緩やかなアーチ: 0.80 -> 1.0 -> 0.85（cosine half-arch）
        x = np.linspace(0.0, np.pi, phrase_len)
        arch = 0.80 + 0.20 * np.sin(x)  # 端で0.80、中央で1.0
        amp[phrase_start:phrase_end] = arch

        # フレーズ末ノートの減衰（末尾 30% を線形に軽くフェード）
        last_seg = segs[-1]
        note_len = last_seg.end_sample - last_seg.start_sample
        decay_len = int(note_len * 0.35)
        if decay_len > 1:
            fade = np.linspace(1.0, 0.55, decay_len)
            amp[last_seg.end_sample - decay_len:last_seg.end_sample] *= fade

    # ノート attack: フレーズ先頭（ブレス直後の無音からの立ち上がり）のみ
    # フェードインする。フレーズ内部（レガート接続）のノート境界には適用
    # しない——ここでフェードを入れると legato の滑らかさを損なうため。
    for seg in segments:
        if not seg.is_phrase_first:
            continue
        n = seg.end_sample - seg.start_sample
        ar = min(attack_release, n // 4) if n >= 4 else 0
        if ar > 0:
            amp[seg.start_sample:seg.start_sample + ar] *= np.linspace(0.0, 1.0, ar)

    return amp
