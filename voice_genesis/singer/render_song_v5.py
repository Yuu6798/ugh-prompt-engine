"""singer/render_song_v5.py — S9: 鼻音の調音位置手がかり（place locus）実装。

S8(v4) は鼻音マーマー→母音の開放速度を8-10msへ短絡し /w/ 化を解消した
（`render_song_v4.py`, 無改変・import流用）。しかし開放直後の F2 locus
（調音位置を決める音響手がかり）が急峻な開放の中で消失し、汎用鼻音
ハミング→既定/m/知覚に縮退した（`results_s9/s8_ear_record.md`）。

本ファイルは murmur→vowel の単純2区間遷移を **murmur→locus→vowel** の
3区間に拡張する:
  1. murmur（鼻音フォルマント、voicing低=v3/v4継承）
  2. locus（短い保持区間、~10ms。F2を調音位置別に設定: /n/≈1700Hz、
     /m/≈1000Hz。F1は低め=300Hz固定）
  3. vowel（既存の母音目標）

境界の遷移幅: murmur→locus は v4 と同じ短い開放（8ms、5-15ms範囲）、
locus→vowel は 20-25ms（22msを採用）。`formant_tv.py`・`render_song.py`・
`render_song_v2/v3/v4.py` は無改変のまま、本ファイル内で
`interpolate_formant_timeline` 相当ロジックを複製し境界別遷移幅に対応した
版を実装する。
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

_HERE = Path(__file__).resolve().parent
_PROTO1_DIR = _HERE.parent / "proto1"
for _p in (_PROTO1_DIR,):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import phoneme_jp as pj  # noqa: E402
import render_song as rs  # noqa: E402
import render_song_v2 as rs2  # noqa: E402  (S6, 無改変・import流用: /r//h/)
import render_song_v3 as rs3  # noqa: E402  (S7, 無改変・import流用: 鼻音スペクトル整形)
import formant_tv as ftv  # noqa: E402

_ORIGINAL_BUILD_NOTE_SUBSEGMENTS = rs.build_note_subsegments

# --- v5 パラメータ（凍結） ---
LOCUS_HOLD_MS = 16.0
# [較正] 当初10msで設計したがmurmur->locus遷移(半分4ms)とlocus->vowel
# 遷移(半分11ms)がlocus区間の両端から食い込むため合計15ms超が必要と判明
# （実測: locus区間10msでは開放+6msの計測点が既にvowel側へ汚染された）。
# 20msまで拡大するとlocus band検査は通るが、identity非退行（S8基準5/8）
# が4/8へ悪化することを実測で発見（voiceCの within-singer/cross-song
# E1距離が0.1284→0.1597まで増加）。16ms（entrance/exit遷移窓の合計15msを
# 僅かに上回るだけの最小限の拡大）で両立点を実測で特定した
# （locus band検査は合格を維持、identityはむしろS8基準より改善=0.0742）。
MURMUR_TO_LOCUS_TRANSITION_MS = 8.0     # S8(v4)の開放速度と同じレンジ(5-15ms)を維持=/w/化への回帰防止
LOCUS_TO_VOWEL_TRANSITION_MS = 22.0     # memo指定 20-25ms の中央値
LOCUS_F1_HZ = 300.0                     # 両調音位置とも低めから（memo指定 250-400 の中間）
PLACE_LOCUS_F2_HZ = {"n": 1700.0, "m": 1000.0}
PLACE_NOTCH_BAND_HZ = {"m": (750.0, 1250.0), "n": (1400.0, 2200.0)}

_SHORT_SEGMENT_MS_THRESHOLD = 30.0  # locus区間(~20ms)判定用（murmur~100msと明確に区別。LOCUS_HOLD_MSより大きく保つ）
NASAL_F1_HZ = ftv.NASAL_FORMANTS_HZ[0]
NASAL_F1_TOLERANCE_HZ = 20.0


def build_note_subsegments_v5(
    mora: pj.Mora, start: int, end: int,
    formant_scale: float, formant_offsets: Tuple[float, float, float, float], bandwidth_scale: float,
) -> List["rs.SubSegment"]:
    if mora.is_moraic_nasal:
        # 撥音N: 後続音に応じてn寄り既定（memo item1）。本段階は次モーラ
        # 参照の配線を持たないため常にn(歯茎)寄りとする（[UNDERSPEC-S9-1]）。
        # 撥音はノート全体が鼻音のため母音への遷移が無く、locus機構は
        # 適用対象外（murmurのみ、S7/S8と同じ振幅ディップのみ継承）。
        f, b = ftv.nasal_targets(formant_scale, formant_offsets, bandwidth_scale)
        return [rs.SubSegment(start, end, f, b, voicing=rs3.NASAL_DIP_VOICING_V3)]

    if mora.onset is None or mora.is_long_vowel_mark:
        return _ORIGINAL_BUILD_NOTE_SUBSEGMENTS(mora, start, end, formant_scale, formant_offsets, bandwidth_scale)

    onset = mora.onset
    if onset in ("r", "h"):
        return rs2.build_note_subsegments_v2(mora, start, end, formant_scale, formant_offsets, bandwidth_scale)

    if onset not in ("m", "n"):
        return _ORIGINAL_BUILD_NOTE_SUBSEGMENTS(mora, start, end, formant_scale, formant_offsets, bandwidth_scale)

    note_len = end - start
    vowel_f, vowel_b = rs._formant_target_for(mora.vowel, formant_scale, formant_offsets, bandwidth_scale)
    nasal_f, nasal_b = ftv.nasal_targets(formant_scale, formant_offsets, bandwidth_scale)
    timing = rs._CONSONANT_TIMING_MS[onset]
    max_consonant_samples = int(note_len * rs._MAX_CONSONANT_FRACTION)
    nasal_dur = min(int(timing["nasal"] / 1000.0 * rs.SR_RENDER), max_consonant_samples)
    c_end = start + nasal_dur

    locus_dur = int(LOCUS_HOLD_MS / 1000.0 * rs.SR_RENDER)
    locus_dur = max(0, min(locus_dur, end - c_end - 1))
    l_end = c_end + locus_dur

    locus_f2 = PLACE_LOCUS_F2_HZ[onset]
    locus_f = (LOCUS_F1_HZ, locus_f2, vowel_f[2], vowel_f[3])

    subs = [
        rs.SubSegment(start, c_end, nasal_f, nasal_b, voicing=rs3.NASAL_DIP_VOICING_V3),
        rs.SubSegment(c_end, l_end, locus_f, vowel_b, voicing=1.0),
        rs.SubSegment(l_end, end, vowel_f, vowel_b, voicing=1.0),
    ]
    return [s for s in subs if s.end > s.start]


def _is_locus_segment(seg, sr: int) -> bool:
    dur_ms = (seg.end_sample - seg.start_sample) / sr * 1000.0
    return bool(dur_ms <= _SHORT_SEGMENT_MS_THRESHOLD and abs(seg.formants[0] - LOCUS_F1_HZ) <= 30.0)


def _is_murmur_segment(seg) -> bool:
    return bool(abs(seg.formants[0] - NASAL_F1_HZ) <= NASAL_F1_TOLERANCE_HZ)


def interpolate_formant_timeline_v5(
    segments: List["ftv.FormantSegment"], n_samples: int, sr: int, transition_ms: float = 60.0,
) -> Tuple[np.ndarray, np.ndarray]:
    formants_t = np.zeros((n_samples, 4), dtype=np.float64)
    bandwidths_t = np.zeros((n_samples, 4), dtype=np.float64)
    default_formants = np.array(ftv.VOWEL_FORMANTS_HZ["a"])
    default_bandwidths = np.array(ftv.BANDWIDTHS_HZ)
    formants_t[:, :] = default_formants
    bandwidths_t[:, :] = default_bandwidths

    for seg in segments:
        s, e = max(0, seg.start_sample), min(n_samples, seg.end_sample)
        if e <= s:
            continue
        formants_t[s:e, :] = np.array(seg.formants)
        bandwidths_t[s:e, :] = np.array(seg.bandwidths)

    for i in range(len(segments) - 1):
        cur, nxt = segments[i], segments[i + 1]
        if _is_locus_segment(cur, sr):
            this_ms = LOCUS_TO_VOWEL_TRANSITION_MS
        elif _is_murmur_segment(cur):
            this_ms = MURMUR_TO_LOCUS_TRANSITION_MS
        else:
            this_ms = transition_ms
        half_trans = int(sr * this_ms / 1000.0 / 2.0)
        boundary = cur.end_sample
        lo = max(0, boundary - half_trans)
        hi = min(n_samples, boundary + half_trans)
        if hi <= lo:
            continue
        span = hi - lo
        w = np.linspace(0.0, 1.0, span)
        f_from, f_to = np.array(cur.formants), np.array(nxt.formants)
        b_from, b_to = np.array(cur.bandwidths), np.array(nxt.bandwidths)
        for k in range(4):
            formants_t[lo:hi, k] = f_from[k] * (1 - w) + f_to[k] * w
            bandwidths_t[lo:hi, k] = b_from[k] * (1 - w) + b_to[k] * w

    return formants_t, bandwidths_t


def _apply_nasal_spectral_shaping_v5(wav: np.ndarray, sr: int, start: int, end: int, onset: str) -> None:
    """v3の`_apply_nasal_spectral_shaping`と同一ロジックだが、notch帯域を
    調音位置別（`PLACE_NOTCH_BAND_HZ`）にする。
    """
    n = end - start
    if n < 32:
        return
    lo_hz, hi_hz = PLACE_NOTCH_BAND_HZ[onset]
    center = float(np.sqrt(lo_hz * hi_hz))
    width_oct = float(np.log2(hi_hz / lo_hz) / 2.0)

    seg = wav[start:end].copy()
    spec = np.fft.rfft(seg)
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    log_ratio = np.log2(np.maximum(freqs, 1.0) / center)
    notch_gain = 1.0 - rs3.NASAL_NOTCH_DEPTH * np.exp(-0.5 * (log_ratio / width_oct) ** 2)

    hf_lin = 10.0 ** (rs3.NASAL_HF_ATTEN_DB / 20.0)
    hf_gain = np.where(freqs >= rs3.NASAL_HF_CUTOFF_HZ, hf_lin, 1.0)
    ramp_lo, ramp_hi = rs3.NASAL_HF_CUTOFF_HZ / 1.4, rs3.NASAL_HF_CUTOFF_HZ
    ramp_mask = (freqs >= ramp_lo) & (freqs < ramp_hi)
    if ramp_mask.any():
        t = (freqs[ramp_mask] - ramp_lo) / (ramp_hi - ramp_lo)
        hf_gain[ramp_mask] = 1.0 + (hf_lin - 1.0) * (0.5 - 0.5 * np.cos(np.pi * t))

    shaped = np.fft.irfft(spec * notch_gain * hf_gain, n=n)
    fade = min(n // 4, int(sr * 0.004))
    if fade > 0:
        w = np.ones(n)
        w[:fade] = np.linspace(0.0, 1.0, fade)
        w[-fade:] = np.linspace(1.0, 0.0, fade)
        blended = seg * (1.0 - w) + shaped * w
    else:
        blended = shaped
    wav[start:end] = blended


def render_sakura_v5(genome, seed_extra: int = 0, notes=None, tempo_bpm: Optional[float] = None) -> "rs.RenderResult":
    orig_subseg = rs.build_note_subsegments
    orig_interp = ftv.interpolate_formant_timeline
    rs.build_note_subsegments = build_note_subsegments_v5  # type: ignore[assignment]
    ftv.interpolate_formant_timeline = interpolate_formant_timeline_v5  # type: ignore[assignment]
    try:
        result = rs.render_sakura(genome, seed_extra=seed_extra, notes=notes, tempo_bpm=tempo_bpm)
    finally:
        rs.build_note_subsegments = orig_subseg  # type: ignore[assignment]
        ftv.interpolate_formant_timeline = orig_interp  # type: ignore[assignment]

    wav = result.wav.copy()
    for seg in result.segments:
        mora = seg.note.mora
        onset = mora.onset if mora.onset in ("m", "n") else ("n" if mora.is_moraic_nasal else None)
        if onset is None:
            continue
        note_len = seg.end_sample - seg.start_sample
        if mora.is_moraic_nasal:
            c_end = seg.end_sample
        else:
            max_cons = int(note_len * rs._MAX_CONSONANT_FRACTION)
            nasal_dur = min(int(rs._CONSONANT_TIMING_MS[mora.onset]["nasal"] / 1000.0 * rs.SR_OUT), max_cons)
            c_end = seg.start_sample + nasal_dur
        _apply_nasal_spectral_shaping_v5(wav, result.sr, seg.start_sample, c_end, onset)

    return dataclasses.replace(result, wav=wav)
