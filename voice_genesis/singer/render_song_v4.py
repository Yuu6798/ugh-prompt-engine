"""singer/render_song_v4.py — S8: 鼻音開放の急峻化（診断先行修正、v4派生）。

診断結果（`results_s8/nasal_diagnosis_and_fix_report.md` §diagnosis に
実測記録）: S7(v3) の /na/ 区間で F1 遷移を実測したところ、境界前後で
F1=250Hz(鼻音)から500Hz(母音/a/)へ **約50msかけて滑らかに遷移**しており
（`formant_tv.interpolate_formant_timeline` の一律 `transition_ms=60.0`
コアーティキュレーション補間が鼻音→母音境界にも無差別適用されている
ため）、これは memo仮説（/w/知覚の本体=50-100msの遅いフォルマント遷移）
と一致する。マーマー区間自体は前半73ms（100ms中）は純粋に鼻音F1=250Hz
を保っており、母音フォルマントの「残存」は境界直前の遷移窓のみに限定
されていた（仮説(b)は部分的に支持）。

**単一機序修正**: 鼻音マーマー終端→母音開始の境界だけ、コアーティキュ
レーション遷移を 60ms から 10ms（memo指定5-15msの中央値）へ短絡する。
`formant_tv.py` は無改変のまま、本ファイルに同等ロジックを複製し
per-boundary で遷移幅を変える版を実装し、`render_song.py` の
`ftv.interpolate_formant_timeline` 呼び出しを一時差し替える。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

_HERE = Path(__file__).resolve().parent
_PROTO1_DIR = _HERE.parent / "proto1"
for _p in (_PROTO1_DIR,):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import render_song as rs  # noqa: E402
import render_song_v3 as rs3  # noqa: E402  (S7, 無改変・import流用: build_note_subsegments_v3)
import formant_tv as ftv  # noqa: E402

_ORIGINAL_INTERPOLATE = ftv.interpolate_formant_timeline

# --- v4 パラメータ（凍結） ---
NASAL_RELEASE_TRANSITION_MS = 10.0  # 旧一律60ms -> 鼻音マーマー終端->母音境界のみ10msへ短絡
NASAL_F1_HZ = ftv.NASAL_FORMANTS_HZ[0]  # 250.0（formant_scale=1.0前提。安全域ボックスで保証済み）
NASAL_F1_TOLERANCE_HZ = 20.0


def _is_nasal_release_boundary(cur, nxt) -> bool:
    """境界検出ヒューリスティック: 現区間の F1 が鼻音F1(~250Hz)に近く、
    次区間の F1 がそこから明確に離れている(=母音側)場合、鼻音開放境界と判定する。
    """
    cur_f1 = cur.formants[0]
    nxt_f1 = nxt.formants[0]
    return bool(abs(cur_f1 - NASAL_F1_HZ) <= NASAL_F1_TOLERANCE_HZ and abs(nxt_f1 - cur_f1) > NASAL_F1_TOLERANCE_HZ)


def interpolate_formant_timeline_v4(
    segments: List["ftv.FormantSegment"], n_samples: int, sr: int, transition_ms: float = 60.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """`ftv.interpolate_formant_timeline`（無改変）と同一ロジックだが、
    鼻音開放境界だけ `NASAL_RELEASE_TRANSITION_MS` を使う。
    """
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
        this_transition_ms = NASAL_RELEASE_TRANSITION_MS if _is_nasal_release_boundary(cur, nxt) else transition_ms
        half_trans = int(sr * this_transition_ms / 1000.0 / 2.0)
        boundary = cur.end_sample
        lo = max(0, boundary - half_trans)
        hi = min(n_samples, boundary + half_trans)
        if hi <= lo:
            continue
        span = hi - lo
        w = np.linspace(0.0, 1.0, span)
        f_from = np.array(cur.formants)
        f_to = np.array(nxt.formants)
        b_from = np.array(cur.bandwidths)
        b_to = np.array(nxt.bandwidths)
        for k in range(4):
            formants_t[lo:hi, k] = f_from[k] * (1 - w) + f_to[k] * w
            bandwidths_t[lo:hi, k] = b_from[k] * (1 - w) + b_to[k] * w

    return formants_t, bandwidths_t


def render_sakura_v4(genome, seed_extra: int = 0, notes=None, tempo_bpm: Optional[float] = None) -> "rs.RenderResult":
    """r/h(v2継承)+n/m/N振幅ディップ(v3継承)+鼻音開放急峻化(v4新規)を
    monkeypatch で適用して描画する。`render_song.py`/`render_song_v2.py`/
    `render_song_v3.py`/`formant_tv.py` のソースは無改変のまま。
    """
    orig_subseg = rs.build_note_subsegments
    orig_interp = ftv.interpolate_formant_timeline
    rs.build_note_subsegments = rs3.build_note_subsegments_v3  # type: ignore[assignment]
    ftv.interpolate_formant_timeline = interpolate_formant_timeline_v4  # type: ignore[assignment]
    try:
        result = rs.render_sakura(genome, seed_extra=seed_extra, notes=notes, tempo_bpm=tempo_bpm)
    finally:
        rs.build_note_subsegments = orig_subseg  # type: ignore[assignment]
        ftv.interpolate_formant_timeline = orig_interp  # type: ignore[assignment]

    # v3 の鼻音スペクトル整形（notch+高域減衰）は継承する（v3のマーマー区間
    # 後処理をそのまま呼ぶ）。振幅ディップは build_note_subsegments_v3 の
    # voicing 設定で既に反映済みのため、ここでは spectral shaping のみ適用。
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
        rs3._apply_nasal_spectral_shaping(wav, result.sr, seg.start_sample, c_end)

    import dataclasses
    return dataclasses.replace(result, wav=wav)
