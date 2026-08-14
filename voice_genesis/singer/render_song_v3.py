"""singer/render_song_v3.py — S7: 鼻音族 /n//m/(+撥音N) の強化(v3派生)。

`render_song.py`（無改変・import流用）を monkeypatch 方式で拡張する
（S6の `render_song_v2.py` と同じ手法）。/r//h/ は v2 の修正をそのまま
継承し、新たに /n//m//N/ に振幅ディップ + 鼻音スペクトル整形
（反共振ノッチ + 高域減衰）を追加する。

鼻音強化の内容（memo 要求 a/b/c）:
  (a) 振幅ディップ: 閉鎖区間の voicing を下げる（既存 nasal_targets の
      フォルマント切替と組み合わせ、母音比で明確な振幅低下を作る）
  (b) 鼻音スペクトル構造: `ftv.nasal_targets`（無改変、F1=250Hz 等）は
      既に低い鼻音フォルマントを提供しているが、反共振（800-1500Hz帯の
      notch）と高域の強い減衰は既存の共振（Lorentzian peak）型フィルタに
      存在しない。formant_tv.py 自体は無改変のまま、鼻音区間の波形へ
      事後的にスペクトル整形（FFT notch + 高域減衰）を適用することで
      実現する（レンダラのコア=共振フィルタは変更しない、後段DSPとして
      追加）
  (c) リリース: 閉鎖から母音への遷移は既存の formant timeline 補間
      （60ms）をそのまま使う（鼻音区間の duration 自体は変更しない）
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
import render_song_v2 as rs2  # noqa: E402  (S6, 無改変・import流用: /r//h/ 修正)

_ORIGINAL_BUILD_NOTE_SUBSEGMENTS = rs.build_note_subsegments

# --- v3 パラメータ（`nasal_fix_report.md` §較正で決定・凍結） ---
NASAL_DIP_VOICING_V3 = 0.15       # 旧 1.0 -> 新 0.15（明確な振幅ディップ。較正: 0.30/0.20/0.15/0.10を実測し、
                                   # 全インスタンスで dip>=5dB(S6のX基準と統一)を満たす最初の値として選定）
NASAL_NOTCH_CENTER_HZ = 1150.0    # 800-1500Hz帯の反共振中心
NASAL_NOTCH_WIDTH_OCT = 0.6
NASAL_NOTCH_DEPTH = 0.80          # 中心周波数での減衰率（1.0=完全除去）
NASAL_HF_CUTOFF_HZ = 3000.0
NASAL_HF_ATTEN_DB = -18.0         # cutoff 以上の追加減衰


def build_note_subsegments_v3(
    mora: pj.Mora, start: int, end: int,
    formant_scale: float, formant_offsets: Tuple[float, float, float, float], bandwidth_scale: float,
) -> List["rs.SubSegment"]:
    # 撥音 N（moraic nasal）: 振幅ディップのみ適用（フォルマントは既存 nasal_targets のまま）
    if mora.is_moraic_nasal:
        f, b = rs.ftv.nasal_targets(formant_scale, formant_offsets, bandwidth_scale)
        return [rs.SubSegment(start, end, f, b, voicing=NASAL_DIP_VOICING_V3)]

    if mora.onset is None or mora.is_long_vowel_mark:
        return _ORIGINAL_BUILD_NOTE_SUBSEGMENTS(mora, start, end, formant_scale, formant_offsets, bandwidth_scale)

    onset = mora.onset
    if onset in ("r", "h"):
        return rs2.build_note_subsegments_v2(mora, start, end, formant_scale, formant_offsets, bandwidth_scale)

    if onset not in ("m", "n"):
        return _ORIGINAL_BUILD_NOTE_SUBSEGMENTS(mora, start, end, formant_scale, formant_offsets, bandwidth_scale)

    note_len = end - start
    vowel_f, vowel_b = rs._formant_target_for(mora.vowel, formant_scale, formant_offsets, bandwidth_scale)
    timing = rs._CONSONANT_TIMING_MS[onset]
    max_consonant_samples = int(note_len * rs._MAX_CONSONANT_FRACTION)
    nasal_dur = min(int(timing["nasal"] / 1000.0 * rs.SR_RENDER), max_consonant_samples)
    c_end = start + nasal_dur
    nasal_f, nasal_b = rs.ftv.nasal_targets(formant_scale, formant_offsets, bandwidth_scale)
    subs = [
        rs.SubSegment(start, c_end, nasal_f, nasal_b, voicing=NASAL_DIP_VOICING_V3),
        rs.SubSegment(c_end, end, vowel_f, vowel_b, voicing=1.0),
    ]
    return [s for s in subs if s.end > s.start]


def _apply_nasal_spectral_shaping(wav: np.ndarray, sr: int, start: int, end: int) -> None:
    """[start:end)（OUT domain サンプル）区間へ notch + 高域減衰を in-place 適用。
    境界は raised-cosine フェードでブレンドしクリックを防ぐ。
    """
    n = end - start
    if n < 32:
        return
    seg = wav[start:end].copy()
    spec = np.fft.rfft(seg)
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)

    log_ratio = np.log2(np.maximum(freqs, 1.0) / NASAL_NOTCH_CENTER_HZ)
    notch_gain = 1.0 - NASAL_NOTCH_DEPTH * np.exp(-0.5 * (log_ratio / NASAL_NOTCH_WIDTH_OCT) ** 2)

    hf_lin = 10.0 ** (NASAL_HF_ATTEN_DB / 20.0)
    hf_gain = np.where(freqs >= NASAL_HF_CUTOFF_HZ, hf_lin, 1.0)
    ramp_lo, ramp_hi = NASAL_HF_CUTOFF_HZ / 1.4, NASAL_HF_CUTOFF_HZ
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


def render_sakura_v3(genome, seed_extra: int = 0, notes=None, tempo_bpm: Optional[float] = None) -> "rs.RenderResult":
    """r/h(v2継承) + n/m/N(v3新規) を monkeypatch で差し替えて描画し、
    レンダ後に鼻音区間へスペクトル整形を事後適用する。
    `render_song.py`・`render_song_v2.py` のソースは無改変のまま。
    """
    original = rs.build_note_subsegments
    rs.build_note_subsegments = build_note_subsegments_v3  # type: ignore[assignment]
    try:
        result = rs.render_sakura(genome, seed_extra=seed_extra, notes=notes, tempo_bpm=tempo_bpm)
    finally:
        rs.build_note_subsegments = original  # type: ignore[assignment]

    wav = result.wav.copy()
    for seg in result.segments:
        mora = seg.note.mora
        is_nasal_onset = mora.onset in ("m", "n")
        if not (is_nasal_onset or mora.is_moraic_nasal):
            continue
        note_len = seg.end_sample - seg.start_sample
        if mora.is_moraic_nasal:
            c_end = seg.end_sample
        else:
            max_cons = int(note_len * rs._MAX_CONSONANT_FRACTION)
            nasal_dur = min(int(rs._CONSONANT_TIMING_MS[mora.onset]["nasal"] / 1000.0 * rs.SR_OUT), max_cons)
            c_end = seg.start_sample + nasal_dur
        _apply_nasal_spectral_shaping(wav, result.sr, seg.start_sample, c_end)

    return dataclasses.replace(result, wav=wav)
